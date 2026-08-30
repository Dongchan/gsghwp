from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from typing_extensions import TypeIs
from zipfile import ZipFile, ZipInfo

from hwp_errors import HwpLiveError


@dataclass(frozen=True, slots=True)
class XlsxReadLimits:
    members: int = 2_048
    entry_bytes: int = 32_000_000
    total_bytes: int = 128_000_000
    compression_ratio: int = 200
    shared_strings: int = 200_000
    shared_string_bytes: int = 32_000_000
    xml_depth: int = 64
    xml_text_bytes: int = 64_000_000
    cell_chars: int = 200_000


def _is_element(value: str | tuple[str, str] | Element | None) -> TypeIs[Element]:
    return ElementTree.iselement(value)


def _safe_member_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return (
        bool(normalized)
        and not normalized.startswith("/")
        and ":" not in normalized.split("/", 1)[0]
        and ".." not in normalized.split("/")
    )


def validate_xlsx_archive(archive: ZipFile, limits: XlsxReadLimits) -> None:
    infos = archive.infolist()
    if len(infos) > limits.members:
        raise HwpLiveError("XLSX ZIP member count limit exceeded")
    total = 0
    seen: set[str] = set()
    for info in infos:
        if not _safe_member_name(info.filename) or info.filename in seen:
            raise HwpLiveError("XLSX ZIP contains traversal or duplicate members")
        seen.add(info.filename)
        if info.file_size > limits.entry_bytes:
            raise HwpLiveError("XLSX ZIP entry byte limit exceeded")
        total += info.file_size
        if total > limits.total_bytes:
            raise HwpLiveError("XLSX ZIP aggregate byte limit exceeded")
        compressed = max(info.compress_size, 1)
        if info.file_size > compressed * limits.compression_ratio:
            raise HwpLiveError("XLSX ZIP compression ratio limit exceeded")


def _member(archive: ZipFile, name: str) -> ZipInfo:
    try:
        return archive.getinfo(name)
    except KeyError as error:
        raise HwpLiveError(f"XLSX member is missing: {name}") from error


def read_bounded_member(archive: ZipFile, name: str, limits: XlsxReadLimits) -> bytes:
    info = _member(archive, name)
    if info.file_size > limits.entry_bytes:
        raise HwpLiveError("XLSX ZIP entry byte limit exceeded")
    with archive.open(info) as source:
        data = source.read(limits.entry_bytes + 1)
    if len(data) > limits.entry_bytes:
        raise HwpLiveError("XLSX ZIP entry byte limit exceeded")
    return data


def iter_xml_elements(
    archive: ZipFile,
    name: str,
    element_name: str,
    limits: XlsxReadLimits,
) -> Iterator[Element]:
    info = _member(archive, name)
    depth = 0
    text_bytes = 0
    with archive.open(info) as source:
        parser: ElementTree.XMLPullParser[Element] = ElementTree.XMLPullParser(
            events=("start", "end")
        )
        read_bytes = 0
        while chunk := source.read(65_536):
            read_bytes += len(chunk)
            if read_bytes > limits.entry_bytes:
                raise HwpLiveError("XLSX XML entry byte limit exceeded")
            parser.feed(chunk)
            for parsed in parser.read_events():
                if len(parsed) != 2 or not _is_element(parsed[1]):
                    continue
                event = parsed[0]
                element = parsed[1]
                if event == "start":
                    depth += 1
                    if depth > limits.xml_depth:
                        raise HwpLiveError("XLSX XML depth limit exceeded")
                    continue
                text_bytes += len((element.text or "").encode())
                text_bytes += len((element.tail or "").encode())
                if text_bytes > limits.xml_text_bytes:
                    raise HwpLiveError("XLSX XML text byte limit exceeded")
                if element.tag.rsplit("}", 1)[-1] == element_name:
                    yield element
                    element.clear()
                depth -= 1
        parser.close()

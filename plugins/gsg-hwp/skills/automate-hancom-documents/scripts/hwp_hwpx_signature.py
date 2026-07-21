from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Final
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from hwp_errors import DocumentAutomationError


@dataclass(frozen=True, slots=True)
class _StructuralSignature:
    sha256: str
    member_count: int


_STRUCTURAL_PREFIXES: Final = ("Contents/", "BinData/")
_OPF_META: Final = "{http://www.idpf.org/2007/opf/}meta"
_BORDER_FILLS: Final = "{http://www.hancom.co.kr/hwpml/2011/head}borderFills"
_BORDER_FILL: Final = "{http://www.hancom.co.kr/hwpml/2011/head}borderFill"


def _local_name(value: str) -> str:
    return value.rsplit("}", maxsplit=1)[-1]


def _sort_attributes(element: ElementTree.Element) -> None:
    attributes = sorted(element.attrib.items())
    element.attrib.clear()
    element.attrib.update(attributes)
    for child in element:
        _sort_attributes(child)


def _element_bytes(element: ElementTree.Element) -> bytes:
    stream = BytesIO()
    ElementTree.ElementTree(element).write(stream, encoding="utf-8")
    return stream.getvalue()


def _border_fill_maps(
    root: ElementTree.Element,
) -> tuple[dict[str, str], list[ElementTree.Element]]:
    identifiers: dict[str, str] = {}
    unique: dict[bytes, ElementTree.Element] = {}
    container = root.find(f".//{_BORDER_FILLS}")
    if container is None:
        return identifiers, []
    for item in container.findall(_BORDER_FILL):
        clone = deepcopy(item)
        identifier = clone.attrib.pop("id", None)
        if identifier is None:
            raise DocumentAutomationError("HWPX borderFill ID가 없습니다")
        _sort_attributes(clone)
        content = _element_bytes(clone)
        identifiers[identifier] = hashlib.sha256(content).hexdigest()
        unique[content] = clone
    return identifiers, [unique[content] for content in sorted(unique)]


def _normalize_href(value: str) -> str:
    if not value.casefold().startswith("bindata/"):
        return value
    stem, separator, extension = value.rpartition(".")
    return value if not separator else f"{stem}.{extension.casefold()}"


def _normalize_xml(content: bytes, border_fills: dict[str, str]) -> bytes:
    root = ElementTree.fromstring(content)
    _, unique_border_fills = _border_fill_maps(root)
    for element in root.iter():
        if element.tag == _OPF_META and element.get("name") == "ModifiedDate":
            element.text = ""
        if _local_name(element.tag) == "presentation":
            element.set("applyto", element.get("applyto", "WholeDoc"))
        if _local_name(element.tag) == "paraPr" and element.get("textDir") == "AUTO":
            element.set("textDir", "LTR")
        for attribute, value in tuple(element.attrib.items()):
            name = _local_name(attribute)
            if name == "borderFillIDRef" and value not in {"", "0"}:
                try:
                    element.set(attribute, border_fills[value])
                except KeyError as error:
                    raise DocumentAutomationError(
                        f"HWPX borderFill 참조를 찾을 수 없습니다: {value}"
                    ) from error
            elif name == "href":
                element.set(attribute, _normalize_href(value))
    container = root.find(f".//{_BORDER_FILLS}")
    if container is not None:
        container[:] = unique_border_fills
        container.set("itemCnt", str(len(unique_border_fills)))
    _sort_attributes(root)
    return _element_bytes(root)


def _normalized_member_name(name: str) -> str:
    if not name.startswith("BinData/"):
        return name
    stem, separator, extension = name.rpartition(".")
    return name if not separator else f"{stem}.{extension.casefold()}"


def structural_signature(path: Path) -> _StructuralSignature:
    digest = hashlib.sha256()
    try:
        with ZipFile(path) as archive:
            members = sorted(
                (
                    _normalized_member_name(item.filename),
                    item.filename,
                )
                for item in archive.infolist()
                if not item.is_dir()
                and item.filename.startswith(_STRUCTURAL_PREFIXES)
            )
            if not any(name.startswith("Contents/section") for name, _ in members):
                raise DocumentAutomationError(
                    f"HWPX 본문 구조를 찾을 수 없습니다: {path}"
                )
            header = ElementTree.fromstring(archive.read("Contents/header.xml"))
            border_fills, _ = _border_fill_maps(header)
            for normalized_name, archive_name in members:
                content = archive.read(archive_name)
                if normalized_name.startswith("Contents/"):
                    content = _normalize_xml(content, border_fills)
                digest.update(normalized_name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(hashlib.sha256(content).digest())
    except (
        BadZipFile,
        ElementTree.ParseError,
        KeyError,
        OSError,
        UnicodeError,
    ) as error:
        raise DocumentAutomationError(f"HWPX 구조를 읽을 수 없습니다: {path}") from error
    return _StructuralSignature(digest.hexdigest(), len(members))

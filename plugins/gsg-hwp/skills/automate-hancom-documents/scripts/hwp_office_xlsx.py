from __future__ import annotations

import posixpath
import re
from pathlib import Path
from xml.etree import ElementTree
from xml.etree.ElementTree import Element
from zipfile import BadZipFile, ZipFile

from hwp_errors import HwpLiveError


_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _name(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _column(value: str) -> int:
    letters = re.match(r"[A-Z]+", value.upper())
    if letters is None:
        raise HwpLiveError(f"엑셀 셀 주소가 올바르지 않습니다: {value}")
    result = 0
    for character in letters.group():
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _coordinate(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", value.upper())
    if match is None:
        raise HwpLiveError(f"엑셀 셀 주소가 올바르지 않습니다: {value}")
    return int(match.group(2)) - 1, _column(match.group(1))


def _range(value: str) -> tuple[int, int, int, int]:
    # `$A$1:$F$37` and `A1:F37` are the same Excel range. hwp_office_excel_com
    # ._range already drops the "$" for .xls, so rejecting it here made a valid
    # range depend on the workbook format. Only "$" is removed: a sheet-qualified
    # or malformed address still fails in _coordinate below.
    parts = value.replace("$", "").split(":")
    if len(parts) not in {1, 2}:
        raise HwpLiveError("엑셀 범위는 A1 또는 A1:D20 형식이어야 합니다")
    start = _coordinate(parts[0])
    end = _coordinate(parts[-1])
    if end[0] < start[0] or end[1] < start[1]:
        raise HwpLiveError("엑셀 범위의 끝 셀이 시작 셀보다 앞에 있습니다")
    return start[0], start[1], end[0], end[1]


def _text(element: Element) -> str:
    return "".join(item.text or "" for item in element.iter() if _name(item) == "t")


def _shared(archive: ZipFile) -> tuple[str, ...]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return ()
    return tuple(_text(item) for item in root if _name(item) == "si")


def _sheet_path(
    archive: ZipFile,
    sheet_name: str | None,
    sheet_index: int,
) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    sheets = tuple(item for item in workbook.iter() if _name(item) == "sheet")
    if sheet_name is None:
        if sheet_index >= len(sheets):
            raise HwpLiveError("요청한 엑셀 시트 번호가 없습니다")
        sheet = sheets[sheet_index]
    else:
        sheet = next((item for item in sheets if item.attrib.get("name") == sheet_name), None)
        if sheet is None:
            raise HwpLiveError(f"엑셀 시트가 없습니다: {sheet_name}")
    relationship_id = sheet.attrib.get(_REL_ID, "")
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(
        (
            item.attrib.get("Target", "")
            for item in relationships
            if item.attrib.get("Id") == relationship_id
        ),
        "",
    )
    if not target:
        raise HwpLiveError("엑셀 시트 파일 연결을 찾지 못했습니다")
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("xl", target))


def _cell_value(cell: Element, shared: tuple[str, ...]) -> str:
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return _text(cell)
    raw = next((item.text or "" for item in cell if _name(item) == "v"), "")
    if kind == "s" and raw:
        try:
            return shared[int(raw)]
        except (IndexError, ValueError) as error:
            raise HwpLiveError("엑셀 공유 문자열 번호가 올바르지 않습니다") from error
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def read_xlsx(
    path: Path,
    *,
    sheet_name: str | None,
    sheet_index: int,
    cell_range: str | None,
) -> tuple[tuple[str, ...], ...]:
    try:
        with ZipFile(path) as archive:
            shared = _shared(archive)
            root = ElementTree.fromstring(
                archive.read(_sheet_path(archive, sheet_name, sheet_index))
            )
    except (BadZipFile, KeyError, OSError, ElementTree.ParseError) as error:
        raise HwpLiveError(f"XLSX 표를 읽지 못했습니다: {path}") from error
    values: dict[tuple[int, int], str] = {}
    for cell in (item for item in root.iter() if _name(item) == "c"):
        reference = cell.attrib.get("r", "")
        if reference:
            values[_coordinate(reference)] = _cell_value(cell, shared)
    if cell_range is not None:
        first_row, first_col, last_row, last_col = _range(cell_range)
    elif values:
        first_row = min(row for row, _ in values)
        first_col = min(column for _, column in values)
        last_row = max(row for row, _ in values)
        last_col = max(column for _, column in values)
    else:
        raise HwpLiveError("엑셀 시트에 표 값이 없습니다")
    return tuple(
        tuple(values.get((row, column), "") for column in range(first_col, last_col + 1))
        for row in range(first_row, last_row + 1)
    )

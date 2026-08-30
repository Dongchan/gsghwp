from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element
from typing import Final
from zipfile import BadZipFile, ZipFile

from hwp_errors import HwpLiveError
from hwp_office_xlsx_safety import (
    XlsxReadLimits,
    iter_xml_elements,
    validate_xlsx_archive,
)


_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_DEFAULT_LIMITS: Final = XlsxReadLimits()
# 한 시트가 실을 수 있는 <mergeCell> 개수 상한이다. XlsxReadLimits 는 바이트와
# 문자 수를 세는 자리라 여기서만 쓰는 개수 한도는 이 모듈에 둔다.
_MERGED_RANGE_LIMIT: Final = 100_000


@dataclass(frozen=True, slots=True)
class XlsxSheet:
    name: str
    path: str


@dataclass(frozen=True, slots=True)
class XlsxRow:
    index: int
    values: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class XlsxMerge:
    """A ``<mergeCell>`` rectangle, zero-based like ``_range``.

    The workbook only stores the value at ``first_row``/``first_column``; every
    other cell the rectangle covers is absent from ``sheetData``. Reading the
    grid without this makes a merged heading look like an empty string, which
    is what forced callers to map columns by hand.
    """

    first_row: int
    first_column: int
    last_row: int
    last_column: int
    ref: str


@dataclass(frozen=True, slots=True)
class XlsxGrid:
    """A value grid plus the merges the workbook actually declares.

    ``merges`` travels beside ``values`` rather than being folded into it: a
    filled-in label is an inference, and the caller has to be able to say which
    cells were written in the file and which came from a merge.
    """

    values: tuple[tuple[str, ...], ...]
    first_row: int
    first_column: int
    merges: tuple[XlsxMerge, ...]


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
    parts = value.replace("$", "").split(":")
    if len(parts) not in {1, 2}:
        raise HwpLiveError("엑셀 범위는 A1 또는 A1:D20 형식이어야 합니다")
    start = _coordinate(parts[0])
    end = _coordinate(parts[-1])
    if end[0] < start[0] or end[1] < start[1]:
        raise HwpLiveError("엑셀 범위의 끝 셀이 시작 셀보다 앞에 있습니다")
    return start[0], start[1], end[0], end[1]


def _text(element: Element, limits: XlsxReadLimits) -> str:
    value = "".join(item.text or "" for item in element.iter() if _name(item) == "t")
    if len(value) > limits.cell_chars:
        raise HwpLiveError("XLSX cell character limit exceeded")
    return value


def _shared(archive: ZipFile, limits: XlsxReadLimits) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    values: list[str] = []
    total_bytes = 0
    for item in iter_xml_elements(archive, "xl/sharedStrings.xml", "si", limits):
        value = _text(item, limits)
        values.append(value)
        total_bytes += len(value.encode())
        if len(values) > limits.shared_strings:
            raise HwpLiveError("XLSX shared string count limit exceeded")
        if total_bytes > limits.shared_string_bytes:
            raise HwpLiveError("XLSX shared string byte limit exceeded")
    return tuple(values)


def sheet_inventory(
    archive: ZipFile, limits: XlsxReadLimits = _DEFAULT_LIMITS
) -> tuple[XlsxSheet, ...]:
    """Return bounded workbook-order sheet names and safe internal paths."""
    validate_xlsx_archive(archive, limits)
    relationships: dict[str, str] = {}
    for item in iter_xml_elements(
        archive, "xl/_rels/workbook.xml.rels", "Relationship", limits
    ):
        if item.attrib.get("TargetMode", "").casefold() == "external":
            raise HwpLiveError("XLSX external worksheet relationships are forbidden")
        relationships[item.attrib.get("Id", "")] = item.attrib.get("Target", "")
    result: list[XlsxSheet] = []
    for sheet in iter_xml_elements(archive, "xl/workbook.xml", "sheet", limits):
        name = sheet.attrib.get("name", "")
        target = relationships.get(sheet.attrib.get(_REL_ID, ""), "")
        if not name or not target:
            raise HwpLiveError("엑셀 시트 파일 연결을 찾지 못했습니다")
        if ":" in target:
            raise HwpLiveError("XLSX worksheet relationship target is unsafe")
        path = posixpath.normpath(
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.join("xl", target)
        )
        if not path.startswith("xl/worksheets/"):
            raise HwpLiveError("XLSX worksheet relationship traversal is forbidden")
        result.append(XlsxSheet(name=name, path=path))
    return tuple(result)


def _sheet_path(
    archive: ZipFile,
    sheet_name: str | None,
    sheet_index: int,
    limits: XlsxReadLimits,
) -> str:
    sheets = sheet_inventory(archive, limits)
    if sheet_name is None:
        if sheet_index >= len(sheets):
            raise HwpLiveError("요청한 엑셀 시트 번호가 없습니다")
        return sheets[sheet_index].path
    sheet = next((item for item in sheets if item.name == sheet_name), None)
    if sheet is None:
        raise HwpLiveError(f"엑셀 시트가 없습니다: {sheet_name}")
    return sheet.path


def _cell_value(
    cell: Element,
    shared: tuple[str, ...],
    limits: XlsxReadLimits,
    *,
    reject_formulas: bool,
) -> str:
    if reject_formulas and any(_name(item) == "f" for item in cell):
        raise HwpLiveError("XLSX formula cells are forbidden in literal-only workflow")
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return _text(cell, limits)
    raw = next((item.text or "" for item in cell if _name(item) == "v"), "")
    if kind == "s" and raw:
        try:
            value = shared[int(raw)]
        except (IndexError, ValueError) as error:
            raise HwpLiveError("엑셀 공유 문자열 번호가 올바르지 않습니다") from error
    elif kind == "b":
        value = "TRUE" if raw == "1" else "FALSE"
    else:
        value = raw
    if len(value) > limits.cell_chars:
        raise HwpLiveError("XLSX cell character limit exceeded")
    return value


def merged_ranges(
    archive: ZipFile,
    sheet: XlsxSheet,
    limits: XlsxReadLimits = _DEFAULT_LIMITS,
) -> tuple[XlsxMerge, ...]:
    """Return the sheet's declared merge rectangles in file order."""
    validate_xlsx_archive(archive, limits)
    merges: list[XlsxMerge] = []
    for element in iter_xml_elements(archive, sheet.path, "mergeCell", limits):
        reference = element.attrib.get("ref", "")
        if not reference:
            continue
        first_row, first_column, last_row, last_column = _range(reference)
        merges.append(
            XlsxMerge(
                first_row,
                first_column,
                last_row,
                last_column,
                reference.replace("$", "").upper(),
            )
        )
        if len(merges) > _MERGED_RANGE_LIMIT:
            raise HwpLiveError("XLSX 병합 셀 개수 한도를 초과했습니다")
    return tuple(merges)


def iter_xlsx_rows(
    archive: ZipFile,
    sheet: XlsxSheet,
    *,
    first_row: int,
    last_row: int,
    columns: frozenset[int],
    max_cells: int,
    limits: XlsxReadLimits = _DEFAULT_LIMITS,
    reject_formulas: bool = False,
) -> Iterator[XlsxRow]:
    """Stream selected literal cells from an already validated workbook."""
    validate_xlsx_archive(archive, limits)
    shared = _shared(archive, limits)
    cell_count = 0
    for element in iter_xml_elements(archive, sheet.path, "row", limits):
        try:
            row_index = int(element.attrib.get("r", "0"))
        except ValueError as error:
            raise HwpLiveError("XLSX row index is invalid") from error
        if row_index > last_row:
            return
        if row_index < first_row:
            continue
        values: list[tuple[int, str]] = []
        for cell in (item for item in element if _name(item) == "c"):
            reference = cell.attrib.get("r", "")
            if not reference:
                continue
            _, column = _coordinate(reference)
            if column not in columns:
                continue
            cell_count += 1
            if cell_count > max_cells:
                raise HwpLiveError("XLSX query cell 한도를 초과했습니다")
            values.append(
                (
                    column,
                    _cell_value(
                        cell,
                        shared,
                        limits,
                        reject_formulas=reject_formulas,
                    ),
                )
            )
        yield XlsxRow(row_index, tuple(sorted(values)))


def read_xlsx_grid(
    path: Path,
    *,
    sheet_name: str | None,
    sheet_index: int,
    cell_range: str | None,
) -> XlsxGrid:
    """Read the value grid and the sheet's merges in the same pass.

    ``read_xlsx`` keeps returning the bare grid so every existing caller is
    unchanged; a caller that has to explain a blank heading asks for this
    instead and gets the merge list beside the values.
    """
    limits = XlsxReadLimits()
    try:
        with path.open("rb") as source, ZipFile(source) as archive:
            sheet = XlsxSheet(
                sheet_name or "selected",
                _sheet_path(archive, sheet_name, sheet_index, limits),
            )
            merges = merged_ranges(archive, sheet, limits)
            selected = _range(cell_range) if cell_range is not None else None
            first_row = 1 if selected is None else selected[0] + 1
            last_row = 1_048_576 if selected is None else selected[2] + 1
            columns = frozenset(
                range(16_384)
                if selected is None
                else range(selected[1], selected[3] + 1)
            )
            rows = tuple(
                iter_xlsx_rows(
                    archive,
                    sheet,
                    first_row=first_row,
                    last_row=last_row,
                    columns=columns,
                    max_cells=1_000_000,
                    limits=limits,
                )
            )
    except (BadZipFile, OSError) as error:
        raise HwpLiveError(f"XLSX 표를 읽지 못했습니다: {path}") from error
    values = {
        (row.index - 1, column): value for row in rows for column, value in row.values
    }
    if selected is None:
        if not values:
            raise HwpLiveError("엑셀 시트에 표 값이 없습니다")
        first_row = min(row for row, _ in values)
        first_col = min(column for _, column in values)
        last_row = max(row for row, _ in values)
        last_col = max(column for _, column in values)
    else:
        first_row, first_col, last_row, last_col = selected
    return XlsxGrid(
        tuple(
            tuple(
                values.get((row, column), "")
                for column in range(first_col, last_col + 1)
            )
            for row in range(first_row, last_row + 1)
        ),
        first_row,
        first_col,
        merges,
    )


def read_xlsx(
    path: Path,
    *,
    sheet_name: str | None,
    sheet_index: int,
    cell_range: str | None,
) -> tuple[tuple[str, ...], ...]:
    return read_xlsx_grid(
        path,
        sheet_name=sheet_name,
        sheet_index=sheet_index,
        cell_range=cell_range,
    ).values

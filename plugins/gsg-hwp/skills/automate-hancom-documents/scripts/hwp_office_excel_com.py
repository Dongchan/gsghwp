from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, runtime_checkable

import pythoncom
from pywintypes import com_error

from hwp_errors import HwpLiveError


@dataclass(frozen=True, slots=True)
class ExcelBorderData:
    line_style: int
    weight: int
    color: int


@dataclass(frozen=True, slots=True)
class ExcelCellData:
    text: str
    covered: bool
    fill_color: int
    fill_pattern: int
    bold: bool
    font_size: float | None
    font_color: int
    font_name: str
    horizontal_alignment: int
    vertical_alignment: int
    wrap_text: bool
    borders: tuple[ExcelBorderData, ExcelBorderData, ExcelBorderData, ExcelBorderData]


@dataclass(frozen=True, slots=True)
class ExcelMergeData:
    row: int
    column: int
    row_span: int
    column_span: int


@dataclass(frozen=True, slots=True)
class ExcelRangeData:
    rows: tuple[tuple[ExcelCellData, ...], ...]
    column_width_points: tuple[float, ...]
    row_height_points: tuple[float, ...]
    merges: tuple[ExcelMergeData, ...]


class _Dimension(Protocol):
    Width: float
    Height: float


class _Border(Protocol):
    LineStyle: int
    Weight: int
    Color: int


class _Borders(Protocol):
    def __call__(self, edge: int) -> _Border: ...


class _Interior(Protocol):
    Color: int
    Pattern: int


class _Font(Protocol):
    Bold: bool
    Size: float | None
    Color: int
    Name: str


class _CellFormatting(Protocol):
    Text: str | int | float | bool | None
    Interior: _Interior
    Font: _Font
    HorizontalAlignment: int
    VerticalAlignment: int
    WrapText: bool
    Borders: _Borders


class _Cell(_CellFormatting, Protocol):
    Address: str
    MergeCells: bool
    MergeArea: _Range


class _Range(Protocol):
    Address: str
    Row: int
    Column: int
    Rows: _Count
    Columns: _Count

    def Cells(self, row: int, column: int) -> _Cell: ...


class _Count(Protocol):
    Count: int


class _Worksheet(Protocol):
    UsedRange: _Range

    def Cells(self, row: int, column: int) -> _Cell: ...

    def Columns(self, column: int) -> _Dimension: ...

    def Rows(self, row: int) -> _Dimension: ...


class _Worksheets(Protocol):
    def __call__(self, key: int | str) -> _Worksheet: ...


class _Workbook(Protocol):
    Worksheets: _Worksheets

    def Close(self, save_changes: bool) -> None: ...


class _Workbooks(Protocol):
    def Open(
        self,
        path: str,
        *,
        UpdateLinks: int,
        ReadOnly: bool,
        IgnoreReadOnlyRecommended: bool,
    ) -> _Workbook: ...


class _ExcelApplication(Protocol):
    Visible: bool
    DisplayAlerts: bool
    Workbooks: _Workbooks

    def Quit(self) -> None: ...


@runtime_checkable
class _Win32Client(Protocol):
    def DispatchEx(self, program_id: str) -> _ExcelApplication: ...


def _coordinate(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\$?([A-Z]+)\$?([1-9][0-9]*)", value.upper())
    if match is None:
        raise HwpLiveError(f"엑셀 셀 주소가 올바르지 않습니다: {value}")
    column = 0
    for character in match.group(1):
        column = column * 26 + ord(character) - ord("A") + 1
    return int(match.group(2)) - 1, column - 1


def _range(value: str) -> tuple[int, int, int, int]:
    parts = value.replace("$", "").split(":")
    if len(parts) not in {1, 2}:
        raise HwpLiveError("엑셀 범위는 A1 또는 A1:F37 형식이어야 합니다")
    first = _coordinate(parts[0])
    last = _coordinate(parts[-1])
    if last[0] < first[0] or last[1] < first[1]:
        raise HwpLiveError("엑셀 범위의 끝 셀이 시작 셀보다 앞에 있습니다")
    return first[0], first[1], last[0], last[1]


def _clip_merge(
    *,
    top: int,
    left: int,
    bottom: int,
    right: int,
    first_row: int,
    first_column: int,
    last_row: int,
    last_column: int,
) -> tuple[int, int, int, int]:
    return (
        max(top, first_row),
        max(left, first_column),
        min(bottom, last_row),
        min(right, last_column),
    )


def _border(cell: _CellFormatting, edge: int) -> ExcelBorderData:
    try:
        border = cell.Borders(edge)
        return ExcelBorderData(int(border.LineStyle), int(border.Weight), int(border.Color))
    except (AttributeError, TypeError, ValueError, com_error):
        return ExcelBorderData(-4142, 2, 0)


def _cell(cell: _CellFormatting, covered: bool) -> ExcelCellData:
    text = "" if cell.Text is None else str(cell.Text).strip()
    font_size = cell.Font.Size
    return ExcelCellData(
        text=text,
        covered=covered,
        fill_color=int(cell.Interior.Color),
        fill_pattern=int(cell.Interior.Pattern),
        bold=bool(cell.Font.Bold),
        font_size=None if font_size is None else float(font_size),
        font_color=int(cell.Font.Color),
        font_name=str(cell.Font.Name),
        horizontal_alignment=int(cell.HorizontalAlignment),
        vertical_alignment=int(cell.VerticalAlignment),
        wrap_text=bool(cell.WrapText),
        borders=(
            _border(cell, 7),
            _border(cell, 10),
            _border(cell, 8),
            _border(cell, 9),
        ),
    )


def read_excel_range(
    path: Path,
    *,
    sheet_name: str | None = None,
    sheet_index: int = 0,
    cell_range: str | None = None,
) -> ExcelRangeData:
    pythoncom.CoInitialize()
    application: _ExcelApplication | None = None
    workbook: _Workbook | None = None
    try:
        client = import_module("win32com.client")
        if not isinstance(client, _Win32Client):
            raise HwpLiveError("win32com.client 모듈 계약이 올바르지 않습니다")
        application = client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        workbook = application.Workbooks.Open(
            str(path.resolve()),
            UpdateLinks=0,
            ReadOnly=True,
            IgnoreReadOnlyRecommended=True,
        )
        worksheet = workbook.Worksheets(sheet_name if sheet_name is not None else sheet_index + 1)
        if cell_range is None:
            used = worksheet.UsedRange
            first_row = int(used.Row) - 1
            first_column = int(used.Column) - 1
            last_row = first_row + int(used.Rows.Count) - 1
            last_column = first_column + int(used.Columns.Count) - 1
        else:
            first_row, first_column, last_row, last_column = _range(cell_range)
        merges: set[ExcelMergeData] = set()
        rows: list[tuple[ExcelCellData, ...]] = []
        for row in range(first_row, last_row + 1):
            values: list[ExcelCellData] = []
            for column in range(first_column, last_column + 1):
                cell = worksheet.Cells(row + 1, column + 1)
                source_cell = cell
                covered = False
                if bool(cell.MergeCells):
                    top, left, bottom, right = _range(cell.MergeArea.Address)
                    clipped_top, clipped_left, clipped_bottom, clipped_right = _clip_merge(
                        top=top,
                        left=left,
                        bottom=bottom,
                        right=right,
                        first_row=first_row,
                        first_column=first_column,
                        last_row=last_row,
                        last_column=last_column,
                    )
                    covered = (row, column) != (clipped_top, clipped_left)
                    if not covered:
                        source_cell = worksheet.Cells(top + 1, left + 1)
                        row_span = clipped_bottom - clipped_top + 1
                        column_span = clipped_right - clipped_left + 1
                        if row_span > 1 or column_span > 1:
                            merges.add(
                                ExcelMergeData(
                                    row=clipped_top - first_row,
                                    column=clipped_left - first_column,
                                    row_span=row_span,
                                    column_span=column_span,
                                )
                            )
                values.append(_cell(source_cell, covered))
            rows.append(tuple(values))
        return ExcelRangeData(
            rows=tuple(rows),
            column_width_points=tuple(
                float(worksheet.Columns(column + 1).Width)
                for column in range(first_column, last_column + 1)
            ),
            row_height_points=tuple(
                float(worksheet.Rows(row + 1).Height)
                for row in range(first_row, last_row + 1)
            ),
            merges=tuple(sorted(merges, key=lambda item: (item.row, item.column))),
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error) as error:
        raise HwpLiveError(f"Excel 표를 읽지 못했습니다: {path}") from error
    finally:
        try:
            if workbook is not None:
                workbook.Close(False)
            if application is not None:
                application.Quit()
        finally:
            pythoncom.CoUninitialize()


def read_excel_matrix(
    path: Path,
    *,
    sheet_name: str | None,
    sheet_index: int,
    cell_range: str | None,
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(cell.text if not cell.covered else "" for cell in row)
        for row in read_excel_range(
            path,
            sheet_name=sheet_name,
            sheet_index=sheet_index,
            cell_range=cell_range,
        ).rows
    )

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hwp_errors import HwpLiveError
from hwp_office_excel_com import (
    ExcelBorderData,
    ExcelCellData,
    ExcelMergeData,
    ExcelRangeData,
    read_excel_range,
)
from hwp_live_table_contract import (
    BorderStyle,
    BorderWidth,
    CellBorder,
    CellBorders,
    CellPadding,
    TableBlock,
    TableCell,
    TableMerge,
)
from hwp_live_values import Alignment, Rgb
from hwp_table_readability import recommended_cell_width


_MIN_COLUMN_WIDTH_MM = 5.0
_MAX_COLUMN_WIDTH_MM = 250.0


def _rgb(value: int) -> Rgb:
    return value & 0xFF, value >> 8 & 0xFF, value >> 16 & 0xFF


def _alignment(value: int) -> Alignment:
    if value == -4108:
        return "center"
    if value == -4152:
        return "right"
    if value == -4131:
        return "left"
    return "right"


def _border(value: ExcelBorderData) -> CellBorder | None:
    styles: dict[int, BorderStyle] = {
        1: "solid",
        -4115: "dash",
        -4118: "dot",
        4: "dash_dot",
        5: "dash_dot_dot",
        -4119: "double_slim",
    }
    style = styles.get(value.line_style)
    if style is None:
        return None
    widths: dict[int, BorderWidth] = {
        1: "0.1mm",
        2: "0.12mm",
        -4138: "0.3mm",
        4: "0.5mm",
    }
    return CellBorder(
        style=style,
        width=widths.get(value.weight, "0.12mm"),
        color=_rgb(value.color),
    )


def _fit_column_widths(
    width_points: tuple[float, ...],
    *,
    target_width_mm: float,
    minimum_widths_mm: tuple[float, ...] | None = None,
) -> tuple[float, ...]:
    if not width_points or any(width < 0 for width in width_points):
        raise ValueError("Excel column widths must be non-negative")
    minimums = minimum_widths_mm or tuple(_MIN_COLUMN_WIDTH_MM for _ in width_points)
    if len(minimums) != len(width_points):
        raise ValueError("minimum widths must match the Excel column count")
    if any(width < _MIN_COLUMN_WIDTH_MM for width in minimums):
        raise ValueError("minimum widths are below the HWP column limit")
    minimum_total = sum(minimums)
    maximum_total = len(width_points) * _MAX_COLUMN_WIDTH_MM
    if target_width_mm < minimum_total or target_width_mm > maximum_total:
        raise ValueError("target width cannot satisfy the HWP column width limits")

    fitted = [0.0] * len(width_points)
    active = set(range(len(width_points)))
    remaining_width = target_width_mm
    while active:
        source_total = sum(width_points[index] for index in active)
        if source_total == 0:
            equal_width = remaining_width / len(active)
            for index in active:
                fitted[index] = equal_width
            break
        scaled = {
            index: remaining_width * width_points[index] / source_total
            for index in active
        }
        narrow = {index for index, width in scaled.items() if width < minimums[index]}
        wide = {
            index for index, width in scaled.items() if width > _MAX_COLUMN_WIDTH_MM
        }
        if not narrow and not wide:
            for index, width in scaled.items():
                fitted[index] = width
            break
        for index in narrow:
            fitted[index] = minimums[index]
        for index in wide:
            fitted[index] = _MAX_COLUMN_WIDTH_MM
        active.difference_update(narrow | wide)
        remaining_width = target_width_mm - sum(fitted)

    correction = target_width_mm - sum(fitted)
    widest = max(range(len(fitted)), key=fitted.__getitem__)
    fitted[widest] += correction
    return tuple(fitted)


def _cell(value: ExcelCellData, preserve_font: bool) -> TableCell:
    if value.covered:
        return TableCell()
    left, right, top, bottom = value.borders
    fill = None
    if value.fill_pattern != -4142 and value.fill_color != 0xFFFFFF:
        fill = _rgb(value.fill_color)
    vertical = "center"
    if value.vertical_alignment == -4160:
        vertical = "top"
    elif value.vertical_alignment == -4107:
        vertical = "bottom"
    return TableCell(
        text=value.text,
        bold=value.bold,
        font_name=value.font_name if preserve_font else None,
        font_size_pt=(
            None
            if not preserve_font or value.font_size is None
            else max(4.0, min(value.font_size, 96.0))
        ),
        text_color=(
            None
            if not preserve_font or value.font_color == 0
            else _rgb(value.font_color)
        ),
        alignment=_alignment(value.horizontal_alignment),
        vertical_alignment=vertical,
        line_spacing_percent=100,
        fill_color=fill,
        padding=CellPadding(left_mm=0.5, right_mm=0.5, top_mm=0.15, bottom_mm=0.15),
        borders=CellBorders(
            left=_border(left),
            right=_border(right),
            top=_border(top),
            bottom=_border(bottom),
        ),
    )


def trim_blank_edges(source: ExcelRangeData) -> ExcelRangeData:
    occupied = tuple(
        (row, column)
        for row, values in enumerate(source.rows)
        for column, cell in enumerate(values)
        if cell.text.strip()
    )
    if not occupied:
        raise HwpLiveError("선택한 Excel 범위에 한컴 표로 옮길 내용이 없습니다")
    first_row = min(row for row, _ in occupied)
    last_row = max(row for row, _ in occupied)
    first_column = min(column for _, column in occupied)
    last_column = max(column for _, column in occupied)
    return _crop_excel_range(source, first_row, first_column, last_row, last_column)


def _crop_excel_range(
    source: ExcelRangeData,
    first_row: int,
    first_column: int,
    last_row: int,
    last_column: int,
) -> ExcelRangeData:
    merges: list[ExcelMergeData] = []
    for merge in source.merges:
        top = max(merge.row, first_row)
        left = max(merge.column, first_column)
        bottom = min(merge.row + merge.row_span - 1, last_row)
        right = min(merge.column + merge.column_span - 1, last_column)
        if top > bottom or left > right:
            continue
        row_span = bottom - top + 1
        column_span = right - left + 1
        if row_span > 1 or column_span > 1:
            merges.append(
                ExcelMergeData(
                    row=top - first_row,
                    column=left - first_column,
                    row_span=row_span,
                    column_span=column_span,
                )
            )
    return ExcelRangeData(
        rows=tuple(
            tuple(row[first_column : last_column + 1])
            for row in source.rows[first_row : last_row + 1]
        ),
        column_width_points=source.column_width_points[first_column : last_column + 1],
        row_height_points=source.row_height_points[first_row : last_row + 1],
        merges=tuple(merges),
    )


def _row_is_gutter(source: ExcelRangeData, row: int) -> bool:
    if any(cell.text.strip() for cell in source.rows[row]):
        return False
    return all(
        not (merge.row < row < merge.row + merge.row_span - 1)
        for merge in source.merges
    )


def _column_is_gutter(source: ExcelRangeData, column: int) -> bool:
    if any(row[column].text.strip() for row in source.rows):
        return False
    return all(
        not (merge.column < column < merge.column + merge.column_span - 1)
        for merge in source.merges
    )


def _bands(count: int, is_gutter: Callable[[int], bool]) -> tuple[tuple[int, int], ...]:
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for index in range(count):
        if is_gutter(index):
            if start is not None:
                bands.append((start, index - 1))
                start = None
            continue
        if start is None:
            start = index
    if start is not None:
        bands.append((start, count - 1))
    return tuple(bands)


def split_excel_by_gutters(source: ExcelRangeData) -> tuple[ExcelRangeData, ...]:
    if not source.rows:
        return (source,)
    row_bands = _bands(len(source.rows), lambda row: _row_is_gutter(source, row))
    pieces: list[ExcelRangeData] = []
    for top, bottom in row_bands or ((0, len(source.rows) - 1),):
        band = _crop_excel_range(source, top, 0, bottom, len(source.rows[0]) - 1)
        column_bands = _bands(
            len(band.rows[0]),
            lambda column: _column_is_gutter(band, column),
        )
        for left, right in column_bands or ((0, len(band.rows[0]) - 1),):
            pieces.append(_crop_excel_range(band, 0, left, len(band.rows) - 1, right))
    return tuple(pieces) if pieces else (source,)


def _excel_column_minimums(source: ExcelRangeData) -> tuple[float, ...]:
    columns = len(source.rows[0])
    minimums = [5.0] * columns
    merge_by_owner = {(merge.row, merge.column): merge for merge in source.merges}
    for row_index, row in enumerate(source.rows):
        for column, cell in enumerate(row):
            if cell.covered or not cell.text.strip():
                continue
            merge = merge_by_owner.get((row_index, column))
            span = merge.column_span if merge is not None else 1
            required = recommended_cell_width(cell.text)
            if span == 1:
                required = min(32.0, required)
            selected_widths = source.column_width_points[column : column + span]
            source_total = sum(selected_widths)
            for offset, source_width in enumerate(selected_widths):
                share = (
                    required / span
                    if source_total == 0
                    else required * source_width / source_total
                )
                minimums[column + offset] = max(
                    minimums[column + offset],
                    min(250.0, share),
                )
    return tuple(round(width, 2) for width in minimums)


def _table_block_from_range(
    source: ExcelRangeData,
    *,
    target_width_mm: float | None,
    base_style_name: str | None,
    caption: str | None,
    caption_style_name: str | None,
    repeat_header: bool,
    preserve_font: bool,
    preserve_row_heights: bool,
) -> TableBlock:
    minimums = _excel_column_minimums(source)
    widths = (
        None
        if target_width_mm is None
        else _fit_column_widths(
            source.column_width_points,
            target_width_mm=target_width_mm,
            minimum_widths_mm=minimums,
        )
    )
    weights = source.column_width_points if target_width_mm is None else None
    point_to_mm = 25.4 / 72.0
    heights = (
        tuple(
            max(3.0, min(30.0, height * point_to_mm))
            for height in source.row_height_points
        )
        if preserve_row_heights
        else None
    )
    wide = len(source.rows[0]) > 1
    return TableBlock(
        kind="table",
        caption=caption,
        caption_style_name=caption_style_name,
        base_style_name=base_style_name,
        rows=tuple(
            tuple(_cell(cell, preserve_font) for cell in row) for row in source.rows
        ),
        column_widths_mm=widths,
        column_width_weights=weights,
        minimum_column_widths_mm=minimums,
        row_heights_mm=heights,
        repeat_header=repeat_header,
        split_wide_table=wide,
        repeat_key_columns=1 if wide and not source.merges else 0,
        merges=tuple(
            TableMerge(
                row=merge.row,
                column=merge.column,
                row_span=merge.row_span,
                column_span=merge.column_span,
            )
            for merge in source.merges
        ),
        left_margin_mm=0,
        right_margin_mm=0,
        indentation_mm=0,
    )


def table_block_from_excel(
    path: Path,
    *,
    sheet_name: str | None = None,
    sheet_index: int = 0,
    cell_range: str | None = None,
    target_width_mm: float | None = None,
    base_style_name: str | None = None,
    caption: str | None = None,
    caption_style_name: str | None = None,
    repeat_header: bool = False,
    preserve_font: bool = False,
    preserve_row_heights: bool = True,
    trim_unused_edges: bool = True,
) -> TableBlock:
    return table_blocks_from_excel(
        path,
        sheet_name=sheet_name,
        sheet_index=sheet_index,
        cell_range=cell_range,
        target_width_mm=target_width_mm,
        base_style_name=base_style_name,
        caption=caption,
        caption_style_name=caption_style_name,
        repeat_header=repeat_header,
        preserve_font=preserve_font,
        preserve_row_heights=preserve_row_heights,
        trim_unused_edges=trim_unused_edges,
        split_gutters=False,
    )[0]


def table_blocks_from_excel(
    path: Path,
    *,
    sheet_name: str | None = None,
    sheet_index: int = 0,
    cell_range: str | None = None,
    target_width_mm: float | None = None,
    base_style_name: str | None = None,
    caption: str | None = None,
    caption_style_name: str | None = None,
    repeat_header: bool = False,
    preserve_font: bool = False,
    preserve_row_heights: bool = True,
    trim_unused_edges: bool = True,
    split_gutters: bool = True,
) -> tuple[TableBlock, ...]:
    source = read_excel_range(
        path,
        sheet_name=sheet_name,
        sheet_index=sheet_index,
        cell_range=cell_range,
    )
    if trim_unused_edges:
        source = trim_blank_edges(source)
    pieces = split_excel_by_gutters(source) if split_gutters else (source,)
    blocks = [
        _table_block_from_range(
            piece,
            target_width_mm=target_width_mm,
            base_style_name=base_style_name,
            caption=caption if len(pieces) == 1 else None,
            caption_style_name=caption_style_name if len(pieces) == 1 else None,
            repeat_header=repeat_header,
            preserve_font=preserve_font,
            preserve_row_heights=preserve_row_heights,
        )
        for piece in pieces
    ]
    return tuple(blocks)

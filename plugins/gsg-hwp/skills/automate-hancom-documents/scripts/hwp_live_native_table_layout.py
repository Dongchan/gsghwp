from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    BooleanValue,
    CaptionCommand,
    CellCommand,
    InsertPictureCommand,
    InsertTextCommand,
    IntegerValue,
    LeaveTableCommand,
    MergeCommand,
    MillimeterValue,
    NativeActionCommand,
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSetter,
    ParameterActionCommand,
    RunCommand,
)
from hwp_live_native_layout_format import cell_format_commands
from hwp_live_native_text_format import ParagraphFormatting, paragraph_command, style_command
from hwp_live_table_contract import TableBlock, TableCell


def cell_address(row: int, column: int) -> str:
    if row < 0 or column < 0:
        raise HwpLiveError("표 셀 행과 열은 음수일 수 없습니다")
    letters = ""
    value = column + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"


def _style_id(
    styles: Mapping[str, int],
    name: str | None,
    explicit: int | None,
    label: str,
) -> int:
    if explicit is not None:
        return explicit
    if name is None:
        raise HwpLiveError(f"{label} 스타일이 자동 해석되지 않았습니다")
    try:
        return styles[name]
    except KeyError as error:
        raise HwpLiveError(f"현재 문서에 {label} 스타일 '{name}'이 없습니다") from error


def _table_create(block: TableBlock) -> ParameterActionCommand:
    rows = len(block.rows)
    columns = len(block.rows[0])
    widths = block.column_widths_mm
    if widths is None:
        widths = tuple(169.0 / columns for _ in range(columns))
    height = sum(block.row_heights_mm) if block.row_heights_mm is not None else rows * 8.0
    return ParameterActionCommand(
        action="TableCreate",
        parameter_set="HTableCreation",
        setters=(
            NativeSetter("Rows", IntegerValue(rows)),
            NativeSetter("Cols", IntegerValue(columns)),
            NativeSetter("WidthType", IntegerValue(2)),
            NativeSetter("HeightType", IntegerValue(0)),
            NativeSetter("WidthValue", MillimeterValue(sum(widths))),
            NativeSetter("HeightValue", MillimeterValue(height)),
            NativeSetter("TableProperties/TreatAsChar", BooleanValue(True)),
        ),
    )


def _size_action(name: str, millimeters: float) -> ParameterActionCommand:
    return ParameterActionCommand(
        action="TablePropertyDialog",
        parameter_set="HShapeObject",
        setters=(
            NativeSetter("HSet/ShapeType", IntegerValue(3)),
            NativeSetter("HSet/ShapeCellSize", IntegerValue(1)),
            NativeSetter(f"ShapeTableCell/{name}", MillimeterValue(millimeters)),
        ),
    )


def _select_range(
    first: str,
    move_action: str,
    count: int,
) -> tuple[NativeActionCommand, ...]:
    return (
        CellCommand(first),
        RunCommand("TableCellBlock"),
        RunCommand("TableCellBlockExtend"),
        *(RunCommand(move_action) for _ in range(count)),
    )


def _geometry_commands(block: TableBlock) -> tuple[NativeActionCommand, ...]:
    commands: list[NativeActionCommand] = []
    rows = len(block.rows)
    columns = len(block.rows[0])
    if block.column_widths_mm is not None:
        for column, width in enumerate(block.column_widths_mm):
            commands.extend(_select_range(cell_address(0, column), "TableLowerCell", rows - 1))
            commands.extend((_size_action("Width", width), RunCommand("Cancel")))
    if block.row_heights_mm is not None:
        for row, height in enumerate(block.row_heights_mm):
            commands.extend(_select_range(cell_address(row, 0), "TableRightCell", columns - 1))
            commands.extend((_size_action("Height", height), RunCommand("Cancel")))
    return tuple(commands)


def _picture_command(
    cell: TableCell,
    assets: Mapping[Path, Path],
) -> InsertPictureCommand:
    image = cell.image_path
    width = cell.image_width_mm
    height = cell.image_height_mm
    if image is None or width is None or height is None:
        raise HwpLiveError("표 셀 그림의 경로 또는 배치 영역이 없습니다")
    try:
        asset = assets[image]
    except KeyError as error:
        raise HwpLiveError(f"표 셀 그림 파일이 준비되지 않았습니다: {image}") from error
    return InsertPictureCommand(path=asset, width_mm=width, height_mm=height)


def _merge_commands(block: TableBlock) -> tuple[NativeActionCommand, ...]:
    columns = len(block.rows[0])
    commands: list[NativeActionCommand] = []
    for merge in sorted(
        block.merges,
        key=lambda item: item.row * columns + item.column,
        reverse=True,
    ):
        anchor = cell_address(merge.row, merge.column)
        endpoint = cell_address(
            merge.row + merge.row_span - 1,
            merge.column + merge.column_span - 1,
        )
        commands.extend((MergeCommand(anchor, endpoint), CellCommand(anchor)))
        commands.extend(cell_format_commands(block.rows[merge.row][merge.column]))
    return tuple(commands)


def table_commands(
    block: TableBlock,
    assets: Mapping[Path, Path],
    styles: Mapping[str, int],
    text_formats: Mapping[
        str, tuple[NativeCharacterFormat, NativeParagraphFormat]
    ],
    caption_format_sources: Mapping[str, NativePosition],
) -> tuple[NativeActionCommand, ...]:
    base_style = _style_id(
        styles,
        block.base_style_name,
        block.base_style_id,
        "표 기준",
    )
    commands: list[NativeActionCommand] = [style_command(base_style)]
    paragraph = paragraph_command(
        ParagraphFormatting(
            alignment=block.alignment,
            left_mm=block.left_margin_mm,
            right_mm=block.right_margin_mm,
            indentation_mm=block.indentation_mm,
        )
    )
    if paragraph is not None:
        commands.append(paragraph)
    commands.extend((_table_create(block), *_geometry_commands(block)))
    for row, cells in enumerate(block.rows):
        for column, cell in enumerate(cells):
            commands.extend((CellCommand(cell_address(row, column)), style_command(base_style)))
            commands.extend(cell_format_commands(cell))
            if cell.text:
                for line_index, line in enumerate(cell.text.split("\n")):
                    if line_index:
                        commands.append(RunCommand("BreakPara"))
                    if line:
                        commands.append(InsertTextCommand(line))
            if cell.image_path is not None:
                if cell.text:
                    commands.append(RunCommand("BreakPara"))
                commands.append(_picture_command(cell, assets))
    commands.extend(_geometry_commands(block))
    commands.extend(_merge_commands(block))
    if block.caption is not None:
        caption_style = _style_id(
            styles,
            block.caption_style_name,
            block.caption_style_id,
            "표 캡션",
        )
        caption_format = text_formats.get(block.caption_style_name or "")
        format_source = caption_format_sources.get(block.caption_style_name or "")
        if caption_format is None:
            commands.append(
                CaptionCommand(
                    caption_style,
                    block.caption,
                    format_source=format_source,
                )
            )
        else:
            character, paragraph = caption_format
            commands.append(
                CaptionCommand(
                    caption_style,
                    block.caption,
                    character,
                    paragraph,
                    format_source,
                )
            )
    commands.append(LeaveTableCommand())
    return tuple(commands)

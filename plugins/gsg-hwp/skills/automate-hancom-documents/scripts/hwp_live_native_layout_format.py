from __future__ import annotations

from typing import assert_never

from hwp_live_native_action_models import (
    EnumerationValue,
    IntegerValue,
    MillimeterValue,
    NativeActionCommand,
    NativeSetter,
    ParameterActionCommand,
    RunCommand,
)
from hwp_live_native_text_format import (
    ParagraphFormatting,
    character_command,
    paragraph_command,
    rgb_value,
    style_command,
)
from hwp_live_table_contract import CellBorders, TableCell


def _padding_command(cell: TableCell) -> ParameterActionCommand | None:
    padding = cell.padding
    if padding is None:
        return None
    return ParameterActionCommand(
        action="TablePropertyDialog",
        parameter_set="HShapeObject",
        setters=(
            NativeSetter("HSet/ShapeType", IntegerValue(3)),
            NativeSetter("HSet/ShapeCellSize", IntegerValue(0)),
            NativeSetter("ShapeTableCell/HasMargin", IntegerValue(1)),
            NativeSetter("ShapeTableCell/MarginLeft", MillimeterValue(padding.left_mm)),
            NativeSetter("ShapeTableCell/MarginRight", MillimeterValue(padding.right_mm)),
            NativeSetter("ShapeTableCell/MarginTop", MillimeterValue(padding.top_mm)),
            NativeSetter("ShapeTableCell/MarginBottom", MillimeterValue(padding.bottom_mm)),
        ),
    )


def _fill_command(cell: TableCell) -> ParameterActionCommand | None:
    if cell.fill_color is None:
        return None
    return ParameterActionCommand(
        action="CellFill",
        parameter_set="HCellBorderFill",
        setters=(
            NativeSetter(
                "FillAttr/Type",
                EnumerationValue("BrushType", "NullBrush|WinBrush"),
            ),
            NativeSetter(
                "FillAttr/WinBrushFaceColor",
                IntegerValue(rgb_value(cell.fill_color)),
            ),
            NativeSetter("FillAttr/WinBrushHatchColor", IntegerValue(0x999999)),
            NativeSetter(
                "FillAttr/WinBrushFaceStyle",
                EnumerationValue("HatchStyle", "None"),
            ),
            NativeSetter("FillAttr/WindowsBrush", IntegerValue(1)),
        ),
    )


def _border_command(borders: CellBorders | None) -> ParameterActionCommand | None:
    if borders is None:
        return None
    setters: list[NativeSetter] = []
    sides = (
        ("Left", "BorderCorlorLeft", borders.left),
        ("Right", "BorderColorRight", borders.right),
        ("Top", "BorderColorTop", borders.top),
        ("Bottom", "BorderColorBottom", borders.bottom),
    )
    line_types = {
        "none": "None",
        "solid": "Solid",
        "dash": "Dash",
        "dot": "Dot",
        "dash_dot": "DashDot",
        "dash_dot_dot": "DashDotDot",
        "long_dash": "LongDash",
        "circle": "Circle",
        "double_slim": "DoubleSlim",
        "slim_thick": "SlimThick",
        "thick_slim": "ThickSlim",
        "slim_thick_slim": "SlimThickSlim",
    }
    for side, color_name, border in sides:
        if border is None:
            continue
        setters.extend(
            (
                NativeSetter(
                    f"HSet/BorderType{side}",
                    EnumerationValue("HwpLineType", line_types[border.style]),
                ),
                NativeSetter(
                    f"HSet/BorderWidth{side}",
                    EnumerationValue("HwpLineWidth", border.width),
                ),
                NativeSetter(f"HSet/{color_name}", IntegerValue(rgb_value(border.color))),
            )
        )
    if not setters:
        return None
    return ParameterActionCommand(
        action="CellBorder",
        parameter_set="HCellBorderFill",
        setters=tuple(setters),
    )


def cell_format_commands(cell: TableCell) -> tuple[NativeActionCommand, ...]:
    commands: list[NativeActionCommand] = []
    if cell.style_id is not None:
        commands.append(style_command(cell.style_id))
    character = character_command(cell)
    if character is not None:
        commands.append(character)
    paragraph = paragraph_command(
        ParagraphFormatting(
            alignment=cell.alignment,
            line_spacing=cell.line_spacing_percent,
            left_mm=0 if cell.image_path is not None else None,
            right_mm=0 if cell.image_path is not None else None,
            indentation_mm=0 if cell.image_path is not None else None,
        )
    )
    if paragraph is not None:
        commands.append(paragraph)
    for command in (
        _fill_command(cell),
        _padding_command(cell),
        _border_command(cell.borders),
    ):
        if command is not None:
            commands.append(command)
    match cell.vertical_alignment:
        case "inherit":
            return tuple(commands)
        case "top":
            commands.append(RunCommand("TableVAlignTop"))
            return tuple(commands)
        case "center":
            commands.append(RunCommand("TableVAlignCenter"))
            return tuple(commands)
        case "bottom":
            commands.append(RunCommand("TableVAlignBottom"))
            return tuple(commands)
    assert_never(cell.vertical_alignment)

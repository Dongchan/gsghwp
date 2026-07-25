from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from hwp_color_normalization import parse_rgb_color
from hwp_live_table_contract import CellBorder, CellBorders, TableCell
from hwp_live_values import Alignment, Rgb
from hwp_operation_contract import OperationInputValue


_ADDRESS: Final = re.compile(r"^[A-Z]+[1-9][0-9]*$")
_ALIGNMENTS: Final[dict[str, Alignment]] = {
    "inherit": "inherit",
    "left": "left",
    "center": "center",
    "right": "right",
    "justify": "justify",
}
_VERTICAL_ALIGNMENTS: Final = frozenset(("inherit", "top", "center", "bottom"))
_BORDER_STYLES: Final = frozenset(("none", "solid", "dash", "dot", "dash_dot", "dash_dot_dot", "long_dash", "circle", "double_slim", "slim_thick", "thick_slim", "slim_thick_slim"))
_BORDER_WIDTHS: Final = frozenset(("0.1mm", "0.12mm", "0.15mm", "0.2mm", "0.25mm", "0.3mm", "0.4mm", "0.5mm", "0.6mm", "0.7mm", "1.0mm", "1.5mm", "2.0mm", "3.0mm", "4.0mm", "5.0mm"))
_TEXT_KEYS: Final = frozenset(
    ("bold", "font_name", "font_size_pt", "text_color", "alignment", "line_spacing")
)
_TABLE_KEYS: Final = _TEXT_KEYS | frozenset(
    (
        "cell",
        "row_height_mm",
        "column_width_mm",
        "vertical_alignment",
        "fill_color",
        "border_style",
        "border_width",
        "border_color",
    )
)


@dataclass(frozen=True, slots=True)
class InputFailure:
    status: Literal["needs_input", "schema_conflict"]
    message: str
    required_inputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TextFormatSpec:
    bold: bool | None
    font_name: str | None
    font_size_pt: float | None
    text_color: Rgb | None
    alignment: Alignment
    line_spacing: int | None


@dataclass(frozen=True, slots=True)
class TableFormatSpec:
    cell: str | None
    formatting: TableCell
    row_height_mm: float | None
    column_width_mm: float | None


@dataclass(frozen=True, slots=True)
class MergeSpec:
    start: str
    end: str


@dataclass(frozen=True, slots=True)
class SplitSpec:
    cell: str
    columns: int
    rows: int
    distribute_height: bool
    merge: bool
    split_mode: Literal["equal", "existing_grid"]


def _address(value: OperationInputValue | None) -> str | None:
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized if _ADDRESS.fullmatch(normalized) is not None else None
    return None


def table_cell_coordinate(address: str) -> tuple[int, int]:
    index = 0
    column = 0
    while index < len(address) and address[index].isalpha():
        column = column * 26 + ord(address[index]) - ord("A") + 1
        index += 1
    return int(address[index:]), column


def _millimeters(value: OperationInputValue | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if 1 <= parsed <= 250 else None


def _rgb(value: OperationInputValue | None) -> Rgb | None:
    return parse_rgb_color(value) if isinstance(value, str) else None


def _boolean(value: OperationInputValue | None) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _integer(value: OperationInputValue | None, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if minimum <= value <= maximum else None
    return None


def _unknown(parameters: Mapping[str, OperationInputValue], allowed: frozenset[str]) -> InputFailure | None:
    names = tuple(sorted(set(parameters) - allowed))
    if not names:
        return None
    return InputFailure("schema_conflict", f"지원하지 않는 서식 입력 필드입니다: {', '.join(names)}")


def parse_text_format(
    parameters: Mapping[str, OperationInputValue],
) -> TextFormatSpec | InputFailure:
    unknown = _unknown(parameters, _TEXT_KEYS)
    if unknown is not None:
        return unknown
    if not parameters:
        return InputFailure(
            "needs_input",
            "적용할 글자 또는 문단 서식이 필요합니다",
            tuple(f"inputs.parameters.{name}" for name in sorted(_TEXT_KEYS)),
        )
    bold = None if "bold" not in parameters else _boolean(parameters.get("bold"))
    name = parameters.get("font_name")
    font_name = name.strip() if isinstance(name, str) and name.strip() else None
    font_size_value = parameters.get("font_size_pt")
    font_size = (
        None
        if "font_size_pt" not in parameters
        or isinstance(font_size_value, bool)
        or not isinstance(font_size_value, (int, float))
        or not 1 <= font_size_value <= 96
        else float(font_size_value)
    )
    color = None if "text_color" not in parameters else _rgb(parameters.get("text_color"))
    raw_alignment = parameters.get("alignment", "inherit")
    alignment = _ALIGNMENTS.get(raw_alignment) if isinstance(raw_alignment, str) else None
    spacing = None if "line_spacing" not in parameters else _integer(parameters.get("line_spacing"), 50, 500)
    if alignment is None:
        return InputFailure("schema_conflict", "문단 정렬 값이 올바르지 않습니다")
    invalid = (
        ("bold" in parameters and bold is None)
        or ("font_name" in parameters and font_name is None)
        or ("font_size_pt" in parameters and font_size is None)
        or ("text_color" in parameters and color is None)
        or ("line_spacing" in parameters and spacing is None)
    )
    if invalid:
        return InputFailure("schema_conflict", "글자·문단 서식 입력 형식이나 범위가 올바르지 않습니다")
    return TextFormatSpec(bold, font_name, font_size, color, alignment, spacing)


def parse_table_format(
    parameters: Mapping[str, OperationInputValue],
) -> TableFormatSpec | InputFailure:
    unknown = _unknown(parameters, _TABLE_KEYS)
    if unknown is not None:
        return unknown
    cell = _address(parameters.get("cell"))
    format_parameters = {name: value for name, value in parameters.items() if name in _TEXT_KEYS}
    row_height = (
        None
        if "row_height_mm" not in parameters
        else _millimeters(parameters.get("row_height_mm"))
    )
    column_width = (
        None
        if "column_width_mm" not in parameters
        else _millimeters(parameters.get("column_width_mm"))
    )
    if (
        ("row_height_mm" in parameters and row_height is None)
        or ("column_width_mm" in parameters and column_width is None)
    ):
        return InputFailure(
            "schema_conflict",
            "행 높이와 열 너비는 1mm 이상 250mm 이하여야 합니다",
        )
    text = parse_text_format(format_parameters) if format_parameters else TextFormatSpec(None, None, None, None, "inherit", None)
    if isinstance(text, InputFailure):
        return text
    vertical = parameters.get("vertical_alignment", "inherit")
    if not isinstance(vertical, str) or vertical not in _VERTICAL_ALIGNMENTS:
        return InputFailure("schema_conflict", "셀 세로 정렬 값이 올바르지 않습니다")
    fill = None if "fill_color" not in parameters else _rgb(parameters.get("fill_color"))
    border_color = (0, 0, 0) if "border_color" not in parameters else _rgb(parameters.get("border_color"))
    border_style = parameters.get("border_style", "solid")
    border_width = parameters.get("border_width", "0.12mm")
    has_border = any(name in parameters for name in ("border_color", "border_style", "border_width"))
    if ("fill_color" in parameters and fill is None) or border_color is None:
        return InputFailure("schema_conflict", "셀 색상 값이 올바르지 않습니다")
    if not isinstance(border_style, str) or border_style not in _BORDER_STYLES:
        return InputFailure("schema_conflict", "셀 테두리 종류가 올바르지 않습니다")
    if not isinstance(border_width, str) or border_width not in _BORDER_WIDTHS:
        return InputFailure("schema_conflict", "셀 테두리 두께가 올바르지 않습니다")
    has_format = (
        bool(format_parameters)
        or row_height is not None
        or column_width is not None
        or "vertical_alignment" in parameters
        or fill is not None
        or has_border
    )
    if not has_format:
        return InputFailure("needs_input", "적용할 셀 서식이 필요합니다", ("inputs.parameters.fill_color", "inputs.parameters.border_width"))
    borders = None
    if has_border:
        border = CellBorder(style=border_style, width=border_width, color=border_color)
        borders = CellBorders(left=border, right=border, top=border, bottom=border)
    return TableFormatSpec(
        cell,
        TableCell(
            bold=text.bold,
            font_name=text.font_name,
            font_size_pt=text.font_size_pt,
            text_color=text.text_color,
            alignment=text.alignment,
            vertical_alignment=vertical,
            line_spacing_percent=text.line_spacing,
            fill_color=fill,
            borders=borders,
        ),
        row_height,
        column_width,
    )


def parse_merge(parameters: Mapping[str, OperationInputValue]) -> MergeSpec | InputFailure:
    unknown = _unknown(parameters, frozenset(("start", "end")))
    if unknown is not None:
        return unknown
    start, end = _address(parameters.get("start")), _address(parameters.get("end"))
    if start is None or end is None:
        return InputFailure("needs_input", "병합 시작·끝 셀 주소가 필요합니다", ("inputs.parameters.start", "inputs.parameters.end"))
    start_row, start_column = table_cell_coordinate(start)
    end_row, end_column = table_cell_coordinate(end)
    if end_row < start_row or end_column < start_column or start == end:
        return InputFailure("schema_conflict", "셀 병합 범위의 끝 주소가 시작 주소보다 뒤에 있어야 합니다")
    return MergeSpec(start, end)


def parse_split(parameters: Mapping[str, OperationInputValue]) -> SplitSpec | InputFailure:
    allowed = frozenset(("cell", "columns", "rows", "distribute_height", "merge", "split_mode"))
    unknown = _unknown(parameters, allowed)
    if unknown is not None:
        return unknown
    cell = _address(parameters.get("cell"))
    columns = _integer(parameters.get("columns"), 1, 65_535)
    rows = _integer(parameters.get("rows"), 1, 65_535)
    if cell is None or columns is None or rows is None:
        return InputFailure(
            "needs_input",
            "나눌 셀 주소와 칸·줄 수가 필요합니다",
            ("inputs.parameters.cell", "inputs.parameters.columns", "inputs.parameters.rows"),
        )
    if columns == 1 and rows == 1:
        return InputFailure("schema_conflict", "셀 나누기는 칸 또는 줄 수가 2 이상이어야 합니다")
    flags: list[bool] = []
    for name, default in (("distribute_height", False), ("merge", False)):
        parsed = default if name not in parameters else _boolean(parameters.get(name))
        if parsed is None:
            return InputFailure("schema_conflict", f"inputs.parameters.{name} 값은 boolean이어야 합니다")
        flags.append(parsed)
    split_mode = parameters.get("split_mode", "equal")
    if split_mode not in ("equal", "existing_grid"):
        return InputFailure(
            "schema_conflict",
            "inputs.parameters.split_mode 값은 equal 또는 existing_grid여야 합니다",
        )
    return SplitSpec(cell, columns, rows, flags[0], flags[1], split_mode)

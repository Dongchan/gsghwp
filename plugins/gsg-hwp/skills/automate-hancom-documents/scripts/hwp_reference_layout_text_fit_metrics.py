from __future__ import annotations

from dataclasses import dataclass

from hwp_live_table_contract import CellPadding
from hwp_reference_layout_contract import ReferenceStyle, TextAnchor
from hwp_reference_layout_geometry import mm_to_hwpunit
from hwp_reference_layout_text_flow import (
    minimum_text_height,
    reference_line_height,
    wrapped_line_count,
)


@dataclass(frozen=True, slots=True)
class TextCellFit:
    style: ReferenceStyle
    padding: CellPadding | None
    padding_changed: bool


def scale_padding(padding: CellPadding, percent: int) -> CellPadding:
    scale = percent / 100
    return CellPadding(
        left_mm=round(padding.left_mm * scale, 4),
        right_mm=round(padding.right_mm * scale, 4),
        top_mm=round(padding.top_mm * scale, 4),
        bottom_mm=round(padding.bottom_mm * scale, 4),
    )


def style_with_padding(
    region_style: ReferenceStyle | None,
    padding: CellPadding | None,
) -> ReferenceStyle | None:
    if region_style is None:
        return None
    return region_style.model_copy(update={"padding": padding})


def allowed_line_count(
    anchor: TextAnchor,
    style: ReferenceStyle,
    region_style: ReferenceStyle | None,
    available_width: int,
    available_height: int,
) -> int:
    explicit = (
        anchor.text.replace("\r\n", "\n").replace("\r", "\n").count("\n")
        + 1
    )
    if explicit > 1:
        return explicit
    if (
        len(anchor.text.strip()) <= 12
        and region_style is not None
        and region_style.fill_color is not None
    ):
        return 1
    line_height = reference_line_height(style)
    if line_height <= 0:
        return 1
    padding = region_style.padding if region_style is not None else None
    vertical_padding = 0 if padding is None else (
        mm_to_hwpunit(padding.top_mm) + mm_to_hwpunit(padding.bottom_mm)
    )
    paragraph_spacing = (
        mm_to_hwpunit(style.paragraph_before_mm)
        + mm_to_hwpunit(style.paragraph_after_mm)
    )
    content_height = max(
        1,
        available_height - vertical_padding - paragraph_spacing,
    )
    physical_lines = max(1, content_height // line_height)
    natural_lines = wrapped_lines_in_cell(
        anchor,
        style,
        region_style,
        available_width=available_width,
    )
    allowed = max(1, min(physical_lines, natural_lines))
    if allowed != 2 or style.font_size_pt is None:
        return allowed
    compact_style = style.model_copy(
        update={"font_size_pt": max(1.0, style.font_size_pt * 0.8)},
    )
    compact_lines = wrapped_lines_in_cell(
        anchor,
        compact_style,
        region_style,
        available_width=available_width,
    )
    return 1 if compact_lines == 1 else allowed


def text_fits_cell(
    anchor: TextAnchor,
    style: ReferenceStyle,
    region_style: ReferenceStyle | None,
    *,
    available_width: int,
    available_height: int,
    allowed_lines: int,
) -> bool:
    lines = wrapped_lines_in_cell(
        anchor,
        style,
        region_style,
        available_width=available_width,
    )
    width_budget = max(1, available_width * 95 // 100)
    height_budget = max(
        1,
        available_height - min(100, available_height // 20),
    )
    return (
        lines <= allowed_lines
        and minimum_text_height(
            anchor,
            style,
            region_style,
            width_budget,
        )
        <= height_budget
    )


def wrapped_lines_in_cell(
    anchor: TextAnchor,
    style: ReferenceStyle,
    region_style: ReferenceStyle | None,
    *,
    available_width: int,
) -> int:
    horizontal_padding = (
        0
        if region_style is None or region_style.padding is None
        else (
            mm_to_hwpunit(region_style.padding.left_mm)
            + mm_to_hwpunit(region_style.padding.right_mm)
        )
    )
    width_budget = max(1, available_width * 95 // 100)
    content_width = max(1, width_budget - horizontal_padding)
    return wrapped_line_count(anchor.text, style, content_width)

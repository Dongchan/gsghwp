from __future__ import annotations

from collections.abc import Sequence

from hwp_live_native_text_format import rgb_value
from hwp_reference_layout_contract import ReferenceLayoutBlock, ReferenceStyle
from hwp_reference_layout_geometry import mm_to_hwpunit


def _optional_color(value: tuple[int, int, int] | None) -> int:
    return -1 if value is None else rgb_value(value)


def _optional_padding(style: ReferenceStyle, side: str) -> int:
    if style.padding is None:
        return -1
    values = {
        "left": style.padding.left_mm,
        "right": style.padding.right_mm,
        "top": style.padding.top_mm,
        "bottom": style.padding.bottom_mm,
    }
    return mm_to_hwpunit(values[side])


def _line_spacing(style: ReferenceStyle) -> int:
    if style.line_spacing_type == "percent":
        return style.line_spacing_percent or 100
    assert style.line_spacing_hwpunit is not None
    return style.line_spacing_hwpunit


def style_integer_groups(
    block: ReferenceLayoutBlock,
) -> tuple[tuple[str, Sequence[int]], ...]:
    return (
        (
            "StyleFontSizes",
            tuple(
                -1
                if style.font_size_pt is None
                else round(style.font_size_pt * 100)
                for style in block.styles
            ),
        ),
        (
            "StyleBold",
            tuple(
                -1 if style.bold is None else int(style.bold)
                for style in block.styles
            ),
        ),
        (
            "StyleTextColors",
            tuple(
                _optional_color(style.text_color)
                for style in block.styles
            ),
        ),
        (
            "StyleFillColors",
            tuple(
                _optional_color(style.fill_color)
                for style in block.styles
            ),
        ),
        (
            "StyleAlignments",
            tuple(
                {
                    "inherit": -1,
                    "left": 0,
                    "center": 1,
                    "right": 2,
                    "justify": 3,
                }[style.alignment]
                for style in block.styles
            ),
        ),
        (
            "StyleVerticalAlignments",
            tuple(
                {
                    "inherit": -1,
                    "top": 0,
                    "center": 1,
                    "bottom": 2,
                }[style.vertical_alignment]
                for style in block.styles
            ),
        ),
        (
            "StyleWidthRatios",
            tuple(
                style.width_ratio_percent
                for style in block.styles
            ),
        ),
        (
            "StyleLetterSpacings",
            tuple(
                style.letter_spacing_percent
                for style in block.styles
            ),
        ),
        (
            "StyleLineSpacingTypes",
            tuple(
                {
                    "percent": 0,
                    "fixed": 1,
                    "margin": 2,
                }[style.line_spacing_type]
                for style in block.styles
            ),
        ),
        (
            "StyleLineSpacings",
            tuple(_line_spacing(style) for style in block.styles),
        ),
        (
            "StylePreviousSpacings",
            tuple(
                mm_to_hwpunit(style.paragraph_before_mm)
                for style in block.styles
            ),
        ),
        (
            "StyleNextSpacings",
            tuple(
                mm_to_hwpunit(style.paragraph_after_mm)
                for style in block.styles
            ),
        ),
        (
            "StylePaddingLeft",
            tuple(
                _optional_padding(style, "left")
                for style in block.styles
            ),
        ),
        (
            "StylePaddingRight",
            tuple(
                _optional_padding(style, "right")
                for style in block.styles
            ),
        ),
        (
            "StylePaddingTop",
            tuple(
                _optional_padding(style, "top")
                for style in block.styles
            ),
        ),
        (
            "StylePaddingBottom",
            tuple(
                _optional_padding(style, "bottom")
                for style in block.styles
            ),
        ),
    )

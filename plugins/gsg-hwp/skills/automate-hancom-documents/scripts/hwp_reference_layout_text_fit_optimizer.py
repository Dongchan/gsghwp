from __future__ import annotations

from collections.abc import Callable

from hwp_live_table_contract import CellPadding
from hwp_reference_layout_contract import ReferenceStyle, TextAnchor
from hwp_reference_layout_text_fit_metrics import (
    TextCellFit,
    allowed_line_count,
    scale_padding,
    style_with_padding,
    text_fits_cell,
    wrapped_lines_in_cell,
)
from hwp_reference_layout_text_fit_search import highest_fitting_integer
from hwp_reference_layout_text_flow import (
    ReferenceTextHeightError,
    minimum_text_height,
)


MINIMUM_FONT_SCALE_PERCENT = 75


def _compress_line_spacing(
    style: ReferenceStyle,
    padding: CellPadding | None,
    fits: Callable[[ReferenceStyle, CellPadding | None], bool],
) -> tuple[ReferenceStyle, bool]:
    if style.line_spacing_type == "percent":
        current = style.line_spacing_percent or 100
        minimum = 50
        field = "line_spacing_percent"
    else:
        assert style.line_spacing_hwpunit is not None
        current = style.line_spacing_hwpunit
        minimum = (
            min(current, max(1, round((style.font_size_pt or 1) * 50)))
            if style.line_spacing_type == "fixed"
            else 0
        )
        field = "line_spacing_hwpunit"

    def build(value: int) -> ReferenceStyle:
        return style.model_copy(update={field: value})

    value = highest_fitting_integer(
        minimum,
        current,
        build,
        lambda candidate: fits(candidate, padding),
    )
    return build(minimum if value is None else value), value is not None


def fit_text_to_cell(
    anchor: TextAnchor,
    text_style: ReferenceStyle,
    region_style: ReferenceStyle | None,
    *,
    available_width: int,
    available_height: int,
) -> TextCellFit:
    allowed_lines = allowed_line_count(
        anchor,
        text_style,
        region_style,
        available_width,
        available_height,
    )

    def fits(
        style: ReferenceStyle,
        padding: CellPadding | None,
    ) -> bool:
        return text_fits_cell(
            anchor,
            style,
            style_with_padding(region_style, padding),
            available_width=available_width,
            available_height=available_height,
            allowed_lines=allowed_lines,
        )

    padding = region_style.padding if region_style is not None else None
    if fits(text_style, padding):
        return TextCellFit(text_style, padding, False)

    padding_changed = False
    original_padding = padding
    has_padding = original_padding is not None and any(
        (
            original_padding.left_mm,
            original_padding.right_mm,
            original_padding.top_mm,
            original_padding.bottom_mm,
        )
    )
    if original_padding is not None and has_padding:
        padding_percent = highest_fitting_integer(
            0,
            99,
            lambda value: scale_padding(original_padding, value),
            lambda value: fits(text_style, value),
        )
        if padding_percent is not None:
            fitted_padding = scale_padding(
                original_padding,
                padding_percent,
            )
            return TextCellFit(text_style, fitted_padding, True)
        padding = scale_padding(original_padding, 0)
        padding_changed = True

    style = text_style
    lines_fit = (
        wrapped_lines_in_cell(
            anchor,
            style,
            style_with_padding(region_style, padding),
            available_width=available_width,
        )
        <= allowed_lines
    )
    if lines_fit:
        style, complete = _compress_line_spacing(style, padding, fits)
        if complete:
            return TextCellFit(style, padding, padding_changed)

    current_spacing = style.letter_spacing_percent
    minimum_spacing = min(current_spacing, -5)

    def build_letter_spacing(value: int) -> ReferenceStyle:
        return style.model_copy(
            update={"letter_spacing_percent": value},
        )

    spacing = highest_fitting_integer(
        minimum_spacing,
        current_spacing,
        build_letter_spacing,
        lambda candidate: fits(candidate, padding),
    )
    if spacing is not None:
        return TextCellFit(
            build_letter_spacing(spacing),
            padding,
            padding_changed,
        )
    style = build_letter_spacing(minimum_spacing)

    lines_fit = (
        wrapped_lines_in_cell(
            anchor,
            style,
            style_with_padding(region_style, padding),
            available_width=available_width,
        )
        <= allowed_lines
    )
    if lines_fit:
        style, complete = _compress_line_spacing(style, padding, fits)
        if complete:
            return TextCellFit(style, padding, padding_changed)

    current_width_ratio = style.width_ratio_percent

    def build_width_ratio(value: int) -> ReferenceStyle:
        return style.model_copy(update={"width_ratio_percent": value})

    width_ratio = highest_fitting_integer(
        50,
        current_width_ratio,
        build_width_ratio,
        lambda candidate: fits(candidate, padding),
    )
    if width_ratio is not None:
        return TextCellFit(
            build_width_ratio(width_ratio),
            padding,
            padding_changed,
        )
    style = build_width_ratio(50)

    if style.font_size_pt is None:
        required = minimum_text_height(
            anchor,
            style,
            style_with_padding(region_style, padding),
            available_width,
        )
        raise ReferenceTextHeightError(available_height, required)
    current_size = round(style.font_size_pt * 100)
    minimum_size = max(
        100,
        (
            current_size * MINIMUM_FONT_SCALE_PERCENT
            + MINIMUM_FONT_SCALE_PERCENT
            - 1
        )
        // 100,
    )

    def build_font_size(value: int) -> ReferenceStyle:
        return style.model_copy(update={"font_size_pt": value / 100})

    size = highest_fitting_integer(
        minimum_size,
        current_size,
        build_font_size,
        lambda candidate: fits(candidate, padding),
    )
    if size is not None:
        return TextCellFit(
            build_font_size(size),
            padding,
            padding_changed,
        )
    minimum_style = build_font_size(minimum_size)
    required = minimum_text_height(
        anchor,
        minimum_style,
        style_with_padding(region_style, padding),
        available_width,
    )
    raise ReferenceTextHeightError(available_height, required)

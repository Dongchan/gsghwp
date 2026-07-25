from __future__ import annotations

from hwp_live_table_contract import CellPadding
from hwp_reference_layout_contract import (
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
)
from hwp_reference_layout_text_fit_optimizer import fit_text_to_cell
from hwp_reference_layout_text_flow import style_for_text


_TEXT_STYLE_FIELDS = (
    "font_name",
    "font_size_pt",
    "bold",
    "text_color",
    "alignment",
    "width_ratio_percent",
    "letter_spacing_percent",
    "line_spacing_type",
    "line_spacing_percent",
    "line_spacing_hwpunit",
    "paragraph_before_mm",
    "paragraph_after_mm",
)


def _cell_span(
    block: ReferenceLayoutBlock,
    anchor: TextAnchor,
) -> tuple[int, int]:
    for merge in block.merges:
        if merge.row == anchor.row and merge.column == anchor.column:
            return merge.row_span, merge.column_span
    return 1, 1


def _combined_style(
    text_style: ReferenceStyle,
    region_style: ReferenceStyle | None,
    padding: CellPadding | None,
) -> ReferenceStyle:
    if region_style is None:
        values = text_style.model_dump()
    else:
        values = region_style.model_dump()
        for field in _TEXT_STYLE_FIELDS:
            values[field] = getattr(text_style, field)
        if text_style.vertical_alignment != "inherit":
            values["vertical_alignment"] = text_style.vertical_alignment
    values["padding"] = None if padding is None else padding.model_dump()
    return ReferenceStyle.model_validate(values)


def fit_reference_text_styles(
    block: ReferenceLayoutBlock,
    row_heights: tuple[int, ...],
    column_widths: tuple[int, ...],
) -> ReferenceLayoutBlock:
    styles = list(block.styles)
    regions = list(block.style_regions)
    anchors: list[TextAnchor] = []
    known_keys = {style.key for style in styles}
    derived: dict[str, str] = {}
    next_key = 1

    def materialize(style: ReferenceStyle) -> str:
        nonlocal next_key
        fingerprint = style.model_dump_json(exclude={"key"})
        existing = derived.get(fingerprint)
        if existing is not None:
            return existing
        while f"fit{next_key:03d}" in known_keys:
            next_key += 1
        key = f"fit{next_key:03d}"
        next_key += 1
        candidate = style.model_copy(update={"key": key})
        styles.append(candidate)
        known_keys.add(key)
        derived[fingerprint] = key
        return key

    for anchor in block.text_anchors:
        text_style, region_style = style_for_text(block, anchor)
        if text_style is None or text_style.font_size_pt is None:
            anchors.append(anchor)
            continue
        marker = anchor.text.strip()
        if len(marker) == 1 and not marker.isalnum():
            anchors.append(anchor)
            continue
        row_span, column_span = _cell_span(block, anchor)
        available_width = sum(
            column_widths[anchor.column : anchor.column + column_span]
        )
        available_height = sum(
            row_heights[anchor.row : anchor.row + row_span]
        )
        fitted = fit_text_to_cell(
            anchor,
            text_style,
            region_style,
            available_width=available_width,
            available_height=available_height,
        )
        if fitted.style == text_style and not fitted.padding_changed:
            anchors.append(anchor)
            continue
        combined = _combined_style(
            fitted.style,
            region_style,
            fitted.padding,
        )
        key = materialize(combined)
        anchors.append(anchor.model_copy(update={"style_key": key}))
        if fitted.padding_changed:
            regions.append(
                StyleRegion(
                    top=anchor.row,
                    left=anchor.column,
                    bottom=anchor.row + row_span,
                    right=anchor.column + column_span,
                    style_key=key,
                )
            )

    return ReferenceLayoutBlock.model_validate(
        {
            **block.model_dump(),
            "styles": styles,
            "style_regions": regions,
            "text_anchors": anchors,
        }
    )

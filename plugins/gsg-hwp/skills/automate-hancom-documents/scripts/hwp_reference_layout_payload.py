from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from hwp_live_native_action_models import (
    IntegerValue,
    NativeArray,
    NativeArrayValue,
    TextValue,
)
from hwp_live_native_text_format import rgb_value
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_style_payload import style_integer_groups


@dataclass(frozen=True, slots=True)
class ReferenceLayoutPayload:
    arrays: tuple[NativeArray, ...]
    values: tuple[NativeArrayValue, ...]


def integer_array(
    name: str,
    values: Iterable[int],
) -> tuple[NativeArray, tuple[NativeArrayValue, ...]]:
    materialized = tuple(values)
    return (
        NativeArray(name, len(materialized)),
        tuple(
            NativeArrayValue(name, index, IntegerValue(value))
            for index, value in enumerate(materialized)
        ),
    )


def text_array(
    name: str,
    values: Iterable[str],
) -> tuple[NativeArray, tuple[NativeArrayValue, ...]]:
    materialized = tuple(values)
    return (
        NativeArray(name, len(materialized)),
        tuple(
            NativeArrayValue(name, index, TextValue(value))
            for index, value in enumerate(materialized)
        ),
    )


def _append_array(
    arrays: list[NativeArray],
    values: list[NativeArrayValue],
    pair: tuple[NativeArray, tuple[NativeArrayValue, ...]],
) -> None:
    array, entries = pair
    if array.count == 0:
        return
    arrays.append(array)
    values.extend(entries)


def compile_reference_layout_payload(
    block: ReferenceLayoutBlock,
) -> ReferenceLayoutPayload:
    style_indexes = {
        style.key: index
        for index, style in enumerate(block.styles)
    }
    arrays: list[NativeArray] = []
    values: list[NativeArrayValue] = []
    integer_groups: tuple[tuple[str, Sequence[int]], ...] = (
        ("GapAxes", tuple(0 if gap.axis == "row" else 1 for gap in block.protected_gaps)),
        ("GapTop", tuple(gap.top for gap in block.protected_gaps)),
        ("GapLeft", tuple(gap.left for gap in block.protected_gaps)),
        ("GapBottom", tuple(gap.bottom for gap in block.protected_gaps)),
        ("GapRight", tuple(gap.right for gap in block.protected_gaps)),
        ("MergeRows", tuple(merge.row for merge in block.merges)),
        ("MergeColumns", tuple(merge.column for merge in block.merges)),
        ("MergeRowSpans", tuple(merge.row_span for merge in block.merges)),
        ("MergeColumnSpans", tuple(merge.column_span for merge in block.merges)),
        (
            "EdgeOrientations",
            tuple(
                0 if edge.orientation == "horizontal" else 1
                for edge in block.visible_edges
            ),
        ),
        ("EdgeLines", tuple(edge.line for edge in block.visible_edges)),
        ("EdgeStarts", tuple(edge.start for edge in block.visible_edges)),
        ("EdgeEnds", tuple(edge.end for edge in block.visible_edges)),
        (
            "EdgeColors",
            tuple(rgb_value(edge.color) for edge in block.visible_edges),
        ),
        *style_integer_groups(block),
        ("RegionTop", tuple(region.top for region in block.style_regions)),
        ("RegionLeft", tuple(region.left for region in block.style_regions)),
        ("RegionBottom", tuple(region.bottom for region in block.style_regions)),
        ("RegionRight", tuple(region.right for region in block.style_regions)),
        (
            "RegionStyleIndexes",
            tuple(
                style_indexes[region.style_key]
                for region in block.style_regions
            ),
        ),
        ("TextRows", tuple(anchor.row for anchor in block.text_anchors)),
        (
            "TextColumns",
            tuple(anchor.column for anchor in block.text_anchors),
        ),
        (
            "TextStyleIndexes",
            tuple(
                -1
                if anchor.style_key is None
                else style_indexes[anchor.style_key]
                for anchor in block.text_anchors
            ),
        ),
        (
            "TextBreakModes",
            tuple(
                0 if anchor.break_mode == "line" else 1
                for anchor in block.text_anchors
            ),
        ),
    )
    for name, entries in integer_groups:
        _append_array(arrays, values, integer_array(name, entries))
    for name, entries in (
        ("EdgeStyles", (edge.style for edge in block.visible_edges)),
        ("EdgeWidths", (edge.width for edge in block.visible_edges)),
        ("StyleKeys", (style.key for style in block.styles)),
        (
            "StyleFontNames",
            (style.font_name or "" for style in block.styles),
        ),
        ("TextValues", (anchor.text for anchor in block.text_anchors)),
    ):
        _append_array(arrays, values, text_array(name, entries))
    return ReferenceLayoutPayload(
        arrays=tuple(arrays),
        values=tuple(values),
    )

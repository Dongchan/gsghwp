from __future__ import annotations

from typing import TYPE_CHECKING

from hwp_reference_layout_gap import ProtectedGap
from hwp_reference_layout_gap_validation import (
    cell_in_gap,
    edge_blocked_interval,
    gap_contains_rectangle,
    rectangles_overlap,
)

if TYPE_CHECKING:
    from hwp_reference_layout_contract import (
        ReferenceLayoutBlock,
        StyleRegion,
        VisibleEdge,
    )


def _safe_detected_gap(
    block: ReferenceLayoutBlock,
    gap: ProtectedGap,
) -> bool:
    anchors = {(anchor.row, anchor.column) for anchor in block.text_anchors}
    if any(cell_in_gap(row, column, gap) for row, column in anchors):
        return False
    for merge in block.merges:
        top = merge.row
        left = merge.column
        bottom = top + merge.row_span
        right = left + merge.column_span
        if not rectangles_overlap(top, left, bottom, right, gap):
            continue
        if (
            not gap_contains_rectangle(gap, top, left, bottom, right)
            or (merge.row, merge.column) in anchors
        ):
            return False
    return True


def _subtract_region(
    region: StyleRegion,
    gap: ProtectedGap,
) -> tuple[StyleRegion, ...]:
    if not rectangles_overlap(
        region.top,
        region.left,
        region.bottom,
        region.right,
        gap,
    ):
        return (region,)
    top = max(region.top, gap.top)
    left = max(region.left, gap.left)
    bottom = min(region.bottom, gap.bottom)
    right = min(region.right, gap.right)
    rectangles = (
        (region.top, region.left, top, region.right),
        (bottom, region.left, region.bottom, region.right),
        (top, region.left, bottom, left),
        (top, right, bottom, region.right),
    )
    return tuple(
        region.model_copy(
            update={
                "top": item_top,
                "left": item_left,
                "bottom": item_bottom,
                "right": item_right,
            }
        )
        for item_top, item_left, item_bottom, item_right in rectangles
        if item_top < item_bottom and item_left < item_right
    )


def _subtract_edge(
    edge: VisibleEdge,
    gaps: tuple[ProtectedGap, ...],
) -> tuple[VisibleEdge, ...]:
    intervals = [(edge.start, edge.end)]
    for gap in gaps:
        blocked = edge_blocked_interval(edge, gap)
        if blocked is None:
            continue
        next_intervals: list[tuple[int, int]] = []
        for start, end in intervals:
            if end <= blocked[0] or blocked[1] <= start:
                next_intervals.append((start, end))
                continue
            if start < blocked[0]:
                next_intervals.append((start, blocked[0]))
            if blocked[1] < end:
                next_intervals.append((blocked[1], end))
        intervals = next_intervals
    return tuple(
        edge.model_copy(update={"start": start, "end": end})
        for start, end in intervals
    )


def protect_detected_gaps(
    block: ReferenceLayoutBlock,
    detected: tuple[ProtectedGap, ...],
) -> ReferenceLayoutBlock:
    from hwp_reference_layout_contract import ReferenceLayoutBlock

    accepted = tuple(
        gap
        for gap in detected
        if _safe_detected_gap(block, gap)
    )
    gaps = (*block.protected_gaps, *accepted)
    unique = {
        (gap.axis, gap.top, gap.left, gap.bottom, gap.right): gap
        for gap in gaps
    }
    protected = tuple(unique[key] for key in sorted(unique))
    merges = tuple(
        merge
        for merge in block.merges
        if not any(
            gap_contains_rectangle(
                gap,
                merge.row,
                merge.column,
                merge.row + merge.row_span,
                merge.column + merge.column_span,
            )
            for gap in accepted
        )
    )
    styles = {style.key: style for style in block.styles}
    regions = list(block.style_regions)
    for gap in protected:
        regions = [
            fragment
            for region in regions
            for fragment in (
                _subtract_region(region, gap)
                if styles[region.style_key].fill_color is not None
                else (region,)
            )
        ]
    edges = tuple(
        fragment
        for edge in block.visible_edges
        for fragment in _subtract_edge(edge, protected)
    )
    return ReferenceLayoutBlock.model_validate(
        {
            **block.model_dump(),
            "merges": merges,
            "visible_edges": edges,
            "style_regions": regions,
            "protected_gaps": protected,
        }
    )

from __future__ import annotations

from hwp_live_table_contract import CellPadding
from hwp_reference_layout_contract import (
    ReferenceLayoutBlock,
    ReferenceMerge,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
    VisibleEdge,
)
from hwp_reference_layout_gap import ProtectedGap
from hwp_reference_layout_gap_row_detection import RowGapInsertion
from hwp_reference_layout_gap_rules import protect_detected_gaps


_GAP_STYLE_BASENAME = "protectedGap"


class _RowIndexMap:
    def __init__(self, insertions: tuple[RowGapInsertion, ...]) -> None:
        self._insertions: tuple[RowGapInsertion, ...] = insertions
        self._boundaries: tuple[int, ...] = tuple(
            item.boundary for item in insertions
        )

    def start(self, row: int) -> int:
        return row + sum(boundary <= row for boundary in self._boundaries)

    def end(self, boundary: int) -> int:
        return boundary + sum(
            inserted < boundary for inserted in self._boundaries
        )

    def line(self, boundary: int) -> int:
        return self.end(boundary)

    def insertion(self, boundary: int) -> RowGapInsertion | None:
        return next(
            (
                item
                for item in self._insertions
                if item.boundary == boundary
            ),
            None,
        )


def _insert_breakpoints(
    values: tuple[float, ...],
    insertions: tuple[RowGapInsertion, ...],
) -> tuple[float, ...]:
    by_boundary = {item.boundary: item for item in insertions}
    result: list[float] = []
    for boundary, value in enumerate(values):
        insertion = by_boundary.get(boundary)
        if insertion is None:
            result.append(value)
        else:
            result.extend((insertion.start, insertion.end))
    return tuple(result)


def _remap_merges(
    block: ReferenceLayoutBlock,
    rows: _RowIndexMap,
) -> tuple[ReferenceMerge, ...]:
    return tuple(
        merge.model_copy(
            update={
                "row": rows.start(merge.row),
                "row_span": (
                    rows.end(merge.row + merge.row_span)
                    - rows.start(merge.row)
                ),
            }
        )
        for merge in block.merges
    )


def _remap_regions(
    block: ReferenceLayoutBlock,
    rows: _RowIndexMap,
) -> tuple[StyleRegion, ...]:
    return tuple(
        region.model_copy(
            update={
                "top": rows.start(region.top),
                "bottom": rows.end(region.bottom),
            }
        )
        for region in block.style_regions
    )


def _remap_anchors(
    block: ReferenceLayoutBlock,
    rows: _RowIndexMap,
) -> tuple[TextAnchor, ...]:
    return tuple(
        anchor.model_copy(update={"row": rows.start(anchor.row)})
        for anchor in block.text_anchors
    )


def _remap_edges(
    block: ReferenceLayoutBlock,
    rows: _RowIndexMap,
) -> tuple[VisibleEdge, ...]:
    edges: list[VisibleEdge] = []
    for edge in block.visible_edges:
        if edge.orientation == "vertical":
            edges.append(
                edge.model_copy(
                    update={
                        "start": rows.start(edge.start),
                        "end": rows.end(edge.end),
                    }
                )
            )
            continue
        line = rows.line(edge.line)
        edges.append(edge.model_copy(update={"line": line}))
        insertion = rows.insertion(edge.line)
        if insertion is not None and insertion.duplicate_edge:
            edges.append(edge.model_copy(update={"line": line + 1}))
    unique = {
        (
            edge.orientation,
            edge.line,
            edge.start,
            edge.end,
            edge.style,
            edge.width,
            edge.color,
        ): edge
        for edge in edges
    }
    return tuple(unique[key] for key in sorted(unique))


def _remap_existing_gaps(
    block: ReferenceLayoutBlock,
    rows: _RowIndexMap,
) -> tuple[ProtectedGap, ...]:
    return tuple(
        gap.model_copy(
            update={
                "top": rows.start(gap.top),
                "bottom": rows.end(gap.bottom),
            }
        )
        for gap in block.protected_gaps
    )


def _gap_style(block: ReferenceLayoutBlock) -> ReferenceStyle:
    known = {style.key for style in block.styles}
    key = _GAP_STYLE_BASENAME
    suffix = 1
    while key in known:
        suffix += 1
        key = f"{_GAP_STYLE_BASENAME}{suffix}"
    return ReferenceStyle(
        key=key,
        font_size_pt=1,
        line_spacing_type="margin",
        line_spacing_hwpunit=0,
        padding=CellPadding(
            left_mm=0,
            right_mm=0,
            top_mm=0,
            bottom_mm=0,
        ),
    )


def insert_protected_row_gaps(
    block: ReferenceLayoutBlock,
    row_breakpoints: tuple[float, ...],
    insertions: tuple[RowGapInsertion, ...],
) -> ReferenceLayoutBlock:
    if not insertions:
        return block.model_copy(update={"row_breakpoints": row_breakpoints})
    rows = _RowIndexMap(insertions)
    remapped = ReferenceLayoutBlock.model_validate(
        {
            **block.model_dump(),
            "row_breakpoints": _insert_breakpoints(
                row_breakpoints,
                insertions,
            ),
            "merges": _remap_merges(block, rows),
            "visible_edges": _remap_edges(block, rows),
            "style_regions": _remap_regions(block, rows),
            "text_anchors": _remap_anchors(block, rows),
            "protected_gaps": _remap_existing_gaps(block, rows),
        }
    )
    detected = tuple(
        ProtectedGap(
            axis="row",
            top=rows.end(item.boundary),
            left=left,
            bottom=rows.end(item.boundary) + 1,
            right=right,
        )
        for item in insertions
        for left, right in item.spans
    )
    gap_style = _gap_style(remapped)
    row_gaps = (
        *(gap for gap in remapped.protected_gaps if gap.axis == "row"),
        *detected,
    )
    styled = remapped.model_copy(
        update={
            "styles": (*remapped.styles, gap_style),
            "style_regions": (
                *remapped.style_regions,
                *(
                    StyleRegion(
                        top=gap.top,
                        left=gap.left,
                        bottom=gap.bottom,
                        right=gap.right,
                        style_key=gap_style.key,
                    )
                    for gap in row_gaps
                ),
            ),
        }
    )
    return protect_detected_gaps(styled, detected)

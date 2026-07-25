from __future__ import annotations

from hwp_reference_layout_contract import ReferenceLayoutBlock


def edge_covers(
    block: ReferenceLayoutBlock,
    orientation: str,
    line: int,
    segment: int,
) -> bool:
    return any(
        edge.orientation == orientation
        and edge.line == line
        and edge.start <= segment < edge.end
        for edge in block.visible_edges
    )


def text_blocked_cells(
    block: ReferenceLayoutBlock,
) -> set[tuple[int, int]]:
    anchors = {(anchor.row, anchor.column) for anchor in block.text_anchors}
    blocked = set(anchors)
    for merge in block.merges:
        if (merge.row, merge.column) not in anchors:
            continue
        blocked.update(
            (row, column)
            for row in range(merge.row, merge.row + merge.row_span)
            for column in range(
                merge.column,
                merge.column + merge.column_span,
            )
        )
    return blocked

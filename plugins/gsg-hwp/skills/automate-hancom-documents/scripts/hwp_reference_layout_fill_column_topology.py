from __future__ import annotations

from hwp_reference_layout_contract import (
    ReferenceLayoutBlock,
    ReferenceMerge,
    StyleRegion,
    TextAnchor,
    VisibleEdge,
)
from hwp_reference_layout_fill_snapping import (
    FillColumnExtension,
    FillRegionTarget,
)
from hwp_reference_layout_gap import ProtectedGap


def _target_key(target: FillRegionTarget) -> tuple[int, int, int, int, str]:
    return (
        target.top,
        target.left,
        target.bottom,
        target.right,
        target.style_key,
    )


def _region_key(region: StyleRegion) -> tuple[int, int, int, int, str]:
    return (
        region.top,
        region.left,
        region.bottom,
        region.right,
        region.style_key,
    )


def _target_merge_key(
    target: FillRegionTarget,
) -> tuple[int, int, int, int]:
    return (
        target.top,
        target.left,
        target.bottom - target.top,
        target.right - target.left,
    )


def _merge_key(merge: ReferenceMerge) -> tuple[int, int, int, int]:
    return (
        merge.row,
        merge.column,
        merge.row_span,
        merge.column_span,
    )


def _remap_merges(
    block: ReferenceLayoutBlock,
    extension: FillColumnExtension,
) -> tuple[ReferenceMerge, ...]:
    split = extension.column
    targets = {_target_merge_key(target) for target in extension.targets}
    remapped: list[ReferenceMerge] = []
    covered_rows: set[int] = set()
    target_rows = {
        row
        for target in extension.targets
        for row in range(target.top, target.bottom)
    }
    for merge in block.merges:
        key = _merge_key(merge)
        if key in targets:
            remapped.append(
                merge.model_copy(
                    update={"column_span": merge.column_span + 1}
                )
            )
            covered_rows.update(
                range(merge.row, merge.row + merge.row_span)
            )
        elif merge.column <= split < merge.column + merge.column_span:
            remapped.append(
                merge.model_copy(
                    update={"column_span": merge.column_span + 1}
                )
            )
            covered_rows.update(
                range(merge.row, merge.row + merge.row_span)
            )
        elif merge.column > split:
            remapped.append(
                merge.model_copy(update={"column": merge.column + 1})
            )
        else:
            remapped.append(merge)
    covered_rows.update(target_rows)
    for row in range(len(block.row_breakpoints) - 1):
        if row not in covered_rows:
            remapped.append(
                ReferenceMerge(
                    row=row,
                    column=split,
                    row_span=1,
                    column_span=2,
                )
            )
    if len(remapped) > 200:
        raise ValueError(
            "filled-region breakpoint requires too many preservation merges"
        )
    return tuple(remapped)


def _remap_regions(
    block: ReferenceLayoutBlock,
    extension: FillColumnExtension,
) -> tuple[StyleRegion, ...]:
    split = extension.column
    targets = {_target_key(target) for target in extension.targets}
    result: list[StyleRegion] = []
    for region in block.style_regions:
        if _region_key(region) in targets:
            update = {"right": region.right + 1}
        elif region.left <= split < region.right:
            update = {"right": region.right + 1}
        elif region.left > split:
            update = {
                "left": region.left + 1,
                "right": region.right + 1,
            }
        else:
            update = {}
        result.append(region.model_copy(update=update))
    return tuple(result)


def _remap_anchors(
    block: ReferenceLayoutBlock,
    split: int,
) -> tuple[TextAnchor, ...]:
    return tuple(
        anchor.model_copy(
            update={
                "column": (
                    anchor.column + 1
                    if anchor.column > split
                    else anchor.column
                )
            }
        )
        for anchor in block.text_anchors
    )


def _remap_edges(
    block: ReferenceLayoutBlock,
    extension: FillColumnExtension,
) -> tuple[VisibleEdge, ...]:
    split = extension.column
    target_rows = {
        row
        for target in extension.targets
        for row in range(target.top, target.bottom)
    }
    result: list[VisibleEdge] = []
    for edge in block.visible_edges:
        if edge.orientation == "vertical":
            if edge.line == split and target_rows:
                start = edge.start
                line = split + (1 if start in target_rows else 0)
                for row in range(edge.start + 1, edge.end):
                    next_line = split + (1 if row in target_rows else 0)
                    if next_line == line:
                        continue
                    result.append(
                        edge.model_copy(
                            update={"line": line, "start": start, "end": row}
                        )
                    )
                    start = row
                    line = next_line
                result.append(
                    edge.model_copy(
                        update={"line": line, "start": start}
                    )
                )
                continue
            update = {
                "line": edge.line + 1 if edge.line > split else edge.line
            }
        else:
            update = {
                "start": edge.start + 1 if edge.start > split else edge.start,
                "end": edge.end + 1 if edge.end > split else edge.end,
            }
        result.append(edge.model_copy(update=update))
    return tuple(result)


def _remap_gaps(
    block: ReferenceLayoutBlock,
    split: int,
) -> tuple[ProtectedGap, ...]:
    result: list[ProtectedGap] = []
    for gap in block.protected_gaps:
        if gap.left <= split < gap.right:
            update = {"right": gap.right + 1}
        elif gap.left > split:
            update = {"left": gap.left + 1, "right": gap.right + 1}
        else:
            update = {}
        result.append(gap.model_copy(update=update))
    return tuple(result)


def _insert_one(
    block: ReferenceLayoutBlock,
    extension: FillColumnExtension,
) -> ReferenceLayoutBlock:
    split = extension.column
    if not (
        block.column_breakpoints[split]
        < extension.position
        < block.column_breakpoints[split + 1]
    ):
        return block
    breakpoints = list(block.column_breakpoints)
    breakpoints.insert(split + 1, extension.position)
    return ReferenceLayoutBlock.model_validate(
        {
            **block.model_dump(),
            "column_breakpoints": breakpoints,
            "merges": _remap_merges(block, extension),
            "visible_edges": _remap_edges(block, extension),
            "style_regions": _remap_regions(block, extension),
            "text_anchors": _remap_anchors(block, split),
            "protected_gaps": _remap_gaps(block, split),
        }
    )


def extend_filled_regions_with_columns(
    block: ReferenceLayoutBlock,
    extensions: tuple[FillColumnExtension, ...],
) -> ReferenceLayoutBlock:
    extended = block
    for extension in extensions:
        extended = _insert_one(extended, extension)
    return extended

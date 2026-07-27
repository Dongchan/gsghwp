from __future__ import annotations

from typing import Protocol

from hwp_reference_layout_contract import (
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    VisibleEdge,
)
from hwp_reference_layout_patch import ReferenceLayoutPatchBlock


class ReferenceRenderDifferenceLike(Protocol):
    row_breakpoints: tuple[float, ...]
    column_breakpoints: tuple[float, ...]
    changed_row_boundaries: tuple[int, ...]
    changed_column_boundaries: tuple[int, ...]


def build_reference_layout_patch(
    block: ReferenceLayoutBlock,
    difference: ReferenceRenderDifferenceLike,
    *,
    target_control_id: str,
    styles: tuple[ReferenceStyle, ...] = (),
    style_regions: tuple[StyleRegion, ...] = (),
    edges: tuple[VisibleEdge, ...] = (),
) -> ReferenceLayoutPatchBlock:
    changed_rows = {
        index
        for boundary in difference.changed_row_boundaries
        for index in (boundary - 1, boundary)
    }
    changed_columns = {
        index
        for boundary in difference.changed_column_boundaries
        for index in (boundary - 1, boundary)
    }
    return ReferenceLayoutPatchBlock(
        kind="reference_layout_patch",
        target_control_id=target_control_id,
        source_image=block.source_image,
        row_breakpoints=difference.row_breakpoints,
        column_breakpoints=difference.column_breakpoints,
        changed_rows=tuple(sorted(changed_rows)),
        changed_columns=tuple(sorted(changed_columns)),
        merges=block.merges,
        styles=styles,
        style_regions=style_regions,
        edges=edges,
        placement_edges=block.visible_edges,
        placement_styles=block.styles,
        placement_style_regions=block.style_regions,
        text_styles=block.styles if block.text_anchors else (),
        text_anchors=block.text_anchors,
    )

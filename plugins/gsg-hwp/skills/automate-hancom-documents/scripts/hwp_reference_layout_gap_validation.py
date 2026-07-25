from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from hwp_reference_layout_gap import ProtectedGap


class MergeLike(Protocol):
    row: int
    column: int
    row_span: int
    column_span: int


class AnchorLike(Protocol):
    row: int
    column: int


class StyleLike(Protocol):
    key: str
    fill_color: tuple[int, int, int] | None


class StyleRegionLike(Protocol):
    top: int
    left: int
    bottom: int
    right: int
    style_key: str


class VisibleEdgeLike(Protocol):
    @property
    def orientation(self) -> Literal["horizontal", "vertical"]: ...

    @property
    def line(self) -> int: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...


@runtime_checkable
class GapLayoutLike(Protocol):
    protected_gaps: Sequence[ProtectedGap]
    merges: Sequence[MergeLike]
    text_anchors: Sequence[AnchorLike]
    styles: Sequence[StyleLike]
    style_regions: Sequence[StyleRegionLike]
    visible_edges: Sequence[VisibleEdgeLike]


def rectangles_overlap(
    top: int,
    left: int,
    bottom: int,
    right: int,
    gap: ProtectedGap,
) -> bool:
    return (
        top < gap.bottom
        and gap.top < bottom
        and left < gap.right
        and gap.left < right
    )


def gap_contains_rectangle(
    gap: ProtectedGap,
    top: int,
    left: int,
    bottom: int,
    right: int,
) -> bool:
    return (
        gap.top <= top
        and gap.left <= left
        and gap.bottom >= bottom
        and gap.right >= right
    )


def cell_in_gap(row: int, column: int, gap: ProtectedGap) -> bool:
    return (
        gap.top <= row < gap.bottom
        and gap.left <= column < gap.right
    )


def edge_blocked_interval(
    edge: VisibleEdgeLike,
    gap: ProtectedGap,
) -> tuple[int, int] | None:
    if edge.orientation == "horizontal":
        if gap.top < edge.line < gap.bottom:
            return gap.left, gap.right
        return None
    if gap.left < edge.line < gap.right:
        return gap.top, gap.bottom
    return None


def validate_protected_gaps(
    block: object,
    rows: int,
    columns: int,
) -> None:
    layout = block
    if not isinstance(layout, GapLayoutLike):
        raise TypeError("protected gap validation requires a reference layout")
    keys = [
        (gap.axis, gap.top, gap.left, gap.bottom, gap.right)
        for gap in layout.protected_gaps
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("protected gap rectangles must be unique")
    styles = {style.key: style for style in layout.styles}
    for gap in layout.protected_gaps:
        if gap.bottom > rows or gap.right > columns:
            raise ValueError(
                "protected gap is outside reference layout bounds"
            )
        for merge in layout.merges:
            if rectangles_overlap(
                merge.row,
                merge.column,
                merge.row + merge.row_span,
                merge.column + merge.column_span,
                gap,
            ):
                raise ValueError("merge intersects a protected gap")
        for anchor in layout.text_anchors:
            if cell_in_gap(anchor.row, anchor.column, gap):
                raise ValueError("text anchor intersects a protected gap")
        for region in layout.style_regions:
            if (
                styles[region.style_key].fill_color is not None
                and rectangles_overlap(
                    region.top,
                    region.left,
                    region.bottom,
                    region.right,
                    gap,
                )
            ):
                raise ValueError("fill style intersects a protected gap")
        for edge in layout.visible_edges:
            blocked = edge_blocked_interval(edge, gap)
            if (
                blocked is not None
                and edge.start < blocked[1]
                and blocked[0] < edge.end
            ):
                raise ValueError("visible edge crosses a protected gap")

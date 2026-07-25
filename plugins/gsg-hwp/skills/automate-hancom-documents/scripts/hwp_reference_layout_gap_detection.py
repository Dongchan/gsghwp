from __future__ import annotations

from PIL import Image

from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_gap import (
    ProtectedGap,
    coalesce_protected_gaps,
)
from hwp_reference_layout_gap_pixels import (
    GapPixelAnalysis,
    analyze_gap_pixels,
    quantile,
)
from hwp_reference_layout_gap_topology import (
    edge_covers,
    text_blocked_cells,
)

MINIMUM_SOURCE_GAP_PIXELS = 2.0


def _is_source_sized_gap(
    gap: ProtectedGap,
    source_image: Image.Image,
    row_breakpoints: tuple[float, ...],
    column_breakpoints: tuple[float, ...],
) -> bool:
    if gap.axis == "column":
        extent = (
            column_breakpoints[gap.right]
            - column_breakpoints[gap.left]
        ) * source_image.width
    else:
        extent = (
            row_breakpoints[gap.bottom]
            - row_breakpoints[gap.top]
        ) * source_image.height
    return extent >= MINIMUM_SOURCE_GAP_PIXELS


def _full_axis_gaps(
    block: ReferenceLayoutBlock,
    row_count: int,
    column_count: int,
    blocked: set[tuple[int, int]],
    analysis: GapPixelAnalysis,
) -> tuple[list[ProtectedGap], set[int], set[int]]:
    gaps: list[ProtectedGap] = []
    full_rows: set[int] = set()
    for row in range(1, row_count - 1):
        item = analysis.rows[row]
        if (
            not any(blocked_row == row for blocked_row, _ in blocked)
            and item.activity <= analysis.row_activity_cutoff
            and item.background_distance <= analysis.row_color_cutoff
            and not any(
                edge.orientation == "vertical"
                and 0 < edge.line < column_count
                and edge.start <= row < edge.end
                for edge in block.visible_edges
            )
        ):
            gaps.append(
                ProtectedGap(
                    axis="row",
                    top=row,
                    left=0,
                    bottom=row + 1,
                    right=column_count,
                )
            )
            full_rows.add(row)
    full_columns: set[int] = set()
    for column in range(1, column_count - 1):
        item = analysis.columns[column]
        if (
            not any(
                blocked_column == column
                for _, blocked_column in blocked
            )
            and item.activity <= analysis.column_activity_cutoff
            and item.background_distance <= analysis.column_color_cutoff
            and not any(
                edge.orientation == "horizontal"
                and 0 < edge.line < row_count
                and edge.start <= column < edge.end
                for edge in block.visible_edges
            )
        ):
            gaps.append(
                ProtectedGap(
                    axis="column",
                    top=0,
                    left=column,
                    bottom=row_count,
                    right=column + 1,
                )
            )
            full_columns.add(column)
    return gaps, full_rows, full_columns


def detect_protected_gaps(
    source_image: Image.Image,
    block: ReferenceLayoutBlock,
    row_breakpoints: tuple[float, ...],
    column_breakpoints: tuple[float, ...],
) -> tuple[ProtectedGap, ...]:
    analysis = analyze_gap_pixels(
        source_image,
        row_breakpoints,
        column_breakpoints,
    )
    row_count = len(row_breakpoints) - 1
    column_count = len(column_breakpoints) - 1
    blocked = text_blocked_cells(block)
    gaps, full_rows, full_columns = _full_axis_gaps(
        block,
        row_count,
        column_count,
        blocked,
        analysis,
    )
    narrow_row = quantile(list(analysis.row_sizes), 0.4)
    narrow_column = quantile(list(analysis.column_sizes), 0.4)
    for row in range(row_count):
        for column in range(column_count):
            if (
                (row, column) in blocked
                or row in full_rows
                or column in full_columns
            ):
                continue
            item = analysis.cells[row, column]
            vertical_pair = edge_covers(
                block,
                "vertical",
                column,
                row,
            ) and edge_covers(
                block,
                "vertical",
                column + 1,
                row,
            )
            horizontal_pair = edge_covers(
                block,
                "horizontal",
                row,
                column,
            ) and edge_covers(
                block,
                "horizontal",
                row + 1,
                column,
            )
            strict_blank = (
                item.activity <= analysis.cell_activity_cutoff
                and item.background_distance <= analysis.cell_color_cutoff
            )
            boundary_blank = (
                item.background_distance
                <= max(0.08, analysis.cell_color_cutoff)
            )
            if (
                (strict_blank or (vertical_pair and boundary_blank))
                and analysis.column_sizes[column] <= narrow_column
                and vertical_pair
            ):
                gaps.append(
                    ProtectedGap(
                        axis="column",
                        top=row,
                        left=column,
                        bottom=row + 1,
                        right=column + 1,
                    )
                )
            if (
                (strict_blank or (horizontal_pair and boundary_blank))
                and analysis.row_sizes[row] <= narrow_row
                and horizontal_pair
            ):
                gaps.append(
                    ProtectedGap(
                        axis="row",
                        top=row,
                        left=column,
                        bottom=row + 1,
                        right=column + 1,
                    )
                )
    return coalesce_protected_gaps([
        gap
        for gap in gaps
        if _is_source_sized_gap(
            gap,
            source_image,
            row_breakpoints,
            column_breakpoints,
        )
    ])

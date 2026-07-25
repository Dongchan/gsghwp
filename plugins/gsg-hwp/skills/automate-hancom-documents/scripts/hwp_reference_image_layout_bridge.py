from __future__ import annotations

from hwp_reference_image_contract import (
    AnalysisAxis,
    ReferenceBreakpointCandidate,
    ReferenceImageAnalysis,
    ReferenceProtectedGap,
)
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_gap import ProtectedGap
from hwp_reference_layout_gap_rules import protect_detected_gaps
from hwp_reference_layout_gap_snapping import (
    preserve_occupied_narrow_columns,
    preserve_occupied_narrow_rows,
)


_SOURCE_PRIORITY = {
    "gap": 0,
    "segment": 1,
    "object": 2,
    "frame": 3,
    "text": 4,
}


def _axis_candidates(
    analysis: ReferenceImageAnalysis,
    axis: AnalysisAxis,
) -> tuple[ReferenceBreakpointCandidate, ...]:
    return tuple(
        candidate
        for candidate in analysis.breakpoint_candidates
        if candidate.axis == axis and candidate.source != "text"
    )


def _snap_breakpoints(
    values: tuple[float, ...],
    candidates: tuple[ReferenceBreakpointCandidate, ...],
) -> tuple[float, ...]:
    snapped = [0.0]
    for index, value in enumerate(values[1:-1], start=1):
        lower = values[index - 1]
        upper = values[index + 1]
        capture = min(value - lower, upper - value) * 0.45
        choices = tuple(
            candidate
            for candidate in candidates
            if snapped[-1] < candidate.position < upper
            and abs(candidate.position - value) <= capture
        )
        selected = min(
            choices,
            key=lambda candidate: (
                _SOURCE_PRIORITY[candidate.source],
                abs(candidate.position - value),
                candidate.position,
            ),
            default=None,
        )
        snapped.append(value if selected is None else selected.position)
    snapped.append(1.0)
    if any(left >= right for left, right in zip(snapped, snapped[1:])):
        return values
    return tuple(snapped)


def _boundary_pair(
    values: tuple[float, ...],
    start: float,
    end: float,
) -> tuple[int, int] | None:
    candidates = tuple(
        (left, right)
        for left in range(len(values) - 1)
        for right in range(left + 1, len(values))
    )
    left, right = min(
        candidates,
        key=lambda pair: (
            abs(values[pair[0]] - start) + abs(values[pair[1]] - end),
            pair[1] - pair[0],
        ),
    )
    tolerance = max(end - start, values[right] - values[left]) * 0.35
    if (
        abs(values[left] - start) > tolerance
        or abs(values[right] - end) > tolerance
    ):
        return None
    return left, right


def _covered_cells(
    values: tuple[float, ...],
    start: float,
    end: float,
) -> tuple[int, int] | None:
    covered = tuple(
        index
        for index, (left, right) in enumerate(zip(values, values[1:]))
        if start <= (left + right) / 2 <= end
    )
    if not covered:
        return None
    return covered[0], covered[-1] + 1


def _topology_gap(
    gap: ReferenceProtectedGap,
    rows: tuple[float, ...],
    columns: tuple[float, ...],
) -> ProtectedGap | None:
    if gap.orientation == "vertical":
        horizontal = _boundary_pair(columns, gap.bbox.left, gap.bbox.right)
        vertical = _covered_cells(rows, gap.bbox.top, gap.bbox.bottom)
        if horizontal is None or vertical is None:
            return None
        return ProtectedGap(
            axis="column",
            top=vertical[0],
            left=horizontal[0],
            bottom=vertical[1],
            right=horizontal[1],
        )
    vertical = _boundary_pair(rows, gap.bbox.top, gap.bbox.bottom)
    horizontal = _covered_cells(columns, gap.bbox.left, gap.bbox.right)
    if vertical is None or horizontal is None:
        return None
    return ProtectedGap(
        axis="row",
        top=vertical[0],
        left=horizontal[0],
        bottom=vertical[1],
        right=horizontal[1],
    )


def align_reference_layout_to_analysis(
    block: ReferenceLayoutBlock,
    analysis: ReferenceImageAnalysis,
) -> ReferenceLayoutBlock:
    if block.analysis_id != analysis.analysis_id:
        raise ValueError(
            "reference layout analysis_id does not match the source analysis"
        )
    rows = preserve_occupied_narrow_rows(
        block,
        _snap_breakpoints(
            block.row_breakpoints,
            _axis_candidates(analysis, "row"),
        ),
    )
    columns = preserve_occupied_narrow_columns(
        block,
        _snap_breakpoints(
            block.column_breakpoints,
            _axis_candidates(analysis, "column"),
        ),
    )
    aligned = block.model_copy(
        update={
            "row_breakpoints": rows,
            "column_breakpoints": columns,
        }
    )
    gaps = tuple(
        topology
        for gap in analysis.protected_gaps
        if (topology := _topology_gap(gap, rows, columns)) is not None
    )
    return protect_detected_gaps(aligned, gaps)

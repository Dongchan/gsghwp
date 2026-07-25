from __future__ import annotations

from hwp_reference_image_pixels import PixelSegment


_MINIMUM_SUPPORTED_FRACTION = 0.70


def _overlap_fraction(
    start: int,
    end: int,
    intervals: list[tuple[int, int]],
) -> float:
    return max(
        (
            max(0, min(end, right) - max(start, left))
            / max(1, end - start)
            for left, right in intervals
        ),
        default=0.0,
    )


def _endpoint_supports(
    candidate: PixelSegment,
    baseline: tuple[PixelSegment, ...],
    tolerance: int,
) -> int:
    perpendicular = (
        "vertical" if candidate.orientation == "horizontal" else "horizontal"
    )
    supports = 0
    for endpoint in (candidate.start, candidate.end):
        if any(
            item.orientation == perpendicular
            and abs(item.position - endpoint) <= tolerance
            and item.start - tolerance <= candidate.position <= item.end + tolerance
            for item in baseline
        ):
            supports += 1
    return supports


def has_structural_support(
    candidate: PixelSegment,
    baseline: tuple[PixelSegment, ...],
    tolerance: int,
) -> bool:
    aligned = [
        item
        for item in baseline
        if item.orientation == candidate.orientation
        and abs(item.position - candidate.position) <= tolerance
    ]
    if _endpoint_supports(candidate, baseline, tolerance) == 2:
        return True
    candidate_length = candidate.end - candidate.start
    return any(
        _overlap_fraction(
            candidate.start,
            candidate.end,
            [(item.start, item.end)],
        )
        >= _MINIMUM_SUPPORTED_FRACTION
        and candidate_length >= item.end - item.start + 4
        for item in aligned
    )


def coalesce_candidates(
    candidates: list[tuple[PixelSegment, int]],
) -> list[tuple[PixelSegment, int]]:
    retained: list[tuple[PixelSegment, int]] = []
    for candidate, drift in sorted(
        candidates,
        key=lambda item: (
            item[0].orientation,
            item[0].position,
            item[0].start,
            item[0].end,
        ),
    ):
        if retained:
            previous, previous_drift = retained[-1]
            if (
                previous.orientation == candidate.orientation
                and abs(previous.position - candidate.position) <= 4
                and candidate.start <= previous.end + 4
            ):
                retained[-1] = (
                    PixelSegment(
                        orientation=previous.orientation,
                        start=min(previous.start, candidate.start),
                        end=max(previous.end, candidate.end),
                        position=round(
                            (previous.position + candidate.position) / 2
                        ),
                        width=max(previous.width, candidate.width),
                        color=previous.color,
                    ),
                    max(previous_drift, drift),
                )
                continue
        retained.append((candidate, drift))
    return retained

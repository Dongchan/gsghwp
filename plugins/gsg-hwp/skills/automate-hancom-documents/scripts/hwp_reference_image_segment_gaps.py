from __future__ import annotations

from hwp_reference_image_pixels import PixelGap, PixelSegment


def _crosses_gap(segment: PixelSegment, gap: PixelGap) -> bool:
    if gap.orientation == "vertical":
        return (
            segment.orientation == "horizontal"
            and gap.bbox.top < segment.position < gap.bbox.bottom
            and segment.start < gap.bbox.left
            and segment.end > gap.bbox.right
        )
    return (
        segment.orientation == "vertical"
        and gap.bbox.left < segment.position < gap.bbox.right
        and segment.start < gap.bbox.top
        and segment.end > gap.bbox.bottom
    )


def exclude_gap_crossing_segments(
    segments: tuple[PixelSegment, ...],
    gaps: tuple[PixelGap, ...],
) -> tuple[PixelSegment, ...]:
    return tuple(
        segment
        for segment in segments
        if not any(_crosses_gap(segment, gap) for gap in gaps)
    )

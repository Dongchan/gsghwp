from __future__ import annotations

from hwp_reference_image_pixels import PixelCanvas, PixelSegment


def _same_line(left: PixelSegment, right: PixelSegment) -> bool:
    return (
        left.orientation == right.orientation
        and abs(left.position - right.position) <= 2
        and abs(left.start - right.start) <= 3
        and abs(left.end - right.end) <= 3
    )


def _coalesce(segments: list[PixelSegment]) -> tuple[PixelSegment, ...]:
    ordered = sorted(
        segments,
        key=lambda item: (item.orientation, item.start, item.end, item.position),
    )
    retained: list[PixelSegment] = []
    for segment in ordered:
        if not retained or not _same_line(retained[-1], segment):
            retained.append(segment)
            continue
        previous = retained[-1]
        retained[-1] = PixelSegment(
            orientation=previous.orientation,
            start=min(previous.start, segment.start),
            end=max(previous.end, segment.end),
            position=round((previous.position + segment.position) / 2),
            width=max(previous.width, segment.width),
            color=previous.color,
        )
    return tuple(retained)


def _horizontal_supports(vertical: PixelSegment, horizontal: PixelSegment) -> bool:
    if horizontal.orientation != "horizontal":
        return False
    endpoint_aligned = (
        abs(horizontal.start - vertical.position) <= 3
        or abs(horizontal.end - vertical.position) <= 3
    )
    return endpoint_aligned and (
        abs(horizontal.position - vertical.start) <= 3
        or abs(horizontal.position - vertical.end) <= 3
    )


def _vertical_supports(horizontal: PixelSegment, vertical: PixelSegment) -> bool:
    if vertical.orientation != "vertical":
        return False
    endpoint_aligned = (
        abs(vertical.start - horizontal.position) <= 3
        or abs(vertical.end - horizontal.position) <= 3
    )
    return endpoint_aligned and (
        abs(vertical.position - horizontal.start) <= 3
        or abs(vertical.position - horizontal.end) <= 3
    )


def filter_layout_segments(
    canvas: PixelCanvas,
    segments: list[PixelSegment],
) -> tuple[PixelSegment, ...]:
    candidates = _coalesce(segments)
    horizontal = tuple(item for item in candidates if item.orientation == "horizontal")
    vertical = tuple(item for item in candidates if item.orientation == "vertical")
    retained_horizontal = [
        item
        for item in horizontal
        if item.end - item.start >= max(30, canvas.width // 14)
    ]
    retained_vertical = [
        item
        for item in vertical
        if item.end - item.start >= max(24, canvas.height // 40)
    ]
    for item in vertical:
        if item in retained_vertical or item.end - item.start < max(
            12, canvas.height // 100
        ):
            continue
        supports = sum(
            _horizontal_supports(item, candidate) for candidate in retained_horizontal
        )
        if supports >= 2:
            retained_vertical.append(item)
    for item in horizontal:
        if item in retained_horizontal or item.end - item.start < max(
            20, canvas.width // 50
        ):
            continue
        supports = sum(
            _vertical_supports(item, candidate) for candidate in retained_vertical
        )
        if supports >= 2:
            retained_horizontal.append(item)
    return _coalesce(retained_horizontal + retained_vertical)

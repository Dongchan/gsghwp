from __future__ import annotations

from hwp_reference_image_pixels import PixelBox, PixelCanvas, PixelSegment
from hwp_reference_image_text_mask import PixelBarrier, build_text_mask


def _active_row_bands(canvas: PixelCanvas, mask: bytes) -> tuple[PixelBox, ...]:
    minimum_pixels = max(2, canvas.width // 500)
    active_rows: list[int] = []
    for y_position in range(canvas.height):
        start = y_position * canvas.width
        end = start + canvas.width
        if mask[start:end].count(255) >= minimum_pixels:
            active_rows.append(y_position)
    bands: list[PixelBox] = []
    start: int | None = None
    previous = -1
    for y_position in (*active_rows, canvas.height + 3):
        if start is None:
            start = y_position
            previous = y_position
            continue
        if y_position - previous <= 2:
            previous = y_position
            continue
        bands.append(PixelBox(0, start, canvas.width, previous + 1))
        start = y_position if y_position < canvas.height else None
        previous = y_position
    return tuple(bands)


def _crosses_barrier(
    previous: int,
    current: int,
    band: PixelBox,
    barriers: tuple[PixelBarrier, ...],
) -> bool:
    return any(
        previous < item.column < current
        and item.top < band.bottom
        and item.bottom > band.top
        for item in barriers
    )


def _column_groups(
    canvas: PixelCanvas,
    mask: bytes,
    band: PixelBox,
    barriers: tuple[PixelBarrier, ...],
) -> tuple[PixelBox, ...]:
    active_columns: list[int] = []
    for x_position in range(canvas.width):
        if any(
            mask[y_position * canvas.width + x_position] == 255
            for y_position in range(band.top, band.bottom)
        ):
            active_columns.append(x_position)
    maximum_gap = max(4, min(36, band.height * 2))
    groups: list[PixelBox] = []
    start: int | None = None
    previous = -1
    for x_position in (*active_columns, canvas.width + maximum_gap + 1):
        if start is None:
            start = x_position
            previous = x_position
            continue
        if (
            x_position - previous <= maximum_gap
            and not _crosses_barrier(previous, x_position, band, barriers)
        ):
            previous = x_position
            continue
        groups.append(PixelBox(start, band.top, previous + 1, band.bottom))
        start = x_position if x_position < canvas.width else None
        previous = x_position
    return tuple(groups)


def _is_line_or_edge(bbox: PixelBox) -> bool:
    return (
        bbox.height <= 4
        and bbox.width >= bbox.height * 8
        or bbox.width <= 4
        and bbox.height >= bbox.width * 8
    )


def _padded(canvas: PixelCanvas, bbox: PixelBox) -> PixelBox:
    x_padding = max(2, bbox.height // 3)
    y_padding = max(2, bbox.height // 5)
    return PixelBox(
        max(0, bbox.left - x_padding),
        max(0, bbox.top - y_padding),
        min(canvas.width, bbox.right + x_padding),
        min(canvas.height, bbox.bottom + y_padding),
    )


def detect_text_regions(
    canvas: PixelCanvas,
    segments: tuple[PixelSegment, ...],
) -> tuple[PixelBox, ...]:
    mask, barriers = build_text_mask(canvas, segments)
    regions: list[PixelBox] = []
    for band in _active_row_bands(canvas, mask):
        if band.height > max(90, canvas.height // 10):
            continue
        for bbox in _column_groups(canvas, mask, band, barriers):
            if bbox.width < 3 or bbox.height < 3 or _is_line_or_edge(bbox):
                continue
            padded = _padded(canvas, bbox)
            if canvas.contrast_ratio(padded) >= 0.006:
                regions.append(padded)
    return tuple(regions)

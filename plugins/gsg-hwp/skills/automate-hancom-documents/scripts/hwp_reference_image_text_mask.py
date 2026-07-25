from __future__ import annotations

from dataclasses import dataclass

from hwp_reference_image_pixels import PixelCanvas, PixelSegment


@dataclass(frozen=True, slots=True)
class PixelBarrier:
    column: int
    top: int
    bottom: int


def _erase_segments(
    canvas: PixelCanvas,
    mask: bytearray,
    segments: tuple[PixelSegment, ...],
) -> None:
    for item in segments:
        padding = item.width + 2
        if item.orientation == "horizontal":
            for y_position in range(
                max(0, item.position - padding),
                min(canvas.height, item.position + padding + 1),
            ):
                start = y_position * canvas.width + item.start
                end = y_position * canvas.width + item.end
                mask[start:end] = b"\0" * (end - start)
        else:
            for y_position in range(
                max(0, item.start),
                min(canvas.height, item.end),
            ):
                for x_position in range(
                    max(0, item.position - padding),
                    min(canvas.width, item.position + padding + 1),
                ):
                    mask[y_position * canvas.width + x_position] = 0


def _runs(values: list[int], maximum_gap: int) -> tuple[tuple[int, int, int], ...]:
    if not values:
        return ()
    result: list[tuple[int, int, int]] = []
    start = values[0]
    previous = values[0]
    count = 1
    for value in values[1:]:
        if value - previous <= maximum_gap:
            previous = value
            count += 1
            continue
        result.append((start, previous + 1, count))
        start = value
        previous = value
        count = 1
    result.append((start, previous + 1, count))
    return tuple(result)


def _vertical_barriers(
    canvas: PixelCanvas,
    source: bytes,
) -> tuple[PixelBarrier, ...]:
    minimum_span = max(30, canvas.height // 12)
    barriers: list[PixelBarrier] = []
    for x_position in range(canvas.width):
        active = [
            y_position
            for y_position in range(canvas.height)
            if source[y_position * canvas.width + x_position] == 255
        ]
        for top, bottom, count in _runs(active, 4):
            span = bottom - top
            if span >= minimum_span and count >= span * 0.2:
                barriers.append(PixelBarrier(x_position, top, bottom))
    return tuple(barriers)


def _horizontal_lines(
    canvas: PixelCanvas,
    source: bytes,
) -> tuple[tuple[int, int, int], ...]:
    minimum_span = max(40, canvas.width // 4)
    lines: list[tuple[int, int, int]] = []
    for y_position in range(canvas.height):
        row_start = y_position * canvas.width
        active = [
            x_position
            for x_position in range(canvas.width)
            if source[row_start + x_position] == 255
        ]
        for left, right, count in _runs(active, 4):
            span = right - left
            if span >= minimum_span and count >= span * 0.2:
                lines.append((y_position, left, right))
    return tuple(lines)


def build_text_mask(
    canvas: PixelCanvas,
    segments: tuple[PixelSegment, ...],
) -> tuple[bytes, tuple[PixelBarrier, ...]]:
    mask = bytearray(canvas.contrast)
    _erase_segments(canvas, mask, segments)
    source = bytes(mask)
    barriers = _vertical_barriers(canvas, source)
    for item in barriers:
        for neighbor in range(
            max(0, item.column - 1),
            min(canvas.width, item.column + 2),
        ):
            for y_position in range(item.top, item.bottom):
                mask[y_position * canvas.width + neighbor] = 0
    for y_position, left, right in _horizontal_lines(canvas, source):
        for neighbor in range(
            max(0, y_position - 1),
            min(canvas.height, y_position + 2),
        ):
            start = neighbor * canvas.width + left
            end = neighbor * canvas.width + right
            mask[start:end] = b"\0" * (end - start)
    return bytes(mask), barriers

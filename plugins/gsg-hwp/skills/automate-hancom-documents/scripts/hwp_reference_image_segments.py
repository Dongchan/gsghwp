from __future__ import annotations

from PIL import Image

from hwp_live_values import Rgb
from hwp_reference_image_edge_drawing import refine_segments_with_edge_drawing
from hwp_reference_image_pixels import PixelCanvas, PixelSegment
from hwp_reference_image_segment_filter import filter_layout_segments


_MINIMUM_STROKE_WIDTH_LIMIT = 4
_MAXIMUM_STROKE_WIDTH_LIMIT = 24
_CENTER_COLOR_TOLERANCE = 12
_SIDE_CONTRAST_MINIMUM = 18


def _color_distance(left: Rgb, right: Rgb) -> int:
    return max(abs(left[index] - right[index]) for index in range(3))


def _maximum_stroke_width(canvas: PixelCanvas) -> int:
    return min(
        _MAXIMUM_STROKE_WIDTH_LIMIT,
        max(_MINIMUM_STROKE_WIDTH_LIMIT, min(canvas.width, canvas.height) // 24),
    )


def _canvas_stroke_width(
    canvas: PixelCanvas,
    x: int,
    y: int,
    *,
    x_step: int,
    y_step: int,
) -> int:
    center = canvas.color(x, y)
    maximum = _maximum_stroke_width(canvas)
    before = 0
    before_color: Rgb | None = None
    for offset in range(1, maximum + 1):
        sample_x = x - x_step * offset
        sample_y = y - y_step * offset
        if not (0 <= sample_x < canvas.width and 0 <= sample_y < canvas.height):
            break
        sample = canvas.color(sample_x, sample_y)
        if _color_distance(center, sample) > _CENTER_COLOR_TOLERANCE:
            before_color = sample
            break
        before += 1
    after = 0
    after_color: Rgb | None = None
    for offset in range(1, maximum + 1):
        sample_x = x + x_step * offset
        sample_y = y + y_step * offset
        if not (0 <= sample_x < canvas.width and 0 <= sample_y < canvas.height):
            break
        sample = canvas.color(sample_x, sample_y)
        if _color_distance(center, sample) > _CENTER_COLOR_TOLERANCE:
            after_color = sample
            break
        after += 1
    width = before + after + 1
    if (
        width > maximum
        or before_color is None
        or after_color is None
        or _color_distance(center, before_color) < _SIDE_CONTRAST_MINIMUM
        or _color_distance(center, after_color) < _SIDE_CONTRAST_MINIMUM
    ):
        return 0
    return width


def _horizontal_stroke_width(canvas: PixelCanvas, x: int, y: int) -> int:
    return _canvas_stroke_width(canvas, x, y, x_step=0, y_step=1)


def _vertical_stroke_width(canvas: PixelCanvas, x: int, y: int) -> int:
    return _canvas_stroke_width(canvas, x, y, x_step=1, y_step=0)


def _candidate_positions(
    canvas: PixelCanvas,
    *,
    orientation: str,
    minimum_length: int,
) -> tuple[int, ...]:
    contrast = Image.frombytes("L", (canvas.width, canvas.height), canvas.contrast)
    if orientation == "vertical":
        contrast = contrast.transpose(Image.Transpose.TRANSPOSE)
    width, height = contrast.size
    pixels = contrast.tobytes()
    positions: list[int] = []
    threshold = max(3, minimum_length // 3)
    for position in range(1, height - 1):
        start = position * width
        if pixels[start : start + width].count(255) >= threshold:
            positions.append(position)
    return tuple(positions)


def _horizontal_segments(canvas: PixelCanvas) -> list[PixelSegment]:
    segments: list[PixelSegment] = []
    minimum_length = max(12, canvas.width // 50)
    margin = 1
    for y_position in _candidate_positions(
        canvas,
        orientation="horizontal",
        minimum_length=minimum_length,
    ):
        start: int | None = None
        maximum_width = 0
        for x_position in range(margin, canvas.width - margin + 1):
            stroke_width = (
                _horizontal_stroke_width(canvas, x_position, y_position)
                if x_position < canvas.width - margin
                else 0
            )
            if stroke_width > 0:
                if start is None:
                    start = x_position
                maximum_width = max(maximum_width, stroke_width)
                continue
            if start is None:
                continue
            if x_position - start >= minimum_length:
                midpoint = start + (x_position - start) // 2
                segments.append(
                    PixelSegment(
                        orientation="horizontal",
                        start=max(0, start - maximum_width),
                        end=min(canvas.width, x_position + maximum_width),
                        position=y_position,
                        width=maximum_width,
                        color=canvas.color(midpoint, y_position),
                    )
                )
            start = None
            maximum_width = 0
    return segments


def _vertical_segments(canvas: PixelCanvas) -> list[PixelSegment]:
    segments: list[PixelSegment] = []
    minimum_length = max(8, canvas.height // 100)
    margin = 1
    for x_position in _candidate_positions(
        canvas,
        orientation="vertical",
        minimum_length=minimum_length,
    ):
        start: int | None = None
        maximum_width = 0
        for y_position in range(margin, canvas.height - margin + 1):
            stroke_width = (
                _vertical_stroke_width(canvas, x_position, y_position)
                if y_position < canvas.height - margin
                else 0
            )
            if stroke_width > 0:
                if start is None:
                    start = y_position
                maximum_width = max(maximum_width, stroke_width)
                continue
            if start is None:
                continue
            if y_position - start >= minimum_length:
                midpoint = start + (y_position - start) // 2
                segments.append(
                    PixelSegment(
                        orientation="vertical",
                        start=max(0, start - maximum_width),
                        end=min(canvas.height, y_position + maximum_width),
                        position=x_position,
                        width=maximum_width,
                        color=canvas.color(x_position, midpoint),
                    )
                )
            start = None
            maximum_width = 0
    return segments


def detect_visible_segments(canvas: PixelCanvas) -> tuple[PixelSegment, ...]:
    baseline = filter_layout_segments(
        canvas,
        _horizontal_segments(canvas) + _vertical_segments(canvas),
    )
    refined = refine_segments_with_edge_drawing(
        canvas,
        baseline,
        horizontal_probe=lambda x, y: _horizontal_stroke_width(canvas, x, y),
        vertical_probe=lambda x, y: _vertical_stroke_width(canvas, x, y),
    )
    return filter_layout_segments(
        canvas,
        list(refined),
    )

from __future__ import annotations

from collections.abc import Sequence

from PIL import Image

from hwp_live_values import Rgb
from hwp_reference_image_edge_drawing import refine_segments_with_edge_drawing
from hwp_reference_image_pixels import PixelCanvas, PixelSegment
from hwp_reference_image_segment_filter import filter_layout_segments


_MAXIMUM_STROKE_WIDTH = 4
_CENTER_COLOR_TOLERANCE = 12
_SIDE_CONTRAST_MINIMUM = 18


def _color_distance(left: Rgb, right: Rgb) -> int:
    return max(abs(left[index] - right[index]) for index in range(3))


def _stroke_width(samples: Sequence[Rgb]) -> int:
    center_index = len(samples) // 2
    center = samples[center_index]
    before = 0
    for index in range(center_index - 1, -1, -1):
        if _color_distance(center, samples[index]) > _CENTER_COLOR_TOLERANCE:
            break
        before += 1
    after = 0
    for index in range(center_index + 1, len(samples)):
        if _color_distance(center, samples[index]) > _CENTER_COLOR_TOLERANCE:
            break
        after += 1
    width = before + after + 1
    if width > _MAXIMUM_STROKE_WIDTH:
        return 0
    before_index = center_index - before - 1
    after_index = center_index + after + 1
    if before_index < 0 or after_index >= len(samples):
        return 0
    if _color_distance(center, samples[before_index]) < _SIDE_CONTRAST_MINIMUM:
        return 0
    if _color_distance(center, samples[after_index]) < _SIDE_CONTRAST_MINIMUM:
        return 0
    return width


def _horizontal_stroke_width(canvas: PixelCanvas, x: int, y: int) -> int:
    samples = tuple(
        canvas.color(x, y + offset)
        for offset in range(-_MAXIMUM_STROKE_WIDTH, _MAXIMUM_STROKE_WIDTH + 1)
    )
    return _stroke_width(samples)


def _vertical_stroke_width(canvas: PixelCanvas, x: int, y: int) -> int:
    samples = tuple(
        canvas.color(x + offset, y)
        for offset in range(-_MAXIMUM_STROKE_WIDTH, _MAXIMUM_STROKE_WIDTH + 1)
    )
    return _stroke_width(samples)


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
    for position in range(_MAXIMUM_STROKE_WIDTH, height - _MAXIMUM_STROKE_WIDTH):
        start = position * width
        if pixels[start : start + width].count(255) >= threshold:
            positions.append(position)
    return tuple(positions)


def _horizontal_segments(canvas: PixelCanvas) -> list[PixelSegment]:
    segments: list[PixelSegment] = []
    minimum_length = max(12, canvas.width // 50)
    margin = _MAXIMUM_STROKE_WIDTH
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
    margin = _MAXIMUM_STROKE_WIDTH
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

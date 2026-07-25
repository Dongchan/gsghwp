from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from PIL import Image

from hwp_reference_image_pixels import PixelBox, PixelCanvas, PixelObject


@dataclass(frozen=True, slots=True)
class _Run:
    left: int
    right: int
    y: int
    color_index: int


def _row_runs(
    pixels: bytes,
    *,
    width: int,
    y_position: int,
    minimum_length: int,
) -> tuple[_Run, ...]:
    row_start = y_position * width
    runs: list[_Run] = []
    start = 0
    color_index = pixels[row_start]
    for x_position in range(1, width + 1):
        next_color = pixels[row_start + x_position] if x_position < width else -1
        if next_color == color_index:
            continue
        if x_position - start >= minimum_length:
            runs.append(_Run(start, x_position, y_position, color_index))
        start = x_position
        color_index = next_color
    return tuple(runs)


def _matching_fraction(
    pixels: bytes,
    *,
    width: int,
    y_position: int,
    run: _Run,
) -> float:
    start = y_position * width + run.left
    end = y_position * width + run.right
    return pixels[start:end].count(run.color_index) / max(1, run.right - run.left)


def _expand_run(
    pixels: bytes,
    *,
    width: int,
    height: int,
    run: _Run,
) -> PixelBox:
    top = run.y
    while top > 0 and _matching_fraction(
        pixels,
        width=width,
        y_position=top - 1,
        run=run,
    ) >= 0.42:
        top -= 1
    bottom = run.y + 1
    while bottom < height and _matching_fraction(
        pixels,
        width=width,
        y_position=bottom,
        run=run,
    ) >= 0.42:
        bottom += 1
    return PixelBox(run.left, top, run.right, bottom)


def _intersection_area(left: PixelBox, right: PixelBox) -> int:
    width = max(0, min(left.right, right.right) - max(left.left, right.left))
    height = max(0, min(left.bottom, right.bottom) - max(left.top, right.top))
    return width * height


def _iou(left: PixelBox, right: PixelBox) -> float:
    intersection = _intersection_area(left, right)
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / max(1, union)


def _contains(outer: PixelBox, inner: PixelBox) -> bool:
    return (
        outer.left <= inner.left <= inner.right <= outer.right
        and outer.top <= inner.top <= inner.bottom <= outer.bottom
    )


def _color_distance(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
) -> int:
    return max(abs(left[index] - right[index]) for index in range(3))


def _ring_differs(
    pixels: bytes,
    *,
    width: int,
    height: int,
    bbox: PixelBox,
    color_index: int,
) -> bool:
    samples = 0
    different = 0
    for y_position in range(max(0, bbox.top - 2), min(height, bbox.bottom + 2)):
        for x_position in range(max(0, bbox.left - 2), min(width, bbox.right + 2)):
            if bbox.left <= x_position < bbox.right and bbox.top <= y_position < bbox.bottom:
                continue
            samples += 1
            different += pixels[y_position * width + x_position] != color_index
    return samples > 0 and different / samples >= 0.12


def _ring_supports_nested_background(
    pixels: bytes,
    *,
    width: int,
    height: int,
    bbox: PixelBox,
    color_index: int,
) -> bool:
    surrounding: Counter[int] = Counter()
    samples = 0
    for y_position in range(max(0, bbox.top - 2), min(height, bbox.bottom + 2)):
        for x_position in range(max(0, bbox.left - 2), min(width, bbox.right + 2)):
            if bbox.left <= x_position < bbox.right and bbox.top <= y_position < bbox.bottom:
                continue
            samples += 1
            index = pixels[y_position * width + x_position]
            if index != color_index:
                surrounding[index] += 1
    return bool(surrounding) and surrounding.most_common(1)[0][1] >= samples * 0.45


def detect_filled_regions(canvas: PixelCanvas) -> list[PixelObject]:
    quantized = canvas.image.quantize(
        colors=64,
        method=Image.Quantize.FASTOCTREE,
        dither=Image.Dither.NONE,
    )
    pixels = quantized.tobytes()
    minimum_length = max(12, canvas.width // 80)
    minimum_height = max(5, canvas.height // 300)
    corners = (
        canvas.color(0, 0),
        canvas.color(canvas.width - 1, 0),
        canvas.color(0, canvas.height - 1),
        canvas.color(canvas.width - 1, canvas.height - 1),
    )
    background = Counter(corners).most_common(1)[0][0]
    candidates: list[tuple[PixelBox, int, tuple[int, int, int]]] = []
    for y_position in range(0, canvas.height, 2):
        for run in _row_runs(
            pixels,
            width=canvas.width,
            y_position=y_position,
            minimum_length=minimum_length,
        ):
            bbox = _expand_run(
                pixels,
                width=canvas.width,
                height=canvas.height,
                run=run,
            )
            if bbox.height < minimum_height:
                continue
            if bbox.width * bbox.height >= canvas.width * canvas.height * 0.82:
                continue
            if not _ring_differs(
                pixels,
                width=canvas.width,
                height=canvas.height,
                bbox=bbox,
                color_index=run.color_index,
            ):
                continue
            color = canvas.color(
                min(canvas.width - 1, (bbox.left + bbox.right) // 2),
                min(canvas.height - 1, run.y),
            )
            touches_frame = (
                bbox.left == 0
                or bbox.top == 0
                or bbox.right == canvas.width
                or bbox.bottom == canvas.height
            )
            if touches_frame and _color_distance(color, background) <= 12:
                continue
            if (
                _color_distance(color, background) <= 12
                and (
                    bbox.width < max(20, canvas.width // 20)
                    or bbox.height < max(6, canvas.height // 200)
                )
            ):
                continue
            if (
                _color_distance(color, background) <= 12
                and not _ring_supports_nested_background(
                    pixels,
                    width=canvas.width,
                    height=canvas.height,
                    bbox=bbox,
                    color_index=run.color_index,
                )
            ):
                continue
            candidates.append((bbox, run.color_index, color))
    retained: list[tuple[PixelBox, int, tuple[int, int, int]]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: item[0].width * item[0].height,
        reverse=True,
    ):
        if any(
            _iou(candidate[0], existing[0]) >= 0.88
            or (
                _contains(existing[0], candidate[0])
                and _color_distance(existing[2], candidate[2]) <= 12
            )
            for existing in retained
        ):
            continue
        retained.append(candidate)
    return [PixelObject(bbox=bbox, fill=color, edges=()) for bbox, _, color in retained]

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import cast

from PIL import Image, ImageFilter, ImageOps, ImageStat


@dataclass(frozen=True, slots=True)
class RegionEvidence:
    activity: float
    background_distance: float


@dataclass(frozen=True, slots=True)
class GapPixelAnalysis:
    cells: dict[tuple[int, int], RegionEvidence]
    rows: tuple[RegionEvidence, ...]
    columns: tuple[RegionEvidence, ...]
    row_activity_cutoff: float
    row_color_cutoff: float
    column_activity_cutoff: float
    column_color_cutoff: float
    cell_activity_cutoff: float
    cell_color_cutoff: float
    row_sizes: tuple[float, ...]
    column_sizes: tuple[float, ...]


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    remainder = position - lower
    return ordered[lower] * (1 - remainder) + ordered[upper] * remainder


def estimate_background_color(
    image: Image.Image,
) -> tuple[float, float, float]:
    width, height = image.size
    stride = max(1, min(width, height) // 128)
    perimeter: list[tuple[int, int, int]] = [
        cast(tuple[int, int, int], image.getpixel((x, y)))
        for x, y in (
            *((x, 0) for x in range(0, width, stride)),
            *((x, height - 1) for x in range(0, width, stride)),
            *((0, y) for y in range(0, height, stride)),
            *((width - 1, y) for y in range(0, height, stride)),
        )
    ]
    quantized: Counter[tuple[int, int, int]] = Counter(
        (color[0] // 16, color[1] // 16, color[2] // 16)
        for color in perimeter
    )
    bucket, _ = quantized.most_common(1)[0]
    members = [
        color
        for color in perimeter
        if (color[0] // 16, color[1] // 16, color[2] // 16) == bucket
    ]
    return (
        sum(color[0] for color in members) / len(members),
        sum(color[1] for color in members) / len(members),
        sum(color[2] for color in members) / len(members),
    )


def _region_box(
    rows: tuple[float, ...],
    columns: tuple[float, ...],
    top: int,
    left: int,
    bottom: int,
    right: int,
    size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = size
    x0 = round(columns[left] * width)
    x1 = round(columns[right] * width)
    y0 = round(rows[top] * height)
    y1 = round(rows[bottom] * height)
    inset_x = max(1, (x1 - x0) // 8)
    inset_y = max(1, (y1 - y0) // 8)
    return (
        min(x1 - 1, x0 + inset_x),
        min(y1 - 1, y0 + inset_y),
        max(x0 + 1, x1 - inset_x),
        max(y0 + 1, y1 - inset_y),
    )


def _region_evidence(
    image: Image.Image,
    edges: Image.Image,
    background: tuple[float, float, float],
    box: tuple[int, int, int, int],
) -> RegionEvidence:
    color = ImageStat.Stat(image.crop(box)).mean
    edge = ImageStat.Stat(edges.crop(box)).mean[0] / 255
    distance = sum(
        abs(channel - reference)
        for channel, reference in zip(color, background, strict=True)
    ) / 765
    return RegionEvidence(
        activity=edge,
        background_distance=distance,
    )


def analyze_gap_pixels(
    source_image: Image.Image,
    row_breakpoints: tuple[float, ...],
    column_breakpoints: tuple[float, ...],
) -> GapPixelAnalysis:
    image = source_image.convert("RGB")
    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    background = estimate_background_color(image)
    edges = ImageOps.autocontrast(image.convert("L")).filter(
        ImageFilter.FIND_EDGES
    )
    row_count = len(row_breakpoints) - 1
    column_count = len(column_breakpoints) - 1

    def measure(
        top: int,
        left: int,
        bottom: int,
        right: int,
    ) -> RegionEvidence:
        return _region_evidence(
            image,
            edges,
            background,
            _region_box(
                row_breakpoints,
                column_breakpoints,
                top,
                left,
                bottom,
                right,
                image.size,
            ),
        )

    cells = {
        (row, column): measure(
            row,
            column,
            row + 1,
            column + 1,
        )
        for row in range(row_count)
        for column in range(column_count)
    }
    rows = tuple(
        measure(row, 0, row + 1, column_count)
        for row in range(row_count)
    )
    columns = tuple(
        measure(0, column, row_count, column + 1)
        for column in range(column_count)
    )
    positive = [
        item.activity
        for item in cells.values()
        if item.activity > 0
    ]
    return GapPixelAnalysis(
        cells=cells,
        rows=rows,
        columns=columns,
        row_activity_cutoff=quantile(
            [item.activity for item in rows],
            0.4,
        ),
        row_color_cutoff=quantile(
            [item.background_distance for item in rows],
            0.4,
        ),
        column_activity_cutoff=quantile(
            [item.activity for item in columns],
            0.4,
        ),
        column_color_cutoff=quantile(
            [item.background_distance for item in columns],
            0.4,
        ),
        cell_activity_cutoff=quantile(positive, 0.4) if positive else 0,
        cell_color_cutoff=quantile(
            [item.background_distance for item in cells.values()],
            0.4,
        ),
        row_sizes=tuple(
            right - left
            for left, right in zip(
                row_breakpoints,
                row_breakpoints[1:],
            )
        ),
        column_sizes=tuple(
            right - left
            for left, right in zip(
                column_breakpoints,
                column_breakpoints[1:],
            )
        ),
    )

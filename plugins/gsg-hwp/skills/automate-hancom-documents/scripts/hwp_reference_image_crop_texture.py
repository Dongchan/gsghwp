from __future__ import annotations

from typing import cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from hwp_reference_image_crop_contract import (
    CropBoundaryAnalysis,
    box_intersection_area,
    expanded_search_box,
)
from hwp_reference_image_pixels import PixelBox


_EDGE_DIFFERENCE = 28
_COLUMN_DENSITY = 0.08
_LOW_DETAIL_COLUMN_DENSITY = 0.065
_ROW_DENSITY = 0.03


def _texture_edges(pixels: NDArray[np.uint8]) -> NDArray[np.bool_]:
    values = pixels.astype(np.int16)
    edges = np.zeros(pixels.shape[:2], dtype=np.bool_)
    column_changes = cast(
        NDArray[np.bool_],
        np.max(
            np.abs(values[:, 1:] - values[:, :-1]),
            axis=2,
        )
        > _EDGE_DIFFERENCE,
    )
    row_changes = cast(
        NDArray[np.bool_],
        np.max(
            np.abs(values[1:, :] - values[:-1, :]),
            axis=2,
        )
        > _EDGE_DIFFERENCE,
    )
    edges[:, 1:] |= column_changes
    edges[1:, :] |= row_changes
    return edges


def _active_runs(
    density: NDArray[np.float64],
    *,
    threshold: float,
) -> tuple[tuple[int, int], ...]:
    values = cast(list[float], density.tolist())
    positions = tuple(
        index
        for index, value in enumerate(values)
        if value >= threshold
    )
    if not positions:
        return ()
    start = positions[0]
    previous = start
    runs: list[tuple[int, int]] = []
    for position in positions[1:]:
        if position - previous > 3:
            runs.append((start, previous + 1))
            start = position
        previous = position
    runs.append((start, previous + 1))
    return tuple(runs)


def _largest_run(
    density: NDArray[np.float64],
    *,
    threshold: float,
    minimum_length: int,
) -> tuple[int, int] | None:
    viable = tuple(
        run
        for run in _active_runs(density, threshold=threshold)
        if run[1] - run[0] >= minimum_length
    )
    return max(viable, key=lambda run: run[1] - run[0], default=None)


def _fallback(requested: PixelBox) -> CropBoundaryAnalysis:
    return CropBoundaryAnalysis(
        requested=requested,
        refined=requested,
        method="requested_fallback",
        confidence=0.0,
        needs_review=True,
        component_count=0,
    )


def refine_tight_raster_boundary(
    image: Image.Image,
    requested: PixelBox,
) -> CropBoundaryAnalysis:
    width, height = image.size
    requested_area = requested.width * requested.height
    if requested_area >= width * height * 0.9:
        return CropBoundaryAnalysis(
            requested=requested,
            refined=requested,
            method="exact_full_image",
            confidence=1.0,
            needs_review=False,
            component_count=0,
        )

    search = expanded_search_box(requested, width=width, height=height)
    with image.crop((search.left, search.top, search.right, search.bottom)) as region:
        with region.convert("RGB") as rgb:
            pixels = np.asarray(rgb, dtype=np.uint8).copy()
    edges = _texture_edges(pixels)
    column_density = cast(NDArray[np.float64], edges.mean(axis=0))
    row_density = cast(NDArray[np.float64], edges.mean(axis=1))
    minimum_width = max(4, round(requested.width * 0.5))
    minimum_height = max(4, round(requested.height * 0.45))
    column_run = _largest_run(
        column_density,
        threshold=_COLUMN_DENSITY,
        minimum_length=minimum_width,
    )
    if column_run is None or column_run[1] - column_run[0] < requested.width * 0.85:
        low_detail_run = _largest_run(
            column_density,
            threshold=_LOW_DETAIL_COLUMN_DENSITY,
            minimum_length=minimum_width,
        )
        if low_detail_run is not None and (
            column_run is None
            or low_detail_run[1] - low_detail_run[0] > column_run[1] - column_run[0]
        ):
            column_run = low_detail_run
    row_run = _largest_run(
        row_density,
        threshold=_ROW_DENSITY,
        minimum_length=minimum_height,
    )
    if column_run is None or row_run is None:
        return _fallback(requested)

    refined = PixelBox(
        left=search.left + column_run[0],
        top=search.top + row_run[0],
        right=search.left + column_run[1],
        bottom=search.top + row_run[1],
    )
    refined_area = refined.width * refined.height
    inside_ratio = box_intersection_area(refined, requested) / max(1, refined_area)
    width_ratio = refined.width / requested.width
    height_ratio = refined.height / requested.height
    viable = (
        inside_ratio >= 0.8
        and 0.5 <= width_ratio <= 1.25
        and 0.45 <= height_ratio <= 1.25
        and refined_area <= requested_area * 1.5
    )
    if not viable:
        return _fallback(requested)

    confidence = round(
        min(
            1.0,
            inside_ratio * 0.55
            + min(1.0, width_ratio) * 0.25
            + min(1.0, height_ratio) * 0.2,
        ),
        4,
    )
    return CropBoundaryAnalysis(
        requested=requested,
        refined=refined,
        method="texture_rectangle",
        confidence=confidence,
        needs_review=confidence < 0.7,
        component_count=1,
    )

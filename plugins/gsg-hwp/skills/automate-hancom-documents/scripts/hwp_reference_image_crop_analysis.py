from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from hwp_reference_image_crop_contract import (
    CropBoundaryAnalysis,
    box_intersection_area,
    expanded_search_box,
    union_boxes,
)
from hwp_reference_image_pixels import PixelBox


@dataclass(frozen=True, slots=True)
class _Component:
    box: PixelBox
    foreground_pixels: int
    request_intersection: int

    @property
    def box_area(self) -> int:
        return self.box.width * self.box.height


def _perimeter_pixels(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    return np.concatenate((image[0], image[-1], image[:, 0], image[:, -1]))


def _dominant_background(image: NDArray[np.uint8]) -> NDArray[np.int16]:
    perimeter = _perimeter_pixels(image)
    quantized = perimeter // 16
    keys = (
        quantized[:, 0].astype(np.int32) * 256
        + quantized[:, 1].astype(np.int32) * 16
        + quantized[:, 2].astype(np.int32)
    )
    winning_key = int(np.bincount(keys, minlength=4096).argmax())
    selected = perimeter[keys == winning_key]
    median = cast(NDArray[np.float64], np.median(selected, axis=0))
    return median.astype(np.int16)


def _foreground_components(
    image: NDArray[np.uint8],
    *,
    search: PixelBox,
    requested: PixelBox,
) -> tuple[_Component, ...]:
    try:
        import cv2
    except ModuleNotFoundError as error:
        raise ValueError(
            "reference image crop analysis requires opencv-contrib-python"
        ) from error

    background = _dominant_background(image)
    difference: NDArray[np.int16] = image.astype(np.int16) - background
    distance = cast(NDArray[np.int16], np.max(np.abs(difference), axis=2))
    mask: NDArray[np.uint8] = (distance > 12).astype(np.uint8) * 255
    kernel_size = 5 if min(image.shape[:2]) >= 180 else 3
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    mask = cast(
        NDArray[np.uint8],
        cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel),
    )
    mask = cast(NDArray[np.uint8], cv2.dilate(mask, kernel))
    count, _, raw_stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    stats: NDArray[np.int32] = np.asarray(raw_stats, dtype=np.int32)
    result: list[_Component] = []
    for index in range(1, count):
        x = int(cast(np.int32, stats[index, 0]))
        y = int(cast(np.int32, stats[index, 1]))
        width = int(cast(np.int32, stats[index, 2]))
        height = int(cast(np.int32, stats[index, 3]))
        pixels = int(cast(np.int32, stats[index, 4]))
        box = PixelBox(
            left=search.left + x,
            top=search.top + y,
            right=search.left + x + width,
            bottom=search.top + y + height,
        )
        intersection = box_intersection_area(box, requested)
        if intersection > 0:
            result.append(
                _Component(
                    box=box,
                    foreground_pixels=pixels,
                    request_intersection=intersection,
                )
            )
    return tuple(result)


def _vertical_overlap(left: PixelBox, right: PixelBox) -> int:
    return max(0, min(left.bottom, right.bottom) - max(left.top, right.top))


def _horizontal_gap(left: PixelBox, right: PixelBox) -> int:
    if left.right < right.left:
        return right.left - left.right
    if right.right < left.left:
        return left.left - right.right
    return 0


def _horizontal_overlap(left: PixelBox, right: PixelBox) -> int:
    return max(0, min(left.right, right.right) - max(left.left, right.left))


def _vertical_gap(left: PixelBox, right: PixelBox) -> int:
    if left.bottom < right.top:
        return right.top - left.bottom
    if right.bottom < left.top:
        return left.top - right.bottom
    return 0


def _related_satellite(main: _Component, component: _Component) -> bool:
    if component is main:
        return True
    minimum_area = max(36, round(main.box_area * 0.0005))
    maximum_area = max(minimum_area, round(main.box_area * 0.12))
    if component.box_area < minimum_area or component.box_area > maximum_area:
        return False
    if box_intersection_area(main.box, component.box) > 0:
        return True

    shared_height = _vertical_overlap(main.box, component.box)
    maximum_horizontal_gap = max(6, round(main.box.width * 0.012))
    if (
        shared_height >= min(main.box.height, component.box.height) * 0.55
        and _horizontal_gap(main.box, component.box) <= maximum_horizontal_gap
    ):
        return True

    shared_width = _horizontal_overlap(main.box, component.box)
    maximum_vertical_gap = max(6, round(main.box.height * 0.04))
    return (
        shared_width >= min(main.box.width, component.box.width) * 0.6
        and _vertical_gap(main.box, component.box) <= maximum_vertical_gap
    )


def _include_related_satellites(
    main: _Component,
    components: tuple[_Component, ...],
) -> PixelBox:
    refined = main.box
    for component in components:
        if not _related_satellite(main, component):
            continue
        refined = union_boxes(refined, component.box)
    return refined


def _touches_search_start(value: int, start: int) -> bool:
    return value <= start + 1


def _touches_search_end(value: int, end: int) -> bool:
    return value >= end - 1


def _safe_group_boundary(
    requested: PixelBox,
    group: PixelBox,
    search: PixelBox,
) -> tuple[PixelBox, int]:
    blocked = 0
    left = group.left
    if group.left < requested.left and _touches_search_start(group.left, search.left):
        left = requested.left
        blocked += 1
    top = group.top
    if group.top < requested.top and _touches_search_start(group.top, search.top):
        top = requested.top
        blocked += 1
    right = group.right
    if group.right > requested.right and _touches_search_end(group.right, search.right):
        right = requested.right
        blocked += 1
    bottom = group.bottom
    if group.bottom > requested.bottom and _touches_search_end(
        group.bottom,
        search.bottom,
    ):
        bottom = requested.bottom
        blocked += 1
    return PixelBox(left=left, top=top, right=right, bottom=bottom), blocked


def refine_crop_boundary(
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
    components = _foreground_components(
        pixels,
        search=search,
        requested=requested,
    )
    if not components:
        return CropBoundaryAnalysis(
            requested=requested,
            refined=requested,
            method="requested_fallback",
            confidence=0.0,
            needs_review=True,
            component_count=0,
        )

    main = max(
        components,
        key=lambda component: (
            component.request_intersection,
            component.box_area,
            component.foreground_pixels,
        ),
    )
    group = _include_related_satellites(main, components)
    refined, blocked_sides = _safe_group_boundary(requested, group, search)
    covered_request = box_intersection_area(refined, requested) / requested_area
    competing_area = sum(item.request_intersection for item in components)
    dominance = main.request_intersection / max(1, competing_area)
    confidence = round(min(1.0, covered_request * 0.65 + dominance * 0.35), 4)
    refined_area = refined.width * refined.height
    viable = (
        covered_request >= 0.35
        and refined.width >= requested.width * 0.25
        and refined.height >= requested.height * 0.25
        and refined_area <= requested_area * 2.5
    )
    search_saturated = blocked_sides >= 2
    if not viable or search_saturated:
        return CropBoundaryAnalysis(
            requested=requested,
            refined=requested,
            method="requested_fallback",
            confidence=min(confidence, 0.5) if search_saturated else confidence,
            needs_review=True,
            component_count=len(components),
        )
    return CropBoundaryAnalysis(
        requested=requested,
        refined=refined,
        method="dominant_connected_content",
        confidence=confidence,
        needs_review=confidence < 0.62,
        component_count=len(components),
    )

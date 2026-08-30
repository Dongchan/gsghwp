from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from hwp_reference_image_budget import ReferenceAnalysisTimeBudget
from hwp_reference_image_pixels import PixelBox, PixelCanvas, PixelGap, PixelObject


@dataclass(frozen=True, slots=True)
class _ObjectGeometry:
    """물체 상자를 배열로 세워 둔 것.

    빈틈 후보는 물체 쌍 전부를 도는데(실측 1,905개 물체 = 3,627,120 쌍) 쌍마다
    다시 물체 전부를 훑어 막힘을 본다. 상자 값은 회전 내내 바뀌지 않으므로 한
    번만 배열로 세워 두면 안쪽 두 겹을 정수 배열 연산으로 접을 수 있다.
    """

    left: NDArray[np.int64]
    top: NDArray[np.int64]
    right: NDArray[np.int64]
    bottom: NDArray[np.int64]
    width: NDArray[np.int64]
    height: NDArray[np.int64]
    structural: NDArray[np.bool_]


def _object_geometry(
    canvas: PixelCanvas,
    objects: tuple[PixelObject, ...],
) -> _ObjectGeometry:
    boxes = np.array(
        [
            (item.bbox.left, item.bbox.top, item.bbox.right, item.bbox.bottom)
            for item in objects
        ],
        dtype=np.int64,
    ).reshape(-1, 4)
    left = boxes[:, 0]
    top = boxes[:, 1]
    right = boxes[:, 2]
    bottom = boxes[:, 3]
    width = right - left
    height = bottom - top
    return _ObjectGeometry(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        width=width,
        height=height,
        structural=(width >= max(10, canvas.width // 100))
        & (height >= max(8, canvas.height // 120)),
    )


def _vertical_pair_mask(
    canvas: PixelCanvas,
    geometry: _ObjectGeometry,
    left_index: int,
) -> NDArray[np.bool_]:
    """세로 빈틈 후보가 될 수 없는 짝을 미리 떨군다.

    아래 판정은 _vertical_candidate 앞머리와 같은 식이다 — 여기서 떨어지는
    짝은 원래 구현에서도 None 이 되므로 남는 짝의 목록과 순서가 같다.
    """
    empty = np.zeros(geometry.left.size, dtype=np.bool_)
    if not geometry.structural.item(left_index):
        return empty
    size = geometry.left - geometry.right.item(left_index)
    shared_top = np.maximum(geometry.top.item(left_index), geometry.top)
    shared_bottom = np.minimum(geometry.bottom.item(left_index), geometry.bottom)
    shared_height = shared_bottom - shared_top
    smaller = np.minimum(geometry.height.item(left_index), geometry.height)
    larger = np.maximum(geometry.height.item(left_index), geometry.height)
    mask = (
        geometry.structural
        & (size >= 2)
        & (size <= canvas.width // 4)
        & (shared_height >= smaller * 0.55)
        & (larger <= smaller * 3)
    )
    mask[left_index] = False
    return mask


def _horizontal_pair_mask(
    canvas: PixelCanvas,
    geometry: _ObjectGeometry,
    top_index: int,
) -> NDArray[np.bool_]:
    empty = np.zeros(geometry.left.size, dtype=np.bool_)
    if not geometry.structural.item(top_index):
        return empty
    size = geometry.top - geometry.bottom.item(top_index)
    shared_left = np.maximum(geometry.left.item(top_index), geometry.left)
    shared_right = np.minimum(geometry.right.item(top_index), geometry.right)
    shared_width = shared_right - shared_left
    smaller = np.minimum(geometry.width.item(top_index), geometry.width)
    larger = np.maximum(geometry.width.item(top_index), geometry.width)
    mask = (
        geometry.structural
        & (size >= 2)
        & (size <= canvas.height // 4)
        & (shared_width >= smaller * 0.55)
        & (larger <= smaller * 3)
    )
    mask[top_index] = False
    return mask


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def _structural_object(canvas: PixelCanvas, bbox: PixelBox) -> bool:
    return (
        bbox.width >= max(10, canvas.width // 100)
        and bbox.height >= max(8, canvas.height // 120)
    )


def _blocked(
    corridor: PixelBox,
    geometry: _ObjectGeometry,
    left_index: int,
    right_index: int,
) -> bool:
    overlap_width = np.maximum(
        0,
        np.minimum(geometry.right, corridor.right)
        - np.maximum(geometry.left, corridor.left),
    )
    overlap_height = np.maximum(
        0,
        np.minimum(geometry.bottom, corridor.bottom)
        - np.maximum(geometry.top, corridor.top),
    )
    surrounds = (
        (geometry.left <= corridor.left)
        & (geometry.top <= corridor.top)
        & (geometry.right >= corridor.right)
        & (geometry.bottom >= corridor.bottom)
    )
    blocking = ~surrounds & (
        overlap_width * overlap_height
        >= corridor.width * corridor.height * 0.08
    )
    blocking[left_index] = False
    blocking[right_index] = False
    return bool(blocking.any())


def _vertical_candidate(
    canvas: PixelCanvas,
    objects: tuple[PixelObject, ...],
    geometry: _ObjectGeometry,
    left_index: int,
    right_index: int,
) -> PixelGap | None:
    left = objects[left_index].bbox
    right = objects[right_index].bbox
    if not (
        _structural_object(canvas, left)
        and _structural_object(canvas, right)
    ):
        return None
    size = right.left - left.right
    if size < 2 or size > canvas.width // 4:
        return None
    shared_top = max(left.top, right.top)
    shared_bottom = min(left.bottom, right.bottom)
    shared_height = shared_bottom - shared_top
    if shared_height < min(left.height, right.height) * 0.55:
        return None
    if max(left.height, right.height) > min(left.height, right.height) * 3:
        return None
    corridor = PixelBox(left.right, shared_top, right.left, shared_bottom)
    if _blocked(corridor, geometry, left_index, right_index):
        return None
    inset = min(4, max(1, size // 4))
    interior = PixelBox(
        corridor.left + inset,
        corridor.top,
        corridor.right - inset,
        corridor.bottom,
    )
    if canvas.contrast_ratio(interior) > 0.025:
        return None
    return PixelGap(
        orientation="vertical",
        bbox=corridor,
        minimum_size=size,
        bounded_by=(left_index, right_index),
    )


def _horizontal_candidate(
    canvas: PixelCanvas,
    objects: tuple[PixelObject, ...],
    geometry: _ObjectGeometry,
    top_index: int,
    bottom_index: int,
) -> PixelGap | None:
    top = objects[top_index].bbox
    bottom = objects[bottom_index].bbox
    if not (
        _structural_object(canvas, top)
        and _structural_object(canvas, bottom)
    ):
        return None
    size = bottom.top - top.bottom
    if size < 2 or size > canvas.height // 4:
        return None
    shared_left = max(top.left, bottom.left)
    shared_right = min(top.right, bottom.right)
    shared_width = shared_right - shared_left
    if shared_width < min(top.width, bottom.width) * 0.55:
        return None
    if max(top.width, bottom.width) > min(top.width, bottom.width) * 3:
        return None
    corridor = PixelBox(shared_left, top.bottom, shared_right, bottom.top)
    if _blocked(corridor, geometry, top_index, bottom_index):
        return None
    inset = min(4, max(1, size // 4))
    interior = PixelBox(
        corridor.left,
        corridor.top + inset,
        corridor.right,
        corridor.bottom - inset,
    )
    if canvas.contrast_ratio(interior) > 0.025:
        return None
    return PixelGap(
        orientation="horizontal",
        bbox=corridor,
        minimum_size=size,
        bounded_by=(top_index, bottom_index),
    )


def _same_gap(left: PixelGap, right: PixelGap) -> bool:
    if left.orientation != right.orientation:
        return False
    overlap_width = _overlap(
        left.bbox.left,
        left.bbox.right,
        right.bbox.left,
        right.bbox.right,
    )
    overlap_height = _overlap(
        left.bbox.top,
        left.bbox.bottom,
        right.bbox.top,
        right.bbox.bottom,
    )
    overlap_area = overlap_width * overlap_height
    minimum_area = min(
        left.bbox.width * left.bbox.height,
        right.bbox.width * right.bbox.height,
    )
    return overlap_area >= minimum_area * 0.7


def detect_protected_gaps(
    canvas: PixelCanvas,
    objects: tuple[PixelObject, ...],
    *,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> tuple[PixelGap, ...]:
    if budget is not None and budget.exhausted():
        return ()
    geometry = _object_geometry(canvas, objects)
    candidates: list[PixelGap] = []
    for first_index in range(len(objects)):
        # 물체 쌍 전부를 도는 회전이라 물체가 많은 그림에서 이 단계가 지배한다
        # (1600x1200 잔모자이크 실측 물체 1,330개, 1.2초).
        if budget is not None and budget.exhausted():
            break
        vertical_mask = _vertical_pair_mask(canvas, geometry, first_index)
        horizontal_mask = _horizontal_pair_mask(canvas, geometry, first_index)
        paired = np.flatnonzero(vertical_mask | horizontal_mask)
        for second_index in cast(list[int], paired.tolist()):
            # 짝의 방문 순서와 세로-가로 순서를 그대로 둔다 — 뒤의 정렬이
            # 안정 정렬이라 같은 넓이끼리는 넣은 차례가 결과를 정한다.
            if vertical_mask.item(second_index):
                vertical = _vertical_candidate(
                    canvas,
                    objects,
                    geometry,
                    first_index,
                    second_index,
                )
                if vertical is not None:
                    candidates.append(vertical)
            if horizontal_mask.item(second_index):
                horizontal = _horizontal_candidate(
                    canvas,
                    objects,
                    geometry,
                    first_index,
                    second_index,
                )
                if horizontal is not None:
                    candidates.append(horizontal)
    retained: list[PixelGap] = []
    for candidate in sorted(
        candidates,
        key=lambda item: item.bbox.width * item.bbox.height,
        reverse=True,
    ):
        # 남긴 빈틈 전부와 겹침을 재므로 후보 수에 대해 제곱이다.
        if budget is not None and budget.exhausted():
            break
        if not any(_same_gap(candidate, existing) for existing in retained):
            retained.append(candidate)
    return tuple(retained)

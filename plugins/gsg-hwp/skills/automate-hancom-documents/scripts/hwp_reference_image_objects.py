from __future__ import annotations

from typing import Literal, cast

import numpy as np
from numpy.typing import NDArray

from hwp_reference_image_budget import ReferenceAnalysisTimeBudget
from hwp_reference_image_fills import detect_filled_regions
from hwp_reference_image_pixels import PixelBox, PixelCanvas, PixelObject, PixelSegment


def _intersection_area(left: PixelBox, right: PixelBox) -> int:
    width = max(0, min(left.right, right.right) - max(left.left, right.left))
    height = max(0, min(left.bottom, right.bottom) - max(left.top, right.top))
    return width * height


def _iou(left: PixelBox, right: PixelBox) -> float:
    intersection = _intersection_area(left, right)
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / max(1, union)


def _axis_table(
    segments: tuple[PixelSegment, ...],
    orientation: str,
) -> NDArray[np.int64]:
    rows = [
        (item.position, item.start, item.end)
        for item in segments
        if item.orientation == orientation
    ]
    if not rows:
        return np.zeros((0, 3), dtype=np.int64)
    return np.array(rows, dtype=np.int64)


def _any_edge_match(
    table: NDArray[np.int64],
    *,
    expected: int,
    lower: int,
    upper: int,
) -> bool:
    if table.shape[0] == 0:
        return False
    tolerance = 3
    return bool(
        (
            (np.abs(table[:, 0] - expected) <= tolerance)
            & (table[:, 1] <= lower + tolerance)
            & (table[:, 2] >= upper - tolerance)
        ).any()
    )


def _attach_edges(
    objects: list[PixelObject],
    segments: tuple[PixelSegment, ...],
) -> list[PixelObject]:
    # 물체 x 변 x 선분 세 겹이라 실측 1.1MP 모자이크에서 17,286,344 회였다.
    # 방향별 표를 한 번 세워 두고 변마다 배열 판정으로 바꾼다 — 판정식이
    # 정수 비교뿐이라 어느 선분 하나라도 맞는지의 답은 그대로다.
    horizontal = _axis_table(segments, "horizontal")
    vertical = _axis_table(segments, "vertical")
    attached: list[PixelObject] = []
    for item in objects:
        bbox = item.bbox
        edges: list[Literal["top", "right", "bottom", "left"]] = []
        if _any_edge_match(
            horizontal,
            expected=bbox.top,
            lower=bbox.left,
            upper=bbox.right,
        ):
            edges.append("top")
        if _any_edge_match(
            vertical,
            expected=bbox.right - 1,
            lower=bbox.top,
            upper=bbox.bottom,
        ):
            edges.append("right")
        if _any_edge_match(
            horizontal,
            expected=bbox.bottom - 1,
            lower=bbox.left,
            upper=bbox.right,
        ):
            edges.append("bottom")
        if _any_edge_match(
            vertical,
            expected=bbox.left,
            lower=bbox.top,
            upper=bbox.bottom,
        ):
            edges.append("left")
        attached.append(
            PixelObject(bbox=bbox, fill=item.fill, edges=tuple(edges))
        )
    return attached


def _outlined_boxes(
    segments: tuple[PixelSegment, ...],
    *,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> list[PixelObject]:
    horizontal = [item for item in segments if item.orientation == "horizontal"]
    table = _axis_table(segments, "horizontal")
    vertical = _axis_table(segments, "vertical")
    boxes: list[PixelObject] = []
    # 가로 선분 짝 x 세로 선분 세 겹이라 실측 1.4MP 빗금에서 85,696,079 회였다.
    # 짝 추림과 좌우 변 확인을 배열로 옮긴다 — 조건이 정수 비교뿐이고 짝을 보는
    # 차례도 그대로라 나오는 상자와 그 순서가 같다.
    for top_index, top in enumerate(horizontal):
        # 가로 선분마다 남은 가로 선분 전부와 세로 표를 보는 회전이라 선분이 많은
        # 그림에서 이 단계가 지배한다(1400x1000 빗금 실측 6,602 선분).
        if budget is not None and budget.exhausted():
            break
        following = slice(top_index + 1, None)
        aligned = (
            (table[following, 0] - top.position >= 4)
            & (np.abs(table[following, 1] - top.start) <= 3)
            & (np.abs(table[following, 2] - top.end) <= 3)
        )
        for offset in cast(list[int], np.flatnonzero(aligned).tolist()):
            bottom = horizontal[top_index + 1 + offset]
            bbox = PixelBox(top.start, top.position, top.end, bottom.position + 1)
            left = _any_edge_match(
                vertical,
                expected=bbox.left,
                lower=bbox.top,
                upper=bbox.bottom,
            )
            right = _any_edge_match(
                vertical,
                expected=bbox.right - 1,
                lower=bbox.top,
                upper=bbox.bottom,
            )
            if left and right:
                boxes.append(
                    PixelObject(
                        bbox=bbox,
                        fill=None,
                        edges=("top", "right", "bottom", "left"),
                    )
                )
    return boxes


def detect_objects(
    canvas: PixelCanvas,
    segments: tuple[PixelSegment, ...],
    *,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> tuple[PixelObject, ...]:
    if budget is not None and budget.exhausted():
        return ()
    filled = _attach_edges(detect_filled_regions(canvas, budget=budget), segments)
    for outlined in _outlined_boxes(segments, budget=budget):
        if budget is not None and budget.exhausted():
            break
        if not any(_iou(outlined.bbox, item.bbox) >= 0.82 for item in filled):
            filled.append(outlined)
    return tuple(filled)

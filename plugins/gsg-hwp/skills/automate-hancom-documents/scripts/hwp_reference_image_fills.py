from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from hwp_reference_image_budget import ReferenceAnalysisTimeBudget
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


def _match_breaks(
    grid: NDArray[np.uint8],
    *,
    left: int,
    right: int,
    color_index: int,
) -> NDArray[np.intp]:
    """이 열 구간에서 같은 색 비율이 0.42 밑으로 떨어지는 행 번호들.

    구간을 위아래로 넓히는 회전은 한 행씩 세면서 올라간다(1240x1754 선화 실측
    3,455,482 회). 세는 값은 (왼쪽, 오른쪽, 색)에만 달렸고 시작 행과는 무관해서
    같은 삼자에 한 번만 세어 두면 된다 — 그 선화에서 구간 7,113 개가 삼자
    254 가지로 줄었다. 세는 값도 0.42 비교도 그대로다.
    """
    column = grid[:, left:right]
    matches = cast(
        NDArray[np.intp],
        np.count_nonzero(np.equal(column, color_index), axis=1),
    )
    dense = matches / max(1, right - left) >= 0.42
    return np.flatnonzero(np.logical_not(dense))


def _expand_run(
    breaks: NDArray[np.intp],
    *,
    height: int,
    run: _Run,
) -> PixelBox:
    above = int(np.searchsorted(breaks, run.y))
    top = 0 if above == 0 else breaks.item(above - 1) + 1
    below = int(np.searchsorted(breaks, run.y + 1))
    bottom = height if below == breaks.size else breaks.item(below)
    return PixelBox(run.left, top, run.right, bottom)


def _color_distance(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
) -> int:
    return max(abs(left[index] - right[index]) for index in range(3))


def _ring_strips(
    grid: NDArray[np.uint8],
    *,
    width: int,
    height: int,
    bbox: PixelBox,
) -> tuple[NDArray[np.uint8], ...]:
    """상자 둘레 2칸 띠를 네 조각으로 잘라 낸다.

    원본 회전은 바깥 상자를 전부 훑으면서 안쪽을 건너뛰었지만, 실제로 세는
    점은 둘레 띠뿐이라 넓이가 아니라 둘레에 비례한다 — 세는 집합이 같으므로
    개수도 같고, 큰 상자에서만 비용이 사라진다.
    """
    outer_top = max(0, bbox.top - 2)
    outer_bottom = min(height, bbox.bottom + 2)
    outer_left = max(0, bbox.left - 2)
    outer_right = min(width, bbox.right + 2)
    inner_top = min(max(bbox.top, outer_top), outer_bottom)
    inner_bottom = max(min(bbox.bottom, outer_bottom), outer_top)
    inner_left = min(max(bbox.left, outer_left), outer_right)
    inner_right = max(min(bbox.right, outer_right), outer_left)
    return (
        grid[outer_top:inner_top, outer_left:outer_right],
        grid[inner_bottom:outer_bottom, outer_left:outer_right],
        grid[inner_top:inner_bottom, outer_left:inner_left],
        grid[inner_top:inner_bottom, inner_right:outer_right],
    )


def _ring_differs(
    grid: NDArray[np.uint8],
    *,
    width: int,
    height: int,
    bbox: PixelBox,
    color_index: int,
) -> bool:
    strips = _ring_strips(grid, width=width, height=height, bbox=bbox)
    samples = sum(int(strip.size) for strip in strips)
    different = sum(
        int(strip.size) - int(np.count_nonzero(np.equal(strip, color_index)))
        for strip in strips
    )
    return samples > 0 and different / samples >= 0.12


def _ring_supports_nested_background(
    grid: NDArray[np.uint8],
    *,
    width: int,
    height: int,
    bbox: PixelBox,
    color_index: int,
) -> bool:
    strips = _ring_strips(grid, width=width, height=height, bbox=bbox)
    samples = sum(int(strip.size) for strip in strips)
    counts = np.zeros(256, dtype=np.int64)
    for strip in strips:
        if strip.size:
            counts += np.bincount(
                cast(NDArray[np.uint8], strip.reshape(-1)),
                minlength=256,
            )
    counts[color_index] = 0
    strongest = int(cast(np.int64, counts.max()))
    return strongest > 0 and strongest >= samples * 0.45


# 구간 메모화는 같은 (왼쪽, 오른쪽, 색) 삼자를 두 번 세지 않으려는 순수 캐시다.
# 담기는 값이 행 수만큼 자라는 배열이라 상한이 없으면 그림에 따라 계속 부푼다 —
# 실측으로 2400x3300 선화에서 항목 5,729개, 73.4MB 였다(1600x1200 잔모자이크는
# 413개 3.6MB). 넘치면 통째로 비운다: 다시 세면 같은 값이 나오므로 결과는 그대로고
# 비용은 그 뒤 몇 번의 재계산에서 그친다.
_MAXIMUM_BREAK_MEMO_BYTES = 16 * 1024 * 1024


def detect_filled_regions(
    canvas: PixelCanvas,
    *,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> list[PixelObject]:
    if budget is not None and budget.exhausted():
        # 색 양자화와 격자 만들기가 회전 앞의 한 번짜리 비용이다(8MP 실측 0.04초).
        return []
    quantized = canvas.image.quantize(
        colors=64,
        method=Image.Quantize.FASTOCTREE,
        dither=Image.Dither.NONE,
    )
    pixels = quantized.tobytes()
    grid = np.frombuffer(pixels, dtype=np.uint8).reshape(canvas.height, canvas.width)
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
    breaks_by_run: dict[tuple[int, int, int], NDArray[np.intp]] = {}
    memo_bytes = 0
    for y_position in range(0, canvas.height, 2):
        # 실측 지배 구간이다(1600x1200 잔모자이크에서 detect_objects 1.2초의
        # 대부분). 한 회전이 한 행의 구간 넓히기라 바깥 회전마다 예산을 묻는
        # 것으로 충분히 잘다.
        if budget is not None and budget.exhausted():
            break
        for run in _row_runs(
            pixels,
            width=canvas.width,
            y_position=y_position,
            minimum_length=minimum_length,
        ):
            span = (run.left, run.right, run.color_index)
            breaks = breaks_by_run.get(span)
            if breaks is None:
                breaks = _match_breaks(
                    grid,
                    left=run.left,
                    right=run.right,
                    color_index=run.color_index,
                )
                if memo_bytes + int(breaks.nbytes) > _MAXIMUM_BREAK_MEMO_BYTES:
                    breaks_by_run.clear()
                    memo_bytes = 0
                breaks_by_run[span] = breaks
                memo_bytes += int(breaks.nbytes)
            bbox = _expand_run(breaks, height=canvas.height, run=run)
            if bbox.height < minimum_height:
                continue
            if bbox.width * bbox.height >= canvas.width * canvas.height * 0.82:
                continue
            if not _ring_differs(
                grid,
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
                    grid,
                    width=canvas.width,
                    height=canvas.height,
                    bbox=bbox,
                    color_index=run.color_index,
                )
            ):
                continue
            candidates.append((bbox, run.color_index, color))
    ordered = sorted(
        candidates,
        key=lambda item: item[0].width * item[0].height,
        reverse=True,
    )
    # 남길지 판정할 때마다 이미 남긴 전부와 겹침을 재므로 후보 수에 대해
    # 제곱이다(1.1MP 모자이크 실측 21,106,850 회). 겹침은 정수 산술뿐이라
    # 배열로 한꺼번에 재도 같은 값이 나온다.
    total = len(ordered)
    boxes = np.zeros((total, 4), dtype=np.int64)
    areas = np.zeros(total, dtype=np.int64)
    colors = np.zeros((total, 3), dtype=np.int64)
    retained: list[tuple[PixelBox, int, tuple[int, int, int]]] = []
    kept = 0
    for candidate in ordered:
        if budget is not None and budget.exhausted():
            break
        bbox, _, color = candidate
        left = boxes[:kept, 0]
        top = boxes[:kept, 1]
        right = boxes[:kept, 2]
        bottom = boxes[:kept, 3]
        overlap_width = np.maximum(
            0,
            np.minimum(right, bbox.right) - np.maximum(left, bbox.left),
        )
        overlap_height = np.maximum(
            0,
            np.minimum(bottom, bbox.bottom) - np.maximum(top, bbox.top),
        )
        intersection = overlap_width * overlap_height
        candidate_area = bbox.width * bbox.height
        union = areas[:kept] + candidate_area - intersection
        overlapping = intersection / np.maximum(1, union) >= 0.88
        surrounding = (
            (left <= bbox.left)
            & (right >= bbox.right)
            & (top <= bbox.top)
            & (bottom >= bbox.bottom)
            if bbox.left <= bbox.right and bbox.top <= bbox.bottom
            else np.zeros(kept, dtype=np.bool_)
        )
        close = (
            cast(
                NDArray[np.int64],
                np.max(
                    np.abs(colors[:kept] - np.array(color, dtype=np.int64)),
                    axis=1,
                ),
            )
            <= 12
        )
        if bool((overlapping | (surrounding & close)).any()):
            continue
        boxes[kept] = (bbox.left, bbox.top, bbox.right, bbox.bottom)
        areas[kept] = candidate_area
        colors[kept] = color
        kept += 1
        retained.append(candidate)
    return [PixelObject(bbox=bbox, fill=color, edges=()) for bbox, _, color in retained]

from __future__ import annotations

import time
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from hwp_live_values import Rgb
from hwp_reference_image_budget import (
    SCAN_ESTIMATE_SAMPLE_LINES,
    ReferenceAnalysisTimeBudget,
    ReferenceScanEstimate,
    sample_positions,
)
from hwp_reference_image_edge_drawing import refine_segments_with_edge_drawing
from hwp_reference_image_pixels import PixelCanvas, PixelOrientation, PixelSegment
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


def _axis_view(canvas: PixelCanvas, orientation: str) -> NDArray[np.uint8]:
    """스캔 축을 (위치, 진행, 3) 배열로 세운다.

    가로 스캔은 y 를 고정하고 x 를 훑으면서 y 축으로 두께를 재고, 세로 스캔은
    x 를 고정하고 y 를 훑으면서 x 축으로 잰다 — 축만 맞바꾸면 같은 계산이다.
    세로 스캔은 열 접근이 되므로 한 번만 전치해 연속 배열로 복사한다(8MP에서
    24MB, 수십 ms). 전치는 값을 바꾸지 않는다.
    """
    image = canvas.rgb_array()
    if orientation == "horizontal":
        return image
    return np.ascontiguousarray(image.transpose(1, 0, 2))


def _probe_side(
    center: NDArray[np.uint8],
    array: NDArray[np.uint8],
    position: int,
    step: int,
    maximum: int,
) -> tuple[NDArray[np.int32], NDArray[np.bool_], NDArray[np.uint8]]:
    """한쪽 방향의 동색 길이·경계 대비를 한 선 전체에 대해 한꺼번에 잰다.

    스칼라 구현의 break 를 '아직 살아 있는 차선' 마스크로 옮긴 것뿐이라 각
    차선의 결과는 스칼라 회전과 같다. uint8 최대-최소로 절대차를 내므로
    자리넘침도 반올림도 없다.
    """
    span = array.shape[0]
    lanes = center.shape[0]
    count = np.zeros(lanes, dtype=np.int32)
    found = np.zeros(lanes, dtype=np.bool_)
    boundary = np.zeros(lanes, dtype=np.uint8)
    active = np.ones(lanes, dtype=np.bool_)
    for offset in range(1, maximum + 1):
        index = position + step * offset
        if not 0 <= index < span:
            break
        sample = cast(NDArray[np.uint8], array[index])
        distance = cast(
            NDArray[np.uint8],
            np.max(
                np.maximum(center, sample) - np.minimum(center, sample),
                axis=1,
            ),
        )
        differs = distance > _CENTER_COLOR_TOLERANCE
        newly = active & differs
        found |= newly
        np.copyto(boundary, distance, where=newly)
        active &= ~differs
        if not bool(active.any()):
            break
        count += active
    return count, found, boundary


def _line_stroke_widths(
    array: NDArray[np.uint8],
    position: int,
    maximum: int,
) -> NDArray[np.int32]:
    """한 후보 선 위 모든 점의 획 두께를 낸다(0 = 획 아님).

    바깥 두 칸을 0 으로 눌러 두는 것은 스칼라 루프가 margin=1 로 첫 칸을 재지
    않고 마지막 칸을 0 으로 닫아 주기 때문이다 — 그 두 칸에서 회전이 시작되면
    구간 경계가 달라진다.
    """
    center = cast(NDArray[np.uint8], array[position])
    before, before_found, before_gap = _probe_side(
        center,
        array,
        position,
        -1,
        maximum,
    )
    after, after_found, after_gap = _probe_side(
        center,
        array,
        position,
        1,
        maximum,
    )
    width = before + after + 1
    valid = (
        (width <= maximum)
        & before_found
        & after_found
        & (before_gap >= _SIDE_CONTRAST_MINIMUM)
        & (after_gap >= _SIDE_CONTRAST_MINIMUM)
    )
    widths = np.where(valid, width, np.int32(0))
    widths[0] = 0
    widths[-1] = 0
    return widths


def _line_runs(widths: NDArray[np.int32]) -> tuple[NDArray[np.intp], NDArray[np.intp]]:
    positive = np.zeros(widths.size + 2, dtype=np.int8)
    positive[1:-1] = widths > 0
    changes = cast(NDArray[np.int8], np.diff(positive))
    return np.flatnonzero(np.equal(changes, 1)), np.flatnonzero(np.equal(changes, -1))


def _scan_axis(
    array: NDArray[np.uint8],
    positions: tuple[int, ...],
    *,
    orientation: PixelOrientation,
    maximum: int,
    minimum_length: int,
    budget: ReferenceAnalysisTimeBudget | None,
) -> list[PixelSegment]:
    limit = array.shape[1]
    segments: list[PixelSegment] = []
    for position in positions:
        # 실측 지배 구간이다. 한 회전이 이미지 폭만큼의 픽셀 재측정이라 바깥
        # 회전마다 예산을 묻는 것으로 충분히 잘다 — 벡터화 뒤에도 청크 경계는
        # 후보 선 하나로 그대로 둔다.
        if budget is not None and budget.exhausted():
            break
        widths = _line_stroke_widths(array, position, maximum)
        starts, ends = _line_runs(widths)
        for start, end in zip(
            cast(list[int], starts.tolist()),
            cast(list[int], ends.tolist()),
            strict=True,
        ):
            if end - start < minimum_length:
                continue
            maximum_width = int(cast(np.int32, widths[start:end].max()))
            midpoint = start + (end - start) // 2
            segments.append(
                PixelSegment(
                    orientation=orientation,
                    start=max(0, start - maximum_width),
                    end=min(limit, end + maximum_width),
                    position=position,
                    width=maximum_width,
                    color=(
                        array.item((position, midpoint, 0)),
                        array.item((position, midpoint, 1)),
                        array.item((position, midpoint, 2)),
                    ),
                )
            )
    return segments


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


def horizontal_candidate_positions(canvas: PixelCanvas) -> tuple[int, ...]:
    return _candidate_positions(
        canvas,
        orientation="horizontal",
        minimum_length=max(12, canvas.width // 50),
    )


def vertical_candidate_positions(canvas: PixelCanvas) -> tuple[int, ...]:
    return _candidate_positions(
        canvas,
        orientation="vertical",
        minimum_length=max(8, canvas.height // 100),
    )


def _horizontal_segments(
    canvas: PixelCanvas,
    *,
    positions: tuple[int, ...] | None = None,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> list[PixelSegment]:
    return _scan_axis(
        _axis_view(canvas, "horizontal"),
        horizontal_candidate_positions(canvas) if positions is None else positions,
        orientation="horizontal",
        maximum=_maximum_stroke_width(canvas),
        minimum_length=max(12, canvas.width // 50),
        budget=budget,
    )


def _vertical_segments(
    canvas: PixelCanvas,
    *,
    positions: tuple[int, ...] | None = None,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> list[PixelSegment]:
    return _scan_axis(
        _axis_view(canvas, "vertical"),
        vertical_candidate_positions(canvas) if positions is None else positions,
        orientation="vertical",
        maximum=_maximum_stroke_width(canvas),
        minimum_length=max(8, canvas.height // 100),
        budget=budget,
    )


def estimate_scan_cost(
    canvas: PixelCanvas,
    *,
    sample_lines: int = SCAN_ESTIMATE_SAMPLE_LINES,
) -> ReferenceScanEstimate:
    """후보 선 표본을 실제 스캔 코드로 돌려 전체 스캔 비용을 외삽한다.

    정적 지표(픽셀 수·에지 비율)로는 부하를 가를 수 없다 — 같은 8MP 라도 사진은
    14.3초, 잔모자이크는 20.1초다. 그래서 이 함수는 그 이미지에서 같은 코드를
    조금 돌려 재고, 가로·세로를 따로 재서 각자의 후보 수로 선형 외삽한다
    (15개 픽스처 실측 오차 -5.3%~+8.6%, 자체 비용 0.014~0.143초).
    """
    horizontal = horizontal_candidate_positions(canvas)
    vertical = vertical_candidate_positions(canvas)
    horizontal_sample = sample_positions(horizontal, sample_lines)
    vertical_sample = sample_positions(vertical, sample_lines)
    # 축 배열 준비는 선 수와 무관한 한 번짜리 비용이라 표본 안에서 재면 배율만큼
    # 뻥튀기된다 — 8MP에서 전치 복사 0.024초가 배율 136 을 타고 3.3초의 허수가
    # 됐다. 시계 밖에서 미리 만들어 전체 스캔과 같은 조건으로 맞춘다.
    horizontal_array = _axis_view(canvas, "horizontal")
    vertical_array = _axis_view(canvas, "vertical")
    maximum = _maximum_stroke_width(canvas)
    started = time.perf_counter()
    sampled_horizontal = _scan_axis(
        horizontal_array,
        horizontal_sample,
        orientation="horizontal",
        maximum=maximum,
        minimum_length=max(12, canvas.width // 50),
        budget=None,
    )
    horizontal_seconds = time.perf_counter() - started
    resumed = time.perf_counter()
    sampled_vertical = _scan_axis(
        vertical_array,
        vertical_sample,
        orientation="vertical",
        maximum=maximum,
        minimum_length=max(8, canvas.height // 100),
        budget=None,
    )
    vertical_seconds = time.perf_counter() - resumed
    horizontal_scale = len(horizontal) / max(1, len(horizontal_sample))
    vertical_scale = len(vertical) / max(1, len(vertical_sample))
    return ReferenceScanEstimate(
        horizontal_candidates=len(horizontal),
        vertical_candidates=len(vertical),
        sampled_lines=len(horizontal_sample) + len(vertical_sample),
        sample_seconds=horizontal_seconds + vertical_seconds,
        scan_seconds=(
            horizontal_seconds * horizontal_scale + vertical_seconds * vertical_scale
        ),
        segment_count=(
            len(sampled_horizontal) * horizontal_scale
            + len(sampled_vertical) * vertical_scale
        ),
    )


def detect_visible_segments(
    canvas: PixelCanvas,
    *,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> tuple[PixelSegment, ...]:
    baseline = filter_layout_segments(
        canvas,
        _horizontal_segments(canvas, budget=budget)
        + _vertical_segments(canvas, budget=budget),
        budget=budget,
    )
    refined = refine_segments_with_edge_drawing(
        canvas,
        baseline,
        horizontal_probe=lambda x, y: _horizontal_stroke_width(canvas, x, y),
        vertical_probe=lambda x, y: _vertical_stroke_width(canvas, x, y),
        budget=budget,
    )
    return filter_layout_segments(
        canvas,
        list(refined),
        budget=budget,
    )

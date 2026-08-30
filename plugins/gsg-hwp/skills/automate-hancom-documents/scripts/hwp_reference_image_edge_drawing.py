from __future__ import annotations

from collections.abc import Callable
from math import ceil
from typing import cast

from hwp_reference_image_budget import ReferenceAnalysisTimeBudget
from hwp_reference_image_edge_support import (
    coalesce_candidates,
    has_structural_support,
)
from hwp_reference_image_pixels import PixelCanvas, PixelSegment


StrokeProbe = Callable[[int, int], int]
_MAXIMUM_AXIS_DEVIATION = 0.025


def _validated_axis_segment(
    canvas: PixelCanvas,
    values: tuple[float, float, float, float],
    horizontal_probe: StrokeProbe,
    vertical_probe: StrokeProbe,
) -> tuple[PixelSegment, int] | None:
    x1, y1, x2, y2 = values
    delta_x = abs(x2 - x1)
    delta_y = abs(y2 - y1)
    horizontal = (
        delta_x >= max(12, canvas.width // 50)
        and delta_y <= max(2.0, delta_x * _MAXIMUM_AXIS_DEVIATION)
    )
    vertical = (
        delta_y >= max(8, canvas.height // 100)
        and delta_x <= max(2.0, delta_y * _MAXIMUM_AXIS_DEVIATION)
    )
    if not horizontal and not vertical:
        return None
    orientation = "horizontal" if horizontal else "vertical"
    start_value = min(x1, x2) if horizontal else min(y1, y2)
    end_value = max(x1, x2) if horizontal else max(y1, y2)
    limit = canvas.width if horizontal else canvas.height
    start = max(4, round(start_value))
    end = min(limit - 4, round(end_value))
    if start >= end:
        return None
    cross_start = y1 if horizontal and x1 <= x2 else y2 if horizontal else x1 if y1 <= y2 else x2
    cross_end = y2 if horizontal and x1 <= x2 else y1 if horizontal else x2 if y1 <= y2 else x1
    drift = ceil(abs(cross_end - cross_start))
    search = max(3, drift + 2)
    probe = horizontal_probe if horizontal else vertical_probe
    supported: list[tuple[int, int]] = []
    for numerator in range(1, 6):
        along = start + (end - start) * numerator // 6
        ratio = (along - start) / max(1, end - start)
        predicted = round(cross_start + (cross_end - cross_start) * ratio)
        positions = range(
            max(4, predicted - search),
            min(
                (canvas.height if horizontal else canvas.width) - 4,
                predicted + search + 1,
            ),
        )
        best = max(
            ((probe(along, position) if horizontal else probe(position, along), position)
             for position in positions),
            default=(0, predicted),
        )
        if best[0] > 0:
            supported.append(best)
    if len(supported) < 3:
        return None
    positions = sorted(position for _, position in supported)
    position = positions[len(positions) // 2]
    width = max(width for width, _ in supported)
    color = (
        canvas.color((start + end) // 2, position)
        if horizontal
        else canvas.color(position, (start + end) // 2)
    )
    return (
        PixelSegment(
            orientation=orientation,
            start=start,
            end=end,
            position=position,
            width=width,
            color=color,
        ),
        drift,
    )


def _is_continuous(
    canvas: PixelCanvas,
    candidate: PixelSegment,
    drift: int,
    horizontal_probe: StrokeProbe,
    vertical_probe: StrokeProbe,
) -> bool:
    horizontal = candidate.orientation == "horizontal"
    probe = horizontal_probe if horizontal else vertical_probe
    cross_limit = canvas.height if horizontal else canvas.width
    search = max(3, drift + 2)
    missing_run = 0
    for along in range(candidate.start, candidate.end, 2):
        supported = any(
            (
                probe(along, position)
                if horizontal
                else probe(position, along)
            )
            > 0
            for position in range(
                max(4, candidate.position - search),
                min(cross_limit - 4, candidate.position + search + 1),
            )
        )
        missing_run = 0 if supported else missing_run + 2
        if missing_run > 4:
            return False
    return True


def _edge_drawing_lines(
    canvas: PixelCanvas,
) -> tuple[tuple[float, float, float, float], ...]:
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as error:
        raise ValueError(
            "reference image edge analysis requires opencv-contrib-python"
        ) from error
    if not hasattr(cv2, "ximgproc"):
        raise ValueError(
            "installed OpenCV does not provide the ximgproc edge analyzer"
        )
    image = np.frombuffer(canvas.rgb, dtype=np.uint8).reshape(
        canvas.height,
        canvas.width,
        3,
    )
    detector = cv2.ximgproc.createEdgeDrawing()
    parameters = cv2.ximgproc.EdgeDrawing.Params()
    parameters.MinLineLength = max(8, min(canvas.width, canvas.height) // 100)
    parameters.NFAValidation = True
    detector.setParams(parameters)
    detector.detectEdges(image)
    lines = cast(object, detector.detectLines())
    if lines is None:
        return ()
    rows: list[list[float]] = (
        np.asarray(lines, dtype=np.float32).reshape(-1, 4).tolist()
    )
    return tuple((row[0], row[1], row[2], row[3]) for row in rows)


def refine_segments_with_edge_drawing(
    canvas: PixelCanvas,
    baseline: tuple[PixelSegment, ...],
    *,
    horizontal_probe: StrokeProbe,
    vertical_probe: StrokeProbe,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> tuple[PixelSegment, ...]:
    if budget is not None and budget.exhausted():
        # 예산이 이미 끝났으면 선 검출 자체를 부르지 않는다. 그 호출은 쪼갤 수
        # 없는 한 번짜리라 800x600 잔모자이크에서 0.132초, 8MP에서 0.34~0.38초를
        # 통째로 쓴다 — 어차피 버릴 결과에 그만큼을 더 쓰면 예산이 그만큼 샌다.
        return baseline
    accepted: list[tuple[PixelSegment, int]] = []
    for values in _edge_drawing_lines(canvas):
        # 실측 지배 구간이다(1400x1000 빗금 3.8초, 1600x1200 잔모자이크 1.9초).
        # 한 회전이 후보 선 하나의 획 재측정이라 바깥 회전마다 묻는 것으로 충분히
        # 잘다 — 선 검출 자체는 한 번짜리 호출이라 8MP에서도 0.38초다.
        if budget is not None and budget.exhausted():
            break
        detected = _validated_axis_segment(
            canvas,
            values,
            horizontal_probe,
            vertical_probe,
        )
        if detected is None:
            continue
        candidate, drift = detected
        if (
            has_structural_support(candidate, baseline, max(4, drift + 2))
            and _is_continuous(
                canvas,
                candidate,
                drift,
                horizontal_probe,
                vertical_probe,
            )
        ):
            accepted.append(detected)
    return baseline + tuple(
        candidate
        for candidate, _ in coalesce_candidates(accepted)
    )

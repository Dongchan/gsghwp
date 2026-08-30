from __future__ import annotations

from hwp_reference_image_budget import ReferenceAnalysisTimeBudget
from hwp_reference_image_pixels import PixelCanvas, PixelSegment


def _same_line(left: PixelSegment, right: PixelSegment) -> bool:
    return (
        left.orientation == right.orientation
        and abs(left.position - right.position) <= 2
        and abs(left.start - right.start) <= 3
        and abs(left.end - right.end) <= 3
    )


def _coalesce(segments: list[PixelSegment]) -> tuple[PixelSegment, ...]:
    ordered = sorted(
        segments,
        key=lambda item: (item.orientation, item.start, item.end, item.position),
    )
    retained: list[PixelSegment] = []
    for segment in ordered:
        if not retained or not _same_line(retained[-1], segment):
            retained.append(segment)
            continue
        previous = retained[-1]
        retained[-1] = PixelSegment(
            orientation=previous.orientation,
            start=min(previous.start, segment.start),
            end=max(previous.end, segment.end),
            position=round((previous.position + segment.position) / 2),
            width=max(previous.width, segment.width),
            color=previous.color,
        )
    return tuple(retained)


_ALIGNMENT_TOLERANCE = 3


def _position_index(
    segments: list[PixelSegment],
) -> dict[int, list[PixelSegment]]:
    index: dict[int, list[PixelSegment]] = {}
    for item in segments:
        index.setdefault(item.position, []).append(item)
    return index


def _endpoint_keys(start: int, end: int) -> set[int]:
    """받침이 될 수 있는 선분의 위치값만 추린다.

    받침 조건의 두 번째 절이 '반대 방향 선분의 위치가 이 선분의 양 끝에서 3칸
    안'이라, 위치값이 그 14개 후보 밖인 선분은 볼 필요조차 없다. 집합으로
    모으므로 두 끝이 가까워 범위가 겹쳐도 한 선분을 두 번 세지 않는다.
    """
    span = _ALIGNMENT_TOLERANCE
    return set(range(start - span, start + span + 1)) | set(
        range(end - span, end + span + 1)
    )


def _has_two_supports(
    index: dict[int, list[PixelSegment]],
    keys: set[int],
    position: int,
) -> bool:
    span = _ALIGNMENT_TOLERANCE
    found = 0
    for key in keys:
        for candidate in index.get(key, ()):
            if (
                abs(candidate.start - position) <= span
                or abs(candidate.end - position) <= span
            ):
                found += 1
                if found >= 2:
                    return True
    return False


def filter_layout_segments(
    canvas: PixelCanvas,
    segments: list[PixelSegment],
    *,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> tuple[PixelSegment, ...]:
    candidates = _coalesce(segments)
    horizontal = tuple(item for item in candidates if item.orientation == "horizontal")
    vertical = tuple(item for item in candidates if item.orientation == "vertical")
    retained_horizontal = [
        item
        for item in horizontal
        if item.end - item.start >= max(30, canvas.width // 14)
    ]
    retained_vertical = [
        item
        for item in vertical
        if item.end - item.start >= max(24, canvas.height // 40)
    ]
    # 아래 두 회전은 선분 하나마다 보존된 반대 방향 선분 전부를 훑어서 선분 수에
    # 대해 제곱이었다(앞선 실측: 12k에서 1.4초, 27k에서 11.7초, 39k에서 32.3초).
    # 받침 조건의 둘째 절이 "반대 방향 선분의 위치가 이 선분의 양 끝에서 3칸 안"
    # 이라, 위치값 통에 담아 두면 한 선분이 보는 상대가 통 14개 안으로 줄어든다.
    # 세는 집합이 같으므로 결과도 같다 — 1400x1000 빗금 픽스처 실측으로 원시
    # 선분 47,840개에서 1.37초가 0.05초가 됐다. 협조 예산은 그대로 묻는다.
    horizontal_index = _position_index(retained_horizontal)
    retained_vertical_seen = set(retained_vertical)
    for item in vertical:
        if budget is not None and budget.exhausted():
            break
        if item in retained_vertical_seen or item.end - item.start < max(
            12, canvas.height // 100
        ):
            continue
        if _has_two_supports(
            horizontal_index,
            _endpoint_keys(item.start, item.end),
            item.position,
        ):
            retained_vertical.append(item)
            retained_vertical_seen.add(item)
    vertical_index = _position_index(retained_vertical)
    retained_horizontal_seen = set(retained_horizontal)
    for item in horizontal:
        if budget is not None and budget.exhausted():
            break
        if item in retained_horizontal_seen or item.end - item.start < max(
            20, canvas.width // 50
        ):
            continue
        if _has_two_supports(
            vertical_index,
            _endpoint_keys(item.start, item.end),
            item.position,
        ):
            retained_horizontal.append(item)
            retained_horizontal_seen.add(item)
    return _coalesce(retained_horizontal + retained_vertical)

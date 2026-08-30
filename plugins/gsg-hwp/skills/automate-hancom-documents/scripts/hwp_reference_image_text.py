from __future__ import annotations

from hwp_reference_image_budget import ReferenceAnalysisTimeBudget
from hwp_reference_image_pixels import PixelBox, PixelCanvas, PixelSegment
from hwp_reference_image_text_mask import PixelBarrier, build_text_mask


# _column_groups 가 한 줄 안에서 이어 붙이는 가로 공백의 최대치다. 열 띠를
# 쪼갤 고랑은 반드시 이보다 넓어야 한다 — 그렇지 않으면 정상 경로가 한 덩어리로
# 묶었을 글자 사이를 재훑기가 갈라 놓는다.
_MAXIMUM_COLUMN_GAP = 36


def _active_row_bands(
    canvas: PixelCanvas,
    mask: bytes,
    frame: PixelBox,
) -> tuple[PixelBox, ...]:
    minimum_pixels = max(2, canvas.width // 500)
    active_rows: list[int] = []
    for y_position in range(frame.top, frame.bottom):
        start = y_position * canvas.width + frame.left
        end = y_position * canvas.width + frame.right
        if mask[start:end].count(255) >= minimum_pixels:
            active_rows.append(y_position)
    bands: list[PixelBox] = []
    start: int | None = None
    previous = -1
    for y_position in (*active_rows, frame.bottom + 3):
        if start is None:
            start = y_position
            previous = y_position
            continue
        if y_position - previous <= 2:
            previous = y_position
            continue
        bands.append(PixelBox(frame.left, start, frame.right, previous + 1))
        start = y_position if y_position < frame.bottom else None
        previous = y_position
    return tuple(bands)


def _crosses_barrier(
    previous: int,
    current: int,
    band: PixelBox,
    barriers: tuple[PixelBarrier, ...],
) -> bool:
    return any(
        previous < item.column < current
        and item.top < band.bottom
        and item.bottom > band.top
        for item in barriers
    )


def _column_groups(
    canvas: PixelCanvas,
    mask: bytes,
    band: PixelBox,
    barriers: tuple[PixelBarrier, ...],
) -> tuple[PixelBox, ...]:
    active_columns: list[int] = []
    for x_position in range(band.left, band.right):
        if any(
            mask[y_position * canvas.width + x_position] == 255
            for y_position in range(band.top, band.bottom)
        ):
            active_columns.append(x_position)
    maximum_gap = max(4, min(_MAXIMUM_COLUMN_GAP, band.height * 2))
    groups: list[PixelBox] = []
    start: int | None = None
    previous = -1
    for x_position in (*active_columns, band.right + maximum_gap + 1):
        if start is None:
            start = x_position
            previous = x_position
            continue
        if (
            x_position - previous <= maximum_gap
            and not _crosses_barrier(previous, x_position, band, barriers)
        ):
            previous = x_position
            continue
        groups.append(PixelBox(start, band.top, previous + 1, band.bottom))
        start = x_position if x_position < band.right else None
        previous = x_position
    return tuple(groups)


def _is_line_or_edge(bbox: PixelBox) -> bool:
    return (
        bbox.height <= 4
        and bbox.width >= bbox.height * 8
        or bbox.width <= 4
        and bbox.height >= bbox.width * 8
    )


def _padded(canvas: PixelCanvas, bbox: PixelBox) -> PixelBox:
    x_padding = max(2, bbox.height // 3)
    y_padding = max(2, bbox.height // 5)
    return PixelBox(
        max(0, bbox.left - x_padding),
        max(0, bbox.top - y_padding),
        min(canvas.width, bbox.right + x_padding),
        min(canvas.height, bbox.bottom + y_padding),
    )


def _column_strips(
    canvas: PixelCanvas,
    mask: bytes,
    band: PixelBox,
) -> tuple[PixelBox, ...]:
    """띠를 세로 고랑에서 갈라 열별 조각으로 낸다.

    고랑의 최소 너비는 두 관측 사이에서 고른다 — 실사례(2613×1488)에서 왼쪽
    상자와 오른쪽 단 사이의 판 고랑은 246px 였고, 한 캡션 안에서 가장 넓게
    벌어진 글자 사이는 45px 였다. 아래 값(2613px 에서 81px)은 그 창 안에 들고
    한 줄 잇기의 상한(_MAXIMUM_COLUMN_GAP)보다 확실히 넓다. 실측으로 16~40
    분의 1(163~65px)은 모두 같은 결과를 냈다: 8분의 1(326px)은 너무 성겨
    되찾는 것이 없었고, 64분의 1(40px)은 실사례에서 +14 로 늘었다.
    """
    minimum_gutter = max(_MAXIMUM_COLUMN_GAP + 1, canvas.width // 32)
    strips: list[PixelBox] = []
    left: int | None = None
    right = band.left
    quiet = 0
    for x_position in range(band.left, band.right):
        if any(
            mask[y_position * canvas.width + x_position] == 255
            for y_position in range(band.top, band.bottom)
        ):
            if left is None:
                left = x_position
            right = x_position + 1
            quiet = 0
            continue
        quiet += 1
        if left is not None and quiet >= minimum_gutter:
            strips.append(PixelBox(left, band.top, right, band.bottom))
            left = None
    if left is not None:
        strips.append(PixelBox(left, band.top, right, band.bottom))
    return tuple(strips)


def _band_regions(
    canvas: PixelCanvas,
    mask: bytes,
    band: PixelBox,
    barriers: tuple[PixelBarrier, ...],
) -> tuple[PixelBox, ...]:
    regions: list[PixelBox] = []
    for bbox in _column_groups(canvas, mask, band, barriers):
        if bbox.width < 3 or bbox.height < 3 or _is_line_or_edge(bbox):
            continue
        padded = _padded(canvas, bbox)
        if canvas.contrast_ratio(padded) >= 0.006:
            regions.append(padded)
    return tuple(regions)


def _rescanned_regions(
    canvas: PixelCanvas,
    mask: bytes,
    band: PixelBox,
    barriers: tuple[PixelBarrier, ...],
    limit: int,
    budget: ReferenceAnalysisTimeBudget | None,
) -> tuple[PixelBox, ...]:
    """너무 높은 띠를 버리는 대신 열 조각별로 다시 훑는다.

    행 띠는 지면 전폭에서 잡히므로, 여러 단으로 나뉜 지면에서는 한쪽 단의 한
    줄이 다른 단의 연속된 내용과 같은 행을 공유한다는 이유만으로 붙는다.
    실사례(2613×1488)에서 왼쪽 상자의 캡션 줄(592~616)이 오른쪽 단의 564~724
    행에 붙어 h=161 짜리 띠가 되었고, 높이 상한 148에 걸려 띠 전체가 버려져
    캡션 4개가 통째로 사라졌다. 고랑에서 갈라 조각마다 행 띠를 다시 잡으면
    그 줄은 h=25 로 돌아온다. 조각 안에서도 여전히 상한을 넘는 띠 — 진짜로
    빽빽한 그림 영역 — 는 예전처럼 버린다.
    """
    regions: list[PixelBox] = []
    for strip in _column_strips(canvas, mask, band):
        if budget is not None and budget.exhausted():
            break
        for sub_band in _active_row_bands(canvas, mask, strip):
            if sub_band.height > limit:
                continue
            regions.extend(_band_regions(canvas, mask, sub_band, barriers))
    return tuple(regions)


def detect_text_regions(
    canvas: PixelCanvas,
    segments: tuple[PixelSegment, ...],
    *,
    budget: ReferenceAnalysisTimeBudget | None = None,
) -> tuple[PixelBox, ...]:
    if budget is not None and budget.exhausted():
        # 마스크 만들기가 이 단계의 최대 비분할 구간이다(8MP 실측 0.49~0.68초).
        # 예산이 이미 끝났으면 그것부터 부르지 않는다.
        return ()
    mask, barriers = build_text_mask(canvas, segments)
    limit = max(90, canvas.height // 10)
    regions: list[PixelBox] = []
    frame = PixelBox(0, 0, canvas.width, canvas.height)
    for band in _active_row_bands(canvas, mask, frame):
        # 띠마다 열 묶음을 세로로 훑는 회전이 지배한다. 마스크 만들기 자체는 한
        # 번짜리라 8MP에서 0.49~0.68초이고, 그것이 이 단계의 최대 비분할 구간이다.
        if budget is not None and budget.exhausted():
            break
        if band.height > limit:
            regions.extend(
                _rescanned_regions(canvas, mask, band, barriers, limit, budget)
            )
            continue
        regions.extend(_band_regions(canvas, mask, band, barriers))
    return tuple(regions)

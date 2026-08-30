from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# 한/글 그림 개체의 자르기(SkipLeft/SkipTop/SkipRight/SkipBottom)는 길이를
# OriginalSizeX/OriginalSizeY 와 같은 좌표계로 센다. 실측(240x180 px 그림):
# OriginalSizeX=18000, OriginalSizeY=13500 -- 픽셀당 75 HWPUNIT, 96 DPI.
# 그래서 "왼쪽에서 얼마나 감출까"는 비율만 알면 정해진다:
#     Skip = 비율 x OriginalSize
# 도구가 비율을 받는 이유가 이것이다. 모델이 원본 픽셀 수를 알 필요가 없고,
# mm/HWPUNIT/픽셀을 헷갈릴 자리가 아예 없어진다.
#
# 자르기는 그림 상자(Width/Height)를 건드리지 않는다. 실측에서 SkipRight 를
# 원본의 절반으로 준 뒤에도 Width/Height 는 22500x16875 그대로였고, 남은 절반이
# 그 상자를 채웠다. 즉 "그 영역으로 확대"가 된다.
_MINIMUM_VISIBLE_HWPUNIT: Final = 1


@dataclass(frozen=True, slots=True)
class PictureOriginalSize:
    """그림 개체가 들고 있는 원본 크기. ShapeDrawImageAttr 에서 읽는다."""

    width_hwpunit: int
    height_hwpunit: int


@dataclass(frozen=True, slots=True)
class PictureSkip:
    """한/글 자르기 값. 네 변에서 감출 길이(HWPUNIT)."""

    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @property
    def any_crop(self) -> bool:
        return bool(self.left or self.top or self.right or self.bottom)


@dataclass(frozen=True, slots=True)
class PictureCropRatios:
    """네 변에서 감출 비율. 0.0 은 그대로, 0.5 는 그 변에서 절반을 감춘다."""

    left: float = 0.0
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0

    @property
    def any_crop(self) -> bool:
        return bool(self.left or self.top or self.right or self.bottom)


class PictureCropError(ValueError):
    pass


def _axis_skips(
    near: float,
    far: float,
    original: int,
    axis: str,
) -> tuple[int, int]:
    if original <= 0:
        raise PictureCropError(
            f"그림 원본 {axis} 크기가 0 이하라 자르기 길이를 계산할 수 없습니다"
        )
    near_skip = int(round(near * original))
    far_skip = int(round(far * original))
    visible = original - near_skip - far_skip
    if visible >= _MINIMUM_VISIBLE_HWPUNIT:
        return near_skip, far_skip
    # 반올림이 원본을 통째로 먹어 치우는 경계다. 비율 검사는 이미 통과한
    # 요청이므로 막지 않고, 보이는 부분이 최소 한 칸은 남도록 먼 쪽을 줄인다.
    far_skip = max(0, original - near_skip - _MINIMUM_VISIBLE_HWPUNIT)
    if original - near_skip - far_skip < _MINIMUM_VISIBLE_HWPUNIT:
        near_skip = max(0, original - _MINIMUM_VISIBLE_HWPUNIT)
        far_skip = 0
    return near_skip, far_skip


def skips_from_ratios(
    ratios: PictureCropRatios,
    original: PictureOriginalSize,
) -> PictureSkip:
    """비율 자르기를 한/글이 쓰는 HWPUNIT 길이로 바꾼다."""
    left, right = _axis_skips(ratios.left, ratios.right, original.width_hwpunit, "가로")
    top, bottom = _axis_skips(
        ratios.top, ratios.bottom, original.height_hwpunit, "세로"
    )
    return PictureSkip(left=left, top=top, right=right, bottom=bottom)


def ratios_from_skips(
    skip: PictureSkip,
    original: PictureOriginalSize,
) -> PictureCropRatios:
    """한/글에서 되읽은 자르기 길이를 비율로 되돌린다 (응답 readback 용)."""
    if original.width_hwpunit <= 0 or original.height_hwpunit <= 0:
        return PictureCropRatios()
    return PictureCropRatios(
        left=skip.left / original.width_hwpunit,
        top=skip.top / original.height_hwpunit,
        right=skip.right / original.width_hwpunit,
        bottom=skip.bottom / original.height_hwpunit,
    )


def visible_fraction(
    skip: PictureSkip, original: PictureOriginalSize
) -> tuple[float, float]:
    """자르고 남은 가로·세로가 원본의 몇 분의 몇인지."""
    if original.width_hwpunit <= 0 or original.height_hwpunit <= 0:
        return 0.0, 0.0
    return (
        (original.width_hwpunit - skip.left - skip.right) / original.width_hwpunit,
        (original.height_hwpunit - skip.top - skip.bottom) / original.height_hwpunit,
    )

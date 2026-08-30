from __future__ import annotations

from pydantic import Field

from hwp_live_values import ContractModel
from hwp_picture_edit_geometry import (
    PictureCropRatios,
    PictureOriginalSize,
    PictureSkip,
    ratios_from_skips,
)


class PictureCropReadback(ContractModel):
    """한/글에서 되읽은 자르기. 비율과 HWPUNIT 을 함께 싣는다.

    모델이 다음 판단을 하려면 둘 다 필요하다. 비율은 "얼마나 잘렸나"를 바로
    말해 주고, HWPUNIT 은 한/글 대화상자에서 보이는 값과 같은 수라 사람이
    대조할 수 있다.
    """

    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)
    skip_left_hwpunit: int = Field(ge=0)
    skip_top_hwpunit: int = Field(ge=0)
    skip_right_hwpunit: int = Field(ge=0)
    skip_bottom_hwpunit: int = Field(ge=0)

    @property
    def any_crop(self) -> bool:
        return bool(
            self.skip_left_hwpunit
            or self.skip_top_hwpunit
            or self.skip_right_hwpunit
            or self.skip_bottom_hwpunit
        )


def crop_readback(
    skip: PictureSkip, original: PictureOriginalSize
) -> PictureCropReadback:
    ratios: PictureCropRatios = ratios_from_skips(skip, original)
    return PictureCropReadback(
        left=ratios.left,
        top=ratios.top,
        right=ratios.right,
        bottom=ratios.bottom,
        skip_left_hwpunit=skip.left,
        skip_top_hwpunit=skip.top,
        skip_right_hwpunit=skip.right,
        skip_bottom_hwpunit=skip.bottom,
    )


class PictureGeometry(ContractModel):
    """그림 한 장의 자리와 크기. 편집 전후를 같은 모양으로 싣는다."""

    picture_id: str = Field(min_length=1, max_length=100)
    table_id: str | None = Field(default=None, max_length=100)
    cell: str | None = Field(default=None, max_length=20)
    # 원본 파일의 크기다. 상자 크기가 아니다. 자르기 비율은 이 값에 곱해진다.
    original_width_hwpunit: int = Field(ge=0)
    original_height_hwpunit: int = Field(ge=0)
    # 문서에 놓인 상자 크기. 자르기는 이 값을 바꾸지 않는다 -- 남은 부분이
    # 같은 상자를 채우므로 결과가 "그 영역으로 확대"가 된다.
    box_width_hwpunit: int | None = Field(default=None, ge=0)
    box_height_hwpunit: int | None = Field(default=None, ge=0)
    crop: PictureCropReadback


class PictureEditEvidence(ContractModel):
    """hwp_edit_picture 한 번의 결과를 그대로 되읽은 것.

    OperationResult 에는 스키마·직렬화 양쪽에서 빠진 채로 실린다
    (SkipJsonSchema + exclude). 봉인된 레거시 도구들의 outputSchema 를 건드리지
    않으면서 새 도구까지 근거를 나르기 위한 통로다 -- changed_pages 와 같은 수법.
    """

    before: PictureGeometry
    after: PictureGeometry
    moved: bool = False
    cropped: bool = False
    # 이사 직전에 목적지 셀에 이미 들어 있던 그림들. 막지 않는다. 겹쳐 놓을지
    # 다른 칸을 고를지는 모델이 정한다.
    destination_occupants: tuple[str, ...] = Field(default=(), max_length=50)
    notices: tuple[str, ...] = Field(default=(), max_length=20)

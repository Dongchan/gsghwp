from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from hwp_live_values import ContractModel
from hwp_picture_edit_evidence import PictureEditEvidence
from hwp_public_contract import PublicActionResult


type PublicCropRatio = Annotated[float, Field(ge=0, lt=1)]
type PublicPictureTargetId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        description=(
            "편집할 그림의 instance_id. hwp_inspect_page_fast가 반환한 값이나 앞선"
            " 후보 target_id를 그대로 씁니다. 생략하면 table_id와 cell로 찾습니다."
        ),
    ),
]
type PublicPictureTableId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        description="hwp_inspect_page_fast의 parent_table_instance_id 또는 표의 instance_id.",
    ),
]
type PublicPicturePage = Annotated[
    int,
    Field(
        ge=1,
        description="그림이 있는 쪽. 생략하면 현재 커서 쪽에서 찾습니다.",
    ),
]


class PublicPictureCrop(ContractModel):
    """한/글 자르기. 네 변에서 감출 몫을 0.0~1.0 비율로 받는다.

    비율로 받는 이유는 하나다. 그림의 원본 픽셀 수를 모델이 알 필요가 없게
    하려는 것이다. 0.25 는 "그 변에서 4분의 1을 감춰라"이고, 도구가 문서에서
    읽은 원본 크기에 곱해 한/글 단위로 바꾼다.

    네 값은 절대값이다. 준 대로가 결과가 되고, 빠뜨린 변은 0 -- 자르지 않음 --
    이 된다. 그래서 같은 요청을 두 번 보내도 결과가 같고, 되돌리려면 아무 값도
    주지 않거나 네 값을 0으로 주면 된다.

    자르기는 그림 상자 크기를 바꾸지 않는다. 남은 부분이 원래 상자를 그대로
    채우므로, 결과는 "그 영역으로 확대"가 된다.
    """

    left: PublicCropRatio = Field(default=0.0, description="왼쪽에서 감출 비율.")
    top: PublicCropRatio = Field(default=0.0, description="위쪽에서 감출 비율.")
    right: PublicCropRatio = Field(default=0.0, description="오른쪽에서 감출 비율.")
    bottom: PublicCropRatio = Field(default=0.0, description="아래쪽에서 감출 비율.")

    @model_validator(mode="after")
    def require_visible_remainder(self) -> PublicPictureCrop:
        if self.left + self.right >= 1:
            raise PydanticCustomError(
                "public_picture_crop_width",
                "left와 right를 합치면 1 미만이어야 가로로 남는 부분이 있습니다",
            )
        if self.top + self.bottom >= 1:
            raise PydanticCustomError(
                "public_picture_crop_height",
                "top과 bottom을 합치면 1 미만이어야 세로로 남는 부분이 있습니다",
            )
        return self

    @property
    def any_crop(self) -> bool:
        return bool(self.left or self.top or self.right or self.bottom)


class PublicPictureEditResult(PublicActionResult):
    """hwp_edit_picture 의 응답.

    PublicActionResult 를 물려받아 공개 도구 계약(succeeded 만이 성공,
    operation_id 멱등성, affected_pages·state_token 재사용)을 그대로 지키고,
    그림 편집에만 필요한 되읽기를 ``picture`` 에 하나 더 싣는다. 물려받은
    쪽에만 필드가 붙으므로 PublicActionResult 를 쓰는 다른 도구들의 응답
    스키마는 한 글자도 바뀌지 않는다.
    """

    picture: PictureEditEvidence | None = Field(
        default=None,
        description=(
            "편집 전후의 그림 상태를 문서에서 그대로 되읽은 값. 원본 크기,"
            " 적용된 자르기(비율과 HWPUNIT), 편집 뒤의 개체 ID가 들어 있습니다."
        ),
    )


_PICTURE_RESULT_FIELDS: Final = frozenset(PublicActionResult.model_fields)


def to_public_picture_edit_result(
    base: PublicActionResult,
    evidence: PictureEditEvidence | None,
) -> PublicPictureEditResult:
    """표준 공개 응답에 그림 되읽기를 얹는다."""
    return PublicPictureEditResult(
        **{name: getattr(base, name) for name in _PICTURE_RESULT_FIELDS},
        picture=evidence,
    )

from __future__ import annotations

from pathlib import Path
from typing import Annotated, assert_never

from pydantic import Field, JsonValue, field_validator, model_validator
from pydantic_core import PydanticCustomError
from typing_extensions import TypeIs

from hwp_live_values import ContractModel


type PublicCellAddress = Annotated[
    str,
    Field(min_length=2, max_length=20, pattern=r"^[A-Za-z]+[1-9][0-9]*$"),
]


class PublicCellSelector(ContractModel):
    address: str | None = Field(
        default=None,
        pattern=r"^[A-Z]+[1-9][0-9]*$",
        description="기준 셀 주소. label과 동시에 사용할 수 없습니다.",
    )
    label: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
        description="기준 셀의 현재 텍스트. 같은 텍스트가 반복되면 occurrence를 사용합니다.",
    )
    occurrence: int = Field(
        default=1,
        ge=1,
        le=20_000,
        description="문서 읽기 순서에서 label이 나타나는 순번입니다.",
    )
    row_offset: int = Field(
        default=0,
        ge=-65_535,
        le=65_535,
        description="기준 셀에서 실제 대상 셀까지의 행 이동량입니다.",
    )
    column_offset: int = Field(
        default=0,
        ge=-16_383,
        le=16_383,
        description="기준 셀에서 실제 대상 셀까지의 열 이동량입니다.",
    )

    @field_validator("address", mode="before")
    @classmethod
    def normalize_address(cls, value: JsonValue) -> JsonValue:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_one_anchor(self) -> PublicCellSelector:
        if (self.address is None) == (self.label is None):
            raise PydanticCustomError(
                "public_cell_selector_anchor",
                "address와 label 중 정확히 하나를 전달하세요",
            )
        if self.address is not None and self.occurrence != 1:
            raise PydanticCustomError(
                "public_cell_selector_occurrence",
                "occurrence는 label 선택에만 사용할 수 있습니다",
            )
        if self.label is not None and not any(
            character.isalnum() for character in self.label
        ):
            raise PydanticCustomError(
                "public_cell_selector_label",
                "label에는 검색할 문자나 숫자가 필요합니다",
            )
        return self


type PublicCellReference = PublicCellAddress | PublicCellSelector


def _is_public_cell_selector(
    reference: PublicCellReference,
) -> TypeIs[PublicCellSelector]:
    return isinstance(reference, PublicCellSelector)


def to_public_cell_selector(reference: PublicCellReference) -> PublicCellSelector:
    match reference:
        case str() as address:
            return PublicCellSelector(address=address)
        case _ as unreachable if not _is_public_cell_selector(unreachable):
            assert_never(unreachable)
        case _:
            return reference


class SeriesTextCell(PublicCellSelector):
    value: str = Field(max_length=200_000)


class SeriesImageCell(PublicCellSelector):
    path: Path

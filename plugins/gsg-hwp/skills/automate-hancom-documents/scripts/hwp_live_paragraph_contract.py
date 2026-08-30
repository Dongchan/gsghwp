from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from hwp_color_normalization import ColorInput
from hwp_live_values import (
    Alignment,
    ContractModel,
    PlanLeadMm,
    PlanTrailMm,
    reject_plan_lead_input,
)


class ParagraphRun(ContractModel):
    text: str = Field(min_length=1, max_length=50_000)
    bold: bool | None = None
    font_name: str | None = Field(default=None, max_length=100)
    font_size_pt: float | None = Field(default=None, ge=1, le=96)
    text_color: ColorInput | None = None


class ParagraphBlock(ContractModel):
    kind: Literal["paragraph"]
    text: str = Field(min_length=1, max_length=50_000)
    runs: tuple[ParagraphRun, ...] = Field(default=(), max_length=100)
    style_id: int | None = Field(default=None, ge=0, le=4095)
    style_name: str | None = Field(default=None, min_length=1, max_length=100)
    style_role: Literal[
        "auto",
        "body",
        "heading",
        "table_title",
        "figure_title",
    ] = "auto"
    preserve_source_text: bool = Field(
        default=False,
        description="Do not normalize a source paragraph's literal bullets, spacing, typos, or units.",
    )
    bold: bool | None = None
    font_name: str | None = Field(default=None, max_length=100)
    font_size_pt: float | None = Field(default=None, ge=1, le=96)
    text_color: ColorInput | None = None
    alignment: Alignment = "inherit"
    align_type_raw: int | None = Field(
        default=None,
        ge=0,
        le=255,
        description="Observed ParaShape/AlignType raw value copied from the local HWP paragraph; avoids translating through hardcoded alignment names.",
    )
    line_spacing_percent: int | None = Field(default=None, ge=50, le=500)
    space_before_mm: float | None = Field(default=None, ge=0, le=100)
    space_after_mm: float | None = Field(default=None, ge=0, le=100)
    plan_lead_mm: PlanLeadMm = None
    plan_trail_mm: PlanTrailMm = None
    left_margin_mm: float | None = Field(default=None, ge=0, le=100)
    right_margin_mm: float | None = Field(default=None, ge=0, le=100)
    indentation_mm: float | None = Field(default=None, ge=-100, le=100)
    heading_type: int | None = Field(
        default=None,
        ge=0,
        le=3,
        description=(
            "문단 머리 모양 (ParaShape/HeadingType): 0 없음, 1 개요, 2 번호, "
            "3 불릿. 0이 아니면 한/글이 번호나 글머리표를 스스로 그리므로 "
            "text에 마커를 다시 쓰면 두 번 나옵니다."
        ),
    )
    heading_level: int | None = Field(
        default=None,
        ge=0,
        le=6,
        description="문단 번호·개요 단계 (ParaShape/Level, 0~6).",
    )

    @model_validator(mode="before")
    @classmethod
    def refuse_plan_lead_input(cls, data: object) -> object:
        return reject_plan_lead_input(data)

    @model_validator(mode="after")
    def validate_style(self) -> ParagraphBlock:
        if self.runs and "".join(run.text for run in self.runs) != self.text:
            raise ValueError("paragraph runs must concatenate to paragraph text")
        if self.style_id is not None and self.style_name is not None:
            raise ValueError("paragraph style name and id are mutually exclusive")
        return self

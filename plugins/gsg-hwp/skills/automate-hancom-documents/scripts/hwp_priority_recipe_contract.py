from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from hwp_live_template_repeat import TableTemplateRepeatPlan
from hwp_live_values import ContractModel


class PriorityRecipeValidationError(ValueError):
    pass


class RecipePosition(ContractModel):
    list_id: int = Field(ge=0)
    paragraph: int = Field(ge=0)
    character: int = Field(ge=0)


class HwpPriorityRecipeInputs(ContractModel):
    table_template: TableTemplateRepeatPlan | None = None
    reconcile_existing: bool = False
    caption_text: str | None = Field(default=None, min_length=1, max_length=2_000)
    caption_style_id: int = Field(default=0, ge=0, le=4_095)
    source_position: RecipePosition | None = None
    target_position: RecipePosition | None = None
    style_copy_type: Literal[0, 1, 2, 3, 4] = 2
    style_id: int | None = Field(default=None, ge=0, le=4_095)
    picture_width_mm: float | None = Field(default=None, ge=1, le=1_000)
    picture_height_mm: float | None = Field(default=None, ge=1, le=1_000)
    picture_embed: bool = True

    @model_validator(mode="after")
    def validate_picture_size(self) -> HwpPriorityRecipeInputs:
        if (self.picture_width_mm is None) != (self.picture_height_mm is None):
            raise PriorityRecipeValidationError(
                "picture width and height must be provided together"
            )
        return self

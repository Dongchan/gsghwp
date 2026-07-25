from __future__ import annotations

from typing import Literal

from pydantic import Field

from hwp_live_contract import LayoutPlan
from hwp_live_values import ContractModel
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateData,
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateRecovery,
    HwpOperateTarget,
    OperationInputValue,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs


ProductionHwpRequestedOperation = Literal[
    "document.inspect_structure",
    "document.append_layout",
    "document.insert_layout",
    "document.save",
    "document.save_reopen_verify",
    "text.format",
    "table.fill_existing",
    "table.expand_and_fill",
    "table.format",
    "table.merge_cells",
    "table.split_cells",
    "table.repeat_template",
    "table.insert_images",
    "table.build_series",
    "table.fill_with_images",
    "image.insert",
    "image.replace",
    "image.insert_or_replace",
    "caption.add",
    "caption.add_or_update",
    "style.apply",
    "style.copy",
    "style.copy_and_apply",
    "page.append_from_template",
]


class ProductionHwpOperateInputs(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    document: str | None = Field(default=None, max_length=32_767)
    operation: ProductionHwpRequestedOperation | None = Field(
        default=None,
        description="Authoritative canonical operation. When omitted, only an exact deterministic route may execute a certified recipe.",
    )
    target: HwpOperateTarget | None = None
    data: HwpOperateData | None = None
    assets: HwpOperateAssets | None = None
    policy: HwpOperatePolicy = Field(default_factory=HwpOperatePolicy)
    postconditions: HwpOperatePostconditions = Field(
        default_factory=HwpOperatePostconditions
    )
    parameters: dict[str, OperationInputValue] = Field(
        default_factory=dict,
        max_length=500,
    )
    layout: LayoutPlan | None = None
    recipe: HwpPriorityRecipeInputs | None = None
    use_defaults: bool = False
    recovery: HwpOperateRecovery | None = None

    def to_canonical_inputs(self) -> HwpOperateInputs:
        return HwpOperateInputs.model_validate(self.model_dump(mode="python"))

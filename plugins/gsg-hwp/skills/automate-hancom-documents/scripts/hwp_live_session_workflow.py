from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hwp_live_contract import LayoutPlan
from hwp_live_native_action_models import NativePageInspection
from hwp_operation_certification import certified_recipe
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationInputValue,
    OperationResult,
    OperationRoutingContext,
    OperationStatus,
    WorkflowResolution,
)
from hwp_operation_registry import operation_registry
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs


WORKFLOW_REQUIRED_INPUTS: Mapping[HwpWorkflowId, tuple[str, ...]] = {
    "document.append_layout": ("inputs.layout",),
    "document.insert_page": ("inputs.target", "inputs.layout"),
    "document.delete_page": ("inputs.target",),
    "control.delete": ("inputs.target",),
    "document.rebuild": ("inputs.layout",),
    "document.replace_selection": ("inputs.target", "inputs.data"),
    "text.insert": ("inputs.target", "inputs.data"),
    "text.replace": ("inputs.target", "inputs.data"),
    "text.format": ("inputs.target", "inputs.parameters"),
    "table.inspect": ("inputs.target",),
    "table.create": ("inputs.target", "inputs.layout"),
    "table.fill_existing": ("inputs.target", "inputs.data"),
    "table.expand_and_fill": ("inputs.target", "inputs.data"),
    "table.format": ("inputs.target", "inputs.parameters"),
    "table.merge_cells": ("inputs.target", "inputs.parameters"),
    "table.split_cells": ("inputs.target", "inputs.parameters"),
    "table.resize": ("inputs.target", "inputs.data"),
    "table.repeat_template": ("inputs.recipe",),
    "table.propagate": ("inputs.target", "inputs.data"),
    "table.import_data": ("inputs.target", "inputs.data"),
    "table.insert_images": ("inputs.target", "inputs.assets"),
    "table.build_series": ("inputs.recipe",),
    "image.insert": ("inputs.assets",),
    "image.replace": ("inputs.target", "inputs.assets"),
    "image.resize": ("inputs.target", "inputs.data"),
    "caption.add": ("inputs.target", "inputs.recipe"),
    "style.apply": ("inputs.recipe",),
    "style.copy": ("inputs.recipe",),
}

_WORKFLOW_TARGET_KINDS: Mapping[HwpWorkflowId, frozenset[str]] = {
    "document.delete_page": frozenset(("page",)),
    "control.delete": frozenset(("control",)),
    "text.format": frozenset(("selection",)),
    "table.fill_existing": frozenset(("table",)),
    "table.expand_and_fill": frozenset(("table",)),
    "table.format": frozenset(("table",)),
    "table.merge_cells": frozenset(("table",)),
    "table.split_cells": frozenset(("table",)),
    "table.repeat_template": frozenset(("table",)),
    "table.insert_images": frozenset(("table",)),
    "table.build_series": frozenset(("table",)),
    "image.replace": frozenset(("picture",)),
    "caption.add": frozenset(("table", "control")),
}

@dataclass(frozen=True, slots=True)
class WorkflowPreflightInputs:
    target: HwpOperateTarget | None
    data: HwpOperateData | None
    assets: HwpOperateAssets | None
    parameters: Mapping[str, OperationInputValue]
    layout: LayoutPlan | None
    recipe: HwpPriorityRecipeInputs | None


def routing_context_summary(
    page: NativePageInspection,
    elapsed_microseconds: int,
) -> OperationRoutingContext:
    table_instance_ids = tuple(
        control.instance_id
        for control in page.controls
        if control.control_type == "tbl"
    )
    return OperationRoutingContext(
        document_id=page.document_id,
        full_name=page.full_name,
        page=page.page,
        page_count=page.page_count,
        text_characters=len(page.text),
        control_count=len(page.controls),
        table_count=len(table_instance_ids),
        picture_count=sum(
            control.control_type in {"gso", "pic", "picture"}
            for control in page.controls
        ),
        table_instance_ids=table_instance_ids,
        native_elapsed_microseconds=elapsed_microseconds,
    )


def workflow_result(
    resolution: WorkflowResolution,
    status: OperationStatus,
    message: str,
    *,
    required_inputs: tuple[str, ...] = (),
) -> OperationResult:
    recipe = (
        None
        if resolution.workflow_id is None
        else certified_recipe(resolution.workflow_id)
    )
    return OperationResult(
        status=status,
        query=resolution.query,
        registry_entries=operation_registry().count,
        lookup_microseconds=resolution.lookup_microseconds,
        workflow_candidates=resolution.candidates,
        required_inputs=required_inputs,
        message=message,
        recipe_id=None if recipe is None else f"recipe:{recipe.recipe_id}",
        recipe_steps=resolution.steps,
    )


def explicit_workflow_preflight(
    resolution: WorkflowResolution,
    inputs: WorkflowPreflightInputs,
) -> OperationResult | None:
    workflow = resolution.workflow_id
    if workflow is None:
        return None
    allowed_target_kinds = _WORKFLOW_TARGET_KINDS.get(workflow)
    if (
        inputs.target is not None
        and allowed_target_kinds is not None
        and inputs.target.kind not in allowed_target_kinds
    ):
        expected = ", ".join(sorted(allowed_target_kinds))
        return workflow_result(
            resolution,
            "schema_conflict",
            f"{workflow} operation의 target.kind는 {expected}이어야 합니다",
        )
    available = {
        "inputs.target": inputs.target is not None,
        "inputs.data": inputs.data is not None
        and bool(inputs.data.cells or inputs.data.rows or inputs.data.records),
        "inputs.assets": inputs.assets is not None and bool(inputs.assets.images),
        "inputs.parameters": bool(inputs.parameters),
        "inputs.layout": inputs.layout is not None,
        "inputs.recipe": inputs.recipe is not None,
    }
    missing = tuple(
        field
        for field in WORKFLOW_REQUIRED_INPUTS.get(workflow, ())
        if not available[field]
    )
    if not missing:
        return None
    return workflow_result(
        resolution,
        "needs_input",
        "명시한 operation에 필요한 구조화 입력을 전달하세요",
        required_inputs=missing,
    )


def workflow_policy(
    resolution: WorkflowResolution,
    policy: HwpOperatePolicy | None,
    postconditions: HwpOperatePostconditions | None,
) -> tuple[HwpOperatePolicy, HwpOperatePostconditions, OperationResult | None]:
    effective_policy = HwpOperatePolicy() if policy is None else policy
    effective_postconditions = (
        HwpOperatePostconditions() if postconditions is None else postconditions
    )
    if (
        effective_policy.allow_row_expansion
        and resolution.workflow_id != "table.expand_and_fill"
    ):
        return effective_policy, effective_postconditions, workflow_result(
            resolution,
            "schema_conflict",
            "allow_row_expansion은 operation=table.expand_and_fill에서만 사용할 수 있습니다",
        )
    if effective_policy.atomic:
        certification = (
            None
            if resolution.workflow_id is None
            else certified_recipe(resolution.workflow_id)
        )
        if certification is None or certification.atomicity != "transactional":
            return effective_policy, effective_postconditions, workflow_result(
                resolution,
                "unsupported",
                "atomic=true를 보장하는 transactional 인증 recipe가 없습니다",
            )
    return effective_policy, effective_postconditions, None

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
from hwp_operation_descriptor import operation_descriptors
from hwp_operation_registry import operation_registry
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs


_LEGACY_WORKFLOW_REQUIRED_INPUTS: Mapping[HwpWorkflowId, tuple[str, ...]] = {
    "document.insert_page": ("inputs.target", "inputs.layout"),
    "document.rebuild": ("inputs.layout",),
    "table.inspect": ("inputs.target",),
    "table.create": ("inputs.target", "inputs.layout"),
    "table.resize": ("inputs.target", "inputs.data"),
    "table.propagate": ("inputs.target", "inputs.data"),
    "table.import_data": ("inputs.target", "inputs.data"),
    "image.resize": ("inputs.target", "inputs.data"),
}

WORKFLOW_REQUIRED_INPUTS: Mapping[HwpWorkflowId, tuple[str, ...]] = {
    **_LEGACY_WORKFLOW_REQUIRED_INPUTS,
    **{
        descriptor.workflow_id: descriptor.input_schema
        for descriptor in operation_descriptors()
    },
}

_WORKFLOW_TARGET_KINDS: Mapping[HwpWorkflowId, frozenset[str]] = {
    descriptor.workflow_id: descriptor.target_kinds
    for descriptor in operation_descriptors()
    if descriptor.target_kinds
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

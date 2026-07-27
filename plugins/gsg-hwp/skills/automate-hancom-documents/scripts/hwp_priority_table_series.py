from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_native_batch import read_native_snapshot
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_template_repeat import repeat_table_template
from hwp_live_template_series_sync import sync_table_template_series
from hwp_operation_contract import (
    HwpOperatePostconditions,
    OperationResult,
    WorkflowResolution,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_priority_recipe_result import priority_recipe_result


def operate_table_series_recipe(
    candidate: HwpDocumentCandidate,
    resolution: WorkflowResolution,
    recipe_inputs: HwpPriorityRecipeInputs | None,
    postconditions: HwpOperatePostconditions,
) -> OperationResult:
    if recipe_inputs is None or recipe_inputs.table_template is None:
        return priority_recipe_result(
            resolution,
            "needs_input",
            "표 반복용 네이티브 template plan이 필요합니다",
            required_inputs=("inputs.recipe.table_template",),
        )
    plan = recipe_inputs.table_template
    if resolution.workflow_id == "table.build_series" and not any(
        block.text_cells or block.images for block in plan.blocks
    ):
        return priority_recipe_result(
            resolution,
            "needs_input",
            "build_series에는 셀 내용 또는 그림 매핑이 필요합니다",
            required_inputs=("inputs.recipe.table_template.blocks",),
        )
    if (
        postconditions.record_count is not None
        and postconditions.record_count != len(plan.blocks)
    ):
        return priority_recipe_result(
            resolution,
            "schema_conflict",
            "record_count와 template block 수가 다릅니다",
        )
    before = read_native_snapshot(candidate.window_handle)
    if before is None:
        raise HwpLiveError("표 반복 전 네이티브 문서 상태를 읽지 못했습니다")
    repeated = (
        sync_table_template_series(candidate.window_handle, plan)
        if recipe_inputs.reconcile_existing
        else repeat_table_template(candidate.window_handle, plan)
    )
    updated_addresses = tuple(dict.fromkeys(
        cell.address
        for block in plan.blocks
        for cell in (*block.text_cells, *block.images)
    ))
    verification_error = repeated.verification_error
    if postconditions.preserve_page_count and repeated.page_count != before.page_count:
        verification_error = "표 반복 후 페이지 수 보존 완료조건을 만족하지 못했습니다"
    verified = repeated.verified and verification_error is None
    changed = repeated.commands_executed > 0
    status = (
        "executed" if verified else "partial_change" if changed else "operation_failed"
    )
    message = (
        "표 양식을 C++/ATL 네이티브로 반복하고 구조를 다시 읽어 검증했습니다"
        if verified
        else "표 반복 명령은 실행됐지만 사후 구조 검증이 일치하지 않았습니다"
        + ("" if verification_error is None else f": {verification_error}")
    )
    return priority_recipe_result(
        resolution,
        status,
        message,
    ).model_copy(
        update={
            "changed": changed,
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": "native_snapshot_before_after",
            "verified": verified,
            "commands_executed": repeated.commands_executed,
            "commands_completed": repeated.commands_executed,
            "native_elapsed_microseconds": repeated.native_elapsed_microseconds,
            "caption_profile_elapsed_microseconds": repeated.caption_profile_elapsed_microseconds,
            "clone_elapsed_microseconds": repeated.clone_elapsed_microseconds,
            "caption_elapsed_microseconds": repeated.caption_elapsed_microseconds,
            "content_elapsed_microseconds": repeated.content_elapsed_microseconds,
            "image_timing_count": repeated.image_timing_count,
            "image_maximum_microseconds": repeated.image_max_microseconds,
            "image_total_microseconds": repeated.image_total_microseconds,
            "current_page": repeated.current_page,
            "page_count": repeated.page_count,
            "modified": repeated.modified,
            "partial_change": not verified and changed,
            "partial_mutation": not verified and changed,
            "retry_safe": verified or not changed,
            "reconcile_required": not verified and changed,
            "failed_step": None if verified else "native_structure_readback",
            "blocks_applied": len(plan.blocks) if verified else None,
            "created_control_ids": repeated.created_control_ids,
            "updated_addresses": updated_addresses,
        }
    )

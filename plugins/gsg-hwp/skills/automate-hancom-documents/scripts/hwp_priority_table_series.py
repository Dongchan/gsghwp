from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_native_batch import read_native_snapshot
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_template_repeat import repeat_table_template
from hwp_live_template_series_discovery import (
    AmbiguousTableSeriesError,
    InvalidTableSeriesError,
)
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
    if postconditions.record_count is not None and postconditions.record_count != len(
        plan.blocks
    ):
        return priority_recipe_result(
            resolution,
            "schema_conflict",
            "record_count와 template block 수가 다릅니다",
        )
    before = read_native_snapshot(candidate.window_handle)
    if before is None:
        raise HwpLiveError("표 반복 전 네이티브 문서 상태를 읽지 못했습니다")
    if recipe_inputs.reconcile_existing:
        try:
            repeated = sync_table_template_series(candidate.window_handle, plan)
        except (AmbiguousTableSeriesError, InvalidTableSeriesError) as exc:
            return priority_recipe_result(
                resolution,
                "schema_conflict",
                str(exc),
            )
    else:
        repeated = repeat_table_template(candidate.window_handle, plan)
    updated_addresses = tuple(
        dict.fromkeys(
            cell.address
            for block in plan.blocks
            for cell in (*block.text_cells, *block.images)
        )
    )
    verification_error = repeated.verification_error
    caption_number_verification = repeated.caption_number_verification
    caption_number_message = repeated.caption_number_verification_message
    page_count_mismatch = False
    if postconditions.preserve_page_count and repeated.page_count != before.page_count:
        verification_error = "표 반복 후 페이지 수 보존 완료조건을 만족하지 못했습니다"
        page_count_mismatch = True
    elif caption_number_verification in {"unavailable", "mismatch"}:
        verification_error = caption_number_message or verification_error
    caption_number_unavailable = (
        caption_number_verification == "unavailable" and not page_count_mismatch
    )
    verified = (
        repeated.verified
        and verification_error is None
        and caption_number_verification != "mismatch"
    )
    changed = repeated.commands_executed > 0
    # 캡션 "번호를 읽지 못한 것"으로 작업 전체를 실패로 보고하지 않는다.
    # 표 복제와 내용 적용이 끝났는데 operation_failed 를 주면 모델은 되돌리고 다시 시도한다.
    # 그게 1 분이면 끝날 일을 몇 분으로 늘렸다. 번호 미확인은 message 로 알리면 충분하다.
    status = (
        "executed" if verified else "partial_change" if changed else "operation_failed"
    )
    message = (
        (
            caption_number_message
            or (
                "표 복제와 내용 적용은 완료됐지만 화면 캡션 번호 검증은 "
                "미완료입니다. 같은 요청을 다시 실행하지 말고 한/글 화면에서 "
                "복제 표의 캡션 번호를 확인하세요."
            )
        )
        if caption_number_unavailable
        else (
            "표 양식을 C++/ATL 네이티브로 반복하고 구조를 다시 읽어 검증했습니다"
            if verified
            else "표 반복 명령은 실행됐지만 사후 구조 검증이 일치하지 않았습니다"
            + ("" if verification_error is None else f": {verification_error}")
        )
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
            # 실제로 바뀐 쪽 범위를 그대로 올린다.
            # 이걸 빠뜨리면 공개 응답의 affected_pages 가 커서가 있는 마지막 쪽 하나만
            # 담게 되고, 모델은 그 쪽만 확인하고 나머지를 안 본다.
            # 라이브에서 10~19 쪽이 바뀌었는데 [19] 만 보고된 원인이 이 한 줄이었다.
            "changed_pages": repeated.affected_pages,
            "page_count": repeated.page_count,
            "modified": repeated.modified,
            "partial_change": False
            if caption_number_unavailable
            else not verified and changed,
            "partial_mutation": False
            if caption_number_unavailable
            else not verified and changed,
            "retry_safe": False
            if caption_number_unavailable
            else verified or not changed,
            "reconcile_required": False
            if caption_number_unavailable
            else not verified and changed,
            "failed_step": (
                None
                if verified
                else "caption_number_readback"
                if caption_number_verification in {"unavailable", "mismatch"}
                else "native_structure_readback"
            ),
            "blocks_applied": (
                len(plan.blocks)
                if verified or (caption_number_unavailable and changed)
                else None
            ),
            "created_control_ids": repeated.created_control_ids,
            "updated_addresses": updated_addresses,
        }
    )

from __future__ import annotations

from hwp_checkpoint_signature import checkpoint_signature_is_complete
from hwp_errors import HwpLiveError
from hwp_live_edit_history import (
    MAX_NATIVE_HISTORY_STEPS,
    LiveEditHistoryStore,
    NativeDocumentEditHistoryEntry,
)
from hwp_live_native_batch import (
    read_native_content_signature,
    read_native_snapshot,
)
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_template_repeat import TableTemplateRepeatPlan, repeat_table_template
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


# 표 반복은 됐는데 되돌리기 기록만 못 남겼을 때 할 말.
#
# 문구는 삭제 계열(`hwp_live_edit_history_runtime.py` 의
# `_DELETION_HISTORY_NOTICE` 와 `_DELETION_NOT_RECORDED_NOTICES`)을 그대로
# 따른다. 같은 손실이므로 같은 말을 해야 하고, 사용자가 다음에 할 일도 같다.
_SERIES_HISTORY_NOTICE = (
    "표 반복은 완료했지만 안전한 전후 문서 지문을 모두 읽지 못해 "
    "MCP 되돌리기 기록은 남기지 않았습니다. "
    "되돌리려면 한/글에서 되돌리기(Ctrl+Z)를 쓰세요"
)
_MAXIMUM_MESSAGE_LENGTH = 4_000


def _series_history_notice_result(result: OperationResult) -> OperationResult:
    """성공 문장 뒤에 "되돌리기 기록은 없다"를 붙인다.

    이어 붙이는 방식과 길이 처리는 `_layout_checkpoint_result` 와 같다.
    성공 자체를 부정하지 않는다 — 편집은 됐고, 없는 것은 부수 기록뿐이다.
    """
    separator = " " if result.message.endswith((".", "!", "?")) else ". "
    suffix = separator + _SERIES_HISTORY_NOTICE
    head = result.message[: _MAXIMUM_MESSAGE_LENGTH - len(suffix)]
    return result.model_copy(update={"message": head + suffix})


def operate_table_series_recipe(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
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

    def edit() -> OperationResult:
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
        return _series_result(
            resolution,
            postconditions,
            repeated,
            plan=plan,
            before_page_count=before.page_count,
        )

    if resolution.workflow_id != "table.build_series":
        return edit()

    # 이 지문 한 쌍은 아래 `history.record` 의 되돌리기 전제조건으로만 쓰인다.
    #
    # 편집 전 값은 엔진에 직접 묻는다. 캐시에 남은 것만 쓰는 방식(`peek`)도
    # 있었지만 그건 두 가지를 잘못한다. 하나는 정확성이다 — 캐시를 채우고
    # 버리지 않는 경로가 있으므로, 낡은 값을 편집 전 상태로 기록하면 되돌리기가
    # 존재한 적 없는 목표를 향해 되감는다. 다른 하나는 실효성이다 — 실사용
    # 공개 경로에는 지문을 미리 데우는 호출이 없어 캐시는 사실상 언제나
    # 비어 있고, 그러면 기록을 통째로 포기하는 것과 같다.
    #
    # 대용량의 17.2초는 여기서 참지 않는다. 그 비용은 엔진의 직렬화 거부이고,
    # 거부를 기억해 다음부터 싸게 되돌려주는 것은 `read_native_content_signature`
    # 안쪽(`_engine_still_refuses`)의 일이다. 이 자리는 필요한 값을 정직하게
    # 묻고, 못 받으면 못 받았다고 답한다.
    before_content_signature = (
        read_native_content_signature(candidate.window_handle) or ""
    )
    result = edit()
    if result.status != "executed" or not result.changed or result.verified is not True:
        return result
    after = read_native_snapshot(candidate.window_handle)
    if after is None:
        raise HwpLiveError("표 반복 후 네이티브 문서 상태를 읽지 못했습니다")
    # 편집 전 지문이 불완전하면 아래 기록 조건은 이미 거짓이다. 그 상태에서
    # 편집 후 지문을 읽는 것은 아무도 읽지 않을 답을 위해 직렬화 한 번을 더
    # 내는 것이다.
    after_content_signature = (
        (read_native_content_signature(candidate.window_handle) or "")
        if checkpoint_signature_is_complete(before_content_signature)
        else ""
    )
    if not (
        checkpoint_signature_is_complete(before_content_signature)
        and checkpoint_signature_is_complete(after_content_signature)
    ):
        # 표 반복은 끝났고 검증도 통과했다. 남지 않은 것은 되돌리기 기록뿐인데,
        # 그 사실을 말하지 않으면 사용자는 hwp_undo 를 눌러서야 알게 된다.
        # 삭제 계열이 같은 자리에서 하는 것과 같다.
        return _series_history_notice_result(result)
    history.record(
        NativeDocumentEditHistoryEntry(
            document_id=candidate.document_id,
            full_name=candidate.full_name,
            operation="table.build_series",
            maximum_native_steps=min(
                result.commands_executed or MAX_NATIVE_HISTORY_STEPS,
                MAX_NATIVE_HISTORY_STEPS,
            ),
            before_page_count=before.page_count,
            after_page_count=after.page_count,
            page=plan.source_page,
            before_content_signature=before_content_signature,
            after_content_signature=after_content_signature,
        )
    )
    return result


def _series_result(
    resolution: WorkflowResolution,
    postconditions: HwpOperatePostconditions,
    repeated: object,
    *,
    plan: TableTemplateRepeatPlan,
    before_page_count: int,
) -> OperationResult:
    from hwp_live_template_repeat import TableTemplateRepeatResult

    if not isinstance(repeated, TableTemplateRepeatResult):
        raise TypeError("repeated must be TableTemplateRepeatResult")
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
    if postconditions.preserve_page_count and repeated.page_count != before_page_count:
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

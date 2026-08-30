from __future__ import annotations

# noqa: SIZE_OK - Certified document edit routing is one exhaustive workflow state machine.

from collections.abc import Mapping
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_edit_history import NO_CHECKPOINT_EVIDENCE, LiveEditHistoryStore
from hwp_live_edit_history_policy import should_capture_full_document_checkpoint
from hwp_live_edit_history_runtime import (
    deletion_without_checkpoint_notice,
    execute_document_edit_history,
    execute_grouped_native_control_deletion,
    execute_grouped_native_page_deletion,
    execute_prepared_control_deletion,
    execute_prepared_page_deletion,
    prepare_control_deletion,
    prepare_page_deletion,
    verify_history_entry_state,
)
from hwp_live_document_edit_commands import (
    build_delete_page_commands,
)
from hwp_live_document_edit_verification import verify_control_deletion
from hwp_live_native_action_models import (
    NativeActionCommand,
    NativePageControl,
    NativePageInspection,
)
from hwp_live_native_batch import read_native_snapshot
from hwp_live_native_history import HistoryDirection, execute_native_history
from hwp_live_rot import HwpDocumentCandidate
from hwp_operation_certification import certified_recipe
from hwp_operation_contract import (
    HwpOperateTarget,
    HwpWorkflowId,
    OperationInputValue,
    OperationResult,
    OperationStatus,
    WorkflowResolution,
)
from hwp_operation_registry import operation_registry


_WORKFLOWS = frozenset[HwpWorkflowId](
    ("document.delete_page", "document.undo", "document.redo", "control.delete")
)
# MCP 기록이 없어 한/글 자신의 실행 이력으로 처리한 경우. 능력은 유지하고
# (거절하면 사용자는 되돌릴 방법 자체를 잃는다) 무엇을 확인하지 못했는지 말한다.
# 이 문구는 성공 문장을 대체한다(`history_notice` 규약).
_UNMANAGED_NATIVE_HISTORY_NOTICES: dict[HwpWorkflowId, str] = {
    "document.undo": (
        "현재 문서에 MCP가 기록한 되돌리기 목표 상태가 없어, 한/글 자신의 실행 "
        "이력을 {applied}단계 되돌렸습니다. 대조할 목표 상태가 없으므로 문서가 "
        "어떤 상태가 됐는지는 MCP가 확인하지 못했습니다. 한/글에서 문서를 "
        "확인하세요"
    ),
    "document.redo": (
        "현재 문서에 MCP가 기록한 다시 실행 목표 상태가 없어, 한/글 자신의 실행 "
        "이력을 {applied}단계 다시 실행했습니다. 대조할 목표 상태가 없으므로 "
        "문서가 어떤 상태가 됐는지는 MCP가 확인하지 못했습니다. 한/글에서 문서를 "
        "확인하세요"
    ),
}


@dataclass(frozen=True, slots=True)
class NativeDocumentEditRequest:
    candidate: HwpDocumentCandidate
    history: LiveEditHistoryStore
    routing_page: NativePageInspection
    resolution: WorkflowResolution
    target: HwpOperateTarget | None
    parameters: Mapping[str, OperationInputValue]
    resolve_only: bool
    allow_document_change: bool


def _result(
    request: NativeDocumentEditRequest,
    status: OperationStatus,
    message: str,
    *,
    required_inputs: tuple[str, ...] = (),
) -> OperationResult:
    workflow = request.resolution.workflow_id
    recipe = None if workflow is None else certified_recipe(workflow)
    return OperationResult(
        status=status,
        query=request.resolution.query,
        registry_entries=operation_registry().count,
        lookup_microseconds=request.resolution.lookup_microseconds,
        workflow_candidates=request.resolution.candidates,
        required_inputs=required_inputs,
        message=message,
        recipe_id=None if recipe is None else f"recipe:{recipe.recipe_id}",
        recipe_steps=request.resolution.steps,
    )


def _page_commands(
    request: NativeDocumentEditRequest,
) -> tuple[tuple[NativeActionCommand, ...], int] | OperationResult:
    target = request.target
    if target is None or target.kind != "page" or target.page_hint is None:
        return _result(
            request,
            "needs_input",
            "삭제할 실제 쪽 번호를 target.page_hint에 전달하세요",
            required_inputs=("inputs.target.page_hint",),
        )
    if target.page_hint > request.routing_page.page_count:
        return _result(
            request, "not_found", "삭제할 쪽이 현재 문서 범위를 벗어났습니다"
        )
    if request.routing_page.page_count == 1:
        return _result(
            request,
            "unsupported",
            "문서의 마지막 한 쪽은 제거할 수 없습니다. 남은 내용이나 개체를 삭제하세요",
        )
    return build_delete_page_commands(target.page_hint), target.page_hint


def _control_commands(
    request: NativeDocumentEditRequest,
) -> tuple[tuple[NativePageControl, ...], tuple[str, ...], int] | OperationResult:
    target = request.target
    if target is None or target.kind != "control" or target.page_hint is None:
        return _result(
            request,
            "needs_input",
            "개체가 있는 쪽과 빠른 구조 조회에서 얻은 개체 ID를 전달하세요",
            required_inputs=(
                "inputs.target.page_hint",
                "inputs.target.control_instance_ids",
            ),
        )
    if target.control_instance_id is not None and target.control_instance_ids:
        return _result(
            request,
            "schema_conflict",
            "단일 개체 ID와 개체 ID 목록을 동시에 전달할 수 없습니다",
        )
    instance_ids = target.control_instance_ids
    if not instance_ids and target.control_instance_id is not None:
        instance_ids = (target.control_instance_id,)
    if not instance_ids:
        return _result(
            request,
            "needs_input",
            "빠른 구조 조회에서 얻은 개체 ID를 하나 이상 전달하세요",
            required_inputs=("inputs.target.control_instance_ids",),
        )
    visible = {
        control.instance_id: control for control in request.routing_page.controls
    }
    missing = tuple(value for value in instance_ids if value not in visible)
    if missing:
        return _result(
            request,
            "not_found",
            "현재 쪽 구조에서 삭제 대상 개체 ID를 찾지 못했습니다: "
            + ", ".join(missing),
        )
    controls = tuple(visible[value] for value in instance_ids)
    return controls, instance_ids, target.page_hint


def _history_request(
    request: NativeDocumentEditRequest,
    workflow: HwpWorkflowId,
) -> tuple[HistoryDirection, int] | OperationResult:
    steps = request.parameters.get("steps", 1)
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1 or steps > 20:
        return _result(
            request,
            "needs_input",
            "실행 이력 단계 수는 1에서 20 사이의 정수여야 합니다",
            required_inputs=("inputs.parameters.steps",),
        )
    return "undo" if workflow == "document.undo" else "redo", steps


def operate_native_document_edit(
    request: NativeDocumentEditRequest,
) -> OperationResult | None:
    workflow = request.resolution.workflow_id
    if workflow not in _WORKFLOWS:
        return None
    if request.resolve_only:
        return _result(
            request, "resolved", "인증된 실시간 문서 편집 recipe를 확정했습니다"
        )
    if not request.allow_document_change:
        return _result(
            request, "confirmation_required", "문서 내용을 변경하는 작업입니다"
        )

    page_target: int | None = None
    control_ids: tuple[str, ...] = ()
    control_targets: tuple[NativePageControl, ...] = ()
    commands: tuple[NativeActionCommand, ...] = ()
    history: tuple[HistoryDirection, int] | None = None
    history_notice = ""
    history_state_confirmed: bool | None = True
    history_changed_pages: tuple[int, ...] = ()
    checkpoint_evidence = NO_CHECKPOINT_EVIDENCE
    if workflow == "document.delete_page":
        prepared = _page_commands(request)
        if isinstance(prepared, OperationResult):
            return prepared
        commands, page_target = prepared
    elif workflow == "control.delete":
        prepared_control = _control_commands(request)
        if isinstance(prepared_control, OperationResult):
            return prepared_control
        control_targets, control_ids, page_target = prepared_control
    else:
        prepared_history = _history_request(request, workflow)
        if isinstance(prepared_history, OperationResult):
            return prepared_history
        history = prepared_history

    before = read_native_snapshot(request.candidate.window_handle)
    if before is None:
        raise HwpLiveError("실시간 편집 전 문서 상태를 읽지 못했습니다")
    custom_history = False
    managed_history = False
    checkpoint_history = should_capture_full_document_checkpoint(
        request.routing_page.full_name
    )
    if workflow == "control.delete":
        assert page_target is not None
        # None means the checkpoint could not be taken. That costs the document
        # checkpoint undo entry, never the deletion the caller asked for: the
        # grouped path below is what large documents already use, and it records
        # an undo entry backed by 한/글's own history instead.
        prepared_deletion = (
            prepare_control_deletion(
                request.candidate,
                request.history,
                request.routing_page.document_id,
                request.routing_page.full_name,
                page_target,
                before.page_count,
                control_targets,
            )
            if checkpoint_history
            else None
        )
        if prepared_deletion is not None:
            history_execution = execute_prepared_control_deletion(
                request.candidate,
                request.history,
                prepared_deletion,
            )
        else:
            history_execution = execute_grouped_native_control_deletion(
                request.candidate,
                request.history,
                request.routing_page.document_id,
                request.routing_page.full_name,
                page_target,
                before.page_count,
                control_targets,
            )
            history_notice = deletion_without_checkpoint_notice("control.delete")
        commands_executed = history_execution.commands_executed
        elapsed_microseconds = history_execution.elapsed_microseconds
        custom_history = history_execution.custom_history
        checkpoint_evidence = history_execution.checkpoint_evidence
        history_notice = "; ".join(
            notice for notice in (history_notice, history_execution.notice) if notice
        )
        history_state_confirmed = history_execution.state_confirmed
        managed_history = True
    elif workflow == "document.delete_page":
        assert page_target is not None
        prepared_deletion = (
            prepare_page_deletion(
                request.candidate,
                request.history,
                request.routing_page.document_id,
                request.routing_page.full_name,
                page_target,
                before.page_count,
            )
            if checkpoint_history
            else None
        )
        if prepared_deletion is not None:
            history_execution = execute_prepared_page_deletion(
                request.candidate,
                request.history,
                prepared_deletion,
                commands,
            )
        else:
            history_execution = execute_grouped_native_page_deletion(
                request.candidate,
                request.history,
                request.routing_page.document_id,
                request.routing_page.full_name,
                page_target,
                before.page_count,
                commands,
            )
            history_notice = deletion_without_checkpoint_notice("document.delete_page")
        commands_executed = history_execution.commands_executed
        elapsed_microseconds = history_execution.elapsed_microseconds
        custom_history = history_execution.custom_history
        checkpoint_evidence = history_execution.checkpoint_evidence
        history_notice = "; ".join(
            notice for notice in (history_notice, history_execution.notice) if notice
        )
        history_state_confirmed = history_execution.state_confirmed
        managed_history = True
    else:
        assert history is not None
        direction, steps = history
        custom_result = execute_document_edit_history(
            request.candidate,
            request.history,
            request.routing_page.document_id,
            request.routing_page.full_name,
            direction,
            steps,
        )
        if custom_result is None:
            # MCP 가 기록한 목표 상태가 없다. 한/글 자신의 이력은 그래도 돌린다
            # (b733c58: 목표 상태가 없다는 이유로 되돌리기를 거절하면 사용자는
            # 되돌릴 방법 자체를 잃는다). 대신 이 경로가 무엇을 확인하지 못했는지
            # 답이 말한다.
            #
            # 예전에는 여기서 `history_state_confirmed = True` 를 박았다. 그
            # 한 줄이 `verified: true` 가 되어, 되돌아갈 목표 상태가 없어 아무것도
            # 대조하지 못한 호출이 "확인했다"고 답했다 — 165쪽 문서가 1쪽이 되는
            # 되돌리기도 그렇게 통과했다.
            native = execute_native_history(
                request.candidate.window_handle,
                direction,
                steps,
            )
            commands_executed = native.applied
            elapsed_microseconds = native.elapsed_microseconds
            custom_history = False
            history_notice = (
                (
                    f"한컴 실행 이력이 더 남아 있지 않아 {native.applied}단계를 "
                    + "적용했습니다. 문서는 이 호출로 바뀌지 않았습니다"
                )
                if native.applied == 0
                else _UNMANAGED_NATIVE_HISTORY_NOTICES[workflow].format(
                    applied=native.applied
                )
            )
            # None: 확인하지 못했다. False(확인해 보니 아니다)도 True(확인했다)도
            # 아니다. 대조할 목표 상태가 없었으므로 이 경로는 어느 쪽도 말할 수
            # 없다.
            history_state_confirmed = None
            history_changed_pages = ()
            checkpoint_evidence = NO_CHECKPOINT_EVIDENCE
            managed_history = False
        else:
            commands_executed = custom_result.commands_executed
            elapsed_microseconds = custom_result.elapsed_microseconds
            custom_history = custom_result.custom_history
            history_notice = custom_result.notice
            history_state_confirmed = custom_result.state_confirmed
            history_changed_pages = custom_result.changed_pages
            checkpoint_evidence = custom_result.checkpoint_evidence
            managed_history = True
    after = read_native_snapshot(request.candidate.window_handle)
    if after is None:
        raise HwpLiveError("실시간 편집 후 문서 상태를 읽지 못했습니다")
    if before.document_id != after.document_id or before.full_name != after.full_name:
        raise HwpLiveError("실시간 편집 중 대상 문서가 바뀌었습니다")
    if managed_history and workflow == "document.undo":
        restored_entry = request.history.peek(
            "redo",
            request.routing_page.document_id,
            request.routing_page.full_name,
        )
        if restored_entry is not None:
            verify_history_entry_state(request.candidate, restored_entry, "undo")
    if workflow == "document.delete_page" and after.page_count != before.page_count - 1:
        raise HwpLiveError("쪽 삭제 후 페이지 수가 정확히 하나 줄지 않았습니다")
    if workflow == "control.delete":
        assert page_target is not None
        verify_control_deletion(
            request.candidate.window_handle,
            page_target,
            control_ids,
            after.page_count,
        )

    applied = commands_executed
    messages = {
        "document.delete_page": "지정한 쪽을 삭제하고 페이지 수 감소를 확인했습니다",
        "document.undo": f"한컴 실행 이력을 {applied}단계 되돌렸습니다",
        "document.redo": f"취소한 한컴 실행 이력을 {applied}단계 다시 실행했습니다",
        "control.delete": "지정한 기존 개체를 삭제하고 빠른 구조에서 제거를 확인했습니다",
    }
    if history_notice:
        # The managed history path could not reach the recorded boundary. It
        # already knows exactly what happened to the document — including that
        # nothing did, or that it could not tell — and that is the whole answer.
        # None of the stock lines below may overwrite or decorate it.
        messages[workflow] = history_notice
    elif managed_history and not custom_history and commands_executed == 0:
        # The grouped native path found the document already at the recorded
        # operation boundary and ran no Undo/Redo at all. The bookkeeping entry
        # moved, but saying "되돌렸습니다" here would be the same lie as
        # reporting a step count that never ran.
        messages[workflow] = (
            "문서가 이미 이 MCP 작업 단위의 경계 상태여서 한컴 실행 이력을 "
            "추가로 실행하지 않았습니다. 문서 내용은 이 호출로 바뀌지 "
            "않았습니다"
        )
    elif managed_history and workflow == "document.undo":
        messages[workflow] = (
            "MCP 문서 체크포인트를 같은 탭에 복구하고 빠른 구조로 확인했습니다"
            if custom_history
            else "MCP 작업 단위를 한컴 이력으로 되돌리고 빠른 구조로 확인했습니다"
        )
    elif managed_history and workflow == "document.redo":
        messages[workflow] = (
            "MCP 문서 체크포인트의 편집 후 상태를 다시 적용하고 빠른 구조로 확인했습니다"
            if custom_history
            else "취소한 MCP 작업 단위를 한컴 이력으로 다시 실행하고 빠른 구조로 확인했습니다"
        )
    changed = (
        commands_executed > 0
        if not managed_history
        else not (not custom_history and commands_executed == 0)
    )
    return _result(request, "executed", messages[workflow]).model_copy(
        update={
            "changed": changed,
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": "native_snapshot_before_after",
            # Tri-state on purpose: None means the managed history path could not
            # prove where the document ended up, False means it proved the
            # document is not where the stock line would claim. Never promoted.
            "verified": history_state_confirmed,
            "commands_executed": commands_executed,
            "commands_completed": commands_executed,
            "native_elapsed_microseconds": elapsed_microseconds,
            "current_page": after.current_page,
            "page_count": after.page_count,
            "modified": after.modified,
            "before_page_count": before.page_count,
            "after_page_count": after.page_count,
            "before_modified": before.modified,
            "after_modified": after.modified,
            "partial_mutation": False,
            "retry_safe": False,
            "resolved_target_id": control_ids[0] if len(control_ids) == 1 else None,
            # Empty means no checkpoint was written on this path, which is a
            # different statement from "the disk copy was not used".
            "document_checkpoint_capture": (checkpoint_evidence.capture_method or None),
            "document_identity_restored": (
                checkpoint_evidence.identity_restored
                if checkpoint_evidence.capture_method
                else None
            ),
            "changed_pages": history_changed_pages,
        }
    )

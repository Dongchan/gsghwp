from __future__ import annotations

# noqa: SIZE_OK - Certified document edit routing is one exhaustive workflow state machine.

from collections.abc import Mapping
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_edit_history import LiveEditHistoryStore
from hwp_live_edit_history_policy import should_capture_full_document_checkpoint
from hwp_live_edit_history_runtime import (
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
from hwp_live_document_edit_verification import (
    capture_history_structure_snapshot,
    verify_control_deletion,
    verify_history_structure_change,
)
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
_EXHAUSTED_MESSAGES: dict[HwpWorkflowId, str] = {
    "document.undo": (
        "되돌릴 한컴 실행 이력이 없습니다. 이력 끝이므로 문서를 변경하지 않았습니다"
    ),
    "document.redo": (
        "다시 실행할 한컴 실행 이력이 없습니다. 이력 끝이므로 문서를 변경하지 "
        "않았습니다"
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
    native_applied_steps: int | None = None
    native_requested_steps: int | None = None
    native_content_changed = False
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
    history_structure_before = None
    if history is not None:
        direction, _steps = history
        if (
            request.history.available(
                direction,
                request.routing_page.document_id,
                request.routing_page.full_name,
            )
            == 0
        ):
            history_structure_before = capture_history_structure_snapshot(
                request.candidate,
                request.routing_page.page,
                before.page_count,
            )
    custom_history = False
    managed_history = False
    checkpoint_history = should_capture_full_document_checkpoint(
        request.routing_page.full_name
    )
    if workflow == "control.delete":
        assert page_target is not None
        if checkpoint_history:
            prepared_deletion = prepare_control_deletion(
                request.candidate,
                request.history,
                request.routing_page.document_id,
                request.routing_page.full_name,
                page_target,
                before.page_count,
                control_targets,
            )
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
        commands_executed = history_execution.commands_executed
        elapsed_microseconds = history_execution.elapsed_microseconds
        custom_history = history_execution.custom_history
        managed_history = True
    elif workflow == "document.delete_page":
        assert page_target is not None
        if checkpoint_history:
            prepared_deletion = prepare_page_deletion(
                request.candidate,
                request.history,
                request.routing_page.document_id,
                request.routing_page.full_name,
                page_target,
                before.page_count,
            )
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
        commands_executed = history_execution.commands_executed
        elapsed_microseconds = history_execution.elapsed_microseconds
        custom_history = history_execution.custom_history
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
            history_result = execute_native_history(
                request.candidate.window_handle,
                direction,
                steps,
            )
            if history_result.applied == 0:
                # The engine reports an empty history stack. Nothing was
                # executed and nothing changed, so this is neither a success
                # nor a failure — report the fact and stop before the
                # before/after comparison, which would find no change and
                # raise as if something had gone wrong.
                return _result(
                    request,
                    "unsupported",
                    _EXHAUSTED_MESSAGES[workflow],
                )
            native_applied_steps = history_result.applied
            native_requested_steps = history_result.steps
            native_content_changed = history_result.content_changed
            commands_executed = history_result.applied
            elapsed_microseconds = history_result.elapsed_microseconds
        else:
            commands_executed = custom_result.commands_executed
            elapsed_microseconds = custom_result.elapsed_microseconds
            custom_history = custom_result.custom_history
            managed_history = True
    after = read_native_snapshot(request.candidate.window_handle)
    if after is None:
        raise HwpLiveError("실시간 편집 후 문서 상태를 읽지 못했습니다")
    if before.document_id != after.document_id or before.full_name != after.full_name:
        raise HwpLiveError("실시간 편집 중 대상 문서가 바뀌었습니다")
    if history_structure_before is not None:
        history_structure_after = capture_history_structure_snapshot(
            request.candidate,
            request.routing_page.page,
            after.page_count,
        )
        if not native_content_changed:
            # The routing page state token covers that page's body text,
            # paragraphs, controls and table cell text, and it is taken from an
            # inspection that restores the caret and selection, so a selection
            # that merely got dropped cannot move it. An unchanged token with no
            # native content evidence therefore means no restored edit was
            # observed anywhere we can see, and claiming success is not allowed.
            verify_history_structure_change(
                history_structure_before,
                history_structure_after,
            )
        # Otherwise the native before/after state already proved that page,
        # control count or control hash changed. The edit was restored outside
        # the inspected page, so an unchanged token here is not a failure.
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

    applied = (
        commands_executed if native_applied_steps is None else native_applied_steps
    )
    messages = {
        "document.delete_page": "지정한 쪽을 삭제하고 페이지 수 감소를 확인했습니다",
        "document.undo": f"한컴 실행 이력을 {applied}단계 되돌렸습니다",
        "document.redo": f"취소한 한컴 실행 이력을 {applied}단계 다시 실행했습니다",
        "control.delete": "지정한 기존 개체를 삭제하고 빠른 구조에서 제거를 확인했습니다",
    }
    if (
        native_applied_steps is not None
        and native_requested_steps is not None
        and native_applied_steps < native_requested_steps
    ):
        # Report the number that actually applied, not the number requested.
        messages[workflow] = (
            f"{messages[workflow]}. 요청한 {native_requested_steps}단계 중 "
            f"{native_applied_steps}단계에서 한컴 실행 이력이 끝나 "
            "나머지는 실행하지 않았습니다"
        )
    if managed_history and not custom_history and commands_executed == 0:
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
    changed = not (managed_history and not custom_history and commands_executed == 0)
    return _result(request, "executed", messages[workflow]).model_copy(
        update={
            "changed": changed,
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": "native_snapshot_before_after",
            "verified": True,
            "commands_executed": commands_executed,
            "commands_completed": commands_executed,
            "native_elapsed_microseconds": elapsed_microseconds,
            "current_page": after.current_page,
            "page_count": after.page_count,
            "modified": True,
            "before_page_count": before.page_count,
            "after_page_count": after.page_count,
            "before_modified": before.modified,
            "after_modified": after.modified,
            "partial_mutation": False,
            "retry_safe": False,
            "resolved_target_id": control_ids[0] if len(control_ids) == 1 else None,
        }
    )

from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import NativeActionCommand, NativeActionRequest
from hwp_live_native_batch import execute_native_actions, read_native_snapshot
from hwp_live_rot import HwpDocumentCandidate
from hwp_operation_certification import certified_recipe
from hwp_operation_contract import (
    OperationResult,
    OperationStatus,
    WorkflowResolution,
)
from hwp_operation_registry import operation_registry


def object_recipe_result(
    resolution: WorkflowResolution,
    status: OperationStatus,
    message: str,
    *,
    required_inputs: tuple[str, ...] = (),
) -> OperationResult:
    workflow = resolution.workflow_id
    recipe = None if workflow is None else certified_recipe(workflow)
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


def execute_object_commands(
    candidate: HwpDocumentCandidate,
    commands: tuple[NativeActionCommand, ...],
) -> tuple[int, int, int, int, bool]:
    native = execute_native_actions(
        candidate.window_handle,
        NativeActionRequest(
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            commands=commands,
        ),
        minimum_version=9,
    )
    if native is None:
        raise HwpLiveError("한컴 프로토콜 9 네이티브 개체 recipe를 사용할 수 없습니다")
    after = read_native_snapshot(candidate.window_handle)
    if after is None:
        raise HwpLiveError("한컴 네이티브 개체 작업 결과를 읽지 못했습니다")
    return (
        native.commands_executed,
        native.elapsed_microseconds,
        after.current_page,
        after.page_count,
        after.modified,
    )

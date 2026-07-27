from __future__ import annotations

import ntpath
from collections.abc import Callable, Mapping
from typing import Final

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_action_models import (
    NativeActionRequest,
    NativePosition,
)
from hwp_live_native_action_results import NativeActionResult, NativeSnapshot
from hwp_live_native_batch import execute_native_actions, read_native_snapshot
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_safety import require_writable_document, run_layout_mutation
from hwp_native_failure_result import ATOMIC_ACTION_MINIMUM_NATIVE_PROTOCOL
from hwp_operation_command import plan_operation_command
from hwp_operation_certification import certified_atomic_action
from hwp_operation_contract import (
    OperationCandidate,
    OperationInputValue,
    OperationResolution,
    OperationResult,
    OperationStatus,
)
from hwp_operation_registry import resolve_operation


_SNAPSHOT_READBACK_ACTIONS: Final = frozenset(
    {
        "MoveDocBegin",
        "MoveListBegin",
        "MoveNextParaBegin",
        "MovePageDown",
        "MovePageUp",
        "MoveParaBegin",
        "MovePrevParaBegin",
        "MoveSelDocBegin",
        "MoveSelListBegin",
        "MoveSelNextParaBegin",
        "MoveSelParaBegin",
        "MoveSelPrevParaBegin",
    }
)


def _same_document(
    snapshot: NativeSnapshot,
    candidate: HwpDocumentCandidate,
) -> bool:
    return snapshot.document_id == candidate.document_id and ntpath.normcase(
        ntpath.normpath(snapshot.full_name)
    ) == ntpath.normcase(ntpath.normpath(candidate.full_name))


def _selection_has_endpoints(
    snapshot: NativeSnapshot,
    first: NativePosition,
    second: NativePosition,
) -> bool:
    selection = snapshot.selection
    return selection.selected and (
        (selection.start == first and selection.end == second)
        or (selection.start == second and selection.end == first)
    )


def _snapshot_postcondition_verified(
    action: str,
    candidate: HwpDocumentCandidate,
    before: NativeSnapshot | None,
    after: NativeSnapshot | None,
) -> bool:
    if (
        before is None
        or after is None
        or not _same_document(before, candidate)
        or not _same_document(after, candidate)
        or before.page_count != after.page_count
        or before.modified != after.modified
        or before.selection.selected
    ):
        return False

    cursor = before.cursor
    after_cursor = after.cursor
    if action == "MoveDocBegin":
        return (
            not after.selection.selected
            and after.current_page == 1
            and after_cursor == NativePosition(0, 0, 0)
        )
    if action == "MoveListBegin":
        return not after.selection.selected and after_cursor == NativePosition(
            cursor.list_id, 0, 0
        )
    if action == "MoveParaBegin":
        return not after.selection.selected and after_cursor == NativePosition(
            cursor.list_id, cursor.paragraph, 0
        )
    if action == "MoveNextParaBegin":
        return not after.selection.selected and after_cursor == NativePosition(
            cursor.list_id, cursor.paragraph + 1, 0
        )
    if action == "MovePrevParaBegin":
        return (
            cursor.paragraph > 0
            and not after.selection.selected
            and after_cursor == NativePosition(cursor.list_id, cursor.paragraph - 1, 0)
        )
    if action == "MovePageDown":
        return (
            cursor.list_id == 0
            and not after.selection.selected
            and after_cursor.list_id == 0
            and after.current_page == before.current_page + 1
        )
    if action == "MovePageUp":
        return (
            cursor.list_id == 0
            and before.current_page > 1
            and not after.selection.selected
            and after_cursor.list_id == 0
            and after.current_page == before.current_page - 1
        )

    selection_target = {
        "MoveSelDocBegin": NativePosition(0, 0, 0),
        "MoveSelListBegin": NativePosition(cursor.list_id, 0, 0),
        "MoveSelNextParaBegin": NativePosition(
            cursor.list_id,
            cursor.paragraph + 1,
            0,
        ),
        "MoveSelParaBegin": NativePosition(
            cursor.list_id,
            cursor.paragraph,
            0,
        ),
        "MoveSelPrevParaBegin": NativePosition(
            cursor.list_id,
            cursor.paragraph - 1,
            0,
        ),
    }.get(action)
    return (
        selection_target is not None
        and selection_target != cursor
        and _selection_has_endpoints(after, cursor, selection_target)
    )


def _not_executed(
    resolution: OperationResolution,
    status: OperationStatus,
    message: str,
    operation: OperationCandidate | None = None,
) -> OperationResult:
    return OperationResult(
        status=status,
        query=resolution.query,
        registry_entries=resolution.registry_entries,
        lookup_microseconds=resolution.lookup_microseconds,
        operation=resolution.operation if operation is None else operation,
        candidates=resolution.candidates,
        message=message,
    )


def _resolved_operation(resolution: OperationResolution) -> OperationCandidate | None:
    if resolution.status == "not_found":
        return None
    if resolution.status == "ambiguous":
        return None
    return resolution.operation


def operate_validated(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    intent_or_operation_id: str,
    inputs: Mapping[str, OperationInputValue],
    *,
    resolve_only: bool,
    allow_document_change: bool,
    use_defaults: bool,
    expected_cursor: tuple[int, int, int] | None,
    unsafe_selectors: set[str],
    guard: Callable[[], None],
) -> OperationResult:
    _ = hwp
    resolution = resolve_operation(intent_or_operation_id)
    operation = _resolved_operation(resolution)
    if resolution.status == "not_found":
        return _not_executed(
            resolution,
            "not_found",
            "1,452개 로컬 작업 레지스트리에서 일치하는 공식 API를 찾지 못했습니다",
        )
    if resolution.status == "ambiguous" or operation is None:
        return _not_executed(
            resolution,
            "ambiguous",
            "정확히 하나로 확정하지 못했습니다. 반환된 안정 operation_id 중 하나를 사용하세요",
        )
    if resolve_only:
        return _not_executed(
            resolution,
            "resolved",
            "로컬 작업 레지스트리에서 공식 API를 확정했습니다",
            operation,
        )
    if operation.latest_live_status == "known_failure":
        return _not_executed(
            resolution,
            "known_failure",
            operation.known_failure_reason
            or "최신 설치 플러그인 실기동에서 실패가 확인된 공식 API입니다",
            operation,
        )
    if operation.execution_policy == "blocked":
        return _not_executed(
            resolution,
            "blocked",
            "저장·종료·실행취소·모달 작업은 생산 플러그인의 단일 API 경로에서 차단됩니다",
            operation,
        )
    if operation.execution_policy == "catalog_only":
        recommendation = (
            ""
            if operation.recommended_tool is None
            else f" 대신 {operation.recommended_tool} 전용 네이티브 도구를 사용하세요"
        )
        return _not_executed(
            resolution,
            "unsupported",
            "공식 API는 찾았지만 현재 생산 단일 실행 계약에는 없는 유형입니다"
            + recommendation,
            operation,
        )
    if certified_atomic_action(operation) is None:
        return _not_executed(
            resolution,
            "unsupported",
            "실기동 PASS로 인증된 단일 Action primitive가 아니므로 production에서 실행하지 않습니다",
            operation,
        )
    if operation.execution_policy == "document_change" and not allow_document_change:
        return _not_executed(
            resolution,
            "confirmation_required",
            "문서 변경 액션입니다. 사용자 요청 범위와 일치할 때 allow_document_change=true로 다시 호출하세요",
            operation,
        )

    command_plan = plan_operation_command(
        operation,
        inputs,
        use_defaults=use_defaults,
    )
    if command_plan.status != "ready" or command_plan.command is None:
        return _not_executed(
            resolution,
            "needs_input" if command_plan.status == "needs_input" else "unsupported",
            command_plan.message,
            operation,
        )
    request = NativeActionRequest(
        document_id=candidate.document_id,
        full_name=candidate.full_name,
        commands=(command_plan.command,),
        expected_cursor=(
            None if expected_cursor is None else NativePosition(*expected_cursor)
        ),
    )

    def execute() -> NativeActionResult:
        native_result = execute_native_actions(
            candidate.window_handle,
            request,
            minimum_version=ATOMIC_ACTION_MINIMUM_NATIVE_PROTOCOL,
        )
        if native_result is None:
            raise HwpLiveError("한컴 네이티브 단일 작업 실행기를 사용할 수 없습니다")
        return native_result

    snapshot_readback = operation.name in _SNAPSHOT_READBACK_ACTIONS
    before = (
        read_native_snapshot(candidate.window_handle) if snapshot_readback else None
    )
    if operation.execution_policy == "document_change":
        require_writable_document(unsafe_selectors, candidate.selector)
        guard()
        native = run_layout_mutation(
            unsafe_selectors,
            candidate.selector,
            execute,
        )
    else:
        native = execute()
    after = read_native_snapshot(candidate.window_handle) if snapshot_readback else None
    verified = _snapshot_postcondition_verified(
        operation.name,
        candidate,
        before,
        after,
    )
    if snapshot_readback:
        message = (
            "공식 이동·선택 Action을 실행하고 네이티브 snapshot 후조건을 확인했습니다"
            if verified
            else "공식 이동·선택 Action은 실행됐지만 네이티브 snapshot 후조건이 일치하지 않았습니다"
        )
        verification = "native_snapshot_before_after"
    else:
        message = (
            "공식 Action은 실행됐지만 이 유형의 관찰 가능한 후조건 readback이 "
            "없어 검증하지 않았습니다"
        )
        verification = "native_action_result"

    return OperationResult(
        status="executed",
        query=resolution.query,
        registry_entries=resolution.registry_entries,
        lookup_microseconds=resolution.lookup_microseconds,
        operation=operation,
        candidates=resolution.candidates,
        message=message,
        execution_mode="native_in_process",
        native_protocol=ATOMIC_ACTION_MINIMUM_NATIVE_PROTOCOL,
        verification=verification,
        verified=verified,
        commands_executed=native.commands_executed,
        native_actions_executed=native.actions_executed,
        native_elapsed_microseconds=native.elapsed_microseconds,
        current_page=None if after is None else after.current_page,
        page_count=None if after is None else after.page_count,
        modified=None if after is None else after.modified,
    )

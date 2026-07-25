from __future__ import annotations

from collections.abc import Callable, Mapping

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_action_models import (
    NativeActionRequest,
    NativePosition,
)
from hwp_live_native_action_results import NativeActionResult
from hwp_live_native_batch import execute_native_actions
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_safety import require_writable_document, run_layout_mutation
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
        document_id=candidate.document.DocumentID,
        full_name=candidate.document.FullName,
        commands=(command_plan.command,),
        expected_cursor=(
            None if expected_cursor is None else NativePosition(*expected_cursor)
        ),
    )
    def execute() -> NativeActionResult:
        native_result = execute_native_actions(
            candidate.window_handle,
            request,
            minimum_version=9,
        )
        if native_result is None:
            raise HwpLiveError("한컴 네이티브 단일 작업 실행기를 사용할 수 없습니다")
        return native_result

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

    return OperationResult(
        status="executed",
        query=resolution.query,
        registry_entries=resolution.registry_entries,
        lookup_microseconds=resolution.lookup_microseconds,
        operation=operation,
        candidates=resolution.candidates,
        message="공식 API를 프로토콜 9 C++/ATL 네이티브 엔진에서 한 건 실행했습니다",
        execution_mode="native_in_process",
        native_protocol=9,
        verification="native_action_result",
        commands_executed=native.commands_executed,
        native_elapsed_microseconds=native.elapsed_microseconds,
    )

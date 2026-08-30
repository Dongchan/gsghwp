from __future__ import annotations

from typing import Final

from hwp_errors import HwpLiveError, HwpTargetProcessLostError
from hwp_mcp_result_arguments import (
    ProductionWorkflowId,
    production_tool_name,
    production_workflow,
    public_missing_fields,
)
from hwp_mcp_result_reuse import wrapper_arguments
from hwp_operation_contract import (
    HwpOperateInputs,
    OperationResult,
    OperationStatus,
    WorkflowTargetCandidate,
    canonical_workflow,
)


_TABLE_TARGET_WORKFLOWS: Final[frozenset[ProductionWorkflowId]] = frozenset(
    (
        "table.fill_existing",
        "table.expand_and_fill",
        "table.insert_images",
        "caption.add",
        "table.format",
        "table.merge_cells",
        "table.split_cells",
    )
)
_DIALOG_REASON_PREFIXES: Final[tuple[str, ...]] = (
    "대상 한컴 창에 대화상자가 떠 있습니다",
    "고아 한컴 오류 대화상자가 남아 있습니다",
    "추가 한컴 대화상자가 남아 있습니다",
)
_DEADLINE_REASON_PREFIX: Final = "한컴 COM 전체 제한시간을 초과했습니다"


def _reason_tokens(reason: str) -> frozenset[str]:
    return frozenset(part.strip() for part in reason.split(";") if part.strip())


def _reason_count(tokens: frozenset[str], name: str) -> int:
    """전송 실패 사유에 실린 수치 토큰을 읽는다.

    마감으로 끊긴 작업도 실제로는 명령을 실행했을 수 있다. 그 수를 자유문에만
    두면 결과는 "0개 실행"이라고 말하게 되므로, 구조화 필드로 옮긴다.
    """

    prefix = f"{name}="
    for token in tokens:
        if not token.startswith(prefix):
            continue
        try:
            value = int(token[len(prefix) :])
        except ValueError:
            return 0
        return max(0, value)
    return 0


def _is_internal_pre_mutation_wrapper(error: HwpLiveError) -> bool:
    cause = error.__cause__
    return isinstance(cause, HwpLiveError) and error.reason == (
        f"{cause.reason}; mutation_started=false"
    )


def _table_candidate(
    candidate: WorkflowTargetCandidate,
) -> WorkflowTargetCandidate | None:
    table_index = candidate.table_index
    if table_index is None:
        return None
    return candidate.model_copy(
        update={
            "candidate_id": f"table:{candidate.page}:{table_index}",
            "kind": "table",
            "picture_index": None,
        }
    )


def _generated_candidates(
    result: OperationResult,
    workflow: ProductionWorkflowId,
) -> tuple[WorkflowTargetCandidate, ...]:
    existing = tuple(
        normalized
        for candidate in result.target_candidates[:3]
        if (normalized := _table_candidate(candidate)) is not None
    )
    if existing:
        return existing
    routing = result.routing_context
    if routing is None:
        return ()
    if workflow in _TABLE_TARGET_WORKFLOWS:
        return tuple(
            WorkflowTargetCandidate(
                candidate_id=f"table:{routing.page}:{index}",
                kind="table",
                page=routing.page,
                table_index=index,
            )
            for index in range(1, min(routing.table_count, 3) + 1)
        )
    if workflow == "image.replace":
        return tuple(
            WorkflowTargetCandidate(
                candidate_id=f"picture:{routing.page}:{index}",
                kind="picture",
                page=routing.page,
                picture_index=index,
            )
            for index in range(1, min(routing.picture_count, 3) + 1)
        )
    return ()


def _target_is_missing(result: OperationResult) -> bool:
    return any(
        field in {"inputs.target", "inputs.target.control_instance_id"}
        for field in result.required_inputs
    )


def _normalized_status(
    result: OperationResult,
    candidates: tuple[WorkflowTargetCandidate, ...],
) -> OperationStatus:
    if (
        result.status == "needs_input"
        and _target_is_missing(result)
        and len(candidates) > 1
    ):
        return "ambiguous"
    if result.status == "operation_failed" and _has_partial_change(result):
        return "partial_change"
    return result.status


def _has_partial_change(result: OperationResult) -> bool:
    return any(
        (
            result.partial_change,
            result.partial_mutation is True,
            (result.commands_completed or 0) > 0,
            (result.commands_executed or 0) > 0,
            bool(result.updated_addresses),
            bool(result.created_control_ids),
            (result.blocks_applied or 0) > 0,
        )
    )


def _successful_change(result: OperationResult) -> bool:
    if result.status != "executed":
        return False
    return _has_partial_change(result)


def _failure_stage(status: OperationStatus, result: OperationResult) -> str | None:
    if status == "needs_input" or status == "schema_conflict":
        return "input_validation"
    if status == "ambiguous":
        return "target_resolution"
    if status == "not_found":
        return "routing"
    if status == "unsupported":
        return "capability"
    if status == "known_failure":
        return "certification"
    if status in {"needs_guard", "confirmation_required", "blocked"}:
        return "guard"
    if status in {
        "operation_in_progress",
        "operation_stale",
        "operation_aborted",
        "operation_reconciled",
        "request_id_conflict",
    }:
        return "idempotency"
    if status == "operation_failed" or status == "partial_change":
        return result.failed_step or "native_execution"
    if status == "transport_error":
        return result.failure_stage or "transport"
    return None


def normalize_production_result(
    result: OperationResult,
    inputs: HwpOperateInputs,
) -> OperationResult:
    workflow = production_workflow(inputs)
    candidates = _generated_candidates(result, workflow)
    status = _normalized_status(result, candidates)
    retryable = (
        status == "needs_input" or status == "ambiguous" or result.retry_safe is True
    )
    missing_fields = (
        ("target.candidate_id",)
        if status == "ambiguous"
        else public_missing_fields(workflow, result.required_inputs)
        if status == "needs_input"
        else ()
    )
    partial_change = status == "partial_change" or result.partial_change
    changed = partial_change or _successful_change(result)
    return result.model_copy(
        update={
            "status": status,
            "changed": changed,
            "verified": result.verified is True and status == "executed",
            "retry_safe": retryable,
            "request_id": inputs.request_id,
            "selected_operation": workflow,
            "failure_stage": _failure_stage(status, result),
            "partial_change": partial_change,
            "missing_fields": missing_fields,
            "target_candidates": candidates,
            "next_tool": production_tool_name(workflow) if retryable else None,
            "next_arguments": wrapper_arguments(workflow, inputs)
            if retryable
            else None,
        }
    )


def transport_error_result(
    inputs: HwpOperateInputs,
    error: HwpLiveError,
    *,
    intent: str | None = None,
    mutation_started: bool | None = None,
) -> OperationResult:
    workflow = canonical_workflow(inputs)
    reason_tokens = _reason_tokens(error.reason)
    dialog_context = error.reason.startswith(_DIALOG_REASON_PREFIXES) and (
        "target_modal_dialog=true" in reason_tokens
    )
    target_process_lost = isinstance(error, HwpTargetProcessLostError)
    structured_transport = (
        dialog_context
        or target_process_lost
        or error.reason.startswith(_DEADLINE_REASON_PREFIX)
    )
    reconcile_required = (
        structured_transport and "reconcile_required=true" in reason_tokens
    )
    dialog_detected = dialog_context and "dialog_detected=true" in reason_tokens
    dialog_user_action_required = (
        dialog_detected and "dialog_user_action_required=true" in reason_tokens
    )
    pre_mutation_marker = _is_internal_pre_mutation_wrapper(error) or (
        structured_transport and "mutation_started=false" in reason_tokens
    )
    if mutation_started is False:
        mutation_evidence = False
    elif error.mutation_started is not None:
        mutation_evidence = error.mutation_started
    elif pre_mutation_marker:
        mutation_evidence = False
    else:
        mutation_evidence = mutation_started
    conservative_change = mutation_evidence is not False
    # An unchanged document is normally the whole case for offering a retry.
    # `safe_to_repeat=False` is how a failure says that rule does not hold for
    # it — 한/글's undo stack moved even though the document did not, so a retry
    # spends another of its steps. It can only subtract: nothing here can hand
    # out retry-safety that the mutation evidence did not already earn.
    return OperationResult(
        status="transport_error",
        changed=conservative_change,
        verified=False,
        commands_executed=_reason_count(reason_tokens, "commands_completed"),
        retry_safe=(
            not conservative_change
            and not reconcile_required
            and not dialog_user_action_required
            and error.safe_to_repeat is not False
        ),
        reconcile_required=reconcile_required,
        request_id=inputs.request_id,
        selected_operation=workflow,
        query=intent or workflow or "hwp_operate",
        registry_entries=1,
        lookup_microseconds=0,
        failure_stage=(
            "dialog"
            if dialog_detected and dialog_user_action_required
            else "process_lost"
            if target_process_lost
            else "transport"
            if conservative_change
            else "connection"
        ),
        partial_change=conservative_change,
        partial_mutation=mutation_evidence,
        missing_fields=(),
        target_candidates=(),
        next_tool=None,
        next_arguments=None,
        message=str(error),
    )

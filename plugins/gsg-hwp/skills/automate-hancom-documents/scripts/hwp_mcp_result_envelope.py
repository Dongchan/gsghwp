from __future__ import annotations

from typing import Final

from hwp_errors import HwpLiveError
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
    if result.status == "needs_input" and _target_is_missing(result) and len(candidates) > 1:
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
        status == "needs_input"
        or status == "ambiguous"
        or result.retry_safe is True
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
            "verified": status == "executed",
            "retry_safe": retryable,
            "request_id": inputs.request_id,
            "selected_operation": workflow,
            "failure_stage": _failure_stage(status, result),
            "partial_change": partial_change,
            "missing_fields": missing_fields,
            "target_candidates": candidates,
            "next_tool": production_tool_name(workflow) if retryable else None,
            "next_arguments": wrapper_arguments(workflow, inputs) if retryable else None,
        }
    )


def transport_error_result(
    inputs: HwpOperateInputs,
    error: HwpLiveError,
    *,
    intent: str | None = None,
    mutation_started: bool = True,
) -> OperationResult:
    workflow = canonical_workflow(inputs)
    return OperationResult(
        status="transport_error",
        changed=mutation_started,
        verified=False,
        retry_safe=not mutation_started,
        request_id=inputs.request_id,
        selected_operation=workflow,
        query=intent or workflow or "hwp_operate",
        registry_entries=1,
        lookup_microseconds=0,
        failure_stage="transport" if mutation_started else "connection",
        partial_change=mutation_started,
        missing_fields=(),
        target_candidates=(),
        next_tool=None,
        next_arguments=None,
        message=str(error),
    )

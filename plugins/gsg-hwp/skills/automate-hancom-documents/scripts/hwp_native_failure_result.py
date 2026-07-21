from __future__ import annotations

from hwp_live_native_action_contract import NativeActionFailure
from hwp_operation_contract import OperationResult
from hwp_operation_registry import operation_registry


def native_action_failure_result(
    query: str,
    failure: NativeActionFailure,
) -> OperationResult:
    return OperationResult(
        status="operation_failed",
        query=query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        message=str(failure),
        execution_mode="native_in_process",
        native_protocol=9,
        verification="native_action_result",
        verified=False,
        commands_executed=failure.commands_completed,
        modified=failure.partial_mutation,
        structure_digest_before=failure.structure_digest_before,
        structure_digest_after=failure.structure_digest_after,
        partial_mutation=failure.partial_mutation,
        retry_safe=failure.retry_safe,
        failed_step=failure.failed_step,
        commands_completed=failure.commands_completed,
    )

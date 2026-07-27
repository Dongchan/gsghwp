from __future__ import annotations

from typing import Final, Literal

from hwp_live_native_action_contract import NativeActionFailure
from hwp_operation_contract import OperationResult
from hwp_operation_registry import operation_registry
from hwp_operation_verification import classify_native_failure_mutation


ATOMIC_ACTION_MINIMUM_NATIVE_PROTOCOL: Final = 9


def native_action_failure_result(
    query: str,
    failure: NativeActionFailure,
    *,
    minimum_native_protocol: Literal[9, 10, 11, 12] = (
        ATOMIC_ACTION_MINIMUM_NATIVE_PROTOCOL
    ),
) -> OperationResult:
    # OperationResult.native_protocol keeps its public compatibility name. Its
    # value is the minimum bridge protocol required by this execution path, not
    # the runtime bridge's reported ProtocolVersion.
    mutation_state = classify_native_failure_mutation(
        commands_completed=failure.commands_completed,
        partial_mutation=failure.partial_mutation,
        retry_safe=failure.retry_safe,
        structure_digest_before=failure.structure_digest_before,
        structure_digest_after=failure.structure_digest_after,
    )
    partial_change = mutation_state != "unchanged"
    partial_mutation = (
        True
        if mutation_state == "changed"
        else False
        if mutation_state == "unchanged"
        else None
    )
    # Production normalization historically treats any completed command as a
    # mutation. Suppress only the count of a proven non-mutating prefix; native
    # mutation evidence remains authoritative for changed and uncertain failures.
    reported_commands = failure.commands_completed if partial_change else 0
    return OperationResult(
        status="partial_change" if partial_change else "operation_failed",
        changed=partial_change,
        query=query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        message=str(failure),
        execution_mode="native_in_process",
        native_protocol=minimum_native_protocol,
        verification="native_action_result",
        verified=False,
        commands_executed=reported_commands,
        modified=partial_change,
        structure_digest_before=failure.structure_digest_before,
        structure_digest_after=failure.structure_digest_after,
        partial_change=partial_change,
        partial_mutation=partial_mutation,
        retry_safe=not partial_change,
        reconcile_required=partial_change,
        failed_step=failure.failed_step,
        commands_completed=reported_commands,
    )

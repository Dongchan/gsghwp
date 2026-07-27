from __future__ import annotations

from typing import Literal

from hwp_operation_contract import HwpWorkflowId, OperationResult
from hwp_operation_descriptor import (
    ATOMIC_ACTION_VERIFICATION_MODES,
    OperationVerificationMode,
    operation_descriptor,
)


NativeFailureMutationState = Literal["unchanged", "changed", "uncertain"]


def classify_native_failure_mutation(
    *,
    commands_completed: int,
    partial_mutation: bool | None,
    retry_safe: bool | None,
    structure_digest_before: str | None,
    structure_digest_after: str | None,
) -> NativeFailureMutationState:
    # The native executor derives partial_mutation from CommandMayMutate for each
    # completed command. Command count is progress evidence, not mutation evidence.
    if partial_mutation is True:
        return "changed"
    if partial_mutation is not False or retry_safe is not True:
        return "uncertain"

    digests_present = (
        structure_digest_before is not None and structure_digest_after is not None
    )
    if digests_present:
        return (
            "unchanged"
            if structure_digest_before == structure_digest_after
            else "uncertain"
        )
    if commands_completed > 0:
        return "uncertain"
    if structure_digest_before is not None or structure_digest_after is not None:
        return "uncertain"
    return "unchanged"


def _approved_verification_modes(
    workflow_id: HwpWorkflowId | None,
) -> tuple[OperationVerificationMode, ...]:
    if workflow_id is None:
        return ATOMIC_ACTION_VERIFICATION_MODES
    descriptor = operation_descriptor(workflow_id)
    return () if descriptor is None else descriptor.verification_modes


def enforce_operation_verification(
    workflow_id: HwpWorkflowId | None,
    result: OperationResult,
) -> OperationResult:
    verification_modes = _approved_verification_modes(workflow_id)
    verified = (
        result.status == "executed"
        and result.verified is True
        and result.verification is not None
        and result.verification in verification_modes
        and not result.partial_change
        and result.partial_mutation is not True
        and not result.reconcile_required
    )
    if result.verified is verified:
        return result
    return result.model_copy(update={"verified": verified})

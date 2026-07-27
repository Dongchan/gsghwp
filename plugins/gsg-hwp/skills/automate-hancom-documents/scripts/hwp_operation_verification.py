from __future__ import annotations

from typing import Final, Literal

from hwp_operation_contract import HwpWorkflowId, OperationResult
from hwp_operation_descriptor import (
    ATOMIC_ACTION_VERIFICATION_MODES,
    OperationVerificationMode,
    operation_descriptor,
)


NativeFailureMutationState = Literal["unchanged", "changed", "uncertain"]

# Save verdicts come from the file fingerprint rather than a readback, so they
# keep the strict two-value rule. Mirrors _SAVE_WORKFLOWS in
# hwp_operation_idempotency, which refuses to interpret a None on these.
_SAVE_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {"document.save", "document.save_reopen_verify"}
)


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


def _preserves_missing_evidence(
    workflow_id: HwpWorkflowId | None,
    result: OperationResult,
    verification_modes: tuple[OperationVerificationMode, ...],
) -> bool:
    # `verified is None` means the operation collected no evidence at all. That
    # is not a claim of success, so there is nothing to demote — but it stays
    # None only when every condition this function polices is otherwise met.
    # Anything else falls through to the unchanged rule and becomes False.
    # This preserves None by returning the result untouched, so None can never
    # be promoted to True.
    if result.verified is not None:
        return False
    if workflow_id in _SAVE_WORKFLOWS:
        return False
    return (
        result.status == "executed"
        and result.verification is not None
        and result.verification in verification_modes
        and not result.partial_change
        and result.partial_mutation is not True
        and not result.reconcile_required
    )


def enforce_operation_verification(
    workflow_id: HwpWorkflowId | None,
    result: OperationResult,
) -> OperationResult:
    verification_modes = _approved_verification_modes(workflow_id)
    if _preserves_missing_evidence(workflow_id, result, verification_modes):
        return result
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

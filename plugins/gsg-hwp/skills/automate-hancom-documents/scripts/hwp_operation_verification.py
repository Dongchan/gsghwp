from __future__ import annotations

from hwp_operation_contract import HwpWorkflowId, OperationResult
from hwp_operation_descriptor import operation_descriptor


def enforce_operation_verification(
    workflow_id: HwpWorkflowId | None,
    result: OperationResult,
) -> OperationResult:
    descriptor = (
        None if workflow_id is None else operation_descriptor(workflow_id)
    )
    verified = (
        result.status == "executed"
        and result.verified is True
        and descriptor is not None
        and result.verification is not None
        and result.verification in descriptor.verification_modes
        and not result.partial_change
        and result.partial_mutation is not True
        and not result.reconcile_required
    )
    if result.verified is verified:
        return result
    return result.model_copy(update={"verified": verified})

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from hwp_errors import HwpLiveError
from hwp_mcp_result_envelope import normalize_production_result, transport_error_result
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
    canonical_workflow,
)


class HwpOperateDispatcher(Protocol):
    async def hwp_operate(
        self,
        intent: str,
        inputs: HwpOperateInputs | None = None,
        guards: HwpOperateGuards | None = None,
    ) -> OperationResult: ...


def new_wrapper_request_id() -> str:
    return f"hwp-wrapper-{uuid4().hex}"


async def dispatch_wrapper(
    operation: HwpOperateDispatcher,
    inputs: HwpOperateInputs,
) -> OperationResult:
    workflow = canonical_workflow(inputs)
    assert workflow is not None
    try:
        result = await operation.hwp_operate(workflow, inputs)
    except HwpLiveError as error:
        return transport_error_result(inputs, error)
    return normalize_production_result(result, inputs)

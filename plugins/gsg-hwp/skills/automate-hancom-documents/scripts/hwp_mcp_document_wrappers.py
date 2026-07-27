from __future__ import annotations

from typing import final

from hwp_mcp_wrapper_inputs import HwpStyleCopyInput
from hwp_mcp_wrappers import (
    HwpOperateDispatcher,
    compact_structured_result,
    dispatch_wrapper,
    new_wrapper_request_id,
)
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    OperationResult,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs


@final
class McpDocumentRecipeWrappers:
    __slots__ = ("_operation",)

    def __init__(self, operation: HwpOperateDispatcher) -> None:
        self._operation = operation

    async def hwp_copy_style(
        self,
        style: HwpStyleCopyInput,
    ) -> OperationResult:
        result = await dispatch_wrapper(
            self._operation,
            HwpOperateInputs(
                request_id=new_wrapper_request_id(),
                operation="style.copy",
                recipe=HwpPriorityRecipeInputs(
                    source_position=style.source_position,
                    target_position=style.target_position,
                    style_copy_type=style.style_copy_type,
                ),
                policy=HwpOperatePolicy(),
                postconditions=HwpOperatePostconditions(),
            ),
        )
        return compact_structured_result(
            result,
            summary=(
                "hwp_copy_style "
                f"{result.status}: changed={result.changed} verified={result.verified}"
            ),
        )

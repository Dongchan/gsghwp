from __future__ import annotations

from typing import final

import hwp_public_action_metadata as metadata
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationInputValue,
)
from hwp_public_action_contract import (
    DOCUMENT_INPUT_ALIASES,
    PublicActionExecutor,
    PublicOperationId,
)
from hwp_public_contract import PublicActionResult, to_public_action_result


class EmptyControlSelectionError(ValueError):
    pass


@final
class HwpPublicLiveEditTools:
    __slots__ = ("_executor",)

    def __init__(self, executor: PublicActionExecutor) -> None:
        self._executor = executor

    async def _execute(
        self,
        operation: HwpWorkflowId,
        intent: str,
        operation_id: PublicOperationId,
        *,
        document_path: str | None,
        target: HwpOperateTarget | None = None,
        steps: int | None = None,
    ) -> PublicActionResult:
        parameters: dict[str, OperationInputValue] = (
            {} if steps is None else {"steps": steps}
        )
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation=operation,
            target=target,
            parameters=parameters,
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(intent, inputs, None)
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

    async def hwp_delete_page(
        self,
        *,
        operation_id: PublicOperationId,
        page: int,
        document_path: str | None = None,
    ) -> PublicActionResult:
        return await self._execute(
            "document.delete_page",
            metadata.DELETE_PAGE_INTENT,
            operation_id,
            document_path=document_path,
            target=HwpOperateTarget(kind="page", page_hint=page),
        )

    async def hwp_delete_control(
        self,
        *,
        operation_id: PublicOperationId,
        page: int,
        control_instance_ids: tuple[str, ...],
        document_path: str | None = None,
    ) -> PublicActionResult:
        if not control_instance_ids:
            raise EmptyControlSelectionError(
                "control_instance_ids must contain at least one id"
            )
        return await self._execute(
            "control.delete",
            metadata.DELETE_CONTROL_INTENT,
            operation_id,
            document_path=document_path,
            target=HwpOperateTarget(
                kind="control",
                page_hint=page,
                control_instance_ids=control_instance_ids,
            ),
        )

    async def hwp_undo(
        self,
        *,
        operation_id: PublicOperationId,
        steps: int = 1,
        document_path: str | None = None,
    ) -> PublicActionResult:
        return await self._execute(
            "document.undo",
            metadata.UNDO_INTENT,
            operation_id,
            document_path=document_path,
            steps=steps,
        )

    async def hwp_redo(
        self,
        *,
        operation_id: PublicOperationId,
        steps: int = 1,
        document_path: str | None = None,
    ) -> PublicActionResult:
        return await self._execute(
            "document.redo",
            metadata.REDO_INTENT,
            operation_id,
            document_path=document_path,
            steps=steps,
        )

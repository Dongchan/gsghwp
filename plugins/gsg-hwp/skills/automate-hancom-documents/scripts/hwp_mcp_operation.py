from __future__ import annotations

from typing import final

from hwp_live_bridge import HancomBridge
from hwp_live_contract import ConnectedDocument, MutationResult
from hwp_mcp_dispatch import McpThreadDispatcher
from hwp_mcp_operation_contract import ProductionHwpOperateInputs
from hwp_mcp_operation_executor import HwpOperationExecutor
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
)
from hwp_public_action_contract import PublicOperationId


@final
class McpOperationHandler:
    __slots__ = (
        "_bridge",
        "_dispatcher",
        "_executor",
    )

    def __init__(
        self,
        bridge: HancomBridge,
        dispatcher: McpThreadDispatcher,
        executor: HwpOperationExecutor,
    ) -> None:
        self._bridge = bridge
        self._dispatcher = dispatcher
        self._executor = executor

    async def hwp_connect(self, selector: str | None = None) -> ConnectedDocument:
        return await self._executor.ensure_connection(selector)

    async def hwp_operate_production(
        self,
        intent: str,
        inputs: ProductionHwpOperateInputs | None = None,
        guards: HwpOperateGuards | None = None,
    ) -> OperationResult:
        canonical_inputs = None if inputs is None else inputs.to_canonical_inputs()
        return await self.hwp_operate(intent, canonical_inputs, guards)

    async def hwp_operate(
        self,
        intent: str,
        inputs: HwpOperateInputs | None = None,
        guards: HwpOperateGuards | None = None,
    ) -> OperationResult:
        requested = HwpOperateInputs() if inputs is None else inputs
        return await self._executor.execute(intent, requested, guards)

    async def hwp_get_operation_status(
        self,
        operation_id: PublicOperationId,
        document_path: str | None = None,
    ) -> OperationResult:
        return await self._executor.get_operation_status(
            operation_id,
            document_path,
        )

    async def hwp_disconnect(self, session_id: str | None = None) -> MutationResult:
        return await self._dispatcher.run_mutation(
            self._bridge.disconnect,
            session_id,
        )

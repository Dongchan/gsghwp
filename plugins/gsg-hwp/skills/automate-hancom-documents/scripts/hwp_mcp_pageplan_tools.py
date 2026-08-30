from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, final

from anyio import to_thread

from hwp_pageplan_g03 import compile_page_plan
from hwp_pageplan_g03_contract import G03CompileRequest, G03CompileResponse
from hwp_pageplan_g04_contract import G04ApplyRequest, G04ApplyResponse

if TYPE_CHECKING:
    from hwp_mcp_operation_executor import HwpOperationExecutor


@final
class HwpPagePlanTools:
    __slots__ = ("_executor",)

    def __init__(self, executor: HwpOperationExecutor) -> None:
        self._executor = executor

    async def hwp_compile_page_plan(
        self,
        *,
        request: G03CompileRequest,
    ) -> G03CompileResponse:
        return await to_thread.run_sync(partial(compile_page_plan, request))

    async def hwp_apply_page_plan(
        self,
        *,
        request: G04ApplyRequest,
    ) -> G04ApplyResponse:
        return await self._executor.apply_page_plan(request)


__all__ = ["HwpPagePlanTools"]

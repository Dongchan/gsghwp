from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Protocol, final

from anyio import to_thread

import hwp_public_action_metadata as metadata
from hwp_layout_preflight import LayoutPreflightResult
from hwp_live_contract import LayoutPlan
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
)
from hwp_public_action_contract import (
    DOCUMENT_INPUT_ALIASES,
    PublicActionExecutor,
    PublicOperationId,
)
from hwp_public_contract import PublicActionResult, to_public_action_result
from hwp_office_excel_layout import table_block_from_excel
from hwp_report_layout import ReportPlan, report_layout_plan


class PublicDocumentExecutor(PublicActionExecutor, Protocol):
    async def preflight_layout(
        self,
        document_selector: str | None,
        plan: LayoutPlan,
    ) -> LayoutPreflightResult: ...


@final
class HwpPublicDocumentTools:
    __slots__ = ("_executor",)

    def __init__(self, executor: PublicDocumentExecutor) -> None:
        self._executor = executor

    async def hwp_preflight_layout(
        self,
        *,
        layout: LayoutPlan,
        document_path: str | None = None,
    ) -> LayoutPreflightResult:
        return await self._executor.preflight_layout(document_path, layout)

    async def hwp_append_layout(
        self,
        *,
        operation_id: PublicOperationId,
        layout: LayoutPlan,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.append_layout",
            layout=layout.model_copy(
                update={
                    "target": "document_end",
                    "page": None,
                    "replace_selection": False,
                }
            ),
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(
            metadata.APPEND_LAYOUT_INTENT, inputs, None
        )
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

    async def hwp_insert_layout(
        self,
        *,
        operation_id: PublicOperationId,
        layout: LayoutPlan,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.insert_layout",
            layout=layout,
            policy=HwpOperatePolicy(
                ambiguity="return_candidates",
                atomic=False,
            ),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(
            metadata.INSERT_LAYOUT_INTENT, inputs, None
        )
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

    async def hwp_append_report(
        self,
        *,
        operation_id: PublicOperationId,
        report: ReportPlan,
        document_path: str | None = None,
    ) -> PublicActionResult:
        return await self.hwp_append_layout(
            operation_id=operation_id,
            layout=report_layout_plan(report),
            document_path=document_path,
        )

    async def hwp_append_excel_table(
        self,
        *,
        operation_id: PublicOperationId,
        excel_path: str,
        sheet_name: str | None = None,
        sheet_index: int = 0,
        cell_range: str | None = None,
        title: str | None = None,
        repeat_header: bool = True,
        preserve_excel_font: bool = False,
        preserve_excel_row_heights: bool = True,
        document_path: str | None = None,
    ) -> PublicActionResult:
        block = await to_thread.run_sync(
            partial(
                table_block_from_excel,
                Path(excel_path),
                sheet_name=sheet_name,
                sheet_index=sheet_index,
                cell_range=cell_range,
                caption=title,
                repeat_header=repeat_header,
                preserve_font=preserve_excel_font,
                preserve_row_heights=preserve_excel_row_heights,
                trim_unused_edges=True,
            )
        )
        return await self.hwp_append_layout(
            operation_id=operation_id,
            layout=LayoutPlan(target="document_end", blocks=(block,)),
            document_path=document_path,
        )

    async def hwp_save_reopen_verify(
        self,
        *,
        operation_id: PublicOperationId,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.save_reopen_verify",
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(
            metadata.SAVE_REOPEN_VERIFY_INTENT,
            inputs,
            None,
        )
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

    async def hwp_save(
        self,
        *,
        operation_id: PublicOperationId,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.save",
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(metadata.SAVE_INTENT, inputs, None)
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

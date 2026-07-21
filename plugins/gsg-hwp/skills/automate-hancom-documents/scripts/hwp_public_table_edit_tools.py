from __future__ import annotations

from typing import Annotated, assert_never, final
from uuid import uuid4

from pydantic import Field
from typing_extensions import TypeIs

import hwp_public_table_metadata as metadata
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    OperationResult,
)
from hwp_public_cell_selector import PublicCellReference
from hwp_public_contract import PublicActionResult, PublicTableTarget
from hwp_public_contract import refresh_public_target_ids, to_public_action_result
from hwp_public_table_edit_contract import (
    PublicMergeTableCellsInput,
    PublicSplitTableCellInput,
    PublicTableAlignment,
    PublicTableBorderStyle,
    PublicTableBorderWidth,
    PublicTableColor,
    PublicTableFormattingInput,
    PublicTableVerticalAlignment,
)
from hwp_public_table_edit_execution import (
    PublicTableEditExecutionRequest,
    execute_public_table_edit,
)
from hwp_public_table_edit_resolution import (
    NamedPublicCellReference,
    PublicTableEditResolutionRequest,
    ResolvedPublicTableEdit,
    resolve_public_table_edit,
)
from hwp_public_table_plan import PublicTableResolutionRequest
from hwp_public_table_target import PublicTableTargetStore
from hwp_public_table_tools import PublicTableExecutor


def _is_resolved_edit(
    value: ResolvedPublicTableEdit | PublicActionResult | OperationResult,
) -> TypeIs[ResolvedPublicTableEdit]:
    return isinstance(value, ResolvedPublicTableEdit)


@final
class HwpPublicTableEditTools:
    __slots__ = ("_executor", "_targets")

    def __init__(
        self,
        executor: PublicTableExecutor,
        targets: PublicTableTargetStore | None = None,
    ) -> None:
        self._executor = executor
        self._targets = PublicTableTargetStore() if targets is None else targets

    def _public_result(
        self,
        result: OperationResult,
        document_path: str | None,
    ) -> PublicActionResult:
        target_ids = refresh_public_target_ids(self._targets, result, document_path)
        return to_public_action_result(result, target_ids)

    async def _resolved(
        self,
        request: PublicTableEditResolutionRequest,
    ) -> ResolvedPublicTableEdit | PublicActionResult:
        resolved = await resolve_public_table_edit(
            self._executor,
            self._targets,
            request,
        )
        match resolved:
            case OperationResult() as failed:
                target = request.resolution.target
                document_path = None if target is None else target.document_path
                return self._public_result(failed, document_path)
            case _ as unreachable if not _is_resolved_edit(unreachable):
                assert_never(unreachable)
            case _:
                return resolved

    async def _execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
    ) -> PublicActionResult:
        result = await execute_public_table_edit(
            self._executor,
            self._targets,
            PublicTableEditExecutionRequest(intent, inputs),
        )
        return self._public_result(result, inputs.document)

    async def hwp_format_table(
        self,
        *,
        target: PublicTableTarget | None = None,
        cell: PublicCellReference,
        row_height_mm: Annotated[float | None, Field(ge=1, le=250)] = None,
        column_width_mm: Annotated[float | None, Field(ge=1, le=250)] = None,
        bold: bool | None = None,
        font_name: Annotated[str | None, Field(min_length=1, max_length=100)] = None,
        font_size_pt: Annotated[float | None, Field(ge=1, le=96)] = None,
        text_color: PublicTableColor | None = None,
        alignment: PublicTableAlignment = "inherit",
        vertical_alignment: PublicTableVerticalAlignment = "inherit",
        line_spacing: Annotated[int | None, Field(ge=50, le=500)] = None,
        fill_color: PublicTableColor | None = None,
        border_style: PublicTableBorderStyle | None = None,
        border_width: PublicTableBorderWidth | None = None,
        border_color: PublicTableColor | None = None,
    ) -> PublicActionResult:
        request = PublicTableEditResolutionRequest(
            resolution=PublicTableResolutionRequest(
                target=target,
                request_id=f"hwp-public-{uuid4().hex}",
                query=metadata.FORMAT_TABLE_INTENT,
            ),
            cells=(NamedPublicCellReference("cell", cell),),
        )
        resolved = await self._resolved(request)
        match resolved:
            case PublicActionResult():
                return resolved
            case _ as unreachable if not _is_resolved_edit(unreachable):
                assert_never(unreachable)
            case _:
                requested = PublicTableFormattingInput(
                    cell=resolved.addresses[0],
                    row_height_mm=row_height_mm,
                    column_width_mm=column_width_mm,
                    bold=bold,
                    font_name=font_name,
                    font_size_pt=font_size_pt,
                    text_color=text_color,
                    alignment=alignment,
                    vertical_alignment=vertical_alignment,
                    line_spacing=line_spacing,
                    fill_color=fill_color,
                    border_style=border_style,
                    border_width=border_width,
                    border_color=border_color,
                )
                return await self._execute(
                    request.resolution.query,
                    HwpOperateInputs(
                        request_id=request.resolution.request_id,
                        document=resolved.target.document_path,
                        operation="table.format",
                        target=resolved.target.target,
                        parameters=dict(requested.to_parameters()),
                        policy=HwpOperatePolicy(ambiguity="return_candidates"),
                        postconditions=HwpOperatePostconditions(verify_structure=True),
                    ),
                )

    async def hwp_merge_table_cells(
        self,
        *,
        target: PublicTableTarget | None = None,
        start_cell: PublicCellReference,
        end_cell: PublicCellReference,
    ) -> PublicActionResult:
        request = PublicTableEditResolutionRequest(
            resolution=PublicTableResolutionRequest(
                target=target,
                request_id=f"hwp-public-{uuid4().hex}",
                query=metadata.MERGE_TABLE_CELLS_INTENT,
            ),
            cells=(
                NamedPublicCellReference("start_cell", start_cell),
                NamedPublicCellReference("end_cell", end_cell),
            ),
            unique_cells_required=True,
        )
        resolved = await self._resolved(request)
        match resolved:
            case PublicActionResult():
                return resolved
            case _ as unreachable if not _is_resolved_edit(unreachable):
                assert_never(unreachable)
            case _:
                requested = PublicMergeTableCellsInput(
                    start_cell=resolved.addresses[0],
                    end_cell=resolved.addresses[1],
                )
                return await self._execute(
                    request.resolution.query,
                    HwpOperateInputs(
                        request_id=request.resolution.request_id,
                        document=resolved.target.document_path,
                        operation="table.merge_cells",
                        target=resolved.target.target,
                        parameters=dict(requested.to_parameters()),
                        policy=HwpOperatePolicy(ambiguity="return_candidates"),
                        postconditions=HwpOperatePostconditions(verify_structure=True),
                    ),
                )

    async def hwp_split_table_cell(
        self,
        *,
        target: PublicTableTarget | None = None,
        cell: PublicCellReference,
        columns: Annotated[int, Field(ge=1, le=65_535)],
        rows: Annotated[int, Field(ge=1, le=65_535)],
        distribute_height: bool = False,
    ) -> PublicActionResult:
        request = PublicTableEditResolutionRequest(
            resolution=PublicTableResolutionRequest(
                target=target,
                request_id=f"hwp-public-{uuid4().hex}",
                query=metadata.SPLIT_TABLE_CELL_INTENT,
            ),
            cells=(NamedPublicCellReference("cell", cell),),
        )
        resolved = await self._resolved(request)
        match resolved:
            case PublicActionResult():
                return resolved
            case _ as unreachable if not _is_resolved_edit(unreachable):
                assert_never(unreachable)
            case _:
                requested = PublicSplitTableCellInput(
                    cell=resolved.addresses[0],
                    columns=columns,
                    rows=rows,
                    distribute_height=distribute_height,
                )
                return await self._execute(
                    request.resolution.query,
                    HwpOperateInputs(
                        request_id=request.resolution.request_id,
                        document=resolved.target.document_path,
                        operation="table.split_cells",
                        target=resolved.target.target,
                        parameters=dict(requested.to_parameters()),
                        policy=HwpOperatePolicy(ambiguity="return_candidates"),
                        postconditions=HwpOperatePostconditions(verify_structure=True),
                    ),
                )

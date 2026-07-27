from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Protocol, final
from pydantic import Field

from hwp_live_structure_contract import DocumentStructure
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    OperationResult,
)
from hwp_public_action_contract import PublicOperationId
from hwp_public_contract import (
    PublicActionResult,
    PublicTableTarget,
    refresh_public_target_ids,
    to_public_action_result,
)
from hwp_public_table_plan import (
    PublicTableResolutionRequest,
    ResolvedPublicTable,
    resolve_public_table,
)
from hwp_public_table_target import (
    CanonicalPublicTableTarget,
    PublicTableDataInput,
    PublicTableTargetStore,
    UnknownPublicTargetError,
    unknown_public_target_result,
)


_FILL_TABLE_INTENT: Final = "기존 표 채우기"
_FILL_TABLE_INPUT_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "inputs.target": "target_id",
        "inputs.policy.numeric_value_mode": "cells",
    }
)


class PublicOperationExecutor(Protocol):
    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult: ...

    async def read_table_structure(
        self,
        document_path: str | None,
        page: int,
    ) -> DocumentStructure: ...


@final
class HwpPublicTools:
    __slots__ = ("_executor", "_targets")

    def __init__(
        self,
        executor: PublicOperationExecutor,
        targets: PublicTableTargetStore | None = None,
    ) -> None:
        self._executor = executor
        self._targets = PublicTableTargetStore() if targets is None else targets

    async def hwp_fill_table(
        self,
        operation_id: PublicOperationId,
        records: Annotated[
            list[dict[str, str]] | None,
            Field(description="표의 실제 머리글 이름을 키로 사용하는 레코드 목록"),
        ] = None,
        cells: dict[str, str] | None = None,
        rows: Annotated[
            list[list[str]] | None,
            Field(
                description="start_cell 또는 현재 한컴 표 셀·선택 셀 블록에서 시작하는 행렬"
            ),
        ] = None,
        start_cell: Annotated[
            str | None,
            Field(
                description="rows의 명시 시작 셀. 생략하면 현재 한컴 표 셀·선택 셀 블록·선택 표를 사용"
            ),
        ] = None,
        fill_blanks_only: bool = False,
        document_path: str | None = None,
        page: int | None = None,
        caption: str | None = None,
        headers: Annotated[
            tuple[str, ...],
            Field(
                description=(
                    "대상 표에 실제 표시된 머리글만 받는 선택 필터. "
                    "records 키를 복사하지 말고 불확실하면 생략합니다."
                )
            ),
        ] = (),
        table_index: int | None = None,
        target_id: str | None = None,
    ) -> PublicActionResult:
        target = PublicTableTarget(
            document_path=document_path,
            page=page,
            caption=caption,
            headers=headers,
            table_index=table_index,
            target_id=target_id,
        )
        requested = PublicTableDataInput(
            target=target,
            records=None if records is None else tuple(records),
            cells=cells,
            rows=(
                None
                if rows is None
                else tuple(tuple(value for value in row) for row in rows)
            ),
            start_cell=start_cell,
            fill_blanks_only=fill_blanks_only,
        )
        request_id = operation_id
        selected_id = target.target_id
        if target.target_id is None and (
            target.page is not None
            or target.caption is not None
            or bool(target.headers)
            or target.table_index is not None
        ):
            resolved = await resolve_public_table(
                self._executor,
                self._targets,
                PublicTableResolutionRequest(
                    target=target,
                    request_id=request_id,
                    query=_FILL_TABLE_INTENT,
                ),
            )
            match resolved:  # noqa: E501  # noqa: MATCH_OK — closed union is exhaustive.
                case ResolvedPublicTable() as table:
                    selected = CanonicalPublicTableTarget(
                        table.document_path,
                        table.target,
                    )
                    selected_id = table.table.control_instance_id
                case OperationResult() as failed:
                    target_ids = refresh_public_target_ids(
                        self._targets,
                        failed,
                        document_path,
                    )
                    return to_public_action_result(
                        failed,
                        target_ids,
                        _FILL_TABLE_INPUT_ALIASES,
                    )
        else:
            try:
                selected = self._targets.resolve(target)
            except UnknownPublicTargetError as error:
                failed = unknown_public_target_result(
                    error,
                    request_id=request_id,
                    query=_FILL_TABLE_INTENT,
                )
                target_ids = refresh_public_target_ids(
                    self._targets,
                    failed,
                    document_path,
                )
                return to_public_action_result(
                    failed,
                    target_ids,
                    _FILL_TABLE_INPUT_ALIASES,
                )
        data = requested.to_canonical_data()
        record_count = max(len(data.records), len(data.cells), len(data.rows))
        inputs = HwpOperateInputs(
            request_id=request_id,
            document=selected.document_path,
            operation="table.fill_existing",
            target=selected.target,
            data=data,
            policy=HwpOperatePolicy(
                preserve_style=True,
                fill_blanks_only=requested.fill_blanks_only,
                ambiguity="return_candidates",
            ),
            postconditions=HwpOperatePostconditions(
                record_count=record_count,
                verify_structure=True,
            ),
        )
        result = await self._executor.execute(_FILL_TABLE_INTENT, inputs, None)
        target_ids = refresh_public_target_ids(
            self._targets,
            result,
            selected.document_path,
        )
        return to_public_action_result(
            result,
            target_ids,
            _FILL_TABLE_INPUT_ALIASES,
            selected_target_id=selected_id,
        )

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Protocol, final
from uuid import uuid4

from pydantic import Field

from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    OperationResult,
)
from hwp_public_contract import (
    PublicActionResult,
    PublicTableTarget,
    refresh_public_target_ids,
    to_public_action_result,
)
from hwp_public_table_target import (
    PublicTableDataInput,
    PublicTableTargetStore,
    UnknownPublicTargetError,
    unknown_public_target_result,
)


_FILL_TABLE_INTENT: Final = "기존 표 채우기"
_FILL_TABLE_INPUT_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {"inputs.target": "target_id"}
)


class PublicOperationExecutor(Protocol):
    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult: ...


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
        records: Annotated[
            list[dict[str, str]] | None,
            Field(description="표의 실제 머리글 이름을 키로 사용하는 레코드 목록"),
        ] = None,
        cells: dict[str, str] | None = None,
        rows: Annotated[
            list[list[str]] | None,
            Field(description="사용자가 start_cell을 명시한 경우에만 쓰는 행렬"),
        ] = None,
        start_cell: Annotated[
            str | None,
            Field(description="rows와 함께 사용하며 추측해서 만들지 않는 시작 셀 주소"),
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
        request_id = f"hwp-public-{uuid4().hex}"
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
        )

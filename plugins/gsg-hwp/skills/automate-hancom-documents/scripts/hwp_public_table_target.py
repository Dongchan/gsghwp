from __future__ import annotations

from dataclasses import dataclass
from typing import Final, final
from uuid import uuid4

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError
from hwp_live_values import ContractModel
from hwp_live_structure_contract import FastPageInspection
from hwp_operation_contract import (
    HwpOperateData,
    HwpOperateTarget,
    OperationResult,
    WorkflowTargetCandidate,
)
from hwp_operation_registry import operation_registry
from hwp_public_contract import PublicTableTarget


_MAX_INSPECTED_TABLE_TARGETS: Final = 256


class PublicTableDataInput(ContractModel):
    target: PublicTableTarget | None = None
    records: tuple[dict[str, str], ...] | None = Field(
        default=None,
        min_length=1,
        max_length=20_000,
    )
    cells: dict[str, str] | None = Field(
        default=None,
        min_length=1,
        max_length=20_000,
    )
    rows: tuple[tuple[str, ...], ...] | None = Field(
        default=None,
        min_length=1,
        max_length=20_000,
    )
    start_cell: str | None = Field(default=None, min_length=2, max_length=20)
    fill_blanks_only: bool = False

    @model_validator(mode="after")
    def require_one_data_format(self) -> PublicTableDataInput:
        modes = sum(
            value is not None for value in (self.records, self.cells, self.rows)
        )
        if modes != 1:
            raise PydanticCustomError(
                "public_table_data_mode",
                "records, cells, rows 중 정확히 하나를 전달하세요",
            )
        return self

    def to_canonical_data(self) -> HwpOperateData:
        if self.records is not None:
            return HwpOperateData(records=self.records)
        if self.cells is not None:
            return HwpOperateData(cells=self.cells)
        assert self.rows is not None
        return HwpOperateData(rows=self.rows, start_cell=self.start_cell)


@dataclass(frozen=True, slots=True)
class CanonicalPublicTableTarget:
    document_path: str | None
    target: HwpOperateTarget


@final
class UnknownPublicTargetError(ValueError):
    __slots__ = ("target_id",)

    def __init__(self, target_id: str) -> None:
        self.target_id = target_id
        prefix = "현재 hwp_inspect_page_fast 조회 결과나 앞선 후보 응답에서 "
        super().__init__(prefix + f"확인되지 않은 target_id입니다: {target_id}")


@final
class PublicTableTargetStore:
    __slots__ = ("_inspected_targets", "_targets")

    def __init__(self) -> None:
        self._targets: dict[str, CanonicalPublicTableTarget] = {}
        self._inspected_targets: dict[str, CanonicalPublicTableTarget] = {}

    def resolve(
        self,
        target: PublicTableTarget | None,
    ) -> CanonicalPublicTableTarget:
        if target is None:
            return CanonicalPublicTableTarget(None, _active_table_target())
        if target.target_id is not None:
            resolved = self._targets.get(
                target.target_id
            ) or self._inspected_targets.get(target.target_id)
            if resolved is None:
                if target.target_id.startswith("hwp-target-"):
                    raise UnknownPublicTargetError(target.target_id)
                return CanonicalPublicTableTarget(
                    target.document_path,
                    HwpOperateTarget(
                        kind="table",
                        binding="active",
                        match_policy="return_candidates",
                        control_instance_id=target.target_id,
                    ),
                )
            return resolved
        return CanonicalPublicTableTarget(
            target.document_path,
            HwpOperateTarget(
                kind="table",
                binding="active",
                page_hint=target.page,
                table_index=target.table_index,
                caption_contains=target.caption,
                header_signature=target.headers,
                match_policy="return_candidates",
            ),
        )

    def remember(
        self,
        candidates: tuple[WorkflowTargetCandidate, ...],
        document_path: str | None,
    ) -> tuple[str, ...]:
        if not candidates:
            return ()
        self._targets.clear()
        target_ids: list[str] = []
        for candidate in candidates[:3]:
            assert candidate.table_index is not None
            target_id = f"hwp-target-{uuid4().hex}"
            self._targets[target_id] = CanonicalPublicTableTarget(
                document_path,
                HwpOperateTarget(
                    kind="table",
                    binding="active",
                    page_hint=candidate.page,
                    table_index=candidate.table_index,
                    match_policy="return_candidates",
                ),
            )
            target_ids.append(target_id)
        return tuple(target_ids)

    def remember_inspection(self, inspection: FastPageInspection) -> None:
        self._inspected_targets.clear()
        table_index = 0
        for control in inspection.controls:
            if control.control_type != "tbl":
                continue
            table_index += 1
            if table_index > _MAX_INSPECTED_TABLE_TARGETS:
                break
            self._inspected_targets[control.instance_id] = CanonicalPublicTableTarget(
                inspection.full_name,
                HwpOperateTarget(
                    kind="table",
                    binding="active",
                    page_hint=inspection.page,
                    table_index=table_index,
                    match_policy="return_candidates",
                    control_instance_id=control.instance_id,
                ),
            )

    def clear(self) -> None:
        self._targets.clear()
        self._inspected_targets.clear()


def _active_table_target() -> HwpOperateTarget:
    return HwpOperateTarget(
        kind="table",
        binding="active",
        match_policy="return_candidates",
    )


def unknown_public_target_result(
    error: UnknownPublicTargetError,
    *,
    request_id: str,
    query: str,
) -> OperationResult:
    return OperationResult(
        request_id=request_id,
        status="ambiguous",
        query=query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        required_inputs=("inputs.target",),
        message=str(error),
        retry_safe=True,
    )

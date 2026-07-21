from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from typing_extensions import TypeIs

from hwp_live_structure_contract import StructureCell
from hwp_operation_contract import HwpOperateTarget, OperationResult
from hwp_operation_registry import operation_registry
from hwp_public_cell_selector import (
    PublicCellReference,
    PublicCellSelector,
    to_public_cell_selector,
)
from hwp_public_table_mapping import TablePlanInputFailure, select_table_cell
from hwp_public_table_plan import (
    PublicTableResolutionRequest,
    PublicTableStructureReader,
    ResolvedPublicTable,
    resolve_public_table,
)
from hwp_public_table_target import (
    CanonicalPublicTableTarget,
    PublicTableTargetStore,
    UnknownPublicTargetError,
    unknown_public_target_result,
)


@dataclass(frozen=True, slots=True)
class NamedPublicCellReference:
    input_name: str
    reference: PublicCellReference


@dataclass(frozen=True, slots=True)
class PublicTableEditResolutionRequest:
    resolution: PublicTableResolutionRequest
    cells: tuple[NamedPublicCellReference, ...]
    unique_cells_required: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedPublicTableEdit:
    target: CanonicalPublicTableTarget
    addresses: tuple[str, ...]


def _is_resolved_public_table(
    result: ResolvedPublicTable | OperationResult,
) -> TypeIs[ResolvedPublicTable]:
    return isinstance(result, ResolvedPublicTable)


def _is_structure_cell(
    result: StructureCell | TablePlanInputFailure,
) -> TypeIs[StructureCell]:
    return isinstance(result, StructureCell)


def _direct_address(selector: PublicCellSelector) -> str | None:
    if (
        selector.address is None
        or selector.row_offset != 0
        or selector.column_offset != 0
    ):
        return None
    return selector.address


def _needs_input_result(
    request: PublicTableResolutionRequest,
    input_name: str,
    failure: TablePlanInputFailure,
) -> OperationResult:
    return OperationResult(
        request_id=request.request_id,
        status="needs_input",
        query=request.query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        required_inputs=(f"inputs.{input_name}.{failure.field}",),
        message=failure.message,
        retry_safe=True,
    )


def _duplicate_result(
    request: PublicTableEditResolutionRequest,
) -> OperationResult:
    resolution = request.resolution
    return OperationResult(
        request_id=resolution.request_id,
        status="needs_input",
        query=resolution.query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        required_inputs=tuple(f"inputs.{cell.input_name}" for cell in request.cells),
        message="서로 다른 셀을 지정해야 합니다",
        retry_safe=True,
    )


async def resolve_public_table_edit(
    reader: PublicTableStructureReader,
    targets: PublicTableTargetStore,
    request: PublicTableEditResolutionRequest,
) -> ResolvedPublicTableEdit | OperationResult:
    selectors = tuple(
        to_public_cell_selector(cell.reference) for cell in request.cells
    )
    direct_addresses = tuple(_direct_address(selector) for selector in selectors)
    if all(address is not None for address in direct_addresses):
        try:
            canonical = targets.resolve(request.resolution.target)
        except UnknownPublicTargetError as error:
            return unknown_public_target_result(
                error,
                request_id=request.resolution.request_id,
                query=request.resolution.query,
            )
        target = canonical.target
        if request.resolution.target is None:
            target = HwpOperateTarget(
                kind="table",
                binding="selection",
                match_policy="return_candidates",
            )
        return ResolvedPublicTableEdit(
            CanonicalPublicTableTarget(canonical.document_path, target),
            tuple(address for address in direct_addresses if address is not None),
        )

    resolved = await resolve_public_table(
        reader,
        targets,
        request.resolution,
    )
    match resolved:
        case OperationResult() as failed:
            return failed
        case _ as unreachable if not _is_resolved_public_table(unreachable):
            assert_never(unreachable)
        case _:
            table = resolved
            addresses: list[str] = []
            for named, selector in zip(request.cells, selectors, strict=True):
                selected = select_table_cell(table.table, selector)
                match selected:
                    case TablePlanInputFailure() as failure:
                        return _needs_input_result(
                            request.resolution,
                            named.input_name,
                            failure,
                        )
                    case _ as unreachable if not _is_structure_cell(unreachable):
                        assert_never(unreachable)
                    case _:
                        addresses.append(selected.address)
            if (
                request.unique_cells_required
                and len(set(addresses)) != len(addresses)
            ):
                return _duplicate_result(request)
            return ResolvedPublicTableEdit(
                CanonicalPublicTableTarget(table.document_path, table.target),
                tuple(addresses),
            )

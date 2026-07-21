from __future__ import annotations

import re
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    CaptureTableCommand,
    NativeActionRequest,
    SelectControlCommand,
    SetCellTextCommand,
)
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_structure_contract import DocumentStructure, StructureCell, StructureTable
from hwp_live_workflow_table_records import plan_record_table
from hwp_live_workflow_table_resolver import (
    ResolvedWorkflowTable as ResolvedWorkflowTable,
    resolve_workflow_table as resolve_workflow_table,
    workflow_table_candidate as workflow_table_candidate,
    workflow_page as workflow_page,
)
from hwp_operation_contract import (
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
)


_ADDRESS = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class PreparedWorkflowTableFill:
    request: NativeActionRequest
    table_index: int
    control_instance_id: str
    replacements: tuple[tuple[str, str], ...]


def _cell_map(table: StructureTable) -> dict[str, StructureCell]:
    return {cell.address: cell for cell in table.cells}


def _column_letters(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _parse_address(address: str) -> tuple[int, int]:
    normalized = address.strip().upper()
    matched = _ADDRESS.fullmatch(normalized)
    if matched is None:
        raise HwpLiveError(f"한컴 표 셀 주소가 올바르지 않습니다: {address}")
    column = 0
    for character in matched.group(1):
        column = column * 26 + ord(character) - 64
    return int(matched.group(2)) - 1, column - 1


def _address(row: int, column: int) -> str:
    return f"{_column_letters(column)}{row + 1}"


def _owner_cell(
    cells: dict[str, StructureCell],
    address: str,
) -> StructureCell:
    cell = cells.get(address.upper())
    if cell is None:
        raise HwpLiveError(f"대상 한컴 표에 {address.upper()} 셀이 없습니다")
    owner = cells.get(cell.owner_address)
    if owner is None:
        raise HwpLiveError(f"대상 한컴 표의 병합 셀 소유자를 찾지 못했습니다: {address}")
    return owner


def _record_replacements(
    table: StructureTable,
    records: tuple[dict[str, str], ...],
) -> dict[str, str]:
    if not records:
        return {}
    cells = _cell_map(table)
    plan = plan_record_table(table, records)
    replacements: dict[str, str] = {}
    for row, record in zip(plan.rows, records, strict=True):
        for column in plan.columns:
            owner = _owner_cell(cells, _address(row, column.target_column))
            value = record.get(column.source_key, "")
            existing = replacements.get(owner.address)
            if existing is not None and existing != value:
                raise HwpLiveError(f"병합 셀 {owner.address}에 서로 다른 값을 입력할 수 없습니다")
            replacements[owner.address] = value
    return replacements


def _row_replacements(
    table: StructureTable,
    rows: tuple[tuple[str, ...], ...],
    start_cell: str | None,
) -> dict[str, str]:
    if not rows:
        return {}
    if start_cell is None:
        raise HwpLiveError("행렬 데이터에는 inputs.data.start_cell이 필요합니다")
    start_row, start_column = _parse_address(start_cell)
    cells = _cell_map(table)
    replacements: dict[str, str] = {}
    for row_offset, values in enumerate(rows):
        for column_offset, value in enumerate(values):
            owner = _owner_cell(
                cells,
                _address(start_row + row_offset, start_column + column_offset),
            )
            existing = replacements.get(owner.address)
            if existing is not None and existing != value:
                raise HwpLiveError(f"병합 셀 {owner.address}에 서로 다른 값을 입력할 수 없습니다")
            replacements[owner.address] = value
    return replacements


def table_fill_replacements(
    table: StructureTable,
    data: HwpOperateData,
    *,
    fill_blanks_only: bool = False,
) -> tuple[tuple[str, str], ...]:
    modes = int(bool(data.cells)) + int(bool(data.rows)) + int(bool(data.records))
    if modes != 1:
        raise HwpLiveError("inputs.data에는 cells, rows, records 중 정확히 하나를 전달하세요")
    cells = _cell_map(table)
    if data.cells:
        replacements = {
            _owner_cell(cells, address.strip().upper()).address: value
            for address, value in data.cells.items()
        }
    elif data.rows:
        replacements = _row_replacements(table, data.rows, data.start_cell)
    else:
        replacements = _record_replacements(table, data.records)
    ordered = tuple(sorted(replacements.items()))
    if not fill_blanks_only:
        return ordered
    return tuple(
        (address, value)
        for address, value in ordered
        if not _owner_cell(cells, address).text.strip()
    )


def table_fill_contract_conflict(
    target: HwpOperateTarget,
    data: HwpOperateData,
    postconditions: HwpOperatePostconditions,
) -> str | None:
    if target.kind != "table":
        return "table.fill_existing operation의 target.kind는 table이어야 합니다"
    if target.match_policy not in {"unique", "return_candidates"}:
        return "현재 인증 recipe는 unique 또는 return_candidates 표 선택만 지원합니다"
    expected = postconditions.record_count
    if expected is None:
        return None
    modes = int(bool(data.cells)) + int(bool(data.rows)) + int(bool(data.records))
    if modes != 1:
        return None
    actual = (
        len(data.cells)
        if data.cells
        else len(data.rows)
        if data.rows
        else len(data.records)
    )
    if actual != expected:
        return f"postconditions.record_count={expected}와 payload 레코드 수 {actual}가 다릅니다"
    return None


def prepare_table_fill(
    candidate: HwpDocumentCandidate,
    table: StructureTable,
    table_index: int,
    data: HwpOperateData,
    policy: HwpOperatePolicy,
    postconditions: HwpOperatePostconditions,
) -> PreparedWorkflowTableFill:
    _ = postconditions
    control_id = table.control_instance_id
    if control_id is None:
        raise HwpLiveError("네이티브 표 제어 식별자가 없습니다")
    replacements = table_fill_replacements(
        table,
        data,
        fill_blanks_only=policy.fill_blanks_only,
    )
    commands = (
        SelectControlCommand(control_id),
        CaptureTableCommand(),
        *(SetCellTextCommand(address, value) for address, value in replacements),
    )
    return PreparedWorkflowTableFill(
        request=NativeActionRequest(
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            commands=commands,
        ),
        table_index=table_index,
        control_instance_id=control_id,
        replacements=replacements,
    )


def verify_table_fill(
    snapshot: DocumentStructure,
    prepared: PreparedWorkflowTableFill,
    *,
    page_count_before: int,
    postconditions: HwpOperatePostconditions,
) -> None:
    if postconditions.preserve_page_count and snapshot.page_count != page_count_before:
        raise HwpLiveError("표 입력 후 페이지 수 보존 완료조건을 만족하지 못했습니다")
    table = next(
        (
            item
            for item in snapshot.tables
            if item.control_instance_id == prepared.control_instance_id
        ),
        None,
    )
    if table is None:
        raise HwpLiveError("입력 후 대상 표를 네이티브 구조에서 다시 찾지 못했습니다")
    cells = _cell_map(table)
    for address, expected in prepared.replacements:
        cell = cells.get(address)
        if cell is None or cell.text != expected:
            raise HwpLiveError(f"{address} 셀의 네이티브 입력 결과가 요청과 다릅니다")

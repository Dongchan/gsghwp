from __future__ import annotations

import re
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    CaptureTableCommand,
    CellCommand,
    NativeActionCommand,
    NativeActionRequest,
    RunCommand,
    SelectControlCommand,
    SetCellTextCommand,
)
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_structure_contract import StructureCell, StructureTable
from hwp_operation_contract import HwpOperateData, HwpOperatePolicy


_ADDRESS = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class ExpandedTablePlan:
    request: NativeActionRequest
    replacements: tuple[tuple[str, str], ...]
    rows_added: int


def data_record_count(data: HwpOperateData) -> int:
    if data.records:
        return len(data.records)
    if data.rows:
        return len(data.rows)
    rows: set[int] = set()
    for address in data.cells:
        row, _ = _parse_address(address)
        rows.add(row)
    return len(rows)


def _normalized(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _parse_address(address: str) -> tuple[int, int]:
    matched = _ADDRESS.fullmatch(address.strip().upper())
    if matched is None:
        raise HwpLiveError(f"한컴 표 셀 주소가 올바르지 않습니다: {address}")
    column = 0
    for character in matched.group(1):
        column = column * 26 + ord(character) - 64
    return int(matched.group(2)) - 1, column - 1


def _column_letters(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _address(row: int, column: int) -> str:
    return f"{_column_letters(column)}{row + 1}"


def _record_columns(
    table: StructureTable,
    records: tuple[dict[str, str], ...],
) -> tuple[int, dict[str, int]]:
    keys = tuple(dict.fromkeys(key for record in records for key in record))
    if not keys:
        raise HwpLiveError("확장할 레코드 필드가 비어 있습니다")
    matches: list[tuple[int, dict[str, int]]] = []
    for row in range(table.rows):
        headings = {
            _normalized(cell.text): cell.column
            for cell in table.cells
            if cell.row == row and cell.owner_address == cell.address and cell.text.strip()
        }
        columns: dict[str, int] = {}
        for key in keys:
            normalized = _normalized(key)
            exact = headings.get(normalized)
            candidates = tuple(
                column for text, column in headings.items() if normalized in text
            )
            if exact is not None:
                columns[key] = exact
            elif len(candidates) == 1:
                columns[key] = candidates[0]
        if len(columns) == len(keys):
            matches.append((row, columns))
    if len(matches) != 1:
        raise HwpLiveError("레코드 필드와 일치하는 표 머리글 행을 정확히 하나 찾지 못했습니다")
    return matches[0]


def _raw_replacements(
    table: StructureTable,
    data: HwpOperateData,
) -> dict[str, str]:
    modes = int(bool(data.cells)) + int(bool(data.rows)) + int(bool(data.records))
    if modes != 1:
        raise HwpLiveError("inputs.data에는 cells, rows, records 중 정확히 하나를 전달하세요")
    if data.cells:
        return {address.strip().upper(): value for address, value in data.cells.items()}
    if data.rows:
        if data.start_cell is None:
            raise HwpLiveError("행렬 데이터에는 inputs.data.start_cell이 필요합니다")
        start_row, start_column = _parse_address(data.start_cell)
        return {
            _address(start_row + row_offset, start_column + column_offset): value
            for row_offset, row in enumerate(data.rows)
            for column_offset, value in enumerate(row)
        }
    header_row, columns = _record_columns(table, data.records)
    return {
        _address(header_row + offset, column): record.get(key, "")
        for offset, record in enumerate(data.records, start=1)
        for key, column in columns.items()
    }


def _owner(
    table: StructureTable,
    by_address: dict[str, StructureCell],
    address: str,
) -> str:
    row, column = _parse_address(address)
    if column >= table.columns:
        raise HwpLiveError(f"대상 셀 열이 표 범위를 벗어납니다: {address}")
    if row >= table.rows:
        return address
    cell = by_address.get(address)
    if cell is None:
        raise HwpLiveError(f"대상 표에 셀이 없습니다: {address}")
    return cell.owner_address


def prepare_expand_and_fill(
    candidate: HwpDocumentCandidate,
    table: StructureTable,
    data: HwpOperateData,
    policy: HwpOperatePolicy,
) -> ExpandedTablePlan:
    control_id = table.control_instance_id
    if control_id is None:
        raise HwpLiveError("확장 대상 표의 네이티브 개체 ID가 없습니다")
    by_address = {cell.address: cell for cell in table.cells}
    replacements: dict[str, str] = {}
    for address, value in _raw_replacements(table, data).items():
        owner = _owner(table, by_address, address)
        existing = by_address.get(owner)
        if policy.fill_blanks_only and existing is not None and existing.text.strip():
            continue
        previous = replacements.get(owner)
        if previous is not None and previous != value:
            raise HwpLiveError(f"병합 셀 {owner}에 서로 다른 값을 입력할 수 없습니다")
        replacements[owner] = value
    ordered = tuple(sorted(replacements.items()))
    maximum_row = max((_parse_address(address)[0] + 1 for address, _ in ordered), default=0)
    rows_added = max(0, maximum_row - table.rows)
    last_row_cells = tuple(cell for cell in table.cells if cell.row == table.rows - 1)
    if rows_added and not last_row_cells:
        raise HwpLiveError("표의 마지막 행을 찾지 못했습니다")
    commands: list[NativeActionCommand] = [
        SelectControlCommand(control_id),
        CaptureTableCommand(),
    ]
    if rows_added:
        commands.append(CellCommand(last_row_cells[0].owner_address))
        commands.extend(RunCommand("TableAppendRow") for _ in range(rows_added))
    commands.extend(SetCellTextCommand(address, value) for address, value in ordered)
    return ExpandedTablePlan(
        NativeActionRequest(
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            commands=tuple(commands),
        ),
        ordered,
        rows_added,
    )

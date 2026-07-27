from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final, Literal

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    CaptureTableCommand,
    NativeActionCommand,
    NativeActionRequest,
    SelectControlCommand,
    SetCellTextCommand,
    TextPatchCommand,
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
from hwp_table_format_inference import infer_table_cell_edits


_ADDRESS = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")

# 네이티브 파서의 절대 한계다. ActionProtocol.cpp:21 이 kMaximumCommands 를
# 20'000 으로 두고 ActionProtocol.cpp:536-538 이 초과 요청을 BAD_REQUEST 로
# 거절한다. 어떤 표 채움 요청도 이 값을 넘겨서는 안 된다.
NATIVE_REQUEST_COMMAND_LIMIT: Final = 20_000
# 표 채움 요청은 언제나 SELECT_CONTROL, CAPTURE_TABLE 로 시작한다.
_TABLE_FILL_PREFIX_COMMANDS: Final = 2

_NATIVE_CALL_DEADLINE_MICROSECONDS: Final = 180_000_000
_NATIVE_CALL_SAFETY_FACTOR: Final = 6
_OBSERVED_SAFE_EDIT_COUNT: Final = 100
_OBSERVED_SAFE_TABLE_CELL_COUNT: Final = 100
_OBSERVED_SAFE_WORST_MICROSECONDS: Final = 24_155_000
# 위 실측 표본은 topology 를 셀마다 다시 만들던 시절의 것이다. 그때 100셀 채움의
# topology 작업량은 실행 1회 + 편집 셀마다 준비 1회로 100 * (100 + 1) 이었다.
_OBSERVED_SAFE_TOPOLOGY_WORK: Final = _OBSERVED_SAFE_TABLE_CELL_COUNT * (
    _OBSERVED_SAFE_EDIT_COUNT + 1
)
_NATIVE_CALL_TARGET_MICROSECONDS: Final = (
    _NATIVE_CALL_DEADLINE_MICROSECONDS // _NATIVE_CALL_SAFETY_FACTOR
)
_NATIVE_CALL_EDIT_BUDGET: Final = min(
    _OBSERVED_SAFE_EDIT_COUNT,
    _NATIVE_CALL_TARGET_MICROSECONDS
    * _OBSERVED_SAFE_EDIT_COUNT
    // _OBSERVED_SAFE_WORST_MICROSECONDS,
    NATIVE_REQUEST_COMMAND_LIMIT - _TABLE_FILL_PREFIX_COMMANDS,
)
_NATIVE_CALL_TOPOLOGY_WORK_BUDGET: Final = min(
    _OBSERVED_SAFE_TOPOLOGY_WORK,
    _NATIVE_CALL_TARGET_MICROSECONDS
    * _OBSERVED_SAFE_TOPOLOGY_WORK
    // _OBSERVED_SAFE_WORST_MICROSECONDS,
)


@dataclass(frozen=True, slots=True)
class PreparedWorkflowTableFill:
    request: NativeActionRequest
    table_index: int
    control_instance_id: str
    replacements: tuple[tuple[str, str], ...]
    native_protocol: Literal[9, 12]
    command_groups: tuple[tuple[NativeActionCommand, ...], ...] = ()


def _cell_map(table: StructureTable) -> dict[str, StructureCell]:
    return {cell.address: cell for cell in table.cells}


def _normalized_cell_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


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
        raise HwpLiveError(
            f"대상 한컴 표의 병합 셀 소유자를 찾지 못했습니다: {address}"
        )
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
                raise HwpLiveError(
                    f"병합 셀 {owner.address}에 서로 다른 값을 입력할 수 없습니다"
                )
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
                raise HwpLiveError(
                    f"병합 셀 {owner.address}에 서로 다른 값을 입력할 수 없습니다"
                )
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
        raise HwpLiveError(
            "inputs.data에는 cells, rows, records 중 정확히 하나를 전달하세요"
        )
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
    *,
    surrounding_texts: tuple[str, ...] = (),
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
    cells = _cell_map(table)
    replacements = tuple(
        (address, value)
        for address, value in replacements
        if _normalized_cell_text(_owner_cell(cells, address).text)
        != _normalized_cell_text(value)
    )
    if policy.preserve_style:
        edits = infer_table_cell_edits(
            table,
            replacements,
            numeric_value_mode=policy.numeric_value_mode,
            surrounding_texts=surrounding_texts,
        )
        replacements = tuple((edit.address, edit.replacement) for edit in edits)
        command_groups = tuple(
            (
                (
                    SetCellTextCommand(
                        edit.address,
                        edit.replacement,
                        expected_text=edit.expected_text,
                        preserve_style=True,
                    ),
                )
                if not edit.expected_text
                else tuple(
                    TextPatchCommand(
                        target="table_cell",
                        expected_text=patch.expected_text,
                        replacement=patch.replacement,
                        occurrence=patch.occurrence,
                        match_case=True,
                        table_instance_id=control_id,
                        cell_address=edit.address,
                        preserve_format=True,
                    )
                    for patch in edit.patches
                )
            )
            for edit in edits
        )
        edit_commands = tuple(command for group in command_groups for command in group)
        native_protocol: Literal[9, 12] = 12
    else:
        command_groups = tuple(
            (SetCellTextCommand(address, value),) for address, value in replacements
        )
        edit_commands = tuple(group[0] for group in command_groups)
        native_protocol = 9
    commands = (
        SelectControlCommand(control_id),
        CaptureTableCommand(),
        *edit_commands,
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
        native_protocol=native_protocol,
        command_groups=command_groups,
    )


def _table_fill_group_topology_work(
    group: tuple[NativeActionCommand, ...],
    *,
    table_cell_count: int,
) -> int:
    return table_cell_count * sum(
        isinstance(command, TextPatchCommand) for command in group
    )


def _table_fill_chunk(
    prepared: PreparedWorkflowTableFill,
    replacements: tuple[tuple[str, str], ...],
    command_groups: tuple[tuple[NativeActionCommand, ...], ...],
) -> PreparedWorkflowTableFill:
    prefix = prepared.request.commands[:_TABLE_FILL_PREFIX_COMMANDS]
    request = replace(
        prepared.request,
        commands=(
            # SELECT_CONTROL, CAPTURE_TABLE 다음에 편집 명령만 오는 형태를
            # 유지한다. 네이티브는 이 형태만 고수준 표 채움으로 인식하고
            # (ActionTextPatch.cpp IsTableTextFillBatch), 인식된 요청만
            # 모든 셀을 shadow 로 검증한 뒤 mutation 을 시작하며
            # (PreflightTableTextCommands) topology 를 요청당 한 번만 만든다
            # (SelectTableForCellPatch 의 reusableTableTextFillRequest).
            # 여기에 CELL 같은 다른 명령을 끼우면 두 보호가 함께 사라진다.
            *prefix,
            *(command for group in command_groups for command in group),
        ),
    )
    return replace(
        prepared,
        request=request,
        replacements=replacements,
        command_groups=command_groups,
    )


def table_fill_chunks(
    prepared: PreparedWorkflowTableFill,
    table: StructureTable,
) -> tuple[PreparedWorkflowTableFill, ...]:
    replacement_count = len(prepared.replacements)
    if replacement_count == 0:
        return (prepared,)
    table_cell_count = max(1, len(table.cells))
    request_edit_commands = prepared.request.commands[2:]
    original_text_patch_count = sum(
        isinstance(command, TextPatchCommand) for command in request_edit_commands
    )
    original_topology_work = table_cell_count * (
        replacement_count + original_text_patch_count + 1
    )
    # 진입 조건은 1e6358f 당시 그대로 둔다. 이 조건을 좁히면 실패했을 때
    # 호출별 readback 확인 보고 경로(_failed_chunk_result)를 타지 않게 되어
    # 큰 채움의 부분 적용 증거가 사라진다. 실제 분할 여부는 아래 루프의
    # 명령 수 예산만 정한다.
    if (
        len(request_edit_commands) <= _NATIVE_CALL_EDIT_BUDGET
        and original_topology_work <= _NATIVE_CALL_TOPOLOGY_WORK_BUDGET
    ):
        return (prepared,)
    if len(prepared.command_groups) != replacement_count:
        raise HwpLiveError(
            "대량 표 채움 명령과 대상 셀의 대응 관계가 올바르지 않습니다"
        )
    if not (
        len(prepared.request.commands) >= 2
        and isinstance(prepared.request.commands[0], SelectControlCommand)
        and isinstance(prepared.request.commands[1], CaptureTableCommand)
    ):
        raise HwpLiveError("대량 표 채움 요청의 표 선택 명령 순서가 올바르지 않습니다")

    # 분할 기준은 요청당 명령 수 하나뿐이다. 네이티브가 고수준 표 채움으로
    # 인식하는 형태에서는 topology 를 준비 1회·실행 1회, 즉 요청당 두 번만
    # 만든다. 그 비용은 표 크기에만 비례하고 한 요청에 담은 편집 수와는
    # 무관하므로 더 잘게 쪼개도 줄지 않는다. 오히려 호출 수만큼 곱해진다.
    chunks: list[PreparedWorkflowTableFill] = []
    start = 0
    edit_count = 0
    for index, group in enumerate(prepared.command_groups):
        group_edit_count = len(group)
        group_topology_work = _table_fill_group_topology_work(
            group,
            table_cell_count=table_cell_count,
        )
        # 한 셀 몫을 더는 나눌 수 없을 때의 거절 조건은 종전 그대로 둔다.
        # 명령 수 한계는 네이티브 ValidateCellTextPatchLimit(한 셀 100개)과
        # 같은 자리를 지키고, topology 한계는 실측 범위를 벗어난 큰 표를
        # 보수적으로 계속 막는다.
        if (
            group_edit_count > _NATIVE_CALL_EDIT_BUDGET
            or table_cell_count + group_topology_work
            > _NATIVE_CALL_TOPOLOGY_WORK_BUDGET
        ):
            address = prepared.replacements[index][0]
            raise HwpLiveError(
                f"{address} 셀의 서식 보존 명령은 안전한 네이티브 호출 예산을 "
                + "한 셀 단위로 초과하여 분할할 수 없습니다"
            )
        if index > start and edit_count + group_edit_count > _NATIVE_CALL_EDIT_BUDGET:
            chunks.append(
                _table_fill_chunk(
                    prepared,
                    prepared.replacements[start:index],
                    prepared.command_groups[start:index],
                )
            )
            start = index
            edit_count = 0
        edit_count += group_edit_count
    chunks.append(
        _table_fill_chunk(
            prepared,
            prepared.replacements[start:],
            prepared.command_groups[start:],
        )
    )
    return tuple(chunks)


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

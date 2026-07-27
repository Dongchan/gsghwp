from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

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

# kMaximumCommands in ActionProtocol.cpp:21. The parser rejects the whole script
# past it (ActionProtocol.cpp:537). The public wrapper accepts up to 20_000 data
# entries (hwp_public_table_target.PublicTableDataInput) and this builder emits
# 2 + edits commands, or 3 + appended_rows + edits when the table grows, so the
# accepted input range reaches past what the native side can ever run. Expand is
# a single request by contract — it is not chunked — so the only honest answer
# for an oversized request is to say so before anything is dispatched.
_NATIVE_COMMAND_LIMIT: Final = 20_000

# 서식을 보존하는 셀 교체는 5필드 SET_CELL_TEXT 형식이다
# (hwp_live_native_action_contract._command_lines 의 SetCellTextCommand 분기,
# ActionProtocol.cpp:400-407 가 파싱한다). 이 저장소의 네이티브가 스스로
# 보고하는 프로토콜은 12 이고(BatchAutomation.cpp:552 `result->lVal = 12`),
# addon/ 어디에도 명령별 버전 게이트가 없어 이 형식이 실제로 몇 번부터
# 있었는지를 소스만으로 더 낮게 증명할 방법이 없다. 그래서 같은 명령을 이미
# 쓰고 있는 채우기 경로의 값(hwp_live_workflow_table.prepare_table_fill 이
# preserve_style 일 때 고르는 native_protocol=12)을 그대로 요구 버전으로 적는다.
# 이 값은 계획이 무엇을 필요로 하는지 기록할 뿐, 여기서 게이트를 올리지는
# 않는다. 실제 dispatch 게이트는 호출자에 있다.
_PRESERVE_STYLE_NATIVE_PROTOCOL: Final = 12
# 3필드 SET_CELL_TEXT 만 쓰는 기존 경로. 호출자가 지금 쓰는 게이트와 같다.
_PLAIN_NATIVE_PROTOCOL: Final = 9


@dataclass(frozen=True, slots=True)
class ExpandedTablePlan:
    request: NativeActionRequest
    replacements: tuple[tuple[str, str], ...]
    rows_added: int
    # 이 요청을 실행하려면 최소 몇 번 프로토콜이 필요한지. 서식 보존 명령이
    # 하나라도 들어가면 12, 아니면 종전과 같은 9 다.
    native_protocol: int = _PLAIN_NATIVE_PROTOCOL


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


def _cell_text_command(
    address: str,
    value: str,
    observed: dict[str, str],
    *,
    preserve_style: bool,
) -> SetCellTextCommand:
    """확장·채우기 셀 하나를 쓰는 명령.

    `observed` 는 이 요청을 만들기 직전에 읽은 표 스냅숏에서 그 셀이 실제로
    담고 있던 문자열이다(StructureCell.text). 채우기 경로가 expected_text 로
    싣는 값과 같은 출처다 — hwp_table_format_inference.infer_table_cell_edits
    는 ContextualCellEdit.expected_text 에 `_target_cell(...).text`, 즉 같은
    스냅숏의 소유 셀 텍스트를 넣는다.
    """
    if not preserve_style:
        return SetCellTextCommand(address, value)
    expected = observed.get(address)
    if expected is None:
        # 스냅숏에 없는 셀이다. 대부분 이 요청의 TableAppendRow 가 이제부터
        # 만들 행이고, 없는 관측을 지어내면 expected_text 단언이 거짓이 되어
        # STALE 검사가 엉뚱한 셀을 통과시키거나 막게 된다. 게다가 네이티브는
        # 빈 셀에는 애초에 서식을 보존하지 않는다 — ActionTextPatch.cpp:280
        # `preserveFormat && !current.empty()`. 새 행 셀에서 3필드 형식은
        # 잃을 서식이 없고, 이는 종전과 완전히 같은 명령이다.
        return SetCellTextCommand(address, value)
    return SetCellTextCommand(
        address,
        value,
        expected_text=expected,
        preserve_style=True,
    )


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
    # 스냅숏에 실재하는 셀의 현재 텍스트만 담는다. 여기 없는 주소는 "관측한
    # 적이 없다"는 뜻이고, 그 사실이 아래에서 expected_text 를 실을지 말지를
    # 가른다.
    observed: dict[str, str] = {}
    for address, value in _raw_replacements(table, data).items():
        owner = _owner(table, by_address, address)
        existing = by_address.get(owner)
        if policy.fill_blanks_only and existing is not None and existing.text.strip():
            continue
        previous = replacements.get(owner)
        if previous is not None and previous != value:
            raise HwpLiveError(f"병합 셀 {owner}에 서로 다른 값을 입력할 수 없습니다")
        replacements[owner] = value
        if existing is not None:
            observed[owner] = existing.text
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
    # 편집 셀 하나당 명령 하나. preserve_style 은 같은 명령의 형식만 바꾸고
    # 명령 수는 늘리지 않으므로 아래 20_000 예산 계산은 종전 그대로다.
    commands.extend(
        _cell_text_command(
            address,
            value,
            observed,
            preserve_style=policy.preserve_style,
        )
        for address, value in ordered
    )
    if len(commands) > _NATIVE_COMMAND_LIMIT:
        raise HwpLiveError(
            f"표 확장·채우기 명령이 네이티브 한계 {_NATIVE_COMMAND_LIMIT}개를 넘었습니다"
            + f" (명령 {len(commands)}개, 셀 {len(ordered)}개, 추가 행 {rows_added}개)."
            + " 데이터를 나눠 여러 번 요청하세요"
        )
    preserving = any(
        isinstance(command, SetCellTextCommand) and command.preserve_style
        for command in commands
    )
    return ExpandedTablePlan(
        NativeActionRequest(
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            commands=tuple(commands),
        ),
        ordered,
        rows_added,
        _PRESERVE_STYLE_NATIVE_PROTOCOL if preserving else _PLAIN_NATIVE_PROTOCOL,
    )

"""표 채움 요청이 네이티브의 고수준 table-fill batch 형태를 유지하는지 고정한다.

네이티브는 ``SELECT_CONTROL, CAPTURE_TABLE, (SET_CELL_TEXT | PATCH_TEXT CELL)...``
형태만 고수준 표 채움으로 인식한다
(``addon/HancomLiveBridgeNative/ActionTextPatch.cpp`` ``IsTableTextFillBatch``).
인식된 요청만
- 모든 셀을 shadow 로 먼저 검증한 뒤 mutation 을 시작하고
  (``PreflightTableTextCommands``),
- 표 topology 를 요청 하나에 한 번만 만들어 재사용한다
  (``SelectTableForCellPatch`` 의 ``reusableTableTextFillRequest``, 커밋 1e6358f).

즉 인식이 깨지면 원자성과 topology 재사용을 동시에 잃는다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "skills" / "automate-hancom-documents" / "scripts"),
)

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import HwpComApplication, HwpComDocument  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    CaptureTableCommand,
    CellCommand,
    NativeActionRequest,
    SelectControlCommand,
    SetCellTextCommand,
    TextPatchCommand,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    StructureCell,
    StructurePosition,
    StructureTable,
)
from hwp_live_workflow_table import (  # noqa: E402
    NATIVE_REQUEST_COMMAND_LIMIT,
    PreparedWorkflowTableFill,
    _NATIVE_CALL_EDIT_BUDGET,
    _TABLE_FILL_PREFIX_COMMANDS,
    prepare_table_fill,
    table_fill_chunks,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
)


def _is_table_text_fill_batch(request: NativeActionRequest) -> bool:
    """``IsTableTextFillBatch`` (ActionTextPatch.cpp:1045-1068) 의 파이썬 포팅."""
    commands = request.commands
    if (
        len(commands) < 3
        or not isinstance(commands[0], SelectControlCommand)
        or not isinstance(commands[1], CaptureTableCommand)
    ):
        return False
    selected_table_id = commands[0].instance_id
    has_text_mutation = False
    for command in commands[2:]:
        if isinstance(command, SetCellTextCommand):
            has_text_mutation = True
            continue
        if (
            isinstance(command, TextPatchCommand)
            and command.target == "table_cell"
            and command.table_instance_id == selected_table_id
        ):
            has_text_mutation = True
            continue
        return False
    return has_text_mutation


def _candidate() -> HwpDocumentCandidate:
    document = cast(
        HwpComDocument,
        cast(
            object,
            SimpleNamespace(DocumentID=17, FullName="C:/documents/bulk-fill.hwp"),
        ),
    )
    return HwpDocumentCandidate(
        selector="active",
        moniker_name="fixture",
        application=cast(HwpComApplication, object()),
        document=document,
        document_id=17,
        full_name="C:/documents/bulk-fill.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )


def _table(cell_count: int, *, existing: str = "") -> StructureTable:
    return StructureTable(
        table_ref="table-ref-00000001",
        control_instance_id="table-17",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=cell_count,
        columns=1,
        merges=(),
        cells=tuple(
            StructureCell(
                address=f"A{row}",
                row=row - 1,
                column=0,
                owner_address=f"A{row}",
                text=existing,
            )
            for row in range(1, cell_count + 1)
        ),
    )


def _new_value(row: int) -> str:
    return "새값" + chr(0xAC00 + (row % 1000))


_PREPARED_CACHE: dict[
    tuple[int, int, bool], tuple[StructureTable, PreparedWorkflowTableFill]
] = {}


def _prepare(
    *,
    cell_count: int,
    edit_count: int,
    preserve_style: bool,
) -> tuple[StructureTable, PreparedWorkflowTableFill]:
    key = (cell_count, edit_count, preserve_style)
    cached = _PREPARED_CACHE.get(key)
    if cached is not None:
        return cached
    table = _table(cell_count, existing="이전값가" if preserve_style else "")
    prepared = prepare_table_fill(
        _candidate(),
        table,
        1,
        HwpOperateData(
            cells={f"A{row}": _new_value(row) for row in range(1, edit_count + 1)}
        ),
        HwpOperatePolicy(preserve_style=preserve_style, numeric_value_mode="display"),
        HwpOperatePostconditions(),
    )
    _PREPARED_CACHE[key] = (table, prepared)
    return table, prepared


_SCALES = (
    pytest.param(400, 400, False, 4, id="plain-400"),
    pytest.param(2_500, 2_500, False, 25, id="plain-2500"),
    pytest.param(100, 100, True, 1, id="preserve-100"),
    pytest.param(400, 400, True, 4, id="preserve-400"),
    pytest.param(2_500, 2_500, True, 25, id="preserve-2500"),
    pytest.param(1_000, 50, False, 1, id="plain-sparse-50-of-1000"),
)


@pytest.mark.parametrize(("cells", "edits", "preserve", "calls"), _SCALES)
def test_every_native_request_keeps_the_table_fill_batch_shape(
    cells: int,
    edits: int,
    preserve: bool,
    calls: int,
) -> None:
    _ = calls
    table, prepared = _prepare(
        cell_count=cells, edit_count=edits, preserve_style=preserve
    )
    chunks = table_fill_chunks(prepared, table)
    for chunk in chunks:
        assert not any(
            isinstance(command, CellCommand) for command in chunk.request.commands
        ), "CELL 명령은 네이티브의 고수준 표 채움 인식을 깨뜨린다"
        assert _is_table_text_fill_batch(chunk.request), (
            "네이티브가 고수준 표 채움 batch 로 인식하지 못하는 요청이 생성되었다"
        )


@pytest.mark.parametrize(("cells", "edits", "preserve", "calls"), _SCALES)
def test_native_execute_call_count_per_scale(
    cells: int,
    edits: int,
    preserve: bool,
    calls: int,
) -> None:
    table, prepared = _prepare(
        cell_count=cells, edit_count=edits, preserve_style=preserve
    )
    assert len(table_fill_chunks(prepared, table)) == calls


@pytest.mark.parametrize(("cells", "edits", "preserve", "calls"), _SCALES)
def test_no_request_exceeds_the_native_command_limit(
    cells: int,
    edits: int,
    preserve: bool,
    calls: int,
) -> None:
    _ = calls
    table, prepared = _prepare(
        cell_count=cells, edit_count=edits, preserve_style=preserve
    )
    for chunk in table_fill_chunks(prepared, table):
        assert len(chunk.request.commands) <= NATIVE_REQUEST_COMMAND_LIMIT


def test_declared_command_limit_matches_the_native_parser() -> None:
    # ActionProtocol.cpp:21 `constexpr size_t kMaximumCommands = 20'000;`
    # ActionProtocol.cpp:536-538 은 이 값을 넘으면 BAD_REQUEST 로 거절한다.
    assert NATIVE_REQUEST_COMMAND_LIMIT == 20_000


def test_edit_budget_can_never_build_an_over_limit_request() -> None:
    # 한 요청의 최대 명령 수 = 접두 2개 + 편집 예산. 예산이 어떤 이유로 커져도
    # 네이티브 파서 한계를 넘는 요청이 만들어져서는 안 된다.
    assert (
        _NATIVE_CALL_EDIT_BUDGET + _TABLE_FILL_PREFIX_COMMANDS
        <= NATIVE_REQUEST_COMMAND_LIMIT
    )


def test_worst_case_reachable_scale_stays_within_the_native_limit() -> None:
    # 도달 가능한 최악 규모는 두 한계 중 작은 쪽이 정한다.
    #   - 스키마상 inputs.data.cells 최대 20,000개
    #     (hwp_operation_contract.py:190 `max_length=20_000`)
    #   - table_fill_chunks 가 종전부터 유지해 온 표 크기 상한 10,100 셀
    # 따라서 실제 최악은 10,100 셀 표를 전부 채우는 경우다.
    table, prepared = _prepare(
        cell_count=10_100, edit_count=10_100, preserve_style=False
    )
    chunks = table_fill_chunks(prepared, table)
    largest = max(len(chunk.request.commands) for chunk in chunks)
    assert largest == _NATIVE_CALL_EDIT_BUDGET + _TABLE_FILL_PREFIX_COMMANDS == 102
    assert largest <= NATIVE_REQUEST_COMMAND_LIMIT
    assert len(chunks) == 101
    for chunk in chunks:
        assert _is_table_text_fill_batch(chunk.request)


def test_table_larger_than_the_measured_envelope_is_still_refused_early() -> None:
    # 종전부터 있던 보수적 거절이다. 표가 실측 범위(10,100 셀)를 넘으면
    # 네이티브를 부르기 전에 멈춘다. 이번 변경에서 이 조건은 건드리지 않았다.
    table, prepared = _prepare(
        cell_count=10_101, edit_count=10_101, preserve_style=False
    )
    with pytest.raises(HwpLiveError):
        table_fill_chunks(prepared, table)


def test_every_edit_reaches_exactly_one_request_in_order() -> None:
    table, prepared = _prepare(cell_count=2_500, edit_count=2_500, preserve_style=True)
    chunks = table_fill_chunks(prepared, table)
    addresses = tuple(
        address for chunk in chunks for address, _ in chunk.replacements
    )
    assert addresses == tuple(address for address, _ in prepared.replacements)
    assert len(set(addresses)) == 2_500


class _FakeNativeWriteLog:
    """C++ 계약을 모사한다.

    인식된 요청은 shadow 전량 검증 뒤에만 쓰고, 인식되지 않은 요청은
    명령 순서대로 쓰다가 처음 어긋난 곳에서 멈춘다.
    """

    def __init__(self, actual: dict[str, str]) -> None:
        self.actual = dict(actual)
        self.written: list[str] = []

    def execute(self, request: NativeActionRequest) -> None:
        edits = [
            command
            for command in request.commands
            if isinstance(command, (SetCellTextCommand, TextPatchCommand))
        ]
        if _is_table_text_fill_batch(request):
            shadow = dict(self.actual)
            for command in edits:
                self._apply(command, shadow, preflight=True)
            for command in edits:
                self._apply(command, self.actual, preflight=False)
            return
        for command in edits:
            self._apply(command, self.actual, preflight=False)

    def _apply(
        self,
        command: SetCellTextCommand | TextPatchCommand,
        cells: dict[str, str],
        *,
        preflight: bool,
    ) -> None:
        if isinstance(command, SetCellTextCommand):
            address = command.address
            current = cells.get(address, "")
            if command.expected_text is not None and current != command.expected_text:
                raise HwpLiveError(f"STALE_CELL_TEXT {address}")
            cells[address] = command.text
        else:
            address = cast(str, command.cell_address)
            current = cells.get(address, "")
            expected = command.expected_text or ""
            if expected not in current:
                raise HwpLiveError(f"TEXT_NOT_FOUND {address}")
            cells[address] = current.replace(expected, command.replacement, 1)
        if not preflight:
            self.written.append(address)


def test_stale_cell_aborts_the_request_without_writing_any_cell() -> None:
    table, prepared = _prepare(cell_count=1_000, edit_count=50, preserve_style=False)
    chunks = table_fill_chunks(prepared, table)
    assert len(chunks) == 1, "50셀 채움은 한 번의 네이티브 호출로 끝나야 한다"

    # 요청을 만든 뒤 마지막 대상 셀만 사용자가 바꿔 놓은 상태.
    stale_address = prepared.replacements[-1][0]
    log = _FakeNativeWriteLog({cell.address: cell.text for cell in table.cells})
    log.actual[stale_address] = "사용자가 방금 입력한 값"

    for chunk in chunks:
        chunk_request = chunk.request
        chunk_commands = tuple(
            command
            for command in chunk_request.commands
            if isinstance(command, SetCellTextCommand)
        )
        # 요청에 담긴 기대값을 요청 작성 시점 문서 상태로 채운다.
        assert chunk_commands
        with pytest.raises(HwpLiveError):
            log.execute(
                NativeActionRequest(
                    document_id=chunk_request.document_id,
                    full_name=chunk_request.full_name,
                    commands=tuple(
                        SetCellTextCommand(
                            command.address,
                            command.text,
                            expected_text="",
                            preserve_style=command.preserve_style,
                        )
                        if isinstance(command, SetCellTextCommand)
                        else command
                        for command in chunk_request.commands
                    ),
                )
            )

    assert log.written == [], (
        "요청 안에 stale 셀이 하나라도 있으면 어떤 셀도 쓰이면 안 된다"
    )


def test_single_cell_patch_group_over_the_native_limit_still_fails_early() -> None:
    # 네이티브 ValidateCellTextPatchLimit (ActionTextPatch.cpp:1070-1097) 은 한 셀에
    # 100개를 넘는 PATCH_TEXT 를 표에 손대기 전에 거절한다. 파이썬도 같은 한계를
    # 표 편집 전에 알려야 한다.
    table = _table(4, existing="이전값가")
    prepared = prepare_table_fill(
        _candidate(),
        table,
        1,
        HwpOperateData(cells={"A1": "새값나"}),
        HwpOperatePolicy(preserve_style=True, numeric_value_mode="display"),
        HwpOperatePostconditions(),
    )
    oversized = tuple(
        TextPatchCommand(
            target="table_cell",
            expected_text="이전값가",
            replacement="새값나",
            occurrence=index + 1,
            match_case=True,
            table_instance_id="table-17",
            cell_address="A1",
            preserve_format=True,
        )
        for index in range(101)
    )
    prepared = PreparedWorkflowTableFill(
        request=NativeActionRequest(
            document_id=prepared.request.document_id,
            full_name=prepared.request.full_name,
            commands=(
                SelectControlCommand("table-17"),
                CaptureTableCommand(),
                *oversized,
            ),
        ),
        table_index=prepared.table_index,
        control_instance_id="table-17",
        replacements=(("A1", "새값나"),),
        native_protocol=12,
        command_groups=(oversized,),
    )
    with pytest.raises(HwpLiveError):
        table_fill_chunks(prepared, table)

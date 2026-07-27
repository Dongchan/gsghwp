"""expand_and_fill 이 전달받은 preserve_style 을 실제로 반영하는지 고정한다.

배경: 공개 도구 hwp_expand_and_fill_table 은 hwp_public_table_tools.py:192 에서
preserve_style=True 를 넘긴다. 그 값이 명령까지 도달하지 않으면 사용자가
"서식 유지"를 요청했는데도 셀 서식이 조용히 날아간다.
"""

from __future__ import annotations

import sys
from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_commands import SetCellTextCommand  # noqa: E402
from hwp_live_native_action_contract import encode_action_request  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    StructureCell,
    StructurePosition,
    StructureTable,
)
from hwp_operation_contract import HwpOperateData, HwpOperatePolicy  # noqa: E402
from hwp_priority_table_expand import prepare_expand_and_fill  # noqa: E402


# ActionProtocol.cpp:21 kMaximumCommands = 20'000; ActionProtocol.cpp:537 rejects
# the whole script past it.
NATIVE_HARD_COMMAND_LIMIT = 20_000


def _column(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _table(
    rows: int,
    columns: int,
    texts: dict[str, str] | None = None,
) -> StructureTable:
    filled = texts or {}
    cells = tuple(
        StructureCell(
            address=f"{_column(column)}{row + 1}",
            row=row,
            column=column,
            owner_address=f"{_column(column)}{row + 1}",
            text=filled.get(f"{_column(column)}{row + 1}", ""),
        )
        for row in range(rows)
        for column in range(columns)
    )
    return StructureTable(
        table_ref="table-ref-0000000001",
        control_instance_id="tbl:1",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=rows,
        columns=columns,
        merges=(),
        cells=cells,
    )


def _candidate() -> HwpDocumentCandidate:
    document = SimpleNamespace(DocumentID=17, FullName="C:/documents/table.hwp")
    return cast(
        HwpDocumentCandidate,
        SimpleNamespace(
            document=document,
            document_id=17,
            full_name="C:/documents/table.hwp",
        ),
    )


def _cell_commands(plan: object) -> dict[str, SetCellTextCommand]:
    request = getattr(plan, "request")
    return {
        command.address: command
        for command in request.commands
        if isinstance(command, SetCellTextCommand)
    }


# --- 핵심: preserve_style=True 는 서식을 유지하는 명령을 만들어야 한다 --------


def test_preserve_style_emits_style_preserving_command_for_existing_cells() -> None:
    table = _table(rows=2, columns=1, texts={"A2": "1,234"})
    data = HwpOperateData(cells={"A2": "5,678"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )

    command = _cell_commands(plan)["A2"]
    assert command.preserve_style is True
    assert command.text == "5,678"


def test_preserve_style_expected_text_is_the_observed_cell_text() -> None:
    """expected_text 는 스냅숏에서 읽은 그 셀의 현재 내용이어야 한다.

    지어낸 값이면 "이 셀이 지금 이 내용일 것"이라는 단언이 거짓이 되고,
    네이티브의 STALE_CELL_TEXT 검사(ActionTextPatch.cpp:272-279)가 엉뚱한
    내용을 통과시킨다.
    """
    table = _table(rows=2, columns=2, texts={"A2": "기존 값", "B2": "다른 값"})
    data = HwpOperateData(cells={"A2": "새 값", "B2": "새 값"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )

    commands = _cell_commands(plan)
    # 요청 값이 같아도 expected_text 는 셀마다 다른, 관측된 값이어야 한다.
    assert commands["A2"].expected_text == "기존 값"
    assert commands["B2"].expected_text == "다른 값"


def test_preserve_style_expected_text_reaches_the_native_wire_form() -> None:
    """단언이 명령 객체에만 있고 전선에 안 실리면 stale 검사는 동작하지 않는다."""
    table = _table(rows=2, columns=1, texts={"A2": "기존"})
    data = HwpOperateData(cells={"A2": "신규"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )
    payload = encode_action_request(plan.request)

    expected = b64encode("기존".encode()).decode("ascii")
    replacement = b64encode("신규".encode()).decode("ascii")
    # ActionProtocol.cpp:400-407 가 받는 5필드 형식이다.
    assert f"SET_CELL_TEXT\tA2\t{expected}\t{replacement}\t1" in payload


def test_empty_existing_cell_still_carries_its_observed_emptiness() -> None:
    """빈 셀도 '비어 있음'을 관측했으므로 stale 단언을 실을 수 있다."""
    table = _table(rows=2, columns=1)
    data = HwpOperateData(cells={"A2": "값"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )

    command = _cell_commands(plan)["A2"]
    assert command.preserve_style is True
    assert command.expected_text == ""


# --- 안전: 관측하지 않은 셀에 expected_text 를 지어내지 않는다 ----------------


def test_appended_row_cells_get_no_invented_expected_text() -> None:
    """새로 붙는 행의 셀은 이 요청이 만든다. 관측한 적이 없으므로 단언도 없다."""
    table = _table(rows=1, columns=1, texts={"A1": "머리글"})
    data = HwpOperateData(cells={"A2": "새 행 값"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )

    assert plan.rows_added == 1
    command = _cell_commands(plan)["A2"]
    assert command.expected_text is None
    assert command.preserve_style is False


def test_mixed_request_preserves_only_the_observed_cells() -> None:
    table = _table(rows=2, columns=1, texts={"A2": "기존"})
    data = HwpOperateData(cells={"A2": "갱신", "A3": "추가"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )

    commands = _cell_commands(plan)
    assert plan.rows_added == 1
    assert commands["A2"].expected_text == "기존"
    assert commands["A2"].preserve_style is True
    assert commands["A3"].expected_text is None
    assert commands["A3"].preserve_style is False


def test_style_preserving_command_without_expected_text_is_rejected() -> None:
    """인코더가 지어낸 서식 보존 명령을 막는지 확인한다(계약 고정)."""
    from hwp_live_native_action_models import NativeActionRequest

    request = NativeActionRequest(
        document_id=17,
        full_name="C:/documents/table.hwp",
        commands=(SetCellTextCommand("A2", "값", preserve_style=True),),
    )

    with pytest.raises(HwpLiveError):
        _ = encode_action_request(request)


# --- 안전: preserve_style=False 는 종전 그대로 -------------------------------


def test_preserve_style_false_keeps_the_previous_plain_command() -> None:
    table = _table(rows=2, columns=1, texts={"A2": "기존"})
    data = HwpOperateData(cells={"A2": "신규"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=False),
    )

    command = _cell_commands(plan)["A2"]
    assert command == SetCellTextCommand("A2", "신규")
    assert plan.native_protocol == 9
    assert "SET_CELL_TEXT\tA2\t" + b64encode("신규".encode()).decode("ascii") in (
        encode_action_request(plan.request)
    )


def test_plan_declares_the_protocol_the_preserving_form_needs() -> None:
    table = _table(rows=2, columns=1, texts={"A2": "기존"})
    data = HwpOperateData(cells={"A2": "신규"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )

    assert plan.native_protocol == 12


def test_appended_rows_only_request_stays_on_the_plain_protocol() -> None:
    table = _table(rows=1, columns=1, texts={"A1": "머리글"})
    data = HwpOperateData(cells={"A2": "새 행"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )

    assert plan.native_protocol == 9


# --- 안전: 명령 예산은 preserve_style 로 달라지지 않는다 ---------------------


def test_preserve_style_does_not_add_commands() -> None:
    texts = {f"A{row + 1}": f"{row}" for row in range(50)}
    table = _table(rows=50, columns=1, texts=texts)
    data = HwpOperateData(cells={f"A{row + 1}": f"새 {row}" for row in range(50)})

    preserving = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )
    plain = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=False),
    )

    assert len(preserving.request.commands) == len(plain.request.commands)
    assert preserving.replacements == plain.replacements
    assert preserving.rows_added == plain.rows_added


def test_worst_case_accepted_request_stays_within_the_native_limit() -> None:
    """받아들여지는 최대 요청도 20_000 명령을 넘지 않는다."""
    edits = NATIVE_HARD_COMMAND_LIMIT - 2
    texts = {f"A{row + 1}": "기존" for row in range(edits)}
    table = _table(rows=edits, columns=1, texts=texts)
    data = HwpOperateData(cells={f"A{row + 1}": "신규" for row in range(edits)})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True),
    )

    assert len(plan.request.commands) == NATIVE_HARD_COMMAND_LIMIT
    assert all(
        command.preserve_style
        for command in plan.request.commands
        if isinstance(command, SetCellTextCommand)
    )


def test_over_limit_request_is_still_rejected_with_preserve_style() -> None:
    texts = {f"A{row + 1}": "기존" for row in range(NATIVE_HARD_COMMAND_LIMIT)}
    table = _table(rows=NATIVE_HARD_COMMAND_LIMIT, columns=1, texts=texts)
    data = HwpOperateData(
        cells={f"A{row + 1}": "신규" for row in range(NATIVE_HARD_COMMAND_LIMIT)},
    )

    with pytest.raises(HwpLiveError) as raised:
        _ = prepare_expand_and_fill(
            _candidate(),
            table,
            data,
            HwpOperatePolicy(preserve_style=True),
        )

    assert "20000" in str(raised.value)


# --- 안전: 기존 expand 동작이 깨지지 않는다 ---------------------------------


def test_fill_blanks_only_still_skips_filled_cells() -> None:
    table = _table(rows=2, columns=2, texts={"A2": "이미 있음"})
    data = HwpOperateData(cells={"A2": "덮어쓰기", "B2": "새 값"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(preserve_style=True, fill_blanks_only=True),
    )

    assert plan.replacements == (("B2", "새 값"),)
    assert "A2" not in _cell_commands(plan)


def test_merged_cell_conflict_still_raises() -> None:
    cells = (
        StructureCell(
            address="A1",
            row=0,
            column=0,
            owner_address="A1",
            text="",
        ),
        StructureCell(
            address="B1",
            row=0,
            column=1,
            owner_address="A1",
            text="",
        ),
    )
    table = StructureTable(
        table_ref="table-ref-0000000002",
        control_instance_id="tbl:2",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=1,
        columns=2,
        merges=(),
        cells=cells,
    )
    data = HwpOperateData(cells={"A1": "가", "B1": "나"})

    with pytest.raises(HwpLiveError):
        _ = prepare_expand_and_fill(
            _candidate(),
            table,
            data,
            HwpOperatePolicy(preserve_style=True),
        )

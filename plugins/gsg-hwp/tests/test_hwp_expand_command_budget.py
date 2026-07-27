from __future__ import annotations

import sys
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
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    StructureCell,
    StructurePosition,
    StructureTable,
)
from hwp_operation_contract import HwpOperateData, HwpOperatePolicy  # noqa: E402
from hwp_priority_table_expand import prepare_expand_and_fill  # noqa: E402


# ActionProtocol.cpp:21 kMaximumCommands = 20'000; ActionProtocol.cpp:537 fails
# the whole script past it. hwp_public_table_target.PublicTableDataInput accepts
# up to 20_000 cells, and expand emits 2 + edits (or 3 + rows + edits) commands,
# so the accepted input range overlaps requests the native side always rejects.
NATIVE_HARD_COMMAND_LIMIT = 20_000


def _column(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _table(rows: int, columns: int) -> StructureTable:
    cells = tuple(
        StructureCell(
            address=f"{_column(column)}{row + 1}",
            row=row,
            column=column,
            owner_address=f"{_column(column)}{row + 1}",
            text="",
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
        SimpleNamespace(document=document),
    )


def _cells(table: StructureTable, count: int) -> HwpOperateData:
    addresses = tuple(cell.address for cell in table.cells)[:count]
    assert len(addresses) == count
    return HwpOperateData(cells={address: "값" for address in addresses})


def test_expand_over_native_command_limit_fails_before_dispatch() -> None:
    table = _table(rows=NATIVE_HARD_COMMAND_LIMIT, columns=1)
    data = _cells(table, NATIVE_HARD_COMMAND_LIMIT)

    with pytest.raises(HwpLiveError) as raised:
        _ = prepare_expand_and_fill(
            _candidate(),
            table,
            data,
            HwpOperatePolicy(),
        )

    assert "20000" in str(raised.value)


def test_expand_at_native_command_limit_still_builds_one_request() -> None:
    # 19_998 edits + SelectControl + CaptureTable == exactly 20_000 commands.
    edits = NATIVE_HARD_COMMAND_LIMIT - 2
    table = _table(rows=edits, columns=1)
    data = _cells(table, edits)

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(),
    )

    assert len(plan.request.commands) == NATIVE_HARD_COMMAND_LIMIT
    assert plan.rows_added == 0
    assert len(plan.replacements) == edits


# --- safety: the budget check must not reject ordinary requests --------------


def test_expand_ordinary_request_is_unaffected() -> None:
    table = _table(rows=3, columns=2)
    data = HwpOperateData(cells={"A2": "가", "B2": "나", "A4": "다"})

    plan = prepare_expand_and_fill(
        _candidate(),
        table,
        data,
        HwpOperatePolicy(),
    )

    # 1 appended row: SelectControl, CaptureTable, Cell, TableAppendRow, 3 edits.
    assert plan.rows_added == 1
    assert len(plan.request.commands) == 7
    assert plan.replacements == (("A2", "가"), ("A4", "다"), ("B2", "나"))


def test_expand_counts_appended_rows_against_the_budget() -> None:
    # Row growth adds commands too: 3 + rows_added + edits. A request that fits
    # only when the appended rows are ignored must still be rejected.
    edits = NATIVE_HARD_COMMAND_LIMIT - 10
    table = _table(rows=1, columns=1)
    data = HwpOperateData(
        cells={f"A{row + 1}": "값" for row in range(edits)},
    )

    with pytest.raises(HwpLiveError):
        _ = prepare_expand_and_fill(
            _candidate(),
            table,
            data,
            HwpOperatePolicy(),
        )

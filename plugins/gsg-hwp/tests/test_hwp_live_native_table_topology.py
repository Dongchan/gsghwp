from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import HwpComApplication  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeDetailedCell,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativePosition,
)
from hwp_live_native_table_topology import (  # noqa: E402
    TableTopology,
    table_formula_selection_region,
    table_topology,
)


_TABLE_ID = "selected-table"


def _column_name(column: int) -> str:
    name = ""
    while column > 0:
        column, remainder = divmod(column - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _address(row: int, column: int) -> str:
    return f"{_column_name(column)}{row}"


def _topology(rows: int, columns: int) -> TableTopology:
    cells = tuple(
        NativeDetailedCell(
            _TABLE_ID,
            _address(row, column),
            (row - 1) * columns + column,
            1,
            1,
            1,
            1,
            "",
            1_000,
            500,
        )
        for row in range(1, rows + 1)
        for column in range(1, columns + 1)
    )
    control = NativeDetailedControl(
        "tbl",
        _TABLE_ID,
        "",
        NativePosition(1, 0, 0),
        1,
        1,
        True,
        rows,
        columns,
        10_000,
        5_000,
    )
    detail = NativeDetailedInspection(
        1,
        "C:/selected-table.hwp",
        1,
        1,
        "",
        (control,),
        cells,
        (),
    )
    return table_topology(detail, _TABLE_ID)


def _application(command: str) -> tuple[HwpComApplication, Mock]:
    field = SimpleNamespace(HSet=object(), Command=command)
    get_default = Mock(return_value=True)
    application = SimpleNamespace(
        HParameterSet=SimpleNamespace(HFieldCtrl=field),
        HAction=SimpleNamespace(GetDefault=get_default),
    )
    return cast(HwpComApplication, cast(object, application)), get_default


def test_native_addresses_are_not_subject_to_the_python_table_formula_limit() -> None:
    topology = _topology(10, 10)
    addresses = tuple(cell.address for cell in topology.cells)

    assert topology.selection_region_by_addresses(addresses) == addresses


def test_table_formula_fallback_supports_more_than_nine_columns_below_limit() -> None:
    topology = _topology(4, 16)
    application, get_default = _application("A1:P4")

    addresses = table_formula_selection_region(application, topology)

    assert addresses == tuple(cell.address for cell in topology.cells)
    get_default.assert_called_once()


def test_table_formula_fallback_explains_the_total_81_cell_limit() -> None:
    topology = _topology(6, 16)
    application, get_default = _application("A1:P6")

    with pytest.raises(HwpLiveError) as failure:
        _ = table_formula_selection_region(application, topology)

    message = str(failure.value)
    assert "행×열 격자가 81셀을 초과" in message
    assert "cell로 대상 셀" in message
    assert "start_cell을 지정" in message
    get_default.assert_not_called()

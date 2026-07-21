from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_action_models import (  # noqa: E402
    CellCommand,
    IntegerValue,
    NativePosition,
    ParameterActionCommand,
    RunCommand,
)
from hwp_live_native_format_commands import (  # noqa: E402
    SplitCommandPlan,
    TableFormatCommandPlan,
    build_native_format_commands,
)
from hwp_live_native_format_contract import PreparedFormatOperation  # noqa: E402
from hwp_live_native_format_inputs import (  # noqa: E402
    InputFailure,
    parse_split,
    parse_table_format,
)
from hwp_live_native_format_target import ResolvedTable  # noqa: E402
from hwp_live_native_format_recipe import topology_preflight  # noqa: E402
from hwp_live_native_action_results import (  # noqa: E402
    NativeDetailedCell,
    NativeDetailedControl,
    NativeDetailedInspection,
)
from hwp_live_native_table_topology import (  # noqa: E402
    table_topology,
    verify_split_preflight,
    verify_split_transition,
)
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_public_table_edit_contract import (  # noqa: E402
    PublicSplitTableCellInput,
    PublicTableFormattingInput,
)


def _cell(
    address: str,
    *,
    row_span: int = 1,
    column_span: int = 1,
    text: str = "",
) -> NativeDetailedCell:
    return NativeDetailedCell(
        "irregular",
        address,
        len(address),
        row_span,
        column_span,
        11,
        11,
        text,
        1_000,
        500,
    )


def _detail(
    cells: tuple[NativeDetailedCell, ...],
    *,
    rows: int,
    columns: int,
    width: int = 10_000,
    height: int = 5_000,
) -> NativeDetailedInspection:
    control = NativeDetailedControl(
        "tbl",
        "irregular",
        "",
        NativePosition(1, 0, 0),
        11,
        11,
        True,
        rows,
        columns,
        width,
        height,
    )
    return NativeDetailedInspection(1, "C:/test.hwp", 11, 12, "", (control,), cells, ())


def test_irregular_column_and_row_sizing_select_from_the_real_target_cell() -> None:
    requested = PublicTableFormattingInput(
        cell="F11",
        row_height_mm=9.0,
        column_width_mm=18.0,
    )
    parsed = parse_table_format(requested.to_parameters())
    assert not isinstance(parsed, InputFailure)

    commands = build_native_format_commands(
        TableFormatCommandPlan(
            parsed,
            ResolvedTable("irregular", "target.control_instance_id", 11, 20, 12),
        )
    )
    selected = tuple(
        command.address for command in commands if isinstance(command, CellCommand)
    )
    actions = tuple(
        command.action for command in commands if isinstance(command, RunCommand)
    )

    assert selected == ("F11", "F11")
    assert "TableCellBlockCol" in actions
    assert "TableCellBlockRow" in actions
    assert "TableLowerCell" not in actions
    assert "TableRightCell" not in actions


def test_existing_grid_split_is_explicit_and_enables_mode2() -> None:
    requested = PublicSplitTableCellInput(
        cell="A1",
        columns=4,
        rows=2,
        split_mode="existing_grid",
    )
    parameters = requested.to_parameters()
    parsed = parse_split(parameters)
    assert not isinstance(parsed, InputFailure)

    commands = build_native_format_commands(
        SplitCommandPlan(
            parsed,
            ResolvedTable("irregular", "target.control_instance_id", 11, 8, 9),
        )
    )
    mode2 = next(
        setter.value
        for command in commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "TableSplitCell"
        for setter in command.setters
        if setter.path == "Mode2"
    )

    assert parameters["split_mode"] == "existing_grid"
    assert isinstance(mode2, IntegerValue)
    assert mode2.value == 1


def test_merge_preflight_requires_a_gap_free_rectangular_region() -> None:
    detail = _detail(
        (
            _cell("A1", column_span=2),
            _cell("C1", column_span=2),
            _cell("E1"),
            _cell("A2"),
            _cell("B2", column_span=2),
            _cell("D2", column_span=2),
        ),
        rows=2,
        columns=5,
    )
    topology = table_topology(detail, "irregular")

    assert topology.merge_region("A1", "C1") == ("A1", "C1")
    with pytest.raises(HwpLiveError, match="직사각형"):
        _ = topology.merge_region("A1", "B2")


def test_existing_grid_split_rejects_a_request_that_does_not_match_spans() -> None:
    topology = table_topology(
        _detail((_cell("A1", row_span=2, column_span=4),), rows=2, columns=4),
        "irregular",
    )
    split = parse_split(
        {"cell": "A1", "columns": 3, "rows": 2, "split_mode": "existing_grid"}
    )
    assert not isinstance(split, InputFailure)

    with pytest.raises(HwpLiveError, match="기존 격자선"):
        verify_split_preflight(topology, split)


def test_equal_split_rejects_a_cell_that_already_spans_the_grid() -> None:
    topology = table_topology(
        _detail((_cell("A1", column_span=4),), rows=1, columns=4),
        "irregular",
    )
    split = parse_split({"cell": "A1", "columns": 4, "rows": 1, "split_mode": "equal"})
    assert not isinstance(split, InputFailure)

    with pytest.raises(HwpLiveError, match="existing_grid"):
        verify_split_preflight(topology, split)


def test_split_transition_blocks_logical_column_and_height_explosion() -> None:
    before = table_topology(
        _detail((_cell("A1", column_span=4),), rows=1, columns=4),
        "irregular",
    )
    after = table_topology(
        _detail(
            tuple(_cell(f"{letter}1") for letter in "ABCDEFG"),
            rows=1,
            columns=7,
            height=17_500,
        ),
        "irregular",
    )
    split = parse_split(
        {"cell": "A1", "columns": 4, "rows": 1, "split_mode": "existing_grid"}
    )
    assert not isinstance(split, InputFailure)

    with pytest.raises(HwpLiveError, match="열 수|높이"):
        verify_split_transition(before, after, split)


def test_existing_grid_split_preserves_the_grid_and_outer_table_size() -> None:
    before = table_topology(
        _detail((_cell("A1", column_span=4),), rows=1, columns=4),
        "irregular",
    )
    after = table_topology(
        _detail(tuple(_cell(f"{letter}1") for letter in "ABCD"), rows=1, columns=4),
        "irregular",
    )
    split = parse_split(
        {"cell": "A1", "columns": 4, "rows": 1, "split_mode": "existing_grid"}
    )
    assert not isinstance(split, InputFailure)

    verify_split_transition(before, after, split)


def test_split_transition_blocks_table_height_growth_even_when_grid_matches() -> None:
    before = table_topology(
        _detail((_cell("A1", column_span=2),), rows=1, columns=2),
        "irregular",
    )
    after = table_topology(
        _detail((_cell("A1"), _cell("B1")), rows=1, columns=2, height=12_000),
        "irregular",
    )
    split = parse_split(
        {"cell": "A1", "columns": 2, "rows": 1, "split_mode": "existing_grid"}
    )
    assert not isinstance(split, InputFailure)

    with pytest.raises(HwpLiveError, match="표 높이"):
        verify_split_transition(before, after, split)


def test_topology_preflight_classifies_a_safe_rejection_as_schema_conflict() -> None:
    detail = _detail((_cell("A1", column_span=3),), rows=1, columns=3)
    split = parse_split({"cell": "A1", "columns": 3, "rows": 1, "split_mode": "equal"})
    assert not isinstance(split, InputFailure)
    prepared = PreparedFormatOperation(
        SplitCommandPlan(
            split,
            ResolvedTable("irregular", "target.control_instance_id", 11, 1, 3),
        ),
        "irregular",
        "target.control_instance_id",
        ("A1",),
    )

    failure = topology_preflight(prepared, detail)

    assert isinstance(failure, InputFailure)
    assert failure.status == "schema_conflict"
    assert "existing_grid" in failure.message

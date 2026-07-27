from __future__ import annotations

# pyright: reportPrivateUsage=false

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

import hwp_live_native_format_recipe as recipe  # noqa: E402
from hwp_live_native_action_contract import (  # noqa: E402
    NativeActionFailure,
    NativeActionFailureEvidence,
)
from hwp_live_native_action_models import (  # noqa: E402
    CaptureTableCommand,
    CellCommand,
    MillimeterValue,
    NativeActionRequest,
    NativeActionResult,
    NativeCharacterFormat,
    NativeDetailedCell,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
    ParameterActionCommand,
    RunCommand,
    SelectControlCommand,
)
from hwp_live_native_format_commands import (  # noqa: E402
    FORMAT_COMMAND_BUDGET,
    FORMAT_READBACK_BUDGET,
    FORMAT_TOPOLOGY_WORK_BUDGET,
    AxisSizeTarget,
    CellRangeTarget,
    TableFormatCommandPlan,
    build_native_format_commands,
    build_native_format_execution_plan,
)
from hwp_live_native_format_contract import (  # noqa: E402
    NativeFormatRecipeRequest,
    PreparedFormatOperation,
)
from hwp_live_native_format_inputs import TableFormatSpec, table_cell_coordinate  # noqa: E402
from hwp_live_native_format_target import ResolvedTable  # noqa: E402
from hwp_live_native_layout_format import cell_format_commands  # noqa: E402
from hwp_live_table_contract import (  # noqa: E402
    CellBorder,
    CellBorders,
    CellPadding,
    TableCell,
)


def _column_name(column: int) -> str:
    name = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _cells(rows: int, columns: int) -> tuple[str, ...]:
    return tuple(
        f"{_column_name(column)}{row}"
        for row in range(1, rows + 1)
        for column in range(1, columns + 1)
    )


def _formatting(
    *,
    width: float | None = None,
    height: float | None = None,
    fill: bool = False,
    border: bool = False,
    padding: bool = False,
    font_size_pt: float | None = None,
    line_spacing_percent: int | None = None,
) -> TableFormatSpec:
    edge = CellBorder(style="solid", width="0.12mm", color=(0, 0, 0))
    return TableFormatSpec(
        None,
        TableCell(
            font_size_pt=font_size_pt,
            line_spacing_percent=line_spacing_percent,
            fill_color=(240, 241, 242) if fill else None,
            padding=(
                CellPadding(left_mm=0, right_mm=0, top_mm=0, bottom_mm=0)
                if padding
                else None
            ),
            borders=(
                CellBorders(left=edge, right=edge, top=edge, bottom=edge)
                if border
                else None
            ),
        ),
        height,
        width,
    )


def _plan(
    rows: int,
    columns: int,
    formatting: TableFormatSpec,
) -> TableFormatCommandPlan:
    return TableFormatCommandPlan(
        formatting,
        ResolvedTable("table-1", "target.control_instance_id", 1, rows, columns),
        _cells(rows, columns),
    )


def _dense_detail(rows: int, columns: int) -> NativeDetailedInspection:
    position = NativePosition(1, 0, 0)
    control = NativeDetailedControl(
        "tbl",
        "table-1",
        "",
        position,
        1,
        1,
        True,
        rows,
        columns,
        columns * 2_000,
        rows * 1_000,
    )
    cells = tuple(
        NativeDetailedCell(
            "table-1",
            address,
            index + 100,
            1,
            1,
            1,
            1,
            "",
            2_000,
            1_000,
        )
        for index, address in enumerate(_cells(rows, columns))
    )
    return NativeDetailedInspection(
        17,
        "C:/documents/sample.hwp",
        1,
        1,
        "",
        (control,),
        cells,
        (),
    )


def _asymmetric_merged_detail() -> NativeDetailedInspection:
    position = NativePosition(1, 0, 0)
    control = NativeDetailedControl(
        "tbl",
        "table-1",
        "",
        position,
        1,
        1,
        True,
        2,
        3,
        6_000,
        2_000,
    )
    cells = (
        NativeDetailedCell("table-1", "A1", 100, 1, 1, 1, 1, "", 2_000, 1_000),
        NativeDetailedCell("table-1", "B1", 101, 1, 2, 1, 1, "", 4_000, 1_000),
        NativeDetailedCell("table-1", "A2", 102, 1, 1, 1, 1, "", 2_000, 1_000),
        NativeDetailedCell("table-1", "B2", 103, 1, 1, 1, 1, "", 2_000, 1_000),
        NativeDetailedCell("table-1", "C2", 104, 1, 1, 1, 1, "", 2_000, 1_000),
    )
    return NativeDetailedInspection(
        17,
        "C:/documents/sample.hwp",
        1,
        1,
        "",
        (control,),
        cells,
        (),
    )


def _with_dense_geometry_target(
    plan: TableFormatCommandPlan,
) -> TableFormatCommandPlan:
    prepared = PreparedFormatOperation(
        plan,
        plan.table.instance_id,
        plan.table.basis,
        plan.cells,
    )
    resolved = recipe._with_topology_cell_geometry_targets(
        prepared,
        _dense_detail(plan.table.rows or 0, plan.table.columns or 0),
    )
    assert isinstance(resolved.plan, TableFormatCommandPlan)
    return resolved.plan


@pytest.mark.parametrize(
    ("rows", "columns"),
    ((10, 10), (20, 20), (50, 20)),
)
def test_row_and_column_size_commands_scale_with_axes(
    rows: int,
    columns: int,
) -> None:
    commands = build_native_format_commands(
        _plan(
            rows,
            columns,
            _formatting(width=18.0, height=9.0),
        )
    )

    actions = tuple(
        command.action for command in commands if isinstance(command, RunCommand)
    )

    assert len(commands) == 3 + 4 * (rows + columns)
    assert actions.count("TableCellBlockCol") == columns
    assert actions.count("TableCellBlockRow") == rows


def _semantic_effect(
    commands: tuple[object, ...],
) -> tuple[
    dict[int, float],
    dict[int, float],
    dict[str, tuple[tuple[str, tuple[object, ...]], ...]],
]:
    current_cell = ""
    selected_axis: str | None = None
    widths: dict[int, float] = {}
    heights: dict[int, float] = {}
    formats: dict[str, dict[str, tuple[object, ...]]] = {}
    for command in commands:
        if isinstance(command, CellCommand):
            current_cell = command.address
            selected_axis = None
        elif isinstance(command, RunCommand):
            if command.action == "TableCellBlockCol":
                selected_axis = "column"
            elif command.action == "TableCellBlockRow":
                selected_axis = "row"
            elif command.action.startswith("TableVAlign"):
                formats.setdefault(current_cell, {})["vertical_alignment"] = (
                    command.action,
                )
        elif isinstance(command, ParameterActionCommand):
            size = next(
                (
                    setter.value.value
                    for setter in command.setters
                    if isinstance(setter.value, MillimeterValue)
                    and setter.path in {"ShapeTableCell/Width", "ShapeTableCell/Height"}
                ),
                None,
            )
            row, column = (
                table_cell_coordinate(current_cell) if current_cell else (0, 0)
            )
            if size is not None and selected_axis == "column":
                widths[column] = size
            elif size is not None and selected_axis == "row":
                heights[row] = size
            elif command.action != "TablePropertyDialog":
                formats.setdefault(current_cell, {})[command.action] = tuple(
                    command.setters
                )
    return (
        widths,
        heights,
        {
            address: tuple(sorted(cell_formats.items()))
            for address, cell_formats in formats.items()
        },
    )


def test_axis_deduplication_preserves_the_legacy_formatting_effect() -> None:
    plan = _plan(
        4,
        5,
        _formatting(width=18.0, height=9.0, fill=True, border=True),
    )
    legacy_columns = tuple(
        AxisSizeTarget(f"column:legacy:{index}", cell, (cell,))
        for index, cell in enumerate(plan.cells)
    )
    legacy_rows = tuple(
        AxisSizeTarget(f"row:legacy:{index}", cell, (cell,))
        for index, cell in enumerate(plan.cells)
    )
    legacy = TableFormatCommandPlan(
        plan.formatting,
        plan.table,
        plan.cells,
        legacy_columns,
        legacy_rows,
    )

    optimized_commands = build_native_format_commands(plan)
    legacy_commands = build_native_format_commands(legacy)

    assert len(optimized_commands) < len(legacy_commands)
    assert _semantic_effect(optimized_commands) == _semantic_effect(legacy_commands)


def test_large_cell_format_is_split_by_command_and_readback_budgets() -> None:
    plan = _plan(
        50,
        20,
        _formatting(width=18.0, height=9.0, fill=True, border=True),
    )

    execution = build_native_format_execution_plan(plan)

    assert len(execution.batches) > 1
    assert all(
        len(batch.commands) <= FORMAT_COMMAND_BUDGET for batch in execution.batches
    )
    assert all(
        batch.readback_cost <= FORMAT_READBACK_BUDGET for batch in execution.batches
    )


@pytest.mark.parametrize(
    ("rows", "columns"),
    ((10, 10), (20, 20), (50, 20)),
)
def test_dense_cell_padding_uses_one_range_geometry_command(
    rows: int,
    columns: int,
) -> None:
    plan = _with_dense_geometry_target(
        _plan(
            rows,
            columns,
            _formatting(
                width=7.5,
                height=4,
                padding=True,
                font_size_pt=1,
                line_spacing_percent=50,
            ),
        )
    )

    assert plan.cell_geometry_targets == (
        CellRangeTarget(
            "cell_geometry:range",
            "A1",
            columns - 1,
            rows - 1,
            plan.cells,
        ),
    )
    commands = build_native_format_commands(plan)
    padding_actions = tuple(
        (index, command)
        for index, command in enumerate(commands)
        if isinstance(command, ParameterActionCommand)
        and command.action == "TablePropertyDialog"
        and any(setter.path == "ShapeTableCell/HasMargin" for setter in command.setters)
    )
    assert len(padding_actions) == 1
    padding_index, _padding_action = padding_actions[0]
    assert commands[padding_index + 1] == RunCommand("Cancel")


def test_asymmetric_merged_padding_keeps_the_legacy_per_cell_fallback() -> None:
    cells = ("A1", "B1", "A2", "B2", "C2")
    plan = TableFormatCommandPlan(
        _formatting(padding=True),
        ResolvedTable("table-1", "target.control_instance_id", 1, 2, 3),
        cells,
    )
    prepared = PreparedFormatOperation(
        plan,
        plan.table.instance_id,
        plan.table.basis,
        plan.cells,
    )

    resolved = recipe._with_topology_cell_geometry_targets(
        prepared,
        _asymmetric_merged_detail(),
    )

    assert isinstance(resolved.plan, TableFormatCommandPlan)
    assert resolved.plan.cell_geometry_targets is None
    assert resolved.plan.table_cell_count == 5
    expected: list[object] = [
        SelectControlCommand(plan.table.instance_id),
        CaptureTableCommand(),
    ]
    formatting_commands = cell_format_commands(plan.formatting.formatting)
    for cell in cells:
        expected.extend((CellCommand(cell), *formatting_commands))
    assert build_native_format_commands(resolved.plan) == tuple(expected)


def test_cell_geometry_budget_counts_every_topology_rebuild_as_m_times_c() -> None:
    plan = _plan(20, 20, _formatting(padding=True))

    execution = build_native_format_execution_plan(plan)

    cell_count = len(plan.cells)
    assert len(execution.batches) > 1
    assert sum(batch.topology_work for batch in execution.batches) == (
        cell_count * cell_count
    )
    assert all(
        batch.topology_work <= FORMAT_TOPOLOGY_WORK_BUDGET
        for batch in execution.batches
    )


@pytest.mark.parametrize(
    "plan",
    (
        _plan(
            1,
            1,
            _formatting(
                padding=True,
                fill=True,
                border=True,
                font_size_pt=1,
                line_spacing_percent=50,
            ),
        ),
        TableFormatCommandPlan(
            _formatting(
                padding=True,
                fill=True,
                border=True,
                font_size_pt=1,
                line_spacing_percent=50,
            ),
            ResolvedTable("table-1", "target.control_instance_id", 1, 2, 2),
            ("A1", "B2"),
        ),
    ),
    ids=("single-cell", "non-rectangular-fallback"),
)
def test_unoptimized_padding_keeps_the_legacy_per_cell_sequence(
    plan: TableFormatCommandPlan,
) -> None:
    expected: list[object] = [
        SelectControlCommand(plan.table.instance_id),
        CaptureTableCommand(),
    ]
    formatting_commands = cell_format_commands(plan.formatting.formatting)
    for cell in plan.cells:
        expected.extend((CellCommand(cell), *formatting_commands))

    assert build_native_format_commands(plan) == tuple(expected)


def _padding_effect(
    commands: tuple[object, ...],
) -> dict[str, tuple[object, ...]]:
    anchor = ""
    endpoint = ""
    extending = False
    effect: dict[str, tuple[object, ...]] = {}
    for command in commands:
        if isinstance(command, CellCommand):
            anchor = endpoint = command.address
            extending = False
        elif isinstance(command, RunCommand):
            if command.action == "TableCellBlockExtend":
                extending = True
            elif extending and command.action in {"TableRightCell", "TableLowerCell"}:
                row, column = table_cell_coordinate(endpoint)
                if command.action == "TableRightCell":
                    column += 1
                else:
                    row += 1
                endpoint = f"{_column_name(column)}{row}"
        elif (
            isinstance(command, ParameterActionCommand)
            and command.action == "TablePropertyDialog"
            and any(
                setter.path == "ShapeTableCell/HasMargin" for setter in command.setters
            )
        ):
            first_row, first_column = table_cell_coordinate(anchor)
            last_row, last_column = table_cell_coordinate(endpoint)
            value = tuple(command.setters)
            for row in range(first_row, last_row + 1):
                for column in range(first_column, last_column + 1):
                    effect[f"{_column_name(column)}{row}"] = value
    return effect


def test_range_padding_preserves_every_legacy_cell_effect() -> None:
    legacy = _plan(
        4,
        5,
        _formatting(
            padding=True,
            fill=True,
            border=True,
            font_size_pt=1,
            line_spacing_percent=50,
        ),
    )
    optimized = _with_dense_geometry_target(legacy)

    legacy_commands: tuple[object, ...] = (
        SelectControlCommand(legacy.table.instance_id),
        CaptureTableCommand(),
        *(
            command
            for cell in legacy.cells
            for command in (
                CellCommand(cell),
                *cell_format_commands(legacy.formatting.formatting),
            )
        ),
    )
    optimized_commands = build_native_format_commands(optimized)

    assert _semantic_effect(optimized_commands) == _semantic_effect(legacy_commands)
    assert _padding_effect(optimized_commands) == _padding_effect(legacy_commands)


def test_recipe_wires_dense_padding_to_one_range_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(10, 10, _formatting(padding=True))
    prepared = PreparedFormatOperation(
        plan,
        plan.table.instance_id,
        plan.table.basis,
        plan.cells,
    )
    requests: list[NativeActionRequest] = []

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 9
        requests.append(request)
        return _success(request)

    def inspect(_window_handle: int, _page: int) -> NativeDetailedInspection:
        return _dense_detail(10, 10)

    def snapshot(_window_handle: int) -> NativeSnapshot:
        return _snapshot()

    monkeypatch.setattr(recipe, "inspect_native_structure", inspect)
    monkeypatch.setattr(recipe, "execute_native_actions", execute)
    monkeypatch.setattr(recipe, "read_native_snapshot", snapshot)

    result = recipe._execute_prepared(_request(), _snapshot(), prepared)

    assert result.status == "executed"
    commands = tuple(command for request in requests for command in request.commands)
    padding_actions = tuple(
        command
        for command in commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "TablePropertyDialog"
        and any(setter.path == "ShapeTableCell/HasMargin" for setter in command.setters)
    )
    assert len(padding_actions) == 1
    actions = tuple(
        command.action for command in commands if isinstance(command, RunCommand)
    )
    assert "TableCellBlock" in actions
    assert "TableCellBlockExtend" in actions


def test_multi_cell_recipe_without_padding_adds_no_topology_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(
        10,
        10,
        _formatting(font_size_pt=1, line_spacing_percent=50),
    )
    prepared = PreparedFormatOperation(
        plan,
        plan.table.instance_id,
        plan.table.basis,
        plan.cells,
    )
    requests: list[NativeActionRequest] = []

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 9
        requests.append(request)
        return _success(request)

    def inspect(_window_handle: int, _page: int) -> NativeDetailedInspection:
        pytest.fail("no-padding multi-cell format inspected topology")

    def snapshot(_window_handle: int) -> NativeSnapshot:
        return _snapshot()

    monkeypatch.setattr(recipe, "inspect_native_structure", inspect)
    monkeypatch.setattr(recipe, "execute_native_actions", execute)
    monkeypatch.setattr(recipe, "read_native_snapshot", snapshot)

    result = recipe._execute_prepared(_request(), _snapshot(), prepared)

    assert result.status == "executed"
    assert len(requests) == 1
    assert requests[0].commands == build_native_format_commands(plan)


def test_no_padding_cell_format_keeps_the_per_cell_command_path() -> None:
    plan = _plan(
        20,
        20,
        _formatting(font_size_pt=1, line_spacing_percent=50),
    )
    commands = build_native_format_commands(plan)

    expected: list[object] = [
        SelectControlCommand(plan.table.instance_id),
        CaptureTableCommand(),
    ]
    formatting_commands = cell_format_commands(plan.formatting.formatting)
    for cell in plan.cells:
        expected.extend((CellCommand(cell), *formatting_commands))

    assert commands == tuple(expected)
    execution = build_native_format_execution_plan(plan)
    assert len(execution.batches) == 2
    assert (
        tuple(
            item.group.addresses[0]
            for batch in execution.batches
            for item in batch.groups
        )
        == plan.cells
    )
    assert all(
        len(batch.commands) <= FORMAT_COMMAND_BUDGET
        and batch.readback_cost <= FORMAT_READBACK_BUDGET
        and batch.topology_work == len(plan.cells)
        for batch in execution.batches
    )


@pytest.mark.parametrize(("rows", "columns"), ((1, 1), (2, 2)))
def test_small_formats_keep_the_single_call_command_sequence(
    rows: int,
    columns: int,
) -> None:
    plan = _plan(
        rows,
        columns,
        _formatting(width=18.0, height=9.0, fill=True, border=True),
    )

    execution = build_native_format_execution_plan(plan)

    assert len(execution.batches) == 1
    assert execution.batches[0].commands == build_native_format_commands(plan)


def _snapshot() -> NativeSnapshot:
    position = NativePosition(1, 0, 0)
    return NativeSnapshot(
        17,
        "C:/documents/sample.hwp",
        1,
        1,
        False,
        position,
        NativeSelection(False, position, position),
        "",
        "tbl",
        "table-1",
        "A1",
        0,
        NativeCharacterFormat("함초롬바탕", 1_000, False, 0),
        NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )


def _request() -> NativeFormatRecipeRequest:
    return cast(
        NativeFormatRecipeRequest,
        cast(
            object,
            SimpleNamespace(
                candidate=SimpleNamespace(window_handle=41, application=None),
                routing_page=SimpleNamespace(
                    document_id=17,
                    full_name="C:/documents/sample.hwp",
                ),
                resolution=SimpleNamespace(
                    workflow_id="table.format",
                    query="표 서식",
                    lookup_microseconds=1,
                    candidates=(),
                    steps=(),
                ),
                postconditions=SimpleNamespace(preserve_page_count=True),
            ),
        ),
    )


def _success(request: NativeActionRequest) -> NativeActionResult:
    return NativeActionResult(
        len(request.commands),
        0,
        0,
        0,
        100,
        (),
    )


def test_single_cell_recipe_adds_no_inspection_or_native_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = PreparedFormatOperation(
        _plan(1, 1, _formatting(fill=True)),
        "table-1",
        "target.control_instance_id",
        ("A1",),
    )
    requests: list[NativeActionRequest] = []

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 9
        requests.append(request)
        return _success(request)

    def snapshot(_window_handle: int) -> NativeSnapshot:
        return _snapshot()

    def inspect(
        _window_handle: int,
        _page: int,
    ) -> NativeDetailedInspection | None:
        pytest.fail("single-cell format inspected topology")

    monkeypatch.setattr(recipe, "execute_native_actions", execute)
    monkeypatch.setattr(recipe, "read_native_snapshot", snapshot)
    monkeypatch.setattr(recipe, "inspect_native_structure", inspect)

    result = recipe._execute_prepared(_request(), _snapshot(), prepared)

    assert result.status == "executed"
    assert len(requests) == 1
    assert requests[0].commands == build_native_format_commands(prepared.plan)


def _detail_without_column_anchors() -> NativeDetailedInspection:
    position = NativePosition(1, 0, 0)
    control = NativeDetailedControl(
        "tbl",
        "table-1",
        "",
        position,
        1,
        1,
        True,
        2,
        2,
        2_000,
        1_000,
    )
    cells = (
        NativeDetailedCell("table-1", "A1", 101, 1, 2, 1, 1, "", 2_000, 500),
        NativeDetailedCell("table-1", "A2", 102, 1, 2, 1, 1, "", 2_000, 500),
    )
    return NativeDetailedInspection(
        17,
        "C:/documents/sample.hwp",
        1,
        1,
        "",
        (control,),
        cells,
        (),
    )


def test_merged_axis_without_a_single_span_anchor_keeps_legacy_commands() -> None:
    plan = TableFormatCommandPlan(
        _formatting(width=18.0),
        ResolvedTable("table-1", "target.control_instance_id", 1, 2, 2),
        ("A1", "A2"),
    )
    prepared = PreparedFormatOperation(
        plan,
        "table-1",
        "target.control_instance_id",
        plan.cells,
    )

    resolved = recipe._with_topology_size_targets(
        prepared,
        _detail_without_column_anchors(),
    )

    assert isinstance(resolved.plan, TableFormatCommandPlan)
    assert resolved.plan.column_size_targets is not None
    assert (
        tuple(target.anchor for target in resolved.plan.column_size_targets)
        == plan.cells
    )
    assert sum(
        isinstance(command, RunCommand) and command.action == "TableCellBlockCol"
        for command in build_native_format_commands(resolved.plan)
    ) == len(plan.cells)


def test_later_format_batch_failure_reports_only_completed_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(20, 20, _formatting(fill=True, border=True))
    prepared = PreparedFormatOperation(
        plan,
        "table-1",
        "target.control_instance_id",
        plan.cells,
    )
    execution = build_native_format_execution_plan(plan)
    assert len(execution.batches) > 1
    first, second = execution.batches[:2]
    local_completed = second.groups[0].end_command + 1
    uncertain = second.groups[1].group.addresses
    calls = 0

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        nonlocal calls
        assert minimum_version == 9
        calls += 1
        if calls == 1:
            assert request.commands == first.commands
            return _success(request)
        raise NativeActionFailure(
            NativeActionFailureEvidence(
                code="POSTCONDITION",
                location="CellBorder",
                message="readback failed",
                commands_completed=local_completed,
                failed_step="CellBorder",
                partial_mutation=True,
                retry_safe=False,
                structure_digest_before="before",
                structure_digest_after="after",
            )
        )

    monkeypatch.setattr(recipe, "execute_native_actions", execute)

    result = recipe._execute_table_format_batches(
        _request(),
        _snapshot(),
        prepared,
    )

    assert not isinstance(result, tuple)
    expected_keys = {item.group.key for item in first.groups} | {
        second.groups[0].group.key
    }
    assert result.updated_addresses == execution.affected_addresses(expected_keys)
    assert result.commands_completed == len(first.commands) + local_completed
    assert result.partial_mutation is True
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert uncertain[0] in result.message


def test_failure_after_size_groups_reports_every_affected_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(
        50,
        20,
        _formatting(width=18.0, height=9.0, fill=True),
    )
    prepared = PreparedFormatOperation(
        plan,
        "table-1",
        "target.control_instance_id",
        plan.cells,
    )
    execution = build_native_format_execution_plan(plan)
    first, second = execution.batches[:2]
    first_keys = {item.group.key for item in first.groups}
    assert execution.completed_addresses(first_keys) != plan.cells
    assert execution.affected_addresses(first_keys) == plan.cells
    calls = 0

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        nonlocal calls
        assert minimum_version == 9
        calls += 1
        if calls == 1:
            return _success(request)
        raise NativeActionFailure(
            NativeActionFailureEvidence(
                code="POSTCONDITION",
                location="CellFill",
                message="readback failed",
                commands_completed=second.groups[0].start_command + 1,
                failed_step="CellFill",
                partial_mutation=True,
                retry_safe=False,
                structure_digest_before="before",
                structure_digest_after="after",
            )
        )

    monkeypatch.setattr(recipe, "execute_native_actions", execute)

    result = recipe._execute_table_format_batches(
        _request(),
        _snapshot(),
        prepared,
    )

    assert not isinstance(result, tuple)
    assert result.updated_addresses == plan.cells
    assert result.partial_mutation is True
    assert result.retry_safe is False


def test_replaying_absolute_format_batches_has_no_duplicate_effect() -> None:
    plan = _plan(
        50,
        20,
        _formatting(width=18.0, height=9.0, fill=True, border=True),
    )
    execution = build_native_format_execution_plan(plan)
    commands = tuple(
        command for batch in execution.batches for command in batch.commands
    )

    once = _semantic_effect(commands)
    twice = _semantic_effect((*commands, *commands))

    assert once == twice

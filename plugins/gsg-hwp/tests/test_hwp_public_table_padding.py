from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_native_format_recipe as recipe  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
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
)
from hwp_live_native_format_commands import (  # noqa: E402
    TableFormatCommandPlan,
    build_native_format_commands,
)
from hwp_live_native_format_contract import (  # noqa: E402
    NativeFormatRecipeRequest,
    PreparedFormatOperation,
)
from hwp_live_native_format_inputs import (  # noqa: E402
    InputFailure,
    TableFormatSpec,
    parse_table_format,
)
from hwp_live_native_format_target import ResolvedTable  # noqa: E402
from hwp_live_structure_contract import DocumentStructure  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
)
from hwp_public_table_edit_tools import HwpPublicTableEditTools  # noqa: E402
from hwp_live_table_contract import TableCell  # noqa: E402


class _CapturingExecutor:
    def __init__(self) -> None:
        self.inputs: list[HwpOperateInputs] = []

    async def read_table_structure(
        self,
        document_path: str | None,
        page: int,
    ) -> DocumentStructure:
        _ = document_path, page
        raise AssertionError(
            "direct public table formatting must not inspect structure"
        )

    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult:
        _ = guards
        self.inputs.append(inputs)
        return OperationResult(
            status="executed",
            query=intent,
            registry_entries=1,
            lookup_microseconds=0,
            message="captured",
        )


async def _public_padding_parameters() -> dict[str, str | int | float | bool]:
    executor = _CapturingExecutor()
    tools = HwpPublicTableEditTools(executor)

    result = await tools.hwp_format_table(
        operation_id="public-padding-flow",
        padding_left_mm=1.1,
        padding_right_mm=1.2,
        padding_top_mm=0.3,
        padding_bottom_mm=0.4,
    )

    assert result.status == "succeeded"
    assert len(executor.inputs) == 1
    return executor.inputs[0].parameters


async def _public_no_padding_parameters() -> dict[str, str | int | float | bool]:
    executor = _CapturingExecutor()
    tools = HwpPublicTableEditTools(executor)

    result = await tools.hwp_format_table(
        operation_id="public-no-padding-flow",
        font_size_pt=9.0,
    )

    assert result.status == "succeeded"
    assert len(executor.inputs) == 1
    return executor.inputs[0].parameters


def _detail() -> NativeDetailedInspection:
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
        4_000,
        2_000,
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
        for index, address in enumerate(("A1", "B1", "A2", "B2"))
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


def _merged_detail() -> NativeDetailedInspection:
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
        4_000,
        2_000,
    )
    cells = (
        NativeDetailedCell("table-1", "A1", 100, 1, 2, 1, 1, "", 4_000, 1_000),
        NativeDetailedCell("table-1", "A2", 101, 1, 1, 1, 1, "", 2_000, 1_000),
        NativeDetailedCell("table-1", "B2", 102, 1, 1, 1, 1, "", 2_000, 1_000),
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


def test_public_padding_reaches_recipe_range_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameters = anyio.run(_public_padding_parameters)
    # alignment/vertical_alignment are omitted while they are "inherit": a
    # neutral key is not a formatting request. parse_table_format defaults both
    # back to "inherit", so the parsed spec below is unchanged.
    assert parameters == {
        "padding_left_mm": 1.1,
        "padding_right_mm": 1.2,
        "padding_top_mm": 0.3,
        "padding_bottom_mm": 0.4,
    }
    parsed = parse_table_format(parameters)
    assert isinstance(parsed, TableFormatSpec)
    assert parsed.formatting.padding is not None
    assert parsed.formatting.padding.model_dump() == {
        "left_mm": 1.1,
        "right_mm": 1.2,
        "top_mm": 0.3,
        "bottom_mm": 0.4,
    }
    plan = TableFormatCommandPlan(
        parsed,
        ResolvedTable("table-1", "target.control_instance_id", 1, 2, 2),
        ("A1", "B1", "A2", "B2"),
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
        return NativeActionResult(len(request.commands), 0, 0, 0, 100, ())

    def inspect(
        _window_handle: int,
        _page: int,
    ) -> NativeDetailedInspection:
        return _detail()

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


def test_omitted_public_padding_preserves_the_previous_command_sequence() -> None:
    parameters = anyio.run(_public_no_padding_parameters)
    assert parameters == {"font_size_pt": 9.0}
    parsed = parse_table_format(parameters)
    assert isinstance(parsed, TableFormatSpec)
    assert parsed.formatting.padding is None
    legacy = TableFormatSpec(
        None,
        TableCell(font_size_pt=9.0),
        None,
        None,
    )
    table = ResolvedTable("table-1", "target.control_instance_id", 1, 1, 2)
    cells = ("A1", "B1")

    assert build_native_format_commands(
        TableFormatCommandPlan(parsed, table, cells)
    ) == build_native_format_commands(TableFormatCommandPlan(legacy, table, cells))


def test_public_padding_keeps_merged_table_per_cell_fallback() -> None:
    parameters = anyio.run(_public_padding_parameters)
    parsed = parse_table_format(parameters)
    assert isinstance(parsed, TableFormatSpec)
    plan = TableFormatCommandPlan(
        parsed,
        ResolvedTable("table-1", "target.control_instance_id", 1, 2, 2),
        ("A1", "A2", "B2"),
    )
    prepared = PreparedFormatOperation(
        plan,
        plan.table.instance_id,
        plan.table.basis,
        plan.cells,
    )

    resolved = recipe._with_topology_cell_geometry_targets(
        prepared,
        _merged_detail(),
    )

    assert isinstance(resolved.plan, TableFormatCommandPlan)
    assert resolved.plan.cell_geometry_targets is None
    commands = build_native_format_commands(resolved.plan)
    padding_actions = tuple(
        command
        for command in commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "TablePropertyDialog"
        and any(setter.path == "ShapeTableCell/HasMargin" for setter in command.setters)
    )
    assert len(padding_actions) == len(plan.cells)


@pytest.mark.parametrize(
    "parameters",
    (
        {"padding_left_mm": 1.0},
        {
            "padding_left_mm": -0.1,
            "padding_right_mm": 1.0,
            "padding_top_mm": 1.0,
            "padding_bottom_mm": 1.0,
        },
        {
            "padding_left_mm": 0.0,
            "padding_right_mm": 20.1,
            "padding_top_mm": 1.0,
            "padding_bottom_mm": 1.0,
        },
    ),
)
def test_padding_parser_rejects_partial_or_out_of_range_edges(
    parameters: dict[str, float],
) -> None:
    assert isinstance(parse_table_format(parameters), InputFailure)

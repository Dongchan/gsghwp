from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_operation_recipe as recipe  # noqa: E402
import hwp_live_session_structure_mutation as legacy  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import LiveHwpApplication  # noqa: E402
from hwp_live_contract import LayoutPlan  # noqa: E402
from hwp_live_native_action_contract import (  # noqa: E402
    NATIVE_ACTION_PAYLOAD_LIMIT,
    NativeActionFailure,
    NativeActionFailureEvidence,
    action_request_payload_length,
)
from hwp_live_native_action_models import (  # noqa: E402
    CellCommand,
    InsertTextCommand,
    MergeCommand,
    MillimeterValue,
    NativeActionRequest,
    NativeActionResult,
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSetter,
    ParameterActionCommand,
    RunCommand,
)
from hwp_live_native_action_results import NativeSnapshot  # noqa: E402
from hwp_live_native_layout import (  # noqa: E402
    LAYOUT_COMMAND_LIMIT,
    LAYOUT_TOPOLOGY_WORK_BUDGET,
    NativeLayoutContext,
    build_native_layout_execution_plan,
    build_native_layout_request,
)
from hwp_live_native_layout_format import (  # noqa: E402
    cell_padding_ranges,
    is_padding_command,
)
from hwp_live_native_table_layout import table_commands  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_operation_contract import OperationResult  # noqa: E402
from hwp_live_table_contract import (  # noqa: E402
    CellPadding,
    TableBlock,
    TableCell,
    TableMerge,
)
from hwp_reference_layout_geometry import SectionPageGeometry  # noqa: E402


PADDING_A = CellPadding(left_mm=1, right_mm=1, top_mm=1, bottom_mm=1)
PADDING_B = CellPadding(left_mm=2, right_mm=2, top_mm=0.5, bottom_mm=0.5)


class _CompletedLayoutFailure(Protocol):
    completed_batches: int
    completed_addresses: tuple[str, ...]


def _page() -> SectionPageGeometry:
    return SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=15,
        header_mm=0,
        footer_mm=0,
        gutter_mm=0,
        gutter_type=0,
    )


def _snapshot(*, modified: bool) -> NativeSnapshot:
    cursor = NativePosition(0, 0, 0)
    return NativeSnapshot(
        document_id=17,
        full_name="C:/documents/layout.hwp",
        current_page=1,
        page_count=1,
        modified=modified,
        cursor=cursor,
        selection=NativeSelection(False, cursor, cursor),
        selected_text="",
        control_type="",
        control_instance_id="",
        cell_address="",
        style_id=0,
        character_format=NativeCharacterFormat("함초롬바탕", 1000, False, 0),
        paragraph_format=NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )


def _success(request: NativeActionRequest) -> NativeActionResult:
    return NativeActionResult(
        commands_executed=len(request.commands),
        actions_executed=len(request.commands),
        text_insertions=0,
        image_insertions=0,
        elapsed_microseconds=len(request.commands),
        created_control_ids=("table-1",)
        if any(
            isinstance(command, ParameterActionCommand)
            and command.action == "TableCreate"
            for command in request.commands
        )
        else (),
    )


def _table(
    rows: int,
    columns: int,
    *,
    padding: str = "dense",
    widths: bool = False,
    heights: bool = False,
) -> TableBlock:
    def cell(row: int, column: int) -> TableCell:
        match padding:
            case "dense":
                value = PADDING_A
            case "checkerboard":
                value = PADDING_A if (row + column) % 2 == 0 else PADDING_B
            case _:
                value = None
        return TableCell(text=f"{row}:{column}", padding=value)

    return TableBlock(
        kind="table",
        base_style_id=0,
        rows=tuple(
            tuple(cell(row, column) for column in range(columns)) for row in range(rows)
        ),
        column_widths_mm=(tuple(8.0 for _ in range(columns)) if widths else None),
        row_heights_mm=tuple(5.0 for _ in range(rows)) if heights else None,
    )


def _plan(table: TableBlock) -> LayoutPlan:
    return LayoutPlan(blocks=(table,))


def _request(plan: LayoutPlan, *, atomic: bool = False) -> NativeActionRequest:
    before = _snapshot(modified=False)
    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=before.document_id,
            full_name=before.full_name,
            style_ids=(),
            page_count=before.page_count,
            page_geometry=_page(),
            expected_cursor=before.cursor,
        ),
        plan,
        {},
    )
    if atomic:
        return NativeActionRequest(
            request.document_id,
            request.full_name,
            request.commands,
            request.expected_cursor,
            request.expected_selection,
            True,
        )
    return request


def _coordinate(address: str) -> tuple[int, int]:
    split = 0
    while split < len(address) and address[split].isalpha():
        split += 1
    column = 0
    for letter in address[:split]:
        column = column * 26 + ord(letter) - ord("A") + 1
    return int(address[split:]) - 1, column - 1


def _address(row: int, column: int) -> str:
    letters = ""
    value = column + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"


def _padding_effect(
    commands: tuple[object, ...],
) -> dict[str, tuple[NativeSetter, ...]]:
    anchor = ""
    endpoint = ""
    extending = False
    selected: tuple[str, ...] = ()
    effect: dict[str, tuple[NativeSetter, ...]] = {}
    for command in commands:
        if isinstance(command, CellCommand):
            anchor = endpoint = command.address
            selected = (anchor,)
            extending = False
        elif isinstance(command, RunCommand):
            if command.action == "TableCellBlockExtend":
                extending = True
            elif extending and command.action in {
                "TableRightCell",
                "TableLowerCell",
            }:
                row, column = _coordinate(endpoint)
                if command.action == "TableRightCell":
                    column += 1
                else:
                    row += 1
                endpoint = _address(row, column)
                top, left = _coordinate(anchor)
                bottom, right = _coordinate(endpoint)
                selected = tuple(
                    _address(cell_row, cell_column)
                    for cell_row in range(min(top, bottom), max(top, bottom) + 1)
                    for cell_column in range(
                        min(left, right),
                        max(left, right) + 1,
                    )
                )
            elif command.action == "Cancel":
                extending = False
        elif isinstance(command, ParameterActionCommand) and is_padding_command(
            command
        ):
            for address in selected:
                effect[address] = command.setters
    return effect


def _padding_actions(
    commands: tuple[object, ...],
) -> tuple[ParameterActionCommand, ...]:
    return tuple(
        command
        for command in commands
        if isinstance(command, ParameterActionCommand) and is_padding_command(command)
    )


def test_dense_padding_is_one_safe_rectangle_with_identical_effect() -> None:
    table = _table(3, 4)
    raw = table_commands(table, {}, {}, {}, {})
    optimized = _request(_plan(table)).commands

    assert cell_padding_ranges(table)[0].addresses == (
        "A1",
        "B1",
        "C1",
        "D1",
        "A2",
        "B2",
        "C2",
        "D2",
        "A3",
        "B3",
        "C3",
        "D3",
    )
    assert len(_padding_actions(raw)) == 12
    assert len(_padding_actions(optimized)) == 1
    padding_index = next(
        index
        for index, command in enumerate(optimized)
        if isinstance(command, ParameterActionCommand) and is_padding_command(command)
    )
    assert optimized[padding_index - 8 : padding_index] == (
        CellCommand("A1"),
        RunCommand("TableCellBlock"),
        RunCommand("TableCellBlockExtend"),
        RunCommand("TableRightCell"),
        RunCommand("TableRightCell"),
        RunCommand("TableRightCell"),
        RunCommand("TableLowerCell"),
        RunCommand("TableLowerCell"),
    )
    assert optimized[padding_index + 1] == RunCommand("Cancel")
    assert _padding_effect(raw) == _padding_effect(optimized)


def test_distinct_padding_values_form_independent_safe_rectangles() -> None:
    rows = tuple(
        tuple(
            TableCell(padding=PADDING_A if column < 2 else PADDING_B)
            for column in range(4)
        )
        for _ in range(2)
    )
    table = TableBlock(kind="table", base_style_id=0, rows=rows)

    ranges = cell_padding_ranges(table)
    optimized = _request(_plan(table)).commands

    assert tuple(target.addresses for target in ranges) == (
        ("A1", "B1", "A2", "B2"),
        ("C1", "D1", "C2", "D2"),
    )
    assert len(_padding_actions(optimized)) == 2
    assert _padding_effect(table_commands(table, {}, {}, {}, {})) == _padding_effect(
        optimized
    )


@pytest.mark.parametrize(
    "table",
    (
        TableBlock(
            kind="table",
            base_style_id=0,
            rows=(
                (TableCell(padding=PADDING_A), TableCell(padding=PADDING_A)),
                (TableCell(padding=PADDING_A), TableCell()),
            ),
        ),
        TableBlock(
            kind="table",
            base_style_id=0,
            rows=(
                (
                    TableCell(padding=PADDING_A),
                    TableCell(padding=PADDING_A),
                    TableCell(padding=PADDING_A),
                ),
                (
                    TableCell(padding=PADDING_A),
                    TableCell(),
                    TableCell(padding=PADDING_A),
                ),
                (
                    TableCell(padding=PADDING_A),
                    TableCell(padding=PADDING_A),
                    TableCell(padding=PADDING_A),
                ),
            ),
        ),
        TableBlock(
            kind="table",
            base_style_id=0,
            rows=(
                (TableCell(padding=PADDING_A), TableCell()),
                (TableCell(), TableCell(padding=PADDING_A)),
            ),
        ),
        TableBlock(
            kind="table",
            base_style_id=0,
            rows=(
                (TableCell(padding=PADDING_A), TableCell()),
                (TableCell(padding=PADDING_A), TableCell()),
            ),
            merges=(TableMerge(row=0, column=0, row_span=1, column_span=2),),
        ),
    ),
    ids=("l-shape", "hole", "scattered", "merged-footprint"),
)
def test_unsafe_padding_regions_keep_exact_per_cell_fallback(
    table: TableBlock,
) -> None:
    raw = table_commands(table, {}, {}, {}, {})

    assert cell_padding_ranges(table) == ()
    assert _request(_plan(table)).commands == raw


def test_grouping_preserves_header_geometry_and_merge_anchor_order() -> None:
    table = TableBlock(
        kind="table",
        base_style_id=0,
        repeat_header=True,
        rows=(
            (
                TableCell(padding=PADDING_A),
                TableCell(),
                TableCell(padding=PADDING_B),
            ),
            (
                TableCell(padding=PADDING_A),
                TableCell(),
                TableCell(padding=PADDING_B),
            ),
            (TableCell(), TableCell(), TableCell(padding=PADDING_B)),
        ),
        column_widths_mm=(12, 13, 14),
        row_heights_mm=(6, 7, 8),
        merges=(TableMerge(row=0, column=0, row_span=1, column_span=2),),
    )
    raw = table_commands(table, {}, {}, {}, {})
    optimized = _request(_plan(table)).commands
    raw_size_actions = tuple(
        command
        for command in raw
        if isinstance(command, ParameterActionCommand)
        and command.action == "TablePropertyDialog"
        and not is_padding_command(command)
    )
    optimized_size_actions = tuple(
        command
        for command in optimized
        if isinstance(command, ParameterActionCommand)
        and command.action == "TablePropertyDialog"
        and not is_padding_command(command)
    )

    assert optimized_size_actions == raw_size_actions
    merge_index = optimized.index(MergeCommand("A1", "B1"))
    assert any(is_padding_command(command) for command in optimized[merge_index:])
    geometry_cells = 6
    main_cells = 9
    cell_positions = tuple(
        index
        for index, command in enumerate(optimized)
        if isinstance(command, CellCommand)
    )
    second_geometry = cell_positions[geometry_cells + main_cells + 1]
    grouped_b_index = next(
        index
        for index, command in enumerate(optimized)
        if isinstance(command, ParameterActionCommand)
        and is_padding_command(command)
        and NativeSetter(
            "ShapeTableCell/MarginLeft",
            MillimeterValue(PADDING_B.left_mm),
        )
        in command.setters
    )
    assert grouped_b_index < second_geometry
    assert _padding_effect(raw) == _padding_effect(optimized)


def test_topology_budget_splits_below_command_limit_by_m_times_c() -> None:
    request = _request(_plan(_table(20, 20, padding="checkerboard")))

    execution = build_native_layout_execution_plan(request)

    assert len(request.commands) < LAYOUT_COMMAND_LIMIT
    assert len(execution.batches) > 1
    assert sum(batch.topology_work for batch in execution.batches) == 160_000
    assert all(
        batch.topology_work <= LAYOUT_TOPOLOGY_WORK_BUDGET
        for batch in execution.batches
    )
    assert (
        tuple(
            command for batch in execution.batches for command in batch.request.commands
        )
        == request.commands
    )


def test_completed_addresses_are_table_qualified_for_multiple_tables() -> None:
    plan = LayoutPlan(
        blocks=(
            _table(10, 10, padding="checkerboard"),
            _table(20, 20, padding="checkerboard"),
        )
    )
    execution = build_native_layout_execution_plan(_request(plan))
    completed_keys = {
        item.group.key for batch in execution.batches for item in batch.groups
    }

    completed = execution.completed_addresses(completed_keys)

    assert tuple(batch.topology_work for batch in execution.batches) == (
        100_000,
        70_000,
    )
    assert len(completed) == 500
    assert len(set(completed)) == 500
    assert "table[1]:A1" in completed
    assert "table[2]:A1" in completed
    first_table_keys = {
        item.group.key
        for batch in execution.batches
        for item in batch.groups
        if item.group.table_index == 0
    }
    first_table_only = execution.completed_addresses(first_table_keys)
    assert len(first_table_only) == 100
    assert all(address.startswith("table[1]:") for address in first_table_only)


def test_small_layout_keeps_the_original_request_and_one_cold_call() -> None:
    request = _request(_plan(_table(10, 10, padding="checkerboard")))

    execution = build_native_layout_execution_plan(request)

    assert len(execution.batches) == 1
    assert execution.batches[0].request is request
    assert execution.batches[0].topology_work == 10_000


@pytest.mark.parametrize(
    ("rows", "columns", "expected_batches"),
    ((10, 10, 1), (20, 20, 1), (20, 50, 2)),
)
def test_dense_size_and_padding_layout_matches_live_scale_batching(
    rows: int,
    columns: int,
    expected_batches: int,
) -> None:
    table = _table(
        rows,
        columns,
        padding="dense",
        widths=True,
        heights=True,
    )
    raw = table_commands(table, {}, {}, {}, {})
    request = _request(_plan(table))

    execution = build_native_layout_execution_plan(request)

    assert len(_padding_actions(request.commands)) == 1
    assert len(request.commands) < len(raw)
    assert len(execution.batches) == expected_batches
    assert all(
        batch.topology_work <= LAYOUT_TOPOLOGY_WORK_BUDGET
        for batch in execution.batches
    )
    if expected_batches == 1:
        assert execution.batches[0].request is request


def test_command_limit_split_counts_each_new_call_cold_start() -> None:
    create = next(
        command
        for command in _request(_plan(_table(1, 1))).commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "TableCreate"
    )
    request = NativeActionRequest(
        17,
        "C:/documents/layout.hwp",
        (
            create,
            *(CellCommand("A1") for _ in range(LAYOUT_COMMAND_LIMIT + 1)),
        ),
    )

    execution = build_native_layout_execution_plan(request)

    assert len(execution.batches) == 2
    assert tuple(batch.topology_work for batch in execution.batches) == (1, 1)


def test_payload_limit_splits_before_native_encoding() -> None:
    request = NativeActionRequest(
        17,
        "C:/documents/layout.hwp",
        (
            InsertTextCommand("a" * 3_100_000),
            InsertTextCommand("b" * 3_100_000),
        ),
        expected_cursor=NativePosition(0, 0, 0),
    )

    execution = build_native_layout_execution_plan(request)

    assert len(execution.batches) == 2
    assert (
        tuple(
            command for batch in execution.batches for command in batch.request.commands
        )
        == request.commands
    )
    assert all(
        action_request_payload_length(batch.request) <= NATIVE_ACTION_PAYLOAD_LIMIT
        for batch in execution.batches
    )
    assert execution.batches[0].request.expected_cursor == NativePosition(0, 0, 0)
    assert execution.batches[1].request.expected_cursor is None


def test_atomic_payload_over_limit_is_rejected_before_dispatch() -> None:
    request = NativeActionRequest(
        17,
        "C:/documents/layout.hwp",
        (
            InsertTextCommand("a" * 3_100_000),
            InsertTextCommand("b" * 3_100_000),
        ),
        atomic=True,
    )

    with pytest.raises(HwpLiveError, match="원자 레이아웃 요청"):
        _ = build_native_layout_execution_plan(request)


def test_single_command_group_over_payload_limit_is_rejected() -> None:
    request = NativeActionRequest(
        17,
        "C:/documents/layout.hwp",
        (InsertTextCommand("a" * 6_000_000),),
    )

    with pytest.raises(HwpLiveError, match="단일 레이아웃 명령 그룹"):
        _ = build_native_layout_execution_plan(request)


def test_atomic_oversize_preserves_one_rollback_request() -> None:
    request = _request(
        LayoutPlan(
            target="document_end",
            blocks=(_table(20, 20, padding="checkerboard"),),
        ),
        atomic=True,
    )

    execution = build_native_layout_execution_plan(request)

    assert len(execution.batches) == 1
    assert execution.batches[0].request is request
    assert execution.batches[0].request.atomic is True


def _operate(
    monkeypatch: pytest.MonkeyPatch,
    plan: LayoutPlan,
    execute: Callable[..., NativeActionResult],
) -> tuple[OperationResult, set[str]]:
    before = _snapshot(modified=False)
    after = _snapshot(modified=True)
    snapshots = iter((before, after))

    def read_snapshot(_window_handle: int) -> NativeSnapshot:
        return next(snapshots)

    def style_plan(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[LayoutPlan, SectionPageGeometry, tuple[()]]:
        return plan, _page(), ()

    def preflight(
        *_args: object,
        **_kwargs: object,
    ) -> SimpleNamespace:
        return SimpleNamespace(overflow="none")

    def prepare(_plan: LayoutPlan) -> dict[Path, Path]:
        return {}

    monkeypatch.setattr(recipe, "read_native_snapshot", read_snapshot)
    monkeypatch.setattr(recipe, "native_style_plan", style_plan)
    monkeypatch.setattr(recipe, "preflight_layout", preflight)
    monkeypatch.setattr(recipe, "prepare_layout_assets", prepare)
    monkeypatch.setattr(recipe, "execute_native_actions", execute)
    unsafe: set[str] = set()
    candidate = cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                selector="doc-selector",
                window_handle=41,
                document_id=before.document_id,
                full_name=before.full_name,
            ),
        ),
    )
    result = recipe.operate_layout(
        candidate,
        "layout",
        plan,
        resolve_only=False,
        allow_document_change=True,
        expected_cursor=None,
        unsafe_selectors=unsafe,
        guard=lambda: None,
    )
    return result, unsafe


def test_operate_layout_wires_oversize_plan_to_multiple_native_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_table(20, 20, padding="checkerboard"))
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

    result, unsafe = _operate(monkeypatch, plan, execute)
    canonical = _request(plan)

    assert result.status == "executed"
    assert len(requests) > 1
    assert requests[0].expected_cursor == canonical.expected_cursor
    assert all(request.expected_cursor is None for request in requests[1:])
    assert all(
        request.commands and isinstance(request.commands[0], CellCommand)
        for request in requests[1:]
    )
    assert (
        tuple(command for request in requests for command in request.commands)
        == canonical.commands
    )
    assert (
        sum(
            isinstance(command, ParameterActionCommand)
            and command.action == "TableCreate"
            for request in requests
            for command in request.commands
        )
        == 1
    )
    assert unsafe == set()


def test_operate_layout_small_request_calls_native_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_table(10, 10, padding="checkerboard"))
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

    result, _ = _operate(monkeypatch, plan, execute)

    assert result.status == "executed"
    assert len(requests) == 1


def test_intermediate_failure_reports_only_completed_ranges_without_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_table(20, 20, padding="checkerboard"))
    canonical = _request(plan)
    execution = build_native_layout_execution_plan(canonical)
    first, second = execution.batches[:2]
    local_completed = second.groups[0].end_command
    completed_keys = {item.group.key for item in first.groups} | set(
        second.completed_group_keys(local_completed)
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
        if len(requests) == 1:
            return _success(request)
        raise NativeActionFailure(
            NativeActionFailureEvidence(
                code="ACTION_FAILED",
                location="TablePropertyDialog",
                message="readback failed",
                commands_completed=local_completed,
                failed_step="TablePropertyDialog",
                partial_mutation=True,
                retry_safe=False,
                structure_digest_before="before",
                structure_digest_after="after",
            )
        )

    result, unsafe = _operate(monkeypatch, plan, execute)

    assert len(requests) == 2
    assert requests[0].commands + requests[1].commands == canonical.commands
    assert result.status == "partial_change"
    assert result.commands_completed == len(first.request.commands) + local_completed
    assert result.updated_addresses == execution.completed_addresses(completed_keys)
    assert result.partial_mutation is True
    assert result.retry_safe is False
    assert result.failed_step == "TablePropertyDialog"
    assert result.reconcile_required is True
    assert unsafe == {"doc-selector"}


class _LegacyHwp:
    def get_pos(self) -> tuple[int, int, int]:
        return (0, 0, 0)

    def get_selected_pos(
        self,
    ) -> tuple[bool, int, int, int, int, int, int]:
        return (False, 0, 0, 0, 0, 0, 0)


def _patch_legacy(
    monkeypatch: pytest.MonkeyPatch,
    plan: LayoutPlan,
    execute: Callable[..., NativeActionResult],
) -> tuple[HwpDocumentCandidate, set[str]]:
    def resolve(
        _hwp: object,
        _plan: LayoutPlan,
        _guard: Callable[[], None],
    ) -> LayoutPlan:
        return plan

    def validate(*_args: object, **_kwargs: object) -> None:
        return None

    def styles(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(styles=())

    def read_snapshot(_window_handle: int) -> NativeSnapshot:
        return _snapshot(modified=True)

    monkeypatch.setattr(legacy, "resolve_layout_styles", resolve)
    monkeypatch.setattr(legacy, "validate_layout_anchor", validate)
    monkeypatch.setattr(legacy, "inspect_styles", styles)
    monkeypatch.setattr(legacy, "execute_native_actions", execute)
    monkeypatch.setattr(legacy, "read_native_snapshot", read_snapshot)
    candidate = cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                selector="legacy-selector",
                window_handle=42,
                document_id=17,
                full_name="C:/documents/layout.hwp",
            ),
        ),
    )
    return candidate, set()


def test_legacy_layout_uses_the_same_multi_call_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_table(20, 20, padding="checkerboard"))
    requests: list[NativeActionRequest] = []

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 4
        requests.append(request)
        return _success(request)

    candidate, unsafe = _patch_legacy(monkeypatch, plan, execute)
    result = legacy.apply_validated_layout(
        cast(LiveHwpApplication, cast(object, _LegacyHwp())),
        candidate,
        plan,
        {},
        unsafe,
        (0, 0, 0),
        None,
        lambda: None,
    )

    assert len(requests) > 1
    assert result.blocks_applied == 1
    assert result.native_elapsed_microseconds == sum(
        len(request.commands) for request in requests
    )
    assert unsafe == set()


def test_legacy_failure_offsets_native_evidence_without_replaying_prior_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_table(20, 20, padding="checkerboard"))
    canonical = _request(plan)
    execution = build_native_layout_execution_plan(canonical)
    first = execution.batches[0]
    requests: list[NativeActionRequest] = []

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 4
        requests.append(request)
        if len(requests) == 1:
            return _success(request)
        raise NativeActionFailure(
            NativeActionFailureEvidence(
                code="ACTION_FAILED",
                location="TablePropertyDialog",
                message="failed",
                commands_completed=3,
                failed_step="TablePropertyDialog",
                partial_mutation=False,
                retry_safe=True,
                structure_digest_before="same",
                structure_digest_after="same",
            )
        )

    candidate, unsafe = _patch_legacy(monkeypatch, plan, execute)
    with pytest.raises(NativeActionFailure) as raised:
        _ = legacy.apply_validated_layout(
            cast(LiveHwpApplication, cast(object, _LegacyHwp())),
            candidate,
            plan,
            {},
            unsafe,
            (0, 0, 0),
            None,
            lambda: None,
        )

    assert len(requests) == 2
    assert requests[0].commands + requests[1].commands == canonical.commands
    assert raised.value.commands_completed == len(first.request.commands) + 3
    completed_failure = cast(
        _CompletedLayoutFailure,
        cast(object, raised.value),
    )
    completed_keys = {item.group.key for item in first.groups}
    assert completed_failure.completed_batches == 1
    assert completed_failure.completed_addresses == execution.completed_addresses(
        completed_keys
    )
    assert raised.value.partial_mutation is True
    assert raised.value.retry_safe is False
    assert raised.value.failed_step == "TablePropertyDialog"
    assert unsafe == {"legacy-selector"}


def test_legacy_first_batch_transport_failure_keeps_unknown_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_table(20, 20, padding="checkerboard"))
    requests: list[NativeActionRequest] = []

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 4
        requests.append(request)
        raise HwpLiveError("transport unavailable before native evidence")

    candidate, unsafe = _patch_legacy(monkeypatch, plan, execute)
    with pytest.raises(HwpLiveError) as raised:
        _ = legacy.apply_validated_layout(
            cast(LiveHwpApplication, cast(object, _LegacyHwp())),
            candidate,
            plan,
            {},
            unsafe,
            (0, 0, 0),
            None,
            lambda: None,
        )

    assert len(requests) == 1
    assert not isinstance(raised.value, NativeActionFailure)
    assert unsafe == {"legacy-selector"}

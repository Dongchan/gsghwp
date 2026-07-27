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
    NativePageControl,
    NativePageInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
)
from hwp_live_native_format_contract import (  # noqa: E402
    NativeFormatRecipeRequest,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_catalog import (  # noqa: E402
    host_visible_tool_catalog,
    proxy_tool_catalog,
    qa_tool_catalog,
    worker_tool_catalog,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateGuards,
    HwpOperateInputs,
    HwpOperatePostconditions,
    HwpOperateTarget,
    OperationInputValue,
    OperationResult,
    WorkflowResolution,
)
from hwp_public_table_edit_tools import HwpPublicTableEditTools  # noqa: E402
from hwp_live_structure_contract import DocumentStructure  # noqa: E402


# A message that tells a model to supply a cell is what made it invent
# addresses. These fragments must never appear in a merge/split failure.
_INVITATION_FRAGMENTS = (
    "셀 주소",
    "지정",
    "다시 요청",
    "입력하세요",
    "전달하세요",
    "예:",
    "A1",
    "B2",
)


def _assert_states_only_what_is_missing(message: str) -> None:
    for fragment in _INVITATION_FRAGMENTS:
        assert fragment not in message, f"{fragment!r} in {message!r}"


def _cell(
    table: str,
    address: str,
    list_id: int,
    *,
    row_span: int = 1,
    column_span: int = 1,
) -> NativeDetailedCell:
    return NativeDetailedCell(
        table, address, list_id, row_span, column_span, 1, 1, "", 1_000, 1_000
    )


def _control(table: str, *, rows: int, columns: int) -> NativeDetailedControl:
    return NativeDetailedControl(
        "tbl",
        table,
        "",
        NativePosition(1, 0, 0),
        1,
        1,
        True,
        rows,
        columns,
        2_000,
        1_000,
    )


def _detail(
    controls: tuple[NativeDetailedControl, ...],
    cells: tuple[NativeDetailedCell, ...],
) -> NativeDetailedInspection:
    return NativeDetailedInspection(
        17, "C:/documents/sample.hwp", 1, 1, "", controls, cells, ()
    )


def _plain_2x3() -> NativeDetailedInspection:
    # table-1: list ids 101..106 reading order.
    return _detail(
        (_control("table-1", rows=2, columns=3),),
        (
            _cell("table-1", "A1", 101),
            _cell("table-1", "B1", 102),
            _cell("table-1", "C1", 103),
            _cell("table-1", "A2", 104),
            _cell("table-1", "B2", 105),
            _cell("table-1", "C2", 106),
        ),
    )


def _row_merged_2x3() -> NativeDetailedInspection:
    # B1 spans columns 2-3, so the rectangle A1..B2 would cut it in half.
    return _detail(
        (_control("table-1", rows=2, columns=3),),
        (
            _cell("table-1", "A1", 101),
            _cell("table-1", "B1", 102, column_span=2),
            _cell("table-1", "A2", 104),
            _cell("table-1", "B2", 105),
            _cell("table-1", "C2", 106),
        ),
    )


def _two_tables() -> NativeDetailedInspection:
    return _detail(
        (
            _control("table-1", rows=2, columns=2),
            _control("table-2", rows=2, columns=2),
        ),
        (
            _cell("table-1", "A1", 101),
            _cell("table-1", "B1", 102),
            _cell("table-1", "A2", 103),
            _cell("table-1", "B2", 104),
            _cell("table-2", "A1", 201),
            _cell("table-2", "B1", 202),
            _cell("table-2", "A2", 203),
            _cell("table-2", "B2", 204),
        ),
    )


def _merged_a1_b2() -> NativeDetailedInspection:
    return _detail(
        (_control("table-1", rows=2, columns=3),),
        (
            _cell("table-1", "A1", 101, row_span=2, column_span=2),
            _cell("table-1", "C1", 103),
            _cell("table-1", "C2", 106),
        ),
    )


def _split_a1() -> NativeDetailedInspection:
    # A1 split into two columns: the grid gains a physical column, so row 2's
    # first cell stretches across columns 1-2 and 7 cells remain.
    return _detail(
        (_control("table-1", rows=2, columns=4),),
        (
            _cell("table-1", "A1", 101),
            _cell("table-1", "B1", 107),
            _cell("table-1", "C1", 102),
            _cell("table-1", "D1", 103),
            _cell("table-1", "A2", 104, column_span=2),
            _cell("table-1", "C2", 105),
            _cell("table-1", "D2", 106),
        ),
    )


def _snapshot(
    *,
    start_list_id: int,
    end_list_id: int,
    table: str = "table-1",
    control_type: str = "tbl",
    cell_address: str = "A1",
) -> NativeSnapshot:
    caret = NativePosition(start_list_id, 0, 0)
    return NativeSnapshot(
        17,
        "C:/documents/sample.hwp",
        1,
        1,
        False,
        caret,
        NativeSelection(
            start_list_id != end_list_id,
            NativePosition(start_list_id, 0, 0),
            NativePosition(end_list_id, 0, 0),
            3,
        ),
        "",
        control_type,
        table if control_type == "tbl" else "",
        cell_address,
        0,
        NativeCharacterFormat("함초롬바탕", 1_000, False, 0),
        NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )


def _request(
    workflow: str,
    parameters: dict[str, OperationInputValue],
    *,
    target: HwpOperateTarget | None = None,
    page_tables: tuple[str, ...] = ("table-1",),
    columns: int = 3,
) -> NativeFormatRecipeRequest:
    position = NativePosition(1, 0, 0)
    return NativeFormatRecipeRequest(
        candidate=cast(
            HwpDocumentCandidate,
            cast(object, SimpleNamespace(window_handle=41, application=None)),
        ),
        routing_page=NativePageInspection(
            17,
            "C:/documents/sample.hwp",
            1,
            1,
            "",
            tuple(
                NativePageControl("tbl", table, position, 2, columns)
                for table in page_tables
            ),
        ),
        resolution=cast(
            WorkflowResolution,
            cast(
                object,
                SimpleNamespace(
                    workflow_id=workflow,
                    query="표 셀 편집",
                    lookup_microseconds=1,
                    candidates=(),
                    steps=(),
                ),
            ),
        ),
        target=target,
        parameters=parameters,
        postconditions=cast(
            HwpOperatePostconditions,
            cast(object, SimpleNamespace(preserve_page_count=True)),
        ),
        resolve_only=False,
        allow_document_change=True,
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
    inspections: tuple[NativeDetailedInspection, ...],
) -> tuple[OperationResult | None, tuple[NativeActionRequest, ...]]:
    remaining = list(inspections)
    native_requests: list[NativeActionRequest] = []

    def execute(
        _window_handle: int,
        native_request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 9
        native_requests.append(native_request)
        return NativeActionResult(len(native_request.commands), 0, 0, 0, 100, ())

    def inspect(_window_handle: int, _page: int) -> NativeDetailedInspection | None:
        return remaining.pop(0) if remaining else None

    monkeypatch.setattr(recipe, "read_native_snapshot", lambda _window: before)
    monkeypatch.setattr(recipe, "inspect_native_structure", inspect)
    monkeypatch.setattr(recipe, "execute_native_actions", execute)
    return (
        recipe.operate_native_format_recipe(request),
        tuple(native_requests),
    )


# --- supplied addresses must reach prepare untouched ----------------------


def test_supplied_merge_addresses_never_enter_selection_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: both addresses are present, so nothing may be resolved or read.
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("supplied addresses must not resolve a selection")

    monkeypatch.setattr(recipe, "resolve_native_table_target", forbidden)
    request = _request("table.merge_cells", {"start": "A1", "end": "B2"})
    before = _snapshot(start_list_id=101, end_list_id=105)

    unchanged = recipe._request_with_selected_cells(
        "table.merge_cells",
        request,
        before,
    )

    # Then: the very same request object continues, parameters included.
    assert unchanged is request
    assert unchanged.parameters == {"start": "A1", "end": "B2"}


def test_supplied_split_address_never_enters_selection_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a supplied address must not resolve a selection")

    monkeypatch.setattr(recipe, "resolve_native_table_target", forbidden)
    parameters: dict[str, OperationInputValue] = {
        "cell": "A1",
        "columns": 2,
        "rows": 1,
    }
    request = _request("table.split_cells", parameters)
    before = _snapshot(start_list_id=101, end_list_id=101)

    unchanged = recipe._request_with_selected_cells(
        "table.split_cells",
        request,
        before,
    )

    assert unchanged is request
    assert unchanged.parameters == parameters


def test_supplied_addresses_emit_the_same_native_merge_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the address path reads structure exactly twice, before and after.
    request = _request("table.merge_cells", {"start": "A1", "end": "B2"})
    before = _snapshot(start_list_id=101, end_list_id=105)

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_plain_2x3(), _merged_a1_b2()),
    )

    assert result is not None
    assert result.status == "executed"
    assert result.updated_addresses == ("A1", "B2")
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "B2")


# --- the current selection fills what the caller omitted ------------------


def test_selected_cell_block_merges_without_any_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the user selected A1..B2 and passed no address at all.
    request = _request("table.merge_cells", {})
    before = _snapshot(start_list_id=101, end_list_id=105)

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_plain_2x3(), _merged_a1_b2()),
    )

    # Then: the selection produced the same command an address pair produces.
    assert result is not None
    assert result.status == "executed"
    assert result.updated_addresses == ("A1", "B2")
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "B2")


def test_selected_cell_block_uses_merged_spans_for_its_corners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: B1 spans columns 2-3, and the selection runs B1..A2. The rectangle
    # therefore reaches column 3 even though no address names it.
    request = _request("table.merge_cells", {})
    before = _snapshot(start_list_id=102, end_list_id=104)
    merged = _detail(
        (_control("table-1", rows=2, columns=3),),
        (_cell("table-1", "A1", 101, row_span=2, column_span=3),),
    )

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_row_merged_2x3(), merged),
    )

    assert result is not None
    assert result.status == "executed"
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "C2")


def test_merge_start_only_is_completed_from_the_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the caller named only the start cell, and A1..B2 is selected.
    request = _request("table.merge_cells", {"start": "A1"})
    before = _snapshot(start_list_id=101, end_list_id=105)

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_plain_2x3(), _merged_a1_b2()),
    )

    # Then: only the omitted end is taken from the selection.
    assert result is not None
    assert result.status == "executed"
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "B2")


def test_merge_end_only_is_completed_from_the_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the caller named only the end cell, and A1..B2 is selected.
    request = _request("table.merge_cells", {"end": "B2"})
    before = _snapshot(start_list_id=101, end_list_id=105)

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_plain_2x3(), _merged_a1_b2()),
    )

    assert result is not None
    assert result.status == "executed"
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "B2")


def test_both_addresses_ignore_a_selection_pointing_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Safety: a request carrying both addresses must keep behaving exactly as
    # it did before half-address completion existed. C1..C2 is selected and
    # must change nothing; only two structure reads may happen, the pre-edit
    # and the post-edit one, with no selection read in front of them.
    request = _request("table.merge_cells", {"start": "A1", "end": "B2"})
    before = _snapshot(start_list_id=103, end_list_id=106)

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_plain_2x3(), _merged_a1_b2()),
    )

    assert result is not None
    assert result.status == "executed"
    assert result.updated_addresses == ("A1", "B2")
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "B2")


def test_start_only_selection_outside_the_target_table_is_refused_by_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: target names table-1 while the selection lives in table-2, and
    # only the start address was supplied.
    #
    # The refusal below is NOT a rule written for the selection path. It is
    # TableTopology.selection_region_by_list_ids in
    # hwp_live_native_table_topology.py - the same function an explicit
    # address pair reaches through topology_preflight -> merge_region. The
    # asserted message is that function's own wording, which is how this test
    # shows where the decision was made. A second copy of the rule here would
    # drift from the address path and become the next bug.
    request = _request(
        "table.merge_cells",
        {"start": "A1"},
        target=HwpOperateTarget(kind="table", control_instance_id="table-1"),
        page_tables=("table-1", "table-2"),
        columns=2,
    )
    before = _snapshot(start_list_id=201, end_list_id=204, table="table-2")

    result, native_requests = _run(monkeypatch, request, before, (_two_tables(),))

    assert result is not None
    assert result.status == "schema_conflict"
    assert result.message == "선택 시작·끝 위치가 대상 표의 실제 셀에 없습니다"
    assert native_requests == ()


def test_start_only_with_the_caret_in_that_same_cell_is_refused_as_one_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: start is A1 and the caret sits in A1 with nothing selected, so
    # the completed pair names a single cell. parse_merge already rejects that
    # for a caller who sends the same address twice; no new rule is added.
    request = _request("table.merge_cells", {"start": "A1"})
    before = _snapshot(start_list_id=101, end_list_id=101)

    result, native_requests = _run(monkeypatch, request, before, (_plain_2x3(),))

    assert result is not None
    assert result.status == "schema_conflict"
    assert result.message == "병합할 서로 다른 두 셀을 확인하지 못했습니다"
    assert native_requests == ()


def test_caret_cell_splits_without_any_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the caret sits in A1 with no block selected.
    parameters: dict[str, OperationInputValue] = {
        "columns": 2,
        "rows": 1,
        "distribute_height": False,
        "merge": False,
        "split_mode": "equal",
    }
    request = _request("table.split_cells", parameters)
    before = _snapshot(start_list_id=101, end_list_id=101)

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_plain_2x3(), _split_a1()),
    )

    assert result is not None
    assert result.status == "executed"
    assert native_requests != ()


# --- refusals that already existed, reached through the selection ---------
#
# None of the three below is a selection-only rule. Every one is decided by
# TableTopology.selection_region_by_list_ids in
# hwp_live_native_table_topology.py, the same function an explicit address pair
# reaches through topology_preflight -> merge_region. The asserted messages are
# that function's own wording, which is how these tests show where the decision
# was made.


def test_selection_outside_the_target_table_is_refused_by_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: target names table-1 while the selection lives in table-2.
    request = _request(
        "table.merge_cells",
        {},
        target=HwpOperateTarget(kind="table", control_instance_id="table-1"),
        page_tables=("table-1", "table-2"),
        columns=2,
    )
    before = _snapshot(start_list_id=201, end_list_id=204, table="table-2")

    result, native_requests = _run(monkeypatch, request, before, (_two_tables(),))

    assert result is not None
    assert result.status == "schema_conflict"
    assert result.message == "선택 시작·끝 위치가 대상 표의 실제 셀에 없습니다"
    assert native_requests == ()


def test_selection_spanning_two_tables_is_refused_by_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the selection starts in table-1 and ends in table-2.
    request = _request(
        "table.merge_cells",
        {},
        target=HwpOperateTarget(kind="table", control_instance_id="table-1"),
        page_tables=("table-1", "table-2"),
        columns=2,
    )
    before = _snapshot(start_list_id=101, end_list_id=204)

    result, native_requests = _run(monkeypatch, request, before, (_two_tables(),))

    assert result is not None
    assert result.status == "schema_conflict"
    assert result.message == "선택 시작·끝 위치가 대상 표의 실제 셀에 없습니다"
    assert native_requests == ()


def test_selection_that_tears_a_merged_cell_is_refused_by_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: A1..B2 cuts the merged B1 in half.
    request = _request("table.merge_cells", {})
    before = _snapshot(start_list_id=101, end_list_id=105)

    result, native_requests = _run(monkeypatch, request, before, (_row_merged_2x3(),))

    assert result is not None
    assert result.status == "schema_conflict"
    assert result.message == "선택 셀 영역이 빈틈 없는 직사각형이 아닙니다"
    assert native_requests == ()


# --- nothing to work from -------------------------------------------------


def test_no_selection_and_no_address_fails_without_baiting_an_invented_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the caret is outside any table and no address was passed.
    request = _request("table.merge_cells", {})
    before = _snapshot(
        start_list_id=1,
        end_list_id=1,
        control_type="",
        cell_address="",
    )

    result, native_requests = _run(monkeypatch, request, before, (_plain_2x3(),))

    assert result is not None
    assert result.status == "schema_conflict"
    assert native_requests == ()
    _assert_states_only_what_is_missing(result.message)


def test_no_resolvable_table_reports_the_missing_input_without_bait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the page holds no table, so no selection can be read at all.
    request = _request("table.merge_cells", {}, page_tables=())
    before = _snapshot(
        start_list_id=1,
        end_list_id=1,
        control_type="",
        cell_address="",
    )

    result, native_requests = _run(monkeypatch, request, before, ())

    assert result is not None
    assert result.status in {"needs_input", "not_found", "ambiguous"}
    assert native_requests == ()
    _assert_states_only_what_is_missing(result.message)


def test_caret_alone_cannot_name_two_cells_to_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the caret sits in one cell with nothing selected.
    request = _request("table.merge_cells", {})
    before = _snapshot(start_list_id=101, end_list_id=101)

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_plain_2x3(), _plain_2x3()),
    )

    assert result is not None
    assert result.status == "schema_conflict"
    assert native_requests == ()
    _assert_states_only_what_is_missing(result.message)


def test_multi_cell_selection_does_not_pick_a_split_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: four cells are selected, which names no single cell to split.
    parameters: dict[str, OperationInputValue] = {"columns": 2, "rows": 1}
    request = _request("table.split_cells", parameters)
    before = _snapshot(start_list_id=101, end_list_id=105)

    result, native_requests = _run(
        monkeypatch,
        request,
        before,
        (_plain_2x3(), _plain_2x3()),
    )

    assert result is not None
    assert result.status == "needs_input"
    assert native_requests == ()
    _assert_states_only_what_is_missing(result.message)


# --- public tool surface --------------------------------------------------


class _CapturingExecutor:
    def __init__(self) -> None:
        self.inputs: list[HwpOperateInputs] = []

    async def read_table_structure(
        self,
        document_path: str | None,
        page: int,
    ) -> DocumentStructure:
        _ = document_path, page
        raise AssertionError("a direct address must not inspect structure")

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


def _public_merge(
    start_cell: str | None,
    end_cell: str | None,
) -> dict[str, OperationInputValue]:
    async def run() -> dict[str, OperationInputValue]:
        executor = _CapturingExecutor()
        tools = HwpPublicTableEditTools(executor)
        result = await tools.hwp_merge_table_cells(
            operation_id="public-merge-flow",
            start_cell=start_cell,
            end_cell=end_cell,
        )
        assert result.status == "succeeded"
        assert len(executor.inputs) == 1
        return executor.inputs[0].parameters

    return anyio.run(run)


def _public_split(cell: str | None) -> dict[str, OperationInputValue]:
    async def run() -> dict[str, OperationInputValue]:
        executor = _CapturingExecutor()
        tools = HwpPublicTableEditTools(executor)
        result = await tools.hwp_split_table_cell(
            operation_id="public-split-flow",
            cell=cell,
            columns=2,
            rows=1,
        )
        assert result.status == "succeeded"
        assert len(executor.inputs) == 1
        return executor.inputs[0].parameters

    return anyio.run(run)


def test_public_merge_with_both_addresses_sends_the_same_parameters() -> None:
    assert _public_merge("a1", "b2") == {"start": "A1", "end": "B2"}


def test_public_split_with_an_address_sends_the_same_parameters() -> None:
    assert _public_split("a1") == {
        "cell": "A1",
        "columns": 2,
        "rows": 1,
        "distribute_height": False,
        "merge": False,
        "split_mode": "equal",
    }


def test_public_merge_without_addresses_sends_no_cell_parameters() -> None:
    assert _public_merge(None, None) == {}


def test_public_split_without_an_address_sends_no_cell_parameter() -> None:
    assert _public_split(None) == {
        "columns": 2,
        "rows": 1,
        "distribute_height": False,
        "merge": False,
        "split_mode": "equal",
    }


def test_public_merge_with_one_address_leaves_the_other_missing() -> None:
    assert _public_merge("a1", None) == {"start": "A1"}


def test_merge_and_split_cell_inputs_are_optional_in_the_public_schema() -> None:
    server = build_server(LiveHwpController(), profile="production")

    async def listed() -> dict[str, object]:
        return {tool.name: tool.inputSchema for tool in await server.list_tools()}

    schemas = anyio.run(listed)
    merge = cast(dict[str, object], schemas["hwp_merge_table_cells"])
    split = cast(dict[str, object], schemas["hwp_split_table_cell"])
    merge_required = cast(list[str], merge.get("required", []))
    split_required = cast(list[str], split.get("required", []))

    assert "start_cell" not in merge_required
    assert "end_cell" not in merge_required
    assert "cell" not in split_required
    # columns/rows stay required: a split still has to say into how many.
    assert "columns" in split_required
    assert "rows" in split_required
    assert "start_cell" in cast(dict[str, object], merge["properties"])
    assert "end_cell" in cast(dict[str, object], merge["properties"])
    assert "cell" in cast(dict[str, object], split["properties"])


def test_tool_counts_are_unchanged() -> None:
    def fresh(profile: str) -> tuple[object, ...]:
        server = build_server(LiveHwpController(), profile=profile)

        async def listed() -> tuple[object, ...]:
            return tuple(await server.list_tools())

        return anyio.run(listed)

    worker = worker_tool_catalog(cast(tuple, fresh("production")))
    assert worker.count == 44
    assert proxy_tool_catalog().count == 1
    assert host_visible_tool_catalog(worker.tools).count == 45
    assert qa_tool_catalog(cast(tuple, fresh("qa"))).count == 62

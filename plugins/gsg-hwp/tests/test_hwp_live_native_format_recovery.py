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
from hwp_errors import HwpLiveError  # noqa: E402
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
from hwp_live_native_format_commands import MergeCommandPlan  # noqa: E402
from hwp_live_native_format_contract import (  # noqa: E402
    NativeFormatRecipeRequest,
    PreparedFormatOperation,
)
from hwp_live_native_format_inputs import (  # noqa: E402
    MergeSpec,
    parse_merge,
)
from hwp_live_native_format_target import ResolvedTable  # noqa: E402
from hwp_live_native_history import NativeHistoryResult  # noqa: E402
from hwp_mcp_result_envelope import transport_error_result  # noqa: E402
from hwp_operation_contract import HwpOperateInputs, OperationResult  # noqa: E402


def _cell(
    address: str,
    list_id: int,
    *,
    row_span: int = 1,
    column_span: int = 1,
) -> NativeDetailedCell:
    return NativeDetailedCell(
        "table-1",
        address,
        list_id,
        row_span,
        column_span,
        1,
        1,
        "",
        1_000,
        1_000,
    )


def _detail(
    cells: tuple[NativeDetailedCell, ...],
    *,
    rows: int,
    columns: int,
) -> NativeDetailedInspection:
    control = NativeDetailedControl(
        "tbl",
        "table-1",
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


def _prepared_merge(start: str, end: str) -> PreparedFormatOperation:
    plan = MergeCommandPlan(
        MergeSpec(start, end),
        ResolvedTable("table-1", "target.control_instance_id", 1, 2, 2),
    )
    return PreparedFormatOperation(
        plan,
        "table-1",
        "target.control_instance_id",
        (start, end),
    )


def _grid_2x2() -> NativeDetailedInspection:
    return _detail(
        (
            _cell("A1", 101),
            _cell("B1", 102),
            _cell("A2", 103),
            _cell("B2", 104),
        ),
        rows=2,
        columns=2,
    )


def _merged_2x2() -> NativeDetailedInspection:
    return _detail(
        (_cell("A1", 101, row_span=2, column_span=2),),
        rows=2,
        columns=2,
    )


def _wrong_merge_2x2() -> NativeDetailedInspection:
    # Hancom merged only the first row instead of the requested rectangle.
    return _detail(
        (
            _cell("A1", 101, column_span=2),
            _cell("A2", 103),
            _cell("B2", 104),
        ),
        rows=2,
        columns=2,
    )


def _history(calls: list[tuple[int, str, int]]):
    def execute_history(
        window_handle: int,
        direction: str,
        steps: int,
    ) -> NativeHistoryResult:
        calls.append((window_handle, direction, steps))
        return NativeHistoryResult("undo", 1, 7)

    return execute_history


def _envelope(error: HwpLiveError) -> OperationResult:
    return transport_error_result(
        HwpOperateInputs(request_id="op-1", operation="table.merge_cells"),
        error,
        intent="표 셀 병합",
    )


# --- (1) verified auto-Undo must report the document as unchanged ---------


def test_verified_undo_reports_no_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the merge really changed the table, verification rejected the
    # result, and the Undo restored the exact pre-edit CellTopology.
    before = _grid_2x2()
    inspections = iter((_wrong_merge_2x2(), before))
    calls: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        recipe,
        "inspect_native_structure",
        lambda _window, _page: next(inspections),
    )
    monkeypatch.setattr(recipe, "execute_native_history", _history(calls))

    # When: the structural postflight runs.
    with pytest.raises(HwpLiveError) as raised:
        recipe._verify_structural_plan(_prepared_merge("A1", "B2"), 41, before, 1)

    # Then: the Undo still runs. Removing this recovery is what would hurt.
    assert calls == [(41, "undo", 1)]
    assert raised.value.mutation_started is False
    envelope = _envelope(raised.value)
    assert envelope.changed is False
    assert envelope.retry_safe is True
    # And: the claim reaches only as far as the evidence. A matching
    # CellTopology is not proof that the whole document is pre-edit state.
    assert "문서는 작업 전 상태입니다" not in raised.value.reason
    assert "CellTopology" in raised.value.reason
    assert "확인하지 않았습니다" in raised.value.reason
    assert envelope.partial_change is False


def test_unverified_undo_stays_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the Undo did NOT restore the pre-edit CellTopology.
    before = _grid_2x2()
    inspections = iter((_wrong_merge_2x2(), _merged_2x2()))
    calls: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        recipe,
        "inspect_native_structure",
        lambda _window, _page: next(inspections),
    )
    monkeypatch.setattr(recipe, "execute_native_history", _history(calls))

    with pytest.raises(HwpLiveError) as raised:
        recipe._verify_structural_plan(_prepared_merge("A1", "B2"), 41, before, 1)

    # Then: no unchanged claim is made and the caller must reconcile.
    assert raised.value.mutation_started is None
    envelope = _envelope(raised.value)
    assert envelope.changed is True
    assert envelope.retry_safe is False


def test_unreadable_post_edit_structure_never_undoes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the post-edit structure could not be read, so nothing proves the
    # native command mutated this table at all. An Undo here reverts whatever
    # the previous history entry is, which may be an earlier, unrelated edit.
    before = _grid_2x2()
    inspections: list[NativeDetailedInspection | None] = [None, before]
    calls: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        recipe,
        "inspect_native_structure",
        lambda _window, _page: inspections.pop(0),
    )
    monkeypatch.setattr(recipe, "execute_native_history", _history(calls))

    with pytest.raises(HwpLiveError) as raised:
        recipe._verify_structural_plan(_prepared_merge("A1", "B2"), 41, before, 1)

    # Then: no Undo ran at all, and the report says so.
    assert calls == []
    assert "자동 Undo를 실행하지 않았습니다" in raised.value.reason
    assert raised.value.mutation_started is None
    envelope = _envelope(raised.value)
    assert envelope.changed is True
    assert envelope.retry_safe is False


def test_unchanged_topology_never_undoes_an_unrelated_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the post-edit CellTopology equals the pre-edit one, so this call
    # left the table exactly as it found it. The only thing an Undo could
    # revert here is somebody else's earlier edit.
    before = _grid_2x2()
    inspections = iter((_grid_2x2(),))
    calls: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        recipe,
        "inspect_native_structure",
        lambda _window, _page: next(inspections),
    )
    monkeypatch.setattr(recipe, "execute_native_history", _history(calls))

    with pytest.raises(HwpLiveError) as raised:
        recipe._verify_structural_plan(_prepared_merge("A1", "B2"), 41, before, 1)

    assert calls == []
    assert "자동 Undo를 실행하지 않았습니다" in raised.value.reason


def test_a_skipped_undo_admits_the_document_state_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Skipping the Undo may leave a partial change behind. Saying nothing
    # about that would be worse than the blind Undo it replaces.
    before = _grid_2x2()
    inspections: list[NativeDetailedInspection | None] = [None]
    monkeypatch.setattr(
        recipe,
        "inspect_native_structure",
        lambda _window, _page: inspections.pop(0),
    )
    monkeypatch.setattr(recipe, "execute_native_history", _history([]))

    with pytest.raises(HwpLiveError) as raised:
        recipe._verify_structural_plan(_prepared_merge("A1", "B2"), 41, before, 1)

    reason = raised.value.reason
    assert "자동 Undo를 실행하지 않았습니다" in reason
    assert "확정할 수 없습니다" in reason
    # No recovery claim may appear when no recovery was attempted.
    assert "복구했습니다" not in reason
    assert raised.value.mutation_started is None


def test_failed_undo_recovery_stays_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the Undo itself failed.
    before = _grid_2x2()
    inspections = iter((_wrong_merge_2x2(),))

    def undo(_window_handle: int, _direction: str, _steps: int) -> NativeHistoryResult:
        raise HwpLiveError("한컴 Undo 메서드가 실행 이력 변경을 확인하지 못했습니다")

    monkeypatch.setattr(
        recipe,
        "inspect_native_structure",
        lambda _window, _page: next(inspections),
    )
    monkeypatch.setattr(recipe, "execute_native_history", undo)

    with pytest.raises(HwpLiveError, match="자동 Undo 복구 검증에도 실패") as raised:
        recipe._verify_structural_plan(_prepared_merge("A1", "B2"), 41, before, 1)

    assert raised.value.mutation_started is None
    envelope = _envelope(raised.value)
    assert envelope.changed is True
    assert envelope.retry_safe is False


# --- (3) reversed merge endpoints describe the same rectangle -------------


def test_parse_merge_accepts_reversed_endpoints() -> None:
    parsed = parse_merge({"start": "D5", "end": "B2"})

    assert isinstance(parsed, MergeSpec)
    assert (parsed.start, parsed.end) == ("D5", "B2")


def test_parse_merge_still_rejects_a_single_cell() -> None:
    parsed = parse_merge({"start": "B2", "end": "B2"})

    assert not isinstance(parsed, MergeSpec)
    assert parsed.status == "schema_conflict"


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


def _merge_request(start: str, end: str) -> NativeFormatRecipeRequest:
    position = NativePosition(1, 0, 0)
    return cast(
        NativeFormatRecipeRequest,
        cast(
            object,
            SimpleNamespace(
                candidate=SimpleNamespace(window_handle=41, application=None),
                routing_page=NativePageInspection(
                    17,
                    "C:/documents/sample.hwp",
                    1,
                    1,
                    "",
                    (NativePageControl("tbl", "table-1", position, 2, 3),),
                ),
                resolution=SimpleNamespace(
                    workflow_id="table.merge_cells",
                    query="표 셀 병합",
                    lookup_microseconds=1,
                    candidates=(),
                    steps=(),
                ),
                target=None,
                parameters={"start": start, "end": end},
                postconditions=SimpleNamespace(preserve_page_count=True),
                resolve_only=False,
                allow_document_change=True,
            ),
        ),
    )


def _run_merge(
    monkeypatch: pytest.MonkeyPatch,
    start: str,
    end: str,
    before: NativeDetailedInspection,
    after: NativeDetailedInspection,
) -> tuple[OperationResult | None, tuple[NativeActionRequest, ...]]:
    snapshot = _snapshot()
    inspections = iter((before, after))
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

    monkeypatch.setattr(recipe, "read_native_snapshot", lambda _window: snapshot)
    monkeypatch.setattr(
        recipe,
        "inspect_native_structure",
        lambda _window, _page: next(inspections),
    )
    monkeypatch.setattr(recipe, "execute_native_actions", execute)
    return (
        recipe.operate_native_format_recipe(_merge_request(start, end)),
        tuple(native_requests),
    )


def _row_merged_grid() -> NativeDetailedInspection:
    # 2x3 grid whose B1 spans columns 2-3.
    return _detail(
        (
            _cell("A1", 101),
            _cell("B1", 102, column_span=2),
            _cell("A2", 104),
            _cell("B2", 105),
            _cell("C2", 106),
        ),
        rows=2,
        columns=3,
    )


def test_reversed_endpoints_merge_the_same_rectangle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a plain 2x3 grid and the two opposite corners given back to front.
    before = _detail(
        (
            _cell("A1", 101),
            _cell("B1", 102),
            _cell("C1", 103),
            _cell("A2", 104),
            _cell("B2", 105),
            _cell("C2", 106),
        ),
        rows=2,
        columns=3,
    )
    after = _detail(
        (
            _cell("A1", 101, row_span=2, column_span=2),
            _cell("C1", 103),
            _cell("C2", 106),
        ),
        rows=2,
        columns=3,
    )

    result, native_requests = _run_merge(monkeypatch, "B2", "A1", before, after)

    assert result is not None
    assert result.status == "executed"
    assert result.updated_addresses == ("A1", "B2")
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "B2")


def test_reversed_endpoints_use_merged_spans_not_string_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: B1 already spans columns 2-3, so the rectangle that contains
    # B1 and A2 reaches column 3 even though neither address mentions it.
    before = _row_merged_grid()
    after = _detail(
        (_cell("A1", 101, row_span=2, column_span=3),),
        rows=2,
        columns=3,
    )

    result, native_requests = _run_merge(monkeypatch, "B1", "A2", before, after)

    assert result is not None
    assert result.status == "executed"
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "C2")


def test_ordered_endpoints_keep_the_untouched_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: endpoints already in canonical order must not be rewritten.
    before = _grid_2x2()
    after = _merged_2x2()

    result, native_requests = _run_merge(monkeypatch, "A1", "B2", before, after)

    assert result is not None
    assert result.status == "executed"
    assert result.updated_addresses == ("A1", "B2")
    merge_command = native_requests[0].commands[-1]
    assert (merge_command.first, merge_command.second) == ("A1", "B2")


def test_reversed_endpoint_that_is_not_a_real_cell_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: C1 is covered by the merged B1 and is therefore not a cell of its
    # own, so no rectangle may be invented for it.
    before = _row_merged_grid()
    result, native_requests = _run_merge(monkeypatch, "C1", "A1", before, before)

    assert result is not None
    assert result.status == "schema_conflict"
    assert native_requests == ()
    assert result.changed is False
    assert result.retry_safe is True


def test_reversed_endpoints_still_reject_a_torn_rectangle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the rectangle spanned by B2 and A1 cuts the merged B1 in half.
    before = _row_merged_grid()
    result, native_requests = _run_merge(monkeypatch, "B2", "A1", before, before)

    assert result is not None
    assert result.status == "schema_conflict"
    assert native_requests == ()
    assert result.changed is False
    assert result.retry_safe is True

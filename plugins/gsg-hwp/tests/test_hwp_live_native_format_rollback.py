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
import hwp_live_native_format_target as target_module  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeDetailedCell,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativePageControl,
    NativePageInspection,
    NativePosition,
)
from hwp_live_native_format_commands import (  # noqa: E402
    SplitCommandPlan,
    TableFormatCommandPlan,
)
from hwp_live_native_format_contract import PreparedFormatOperation  # noqa: E402
from hwp_live_native_format_inputs import SplitSpec, TableFormatSpec  # noqa: E402
from hwp_live_native_format_target import ResolvedTable  # noqa: E402
from hwp_live_native_history import NativeHistoryResult  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_table_contract import TableCell  # noqa: E402


def _cell(
    address: str,
    list_id: int,
    *,
    row_span: int = 1,
    column_span: int = 1,
    width_hwpunit: int = 1_000,
    height_hwpunit: int = 1_000,
) -> NativeDetailedCell:
    return NativeDetailedCell(
        "table-1",
        address,
        list_id,
        row_span,
        column_span,
        1,
        1,
        "보존 텍스트" if address == "A1" else "",
        width_hwpunit,
        height_hwpunit,
    )


def _detail(
    cells: tuple[NativeDetailedCell, ...],
    *,
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
        1,
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


def _prepared_split() -> PreparedFormatOperation:
    split = SplitSpec(
        cell="A1",
        columns=2,
        rows=1,
        distribute_height=False,
        merge=False,
        split_mode="existing_grid",
    )
    plan = SplitCommandPlan(
        split,
        ResolvedTable("table-1", "target.control_instance_id", 1, 1, 2),
    )
    return PreparedFormatOperation(
        plan,
        "table-1",
        "target.control_instance_id",
        ("A1",),
    )


def test_table_resize_postflight_follows_table_after_page_reflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: resizing a selected cell moves its table from page 11 to page 12.
    formatting = TableFormatSpec(None, TableCell(), 8.0, None)
    plan = TableFormatCommandPlan(
        formatting,
        ResolvedTable("table-1", "target.control_instance_id", 11, 3, 6),
        ("B2",),
    )
    prepared = PreparedFormatOperation(
        plan,
        "table-1",
        "target.control_instance_id",
        ("B2",),
    )
    resized = _detail((_cell("B2", 102, height_hwpunit=2_268),), columns=6)
    inspected_pages: list[int] = []

    def inspect(_window_handle: int, page: int) -> NativeDetailedInspection:
        inspected_pages.append(page)
        return resized

    monkeypatch.setattr(recipe, "inspect_native_structure", inspect)

    # When: the postflight verifies the successful resize.
    recipe._verify_structural_plan(prepared, 41, None, 12)

    # Then: it follows the table to the page reported by the post-edit snapshot.
    assert inspected_pages == [12]


def test_table_resize_postflight_scales_expected_size_by_cell_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    formatting = TableFormatSpec(None, TableCell(), 26.0, 20.0)
    plan = TableFormatCommandPlan(
        formatting,
        ResolvedTable("table-1", "target.control_instance_id", 12, 3, 6),
        ("A1",),
    )
    prepared = PreparedFormatOperation(
        plan,
        "table-1",
        "target.control_instance_id",
        ("A1",),
    )
    resized = _detail(
        (
            _cell(
                "A1",
                101,
                row_span=2,
                column_span=3,
                width_hwpunit=17_007,
                height_hwpunit=14_740,
            ),
        ),
        columns=6,
    )
    monkeypatch.setattr(recipe, "inspect_native_structure", lambda _window, _page: resized)

    recipe._verify_structural_plan(prepared, 41, None, 12)


def test_explicit_table_id_follows_the_control_to_its_actual_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = NativePageInspection(17, "C:/documents/sample.hwp", 1, 12, "", ())
    target_control = NativePageControl(
        "tbl",
        "table-1",
        NativePosition(1, 0, 0),
        3,
        6,
    )
    inspected_pages: list[int] = []

    def inspect(
        _window_handle: int,
        page: int,
        *,
        include_cells: bool,
    ) -> NativePageInspection:
        _ = include_cells
        inspected_pages.append(page)
        controls = (target_control,) if page == 12 else ()
        return NativePageInspection(
            17,
            "C:/documents/sample.hwp",
            page,
            12,
            "",
            controls,
        )

    monkeypatch.setattr(target_module, "inspect_native_page", inspect)
    request = target_module.NativeTableTargetRequest(
        cast(
            HwpDocumentCandidate,
            cast(object, SimpleNamespace(window_handle=41)),
        ),
        routing,
        None,
        "",
        "",
    )

    resolved = target_module._resolved_table(
        request,
        "table-1",
        "target.control_instance_id",
        1,
    )

    assert resolved.page == 12
    assert resolved.rows == 3
    assert resolved.columns == 6
    assert inspected_pages[-1] == 12


def test_structural_postflight_undoes_a_split_that_corrupts_the_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid merged cell, an invalid split result, and its restored state.
    before = _detail((_cell("A1", 101, column_span=2),), columns=2)
    corrupted = _detail(
        (_cell("A1", 101), _cell("B1", 102), _cell("C1", 103)),
        columns=3,
    )
    inspections = iter((corrupted, before))
    history_calls: list[tuple[int, str, int]] = []

    def inspect(_window_handle: int, _page: int) -> NativeDetailedInspection:
        return next(inspections)

    def execute_history(
        window_handle: int,
        direction: str,
        steps: int,
    ) -> NativeHistoryResult:
        history_calls.append((window_handle, direction, steps))
        return NativeHistoryResult("undo", 1, 7)

    monkeypatch.setattr(recipe, "inspect_native_structure", inspect)
    monkeypatch.setattr(recipe, "execute_native_history", execute_history, raising=False)

    # When: postflight detects the unexpected physical column growth.
    with pytest.raises(HwpLiveError, match="자동 Undo.*복구"):
        recipe._verify_structural_plan(_prepared_split(), 41, before, 1)

    # Then: exactly one native Undo restores the pre-edit topology.
    assert history_calls == [(41, "undo", 1)]

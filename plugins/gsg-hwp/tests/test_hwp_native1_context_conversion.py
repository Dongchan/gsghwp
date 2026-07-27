from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_api import ShapeValue  # noqa: E402
from hwp_live_contract import OpenDocument  # noqa: E402
from hwp_live_inspection import inspect_native_context  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeCharacterFormat,
    NativePageControl,
    NativePageInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
)
from hwp_live_native_format_target import (  # noqa: E402
    NativeTableTargetRequest,
    ResolvedTable,
    resolve_native_table_target,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_operation_contract import HwpOperateTarget  # noqa: E402


def _document() -> OpenDocument:
    return OpenDocument(
        selector="active",
        title="test.hwp",
        full_name="C:/test.hwp",
        document_id=7,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=8,
        active=True,
        window_handle=100,
    )


def _snapshot(
    *,
    mode: int,
    selected: bool = True,
    start: NativePosition | None = None,
    end: NativePosition | None = None,
    control_type: str = "tbl",
    control_instance_id: str = "table-selected",
    cell_address: str = "B2",
    cell_addresses: tuple[str, ...] = (),
    cell_address_error: str = "",
) -> NativeSnapshot:
    resolved_start = NativePosition(101, 0, 0) if start is None else start
    resolved_end = NativePosition(104, 0, 0) if end is None else end
    return NativeSnapshot(
        document_id=7,
        full_name="C:/test.hwp",
        current_page=4,
        page_count=8,
        modified=False,
        cursor=NativePosition(104, 0, 0),
        selection=NativeSelection(
            selected=selected,
            start=resolved_start,
            end=resolved_end,
            mode=mode,
            cell_addresses=cell_addresses,
            cell_address_error=cell_address_error,
        ),
        selected_text="selected text" if selected else "",
        control_type=control_type,
        control_instance_id=control_instance_id,
        cell_address=cell_address,
        style_id=0,
        character_format=NativeCharacterFormat("함초롬바탕", 1000, False, 0),
        paragraph_format=NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )


_PAGE_SETUP: dict[str, ShapeValue] = {
    "PaperWidth": 210,
    "PaperHeight": 297,
    "Landscape": 0,
    "TopMargin": 20,
    "BottomMargin": 15,
    "LeftMargin": 20,
    "RightMargin": 20,
}


def test_native_context_matches_the_previous_selected_cells_output() -> None:
    context = inspect_native_context(
        _snapshot(mode=19),
        _document(),
        "page text",
        _PAGE_SETUP,
    )

    assert context.current_page == 4
    assert context.cursor.model_dump() == {
        "list_id": 104,
        "paragraph": 0,
        "character": 0,
    }
    assert context.selection.model_dump() == {
        "selected": True,
        "start_list": 101,
        "start_paragraph": 0,
        "start_character": 0,
        "end_list": 104,
        "end_paragraph": 0,
        "end_character": 0,
    }
    assert context.active_target.model_dump() == {
        "basis": "current_or_last_hwp_position",
        "kind": "selected_cells",
        "selection_mode_raw": 19,
        "selection_mode": "cells",
        "strict_selection": True,
        "multiple_cells": True,
        "control_type": "tbl",
        "control_instance_id": "table-selected",
        "cell_address": "B2",
        "cell_addresses": (),
        "cell_address_error": None,
    }
    assert context.selected_text == "selected text"
    assert context.page_text == "page text"
    assert context.character_style.face_name == "함초롬바탕"
    assert context.paragraph_style.line_spacing == 160
    assert context.page_setup.paper_width_mm == 210


def test_native_context_preserves_cellsel_optional_output_and_control_id() -> None:
    context = inspect_native_context(
        _snapshot(
            mode=19,
            control_instance_id="256-cache-stable-id",
            cell_addresses=("A1", "B1", "A2", "B2"),
            cell_address_error="",
        ),
        _document(),
        "page text",
        _PAGE_SETUP,
    )

    assert context.active_target.control_instance_id == "256-cache-stable-id"
    assert context.active_target.cell_addresses == ("A1", "B1", "A2", "B2")
    assert context.active_target.cell_address_error is None


def test_native_context_keeps_control_selection_out_of_cell_context() -> None:
    context = inspect_native_context(
        _snapshot(mode=4, cell_address="B2"),
        _document(),
        "page text",
        _PAGE_SETUP,
    )

    assert context.active_target.kind == "selected_table"
    assert context.active_target.selection_mode == "control"
    assert context.active_target.cell_address is None


def test_cached_control_id_resolves_against_a_fresh_native_page_after_reattach() -> (
    None
):
    context = inspect_native_context(
        _snapshot(mode=4, control_instance_id="256-cache-stable-id"),
        _document(),
        "page text",
        _PAGE_SETUP,
    )
    cached_id = context.active_target.control_instance_id
    assert cached_id == "256-cache-stable-id"

    fresh_page = NativePageInspection(
        document_id=7,
        full_name="C:/test.hwp",
        page=4,
        page_count=8,
        text="reattached page",
        controls=(
            NativePageControl(
                control_type="tbl",
                instance_id=cached_id,
                anchor=NativePosition(104, 0, 0),
                rows=2,
                columns=2,
            ),
        ),
    )
    resolved = resolve_native_table_target(
        NativeTableTargetRequest(
            candidate=cast(
                HwpDocumentCandidate,
                cast(object, SimpleNamespace(window_handle=100)),
            ),
            routing_page=fresh_page,
            target=HwpOperateTarget(
                kind="table",
                control_instance_id=cached_id,
            ),
            snapshot_control_type="",
            snapshot_control_id="",
        )
    )

    assert isinstance(resolved, ResolvedTable)
    assert resolved.instance_id == cached_id
    assert resolved.basis == "target.control_instance_id"
    assert (resolved.rows, resolved.columns) == (2, 2)

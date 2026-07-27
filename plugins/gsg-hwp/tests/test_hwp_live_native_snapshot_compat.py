from __future__ import annotations

import sys
from base64 import b64encode
from pathlib import Path


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
from hwp_live_native_action_contract import (  # noqa: E402
    decode_snapshot,
    decoded_logical_cell_selection,
)


def _encoded(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii")


def _document() -> OpenDocument:
    return OpenDocument(
        selector="active",
        title="test.hwp",
        full_name="C:/test.hwp",
        document_id=7,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=27,
        active=True,
        window_handle=100,
    )


def _page_setup() -> dict[str, ShapeValue]:
    return {
        "PaperWidth": 210,
        "PaperHeight": 297,
        "Landscape": 0,
        "TopMargin": 20,
        "BottomMargin": 15,
        "LeftMargin": 20,
        "RightMargin": 20,
    }


def test_decode_snapshot_accepts_legacy_selection_record_without_mode() -> None:
    # Given: the protocol-9 snapshot emitted by the currently loaded native bridge.
    payload = "\n".join(
        (
            "HCS1",
            f"DOC\t7\t{_encoded('C:/test.hwp')}",
            "STATE\t8\t27\t0",
            "CURSOR\t317\t0\t0",
            "SELECTION\t0\t0\t0\t0\t0\t0\t0",
            "TEXT\t",
            f"CONTEXT\t{_encoded('tbl')}\t{_encoded('2004510018')}\t{_encoded('A1')}",
            "STYLE\t0",
            f"CHAR\t{_encoded('맑은 고딕')}\t1100\t0\t0",
            "PARA\t3\t200\t0\t0\t0\t0\t0",
            "END",
        )
    )

    # When: Python decodes the snapshot before inserting at the live caret.
    snapshot = decode_snapshot(payload)
    context = inspect_native_context(
        snapshot,
        _document(),
        "",
        _page_setup(),
    )

    # Then: the legacy record remains a collapsed A1 cell caret with no raw mode.
    assert snapshot.cursor.list_id == 317
    assert snapshot.selection.selected is False
    assert snapshot.selection.mode == 0
    assert snapshot.control_type == "tbl"
    assert snapshot.control_instance_id == "2004510018"
    assert snapshot.cell_address == "A1"
    assert decoded_logical_cell_selection(snapshot.selection) is None
    assert context.active_target.kind == "table_cell"
    assert context.active_target.cell_address == "A1"
    assert context.active_target.cell_addresses == ()


def test_old_native_caret_outside_a_table_remains_a_caret() -> None:
    payload = "\n".join(
        (
            "HCS1",
            f"DOC\t7\t{_encoded('C:/test.hwp')}",
            "STATE\t8\t27\t0",
            "CURSOR\t317\t0\t0",
            "SELECTION\t0\t0\t0\t0\t0\t0\t0",
            "TEXT\t",
            "CONTEXT\t\t\t",
            "STYLE\t0",
            f"CHAR\t{_encoded('맑은 고딕')}\t1100\t0\t0",
            "PARA\t3\t200\t0\t0\t0\t0\t0",
            "END",
        )
    )

    snapshot = decode_snapshot(payload)
    context = inspect_native_context(
        snapshot,
        _document(),
        "",
        _page_setup(),
    )

    assert decoded_logical_cell_selection(snapshot.selection) is None
    assert context.active_target.kind == "caret"
    assert context.active_target.cell_address is None
    assert context.active_target.cell_addresses == ()


def test_decode_snapshot_accepts_strict_cell_addresses_without_text_positions() -> None:
    # Given: HWP exposes a strict cell block through TableFormula, but not GetSelectedPos.
    payload = "\n".join(
        (
            "HCS1",
            f"DOC\t7\t{_encoded('C:/test.hwp')}",
            "STATE\t8\t27\t0",
            "CURSOR\t479\t0\t0",
            f"SELECTION\t0\t19\t0\t0\t0\t0\t0\t0\t{_encoded('B2,C2,B3,C3')}",
            "TEXT\t",
            f"CONTEXT\t{_encoded('tbl')}\t{_encoded('2004510018')}\t{_encoded('C3')}",
            "STYLE\t0",
            f"CHAR\t{_encoded('맑은 고딕')}\t1100\t0\t0",
            "PARA\t3\t200\t0\t0\t0\t0\t0",
            "END",
        )
    )

    # When: Python decodes the current native snapshot.
    snapshot = decode_snapshot(payload)

    # Then: the exact physical cell owners survive even though text positions are zero.
    assert snapshot.selection.selected is False
    assert snapshot.selection.mode == 19
    assert snapshot.selection.cell_addresses == ("B2", "C2", "B3", "C3")


def test_new_native_keeps_unmerged_selection_behavior() -> None:
    addresses = "B2,C2,B3,C3"
    payload = "\n".join(
        (
            "HCS1",
            f"DOC\t7\t{_encoded('C:/test.hwp')}",
            "STATE\t8\t27\t0",
            "CURSOR\t479\t0\t0",
            f"SELECTION\t0\t19\t0\t0\t0\t0\t0\t0\t{_encoded(addresses)}\t\t{_encoded(addresses)}\t",
            "TEXT\t",
            f"CONTEXT\t{_encoded('tbl')}\t{_encoded('2004510018')}\t{_encoded('C3')}",
            "STYLE\t0",
            f"CHAR\t{_encoded('맑은 고딕')}\t1100\t0\t0",
            "PARA\t3\t200\t0\t0\t0\t0\t0",
            "END",
        )
    )

    snapshot = decode_snapshot(payload)
    context = inspect_native_context(
        snapshot,
        _document(),
        "",
        _page_setup(),
    )

    expected = ("B2", "C2", "B3", "C3")
    assert snapshot.selection.cell_addresses == expected
    assert context.active_target.cell_addresses == expected


def test_decode_snapshot_accepts_12_field_logical_selection() -> None:
    # Given: native omits the optional logical-error field after successful expansion.
    payload = "\n".join(
        (
            "HCS1",
            f"DOC\t7\t{_encoded('C:/test.hwp')}",
            "STATE\t8\t27\t0",
            "CURSOR\t479\t0\t0",
            f"SELECTION\t0\t19\t0\t0\t0\t0\t0\t0\t{_encoded('C1,C3,C4')}\t\t{_encoded('C1,C2,C3,C4')}",
            "TEXT\t",
            f"CONTEXT\t{_encoded('tbl')}\t{_encoded('2004510018')}\t{_encoded('C4')}",
            "STYLE\t0",
            f"CHAR\t{_encoded('맑은 고딕')}\t1100\t0\t0",
            "PARA\t3\t200\t0\t0\t0\t0\t0",
            "END",
        )
    )

    snapshot = decode_snapshot(payload)
    context = inspect_native_context(
        snapshot,
        _document(),
        "",
        _page_setup(),
    )

    assert snapshot.selection.cell_addresses == ("C1", "C3", "C4")
    assert decoded_logical_cell_selection(snapshot.selection) == (
        ("C1", "C2", "C3", "C4"),
        "",
    )
    assert snapshot.cell_address == "C4"
    assert context.active_target.cell_addresses == ("C1", "C2", "C3", "C4")
    assert context.active_target.cell_address == "C4"


def test_new_native_logical_failure_does_not_replace_physical_owner_state() -> None:
    logical_error = "selected cell logical topology expansion failed"
    payload = "\n".join(
        (
            "HCS1",
            f"DOC\t7\t{_encoded('C:/test.hwp')}",
            "STATE\t8\t27\t0",
            "CURSOR\t479\t0\t0",
            f"SELECTION\t0\t19\t0\t0\t0\t0\t0\t0\t{_encoded('C1,C3,C4')}\t\t\t{_encoded(logical_error)}",
            "TEXT\t",
            f"CONTEXT\t{_encoded('tbl')}\t{_encoded('2004510018')}\t{_encoded('C4')}",
            "STYLE\t0",
            f"CHAR\t{_encoded('맑은 고딕')}\t1100\t0\t0",
            "PARA\t3\t200\t0\t0\t0\t0\t0",
            "END",
        )
    )
    snapshot = decode_snapshot(payload)
    context = inspect_native_context(
        snapshot,
        _document(),
        "",
        _page_setup(),
    )

    assert snapshot.selection.cell_addresses == ("C1", "C3", "C4")
    assert snapshot.selection.cell_address_error == ""
    assert context.active_target.cell_addresses == ()
    assert context.active_target.cell_address_error == logical_error


def test_native_snapshot_expands_only_plural_selection_addresses_from_topology() -> (
    None
):
    source = (
        Path(__file__).resolve().parents[1]
        / "addon"
        / "HancomLiveBridgeNative"
        / "LiveInspection.cpp"
    ).read_text(encoding="utf-8")
    compact = "".join(source.split())

    assert "boolExpandLogicalSelectionAddresses(" in compact
    assert "std::wstring*consterror)noexcept{try{" in compact
    assert "topology.Find(address)" in compact
    assert "rowOffset<cell->rowSpan" in compact
    assert "columnOffset<cell->columnSpan" in compact
    assert "if(!hasMergedCell){*logical=physical;returntrue;}" in compact
    assert "if(!CanRestoreSelection(physicalSelection))" in compact
    assert (
        "constboollogicalSelectionAttempted=!selection.cellAddresses.empty();"
        in compact
    )
    assert "if(logicalSelectionAttempted){output<<" in compact
    assert (
        "constboolexpanded=inspected&&ExpandLogicalSelectionAddresses("
        "topology,physicalSelection.cellAddresses,&logicalCellAddresses,"
        "&logicalAddressError);"
    ) in compact
    assert (
        "catch(...){try{if(logical!=nullptr){logical->clear();}}catch(...){}"
        "try{if(error!=nullptr){*error="
        'L"selectedlogicalcellexpansionfailedunexpectedly";}}catch(...){}'
        "returnfalse;}"
    ) in compact
    expansion_call = compact.index(
        "constboolexpanded=inspected&&ExpandLogicalSelectionAddresses("
    )
    restore_call = compact.index(
        "if(!RestoreSelection(hwp,cursor,physicalSelection)"
    )
    assert expansion_call < restore_call
    assert "selection.cellAddresses=std::move(logicalCellAddresses);" not in compact
    assert "EncodeUtf8Base64(selectedCells.str())" in compact
    assert "EncodeUtf8Base64(selection.cellAddressError)" in compact
    assert "EncodeUtf8Base64(logicalSelectedCells.str())" in compact
    assert "EncodeUtf8Base64(logicalAddressError)" in compact
    assert "conststd::wstringcell=CellAddress(hwp);" in compact


def test_decode_snapshot_preserves_strict_cell_address_inspection_error() -> None:
    # Given: native HWP inspection identifies a strict block but cannot read its addresses.
    error = "TableFormula Command property could not be read"
    payload = "\n".join(
        (
            "HCS1",
            f"DOC\t7\t{_encoded('C:/test.hwp')}",
            "STATE\t8\t27\t0",
            "CURSOR\t479\t0\t0",
            f"SELECTION\t0\t19\t0\t0\t0\t0\t0\t0\t\t{_encoded(error)}",
            "TEXT\t",
            f"CONTEXT\t{_encoded('tbl')}\t{_encoded('2004510018')}\t{_encoded('C3')}",
            "STYLE\t0",
            f"CHAR\t{_encoded('맑은 고딕')}\t1100\t0\t0",
            "PARA\t3\t200\t0\t0\t0\t0\t0",
            "END",
        )
    )

    # When: Python decodes the native diagnostic record.
    snapshot = decode_snapshot(payload)

    # Then: the exact native stage remains available to selection consumers.
    assert snapshot.selection.cell_addresses == ()
    assert snapshot.selection.cell_address_error == error

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

from hwp_live_native_action_contract import decode_snapshot  # noqa: E402


def _encoded(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii")


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

    # Then: the legacy record remains a collapsed A1 cell caret with no raw mode.
    assert snapshot.cursor.list_id == 317
    assert snapshot.selection.selected is False
    assert snapshot.selection.mode == 0
    assert snapshot.control_type == "tbl"
    assert snapshot.control_instance_id == "2004510018"
    assert snapshot.cell_address == "A1"


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

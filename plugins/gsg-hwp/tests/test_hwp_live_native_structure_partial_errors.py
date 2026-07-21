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

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeControlInspectionError,
    NativeDetailedCell,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativePosition,
)
from hwp_live_native_structure import document_structure_from_native  # noqa: E402


def _failed_table() -> NativeDetailedControl:
    return NativeDetailedControl(
        control_type="tbl",
        instance_id="failed-table",
        user_description="",
        anchor=NativePosition(0, 0, 0),
        page_start=9,
        page_end=9,
        top_level=True,
        rows=None,
        columns=None,
    )


def _valid_table() -> NativeDetailedControl:
    return NativeDetailedControl(
        control_type="tbl",
        instance_id="valid-table",
        user_description="",
        anchor=NativePosition(0, 1, 0),
        page_start=9,
        page_end=9,
        top_level=True,
        rows=1,
        columns=1,
    )


def _inspection(*controls: NativeDetailedControl) -> NativeDetailedInspection:
    cells = (
        NativeDetailedCell(
            table_instance_id="valid-table",
            address="A1",
            list_id=10,
            row_span=1,
            column_span=1,
            page_start=9,
            page_end=9,
            text="조망위치",
        ),
    ) if any(control.instance_id == "valid-table" for control in controls) else ()
    return NativeDetailedInspection(
        document_id=1,
        full_name=r"C:\test\sample.hwp",
        page=9,
        page_count=10,
        text="",
        controls=controls,
        cells=cells,
        captions=(),
        inspection_errors=(
            NativeControlInspectionError(
                control_instance_id="failed-table",
                code="TABLE_INSPECTION",
                message="table cell geometry is incomplete",
            ),
        ),
    )


def test_keeps_valid_table_when_sibling_table_inspection_fails() -> None:
    structure = document_structure_from_native(
        _inspection(_failed_table(), _valid_table()),
        selector="sample",
        window_handle=1,
    )

    assert tuple(table.control_instance_id for table in structure.tables) == (
        "valid-table",
    )


def test_raises_when_every_relevant_table_inspection_fails() -> None:
    with pytest.raises(HwpLiveError, match="TABLE_INSPECTION"):
        _ = document_structure_from_native(
            _inspection(_failed_table()),
            selector="sample",
            window_handle=1,
        )

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_api import (  # noqa: E402
    HwpComApplication,
    HwpComDocument,
    LiveHwpApplication,
)
from hwp_live_native_action_models import (  # noqa: E402
    NativeDetailedCell,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativePosition,
)
from hwp_live_native_structure import document_structure_from_native  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session_structure_inspection import (  # noqa: E402
    inspect_candidate_structure,
)


def _candidate() -> HwpDocumentCandidate:
    return HwpDocumentCandidate(
        selector="scope-fixture",
        moniker_name="fixture",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, object()),
        document_id=17,
        full_name="C:/documents/scope.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )


def _native(page: int) -> NativeDetailedInspection:
    return NativeDetailedInspection(
        document_id=17,
        full_name="C:/documents/scope.hwp",
        page=page,
        page_count=31,
        text="",
        controls=(),
        cells=(),
        captions=(),
    )


def test_explicit_page_uses_scoped_native_request() -> None:
    # Given
    candidate = _candidate()

    # When
    with (
        patch("hwp_live_session_structure_inspection.read_native_snapshot") as snapshot,
        patch(
            "hwp_live_session_structure_inspection.inspect_native_structure",
            return_value=_native(31),
        ) as inspect_native,
    ):
        structure = inspect_candidate_structure(
            cast(LiveHwpApplication, object()),
            candidate,
            31,
            lambda: None,
        )

    # Then
    snapshot.assert_not_called()
    inspect_native.assert_called_once_with(100, 31)
    assert structure.page == 31


def test_omitted_page_preserves_unscoped_native_request() -> None:
    # Given
    candidate = _candidate()

    # When
    with (
        patch(
            "hwp_live_session_structure_inspection.read_native_snapshot",
            return_value=SimpleNamespace(current_page=31),
        ),
        patch(
            "hwp_live_session_structure_inspection.inspect_native_structure",
            return_value=_native(31),
        ) as inspect_native,
    ):
        structure = inspect_candidate_structure(
            cast(LiveHwpApplication, object()),
            candidate,
            0,
            lambda: None,
        )

    # Then
    inspect_native.assert_called_once_with(100, -31)
    assert structure.page == 31


def test_table_crossing_requested_page_boundary_is_preserved() -> None:
    # Given
    table = NativeDetailedControl(
        control_type="tbl",
        instance_id="spanning-table",
        user_description="표",
        anchor=NativePosition(0, 10, 0),
        page_start=1,
        page_end=2,
        top_level=True,
        rows=1,
        columns=1,
    )
    nested_picture = NativeDetailedControl(
        control_type="gso",
        instance_id="nested-picture",
        user_description="그림",
        anchor=NativePosition(10, 0, 0),
        page_start=2,
        page_end=2,
        top_level=False,
        rows=None,
        columns=None,
    )
    cell = NativeDetailedCell(
        table_instance_id="spanning-table",
        address="A1",
        list_id=10,
        row_span=1,
        column_span=1,
        page_start=1,
        page_end=2,
        text="두 쪽에 걸친 셀",
    )
    native = NativeDetailedInspection(
        document_id=17,
        full_name="C:/documents/scope.hwp",
        page=2,
        page_count=2,
        text="",
        controls=(table, nested_picture),
        cells=(cell,),
        captions=(),
    )

    # When
    structure = document_structure_from_native(
        native,
        selector="scope-fixture",
        window_handle=100,
    )

    # Then
    assert len(structure.tables) == 1
    assert structure.tables[0].page_start == 1
    assert structure.tables[0].page_end == 2
    assert structure.tables[0].cells[0].text == "두 쪽에 걸친 셀"
    assert structure.tables[0].cells[0].has_picture is True

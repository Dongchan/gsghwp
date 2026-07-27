from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from base64 import b64encode
from pathlib import Path
from typing import ClassVar, cast, override
from unittest.mock import patch

import pytest


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
)
from hwp_live_contract import LiveContext, OpenDocument  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_inspection import (  # noqa: E402
    inspect_native_context,
    with_selected_cell_addresses,
)
from hwp_live_native_action_contract import decode_snapshot  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    CellCommand,
    NativeCharacterFormat,
    NativeDetailedCell,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
)
from hwp_live_native_format_commands import (  # noqa: E402
    TableFormatCommandPlan,
    build_native_format_commands,
)
from hwp_live_native_format_inputs import InputFailure, parse_table_format  # noqa: E402
from hwp_live_native_format_contract import PreparedFormatOperation  # noqa: E402
from hwp_live_native_format_recipe import _resolve_selected_table_cells  # noqa: E402
from hwp_live_native_format_target import ResolvedTable  # noqa: E402
from hwp_live_native_table_topology import table_topology  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session_table_fill import _bind_live_table_selection  # noqa: E402
from hwp_operation_contract import HwpOperateData, HwpOperateTarget  # noqa: E402
from hwp_public_table_edit_contract import PublicTableFormattingInput  # noqa: E402
from hwp_public_table_target import PublicTableDataInput  # noqa: E402


class _Control:
    CtrlID: ClassVar[str] = "tbl"

    def GetCtrlInstID(self) -> str:
        return "table-selected"


class _FormulaField:
    HSet: ClassVar[object] = object()
    Command: ClassVar[str] = "A1,B1,A2,B2"


class _FormulaParameterSets:
    HFieldCtrl: ClassVar[_FormulaField] = _FormulaField()


class _FormulaAction:
    calls: list[tuple[str, object]]

    def __init__(self) -> None:
        self.calls = []

    def GetDefault(self, action: str, parameters: object) -> bool:
        self.calls.append((action, parameters))
        return True


class _FormulaApplication:
    HParameterSet: ClassVar[_FormulaParameterSets] = _FormulaParameterSets()
    SelectionMode: ClassVar[int] = 19

    def __init__(self) -> None:
        self.HAction = _FormulaAction()


class _SelectedCellsHwp:
    SelectionMode: ClassVar[int] = 19
    IsModified: ClassVar[bool] = False
    current_page: ClassVar[int] = 4
    ParentCtrl: ClassVar[_Control] = _Control()

    def get_pos(self) -> tuple[int, int, int]:
        return 104, 0, 0

    def get_selected_pos(
        self,
    ) -> tuple[bool, int, int, int, int, int, int]:
        return True, 101, 0, 0, 104, 0, 0

    def get_text_file(self, *, format: str, option: str) -> str:
        assert (format, option) == ("UNICODE", "saveblock:true")
        return ""

    def get_charshape_as_dict(self) -> dict[str, int | str]:
        return {
            "FaceNameHangul": "함초롬바탕",
            "Height": 1000,
            "Bold": 0,
            "TextColor": 0,
        }

    def get_parashape_as_dict(self) -> dict[str, int]:
        return {
            "AlignType": 0,
            "LineSpacing": 160,
            "LeftMargin": 0,
            "RightMargin": 0,
            "Indentation": 0,
            "PrevSpacing": 0,
            "NextSpacing": 0,
        }

    def get_pagedef_as_dict(self, option: str) -> dict[str, int]:
        assert option == "eng"
        return {
            "PaperWidth": 210,
            "PaperHeight": 297,
            "Landscape": 0,
            "TopMargin": 20,
            "BottomMargin": 15,
            "LeftMargin": 20,
            "RightMargin": 20,
        }

    def get_page_text(self, page: int) -> str:
        assert page == 3
        return "page text"

    def is_cell(self) -> bool:
        return True

    def get_cell_addr(self) -> str:
        return "B2"


class _SelectedSingleCellHwp(_SelectedCellsHwp):
    SelectionMode: ClassVar[int] = 3

    @override
    def get_selected_pos(
        self,
    ) -> tuple[bool, int, int, int, int, int, int]:
        return True, 104, 0, 0, 104, 0, 0


class _StrictSelectedCellsWithoutTextRangeHwp(_SelectedCellsHwp):
    @override
    def get_selected_pos(
        self,
    ) -> tuple[bool, int, int, int, int, int, int]:
        return False, 0, 0, 0, 0, 0, 0


class _SelectedTableHwp(_SelectedCellsHwp):
    SelectionMode: ClassVar[int] = 4
    CurSelectedCtrl: ClassVar[_Control] = _Control()

    @override
    def is_cell(self) -> bool:
        raise AssertionError("selected controls must not be queried as table cells")


def _inspect_context(
    hwp: _SelectedCellsHwp,
    document: OpenDocument,
) -> LiveContext:
    raw = hwp.get_selected_pos()
    control = (
        hwp.CurSelectedCtrl
        if (hwp.SelectionMode & 0x0F) == 4
        else hwp.ParentCtrl
    )
    snapshot = NativeSnapshot(
        document_id=document.document_id,
        full_name=document.full_name,
        current_page=hwp.current_page,
        page_count=document.page_count,
        modified=hwp.IsModified,
        cursor=NativePosition(*hwp.get_pos()),
        selection=NativeSelection(
            selected=raw[0],
            start=NativePosition(*(value or 0 for value in raw[1:4])),
            end=NativePosition(*(value or 0 for value in raw[4:7])),
            mode=hwp.SelectionMode,
        ),
        selected_text=(
            hwp.get_text_file(format="UNICODE", option="saveblock:true")
            if raw[0]
            else ""
        ),
        control_type=control.CtrlID,
        control_instance_id=control.GetCtrlInstID(),
        cell_address="B2",
        style_id=0,
        character_format=NativeCharacterFormat("함초롬바탕", 1000, False, 0),
        paragraph_format=NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )
    return inspect_native_context(
        snapshot,
        document,
        hwp.get_page_text(hwp.current_page - 1),
        hwp.get_pagedef_as_dict("eng"),
    )


def _encoded(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii")


def test_native_snapshot_preserves_the_raw_hwp_selection_mode() -> None:
    payload = "\n".join(
        (
            "HCS1",
            f"DOC\t7\t{_encoded('C:/test.hwp')}",
            "STATE\t4\t8\t0",
            "CURSOR\t104\t0\t0",
            "SELECTION\t1\t19\t101\t0\t0\t104\t0\t0",
            "TEXT\t",
            f"CONTEXT\t{_encoded('tbl')}\t{_encoded('table-selected')}\t{_encoded('B2')}",
            "STYLE\t0",
            f"CHAR\t{_encoded('함초롬바탕')}\t1000\t0\t0",
            "PARA\t0\t160\t0\t0\t0\t0\t0",
            "END",
        )
    )

    snapshot = decode_snapshot(payload)

    assert snapshot.selection.mode == 19
    assert snapshot.selection.base_mode == 3
    assert snapshot.selection.strict is True


def test_hwp_inspect_reports_selected_cells_as_the_active_last_hwp_target() -> None:
    document = OpenDocument(
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

    context = _inspect_context(_SelectedCellsHwp(), document)

    assert context.active_target.basis == "current_or_last_hwp_position"
    assert context.active_target.kind == "selected_cells"
    assert context.active_target.selection_mode_raw == 19
    assert context.active_target.strict_selection is True
    assert context.active_target.multiple_cells is True
    assert context.active_target.control_type == "tbl"
    assert context.active_target.control_instance_id == "table-selected"
    assert context.active_target.cell_address == "B2"


def test_hwp_inspect_can_expose_native_selected_cell_addresses() -> None:
    document = OpenDocument(
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
    context = _inspect_context(_SelectedCellsHwp(), document)

    enriched = with_selected_cell_addresses(
        context,
        ("A1", "B1", "A2", "B2"),
    )

    assert enriched.active_target.cell_addresses == ("A1", "B1", "A2", "B2")
    assert enriched.active_target.cell_address_error is None


def test_hwp_inspect_distinguishes_one_selected_cell_from_multiple_cells() -> None:
    document = OpenDocument(
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

    context = _inspect_context(_SelectedSingleCellHwp(), document)

    assert context.active_target.kind == "selected_cells"
    assert context.active_target.multiple_cells is False
    assert context.active_target.cell_address == "B2"


def test_hwp_inspect_recognizes_strict_cells_without_a_text_selection_range() -> None:
    # Given: HWP reports a strict cell block but no ordinary text-selection range.
    document = OpenDocument(
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
    # When: the active HWP target is inspected.
    context = _inspect_context(_StrictSelectedCellsWithoutTextRangeHwp(), document)

    # Then: selection mode 19 still identifies a multi-cell block.
    assert context.active_target.selection_mode_raw == 19
    assert context.active_target.strict_selection is True
    assert context.active_target.multiple_cells is True


def test_hwp_inspect_reports_a_selected_table_without_probing_cell_state() -> None:
    document = OpenDocument(
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

    context = _inspect_context(_SelectedTableHwp(), document)

    assert context.active_target.kind == "selected_table"
    assert context.active_target.selection_mode == "control"
    assert context.active_target.control_type == "tbl"
    assert context.active_target.control_instance_id == "table-selected"
    assert context.active_target.cell_address is None


def _cell(address: str, list_id: int) -> NativeDetailedCell:
    return NativeDetailedCell(
        "table-selected",
        address,
        list_id,
        1,
        1,
        4,
        4,
        "",
        1000,
        500,
    )


def test_cell_topology_resolves_a_reversed_selection_from_list_ids() -> None:
    control = NativeDetailedControl(
        "tbl",
        "table-selected",
        "",
        NativePosition(1, 0, 0),
        4,
        4,
        True,
        2,
        2,
        2000,
        1000,
    )
    detail = NativeDetailedInspection(
        7,
        "C:/test.hwp",
        4,
        8,
        "",
        (control,),
        (_cell("A1", 101), _cell("B1", 102), _cell("A2", 103), _cell("B2", 104)),
        (),
    )

    addresses = table_topology(detail, "table-selected").selection_region_by_list_ids(
        104,
        101,
    )

    assert addresses == ("A1", "B1", "A2", "B2")


def test_table_format_without_cell_can_apply_to_resolved_selected_cells() -> None:
    requested = PublicTableFormattingInput(cell=None, fill_color="#DDEEFF")
    parsed = parse_table_format(requested.to_parameters())
    assert not isinstance(parsed, InputFailure)
    assert parsed.cell is None

    commands = build_native_format_commands(
        TableFormatCommandPlan(
            parsed,
            ResolvedTable(
                "table-selected",
                "native.selection",
                4,
                2,
                2,
            ),
            ("A1", "B1", "A2", "B2"),
        )
    )

    assert tuple(
        command.address for command in commands if isinstance(command, CellCommand)
    ) == ("A1", "B1", "A2", "B2")


def _snapshot(
    selection_mode: int,
    *,
    selected: bool = True,
    start_list: int = 101,
    end_list: int = 104,
    cell_addresses: tuple[str, ...] = (),
    cell_address_error: str = "",
) -> NativeSnapshot:
    return NativeSnapshot(
        7,
        "C:/test.hwp",
        4,
        8,
        False,
        NativePosition(104, 0, 0),
        NativeSelection(
            selected,
            NativePosition(start_list, 0, 0),
            NativePosition(end_list, 0, 0),
            selection_mode,
            cell_addresses,
            cell_address_error,
        ),
        "",
        "tbl",
        "table-selected",
        "B2",
        0,
        NativeCharacterFormat("함초롬바탕", 1_000, False, 0),
        NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )


def _selected_table_detail() -> NativeDetailedInspection:
    return NativeDetailedInspection(
        7,
        "C:/test.hwp",
        4,
        8,
        "",
        (
            NativeDetailedControl(
                "tbl",
                "table-selected",
                "",
                NativePosition(1, 0, 0),
                4,
                4,
                True,
                2,
                2,
                2_000,
                1_000,
            ),
        ),
        (_cell("A1", 101), _cell("B1", 102), _cell("A2", 103), _cell("B2", 104)),
        (),
    )


def test_table_format_without_cell_expands_a_selected_table_to_all_cells() -> None:
    parsed = parse_table_format(
        PublicTableFormattingInput(cell=None, fill_color="#DDEEFF").to_parameters()
    )
    assert not isinstance(parsed, InputFailure)
    prepared = PreparedFormatOperation(
        TableFormatCommandPlan(
            parsed,
            ResolvedTable("table-selected", "native.selection", 4, 2, 2),
        ),
        "table-selected",
        "native.selection",
        (),
    )

    resolved = _resolve_selected_table_cells(
        prepared,
        _snapshot(4),
        _selected_table_detail(),
    )

    assert not isinstance(resolved, InputFailure)
    assert isinstance(resolved.plan, TableFormatCommandPlan)
    assert resolved.plan.cells == ("A1", "B1", "A2", "B2")


def test_table_format_uses_strict_cell_addresses_when_text_positions_are_zero() -> None:
    # Given: a real strict HWP cell block whose ordinary selection coordinates are unavailable.
    parsed = parse_table_format(
        PublicTableFormattingInput(cell=None, fill_color="#DDEEFF").to_parameters()
    )
    assert not isinstance(parsed, InputFailure)
    prepared = PreparedFormatOperation(
        TableFormatCommandPlan(
            parsed,
            ResolvedTable("table-selected", "native.selection", 4, 2, 2),
        ),
        "table-selected",
        "native.selection",
        (),
    )
    snapshot = _snapshot(
        19,
        selected=False,
        start_list=0,
        end_list=0,
        cell_addresses=("A1", "B1", "A2", "B2"),
    )

    # When: the selection is bound to a table-format request.
    resolved = _resolve_selected_table_cells(
        prepared,
        snapshot,
        _selected_table_detail(),
    )

    # Then: formatting targets the selected rectangle, not the active endpoint alone.
    assert not isinstance(resolved, InputFailure)
    assert isinstance(resolved.plan, TableFormatCommandPlan)
    assert resolved.plan.cells == ("A1", "B1", "A2", "B2")


def test_table_format_reports_strict_cell_address_inspection_failure() -> None:
    # Given: HWP reports a strict block, but native TableFormula inspection failed.
    parsed = parse_table_format(
        PublicTableFormattingInput(cell=None, fill_color="#DDEEFF").to_parameters()
    )
    assert not isinstance(parsed, InputFailure)
    prepared = PreparedFormatOperation(
        TableFormatCommandPlan(
            parsed,
            ResolvedTable("table-selected", "native.selection", 4, 2, 2),
        ),
        "table-selected",
        "native.selection",
        (),
    )
    snapshot = _snapshot(
        19,
        selected=False,
        start_list=0,
        end_list=0,
        cell_address_error="TableFormula Command property could not be read",
    )

    # When: formatting resolves the selected physical cells.
    resolved = _resolve_selected_table_cells(
        prepared,
        snapshot,
        _selected_table_detail(),
    )

    # Then: it reports the native inspection stage instead of inferring a bogus range.
    assert isinstance(resolved, InputFailure)
    assert resolved.status == "schema_conflict"
    assert "TableFormula Command property" in resolved.message


def test_table_format_falls_back_to_safe_table_formula_addresses() -> None:
    parsed = parse_table_format(
        PublicTableFormattingInput(cell=None, fill_color="#DDEEFF").to_parameters()
    )
    assert not isinstance(parsed, InputFailure)
    prepared = PreparedFormatOperation(
        TableFormatCommandPlan(
            parsed,
            ResolvedTable("table-selected", "native.selection", 4, 2, 2),
        ),
        "table-selected",
        "native.selection",
        (),
    )
    application = _FormulaApplication()

    resolved = _resolve_selected_table_cells(
        prepared,
        _snapshot(0, selected=False, start_list=0, end_list=0),
        _selected_table_detail(),
        cast(HwpComApplication, cast(object, application)),
    )

    assert not isinstance(resolved, InputFailure)
    assert isinstance(resolved.plan, TableFormatCommandPlan)
    assert resolved.plan.cells == ("A1", "B1", "A2", "B2")
    assert application.HAction.calls == [("TableFormula", _FormulaField.HSet)]


def test_table_rows_can_defer_the_start_cell_to_the_live_hwp_selection() -> None:
    requested = PublicTableDataInput(rows=(("첫째", "둘째"),))

    canonical = requested.to_canonical_data()

    assert canonical.rows == (("첫째", "둘째"),)
    assert canonical.start_cell is None


def test_table_rows_bind_to_the_selected_cell_block_and_preserve_its_bounds() -> None:
    candidate = HwpDocumentCandidate(
        selector="active",
        moniker_name="fixture",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, object()),
        document_id=7,
        full_name="C:/test.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )
    target = HwpOperateTarget(kind="table")
    data = HwpOperateData(rows=(("첫째", "둘째"), ("셋째", "넷째")))

    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=_snapshot(19),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_native_structure",
            return_value=_selected_table_detail(),
        ),
    ):
        bound_target, bound_data, selected = _bind_live_table_selection(
            candidate,
            target,
            data,
        )

    assert bound_target.control_instance_id == "table-selected"
    assert bound_target.page_hint == 4
    assert bound_data.start_cell == "A1"
    assert selected == frozenset(("A1", "B1", "A2", "B2"))


def test_table_rows_use_strict_cell_addresses_when_text_positions_are_zero() -> None:
    # Given: HWP reports a B2:C3-style block only through its physical cell addresses.
    candidate = HwpDocumentCandidate(
        selector="active",
        moniker_name="fixture",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, object()),
        document_id=7,
        full_name="C:/test.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )
    target = HwpOperateTarget(kind="table")
    data = HwpOperateData(rows=(("첫째", "둘째"), ("셋째", "넷째")))
    snapshot = _snapshot(
        19,
        selected=False,
        start_list=0,
        end_list=0,
        cell_addresses=("A1", "B1", "A2", "B2"),
    )

    # When: the live selection is bound to the fill matrix.
    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=snapshot,
        ),
        patch(
            "hwp_live_session_table_fill.inspect_native_structure",
            return_value=_selected_table_detail(),
        ),
    ):
        bound_target, bound_data, selected = _bind_live_table_selection(
            candidate,
            target,
            data,
        )

    # Then: the matrix starts at the block origin and keeps the exact selection bounds.
    assert bound_target.control_instance_id == "table-selected"
    assert bound_data.start_cell == "A1"
    assert selected == frozenset(("A1", "B1", "A2", "B2"))


def test_table_rows_report_strict_cell_address_inspection_failure() -> None:
    # Given: a strict HWP cell block has no safe physical-address snapshot.
    candidate = HwpDocumentCandidate(
        selector="active",
        moniker_name="fixture",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, object()),
        document_id=7,
        full_name="C:/test.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )
    snapshot = _snapshot(
        19,
        selected=False,
        start_list=0,
        end_list=0,
        cell_address_error="TableFormula Command property could not be read",
    )

    # When/Then: table fill stops with that exact native reason before guessing C4.
    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=snapshot,
        ),
        pytest.raises(HwpLiveError, match="TableFormula Command property"),
    ):
        _bind_live_table_selection(
            candidate,
            HwpOperateTarget(kind="table"),
            HwpOperateData(rows=(("첫째", "둘째"), ("셋째", "넷째"))),
        )


def test_table_rows_fall_back_to_safe_table_formula_addresses() -> None:
    application = _FormulaApplication()
    candidate = HwpDocumentCandidate(
        selector="active",
        moniker_name="fixture",
        application=cast(HwpComApplication, cast(object, application)),
        document=cast(HwpComDocument, object()),
        document_id=7,
        full_name="C:/test.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )

    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=_snapshot(0, selected=False, start_list=0, end_list=0),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_native_structure",
            return_value=_selected_table_detail(),
        ),
    ):
        bound_target, bound_data, selected = _bind_live_table_selection(
            candidate,
            HwpOperateTarget(kind="table"),
            HwpOperateData(rows=(("첫째", "둘째"), ("셋째", "넷째"))),
        )

    assert bound_target.control_instance_id == "table-selected"
    assert bound_data.start_cell == "A1"
    assert selected == frozenset(("A1", "B1", "A2", "B2"))
    assert application.HAction.calls == [("TableFormula", _FormulaField.HSet)]

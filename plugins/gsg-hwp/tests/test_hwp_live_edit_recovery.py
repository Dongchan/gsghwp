from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_document_style_profile import resolve_layout_style_profile  # noqa: E402
from hwp_live_contract import DocumentStyle, LayoutPlan  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    DeleteControlCommand,
    IntegerValue,
    NativePageControl,
    NativePosition,
    ParameterActionCommand,
    SaveDocumentFileCommand,
    TextValue,
)
from hwp_live_table_contract import (  # noqa: E402
    CellBorder,
    CellBorders,
    TableBlock,
    TableCell,
    TableMerge,
)


@dataclass(frozen=True, slots=True)
class _WindowCandidate:
    window_handle: int


from hwp_mcp_registry import search_tool_specs, tool_names  # noqa: E402
from hwp_operation_contract import HwpOperateInputs  # noqa: E402


def test_merged_title_and_picture_cells_inherit_conflicting_shared_borders() -> None:
    thin = CellBorder(width="0.12mm")
    thick = CellBorder(width="0.5mm")
    table = TableBlock(
        kind="table",
        rows=(
            (
                TableCell(
                    text="현황사진",
                    borders=CellBorders(left=thin, right=thin, top=thin, bottom=thick),
                ),
                TableCell(),
            ),
            (
                TableCell(
                    text="사진 1",
                    borders=CellBorders(left=thin, right=thin, top=thin, bottom=thin),
                ),
                TableCell(
                    text="사진 2",
                    borders=CellBorders(left=thin, right=thin, top=thin, bottom=thin),
                ),
            ),
        ),
        merges=(TableMerge(row=0, column=0, column_span=2),),
    )

    resolved = resolve_layout_style_profile(
        LayoutPlan(blocks=(table,)),
        (DocumentStyle(style_id=0, name="바탕글"),),
        fallback_style_id=0,
        content_width_mm=170.0,
    )

    styled = resolved.blocks[0]
    assert isinstance(styled, TableBlock)
    assert styled.rows[0][0].borders is not None
    assert styled.rows[0][0].borders.top == thin
    assert styled.rows[0][0].borders.bottom is None
    assert styled.rows[1][0].borders is not None
    assert styled.rows[1][0].borders.left == thin
    assert styled.rows[1][0].borders.top is None
    assert styled.rows[1][1].borders is not None
    assert styled.rows[1][1].borders.top is None


def test_production_mcp_exposes_live_delete_and_history_tools() -> None:
    expected = {
        "hwp_delete_page",
        "hwp_delete_control",
        "hwp_undo",
        "hwp_redo",
    }

    assert expected <= tool_names("production")
    assert HwpOperateInputs(operation="document.undo").operation == "document.undo"
    assert HwpOperateInputs(operation="document.redo").operation == "document.redo"
    assert HwpOperateInputs(operation="control.delete").operation == "control.delete"


def test_tool_search_routes_page_control_rollback_and_redo_without_catalog_detour() -> (
    None
):
    cases = (
        ("54쪽 빈 페이지 삭제", "hwp_delete_page"),
        ("잘못 만든 독립 표 개체 삭제", "hwp_delete_control"),
        ("방금 한컴 작업 실행 취소 롤백", "hwp_undo"),
        ("취소한 한컴 작업 다시 실행", "hwp_redo"),
    )

    for query, expected in cases:
        matches = search_tool_specs(query, profile="production", limit=3)
        assert matches[0].name == expected


def test_live_edit_command_plans_use_bounded_official_native_actions() -> None:
    from hwp_live_document_edit_commands import (  # noqa: PLC0415
        build_delete_control_commands,
        build_delete_page_commands,
    )
    from hwp_live_native_history import build_native_history_payload  # noqa: PLC0415

    page_commands = build_delete_page_commands(7)
    assert len(page_commands) == 1
    page_action = page_commands[0]
    assert isinstance(page_action, ParameterActionCommand)
    assert page_action.action == "DeletePage"
    assert page_action.parameter_set == "HDeletePage"
    setters = {setter.path: setter.value for setter in page_action.setters}
    assert setters == {
        "Range": IntegerValue(2),
        "RangeCustom": TextValue("7"),
        "UsingPagenum": IntegerValue(0),
    }

    assert build_native_history_payload("undo") == (
        "HCV1\nAUTOMATION\tIXHwpDocument\tUndo\tmethod\nARG\tI4\t1\nEND"
    )
    assert build_native_history_payload("redo") == (
        "HCV1\nAUTOMATION\tIXHwpDocument\tRedo\tmethod\nARG\tI4\t1\nEND"
    )
    table = NativePageControl("tbl", "table-1", NativePosition(0, 4, 2), 2, 2)
    picture = NativePageControl("gso", "picture-2", NativePosition(3, 1, 0), None, None)
    assert build_delete_control_commands((table, picture)) == (
        DeleteControlCommand("picture-2"),
        DeleteControlCommand("table-1"),
    )


def test_document_edit_history_is_operation_grouped_and_disk_bounded(
    tmp_path: Path,
) -> None:
    from hwp_live_edit_history import (  # noqa: PLC0415
        DocumentCheckpoint,
        DocumentEditHistoryEntry,
        LiveEditHistoryStore,
        PageControlSnapshot,
    )

    store = LiveEditHistoryStore(temp_parent=tmp_path, max_entries=2, max_bytes=32)
    before_path = store.new_checkpoint_path()
    after_path = store.new_checkpoint_path()
    before_path.write_bytes(b"BEFORE")
    after_path.write_bytes(b"AFTER")
    control = PageControlSnapshot(
        instance_id="table-old",
        control_type="tbl",
        rows=2,
        columns=2,
    )
    deleted = DocumentEditHistoryEntry(
        document_id=7,
        full_name=r"C:\GSG_HWP_QA\sample.hwp",
        operation="control.delete",
        before=DocumentCheckpoint(before_path, 6, 4),
        after=DocumentCheckpoint(after_path, 5, 4),
        page=4,
        before_controls=(control,),
        after_controls=(),
    )
    store.record(deleted)

    assert store.available("undo", 7, r"c:\gsg_hwp_qa\SAMPLE.hwp") == 1
    assert store.peek("undo", 7, r"C:\GSG_HWP_QA\sample.hwp") == deleted
    store.commit("undo", deleted)
    assert store.available("undo", 7, deleted.full_name) == 0
    assert store.peek("redo", 7, deleted.full_name) == deleted

    store.commit("redo", deleted)
    assert store.peek("undo", 7, deleted.full_name) == deleted
    history_root = before_path.parent
    store.cleanup()
    assert not history_root.exists()


def test_large_document_switches_to_grouped_native_history(tmp_path: Path) -> None:
    from hwp_live_edit_history import (  # noqa: PLC0415
        LiveEditHistoryStore,
        NativeDocumentEditHistoryEntry,
    )
    from hwp_live_edit_history_runtime import (  # noqa: PLC0415
        should_use_document_checkpoint,
    )

    document = tmp_path / "large.hwp"
    with document.open("wb") as output:
        output.truncate(193 * 1024 * 1024)

    assert not should_use_document_checkpoint(str(document))
    store = LiveEditHistoryStore(temp_parent=tmp_path)
    entry = NativeDocumentEditHistoryEntry(
        document_id=11,
        full_name=str(document),
        operation="document.delete_page",
        maximum_native_steps=1,
        before_page_count=4,
        after_page_count=3,
        page=4,
    )
    store.record(entry)
    assert store.peek("undo", 11, str(document)) == entry
    store.commit("undo", entry)
    assert store.peek("redo", 11, str(document)) == entry
    store.cleanup()


def test_grouped_native_history_stops_at_verified_operation_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hwp_live_edit_history_runtime as runtime  # noqa: PLC0415

    matches = iter((False, False, True))
    calls: list[str] = []

    def fake_history(
        _window_handle: int, direction: str, steps: int
    ) -> SimpleNamespace:
        calls.append(direction)
        assert steps == 1
        return SimpleNamespace(elapsed_microseconds=7)

    monkeypatch.setattr(runtime, "execute_native_history", fake_history)
    monkeypatch.setattr(
        runtime, "_matches_expected_state", lambda *args, **kwargs: next(matches)
    )

    executed, elapsed = runtime._execute_native_history_until_state(
        _WindowCandidate(window_handle=17),
        "undo",
        5,
        page_count=3,
    )

    assert executed == 2
    assert elapsed == 14
    assert calls == ["undo", "undo"]


def test_document_history_capture_uses_disk_checkpoint_command(
    tmp_path: Path,
) -> None:
    from hwp_live_edit_history import build_capture_document_commands  # noqa: PLC0415

    checkpoint_path = tmp_path / "before.hwp-checkpoint"

    assert build_capture_document_commands(checkpoint_path) == (
        SaveDocumentFileCommand(checkpoint_path),
    )

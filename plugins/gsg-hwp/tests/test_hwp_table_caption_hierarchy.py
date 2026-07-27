from __future__ import annotations

import sys
from pathlib import Path
from typing import cast, final, override

import pytest

SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_caption as caption_module  # noqa: E402
from hwp_live_api import (  # noqa: E402
    HwpComApplication,
    HwpComDocument,
    LiveHwpApplication,
)
from hwp_live_caption import (  # noqa: E402
    TableCaptionFormatSource,
    caption_format_sources,
    find_hierarchical_table_caption_source,
    has_hierarchical_table_caption,
)
from hwp_live_contract import LayoutPlan  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    CaptionCommand,
    NativeActionResult,
    NativeCharacterFormat,
    NativeDetailedCaption,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
    RunCommand,
    SelectControlCommand,
)
from hwp_live_native_layout import (  # noqa: E402
    NativeLayoutContext,
    build_native_layout_request,
)
from hwp_live_table_contract import TableBlock, TableCell  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402


class _StateWrapper:
    cursor: tuple[int, int, int]
    restores: int

    def __init__(self) -> None:
        self.cursor = (0, 7, 0)
        self.restores = 0

    @property
    def IsModified(self) -> bool:
        return False

    def get_pos(self) -> tuple[int, int, int]:
        return self.cursor

    def get_selected_pos(
        self,
    ) -> tuple[
        bool,
        int | None,
        int | None,
        int | None,
        int | None,
        int | None,
        int | None,
    ]:
        return (False, None, None, None, None, None, None)

    def set_pos(self, list_id: int, paragraph: int, character: int) -> bool:
        self.cursor = (list_id, paragraph, character)
        self.restores += 1
        return True

    def get_page_text(self, page: int) -> str:
        return "<표 5.5.3-23> 원본" if page == 2 else "본문"


@final
class _ParagraphWrapper(_StateWrapper):
    def __init__(self) -> None:
        super().__init__()
        self.selected = False

    @property
    def current_page(self) -> int:
        return 3 if self.cursor[1] >= 10 else 2

    @override
    def get_selected_pos(
        self,
    ) -> tuple[
        bool,
        int | None,
        int | None,
        int | None,
        int | None,
        int | None,
        int | None,
    ]:
        if not self.selected:
            return (False, None, None, None, None, None, None)
        list_id, paragraph, character = self.cursor
        return (True, list_id, paragraph, character, list_id, paragraph, 20)

    @override
    def set_pos(self, list_id: int, paragraph: int, character: int) -> bool:
        self.selected = False
        return super().set_pos(list_id, paragraph, character)

    def MoveParaBegin(self) -> bool:
        self.cursor = (self.cursor[0], self.cursor[1], 0)
        return True

    def MoveSelParaEnd(self) -> bool:
        self.selected = True
        return True

    def get_text_file(self, *, format: str, option: str) -> str:
        assert (format, option) == ("UNICODE", "saveblock:true")
        return "<표 5.5.3-23> 원본" if self.cursor[1] == 11 else "본문"


def _captioned_table(*, style_id: int) -> TableBlock:
    return TableBlock(
        kind="table",
        caption="새 표",
        base_style_id=0,
        caption_style_id=style_id,
        rows=((TableCell(text="값"),),),
    )


def test_hierarchical_table_caption_detection_rejects_flat_sequence() -> None:
    assert has_hierarchical_table_caption("<표 5.5.3-23> 기존 표")
    assert has_hierarchical_table_caption("(표 2.4-7) 기존 표")
    assert not has_hierarchical_table_caption("표 42 기존 표")
    assert not has_hierarchical_table_caption("<그림 5.5.3-23> 기존 그림")


def test_matching_hierarchical_caption_source_reaches_native_caption_command() -> None:
    source_position = NativePosition(0, 41, 0)
    plan = LayoutPlan(blocks=(_captioned_table(style_id=4),))
    sources = caption_format_sources(
        plan,
        TableCaptionFormatSource(style_id=4, position=source_position),
    )

    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            style_ids=(),
            caption_format_sources=sources,
        ),
        plan,
        {},
    )

    caption = next(
        command for command in request.commands if isinstance(command, CaptionCommand)
    )
    assert caption.format_source == source_position
    caption_index = request.commands.index(caption)
    assert request.commands[caption_index + 1 : caption_index + 9] == (
        RunCommand("ShapeObjAttachCaption"),
        RunCommand("MoveParaBegin"),
        RunCommand("MoveSelRight"),
        RunCommand("MoveSelRight"),
        RunCommand("MoveSelRight"),
        RunCommand("MoveSelRight"),
        RunCommand("Delete"),
        RunCommand("CloseEx"),
    )


def test_nonmatching_caption_style_keeps_existing_flat_number_behavior() -> None:
    plan = LayoutPlan(blocks=(_captioned_table(style_id=4),))

    sources = caption_format_sources(
        plan,
        TableCaptionFormatSource(
            style_id=11,
            position=NativePosition(0, 41, 0),
        ),
    )

    assert sources == ()


def test_hierarchical_caption_source_is_read_from_nearest_attached_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "C:/documents/sample.hwp"
    table_position = NativePosition(0, 12, 0)
    source_position = NativePosition(31, 4, 0)
    detailed = NativeDetailedInspection(
        document_id=17,
        full_name=path,
        page=3,
        page_count=5,
        text="<표 5.5.3-23> 원본",
        controls=(
            NativeDetailedControl(
                control_type="tbl",
                instance_id="table-1",
                user_description="표",
                anchor=table_position,
                page_start=3,
                page_end=3,
                top_level=True,
                rows=2,
                columns=2,
            ),
        ),
        cells=(),
        captions=(
            NativeDetailedCaption(
                table_instance_id="table-1",
                text="원본",
                automatic_number=False,
                style_id=4,
                style_name="",
                page_start=3,
                page_end=3,
            ),
        ),
    )
    snapshot = NativeSnapshot(
        document_id=17,
        full_name=path,
        current_page=3,
        page_count=5,
        modified=False,
        cursor=source_position,
        selection=NativeSelection(
            selected=True,
            start=source_position,
            end=NativePosition(31, 4, 2),
        ),
        selected_text="원본",
        control_type="tbl",
        control_instance_id="table-1",
        cell_address="",
        style_id=4,
        character_format=NativeCharacterFormat("함초롬바탕", 1_000, False, 0),
        paragraph_format=NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )
    native_result = NativeActionResult(4, 4, 0, 0, 10, ())
    requests: list[tuple[object, ...]] = []

    def execute(
        window_handle: int,
        request: object,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        _ = window_handle, minimum_version
        commands = cast(tuple[object, ...], getattr(request, "commands"))
        requests.append(commands)
        return native_result

    def inspect(
        window_handle: int,
        page: int,
    ) -> NativeDetailedInspection | None:
        return detailed if (window_handle, page) == (101, 3) else None

    def read_snapshot(window_handle: int) -> NativeSnapshot | None:
        return snapshot if window_handle == 101 else None

    monkeypatch.setattr(
        caption_module,
        "inspect_native_structure",
        inspect,
    )
    monkeypatch.setattr(caption_module, "execute_native_actions", execute)
    monkeypatch.setattr(
        caption_module,
        "read_native_snapshot",
        read_snapshot,
    )
    wrapper = _StateWrapper()
    candidate = HwpDocumentCandidate(
        selector="sample",
        moniker_name="!HwpObject.1",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, object()),
        document_id=17,
        full_name=path,
        document_format="HWP",
        edit_mode=1,
        window_handle=101,
        active=True,
    )

    source = find_hierarchical_table_caption_source(
        candidate,
        cast(LiveHwpApplication, cast(object, wrapper)),
        lambda: None,
        setup_page=5,
    )

    assert source == TableCaptionFormatSource(4, source_position)
    assert requests == [
        (
            SelectControlCommand("table-1"),
            RunCommand("ShapeObjAttachCaption"),
            RunCommand("MoveParaBegin"),
            RunCommand("MoveSelParaEnd"),
        ),
        (RunCommand("CloseEx"),),
    ]
    assert wrapper.restores == 1


def test_manual_hierarchical_title_before_table_supplies_format_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "C:/documents/sample.hwp"
    source_position = NativePosition(0, 11, 0)
    detailed = NativeDetailedInspection(
        document_id=17,
        full_name=path,
        page=3,
        page_count=5,
        text="<표 5.5.3-23> 원본",
        controls=(
            NativeDetailedControl(
                control_type="tbl",
                instance_id="table-1",
                user_description="표",
                anchor=NativePosition(0, 12, 0),
                page_start=3,
                page_end=3,
                top_level=True,
                rows=2,
                columns=2,
            ),
        ),
        cells=(),
        captions=(),
    )
    snapshot = NativeSnapshot(
        document_id=17,
        full_name=path,
        current_page=3,
        page_count=5,
        modified=False,
        cursor=NativePosition(0, 11, 20),
        selection=NativeSelection(
            selected=True,
            start=source_position,
            end=NativePosition(0, 11, 20),
        ),
        selected_text="<표 5.5.3-23> 원본",
        control_type="",
        control_instance_id="",
        cell_address="",
        style_id=4,
        character_format=NativeCharacterFormat("함초롬바탕", 1_000, False, 0),
        paragraph_format=NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )

    def inspect(
        window_handle: int,
        page: int,
    ) -> NativeDetailedInspection | None:
        return detailed if (window_handle, page) == (101, 3) else None

    def read_snapshot(window_handle: int) -> NativeSnapshot | None:
        return snapshot if window_handle == 101 else None

    monkeypatch.setattr(caption_module, "inspect_native_structure", inspect)
    monkeypatch.setattr(caption_module, "read_native_snapshot", read_snapshot)
    wrapper = _ParagraphWrapper()
    candidate = HwpDocumentCandidate(
        selector="sample",
        moniker_name="!HwpObject.1",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, object()),
        document_id=17,
        full_name=path,
        document_format="HWP",
        edit_mode=1,
        window_handle=101,
        active=True,
    )

    source = find_hierarchical_table_caption_source(
        candidate,
        cast(LiveHwpApplication, cast(object, wrapper)),
        lambda: None,
        setup_page=5,
    )

    assert source == TableCaptionFormatSource(4, source_position)
    assert wrapper.cursor == (0, 7, 0)
    assert not wrapper.selected

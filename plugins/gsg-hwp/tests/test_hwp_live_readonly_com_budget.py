"""Cost and safety pins for the three read-only live inspection paths.

These cover work that must not change what the caller sees while spending fewer
cross-process COM round trips:

* ``hwp_live_preview.render_page`` -- must not ask HWP for the caret page a
  second time when ``goto_page`` already reported it, and must still hand back
  the caret and selection exactly as it found them.
* ``hwp_live_caption.find_hierarchical_table_caption_source`` -- must scan the
  whole page text instead of silently dropping everything past a fixed cut.
* ``hwp_live_inspection.inspect_styles`` -- must leave the selection untouched
  and must not pay for a caret read whose value it never looks at.

Every ``guard()`` is four COM round trips (``XHwpDocuments`` ->
``Active_XHwpDocument`` -> ``DocumentID`` -> ``FullName``), so guard counts are
pinned alongside the direct wrapper calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast, final

import pytest
from PIL import Image

SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_caption as caption_module  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import LiveHwpApplication, SelectionRange  # noqa: E402
from hwp_live_caption import (  # noqa: E402
    TableCaptionFormatSource,
    find_hierarchical_table_caption_source,
)
from hwp_live_contract import DocumentStyleList, PreviewResult  # noqa: E402
from hwp_live_inspection import inspect_styles  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeActionResult,
    NativeCharacterFormat,
    NativeDetailedCaption,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
)
from hwp_live_preview import PreviewApplication, render_page  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402


NO_SELECTION: SelectionRange = (False, None, None, None, None, None, None)


# --------------------------------------------------------------------------
# (1) render_page
# --------------------------------------------------------------------------
@final
class _RecordingDocument:
    Modified: int = 0


@final
class _RecordingCandidate:
    document: _RecordingDocument = _RecordingDocument()
    window_handle: int = 100


@final
class _RecordingViewProperties:
    def __init__(self, option_flags: int) -> None:
        self.option_flags = option_flags

    def Item(self, name: str) -> int:  # noqa: N802
        assert name == "OptionFlag"
        return self.option_flags

    def SetItem(self, name: str, value: int) -> None:  # noqa: N802
        assert name == "OptionFlag"
        self.option_flags = value


@final
class _RecordingComApplication:
    def __init__(self, option_flags: int) -> None:
        self._view_properties = _RecordingViewProperties(option_flags)

    @property
    def ViewProperties(self) -> _RecordingViewProperties:  # noqa: N802
        return self._view_properties

    @ViewProperties.setter
    def ViewProperties(self, value: _RecordingViewProperties) -> None:  # noqa: N802
        self._view_properties = value


@final
class _RecordingPreviewHwp:
    """Counts every wrapper member render_page touches."""

    def __init__(
        self,
        *,
        page_count: int = 2,
        selection_mode: int = 0,
        selection: SelectionRange = NO_SELECTION,
        cursor: tuple[int, int, int] = (0, 0, 0),
        goto_result: object = None,
    ) -> None:
        self.calls: list[str] = []
        self._page = 1
        self._page_count = page_count
        self._cursor = cursor
        self._selection = selection
        self._selection_mode = selection_mode
        self._goto_result = goto_result
        self._track = selection[0]
        self.SelectionMode = selection_mode
        self.hwp = _RecordingComApplication(0x0008)

    @property
    def PageCount(self) -> int:  # noqa: N802
        self.calls.append("PageCount")
        return self._page_count

    @property
    def current_page(self) -> int:
        self.calls.append("current_page")
        return self._page

    def get_pos(self) -> tuple[int, int, int]:
        self.calls.append("get_pos")
        return self._cursor

    def get_selected_pos(self) -> SelectionRange:
        self.calls.append("get_selected_pos")
        return self._selection

    def RecalcPageCount(self) -> bool:  # noqa: N802
        self.calls.append("RecalcPageCount")
        return True

    def goto_page(self, page_index: int | str = 1) -> object:
        self.calls.append("goto_page")
        self._page = int(page_index)
        if self._track:
            self._cursor = (0, self._page, 0)
        if self._goto_result is not None:
            return self._goto_result
        return (self._page, self._page)

    def set_pos(self, List: int, para: int, pos: int) -> bool:  # noqa: N803
        self.calls.append("set_pos")
        self._cursor = (List, para, pos)
        self._selection = NO_SELECTION
        self.SelectionMode = 0
        return True

    def select_text(self, selection: SelectionRange) -> bool:
        self.calls.append("select_text")
        self._selection = selection
        self.SelectionMode = self._selection_mode
        end_list, end_para, end_pos = selection[4:]
        self._cursor = (int(end_list or 0), int(end_para or 0), int(end_pos or 0))
        return True

    def Cancel(self) -> bool:  # noqa: N802
        self.calls.append("Cancel")
        self._selection = NO_SELECTION
        self.SelectionMode = 0
        return True

    def get_cell_addr(self) -> str:
        self.calls.append("get_cell_addr")
        return "C4"

    def TableCellBlock(self) -> bool:  # noqa: N802
        return True

    def TableCellBlockExtend(self) -> bool:  # noqa: N802
        return True

    def TableRightCell(self) -> bool:  # noqa: N802
        return True

    def TableLowerCell(self) -> bool:  # noqa: N802
        return True

    def create_page_image(
        self,
        path: str,
        pgno: int = -1,
        resolution: int = 300,
        depth: int = 24,
        format: str = "bmp",
    ) -> bool:
        _ = (depth, format)
        self.calls.append(f"create_page_image(pgno={pgno},res={resolution})")
        image = Image.new("RGB", (16, 16), "white")
        image.putpixel((8, 8), (0, 0, 0))
        image.save(path)
        return True


def _render(
    hwp: _RecordingPreviewHwp,
    directory: Path,
    page: int = 2,
) -> tuple[PreviewResult, int]:
    guards = [0]

    def guard() -> None:
        guards[0] += 1

    result = render_page(
        _RecordingCandidate(),
        cast(PreviewApplication, cast(object, hwp)),
        page,
        144,
        "budget-session",
        guard,
        directory=directory,
    )
    return result, guards[0]


def test_page_render_reuses_the_page_goto_page_already_reported(
    tmp_path: Path,
) -> None:
    # Given an explicitly requested page, nothing needs the caret page up front.
    hwp = _RecordingPreviewHwp()

    # When
    result, guards = _render(hwp, tmp_path)

    # Then goto_page's own return value answers "which page are we on now", so
    # current_page - a four round trip property walk - is never asked again.
    assert result.page == 2
    assert hwp.calls.count("current_page") == 0
    assert hwp.calls.count("goto_page") == 1
    assert guards == 20


def test_page_render_still_asks_hwp_when_goto_page_reports_no_pair(
    tmp_path: Path,
) -> None:
    # Given pyhwpx's out-of-range shape, goto_page returns False, not a pair.
    hwp = _RecordingPreviewHwp(goto_result=False)

    # When
    result, _ = _render(hwp, tmp_path)

    # Then the direct read is still available as a fallback, so the page check
    # behaves exactly as it did before the reuse was introduced.
    assert result.page == 2
    assert hwp.calls.count("current_page") == 1


def test_page_render_reports_the_same_error_when_the_move_lands_elsewhere(
    tmp_path: Path,
) -> None:
    # Given a goto_page that claims a different page than the one requested.
    hwp = _RecordingPreviewHwp(goto_result=(9, 9))

    # When / Then the reused value must still be validated, not trusted.
    with pytest.raises(HwpLiveError, match="미리보기 쪽으로 이동하지 못했습니다"):
        _ = _render(hwp, tmp_path)


def test_page_render_restores_the_caret_and_selection_it_found(
    tmp_path: Path,
) -> None:
    # Given a live text selection the user made.
    selection: SelectionRange = (True, 0, 4, 1, 0, 4, 8)
    hwp = _RecordingPreviewHwp(
        selection_mode=1,
        selection=selection,
        cursor=(0, 4, 8),
    )

    # When
    _ = _render(hwp, tmp_path)

    # Then the cheaper page read must not cost the user their selection.
    assert hwp.get_selected_pos() == selection
    assert hwp.get_pos() == (0, 4, 8)
    assert hwp.SelectionMode == 1


# --------------------------------------------------------------------------
# (2) caption scan over long pages
# --------------------------------------------------------------------------
_FILLER = "본문 내용이 이어집니다. "
_CAPTION_MARKER = "<표 5.5.3-23> 원본 서식"


def _long_page_text(prefix_characters: int) -> str:
    """Page text whose only hierarchical caption sits past ``prefix_characters``."""
    repeats = prefix_characters // len(_FILLER) + 1
    return _FILLER * repeats + _CAPTION_MARKER


@final
class _CaptionHwp:
    def __init__(self, page_texts: dict[int, str]) -> None:
        self.page_texts = page_texts
        self.observed_text_lengths: list[int] = []
        self.cursor = (0, 7, 0)
        self.selection: SelectionRange = NO_SELECTION

    @property
    def IsModified(self) -> bool:  # noqa: N802
        return False

    def get_pos(self) -> tuple[int, int, int]:
        return self.cursor

    def get_selected_pos(self) -> SelectionRange:
        return self.selection

    def set_pos(self, List: int, para: int, pos: int) -> bool:  # noqa: N803
        self.cursor = (List, para, pos)
        return True

    def select_text(self, selection: SelectionRange) -> bool:
        self.selection = selection
        return True

    def get_page_text(self, pgno: int = 0, option: int = 0xFFFFFFFF) -> str:
        _ = option
        text = self.page_texts.get(pgno, "본문")
        self.observed_text_lengths.append(len(text))
        return text


def _candidate(path: str) -> HwpDocumentCandidate:
    return cast(
        HwpDocumentCandidate,
        cast(
            object,
            type(
                "_Candidate",
                (),
                {
                    "document_id": 1,
                    "full_name": path,
                    "window_handle": 101,
                    "active": True,
                },
            )(),
        ),
    )


def _detailed(path: str) -> NativeDetailedInspection:
    return NativeDetailedInspection(
        document_id=1,
        full_name=path,
        page=1,
        page_count=1,
        text="",
        controls=(
            NativeDetailedControl(
                control_type="tbl",
                instance_id="tbl-1",
                user_description="",
                anchor=NativePosition(0, 6, 0),
                page_start=1,
                page_end=1,
                top_level=True,
                rows=2,
                columns=2,
                width_hwpunit=1000,
                height_hwpunit=1000,
            ),
        ),
        cells=(),
        captions=(
            NativeDetailedCaption(
                table_instance_id="tbl-1",
                text="원본 서식",
                automatic_number=False,
                style_id=4,
                style_name="표제목",
                page_start=1,
                page_end=1,
            ),
        ),
        inspection_errors=(),
    )


def _snapshot(path: str) -> NativeSnapshot:
    position = NativePosition(0, 6, 0)
    return NativeSnapshot(
        document_id=1,
        full_name=path,
        current_page=1,
        page_count=1,
        modified=False,
        cursor=position,
        selection=NativeSelection(
            selected=True,
            mode=1,
            start=position,
            end=NativePosition(0, 6, 9),
            cell_addresses=(),
            cell_address_error="",
        ),
        selected_text=_CAPTION_MARKER,
        control_type="tbl",
        control_instance_id="tbl-1",
        cell_address="",
        style_id=4,
        character_format=NativeCharacterFormat("바탕", 1000, False, 0),
        paragraph_format=NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )


def _find_caption_source(
    monkeypatch: pytest.MonkeyPatch,
    page_texts: dict[int, str],
) -> tuple[TableCaptionFormatSource | None, _CaptionHwp]:
    path = "C:/documents/long.hwp"
    def inspect(window_handle: int, page: int) -> NativeDetailedInspection:
        _ = (window_handle, page)
        return _detailed(path)

    def snapshot(window_handle: int) -> NativeSnapshot:
        _ = window_handle
        return _snapshot(path)

    def actions(
        window_handle: int,
        request: object,
        *,
        minimum_version: int = 2,
    ) -> NativeActionResult:
        _ = (window_handle, request, minimum_version)
        return cast(NativeActionResult, object())

    monkeypatch.setattr(caption_module, "inspect_native_structure", inspect)
    monkeypatch.setattr(caption_module, "read_native_snapshot", snapshot)
    monkeypatch.setattr(caption_module, "execute_native_actions", actions)
    hwp = _CaptionHwp(page_texts)
    source = find_hierarchical_table_caption_source(
        _candidate(path),
        cast(LiveHwpApplication, cast(object, hwp)),
        lambda: None,
        setup_page=1,
    )
    return source, hwp


def test_hierarchical_caption_is_found_past_two_hundred_thousand_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a page whose only hierarchical caption starts well past the cut that
    # used to be applied to the scanned text.
    page_text = _long_page_text(250_000)
    assert page_text.index(_CAPTION_MARKER) > 200_000

    # When
    source, _ = _find_caption_source(monkeypatch, {0: page_text})

    # Then the caption is found instead of being silently invisible.
    assert source == TableCaptionFormatSource(4, NativePosition(0, 6, 0))


def test_caption_scan_reads_the_page_text_without_truncating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a page far longer than the old cut.
    page_text = _long_page_text(250_000)
    scanned: list[int] = []
    real_matcher = caption_module.has_hierarchical_table_caption

    def recording_matcher(text: str) -> bool:
        scanned.append(len(text))
        return real_matcher(text)

    monkeypatch.setattr(
        caption_module,
        "has_hierarchical_table_caption",
        recording_matcher,
    )

    # When
    _, hwp = _find_caption_source(monkeypatch, {0: page_text})

    # Then the matcher saw every character HWP handed over. Slicing here could
    # never save a COM round trip - the whole string has already crossed the
    # process boundary before any slice runs - so a cut could only lose a
    # caption that lives past it.
    assert len(page_text) > 200_000
    assert hwp.observed_text_lengths == [len(page_text)]
    assert scanned[0] == len(page_text)


def test_short_pages_without_a_hierarchical_caption_still_report_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a page with no hierarchical caption at all.
    source, hwp = _find_caption_source(monkeypatch, {0: "본문만 있습니다"})

    # Then a genuine "not found" is still a genuine "not found".
    assert source is None
    assert hwp.observed_text_lengths == [len("본문만 있습니다")]


# --------------------------------------------------------------------------
# (3) inspect_styles
# --------------------------------------------------------------------------
_STYLE_XML = (
    "<HWPML><HEAD><STYLELIST>"
    '<STYLE Id="0" Name="바탕글" EngName="Normal"/>'
    '<STYLE Id="4" Name="표제목" EngName="TableTitle"/>'
    "</STYLELIST></HEAD></HWPML>"
)


@final
class _StyleHwp:
    def __init__(
        self,
        *,
        selection: SelectionRange = NO_SELECTION,
        cursor: tuple[int, int, int] = (0, 7, 3),
        style_xml: str = _STYLE_XML,
    ) -> None:
        self.calls: list[str] = []
        self.cursor = cursor
        self.selection = selection
        self._style_xml = style_xml
        self.text_file_options: list[str] = []

    @property
    def IsModified(self) -> bool:  # noqa: N802
        self.calls.append("IsModified")
        return False

    def get_pos(self) -> tuple[int, int, int]:
        self.calls.append("get_pos")
        return self.cursor

    def get_selected_pos(self) -> SelectionRange:
        self.calls.append("get_selected_pos")
        return self.selection

    def MoveSelRight(self) -> bool:  # noqa: N802
        self.calls.append("MoveSelRight")
        self.selection = (True, 0, 7, 3, 0, 7, 4)
        return True

    def MoveSelLeft(self) -> bool:  # noqa: N802
        self.calls.append("MoveSelLeft")
        return True

    def get_text_file(
        self,
        format: str = "UNICODE",
        option: str = "saveblock:true",
    ) -> str:
        self.calls.append("get_text_file")
        self.text_file_options.append(f"{format}|{option}")
        return self._style_xml

    def select_text(self, selection: SelectionRange) -> bool:
        self.calls.append("select_text")
        self.selection = selection
        return True

    def set_pos(self, List: int, para: int, pos: int) -> bool:  # noqa: N803
        self.calls.append("set_pos")
        self.cursor = (List, para, pos)
        self.selection = NO_SELECTION
        return True


def _inspect(hwp: _StyleHwp) -> tuple[DocumentStyleList, int]:
    guards = [0]

    def guard() -> None:
        guards[0] += 1

    styles = inspect_styles(cast(LiveHwpApplication, cast(object, hwp)), guard)
    return styles, guards[0]


def test_style_list_is_parsed_from_the_document_style_definitions() -> None:
    # Given a caret with no selection.
    hwp = _StyleHwp()

    # When
    styles, _ = _inspect(hwp)

    # Then every declared style is reported, in document order.
    listed = [
        (style.style_id, style.name, style.english_name) for style in styles.styles
    ]
    assert listed == [(0, "바탕글", "Normal"), (4, "표제목", "TableTitle")]


def test_style_inspection_leaves_an_existing_selection_exactly_as_found() -> None:
    # Given a block the user had selected.
    selection: SelectionRange = (True, 0, 7, 3, 0, 7, 9)
    hwp = _StyleHwp(selection=selection)

    # When
    _ = _inspect(hwp)

    # Then the selection is handed back untouched, and the block export path is
    # used without ever nudging the selection to create one.
    assert hwp.selection == selection
    assert "MoveSelRight" not in hwp.calls
    assert "MoveSelLeft" not in hwp.calls
    assert hwp.text_file_options == ["HWPML2X|saveblock:true"]


def test_style_inspection_does_not_read_the_caret_it_never_uses() -> None:
    # Given a selection, the caret plays no part: the block is restored through
    # select_text and the caret comparison is dead.
    hwp = _StyleHwp(selection=(True, 0, 7, 3, 0, 7, 9))

    # When
    _, guards = _inspect(hwp)

    # Then no COM round trip is spent fetching a value that is thrown away.
    assert hwp.calls.count("get_pos") == 0
    assert hwp.calls == [
        "get_selected_pos",
        "IsModified",
        "get_text_file",
        "select_text",
        "get_selected_pos",
        "IsModified",
    ]
    assert guards == 8


def test_style_inspection_restores_the_caret_when_nothing_was_selected() -> None:
    # Given a bare caret, the selection has to be created and then undone, so
    # the caret genuinely is needed here and is still read.
    hwp = _StyleHwp(cursor=(0, 7, 3))

    # When
    _ = _inspect(hwp)

    # Then
    assert hwp.cursor == (0, 7, 3)
    assert hwp.selection == NO_SELECTION
    assert hwp.calls.count("get_pos") == 2
    assert "MoveSelRight" in hwp.calls


def test_style_inspection_rejects_a_selection_that_came_back_different() -> None:
    # Given a restore that quietly lands on a different block.
    hwp = _StyleHwp(selection=(True, 0, 7, 3, 0, 7, 9))
    original_select_text = hwp.select_text

    def drifting_select_text(selection: SelectionRange) -> bool:
        _ = original_select_text(selection)
        hwp.selection = (True, 0, 9, 0, 0, 9, 5)
        return True

    hwp.select_text = drifting_select_text  # type: ignore[method-assign]

    # When / Then the safety check still fires.
    with pytest.raises(HwpLiveError, match="문서 상태가 바뀌었습니다"):
        _ = _inspect(hwp)


def test_style_inspection_rejects_an_empty_style_document() -> None:
    hwp = _StyleHwp(style_xml="<HWPML><HEAD></HEAD></HWPML>")

    with pytest.raises(HwpLiveError, match="스타일 목록을 찾지 못했습니다"):
        _ = _inspect(hwp)

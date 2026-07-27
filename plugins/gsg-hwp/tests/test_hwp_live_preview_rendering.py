from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, final
from unittest.mock import patch

import pytest
from PIL import Image


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import SelectionRange  # noqa: E402
from hwp_live_preview import (  # noqa: E402
    CellSelectionState,
    PreviewViewProperties,
    render_page,
)


class PageRenderer(Protocol):
    def __call__(
        self,
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool: ...


@final
class _FakeDocument:
    Modified: int = 0


@final
class _FakeCandidate:
    document: _FakeDocument = _FakeDocument()
    window_handle: int = 100


@final
class _FakeViewProperties:
    def __init__(self, option_flags: int) -> None:
        self.option_flags = option_flags

    def Item(self, name: str) -> int:  # noqa: N802
        assert name == "OptionFlag"
        return self.option_flags

    def SetItem(self, name: str, value: int) -> None:  # noqa: N802
        assert name == "OptionFlag"
        self.option_flags = value


@final
class _FakeComApplication:
    def __init__(self, option_flags: int = 0x0008) -> None:
        self._view_properties: PreviewViewProperties = _FakeViewProperties(option_flags)
        self.assigned_option_flags: list[int] = []

    @property
    def ViewProperties(self) -> PreviewViewProperties:  # noqa: N802
        return self._view_properties

    @ViewProperties.setter
    def ViewProperties(self, value: PreviewViewProperties) -> None:  # noqa: N802
        self._view_properties = value
        self.assigned_option_flags.append(int(value.Item("OptionFlag")))


@final
class _FakeHwpApplication:
    def __init__(
        self,
        renderer: PageRenderer,
        option_flags: int = 0x0008,
        selection_mode: int = 0,
        selection_range: SelectionRange = (
            False,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
        page_count: int = 1,
        cursor: tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        self._current_page = 1
        self._renderer = renderer
        self._selection = selection_range
        self._selection_mode = selection_mode
        self._page_count = page_count
        self._cursor = cursor
        self._track_text_cursor = selection_range[0]
        self.hwp = _FakeComApplication(option_flags)
        self.SelectionMode = selection_mode
        self.recalc_calls = 0
        self.goto_calls = 0
        self.set_pos_calls = 0
        self.select_text_calls = 0
        self.cancel_calls = 0
        self.cell_block_calls = 0
        self.cell_block_extend_calls = 0
        self.right_calls = 0
        self.lower_calls = 0
        self.cell_address = "C4"

    @property
    def PageCount(self) -> int:
        return self._page_count

    @property
    def current_page(self) -> int:
        return self._current_page

    def get_pos(self) -> tuple[int, int, int]:
        return self._cursor

    def get_selected_pos(self) -> SelectionRange:
        return self._selection

    def RecalcPageCount(self) -> bool:
        self.recalc_calls += 1
        return True

    def goto_page(self, page_index: int | str = 1) -> tuple[int, int]:
        self.goto_calls += 1
        self._current_page = int(page_index)
        if self._track_text_cursor:
            self._cursor = (0, self._current_page, 0)
        return (0, 0)

    def set_pos(self, List: int, para: int, pos: int) -> bool:  # noqa: N803
        _ = (List, para, pos)
        self.set_pos_calls += 1
        if self._track_text_cursor:
            self._cursor = (List, para, pos)
        self._selection = (False, None, None, None, None, None, None)
        self.SelectionMode = 0
        return True

    def select_text(self, selection: SelectionRange) -> bool:
        end_list, end_para, end_pos = selection[4:]
        if (
            not isinstance(end_list, int)
            or not isinstance(end_para, int)
            or not isinstance(end_pos, int)
        ):
            return False
        self.select_text_calls += 1
        self._selection = selection
        self.SelectionMode = self._selection_mode
        self._cursor = (end_list, end_para, end_pos)
        return True

    def get_cell_addr(self) -> str:
        return self.cell_address

    def Cancel(self) -> bool:
        self.cancel_calls += 1
        self._selection = (False, None, None, None, None, None, None)
        self.SelectionMode = 0
        return True

    def TableCellBlock(self) -> bool:
        self.cell_block_calls += 1
        self.SelectionMode = 3
        return True

    def TableCellBlockExtend(self) -> bool:
        self.cell_block_extend_calls += 1
        self.SelectionMode = 19
        return True

    def TableRightCell(self) -> bool:
        self.right_calls += 1
        return True

    def TableLowerCell(self) -> bool:
        self.lower_calls += 1
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
        return self._renderer(path, pgno=pgno, resolution=resolution)


def _write_test_png(
    path: str,
    *,
    blank: bool,
    black_pixel: tuple[int, int] = (8, 8),
) -> None:
    image = Image.new("RGB", (16, 16), "white")
    if not blank:
        image.putpixel(black_pixel, (0, 0, 0))
    image.save(path)


def _native_page_structure(*, has_picture: bool) -> SimpleNamespace:
    controls = (
        (
            SimpleNamespace(
                control_type="gso",
                page_start=1,
                page_end=1,
            ),
        )
        if has_picture
        else ()
    )
    return SimpleNamespace(
        page=1,
        text="",
        controls=controls,
        cells=(),
        captions=(),
    )


def test_repeated_page_render_keeps_each_completed_result_readable(
    tmp_path: Path,
) -> None:
    # Given
    rendered_pixels = iter(((4, 4), (12, 12)))

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = (pgno, resolution)
        _write_test_png(path, blank=False, black_pixel=next(rendered_pixels))
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image)

    # When
    with patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)):
        first = render_page(candidate, hwp, 1, 144, "session-one", lambda: None)
        second = render_page(candidate, hwp, 1, 144, "session-one", lambda: None)

    # Then
    assert first.path != second.path
    with Image.open(first.path) as first_image:
        assert first_image.getpixel((4, 4)) == (0, 0, 0)
        assert first_image.getpixel((12, 12)) == (255, 255, 255)
    with Image.open(second.path) as second_image:
        assert second_image.getpixel((4, 4)) == (255, 255, 255)
        assert second_image.getpixel((12, 12)) == (0, 0, 0)
    assert not tuple(first.path.parent.glob(".rendering-*.png"))


def test_page_render_hides_guidelines_and_restores_view_options(
    tmp_path: Path,
) -> None:
    # Given
    observed_option_flags: list[int] = []

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = (pgno, resolution)
        observed_option_flags.append(hwp.hwp.ViewProperties.Item("OptionFlag"))
        _write_test_png(path, blank=False)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image, option_flags=0x0029)

    # When
    with patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)):
        _ = render_page(candidate, hwp, 1, 144, "session-view", lambda: None)

    # Then
    assert observed_option_flags == [0x0021]
    assert hwp.hwp.ViewProperties.Item("OptionFlag") == 0x0029
    assert hwp.hwp.assigned_option_flags == [0x0021, 0x0029]


def test_page_render_without_selection_keeps_current_page_render_route(
    tmp_path: Path,
) -> None:
    observed_render_state: list[tuple[int, int, int]] = []

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = resolution
        observed_render_state.append((pgno, hwp.current_page, hwp.SelectionMode))
        _write_test_png(path, blank=False)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image, page_count=2)

    with patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)):
        result = render_page(
            candidate,
            hwp,
            2,
            144,
            "session-no-selection-page-two",
            lambda: None,
        )

    assert result.page == 2
    assert observed_render_state == [(0, 2, 0)]
    assert hwp.recalc_calls == 1
    assert hwp.goto_calls == 1
    assert hwp.set_pos_calls == 1
    assert hwp.cancel_calls == 0
    assert hwp.select_text_calls == 0


def test_text_selection_is_collapsed_for_requested_page_and_restored(
    tmp_path: Path,
) -> None:
    text_selection: SelectionRange = (True, 0, 4, 1, 0, 4, 8)
    observed_render_state: list[tuple[int, int, int, bool]] = []

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = resolution
        observed_render_state.append(
            (pgno, hwp.current_page, hwp.SelectionMode, hwp.get_selected_pos()[0])
        )
        _write_test_png(path, blank=False)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(
        create_page_image,
        selection_mode=1,
        selection_range=text_selection,
        page_count=2,
        cursor=(0, 4, 8),
    )
    original_cursor = hwp.get_pos()

    with patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)):
        result = render_page(
            candidate,
            hwp,
            2,
            144,
            "session-text-selection-page-two",
            lambda: None,
        )

    assert result.page == 2
    assert observed_render_state == [(0, 2, 0, False)]
    assert hwp.get_selected_pos() == text_selection
    assert hwp.get_pos() == original_cursor
    assert hwp.SelectionMode == 1
    assert hwp.recalc_calls == 1
    assert hwp.goto_calls == 1
    assert hwp.set_pos_calls == 1
    assert hwp.cancel_calls == 1
    assert hwp.select_text_calls == 1


def test_cell_block_render_does_not_move_or_collapse_the_selection(
    tmp_path: Path,
) -> None:
    observed_pages: list[int] = []

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = resolution
        observed_pages.append(pgno)
        _write_test_png(path, blank=False)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image, selection_mode=19)

    state = CellSelectionState(101, "C4", ("lower", "lower", "lower"), 19)
    with (
        patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)),
        patch("hwp_live_preview._capture_cell_selection", return_value=state),
    ):
        result = render_page(
            candidate,
            hwp,
            1,
            144,
            "session-cell-block",
            lambda: None,
        )

    with Image.open(result.path) as image:
        assert image.getpixel((8, 8)) == (0, 0, 0)
    assert observed_pages == [0]
    assert hwp.SelectionMode == 19
    assert hwp.recalc_calls == 1
    assert hwp.goto_calls == 1
    assert hwp.set_pos_calls == 2
    assert hwp.cancel_calls == 1
    assert hwp.cell_block_calls == 1
    assert hwp.cell_block_extend_calls == 1
    assert hwp.right_calls == 0
    assert hwp.lower_calls == 3


def test_cell_block_render_rejects_a_collapsed_selection(tmp_path: Path) -> None:
    candidate = _FakeCandidate()

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = (pgno, resolution)
        _write_test_png(path, blank=False)
        hwp.SelectionMode = 0
        return True

    hwp = _FakeHwpApplication(create_page_image, selection_mode=19)

    state = CellSelectionState(101, "C4", ("lower",), 19)
    with (
        patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)),
        patch("hwp_live_preview._capture_cell_selection", return_value=state),
        patch(
            "hwp_live_preview._restore_cell_selection",
            side_effect=HwpLiveError("셀 블록 복원 실패"),
        ),
        pytest.raises(HwpLiveError, match="복원 실패"),
    ):
        _ = render_page(
            candidate,
            hwp,
            1,
            144,
            "session-cell-block-collapse",
            lambda: None,
        )

    assert not tuple((tmp_path / "hancom-live-agent").rglob("*.png"))


def test_failed_page_render_removes_incomplete_file(tmp_path: Path) -> None:
    # Given
    def create_incomplete_page(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = (pgno, resolution)
        _ = Path(path).write_bytes(b"incomplete")
        return False

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_incomplete_page)

    # When
    with (
        patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)),
        pytest.raises(HwpLiveError, match="만들지 못했습니다"),
    ):
        _ = render_page(candidate, hwp, 1, 144, "session-two", lambda: None)

    # Then
    preview_root = tmp_path / "hancom-live-agent"
    assert not tuple(preview_root.rglob("*.png"))
    assert hwp.hwp.ViewProperties.Item("OptionFlag") == 0x0008
    assert hwp.hwp.assigned_option_flags == [0, 0x0008]


def test_content_page_retries_one_blank_render_and_publishes_second(
    tmp_path: Path,
) -> None:
    render_count = 0

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        nonlocal render_count
        _ = (pgno, resolution)
        render_count += 1
        _write_test_png(path, blank=render_count == 1)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image)

    with (
        patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)),
        patch(
            "hwp_live_preview.inspect_native_structure",
            return_value=_native_page_structure(has_picture=True),
        ) as inspect_structure,
    ):
        result = render_page(
            candidate,
            hwp,
            1,
            144,
            "session-blank-then-content",
            lambda: None,
        )

    assert render_count == 2
    assert inspect_structure.call_count == 1
    with Image.open(result.path) as image:
        assert image.getextrema() == ((0, 255), (0, 255), (0, 255))


def test_content_page_fails_after_two_blank_renders(tmp_path: Path) -> None:
    render_count = 0

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        nonlocal render_count
        _ = (pgno, resolution)
        render_count += 1
        _write_test_png(path, blank=True)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image)

    with (
        patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)),
        patch(
            "hwp_live_preview.inspect_native_structure",
            return_value=_native_page_structure(has_picture=True),
        ),
        pytest.raises(HwpLiveError, match="빈 미리보기"),
    ):
        _ = render_page(
            candidate,
            hwp,
            1,
            144,
            "session-two-blank-renders",
            lambda: None,
        )

    assert render_count == 2
    assert not tuple((tmp_path / "hancom-live-agent").rglob("*.png"))


def test_actually_blank_page_is_published_without_retry(tmp_path: Path) -> None:
    render_count = 0

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        nonlocal render_count
        _ = (pgno, resolution)
        render_count += 1
        _write_test_png(path, blank=True)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image)

    with (
        patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)),
        patch(
            "hwp_live_preview.inspect_native_structure",
            return_value=_native_page_structure(has_picture=False),
        ) as inspect_structure,
    ):
        result = render_page(
            candidate,
            hwp,
            1,
            144,
            "session-actually-blank",
            lambda: None,
        )

    assert render_count == 1
    assert inspect_structure.call_count == 1
    assert result.path.is_file()


def test_blank_render_retry_preserves_cell_block_selection(tmp_path: Path) -> None:
    render_count = 0

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        nonlocal render_count
        _ = (pgno, resolution)
        render_count += 1
        _write_test_png(path, blank=render_count == 1)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image, selection_mode=19)
    state = CellSelectionState(101, "C4", ("lower", "lower"), 19)

    with (
        patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)),
        patch("hwp_live_preview._capture_cell_selection", return_value=state),
        patch(
            "hwp_live_preview.inspect_native_structure",
            return_value=_native_page_structure(has_picture=True),
        ),
    ):
        result = render_page(
            candidate,
            hwp,
            1,
            144,
            "session-cell-selection-retry",
            lambda: None,
        )

    assert result.path.is_file()
    assert render_count == 2
    assert hwp.SelectionMode == 19
    assert hwp.recalc_calls == 2
    assert hwp.goto_calls == 2
    assert hwp.set_pos_calls == 2
    assert hwp.cancel_calls == 1
    assert hwp.cell_block_calls == 1
    assert hwp.cell_block_extend_calls == 1
    assert hwp.lower_calls == 2


def test_blank_render_retry_keeps_text_selection_collapsed_until_restore(
    tmp_path: Path,
) -> None:
    text_selection: SelectionRange = (True, 0, 7, 2, 0, 7, 9)
    observed_render_state: list[tuple[int, int, int, bool]] = []

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = resolution
        observed_render_state.append(
            (pgno, hwp.current_page, hwp.SelectionMode, hwp.get_selected_pos()[0])
        )
        _write_test_png(path, blank=len(observed_render_state) == 1)
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(
        create_page_image,
        selection_mode=1,
        selection_range=text_selection,
        page_count=2,
        cursor=(0, 7, 9),
    )
    original_cursor = hwp.get_pos()

    with (
        patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)),
        patch(
            "hwp_live_preview.inspect_native_structure",
            return_value=_native_page_structure(has_picture=True),
        ),
    ):
        result = render_page(
            candidate,
            hwp,
            2,
            144,
            "session-text-selection-retry",
            lambda: None,
        )

    assert result.path.is_file()
    assert observed_render_state == [
        (0, 2, 0, False),
        (0, 2, 0, False),
    ]
    assert hwp.get_selected_pos() == text_selection
    assert hwp.get_pos() == original_cursor
    assert hwp.SelectionMode == 1
    assert hwp.recalc_calls == 2
    assert hwp.goto_calls == 2
    assert hwp.set_pos_calls == 1
    assert hwp.cancel_calls == 1
    assert hwp.select_text_calls == 1

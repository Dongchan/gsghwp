from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol, final
from unittest.mock import patch

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import SelectionRange  # noqa: E402
from hwp_live_preview import render_page  # noqa: E402


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
        self._view_properties = _FakeViewProperties(option_flags)
        self.assigned_option_flags: list[int] = []

    @property
    def ViewProperties(self) -> _FakeViewProperties:  # noqa: N802
        return self._view_properties

    @ViewProperties.setter
    def ViewProperties(self, value: _FakeViewProperties) -> None:  # noqa: N802
        self._view_properties = value
        self.assigned_option_flags.append(value.option_flags)


@final
class _FakeHwpApplication:
    def __init__(self, renderer: PageRenderer, option_flags: int = 0x0008) -> None:
        self._current_page = 1
        self._renderer = renderer
        self.hwp = _FakeComApplication(option_flags)

    @property
    def PageCount(self) -> int:
        return 1

    @property
    def current_page(self) -> int:
        return self._current_page

    def get_pos(self) -> tuple[int, int, int]:
        return (0, 0, 0)

    def get_selected_pos(self) -> SelectionRange:
        return (False, None, None, None, None, None, None)

    def RecalcPageCount(self) -> bool:
        return True

    def goto_page(self, page_index: int | str = 1) -> tuple[int, int]:
        self._current_page = int(page_index)
        return (0, 0)

    def set_pos(self, List: int, para: int, pos: int) -> bool:  # noqa: N803
        _ = (List, para, pos)
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


def test_repeated_page_render_keeps_each_completed_result_readable(
    tmp_path: Path,
) -> None:
    # Given
    rendered_contents = iter((b"first-render", b"second-render"))

    def create_page_image(
        path: str,
        *,
        pgno: int,
        resolution: int,
    ) -> bool:
        _ = (pgno, resolution)
        _ = Path(path).write_bytes(next(rendered_contents))
        return True

    candidate = _FakeCandidate()
    hwp = _FakeHwpApplication(create_page_image)

    # When
    with patch("hwp_live_preview.tempfile.gettempdir", return_value=str(tmp_path)):
        first = render_page(candidate, hwp, 1, 144, "session-one", lambda: None)
        second = render_page(candidate, hwp, 1, 144, "session-one", lambda: None)

    # Then
    assert first.path != second.path
    assert first.path.read_bytes() == b"first-render"
    assert second.path.read_bytes() == b"second-render"
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
        _ = Path(path).write_bytes(b"render")
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

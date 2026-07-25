from __future__ import annotations

import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from hwp_errors import HwpLiveError
from hwp_live_api import SelectionRange
from hwp_live_contract import PreviewResult


class PreviewDocument(Protocol):
    @property
    def Modified(self) -> int: ...


class PreviewCandidate(Protocol):
    @property
    def document(self) -> PreviewDocument: ...


class PreviewViewProperties(Protocol):
    def Item(self, name: str) -> int: ...  # noqa: N802

    def SetItem(self, name: str, value: int) -> None: ...  # noqa: N802


class PreviewComApplication(Protocol):
    @property
    def ViewProperties(self) -> PreviewViewProperties: ...  # noqa: N802

    @ViewProperties.setter
    def ViewProperties(  # noqa: N802
        self,
        value: PreviewViewProperties,
    ) -> None: ...


class PreviewApplication(Protocol):
    hwp: PreviewComApplication

    @property
    def PageCount(self) -> int: ...

    @property
    def current_page(self) -> int: ...

    def get_pos(self) -> tuple[int, int, int]: ...

    def get_selected_pos(self) -> SelectionRange: ...

    def RecalcPageCount(self) -> bool: ...

    def goto_page(self, page_index: int | str = 1) -> tuple[int, int]: ...

    def set_pos(self, List: int, para: int, pos: int) -> bool: ...

    def create_page_image(
        self,
        path: str,
        pgno: int = -1,
        resolution: int = 300,
        depth: int = 24,
        format: str = "bmp",
    ) -> bool: ...


def _hide_guidelines(hwp: PreviewApplication, guard: Callable[[], None]) -> int:
    properties = hwp.hwp.ViewProperties
    guard()
    option_flags = int(properties.Item("OptionFlag"))
    guard()
    properties.SetItem("OptionFlag", option_flags & ~0x0008)
    hwp.hwp.ViewProperties = properties
    guard()
    return option_flags


def _restore_guidelines(
    hwp: PreviewApplication,
    option_flags: int,
    guard: Callable[[], None],
) -> None:
    properties = hwp.hwp.ViewProperties
    guard()
    properties.SetItem("OptionFlag", option_flags)
    hwp.hwp.ViewProperties = properties
    guard()


def render_page(
    candidate: PreviewCandidate,
    hwp: PreviewApplication,
    page: int,
    dpi: int,
    session_id: str,
    guard: Callable[[], None],
    *,
    directory: Path | None = None,
) -> PreviewResult:
    guard()
    if dpi < 72 or dpi > 600:
        raise HwpLiveError("미리보기 DPI는 72부터 600까지 지원합니다")
    if page == 0:
        target_page = hwp.current_page
        guard()
    else:
        target_page = page
    page_count = hwp.PageCount
    guard()
    if target_page < 1 or target_page > page_count:
        raise HwpLiveError("미리보기 쪽 번호가 문서 범위를 벗어났습니다")
    cursor = hwp.get_pos()
    guard()
    selection = hwp.get_selected_pos()
    guard()
    modified = candidate.document.Modified
    guard()
    before = (modified, cursor, selection)
    output_directory = (
        Path(tempfile.gettempdir()) / "hancom-live-agent" / session_id
        if directory is None
        else directory
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    render_id = secrets.token_hex(6)
    partial_path = (
        output_directory / f".rendering-page-{target_page:03}-{render_id}.png"
    )
    path = output_directory / f"page-{target_page:03}-{render_id}.png"
    published = False
    try:
        try:
            option_flags = _hide_guidelines(hwp, guard)
            try:
                if selection[0]:
                    rendered = hwp.create_page_image(
                        str(partial_path),
                        pgno=target_page,
                        resolution=dpi,
                    )
                    guard()
                else:
                    _ = hwp.RecalcPageCount()
                    guard()
                    _ = hwp.goto_page(target_page)
                    guard()
                    current_page = hwp.current_page
                    guard()
                    if current_page != target_page:
                        raise HwpLiveError("한컴 미리보기 쪽으로 이동하지 못했습니다")
                    rendered = hwp.create_page_image(
                        str(partial_path),
                        pgno=0,
                        resolution=dpi,
                    )
                    guard()
            finally:
                _restore_guidelines(hwp, option_flags, guard)
        finally:
            if not selection[0]:
                guard()
                if not hwp.set_pos(*cursor):
                    raise HwpLiveError("한컴 미리보기 후 커서를 복원하지 못했습니다")
                guard()
        if not rendered:
            raise HwpLiveError("한컴 현재 쪽 미리보기를 만들지 못했습니다")
        modified = candidate.document.Modified
        guard()
        restored_cursor = hwp.get_pos()
        guard()
        restored_selection = hwp.get_selected_pos()
        guard()
        after = (modified, restored_cursor, restored_selection)
        if (
            before != after
            or not partial_path.is_file()
            or partial_path.stat().st_size == 0
        ):
            raise HwpLiveError("한컴 미리보기 상태 보존 검증에 실패했습니다")
        _ = partial_path.replace(path)
        published = True
        return PreviewResult(page=target_page, path=path)
    finally:
        if not published:
            partial_path.unlink(missing_ok=True)

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_contract import PreviewResult
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_safety import LIVE_OPERATION_ERRORS


def render_page(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
    page: int,
    dpi: int,
    session_id: str,
    guard: Callable[[], None],
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
    directory = Path(tempfile.gettempdir()) / "hancom-live-agent" / session_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"page-{target_page:03}.png"
    path.unlink(missing_ok=True)
    try:
        try:
            if selection[0]:
                rendered = hwp.create_page_image(
                    str(path),
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
                rendered = hwp.create_page_image(str(path), pgno=0, resolution=dpi)
                guard()
        finally:
            if not selection[0]:
                guard()
                if not hwp.set_pos(*cursor):
                    raise HwpLiveError("한컴 미리보기 후 커서를 복원하지 못했습니다")
                guard()
    except LIVE_OPERATION_ERRORS:
        path.unlink(missing_ok=True)
        raise
    if not rendered:
        raise HwpLiveError("한컴 현재 쪽 미리보기를 만들지 못했습니다")
    modified = candidate.document.Modified
    guard()
    restored_cursor = hwp.get_pos()
    guard()
    restored_selection = hwp.get_selected_pos()
    guard()
    after = (modified, restored_cursor, restored_selection)
    if before != after or not path.is_file() or path.stat().st_size == 0:
        raise HwpLiveError("한컴 미리보기 상태 보존 검증에 실패했습니다")
    return PreviewResult(page=target_page, path=path)


def cleanup_previews(session_id: str) -> None:
    directory = Path(tempfile.gettempdir()) / "hancom-live-agent" / session_id
    shutil.rmtree(directory, ignore_errors=True)

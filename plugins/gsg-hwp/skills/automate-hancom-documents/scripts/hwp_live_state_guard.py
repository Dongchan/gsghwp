from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager

from hwp_errors import HwpLiveError
from hwp_live_api import HwpControl, LiveHwpApplication, SelectionRange


def _restore(
    hwp: LiveHwpApplication,
    cursor: tuple[int, int, int],
    selection: SelectionRange,
    guard: Callable[[], None],
) -> None:
    guard()
    restored = hwp.select_text(selection) if selection[0] else hwp.set_pos(*cursor)
    guard()
    if not restored:
        raise HwpLiveError("한컴 구조를 읽은 뒤 커서와 선택 영역을 복원하지 못했습니다")
    if selection[0]:
        restored_selection = hwp.get_selected_pos()
        guard()
        if restored_selection != selection:
            raise HwpLiveError("한컴 구조를 읽은 뒤 선택 영역이 달라졌습니다")
    else:
        restored_cursor = hwp.get_pos()
        guard()
        restored_selection = hwp.get_selected_pos()
        guard()
        if restored_cursor != cursor or restored_selection[0]:
            raise HwpLiveError("한컴 구조를 읽은 뒤 커서가 달라졌습니다")


@contextmanager
def preserved_live_state(
    hwp: LiveHwpApplication,
    *,
    verify_modified: bool = True,
    guard: Callable[[], None],
) -> Generator[None]:
    guard()
    cursor = hwp.get_pos()
    guard()
    selection = hwp.get_selected_pos()
    guard()
    modified = hwp.IsModified
    guard()
    try:
        yield
    finally:
        _restore(hwp, cursor, selection, guard)
        current_modified = hwp.IsModified
        guard()
        if verify_modified and current_modified != modified:
            raise HwpLiveError("한컴 구조를 읽는 동안 문서 수정 상태가 바뀌었습니다")


def move_to_control(
    hwp: LiveHwpApplication,
    control: HwpControl,
    guard: Callable[[], None],
) -> None:
    guard()
    if not hwp.move_to_ctrl(control):
        raise HwpLiveError("한컴 개체 위치로 이동하지 못했습니다")
    guard()

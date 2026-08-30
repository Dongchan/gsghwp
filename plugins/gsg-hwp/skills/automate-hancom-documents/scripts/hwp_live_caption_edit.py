from __future__ import annotations

from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import HwpControl, LiveHwpApplication
from hwp_live_control import require_exact_control_context, select_exact_control


def attach_table_caption(
    hwp: LiveHwpApplication,
    table: HwpControl,
    title: str,
    style_name: str,
    guard: Callable[[], None],
) -> None:
    anchor = select_exact_control(hwp, table, guard)
    guard()
    table_list_id = hwp.get_pos()[0]
    guard()
    if not hwp.HAction.Run("ShapeObjAttachCaption"):
        raise HwpLiveError("한컴 표 캡션을 만들지 못했습니다")
    guard()
    caption_list_id = hwp.get_pos()[0]
    guard()
    if caption_list_id == table_list_id:
        raise HwpLiveError("정확한 표 캡션 편집 위치를 확인하지 못했습니다")
    require_exact_control_context(
        hwp,
        table,
        anchor,
        guard,
        expected_list_id=caption_list_id,
        reject_cell=True,
        error_message="정확한 표 캡션 편집 위치를 확인하지 못했습니다",
    )
    try:
        require_exact_control_context(
            hwp,
            table,
            anchor,
            guard,
            expected_list_id=caption_list_id,
            reject_cell=True,
            error_message="정확한 표 캡션 편집 위치를 확인하지 못했습니다",
        )
        if not hwp.set_style(style_name):
            raise HwpLiveError("한컴 표 캡션 스타일을 적용하지 못했습니다")
        guard()
        require_exact_control_context(
            hwp,
            table,
            anchor,
            guard,
            expected_list_id=caption_list_id,
            reject_cell=True,
            error_message="정확한 표 캡션 편집 위치를 확인하지 못했습니다",
        )
        if not hwp.MoveParaEnd():
            raise HwpLiveError("한컴 표 캡션의 제목 위치로 이동하지 못했습니다")
        guard()
        require_exact_control_context(
            hwp,
            table,
            anchor,
            guard,
            expected_list_id=caption_list_id,
            reject_cell=True,
            error_message="정확한 표 캡션 편집 위치를 확인하지 못했습니다",
        )
        if not hwp.insert_text(title):
            raise HwpLiveError("한컴 표 캡션 제목을 넣지 못했습니다")
        guard()
    finally:
        require_exact_control_context(
            hwp,
            table,
            anchor,
            guard,
            expected_list_id=caption_list_id,
            reject_cell=True,
            error_message="정확한 표 캡션 편집 위치를 확인하지 못했습니다",
        )
        if not hwp.CloseEx():
            raise HwpLiveError("한컴 표 캡션 편집을 끝내지 못했습니다")
        guard()

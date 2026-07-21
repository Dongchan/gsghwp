from __future__ import annotations

from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_control import control_anchor_position
from hwp_live_safety import LIVE_OPERATION_ERRORS


def require_empty_paragraph(
    hwp: LiveHwpApplication,
    guard: Callable[[], None],
) -> None:
    guard()
    cursor = hwp.get_pos()
    guard()
    selected_position = hwp.get_selected_pos()
    guard()
    if selected_position[0]:
        raise HwpLiveError("표는 선택 영역이 없는 빈 문단에만 삽입할 수 있습니다")
    try:
        guard()
        controls = hwp.ctrl_list
        guard()
        for control in controls:
            guard()
            position = control_anchor_position(control, guard)
            if position[:2] == cursor[:2]:
                raise HwpLiveError(
                    "표는 기존 표·그림 등 개체가 없는 빈 문단에만 삽입할 수 있습니다"
                )
    except HwpLiveError:
        raise
    except LIVE_OPERATION_ERRORS as error:
        raise HwpLiveError(
            "표 기준 문단의 기존 개체를 확인하지 못해 삽입을 중단했습니다"
        ) from error
    try:
        guard()
        if not hwp.MoveParaBegin():
            raise HwpLiveError("표 기준 문단의 시작 위치를 확인하지 못했습니다")
        guard()
        paragraph_begin = hwp.get_pos()
        guard()
        _ = hwp.MoveSelParaEnd()
        guard()
        selected_position = hwp.get_selected_pos()
        guard()
        if selected_position[0]:
            paragraph_text = hwp.get_text_file(
                format="UNICODE",
                option="saveblock:true",
            )
            guard()
        else:
            guard()
            if not hwp.MoveParaEnd():
                raise HwpLiveError("표 기준 문단의 끝 위치를 확인하지 못했습니다")
            guard()
            paragraph_end = hwp.get_pos()
            guard()
            if paragraph_end != paragraph_begin:
                raise HwpLiveError("표 기준 문단 텍스트를 안전하게 선택하지 못했습니다")
            paragraph_text = ""
    finally:
        guard()
        if not hwp.set_pos(*cursor):
            raise HwpLiveError("표 기준 문단을 확인한 뒤 커서를 복원하지 못했습니다")
        guard()
    if paragraph_text.strip("\r\n"):
        raise HwpLiveError("표는 기존 텍스트가 없는 빈 문단에만 삽입할 수 있습니다")

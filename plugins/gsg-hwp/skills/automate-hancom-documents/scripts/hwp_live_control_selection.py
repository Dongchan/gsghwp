from __future__ import annotations

from collections.abc import Callable

from pywintypes import com_error

from hwp_errors import HwpLiveError
from hwp_live_api import HwpControl, LiveHwpApplication
from hwp_live_control_core import (
    control_anchor_position,
    control_instance_id,
    require_exact_table_cell_context,
)


def _is_exact_selected_control(
    hwp: LiveHwpApplication,
    *,
    expected_ctrl_id: str,
    expected_instance_id: str,
    expected_anchor: tuple[int, int, int],
    guard: Callable[[], None],
) -> bool:
    guard()
    try:
        selected = hwp.CurSelectedCtrl
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error):
        return False
    guard()
    try:
        selected_ctrl_id = selected.CtrlID
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error):
        return False
    guard()
    try:
        selected_instance_id = str(selected.GetCtrlInstID()).strip()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error):
        return False
    guard()
    try:
        selected_anchor = control_anchor_position(selected, guard)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error):
        return False
    guard()
    return (
        selected_ctrl_id == expected_ctrl_id
        and selected_instance_id == expected_instance_id
        and selected_anchor == expected_anchor
    )


def select_exact_control(
    hwp: LiveHwpApplication,
    control: HwpControl,
    guard: Callable[[], None],
) -> tuple[int, int, int]:
    guard()
    anchor = control_anchor_position(control, guard)
    expected_ctrl_id = control.CtrlID
    guard()
    expected_instance_id = control_instance_id(control, guard)
    _ = hwp.SelectCtrl(control, option=1)
    guard()
    if _is_exact_selected_control(
        hwp,
        expected_ctrl_id=expected_ctrl_id,
        expected_instance_id=expected_instance_id,
        expected_anchor=anchor,
        guard=guard,
    ):
        return anchor
    if not hwp.move_to_ctrl(control):
        raise HwpLiveError("정확한 한컴 제어 개체를 선택하지 못했습니다")
    guard()
    if not hwp.SelectCtrlFront():
        raise HwpLiveError("정확한 한컴 제어 개체를 선택하지 못했습니다")
    guard()
    if not _is_exact_selected_control(
        hwp,
        expected_ctrl_id=expected_ctrl_id,
        expected_instance_id=expected_instance_id,
        expected_anchor=anchor,
        guard=guard,
    ):
        raise HwpLiveError("선택한 한컴 제어 개체가 요청한 개체와 다릅니다")
    return anchor


def enter_exact_table_control(
    hwp: LiveHwpApplication,
    control: HwpControl,
    guard: Callable[[], None],
) -> tuple[int, int, int]:
    anchor = select_exact_control(hwp, control, guard)
    guard()
    if not hwp.ShapeObjTextBoxEdit():
        raise HwpLiveError("요청한 표의 편집 영역에 들어가지 못했습니다")
    guard()
    require_exact_table_cell_context(hwp, control, anchor, guard)
    return anchor

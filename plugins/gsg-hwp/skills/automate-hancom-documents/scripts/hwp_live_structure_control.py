from __future__ import annotations

from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import HwpControl, LiveHwpApplication
from hwp_live_control import control_anchor_position, control_instance_id
from hwp_live_state_guard import move_to_control
from hwp_live_structure_contract import CreatedControl
from hwp_live_structure_identity import (
    control_kind,
    control_ref,
    structure_position,
    table_ref,
)
from hwp_live_structure_table import inspect_table_control


def find_table_control(
    hwp: LiveHwpApplication,
    document_id: int,
    requested_ref: str,
    guard: Callable[[], None],
) -> tuple[HwpControl, str, tuple[int, int, int]]:
    control_list = hwp.ctrl_list
    guard()
    for control in control_list:
        guard()
        ctrl_id = control.CtrlID
        guard()
        if ctrl_id != "tbl":
            continue
        anchor = control_anchor_position(control, guard)
        reference = control_ref(
            document_id,
            ctrl_id,
            control_instance_id(control, guard),
        )
        if table_ref(reference) == requested_ref:
            return control, reference, anchor
    raise HwpLiveError("현재 한컴 문서에서 지정한 표 참조값을 찾지 못했습니다")


def describe_created_control(
    hwp: LiveHwpApplication,
    document_id: int,
    control: HwpControl,
    guard: Callable[[], None],
) -> CreatedControl:
    guard()
    anchor = control_anchor_position(control, guard)
    ctrl_id = control.CtrlID
    guard()
    instance_id = control_instance_id(control, guard)
    control_list = hwp.ctrl_list
    guard()
    if not any(
        candidate.CtrlID == ctrl_id
        and control_instance_id(candidate, guard) == instance_id
        for candidate in control_list
    ):
        raise HwpLiveError("생성한 한컴 개체를 현재 문서에서 다시 찾지 못했습니다")
    reference = control_ref(document_id, ctrl_id, instance_id)
    if ctrl_id == "tbl":
        table = inspect_table_control(hwp, control, reference, anchor, guard)
        return CreatedControl(
            control_ref=reference,
            ctrl_id="tbl",
            kind="table",
            anchor=table.anchor,
            page_start=table.page_start,
            page_end=table.page_end,
            table_ref=table.table_ref,
        )
    move_to_control(hwp, control, guard)
    page = hwp.current_page
    guard()
    user_description = control.UserDesc
    guard()
    return CreatedControl(
        control_ref=reference,
        ctrl_id=ctrl_id,
        kind=control_kind(ctrl_id, user_description),
        anchor=structure_position(anchor),
        page_start=page,
        page_end=page,
    )

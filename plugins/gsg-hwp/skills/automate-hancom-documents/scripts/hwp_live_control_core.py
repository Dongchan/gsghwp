from __future__ import annotations

from collections.abc import Callable

from pywintypes import com_error

from hwp_errors import HwpLiveError
from hwp_live_api import HwpControl, LiveHwpApplication


def control_anchor_position(
    control: HwpControl,
    guard: Callable[[], None],
    *,
    option: int = 0,
) -> tuple[int, int, int]:
    try:
        guard()
        position = control.GetAnchorPos(option)
        guard()
        list_id = int(position.Item("List"))
        paragraph = int(position.Item("Para"))
        character = int(position.Item("Pos"))
        guard()
    except HwpLiveError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error) as exc:
        raise HwpLiveError("한컴 제어 개체의 기준 위치를 확인하지 못했습니다") from exc
    if min(list_id, paragraph, character) < 0:
        raise HwpLiveError("한컴 제어 개체의 기준 위치가 올바르지 않습니다")
    return list_id, paragraph, character


def _read_control_instance_id(control: HwpControl, error_message: str) -> str:
    try:
        identifier = str(control.GetCtrlInstID()).strip()
    except HwpLiveError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error) as exc:
        raise HwpLiveError(error_message) from exc
    if not identifier:
        raise HwpLiveError(error_message)
    return identifier


def control_instance_id(
    control: HwpControl,
    guard: Callable[[], None],
    *,
    error_message: str = "한컴 제어 개체의 고유 식별값을 확인하지 못했습니다",
) -> str:
    guard()
    identifier = _read_control_instance_id(control, error_message)
    guard()
    return identifier


def control_instance_ids(
    hwp: LiveHwpApplication,
    guard: Callable[[], None],
) -> frozenset[str]:
    guard()
    controls = tuple(hwp.ctrl_list)
    guard()
    table_controls: list[HwpControl] = []
    for control in controls:
        guard()
        ctrl_id = control.CtrlID
        guard()
        if ctrl_id == "tbl":
            table_controls.append(control)
    identifiers = tuple(
        control_instance_id(control, guard) for control in table_controls
    )
    if len(identifiers) != len(set(identifiers)):
        raise HwpLiveError("한컴 제어 개체 고유 식별값이 중복되어 작업을 중단했습니다")
    return frozenset(identifiers)


def require_hwp_2024(
    hwp: LiveHwpApplication,
    guard: Callable[[], None],
) -> None:
    guard()
    try:
        version = tuple(hwp.Version)
        major_version = int(version[0])
    except HwpLiveError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error) as exc:
        raise HwpLiveError("한컴 버전을 확인하지 못했습니다") from exc
    guard()
    if major_version < 13:
        raise HwpLiveError("실시간 구조 편집은 한/글 2024 이상에서만 지원합니다")


def find_single_new_table_control(
    hwp: LiveHwpApplication,
    before_ids: frozenset[str],
    guard: Callable[[], None],
) -> HwpControl:
    guard()
    controls = tuple(hwp.ctrl_list)
    guard()
    candidates: list[HwpControl] = []
    for control in controls:
        guard()
        ctrl_id = control.CtrlID
        guard()
        if ctrl_id != "tbl":
            continue
        identifier = control_instance_id(control, guard)
        if identifier not in before_ids:
            candidates.append(control)
    if len(candidates) != 1:
        raise HwpLiveError("방금 생성한 한컴 표 개체를 정확히 하나 식별하지 못했습니다")
    return candidates[0]


def require_exact_control_context(
    hwp: LiveHwpApplication,
    control: HwpControl,
    anchor: tuple[int, int, int],
    guard: Callable[[], None],
    *,
    expected_list_id: int | None = None,
    reject_cell: bool = False,
    error_message: str,
) -> None:
    guard()
    position = hwp.get_pos()
    guard()
    if expected_list_id is not None and position[0] != expected_list_id:
        raise HwpLiveError(error_message)
    if reject_cell:
        guard()
        in_cell = hwp.is_cell()
        guard()
        if in_cell:
            raise HwpLiveError(error_message)
    guard()
    parent = hwp.ParentCtrl
    guard()
    parent_id = parent.CtrlID
    guard()
    control_id = control.CtrlID
    guard()
    if parent_id != control_id:
        raise HwpLiveError(error_message)
    parent_instance_id = control_instance_id(
        parent,
        guard,
        error_message=error_message,
    )
    control_instance = control_instance_id(
        control,
        guard,
        error_message=error_message,
    )
    if parent_instance_id != control_instance:
        raise HwpLiveError(error_message)
    parent_anchor = control_anchor_position(parent, guard)
    if parent_anchor != anchor:
        raise HwpLiveError(error_message)


def require_exact_table_cell_context(
    hwp: LiveHwpApplication,
    control: HwpControl,
    anchor: tuple[int, int, int],
    guard: Callable[[], None],
    *,
    expected_address: str | None = None,
) -> None:
    error_message = "현재 편집 위치가 요청한 표 셀과 다릅니다"
    require_exact_control_context(
        hwp,
        control,
        anchor,
        guard,
        error_message=error_message,
    )
    guard()
    if not hwp.is_cell():
        raise HwpLiveError(error_message)
    guard()
    if expected_address is None:
        return
    address = hwp.get_cell_addr()
    guard()
    if address != expected_address:
        raise HwpLiveError(error_message)


def require_exact_table_cell_identity(
    hwp: LiveHwpApplication,
    expected_control_instance_id: str,
    guard: Callable[[], None],
    *,
    expected_address: str,
) -> None:
    error_message = "현재 편집 위치가 요청한 표 셀과 다릅니다"
    guard()
    try:
        in_cell = hwp.is_cell()
        address = hwp.get_cell_addr()
        parent = hwp.ParentCtrl
        parent_instance_id = _read_control_instance_id(parent, error_message)
    except HwpLiveError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error) as exc:
        raise HwpLiveError(error_message) from exc
    guard()
    if (
        not in_cell
        or address != expected_address
        or parent_instance_id != expected_control_instance_id
    ):
        raise HwpLiveError(error_message)

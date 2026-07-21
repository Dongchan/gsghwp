from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_native_batch import inspect_native_page


def verify_control_deletion(
    window_handle: int,
    page: int,
    instance_ids: tuple[str, ...],
    after_page_count: int,
) -> None:
    if page > after_page_count:
        return
    inspected = inspect_native_page(window_handle, page, include_cells=False)
    if inspected is None:
        raise HwpLiveError("개체 삭제 후 빠른 구조를 읽지 못했습니다")
    remaining = {control.instance_id for control in inspected.controls}.intersection(instance_ids)
    if remaining:
        raise HwpLiveError("개체 삭제 후에도 대상 ID가 구조에 남아 있습니다")

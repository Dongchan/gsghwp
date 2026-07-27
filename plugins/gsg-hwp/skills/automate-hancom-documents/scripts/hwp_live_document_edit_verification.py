from __future__ import annotations

import ntpath
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_native_batch import inspect_native_page, inspect_native_structure
from hwp_live_native_structure import document_structure_from_native
from hwp_live_rot import HwpDocumentCandidate


@dataclass(frozen=True, slots=True)
class HistoryStructureSnapshot:
    document_id: int
    full_name: str
    page: int
    page_count: int
    state_token: str


def _normalized_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value))


def capture_history_structure_snapshot(
    candidate: HwpDocumentCandidate,
    page: int,
    page_count: int,
) -> HistoryStructureSnapshot:
    inspected_page = min(page, page_count)
    inspected = inspect_native_structure(candidate.window_handle, inspected_page)
    if inspected is None:
        raise HwpLiveError("한컴 실행 이력 검증용 상세 구조를 읽지 못했습니다")
    structure = document_structure_from_native(
        inspected,
        selector=candidate.selector,
        window_handle=candidate.window_handle,
    )
    if (
        structure.document_id != candidate.document_id
        or _normalized_path(structure.full_name)
        != _normalized_path(candidate.full_name)
        or structure.page_count != page_count
    ):
        raise HwpLiveError("한컴 실행 이력 구조 조회 중 대상 문서 상태가 바뀌었습니다")
    return HistoryStructureSnapshot(
        document_id=structure.document_id,
        full_name=structure.full_name,
        page=structure.page,
        page_count=structure.page_count,
        state_token=structure.state_token,
    )


def verify_history_structure_change(
    before: HistoryStructureSnapshot,
    after: HistoryStructureSnapshot,
) -> None:
    if before.document_id != after.document_id or _normalized_path(
        before.full_name
    ) != _normalized_path(after.full_name):
        raise HwpLiveError("한컴 실행 이력 검증 중 대상 문서가 바뀌었습니다")
    if before.page_count != after.page_count:
        return
    if before.page != after.page:
        raise HwpLiveError("한컴 실행 이력 검증 쪽이 전후에 달라졌습니다")
    if before.state_token == after.state_token:
        raise HwpLiveError(
            "한컴 실행 이력이 없거나 실행 후 문서 구조 상태가 바뀌지 않았습니다"
        )


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

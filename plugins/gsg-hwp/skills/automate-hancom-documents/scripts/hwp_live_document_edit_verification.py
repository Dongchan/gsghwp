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
    applied_steps: int | None = None,
) -> None:
    """Refuse to call an undo/redo a success when nothing was restored.

    `applied_steps` is what 한/글 itself said it did, and it is only used to say
    so in the message. The caller reaches this having already found no change in
    the native whole-document state (page count, control count, control hash),
    so an unchanged page token here is a second, independent no.

    That agreement is why the last branch refuses with `mutation_started=False`.
    Without it the failure travels as "unknown", `transport_error_result` reads
    unknown conservatively as changed, and the answer says `modified: true`
    about a document two probes just measured as untouched — which is the
    opposite of what was observed. The two probes before it are less certain
    about the whole document, so they stay unknown.
    """
    if before.document_id != after.document_id or _normalized_path(
        before.full_name
    ) != _normalized_path(after.full_name):
        raise HwpLiveError("한컴 실행 이력 검증 중 대상 문서가 바뀌었습니다")
    if before.page_count != after.page_count:
        return
    if before.page != after.page:
        raise HwpLiveError("한컴 실행 이력 검증 쪽이 전후에 달라졌습니다")
    if before.state_token == after.state_token:
        # The old wording was "한컴 실행 이력이 없거나 ... 바뀌지 않았습니다" — a
        # disjunction the caller could already resolve, since it knows how many
        # steps the engine reported. Say which one it was.
        ran = (
            "한컴 실행 이력을 실행했지만"
            if applied_steps is None
            else f"한컴 실행 이력을 {applied_steps}단계 실행했지만"
        )
        raise HwpLiveError(
            f"{ran} 문서 내용도 대상 쪽 구조도 바뀌지 않아 "
            "되돌린 편집을 확인하지 못했습니다. 이 호출로 문서 내용은 바뀌지 "
            "않았습니다"
            + (
                ""
                if not applied_steps
                else f"(한/글 자체 이력은 {applied_steps}단계 소비됐으므로 "
                "그대로 다시 실행하지 마세요)"
            ),
            mutation_started=False,
            # The engine consumed history steps to get here even though nothing
            # in the document moved. Offering a retry would spend more of them.
            safe_to_repeat=not applied_steps,
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

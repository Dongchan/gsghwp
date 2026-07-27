from __future__ import annotations

import ntpath
from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_contract import LiveContext, OpenDocument
from hwp_live_inspection import inspect_native_context
from hwp_live_native_action_models import NativeSnapshot
from hwp_live_native_batch import (
    inspect_native_page,
    inspect_native_structure,
    read_native_snapshot,
)
from hwp_live_native_structure import document_structure_from_native
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_structure_contract import DocumentStructure


def connected_document(
    candidate: HwpDocumentCandidate,
    snapshot: NativeSnapshot,
) -> OpenDocument:
    _require_snapshot_document(candidate, snapshot, "현재 상태")
    return OpenDocument(
        selector=candidate.selector,
        title=ntpath.basename(candidate.full_name) or "저장되지 않은 문서",
        full_name=candidate.full_name,
        document_id=candidate.document_id,
        format=candidate.document_format,
        edit_mode=candidate.edit_mode,
        modified=snapshot.modified,
        page_count=snapshot.page_count,
        active=True,
        window_handle=candidate.window_handle,
    )


def _require_snapshot_document(
    candidate: HwpDocumentCandidate,
    snapshot: NativeSnapshot,
    label: str,
) -> None:
    expected_name = ntpath.normcase(ntpath.normpath(candidate.full_name))
    actual_name = ntpath.normcase(ntpath.normpath(snapshot.full_name))
    if snapshot.document_id != candidate.document_id or actual_name != expected_name:
        raise HwpLiveError(
            f"한컴 네이티브 {label} 문서가 현재 연결 문서와 다릅니다"
        )


def inspect_candidate_context(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    guard: Callable[[], None],
) -> LiveContext:
    guard()
    snapshot = read_native_snapshot(candidate.window_handle)
    guard()
    if snapshot is None:
        raise HwpLiveError("한컴 네이티브 현재 상태 조회를 사용할 수 없습니다")
    _require_snapshot_document(candidate, snapshot, "현재 상태 조회")
    page = inspect_native_page(
        candidate.window_handle,
        snapshot.current_page,
        include_cells=False,
    )
    guard()
    if page is None:
        raise HwpLiveError("한컴 네이티브 현재 쪽 본문 조회를 사용할 수 없습니다")
    expected_name = ntpath.normcase(ntpath.normpath(candidate.full_name))
    actual_name = ntpath.normcase(ntpath.normpath(page.full_name))
    if (
        page.document_id != candidate.document_id
        or actual_name != expected_name
        or page.page != snapshot.current_page
        or page.page_count != snapshot.page_count
    ):
        raise HwpLiveError(
            "한컴 네이티브 현재 쪽 본문이 현재 상태 조회 결과와 다릅니다"
        )
    page_setup = hwp.get_pagedef_as_dict("eng")
    guard()
    return inspect_native_context(
        snapshot,
        connected_document(candidate, snapshot),
        page.text,
        page_setup,
    )


def inspect_candidate_structure(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    page: int,
    guard: Callable[[], None],
) -> DocumentStructure:
    _ = hwp
    guard()
    try:
        native_page = page
        if page == 0:
            snapshot = read_native_snapshot(candidate.window_handle)
            if snapshot is None:
                raise HwpLiveError("한컴 네이티브 현재 쪽을 읽을 수 없습니다")
            native_page = -snapshot.current_page
        native = inspect_native_structure(candidate.window_handle, native_page)
        if native is None:
            raise HwpLiveError("한컴 네이티브 상세 구조 조회를 사용할 수 없습니다")
        expected_name = ntpath.normcase(ntpath.normpath(candidate.full_name))
        actual_name = ntpath.normcase(ntpath.normpath(native.full_name))
        if native.document_id != candidate.document_id or actual_name != expected_name:
            raise HwpLiveError(
                "한컴 네이티브 상세 구조 조회 문서가 현재 연결 문서와 다릅니다"
            )
        return document_structure_from_native(
            native,
            selector=candidate.selector,
            window_handle=candidate.window_handle,
        )
    finally:
        guard()


def inspect_candidate_state(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    page: int,
    guard: Callable[[], None],
) -> tuple[LiveContext, DocumentStructure, int]:
    structure = inspect_candidate_structure(hwp, candidate, page, guard)
    context = inspect_candidate_context(hwp, candidate, guard)
    return context, structure, candidate.window_handle

from __future__ import annotations

import ntpath
from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_contract import LiveContext, OpenDocument
from hwp_live_inspection import inspect_context
from hwp_live_native_batch import (
    inspect_native_structure,
    read_native_snapshot,
)
from hwp_live_native_structure import document_structure_from_native
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_structure_contract import DocumentStructure


def connected_document(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
    guard: Callable[[], None],
) -> OpenDocument:
    guard()
    document = candidate.public()
    guard()
    page_count = hwp.PageCount
    guard()
    modified = hwp.IsModified
    guard()
    return document.model_copy(
        update={
            "active": True,
            "page_count": page_count,
            "modified": modified,
        }
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
        target_page = page
        if target_page == 0:
            snapshot = read_native_snapshot(candidate.window_handle)
            if snapshot is None:
                raise HwpLiveError("한컴 네이티브 현재 쪽을 읽을 수 없습니다")
            target_page = snapshot.current_page
        native = inspect_native_structure(candidate.window_handle, target_page)
        if native is None:
            raise HwpLiveError("한컴 네이티브 상세 구조 조회를 사용할 수 없습니다")
        expected_name = ntpath.normcase(ntpath.normpath(candidate.full_name))
        actual_name = ntpath.normcase(ntpath.normpath(native.full_name))
        if (
            native.document_id != candidate.document_id
            or actual_name != expected_name
        ):
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
    context = inspect_context(hwp, connected_document(candidate, hwp, guard), guard)
    return context, structure, candidate.window_handle

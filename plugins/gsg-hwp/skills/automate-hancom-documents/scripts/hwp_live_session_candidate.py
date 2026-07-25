from __future__ import annotations

import ntpath

from hwp_errors import HwpLiveError
from hwp_live_contract import OpenDocument, OpenDocumentList
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_session_types import DocumentCatalog


def _normalized_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value)) if value else ""


def _matches_document(document: OpenDocument, requested: str) -> bool:
    if document.selector == requested:
        return True
    lowered = requested.casefold()
    for prefix in ("document_id:", "id:"):
        if lowered.startswith(prefix):
            try:
                return document.document_id == int(requested[len(prefix) :])
            except ValueError:
                return False
    normalized = _normalized_path(requested)
    return bool(normalized) and _normalized_path(document.full_name) == normalized


def select_operation_document(
    listing: OpenDocumentList,
    requested: str | None,
) -> OpenDocument:
    editable = tuple(
        document
        for document in listing.documents
        if document.edit_mode == 1
    )
    if requested:
        matches = tuple(
            document
            for document in editable
            if _matches_document(document, requested)
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise HwpLiveError(
                "지정한 문서 경로 또는 문서 ID가 여러 HWP 프로세스와 일치합니다. "
                + "hwp_list_open_documents의 selector를 지정하세요"
            )
        raise HwpLiveError(
            "hwp_operate의 document와 일치하는 편집 문서를 찾을 수 없습니다"
        )
    active = tuple(document for document in editable if document.active)
    if len(active) == 1:
        return active[0]
    if not editable:
        raise HwpLiveError("편집 가능한 한컴 문서가 없습니다")
    if len(editable) == 1:
        return editable[0]
    raise HwpLiveError(
        "편집 가능한 한컴 문서가 여러 개입니다. 전체 경로, document_id:<ID>, "
        + "또는 hwp_list_open_documents의 selector를 지정하세요"
    )


def select_live_candidate(
    catalog: DocumentCatalog,
    selector: str,
) -> HwpDocumentCandidate:
    return select_scanned_candidate(catalog.scan(), selector)


def select_scanned_candidate(
    candidates: tuple[HwpDocumentCandidate, ...],
    selector: str,
) -> HwpDocumentCandidate:
    matches = tuple(
        candidate for candidate in candidates if candidate.selector == selector
    )
    if len(matches) != 1:
        raise HwpLiveError("선택한 한컴 문서를 정확히 하나 찾을 수 없습니다")
    candidate = matches[0]
    if candidate.edit_mode != 1:
        raise HwpLiveError("읽기 전용 한컴 문서는 라이브 편집하지 않습니다")
    return candidate

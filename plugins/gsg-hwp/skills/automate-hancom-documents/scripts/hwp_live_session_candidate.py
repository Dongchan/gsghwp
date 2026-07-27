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


def _basename_candidates(
    documents: tuple[OpenDocument, ...],
    requested: str,
) -> tuple[OpenDocument, ...]:
    if ntpath.basename(requested) != requested:
        return ()
    requested_name = requested.casefold()
    return tuple(
        document
        for document in documents
        if ntpath.basename(document.full_name).casefold() == requested_name
    )


def _document_candidate_message(candidates: tuple[OpenDocument, ...]) -> str:
    shown = candidates[:8]
    details = "; ".join(
        (
            f"selector={document.selector}, "
            + f"document_id={document.document_id}, "
            + f"path={document.full_name}"
        )
        for document in shown
    )
    omitted = len(candidates) - len(shown)
    suffix = f"; 그 외 {omitted}개" if omitted else ""
    return details + suffix


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
        basename_matches = _basename_candidates(editable, requested)
        if basename_matches:
            raise HwpLiveError(
                "파일명만으로 한컴 문서를 선택하지 않습니다. "
                + f"일치 후보 {len(basename_matches)}개: "
                + _document_candidate_message(basename_matches)
                + ". 전체 경로, document_id:<ID> 또는 selector를 지정하세요"
            )
        raise HwpLiveError(
            "document_selector와 일치하는 편집 문서를 찾을 수 없습니다. "
            + "전체 경로, document_id:<ID> 또는 "
            + "hwp_list_open_documents의 selector를 지정하세요"
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

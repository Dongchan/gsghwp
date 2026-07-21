from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_safety import require_writable_document, run_layout_mutation
from hwp_live_structure_contract import (
    DocumentStructure,
    TableCellUpdate,
    TableImageResult,
    TableImageUpdate,
    TableUpdateResult,
)
from hwp_live_table_image import apply_table_images, prepare_table_images
from hwp_live_table_update import apply_table_update, prepare_table_update


@dataclass(slots=True)
class _TableUpdateBox:
    result: TableUpdateResult | None = None


@dataclass(slots=True)
class _TableImageBox:
    result: TableImageResult | None = None


def update_validated_table_cells(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    table_ref: str,
    state_token: str,
    updates: tuple[TableCellUpdate, ...],
    unsafe_selectors: set[str],
    cached_snapshot: DocumentStructure | None,
    guard: Callable[[], None],
) -> TableUpdateResult:
    require_writable_document(unsafe_selectors, candidate.selector)
    guard()
    prepared = prepare_table_update(
        hwp,
        selector=candidate.selector,
        document_id=candidate.document.DocumentID,
        full_name=candidate.document.FullName,
        window_handle=candidate.window_handle,
        requested_ref=table_ref,
        state_token=state_token,
        updates=updates,
        cached_snapshot=cached_snapshot,
        guard=guard,
    )
    box = _TableUpdateBox()

    def mutate_table() -> None:
        box.result = apply_table_update(
            hwp,
            selector=candidate.selector,
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            window_handle=candidate.window_handle,
            prepared=prepared,
            guard=guard,
        )

    run_layout_mutation(unsafe_selectors, candidate.selector, mutate_table)
    guard()
    if box.result is None:
        raise HwpLiveError("한컴 표 수정 결과를 확인하지 못했습니다")
    return box.result


def insert_validated_table_images(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    table_ref: str,
    state_token: str,
    images: tuple[TableImageUpdate, ...],
    unsafe_selectors: set[str],
    cached_snapshot: DocumentStructure | None,
    guard: Callable[[], None],
) -> TableImageResult:
    require_writable_document(unsafe_selectors, candidate.selector)
    guard()
    prepared = prepare_table_images(
        hwp,
        selector=candidate.selector,
        document_id=candidate.document.DocumentID,
        full_name=candidate.document.FullName,
        window_handle=candidate.window_handle,
        requested_ref=table_ref,
        state_token=state_token,
        images=images,
        cached_snapshot=cached_snapshot,
        guard=guard,
    )
    box = _TableImageBox()

    def mutate_table() -> None:
        box.result = apply_table_images(
            hwp,
            selector=candidate.selector,
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            window_handle=candidate.window_handle,
            prepared=prepared,
            guard=guard,
        )

    run_layout_mutation(unsafe_selectors, candidate.selector, mutate_table)
    guard()
    if box.result is None:
        raise HwpLiveError("한컴 표 그림 삽입 결과를 확인하지 못했습니다")
    return box.result

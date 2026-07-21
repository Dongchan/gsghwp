from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_batch_contract import NativeBatchResult
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_safety import require_writable_document, run_layout_mutation
from hwp_live_structure_contract import (
    DocumentStructure,
    TableCellUpdate,
    TableImageUpdate,
)
from hwp_live_table_image import (
    PreparedTableImages,
    apply_native_table_image_batch,
    prepare_table_images,
)
from hwp_live_table_update import (
    PreparedTableUpdate,
    apply_native_table_update_batch,
    prepare_table_update,
)


@dataclass(frozen=True, slots=True)
class PendingTableUpdate:
    table_ref: str
    state_token: str
    updates: tuple[TableCellUpdate, ...]
    snapshot: DocumentStructure


@dataclass(frozen=True, slots=True)
class PendingTableImages:
    table_ref: str
    state_token: str
    images: tuple[TableImageUpdate, ...]
    snapshot: DocumentStructure


@dataclass(slots=True)
class _NativeBatchBox:
    result: NativeBatchResult | None = None


def update_validated_table_batch(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    pending: tuple[PendingTableUpdate, ...],
    unsafe_selectors: set[str],
    guard: Callable[[], None],
) -> NativeBatchResult:
    require_writable_document(unsafe_selectors, candidate.selector)
    guard()
    prepared: tuple[PreparedTableUpdate, ...] = tuple(
        prepare_table_update(
            hwp,
            selector=candidate.selector,
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            window_handle=candidate.window_handle,
            requested_ref=item.table_ref,
            state_token=item.state_token,
            updates=item.updates,
            cached_snapshot=item.snapshot,
            guard=guard,
        )
        for item in pending
    )
    box = _NativeBatchBox()

    def mutate_tables() -> None:
        box.result = apply_native_table_update_batch(
            hwp,
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            window_handle=candidate.window_handle,
            prepared=prepared,
            guard=guard,
        )

    run_layout_mutation(unsafe_selectors, candidate.selector, mutate_tables)
    guard()
    if box.result is None:
        raise HwpLiveError("한컴 다중 표 네이티브 배치 결과를 확인하지 못했습니다")
    return box.result


def insert_validated_table_image_batch(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    pending: tuple[PendingTableImages, ...],
    unsafe_selectors: set[str],
    guard: Callable[[], None],
) -> NativeBatchResult:
    require_writable_document(unsafe_selectors, candidate.selector)
    guard()
    prepared: tuple[PreparedTableImages, ...] = tuple(
        prepare_table_images(
            hwp,
            selector=candidate.selector,
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            window_handle=candidate.window_handle,
            requested_ref=item.table_ref,
            state_token=item.state_token,
            images=item.images,
            cached_snapshot=item.snapshot,
            guard=guard,
        )
        for item in pending
    )
    box = _NativeBatchBox()

    def mutate_tables() -> None:
        box.result = apply_native_table_image_batch(
            hwp,
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            window_handle=candidate.window_handle,
            prepared=prepared,
            guard=guard,
        )

    run_layout_mutation(unsafe_selectors, candidate.selector, mutate_tables)
    guard()
    if box.result is None:
        raise HwpLiveError("한컴 다중 그림 네이티브 배치 결과를 확인하지 못했습니다")
    return box.result

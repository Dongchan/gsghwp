from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_batch import execute_native_batch, read_native_snapshot
from hwp_live_native_batch_contract import (
    NativeBatchRequest,
    NativeBatchResult,
    NativeImageCell,
    NativeTableBatch,
)
from hwp_live_native_table_snapshot import refresh_native_table_snapshot
from hwp_live_structure_contract import (
    DocumentStructure,
    StructureTable,
    TableCellUpdate,
    TableImageResult,
    TableImageUpdate,
)
from hwp_live_table_update import prepare_table_update
from hwp_trusted_paths import input_local_image


@dataclass(frozen=True, slots=True)
class PreparedTableImages:
    table: StructureTable
    images: tuple[TableImageUpdate, ...]
    paths: tuple[Path, ...]
    snapshot: DocumentStructure


def _native_control_id(table: StructureTable) -> str:
    if table.control_instance_id is None:
        raise HwpLiveError("네이티브 표 제어 식별자가 없습니다. 상세 구조를 다시 조회하세요")
    return table.control_instance_id


def apply_native_table_image_batch(
    hwp: LiveHwpApplication,
    *,
    document_id: int,
    full_name: str,
    window_handle: int,
    prepared: tuple[PreparedTableImages, ...],
    guard: Callable[[], None],
) -> NativeBatchResult:
    if not prepared:
        raise HwpLiveError("네이티브로 처리할 한컴 표 그림 배치가 없습니다")
    _ = (hwp, guard)
    tables = tuple(
        NativeTableBatch(
            control_id=_native_control_id(item.table),
            operations=tuple(
                NativeImageCell(
                    address=image.address,
                    expected_text=image.expected_text,
                    path=path,
                )
                for image, path in zip(item.images, item.paths, strict=True)
            ),
        )
        for item in prepared
    )
    result = execute_native_batch(
        window_handle,
        NativeBatchRequest(
            document_id=document_id,
            full_name=full_name,
            tables=tables,
        ),
    )
    if result is None:
        raise HwpLiveError("한컴 네이티브 배치 DLL 적용 후 한/글을 한 번 다시 실행해야 합니다")
    expected_count = sum(len(item.images) for item in prepared)
    if result.text_updates != 0 or result.image_updates != expected_count:
        raise HwpLiveError("한컴 네이티브 다중 그림 배치 처리 건수가 요청과 다릅니다")
    return result


def _table(snapshot: DocumentStructure, requested_ref: str) -> StructureTable:
    table = next((item for item in snapshot.tables if item.table_ref == requested_ref), None)
    if table is None:
        raise HwpLiveError("구조 스냅샷에서 지정한 표 참조값을 찾지 못했습니다")
    return table


def prepare_table_images(
    hwp: LiveHwpApplication,
    *,
    selector: str,
    document_id: int,
    full_name: str,
    window_handle: int,
    requested_ref: str,
    state_token: str,
    images: tuple[TableImageUpdate, ...],
    cached_snapshot: DocumentStructure | None,
    guard: Callable[[], None],
) -> PreparedTableImages:
    if not images:
        raise HwpLiveError("삽입할 한컴 표 그림이 없습니다")
    text_checks = tuple(
        TableCellUpdate(
            address=image.address,
            expected_text=image.expected_text,
            replacement=image.expected_text,
        )
        for image in images
    )
    prepared = prepare_table_update(
        hwp,
        selector=selector,
        document_id=document_id,
        full_name=full_name,
        window_handle=window_handle,
        requested_ref=requested_ref,
        state_token=state_token,
        updates=text_checks,
        cached_snapshot=cached_snapshot,
        guard=guard,
    )
    return PreparedTableImages(
        table=prepared.table,
        images=images,
        paths=tuple(input_local_image(image.path) for image in images),
        snapshot=prepared.snapshot,
    )


def apply_table_images(
    hwp: LiveHwpApplication,
    *,
    selector: str,
    document_id: int,
    full_name: str,
    window_handle: int,
    prepared: PreparedTableImages,
    guard: Callable[[], None],
) -> TableImageResult:
    requested_ref = prepared.table.table_ref
    _ = (hwp, selector, guard)
    native_result = execute_native_batch(
        window_handle,
        NativeBatchRequest(
            document_id=document_id,
            full_name=full_name,
            tables=(
                NativeTableBatch(
                    control_id=_native_control_id(prepared.table),
                    operations=tuple(
                        NativeImageCell(
                            address=image.address,
                            expected_text=image.expected_text,
                            path=path,
                        )
                        for image, path in zip(prepared.images, prepared.paths, strict=True)
                    ),
                ),
            ),
        ),
    )
    if native_result is None:
        raise HwpLiveError("한컴 네이티브 배치 DLL 적용 후 한/글을 한 번 다시 실행해야 합니다")
    if native_result.text_updates != 0 or native_result.image_updates != len(prepared.images):
        raise HwpLiveError("한컴 네이티브 그림 배치 처리 건수가 요청과 다릅니다")

    native_snapshot = refresh_native_table_snapshot(prepared.snapshot, prepared.table)
    if native_snapshot is None:
        raise HwpLiveError("한컴 네이티브 그림 삽입 결과를 다시 조회하지 못했습니다")
    after_snapshot, after = native_snapshot
    cells = {cell.address: cell for cell in after.cells}
    for image in prepared.images:
        cell = cells.get(image.address)
        if cell is None or not cell.has_picture:
            raise HwpLiveError(f"{image.address} 셀에 삽입한 그림을 다시 확인하지 못했습니다")
    state = read_native_snapshot(window_handle)
    if state is None:
        raise HwpLiveError("한컴 네이티브 그림 삽입 후 문서 상태를 읽지 못했습니다")
    return TableImageResult(
        table_ref=requested_ref,
        state_token=after_snapshot.state_token,
        current_page=state.current_page,
        modified=state.modified,
        inserted_addresses=tuple(image.address for image in prepared.images),
        inserted_paths=prepared.paths,
        native_elapsed_microseconds=native_result.elapsed_microseconds,
        table=after,
    )

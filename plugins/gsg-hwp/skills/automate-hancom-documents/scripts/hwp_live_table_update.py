from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_batch import execute_native_batch, read_native_snapshot
from hwp_live_native_batch_contract import (
    NativeBatchRequest,
    NativeBatchResult,
    NativeTableBatch,
    NativeTextCell,
)
from hwp_live_native_table_snapshot import refresh_native_table_snapshot
from hwp_live_structure_contract import (
    DocumentStructure,
    StructureTable,
    TableCellUpdate,
    TableUpdateResult,
)
from hwp_live_structure_identity import is_matching_structure_snapshot


@dataclass(frozen=True, slots=True)
class PreparedTableUpdate:
    snapshot: DocumentStructure
    table: StructureTable
    updates: tuple[TableCellUpdate, ...]


def apply_native_table_update_batch(
    hwp: LiveHwpApplication,
    *,
    document_id: int,
    full_name: str,
    window_handle: int,
    prepared: tuple[PreparedTableUpdate, ...],
    guard: Callable[[], None],
) -> NativeBatchResult:
    if not prepared:
        raise HwpLiveError("네이티브로 처리할 한컴 표 배치가 없습니다")
    _ = (hwp, guard)
    native_tables = tuple(
        NativeTableBatch(
            control_id=_native_control_id(item.table),
            operations=tuple(
                NativeTextCell(
                    address=update.address,
                    expected_text=update.expected_text,
                    replacement=update.replacement,
                )
                for update in item.updates
            ),
        )
        for item in prepared
    )
    result = execute_native_batch(
        window_handle,
        NativeBatchRequest(
            document_id=document_id,
            full_name=full_name,
            tables=native_tables,
        ),
    )
    if result is None:
        raise HwpLiveError("한컴 네이티브 배치 DLL 적용 후 한/글을 한 번 다시 실행해야 합니다")
    expected_count = sum(len(item.updates) for item in prepared)
    if result.text_updates != expected_count or result.image_updates != 0:
        raise HwpLiveError("한컴 네이티브 다중 표 배치 처리 건수가 요청과 다릅니다")
    return result


def _table(snapshot: DocumentStructure, requested_ref: str) -> StructureTable:
    table = next(
        (item for item in snapshot.tables if item.table_ref == requested_ref),
        None,
    )
    if table is None:
        raise HwpLiveError("구조 스냅샷에서 지정한 표 참조값을 찾지 못했습니다")
    return table


def _native_control_id(table: StructureTable) -> str:
    if table.control_instance_id is None:
        raise HwpLiveError("네이티브 표 제어 식별자가 없습니다. 상세 구조를 다시 조회하세요")
    return table.control_instance_id


def _preflight(table: StructureTable, updates: tuple[TableCellUpdate, ...]) -> None:
    if not updates:
        raise HwpLiveError("수정할 한컴 표 셀이 없습니다")
    addresses = tuple(update.address for update in updates)
    if len(addresses) != len(set(addresses)):
        raise HwpLiveError("한 번의 요청에서 같은 표 셀을 두 번 수정할 수 없습니다")
    cells = {cell.address: cell for cell in table.cells}
    for update in updates:
        cell = cells.get(update.address)
        if cell is None:
            raise HwpLiveError(f"한컴 표에 {update.address} 셀이 없습니다")
        if cell.owner_address != cell.address:
            raise HwpLiveError(
                f"{update.address} 셀은 병합된 {cell.owner_address} 셀로만 수정할 수 있습니다"
            )
        if cell.has_picture or cell.has_nested_table:
            raise HwpLiveError(f"{update.address} 셀에 개체가 있어 텍스트 전체 교체를 중단했습니다")
        if cell.text != update.expected_text:
            raise HwpLiveError(f"{update.address} 셀 내용이 조회한 내용과 다릅니다")


def prepare_table_update(
    hwp: LiveHwpApplication,
    *,
    selector: str,
    document_id: int,
    full_name: str,
    window_handle: int,
    requested_ref: str,
    state_token: str,
    updates: tuple[TableCellUpdate, ...],
    cached_snapshot: DocumentStructure | None,
    guard: Callable[[], None],
) -> PreparedTableUpdate:
    _ = (hwp, guard)
    if cached_snapshot is None or not is_matching_structure_snapshot(
        cached_snapshot, selector, document_id, full_name, window_handle, state_token
    ):
        raise HwpLiveError("상세 구조 스냅샷이 없거나 바뀌었습니다. 표를 다시 조회하세요")
    before = _table(cached_snapshot, requested_ref)
    _ = _native_control_id(before)
    native_snapshot = refresh_native_table_snapshot(cached_snapshot, before)
    if native_snapshot is None:
        raise HwpLiveError("한컴 네이티브 상세 구조 조회를 사용할 수 없습니다")
    live_snapshot, live_table = native_snapshot
    if live_snapshot.state_token != state_token or live_table != before:
        raise HwpLiveError("한컴 문서 구조가 조회 이후 바뀌어 표 수정을 중단했습니다")
    _preflight(before, updates)
    return PreparedTableUpdate(snapshot=cached_snapshot, table=before, updates=updates)


def apply_table_update(
    hwp: LiveHwpApplication,
    *,
    selector: str,
    document_id: int,
    full_name: str,
    window_handle: int,
    prepared: PreparedTableUpdate,
    guard: Callable[[], None],
) -> TableUpdateResult:
    requested_ref = prepared.table.table_ref
    _ = (hwp, selector, guard)
    batch = NativeBatchRequest(
        document_id=document_id,
        full_name=full_name,
        tables=(
            NativeTableBatch(
                control_id=_native_control_id(prepared.table),
                operations=tuple(
                    NativeTextCell(
                        address=update.address,
                        expected_text=update.expected_text,
                        replacement=update.replacement,
                    )
                    for update in prepared.updates
                ),
            ),
        ),
    )
    native_result = execute_native_batch(window_handle, batch)
    if native_result is None:
        raise HwpLiveError("한컴 네이티브 배치 DLL 적용 후 한/글을 한 번 다시 실행해야 합니다")
    if (
        native_result.text_updates != len(prepared.updates)
        or native_result.image_updates != 0
    ):
        raise HwpLiveError("한컴 네이티브 표 배치 처리 건수가 요청과 다릅니다")
    native_snapshot = refresh_native_table_snapshot(prepared.snapshot, prepared.table)
    if native_snapshot is None:
        raise HwpLiveError("한컴 네이티브 표 수정 결과를 다시 조회하지 못했습니다")
    after_snapshot, after = native_snapshot
    cells = {cell.address: cell for cell in after.cells}
    for update in prepared.updates:
        cell = cells.get(update.address)
        if cell is None or cell.text != update.replacement:
            raise HwpLiveError(f"{update.address} 셀의 네이티브 입력 결과가 요청과 다릅니다")
    state = read_native_snapshot(window_handle)
    if state is None:
        raise HwpLiveError("한컴 네이티브 표 수정 후 문서 상태를 읽지 못했습니다")
    return TableUpdateResult(
        table_ref=requested_ref,
        state_token=after_snapshot.state_token,
        current_page=state.current_page,
        modified=state.modified,
        updated_addresses=tuple(update.address for update in prepared.updates),
        table=after,
        native_elapsed_microseconds=native_result.elapsed_microseconds,
    )

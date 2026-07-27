from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Protocol

from hwp_errors import HwpLiveError
from hwp_live_document_edit_commands import build_delete_control_commands
from hwp_live_edit_history import (
    DocumentCheckpoint,
    DocumentEditHistoryEntry,
    HistoryDirection,
    HistoryOperation,
    LiveEditHistoryEntry,
    LiveEditHistoryStore,
    NativeDocumentEditHistoryEntry,
    PageControlSnapshot,
    build_capture_document_commands,
)
from hwp_live_native_action_models import (
    DeleteControlCommand,
    NativeActionCommand,
    NativeActionRequest,
    NativePageControl,
    RestoreDocumentFileCommand,
)
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_page,
    read_native_snapshot,
)
from hwp_live_native_history import execute_native_history
from hwp_live_rot import HwpDocumentCandidate


_MAX_CHECKPOINT_SOURCE_FILE_BYTES = 192 * 1024 * 1024


class WindowHandleCandidate(Protocol):
    @property
    def window_handle(self) -> int: ...


@dataclass(frozen=True, slots=True)
class PreparedDocumentEdit:
    document_id: int
    full_name: str
    operation: HistoryOperation
    before: DocumentCheckpoint
    after_path: Path
    page: int
    before_controls: tuple[PageControlSnapshot, ...]
    target_control_ids: tuple[str, ...]
    capture_elapsed_microseconds: int


@dataclass(frozen=True, slots=True)
class LiveEditHistoryExecution:
    commands_executed: int
    elapsed_microseconds: int
    custom_history: bool


def should_use_document_checkpoint(full_name: str) -> bool:
    try:
        return Path(full_name).stat().st_size <= _MAX_CHECKPOINT_SOURCE_FILE_BYTES
    except OSError:
        return True


def _control_state(
    controls: tuple[NativePageControl, ...],
) -> tuple[PageControlSnapshot, ...]:
    return tuple(
        PageControlSnapshot(
            instance_id=control.instance_id,
            control_type=control.control_type,
            rows=control.rows,
            columns=control.columns,
        )
        for control in controls
    )


def _remove_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _capture_checkpoint(
    candidate: HwpDocumentCandidate,
    document_id: int,
    full_name: str,
    path: Path,
    page_count: int,
) -> tuple[DocumentCheckpoint, int]:
    native = execute_native_actions(
        candidate.window_handle,
        NativeActionRequest(
            document_id,
            full_name,
            build_capture_document_commands(path),
        ),
        minimum_version=9,
    )
    if native is None:
        raise HwpLiveError("한컴 프로토콜 9 문서 체크포인트 저장기를 사용할 수 없습니다")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise HwpLiveError("문서 체크포인트 파일을 확인하지 못했습니다") from error
    if size < 1:
        raise HwpLiveError("문서 체크포인트 파일이 비어 있습니다")
    return DocumentCheckpoint(path, size, page_count), native.elapsed_microseconds


def _restore_checkpoint(
    candidate: HwpDocumentCandidate,
    document_id: int,
    full_name: str,
    checkpoint: DocumentCheckpoint,
) -> tuple[int, int]:
    try:
        actual_size = checkpoint.path.stat().st_size
    except OSError as error:
        raise HwpLiveError("복구할 문서 체크포인트 파일을 확인하지 못했습니다") from error
    if actual_size != checkpoint.bytes:
        raise HwpLiveError("복구할 문서 체크포인트 파일 크기가 변경되었습니다")
    native = execute_native_actions(
        candidate.window_handle,
        NativeActionRequest(
            document_id,
            full_name,
            (
                RestoreDocumentFileCommand(
                    checkpoint.path,
                    checkpoint.page_count,
                ),
            ),
        ),
        minimum_version=12,
    )
    if native is None:
        raise HwpLiveError("한컴 네이티브 문서 체크포인트 복원기를 사용할 수 없습니다")
    snapshot = read_native_snapshot(candidate.window_handle)
    if snapshot is None:
        raise HwpLiveError("체크포인트 적용 후 네이티브 문서 상태를 읽지 못했습니다")
    expected_key = document_id, os.path.normcase(os.path.abspath(full_name))
    actual_key = (
        snapshot.document_id,
        os.path.normcase(os.path.abspath(snapshot.full_name)),
    )
    if actual_key != expected_key or snapshot.page_count != checkpoint.page_count:
        raise HwpLiveError("체크포인트 적용 후 문서 식별값 또는 페이지 수가 바뀌었습니다")
    return native.commands_executed, native.elapsed_microseconds


def prepare_control_deletion(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    document_id: int,
    full_name: str,
    page: int,
    page_count: int,
    target_controls: tuple[NativePageControl, ...],
) -> PreparedDocumentEdit:
    detailed_page = inspect_native_page(
        candidate.window_handle,
        page,
        include_cells=True,
    )
    if detailed_page is None:
        raise HwpLiveError("개체 삭제 전 대상 쪽의 상세 구조를 읽지 못했습니다")
    before_path = history.new_checkpoint_path()
    after_path = history.new_checkpoint_path()
    try:
        before, elapsed = _capture_checkpoint(
            candidate,
            document_id,
            full_name,
            before_path,
            page_count,
        )
    except (HwpLiveError, OSError, ValueError):
        _remove_path(before_path)
        _remove_path(after_path)
        raise
    return PreparedDocumentEdit(
        document_id=document_id,
        full_name=full_name,
        operation="control.delete",
        before=before,
        after_path=after_path,
        page=page,
        before_controls=_control_state(detailed_page.controls),
        target_control_ids=tuple(control.instance_id for control in target_controls),
        capture_elapsed_microseconds=elapsed,
    )


def prepare_page_deletion(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    document_id: int,
    full_name: str,
    page: int,
    page_count: int,
) -> PreparedDocumentEdit:
    before_path = history.new_checkpoint_path()
    after_path = history.new_checkpoint_path()
    try:
        before, elapsed = _capture_checkpoint(
            candidate,
            document_id,
            full_name,
            before_path,
            page_count,
        )
    except (HwpLiveError, OSError, ValueError):
        _remove_path(before_path)
        _remove_path(after_path)
        raise
    return PreparedDocumentEdit(
        document_id=document_id,
        full_name=full_name,
        operation="document.delete_page",
        before=before,
        after_path=after_path,
        page=page,
        before_controls=(),
        target_control_ids=(),
        capture_elapsed_microseconds=elapsed,
    )


def _record_completed_edit(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    prepared: PreparedDocumentEdit,
) -> tuple[DocumentEditHistoryEntry, int]:
    snapshot = read_native_snapshot(candidate.window_handle)
    if snapshot is None:
        raise HwpLiveError("편집 후 문서 상태를 읽지 못했습니다")
    after, elapsed = _capture_checkpoint(
        candidate,
        prepared.document_id,
        prepared.full_name,
        prepared.after_path,
        snapshot.page_count,
    )
    after_controls: tuple[PageControlSnapshot, ...] = ()
    if prepared.operation == "control.delete":
        inspected = inspect_native_page(
            candidate.window_handle,
            prepared.page,
            include_cells=True,
        )
        if inspected is None:
            raise HwpLiveError("개체 삭제 후 쪽 구조를 읽지 못했습니다")
        after_controls = _control_state(inspected.controls)
    entry = DocumentEditHistoryEntry(
        document_id=prepared.document_id,
        full_name=prepared.full_name,
        operation=prepared.operation,
        before=prepared.before,
        after=after,
        page=prepared.page,
        before_controls=prepared.before_controls,
        after_controls=after_controls,
    )
    history.record(entry)
    return entry, elapsed


def _restore_prepared_before_failure(
    candidate: HwpDocumentCandidate,
    prepared: PreparedDocumentEdit,
) -> None:
    _ = _restore_checkpoint(
        candidate,
        prepared.document_id,
        prepared.full_name,
        prepared.before,
    )
    _remove_path(prepared.before.path)
    _remove_path(prepared.after_path)


def execute_prepared_control_deletion(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    prepared: PreparedDocumentEdit,
) -> LiveEditHistoryExecution:
    commands = tuple(
        DeleteControlCommand(instance_id)
        for instance_id in prepared.target_control_ids
    )
    try:
        native = execute_native_actions(
            candidate.window_handle,
            NativeActionRequest(prepared.document_id, prepared.full_name, commands),
            minimum_version=9,
        )
        if native is None:
            raise HwpLiveError("한컴 프로토콜 9 기존 개체 삭제기를 사용할 수 없습니다")
        _, after_capture_elapsed = _record_completed_edit(
            candidate,
            history,
            prepared,
        )
    except (HwpLiveError, OSError, ValueError):
        _restore_prepared_before_failure(candidate, prepared)
        raise
    return LiveEditHistoryExecution(
        commands_executed=native.commands_executed,
        elapsed_microseconds=(
            prepared.capture_elapsed_microseconds
            + native.elapsed_microseconds
            + after_capture_elapsed
        ),
        custom_history=True,
    )


def execute_prepared_page_deletion(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    prepared: PreparedDocumentEdit,
    commands: tuple[NativeActionCommand, ...],
) -> LiveEditHistoryExecution:
    try:
        native = execute_native_actions(
            candidate.window_handle,
            NativeActionRequest(prepared.document_id, prepared.full_name, commands),
            minimum_version=9,
        )
        if native is None:
            raise HwpLiveError("한컴 프로토콜 9 쪽 삭제기를 사용할 수 없습니다")
        _, after_capture_elapsed = _record_completed_edit(
            candidate,
            history,
            prepared,
        )
    except (HwpLiveError, OSError, ValueError):
        _restore_prepared_before_failure(candidate, prepared)
        raise
    return LiveEditHistoryExecution(
        commands_executed=native.commands_executed,
        elapsed_microseconds=(
            prepared.capture_elapsed_microseconds
            + native.elapsed_microseconds
            + after_capture_elapsed
        ),
        custom_history=True,
    )


def _matches_expected_state(
    candidate: WindowHandleCandidate,
    *,
    page_count: int,
    page: int | None = None,
    controls: tuple[PageControlSnapshot, ...] | None = None,
) -> bool:
    snapshot = read_native_snapshot(candidate.window_handle)
    if snapshot is None or snapshot.page_count != page_count:
        return False
    if page is None or controls is None:
        return True
    inspected = inspect_native_page(candidate.window_handle, page, include_cells=True)
    if inspected is None:
        return False
    actual = _control_state(inspected.controls)
    return Counter(item.signature for item in actual) == Counter(
        item.signature for item in controls
    )


def _execute_native_history_until_state(
    candidate: WindowHandleCandidate,
    direction: HistoryDirection,
    maximum_steps: int,
    *,
    page_count: int,
    page: int | None = None,
    controls: tuple[PageControlSnapshot, ...] | None = None,
) -> tuple[int, int]:
    if _matches_expected_state(
        candidate,
        page_count=page_count,
        page=page,
        controls=controls,
    ):
        return 0, 0
    elapsed = 0
    for step in range(1, maximum_steps + 1):
        result = execute_native_history(candidate.window_handle, direction, 1)
        elapsed += result.elapsed_microseconds
        if _matches_expected_state(
            candidate,
            page_count=page_count,
            page=page,
            controls=controls,
        ):
            return step, elapsed
    raise HwpLiveError(
        f"한컴 {direction}를 {maximum_steps}단계 실행했지만 MCP 작업 경계 상태가 복구되지 않았습니다"
    )


def execute_grouped_native_control_deletion(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    document_id: int,
    full_name: str,
    page: int,
    page_count: int,
    target_controls: tuple[NativePageControl, ...],
) -> LiveEditHistoryExecution:
    inspected_before = inspect_native_page(candidate.window_handle, page, include_cells=True)
    if inspected_before is None:
        raise HwpLiveError("개체 삭제 전 대상 쪽의 상세 구조를 읽지 못했습니다")
    before_controls = _control_state(inspected_before.controls)
    commands = build_delete_control_commands(target_controls)
    native_elapsed = 0
    commands_executed = 0
    try:
        native = execute_native_actions(
            candidate.window_handle,
            NativeActionRequest(document_id, full_name, commands),
            minimum_version=9,
        )
        if native is None:
            raise HwpLiveError("한컴 프로토콜 9 기존 개체 삭제기를 사용할 수 없습니다")
        commands_executed = native.commands_executed
        native_elapsed = native.elapsed_microseconds
        snapshot_after = read_native_snapshot(candidate.window_handle)
        inspected_after = inspect_native_page(candidate.window_handle, page, include_cells=True)
        if snapshot_after is None or inspected_after is None:
            raise HwpLiveError("개체 삭제 후 문서 구조를 읽지 못했습니다")
        entry = NativeDocumentEditHistoryEntry(
            document_id=document_id,
            full_name=full_name,
            operation="control.delete",
            maximum_native_steps=len(commands),
            before_page_count=page_count,
            after_page_count=snapshot_after.page_count,
            page=page,
            before_controls=before_controls,
            after_controls=_control_state(inspected_after.controls),
        )
        history.record(entry)
    except (HwpLiveError, OSError, ValueError):
        if commands_executed:
            try:
                _ = _execute_native_history_until_state(
                    candidate,
                    "undo",
                    commands_executed,
                    page_count=page_count,
                    page=page,
                    controls=before_controls,
                )
            except HwpLiveError as restore_error:
                raise HwpLiveError(
                    "대용량 문서 개체 삭제 실패 후 한컴 이력 복구도 검증하지 못했습니다"
                ) from restore_error
        raise
    return LiveEditHistoryExecution(commands_executed, native_elapsed, False)


def execute_grouped_native_page_deletion(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    document_id: int,
    full_name: str,
    page: int,
    page_count: int,
    commands: tuple[NativeActionCommand, ...],
) -> LiveEditHistoryExecution:
    native_elapsed = 0
    commands_executed = 0
    try:
        native = execute_native_actions(
            candidate.window_handle,
            NativeActionRequest(document_id, full_name, commands),
            minimum_version=9,
        )
        if native is None:
            raise HwpLiveError("한컴 프로토콜 9 쪽 삭제기를 사용할 수 없습니다")
        commands_executed = native.commands_executed
        native_elapsed = native.elapsed_microseconds
        snapshot_after = read_native_snapshot(candidate.window_handle)
        if snapshot_after is None or snapshot_after.page_count != page_count - 1:
            raise HwpLiveError("쪽 삭제 후 페이지 수가 정확히 하나 줄지 않았습니다")
        history.record(
            NativeDocumentEditHistoryEntry(
                document_id=document_id,
                full_name=full_name,
                operation="document.delete_page",
                maximum_native_steps=len(commands),
                before_page_count=page_count,
                after_page_count=snapshot_after.page_count,
                page=page,
            )
        )
    except (HwpLiveError, OSError, ValueError):
        if commands_executed:
            try:
                _ = _execute_native_history_until_state(
                    candidate,
                    "undo",
                    commands_executed,
                    page_count=page_count,
                )
            except HwpLiveError as restore_error:
                raise HwpLiveError(
                    "대용량 문서 쪽 삭제 실패 후 한컴 이력 복구도 검증하지 못했습니다"
                ) from restore_error
        raise
    return LiveEditHistoryExecution(commands_executed, native_elapsed, False)


def verify_history_entry_state(
    candidate: HwpDocumentCandidate,
    entry: LiveEditHistoryEntry,
    direction: HistoryDirection,
) -> None:
    if isinstance(entry, NativeDocumentEditHistoryEntry):
        page_count = (
            entry.before_page_count if direction == "undo" else entry.after_page_count
        )
    else:
        checkpoint = entry.before if direction == "undo" else entry.after
        page_count = checkpoint.page_count
    controls = None
    if entry.operation == "control.delete":
        controls = entry.before_controls if direction == "undo" else entry.after_controls
    if not _matches_expected_state(
        candidate,
        page_count=page_count,
        page=entry.page,
        controls=controls,
    ):
        raise HwpLiveError("복구한 문서의 페이지 수 또는 대상 개체 구조가 이력과 다릅니다")


def execute_document_edit_history(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    document_id: int,
    full_name: str,
    direction: HistoryDirection,
    steps: int,
) -> LiveEditHistoryExecution | None:
    available = history.available(direction, document_id, full_name)
    if available == 0:
        return None
    if steps > available:
        raise HwpLiveError(
            f"현재 문서의 MCP 편집 {direction} 이력은 {available}단계입니다; "
            + "기본 한컴 이력과 한 호출에서 혼합 실행할 수 없습니다"
        )
    commands = 0
    elapsed = 0
    checkpoint_history = True
    for _ in range(steps):
        entry = history.peek(direction, document_id, full_name)
        if entry is None:
            raise HwpLiveError("문서 편집 이력이 실행 중 소진되었습니다")
        if isinstance(entry, NativeDocumentEditHistoryEntry):
            checkpoint_history = False
            target_page_count = (
                entry.before_page_count if direction == "undo" else entry.after_page_count
            )
            target_controls = None
            if entry.operation == "control.delete":
                target_controls = (
                    entry.before_controls if direction == "undo" else entry.after_controls
                )
            executed, duration = _execute_native_history_until_state(
                candidate,
                direction,
                entry.maximum_native_steps,
                page_count=target_page_count,
                page=entry.page,
                controls=target_controls,
            )
        else:
            checkpoint = entry.before if direction == "undo" else entry.after
            executed, duration = _restore_checkpoint(
                candidate,
                entry.document_id,
                entry.full_name,
                checkpoint,
            )
        verify_history_entry_state(candidate, entry, direction)
        history.commit(direction, entry)
        verify_history_entry_state(candidate, entry, direction)
        commands += executed
        elapsed += duration
    return LiveEditHistoryExecution(commands, elapsed, checkpoint_history)

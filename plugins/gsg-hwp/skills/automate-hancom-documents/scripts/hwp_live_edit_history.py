from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    NativeActionCommand,
    SaveDocumentFileCommand,
)


type HistoryDirection = Literal["undo", "redo"]
type HistoryOperation = Literal["control.delete", "document.delete_page"]


@dataclass(frozen=True, slots=True)
class DocumentCheckpoint:
    path: Path
    bytes: int
    page_count: int


@dataclass(frozen=True, slots=True)
class PageControlSnapshot:
    instance_id: str
    control_type: str
    rows: int | None
    columns: int | None

    @property
    def signature(self) -> tuple[str, int | None, int | None]:
        return self.control_type, self.rows, self.columns


@dataclass(frozen=True, slots=True)
class DocumentEditHistoryEntry:
    document_id: int
    full_name: str
    operation: HistoryOperation
    before: DocumentCheckpoint
    after: DocumentCheckpoint
    page: int | None = None
    before_controls: tuple[PageControlSnapshot, ...] = ()
    after_controls: tuple[PageControlSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeDocumentEditHistoryEntry:
    document_id: int
    full_name: str
    operation: HistoryOperation
    maximum_native_steps: int
    before_page_count: int
    after_page_count: int
    page: int | None = None
    before_controls: tuple[PageControlSnapshot, ...] = ()
    after_controls: tuple[PageControlSnapshot, ...] = ()


type LiveEditHistoryEntry = DocumentEditHistoryEntry | NativeDocumentEditHistoryEntry


def _document_key(document_id: int, full_name: str) -> tuple[int, str]:
    return document_id, os.path.normcase(os.path.abspath(full_name))


def _entry_bytes(entry: LiveEditHistoryEntry) -> int:
    if isinstance(entry, NativeDocumentEditHistoryEntry):
        return 0
    return entry.before.bytes + entry.after.bytes


def _entry_paths(entry: LiveEditHistoryEntry) -> tuple[Path, ...]:
    if isinstance(entry, NativeDocumentEditHistoryEntry):
        return ()
    return entry.before.path, entry.after.path


def build_capture_document_commands(
    checkpoint_path: Path,
) -> tuple[NativeActionCommand, ...]:
    if not checkpoint_path.is_absolute():
        raise ValueError("document checkpoint path must be absolute")
    return (SaveDocumentFileCommand(checkpoint_path),)


class LiveEditHistoryStore:
    __slots__ = (
        "_max_bytes",
        "_max_entries",
        "_redo",
        "_temp_parent",
        "_temporary_directory",
        "_total_bytes",
        "_undo",
    )

    def __init__(
        self,
        *,
        temp_parent: Path | None = None,
        max_entries: int = 20,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if max_entries < 1 or max_bytes < 1:
            raise ValueError("history limits must be positive")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._temp_parent = temp_parent
        self._temporary_directory: TemporaryDirectory[str] | None = None
        self._undo: list[LiveEditHistoryEntry] = []
        self._redo: list[LiveEditHistoryEntry] = []
        self._total_bytes = 0

    def _root(self) -> Path:
        if self._temporary_directory is None:
            self._temporary_directory = TemporaryDirectory(
                prefix="gsg-hwp-live-history-",
                dir=self._temp_parent,
            )
        return Path(self._temporary_directory.name)

    def new_checkpoint_path(self) -> Path:
        return self._root() / f"document-{secrets.token_hex(12)}.hwp-checkpoint"

    @staticmethod
    def _matches(
        entry: LiveEditHistoryEntry,
        document_id: int,
        full_name: str,
    ) -> bool:
        return _document_key(entry.document_id, entry.full_name) == _document_key(
            document_id,
            full_name,
        )

    def available(
        self,
        direction: HistoryDirection,
        document_id: int,
        full_name: str,
    ) -> int:
        stack = self._undo if direction == "undo" else self._redo
        count = 0
        for entry in reversed(stack):
            if not self._matches(entry, document_id, full_name):
                break
            count += 1
        return count

    def peek(
        self,
        direction: HistoryDirection,
        document_id: int,
        full_name: str,
    ) -> LiveEditHistoryEntry | None:
        stack = self._undo if direction == "undo" else self._redo
        if not stack:
            return None
        entry = stack[-1]
        if not self._matches(entry, document_id, full_name):
            raise HwpLiveError("다른 문서의 편집 이력이 현재 세션에 남아 있습니다")
        return entry

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _drop_entry(self, entry: LiveEditHistoryEntry) -> None:
        for path in _entry_paths(entry):
            self._remove_file(path)
        self._total_bytes -= _entry_bytes(entry)

    def _clear_stack(self, stack: list[LiveEditHistoryEntry]) -> None:
        while stack:
            self._drop_entry(stack.pop())

    def validate_capacity(self, entry: LiveEditHistoryEntry) -> None:
        if isinstance(entry, NativeDocumentEditHistoryEntry):
            if entry.maximum_native_steps < 1 or entry.maximum_native_steps > 100:
                raise ValueError("grouped native history steps must be between 1 and 100")
            if entry.before_page_count < 1 or entry.after_page_count < 1:
                raise ValueError("grouped native history page counts must be positive")
            if entry.operation == "control.delete":
                if entry.page is None or not entry.before_controls:
                    raise ValueError("control delete history requires its page and controls")
            elif entry.page is None:
                raise ValueError("page delete history requires its deleted page")
            return
        if entry.before.page_count < 1 or entry.after.page_count < 1:
            raise ValueError("document checkpoint page counts must be positive")
        if entry.before.path == entry.after.path:
            raise ValueError("before and after checkpoints must use different files")
        if entry.operation == "control.delete":
            if entry.page is None or not entry.before_controls:
                raise ValueError("control delete history requires its page and controls")
            if len(entry.before_controls) > 100:
                raise HwpLiveError("한 번의 삭제 복구 대상은 최대 100개입니다")
        elif entry.page is None:
            raise ValueError("page delete history requires its deleted page")
        required = _entry_bytes(entry)
        if required > self._max_bytes:
            raise HwpLiveError("문서 체크포인트가 세션의 디스크 이력 한도를 초과했습니다")
        for checkpoint in (entry.before, entry.after):
            try:
                actual = checkpoint.path.stat().st_size
            except OSError as error:
                raise HwpLiveError("문서 체크포인트 파일을 확인하지 못했습니다") from error
            if actual < 1 or actual != checkpoint.bytes:
                raise HwpLiveError("문서 체크포인트 파일 크기가 일치하지 않습니다")

    def record(self, entry: LiveEditHistoryEntry) -> None:
        self.validate_capacity(entry)
        self._clear_stack(self._redo)
        self._undo.append(entry)
        self._total_bytes += _entry_bytes(entry)
        while len(self._undo) > self._max_entries or self._total_bytes > self._max_bytes:
            self._drop_entry(self._undo.pop(0))

    def commit(
        self,
        direction: HistoryDirection,
        entry: LiveEditHistoryEntry,
    ) -> None:
        source = self._undo if direction == "undo" else self._redo
        target = self._redo if direction == "undo" else self._undo
        if not source or source[-1] != entry:
            raise HwpLiveError("문서 편집 이력이 실행 중 변경되었습니다")
        _ = source.pop()
        target.append(entry)

    def discard(self, entry: LiveEditHistoryEntry) -> None:
        for path in _entry_paths(entry):
            self._remove_file(path)

    def cleanup(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._total_bytes = 0
        temporary_directory, self._temporary_directory = self._temporary_directory, None
        if temporary_directory is not None:
            temporary_directory.cleanup()

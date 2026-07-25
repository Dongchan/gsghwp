from __future__ import annotations

import hashlib
import msvcrt
import os
import secrets
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import final

from hwp_operation_journal_contract import JournalRecord
from hwp_operation_journal_retention import (
    JournalPruneReport,
    JournalRetentionPolicy,
    prune_terminal_records,
)


@final
class OperationJournalStore:
    __slots__ = ("_lock_path", "_root")

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._root / ".journal.lock"
        try:
            with self._lock_path.open("xb") as stream:
                _ = stream.write(b"\0")
        except FileExistsError:
            pass

    @contextmanager
    def transaction(self, *, blocking: bool = True) -> Generator[bool]:
        with self._lock_path.open("r+b", buffering=0) as stream:
            _ = stream.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(stream.fileno(), mode, 1)
            except OSError:
                if blocking:
                    raise
                yield False
                return
            try:
                yield True
            finally:
                _ = stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def entry_path(self, document_session: str, request_id: str) -> Path:
        name = hashlib.sha256(
            f"{document_session}\0{request_id}".encode("utf-8")
        ).hexdigest()
        return self._root / f"{name}.json"

    @staticmethod
    def create(path: Path, record: JournalRecord) -> None:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            _ = stream.write(record.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def replace(path: Path, record: JournalRecord) -> None:
        temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            OperationJournalStore.create(temporary, record)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def read(path: Path) -> JournalRecord:
        return JournalRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def prune(
        self,
        policy: JournalRetentionPolicy,
        now: datetime,
    ) -> JournalPruneReport:
        return prune_terminal_records(self._root, policy, now.timestamp())

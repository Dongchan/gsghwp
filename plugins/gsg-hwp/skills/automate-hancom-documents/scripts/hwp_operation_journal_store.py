from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path
from typing import final

from hwp_operation_journal_contract import JournalRecord


@final
class OperationJournalStore:
    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

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

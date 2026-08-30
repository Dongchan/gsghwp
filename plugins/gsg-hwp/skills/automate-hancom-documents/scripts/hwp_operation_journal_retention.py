from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from hwp_operation_journal_contract import JournalRecord


DEFAULT_TERMINAL_TTL: Final = timedelta(days=30)
DEFAULT_MAX_TERMINAL_FILES: Final = 10_000
DEFAULT_MAX_TERMINAL_BYTES: Final = 64 * 1024 * 1024
DEFAULT_CLEANUP_INTERVAL: Final = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class JournalRetentionPolicy:
    terminal_ttl: timedelta = DEFAULT_TERMINAL_TTL
    max_terminal_files: int = DEFAULT_MAX_TERMINAL_FILES
    max_terminal_bytes: int = DEFAULT_MAX_TERMINAL_BYTES
    cleanup_interval: timedelta = DEFAULT_CLEANUP_INTERVAL

    def __post_init__(self) -> None:
        if self.terminal_ttl <= timedelta(0):
            raise ValueError("terminal TTL must be positive")
        if self.max_terminal_files <= 0:
            raise ValueError("maximum terminal file count must be positive")
        if self.max_terminal_bytes <= 0:
            raise ValueError("maximum terminal byte count must be positive")
        if self.cleanup_interval <= timedelta(0):
            raise ValueError("cleanup interval must be positive")

    @property
    def cleanup_interval_seconds(self) -> float:
        return self.cleanup_interval.total_seconds()


DEFAULT_JOURNAL_RETENTION: Final = JournalRetentionPolicy()


@dataclass(frozen=True, slots=True)
class JournalPruneReport:
    lock_acquired: bool
    scanned_files: int
    removed_files: int
    removed_bytes: int
    protected_files: int
    invalid_files: int
    failed_deletions: int
    remaining_terminal_files: int
    remaining_terminal_bytes: int

    @classmethod
    def lock_busy(cls) -> JournalPruneReport:
        return cls(
            lock_acquired=False,
            scanned_files=0,
            removed_files=0,
            removed_bytes=0,
            protected_files=0,
            invalid_files=0,
            failed_deletions=0,
            remaining_terminal_files=0,
            remaining_terminal_bytes=0,
        )


@dataclass(frozen=True, slots=True)
class _TerminalEntry:
    path: Path
    updated_at_seconds: float
    size: int


def _is_protected(record: JournalRecord) -> bool:
    """아직 끝나지 않은 작업만 지우기에서 뺀다.

    지우면 안 되는 것은 "진행 중"이다. `accepted`/`executing` 은 종단 결과가
    아니라 살아 있는 작업의 자리표라서, 지우면 그 작업이 자기 기록을 잃는다.

    끝난 것은 전부 한도 안에서 늙어 사라진다 — 실패도, 수습이 필요한 실패도
    마찬가지다. 예전에는 `partial_mutation is True` 이거나 `failure_code` 에
    "reconcile_required" 가 든 레코드를 TTL(30일)·파일 수(10,000)·용량(64MiB)
    **어느 한도로도** 지우지 않았다. 그 보호를 푸는 경로는
    `record_save_reconciliation` 이 `failure_code=None` 으로 지우는 한 가지뿐이고
    (`mark_failed` 계열은 코드를 지우지 않는다), 그 경로를 타지 못한 레코드는
    디스크에 영원히 남았다. 두 조건은 실제로 같은 레코드에 함께 붙는다:
    `with_result` 가 `partial_mutation` 을 결과에서 그대로 받아 오고, 부분 변경을
    보고하는 작업은 `reconcile_required` 도 함께 세운다.

    공개 계약이 이미 이 답을 써 놓았다(SKILL.md): 완료 결과는 기본 30일까지
    재생 가능하고, 종단 저널이 10,000건이나 64MiB 를 넘으면 오래된 종단 결과부터
    쫓아낸다. 예외는 적혀 있지 않다. "아직 수습 안 된 최근 건"은 최근이라는
    이유로 이미 지켜진다 — TTL 을 넘지 않았고, 한도 축출은 오래된 것부터다.
    """
    return record.state in {"accepted", "executing"}


def _read_terminal_entry(path: Path) -> tuple[_TerminalEntry | None, bool]:
    try:
        size = path.stat().st_size
        record = JournalRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, False
    except (OSError, ValidationError):
        return None, True
    if _is_protected(record):
        return None, False
    return (
        _TerminalEntry(
            path=path,
            updated_at_seconds=record.updated_at.timestamp(),
            size=size,
        ),
        False,
    )


def prune_terminal_records(
    root: Path,
    policy: JournalRetentionPolicy,
    now_seconds: float,
) -> JournalPruneReport:
    scanned_files = 0
    protected_files = 0
    invalid_files = 0
    terminal_entries: list[_TerminalEntry] = []
    for path in root.glob("*.json"):
        scanned_files += 1
        entry, invalid = _read_terminal_entry(path)
        if invalid:
            invalid_files += 1
        elif entry is None:
            protected_files += 1
        else:
            terminal_entries.append(entry)

    ttl_cutoff = now_seconds - policy.terminal_ttl.total_seconds()
    expired = {
        entry.path
        for entry in terminal_entries
        if entry.updated_at_seconds <= ttl_cutoff
    }
    retained = [entry for entry in terminal_entries if entry.path not in expired]
    retained.sort(key=lambda entry: (entry.updated_at_seconds, entry.path.name))
    retained_bytes = sum(entry.size for entry in retained)
    remove_count = 0
    while (
        len(retained) - remove_count > policy.max_terminal_files
        or retained_bytes > policy.max_terminal_bytes
    ):
        entry = retained[remove_count]
        remove_count += 1
        retained_bytes -= entry.size
    over_limit = {entry.path for entry in retained[:remove_count]}

    removals = expired | over_limit
    removed_files = 0
    removed_bytes = 0
    failed_deletions = 0
    sizes = {entry.path: entry.size for entry in terminal_entries}
    for path in removals:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            failed_deletions += 1
        else:
            removed_files += 1
            removed_bytes += sizes[path]

    total_terminal_bytes = sum(entry.size for entry in terminal_entries)
    return JournalPruneReport(
        lock_acquired=True,
        scanned_files=scanned_files,
        removed_files=removed_files,
        removed_bytes=removed_bytes,
        protected_files=protected_files,
        invalid_files=invalid_files,
        failed_deletions=failed_deletions,
        remaining_terminal_files=len(terminal_entries) - removed_files,
        remaining_terminal_bytes=total_terminal_bytes - removed_bytes,
    )

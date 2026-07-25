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
    if record.state in {"accepted", "executing"}:
        return True
    if record.partial_mutation is True:
        return True
    return (
        record.failure_code is not None
        and "reconcile_required" in record.failure_code
    )


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

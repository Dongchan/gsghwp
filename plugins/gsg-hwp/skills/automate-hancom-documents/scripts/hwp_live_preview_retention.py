from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final, final, override


DEFAULT_COMPLETED_TTL: Final = timedelta(hours=24)
DEFAULT_RESULT_GRACE: Final = timedelta(minutes=15)
DEFAULT_MAX_TOTAL_BYTES: Final = 64 * 1024 * 1024
DEFAULT_MAX_SESSION_BYTES: Final = 16 * 1024 * 1024
DEFAULT_MAX_SESSION_FILES: Final = 16
DEFAULT_CLEANUP_INTERVAL: Final = timedelta(minutes=15)


@final
class PreviewRetentionConfigurationError(ValueError):
    __slots__: tuple[str, ...] = ("reason",)
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class PreviewRetentionPolicy:
    completed_ttl: timedelta = DEFAULT_COMPLETED_TTL
    result_grace: timedelta = DEFAULT_RESULT_GRACE
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_session_bytes: int = DEFAULT_MAX_SESSION_BYTES
    max_session_files: int = DEFAULT_MAX_SESSION_FILES
    cleanup_interval: timedelta = DEFAULT_CLEANUP_INTERVAL

    def __post_init__(self) -> None:
        if self.completed_ttl <= timedelta(0):
            raise PreviewRetentionConfigurationError(
                "completed preview TTL must be positive"
            )
        if self.result_grace <= timedelta(0):
            raise PreviewRetentionConfigurationError(
                "preview result grace must be positive"
            )
        if self.completed_ttl < self.result_grace:
            raise PreviewRetentionConfigurationError(
                "completed preview TTL must include the result grace"
            )
        if self.max_total_bytes <= 0:
            raise PreviewRetentionConfigurationError(
                "maximum preview byte count must be positive"
            )
        if self.max_session_bytes <= 0:
            raise PreviewRetentionConfigurationError(
                "maximum session preview byte count must be positive"
            )
        if self.max_session_files <= 0:
            raise PreviewRetentionConfigurationError(
                "maximum session preview file count must be positive"
            )
        if self.cleanup_interval <= timedelta(0):
            raise PreviewRetentionConfigurationError(
                "preview cleanup interval must be positive"
            )

    @property
    def cleanup_interval_seconds(self) -> float:
        return self.cleanup_interval.total_seconds()


DEFAULT_PREVIEW_RETENTION: Final = PreviewRetentionPolicy()


@dataclass(frozen=True, slots=True)
class PreviewFileEntry:
    path: Path
    modified_at_seconds: float
    size: int


@dataclass(frozen=True, slots=True)
class PreviewRetentionDecision:
    removals: frozenset[Path]


@dataclass(frozen=True, slots=True)
class PreviewPruneReport:
    lock_acquired: bool
    active_sessions: int
    scanned_files: int
    removed_files: int
    removed_incomplete_files: int
    removed_directories: int
    removed_bytes: int
    protected_files: int
    failed_deletions: int
    remaining_bytes: int
    capacity_deferred_bytes: int

    @classmethod
    def lock_busy(cls) -> PreviewPruneReport:
        return cls(
            lock_acquired=False,
            active_sessions=0,
            scanned_files=0,
            removed_files=0,
            removed_incomplete_files=0,
            removed_directories=0,
            removed_bytes=0,
            protected_files=0,
            failed_deletions=0,
            remaining_bytes=0,
            capacity_deferred_bytes=0,
        )


def select_preview_removals(
    entries: tuple[PreviewFileEntry, ...],
    policy: PreviewRetentionPolicy,
    now_seconds: float,
    protected_bytes: int,
) -> PreviewRetentionDecision:
    ttl_cutoff = now_seconds - policy.completed_ttl.total_seconds()
    grace_cutoff = now_seconds - policy.result_grace.total_seconds()
    expired = {
        entry.path
        for entry in entries
        if entry.modified_at_seconds <= ttl_cutoff
    }
    retained = [entry for entry in entries if entry.path not in expired]
    retained.sort(key=lambda entry: (entry.modified_at_seconds, entry.path.name))
    remaining_bytes = protected_bytes + sum(entry.size for entry in retained)
    removals = set(expired)
    for entry in retained:
        if remaining_bytes <= policy.max_total_bytes:
            break
        if entry.modified_at_seconds > grace_cutoff:
            continue
        removals.add(entry.path)
        remaining_bytes -= entry.size
    return PreviewRetentionDecision(removals=frozenset(removals))

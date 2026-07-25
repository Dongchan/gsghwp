from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from hwp_live_preview_lock import PreviewFileLock
from hwp_live_preview_retention import (
    PreviewFileEntry,
    PreviewPruneReport,
    PreviewRetentionPolicy,
    select_preview_removals,
)


@dataclass(frozen=True, slots=True)
class _FileScan:
    completed: tuple[PreviewFileEntry, ...]
    incomplete: tuple[PreviewFileEntry, ...]
    scanned_files: int
    protected_files: int
    protected_bytes: int


def _scan_files(paths: Iterable[Path]) -> _FileScan:
    completed: list[PreviewFileEntry] = []
    incomplete: list[PreviewFileEntry] = []
    scanned_files = 0
    protected_files = 0
    protected_bytes = 0
    for path in paths:
        if path.name == ".owner.lock":
            continue
        scanned_files += 1
        try:
            if path.is_symlink() or not path.is_file():
                protected_files += 1
                continue
            stat = path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            protected_files += 1
            continue
        entry = PreviewFileEntry(path, stat.st_mtime, stat.st_size)
        if path.name.startswith(".rendering-") and path.suffix.lower() == ".png":
            incomplete.append(entry)
        elif path.suffix.lower() == ".png":
            completed.append(entry)
        else:
            protected_files += 1
            protected_bytes += entry.size
    return _FileScan(
        completed=tuple(completed),
        incomplete=tuple(incomplete),
        scanned_files=scanned_files,
        protected_files=protected_files,
        protected_bytes=protected_bytes,
    )


def _flat_directory_bytes(directory: Path) -> int:
    try:
        scan = _scan_files(directory.iterdir())
    except OSError:
        return 0
    return (
        scan.protected_bytes
        + sum(entry.size for entry in scan.completed)
        + sum(entry.size for entry in scan.incomplete)
    )


def _delete_entries(
    entries: Iterable[PreviewFileEntry],
) -> tuple[int, int, int]:
    removed_files = 0
    removed_bytes = 0
    failed_deletions = 0
    for entry in entries:
        try:
            entry.path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            failed_deletions += 1
        else:
            removed_files += 1
            removed_bytes += entry.size
    return removed_files, removed_bytes, failed_deletions


def _remove_empty_directories(
    directories: Iterable[Path],
) -> tuple[int, int]:
    removed_directories = 0
    failed_deletions = 0
    for directory in directories:
        try:
            remaining = tuple(
                path
                for path in directory.iterdir()
                if path.name != ".owner.lock"
            )
            if remaining:
                continue
            (directory / ".owner.lock").unlink(missing_ok=True)
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            failed_deletions += 1
        else:
            removed_directories += 1
    return removed_directories, failed_deletions


def prune_preview_root(
    root: Path,
    policy: PreviewRetentionPolicy,
    now_seconds: float,
) -> PreviewPruneReport:
    completed: list[PreviewFileEntry] = []
    incomplete: list[PreviewFileEntry] = []
    inactive_directories: list[Path] = []
    active_sessions = 0
    scanned_files = 0
    protected_files = 0
    protected_bytes = 0

    for path in root.iterdir():
        if path.name == ".cleanup.lock":
            continue
        if path.is_symlink():
            protected_files += 1
            continue
        if path.is_file():
            scan = _scan_files((path,))
        elif path.is_dir():
            owner = PreviewFileLock.try_acquire(path / ".owner.lock")
            if owner is None:
                active_sessions += 1
                protected_bytes += _flat_directory_bytes(path)
                continue
            with owner:
                inactive_directories.append(path)
                scan = _scan_files(path.iterdir())
        else:
            protected_files += 1
            continue
        completed.extend(scan.completed)
        incomplete.extend(scan.incomplete)
        scanned_files += scan.scanned_files
        protected_files += scan.protected_files
        protected_bytes += scan.protected_bytes

    incomplete_removed, incomplete_bytes, incomplete_failed = _delete_entries(
        incomplete
    )
    failed_incomplete_bytes = sum(entry.size for entry in incomplete) - incomplete_bytes
    decision = select_preview_removals(
        tuple(completed),
        policy,
        now_seconds,
        protected_bytes + failed_incomplete_bytes,
    )
    completed_removed, completed_bytes, completed_failed = _delete_entries(
        entry for entry in completed if entry.path in decision.removals
    )
    removed_directories, directory_failures = _remove_empty_directories(
        inactive_directories
    )
    removed_files = incomplete_removed + completed_removed
    removed_bytes = incomplete_bytes + completed_bytes
    initial_bytes = (
        protected_bytes
        + sum(entry.size for entry in incomplete)
        + sum(entry.size for entry in completed)
    )
    remaining_bytes = initial_bytes - removed_bytes
    return PreviewPruneReport(
        lock_acquired=True,
        active_sessions=active_sessions,
        scanned_files=scanned_files,
        removed_files=removed_files,
        removed_incomplete_files=incomplete_removed,
        removed_directories=removed_directories,
        removed_bytes=removed_bytes,
        protected_files=protected_files,
        failed_deletions=incomplete_failed + completed_failed + directory_failures,
        remaining_bytes=remaining_bytes,
        capacity_deferred_bytes=max(0, remaining_bytes - policy.max_total_bytes),
    )

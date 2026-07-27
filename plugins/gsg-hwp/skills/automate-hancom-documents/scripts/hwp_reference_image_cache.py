from __future__ import annotations

import hashlib
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from hwp_live_preview_lock import PreviewFileLock


_DEFAULT_MAX_SOURCE_BYTES: Final = 64 * 1024 * 1024
_DEFAULT_MAX_SOURCE_PIXELS: Final = 24_000_000
_DEFAULT_MAX_ANALYSIS_PIXELS: Final = 8_000_000
_DEFAULT_MAX_ANALYSIS_ARTIFACT_BYTES: Final = 32 * 1024 * 1024
_DEFAULT_MAX_CACHE_BYTES: Final = 256 * 1024 * 1024
_DEFAULT_MAX_CACHE_ENTRIES: Final = 128
_DEFAULT_TTL_SECONDS: Final = 7 * 24 * 60 * 60
_DEFAULT_LOCK_TIMEOUT_SECONDS: Final = 30.0
_DEFAULT_STALE_LOCK_SECONDS: Final = 30 * 60.0
_EMPTY_ANALYSIS_IDS: Final[frozenset[str]] = frozenset()
_PARTIAL_ANALYSIS_ID: Final = re.compile(
    r"^\.partial-(ria-[0-9a-f]{16})-"
)


@dataclass(frozen=True, slots=True)
class ReferenceImageResourceLimits:
    max_source_bytes: int = _DEFAULT_MAX_SOURCE_BYTES
    max_source_pixels: int = _DEFAULT_MAX_SOURCE_PIXELS
    max_analysis_pixels: int = _DEFAULT_MAX_ANALYSIS_PIXELS
    max_analysis_artifact_bytes: int = _DEFAULT_MAX_ANALYSIS_ARTIFACT_BYTES
    max_cache_bytes: int = _DEFAULT_MAX_CACHE_BYTES
    max_cache_entries: int = _DEFAULT_MAX_CACHE_ENTRIES
    ttl_seconds: float = _DEFAULT_TTL_SECONDS
    lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS
    stale_lock_seconds: float = _DEFAULT_STALE_LOCK_SECONDS

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_source_bytes,
            self.max_source_pixels,
            self.max_analysis_pixels,
            self.max_analysis_artifact_bytes,
            self.max_cache_bytes,
            self.max_cache_entries,
        )
        if any(value < 1 for value in integer_limits):
            raise ValueError("reference image resource limits must be positive")
        if self.max_source_pixels < 4 or self.max_analysis_pixels < 4:
            raise ValueError("reference image pixel limits must allow a 2 by 2 image")
        if self.max_analysis_pixels > self.max_source_pixels:
            raise ValueError("analysis pixel limit cannot exceed source pixel limit")
        if self.max_analysis_artifact_bytes > self.max_cache_bytes:
            raise ValueError("analysis artifact limit cannot exceed cache byte limit")
        if self.ttl_seconds <= 0 or self.lock_timeout_seconds <= 0:
            raise ValueError("reference image time limits must be positive")
        if self.stale_lock_seconds <= 0:
            raise ValueError("reference image stale lock limit must be positive")

    @property
    def analysis_fingerprint(self) -> str:
        encoded = f"max_analysis_pixels={self.max_analysis_pixels}".encode("ascii")
        return hashlib.sha256(encoded).hexdigest()[:16]


DEFAULT_REFERENCE_IMAGE_LIMITS: Final = ReferenceImageResourceLimits()


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    path: Path
    size: int
    modified_at: float


def acquire_reference_image_lock(
    artifact_root: Path,
    analysis_id: str,
    limits: ReferenceImageResourceLimits,
) -> PreviewFileLock:
    lock_path = artifact_root / ".locks" / f"{analysis_id}.lock"
    deadline = time.monotonic() + limits.lock_timeout_seconds
    while True:
        lock = PreviewFileLock.try_acquire(lock_path)
        if lock is not None:
            return lock
        if time.monotonic() >= deadline:
            raise ValueError("reference image analysis lock timeout")
        time.sleep(0.05)


def directory_size(directory: Path) -> int:
    size, _ = _directory_stats(directory)
    return size


def _directory_stats(directory: Path) -> tuple[int, float]:
    total = 0
    modified_at = directory.stat().st_mtime
    for path in directory.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                total += stat.st_size
                modified_at = max(modified_at, stat.st_mtime)
        except OSError:
            continue
    return total, modified_at


def _acquire_directory_locks(
    artifact_root: Path,
    directory: Path,
) -> tuple[PreviewFileLock, ...] | None:
    lock_paths = [artifact_root / ".locks" / f"{directory.name}.lock"]
    if not directory.name.startswith("ria-"):
        lock_paths.extend(directory.rglob("*.lock"))
    acquired: list[PreviewFileLock] = []
    for path in lock_paths:
        lock = PreviewFileLock.try_acquire(path)
        if lock is None:
            for held in reversed(acquired):
                held.close()
            return None
        acquired.append(lock)
    return tuple(acquired)


def _close_locks(locks: tuple[PreviewFileLock, ...]) -> None:
    for lock in reversed(locks):
        lock.close()


def remove_reference_cache_directory(artifact_root: Path, directory: Path) -> None:
    resolved_root = artifact_root.resolve()
    resolved = directory.resolve()
    if resolved.parent != resolved_root:
        raise ValueError("reference image cache removal escaped artifact root")
    if resolved.is_symlink():
        resolved.unlink(missing_ok=True)
        return
    shutil.rmtree(resolved, ignore_errors=False)


def prune_reference_image_cache(
    artifact_root: Path,
    limits: ReferenceImageResourceLimits,
    *,
    protected_analysis_ids: frozenset[str] = _EMPTY_ANALYSIS_IDS,
) -> None:
    artifact_root.mkdir(parents=True, exist_ok=True)
    cleanup_lock = PreviewFileLock.try_acquire(artifact_root / ".cleanup.lock")
    if cleanup_lock is None:
        return
    with cleanup_lock:
        now = time.time()
        entries: list[_CacheEntry] = []
        protected_bytes = 0
        protected_entries = 0
        for directory in artifact_root.iterdir():
            if (
                directory.name == ".locks"
                or directory.is_symlink()
                or not directory.is_dir()
            ):
                continue
            if directory.name.startswith(".partial-"):
                matched = _PARTIAL_ANALYSIS_ID.match(directory.name)
                if matched is None:
                    continue
                partial_lock = PreviewFileLock.try_acquire(
                    artifact_root / ".locks" / f"{matched.group(1)}.lock"
                )
                if partial_lock is None:
                    continue
                with partial_lock:
                    remove_reference_cache_directory(artifact_root, directory)
                continue
            if directory.name in protected_analysis_ids:
                size, _ = _directory_stats(directory)
                protected_bytes += size
                protected_entries += 1
                continue
            entry_locks = _acquire_directory_locks(artifact_root, directory)
            if entry_locks is None:
                size, _ = _directory_stats(directory)
                protected_bytes += size
                protected_entries += 1
                continue
            try:
                result_path = directory / "result.json"
                if directory.name.startswith("ria-") and not result_path.is_file():
                    try:
                        remove_reference_cache_directory(artifact_root, directory)
                    except OSError:
                        pass
                    continue
                size, modified_at = _directory_stats(directory)
                if now - modified_at >= limits.ttl_seconds:
                    _close_locks(entry_locks)
                    entry_locks = ()
                    try:
                        remove_reference_cache_directory(artifact_root, directory)
                    except OSError:
                        pass
                    continue
                entries.append(_CacheEntry(directory, size, modified_at))
            finally:
                _close_locks(entry_locks)
        entries.sort(key=lambda entry: (entry.modified_at, entry.path.name))
        total = protected_bytes + sum(entry.size for entry in entries)
        retained_count = protected_entries + len(entries)
        while entries and (
            retained_count > limits.max_cache_entries
            or total > limits.max_cache_bytes
        ):
            entry = entries.pop(0)
            entry_locks = _acquire_directory_locks(artifact_root, entry.path)
            if entry_locks is None:
                continue
            _close_locks(entry_locks)
            try:
                remove_reference_cache_directory(artifact_root, entry.path)
                total -= entry.size
                retained_count -= 1
            except OSError:
                continue

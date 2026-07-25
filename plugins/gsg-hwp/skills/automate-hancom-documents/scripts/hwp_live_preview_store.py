from __future__ import annotations

import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import final

from hwp_errors import HwpLiveError
from hwp_live_preview_cleanup import prune_preview_root
from hwp_live_preview_lock import PreviewFileLock
from hwp_live_preview_retention import (
    DEFAULT_PREVIEW_RETENTION,
    PreviewPruneReport,
    PreviewRetentionPolicy,
)


_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def default_preview_root() -> Path:
    return Path(tempfile.gettempdir()) / "hancom-live-agent"


@final
class PreviewSessionLease:
    __slots__ = (
        "_active",
        "_clock",
        "_max_bytes",
        "_max_files",
        "_owner_lock",
        "_result_grace_seconds",
        "directory",
    )

    def __init__(
        self,
        directory: Path,
        owner_lock: PreviewFileLock,
        *,
        max_bytes: int,
        max_files: int,
        result_grace_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self.directory = directory
        self._owner_lock = owner_lock
        self._active = True
        self._clock = clock
        self._max_bytes = max_bytes
        self._max_files = max_files
        self._result_grace_seconds = result_grace_seconds

    def commit(self, path: Path) -> None:
        if not self._active:
            raise HwpLiveError("종료된 미리보기 세션에는 결과를 게시할 수 없습니다")
        if (
            path.parent != self.directory
            or path.is_symlink()
            or not path.is_file()
            or not path.name.startswith("page-")
            or path.suffix.casefold() != ".png"
        ):
            raise HwpLiveError("미리보기 결과 경로가 현재 세션에 속하지 않습니다")
        entries: list[tuple[Path, float, int]] = []
        try:
            for candidate in self.directory.iterdir():
                if (
                    candidate.is_symlink()
                    or not candidate.is_file()
                    or not candidate.name.startswith("page-")
                    or candidate.suffix.casefold() != ".png"
                ):
                    continue
                status = candidate.stat()
                entries.append((candidate, status.st_mtime, status.st_size))
        except OSError as error:
            path.unlink(missing_ok=True)
            raise HwpLiveError("미리보기 세션 용량을 확인할 수 없습니다") from error
        projected_count = len(entries)
        projected_bytes = sum(size for _, _, size in entries)
        grace_cutoff = self._clock() - self._result_grace_seconds
        removable = sorted(
            (
                entry
                for entry in entries
                if entry[0] != path and entry[1] <= grace_cutoff
            ),
            key=lambda entry: (entry[1], entry[0].name),
        )
        removals: list[tuple[Path, float, int]] = []
        for entry in removable:
            if (
                projected_count <= self._max_files
                and projected_bytes <= self._max_bytes
            ):
                break
            removals.append(entry)
            projected_count -= 1
            projected_bytes -= entry[2]
        if projected_count > self._max_files or projected_bytes > self._max_bytes:
            path.unlink(missing_ok=True)
            raise HwpLiveError(
                "미리보기 세션 할당량을 초과했고 읽기 유예 중인 결과가 있어 게시하지 않았습니다"
            )
        for removable_path, _, _ in removals:
            try:
                removable_path.unlink()
            except OSError as error:
                path.unlink(missing_ok=True)
                raise HwpLiveError(
                    "미리보기 세션의 이전 결과를 정리할 수 없습니다"
                ) from error

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        self._owner_lock.close()

    def __enter__(self) -> PreviewSessionLease:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc_value, traceback)
        self.close()


@final
class PreviewStore:
    __slots__ = ("_clock", "_retention", "_root")

    def __init__(
        self,
        root: Path | None = None,
        *,
        retention: PreviewRetentionPolicy = DEFAULT_PREVIEW_RETENTION,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = default_preview_root() if root is None else root
        self._root.mkdir(parents=True, exist_ok=True)
        self._retention = retention
        self._clock = clock

    @property
    def cleanup_interval_seconds(self) -> float:
        return self._retention.cleanup_interval_seconds

    def open_session(self, session_id: str) -> PreviewSessionLease:
        if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            raise HwpLiveError("유효한 미리보기 세션 ID가 아닙니다")
        with PreviewFileLock.acquire(self._root / ".cleanup.lock"):
            directory = self._root / session_id
            directory.mkdir(parents=True, exist_ok=True)
            owner = PreviewFileLock.try_acquire(directory / ".owner.lock")
            if owner is None:
                raise HwpLiveError("미리보기 세션이 이미 다른 worker에서 사용 중입니다")
            return PreviewSessionLease(
                directory,
                owner,
                max_bytes=self._retention.max_session_bytes,
                max_files=self._retention.max_session_files,
                result_grace_seconds=self._retention.result_grace.total_seconds(),
                clock=self._clock,
            )

    def prune(self) -> PreviewPruneReport:
        cleanup_lock = PreviewFileLock.try_acquire(self._root / ".cleanup.lock")
        if cleanup_lock is None:
            return PreviewPruneReport.lock_busy()
        with cleanup_lock:
            return prune_preview_root(
                self._root,
                self._retention,
                self._clock(),
            )

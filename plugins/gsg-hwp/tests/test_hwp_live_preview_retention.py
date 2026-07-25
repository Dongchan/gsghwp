from __future__ import annotations

import multiprocessing
import os
import sys
from datetime import timedelta
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import Final

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_preview_retention import PreviewRetentionPolicy  # noqa: E402
from hwp_live_preview_store import PreviewStore  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402


_DEFAULT_TTL: Final = timedelta(hours=24)
_DEFAULT_GRACE: Final = timedelta(minutes=15)
_DEFAULT_INTERVAL: Final = timedelta(minutes=15)


def _policy(
    *,
    ttl: timedelta = _DEFAULT_TTL,
    grace: timedelta = _DEFAULT_GRACE,
    max_bytes: int = 64 * 1024 * 1024,
    max_session_bytes: int = 16 * 1024 * 1024,
    max_session_files: int = 16,
) -> PreviewRetentionPolicy:
    return PreviewRetentionPolicy(
        completed_ttl=ttl,
        result_grace=grace,
        max_total_bytes=max_bytes,
        max_session_bytes=max_session_bytes,
        max_session_files=max_session_files,
        cleanup_interval=_DEFAULT_INTERVAL,
    )


def _hold_preview_session(root: str, ready: Event, release: Event) -> None:
    store = PreviewStore(Path(root))
    with store.open_session("child-session") as session:
        _ = (session.directory / "page-001-child.png").write_bytes(b"active")
        ready.set()
        assert release.wait(10)


def test_prune_preserves_session_while_current_process_owns_lock(
    tmp_path: Path,
) -> None:
    # Given
    current = [1_000.0]
    store = PreviewStore(
        tmp_path / "previews",
        retention=_policy(
            ttl=timedelta(seconds=1),
            grace=timedelta(seconds=1),
        ),
        clock=lambda: current[0],
    )
    session = store.open_session("active-session")
    completed = session.directory / "page-001-active.png"
    _ = completed.write_bytes(b"active")
    os.utime(completed, (1_000.0, 1_000.0))
    current[0] = 1_002.0

    # When
    try:
        report = store.prune()
    finally:
        session.close()

    # Then
    assert report.active_sessions == 1
    assert report.removed_files == 0
    assert completed.exists()


def test_prune_preserves_session_owned_by_another_process(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "previews"
    store = PreviewStore(
        root,
        retention=_policy(
            ttl=timedelta(seconds=1),
            grace=timedelta(seconds=1),
        ),
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_preview_session,
        args=(str(root), ready, release),
    )
    process.start()

    # When
    try:
        assert ready.wait(10)
        report = store.prune()
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(10)

    # Then
    assert process.exitcode == 0
    assert report.active_sessions == 1
    assert (root / "child-session" / "page-001-child.png").exists()


def test_prune_removes_partial_but_keeps_fresh_completed_result(
    tmp_path: Path,
) -> None:
    # Given
    current = [2_000.0]
    store = PreviewStore(
        tmp_path / "previews",
        retention=_policy(),
        clock=lambda: current[0],
    )
    with store.open_session("released-session") as session:
        partial = session.directory / ".rendering-page-001-dead.png"
        completed = session.directory / "page-001-complete.png"
        _ = partial.write_bytes(b"partial")
        _ = completed.write_bytes(b"complete")
        os.utime(completed, (2_000.0, 2_000.0))

    # When
    report = store.prune()

    # Then
    assert report.removed_incomplete_files == 1
    assert not partial.exists()
    assert completed.read_bytes() == b"complete"


def test_prune_expires_completed_result_and_empty_session_directory(
    tmp_path: Path,
) -> None:
    # Given
    current = [3_000.0]
    store = PreviewStore(
        tmp_path / "previews",
        retention=_policy(
            ttl=timedelta(seconds=10),
            grace=timedelta(seconds=1),
        ),
        clock=lambda: current[0],
    )
    with store.open_session("expired-session") as session:
        directory = session.directory
        completed = directory / "page-001-expired.png"
        _ = completed.write_bytes(b"expired")
        os.utime(completed, (3_000.0, 3_000.0))
    current[0] = 3_011.0

    # When
    report = store.prune()

    # Then
    assert report.removed_files == 1
    assert report.removed_directories == 1
    assert not directory.exists()


def test_capacity_pressure_keeps_result_during_client_read_grace(
    tmp_path: Path,
) -> None:
    # Given
    current = [4_000.0]
    store = PreviewStore(
        tmp_path / "previews",
        retention=_policy(grace=timedelta(seconds=60), max_bytes=1),
        clock=lambda: current[0],
    )
    with store.open_session("fresh-session") as session:
        completed = session.directory / "page-001-fresh.png"
        _ = completed.write_bytes(b"fresh")
        os.utime(completed, (4_000.0, 4_000.0))

    # When
    report = store.prune()

    # Then
    assert completed.exists()
    assert report.remaining_bytes == 5
    assert report.capacity_deferred_bytes == 4


def test_capacity_pressure_removes_oldest_result_after_client_grace(
    tmp_path: Path,
) -> None:
    # Given
    current = [5_100.0]
    store = PreviewStore(
        tmp_path / "previews",
        retention=_policy(grace=timedelta(seconds=10), max_bytes=4),
        clock=lambda: current[0],
    )
    with store.open_session("capacity-session") as session:
        older = session.directory / "page-001-older.png"
        newer = session.directory / "page-001-newer.png"
        _ = older.write_bytes(b"old!")
        _ = newer.write_bytes(b"new!")
        os.utime(older, (5_000.0, 5_000.0))
        os.utime(newer, (5_001.0, 5_001.0))

    # When
    report = store.prune()

    # Then
    assert not older.exists()
    assert newer.read_bytes() == b"new!"
    assert report.remaining_bytes == 4
    assert report.capacity_deferred_bytes == 0


def test_session_quota_evicts_only_results_past_client_grace(tmp_path: Path) -> None:
    current = [6_000.0]
    store = PreviewStore(
        tmp_path / "previews",
        retention=_policy(
            grace=timedelta(seconds=10),
            max_session_bytes=8,
            max_session_files=2,
        ),
        clock=lambda: current[0],
    )
    with store.open_session("quota-session") as session:
        oldest = session.directory / "page-001-oldest.png"
        older = session.directory / "page-002-older.png"
        newest = session.directory / "page-003-newest.png"
        _ = oldest.write_bytes(b"old!")
        _ = older.write_bytes(b"keep")
        _ = newest.write_bytes(b"new!")
        os.utime(oldest, (5_000.0, 5_000.0))
        os.utime(older, (5_001.0, 5_001.0))
        session.commit(newest)
        assert not oldest.exists()
        assert older.read_bytes() == b"keep"
        assert newest.read_bytes() == b"new!"


def test_session_quota_rejects_new_result_during_client_grace(
    tmp_path: Path,
) -> None:
    current = [7_000.0]
    store = PreviewStore(
        tmp_path / "previews",
        retention=_policy(
            grace=timedelta(seconds=60),
            max_session_bytes=4,
            max_session_files=1,
        ),
        clock=lambda: current[0],
    )
    with store.open_session("grace-session") as session:
        existing = session.directory / "page-001-existing.png"
        newest = session.directory / "page-002-newest.png"
        _ = existing.write_bytes(b"keep")
        _ = newest.write_bytes(b"new!")
        os.utime(existing, (7_000.0, 7_000.0))
        with pytest.raises(HwpLiveError, match="할당량"):
            session.commit(newest)
        assert existing.read_bytes() == b"keep"
        assert not newest.exists()


def test_session_quota_rejects_single_oversized_result(tmp_path: Path) -> None:
    store = PreviewStore(
        tmp_path / "previews",
        retention=_policy(max_session_bytes=3),
    )
    with store.open_session("oversize-session") as session:
        result = session.directory / "page-001-large.png"
        _ = result.write_bytes(b"large")
        with pytest.raises(HwpLiveError, match="할당량"):
            session.commit(result)
        assert not result.exists()

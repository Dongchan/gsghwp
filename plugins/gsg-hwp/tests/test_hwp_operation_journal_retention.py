from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, cast


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_operation_contract import OperationResult  # noqa: E402
from hwp_operation_journal import (  # noqa: E402
    OperationJournal,
    operation_result_digest,
)
from hwp_operation_journal_retention import JournalRetentionPolicy  # noqa: E402
from hwp_operation_journal_store import OperationJournalStore  # noqa: E402


_SESSION = "retention-document-session"
_DEFAULT_TTL: Final = timedelta(days=30)
_DEFAULT_CLEANUP_INTERVAL: Final = timedelta(minutes=15)
_LOCK_HOLDER = (
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from hwp_operation_journal_store import OperationJournalStore\n"
    "store = OperationJournalStore(Path(sys.argv[2]))\n"
    "with store.transaction() as acquired:\n"
    "    print('locked' if acquired else 'not-locked', flush=True)\n"
    "    _ = sys.stdin.readline()\n"
)


def _policy(
    *,
    ttl: timedelta = _DEFAULT_TTL,
    max_files: int = 10_000,
    max_bytes: int = 64 * 1024 * 1024,
    cleanup_interval: timedelta = _DEFAULT_CLEANUP_INTERVAL,
) -> JournalRetentionPolicy:
    return JournalRetentionPolicy(
        terminal_ttl=ttl,
        max_terminal_files=max_files,
        max_terminal_bytes=max_bytes,
        cleanup_interval=cleanup_interval,
    )


def _result(label: str, *, partial_mutation: bool = False) -> OperationResult:
    return OperationResult(
        status="executed",
        changed=True,
        query=label[:100],
        registry_entries=1,
        lookup_microseconds=0,
        message=label,
        partial_mutation=partial_mutation,
        retry_safe=not partial_mutation,
    )


def _commit(journal: OperationJournal, request_id: str, message: str = "committed") -> None:
    decision = journal.begin(_SESSION, request_id, f"digest:{request_id}")
    assert decision.kind == "execute"
    journal.mark_executing(_SESSION, request_id)
    result = _result(message)
    journal.mark_verified(
        _SESSION,
        request_id,
        result,
        operation_result_digest(result),
    )
    journal.mark_committed(_SESSION, request_id)


def test_prune_expires_terminal_records_but_preserves_active_and_reconcile_required(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock() -> datetime:
        return current[0]

    root = tmp_path / "journal"
    journal = OperationJournal(root, clock=clock, retention=_policy())
    store = OperationJournalStore(root)
    _commit(journal, "expired")

    _ = journal.begin(_SESSION, "active", "digest:active")
    journal.mark_executing(_SESSION, "active")

    _ = journal.begin(_SESSION, "reconcile", "digest:reconcile")
    journal.mark_executing(_SESSION, "reconcile")
    partial = _result("partial", partial_mutation=True)
    journal.mark_failed(
        _SESSION,
        "reconcile",
        "partial_mutation_reconcile_required",
        result=partial,
        result_digest=operation_result_digest(partial),
    )
    current[0] += timedelta(days=31)

    report = journal.prune()

    assert report.lock_acquired is True
    assert report.removed_files == 1
    assert not store.entry_path(_SESSION, "expired").exists()
    assert store.entry_path(_SESSION, "active").exists()
    assert store.entry_path(_SESSION, "reconcile").exists()


def test_prune_keeps_newest_terminal_records_within_file_limit(tmp_path: Path) -> None:
    current = [datetime(2026, 2, 1, tzinfo=UTC)]

    def clock() -> datetime:
        return current[0]

    root = tmp_path / "journal"
    journal = OperationJournal(
        root,
        clock=clock,
        retention=_policy(ttl=timedelta(days=365), max_files=2),
    )
    store = OperationJournalStore(root)
    for request_id in ("oldest", "middle", "newest"):
        _commit(journal, request_id)
        current[0] += timedelta(seconds=1)

    report = journal.prune()

    assert report.removed_files == 1
    assert not store.entry_path(_SESSION, "oldest").exists()
    assert store.entry_path(_SESSION, "middle").exists()
    assert store.entry_path(_SESSION, "newest").exists()
    assert journal.begin(_SESSION, "newest", "digest:newest").kind == "replay"


def test_prune_keeps_newest_terminal_records_within_byte_limit(tmp_path: Path) -> None:
    current = [datetime(2026, 3, 1, tzinfo=UTC)]

    def clock() -> datetime:
        return current[0]

    root = tmp_path / "journal"
    unrestricted = OperationJournal(
        root,
        clock=clock,
        retention=_policy(ttl=timedelta(days=365)),
    )
    store = OperationJournalStore(root)
    _commit(unrestricted, "older", "o" * 2_000)
    current[0] += timedelta(seconds=1)
    _commit(unrestricted, "newer", "n" * 2_000)
    newer_path = store.entry_path(_SESSION, "newer")
    newer_size = newer_path.stat().st_size

    bounded = OperationJournal(
        root,
        clock=clock,
        retention=_policy(
            ttl=timedelta(days=365),
            max_bytes=newer_size,
        ),
    )
    report = bounded.prune()

    assert report.removed_files == 1
    assert report.remaining_terminal_bytes <= newer_size
    assert not store.entry_path(_SESSION, "older").exists()
    assert newer_path.exists()


def test_prune_preserves_unreadable_records_for_manual_reconciliation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "journal"
    root.mkdir()
    unreadable = root / "unreadable.json"
    _ = unreadable.write_text("{not valid json", encoding="utf-8")
    journal = OperationJournal(root, retention=_policy())

    report = journal.prune()

    assert report.invalid_files == 1
    assert report.removed_files == 0
    assert unreadable.exists()


def test_prune_skips_when_another_process_owns_journal_transaction(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 4, 1, tzinfo=UTC)]

    def clock() -> datetime:
        return current[0]

    root = tmp_path / "journal"
    journal = OperationJournal(
        root,
        clock=clock,
        retention=_policy(ttl=timedelta(seconds=1)),
    )
    store = OperationJournalStore(root)
    _commit(journal, "expired")
    current[0] += timedelta(seconds=2)
    entry = store.entry_path(_SESSION, "expired")

    process = subprocess.Popen(
        [sys.executable, "-c", _LOCK_HOLDER, str(SCRIPTS), str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        assert cast(str, process.stdout.readline()).strip() == "locked"
        skipped = journal.prune()
        assert skipped.lock_acquired is False
        assert entry.exists()
    finally:
        output, errors = process.communicate("\n", timeout=10)

    assert process.returncode == 0, f"{output}\n{errors}"
    completed = journal.prune()
    assert completed.lock_acquired is True
    assert completed.removed_files == 1
    assert not entry.exists()

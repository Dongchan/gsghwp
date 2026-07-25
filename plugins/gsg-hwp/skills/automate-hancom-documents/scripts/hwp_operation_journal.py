from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — journal transitions stay together as one atomic state machine.

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Final, Literal, final

from hwp_operation_contract import (
    HwpOperateRecovery,
    OperationJournalState,
    OperationResult,
)
from hwp_operation_journal_contract import (
    JournalDecision,
    JournalDecisionKind,
    JournalRecord,
    JournalSnapshot,
    OperationJournalError,
    default_operation_journal_path,
    document_session_key,
    operation_result_digest,
    request_payload_digest,
)
from hwp_operation_journal_recovery import recover_record
from hwp_operation_journal_retention import (
    DEFAULT_JOURNAL_RETENTION,
    JournalPruneReport,
    JournalRetentionPolicy,
)
from hwp_operation_journal_store import OperationJournalStore


DEFAULT_STALE_TIMEOUT: Final = timedelta(minutes=2)
HEARTBEAT_INTERVAL_SECONDS: Final = 10
__all__: Final = (
    "OperationJournal",
    "OperationJournalError",
    "default_operation_journal_path",
    "document_session_key",
    "operation_result_digest",
    "request_payload_digest",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@final
class OperationJournal:
    __slots__ = ("_clock", "_lock", "_retention", "_stale_timeout", "_store")

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
        stale_timeout: timedelta = DEFAULT_STALE_TIMEOUT,
        retention: JournalRetentionPolicy = DEFAULT_JOURNAL_RETENTION,
    ) -> None:
        if stale_timeout <= timedelta(0):
            raise OperationJournalError("stale timeout must be positive")
        self._clock = clock
        self._lock = Lock()
        self._retention = retention
        self._stale_timeout = stale_timeout
        self._store = OperationJournalStore(
            default_operation_journal_path() if path is None else path
        )

    @property
    def heartbeat_interval_seconds(self) -> int:
        return min(
            HEARTBEAT_INTERVAL_SECONDS,
            max(1, self.stale_after_seconds // 3),
        )

    @property
    def stale_after_seconds(self) -> int:
        return int(self._stale_timeout.total_seconds())

    @property
    def cleanup_interval_seconds(self) -> float:
        return self._retention.cleanup_interval_seconds

    def _recover(
        self,
        path: Path,
        document_session: str,
        record: JournalRecord,
        recovery: HwpOperateRecovery,
        now: datetime,
    ) -> JournalDecision:
        transition = recover_record(record, recovery, now)
        if transition.persist:
            self._store.replace(path, transition.record)
        return transition.record.decision(
            transition.decision,
            document_session,
            self.stale_after_seconds,
        )

    def begin(
        self,
        document_session: str,
        request_id: str,
        request_digest: str,
        recovery: HwpOperateRecovery | None = None,
    ) -> JournalDecision:
        with self._lock, self._store.transaction():
            path = self._store.entry_path(document_session, request_id)
            now = self._clock()
            record = JournalRecord(
                request_digest=request_digest,
                state="accepted",
                started_at=now,
                updated_at=now,
                heartbeat_at=now,
            )
            try:
                self._store.create(path, record)
                return record.decision(
                    "execute", document_session, self.stale_after_seconds
                )
            except FileExistsError:
                record = self._store.read(path)
            if record.request_digest != request_digest:
                return record.decision(
                    "conflict", document_session, self.stale_after_seconds
                )
            match record.state:  # noqa: E501  # noqa: MATCH_OK — OperationJournalState is exhaustive.
                case "verified":
                    record = record.model_copy(update={"state": "committed", "updated_at": now})
                    self._store.replace(path, record)
                case "committed":
                    pass
                case "accepted" | "executing":
                    if now - record.heartbeat_at < self._stale_timeout:
                        return record.decision(
                            "in_progress", document_session, self.stale_after_seconds
                        )
                    if recovery is None:
                        return record.decision(
                            "stale", document_session, self.stale_after_seconds
                        )
                    return self._recover(path, document_session, record, recovery, now)
                case "failed" | "aborted":
                    if recovery is None:
                        return record.decision(
                            record.state, document_session, self.stale_after_seconds
                        )
                    return self._recover(path, document_session, record, recovery, now)
            if record.result is None or record.result_digest is None:
                raise OperationJournalError("committed operation journal entry has no result")
            return record.decision("replay", document_session, self.stale_after_seconds)

    def lookup(
        self,
        document_session: str,
        request_id: str,
    ) -> JournalDecision | None:
        with self._lock, self._store.transaction():
            path = self._store.entry_path(document_session, request_id)
            if not path.exists():
                return None
            record = self._store.read(path)
            active_decision: JournalDecisionKind = (
                "in_progress"
                if self._clock() - record.heartbeat_at < self._stale_timeout
                else "stale"
            )
            decisions: dict[OperationJournalState, JournalDecisionKind] = {
                "accepted": active_decision,
                "executing": active_decision,
                "verified": "replay",
                "committed": "replay",
                "failed": "failed",
                "aborted": "aborted",
            }
            decision = decisions[record.state]
            return record.decision(
                decision,
                document_session,
                self.stale_after_seconds,
            )

    def mark_executing(self, document_session: str, request_id: str) -> None:
        with self._lock, self._store.transaction():
            path = self._store.entry_path(document_session, request_id)
            record = self._store.read(path)
            if record.state != "accepted":
                raise OperationJournalError("operation journal entry did not enter executing state")
            now = self._clock()
            self._store.replace(
                path,
                record.model_copy(
                    update={"state": "executing", "updated_at": now, "heartbeat_at": now}
                ),
            )

    def heartbeat(self, document_session: str, request_id: str) -> None:
        with self._lock, self._store.transaction():
            path = self._store.entry_path(document_session, request_id)
            record = self._store.read(path)
            if record.state not in {"accepted", "executing"}:
                return
            now = self._clock()
            self._store.replace(
                path,
                record.model_copy(update={"updated_at": now, "heartbeat_at": now}),
            )

    def _mark_terminal(
        self,
        key: tuple[str, str],
        state: Literal["failed", "aborted"],
        failure_code: str,
        result: OperationResult | None = None,
        result_digest: str | None = None,
    ) -> None:
        with self._lock, self._store.transaction():
            path = self._store.entry_path(*key)
            record = self._store.read(path)
            if record.state not in {"accepted", "executing"}:
                return
            now = self._clock()
            updated = record.model_copy(
                update={
                    "state": state,
                    "updated_at": now,
                    "heartbeat_at": now,
                    "failure_code": failure_code,
                }
            )
            if result is not None:
                updated = updated.with_result(result, result_digest)
            self._store.replace(path, updated)

    def mark_failed(
        self,
        document_session: str,
        request_id: str,
        failure_code: str,
        *,
        result: OperationResult | None = None,
        result_digest: str | None = None,
    ) -> None:
        self._mark_terminal(
            (document_session, request_id),
            "failed",
            failure_code,
            result,
            result_digest,
        )

    def mark_aborted(
        self,
        document_session: str,
        request_id: str,
        failure_code: str,
    ) -> None:
        self._mark_terminal((document_session, request_id), "aborted", failure_code)

    def mark_verified(
        self,
        document_session: str,
        request_id: str,
        result: OperationResult,
        result_digest: str,
    ) -> None:
        with self._lock, self._store.transaction():
            path = self._store.entry_path(document_session, request_id)
            record = self._store.read(path)
            if record.state != "executing":
                raise OperationJournalError("operation journal entry did not enter verified state")
            updated = record.model_copy(
                update={"state": "verified", "updated_at": self._clock()}
            )
            self._store.replace(path, updated.with_result(result, result_digest))

    def mark_committed(self, document_session: str, request_id: str) -> None:
        with self._lock, self._store.transaction():
            path = self._store.entry_path(document_session, request_id)
            record = self._store.read(path)
            if record.state == "committed":
                return
            if record.state != "verified":
                raise OperationJournalError("operation journal entry did not enter committed state")
            self._store.replace(
                path,
                record.model_copy(update={"state": "committed", "updated_at": self._clock()}),
            )

    def snapshot(self, document_session: str, request_id: str) -> JournalSnapshot:
        with self._lock, self._store.transaction():
            return self._store.read(
                self._store.entry_path(document_session, request_id)
            ).snapshot()

    def prune(self) -> JournalPruneReport:
        with self._lock:
            with self._store.transaction(blocking=False) as acquired:
                if not acquired:
                    return JournalPruneReport.lock_busy()
                return self._store.prune(self._retention, self._clock())

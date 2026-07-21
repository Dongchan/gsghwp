from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hwp_operation_contract import HwpOperateRecovery, OperationJournalState
from hwp_operation_journal_contract import (
    JournalDecisionKind,
    JournalRecord,
)


@dataclass(frozen=True, slots=True)
class RecoveryTransition:
    record: JournalRecord
    decision: JournalDecisionKind
    persist: bool


def recover_record(
    record: JournalRecord,
    recovery: HwpOperateRecovery,
    now: datetime,
) -> RecoveryTransition:
    match recovery.action:
        case "reconcile":
            if record.state not in {"accepted", "executing"}:
                return RecoveryTransition(record, "reconcile", False)
            reconciled = record.model_copy(
                update={
                    "state": "aborted",
                    "updated_at": now,
                    "heartbeat_at": now,
                    "failure_code": "stale_reconcile_confirmed",
                }
            )
            return RecoveryTransition(reconciled, "reconcile", True)
        case "recover":
            if record.partial_mutation is True:
                if record.state in {"accepted", "executing"}:
                    reconciled = record.model_copy(
                        update={
                            "state": "aborted",
                            "updated_at": now,
                            "heartbeat_at": now,
                            "failure_code": (
                                record.failure_code
                                or "partial_mutation_reconcile_required"
                            ),
                        }
                    )
                    return RecoveryTransition(reconciled, "reconcile", True)
                return RecoveryTransition(record, "reconcile", False)
            archived_state: OperationJournalState = (
                "aborted" if record.state in {"accepted", "executing"} else record.state
            )
            archived = record.model_copy(
                update={
                    "state": archived_state,
                    "updated_at": now,
                    "heartbeat_at": now,
                    "failure_code": (
                        "stale_recover_confirmed"
                        if archived_state == "aborted" and record.failure_code is None
                        else record.failure_code
                    ),
                }
            )
            recovered = JournalRecord(
                request_digest=record.request_digest,
                attempt=record.attempt + 1,
                state="accepted",
                started_at=now,
                updated_at=now,
                heartbeat_at=now,
                previous_attempts=(
                    *record.previous_attempts,
                    archived.attempt_snapshot(),
                ),
            )
            return RecoveryTransition(recovered, "execute", True)

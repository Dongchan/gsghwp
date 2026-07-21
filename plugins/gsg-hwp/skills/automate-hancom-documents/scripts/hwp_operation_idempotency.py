from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Thread
from typing import Literal, final

from anyio import get_cancelled_exc_class

from hwp_errors import HwpLiveError
from hwp_live_contract import OpenDocument
from hwp_operation_contract import HwpOperateGuards, HwpOperateInputs, OperationResult
from hwp_operation_journal import (
    OperationJournal,
    OperationJournalError,
    document_session_key,
    operation_result_digest,
    request_payload_digest,
)
from hwp_operation_journal_contract import JournalDecision, JournalDecisionKind
from hwp_operation_registry import operation_registry


@dataclass(frozen=True, slots=True)
class OperationTicket:
    document_session: str
    request_id: str
    action: Literal["execute", "reconcile"]


@final
class OperationIdempotency:
    __slots__ = ("_journal",)

    def __init__(self, journal: OperationJournal) -> None:
        self._journal = journal

    @staticmethod
    def _blocked_result(
        decision: JournalDecision,
        request_id: str,
        intent: str,
    ) -> OperationResult:
        kind: JournalDecisionKind = decision.kind
        match kind:
            case "in_progress":
                status = "operation_in_progress"
                idempotency_status = "in_progress"
                message = "동일 request_id 작업이 현재 실행 중이므로 중복 실행하지 않았습니다"
            case "stale":
                status = "operation_stale"
                idempotency_status = "stale"
                message = "작업 heartbeat가 만료됐습니다. 사용자 확인 후 recovery.action을 지정하세요"
            case "failed":
                status = "operation_failed"
                idempotency_status = "failed"
                message = "이전 작업이 실패했습니다. 사용자 확인 후 recover 또는 reconcile을 지정하세요"
            case "aborted":
                status = "operation_aborted"
                idempotency_status = "aborted"
                message = "이전 작업이 중단됐습니다. 사용자 확인 후 recover 또는 reconcile을 지정하세요"
            case "conflict":
                status = "request_id_conflict"
                idempotency_status = "conflict"
                message = "동일 request_id에 다른 요청 내용이 들어와 실행하지 않았습니다"
            case "execute" | "replay" | "reconcile":
                raise OperationJournalError("executable journal decision reached blocked result")
        return OperationResult(
            request_id=request_id,
            idempotency_status=idempotency_status,
            journal_state=decision.state,
            journal_attempt=decision.attempt,
            started_at=decision.started_at,
            updated_at=decision.updated_at,
            stale_after_seconds=decision.stale_after_seconds,
            journal_failure_code=decision.failure_code,
            result_digest=decision.result_digest,
            status=status,
            query=intent,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            message=message,
            structure_digest_before=decision.structure_digest_before,
            structure_digest_after=decision.structure_digest_after,
            partial_mutation=decision.partial_mutation,
            retry_safe=decision.retry_safe,
            failed_step=decision.failed_step,
            commands_completed=decision.commands_completed,
            resolved_target_id=decision.resolved_target_id,
            target_resolution_basis=decision.target_resolution_basis,
        )

    def prepare(
        self,
        document: OpenDocument,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationTicket | OperationResult | None:
        request_id = inputs.request_id
        if request_id is None:
            return None
        session = document_session_key(document)
        decision = self._journal.begin(
            session,
            request_id,
            request_payload_digest(intent, inputs, guards),
            inputs.recovery,
        )
        match decision.kind:
            case "execute":
                self._journal.mark_executing(session, request_id)
                return OperationTicket(session, request_id, "execute")
            case "reconcile":
                return OperationTicket(session, request_id, "reconcile")
            case "replay":
                if decision.result is None:
                    raise OperationJournalError("replay decision has no operation result")
                return decision.result.model_copy(
                    update={
                        "idempotency_status": "replayed",
                        "result_digest": decision.result_digest,
                        "journal_state": decision.state,
                        "journal_attempt": decision.attempt,
                        "started_at": decision.started_at,
                        "updated_at": decision.updated_at,
                        "stale_after_seconds": decision.stale_after_seconds,
                        "journal_failure_code": decision.failure_code,
                    }
                )
            case "in_progress" | "stale" | "failed" | "aborted" | "conflict":
                return self._blocked_result(decision, request_id, intent)

    def _heartbeat(self, ticket: OperationTicket, stop: Event) -> None:
        while not stop.wait(self._journal.heartbeat_interval_seconds):
            self._journal.heartbeat(ticket.document_session, ticket.request_id)

    @contextmanager
    def execution(self, ticket: OperationTicket | None) -> Generator[None]:
        if ticket is None or ticket.action == "reconcile":
            yield
            return
        stop = Event()
        heartbeat = Thread(
            target=self._heartbeat,
            args=(ticket, stop),
            name=f"HancomJournalHeartbeat-{ticket.request_id}",
            daemon=True,
        )
        heartbeat.start()
        cancelled_error = get_cancelled_exc_class()
        try:
            yield
        except cancelled_error:
            self._journal.mark_aborted(
                ticket.document_session,
                ticket.request_id,
                "client_cancelled",
            )
            raise
        except HwpLiveError as error:
            self._journal.mark_failed(
                ticket.document_session,
                ticket.request_id,
                type(error).__name__,
            )
            raise
        finally:
            stop.set()
            heartbeat.join()

    def commit(
        self,
        ticket: OperationTicket | None,
        result: OperationResult,
    ) -> OperationResult:
        if ticket is None:
            return result
        if ticket.action == "reconcile":
            snapshot = self._journal.snapshot(ticket.document_session, ticket.request_id)
            result = snapshot.preserve_result_evidence(result)
            return result.model_copy(
                update={
                    "request_id": ticket.request_id,
                    "idempotency_status": "reconciled",
                    "journal_state": snapshot.state,
                    "journal_attempt": snapshot.attempt,
                    "started_at": snapshot.started_at,
                    "updated_at": snapshot.updated_at,
                    "stale_after_seconds": self._journal.stale_after_seconds,
                    "journal_failure_code": snapshot.failure_code,
                    "status": "operation_reconciled",
                    "message": "이전 시도를 중단 상태로 확정하고 네이티브 resolve/snapshot만 수행했습니다",
                }
            )
        if result.status == "operation_failed":
            failed = result.model_copy(
                update={
                    "request_id": ticket.request_id,
                    "idempotency_status": "failed",
                }
            )
            digest = operation_result_digest(failed)
            failed = failed.model_copy(update={"result_digest": digest})
            self._journal.mark_failed(
                ticket.document_session,
                ticket.request_id,
                "native_verification_failed",
                result=failed,
                result_digest=digest,
            )
            snapshot = self._journal.snapshot(ticket.document_session, ticket.request_id)
            return failed.model_copy(
                update={
                    "journal_state": snapshot.state,
                    "journal_attempt": snapshot.attempt,
                    "started_at": snapshot.started_at,
                    "updated_at": snapshot.updated_at,
                    "stale_after_seconds": self._journal.stale_after_seconds,
                    "journal_failure_code": snapshot.failure_code,
                }
            )
        committed = result.model_copy(
            update={
                "request_id": ticket.request_id,
                "idempotency_status": "committed",
            }
        )
        digest = operation_result_digest(committed)
        committed = committed.model_copy(update={"result_digest": digest})
        self._journal.mark_verified(
            ticket.document_session,
            ticket.request_id,
            committed,
            digest,
        )
        self._journal.mark_committed(ticket.document_session, ticket.request_id)
        snapshot = self._journal.snapshot(ticket.document_session, ticket.request_id)
        return committed.model_copy(
            update={
                "journal_state": snapshot.state,
                "journal_attempt": snapshot.attempt,
                "started_at": snapshot.started_at,
                "updated_at": snapshot.updated_at,
                "stale_after_seconds": self._journal.stale_after_seconds,
                "journal_failure_code": snapshot.failure_code,
            }
        )

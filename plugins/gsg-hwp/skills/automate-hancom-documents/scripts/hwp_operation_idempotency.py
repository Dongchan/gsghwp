from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — idempotency decisions and journal finalization share one state machine.

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Event, Thread
from typing import Final, Literal, final

from anyio import CancelScope, get_cancelled_exc_class, to_thread

from hwp_errors import HwpLiveError
from hwp_live_contract import OpenDocument
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
    canonical_workflow,
)
from hwp_operation_journal import (
    JournalRequestLookup,
    OperationJournal,
    OperationJournalError,
    document_session_key,
    operation_result_digest,
    request_payload_digest,
)
from hwp_operation_journal_contract import JournalDecision, JournalDecisionKind
from hwp_operation_registry import operation_registry
from hwp_save_fingerprint import (
    SaveFileFingerprint,
    capture_save_file_fingerprint,
    reconcile_save_fingerprint,
    save_baseline_diagnostic_reason,
)


_SAVE_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {"document.save", "document.save_reopen_verify"}
)
_MISSING_VERIFICATION_MARKER: Final = "[verification=evidence_unavailable]"
_MISSING_VERIFICATION_NOTICE: Final = (
    f"{_MISSING_VERIFICATION_MARKER} 네이티브 명령은 실행됐고 문서는 변경됐지만, "
    "이 선택 범위·인자 조합에서는 결과를 되읽어 증명할 방법이 없어 검증을 "
    "생략했습니다. 검증이 불일치한 것이 아니므로 되돌리거나 다시 적용하지 "
    "마세요. 확인이 필요하면 hwp_inspect로 결과를 직접 읽으세요"
)
# OperationResult.message is capped at 4_000 characters by the contract.
_MESSAGE_LIMIT: Final = 4_000


@dataclass(frozen=True, slots=True)
class OperationTicket:
    document_session: str | None
    request_id: str | None
    action: Literal["execute", "reconcile"]
    operation: str | None = None
    document_path: str | None = None
    save_fingerprint_before: SaveFileFingerprint | None = None


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
        match kind:  # noqa: E501  # noqa: MATCH_OK — JournalDecisionKind is exhaustive.
            case "in_progress":
                status = "operation_in_progress"
                idempotency_status = "in_progress"
                message = (
                    "동일 request_id 작업이 현재 실행 중이므로 중복 실행하지 않았습니다"
                )
                preserved = None
            case "stale":
                status = "operation_stale"
                idempotency_status = "stale"
                message = "작업 heartbeat가 만료됐습니다. 사용자 확인 후 recovery.action을 지정하세요"
                preserved = decision.result
            case "failed":
                status = "operation_failed"
                idempotency_status = "failed"
                message = "이전 작업이 실패했습니다. 사용자 확인 후 recover 또는 reconcile을 지정하세요"
                preserved = decision.result
            case "aborted":
                status = "operation_aborted"
                idempotency_status = "aborted"
                message = "이전 작업이 중단됐습니다. 사용자 확인 후 recover 또는 reconcile을 지정하세요"
                preserved = decision.result
            case "conflict":
                status = "request_id_conflict"
                idempotency_status = "conflict"
                message = (
                    "동일 request_id에 다른 요청 내용이 들어와 실행하지 않았습니다"
                )
                preserved = None
            case "execute" | "replay" | "reconcile":
                raise OperationJournalError(
                    "executable journal decision reached blocked result"
                )
        if preserved is not None:
            preserve_partial = kind == "failed" and preserved.status == "partial_change"
            updated = preserved.model_copy(
                update={
                    "request_id": request_id,
                    "idempotency_status": idempotency_status,
                    "journal_state": decision.state,
                    "journal_attempt": decision.attempt,
                    "started_at": decision.started_at,
                    "updated_at": decision.updated_at,
                    "stale_after_seconds": decision.stale_after_seconds,
                    "journal_failure_code": decision.failure_code,
                    "status": preserved.status if preserve_partial else status,
                    "query": intent,
                    "message": preserved.message if preserve_partial else message,
                }
            )
            return updated.model_copy(
                update={"result_digest": operation_result_digest(updated)}
            )
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

    @staticmethod
    def _replayed_result(decision: JournalDecision) -> OperationResult:
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

    @staticmethod
    def _lookup_failure_result(
        request_id: str,
        lookup: JournalRequestLookup,
    ) -> OperationResult:
        issue_states = {issue.state for issue in lookup.issues}
        if len(lookup.decisions) > 1:
            failure_code = "journal_lookup_ambiguous"
            detail = "같은 operation_id에 여러 저널 레코드가 매칭됐습니다"
        elif issue_states == {"unreadable"}:
            failure_code = "journal_lookup_unreadable"
            detail = "일치 가능성이 있는 저널 레코드를 읽을 수 없습니다"
        elif issue_states == {"incompatible"}:
            failure_code = "journal_lookup_incompatible"
            detail = "일치하는 저널 레코드가 현재 모델과 호환되지 않습니다"
        else:
            failure_code = "journal_lookup_unreadable_or_incompatible"
            detail = (
                "일치 가능성이 있는 저널 레코드가 unreadable/incompatible 상태입니다"
            )
        return OperationResult(
            request_id=request_id,
            idempotency_status="failed",
            journal_failure_code=failure_code,
            status="transport_error",
            query="operation status",
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            failure_stage="operation_journal_lookup",
            message=(
                f"{detail}. 원본 저널을 변경하지 않았으며 작업의 성공·실패를 "
                "판정할 수 없습니다. operation_id를 재사용하지 말고 수동으로 "
                "저장·문서 상태를 확인하세요"
            ),
            verified=False,
            retry_safe=False,
            reconcile_required=True,
        )

    @staticmethod
    def _disclose_missing_verification(result: OperationResult) -> OperationResult:
        # `verified` stays None so the response never claims a proof it does not
        # have. The public schema projects `verified` down to a strict bool, so
        # the message is the only channel that can tell the caller the
        # difference between "readback disagreed" and "no readback existed".
        if _MISSING_VERIFICATION_MARKER in result.message:
            return result
        budget = _MESSAGE_LIMIT - len(_MISSING_VERIFICATION_NOTICE) - 1
        head = result.message[:budget]
        return result.model_copy(
            update={"message": f"{head} {_MISSING_VERIFICATION_NOTICE}"}
        )

    @staticmethod
    def _with_response_digest(result: OperationResult) -> OperationResult:
        return result.model_copy(
            update={"result_digest": operation_result_digest(result)}
        )

    def prepare(
        self,
        document: OpenDocument,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationTicket | OperationResult | None:
        request_id = inputs.request_id
        workflow = canonical_workflow(inputs)
        save_fingerprint_before = (
            capture_save_file_fingerprint(document.full_name)
            if workflow in {"document.save", "document.save_reopen_verify"}
            else None
        )
        if request_id is None:
            if save_fingerprint_before is None:
                return None
            # The generic QA operation can omit request_id even though the
            # production save tools require one. Keep an in-memory context so
            # its immediate response gets evidence without creating a journal.
            return OperationTicket(
                None,
                None,
                "execute",
                workflow,
                document.full_name,
                save_fingerprint_before,
            )
        session = document_session_key(document)
        decision = self._journal.begin(
            session,
            request_id,
            request_payload_digest(intent, inputs, guards),
            inputs.recovery,
            document_path=document.full_name,
            operation=workflow,
            save_fingerprint_before=save_fingerprint_before,
        )
        match decision.kind:  # noqa: E501  # noqa: MATCH_OK — JournalDecisionKind is exhaustive.
            case "execute":
                self._journal.mark_executing(session, request_id)
                return OperationTicket(
                    session,
                    request_id,
                    "execute",
                    decision.operation,
                    decision.document_path,
                    decision.save_fingerprint_before,
                )
            case "reconcile":
                return OperationTicket(
                    session,
                    request_id,
                    "reconcile",
                    decision.operation,
                    decision.document_path,
                    decision.save_fingerprint_before,
                )
            case "replay":
                return self._replayed_result(decision)
            case "in_progress" | "stale" | "failed" | "aborted" | "conflict":
                return self._blocked_result(decision, request_id, intent)

    def status(
        self,
        document: OpenDocument,
        request_id: str,
    ) -> OperationResult:
        decision = self._journal.lookup(document_session_key(document), request_id)
        if decision is None:
            return OperationResult(
                request_id=request_id,
                status="not_found",
                query="operation status",
                registry_entries=operation_registry().count,
                lookup_microseconds=0,
                message=(
                    "해당 operation_id의 작업 기록이 없거나 보존 한도에 따라 "
                    "만료되었습니다. 이 ID를 새 작업에 재사용하지 마세요"
                ),
            )
        return self._status_decision(decision, request_id)

    def status_without_connection(
        self,
        request_id: str,
        document_path: str | None,
    ) -> OperationResult | None:
        lookup = self._journal.lookup_request_with_issues(
            request_id,
            document_path,
        )
        matching_issues = tuple(
            issue for issue in lookup.issues if issue.match == "matched"
        )
        if len(lookup.decisions) == 0 and not lookup.issues:
            return None
        if len(lookup.decisions) != 1 or matching_issues:
            return self._lookup_failure_result(request_id, lookup)
        decision = lookup.decisions[0]
        if (
            decision.operation in {"document.save", "document.save_reopen_verify"}
            and decision.document_path is not None
            and decision.kind in {"failed", "replay"}
        ):
            reconciled, disposition = reconcile_save_fingerprint(
                decision.result,
                before=decision.save_fingerprint_before,
                after=capture_save_file_fingerprint(decision.document_path),
            )
            if decision.kind == "replay":
                replayed = self._replayed_result(decision)
                if disposition == "confirmed":
                    updated = reconciled.model_copy(
                        update={
                            "request_id": request_id,
                            "idempotency_status": replayed.idempotency_status,
                            "journal_state": replayed.journal_state,
                            "journal_attempt": replayed.journal_attempt,
                            "started_at": replayed.started_at,
                            "updated_at": replayed.updated_at,
                            "stale_after_seconds": replayed.stale_after_seconds,
                            "journal_failure_code": replayed.journal_failure_code,
                        }
                    )
                    return self._with_response_digest(updated)
                return replayed
            if disposition in {"confirmed", "likely"}:
                updated = reconciled.model_copy(
                    update={
                        "request_id": request_id,
                        "idempotency_status": (
                            "committed" if disposition == "confirmed" else "failed"
                        ),
                    }
                )
                digest = operation_result_digest(updated)
                updated = updated.model_copy(update={"result_digest": digest})
                self._journal.record_save_reconciliation(
                    decision.document_session,
                    request_id,
                    updated,
                    digest,
                    confirmed=disposition == "confirmed",
                )
                refreshed = self._journal.lookup(
                    decision.document_session,
                    request_id,
                )
                if refreshed is None:
                    raise OperationJournalError(
                        "save reconciliation journal entry disappeared"
                    )
                return self._status_decision(refreshed, request_id)
            updated = reconciled.model_copy(
                update={
                    "request_id": request_id,
                    "idempotency_status": "failed",
                    "journal_state": decision.state,
                    "journal_attempt": decision.attempt,
                    "started_at": decision.started_at,
                    "updated_at": decision.updated_at,
                    "stale_after_seconds": decision.stale_after_seconds,
                    "journal_failure_code": decision.failure_code,
                }
            )
            return self._with_response_digest(updated)
        return self._status_decision(decision, request_id)

    def _status_decision(
        self,
        decision: JournalDecision,
        request_id: str,
    ) -> OperationResult:
        match decision.kind:  # noqa: E501  # noqa: MATCH_OK — observable decision kinds are exhaustive.
            case "replay":
                return self._replayed_result(decision)
            case "in_progress" | "stale" | "failed" | "aborted":
                return self._blocked_result(
                    decision,
                    request_id,
                    "operation status",
                )
            case "execute" | "reconcile" | "conflict":
                raise OperationJournalError(
                    "non-observable journal decision reached status lookup"
                )

    def _heartbeat(self, ticket: OperationTicket, stop: Event) -> None:
        document_session = ticket.document_session
        request_id = ticket.request_id
        if document_session is None or request_id is None:
            return
        while not stop.wait(self._journal.heartbeat_interval_seconds):
            self._journal.heartbeat(document_session, request_id)

    @asynccontextmanager
    async def execution(
        self,
        ticket: OperationTicket | None,
    ) -> AsyncGenerator[None]:
        if (
            ticket is None
            or ticket.action == "reconcile"
            or ticket.document_session is None
            or ticket.request_id is None
        ):
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
            with CancelScope(shield=True):
                await to_thread.run_sync(
                    self._journal.mark_aborted,
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
            with CancelScope(shield=True):
                await to_thread.run_sync(heartbeat.join)

    @staticmethod
    def _attach_successful_save_fingerprint(
        ticket: OperationTicket,
        result: OperationResult,
    ) -> OperationResult:
        if (
            ticket.action != "execute"
            or ticket.operation not in {"document.save", "document.save_reopen_verify"}
            or ticket.document_path is None
        ):
            return result
        if result.status != "executed":
            before = ticket.save_fingerprint_before
            baseline_evidence = {
                "save_baseline_file_size": None if before is None else before.size,
                "save_baseline_file_mtime_ns": (
                    None if before is None else before.mtime_ns
                ),
                "save_baseline_sha256": None if before is None else before.sha256,
            }
            baseline_reason = save_baseline_diagnostic_reason(
                before,
                attached=True,
            )
            baseline_evidence["save_baseline_diagnostic_reason"] = baseline_reason
            if baseline_reason is None:
                return result.model_copy(update=baseline_evidence)
            marker = f"[save_baseline_reason={baseline_reason}]"
            return result.model_copy(
                update={
                    **baseline_evidence,
                    "failure_stage": (
                        result.failure_stage or f"save_baseline_{baseline_reason}"
                    ),
                    "message": (
                        result.message
                        if marker in result.message
                        else f"{result.message} {marker}"
                    ),
                }
            )
        reconciled, disposition = reconcile_save_fingerprint(
            result,
            before=ticket.save_fingerprint_before,
            after=capture_save_file_fingerprint(ticket.document_path),
        )
        if disposition == "confirmed":
            return reconciled.model_copy(
                update={
                    "query": result.query,
                    "message": result.message,
                }
            )
        if (
            ticket.operation == "document.save_reopen_verify"
            and result.disk_persistence_verified is True
        ):
            # Protocol 12 lifecycle results do not expose native file size and
            # mtime. Preserve the stronger native reopen verdict, but do not
            # claim that the separate six-condition fingerprint proof passed.
            return result.model_copy(
                update={
                    "save_baseline_file_size": (reconciled.save_baseline_file_size),
                    "save_baseline_file_mtime_ns": (
                        reconciled.save_baseline_file_mtime_ns
                    ),
                    "save_baseline_sha256": reconciled.save_baseline_sha256,
                    "saved_file_size": reconciled.saved_file_size,
                    "saved_file_mtime_ns": reconciled.saved_file_mtime_ns,
                    "saved_file_sha256": reconciled.saved_file_sha256,
                    "save_fingerprint_stable": (reconciled.save_fingerprint_stable),
                    "save_fingerprint_changed": (reconciled.save_fingerprint_changed),
                    "save_fingerprint_verified": False,
                }
            )
        return reconciled

    def commit(
        self,
        ticket: OperationTicket | None,
        result: OperationResult,
    ) -> OperationResult:
        if ticket is None:
            return result
        if ticket.action == "reconcile":
            if ticket.document_session is None or ticket.request_id is None:
                raise OperationJournalError("reconcile ticket has no journal identity")
            snapshot = self._journal.snapshot(
                ticket.document_session, ticket.request_id
            )
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
        result = self._attach_successful_save_fingerprint(ticket, result)
        if ticket.document_session is None or ticket.request_id is None:
            if result.status == "executed" and result.verified is not True:
                return result.model_copy(
                    update={
                        "status": "operation_failed",
                        "retry_safe": False,
                    }
                )
            return result
        # `verified` is tri-state. False means the operation read the result
        # back and it disagreed — a real defect. None only means the operation
        # had no way to prove what it did (a selection spanning paragraphs,
        # cells or controls; an image.replace without a size). The native
        # command still ran and the document still changed, so demoting None to
        # operation_failed reported a success as a failure and pushed callers
        # into undo. Save workflows keep the stricter `is not True` rule: their
        # verdict comes from the file fingerprint, and the save_reopen_verify
        # branch of _attach_successful_save_fingerprint can carry a None here.
        save_workflow = ticket.operation in _SAVE_WORKFLOWS
        unverified_execution = (
            result.status == "executed" and result.verified is not True
        )
        # Narrow entry gate: missing evidence alone no longer opens the failure
        # branch. Everything that does enter is handled exactly as before, so a
        # result that already carried reconcile_required, partial_mutation or a
        # failure status keeps its original verdict.
        verification_failed = result.status == "executed" and (
            result.verified is False or (save_workflow and result.verified is None)
        )
        missing_verification_evidence = (
            result.status == "executed"
            and result.verified is None
            and not save_workflow
        )
        if (
            result.status in {"operation_failed", "partial_change", "transport_error"}
            or result.reconcile_required
            or result.partial_mutation is True
            or verification_failed
        ):
            reconcile_required = (
                result.reconcile_required
                or result.partial_mutation is True
                or result.status == "partial_change"
                or (unverified_execution and result.changed)
            )
            failure_code = (
                "partial_mutation_reconcile_required"
                if reconcile_required
                else "transport_error"
                if result.status == "transport_error"
                else "unverified_operation_result"
                if unverified_execution
                else "native_verification_failed"
            )
            failed = result.model_copy(
                update={
                    "request_id": ticket.request_id,
                    "idempotency_status": "failed",
                    "status": (
                        "operation_failed" if unverified_execution else result.status
                    ),
                    "verified": False,
                    "reconcile_required": reconcile_required,
                    "retry_safe": (False if reconcile_required else result.retry_safe),
                }
            )
            digest = operation_result_digest(failed)
            failed = failed.model_copy(update={"result_digest": digest})
            self._journal.mark_failed(
                ticket.document_session,
                ticket.request_id,
                failure_code,
                result=failed,
                result_digest=digest,
            )
            snapshot = self._journal.snapshot(
                ticket.document_session, ticket.request_id
            )
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
        if missing_verification_evidence:
            # retry_safe is deliberately left as the operation reported it.
            # Forcing False emits the same signal as a reconcile-required
            # failure and invites undo; forcing True invites re-applying an
            # edit that already landed. Successful results leave it None, which
            # correctly reads as "no retry recommendation".
            result = self._disclose_missing_verification(result)
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

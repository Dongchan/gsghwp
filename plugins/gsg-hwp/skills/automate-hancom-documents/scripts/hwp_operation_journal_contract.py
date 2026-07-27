from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from os import environ
from pathlib import Path
from typing import Literal

from pydantic import Field

from hwp_live_contract import OpenDocument
from hwp_live_values import ContractModel
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    OperationJournalState,
    OperationResult,
)
from hwp_save_fingerprint import SaveFileFingerprint


type JournalDecisionKind = Literal[
    "execute",
    "replay",
    "in_progress",
    "stale",
    "failed",
    "aborted",
    "reconcile",
    "conflict",
]


class OperationJournalError(RuntimeError):
    pass


class JournalAttemptSnapshot(ContractModel):
    attempt: int = Field(ge=1)
    state: OperationJournalState
    started_at: datetime
    updated_at: datetime
    heartbeat_at: datetime
    failure_code: str | None = Field(default=None, min_length=1, max_length=200)
    structure_digest_before: str | None = Field(default=None, max_length=500)
    structure_digest_after: str | None = Field(default=None, max_length=500)
    partial_mutation: bool | None = None
    retry_safe: bool | None = None
    failed_step: str | None = Field(default=None, max_length=200)
    commands_completed: int | None = Field(default=None, ge=0)
    resolved_target_id: str | None = Field(default=None, max_length=500)
    target_resolution_basis: str | None = Field(default=None, max_length=500)


class JournalSnapshot(JournalAttemptSnapshot):
    previous_attempts: tuple[JournalAttemptSnapshot, ...] = ()

    def preserve_result_evidence(self, result: OperationResult) -> OperationResult:
        if (
            self.structure_digest_before is not None
            and result.structure_digest_before is None
        ):
            result = result.model_copy(
                update={"structure_digest_before": self.structure_digest_before}
            )
        if (
            self.structure_digest_after is not None
            and result.structure_digest_after is None
        ):
            result = result.model_copy(
                update={"structure_digest_after": self.structure_digest_after}
            )
        if self.partial_mutation is not None and result.partial_mutation is None:
            result = result.model_copy(
                update={"partial_mutation": self.partial_mutation}
            )
        if self.retry_safe is not None and result.retry_safe is None:
            result = result.model_copy(update={"retry_safe": self.retry_safe})
        if self.failed_step is not None and result.failed_step is None:
            result = result.model_copy(update={"failed_step": self.failed_step})
        if self.commands_completed is not None and result.commands_completed is None:
            result = result.model_copy(
                update={"commands_completed": self.commands_completed}
            )
        if self.resolved_target_id is not None and result.resolved_target_id is None:
            result = result.model_copy(
                update={"resolved_target_id": self.resolved_target_id}
            )
        if (
            self.target_resolution_basis is not None
            and result.target_resolution_basis is None
        ):
            result = result.model_copy(
                update={"target_resolution_basis": self.target_resolution_basis}
            )
        return result


@dataclass(frozen=True, slots=True)
class JournalDecision:
    kind: JournalDecisionKind
    document_session: str
    request_digest: str
    state: OperationJournalState
    attempt: int
    started_at: datetime
    updated_at: datetime
    stale_after_seconds: int
    failure_code: str | None = None
    result: OperationResult | None = None
    result_digest: str | None = None
    structure_digest_before: str | None = None
    structure_digest_after: str | None = None
    partial_mutation: bool | None = None
    retry_safe: bool | None = None
    failed_step: str | None = None
    commands_completed: int | None = None
    resolved_target_id: str | None = None
    target_resolution_basis: str | None = None
    request_id: str | None = None
    document_path: str | None = None
    operation: str | None = None
    save_fingerprint_before: SaveFileFingerprint | None = None


class JournalRecord(ContractModel):
    request_digest: str
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_session: str | None = Field(default=None, min_length=1, max_length=500)
    document_path: str | None = Field(default=None, max_length=32_767)
    operation: str | None = Field(default=None, min_length=1, max_length=100)
    save_fingerprint_before: SaveFileFingerprint | None = None
    attempt: int = Field(default=1, ge=1)
    state: OperationJournalState
    started_at: datetime
    updated_at: datetime
    heartbeat_at: datetime
    failure_code: str | None = Field(default=None, min_length=1, max_length=200)
    previous_attempts: tuple[JournalAttemptSnapshot, ...] = ()
    result: OperationResult | None = None
    result_changed_pages: tuple[int, ...] = Field(default=(), max_length=20_000)
    result_digest: str | None = None
    structure_digest_before: str | None = Field(default=None, max_length=500)
    structure_digest_after: str | None = Field(default=None, max_length=500)
    partial_mutation: bool | None = None
    retry_safe: bool | None = None
    failed_step: str | None = Field(default=None, max_length=200)
    commands_completed: int | None = Field(default=None, ge=0)
    resolved_target_id: str | None = Field(default=None, max_length=500)
    target_resolution_basis: str | None = Field(default=None, max_length=500)

    def with_result(
        self,
        result: OperationResult,
        result_digest: str | None,
    ) -> JournalRecord:
        return self.model_copy(
            update={
                "result": result,
                "result_changed_pages": result.changed_pages,
                "result_digest": result_digest,
                "structure_digest_before": result.structure_digest_before,
                "structure_digest_after": result.structure_digest_after,
                "partial_mutation": result.partial_mutation,
                "retry_safe": result.retry_safe,
                "failed_step": result.failed_step,
                "commands_completed": result.commands_completed,
                "resolved_target_id": result.resolved_target_id,
                "target_resolution_basis": result.target_resolution_basis,
            }
        )

    def snapshot(self) -> JournalSnapshot:
        return JournalSnapshot(
            attempt=self.attempt,
            state=self.state,
            started_at=self.started_at,
            updated_at=self.updated_at,
            heartbeat_at=self.heartbeat_at,
            failure_code=self.failure_code,
            structure_digest_before=self.structure_digest_before,
            structure_digest_after=self.structure_digest_after,
            partial_mutation=self.partial_mutation,
            retry_safe=self.retry_safe,
            failed_step=self.failed_step,
            commands_completed=self.commands_completed,
            resolved_target_id=self.resolved_target_id,
            target_resolution_basis=self.target_resolution_basis,
            previous_attempts=self.previous_attempts,
        )

    def attempt_snapshot(self) -> JournalAttemptSnapshot:
        return JournalAttemptSnapshot(
            attempt=self.attempt,
            state=self.state,
            started_at=self.started_at,
            updated_at=self.updated_at,
            heartbeat_at=self.heartbeat_at,
            failure_code=self.failure_code,
            structure_digest_before=self.structure_digest_before,
            structure_digest_after=self.structure_digest_after,
            partial_mutation=self.partial_mutation,
            retry_safe=self.retry_safe,
            failed_step=self.failed_step,
            commands_completed=self.commands_completed,
            resolved_target_id=self.resolved_target_id,
            target_resolution_basis=self.target_resolution_basis,
        )

    def decision(
        self,
        kind: JournalDecisionKind,
        document_session: str,
        stale_after_seconds: int,
    ) -> JournalDecision:
        result = (
            None
            if self.result is None
            else self.result.model_copy(
                update={"changed_pages": self.result_changed_pages}
            )
        )
        return JournalDecision(
            kind=kind,
            document_session=document_session,
            request_digest=self.request_digest,
            state=self.state,
            attempt=self.attempt,
            started_at=self.started_at,
            updated_at=self.updated_at,
            stale_after_seconds=stale_after_seconds,
            failure_code=self.failure_code,
            result=result,
            result_digest=self.result_digest,
            structure_digest_before=self.structure_digest_before,
            structure_digest_after=self.structure_digest_after,
            partial_mutation=self.partial_mutation,
            retry_safe=self.retry_safe,
            failed_step=self.failed_step,
            commands_completed=self.commands_completed,
            resolved_target_id=self.resolved_target_id,
            target_resolution_basis=self.target_resolution_basis,
            request_id=self.request_id,
            document_path=self.document_path,
            operation=self.operation,
            save_fingerprint_before=self.save_fingerprint_before,
        )


def default_operation_journal_path() -> Path:
    local_app_data = environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "HancomDocumentAutomation" / "operation-journal-v2"


def document_session_key(document: OpenDocument) -> str:
    identity = "\0".join(
        (
            str(document.window_handle),
            str(document.document_id),
            document.full_name.replace("/", "\\").casefold(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def request_payload_digest(
    intent: str,
    inputs: HwpOperateInputs,
    guards: HwpOperateGuards | None,
) -> str:
    payload = {
        "intent": intent,
        "inputs": inputs.model_dump(
            mode="json",
            exclude={"request_id", "recovery"},
        ),
        "guards": None if guards is None else guards.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def operation_result_digest(result: OperationResult) -> str:
    payload = result.model_dump(
        mode="json",
        exclude={
            "idempotency_status",
            "result_digest",
            "journal_state",
            "journal_attempt",
            "started_at",
            "updated_at",
            "stale_after_seconds",
            "journal_failure_code",
        },
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

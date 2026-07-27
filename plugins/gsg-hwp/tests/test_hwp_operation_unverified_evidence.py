"""Missing verification evidence must not be reported as a failed operation.

`OperationResult.verified` is tri-state:

* ``True``  - the operation read the result back and it matched.
* ``False`` - the operation read the result back and it disagreed. A real defect.
* ``None``  - the operation could not collect evidence at all. The native
  command still ran and the document still changed.

``commit()`` used to collapse ``None`` into ``False`` via ``verified is not True``,
which turned a successful edit into ``operation_failed`` + ``reconcile_required``
and pushed the calling model toward ``undo``. These tests pin the split, and pin
that every genuinely dangerous path is left exactly as it was.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_contract import OpenDocument  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateInputs,
    OperationResult,
)
from hwp_operation_idempotency import (  # noqa: E402
    OperationIdempotency,
    OperationTicket,
)
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_public_contract import to_public_action_result  # noqa: E402


_EVIDENCE_MARKER = "[verification=evidence_unavailable]"
_WINDOWS_FILETIME_EPOCH = 116_444_736_000_000_000


def _document() -> OpenDocument:
    return OpenDocument(
        selector="document-1",
        title="sample.hwp",
        full_name="C:/documents/sample.hwp",
        document_id=1,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=1,
        active=True,
        window_handle=100,
    )


def _save_document(path: Path) -> OpenDocument:
    return OpenDocument(
        selector="save-document",
        title=path.name,
        full_name=str(path),
        document_id=7,
        format="HWP",
        edit_mode=1,
        modified=True,
        page_count=1,
        active=True,
        window_handle=7001,
    )


def _executed(
    *,
    verified: bool | None,
    status: str = "executed",
    changed: bool = True,
    **overrides: object,
) -> OperationResult:
    payload: dict[str, object] = {
        "status": status,
        "changed": changed,
        "query": "스타일 적용",
        "registry_entries": 1,
        "lookup_microseconds": 0,
        "message": "프로토콜 9 C++/ATL 네이티브 개체 recipe를 실행했습니다",
        "verification": "native_snapshot_before_after",
        "verified": verified,
    }
    payload.update(overrides)
    return OperationResult.model_validate(payload)


def _save_result(path: Path, *, verified: bool | None) -> OperationResult:
    stat = path.stat()
    return OperationResult(
        status="executed",
        changed=True,
        query="save",
        registry_entries=1,
        lookup_microseconds=0,
        message="saved",
        verification="native_save_result",
        verified=verified,
        modified=False,
        saved_path=str(path),
        before_modified=True,
        save_hresult=0,
        save_return=1,
        post_save_modified=False,
        saved_file_size=stat.st_size,
        saved_file_write_time_100ns=(
            stat.st_mtime_ns // 100 + _WINDOWS_FILETIME_EPOCH
        ),
        live_state_preserved_after_save=True,
        disk_persistence_verified=False,
        partial_mutation=False,
        retry_safe=True,
        reconcile_required=False,
    )


def _commit(
    tmp_path: Path,
    result: OperationResult,
    *,
    operation: str = "style.apply",
    request_id: str = "operation-1",
    document: OpenDocument | None = None,
) -> OperationResult:
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    open_document = _document() if document is None else document
    prepared = idempotency.prepare(
        open_document,
        "스타일 적용",
        HwpOperateInputs(
            request_id=request_id,
            document=open_document.full_name,
            operation=operation,
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)
    return idempotency.commit(prepared, result)


# --------------------------------------------------------------------------
# Core: missing evidence is not a failure.
# --------------------------------------------------------------------------


def test_executed_without_verification_evidence_is_not_demoted(
    tmp_path: Path,
) -> None:
    committed = _commit(tmp_path, _executed(verified=None))

    assert committed.status == "executed"
    assert committed.idempotency_status == "committed"
    assert committed.reconcile_required is False
    assert committed.journal_failure_code is None


def test_missing_evidence_keeps_verified_none_rather_than_claiming_success(
    tmp_path: Path,
) -> None:
    committed = _commit(tmp_path, _executed(verified=None))

    # Neither "proven" (True) nor "disagreed" (False).
    assert committed.verified is None


def test_missing_evidence_is_disclosed_in_the_message(tmp_path: Path) -> None:
    committed = _commit(tmp_path, _executed(verified=None))

    assert _EVIDENCE_MARKER in committed.message
    # The original operation message is preserved, not replaced.
    assert "네이티브 개체 recipe를 실행했습니다" in committed.message


def test_missing_evidence_disclosure_reaches_the_public_result(
    tmp_path: Path,
) -> None:
    committed = _commit(tmp_path, _executed(verified=None))
    public = to_public_action_result(committed, ())

    # `verified` is a strict bool in the public schema, so the message is the
    # only channel that can carry "evidence was unavailable" to the model.
    assert public.verified is False
    assert _EVIDENCE_MARKER in public.message


def test_missing_evidence_does_not_assert_a_retry_recommendation(
    tmp_path: Path,
) -> None:
    committed = _commit(tmp_path, _executed(verified=None))

    # Not False (that reads as "recovery needed" and invites undo) and not True
    # (that invites re-applying an edit which already landed).
    assert committed.retry_safe is None


def test_missing_evidence_disclosure_is_not_duplicated_on_replay(
    tmp_path: Path,
) -> None:
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    document = _document()
    inputs = HwpOperateInputs(
        request_id="replayed-operation",
        document=document.full_name,
        operation="style.apply",
    )
    prepared = idempotency.prepare(document, "스타일 적용", inputs, None)
    assert isinstance(prepared, OperationTicket)
    committed = idempotency.commit(prepared, _executed(verified=None))

    replayed = idempotency.prepare(document, "스타일 적용", inputs, None)

    assert isinstance(replayed, OperationResult)
    assert replayed.idempotency_status == "replayed"
    assert replayed.status == "executed"
    assert replayed.verified is None
    assert replayed.message.count(_EVIDENCE_MARKER) == 1
    assert committed.message.count(_EVIDENCE_MARKER) == 1


def test_long_message_stays_within_the_contract_limit(tmp_path: Path) -> None:
    committed = _commit(
        tmp_path,
        _executed(verified=None, message="가" * 3_990),
    )

    assert _EVIDENCE_MARKER in committed.message
    assert len(committed.message) <= 4_000
    # Re-validation must still succeed, otherwise the response cannot serialize.
    assert OperationResult.model_validate(committed.model_dump()) is not None


# --------------------------------------------------------------------------
# Safety: verification mismatch (False) must still fail.
# --------------------------------------------------------------------------


def test_verification_mismatch_is_still_reported_as_failure(
    tmp_path: Path,
) -> None:
    committed = _commit(tmp_path, _executed(verified=False))

    assert committed.status == "operation_failed"
    assert committed.idempotency_status == "failed"
    assert committed.verified is False
    assert committed.reconcile_required is True
    assert committed.retry_safe is False
    assert committed.journal_failure_code == "partial_mutation_reconcile_required"


def test_verification_mismatch_without_change_still_fails(tmp_path: Path) -> None:
    committed = _commit(tmp_path, _executed(verified=False, changed=False))

    assert committed.status == "operation_failed"
    assert committed.idempotency_status == "failed"
    assert committed.verified is False
    assert committed.reconcile_required is False
    assert committed.journal_failure_code == "unverified_operation_result"


def test_verification_mismatch_is_not_annotated_as_missing_evidence(
    tmp_path: Path,
) -> None:
    committed = _commit(tmp_path, _executed(verified=False))

    assert _EVIDENCE_MARKER not in committed.message


def test_journal_keeps_a_verification_mismatch_blocked_afterwards(
    tmp_path: Path,
) -> None:
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    document = _document()
    inputs = HwpOperateInputs(
        request_id="mismatch-operation",
        document=document.full_name,
        operation="style.apply",
    )
    prepared = idempotency.prepare(document, "스타일 적용", inputs, None)
    assert isinstance(prepared, OperationTicket)
    _ = idempotency.commit(prepared, _executed(verified=False))

    looked_up = idempotency.status(document, "mismatch-operation")

    assert looked_up.status == "operation_failed"
    assert looked_up.idempotency_status == "failed"


# --------------------------------------------------------------------------
# Safety: every other failure path is untouched even when evidence is missing.
# --------------------------------------------------------------------------


def test_reconcile_required_still_fails_when_evidence_is_missing(
    tmp_path: Path,
) -> None:
    committed = _commit(
        tmp_path,
        _executed(verified=None, reconcile_required=True),
    )

    assert committed.status == "operation_failed"
    assert committed.idempotency_status == "failed"
    assert committed.reconcile_required is True
    assert committed.retry_safe is False
    assert committed.journal_failure_code == "partial_mutation_reconcile_required"


def test_partial_mutation_still_fails_when_evidence_is_missing(
    tmp_path: Path,
) -> None:
    committed = _commit(
        tmp_path,
        _executed(verified=None, partial_mutation=True),
    )

    assert committed.status == "operation_failed"
    assert committed.idempotency_status == "failed"
    assert committed.reconcile_required is True
    assert committed.retry_safe is False


@pytest.mark.parametrize("status", ["partial_change", "transport_error"])
def test_non_executed_failure_statuses_are_preserved(
    tmp_path: Path,
    status: str,
) -> None:
    committed = _commit(tmp_path, _executed(verified=None, status=status))

    assert committed.status == status
    assert committed.idempotency_status == "failed"
    assert committed.verified is False
    assert _EVIDENCE_MARKER not in committed.message


def test_operation_failed_status_is_preserved(tmp_path: Path) -> None:
    committed = _commit(
        tmp_path,
        _executed(verified=None, status="operation_failed"),
    )

    assert committed.status == "operation_failed"
    assert committed.idempotency_status == "failed"
    assert committed.verified is False
    assert _EVIDENCE_MARKER not in committed.message


# --------------------------------------------------------------------------
# Safety: save workflows keep the strict rule.
# --------------------------------------------------------------------------


def test_save_verdict_still_comes_from_the_file_fingerprint(
    tmp_path: Path,
) -> None:
    path = tmp_path / "doc.hwp"
    _ = path.write_bytes(b"before save")
    document = _save_document(path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            request_id="save-operation",
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)
    _ = path.write_bytes(b"after save with different bytes")

    committed = idempotency.commit(prepared, _save_result(path, verified=None))

    # The fingerprint proved the write, so the save is confirmed regardless of
    # the tri-state the native layer reported.
    assert committed.status == "executed"
    assert committed.verified is True
    assert committed.save_fingerprint_verified is True
    assert _EVIDENCE_MARKER not in committed.message


def test_unconfirmed_save_fingerprint_still_fails_without_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "doc.hwp"
    _ = path.write_bytes(b"unchanged bytes")
    document = _save_document(path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            request_id="save-unconfirmed",
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    committed = idempotency.commit(prepared, _save_result(path, verified=None))

    assert committed.status == "operation_failed"
    assert committed.idempotency_status == "failed"
    assert committed.verified is False
    assert committed.save_fingerprint_verified is False
    assert committed.reconcile_required is True
    assert _EVIDENCE_MARKER not in committed.message


def test_save_reopen_verify_without_evidence_is_not_relaxed(
    tmp_path: Path,
) -> None:
    # Protocol 12 lifecycle results keep their own `verified` value through
    # `_attach_successful_save_fingerprint`, so a save workflow can still carry
    # `verified=None` into the decision. Save must keep the strict rule.
    path = tmp_path / "reopen.hwp"
    _ = path.write_bytes(b"before save")
    document = _save_document(path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    prepared = idempotency.prepare(
        document,
        "save and reopen",
        HwpOperateInputs(
            request_id="save-reopen-operation",
            document=document.full_name,
            operation="document.save_reopen_verify",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    committed = idempotency.commit(
        prepared,
        _save_result(path, verified=None).model_copy(
            update={
                "query": "save and reopen",
                "verification": "native_save_reopen_result",
                "reopened_path": str(path),
                "disk_persistence_verified": True,
            }
        ),
    )

    assert committed.status == "operation_failed"
    assert committed.idempotency_status == "failed"
    assert _EVIDENCE_MARKER not in committed.message


def test_save_without_request_id_keeps_its_strict_demotion(
    tmp_path: Path,
) -> None:
    # `prepare()` hands back a ticket with no journal identity only for save
    # workflows that omit request_id. That branch is deliberately untouched.
    path = tmp_path / "qa.hwp"
    _ = path.write_bytes(b"unchanged bytes")
    document = _save_document(path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(operation="document.save"),
        None,
    )
    assert isinstance(prepared, OperationTicket)
    assert prepared.request_id is None

    committed = idempotency.commit(prepared, _save_result(path, verified=None))

    assert committed.status == "operation_failed"
    assert committed.retry_safe is False

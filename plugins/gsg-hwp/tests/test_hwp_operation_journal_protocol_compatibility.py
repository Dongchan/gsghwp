from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation import McpOperationHandler  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_operation_contract import OperationResult  # noqa: E402
from hwp_operation_idempotency import OperationIdempotency  # noqa: E402
from hwp_operation_journal import (  # noqa: E402
    OperationJournal,
    operation_result_digest,
)
from hwp_operation_journal_contract import JournalRecord  # noqa: E402
from hwp_operation_journal_store import (  # noqa: E402
    JournalLookupIssue,
    OperationJournalStore,
)
from hwp_save_fingerprint import capture_save_file_fingerprint  # noqa: E402


_CURRENT_PROTOCOL = 12
_CURRENT_SESSION = "current-document-session"


def _result(label: str, *, request_id: str | None = None) -> OperationResult:
    return OperationResult(
        request_id=request_id,
        status="executed",
        changed=True,
        query=label,
        registry_entries=1,
        lookup_microseconds=0,
        message=label,
        execution_mode="native_in_process",
        native_protocol=_CURRENT_PROTOCOL,
        verification="native_operation_specific_readback",
        verified=True,
    )


def _commit_current(journal: OperationJournal, request_id: str) -> OperationResult:
    result = _result(request_id, request_id=request_id)
    decision = journal.begin(
        _CURRENT_SESSION,
        request_id,
        f"digest:{request_id}",
        document_path=r"C:\fixtures\current.hwp",
        operation="text.replace",
    )
    assert decision.kind == "execute"
    journal.mark_executing(_CURRENT_SESSION, request_id)
    journal.mark_verified(
        _CURRENT_SESSION,
        request_id,
        result,
        operation_result_digest(result),
    )
    journal.mark_committed(_CURRENT_SESSION, request_id)
    return result


def _write_protocol_record(
    root: Path,
    *,
    protocol: int,
    request_id: str | None,
) -> Path:
    recorded_at = datetime(2026, 7, 20, tzinfo=UTC)
    result = _result(f"incompatible protocol {protocol}")
    record = JournalRecord(
        request_digest=f"digest:incompatible:{protocol}",
        request_id=request_id,
        document_session=(
            None if request_id is None else "incompatible-document-session"
        ),
        operation=None if request_id is None else "document.save",
        state="committed",
        started_at=recorded_at,
        updated_at=recorded_at,
        heartbeat_at=recorded_at,
        result=result,
        result_digest=operation_result_digest(result),
    )
    payload = record.model_dump(mode="json")
    result_payload = cast(dict[str, object], payload["result"])
    runtime_payload = cast(dict[str, object], result_payload["runtime"])
    runtime_payload["protocol"] = protocol
    result_payload["native_protocol"] = protocol
    if request_id is None:
        for field in ("request_id", "document_session", "document_path", "operation"):
            payload.pop(field)
    path = root / f"0000-incompatible-{protocol}.json"
    _ = path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _operation_handler(journal: OperationJournal) -> McpOperationHandler:
    bridge = cast(HancomBridge, object())
    dispatcher = cast(McpThreadDispatcher, object())
    executor = HwpOperationExecutor(bridge, dispatcher, journal)
    return McpOperationHandler(bridge, dispatcher, executor)


@pytest.mark.parametrize("incompatible_protocol", [8, 99])
def test_hwp_get_operation_status_skips_unrelated_incompatible_record(
    tmp_path: Path,
    incompatible_protocol: int,
) -> None:
    root = tmp_path / "journal"
    journal = OperationJournal(root)
    operation_id = f"current-operation-with-{incompatible_protocol}"
    expected = _commit_current(journal, operation_id)
    incompatible = _write_protocol_record(
        root,
        protocol=incompatible_protocol,
        request_id=None,
    )
    original = incompatible.read_bytes()

    result = anyio.run(
        _operation_handler(journal).hwp_get_operation_status,
        operation_id,
        None,
    )

    assert result.status == expected.status
    assert result.request_id == operation_id
    assert result.idempotency_status == "replayed"
    assert result.runtime.protocol == _CURRENT_PROTOCOL
    assert result.native_protocol == _CURRENT_PROTOCOL
    assert incompatible.read_bytes() == original


def test_lookup_preserves_non_utf8_issue_without_blocking_valid_match(
    tmp_path: Path,
) -> None:
    root = tmp_path / "journal"
    journal = OperationJournal(root)
    operation_id = "current-operation-with-non-utf8-neighbor"
    expected = _commit_current(journal, operation_id)
    unreadable = root / "0000-unreadable.json"
    _ = unreadable.write_bytes(b"\xff\xfe\x80")
    original_digest = hashlib.sha256(unreadable.read_bytes()).hexdigest()

    lookup = journal.lookup_request_with_issues(operation_id, None)
    result = anyio.run(
        _operation_handler(journal).hwp_get_operation_status,
        operation_id,
        None,
    )

    assert len(lookup.decisions) == 1
    assert lookup.decisions[0].request_id == operation_id
    assert len(lookup.issues) == 1
    issue = lookup.issues[0]
    assert issue.path == unreadable
    assert issue.state == "unreadable"
    assert issue.match == "unknown"
    assert issue.error_type == "UnicodeDecodeError"
    assert result.status == expected.status
    assert result.idempotency_status == "replayed"
    assert hashlib.sha256(unreadable.read_bytes()).hexdigest() == original_digest


def test_lookup_readers_mark_disappeared_record_unreadable(tmp_path: Path) -> None:
    missing = tmp_path / "disappeared.json"

    issues = (
        OperationJournalStore.read_lookup_metadata(missing),
        OperationJournalStore.read_for_lookup(missing),
    )

    assert all(isinstance(issue, JournalLookupIssue) for issue in issues)
    for issue in issues:
        assert isinstance(issue, JournalLookupIssue)
        assert issue.path == missing
        assert issue.state == "unreadable"
        assert issue.match == "unknown"
        assert issue.error_type == "FileNotFoundError"


def test_unknown_non_utf8_record_returns_fail_closed_without_connection_fallback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "journal"
    journal = OperationJournal(root)
    unreadable = root / "only-record.json"
    _ = unreadable.write_bytes(b"\xff\xfe\x80")
    original_digest = hashlib.sha256(unreadable.read_bytes()).hexdigest()

    result = anyio.run(
        _operation_handler(journal).hwp_get_operation_status,
        "possibly-hidden-operation",
        None,
    )

    assert result.status == "transport_error"
    assert result.idempotency_status == "failed"
    assert result.failure_stage == "operation_journal_lookup"
    assert result.journal_failure_code == "journal_lookup_unreadable"
    assert result.verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert "판정할 수 없습니다" in result.message
    assert hashlib.sha256(unreadable.read_bytes()).hexdigest() == original_digest


def test_hwp_get_operation_status_preserves_current_protocol_behavior(
    tmp_path: Path,
) -> None:
    journal = OperationJournal(tmp_path / "journal")
    operation_id = "current-protocol-operation"
    expected = _commit_current(journal, operation_id)

    result = anyio.run(
        _operation_handler(journal).hwp_get_operation_status,
        operation_id,
        None,
    )

    assert result.status == expected.status
    assert result.request_id == operation_id
    assert result.idempotency_status == "replayed"
    assert result.runtime.protocol == _CURRENT_PROTOCOL
    assert result.native_protocol == _CURRENT_PROTOCOL


@pytest.mark.parametrize("compatible_protocol", [9, 10, 11])
def test_historical_protocol_records_remain_queryable_and_unchanged(
    tmp_path: Path,
    compatible_protocol: int,
) -> None:
    root = tmp_path / "journal"
    journal = OperationJournal(root)
    operation_id = f"compatible-operation-{compatible_protocol}"
    compatible = _write_protocol_record(
        root,
        protocol=compatible_protocol,
        request_id=operation_id,
    )
    original_digest = hashlib.sha256(compatible.read_bytes()).hexdigest()

    lookup = journal.lookup_request_with_issues(operation_id, None)
    result = anyio.run(
        _operation_handler(journal).hwp_get_operation_status,
        operation_id,
        None,
    )

    assert len(lookup.decisions) == 1
    assert lookup.issues == ()
    assert result.status == "executed"
    assert result.idempotency_status == "replayed"
    assert result.runtime.protocol == compatible_protocol
    assert result.native_protocol == compatible_protocol
    assert hashlib.sha256(compatible.read_bytes()).hexdigest() == original_digest


@pytest.mark.parametrize("incompatible_protocol", [8, 99])
def test_matching_incompatible_record_returns_fail_closed_and_remains_unchanged(
    tmp_path: Path,
    incompatible_protocol: int,
) -> None:
    root = tmp_path / "journal"
    journal = OperationJournal(root)
    operation_id = f"incompatible-operation-{incompatible_protocol}"
    incompatible = _write_protocol_record(
        root,
        protocol=incompatible_protocol,
        request_id=operation_id,
    )
    original_digest = hashlib.sha256(incompatible.read_bytes()).hexdigest()

    lookup = journal.lookup_request_with_issues(operation_id, None)
    result = anyio.run(
        _operation_handler(journal).hwp_get_operation_status,
        operation_id,
        None,
    )

    assert lookup.decisions == ()
    assert len(lookup.issues) == 1
    issue = lookup.issues[0]
    assert issue.path == incompatible
    assert issue.state == "incompatible"
    assert issue.match == "matched"
    assert result.status == "transport_error"
    assert result.idempotency_status == "failed"
    assert result.failure_stage == "operation_journal_lookup"
    assert result.journal_failure_code == "journal_lookup_incompatible"
    assert result.verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert "판정할 수 없습니다" in result.message
    assert hashlib.sha256(incompatible.read_bytes()).hexdigest() == original_digest


def test_revalidated_status_digest_matches_dynamic_response_and_preserves_journal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "journal"
    saved_path = tmp_path / "saved.hwp"
    _ = saved_path.write_bytes(b"before save")
    before = capture_save_file_fingerprint(str(saved_path))
    journal = OperationJournal(root)
    operation_id = "revalidated-save-digest"
    decision = journal.begin(
        "save-document-session",
        operation_id,
        "digest:revalidated-save",
        document_path=str(saved_path),
        operation="document.save",
        save_fingerprint_before=before,
    )
    assert decision.kind == "execute"
    journal.mark_executing("save-document-session", operation_id)
    _ = saved_path.write_bytes(b"after confirmed save")
    saved_stat = saved_path.stat()
    saved_result = _result("original save response").model_copy(
        update={
            "saved_path": str(saved_path),
            "before_modified": True,
            "save_hresult": 0,
            "save_return": 1,
            "post_save_modified": False,
            "saved_file_size": saved_stat.st_size,
            "saved_file_write_time_100ns": (
                saved_stat.st_mtime_ns // 100 + 116_444_736_000_000_000
            ),
        }
    )
    journal.mark_verified(
        "save-document-session",
        operation_id,
        saved_result,
        operation_result_digest(saved_result),
    )
    journal.mark_committed("save-document-session", operation_id)
    record_path = OperationJournalStore(root).entry_path(
        "save-document-session",
        operation_id,
    )
    original_digest = hashlib.sha256(record_path.read_bytes()).hexdigest()

    replayed = OperationIdempotency(journal).status_without_connection(
        operation_id,
        str(saved_path),
    )

    assert replayed is not None
    assert replayed.idempotency_status == "replayed"
    assert replayed.query == "operation status"
    assert replayed.result_digest == operation_result_digest(replayed)
    assert hashlib.sha256(record_path.read_bytes()).hexdigest() == original_digest


def test_failed_status_digest_matches_dynamic_response_and_preserves_journal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "journal"
    journal = OperationJournal(root)
    operation_id = "failed-status-digest"
    document_session = "failed-document-session"
    decision = journal.begin(
        document_session,
        operation_id,
        "digest:failed-status",
        operation="text.replace",
    )
    assert decision.kind == "execute"
    journal.mark_executing(document_session, operation_id)
    failed_result = _result("original failed response").model_copy(
        update={
            "status": "transport_error",
            "verified": False,
            "retry_safe": False,
        }
    )
    journal.mark_failed(
        document_session,
        operation_id,
        "native_response_lost",
        result=failed_result,
        result_digest=operation_result_digest(failed_result),
    )
    record_path = OperationJournalStore(root).entry_path(
        document_session,
        operation_id,
    )
    original_digest = hashlib.sha256(record_path.read_bytes()).hexdigest()

    replayed = OperationIdempotency(journal).status_without_connection(
        operation_id,
        None,
    )

    assert replayed is not None
    assert replayed.status == "operation_failed"
    assert replayed.idempotency_status == "failed"
    assert replayed.query == "operation status"
    assert replayed.result_digest == operation_result_digest(replayed)
    assert hashlib.sha256(record_path.read_bytes()).hexdigest() == original_digest

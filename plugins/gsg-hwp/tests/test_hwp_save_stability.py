from __future__ import annotations

import sys
from base64 import b64encode
from pathlib import Path
from typing import ClassVar

import anyio
import pytest
from pydantic import BaseModel, ConfigDict, JsonValue


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
import hwp_live_session_lifecycle as lifecycle_module  # noqa: E402
from hwp_live_native_batch_contract import (  # noqa: E402
    decode_lifecycle_result,
    decode_save_result,
)
from hwp_live_contract import OpenDocument  # noqa: E402
from hwp_live_process_lane import HwpLaneOperationContext  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_session_lifecycle import (  # noqa: E402
    SaveStateMachine,
    lifecycle_result,
    save_result,
)
from hwp_mcp import build_server  # noqa: E402
from hwp_operation_contract import HwpOperateInputs  # noqa: E402
from hwp_operation_idempotency import (  # noqa: E402
    OperationIdempotency,
    OperationTicket,
)
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_public_contract import to_public_action_result  # noqa: E402


class _InputSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    required: tuple[str, ...] = ()
    properties: dict[str, JsonValue]


def _encoded(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii")


def _save_payload(
    *,
    verified: bool = True,
    after_document_hash: str = "103",
    before_modified: bool = True,
    save_hresult: int = 0,
    save_return: int = 1,
    saved_path: str = "C:/docs/sample.hwp",
    file_size: int = 4096,
    file_write_time_100ns: int | None = 133_700_000_000_000_000,
    elapsed_microseconds: int = 250,
) -> str:
    fields = [
        "HLS1",
        "1" if verified else "0",
        _encoded(saved_path),
        "3",
        "1" if before_modified else "0",
        "2",
        "101",
        "102",
        "103",
        str(save_hresult),
        str(save_return),
        "0",
        "3",
        "0",
        "2",
        "101",
        "102",
        after_document_hash,
        str(file_size),
    ]
    if file_write_time_100ns is not None:
        fields.append(str(file_write_time_100ns))
    fields.append(str(elapsed_microseconds))
    return "\t".join(fields)


def _lifecycle_payload() -> str:
    return "\t".join(
        (
            "HCL12",
            "0",
            _encoded("C:/docs/sample.hwp"),
            "3",
            "1",
            "2",
            "101",
            "102",
            "103",
            "0",
            "1",
            "0",
            "0",
            "-1",
            "0",
            "0",
            "1",
            "0",
            "1",
            "3",
            "0",
            "2",
            "101",
            "102",
            "103",
            "300",
        )
    )


def _diagnostic_suffix(
    *,
    before_length: str = "65537",
    after_length: str = "65537",
    before_sections: str = "000000000000000b,000000000000000c",
    after_sections: str = "000000000000000b,000000000000000d",
) -> str:
    return "\t".join((before_length, after_length, before_sections, after_sections))


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


def test_general_save_reports_live_and_disk_evidence_without_claiming_persistence() -> (
    None
):
    native = decode_save_result(_save_payload())
    result = save_result("문서 일반 저장", native)

    assert native.verified is True
    assert native.before_text_hash == native.after_text_hash == "102"
    assert native.before_document_hash == native.after_document_hash == "103"
    assert result.status == "executed"
    assert result.verified is True
    assert result.verification == "native_save_result"
    assert result.saved_path == "C:/docs/sample.hwp"
    assert result.live_state_preserved_after_save is True
    assert result.disk_persistence_verified is False
    assert result.saved_file_size == 4096
    assert result.saved_file_write_time_100ns == 133_700_000_000_000_000
    assert "디스크 내용 영속성은 재개방 진단에서만" in result.message
    assert "save_state=verified" in result.message
    assert (
        "save_transitions=queued,native_started,native_returned,"
        "metadata_changed,verified"
    ) in result.message
    assert result.partial_mutation is False
    assert result.retry_safe is True
    assert result.reconcile_required is False


def test_save_state_machine_preserves_ordered_progress_and_evidence() -> None:
    state = SaveStateMachine()

    state.native_started(source="DocumentBeforeSave")
    state.heartbeat(source="watchdog", detail="responding=false")
    state.progress(source="progress_control", detail="visible=true")
    state.native_returned(source="DocumentAfterSave")
    state.metadata_changed(source="file_stat", detail="size=4096")
    state.verified(source="native_readback")

    snapshot = state.snapshot()
    assert snapshot.phase == "verified"
    assert snapshot.terminal is True
    assert snapshot.retry_safe is True
    assert tuple(transition.phase for transition in snapshot.transitions) == (
        "queued",
        "native_started",
        "heartbeat",
        "progress",
        "native_returned",
        "metadata_changed",
        "verified",
    )
    assert snapshot.transitions[1].source == "DocumentBeforeSave"
    assert snapshot.transitions[3].detail == "visible=true"


def test_queued_save_is_retry_safe_before_native_mutation() -> None:
    snapshot = SaveStateMachine().snapshot()

    assert snapshot.phase == "queued"
    assert snapshot.terminal is False
    assert snapshot.retry_safe is True
    assert snapshot.close_blocked is True


def test_save_state_machine_refuses_verified_without_metadata_evidence() -> None:
    state = SaveStateMachine()

    state.native_started(source="native_dispatch")
    state.native_started(source="DocumentBeforeSave")
    state.native_returned(source="native_return")
    state.verified(source="native_readback")

    snapshot = state.snapshot()
    assert snapshot.phase == "native_returned"
    assert snapshot.terminal is False
    assert snapshot.retry_safe is False
    assert snapshot.close_blocked is True
    assert tuple(transition.source for transition in snapshot.transitions) == (
        "request",
        "native_dispatch",
        "DocumentBeforeSave",
        "native_return",
    )


def test_save_state_machine_keeps_one_watchdog_warning_without_phase_regression() -> (
    None
):
    state = SaveStateMachine()
    state.native_started(source="native_dispatch")
    state.native_started(source="DocumentBeforeSave")
    state.progress(source="DocumentAfterSave")

    for sample in range(2_000):
        state.heartbeat(
            source="watchdog",
            detail=f"elapsed_seconds={30 + sample / 10:.1f}",
        )

    snapshot = state.snapshot()
    assert snapshot.phase == "progress"
    assert tuple(transition.phase for transition in snapshot.transitions) == (
        "queued",
        "native_started",
        "heartbeat",
        "progress",
        "progress",
    )
    assert tuple(transition.source for transition in snapshot.transitions) == (
        "request",
        "native_dispatch",
        "DocumentBeforeSave",
        "DocumentAfterSave",
        "watchdog",
    )


def test_save_state_machine_coalesces_repeated_watchdog_heartbeats() -> None:
    state = SaveStateMachine()
    state.native_started(source="native_dispatch")

    for sample in range(2_000):
        state.heartbeat(
            source="watchdog",
            detail=f"elapsed_seconds={30 + sample / 10:.1f}",
        )
    state.progress(source="watchdog", detail="metadata_changed=true")
    for sample in range(2_000):
        state.heartbeat(
            source="watchdog",
            detail=f"elapsed_seconds={60 + sample / 10:.1f}",
        )
        state.progress(source="watchdog", detail="metadata_changed=true")

    snapshot = state.snapshot()
    assert snapshot.phase == "progress"
    assert tuple(transition.phase for transition in snapshot.transitions) == (
        "queued",
        "native_started",
        "heartbeat",
        "progress",
    )


def test_save_state_machine_uncertain_terminal_is_not_retry_safe() -> None:
    state = SaveStateMachine()
    state.native_started(source="DocumentBeforeSave")
    state.uncertain(source="watchdog", detail="deadline_seconds=180")

    snapshot = state.snapshot()
    assert snapshot.phase == "uncertain"
    assert snapshot.terminal is True
    assert snapshot.retry_safe is False
    assert snapshot.close_blocked is True


def test_legacy_save_payload_remains_decodable() -> None:
    native = decode_save_result(_save_payload(file_write_time_100ns=None))

    assert native.verified is True
    assert native.file_write_time_100ns is None
    assert native.before_document_length is None
    assert native.after_document_length is None
    assert native.before_document_section_hashes is None
    assert native.after_document_section_hashes is None


def test_save_parses_complete_trailing_document_fingerprint_diagnostics() -> None:
    native = decode_save_result(f"{_save_payload()}\t{_diagnostic_suffix()}")

    assert native.verified is True
    assert native.before_document_length == 65_537
    assert native.after_document_length == 65_537
    assert native.before_document_section_hashes == (
        "000000000000000b",
        "000000000000000c",
    )
    assert native.after_document_section_hashes == (
        "000000000000000b",
        "000000000000000d",
    )


def test_save_accepts_empty_section_diagnostics_only_for_failed_capture() -> None:
    suffix = _diagnostic_suffix(
        before_length="0",
        after_length="0",
        before_sections="",
        after_sections="",
    )
    native = decode_save_result(f"{_save_payload(verified=False)}\t{suffix}")

    assert native.verified is False
    assert native.before_document_length == 0
    assert native.after_document_length == 0
    assert native.before_document_section_hashes == ()
    assert native.after_document_section_hashes == ()


def test_diagnostics_do_not_override_existing_document_hash_verdict() -> None:
    same_diagnostics = _diagnostic_suffix(
        after_sections="000000000000000b,000000000000000c"
    )

    with pytest.raises(HwpLiveError, match="readback 근거"):
        _ = decode_save_result(
            f"{_save_payload(after_document_hash='999')}\t{same_diagnostics}"
        )


@pytest.mark.parametrize(
    "payload",
    (
        f"{_save_payload()}\t65537",
        f"{_save_payload()}\t65537\t65537",
        f"{_save_payload()}\t65537\t65537\t000000000000000b,000000000000000c",
        f"{_save_payload()}\t{_diagnostic_suffix()}\textra",
        f"{_lifecycle_payload()}\t65537",
        f"{_lifecycle_payload()}\t65537\t65537",
        f"{_lifecycle_payload()}\t65537\t65537\t000000000000000b,000000000000000c",
        f"{_lifecycle_payload()}\t{_diagnostic_suffix()}\textra",
    ),
)
def test_document_fingerprint_diagnostics_reject_partial_or_unknown_fields(
    payload: str,
) -> None:
    decoder = (
        decode_save_result if payload.startswith("HLS1\t") else decode_lifecycle_result
    )

    with pytest.raises(HwpLiveError, match="응답 형식"):
        _ = decoder(payload)


@pytest.mark.parametrize(
    "suffix",
    (
        _diagnostic_suffix(before_sections="000000000000000b"),
        _diagnostic_suffix(before_sections="000000000000000B,000000000000000c"),
        _diagnostic_suffix(before_sections="0000000000000000b,000000000000000c"),
        _diagnostic_suffix(before_sections="000000000000000g,000000000000000c"),
        _diagnostic_suffix(before_sections=""),
        _diagnostic_suffix(before_length="0"),
        _diagnostic_suffix(before_length="x"),
        _diagnostic_suffix(before_length="065537"),
        _diagnostic_suffix(before_length="18446744073709551616"),
    ),
)
def test_save_rejects_malformed_document_fingerprint_diagnostics(
    suffix: str,
) -> None:
    with pytest.raises(HwpLiveError, match="진단|해시"):
        _ = decode_save_result(f"{_save_payload()}\t{suffix}")


def test_general_save_rejects_claimed_success_when_document_hash_changed() -> None:
    with pytest.raises(HwpLiveError, match="readback 근거"):
        _ = decode_save_result(_save_payload(after_document_hash="999"))


def test_general_save_rejects_claimed_success_without_disk_evidence() -> None:
    with pytest.raises(HwpLiveError, match="readback 근거"):
        _ = decode_save_result(_save_payload(file_size=0))


@pytest.mark.parametrize(
    ("payload", "partial_mutation", "missing_evidence"),
    (
        (
            _save_payload(verified=False, save_hresult=-2_147_024_864),
            True,
            "save_call",
        ),
        (
            _save_payload(verified=False, saved_path=""),
            False,
            "saved_path",
        ),
        (
            _save_payload(verified=False, file_size=0),
            False,
            "file_metadata",
        ),
    ),
    ids=("file_locked", "invalid_path", "missing_file_evidence"),
)
def test_uncertain_save_is_not_retry_safe_or_verified(
    payload: str,
    partial_mutation: bool,
    missing_evidence: str,
) -> None:
    result = save_result("문서 일반 저장", decode_save_result(payload))
    public = to_public_action_result(result, ())

    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert result.partial_mutation is partial_mutation
    assert result.disk_persistence_verified is False
    assert f"save_evidence_missing={missing_evidence}" in result.message
    assert public.retry_safe is False
    assert public.reconcile_required is True
    assert public.save_evidence is not None
    assert public.save_evidence.disk_persistence_verified is False


def test_lv6_save_uses_strict_file_fingerprint_without_claiming_live_state(
    tmp_path: Path,
) -> None:
    document_path = tmp_path / "lv6-format-save.hwp"
    _ = document_path.write_bytes(b"before native save")
    document = _save_document(document_path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    _ = document_path.write_bytes(b"after native save with changed bytes")
    saved_stat = document_path.stat()
    native = decode_save_result(
        _save_payload(
            verified=False,
            after_document_hash="999",
            saved_path=str(document_path),
            file_size=saved_stat.st_size,
            file_write_time_100ns=(
                saved_stat.st_mtime_ns // 100 + 116_444_736_000_000_000
            ),
        )
    )
    preliminary = save_result("문서 일반 저장", native)

    assert native.verified is False
    assert native.before_document_hash != native.after_document_hash
    assert preliminary.status == "executed"
    assert preliminary.verified is False
    assert preliminary.live_state_preserved_after_save is False
    assert preliminary.partial_mutation is False
    assert preliminary.retry_safe is False
    assert preliminary.reconcile_required is True
    assert "save_state=uncertain" in preliminary.message
    assert "live_state_verification=document_fingerprint" in preliminary.message

    result = idempotency.commit(prepared, preliminary)
    public = to_public_action_result(result, ())

    assert result.status == "executed"
    assert result.verified is True
    assert result.save_fingerprint_stable is True
    assert result.save_fingerprint_changed is True
    assert result.save_fingerprint_verified is True
    assert result.disk_persistence_verified is True
    assert result.live_state_preserved_after_save is False
    assert result.partial_mutation is False
    assert result.retry_safe is True
    assert result.reconcile_required is False
    assert public.status == "succeeded"
    assert public.save_evidence is not None
    assert public.save_evidence.fingerprint_verified is True
    assert public.save_evidence.disk_persistence_verified is True
    assert public.save_evidence.live_state_preserved_after_save is False


def test_lv6_native_completion_does_not_bypass_unchanged_fingerprint(
    tmp_path: Path,
) -> None:
    document_path = tmp_path / "unchanged-format-save.hwp"
    _ = document_path.write_bytes(b"unchanged bytes")
    document = _save_document(document_path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "unchanged-journal"))
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    saved_stat = document_path.stat()
    preliminary = save_result(
        "문서 일반 저장",
        decode_save_result(
            _save_payload(
                verified=False,
                after_document_hash="999",
                saved_path=str(document_path),
                file_size=saved_stat.st_size,
                file_write_time_100ns=(
                    saved_stat.st_mtime_ns // 100 + 116_444_736_000_000_000
                ),
            )
        ),
    )
    result = idempotency.commit(prepared, preliminary)

    assert preliminary.status == "executed"
    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.save_fingerprint_changed is False
    assert result.save_fingerprint_verified is False
    assert result.disk_persistence_verified is False
    assert result.partial_mutation is False
    assert result.retry_safe is False
    assert result.reconcile_required is True


def test_slow_save_keeps_evidence_semantics_without_retrying() -> None:
    native = decode_save_result(_save_payload(elapsed_microseconds=45_000_000))
    result = save_result("느린 문서 일반 저장", native)

    assert native.elapsed_microseconds == 45_000_000
    assert result.verified is True
    assert result.live_state_preserved_after_save is True
    assert result.disk_persistence_verified is False


def test_clean_no_op_save_is_verified_without_reporting_a_change() -> None:
    native = decode_save_result(_save_payload(before_modified=False, save_return=0))
    result = save_result("변경 없는 문서 저장", native)

    assert result.verified is True
    assert result.changed is False
    assert result.retry_safe is True


def test_native_success_cannot_override_watchdog_uncertain_save_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = tmp_path / "copy.hwp"
    _ = document.write_bytes(b"before")
    context = HwpLaneOperationContext(
        session_id="session-1",
        process_id=77,
        window_handle=101,
        document_path=document,
        document_id=17,
    )
    state = SaveStateMachine()
    state.native_started(source="native_dispatch")
    state.native_started(source="DocumentBeforeSave")
    state.uncertain(source="watchdog", detail="deadline_seconds=180")
    assert context.attach_save_state(state) is state
    monkeypatch.setattr(
        lifecycle_module,
        "current_lane_operation_context",
        lambda: context,
    )

    result = save_result("문서 일반 저장", decode_save_result(_save_payload()))

    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert result.partial_mutation is True
    assert result.live_state_preserved_after_save is False
    assert "save_state=uncertain" in result.message
    assert tuple(transition.source for transition in state.snapshot().transitions) == (
        "request",
        "native_dispatch",
        "DocumentBeforeSave",
        "watchdog",
    )


def test_reopen_failure_reports_recovered_document_session() -> None:
    payload = _lifecycle_payload()
    native = decode_lifecycle_result(payload)
    result = lifecycle_result("문서 저장 재개방 검증", native)

    assert native.recovered is True
    assert native.before_document_length is None
    assert native.after_document_length is None
    assert native.before_document_section_hashes is None
    assert native.after_document_section_hashes is None
    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.session_recovered is True
    assert result.partial_mutation is False
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert result.disk_persistence_verified is False


def test_lifecycle_parses_complete_trailing_document_fingerprint_diagnostics() -> None:
    native = decode_lifecycle_result(f"{_lifecycle_payload()}\t{_diagnostic_suffix()}")

    assert native.recovered is True
    assert native.before_document_length == 65_537
    assert native.after_document_length == 65_537
    assert native.before_document_section_hashes == (
        "000000000000000b",
        "000000000000000c",
    )
    assert native.after_document_section_hashes == (
        "000000000000000b",
        "000000000000000d",
    )


def test_production_exposes_non_destructive_save_separately() -> None:
    server = build_server(LiveHwpController(), profile="production")
    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _InputSchema.model_validate(tool.inputSchema) for tool in tools
    }

    assert "hwp_save" in schemas
    assert schemas["hwp_save"].required == ("operation_id",)
    assert frozenset(schemas["hwp_save"].properties) == frozenset(
        ("operation_id", "document_path", "document_selector")
    )
    assert "hwp_save_reopen_verify" in schemas

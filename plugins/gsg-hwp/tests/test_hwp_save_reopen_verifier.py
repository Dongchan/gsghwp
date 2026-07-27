from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_session_lifecycle as lifecycle_module  # noqa: E402
from hwp_live_native_batch_contract import NativeLifecycleResult  # noqa: E402
from hwp_live_process_lane import HwpLaneOperationContext  # noqa: E402
from hwp_live_session_lifecycle import SaveStateMachine, lifecycle_result  # noqa: E402


def _native_result(**changes: object) -> NativeLifecycleResult:
    result = NativeLifecycleResult(
        verified=True,
        reopened_path="C:/documents/save-reopen.hwp",
        before_page_count=34,
        before_modified=False,
        before_control_count=88,
        before_control_hash="12562077635838421888",
        before_text_hash="4889131960745186470",
        before_document_hash="1155038942252214356",
        save_hresult=0,
        save_return=1,
        post_save_modified=False,
        clear_hresult=0,
        clear_return=-1,
        open_hresult=0,
        open_return=1,
        recovered=False,
        recovery_hresult=-2_147_483_638,
        recovery_return=-1,
        after_page_count=34,
        after_modified=False,
        after_control_count=88,
        after_control_hash="12562077635838421888",
        after_text_hash="4889131960745186470",
        after_document_hash="1155038942252214356",
        elapsed_microseconds=300,
    )
    return replace(result, **changes)


def test_hwpml_only_mismatch_reports_exact_failure_without_claiming_persistence() -> (
    None
):
    native = _native_result(
        verified=False,
        after_document_hash="11349024622176549710",
    )

    result = lifecycle_result("문서 저장 재개방 검증", native)

    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.failure_stage == "document_fingerprint"
    assert result.disk_persistence_verified is False
    assert result.reconcile_required is True
    assert "save_state=uncertain" in result.message
    assert "문서 지문" in result.message


@pytest.mark.parametrize(
    ("changes", "failure_stage"),
    (
        ({"save_hresult": -2_147_024_864, "verified": False}, "save"),
        ({"clear_hresult": -2_147_024_864, "verified": False}, "clear"),
        ({"open_return": 0, "verified": False}, "reopen"),
        ({"reopened_path": None, "verified": False}, "session_identity"),
        ({"after_text_hash": "999", "verified": False}, "text_fingerprint"),
    ),
)
def test_real_persistence_failures_remain_failed_with_a_precise_stage(
    changes: dict[str, object],
    failure_stage: str,
) -> None:
    result = lifecycle_result("문서 저장 재개방 검증", _native_result(**changes))

    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.failure_stage == failure_stage
    assert result.disk_persistence_verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True


def test_verified_native_reopen_remains_a_verified_persistence_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = tmp_path / "save-reopen.hwp"
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
    assert context.attach_save_state(state) is state
    context.arm_watchdog_baseline()
    _ = document.write_bytes(b"after-save")
    monkeypatch.setattr(
        lifecycle_module,
        "current_lane_operation_context",
        lambda: context,
    )

    result = lifecycle_result(
        "문서 저장 재개방 검증",
        _native_result(
            before_modified=True,
            reopened_path=str(document),
        ),
    )

    assert result.status == "executed"
    assert result.verified is True
    assert result.failure_stage is None
    assert result.disk_persistence_verified is True
    assert result.reconcile_required is False
    assert "save_state=verified" in result.message
    assert "save_close_blocked=false" in result.message


def test_native_reopen_success_cannot_override_watchdog_uncertain_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = tmp_path / "save-reopen.hwp"
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

    result = lifecycle_result("문서 저장 재개방 검증", _native_result())

    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.failure_stage == "native_verification"
    assert result.disk_persistence_verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert "save_state=uncertain" in result.message
    assert tuple(transition.source for transition in state.snapshot().transitions) == (
        "request",
        "native_dispatch",
        "DocumentBeforeSave",
        "watchdog",
    )


def test_recovered_fingerprint_mismatch_preserves_session_without_claiming_success() -> (
    None
):
    native = _native_result(
        verified=False,
        recovered=True,
    )

    result = lifecycle_result("문서 저장 재개방 검증", native)

    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.failure_stage == "native_verification"
    assert result.session_recovered is True
    assert result.live_state_preserved_after_save is True
    assert result.partial_mutation is False
    assert result.disk_persistence_verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True

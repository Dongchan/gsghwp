from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_bridge  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_mcp_result_envelope import transport_error_result  # noqa: E402
from hwp_operation_contract import HwpOperateInputs  # noqa: E402


_deadline_error = cast(
    Callable[..., HwpLiveError],
    getattr(hwp_live_bridge, "_deadline_error"),
)
_modal_dialog_error = cast(
    Callable[..., HwpLiveError],
    getattr(hwp_live_bridge, "_modal_dialog_error"),
)
_process_lost_error = cast(
    Callable[..., HwpLiveError],
    getattr(hwp_live_bridge, "_process_lost_error"),
)


def test_transport_envelope_distinguishes_mutation_evidence_states() -> None:
    inputs = HwpOperateInputs(operation="table.fill_existing")

    unchanged = transport_error_result(
        inputs,
        HwpLiveError("transport failed", mutation_started=False),
    )
    changed = transport_error_result(
        inputs,
        HwpLiveError("transport failed", mutation_started=True),
    )
    uncertain = transport_error_result(
        inputs,
        HwpLiveError("transport failed"),
    )
    pre_start = transport_error_result(
        inputs,
        HwpLiveError("connection failed", mutation_started=True),
        mutation_started=False,
    )
    explicit_started = transport_error_result(
        inputs,
        HwpLiveError("transport failed"),
        mutation_started=True,
    )

    assert unchanged.changed is False
    assert unchanged.partial_change is False
    assert unchanged.partial_mutation is False
    assert unchanged.retry_safe is True

    assert changed.changed is True
    assert changed.partial_change is True
    assert changed.partial_mutation is True
    assert changed.retry_safe is False

    assert uncertain.changed is True
    assert uncertain.partial_change is True
    assert uncertain.partial_mutation is None
    assert uncertain.retry_safe is False

    assert pre_start.changed is False
    assert pre_start.partial_change is False
    assert pre_start.partial_mutation is False
    assert pre_start.retry_safe is True

    assert explicit_started.changed is True
    assert explicit_started.partial_change is True
    assert explicit_started.partial_mutation is True
    assert explicit_started.retry_safe is False


def test_bridge_error_factories_attach_their_mutation_evidence() -> None:
    before_mutation = (
        _deadline_error(
            mutation=True,
            phase="queued",
            process_lane_isolation=False,
        ),
        _process_lost_error(
            process_id=17,
            mutation=True,
            phase="queued",
            diagnostic=None,
        ),
        _modal_dialog_error(
            mutation=True,
            phase="queued",
            diagnostic="대상 한컴 창에 대화상자가 떠 있습니다",
        ),
    )
    after_mutation = (
        _deadline_error(
            mutation=True,
            phase="running",
            process_lane_isolation=True,
        ),
        _process_lost_error(
            process_id=17,
            mutation=True,
            phase="running",
            diagnostic=None,
        ),
        _modal_dialog_error(
            mutation=True,
            phase="running",
            diagnostic="대상 한컴 창에 대화상자가 떠 있습니다",
        ),
    )

    assert all(error.mutation_started is False for error in before_mutation)
    assert all("mutation_started=false" in error.reason for error in before_mutation)
    assert all(error.mutation_started is True for error in after_mutation)
    assert all("mutation_started=true" in error.reason for error in after_mutation)


def test_legacy_internal_pre_mutation_wrapper_remains_retry_safe() -> None:
    inputs = HwpOperateInputs(operation="table.fill_existing")
    cause = HwpLiveError("native inspection failed")
    wrapper = HwpLiveError(f"{cause.reason}; mutation_started=false")
    wrapper.__cause__ = cause

    result = transport_error_result(inputs, wrapper)

    assert result.changed is False
    assert result.partial_change is False
    assert result.partial_mutation is False
    assert result.retry_safe is True

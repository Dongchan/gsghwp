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

from hwp_operation_contract import OperationResult  # noqa: E402
from hwp_operation_descriptor import (  # noqa: E402
    ATOMIC_ACTION_VERIFICATION_MODES,
    OperationVerificationMode,
)
from hwp_operation_verification import enforce_operation_verification  # noqa: E402


def _result(
    verification: OperationVerificationMode,
    *,
    verified: bool = True,
    partial_change: bool = False,
    partial_mutation: bool | None = None,
    reconcile_required: bool = False,
) -> OperationResult:
    return OperationResult(
        status="executed",
        changed=True,
        query="atomic action verification",
        registry_entries=1,
        lookup_microseconds=0,
        message="result",
        verification=verification,
        verified=verified,
        partial_change=partial_change,
        partial_mutation=partial_mutation,
        reconcile_required=reconcile_required,
    )


def test_atomic_action_snapshot_readback_survives_the_final_gate() -> None:
    result = enforce_operation_verification(
        None,
        _result("native_snapshot_before_after"),
    )

    assert ATOMIC_ACTION_VERIFICATION_MODES == ("native_snapshot_before_after",)
    assert result.verified is True


def test_atomic_unconfirmed_snapshot_readback_remains_unverified() -> None:
    result = enforce_operation_verification(
        None,
        _result("native_snapshot_before_after", verified=False),
    )

    assert result.verified is False


@pytest.mark.parametrize(
    ("partial_change", "partial_mutation", "reconcile_required"),
    (
        (True, None, False),
        (False, True, False),
        (False, None, True),
    ),
    ids=("partial-change", "partial-mutation", "reconcile-required"),
)
def test_atomic_snapshot_mode_rejects_inconsistent_results(
    partial_change: bool,
    partial_mutation: bool | None,
    reconcile_required: bool,
) -> None:
    result = _result(
        "native_snapshot_before_after",
        partial_change=partial_change,
        partial_mutation=partial_mutation,
        reconcile_required=reconcile_required,
    )

    enforced = enforce_operation_verification(None, result)

    assert enforced.verified is False


def test_atomic_native_action_result_without_readback_remains_unverified() -> None:
    result = enforce_operation_verification(
        None,
        _result("native_action_result"),
    )

    assert result.verified is False


def test_workflow_results_still_use_descriptor_verification_modes() -> None:
    action_only = enforce_operation_verification(
        "text.replace",
        _result("native_action_result"),
    )
    descriptor_readback = enforce_operation_verification(
        "text.replace",
        _result("native_operation_specific_readback"),
    )

    assert action_only.verified is False
    assert descriptor_readback.verified is True

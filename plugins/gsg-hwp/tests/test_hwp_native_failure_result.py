from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_action_contract import (  # noqa: E402
    NativeActionFailure,
    NativeActionFailureEvidence,
)
from hwp_mcp_result_envelope import normalize_production_result  # noqa: E402
from hwp_native_failure_result import native_action_failure_result  # noqa: E402
from hwp_operation_contract import HwpOperateInputs, OperationResult  # noqa: E402


_INPUTS = HwpOperateInputs(
    request_id="native-failure-result",
    operation="table.fill_existing",
)


def _failure(
    *,
    partial_mutation: bool,
    retry_safe: bool,
    before: str | None,
    after: str | None,
) -> NativeActionFailure:
    return NativeActionFailure(
        NativeActionFailureEvidence(
            code="CELL_NOT_FOUND",
            location="A1",
            message="target disappeared",
            commands_completed=3,
            failed_step="CELL",
            partial_mutation=partial_mutation,
            retry_safe=retry_safe,
            structure_digest_before=before,
            structure_digest_after=after,
        )
    )


def test_non_mutating_native_prefix_does_not_become_partial_change() -> None:
    raw = native_action_failure_result(
        "fill table",
        _failure(
            partial_mutation=False,
            retry_safe=True,
            before="same",
            after="same",
        ),
    )

    result = normalize_production_result(raw, _INPUTS)

    assert result.status == "operation_failed"
    assert result.changed is False
    assert result.partial_change is False
    assert result.partial_mutation is False
    assert result.reconcile_required is False
    assert result.retry_safe is True


def test_confirmed_native_mutation_remains_partial_change() -> None:
    raw = native_action_failure_result(
        "fill table",
        _failure(
            partial_mutation=True,
            retry_safe=False,
            before="same",
            after="same",
        ),
    )

    result = normalize_production_result(raw, _INPUTS)

    assert result.status == "partial_change"
    assert result.changed is True
    assert result.partial_change is True
    assert result.partial_mutation is True
    assert result.reconcile_required is True
    assert result.retry_safe is False


def test_incomplete_native_no_mutation_evidence_is_conservative() -> None:
    raw = native_action_failure_result(
        "fill table",
        _failure(
            partial_mutation=False,
            retry_safe=True,
            before="before",
            after=None,
        ),
    )

    result = normalize_production_result(raw, _INPUTS)

    assert result.status == "partial_change"
    assert result.changed is True
    assert result.partial_change is True
    assert result.partial_mutation is None
    assert result.reconcile_required is True
    assert result.retry_safe is False


def test_non_native_failure_command_count_keeps_existing_conservative_rule() -> None:
    raw = OperationResult(
        status="operation_failed",
        query="another workflow",
        registry_entries=1,
        lookup_microseconds=0,
        message="failed",
        commands_executed=3,
        commands_completed=3,
        partial_mutation=False,
        retry_safe=True,
    )

    result = normalize_production_result(raw, _INPUTS)

    assert result.status == "partial_change"
    assert result.partial_change is True
    assert result.changed is True

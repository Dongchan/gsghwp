"""`enforce_operation_verification` must preserve the `verified` tri-state.

The function's job is to refuse to call anything ``True`` unless it was proven
by a descriptor-approved readback. It used to compute a plain ``bool``, which
also rewrote ``None`` ("the operation collected no evidence") into ``False``
("the readback disagreed") before ``commit()`` could tell them apart.

Only that rewrite changes. The ``True -> False`` demotion is pinned here by an
exhaustive differential test against the previous implementation.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path


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
from hwp_operation_verification import (  # noqa: E402
    _approved_verification_modes,
    enforce_operation_verification,
)
from hwp_public_contract import to_public_action_result  # noqa: E402


_EVIDENCE_MARKER = "[verification=evidence_unavailable]"

_WORKFLOWS = (
    None,
    "style.apply",
    "text.replace",
    "image.replace",
    "document.save",
    "document.save_reopen_verify",
)
_STATUSES = (
    "executed",
    "operation_failed",
    "partial_change",
    "transport_error",
    "not_found",
)
_MODES = (
    None,
    "native_action_result",
    "native_snapshot_before_after",
    "native_operation_specific_readback",
    "native_save_result",
)
_SAVE_WORKFLOWS = frozenset({"document.save", "document.save_reopen_verify"})


def _result(
    *,
    status: str = "executed",
    verified: bool | None = None,
    verification: str | None = "native_snapshot_before_after",
    partial_change: bool = False,
    partial_mutation: bool | None = False,
    reconcile_required: bool = False,
) -> OperationResult:
    return OperationResult.model_validate(
        {
            "status": status,
            "changed": True,
            "query": "스타일 적용",
            "registry_entries": 1,
            "lookup_microseconds": 0,
            "message": "프로토콜 9 C++/ATL 네이티브 개체 recipe를 실행했습니다",
            "verification": verification,
            "verified": verified,
            "partial_change": partial_change,
            "partial_mutation": partial_mutation,
            "reconcile_required": reconcile_required,
        }
    )


def _previous_rule(
    workflow_id: str | None,
    result: OperationResult,
) -> OperationResult:
    """Verbatim copy of the implementation before the tri-state change.

    Kept as a differential oracle so any drift in the `True -> False` demotion
    shows up as a test failure rather than a silent behaviour change.
    """
    verification_modes = _approved_verification_modes(workflow_id)
    verified = (
        result.status == "executed"
        and result.verified is True
        and result.verification is not None
        and result.verification in verification_modes
        and not result.partial_change
        and result.partial_mutation is not True
        and not result.reconcile_required
    )
    if result.verified is verified:
        return result
    return result.model_copy(update={"verified": verified})


def _every_input() -> Iterator[tuple[str | None, OperationResult]]:
    for workflow_id in _WORKFLOWS:
        for status in _STATUSES:
            for verified in (True, False, None):
                for verification in _MODES:
                    for partial_change in (False, True):
                        for partial_mutation in (None, False, True):
                            for reconcile_required in (False, True):
                                yield (
                                    workflow_id,
                                    _result(
                                        status=status,
                                        verified=verified,
                                        verification=verification,
                                        partial_change=partial_change,
                                        partial_mutation=partial_mutation,
                                        reconcile_required=reconcile_required,
                                    ),
                                )


def _preserves_evidence_gap(
    workflow_id: str | None,
    result: OperationResult,
) -> bool:
    """The documented condition under which `None` is allowed to survive."""
    return (
        result.verified is None
        and workflow_id not in _SAVE_WORKFLOWS
        and result.status == "executed"
        and result.verification is not None
        and result.verification in _approved_verification_modes(workflow_id)
        and not result.partial_change
        and result.partial_mutation is not True
        and not result.reconcile_required
    )


# --------------------------------------------------------------------------
# Safety: the True -> False demotion is untouched.
# --------------------------------------------------------------------------


def test_non_none_verified_is_bit_identical_to_the_previous_rule() -> None:
    checked = 0
    for workflow_id, result in _every_input():
        if result.verified is None:
            continue
        checked += 1
        assert (
            enforce_operation_verification(workflow_id, result).verified
            == _previous_rule(workflow_id, result).verified
        ), (workflow_id, result.status, result.verified, result.verification)
    # Guard against the loop silently checking nothing.
    assert checked == 2 * 5 * 5 * 2 * 3 * 2 * len(_WORKFLOWS)


def test_the_only_difference_from_the_previous_rule_is_the_evidence_gap() -> None:
    differing = 0
    for workflow_id, result in _every_input():
        current = enforce_operation_verification(workflow_id, result).verified
        previous = _previous_rule(workflow_id, result).verified
        if current is previous:
            continue
        differing += 1
        # Every difference must be a preserved None, never anything else.
        assert current is None
        assert previous is False
        assert _preserves_evidence_gap(workflow_id, result)
    # The change must actually do something, or this test proves nothing.
    assert differing > 0


def test_missing_evidence_is_never_promoted_to_true() -> None:
    for workflow_id, result in _every_input():
        if result.verified is not None:
            continue
        assert enforce_operation_verification(workflow_id, result).verified is not True


def test_verified_true_without_approved_mode_is_still_demoted() -> None:
    enforced = enforce_operation_verification(
        "text.replace",
        _result(verified=True, verification="native_action_result"),
    )

    assert enforced.verified is False


def test_verified_true_with_partial_change_is_still_demoted() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=True, partial_change=True),
    )

    assert enforced.verified is False


def test_verified_true_with_partial_mutation_is_still_demoted() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=True, partial_mutation=True),
    )

    assert enforced.verified is False


def test_verified_true_with_reconcile_required_is_still_demoted() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=True, reconcile_required=True),
    )

    assert enforced.verified is False


def test_verified_true_on_non_executed_status_is_still_demoted() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=True, status="operation_failed"),
    )

    assert enforced.verified is False


def test_verified_false_input_stays_false() -> None:
    enforced = enforce_operation_verification("style.apply", _result(verified=False))

    assert enforced.verified is False


# --------------------------------------------------------------------------
# Core: missing evidence survives when nothing disqualifies it.
# --------------------------------------------------------------------------


def test_missing_evidence_survives_when_nothing_disqualifies_it() -> None:
    enforced = enforce_operation_verification("style.apply", _result(verified=None))

    assert enforced.verified is None


def test_missing_evidence_survives_for_sizeless_image_replace() -> None:
    enforced = enforce_operation_verification("image.replace", _result(verified=None))

    assert enforced.verified is None


def test_missing_evidence_leaves_the_rest_of_the_result_untouched() -> None:
    original = _result(verified=None)

    enforced = enforce_operation_verification("style.apply", original)

    assert enforced.model_dump() == original.model_dump()


# --------------------------------------------------------------------------
# Safety: a disqualified evidence gap is still False.
# --------------------------------------------------------------------------


def test_missing_evidence_with_reconcile_required_becomes_false() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=None, reconcile_required=True),
    )

    assert enforced.verified is False


def test_missing_evidence_with_partial_mutation_becomes_false() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=None, partial_mutation=True),
    )

    assert enforced.verified is False


def test_missing_evidence_with_partial_change_becomes_false() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=None, partial_change=True),
    )

    assert enforced.verified is False


def test_missing_evidence_on_non_executed_status_becomes_false() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=None, status="transport_error"),
    )

    assert enforced.verified is False


def test_missing_evidence_with_unapproved_mode_becomes_false() -> None:
    enforced = enforce_operation_verification(
        "text.replace",
        _result(verified=None, verification="native_action_result"),
    )

    assert enforced.verified is False


def test_missing_evidence_without_any_mode_becomes_false() -> None:
    enforced = enforce_operation_verification(
        "style.apply",
        _result(verified=None, verification=None),
    )

    assert enforced.verified is False


# --------------------------------------------------------------------------
# Safety: save workflows keep the strict bool.
# --------------------------------------------------------------------------


def test_save_workflows_never_carry_missing_evidence() -> None:
    for workflow_id in sorted(_SAVE_WORKFLOWS):
        mode = (
            "native_save_result"
            if workflow_id == "document.save"
            else "native_save_reopen_result"
        )
        enforced = enforce_operation_verification(
            workflow_id,
            _result(verified=None, verification=mode),
        )

        assert enforced.verified is False, workflow_id


def test_no_save_result_reaches_commit_with_missing_evidence() -> None:
    for workflow_id, result in _every_input():
        if workflow_id not in _SAVE_WORKFLOWS:
            continue
        assert (
            enforce_operation_verification(workflow_id, result).verified is not None
        ), workflow_id


# --------------------------------------------------------------------------
# End to end: the evidence gap reaches commit() and the public message.
# --------------------------------------------------------------------------


def test_evidence_gap_survives_enforce_then_commit_into_the_public_message(
    tmp_path: Path,
) -> None:
    document = OpenDocument(
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
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    prepared = idempotency.prepare(
        document,
        "스타일 적용",
        HwpOperateInputs(
            request_id="end-to-end-operation",
            document=document.full_name,
            operation="style.apply",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    # Exactly what the executor does: enforce, then commit.
    enforced = enforce_operation_verification("style.apply", _result(verified=None))
    assert enforced.verified is None

    committed = idempotency.commit(prepared, enforced)
    public = to_public_action_result(committed, ())

    assert committed.verified is None
    assert committed.status == "executed"
    assert committed.reconcile_required is False
    assert public.status == "succeeded"
    assert _EVIDENCE_MARKER in public.message

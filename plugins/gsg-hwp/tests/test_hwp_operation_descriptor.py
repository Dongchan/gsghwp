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

from hwp_live_contract import OpenDocument  # noqa: E402
from hwp_live_session_workflow import WORKFLOW_REQUIRED_INPUTS  # noqa: E402
from hwp_mcp_registry import tool_spec  # noqa: E402
from hwp_operation_certification import certified_recipe  # noqa: E402
from hwp_operation_contract import HwpOperateInputs, OperationResult  # noqa: E402
from hwp_operation_descriptor import (  # noqa: E402
    operation_descriptor,
    operation_descriptors,
)
from hwp_operation_verification import enforce_operation_verification  # noqa: E402
from hwp_operation_idempotency import (  # noqa: E402
    OperationIdempotency,
    OperationTicket,
)
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_workflow_router import resolve_explicit_workflow  # noqa: E402


def _result(
    *,
    verification: str,
    status: str = "executed",
    verified: bool | None = True,
) -> OperationResult:
    return OperationResult.model_validate(
        {
            "status": status,
            "changed": True,
            "query": "본문 교체",
            "registry_entries": 1,
            "lookup_microseconds": 0,
            "message": "result",
            "verification": verification,
            "verified": verified,
        }
    )


def test_text_patch_workflows_share_one_certified_readback_recipe_shape() -> None:
    expected_steps = ("ResolveTextPatch", "PatchText", "VerifyTextPatch")

    for workflow in ("text.insert", "text.replace", "document.replace_selection"):
        descriptor = operation_descriptor(workflow)
        assert descriptor is not None
        assert descriptor.routing_steps == expected_steps
        assert descriptor.recipe is not None
        assert descriptor.recipe.steps == expected_steps
        assert descriptor.verification_modes == (
            "native_operation_specific_readback",
        )


def test_descriptor_drives_certification_routing_schema_and_tool_mapping() -> None:
    for descriptor in operation_descriptors():
        if descriptor.recipe is not None:
            certified = certified_recipe(descriptor.workflow_id)
            assert certified is not None
            assert certified.recipe_id == descriptor.recipe.recipe_id
            assert certified.steps == descriptor.recipe.steps
            assert certified.atomicity == descriptor.recipe.atomicity

        resolution = resolve_explicit_workflow(
            descriptor.workflow_id,
            descriptor.workflow_id,
        )
        assert resolution.steps == descriptor.routing_steps
        assert resolution.candidates[0].execution == descriptor.execution
        assert (
            WORKFLOW_REQUIRED_INPUTS[descriptor.workflow_id]
            == descriptor.input_schema
        )

        for public_tool in descriptor.public_tools:
            assert descriptor.workflow_id in tool_spec(public_tool).workflows


def test_patch_tool_exposes_insert_and_replace_workflows_from_descriptor() -> None:
    spec = tool_spec("hwp_patch_text")

    assert spec.workflow == "text.replace"
    assert spec.workflows == ("text.replace", "text.insert")


def test_verified_true_requires_descriptor_approved_readback() -> None:
    action_only = enforce_operation_verification(
        "text.replace",
        _result(verification="native_action_result"),
    )
    readback = enforce_operation_verification(
        "text.replace",
        _result(verification="native_operation_specific_readback"),
    )
    explicit_false = enforce_operation_verification(
        "text.replace",
        _result(
            verification="native_operation_specific_readback",
            verified=False,
        ),
    )
    failed = enforce_operation_verification(
        "text.replace",
        _result(
            verification="native_operation_specific_readback",
            status="operation_failed",
        ),
    )

    assert action_only.verified is False
    assert readback.verified is True
    assert explicit_false.verified is False
    assert failed.verified is False


def test_unverified_executed_mutation_is_not_committed(
    tmp_path: Path,
) -> None:
    operation_id = "unverified-operation"
    inputs = HwpOperateInputs(
        request_id=operation_id,
        operation="text.replace",
    )
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
    prepared = idempotency.prepare(document, "본문 교체", inputs, None)

    assert isinstance(prepared, OperationTicket)
    unverified = enforce_operation_verification(
        "text.replace",
        _result(verification="native_action_result"),
    )
    result = idempotency.commit(prepared, unverified)

    assert result.status == "operation_failed"
    assert result.idempotency_status == "failed"
    assert result.verified is False
    assert result.reconcile_required is True
    assert result.journal_failure_code == "partial_mutation_reconcile_required"

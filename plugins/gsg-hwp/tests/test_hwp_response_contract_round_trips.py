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
from hwp_mcp_registry import tool_spec, tool_specs  # noqa: E402
from hwp_operation_contract import HwpOperateInputs, OperationResult  # noqa: E402
from hwp_operation_idempotency import (  # noqa: E402
    OperationIdempotency,
    OperationTicket,
)
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_public_contract import PublicActionResult, to_public_action_result  # noqa: E402
from hwp_table_format_inference import TableFormatAmbiguity  # noqa: E402


_EXISTING_PUBLIC_RESULT_FIELDS = frozenset(
    {
        "status",
        "message",
        "request_id",
        "idempotency_status",
        "runtime",
        "verified",
        "modified",
        "required_inputs",
        "target_candidates",
        "format_candidates",
        "recipe_id",
        "commands_executed",
        "updated_addresses",
        "created_target_ids",
        "retry_safe",
        "reconcile_required",
        "save_evidence",
    }
)
_NEW_CONTINUATION_FIELDS = frozenset(
    {
        "affected_pages",
        "state_token",
        "affected_target_ids",
        "selected_target_id",
        "input_guidance",
        "retry_operation_id",
    }
)


def test_success_response_exposes_optional_continuation_scope() -> None:
    # Given
    operation = OperationResult(
        request_id="same-logical-operation",
        result_digest="c" * 64,
        status="executed",
        changed=True,
        query="edit",
        registry_entries=1,
        lookup_microseconds=0,
        message="changed",
        current_page=7,
        modified=True,
        verified=True,
        resolved_target_id="table-7",
        created_control_ids=("picture-9",),
        retry_safe=True,
    )

    # When
    result = to_public_action_result(operation, ())
    fields = PublicActionResult.model_fields

    # Then
    assert result.affected_pages == (7,)
    assert result.state_token == "c" * 64
    assert result.selected_target_id == "table-7"
    assert result.affected_target_ids == ("table-7", "picture-9")
    assert result.retry_operation_id == operation.request_id
    assert _EXISTING_PUBLIC_RESULT_FIELDS <= fields.keys()
    assert _NEW_CONTINUATION_FIELDS <= fields.keys()
    assert {name for name, field in fields.items() if field.is_required()} == {
        "status",
        "message",
        "runtime",
        "verified",
        "modified",
        "retry_safe",
    }


def test_failed_journal_status_keeps_reconciliation_scope(tmp_path: Path) -> None:
    # Given
    operation_id = "reconcile-operation"
    failed = OperationResult(
        request_id=operation_id,
        status="partial_change",
        changed=True,
        query="edit",
        registry_entries=1,
        lookup_microseconds=0,
        message="verification failed",
        current_page=4,
        modified=True,
        verified=False,
        structure_digest_after="d" * 64,
        reconcile_required=True,
        retry_safe=False,
        resolved_target_id="table-4",
        updated_addresses=("B2",),
    )
    document = OpenDocument(
        selector="active-document",
        title="reconcile.hwp",
        full_name="C:/documents/reconcile.hwp",
        document_id=47,
        format="HWP",
        edit_mode=1,
        modified=True,
        page_count=7,
        active=True,
        window_handle=404,
    )
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    prepared = idempotency.prepare(
        document,
        "edit",
        HwpOperateInputs(
            request_id=operation_id,
            operation="table.fill_existing",
        ),
        None,
    )
    match prepared:  # noqa: E501  # noqa: MATCH_OK — every non-ticket outcome fails the test.
        case OperationTicket() as ticket:
            _ = idempotency.commit(ticket, failed)
        case _:
            raise AssertionError("new operation did not receive an execution ticket")

    # When
    blocked = idempotency.status(document, operation_id)
    result = to_public_action_result(blocked, ())

    # Then
    assert blocked.current_page == 4
    assert blocked.updated_addresses == ("B2",)
    assert result.affected_pages == (4,)
    assert result.state_token == "d" * 64
    assert result.affected_target_ids == ("table-4",)


def test_format_ambiguity_without_candidates_requests_executable_cells() -> None:
    # Given
    ambiguity = TableFormatAmbiguity("format is ambiguous")
    operation = OperationResult(
        request_id="format-operation",
        status="needs_input",
        query="fill",
        registry_entries=1,
        lookup_microseconds=0,
        required_inputs=("inputs.policy.numeric_value_mode",),
        message=str(ambiguity),
        retry_safe=True,
    )

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert ambiguity.candidates == ()
    assert "cells" in str(ambiguity)
    assert result.format_candidates == ()
    assert result.required_inputs == ("cells",)
    assert result.input_guidance
    assert all("format_candidates" not in item for item in result.input_guidance)


def test_write_descriptions_advertise_continuation_retry_and_bulk_fields() -> None:
    # Given
    direct_writes = tuple(
        spec
        for spec in tool_specs("production")
        if spec.operation == "write" and spec.handler_source == "bindings"
    )

    # When
    descriptions = tuple(spec.description for spec in direct_writes)

    # Then
    assert descriptions
    assert all("affected_pages" in description for description in descriptions)
    assert all("state_token" in description for description in descriptions)
    assert all("selected_target_id" in description for description in descriptions)
    assert all("affected_target_ids" in description for description in descriptions)
    assert all("operation_id" in description for description in descriptions)
    assert "layout.blocks" in tool_spec("hwp_append_layout").description
    assert "bulk" in tool_spec("hwp_append_layout").description
    assert "layout.blocks" in tool_spec("hwp_insert_layout").description
    assert "bulk" in tool_spec("hwp_insert_layout").description

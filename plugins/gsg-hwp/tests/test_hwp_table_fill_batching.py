from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast
from unittest.mock import MagicMock, patch

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_api import HwpComApplication, HwpComDocument, LiveHwpApplication  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_contract import (  # noqa: E402
    NativeActionFailure,
    NativeActionFailureEvidence,
)
from hwp_live_native_action_models import (  # noqa: E402
    CellCommand,
    NativeActionRequest,
    NativeActionResult,
    SetCellTextCommand,
    TextPatchCommand,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session_table_fill import operate_table_fill  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    DocumentStructure,
    StructureCell,
    StructurePosition,
    StructureTable,
)
from hwp_live_workflow_table import (  # noqa: E402
    PreparedWorkflowTableFill,
    prepare_table_fill,
    table_fill_chunks,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    OperationResult,
    WorkflowCandidate,
    WorkflowResolution,
)
from hwp_public_contract import to_public_action_result  # noqa: E402


def _candidate() -> HwpDocumentCandidate:
    document = cast(
        HwpComDocument,
        cast(
            object,
            SimpleNamespace(
                DocumentID=17,
                FullName="C:/documents/bulk-fill.hwp",
            ),
        ),
    )
    return HwpDocumentCandidate(
        selector="active",
        moniker_name="fixture",
        application=cast(HwpComApplication, object()),
        document=document,
        document_id=17,
        full_name="C:/documents/bulk-fill.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )


def _resolution() -> WorkflowResolution:
    return WorkflowResolution(
        query="기존 표 채우기",
        status="resolved",
        lookup_microseconds=0,
        workflow_id="table.fill_existing",
        candidates=(
            WorkflowCandidate(
                workflow_id="table.fill_existing",
                description="기존 표 채우기",
                steps=("ResolveTable", "FillCells", "VerifyStructure"),
                execution="recipe",
                confidence=1,
                match_kind="explicit",
            ),
        ),
        steps=("ResolveTable", "FillCells", "VerifyStructure"),
        match_kind="explicit",
    )


def _values(count: int) -> dict[str, str]:
    return {f"A{row}": f"값-{row}" for row in range(1, count + 1)}


def _table(count: int, values: dict[str, str] | None = None) -> StructureTable:
    actual = {} if values is None else values
    return StructureTable(
        table_ref="table-ref-00000001",
        control_instance_id="table-17",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=count,
        columns=1,
        merges=(),
        cells=tuple(
            StructureCell(
                address=f"A{row}",
                row=row - 1,
                column=0,
                owner_address=f"A{row}",
                text=actual.get(f"A{row}", ""),
            )
            for row in range(1, count + 1)
        ),
    )


def _structure(table: StructureTable) -> DocumentStructure:
    return DocumentStructure(
        selector="active",
        document_id=17,
        full_name="C:/documents/bulk-fill.hwp",
        window_handle=100,
        page=1,
        page_count=1,
        state_token="0" * 16,
        page_text="",
        paragraphs=(),
        controls=(),
        tables=(table,),
    )


def _prepared(count: int) -> tuple[StructureTable, PreparedWorkflowTableFill]:
    table = _table(count)
    prepared = prepare_table_fill(
        _candidate(),
        table,
        1,
        HwpOperateData(cells=_values(count)),
        HwpOperatePolicy(preserve_style=False),
        HwpOperatePostconditions(),
    )
    return table, prepared


def _success(request: NativeActionRequest) -> NativeActionResult:
    return NativeActionResult(
        commands_executed=len(request.commands),
        actions_executed=0,
        text_insertions=sum(
            isinstance(command, SetCellTextCommand) for command in request.commands
        ),
        image_insertions=0,
        elapsed_microseconds=100,
        created_control_ids=(),
    )


class _Execute(Protocol):
    def __call__(
        self,
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult: ...


def _operate(
    table: StructureTable,
    prepared: PreparedWorkflowTableFill,
    after: StructureTable,
    execute: _Execute,
) -> tuple[
    OperationResult,
    DocumentStructure,
    MagicMock,
    tuple[MagicMock, MagicMock],
]:
    read_snapshot = patch(
        "hwp_live_session_table_fill.read_native_snapshot",
        side_effect=(
            SimpleNamespace(
                control_type="",
                control_instance_id=None,
                current_page=1,
            ),
            SimpleNamespace(current_page=1),
        ),
    )
    inspect = patch(
        "hwp_live_session_table_fill.inspect_candidate_structure",
        side_effect=(_structure(table), _structure(after)),
    )
    resolve = patch(
        "hwp_live_session_table_fill.resolve_workflow_table",
        return_value=SimpleNamespace(table=table, table_index=1, candidates=()),
    )
    prepare = patch(
        "hwp_live_session_table_fill.prepare_table_fill",
        return_value=prepared,
    )
    native = patch(
        "hwp_live_session_table_fill.execute_native_actions",
        side_effect=execute,
    )
    with (
        read_snapshot as read_mock,
        inspect as inspect_mock,
        resolve,
        prepare,
        native as native_mock,
    ):
        result, returned = operate_table_fill(
            _candidate(),
            cast(LiveHwpApplication, object()),
            _resolution(),
            HwpOperateTarget(
                kind="table",
                page_hint=1,
                control_instance_id="table-17",
            ),
            HwpOperateData(cells=_values(len(table.cells))),
            HwpOperatePolicy(preserve_style=False),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )
    assert result is not None
    assert returned is not None
    return (
        result,
        returned,
        cast(MagicMock, read_mock),
        (cast(MagicMock, inspect_mock), cast(MagicMock, native_mock)),
    )


def test_large_fill_uses_bounded_native_calls_without_quadratic_preflight() -> None:
    table, prepared = _prepared(400)
    chunks = table_fill_chunks(prepared, table)
    requests: list[NativeActionRequest] = []

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        _ = window_handle
        assert minimum_version == 9
        requests.append(request)
        return _success(request)

    result, _, _, _ = _operate(
        table,
        prepared,
        _table(400, _values(400)),
        execute,
    )

    assert len(requests) == len(chunks) > 1
    assert all(
        any(isinstance(command, CellCommand) for command in request.commands)
        for request in requests
    )
    assert all(
        sum(isinstance(command, SetCellTextCommand) for command in request.commands)
        <= 100
        for request in requests
    )
    executed_addresses = tuple(
        command.address
        for request in requests
        for command in request.commands
        if isinstance(command, SetCellTextCommand)
    )
    assert executed_addresses == tuple(address for address, _ in prepared.replacements)
    assert len(set(executed_addresses)) == 400
    assert result.status == "executed"
    assert result.updated_addresses == executed_addresses


def test_sparse_fill_in_large_table_bypasses_quadratic_preflight_once() -> None:
    table = _table(1_000)
    prepared = prepare_table_fill(
        _candidate(),
        table,
        1,
        HwpOperateData(cells=_values(50)),
        HwpOperatePolicy(preserve_style=False),
        HwpOperatePostconditions(),
    )

    chunks = table_fill_chunks(prepared, table)

    assert len(chunks) == 1
    assert chunks[0].request is not prepared.request
    assert any(
        isinstance(command, CellCommand) for command in chunks[0].request.commands
    )
    assert (
        sum(
            isinstance(command, SetCellTextCommand)
            for command in chunks[0].request.commands
        )
        == 50
    )


def test_sparse_transformed_call_failure_reports_exact_readback() -> None:
    table = _table(1_000)
    prepared = prepare_table_fill(
        _candidate(),
        table,
        1,
        HwpOperateData(cells=_values(50)),
        HwpOperatePolicy(preserve_style=False),
        HwpOperatePostconditions(),
    )
    confirmed = ("A1", "A2")
    failure = NativeActionFailure(
        NativeActionFailureEvidence(
            code="TEXT_PATCH_READBACK",
            location=confirmed[-1],
            message="readback failed",
            commands_completed=5,
            failed_step="SET_CELL_TEXT",
            partial_mutation=True,
            retry_safe=False,
            structure_digest_before="before",
            structure_digest_after="after",
        )
    )

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        _ = window_handle, request, minimum_version
        raise failure

    result, _, _, mocks = _operate(
        table,
        prepared,
        _table(1_000, {address: _values(50)[address] for address in confirmed}),
        execute,
    )
    _, native = mocks

    assert native.call_count == 1
    assert result.status == "partial_change"
    assert result.updated_addresses == confirmed
    assert result.commands_completed == 5
    assert result.retry_safe is False
    assert result.reconcile_required is True


def test_100_cell_fill_keeps_one_original_call_and_no_extra_reads() -> None:
    table, prepared = _prepared(100)

    def execute_once(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        _ = window_handle
        assert minimum_version == 9
        return _success(request)

    result, _, read_snapshot, mocks = _operate(
        table,
        prepared,
        _table(100, _values(100)),
        execute_once,
    )
    inspect, execute = mocks

    execute.assert_called_once()
    executed_request = cast(NativeActionRequest, execute.call_args.args[1])
    assert executed_request is prepared.request
    assert not any(
        isinstance(command, CellCommand) for command in executed_request.commands
    )
    assert read_snapshot.call_count == 2
    assert inspect.call_count == 2
    assert result.status == "executed"
    assert result.commands_executed == len(prepared.request.commands)


def test_later_chunk_failure_reports_only_readback_confirmed_addresses() -> None:
    table, prepared = _prepared(205)
    chunks = table_fill_chunks(prepared, table)
    first_addresses = tuple(address for address, _ in chunks[0].replacements)
    second_confirmed = tuple(address for address, _ in chunks[1].replacements[:2])
    confirmed = (*first_addresses, *second_confirmed)
    corrupted_address = first_addresses[0]
    readback_confirmed = tuple(
        address for address in confirmed if address != corrupted_address
    )
    after_values = {address: _values(205)[address] for address in readback_confirmed}
    failure = NativeActionFailure(
        NativeActionFailureEvidence(
            code="TEXT_PATCH_READBACK",
            location=second_confirmed[-1],
            message="readback failed",
            commands_completed=5,
            failed_step="SET_CELL_TEXT",
            partial_mutation=True,
            retry_safe=False,
            structure_digest_before="before",
            structure_digest_after="after",
        )
    )
    calls = 0

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        nonlocal calls
        _ = window_handle, minimum_version
        calls += 1
        if calls == 2:
            raise failure
        return _success(request)

    result, returned, _, mocks = _operate(
        table,
        prepared,
        _table(205, after_values),
        execute,
    )
    _, native = mocks
    public = to_public_action_result(result, ())

    assert native.call_count == 2
    assert result.status == "partial_change"
    assert result.updated_addresses == readback_confirmed
    assert result.commands_completed == len(chunks[0].request.commands) + 5
    assert result.partial_mutation is True
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert returned.tables[0].cells == _table(205, after_values).cells
    assert public.status == "partial_failure"
    assert public.updated_addresses == readback_confirmed


def test_final_verification_failure_reports_only_actual_matching_addresses() -> None:
    table, prepared = _prepared(400)
    mismatched = "A1"
    matching = tuple(
        address for address, _ in prepared.replacements if address != mismatched
    )
    after_values = {address: _values(400)[address] for address in matching}

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        _ = window_handle, minimum_version
        return _success(request)

    result, _, _, mocks = _operate(
        table,
        prepared,
        _table(400, after_values),
        execute,
    )
    _, native = mocks

    assert native.call_count > 1
    assert result.status == "partial_change"
    assert result.updated_addresses == matching
    assert result.commands_completed == sum(
        len(cast(NativeActionRequest, call.args[1]).commands)
        for call in native.call_args_list
    )
    assert result.retry_safe is False
    assert result.reconcile_required is True


def test_incomplete_failure_readback_is_unknown_and_requires_reconciliation() -> None:
    table, prepared = _prepared(205)
    failure = NativeActionFailure(
        NativeActionFailureEvidence(
            code="CELL_NOT_FOUND",
            location="A1",
            message="cell disappeared before mutation",
            commands_completed=3,
            failed_step="CELL",
            partial_mutation=False,
            retry_safe=True,
            structure_digest_before="same",
            structure_digest_after="same",
        )
    )
    missing_target = _table(205).model_copy(
        update={"control_instance_id": "table-other"}
    )

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        _ = window_handle, request, minimum_version
        raise failure

    result, _, _, _ = _operate(
        table,
        prepared,
        missing_target,
        execute,
    )

    assert result.status == "operation_failed"
    assert result.changed is False
    assert result.partial_mutation is None
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert result.commands_completed == 3
    assert result.updated_addresses == ()


def test_oversized_single_cell_patch_group_fails_before_native_execution() -> None:
    current = {f"A{row}": "old" for row in range(1, 102)}
    desired = {f"A{row}": "new" for row in range(1, 102)}
    current["A1"] = " / ".join(str(value) for value in range(1, 151))
    desired["A1"] = " / ".join(str(value + 1_000) for value in range(1, 151))
    table = _table(101, current)
    prepared = prepare_table_fill(
        _candidate(),
        table,
        1,
        HwpOperateData(cells=desired),
        HwpOperatePolicy(preserve_style=True, numeric_value_mode="display"),
        HwpOperatePostconditions(),
    )

    assert (
        sum(
            isinstance(command, TextPatchCommand)
            for command in prepared.command_groups[0]
        )
        == 150
    )
    with pytest.raises(HwpLiveError, match="안전한 네이티브 호출 예산"):
        _ = table_fill_chunks(prepared, table)


def test_retry_omits_addresses_already_confirmed_after_partial_fill() -> None:
    desired = _values(205)
    confirmed = tuple(list(desired)[:103])
    current = _table(
        205,
        {address: desired[address] for address in confirmed},
    )

    retried = prepare_table_fill(
        _candidate(),
        current,
        1,
        HwpOperateData(cells=desired),
        HwpOperatePolicy(preserve_style=False),
        HwpOperatePostconditions(),
    )
    retried_addresses = tuple(address for address, _ in retried.replacements)

    assert set(retried_addresses).isdisjoint(confirmed)
    assert set(retried_addresses) == set(desired).difference(confirmed)
    assert all(
        command.address not in confirmed
        for group in retried.command_groups
        for command in group
        if isinstance(command, SetCellTextCommand)
    )

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import patch

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_priority_table_recipes as table_recipes  # noqa: E402
from hwp_live_api import (  # noqa: E402
    HwpComApplication,
    HwpComDocument,
    LiveHwpApplication,
)
from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_contract import ConnectedDocument, OpenDocument  # noqa: E402
from hwp_live_layout_contract import LayoutPlan  # noqa: E402
from hwp_live_native_action_models import NativeActionRequest  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    DocumentStructure,
    StructureCell,
    StructurePosition,
    StructureTable,
)
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateAssets,
    HwpOperateData,
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationInputValue,
    OperationResult,
    WorkflowResolution,
)
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs  # noqa: E402
from hwp_public_contract import to_public_action_result  # noqa: E402


Workflow = Literal["table.expand_and_fill", "table.insert_images"]
_CONTROL_ID = "table-control-1"
_DOCUMENT_PATH = "C:/documents/priority-table.hwp"


def _cell(
    address: str,
    row: int,
    *,
    text: str = "",
    has_picture: bool = False,
) -> StructureCell:
    return StructureCell(
        address=address,
        row=row,
        column=0,
        owner_address=address,
        text=text,
        has_picture=has_picture,
    )


def _table(*cells: StructureCell, rows: int) -> StructureTable:
    return StructureTable(
        table_ref="table-ref-00000001",
        control_instance_id=_CONTROL_ID,
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=rows,
        columns=1,
        merges=(),
        cells=cells,
    )


def _snapshot(table: StructureTable, *, token: str) -> DocumentStructure:
    return DocumentStructure(
        selector="priority-table",
        document_id=17,
        full_name=_DOCUMENT_PATH,
        window_handle=101,
        page=1,
        page_count=1,
        state_token=token,
        page_text="",
        paragraphs=(),
        controls=(),
        tables=(table,),
    )


def _candidate() -> HwpDocumentCandidate:
    application = cast(HwpComApplication, object())
    document = cast(
        HwpComDocument,
        cast(
            object,
            SimpleNamespace(
                DocumentID=17,
                FullName=_DOCUMENT_PATH,
                Modified=False,
            ),
        ),
    )
    return HwpDocumentCandidate(
        selector="priority-table",
        moniker_name="priority-table-moniker",
        application=application,
        document=document,
        document_id=17,
        full_name=_DOCUMENT_PATH,
        document_format="HWP",
        edit_mode=1,
        window_handle=101,
        active=True,
        page_count=1,
    )


def _open_document() -> OpenDocument:
    return OpenDocument(
        selector="priority-table",
        title="priority-table.hwp",
        full_name=_DOCUMENT_PATH,
        document_id=17,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=1,
        active=True,
        window_handle=101,
    )


def _resolution(workflow: Workflow) -> WorkflowResolution:
    return WorkflowResolution(
        query=workflow,
        status="resolved",
        lookup_microseconds=1,
        workflow_id=workflow,
    )


def _inputs(
    workflow: Workflow,
    operation_id: str,
    *,
    no_op: bool,
) -> HwpOperateInputs:
    target = HwpOperateTarget(
        kind="table",
        page_hint=1,
        control_instance_id=_CONTROL_ID,
    )
    if workflow == "table.expand_and_fill":
        return HwpOperateInputs(
            request_id=operation_id,
            operation=workflow,
            target=target,
            data=HwpOperateData(
                cells=(
                    {"A1": "replacement"}
                    if no_op
                    else {"A1": "updated", "A2": "inserted"}
                )
            ),
            policy=HwpOperatePolicy(
                allow_row_expansion=True,
                fill_blanks_only=no_op,
            ),
        )
    return HwpOperateInputs(
        request_id=operation_id,
        operation=workflow,
        target=target,
        assets=HwpOperateAssets(images={"A1": Path("image.png")}),
        policy=HwpOperatePolicy(preserve_existing_images=no_op),
    )


def _snapshots(
    workflow: Workflow,
    *,
    no_op: bool,
    failed_readback: bool,
) -> tuple[DocumentStructure, ...]:
    if workflow == "table.expand_and_fill":
        before = _snapshot(
            _table(
                _cell("A1", 0, text="already filled" if no_op else ""),
                rows=1,
            ),
            token="state-token-before",
        )
        if no_op:
            return (before,)
        after_text = "wrong" if failed_readback else "inserted"
        after = _snapshot(
            _table(
                _cell("A1", 0, text="updated"),
                _cell("A2", 1, text=after_text),
                rows=2,
            ),
            token="state-token-after-1",
        )
        return before, after
    before = _snapshot(
        _table(_cell("A1", 0, has_picture=no_op), rows=1),
        token="state-token-before",
    )
    if no_op:
        return (before,)
    after = _snapshot(
        _table(
            _cell("A1", 0, has_picture=not failed_readback),
            rows=1,
        ),
        token="state-token-after-1",
    )
    return before, after


async def _run(
    tmp_path: Path,
    workflow: Workflow,
    *,
    no_op: bool = False,
    failed_readback: bool = False,
    repeat: bool = False,
) -> tuple[tuple[OperationResult, ...], int, int]:
    expected_workflow = workflow
    inputs = _inputs(
        workflow,
        f"{workflow.split('.')[-1]}-{'noop' if no_op else 'mutation'}",
        no_op=no_op,
    )
    snapshots: Iterator[DocumentStructure] = iter(
        _snapshots(
            workflow,
            no_op=no_op,
            failed_readback=failed_readback,
        )
    )
    candidate = _candidate()
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(
        bridge,
        dispatcher,
        OperationJournal(tmp_path / "journal"),
    )
    connected = ConnectedDocument(
        session_id="priority-table-session",
        document=_open_document(),
    )
    recipe_calls = 0
    native_calls = 0

    def inspect(
        _hwp: LiveHwpApplication,
        _candidate: HwpDocumentCandidate,
        _page: int,
        _guard: Callable[[], None],
    ) -> DocumentStructure:
        return next(snapshots)

    def execute(
        _candidate: HwpDocumentCandidate,
        _request: NativeActionRequest,
        minimum_version: int,
    ) -> tuple[int, int]:
        nonlocal native_calls
        native_calls += 1
        if no_op:
            raise AssertionError("verified no-op must not execute native commands")
        assert minimum_version == (12 if workflow == "table.expand_and_fill" else 9)
        return 5, 17

    def operate(
        _session_id: str,
        _intent: str,
        _parameters: Mapping[str, OperationInputValue],
        *,
        resolve_only: bool,
        allow_document_change: bool,
        use_defaults: bool,
        expected_cursor: tuple[int, int, int] | None,
        workflow: HwpWorkflowId | None = None,
        target: HwpOperateTarget | None = None,
        data: HwpOperateData | None = None,
        assets: HwpOperateAssets | None = None,
        policy: HwpOperatePolicy | None = None,
        postconditions: HwpOperatePostconditions | None = None,
        layout: LayoutPlan | None = None,
        recipe: HwpPriorityRecipeInputs | None = None,
    ) -> OperationResult:
        nonlocal recipe_calls
        recipe_calls += 1
        _ = (use_defaults, expected_cursor, layout)
        assert workflow == expected_workflow
        assert policy is not None
        assert postconditions is not None
        result = table_recipes.operate_table_recipe(
            candidate,
            cast(LiveHwpApplication, object()),
            _resolution(expected_workflow),
            target,
            data,
            assets,
            policy,
            postconditions,
            recipe,
            resolve_only=resolve_only,
            allow_document_change=allow_document_change,
        )
        assert result is not None
        return result

    results: list[OperationResult] = []
    with (
        patch.object(HancomBridge, "ensure_connection", return_value=connected),
        patch.object(HancomBridge, "operate", side_effect=operate),
        patch.object(
            table_recipes,
            "inspect_candidate_structure",
            side_effect=inspect,
        ),
        patch.object(table_recipes, "_execute", side_effect=execute),
    ):
        try:
            results.append(await executor.execute(workflow, inputs, None))
            if repeat:
                results.append(await executor.execute(workflow, inputs, None))
        finally:
            await dispatcher.close(lambda: None)
    return tuple(results), recipe_calls, native_calls


@pytest.mark.parametrize(
    "workflow",
    ("table.expand_and_fill", "table.insert_images"),
)
def test_verified_table_recipe_is_committed_and_replayed(
    tmp_path: Path,
    workflow: Workflow,
) -> None:
    results, recipe_calls, native_calls = anyio.run(
        partial(_run, tmp_path, workflow, repeat=True),
    )

    assert tuple(result.status for result in results) == ("executed", "executed")
    assert tuple(result.verified for result in results) == (True, True)
    expected_protocol = 12 if workflow == "table.expand_and_fill" else 9
    assert tuple(result.native_protocol for result in results) == (
        expected_protocol,
        expected_protocol,
    )
    assert tuple(result.idempotency_status for result in results) == (
        "committed",
        "replayed",
    )
    assert tuple(to_public_action_result(result, ()).status for result in results) == (
        "succeeded",
        "succeeded",
    )
    assert recipe_calls == 1
    assert native_calls == 1


@pytest.mark.parametrize(
    "workflow",
    ("table.expand_and_fill", "table.insert_images"),
)
def test_failed_table_recipe_readback_is_reported_as_failure(
    tmp_path: Path,
    workflow: Workflow,
) -> None:
    (result,), recipe_calls, native_calls = anyio.run(
        partial(_run, tmp_path, workflow, failed_readback=True),
    )

    assert result.status == "transport_error"
    assert result.verified is False
    assert result.idempotency_status == "failed"
    assert to_public_action_result(result, ()).status == "partial_failure"
    assert recipe_calls == 1
    assert native_calls == 1


@pytest.mark.parametrize(
    "workflow",
    ("table.expand_and_fill", "table.insert_images"),
)
def test_policy_satisfied_table_noop_is_verified_without_native_commands(
    tmp_path: Path,
    workflow: Workflow,
) -> None:
    (result,), recipe_calls, native_calls = anyio.run(
        partial(_run, tmp_path, workflow, no_op=True),
    )

    assert result.status == "executed"
    assert result.verified is True
    assert result.idempotency_status == "committed"
    assert result.verification == "native_structure_no_change"
    assert result.modified is False
    assert result.commands_executed == 0
    assert to_public_action_result(result, ()).status == "succeeded"
    assert recipe_calls == 1
    assert native_calls == 0

from __future__ import annotations

import sys
from pathlib import Path
from threading import Event
from unittest.mock import patch

import anyio
from pydantic import JsonValue


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_contract import (  # noqa: E402
    ConnectedDocument,
    OpenDocument,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_operation_contract import HwpOperateInputs, OperationResult  # noqa: E402
from hwp_operation_journal import (  # noqa: E402
    OperationJournal,
    document_session_key,
)


def _document() -> OpenDocument:
    return OpenDocument(
        selector="active-document",
        title="cancel.hwp",
        full_name="C:/documents/cancel.hwp",
        document_id=31,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=1,
        active=True,
        window_handle=303,
    )


def _changed_result() -> OperationResult:
    return OperationResult(
        status="executed",
        changed=True,
        query="cancelled mutation",
        registry_entries=1,
        lookup_microseconds=0,
        message="changed once",
        modified=True,
        verification="native_snapshot_before_after",
        verified=True,
    )


def test_mutation_cancelled_before_com_start_changes_nothing() -> None:
    dispatcher = McpThreadDispatcher(watch_workers=1)
    blocker_started = Event()
    release_blocker = Event()
    changes = 0

    def block_executor() -> None:
        blocker_started.set()
        _ = release_blocker.wait()

    def mutate() -> None:
        nonlocal changes
        changes += 1

    async def exercise_cancellation() -> None:
        request_ready = anyio.Event()
        request_finished = anyio.Event()
        scopes: list[anyio.CancelScope] = []

        async def queued_mutation() -> None:
            try:
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    request_ready.set()
                    await dispatcher.run_mutation(mutate)
            finally:
                request_finished.set()

        try:
            async with anyio.create_task_group() as tasks:
                _ = tasks.start_soon(dispatcher.run, block_executor)
                _ = await dispatcher.watch(blocker_started.wait)
                _ = tasks.start_soon(queued_mutation)
                await request_ready.wait()
                await anyio.sleep(0)
                scopes[0].cancel()
                await request_finished.wait()
                release_blocker.set()
        finally:
            release_blocker.set()
            await dispatcher.close(lambda: None)

    anyio.run(exercise_cancellation)

    assert changes == 0


def test_mutation_cancelled_after_com_start_changes_exactly_once() -> None:
    dispatcher = McpThreadDispatcher(watch_workers=1)
    mutation_started = Event()
    release_mutation = Event()
    changes = 0

    def mutate() -> None:
        nonlocal changes
        mutation_started.set()
        _ = release_mutation.wait()
        changes += 1

    async def exercise_cancellation() -> None:
        scopes: list[anyio.CancelScope] = []

        async def call_mutation() -> None:
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await dispatcher.run_mutation(mutate)

        try:
            async with anyio.create_task_group() as tasks:
                _ = tasks.start_soon(call_mutation)
                _ = await dispatcher.watch(mutation_started.wait)
                scopes[0].cancel()
                await anyio.sleep(0)
                release_mutation.set()
        finally:
            release_mutation.set()
            await dispatcher.close(lambda: None)

    anyio.run(exercise_cancellation)

    assert changes == 1


def test_started_mutation_is_committed_once_after_caller_cancellation(
    tmp_path: Path,
) -> None:
    document = _document()
    request_id = "cancel-after-com-start"
    journal = OperationJournal(tmp_path / "journal")
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, journal)
    connected = ConnectedDocument(
        session_id="active-session",
        document=document,
    )
    mutation_started = Event()
    release_mutation = Event()
    changes = 0
    cleanup_states: list[str] = []

    def mutate(*_args: JsonValue, **_kwargs: JsonValue) -> OperationResult:
        nonlocal changes
        mutation_started.set()
        _ = release_mutation.wait()
        changes += 1
        return _changed_result()

    async def exercise_cancellation() -> None:
        scopes: list[anyio.CancelScope] = []

        async def call_operation() -> None:
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                _ = await executor.execute(
                    "cancelled mutation",
                    HwpOperateInputs(
                        request_id=request_id,
                        operation="table.fill_existing",
                    ),
                    None,
                )

        try:
            async with anyio.create_task_group() as tasks:
                _ = tasks.start_soon(call_operation)
                _ = await dispatcher.watch(mutation_started.wait)
                scopes[0].cancel()
                await anyio.sleep(0)
                release_mutation.set()
        finally:
            release_mutation.set()
            await dispatcher.close(
                lambda: cleanup_states.append(
                    journal.snapshot(
                        document_session_key(document),
                        request_id,
                    ).state
                )
            )

    with (
        patch.object(
            HancomBridge,
            "ensure_connection",
            return_value=connected,
        ),
        patch.object(HancomBridge, "operate", side_effect=mutate),
    ):
        anyio.run(exercise_cancellation)

    snapshot = journal.snapshot(document_session_key(document), request_id)
    assert changes == 1
    assert snapshot.state == "committed"
    assert snapshot.failure_code is None
    assert cleanup_states == ["committed"]

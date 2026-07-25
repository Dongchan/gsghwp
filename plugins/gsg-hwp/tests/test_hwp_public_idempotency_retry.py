from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from threading import get_ident
from unittest.mock import patch

import anyio
import pytest
from pydantic import JsonValue


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_contract import ConnectedDocument, OpenDocument  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_operation_contract import OperationResult  # noqa: E402
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_operation_journal_contract import JournalRecord  # noqa: E402
from hwp_operation_journal_store import OperationJournalStore  # noqa: E402
from hwp_public_contract import PublicActionResult  # noqa: E402
from hwp_public_live_edit_tools import HwpPublicLiveEditTools  # noqa: E402


@dataclass(frozen=True, slots=True)
class _DeleteCall:
    operation_id: str
    page: int


@dataclass(frozen=True, slots=True)
class _Scenario:
    calls: tuple[_DeleteCall, ...]
    drop_first_response: bool = False
    query_after_first: bool = False


@dataclass(frozen=True, slots=True)
class _ScenarioResult:
    calls: tuple[PublicActionResult, ...]
    queried: OperationResult | None
    changes: int


def _document() -> OpenDocument:
    return OpenDocument(
        selector="active-document",
        title="retry.hwp",
        full_name="C:/documents/retry.hwp",
        document_id=47,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=4,
        active=True,
        window_handle=404,
    )


def _changed_result() -> OperationResult:
    return OperationResult(
        status="executed",
        changed=True,
        query="delete page",
        registry_entries=1,
        lookup_microseconds=0,
        message="changed",
        modified=True,
        verification="native_snapshot_before_after",
        verified=True,
    )


async def _exercise(path: Path, scenario: _Scenario) -> _ScenarioResult:
    document = _document()
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, OperationJournal(path))
    connected = ConnectedDocument(
        session_id="retry-session",
        document=document,
    )
    tools = HwpPublicLiveEditTools(executor)
    changes = 0
    results: list[PublicActionResult] = []
    queried: OperationResult | None = None

    def mutate(*_args: JsonValue, **_kwargs: JsonValue) -> OperationResult:
        nonlocal changes
        changes += 1
        return _changed_result()

    with (
        patch.object(
            HancomBridge,
            "ensure_connection",
            return_value=connected,
        ),
        patch.object(HancomBridge, "operate", side_effect=mutate),
    ):
        try:
            for index, call in enumerate(scenario.calls):
                result = await tools.hwp_delete_page(
                    operation_id=call.operation_id,
                    page=call.page,
                )
                if index == 0 and scenario.drop_first_response:
                    send, receive = anyio.create_memory_object_stream[
                        PublicActionResult
                    ]()
                    receive.close()
                    with pytest.raises(
                        (anyio.BrokenResourceError, anyio.ClosedResourceError)
                    ):
                        await send.send(result)
                    send.close()
                else:
                    results.append(result)
                if index == 0 and scenario.query_after_first:
                    queried = await executor.get_operation_status(
                        call.operation_id,
                        None,
                    )
        finally:
            await dispatcher.close(lambda: None)
    return _ScenarioResult(tuple(results), queried, changes)


def test_same_operation_id_replays_result_and_changes_once(tmp_path: Path) -> None:
    operation_id = "same-operation"
    result = anyio.run(
        _exercise,
        tmp_path / "journal",
        _Scenario(
            calls=(
                _DeleteCall(operation_id, 2),
                _DeleteCall(operation_id, 2),
            )
        ),
    )

    assert result.changes == 1
    assert tuple(call.request_id for call in result.calls) == (
        operation_id,
        operation_id,
    )
    assert tuple(call.idempotency_status for call in result.calls) == (
        "committed",
        "replayed",
    )


def test_same_operation_id_with_different_payload_conflicts(tmp_path: Path) -> None:
    operation_id = "conflicting-operation"
    result = anyio.run(
        _exercise,
        tmp_path / "journal",
        _Scenario(
            calls=(
                _DeleteCall(operation_id, 2),
                _DeleteCall(operation_id, 3),
            )
        ),
    )

    assert result.changes == 1
    assert result.calls[1].request_id == operation_id
    assert result.calls[1].idempotency_status == "conflict"


def test_retry_after_completed_operation_loses_response_changes_once(
    tmp_path: Path,
) -> None:
    operation_id = "lost-response-operation"
    result = anyio.run(
        _exercise,
        tmp_path / "journal",
        _Scenario(
            calls=(
                _DeleteCall(operation_id, 2),
                _DeleteCall(operation_id, 2),
            ),
            drop_first_response=True,
            query_after_first=True,
        ),
    )

    assert result.changes == 1
    assert result.queried is not None
    assert result.queried.request_id == operation_id
    assert result.queried.idempotency_status == "replayed"
    assert result.calls[0].idempotency_status == "replayed"


def test_journal_reads_and_fsync_writes_do_not_run_on_event_loop(
    tmp_path: Path,
) -> None:
    operation_id = "off-event-loop-journal"
    event_loop_thread = get_ident()
    io_thread_ids: list[int] = []
    original_create = OperationJournalStore.create
    original_replace = OperationJournalStore.replace
    original_read = OperationJournalStore.read

    def traced_create(path: Path, record: JournalRecord) -> None:
        io_thread_ids.append(get_ident())
        original_create(path, record)

    def traced_replace(path: Path, record: JournalRecord) -> None:
        io_thread_ids.append(get_ident())
        original_replace(path, record)

    def traced_read(path: Path) -> JournalRecord:
        io_thread_ids.append(get_ident())
        return original_read(path)

    with (
        patch.object(OperationJournalStore, "create", side_effect=traced_create),
        patch.object(OperationJournalStore, "replace", side_effect=traced_replace),
        patch.object(OperationJournalStore, "read", side_effect=traced_read),
    ):
        result = anyio.run(
            _exercise,
            tmp_path / "journal",
            _Scenario(
                calls=(_DeleteCall(operation_id, 2),),
                query_after_first=True,
            ),
        )

    assert result.changes == 1
    assert io_thread_ids
    assert all(thread_id != event_loop_thread for thread_id in io_thread_ids)

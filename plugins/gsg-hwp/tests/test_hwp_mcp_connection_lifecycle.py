from __future__ import annotations

import sys
from threading import Lock
from time import sleep
from pathlib import Path
from unittest.mock import patch

import anyio


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_contract import (  # noqa: E402
    ConnectedDocument,
    OpenDocument,
    OpenDocumentList,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation import McpOperationHandler  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_mcp_result_envelope import normalize_production_result  # noqa: E402
from hwp_operation_contract import HwpOperateInputs, OperationResult  # noqa: E402
from hwp_public_contract import to_public_action_result  # noqa: E402


def _document() -> OpenDocument:
    return OpenDocument(
        selector="first-active-document",
        title="first.hwp",
        full_name="C:/documents/first.hwp",
        document_id=17,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=3,
        active=True,
        window_handle=101,
    )


def test_executor_reconnects_when_the_active_document_changes() -> None:
    first_document = _document()
    second_document = first_document.model_copy(
        update={
            "selector": "second-active-document",
            "title": "second.hwp",
            "full_name": "C:/documents/second.hwp",
            "document_id": 18,
            "window_handle": 102,
        }
    )
    first_connection = ConnectedDocument(
        session_id="first-session",
        document=first_document,
    )
    second_connection = ConnectedDocument(
        session_id="second-session",
        document=second_document,
    )
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, None)
    handler = McpOperationHandler(bridge, dispatcher, executor)

    async def connect_after_switch() -> tuple[ConnectedDocument, ConnectedDocument]:
        try:
            first = await handler.hwp_connect()
            second = await handler.hwp_connect()
            return first, second
        finally:
            await dispatcher.close(bridge.close)

    with (
        patch.object(
            HancomBridge,
            "list_open_documents",
            side_effect=(
                OpenDocumentList(documents=(first_document,)),
                OpenDocumentList(documents=(second_document,)),
            ),
        ),
        patch.object(
            HancomBridge,
            "connect",
            side_effect=(first_connection, second_connection),
        ) as native_connect,
        patch.object(
            HancomBridge,
            "disconnect",
            side_effect=HwpLiveError("닫힌 문서의 이전 세션입니다"),
        ) as native_disconnect,
    ):
        first, second = anyio.run(connect_after_switch)

    assert first == first_connection
    assert second == second_connection
    assert native_connect.call_count == 2
    native_disconnect.assert_called_once_with(first_connection.session_id)


def test_connection_transport_error_is_unchanged_and_retry_safe() -> None:
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, None)
    inputs = HwpOperateInputs(operation="table.fill_existing")

    async def execute_without_connection() -> OperationResult:
        try:
            return await executor.execute("delete page", inputs, None)
        finally:
            await dispatcher.close(bridge.close)

    with patch.object(
        HancomBridge,
        "list_open_documents",
        side_effect=HwpLiveError("열린 문서를 확인할 수 없습니다"),
    ):
        result = anyio.run(execute_without_connection)

    assert result.status == "transport_error"
    assert result.failure_stage == "connection"
    assert result.changed is False
    assert result.partial_change is False
    assert result.retry_safe is True

    normalized_result = normalize_production_result(result, inputs)
    assert normalized_result.failure_stage == "connection"
    assert normalized_result.changed is False
    assert normalized_result.partial_change is False
    assert normalized_result.retry_safe is True

    public_result = to_public_action_result(normalized_result, ())
    assert public_result.status == "failed"
    assert public_result.modified is False
    assert public_result.retry_safe is True


def test_dispatcher_serializes_operations_before_the_sta_bridge() -> None:
    dispatcher = McpThreadDispatcher(watch_workers=1)
    state_lock = Lock()
    active = 0
    maximum_active = 0

    def operation() -> None:
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        sleep(0.02)
        with state_lock:
            active -= 1

    async def run_operations() -> None:
        try:
            async with anyio.create_task_group() as tasks:
                for _ in range(6):
                    tasks.start_soon(dispatcher.run, operation)
        finally:
            await dispatcher.close(lambda: None)

    anyio.run(run_operations)

    assert maximum_active == 1

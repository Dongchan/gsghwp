from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import final
from unittest.mock import patch


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_bridge_contract import (  # noqa: E402
    HancomDialogDismissResult,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_contract import (  # noqa: E402
    ConnectedDocument,
    MutationResult,
    OpenDocument,
    OpenDocumentList,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_session_types import LiveSessionReference  # noqa: E402
from hwp_live_structure_contract import FastPageInspection  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation import McpOperationHandler  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402


def _document() -> OpenDocument:
    return OpenDocument(
        selector="session-owner-document",
        title="session-owner.hwp",
        full_name="C:/documents/session-owner.hwp",
        document_id=91,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=3,
        active=True,
        window_handle=909,
    )


@final
class SessionState:
    def __init__(self, document: OpenDocument) -> None:
        self.connect_calls = 0
        self.connection: ConnectedDocument | None = None
        self.document = document
        self.fail_disconnect_after_clear = False

    def list_open_documents(self) -> OpenDocumentList:
        return OpenDocumentList(documents=(self.document,))

    def connect(self, selector: str) -> ConnectedDocument:
        if self.connection is not None:
            raise HwpLiveError("이미 한컴 라이브 문서에 연결되어 있습니다")
        if selector != self.document.selector:
            raise HwpLiveError("선택한 문서를 찾을 수 없습니다")
        self.connect_calls += 1
        self.connection = ConnectedDocument(
            session_id=f"session-{self.connect_calls}",
            document=self.document,
        )
        return self.connection

    def connect_deferred(self, selector: str) -> ConnectedDocument:
        return self.connect(selector)

    def current_session(self) -> LiveSessionReference | None:
        connection = self.connection
        if connection is None:
            return None
        return LiveSessionReference(
            connection.session_id,
            connection.document.selector,
        )

    def connection_moniker(self, session_id: str) -> str:
        connection = self.connection
        if connection is None or connection.session_id != session_id:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        return "!HwpObject.91.1"

    def disconnect(self, session_id: str) -> MutationResult:
        connection = self.connection
        if connection is None or connection.session_id != session_id:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        self.connection = None
        if self.fail_disconnect_after_clear:
            raise HwpLiveError("해제 결과 COM 속성을 읽을 수 없습니다")
        return MutationResult(
            action="disconnect",
            current_page=2,
            modified=False,
        )

    def inspect_page_fast(
        self,
        session_id: str,
        page: int,
        *,
        include_cells: bool,
    ) -> FastPageInspection:
        _ = include_cells
        connection = self.connection
        if connection is None or connection.session_id != session_id:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        return FastPageInspection(
            document_id=self.document.document_id,
            full_name=self.document.full_name,
            page=page,
            page_count=self.document.page_count,
            text="",
            controls=(),
        )

    def close(self) -> None:
        self.connection = None


@final
class ChangeSignal:
    def __init__(self) -> None:
        self.active = False
        self.block_next_start = False
        self.release_start = Event()
        self.start_calls = 0
        self.start_entered = Event()
        self.stop_calls = 0

    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        _ = (process_id, moniker_name)
        self.start_calls += 1
        self.active = True
        if self.block_next_start:
            self.block_next_start = False
            self.start_entered.set()
            _ = self.release_start.wait()

    def sequence(self) -> int:
        return 0

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        _ = (after_sequence, timeout_seconds)
        return 0

    def stop(self) -> None:
        self.stop_calls += 1
        self.active = False


@final
class WindowReader:
    def __init__(self, window_handle: int) -> None:
        self.state = HancomWindowState(
            window_handle=window_handle,
            process_id=919,
            exists=True,
            visible=True,
            enabled=True,
            foreground=True,
            title="Hwp",
            class_name="HwpFrame",
            dialogs=(),
        )

    def read(self, window_handle: int) -> HancomWindowState:
        assert window_handle == self.state.window_handle
        return self.state

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        return HancomWindowStateList(windows=(self.state,))

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        assert window_handle == self.state.window_handle
        return HancomDialogDismissResult(
            before=self.state,
            after=self.state,
            dismissed_handles=(),
        )


@contextmanager
def patched_controller(state: SessionState) -> Generator[None]:
    with (
        patch.object(
            LiveHwpController,
            "list_open_documents",
            side_effect=state.list_open_documents,
        ),
        patch.object(LiveHwpController, "connect", side_effect=state.connect),
        patch.object(
            LiveHwpController,
            "connect_deferred",
            side_effect=state.connect_deferred,
        ),
        patch.object(
            LiveHwpController,
            "current_session",
            side_effect=state.current_session,
        ),
        patch.object(
            LiveHwpController,
            "connection_moniker",
            side_effect=state.connection_moniker,
        ),
        patch.object(LiveHwpController, "disconnect", side_effect=state.disconnect),
        patch.object(
            LiveHwpController,
            "inspect_page_fast",
            side_effect=state.inspect_page_fast,
        ),
        patch.object(LiveHwpController, "close", side_effect=state.close),
    ):
        yield


def session_system() -> tuple[
    SessionState,
    ChangeSignal,
    HancomBridge,
    McpThreadDispatcher,
    HwpOperationExecutor,
    McpOperationHandler,
]:
    document = _document()
    state = SessionState(document)
    signal = ChangeSignal()
    bridge = HancomBridge(
        LiveHwpController(),
        window_reader=WindowReader(document.window_handle),
        change_signal=signal,
    )
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, None)
    handler = McpOperationHandler(bridge, dispatcher, executor)
    return state, signal, bridge, dispatcher, executor, handler

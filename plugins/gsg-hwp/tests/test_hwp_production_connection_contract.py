from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar, cast, final
from unittest.mock import patch

import anyio
import pytest
from pydantic import BaseModel, ConfigDict


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
    HancomWindowChildState,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_contract import ConnectedDocument  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_windows import WindowStateReader  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_session_state_support import patched_controller, session_system  # noqa: E402


class _InputSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    required: tuple[str, ...] = ()


@final
class _PopupWindowReader:
    _state: HancomWindowState

    def __init__(self, state: HancomWindowState) -> None:
        self._state = state

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        return HancomWindowStateList(windows=(self._state,))

    def read(self, window_handle: int) -> HancomWindowState:
        _ = window_handle
        return self._state

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        _ = window_handle
        raise AssertionError("팝업 자동 진단은 창을 닫지 않아야 합니다")


def test_bridge_error_includes_visible_hwp_popup_text() -> None:
    popup = HancomWindowState(
        window_handle=21502142,
        process_id=12456,
        exists=True,
        visible=True,
        enabled=True,
        foreground=True,
        title="Hwp",
        class_name="#32770",
        dialogs=(),
        children=(
            HancomWindowChildState(
                window_handle=21502143,
                title="TypeInitializationException: PopupBorderImpl",
                class_name="Static",
                visible=True,
                enabled=True,
            ),
        ),
    )
    bridge = HancomBridge(
        LiveHwpController(),
        window_reader=cast(WindowStateReader, _PopupWindowReader(popup)),
    )
    try:
        with patch.object(
            LiveHwpController,
            "list_open_documents",
            side_effect=HwpLiveError("RPC 서버를 사용할 수 없습니다(-2147023174)"),
        ):
            with pytest.raises(HwpLiveError) as captured:
                _ = bridge.list_open_documents()
    finally:
        bridge.close()

    assert "RPC 서버를 사용할 수 없습니다(-2147023174)" in captured.value.reason
    assert "PopupBorderImpl" in captured.value.reason


def test_production_read_tools_need_no_session_preflight() -> None:
    server = build_server(LiveHwpController(), profile="production")
    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _InputSchema.model_validate(tool.inputSchema) for tool in tools
    }

    for name in (
        "hwp_inspect",
        "hwp_inspect_page_fast",
        "hwp_inspect_structure",
        "hwp_list_styles",
    ):
        assert "session_id" not in schemas[name].required


def test_connect_reuses_the_controller_owned_session_for_the_same_document() -> None:
    state, _, bridge, dispatcher, _, handler = session_system()

    async def connect_twice() -> tuple[ConnectedDocument, ConnectedDocument]:
        try:
            first = await handler.hwp_connect(state.document.selector)
            second = await handler.hwp_connect(state.document.selector)
            return first, second
        finally:
            await dispatcher.close(bridge.close)

    with patched_controller(state):
        first, second = anyio.run(connect_twice)

    assert first == second
    assert first.session_id == "session-1"
    assert state.connect_calls == 1

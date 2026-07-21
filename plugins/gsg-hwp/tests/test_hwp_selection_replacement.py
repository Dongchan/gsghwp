from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import anyio
from pydantic import BaseModel, ConfigDict, Field, JsonValue


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_contract import (  # noqa: E402
    ActiveHwpTarget,
    CharacterStyle,
    ConnectedDocument,
    CursorPosition,
    LiveContext,
    MutationResult,
    OpenDocument,
    OpenDocumentList,
    PageSetup,
    ParagraphStyle,
    SelectionPosition,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402


class _InputSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    required: tuple[str, ...] = ()
    properties: dict[str, JsonValue] = Field(default_factory=dict)


def _document() -> OpenDocument:
    return OpenDocument(
        selector="active-document",
        title="sample.hwp",
        full_name="C:/documents/sample.hwp",
        document_id=17,
        format="HWP",
        edit_mode=1,
        modified=True,
        page_count=3,
        active=True,
        window_handle=101,
    )


def _selected_context(document: OpenDocument) -> LiveContext:
    return LiveContext(
        document=document,
        current_page=2,
        cursor=CursorPosition(list_id=0, paragraph=7, character=3),
        selection=SelectionPosition(
            selected=True,
            start_list=0,
            start_paragraph=7,
            start_character=3,
            end_list=0,
            end_paragraph=8,
            end_character=5,
        ),
        active_target=ActiveHwpTarget(
            kind="selected_text",
            selection_mode_raw=1,
            selection_mode="text",
            strict_selection=False,
            multiple_cells=False,
        ),
        selected_text="기존 문장\r\n둘째 문장",
        page_text="기존 문장\r\n둘째 문장",
        character_style=CharacterStyle(
            face_name="맑은 고딕",
            height_hwpunit=1_000,
            bold=False,
            text_color=0,
        ),
        paragraph_style=ParagraphStyle(
            align_type=0,
            line_spacing=160,
            left_margin_hwpunit=0,
            right_margin_hwpunit=0,
            indentation_hwpunit=0,
            previous_spacing_hwpunit=0,
            next_spacing_hwpunit=0,
        ),
        page_setup=PageSetup(
            paper_width_mm=210,
            paper_height_mm=297,
            landscape=0,
            top_margin_mm=15,
            bottom_margin_mm=15,
            left_margin_mm=20,
            right_margin_mm=20,
        ),
    )


def test_production_replacement_tool_accepts_only_current_selection_and_text() -> None:
    server = build_server(LiveHwpController(), profile="production")
    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _InputSchema.model_validate(tool.inputSchema) for tool in tools
    }

    schema = schemas["hwp_replace_selected_text"]

    assert schema.required == ("replacement",)
    assert frozenset(schema.properties) == frozenset(("replacement", "document_path"))


def test_executor_replaces_the_exact_selection_returned_by_inspection() -> None:
    document = _document()
    connected = ConnectedDocument(session_id="selection-session", document=document)
    context = _selected_context(document)
    mutation = MutationResult(action="replace_selection", current_page=2, modified=True)
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, None)

    async def replace() -> MutationResult:
        try:
            return await executor.replace_selected_text(
                document.selector,
                "바뀐 문장",
            )
        finally:
            await dispatcher.close(bridge.close)

    with (
        patch.object(
            HancomBridge,
            "list_open_documents",
            return_value=OpenDocumentList(documents=(document,)),
        ),
        patch.object(HancomBridge, "connect", return_value=connected),
        patch.object(HancomBridge, "context", return_value=context),
        patch.object(
            HancomBridge,
            "replace_selection",
            return_value=mutation,
        ) as native_replace,
    ):
        result = anyio.run(replace)

    assert result == mutation
    native_replace.assert_called_once_with(
        connected.session_id,
        context.selection,
        context.selected_text,
        "바뀐 문장",
    )

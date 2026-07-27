from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, patch

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
    OpenDocument,
    PageSetup,
    ParagraphStyle,
    SelectionPosition,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_live_text_patch_contract import TextPatchRequest, TextPatchTarget  # noqa: E402
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateInputs,
    HwpOperateTarget,
    OperationResult,
)


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


def test_production_replacement_tool_accepts_explicit_text_target() -> None:
    server = build_server(LiveHwpController(), profile="production")
    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _InputSchema.model_validate(tool.inputSchema) for tool in tools
    }

    schema = schemas["hwp_replace_selected_text"]

    assert schema.required == ("operation_id", "replacement")
    assert frozenset(schema.properties) == frozenset(
        (
            "operation_id",
            "replacement",
            "target",
            "expected_text",
            "document_path",
            "document_selector",
        )
    )


def test_executor_replaces_the_exact_selection_returned_by_inspection(
    tmp_path: Path,
) -> None:
    document = _document()
    connected = ConnectedDocument(session_id="selection-session", document=document)
    context = _selected_context(document)
    atomic_result = OperationResult(
        request_id="selection-replacement",
        status="executed",
        changed=True,
        query="선택 텍스트 교체",
        registry_entries=1,
        lookup_microseconds=0,
        message="선택 본문을 교체하고 다시 읽어 검증했습니다",
        verification="native_operation_specific_readback",
        verified=True,
    )
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(
        bridge,
        dispatcher,
        OperationJournal(tmp_path / "journal"),
    )

    async def replace() -> OperationResult:
        try:
            return await executor.replace_selected_text(
                "선택 텍스트 교체",
                HwpOperateInputs(
                    request_id="selection-replacement",
                    document=document.selector,
                    operation="document.replace_selection",
                    target=HwpOperateTarget(
                        kind="selection",
                        binding="selection",
                    ),
                    parameters={"replacement": "바뀐 문장"},
                ),
                "바뀐 문장",
            )
        finally:
            await dispatcher.close(bridge.close)

    with (
        patch.object(
            HancomBridge,
            "ensure_connection",
            return_value=connected,
        ),
        patch.object(HancomBridge, "context", return_value=context),
        patch.object(
            HwpOperationExecutor,
            "patch_text",
            new=AsyncMock(return_value=atomic_result),
        ) as atomic_patch,
    ):
        result = anyio.run(replace)

    assert result.status == "executed"
    assert result.request_id == "selection-replacement"
    assert result.changed is True
    atomic_patch.assert_awaited_once()
    await_args = atomic_patch.await_args
    assert await_args is not None
    assert await_args.args[2] == TextPatchRequest(
        target=TextPatchTarget(kind="current"),
        expected_text=context.selected_text,
        replacement="바뀐 문장",
    )

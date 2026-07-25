from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from sys import argv, stderr

from mcp.server.fastmcp import FastMCP

from hwp_live_bridge import HancomBridge
from hwp_live_preview_maintenance import maintain_live_previews
from hwp_live_session import LiveHwpController
from hwp_codex_skill_install import ensure_codex_skill_registered
from hwp_mcp_dispatch import McpThreadDispatcher
from hwp_mcp_document_wrappers import McpDocumentRecipeWrappers
from hwp_mcp_forward import ForwardingFastMCP
from hwp_mcp_metadata import MCP_INSTRUCTIONS
from hwp_mcp_operation import McpOperationHandler
from hwp_mcp_operation_executor import HwpOperationExecutor
from hwp_mcp_qa_handlers import McpQaHandlers
from hwp_mcp_registration import McpToolBindings, register_mcp_tools
from hwp_mcp_resources import register_guidance_resources
from hwp_mcp_registry import McpProfile, configured_mcp_profile
from hwp_native_install import ensure_native_bridge_registered
from hwp_operation_journal import OperationJournal
from hwp_operation_journal_maintenance import maintain_operation_journal
from hwp_public_document_tools import HwpPublicDocumentTools
from hwp_public_inspection_tools import HwpPublicInspectionTools
from hwp_public_live_edit_tools import HwpPublicLiveEditTools
from hwp_public_action_contract import PublicObjectTargetStore
from hwp_public_object_tools import HwpPublicObjectTools
from hwp_public_selection_tools import HwpPublicSelectionTools
from hwp_public_table_edit_tools import HwpPublicTableEditTools
from hwp_public_table_target import PublicTableTargetStore
from hwp_public_table_tools import HwpPublicTableTools
from hwp_public_tools import HwpPublicTools
from hwp_public_visibility_tools import HwpPublicVisibilityTools
from hwp_reference_image_tools import HwpReferenceImageTools
from hwp_runtime_identity import startup_runtime_record


def build_server(
    controller: LiveHwpController,
    *,
    profile: McpProfile = "production",
    operation_journal: OperationJournal | None = None,
) -> ForwardingFastMCP:
    bridge = HancomBridge(controller)
    dispatcher = McpThreadDispatcher()
    journal = OperationJournal() if operation_journal is None else operation_journal
    operation_executor = HwpOperationExecutor(bridge, dispatcher, journal)
    operation = McpOperationHandler(bridge, dispatcher, operation_executor)
    public_targets = PublicTableTargetStore()
    object_targets = PublicObjectTargetStore()
    public_tools = HwpPublicTools(operation_executor, public_targets)
    public_table_tools = HwpPublicTableTools(operation_executor, public_targets)
    public_visibility_tools = HwpPublicVisibilityTools(operation_executor)
    public_table_edit_tools = HwpPublicTableEditTools(
        operation_executor, public_targets
    )
    public_object_tools = HwpPublicObjectTools(operation_executor, object_targets)
    public_inspection_tools = HwpPublicInspectionTools(
        operation_executor,
        object_targets,
        public_targets,
    )
    public_selection_tools = HwpPublicSelectionTools(operation_executor)
    public_document_tools = HwpPublicDocumentTools(operation_executor)
    public_live_edit_tools = HwpPublicLiveEditTools(operation_executor)
    reference_image_tools = HwpReferenceImageTools()
    document_wrappers = McpDocumentRecipeWrappers(operation)
    qa = McpQaHandlers(bridge, dispatcher)

    @asynccontextmanager
    async def lifespan(_: FastMCP[None]) -> AsyncGenerator[None]:
        async with maintain_operation_journal(journal):
            async with maintain_live_previews(controller.preview_store):
                try:
                    yield
                finally:
                    await dispatcher.close(bridge.close)

    server = ForwardingFastMCP(
        "hancom-hwp-live" if profile == "production" else "hancom-hwp-qa",
        instructions=MCP_INSTRUCTIONS,
        lifespan=lifespan,
    )
    register_mcp_tools(
        server,
        McpToolBindings(
            operation=operation,
            document_wrappers=document_wrappers,
            qa=qa,
            public_tools=public_tools,
            public_table_tools=public_table_tools,
            public_visibility_tools=public_visibility_tools,
            public_table_edit_tools=public_table_edit_tools,
            public_object_tools=public_object_tools,
            public_inspection_tools=public_inspection_tools,
            public_selection_tools=public_selection_tools,
            public_document_tools=public_document_tools,
            public_live_edit_tools=public_live_edit_tools,
            reference_image_tools=reference_image_tools,
        ),
        profile,
    )
    register_guidance_resources(server)
    return server


def main() -> None:
    _ = ensure_codex_skill_registered()
    _ = stderr.write(f"{startup_runtime_record()}\n")
    _ = stderr.flush()
    _ = ensure_native_bridge_registered()
    build_server(
        LiveHwpController(),
        profile=configured_mcp_profile(argv[1:]),
    ).run(transport="stdio")


if __name__ == "__main__":
    main()

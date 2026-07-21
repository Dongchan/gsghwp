from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from hwp_mcp_catalog import register_catalog_tools
from hwp_mcp_document_wrappers import McpDocumentRecipeWrappers
from hwp_mcp_forward import ForwardingFastMCP, HwpExecuteArguments
from hwp_mcp_metadata import register_public_tools
from hwp_mcp_operation import McpOperationHandler
from hwp_mcp_qa_handlers import McpQaHandlers
from hwp_mcp_registry import McpProfile, tool_names, tool_spec
from hwp_public_document_tools import HwpPublicDocumentTools
from hwp_public_inspection_tools import HwpPublicInspectionTools
from hwp_public_live_edit_tools import HwpPublicLiveEditTools
from hwp_public_object_tools import HwpPublicObjectTools
from hwp_public_selection_tools import HwpPublicSelectionTools
from hwp_public_table_edit_tools import HwpPublicTableEditTools
from hwp_public_table_tools import HwpPublicTableTools
from hwp_public_tools import HwpPublicTools
from hwp_public_visibility_tools import HwpPublicVisibilityTools


@dataclass(frozen=True, slots=True)
class McpToolBindings:
    operation: McpOperationHandler
    document_wrappers: McpDocumentRecipeWrappers
    qa: McpQaHandlers
    public_tools: HwpPublicTools
    public_table_tools: HwpPublicTableTools
    public_visibility_tools: HwpPublicVisibilityTools
    public_table_edit_tools: HwpPublicTableEditTools
    public_object_tools: HwpPublicObjectTools
    public_inspection_tools: HwpPublicInspectionTools
    public_selection_tools: HwpPublicSelectionTools
    public_document_tools: HwpPublicDocumentTools
    public_live_edit_tools: HwpPublicLiveEditTools


def register_mcp_tools(
    server: ForwardingFastMCP,
    bindings: McpToolBindings,
    profile: McpProfile,
) -> None:
    forwardable_tools = tool_names(profile) - {"hwp_execute"}

    async def hwp_execute(
        tool_name: str,
        arguments: HwpExecuteArguments,
    ) -> JsonValue:
        if tool_name not in forwardable_tools:
            available_tools: list[JsonValue] = [
                name for name in sorted(forwardable_tools)
            ]
            failure: dict[str, JsonValue] = {
                "status": "unsupported",
                "requested_tool": tool_name,
                "available_tools": available_tools,
                "message": "설치된 현재 프로필에 등록된 HWP 도구가 아닙니다.",
            }
            return failure
        return await server.call_unconverted_tool(tool_name, arguments)

    qa_operate_tool = {
        "production": None,
        "qa": bindings.operation.hwp_operate,
    }[profile]
    if qa_operate_tool is not None:
        server.add_tool(
            qa_operate_tool,
            name="hwp_operate",
            description=tool_spec("hwp_operate").description,
        )

    public_action_tools = (
        (bindings.public_tools.hwp_fill_table, "hwp_fill_table"),
        (bindings.public_table_tools.hwp_expand_and_fill_table, "hwp_expand_and_fill_table"),
        (bindings.public_table_tools.hwp_repeat_table_template, "hwp_repeat_table_template"),
        (bindings.public_table_tools.hwp_build_table_series, "hwp_build_table_series"),
        (bindings.public_visibility_tools.hwp_sync_visibility_analysis_tables, "hwp_sync_visibility_analysis_tables"),
        (bindings.public_table_tools.hwp_fill_table_images, "hwp_fill_table_images"),
        (bindings.public_table_edit_tools.hwp_format_table, "hwp_format_table"),
        (bindings.public_table_edit_tools.hwp_merge_table_cells, "hwp_merge_table_cells"),
        (bindings.public_table_edit_tools.hwp_split_table_cell, "hwp_split_table_cell"),
        (bindings.public_object_tools.hwp_insert_image, "hwp_insert_image"),
        (bindings.public_object_tools.hwp_replace_image, "hwp_replace_image"),
        (bindings.public_object_tools.hwp_add_caption, "hwp_add_caption"),
        (bindings.public_selection_tools.hwp_apply_style, "hwp_apply_style"),
        (bindings.public_selection_tools.hwp_format_text, "hwp_format_text"),
        (
            bindings.public_selection_tools.hwp_replace_selected_text,
            "hwp_replace_selected_text",
        ),
        (bindings.public_live_edit_tools.hwp_delete_page, "hwp_delete_page"),
        (bindings.public_live_edit_tools.hwp_delete_control, "hwp_delete_control"),
        (bindings.public_live_edit_tools.hwp_undo, "hwp_undo"),
        (bindings.public_live_edit_tools.hwp_redo, "hwp_redo"),
        (bindings.public_document_tools.hwp_insert_layout, "hwp_insert_layout"),
        (bindings.public_document_tools.hwp_append_layout, "hwp_append_layout"),
        (bindings.public_document_tools.hwp_append_report, "hwp_append_report"),
        (bindings.public_document_tools.hwp_append_excel_table, "hwp_append_excel_table"),
        (bindings.public_document_tools.hwp_save_reopen_verify, "hwp_save_reopen_verify"),
    )
    for public_tool, name in public_action_tools:
        server.add_tool(public_tool, name=name, description=tool_spec(name).description)

    register_public_tools(
        server,
        (
            bindings.document_wrappers.hwp_copy_style,
            bindings.qa.hwp_list_open_documents,
            bindings.operation.hwp_connect,
            bindings.public_inspection_tools.hwp_inspect,
            bindings.public_inspection_tools.hwp_inspect_page_fast,
            bindings.public_inspection_tools.hwp_inspect_structure,
            bindings.public_inspection_tools.hwp_render_page,
            bindings.public_inspection_tools.hwp_list_styles,
            bindings.qa.hwp_watch_state,
            bindings.qa.hwp_list_window_states,
            bindings.qa.hwp_inspect_window_state,
            bindings.qa.hwp_dismiss_dialogs,
            bindings.qa.hwp_run_official_api_batch,
            bindings.qa.hwp_probe_official_api_batch,
            bindings.qa.hwp_probe_official_api_payload,
            bindings.qa.hwp_replace_selection,
            bindings.qa.hwp_apply_layout,
            bindings.qa.hwp_update_table_cells,
            bindings.qa.hwp_insert_table_images,
            bindings.qa.hwp_import_office_table,
            bindings.qa.hwp_propagate_table_cells,
            bindings.qa.hwp_insert_folder_images,
            bindings.qa.hwp_rebuild_document,
            bindings.operation.hwp_disconnect,
        ),
        profile,
    )
    register_catalog_tools(server, profile)
    server.add_tool(
        server.hwp_runtime_info,
        name="hwp_runtime_info",
        description=tool_spec("hwp_runtime_info").description,
    )
    server.add_tool(
        hwp_execute,
        name="hwp_execute",
        description=tool_spec("hwp_execute").description,
    )

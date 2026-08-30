from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from difflib import get_close_matches
from functools import wraps
from types import MappingProxyType
from typing import cast

from mcp.types import AnyFunction
from pydantic import BaseModel, JsonValue

from hwp_custom_action_tools import HwpCustomActionTools
from hwp_live_contract import ResolvedOpenDocument
from hwp_mcp_catalog import (
    call_production_catalog_gateway_tool,
    catalog_tool_handlers,
    production_catalog_gateway_tool_names,
)
from hwp_mcp_document_wrappers import McpDocumentRecipeWrappers
from hwp_mcp_forward import ForwardingFastMCP, HwpExecuteArguments
from hwp_mcp_operation import McpOperationHandler
from hwp_mcp_operation_executor import HwpOperationExecutor
from hwp_mcp_qa_handlers import McpQaHandlers
from hwp_mcp_pageplan_tools import HwpPagePlanTools
from hwp_mcp_registry import (
    MCP_TOOL_SPECS,
    McpProfile,
    McpToolSpec,
    ToolHandlerSource,
    tool_names,
    tool_specs,
)
from hwp_public_document_tools import HwpPublicDocumentTools
from hwp_public_graph_tools import HwpPublicGraphTools, graph_call_tool_result
from hwp_public_inspection_tools import HwpPublicInspectionTools
from hwp_public_live_edit_tools import HwpPublicLiveEditTools
from hwp_public_object_tools import HwpPublicObjectTools
from hwp_public_restructure_tools import HwpPublicRestructureTools
from hwp_public_selection_tools import HwpPublicSelectionTools
from hwp_public_xlsx_tools import HwpPublicXlsxTools
from hwp_public_table_edit_tools import HwpPublicTableEditTools
from hwp_public_table_tools import HwpPublicTableTools
from hwp_public_tools import HwpPublicTools
from hwp_public_visibility_tools import HwpPublicVisibilityTools
from hwp_reference_image_tools import HwpReferenceImageTools


@dataclass(frozen=True, slots=True)
class McpToolBindings:
    operation_executor: HwpOperationExecutor
    operation: McpOperationHandler
    document_wrappers: McpDocumentRecipeWrappers
    qa: McpQaHandlers
    public_tools: HwpPublicTools
    public_table_tools: HwpPublicTableTools
    public_visibility_tools: HwpPublicVisibilityTools
    public_table_edit_tools: HwpPublicTableEditTools
    public_object_tools: HwpPublicObjectTools
    public_restructure_tools: HwpPublicRestructureTools
    public_inspection_tools: HwpPublicInspectionTools
    public_graph_tools: HwpPublicGraphTools
    public_selection_tools: HwpPublicSelectionTools
    public_xlsx_tools: HwpPublicXlsxTools
    public_document_tools: HwpPublicDocumentTools
    public_live_edit_tools: HwpPublicLiveEditTools
    reference_image_tools: HwpReferenceImageTools
    pageplan_tools: HwpPagePlanTools
    custom_action_tools: HwpCustomActionTools


class McpToolBindingError(RuntimeError):
    tool_name: str
    handler_source: ToolHandlerSource
    match_count: int

    def __init__(
        self,
        tool_name: str,
        handler_source: ToolHandlerSource,
        match_count: int,
    ) -> None:
        super().__init__(
            f"{tool_name} requires exactly one {handler_source} handler; found {match_count}"
        )
        self.tool_name = tool_name
        self.handler_source = handler_source
        self.match_count = match_count


def _binding_handlers(
    bindings: McpToolBindings,
    specs: tuple[McpToolSpec, ...],
) -> Mapping[str, AnyFunction]:
    handlers: dict[str, AnyFunction] = {}
    binding_owners = tuple(binding_field.name for binding_field in fields(bindings))
    for spec in specs:
        if spec.handler_source != "bindings":
            continue
        matches: list[AnyFunction] = []
        owners = (
            (spec.binding_owner,) if spec.binding_owner is not None else binding_owners
        )
        for owner in owners:
            provider = cast(object, getattr(bindings, owner))
            candidate = cast(object, getattr(provider, spec.name, None))
            if callable(candidate):
                matches.append(cast(AnyFunction, candidate))
        if len(matches) != 1:
            raise McpToolBindingError(spec.name, spec.handler_source, len(matches))
        handlers[spec.name] = matches[0]
    return MappingProxyType(handlers)


def _closest_tool_names(tool_name: str, candidates: frozenset[str]) -> tuple[str, ...]:
    return tuple(get_close_matches(tool_name, sorted(candidates), n=3, cutoff=0.7))


def _unsupported_forward_message(tool_name: str, profile: McpProfile) -> str:
    """Say which name failed and why, not just that something is missing.

    The old sentence ("설치된 현재 프로필에 등록된 HWP 도구가 아닙니다.") never
    named ``tool_name``, so it read as if ``hwp_execute`` itself were missing —
    ``hwp_execute`` is registered in every profile and the name it was asked to
    forward is what does not exist here.
    """
    if tool_name == "hwp_execute":
        return (
            "hwp_execute는 자기 자신을 전달하지 않습니다. "
            "tool_name에는 실제로 실행할 HWP 도구 이름을 넣으세요."
        )
    known = tool_name in {spec.name for spec in MCP_TOOL_SPECS}
    if known:
        return (
            f"{tool_name}은(는) 이 빌드에 있지만 현재 프로필({profile})에는 "
            "등록되지 않은 도구입니다. available_tools 중에서 고르세요."
        )
    suggestions = _closest_tool_names(tool_name, tool_names(profile))
    hint = (
        ""
        if not suggestions
        else " 이름이 비슷한 도구: " + ", ".join(suggestions) + "."
    )
    missing = (
        f"{tool_name}(이)라는 HWP 도구는 없습니다. available_tools의 이름은 모두 "
        "직접 호출할 수 있으므로 hwp_execute를 거치지 않아도 됩니다."
    )
    return missing + hint


def _public_tool_handler(
    executor: HwpOperationExecutor,
    tool_name: str,
    handler: AnyFunction,
) -> AnyFunction:
    if tool_name == "hwp_open_document":

        async def scoped_open_document(
            path: str,
            reference_selector: str | None = None,
            new_tab: bool = True,
            restore_reference: bool = True,
        ) -> ResolvedOpenDocument:
            async with executor.public_tool_session_scope(tool_name):
                result = cast(
                    object,
                    await handler(
                        path=path,
                        reference_selector=reference_selector,
                        new_tab=new_tab,
                        restore_reference=restore_reference,
                    ),
                )
            return ResolvedOpenDocument.model_validate(result)

        return scoped_open_document

    @wraps(handler)
    async def scoped_handler(*args: object, **kwargs: object) -> object:
        async with executor.public_tool_session_scope(tool_name):
            result = cast(object, await handler(*args, **kwargs))
        if tool_name in {
            "hwp_get_graph_manifest",
            "hwp_query_graph",
            "hwp_get_graph_node",
            "hwp_get_graph_property",
            "hwp_get_graph_asset",
            "hwp_fetch_graph_artifact",
            "hwp_validate_graph_patch",
            "hwp_diff_graph_patch",
            "hwp_invert_graph_patch",
            "hwp_graph_patch_history",
            "hwp_apply_graph_patch",
            "hwp_reconcile_graph_patch",
        }:
            return graph_call_tool_result(tool_name, cast(BaseModel, result))
        return result

    return scoped_handler


def register_mcp_tools(
    server: ForwardingFastMCP,
    bindings: McpToolBindings,
    profile: McpProfile,
) -> None:
    specs = tool_specs(profile)
    forwardable_tools = tool_names(profile) - {"hwp_execute"}
    catalog_gateway_tools: frozenset[str] = (
        production_catalog_gateway_tool_names()
        if profile == "production"
        else frozenset()
    )

    async def hwp_execute(
        tool_name: str,
        arguments: HwpExecuteArguments,
    ) -> JsonValue:
        if tool_name in catalog_gateway_tools:
            return await call_production_catalog_gateway_tool(
                tool_name,
                arguments.root,
            )
        if tool_name not in forwardable_tools:
            available_tools: list[JsonValue] = [
                name for name in sorted(forwardable_tools)
            ]
            failure: dict[str, JsonValue] = {
                "status": "unsupported",
                "requested_tool": tool_name,
                "available_tools": available_tools,
                "message": _unsupported_forward_message(tool_name, profile),
            }
            return failure
        return await server.call_unconverted_tool(tool_name, arguments)

    handlers: dict[str, tuple[ToolHandlerSource, AnyFunction]] = {
        name: (
            "bindings",
            _public_tool_handler(bindings.operation_executor, name, handler),
        )
        for name, handler in _binding_handlers(bindings, specs).items()
    }
    expected_catalog = frozenset(
        spec.name for spec in specs if spec.handler_source == "catalog"
    )
    handlers.update(
        {
            name: ("catalog", handler)
            for name, handler in catalog_tool_handlers(profile).items()
            if name in expected_catalog
        }
    )
    handlers["hwp_runtime_info"] = ("server", server.hwp_runtime_info)
    handlers["hwp_execute"] = ("gateway", hwp_execute)

    for spec in specs:
        resolved = handlers.get(spec.name)
        if resolved is None or resolved[0] != spec.handler_source:
            raise McpToolBindingError(
                spec.name,
                spec.handler_source,
                0 if resolved is None else 1,
            )
        server.add_tool(
            resolved[1],
            name=spec.name,
            description=spec.description,
        )

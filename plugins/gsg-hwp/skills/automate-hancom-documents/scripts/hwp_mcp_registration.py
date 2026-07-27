from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from functools import wraps
from types import MappingProxyType
from typing import cast

from mcp.types import AnyFunction
from pydantic import JsonValue

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
from hwp_mcp_registry import (
    McpProfile,
    McpToolSpec,
    ToolHandlerSource,
    tool_names,
    tool_specs,
)
from hwp_public_document_tools import HwpPublicDocumentTools
from hwp_public_inspection_tools import HwpPublicInspectionTools
from hwp_public_live_edit_tools import HwpPublicLiveEditTools
from hwp_public_object_tools import HwpPublicObjectTools
from hwp_public_selection_tools import HwpPublicSelectionTools
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
    public_inspection_tools: HwpPublicInspectionTools
    public_selection_tools: HwpPublicSelectionTools
    public_document_tools: HwpPublicDocumentTools
    public_live_edit_tools: HwpPublicLiveEditTools
    reference_image_tools: HwpReferenceImageTools


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


def _public_tool_handler(
    executor: HwpOperationExecutor,
    tool_name: str,
    handler: AnyFunction,
) -> AnyFunction:
    @wraps(handler)
    async def scoped_handler(*args: object, **kwargs: object) -> object:
        async with executor.public_tool_session_scope(tool_name):
            return cast(object, await handler(*args, **kwargs))

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
                "message": "설치된 현재 프로필에 등록된 HWP 도구가 아닙니다.",
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

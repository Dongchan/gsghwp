from __future__ import annotations

from pydantic import JsonValue

from hwp_mcp_registry import ToolEffect, tool_effect


def worker_tool_effect(
    name: str,
    arguments: dict[str, JsonValue],
) -> ToolEffect:
    target_name = name
    target_arguments = arguments
    if name == "hwp_execute":
        forwarded = arguments.get("tool_name")
        if not isinstance(forwarded, str) or forwarded == "hwp_execute":
            return "document"
        target_name = forwarded
        nested = arguments.get("arguments")
        target_arguments = nested if isinstance(nested, dict) else {}

    if target_name in {"hwp_operate", "hwp_operate_production"}:
        resolve_only = target_arguments.get("resolve_only")
        allow_change = target_arguments.get("allow_document_change")
        if resolve_only is True or allow_change is False:
            return "read"

    try:
        return tool_effect(target_name)
    except KeyError:
        return "document"


def worker_tool_may_mutate(
    name: str,
    arguments: dict[str, JsonValue],
) -> bool:
    return worker_tool_effect(name, arguments) in {"document", "file"}

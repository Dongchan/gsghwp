from __future__ import annotations

from typing import Final

from pydantic import JsonValue


_NON_MUTATING_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "hwp_connect",
        "hwp_disconnect",
        "hwp_get_capabilities",
        "hwp_get_official_api_coverage",
        "hwp_get_operation_status",
        "hwp_inspect",
        "hwp_inspect_page_fast",
        "hwp_inspect_structure",
        "hwp_inspect_window_state",
        "hwp_list_open_documents",
        "hwp_list_styles",
        "hwp_list_window_states",
        "hwp_render_page",
        "hwp_runtime_info",
        "hwp_search_official_api",
        "hwp_search_tools",
        "hwp_watch_state",
    }
)


def worker_tool_may_mutate(
    name: str,
    arguments: dict[str, JsonValue],
) -> bool:
    if name in _NON_MUTATING_TOOLS:
        return False
    if name in {"hwp_operate", "hwp_operate_production"}:
        resolve_only = arguments.get("resolve_only")
        allow_change = arguments.get("allow_document_change")
        if resolve_only is True or allow_change is False:
            return False
    return True

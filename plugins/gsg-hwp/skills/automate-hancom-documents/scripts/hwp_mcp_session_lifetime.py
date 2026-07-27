from __future__ import annotations

from typing import Final, Literal


type PublicSessionLifetime = Literal["stateless", "persistent", "disconnect"]

_PERSISTENT_SESSION_TOOLS: Final = frozenset(
    {
        "hwp_connect",
        "hwp_watch_state",
        "hwp_run_official_api_batch",
        "hwp_replace_selection",
        "hwp_apply_layout",
        "hwp_update_table_cells",
        "hwp_insert_table_images",
        "hwp_import_office_table",
        "hwp_propagate_table_cells",
        "hwp_insert_folder_images",
    }
)


def public_session_lifetime(tool_name: str) -> PublicSessionLifetime:
    if tool_name == "hwp_disconnect":
        return "disconnect"
    if tool_name in _PERSISTENT_SESSION_TOOLS:
        return "persistent"
    return "stateless"


def persistent_session_tool_names() -> frozenset[str]:
    return _PERSISTENT_SESSION_TOOLS

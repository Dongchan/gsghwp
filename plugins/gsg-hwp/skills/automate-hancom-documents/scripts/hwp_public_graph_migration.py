from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class MutationBackend(StrEnum):
    GRAPH_PATCH = "graph_patch"
    PROTOCOL14 = "protocol14"


class MigrationError(RuntimeError):
    code: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


MIGRATED_PUBLIC_TOOLS: tuple[str, ...] = (
    "hwp_inspect",
    "hwp_inspect_structure",
    "hwp_patch_text",
    "hwp_format_text",
    "hwp_fill_table",
    "hwp_format_table",
    "hwp_insert_image",
    "hwp_append_layout",
    "hwp_insert_layout",
    "hwp_undo",
    "hwp_redo",
)


@dataclass(frozen=True, slots=True)
class MigrationRoute:
    tool_name: str
    backend: MutationBackend
    fallback: bool
    public_name: str
    schema_unchanged: Literal[True] = True


def route_public_tool(
    tool_name: str,
    *,
    graph_available: bool,
    force_protocol14: bool = False,
) -> MigrationRoute:
    if tool_name not in MIGRATED_PUBLIC_TOOLS:
        raise MigrationError("UNKNOWN_PUBLIC_TOOL", tool_name)
    if force_protocol14 or not graph_available:
        return MigrationRoute(
            tool_name=tool_name,
            backend=MutationBackend.PROTOCOL14,
            fallback=True,
            public_name=tool_name,
        )
    return MigrationRoute(
        tool_name=tool_name,
        backend=MutationBackend.GRAPH_PATCH,
        fallback=False,
        public_name=tool_name,
    )


def forbid_legacy_direct_mutation(backend: MutationBackend) -> MutationBackend:
    return backend


__all__ = [
    "MIGRATED_PUBLIC_TOOLS",
    "MigrationError",
    "MigrationRoute",
    "MutationBackend",
    "forbid_legacy_direct_mutation",
    "route_public_tool",
]

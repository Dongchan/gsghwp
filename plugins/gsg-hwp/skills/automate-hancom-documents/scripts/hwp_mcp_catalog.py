from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Final, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import AnyFunction, Tool
from pydantic import JsonValue, TypeAdapter

from hwp_errors import HwpLiveError
from hwp_native_capabilities import (
    NativeCapabilityInventory,
    native_capability_inventory,
)
from hwp_official_api_catalog import load_official_api_catalog
from hwp_official_api_contract import (
    OfficialApiCategory,
    OfficialApiCoverageReport,
    OfficialApiMatch,
    OfficialApiSearchResult,
)
from hwp_official_api_coverage import build_official_api_coverage
from hwp_official_api_search import search_official_api
from hwp_mcp_metadata import register_public_tools
from hwp_mcp_registry import (
    CapabilityCategory,
    CapabilityOperation,
    McpProfile,
    proxy_tool_specs,
    search_tool_specs,
)
from hwp_runtime_identity import RuntimeStatus, tool_schema_hash


type ToolCatalogName = Literal[
    "worker_tools",
    "proxy_tools",
    "host_visible_tools",
    "qa_tools",
]


_JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_JSON_OBJECT: Final[TypeAdapter[dict[str, JsonValue]]] = TypeAdapter(
    dict[str, JsonValue]
)
_PRODUCTION_CATALOG_GATEWAY_TOOLS: Final = frozenset(("hwp_get_official_api_coverage",))
_OFFICIAL_API_MATCH_LIMIT: Final = 5
_OFFICIAL_API_ITEM_MAX_BYTES: Final = 4_096


class McpToolCatalogError(RuntimeError):
    catalog_name: ToolCatalogName
    conflicting_names: tuple[str, ...]

    def __init__(
        self,
        catalog_name: ToolCatalogName,
        conflicting_names: tuple[str, ...],
    ) -> None:
        message = (
            f"{catalog_name} catalog must not be empty"
            if not conflicting_names
            else f"{catalog_name} catalog has conflicting tools: "
            + ", ".join(conflicting_names)
        )
        super().__init__(message)
        self.catalog_name = catalog_name
        self.conflicting_names = conflicting_names


@dataclass(frozen=True, slots=True)
class McpToolCatalog:
    name: ToolCatalogName
    tools: tuple[Tool, ...]

    def __post_init__(self) -> None:
        if not self.tools:
            raise McpToolCatalogError(self.name, ())
        seen: set[str] = set()
        duplicates: set[str] = set()
        for tool in self.tools:
            if tool.name in seen:
                duplicates.add(tool.name)
            seen.add(tool.name)
        if duplicates:
            raise McpToolCatalogError(self.name, tuple(sorted(duplicates)))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)

    @property
    def count(self) -> int:
        return len(self.tools)

    @property
    def schema_hash(self) -> str:
        return tool_schema_hash(self.tools)


_PROXY_TOOL_SPECS: Final = proxy_tool_specs("production")
if tuple(spec.name for spec in _PROXY_TOOL_SPECS) != ("hwp_reload",):
    raise McpToolCatalogError(
        "proxy_tools", tuple(spec.name for spec in _PROXY_TOOL_SPECS)
    )
_PROXY_TOOL_SPEC: Final = _PROXY_TOOL_SPECS[0]
HWP_RELOAD_TOOL: Final = Tool(
    name=_PROXY_TOOL_SPEC.name,
    description=_PROXY_TOOL_SPEC.description,
    inputSchema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    outputSchema=RuntimeStatus.model_json_schema(),
)
_PROXY_TOOL_CATALOG: Final = McpToolCatalog("proxy_tools", (HWP_RELOAD_TOOL,))
_PROXY_TOOL_NAMES: Final = frozenset(_PROXY_TOOL_CATALOG.names)


def _worker_surface_catalog(
    name: Literal["worker_tools", "qa_tools"],
    tools: Sequence[Tool],
) -> McpToolCatalog:
    catalog = McpToolCatalog(name, tuple(tools))
    conflicts = tuple(
        tool_name for tool_name in catalog.names if tool_name in _PROXY_TOOL_NAMES
    )
    if conflicts:
        raise McpToolCatalogError(name, conflicts)
    return catalog


def worker_tool_catalog(tools: Sequence[Tool]) -> McpToolCatalog:
    return _worker_surface_catalog("worker_tools", tools)


def proxy_tool_catalog() -> McpToolCatalog:
    return _PROXY_TOOL_CATALOG


def host_visible_tool_catalog(worker_tools: Sequence[Tool]) -> McpToolCatalog:
    worker = worker_tool_catalog(worker_tools)
    return McpToolCatalog(
        "host_visible_tools",
        worker.tools + _PROXY_TOOL_CATALOG.tools,
    )


def qa_tool_catalog(tools: Sequence[Tool]) -> McpToolCatalog:
    return _worker_surface_catalog("qa_tools", tools)


def production_catalog_gateway_tool_names() -> frozenset[str]:
    return _PRODUCTION_CATALOG_GATEWAY_TOOLS


def _json_bytes(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = "…"
    marker_size = len(marker.encode("utf-8"))
    if max_bytes < marker_size:
        return ""
    return (
        encoded[: max_bytes - marker_size].decode(
            "utf-8",
            errors="ignore",
        )
        + marker
    )


def bounded_official_api_match(match: OfficialApiMatch) -> JsonValue:
    payload = _JSON_OBJECT.validate_python(
        match.model_dump(mode="json", exclude_none=True)
    )
    payload["truncated"] = False
    normalized = _JSON_VALUE.validate_python(payload)
    if len(_json_bytes(normalized)) <= _OFFICIAL_API_ITEM_MAX_BYTES:
        return normalized

    payload["truncated"] = True
    while len(_json_bytes(_JSON_VALUE.validate_python(payload))) > (
        _OFFICIAL_API_ITEM_MAX_BYTES
    ):
        text_values: dict[str, str] = {}
        for candidate in ("description", "declaration"):
            value = payload.get(candidate)
            if isinstance(value, str) and value:
                text_values[candidate] = value
        if not text_values:
            raise HwpLiveError("공식 API 검색 항목을 4KiB 이내로 제한할 수 없습니다")
        field = max(
            text_values,
            key=lambda candidate: len(text_values[candidate].encode("utf-8")),
        )
        current = text_values[field]
        overflow = (
            len(_json_bytes(_JSON_VALUE.validate_python(payload)))
            - _OFFICIAL_API_ITEM_MAX_BYTES
        )
        target_bytes = max(0, len(current.encode("utf-8")) - overflow - 16)
        payload[field] = _truncate_utf8(current, target_bytes)
    return _JSON_VALUE.validate_python(payload)


def _bounded_official_api_search(query: str, limit: int) -> JsonValue:
    result = search_official_api(
        load_official_api_catalog(),
        query,
        category="all",
        limit=min(limit, _OFFICIAL_API_MATCH_LIMIT),
    )
    return _JSON_VALUE.validate_python(
        {
            "query": result.query,
            "category": result.category,
            "total_matches": result.total_matches,
            "matches": [bounded_official_api_match(match) for match in result.matches],
        }
    )


async def call_production_catalog_gateway_tool(
    tool_name: str,
    arguments: Mapping[str, JsonValue],
) -> JsonValue:
    if tool_name == "hwp_get_official_api_coverage":
        if arguments:
            raise HwpLiveError("공식 API coverage 조회는 인자를 받지 않습니다")
        return _JSON_VALUE.validate_python(
            build_official_api_coverage(load_official_api_catalog()).model_dump(
                mode="json"
            )
        )
    raise HwpLiveError(f"production catalog gateway 도구가 아닙니다: {tool_name}")


def catalog_tool_handlers(profile: McpProfile) -> Mapping[str, AnyFunction]:
    async def hwp_get_capabilities() -> NativeCapabilityInventory:
        return native_capability_inventory(profile)

    async def hwp_search_tools(
        query: str,
        category: CapabilityCategory | None = None,
        operation: CapabilityOperation | None = None,
        limit: int = 10,
        include_official_api: bool = False,
    ) -> dict[str, object]:
        matches = search_tool_specs(
            query,
            profile=profile,
            category=category,
            operation=operation,
            limit=limit,
        )
        response: dict[str, object] = {
            "query": query,
            "matches": tuple(
                {
                    "name": match.name,
                    "description": match.description,
                    "category": match.category,
                    "operation": match.operation,
                    "execution_path": match.execution_path,
                }
                for match in matches
            ),
        }
        if include_official_api:
            response["official_api"] = _bounded_official_api_search(query, limit)
        return response

    async def hwp_search_official_api(
        query: str,
        category: OfficialApiCategory = "all",
        limit: int = 20,
    ) -> OfficialApiSearchResult:
        return search_official_api(
            load_official_api_catalog(),
            query,
            category=category,
            limit=limit,
        )

    async def hwp_get_official_api_coverage() -> OfficialApiCoverageReport:
        return build_official_api_coverage(load_official_api_catalog())

    handlers = (
        hwp_get_capabilities,
        hwp_search_tools,
        hwp_search_official_api,
        hwp_get_official_api_coverage,
    )
    return MappingProxyType({handler.__name__: handler for handler in handlers})


def register_catalog_tools(server: FastMCP[None], profile: McpProfile) -> None:
    register_public_tools(
        server,
        tuple(catalog_tool_handlers(profile).values()),
        profile,
    )

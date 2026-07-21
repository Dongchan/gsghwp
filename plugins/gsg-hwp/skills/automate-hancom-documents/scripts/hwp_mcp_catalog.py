from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from hwp_native_capabilities import (
    NativeCapabilityInventory,
    native_capability_inventory,
)
from hwp_official_api_catalog import load_official_api_catalog
from hwp_official_api_contract import (
    OfficialApiCategory,
    OfficialApiCoverageReport,
    OfficialApiSearchResult,
)
from hwp_official_api_coverage import build_official_api_coverage
from hwp_official_api_search import search_official_api
from hwp_mcp_metadata import register_public_tools
from hwp_mcp_registry import (
    CapabilityCategory,
    CapabilityOperation,
    McpProfile,
    search_tool_specs,
)


def register_catalog_tools(server: FastMCP[None], profile: McpProfile) -> None:
    async def hwp_get_capabilities() -> NativeCapabilityInventory:
        return native_capability_inventory(profile)

    async def hwp_search_tools(
        query: str,
        category: CapabilityCategory | None = None,
        operation: CapabilityOperation | None = None,
        limit: int = 10,
    ) -> dict[str, object]:
        matches = search_tool_specs(
            query,
            profile=profile,
            category=category,
            operation=operation,
            limit=limit,
        )
        return {
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

    register_public_tools(
        server,
        (
            hwp_get_capabilities,
            hwp_search_tools,
            hwp_search_official_api,
            hwp_get_official_api_coverage,
        ),
        profile,
    )

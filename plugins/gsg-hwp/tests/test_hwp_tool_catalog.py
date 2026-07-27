from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import anyio
from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_catalog import (  # noqa: E402
    McpToolCatalogError,
    host_visible_tool_catalog,
    proxy_tool_catalog,
    qa_tool_catalog,
    worker_tool_catalog,
)
from hwp_mcp_registry import McpProfile  # noqa: E402


_SCHEMA_CHILD_KEYS = frozenset(
    (
        "additionalItems",
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    )
)
_SCHEMA_CHILD_LIST_KEYS = frozenset(("allOf", "anyOf", "oneOf", "prefixItems"))
_SCHEMA_CHILD_MAP_KEYS = frozenset(
    ("$defs", "definitions", "dependentSchemas", "patternProperties", "properties")
)
_JSON_OBJECT: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


class _CatalogRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    count: int = Field(ge=1)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    names: tuple[str, ...]


class _CompatibilityManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    tool_catalogs: dict[str, _CatalogRecord]
    production_tools: tuple[str, ...]
    qa_tool_count: int


def _fresh_tools(profile: McpProfile) -> tuple[Tool, ...]:
    server = build_server(LiveHwpController(), profile=profile)

    async def listed() -> tuple[Tool, ...]:
        return tuple(await server.list_tools())

    return anyio.run(listed)


def _contains_schema_title(value: JsonValue) -> bool:
    match value:  # noqa: E501  # noqa: MATCH_OK — JsonValue is exhaustively covered.
        case dict() as schema:
            if "title" in schema:
                return True
            for key, item in schema.items():
                if key in _SCHEMA_CHILD_KEYS and _contains_schema_title(item):
                    return True
                if key in _SCHEMA_CHILD_LIST_KEYS and isinstance(item, list):
                    if any(_contains_schema_title(child) for child in item):
                        return True
                if key in _SCHEMA_CHILD_MAP_KEYS and isinstance(item, dict):
                    if any(_contains_schema_title(child) for child in item.values()):
                        return True
            return False
        case list() as items:
            return any(_contains_schema_title(item) for item in items)
        case str() | bool() | int() | float() | None:
            return False


def test_public_input_schemas_omit_generated_titles() -> None:
    production = host_visible_tool_catalog(_fresh_tools("production"))
    qa = qa_tool_catalog(_fresh_tools("qa"))

    assert all(
        not _contains_schema_title(tool.inputSchema)
        for tool in (*production.tools, *qa.tools)
    )


def test_title_named_fields_survive_schema_metadata_compaction() -> None:
    tools = {
        tool.name: tool
        for tool in host_visible_tool_catalog(_fresh_tools("production")).tools
    }
    report_definitions = _JSON_OBJECT.validate_python(
        tools["hwp_append_report"].inputSchema.get("$defs")
    )
    for definition_name in ("ReportPlan", "ReportSection", "ReportTable"):
        definition = _JSON_OBJECT.validate_python(report_definitions[definition_name])
        properties = _JSON_OBJECT.validate_python(definition.get("properties"))
        assert "title" in properties
    excel_properties = _JSON_OBJECT.validate_python(
        tools["hwp_append_excel_table"].inputSchema.get("properties")
    )
    assert "title" in excel_properties


def test_worker_proxy_host_and_qa_catalogs_match_manifest() -> None:
    worker = worker_tool_catalog(_fresh_tools("production"))
    proxy = proxy_tool_catalog()
    host_visible = host_visible_tool_catalog(worker.tools)
    qa = qa_tool_catalog(_fresh_tools("qa"))
    manifest = _CompatibilityManifest.model_validate_json(
        (SCRIPTS.parents[2] / "compatibility-manifest.json").read_text(encoding="utf-8")
    )

    assert worker.count == 44
    assert proxy.count == 1
    assert host_visible.count == 45
    assert qa.count == 62
    assert "hwp_reload" not in worker.names
    assert proxy.names == ("hwp_reload",)
    assert host_visible.names.count("hwp_reload") == 1

    host_by_name = {tool.name: tool for tool in host_visible.tools}
    for worker_tool in worker.tools:
        host_tool = host_by_name[worker_tool.name]
        assert host_tool.description == worker_tool.description
        assert host_tool.inputSchema == worker_tool.inputSchema

    for catalog in (worker, proxy, host_visible, qa):
        record = manifest.tool_catalogs[catalog.name]
        assert record.count == catalog.count
        assert record.schema_hash == catalog.schema_hash
        assert record.names == catalog.names

    assert manifest.production_tools == worker.names
    assert manifest.qa_tool_count == qa.count


def test_worker_catalog_rejects_proxy_only_tools() -> None:
    worker_tools = _fresh_tools("production")
    reload_tool = proxy_tool_catalog().tools[0]

    with pytest.raises(McpToolCatalogError):
        _ = worker_tool_catalog((*worker_tools, reload_tool))

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

import anyio
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
import hwp_mcp_catalog as catalog  # noqa: E402
from hwp_mcp_catalog import (  # noqa: E402
    host_visible_tool_catalog,
    proxy_tool_catalog,
    qa_tool_catalog,
    worker_tool_catalog,
)
from hwp_mcp_forward import HwpExecuteArguments  # noqa: E402
from hwp_mcp_registry import tool_spec  # noqa: E402
from hwp_official_api_contract import OfficialApiMatch  # noqa: E402


_LEGACY_SEARCH_RESPONSE_SHA256 = (
    "97a9ddb52455d317360741a6809eeaca068bc7cf14c031cdf11514f9c36e9dee"
)
_OFFICIAL_API_ITEM_MAX_BYTES = 4_096
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class _OfficialApiMatchRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    name: str
    declaration: str | None = None
    source_document: str
    source_page_start: int
    source_page_end: int


class _OfficialApiSearchRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    total_matches: int
    matches: tuple[_OfficialApiMatchRecord, ...]


class _ToolSearchWithOfficialApiRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    official_api: _OfficialApiSearchRecord


class _OfficialApiCatalogRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    action_entries: int


class _OfficialApiCoverageRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    schema_version: int
    catalog: _OfficialApiCatalogRecord


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


async def _call_execute(
    tool_name: str,
    arguments: Mapping[str, JsonValue],
) -> JsonValue:
    server = build_server(LiveHwpController(), profile="production")
    return await server.call_unconverted_tool(
        "hwp_execute",
        HwpExecuteArguments(
            {
                "tool_name": tool_name,
                "arguments": dict(arguments),
            }
        ),
    )


async def _call_search(
    query: str,
    include_official_api: bool,
    limit: int = 10,
) -> JsonValue:
    server = build_server(LiveHwpController(), profile="production")
    return await server.call_unconverted_tool(
        "hwp_search_tools",
        HwpExecuteArguments(
            {
                "query": query,
                "include_official_api": include_official_api,
                "limit": limit,
            }
        ),
    )


async def _official_then_legacy_search() -> dict[str, JsonValue]:
    server = build_server(LiveHwpController(), profile="production")
    _ = await server.call_unconverted_tool(
        "hwp_search_tools",
        HwpExecuteArguments(
            {"query": "CtrlID", "include_official_api": True, "limit": 1}
        ),
    )
    response = await server.call_unconverted_tool(
        "hwp_search_tools",
        HwpExecuteArguments({"query": "표 채우는 도구 뭐 있지?"}),
    )
    if not isinstance(response, dict):
        raise AssertionError("tool search response is not an object")
    return response


def test_production_catalog_gateway_searches_official_api_with_bounded_details() -> (
    None
):
    response = anyio.run(
        _call_search,
        "a",
        True,
        50,
    )

    assert isinstance(response, dict)
    parsed = _ToolSearchWithOfficialApiRecord.model_validate(response)
    assert parsed.official_api.total_matches > 5
    official_api = response["official_api"]
    assert isinstance(official_api, dict)
    matches = official_api["matches"]
    assert isinstance(matches, list)
    assert len(matches) == 5
    assert all(
        len(_json_bytes(match)) <= _OFFICIAL_API_ITEM_MAX_BYTES for match in matches
    )

    detail = anyio.run(
        _call_search,
        "Open",
        True,
        1,
    )
    assert isinstance(detail, dict)
    detail_record = _ToolSearchWithOfficialApiRecord.model_validate(detail)
    assert len(detail_record.official_api.matches) == 1
    match = detail_record.official_api.matches[0]
    assert match.name == "Open"
    assert match.declaration
    assert match.source_document == "HwpAutomation_2504.pdf"
    assert match.source_page_start <= match.source_page_end


def test_official_api_match_payload_has_a_hard_four_kibibyte_cap() -> None:
    oversized = OfficialApiMatch(
        category="automation",
        name="Oversized",
        source_document="HwpAutomation_2504.pdf",
        source_page_start=1,
        source_page_end=1,
        description="가" * 10_000,
        declaration="x" * 20_000,
        owner="HwpObject",
    )

    payload = catalog._bounded_official_api_match(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        oversized
    )

    assert isinstance(payload, dict)
    assert payload["truncated"] is True
    assert len(_json_bytes(payload)) <= _OFFICIAL_API_ITEM_MAX_BYTES


def test_production_catalog_gateway_exposes_static_coverage_without_a_session() -> None:
    response = anyio.run(
        _call_execute,
        "hwp_get_official_api_coverage",
        dict[str, JsonValue](),
    )

    assert isinstance(response, dict)
    parsed = _OfficialApiCoverageRecord.model_validate(response)
    assert parsed.schema_version == 1
    assert parsed.catalog.action_entries > 0


def test_official_api_gateway_is_call_scoped_and_legacy_search_is_byte_identical() -> (
    None
):
    legacy = anyio.run(_official_then_legacy_search)
    payload = _json_bytes(legacy)

    assert "official_api" not in legacy
    assert hashlib.sha256(payload).hexdigest() == _LEGACY_SEARCH_RESPONSE_SHA256


def test_catalog_gateway_keeps_public_counts_and_guides_explicit_api_requests() -> None:
    production_server = build_server(LiveHwpController(), profile="production")
    qa_server = build_server(LiveHwpController(), profile="qa")

    async def catalogs() -> tuple[int, int]:
        production_tools = tuple(await production_server.list_tools())
        qa_tools = tuple(await qa_server.list_tools())
        worker = worker_tool_catalog(production_tools)
        host = host_visible_tool_catalog(worker.tools)
        qa = qa_tool_catalog(qa_tools)
        assert worker.count == 44
        assert proxy_tool_catalog().count == 1
        assert host.count == 45
        return host.count, qa.count

    assert anyio.run(catalogs) == (45, 62)
    tools_by_name = {
        tool.name: tool for tool in anyio.run(production_server.list_tools)
    }
    search_tool = tools_by_name["hwp_search_tools"]
    input_schema = _JSON_OBJECT.validate_python(search_tool.inputSchema)
    properties = input_schema["properties"]
    assert isinstance(properties, dict)
    assert properties["include_official_api"] == {
        "default": False,
        "type": "boolean",
    }
    required = input_schema["required"]
    assert isinstance(required, list)
    assert "include_official_api" not in required

    description = tool_spec("hwp_search_tools").description
    for phrase in ("공식 API 찾아봐", "API 열어봐", "API 확인", "명세 확인"):
        assert phrase in description

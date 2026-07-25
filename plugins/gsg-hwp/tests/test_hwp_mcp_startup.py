from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import anyio
from pydantic import BaseModel, ConfigDict, JsonValue


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_rot  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
import hwp_mcp  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_forward import HwpExecuteArguments  # noqa: E402
from hwp_mcp_registry import tool_names, tool_spec  # noqa: E402
from hwp_runtime_identity import RuntimeStatus  # noqa: E402


class _CompatibilityManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    production_tools: tuple[str, ...]
    qa_tool_count: int


class _McpServerConfiguration(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    command: str
    args: tuple[str, ...]


class _McpConfiguration(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    mcpServers: dict[str, _McpServerConfiguration]


class _ToolMatch(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    name: str


class _ToolSearchResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    matches: tuple[_ToolMatch, ...]


def test_rot_catalog_defers_com_runtime_loading_until_first_scan() -> None:
    with (
        patch.object(hwp_live_rot, "_load_pythoncom") as load_pythoncom,
        patch.object(hwp_live_rot, "_load_win32_client") as load_win32_client,
    ):
        _ = hwp_live_rot.HwpRotCatalog()

    load_pythoncom.assert_not_called()
    load_win32_client.assert_not_called()


def test_fresh_production_server_exposes_every_registered_tool() -> None:
    server = build_server(LiveHwpController(), profile="production")

    listed = frozenset(tool.name for tool in anyio.run(server.list_tools))

    assert listed == tool_names("production")


def test_registered_descriptions_come_from_the_authoritative_catalog() -> None:
    server = build_server(LiveHwpController(), profile="production")

    listed = anyio.run(server.list_tools)

    for tool in listed:
        assert tool.description == tool_spec(tool.name).description


def test_main_defers_wrapper_and_operation_registry_loading() -> None:
    server = MagicMock()
    with (
        patch.object(hwp_mcp, "startup_runtime_record", return_value="runtime"),
        patch.object(hwp_mcp, "ensure_codex_skill_registered"),
        patch.object(hwp_mcp, "ensure_native_bridge_registered"),
        patch.object(hwp_mcp, "build_server", return_value=server),
        patch.object(hwp_mcp, "configured_mcp_profile", return_value="production"),
        patch.object(hwp_mcp, "preload_live_wrapper", create=True) as preload,
        patch.object(hwp_mcp, "operation_registry", create=True) as registry,
    ):
        hwp_mcp.main()

    preload.assert_not_called()
    registry.assert_not_called()
    server.run.assert_called_once_with(transport="stdio")


def test_production_catalog_keeps_stable_forward_gateway() -> None:
    assert "hwp_execute" in tool_names("production")


def test_mcp_configuration_uses_portable_update_launcher() -> None:
    configuration = _McpConfiguration.model_validate_json(
        (SCRIPTS.parents[2] / ".mcp.json").read_text(encoding="utf-8")
    )
    server = configuration.mcpServers["gsg-hwp"]

    assert server.command == "powershell.exe"
    assert server.args == (
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "./scripts/start-mcp.ps1",
    )


def test_runtime_identity_tool_is_public_and_forwardable() -> None:
    assert "hwp_runtime_info" in tool_names("production")
    server = build_server(LiveHwpController(), profile="production")

    async def runtime_identity() -> tuple[RuntimeStatus, RuntimeStatus]:
        direct = await server.call_unconverted_tool(
            "hwp_runtime_info",
            HwpExecuteArguments({}),
        )
        forwarded = await server.call_unconverted_tool(
            "hwp_execute",
            HwpExecuteArguments({"tool_name": "hwp_runtime_info", "arguments": {}}),
        )
        return (
            RuntimeStatus.model_validate(direct),
            RuntimeStatus.model_validate(forwarded),
        )

    direct, forwarded = anyio.run(runtime_identity)

    assert direct == forwarded
    assert direct.process_id > 0
    assert direct.worker_process_id == direct.process_id
    assert direct.source_path == str(SCRIPTS.parents[2].resolve())
    assert len(direct.tool_schema_hash) == 64


def test_stable_forward_gateway_calls_newer_registered_tool() -> None:
    server = build_server(LiveHwpController(), profile="production")

    async def forward_search() -> JsonValue:
        return await server.call_unconverted_tool(
            "hwp_execute",
            HwpExecuteArguments(
                {
                    "tool_name": "hwp_search_tools",
                    "arguments": {"query": "페이지 삭제", "limit": 3},
                }
            ),
        )

    response = _ToolSearchResponse.model_validate(anyio.run(forward_search))

    assert response.matches[0].name == "hwp_delete_page"


def test_stable_forward_gateway_serializes_newer_tool_result() -> None:
    server = build_server(LiveHwpController(), profile="production")

    async def forward_search() -> None:
        _ = await server.call_tool(
            "hwp_execute",
            {
                "tool_name": "hwp_search_tools",
                "arguments": {"query": "페이지 삭제", "limit": 3},
            },
        )

    anyio.run(forward_search)


def test_compatibility_manifest_matches_fresh_production_catalog() -> None:
    manifest = _CompatibilityManifest.model_validate_json(
        (SCRIPTS.parents[2] / "compatibility-manifest.json").read_text(encoding="utf-8")
    )

    assert frozenset(manifest.production_tools) == tool_names("production")
    assert manifest.qa_tool_count == len(tool_names("qa"))

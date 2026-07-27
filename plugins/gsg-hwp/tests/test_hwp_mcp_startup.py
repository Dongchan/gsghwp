from __future__ import annotations

import json
import sys
from io import StringIO
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
from hwp_mcp_forward import ForwardingFastMCP, HwpExecuteArguments  # noqa: E402
from hwp_mcp_registry import tool_names, tool_spec  # noqa: E402
from hwp_runtime_identity import RuntimeStatus  # noqa: E402


class _CompatibilityManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    production_tools: tuple[str, ...]
    qa_tool_count: int


class _McpServerConfiguration(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

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
        patch.object(
            hwp_mcp,
            "startup_codex_skill_registration_record",
            return_value="skill-registration",
        ),
        patch.object(hwp_mcp, "startup_runtime_record", return_value="runtime"),
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


def test_main_starts_without_a_legacy_junction_or_writable_skill_home(
    tmp_path: Path,
) -> None:
    # Given
    server = MagicMock()
    stderr = StringIO()
    codex_home = tmp_path / "read-only-codex-home"
    _ = codex_home.write_text("not a directory\n", encoding="utf-8")

    # When
    with (
        patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}),
        patch.object(hwp_mcp, "stderr", stderr),
        patch.object(hwp_mcp, "startup_runtime_record", return_value="runtime"),
        patch.object(hwp_mcp, "ensure_native_bridge_registered"),
        patch.object(hwp_mcp, "build_server", return_value=server),
        patch.object(hwp_mcp, "configured_mcp_profile", return_value="production"),
    ):
        hwp_mcp.main()

    # Then
    registration_record, runtime_record = stderr.getvalue().splitlines()
    registration = json.loads(registration_record)
    assert registration["event"] == "hwp_codex_skill_registration"
    assert registration["owner"] == "plugin_manifest"
    assert registration["legacy_state"] == "absent"
    assert runtime_record == "runtime"
    assert codex_home.read_text(encoding="utf-8") == "not a directory\n"
    server.run.assert_called_once_with(transport="stdio")


def test_production_catalog_keeps_stable_forward_gateway() -> None:
    assert "hwp_execute" in tool_names("production")


def test_mcp_configuration_uses_runtime_preflight_proxy() -> None:
    configuration = _McpConfiguration.model_validate_json(
        (SCRIPTS.parents[2] / ".mcp.json").read_text(encoding="utf-8")
    )
    server = configuration.mcpServers["gsg-hwp-beta-live"]

    assert server.args == (
        "./.venv/Scripts/python.exe",
        "-X",
        "utf8",
        "-B",
        "./skills/automate-hancom-documents/scripts/hwp_runtime_preflight.py",
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
            HwpExecuteArguments(
                {"tool_name": "hwp_runtime_info", "arguments": {}}
            ),
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


def test_document_selector_is_a_public_compatible_alias() -> None:
    server = ForwardingFastMCP("document-selector-alias")

    async def target(*, document_path: str | None = None) -> dict[str, str | None]:
        return {"document_path": document_path}

    server.add_tool(target, name="target")

    async def invoke() -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        listed = {tool.name: tool for tool in await server.list_tools()}
        schema = listed["target"].inputSchema
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise AssertionError("tool properties schema is not an object")
        assert {"document_path", "document_selector"} <= properties.keys()
        direct = await server.call_unconverted_tool(
            "target",
            HwpExecuteArguments({"document_selector": "document_id:7"}),
        )
        legacy = await server.call_unconverted_tool(
            "target",
            HwpExecuteArguments({"document_path": "C:/docs/report.hwp"}),
        )
        if not isinstance(direct, dict) or not isinstance(legacy, dict):
            raise AssertionError("alias target result is not an object")
        return direct, legacy

    direct, legacy = anyio.run(invoke)

    assert direct == {"document_path": "document_id:7"}
    assert legacy == {"document_path": "C:/docs/report.hwp"}


def test_document_selector_alias_rejects_conflicting_values() -> None:
    server = ForwardingFastMCP("document-selector-conflict")

    async def target(*, document_path: str | None = None) -> str | None:
        return document_path

    server.add_tool(target, name="target")

    async def invoke() -> None:
        _ = await server.call_unconverted_tool(
            "target",
            HwpExecuteArguments(
                {
                    "document_path": "C:/one/report.hwp",
                    "document_selector": "D:/two/report.hwp",
                }
            ),
        )

    try:
        anyio.run(invoke)
    except Exception as error:
        assert "document_path" in str(error)
        assert "document_selector" in str(error)
    else:
        raise AssertionError("conflicting document aliases must be rejected")


def test_compatibility_manifest_matches_fresh_production_catalog() -> None:
    manifest = _CompatibilityManifest.model_validate_json(
        (SCRIPTS.parents[2] / "compatibility-manifest.json").read_text(encoding="utf-8")
    )

    assert frozenset(manifest.production_tools) == tool_names("production")
    assert manifest.qa_tool_count == len(tool_names("qa"))

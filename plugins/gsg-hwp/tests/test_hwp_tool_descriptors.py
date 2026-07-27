from __future__ import annotations

import sys
from pathlib import Path

import anyio
from mcp.types import Tool
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
from hwp_mcp_registration import McpToolBindingError  # noqa: E402
import hwp_mcp_registry as registry  # noqa: E402
from hwp_mcp_registry import (  # noqa: E402
    McpProfile,
    McpToolSpec,
    McpToolSpecError,
    proxy_tool_specs,
    tool_names,
    tool_spec,
    tool_specs,
)
from hwp_operation_descriptor import (  # noqa: E402
    descriptor_adapter_target,
    operation_descriptor,
)


def _fresh_tools(profile: McpProfile) -> tuple[Tool, ...]:
    server = build_server(LiveHwpController(), profile=profile)

    async def listed() -> tuple[Tool, ...]:
        return tuple(await server.list_tools())

    return anyio.run(listed)


def test_registry_and_registration_match_for_every_worker_profile() -> None:
    for profile in ("production", "qa"):
        tools = _fresh_tools(profile)
        specs = tool_specs(profile)

        assert tuple(tool.name for tool in tools) == tuple(spec.name for spec in specs)
        assert frozenset(tool.name for tool in tools) == tool_names(profile)
        for tool in tools:
            assert tool.inputSchema["type"] == "object"


def test_production_descriptors_derive_handler_operation_mutation_and_verifier() -> (
    None
):
    registered_names = frozenset(tool.name for tool in _fresh_tools("production"))

    for spec in tool_specs("production"):
        assert spec.name in registered_names
        assert spec.handler_source in {"bindings", "catalog", "server", "gateway"}
        assert spec.exposure == "worker"
        assert spec.operation in {"read", "write", "session"}
        for workflow in spec.workflows:
            assert operation_descriptor(workflow) is not None
        if spec.may_mutate:
            assert spec.has_readback_verifier

    proxy_specs = proxy_tool_specs("production")
    assert tuple(spec.name for spec in proxy_specs) == ("hwp_reload",)
    assert proxy_specs[0].handler_source == "proxy"
    assert proxy_specs[0].exposure == "proxy"
    assert tool_spec("hwp_render_page").binding_owner == "public_inspection_tools"


def test_mutating_production_tool_without_verifier_is_rejected() -> None:
    with pytest.raises(McpToolSpecError):
        McpToolSpec(
            "hwp_missing_verifier",
            "mutating tool without a readback verifier",
            "text",
            "write",
            "python_catalog",
            requires_session=False,
            profiles=frozenset(("production",)),
        )


def test_append_adapters_share_layout_workflow_and_verifier() -> None:
    primary = tool_spec("hwp_append_layout")

    for adapter_name in ("hwp_append_report", "hwp_append_excel_table"):
        adapter = tool_spec(adapter_name)
        assert descriptor_adapter_target(adapter_name) == primary.name
        assert adapter.workflows == primary.workflows == ("document.append_layout",)
        assert adapter.verification_modes == primary.verification_modes


def test_partial_registry_addition_fails_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan = McpToolSpec(
        "hwp_orphan_registration",
        "orphan registration test tool",
        "diagnostic",
        "read",
        "python_catalog",
        requires_session=False,
        profiles=frozenset(("production",)),
    )
    monkeypatch.setattr(registry, "MCP_TOOL_SPECS", (*registry.MCP_TOOL_SPECS, orphan))

    with pytest.raises(McpToolBindingError):
        build_server(LiveHwpController(), profile="production")

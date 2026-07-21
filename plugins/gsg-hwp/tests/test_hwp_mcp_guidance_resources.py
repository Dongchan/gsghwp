from __future__ import annotations

import sys
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_mcp_resources import (  # noqa: E402
    NATIVE_LAYOUT_RESOURCE_URI,
    SKILL_RESOURCE_URI,
    register_guidance_resources,
)
from hwp_mcp_hot_reload import build_proxy  # noqa: E402


def test_guidance_resources_are_listed_and_utf8_readable() -> None:
    server = FastMCP("guidance-test")
    register_guidance_resources(server)

    resources = anyio.run(server.list_resources)
    uris = {str(resource.uri) for resource in resources}

    assert {SKILL_RESOURCE_URI, NATIVE_LAYOUT_RESOURCE_URI} <= uris

    skill_contents = tuple(anyio.run(server.read_resource, SKILL_RESOURCE_URI))
    layout_contents = tuple(anyio.run(server.read_resource, NATIVE_LAYOUT_RESOURCE_URI))
    skill_content = skill_contents[0].content
    layout_content = layout_contents[0].content

    assert isinstance(skill_content, str)
    assert isinstance(layout_content, str)
    assert "CreatePageImage" in skill_content
    assert "editable structure" in layout_content
    assert "border_mode" in layout_content
    assert "explicit" in layout_content


def test_stable_proxy_exposes_guidance_without_worker_tool_discovery() -> None:
    proxy = build_proxy()

    resources = anyio.run(proxy.list_resources)
    uris = {str(resource.uri) for resource in resources}

    assert {SKILL_RESOURCE_URI, NATIVE_LAYOUT_RESOURCE_URI} <= uris

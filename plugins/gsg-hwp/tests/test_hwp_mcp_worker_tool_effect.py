from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import JsonValue


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_mcp_registry import (  # noqa: E402
    MCP_TOOL_SPECS,
    ToolEffect,
    tool_effect,
    tool_names,
)
from hwp_mcp_worker_tool_policy import (  # noqa: E402
    worker_tool_effect,
    worker_tool_may_mutate,
)


def test_every_production_tool_has_one_registry_effect() -> None:
    effects = {spec.name: spec.effect for spec in MCP_TOOL_SPECS}

    assert set(tool_names("production")) <= effects.keys()
    assert set(effects.values()) <= {
        "read",
        "document",
        "file",
        "artifact",
        "session",
    }
    for spec in MCP_TOOL_SPECS:
        assert worker_tool_effect(spec.name, {}) == spec.effect


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    (
        ("hwp_analyze_reference_image", "artifact"),
        ("hwp_get_reference_image_analysis_section", "read"),
        ("hwp_preflight_layout", "read"),
        ("hwp_render_page", "artifact"),
        ("hwp_patch_text", "document"),
        ("hwp_save", "file"),
        ("hwp_connect", "session"),
    ),
)
def test_registry_distinguishes_tool_effects(
    tool_name: str,
    expected: ToolEffect,
) -> None:
    assert tool_effect(tool_name) == expected


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    (
        ("hwp_runtime_info", "read"),
        ("hwp_analyze_reference_image", "artifact"),
        ("hwp_patch_text", "document"),
        ("hwp_save", "file"),
        ("hwp_connect", "session"),
    ),
)
def test_hwp_execute_uses_forwarded_tool_effect(
    forwarded: str,
    expected: ToolEffect,
) -> None:
    arguments: dict[str, JsonValue] = {
        "tool_name": forwarded,
        "arguments": {},
    }

    assert worker_tool_effect("hwp_execute", arguments) == expected


def test_resolve_only_operation_is_read_only() -> None:
    arguments: dict[str, JsonValue] = {
        "resolve_only": True,
        "allow_document_change": True,
    }

    assert worker_tool_effect("hwp_operate", arguments) == "read"
    assert worker_tool_may_mutate("hwp_operate", arguments) is False


def test_unknown_tool_cannot_silently_enter_registry_classification() -> None:
    with pytest.raises(KeyError):
        _ = tool_effect("hwp_test_unclassified")

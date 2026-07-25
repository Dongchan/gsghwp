from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, cast

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = (
    PLUGIN_ROOT
    / "addon"
    / "HancomMcpLauncher"
    / "bin"
    / "Release"
    / "HancomMcpLauncher.exe"
)


def _result_object(result: CallToolResult) -> dict[str, Any]:
    if result.isError:
        messages = [
            block.text for block in result.content if isinstance(block, TextContent)
        ]
        raise RuntimeError("; ".join(messages) or "MCP tool returned an error")
    if result.structuredContent is not None:
        return cast(dict[str, Any], result.structuredContent)
    for block in result.content:
        if isinstance(block, TextContent):
            parsed = json.loads(block.text)
            if isinstance(parsed, dict):
                return cast(dict[str, Any], parsed)
    raise RuntimeError("MCP tool returned no structured object")


def _matching_document(
    listed: dict[str, Any],
    document: Path,
) -> dict[str, Any]:
    expected = os.path.normcase(os.path.abspath(document))
    matches = [
        item
        for item in listed["documents"]
        if os.path.normcase(os.path.abspath(item["full_name"])) == expected
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one open document for {document}, found {len(matches)}"
        )
    return cast(dict[str, Any], matches[0])


async def _run(
    document: Path,
    fixture_path: Path,
    operation_id: str,
    render_copy: Path,
) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=str(LAUNCHER),
        args=[
            "./.venv/Scripts/python.exe",
            "-B",
            "./skills/automate-hancom-documents/scripts/hwp_mcp_hot_reload.py",
        ],
        cwd=PLUGIN_ROOT,
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            runtime = _result_object(
                await session.call_tool("hwp_runtime_info", {})
            )
            listed_before = _result_object(
                await session.call_tool("hwp_list_open_documents", {})
            )
            opened_before = _matching_document(listed_before, document)
            baseline_page_count = int(opened_before["page_count"])
            started = time.perf_counter()
            inserted = _result_object(
                await session.call_tool(
                    "hwp_insert_layout",
                    {
                        "operation_id": operation_id,
                        "document_path": str(document),
                        "layout": {
                            "target": "after_page",
                            "page": baseline_page_count,
                            "replace_selection": False,
                            "blocks": [fixture],
                        },
                    },
                )
            )
            wall_time_ms = round((time.perf_counter() - started) * 1000, 3)
            operation = _result_object(
                await session.call_tool(
                    "hwp_get_operation_status",
                    {
                        "operation_id": operation_id,
                        "document_path": str(document),
                    },
                )
            )
            listed_after = _result_object(
                await session.call_tool("hwp_list_open_documents", {})
            )
            opened_after = _matching_document(listed_after, document)
            after_page_count = int(opened_after["page_count"])
            created_page = baseline_page_count + 1
            structure = None
            render = None
            if after_page_count >= created_page:
                structure = _result_object(
                    await session.call_tool(
                        "hwp_inspect_structure",
                        {
                            "document_path": str(document),
                            "page": created_page,
                        },
                    )
                )
                render = _result_object(
                    await session.call_tool(
                        "hwp_render_page",
                        {
                            "document_path": str(document),
                            "page": created_page,
                            "dpi": 144,
                        },
                    )
                )
                source_render = Path(render["path"])
                render_copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_render, render_copy)
            return {
                "runtime": runtime,
                "operation_id": operation_id,
                "fixture": {
                    "rows": len(fixture["row_breakpoints"]) - 1,
                    "columns": len(fixture["column_breakpoints"]) - 1,
                    "merges": len(fixture["merges"]),
                    "visible_edges": len(fixture["visible_edges"]),
                    "style_regions": len(fixture["style_regions"]),
                    "text_anchors": len(fixture["text_anchors"]),
                },
                "baseline_page_count": baseline_page_count,
                "after_page_count": after_page_count,
                "wall_time_ms": wall_time_ms,
                "inserted": inserted,
                "operation": operation,
                "structure": structure,
                "render": render,
                "render_copy": str(render_copy) if render is not None else None,
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--render-copy", type=Path, required=True)
    arguments = parser.parse_args()
    result = anyio.run(
        _run,
        arguments.document.resolve(),
        arguments.fixture.resolve(),
        arguments.operation_id,
        arguments.render_copy.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["inserted"].get("status") != "succeeded":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

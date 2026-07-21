from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
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
            block.text
            for block in result.content
            if isinstance(block, TextContent)
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


def _inspection_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _history_directories() -> frozenset[str]:
    root = Path(tempfile.gettempdir())
    return frozenset(
        str(path.resolve()) for path in root.glob("gsg-hwp-live-history-*")
    )


async def _run(document: Path, mutate: bool) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=str(LAUNCHER),
        args=[
            "./.venv/Scripts/python.exe",
            "-B",
            "./skills/automate-hancom-documents/scripts/hwp_mcp_hot_reload.py",
        ],
        cwd=PLUGIN_ROOT,
    )
    history_before = _history_directories()
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_before = await session.list_tools()
            names_before = frozenset(tool.name for tool in tools_before.tools)
            runtime_before = _result_object(
                await session.call_tool("hwp_runtime_info", {})
            )
            runtime_after = _result_object(await session.call_tool("hwp_reload", {}))
            tools_after = await session.list_tools()
            names_after = frozenset(tool.name for tool in tools_after.tools)

            search = _result_object(
                await session.call_tool(
                    "hwp_search_tools",
                    {
                        "query": "표 행은 늘리지 말고 기존 셀 값만 채워",
                        "limit": 5,
                    },
                )
            )
            search_names = tuple(match["name"] for match in search["matches"])

            listed = _result_object(
                await session.call_tool("hwp_list_open_documents", {})
            )
            expected_path = os.path.normcase(os.path.abspath(document))
            matching = [
                item
                for item in listed["documents"]
                if os.path.normcase(os.path.abspath(item["full_name"]))
                == expected_path
            ]
            if len(matching) != 1:
                raise RuntimeError(
                    f"Expected one open document for {document}, found {len(matching)}"
                )
            opened = matching[0]
            baseline_page_count = int(opened["page_count"])
            baseline = _result_object(
                await session.call_tool(
                    "hwp_inspect_page_fast",
                    {
                        "document_path": str(document),
                        "page": baseline_page_count,
                        "include_cells": True,
                    },
                )
            )
            baseline_hash = _inspection_hash(baseline)

            deleted = False
            restored: dict[str, Any] | None = None
            delete_result: dict[str, Any] | None = None
            undo_result: dict[str, Any] | None = None
            after_delete: dict[str, Any] | None = None
            if mutate:
                try:
                    delete_result = _result_object(
                        await session.call_tool(
                            "hwp_delete_page",
                            {
                                "document_path": str(document),
                                "page": baseline_page_count,
                            },
                        )
                    )
                    deleted = bool(delete_result.get("modified"))
                    after_delete = _result_object(
                        await session.call_tool(
                            "hwp_inspect_page_fast",
                            {
                                "document_path": str(document),
                                "page": baseline_page_count - 1,
                                "include_cells": False,
                            },
                        )
                    )
                    undo_result = _result_object(
                        await session.call_tool(
                            "hwp_undo",
                            {"document_path": str(document), "steps": 1},
                        )
                    )
                    restored = _result_object(
                        await session.call_tool(
                            "hwp_inspect_page_fast",
                            {
                                "document_path": str(document),
                                "page": baseline_page_count,
                                "include_cells": True,
                            },
                        )
                    )
                    deleted = False
                finally:
                    if deleted:
                        _ = await session.call_tool(
                            "hwp_undo",
                            {"document_path": str(document), "steps": 1},
                        )

    history_after = _history_directories()
    assert "hwp_runtime_info" in names_before
    assert "hwp_reload" in names_before
    assert names_before == names_after
    assert runtime_after["generation"] > runtime_before["generation"]
    assert runtime_after["worker_process_id"] != runtime_before["worker_process_id"]
    assert search_names[0] == "hwp_fill_table"
    assert "hwp_expand_and_fill_table" not in search_names
    assert history_after == history_before
    result: dict[str, Any] = {
        "document": str(document),
        "mode": "edit-and-undo" if mutate else "read-only",
        "tool_count": len(names_after),
        "runtime_before": runtime_before,
        "runtime_after": runtime_after,
        "search_matches": search_names,
        "baseline_page_count": baseline_page_count,
        "baseline_inspection_hash": baseline_hash,
        "checkpoint_directories_created": sorted(history_after - history_before),
    }
    if mutate:
        assert delete_result is not None and delete_result["modified"] is True
        assert delete_result["verified"] is True
        assert after_delete is not None
        assert int(after_delete["page_count"]) == baseline_page_count - 1
        assert undo_result is not None and undo_result["modified"] is True
        assert undo_result["verified"] is True
        assert restored is not None
        assert int(restored["page_count"]) == baseline_page_count
        assert _inspection_hash(restored) == baseline_hash
        result.update(
            {
                "after_delete_page_count": int(after_delete["page_count"]),
                "restored_page_count": int(restored["page_count"]),
                "restored_inspection_hash": _inspection_hash(restored),
                "delete_result": delete_result,
                "undo_result": undo_result,
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--read-only", action="store_true")
    arguments = parser.parse_args()
    document = arguments.document.resolve()
    if not document.is_file():
        raise SystemExit(f"Document does not exist: {document}")
    result = anyio.run(_run, document, not arguments.read_only)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

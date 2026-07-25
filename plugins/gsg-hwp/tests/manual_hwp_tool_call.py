from __future__ import annotations

import argparse
import json
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from manual_hwp_mcp_qa import LAUNCHER, PLUGIN_ROOT, _result_object


async def _run(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=str(LAUNCHER),
        args=[
            "./.venv/Scripts/python.exe",
            "-B",
            "./skills/automate-hancom-documents/scripts/hwp_mcp_hot_reload.py",
        ],
        cwd=PLUGIN_ROOT,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return _result_object(await session.call_tool(tool, arguments))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True)
    parser.add_argument("--arguments", default="{}")
    arguments = parser.parse_args()
    tool_arguments = json.loads(arguments.arguments)
    if not isinstance(tool_arguments, dict):
        raise SystemExit("--arguments must decode to a JSON object")
    result = anyio.run(_run, arguments.tool, tool_arguments)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

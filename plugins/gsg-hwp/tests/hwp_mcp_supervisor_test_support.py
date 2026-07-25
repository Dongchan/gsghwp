from __future__ import annotations

from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)


def write_blocking_worker(tmp_path: Path, port: int) -> Path:
    path = tmp_path / "cancellable_worker.py"
    _ = path.write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "import sys",
                "import anyio",
                f"sys.path.insert(0, {str(SCRIPTS)!r})",
                "from hwp_mcp_forward import ForwardingFastMCP",
                "server = ForwardingFastMCP('cancellation-test')",
                "server.add_tool(server.hwp_runtime_info, name='hwp_runtime_info')",
                "@server.tool()",
                "async def hwp_test_slow_change() -> dict[str, bool]:",
                f"    stream = await anyio.connect_tcp('127.0.0.1', {port})",
                "    async with stream:",
                "        await stream.send(b'S')",
                "        _ = await stream.receive(1)",
                "    return {'changed': True}",
                "@server.tool()",
                "async def hwp_test_probe() -> dict[str, bool]:",
                "    return {'ready': True}",
                "@server.tool()",
                "async def hwp_watch_state(timeout_ms: int = 0) -> dict[str, bool]:",
                "    _ = timeout_ms",
                f"    stream = await anyio.connect_tcp('127.0.0.1', {port})",
                "    async with stream:",
                "        await stream.send(b'W')",
                "        _ = await stream.receive(1)",
                "    return {'changed': False}",
                "server.run(transport='stdio')",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path

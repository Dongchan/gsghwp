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
                "from hwp_mcp_forward import ForwardingFastMCP, HwpExecuteArguments",
                "from hwp_public_contract import PublicActionResult",
                "from hwp_runtime_identity import RUNTIME_BUILD_INFO",
                "server = ForwardingFastMCP('cancellation-test')",
                "server.add_tool(server.hwp_runtime_info, name='hwp_runtime_info')",
                "@server.tool()",
                "async def hwp_test_slow_change() -> PublicActionResult:",
                f"    stream = await anyio.connect_tcp('127.0.0.1', {port})",
                "    async with stream:",
                "        await stream.send(b'S')",
                "        _ = await stream.receive(1)",
                "    return PublicActionResult(",
                "        status='succeeded',",
                "        message='changed',",
                "        runtime=RUNTIME_BUILD_INFO,",
                "        verified=True,",
                "        modified=True,",
                "        retry_safe=True,",
                "    )",
                "@server.tool()",
                "async def hwp_execute(",
                "    tool_name: str,",
                "    arguments: HwpExecuteArguments,",
                "):",
                "    return await server.call_unconverted_tool(tool_name, arguments)",
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


def write_crashing_worker(tmp_path: Path) -> Path:
    path = tmp_path / "crashing_worker.py"
    _ = path.write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "import os",
                "import sys",
                f"sys.path.insert(0, {str(SCRIPTS)!r})",
                "from hwp_mcp_forward import ForwardingFastMCP",
                "from hwp_public_contract import PublicActionResult",
                "server = ForwardingFastMCP('transport-loss-test')",
                "server.add_tool(server.hwp_runtime_info, name='hwp_runtime_info')",
                "@server.tool()",
                "async def hwp_test_crash() -> PublicActionResult:",
                "    os._exit(71)",
                "@server.tool()",
                "async def hwp_test_probe() -> dict[str, bool]:",
                "    return {'ready': True}",
                "server.run(transport='stdio')",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path

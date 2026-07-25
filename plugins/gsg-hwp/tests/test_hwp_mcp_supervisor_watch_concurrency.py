from __future__ import annotations

import sys
from pathlib import Path

import anyio
from anyio.abc import SocketAttribute, SocketStream
from mcp.shared.memory import create_connected_server_and_client_session

from hwp_mcp_supervisor_test_support import SCRIPTS, write_blocking_worker


sys.path.insert(0, str(SCRIPTS))

from hwp_mcp_hot_reload import build_proxy  # noqa: E402
from hwp_mcp_worker_session import HwpWorkerLaunch  # noqa: E402


async def _probe_qa_call_while_watch_is_waiting(tmp_path: Path) -> None:
    listener = await anyio.create_tcp_listener(
        local_host="127.0.0.1",
        local_port=0,
    )
    port = int(listener.extra(SocketAttribute.local_address)[1])
    worker_script = write_blocking_worker(tmp_path, port)
    proxy = build_proxy(
        HwpWorkerLaunch(
            python_executable=Path(sys.executable),
            worker_script=worker_script,
            watch_paths=(worker_script,),
        )
    )
    watch_started = anyio.Event()
    release_watch = anyio.Event()
    watch_finished = anyio.Event()

    async def coordinate_watch(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"W"
            watch_started.set()
            await release_watch.wait()
            await stream.send(b"R")

    async with listener:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(listener.serve, coordinate_watch)
            async with create_connected_server_and_client_session(proxy) as client:

                async def run_watch() -> None:
                    result = await client.call_tool(
                        "hwp_watch_state",
                        {"timeout_ms": 30_000},
                    )
                    assert result.isError is False
                    watch_finished.set()

                _ = tasks.start_soon(run_watch)
                await watch_started.wait()
                try:
                    with anyio.fail_after(0.5):
                        probe = await client.call_tool("hwp_test_probe", {})
                    assert probe.isError is False
                finally:
                    release_watch.set()
                await watch_finished.wait()
            tasks.cancel_scope.cancel()


def test_qa_watch_does_not_block_an_unrelated_proxy_call(tmp_path: Path) -> None:
    anyio.run(_probe_qa_call_while_watch_is_waiting, tmp_path)

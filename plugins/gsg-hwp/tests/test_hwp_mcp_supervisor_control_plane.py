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
from hwp_mcp_worker_supervisor import HwpWorkerSupervisor  # noqa: E402
from hwp_runtime_identity import RuntimeStatus  # noqa: E402


async def _probe_status_while_worker_is_busy(tmp_path: Path) -> None:
    listener = await anyio.create_tcp_listener(
        local_host="127.0.0.1",
        local_port=0,
    )
    port = int(listener.extra(SocketAttribute.local_address)[1])
    worker_script = write_blocking_worker(tmp_path, port)
    supervisor = HwpWorkerSupervisor(
        HwpWorkerLaunch(
            python_executable=Path(sys.executable),
            worker_script=worker_script,
            watch_paths=(worker_script,),
        )
    )
    mutation_started = anyio.Event()
    release_mutation = anyio.Event()

    async def coordinate_worker(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"S"
            mutation_started.set()
            await release_mutation.wait()
            await stream.send(b"R")

    async def run_call() -> None:
        _ = await supervisor.call_tool("hwp_test_slow_change", {})

    async with listener:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(listener.serve, coordinate_worker)
            async with supervisor.running():
                _ = tasks.start_soon(run_call)
                await mutation_started.wait()
                with anyio.fail_after(0.5):
                    status = await supervisor.status()
                assert status.worker_state == "busy"
                release_mutation.set()
            tasks.cancel_scope.cancel()


def test_runtime_status_is_local_while_worker_is_busy(tmp_path: Path) -> None:
    anyio.run(_probe_status_while_worker_is_busy, tmp_path)


async def _probe_deferred_reload_while_worker_is_busy(tmp_path: Path) -> None:
    listener = await anyio.create_tcp_listener(
        local_host="127.0.0.1",
        local_port=0,
    )
    port = int(listener.extra(SocketAttribute.local_address)[1])
    worker_script = write_blocking_worker(tmp_path, port)
    supervisor = HwpWorkerSupervisor(
        HwpWorkerLaunch(
            python_executable=Path(sys.executable),
            worker_script=worker_script,
            watch_paths=(worker_script,),
        )
    )
    mutation_started = anyio.Event()
    release_mutation = anyio.Event()
    call_finished = anyio.Event()

    async def coordinate_worker(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"S"
            mutation_started.set()
            await release_mutation.wait()
            await stream.send(b"R")

    async def run_call() -> None:
        _ = await supervisor.call_tool("hwp_test_slow_change", {})
        call_finished.set()

    async with listener:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(listener.serve, coordinate_worker)
            async with supervisor.running():
                before = await supervisor.status()
                _ = tasks.start_soon(run_call)
                await mutation_started.wait()
                with anyio.fail_after(0.5):
                    deferred = await supervisor.reload()
                assert deferred.worker_state == "busy"
                assert deferred.reload_state == "deferred"

                release_mutation.set()
                await call_finished.wait()
                probe = await supervisor.call_tool("hwp_test_probe", {})
                after = await supervisor.status()
                assert probe.reloaded is True
                assert after.generation == before.generation + 1
                assert after.reload_state == "reloaded"
            tasks.cancel_scope.cancel()


def test_reload_is_deferred_until_busy_worker_reaches_quiescence(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_deferred_reload_while_worker_is_busy, tmp_path)


async def _probe_proxy_control_plane_while_worker_is_busy(tmp_path: Path) -> None:
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
    mutation_started = anyio.Event()
    release_mutation = anyio.Event()
    call_finished = anyio.Event()

    async def coordinate_worker(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"S"
            mutation_started.set()
            await release_mutation.wait()
            await stream.send(b"R")

    async with listener:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(listener.serve, coordinate_worker)
            async with create_connected_server_and_client_session(proxy) as client:

                async def run_call() -> None:
                    result = await client.call_tool("hwp_test_slow_change", {})
                    assert result.isError is False
                    call_finished.set()

                _ = tasks.start_soon(run_call)
                await mutation_started.wait()
                with anyio.fail_after(0.5):
                    runtime_result = await client.call_tool("hwp_runtime_info", {})
                assert runtime_result.structuredContent is not None
                runtime = RuntimeStatus.model_validate(runtime_result.structuredContent)
                assert runtime.worker_state == "busy"

                with anyio.fail_after(0.5):
                    reload_result = await client.call_tool("hwp_reload", {})
                assert reload_result.structuredContent is not None
                deferred = RuntimeStatus.model_validate(reload_result.structuredContent)
                assert deferred.worker_state == "busy"
                assert deferred.reload_state == "deferred"

                release_mutation.set()
                await call_finished.wait()
                probe = await client.call_tool("hwp_test_probe", {})
                assert probe.isError is False
                final_result = await client.call_tool("hwp_runtime_info", {})
                assert final_result.structuredContent is not None
                final = RuntimeStatus.model_validate(final_result.structuredContent)
                assert final.generation == runtime.generation + 1
                assert final.reload_state == "reloaded"
            tasks.cancel_scope.cancel()


def test_proxy_control_plane_stays_responsive_while_worker_is_busy(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_proxy_control_plane_while_worker_is_busy, tmp_path)

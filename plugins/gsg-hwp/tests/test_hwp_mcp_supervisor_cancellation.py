from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, assert_never

import anyio
import pytest
from anyio.abc import SocketAttribute, SocketStream

from hwp_mcp_supervisor_test_support import SCRIPTS, write_blocking_worker

sys.path.insert(0, str(SCRIPTS))

from hwp_mcp_worker_protocol import HwpWorkerCallTimeout  # noqa: E402
from hwp_mcp_worker_session import HwpWorkerLaunch  # noqa: E402
from hwp_mcp_worker_supervisor import HwpWorkerSupervisor  # noqa: E402


async def _probe_supervisor_after_cancelled_call(
    tmp_path: Path,
    probe: Literal["runtime_info", "task_group"],
) -> None:
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
    call_scope = anyio.CancelScope()

    async def coordinate_worker(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"S"
            mutation_started.set()
            await release_mutation.wait()
            await stream.send(b"R")

    async def abandon_call() -> None:
        with call_scope:
            _ = await supervisor.call_tool("hwp_test_slow_change", {})

    async with listener:
        async with anyio.create_task_group() as listener_tasks:
            _ = listener_tasks.start_soon(listener.serve, coordinate_worker)
            async with supervisor.running():
                _ = listener_tasks.start_soon(abandon_call)
                await mutation_started.wait()
                call_scope.cancel()
                await anyio.sleep(0)
                release_mutation.set()
                match probe:
                    case "runtime_info":
                        status = await supervisor.status()
                        assert status.runtime.worker_process_id > 0
                    case "task_group":
                        tools = await supervisor.list_tools()
                        assert "hwp_runtime_info" in {tool.name for tool in tools.tools}
                    case unreachable:
                        assert_never(unreachable)
            listener_tasks.cancel_scope.cancel()


def test_cancelled_call_allows_later_runtime_info(tmp_path: Path) -> None:
    anyio.run(_probe_supervisor_after_cancelled_call, tmp_path, "runtime_info")


def test_closed_response_stream_does_not_stop_supervisor_task_group(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_supervisor_after_cancelled_call, tmp_path, "task_group")


async def _probe_call_deadline_with_worker_isolation(tmp_path: Path) -> None:
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
        ),
        call_timeout_seconds=0.05,
    )
    mutation_started = anyio.Event()

    async def coordinate_worker(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"S"
            mutation_started.set()
            with pytest.raises((anyio.EndOfStream, anyio.BrokenResourceError)):
                _ = await stream.receive(1)

    async with listener:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(listener.serve, coordinate_worker)
            async with supervisor.running():
                initial_worker_pid = (
                    await supervisor.status()
                ).runtime.worker_process_id
                with (
                    anyio.fail_after(0.5),
                    pytest.raises(HwpWorkerCallTimeout) as timeout,
                ):
                    _ = await supervisor.call_tool("hwp_test_slow_change", {})
                await mutation_started.wait()
                assert timeout.value.started is True
                assert timeout.value.reconcile_required is True
                assert "retry_safe=false" in str(timeout.value)
                with anyio.fail_after(6):
                    while (await supervisor.status()).generation == 1:
                        await anyio.sleep(0.01)
                probe = await supervisor.call_tool("hwp_test_probe", {})
                restarted = await supervisor.status()
                assert probe.result.isError is False
                assert restarted.generation > 1
                assert restarted.runtime.worker_process_id != initial_worker_pid
            tasks.cancel_scope.cancel()


def test_call_deadline_isolates_stuck_worker_before_next_call(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_call_deadline_with_worker_isolation, tmp_path)

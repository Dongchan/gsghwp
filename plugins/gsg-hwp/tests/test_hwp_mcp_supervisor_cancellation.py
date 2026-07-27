from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anyio
from anyio.abc import SocketAttribute, SocketStream
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent
from pydantic import JsonValue, TypeAdapter

from hwp_mcp_supervisor_test_support import (
    SCRIPTS,
    write_blocking_worker,
    write_crashing_worker,
)

sys.path.insert(0, str(SCRIPTS))

from hwp_mcp_hot_reload import ReloadingHwpMCP, build_proxy  # noqa: E402
from hwp_mcp_worker_session import HwpWorkerLaunch  # noqa: E402
from hwp_mcp_worker_supervisor import HwpWorkerSupervisor  # noqa: E402


_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def _result_text(result: CallToolResult) -> str:
    for item in result.content:
        if isinstance(item, TextContent):
            return item.text
    raise AssertionError("tool result did not include text content")


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
                with anyio.fail_after(1):
                    while (await supervisor.status()).worker_state == "busy":
                        await anyio.sleep(0.01)
            listener_tasks.cancel_scope.cancel()


def test_cancelled_call_allows_later_runtime_info(tmp_path: Path) -> None:
    anyio.run(_probe_supervisor_after_cancelled_call, tmp_path, "runtime_info")


def test_closed_response_stream_does_not_stop_supervisor_task_group(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_supervisor_after_cancelled_call, tmp_path, "task_group")


async def _probe_blocked_mutation_allows_unrelated_call(tmp_path: Path) -> None:
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
    mutation_finished = anyio.Event()
    release_mutation = anyio.Event()

    async def coordinate_worker(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"S"
            mutation_started.set()
            await release_mutation.wait()
            await stream.send(b"R")

    async def run_mutation() -> None:
        try:
            _ = await supervisor.call_tool("hwp_test_slow_change", {})
        finally:
            mutation_finished.set()

    async with listener:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(listener.serve, coordinate_worker)
            async with supervisor.running():
                _ = tasks.start_soon(run_mutation)
                await mutation_started.wait()
                try:
                    with anyio.fail_after(0.5):
                        probe = await supervisor.call_tool("hwp_test_probe", {})
                    assert probe.result.isError is False
                finally:
                    release_mutation.set()
                    with anyio.fail_after(1):
                        await mutation_finished.wait()
            tasks.cancel_scope.cancel()


def test_blocked_mutation_does_not_block_unrelated_worker_call(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_blocked_mutation_allows_unrelated_call, tmp_path)


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
    release_mutation = anyio.Event()

    async def coordinate_worker(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"S"
            mutation_started.set()
            await release_mutation.wait()
            await stream.send(b"R")

    async with listener:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(listener.serve, coordinate_worker)
            async with supervisor.running():
                before = await supervisor.status()
                with anyio.fail_after(0.5):
                    timeout = await supervisor.call_tool("hwp_test_slow_change", {})
                await mutation_started.wait()
                payload = timeout.result.structuredContent
                assert payload is not None
                assert "failure_code=worker_timeout" in _result_text(timeout.result)
                assert payload["retry_safe"] is False
                assert payload["reconcile_required"] is True
                probe = await supervisor.call_tool("hwp_test_probe", {})
                assert probe.result.isError is False
                assert (await supervisor.status()).generation == before.generation
                release_mutation.set()
                with anyio.fail_after(1):
                    while (await supervisor.status()).worker_state == "busy":
                        await anyio.sleep(0.01)
            tasks.cancel_scope.cancel()


def test_call_deadline_isolates_stuck_worker_before_next_call(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_call_deadline_with_worker_isolation, tmp_path)


async def _probe_worker_transport_loss_is_structured(tmp_path: Path) -> None:
    worker_script = write_crashing_worker(tmp_path)
    supervisor = HwpWorkerSupervisor(
        HwpWorkerLaunch(
            python_executable=Path(sys.executable),
            worker_script=worker_script,
            watch_paths=(worker_script,),
        ),
        call_timeout_seconds=2,
    )

    async with supervisor.running():
        before = await supervisor.status()
        lost = await supervisor.call_tool("hwp_test_crash", {})
        payload = lost.result.structuredContent
        assert payload is not None
        assert "failure_code=transport_lost" in _result_text(lost.result)
        assert payload["retry_safe"] is False
        with anyio.fail_after(3):
            while (await supervisor.status()).generation == before.generation:
                await anyio.sleep(0.01)
        probe = await supervisor.call_tool("hwp_test_probe", {})
        assert probe.result.isError is False


def test_worker_transport_loss_is_structured_and_outer_session_survives(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_worker_transport_loss_is_structured, tmp_path)


async def _probe_transport_loss_through_actual_mcp_call(tmp_path: Path) -> None:
    worker_script = write_crashing_worker(tmp_path)
    proxy = build_proxy(
        HwpWorkerLaunch(
            python_executable=Path(sys.executable),
            worker_script=worker_script,
            watch_paths=(worker_script,),
        )
    )

    async with create_connected_server_and_client_session(proxy) as client:
        lost = await client.call_tool("hwp_test_crash", {})
        assert lost.isError is False
        payload = lost.structuredContent
        assert payload is not None
        assert payload["status"] == "failed"
        assert "failure_code=transport_lost" in _result_text(lost)
        assert payload["retry_safe"] is False
        with anyio.fail_after(3):
            while True:
                probe = await client.call_tool("hwp_test_probe", {})
                if probe.isError is False:
                    break
                await anyio.sleep(0.01)


def test_transport_loss_structure_reaches_actual_mcp_client(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_transport_loss_through_actual_mcp_call, tmp_path)


async def _probe_worker_timeout_through_actual_mcp_call(tmp_path: Path) -> None:
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

    @asynccontextmanager
    async def lifespan(_: FastMCP[None]) -> AsyncGenerator[None]:
        async with supervisor.running():
            yield

    proxy = ReloadingHwpMCP(supervisor, lifespan)
    mutation_started = anyio.Event()
    release_mutation = anyio.Event()

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
                timeout = await client.call_tool(
                    "hwp_execute",
                    {
                        "tool_name": "hwp_test_slow_change",
                        "arguments": {},
                    },
                )
                await mutation_started.wait()
                assert timeout.isError is False
                payload = timeout.structuredContent
                assert payload is not None
                action_payload = _JSON_OBJECT.validate_python(payload["result"])
                assert action_payload["status"] == "failed"
                assert "failure_code=worker_timeout" in _result_text(timeout)
                assert action_payload["retry_safe"] is False
                assert action_payload["reconcile_required"] is True
                probe = await client.call_tool("hwp_test_probe", {})
                assert probe.isError is False
                release_mutation.set()
                with anyio.fail_after(1):
                    while (await supervisor.status()).worker_state == "busy":
                        await anyio.sleep(0.01)
            tasks.cancel_scope.cancel()


def test_worker_timeout_structure_reaches_actual_mcp_client(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_worker_timeout_through_actual_mcp_call, tmp_path)


async def _probe_unreleased_call_shutdown_is_bounded(tmp_path: Path) -> None:
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
        call_timeout_seconds=60,
    )
    mutation_started = anyio.Event()

    async def coordinate_worker(stream: SocketStream) -> None:
        async with stream:
            assert await stream.receive(1) == b"S"
            mutation_started.set()
            await anyio.sleep_forever()

    async def run_mutation() -> None:
        try:
            _ = await supervisor.call_tool("hwp_test_slow_change", {})
        except anyio.get_cancelled_exc_class():
            return

    async with listener:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(listener.serve, coordinate_worker)
            with anyio.fail_after(3):
                async with supervisor.running():
                    _ = tasks.start_soon(run_mutation)
                    await mutation_started.wait()
            tasks.cancel_scope.cancel()


def test_running_shutdown_cancels_unreleased_call_after_bounded_drain(
    tmp_path: Path,
) -> None:
    anyio.run(_probe_unreleased_call_shutdown_is_bounded, tmp_path)

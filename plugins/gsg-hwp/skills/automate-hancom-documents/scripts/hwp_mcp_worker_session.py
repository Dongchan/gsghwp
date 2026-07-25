from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import anyio
from anyio.streams.memory import MemoryObjectSendStream
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from hwp_mcp_worker_protocol import (
    HwpWorkerProtocolError,
    WorkerCallTool,
    WorkerCycle,
    WorkerReload,
    WorkerRequest,
    WorkerStatus,
    WorkerToolResult,
    send_reply,
)
from hwp_runtime_identity import RuntimeStatus


@dataclass(frozen=True, slots=True)
class HwpWorkerLaunch:
    python_executable: Path
    worker_script: Path
    watch_paths: tuple[Path, ...]
    worker_arguments: tuple[str, ...] = ()

    def resolved(self) -> HwpWorkerLaunch:
        return HwpWorkerLaunch(
            python_executable=self.python_executable.resolve(),
            worker_script=self.worker_script.resolve(),
            watch_paths=tuple(path.resolve() for path in self.watch_paths),
            worker_arguments=self.worker_arguments,
        )


async def request_worker_reload(
    requests: MemoryObjectSendStream[WorkerRequest],
) -> WorkerStatus | None:
    send, receive = anyio.create_memory_object_stream[WorkerStatus](1)
    async with send, receive:
        try:
            requests.send_nowait(WorkerReload(send))
        except anyio.WouldBlock:
            return None
        return await receive.receive()


@asynccontextmanager
async def open_worker_session(
    launch: HwpWorkerLaunch,
) -> AsyncGenerator[ClientSession]:
    parameters = StdioServerParameters(
        command=str(launch.python_executable),
        args=["-B", str(launch.worker_script), *launch.worker_arguments],
        cwd=launch.worker_script.parent,
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            _ = await session.initialize()
            yield session


async def read_worker_status(cycle: WorkerCycle) -> WorkerStatus:
    tools = tuple((await cycle.session.list_tools()).tools)
    result = await cycle.session.call_tool("hwp_runtime_info", {})
    if result.isError or result.structuredContent is None:
        raise HwpWorkerProtocolError(
            "HWP worker did not return structured runtime identity"
        )
    runtime = RuntimeStatus.model_validate(result.structuredContent)
    return WorkerStatus(
        runtime=runtime,
        tools=tools,
        generation=cycle.generation,
        source_hash=cycle.source_hash,
        loaded_source_hash=cycle.loaded_source_hash,
        reloaded=cycle.reloaded,
        worker_state="idle",
        reload_state="reloaded" if cycle.reloaded else "not_requested",
    )


async def respond_to_worker_request(
    request: WorkerRequest,
    cycle: WorkerCycle,
) -> None:
    if isinstance(request, WorkerCallTool):
        result = await cycle.session.call_tool(request.name, request.arguments)
        await send_reply(request.reply, WorkerToolResult(result, cycle.reloaded))
        return
    if isinstance(request, WorkerReload):
        await send_reply(request.reply, await read_worker_status(cycle))
        return
    raise HwpWorkerProtocolError("worker quiescence signal cannot be forwarded")

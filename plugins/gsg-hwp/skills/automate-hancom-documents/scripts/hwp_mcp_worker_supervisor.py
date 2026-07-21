from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never, final

import anyio
from anyio.streams.memory import MemoryObjectSendStream
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, Tool
from pydantic import JsonValue

from hwp_runtime_identity import (
    RuntimeStatus,
    refresh_runtime_source_state,
    runtime_source_state,
)


class HwpWorkerProtocolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HwpWorkerLaunch:
    python_executable: Path
    worker_script: Path
    watch_paths: tuple[Path, ...]
    worker_arguments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkerToolList:
    tools: tuple[Tool, ...]
    reloaded: bool


@dataclass(frozen=True, slots=True)
class WorkerToolResult:
    result: CallToolResult
    reloaded: bool


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    runtime: RuntimeStatus
    tools: tuple[Tool, ...]
    generation: int
    source_hash: str
    loaded_source_hash: str
    reloaded: bool


@dataclass(frozen=True, slots=True)
class _ListTools:
    reply: MemoryObjectSendStream[WorkerToolList]


@dataclass(frozen=True, slots=True)
class _CallTool:
    name: str
    arguments: dict[str, JsonValue]
    reply: MemoryObjectSendStream[WorkerToolResult]


@dataclass(frozen=True, slots=True)
class _ReadStatus:
    reply: MemoryObjectSendStream[WorkerStatus]


@dataclass(frozen=True, slots=True)
class _Reload:
    reply: MemoryObjectSendStream[WorkerStatus]


type _WorkerRequest = _ListTools | _CallTool | _ReadStatus | _Reload


@dataclass(frozen=True, slots=True)
class _WorkerCycle:
    session: ClientSession
    generation: int
    source_hash: str
    loaded_source_hash: str
    reloaded: bool


def _forces_reload(request: _WorkerRequest) -> bool:
    match request:
        case _Reload():
            return True
        case _ListTools() | _CallTool() | _ReadStatus():
            return False
        case unreachable:
            assert_never(unreachable)


@final
class HwpWorkerSupervisor:
    __slots__ = (
        "_launch",
        "_ready",
        "_receive",
        "_send",
    )

    def __init__(self, launch: HwpWorkerLaunch) -> None:
        self._launch = HwpWorkerLaunch(
            python_executable=launch.python_executable.resolve(),
            worker_script=launch.worker_script.resolve(),
            watch_paths=tuple(path.resolve() for path in launch.watch_paths),
            worker_arguments=launch.worker_arguments,
        )
        self._send, self._receive = anyio.create_memory_object_stream[_WorkerRequest](0)
        self._ready = anyio.Event()

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[None]:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(self._serve)
            await self._ready.wait()
            try:
                yield
            finally:
                await self._send.aclose()

    async def list_tools(self) -> WorkerToolList:
        send, receive = anyio.create_memory_object_stream[WorkerToolList](1)
        async with send, receive:
            await self._send.send(_ListTools(send))
            return await receive.receive()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> WorkerToolResult:
        send, receive = anyio.create_memory_object_stream[WorkerToolResult](1)
        async with send, receive:
            await self._send.send(_CallTool(name, arguments, send))
            return await receive.receive()

    async def status(self) -> WorkerStatus:
        send, receive = anyio.create_memory_object_stream[WorkerStatus](1)
        async with send, receive:
            await self._send.send(_ReadStatus(send))
            return await receive.receive()

    async def reload(self) -> WorkerStatus:
        send, receive = anyio.create_memory_object_stream[WorkerStatus](1)
        async with send, receive:
            await self._send.send(_Reload(send))
            return await receive.receive()

    async def _serve(self) -> None:
        pending: _WorkerRequest | None = None
        pending_reloaded = False
        generation = 0
        async with self._receive:
            while True:
                source_state = runtime_source_state(self._launch.watch_paths)
                loaded_source_hash = source_state.content_hash
                parameters = StdioServerParameters(
                    command=str(self._launch.python_executable),
                    args=[
                        "-B",
                        str(self._launch.worker_script),
                        *self._launch.worker_arguments,
                    ],
                    cwd=self._launch.worker_script.parent,
                )
                async with stdio_client(parameters) as streams:
                    async with ClientSession(*streams) as session:
                        _ = await session.initialize()
                        generation += 1
                        self._ready.set()
                        while True:
                            if pending is None:
                                try:
                                    request = await self._receive.receive()
                                except anyio.EndOfStream:
                                    return
                                reloaded = False
                            else:
                                request, pending = pending, None
                                reloaded, pending_reloaded = pending_reloaded, False
                            current_source_state = refresh_runtime_source_state(
                                source_state,
                                self._launch.watch_paths,
                            )
                            current_source_hash = current_source_state.content_hash
                            if not reloaded and (
                                _forces_reload(request)
                                or current_source_hash != loaded_source_hash
                            ):
                                pending = request
                                pending_reloaded = True
                                break
                            source_state = current_source_state
                            await self._respond(
                                request,
                                _WorkerCycle(
                                    session=session,
                                    generation=generation,
                                    source_hash=current_source_hash,
                                    loaded_source_hash=loaded_source_hash,
                                    reloaded=reloaded,
                                ),
                            )

    async def _status(self, cycle: _WorkerCycle) -> WorkerStatus:
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
        )

    async def _respond(
        self,
        request: _WorkerRequest,
        cycle: _WorkerCycle,
    ) -> None:
        match request:
            case _ListTools(reply=reply):
                tools = tuple((await cycle.session.list_tools()).tools)
                await reply.send(WorkerToolList(tools, cycle.reloaded))
            case _CallTool(name=name, arguments=arguments, reply=reply):
                result = await cycle.session.call_tool(name, arguments)
                await reply.send(WorkerToolResult(result, cycle.reloaded))
            case _ReadStatus(reply=reply) | _Reload(reply=reply):
                await reply.send(await self._status(cycle))
            case unreachable:
                assert_never(unreachable)

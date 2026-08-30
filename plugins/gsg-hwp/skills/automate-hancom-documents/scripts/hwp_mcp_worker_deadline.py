from __future__ import annotations

from typing import Final

import anyio
from anyio.streams.memory import MemoryObjectSendStream
from pydantic import JsonValue

from hwp_mcp_registry import ToolEffect
from hwp_mcp_worker_protocol import (
    HwpWorkerCallTimeout,
    HwpWorkerProtocolError,
    WorkerCallState,
    WorkerCallTool,
    WorkerRequest,
    WorkerToolResult,
)


DEFAULT_CALL_TIMEOUT_SECONDS: Final = 240.0


def positive_call_timeout(timeout_seconds: float) -> float:
    if timeout_seconds <= 0:
        raise HwpWorkerProtocolError("worker call timeout must be positive")
    return timeout_seconds


async def call_worker_before_deadline(
    requests: MemoryObjectSendStream[WorkerRequest],
    name: str,
    arguments: dict[str, JsonValue],
    timeout_seconds: float,
    *,
    effect: ToolEffect,
    state: WorkerCallState | None = None,
) -> WorkerToolResult:
    call_state = WorkerCallState() if state is None else state
    dispatched = False
    send, receive = anyio.create_memory_object_stream[WorkerToolResult](1)
    async with send, receive:
        try:
            with anyio.move_on_after(timeout_seconds):
                await requests.send(WorkerCallTool(name, arguments, send, call_state))
                dispatched = True
                return await receive.receive()
        except BaseException:
            call_state.cancelled.set()
            raise
        call_state.cancelled.set()
    raise HwpWorkerCallTimeout(
        name,
        timeout_seconds,
        dispatched=dispatched,
        started=call_state.started.is_set(),
        effect=effect,
    )

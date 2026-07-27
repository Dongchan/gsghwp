from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import anyio
from anyio.streams.memory import MemoryObjectSendStream
from mcp.client.session import ClientSession
from mcp.types import CallToolResult, Tool
from pydantic import JsonValue

from hwp_mcp_registry import ToolEffect
from hwp_runtime_identity import RuntimeStatus


class HwpWorkerProtocolError(RuntimeError):
    pass


class HwpWorkerCallTimeout(HwpWorkerProtocolError, TimeoutError):
    tool_name: str
    timeout_seconds: float
    dispatched: bool
    started: bool
    reconcile_required: bool
    retry_safe: bool
    effect: ToolEffect

    def __init__(
        self,
        tool_name: str,
        timeout_seconds: float,
        *,
        dispatched: bool,
        started: bool,
        effect: ToolEffect,
    ) -> None:
        self.tool_name = tool_name
        self.timeout_seconds = timeout_seconds
        self.dispatched = dispatched
        self.started = started
        self.effect = effect
        self.reconcile_required = started and effect in {"document", "file"}
        self.retry_safe = not started or effect in {"read", "artifact"}
        phase = "running" if started else "queued" if dispatched else "queue_wait"
        isolation = "; worker_isolation_required=true" if started else ""
        recovery = (
            f"; reconcile_required={'true' if self.reconcile_required else 'false'}"
            f"; retry_safe={'true' if self.retry_safe else 'false'}"
        )
        super().__init__(
            "".join(
                (
                    f"HWP worker total deadline exceeded for {tool_name} ",
                    f"after {timeout_seconds:g}s; phase={phase}; effect={effect}",
                    isolation,
                    recovery,
                )
            )
        )


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
    worker_state: Literal["idle", "busy", "unknown"]
    reload_state: Literal["not_requested", "reloaded", "deferred", "unknown"]


@dataclass(slots=True)
class WorkerCallState:
    started: anyio.Event = field(default_factory=anyio.Event)
    cancelled: anyio.Event = field(default_factory=anyio.Event)


@dataclass(frozen=True, slots=True)
class WorkerCallTool:
    name: str
    arguments: dict[str, JsonValue]
    reply: MemoryObjectSendStream[WorkerToolResult]
    state: WorkerCallState = field(default_factory=WorkerCallState)


@dataclass(frozen=True, slots=True)
class WorkerReload:
    reply: MemoryObjectSendStream[WorkerStatus]


@dataclass(frozen=True, slots=True)
class WorkerQuiesced:
    pass


type WorkerRequest = WorkerCallTool | WorkerReload | WorkerQuiesced


@dataclass(frozen=True, slots=True)
class WorkerCycle:
    session: ClientSession
    generation: int
    source_hash: str
    loaded_source_hash: str
    reloaded: bool


def forces_reload(request: WorkerRequest) -> bool:
    return isinstance(request, WorkerReload)


async def send_reply[T](
    reply: MemoryObjectSendStream[T],
    value: T,
) -> None:
    try:
        await reply.send(value)
    except (anyio.ClosedResourceError, anyio.BrokenResourceError):
        return

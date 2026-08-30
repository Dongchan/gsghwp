from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Final, final

import anyio
from anyio.streams.memory import MemoryObjectSendStream

from hwp_mcp_worker_protocol import (
    HwpWorkerProtocolError,
    WorkerCallTool,
    WorkerQuiesced,
    WorkerRequest,
)


_CONCURRENT_TOOLS: Final = frozenset({"hwp_watch_state"})


def runs_concurrently(request: WorkerRequest) -> bool:
    return isinstance(request, WorkerCallTool) and request.name in _CONCURRENT_TOOLS


@dataclass(frozen=True, slots=True)
class WorkerActivitySnapshot:
    active_tool_names: tuple[str, ...]
    busy_elapsed_seconds: float | None
    busy_timeout_seconds: float | None
    busy_timeout_remaining_seconds: float | None


@final
class WorkerActivity:
    __slots__ = ("_active", "_next_id", "_wake")

    def __init__(self, wake: MemoryObjectSendStream[WorkerRequest]) -> None:
        self._active: dict[int, tuple[str, float]] = {}
        self._next_id = 0
        self._wake = wake

    @property
    def busy(self) -> bool:
        return bool(self._active)

    def start(self, tool_name: str) -> int:
        self._next_id += 1
        self._active[self._next_id] = (tool_name, monotonic())
        return self._next_id

    def finish(self, activity_id: int, *, notify: bool = False) -> None:
        if self._active.pop(activity_id, None) is None:
            raise HwpWorkerProtocolError("worker activity counter underflow")
        if not notify or self._active:
            return
        try:
            self._wake.send_nowait(WorkerQuiesced())
        except (
            anyio.WouldBlock,
            anyio.ClosedResourceError,
            anyio.BrokenResourceError,
        ):
            return

    def snapshot(self, timeout_seconds: float) -> WorkerActivitySnapshot:
        if not self._active:
            return WorkerActivitySnapshot((), None, None, None)
        now = monotonic()
        oldest_started_at = min(started_at for _, started_at in self._active.values())
        elapsed = max(0.0, now - oldest_started_at)
        return WorkerActivitySnapshot(
            active_tool_names=tuple(name for name, _ in self._active.values()),
            busy_elapsed_seconds=elapsed,
            busy_timeout_seconds=timeout_seconds,
            busy_timeout_remaining_seconds=max(0.0, timeout_seconds - elapsed),
        )

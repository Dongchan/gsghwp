from __future__ import annotations

from typing import Final, final

import anyio
from anyio.streams.memory import MemoryObjectSendStream

from hwp_mcp_worker_protocol import (
    HwpWorkerProtocolError,
    WorkerCallTool,
    WorkerCycle,
    WorkerQuiesced,
    WorkerRequest,
)
from hwp_mcp_worker_session import respond_to_worker_request


_CONCURRENT_TOOLS: Final = frozenset({"hwp_watch_state"})


def runs_concurrently(request: WorkerRequest) -> bool:
    return isinstance(request, WorkerCallTool) and request.name in _CONCURRENT_TOOLS


@final
class WorkerActivity:
    __slots__ = ("_active", "_wake")

    def __init__(self, wake: MemoryObjectSendStream[WorkerRequest]) -> None:
        self._active = 0
        self._wake = wake

    @property
    def busy(self) -> bool:
        return self._active > 0

    def start(self) -> None:
        self._active += 1

    def finish(self, *, notify: bool = False) -> None:
        if self._active < 1:
            raise HwpWorkerProtocolError("worker activity counter underflow")
        self._active -= 1
        if not notify or self._active > 0:
            return
        try:
            self._wake.send_nowait(WorkerQuiesced())
        except (
            anyio.WouldBlock,
            anyio.ClosedResourceError,
            anyio.BrokenResourceError,
        ):
            return

    async def respond(self, request: WorkerRequest, cycle: WorkerCycle) -> None:
        try:
            await respond_to_worker_request(request, cycle)
        finally:
            self.finish(notify=True)

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import final

import anyio
from pydantic import JsonValue

from hwp_mcp_worker_concurrency import WorkerActivity, runs_concurrently
from hwp_mcp_worker_deadline import (
    DEFAULT_CALL_TIMEOUT_SECONDS,
    call_worker_before_deadline,
    positive_call_timeout,
)
from hwp_mcp_worker_protocol import (
    HwpWorkerCallTimeout,
    HwpWorkerProtocolError,
    WorkerCallTool,
    WorkerCycle,
    WorkerQuiesced,
    WorkerRequest,
    WorkerStatus,
    WorkerToolList,
    WorkerToolResult,
    forces_reload,
)
from hwp_mcp_worker_session import (
    HwpWorkerLaunch,
    open_worker_session,
    read_worker_status,
    request_worker_reload,
    respond_to_worker_request,
)
from hwp_mcp_worker_status import supervisor_status
from hwp_mcp_worker_tool_policy import worker_tool_may_mutate
from hwp_runtime_identity import refresh_runtime_source_state, runtime_source_state


@final
class HwpWorkerSupervisor:
    __slots__ = (
        "_launch",
        "_ready",
        "_receive",
        "_reload_requested",
        "_restarting",
        "_send",
        "_source_state",
        "_status_snapshot",
        "_activity",
        "_call_timeout_seconds",
        "_cycle_scope",
    )

    def __init__(
        self,
        launch: HwpWorkerLaunch,
        *,
        call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> None:
        self._launch = launch.resolved()
        self._send, self._receive = anyio.create_memory_object_stream[WorkerRequest](1)
        self._activity = WorkerActivity(self._send)
        self._call_timeout_seconds = positive_call_timeout(call_timeout_seconds)
        self._ready = anyio.Event()
        self._source_state = runtime_source_state(self._launch.watch_paths)
        self._status_snapshot: WorkerStatus | None = None
        self._reload_requested = False
        self._restarting = True
        self._cycle_scope: anyio.CancelScope | None = None

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
        status = await self.status()
        return WorkerToolList(status.tools, status.reloaded)

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> WorkerToolResult:
        mutation = worker_tool_may_mutate(name, arguments)
        try:
            result = await call_worker_before_deadline(
                self._send,
                name,
                arguments,
                self._call_timeout_seconds,
                mutation=mutation,
            )
        except HwpWorkerCallTimeout as error:
            if error.started:
                self._request_worker_isolation()
            raise
        if "worker_isolation_required=true" in result.result.model_dump_json():
            self._request_worker_isolation()
        return result

    def _request_worker_isolation(self) -> None:
        self._restarting = True
        cycle_scope = self._cycle_scope
        if cycle_scope is not None:
            cycle_scope.cancel()

    async def status(self) -> WorkerStatus:
        snapshot = self._status_snapshot
        if snapshot is None:
            raise HwpWorkerProtocolError("HWP worker status is not available")
        self._source_state = refresh_runtime_source_state(
            self._source_state,
            self._launch.watch_paths,
        )
        return supervisor_status(
            snapshot, self._source_state.content_hash,
            restarting=self._restarting, busy=self._activity.busy,
            reload_requested=self._reload_requested,
        )

    async def reload(self) -> WorkerStatus:
        status = await self.status()
        if status.worker_state == "busy":
            self._reload_requested = True
            return replace(status, reload_state="deferred")
        if status.worker_state == "unknown":
            return replace(status, reload_state="unknown")
        result = await request_worker_reload(self._send)
        if result is not None:
            return result
        self._reload_requested = True
        return replace(
            await self.status(),
            worker_state="busy",
            reload_state="deferred",
        )

    async def _serve(self) -> None:
        pending: WorkerRequest | None = None
        pending_reloaded = False
        generation = 0
        async with self._receive:
            while True:
                source_state = refresh_runtime_source_state(
                    self._source_state,
                    self._launch.watch_paths,
                )
                self._source_state = source_state
                loaded_source_hash = source_state.content_hash
                async with open_worker_session(self._launch) as session:
                    generation += 1
                    cycle = WorkerCycle(
                        session=session,
                        generation=generation,
                        source_hash=source_state.content_hash,
                        loaded_source_hash=loaded_source_hash,
                        reloaded=pending_reloaded,
                    )
                    self._status_snapshot = await read_worker_status(cycle)
                    self._restarting = False
                    self._ready.set()
                    async with anyio.create_task_group() as responses:
                        self._cycle_scope = responses.cancel_scope
                        try:
                            while True:
                                if pending is None:
                                    try:
                                        request = await self._receive.receive()
                                    except anyio.EndOfStream:
                                        return
                                    reloaded, pending_reloaded = pending_reloaded, False
                                else:
                                    request, pending = pending, None
                                    reloaded, pending_reloaded = pending_reloaded, False
                                if (
                                    isinstance(request, WorkerCallTool)
                                    and request.state.cancelled.is_set()
                                ):
                                    continue
                                current_source_state = refresh_runtime_source_state(
                                    source_state,
                                    self._launch.watch_paths,
                                )
                                self._source_state = current_source_state
                                current_source_hash = current_source_state.content_hash
                                if isinstance(request, WorkerQuiesced):
                                    if self._activity.busy:
                                        continue
                                    if (
                                        self._reload_requested
                                        or current_source_hash != loaded_source_hash
                                    ):
                                        self._reload_requested = False
                                        pending_reloaded = True
                                        self._restarting = True
                                        break
                                    continue
                                if not reloaded and (
                                    forces_reload(request)
                                    or current_source_hash != loaded_source_hash
                                    or (
                                        self._reload_requested
                                        and not self._activity.busy
                                    )
                                ):
                                    pending = request
                                    self._reload_requested = False
                                    pending_reloaded = True
                                    self._restarting = True
                                    break
                                source_state = current_source_state
                                cycle_request = WorkerCycle(
                                    session=session,
                                    generation=generation,
                                    source_hash=current_source_hash,
                                    loaded_source_hash=loaded_source_hash,
                                    reloaded=reloaded,
                                )
                                if isinstance(request, WorkerCallTool):
                                    request.state.started.set()
                                self._activity.start()
                                if runs_concurrently(request):
                                    _ = responses.start_soon(
                                        self._activity.respond,
                                        request,
                                        cycle_request,
                                    )
                                    continue
                                try:
                                    await respond_to_worker_request(request, cycle_request)
                                finally:
                                    self._activity.finish()
                                if self._reload_requested and not self._activity.busy:
                                    self._reload_requested = False
                                    pending_reloaded = True
                                    self._restarting = True
                                    break
                        finally:
                            self._cycle_scope = None

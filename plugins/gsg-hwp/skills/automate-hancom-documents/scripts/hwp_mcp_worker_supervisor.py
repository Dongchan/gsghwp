from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from time import monotonic
from typing import Final, final

import anyio
from mcp.types import CallToolResult, TextContent
from pydantic import JsonValue

from hwp_mcp_worker_concurrency import WorkerActivity, WorkerActivitySnapshot
from hwp_mcp_worker_deadline import (
    DEFAULT_CALL_TIMEOUT_SECONDS,
    call_worker_before_deadline,
    positive_call_timeout,
)
from hwp_mcp_worker_protocol import (
    HwpWorkerCallTimeout,
    HwpWorkerProtocolError,
    WorkerCallState,
    WorkerCallTool,
    WorkerCycle,
    WorkerQuiesced,
    WorkerRequest,
    WorkerStatus,
    WorkerToolList,
    WorkerToolResult,
    forces_reload,
    send_reply,
)
from hwp_mcp_worker_session import (
    HwpWorkerLaunch,
    HwpWorkerTransportLost,
    open_worker_session,
    read_worker_status,
    request_worker_reload,
    respond_to_worker_request,
)
from hwp_mcp_worker_status import supervisor_status
from hwp_mcp_worker_tool_policy import worker_tool_effect
from hwp_mcp_registry import ToolEffect
from hwp_runtime_identity import refresh_runtime_source_state, runtime_source_state


_WORKER_SHUTDOWN_DRAIN_SECONDS: Final = 0.25


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
        "_stopped",
        "_status_snapshot",
        "_activity",
        "_call_timeout_seconds",
        "_cycle_ready",
        "_cycle_scope",
        "_transport_lost_generation",
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
        self._stopped = anyio.Event()
        self._source_state = runtime_source_state(self._launch.watch_paths)
        self._status_snapshot: WorkerStatus | None = None
        self._reload_requested = False
        self._restarting = True
        self._cycle_ready = anyio.Event()
        self._cycle_scope: anyio.CancelScope | None = None
        self._transport_lost_generation: int | None = None

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[None]:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(self._serve_until_stopped)
            await self._ready.wait()
            try:
                yield
            finally:
                cycle_scope = self._cycle_scope
                await self._send.aclose()
                with anyio.move_on_after(_WORKER_SHUTDOWN_DRAIN_SECONDS):
                    await self._stopped.wait()
                if not self._stopped.is_set():
                    self._restarting = True
                    if cycle_scope is not None:
                        cycle_scope.cancel()
                    else:
                        self._request_worker_isolation()
                    tasks.cancel_scope.cancel()

    async def _serve_until_stopped(self) -> None:
        try:
            await self._serve()
        finally:
            self._stopped.set()

    async def list_tools(self) -> WorkerToolList:
        status = await self.status()
        return WorkerToolList(status.tools, status.reloaded)

    @staticmethod
    def _request_id(arguments: dict[str, JsonValue]) -> str | None:
        value = arguments.get("operation_id")
        if isinstance(value, str) and value:
            return value
        forwarded = arguments.get("arguments")
        if isinstance(forwarded, dict):
            nested = forwarded.get("operation_id")
            if isinstance(nested, str) and nested:
                return nested
        return None

    @staticmethod
    def _is_save_call(name: str, arguments: dict[str, JsonValue]) -> bool:
        if name in {"hwp_save", "hwp_save_reopen_verify"}:
            return True
        return name == "hwp_execute" and arguments.get("tool_name") in {
            "hwp_save",
            "hwp_save_reopen_verify",
        }

    @staticmethod
    def _is_disconnect_call(name: str, arguments: dict[str, JsonValue]) -> bool:
        return name == "hwp_disconnect" or (
            name == "hwp_execute" and arguments.get("tool_name") == "hwp_disconnect"
        )

    def _structured_failure(
        self,
        *,
        failure_code: str,
        name: str,
        arguments: dict[str, JsonValue],
        effect: ToolEffect,
        started: bool,
        detail: str,
        retry_safe_override: bool | None = None,
    ) -> WorkerToolResult:
        snapshot = self._status_snapshot
        if snapshot is None:
            raise HwpWorkerProtocolError("HWP worker status is not available")
        reconcile_required = started and effect in {"document", "file"}
        # HwpWorkerCallTimeout 과 같은 식이다(hwp_mcp_worker_protocol.py:44).
        # 시작되지 않은 호출은 문서를 건드릴 기회가 없었으므로 무엇을
        # 되짚을 것도, 다시 부르는 것을 막을 이유도 없다. 여기가 `and`인
        # 동안 started=False + effect="document" 에서 두 식이 갈렸고 —
        # worker_tool_effect 는 미등록 이름을 전부 "document" 로 분류하므로
        # hwp_execute 경유 호출 대부분이 그 조합이다 — 디스패치조차 되지
        # 않은 호출이 retry_safe=false 로 나갔다. reconcile_required=false 를
        # 같은 응답에 달고서.
        retry_safe = (
            not started or effect in {"read", "artifact"}
            if retry_safe_override is None
            else retry_safe_override
        )
        message = (
            f"failure_code={failure_code}; tool={name}; mutation_started="
            f"{'true' if started else 'false'}; retry_safe="
            f"{'true' if retry_safe else 'false'}; reconcile_required="
            f"{'true' if reconcile_required else 'false'}; {detail}"
        )
        if self._is_save_call(name, arguments):
            message = (
                f"{message}; save_state=uncertain; save_terminal=true"
                "; save_close_blocked=true"
            )
        payload: dict[str, JsonValue] = {
            "status": "failed",
            "message": message,
            "request_id": self._request_id(arguments),
            "idempotency_status": "failed",
            "runtime": snapshot.runtime.model_dump(mode="json"),
            "verified": False,
            "modified": started and effect in {"document", "file"},
            "required_inputs": [],
            "target_candidates": [],
            "format_candidates": [],
            "recipe_id": None,
            "commands_executed": 0,
            "updated_addresses": [],
            "created_target_ids": [],
            "affected_pages": [],
            "state_token": None,
            "affected_target_ids": [],
            "selected_target_id": None,
            "input_guidance": [],
            "retry_operation_id": None,
            "retry_safe": retry_safe,
            "reconcile_required": reconcile_required,
            "save_evidence": None,
        }
        structured_payload: dict[str, JsonValue] = (
            {"result": payload} if name == "hwp_execute" else payload
        )
        result = CallToolResult(
            content=[TextContent(type="text", text=message)],
            structuredContent=structured_payload,
        )
        failure_tool = next(
            (tool for tool in snapshot.tools if tool.name == name),
            None,
        )
        return WorkerToolResult(
            result,
            False,
            supervisor_failure_tool=failure_tool,
        )

    async def call_tool(
        self, name: str, arguments: dict[str, JsonValue]
    ) -> WorkerToolResult:
        call_started_at = monotonic()
        if self._is_disconnect_call(name, arguments) and (
            self._activity.busy or self._restarting
        ):
            self._request_worker_isolation()
            message = (
                "failure_code=busy_disconnect_recovery; "
                "worker_isolation_requested=true; "
                "connection_reference_release=pending_worker_exit; "
                "document_close_attempted=false; current_page=unknown; "
                "modified=unknown; retry_safe=true; user_action=hwp_runtime_info에서 "
                "worker_state=idle을 확인한 뒤 document selector로 다시 연결하세요"
            )
            return WorkerToolResult(
                CallToolResult(
                    isError=True,
                    content=[TextContent(type="text", text=message)],
                ),
                False,
            )
        effect = worker_tool_effect(name, arguments)
        if self._restarting:
            cycle_ready = self._cycle_ready
            remaining = self._call_timeout_seconds - (monotonic() - call_started_at)
            with anyio.move_on_after(max(0.0, remaining)) as restart_deadline:
                await cycle_ready.wait()
            if restart_deadline.cancel_called:
                return self._structured_failure(
                    failure_code="worker_restart_timeout",
                    name=name,
                    arguments=arguments,
                    effect=effect,
                    started=False,
                    detail=(
                        f"deadline_seconds={self._call_timeout_seconds:g}; "
                        "phase=worker_restart_wait"
                    ),
                )
        remaining = self._call_timeout_seconds - (monotonic() - call_started_at)
        if remaining <= 0:
            return self._structured_failure(
                failure_code="worker_timeout",
                name=name,
                arguments=arguments,
                effect=effect,
                started=False,
                detail=(
                    f"deadline_seconds={self._call_timeout_seconds:g}; "
                    "phase=predispatch"
                ),
            )
        state = WorkerCallState()
        try:
            result = await call_worker_before_deadline(
                self._send,
                name,
                arguments,
                remaining,
                effect=effect,
                state=state,
            )
        except HwpWorkerCallTimeout as error:
            if error.started:
                self._request_worker_isolation()
            return self._structured_failure(
                failure_code="worker_timeout",
                name=name,
                arguments=arguments,
                effect=effect,
                started=error.started,
                detail=f"deadline_seconds={self._call_timeout_seconds:g}",
                # The timeout already decided this, and its rule is the wider
                # one: a read or artifact call that ran and was cut off left
                # nothing behind to reconcile, so it stays retry-safe.
                retry_safe_override=error.retry_safe,
            )
        except BaseException:
            if state.started.is_set():
                self._request_worker_isolation()
            raise
        return result

    def _request_worker_isolation(self) -> None:
        self._begin_worker_restart()
        cycle_scope = self._cycle_scope
        if cycle_scope is not None:
            cycle_scope.cancel()

    def _begin_worker_restart(self) -> None:
        if not self._restarting:
            self._cycle_ready = anyio.Event()
        self._restarting = True

    def _cycle_transport_lost(self, generation: int) -> bool:
        return self._transport_lost_generation == generation

    async def _respond_worker_call(
        self,
        request: WorkerCallTool,
        cycle: WorkerCycle,
        activity_id: int,
    ) -> None:
        try:
            await respond_to_worker_request(request, cycle)
        except HwpWorkerTransportLost as error:
            self._begin_worker_restart()
            self._transport_lost_generation = cycle.generation
            failure = self._structured_failure(
                failure_code="transport_lost",
                name=request.name,
                arguments=request.arguments,
                effect=worker_tool_effect(request.name, request.arguments),
                started=request.state.started.is_set(),
                detail=str(error),
                retry_safe_override=False,
            )
            with anyio.CancelScope(shield=True):
                await send_reply(request.reply, failure)
        finally:
            self._activity.finish(activity_id, notify=True)
            if (
                self._transport_lost_generation == cycle.generation
                and not self._activity.busy
            ):
                self._request_worker_isolation()

    async def status(self) -> WorkerStatus:
        snapshot = self._status_snapshot
        if snapshot is None:
            raise HwpWorkerProtocolError("HWP worker status is not available")
        self._source_state = refresh_runtime_source_state(
            self._source_state,
            self._launch.watch_paths,
        )
        return supervisor_status(
            snapshot,
            self._source_state.content_hash,
            restarting=self._restarting,
            busy=self._activity.busy,
            reload_requested=self._reload_requested,
        )

    def activity_snapshot(self) -> WorkerActivitySnapshot:
        return self._activity.snapshot(self._call_timeout_seconds)

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
                with anyio.CancelScope() as cycle_scope:
                    self._cycle_scope = cycle_scope
                    try:
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
                            self._transport_lost_generation = None
                            self._cycle_ready.set()
                            self._ready.set()
                            async with anyio.create_task_group() as responses:
                                while True:
                                    if pending is None:
                                        try:
                                            request = await self._receive.receive()
                                        except anyio.EndOfStream:
                                            return
                                        reloaded, pending_reloaded = (
                                            pending_reloaded,
                                            False,
                                        )
                                    else:
                                        request, pending = pending, None
                                        reloaded, pending_reloaded = (
                                            pending_reloaded,
                                            False,
                                        )
                                    if (
                                        isinstance(request, WorkerCallTool)
                                        and request.state.cancelled.is_set()
                                    ):
                                        continue
                                    if isinstance(
                                        request, WorkerCallTool
                                    ) and self._cycle_transport_lost(generation):
                                        failure = self._structured_failure(
                                            failure_code="transport_lost",
                                            name=request.name,
                                            arguments=request.arguments,
                                            effect=worker_tool_effect(
                                                request.name,
                                                request.arguments,
                                            ),
                                            started=False,
                                            detail=(
                                                "worker cycle transport is restarting"
                                            ),
                                            retry_safe_override=False,
                                        )
                                        await send_reply(request.reply, failure)
                                        continue
                                    current_source_state = refresh_runtime_source_state(
                                        source_state,
                                        self._launch.watch_paths,
                                    )
                                    self._source_state = current_source_state
                                    current_source_hash = (
                                        current_source_state.content_hash
                                    )
                                    if isinstance(request, WorkerQuiesced):
                                        if self._activity.busy:
                                            continue
                                        if (
                                            self._reload_requested
                                            or current_source_hash != loaded_source_hash
                                        ):
                                            self._reload_requested = False
                                            pending_reloaded = True
                                            self._begin_worker_restart()
                                            break
                                        continue
                                    reload_needed = not reloaded and (
                                        forces_reload(request)
                                        or current_source_hash != loaded_source_hash
                                        or (
                                            self._reload_requested
                                            and not self._activity.busy
                                        )
                                    )
                                    if reload_needed and self._activity.busy:
                                        self._reload_requested = True
                                    elif reload_needed:
                                        pending = request
                                        self._reload_requested = False
                                        pending_reloaded = True
                                        self._begin_worker_restart()
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
                                        activity_id = self._activity.start(request.name)
                                        _ = responses.start_soon(
                                            self._respond_worker_call,
                                            request,
                                            cycle_request,
                                            activity_id,
                                        )
                                        continue
                                    activity_id = self._activity.start(
                                        type(request).__name__
                                    )
                                    try:
                                        await respond_to_worker_request(
                                            request, cycle_request
                                        )
                                    finally:
                                        self._activity.finish(activity_id)
                                    if (
                                        self._reload_requested
                                        and not self._activity.busy
                                    ):
                                        self._reload_requested = False
                                        pending_reloaded = True
                                        self._begin_worker_restart()
                                        break
                    finally:
                        if self._cycle_scope is cycle_scope:
                            self._cycle_scope = None

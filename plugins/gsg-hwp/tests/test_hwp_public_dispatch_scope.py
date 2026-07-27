from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from gc import collect
from pathlib import Path
from threading import Event, Lock, Timer
from time import monotonic
from typing import cast, final, override
from unittest.mock import patch
from weakref import ReferenceType, ref

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_native_batch import (  # noqa: E402
    _batch_for_window,
    _native_dispatch_state,
    _Win32Client,
    clear_native_document_route,
    native_dispatch_cache_metrics,
    reset_native_dispatch_cache_metrics,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp_dispatch import CleanupStatus, McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation_executor import (  # noqa: E402
    HwpOperationExecutor,
    _observe_best_effort_cleanup,
)
from hwp_mcp_registration import _public_tool_handler  # noqa: E402


@final
class _Controller:
    def restore_activation(self) -> None:
        return

    def invalidate_process_loss(self) -> None:
        return

    def release_idle_references(self) -> None:
        return

    def close(self) -> None:
        return


@final
class _IdleTrackingController:
    def __init__(
        self,
        *,
        release_started: Event | None = None,
        allow_release: Event | None = None,
        fail_release: bool = False,
    ) -> None:
        self._allow_release = allow_release
        self._fail_release = fail_release
        self._release_started = release_started
        self.idle_release_calls = 0

    def restore_activation(self) -> None:
        return

    def invalidate_process_loss(self) -> None:
        return

    def release_idle_references(self) -> None:
        self.idle_release_calls += 1
        if self._release_started is not None:
            self._release_started.set()
        if self._allow_release is not None:
            assert self._allow_release.wait(2)
        if self._fail_release:
            raise RuntimeError("idle cleanup failed")

    def close(self) -> None:
        return


@final
class _CompletesWhenCallbackRegistered(Future[None]):
    def __init__(self) -> None:
        super().__init__()
        self.callback_registrations = 0

    @override
    def add_done_callback(
        self,
        fn: Callable[[Future[None]], object],
    ) -> None:
        self.callback_registrations += 1
        super().add_done_callback(fn)
        if not self.done():
            self.set_result(None)


@final
class _ControlledCleanupClock:
    def __init__(self) -> None:
        self._lock = Lock()
        self._now = 0.0
        self.allow_retry = Event()
        self.retry_waiting = Event()
        self.wait_seconds: list[float] = []

    def now(self) -> float:
        with self._lock:
            return self._now

    def wait(self, event: Event, timeout_seconds: float) -> bool:
        self.retry_waiting.set()
        assert self.allow_retry.wait(2)
        with self._lock:
            self.wait_seconds.append(timeout_seconds)
            self._now += timeout_seconds
        return event.is_set()


@final
class _Moniker:
    def __init__(self, name: str) -> None:
        self.name = name

    def GetDisplayName(self, context: object, moniker: object) -> str:
        _ = context, moniker
        return self.name


@final
class _Source:
    def QueryInterface(self, interface_id: object) -> object:
        _ = interface_id
        return self


@final
class _Rot:
    def __init__(self, name: str) -> None:
        self.moniker = _Moniker(name)
        self.source = _Source()

    def EnumRunning(self) -> tuple[_Moniker]:
        return (self.moniker,)

    def GetObject(self, moniker: object) -> _Source:
        assert moniker is self.moniker
        return self.source


@final
class _PythonCom:
    IID_IDispatch = object()

    def __init__(self, rot: _Rot) -> None:
        self.rot = rot

    def CoInitialize(self) -> None:
        return

    def CoUninitialize(self) -> None:
        return

    def CreateBindCtx(self, reserved: int) -> object:
        _ = reserved
        return object()

    def GetRunningObjectTable(self) -> _Rot:
        return self.rot


@final
class _Process:
    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]:
        _ = window_handle
        return 7, 1234


@final
class _Batch:
    ProtocolVersion = 12
    TargetDocumentID = 0


@final
class _EphemeralBatchClient:
    def __init__(self) -> None:
        self.dispatch_calls = 0
        self.dispatch_refs: list[ReferenceType[_Batch]] = []

    def Dispatch(self, source: object) -> _Batch:
        _ = source
        self.dispatch_calls += 1
        batch = _Batch()
        self.dispatch_refs.append(ref(batch))
        return batch


def _bridge() -> HancomBridge:
    return HancomBridge(
        cast(LiveHwpController, cast(object, _Controller())),
        call_timeout_seconds=1,
    )


def _lane_dispatch_state(bridge: HancomBridge) -> tuple[int, int]:
    lane = bridge._process_lanes[0]
    assert lane.acquire(timeout=1)
    future = lane.submit(
        lambda: (
            _native_dispatch_state.operation_depth,
            len(_native_dispatch_state.operation_entries),
        )
    )
    future.add_done_callback(lambda _: lane.release())
    return future.result(timeout=1)


def _native_stage(
    client: _EphemeralBatchClient,
    depths: list[int],
) -> int:
    depths.append(_native_dispatch_state.operation_depth)
    batch = _batch_for_window(
        5678,
        12,
        _PythonCom(_Rot("!HancomLiveBatch.1234.5678")),
        cast(_Win32Client, cast(object, client)),
        _Process(),
    )
    assert batch is not None
    return int(batch.ProtocolVersion)


def _call_native_stage(
    bridge: HancomBridge,
    client: _EphemeralBatchClient,
    depths: list[int],
) -> int:
    return bridge._call(
        lambda: _native_stage(client, depths),
        process_id=0,
    )


def _reset_native_dispatch_measurement() -> None:
    clear_native_document_route(5678)
    reset_native_dispatch_cache_metrics()


@pytest.mark.parametrize("grace_seconds", (0.05, 0.1))
def test_cleanup_observer_uses_one_high_resolution_deadline(
    grace_seconds: float,
) -> None:
    future = Future[None]()
    started_ns = 7_000_000_000
    observation_cost_ns = 250
    clock_values = iter((started_ns, started_ns + observation_cost_ns))
    waits: list[tuple[tuple[Future[None], ...], float]] = []

    async def record_wait(
        futures: tuple[Future[None], ...],
        timeout_seconds: float,
    ) -> None:
        waits.append((futures, timeout_seconds))

    async def exercise() -> None:
        await _observe_best_effort_cleanup(
            (future,),
            grace_seconds=grace_seconds,
            clock_ns=lambda: next(clock_values),
            wait_for_completion=record_wait,
        )

    anyio.run(exercise)
    _ = future.cancel()

    assert len(waits) == 1
    assert waits[0][0] == (future,)
    expected_wait = grace_seconds - observation_cost_ns / 1_000_000_000
    assert abs(waits[0][1] - expected_wait) < 1e-12


def test_cleanup_observer_returns_at_deadline_without_polling() -> None:
    future = Future[None]()
    wait_calls = 0
    clock_values = iter((10_000_000_000, 10_050_000_000))

    async def unexpected_wait(
        _futures: tuple[Future[None], ...],
        _timeout_seconds: float,
    ) -> None:
        nonlocal wait_calls
        wait_calls += 1

    async def exercise() -> None:
        await _observe_best_effort_cleanup(
            (future,),
            clock_ns=lambda: next(clock_values),
            wait_for_completion=unexpected_wait,
        )

    anyio.run(exercise)
    _ = future.cancel()

    assert wait_calls == 0


def test_cleanup_observer_wakes_from_future_completion_callback() -> None:
    future = _CompletesWhenCallbackRegistered()

    async def exercise() -> None:
        await _observe_best_effort_cleanup(
            (future,),
            clock_ns=lambda: 0,
        )

    anyio.run(exercise)

    assert future.done()
    assert future.callback_registrations == 1


def test_public_tool_reuses_dispatch_across_two_bridge_stages() -> None:
    _reset_native_dispatch_measurement()
    client = _EphemeralBatchClient()
    depths: list[int] = []

    async def exercise() -> tuple[int, int, bool]:
        bridge = _bridge()
        dispatcher = McpThreadDispatcher(operation_workers=2)
        executor = HwpOperationExecutor(bridge, dispatcher, None)

        async def handler() -> None:
            _ = await dispatcher.run(_call_native_stage, bridge, client, depths)
            _ = collect()
            _ = await dispatcher.run(_call_native_stage, bridge, client, depths)

        wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(executor, "hwp_fill_table", handler),
        )
        try:
            _ = await wrapped()
            _ = collect()
            reference_alive_at_return = client.dispatch_refs[0]() is not None
            final_depth, strong_entries = await dispatcher.run(
                _lane_dispatch_state,
                bridge,
            )
            return final_depth, strong_entries, reference_alive_at_return
        finally:
            await dispatcher.close(bridge.close)

    with (
        patch("hwp_live_process_lane._initialize_sta"),
        patch("hwp_live_process_lane._uninitialize_sta"),
    ):
        final_depth, strong_entries, reference_alive_at_return = anyio.run(exercise)

    _ = collect()
    metrics = native_dispatch_cache_metrics()
    assert (metrics.cache_hits, metrics.cache_misses, metrics.rot_scans) == (1, 1, 1)
    assert client.dispatch_calls == 1
    assert depths == [2, 2]
    assert final_depth == 0
    assert strong_entries == 0
    assert not reference_alive_at_return
    assert len(client.dispatch_refs) == 1
    assert client.dispatch_refs[0]() is None


def test_public_tool_exception_releases_dispatch_strong_reference() -> None:
    _reset_native_dispatch_measurement()
    client = _EphemeralBatchClient()
    depths: list[int] = []

    async def exercise() -> tuple[int, int, bool]:
        bridge = _bridge()
        dispatcher = McpThreadDispatcher(operation_workers=2)
        executor = HwpOperationExecutor(bridge, dispatcher, None)

        async def handler() -> None:
            _ = await dispatcher.run(_call_native_stage, bridge, client, depths)
            raise RuntimeError("public handler failed")

        wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(executor, "hwp_fill_table", handler),
        )
        try:
            with pytest.raises(RuntimeError, match="public handler failed"):
                _ = await wrapped()
            _ = collect()
            reference_alive_at_return = client.dispatch_refs[0]() is not None
            final_depth, strong_entries = await dispatcher.run(
                _lane_dispatch_state,
                bridge,
            )
            return final_depth, strong_entries, reference_alive_at_return
        finally:
            await dispatcher.close(bridge.close)

    with (
        patch("hwp_live_process_lane._initialize_sta"),
        patch("hwp_live_process_lane._uninitialize_sta"),
    ):
        final_depth, strong_entries, reference_alive_at_return = anyio.run(exercise)

    _ = collect()
    assert depths == [2]
    assert final_depth == 0
    assert strong_entries == 0
    assert not reference_alive_at_return
    assert len(client.dispatch_refs) == 1
    assert client.dispatch_refs[0]() is None


def test_public_tool_cancellation_releases_dispatch_strong_reference() -> None:
    _reset_native_dispatch_measurement()
    client = _EphemeralBatchClient()
    depths: list[int] = []

    async def exercise() -> tuple[int, int, bool]:
        bridge = _bridge()
        dispatcher = McpThreadDispatcher(operation_workers=2)
        executor = HwpOperationExecutor(bridge, dispatcher, None)
        cancel_scope: anyio.CancelScope

        async def handler() -> None:
            _ = await dispatcher.run(_call_native_stage, bridge, client, depths)
            cancel_scope.cancel()
            await anyio.sleep_forever()

        wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(executor, "hwp_fill_table", handler),
        )
        try:
            with anyio.CancelScope() as cancel_scope:
                _ = await wrapped()
            _ = collect()
            reference_alive_at_return = client.dispatch_refs[0]() is not None
            final_depth, strong_entries = await dispatcher.run(
                _lane_dispatch_state,
                bridge,
            )
            return final_depth, strong_entries, reference_alive_at_return
        finally:
            await dispatcher.close(bridge.close)

    with (
        patch("hwp_live_process_lane._initialize_sta"),
        patch("hwp_live_process_lane._uninitialize_sta"),
    ):
        final_depth, strong_entries, reference_alive_at_return = anyio.run(exercise)

    _ = collect()
    assert depths == [2]
    assert final_depth == 0
    assert strong_entries == 0
    assert not reference_alive_at_return
    assert len(client.dispatch_refs) == 1
    assert client.dispatch_refs[0]() is None


def test_stateless_cleanup_does_not_wait_for_unrelated_pid_lane() -> None:
    controller_a = _IdleTrackingController()
    controller_b = _IdleTrackingController()
    lane_a_started = Event()
    allow_lane_a_return = Event()

    def block_lane_a() -> None:
        lane_a_started.set()
        assert allow_lane_a_return.wait(2)

    with (
        patch("hwp_live_process_lane._initialize_sta"),
        patch("hwp_live_process_lane._uninitialize_sta"),
        patch("hwp_live_bridge.open_process_exit_watch", return_value=None),
    ):
        bridge = _bridge()
        bridge._controllers = {
            101: cast(LiveHwpController, cast(object, controller_a)),
            202: cast(LiveHwpController, cast(object, controller_b)),
        }
        dispatcher = McpThreadDispatcher(operation_workers=2)
        executor = HwpOperationExecutor(bridge, dispatcher, None)

        async def handler() -> None:
            _ = await dispatcher.run(
                bridge._call,
                lambda: None,
                process_id=202,
            )

        wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(executor, "hwp_inspect_page_fast", handler),
        )

        async def invoke() -> float:
            started_at = monotonic()
            _ = await wrapped()
            return monotonic() - started_at

        with ThreadPoolExecutor(max_workers=1) as calls:
            blocked = calls.submit(
                bridge._call,
                block_lane_a,
                process_id=101,
            )
            assert lane_a_started.wait(1)
            fallback_release = Timer(0.5, allow_lane_a_return.set)
            fallback_release.start()
            try:
                elapsed_seconds = anyio.run(invoke)
            finally:
                allow_lane_a_return.set()
                fallback_release.cancel()
                blocked.result(timeout=2)
                anyio.run(dispatcher.close, bridge.close)

    assert elapsed_seconds < 0.25
    assert controller_a.idle_release_calls == 0
    assert controller_b.idle_release_calls == 1


def test_stateless_cleanup_does_not_wait_for_slow_target_cleanup() -> None:
    cleanup_started = Event()
    allow_cleanup_return = Event()
    controller = _IdleTrackingController(
        release_started=cleanup_started,
        allow_release=allow_cleanup_return,
    )
    next_controller = _IdleTrackingController()

    with (
        patch("hwp_live_process_lane._initialize_sta"),
        patch("hwp_live_process_lane._uninitialize_sta"),
        patch("hwp_live_bridge.open_process_exit_watch", return_value=None),
    ):
        bridge = _bridge()
        bridge._controllers = {
            202: cast(LiveHwpController, cast(object, controller)),
            303: cast(LiveHwpController, cast(object, next_controller)),
        }
        dispatcher = McpThreadDispatcher(operation_workers=1)
        executor = HwpOperationExecutor(bridge, dispatcher, None)

        async def first_handler() -> str:
            _ = await dispatcher.run(
                bridge._call,
                lambda: None,
                process_id=202,
            )
            return "first-inspection-complete"

        async def next_handler() -> str:
            _ = await dispatcher.run(
                bridge._call,
                lambda: None,
                process_id=303,
            )
            return "next-inspection-complete"

        first_wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(
                executor,
                "hwp_inspect_page_fast",
                first_handler,
            ),
        )
        next_wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(
                executor,
                "hwp_inspect_page_fast",
                next_handler,
            ),
        )

        async def invoke() -> tuple[object, object, float]:
            started_at = monotonic()
            first_result = await first_wrapped()
            next_result = await next_wrapped()
            return first_result, next_result, monotonic() - started_at

        fallback_release = Timer(0.5, allow_cleanup_return.set)
        fallback_release.start()
        try:
            first_result, next_result, elapsed_seconds = anyio.run(invoke)
        finally:
            allow_cleanup_return.set()
            fallback_release.cancel()
            anyio.run(dispatcher.close, bridge.close)

    assert first_result == "first-inspection-complete"
    assert next_result == "next-inspection-complete"
    assert cleanup_started.is_set()
    assert elapsed_seconds < 0.25
    assert controller.idle_release_calls == 1
    assert next_controller.idle_release_calls == 1


def test_stateless_cleanup_failure_does_not_replace_public_result() -> None:
    controller = _IdleTrackingController(fail_release=True)

    with (
        patch("hwp_live_process_lane._initialize_sta"),
        patch("hwp_live_process_lane._uninitialize_sta"),
        patch("hwp_live_bridge.open_process_exit_watch", return_value=None),
    ):
        bridge = _bridge()
        bridge._controllers = {
            202: cast(LiveHwpController, cast(object, controller)),
        }
        dispatcher = McpThreadDispatcher(operation_workers=2)
        executor = HwpOperationExecutor(bridge, dispatcher, None)

        async def handler() -> str:
            _ = await dispatcher.run(
                bridge._call,
                lambda: None,
                process_id=202,
            )
            return "inspection-complete"

        wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(executor, "hwp_inspect_page_fast", handler),
        )

        async def invoke() -> object:
            try:
                return await wrapped()
            finally:
                await dispatcher.close(bridge.close)

        result = anyio.run(invoke)

    assert result == "inspection-complete"
    assert controller.idle_release_calls == 1


def test_same_pid_followup_supersedes_pending_idle_cleanup() -> None:
    cleanup_started = Event()
    allow_cleanup_return = Event()
    second_handler_entered = Event()
    controller = _IdleTrackingController(
        release_started=cleanup_started,
        allow_release=allow_cleanup_return,
    )

    with (
        patch("hwp_live_process_lane._initialize_sta"),
        patch("hwp_live_process_lane._uninitialize_sta"),
        patch("hwp_live_bridge.open_process_exit_watch", return_value=None),
    ):
        bridge = _bridge()
        bridge._controllers = {
            202: cast(LiveHwpController, cast(object, controller)),
        }
        dispatcher = McpThreadDispatcher(
            operation_workers=1,
            cleanup_quiet_seconds=10,
        )
        executor = HwpOperationExecutor(bridge, dispatcher, None)

        async def first_handler() -> str:
            _ = await dispatcher.run(
                bridge._call,
                lambda: None,
                process_id=202,
            )
            return "first-inspection-complete"

        async def second_handler() -> str:
            def enter_handler() -> None:
                second_handler_entered.set()

            _ = await dispatcher.run(
                bridge._call,
                enter_handler,
                process_id=202,
            )
            return "second-inspection-complete"

        first_wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(
                executor,
                "hwp_inspect_page_fast",
                first_handler,
            ),
        )
        second_wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(
                executor,
                "hwp_inspect_page_fast",
                second_handler,
            ),
        )

        async def invoke() -> tuple[object, object, bool, CleanupStatus]:
            try:
                first_result = await first_wrapped()
                second_result = await second_wrapped()
                cleanup_started_before_second_return = cleanup_started.is_set()
                status = dispatcher.cleanup_status()
                return (
                    first_result,
                    second_result,
                    cleanup_started_before_second_return,
                    status,
                )
            finally:
                allow_cleanup_return.set()
                await dispatcher.close(bridge.close)

        fallback_release = Timer(1, allow_cleanup_return.set)
        fallback_release.start()
        try:
            (
                first_result,
                second_result,
                cleanup_started_before_second_return,
                status,
            ) = anyio.run(invoke)
        finally:
            allow_cleanup_return.set()
            fallback_release.cancel()

    assert first_result == "first-inspection-complete"
    assert second_result == "second-inspection-complete"
    assert second_handler_entered.is_set()
    assert not cleanup_started_before_second_return
    assert status.coalesced == 1
    assert cleanup_started.is_set()
    assert controller.idle_release_calls == 1


def test_idle_cleanup_failure_retries_and_exposes_pid_status() -> None:
    retry_clock = _ControlledCleanupClock()
    retry_succeeded = Event()
    cleanup_calls: list[frozenset[int]] = []

    def flaky_idle_cleanup(
        _bridge: HancomBridge,
        process_ids: frozenset[int],
    ) -> None:
        cleanup_calls.append(process_ids)
        if len(cleanup_calls) == 1:
            raise RuntimeError("injected bridge cleanup failure")
        retry_succeeded.set()

    with (
        patch("hwp_live_process_lane._initialize_sta"),
        patch("hwp_live_process_lane._uninitialize_sta"),
        patch("hwp_live_bridge.open_process_exit_watch", return_value=None),
        patch.object(
            HancomBridge,
            "release_idle_references",
            autospec=True,
            side_effect=flaky_idle_cleanup,
        ),
    ):
        bridge = _bridge()
        bridge._controllers = {
            202: cast(LiveHwpController, cast(object, _Controller())),
        }
        dispatcher = McpThreadDispatcher(
            operation_workers=1,
            cleanup_quiet_seconds=0,
            cleanup_retry_initial_seconds=0.05,
            cleanup_retry_max_seconds=0.1,
            cleanup_clock=retry_clock.now,
            cleanup_waiter=retry_clock.wait,
        )
        executor = HwpOperationExecutor(bridge, dispatcher, None)

        async def handler() -> str:
            _ = await dispatcher.run(
                bridge._call,
                lambda: None,
                process_id=202,
            )
            return "inspection-complete"

        wrapped = cast(
            Callable[[], Awaitable[object]],
            _public_tool_handler(executor, "hwp_inspect_page_fast", handler),
        )

        async def invoke() -> tuple[object, CleanupStatus, CleanupStatus]:
            try:
                result = await wrapped()
                assert await dispatcher.watch(retry_clock.retry_waiting.wait, 1)
                failed_status = dispatcher.cleanup_status()
                retry_clock.allow_retry.set()
                assert await dispatcher.watch(retry_succeeded.wait, 1)
                with anyio.fail_after(1):
                    while dispatcher.cleanup_status().pending:
                        await anyio.sleep(0)
                recovered_status = dispatcher.cleanup_status()
                return result, failed_status, recovered_status
            finally:
                retry_clock.allow_retry.set()
                await dispatcher.close(bridge.close)

        result, failed_status, recovered_status = anyio.run(invoke)

    failed_process = failed_status.processes[0]
    recovered_process = recovered_status.processes[0]
    assert result == "inspection-complete"
    assert cleanup_calls == [frozenset((202,)), frozenset((202,))]
    assert failed_status.failures == 1
    assert failed_status.pending == 1
    assert failed_status.running == 0
    assert failed_process.process_id == 202
    assert failed_process.attempts == 1
    assert failed_process.next_retry_at is not None
    assert abs(failed_process.next_retry_at - 0.05) < 1e-12
    assert failed_process.last_error == (
        "RuntimeError: injected bridge cleanup failure"
    )
    assert recovered_status.attempts == 2
    assert recovered_status.successes == 1
    assert recovered_status.failures == 1
    assert recovered_status.retries == 1
    assert recovered_status.pending == 0
    assert recovered_process.last_error is None
    assert len(retry_clock.wait_seconds) == 1
    assert abs(retry_clock.wait_seconds[0] - 0.05) < 1e-12

from __future__ import annotations

import anyio
import pytest

from hwp_session_state_support import patched_controller, session_system
from hwp_errors import HwpLiveError
from hwp_live_bridge import HancomBridge
from hwp_live_contract import ConnectedDocument
from hwp_mcp_operation_executor import HwpOperationExecutor


def test_connect_cancellation_cannot_split_session_ownership() -> None:
    # Given
    state, signal, bridge, dispatcher, executor, _ = session_system()
    signal.block_next_start = True

    async def exercise() -> tuple[ConnectedDocument, ConnectedDocument]:
        first_results: list[ConnectedDocument] = []
        scopes: list[anyio.CancelScope] = []
        finished = anyio.Event()

        async def first_connect() -> None:
            try:
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    first_results.append(await executor.ensure_connection(None))
            finally:
                finished.set()

        try:
            async with anyio.create_task_group() as tasks:
                _ = tasks.start_soon(first_connect)
                _ = await dispatcher.watch(signal.start_entered.wait)
                # When
                scopes[0].cancel()
                signal.release_start.set()
                await finished.wait()
            second = await executor.ensure_connection(None)
            return first_results[0], second
        finally:
            signal.release_start.set()
            await dispatcher.close(bridge.close)

    with patched_controller(state):
        first, second = anyio.run(exercise)

    # Then
    assert first.session_id == second.session_id == "session-1"
    assert state.connect_calls == 1
    assert signal.start_calls == 1


def test_disconnect_failure_after_owner_clear_cleans_and_reconnects() -> None:
    # Given
    state, signal, bridge, dispatcher, _, handler = session_system()

    async def exercise() -> tuple[ConnectedDocument, bool, bool, ConnectedDocument]:
        try:
            first = await handler.hwp_connect()
            _ = await dispatcher.run(
                bridge.inspect_page_fast,
                first.session_id,
                1,
            )
            state.fail_disconnect_after_clear = True
            # When
            with pytest.raises(HwpLiveError, match="COM 속성"):
                _ = await handler.hwp_disconnect()
            cleanup_observed = not signal.active
            cache_cleared = not bridge.has_cached_fast_inspections()
            state.fail_disconnect_after_clear = False
            second = await handler.hwp_connect()
            return first, cleanup_observed, cache_cleared, second
        finally:
            await dispatcher.close(bridge.close)

    with patched_controller(state):
        first, cleanup_observed, cache_cleared, second = anyio.run(exercise)

    # Then
    assert cleanup_observed is True
    assert cache_cleared is True
    assert second.session_id != first.session_id
    assert state.connect_calls == 2


def test_invalid_disconnect_preserves_the_live_owner() -> None:
    # Given
    state, signal, bridge, dispatcher, _, handler = session_system()

    async def exercise() -> tuple[ConnectedDocument, bool, ConnectedDocument]:
        try:
            first = await handler.hwp_connect()
            # When
            with pytest.raises(HwpLiveError, match="유효한 한컴 라이브 세션"):
                _ = await handler.hwp_disconnect("wrong-session")
            remained_active = signal.active
            second = await handler.hwp_connect()
            return first, remained_active, second
        finally:
            await dispatcher.close(bridge.close)

    with patched_controller(state):
        first, remained_active, second = anyio.run(exercise)

    # Then
    assert remained_active is True
    assert second.session_id == first.session_id
    assert state.connect_calls == 1


def test_session_id_is_stored_only_by_the_controller_core() -> None:
    # Given / When
    bridge_slots = HancomBridge.__slots__
    executor_slots = HwpOperationExecutor.__slots__

    # Then
    assert "_connected_session_id" not in bridge_slots
    assert "_connected_window_handle" not in bridge_slots
    assert "operation_session_id" not in executor_slots
    assert "operation_document" not in executor_slots

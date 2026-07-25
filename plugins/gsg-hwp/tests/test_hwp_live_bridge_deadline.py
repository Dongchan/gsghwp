from __future__ import annotations

import sys
from pathlib import Path
from threading import BoundedSemaphore, Event
from unittest.mock import patch
from collections.abc import Callable
from typing import TypeVar, cast, final

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_native_action_contract import (  # noqa: E402
    NativeActionFailure,
    NativeActionFailureEvidence,
)
from hwp_live_session import LiveHwpController  # noqa: E402


T = TypeVar("T")


def _call(
    bridge: HancomBridge,
    operation: Callable[[], T],
    *,
    process_id: int,
    mutation: bool = False,
) -> T:
    method_name = "_call_mutation" if mutation else "_call"
    method = cast(Callable[..., T], getattr(bridge, method_name))
    return method(operation, process_id=process_id)


@final
class _Controller:
    def __init__(self) -> None:
        self.restore_calls = 0

    def restore_activation(self) -> None:
        self.restore_calls += 1

    def close(self) -> None:
        return


@final
class _BusyError(RuntimeError):
    def __init__(self, hresult: int) -> None:
        self.hresult = hresult
        super().__init__(f"COM failure {hresult}")


def _bridge(
    controller: _Controller,
    *,
    timeout: float = 1,
    queue_limit: int = 2,
) -> HancomBridge:
    return HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        call_timeout_seconds=timeout,
        process_queue_limit=queue_limit,
    )


def test_read_retries_com_busy_but_mutation_does_not() -> None:
    controller = _Controller()
    bridge = _bridge(controller)
    read_attempts = 0
    mutation_attempts = 0

    def read() -> str:
        nonlocal read_attempts
        read_attempts += 1
        if read_attempts < 3:
            raise _BusyError(-2_147_417_846)
        return "ready"

    def mutate() -> None:
        nonlocal mutation_attempts
        mutation_attempts += 1
        raise _BusyError(-2_147_418_111)

    try:
        assert _call(bridge, read, process_id=17) == "ready"
        with pytest.raises(_BusyError):
            _ = _call(bridge, mutate, process_id=17, mutation=True)
        assert read_attempts == 3
        assert mutation_attempts == 1
        assert controller.restore_calls == 4
    finally:
        bridge.close()


def test_process_queue_wait_is_bounded_by_total_deadline() -> None:
    controller = _Controller()
    bridge = _bridge(controller, timeout=0.01, queue_limit=1)
    held = BoundedSemaphore(1)
    assert held.acquire(blocking=False)
    queues = cast(
        dict[int, BoundedSemaphore],
        getattr(bridge, "_process_queues"),
    )
    queues[23] = held
    called = False

    def operation() -> None:
        nonlocal called
        called = True

    try:
        with pytest.raises(HwpLiveError) as timeout:
            _ = _call(bridge, operation, process_id=23)
        assert called is False
        assert "phase=queue_wait" in timeout.value.reason
        assert "worker_isolation_required=false" in timeout.value.reason
        assert "retry_safe=true" in timeout.value.reason
    finally:
        held.release()
        bridge.close()


def test_running_mutation_timeout_is_not_retried_and_requires_reconcile() -> None:
    controller = _Controller()
    bridge = _bridge(controller, timeout=0.02, queue_limit=1)
    started = Event()
    release = Event()
    finished = Event()
    attempts = 0

    def mutation() -> None:
        nonlocal attempts
        attempts += 1
        started.set()
        _ = release.wait(1)
        finished.set()

    try:
        with pytest.raises(HwpLiveError) as timeout:
            _ = _call(bridge, mutation, process_id=31, mutation=True)
        assert started.is_set()
        assert attempts == 1
        assert "phase=running" in timeout.value.reason
        assert "worker_isolation_required=true" in timeout.value.reason
        assert "reconcile_required=true" in timeout.value.reason
        assert "retry_safe=false" in timeout.value.reason
    finally:
        release.set()
        assert finished.wait(1)
        bridge.close()


def test_structured_native_failure_survives_popup_diagnostic() -> None:
    controller = _Controller()
    bridge = _bridge(controller)
    failure = NativeActionFailure(
        NativeActionFailureEvidence(
            code="AMBIGUOUS_TEXT_MATCH",
            location="0:1:0-0:1:4|0:2:0-0:2:4",
            message="multiple text matches require an explicit occurrence",
            commands_completed=0,
            failed_step="text.find",
            partial_mutation=False,
            retry_safe=True,
            structure_digest_before=None,
            structure_digest_after=None,
        )
    )

    def ambiguous_patch() -> None:
        raise failure

    try:
        with (
            patch("hwp_live_bridge.popup_diagnostic", return_value="test.hwp"),
            pytest.raises(NativeActionFailure) as raised,
        ):
            _ = _call(bridge, ambiguous_patch, process_id=41, mutation=True)
        assert raised.value is failure
    finally:
        bridge.close()

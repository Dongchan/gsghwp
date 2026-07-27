from __future__ import annotations

import sys
from math import isclose
from pathlib import Path
from threading import Event
from time import monotonic
from typing import final

SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_events import HwpEventObservation  # noqa: E402
from hwp_live_bridge_contract import (  # noqa: E402
    HancomDialogDismissResult,
    HancomWindowChildState,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_process_lane import (  # noqa: E402
    HwpLaneOperationContext,
    HwpMutationWatchdog,
    HwpProcessLane,
    MutationWatchdogProbeSample,
    Win32MutationWatchdogProbe,
    adaptive_watchdog_poll_seconds,
    current_lane_operation_context,
)
from hwp_live_session_lifecycle import (  # noqa: E402
    SaveStateMachine,
    save_preflight_result,
)


@final
class _Probe:
    def __init__(self, samples: list[MutationWatchdogProbeSample]) -> None:
        self._samples = samples
        self.calls = 0

    def sample(self) -> MutationWatchdogProbeSample:
        index = min(self.calls, len(self._samples) - 1)
        self.calls += 1
        return self._samples[index]

    def close(self) -> None:
        return


@final
class _BlockingProbe:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def sample(self) -> MutationWatchdogProbeSample:
        self.started.set()
        _ = self.release.wait(2)
        return _sample()

    def close(self) -> None:
        return


@final
class _FlakyProbe:
    def __init__(self) -> None:
        self.calls = 0

    def sample(self) -> MutationWatchdogProbeSample:
        self.calls += 1
        if self.calls == 1:
            raise OSError("transient Win32 probe failure")
        return _sample(responding=False)

    def close(self) -> None:
        return


@final
class _WindowReader:
    def __init__(self, window: HancomWindowState) -> None:
        self._window = window

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        return HancomWindowStateList(windows=(self._window,))

    def read(self, window_handle: int) -> HancomWindowState:
        assert window_handle == self._window.window_handle
        return self._window

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        raise AssertionError(f"watchdog cannot dismiss dialogs: {window_handle}")


def _sample(
    *,
    alive: bool = True,
    responding: bool = True,
    progress_visible: bool = False,
    modal: bool = False,
    size: int | None = 100,
    mtime_ns: int | None = 1,
) -> MutationWatchdogProbeSample:
    return MutationWatchdogProbeSample(
        process_alive=alive,
        responding=responding,
        progress_control_present=progress_visible,
        progress_control_visible=progress_visible,
        modal_present=modal,
        file_size=size,
        file_mtime_ns=mtime_ns,
    )


def test_watchdog_records_warning_progress_and_uncertain_terminal() -> None:
    state = SaveStateMachine()
    state.native_started(source="DocumentBeforeSave")
    probe = _Probe(
        [
            _sample(),
            _sample(responding=False),
            _sample(responding=False, progress_visible=True, size=120, mtime_ns=2),
            _sample(responding=False, size=120, mtime_ns=2),
        ]
    )
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=Path("C:/documents/copy.hwp"),
        document_id=17,
        state=state,
        probe=probe,
        warning_seconds=30,
        terminal_seconds=180,
        started_at=0,
    )

    baseline = watchdog.observe(now=0)
    warning = watchdog.observe(now=31)
    progress = watchdog.observe(now=32)
    terminal = watchdog.observe(now=181)

    assert baseline.warning is False
    assert warning.warning is True
    assert warning.responding is False
    assert progress.progress_control_visible is True
    assert progress.metadata_changed is True
    assert terminal.terminal_uncertain is True
    assert state.snapshot().phase == "uncertain"
    assert state.snapshot().retry_safe is False


def test_watchdog_anchors_file_metadata_before_the_first_sample(
    tmp_path: Path,
) -> None:
    document = tmp_path / "copy.hwp"
    _ = document.write_bytes(b"before")
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=document,
        document_id=17,
        state=SaveStateMachine(),
        probe=_Probe(
            [
                _sample(
                    size=len(b"after-save"),
                    mtime_ns=document.stat().st_mtime_ns + 1,
                )
            ]
        ),
        started_at=0,
    )
    _ = document.write_bytes(b"after-save")

    observation = watchdog.observe(now=1)

    assert observation.metadata_changed is True


def test_unarmed_watchdog_does_not_attribute_queued_save_events_or_file_delta(
    tmp_path: Path,
) -> None:
    document = tmp_path / "copy.hwp"
    _ = document.write_bytes(b"queued-baseline")
    state = SaveStateMachine()
    events = [
        HwpEventObservation(
            sequence=1,
            name="DocumentBeforeSave",
            document_id=17,
            observed_at_monotonic_ns=1,
        ),
        HwpEventObservation(
            sequence=2,
            name="DocumentAfterSave",
            document_id=17,
            observed_at_monotonic_ns=2,
        ),
    ]
    baseline_size = document.stat().st_size
    baseline_mtime_ns = document.stat().st_mtime_ns
    probe = _Probe(
        [
            _sample(size=20, mtime_ns=2),
            _sample(size=baseline_size, mtime_ns=baseline_mtime_ns),
            _sample(size=baseline_size + 10, mtime_ns=baseline_mtime_ns + 1),
        ]
    )
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=document,
        document_id=17,
        state=state,
        probe=probe,
        event_reader=lambda after: tuple(
            event for event in events if event.sequence > after
        ),
        started_at=0,
        armed=False,
    )

    queued = watchdog.observe(now=31)
    queued_snapshot = state.snapshot()
    watchdog.arm(started_at=32, after_event_sequence=2)
    armed_baseline = watchdog.observe(now=32)
    events.append(
        HwpEventObservation(
            sequence=3,
            name="DocumentBeforeSave",
            document_id=17,
            observed_at_monotonic_ns=3,
        )
    )
    changed = watchdog.observe(now=33)

    assert queued.warning is False
    assert queued.metadata_changed is False
    assert queued_snapshot.phase == "queued"
    assert tuple(transition.source for transition in queued_snapshot.transitions) == (
        "request",
    )
    assert state.snapshot().phase == "progress"
    assert tuple(transition.source for transition in state.snapshot().transitions) == (
        "request",
        "DocumentBeforeSave",
        "watchdog",
    )
    assert armed_baseline.metadata_changed is False
    assert changed.metadata_changed is True


def test_watchdog_detects_file_created_after_missing_baseline(
    tmp_path: Path,
) -> None:
    document = tmp_path / "new-copy.hwp"
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=document,
        document_id=17,
        state=SaveStateMachine(),
        probe=_Probe(
            [
                _sample(size=None, mtime_ns=None),
                _sample(size=10, mtime_ns=1),
            ]
        ),
        started_at=0,
    )

    baseline = watchdog.observe(now=0)
    created = watchdog.observe(now=1)

    assert baseline.metadata_changed is False
    assert created.metadata_changed is True


def test_context_reanchors_metadata_when_queued_mutation_finishes(
    tmp_path: Path,
) -> None:
    document = tmp_path / "copy.hwp"
    _ = document.write_bytes(b"request")
    context = HwpLaneOperationContext(
        session_id="session-1",
        process_id=77,
        window_handle=101,
        document_path=document,
        document_id=17,
    )
    _ = document.write_bytes(b"earlier-mutation")

    context.arm_watchdog_baseline()
    assert context.watchdog_metadata_changed is False

    _ = document.write_bytes(b"this-save")
    assert context.watchdog_metadata_changed is True


def test_process_lane_reanchors_metadata_at_actual_execution(
    tmp_path: Path,
) -> None:
    document = tmp_path / "copy.hwp"
    _ = document.write_bytes(b"request")
    first_started = Event()
    release_first = Event()
    context = HwpLaneOperationContext(
        session_id="session-1",
        process_id=77,
        window_handle=101,
        document_path=document,
        document_id=17,
    )
    lane = HwpProcessLane(77, 2)

    def earlier_operation() -> None:
        first_started.set()
        _ = release_first.wait(1)
        _ = document.write_bytes(b"earlier-mutation")

    def this_operation() -> bool:
        assert context.watchdog_metadata_changed is False
        _ = document.write_bytes(b"this-save")
        return context.watchdog_metadata_changed

    try:
        earlier = lane.submit(earlier_operation)
        assert first_started.wait(1)
        current = lane.submit(
            this_operation,
            context=context,
            on_start=context.arm_watchdog_baseline,
        )
        release_first.set()
        _ = earlier.result(timeout=1)
        assert current.result(timeout=1) is True
    finally:
        release_first.set()
        lane.shutdown(wait=True)


def test_lane_context_detects_a_fast_save_before_watchdog_sampling(
    tmp_path: Path,
) -> None:
    document = tmp_path / "copy.hwp"
    _ = document.write_bytes(b"before")
    context = HwpLaneOperationContext(
        session_id="session-1",
        process_id=77,
        window_handle=101,
        document_path=document,
        document_id=17,
    )
    context.arm_watchdog_baseline()

    _ = document.write_bytes(b"after-save")

    assert context.watchdog_metadata_changed is True


def test_watchdog_polling_adapts_to_low_cpu_priority_and_document_size() -> None:
    fast = adaptive_watchdog_poll_seconds(
        process_id=77,
        document_path=Path("C:/documents/small.hwp"),
        effective_processors=8,
        priority_class=0x20,
        document_size=0,
    )
    constrained = adaptive_watchdog_poll_seconds(
        process_id=77,
        document_path=Path("C:/documents/large.hwp"),
        effective_processors=2,
        priority_class=0x4000,
        document_size=64 * 1024 * 1024,
    )
    capped = adaptive_watchdog_poll_seconds(
        process_id=77,
        document_path=Path("C:/documents/huge.hwp"),
        base_seconds=0.4,
        effective_processors=1,
        priority_class=0x40,
        document_size=1024 * 1024 * 1024,
    )

    assert fast == 0.1
    assert fast < constrained <= 0.5
    assert capped == 0.5


def test_watchdog_adaptive_wait_preserves_absolute_warning_and_terminal_edges() -> None:
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=Path("C:/documents/copy.hwp"),
        document_id=17,
        state=SaveStateMachine(),
        probe=_Probe([_sample()]),
        warning_seconds=30,
        terminal_seconds=180,
        poll_seconds=0.2,
        poll_max_seconds=0.5,
        started_at=0,
    )

    assert isclose(
        watchdog.next_poll_seconds(now=29.95, probe_elapsed_seconds=0.01),
        0.05,
    )
    assert isclose(
        watchdog.next_poll_seconds(now=31, probe_elapsed_seconds=0.4),
        0.5,
    )
    assert isclose(
        watchdog.next_poll_seconds(now=179.8, probe_elapsed_seconds=0.4),
        0.2,
    )


def test_watchdog_correlates_native_save_events_by_document_id() -> None:
    state = SaveStateMachine()
    state.native_started(source="native_dispatch")
    events = (
        HwpEventObservation(
            sequence=1,
            name="DocumentBeforeSave",
            document_id=18,
            observed_at_monotonic_ns=1,
        ),
        HwpEventObservation(
            sequence=2,
            name="DocumentBeforeSave",
            document_id=17,
            observed_at_monotonic_ns=2,
        ),
        HwpEventObservation(
            sequence=3,
            name="DocumentAfterSave",
            document_id=17,
            observed_at_monotonic_ns=3,
        ),
    )
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=Path("C:/documents/copy.hwp"),
        document_id=17,
        state=state,
        probe=_Probe([_sample()]),
        event_reader=lambda after: tuple(
            event for event in events if event.sequence > after
        ),
        started_at=0,
    )

    observation = watchdog.observe(now=1)

    assert observation.last_event_sequence == 3
    assert tuple(transition.source for transition in state.snapshot().transitions) == (
        "request",
        "native_dispatch",
        "DocumentBeforeSave",
        "DocumentAfterSave",
    )
    assert state.snapshot().phase == "progress"


def test_watchdog_thread_keeps_sampling_while_mutation_is_blocked() -> None:
    probe = _Probe([_sample(responding=False)])
    state = SaveStateMachine()
    state.native_started(source="DocumentBeforeSave")
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=Path("C:/documents/copy.hwp"),
        document_id=17,
        state=state,
        probe=probe,
        warning_seconds=0.01,
        terminal_seconds=1,
        poll_seconds=0.005,
    )
    mutation_release = Event()

    watchdog.start()
    deadline = monotonic() + 0.5
    while probe.calls < 3 and monotonic() < deadline:
        _ = mutation_release.wait(0.005)
    watchdog.stop()

    assert probe.calls >= 3
    assert watchdog.snapshot().warning is True


def test_watchdog_snapshot_and_stop_do_not_wait_for_a_stalled_os_probe() -> None:
    probe = _BlockingProbe()
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=Path("C:/documents/copy.hwp"),
        document_id=17,
        state=SaveStateMachine(),
        probe=probe,
        poll_seconds=0.05,
    )

    watchdog.start()
    assert probe.started.wait(0.5)
    try:
        snapshot_started = monotonic()
        snapshot = watchdog.snapshot()
        assert monotonic() - snapshot_started < 0.1
        assert snapshot.responding is None

        stop_started = monotonic()
        watchdog.stop()
        assert monotonic() - stop_started < 0.5
    finally:
        probe.release.set()


def test_watchdog_survives_a_transient_os_probe_failure() -> None:
    probe = _FlakyProbe()
    watchdog = HwpMutationWatchdog(
        process_id=77,
        window_handle=101,
        document_path=Path("C:/documents/copy.hwp"),
        document_id=17,
        state=SaveStateMachine(),
        probe=probe,
        warning_seconds=0.05,
        terminal_seconds=1,
        poll_seconds=0.01,
    )

    watchdog.start()
    deadline = monotonic() + 0.5
    while probe.calls < 2 and monotonic() < deadline:
        _ = Event().wait(0.01)
    watchdog.stop()

    assert probe.calls >= 2


def test_watchdog_distinguishes_hidden_progress_control_from_absence(
    tmp_path: Path,
) -> None:
    document = tmp_path / "copy.hwp"
    _ = document.write_bytes(b"fixture")
    window = HancomWindowState(
        window_handle=101,
        process_id=77,
        exists=True,
        visible=True,
        enabled=True,
        foreground=False,
        title="copy.hwp - 한글",
        class_name="HwpMain",
        dialogs=(),
        children=(
            HancomWindowChildState(
                window_handle=102,
                title="",
                class_name="msctls_progress32",
                visible=False,
                enabled=True,
            ),
        ),
    )

    def not_hung(window_handle: int) -> bool:
        assert window_handle == 101
        return False

    probe = Win32MutationWatchdogProbe(
        window_handle=101,
        document_path=document,
        window_reader=_WindowReader(window),
        exit_watch=None,
        hung_reader=not_hung,
    )

    sample = probe.sample()
    probe.close()

    assert sample.process_alive is None
    assert sample.responding is True
    assert sample.progress_control_present is True
    assert sample.progress_control_visible is False
    assert sample.modal_present is False
    assert sample.file_size == len(b"fixture")


def test_process_lane_exposes_save_context_only_on_its_sta_thread() -> None:
    context = HwpLaneOperationContext(
        session_id="session-1",
        process_id=77,
        window_handle=101,
        document_path=Path("C:/documents/copy.hwp"),
        document_id=17,
    )
    lane = HwpProcessLane(77, 1)

    def operation() -> object:
        assert current_lane_operation_context() is context
        _ = save_preflight_result(
            "save",
            resolve_only=False,
            allow_document_change=True,
        )
        return context.save_state()

    try:
        state = lane.submit(operation, context=context).result(timeout=1)
    finally:
        lane.shutdown(wait=True)

    assert isinstance(state, SaveStateMachine)
    assert state.snapshot().phase == "native_started"
    assert state.snapshot().transitions[-1].source == "native_dispatch"
    assert current_lane_operation_context() is None

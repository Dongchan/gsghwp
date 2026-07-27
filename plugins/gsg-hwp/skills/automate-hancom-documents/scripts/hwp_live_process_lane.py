from __future__ import annotations

# pyright: reportAny=false

from collections.abc import Callable
from concurrent.futures import Future
from ctypes import POINTER, WinDLL, byref, c_size_t, wintypes
from dataclasses import dataclass
from importlib import import_module
from os import cpu_count
from pathlib import Path
from queue import Empty, Queue
from threading import BoundedSemaphore, Event, Lock, Thread, local
from time import monotonic
from typing import Final, Generic, Protocol, TypeVar, cast, final

from hwp_live_bridge_contract import HancomWindowState
from hwp_live_events import HwpEventObservation
from hwp_live_windows import ProcessExitWatch, WindowStateReader


T = TypeVar("T")
_PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
_IDLE_PRIORITY_CLASS: Final = 0x40
_BELOW_NORMAL_PRIORITY_CLASS: Final = 0x4000
_WATCHDOG_DOCUMENT_SCALE_BYTES: Final = 64 * 1024 * 1024
_WATCHDOG_MIN_POLL_SECONDS: Final = 0.05
_WATCHDOG_MAX_POLL_SECONDS: Final = 0.5


class SaveStateObserver(Protocol):
    def native_started(self, *, source: str, detail: str | None = None) -> None: ...

    def heartbeat(self, *, source: str, detail: str | None = None) -> None: ...

    def progress(self, *, source: str, detail: str | None = None) -> None: ...

    def uncertain(self, *, source: str, detail: str | None = None) -> None: ...


@final
class HwpLaneOperationContext:
    __slots__ = (
        "_baseline_captured",
        "_baseline_file_exists",
        "_baseline_file_mtime_ns",
        "_baseline_file_size",
        "_close_requested",
        "_lock",
        "_save_state",
        "_watchdog_metadata_changed",
        "document_id",
        "document_path",
        "process_id",
        "session_id",
        "window_handle",
    )

    def __init__(
        self,
        *,
        session_id: str,
        process_id: int,
        window_handle: int,
        document_path: Path,
        document_id: int,
    ) -> None:
        self._baseline_captured = False
        self._baseline_file_exists: bool | None = None
        self._baseline_file_mtime_ns: int | None = None
        self._baseline_file_size: int | None = None
        self._close_requested = False
        self._lock = Lock()
        self._save_state: SaveStateObserver | None = None
        self._watchdog_metadata_changed = False
        self.document_id = document_id
        self.document_path = document_path
        self.process_id = process_id
        self.session_id = session_id
        self.window_handle = window_handle

    def attach_save_state(
        self,
        state: SaveStateObserver,
    ) -> SaveStateObserver | None:
        with self._lock:
            if self._save_state is None:
                self._save_state = state
            return self._save_state

    def request_close_guard(self) -> SaveStateObserver | None:
        with self._lock:
            self._close_requested = True
            return self._save_state

    def save_state(self) -> SaveStateObserver | None:
        with self._lock:
            return self._save_state

    def record_watchdog_metadata_change(self) -> None:
        with self._lock:
            self._watchdog_metadata_changed = True

    def arm_watchdog_baseline(self) -> None:
        try:
            baseline = self.document_path.stat()
        except FileNotFoundError:
            exists: bool | None = False
            mtime_ns = None
            size = None
        except OSError:
            exists = None
            mtime_ns = None
            size = None
        else:
            exists = True
            mtime_ns = baseline.st_mtime_ns
            size = baseline.st_size
        with self._lock:
            self._baseline_captured = True
            self._baseline_file_exists = exists
            self._baseline_file_mtime_ns = mtime_ns
            self._baseline_file_size = size
            self._watchdog_metadata_changed = False

    @property
    def watchdog_metadata_changed(self) -> bool:
        with self._lock:
            changed = self._watchdog_metadata_changed
            baseline_captured = self._baseline_captured
            baseline_file_exists = self._baseline_file_exists
            baseline_file_mtime_ns = self._baseline_file_mtime_ns
            baseline_file_size = self._baseline_file_size
        if changed:
            return True
        if not baseline_captured:
            return False
        try:
            current = self.document_path.stat()
        except FileNotFoundError:
            current_exists: bool | None = False
            current_mtime_ns = None
            current_size = None
        except OSError:
            return False
        else:
            current_exists = True
            current_mtime_ns = current.st_mtime_ns
            current_size = current.st_size
        changed = (
            (
                baseline_file_exists is not None
                and baseline_file_exists != current_exists
            )
            or (
                baseline_file_size is not None
                and current_size is not None
                and current_size != baseline_file_size
            )
            or (
                baseline_file_mtime_ns is not None
                and current_mtime_ns is not None
                and current_mtime_ns != baseline_file_mtime_ns
            )
        )
        if changed:
            self.record_watchdog_metadata_change()
        return changed

    def native_started(self, *, source: str, detail: str | None = None) -> None:
        state = self.save_state()
        if state is not None:
            state.native_started(source=source, detail=detail)

    def heartbeat(self, *, source: str, detail: str | None = None) -> None:
        state = self.save_state()
        if state is not None:
            state.heartbeat(source=source, detail=detail)

    def progress(self, *, source: str, detail: str | None = None) -> None:
        state = self.save_state()
        if state is not None:
            state.progress(source=source, detail=detail)

    def uncertain(self, *, source: str, detail: str | None = None) -> None:
        state = self.save_state()
        if state is not None:
            state.uncertain(source=source, detail=detail)


class _LaneThreadState(local):
    operation_context: HwpLaneOperationContext | None

    def __init__(self) -> None:
        self.operation_context = None


_LANE_THREAD_STATE = _LaneThreadState()


def current_lane_operation_context() -> HwpLaneOperationContext | None:
    return _LANE_THREAD_STATE.operation_context


@dataclass(frozen=True, slots=True)
class MutationWatchdogProbeSample:
    process_alive: bool | None
    responding: bool | None
    progress_control_present: bool | None
    progress_control_visible: bool | None
    modal_present: bool | None
    file_size: int | None
    file_mtime_ns: int | None


class MutationWatchdogProbe(Protocol):
    def sample(self) -> MutationWatchdogProbeSample: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MutationWatchdogObservation:
    process_id: int
    window_handle: int
    document_path: Path
    document_id: int
    elapsed_seconds: float
    process_alive: bool | None
    responding: bool | None
    progress_control_present: bool | None
    progress_control_visible: bool | None
    modal_present: bool | None
    file_size: int | None
    file_mtime_ns: int | None
    metadata_changed: bool
    warning: bool
    terminal_uncertain: bool
    last_event_sequence: int
    poll_seconds: float


SaveEventReader = Callable[[int], tuple[HwpEventObservation, ...]]
WindowHungReader = Callable[[int], bool | None]


def _target_process_environment(process_id: int) -> tuple[int | None, int | None]:
    try:
        kernel32 = WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not handle:
            return None, None
        try:
            process_mask = c_size_t()
            system_mask = c_size_t()
            get_affinity = kernel32.GetProcessAffinityMask
            get_affinity.argtypes = (
                wintypes.HANDLE,
                POINTER(c_size_t),
                POINTER(c_size_t),
            )
            get_affinity.restype = wintypes.BOOL
            effective_processors = (
                int(process_mask.value).bit_count()
                if get_affinity(handle, byref(process_mask), byref(system_mask))
                else None
            )
            get_priority = kernel32.GetPriorityClass
            get_priority.argtypes = (wintypes.HANDLE,)
            get_priority.restype = wintypes.DWORD
            priority_value = int(get_priority(handle))
            priority_class = priority_value if priority_value > 0 else None
            return effective_processors, priority_class
        finally:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            _ = close_handle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None, None


def adaptive_watchdog_poll_seconds(
    *,
    process_id: int,
    document_path: Path,
    base_seconds: float = 0.1,
    minimum_seconds: float = _WATCHDOG_MIN_POLL_SECONDS,
    maximum_seconds: float = _WATCHDOG_MAX_POLL_SECONDS,
    effective_processors: int | None = None,
    priority_class: int | None = None,
    document_size: int | None = None,
) -> float:
    if process_id < 1:
        raise ValueError("watchdog process_id must be positive")
    if minimum_seconds <= 0 or maximum_seconds < minimum_seconds:
        raise ValueError("watchdog poll bounds are invalid")
    if base_seconds <= 0:
        raise ValueError("watchdog base poll must be positive")
    if effective_processors is not None and effective_processors < 1:
        raise ValueError("effective processor count must be positive")
    detected_processors: int | None = None
    detected_priority: int | None = None
    if effective_processors is None or priority_class is None:
        detected_processors, detected_priority = _target_process_environment(process_id)
    processors = (
        effective_processors
        if effective_processors is not None
        else detected_processors or cpu_count() or 1
    )
    selected_priority = (
        priority_class if priority_class is not None else detected_priority
    )
    if document_size is None:
        try:
            document_size = document_path.stat().st_size
        except OSError:
            document_size = 0
    processor_factor = 1.0 + min(max(4 - processors, 0), 3) / 4
    priority_factor = (
        1.5
        if selected_priority == _IDLE_PRIORITY_CLASS
        else 1.25
        if selected_priority == _BELOW_NORMAL_PRIORITY_CLASS
        else 1.0
    )
    document_factor = 1.0 + min(
        max(document_size, 0) / _WATCHDOG_DOCUMENT_SCALE_BYTES,
        1.0,
    )
    adapted = base_seconds * processor_factor * priority_factor * document_factor
    return min(maximum_seconds, max(minimum_seconds, adapted))


def _window_hung(window_handle: int) -> bool | None:
    try:
        user32 = WinDLL("user32", use_last_error=True)
        is_hung = user32.IsHungAppWindow
        is_hung.argtypes = (wintypes.HWND,)
        is_hung.restype = wintypes.BOOL
        return bool(is_hung(window_handle))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


@final
class Win32MutationWatchdogProbe:
    __slots__ = (
        "_document_path",
        "_exit_watch",
        "_hung_reader",
        "_window_handle",
        "_windows",
    )

    def __init__(
        self,
        *,
        window_handle: int,
        document_path: Path,
        window_reader: WindowStateReader,
        exit_watch: ProcessExitWatch | None,
        hung_reader: WindowHungReader = _window_hung,
    ) -> None:
        self._document_path = document_path
        self._exit_watch = exit_watch
        self._hung_reader = hung_reader
        self._window_handle = window_handle
        self._windows = window_reader

    def _window(self) -> HancomWindowState | None:
        try:
            return self._windows.read(self._window_handle)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _file_metadata(self) -> tuple[int | None, int | None]:
        try:
            stat = self._document_path.stat()
        except OSError:
            return None, None
        return stat.st_size, stat.st_mtime_ns

    def sample(self) -> MutationWatchdogProbeSample:
        exit_watch = self._exit_watch
        process_alive = None if exit_watch is None else not exit_watch.exited()
        window = self._window()
        hung = self._hung_reader(self._window_handle)
        responding = (
            None if window is None or not window.exists or hung is None else not hung
        )
        progress_children = (
            ()
            if window is None
            else tuple(
                child
                for child in window.children
                if child.class_name.casefold() == "msctls_progress32"
            )
        )
        progress_present = None if window is None else bool(progress_children)
        progress_visible = (
            None
            if window is None
            else any(child.visible for child in progress_children)
        )
        modal_present = (
            None
            if window is None
            else any(dialog.visible and dialog.modal for dialog in window.dialogs)
        )
        file_size, file_mtime_ns = self._file_metadata()
        return MutationWatchdogProbeSample(
            process_alive=process_alive,
            responding=responding,
            progress_control_present=progress_present,
            progress_control_visible=progress_visible,
            modal_present=modal_present,
            file_size=file_size,
            file_mtime_ns=file_mtime_ns,
        )

    def close(self) -> None:
        exit_watch = self._exit_watch
        if exit_watch is not None:
            exit_watch.close()
        self._exit_watch = None


@final
class HwpMutationWatchdog:
    __slots__ = (
        "_arm_generation",
        "_armed",
        "_baseline_file_exists",
        "_baseline_file_mtime_ns",
        "_baseline_file_size",
        "_document_id",
        "_document_path",
        "_event_reader",
        "_last_event_sequence",
        "_latest",
        "_lock",
        "_current_poll_seconds",
        "_poll_max_seconds",
        "_poll_seconds",
        "_probe",
        "_process_id",
        "_started_at",
        "_state",
        "_stop",
        "_terminal_seconds",
        "_thread",
        "_warning_seconds",
        "_window_handle",
    )

    def __init__(
        self,
        *,
        process_id: int,
        window_handle: int,
        document_path: Path,
        document_id: int,
        state: SaveStateObserver,
        probe: MutationWatchdogProbe,
        event_reader: SaveEventReader | None = None,
        warning_seconds: float = 30.0,
        terminal_seconds: float = 180.0,
        poll_seconds: float = 0.1,
        poll_max_seconds: float = _WATCHDOG_MAX_POLL_SECONDS,
        started_at: float | None = None,
        after_event_sequence: int = 0,
        armed: bool = True,
    ) -> None:
        if process_id < 1:
            raise ValueError("watchdog process_id must be positive")
        if window_handle < 1:
            raise ValueError("watchdog window_handle must be positive")
        if document_id < 0:
            raise ValueError("watchdog document_id must be non-negative")
        if warning_seconds < 0:
            raise ValueError("watchdog warning_seconds must be non-negative")
        if terminal_seconds <= warning_seconds:
            raise ValueError(
                "watchdog terminal_seconds must be greater than warning_seconds"
            )
        if poll_seconds <= 0:
            raise ValueError("watchdog poll_seconds must be positive")
        if poll_max_seconds < poll_seconds:
            raise ValueError("watchdog maximum poll must cover the base poll")
        if after_event_sequence < 0:
            raise ValueError("watchdog after_event_sequence must be non-negative")
        try:
            baseline = document_path.stat() if armed else None
        except FileNotFoundError:
            self._baseline_file_exists: bool | None = False
            self._baseline_file_mtime_ns = None
            self._baseline_file_size = None
        except OSError:
            self._baseline_file_exists = None
            self._baseline_file_mtime_ns = None
            self._baseline_file_size = None
        else:
            self._baseline_file_exists = None if baseline is None else True
            self._baseline_file_mtime_ns = (
                None if baseline is None else baseline.st_mtime_ns
            )
            self._baseline_file_size = None if baseline is None else baseline.st_size
        self._arm_generation = 1 if armed else 0
        self._armed = armed
        self._document_id = document_id
        self._document_path = document_path
        self._event_reader = event_reader
        self._last_event_sequence = after_event_sequence
        self._latest: MutationWatchdogObservation | None = None
        self._lock = Lock()
        self._current_poll_seconds = poll_seconds
        self._poll_max_seconds = poll_max_seconds
        self._poll_seconds = poll_seconds
        self._probe = probe
        self._process_id = process_id
        self._started_at = monotonic() if started_at is None else started_at
        self._state = state
        self._stop = Event()
        self._terminal_seconds = terminal_seconds
        self._thread: Thread | None = None
        self._warning_seconds = warning_seconds
        self._window_handle = window_handle

    def arm(
        self,
        *,
        started_at: float | None = None,
        after_event_sequence: int | None = None,
    ) -> None:
        try:
            baseline = self._document_path.stat()
        except FileNotFoundError:
            exists: bool | None = False
            mtime_ns = None
            size = None
        except OSError:
            exists = None
            mtime_ns = None
            size = None
        else:
            exists = True
            mtime_ns = baseline.st_mtime_ns
            size = baseline.st_size
        with self._lock:
            self._arm_generation += 1
            self._armed = True
            self._baseline_file_exists = exists
            self._baseline_file_mtime_ns = mtime_ns
            self._baseline_file_size = size
            if after_event_sequence is not None:
                self._last_event_sequence = after_event_sequence
            self._started_at = monotonic() if started_at is None else started_at
            self._latest = None

    def _observe_events(self) -> None:
        reader = self._event_reader
        if reader is None:
            return
        events = reader(self._last_event_sequence)
        for event in events:
            self._last_event_sequence = max(
                self._last_event_sequence,
                event.sequence,
            )
            if event.document_id != self._document_id:
                continue
            if event.name == "DocumentBeforeSave":
                self._state.native_started(source=event.name)
            elif event.name == "DocumentAfterSave":
                self._state.progress(
                    source=event.name,
                    detail="native save event returned",
                )

    def observe(self, *, now: float | None = None) -> MutationWatchdogObservation:
        with self._lock:
            sampled_generation = self._arm_generation
        sample = self._probe.sample()
        with self._lock:
            observed_at = monotonic() if now is None else now
            correlate = self._armed and sampled_generation == self._arm_generation
            if correlate:
                self._observe_events()
            metadata_changed = correlate and (
                (
                    self._baseline_file_exists is False
                    and (
                        sample.file_size is not None or sample.file_mtime_ns is not None
                    )
                )
                or (
                    sample.file_size is not None
                    and self._baseline_file_size is not None
                    and sample.file_size != self._baseline_file_size
                )
                or (
                    sample.file_mtime_ns is not None
                    and self._baseline_file_mtime_ns is not None
                    and sample.file_mtime_ns != self._baseline_file_mtime_ns
                )
            )
            elapsed_seconds = (
                max(0.0, observed_at - self._started_at) if correlate else 0.0
            )
            warning = correlate and elapsed_seconds >= self._warning_seconds
            terminal = correlate and elapsed_seconds >= self._terminal_seconds
            detail = (
                f"elapsed_seconds={elapsed_seconds:.3f};"
                f"responding={sample.responding};"
                f"progress_present={sample.progress_control_present};"
                f"progress_visible={sample.progress_control_visible};"
                f"modal_present={sample.modal_present};"
                f"metadata_changed={metadata_changed}"
            )
            if warning and not terminal:
                self._state.heartbeat(source="watchdog", detail=detail)
            if correlate and (
                sample.progress_control_visible is True or metadata_changed
            ):
                self._state.progress(source="watchdog", detail=detail)
            if metadata_changed and isinstance(self._state, HwpLaneOperationContext):
                self._state.record_watchdog_metadata_change()
            if terminal:
                self._state.uncertain(source="watchdog", detail=detail)
            observation = MutationWatchdogObservation(
                process_id=self._process_id,
                window_handle=self._window_handle,
                document_path=self._document_path,
                document_id=self._document_id,
                elapsed_seconds=elapsed_seconds,
                process_alive=sample.process_alive,
                responding=sample.responding,
                progress_control_present=sample.progress_control_present,
                progress_control_visible=sample.progress_control_visible,
                modal_present=sample.modal_present,
                file_size=sample.file_size,
                file_mtime_ns=sample.file_mtime_ns,
                metadata_changed=metadata_changed,
                warning=warning,
                terminal_uncertain=terminal,
                last_event_sequence=self._last_event_sequence,
                poll_seconds=self._current_poll_seconds,
            )
            self._latest = observation
            return observation

    @property
    def poll_seconds(self) -> float:
        return self._poll_seconds

    def next_poll_seconds(
        self,
        *,
        now: float,
        probe_elapsed_seconds: float,
    ) -> float:
        if probe_elapsed_seconds < 0:
            raise ValueError("watchdog probe elapsed time must be non-negative")
        cadence = min(
            self._poll_max_seconds,
            max(self._poll_seconds, probe_elapsed_seconds * 2),
        )
        elapsed_seconds = max(0.0, now - self._started_at)
        boundaries = tuple(
            boundary - elapsed_seconds
            for boundary in (self._warning_seconds, self._terminal_seconds)
            if boundary > elapsed_seconds
        )
        return cadence if not boundaries else min(cadence, min(boundaries))

    def snapshot(self) -> MutationWatchdogObservation:
        if self._lock.acquire(blocking=False):
            try:
                latest = self._latest
            finally:
                self._lock.release()
            if latest is not None:
                return latest
        with self._lock:
            armed = self._armed
            started_at = self._started_at
        elapsed_seconds = max(0.0, monotonic() - started_at) if armed else 0.0
        return MutationWatchdogObservation(
            process_id=self._process_id,
            window_handle=self._window_handle,
            document_path=self._document_path,
            document_id=self._document_id,
            elapsed_seconds=elapsed_seconds,
            process_alive=None,
            responding=None,
            progress_control_present=None,
            progress_control_visible=None,
            modal_present=None,
            file_size=None,
            file_mtime_ns=None,
            metadata_changed=False,
            warning=elapsed_seconds >= self._warning_seconds,
            terminal_uncertain=elapsed_seconds >= self._terminal_seconds,
            last_event_sequence=self._last_event_sequence,
            poll_seconds=self._current_poll_seconds,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            probe_started = monotonic()
            try:
                observation = self.observe()
            except (OSError, RuntimeError, ValueError):
                _ = self._stop.wait(self._poll_seconds)
                continue
            probe_finished = monotonic()
            if observation.terminal_uncertain:
                return
            next_poll_seconds = self.next_poll_seconds(
                now=probe_finished,
                probe_elapsed_seconds=probe_finished - probe_started,
            )
            with self._lock:
                self._current_poll_seconds = next_poll_seconds
            _ = self._stop.wait(next_poll_seconds)

    def start(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                name=f"HancomWatchdog-PID-{self._process_id}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=min(0.25, self._poll_max_seconds))
        self._probe.close()
        if thread is None or not thread.is_alive():
            self._thread = None


class _PythonComModule(Protocol):
    def CoInitialize(self) -> None: ...

    def CoUninitialize(self) -> None: ...


def _pythoncom() -> _PythonComModule:
    return cast(
        _PythonComModule,
        cast(object, import_module("pythoncom")),
    )


def _initialize_sta() -> None:
    _pythoncom().CoInitialize()


def _uninitialize_sta() -> None:
    _pythoncom().CoUninitialize()


class _QueuedOperation(Protocol):
    def run(self) -> None: ...

    def cancel(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _FutureOperation(Generic[T]):
    operation: Callable[[], T]
    future: Future[T]
    context: HwpLaneOperationContext | None = None
    on_start: Callable[[], None] | None = None

    def run(self) -> None:
        if not self.future.set_running_or_notify_cancel():
            return
        previous_context = _LANE_THREAD_STATE.operation_context
        _LANE_THREAD_STATE.operation_context = self.context
        try:
            if self.on_start is not None:
                self.on_start()
            result = self.operation()
        except BaseException as error:
            self.future.set_exception(error)
        else:
            self.future.set_result(result)
        finally:
            _LANE_THREAD_STATE.operation_context = previous_context

    def cancel(self) -> None:
        _ = self.future.cancel()


@final
class HwpProcessLane:
    __slots__ = (
        "_capacity",
        "_closed",
        "_lock",
        "_poisoned",
        "_queue",
        "_worker",
        "process_id",
    )

    process_id: int
    _capacity: BoundedSemaphore
    _closed: bool
    _lock: Lock
    _poisoned: bool
    _queue: Queue[_QueuedOperation | None]
    _worker: Thread

    def __init__(self, process_id: int, queue_limit: int) -> None:
        self.process_id = process_id
        self._capacity = BoundedSemaphore(queue_limit)
        self._closed = False
        self._lock = Lock()
        self._poisoned = False
        self._queue = Queue()
        self._worker = Thread(
            name=f"HancomBridge-PID-{process_id}-STA_0",
            target=self._run,
            daemon=True,
        )
        self._worker.start()

    @property
    def poisoned(self) -> bool:
        with self._lock:
            return self._poisoned

    def acquire(self, timeout: float) -> bool:
        return self._capacity.acquire(timeout=timeout)

    def release(self) -> None:
        self._capacity.release()

    def submit(
        self,
        operation: Callable[[], T],
        *,
        context: HwpLaneOperationContext | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> Future[T]:
        with self._lock:
            if self._closed or self._poisoned:
                raise RuntimeError("HWP process lane is unavailable")
            future = Future[T]()
            self._queue.put(_FutureOperation(operation, future, context, on_start))
            return future

    def poison(self) -> None:
        with self._lock:
            if self._poisoned:
                return
            self._poisoned = True
            self._closed = True
            self._cancel_pending()
            self._queue.put(None)

    def shutdown(self, *, wait: bool) -> None:
        with self._lock:
            already_closed = self._closed
            self._closed = True
            if not already_closed:
                self._cancel_pending()
                self._queue.put(None)
        if wait:
            self._worker.join()

    def _cancel_pending(self) -> None:
        while True:
            try:
                queued = self._queue.get_nowait()
            except Empty:
                return
            if queued is None:
                continue
            queued.cancel()

    def _run(self) -> None:
        _initialize_sta()
        try:
            while True:
                queued = self._queue.get()
                if queued is None:
                    return
                queued.run()
        finally:
            _uninitialize_sta()

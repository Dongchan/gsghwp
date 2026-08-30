from __future__ import annotations

import subprocess
import time
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Final, IO, Protocol, assert_never, final

from pydantic import ValidationError

from hwp_errors import HwpLiveError
from hwp_live_events import (
    ChangeNotifier,
    ChangeObserver,
    HwpEventObservation,
    WinEventChangeSignal,
)
from hwp_live_native_contract import (
    NativeEvent,
    NativeMessage,
    NativeReady,
    is_native_message,
    parse_native_message,
)


_START_TIMEOUT_SECONDS: Final = 2.0
_STOP_TIMEOUT_SECONDS: Final = 1.0
_DEFAULT_EXECUTABLE: Final = (
    Path(__file__).resolve().parents[3]
    / "addon"
    / "HancomEventBridge"
    / "bin"
    / "Release"
    / "HancomEventBridge.exe"
)


class NativeBridgeProcess(Protocol):
    @property
    def stdin(self) -> IO[str] | None: ...

    @property
    def stdout(self) -> IO[str] | None: ...

    @property
    def stderr(self) -> IO[str] | None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class NativeBridgeSpawner(Protocol):
    def __call__(self, command: tuple[str, ...]) -> NativeBridgeProcess: ...


class EventSource(Protocol):
    def start(self, process_id: int, moniker_name: str | None = None) -> None: ...

    def stop(self) -> None: ...


def _spawn_native_bridge(command: tuple[str, ...]) -> NativeBridgeProcess:
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


@final
class NativeHwpEventSignal:
    __slots__ = (
        "_executable",
        "_failure",
        "_failure_lock",
        "_finished",
        "_notifier",
        "_process",
        "_ready",
        "_spawner",
        "_thread",
    )

    def __init__(
        self,
        *,
        notifier: ChangeNotifier,
        executable: Path = _DEFAULT_EXECUTABLE,
        spawner: NativeBridgeSpawner = _spawn_native_bridge,
    ) -> None:
        self._executable = executable
        self._failure: str | None = None
        self._failure_lock = Lock()
        self._finished = Event()
        self._notifier = notifier
        self._process: NativeBridgeProcess | None = None
        self._ready = Event()
        self._spawner = spawner
        self._thread: Thread | None = None

    def _set_failure(self, reason: str) -> None:
        with self._failure_lock:
            self._failure = reason

    def _failure_reason(self) -> str | None:
        with self._failure_lock:
            return self._failure

    def _read_messages(self, process: NativeBridgeProcess) -> None:
        output = process.stdout
        if output is None:
            self._set_failure("native bridge stdout pipe is unavailable")
            self._finished.set()
            return
        try:
            for line in output:
                if not line.strip():
                    continue
                try:
                    message: NativeMessage | None = parse_native_message(line)
                except ValidationError as error:
                    self._set_failure(f"native bridge message is invalid: {error}")
                    continue
                match message:
                    case _ as unreachable if not is_native_message(unreachable):
                        assert_never(unreachable)
                    case NativeReady():
                        self._ready.set()
                    case NativeEvent(
                        event=event_name,
                        document_id=document_id,
                    ):
                        self._notifier.notify(event_name, document_id)
        except (OSError, UnicodeError, ValueError) as error:
            self._set_failure(f"native bridge output failed: {error}")
        finally:
            self._finished.set()

    @staticmethod
    def _stderr(process: NativeBridgeProcess) -> str:
        error_output = process.stderr
        if error_output is None:
            return ""
        try:
            return error_output.read().strip()
        except (OSError, UnicodeError, ValueError):
            return ""

    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        self.stop()
        if process_id <= 0 or not moniker_name:
            return
        if not self._executable.is_file():
            raise HwpLiveError(
                f"한컴 네이티브 이벤트 브리지를 찾을 수 없습니다: {self._executable}"
            )
        command = (
            str(self._executable),
            "--moniker",
            moniker_name,
            "--process-id",
            str(process_id),
        )
        try:
            process = self._spawner(command)
        except OSError as error:
            raise HwpLiveError(
                f"한컴 네이티브 이벤트 브리지를 시작하지 못했습니다: {error}"
            ) from error
        self._process = process
        self._failure = None
        self._finished.clear()
        self._ready.clear()
        self._thread = Thread(
            target=self._read_messages,
            args=(process,),
            name="HancomBridge-NativeEvents",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while not self._ready.wait(0.02):
            if self._finished.is_set() or time.monotonic() >= deadline:
                break
        if self._ready.is_set():
            return
        failure = self._failure_reason()
        if process.poll() is not None:
            failure = self._stderr(process) or failure
        self.stop()
        reason = failure or "native bridge did not report ready before timeout"
        raise HwpLiveError(f"한컴 네이티브 이벤트 연결 실패: {reason}")

    def sequence(self) -> int:
        return self._notifier.sequence()

    def events_after(self, after_sequence: int) -> tuple[HwpEventObservation, ...]:
        return self._notifier.events_after(after_sequence)

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        return self._notifier.wait(after_sequence, timeout_seconds)

    def observe_window(
        self,
        window_handle: int,
        observer: ChangeObserver,
    ) -> None:
        self._notifier.observe_window(window_handle, observer)

    def forget_window(self, window_handle: int) -> None:
        self._notifier.forget_window(window_handle)

    def stop(self) -> None:
        process = self._process
        thread = self._thread
        if process is None:
            return
        if process.poll() is None:
            input_stream = process.stdin
            if input_stream is not None:
                try:
                    _ = input_stream.write("shutdown\n")
                    input_stream.flush()
                except (BrokenPipeError, OSError, ValueError) as error:
                    self._set_failure(f"native bridge shutdown input failed: {error}")
            try:
                _ = process.wait(timeout=_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    _ = process.wait(timeout=_STOP_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    _ = process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        if thread is not None:
            thread.join(timeout=_STOP_TIMEOUT_SECONDS)
        self._process = None
        self._thread = None


@final
class HybridChangeSignal:
    __slots__ = (
        "_native",
        "_native_failure",
        "_notifier",
        "_started",
        "_windows",
    )

    def __init__(
        self,
        *,
        window_signal: EventSource | None = None,
        native_signal: EventSource | None = None,
    ) -> None:
        self._notifier = ChangeNotifier()
        self._windows = (
            WinEventChangeSignal(self._notifier)
            if window_signal is None
            else window_signal
        )
        self._native = (
            NativeHwpEventSignal(notifier=self._notifier)
            if native_signal is None
            else native_signal
        )
        self._native_failure: str | None = None
        self._started = False

    @property
    def native_failure(self) -> str | None:
        return self._native_failure

    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        if self._started:
            self.stop()
        self._native_failure = None
        self._windows.start(process_id, moniker_name)
        try:
            self._native.start(process_id, moniker_name)
        except HwpLiveError as error:
            self._native_failure = str(error)
        self._started = True

    def sequence(self) -> int:
        return self._notifier.sequence()

    def events_after(self, after_sequence: int) -> tuple[HwpEventObservation, ...]:
        return self._notifier.events_after(after_sequence)

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        return self._notifier.wait(after_sequence, timeout_seconds)

    def observe_window(
        self,
        window_handle: int,
        observer: ChangeObserver,
    ) -> None:
        """Both sources share one notifier, so this covers native and window events."""
        self._notifier.observe_window(window_handle, observer)

    def forget_window(self, window_handle: int) -> None:
        self._notifier.forget_window(window_handle)

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._native.stop()
        finally:
            try:
                self._windows.stop()
            finally:
                self._started = False

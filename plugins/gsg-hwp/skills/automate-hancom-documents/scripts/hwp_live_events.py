from __future__ import annotations

from collections import deque
from ctypes import WINFUNCTYPE, WinDLL, wintypes
from dataclasses import dataclass
from importlib import import_module
from threading import Event, Lock, Thread
from time import monotonic_ns
from typing import Final, Protocol, final, runtime_checkable

from hwp_errors import HwpLiveError


_WIN_EVENT_CALLBACK = WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD,
)


class SetWinEventHookFunction(Protocol):
    def __call__(
        self,
        event_min: int,
        event_max: int,
        module_handle: int,
        callback: object,
        process_id: int,
        thread_id: int,
        flags: int,
    ) -> int: ...


class UnhookWinEventFunction(Protocol):
    def __call__(self, hook: int) -> bool: ...


@final
class WinEventApi:
    __slots__ = ("_set_hook", "_unhook")

    _set_hook: SetWinEventHookFunction
    _unhook: UnhookWinEventFunction

    def __init__(
        self,
        set_hook: SetWinEventHookFunction,
        unhook: UnhookWinEventFunction,
    ) -> None:
        self._set_hook = set_hook
        self._unhook = unhook

    def set_hook(self, callback: object, process_id: int) -> int:
        return self._set_hook(
            _EVENT_MIN,
            _EVENT_MAX,
            0,
            callback,
            process_id,
            0,
            _WINEVENT_OUTOFCONTEXT,
        )

    def unhook(self, hook: int) -> bool:
        return self._unhook(hook)


@runtime_checkable
class PythonComPump(Protocol):
    def PumpWaitingMessages(self) -> int: ...


class ChangeSignal(Protocol):
    def start(self, process_id: int, moniker_name: str | None = None) -> None: ...

    def sequence(self) -> int: ...

    def wait(self, after_sequence: int, timeout_seconds: float) -> int: ...

    def stop(self) -> None: ...


@runtime_checkable
class HwpEventSignal(Protocol):
    def events_after(self, after_sequence: int) -> tuple[HwpEventObservation, ...]: ...


_EVENT_HISTORY_LIMIT: Final = 256


@final
@dataclass(frozen=True, slots=True)
class HwpEventObservation:
    sequence: int
    name: str
    document_id: int | None
    observed_at_monotonic_ns: int


@final
class ChangeNotifier:
    __slots__ = ("_changed", "_events", "_lock", "_sequence")

    _changed: Event
    _events: deque[HwpEventObservation]
    _lock: Lock
    _sequence: int

    def __init__(self) -> None:
        self._changed = Event()
        self._events = deque(maxlen=_EVENT_HISTORY_LIMIT)
        self._lock = Lock()
        self._sequence = 0

    def notify(
        self,
        name: str | None = None,
        document_id: int | None = None,
    ) -> None:
        with self._lock:
            self._sequence += 1
            if name is not None:
                self._events.append(
                    HwpEventObservation(
                        sequence=self._sequence,
                        name=name,
                        document_id=document_id,
                        observed_at_monotonic_ns=monotonic_ns(),
                    )
                )
        self._changed.set()

    def sequence(self) -> int:
        with self._lock:
            return self._sequence

    def events_after(self, after_sequence: int) -> tuple[HwpEventObservation, ...]:
        with self._lock:
            return tuple(
                observation
                for observation in self._events
                if observation.sequence > after_sequence
            )

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        with self._lock:
            if self._sequence > after_sequence:
                return self._sequence
            self._changed.clear()
        _ = self._changed.wait(max(0.0, timeout_seconds))
        return self.sequence()


def _load_user32() -> WinEventApi:
    library = WinDLL("user32", use_last_error=True)
    raw_set_hook = library.SetWinEventHook
    raw_unhook = library.UnhookWinEvent
    raw_set_hook.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HMODULE,
        _WIN_EVENT_CALLBACK,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    raw_set_hook.restype = wintypes.HANDLE
    raw_unhook.argtypes = [wintypes.HANDLE]
    raw_unhook.restype = wintypes.BOOL
    set_hook: SetWinEventHookFunction = raw_set_hook
    unhook: UnhookWinEventFunction = raw_unhook
    return WinEventApi(set_hook, unhook)


def _load_message_pump() -> PythonComPump:
    module = import_module("pythoncom")
    if not isinstance(module, PythonComPump):
        raise HwpLiveError("Windows 이벤트 메시지 펌프를 찾을 수 없습니다")
    return module


_EVENT_MIN = 3
_EVENT_MAX = 0x800C
_RELEVANT_EVENTS = frozenset(
    {
        3,
        0x10,
        0x11,
        0x8001,
        0x8002,
        0x8003,
        0x8005,
        0x800C,
    }
)
_WINEVENT_OUTOFCONTEXT = 0


@final
class WinEventChangeSignal:
    __slots__ = (
        "_callback",
        "_notifier",
        "_process_id",
        "_pump",
        "_ready",
        "_stop",
        "_thread",
        "_unhook_failed",
        "_user32",
    )

    _callback: object | None
    _notifier: ChangeNotifier
    _process_id: int
    _pump: PythonComPump
    _ready: Event
    _stop: Event
    _thread: Thread | None
    _unhook_failed: bool
    _user32: WinEventApi

    def __init__(self, notifier: ChangeNotifier | None = None) -> None:
        self._user32 = _load_user32()
        self._pump = _load_message_pump()
        self._callback = None
        self._notifier = ChangeNotifier() if notifier is None else notifier
        self._process_id = 0
        self._ready = Event()
        self._stop = Event()
        self._thread = None
        self._unhook_failed = False

    def _on_event(
        self,
        hook: int,
        event: int,
        window_handle: int,
        object_id: int,
        child_id: int,
        event_thread: int,
        event_time: int,
    ) -> None:
        _ = (hook, window_handle, object_id, child_id, event_thread, event_time)
        if event not in _RELEVANT_EVENTS:
            return
        self._notifier.notify()

    def _run(self) -> None:
        callback = _WIN_EVENT_CALLBACK(self._on_event)
        self._callback = callback
        hook = self._user32.set_hook(callback, self._process_id)
        self._ready.set()
        if not hook:
            return
        try:
            while not self._stop.wait(0.02):
                _ = self._pump.PumpWaitingMessages()
        finally:
            self._unhook_failed = not self._user32.unhook(hook)
            self._callback = None

    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        _ = moniker_name
        self.stop()
        if process_id <= 0:
            return
        self._process_id = process_id
        self._ready.clear()
        self._stop.clear()
        self._unhook_failed = False
        self._thread = Thread(
            target=self._run,
            name="HancomBridge-WinEvent",
            daemon=True,
        )
        self._thread.start()
        _ = self._ready.wait(1.0)

    def sequence(self) -> int:
        return self._notifier.sequence()

    def events_after(self, after_sequence: int) -> tuple[HwpEventObservation, ...]:
        return self._notifier.events_after(after_sequence)

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        return self._notifier.wait(after_sequence, timeout_seconds)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
            if thread.is_alive():
                raise HwpLiveError(
                    "Windows 이벤트 감시 스레드를 안전하게 종료하지 못했습니다"
                )
        self._thread = None
        self._callback = None
        if self._unhook_failed:
            raise HwpLiveError("Windows 이벤트 훅을 안전하게 해제하지 못했습니다")

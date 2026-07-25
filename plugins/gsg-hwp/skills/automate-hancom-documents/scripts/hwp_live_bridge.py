from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections.abc import Callable
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from typing import Final, TypeVar, final, override

from hwp_errors import HwpLiveError
from hwp_live_bridge_contract import (
    BridgeSnapshot,
    BridgeState,
    HancomDialogDismissResult,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_bridge_diagnostic import popup_diagnostic
from hwp_live_bridge_mutation import HancomBridgeDocumentMutationMixin
from hwp_live_bridge_operation import HancomBridgeOperationMixin
from hwp_live_bridge_session import HancomBridgeSessionMixin
from hwp_live_contract import (
    ConnectedDocument,
    DocumentStyleList,
    LiveContext,
    PreviewResult,
)
from hwp_live_events import ChangeSignal
from hwp_live_native_events import HybridChangeSignal
from hwp_live_session import LiveHwpController
from hwp_live_state_cache import HancomStateCache
from hwp_live_structure_contract import (
    DocumentStructure,
    FastPageInspection,
)
from hwp_live_windows import Win32WindowStateReader, WindowStateReader


T = TypeVar("T")
_FAST_INSPECTION_CACHE_LIMIT: Final = 32
_DEFAULT_CALL_TIMEOUT_SECONDS: Final = 180.0
_DEFAULT_PROCESS_QUEUE_LIMIT: Final = 8
_RPC_E_CALL_REJECTED: Final = -2_147_418_111
_RPC_E_SERVERCALL_RETRYLATER: Final = -2_147_417_846
_RPC_E_SERVERCALL_REJECTED: Final = -2_147_417_845
_COM_BUSY_HRESULTS: Final = frozenset(
    {
        _RPC_E_CALL_REJECTED,
        _RPC_E_SERVERCALL_RETRYLATER,
        _RPC_E_SERVERCALL_REJECTED,
    }
)
_COM_BUSY_DELAYS: Final = (0.025, 0.05, 0.1)


@dataclass(frozen=True, slots=True)
class _FastInspectionCacheEntry:
    sequence: int
    inspection: FastPageInspection


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _is_com_busy_error(error: BaseException) -> bool:
    signed = {str(value) for value in _COM_BUSY_HRESULTS}
    unsigned = {str(value & 0xFFFFFFFF) for value in _COM_BUSY_HRESULTS}
    hexadecimal = {f"0x{value & 0xFFFFFFFF:08x}" for value in _COM_BUSY_HRESULTS}
    for item in _exception_chain(error):
        hresult = getattr(item, "hresult", None)
        if isinstance(hresult, int) and (
            hresult in _COM_BUSY_HRESULTS
            or hresult - 0x1_0000_0000 in _COM_BUSY_HRESULTS
        ):
            return True
        message = str(item).lower()
        if any(token in message for token in signed | unsigned | hexadecimal):
            return True
        if "call was rejected by callee" in message or "server busy" in message:
            return True
    return False


def _deadline_error(*, mutation: bool, phase: str, isolation: bool) -> HwpLiveError:
    reconcile = mutation and phase == "running"
    return HwpLiveError(
        "".join(
            (
                "한컴 COM 전체 제한시간을 초과했습니다",
                f"; phase={phase}",
                f"; worker_isolation_required={'true' if isolation else 'false'}",
                f"; reconcile_required={'true' if reconcile else 'false'}",
                f"; retry_safe={'false' if reconcile else 'true'}",
            )
        )
    )


@final
class HancomBridge(
    HancomBridgeSessionMixin,
    HancomBridgeOperationMixin,
    HancomBridgeDocumentMutationMixin,
):
    __slots__: tuple[str, ...] = (
        "_cache",
        "_closed",
        "_controller",
        "_call_timeout_seconds",
        "_events",
        "_executor",
        "_fast_inspections",
        "_initial_event_signal",
        "_lifecycle_lock",
        "_process_sessions",
        "_process_queues",
        "_process_queue_limit",
        "_poisoned",
        "_session_processes",
        "_windows",
    )

    _cache: HancomStateCache
    _closed: bool
    _controller: LiveHwpController
    _call_timeout_seconds: float
    _events: dict[int, ChangeSignal]
    _executor: ThreadPoolExecutor
    _fast_inspections: dict[tuple[str, int, bool], _FastInspectionCacheEntry]
    _initial_event_signal: ChangeSignal | None
    _lifecycle_lock: Lock
    _process_sessions: dict[int, set[str]]
    _process_queues: dict[int, BoundedSemaphore]
    _process_queue_limit: int
    _poisoned: bool
    _session_processes: dict[str, int]
    _windows: WindowStateReader

    def __init__(
        self,
        controller: LiveHwpController,
        window_reader: WindowStateReader | None = None,
        change_signal: ChangeSignal | None = None,
        *,
        call_timeout_seconds: float = _DEFAULT_CALL_TIMEOUT_SECONDS,
        process_queue_limit: int = _DEFAULT_PROCESS_QUEUE_LIMIT,
    ) -> None:
        if call_timeout_seconds <= 0:
            raise ValueError("한컴 COM 제한시간은 양수여야 합니다")
        if process_queue_limit < 1:
            raise ValueError("한컴 프로세스 큐 한도는 1 이상이어야 합니다")
        self._controller = controller
        self._call_timeout_seconds = call_timeout_seconds
        self._closed = False
        self._poisoned = False
        self._lifecycle_lock = Lock()
        self._cache = HancomStateCache()
        self._fast_inspections = {}
        self._events = {}
        self._initial_event_signal = change_signal
        self._process_sessions = {}
        self._process_queues = {}
        self._process_queue_limit = process_queue_limit
        self._session_processes = {}
        self._windows = (
            Win32WindowStateReader() if window_reader is None else window_reader
        )
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="HancomBridge-STA",
        )

    @override
    def _call(
        self,
        operation: Callable[[], T],
        *,
        session_id: str | None = None,
        process_id: int | None = None,
        mutation: bool = False,
    ) -> T:
        def invoke() -> T:
            delays = () if mutation else _COM_BUSY_DELAYS
            for delay in (*delays, None):
                try:
                    return operation()
                except BaseException as error:
                    if delay is None or not _is_com_busy_error(error):
                        raise
                finally:
                    self._controller.restore_activation()
                time.sleep(delay)
            raise AssertionError("unreachable COM retry state")

        started = time.monotonic()
        with self._lifecycle_lock:
            if self._closed:
                raise HwpLiveError("한컴 브리지가 이미 종료되었습니다")
            if self._poisoned:
                raise _deadline_error(
                    mutation=mutation,
                    phase="running",
                    isolation=True,
                )
            queue_key = (
                process_id
                if process_id is not None
                else self._session_processes.get(session_id or "", 0)
            )
            queue = self._process_queues.setdefault(
                queue_key,
                BoundedSemaphore(self._process_queue_limit),
            )
        remaining = self._call_timeout_seconds - (time.monotonic() - started)
        if remaining <= 0 or not queue.acquire(timeout=max(0.0, remaining)):
            raise _deadline_error(
                mutation=mutation,
                phase="queue_wait",
                isolation=False,
            )
        future: Future[T]
        try:
            with self._lifecycle_lock:
                if self._closed:
                    raise HwpLiveError("한컴 브리지가 이미 종료되었습니다")
                if self._poisoned:
                    raise _deadline_error(
                        mutation=mutation,
                        phase="running",
                        isolation=True,
                    )
                future = self._executor.submit(invoke)
        except BaseException:
            queue.release()
            raise
        future.add_done_callback(lambda _: queue.release())
        try:
            remaining = self._call_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise FutureTimeoutError
            return future.result(timeout=remaining)
        except FutureTimeoutError:
            if future.done():
                return future.result()
            queued = future.cancel()
            if not queued:
                with self._lifecycle_lock:
                    self._poisoned = True
            raise _deadline_error(
                mutation=mutation,
                phase="queued" if queued else "running",
                isolation=not queued,
            )
        except HwpLiveError as error:
            if type(error) is not HwpLiveError or "worker_isolation_required=true" in error.reason:
                raise
            popup = popup_diagnostic(self._windows)
            if popup is None:
                raise
            raise HwpLiveError(
                f"{error.reason}; 감지된 한컴 오류 창: {popup}"
            ) from error

    @override
    def _bridge_controller(self) -> LiveHwpController:
        return self._controller

    @override
    def _bridge_process_id(self, window_handle: int) -> int:
        return self._windows.read(window_handle).process_id

    @override
    def _call_mutation(
        self,
        operation: Callable[[], T],
        *,
        session_id: str | None = None,
        process_id: int | None = None,
    ) -> T:
        def invoke() -> T:
            self._fast_inspections.clear()
            try:
                return operation()
            finally:
                self._fast_inspections.clear()

        return self._call(
            invoke,
            session_id=session_id,
            process_id=process_id,
            mutation=True,
        )

    @override
    def _activate_connection(self, connected: ConnectedDocument) -> None:
        self._cache.clear()
        self._fast_inspections.clear()
        window = self._windows.read(connected.document.window_handle)
        session_id = connected.session_id
        if session_id in self._session_processes:
            return
        moniker_name = self._controller.connection_moniker(connected.session_id)
        process_id = window.process_id
        signal = self._events.get(process_id)
        if signal is None:
            signal = self._initial_event_signal
            if signal is None:
                signal = HybridChangeSignal()
            else:
                self._initial_event_signal = None
            try:
                signal.start(process_id, moniker_name)
            except (HwpLiveError, OSError, RuntimeError, ValueError):
                if not self._events and self._initial_event_signal is None:
                    self._initial_event_signal = signal
                raise
            self._events[process_id] = signal
        self._session_processes[session_id] = process_id
        self._process_sessions.setdefault(process_id, set()).add(session_id)

    @override
    def _clear_connection_state(self, session_id: str | None = None) -> None:
        try:
            if session_id is None:
                self._stop_all_events()
                return
            process_id = self._session_processes.pop(session_id, None)
            if process_id is None:
                return
            sessions = self._process_sessions.get(process_id)
            if sessions is not None:
                sessions.discard(session_id)
            if sessions:
                return
            _ = self._process_sessions.pop(process_id, None)
            signal = self._events.pop(process_id, None)
            if signal is not None:
                signal.stop()
        finally:
            self._cache.clear()
            self._fast_inspections.clear()

    def _stop_all_events(self) -> None:
        signals = tuple(self._events.values())
        self._events.clear()
        self._process_sessions.clear()
        self._session_processes.clear()
        for signal in signals:
            signal.stop()

    def _event_signal(self, session_id: str) -> ChangeSignal:
        process_id = self._session_processes.get(session_id)
        signal = None if process_id is None else self._events.get(process_id)
        if signal is None:
            raise HwpLiveError("한컴 문서 세션의 프로세스 이벤트 감시가 없습니다")
        return signal

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            cleanup = (
                None
                if self._poisoned
                else self._executor.submit(self._controller.close)
            )
        try:
            if cleanup is not None:
                try:
                    cleanup.result(timeout=self._call_timeout_seconds)
                except FutureTimeoutError:
                    self._poisoned = True
        finally:
            try:
                self._stop_all_events()
            finally:
                self._cache.clear()
                self._fast_inspections.clear()
                self._process_queues.clear()
                self._executor.shutdown(
                    wait=not self._poisoned,
                    cancel_futures=True,
                )

    def _inspect_and_refresh(
        self,
        session_id: str,
        page: int,
        after_revision: int,
    ) -> BridgeState:
        context, structure, window_handle = self._controller.inspect_state(
            session_id,
            page,
        )
        snapshot = BridgeSnapshot(
            context=context,
            structure=structure,
            window=self._windows.read(window_handle),
        )
        return self._cache.refresh(snapshot, after_revision)

    def window_state(self, window_handle: int) -> HancomWindowState:
        if window_handle < 1:
            raise HwpLiveError("한컴 창 핸들은 1 이상이어야 합니다")
        return self._windows.read(window_handle)

    def list_window_states(self) -> HancomWindowStateList:
        return self._windows.list_visible_hwp_windows()

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        if window_handle < 1:
            raise HwpLiveError("한컴 창 핸들은 1 이상이어야 합니다")
        return self._call(
            lambda: self._windows.dismiss_dialogs(window_handle),
            process_id=self._bridge_process_id(window_handle),
        )

    def context(self, session_id: str) -> LiveContext:
        return self._call(
            lambda: self._controller.context(session_id),
            session_id=session_id,
        )

    def styles(self, session_id: str) -> DocumentStyleList:
        return self._call(
            lambda: self._controller.styles(session_id),
            session_id=session_id,
        )

    def structure(self, session_id: str, page: int) -> DocumentStructure:
        return self._call(
            lambda: self._controller.structure(session_id, page),
            session_id=session_id,
        )

    def inspect_page_fast(
        self,
        session_id: str,
        page: int,
        *,
        include_cells: bool = False,
    ) -> FastPageInspection:
        def inspect() -> FastPageInspection:
            key = (session_id, page, include_cells)
            signal = self._event_signal(session_id)
            sequence = signal.sequence()
            cached = self._fast_inspections.get(key)
            if cached is not None and cached.sequence == sequence:
                if signal.sequence() == sequence:
                    return cached.inspection
            inspection = self._controller.inspect_page_fast(
                session_id,
                page,
                include_cells=include_cells,
            )
            if (
                key not in self._fast_inspections
                and len(self._fast_inspections) >= _FAST_INSPECTION_CACHE_LIMIT
            ):
                self._fast_inspections.clear()
            self._fast_inspections[key] = _FastInspectionCacheEntry(
                sequence=signal.sequence(),
                inspection=inspection,
            )
            return inspection

        return self._call(inspect, session_id=session_id)

    def has_cached_fast_inspections(self) -> bool:
        return bool(self._fast_inspections)

    def watch_state(
        self,
        session_id: str,
        page: int,
        after_revision: int,
        timeout_ms: int,
    ) -> BridgeState:
        if after_revision < 0:
            raise HwpLiveError("브리지 revision은 0 이상이어야 합니다")
        if timeout_ms < 0 or timeout_ms > 30_000:
            raise HwpLiveError("브리지 대기 시간은 0~30000ms여야 합니다")
        deadline = time.monotonic() + timeout_ms / 1_000
        signal = self._event_signal(session_id)
        while True:
            poll_started = time.monotonic()
            sequence = signal.sequence()
            state = self._call(
                lambda: self._inspect_and_refresh(
                    session_id,
                    page,
                    after_revision,
                ),
                session_id=session_id,
            )
            if state.revision > after_revision or time.monotonic() >= deadline:
                return state
            if signal.sequence() > sequence:
                continue
            next_poll = min(deadline, poll_started + 0.25)
            remaining = max(0.0, next_poll - time.monotonic())
            _ = signal.wait(sequence, remaining)

    def render_page(self, session_id: str, page: int, dpi: int) -> PreviewResult:
        return self._call(
            lambda: self._controller.render_page(session_id, page, dpi),
            session_id=session_id,
        )

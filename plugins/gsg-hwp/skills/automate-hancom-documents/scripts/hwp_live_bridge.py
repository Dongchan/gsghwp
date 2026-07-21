from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Final, TypeVar, final, override

from hwp_errors import HwpLiveError
from hwp_live_bridge_contract import (
    BridgeSnapshot,
    BridgeState,
    HancomDialogDismissResult,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_bridge_mutation import HancomBridgeDocumentMutationMixin
from hwp_live_bridge_operation import HancomBridgeOperationMixin
from hwp_live_contract import (
    ConnectedDocument,
    DocumentStyleList,
    LiveContext,
    MutationResult,
    OpenDocumentList,
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


@dataclass(frozen=True, slots=True)
class _FastInspectionCacheEntry:
    sequence: int
    inspection: FastPageInspection


@final
class HancomBridge(HancomBridgeOperationMixin, HancomBridgeDocumentMutationMixin):
    __slots__: tuple[str, ...] = (
        "_cache",
        "_closed",
        "_connected_session_id",
        "_connected_window_handle",
        "_controller",
        "_events",
        "_executor",
        "_fast_inspections",
        "_lifecycle_lock",
        "_windows",
    )

    _cache: HancomStateCache
    _closed: bool
    _connected_session_id: str | None
    _connected_window_handle: int | None
    _controller: LiveHwpController
    _events: ChangeSignal
    _executor: ThreadPoolExecutor
    _fast_inspections: dict[tuple[str, int, bool], _FastInspectionCacheEntry]
    _lifecycle_lock: Lock
    _windows: WindowStateReader

    def __init__(
        self,
        controller: LiveHwpController,
        window_reader: WindowStateReader | None = None,
        change_signal: ChangeSignal | None = None,
    ) -> None:
        self._controller = controller
        self._closed = False
        self._connected_session_id = None
        self._connected_window_handle = None
        self._lifecycle_lock = Lock()
        self._cache = HancomStateCache()
        self._fast_inspections = {}
        self._events = (
            HybridChangeSignal() if change_signal is None else change_signal
        )
        self._windows = (
            Win32WindowStateReader() if window_reader is None else window_reader
        )
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="HancomBridge-STA",
        )

    def _call(self, operation: Callable[[], T]) -> T:
        with self._lifecycle_lock:
            if self._closed:
                raise HwpLiveError("한컴 브리지가 이미 종료되었습니다")
            future = self._executor.submit(operation)
        try:
            return future.result()
        except HwpLiveError as error:
            popup = self._popup_diagnostic()
            if popup is None:
                raise
            raise HwpLiveError(
                f"{error.reason}; 감지된 한컴 오류 창: {popup}"
            ) from error

    def _popup_diagnostic(self) -> str | None:
        try:
            windows = self._windows.list_visible_hwp_windows().windows
        except (HwpLiveError, OSError, RuntimeError):
            return None
        diagnostics: list[str] = []
        for window in windows:
            if window.class_name != "#32770" and not window.dialogs:
                continue
            texts = tuple(
                dict.fromkeys(
                    text.strip()
                    for text in (
                        window.title,
                        *(child.title for child in window.children),
                    )
                    if text.strip()
                )
            )
            detail = " | ".join(texts[:8])
            diagnostics.append(
                f"HWND={window.window_handle}, class={window.class_name}, text={detail}"
            )
        return "; ".join(diagnostics) or None

    @override
    def _bridge_controller(self) -> LiveHwpController:
        return self._controller

    @override
    def _call_mutation(self, operation: Callable[[], T]) -> T:
        def invoke() -> T:
            self._fast_inspections.clear()
            try:
                return operation()
            finally:
                self._fast_inspections.clear()

        return self._call(invoke)

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            cleanup = self._executor.submit(self._controller.close)
        try:
            cleanup.result()
        finally:
            try:
                self._events.stop()
            finally:
                self._cache.clear()
                self._fast_inspections.clear()
                self._executor.shutdown(wait=True, cancel_futures=True)

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

    def list_open_documents(self) -> OpenDocumentList:
        return self._call(self._controller.list_open_documents)

    def connect(self, selector: str) -> ConnectedDocument:
        def operation() -> ConnectedDocument:
            connected = self._controller.connect(selector)
            try:
                self._cache.clear()
                self._fast_inspections.clear()
                window = self._windows.read(connected.document.window_handle)
                moniker_name = self._controller.connection_moniker(connected.session_id)
                self._events.start(window.process_id, moniker_name)
                self._connected_session_id = connected.session_id
                self._connected_window_handle = connected.document.window_handle
            except (HwpLiveError, OSError, RuntimeError, ValueError):
                try:
                    _ = self._controller.disconnect(connected.session_id)
                finally:
                    self._cache.clear()
                    self._events.stop()
                raise
            return connected

        return self._call(operation)

    def window_state(self, window_handle: int) -> HancomWindowState:
        if window_handle < 1:
            raise HwpLiveError("한컴 창 핸들은 1 이상이어야 합니다")
        return self._windows.read(window_handle)

    def list_window_states(self) -> HancomWindowStateList:
        return self._windows.list_visible_hwp_windows()

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        if window_handle < 1:
            raise HwpLiveError("한컴 창 핸들은 1 이상이어야 합니다")
        return self._call(lambda: self._windows.dismiss_dialogs(window_handle))

    def context(self, session_id: str) -> LiveContext:
        return self._call(lambda: self._controller.context(session_id))

    def styles(self, session_id: str) -> DocumentStyleList:
        return self._call(lambda: self._controller.styles(session_id))

    def structure(self, session_id: str, page: int) -> DocumentStructure:
        return self._call(lambda: self._controller.structure(session_id, page))

    def inspect_page_fast(
        self,
        session_id: str,
        page: int,
        *,
        include_cells: bool = False,
    ) -> FastPageInspection:
        def inspect() -> FastPageInspection:
            key = (session_id, page, include_cells)
            sequence = self._events.sequence()
            cached = self._fast_inspections.get(key)
            if cached is not None and cached.sequence == sequence:
                if self._events.sequence() == sequence:
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
                sequence=self._events.sequence(),
                inspection=inspection,
            )
            return inspection

        return self._call(inspect)

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
        while True:
            poll_started = time.monotonic()
            sequence = self._events.sequence()
            state = self._call(
                lambda: self._inspect_and_refresh(
                    session_id,
                    page,
                    after_revision,
                )
            )
            if state.revision > after_revision or time.monotonic() >= deadline:
                return state
            if self._events.sequence() > sequence:
                continue
            next_poll = min(deadline, poll_started + 0.25)
            remaining = max(0.0, next_poll - time.monotonic())
            _ = self._events.wait(sequence, remaining)

    def render_page(self, session_id: str, page: int, dpi: int) -> PreviewResult:
        return self._call(lambda: self._controller.render_page(session_id, page, dpi))

    def disconnect(self, session_id: str) -> MutationResult:
        def operation() -> MutationResult:
            result = self._controller.disconnect(session_id)
            self._connected_session_id = None
            self._connected_window_handle = None
            self._cache.clear()
            self._fast_inspections.clear()
            self._events.stop()
            return result

        return self._call(operation)

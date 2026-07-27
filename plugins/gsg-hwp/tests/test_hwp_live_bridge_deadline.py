from __future__ import annotations

import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread, current_thread, enumerate as enumerate_threads
from time import monotonic, sleep
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

from hwp_errors import (  # noqa: E402
    HwpLiveError,
    is_hwp_target_process_lost_error,
)
from hwp_live_bridge import HancomBridge, operation_recovery_scope  # noqa: E402
from hwp_live_bridge_contract import (  # noqa: E402
    HancomDialogDismissResult,
    HancomDialogState,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_contract import (  # noqa: E402
    ConnectedDocument,
    LiveContext,
    MutationResult,
    OpenDocument,
    OpenDocumentList,
)
from hwp_live_process_lane import (  # noqa: E402
    HwpLaneOperationContext,
    HwpProcessLane,
    current_lane_operation_context,
)
from hwp_live_native_action_contract import (  # noqa: E402
    NativeActionFailure,
    NativeActionFailureEvidence,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_session_lifecycle import SaveStateMachine  # noqa: E402
from hwp_live_session_lifecycle import save_preflight_result  # noqa: E402
from hwp_live_session_types import LiveSessionReference  # noqa: E402
from hwp_live_windows import WindowStateReader  # noqa: E402


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


def _call_for_session(
    bridge: HancomBridge,
    operation: Callable[[], T],
    *,
    session_id: str,
    mutation: bool = False,
) -> T:
    method_name = "_call_mutation" if mutation else "_call"
    method = cast(Callable[..., T], getattr(bridge, method_name))
    return method(operation, session_id=session_id)


@final
class _Controller:
    def __init__(
        self,
        *,
        fail_close: bool = False,
        lifecycle: list[str] | None = None,
    ) -> None:
        self.close_calls = 0
        self.fail_close = fail_close
        self.invalidate_calls = 0
        self.lifecycle = lifecycle
        self.restore_calls = 0

    def restore_activation(self) -> None:
        self.restore_calls += 1

    def close(self) -> None:
        self.close_calls += 1
        if self.lifecycle is not None:
            self.lifecycle.append("controller.close")
        if self.fail_close:
            raise HwpLiveError("closed HWP process")

    def invalidate_process_loss(self) -> None:
        self.invalidate_calls += 1


@final
class _DisconnectController:
    def __init__(self, session_id: str | None = "safe-session") -> None:
        self.close_calls = 0
        self.connected_document: ConnectedDocument | None = None
        self.context_calls = 0
        self.disconnect_calls: list[str] = []
        self.recovery_probe_calls = 0
        self.session_id = session_id

    def close(self) -> None:
        self.close_calls += 1

    def current_session(self) -> LiveSessionReference | None:
        if self.session_id is None:
            return None
        selector = (
            "safe"
            if self.connected_document is None
            else self.connected_document.document.selector
        )
        return LiveSessionReference(session_id=self.session_id, selector=selector)

    def session_for_selector(
        self,
        selector: str,
    ) -> LiveSessionReference | None:
        current = self.current_session()
        if current is None or current.selector != selector:
            return None
        return current

    def connect_deferred(self, selector: str) -> ConnectedDocument:
        self.recovery_probe_calls += 1
        connected = self.connected_document
        if connected is None or connected.document.selector != selector:
            raise HwpLiveError("known document unavailable")
        return connected

    def disconnect(self, session_id: str) -> MutationResult:
        self.disconnect_calls.append(session_id)
        return MutationResult(action="disconnect", current_page=1, modified=False)

    def context(self, session_id: str) -> LiveContext:
        self.context_calls += 1
        return cast(LiveContext, cast(object, f"readback:{session_id}"))

    def restore_activation(self) -> None:
        return

    def invalidate_process_loss(self) -> None:
        return


@final
class _KnownStaleController:
    def __init__(self) -> None:
        self.list_calls = 0

    def session_for_selector(self, selector: str) -> None:
        _ = selector
        return None

    def current_session(self) -> None:
        return None

    def list_open_documents(self) -> OpenDocumentList:
        self.list_calls += 1
        return OpenDocumentList(documents=())

    def restore_activation(self) -> None:
        return

    def invalidate_process_loss(self) -> None:
        return

    def close(self) -> None:
        return


@final
class _OrderedSignal:
    def __init__(self, lifecycle: list[str], *, fail_stop: bool = False) -> None:
        self._fail_stop = fail_stop
        self._lifecycle = lifecycle

    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        _ = process_id, moniker_name

    def sequence(self) -> int:
        return 0

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        _ = after_sequence, timeout_seconds
        return 0

    def stop(self) -> None:
        self._lifecycle.append("event.stop")
        if self._fail_stop:
            raise HwpLiveError("event stop failed")


@final
class _BusyError(RuntimeError):
    def __init__(self, hresult: int) -> None:
        self.hresult = hresult
        super().__init__(f"COM failure {hresult}")


@final
class _ExitWatch:
    def __init__(self, exited: Event) -> None:
        self.closed = False
        self._exited = exited

    def exited(self) -> bool:
        return self._exited.is_set()

    def close(self) -> None:
        self.closed = True


def _exit_watch_factory(state: Event) -> Callable[[int], _ExitWatch]:
    def create(process_id: int) -> _ExitWatch:
        _ = process_id
        return _ExitWatch(state)

    return create


@final
class _DiagnosticWindowReader:
    def __init__(self, windows: tuple[HancomWindowState, ...]) -> None:
        self.dismiss_calls = 0
        self._windows = windows

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        return HancomWindowStateList(windows=self._windows)

    def read(self, window_handle: int) -> HancomWindowState:
        return next(
            window for window in self._windows if window.window_handle == window_handle
        )

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        _ = window_handle
        self.dismiss_calls += 1
        raise AssertionError("production diagnostics must not dismiss dialogs")


@pytest.mark.parametrize(
    "hresult",
    (
        -2_147_023_174,
        -2_147_220_995,
        -2_147_417_848,
        0x800706BA,
        0x800401FD,
        0x80010108,
    ),
)
def test_process_loss_hresult_family_is_not_classified_as_com_busy(
    hresult: int,
) -> None:
    error = _BusyError(hresult)

    assert is_hwp_target_process_lost_error(error) is True
    assert is_hwp_target_process_lost_error(_BusyError(-2_147_417_846)) is False


@final
class _OpenController:
    def __init__(self) -> None:
        self.restore_calls = 0
        self.open_thread = ""
        self.document = OpenDocument(
            selector="doc:11:ready.hwp",
            title="ready.hwp",
            full_name=r"C:\qa\ready.hwp",
            document_id=11,
            format="HWP",
            edit_mode=1,
            modified=False,
            page_count=1,
            active=True,
            window_handle=7001,
        )

    def restore_activation(self) -> None:
        self.restore_calls += 1

    def close(self) -> None:
        return

    def list_open_documents(self) -> OpenDocumentList:
        return OpenDocumentList(documents=(self.document,))

    def open_document(
        self,
        path: str,
        reference_selector: str | None,
        new_tab: bool,
        restore_reference: bool = True,
    ) -> OpenDocument:
        assert path == r"C:\qa\new.hwp"
        assert reference_selector == self.document.selector
        assert new_tab is True
        assert restore_reference is True
        self.open_thread = current_thread().name
        return self.document


@final
class _OpenWindowReader:
    def read(self, window_handle: int) -> HancomWindowState:
        assert window_handle == 7001
        return HancomWindowState(
            window_handle=window_handle,
            process_id=909,
            exists=True,
            visible=True,
            enabled=True,
            foreground=True,
            title="ready.hwp",
            class_name="HwpMain",
            dialogs=(),
        )


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


def test_open_document_routes_to_the_reference_hwp_process_lane() -> None:
    controller = _OpenController()
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(WindowStateReader, cast(object, _OpenWindowReader())),
        call_timeout_seconds=1,
    )
    try:
        opened = bridge.open_document(
            r"C:\qa\new.hwp",
            controller.document.selector,
            True,
        )
        assert opened == controller.document
        assert controller.open_thread.startswith("HancomBridge-PID-909-STA")
    finally:
        bridge.close()


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


def test_close_is_blocked_before_cleanup_while_save_is_executing() -> None:
    controller = _Controller()
    bridge = _bridge(controller)
    context = HwpLaneOperationContext(
        session_id="saving-session",
        process_id=71,
        window_handle=701,
        document_path=Path("C:/qa/copy.hwp"),
        document_id=17,
    )
    state = SaveStateMachine()
    state.native_started(source="DocumentBeforeSave")
    _ = context.attach_save_state(state)
    active = cast(
        dict[int, HwpLaneOperationContext],
        getattr(bridge, "_active_mutation_contexts"),
    )
    active[id(context)] = context

    with pytest.raises(HwpLiveError) as blocked:
        bridge.close()

    assert "MCP 자동 연결 해제만 보류" in blocked.value.reason
    assert "save_state=native_started" in blocked.value.reason
    assert "save_close_blocked=true" in blocked.value.reason
    assert "retry_safe=false" in blocked.value.reason
    assert controller.close_calls == 0
    assert getattr(bridge, "_closed") is False

    state.metadata_unchanged(source="test_reconcile")
    state.verified(source="test_reconcile")
    bridge.close()
    assert controller.close_calls == 1


def test_disconnect_blocks_only_the_session_with_uncertain_save() -> None:
    controller = _DisconnectController()
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        call_timeout_seconds=1,
    )
    uncertain = SaveStateMachine()
    uncertain.native_started(source="DocumentBeforeSave")
    uncertain.uncertain(source="com_deadline")
    uncertain_states = cast(
        dict[str, SaveStateMachine],
        getattr(bridge, "_uncertain_save_states"),
    )
    uncertain_states["saving-session"] = uncertain
    session_processes = cast(
        dict[str, int],
        getattr(bridge, "_session_processes"),
    )
    session_processes["safe-session"] = 0

    try:
        with pytest.raises(HwpLiveError) as blocked:
            _ = bridge.disconnect("saving-session")
        assert "MCP 자동 연결 해제만 보류" in blocked.value.reason
        assert "save_state=uncertain" in blocked.value.reason
        assert controller.disconnect_calls == []

        result = bridge.disconnect("safe-session")
        assert result.action == "disconnect"
        assert controller.disconnect_calls == ["safe-session"]
    finally:
        _ = uncertain_states.pop("saving-session", None)
        bridge.close()


def test_disconnect_rechecks_save_guard_inside_the_process_lane() -> None:
    controller = _DisconnectController()
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        call_timeout_seconds=1,
    )
    session_processes = cast(
        dict[str, int],
        getattr(bridge, "_session_processes"),
    )
    process_sessions = cast(
        dict[int, set[str]],
        getattr(bridge, "_process_sessions"),
    )
    uncertain_states = cast(
        dict[str, SaveStateMachine],
        getattr(bridge, "_uncertain_save_states"),
    )
    session_processes["saving-session"] = 71
    process_sessions[71] = {"saving-session"}
    blocker_started = Event()
    release_blocker = Event()

    def block_lane() -> None:
        blocker_started.set()
        _ = release_blocker.wait(1)

    uncertain = SaveStateMachine()
    uncertain.native_started(source="native_dispatch")
    uncertain.uncertain(source="test")

    try:
        with ThreadPoolExecutor(max_workers=2) as calls:
            blocker = calls.submit(
                _call_for_session,
                bridge,
                block_lane,
                session_id="saving-session",
            )
            assert blocker_started.wait(1)
            disconnect = calls.submit(bridge.disconnect, "saving-session")
            sleep(0.05)
            uncertain_states["saving-session"] = uncertain
            release_blocker.set()
            blocker.result(timeout=1)
            with pytest.raises(HwpLiveError, match="MCP 자동 연결 해제만 보류"):
                _ = disconnect.result(timeout=1)
        assert controller.disconnect_calls == []
    finally:
        release_blocker.set()
        _ = uncertain_states.pop("saving-session", None)
        bridge.close()


def test_disconnect_without_id_uses_unique_per_process_current_session() -> None:
    base_controller = _DisconnectController(session_id=None)
    process_controller = _DisconnectController(session_id="process-session")
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, base_controller)),
        call_timeout_seconds=1,
    )
    controllers = cast(
        dict[int, LiveHwpController],
        getattr(bridge, "_controllers"),
    )
    session_processes = cast(
        dict[str, int],
        getattr(bridge, "_session_processes"),
    )
    process_sessions = cast(
        dict[int, set[str]],
        getattr(bridge, "_process_sessions"),
    )
    controllers[71] = cast(LiveHwpController, cast(object, process_controller))
    session_processes["process-session"] = 71
    process_sessions[71] = {"process-session"}

    try:
        result = bridge.disconnect()

        assert result.action == "disconnect"
        assert base_controller.disconnect_calls == []
        assert process_controller.disconnect_calls == ["process-session"]
    finally:
        bridge.close()


def test_rpc_server_unavailable_invalidates_session_without_retry() -> None:
    lifecycle: list[str] = []
    controller = _Controller(lifecycle=lifecycle)
    bridge = _bridge(controller)
    session_processes = cast(
        dict[str, int],
        getattr(bridge, "_session_processes"),
    )
    process_sessions = cast(
        dict[int, set[str]],
        getattr(bridge, "_process_sessions"),
    )
    events = cast(dict[int, _OrderedSignal], getattr(bridge, "_events"))
    session_processes["lost-session"] = 701
    process_sessions[701] = {"lost-session"}
    events[701] = _OrderedSignal(lifecycle)
    attempts = 0

    def disconnected_call() -> None:
        nonlocal attempts
        attempts += 1
        raise _BusyError(-2_147_023_174)

    try:
        with pytest.raises(HwpLiveError) as lost:
            _ = _call_for_session(
                bridge,
                disconnected_call,
                session_id="lost-session",
                mutation=True,
            )
        assert attempts == 1
        assert "target_process_lost=true" in lost.value.reason
        assert "reconnect_required=true" in lost.value.reason
        assert "worker_isolation_required=true" in lost.value.reason
        assert "reconcile_required=true" in lost.value.reason
        assert "retry_safe=false" in lost.value.reason
        assert session_processes == {}
        assert process_sessions == {}
        assert events == {}
        assert lifecycle == ["event.stop"]
        assert controller.invalidate_calls == 1

        with pytest.raises(HwpLiveError) as stale:
            _ = _call_for_session(
                bridge,
                lambda: "must not run",
                session_id="lost-session",
            )
        assert "재연결" in stale.value.reason
        assert _call(bridge, lambda: "new-process", process_id=702) == "new-process"
    finally:
        bridge.close()


def test_target_process_exit_interrupts_blocked_call_before_total_timeout() -> None:
    controller = _Controller()
    exited = Event()
    release = Event()
    started = Event()
    watch = _ExitWatch(exited)
    windows = _DiagnosticWindowReader(())
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(WindowStateReader, cast(object, windows)),
        call_timeout_seconds=0.5,
    )

    def block() -> None:
        started.set()
        _ = release.wait(2)

    def end_process() -> None:
        assert started.wait(1)
        sleep(0.02)
        exited.set()

    terminator = Thread(target=end_process)
    terminator.start()
    began = monotonic()
    try:
        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                return_value=watch,
                create=True,
            ),
            pytest.raises(HwpLiveError) as lost,
        ):
            _ = _call(bridge, block, process_id=811, mutation=True)
        elapsed = monotonic() - began
        assert elapsed < 0.25
        assert "target_process_lost=true" in lost.value.reason
        assert "reconnect_required=true" in lost.value.reason
        assert "reconcile_required=true" in lost.value.reason
        assert watch.closed is True
    finally:
        release.set()
        terminator.join(timeout=1)
        bridge.close()


def test_blocked_call_reports_target_modal_without_dismissing_it() -> None:
    controller = _Controller()
    release = Event()
    started = Event()
    never_exited = Event()
    watch = _ExitWatch(never_exited)
    dialog = HancomDialogState(
        window_handle=8122,
        owner_handle=8121,
        title="문서가 변경되었습니다",
        class_name="#32770",
        visible=True,
        enabled=True,
        modal=True,
    )
    main = HancomWindowState(
        window_handle=8121,
        process_id=812,
        exists=True,
        visible=True,
        enabled=False,
        foreground=True,
        title="modal.hwp - 한글",
        class_name="HwpMain",
        dialogs=(dialog,),
    )
    windows = _DiagnosticWindowReader((main,))
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(WindowStateReader, cast(object, windows)),
        call_timeout_seconds=0.5,
    )

    def block() -> None:
        started.set()
        _ = release.wait(2)

    began = monotonic()
    try:
        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                return_value=watch,
                create=True,
            ),
            patch(
                "hwp_live_bridge._MODAL_DIAGNOSTIC_DELAY_SECONDS",
                0.01,
                create=True,
            ),
            patch(
                "hwp_live_bridge._MODAL_DIAGNOSTIC_INTERVAL_SECONDS",
                0.01,
                create=True,
            ),
            pytest.raises(HwpLiveError) as modal,
        ):
            _ = _call(bridge, block, process_id=812, mutation=True)
        elapsed = monotonic() - began
        assert started.is_set()
        assert elapsed < 0.25
        assert "대상 한컴 창에 대화상자가 떠 있습니다" in modal.value.reason
        assert dialog.title in modal.value.reason
        assert "reconcile_required=true" in modal.value.reason
        assert "retry_safe=false" in modal.value.reason
        assert windows.dismiss_calls == 0
    finally:
        release.set()
        bridge.close()


def test_modal_after_save_dispatch_retains_close_blocker() -> None:
    controller = _DisconnectController()
    release = Event()
    started = Event()
    never_exited = Event()
    watch = _ExitWatch(never_exited)
    dialog = HancomDialogState(
        window_handle=8142,
        owner_handle=8141,
        title="저장 처리 중",
        class_name="#32770",
        visible=True,
        enabled=True,
        modal=True,
    )
    main = HancomWindowState(
        window_handle=8141,
        process_id=814,
        exists=True,
        visible=True,
        enabled=False,
        foreground=True,
        title="saving.hwp - 한글",
        class_name="HwpMain",
        dialogs=(dialog,),
    )
    windows = _DiagnosticWindowReader((main,))
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(WindowStateReader, cast(object, windows)),
        call_timeout_seconds=0.5,
    )
    targets = cast(dict[str, object], getattr(bridge, "_session_watch_targets"))
    targets["saving-session"] = type(
        "_Target",
        (),
        {
            "process_id": 814,
            "window_handle": 8141,
            "document_path": Path("C:/qa/saving.hwp"),
            "document_id": 17,
        },
    )()
    session_processes = cast(dict[str, int], getattr(bridge, "_session_processes"))
    session_processes["saving-session"] = 814

    def block_after_save_dispatch() -> None:
        _ = save_preflight_result(
            "save",
            resolve_only=False,
            allow_document_change=True,
        )
        started.set()
        _ = release.wait(2)

    try:
        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                return_value=watch,
                create=True,
            ),
            patch(
                "hwp_live_bridge._MODAL_DIAGNOSTIC_DELAY_SECONDS",
                0.01,
                create=True,
            ),
            patch(
                "hwp_live_bridge._MODAL_DIAGNOSTIC_INTERVAL_SECONDS",
                0.01,
                create=True,
            ),
            pytest.raises(HwpLiveError) as modal,
        ):
            _ = _call_for_session(
                bridge,
                block_after_save_dispatch,
                session_id="saving-session",
                mutation=True,
            )

        assert started.is_set()
        assert "save_state=uncertain" in modal.value.reason
        with pytest.raises(HwpLiveError) as blocked:
            _ = bridge.disconnect("saving-session")
        assert "MCP 자동 연결 해제만 보류" in blocked.value.reason
        assert "save_state=uncertain" in blocked.value.reason
        assert controller.disconnect_calls == []
    finally:
        release.set()
        uncertain = cast(
            dict[str, SaveStateMachine],
            getattr(bridge, "_uncertain_save_states"),
        )
        _ = uncertain.pop("saving-session", None)
        bridge.close()


def test_process_loss_does_not_attribute_title_only_cross_process_dialog() -> None:
    controller = _Controller()
    exited = Event()
    release = Event()
    started = Event()
    watch = _ExitWatch(exited)
    orphan = HancomWindowState(
        window_handle=9911,
        process_id=991,
        exists=True,
        visible=True,
        enabled=True,
        foreground=True,
        title="DDE Server Window: Hwp.exe - 응용 프로그램 오류",
        class_name="#32770",
        dialogs=(),
    )
    windows = _DiagnosticWindowReader((orphan,))
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(WindowStateReader, cast(object, windows)),
        call_timeout_seconds=0.5,
    )

    def block() -> None:
        started.set()
        _ = release.wait(2)

    def end_process() -> None:
        assert started.wait(1)
        sleep(0.02)
        exited.set()

    terminator = Thread(target=end_process)
    terminator.start()
    try:
        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                return_value=watch,
                create=True,
            ),
            pytest.raises(HwpLiveError) as lost,
        ):
            _ = _call(bridge, block, process_id=813)
        assert "고아 한컴 오류 대화상자" not in lost.value.reason
        assert orphan.title not in lost.value.reason
        assert windows.dismiss_calls == 0
    finally:
        release.set()
        terminator.join(timeout=1)
        bridge.close()


def test_process_lane_balances_sta_on_its_worker_thread() -> None:
    lifecycle: list[tuple[str, str]] = []

    def initialize_sta() -> None:
        lifecycle.append(("initialize", current_thread().name))

    def uninitialize_sta() -> None:
        lifecycle.append(("uninitialize", current_thread().name))

    with (
        patch(
            "hwp_live_process_lane._initialize_sta",
            new=initialize_sta,
            create=True,
        ),
        patch(
            "hwp_live_process_lane._uninitialize_sta",
            new=uninitialize_sta,
            create=True,
        ),
    ):
        lane = HwpProcessLane(19, 1)
        try:
            worker_thread = lane.submit(lambda: current_thread().name).result(timeout=1)
        finally:
            lane.shutdown(wait=True)

    assert lifecycle == [
        ("initialize", worker_thread),
        ("uninitialize", worker_thread),
    ]
    assert worker_thread.startswith("HancomBridge-PID-19-STA")


def test_poisoned_process_lane_does_not_block_interpreter_exit() -> None:
    script = "\n".join(
        (
            "from threading import Event",
            "import sys",
            f"sys.path.insert(0, {str(SCRIPTS)!r})",
            "from hwp_live_process_lane import HwpProcessLane",
            "started = Event()",
            "blocked = Event()",
            "lane = HwpProcessLane(29, 1)",
            "def block() -> None:",
            "    started.set()",
            "    blocked.wait()",
            "lane.submit(block)",
            "if not started.wait(5):",
            "    raise RuntimeError('lane worker did not start')",
            "lane.poison()",
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_process_queue_wait_is_bounded_by_total_deadline() -> None:
    controller = _Controller()
    bridge = _bridge(controller, timeout=0.01, queue_limit=1)
    held = HwpProcessLane(23, 1)
    assert held.acquire(0)
    lanes = cast(
        dict[int, HwpProcessLane],
        getattr(bridge, "_process_lanes"),
    )
    lanes[23] = held
    called = False

    def operation() -> None:
        nonlocal called
        called = True

    try:
        with pytest.raises(HwpLiveError) as timeout:
            _ = _call(bridge, operation, process_id=23)
        assert called is False
        assert "phase=queue_wait" in timeout.value.reason
        assert "queue_busy=true" in timeout.value.reason
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
        assert "worker_isolation_required=false" in timeout.value.reason
        assert "process_lane_isolation_required=true" in timeout.value.reason
        assert "reconcile_required=true" in timeout.value.reason
        assert "retry_safe=false" in timeout.value.reason
    finally:
        release.set()
        assert finished.wait(1)
        bridge.close()


def test_different_hwp_processes_use_independent_sta_lanes() -> None:
    controller = _Controller()
    bridge = _bridge(controller, timeout=0.2, queue_limit=2)
    first_started = Event()
    release_first = Event()

    def block_first_process() -> str:
        first_started.set()
        _ = release_first.wait(1)
        return "first"

    try:
        with ThreadPoolExecutor(max_workers=2) as calls:
            first = calls.submit(_call, bridge, block_first_process, process_id=101)
            assert first_started.wait(1)
            second = calls.submit(_call, bridge, lambda: "second", process_id=202)
            assert second.result(timeout=0.1) == "second"
            release_first.set()
            assert first.result(timeout=1) == "first"
    finally:
        release_first.set()
        bridge.close()


def test_same_pid_queued_mutation_reanchors_at_actual_lane_start(
    tmp_path: Path,
) -> None:
    document = tmp_path / "copy.hwp"
    _ = document.write_bytes(b"request")
    controller = _DisconnectController()
    never_exited = Event()
    watch = _ExitWatch(never_exited)
    main = HancomWindowState(
        window_handle=8151,
        process_id=815,
        exists=True,
        visible=True,
        enabled=True,
        foreground=True,
        title="copy.hwp - 한글",
        class_name="HwpMain",
        dialogs=(),
    )
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(
            WindowStateReader,
            cast(object, _DiagnosticWindowReader((main,))),
        ),
        call_timeout_seconds=2,
    )
    targets = cast(dict[str, object], getattr(bridge, "_session_watch_targets"))
    targets["saving-session"] = type(
        "_Target",
        (),
        {
            "process_id": 815,
            "window_handle": 8151,
            "document_path": document,
            "document_id": 17,
        },
    )()
    session_processes = cast(dict[str, int], getattr(bridge, "_session_processes"))
    session_processes["saving-session"] = 815
    first_started = Event()
    mutate_first = Event()

    def first_mutation() -> None:
        first_started.set()
        assert mutate_first.wait(1)
        _ = document.write_bytes(b"earlier-mutation")

    def second_mutation() -> tuple[bool, bool]:
        context = current_lane_operation_context()
        assert context is not None
        before_own_mutation = context.watchdog_metadata_changed
        _ = document.write_bytes(b"this-save")
        return before_own_mutation, context.watchdog_metadata_changed

    try:
        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                return_value=watch,
                create=True,
            ),
            ThreadPoolExecutor(max_workers=2) as calls,
        ):
            first = calls.submit(
                _call_for_session,
                bridge,
                first_mutation,
                session_id="saving-session",
                mutation=True,
            )
            assert first_started.wait(1)
            second = calls.submit(
                _call_for_session,
                bridge,
                second_mutation,
                session_id="saving-session",
                mutation=True,
            )
            active = cast(
                dict[int, HwpLaneOperationContext],
                getattr(bridge, "_active_mutation_contexts"),
            )
            deadline = monotonic() + 1
            while len(active) < 2 and monotonic() < deadline:
                sleep(0.005)
            assert len(active) == 2
            mutate_first.set()
            assert first.result(timeout=1) is None
            assert second.result(timeout=1) == (False, True)
    finally:
        mutate_first.set()
        bridge.close()


def test_different_hwp_processes_use_independent_controllers() -> None:
    root = _Controller()
    created: list[_Controller] = []

    def create_controller() -> LiveHwpController:
        controller = _Controller()
        created.append(controller)
        return cast(LiveHwpController, cast(object, controller))

    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, root)),
        controller_factory=create_controller,
    )
    current_controller = cast(
        Callable[[], LiveHwpController],
        getattr(bridge, "_bridge_controller"),
    )
    try:
        first = _call(bridge, lambda: id(current_controller()), process_id=501)
        second = _call(bridge, lambda: id(current_controller()), process_id=502)
        first_again = _call(
            bridge,
            lambda: id(current_controller()),
            process_id=501,
        )
    finally:
        bridge.close()

    assert first == first_again
    assert first != second
    assert len(created) == 2


def test_closed_process_cleanup_does_not_abort_other_controller_cleanup() -> None:
    root = _Controller()
    created = [_Controller(fail_close=True), _Controller()]

    def create_controller() -> LiveHwpController:
        return cast(LiveHwpController, cast(object, created.pop(0)))

    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, root)),
        controller_factory=create_controller,
    )
    current_controller = cast(
        Callable[[], LiveHwpController],
        getattr(bridge, "_bridge_controller"),
    )
    first = cast(
        _Controller,
        cast(
            object,
            _call(bridge, current_controller, process_id=601),
        ),
    )
    second = cast(
        _Controller,
        cast(
            object,
            _call(bridge, current_controller, process_id=602),
        ),
    )

    bridge.close()

    assert first.close_calls == 1
    assert second.close_calls == 1


def test_bridge_stops_event_sidecars_before_controller_cleanup() -> None:
    lifecycle: list[str] = []
    controller = _Controller(lifecycle=lifecycle)
    bridge = _bridge(controller)
    events = cast(dict[int, _OrderedSignal], getattr(bridge, "_events"))
    events[0] = _OrderedSignal(lifecycle)

    bridge.close()

    assert lifecycle == ["event.stop", "controller.close"]


def test_event_stop_failure_does_not_skip_controller_cleanup() -> None:
    lifecycle: list[str] = []
    controller = _Controller(lifecycle=lifecycle)
    bridge = _bridge(controller)
    events = cast(dict[int, _OrderedSignal], getattr(bridge, "_events"))
    events[0] = _OrderedSignal(lifecycle, fail_stop=True)

    with pytest.raises(HwpLiveError, match="event stop failed"):
        bridge.close()

    assert controller.close_calls == 1
    assert lifecycle == ["event.stop", "controller.close"]


def test_same_hwp_process_operations_remain_serial() -> None:
    controller = _Controller()
    bridge = _bridge(controller, timeout=1, queue_limit=2)
    first_started = Event()
    second_started = Event()
    release_first = Event()

    def first_operation() -> str:
        first_started.set()
        _ = release_first.wait(1)
        return "first"

    def second_operation() -> str:
        second_started.set()
        return "second"

    try:
        with ThreadPoolExecutor(max_workers=2) as calls:
            first = calls.submit(_call, bridge, first_operation, process_id=203)
            assert first_started.wait(1)
            second = calls.submit(_call, bridge, second_operation, process_id=203)
            sleep(0.02)
            assert second_started.is_set() is False
            release_first.set()
            assert first.result(timeout=1) == "first"
            assert second.result(timeout=1) == "second"
    finally:
        release_first.set()
        bridge.close()


def test_quarantine_cancels_a_queued_mutation_before_it_runs() -> None:
    controller = _Controller()
    bridge = _bridge(controller, timeout=0.05, queue_limit=2)
    first_started = Event()
    first_finished = Event()
    second_started = Event()
    release_first = Event()
    alive = Event()

    def first_operation() -> None:
        first_started.set()
        _ = release_first.wait(1)
        first_finished.set()

    def second_operation() -> None:
        second_started.set()

    try:
        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                side_effect=_exit_watch_factory(alive),
                create=True,
            ),
            ThreadPoolExecutor(max_workers=2) as calls,
        ):
            first = calls.submit(
                _call,
                bridge,
                first_operation,
                process_id=300,
                mutation=True,
            )
            assert first_started.wait(1)
            sleep(0.015)
            second = calls.submit(
                _call,
                bridge,
                second_operation,
                process_id=300,
                mutation=True,
            )
            sleep(0.005)
            assert second_started.is_set() is False

            with pytest.raises(HwpLiveError):
                _ = first.result(timeout=1)
            with pytest.raises(HwpLiveError) as gated:
                _ = second.result(timeout=1)
            assert "mutation_gate=true" in gated.value.reason
            assert second_started.is_set() is False

            release_first.set()
            assert first_finished.wait(1)
            assert (
                _call(
                    bridge,
                    lambda: "recovered",
                    process_id=300,
                    mutation=True,
                )
                == "recovered"
            )
    finally:
        release_first.set()
        bridge.close()


def test_running_timeout_allows_reads_and_recovers_live_process_lane() -> None:
    controller = _Controller()
    bridge = _bridge(controller, timeout=0.02, queue_limit=1)
    release = Event()
    finished = Event()
    alive = Event()
    read_started = Event()
    operation_threads: list[str] = []

    def block_target() -> None:
        operation_threads.append(current_thread().name)
        _ = release.wait(1)
        finished.set()

    def read_target() -> str:
        operation_threads.append(current_thread().name)
        read_started.set()
        return "readback"

    try:
        with patch(
            "hwp_live_bridge.open_process_exit_watch",
            side_effect=_exit_watch_factory(alive),
            create=True,
        ):
            with pytest.raises(HwpLiveError) as timeout:
                _ = _call(
                    bridge,
                    block_target,
                    process_id=301,
                    mutation=True,
                )
            assert "process_lane_isolation_required=true" in timeout.value.reason
            assert _call(bridge, lambda: "healthy", process_id=302) == "healthy"

            lanes = cast(
                dict[int, HwpProcessLane],
                getattr(bridge, "_process_lanes"),
            )
            target_lane = lanes[301]
            with ThreadPoolExecutor(max_workers=1) as reads:
                read = reads.submit(
                    _call,
                    bridge,
                    read_target,
                    process_id=301,
                )
                sleep(0.005)
                assert read_started.is_set() is False
                with pytest.raises(HwpLiveError) as quarantined:
                    _ = _call(
                        bridge,
                        lambda: "must not mutate",
                        process_id=301,
                        mutation=True,
                    )
                assert "mutation_gate=true" in quarantined.value.reason
                assert "reads_allowed=true" in quarantined.value.reason

                release.set()
                assert finished.wait(1)
                assert read.result(timeout=1) == "readback"

            assert lanes[301] is target_lane
            assert len(set(operation_threads)) == 1
            assert (
                _call(
                    bridge,
                    lambda: "recovered",
                    process_id=301,
                    mutation=True,
                )
                == "recovered"
            )
    finally:
        release.set()
        bridge.close()


def test_confirmed_save_fingerprint_recovers_lane_but_keeps_disconnect_guard(
    tmp_path: Path,
) -> None:
    controller = _DisconnectController("saving-session")
    release = Event()
    started = Event()
    finished = Event()
    alive = Event()
    document = tmp_path / "saving.hwp"
    _ = document.write_bytes(b"before")
    open_document = OpenDocument(
        selector="saving-document",
        title=document.name,
        full_name=str(document),
        document_id=17,
        format="HWP",
        edit_mode=1,
        modified=True,
        page_count=1,
        active=True,
        window_handle=8151,
    )
    controller.connected_document = ConnectedDocument(
        session_id="saving-session",
        document=open_document,
    )
    main = HancomWindowState(
        window_handle=8151,
        process_id=815,
        exists=True,
        visible=True,
        enabled=True,
        foreground=True,
        title="saving.hwp - 한글",
        class_name="HwpMain",
        dialogs=(),
    )
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(
            WindowStateReader,
            cast(object, _DiagnosticWindowReader((main,))),
        ),
        call_timeout_seconds=0.02,
        process_queue_limit=1,
    )
    targets = cast(dict[str, object], getattr(bridge, "_session_watch_targets"))
    targets["saving-session"] = type(
        "_Target",
        (),
        {
            "process_id": 815,
            "window_handle": 8151,
            "document_path": document,
            "document_id": 17,
            "document": open_document,
        },
    )()
    session_processes = cast(dict[str, int], getattr(bridge, "_session_processes"))
    session_processes["saving-session"] = 815

    def saving() -> None:
        context = current_lane_operation_context()
        assert context is not None
        state = context.save_state()
        assert isinstance(state, SaveStateMachine)
        state.native_started(source="native_call")
        started.set()
        _ = document.write_bytes(b"after")
        _ = release.wait(1)
        finished.set()
        raise HwpLiveError("late save transport failure")

    call_mutation = cast(
        Callable[..., None],
        getattr(bridge, "_call_mutation"),
    )
    try:
        with patch(
            "hwp_live_bridge.open_process_exit_watch",
            side_effect=_exit_watch_factory(alive),
            create=True,
        ):
            with (
                operation_recovery_scope("saving-operation"),
                pytest.raises(HwpLiveError) as timeout,
            ):
                call_mutation(
                    saving,
                    session_id="saving-session",
                    save_operation=True,
                )
            assert started.is_set()
            assert "save_state=uncertain" in timeout.value.reason

            poisoned = cast(set[int], getattr(bridge, "_poisoned_processes"))
            assert 815 in poisoned
            with pytest.raises(HwpLiveError) as gated:
                call_mutation(
                    lambda: None,
                    session_id="saving-session",
                )
            assert "file_metadata_changed=true" in gated.value.reason

            with (
                patch.object(
                    HancomBridge,
                    "_context_save_state",
                    return_value=SaveStateMachine(),
                ),
                patch.object(
                    HancomBridge,
                    "_recover_poisoned_process",
                    return_value=True,
                ) as recover_nonterminal,
            ):
                assert (
                    bridge.reconcile_confirmed_save(
                        open_document.selector,
                        "saving-operation",
                    )
                    is False
                )
                recover_nonterminal.assert_not_called()
            confirmed_outcomes = cast(
                set[int],
                getattr(bridge, "_recovery_outcome_confirmed"),
            )
            assert (
                bridge.reconcile_confirmed_save(
                    open_document.selector,
                    "older-saving-operation",
                )
                is False
            )
            assert 815 not in confirmed_outcomes
            assert (
                bridge.reconcile_confirmed_save(
                    open_document.selector,
                    "saving-operation",
                )
                is False
            )
            assert 815 in confirmed_outcomes

            release.set()
            assert finished.wait(1)
            recovery_deadline = monotonic() + 1
            while monotonic() < recovery_deadline and (
                controller.recovery_probe_calls == 0 or 815 in poisoned
            ):
                sleep(0.005)
            assert controller.recovery_probe_calls == 1
            assert 815 not in poisoned
            assert 815 not in confirmed_outcomes
            assert bridge.context("saving-session") == "readback:saving-session"

            with pytest.raises(HwpLiveError) as guarded:
                _ = bridge.disconnect("saving-session")
            assert "save_state=uncertain" in guarded.value.reason
            assert "save_close_blocked=true" in guarded.value.reason
            assert controller.disconnect_calls == []
    finally:
        release.set()
        uncertain = cast(
            dict[str, SaveStateMachine],
            getattr(bridge, "_uncertain_save_states"),
        )
        _ = uncertain.pop("saving-session", None)
        bridge.close()


def test_late_mutation_failure_does_not_treat_generic_read_as_outcome(
    tmp_path: Path,
) -> None:
    controller = _DisconnectController("failed-session")
    release = Event()
    finished = Event()
    alive = Event()
    document = tmp_path / "failed.hwp"
    _ = document.write_bytes(b"before")
    open_document = OpenDocument(
        selector="failed-document",
        title=document.name,
        full_name=str(document),
        document_id=20,
        format="HWP",
        edit_mode=1,
        modified=True,
        page_count=1,
        active=True,
        window_handle=8181,
    )
    controller.connected_document = ConnectedDocument(
        session_id="failed-session",
        document=open_document,
    )
    main = HancomWindowState(
        window_handle=8181,
        process_id=818,
        exists=True,
        visible=True,
        enabled=True,
        foreground=True,
        title="failed.hwp - 한글",
        class_name="HwpMain",
        dialogs=(),
    )
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(
            WindowStateReader,
            cast(object, _DiagnosticWindowReader((main,))),
        ),
        call_timeout_seconds=0.02,
        process_queue_limit=1,
    )
    targets = cast(dict[str, object], getattr(bridge, "_session_watch_targets"))
    targets["failed-session"] = type(
        "_Target",
        (),
        {
            "process_id": 818,
            "window_handle": 8181,
            "document_path": document,
            "document_id": 20,
            "document": open_document,
        },
    )()
    session_processes = cast(dict[str, int], getattr(bridge, "_session_processes"))
    session_processes["failed-session"] = 818

    def fail_after_timeout() -> None:
        _ = release.wait(1)
        finished.set()
        raise HwpLiveError(
            "".join(
                (
                    "late mutation failure; mutation_started=true; retry_safe=false; ",
                    "mutation_started=false; retry_safe=true",
                )
            )
        )

    try:
        with patch(
            "hwp_live_bridge.open_process_exit_watch",
            side_effect=_exit_watch_factory(alive),
            create=True,
        ):
            with pytest.raises(HwpLiveError):
                _ = _call_for_session(
                    bridge,
                    fail_after_timeout,
                    session_id="failed-session",
                    mutation=True,
                )
            poisoned = cast(set[int], getattr(bridge, "_poisoned_processes"))
            assert 818 in poisoned

            release.set()
            assert finished.wait(1)
            probe_deadline = monotonic() + 1
            while monotonic() < probe_deadline and controller.recovery_probe_calls == 0:
                sleep(0.005)
            assert controller.recovery_probe_calls == 1
            assert 818 in poisoned

            assert bridge.context("failed-session") == "readback:failed-session"
            assert 818 in poisoned
            with pytest.raises(HwpLiveError) as unresolved:
                _ = _call_for_session(
                    bridge,
                    lambda: "must stay gated",
                    session_id="failed-session",
                    mutation=True,
                )
            assert "reconcile_required=true" in unresolved.value.reason

            _ = bridge.disconnect("failed-session")
            assert 818 not in poisoned
            assert controller.disconnect_calls == ["failed-session"]
    finally:
        release.set()
        bridge.close()


def test_dead_poisoned_process_remains_blocked_and_invalidates_session(
    tmp_path: Path,
) -> None:
    controller = _DisconnectController("lost-session")
    release = Event()
    exited = Event()
    document = tmp_path / "lost.hwp"
    _ = document.write_bytes(b"before")
    open_document = OpenDocument(
        selector="lost-document",
        title=document.name,
        full_name=str(document),
        document_id=18,
        format="HWP",
        edit_mode=1,
        modified=True,
        page_count=1,
        active=True,
        window_handle=8161,
    )
    controller.connected_document = ConnectedDocument(
        session_id="lost-session",
        document=open_document,
    )
    main = HancomWindowState(
        window_handle=8161,
        process_id=816,
        exists=True,
        visible=True,
        enabled=True,
        foreground=True,
        title="lost.hwp - 한글",
        class_name="HwpMain",
        dialogs=(),
    )
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(
            WindowStateReader,
            cast(object, _DiagnosticWindowReader((main,))),
        ),
        call_timeout_seconds=0.02,
        process_queue_limit=1,
    )
    targets = cast(dict[str, object], getattr(bridge, "_session_watch_targets"))
    targets["lost-session"] = type(
        "_Target",
        (),
        {
            "process_id": 816,
            "window_handle": 8161,
            "document_path": document,
            "document_id": 18,
            "document": open_document,
        },
    )()
    session_processes = cast(dict[str, int], getattr(bridge, "_session_processes"))
    session_processes["lost-session"] = 816

    try:
        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                side_effect=_exit_watch_factory(exited),
                create=True,
            ),
            pytest.raises(HwpLiveError),
        ):
            _ = _call_for_session(
                bridge,
                lambda: release.wait(1),
                session_id="lost-session",
                mutation=True,
            )
        exited.set()

        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                side_effect=_exit_watch_factory(exited),
                create=True,
            ),
            pytest.raises(HwpLiveError) as lost,
        ):
            _ = _call_for_session(
                bridge,
                lambda: "must not read",
                session_id="lost-session",
            )
        assert "target_process_lost=true" in lost.value.reason

        with pytest.raises(HwpLiveError) as stale:
            _ = _call_for_session(
                bridge,
                lambda: "must stay blocked",
                session_id="lost-session",
            )
        assert "reconnect_required=true" in stale.value.reason
    finally:
        release.set()
        bridge.close()


def test_known_stale_session_does_not_fall_back_to_global_discovery() -> None:
    controller = _KnownStaleController()
    document = OpenDocument(
        selector="known-document",
        title="known.hwp",
        full_name="C:/qa/known.hwp",
        document_id=19,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=1,
        active=True,
        window_handle=8171,
    )
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(
            WindowStateReader,
            cast(object, _DiagnosticWindowReader(())),
        ),
    )
    targets = cast(dict[str, object], getattr(bridge, "_session_watch_targets"))
    targets["known-session"] = type(
        "_Target",
        (),
        {
            "process_id": 817,
            "window_handle": 8171,
            "document_path": Path(document.full_name),
            "document_id": document.document_id,
            "document": document,
        },
    )()
    session_processes = cast(dict[str, int], getattr(bridge, "_session_processes"))
    session_processes["known-session"] = 817

    try:
        with pytest.raises(HwpLiveError) as stale:
            _ = bridge.ensure_connection(document.selector)
        assert "known_connection_stale=true" in stale.value.reason
        assert "global_discovery_skipped=true" in stale.value.reason
        assert controller.list_calls == 0
    finally:
        bridge.close()


def test_soft_quarantined_process_lane_closes_without_waiting_for_running_call() -> (
    None
):
    controller = _Controller()
    bridge = _bridge(controller, timeout=0.02, queue_limit=1)
    release = Event()
    alive = Event()

    try:
        with (
            patch(
                "hwp_live_bridge.open_process_exit_watch",
                side_effect=_exit_watch_factory(alive),
                create=True,
            ),
            pytest.raises(HwpLiveError),
        ):
            _ = _call(
                bridge,
                lambda: release.wait(1),
                process_id=401,
                mutation=True,
            )

        close_started = monotonic()
        bridge.close()
        assert monotonic() - close_started < 0.2

        release.set()
        deadline = monotonic() + 1
        while monotonic() < deadline and any(
            thread.name.startswith("HancomBridge-PID-401-STA")
            for thread in enumerate_threads()
        ):
            sleep(0.01)
        assert not any(
            thread.name.startswith("HancomBridge-PID-401-STA")
            for thread in enumerate_threads()
        )
    finally:
        release.set()
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

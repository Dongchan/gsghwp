from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import cast, final

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import (  # noqa: E402
    HwpComApplication,
    HwpComDocument,
    LiveHwpApplication,
)
from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_bridge_contract import (  # noqa: E402
    HancomDialogDismissResult,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_contract import (  # noqa: E402
    ConnectedDocument,
    OpenDocument,
    OpenDocumentList,
)
from hwp_live_process_lane import current_lane_operation_context  # noqa: E402
from hwp_live_rot import (  # noqa: E402
    HwpDocumentCandidate,
    HwpDocumentIdentity,
    HwpRotCatalog,
    _selector,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_session_core import LiveHwpSessionCore  # noqa: E402
from hwp_live_session_lifecycle import SaveStateMachine  # noqa: E402
from hwp_live_session_types import LiveSessionReference  # noqa: E402
from hwp_live_windows import WindowStateReader  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_mcp_session_lifetime import (  # noqa: E402
    persistent_session_tool_names,
)
from hwp_operation_contract import OperationResult  # noqa: E402


@final
class _Document:
    def __init__(self, document_id: int, full_name: str) -> None:
        self.DocumentID = document_id
        self.FullName = full_name
        self.Format = "HWP"
        self.EditMode = 1
        self.Modified = 0

    def SetActive_XHwpDocument(self) -> None:
        return


@final
class _Documents:
    def __init__(
        self,
        document: _Document,
        *,
        leading_missing_item: bool = False,
    ) -> None:
        self._document = document
        self._leading_missing_item = leading_missing_item
        self.Active_XHwpDocument = document
        self.Count = 2 if leading_missing_item else 1

    def Item(self, index: int) -> _Document | None:
        if self._leading_missing_item and index == 0:
            return None
        assert index == (1 if self._leading_missing_item else 0)
        return self._document


@final
class _Window:
    WindowHandle = 701


@final
class _Windows:
    Active_XHwpWindow = _Window()


@final
class _Application:
    def __init__(
        self,
        document: _Document,
        *,
        leading_missing_item: bool = False,
    ) -> None:
        self.XHwpDocuments = _Documents(
            document,
            leading_missing_item=leading_missing_item,
        )
        self.XHwpWindows = _Windows()
        self.PageCount = 4

    def RegisterModule(self, *, ModuleType: str, ModuleData: str) -> bool:
        _ = ModuleType, ModuleData
        return True


@final
class _Wrapper:
    def __init__(self, application: _Application) -> None:
        self.hwp = cast(HwpComApplication, cast(object, application))
        self.on_quit = False
        self.htf_fonts: dict[str, dict[str, int | str]] = {}

    @property
    def Version(self) -> list[int]:
        return [13, 0, 0, 0]

    @property
    def PageCount(self) -> int:
        return 4


def _candidate(application: _Application, document: _Document) -> HwpDocumentCandidate:
    return HwpDocumentCandidate(
        selector="cached-document",
        moniker_name="!HwpObject.701",
        application=cast(HwpComApplication, cast(object, application)),
        document=cast(HwpComDocument, cast(object, document)),
        document_id=document.DocumentID,
        full_name=document.FullName,
        document_format=document.Format,
        edit_mode=document.EditMode,
        window_handle=701,
        active=True,
        page_count=4,
    )


@final
class _IdentityCatalog:
    def __init__(self, candidate: HwpDocumentCandidate) -> None:
        self.candidate = candidate
        self.resolve_calls = 0
        self.scan_calls = 0
        self.release_calls = 0

    def scan(self) -> tuple[HwpDocumentCandidate, ...]:
        self.scan_calls += 1
        return (self.candidate,)

    def resolve_identity(
        self,
        identity: HwpDocumentIdentity,
    ) -> HwpDocumentCandidate:
        self.resolve_calls += 1
        assert identity.selector == self.candidate.selector
        return self.candidate

    def release_com_references(self) -> None:
        self.release_calls += 1

    def close(self) -> None:
        return


def test_transient_core_release_drops_session_and_com_holders() -> None:
    document = _Document(7, r"C:\fixtures\cached.hwp")
    application = _Application(document)
    catalog = _IdentityCatalog(_candidate(application, document))
    released: list[LiveHwpApplication] = []
    controller = LiveHwpSessionCore(
        catalog=catalog,
        attacher=lambda candidate, wrapper=None: cast(
            LiveHwpApplication,
            cast(object, _Wrapper(application)),
        ),
        releaser=lambda wrapper: released.append(wrapper),
    )

    connected = controller.connect_deferred("cached-document")
    _, wrapper = controller._validate(connected.session_id)

    assert controller.release_transient(connected.session_id)
    assert controller.session_count() == 0
    assert controller.current_session() is None
    assert controller._sessions == {}
    assert controller._wrapper is None
    assert controller._connected_candidate is None
    assert controller._listed_candidates == ()
    assert released == [wrapper]
    assert catalog.release_calls == 1
    controller.close()


def test_explicit_session_restore_releases_com_holders_but_keeps_identity() -> None:
    document = _Document(7, r"C:\fixtures\cached.hwp")
    application = _Application(document)
    catalog = _IdentityCatalog(_candidate(application, document))
    released: list[LiveHwpApplication] = []
    controller = LiveHwpSessionCore(
        catalog=catalog,
        attacher=lambda candidate, wrapper=None: cast(
            LiveHwpApplication,
            cast(object, _Wrapper(application)),
        ),
        releaser=lambda wrapper: released.append(wrapper),
    )

    connected = controller.connect_deferred("cached-document")
    _, first_wrapper = controller._validate(connected.session_id)
    controller.restore_activation()

    entry = controller._sessions[connected.session_id]
    assert controller.session_count() == 1
    assert controller.session_for_selector("cached-document") is not None
    assert isinstance(entry.candidate, HwpDocumentIdentity)
    assert entry.candidate.document_id == 7
    assert entry.candidate.moniker_name == "!HwpObject.701"
    assert entry.wrapper is None
    assert controller._wrapper is None
    assert controller._connected_candidate is entry.candidate
    assert released == [first_wrapper]
    assert catalog.release_calls == 1

    _, second_wrapper = controller._validate(connected.session_id)
    controller.restore_activation()

    assert controller.session_count() == 1
    assert released == [first_wrapper, second_wrapper]
    assert catalog.resolve_calls >= 2
    controller.close()


@final
class _FailingPageCountWrapper:
    def __init__(self, application: _Application) -> None:
        self.hwp = cast(HwpComApplication, cast(object, application))
        self.on_quit = False
        self.htf_fonts: dict[str, dict[str, int | str]] = {}

    @property
    def Version(self) -> list[int]:
        return [13, 0, 0, 0]

    @property
    def PageCount(self) -> int:
        raise RuntimeError("page count failed")


def test_validate_error_releases_com_holders_and_keeps_logical_session() -> None:
    document = _Document(7, r"C:\fixtures\cached.hwp")
    application = _Application(document)
    catalog = _IdentityCatalog(_candidate(application, document))
    released: list[LiveHwpApplication] = []
    controller = LiveHwpSessionCore(
        catalog=catalog,
        attacher=lambda candidate, wrapper=None: cast(
            LiveHwpApplication,
            cast(object, _FailingPageCountWrapper(application)),
        ),
        releaser=lambda wrapper: released.append(wrapper),
    )
    connected = controller.connect_deferred("cached-document")

    with pytest.raises(RuntimeError, match="page count failed"):
        _ = controller._validate(connected.session_id)

    entry = controller._sessions[connected.session_id]
    assert controller.session_count() == 1
    assert isinstance(entry.candidate, HwpDocumentIdentity)
    assert entry.wrapper is None
    assert controller._wrapper is None
    assert controller._activation_restore is None
    assert len(released) == 1
    assert catalog.release_calls == 1
    controller.close()


def test_reconnect_uses_cached_identity_without_catalog_scan() -> None:
    document = _Document(7, r"C:\fixtures\cached.hwp")
    application = _Application(document)
    catalog = _IdentityCatalog(_candidate(application, document))
    controller = LiveHwpSessionCore(
        catalog=catalog,
        attacher=lambda candidate, wrapper=None: cast(
            LiveHwpApplication,
            cast(object, _Wrapper(application)),
        ),
        releaser=lambda wrapper: None,
    )

    first = controller.connect_deferred("cached-document")
    assert controller.release_transient(first.session_id)
    second = controller.connect_deferred("cached-document")

    assert second.session_id != first.session_id
    assert catalog.scan_calls == 1
    assert catalog.resolve_calls == 1
    controller.close()


@final
class _DirectMoniker:
    def __init__(self, display_name: str) -> None:
        self.display_name = display_name


@final
class _DispatchSource:
    def QueryInterface(self, _interface_id: object) -> _DispatchSource:
        return self


@final
class _DirectRot:
    def __init__(self) -> None:
        self.get_object_calls: list[str] = []

    def EnumRunning(self) -> tuple[object, ...]:
        raise AssertionError("cached identity reconnect must not enumerate the ROT")

    def GetObject(self, moniker: _DirectMoniker) -> _DispatchSource:
        self.get_object_calls.append(moniker.display_name)
        return _DispatchSource()


@final
class _DirectPythonCom:
    IID_IDispatch = object()

    def __init__(self, rot: _DirectRot) -> None:
        self._rot = rot

    def CoInitialize(self) -> None:
        return

    def CoUninitialize(self) -> None:
        return

    def GetRunningObjectTable(self) -> _DirectRot:
        return self._rot

    def CreateItemMoniker(self, delimiter: str, item: str) -> _DirectMoniker:
        return _DirectMoniker(delimiter + item)


@final
class _DirectClient:
    def __init__(self, application: _Application) -> None:
        self._application = application

    def Dispatch(self, _source: _DispatchSource) -> HwpComApplication:
        return cast(HwpComApplication, cast(object, self._application))


def test_rot_identity_resolution_binds_exact_moniker_without_enumeration() -> None:
    document = _Document(7, r"C:\fixtures\cached.hwp")
    application = _Application(document, leading_missing_item=True)
    candidate = replace(
        _candidate(application, document),
        selector=_selector(
            "!HwpObject.701",
            document.DocumentID,
            document.FullName,
        ),
    )
    identity = HwpDocumentIdentity.from_candidate(candidate, page_count=4)
    rot = _DirectRot()
    catalog = HwpRotCatalog(cache_ttl_seconds=60)
    setattr(
        catalog,
        "_runtime",
        type(
            "_Runtime",
            (),
            {
                "pythoncom": _DirectPythonCom(rot),
                "win32_client": _DirectClient(application),
            },
        )(),
    )

    resolved = catalog.resolve_identity(identity)

    assert resolved.selector == identity.selector
    assert resolved.document_id == identity.document_id
    assert resolved.document is document
    assert rot.get_object_calls == [identity.moniker_name]
    catalog.close()


@final
class _ScopeBridge:
    def __init__(self) -> None:
        self.document = OpenDocument(
            selector="scope-document",
            title="scope.hwp",
            full_name=r"C:\fixtures\scope.hwp",
            document_id=3,
            format="HWP",
            edit_mode=1,
            modified=False,
            page_count=1,
            active=True,
            window_handle=303,
        )
        self.ensure_lifetimes: list[bool | None] = []
        self.release_calls: list[str] = []
        self.idle_release_calls = 0
        self.idle_release_process_ids: list[frozenset[int]] = []

    def ensure_connection(
        self,
        _selector: str | None,
        *,
        persistent: bool | None = None,
    ) -> ConnectedDocument:
        self.ensure_lifetimes.append(persistent)
        return ConnectedDocument(session_id="scope-session", document=self.document)

    def release_transient_connection(self, session_id: str) -> bool:
        self.release_calls.append(session_id)
        return True

    def release_idle_references(self, process_ids: frozenset[int]) -> None:
        self.idle_release_calls += 1
        self.idle_release_process_ids.append(process_ids)


def test_stateless_public_scope_releases_on_error() -> None:
    bridge = _ScopeBridge()
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(
        cast(HancomBridge, cast(object, bridge)),
        dispatcher,
        None,
    )

    async def fail_after_connect() -> None:
        try:
            async with executor.public_tool_session_scope("hwp_inspect_page_fast"):
                _ = await executor.ensure_connection(None)
                raise HwpLiveError("inspection failed")
        finally:
            await dispatcher.close(lambda: None)

    with pytest.raises(HwpLiveError, match="inspection failed"):
        anyio.run(fail_after_connect)

    assert bridge.ensure_lifetimes == [False]
    assert bridge.release_calls == ["scope-session"]
    assert bridge.idle_release_calls == 1
    assert bridge.idle_release_process_ids == [frozenset()]


def test_connect_scope_keeps_explicit_session() -> None:
    bridge = _ScopeBridge()
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(
        cast(HancomBridge, cast(object, bridge)),
        dispatcher,
        None,
    )

    async def connect() -> None:
        try:
            async with executor.public_tool_session_scope("hwp_connect"):
                _ = await executor.ensure_connection(None)
        finally:
            await dispatcher.close(lambda: None)

    anyio.run(connect)

    assert {"hwp_connect", "hwp_watch_state"} <= persistent_session_tool_names()
    assert bridge.ensure_lifetimes == [True]
    assert bridge.release_calls == []
    assert bridge.idle_release_calls == 0


@final
class _Signal:
    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        _ = process_id, moniker_name

    def sequence(self) -> int:
        return 0

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        _ = after_sequence, timeout_seconds
        return 0

    def stop(self) -> None:
        return


@final
class _WindowReader:
    def read(self, window_handle: int) -> HancomWindowState:
        return HancomWindowState(
            window_handle=window_handle,
            process_id=808,
            exists=True,
            visible=True,
            enabled=True,
            foreground=True,
            title="save.hwp - 한글",
            class_name="HwpMain",
            dialogs=(),
        )

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        return HancomWindowStateList(windows=())

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        state = self.read(window_handle)
        return HancomDialogDismissResult(
            before=state,
            after=state,
            dismissed_handles=(),
        )


@final
class _DiscoveryController:
    def __init__(self, document: OpenDocument) -> None:
        self.document = document

    def list_open_documents(self) -> OpenDocumentList:
        return OpenDocumentList(documents=(self.document,))

    def current_session(self) -> None:
        return None

    def restore_activation(self) -> None:
        return

    def release_idle_references(self) -> None:
        return

    def close(self) -> None:
        return


@final
class _SavingController:
    def __init__(self, document: OpenDocument) -> None:
        self.document = document
        self.session_id: str | None = None
        self.operation_started = Event()
        self.allow_operation_return = Event()
        self.reference_released = Event()

    def connect_deferred(self, selector: str) -> ConnectedDocument:
        assert selector == self.document.selector
        self.session_id = "save-session"
        return ConnectedDocument(session_id="save-session", document=self.document)

    def current_session(self) -> LiveSessionReference | None:
        if self.session_id is None:
            return None
        return LiveSessionReference(self.session_id, self.document.selector)

    def session_for_selector(self, selector: str) -> LiveSessionReference | None:
        current = self.current_session()
        return current if current is not None and current.selector == selector else None

    def connection_moniker(self, session_id: str) -> str:
        assert session_id == self.session_id
        return "!HancomLiveBridge.808.8080.8"

    def set_style_state_token(self, session_id: str, state_token: str) -> None:
        assert session_id == self.session_id
        assert state_token

    def operate(
        self,
        session_id: str,
        *_args: object,
        **_kwargs: object,
    ) -> OperationResult:
        assert session_id == self.session_id
        context = current_lane_operation_context()
        assert context is not None
        state = context.save_state()
        assert isinstance(state, SaveStateMachine)
        state.native_started(source="native_call")
        self.operation_started.set()
        assert self.allow_operation_return.wait(2)
        state.native_returned(source="native_return")
        state.metadata_changed(source="file_metadata")
        state.verified(source="readback")
        return OperationResult(
            status="executed",
            query="save",
            registry_entries=1,
            lookup_microseconds=0,
            message="saved",
            verified=True,
            retry_safe=True,
        )

    def last_operation_routing_context(self, session_id: str) -> None:
        assert session_id == self.session_id
        return None

    def release_transient(self, session_id: str) -> bool:
        assert session_id == self.session_id
        self.session_id = None
        self.reference_released.set()
        return True

    def restore_activation(self) -> None:
        return

    def release_idle_references(self) -> None:
        return

    def close(self) -> None:
        self.session_id = None


def test_save_in_progress_defers_release_until_operation_returns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "save.hwp"
    _ = path.write_bytes(b"fixture")
    document = OpenDocument(
        selector="save-document",
        title=path.name,
        full_name=str(path),
        document_id=8,
        format="HWP",
        edit_mode=1,
        modified=True,
        page_count=1,
        active=True,
        window_handle=8080,
    )
    discovery = _DiscoveryController(document)
    saving = _SavingController(document)
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, discovery)),
        window_reader=cast(WindowStateReader, _WindowReader()),
        change_signal=_Signal(),
        controller_factory=lambda: cast(
            LiveHwpController,
            cast(object, saving),
        ),
        call_timeout_seconds=3,
    )
    connected = bridge.ensure_connection(document.selector, persistent=False)

    try:
        with ThreadPoolExecutor(max_workers=2) as calls:
            operation = calls.submit(
                bridge.operate,
                connected.session_id,
                "save",
                {},
                resolve_only=False,
                allow_document_change=True,
                use_defaults=False,
                expected_cursor=None,
                workflow="document.save",
            )
            assert saving.operation_started.wait(1)

            assert not bridge.release_transient_connection(connected.session_id)
            assert not saving.reference_released.is_set()

            saving.allow_operation_return.set()
            assert operation.result(timeout=2).status == "executed"
            assert saving.reference_released.wait(1)
    finally:
        saving.allow_operation_return.set()
        bridge.close()

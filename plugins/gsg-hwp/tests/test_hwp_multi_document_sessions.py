from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from pathlib import Path
from typing import cast, final
from unittest.mock import patch

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

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
from hwp_live_contract import ConnectedDocument, OpenDocument  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_session_candidate import select_operation_document  # noqa: E402
from hwp_live_contract import OpenDocumentList  # noqa: E402


_APPLICATIONS_BY_HANDLE: dict[int, _Application] = {}


@final
class _Document:
    def __init__(
        self,
        documents: _Documents,
        document_id: int,
        path: str,
    ) -> None:
        self._documents = documents
        self.DocumentID = document_id
        self.FullName = path
        self.Format = "HWP"
        self.EditMode = 1
        self.Modified = 0

    def SetActive_XHwpDocument(self) -> None:
        self._documents.active = self

    def Open(
        self,
        path: str,
        format: str | None,
        arguments: str | None,
    ) -> bool:
        _ = format, arguments
        if not Path(path).is_file():
            return False
        self.FullName = str(Path(path).resolve())
        self.Format = Path(path).suffix.removeprefix(".").upper()
        self._documents.active = self
        return True


@final
class _Documents:
    def __init__(self) -> None:
        self.items: list[_Document] = []
        self.active: _Document

    @property
    def Count(self) -> int:
        return len(self.items)

    @property
    def Active_XHwpDocument(self) -> _Document:
        return self.active

    def Item(self, index: int) -> _Document:
        return self.items[index]

    def FindItem(self, document_id: int) -> _Document | None:
        return next(
            (item for item in self.items if item.DocumentID == document_id),
            None,
        )

    def Add(self, new_tab: bool) -> _Document:
        _ = new_tab
        document = _Document(self, len(self.items) + 1, "")
        self.items.append(document)
        self.active = document
        return document


@final
class _Window:
    def __init__(self, handle: int) -> None:
        self.WindowHandle = handle


@final
class _Windows:
    def __init__(self, handle: int) -> None:
        self.Active_XHwpWindow = _Window(handle)


@final
class _Application:
    def __init__(
        self,
        handle: int,
        paths: tuple[str, ...],
    ) -> None:
        self.page_count_reads = 0
        self.run_actions: list[str] = []
        self.XHwpDocuments = _Documents()
        self.XHwpWindows = _Windows(handle)
        _APPLICATIONS_BY_HANDLE[handle] = self
        for index, path in enumerate(paths, start=1):
            self.XHwpDocuments.items.append(_Document(self.XHwpDocuments, index, path))
        self.XHwpDocuments.active = self.XHwpDocuments.items[0]

    @property
    def PageCount(self) -> int:
        self.page_count_reads += 1
        return self.XHwpDocuments.Active_XHwpDocument.DocumentID + 1

    def RegisterModule(self, *, ModuleType: str, ModuleData: str) -> bool:
        return (
            ModuleType == "FilePathCheckDLL" and ModuleData == "FilePathCheckerModule"
        )

    def Open(
        self,
        path: str,
        format: str | None,
        arguments: str | None,
    ) -> bool:
        return self.XHwpDocuments.Active_XHwpDocument.Open(
            path,
            format,
            arguments,
        )

    def Run(self, action: str) -> bool:
        self.run_actions.append(action)
        if action != "FileClose":
            return False
        active = self.XHwpDocuments.Active_XHwpDocument
        self.XHwpDocuments.items.remove(active)
        self.XHwpDocuments.active = self.XHwpDocuments.items[0]
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
        return cast(_Application, cast(object, self.hwp)).PageCount

    @property
    def IsModified(self) -> bool:
        return bool(
            cast(
                _Application,
                cast(object, self.hwp),
            ).XHwpDocuments.Active_XHwpDocument.Modified
        )

    @property
    def current_page(self) -> int:
        return 1


@pytest.fixture(autouse=True)
def _native_snapshot_for_fake_applications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _APPLICATIONS_BY_HANDLE.clear()

    def read_snapshot(window_handle: int) -> NativeSnapshot | None:
        application = _APPLICATIONS_BY_HANDLE.get(window_handle)
        if application is None:
            return None
        document = application.XHwpDocuments.Active_XHwpDocument
        return NativeSnapshot(
            document_id=document.DocumentID,
            full_name=document.FullName,
            current_page=1,
            page_count=application.PageCount,
            modified=bool(document.Modified),
            cursor=NativePosition(0, 0, 0),
            selection=NativeSelection(
                selected=False,
                start=NativePosition(0, 0, 0),
                end=NativePosition(0, 0, 0),
                mode=0,
            ),
            selected_text="",
            control_type="",
            control_instance_id="",
            cell_address="",
            style_id=0,
            character_format=NativeCharacterFormat("", 0, False, 0),
            paragraph_format=NativeParagraphFormat(0, 0, 0, 0, 0, 0, 0),
        )

    monkeypatch.setattr(
        "hwp_live_session_core.read_native_snapshot",
        read_snapshot,
    )


@final
class _Catalog:
    def __init__(self, applications: tuple[_Application, ...]) -> None:
        self.applications = applications
        self.close_calls = 0

    def scan(self) -> tuple[HwpDocumentCandidate, ...]:
        candidates: list[HwpDocumentCandidate] = []
        for process_index, application in enumerate(self.applications, start=1):
            active = application.XHwpDocuments.Active_XHwpDocument
            for document in application.XHwpDocuments.items:
                candidates.append(
                    HwpDocumentCandidate(
                        selector=f"process-{process_index}-document-{document.DocumentID}",
                        moniker_name=f"!HwpObject.{process_index}",
                        application=cast(
                            HwpComApplication,
                            cast(object, application),
                        ),
                        document=cast(HwpComDocument, cast(object, document)),
                        document_id=document.DocumentID,
                        full_name=document.FullName,
                        document_format=document.Format,
                        edit_mode=document.EditMode,
                        window_handle=application.XHwpWindows.Active_XHwpWindow.WindowHandle,
                        active=document is active,
                    )
                )
        return tuple(candidates)

    def close(self) -> None:
        self.close_calls += 1


def _attach(
    candidate: HwpDocumentCandidate,
    wrapper: LiveHwpApplication | None = None,
) -> LiveHwpApplication:
    if wrapper is None:
        application = cast(
            _Application,
            cast(object, candidate.application),
        )
        return cast(
            LiveHwpApplication,
            cast(object, _Wrapper(application)),
        )
    wrapper.hwp = candidate.application
    return wrapper


def _release(wrapper: LiveHwpApplication) -> None:
    _ = wrapper


@final
class _Signal:
    def __init__(self) -> None:
        self.start_calls: list[tuple[int, str | None]] = []
        self.stop_calls = 0

    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        self.start_calls.append((process_id, moniker_name))

    def sequence(self) -> int:
        return 0

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        _ = (after_sequence, timeout_seconds)
        return 0

    def stop(self) -> None:
        self.stop_calls += 1


@final
class _BridgeController:
    def connection_moniker(self, session_id: str) -> str:
        return f"!HancomLiveBridge.{session_id}"

    def restore_activation(self) -> None:
        return

    def close(self) -> None:
        return


@final
class _WindowReader:
    def __init__(self, processes: dict[int, int]) -> None:
        self.processes = processes

    def read(self, window_handle: int) -> HancomWindowState:
        process_id = self.processes[window_handle]
        return HancomWindowState(
            window_handle=window_handle,
            process_id=process_id,
            exists=True,
            visible=True,
            enabled=True,
            foreground=True,
            title="Hwp",
            class_name="HwpFrame",
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


def test_controller_keeps_document_sessions_and_restores_active_tab() -> None:
    first_process = _Application(
        101,
        ("C:/alpha/report.hwp", "C:/beta/report.hwp"),
    )
    second_process = _Application(202, ("D:/other/report.hwp",))
    controller = LiveHwpController(
        catalog=_Catalog((first_process, second_process)),
        attacher=_attach,
        releaser=_release,
    )

    first = controller.connect("process-1-document-1")
    second = controller.connect("process-1-document-2")
    third = controller.connect("process-2-document-1")

    assert controller.session_count() == 3
    assert first.session_id != second.session_id != third.session_id
    assert first_process.XHwpDocuments.Active_XHwpDocument.DocumentID == 1
    _, _ = controller._validate(second.session_id)
    assert first_process.XHwpDocuments.Active_XHwpDocument.DocumentID == 2
    controller.restore_activation()
    assert first_process.XHwpDocuments.Active_XHwpDocument.DocumentID == 1
    assert controller.session_for_selector("process-1-document-1") is not None
    assert controller.session_for_selector("process-1-document-2") is not None
    controller.close()


def test_process_loss_invalidates_sessions_without_releasing_dead_com_wrappers() -> (
    None
):
    application = _Application(101, ("C:/alpha/report.hwp",))
    catalog = _Catalog((application,))
    released: list[LiveHwpApplication] = []
    controller = LiveHwpController(
        catalog=catalog,
        attacher=_attach,
        releaser=lambda wrapper: released.append(wrapper),
    )
    connected = controller.connect("process-1-document-1")

    controller.invalidate_process_loss()

    assert controller.session_count() == 0
    assert controller.current_session() is None
    assert controller.session_for_selector(connected.document.selector) is None
    assert released == []
    assert catalog.close_calls == 1


def test_connect_reports_restored_active_state_and_target_page_count() -> None:
    application = _Application(
        101,
        ("C:/alpha/report.hwp", "C:/beta/report.hwp"),
    )
    controller = LiveHwpController(
        catalog=_Catalog((application,)),
        attacher=_attach,
        releaser=_release,
    )

    connected = controller.connect("process-1-document-2")
    reconnected = controller.connect("process-1-document-2")

    assert application.XHwpDocuments.Active_XHwpDocument.DocumentID == 1
    assert connected.document.active is False
    assert connected.document.page_count == 3
    assert reconnected.document.active is False
    assert reconnected.document.page_count == 3
    controller.close()


def test_operation_restore_refreshes_stored_active_metadata() -> None:
    application = _Application(
        101,
        ("C:/alpha/report.hwp", "C:/beta/report.hwp"),
    )
    controller = LiveHwpController(
        catalog=_Catalog((application,)),
        attacher=_attach,
        releaser=_release,
    )
    first = controller.connect("process-1-document-1")
    second = controller.connect("process-1-document-2")

    _, _ = controller._validate(second.session_id)
    assert application.XHwpDocuments.Active_XHwpDocument.DocumentID == 2
    controller.restore_activation()
    first_after = controller.connect("process-1-document-1")
    second_after = controller.connect("process-1-document-2")

    assert first_after.session_id == first.session_id
    assert first_after.document.active is True
    assert first_after.document.page_count == 2
    assert second_after.session_id == second.session_id
    assert second_after.document.active is False
    assert second_after.document.page_count == 3
    controller.close()


def test_deferred_connection_activates_only_when_operation_starts() -> None:
    application = _Application(
        101,
        ("C:/alpha/report.hwp", "C:/beta/report.hwp"),
    )
    controller = LiveHwpController(
        catalog=_Catalog((application,)),
        attacher=_attach,
        releaser=_release,
    )

    connected = controller.connect_deferred("process-1-document-2")

    assert application.XHwpDocuments.Active_XHwpDocument.DocumentID == 1
    _, _ = controller._validate(connected.session_id)
    assert application.XHwpDocuments.Active_XHwpDocument.DocumentID == 2
    controller.restore_activation()
    assert application.XHwpDocuments.Active_XHwpDocument.DocumentID == 1
    controller.close()


def test_controller_opens_same_filename_in_selected_processes_and_restores_tabs(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "one" / "report.hwp"
    second_path = tmp_path / "two" / "report.hwp"
    active_path = tmp_path / "three" / "active.hwp"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    active_path.parent.mkdir()
    _ = first_path.write_bytes(b"first")
    _ = second_path.write_bytes(b"second")
    _ = active_path.write_bytes(b"active")
    first_process = _Application(101, ("C:/base/first.hwp",))
    second_process = _Application(202, ("D:/base/second.hwp",))
    controller = LiveHwpController(
        catalog=_Catalog((first_process, second_process)),
        attacher=_attach,
        releaser=_release,
    )

    first = controller.open_document(
        str(first_path),
        "process-1-document-1",
        True,
    )
    second = controller.open_document(
        str(second_path),
        "process-2-document-1",
        True,
    )
    duplicate = controller.open_document(
        str(first_path),
        "process-1-document-1",
        True,
    )

    assert first.full_name == str(first_path.resolve())
    assert second.full_name == str(second_path.resolve())
    assert first.title == second.title == "report.hwp"
    assert duplicate.selector == first.selector
    assert len(first_process.XHwpDocuments.items) == 2
    assert len(second_process.XHwpDocuments.items) == 2
    assert first_process.page_count_reads >= 1
    assert second_process.page_count_reads >= 1
    assert first_process.XHwpDocuments.Active_XHwpDocument.DocumentID == 1
    assert second_process.XHwpDocuments.Active_XHwpDocument.DocumentID == 1

    active = controller.open_document(
        str(active_path),
        "process-1-document-1",
        True,
        False,
    )

    assert active.full_name == str(active_path.resolve())
    assert active.active
    assert first_process.XHwpDocuments.Active_XHwpDocument.DocumentID == 3
    controller.close()


def test_open_document_failure_closes_added_tab_and_restores_reference(
    tmp_path: Path,
) -> None:
    # Given
    requested = tmp_path / "fails-to-open.hwp"
    _ = requested.write_bytes(b"fixture")
    application = _Application(101, ("C:/base/first.hwp",))
    controller = LiveHwpController(
        catalog=_Catalog((application,)),
        attacher=_attach,
        releaser=_release,
    )

    # When / Then
    with (
        patch.object(application, "Open", return_value=False),
        pytest.raises(HwpLiveError, match="문서를 열지 못했습니다"),
    ):
        _ = controller.open_document(
            str(requested),
            "process-1-document-1",
            True,
        )

    assert len(application.XHwpDocuments.items) == 1
    assert application.XHwpDocuments.Active_XHwpDocument.DocumentID == 1
    assert application.run_actions == ["FileClose"]
    controller.close()


def test_document_path_and_document_id_are_ambiguity_safe() -> None:
    documents = OpenDocumentList(
        documents=(
            OpenDocument(
                selector="one",
                title="report.hwp",
                full_name="C:/one/report.hwp",
                document_id=7,
                format="HWP",
                edit_mode=1,
                modified=False,
                page_count=1,
                active=True,
                window_handle=101,
            ),
            OpenDocument(
                selector="two",
                title="report.hwp",
                full_name="D:/two/report.hwp",
                document_id=7,
                format="HWP",
                edit_mode=1,
                modified=False,
                page_count=1,
                active=True,
                window_handle=202,
            ),
        )
    )

    assert select_operation_document(documents, "D:/two/report.hwp").selector == "two"
    assert select_operation_document(documents, "two").selector == "two"
    try:
        _ = select_operation_document(documents, "document_id:7")
    except Exception as error:
        assert "여러 HWP 프로세스" in str(error)
    else:
        raise AssertionError("duplicate document IDs must remain ambiguous")

    try:
        _ = select_operation_document(documents, "report.hwp")
    except Exception as error:
        message = str(error)
        assert "일치 후보 2개" in message
        assert "selector=one" in message
        assert "C:/one/report.hwp" in message
        assert "selector=two" in message
        assert "D:/two/report.hwp" in message
    else:
        raise AssertionError("a duplicated basename must return its candidates")

    try:
        _ = select_operation_document(documents, "missing-document")
    except Exception as error:
        assert "hwp_operate" not in str(error)
        assert "document_selector" in str(error)
    else:
        raise AssertionError("a missing selector must not choose an arbitrary document")


def test_inactive_candidate_keeps_its_own_scanned_page_count() -> None:
    application = _Application(
        101,
        ("C:/alpha/report.hwp", "C:/beta/report.hwp"),
    )
    document = application.XHwpDocuments.items[1]
    candidate = HwpDocumentCandidate(
        selector="process-1-document-2",
        moniker_name="!HancomLiveBridge.9001.101.2",
        application=cast(HwpComApplication, cast(object, application)),
        document=cast(HwpComDocument, cast(object, document)),
        document_id=document.DocumentID,
        full_name=document.FullName,
        document_format=document.Format,
        edit_mode=document.EditMode,
        window_handle=101,
        active=False,
        page_count=3,
    )

    public = candidate.public()

    assert public.active is False
    assert public.page_count == 3


def test_event_watchers_are_reused_once_per_hwp_process() -> None:
    first_signal = _Signal()
    second_signal = _Signal()
    controller = cast(
        LiveHwpController,
        cast(object, _BridgeController()),
    )
    bridge = HancomBridge(
        controller,
        window_reader=_WindowReader({101: 9001, 102: 9001, 202: 9002}),
        change_signal=first_signal,
    )

    def connected(session_id: str, handle: int) -> ConnectedDocument:
        return ConnectedDocument(
            session_id=session_id,
            document=OpenDocument(
                selector=session_id,
                title=f"{session_id}.hwp",
                full_name=f"C:/{session_id}.hwp",
                document_id=handle,
                format="HWP",
                edit_mode=1,
                modified=False,
                page_count=1,
                active=True,
                window_handle=handle,
            ),
        )

    with patch("hwp_live_bridge.HybridChangeSignal", return_value=second_signal):
        bridge._activate_connection(connected("one", 101))
        bridge._activate_connection(connected("two", 102))
        bridge._activate_connection(connected("three", 202))

    assert first_signal.start_calls == [(9001, "!HancomLiveBridge.one")]
    assert second_signal.start_calls == [(9002, "!HancomLiveBridge.three")]
    bridge._clear_connection_state("one")
    assert first_signal.stop_calls == 0
    bridge._clear_connection_state("two")
    assert first_signal.stop_calls == 1
    bridge._clear_connection_state("three")
    assert second_signal.stop_calls == 1
    bridge.close()

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

import hwp_live_session_structure_table as structure_table  # noqa: E402
import hwp_priority_object_runtime as object_runtime  # noqa: E402
import hwp_priority_table_recipes as table_recipes  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import (  # noqa: E402
    HwpComApplication,
    HwpComDocument,
    LiveHwpApplication,
)
from hwp_live_native_action_models import (  # noqa: E402
    NativeActionRequest,
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
    RunCommand,
)
from hwp_live_rot import HwpDocumentCandidate, HwpDocumentIdentity  # noqa: E402
from hwp_live_session_core import LiveHwpSessionCore  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    DocumentStructure,
    StructureCell,
    StructurePosition,
    StructureTable,
    TableCellUpdate,
    TableImageUpdate,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateAssets,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    WorkflowResolution,
)


_ORIGINAL = r"C:\documents\quarterly-report.hwp"
_RENAMED = r"C:\documents\quarterly-report-final.hwp"
_OTHER = r"C:\documents\unrelated.hwp"
_CONTROL_ID = "table-control-1"


# --------------------------------------------------------------------------
# Fake COM surface. Every property getter is a cross-process round trip in
# production, so each read is counted.
# --------------------------------------------------------------------------


@final
class _Counter:
    def __init__(self) -> None:
        self.log: list[str] = []

    def hit(self, name: str) -> None:
        self.log.append(name)

    def reset(self) -> None:
        self.log.clear()

    @property
    def reads(self) -> int:
        return len(self.log)


@final
class _Document:
    def __init__(
        self,
        counter: _Counter,
        documents: _Documents,
        document_id: int,
        path: str,
    ) -> None:
        self._counter = counter
        self._documents = documents
        self._document_id = document_id
        self._full_name = path

    def rename(self, path: str) -> None:
        """Simulate the user picking 다른 이름으로 저장 inside 한/글."""
        self._full_name = path

    @property
    def DocumentID(self) -> int:
        self._counter.hit("Document.DocumentID")
        return self._document_id

    @property
    def FullName(self) -> str:
        self._counter.hit("Document.FullName")
        return self._full_name

    @property
    def Format(self) -> str:
        self._counter.hit("Document.Format")
        return "HWP"

    @property
    def EditMode(self) -> int:
        self._counter.hit("Document.EditMode")
        return 1

    @property
    def Modified(self) -> int:
        self._counter.hit("Document.Modified")
        return 0

    def SetActive_XHwpDocument(self) -> None:
        self._counter.hit("Document.SetActive_XHwpDocument")
        self._documents.activate(self)


@final
class _Documents:
    def __init__(self, counter: _Counter) -> None:
        self._counter = counter
        self.items: list[_Document] = []
        self._active: _Document | None = None

    def activate(self, document: _Document) -> None:
        self._active = document

    @property
    def Count(self) -> int:
        self._counter.hit("Documents.Count")
        return len(self.items)

    @property
    def Active_XHwpDocument(self) -> _Document:
        self._counter.hit("Documents.Active_XHwpDocument")
        assert self._active is not None
        return self._active

    def Item(self, index: int) -> _Document:
        self._counter.hit("Documents.Item")
        return self.items[index]


@final
class _Window:
    def __init__(self, counter: _Counter, handle: int) -> None:
        self._counter = counter
        self._handle = handle

    @property
    def WindowHandle(self) -> int:
        self._counter.hit("Window.WindowHandle")
        return self._handle


@final
class _Windows:
    def __init__(self, counter: _Counter, handle: int) -> None:
        self._counter = counter
        self._window = _Window(counter, handle)

    @property
    def Active_XHwpWindow(self) -> _Window:
        self._counter.hit("Windows.Active_XHwpWindow")
        return self._window


@final
class _Application:
    def __init__(self, counter: _Counter, handle: int, paths: tuple[str, ...]) -> None:
        self._counter = counter
        self._documents = _Documents(counter)
        self._windows = _Windows(counter, handle)
        for index, path in enumerate(paths, start=1):
            self._documents.items.append(
                _Document(counter, self._documents, index, path)
            )
        self._documents.activate(self._documents.items[0])

    @property
    def documents(self) -> _Documents:
        return self._documents

    @property
    def XHwpDocuments(self) -> _Documents:
        self._counter.hit("Application.XHwpDocuments")
        return self._documents

    @property
    def XHwpWindows(self) -> _Windows:
        self._counter.hit("Application.XHwpWindows")
        return self._windows

    @property
    def PageCount(self) -> int:
        self._counter.hit("Application.PageCount")
        return 4

    def RegisterModule(self, *, ModuleType: str, ModuleData: str) -> bool:
        self._counter.hit("Application.RegisterModule")
        return (
            ModuleType == "FilePathCheckDLL" and ModuleData == "FilePathCheckerModule"
        )


def _as_application(application: _Application) -> HwpComApplication:
    return cast(HwpComApplication, cast(object, application))


def _as_document(document: _Document) -> HwpComDocument:
    return cast(HwpComDocument, cast(object, document))


def _candidate(
    application: _Application,
    document: _Document,
    *,
    full_name: str,
    document_id: int = 1,
    handle: int = 101,
) -> HwpDocumentCandidate:
    return HwpDocumentCandidate(
        selector="session-document",
        moniker_name="!HancomLiveBridge.9001.101.1",
        application=_as_application(application),
        document=_as_document(document),
        document_id=document_id,
        full_name=full_name,
        document_format="HWP",
        edit_mode=1,
        window_handle=handle,
        active=True,
        page_count=4,
    )


def _snapshot(document_id: int, full_name: str) -> NativeSnapshot:
    return NativeSnapshot(
        document_id=document_id,
        full_name=full_name,
        current_page=1,
        page_count=4,
        modified=True,
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


@final
class _NativeResult:
    commands_executed = 3
    elapsed_microseconds = 17


# --------------------------------------------------------------------------
# 1. Native requests must carry the identity the session confirmed.
# --------------------------------------------------------------------------


def test_object_command_request_keeps_session_identity_after_save_as() -> None:
    # Given a session bound to the original path
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL,))
    document = application.documents.items[0]
    candidate = _candidate(application, document, full_name=_ORIGINAL)
    # When the user saves the document under a new name mid-operation
    document.rename(_RENAMED)
    counter.reset()
    captured: list[NativeActionRequest] = []

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> _NativeResult:
        _ = (window_handle, minimum_version)
        captured.append(request)
        return _NativeResult()

    with (
        patch.object(object_runtime, "execute_native_actions", side_effect=execute),
        patch.object(
            object_runtime,
            "read_native_snapshot",
            return_value=_snapshot(1, _ORIGINAL),
        ),
    ):
        _ = object_runtime.execute_object_commands(
            candidate,
            (RunCommand("MoveDocEnd"),),
        )

    # Then the request targets the document the session confirmed, not the new path
    assert len(captured) == 1
    assert captured[0].document_id == 1
    assert captured[0].full_name == _ORIGINAL
    # and no raw COM re-read happened while building the request
    assert counter.log == []


def test_picture_caption_probe_keeps_session_identity_after_save_as() -> None:
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL,))
    document = application.documents.items[0]
    candidate = _candidate(application, document, full_name=_ORIGINAL)
    document.rename(_RENAMED)
    counter.reset()
    captured: list[NativeActionRequest] = []

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> _NativeResult:
        _ = (window_handle, minimum_version)
        captured.append(request)
        return _NativeResult()

    with (
        patch.object(object_runtime, "execute_native_actions", side_effect=execute),
        patch.object(
            object_runtime,
            "read_native_snapshot",
            return_value=_snapshot(1, _ORIGINAL),
        ),
    ):
        probe = object_runtime.probe_picture_caption(candidate, "control-1")

    assert probe.commands_executed == 3
    assert len(captured) == 1
    assert captured[0].full_name == _ORIGINAL
    assert counter.log == []


def test_picture_caption_probe_rejects_a_snapshot_from_another_document() -> None:
    # Safety: the snapshot check must compare against the session identity,
    # so a snapshot describing a different document is still refused.
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL,))
    document = application.documents.items[0]
    candidate = _candidate(application, document, full_name=_ORIGINAL)

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> _NativeResult:
        _ = (window_handle, request, minimum_version)
        return _NativeResult()

    with (
        patch.object(object_runtime, "execute_native_actions", side_effect=execute),
        patch.object(
            object_runtime,
            "read_native_snapshot",
            return_value=_snapshot(2, _OTHER),
        ),
        pytest.raises(HwpLiveError, match="대상 문서와 다릅니다"),
    ):
        _ = object_runtime.probe_picture_caption(candidate, "control-1")


def test_table_image_recipe_request_keeps_session_identity_after_save_as() -> None:
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL,))
    document = application.documents.items[0]
    candidate = _candidate(application, document, full_name=_ORIGINAL)
    document.rename(_RENAMED)
    counter.reset()

    table = StructureTable(
        table_ref="table-ref-00000001",
        control_instance_id=_CONTROL_ID,
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=1,
        columns=1,
        merges=(),
        cells=(
            StructureCell(
                address="A1",
                row=0,
                column=0,
                owner_address="A1",
                text="",
                has_picture=False,
            ),
        ),
    )
    before = DocumentStructure(
        selector="session-document",
        document_id=1,
        full_name=_ORIGINAL,
        window_handle=101,
        page=1,
        page_count=4,
        state_token="state-token-before-1",
        page_text="",
        paragraphs=(),
        controls=(),
        tables=(table,),
    )
    after = before.model_copy(
        update={
            "state_token": "state-token-after-01",
            "tables": (
                table.model_copy(
                    update={
                        "cells": (
                            table.cells[0].model_copy(update={"has_picture": True}),
                        )
                    }
                ),
            ),
        }
    )
    snapshots = iter((before, after))
    captured: list[NativeActionRequest] = []
    minimum_versions: list[int] = []

    def execute(
        _candidate: HwpDocumentCandidate,
        request: NativeActionRequest,
        minimum_version: int,
    ) -> tuple[int, int]:
        captured.append(request)
        minimum_versions.append(minimum_version)
        return 2, minimum_version

    with (
        patch.object(
            table_recipes,
            "inspect_candidate_structure",
            side_effect=lambda *args, **kwargs: next(snapshots),
        ),
        patch.object(table_recipes, "_execute", side_effect=execute),
    ):
        result = table_recipes.operate_table_recipe(
            candidate,
            cast(LiveHwpApplication, object()),
            WorkflowResolution(
                query="table.insert_images",
                status="resolved",
                lookup_microseconds=1,
                workflow_id="table.insert_images",
            ),
            HwpOperateTarget(
                kind="table",
                page_hint=1,
                control_instance_id=_CONTROL_ID,
            ),
            None,
            HwpOperateAssets(images={"A1": Path("image.png")}),
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            None,
            resolve_only=False,
            allow_document_change=True,
        )

    assert result is not None and result.status == "executed"
    assert len(captured) == 1
    assert captured[0].document_id == 1
    assert captured[0].full_name == _ORIGINAL
    assert minimum_versions == [9]
    assert counter.log == []


def test_validated_table_cell_update_keeps_session_identity_after_save_as() -> None:
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL,))
    document = application.documents.items[0]
    candidate = _candidate(application, document, full_name=_ORIGINAL)
    document.rename(_RENAMED)
    counter.reset()
    prepared_identities: list[tuple[int, str]] = []
    applied_identities: list[tuple[int, str]] = []

    def prepare(hwp: object, **kwargs: object) -> object:
        _ = hwp
        prepared_identities.append(
            (cast(int, kwargs["document_id"]), cast(str, kwargs["full_name"]))
        )
        return object()

    def apply(hwp: object, **kwargs: object) -> object:
        _ = hwp
        applied_identities.append(
            (cast(int, kwargs["document_id"]), cast(str, kwargs["full_name"]))
        )
        return object()

    with (
        patch.object(structure_table, "prepare_table_update", side_effect=prepare),
        patch.object(structure_table, "apply_table_update", side_effect=apply),
    ):
        _ = structure_table.update_validated_table_cells(
            cast(LiveHwpApplication, object()),
            candidate,
            "table-ref-00000001",
            "token",
            (TableCellUpdate(address="A1", expected_text="", replacement="x"),),
            set(),
            None,
            lambda: None,
        )

    assert prepared_identities == [(1, _ORIGINAL)]
    assert applied_identities == [(1, _ORIGINAL)]
    assert counter.log == []


def test_validated_table_image_insert_keeps_session_identity_after_save_as() -> None:
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL,))
    document = application.documents.items[0]
    candidate = _candidate(application, document, full_name=_ORIGINAL)
    document.rename(_RENAMED)
    counter.reset()
    prepared_identities: list[tuple[int, str]] = []
    applied_identities: list[tuple[int, str]] = []

    def prepare(hwp: object, **kwargs: object) -> object:
        _ = hwp
        prepared_identities.append(
            (cast(int, kwargs["document_id"]), cast(str, kwargs["full_name"]))
        )
        return object()

    def apply(hwp: object, **kwargs: object) -> object:
        _ = hwp
        applied_identities.append(
            (cast(int, kwargs["document_id"]), cast(str, kwargs["full_name"]))
        )
        return object()

    with (
        patch.object(structure_table, "prepare_table_images", side_effect=prepare),
        patch.object(structure_table, "apply_table_images", side_effect=apply),
    ):
        _ = structure_table.insert_validated_table_images(
            cast(LiveHwpApplication, object()),
            candidate,
            "table-ref-00000001",
            "token",
            (
                TableImageUpdate(
                    address="A1",
                    expected_text="",
                    path=Path("image.png"),
                ),
            ),
            set(),
            None,
            lambda: None,
        )

    assert prepared_identities == [(1, _ORIGINAL)]
    assert applied_identities == [(1, _ORIGINAL)]
    assert counter.log == []


# --------------------------------------------------------------------------
# 2. _validate identity confirmation period.
# --------------------------------------------------------------------------


@final
class _IdentityCatalog:
    """Mirrors the COM access shape of HwpRotCatalog.resolve_identity."""

    def __init__(self, application: _Application) -> None:
        self._application = application
        self.resolve_calls = 0
        self.release_calls = 0
        self.close_calls = 0

    def _build(
        self,
        document: _Document,
        *,
        document_id: int,
        full_name: str,
        handle: int,
        active: bool,
        page_count: int | None,
    ) -> HwpDocumentCandidate:
        return HwpDocumentCandidate(
            selector=f"document-{document_id}",
            moniker_name=f"!HancomLiveBridge.9001.101.{document_id}",
            application=_as_application(self._application),
            document=_as_document(document),
            document_id=document_id,
            full_name=full_name,
            document_format=document.Format,
            edit_mode=document.EditMode,
            window_handle=handle,
            active=active,
            page_count=page_count,
        )

    def scan(self) -> tuple[HwpDocumentCandidate, ...]:
        documents = self._application.XHwpDocuments
        active = documents.Active_XHwpDocument
        handle = self._application.XHwpWindows.Active_XHwpWindow.WindowHandle
        built: list[HwpDocumentCandidate] = []
        for index in range(documents.Count):
            document = documents.Item(index)
            is_active = document is active
            built.append(
                self._build(
                    document,
                    document_id=document.DocumentID,
                    full_name=document.FullName,
                    handle=handle,
                    active=is_active,
                    page_count=self._application.PageCount if is_active else None,
                )
            )
        return tuple(built)

    def resolve_identity(
        self,
        identity: HwpDocumentIdentity,
    ) -> HwpDocumentCandidate:
        # Same COM access shape as HwpRotCatalog.resolve_identity so the read
        # counts below mean what they say.
        self.resolve_calls += 1
        documents = self._application.XHwpDocuments
        active = documents.Active_XHwpDocument
        active_id = active.DocumentID
        active_path = active.FullName
        handle = self._application.XHwpWindows.Active_XHwpWindow.WindowHandle
        matches: list[_Document] = []
        for index in range(documents.Count):
            document = documents.Item(index)
            if document.DocumentID != identity.document_id:
                continue
            if document.FullName.lower() != identity.full_name.lower():
                continue
            matches.append(document)
        if len(matches) != 1:
            raise HwpLiveError(
                "캐시된 신원과 일치하는 한컴 문서를 정확히 하나 찾지 못했습니다"
            )
        target = matches[0]
        full_name = target.FullName
        is_active = (
            active_id == identity.document_id
            and active_path.lower() == full_name.lower()
        )
        return self._build(
            target,
            document_id=identity.document_id,
            full_name=full_name,
            handle=handle,
            active=is_active,
            page_count=(
                self._application.PageCount if is_active else identity.page_count
            ),
        )

    def release_com_references(self) -> None:
        self.release_calls += 1

    def close(self) -> None:
        self.close_calls += 1


@final
class _Wrapper:
    def __init__(self, application: _Application) -> None:
        self.hwp = _as_application(application)
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
        return False

    @property
    def current_page(self) -> int:
        return 1


def _attach(
    candidate: HwpDocumentCandidate,
    wrapper: LiveHwpApplication | None = None,
) -> LiveHwpApplication:
    # Mirrors hwp_live_rot.attach_wrapper, including its identity re-check.
    from hwp_live_rot import require_active_candidate

    require_active_candidate(candidate, candidate.application)
    if wrapper is None:
        return cast(
            LiveHwpApplication,
            cast(
                object,
                _Wrapper(cast(_Application, cast(object, candidate.application))),
            ),
        )
    wrapper.hwp = candidate.application
    return wrapper


def _release(wrapper: LiveHwpApplication) -> None:
    _ = wrapper


def _core(
    application: _Application,
    selector: str = "document-1",
) -> tuple[LiveHwpSessionCore, _IdentityCatalog, str]:
    catalog = _IdentityCatalog(application)
    core = LiveHwpSessionCore(
        catalog=cast(object, catalog),  # pyright: ignore[reportArgumentType]
        attacher=_attach,
        releaser=_release,
    )
    connected = core.connect_deferred(selector)
    return core, catalog, connected.session_id


@pytest.fixture(autouse=True)
def _no_native_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hwp_live_session_core.read_native_snapshot",
        lambda window_handle: None,
    )


def test_identity_is_confirmed_once_per_activation_window() -> None:
    # A single tool call such as propagate_table_cells enters _validate
    # several times. The document identity must be resolved once, not once
    # per entry.
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL, _OTHER))
    core, catalog, session_id = _core(application)
    counter.reset()

    deltas: list[int] = []
    previous = 0
    for _ in range(4):
        _, _ = core._validate(session_id)
        deltas.append(counter.reads - previous)
        previous = counter.reads

    # Identity is resolved from the running object table exactly once.
    assert catalog.resolve_calls == 1
    # Re-entry costs only the live re-confirmation: XHwpDocuments,
    # Active_XHwpDocument, DocumentID, FullName, PageCount, Modified.
    assert deltas[1:] == [6, 6, 6]
    # Before this change every re-entry cost a full resolve: 27 + 2 * D reads
    # (31 for the two open documents here), so the window cost 40 + 31 * 3.
    assert counter.reads == deltas[0] + 18
    assert counter.reads < 70

    core.restore_activation()
    core.close()


@pytest.mark.parametrize(
    "teardown",
    (
        "restore_activation",
        "disconnect",
        "release_transient",
        "invalidate_process_loss",
        "close",
    ),
)
def test_confirmed_identity_is_dropped_at_every_invalidation_point(
    teardown: str,
) -> None:
    # Safety: the confirmed identity may only survive inside one activation
    # window. Every teardown path must drop it explicitly, not by relying on
    # the wrapper release happening to run first.
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL, _OTHER))
    core, _, session_id = _core(application)

    _, _ = core._validate(session_id)
    assert core._validated_candidate is not None

    if teardown == "disconnect":
        _ = core.disconnect(session_id)
    elif teardown == "release_transient":
        _ = core.release_transient(session_id)
    else:
        getattr(core, teardown)()

    assert core._validated_candidate is None
    core.close()


def test_confirmed_identity_is_dropped_when_the_last_session_goes_idle() -> None:
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL, _OTHER))
    core, _, session_id = _core(application)

    candidate, _ = core._validate(session_id)
    _ = core.release_transient(session_id)
    # Re-arm the field to prove release_idle_references drops it on its own.
    core._validated_candidate = candidate
    core.release_idle_references()

    assert core._validated_candidate is None
    core.close()


def test_each_activation_window_reconfirms_identity() -> None:
    # Invalidation: restore_activation ends the window, so the next tool call
    # resolves the identity again from the running object table.
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL, _OTHER))
    core, catalog, session_id = _core(application)

    _, _ = core._validate(session_id)
    core.restore_activation()
    _, _ = core._validate(session_id)
    core.restore_activation()

    assert catalog.resolve_calls == 2
    core.close()


def test_reused_identity_is_never_served_for_a_switched_document() -> None:
    # Safety: if 한/글 switches to a different tab mid-window the reused
    # identity must not be handed out; the full resolve path runs again and
    # brings the target document back.
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL, _OTHER))
    core, catalog, session_id = _core(application)

    candidate, _ = core._validate(session_id)
    assert candidate.full_name == _ORIGINAL
    resolved_once = catalog.resolve_calls

    application.documents.activate(application.documents.items[1])
    candidate, _ = core._validate(session_id)

    assert catalog.resolve_calls == resolved_once + 1
    assert candidate.document_id == 1
    assert candidate.full_name == _ORIGINAL
    assert application.documents.Active_XHwpDocument.DocumentID == 1
    core.restore_activation()
    core.close()


def test_save_as_inside_the_window_fails_closed_instead_of_following_the_path() -> None:
    # Safety: a 다른 이름으로 저장 during the window breaks the confirmed
    # identity. The session must refuse rather than adopt the new path.
    counter = _Counter()
    application = _Application(counter, 101, (_ORIGINAL, _OTHER))
    core, _, session_id = _core(application)

    _, _ = core._validate(session_id)
    application.documents.items[0].rename(_RENAMED)

    with pytest.raises(HwpLiveError, match="정확히 하나 찾지 못했습니다"):
        _, _ = core._validate(session_id)
    core.close()


def test_activation_restore_survives_repeated_validation() -> None:
    # The originally active tab must still be restored after a tool call that
    # entered _validate more than once.
    counter = _Counter()
    application = _Application(counter, 101, (_OTHER, _ORIGINAL))
    core, _, session_id = _core(application, "document-2")

    assert application.documents.Active_XHwpDocument.DocumentID == 1
    _, _ = core._validate(session_id)
    _, _ = core._validate(session_id)
    assert application.documents.Active_XHwpDocument.DocumentID == 2

    core.restore_activation()

    assert application.documents.Active_XHwpDocument.DocumentID == 1
    core.close()

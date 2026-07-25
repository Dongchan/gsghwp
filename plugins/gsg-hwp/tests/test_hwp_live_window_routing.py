from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from pathlib import Path
from typing import cast, final
from unittest.mock import patch


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_batch import (  # noqa: E402
    _batch_for_window,
    _source_for_window,
    _Win32Client,
    activate_native_document,
    clear_native_document_route,
    select_native_document_route,
)
from hwp_live_rot import (  # noqa: E402
    HwpRotCatalog,
    _addon_document_id,
    _addon_window_handle,
)


@final
class _Moniker:
    def __init__(self, name: str) -> None:
        self.name = name

    def GetDisplayName(self, context: object, moniker: object) -> str:
        _ = (context, moniker)
        return self.name


@final
class _Source:
    def QueryInterface(self, interface_id: object) -> object:
        _ = interface_id
        return self


@final
class _Rot:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.monikers = tuple(_Moniker(name) for name in names)
        self.sources = {moniker.name: _Source() for moniker in self.monikers}

    def EnumRunning(self) -> tuple[_Moniker, ...]:
        return self.monikers

    def GetObject(self, moniker: object) -> _Source:
        assert isinstance(moniker, _Moniker)
        return self.sources[moniker.name]


@final
class _PythonCom:
    IID_IDispatch = object()

    def __init__(self, rot: _Rot) -> None:
        self.rot = rot

    def CoInitialize(self) -> None:
        pass

    def CoUninitialize(self) -> None:
        pass

    def CreateBindCtx(self, reserved: int) -> object:
        _ = reserved
        return object()

    def GetRunningObjectTable(self) -> _Rot:
        return self.rot


@final
class _Process:
    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]:
        _ = window_handle
        return 7, 1234


@final
class _DynamicBatch:
    ProtocolVersion = 12
    TargetDocumentID = 0

    def __init__(self) -> None:
        self.activated_document_id = 0

    def ActivateDocument(self, document_id: int) -> str:
        self.activated_document_id = document_id
        return "Local\\HancomLiveActivation.test"

    def ActivationStatus(self, document_id: int) -> int:
        return 1 if document_id == self.activated_document_id else -1


@final
class _EventHandle:
    def Close(self) -> None:
        return


@final
class _Events:
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258

    def OpenEvent(
        self,
        desired_access: int,
        inherit_handle: bool,
        name: str,
    ) -> _EventHandle:
        _ = desired_access, inherit_handle, name
        return _EventHandle()

    def WaitForMultipleObjects(
        self,
        handles: tuple[_EventHandle, ...],
        wait_all: bool,
        milliseconds: int,
    ) -> int:
        _ = handles, wait_all, milliseconds
        return self.WAIT_OBJECT_0


@final
class _BatchClient:
    def __init__(self, batch: _DynamicBatch) -> None:
        self.batch = batch

    def Dispatch(self, source: object) -> _DynamicBatch:
        _ = source
        return self.batch


@final
class _CatalogDocument:
    def __init__(self, documents: _CatalogDocuments, document_id: int) -> None:
        self._documents = documents
        self.DocumentID = document_id
        self.FullName = f"C:/docs/{document_id}.hwp"
        self.Format = "HWP"
        self.EditMode = 1
        self.Modified = 0

    def SetActive_XHwpDocument(self) -> None:
        self._documents.active = self


@final
class _CatalogDocuments:
    def __init__(self) -> None:
        self.items = (
            _CatalogDocument(self, 1),
            _CatalogDocument(self, 2),
        )
        self.active = self.items[0]

    @property
    def Count(self) -> int:
        return len(self.items)

    @property
    def Active_XHwpDocument(self) -> _CatalogDocument:
        return self.active

    def Item(self, index: int) -> _CatalogDocument:
        return self.items[index]


@final
class _CatalogWindow:
    WindowHandle = 5678


@final
class _CatalogWindows:
    Active_XHwpWindow = _CatalogWindow()


@final
class _CatalogApplication:
    def __init__(self) -> None:
        self.XHwpDocuments = _CatalogDocuments()
        self.XHwpWindows = _CatalogWindows()

    @property
    def PageCount(self) -> int:
        return 2


@final
class _CatalogClient:
    def __init__(self, application: _CatalogApplication) -> None:
        self.application = application

    def Dispatch(self, source: object) -> _CatalogApplication:
        _ = source
        return self.application


def test_native_batch_prefers_exact_process_window_moniker() -> None:
    clear_native_document_route(5678)
    rot = _Rot(
        (
            "!HancomLiveBatch.1234",
            "!HancomLiveBatch.1234.5678",
        )
    )

    source = _source_for_window(5678, _PythonCom(rot), _Process())

    assert source is rot.sources["!HancomLiveBatch.1234.5678"]


def test_rot_catalog_keeps_fallback_documents_missing_scoped_monikers() -> None:
    rot = _Rot(
        (
            "!HancomLiveBridge.1234",
            "!HancomLiveBridge.1234.5678",
            "!HancomLiveBridge.1234.5678.1",
        )
    )
    application = _CatalogApplication()
    with (
        patch("hwp_live_rot._load_pythoncom", return_value=_PythonCom(rot)),
        patch(
            "hwp_live_rot._load_win32_client",
            return_value=_CatalogClient(application),
        ),
    ):
        catalog = HwpRotCatalog(cache_ttl_seconds=0)
        candidates = catalog.scan(force=True)
        catalog.close()

    assert [candidate.document_id for candidate in candidates] == [1, 2]
    assert candidates[0].moniker_name == "!HancomLiveBridge.1234.5678.1"
    assert candidates[1].moniker_name == "!HancomLiveBridge.1234"


def test_native_batch_falls_back_to_legacy_process_moniker() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234",))

    source = _source_for_window(5678, _PythonCom(rot), _Process())

    assert source is rot.sources["!HancomLiveBatch.1234"]


def test_native_batch_falls_back_when_selected_document_moniker_is_missing() -> None:
    rot = _Rot(
        (
            "!HancomLiveBatch.1234",
            "!HancomLiveBatch.1234.5678",
            "!HancomLiveBatch.1234.5678.42",
        )
    )
    select_native_document_route(5678, 42)
    try:
        source = _source_for_window(5678, _PythonCom(rot), _Process())
        assert source is rot.sources["!HancomLiveBatch.1234.5678.42"]

        missing = _Rot(
            (
                "!HancomLiveBatch.1234",
                "!HancomLiveBatch.1234.5678",
            )
        )
        assert _source_for_window(
            5678,
            _PythonCom(missing),
            _Process(),
        ) is missing.sources["!HancomLiveBatch.1234.5678"]
    finally:
        clear_native_document_route(5678)


def test_dynamic_window_batch_accepts_selected_document_route() -> None:
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    select_native_document_route(5678, 42)
    try:
        selected = _batch_for_window(
            5678,
            12,
            _PythonCom(rot),
            cast(_Win32Client, cast(object, _BatchClient(batch))),
            _Process(),
        )
    finally:
        clear_native_document_route(5678)

    assert selected is batch


def test_native_document_activation_uses_dynamic_window_batch() -> None:
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    pythoncom = _PythonCom(rot)
    batch = _DynamicBatch()
    client = cast(_Win32Client, cast(object, _BatchClient(batch)))
    with (
        patch(
            "hwp_live_native_batch._modules",
            return_value=(pythoncom, client, _Process()),
        ),
        patch(
            "hwp_live_native_batch.import_module",
            return_value=_Events(),
        ),
    ):
        activated = activate_native_document(5678, 42)

    assert activated
    assert batch.activated_document_id == 42
    clear_native_document_route(5678)


def test_addon_window_handle_only_accepts_pid_handle_moniker() -> None:
    assert _addon_window_handle("!HancomLiveBridge.1234.5678") == 5678
    assert _addon_window_handle("!HancomLiveBridge.1234.5678.42") == 5678
    assert _addon_window_handle("!HancomLiveBridge.1234") is None
    assert _addon_window_handle("!HancomLiveBridge.bad.5678") is None
    assert _addon_window_handle("!HwpObject.1234.5678") is None
    assert _addon_document_id("!HancomLiveBridge.1234.5678.42") == 42
    assert _addon_document_id("!HancomLiveBridge.1234.5678") is None

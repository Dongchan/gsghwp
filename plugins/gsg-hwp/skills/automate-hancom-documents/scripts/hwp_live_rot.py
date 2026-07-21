from __future__ import annotations

import hashlib
import ntpath
from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from importlib import import_module
from io import StringIO
from typing import Protocol, final, runtime_checkable

from hwp_errors import HwpLiveError
from hwp_live_addon import ADDON_MONIKER_PREFIX
from hwp_live_api import (
    HwpComApplication,
    HwpComDocument,
    LiveHwpApplication,
)
from hwp_live_contract import OpenDocument
from hwp_live_wrapper import detached_live_wrapper


class BindContext(Protocol):
    pass


class InterfaceIdentifier(Protocol):
    pass


class RotMoniker(Protocol):
    def GetDisplayName(
        self,
        context: BindContext,
        moniker: RotMoniker,
    ) -> str: ...


class DispatchSource(Protocol):
    def QueryInterface(
        self,
        interface_id: InterfaceIdentifier,
    ) -> DispatchSource: ...


class RunningObjectTable(Protocol):
    def EnumRunning(self) -> Iterable[RotMoniker]: ...

    def GetObject(self, moniker: RotMoniker) -> DispatchSource: ...


@runtime_checkable
class PythonComModule(Protocol):
    @property
    def IID_IDispatch(self) -> InterfaceIdentifier: ...

    def CoInitialize(self) -> None: ...

    def CoUninitialize(self) -> None: ...

    def CreateBindCtx(self, reserved: int) -> BindContext: ...

    def GetRunningObjectTable(self) -> RunningObjectTable: ...


@runtime_checkable
class Win32ClientModule(Protocol):
    def Dispatch(self, source: DispatchSource) -> HwpComApplication: ...


@runtime_checkable
class PyWinTypesModule(Protocol):
    @property
    def com_error(self) -> type[Exception]: ...


def _load_pythoncom() -> PythonComModule:
    module = import_module("pythoncom")
    if not isinstance(module, PythonComModule):
        raise HwpLiveError("Windows COM 런타임을 찾을 수 없습니다")
    return module


def _load_win32_client() -> Win32ClientModule:
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        module = import_module("win32com.client")
    if not isinstance(module, Win32ClientModule):
        raise HwpLiveError("Windows COM 디스패치 런타임을 찾을 수 없습니다")
    return module


def _load_com_error() -> type[Exception]:
    module = import_module("pywintypes")
    if not isinstance(module, PyWinTypesModule):
        raise HwpLiveError("Windows COM 오류 형식을 찾을 수 없습니다")
    return module.com_error


_COM_ERROR = _load_com_error()
def _normalized_full_name(full_name: str) -> str:
    return ntpath.normcase(ntpath.normpath(full_name)) if full_name else ""


def _selector(moniker_name: str, document_id: int, full_name: str) -> str:
    identity = f"{moniker_name}\0{document_id}\0{_normalized_full_name(full_name)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class HwpDocumentCandidate:
    selector: str
    moniker_name: str
    application: HwpComApplication
    document: HwpComDocument
    document_id: int
    full_name: str
    document_format: str
    edit_mode: int
    window_handle: int
    active: bool

    def public(self) -> OpenDocument:
        title = ntpath.basename(self.full_name) if self.full_name else "저장되지 않은 문서"
        pages = self.application.PageCount if self.active else 0
        return OpenDocument(
            selector=self.selector,
            title=title,
            full_name=self.full_name,
            document_id=self.document_id,
            format=self.document_format,
            edit_mode=self.edit_mode,
            modified=bool(self.document.Modified),
            page_count=pages,
            active=self.active,
            window_handle=self.window_handle,
        )


@dataclass(frozen=True, slots=True)
class _ComRuntime:
    pythoncom: PythonComModule
    win32_client: Win32ClientModule


@final
class HwpRotCatalog:
    __slots__ = ("_initialized", "_runtime")

    _initialized: bool
    _runtime: _ComRuntime | None

    def __init__(self) -> None:
        self._initialized = False
        self._runtime = None

    def _loaded_runtime(self) -> _ComRuntime:
        runtime = self._runtime
        if runtime is None:
            runtime = _ComRuntime(_load_pythoncom(), _load_win32_client())
            self._runtime = runtime
        return runtime

    def scan(self) -> tuple[HwpDocumentCandidate, ...]:
        runtime = self._loaded_runtime()
        if not self._initialized:
            runtime.pythoncom.CoInitialize()
            self._initialized = True
        context = runtime.pythoncom.CreateBindCtx(0)
        rot = runtime.pythoncom.GetRunningObjectTable()
        candidates: dict[tuple[str, int, int], HwpDocumentCandidate] = {}
        matching_errors = 0
        try:
            for moniker in rot.EnumRunning():
                try:
                    name = moniker.GetDisplayName(context, moniker)
                except _COM_ERROR:
                    continue
                if not name.startswith(("!HwpObject.", ADDON_MONIKER_PREFIX)):
                    continue
                try:
                    source = rot.GetObject(moniker).QueryInterface(
                        runtime.pythoncom.IID_IDispatch
                    )
                    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                        application = runtime.win32_client.Dispatch(source)
                    documents = application.XHwpDocuments
                    active_id = documents.Active_XHwpDocument.DocumentID
                    handle = application.XHwpWindows.Active_XHwpWindow.WindowHandle
                    for index in range(documents.Count):
                        document = documents.Item(index)
                        document_id = int(document.DocumentID)
                        full_name = str(document.FullName)
                        candidate = HwpDocumentCandidate(
                            selector=_selector(
                                name,
                                document_id,
                                full_name,
                            ),
                            moniker_name=name,
                            application=application,
                            document=document,
                            document_id=document_id,
                            full_name=full_name,
                            document_format=str(document.Format),
                            edit_mode=int(document.EditMode),
                            window_handle=handle,
                            active=document_id == active_id,
                        )
                        identity = (
                            _normalized_full_name(full_name),
                            document_id,
                            handle,
                        )
                        previous = candidates.get(identity)
                        if previous is None or (
                            name.startswith(ADDON_MONIKER_PREFIX)
                            and not previous.moniker_name.startswith(
                                ADDON_MONIKER_PREFIX
                            )
                        ):
                            candidates[identity] = candidate
                except _COM_ERROR:
                    matching_errors += 1
        except _COM_ERROR as error:
            raise HwpLiveError("열려 있는 한컴 문서 목록을 읽을 수 없습니다") from error
        if not candidates and matching_errors:
            raise HwpLiveError("열려 있는 한컴 문서 목록을 읽을 수 없습니다")
        return tuple(candidates.values())

    def close(self) -> None:
        if not self._initialized:
            return
        self._loaded_runtime().pythoncom.CoUninitialize()
        self._initialized = False


def require_active_candidate(
    candidate: HwpDocumentCandidate,
    application: HwpComApplication,
) -> None:
    try:
        active = application.XHwpDocuments.Active_XHwpDocument
        if not candidate.active or active.DocumentID != candidate.document_id:
            raise HwpLiveError("활성 한컴 문서가 연결 대상과 다릅니다")
        if _normalized_full_name(active.FullName) != _normalized_full_name(
            candidate.full_name
        ):
            raise HwpLiveError("활성 한컴 문서 경로가 연결 대상과 다릅니다")
    except _COM_ERROR as error:
        raise HwpLiveError("대상 한컴 문서 상태를 확인할 수 없습니다") from error


def attach_wrapper(
    candidate: HwpDocumentCandidate,
    wrapper: LiveHwpApplication | None = None,
) -> LiveHwpApplication:
    if not candidate.active:
        raise HwpLiveError(
            "연결 대상 한컴 탭이 활성 상태가 아닙니다. 대상 탭을 먼저 활성화하세요"
        )
    require_active_candidate(candidate, candidate.application)
    if wrapper is None:
        wrapper = detached_live_wrapper()
    wrapper.hwp = candidate.application
    wrapper.on_quit = False
    wrapper.htf_fonts = {}
    return wrapper


def release_wrapper(wrapper: LiveHwpApplication) -> None:
    del wrapper.hwp

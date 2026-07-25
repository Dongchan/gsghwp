from __future__ import annotations

import hashlib
import ntpath
import time
from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
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
from hwp_live_native_batch import (
    activate_native_document,
)
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


def _addon_route(moniker_name: str) -> tuple[int, int | None] | None:
    if not moniker_name.startswith(ADDON_MONIKER_PREFIX):
        return None
    suffix = moniker_name[len(ADDON_MONIKER_PREFIX) :]
    parts = suffix.split(".")
    if len(parts) not in (2, 3):
        return None
    process_id_text = parts[0]
    window_handle_text = parts[1]
    document_id_text = parts[2] if len(parts) == 3 else None
    if not process_id_text.isdecimal() or not window_handle_text.isdecimal():
        return None
    process_id = int(process_id_text)
    window_handle = int(window_handle_text)
    if process_id <= 0 or window_handle <= 0:
        return None
    if document_id_text is None:
        return window_handle, None
    if not document_id_text.isdecimal():
        return None
    document_id = int(document_id_text)
    if document_id <= 0:
        return None
    return window_handle, document_id


def _addon_window_handle(moniker_name: str) -> int | None:
    route = _addon_route(moniker_name)
    return None if route is None else route[0]


def _addon_document_id(moniker_name: str) -> int | None:
    route = _addon_route(moniker_name)
    return None if route is None else route[1]


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
class ActiveDocumentRestore:
    application: HwpComApplication
    document: HwpComDocument
    document_id: int
    full_name: str
    window_handle: int
    native: bool


@dataclass(frozen=True, slots=True)
class _ComRuntime:
    pythoncom: PythonComModule
    win32_client: Win32ClientModule


@final
class HwpRotCatalog:
    __slots__ = (
        "_cache_deadline",
        "_cache_ttl_seconds",
        "_cached_candidates",
        "_initialized",
        "_runtime",
    )

    _cache_deadline: float
    _cache_ttl_seconds: float
    _cached_candidates: tuple[HwpDocumentCandidate, ...] | None
    _initialized: bool
    _runtime: _ComRuntime | None

    def __init__(self, *, cache_ttl_seconds: float = 0.5) -> None:
        if cache_ttl_seconds < 0:
            raise ValueError("ROT 캐시 유지 시간은 0 이상이어야 합니다")
        self._cache_deadline = 0.0
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_candidates = None
        self._initialized = False
        self._runtime = None

    def _loaded_runtime(self) -> _ComRuntime:
        runtime = self._runtime
        if runtime is None:
            runtime = _ComRuntime(_load_pythoncom(), _load_win32_client())
            self._runtime = runtime
        return runtime

    def scan(self, *, force: bool = False) -> tuple[HwpDocumentCandidate, ...]:
        now = time.monotonic()
        if (
            not force
            and self._cached_candidates is not None
            and now < self._cache_deadline
        ):
            return self._cached_candidates
        runtime = self._loaded_runtime()
        if not self._initialized:
            runtime.pythoncom.CoInitialize()
            self._initialized = True
        context = runtime.pythoncom.CreateBindCtx(0)
        rot = runtime.pythoncom.GetRunningObjectTable()
        document_candidates: dict[
            tuple[str, int, int], HwpDocumentCandidate
        ] = {}
        window_candidates: dict[
            tuple[str, int, int], HwpDocumentCandidate
        ] = {}
        fallback_candidates: dict[
            tuple[str, int, int], HwpDocumentCandidate
        ] = {}
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
                    active_id = int(
                        documents.Active_XHwpDocument.DocumentID
                    )
                    observed_handle = int(
                        application.XHwpWindows.Active_XHwpWindow.WindowHandle
                    )
                    scoped_handle = _addon_window_handle(name)
                    scoped_document_id = _addon_document_id(name)
                    if (
                        scoped_document_id is None
                        and scoped_handle is not None
                        and observed_handle != scoped_handle
                    ):
                        matching_errors += 1
                        continue
                    handle = scoped_handle or observed_handle
                    target_candidates = (
                        document_candidates
                        if scoped_document_id is not None
                        else window_candidates
                        if scoped_handle is not None
                        else fallback_candidates
                    )
                    for index in range(documents.Count):
                        document = documents.Item(index)
                        document_id = int(document.DocumentID)
                        if (
                            scoped_document_id is not None
                            and document_id != scoped_document_id
                        ):
                            continue
                        if (
                            scoped_document_id is None
                            and scoped_handle is not None
                            and document_id != active_id
                        ):
                            continue
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
                        previous = target_candidates.get(identity)
                        if previous is None or (
                            name.startswith(ADDON_MONIKER_PREFIX)
                            and not previous.moniker_name.startswith(
                                ADDON_MONIKER_PREFIX
                            )
                        ):
                            target_candidates[identity] = candidate
                except _COM_ERROR:
                    matching_errors += 1
        except _COM_ERROR as error:
            raise HwpLiveError("열려 있는 한컴 문서 목록을 읽을 수 없습니다") from error
        candidates = dict(fallback_candidates)
        candidates.update(window_candidates)
        candidates.update(document_candidates)
        if not candidates and matching_errors:
            raise HwpLiveError("열려 있는 한컴 문서 목록을 읽을 수 없습니다")
        result = tuple(candidates.values())
        self._cached_candidates = result
        self._cache_deadline = time.monotonic() + self._cache_ttl_seconds
        return result

    def close(self) -> None:
        self._cached_candidates = None
        self._cache_deadline = 0.0
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


def candidate_supports_native_activation(
    candidate: HwpDocumentCandidate,
) -> bool:
    return candidate.moniker_name.startswith(ADDON_MONIKER_PREFIX)


def activate_candidate(
    candidate: HwpDocumentCandidate,
) -> tuple[HwpDocumentCandidate, ActiveDocumentRestore | None]:
    application = candidate.application
    try:
        active = application.XHwpDocuments.Active_XHwpDocument
        active_id = int(active.DocumentID)
        active_path = str(active.FullName)
        if (
            active_id == candidate.document_id
            and _normalized_full_name(active_path)
            == _normalized_full_name(candidate.full_name)
        ):
            return replace(candidate, active=True), None
        restore = ActiveDocumentRestore(
            application=application,
            document=active,
            document_id=active_id,
            full_name=active_path,
            window_handle=candidate.window_handle,
            native=candidate_supports_native_activation(candidate),
        )
        try:
            if restore.native:
                if not activate_native_document(
                    candidate.window_handle,
                    candidate.document_id,
                ):
                    raise HwpLiveError(
                        "한컴 네이티브 문서 탭 전환을 사용할 수 없습니다"
                    )
            else:
                candidate.document.SetActive_XHwpDocument()
            activated = application.XHwpDocuments.Active_XHwpDocument
            if (
                int(activated.DocumentID) != candidate.document_id
                or _normalized_full_name(str(activated.FullName))
                != _normalized_full_name(candidate.full_name)
            ):
                raise HwpLiveError("지정한 한컴 문서 탭을 활성화하지 못했습니다")
        except (HwpLiveError, _COM_ERROR):
            restore_active_document(restore)
            raise
        return replace(candidate, active=True), restore
    except _COM_ERROR as error:
        raise HwpLiveError("지정한 한컴 문서 탭을 활성화하지 못했습니다") from error


def restore_active_document(restore: ActiveDocumentRestore | None) -> None:
    if restore is None:
        return
    try:
        if restore.native:
            if not activate_native_document(
                restore.window_handle,
                restore.document_id,
            ):
                raise HwpLiveError(
                    "한컴 네이티브 문서 탭 복원을 사용할 수 없습니다"
                )
        else:
            restore.document.SetActive_XHwpDocument()
        active = restore.application.XHwpDocuments.Active_XHwpDocument
        if (
            int(active.DocumentID) != restore.document_id
            or _normalized_full_name(str(active.FullName))
            != _normalized_full_name(restore.full_name)
        ):
            raise HwpLiveError("기존 활성 한컴 문서 탭을 복원하지 못했습니다")
    except _COM_ERROR as error:
        raise HwpLiveError("기존 활성 한컴 문서 탭을 복원하지 못했습니다") from error


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

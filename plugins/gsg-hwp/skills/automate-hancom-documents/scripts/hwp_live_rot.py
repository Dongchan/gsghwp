from __future__ import annotations

import hashlib
import json
import ntpath
import sys
import time
from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from importlib import import_module
from io import StringIO
from threading import local
from typing import Protocol, cast, final, runtime_checkable
from weakref import ReferenceType, ref

from hwp_errors import HwpLiveError
from hwp_live_addon import ADDON_MONIKER_PREFIX
from hwp_live_api import (
    HwpComApplication,
    HwpComDocument,
    LiveHwpApplication,
)
from hwp_live_contract import OpenDocument
from hwp_live_native_batch import (
    NativeActivationCallError,
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

    def CreateItemMoniker(self, delimiter: str, item: str) -> RotMoniker: ...

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
_TRANSIENT_SCAN_RETRY_DELAY_SECONDS = 0.02


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


@dataclass(frozen=True, slots=True, weakref_slot=True)
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
    page_count: int | None = None

    def public(self) -> OpenDocument:
        title = (
            ntpath.basename(self.full_name) if self.full_name else "저장되지 않은 문서"
        )
        pages = (
            self.page_count
            if self.page_count is not None
            else self.application.PageCount
            if self.active
            else 0
        )
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
class HwpDocumentIdentity:
    selector: str
    moniker_name: str
    document_id: int
    full_name: str
    document_format: str
    edit_mode: int
    modified: bool
    active: bool
    window_handle: int
    page_count: int

    @classmethod
    def from_candidate(
        cls,
        candidate: HwpDocumentCandidate,
        *,
        page_count: int,
        selector: str | None = None,
        modified: bool | None = None,
        active: bool | None = None,
    ) -> HwpDocumentIdentity:
        if page_count < 0:
            raise HwpLiveError("한컴 문서의 본문 쪽 수가 올바르지 않습니다")
        return cls(
            selector=candidate.selector if selector is None else selector,
            moniker_name=candidate.moniker_name,
            document_id=candidate.document_id,
            full_name=candidate.full_name,
            document_format=candidate.document_format,
            edit_mode=candidate.edit_mode,
            modified=(
                bool(candidate.document.Modified)
                if modified is None
                else modified
            ),
            active=candidate.active if active is None else active,
            window_handle=candidate.window_handle,
            page_count=page_count,
        )

    @classmethod
    def from_document(
        cls,
        candidate: HwpDocumentCandidate,
        document: OpenDocument,
        *,
        selector: str | None = None,
    ) -> HwpDocumentIdentity:
        if document.page_count < 0:
            raise HwpLiveError("한컴 문서의 본문 쪽 수가 올바르지 않습니다")
        return cls(
            selector=document.selector if selector is None else selector,
            moniker_name=candidate.moniker_name,
            document_id=document.document_id,
            full_name=document.full_name,
            document_format=document.format,
            edit_mode=document.edit_mode,
            modified=document.modified,
            active=document.active,
            window_handle=document.window_handle,
            page_count=document.page_count,
        )

    def public(self) -> OpenDocument:
        title = (
            ntpath.basename(self.full_name) if self.full_name else "저장되지 않은 문서"
        )
        return OpenDocument(
            selector=self.selector,
            title=title,
            full_name=self.full_name,
            document_id=self.document_id,
            format=self.document_format,
            edit_mode=self.edit_mode,
            modified=self.modified,
            page_count=self.page_count,
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


class _RotThreadState(local):
    cache_deadline: float
    cached_candidates: tuple[ReferenceType[HwpDocumentCandidate], ...] | None
    initialized: bool
    page_counts: dict[tuple[str, int, int], int]

    def __init__(self) -> None:
        self.cache_deadline = 0.0
        self.cached_candidates = None
        self.initialized = False
        self.page_counts = {}


@final
class HwpRotCatalog:
    __slots__ = (
        "_cache_ttl_seconds",
        "_runtime",
        "_thread_state",
    )

    _cache_ttl_seconds: float
    _runtime: _ComRuntime | None
    _thread_state: _RotThreadState

    def __init__(self, *, cache_ttl_seconds: float = 0.5) -> None:
        if cache_ttl_seconds < 0:
            raise ValueError("ROT 캐시 유지 시간은 0 이상이어야 합니다")
        self._thread_state = _RotThreadState()
        self._cache_deadline = 0.0
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_candidates = None
        self._initialized = False
        self._runtime = None

    @property
    def _cache_deadline(self) -> float:
        return self._thread_state.cache_deadline

    @_cache_deadline.setter
    def _cache_deadline(self, value: float) -> None:
        self._thread_state.cache_deadline = value

    @property
    def _cached_candidates(
        self,
    ) -> tuple[ReferenceType[HwpDocumentCandidate], ...] | None:
        return self._thread_state.cached_candidates

    @_cached_candidates.setter
    def _cached_candidates(
        self,
        value: tuple[ReferenceType[HwpDocumentCandidate], ...] | None,
    ) -> None:
        self._thread_state.cached_candidates = value

    @property
    def _initialized(self) -> bool:
        return self._thread_state.initialized

    @_initialized.setter
    def _initialized(self, value: bool) -> None:
        self._thread_state.initialized = value

    def _loaded_runtime(self) -> _ComRuntime:
        runtime = self._runtime
        if runtime is None:
            runtime = _ComRuntime(_load_pythoncom(), _load_win32_client())
            self._runtime = runtime
        return runtime

    @staticmethod
    def _page_count_key(
        *,
        full_name: str,
        document_id: int,
        window_handle: int,
    ) -> tuple[str, int, int]:
        return (
            _normalized_full_name(full_name),
            document_id,
            window_handle,
        )

    def remember_page_count(
        self,
        *,
        full_name: str,
        document_id: int,
        window_handle: int,
        page_count: int,
    ) -> None:
        if page_count < 1:
            raise HwpLiveError("한컴 문서의 본문 쪽 수를 정확히 읽지 못했습니다")
        key = self._page_count_key(
            full_name=full_name,
            document_id=document_id,
            window_handle=window_handle,
        )
        self._thread_state.page_counts[key] = page_count

    def _resolve_page_counts(
        self,
        candidates: tuple[HwpDocumentCandidate, ...],
    ) -> tuple[HwpDocumentCandidate, ...]:
        resolved: list[HwpDocumentCandidate] = []
        visible_keys: set[tuple[str, int, int]] = set()
        for candidate in candidates:
            key = self._page_count_key(
                full_name=candidate.full_name,
                document_id=candidate.document_id,
                window_handle=candidate.window_handle,
            )
            visible_keys.add(key)
            page_count = candidate.page_count
            if page_count is None:
                page_count = self._thread_state.page_counts.get(key)
            if page_count is None and candidate.active:
                page_count = int(candidate.application.PageCount)
            if page_count is not None:
                if page_count < 1:
                    raise HwpLiveError(
                        "한컴 문서의 본문 쪽 수를 정확히 읽지 못했습니다"
                    )
                self._thread_state.page_counts[key] = page_count
                candidate = replace(candidate, page_count=page_count)
            resolved.append(candidate)
        self._thread_state.page_counts = {
            key: page_count
            for key, page_count in self._thread_state.page_counts.items()
            if key in visible_keys
        }
        return tuple(resolved)

    def scan(self, *, force: bool = False) -> tuple[HwpDocumentCandidate, ...]:
        return self._scan(force=force, retry_transient=True)

    def _scan(
        self,
        *,
        force: bool,
        retry_transient: bool,
    ) -> tuple[HwpDocumentCandidate, ...]:
        now = time.monotonic()
        cached_references = self._cached_candidates
        if (
            not force
            and cached_references is not None
            and now < self._cache_deadline
        ):
            cached: list[HwpDocumentCandidate] = []
            for candidate_reference in cached_references:
                candidate = candidate_reference()
                if candidate is None:
                    cached.clear()
                    self._cached_candidates = None
                    break
                cached.append(candidate)
            else:
                return tuple(cached)
        runtime = self._loaded_runtime()
        if not self._initialized:
            runtime.pythoncom.CoInitialize()
            self._initialized = True
        context = runtime.pythoncom.CreateBindCtx(0)
        rot = runtime.pythoncom.GetRunningObjectTable()
        document_candidates: dict[tuple[str, int, int], HwpDocumentCandidate] = {}
        window_candidates: dict[tuple[str, int, int], HwpDocumentCandidate] = {}
        fallback_candidates: dict[tuple[str, int, int], HwpDocumentCandidate] = {}
        matching_errors = 0
        transient_unavailable = False
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
                    try:
                        documents = application.XHwpDocuments
                    except AttributeError as error:
                        diagnostic = json.dumps(
                            {
                                "event": "hwp.rot.resolve.error",
                                "moniker": name,
                                "stage": "application.XHwpDocuments",
                                "error_type": type(error).__name__,
                                "cause": str(error),
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        _ = sys.stderr.write(f"{diagnostic}\n")
                        matching_errors += 1
                        continue
                    active = cast(
                        HwpComDocument | None,
                        documents.Active_XHwpDocument,
                    )
                    if active is None:
                        matching_errors += 1
                        transient_unavailable = True
                        continue
                    active_id = int(active.DocumentID)
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
                        document = cast(
                            HwpComDocument | None,
                            documents.Item(index),
                        )
                        if document is None:
                            matching_errors += 1
                            transient_unavailable = True
                            continue
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
                        page_count = (
                            int(application.PageCount)
                            if document_id == active_id
                            else None
                        )
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
                            page_count=page_count,
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
                except AttributeError:
                    matching_errors += 1
                    transient_unavailable = True
                except (_COM_ERROR, TypeError, ValueError):
                    matching_errors += 1
        except _COM_ERROR as error:
            raise HwpLiveError("열려 있는 한컴 문서 목록을 읽을 수 없습니다") from error
        candidates = dict(fallback_candidates)
        candidates.update(window_candidates)
        for identity, candidate in document_candidates.items():
            process_candidate = candidates.get(identity)
            if process_candidate is not None:
                candidate = replace(
                    candidate,
                    active=process_candidate.active,
                )
            candidates[identity] = candidate
        if not candidates and matching_errors:
            if retry_transient and transient_unavailable:
                time.sleep(_TRANSIENT_SCAN_RETRY_DELAY_SECONDS)
                return self._scan(force=True, retry_transient=False)
            raise HwpLiveError(
                "; ".join(
                    (
                        "열려 있는 한컴 문서 목록을 읽을 수 없습니다. 한컴 문서 "
                        + "탭이 아직 준비되지 않았거나 닫히는 중이면 잠시 후 요청한 "
                        + "읽기 도구를 다시 실행하세요",
                        "mutation_started=false",
                        "retry_safe=true",
                        "user_action=잠시 후 요청한 읽기 도구를 다시 실행하세요",
                    )
                )
            )
        result = self._resolve_page_counts(tuple(candidates.values()))
        self._cached_candidates = tuple(ref(candidate) for candidate in result)
        self._cache_deadline = time.monotonic() + self._cache_ttl_seconds
        return result

    def resolve_identity(
        self,
        identity: HwpDocumentIdentity,
    ) -> HwpDocumentCandidate:
        if not identity.moniker_name.startswith("!"):
            raise HwpLiveError("캐시된 한컴 문서 모니커 형식이 올바르지 않습니다")
        runtime = self._loaded_runtime()
        if not self._initialized:
            runtime.pythoncom.CoInitialize()
            self._initialized = True
        try:
            moniker = runtime.pythoncom.CreateItemMoniker(
                "!",
                identity.moniker_name[1:],
            )
            source = (
                runtime.pythoncom.GetRunningObjectTable()
                .GetObject(moniker)
                .QueryInterface(runtime.pythoncom.IID_IDispatch)
            )
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                application = runtime.win32_client.Dispatch(source)
            documents = application.XHwpDocuments
            active = cast(
                HwpComDocument | None,
                documents.Active_XHwpDocument,
            )
            if active is None:
                raise HwpLiveError(
                    "; ".join(
                        (
                            "대상 한컴 문서 탭이 아직 준비되지 않았습니다",
                            "mutation_started=false",
                            "retry_safe=true",
                            "user_action=잠시 후 요청한 읽기 도구를 다시 실행하세요",
                        )
                    )
                )
            active_id = int(active.DocumentID)
            active_path = str(active.FullName)
            observed_handle = int(
                application.XHwpWindows.Active_XHwpWindow.WindowHandle
            )
            scoped_handle = _addon_window_handle(identity.moniker_name)
            if (
                scoped_handle is not None
                and scoped_handle != identity.window_handle
            ) or (
                scoped_handle is None
                and observed_handle != identity.window_handle
            ):
                raise HwpLiveError(
                    "캐시된 한컴 문서 창 핸들이 현재 모니커 대상과 다릅니다"
                )
            scoped_document_id = _addon_document_id(identity.moniker_name)
            if (
                scoped_document_id is not None
                and scoped_document_id != identity.document_id
            ):
                raise HwpLiveError(
                    "캐시된 한컴 문서 ID가 현재 모니커 대상과 다릅니다"
                )
            matches: list[HwpComDocument] = []
            for index in range(documents.Count):
                document = cast(
                    HwpComDocument | None,
                    documents.Item(index),
                )
                if document is None:
                    continue
                if int(document.DocumentID) != identity.document_id:
                    continue
                if _normalized_full_name(
                    str(document.FullName)
                ) != _normalized_full_name(identity.full_name):
                    continue
                matches.append(document)
            if len(matches) != 1:
                raise HwpLiveError(
                    "캐시된 신원과 일치하는 한컴 문서를 정확히 하나 찾지 못했습니다"
                )
            document = matches[0]
            full_name = str(document.FullName)
            active_target = (
                active_id == identity.document_id
                and _normalized_full_name(active_path)
                == _normalized_full_name(full_name)
            )
            page_count = (
                int(application.PageCount)
                if active_target
                else identity.page_count
            )
            candidate = HwpDocumentCandidate(
                selector=identity.selector,
                moniker_name=identity.moniker_name,
                application=application,
                document=document,
                document_id=identity.document_id,
                full_name=full_name,
                document_format=str(document.Format),
                edit_mode=int(document.EditMode),
                window_handle=identity.window_handle,
                active=active_target,
                page_count=page_count,
            )
            return self._resolve_page_counts((candidate,))[0]
        except HwpLiveError:
            raise
        except (
            _COM_ERROR,
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            raise HwpLiveError(
                "캐시된 한컴 문서 모니커에 직접 연결하지 못했습니다"
            ) from error

    def release_com_references(self) -> None:
        self._cached_candidates = None
        self._cache_deadline = 0.0

    def close(self) -> None:
        self.release_com_references()
        self._thread_state.page_counts.clear()
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
        if active_id == candidate.document_id and _normalized_full_name(
            active_path
        ) == _normalized_full_name(candidate.full_name):
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
                try:
                    if not activate_native_document(
                        candidate.window_handle,
                        candidate.document_id,
                    ):
                        candidate.document.SetActive_XHwpDocument()
                except NativeActivationCallError:
                    candidate.document.SetActive_XHwpDocument()
            else:
                candidate.document.SetActive_XHwpDocument()
            activated = application.XHwpDocuments.Active_XHwpDocument
            if int(
                activated.DocumentID
            ) != candidate.document_id or _normalized_full_name(
                str(activated.FullName)
            ) != _normalized_full_name(candidate.full_name):
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
            try:
                if not activate_native_document(
                    restore.window_handle,
                    restore.document_id,
                ):
                    restore.document.SetActive_XHwpDocument()
            except NativeActivationCallError:
                restore.document.SetActive_XHwpDocument()
        else:
            restore.document.SetActive_XHwpDocument()
        active = restore.application.XHwpDocuments.Active_XHwpDocument
        if int(active.DocumentID) != restore.document_id or _normalized_full_name(
            str(active.FullName)
        ) != _normalized_full_name(restore.full_name):
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

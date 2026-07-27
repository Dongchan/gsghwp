from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from threading import Lock, local
from time import monotonic, perf_counter_ns
from typing import Final, Never, Protocol, runtime_checkable
from weakref import ReferenceType, ref

from pywintypes import com_error

from hwp_errors import HwpLiveError
from hwp_live_native_action_contract import (
    decode_action_result,
    decode_detailed_inspection,
    decode_page_inspection,
    decode_page_inspection_batch,
    decode_snapshot,
    encode_action_request,
)
from hwp_live_native_action_models import (
    NativeActionRequest,
    NativeActionResult,
    NativeDetailedInspection,
    NativePageInspection,
    NativeSnapshot,
)
from hwp_live_native_batch_contract import (
    NativeBatchRequest,
    NativeBatchResult,
    NativeLifecycleResult,
    NativeSaveResult,
    decode_batch_result,
    decode_lifecycle_result,
    decode_save_result,
    encode_batch_request,
)


class _Moniker(Protocol):
    def GetDisplayName(self, context: object, moniker: _Moniker) -> str: ...


class _Rot(Protocol):
    def EnumRunning(self) -> tuple[_Moniker, ...]: ...

    def GetObject(self, moniker: _Moniker) -> _DispatchSource: ...


@runtime_checkable
class _PythonCom(Protocol):
    IID_IDispatch: object

    def CoInitialize(self) -> None: ...

    def CoUninitialize(self) -> None: ...

    def CreateBindCtx(self, reserved: int) -> object: ...

    def GetRunningObjectTable(self) -> _Rot: ...


class _DispatchSource(Protocol):
    def QueryInterface(self, interface_id: object) -> object: ...


_NATIVE_DISPATCH_CACHE_TTL_SECONDS: Final = 0.5
_NATIVE_DISPATCH_CACHE_MAX_ENTRIES: Final = 16
_document_routes: dict[int, int] = {}
_document_route_generations: dict[int, int] = {}
_document_route_lock = Lock()


@dataclass(frozen=True, slots=True)
class NativeDispatchCacheMetrics:
    cache_hits: int
    cache_misses: int
    rot_scans: int
    dispatch_creations: int
    invalidations: int
    lookup_nanoseconds: int


class NativeActivationCallError(HwpLiveError):
    pass


@dataclass(frozen=True, slots=True)
class _NativeDispatchCacheEntry:
    batch_ref: ReferenceType[_BatchDispatch]
    expires_at: float


class _NativeDispatchThreadState(local):
    entries: dict[tuple[int, int, int | None, int], _NativeDispatchCacheEntry]
    operation_depth: int
    operation_entries: dict[
        tuple[int, int, int | None, int],
        _BatchDispatch,
    ]

    def __init__(self) -> None:
        self.entries = {}
        self.operation_depth = 0
        self.operation_entries = {}


_native_dispatch_state = _NativeDispatchThreadState()
_native_dispatch_metrics_lock = Lock()
_native_dispatch_cache_hits = 0
_native_dispatch_cache_misses = 0
_native_dispatch_rot_scans = 0
_native_dispatch_creations = 0
_native_dispatch_invalidations = 0
_native_dispatch_lookup_nanoseconds = 0


def _record_native_dispatch_metrics(
    *,
    cache_hit: bool | None = None,
    rot_scan: bool = False,
    dispatch_creation: bool = False,
    invalidation: bool = False,
    lookup_nanoseconds: int = 0,
) -> None:
    global _native_dispatch_cache_hits
    global _native_dispatch_cache_misses
    global _native_dispatch_creations
    global _native_dispatch_invalidations
    global _native_dispatch_lookup_nanoseconds
    global _native_dispatch_rot_scans
    with _native_dispatch_metrics_lock:
        if cache_hit is True:
            _native_dispatch_cache_hits += 1
        elif cache_hit is False:
            _native_dispatch_cache_misses += 1
        if rot_scan:
            _native_dispatch_rot_scans += 1
        if dispatch_creation:
            _native_dispatch_creations += 1
        if invalidation:
            _native_dispatch_invalidations += 1
        _native_dispatch_lookup_nanoseconds += lookup_nanoseconds


def native_dispatch_cache_metrics() -> NativeDispatchCacheMetrics:
    with _native_dispatch_metrics_lock:
        return NativeDispatchCacheMetrics(
            cache_hits=_native_dispatch_cache_hits,
            cache_misses=_native_dispatch_cache_misses,
            rot_scans=_native_dispatch_rot_scans,
            dispatch_creations=_native_dispatch_creations,
            invalidations=_native_dispatch_invalidations,
            lookup_nanoseconds=_native_dispatch_lookup_nanoseconds,
        )


def reset_native_dispatch_cache_metrics() -> None:
    global _native_dispatch_cache_hits
    global _native_dispatch_cache_misses
    global _native_dispatch_creations
    global _native_dispatch_invalidations
    global _native_dispatch_lookup_nanoseconds
    global _native_dispatch_rot_scans
    with _native_dispatch_metrics_lock:
        _native_dispatch_cache_hits = 0
        _native_dispatch_cache_misses = 0
        _native_dispatch_rot_scans = 0
        _native_dispatch_creations = 0
        _native_dispatch_invalidations = 0
        _native_dispatch_lookup_nanoseconds = 0


def _route_state(window_handle: int) -> tuple[int | None, int]:
    with _document_route_lock:
        return (
            _document_routes.get(window_handle),
            _document_route_generations.get(window_handle, 0),
        )


def _invalidate_native_dispatch(window_handle: int) -> None:
    with _document_route_lock:
        _document_route_generations[window_handle] = (
            _document_route_generations.get(window_handle, 0) + 1
        )
    entries = _native_dispatch_state.entries
    _native_dispatch_state.entries = {
        key: entry for key, entry in entries.items() if key[1] != window_handle
    }
    operation_entries = _native_dispatch_state.operation_entries
    _native_dispatch_state.operation_entries = {
        key: batch
        for key, batch in operation_entries.items()
        if key[1] != window_handle
    }
    _record_native_dispatch_metrics(invalidation=True)


def _raise_native_call_error(
    window_handle: int,
    message: str,
    error: BaseException,
    failure_type: type[HwpLiveError] = HwpLiveError,
) -> Never:
    _invalidate_native_dispatch(window_handle)
    detail = ""
    if isinstance(error, com_error) and error.args and isinstance(error.args[0], int):
        detail = f" (HRESULT 0x{error.args[0] & 0xFFFFFFFF:08X})"
    elif str(error):
        detail = f" ({type(error).__name__}: {str(error)[:500]})"
    raise failure_type(message + detail) from error


def select_native_document_route(window_handle: int, document_id: int) -> None:
    if window_handle <= 0 or document_id <= 0:
        raise ValueError("한컴 네이티브 문서 라우팅 값은 양수여야 합니다")
    with _document_route_lock:
        previous = _document_routes.get(window_handle)
        _document_routes[window_handle] = document_id
        if previous != document_id:
            _document_route_generations[window_handle] = (
                _document_route_generations.get(window_handle, 0) + 1
            )


def clear_native_document_route(window_handle: int) -> None:
    with _document_route_lock:
        _ = _document_routes.pop(window_handle, None)
        _document_route_generations[window_handle] = (
            _document_route_generations.get(window_handle, 0) + 1
        )
    entries = _native_dispatch_state.entries
    _native_dispatch_state.entries = {
        key: entry for key, entry in entries.items() if key[1] != window_handle
    }
    operation_entries = _native_dispatch_state.operation_entries
    _native_dispatch_state.operation_entries = {
        key: batch
        for key, batch in operation_entries.items()
        if key[1] != window_handle
    }


class _BatchDispatch(Protocol):
    ProtocolVersion: int
    TargetDocumentID: int

    def Execute(self, payload: str) -> str: ...

    def ExecuteActions(self, payload: str) -> str: ...

    def Snapshot(self) -> str: ...

    def InspectPage(self, page: int) -> str: ...

    def InspectPageV3(self, page: int) -> str: ...

    def InspectPageSummary(self, page: int) -> str: ...

    def InspectPagesV3(self, pages: str) -> str: ...

    def InspectRoutingContext(self, page_hint: int) -> str: ...

    def InspectStructure(self, page: int) -> str: ...

    def ProbeOfficialApi(self, payload: str) -> str: ...

    def SaveReopenVerify(self) -> str: ...

    def SaveVerify(self) -> str: ...

    def ActivateDocument(self, document_id: int) -> str: ...

    def ActivationStatus(self, document_id: int) -> int: ...


def enter_native_dispatch_operation_scope() -> None:
    state = _native_dispatch_state
    if state.operation_depth == 0:
        state.operation_entries.clear()
    state.operation_depth += 1


def exit_native_dispatch_operation_scope() -> None:
    state = _native_dispatch_state
    if state.operation_depth < 1:
        raise RuntimeError("네이티브 dispatch 작업 scope 깊이가 일치하지 않습니다")
    state.operation_depth -= 1
    if state.operation_depth == 0:
        state.operation_entries.clear()


@contextmanager
def native_dispatch_operation_scope() -> Generator[None, None, None]:
    enter_native_dispatch_operation_scope()
    try:
        yield
    finally:
        exit_native_dispatch_operation_scope()


class _EventHandle(Protocol):
    def Close(self) -> None: ...


@runtime_checkable
class _Win32Event(Protocol):
    WAIT_OBJECT_0: int
    WAIT_TIMEOUT: int

    def OpenEvent(
        self,
        desired_access: int,
        inherit_handle: bool,
        name: str,
    ) -> _EventHandle: ...

    def WaitForMultipleObjects(
        self,
        handles: tuple[_EventHandle, ...],
        wait_all: bool,
        milliseconds: int,
    ) -> int: ...


@runtime_checkable
class _Win32Client(Protocol):
    def Dispatch(self, source: object) -> _BatchDispatch: ...


@runtime_checkable
class _Win32Process(Protocol):
    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]: ...


def _modules() -> tuple[_PythonCom, _Win32Client, _Win32Process]:
    pythoncom = import_module("pythoncom")
    client = import_module("win32com.client")
    process = import_module("win32process")
    if not isinstance(pythoncom, _PythonCom):
        raise HwpLiveError("pythoncom 모듈 계약이 올바르지 않습니다")
    if not isinstance(client, _Win32Client):
        raise HwpLiveError("win32com.client 모듈 계약이 올바르지 않습니다")
    if not isinstance(process, _Win32Process):
        raise HwpLiveError("win32process 모듈 계약이 올바르지 않습니다")
    return pythoncom, client, process


@contextmanager
def _com_apartment() -> Generator[
    tuple[_PythonCom, _Win32Client, _Win32Process],
    None,
    None,
]:
    modules = _modules()
    pythoncom, _, _ = modules
    pythoncom.CoInitialize()
    try:
        yield modules
    finally:
        pythoncom.CoUninitialize()


def _source_for_window(
    window_handle: int,
    pythoncom: _PythonCom,
    process: _Win32Process,
) -> _DispatchSource | None:
    try:
        _, process_id = process.GetWindowThreadProcessId(window_handle)
    except OSError:
        _invalidate_native_dispatch(window_handle)
        return None
    if process_id <= 0:
        _invalidate_native_dispatch(window_handle)
        return None
    document_id, _ = _route_state(window_handle)
    return _source_for_route(
        window_handle,
        process_id,
        document_id,
        pythoncom,
    )


def _source_for_route(
    window_handle: int,
    process_id: int,
    document_id: int | None,
    pythoncom: _PythonCom,
) -> _DispatchSource | None:
    document_name = (
        None
        if document_id is None
        else f"!HancomLiveBatch.{process_id}.{window_handle}.{document_id}"
    )
    window_name = f"!HancomLiveBatch.{process_id}.{window_handle}"
    legacy_name = f"!HancomLiveBatch.{process_id}"
    context = pythoncom.CreateBindCtx(0)
    rot = pythoncom.GetRunningObjectTable()
    window_moniker: _Moniker | None = None
    legacy_moniker: _Moniker | None = None
    for moniker in rot.EnumRunning():
        display_name = moniker.GetDisplayName(context, moniker)
        if document_name is not None and display_name == document_name:
            return rot.GetObject(moniker)
        if display_name == window_name:
            window_moniker = moniker
        if display_name == legacy_name:
            legacy_moniker = moniker
    if window_moniker is not None:
        return rot.GetObject(window_moniker)
    if legacy_moniker is not None:
        return rot.GetObject(legacy_moniker)
    return None


def native_batch_available(window_handle: int) -> bool:
    with _com_apartment() as (pythoncom, _, process):
        return _source_for_window(window_handle, pythoncom, process) is not None


def activate_native_document(window_handle: int, document_id: int) -> bool:
    select_native_document_route(window_handle, document_id)
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 12, *modules)
        if batch is None:
            return False
        try:
            token = str(batch.ActivateDocument(document_id))
            if not token:
                return False
            event_module = import_module("win32event")
            if not isinstance(event_module, _Win32Event):
                raise HwpLiveError("Windows 이벤트 대기 런타임을 찾을 수 없습니다")
            success = event_module.OpenEvent(
                0x00100000,
                False,
                token + ".Success",
            )
            failure = event_module.OpenEvent(
                0x00100000,
                False,
                token + ".Failure",
            )
            try:
                waited = event_module.WaitForMultipleObjects(
                    (success, failure),
                    False,
                    45_000,
                )
                if waited == event_module.WAIT_OBJECT_0:
                    return True
                if waited == event_module.WAIT_OBJECT_0 + 1:
                    activation = int(batch.ActivationStatus(document_id))
                    raise HwpLiveError(
                        "한컴 네이티브 문서 탭 전환이 실패했습니다"
                        + f" (HRESULT 0x{activation & 0xFFFFFFFF:08X})"
                    )
                if waited == event_module.WAIT_TIMEOUT:
                    raise HwpLiveError(
                        "한컴 네이티브 문서 탭 전환 제한시간을 초과했습니다"
                        + "; worker_isolation_required=true"
                        + "; reconcile_required=false; retry_safe=true"
                    )
                raise HwpLiveError(
                    f"한컴 네이티브 문서 탭 전환 대기 결과가 올바르지 않습니다 ({waited})"
                )
            finally:
                failure.Close()
                success.Close()
        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            com_error,
        ) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 문서 탭 전환에 실패했습니다",
                error,
                NativeActivationCallError,
            )
        finally:
            batch = None


def _batch_for_window(
    window_handle: int,
    minimum_version: int,
    pythoncom: _PythonCom,
    client: _Win32Client,
    process: _Win32Process,
) -> _BatchDispatch | None:
    lookup_started = perf_counter_ns()
    try:
        _, process_id = process.GetWindowThreadProcessId(window_handle)
    except OSError:
        _invalidate_native_dispatch(window_handle)
        return None
    if process_id <= 0:
        _invalidate_native_dispatch(window_handle)
        return None
    document_id, route_generation = _route_state(window_handle)
    key = (process_id, window_handle, document_id, route_generation)
    now = monotonic()
    entries = _native_dispatch_state.entries
    entries = {
        cached_key: entry
        for cached_key, entry in entries.items()
        if entry.expires_at > now
        and not (cached_key[1] == window_handle and cached_key[3] != route_generation)
    }
    _native_dispatch_state.entries = entries
    entry = entries.get(key)
    cached_batch = (
        _native_dispatch_state.operation_entries.get(key)
        if _native_dispatch_state.operation_depth > 0
        else None
    )
    if cached_batch is None:
        cached_batch = None if entry is None else entry.batch_ref()
    if entry is not None and cached_batch is None:
        _ = entries.pop(key, None)
        entry = None
    batch: _BatchDispatch
    if cached_batch is None:
        _record_native_dispatch_metrics(cache_hit=False, rot_scan=True)
        source = _source_for_route(
            window_handle,
            process_id,
            document_id,
            pythoncom,
        )
        if source is None:
            _record_native_dispatch_metrics(
                lookup_nanoseconds=perf_counter_ns() - lookup_started
            )
            return None
        try:
            batch = client.Dispatch(source.QueryInterface(pythoncom.IID_IDispatch))
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            _invalidate_native_dispatch(window_handle)
            raise HwpLiveError("한컴 네이티브 실시간 연결에 실패했습니다") from error
        _record_native_dispatch_metrics(dispatch_creation=True)
    else:
        batch = cached_batch
        _record_native_dispatch_metrics(cache_hit=True)
    try:
        if int(batch.ProtocolVersion) < minimum_version:
            _invalidate_native_dispatch(window_handle)
            raise HwpLiveError("한컴 네이티브 실시간 프로토콜 버전이 낮습니다")
        target_document_id = int(batch.TargetDocumentID)
        if document_id is not None and target_document_id not in (0, document_id):
            _invalidate_native_dispatch(window_handle)
            raise HwpLiveError("한컴 네이티브 라우팅 문서 ID가 대상과 다릅니다")
    except HwpLiveError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
        _invalidate_native_dispatch(window_handle)
        raise HwpLiveError("한컴 네이티브 실시간 연결에 실패했습니다") from error
    if cached_batch is None:
        if len(entries) >= _NATIVE_DISPATCH_CACHE_MAX_ENTRIES:
            oldest_key = min(
                entries,
                key=lambda cached_key: entries[cached_key].expires_at,
            )
            _ = entries.pop(oldest_key, None)
        try:
            batch_ref = ref(batch)
        except TypeError:
            batch_ref = None
        if batch_ref is not None:
            entries[key] = _NativeDispatchCacheEntry(
                batch_ref=batch_ref,
                expires_at=now + _NATIVE_DISPATCH_CACHE_TTL_SECONDS,
            )
    if _native_dispatch_state.operation_depth > 0:
        _native_dispatch_state.operation_entries[key] = batch
    _record_native_dispatch_metrics(
        lookup_nanoseconds=perf_counter_ns() - lookup_started
    )
    return batch


def execute_native_batch(
    window_handle: int,
    request: NativeBatchRequest,
) -> NativeBatchResult | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 1, *modules)
        if batch is None:
            return None

        try:
            response = batch.Execute(encode_batch_request(request))
        except HwpLiveError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 배치 실행에 실패했습니다",
                error,
            )
        finally:
            batch = None
        return decode_batch_result(str(response))


def execute_native_lifecycle(window_handle: int) -> NativeLifecycleResult | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 8, *modules)
        if batch is None:
            return None
        try:
            response = batch.SaveReopenVerify()
        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            com_error,
        ) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 저장·재개방 검증 실행에 실패했습니다",
                error,
            )
        finally:
            batch = None
        return decode_lifecycle_result(str(response))


def execute_native_save(window_handle: int) -> NativeSaveResult | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 12, *modules)
        if batch is None:
            return None
        try:
            response = batch.SaveVerify()
        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            com_error,
        ) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 일반 저장 검증 실행에 실패했습니다",
                error,
            )
        finally:
            batch = None
        return decode_save_result(str(response))


def read_native_snapshot(window_handle: int) -> NativeSnapshot | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 2, *modules)
        if batch is None:
            return None
        try:
            response = batch.Snapshot()
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 현재 상태 조회에 실패했습니다",
                error,
            )
        finally:
            batch = None
        return decode_snapshot(str(response))


def inspect_native_page(
    window_handle: int,
    page: int,
    *,
    include_cells: bool = True,
) -> NativePageInspection | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 3 if include_cells else 4, *modules)
        if batch is None:
            return None
        try:
            response = (
                batch.InspectPageV3(page)
                if include_cells
                else batch.InspectPageSummary(page)
            )
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 쪽 구조 조회에 실패했습니다",
                error,
            )
        finally:
            batch = None
        return decode_page_inspection(str(response))


def inspect_native_pages(
    window_handle: int,
    pages: tuple[int, ...],
    *,
    include_cells: bool = True,
) -> tuple[NativePageInspection, ...]:
    if not pages:
        return ()
    if len(set(pages)) != len(pages) or any(page < 1 for page in pages):
        raise HwpLiveError("한컴 네이티브 다중 쪽 번호가 올바르지 않습니다")
    if not include_cells:
        inspected = tuple(
            inspect_native_page(window_handle, page, include_cells=False)
            for page in pages
        )
        if any(page is None for page in inspected):
            raise HwpLiveError("한컴 네이티브 다중 쪽 구조를 읽지 못했습니다")
        return tuple(page for page in inspected if page is not None)
    response: str | None = None
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 8, *modules)
        if batch is None:
            return ()
        try:
            response = str(batch.InspectPagesV3(",".join(str(page) for page in pages)))
        except AttributeError:
            response = None
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 다중 쪽 구조 조회에 실패했습니다",
                error,
            )
        finally:
            batch = None
    if response is None:
        inspected = tuple(
            inspect_native_page(window_handle, page, include_cells=True)
            for page in pages
        )
        if any(page is None for page in inspected):
            raise HwpLiveError("한컴 네이티브 다중 쪽 구조를 읽지 못했습니다")
        return tuple(page for page in inspected if page is not None)
    decoded = decode_page_inspection_batch(response)
    if tuple(page.page for page in decoded) != pages:
        raise HwpLiveError("한컴 네이티브 다중 쪽 구조 순서가 요청과 다릅니다")
    return decoded


def read_native_routing_context(
    window_handle: int,
    page_hint: int | None,
) -> NativePageInspection | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 8, *modules)
        if batch is None:
            return None
        try:
            response = batch.InspectRoutingContext(
                0 if page_hint is None else page_hint
            )
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 빠른 구조 조회에 실패했습니다",
                error,
            )
        finally:
            batch = None
        return decode_page_inspection(str(response))


def inspect_native_structure(
    window_handle: int,
    page: int,
) -> NativeDetailedInspection | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 7, *modules)
        if batch is None:
            return None
        try:
            response = batch.InspectStructure(page)
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 상세 구조 조회에 실패했습니다",
                error,
            )
        finally:
            batch = None
        return decode_detailed_inspection(str(response))


def execute_native_actions(
    window_handle: int,
    request: NativeActionRequest,
    *,
    minimum_version: int = 2,
) -> NativeActionResult | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, minimum_version, *modules)
        if batch is None:
            return None
        try:
            response = batch.ExecuteActions(encode_action_request(request))
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            _raise_native_call_error(
                window_handle,
                "한컴 네이티브 액션 배치 실행에 실패했습니다",
                error,
            )
        finally:
            batch = None
        return decode_action_result(str(response))


def probe_official_api(window_handle: int, payload: str) -> str | None:
    with _com_apartment() as modules:
        batch = _batch_for_window(window_handle, 6, *modules)
        if batch is None:
            return None
        try:
            response = str(batch.ProbeOfficialApi(payload))
        except (OSError, RuntimeError, TypeError, ValueError, com_error) as error:
            detail = f"{type(error).__name__}: {error}"
            _raise_native_call_error(
                window_handle,
                f"한컴 공식 API 네이티브 검증 호출에 실패했습니다: {detail}",
                error,
            )
        finally:
            batch = None
        return response

from __future__ import annotations

import hashlib
import json
import ntpath
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from importlib import import_module
from io import StringIO
from threading import Lock, local
from typing import Final, Protocol, cast, final, runtime_checkable
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
    native_foreground_guard,
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

# 한/글은 문서를 연 직후 쪽 나누기를 백그라운드로 끝낸다. 그동안 PageCount 는
# 0 을 돌려주고, 문서가 클수록 그 구간이 길다. "아직 값이 없다"는 거부의 근거가
# 아니라 잠깐 기다렸다 다시 읽을 근거다.
#
# 상한이 무엇을 묶는지 정확히. 예산은 함수에 들어온 순간부터 실제 시계
# (monotonic)로 센다 -- sleep 의 합이 아니다. 첫 읽기가 느려서 예산을 다 써
# 버렸으면 그 사실이 곧바로 보인다.
#   묶는다   -- 재계산·재읽기·폴링을 "시작할지" 판단하는 모든 시점. 예산이 다
#               되었으면 다음 것을 아예 시작하지 않고 끝낸다.
#   못 묶는다 -- 예산 검사 하나를 지나면 끊을 수 없는 호출이 최대 둘 이어진다.
#               RecalcPageCount 와 그 직후 읽기다(재계산 값을 한 번은 읽는다 --
#               아래 주석 참고). RecalcPageCount 는 IHwpObject 의 동기 메서드고
#               COM 읽기도 동기라, 시작한 뒤에는 중간에 끊을 방법이 없다.
#
# 그래서 최악의 실제 경과는 (상한) + (재계산 한 번) + (읽기 한 번)이다. 재계산이
# 오래 걸리는 만큼은 이 함수가 줄일 수 있는 값이 아니라 한/글이 쪽 나누기에 쓰는
# 시간이고, 그 시간은 기다리든 안 기다리든 다음 조회에서 어차피 치른다. 줄일 수
# 있는 것은 "언제 시작하느냐" 하나뿐이고 그게 _RECALC_MINIMUM_BUDGET_SHARE 다.
#
# 상한 1.0초의 근거: 정상 문서는 첫 읽기에서 바로 1 이상이 나와 이 경로에
# 들어오지 않고, 들어오더라도 RecalcPageCount()가 쪽 나누기를 그 자리에서
# 끝내므로 보통 첫 재읽기에서 끝난다. 즉 폴링은 재계산이 안 먹혔을 때의
# 뒷받침일 뿐이다. 그리고 이 함수를 부르는 세 자리는 전부 대상 탭을 활성화해 둔
# 구간 안이다 -- 기다리는 동안 사용자가 보던 탭이 바뀐 채로 머문다(호출부 주석
# 참고). 이미 엔진에 계산을 시켜 놓은 값을 더 오래 붙잡고 기다려서 얻는 것이,
# 사용자의 화면을 3초 붙잡는 대가보다 크지 않다. 못 읽어도 거부가 아니라
# "모른다"(page_count=None)이므로 짧은 상한이 도구를 막지 않는다.
PAGE_COUNT_SETTLE_TIMEOUT_SECONDS: Final = 1.0
PAGE_COUNT_SETTLE_POLL_SECONDS: Final = 0.05
# RecalcPageCount() 를 시작하려면 예산이 이만큼(비율)은 남아 있어야 한다.
# 재계산은 통틀어 한 번뿐이고 끊을 수 없으므로, 통제할 수 있는 것은 시작 시점뿐
# 이다. 이 검사가 실제로 걸리는 경우는 첫 PageCount 읽기 자체가 예산의 절반을
# 넘게 쓴 때다 -- 한/글 UI 스레드가 막혀 프로세스 간 읽기 하나가 초 단위로
# 걸리는 상황이고, 그때 재계산은 그보다 더 걸린다. 상한을 크게 넘길 것이 뻔한데
# 얻는 것은 없으므로 시작하지 않는다.
_RECALC_MINIMUM_BUDGET_SHARE: Final = 0.5


@dataclass(frozen=True, slots=True)
class SettledPageCount:
    """One page-count reading and whether it had to be waited for.

    ``page_count`` is ``None`` only when the count is genuinely still unknown --
    never 0, because no document has zero body pages. ``waited`` tells a caller
    that holds an activation guard that real time passed between its last check
    and this value, so it can re-confirm the target if it cares.
    """

    page_count: int | None
    waited: bool


@runtime_checkable
class PageCountRecalculator(Protocol):
    def RecalcPageCount(self) -> bool: ...


def settle_page_count(
    read: Callable[[], int],
    *,
    source: object | None = None,
    timeout_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> SettledPageCount:
    """Read the body page count, waiting only while it is still unknown.

    The first read is unconditional and is the whole cost on a document that is
    already paginated -- nothing below runs on the healthy path. Only a reading
    below one enters the wait, and the wait first asks Hancom to finish
    pagination through ``source.RecalcPageCount``: the same call
    ``hwp_render_page`` already makes on a read path (``hwp_live_preview.py``),
    so it is not a new kind of side effect. The lookup is deliberately made here
    and not by the caller, so an object that does not offer it costs nothing on
    the healthy path and simply skips straight to re-reading. A recalculation
    that fails is a hint that did not land, not a failure of the read.

    The budget is measured against ``monotonic`` from the moment this is
    entered, not against the sum of the ``sleep`` calls, and it covers the first
    read too. Every step that can block -- the recalculation, each re-read, each
    poll -- is only *started* while the budget still has time in it. One budget
    check can be followed by two uninterruptible calls, though: the
    recalculation and the one read that always takes its result, so the worst
    real elapsed time is the budget plus one recalculation plus one read --
    exactly the bound ``test_settled_page_count_gives_up_within_the_bound``
    asserts. Nothing here can interrupt a call in flight: ``RecalcPageCount`` is
    a synchronous ``IHwpObject`` method and a COM property read is synchronous
    too. What can be controlled is when the uninterruptible one is allowed to
    start, and that is ``_RECALC_MINIMUM_BUDGET_SHARE``.

    This function never raises on an unreadable count; it returns
    ``page_count=None``. Errors from ``read`` itself still propagate, because a
    COM read that throws is a broken connection, not a slow layout.
    """
    budget = (
        PAGE_COUNT_SETTLE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    )
    deadline = monotonic() + budget
    page_count = read()
    if page_count >= 1:
        return SettledPageCount(page_count=page_count, waited=False)
    recalculated = False
    while True:
        # 상한은 상한이다. 남은 예산이 없으면 다음 호출을 시작하지 않는다.
        remaining = deadline - monotonic()
        if remaining <= 0:
            return SettledPageCount(page_count=None, waited=True)
        if not recalculated:
            recalculated = True
            if (
                isinstance(source, PageCountRecalculator)
                and remaining >= budget * _RECALC_MINIMUM_BUDGET_SHARE
            ):
                try:
                    _ = source.RecalcPageCount()
                except (OSError, RuntimeError, TypeError, ValueError, _COM_ERROR):
                    pass
        # 방금 재계산을 마쳤다면 그 결과는 예산이 넘었더라도 한 번 읽는다: 비용은
        # 이미 치렀고 읽기 하나를 아껴서 되찾을 시간이 없다. 그 밖의 모든 읽기는
        # 바로 위 예산 검사를 통과한 것뿐이다.
        page_count = read()
        if page_count >= 1:
            return SettledPageCount(page_count=page_count, waited=True)
        # 마지막 폴링이 상한을 넘기지 않도록 남은 예산까지만 잔다.
        remaining = deadline - monotonic()
        if remaining <= 0:
            return SettledPageCount(page_count=None, waited=True)
        sleep(min(PAGE_COUNT_SETTLE_POLL_SECONDS, remaining))


def confirmed_page_count(page_count: int | None) -> int | None:
    """Fold every "not a real page count" reading onto ``None``.

    0 쪽인 문서는 없다. 0 이나 음수는 "쪽이 없다"가 아니라 "아직 모른다"이므로
    사실처럼 응답에 싣지 않는다.
    """
    return page_count if page_count is not None and page_count >= 1 else None


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
        # 비활성 탭은 쪽 수를 읽을 방법이 없다. 예전에는 그때 0 을 실어 보냈는데
        # 0 쪽인 문서는 없으므로 그건 거짓이었다. 모르면 null 로 답한다.
        pages = (
            self.page_count
            if self.page_count is not None
            else int(self.application.PageCount)
            if self.active
            else None
        )
        return OpenDocument(
            selector=self.selector,
            title=title,
            full_name=self.full_name,
            document_id=self.document_id,
            format=self.document_format,
            edit_mode=self.edit_mode,
            modified=bool(self.document.Modified),
            page_count=confirmed_page_count(pages),
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
    page_count: int | None

    @classmethod
    def from_candidate(
        cls,
        candidate: HwpDocumentCandidate,
        *,
        page_count: int | None,
        selector: str | None = None,
        modified: bool | None = None,
        active: bool | None = None,
    ) -> HwpDocumentIdentity:
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
    """Per-thread state that genuinely cannot leave its COM apartment.

    ``cached_candidates`` holds references to live cross-process COM objects and
    ``initialized`` tracks this thread's ``CoInitialize``, so both must stay
    thread-local. Remembered page counts are plain integers and deliberately do
    not live here -- see ``HwpRotCatalog._page_counts``.
    """

    cache_deadline: float
    cached_candidates: tuple[ReferenceType[HwpDocumentCandidate], ...] | None
    initialized: bool

    def __init__(self) -> None:
        self.cache_deadline = 0.0
        self.cached_candidates = None
        self.initialized = False


@final
class HwpRotCatalog:
    __slots__ = (
        "_cache_ttl_seconds",
        "_page_counts",
        "_page_counts_lock",
        "_runtime",
        "_thread_state",
    )

    _cache_ttl_seconds: float
    # 확인된 쪽 수의 기억. 한 도구 호출과 다음 호출은 서로 다른 작업 스레드에서
    # 돌기 때문에(MCP 작업 실행기 8개) 이 기억이 스레드 지역이면 hwp_open_document
    # 가 읽어 둔 값이 바로 뒤의 hwp_list_open_documents 에서 사라진다. 실제로
    # "열 때 28, 직후 목록에서 0" 이 그 증상이었다. COM 포인터가 아니라 정수뿐이라
    # 아파트먼트에 묶일 이유가 없으므로 프로세스 단위로 두고 잠금으로 지킨다.
    _page_counts: dict[tuple[str, int, int], int]
    _page_counts_lock: Lock
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
        self._page_counts = {}
        self._page_counts_lock = Lock()
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
        page_count: int | None,
    ) -> None:
        # 확정되지 않은 쪽 수는 기억하지 않는다. 기억할 값이 없다는 것이 호출을
        # 거부할 이유는 아니므로 조용히 넘어간다.
        confirmed = confirmed_page_count(page_count)
        if confirmed is None:
            return
        key = self._page_count_key(
            full_name=full_name,
            document_id=document_id,
            window_handle=window_handle,
        )
        with self._page_counts_lock:
            self._page_counts[key] = confirmed

    def _resolve_page_counts(
        self,
        candidates: tuple[HwpDocumentCandidate, ...],
        *,
        prune: bool = False,
    ) -> tuple[HwpDocumentCandidate, ...]:
        """Fill in what each candidate's page count is, or that it is unknown.

        ``prune`` may only be set by a caller that just enumerated *every* open
        document, because pruning drops the memory of anything not in
        ``candidates``. ``resolve_identity`` resolves one document at a time and
        must never prune -- doing so would erase every other document's
        confirmed page count and put ``hwp_list_open_documents`` right back to
        answering "unknown" for tabs it had already measured.
        """
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
            if page_count is None and not candidate.active:
                # 비활성 탭은 활성화하지 않고는 쪽 수를 읽을 수 없다. 이 문서에서
                # 앞서 직접 확인해 둔 값이 있으면 그것을 쓴다.
                with self._page_counts_lock:
                    page_count = self._page_counts.get(key)
            if page_count is None and candidate.active:
                page_count = int(candidate.application.PageCount)
            # 활성 탭이 0 을 돌려주면 쪽 나누기가 아직 끝나지 않은 것이다. 그때
            # 예전에 기억한 값을 지금 값인 척 싣지 않는다. 목록 조회는 기다리지
            # 않고 모른다고 답하며, 쪽 수가 필요한 호출자가 그 자리에서 기다린다.
            page_count = confirmed_page_count(page_count)
            if page_count is not None:
                with self._page_counts_lock:
                    self._page_counts[key] = page_count
            if page_count != candidate.page_count:
                candidate = replace(candidate, page_count=page_count)
            resolved.append(candidate)
        if prune:
            with self._page_counts_lock:
                self._page_counts = {
                    key: page_count
                    for key, page_count in self._page_counts.items()
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
        result = self._resolve_page_counts(tuple(candidates.values()), prune=True)
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
        with self._page_counts_lock:
            self._page_counts.clear()
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
            # SetActive_XHwpDocument은 한컴 UI 스레드로 마샬링되어 창을
            # 올린다. 네이티브 활성화(activate_native_document)는 브리지
            # Invoke를 거치므로 이미 가드 안이지만, 그게 실패했을 때의 폴백과
            # 비네이티브 경로는 Invoke를 거치지 않는다. 그래서 구간 전체를
            # 브리지 가드로 감싼다. 중첩은 브리지가 깊이로 처리한다.
            with native_foreground_guard(candidate.window_handle):
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
        # activate_candidate과 같은 이유로 가드 안에서 되돌린다. 복원도 탭을
        # 바꾸는 활성화라 창을 올릴 수 있다.
        with native_foreground_guard(restore.window_handle):
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

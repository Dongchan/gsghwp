from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_contract import (
    ConnectedDocument,
    MutationResult,
    OpenDocument,
    OpenDocumentList,
)
from hwp_live_control import require_hwp_2024
from hwp_live_edit_history import LiveEditHistoryStore
from hwp_live_native_batch import (
    clear_native_document_route,
    read_native_routing_context,
    select_native_document_route,
)
from hwp_live_preview_store import PreviewSessionLease, PreviewStore
from hwp_live_rot import (
    ActiveDocumentRestore,
    HwpDocumentCandidate,
    HwpRotCatalog,
    activate_candidate,
    attach_wrapper,
    candidate_supports_native_activation,
    release_wrapper,
    require_active_candidate,
    restore_active_document,
)
from hwp_live_security import is_file_path_checker_installed
from hwp_live_session_candidate import (
    select_live_candidate,
    select_operation_document,
    select_scanned_candidate,
)
from hwp_live_session_structure import connected_document
from hwp_live_session_types import (
    DocumentCatalog,
    LiveSessionReference,
    RoutingContextReader,
    WrapperAttacher,
    WrapperReleaser,
)
from hwp_live_structure_contract import DocumentStructure
from hwp_official_api_live import (
    OfficialApiLiveBatchResult,
    OfficialApiLiveCategory,
    run_official_api_live_batch,
)
from hwp_operation_contract import OperationRoutingContext


@dataclass(slots=True)
class _LiveDocumentSession:
    session_id: str
    selector: str
    moniker_name: str
    candidate: HwpDocumentCandidate
    wrapper: LiveHwpApplication | None
    preview_session: PreviewSessionLease


class LiveHwpSessionCore:
    __slots__: tuple[str, ...] = (
        "_attacher",
        "_activation_restore",
        "_catalog",
        "_last_routing_context",
        "_live_edit_history",
        "_listed_candidates",
        "_moniker_name",
        "_preview_session",
        "_preview_store",
        "_releaser",
        "_routing_context_reader",
        "_selector",
        "_session_id",
        "_selector_sessions",
        "_sessions",
        "_structure_snapshot",
        "_unsafe_selectors",
        "_wrapper",
        "_connected_candidate",
    )
    _attacher: WrapperAttacher
    _activation_restore: ActiveDocumentRestore | None
    _catalog: DocumentCatalog
    _last_routing_context: OperationRoutingContext | None
    _live_edit_history: LiveEditHistoryStore
    _listed_candidates: tuple[HwpDocumentCandidate, ...]
    _moniker_name: str | None
    _preview_session: PreviewSessionLease | None
    _preview_store: PreviewStore
    _releaser: WrapperReleaser
    _routing_context_reader: RoutingContextReader
    _selector: str | None
    _session_id: str | None
    _selector_sessions: dict[str, str]
    _sessions: dict[str, _LiveDocumentSession]
    _structure_snapshot: DocumentStructure | None
    _unsafe_selectors: set[str]
    _wrapper: LiveHwpApplication | None
    _connected_candidate: HwpDocumentCandidate | None

    def __init__(
        self,
        *,
        catalog: DocumentCatalog | None = None,
        attacher: WrapperAttacher = attach_wrapper,
        releaser: WrapperReleaser = release_wrapper,
        routing_context_reader: RoutingContextReader = read_native_routing_context,
    ) -> None:
        self._catalog = HwpRotCatalog() if catalog is None else catalog
        self._attacher = attacher
        self._activation_restore = None
        self._releaser = releaser
        self._routing_context_reader = routing_context_reader
        self._connected_candidate = None
        self._last_routing_context = None
        self._live_edit_history = LiveEditHistoryStore()
        self._listed_candidates = ()
        self._moniker_name = None
        self._preview_session = None
        self._preview_store = PreviewStore()
        self._selector = None
        self._session_id = None
        self._selector_sessions = {}
        self._sessions = {}
        self._structure_snapshot = None
        self._unsafe_selectors = set()
        self._wrapper = None

    def list_open_documents(self) -> OpenDocumentList:
        candidates = (
            self._catalog.scan(force=True)
            if isinstance(self._catalog, HwpRotCatalog)
            else self._catalog.scan()
        )
        self._listed_candidates = candidates
        return OpenDocumentList(
            documents=tuple(candidate.public() for candidate in candidates)
        )

    def open_document(
        self,
        path: str,
        reference_selector: str | None,
        new_tab: bool,
    ) -> OpenDocument:
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise HwpLiveError("열 문서 경로가 파일이 아닙니다")
        requested_path = str(resolved)
        before = self.list_open_documents()
        existing = tuple(
            document
            for document in before.documents
            if Path(document.full_name).resolve() == resolved
        )
        if len(existing) == 1:
            return existing[0]
        if len(existing) > 1:
            raise HwpLiveError(
                "같은 전체 경로의 열린 문서가 여러 개입니다. 기존 문서를 "
                + "먼저 정리한 뒤 다시 시도하세요"
            )
        reference = select_operation_document(before, reference_selector)
        candidate = select_live_candidate(self._catalog, reference.selector)
        active = candidate.application.XHwpDocuments.Active_XHwpDocument
        restore = ActiveDocumentRestore(
            application=candidate.application,
            document=active,
            document_id=int(active.DocumentID),
            full_name=str(active.FullName),
            window_handle=candidate.window_handle,
            native=candidate_supports_native_activation(candidate),
        )
        candidate, _ = activate_candidate(candidate)
        opened_document_id: int
        try:
            registered = candidate.application.RegisterModule(
                ModuleType="FilePathCheckDLL",
                ModuleData="FilePathCheckerModule",
            )
            if not registered and not is_file_path_checker_installed():
                raise HwpLiveError("한컴 파일 경로 보안 모듈 등록이 거부되었습니다")
            opened = candidate.application.XHwpDocuments.Add(new_tab)
            if not opened.Open(requested_path, None, None):
                raise HwpLiveError("한컴에서 문서를 열지 못했습니다")
            opened_document_id = int(opened.DocumentID)
            opened_full_name = Path(str(opened.FullName)).resolve()
            active_opened = candidate.application.XHwpDocuments.Active_XHwpDocument
            if (
                opened_full_name != resolved
                or int(active_opened.DocumentID) != opened_document_id
                or Path(str(active_opened.FullName)).resolve() != resolved
            ):
                raise HwpLiveError(
                    "한컴이 연 문서의 전체 경로 또는 document_id가 요청과 다릅니다"
                )
            if int(candidate.application.PageCount) < 1:
                raise HwpLiveError("한컴이 연 문서의 본문 쪽을 읽지 못했습니다")
        finally:
            restore_active_document(restore)
        after = self.list_open_documents()
        matches = tuple(
            document
            for document in after.documents
            if Path(document.full_name).resolve() == resolved
            and document.document_id == opened_document_id
        )
        if len(matches) != 1:
            raise HwpLiveError(
                "문서를 열었지만 정확한 전체 경로와 document_id를 다시 찾지 못했습니다"
            )
        return matches[0]

    def connect(self, selector: str) -> ConnectedDocument:
        existing_id = self._selector_sessions.get(selector)
        if existing_id is not None:
            entry = self._sessions[existing_id]
            self._set_current_entry(entry)
            if entry.wrapper is None:
                _ = self._validate(entry.session_id)
            return ConnectedDocument(
                session_id=entry.session_id,
                document=entry.candidate.public(),
            )
        listed_candidates, self._listed_candidates = self._listed_candidates, ()
        candidate = (
            select_scanned_candidate(listed_candidates, selector)
            if listed_candidates
            else select_live_candidate(self._catalog, selector)
        )
        candidate, restore = activate_candidate(candidate)
        hwp = self._attacher(candidate)
        guard = self._guard(candidate, hwp)
        connected: ConnectedDocument | None = None
        preview_session: PreviewSessionLease | None = None
        ready = False
        try:
            require_hwp_2024(hwp, guard)
            registered = hwp.hwp.RegisterModule(
                ModuleType="FilePathCheckDLL",
                ModuleData="FilePathCheckerModule",
            )
            if not registered and not is_file_path_checker_installed():
                raise HwpLiveError("한컴 파일 경로 보안 모듈 등록이 거부되었습니다")
            session_id = secrets.token_urlsafe(24)
            preview_session = self._preview_store.open_session(session_id)
            connected = ConnectedDocument(
                session_id=session_id,
                document=connected_document(candidate, hwp, guard),
            )
            restore_active_document(restore)
            ready = True
        finally:
            if not ready:
                try:
                    if preview_session is not None:
                        preview_session.close()
                finally:
                    try:
                        self._releaser(hwp)
                    finally:
                        restore_active_document(restore)
        if preview_session is None:
            raise HwpLiveError("한컴 미리보기 세션 연결 결과가 없습니다")
        entry = _LiveDocumentSession(
            session_id=connected.session_id,
            selector=selector,
            moniker_name=candidate.moniker_name,
            candidate=candidate,
            wrapper=hwp,
            preview_session=preview_session,
        )
        self._sessions[entry.session_id] = entry
        self._selector_sessions[entry.selector] = entry.session_id
        self._set_current_entry(entry)
        return connected

    def connect_deferred(self, selector: str) -> ConnectedDocument:
        existing_id = self._selector_sessions.get(selector)
        if existing_id is not None:
            entry = self._sessions[existing_id]
            self._set_current_entry(entry)
            return ConnectedDocument(
                session_id=entry.session_id,
                document=entry.candidate.public(),
            )
        listed_candidates, self._listed_candidates = self._listed_candidates, ()
        candidate = (
            select_scanned_candidate(listed_candidates, selector)
            if listed_candidates
            else select_live_candidate(self._catalog, selector)
        )
        session_id = secrets.token_urlsafe(24)
        preview_session = self._preview_store.open_session(session_id)
        entry = _LiveDocumentSession(
            session_id=session_id,
            selector=selector,
            moniker_name=candidate.moniker_name,
            candidate=candidate,
            wrapper=None,
            preview_session=preview_session,
        )
        self._sessions[entry.session_id] = entry
        self._selector_sessions[entry.selector] = entry.session_id
        self._set_current_entry(entry)
        return ConnectedDocument(
            session_id=session_id,
            document=candidate.public(),
        )

    def _set_current_entry(self, entry: _LiveDocumentSession) -> None:
        if self._session_id != entry.session_id:
            self._structure_snapshot = None
            self._last_routing_context = None
        select_native_document_route(
            entry.candidate.window_handle,
            entry.candidate.document_id,
        )
        self._wrapper = entry.wrapper
        self._connected_candidate = entry.candidate
        self._moniker_name = entry.moniker_name
        self._preview_session = entry.preview_session
        self._selector = entry.selector
        self._session_id = entry.session_id

    @property
    def preview_store(self) -> PreviewStore:
        return self._preview_store

    def current_session(self) -> LiveSessionReference | None:
        if self._session_id is None:
            return None
        if self._selector is None:
            raise HwpLiveError("연결된 한컴 문서 선택자가 없습니다")
        return LiveSessionReference(
            session_id=self._session_id,
            selector=self._selector,
        )

    def session_for_selector(self, selector: str) -> LiveSessionReference | None:
        session_id = self._selector_sessions.get(selector)
        if session_id is None:
            return None
        return LiveSessionReference(session_id=session_id, selector=selector)

    def session_count(self) -> int:
        return len(self._sessions)

    def connection_moniker(self, session_id: str) -> str:
        entry = self._sessions.get(session_id)
        if entry is None:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        return entry.moniker_name

    def run_official_api_batch(
        self,
        session_id: str,
        category: OfficialApiLiveCategory,
        start: int,
        limit: int,
    ) -> OfficialApiLiveBatchResult:
        candidate, _ = self._validate(session_id)
        return run_official_api_live_batch(
            candidate.window_handle,
            category,
            start,
            limit,
        )

    def _validate(
        self,
        session_id: str,
    ) -> tuple[HwpDocumentCandidate, LiveHwpApplication]:
        entry = self._sessions.get(session_id)
        if entry is None:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        if self._activation_restore is not None and self._session_id != session_id:
            raise HwpLiveError("한 작업 안에서 서로 다른 한컴 문서 세션을 전환할 수 없습니다")
        candidate = select_live_candidate(self._catalog, entry.selector)
        select_native_document_route(
            candidate.window_handle,
            candidate.document_id,
        )
        candidate, restore = activate_candidate(candidate)
        self._activation_restore = restore
        hwp: LiveHwpApplication | None = None
        created_wrapper = entry.wrapper is None
        try:
            hwp = (
                self._attacher(candidate)
                if entry.wrapper is None
                else self._attacher(candidate, entry.wrapper)
            )
            guard = self._guard(candidate, hwp)
            if created_wrapper:
                require_hwp_2024(hwp, guard)
                registered = hwp.hwp.RegisterModule(
                    ModuleType="FilePathCheckDLL",
                    ModuleData="FilePathCheckerModule",
                )
                if not registered and not is_file_path_checker_installed():
                    raise HwpLiveError(
                        "한컴 파일 경로 보안 모듈 등록이 거부되었습니다"
                    )
            guard()
        except (HwpLiveError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
            try:
                if created_wrapper and hwp is not None:
                    self._releaser(hwp)
            finally:
                self.restore_activation()
            raise
        entry.candidate = candidate
        entry.wrapper = hwp
        self._set_current_entry(entry)
        return candidate, hwp

    def restore_activation(self) -> None:
        restore, self._activation_restore = self._activation_restore, None
        restore_active_document(restore)

    @staticmethod
    def _guard(
        candidate: HwpDocumentCandidate,
        hwp: LiveHwpApplication,
    ) -> Callable[[], None]:
        return lambda: require_active_candidate(candidate, hwp.hwp)

    def disconnect(self, session_id: str) -> MutationResult:
        entry = self._sessions.get(session_id)
        if entry is None:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        _, wrapper = self._validate(session_id)
        try:
            current_page = wrapper.current_page
            modified = wrapper.IsModified
        finally:
            _ = self._sessions.pop(session_id, None)
            _ = self._selector_sessions.pop(entry.selector, None)
            self._listed_candidates = ()
            self._structure_snapshot = None
            self._last_routing_context = None
            try:
                if entry.wrapper is not None:
                    self._releaser(entry.wrapper)
            finally:
                entry.preview_session.close()
            remaining = next(iter(self._sessions.values()), None)
            clear_native_document_route(entry.candidate.window_handle)
            if remaining is None:
                self._moniker_name = None
                self._connected_candidate = None
                self._preview_session = None
                self._selector = None
                self._session_id = None
                self._wrapper = None
            else:
                self._set_current_entry(remaining)
        return MutationResult(
            action="disconnect",
            current_page=current_page,
            modified=modified,
        )

    def close(self) -> None:
        self.restore_activation()
        sessions = tuple(self._sessions.values())
        self._sessions.clear()
        self._selector_sessions.clear()
        self._moniker_name = None
        self._connected_candidate = None
        self._listed_candidates = ()
        self._preview_session = None
        self._selector = None
        self._session_id = None
        self._last_routing_context = None
        self._live_edit_history.cleanup()
        self._wrapper = None
        try:
            for entry in sessions:
                try:
                    if entry.wrapper is not None:
                        self._releaser(entry.wrapper)
                finally:
                    try:
                        entry.preview_session.close()
                    finally:
                        clear_native_document_route(
                            entry.candidate.window_handle
                        )
        finally:
            self._catalog.close()

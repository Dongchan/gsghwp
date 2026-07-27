from __future__ import annotations

import ntpath
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pywintypes import com_error

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
    read_native_snapshot,
    read_native_routing_context,
    select_native_document_route,
)
from hwp_live_preview_store import PreviewSessionLease, PreviewStore
from hwp_live_rot import (
    ActiveDocumentRestore,
    HwpDocumentCandidate,
    HwpDocumentIdentity,
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
)
from hwp_live_session_structure import connected_document
from hwp_live_session_types import (
    DocumentCatalog,
    IdentityDocumentCatalog,
    LiveSessionReference,
    RoutingContextReader,
    WrapperAttacher,
    WrapperReleaser,
)
from hwp_live_style_cache import StyleCacheMetrics, StyleInspectionCache
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
    candidate: HwpDocumentIdentity
    wrapper: LiveHwpApplication | None
    preview_session: PreviewSessionLease
    page_count: int


class LiveHwpSessionCore:
    __slots__: tuple[str, ...] = (
        "_attacher",
        "_activation_restore",
        "_catalog",
        "_last_routing_context",
        "_live_edit_history",
        "_identities",
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
        "_style_cache",
        "_style_state_tokens",
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
    _identities: dict[str, HwpDocumentIdentity]
    _listed_candidates: tuple[HwpDocumentIdentity, ...]
    _moniker_name: str | None
    _preview_session: PreviewSessionLease | None
    _preview_store: PreviewStore
    _releaser: WrapperReleaser
    _routing_context_reader: RoutingContextReader
    _selector: str | None
    _session_id: str | None
    _selector_sessions: dict[str, str]
    _sessions: dict[str, _LiveDocumentSession]
    _style_cache: StyleInspectionCache
    _style_state_tokens: dict[str, str]
    _structure_snapshot: DocumentStructure | None
    _unsafe_selectors: set[str]
    _wrapper: LiveHwpApplication | None
    _connected_candidate: HwpDocumentIdentity | None

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
        self._identities = {}
        self._listed_candidates = ()
        self._moniker_name = None
        self._preview_session = None
        self._preview_store = PreviewStore()
        self._selector = None
        self._session_id = None
        self._selector_sessions = {}
        self._sessions = {}
        self._style_cache = StyleInspectionCache()
        self._style_state_tokens = {}
        self._structure_snapshot = None
        self._unsafe_selectors = set()
        self._wrapper = None

    def _scan_candidates(
        self,
        *,
        force: bool,
    ) -> tuple[HwpDocumentCandidate, ...]:
        if force and isinstance(self._catalog, HwpRotCatalog):
            return self._catalog.scan(force=True)
        return self._catalog.scan()

    def _refresh_session_candidates(
        self,
        candidates: tuple[HwpDocumentCandidate, ...],
        *,
        required_selector: str | None = None,
    ) -> None:
        def matches(
            identity: HwpDocumentIdentity,
        ) -> tuple[HwpDocumentCandidate, ...]:
            normalized_name = ntpath.normcase(ntpath.normpath(identity.full_name))
            return tuple(
                candidate
                for candidate in candidates
                if candidate.document_id == identity.document_id
                and candidate.window_handle == identity.window_handle
                and ntpath.normcase(ntpath.normpath(candidate.full_name))
                == normalized_name
            )

        required_entry = (
            None
            if required_selector is None
            else next(
                (
                    entry
                    for entry in self._sessions.values()
                    if entry.selector == required_selector
                ),
                None,
            )
        )
        if required_entry is not None and len(matches(required_entry.candidate)) != 1:
            raise HwpLiveError(
                "탭 복원 후 대상 한컴 문서의 활성 상태를 정확히 다시 읽지 못했습니다"
            )
        for entry in self._sessions.values():
            candidates_for_entry = matches(entry.candidate)
            if len(candidates_for_entry) != 1:
                continue
            candidate = candidates_for_entry[0]
            page_count = (
                entry.page_count
                if candidate.page_count is None
                else candidate.page_count
            )
            document = candidate.public().model_copy(
                update={
                    "selector": entry.selector,
                    "page_count": page_count,
                }
            )
            entry.page_count = page_count
            entry.candidate = HwpDocumentIdentity.from_document(
                candidate,
                document,
                selector=entry.selector,
            )
            entry.moniker_name = candidate.moniker_name
        if self._session_id is not None:
            current = self._sessions.get(self._session_id)
            if current is not None:
                self._connected_candidate = current.candidate
                self._moniker_name = current.moniker_name

    @staticmethod
    def _session_document(entry: _LiveDocumentSession) -> OpenDocument:
        return entry.candidate.public()

    def _refresh_session_document(
        self,
        entry: _LiveDocumentSession,
    ) -> OpenDocument:
        if isinstance(self._catalog, IdentityDocumentCatalog):
            candidate = self._catalog.resolve_identity(self._entry_identity(entry))
            page_count = (
                entry.page_count
                if candidate.page_count is None
                else candidate.page_count
            )
            document = candidate.public().model_copy(
                update={
                    "selector": entry.selector,
                    "page_count": page_count,
                }
            )
            entry.candidate = HwpDocumentIdentity.from_document(
                candidate,
                document,
                selector=entry.selector,
            )
            entry.moniker_name = candidate.moniker_name
            entry.page_count = page_count
            return self._session_document(entry)
        candidates = self._scan_candidates(force=True)
        self._refresh_session_candidates(
            candidates,
            required_selector=entry.selector,
        )
        return self._session_document(entry)

    @staticmethod
    def _entry_identity(entry: _LiveDocumentSession) -> HwpDocumentIdentity:
        return entry.candidate

    def _release_catalog_com_references(self) -> None:
        if isinstance(self._catalog, IdentityDocumentCatalog):
            self._catalog.release_com_references()

    def _remember_page_count(
        self,
        candidate: HwpDocumentCandidate | HwpDocumentIdentity,
        page_count: int,
    ) -> None:
        if not isinstance(self._catalog, HwpRotCatalog):
            return
        self._catalog.remember_page_count(
            full_name=candidate.full_name,
            document_id=candidate.document_id,
            window_handle=candidate.window_handle,
            page_count=page_count,
        )

    def list_open_documents(self) -> OpenDocumentList:
        candidates = self._scan_candidates(force=False)
        documents = tuple(candidate.public() for candidate in candidates)
        identities = tuple(
            HwpDocumentIdentity.from_document(candidate, document)
            for candidate, document in zip(candidates, documents, strict=True)
        )
        self._listed_candidates = identities
        self._identities.update(
            (identity.selector, identity) for identity in identities
        )
        return OpenDocumentList(documents=documents)

    def open_document(
        self,
        path: str,
        reference_selector: str | None,
        new_tab: bool,
        restore_reference: bool = True,
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
        opened_page_count: int
        opened = None
        open_complete = False
        try:
            registered = candidate.application.RegisterModule(
                ModuleType="FilePathCheckDLL",
                ModuleData="FilePathCheckerModule",
            )
            if not registered and not is_file_path_checker_installed():
                raise HwpLiveError("한컴 파일 경로 보안 모듈 등록이 거부되었습니다")
            opened = candidate.application.XHwpDocuments.Add(new_tab)
            if not candidate.application.Open(requested_path, None, None):
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
            opened_page_count = int(candidate.application.PageCount)
            if opened_page_count < 1:
                raise HwpLiveError("한컴이 연 문서의 본문 쪽을 읽지 못했습니다")
            if isinstance(self._catalog, HwpRotCatalog):
                self._catalog.remember_page_count(
                    full_name=str(opened_full_name),
                    document_id=opened_document_id,
                    window_handle=candidate.window_handle,
                    page_count=opened_page_count,
                )
            open_complete = True
        finally:
            try:
                if not open_complete and opened is not None:
                    opened.SetActive_XHwpDocument()
                    if not candidate.application.Run("FileClose"):
                        raise HwpLiveError(
                            "열기 실패 후 추가된 빈 문서 탭을 닫지 못했습니다"
                        )
            finally:
                if restore_reference or not open_complete:
                    restore_active_document(restore)
        try:
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
        except BaseException:
            if not restore_reference:
                restore_active_document(restore)
            raise

    def connect(self, selector: str) -> ConnectedDocument:
        existing_id = self._selector_sessions.get(selector)
        if existing_id is not None:
            entry = self._sessions[existing_id]
            document = self._refresh_session_document(entry)
            self._set_current_entry(entry)
            if entry.wrapper is None:
                _ = self._validate(entry.session_id)
                self.restore_activation()
                document = self._refresh_session_document(entry)
            return ConnectedDocument(
                session_id=entry.session_id,
                document=document,
            )
        listed_identities, self._listed_candidates = self._listed_candidates, ()
        listed_identity = next(
            (
                identity
                for identity in listed_identities
                if identity.selector == selector
            ),
            None,
        )
        cached_identity = listed_identity or self._identities.get(selector)
        if cached_identity is not None and isinstance(
            self._catalog,
            IdentityDocumentCatalog,
        ):
            try:
                candidate = self._catalog.resolve_identity(cached_identity)
            except HwpLiveError:
                _ = self._identities.pop(selector, None)
                candidate = select_live_candidate(self._catalog, selector)
        else:
            candidate = select_live_candidate(self._catalog, selector)
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
            snapshot = read_native_snapshot(candidate.window_handle)
            guard()
            if snapshot is None:
                raise HwpLiveError("한컴 네이티브 현재 상태 조회를 사용할 수 없습니다")
            connected = ConnectedDocument(
                session_id=session_id,
                document=connected_document(candidate, snapshot),
            )
            self._remember_page_count(candidate, connected.document.page_count)
            restore_active_document(restore)
            restore = None
            candidates = self._scan_candidates(force=True)
            self._refresh_session_candidates(candidates)
            normalized_name = ntpath.normcase(
                ntpath.normpath(connected.document.full_name)
            )
            matches = tuple(
                scanned
                for scanned in candidates
                if scanned.document_id == connected.document.document_id
                and scanned.window_handle == connected.document.window_handle
                and ntpath.normcase(ntpath.normpath(scanned.full_name))
                == normalized_name
            )
            if len(matches) != 1:
                raise HwpLiveError(
                    "연결 후 네이티브 브리지 문서를 정확히 다시 찾지 못했습니다"
                )
            candidate = matches[0]
            connected = connected.model_copy(
                update={
                    "document": candidate.public().model_copy(
                        update={
                            "selector": selector,
                            "page_count": connected.document.page_count,
                            "modified": connected.document.modified,
                        }
                    )
                }
            )
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
            candidate=HwpDocumentIdentity.from_document(
                candidate,
                connected.document,
                selector=selector,
            ),
            wrapper=hwp,
            preview_session=preview_session,
            page_count=connected.document.page_count,
        )
        self._sessions[entry.session_id] = entry
        self._selector_sessions[entry.selector] = entry.session_id
        self._set_current_entry(entry)
        return connected

    def connect_deferred(self, selector: str) -> ConnectedDocument:
        existing_id = self._selector_sessions.get(selector)
        if existing_id is not None:
            entry = self._sessions[existing_id]
            document = self._refresh_session_document(entry)
            self._set_current_entry(entry)
            return ConnectedDocument(
                session_id=entry.session_id,
                document=document,
            )
        listed_identities, self._listed_candidates = self._listed_candidates, ()
        listed_identity = next(
            (
                identity
                for identity in listed_identities
                if identity.selector == selector
            ),
            None,
        )
        identity = listed_identity or self._identities.get(selector)
        if identity is not None and isinstance(
            self._catalog,
            IdentityDocumentCatalog,
        ):
            try:
                candidate = self._catalog.resolve_identity(identity)
            except HwpLiveError:
                _ = self._identities.pop(selector, None)
                candidate = select_live_candidate(self._catalog, selector)
        else:
            candidate = select_live_candidate(self._catalog, selector)
        session_id = secrets.token_urlsafe(24)
        preview_session = self._preview_store.open_session(session_id)
        document = candidate.public().model_copy(update={"selector": selector})
        stored_identity = HwpDocumentIdentity.from_document(
            candidate,
            document,
            selector=selector,
        )
        entry = _LiveDocumentSession(
            session_id=session_id,
            selector=selector,
            moniker_name=candidate.moniker_name,
            candidate=stored_identity,
            wrapper=None,
            preview_session=preview_session,
            page_count=document.page_count,
        )
        self._sessions[entry.session_id] = entry
        self._selector_sessions[entry.selector] = entry.session_id
        self._set_current_entry(entry)
        return ConnectedDocument(
            session_id=session_id,
            document=document,
        )

    def _set_current_entry(self, entry: _LiveDocumentSession) -> None:
        if self._session_id != entry.session_id:
            self._style_cache.clear()
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

    def set_style_state_token(self, session_id: str, state_token: str) -> None:
        if session_id not in self._sessions:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        self._style_state_tokens[session_id] = state_token

    def style_state_token(self, session_id: str) -> str | None:
        if session_id not in self._sessions:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        return self._style_state_tokens.get(session_id)

    def style_cache_metrics(self) -> StyleCacheMetrics:
        return self._style_cache.metrics

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
            raise HwpLiveError(
                "한 작업 안에서 서로 다른 한컴 문서 세션을 전환할 수 없습니다"
            )
        candidate = (
            self._catalog.resolve_identity(self._entry_identity(entry))
            if isinstance(self._catalog, IdentityDocumentCatalog)
            else select_live_candidate(self._catalog, entry.selector)
        )
        select_native_document_route(
            candidate.window_handle,
            candidate.document_id,
        )
        candidate, restore = activate_candidate(candidate)
        self._activation_restore = restore
        hwp: LiveHwpApplication | None = None
        created_wrapper = entry.wrapper is None
        identity: HwpDocumentIdentity | None = None
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
                    raise HwpLiveError("한컴 파일 경로 보안 모듈 등록이 거부되었습니다")
            guard()
            page_count = int(hwp.PageCount)
            if page_count < 1:
                raise HwpLiveError("한컴 문서의 본문 쪽 수를 정확히 읽지 못했습니다")
            identity = HwpDocumentIdentity.from_candidate(
                candidate,
                page_count=page_count,
                selector=entry.selector,
                active=True,
            )
        except (
            HwpLiveError,
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            try:
                if created_wrapper and hwp is not None:
                    self._releaser(hwp)
            finally:
                self.restore_activation()
            raise
        entry.candidate = identity
        entry.moniker_name = identity.moniker_name
        entry.page_count = identity.page_count
        entry.wrapper = hwp
        self._identities[entry.selector] = identity
        self._set_current_entry(entry)
        return candidate, hwp

    def restore_activation(self) -> None:
        restore, self._activation_restore = self._activation_restore, None
        wrapper = self._wrapper
        current = (
            None
            if self._session_id is None
            else self._sessions.get(self._session_id)
        )
        identity = None if current is None else current.candidate
        try:
            if current is not None and identity is not None and wrapper is not None:
                snapshot = read_native_snapshot(identity.window_handle)
                if snapshot is not None and (
                    snapshot.document_id == identity.document_id
                    and ntpath.normcase(ntpath.normpath(snapshot.full_name))
                    == ntpath.normcase(ntpath.normpath(identity.full_name))
                ):
                    page_count = snapshot.page_count
                    modified = snapshot.modified
                else:
                    page_count = int(wrapper.PageCount)
                    modified = identity.modified
                if page_count < 1:
                    raise HwpLiveError(
                        "한컴 문서의 본문 쪽 수를 정확히 읽지 못했습니다"
                    )
                identity = replace(
                    identity,
                    page_count=page_count,
                    modified=modified,
                )
                current.candidate = identity
                current.page_count = page_count
                self._identities[current.selector] = identity
                self._remember_page_count(identity, page_count)
            restore_active_document(restore)
            if identity is not None:
                identity = replace(identity, active=restore is None)
                if current is not None:
                    current.candidate = identity
                    self._identities[current.selector] = identity
        finally:
            try:
                if wrapper is not None:
                    for entry in self._sessions.values():
                        if entry.wrapper is wrapper:
                            entry.wrapper = None
                    self._wrapper = None
                    self._releaser(wrapper)
            finally:
                current = (
                    None
                    if self._session_id is None
                    else self._sessions.get(self._session_id)
                )
                if current is None:
                    self._connected_candidate = None
                else:
                    self._connected_candidate = current.candidate
                    self._moniker_name = current.moniker_name
                self._release_catalog_com_references()

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
            _ = self._identities.pop(entry.selector, None)
            _ = self._style_state_tokens.pop(session_id, None)
            self._style_cache.clear()
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

    def release_transient(self, session_id: str) -> bool:
        entry = self._sessions.get(session_id)
        if entry is None:
            return False
        identity = self._entry_identity(entry)
        try:
            if self._activation_restore is not None:
                self.restore_activation()
        finally:
            _ = self._sessions.pop(session_id, None)
            _ = self._selector_sessions.pop(entry.selector, None)
            _ = self._style_state_tokens.pop(session_id, None)
            self._identities[entry.selector] = identity
            self._style_cache.clear()
            self._listed_candidates = ()
            self._structure_snapshot = None
            self._last_routing_context = None
            self._activation_restore = None
            try:
                try:
                    if entry.wrapper is not None:
                        self._releaser(entry.wrapper)
                finally:
                    try:
                        entry.preview_session.close()
                    finally:
                        clear_native_document_route(entry.candidate.window_handle)
            finally:
                remaining = next(iter(self._sessions.values()), None)
                if remaining is None:
                    self._moniker_name = None
                    self._connected_candidate = None
                    self._preview_session = None
                    self._selector = None
                    self._session_id = None
                    self._wrapper = None
                else:
                    self._set_current_entry(remaining)
                self._release_catalog_com_references()
        return True

    def release_idle_references(self) -> None:
        if self._sessions:
            return
        self._activation_restore = None
        self._listed_candidates = ()
        self._moniker_name = None
        self._connected_candidate = None
        self._preview_session = None
        self._selector = None
        self._session_id = None
        self._wrapper = None
        self._release_catalog_com_references()

    def invalidate_process_loss(self) -> None:
        sessions = tuple(self._sessions.values())
        self._sessions.clear()
        self._selector_sessions.clear()
        self._identities.clear()
        self._style_state_tokens.clear()
        self._style_cache.clear()
        self._activation_restore = None
        self._moniker_name = None
        self._connected_candidate = None
        self._listed_candidates = ()
        self._preview_session = None
        self._selector = None
        self._session_id = None
        self._last_routing_context = None
        self._structure_snapshot = None
        self._unsafe_selectors.clear()
        self._live_edit_history.cleanup()
        self._wrapper = None
        try:
            for entry in sessions:
                try:
                    entry.preview_session.close()
                finally:
                    clear_native_document_route(entry.candidate.window_handle)
        finally:
            self._catalog.close()

    def close(self) -> None:
        try:
            self.restore_activation()
        except (HwpLiveError, com_error):
            pass
        sessions = tuple(self._sessions.values())
        self._sessions.clear()
        self._selector_sessions.clear()
        self._identities.clear()
        self._style_state_tokens.clear()
        self._style_cache.clear()
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
                except (HwpLiveError, com_error):
                    pass
                finally:
                    try:
                        entry.preview_session.close()
                    finally:
                        clear_native_document_route(entry.candidate.window_handle)
        finally:
            self._catalog.close()

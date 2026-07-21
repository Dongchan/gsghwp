from __future__ import annotations

import secrets
from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_contract import ConnectedDocument, MutationResult, OpenDocumentList
from hwp_live_control import require_hwp_2024
from hwp_live_edit_history import LiveEditHistoryStore
from hwp_live_native_batch import read_native_routing_context
from hwp_live_preview import cleanup_previews
from hwp_live_rot import (
    HwpDocumentCandidate,
    HwpRotCatalog,
    attach_wrapper,
    release_wrapper,
    require_active_candidate,
)
from hwp_live_security import is_file_path_checker_installed
from hwp_live_session_candidate import select_live_candidate, select_scanned_candidate
from hwp_live_session_structure import connected_document
from hwp_live_session_types import (
    DocumentCatalog,
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


class LiveHwpSessionCore:
    __slots__: tuple[str, ...] = (
        "_attacher",
        "_catalog",
        "_last_routing_context",
        "_live_edit_history",
        "_listed_candidates",
        "_moniker_name",
        "_releaser",
        "_routing_context_reader",
        "_selector",
        "_session_id",
        "_structure_snapshot",
        "_unsafe_selectors",
        "_wrapper",
        "_connected_candidate",
    )
    _attacher: WrapperAttacher
    _catalog: DocumentCatalog
    _last_routing_context: OperationRoutingContext | None
    _live_edit_history: LiveEditHistoryStore
    _listed_candidates: tuple[HwpDocumentCandidate, ...]
    _moniker_name: str | None
    _releaser: WrapperReleaser
    _routing_context_reader: RoutingContextReader
    _selector: str | None
    _session_id: str | None
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
        self._releaser = releaser
        self._routing_context_reader = routing_context_reader
        self._connected_candidate = None
        self._last_routing_context = None
        self._live_edit_history = LiveEditHistoryStore()
        self._listed_candidates = ()
        self._moniker_name = None
        self._selector = None
        self._session_id = None
        self._structure_snapshot = None
        self._unsafe_selectors = set()
        self._wrapper = None

    def list_open_documents(self) -> OpenDocumentList:
        candidates = self._catalog.scan()
        self._listed_candidates = candidates
        return OpenDocumentList(
            documents=tuple(candidate.public() for candidate in candidates)
        )

    def connect(self, selector: str) -> ConnectedDocument:
        if self._session_id is not None:
            raise HwpLiveError("이미 한컴 라이브 문서에 연결되어 있습니다")
        listed_candidates, self._listed_candidates = self._listed_candidates, ()
        candidate = (
            select_scanned_candidate(listed_candidates, selector)
            if listed_candidates
            else select_live_candidate(self._catalog, selector)
        )
        hwp = self._attacher(candidate, self._wrapper)
        guard = self._guard(candidate, hwp)
        connected: ConnectedDocument | None = None
        try:
            require_hwp_2024(hwp, guard)
            registered = hwp.hwp.RegisterModule(
                ModuleType="FilePathCheckDLL",
                ModuleData="FilePathCheckerModule",
            )
            if not registered and not is_file_path_checker_installed():
                raise HwpLiveError("한컴 파일 경로 보안 모듈 등록이 거부되었습니다")
            session_id = secrets.token_urlsafe(24)
            connected = ConnectedDocument(
                session_id=session_id,
                document=connected_document(candidate, hwp, guard),
            )
        finally:
            if connected is None:
                self._releaser(hwp)
        if connected is None:
            raise HwpLiveError("한컴 라이브 문서 연결 결과가 없습니다")
        self._wrapper = hwp
        self._connected_candidate = candidate
        self._moniker_name = candidate.moniker_name
        self._selector = selector
        self._session_id = connected.session_id
        return connected

    def connection_moniker(self, session_id: str) -> str:
        if self._session_id is None or session_id != self._session_id:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        if self._moniker_name is None:
            raise HwpLiveError("연결된 한컴 ROT 모니커가 없습니다")
        return self._moniker_name

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
        if self._session_id is None or session_id != self._session_id:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        candidate = self._connected_candidate
        if candidate is None:
            raise HwpLiveError("연결된 한컴 문서 후보가 없습니다")
        hwp = self._attacher(candidate, self._wrapper)
        self._guard(candidate, hwp)()
        self._wrapper = hwp
        return candidate, hwp

    @staticmethod
    def _guard(
        candidate: HwpDocumentCandidate,
        hwp: LiveHwpApplication,
    ) -> Callable[[], None]:
        return lambda: require_active_candidate(candidate, hwp.hwp)

    def disconnect(self, session_id: str) -> MutationResult:
        if self._session_id is None or session_id != self._session_id:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        wrapper = self._wrapper
        try:
            current_page = wrapper.current_page if wrapper is not None else 0
            modified = wrapper.IsModified if wrapper is not None else False
        finally:
            self._moniker_name = None
            self._connected_candidate = None
            self._listed_candidates = ()
            self._selector = None
            self._session_id = None
            self._structure_snapshot = None
            self._last_routing_context = None
            self._live_edit_history.cleanup()
            self._wrapper = None
            if wrapper is not None:
                try:
                    self._releaser(wrapper)
                finally:
                    cleanup_previews(session_id)
        return MutationResult(
            action="disconnect",
            current_page=current_page,
            modified=modified,
        )

    def close(self) -> None:
        wrapper = self._wrapper
        session_id = self._session_id
        self._moniker_name = None
        self._connected_candidate = None
        self._listed_candidates = ()
        self._selector = None
        self._session_id = None
        self._last_routing_context = None
        self._live_edit_history.cleanup()
        self._wrapper = None
        try:
            if wrapper is not None:
                self._releaser(wrapper)
        finally:
            if session_id is not None:
                cleanup_previews(session_id)
            self._catalog.close()

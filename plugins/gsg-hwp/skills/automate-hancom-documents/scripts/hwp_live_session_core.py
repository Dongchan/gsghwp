from __future__ import annotations

import ntpath
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from pywintypes import com_error

from hwp_document_convention_cache import ConventionProfileCache
from hwp_document_style_observation import (
    synchronize_document_style_usage_cache,
)
from hwp_errors import HwpLiveError
from hwp_live_api import HwpComApplication, HwpComDocument, LiveHwpApplication
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
    native_foreground_guard,
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
    confirmed_page_count,
    release_wrapper,
    require_active_candidate,
    restore_active_document,
    settle_page_count,
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
    page_count: int | None


def _current_or_remembered_page_count(
    candidate: HwpDocumentCandidate,
    document: OpenDocument,
    entry: _LiveDocumentSession,
) -> int | None:
    """Fill an unknown page count from the session ledger, but only honestly.

    ``document`` is ``candidate.public()``, which already produced this
    candidate's *current* answer: an active tab is read live and every reading
    below one is folded onto ``None`` by ``confirmed_page_count``, because no
    document has zero body pages and 0 means "Hancom has not finished
    paginating", not "no pages".

    So an active candidate's ``None`` is a measurement, not a gap. Refilling it
    from ``entry.page_count`` would put a remembered number back on the wire as
    if it were this instant's reading -- the exact thing
    ``HwpRotCatalog._resolve_page_counts`` refuses to do one layer down, which
    made the comment there false for anything that went through here. The ledger
    answers only for a candidate that could not be read at all, which is exactly
    the inactive one: a background tab's count cannot be read without activating
    it, so the last confirmed reading is the truest answer available.
    """
    if document.page_count is not None or candidate.active:
        return document.page_count
    return entry.page_count


def _normalized_document_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value)) if value else ""


@dataclass(frozen=True, slots=True)
class _ProcessDocument:
    """한 한컴 프로세스가 지금 들고 있는 문서 한 개의 신원.

    ``modified`` 가 ``None`` 이면 "읽지 않았다"는 뜻이다. 명부를 다시 대조할
    때는 신원(``key``)만 있으면 되므로 문서마다 속성 하나를 덜 읽는다.
    """

    document_id: int
    full_name: str
    modified: bool | None

    @property
    def key(self) -> tuple[int, str]:
        return (self.document_id, _normalized_document_path(self.full_name))

    def describe(self) -> str:
        state = "unknown" if self.modified is None else str(self.modified).lower()
        return (
            f"document_id={self.document_id}"
            + f", path={self.full_name or '(저장되지 않은 문서)'}"
            + f", modified={state}"
        )


@dataclass(frozen=True, slots=True)
class _ProcessCensus:
    """한 프로세스의 문서 명부와, 그 명부를 끝까지 믿어도 되는지 여부.

    ``complete`` 가 거짓이면 열거 도중 한/글이 일시적으로 답하지 않은 것이다
    (``hwp_live_rot`` 의 형제 열거기들과 같은 상황). 그때 ``documents`` 는
    "지금까지 본 것"일 뿐 "전부"가 아니므로, 없어진 문서를 판정하는 근거로
    써서는 안 된다.
    """

    documents: tuple[_ProcessDocument, ...]
    complete: bool


def read_process_documents(
    application: HwpComApplication,
    *,
    with_modified: bool = True,
) -> _ProcessCensus:
    """대상 한컴 프로세스가 지금 열고 있는 문서 전부를 읽는다.

    ROT 열거(`list_open_documents`)는 모든 한컴 프로세스를 한꺼번에 답하므로
    "이 프로세스에서 무엇이 닫힐 수 있는가"를 묻는 데 쓸 수 없다.
    ``XHwpDocuments`` 는 정확히 이 프로세스의 문서 모음이라 그 질문에 답한다.
    읽는 값은 전부 문서 속성이라 탭을 활성화하지 않는다.

    일시 응답 실패는 ``hwp_live_rot`` 의 형제 열거기와 똑같이 다룬다:
    ``XHwpDocuments`` 자체가 없거나(AttributeError) ``Item`` 이 ``None`` 을
    주거나 속성 읽기가 COM 오류를 내면 예외를 밖으로 던지지 않고
    ``complete=False`` 로 알린다. 판단은 부르는 쪽 몫이다 -- 변이 전이면
    fail-closed, 변이 뒤면 진행 중인 결과를 덮지 않는 쪽.
    """
    try:
        documents = application.XHwpDocuments
        count = int(documents.Count)
    except (AttributeError, com_error):
        return _ProcessCensus((), complete=False)
    read: list[_ProcessDocument] = []
    complete = True
    for index in range(count):
        try:
            item = cast(HwpComDocument | None, documents.Item(index))
            if item is None:
                # 방어적 중복이다: 이 줄이 없어도 바로 아래 속성 읽기가
                # AttributeError 를 내고 except 가 같은 결론(complete=False)을
                # 낸다. 그래도 남긴다 -- hwp_live_rot 의 형제 열거기(:706-712,
                # :863-869)가 None 을 명시적으로 다루고, 그 관례를 여기서
                # 깨면 다음 사람이 두 열거기를 다르게 읽는다.
                complete = False
                continue
            read.append(
                _ProcessDocument(
                    document_id=int(item.DocumentID),
                    full_name=str(item.FullName),
                    modified=bool(item.Modified) if with_modified else None,
                )
            )
        except (AttributeError, com_error):
            complete = False
    return _ProcessCensus(tuple(read), complete=complete)


def documents_that_must_survive(
    documents: tuple[_ProcessDocument, ...],
    requested_path: str,
) -> tuple[_ProcessDocument, ...]:
    """열기 때문에 사라지면 안 되는 문서만 남긴다.

    한/글 ``Application.Open`` 은 **활성 문서 자리를 재사용한다** -- 네이티브
    쪽 재적재 경로가 ``Clear`` 뒤에 ``Open`` 을 부르는 이유가 그것이다
    (``ActionLifecycle.cpp`` 의 "Open the user's path in the same document
    slot"). 그래서 활성 탭에 남의 문서가 있으면 그 문서는 조용히 닫힌다.

    남길 것과 뺄 것:

    - 고친 문서는 **무조건** 남긴다. 지금 열려는 바로 그 경로여도 그렇다 --
      저장하지 않은 편집이 있는데 다시 적재하면 그 편집이 사라진다.
    - 이름이 있고 지금 열려는 경로가 아닌 문서는 남긴다.
    - 고친 적 없는 같은 경로 문서는 뺀다(재적재).
    - 저장된 적도 고친 적도 없는 빈 새 탭은 뺀다 -- 잃을 것이 없고, 한/글을
      막 띄운 초기 상태가 바로 그것이다.

    ``modified`` 가 ``None``(읽지 않음)이면 "고치지 않음"으로 다룬다. 이
    함수는 ``with_modified=True`` 로 읽은 명부에만 쓰라는 뜻이다.
    """
    requested_key = _normalized_document_path(requested_path)
    return tuple(
        document
        for document in documents
        if document.modified
        or (
            bool(document.full_name)
            and _normalized_document_path(document.full_name) != requested_key
        )
    )


def missing_documents(
    expected: tuple[_ProcessDocument, ...],
    present: tuple[_ProcessDocument, ...],
) -> tuple[_ProcessDocument, ...]:
    live = {document.key for document in present}
    return tuple(document for document in expected if document.key not in live)


def describe_documents(documents: tuple[_ProcessDocument, ...]) -> str:
    shown = documents[:8]
    detail = "; ".join(document.describe() for document in shown)
    omitted = len(documents) - len(shown)
    return detail + (f"; 그 외 {omitted}개" if omitted else "")


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
        "_validated_candidate",
        "_wrapper",
        "_connected_candidate",
        "_convention_cache",
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
    _validated_candidate: HwpDocumentCandidate | None
    _wrapper: LiveHwpApplication | None
    _connected_candidate: HwpDocumentIdentity | None
    _convention_cache: ConventionProfileCache

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
        self._convention_cache = ConventionProfileCache()
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
        self._validated_candidate = None
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
            public = candidate.public()
            page_count = _current_or_remembered_page_count(candidate, public, entry)
            document = public.model_copy(
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
            public = candidate.public()
            page_count = _current_or_remembered_page_count(candidate, public, entry)
            document = public.model_copy(
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
        page_count: int | None,
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
        """열려 있던 다른 문서를 닫지 않고 문서 하나를 연다.

        보호의 근거는 대상 프로세스의 문서 명부다. 명부는 전경 가드 안, 변이
        COM 호출(``XHwpDocuments.Add`` / ``Application.Open``) **바로 앞**에서
        읽는다 -- 그 앞의 ROT 스캔·활성화까지 마친 뒤여야 명부와 실제 상태의
        간격이 가장 좁다.

        **남는 창(원자성 없음).** 한/글은 우리 것이 아니므로 명부를 읽은 순간과
        ``Add`` 사이에 사용자가 직접 탭을 열거나 닫을 수 있다. 그 창은 없앨 수
        없고 좁힐 수만 있다. 그래서 판정을 이렇게 나눈다: 그 사이에 **생긴**
        문서는 보호 대상에 못 들어가고(다음 호출부터 보호된다), 그 사이에
        사용자가 **닫은** 문서는 우리가 닫은 것으로 고발되지 않도록 명부를
        변이 직전에 다시 읽어 기준으로 삼는다.
        """
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
        opened_page_count: int | None = None
        opened = None
        # 실패 정리(FileClose)는 우리가 만든 탭에만 허용한다. Add 가 새 자리를
        # 주지 못했을 때 이 값이 False 로 남고, 그러면 정리가 사용자의 문서를
        # 닫아 버리는 일이 없다.
        opened_is_added_tab = False
        open_complete = False
        # 사용자가 말한 "처음 작업 시작할 때"가 바로 이 구간이다. XHwpDocuments
        # .Add와 Application.Open, 그리고 실패 정리의 SetActive_XHwpDocument는
        # 전부 파이썬이 한컴 COM을 직접 호출하는 경로라 브리지 Invoke의 가드가
        # 덮지 못한다. 구간의 시작과 끝만 브리지에 알리고, 무장·거부·복원 판단은
        # 전부 C++에 남긴다.
        with native_foreground_guard(candidate.window_handle):
            try:
                registered = candidate.application.RegisterModule(
                    ModuleType="FilePathCheckDLL",
                    ModuleData="FilePathCheckerModule",
                )
                if not registered and not is_file_path_checker_installed():
                    raise HwpLiveError("한컴 파일 경로 보안 모듈 등록이 거부되었습니다")
                # 명부는 여기서 읽는다. ROT 스캔·활성화·모듈 등록이 전부 끝난
                # 뒤이고 변이 COM 호출(Add/Open) 바로 앞이라, 명부와 실제 상태
                # 사이의 간격이 가장 좁다. 이 명부가 없으면 열기가 무엇을
                # 덮어썼는지 사후에도 알 수 없다 -- 실제로 두 번, 열려 있던
                # 문서가 조용히 닫혔는데 열기는 성공을 반환했다.
                census = read_process_documents(candidate.application)
                if not census.complete:
                    # 변이 전이므로 fail-closed 다. 다만 생예외가 아니라
                    # 구조화된 거부로 답한다: 무엇을 지키려다 멈췄는지 부르는
                    # 쪽이 알아야 재시도를 판단할 수 있다.
                    raise HwpLiveError(
                        "; ".join(
                            (
                                "한컴 문서 명부를 끝까지 읽지 못해 문서 열기를 "
                                + "시작하지 않았습니다",
                                "opened=false",
                                "mutation_started=false",
                                "retry_safe=true",
                                "cause=한컴이 문서 열거에 일시적으로 응답하지 "
                                + "않았습니다",
                                "user_action=잠시 후 다시 호출하세요",
                            )
                        )
                    )
                process_documents = census.documents
                must_survive = documents_that_must_survive(
                    process_documents,
                    requested_path,
                )
                if must_survive and not new_tab:
                    # new_tab=False 는 XHwpDocuments.Add(False) 로 그대로
                    # 내려간다. 그 호출이 새 문서 자리를 만들어 주지 못하면 바로
                    # 뒤의 Open 이 활성 탭을 재사용해 사용자의 문서를 닫는다.
                    # 닫힌 뒤에는 되돌릴 방법이 없으므로, 잃을 것이 있는
                    # 프로세스에서는 변이를 아예 시작하지 않는다.
                    raise HwpLiveError(
                        "; ".join(
                            (
                                "new_tab=false 열기는 이 한컴 프로세스에 열려 "
                                + "있는 문서를 대체할 수 있어 시작하지 "
                                + "않았습니다",
                                "opened=false",
                                "mutation_started=false",
                                "retry_safe=true",
                                f"documents_at_risk={len(must_survive)}",
                                describe_documents(must_survive),
                                "user_action=new_tab=true로 다시 호출하면 빈 "
                                + "새 탭에 열립니다",
                            )
                        )
                    )
                opened = candidate.application.XHwpDocuments.Add(new_tab)
                # 이 읽기가 실패하면 opened_is_added_tab 이 False 로 남고, 아래
                # 실패 정리는 탭을 닫지 않는다. 방금 만든 빈 탭이 남을 수는
                # 있지만, 우리 것인지 모르는 탭을 닫아 남의 문서를 없애는 것보다
                # 낫다. 의도한 보수적 방향이다.
                opened_is_added_tab = int(opened.DocumentID) not in {
                    document.document_id for document in process_documents
                }
                if must_survive:
                    # Add 가 정말로 새 문서 자리를 만들어 줬는지를 Open 앞에서
                    # 끝낸다. Open 은 활성 문서 자리를 재사용하므로, 이 확인을
                    # 통과하지 못한 채로 Open 을 부르면 그 자리에 있던 사용자
                    # 문서가 닫힌다. 닫힌 뒤의 보고는 사과일 뿐이라 여기서 멈춘다.
                    #
                    # 신원만 대조하면 되므로 modified 는 읽지 않는다.
                    added_census = read_process_documents(
                        candidate.application,
                        with_modified=False,
                    )
                    if not added_census.complete:
                        # 아직 Open 전이다. 못 믿을 명부로 진행하느니 멈춘다.
                        raise HwpLiveError(
                            "; ".join(
                                (
                                    "빈 새 탭을 만든 뒤 문서 명부를 끝까지 "
                                    + "읽지 못해 열기를 중단했습니다",
                                    "opened=false",
                                    "retry_safe=true",
                                    f"documents_at_risk={len(must_survive)}",
                                    describe_documents(must_survive),
                                    "user_action=잠시 후 다시 호출하세요",
                                )
                            )
                        )
                    lost_to_add = missing_documents(
                        must_survive,
                        added_census.documents,
                    )
                    if lost_to_add:
                        raise HwpLiveError(
                            "; ".join(
                                (
                                    "빈 새 탭을 만드는 중에 이미 열려 있던 "
                                    + "한컴 문서가 닫혔습니다",
                                    "opened=false",
                                    f"documents_closed={len(lost_to_add)}",
                                    describe_documents(lost_to_add),
                                    "user_action=한/글에서 닫힌 문서를 다시 "
                                    + "열고 확인하세요",
                                )
                            )
                        )
                    active_added = (
                        candidate.application.XHwpDocuments.Active_XHwpDocument
                    )
                    if not opened_is_added_tab or int(
                        active_added.DocumentID
                    ) != int(opened.DocumentID):
                        raise HwpLiveError(
                            "; ".join(
                                (
                                    "빈 새 탭을 얻지 못해 문서 열기를 "
                                    + "중단했습니다",
                                    "opened=false",
                                    "mutation_started=false",
                                    "retry_safe=true",
                                    f"documents_at_risk={len(must_survive)}",
                                    describe_documents(must_survive),
                                    "user_action=한/글에서 새 탭을 하나 만든 "
                                    + "뒤 다시 호출하세요",
                                )
                            )
                        )
                if not candidate.application.Open(requested_path, None, None):
                    raise HwpLiveError("한컴에서 문서를 열지 못했습니다")
                # 지킬 것이 없으면 읽지 않는다: 대조할 대상이 없는 명부 읽기는
                # 정의상 낭비다(변이 경로에는 COM-busy 재시도가 없다,
                # hwp_live_bridge.py 의 _COM_BUSY_DELAYS 는 읽기 전용이다).
                lost_to_open: tuple[_ProcessDocument, ...] = ()
                if must_survive:
                    opened_census = read_process_documents(
                        candidate.application,
                        with_modified=False,
                    )
                    # 여기는 이미 문서가 열린 뒤다. 열거가 일시적으로 끊긴 것을
                    # "문서가 닫혔다"로 읽으면 성공한 열기를 취소하고 롤백이
                    # 방금 연 문서를 닫아 버린다. 못 읽었으면 고발하지 않는다.
                    if opened_census.complete:
                        lost_to_open = missing_documents(
                            must_survive,
                            opened_census.documents,
                        )
                if lost_to_open:
                    # 여기까지 왔다면 Open 이 새 탭이 아닌 다른 자리를 먹었다는
                    # 뜻이다. 되돌릴 수는 없지만, 성공으로 답하지는 않는다.
                    raise HwpLiveError(
                        "; ".join(
                            (
                                "문서를 여는 동안 이미 열려 있던 한컴 문서가 "
                                + "닫혔습니다",
                                f"requested_path={requested_path}",
                                f"documents_closed={len(lost_to_open)}",
                                describe_documents(lost_to_open),
                                "user_action=한/글에서 닫힌 문서를 다시 열고 "
                                + "확인하세요",
                            )
                        )
                    )
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
                # 큰 문서는 Open 이 돌아온 뒤에도 한/글이 쪽 나누기를 끝내지
                # 않아 PageCount 가 잠깐 0 이다. 예전에는 그걸 곧바로 거부했다.
                # 문서는 이미 정상적으로 열려 있으므로 거부할 일이 아니라 잠깐
                # 기다렸다 다시 읽을 일이고, 그래도 모르면 모른다고 답한다.
                #
                # 이 기다림은 원래 활성 문서를 복원(아래 finally)하기 전이다.
                # 뒤로 못 옮긴다: Application.PageCount 는 그때그때의 활성
                # 문서를 답하므로 복원한 뒤에 읽으면 방금 연 문서가 아니라
                # 사용자의 원래 문서 쪽 수를 읽게 된다 -- 틀린 값을 싣느니
                # 기다리는 편이 낫다. 아래 목록 조회가 이 값을 다시 못 읽는
                # 이유도 같다(뒤 주석). 대신 붙잡는 시간은 상한으로 묶는다:
                # hwp_live_rot.PAGE_COUNT_SETTLE_TIMEOUT_SECONDS.
                opened_page_count = settle_page_count(
                    lambda: int(candidate.application.PageCount),
                    source=candidate.application,
                ).page_count
                # 기억의 열쇠에는 창 핸들이 들어간다. 예전에는 기준 문서의
                # 핸들만 걸었는데, 새 탭은 자기 창 핸들을 갖기 때문에 바로 뒤의
                # 목록 조회가 그 값을 찾지 못했고 방금 읽은 쪽 수가 그대로
                # 사라졌다. 두 핸들 모두에 건다: 네이티브 브리지 모니커가 있는
                # 문서는 자기 핸들로 열거되고, 그렇지 않은 문서는 열거 시점의
                # 활성 창 핸들로 열거된다. 두 열쇠 모두 전체 경로와 document_id
                # 가 이 문서의 것이므로 다른 문서의 값이 될 수 없다.
                opened_window_handle = int(
                    candidate.application.XHwpWindows.Active_XHwpWindow.WindowHandle
                )
                if isinstance(self._catalog, HwpRotCatalog):
                    for remembered_handle in {
                        opened_window_handle,
                        candidate.window_handle,
                    }:
                        self._catalog.remember_page_count(
                            full_name=str(opened_full_name),
                            document_id=opened_document_id,
                            window_handle=remembered_handle,
                            page_count=opened_page_count,
                        )
                open_complete = True
            finally:
                try:
                    close_added_tab = (
                        not open_complete
                        and opened is not None
                        and opened_is_added_tab
                    )
                    if close_added_tab and opened is not None:
                        # id 는 활성화보다 먼저 채취한다. SetActive 가 죽는
                        # 상황이면 그 뒤로는 opened 에서 아무것도 못 읽는다.
                        # id 를 못 읽으면 아래 대조는 어떤 document_id 와도
                        # 같지 않아 자연히 침묵한다.
                        added_document_id: int | None
                        try:
                            added_document_id = int(opened.DocumentID)
                        except (AttributeError, com_error):
                            added_document_id = None
                        cleanup_ran = True
                        try:
                            opened.SetActive_XHwpDocument()
                            _ = candidate.application.Run("FileClose")
                        except (AttributeError, com_error):
                            # 정리 자체가 죽었다. 이 블록은 이미 실패가 진행
                            # 중인 finally 이므로, 정리 실패를 새 예외로 올려
                            # 원래 실패 원인을 갈아치우지 않는다. 빈 탭이 남을
                            # 수 있고 그것은 감수한다.
                            cleanup_ran = False
                        # 한/글 2024 의 Run("FileClose") 는 탭을 정말로 닫고도
                        # False 를 돌려준다(실측). 그 반환값으로 판정하면 실패
                        # 정리가 늘 "닫지 못했다"고 외치며 진짜 실패 원인을
                        # 덮는다. 그래서 반환값이 아니라 문서 목록을 본다.
                        #
                        # 열거가 일시적으로 끊겨 명부를 다 못 읽었으면 판정하지
                        # 않는다 -- 같은 이유로, 확인 실패가 원인을 대체하면 안
                        # 된다.
                        leftover_census = (
                            read_process_documents(
                                candidate.application,
                                with_modified=False,
                            )
                            if cleanup_ran
                            else _ProcessCensus((), complete=False)
                        )
                        if leftover_census.complete and any(
                            document.document_id == added_document_id
                            for document in leftover_census.documents
                        ):
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
            # 목록은 원래 활성 문서를 복원한 뒤에 읽으므로 방금 연 문서는 이미
            # 비활성이고, 비활성 탭의 쪽 수는 열거만으로는 알 수 없다. 여기서
            # 실제로 읽은 값이 있으면 그것을 싣는다. 이것이 hwp_open_document 가
            # page_count 0 을 돌려주던 경로다.
            if opened_page_count is None:
                return matches[0]
            return matches[0].model_copy(
                update={"page_count": opened_page_count},
            )
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
        entry = self._sessions.get(session_id)
        if entry is None:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        synchronize_document_style_usage_cache(
            (id(self), session_id),
            state_token,
            (
                entry.candidate.window_handle,
                entry.candidate.document_id,
                entry.candidate.full_name,
            ),
        )
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

    def _reusable_validation(
        self,
        session_id: str,
        entry: _LiveDocumentSession,
    ) -> tuple[HwpDocumentCandidate, LiveHwpApplication] | None:
        """Return the identity already confirmed in this activation window.

        One tool call enters ``_validate`` many times (``propagate_table_cells``
        re-enters once per target). Re-resolving the moniker every time costs a
        full round of cross-process COM reads while proving nothing new: the
        window is only open between one ``_validate`` and the matching
        ``restore_activation``. The reused candidate is still re-confirmed
        against the live active document below, and the native executor
        re-validates the identity in-process immediately before it runs.
        Anything that fails the re-confirmation drops the reuse and falls back
        to the full resolve path, so no check is skipped, only repeated less
        often.
        """
        candidate = self._validated_candidate
        wrapper = entry.wrapper
        if candidate is None or wrapper is None or self._session_id != session_id:
            return None
        identity = entry.candidate
        if (
            candidate.document_id != identity.document_id
            or candidate.window_handle != identity.window_handle
            or candidate.moniker_name != identity.moniker_name
            or ntpath.normcase(ntpath.normpath(candidate.full_name))
            != ntpath.normcase(ntpath.normpath(identity.full_name))
        ):
            self._validated_candidate = None
            return None
        try:
            require_active_candidate(candidate, candidate.application)
        except (HwpLiveError, com_error):
            self._validated_candidate = None
            return None
        return candidate, wrapper

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
        reused = self._reusable_validation(session_id, entry)
        if reused is not None:
            confirmed, wrapper = reused
            reused_wrapper = wrapper
            settled = settle_page_count(
                lambda: int(reused_wrapper.PageCount),
                source=reused_wrapper,
            )
            if settled.waited:
                # 기다리는 동안 대상 탭이 바뀌었을 수 있다. 기다린 경우에만
                # 다시 확인한다(정상 경로에는 왕복이 늘지 않는다). 이 확인을
                # 건너뛰면 다른 문서의 쪽 수를 이 세션 것으로 적게 된다. 새 거부가
                # 아니라 아래 전체 경로가 이미 쓰는 그 신원 확인이다.
                try:
                    require_active_candidate(confirmed, confirmed.application)
                except (HwpLiveError, com_error):
                    self._validated_candidate = None
                    raise
            page_count = settled.page_count
            identity = HwpDocumentIdentity.from_candidate(
                confirmed,
                page_count=page_count,
                selector=entry.selector,
                active=True,
            )
            entry.candidate = identity
            entry.moniker_name = identity.moniker_name
            entry.page_count = identity.page_count
            self._identities[entry.selector] = identity
            self._set_current_entry(entry)
            return confirmed, wrapper
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
            live_wrapper = hwp
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
            # 80MB 급 문서를 열자마자 조회하면 한/글이 아직 쪽 나누기를 끝내지
            # 않아 PageCount 가 0 이다. 예전에는 여기서 모든 세션 도구를 거부해
            # 문서를 열자마자 아무것도 못 하게 만들었다. 이제는 짧게 기다렸다
            # 다시 읽고, 그래도 모르면 쪽 수만 미확정(None)으로 두고 진행한다.
            #
            # 이 자리는 activate_candidate 로 대상 탭을 활성화한 뒤다. 기다림을
            # 활성화 구간 밖으로 옮길 수 없다: PageCount 는 활성 문서의 값이라
            # 원래 탭을 복원한 뒤에 읽으면 다른 문서를 읽는다. 다만 활성화
            # 구간은 이 기다림이 여는 것이 아니라 도구 호출 전체가 이미 여는
            # 것이고(restore_activation 이 닫는다), 이 기다림은 그 구간의 앞을
            # 늘릴 뿐이다. 늘어나는 몫은 상한으로 묶는다:
            # hwp_live_rot.PAGE_COUNT_SETTLE_TIMEOUT_SECONDS.
            settled = settle_page_count(
                lambda: int(live_wrapper.PageCount),
                source=live_wrapper,
            )
            if settled.waited:
                # 기다린 경우에만 가드를 한 번 더 돈다. 새 거부가 아니라 바로
                # 위에서 이미 도는 그 가드의 새 호출 지점이다: 기다리는 동안
                # 대상이 바뀌었을 수 있으므로 그때만 다시 확인한다. 정상 문서는
                # waited=False 라 왕복이 늘지 않는다.
                guard()
            page_count = settled.page_count
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
                self._validated_candidate = None
                self.restore_activation()
            raise
        entry.candidate = identity
        entry.moniker_name = identity.moniker_name
        entry.page_count = identity.page_count
        entry.wrapper = hwp
        self._identities[entry.selector] = identity
        self._validated_candidate = candidate
        self._set_current_entry(entry)
        return candidate, hwp

    def restore_activation(self) -> None:
        # End of the activation window: the confirmed identity must not be
        # reused by the next tool call.
        self._validated_candidate = None
        restore, self._activation_restore = self._activation_restore, None
        wrapper = self._wrapper
        current = (
            None if self._session_id is None else self._sessions.get(self._session_id)
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
                # 여기는 도구 실행이 끝난 뒤의 장부 갱신이다. 쪽 수를 못 읽었다고
                # 이미 끝난 작업을 실패로 만들 이유가 없고, 여기서 기다리면 모든
                # 도구의 꼬리에 지연이 붙는다. 미확정으로 적고 넘어간다.
                identity = replace(
                    identity,
                    page_count=confirmed_page_count(page_count),
                    modified=modified,
                )
                current.candidate = identity
                current.page_count = identity.page_count
                self._identities[current.selector] = identity
                self._remember_page_count(identity, identity.page_count)
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
            self._validated_candidate = None
            _ = self._sessions.pop(session_id, None)
            _ = self._selector_sessions.pop(entry.selector, None)
            _ = self._identities.pop(entry.selector, None)
            _ = self._style_state_tokens.pop(session_id, None)
            self._unsafe_selectors.discard(entry.selector)
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
            self._validated_candidate = None
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
        self._validated_candidate = None
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
        self._validated_candidate = None
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
        self._validated_candidate = None
        self._moniker_name = None
        self._connected_candidate = None
        self._listed_candidates = ()
        self._preview_session = None
        self._selector = None
        self._session_id = None
        self._last_routing_context = None
        self._unsafe_selectors.clear()
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

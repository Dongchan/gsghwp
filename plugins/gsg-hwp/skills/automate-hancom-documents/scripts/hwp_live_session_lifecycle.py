from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic_ns
from typing import Final, Literal, cast, final

from hwp_errors import HwpLiveError
from hwp_live_native_batch_contract import NativeLifecycleResult, NativeSaveResult
from hwp_live_process_lane import (
    HwpLaneOperationContext,
    current_lane_operation_context,
)
from hwp_operation_contract import OperationResult
from hwp_operation_registry import operation_registry


# C++ 이 "부르지 않았다"를 적어 보내는 두 자리. E_PENDING 은 HRESULT 변수의
# 초기값이고(DocumentLifecycle.cpp:306-320), E_ABORT 는 저장은 끝났으나
# 복원원이 없어 Clear 를 건너뛴 자리다(:340-341).
_E_PENDING: Final = -2_147_483_638  # 0x8000000A
_E_ABORT: Final = -2_147_467_260  # 0x80004004


SavePhase = Literal[
    "queued",
    "native_started",
    "heartbeat",
    "progress",
    "native_returned",
    "metadata_changed",
    "metadata_unchanged",
    "verified",
    "uncertain",
]


@final
@dataclass(frozen=True, slots=True)
class SaveTransition:
    phase: SavePhase
    source: str
    observed_at_monotonic_ns: int
    detail: str | None = None


@final
@dataclass(frozen=True, slots=True)
class SaveStateSnapshot:
    phase: SavePhase
    transitions: tuple[SaveTransition, ...]
    terminal: bool
    retry_safe: bool
    close_blocked: bool


@final
class SaveStateMachine:
    __slots__ = ("_lock", "_phase", "_transitions")

    def __init__(self) -> None:
        self._lock = Lock()
        self._phase: SavePhase = "queued"
        self._transitions = [
            SaveTransition(
                phase="queued",
                source="request",
                observed_at_monotonic_ns=monotonic_ns(),
            )
        ]

    def _append(
        self,
        phase: SavePhase,
        *,
        source: str,
        detail: str | None = None,
    ) -> None:
        self._phase = phase
        self._transitions.append(
            SaveTransition(
                phase=phase,
                source=source,
                detail=detail,
                observed_at_monotonic_ns=monotonic_ns(),
            )
        )

    def native_started(self, *, source: str, detail: str | None = None) -> None:
        with self._lock:
            if self._phase == "queued":
                self._append("native_started", source=source, detail=detail)
                return
            if (
                source == "DocumentBeforeSave"
                and self._phase in {"native_started", "heartbeat", "progress"}
                and not any(
                    transition.source == source for transition in self._transitions
                )
            ):
                self._append(
                    "heartbeat" if self._phase != "progress" else "progress",
                    source=source,
                    detail=detail,
                )

    def heartbeat(self, *, source: str, detail: str | None = None) -> None:
        with self._lock:
            if self._phase not in {"native_started", "heartbeat", "progress"}:
                return
            if any(
                transition.source == source
                and transition.phase in {"heartbeat", "progress"}
                for transition in self._transitions
            ):
                return
            self._append(
                "progress" if self._phase == "progress" else "heartbeat",
                source=source,
                detail=detail,
            )

    def progress(self, *, source: str, detail: str | None = None) -> None:
        with self._lock:
            if self._phase not in {"native_started", "heartbeat", "progress"}:
                return
            if self._phase == "progress":
                if source != "DocumentAfterSave" or any(
                    transition.source == source for transition in self._transitions
                ):
                    return
            self._append("progress", source=source, detail=detail)

    def native_returned(self, *, source: str, detail: str | None = None) -> None:
        with self._lock:
            if self._phase in {
                "native_returned",
                "metadata_changed",
                "verified",
                "uncertain",
            }:
                return
            if self._phase == "queued":
                self._append("native_started", source="native_call")
            self._append("native_returned", source=source, detail=detail)

    def metadata_changed(self, *, source: str, detail: str | None = None) -> None:
        with self._lock:
            if self._phase in {
                "metadata_changed",
                "metadata_unchanged",
                "verified",
                "uncertain",
            }:
                return
            if self._phase not in {"native_returned"}:
                if self._phase == "queued":
                    self._append("native_started", source="native_call")
                self._append("native_returned", source="native_call")
            self._append("metadata_changed", source=source, detail=detail)

    def metadata_unchanged(self, *, source: str, detail: str | None = None) -> None:
        with self._lock:
            if self._phase in {
                "metadata_changed",
                "metadata_unchanged",
                "verified",
                "uncertain",
            }:
                return
            if self._phase != "native_returned":
                if self._phase == "queued":
                    self._append("native_started", source="native_call")
                self._append("native_returned", source="native_call")
            self._append("metadata_unchanged", source=source, detail=detail)

    def verified(self, *, source: str, detail: str | None = None) -> None:
        with self._lock:
            if self._phase in {"verified", "uncertain"}:
                return
            if self._phase not in {"metadata_changed", "metadata_unchanged"}:
                return
            self._append("verified", source=source, detail=detail)

    def uncertain(self, *, source: str, detail: str | None = None) -> None:
        with self._lock:
            if self._phase in {"verified", "uncertain"}:
                return
            self._append("uncertain", source=source, detail=detail)

    def snapshot(self) -> SaveStateSnapshot:
        with self._lock:
            phase = self._phase
            transitions = tuple(self._transitions)
        terminal = phase in {"verified", "uncertain"}
        return SaveStateSnapshot(
            phase=phase,
            transitions=transitions,
            terminal=terminal,
            retry_safe=phase in {"queued", "verified"},
            close_blocked=phase != "verified",
        )


def _save_state_suffix(state: SaveStateSnapshot) -> str:
    transitions = ",".join(transition.phase for transition in state.transitions)
    return (
        f"; save_state={state.phase}; save_transitions={transitions}; "
        f"save_terminal={'true' if state.terminal else 'false'}; "
        f"save_close_blocked={'true' if state.close_blocked else 'false'}"
    )


def _lane_save_state() -> tuple[SaveStateMachine, HwpLaneOperationContext | None]:
    context = current_lane_operation_context()
    if context is None:
        return SaveStateMachine(), None
    existing = context.save_state()
    if isinstance(existing, SaveStateMachine):
        return existing, context
    created = SaveStateMachine()
    attached = context.attach_save_state(created)
    if attached is None:
        raise HwpLiveError(
            "".join(
                (
                    "문서 닫기가 시작되어 저장을 실행하지 않았습니다",
                    "; save_state=queued; mutation_started=false",
                    "; retry_safe=true",
                )
            )
        )
    return cast(SaveStateMachine, attached), context


def _native_save_state(native: NativeSaveResult) -> SaveStateSnapshot:
    state, context = _lane_save_state()
    state.native_returned(
        source="native_return",
        detail=f"hresult={native.save_hresult};return={native.save_return}",
    )
    metadata_changed = (
        context.watchdog_metadata_changed
        if context is not None
        else (
            native.before_modified is True
            and native.saved_path not in {None, ""}
            and native.file_size > 0
        )
    )
    if metadata_changed:
        state.metadata_changed(
            source="watchdog" if context is not None else "native_file_metadata",
            detail=(
                f"size={native.file_size};"
                f"write_time_100ns={native.file_write_time_100ns}"
            ),
        )
    elif native.before_modified is False and native.save_return == 0:
        state.metadata_unchanged(
            source="clean_no_op",
            detail="document was already unmodified",
        )
    if native.verified:
        state.verified(source="native_readback")
        if state.snapshot().phase != "verified":
            state.uncertain(
                source="metadata_evidence_missing",
                detail="native readback returned without file metadata evidence",
            )
    else:
        state.uncertain(source="native_readback")
    return state.snapshot()


def _native_lifecycle_save_state(
    native: NativeLifecycleResult,
) -> SaveStateSnapshot:
    state, context = _lane_save_state()
    state.native_returned(
        source="native_lifecycle_return",
        detail=f"hresult={native.save_hresult};return={native.save_return}",
    )
    if context is not None and context.watchdog_metadata_changed:
        state.metadata_changed(
            source="watchdog",
            detail="file size or write time changed before native return",
        )
    elif native.before_modified is False and native.save_return == 0:
        state.metadata_unchanged(
            source="clean_no_op",
            detail="document was already unmodified",
        )
    if native.verified:
        state.verified(source="native_reopen_readback")
        if state.snapshot().phase != "verified":
            state.uncertain(
                source="metadata_evidence_missing",
                detail="native reopen returned without file metadata evidence",
            )
    else:
        state.uncertain(source="native_reopen_readback")
    return state.snapshot()


def _reopen_baseline_unavailable(native: NativeLifecycleResult) -> bool:
    # DocumentLifecycle.cpp:337-341 — 저장이 끝난 뒤 재개방 검증의 복원원이
    # 없으면 C++ 은 Clear 를 **부르지 않고** clear_hresult 자리에 E_ABORT 를
    # 적어 보낸다. 복원원은 둘이다: 저장 전 문서 지문(before.captured,
    # OfficialApiState.cpp:718-740)과 SetTextFile 로 되돌릴 HWP 블록
    # (ReadDocumentBlock, DocumentLifecycle.cpp:212-223). 엔진은 자기 메모리
    # 한계를 넘는 문서를 직렬화하지 않으므로 그 크기에서는 둘 다 항상 없다.
    # 그러면 Clear 도 Open 도 시도되지 않아 문서는 그대로 열려 있는데,
    # clear_hresult < 0 만 보는 쪽은 그것을 "Clear 호출 실패"로 부르고
    # partial_mutation 을 세운다 — 아무것도 건드리지 않은 호출에 대해.
    #
    # 시도되지 않았다는 증인을 함께 요구한다. Clear 가 실제로 돌았다면
    # clear_return 이 -1 이 아니고, Open 이 돌았다면 open_hresult 가 초기값
    # E_PENDING 이 아니다. 하나라도 어긋나면 기존 "clear" 로 되돌아간다.
    return (
        native.clear_hresult == _E_ABORT
        and native.clear_return == -1
        and native.open_hresult == _E_PENDING
        and native.open_return == -1
        and native.recovered is False
        and native.recovery_hresult == _E_PENDING
    )


def _lifecycle_failure_stage(native: NativeLifecycleResult) -> str | None:
    if native.verified:
        return None
    save_completed = (
        native.save_hresult >= 0
        and (
            native.save_return == 1
            or (native.before_modified is False and native.save_return == 0)
        )
        and native.post_save_modified is False
    )
    if not save_completed:
        return "save"
    if native.clear_hresult < 0:
        return (
            "reopen_baseline_unavailable"
            if _reopen_baseline_unavailable(native)
            else "clear"
        )
    if native.open_hresult < 0 or native.open_return != 1:
        return "reopen"
    if native.reopened_path is None:
        return "session_identity"
    if native.after_modified is not False:
        return "modified_state"
    if native.before_page_count != native.after_page_count:
        return "page_fingerprint"
    if (
        native.before_control_count != native.after_control_count
        or native.before_control_hash != native.after_control_hash
    ):
        return "control_fingerprint"
    if native.before_text_hash != native.after_text_hash:
        return "text_fingerprint"
    if native.before_document_hash != native.after_document_hash:
        return "document_fingerprint"
    return "native_verification"


def _lifecycle_failure_message(stage: str, recovered: bool) -> str:
    if stage == "reopen_baseline_unavailable":
        # "실패했습니다"로 부르지 않는다. 실패한 호출이 없다.
        return (
            "Save 호출과 저장 직후 수정 상태는 확인했지만, 재개방 검증의 "
            "복원원(저장 전 문서 지문·복구 블록)을 엔진이 만들지 못해 "
            "Clear·재개방을 시도하지 않았습니다. 문서는 그대로 열려 있고 "
            "디스크 영속성만 이 경로에서 확인되지 않았습니다. 저장 여부는 "
            "operation status의 저장 전후 파일 지문으로 확인하세요"
        )
    descriptions = {
        "save": "Save 호출 또는 저장 직후 수정 상태 확인",
        "clear": "저장 문서 Clear 호출",
        "reopen": "같은 경로 Open 호출",
        "session_identity": "재개방 문서 경로·세션 식별",
        "modified_state": "재개방 후 수정 상태 확인",
        "page_fingerprint": "재개방 전후 쪽 수 대조",
        "control_fingerprint": "재개방 전후 개체 구조 지문 대조",
        "text_fingerprint": "재개방 전후 본문 지문 대조",
        "document_fingerprint": "재개방 전후 문서 지문·서식 대조",
        "native_verification": "네이티브 저장·재개방 최종 판정",
    }
    recovery = " 세션 내용은 복구됐지만" if recovered else ""
    return f"{descriptions[stage]}에 실패했습니다.{recovery} 디스크 영속성은 확인되지 않았습니다"


def lifecycle_preflight_result(
    query: str,
    *,
    resolve_only: bool,
    allow_document_change: bool,
) -> OperationResult | None:
    if resolve_only:
        return OperationResult(
            status="resolved",
            query=query,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            message="저장·재개방 검증 operation을 확정했습니다",
        )
    if not allow_document_change:
        return OperationResult(
            status="confirmation_required",
            query=query,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            message="저장·재개방 검증은 문서 변경을 포함하므로 확인이 필요합니다",
        )
    state, _ = _lane_save_state()
    state.native_started(source="native_dispatch")
    return None


def save_preflight_result(
    query: str,
    *,
    resolve_only: bool,
    allow_document_change: bool,
) -> OperationResult | None:
    if resolve_only:
        return OperationResult(
            status="resolved",
            query=query,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            message="일반 저장 operation을 확정했습니다",
        )
    if not allow_document_change:
        return OperationResult(
            status="confirmation_required",
            query=query,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            message="일반 저장은 파일 상태를 변경하므로 확인이 필요합니다",
        )
    state, _ = _lane_save_state()
    state.native_started(source="native_dispatch")
    return None


def _native_save_completed(native: NativeSaveResult) -> bool:
    return (
        native.save_hresult >= 0
        and (
            native.save_return == 1
            or (native.before_modified is False and native.save_return == 0)
        )
        and native.post_save_modified is False
        and native.after_modified is False
    )


def _document_hash_evidence_absent(native: NativeSaveResult) -> bool:
    # 엔진은 자기 메모리 한계를 넘는 문서를 직렬화하지 않는다. 그때
    # CaptureDocumentFingerprint 의 GetTextFile(HWPML2X) 가 S_OK 와 빈 문자열을
    # 돌려주고(OfficialApiState.cpp:539-551), C++ 은 문서 해시를 0 으로 적어
    # 보내며 파이썬은 그것을 None 으로 읽는다. 즉 저장 전후 둘 다 없다는 것은
    # "문서가 달라졌다"가 아니라 "이 증인은 이 문서 크기에서 애초에 존재하지
    # 않는다"이다. 한쪽만 없으면 그 사이에 무엇이 있었는지 모르므로 여기가
    # 아니다.
    return native.before_document_hash is None and native.after_document_hash is None


def _observable_live_state_stable(native: NativeSaveResult) -> bool:
    return (
        native.before_page_count is not None
        and native.before_page_count == native.after_page_count
        and native.before_control_count is not None
        and native.before_control_count == native.after_control_count
        and native.before_control_hash == native.after_control_hash
        and native.before_text_hash is not None
        and native.before_text_hash == native.after_text_hash
        # 없는 증인을 불일치로 세지 않는다. 문서 해시가 서로 "다른" 경우는
        # 이미 여기를 통과해 파일 지문(SHA-256) 판정으로 넘어간다
        # (test_lv6_save_uses_strict_file_fingerprint_without_claiming_live_state).
        # 아예 "못 만든" 경우만 실패로 묶어 둘 근거가 없었다. 대용량 문서에서
        # 그 조합이 성공한 저장을 통째로 operation_failed 로 만들었다.
        and (native.before_document_hash is None)
        == (native.after_document_hash is None)
    )


def _save_metadata_observed(save_state: SaveStateSnapshot) -> bool:
    return any(
        transition.phase in {"metadata_changed", "metadata_unchanged"}
        for transition in save_state.transitions
    )


_NATIVE_READBACK_UNCERTAINTY_SOURCES: Final = frozenset(
    {
        "native_readback",
        "native_reopen_readback",
        "metadata_evidence_missing",
    }
)


def _save_state_has_external_uncertainty(save_state: SaveStateSnapshot) -> bool:
    return any(
        transition.phase == "uncertain"
        and transition.source not in {"native_readback", "metadata_evidence_missing"}
        for transition in save_state.transitions
    )


def save_state_uncertainty_is_native_readback_only(
    save_state: SaveStateSnapshot,
) -> bool:
    """미확정의 출처가 네이티브 readback 하나뿐인지 본다.

    네이티브 readback 의 판정은 저장 전후 문서 지문 대조를 요구하고
    (DocumentLifecycle.cpp:258-267 → SameFingerprint :147-158), 그 함수는
    첫 줄에서 ``before.captured && after.captured`` 를 본다. 엔진이 자기
    메모리 한계 위의 문서를 직렬화하지 못하면 이 증인은 애초에 존재하지
    않으므로, 그 크기에서는 저장할 때마다 미확정이 새로 찍힌다.

    감시견 관측, 대화상자, 프로세스 소실, 마감 같은 **바깥** 출처는 저장이
    실제로 어디까지 갔는지 모른다는 뜻이라 이 함수가 거짓을 낸다. 그런
    미확정은 더 강한 디스크 증거가 나와도 걷어내면 안 된다.
    """
    uncertain = tuple(
        transition
        for transition in save_state.transitions
        if transition.phase == "uncertain"
    )
    return len(uncertain) > 0 and all(
        transition.source in _NATIVE_READBACK_UNCERTAINTY_SOURCES
        for transition in uncertain
    )


def _save_evidence_missing(
    native: NativeSaveResult,
    save_state: SaveStateSnapshot,
) -> str | None:
    if not _native_save_completed(native):
        return (
            "modified_state"
            if native.save_hresult >= 0
            and native.save_return in {0, 1}
            and (
                native.post_save_modified is not False
                or native.after_modified is not False
            )
            else "save_call"
        )
    if native.saved_path in {None, ""}:
        return "saved_path"
    if (
        native.file_size <= 0
        or native.file_write_time_100ns is None
        or native.file_write_time_100ns <= 0
    ):
        return "file_metadata"
    if not _save_metadata_observed(save_state):
        return "save_state_metadata"
    if not _observable_live_state_stable(native):
        if (
            native.before_page_count is None
            or native.before_page_count != native.after_page_count
        ):
            return "page_fingerprint"
        if (
            native.before_control_count is None
            or native.before_control_count != native.after_control_count
            or native.before_control_hash != native.after_control_hash
        ):
            return "control_fingerprint"
        if (
            native.before_text_hash is None
            or native.before_text_hash != native.after_text_hash
        ):
            return "text_fingerprint"
        return "document_fingerprint"
    return None


def _live_state_verification_stage(native: NativeSaveResult) -> str:
    if native.verified:
        return "verified"
    if not _native_save_completed(native):
        return "save_call"
    if (
        native.before_page_count is None
        or native.before_page_count != native.after_page_count
    ):
        return "page_fingerprint"
    if (
        native.before_control_count is None
        or native.before_control_count != native.after_control_count
        or native.before_control_hash != native.after_control_hash
    ):
        return "control_fingerprint"
    if (
        native.before_text_hash is None
        or native.before_text_hash != native.after_text_hash
    ):
        return "text_fingerprint"
    if _document_hash_evidence_absent(native):
        # 대조에 실패한 것이 아니라 엔진이 이 크기의 문서에 대해 지문을 만들지
        # 못했다. 두 경우를 같은 이름으로 부르면 읽는 쪽이 문서가 변했다고 읽는다.
        return "document_fingerprint_unavailable"
    if (
        native.before_document_hash is None
        or native.before_document_hash != native.after_document_hash
    ):
        return "document_fingerprint"
    if native.saved_path in {None, ""}:
        return "saved_path"
    if (
        native.file_size <= 0
        or native.file_write_time_100ns is None
        or native.file_write_time_100ns <= 0
    ):
        return "file_metadata"
    return "native_verification"


def save_result(query: str, native: NativeSaveResult) -> OperationResult:
    save_state = _native_save_state(native)
    live_state_preserved = native.verified and save_state.phase == "verified"
    missing_evidence = _save_evidence_missing(native, save_state)
    save_completion_observed = missing_evidence is None
    execution_observed = live_state_preserved or save_completion_observed
    partial_mutation = not (
        _native_save_completed(native)
        and _observable_live_state_stable(native)
        and not _save_state_has_external_uncertainty(save_state)
    )
    live_state_stage = _live_state_verification_stage(native)
    return OperationResult(
        status="executed" if execution_observed else "operation_failed",
        changed=native.before_modified is True and native.post_save_modified is False,
        query=query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        message=(
            "Save 호출, 수정 상태 해제, 대상 경로·파일 메타데이터와 저장 전후 "
            + "라이브 문서 상태 보존을 확인했습니다. 디스크 내용 영속성은 재개방 "
            + "진단에서만 검증됩니다"
            if live_state_preserved
            else (
                "네이티브 Save 완료값·파일 메타데이터와 쪽·개체·본문 상태를 "
                + "관측했습니다. 라이브 문서 상태의 엄격한 검증 결과는 별도로 "
                + "유지하며 최종 저장 판정은 기존 저장 전후 SHA-256 지문 "
                + f"교차검증을 따릅니다; live_state_verification={live_state_stage}"
                if save_completion_observed
                else "일반 저장을 확정할 근거가 부족하여 자동 재시도하지 않습니다. "
                + "operation status의 저장 전후 파일 지문과 아래 누락 근거를 "
                + f"확인하세요; save_evidence_missing={missing_evidence}; "
                + f"live_state_verification={live_state_stage}"
            )
        )
        + _save_state_suffix(save_state),
        execution_mode="native_in_process",
        native_protocol=12,
        verification="native_save_result",
        verified=live_state_preserved,
        native_elapsed_microseconds=native.elapsed_microseconds,
        page_count=native.after_page_count,
        modified=native.after_modified,
        saved_path=native.saved_path,
        before_page_count=native.before_page_count,
        before_modified=native.before_modified,
        before_control_count=native.before_control_count,
        before_control_hash=native.before_control_hash,
        before_text_hash=native.before_text_hash,
        before_document_hash=native.before_document_hash,
        save_hresult=native.save_hresult,
        save_return=native.save_return,
        post_save_modified=native.post_save_modified,
        after_page_count=native.after_page_count,
        after_modified=native.after_modified,
        after_control_count=native.after_control_count,
        after_control_hash=native.after_control_hash,
        after_text_hash=native.after_text_hash,
        after_document_hash=native.after_document_hash,
        saved_file_size=native.file_size,
        saved_file_write_time_100ns=native.file_write_time_100ns,
        live_state_preserved_after_save=live_state_preserved,
        disk_persistence_verified=False,
        partial_mutation=partial_mutation,
        retry_safe=live_state_preserved,
        reconcile_required=not live_state_preserved,
    )


def lifecycle_result(
    query: str,
    native: NativeLifecycleResult,
) -> OperationResult:
    failure_stage = _lifecycle_failure_stage(native)
    save_state = _native_lifecycle_save_state(native)
    verified = native.verified and save_state.phase == "verified"
    if not verified and failure_stage is None:
        failure_stage = "native_verification"
    return OperationResult(
        status="executed" if verified else "operation_failed",
        query=query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        message=(
            (
                "프로토콜 12 C++/ATL에서 저장 후 같은 경로를 재개방하고 "
                "본문·표·문자 서식을 검증했습니다"
            )
            if verified
            else _lifecycle_failure_message(
                failure_stage or "native_verification",
                native.recovered,
            )
        )
        + _save_state_suffix(save_state),
        failure_stage=failure_stage,
        execution_mode="native_in_process",
        native_protocol=12,
        verification="native_save_reopen_result",
        verified=verified,
        native_elapsed_microseconds=native.elapsed_microseconds,
        page_count=native.after_page_count,
        modified=native.after_modified,
        reopened_path=native.reopened_path,
        before_page_count=native.before_page_count,
        before_modified=native.before_modified,
        before_control_count=native.before_control_count,
        before_control_hash=native.before_control_hash,
        before_text_hash=native.before_text_hash,
        before_document_hash=native.before_document_hash,
        save_hresult=native.save_hresult,
        save_return=native.save_return,
        post_save_modified=native.post_save_modified,
        clear_hresult=native.clear_hresult,
        clear_return=native.clear_return,
        open_hresult=native.open_hresult,
        open_return=native.open_return,
        session_recovered=native.recovered,
        recovery_hresult=native.recovery_hresult,
        recovery_return=native.recovery_return,
        after_page_count=native.after_page_count,
        after_modified=native.after_modified,
        after_control_count=native.after_control_count,
        after_control_hash=native.after_control_hash,
        after_text_hash=native.after_text_hash,
        after_document_hash=native.after_document_hash,
        live_state_preserved_after_save=verified or native.recovered,
        disk_persistence_verified=verified,
        # 시도되지 않은 Clear·Open 은 문서를 반쯤 바꿔 놓을 수 없다.
        partial_mutation=not (
            verified
            or native.recovered
            or failure_stage == "reopen_baseline_unavailable"
        ),
        retry_safe=verified,
        reconcile_required=not verified,
    )

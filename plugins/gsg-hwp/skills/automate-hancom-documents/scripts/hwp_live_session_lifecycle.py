from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic_ns
from typing import Literal, cast, final

from hwp_errors import HwpLiveError
from hwp_live_native_batch_contract import NativeLifecycleResult, NativeSaveResult
from hwp_live_process_lane import (
    HwpLaneOperationContext,
    current_lane_operation_context,
)
from hwp_operation_contract import OperationResult
from hwp_operation_registry import operation_registry


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
        return "clear"
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


def _observable_live_state_stable(native: NativeSaveResult) -> bool:
    return (
        native.before_page_count is not None
        and native.before_page_count == native.after_page_count
        and native.before_control_count is not None
        and native.before_control_count == native.after_control_count
        and native.before_control_hash == native.after_control_hash
        and native.before_text_hash is not None
        and native.before_text_hash == native.after_text_hash
        and native.before_document_hash is not None
        and native.after_document_hash is not None
    )


def _save_metadata_observed(save_state: SaveStateSnapshot) -> bool:
    return any(
        transition.phase in {"metadata_changed", "metadata_unchanged"}
        for transition in save_state.transitions
    )


def _save_state_has_external_uncertainty(save_state: SaveStateSnapshot) -> bool:
    return any(
        transition.phase == "uncertain"
        and transition.source not in {"native_readback", "metadata_evidence_missing"}
        for transition in save_state.transitions
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
        partial_mutation=False if verified or native.recovered else True,
        retry_safe=verified,
        reconcile_required=not verified,
    )

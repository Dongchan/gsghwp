from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — public response models and their one-way adapter form one schema boundary.

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, Protocol, assert_never

from typing_extensions import TypeIs

from pydantic import Field

from hwp_current_format_insert_contract import CurrentFormatInsertEvidence
from hwp_public_cell_selector import SeriesImageCell, SeriesTextCell
from hwp_live_values import ContractModel
from hwp_operation_contract import (
    IdempotencyStatus,
    OperationPhaseTiming,
    OperationResult,
    OperationStatus,
    TableFormatCandidate,
    WorkflowTargetCandidate,
)
from hwp_runtime_identity import RuntimeBuildInfo
from hwp_visibility_template_observation import VisibilityTemplateObservation


type PublicActionStatus = Literal[
    "succeeded",
    "needs_input",
    "needs_target",
    "unsupported",
    "failed",
    "partial_failure",
]
type FailedOperationStatus = Literal[
    "resolved",
    "not_found",
    "needs_guard",
    "confirmation_required",
    "blocked",
    "known_failure",
    "schema_conflict",
    "operation_in_progress",
    "operation_stale",
    "operation_failed",
    "operation_aborted",
    "operation_reconciled",
    "request_id_conflict",
]
type TerminalPublicActionStatus = Literal[
    "succeeded",
    "unsupported",
    "failed",
    "partial_failure",
]

_EMPTY_INPUT_ALIASES: Final[Mapping[str, str]] = MappingProxyType({})


class PublicTableTarget(ContractModel):
    document_path: str | None = Field(default=None, max_length=32_767)
    page: int | None = Field(default=None, ge=1)
    caption: str | None = Field(default=None, min_length=1, max_length=500)
    headers: tuple[str, ...] = Field(default=(), max_length=100)
    table_index: int | None = Field(default=None, ge=1)
    target_id: str | None = Field(default=None, min_length=1, max_length=128)


class PublicTargetCandidate(ContractModel):
    target_id: str = Field(min_length=1, max_length=128)
    page: int = Field(ge=1)
    table_index: int | None = Field(default=None, ge=1)
    rows: int | None = Field(default=None, ge=1)
    columns: int | None = Field(default=None, ge=1)
    caption: str | None = Field(default=None, max_length=2_000)
    headers: tuple[str, ...] = Field(default=(), max_length=100)


class SeriesItem(ContractModel):
    values: dict[str, str] = Field(
        default_factory=dict,
        max_length=500,
        description="고유한 현재 셀 텍스트 또는 셀 주소를 새 텍스트에 대응시킵니다.",
    )
    images: dict[str, Path] = Field(
        default_factory=dict,
        max_length=200,
        description="고유한 현재 셀 텍스트 또는 셀 주소를 그림 경로에 대응시킵니다.",
    )
    text_cells: tuple[SeriesTextCell, ...] = Field(
        default=(),
        max_length=500,
        description="중복 라벨이나 상대 위치가 필요한 텍스트 셀 선택 목록입니다.",
    )
    image_cells: tuple[SeriesImageCell, ...] = Field(
        default=(),
        max_length=200,
        description="중복 라벨이나 상대 위치가 필요한 그림 셀 선택 목록입니다.",
    )
    caption: str | None = Field(default=None, min_length=1, max_length=2_000)


class PublicTargetStore(Protocol):
    def remember(
        self,
        candidates: tuple[WorkflowTargetCandidate, ...],
        document_path: str | None,
    ) -> tuple[str, ...]: ...

    def clear(self) -> None: ...


class PublicSaveEvidence(ContractModel):
    path: str | None = Field(default=None, max_length=32_767)
    save_hresult: int | None = None
    save_return: int | None = Field(default=None, ge=-1, le=1)
    post_save_modified: bool | None = None
    baseline_file_size: int | None = Field(default=None, ge=0)
    baseline_file_mtime_ns: int | None = Field(default=None, ge=0)
    baseline_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    baseline_diagnostic_reason: (
        Literal[
            "file_missing",
            "access_failed",
            "unstable",
            "incomplete",
            "not_attached",
        ]
        | None
    ) = None
    file_size: int | None = Field(default=None, ge=0)
    file_write_time_100ns: int | None = Field(default=None, ge=0)
    file_mtime_ns: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fingerprint_stable: bool | None = None
    fingerprint_changed: bool | None = None
    fingerprint_verified: bool | None = None
    live_state_preserved_after_save: bool | None = None
    disk_persistence_verified: bool | None = None
    session_recovered: bool | None = None


class PublicActionResult(ContractModel):
    status: PublicActionStatus
    message: str = Field(max_length=4_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    idempotency_status: IdempotencyStatus = "not_requested"
    runtime: RuntimeBuildInfo
    verified: bool
    modified: bool
    required_inputs: tuple[str, ...] = Field(default=(), max_length=20)
    target_candidates: tuple[PublicTargetCandidate, ...] = Field(
        default=(),
        max_length=3,
    )
    format_candidates: tuple[TableFormatCandidate, ...] = Field(
        default=(),
        max_length=24,
    )
    recipe_id: str | None = Field(default=None, max_length=100)
    commands_executed: int = Field(default=0, ge=0)
    phase_timings: tuple[OperationPhaseTiming, ...] = Field(
        default=(),
        max_length=32,
    )
    updated_addresses: tuple[str, ...] = Field(default=(), max_length=20_000)
    created_target_ids: tuple[str, ...] = Field(default=(), max_length=500)
    affected_pages: tuple[int, ...] = Field(default=(), max_length=500)
    state_token: str | None = Field(default=None, max_length=500)
    affected_target_ids: tuple[str, ...] = Field(default=(), max_length=500)
    selected_target_id: str | None = Field(default=None, max_length=500)
    input_guidance: tuple[str, ...] = Field(default=(), max_length=20)
    retry_operation_id: str | None = Field(default=None, min_length=1, max_length=128)
    retry_safe: bool
    reconcile_required: bool = False
    save_evidence: PublicSaveEvidence | None = None
    # Which checkpoint path the bridge actually used, straight from its own
    # call results. null means this operation captured no checkpoint.
    document_checkpoint_capture: Literal["document_file", "encoded_block"] | None = None
    # true means SaveAs adopted the checkpoint copy despite lock:false and the
    # bridge saved the user's own document to move the session back onto it —
    # a write nobody asked for. Never omitted when a checkpoint was captured:
    # false is the reassurance, null only means there was no checkpoint.
    document_identity_restored: bool | None = None
    visibility_template_observation: tuple[VisibilityTemplateObservation, ...] = Field(
        default=(),
        max_length=20,
    )
    current_format_insert: CurrentFormatInsertEvidence | None = None


def _is_failed_status(status: OperationStatus) -> TypeIs[FailedOperationStatus]:
    return status in {
        "resolved",
        "not_found",
        "needs_guard",
        "confirmation_required",
        "blocked",
        "known_failure",
        "schema_conflict",
        "operation_in_progress",
        "operation_stale",
        "operation_failed",
        "operation_aborted",
        "operation_reconciled",
        "request_id_conflict",
    }


def _is_terminal_status(
    status: PublicActionStatus,
) -> TypeIs[TerminalPublicActionStatus]:
    return status in {"succeeded", "unsupported", "failed", "partial_failure"}


def _public_status(result: OperationResult) -> PublicActionStatus:
    match result.status:
        case "executed":
            return (
                "partial_failure"
                if result.failure_stage
                in {
                    "image_replace_content_verification",
                    "table_caption_visibility_verification",
                }
                else "succeeded"
            )
        case "needs_input":
            return "needs_input"
        case "ambiguous":
            return "needs_target"
        case "unsupported":
            return "unsupported"
        case "partial_change":
            return "partial_failure"
        case "transport_error":
            changed = (
                result.changed
                or result.modified is True
                or result.partial_change
                or result.partial_mutation is True
            )
            return "partial_failure" if changed else "failed"
        case _ as unreachable if not _is_failed_status(unreachable):
            assert_never(unreachable)
        case _:
            return "failed"


def _required_inputs(
    status: PublicActionStatus,
    result: OperationResult,
    input_aliases: Mapping[str, str],
) -> tuple[str, ...]:
    match status:
        case "needs_target":
            if result.text_candidates:
                return ("target.occurrence",)
            return (input_aliases.get("inputs.target", "target.target_id"),)
        case "needs_input":
            # 막힌 필드는 반드시 제 이름으로 알려준다. 예전에는
            # inputs.policy.numeric_value_mode 를 "cells" 로 바꿔 불렀고,
            # 모델은 고칠 수 없는 필드 대신 cells 값을 지어내 다시 보내다가
            # 같은 자리에서 또 막혔다. 아래 이름은 모두 실제 도구 파라미터다.
            aliases = {
                "inputs.data": "records | cells | rows",
                "inputs.data.start_cell": "start_cell",
                "inputs.target": "target",
                "inputs.policy.numeric_value_mode": "numeric_value_mode",
                "inputs.policy.preserve_display_format": "preserve_display_format",
                "inputs.policy.scale_conflict": "scale_conflict",
                **input_aliases,
            }
            return tuple(
                aliases.get(value, value.removeprefix("inputs."))
                for value in result.required_inputs
            )
        case _ as unreachable if not _is_terminal_status(unreachable):
            assert_never(unreachable)
        case _:
            return ()


def _verified(result: OperationResult) -> bool:
    return result.verified is True


def _public_message(result: OperationResult) -> str:
    if not result.text_candidates:
        return result.message
    locations = ", ".join(
        (
            f"occurrence={candidate.occurrence} "
            f"start={candidate.start.list_id}:{candidate.start.paragraph}:"
            f"{candidate.start.character} "
            f"end={candidate.end.list_id}:{candidate.end.paragraph}:"
            f"{candidate.end.character}"
        )
        for candidate in result.text_candidates
    )
    return (
        f"{result.message} 후보 위치: {locations}. "
        "전체 후보는 operation_id로 hwp_get_operation_status를 조회할 수 있습니다"
    )


# 값을 지어내라고 시키는 문구를 여기에 두지 마라. 안내는 호출자가 실제로 바꿀
# 수 있는 파라미터만 가리켜야 한다. 예전 문구("cells의 해당 주소에 단위·괄호·
# 줄바꿈을 포함한 최종 표시 문자열을 직접 지정하세요")는 그대로 따라 해도 같은
# 재조립을 거쳐 다시 실패했다.
_DISPLAY_FORMAT_GUIDANCE: Final = (
    "preserve_display_format=false: 기존 셀의 접두어·단위·괄호·자릿수를 되살리지 "
    "않고 준 문자열을 그대로 씁니다. 글꼴·크기·색은 preserve_character_style이 "
    "따로 유지하므로 서식은 잃지 않습니다."
)
_NUMERIC_MODE_WITH_CANDIDATES_GUIDANCE: Final = (
    "numeric_value_mode: format_candidates의 각 항목이 그 값으로 실행했을 때의 "
    "replacement를 보여줍니다. 원하는 결과를 낸 항목의 numeric_value_mode를 "
    "display 또는 base로 그대로 지정하세요."
)


def _input_guidance(
    status: PublicActionStatus,
    result: OperationResult,
) -> tuple[str, ...]:
    if status != "needs_input":
        return ()
    required = frozenset(result.required_inputs)
    if "inputs.policy.preserve_display_format" in required:
        return (_DISPLAY_FORMAT_GUIDANCE,)
    if "inputs.policy.numeric_value_mode" not in required:
        return ()
    if result.format_candidates:
        return (_NUMERIC_MODE_WITH_CANDIDATES_GUIDANCE,)
    # 고를 후보가 없다. 표시 형식 추론이 값을 안전하게 옮길 수 없다고 판단한
    # 경우이므로, 실제로 통하는 손잡이는 재조립을 끄는 것 하나다.
    return (_DISPLAY_FORMAT_GUIDANCE,)


_MAXIMUM_AFFECTED_PAGES = 500


def _affected_pages(result: OperationResult, modified: bool) -> tuple[int, ...]:
    """실제로 바뀐 쪽. 근거가 없으면 종전대로 커서 쪽만 보고한다.

    변경 여부 판정은 종전과 같다. 바뀌지 않은 작업은 근거가 남아 있어도 빈 목록이다.
    """
    if not (modified or result.changed or result.reconcile_required):
        return ()
    if result.changed_pages:
        return tuple(sorted(set(result.changed_pages)))
    return () if result.current_page is None else (result.current_page,)


def _truncation_note(pages: tuple[int, ...]) -> str:
    """상한을 넘겨 잘라냈다는 사실을 응답 본문으로 알린다.

    조용히 500개로 자르면 모델은 그 뒤 쪽을 "안 바뀐 쪽"으로 오해한다. 지금 고치는
    결함과 같은 종류의 거짓말이므로, 구조화 필드는 스키마를 지키되 잘린 범위는 말한다.
    """
    return (
        f" 변경된 쪽이 {len(pages)}개로 affected_pages 상한 "
        f"{_MAXIMUM_AFFECTED_PAGES}개를 넘어 앞쪽 {_MAXIMUM_AFFECTED_PAGES}개만 "
        f"담았습니다. 실제 변경 범위는 {pages[0]}~{pages[-1]}쪽이며 "
        f"{pages[_MAXIMUM_AFFECTED_PAGES]}쪽부터는 목록에서 빠졌습니다."
    )


def _affected_target_ids(
    result: OperationResult,
    selected_target_id: str | None,
) -> tuple[str, ...]:
    values = (
        selected_target_id,
        result.resolved_target_id,
        *result.created_control_ids,
    )
    return tuple(dict.fromkeys(value for value in values if value is not None))


def refresh_public_target_ids(
    store: PublicTargetStore,
    result: OperationResult,
    document_path: str | None,
) -> tuple[str, ...]:
    if result.target_candidates:
        return store.remember(result.target_candidates, document_path)
    if (
        result.status != "needs_input"
        or result.changed
        or result.modified is True
        or result.partial_change
        or result.partial_mutation is True
    ):
        store.clear()
    return ()


def to_public_action_result(
    result: OperationResult,
    target_ids: tuple[str, ...],
    input_aliases: Mapping[str, str] = _EMPTY_INPUT_ALIASES,
    *,
    selected_target_id: str | None = None,
) -> PublicActionResult:
    candidates = result.target_candidates[:3]
    public_candidates = tuple(
        PublicTargetCandidate(
            target_id=target_id,
            page=candidate.page,
            table_index=candidate.table_index,
            rows=candidate.rows,
            columns=candidate.columns,
            caption=candidate.caption,
            headers=candidate.header_preview,
        )
        for candidate, target_id in zip(candidates, target_ids, strict=True)
    )
    status = _public_status(result)
    modified = result.changed if result.modified is None else result.modified
    retry_safe = (
        status in {"needs_input", "needs_target"}
        if result.retry_safe is None
        else result.retry_safe
    )
    selected_id = selected_target_id or result.resolved_target_id
    affected_pages = _affected_pages(result, modified)
    message = _public_message(result)
    if len(affected_pages) > _MAXIMUM_AFFECTED_PAGES:
        note = _truncation_note(affected_pages)
        affected_pages = affected_pages[:_MAXIMUM_AFFECTED_PAGES]
        message = message[: 4_000 - len(note)] + note
    state_token = (
        result.structure_digest_after
        or result.after_document_hash
        or result.result_digest
    )
    retry_operation_id = (
        result.request_id
        if result.request_id is not None
        and status not in {"needs_input", "needs_target", "unsupported"}
        and (status == "succeeded" or retry_safe)
        else None
    )
    save_evidence = (
        None
        if result.save_hresult is None
        and result.saved_file_sha256 is None
        and result.save_baseline_sha256 is None
        and result.save_baseline_diagnostic_reason is None
        else PublicSaveEvidence(
            path=result.saved_path or result.reopened_path,
            save_hresult=result.save_hresult,
            save_return=result.save_return,
            post_save_modified=result.post_save_modified,
            baseline_file_size=result.save_baseline_file_size,
            baseline_file_mtime_ns=result.save_baseline_file_mtime_ns,
            baseline_sha256=result.save_baseline_sha256,
            baseline_diagnostic_reason=result.save_baseline_diagnostic_reason,
            file_size=result.saved_file_size,
            file_write_time_100ns=result.saved_file_write_time_100ns,
            file_mtime_ns=result.saved_file_mtime_ns,
            sha256=result.saved_file_sha256,
            fingerprint_stable=result.save_fingerprint_stable,
            fingerprint_changed=(
                None
                if result.save_fingerprint_changed is True
                and result.save_fingerprint_verified is not True
                else result.save_fingerprint_changed
            ),
            fingerprint_verified=result.save_fingerprint_verified,
            live_state_preserved_after_save=(result.live_state_preserved_after_save),
            disk_persistence_verified=result.disk_persistence_verified,
            session_recovered=result.session_recovered,
        )
    )
    return PublicActionResult(
        status=status,
        message=message,
        request_id=result.request_id,
        idempotency_status=result.idempotency_status,
        runtime=result.runtime,
        verified=_verified(result),
        modified=modified,
        required_inputs=_required_inputs(status, result, input_aliases),
        target_candidates=public_candidates,
        format_candidates=result.format_candidates,
        recipe_id=result.recipe_id,
        commands_executed=result.commands_executed or 0,
        phase_timings=result.phase_timings,
        updated_addresses=result.updated_addresses,
        created_target_ids=result.created_control_ids,
        affected_pages=affected_pages,
        state_token=state_token,
        affected_target_ids=_affected_target_ids(result, selected_id),
        selected_target_id=selected_id,
        input_guidance=_input_guidance(status, result),
        retry_operation_id=retry_operation_id,
        retry_safe=retry_safe,
        reconcile_required=result.reconcile_required,
        save_evidence=save_evidence,
        document_checkpoint_capture=result.document_checkpoint_capture,
        document_identity_restored=result.document_identity_restored,
        visibility_template_observation=result.visibility_template_observation,
        current_format_insert=result.current_format_insert,
    )

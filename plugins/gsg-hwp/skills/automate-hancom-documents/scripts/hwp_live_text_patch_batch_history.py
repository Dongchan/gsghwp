from __future__ import annotations

# pyright: reportPrivateUsage=false

from collections.abc import Callable
from dataclasses import replace
from time import perf_counter_ns
from typing import Final

from hwp_checkpoint_signature import checkpoint_signatures_match
from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_edit_history import (
    NO_CHECKPOINT_EVIDENCE,
    LiveEditHistoryStore,
    LogicalTextPatchBatchHistoryEntry,
)
from hwp_live_edit_history_runtime import (
    _CHECKPOINT_NOT_RECORDED_NOTICE,
    _broken_document_identity,
    _capture_failure_clause,
    _capture_reason_clause,
    _identity_notice_after_applied_edit,
    _prepare_text_patch_checkpoint,
    _record_completed_edit,
    _remove_path,
    _restore_prepared_before_failure,
    document_checkpoint_unavailable_reason,
)
from hwp_live_native_action_contract import (
    NativeActionFailure,
    NativeActionFailureEvidence,
)
from hwp_live_native_action_models import (
    BooleanValue,
    IntegerValue,
    NativeActionCommand,
    NativeActionRequest,
    NativePosition,
    NativeSetter,
    MovePositionCommand,
    ParameterActionCommand,
    TextPatchCommand,
    TextValue,
)
from hwp_live_native_batch import (
    execute_native_actions,
    preflight_native_text_patches,
    read_native_content_signature,
)
from hwp_live_native_text_format import ParagraphFormatting, paragraph_command
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_text_patch_batch import (
    TextPatchBatchVerificationError,
    TextPatchPreparedReceipt,
    compile_text_patch_batch,
    patch_validated_text_batch,
    preflight_text_patch_batch,
    validate_text_patch_plan_guard,
)
from hwp_live_text_patch_prepared import (
    canonical_prepared_text_patch_requests,
    effective_prepared_text_patch_requests,
)
from hwp_live_text_patch_contract import (
    TextPatchPhaseTiming,
    TextPatchPlanGuard,
    TextPatchRequest,
    TextPatchResult,
    validate_text_patch_request,
)


def _replacement_end(start: NativePosition, text: str) -> NativePosition:
    return NativePosition(
        start.list_id,
        start.paragraph,
        start.character + len(text.encode("utf-16-le")) // 2,
    )


def _inverse_format_command(
    command: ParameterActionCommand,
    receipt: TextPatchPreparedReceipt,
    target_index: int,
) -> ParameterActionCommand:
    target = receipt.targets[target_index]
    if command.action == "CharShape":
        values: list[NativeSetter] = []
        for setter in command.setters:
            if setter.path == "Bold":
                value = BooleanValue(target.character_format.bold)
            elif setter.path == "Height":
                value = IntegerValue(target.character_format.height_hwpunit)
            elif setter.path == "TextColor":
                value = IntegerValue(target.character_format.text_color)
            elif setter.path.startswith("FaceName"):
                value = TextValue(target.character_format.face_name)
            elif setter.path.startswith("FontType"):
                value = IntegerValue(1)
            else:
                raise HwpLiveError(
                    f"prepared text.patch inverse가 {setter.path} 서식을 캡처하지 못했습니다"
                )
            values.append(NativeSetter(setter.path, value))
        return replace(command, setters=tuple(values))
    if command.action == "ParagraphShape":
        restored = paragraph_command(
            ParagraphFormatting(
                "inherit",
                align_type_raw=(
                    target.alignment
                    if any(item.path == "AlignType" for item in command.setters)
                    else None
                ),
                line_spacing=(
                    target.line_spacing
                    if any(item.path == "LineSpacing" for item in command.setters)
                    else None
                ),
            )
        )
        if restored is None:
            raise HwpLiveError("prepared paragraph inverse가 비어 있습니다")
        return restored
    raise HwpLiveError(
        f"prepared text.patch inverse가 {command.action}을 지원하지 않습니다"
    )


def _prepared_command_inverses(
    requests: tuple[TextPatchRequest, ...],
    receipt: TextPatchPreparedReceipt,
) -> tuple[
    tuple[NativeActionCommand, ...], tuple[tuple[NativeActionCommand, ...], ...]
]:
    canonical = canonical_prepared_text_patch_requests(requests)
    if canonical is None:
        raise HwpLiveError(
            "semantic text.patch target에는 logical inverse를 만들 수 없습니다"
        )
    effective = effective_prepared_text_patch_requests(requests, receipt.targets)
    forward = compile_text_patch_batch(effective).commands
    inverse_by_forward: list[tuple[NativeActionCommand, ...]] = []
    target_index = -1
    active_request: TextPatchRequest | None = None
    for command in forward:
        if isinstance(command, TextPatchCommand):
            target_index += 1
            active_request = effective[target_index]
            state = receipt.targets[target_index] if receipt.targets else None
            start = (
                state.selection.start
                if state is not None
                else active_request.target.start
            )
            if start is None:
                raise HwpLiveError("prepared text.patch inverse 시작 위치가 없습니다")
            old_text = (
                active_request.expected_text
                if active_request.formatting is not None
                and active_request.expected_text == active_request.replacement
                else state.text
                if state is not None
                else active_request.expected_text
            )
            if old_text is None:
                raise HwpLiveError("prepared text.patch inverse 원문이 없습니다")
            if active_request.replacement:
                inverse = TextPatchCommand(
                    "range",
                    active_request.replacement,
                    old_text,
                    start=start,
                    end=_replacement_end(start, active_request.replacement),
                )
                inverse_by_forward.append((inverse,))
            else:
                inverse_by_forward.append(
                    (
                        MovePositionCommand(start),
                        TextPatchCommand(
                            "current",
                            None,
                            old_text,
                        ),
                    )
                )
            continue
        if not isinstance(command, ParameterActionCommand) or active_request is None:
            raise HwpLiveError("prepared text.patch command 순서가 올바르지 않습니다")
        if not receipt.targets:
            raise HwpLiveError("prepared text.patch formatting inverse가 없습니다")
        inverse_by_forward.append(
            (_inverse_format_command(command, receipt, target_index),)
        )
    return forward, tuple(inverse_by_forward)


def _completed_prepared_inverse(
    inverse_by_forward: tuple[tuple[NativeActionCommand, ...], ...],
    completed: int,
) -> tuple[NativeActionCommand, ...]:
    """Bind each completed target's format inverses to its restored text selection."""
    target_groups: list[list[tuple[NativeActionCommand, ...]]] = []
    for inverse_group in inverse_by_forward[:completed]:
        if any(isinstance(command, TextPatchCommand) for command in inverse_group):
            target_groups.append([inverse_group])
        elif target_groups:
            target_groups[-1].append(inverse_group)
        else:
            raise HwpLiveError(
                "prepared text.patch inverse target 순서가 올바르지 않습니다"
            )
    return tuple(
        command
        for target_group in reversed(target_groups)
        for inverse_group in (target_group[0], *reversed(target_group[1:]))
        for command in inverse_group
    )


def _restored_prepared_failure(error: Exception) -> HwpLiveError:
    if isinstance(error, NativeActionFailure):
        return NativeActionFailure(
            NativeActionFailureEvidence(
                error.code,
                error.location,
                error.message,
                0,
                error.failed_step,
                False,
                True,
                None,
                None,
            )
        )
    if isinstance(error, HwpLiveError):
        error.mutation_started = False
        error.safe_to_repeat = True
        return error
    return HwpLiveError(
        str(error),
        mutation_started=False,
        safe_to_repeat=True,
    )


def _completed_forward_commands(error: Exception, total: int) -> int | None:
    """역패치를 몇 개까지 되돌려야 하는지에 대한 *확인 가능한* 근거.

    None 은 "모른다"이고, 그때는 되돌리지 않는다. 예전에는 여기서 모르는 경우를
    전량 실행으로 가정했는데(``completed = len(inverse_by_forward)``), 그
    가정이 틀리면 실행된 적 없는 명령의 역패치까지 문서에 쏘게 된다 — 되돌리기가
    아니라 새 편집이다.
    """
    if isinstance(error, NativeActionFailure):
        # 네이티브가 직접 센 수다. commands_completed 는 실패한 명령을 빼고
        # 세므로, 그 명령이 사후 검사에서 멈추기 전에 이미 문서를 바꿨다면
        # (partial_mutation) 그 하나의 역패치도 필요하다.
        completed = error.commands_completed + (1 if error.partial_mutation else 0)
        return min(max(completed, 0), total)
    if isinstance(error, HwpLiveError):
        if error.mutation_started is False:
            return 0
        if isinstance(error, TextPatchBatchVerificationError):
            # 네이티브 실행 자체는 끝났고 그 뒤의 판독·검증에서 실패한 경우다.
            # patch_validated_text_batch 가 네이티브 결과의 실행 명령 수를
            # 실패에 실어 보낸다.
            return min(max(error.native_commands_completed, 0), total)
    return None


def _rollback_prepared_text_patch_batch(
    candidate: HwpDocumentCandidate,
    forward: tuple[NativeActionCommand, ...],
    inverse_by_forward: tuple[tuple[NativeActionCommand, ...], ...],
    error: Exception,
    before_signature: str,
    receipt: TextPatchPreparedReceipt,
    *,
    completed_commands: int | None = None,
) -> bool:
    completed = (
        _completed_forward_commands(error, len(inverse_by_forward))
        if completed_commands is None
        else min(max(completed_commands, 0), len(inverse_by_forward))
    )
    if completed is None:
        raise HwpLiveError(
            "prepared text.patch 실패가 몇 번째 명령까지 실행됐는지 말해 주지 않아"
            + " 역패치를 만들지 못했습니다; 전량 실행을 가정하고 되돌리는 대신"
            + " 문서를 네이티브가 멈춘 자리에 그대로 두었습니다"
            + f"; 원인: {error}",
            mutation_started=True,
            safe_to_repeat=False,
        ) from error
    inverse = _completed_prepared_inverse(inverse_by_forward, completed)
    if not inverse:
        return False
    try:
        restored = execute_native_actions(
            candidate.window_handle,
            NativeActionRequest(candidate.document_id, candidate.full_name, inverse),
            minimum_version=14,
        )
        restored_signature = (
            read_native_content_signature(candidate.window_handle) or ""
        )
        readback = preflight_native_text_patches(
            candidate.window_handle,
            NativeActionRequest(candidate.document_id, candidate.full_name, forward),
        )
    except Exception as rollback_error:  # noqa: BLE001 - restoration is uncertain
        raise HwpLiveError(
            "prepared text.patch inverse rollback 실행 또는 검증에 실패했습니다",
            mutation_started=True,
            safe_to_repeat=False,
        ) from rollback_error
    exact_targets_restored = (
        readback is not None and readback.targets == receipt.targets
    )
    # 지문 대조는 체크포인트가 쓰는 그 규약(checkpoint_signatures_match)을 그대로
    # 쓴다. 원시 문자열 비교는 같은 문서를 다른 세대의 서명 형식으로 읽었을 때
    # 거짓 불일치를 내고, "빈 지문이면 검사 생략"은 대조를 못 한 것을 통과로
    # 바꿔 놓았다. 못 한 것은 못 했다고 말한다.
    signature_confirmed = checkpoint_signatures_match(
        before_signature, restored_signature
    )
    if restored is None or not exact_targets_restored or not signature_confirmed:
        raise HwpLiveError(
            "prepared text.patch inverse rollback 뒤 편집 전 상태를 확인하지 못했습니다"
            + f"; 역패치 실행={'보고됨' if restored is not None else '보고 없음'}"
            + f"; 대상 원문·서식 복원={'확인' if exact_targets_restored else '미확인'}"
            + "; 문서 지문 대조="
            + _signature_verdict(before_signature, restored_signature),
            mutation_started=True,
            safe_to_repeat=False,
        ) from error
    return True


def _signature_verdict(before_signature: str, restored_signature: str) -> str:
    if not before_signature or not restored_signature:
        return "지문을 읽지 못해 대조 불가"
    return (
        "일치"
        if checkpoint_signatures_match(before_signature, restored_signature)
        else "불일치"
    )


def record_prepared_text_patch_history(
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    result: TextPatchResult,
    forward: tuple[NativeActionCommand, ...],
    inverse_by_forward: tuple[tuple[NativeActionCommand, ...], ...],
    before_signature: str,
) -> None:
    after_signature = read_native_content_signature(candidate.window_handle) or ""
    inverse = _completed_prepared_inverse(inverse_by_forward, len(inverse_by_forward))
    history.record(
        LogicalTextPatchBatchHistoryEntry(
            candidate.document_id,
            candidate.full_name,
            "text.patch",
            forward,
            inverse,
            before_signature,
            after_signature,
            result.before.page_count,
            result.after.page_count,
            result.after.current_page,
            (result.after.current_page,),
        )
    )


def _execute_prepared_text_patch_batch(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    requests: tuple[TextPatchRequest, ...],
    unsafe_selectors: set[str],
    guard: Callable[[], None],
    receipt: TextPatchPreparedReceipt,
) -> TextPatchResult:
    before_signature = read_native_content_signature(candidate.window_handle) or ""
    forward, inverse_by_forward = _prepared_command_inverses(requests, receipt)
    try:
        result = patch_validated_text_batch(
            hwp, candidate, requests, unsafe_selectors, guard, receipt
        )
    except Exception as error:  # noqa: BLE001 - exact inverse transaction boundary
        if not (isinstance(error, HwpLiveError) and error.mutation_started is False):
            restored = _rollback_prepared_text_patch_batch(
                candidate,
                forward,
                inverse_by_forward,
                error,
                before_signature,
                receipt,
            )
            if restored:
                raise _restored_prepared_failure(error) from error
        raise
    try:
        record_prepared_text_patch_history(
            candidate, history, result, forward, inverse_by_forward, before_signature
        )
    except Exception as error:  # noqa: BLE001 - history is transactional here
        # 여기 오는 실패는 편집이 *끝난 뒤* 이력 기록에서 난 것이다. 전량 실행은
        # 가정이 아니라 관측이다 — patch_validated_text_batch 가 result 를
        # 돌려주었고, 그 안의 네이티브 결과가 모든 명령의 실행을 보고했다.
        restored = _rollback_prepared_text_patch_batch(
            candidate,
            forward,
            inverse_by_forward,
            error,
            before_signature,
            receipt,
            completed_commands=len(inverse_by_forward),
        )
        if restored:
            raise _restored_prepared_failure(error) from error
        raise
    return result


# The checkpointed path answers a mid-run failure with "the copy was restored"
# or "restoring it failed". This path has no copy at all, so neither sentence is
# available and the difference matters to whoever reads the failure: the document
# is left wherever the native run stopped and nothing here will move it back.
_UNRECORDED_FAILURE_CLAUSE: Final = (
    "체크포인트 없이 실행하다 실패했고, 되돌린 것은 없습니다"
)


def _unrecorded_failure(
    error: HwpLiveError,
    notice: str,
    timings: tuple[TextPatchPhaseTiming, ...],
    total_started: int,
) -> None:
    """Carry the missing-checkpoint notice and the phase timings out on the error.

    Without this the failure arrives as a bare HwpLiveError with empty
    `phase_timings`, which is exactly the shape a failed checkpoint rollback
    has - the reader cannot tell the two apart, and the one fact that separates
    them (no checkpoint was ever taken, so no rollback ran) is the one the
    reader needs.
    """
    error.reason = "; ".join(
        part for part in (error.reason, _UNRECORDED_FAILURE_CLAUSE, notice) if part
    )
    error.args = (error.reason,)
    error.phase_timings = (
        *(
            (timing.phase, timing.source, timing.elapsed_microseconds)
            for timing in timings
        ),
        ("total", "python", (perf_counter_ns() - total_started) // 1_000),
    )


def _unrecorded_text_patch_batch(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    requests: tuple[TextPatchRequest, ...],
    unsafe_selectors: set[str],
    guard: Callable[[], None],
    receipt: TextPatchPreparedReceipt | None,
    notice: str,
    timings: tuple[TextPatchPhaseTiming, ...],
    total_started: int,
) -> TextPatchResult:
    """Patch the batch without an MCP undo entry, saying why it has none."""
    try:
        result = patch_validated_text_batch(
            hwp,
            candidate,
            requests,
            unsafe_selectors,
            guard,
            receipt,
        )
    except HwpLiveError as error:
        _unrecorded_failure(error, notice, timings, total_started)
        raise
    return replace(
        result,
        notice=notice,
        phase_timings=(
            *timings,
            *result.phase_timings,
            TextPatchPhaseTiming(
                "total", "python", (perf_counter_ns() - total_started) // 1_000
            ),
        ),
    )


def execute_managed_text_patch_batch(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    history: LiveEditHistoryStore,
    requests: tuple[TextPatchRequest, ...],
    unsafe_selectors: set[str],
    guard: Callable[[], None],
    plan_guard: TextPatchPlanGuard | None = None,
) -> TextPatchResult:
    """Run one checkpoint lifecycle around one native text patch batch."""
    total_started = perf_counter_ns()
    validation_started = perf_counter_ns()
    try:
        has_exact_omission = any(
            request.expected_text is None
            and request.target.kind in {"range", "table_cell"}
            for request in requests
        )
        if has_exact_omission and plan_guard is None:
            raise HwpLiveError(
                "expected_text omission requires all text.patch batch plan guards",
                mutation_started=False,
                safe_to_repeat=True,
            )
        if plan_guard is not None:
            validate_text_patch_plan_guard(plan_guard)
        allow_exact_omission = plan_guard is not None
        for request in requests:
            validate_text_patch_request(
                request,
                allow_exact_omission=allow_exact_omission,
                require_current_expected=True,
            )
        _ = compile_text_patch_batch(requests)
    except HwpLiveError as error:
        validation_elapsed = (perf_counter_ns() - validation_started) // 1_000
        error.phase_timings = (
            ("validation", "python", validation_elapsed),
            ("total", "python", (perf_counter_ns() - total_started) // 1_000),
        )
        raise
    validation_elapsed = (perf_counter_ns() - validation_started) // 1_000
    preflight_started = perf_counter_ns()
    try:
        receipt = preflight_text_patch_batch(candidate, requests, plan_guard)
    except HwpLiveError as error:
        preflight_elapsed = (perf_counter_ns() - preflight_started) // 1_000
        error.phase_timings = (
            ("validation", "python", validation_elapsed),
            ("planning_preflight", "python", preflight_elapsed),
            ("total", "python", (perf_counter_ns() - total_started) // 1_000),
        )
        raise
    preflight_elapsed = (perf_counter_ns() - preflight_started) // 1_000
    if (
        receipt is not None
        and canonical_prepared_text_patch_requests(requests) is not None
    ):
        prepared_result = _execute_prepared_text_patch_batch(
            hwp,
            candidate,
            history,
            requests,
            unsafe_selectors,
            guard,
            receipt,
        )
        return replace(
            prepared_result,
            phase_timings=(
                TextPatchPhaseTiming("validation", "python", validation_elapsed),
                TextPatchPhaseTiming("planning_preflight", "python", preflight_elapsed),
                TextPatchPhaseTiming(
                    "planning_preflight",
                    "native",
                    receipt.native_elapsed_microseconds,
                ),
                *prepared_result.phase_timings,
                TextPatchPhaseTiming(
                    "total", "python", (perf_counter_ns() - total_started) // 1_000
                ),
            ),
        )
    unavailable = document_checkpoint_unavailable_reason(candidate)
    if unavailable:
        # No checkpoint may be taken: either two copies will not fit the
        # session's disk history, or 한/글 has already proved it cannot sign this
        # document. Refusing here would cost the user the whole batch to protect
        # an undo entry they were never going to get, so patch and say the entry
        # is missing -- a later hwp_undo falls through to 한/글's own history.
        return _unrecorded_text_patch_batch(
            hwp,
            candidate,
            requests,
            unsafe_selectors,
            guard,
            receipt,
            unavailable,
            (
                TextPatchPhaseTiming("validation", "python", validation_elapsed),
                TextPatchPhaseTiming("planning_preflight", "python", preflight_elapsed),
                *(
                    ()
                    if receipt is None
                    else (
                        TextPatchPhaseTiming(
                            "planning_preflight",
                            "native",
                            receipt.native_elapsed_microseconds,
                        ),
                    )
                ),
            ),
            total_started,
        )
    checkpoint_started = perf_counter_ns()
    # A broken document identity leaves here as DocumentIdentityError and stops
    # the call before the edit: it is the one failure where going ahead would
    # write the user's work into a file that no longer exists.
    prepared = _prepare_text_patch_checkpoint(candidate, history)
    checkpoint_before_elapsed = (perf_counter_ns() - checkpoint_started) // 1_000
    if isinstance(prepared, str):
        # The document could not be checkpointed - the classic case is a
        # picture-heavy document the bridge could not copy. Patch and report the
        # missing undo entry in the words the preparation step chose: it is the
        # only place that saw the reason.
        return _unrecorded_text_patch_batch(
            hwp,
            candidate,
            requests,
            unsafe_selectors,
            guard,
            receipt,
            prepared,
            (
                TextPatchPhaseTiming("validation", "python", validation_elapsed),
                TextPatchPhaseTiming("planning_preflight", "python", preflight_elapsed),
                *(
                    ()
                    if receipt is None
                    else (
                        TextPatchPhaseTiming(
                            "planning_preflight",
                            "native",
                            receipt.native_elapsed_microseconds,
                        ),
                    )
                ),
                TextPatchPhaseTiming(
                    "checkpoint_before", "python", checkpoint_before_elapsed
                ),
            ),
            total_started,
        )
    mutation_started = perf_counter_ns()
    try:
        result = patch_validated_text_batch(
            hwp,
            candidate,
            requests,
            unsafe_selectors,
            guard,
            receipt,
        )
    except Exception as error:  # noqa: BLE001 - transaction rollback boundary
        mutation_elapsed = (perf_counter_ns() - mutation_started) // 1_000
        if isinstance(error, HwpLiveError) and error.mutation_started is False:
            cleanup_started = perf_counter_ns()
            _remove_path(prepared.before.path)
            _remove_path(prepared.after_path)
            cleanup_elapsed = (perf_counter_ns() - cleanup_started) // 1_000
            error.phase_timings = (
                ("validation", "python", validation_elapsed),
                ("planning_preflight", "python", preflight_elapsed),
                ("checkpoint_before", "python", checkpoint_before_elapsed),
                ("mutation", "python", mutation_elapsed),
                ("checkpoint_after_history", "python", cleanup_elapsed),
                ("total", "python", (perf_counter_ns() - total_started) // 1_000),
            )
            raise
        rollback_started = perf_counter_ns()
        _restore_prepared_before_failure(candidate, prepared, error)
        rollback_elapsed = (perf_counter_ns() - rollback_started) // 1_000
        if isinstance(error, HwpLiveError):
            error.phase_timings = (
                ("validation", "python", validation_elapsed),
                ("planning_preflight", "python", preflight_elapsed),
                ("checkpoint_before", "python", checkpoint_before_elapsed),
                ("mutation", "python", mutation_elapsed),
                ("rollback", "python", rollback_elapsed),
                ("total", "python", (perf_counter_ns() - total_started) // 1_000),
            )
        raise
    checkpoint_after_started = perf_counter_ns()
    try:
        _, _elapsed, after_evidence = _record_completed_edit(
            candidate,
            history,
            prepared,
            require_complete_signatures=True,
        )
    except (HwpLiveError, OSError, ValueError) as error:
        _remove_path(prepared.before.path)
        _remove_path(prepared.after_path)
        checkpoint_after_elapsed = (
            perf_counter_ns() - checkpoint_after_started
        ) // 1_000
        broken = _broken_document_identity(candidate, candidate.full_name, error)
        return replace(
            result,
            notice=(
                _identity_notice_after_applied_edit(broken)
                if broken is not None
                else (_CHECKPOINT_NOT_RECORDED_NOTICE + _capture_failure_clause(error))
            ),
            checkpoint_evidence=prepared.checkpoint_evidence,
            phase_timings=(
                TextPatchPhaseTiming("validation", "python", validation_elapsed),
                TextPatchPhaseTiming("planning_preflight", "python", preflight_elapsed),
                *(
                    ()
                    if receipt is None
                    else (
                        TextPatchPhaseTiming(
                            "planning_preflight",
                            "native",
                            receipt.native_elapsed_microseconds,
                        ),
                    )
                ),
                TextPatchPhaseTiming(
                    "checkpoint_before", "python", checkpoint_before_elapsed
                ),
                *result.phase_timings,
                TextPatchPhaseTiming(
                    "checkpoint_after_history",
                    "python",
                    checkpoint_after_elapsed,
                ),
                TextPatchPhaseTiming(
                    "total",
                    "python",
                    (perf_counter_ns() - total_started) // 1_000,
                ),
            ),
        )
    checkpoint_after_elapsed = (perf_counter_ns() - checkpoint_after_started) // 1_000
    timings = (
        TextPatchPhaseTiming("validation", "python", validation_elapsed),
        TextPatchPhaseTiming(
            "planning_preflight",
            "python",
            preflight_elapsed,
        ),
        *(
            ()
            if receipt is None
            else (
                TextPatchPhaseTiming(
                    "planning_preflight",
                    "native",
                    receipt.native_elapsed_microseconds,
                ),
            )
        ),
        TextPatchPhaseTiming(
            "checkpoint_before",
            "python",
            checkpoint_before_elapsed,
        ),
        *result.phase_timings,
        TextPatchPhaseTiming(
            "checkpoint_after_history",
            "python",
            checkpoint_after_elapsed,
        ),
        TextPatchPhaseTiming(
            "total",
            "python",
            (perf_counter_ns() - total_started) // 1_000,
        ),
    )
    result = replace(result, phase_timings=timings)
    evidence = prepared.checkpoint_evidence.merged(after_evidence)
    if evidence == NO_CHECKPOINT_EVIDENCE:
        return result
    if evidence.unavailable_reason:
        return replace(
            result,
            checkpoint_evidence=evidence,
            notice=result.notice
            or (
                "편집을 적용하고 MCP 되돌리기 기록도 남겼습니다. 다만 문서 사본 방식 "
                + "체크포인트를 쓰지 못해 예전 방식으로 떴습니다."
                + _capture_reason_clause(evidence.unavailable_reason)
            ),
        )
    return replace(result, checkpoint_evidence=evidence)

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication, SelectionRange
from hwp_live_contract import (
    LayoutPlan,
    LayoutResult,
    MutationResult,
    SelectionPosition,
)
from hwp_live_inspection import inspect_styles
from hwp_live_layout import resolve_layout_styles, validate_layout_anchor
from hwp_live_native_action_contract import (
    NativeActionFailure,
    NativeActionFailureEvidence,
    encode_action_request,
)
from hwp_live_native_action_models import (
    NativeActionRequest,
    NativeActionResult,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
    ReplaceSelectionCommand,
    TextPatchCommand,
)
from hwp_live_native_batch import execute_native_actions, read_native_snapshot
from hwp_live_native_format_commands import (
    TextFormatCommandPlan,
    build_native_format_commands,
)
from hwp_live_native_layout import (
    NativeLayoutContext,
    build_native_layout_execution_plan,
    build_native_layout_request,
)
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_safety import require_writable_document, run_layout_mutation
from hwp_live_text_format_verification import verify_requested_text_format
from hwp_live_text_patch_contract import TextPatchRequest, TextPatchResult


@dataclass(slots=True)
class _NativeLayoutBox:
    result: NativeActionResult | None = None
    snapshot: NativeSnapshot | None = None


@dataclass(slots=True)
class _NativeMutationBox:
    result: NativeActionResult | None = None
    snapshot: NativeSnapshot | None = None


class _LegacyLayoutBatchFailure(NativeActionFailure):
    completed_batches: int
    completed_addresses: tuple[str, ...]

    def __init__(
        self,
        evidence: NativeActionFailureEvidence,
        *,
        completed_batches: int,
        completed_addresses: tuple[str, ...],
    ) -> None:
        super().__init__(evidence)
        self.completed_batches = completed_batches
        self.completed_addresses = completed_addresses


def _merge_layout_results(
    first: NativeActionResult,
    second: NativeActionResult,
) -> NativeActionResult:
    return replace(
        first,
        commands_executed=first.commands_executed + second.commands_executed,
        actions_executed=first.actions_executed + second.actions_executed,
        text_insertions=first.text_insertions + second.text_insertions,
        image_insertions=first.image_insertions + second.image_insertions,
        elapsed_microseconds=(first.elapsed_microseconds + second.elapsed_microseconds),
        created_control_ids=(first.created_control_ids + second.created_control_ids),
        call_results=first.call_results + second.call_results,
        image_timing_count=(first.image_timing_count + second.image_timing_count),
        image_max_microseconds=max(
            first.image_max_microseconds,
            second.image_max_microseconds,
        ),
        image_total_microseconds=(
            first.image_total_microseconds + second.image_total_microseconds
        ),
    )


def _legacy_layout_failure(
    failure: NativeActionFailure,
    *,
    batch_index: int,
    commands_completed_before: int,
    command_limit: int,
    completed_addresses: tuple[str, ...],
) -> _LegacyLayoutBatchFailure:
    local_completed = min(max(failure.commands_completed, 0), command_limit)
    commands_completed = commands_completed_before + local_completed
    partial_mutation = batch_index > 0 or failure.partial_mutation
    return _LegacyLayoutBatchFailure(
        NativeActionFailureEvidence(
            code=failure.code,
            location=failure.location,
            message=(
                f"{failure.message}; 완료 batch {batch_index}개, "
                f"완료 셀 {len(completed_addresses)}개"
            ),
            commands_completed=commands_completed,
            failed_step=failure.failed_step,
            partial_mutation=partial_mutation,
            retry_safe=failure.retry_safe and not partial_mutation,
            structure_digest_before=failure.structure_digest_before,
            structure_digest_after=failure.structure_digest_after,
        ),
        completed_batches=batch_index,
        completed_addresses=completed_addresses,
    )


def _native_selection(selection: SelectionRange) -> NativeSelection:
    selected, slist, spara, spos, elist, epara, epos = selection

    def coordinate(value: int | None) -> int:
        return 0 if value is None else value

    return NativeSelection(
        selected,
        NativePosition(coordinate(slist), coordinate(spara), coordinate(spos)),
        NativePosition(coordinate(elist), coordinate(epara), coordinate(epos)),
    )


def _selection_range(expected: SelectionPosition) -> SelectionRange:
    if not expected.selected:
        raise HwpLiveError("수정할 선택 영역의 좌표가 없습니다")
    return (
        True,
        expected.start_list,
        expected.start_paragraph,
        expected.start_character,
        expected.end_list,
        expected.end_paragraph,
        expected.end_character,
    )


def replace_validated_selection(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    expected_selection: SelectionPosition,
    expected_text: str,
    replacement: str,
    unsafe_selectors: set[str],
    guard: Callable[[], None],
) -> MutationResult:
    _ = hwp
    if len(expected_text) > 1_000_000 or len(replacement) > 1_000_000:
        raise HwpLiveError("라이브 텍스트 수정 크기 한도를 초과했습니다")
    selection = _selection_range(expected_selection)
    if selection[1] != selection[4]:
        raise HwpLiveError("서로 다른 한컴 컨트롤을 가로지른 선택은 수정하지 않습니다")
    require_writable_document(unsafe_selectors, candidate.selector)
    guard()
    native = _NativeMutationBox()

    def mutate_native_selection() -> None:
        native.result = execute_native_actions(
            candidate.window_handle,
            NativeActionRequest(
                document_id=candidate.document_id,
                full_name=candidate.full_name,
                commands=(ReplaceSelectionCommand(expected_text, replacement),),
                expected_selection=_native_selection(selection),
            ),
            minimum_version=9,
        )
        if native.result is None:
            return
        native.snapshot = read_native_snapshot(candidate.window_handle)
        if native.snapshot is None:
            raise HwpLiveError("네이티브 선택 교체 후 한컴 문서 상태를 읽지 못했습니다")

    run_layout_mutation(
        unsafe_selectors,
        candidate.selector,
        mutate_native_selection,
    )
    guard()
    if native.result is None or native.snapshot is None:
        raise HwpLiveError("한컴 네이티브 선택 교체를 사용할 수 없습니다")
    return MutationResult(
        action="replace_selection",
        current_page=native.snapshot.current_page,
        modified=native.snapshot.modified,
    )


def patch_validated_text(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    request: TextPatchRequest,
    unsafe_selectors: set[str],
    guard: Callable[[], None],
) -> TextPatchResult:
    _ = hwp
    if (
        request.expected_text is not None and len(request.expected_text) > 1_000_000
    ) or len(request.replacement) > 1_000_000:
        raise HwpLiveError("라이브 text.patch 크기 한도를 초과했습니다")
    if request.target.kind != "current" and request.expected_text is None:
        raise HwpLiveError(
            "범위·검색·표 셀 text.patch에는 확인할 기존 텍스트가 필요합니다"
        )
    if not request.replacement and request.formatting is not None:
        raise HwpLiveError(
            "삭제 결과에는 적용할 텍스트가 없으므로 글자 서식을 함께 요청할 수 없습니다"
        )
    require_writable_document(unsafe_selectors, candidate.selector)
    guard()
    before = read_native_snapshot(candidate.window_handle)
    if before is None:
        raise HwpLiveError("text.patch 전 한컴 문서 상태를 읽지 못했습니다")
    target = request.target
    commands = (
        TextPatchCommand(
            target=target.kind,
            expected_text=request.expected_text,
            replacement=request.replacement,
            start=target.start,
            end=target.end,
            occurrence=target.occurrence,
            match_case=target.match_case,
            table_instance_id=target.table_instance_id,
            cell_address=target.cell_address,
        ),
        *(
            ()
            if request.formatting is None
            else build_native_format_commands(TextFormatCommandPlan(request.formatting))
        ),
    )
    native_result = execute_native_actions(
        candidate.window_handle,
        NativeActionRequest(
            document_id=candidate.document_id,
            full_name=candidate.full_name,
            commands=commands,
            expected_cursor=before.cursor,
            expected_selection=before.selection,
        ),
        minimum_version=11,
    )
    if native_result is None:
        raise HwpLiveError("프로토콜 11 네이티브 text.patch를 사용할 수 없습니다")
    after = read_native_snapshot(candidate.window_handle)
    if after is None:
        raise HwpLiveError("text.patch 후 한컴 문서 상태를 읽지 못했습니다")
    if request.replacement:
        if not after.selection.selected or after.selected_text != request.replacement:
            raise HwpLiveError(
                "text.patch 후 변경한 본문 범위를 다시 읽어 확인하지 못했습니다"
            )
    elif after.selection.selected:
        raise HwpLiveError("text.patch 삭제 후 선택 영역이 예상대로 접히지 않았습니다")
    if request.formatting is not None:
        verify_requested_text_format(request.formatting, after)
    guard()
    return TextPatchResult(native_result, before, after)


def apply_validated_layout(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    plan: LayoutPlan,
    assets: dict[Path, Path],
    unsafe_selectors: set[str],
    expected_cursor: tuple[int, int, int],
    expected_selected_text: str | None,
    guard: Callable[[], None],
) -> LayoutResult:
    require_writable_document(unsafe_selectors, candidate.selector)
    guard()
    cursor = hwp.get_pos()
    guard()
    if cursor != expected_cursor:
        raise HwpLiveError("현재 커서가 에이전트가 확인한 위치와 다릅니다")
    selected = hwp.get_selected_pos()
    guard()
    if plan.replace_selection:
        raise HwpLiveError(
            "선택 영역을 복합 레이아웃으로 교체하는 기능은 원문 삭제 위험 때문에 사용하지 않습니다"
        )
    if selected[0]:
        raise HwpLiveError("선택 영역이 있어 레이아웃 삽입을 중단했습니다")
    if expected_selected_text is not None:
        raise HwpLiveError("선택 영역이 없으므로 예상 선택 텍스트를 보내지 마세요")
    resolved_plan = resolve_layout_styles(hwp, plan, guard)
    if resolved_plan.target == "current":
        validate_layout_anchor(hwp, resolved_plan, guard)
    styles = inspect_styles(hwp, guard)
    position = NativePosition(*cursor)
    native_request = build_native_layout_request(
        NativeLayoutContext(
            document_id=candidate.document_id,
            full_name=candidate.full_name,
            style_ids=tuple((style.name, style.style_id) for style in styles.styles),
            expected_cursor=position,
            expected_selection=_native_selection(selected),
        ),
        resolved_plan,
        assets,
    )
    _ = encode_action_request(native_request)
    execution = build_native_layout_execution_plan(native_request)
    native = _NativeLayoutBox()

    def mutate_native_layout() -> None:
        commands_completed = 0
        completed_group_keys: set[int] = set()
        for batch_index, batch in enumerate(execution.batches):
            try:
                batch_result = execute_native_actions(
                    candidate.window_handle,
                    batch.request,
                    minimum_version=4,
                )
            except NativeActionFailure as failure:
                if len(execution.batches) == 1:
                    raise
                local_completed = min(
                    max(failure.commands_completed, 0),
                    len(batch.request.commands),
                )
                completed_group_keys.update(batch.completed_group_keys(local_completed))
                raise _legacy_layout_failure(
                    failure,
                    batch_index=batch_index,
                    commands_completed_before=commands_completed,
                    command_limit=len(batch.request.commands),
                    completed_addresses=execution.completed_addresses(
                        completed_group_keys
                    ),
                ) from failure
            except HwpLiveError as error:
                if len(execution.batches) == 1 or batch_index == 0:
                    raise
                completed_addresses = execution.completed_addresses(
                    completed_group_keys
                )
                raise _LegacyLayoutBatchFailure(
                    NativeActionFailureEvidence(
                        code="LAYOUT_BATCH_TRANSPORT",
                        location=f"{batch_index + 1}/{len(execution.batches)}",
                        message=(
                            f"{error}; 완료 batch {batch_index}개, "
                            f"완료 셀 {len(completed_addresses)}개"
                        ),
                        commands_completed=commands_completed,
                        failed_step=f"layout_batch:{batch_index + 1}",
                        partial_mutation=True,
                        retry_safe=False,
                        structure_digest_before=None,
                        structure_digest_after=None,
                    ),
                    completed_batches=batch_index,
                    completed_addresses=completed_addresses,
                ) from error
            if batch_result is None:
                if len(execution.batches) == 1:
                    return
                partial_mutation = batch_index > 0
                completed_addresses = execution.completed_addresses(
                    completed_group_keys
                )
                raise _LegacyLayoutBatchFailure(
                    NativeActionFailureEvidence(
                        code="LAYOUT_BATCH_UNAVAILABLE",
                        location=f"{batch_index + 1}/{len(execution.batches)}",
                        message=(
                            "한컴 네이티브 레이아웃 배치를 사용할 수 없습니다; "
                            f"완료 batch {batch_index}개, "
                            f"완료 셀 {len(completed_addresses)}개"
                        ),
                        commands_completed=commands_completed,
                        failed_step=f"layout_batch:{batch_index + 1}",
                        partial_mutation=partial_mutation,
                        retry_safe=not partial_mutation,
                        structure_digest_before=None,
                        structure_digest_after=None,
                    ),
                    completed_batches=batch_index,
                    completed_addresses=completed_addresses,
                )
            if batch_result.commands_executed != len(batch.request.commands):
                local_completed = min(
                    max(batch_result.commands_executed, 0),
                    len(batch.request.commands),
                )
                completed_group_keys.update(batch.completed_group_keys(local_completed))
                completed_addresses = execution.completed_addresses(
                    completed_group_keys
                )
                partial_mutation = batch_index > 0 or local_completed > 0
                raise _LegacyLayoutBatchFailure(
                    NativeActionFailureEvidence(
                        code="LAYOUT_BATCH_INCOMPLETE",
                        location=f"{batch_index + 1}/{len(execution.batches)}",
                        message=(
                            "레이아웃 batch 완료 명령 수가 요청과 다릅니다; "
                            f"완료 batch {batch_index}개, "
                            f"완료 셀 {len(completed_addresses)}개"
                        ),
                        commands_completed=commands_completed + local_completed,
                        failed_step=f"layout_batch:{batch_index + 1}",
                        partial_mutation=partial_mutation,
                        retry_safe=not partial_mutation,
                        structure_digest_before=None,
                        structure_digest_after=None,
                    ),
                    completed_batches=batch_index,
                    completed_addresses=completed_addresses,
                )
            native.result = (
                batch_result
                if native.result is None
                else _merge_layout_results(native.result, batch_result)
            )
            commands_completed += batch_result.commands_executed
            completed_group_keys.update(item.group.key for item in batch.groups)
        native.snapshot = read_native_snapshot(candidate.window_handle)
        if native.snapshot is None:
            raise HwpLiveError(
                "네이티브 레이아웃 배치 후 한컴 문서 상태를 읽지 못했습니다"
            )

    run_layout_mutation(
        unsafe_selectors,
        candidate.selector,
        mutate_native_layout,
    )
    if native.result is None or native.snapshot is None:
        raise HwpLiveError("한컴 네이티브 레이아웃 배치를 사용할 수 없습니다")
    return LayoutResult(
        blocks_applied=len(plan.blocks),
        current_page=native.snapshot.current_page,
        modified=native.snapshot.modified,
        created_control_ids=native.result.created_control_ids,
        native_elapsed_microseconds=native.result.elapsed_microseconds,
    )

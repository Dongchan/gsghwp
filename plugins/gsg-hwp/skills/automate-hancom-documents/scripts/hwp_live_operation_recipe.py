from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace

from hwp_document_style_profile import resolve_layout_style_profile
from hwp_errors import HwpLiveError
from hwp_layout_preflight import preflight_layout
from hwp_live_api import ShapeValue
from hwp_live_caption import (
    caption_format_sources,
    find_hierarchical_table_caption_source,
)
from hwp_live_contract import DocumentStyleList, LayoutPlan, PageBreakBlock
from hwp_live_document_edit_commands import build_delete_page_commands
from hwp_live_inspection import inspect_styles
from hwp_live_layout import prepare_layout_assets
from hwp_live_native_action_contract import (
    NativeActionFailure,
    NativeActionFailureEvidence,
    encode_action_request,
)
from hwp_live_native_action_models import NativePosition
from hwp_live_native_action_results import NativeActionResult, NativeSnapshot
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_page,
    read_native_snapshot,
)
from hwp_live_native_layout import (
    NativeLayoutContext,
    NativeLayoutExecutionPlan,
    build_native_layout_execution_plan,
    build_native_layout_request,
)
from hwp_live_operation_recipe_contract import (
    is_document_end_layout_intent as is_document_end_layout_intent,
    layout_operation_result,
)
from hwp_live_rot import HwpDocumentCandidate, attach_wrapper, release_wrapper
from hwp_live_safety import (
    mark_document_unsafe,
    require_writable_document,
    run_layout_mutation,
)
from hwp_operation_contract import (
    OperationResult,
    OperationStatus,
    page_growth_evidence,
)
from hwp_reference_layout_geometry import (
    MILLIMETERS_PER_INCH,
    HWPUNITS_PER_INCH,
    SectionPageGeometry,
)
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_patch import ReferenceLayoutPatchBlock
from hwp_live_table_contract import TableBlock


class _LayoutBatchFailure(HwpLiveError):
    error: HwpLiveError
    batch_index: int
    completed_batches: int
    commands_completed: int
    elapsed_microseconds: int
    completed_group_keys: frozenset[int]

    def __init__(
        self,
        error: HwpLiveError,
        *,
        batch_index: int,
        completed_batches: int,
        commands_completed: int,
        elapsed_microseconds: int,
        completed_group_keys: set[int],
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.batch_index = batch_index
        self.completed_batches = completed_batches
        self.commands_completed = commands_completed
        self.elapsed_microseconds = elapsed_microseconds
        self.completed_group_keys = frozenset(completed_group_keys)


def _merge_native_results(
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


def _layout_batch_failure_result(
    query: str,
    plan: LayoutPlan,
    execution: NativeLayoutExecutionPlan,
    before: NativeSnapshot,
    native_protocol: int,
    lookup_microseconds: int,
    failure: _LayoutBatchFailure,
) -> OperationResult:
    error = failure.error
    partial_mutation: bool | None = (
        True
        if failure.completed_batches > 0
        else error.partial_mutation
        if isinstance(error, NativeActionFailure)
        else None
    )
    retry_safe = (
        isinstance(error, NativeActionFailure)
        and error.retry_safe
        and partial_mutation is False
    )
    completed_addresses = execution.completed_addresses(
        set(failure.completed_group_keys)
    )
    failed_step = (
        error.failed_step
        if isinstance(error, NativeActionFailure)
        else f"layout_batch:{failure.batch_index + 1}"
    )
    base = layout_operation_result(
        query,
        "partial_change" if partial_mutation is True else "operation_failed",
        (
            f"레이아웃 호출 {failure.batch_index + 1}/"
            f"{len(execution.batches)}에서 실패했습니다. "
            f"완료 batch {failure.completed_batches}개, "
            f"완료 셀 {len(completed_addresses)}개: {error}"
        ),
        lookup_microseconds,
        plan,
    )
    return base.model_copy(
        update={
            "changed": partial_mutation is True,
            "execution_mode": "native_in_process",
            "native_protocol": native_protocol,
            "verification": "native_action_result",
            "verified": False,
            "commands_executed": failure.commands_completed,
            "commands_completed": failure.commands_completed,
            "native_elapsed_microseconds": failure.elapsed_microseconds,
            "current_page": before.current_page,
            "page_count": before.page_count,
            "modified": (
                True
                if partial_mutation is True
                else before.modified
                if partial_mutation is False
                else None
            ),
            "partial_change": partial_mutation is True,
            "partial_mutation": partial_mutation,
            "retry_safe": retry_safe,
            "reconcile_required": partial_mutation is not False or not retry_safe,
            "failed_step": failed_step,
            "structure_digest_before": (
                error.structure_digest_before
                if isinstance(error, NativeActionFailure)
                else None
            ),
            "structure_digest_after": (
                error.structure_digest_after
                if isinstance(error, NativeActionFailure)
                else None
            ),
            "updated_addresses": completed_addresses,
        }
    )


def _page_value(values: Mapping[str, ShapeValue], key: str) -> float:
    value = values.get(key)
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def reference_append_page_count(
    plan: LayoutPlan,
    current_page_count: int,
) -> int:
    reference_blocks = tuple(
        block for block in plan.blocks if isinstance(block, ReferenceLayoutBlock)
    )
    if (
        plan.target != "after_page"
        or plan.page != current_page_count
        or not reference_blocks
        or len(plan.blocks) != len(reference_blocks) * 2 - 1
        or not all(
            isinstance(block, ReferenceLayoutBlock | PageBreakBlock)
            for block in plan.blocks
        )
        or sum(isinstance(block, PageBreakBlock) for block in plan.blocks)
        != len(reference_blocks) - 1
    ):
        return 0
    return len(reference_blocks)


def native_style_plan(
    candidate: HwpDocumentCandidate,
    plan: LayoutPlan,
    style_id: int,
    guard: Callable[[], None],
    *,
    setup_page: int,
    style_list: DocumentStyleList | None = None,
) -> tuple[
    LayoutPlan,
    SectionPageGeometry,
    tuple[tuple[str, NativePosition], ...],
]:
    wrapper = attach_wrapper(candidate)
    try:
        styles = (
            inspect_styles(wrapper, guard) if style_list is None else style_list
        ).styles
        guard()
        cursor = wrapper.get_pos()
        guard()
        try:
            if wrapper.current_page != setup_page:
                _ = wrapper.goto_page(setup_page)
                guard()
            page = wrapper.get_pagedef_as_dict("eng")
            guard()
        finally:
            restored = wrapper.set_pos(*cursor)
            guard()
            if not restored:
                raise HwpLiveError(
                    "구역 용지 정보를 읽은 뒤 한컴 커서를 복원하지 못했습니다"
                )
        caption_source = (
            find_hierarchical_table_caption_source(
                candidate,
                wrapper,
                guard,
                setup_page=setup_page,
            )
            if any(
                isinstance(block, TableBlock) and block.caption is not None
                for block in plan.blocks
            )
            else None
        )
    finally:
        release_wrapper(wrapper)
    geometry = SectionPageGeometry.from_mm(
        paper_width_mm=_page_value(page, "PaperWidth"),
        paper_height_mm=_page_value(page, "PaperHeight"),
        landscape=bool(_page_value(page, "Landscape")),
        left_margin_mm=_page_value(page, "LeftMargin"),
        right_margin_mm=_page_value(page, "RightMargin"),
        top_margin_mm=_page_value(page, "TopMargin"),
        bottom_margin_mm=_page_value(page, "BottomMargin"),
        header_mm=_page_value(page, "HeaderLen"),
        footer_mm=_page_value(page, "FooterLen"),
        gutter_mm=_page_value(page, "GutterLen"),
        gutter_type=int(_page_value(page, "GutterType")),
    )
    content_width_mm = (
        geometry.usable_area(page_number=setup_page).width
        * MILLIMETERS_PER_INCH
        / HWPUNITS_PER_INCH
    )
    resolved = resolve_layout_style_profile(
        plan.expand_image_frames(content_width_mm),
        styles,
        fallback_style_id=style_id,
        content_width_mm=content_width_mm,
    )
    return resolved, geometry, caption_format_sources(resolved, caption_source)


def operate_layout(
    candidate: HwpDocumentCandidate,
    query: str,
    plan: LayoutPlan | None,
    *,
    resolve_only: bool,
    allow_document_change: bool,
    expected_cursor: tuple[int, int, int] | None,
    unsafe_selectors: set[str],
    guard: Callable[[], None],
    atomic: bool = False,
    style_list: DocumentStyleList | None = None,
) -> OperationResult:
    started = time.perf_counter_ns()
    lookup_microseconds = (time.perf_counter_ns() - started) // 1_000
    if resolve_only:
        return layout_operation_result(
            query,
            "resolved",
            "요청한 삽입 위치와 네이티브 레이아웃 적용 레시피를 확정했습니다",
            lookup_microseconds,
            plan,
        )
    if not allow_document_change:
        return layout_operation_result(
            query,
            "confirmation_required",
            "문서 변경 레시피입니다. 사용자 요청 범위와 일치할 때 allow_document_change=true로 다시 호출하세요",
            lookup_microseconds,
            plan,
        )
    if plan is None:
        return layout_operation_result(
            query,
            "needs_input",
            "추가할 표·문단·그림을 layout에 전달하세요",
            lookup_microseconds,
            plan,
        )

    require_writable_document(unsafe_selectors, candidate.selector)
    guard()
    before = read_native_snapshot(candidate.window_handle)
    if before is None:
        raise HwpLiveError("네이티브 레시피 실행 전 한컴 문서 상태를 읽지 못했습니다")
    if (
        plan.target == "current"
        and before.selection.selected
        and not plan.replace_selection
    ):
        return layout_operation_result(
            query,
            "needs_input",
            "현재 선택 영역이 있습니다. 삽입하려면 선택을 해제하고, 선택 내용을 교체하려면 replace_selection=true를 지정하세요",
            lookup_microseconds,
            plan,
        )

    setup_page = (
        plan.page
        if plan.target == "after_page" and plan.page is not None
        else before.page_count
        if plan.target == "document_end"
        else before.current_page
    )
    layout_page = (
        plan.page + 1
        if plan.target == "after_page" and plan.page is not None
        else setup_page
    )
    resolved_plan, page_geometry, table_caption_sources = native_style_plan(
        candidate,
        plan,
        before.style_id,
        guard,
        setup_page=setup_page,
        style_list=style_list,
    )
    preflight = preflight_layout(
        resolved_plan,
        page_geometry,
        page_number=layout_page,
    )
    if preflight.overflow == "definite":
        base = layout_operation_result(
            query,
            "needs_input",
            (
                "레이아웃 사전 검사에서 명확한 쪽 넘침을 탐지해 문서를 변경하지 "
                f"않았습니다: usable={preflight.usable_width_mm}x"
                f"{preflight.usable_height_mm}mm, estimated="
                f"{preflight.estimated_width_mm}x"
                f"{preflight.estimated_height_mm}mm"
            ),
            lookup_microseconds,
            plan,
        )
        return base.model_copy(
            update={
                "verified": False,
                "retry_safe": True,
                "partial_mutation": False,
            }
        )
    assets = prepare_layout_assets(resolved_plan)
    request = replace(
        build_native_layout_request(
            NativeLayoutContext(
                document_id=candidate.document_id,
                full_name=candidate.full_name,
                style_ids=(),
                page_count=before.page_count,
                page_geometry=page_geometry,
                page_number=layout_page,
                base_style_id=before.style_id,
                caption_format_sources=table_caption_sources,
                expected_cursor=(
                    before.cursor
                    if expected_cursor is None
                    else NativePosition(*expected_cursor)
                ),
                expected_selection=(
                    before.selection if plan.replace_selection else None
                ),
            ),
            resolved_plan,
            assets,
        ),
        atomic=atomic,
    )
    _ = encode_action_request(request)
    execution = build_native_layout_execution_plan(request)
    native_protocol = (
        10
        if any(
            isinstance(block, ReferenceLayoutBlock | ReferenceLayoutPatchBlock)
            for block in resolved_plan.blocks
        )
        else 9
    )
    reference_append_pages = reference_append_page_count(
        resolved_plan,
        before.page_count,
    )
    trailing_page = (
        before.page_count + reference_append_pages + 1
        if reference_append_pages
        else None
    )

    def mutate_and_snapshot() -> tuple[NativeActionResult, NativeSnapshot, int]:
        expected_commands = sum(
            len(batch.request.commands) for batch in execution.batches
        )
        native_result: NativeActionResult | None = None
        commands_completed = 0
        elapsed_microseconds = 0
        completed_group_keys: set[int] = set()
        for batch_index, batch in enumerate(execution.batches):
            try:
                batch_result = execute_native_actions(
                    candidate.window_handle,
                    batch.request,
                    minimum_version=native_protocol,
                )
                if batch_result is None:
                    raise HwpLiveError(
                        "한컴 네이티브 레시피 실행기를 사용할 수 없습니다"
                    )
                if batch_result.commands_executed != len(batch.request.commands):
                    partial_mutation = batch_result.commands_executed > 0
                    raise NativeActionFailure(
                        NativeActionFailureEvidence(
                            code="LAYOUT_BATCH_INCOMPLETE",
                            location=(f"{batch_index + 1}/{len(execution.batches)}"),
                            message=(
                                "네이티브 레이아웃 batch 완료 명령 수가 요청과 다릅니다"
                            ),
                            commands_completed=batch_result.commands_executed,
                            failed_step=f"layout_batch:{batch_index + 1}",
                            partial_mutation=partial_mutation,
                            retry_safe=not partial_mutation,
                            structure_digest_before=None,
                            structure_digest_after=None,
                        )
                    )
            except HwpLiveError as error:
                if len(execution.batches) == 1:
                    raise
                local_completed = (
                    min(
                        max(error.commands_completed, 0),
                        len(batch.request.commands),
                    )
                    if isinstance(error, NativeActionFailure)
                    else 0
                )
                completed_group_keys.update(batch.completed_group_keys(local_completed))
                raise _LayoutBatchFailure(
                    error,
                    batch_index=batch_index,
                    completed_batches=batch_index,
                    commands_completed=commands_completed + local_completed,
                    elapsed_microseconds=elapsed_microseconds,
                    completed_group_keys=completed_group_keys,
                ) from error
            native_result = (
                batch_result
                if native_result is None
                else _merge_native_results(native_result, batch_result)
            )
            commands_completed += batch_result.commands_executed
            elapsed_microseconds += batch_result.elapsed_microseconds
            completed_group_keys.update(item.group.key for item in batch.groups)
        if native_result is None:
            raise HwpLiveError("네이티브 레이아웃 batch 결과가 없습니다")
        after_snapshot = read_native_snapshot(candidate.window_handle)
        if after_snapshot is None:
            raise HwpLiveError(
                "네이티브 레시피 실행 후 한컴 문서 상태를 읽지 못했습니다"
            )
        if trailing_page is not None and after_snapshot.page_count == trailing_page:
            page = inspect_native_page(
                candidate.window_handle,
                trailing_page,
                include_cells=False,
            )
            if page is None:
                raise HwpLiveError(
                    "참조 레이아웃 뒤쪽의 빈 쪽 여부를 확인하지 못했습니다"
                )
            if not page.text.strip() and not page.controls:
                cleanup_request = replace(
                    request,
                    commands=build_delete_page_commands(trailing_page),
                    expected_cursor=None,
                    expected_selection=None,
                    atomic=False,
                )
                cleanup_result = execute_native_actions(
                    candidate.window_handle,
                    cleanup_request,
                    minimum_version=native_protocol,
                )
                if cleanup_result is None:
                    raise HwpLiveError("확인된 빈 꼬리 쪽을 정리하지 못했습니다")
                native_result = _merge_native_results(
                    native_result,
                    cleanup_result,
                )
                expected_commands += len(cleanup_request.commands)
                after_snapshot = read_native_snapshot(candidate.window_handle)
                if after_snapshot is None:
                    raise HwpLiveError("빈 꼬리 쪽 정리 후 문서 상태를 읽지 못했습니다")
        return native_result, after_snapshot, expected_commands

    try:
        native, after, expected_commands = run_layout_mutation(
            unsafe_selectors,
            candidate.selector,
            mutate_and_snapshot,
        )
    except _LayoutBatchFailure as failure:
        return _layout_batch_failure_result(
            query,
            plan,
            execution,
            before,
            native_protocol,
            lookup_microseconds,
            failure,
        )

    verified = (
        native.commands_executed == expected_commands
        and after.document_id == before.document_id
        and after.modified
    )
    if plan.target == "after_page":
        verified = (
            verified
            and plan.page is not None
            and after.page_count >= before.page_count + 1
            and after.current_page >= plan.page + 1
        )
    if reference_append_pages:
        verified = (
            verified and after.page_count == before.page_count + reference_append_pages
        )
    if not verified:
        mark_document_unsafe(unsafe_selectors, candidate.selector)
    status: OperationStatus = "executed" if verified else "partial_change"
    layout_call_count = len(execution.batches)
    message = (
        (
            "요청한 위치 이동과 ApplyLayout Bulk를 프로토콜 "
            f"{native_protocol} C++/ATL 네이티브 경로로 "
            f"{layout_call_count}회 실행하고 전후 상태를 검증했습니다"
        )
        if verified
        else "네이티브 레이아웃 배치는 실행됐지만 삽입 위치 또는 문서 상태의 사후 검증이 일치하지 않았습니다"
    )
    completed_group_keys = {
        item.group.key for batch in execution.batches for item in batch.groups
    }
    base = layout_operation_result(query, status, message, lookup_microseconds, plan)
    return base.model_copy(
        update={
            "changed": True,
            "execution_mode": "native_in_process",
            "native_protocol": native_protocol,
            "verification": "native_snapshot_before_after",
            "verified": verified,
            "commands_executed": native.commands_executed,
            "native_actions_executed": native.actions_executed,
            "native_elapsed_microseconds": native.elapsed_microseconds,
            "blocks_applied": len(resolved_plan.blocks),
            "created_control_ids": native.created_control_ids,
            "current_page": after.current_page,
            "page_count": after.page_count,
            # 전에 없던 쪽은 이 배치가 만든 것이다. 커서가 마지막 쪽에 있다고 해서
            # 마지막 쪽만 바뀐 게 아니다.
            "changed_pages": page_growth_evidence(
                before_page_count=before.page_count,
                after_page_count=after.page_count,
                current_page=after.current_page,
            ),
            "modified": after.modified,
            "retry_safe": verified,
            "partial_mutation": False if verified else True,
            "commands_completed": native.commands_executed,
            "updated_addresses": execution.completed_addresses(completed_group_keys),
        }
    )

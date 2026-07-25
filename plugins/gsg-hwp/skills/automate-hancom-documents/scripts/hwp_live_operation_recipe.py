from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace

from hwp_document_style_profile import resolve_layout_style_profile
from hwp_errors import HwpLiveError
from hwp_layout_preflight import preflight_layout
from hwp_live_api import ShapeValue
from hwp_live_contract import LayoutPlan, PageBreakBlock
from hwp_live_document_edit_commands import build_delete_page_commands
from hwp_live_inspection import inspect_styles
from hwp_live_layout import prepare_layout_assets
from hwp_live_native_action_contract import encode_action_request
from hwp_live_native_action_models import NativePosition
from hwp_live_native_action_results import NativeActionResult, NativeSnapshot
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_page,
    read_native_snapshot,
)
from hwp_live_native_layout import NativeLayoutContext, build_native_layout_request
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
from hwp_operation_contract import OperationResult, OperationStatus
from hwp_reference_layout_geometry import (
    MILLIMETERS_PER_INCH,
    HWPUNITS_PER_INCH,
    SectionPageGeometry,
)
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_patch import ReferenceLayoutPatchBlock


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
) -> tuple[LayoutPlan, SectionPageGeometry]:
    wrapper = attach_wrapper(candidate)
    try:
        styles = inspect_styles(wrapper, guard).styles
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
    return resolved, geometry


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
    resolved_plan, page_geometry = native_style_plan(
        candidate,
        plan,
        before.style_id,
        guard,
        setup_page=setup_page,
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
                document_id=candidate.document.DocumentID,
                full_name=candidate.document.FullName,
                style_ids=(),
                page_count=before.page_count,
                page_geometry=page_geometry,
                page_number=layout_page,
                base_style_id=before.style_id,
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
        expected_commands = len(request.commands)
        native_result = execute_native_actions(
            candidate.window_handle,
            request,
            minimum_version=native_protocol,
        )
        if native_result is None:
            raise HwpLiveError("한컴 네이티브 레시피 실행기를 사용할 수 없습니다")
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
                native_result = replace(
                    native_result,
                    commands_executed=(
                        native_result.commands_executed
                        + cleanup_result.commands_executed
                    ),
                    actions_executed=(
                        native_result.actions_executed + cleanup_result.actions_executed
                    ),
                    text_insertions=(
                        native_result.text_insertions + cleanup_result.text_insertions
                    ),
                    image_insertions=(
                        native_result.image_insertions + cleanup_result.image_insertions
                    ),
                    elapsed_microseconds=(
                        native_result.elapsed_microseconds
                        + cleanup_result.elapsed_microseconds
                    ),
                    created_control_ids=(
                        native_result.created_control_ids
                        + cleanup_result.created_control_ids
                    ),
                    call_results=(
                        native_result.call_results + cleanup_result.call_results
                    ),
                    image_timing_count=(
                        native_result.image_timing_count
                        + cleanup_result.image_timing_count
                    ),
                    image_max_microseconds=max(
                        native_result.image_max_microseconds,
                        cleanup_result.image_max_microseconds,
                    ),
                    image_total_microseconds=(
                        native_result.image_total_microseconds
                        + cleanup_result.image_total_microseconds
                    ),
                )
                expected_commands += len(cleanup_request.commands)
                after_snapshot = read_native_snapshot(candidate.window_handle)
                if after_snapshot is None:
                    raise HwpLiveError("빈 꼬리 쪽 정리 후 문서 상태를 읽지 못했습니다")
        return native_result, after_snapshot, expected_commands

    native, after, expected_commands = run_layout_mutation(
        unsafe_selectors,
        candidate.selector,
        mutate_and_snapshot,
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
    message = (
        f"요청한 위치 이동과 ApplyLayout Bulk를 프로토콜 {native_protocol} C++/ATL 네이티브 경로로 한 번 실행하고 전후 상태를 검증했습니다"
        if verified
        else "네이티브 레이아웃 배치는 실행됐지만 삽입 위치 또는 문서 상태의 사후 검증이 일치하지 않았습니다"
    )
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
            "modified": after.modified,
            "retry_safe": verified,
        }
    )

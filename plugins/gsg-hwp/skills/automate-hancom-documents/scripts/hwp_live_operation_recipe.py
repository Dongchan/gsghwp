from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace

from hwp_document_style_profile import resolve_layout_style_profile
from hwp_errors import HwpLiveError
from hwp_live_contract import LayoutPlan
from hwp_live_inspection import inspect_styles
from hwp_live_layout import prepare_layout_assets
from hwp_live_native_action_contract import encode_action_request
from hwp_live_native_action_models import NativePosition
from hwp_live_native_batch import execute_native_actions, read_native_snapshot
from hwp_live_native_layout import NativeLayoutContext, build_native_layout_request
from hwp_live_rot import HwpDocumentCandidate, attach_wrapper, release_wrapper
from hwp_operation_contract import OperationResult, OperationStatus
from hwp_operation_registry import operation_registry


DOCUMENT_END_LAYOUT_RECIPE_ID = "recipe:document_end_layout"
_DOCUMENT_END_LAYOUT_ALIASES = frozenset(
    {
        DOCUMENT_END_LAYOUT_RECIPE_ID,
        "문서 끝에 표 추가",
        "문서 마지막에 표 추가",
        "문서 끝에 레이아웃 추가",
        "문서 마지막에 레이아웃 추가",
        "add table at document end",
        "append table",
    }
)


def _compact(value: str) -> str:
    return "".join(
        character.casefold()
        for character in value
        if character.isalnum() or character == ":"
    )


def is_document_end_layout_intent(value: str) -> bool:
    compact = _compact(value.strip())
    return any(compact == _compact(alias) for alias in _DOCUMENT_END_LAYOUT_ALIASES)


def _base_result(
    query: str,
    status: OperationStatus,
    message: str,
    lookup_microseconds: int,
) -> OperationResult:
    return OperationResult(
        status=status,
        query=query,
        registry_entries=operation_registry().count,
        lookup_microseconds=lookup_microseconds,
        message=message,
        recipe_id=DOCUMENT_END_LAYOUT_RECIPE_ID,
        recipe_steps=("MoveDocEnd", "ApplyLayout"),
    )


def _page_value(values: Mapping[str, object], key: str) -> float:
    value = values.get(key)
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _native_style_plan(
    candidate: HwpDocumentCandidate,
    plan: LayoutPlan,
    style_id: int,
    guard: Callable[[], None],
) -> LayoutPlan:
    wrapper = attach_wrapper(candidate)
    try:
        styles = inspect_styles(wrapper, guard).styles
        guard()
        page = wrapper.get_pagedef_as_dict("eng")
        guard()
    finally:
        release_wrapper(wrapper)
    content_width_mm = (
        _page_value(page, "PaperWidth")
        - _page_value(page, "LeftMargin")
        - _page_value(page, "RightMargin")
    )
    resolved = resolve_layout_style_profile(
        plan.expand_image_frames(content_width_mm),
        styles,
        fallback_style_id=style_id,
        content_width_mm=content_width_mm,
    )
    return resolved.model_copy(update={"target": "document_end"})


def operate_document_end_layout(
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
    _ = (unsafe_selectors, guard)
    started = time.perf_counter_ns()
    lookup_microseconds = (time.perf_counter_ns() - started) // 1_000
    if resolve_only:
        return _base_result(
            query,
            "resolved",
            "문서 끝 이동과 네이티브 레이아웃 적용 레시피를 확정했습니다",
            lookup_microseconds,
        )
    if not allow_document_change:
        return _base_result(
            query,
            "confirmation_required",
            "문서 변경 레시피입니다. 사용자 요청 범위와 일치할 때 allow_document_change=true로 다시 호출하세요",
            lookup_microseconds,
        )
    if plan is None:
        return _base_result(
            query,
            "needs_input",
            "추가할 표·문단·그림을 layout에 전달하세요",
            lookup_microseconds,
        )
    if plan.replace_selection:
        return _base_result(
            query,
            "needs_input",
            "문서 끝 레시피는 선택 영역 교체를 허용하지 않습니다",
            lookup_microseconds,
        )

    before = read_native_snapshot(candidate.window_handle)
    if before is None:
        raise HwpLiveError("네이티브 레시피 실행 전 한컴 문서 상태를 읽지 못했습니다")

    resolved_plan = _native_style_plan(
        candidate,
        plan,
        before.style_id,
        guard,
    )
    assets = prepare_layout_assets(resolved_plan)
    request = replace(
        build_native_layout_request(
            NativeLayoutContext(
                document_id=candidate.document.DocumentID,
                full_name=candidate.document.FullName,
                style_ids=(),
                expected_cursor=(
                    None if expected_cursor is None else NativePosition(*expected_cursor)
                ),
            ),
            resolved_plan,
            assets,
        ),
        atomic=atomic,
    )
    _ = encode_action_request(request)
    native = execute_native_actions(
        candidate.window_handle,
        request,
        minimum_version=9,
    )
    if native is None:
        raise HwpLiveError("한컴 네이티브 레시피 실행기를 사용할 수 없습니다")
    after = read_native_snapshot(candidate.window_handle)
    if after is None:
        raise HwpLiveError("네이티브 레시피 실행 후 한컴 문서 상태를 읽지 못했습니다")

    base = _base_result(
        query,
        "executed",
        "MoveDocEnd와 ApplyLayout을 한 번의 프로토콜 9 C++/ATL 네이티브 배치로 실행했습니다",
        lookup_microseconds,
    )
    return base.model_copy(
        update={
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": "native_snapshot_before_after",
            "commands_executed": native.commands_executed,
            "native_elapsed_microseconds": native.elapsed_microseconds,
            "blocks_applied": len(resolved_plan.blocks),
            "created_control_ids": native.created_control_ids,
            "current_page": after.current_page,
            "page_count": after.page_count,
            "modified": after.modified,
        }
    )

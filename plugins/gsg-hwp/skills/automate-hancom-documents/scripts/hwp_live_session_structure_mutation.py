from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
from hwp_live_native_action_contract import encode_action_request
from hwp_live_native_action_models import (
    NativeActionRequest,
    NativeActionResult,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
    ReplaceSelectionCommand,
)
from hwp_live_native_batch import execute_native_actions, read_native_snapshot
from hwp_live_native_layout import NativeLayoutContext, build_native_layout_request
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_safety import require_writable_document, run_layout_mutation


@dataclass(slots=True)
class _NativeLayoutBox:
    result: NativeActionResult | None = None
    snapshot: NativeSnapshot | None = None


@dataclass(slots=True)
class _NativeMutationBox:
    result: NativeActionResult | None = None
    snapshot: NativeSnapshot | None = None


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
                document_id=candidate.document.DocumentID,
                full_name=candidate.document.FullName,
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
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            style_ids=tuple((style.name, style.style_id) for style in styles.styles),
            expected_cursor=position,
            expected_selection=_native_selection(selected),
        ),
        resolved_plan,
        assets,
    )
    _ = encode_action_request(native_request)
    native = _NativeLayoutBox()

    def mutate_native_layout() -> None:
        native.result = execute_native_actions(
            candidate.window_handle,
            native_request,
            minimum_version=4,
        )
        if native.result is None:
            return
        native.snapshot = read_native_snapshot(candidate.window_handle)
        if native.snapshot is None:
            raise HwpLiveError("네이티브 레이아웃 배치 후 한컴 문서 상태를 읽지 못했습니다")

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

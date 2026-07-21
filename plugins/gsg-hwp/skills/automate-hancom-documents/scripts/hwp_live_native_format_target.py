from __future__ import annotations

from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import NativeDetailedInspection, NativePageInspection
from hwp_live_native_batch import inspect_native_page, inspect_native_structure
from hwp_live_rot import HwpDocumentCandidate
from hwp_operation_contract import HwpOperateTarget, OperationStatus


@dataclass(frozen=True, slots=True)
class NativeTableTargetRequest:
    candidate: HwpDocumentCandidate
    routing_page: NativePageInspection
    target: HwpOperateTarget | None
    snapshot_control_type: str
    snapshot_control_id: str


@dataclass(frozen=True, slots=True)
class ResolvedTable:
    instance_id: str
    basis: str
    page: int
    rows: int | None = None
    columns: int | None = None


@dataclass(frozen=True, slots=True)
class TargetFailure:
    status: OperationStatus
    message: str
    required_inputs: tuple[str, ...] = ()


def _table_ids(page: NativePageInspection) -> tuple[str, ...]:
    return tuple(
        control.instance_id
        for control in page.controls
        if control.control_type == "tbl" and control.instance_id
    )


def _detail_matches(
    detail: NativeDetailedInspection,
    target: HwpOperateTarget,
) -> tuple[str, ...]:
    table_ids = {
        control.instance_id
        for control in detail.controls
        if control.control_type == "tbl"
    }
    if target.caption_contains is not None:
        table_ids.intersection_update(
            caption.table_instance_id
            for caption in detail.captions
            if target.caption_contains in caption.text
        )
    if target.header_signature:
        normalized = tuple(value.strip() for value in target.header_signature)
        table_ids.intersection_update(
            table_id
            for table_id in table_ids
            if all(
                any(
                    cell.table_instance_id == table_id and header in cell.text
                    for cell in detail.cells
                )
                for header in normalized
            )
        )
    return tuple(sorted(table_ids))


def _page_for_target(
    request: NativeTableTargetRequest,
    page_number: int,
) -> NativePageInspection:
    if page_number == request.routing_page.page:
        return request.routing_page
    page = inspect_native_page(
        request.candidate.window_handle,
        page_number,
        include_cells=False,
    )
    if page is None:
        raise HwpLiveError("표 대상 페이지를 네이티브로 읽지 못했습니다")
    return page


def _resolved_table(
    request: NativeTableTargetRequest,
    instance_id: str,
    basis: str,
    page_number: int,
) -> ResolvedTable:
    page = _page_for_target(request, page_number)
    control = next(
        (
            item
            for item in page.controls
            if item.control_type == "tbl" and item.instance_id == instance_id
        ),
        None,
    )
    if control is None or control.rows is None or control.columns is None:
        inspected = inspect_native_page(
            request.candidate.window_handle,
            page_number,
            include_cells=True,
        )
        if inspected is not None:
            control = next(
                (
                    item
                    for item in inspected.controls
                    if item.control_type == "tbl"
                    and item.instance_id == instance_id
                ),
                control,
            )
    return ResolvedTable(
        instance_id,
        basis,
        page_number,
        None if control is None else control.rows,
        None if control is None else control.columns,
    )


def _search_structured_target(
    request: NativeTableTargetRequest,
    target: HwpOperateTarget,
) -> tuple[str, ...]:
    first_page = target.page_hint or request.routing_page.page
    pages = (first_page,)
    if target.scope == "document":
        pages = (
            first_page,
            *(
                page
                for page in range(1, request.routing_page.page_count + 1)
                if page != first_page
            ),
        )
    matches: list[str] = []
    for page_number in pages:
        page = _page_for_target(request, page_number)
        if (
            target.caption_contains is not None
            and target.caption_contains not in page.text
        ):
            continue
        detail = inspect_native_structure(request.candidate.window_handle, page_number)
        if detail is None:
            raise HwpLiveError("표 대상 상세 구조를 네이티브로 읽지 못했습니다")
        matches.extend(_detail_matches(detail, target))
        if matches and target.match_policy == "first":
            break
    return tuple(dict.fromkeys(matches))


def resolve_native_table_target(
    request: NativeTableTargetRequest,
) -> ResolvedTable | TargetFailure:
    target = request.target
    if target is not None and target.kind != "table":
        return TargetFailure(
            "schema_conflict",
            "표 작업의 target.kind는 table이어야 합니다",
        )
    if target is not None and target.control_instance_id is not None:
        return _resolved_table(
            request,
            target.control_instance_id,
            "target.control_instance_id",
            target.page_hint or request.routing_page.page,
        )
    if target is not None and (
        target.caption_contains is not None or target.header_signature
    ):
        matches = _search_structured_target(request, target)
        if len(matches) == 1 or (matches and target.match_policy == "first"):
            basis = (
                "target.caption_contains"
                if target.caption_contains is not None
                else "target.header_signature"
            )
            return _resolved_table(
                request,
                matches[0],
                basis,
                target.page_hint or request.routing_page.page,
            )
        return TargetFailure(
            "ambiguous" if matches else "not_found",
            "구조화된 표 조건과 일치하는 대상을 하나로 확정하지 못했습니다",
        )
    page = request.routing_page
    if target is not None and target.page_hint is not None:
        page = _page_for_target(request, target.page_hint)
    tables = _table_ids(page)
    if target is not None and target.table_index is not None:
        index = target.table_index - 1
        if index >= len(tables):
            return TargetFailure("not_found", "지정한 표 순번이 대상 페이지에 없습니다")
        return _resolved_table(
            request,
            tables[index],
            "target.table_index",
            page.page,
        )
    use_selection = target is None or target.binding == "selection"
    if (
        use_selection
        and request.snapshot_control_type == "tbl"
        and request.snapshot_control_id
    ):
        return _resolved_table(
            request,
            request.snapshot_control_id,
            "native.selection",
            request.routing_page.page,
        )
    if len(tables) == 1:
        return _resolved_table(
            request,
            tables[0],
            "target.unique_page_table",
            page.page,
        )
    return TargetFailure(
        "ambiguous" if tables else "needs_input",
        "고유한 표 대상이 필요합니다",
        ("inputs.target.control_instance_id",),
    )

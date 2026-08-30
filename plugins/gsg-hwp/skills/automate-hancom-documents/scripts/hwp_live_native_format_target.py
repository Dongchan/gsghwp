from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, cast

from hwp_errors import HwpLiveError
from hwp_live_api import HwpComApplication, HwpControl
from hwp_live_native_action_models import NativeDetailedInspection, NativePageInspection
from hwp_live_native_batch import inspect_native_page, inspect_native_structure
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_safety import LIVE_OPERATION_ERRORS
from hwp_live_structure_identity import control_ref, table_ref
from hwp_operation_contract import HwpOperateTarget, OperationStatus


#: 대상을 호출자의 조건이 아니라 한/글의 현재 선택에서 읽어낸 근거들. 이 근거로
#: 표가 정해졌다는 것은 "지금 선택된 것이 표다"를 이미 확인했다는 뜻이므로,
#: 어느 셀에 적용할지도 그 선택에서 읽어야 한다(hwp_live_native_format_prepare).
SELECTION_TARGET_BASES: Final = frozenset({"native.selection", "com.selection"})


class _CurrentControlReader(Protocol):
    """한/글이 지금 무엇을 지목하고 있는지를 묻는 두 창구."""

    @property
    def CurSelectedCtrl(self) -> HwpControl: ...

    @property
    def ParentCtrl(self) -> HwpControl: ...


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


# Two identifiers name the same table, and only one of them is HWP's.
#
# hwp_inspect_page_fast reports ``instance_id``, which is what GetCtrlInstID()
# returned and the only thing SelectCtrl understands. hwp_inspect_structure
# reports ``table_ref`` -- "table:" plus a digest -- and hides the native id
# (StructureTable.control_instance_id is excluded from its output), so a caller
# working from the structure response has no other identifier to hand back.
#
# Passing that digest straight through to the bridge selected nothing, and the
# CurSelectedCtrl read that followed reported a type mismatch instead of the
# real problem. The digest is a pure function of (document id, "tbl", instance
# id), so the two spaces are joined here by recomputing it per candidate table.
def _table_reference(document_id: int, instance_id: str) -> str:
    return table_ref(control_ref(document_id, "tbl", instance_id))


def _names_table(
    document_id: int,
    instance_id: str,
    requested: str,
) -> bool:
    if instance_id == requested:
        return True
    return requested.startswith("table:") and (
        _table_reference(document_id, instance_id) == requested
    )


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
    if control is None:
        for candidate_page_number in range(1, request.routing_page.page_count + 1):
            if candidate_page_number == page_number:
                continue
            candidate_page = _page_for_target(request, candidate_page_number)
            candidate_control = next(
                (
                    item
                    for item in candidate_page.controls
                    if item.control_type == "tbl"
                    and item.instance_id == instance_id
                ),
                None,
            )
            if candidate_control is None:
                continue
            page_number = candidate_page_number
            control = candidate_control
            break
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


def _locate_requested_table(
    request: NativeTableTargetRequest,
    requested: str,
    page_hint: int,
) -> tuple[str, int] | None:
    """The native instance id and page of the table the caller named.

    ``None`` when no table in the document answers to that identifier, which
    used to reach the bridge as a SelectCtrl argument nothing could match.
    """
    pages = (
        page_hint,
        *(
            page
            for page in range(1, request.routing_page.page_count + 1)
            if page != page_hint
        ),
    )
    for page_number in pages:
        page = _page_for_target(request, page_number)
        for control in page.controls:
            if control.control_type != "tbl" or not control.instance_id:
                continue
            if _names_table(page.document_id, control.instance_id, requested):
                return control.instance_id, page_number
    return None


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


def _com_selected_table_id(application: HwpComApplication | None) -> str | None:
    """한/글이 지금 지목하는 개체가 표라면 그 native instance id.

    네이티브 현재 상태 조회(read_native_snapshot)의 CONTEXT 레코드가 표를
    싣지 못하고 돌아오는 일이 실제로 있다. 2026-08-23 12:32:09Z 의 운영 저널
    기록(operation=table.format, status=ambiguous, "고유한 표 대상이
    필요합니다")이 그 경우다 -- 사용자는 한/글에서 A3:E3 을 직접 선택해 둔
    상태였고, 같은 세션의 다음 조회는 표 1152398909 를 정상적으로 읽어냈다.
    그 한 번의 빈 스냅샷 때문에 도구는 "표를 지정하라"로 되돌려보냈고, 모델은
    선택을 읽는 호출을 한 번 더 쓴 뒤 같은 요청을 다시 보냈다.

    같은 종류의 빈 응답에 대한 우회는 이미 이 저장소에 있다:
    hwp_live_native_format_recipe._resolve_selected_table_cells 는
    스냅샷의 selection.mode 가 0 으로 오면 application.SelectionMode 를 다시
    읽는다. 여기도 같은 자리에서 같은 방식으로 되묻는다.

    한/글이 아무것도 지목하고 있지 않으면 ``None``. 그때는 호출부가 지금까지와
    똑같이 판단한다 -- 새로 막는 것은 없다.
    """
    if application is None:
        return None
    # 두 속성은 한/글 COM 응용 개체의 것이지만 HwpComApplication 선언에는 없다
    # (hwp_live_api.py:131-161; 같은 개체를 다른 이름으로 보는
    # LiveHwpApplication:187-190 에만 적혀 있다). 여기서 읽는 두 가지만 좁게
    # 적어 두고 그 이름으로 본다.
    reader = cast(_CurrentControlReader, cast(object, application))
    # CurSelectedCtrl 이 먼저다. 표 자체를 고른 상태는 그쪽이 답하고, 셀 블록
    # 선택이나 셀 안의 커서는 ParentCtrl 이 답한다. 네이티브 쪽 순서와 같다
    # (ComState.cpp ReadCurrentControlBestEffort).
    readers = (
        lambda: reader.CurSelectedCtrl,
        lambda: reader.ParentCtrl,
    )
    for read in readers:
        try:
            control = read()
            if str(control.CtrlID).strip() != "tbl":
                continue
            instance = str(control.GetCtrlInstID()).strip()
        except LIVE_OPERATION_ERRORS:
            # 지목한 개체가 없으면 한/글은 값을 주지 않는다. 없다는 답도 답이다.
            continue
        if instance:
            return instance
    return None


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
        requested = target.control_instance_id
        located = _locate_requested_table(
            request,
            requested,
            target.page_hint or request.routing_page.page,
        )
        if located is None:
            return TargetFailure(
                "not_found",
                "target으로 받은 표 식별자가 이 문서의 어떤 표와도 "
                f"일치하지 않습니다: {requested}",
                ("inputs.target.control_instance_id",),
            )
        instance_id, page_number = located
        return _resolved_table(
            request,
            instance_id,
            "target.control_instance_id",
            page_number,
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
    if use_selection:
        # 스냅샷이 현재 개체를 싣지 못했을 때만 온다. 호출자가 대상을 지목한
        # 요청은 위에서 이미 끝났으므로, 여기서 읽는 선택이 호출자의 지목을
        # 이기는 일은 없다.
        selected = _com_selected_table_id(request.candidate.application)
        if selected is not None:
            return _resolved_table(
                request,
                selected,
                "com.selection",
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

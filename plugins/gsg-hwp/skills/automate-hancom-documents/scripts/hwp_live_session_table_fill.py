from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_structure,
    read_native_snapshot,
)
from hwp_live_native_table_topology import (
    table_formula_selection_region,
    table_topology,
)
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_session_structure import inspect_candidate_structure
from hwp_live_session_workflow import workflow_result
from hwp_live_structure_contract import DocumentStructure
from hwp_live_workflow_table import (
    prepare_table_fill,
    resolve_workflow_table,
    table_fill_contract_conflict,
    verify_table_fill,
    workflow_table_candidate,
    workflow_page,
)
from hwp_live_workflow_table_records import TableRecordMappingError
from hwp_operation_contract import (
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    OperationResult,
    WorkflowResolution,
)
from hwp_table_format_inference import TableFormatAmbiguity


def _has_explicit_table_selector(target: HwpOperateTarget) -> bool:
    return (
        target.control_instance_id is not None
        or target.page_hint is not None
        or target.table_index is not None
        or target.caption_contains is not None
        or bool(target.header_signature)
    )


def _bind_live_table_selection(
    candidate: HwpDocumentCandidate,
    target: HwpOperateTarget,
    data: HwpOperateData,
) -> tuple[HwpOperateTarget, HwpOperateData, frozenset[str]]:
    before = read_native_snapshot(candidate.window_handle)
    if before is None:
        raise HwpLiveError("표 채움 전 현재 한컴 커서와 선택 상태를 읽지 못했습니다")
    bound_target = target
    if (
        not _has_explicit_table_selector(target)
        and before.control_type == "tbl"
        and before.control_instance_id
    ):
        bound_target = target.model_copy(
            update={
                "control_instance_id": before.control_instance_id,
                "page_hint": before.current_page,
            }
        )
    if not data.rows or data.start_cell is not None:
        return bound_target, data, frozenset()
    if (
        _has_explicit_table_selector(target)
        and target.control_instance_id is None
    ):
        return bound_target, data, frozenset()
    if (
        before.control_type != "tbl"
        or not before.control_instance_id
        or (
            bound_target.control_instance_id is not None
            and bound_target.control_instance_id != before.control_instance_id
        )
    ):
        return bound_target, data, frozenset()
    selected_addresses: tuple[str, ...] = ()
    selection_mode = before.selection.mode or int(candidate.application.SelectionMode)
    base_mode = selection_mode & 0x0F
    strict_selection = bool(selection_mode & 0x10)
    if base_mode == 3:
        if not before.selection.cell_addresses and before.selection.cell_address_error:
            raise HwpLiveError(before.selection.cell_address_error)
        detail = inspect_native_structure(candidate.window_handle, before.current_page)
        if detail is None:
            raise HwpLiveError("선택 셀 범위를 위한 실제 표 구조를 읽지 못했습니다")
        topology = table_topology(
            detail,
            before.control_instance_id,
        )
        if before.selection.cell_addresses:
            selected_addresses = topology.selection_region_by_addresses(
                before.selection.cell_addresses
            )
        elif before.selection.selected:
            selected_addresses = topology.selection_region_by_list_ids(
                before.selection.start.list_id,
                before.selection.end.list_id,
            )
        elif strict_selection:
            selected_addresses = table_formula_selection_region(
                candidate.application,
                topology,
            )
        else:
            selected_addresses = topology.selection_region_by_list_ids(
                before.selection.start.list_id,
                before.selection.end.list_id,
            )
        start_cell = selected_addresses[0]
    elif base_mode == 4:
        start_cell = "A1"
    else:
        start_cell = before.cell_address
    if not start_cell:
        return bound_target, data, frozenset()
    selected_range: frozenset[str] = (
        frozenset(selected_addresses)
        if len(selected_addresses) > 1
        else frozenset[str]()
    )
    return (
        bound_target,
        data.model_copy(update={"start_cell": start_cell}),
        selected_range,
    )


def operate_table_fill(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
    resolution: WorkflowResolution,
    target: HwpOperateTarget | None,
    data: HwpOperateData | None,
    policy: HwpOperatePolicy,
    postconditions: HwpOperatePostconditions,
    *,
    allow_document_change: bool,
) -> tuple[OperationResult | None, DocumentStructure | None]:
    if (
        resolution.status != "resolved"
        or resolution.candidates[0].workflow_id != "table.fill_existing"
    ):
        return None, None
    if target is None or data is None or not (data.cells or data.rows or data.records):
        return (
            workflow_result(
                resolution,
                "needs_input",
                "기존 표 채움에는 대상 조건과 cells, rows, records 중 하나가 필요합니다",
                required_inputs=("inputs.target", "inputs.data"),
            ),
            None,
        )
    target, data, selected_addresses = _bind_live_table_selection(
        candidate,
        target,
        data,
    )
    if data.rows and data.start_cell is None:
        return (
            workflow_result(
                resolution,
                "needs_input",
                "행렬 표 채움은 start_cell을 지정하거나 한컴 표 셀에 커서를 두거나 셀을 선택해야 합니다",
                required_inputs=("inputs.data.start_cell",),
            ),
            None,
        )
    contract_conflict = table_fill_contract_conflict(
        target,
        data,
        postconditions,
    )
    if contract_conflict is not None:
        return (
            workflow_result(
                resolution,
                "schema_conflict",
                contract_conflict,
            ),
            None,
        )
    if not allow_document_change:
        return (
            workflow_result(
                resolution,
                "confirmation_required",
                "기존 표 내용을 변경하는 작업입니다",
            ),
            None,
        )
    page = workflow_page(target)
    before = inspect_candidate_structure(
        hwp,
        candidate,
        page,
        lambda: None,
    )
    resolved_table = resolve_workflow_table(before, target)
    if resolved_table.table is None or resolved_table.table_index is None:
        return (
            workflow_result(
                resolution,
                "ambiguous" if resolved_table.candidates else "not_found",
                (
                    "대상 조건과 일치하는 표를 하나로 확정하지 못했습니다"
                    if resolved_table.candidates
                    else "대상 조건과 일치하는 표가 없습니다"
                ),
            ).model_copy(update={"target_candidates": resolved_table.candidates}),
            None,
        )
    try:
        anchor_paragraph = resolved_table.table.anchor.paragraph
        surrounding_texts = tuple(
            paragraph.text
            for paragraph in before.paragraphs
            if anchor_paragraph - 4 <= paragraph.index < anchor_paragraph
            and paragraph.text.strip()
        )
        if resolved_table.table.caption is not None:
            surrounding_texts += (resolved_table.table.caption.text,)
        prepared = prepare_table_fill(
            candidate,
            resolved_table.table,
            resolved_table.table_index,
            data,
            policy,
            postconditions,
            surrounding_texts=surrounding_texts,
        )
    except TableRecordMappingError as error:
        table_candidate = workflow_table_candidate(
            resolved_table.table,
            resolved_table.table_index,
            before.page,
        )
        return (
            workflow_result(
                resolution,
                "needs_input",
                str(error),
                required_inputs=("inputs.data",),
            ).model_copy(update={"target_candidates": (table_candidate,)}),
            before,
        )
    except TableFormatAmbiguity as error:
        table_candidate = workflow_table_candidate(
            resolved_table.table,
            resolved_table.table_index,
            before.page,
        )
        return (
            workflow_result(
                resolution,
                "needs_input",
                str(error),
                required_inputs=("inputs.policy.numeric_value_mode",),
            ).model_copy(
                update={
                    "target_candidates": (table_candidate,),
                    "format_candidates": error.candidates,
                }
            ),
            before,
        )
    if selected_addresses and any(
        address not in selected_addresses for address, _ in prepared.replacements
    ):
        return (
            workflow_result(
                resolution,
                "schema_conflict",
                "행렬 데이터가 현재 선택한 셀 블록 밖으로 넘어갑니다",
            ),
            before,
        )
    native = execute_native_actions(
        candidate.window_handle,
        prepared.request,
        minimum_version=prepared.native_protocol,
    )
    if native is None:
        raise HwpLiveError(
            f"한컴 프로토콜 {prepared.native_protocol} 네이티브 표 채움 실행기를 "
            + "사용할 수 없습니다"
        )
    state_after = read_native_snapshot(candidate.window_handle)
    if state_after is None:
        raise HwpLiveError("한컴 네이티브 표 채움 후 현재 쪽을 읽지 못했습니다")
    after = inspect_candidate_structure(
        hwp,
        candidate,
        state_after.current_page,
        lambda: None,
    )
    verify_table_fill(
        after,
        prepared,
        page_count_before=before.page_count,
        postconditions=postconditions,
    )
    result = workflow_result(
        resolution,
        "executed",
        f"기존 표를 찾아 프로토콜 {prepared.native_protocol} C++/ATL "
        "네이티브 배치로 채우고 구조를 검증했습니다",
    ).model_copy(
        update={
            "execution_mode": "native_in_process",
            "native_protocol": prepared.native_protocol,
            "verification": "native_snapshot_before_after",
            "verified": True,
            "commands_executed": native.commands_executed,
            "native_elapsed_microseconds": native.elapsed_microseconds,
            "current_page": after.page,
            "page_count": after.page_count,
            "modified": bool(prepared.replacements),
            "updated_addresses": tuple(
                address for address, _ in prepared.replacements
            ),
        }
    )
    return result, after

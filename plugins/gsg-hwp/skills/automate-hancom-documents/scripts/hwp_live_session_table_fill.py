from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_batch import execute_native_actions
from hwp_live_native_table_snapshot import refresh_native_table_snapshot
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
        prepared = prepare_table_fill(
            candidate,
            resolved_table.table,
            resolved_table.table_index,
            data,
            policy,
            postconditions,
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
    native = execute_native_actions(
        candidate.window_handle,
        prepared.request,
        minimum_version=9,
    )
    if native is None:
        raise HwpLiveError("한컴 프로토콜 9 네이티브 표 채움 실행기를 사용할 수 없습니다")
    refreshed = refresh_native_table_snapshot(before, resolved_table.table)
    if refreshed is None:
        raise HwpLiveError("한컴 네이티브 표 채움 결과를 다시 조회하지 못했습니다")
    after, _ = refreshed
    verify_table_fill(
        after,
        prepared,
        page_count_before=before.page_count,
        postconditions=postconditions,
    )
    result = workflow_result(
        resolution,
        "executed",
        "기존 표를 찾아 프로토콜 9 C++/ATL 네이티브 배치로 채우고 구조를 검증했습니다",
    ).model_copy(
        update={
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": "native_snapshot_before_after",
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

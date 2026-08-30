from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_edit_history import LiveEditHistoryStore
from hwp_live_native_action_models import NativeActionRequest
from hwp_live_native_batch import execute_native_actions
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_session_structure import inspect_candidate_structure
from hwp_live_structure_contract import DocumentStructure, StructureTable
from hwp_live_workflow_table import resolve_workflow_table, workflow_page
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationResult,
    OperationStatus,
    WorkflowResolution,
)
from hwp_operation_registry import operation_registry
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_priority_recipe_result import priority_recipe_result as _result
from hwp_priority_table_expand import (
    ExpandedTablePlan,
    data_record_count,
    prepare_expand_and_fill,
)
from hwp_priority_table_images import prepare_table_image_commands
from hwp_priority_table_series import operate_table_series_recipe


_TABLE_WORKFLOWS = frozenset[HwpWorkflowId](
    (
        "table.repeat_template",
        "table.build_series",
        "table.expand_and_fill",
        "table.insert_images",
    )
)


def _resolved_table(
    snapshot: DocumentStructure,
    target: HwpOperateTarget,
) -> tuple[StructureTable | None, OperationResult | None]:
    resolved = resolve_workflow_table(snapshot, target)
    if resolved.table is not None:
        return resolved.table, None
    status: OperationStatus = "ambiguous" if resolved.candidates else "not_found"
    return None, OperationResult(
        status=status,
        query="table target resolution",
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        target_candidates=resolved.candidates,
        message=(
            "대상 조건과 일치하는 표를 하나로 확정하지 못했습니다"
            if resolved.candidates
            else "대상 조건과 일치하는 표가 없습니다"
        ),
    )


def _execute(
    candidate: HwpDocumentCandidate,
    request: NativeActionRequest,
    minimum_version: int,
) -> tuple[int, int]:
    native = execute_native_actions(
        candidate.window_handle,
        request,
        minimum_version=minimum_version,
    )
    if native is None:
        raise HwpLiveError(
            f"한컴 프로토콜 {minimum_version} 네이티브 표 recipe를 사용할 수 없습니다"
        )
    return native.commands_executed, native.elapsed_microseconds


def _table_after(
    snapshot: DocumentStructure,
    control_id: str,
) -> StructureTable:
    table = next(
        (item for item in snapshot.tables if item.control_instance_id == control_id),
        None,
    )
    if table is None:
        raise HwpLiveError("작업 후 대상 표를 네이티브 구조에서 다시 찾지 못했습니다")
    return table


def _verify_expansion(
    after: DocumentStructure,
    before: DocumentStructure,
    table: StructureTable,
    plan: ExpandedTablePlan,
    postconditions: HwpOperatePostconditions,
) -> None:
    control_id = table.control_instance_id
    if control_id is None:
        raise HwpLiveError("확장 표의 네이티브 개체 ID가 없습니다")
    updated = _table_after(after, control_id)
    if updated.rows < table.rows + plan.rows_added:
        raise HwpLiveError("요청한 표 행 확장 결과를 확인하지 못했습니다")
    cells = {cell.address: cell for cell in updated.cells}
    for address, value in plan.replacements:
        if address not in cells or cells[address].text != value:
            raise HwpLiveError(f"{address} 셀의 확장 입력 결과가 요청과 다릅니다")
    if postconditions.preserve_page_count and after.page_count != before.page_count:
        raise HwpLiveError("표 확장 후 페이지 수 보존 완료조건을 만족하지 못했습니다")


def operate_table_recipe(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
    resolution: WorkflowResolution,
    target: HwpOperateTarget | None,
    data: HwpOperateData | None,
    assets: HwpOperateAssets | None,
    policy: HwpOperatePolicy,
    postconditions: HwpOperatePostconditions,
    recipe_inputs: HwpPriorityRecipeInputs | None,
    *,
    resolve_only: bool,
    allow_document_change: bool,
    history: LiveEditHistoryStore | None = None,
) -> OperationResult | None:
    workflow = resolution.workflow_id
    if workflow not in _TABLE_WORKFLOWS:
        return None
    if resolve_only:
        return _result(
            resolution, "resolved", "인증된 프로토콜 9 표 recipe를 확정했습니다"
        )
    if not allow_document_change:
        return _result(
            resolution, "confirmation_required", "표 구조나 내용을 변경하는 작업입니다"
        )
    if workflow in {"table.repeat_template", "table.build_series"}:
        if history is None:
            raise HwpLiveError("표 시리즈 작업에는 MCP 편집 이력 저장소가 필요합니다")
        return operate_table_series_recipe(
            candidate,
            history,
            resolution,
            recipe_inputs,
            postconditions,
        )
    if target is None or target.kind != "table":
        return _result(
            resolution,
            "needs_input",
            "고유한 표 대상이 필요합니다",
            required_inputs=("inputs.target",),
        )
    page = workflow_page(target)
    before = inspect_candidate_structure(hwp, candidate, page, lambda: None)
    table, failed = _resolved_table(before, target)
    if table is None:
        assert failed is not None
        return failed.model_copy(
            update={
                "query": resolution.query,
                "workflow_candidates": resolution.candidates,
            }
        )
    expansion_plan: ExpandedTablePlan | None = None
    if workflow == "table.expand_and_fill":
        if data is None or not (data.cells or data.rows or data.records):
            return _result(
                resolution,
                "needs_input",
                "확장 후 채울 표 데이터가 필요합니다",
                required_inputs=("inputs.data",),
            )
        if (
            postconditions.record_count is not None
            and postconditions.record_count != data_record_count(data)
        ):
            return _result(
                resolution,
                "schema_conflict",
                "record_count와 입력 레코드 수가 다릅니다",
            )
        expansion_plan = prepare_expand_and_fill(candidate, table, data, policy)
        if not expansion_plan.replacements and expansion_plan.rows_added == 0:
            return _result(
                resolution,
                "executed",
                "빈 셀만 채우기 정책에 따라 변경할 셀이 없습니다",
            ).model_copy(
                update={
                    "execution_mode": "native_in_process",
                    "native_protocol": 9,
                    "verification": "native_structure_no_change",
                    "verified": True,
                    "commands_executed": 0,
                    "native_elapsed_microseconds": 0,
                    "current_page": before.page,
                    "page_count": before.page_count,
                    "modified": False,
                }
            )
        native_protocol = expansion_plan.native_protocol
        commands_executed, elapsed = _execute(
            candidate,
            expansion_plan.request,
            native_protocol,
        )
        updated_addresses = tuple(address for address, _ in expansion_plan.replacements)
    else:
        native_protocol = 9
        if assets is None or not assets.images:
            return _result(
                resolution,
                "needs_input",
                "셀 주소별 그림 경로가 필요합니다",
                required_inputs=("inputs.assets.images",),
            )
        commands, updated_addresses = prepare_table_image_commands(
            table, assets, policy
        )
        if not updated_addresses:
            return _result(
                resolution,
                "executed",
                "기존 그림 보존 정책에 따라 변경할 셀이 없습니다",
            ).model_copy(
                update={
                    "execution_mode": "native_in_process",
                    "native_protocol": 9,
                    "verification": "native_structure_no_change",
                    "verified": True,
                    "commands_executed": 0,
                    "native_elapsed_microseconds": 0,
                    "current_page": before.page,
                    "page_count": before.page_count,
                    "modified": False,
                }
            )
        commands_executed, elapsed = _execute(
            candidate,
            NativeActionRequest(candidate.document_id, candidate.full_name, commands),
            native_protocol,
        )
    after = inspect_candidate_structure(hwp, candidate, before.page, lambda: None)
    try:
        if workflow == "table.expand_and_fill":
            if expansion_plan is None:
                raise HwpLiveError("table expansion executed without a prepared plan")
            _verify_expansion(after, before, table, expansion_plan, postconditions)
        else:
            control_id = table.control_instance_id
            assert control_id is not None
            cells = {
                cell.address: cell for cell in _table_after(after, control_id).cells
            }
            if any(
                address not in cells or not cells[address].has_picture
                for address in updated_addresses
            ):
                raise HwpLiveError(
                    "표 그림 삽입 결과를 네이티브 구조에서 확인하지 못했습니다"
                )
    except HwpLiveError as error:
        return _result(resolution, "partial_change", str(error)).model_copy(
            update={
                "execution_mode": "native_in_process",
                "native_protocol": native_protocol,
                "verification": "native_snapshot_before_after",
                "verified": False,
                "commands_executed": commands_executed,
                "commands_completed": commands_executed,
                "native_elapsed_microseconds": elapsed,
                "current_page": after.page,
                "page_count": after.page_count,
                "modified": True,
                "changed": True,
                "updated_addresses": updated_addresses,
                "partial_change": True,
                "retry_safe": False,
            }
        )
    return _result(
        resolution,
        "executed",
        f"프로토콜 {native_protocol} C++/ATL 네이티브 표 recipe를 실행하고 검증했습니다",
    ).model_copy(
        update={
            "execution_mode": "native_in_process",
            "native_protocol": native_protocol,
            "verification": "native_snapshot_before_after",
            "verified": True,
            "commands_executed": commands_executed,
            "native_elapsed_microseconds": elapsed,
            "current_page": after.page,
            "page_count": after.page_count,
            "modified": True,
            "updated_addresses": updated_addresses,
        }
    )

from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — native format state machine; splitting obscures mutation ordering

from hwp_errors import HwpLiveError
from hwp_live_api import HwpComApplication
from hwp_live_native_action_models import (
    NativeActionRequest,
    NativeDetailedInspection,
    NativeSnapshot,
)
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_structure,
    read_native_snapshot,
)
from hwp_live_native_format_commands import (
    MergeCommandPlan,
    SplitCommandPlan,
    TableFormatCommandPlan,
    TextFormatCommandPlan,
    build_native_format_commands,
)
from hwp_live_native_format_contract import (
    NativeFormatRecipeRequest as NativeFormatRecipeRequest,
    PreparedFormatOperation,
    is_native_format_workflow,
)
from hwp_live_native_format_inputs import InputFailure
from hwp_live_native_history import execute_native_history
from hwp_live_native_format_prepare import (
    PreparationFailure,
    prepare_native_format_operation,
)
from hwp_live_native_table_topology import (
    table_formula_selection_region,
    table_topology,
    verify_merge_transition,
    verify_split_preflight,
    verify_split_transition,
)
from hwp_live_native_format_target import TargetFailure
from hwp_operation_certification import certified_recipe
from hwp_operation_contract import OperationResult, OperationStatus
from hwp_operation_registry import operation_registry


def _result(
    request: NativeFormatRecipeRequest,
    status: OperationStatus,
    message: str,
    *,
    required_inputs: tuple[str, ...] = (),
) -> OperationResult:
    workflow = request.resolution.workflow_id
    recipe = None if workflow is None else certified_recipe(workflow)
    return OperationResult(
        status=status,
        query=request.resolution.query,
        registry_entries=operation_registry().count,
        lookup_microseconds=request.resolution.lookup_microseconds,
        workflow_candidates=request.resolution.candidates,
        required_inputs=required_inputs,
        message=message,
        recipe_id=None if recipe is None else f"recipe:{recipe.recipe_id}",
        recipe_steps=request.resolution.steps,
    )


def _failure_result(
    request: NativeFormatRecipeRequest,
    failure: PreparationFailure,
) -> OperationResult:
    if isinstance(failure, InputFailure):
        return _result(
            request,
            failure.status,
            failure.message,
            required_inputs=failure.required_inputs,
        )
    return _result(
        request,
        failure.status,
        failure.message,
        required_inputs=failure.required_inputs,
    )


def _verify_structural_plan(
    prepared: PreparedFormatOperation,
    window_handle: int,
    before_detail: NativeDetailedInspection | None,
    after_page: int,
) -> None:
    plan = prepared.plan
    if isinstance(plan, TableFormatCommandPlan):
        formatting = plan.formatting
        if (
            formatting.row_height_mm is None
            and formatting.column_width_mm is None
        ):
            return
        detail = inspect_native_structure(window_handle, after_page)
        if detail is None:
            raise HwpLiveError(
                "표 행·열 크기 변경 후 실제 셀 속성을 읽지 못했습니다"
            )
        cells = tuple(
            item
            for item in detail.cells
            if item.table_instance_id == plan.table.instance_id
            and item.address in plan.cells
        )
        if len(cells) != len(plan.cells):
            raise HwpLiveError("표 행·열 크기 변경 후 대상 셀을 다시 찾지 못했습니다")
        expected_width = (
            None
            if formatting.column_width_mm is None
            else round(formatting.column_width_mm * 283.4645669)
        )
        expected_height = (
            None
            if formatting.row_height_mm is None
            else round(formatting.row_height_mm * 283.4645669)
        )
        if expected_width is not None and any(
            cell.width_hwpunit is None
            or abs(
                cell.width_hwpunit - expected_width * cell.column_span
            ) > cell.column_span
            for cell in cells
        ):
            raise HwpLiveError(
                "요청한 열 너비와 한컴의 실제 셀 너비가 일치하지 않습니다"
            )
        if expected_height is not None and any(
            cell.height_hwpunit is None
            or abs(
                cell.height_hwpunit - expected_height * cell.row_span
            ) > cell.row_span
            for cell in cells
        ):
            raise HwpLiveError(
                "요청한 행 높이와 한컴의 실제 셀 높이가 일치하지 않습니다"
            )
        return
    if isinstance(plan, (MergeCommandPlan, SplitCommandPlan)):
        table = plan.table
        if before_detail is None:
            raise HwpLiveError("표 구조 변경 전 대상 표의 실제 셀 구조가 없습니다")
        before_topology = table_topology(before_detail, table.instance_id)
        try:
            detail = inspect_native_structure(window_handle, after_page)
            if detail is None or not any(
                control.instance_id == table.instance_id for control in detail.controls
            ):
                raise HwpLiveError(
                    "표 구조 변경 후 대상 표를 네이티브 구조에서 확인하지 못했습니다"
                )
            after_topology = table_topology(detail, table.instance_id)
            if isinstance(plan, MergeCommandPlan):
                verify_merge_transition(
                    before_topology,
                    after_topology,
                    plan.merge.start,
                    plan.merge.end,
                )
            else:
                verify_split_transition(before_topology, after_topology, plan.split)
        except HwpLiveError as verification_error:
            try:
                _ = execute_native_history(window_handle, "undo", 1)
                restored = inspect_native_structure(window_handle, table.page)
                if (
                    restored is None
                    or table_topology(restored, table.instance_id) != before_topology
                ):
                    raise HwpLiveError(
                        "자동 Undo 후 작업 전 CellTopology가 복원되지 않았습니다"
                    )
            except HwpLiveError as rollback_error:
                raise HwpLiveError(
                    f"{verification_error}. 자동 Undo 복구 검증에도 실패했습니다: {rollback_error}"
                ) from rollback_error
            raise HwpLiveError(
                f"{verification_error}. 자동 Undo로 작업 전 표 구조를 복구했습니다"
            ) from verification_error
        return
    return


def topology_preflight(
    prepared: PreparedFormatOperation,
    before_detail: NativeDetailedInspection,
) -> InputFailure | None:
    plan = prepared.plan
    try:
        match plan:  # noqa: E501  # noqa: MATCH_OK — closed union is fully enumerated
            case MergeCommandPlan():
                _ = table_topology(
                    before_detail,
                    plan.table.instance_id,
                ).merge_region(plan.merge.start, plan.merge.end)
            case SplitCommandPlan():
                verify_split_preflight(
                    table_topology(before_detail, plan.table.instance_id),
                    plan.split,
                )
            case TableFormatCommandPlan() | TextFormatCommandPlan():
                pass
    except HwpLiveError as error:
        return InputFailure("schema_conflict", str(error))
    return None


def _resolve_selected_table_cells(
    prepared: PreparedFormatOperation,
    before: NativeSnapshot,
    detail: NativeDetailedInspection,
    application: HwpComApplication | None = None,
) -> PreparedFormatOperation | InputFailure:
    plan = prepared.plan
    if not isinstance(plan, TableFormatCommandPlan) or plan.cells:
        return prepared
    try:
        topology = table_topology(detail, plan.table.instance_id)
        selection_mode = before.selection.mode
        if selection_mode == 0 and application is not None:
            selection_mode = int(application.SelectionMode)
        base_mode = selection_mode & 0x0F
        strict_selection = bool(selection_mode & 0x10)
        if base_mode == 3:
            if before.selection.cell_addresses:
                addresses = topology.selection_region_by_addresses(
                    before.selection.cell_addresses
                )
            elif before.selection.cell_address_error:
                raise HwpLiveError(before.selection.cell_address_error)
            elif before.selection.selected:
                addresses = topology.selection_region_by_list_ids(
                    before.selection.start.list_id,
                    before.selection.end.list_id,
                )
            elif strict_selection and application is not None:
                addresses = table_formula_selection_region(application, topology)
            else:
                addresses = topology.selection_region_by_list_ids(
                    before.selection.start.list_id,
                    before.selection.end.list_id,
                )
        elif base_mode == 4 and before.control_type == "tbl":
            addresses = tuple(cell.address for cell in topology.cells)
        else:
            return InputFailure(
                "needs_input",
                "현재 선택한 표 셀 범위를 확인하지 못했습니다",
                ("inputs.parameters.cell",),
            )
    except HwpLiveError as error:
        return InputFailure("schema_conflict", str(error))
    if not addresses:
        return InputFailure(
            "needs_input",
            "현재 선택한 표 셀이 없습니다",
            ("inputs.parameters.cell",),
        )
    resolved_plan = TableFormatCommandPlan(plan.formatting, plan.table, addresses)
    return PreparedFormatOperation(
        resolved_plan,
        prepared.target_id,
        prepared.target_basis,
        addresses,
    )


def _execute_prepared(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
    prepared: PreparedFormatOperation,
) -> OperationResult:
    plan = prepared.plan
    detail_page: int | None = None
    match plan:  # noqa: E501  # noqa: MATCH_OK — closed union is fully enumerated
        case TableFormatCommandPlan():
            detail_page = plan.table.page if not plan.cells else None
        case MergeCommandPlan() | SplitCommandPlan():
            detail_page = plan.table.page
        case TextFormatCommandPlan():
            pass
    before_detail = (
        inspect_native_structure(request.candidate.window_handle, detail_page)
        if detail_page is not None
        else None
    )
    if detail_page is not None and before_detail is None:
        raise HwpLiveError("표 구조 변경 전 대상 표의 실제 셀 구조를 읽지 못했습니다")
    if isinstance(plan, TableFormatCommandPlan) and not plan.cells:
        assert before_detail is not None
        selected = _resolve_selected_table_cells(
            prepared,
            before,
            before_detail,
            request.candidate.application,
        )
        if isinstance(selected, InputFailure):
            return _failure_result(request, selected).model_copy(
                update={
                    "verified": False,
                    "commands_executed": 0,
                    "commands_completed": 0,
                    "current_page": before.current_page,
                    "page_count": before.page_count,
                    "modified": before.modified,
                    "partial_mutation": False,
                    "retry_safe": True,
                    "resolved_target_id": prepared.target_id,
                    "target_resolution_basis": prepared.target_basis,
                }
            )
        prepared = selected
        plan = prepared.plan
    if isinstance(plan, (MergeCommandPlan, SplitCommandPlan)):
        assert before_detail is not None
        preflight = topology_preflight(prepared, before_detail)
        if preflight is not None:
            return _failure_result(request, preflight).model_copy(
                update={
                    "verified": False,
                    "commands_executed": 0,
                    "commands_completed": 0,
                    "current_page": before.current_page,
                    "page_count": before.page_count,
                    "modified": before.modified,
                    "partial_mutation": False,
                    "retry_safe": True,
                    "resolved_target_id": prepared.target_id,
                    "target_resolution_basis": prepared.target_basis,
                }
            )
    commands = build_native_format_commands(prepared.plan)
    native = execute_native_actions(
        request.candidate.window_handle,
        NativeActionRequest(
            request.routing_page.document_id,
            request.routing_page.full_name,
            commands,
        ),
        minimum_version=9,
    )
    if native is None:
        raise HwpLiveError(
            "한컴 프로토콜 9 네이티브 서식 recipe를 사용할 수 없습니다"
        )
    after = read_native_snapshot(request.candidate.window_handle)
    if after is None:
        raise HwpLiveError("네이티브 서식 작업 후 문서 상태를 읽지 못했습니다")
    if (
        request.postconditions.preserve_page_count
        and after.page_count != before.page_count
    ):
        raise HwpLiveError(
            "서식 작업 후 페이지 수 보존 완료조건을 만족하지 못했습니다"
        )
    _verify_structural_plan(
        prepared,
        request.candidate.window_handle,
        before_detail,
        after.current_page,
    )
    return _result(
        request,
        "executed",
        "프로토콜 9 C++/ATL 네이티브 서식 recipe를 실행하고 검증했습니다",
    ).model_copy(
        update={
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": "native_snapshot_before_after",
            "verified": True,
            "commands_executed": native.commands_executed,
            "commands_completed": native.commands_executed,
            "native_elapsed_microseconds": native.elapsed_microseconds,
            "current_page": after.current_page,
            "page_count": after.page_count,
            "modified": after.modified,
            "partial_mutation": False,
            "retry_safe": True,
            "resolved_target_id": prepared.target_id,
            "target_resolution_basis": prepared.target_basis,
            "updated_addresses": prepared.updated_addresses,
        }
    )


def operate_native_format_recipe(
    request: NativeFormatRecipeRequest,
) -> OperationResult | None:
    workflow = request.resolution.workflow_id
    if not is_native_format_workflow(workflow):
        return None
    if request.resolve_only:
        return _result(
            request,
            "resolved",
            "인증된 프로토콜 9 네이티브 서식 recipe를 확정했습니다",
        )
    if not request.allow_document_change:
        return _result(
            request,
            "confirmation_required",
            "문서 서식이나 표 구조를 변경하는 작업입니다",
        )
    before = read_native_snapshot(request.candidate.window_handle)
    if before is None:
        raise HwpLiveError("네이티브 서식 작업 전 문서 상태를 읽지 못했습니다")
    prepared = prepare_native_format_operation(workflow, request, before)
    if isinstance(prepared, (InputFailure, TargetFailure)):
        return _failure_result(request, prepared)
    return _execute_prepared(request, before, prepared)

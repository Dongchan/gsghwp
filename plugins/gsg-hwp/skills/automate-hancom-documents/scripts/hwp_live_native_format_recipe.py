from __future__ import annotations

from hwp_errors import HwpLiveError
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
    build_native_format_commands,
)
from hwp_live_native_format_contract import (
    NativeFormatRecipeRequest as NativeFormatRecipeRequest,
    PreparedFormatOperation,
    is_native_format_workflow,
)
from hwp_live_native_format_inputs import InputFailure
from hwp_live_native_format_inputs import table_cell_coordinate
from hwp_live_native_format_prepare import (
    PreparationFailure,
    prepare_native_format_operation,
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
) -> None:
    plan = prepared.plan
    if isinstance(plan, TableFormatCommandPlan):
        formatting = plan.formatting
        if (
            formatting.row_height_mm is None
            and formatting.column_width_mm is None
        ):
            return
        detail = inspect_native_structure(window_handle, plan.table.page)
        if detail is None:
            raise HwpLiveError(
                "표 행·열 크기 변경 후 실제 셀 속성을 읽지 못했습니다"
            )
        cell = next(
            (
                item
                for item in detail.cells
                if item.table_instance_id == plan.table.instance_id
                and item.address == formatting.cell
            ),
            None,
        )
        if cell is None:
            raise HwpLiveError(
                "표 행·열 크기 변경 후 대상 셀을 다시 찾지 못했습니다"
            )
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
        if (
            expected_width is not None
            and (
                cell.width_hwpunit is None
                or abs(cell.width_hwpunit - expected_width) > 1
            )
        ):
            raise HwpLiveError(
                "요청한 열 너비와 한컴의 실제 셀 너비가 일치하지 않습니다"
            )
        if (
            expected_height is not None
            and (
                cell.height_hwpunit is None
                or abs(cell.height_hwpunit - expected_height) > 1
            )
        ):
            raise HwpLiveError(
                "요청한 행 높이와 한컴의 실제 셀 높이가 일치하지 않습니다"
            )
        return
    if isinstance(plan, (MergeCommandPlan, SplitCommandPlan)):
        table = plan.table
        detail = inspect_native_structure(window_handle, table.page)
        if before_detail is None or detail is None or not any(
            control.instance_id == table.instance_id for control in detail.controls
        ):
            raise HwpLiveError(
                "표 구조 변경 후 대상 표를 네이티브 구조에서 확인하지 못했습니다"
            )
        before_cells = tuple(
            cell
            for cell in before_detail.cells
            if cell.table_instance_id == table.instance_id
        )
        after_cells = tuple(
            cell for cell in detail.cells if cell.table_instance_id == table.instance_id
        )
        if isinstance(plan, MergeCommandPlan):
            start_row, start_column = table_cell_coordinate(plan.merge.start)
            end_row, end_column = table_cell_coordinate(plan.merge.end)
            merged_cells = tuple(
                cell
                for cell in before_cells
                if start_row
                <= table_cell_coordinate(cell.address)[0]
                <= end_row
                and start_column
                <= table_cell_coordinate(cell.address)[1]
                <= end_column
            )
            anchor = next(
                (cell for cell in after_cells if cell.address == plan.merge.start),
                None,
            )
            if (
                len(merged_cells) < 2
                or len(after_cells) != len(before_cells) - len(merged_cells) + 1
                or anchor is None
                or anchor.row_span != end_row - start_row + 1
                or anchor.column_span != end_column - start_column + 1
            ):
                raise HwpLiveError(
                    "요청한 셀 범위가 실제 한컴 표에서 병합되지 않았습니다"
                )
            return
        expected_count = len(before_cells) + plan.split.columns * plan.split.rows - 1
        before_cell = next(
            (cell for cell in before_cells if cell.address == plan.split.cell),
            None,
        )
        after_cell = next(
            (cell for cell in after_cells if cell.address == plan.split.cell),
            None,
        )
        if (
            before_cell is None
            or after_cell is None
            or len(after_cells) != expected_count
            or before_cell.text.strip() not in after_cell.text
        ):
            raise HwpLiveError(
                "요청한 셀 하나가 실제 한컴 표에서 지정한 칸·줄 수로 나뉘지 않았습니다"
            )
        return
    return


def _execute_prepared(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
    prepared: PreparedFormatOperation,
) -> OperationResult:
    plan = prepared.plan
    before_detail = (
        inspect_native_structure(request.candidate.window_handle, plan.table.page)
        if isinstance(plan, (MergeCommandPlan, SplitCommandPlan))
        else None
    )
    if isinstance(plan, (MergeCommandPlan, SplitCommandPlan)) and before_detail is None:
        raise HwpLiveError("표 구조 변경 전 대상 표의 실제 셀 구조를 읽지 못했습니다")
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

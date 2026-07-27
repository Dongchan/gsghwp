from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — native format state machine; splitting obscures mutation ordering

from dataclasses import replace

from hwp_errors import HwpLiveError
from hwp_live_api import HwpComApplication
from hwp_live_native_action_contract import NativeActionFailure
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
    AxisSizeTarget,
    CellRangeTarget,
    MergeCommandPlan,
    NativeFormatCommandBatch,
    NativeFormatExecutionPlan,
    SplitCommandPlan,
    TableFormatCommandPlan,
    TextFormatCommandPlan,
    build_native_format_commands,
    build_native_format_execution_plan,
)
from hwp_live_native_format_contract import (
    NativeFormatRecipeRequest as NativeFormatRecipeRequest,
    NativeFormatWorkflow,
    PreparedFormatOperation,
    is_native_format_workflow,
)
from hwp_live_native_format_inputs import (
    InputFailure,
    MergeSpec,
    table_cell_coordinate,
)
from hwp_live_native_history import execute_native_history
from hwp_live_native_format_prepare import (
    PreparationFailure,
    has_explicit_table_locator,
    prepare_native_format_operation,
)
from hwp_live_native_table_topology import (
    table_formula_selection_region,
    table_topology,
    verify_merge_transition,
    verify_split_preflight,
    verify_split_transition,
)
from hwp_live_native_format_target import (
    NativeTableTargetRequest,
    TargetFailure,
    resolve_native_table_target,
)
from hwp_live_text_format_verification import verify_text_format
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
        if formatting.row_height_mm is None and formatting.column_width_mm is None:
            return
        detail = inspect_native_structure(window_handle, after_page)
        if detail is None:
            raise HwpLiveError("표 행·열 크기 변경 후 실제 셀 속성을 읽지 못했습니다")
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
            or abs(cell.width_hwpunit - expected_width * cell.column_span)
            > cell.column_span
            for cell in cells
        ):
            raise HwpLiveError(
                "요청한 열 너비와 한컴의 실제 셀 너비가 일치하지 않습니다"
            )
        if expected_height is not None and any(
            cell.height_hwpunit is None
            or abs(cell.height_hwpunit - expected_height * cell.row_span)
            > cell.row_span
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
        # Only a CellTopology we actually read back proves this call mutated the
        # table. Without that proof the Undo below may have reverted an earlier,
        # unrelated edit instead, so no "unchanged" claim may be made.
        observed_mutation = False
        try:
            detail = inspect_native_structure(window_handle, after_page)
            if detail is None or not any(
                control.instance_id == table.instance_id for control in detail.controls
            ):
                raise HwpLiveError(
                    "표 구조 변경 후 대상 표를 네이티브 구조에서 확인하지 못했습니다"
                )
            after_topology = table_topology(detail, table.instance_id)
            observed_mutation = after_topology != before_topology
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
            if not observed_mutation:
                raise HwpLiveError(
                    f"{verification_error}. 자동 Undo를 실행했지만 이 호출이 표를"
                    " 바꿨다는 네이티브 증거가 없어 변경 여부를 확정하지 못했습니다"
                ) from verification_error
            raise HwpLiveError(
                f"{verification_error}. 자동 Undo로 작업 전 표 구조를 복구했습니다"
                "; mutation_started=false; 문서는 작업 전 상태입니다",
                mutation_started=False,
            ) from verification_error
        return
    return


def _selected_structure_addresses(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
) -> tuple[str, ...] | InputFailure:
    """Cell addresses the live selection covers inside the target table.

    This resolves, it does not validate. The only judgement is made by
    ``TableTopology.selection_region_by_list_ids``, the same function an
    explicit address pair reaches through ``topology_preflight``: it already
    refuses a selection whose endpoints are not cells of this table (selection
    outside the table, or spanning two tables) and a region that is not a
    gap-free rectangle. Adding a second copy of those rules here would let the
    selection path drift away from the address path, so there is none.

    An empty result means the selection cannot supply what the caller omitted.
    Nothing is rejected for that; the request is left exactly as it arrived so
    the ordinary missing-input failure reports it.
    """
    resolved = resolve_native_table_target(
        NativeTableTargetRequest(
            candidate=request.candidate,
            routing_page=request.routing_page,
            target=request.target,
            snapshot_control_type=before.control_type,
            snapshot_control_id=before.control_instance_id,
        )
    )
    if isinstance(resolved, TargetFailure):
        # prepare_native_format_operation resolves the same target and reports
        # this failure itself, so it is not duplicated here.
        return ()
    detail = inspect_native_structure(request.candidate.window_handle, resolved.page)
    if detail is None:
        return ()
    selection = before.selection
    try:
        return table_topology(
            detail,
            resolved.instance_id,
        ).selection_region_by_list_ids(
            selection.start.list_id,
            selection.end.list_id,
        )
    except HwpLiveError as error:
        return InputFailure("schema_conflict", str(error))


def _request_with_selected_cells(
    workflow: NativeFormatWorkflow,
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
) -> NativeFormatRecipeRequest | InputFailure:
    """Fill omitted merge/split cell addresses from the live cell selection.

    Addresses the caller supplied are never touched, so a request that carries
    them takes byte-identical parameters into
    ``prepare_native_format_operation``.
    """
    if workflow == "table.merge_cells":
        if "start" in request.parameters or "end" in request.parameters:
            return request
    elif workflow == "table.split_cells":
        if "cell" in request.parameters:
            return request
    else:
        return request
    region = _selected_structure_addresses(request, before)
    if isinstance(region, InputFailure):
        return region
    if not region:
        return request
    if workflow == "table.merge_cells":
        # A caret with no cell block resolves to one cell, so start equals end
        # and parse_merge rejects it exactly as it rejects a caller that sent
        # the same address twice.
        added = {"start": region[0], "end": region[-1]}
    elif len(region) == 1:
        added = {"cell": region[0]}
    else:
        # A multi-cell selection does not name the one cell a split applies to.
        # Nothing is injected, so parse_split reports the missing input.
        return request
    return replace(request, parameters={**request.parameters, **added})


def _normalized_merge_plan(
    prepared: PreparedFormatOperation,
    before_detail: NativeDetailedInspection,
) -> PreparedFormatOperation | InputFailure:
    """Restore the rectangle when the two merge corners arrive back to front.

    A merged cell covers slots its own address never names, so the rectangle
    cannot be recovered by sorting the two address strings. The corners are
    resolved against the live CellTopology instead, which rejects anything that
    is not a gap-free rectangle of whole cells.
    """
    plan = prepared.plan
    assert isinstance(plan, MergeCommandPlan)
    start, end = plan.merge.start, plan.merge.end
    start_row, start_column = table_cell_coordinate(start)
    end_row, end_column = table_cell_coordinate(end)
    if end_row >= start_row and end_column >= start_column:
        return prepared
    try:
        region = table_topology(
            before_detail,
            plan.table.instance_id,
        ).selection_region_by_addresses((start, end))
    except HwpLiveError as error:
        return InputFailure("schema_conflict", str(error))
    if len(region) < 2:
        return InputFailure(
            "schema_conflict",
            "병합 시작·끝 주소가 서로 다른 두 셀을 덮지 않습니다",
        )
    return PreparedFormatOperation(
        MergeCommandPlan(MergeSpec(region[0], region[-1]), plan.table),
        prepared.target_id,
        prepared.target_basis,
        (region[0], region[-1]),
    )


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


def _selected_cells_failure(
    error: HwpLiveError,
    cell_address_error: str,
) -> HwpLiveError:
    reason = error.reason.partition(" 표 서식은 ")[0].rstrip()
    native_reason = cell_address_error.strip()
    if native_reason and native_reason not in reason:
        reason = f"{reason}. 네이티브 셀 주소 검사 오류: {native_reason}"
    return HwpLiveError(reason, mutation_started=error.mutation_started)


def _resolve_selected_table_cells(
    prepared: PreparedFormatOperation,
    before: NativeSnapshot,
    detail: NativeDetailedInspection,
    application: HwpComApplication | None = None,
    *,
    explicit_target: bool = False,
) -> PreparedFormatOperation | InputFailure:
    plan = prepared.plan
    if not isinstance(plan, TableFormatCommandPlan) or plan.cells:
        return prepared
    selection_mode = before.selection.mode
    base_mode = selection_mode & 0x0F
    strict_selection = bool(selection_mode & 0x10)
    try:
        topology = table_topology(detail, plan.table.instance_id)
        if explicit_target:
            addresses = tuple(cell.address for cell in topology.cells)
        else:
            if selection_mode == 0 and application is not None:
                selection_mode = int(application.SelectionMode)
                base_mode = selection_mode & 0x0F
                strict_selection = bool(selection_mode & 0x10)
            if base_mode == 3:
                if before.selection.cell_addresses:
                    addresses = topology.selection_region_by_addresses(
                        before.selection.cell_addresses
                    )
                elif before.selection.selected:
                    addresses = topology.selection_region_by_list_ids(
                        before.selection.start.list_id,
                        before.selection.end.list_id,
                    )
                elif before.selection.cell_address_error:
                    raise HwpLiveError(before.selection.cell_address_error)
                elif strict_selection and application is not None:
                    addresses = table_formula_selection_region(application, topology)
                else:
                    raise HwpLiveError("현재 선택한 표 셀 범위를 확인하지 못했습니다")
            elif base_mode == 4 and before.control_type == "tbl":
                addresses = tuple(cell.address for cell in topology.cells)
            else:
                return InputFailure(
                    "needs_input",
                    "현재 선택한 표 셀 범위를 확인하지 못했습니다",
                    ("inputs.parameters.cell",),
                )
    except HwpLiveError as error:
        if not explicit_target and base_mode == 3:
            error = _selected_cells_failure(
                error,
                before.selection.cell_address_error,
            )
        return InputFailure("schema_conflict", str(error))
    if not addresses:
        if explicit_target:
            return InputFailure(
                "schema_conflict",
                "지정한 표의 실제 셀 주소를 네이티브 topology에서 확인하지 못했습니다",
            )
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


def _fallback_axis_targets(
    cells: tuple[str, ...],
    *,
    column: bool,
) -> tuple[AxisSizeTarget, ...]:
    label = "column" if column else "row"
    return tuple(
        AxisSizeTarget(
            f"{label}:fallback:{index}",
            address,
            (address,),
        )
        for index, address in enumerate(cells)
    )


def _topology_axis_targets(
    prepared: PreparedFormatOperation,
    detail: NativeDetailedInspection,
    *,
    column: bool,
) -> tuple[AxisSizeTarget, ...]:
    plan = prepared.plan
    assert isinstance(plan, TableFormatCommandPlan)
    topology = table_topology(detail, plan.table.instance_id)
    selected = tuple(topology.by_address.get(address.upper()) for address in plan.cells)
    if any(cell is None for cell in selected):
        raise HwpLiveError("표 크기 변경 대상 셀이 실제 CellTopology에 없습니다")
    physical = tuple(cell for cell in selected if cell is not None)
    axes = sorted(
        {
            axis
            for cell in physical
            for axis in range(
                (
                    table_cell_coordinate(cell.address)[1]
                    if column
                    else table_cell_coordinate(cell.address)[0]
                ),
                (
                    table_cell_coordinate(cell.address)[1] + cell.column_span
                    if column
                    else table_cell_coordinate(cell.address)[0] + cell.row_span
                ),
            )
        }
    )
    label = "column" if column else "row"
    targets: list[AxisSizeTarget] = []
    for axis in axes:
        anchor = next(
            (
                cell
                for cell in topology.cells
                if (
                    table_cell_coordinate(cell.address)[1]
                    if column
                    else table_cell_coordinate(cell.address)[0]
                )
                == axis
                and (cell.column_span if column else cell.row_span) == 1
            ),
            None,
        )
        if anchor is None:
            return _fallback_axis_targets(plan.cells, column=column)
        affected = tuple(
            cell.address
            for cell in physical
            if (
                (
                    table_cell_coordinate(cell.address)[1]
                    if column
                    else table_cell_coordinate(cell.address)[0]
                )
                <= axis
                < (
                    table_cell_coordinate(cell.address)[1] + cell.column_span
                    if column
                    else table_cell_coordinate(cell.address)[0] + cell.row_span
                )
            )
        )
        targets.append(
            AxisSizeTarget(
                f"{label}:{axis}",
                anchor.address,
                affected,
            )
        )
    return tuple(targets)


def _with_topology_size_targets(
    prepared: PreparedFormatOperation,
    detail: NativeDetailedInspection,
) -> PreparedFormatOperation:
    plan = prepared.plan
    if not isinstance(plan, TableFormatCommandPlan) or len(plan.cells) < 2:
        return prepared
    formatted = plan.formatting
    column_targets = (
        None
        if formatted.column_width_mm is None
        else _topology_axis_targets(prepared, detail, column=True)
    )
    row_targets = (
        None
        if formatted.row_height_mm is None
        else _topology_axis_targets(prepared, detail, column=False)
    )
    if column_targets is None and row_targets is None:
        return prepared
    resolved_plan = TableFormatCommandPlan(
        plan.formatting,
        plan.table,
        plan.cells,
        column_targets,
        row_targets,
        plan.cell_geometry_targets,
        plan.table_cell_count,
    )
    return PreparedFormatOperation(
        resolved_plan,
        prepared.target_id,
        prepared.target_basis,
        prepared.updated_addresses,
    )


def _topology_cell_geometry_targets(
    prepared: PreparedFormatOperation,
    detail: NativeDetailedInspection,
) -> tuple[tuple[CellRangeTarget, ...] | None, int]:
    plan = prepared.plan
    assert isinstance(plan, TableFormatCommandPlan)
    topology = table_topology(detail, plan.table.instance_id)
    selected = tuple(topology.by_address.get(address.upper()) for address in plan.cells)
    if any(cell is None for cell in selected):
        raise HwpLiveError("셀 geometry 변경 대상이 실제 CellTopology에 없습니다")
    physical = tuple(cell for cell in selected if cell is not None)
    if any(cell.row_span != 1 or cell.column_span != 1 for cell in physical):
        # NativeDetailedCell does not expose the native right/down neighbour
        # graph. A merged rectangle therefore cannot prove the exact block
        # extension path in Python and must retain the cell-local sequence.
        return None, topology.cell_count
    try:
        region = topology.selection_region_by_addresses(plan.cells)
    except HwpLiveError:
        return None, topology.cell_count
    if frozenset(region) != frozenset(address.upper() for address in plan.cells):
        return None, topology.cell_count
    top = min(table_cell_coordinate(cell.address)[0] for cell in physical)
    left = min(table_cell_coordinate(cell.address)[1] for cell in physical)
    bottom = max(
        table_cell_coordinate(cell.address)[0] + cell.row_span - 1 for cell in physical
    )
    right = max(
        table_cell_coordinate(cell.address)[1] + cell.column_span - 1
        for cell in physical
    )

    def cell_at(row: int, column: int):
        return next(
            (
                cell
                for cell in topology.cells
                if table_cell_coordinate(cell.address)[0]
                <= row
                < table_cell_coordinate(cell.address)[0] + cell.row_span
                and table_cell_coordinate(cell.address)[1]
                <= column
                < table_cell_coordinate(cell.address)[1] + cell.column_span
            ),
            None,
        )

    first = cell_at(top, left)
    endpoint = cell_at(bottom, right)
    if first is None or endpoint is None:
        return None, topology.cell_count
    current = first
    right_steps = 0
    while table_cell_coordinate(current.address)[1] + current.column_span - 1 < right:
        next_column = table_cell_coordinate(current.address)[1] + current.column_span
        next_cell = cell_at(top, next_column)
        if next_cell is None or next_cell is current:
            return None, topology.cell_count
        right_steps += 1
        current = next_cell
    down_steps = 0
    while table_cell_coordinate(current.address)[0] + current.row_span - 1 < bottom:
        next_row = table_cell_coordinate(current.address)[0] + current.row_span
        next_cell = cell_at(next_row, right)
        if next_cell is None or next_cell is current:
            return None, topology.cell_count
        down_steps += 1
        current = next_cell
    if current.address != endpoint.address:
        return None, topology.cell_count
    return (
        (
            CellRangeTarget(
                "cell_geometry:range",
                first.address,
                right_steps,
                down_steps,
                plan.cells,
            ),
        ),
        topology.cell_count,
    )


def _with_topology_cell_geometry_targets(
    prepared: PreparedFormatOperation,
    detail: NativeDetailedInspection,
) -> PreparedFormatOperation:
    plan = prepared.plan
    if (
        not isinstance(plan, TableFormatCommandPlan)
        or len(plan.cells) < 2
        or plan.formatting.formatting.padding is None
    ):
        return prepared
    targets, table_cell_count = _topology_cell_geometry_targets(prepared, detail)
    resolved_plan = TableFormatCommandPlan(
        plan.formatting,
        plan.table,
        plan.cells,
        plan.column_size_targets,
        plan.row_size_targets,
        targets,
        table_cell_count,
    )
    return PreparedFormatOperation(
        resolved_plan,
        prepared.target_id,
        prepared.target_basis,
        prepared.updated_addresses,
    )


def _pre_mutation_failure(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
    prepared: PreparedFormatOperation,
    failure: InputFailure,
) -> OperationResult:
    """Envelope for a rejection raised before any native command ran."""
    return _failure_result(request, failure).model_copy(
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


def _failed_table_format_batch(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
    prepared: PreparedFormatOperation,
    execution: NativeFormatExecutionPlan,
    batch: NativeFormatCommandBatch,
    batch_index: int,
    completed_group_keys: set[str],
    commands_completed_before: int,
    elapsed_before: int,
    error: HwpLiveError | None,
) -> OperationResult:
    failure_commands = (
        min(error.commands_completed, len(batch.commands))
        if isinstance(error, NativeActionFailure)
        else 0
    )
    completed_group_keys.update(batch.completed_group_keys(failure_commands))
    affected_addresses = execution.affected_addresses(completed_group_keys)
    uncertain_addresses = (
        batch.uncertain_addresses(failure_commands)
        if isinstance(error, NativeActionFailure) and error.partial_mutation
        else ()
    )
    prior_mutation = bool(completed_group_keys)
    if error is None:
        partial_mutation: bool | None = prior_mutation
        retry_safe = not prior_mutation
        failed_step = "native_format_protocol"
        detail = "프로토콜 9 네이티브 실행기를 사용할 수 없습니다"
    elif isinstance(error, NativeActionFailure):
        partial_mutation = prior_mutation or error.partial_mutation
        retry_safe = error.retry_safe and not partial_mutation
        failed_step = error.failed_step or error.location or "native_format_batch"
        detail = str(error)
    else:
        partial_mutation = True if prior_mutation else None
        retry_safe = False
        failed_step = "native_format_batch"
        detail = str(error)
    detail = detail[:1_000]
    uncertain_preview = ", ".join(uncertain_addresses[:8])
    uncertain = (
        (
            uncertain_preview
            if len(uncertain_addresses) <= 8
            else f"{len(uncertain_addresses)}개 중 {uncertain_preview}, ..."
        )
        if uncertain_addresses
        else "없음(실패 명령 내부 적용 여부는 네이티브 증거가 없으면 확정 불가)"
    )
    message = (
        f"표 서식 호출 {batch_index + 1}/{len(execution.batches)}에서 실패했습니다. "
        f"완료 명령 그룹이 적용한 주소 {len(affected_addresses)}개, "
        f"실패 그룹 적용 불확실 주소: {uncertain}. {detail}"
    )
    commands_completed = commands_completed_before + failure_commands
    return _result(
        request,
        "partial_change" if partial_mutation is True else "operation_failed",
        message,
    ).model_copy(
        update={
            "changed": partial_mutation is True,
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": "native_operation_specific_readback",
            "verified": False,
            "commands_executed": commands_completed,
            "commands_completed": commands_completed,
            "native_elapsed_microseconds": elapsed_before,
            "current_page": before.current_page,
            "page_count": before.page_count,
            "modified": True if partial_mutation is True else before.modified,
            "partial_change": partial_mutation is True,
            "partial_mutation": partial_mutation,
            "retry_safe": retry_safe,
            "reconcile_required": partial_mutation is not False,
            "failed_step": failed_step,
            "structure_digest_before": (
                error.structure_digest_before
                if isinstance(error, NativeActionFailure)
                else None
            ),
            "structure_digest_after": (
                error.structure_digest_after
                if isinstance(error, NativeActionFailure)
                else None
            ),
            "resolved_target_id": prepared.target_id,
            "target_resolution_basis": prepared.target_basis,
            "updated_addresses": affected_addresses,
        }
    )


def _execute_table_format_batches(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
    prepared: PreparedFormatOperation,
) -> tuple[int, int, int] | OperationResult:
    plan = prepared.plan
    assert isinstance(plan, TableFormatCommandPlan)
    execution = build_native_format_execution_plan(plan)
    completed_group_keys: set[str] = set()
    commands_completed = 0
    elapsed_microseconds = 0
    for batch_index, batch in enumerate(execution.batches):
        try:
            native = execute_native_actions(
                request.candidate.window_handle,
                NativeActionRequest(
                    request.routing_page.document_id,
                    request.routing_page.full_name,
                    batch.commands,
                ),
                minimum_version=9,
            )
        except HwpLiveError as error:
            return _failed_table_format_batch(
                request,
                before,
                prepared,
                execution,
                batch,
                batch_index,
                completed_group_keys,
                commands_completed,
                elapsed_microseconds,
                error,
            )
        if native is None:
            return _failed_table_format_batch(
                request,
                before,
                prepared,
                execution,
                batch,
                batch_index,
                completed_group_keys,
                commands_completed,
                elapsed_microseconds,
                None,
            )
        commands_completed += native.commands_executed
        elapsed_microseconds += native.elapsed_microseconds
        completed_group_keys.update(item.group.key for item in batch.groups)
    return commands_completed, elapsed_microseconds, len(execution.batches)


def _execute_prepared(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
    prepared: PreparedFormatOperation,
) -> OperationResult:
    plan = prepared.plan
    detail_page: int | None = None
    match plan:  # noqa: E501  # noqa: MATCH_OK — closed union is fully enumerated
        case TableFormatCommandPlan():
            formatted = plan.formatting
            needs_axis_topology = len(plan.cells) > 1 and (
                formatted.column_width_mm is not None
                or formatted.row_height_mm is not None
            )
            needs_cell_geometry_topology = (
                len(plan.cells) > 1 and formatted.formatting.padding is not None
            )
            detail_page = (
                plan.table.page
                if not plan.cells or needs_axis_topology or needs_cell_geometry_topology
                else None
            )
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
            explicit_target=has_explicit_table_locator(request),
        )
        if isinstance(selected, InputFailure):
            return _pre_mutation_failure(request, before, prepared, selected)
        prepared = selected
        plan = prepared.plan
    if (
        isinstance(plan, TableFormatCommandPlan)
        and before_detail is not None
        and len(plan.cells) > 1
        and (
            plan.formatting.column_width_mm is not None
            or plan.formatting.row_height_mm is not None
        )
    ):
        prepared = _with_topology_size_targets(prepared, before_detail)
        plan = prepared.plan
    if (
        isinstance(plan, TableFormatCommandPlan)
        and before_detail is not None
        and len(plan.cells) > 1
        and plan.formatting.formatting.padding is not None
    ):
        prepared = _with_topology_cell_geometry_targets(prepared, before_detail)
        plan = prepared.plan
    if isinstance(plan, (MergeCommandPlan, SplitCommandPlan)):
        assert before_detail is not None
        if isinstance(plan, MergeCommandPlan):
            normalized = _normalized_merge_plan(prepared, before_detail)
            if isinstance(normalized, InputFailure):
                return _pre_mutation_failure(request, before, prepared, normalized)
            prepared = normalized
            plan = prepared.plan
        preflight = topology_preflight(prepared, before_detail)
        if preflight is not None:
            return _pre_mutation_failure(request, before, prepared, preflight)
    if isinstance(plan, TableFormatCommandPlan):
        executed = _execute_table_format_batches(
            request,
            before,
            prepared,
        )
        if isinstance(executed, OperationResult):
            return executed
        commands_executed, elapsed_microseconds, native_call_count = executed
    else:
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
        commands_executed = native.commands_executed
        elapsed_microseconds = native.elapsed_microseconds
        native_call_count = 1
    after = read_native_snapshot(request.candidate.window_handle)
    if after is None:
        raise HwpLiveError("네이티브 서식 작업 후 문서 상태를 읽지 못했습니다")
    if (
        request.postconditions.preserve_page_count
        and after.page_count != before.page_count
    ):
        raise HwpLiveError("서식 작업 후 페이지 수 보존 완료조건을 만족하지 못했습니다")
    if isinstance(prepared.plan, TextFormatCommandPlan):
        verify_text_format(prepared.plan.formatting, before, after)
    _verify_structural_plan(
        prepared,
        request.candidate.window_handle,
        before_detail,
        after.current_page,
    )
    return _result(
        request,
        "executed",
        (
            "프로토콜 9 C++/ATL 네이티브 서식 recipe를 "
            f"{native_call_count}회 제한 호출로 실행하고 검증했습니다"
        ),
    ).model_copy(
        update={
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": "native_operation_specific_readback",
            "verified": True,
            "commands_executed": commands_executed,
            "commands_completed": commands_executed,
            "native_elapsed_microseconds": elapsed_microseconds,
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
    selected = _request_with_selected_cells(workflow, request, before)
    if isinstance(selected, InputFailure):
        return _failure_result(request, selected)
    request = selected
    prepared = prepare_native_format_operation(workflow, request, before)
    if isinstance(prepared, (InputFailure, TargetFailure)):
        return _failure_result(request, prepared)
    return _execute_prepared(request, before, prepared)

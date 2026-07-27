from __future__ import annotations

from typing import assert_never

from hwp_live_native_action_models import NativeSelection, NativeSnapshot
from hwp_live_native_format_commands import (
    MergeCommandPlan,
    SplitCommandPlan,
    TableFormatCommandPlan,
    TextFormatCommandPlan,
)
from hwp_live_native_format_contract import (
    NativeFormatRecipeRequest,
    NativeFormatWorkflow,
    PreparedFormatOperation,
)
from hwp_live_native_format_inputs import (
    InputFailure,
    parse_merge,
    parse_split,
    parse_table_format,
    parse_text_format,
    table_cell_coordinate,
)
from hwp_live_native_format_target import (
    NativeTableTargetRequest,
    ResolvedTable,
    TargetFailure,
    resolve_native_table_target,
)


type PreparationFailure = InputFailure | TargetFailure


def has_explicit_table_locator(request: NativeFormatRecipeRequest) -> bool:
    target = request.target
    return (
        target is not None
        and target.binding != "selection"
        and (
            target.control_instance_id is not None
            or target.table_index is not None
            or target.caption_contains is not None
            or bool(target.header_signature)
            or target.page_hint is not None
        )
    )


def _missing_table_format_target(message: str) -> InputFailure:
    return InputFailure(
        "needs_input",
        f"{message}. target으로 대상 표를 지정하거나 cell로 적용할 셀을 지정하세요",
        ("inputs.target", "inputs.parameters.cell"),
    )


def _selection_id(selection: NativeSelection) -> str:
    start, end = selection.start, selection.end
    return (
        f"selection:{start.list_id}:{start.paragraph}:{start.character}-"
        f"{end.list_id}:{end.paragraph}:{end.character}"
    )


def _resolve_table(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
) -> ResolvedTable | TargetFailure:
    return resolve_native_table_target(
        NativeTableTargetRequest(
            candidate=request.candidate,
            routing_page=request.routing_page,
            target=request.target,
            snapshot_control_type=before.control_type,
            snapshot_control_id=before.control_instance_id,
        )
    )


def _prepare_text(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
) -> PreparedFormatOperation | InputFailure:
    if request.target is None or request.target.kind != "selection":
        return InputFailure(
            "needs_input",
            "선택 영역 target이 필요합니다",
            ("inputs.target",),
        )
    if not before.selection.selected:
        return InputFailure(
            "needs_input",
            "서식을 적용할 텍스트 선택 영역이 없습니다",
            ("inputs.target",),
        )
    parsed = parse_text_format(request.parameters)
    if isinstance(parsed, InputFailure):
        return parsed
    return PreparedFormatOperation(
        TextFormatCommandPlan(parsed),
        _selection_id(before.selection),
        "target.selection",
        (),
    )


def _prepare_table_format(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
) -> PreparedFormatOperation | PreparationFailure:
    resolved = _resolve_table(request, before)
    if isinstance(resolved, TargetFailure):
        return resolved
    parsed = parse_table_format(request.parameters)
    if isinstance(parsed, InputFailure):
        return parsed
    cells: tuple[str, ...]
    if parsed.cell is not None:
        cells = (parsed.cell,)
    elif has_explicit_table_locator(request):
        cells = ()
    elif before.selection.base_mode in {3, 4}:
        if before.control_type != "tbl" or not before.control_instance_id:
            return _missing_table_format_target(
                "현재 선택이 표 또는 표 셀 선택이 아닙니다"
            )
        cells = ()
    elif before.control_type == "tbl" and before.cell_address:
        cells = (before.cell_address,)
    else:
        return _missing_table_format_target(
            "현재 커서가 표 셀 안에 있지 않고 선택한 표나 셀도 없습니다"
        )
    if parsed.cell is not None and (
        parsed.row_height_mm is not None or parsed.column_width_mm is not None
    ):
        row, column = table_cell_coordinate(parsed.cell)
        if resolved.rows is None or resolved.columns is None:
            return InputFailure(
                "schema_conflict",
                "행·열 크기 변경 전에 대상 표의 행·열 수를 확인하지 못했습니다",
            )
        if row > resolved.rows or column > resolved.columns:
            return InputFailure(
                "schema_conflict",
                f"{parsed.cell} 셀이 대상 표의 {resolved.rows}행 {resolved.columns}열 범위를 벗어납니다",
            )
    return PreparedFormatOperation(
        TableFormatCommandPlan(parsed, resolved, cells),
        resolved.instance_id,
        resolved.basis,
        cells,
    )


def _prepare_merge(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
) -> PreparedFormatOperation | PreparationFailure:
    resolved = _resolve_table(request, before)
    if isinstance(resolved, TargetFailure):
        return resolved
    parsed = parse_merge(request.parameters)
    if isinstance(parsed, InputFailure):
        return parsed
    return PreparedFormatOperation(
        MergeCommandPlan(parsed, resolved),
        resolved.instance_id,
        resolved.basis,
        (parsed.start, parsed.end),
    )


def _prepare_split(
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
) -> PreparedFormatOperation | PreparationFailure:
    resolved = _resolve_table(request, before)
    if isinstance(resolved, TargetFailure):
        return resolved
    parsed = parse_split(request.parameters)
    if isinstance(parsed, InputFailure):
        return parsed
    return PreparedFormatOperation(
        SplitCommandPlan(parsed, resolved),
        resolved.instance_id,
        resolved.basis,
        (parsed.cell,),
    )


def prepare_native_format_operation(
    workflow: NativeFormatWorkflow,
    request: NativeFormatRecipeRequest,
    before: NativeSnapshot,
) -> PreparedFormatOperation | PreparationFailure:
    if workflow == "text.format":
        return _prepare_text(request, before)
    if workflow == "table.format":
        return _prepare_table_format(request, before)
    if workflow == "table.merge_cells":
        return _prepare_merge(request, before)
    if workflow == "table.split_cells":
        return _prepare_split(request, before)
    assert_never(workflow)

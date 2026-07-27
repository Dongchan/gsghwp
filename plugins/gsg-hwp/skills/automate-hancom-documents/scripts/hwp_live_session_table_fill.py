from __future__ import annotations

from dataclasses import replace
from typing import Final

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_action_contract import NativeActionFailure
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_page,
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
    PreparedWorkflowTableFill,
    ResolvedWorkflowTable,
    prepare_table_fill,
    resolve_workflow_table,
    table_fill_contract_conflict,
    table_fill_chunks,
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
from hwp_table_format_inference import FORMAT_ESCAPE_HINT, TableFormatAmbiguity


_PRE_MUTATION_MARKER: Final = "; mutation_started=false"
_POST_WRITE_TEXT_FAILURES: Final = frozenset(
    {
        ("SET_CELL_TEXT", "TEXT_RANGE"),
        ("SET_CELL_TEXT", "TEXT_PATCH_READBACK"),
        ("PATCH_TEXT", "TEXT_RANGE"),
        ("PATCH_TEXT", "TEXT_PATCH_READBACK"),
    }
)


def _pre_mutation_error(error: HwpLiveError) -> HwpLiveError:
    return HwpLiveError(f"{error.reason}{_PRE_MUTATION_MARKER}")


def _hwp_paragraph_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\r\n")


def _verify_table_fill_readback(
    snapshot: DocumentStructure,
    prepared: PreparedWorkflowTableFill,
    *,
    page_count_before: int,
    postconditions: HwpOperatePostconditions,
) -> None:
    normalized = replace(
        prepared,
        replacements=tuple(
            (address, _hwp_paragraph_text(value))
            for address, value in prepared.replacements
        ),
    )
    verify_table_fill(
        snapshot,
        normalized,
        page_count_before=page_count_before,
        postconditions=postconditions,
    )


def _table_fill_values(
    snapshot: DocumentStructure,
    control_instance_id: str,
) -> dict[str, str] | None:
    table = next(
        (
            item
            for item in snapshot.tables
            if item.control_instance_id == control_instance_id
        ),
        None,
    )
    if table is None:
        return None
    return {cell.address: _hwp_paragraph_text(cell.text) for cell in table.cells}


def _matching_table_fill_addresses(
    snapshot: DocumentStructure,
    prepared: PreparedWorkflowTableFill,
    attempted: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    values = _table_fill_values(snapshot, prepared.control_instance_id)
    if values is None:
        return ()
    return tuple(
        address
        for address, expected in attempted
        if values.get(address) == _hwp_paragraph_text(expected)
    )


def _complete_table_fill_readback(
    snapshot: DocumentStructure,
    prepared: PreparedWorkflowTableFill,
    attempted: tuple[tuple[str, str], ...],
) -> bool:
    values = _table_fill_values(snapshot, prepared.control_instance_id)
    return values is not None and all(address in values for address, _ in attempted)


def _changed_table_fill_addresses(
    before: DocumentStructure,
    after: DocumentStructure,
    prepared: PreparedWorkflowTableFill,
    attempted: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    before_values = _table_fill_values(before, prepared.control_instance_id)
    after_values = _table_fill_values(after, prepared.control_instance_id)
    if before_values is None or after_values is None:
        return ()
    return tuple(
        address
        for address, _ in attempted
        if address in before_values
        and address in after_values
        and before_values[address] != after_values[address]
    )


def _read_table_fill_structure(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
) -> DocumentStructure:
    state_after = read_native_snapshot(candidate.window_handle)
    if state_after is None:
        raise HwpLiveError("한컴 네이티브 표 채움 후 현재 쪽을 읽지 못했습니다")
    return inspect_candidate_structure(
        hwp,
        candidate,
        state_after.current_page,
        lambda: None,
    )


def _failed_chunk_result(
    resolution: WorkflowResolution,
    prepared: PreparedWorkflowTableFill,
    error: HwpLiveError,
    *,
    before: DocumentStructure,
    after: DocumentStructure | None,
    attempted: tuple[tuple[str, str], ...],
    confirmed_before_failure: tuple[str, ...],
    commands_completed_before_failure: int,
    native_elapsed_microseconds: int,
) -> OperationResult:
    failure_commands = (
        error.commands_completed if isinstance(error, NativeActionFailure) else 0
    )
    readback_complete = (
        after is not None
        and _complete_table_fill_readback(before, prepared, attempted)
        and _complete_table_fill_readback(after, prepared, attempted)
    )
    if after is None:
        updated_addresses = confirmed_before_failure
        changed_addresses = confirmed_before_failure
    else:
        observed_updated = _matching_table_fill_addresses(
            after,
            prepared,
            attempted,
        )
        observed_changed = _changed_table_fill_addresses(
            before,
            after,
            prepared,
            attempted,
        )
        if readback_complete:
            updated_addresses = observed_updated
            changed_addresses = observed_changed
        else:
            updated_set = {*confirmed_before_failure, *observed_updated}
            changed_set = {*confirmed_before_failure, *observed_changed}
            updated_addresses = tuple(
                address for address, _ in attempted if address in updated_set
            )
            changed_addresses = tuple(
                address for address, _ in attempted if address in changed_set
            )
    native_partial = (
        error.partial_mutation if isinstance(error, NativeActionFailure) else False
    )
    known_partial_mutation = bool(changed_addresses) or native_partial
    partial_mutation = (
        known_partial_mutation if readback_complete or known_partial_mutation else None
    )
    retry_safe = (
        (error.retry_safe if isinstance(error, NativeActionFailure) else True)
        and readback_complete
        and partial_mutation is False
    )
    commands_completed = commands_completed_before_failure + failure_commands
    readback_message = (
        "실제 구조 readback으로 요청값을 확인했습니다"
        if readback_complete
        else (
            "구조 readback에 대상 표 또는 셀이 없어 실패 조각 상태는 미확인입니다"
            if after is not None
            else "완료 조각의 네이티브 readback만 확인했으며 실패 조각 상태는 미확인입니다"
        )
    )
    failure_prefix = (
        f"{error}; 대량 표 채움 {len(attempted)}/{len(prepared.replacements)}개 "
    )
    failure_progress = (
        f"주소까지 시도했고 updated_addresses의 {len(updated_addresses)}개는 "
    )
    result = workflow_result(
        resolution,
        "partial_change" if partial_mutation is True else "operation_failed",
        "".join((failure_prefix, failure_progress, readback_message)),
    )
    return result.model_copy(
        update={
            "changed": partial_mutation is True,
            "execution_mode": "native_in_process",
            "native_protocol": prepared.native_protocol,
            "verification": "native_snapshot_before_after",
            "verified": False,
            "commands_executed": commands_completed,
            "native_elapsed_microseconds": native_elapsed_microseconds,
            "current_page": None if after is None else after.page,
            "page_count": None if after is None else after.page_count,
            "modified": partial_mutation is True,
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
            "partial_change": partial_mutation is True,
            "partial_mutation": partial_mutation,
            "retry_safe": retry_safe,
            "reconcile_required": partial_mutation is not False,
            "failed_step": (
                error.failed_step
                if isinstance(error, NativeActionFailure)
                else "native_table_fill_chunk"
            ),
            "commands_completed": commands_completed,
            "resolved_target_id": prepared.control_instance_id,
            "updated_addresses": updated_addresses,
        }
    )


def _has_explicit_table_selector(target: HwpOperateTarget) -> bool:
    return (
        target.control_instance_id is not None
        or target.page_hint is not None
        or target.table_index is not None
        or target.caption_contains is not None
        or bool(target.header_signature)
    )


def _selected_cells_failure(
    error: HwpLiveError,
    cell_address_error: str,
) -> HwpLiveError:
    reason = error.reason.partition(" 표 서식은 ")[0].rstrip()
    native_reason = cell_address_error.strip()
    if native_reason and native_reason not in reason:
        reason = f"{reason}. 네이티브 셀 주소 검사 오류: {native_reason}"
    return HwpLiveError(reason, mutation_started=error.mutation_started)


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
    if _has_explicit_table_selector(target) and target.control_instance_id is None:
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
        try:
            detail = inspect_native_structure(
                candidate.window_handle,
                before.current_page,
            )
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
            elif before.selection.cell_address_error:
                raise HwpLiveError(before.selection.cell_address_error)
            elif strict_selection:
                selected_addresses = table_formula_selection_region(
                    candidate.application,
                    topology,
                )
            else:
                raise HwpLiveError("현재 선택한 표 셀 범위를 확인하지 못했습니다")
            start_cell = selected_addresses[0]
        except HwpLiveError as error:
            raise _selected_cells_failure(
                error,
                before.selection.cell_address_error,
            ) from error
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


def _resolve_table_fill_target(
    hwp: LiveHwpApplication,
    candidate: HwpDocumentCandidate,
    target: HwpOperateTarget,
    first_structure: DocumentStructure,
) -> tuple[DocumentStructure, ResolvedWorkflowTable]:
    resolved = resolve_workflow_table(first_structure, target)
    target_id = target.control_instance_id
    if target_id is None or (
        resolved.table is not None and resolved.table_index is not None
    ):
        return first_structure, resolved
    if any(table.control_instance_id == target_id for table in first_structure.tables):
        return first_structure, resolved
    for page_number in range(1, first_structure.page_count + 1):
        if page_number == first_structure.page:
            continue
        fast_page = inspect_native_page(
            candidate.window_handle,
            page_number,
            include_cells=False,
        )
        if fast_page is not None and not any(
            control.control_type == "tbl" and control.instance_id == target_id
            for control in fast_page.controls
        ):
            continue
        structure = inspect_candidate_structure(
            hwp,
            candidate,
            page_number,
            lambda: None,
        )
        resolved = resolve_workflow_table(structure, target)
        if (resolved.table is not None and resolved.table_index is not None) or any(
            table.control_instance_id == target_id for table in structure.tables
        ):
            return structure, resolved
    return first_structure, resolved


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
    try:
        target, data, selected_addresses = _bind_live_table_selection(
            candidate,
            target,
            data,
        )
    except HwpLiveError as error:
        raise _pre_mutation_error(error) from error
    if data.rows and data.start_cell is None:
        return (
            workflow_result(
                resolution,
                "needs_input",
                "행렬 표 채움 대상으로 사용할 현재 표 셀 위치나 선택 범위를 확인하지 못했습니다",
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
    try:
        before = inspect_candidate_structure(
            hwp,
            candidate,
            page,
            lambda: None,
        )
    except HwpLiveError as error:
        raise _pre_mutation_error(error) from error
    try:
        before, resolved_table = _resolve_table_fill_target(
            hwp,
            candidate,
            target,
            before,
        )
    except HwpLiveError as error:
        raise _pre_mutation_error(error) from error
    if resolved_table.table is None or resolved_table.table_index is None:
        exact_target_unresolved = (
            target.control_instance_id is not None and not resolved_table.candidates
        )
        return (
            workflow_result(
                resolution,
                "ambiguous" if resolved_table.candidates else "not_found",
                (
                    "대상 조건과 일치하는 표를 하나로 확정하지 못했습니다"
                    if resolved_table.candidates
                    else (
                        f"지정한 표 target_id '{target.control_instance_id}'의 위치를 "
                        "요청의 page/table_index 조건과 함께 문서 전체에서 확정하지 "
                        "못해 페이지 바인딩에 실패했습니다. "
                        "대상 문서를 확인하고 hwp_inspect_page_fast로 해당 쪽을 "
                        "다시 조회한 뒤 최신 target_id를 전달하세요"
                    )
                    if exact_target_unresolved
                    else "대상 조건과 일치하는 표가 없습니다"
                ),
                required_inputs=(
                    ("inputs.target.control_instance_id",)
                    if exact_target_unresolved
                    else ()
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
    try:
        chunks = table_fill_chunks(prepared, resolved_table.table)
    except HwpLiveError as error:
        raise _pre_mutation_error(error) from error
    if not prepared.replacements and prepared.format_reverted:
        # 편집이 0건인데 요청값과 셀 값은 달랐다. 표시 형식 재조립이 요청을
        # 원래 값으로 되돌렸다는 뜻이다. 이것을 executed 로 보고하면 모델은
        # 성공했다고 믿고 넘어가고 사용자는 나중에 안 바뀐 것을 발견한다.
        table_candidate = workflow_table_candidate(
            resolved_table.table,
            resolved_table.table_index,
            before.page,
        )
        reverted = ", ".join(
            f"{cell.address}: 요청 {cell.requested!r} → 표시 형식 적용 후 "
            + f"{cell.rebuilt!r} (기존 값과 같음)"
            for cell in prepared.format_reverted[:8]
        )
        return (
            workflow_result(
                resolution,
                "needs_input",
                "기존 셀의 표시 형식(접두어·부호·단위·괄호·자릿수)이 요청값에 다시 "
                + f"붙어 결과가 원래 값과 같아졌습니다. 편집하지 않았습니다 — {reverted}. "
                + FORMAT_ESCAPE_HINT,
                required_inputs=("inputs.policy.preserve_display_format",),
            ).model_copy(update={"target_candidates": (table_candidate,)}),
            before,
        )
    if not prepared.replacements:
        _verify_table_fill_readback(
            before,
            prepared,
            page_count_before=before.page_count,
            postconditions=postconditions,
        )
        result = workflow_result(
            resolution,
            "executed",
            "요청한 표 셀 값이 이미 모두 일치해 네이티브 편집을 실행하지 않았습니다",
        ).model_copy(
            update={
                "execution_mode": "native_in_process",
                "native_protocol": prepared.native_protocol,
                "verification": "native_structure_no_change",
                "verified": True,
                "commands_executed": 0,
                "native_elapsed_microseconds": 0,
                "current_page": before.page,
                "page_count": before.page_count,
                "modified": False,
                "partial_mutation": False,
                "retry_safe": True,
                "commands_completed": 0,
                "resolved_target_id": prepared.control_instance_id,
            }
        )
        return result, before

    commands_executed = 0
    native_actions_executed = 0
    native_elapsed_microseconds = 0
    transformed_execution = any(
        chunk.request is not prepared.request for chunk in chunks
    )
    confirmed_addresses: tuple[str, ...] = ()
    attempted: tuple[tuple[str, str], ...] = ()
    for chunk in chunks:
        attempted += chunk.replacements
        try:
            native = execute_native_actions(
                candidate.window_handle,
                chunk.request,
                minimum_version=chunk.native_protocol,
            )
            if native is None:
                raise HwpLiveError(
                    f"한컴 프로토콜 {chunk.native_protocol} 네이티브 표 채움 실행기를 "
                    + "사용할 수 없습니다"
                )
        except NativeActionFailure as failure:
            if (failure.failed_step, failure.code) in _POST_WRITE_TEXT_FAILURES:
                failure.partial_mutation = True
                failure.retry_safe = False
            if chunk.request is prepared.request:
                raise
            try:
                after_failure = _read_table_fill_structure(candidate, hwp)
            except HwpLiveError:
                after_failure = None
            return (
                _failed_chunk_result(
                    resolution,
                    prepared,
                    failure,
                    before=before,
                    after=after_failure,
                    attempted=attempted,
                    confirmed_before_failure=confirmed_addresses,
                    commands_completed_before_failure=commands_executed,
                    native_elapsed_microseconds=native_elapsed_microseconds,
                ),
                after_failure,
            )
        except HwpLiveError as error:
            if chunk.request is prepared.request:
                raise
            try:
                after_failure = _read_table_fill_structure(candidate, hwp)
            except HwpLiveError:
                after_failure = None
            return (
                _failed_chunk_result(
                    resolution,
                    prepared,
                    error,
                    before=before,
                    after=after_failure,
                    attempted=attempted,
                    confirmed_before_failure=confirmed_addresses,
                    commands_completed_before_failure=commands_executed,
                    native_elapsed_microseconds=native_elapsed_microseconds,
                ),
                after_failure,
            )
        commands_executed += native.commands_executed
        native_actions_executed += getattr(native, "actions_executed", 0)
        native_elapsed_microseconds += native.elapsed_microseconds
        confirmed_addresses += tuple(address for address, _ in chunk.replacements)

    try:
        after = _read_table_fill_structure(candidate, hwp)
    except HwpLiveError as error:
        if not transformed_execution:
            raise
        return (
            _failed_chunk_result(
                resolution,
                prepared,
                error,
                before=before,
                after=None,
                attempted=attempted,
                confirmed_before_failure=confirmed_addresses,
                commands_completed_before_failure=commands_executed,
                native_elapsed_microseconds=native_elapsed_microseconds,
            ),
            None,
        )
    try:
        _verify_table_fill_readback(
            after,
            prepared,
            page_count_before=before.page_count,
            postconditions=postconditions,
        )
    except HwpLiveError as error:
        if not transformed_execution:
            raise
        return (
            _failed_chunk_result(
                resolution,
                prepared,
                error,
                before=before,
                after=after,
                attempted=attempted,
                confirmed_before_failure=confirmed_addresses,
                commands_completed_before_failure=commands_executed,
                native_elapsed_microseconds=native_elapsed_microseconds,
            ),
            after,
        )
    # 일부만 되돌려진 경우다. 실행 자체는 했으니 executed 가 맞지만, 어떤 셀이
    # 빠졌는지 말하지 않으면 그 셀은 조용한 무편집으로 남는다.
    reverted_note = (
        ""
        if not prepared.format_reverted
        else (
            " 다만 "
            + ", ".join(cell.address for cell in prepared.format_reverted[:8])
            + " 셀은 표시 형식이 요청값을 원래 값으로 되돌려 편집하지 않았습니다. "
            + FORMAT_ESCAPE_HINT
        )
    )
    result = workflow_result(
        resolution,
        "executed",
        f"기존 표를 찾아 프로토콜 {prepared.native_protocol} C++/ATL "
        + f"네이티브 호출 {len(chunks)}회로 채우고 구조를 검증했습니다"
        + reverted_note,
    ).model_copy(
        update={
            "changed": bool(prepared.replacements),
            "execution_mode": "native_in_process",
            "native_protocol": prepared.native_protocol,
            "verification": "native_snapshot_before_after",
            "verified": True,
            "commands_executed": commands_executed,
            "native_actions_executed": native_actions_executed,
            "native_elapsed_microseconds": native_elapsed_microseconds,
            "current_page": after.page,
            "page_count": after.page_count,
            "modified": bool(prepared.replacements),
            "partial_mutation": False,
            "retry_safe": True,
            "commands_completed": commands_executed,
            "resolved_target_id": prepared.control_instance_id,
            "updated_addresses": tuple(address for address, _ in prepared.replacements),
        }
    )
    return result, after

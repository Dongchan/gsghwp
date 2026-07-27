from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import (  # noqa: E402
    HwpComApplication,
    HwpComDocument,
    LiveHwpApplication,
)
from hwp_live_native_action_contract import (  # noqa: E402
    NativeActionFailure,
    NativeActionFailureEvidence,
)
from hwp_live_native_action_models import (  # noqa: E402
    NativeActionRequest,
    NativeActionResult,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session_table_fill import operate_table_fill  # noqa: E402
from hwp_live_workflow_table import PreparedWorkflowTableFill  # noqa: E402
from hwp_mcp_result_envelope import (  # noqa: E402
    normalize_production_result,
    transport_error_result,
)
from hwp_native_failure_result import native_action_failure_result  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateData,
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    WorkflowCandidate,
    WorkflowResolution,
)
from hwp_public_contract import PublicTableTarget, to_public_action_result  # noqa: E402
from hwp_public_table_target import PublicTableTargetStore  # noqa: E402


NATIVE_ROOT = Path(__file__).resolve().parents[1] / "addon" / "HancomLiveBridgeNative"


def _resolved_table_fill() -> WorkflowResolution:
    return WorkflowResolution(
        query="기존 표 채우기",
        status="resolved",
        lookup_microseconds=0,
        workflow_id="table.fill_existing",
        candidates=(
            WorkflowCandidate(
                workflow_id="table.fill_existing",
                description="기존 표 채우기",
                steps=("ResolveTable", "FillCells", "VerifyStructure"),
                execution="recipe",
                confidence=1,
                match_kind="explicit",
            ),
        ),
        steps=("ResolveTable", "FillCells", "VerifyStructure"),
        match_kind="explicit",
    )


def _table_candidate() -> HwpDocumentCandidate:
    return HwpDocumentCandidate(
        selector="active",
        moniker_name="fixture",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, object()),
        document_id=17,
        full_name="C:/documents/sample.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )


def _table_structure(
    page: int,
    page_count: int,
    control_instance_id: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        page=page,
        page_count=page_count,
        paragraphs=(),
        tables=(
            SimpleNamespace(
                control_instance_id=control_instance_id,
                anchor=SimpleNamespace(paragraph=0),
                caption=None,
                cells=(),
                rows=1,
                columns=1,
            ),
        ),
    )


def _prepared_fill(control_instance_id: str) -> PreparedWorkflowTableFill:
    return PreparedWorkflowTableFill(
        request=NativeActionRequest(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            commands=(),
        ),
        table_index=1,
        control_instance_id=control_instance_id,
        replacements=(("A1", "값"),),
        native_protocol=12,
    )


def _native_fill_success() -> NativeActionResult:
    return NativeActionResult(
        commands_executed=2,
        actions_executed=0,
        text_insertions=1,
        image_insertions=0,
        elapsed_microseconds=75,
        created_control_ids=(),
    )


def test_table_navigation_accepts_false_only_after_target_context_readback() -> None:
    source = (NATIVE_ROOT / "TableInspection.cpp").read_text(encoding="utf-8")
    compact = "".join(source.split())

    assert "bool InvokeAction(" in source
    assert "bool TableContextMatches(" in source
    assert "GetPositionList(hwp, &currentList)" in source
    assert "ParentMatches(hwp, tableInstanceId)" in source
    assert (
        'RunTableNavigationAction(hwp,action,L"TableColEnd",tableInstanceId)' in compact
    )
    assert (
        'RunTableNavigationAction(hwp,action,L"TableColPageDown",tableInstanceId)'
        in compact
    )


def test_table_context_prerequisites_have_distinct_native_error_codes() -> None:
    inspection = (NATIVE_ROOT / "TableInspection.cpp").read_text(encoding="utf-8")
    protocol = (NATIVE_ROOT / "LiveInspection.cpp").read_text(encoding="utf-8")

    assert all(
        code in inspection
        for code in (
            'L"TABLE_HACTION"',
            'L"TABLE_DOCUMENT_INFO"',
            'L"TABLE_EDIT_CONTEXT"',
            'L"TABLE_COLUMN_END"',
            'L"TABLE_PAGE_DOWN"',
        )
    )
    assert "tableErrorCode" in protocol
    assert "EncodeUtf8Base64(tableErrorCode)" in protocol


def test_pre_mutation_table_inspection_failure_is_safe_to_retry() -> None:
    # Given
    target = HwpOperateTarget(
        kind="table",
        page_hint=1,
        control_instance_id="table-17",
    )
    data = HwpOperateData(cells={"A1": "값"})
    inputs = HwpOperateInputs(
        request_id="table-inspection-before-mutation",
        operation="table.fill_existing",
    )

    # When
    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=SimpleNamespace(
                control_type="tbl",
                control_instance_id="table-17",
                current_page=1,
            ),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            side_effect=HwpLiveError("네이티브 상세 구조 조회 실패"),
        ),
        pytest.raises(HwpLiveError) as raised,
    ):
        _ = operate_table_fill(
            _table_candidate(),
            cast(LiveHwpApplication, object()),
            _resolved_table_fill(),
            target,
            data,
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    result = transport_error_result(
        inputs,
        raised.value,
        intent="기존 표 채우기",
    )
    public = to_public_action_result(result, ())

    # Then
    assert public.commands_executed == 0
    assert public.modified is False
    assert public.retry_safe is True


def test_set_cell_text_readback_failure_after_mutation_is_not_retry_safe() -> None:
    # Given
    target = HwpOperateTarget(
        kind="table",
        page_hint=1,
        control_instance_id="table-17",
    )
    data = HwpOperateData(cells={"A1": "값"})
    inputs = HwpOperateInputs(
        request_id="table-inspection-after-mutation",
        operation="table.fill_existing",
    )
    prepared = PreparedWorkflowTableFill(
        request=NativeActionRequest(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            commands=(),
        ),
        table_index=1,
        control_instance_id="table-17",
        replacements=(("A1", "값"),),
        native_protocol=12,
    )
    structure = SimpleNamespace(page=1, page_count=1, paragraphs=())
    resolved = SimpleNamespace(
        table=SimpleNamespace(
            anchor=SimpleNamespace(paragraph=0),
            caption=None,
            cells=(),
        ),
        table_index=1,
        candidates=(),
    )
    native_failure = NativeActionFailure(
        NativeActionFailureEvidence(
            code="TEXT_RANGE",
            location="A1",
            message="text range must be non-empty and stay within one HWP list",
            commands_completed=0,
            failed_step="SET_CELL_TEXT",
            partial_mutation=False,
            retry_safe=True,
            structure_digest_before="same",
            structure_digest_after="same",
        )
    )

    # When
    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=SimpleNamespace(
                control_type="tbl",
                control_instance_id="table-17",
                current_page=1,
            ),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            return_value=structure,
        ),
        patch(
            "hwp_live_session_table_fill.resolve_workflow_table",
            return_value=resolved,
        ),
        patch(
            "hwp_live_session_table_fill.prepare_table_fill",
            return_value=prepared,
        ),
        patch(
            "hwp_live_session_table_fill.execute_native_actions",
            side_effect=native_failure,
        ),
        pytest.raises(NativeActionFailure) as raised,
    ):
        _ = operate_table_fill(
            _table_candidate(),
            cast(LiveHwpApplication, object()),
            _resolved_table_fill(),
            target,
            data,
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    result = native_action_failure_result("기존 표 채우기", raised.value)
    normalized = normalize_production_result(result, inputs)
    public = to_public_action_result(normalized, ())

    # Then
    assert public.commands_executed == 0
    assert public.modified is True
    assert public.retry_safe is False


def test_text_patch_readback_failure_after_mutation_is_not_retry_safe() -> None:
    target = HwpOperateTarget(
        kind="table",
        page_hint=1,
        control_instance_id="table-17",
    )
    data = HwpOperateData(cells={"A1": "ㅇ 항목\n- 세부\n* 근거"})
    inputs = HwpOperateInputs(
        request_id="table-patch-readback-after-mutation",
        operation="table.fill_existing",
    )
    prepared = PreparedWorkflowTableFill(
        request=NativeActionRequest(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            commands=(),
        ),
        table_index=1,
        control_instance_id="table-17",
        replacements=(("A1", "ㅇ 항목\n- 세부\n* 근거"),),
        native_protocol=12,
    )
    structure = SimpleNamespace(page=1, page_count=1, paragraphs=())
    resolved = SimpleNamespace(
        table=SimpleNamespace(
            anchor=SimpleNamespace(paragraph=0),
            caption=None,
            cells=(),
        ),
        table_index=1,
        candidates=(),
    )
    native_failure = NativeActionFailure(
        NativeActionFailureEvidence(
            code="TEXT_PATCH_READBACK",
            location="A1",
            message="reselected text does not match the requested replacement",
            commands_completed=2,
            failed_step="PATCH_TEXT",
            partial_mutation=False,
            retry_safe=True,
            structure_digest_before="same",
            structure_digest_after="same",
        )
    )

    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=SimpleNamespace(
                control_type="tbl",
                control_instance_id="table-17",
                current_page=1,
            ),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            return_value=structure,
        ),
        patch(
            "hwp_live_session_table_fill.resolve_workflow_table",
            return_value=resolved,
        ),
        patch(
            "hwp_live_session_table_fill.prepare_table_fill",
            return_value=prepared,
        ),
        patch(
            "hwp_live_session_table_fill.execute_native_actions",
            side_effect=native_failure,
        ),
        pytest.raises(NativeActionFailure) as raised,
    ):
        _ = operate_table_fill(
            _table_candidate(),
            cast(LiveHwpApplication, object()),
            _resolved_table_fill(),
            target,
            data,
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    result = native_action_failure_result("기존 표 채우기", raised.value)
    normalized = normalize_production_result(result, inputs)
    public = to_public_action_result(normalized, ())

    assert public.modified is True
    assert public.retry_safe is False


def test_successful_set_cell_text_does_not_enter_text_range_safety_net() -> None:
    # Given
    target = HwpOperateTarget(
        kind="table",
        page_hint=1,
        control_instance_id="table-17",
    )
    data = HwpOperateData(cells={"A1": "값"})
    prepared = PreparedWorkflowTableFill(
        request=NativeActionRequest(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            commands=(),
        ),
        table_index=1,
        control_instance_id="table-17",
        replacements=(("A1", "값"),),
        native_protocol=12,
    )
    structure = SimpleNamespace(page=1, page_count=1, paragraphs=())
    resolved = SimpleNamespace(
        table=SimpleNamespace(
            anchor=SimpleNamespace(paragraph=0),
            caption=None,
            cells=(),
        ),
        table_index=1,
        candidates=(),
    )
    native_success = NativeActionResult(
        commands_executed=2,
        actions_executed=0,
        text_insertions=1,
        image_insertions=0,
        elapsed_microseconds=75,
        created_control_ids=(),
    )

    # When
    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=SimpleNamespace(
                control_type="tbl",
                control_instance_id="table-17",
                current_page=1,
            ),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            return_value=structure,
        ),
        patch(
            "hwp_live_session_table_fill.resolve_workflow_table",
            return_value=resolved,
        ),
        patch(
            "hwp_live_session_table_fill.prepare_table_fill",
            return_value=prepared,
        ),
        patch(
            "hwp_live_session_table_fill.execute_native_actions",
            return_value=native_success,
        ),
        patch("hwp_live_session_table_fill.verify_table_fill") as verify,
    ):
        result, after = operate_table_fill(
            _table_candidate(),
            cast(LiveHwpApplication, object()),
            _resolved_table_fill(),
            target,
            data,
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    # Then
    verify.assert_called_once()
    assert result is not None
    assert result.status == "executed"
    assert result.verified is True
    assert result.modified is True
    assert result.commands_executed == 2
    assert after is structure


def test_multiline_table_fill_accepts_hwp_crlf_readback() -> None:
    target = HwpOperateTarget(
        kind="table",
        page_hint=1,
        control_instance_id="table-17",
    )
    replacement = "ㅇ 항목\n- 세부\n* 근거"
    data = HwpOperateData(cells={"A1": replacement})
    prepared = PreparedWorkflowTableFill(
        request=NativeActionRequest(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            commands=(),
        ),
        table_index=1,
        control_instance_id="table-17",
        replacements=(("A1", replacement),),
        native_protocol=12,
    )
    table = SimpleNamespace(
        control_instance_id="table-17",
        anchor=SimpleNamespace(paragraph=0),
        caption=None,
        cells=(
            SimpleNamespace(
                address="A1",
                text="ㅇ 항목\r\n- 세부\r\n* 근거",
            ),
        ),
    )
    before = SimpleNamespace(
        page=1,
        page_count=1,
        paragraphs=(),
        tables=(table,),
    )
    after = SimpleNamespace(
        page=1,
        page_count=1,
        paragraphs=(),
        tables=(table,),
    )
    resolved = SimpleNamespace(
        table=table,
        table_index=1,
        candidates=(),
    )
    native_success = NativeActionResult(
        commands_executed=3,
        actions_executed=2,
        text_insertions=1,
        image_insertions=0,
        elapsed_microseconds=75,
        created_control_ids=(),
    )

    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            side_effect=(
                SimpleNamespace(
                    control_type="tbl",
                    control_instance_id="table-17",
                    current_page=1,
                ),
                SimpleNamespace(current_page=1),
            ),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            side_effect=(before, after),
        ),
        patch(
            "hwp_live_session_table_fill.resolve_workflow_table",
            return_value=resolved,
        ),
        patch(
            "hwp_live_session_table_fill.prepare_table_fill",
            return_value=prepared,
        ),
        patch(
            "hwp_live_session_table_fill.execute_native_actions",
            return_value=native_success,
        ),
    ):
        result, returned = operate_table_fill(
            _table_candidate(),
            cast(LiveHwpApplication, object()),
            _resolved_table_fill(),
            target,
            data,
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    assert result is not None
    assert result.status == "executed"
    assert result.verified is True
    assert returned is after


def test_cache_cold_target_id_searches_from_cursor_page_29_to_page_1() -> None:
    target_id = "2002765770"
    current_id = "1150406428"
    canonical = PublicTableTargetStore().resolve(PublicTableTarget(target_id=target_id))
    target = canonical.target
    assert target.control_instance_id == target_id
    assert target.page_hint is None
    assert target.table_index is None
    current_structure = _table_structure(29, 29, current_id)
    target_structure = _table_structure(1, 29, target_id)

    def inspect_structure(
        _hwp: object,
        _candidate: HwpDocumentCandidate,
        page: int,
        _guard: object,
    ) -> SimpleNamespace:
        if page == 0:
            return current_structure
        if page == 1:
            return target_structure
        raise AssertionError(f"unexpected detailed inspection for page {page}")

    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            side_effect=(
                SimpleNamespace(
                    control_type="tbl",
                    control_instance_id=current_id,
                    current_page=29,
                ),
                SimpleNamespace(current_page=1),
            ),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_native_page",
            return_value=SimpleNamespace(
                controls=(
                    SimpleNamespace(
                        control_type="tbl",
                        instance_id=target_id,
                    ),
                ),
            ),
        ) as inspect_fast,
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            side_effect=inspect_structure,
        ) as inspect_detailed,
        patch(
            "hwp_live_session_table_fill.prepare_table_fill",
            return_value=_prepared_fill(target_id),
        ) as prepare,
        patch(
            "hwp_live_session_table_fill.execute_native_actions",
            return_value=_native_fill_success(),
        ),
        patch("hwp_live_session_table_fill.verify_table_fill"),
    ):
        result, returned = operate_table_fill(
            _table_candidate(),
            cast(LiveHwpApplication, object()),
            _resolved_table_fill(),
            target,
            HwpOperateData(cells={"A1": "값"}),
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    inspect_fast.assert_called_once_with(100, 1, include_cells=False)
    assert [item.args[2] for item in inspect_detailed.call_args_list] == [0, 1, 1]
    prepare.assert_called_once()
    assert prepare.call_args.args[2] == 1
    assert result is not None
    assert result.status == "executed"
    assert result.commands_executed == 2
    assert result.verified is True
    assert returned is target_structure


def test_exact_target_page_hint_short_circuits_cross_page_lookup() -> None:
    target_id = "2002765770"
    target = HwpOperateTarget(
        kind="table",
        page_hint=1,
        table_index=1,
        control_instance_id=target_id,
    )
    target_structure = _table_structure(1, 29, target_id)

    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            side_effect=(
                SimpleNamespace(
                    control_type="tbl",
                    control_instance_id="1150406428",
                    current_page=29,
                ),
                SimpleNamespace(current_page=1),
            ),
        ),
        patch("hwp_live_session_table_fill.inspect_native_page") as inspect_fast,
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            return_value=target_structure,
        ) as inspect_detailed,
        patch(
            "hwp_live_session_table_fill.prepare_table_fill",
            return_value=_prepared_fill(target_id),
        ),
        patch(
            "hwp_live_session_table_fill.execute_native_actions",
            return_value=_native_fill_success(),
        ),
        patch("hwp_live_session_table_fill.verify_table_fill"),
    ):
        result, _ = operate_table_fill(
            _table_candidate(),
            cast(LiveHwpApplication, object()),
            _resolved_table_fill(),
            target,
            HwpOperateData(cells={"A1": "값"}),
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    inspect_fast.assert_not_called()
    assert [item.args[2] for item in inspect_detailed.call_args_list] == [1, 1]
    assert result is not None
    assert result.status == "executed"


def test_missing_exact_target_id_reports_page_binding_failure_without_detailed_scan() -> (
    None
):
    target_id = "missing-table-id"
    current_structure = _table_structure(29, 29, "1150406428")

    def fast_page(
        _window_handle: int,
        page: int,
        *,
        include_cells: bool,
    ) -> SimpleNamespace:
        assert include_cells is False
        return SimpleNamespace(
            controls=(
                SimpleNamespace(
                    control_type="tbl",
                    instance_id=f"other-table-{page}",
                ),
            ),
        )

    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            return_value=SimpleNamespace(
                control_type="tbl",
                control_instance_id="1150406428",
                current_page=29,
            ),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_native_page",
            side_effect=fast_page,
        ) as inspect_fast,
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            return_value=current_structure,
        ) as inspect_detailed,
        patch("hwp_live_session_table_fill.prepare_table_fill") as prepare,
        patch("hwp_live_session_table_fill.execute_native_actions") as execute,
    ):
        result, returned = operate_table_fill(
            _table_candidate(),
            cast(LiveHwpApplication, object()),
            _resolved_table_fill(),
            HwpOperateTarget(
                kind="table",
                control_instance_id=target_id,
            ),
            HwpOperateData(cells={"A1": "값"}),
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    assert inspect_fast.call_count == 28
    assert [item.args[1] for item in inspect_fast.call_args_list] == list(range(1, 29))
    inspect_detailed.assert_called_once()
    prepare.assert_not_called()
    execute.assert_not_called()
    assert result is not None
    assert result.status == "not_found"
    assert target_id in result.message
    assert "문서 전체" in result.message
    assert "페이지 바인딩" in result.message
    assert "hwp_inspect_page_fast" in result.message
    assert result.required_inputs == ("inputs.target.control_instance_id",)
    assert returned is None

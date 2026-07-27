"""표시 형식 보존과 글자 서식 보존을 갈라놓은 두 정책을 고정한다.

사용자는 표의 `EL.+39.3m` 을 `39.3` 으로 바꾸려다 세 경로에서 모두 막혔고,
마지막에는 셀을 직접 선택해 달라는 요구까지 받았다. 뿌리는 `preserve_style`
하나가 서로 다른 두 가지 — 파이썬의 표시 문자열 재조립과 네이티브의 글자 서식
보존 — 를 함께 제어한 것이다. 접두어를 지우려고 끄면 글꼴까지 잃었으므로 아무도
끄지 못했고, 공개 도구는 아예 True 로 못 박아 두었다.

여기서 고정하는 것:
  1. 접두어 붙은 셀에 맨숫자를 넣으면 실제로 맨숫자가 된다
  2. 정책 인자를 하나도 주지 않은 요청의 네이티브 명령 스트림은 변경 전과
     바이트 단위로 같다
  3. 접두어를 버리면서 글자 서식은 유지된다
  4. 배율 충돌 검사는 기본값에서 여전히 막는다
  5. 편집이 0건이면 executed 로 보고하지 않는다
  6. 실패 안내가 값을 지어내라고 시키지 않는다
"""

from __future__ import annotations

import hashlib
import sys
from base64 import b64encode
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

from hwp_live_native_action_contract import encode_action_request  # noqa: E402
from hwp_live_native_action_models import SetCellTextCommand  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session_table_fill import operate_table_fill  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    StructureCell,
    StructurePosition,
    StructureTable,
)
from hwp_live_workflow_table import prepare_table_fill  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    OperationResult,
    WorkflowCandidate,
    WorkflowResolution,
)
from hwp_priority_table_expand import prepare_expand_and_fill  # noqa: E402
from hwp_public_contract import to_public_action_result  # noqa: E402
from hwp_table_format_inference import (  # noqa: E402
    FORMAT_ESCAPE_HINT,
    TableFormatAmbiguity,
    plan_table_cell_edits,
)


def _cell(
    address: str,
    row: int,
    column: int,
    text: str,
    owner: str | None = None,
) -> StructureCell:
    return StructureCell(
        address=address,
        row=row,
        column=column,
        owner_address=owner or address,
        text=text,
    )


def _table() -> StructureTable:
    """접두어·부호·단위가 붙은 표고 열과 자리구분·괄호가 있는 면적 열."""
    cells = (
        _cell("A1", 0, 0, "구분"),
        _cell("B1", 0, 1, "표고"),
        _cell("C1", 0, 2, "면적"),
        _cell("A2", 1, 0, "항목 1"),
        _cell("B2", 1, 1, "EL.+39.3m"),
        _cell("C2", 1, 2, "1,234\n(56)"),
        _cell("A3", 2, 0, "항목 2"),
        _cell("B3", 2, 1, "EL.+41.7m"),
        _cell("C3", 2, 2, "2,345\n(67)"),
        _cell("A4", 3, 0, "항목 3"),
        _cell("B4", 3, 1, ""),
        _cell("C4", 3, 2, "3,456\n(78)"),
    )
    return StructureTable(
        table_ref="table-ref-00000001",
        control_instance_id="table-17",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=4,
        columns=3,
        merges=(),
        cells=cells,
    )


def _candidate() -> HwpDocumentCandidate:
    return cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                window_handle=100,
                document_id=17,
                full_name="C:/documents/sample.hwp",
                document=SimpleNamespace(
                    DocumentID=17,
                    FullName="C:/documents/sample.hwp",
                ),
            ),
        ),
    )


def _prepared(policy: HwpOperatePolicy, cells: dict[str, str]):
    return prepare_table_fill(
        _candidate(),
        _table(),
        1,
        HwpOperateData(cells=cells),
        policy,
        HwpOperatePostconditions(),
    )


# --- 핵심: EL.+39.3m 셀에 39.3 을 넣으면 39.3 이 된다 ------------------------


def test_prefixed_cell_accepts_a_bare_number_when_display_format_is_released() -> None:
    prepared = _prepared(
        HwpOperatePolicy(preserve_display_format=False),
        {"B2": "39.3"},
    )

    assert prepared.replacements == (("B2", "39.3"),)
    command = prepared.request.commands[-1]
    assert isinstance(command, SetCellTextCommand)
    assert command.text == "39.3"


def test_display_format_preservation_is_what_used_to_swallow_the_edit() -> None:
    """분리 전에는 이 경로밖에 없었고, 편집이 조용히 사라졌다."""
    plan = plan_table_cell_edits(
        _table(),
        (("B2", "39.3"),),
        numeric_value_mode="display",
    )

    assert plan.edits == ()
    assert len(plan.format_reverted) == 1
    reverted = plan.format_reverted[0]
    assert (reverted.address, reverted.requested) == ("B2", "39.3")
    assert reverted.rebuilt == "EL.+39.3m"
    assert reverted.existing == "EL.+39.3m"


# --- 두 파라미터 분리의 존재 이유: 접두어는 버리고 글꼴은 지킨다 -------------


def test_releasing_the_display_format_still_preserves_the_character_style() -> None:
    prepared = _prepared(
        HwpOperatePolicy(
            preserve_display_format=False,
            preserve_character_style=True,
        ),
        {"B2": "39.3"},
    )

    command = prepared.request.commands[-1]
    assert isinstance(command, SetCellTextCommand)
    assert command.text == "39.3"
    # 글자 서식을 살리는 네이티브 형식이어야 한다. 그리고 expected_text 가 실려야
    # ActionTextPatch.cpp 의 STALE_CELL_TEXT 검사가 계속 동작한다.
    assert command.preserve_style is True
    assert command.expected_text == "EL.+39.3m"
    assert prepared.native_protocol == 12
    wire = encode_action_request(prepared.request)
    expected = b64encode("EL.+39.3m".encode("utf-8")).decode("ascii")
    replacement = b64encode("39.3".encode("utf-8")).decode("ascii")
    assert f"SET_CELL_TEXT\tB2\t{expected}\t{replacement}\t1" in wire


def test_releasing_both_matches_the_old_plain_command_exactly() -> None:
    """둘 다 끄면 예전 preserve_style=False 와 같은 3필드 명령이다."""
    split = _prepared(
        HwpOperatePolicy(
            preserve_display_format=False,
            preserve_character_style=False,
        ),
        {"B2": "39.3"},
    )
    legacy = _prepared(HwpOperatePolicy(preserve_style=False), {"B2": "39.3"})

    command = split.request.commands[-1]
    assert isinstance(command, SetCellTextCommand)
    assert command.preserve_style is False
    assert command.expected_text is None
    assert split.native_protocol == 9
    assert encode_action_request(split.request) == encode_action_request(legacy.request)


def test_the_two_parameters_control_different_things() -> None:
    """이름이 가리키는 대상이 실제로 다른지 확인한다.

    표시 형식만 끄면 문자열이 바뀌고 글자 서식 보존은 남는다. 글자 서식만 끄면
    문자열은 그대로 재조립되고 서식 보존만 사라진다.
    """
    display_off = _prepared(
        HwpOperatePolicy(preserve_display_format=False),
        {"B3": "41.9"},
    )
    style_off = _prepared(
        HwpOperatePolicy(preserve_character_style=False),
        {"B3": "41.9"},
    )

    assert display_off.replacements == (("B3", "41.9"),)
    # 표시 형식을 유지하면 접두어·부호·단위가 다시 붙는다.
    assert style_off.replacements == (("B3", "EL.+41.9m"),)
    assert all(
        getattr(command, "preserve_format", True) is False
        for command in style_off.request.commands[2:]
    )


# --- 안전 (필수): 정책 인자를 안 준 요청은 변경 전과 바이트 단위로 같다 ------


# 변경 전 코드(커밋 5ba6dd8)에 같은 시나리오를 넣어 뽑은 네이티브 wire 의
# SHA-256 이다. 이 값이 흔들리면 아무 파라미터도 주지 않은 기존 요청의 동작이
# 달라졌다는 뜻이다.
_DEFAULT_FILL_WIRE_SHA256 = (
    "f2d3e813a910603c3698a6f337baf2392a6f0e38600e0a79e123b6ba9ddaa5ce"
)
_DEFAULT_EXPAND_WIRE_SHA256 = (
    "9a8367e2ddc668b6860041570e824867acb0f3c773b6b9f0dc939607a780c69f"
)
_DEFAULT_FILL_WIRE = (
    "HCA1\n"
    "DOC\t17\tQzovZG9jdW1lbnRzL3NhbXBsZS5od3A=\n"
    "SELECT_CONTROL\tdGFibGUtMTc=\n"
    "CAPTURE_TABLE\n"
    "PATCH_TEXT\tCELL\tdGFibGUtMTc=\tA3\t1\t1\tMg==\tOQ==\t1\n"
    "PATCH_TEXT\tCELL\tdGFibGUtMTc=\tB3\t1\t1\tKzQxLjc=\tKzQxLjk=\t1\n"
    "PATCH_TEXT\tCELL\tdGFibGUtMTc=\tC3\t1\t1\tNjc=\tNDM=\t1\n"
    "PATCH_TEXT\tCELL\tdGFibGUtMTc=\tC3\t1\t1\tMiwzNDU=\tOSw4NzY=\t1\n"
    "END"
)
_DEFAULT_EXPAND_WIRE = (
    "HCA1\n"
    "DOC\t17\tQzovZG9jdW1lbnRzL3NhbXBsZS5od3A=\n"
    "SELECT_CONTROL\tdGFibGUtMTc=\n"
    "CAPTURE_TABLE\n"
    "CELL\tA4\n"
    "RUN\tTableAppendRow\n"
    "SET_CELL_TEXT\tB3\tRUwuKzQxLjdt\tRUwuKzQxLjlt\t1\n"
    "SET_CELL_TEXT\tB5\t7IOIIO2WiQ==\n"
    "END"
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_default_policy_table_fill_wire_is_byte_identical_to_before() -> None:
    prepared = _prepared(
        HwpOperatePolicy(),
        {"B3": "EL.+41.9m", "C3": "9,876\n(43)", "A3": "항목 9"},
    )

    wire = encode_action_request(prepared.request)
    assert wire == _DEFAULT_FILL_WIRE
    assert _sha256(wire) == _DEFAULT_FILL_WIRE_SHA256
    assert prepared.native_protocol == 12


def test_default_policy_expand_and_fill_wire_is_byte_identical_to_before() -> None:
    expanded = prepare_expand_and_fill(
        _candidate(),
        _table(),
        HwpOperateData(cells={"B3": "EL.+41.9m", "B5": "새 행"}),
        HwpOperatePolicy(),
    )

    wire = encode_action_request(expanded.request)
    assert wire == _DEFAULT_EXPAND_WIRE
    assert _sha256(wire) == _DEFAULT_EXPAND_WIRE_SHA256
    assert expanded.native_protocol == 12
    assert expanded.rows_added == 1


def test_default_policy_equals_the_legacy_single_switch() -> None:
    """예전 요청은 preserve_style 하나만 주었다. 그 뜻이 보존되어야 한다."""
    default = HwpOperatePolicy()
    legacy_on = HwpOperatePolicy(preserve_style=True)
    legacy_off = HwpOperatePolicy(preserve_style=False)

    assert default.preserve_display_format is True
    assert default.preserve_character_style is True
    assert legacy_on == default
    assert legacy_off.preserve_display_format is False
    assert legacy_off.preserve_character_style is False
    assert default.numeric_value_mode == "infer"
    assert default.scale_conflict == "reject"


# --- 안전 (필수): 배율 충돌 검사는 기본값에서 그대로 막는다 -----------------


def _scaled_table() -> StructureTable:
    """머리글이 `천㎡` 인 표. 이 열에 2000 을 쓰면 1000배 틀릴 수 있다."""
    cells = (
        _cell("A1", 0, 0, "구분"),
        _cell("B1", 0, 1, "면적(천㎡)"),
        _cell("A2", 1, 0, "항목 1"),
        _cell("B2", 1, 1, "1,234"),
        _cell("A3", 2, 0, "항목 2"),
        _cell("B3", 2, 1, "2,345"),
    )
    return StructureTable(
        table_ref="table-ref-00000002",
        control_instance_id="table-18",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=3,
        columns=2,
        merges=(),
        cells=cells,
    )


# 머리글은 천 단위, 표 위 문단은 백만 단위라고 말한다. 어느 쪽을 고르든
# 1000배 틀릴 수 있는 상황이다.
_CONFLICTING_SURROUNDINGS = ("면적 단위는 ×1,000,000 기준이다",)


def test_scale_conflict_still_stops_the_edit_by_default() -> None:
    with pytest.raises(TableFormatAmbiguity) as raised:
        _ = plan_table_cell_edits(
            _scaled_table(),
            (("B3", "2000"),),
            numeric_value_mode="display",
            surrounding_texts=_CONFLICTING_SURROUNDINGS,
        )

    assert "서로 다른 표시 배율" in str(raised.value)


def test_scale_conflict_still_stops_the_edit_through_the_default_policy() -> None:
    """공개 기본값(scale_conflict='reject')으로도 그대로 막혀야 한다."""
    assert HwpOperatePolicy().scale_conflict == "reject"

    with pytest.raises(TableFormatAmbiguity):
        _ = plan_table_cell_edits(
            _scaled_table(),
            (("B3", "2000"),),
            numeric_value_mode="display",
            surrounding_texts=_CONFLICTING_SURROUNDINGS,
            scale_conflict=HwpOperatePolicy().scale_conflict,
        )


def test_scale_conflict_is_only_bypassed_when_asked_explicitly() -> None:
    plan = plan_table_cell_edits(
        _scaled_table(),
        (("B3", "2000"),),
        numeric_value_mode="display",
        surrounding_texts=_CONFLICTING_SURROUNDINGS,
        scale_conflict="ignore_scale",
    )

    assert plan.edits[0].replacement == "2,000"


def test_merged_cell_owner_guard_is_untouched() -> None:
    """비소유 셀에 쓰면 다른 칸이 조용히 바뀐다. 이 검사는 유지되어야 한다."""
    merged = StructureTable(
        table_ref="table-ref-00000003",
        control_instance_id="table-19",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=2,
        columns=2,
        merges=(),
        cells=(
            _cell("A1", 0, 0, "머리글"),
            _cell("B1", 0, 1, "값"),
            _cell("A2", 1, 0, "소유 셀"),
            _cell("B2", 1, 1, "소유 셀", owner="A2"),
        ),
    )

    with pytest.raises(Exception) as raised:
        _ = plan_table_cell_edits(
            merged,
            (("B2", "새 값"),),
            numeric_value_mode="display",
        )

    assert "병합된 A2 셀로만" in str(raised.value)


# --- 편집 0건을 executed 로 보고하지 않는다 ---------------------------------


def _resolution() -> WorkflowResolution:
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


def _run_fill(cells: dict[str, str], policy: HwpOperatePolicy):
    structure = SimpleNamespace(page=1, page_count=1, paragraphs=())
    resolved = SimpleNamespace(table=_table(), table_index=1, candidates=())
    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            side_effect=(SimpleNamespace(), SimpleNamespace(current_page=1)),
        ),
        patch(
            "hwp_live_session_table_fill.inspect_candidate_structure",
            side_effect=(structure, structure),
        ),
        patch(
            "hwp_live_session_table_fill.resolve_workflow_table",
            return_value=resolved,
        ),
        patch(
            "hwp_live_session_table_fill.execute_native_actions",
            return_value=SimpleNamespace(
                commands_executed=1,
                elapsed_microseconds=100,
            ),
        ),
        patch("hwp_live_session_table_fill.verify_table_fill"),
    ):
        result, _ = operate_table_fill(
            _candidate(),
            cast(object, object()),
            _resolution(),
            HwpOperateTarget(
                kind="table",
                page_hint=1,
                control_instance_id="table-17",
            ),
            HwpOperateData(cells=cells),
            policy,
            HwpOperatePostconditions(),
            allow_document_change=True,
        )
    return result


def test_format_reverted_zero_edit_is_not_reported_as_executed() -> None:
    result = _run_fill({"B2": "39.3"}, HwpOperatePolicy())

    assert result is not None
    assert result.status != "executed"
    assert result.status == "needs_input"
    assert result.required_inputs == ("inputs.policy.preserve_display_format",)
    assert "B2" in result.message
    assert "39.3" in result.message


def test_already_equal_zero_edit_is_still_reported_as_executed() -> None:
    """ "이미 같아서 안 했다"는 종전대로 executed 다. 두 상황은 다르다."""
    result = _run_fill({"B2": "EL.+39.3m"}, HwpOperatePolicy())

    assert result is not None
    assert result.status == "executed"
    assert result.modified is False
    assert "이미 모두 일치" in result.message


def test_zero_edit_revert_and_already_equal_report_different_statuses() -> None:
    reverted = _run_fill({"B2": "39.3"}, HwpOperatePolicy())
    equal = _run_fill({"B2": "EL.+39.3m"}, HwpOperatePolicy())

    assert reverted is not None and equal is not None
    assert reverted.status != equal.status


# --- 실패 안내가 값을 지어내라고 시키지 않는다 -------------------------------


_INVENTED_VALUE_PHRASES = (
    "최종 표시 문자열을 직접 지정",
    "단위·괄호·줄바꿈을 포함한",
)


def test_format_escape_hint_never_asks_for_an_invented_display_string() -> None:
    assert all(phrase not in FORMAT_ESCAPE_HINT for phrase in _INVENTED_VALUE_PHRASES)
    assert "preserve_display_format" in FORMAT_ESCAPE_HINT


def test_format_ambiguity_message_points_at_a_parameter_that_exists() -> None:
    ambiguity = TableFormatAmbiguity("표시 형식을 하나로 정하지 못했습니다")

    assert all(phrase not in str(ambiguity) for phrase in _INVENTED_VALUE_PHRASES)
    assert "preserve_display_format" in str(ambiguity)
    assert "preserve_display_format" in HwpOperatePolicy.model_fields


def _needs_input(required: tuple[str, ...]) -> OperationResult:
    return OperationResult(
        request_id="format-operation",
        status="needs_input",
        query="fill",
        registry_entries=1,
        lookup_microseconds=0,
        required_inputs=required,
        message="표시 형식을 하나로 정하지 못했습니다",
        retry_safe=True,
    )


def test_public_needs_input_names_numeric_value_mode_not_cells() -> None:
    result = to_public_action_result(
        _needs_input(("inputs.policy.numeric_value_mode",)),
        (),
    )

    assert result.required_inputs == ("numeric_value_mode",)
    assert "cells" not in result.required_inputs
    assert result.input_guidance
    assert all(
        phrase not in item
        for item in result.input_guidance
        for phrase in _INVENTED_VALUE_PHRASES
    )


def test_public_needs_input_names_preserve_display_format() -> None:
    result = to_public_action_result(
        _needs_input(("inputs.policy.preserve_display_format",)),
        (),
    )

    assert result.required_inputs == ("preserve_display_format",)
    assert result.input_guidance
    assert any("preserve_display_format" in item for item in result.input_guidance)


def test_every_reported_required_input_is_a_real_public_parameter() -> None:
    """모델에게 알려 준 이름은 실제로 고칠 수 있는 파라미터여야 한다."""
    from inspect import signature

    from hwp_public_tools import HwpPublicTools

    parameters = signature(HwpPublicTools.hwp_fill_table).parameters
    for internal in (
        "inputs.policy.numeric_value_mode",
        "inputs.policy.preserve_display_format",
        "inputs.policy.scale_conflict",
    ):
        reported = to_public_action_result(
            _needs_input((internal,)), ()
        ).required_inputs
        assert len(reported) == 1
        assert reported[0] in parameters, reported[0]


def test_public_fill_table_exposes_both_split_parameters() -> None:
    from inspect import signature

    from hwp_public_tools import HwpPublicTools

    parameters = signature(HwpPublicTools.hwp_fill_table).parameters

    assert parameters["preserve_display_format"].default is True
    assert parameters["preserve_character_style"].default is True
    assert parameters["numeric_value_mode"].default == "infer"
    assert parameters["scale_conflict"].default == "reject"
    assert "preserve_style" not in parameters

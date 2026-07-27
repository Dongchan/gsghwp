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

from hwp_live_native_action_contract import encode_action_request  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeActionRequest,
    SetCellTextCommand,
    TextPatchCommand,
)
from hwp_live_session_table_fill import operate_table_fill  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    StructureCell,
    StructurePosition,
    StructureTable,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_workflow_table import (  # noqa: E402
    PreparedWorkflowTableFill,
    prepare_table_fill,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    WorkflowCandidate,
    WorkflowResolution,
)
from hwp_table_format_inference import (  # noqa: E402
    TableFormatAmbiguity,
    infer_table_cell_edits,
)


def _table(
    *,
    header: str = "면적(천㎡)",
    values: tuple[str, ...] = ("1,234\n(56)", "2,345\n(67)", "3,456\n(78)", ""),
) -> StructureTable:
    cells: list[StructureCell] = []
    headers = ("구분", header, "비고")
    for column, text in enumerate(headers):
        address = f"{'ABC'[column]}1"
        cells.append(
            StructureCell(
                address=address,
                row=0,
                column=column,
                owner_address=address,
                text=text,
            )
        )
    for row, value in enumerate(values, start=1):
        for column, text in enumerate((f"항목 {row}", value, "")):
            address = f"{'ABC'[column]}{row + 1}"
            cells.append(
                StructureCell(
                    address=address,
                    row=row,
                    column=column,
                    owner_address=address,
                    text=text,
                )
            )
    return StructureTable(
        table_ref="table-ref-00000001",
        control_instance_id="table-17",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=1,
        page_end=1,
        rows=5,
        columns=3,
        merges=(),
        cells=tuple(cells),
    )


def test_preserves_number_grouping_parentheses_and_line_breaks() -> None:
    edits = infer_table_cell_edits(
        _table(),
        (("B3", "9,876\n(43)"),),
        numeric_value_mode="display",
    )

    assert edits[0].replacement == "9,876\n(43)"
    assert tuple(
        (patch.expected_text, patch.replacement, patch.occurrence)
        for patch in edits[0].patches
    ) == (("67", "43", 1), ("2,345", "9,876", 1))


def test_scale_inference_returns_display_and_base_candidates_without_mutation() -> None:
    table = _table(values=("1,234", "2,345", "3,456", ""))

    with pytest.raises(TableFormatAmbiguity) as raised:
        _ = infer_table_cell_edits(
            table,
            (("B3", "1234000"),),
            numeric_value_mode="infer",
        )

    candidates = raised.value.candidates
    assert tuple(candidate.numeric_value_mode for candidate in candidates) == (
        "display",
        "base",
    )
    assert tuple(candidate.replacement for candidate in candidates) == (
        "1,234,000",
        "1,234",
    )
    assert all(candidate.inferred_scale == 1_000 for candidate in candidates)


def test_external_unit_context_keeps_unscaled_tokens_out_of_base_candidate() -> None:
    table = _table(header="합계")

    with pytest.raises(TableFormatAmbiguity) as raised:
        _ = infer_table_cell_edits(
            table,
            (("B3", "2345000\n(67)"),),
            numeric_value_mode="infer",
            surrounding_texts=("단위 : 천㎡, ( ) : 개소",),
        )

    assert tuple(candidate.replacement for candidate in raised.value.candidates) == (
        "2,345,000\n(67)",
        "2,345\n(67)",
    )


def test_external_unit_context_accepts_display_value_matching_existing_magnitude() -> (
    None
):
    edits = infer_table_cell_edits(
        _table(header="합계"),
        (("B3", "2,346\n(68)"),),
        numeric_value_mode="infer",
        surrounding_texts=("단위 : 천㎡, ( ) : 개소",),
    )

    assert edits[0].replacement == "2,346\n(68)"


def test_korean_prose_digit_syllables_are_not_treated_as_scale_factors() -> None:
    edits = infer_table_cell_edits(
        _table(header="합계"),
        (("B3", "2345000\n(67)"),),
        numeric_value_mode="infer",
        surrounding_texts=("서울특별시 사업구역",),
    )

    assert edits[0].replacement == "2,345,000\n(67)"


def test_base_value_mode_uses_generic_english_scale_word() -> None:
    table = _table(
        header="mass (million kg)",
        values=("1.2", "2.3", "3.4", ""),
    )

    edits = infer_table_cell_edits(
        table,
        (("B3", "4500000"),),
        numeric_value_mode="base",
    )

    assert edits[0].replacement == "4.5"


def test_empty_cell_uses_unique_repeated_column_pattern() -> None:
    edits = infer_table_cell_edits(
        _table(),
        (("B5", "9876\n(43)"),),
        numeric_value_mode="display",
    )

    assert edits[0].expected_text == ""
    assert edits[0].replacement == "9,876\n(43)"
    assert edits[0].patches == ()


def test_layout_mismatch_is_confirmation_required_not_silently_reflowed() -> None:
    with pytest.raises(TableFormatAmbiguity, match="줄바꿈"):
        _ = infer_table_cell_edits(
            _table(),
            (("B3", "9,876 (43)"),),
            numeric_value_mode="display",
        )


def test_native_preserve_flags_are_encoded_only_for_new_commands() -> None:
    request = NativeActionRequest(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        commands=(
            TextPatchCommand(
                target="table_cell",
                expected_text="2,345",
                replacement="9,876",
                occurrence=1,
                match_case=True,
                table_instance_id="table-17",
                cell_address="B3",
                preserve_format=True,
            ),
            SetCellTextCommand(
                address="B5",
                text="9,876\n(43)",
                expected_text="",
                preserve_style=True,
            ),
        ),
    )

    payload = encode_action_request(request)

    patch_line = next(
        line for line in payload.splitlines() if line.startswith("PATCH_TEXT")
    )
    cell_line = next(
        line for line in payload.splitlines() if line.startswith("SET_CELL_TEXT")
    )
    assert patch_line.endswith("\t1")
    assert cell_line.endswith("\t1")
    assert len(cell_line.split("\t")) == 5


def test_preserve_style_policy_routes_cell_changes_to_minimal_native_patches() -> None:
    candidate = cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                document_id=17,
                full_name="C:/documents/sample.hwp",
                document=SimpleNamespace(
                    DocumentID=17,
                    FullName="C:/documents/sample.hwp",
                ),
            ),
        ),
    )

    prepared = prepare_table_fill(
        candidate,
        _table(),
        1,
        HwpOperateData(cells={"B3": "9,876\n(43)"}),
        HwpOperatePolicy(
            preserve_style=True,
            numeric_value_mode="display",
        ),
        HwpOperatePostconditions(),
    )

    patches = tuple(
        command
        for command in prepared.request.commands
        if isinstance(command, TextPatchCommand)
    )
    assert prepared.native_protocol == 12
    assert prepared.replacements == (("B3", "9,876\n(43)"),)
    assert len(patches) == 2
    assert all(command.preserve_format for command in patches)
    assert not any(
        isinstance(command, SetCellTextCommand) for command in prepared.request.commands
    )


def test_verified_table_fill_result_survives_operation_verification_gate() -> None:
    resolution = WorkflowResolution(
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
    prepared = PreparedWorkflowTableFill(
        request=NativeActionRequest(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            commands=(),
        ),
        table_index=1,
        control_instance_id="table-17",
        replacements=(("B3", "2,346\n(68)"),),
        native_protocol=12,
    )
    target = HwpOperateTarget(
        kind="table",
        page_hint=1,
        control_instance_id="table-17",
    )
    data = HwpOperateData(cells={"B3": "2,346\n(68)"})
    candidate = cast(
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
    structure = SimpleNamespace(page=1, page_count=1, paragraphs=())
    resolved = SimpleNamespace(table=_table(), table_index=1, candidates=())
    with (
        patch(
            "hwp_live_session_table_fill.read_native_snapshot",
            side_effect=(
                SimpleNamespace(),
                SimpleNamespace(current_page=1),
            ),
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
            "hwp_live_session_table_fill.prepare_table_fill",
            return_value=prepared,
        ),
        patch(
            "hwp_live_session_table_fill.execute_native_actions",
            return_value=SimpleNamespace(
                commands_executed=3,
                elapsed_microseconds=100,
            ),
        ),
        patch("hwp_live_session_table_fill.verify_table_fill"),
    ):
        result, _ = operate_table_fill(
            candidate,
            cast(object, object()),
            resolution,
            target,
            data,
            HwpOperatePolicy(),
            HwpOperatePostconditions(),
            allow_document_change=True,
        )

    assert result is not None
    assert result.status == "executed"
    assert result.verified is True
    assert result.verification == "native_snapshot_before_after"
    assert result.native_protocol == 12

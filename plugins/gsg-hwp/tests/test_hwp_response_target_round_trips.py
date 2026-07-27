from __future__ import annotations

import sys
from pathlib import Path
from typing import final

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_structure_contract import (  # noqa: E402
    DocumentStructure,
    FastPageControl,
    FastPageInspection,
    StructureCell,
    StructurePosition,
    StructureTable,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateGuards,
    HwpOperateInputs,
    HwpOperateTarget,
    OperationResult,
)
from hwp_public_contract import PublicActionResult, PublicTableTarget  # noqa: E402
from hwp_public_table_target import PublicTableTargetStore  # noqa: E402
from hwp_public_table_tools import HwpPublicTableTools  # noqa: E402
from hwp_public_tools import HwpPublicTools  # noqa: E402
from hwp_live_workflow_table_resolver import resolve_workflow_table  # noqa: E402


def _table(control_id: str, header: str) -> StructureTable:
    cell = StructureCell(
        address="A1",
        row=0,
        column=0,
        owner_address="A1",
        text=header,
    )
    return StructureTable(
        table_ref=f"table-ref-{control_id:0>16}",
        control_instance_id=control_id,
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=2,
        page_end=2,
        rows=1,
        columns=1,
        merges=(),
        cells=(cell,),
    )


def _structure(*tables: StructureTable) -> DocumentStructure:
    return DocumentStructure(
        selector="document-id:17",
        document_id=17,
        full_name="C:/documents/sample.hwp",
        window_handle=170,
        page=2,
        page_count=3,
        state_token="a" * 64,
        page_text="",
        paragraphs=(),
        controls=(),
        tables=tables,
    )


@final
class _TableExecutor:
    def __init__(self, structure: DocumentStructure) -> None:
        self.structure = structure
        self.execute_calls: list[HwpOperateInputs] = []
        self.structure_calls: list[tuple[str | None, int]] = []

    async def read_table_structure(
        self,
        document_path: str | None,
        page: int,
    ) -> DocumentStructure:
        self.structure_calls.append((document_path, page))
        return self.structure

    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult:
        _ = guards
        self.execute_calls.append(inputs)
        return OperationResult(
            request_id=inputs.request_id,
            idempotency_status="committed",
            result_digest="b" * 64,
            status="executed",
            changed=True,
            query=intent,
            registry_entries=1,
            lookup_microseconds=0,
            message="changed",
            current_page=2,
            modified=True,
            verified=True,
            updated_addresses=("A1",),
            retry_safe=True,
        )


def test_narrowed_single_table_is_selected_inside_one_public_call() -> None:
    # Given
    executor = _TableExecutor(
        _structure(_table("table-other", "Other"), _table("table-name", "Name"))
    )
    tools = HwpPublicTools(executor)

    async def fill() -> PublicActionResult:
        return await tools.hwp_fill_table(
            operation_id="single-narrowed-table",
            records=[{"Name": "value"}],
            page=2,
            headers=("Name",),
        )

    # When
    result = anyio.run(fill)

    # Then
    assert len(executor.execute_calls) == 1
    assert executor.execute_calls[0].target is not None
    assert executor.execute_calls[0].target.control_instance_id == "table-name"
    assert result.status == "succeeded"
    assert result.selected_target_id == "table-name"


@pytest.mark.parametrize(
    ("headers", "tables", "candidate_count"),
    [
        (("Missing",), (_table("table-only", "Other"),), 1),
        (
            ("Name",),
            (_table("table-first", "Name"), _table("table-second", "Name")),
            2,
        ),
    ],
)
def test_single_global_fallback_and_multiple_matches_still_need_target(
    headers: tuple[str, ...],
    tables: tuple[StructureTable, ...],
    candidate_count: int,
) -> None:
    # Given
    executor = _TableExecutor(_structure(*tables))
    tools = HwpPublicTools(executor)

    async def fill() -> PublicActionResult:
        return await tools.hwp_fill_table(
            operation_id="unsafe-auto-target",
            records=[{"Name": "value"}],
            page=2,
            headers=headers,
        )

    # When
    result = anyio.run(fill)

    # Then
    assert executor.execute_calls == []
    assert result.status == "needs_target"
    assert len(result.target_candidates) == candidate_count


def test_fast_inspection_id_repeats_without_semantic_target_reresolution() -> None:
    # Given
    target_id = "2002765770"
    store = PublicTableTargetStore()
    store.remember_inspection(
        FastPageInspection(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            page=2,
            page_count=3,
            text="",
            controls=(
                FastPageControl(
                    control_type="tbl",
                    instance_id=target_id,
                    anchor=StructurePosition(list_id=7, paragraph=3, character=0),
                    rows=4,
                    columns=16,
                ),
            ),
        )
    )
    executor = _TableExecutor(_structure(_table("unrelated-table", "Other")))
    tools = HwpPublicTableTools(executor, store)

    async def repeat() -> PublicActionResult:
        return await tools.hwp_repeat_table_template(
            operation_id="repeat-fast-inspection-id",
            target=PublicTableTarget(target_id=target_id),
            count=2,
        )

    # When
    result = anyio.run(repeat)

    # Then
    assert executor.structure_calls == []
    assert len(executor.execute_calls) == 1
    inputs = executor.execute_calls[0]
    assert inputs.target is not None
    assert inputs.target.page_hint == 2
    assert inputs.target.control_instance_id == target_id
    assert inputs.recipe is not None
    assert inputs.recipe.table_template is not None
    assert inputs.recipe.table_template.source_page == 2
    assert inputs.recipe.table_template.source_control_id == target_id
    assert result.status == "succeeded"


def test_missing_exact_control_id_does_not_return_unrelated_semantic_candidates() -> (
    None
):
    # Given
    structure = _structure(_table("unrelated-table", "Other"))
    target = HwpOperateTarget(
        kind="table",
        binding="active",
        page_hint=2,
        control_instance_id="missing-exact-id",
        match_policy="return_candidates",
    )

    # When
    resolved = resolve_workflow_table(structure, target)

    # Then
    assert resolved.table is None
    assert resolved.table_index is None
    assert resolved.candidates == ()

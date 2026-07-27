from __future__ import annotations

from dataclasses import dataclass

from hwp_live_structure_contract import DocumentStructure, StructureTable
from hwp_operation_contract import HwpOperateTarget, WorkflowTargetCandidate


@dataclass(frozen=True, slots=True)
class ResolvedWorkflowTable:
    table: StructureTable | None
    table_index: int | None
    candidates: tuple[WorkflowTargetCandidate, ...]


def workflow_page(target: HwpOperateTarget | None) -> int:
    if target is not None and target.page_hint is not None:
        return target.page_hint
    return 0


def normalize_table_text(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _header_preview(table: StructureTable) -> tuple[str, ...]:
    values: list[str] = []
    for cell in sorted(table.cells, key=lambda item: (item.row, item.column)):
        text = cell.text.strip()
        if text and text not in values:
            values.append(text)
        if len(values) == 12:
            break
    return tuple(values)


def workflow_table_candidate(
    table: StructureTable,
    table_index: int,
    page: int,
) -> WorkflowTargetCandidate:
    return WorkflowTargetCandidate(
        page=page,
        table_index=table_index,
        rows=table.rows,
        columns=table.columns,
        caption=None if table.caption is None else table.caption.text,
        header_preview=_header_preview(table),
    )


def resolve_workflow_table(
    snapshot: DocumentStructure,
    target: HwpOperateTarget | None,
) -> ResolvedWorkflowTable:
    indexed = tuple(enumerate(snapshot.tables, start=1))
    filtered = indexed
    if target is not None and target.control_instance_id is not None:
        filtered = tuple(
            item
            for item in filtered
            if item[1].control_instance_id == target.control_instance_id
        )
    if target is not None and target.table_index is not None:
        filtered = tuple(item for item in filtered if item[0] == target.table_index)
    if target is not None and target.caption_contains is not None:
        expected = normalize_table_text(target.caption_contains)
        filtered = tuple(
            item
            for item in filtered
            if item[1].caption is not None
            and expected in normalize_table_text(item[1].caption.text)
        )
    if target is not None and target.header_signature:
        headers = tuple(
            normalize_table_text(value) for value in target.header_signature
        )
        filtered = tuple(
            item
            for item in filtered
            if all(
                any(header in normalize_table_text(cell.text) for cell in item[1].cells)
                for header in headers
            )
        )
    if len(filtered) == 1:
        table_index, table = filtered[0]
        return ResolvedWorkflowTable(table, table_index, ())
    if not filtered and target is not None and target.control_instance_id is not None:
        return ResolvedWorkflowTable(None, None, ())
    source = filtered if filtered else indexed
    return ResolvedWorkflowTable(
        None,
        None,
        tuple(
            workflow_table_candidate(table, index, snapshot.page)
            for index, table in source[:3]
        ),
    )

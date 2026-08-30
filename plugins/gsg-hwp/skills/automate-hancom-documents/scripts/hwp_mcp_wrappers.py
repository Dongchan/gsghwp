from __future__ import annotations

from typing import Final, Literal, NamedTuple, Protocol, TypeVar, cast
from uuid import uuid4

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from hwp_errors import HwpLiveError
from hwp_live_structure_contract import (
    DocumentStructure,
    PageParagraph,
    StructurePosition,
    StructureTable,
)
from hwp_mcp_result_envelope import normalize_production_result, transport_error_result
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
    canonical_workflow,
)


class HwpOperateDispatcher(Protocol):
    async def hwp_operate(
        self,
        intent: str,
        inputs: HwpOperateInputs | None = None,
        guards: HwpOperateGuards | None = None,
    ) -> OperationResult: ...


StructuredPayload = TypeVar("StructuredPayload", bound=BaseModel)
STRUCTURE_RESPONSE_UTF8_LIMIT: Final = 32_000
_STRUCTURE_TEXT_FIELD_LIMIT: Final = 1_024
_StructureResponseSection = Literal[
    "page_text_characters",
    "paragraph_text_characters",
    "cell_text_characters",
    "cell_formats",
    "paragraphs",
    "cells",
    "controls",
    "tables",
    "unsupported_records",
]


class _StructureResponseTruncation(NamedTuple):
    original_utf8_bytes: int
    omissions: tuple[tuple[_StructureResponseSection, int], ...]


def new_wrapper_request_id() -> str:
    return f"hwp-wrapper-{uuid4().hex}"


def _compact_utf8_bytes(payload: BaseModel) -> int:
    serialized = payload.model_dump_json(by_alias=True, exclude_none=True)
    return len(serialized.encode("utf-8"))


def _add_omission(
    omissions: dict[_StructureResponseSection, int],
    section: _StructureResponseSection,
    count: int,
) -> None:
    if count > 0:
        omissions[section] = omissions.get(section, 0) + count


def _replace_cell_text(
    structure: DocumentStructure,
    limit: int,
) -> DocumentStructure:
    tables: list[StructureTable] = []
    for table in structure.tables:
        cells = tuple(
            cell.model_copy(update={"text": cell.text[:limit]}) for cell in table.cells
        )
        tables.append(table.model_copy(update={"cells": cells}))
    return structure.model_copy(update={"tables": tuple(tables)})


def _replace_paragraph_text(
    structure: DocumentStructure,
    targets: tuple[PageParagraph, ...],
) -> DocumentStructure:
    target_set = frozenset(targets)
    paragraphs = tuple(
        paragraph.model_copy(
            update={"text": paragraph.text[:_STRUCTURE_TEXT_FIELD_LIMIT]}
        )
        if paragraph in target_set
        else paragraph
        for paragraph in structure.paragraphs
    )
    return structure.model_copy(update={"paragraphs": paragraphs})


def _reduce_structure_response(
    structure: DocumentStructure,
    omissions: dict[_StructureResponseSection, int],
    protected_positions: frozenset[StructurePosition],
) -> DocumentStructure:
    cell_format_count = sum(len(table.cell_formats) for table in structure.tables)
    if cell_format_count:
        _add_omission(omissions, "cell_formats", cell_format_count)
        tables = tuple(
            table.model_copy(update={"cell_formats": ()}) for table in structure.tables
        )
        return structure.model_copy(update={"tables": tables})
    if structure.page_text:
        _add_omission(omissions, "page_text_characters", len(structure.page_text))
        return structure.model_copy(update={"page_text": ""})

    cell_characters = sum(
        max(0, len(cell.text) - _STRUCTURE_TEXT_FIELD_LIMIT)
        for table in structure.tables
        for cell in table.cells
    )
    if cell_characters:
        _add_omission(omissions, "cell_text_characters", cell_characters)
        return _replace_cell_text(structure, _STRUCTURE_TEXT_FIELD_LIMIT)

    cell_characters = sum(
        len(cell.text) for table in structure.tables for cell in table.cells
    )
    if cell_characters:
        _add_omission(omissions, "cell_text_characters", cell_characters)
        return _replace_cell_text(structure, 0)

    unprotected_paragraphs = tuple(
        paragraph
        for paragraph in structure.paragraphs
        if paragraph.position not in protected_positions
    )
    paragraph_characters = sum(
        max(0, len(paragraph.text) - _STRUCTURE_TEXT_FIELD_LIMIT)
        for paragraph in unprotected_paragraphs
    )
    if paragraph_characters:
        _add_omission(omissions, "paragraph_text_characters", paragraph_characters)
        return _replace_paragraph_text(structure, unprotected_paragraphs)

    if unprotected_paragraphs:
        kept_count = len(unprotected_paragraphs) // 2
        kept_unprotected = frozenset(unprotected_paragraphs[:kept_count])
        kept_paragraphs = tuple(
            paragraph
            for paragraph in structure.paragraphs
            if paragraph.position in protected_positions
            or paragraph in kept_unprotected
        )
        omitted_count = len(unprotected_paragraphs) - kept_count
        _add_omission(omissions, "paragraphs", omitted_count)
        return structure.model_copy(update={"paragraphs": kept_paragraphs})

    if structure.controls:
        kept_count = len(structure.controls) // 2
        _add_omission(omissions, "controls", len(structure.controls) - kept_count)
        return structure.model_copy(
            update={"controls": structure.controls[:kept_count]}
        )

    if structure.unsupported_records:
        kept_count = len(structure.unsupported_records) // 2
        omitted_count = len(structure.unsupported_records) - kept_count
        _add_omission(omissions, "unsupported_records", omitted_count)
        kept = structure.unsupported_records[:kept_count]
        return structure.model_copy(update={"unsupported_records": kept})

    if any(table.cells for table in structure.tables):
        shortened_tables: list[StructureTable] = []
        omitted_cells = 0
        for table in structure.tables:
            kept_count = len(table.cells) // 2
            omitted_cells += len(table.cells) - kept_count
            shortened_tables.append(
                table.model_copy(update={"cells": table.cells[:kept_count]})
            )
        _add_omission(omissions, "cells", omitted_cells)
        return structure.model_copy(update={"tables": tuple(shortened_tables)})

    if structure.tables:
        kept_count = len(structure.tables) // 2
        _add_omission(omissions, "tables", len(structure.tables) - kept_count)
        return structure.model_copy(update={"tables": structure.tables[:kept_count]})

    paragraph_characters = sum(
        max(0, len(paragraph.text) - _STRUCTURE_TEXT_FIELD_LIMIT)
        for paragraph in structure.paragraphs
    )
    if paragraph_characters:
        _add_omission(omissions, "paragraph_text_characters", paragraph_characters)
        return _replace_paragraph_text(structure, structure.paragraphs)

    if structure.paragraphs:
        kept_count = len(structure.paragraphs) // 2
        _add_omission(omissions, "paragraphs", len(structure.paragraphs) - kept_count)
        kept = structure.paragraphs[:kept_count]
        return structure.model_copy(update={"paragraphs": kept})
    return structure


def _bounded_structure_response(
    structure: DocumentStructure,
) -> tuple[DocumentStructure, _StructureResponseTruncation | None]:
    original_utf8_bytes = _compact_utf8_bytes(structure)
    if original_utf8_bytes <= STRUCTURE_RESPONSE_UTF8_LIMIT:
        return structure, None
    bounded = structure
    omissions: dict[_StructureResponseSection, int] = {}
    protected_positions = frozenset(
        position
        for table in structure.tables
        for position in (table.preceding_paragraph, table.following_paragraph)
        if position is not None
    )
    while _compact_utf8_bytes(bounded) > STRUCTURE_RESPONSE_UTF8_LIMIT:
        reduced = _reduce_structure_response(bounded, omissions, protected_positions)
        if reduced is bounded:
            break
        bounded = reduced
    return bounded, _StructureResponseTruncation(
        original_utf8_bytes=original_utf8_bytes,
        omissions=tuple(omissions.items()),
    )


def _response_truncation_fact(truncation: _StructureResponseTruncation) -> str:
    omitted = ",".join(f"{section}:{count}" for section, count in truncation.omissions)
    return " ".join(
        (
            "truncated=true",
            f"original_utf8_bytes={truncation.original_utf8_bytes}",
            f"limit_utf8_bytes={STRUCTURE_RESPONSE_UTF8_LIMIT}",
            "reason=utf8_byte_budget",
            f"omissions={omitted}",
        )
    )


def compact_structured_result(
    payload: StructuredPayload,
    *,
    summary: str,
) -> StructuredPayload:
    if isinstance(payload, DocumentStructure):
        bounded_payload, truncation = _bounded_structure_response(payload)
    else:
        bounded_payload = payload
        truncation = None
    facts: list[str] = []
    if isinstance(payload, DocumentStructure) and payload.unsupported_records:
        facts.append(f"unsupported={len(payload.unsupported_records)}")
    if truncation is not None:
        facts.append(_response_truncation_fact(truncation))
    reported_summary = " ".join((summary, *facts))
    return cast(
        StructuredPayload,
        CallToolResult(
            content=[TextContent(type="text", text=reported_summary)],
            structuredContent=bounded_payload.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
        ),
    )


async def dispatch_wrapper(
    operation: HwpOperateDispatcher,
    inputs: HwpOperateInputs,
) -> OperationResult:
    workflow = canonical_workflow(inputs)
    assert workflow is not None
    try:
        result = await operation.hwp_operate(workflow, inputs)
    except HwpLiveError as error:
        return transport_error_result(inputs, error)
    return normalize_production_result(result, inputs)

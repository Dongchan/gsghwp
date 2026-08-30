from __future__ import annotations

from typing import Literal

from pydantic import Field

from hwp_live_values import ContractModel


type VisibilityTemplateMismatchCode = Literal[
    "header_text_mismatch",
    "header_span_too_small",
    "slot_height_too_small",
    "slot_gaps_nonuniform",
    "missing_owner_cells",
    "ambiguous_multiple_header_structures",
    "missing_required_slot_cell",
]


class VisibilityTemplateMismatch(ContractModel):
    code: VisibilityTemplateMismatchCode
    reason: str = Field(min_length=1, max_length=500)
    expected: str = Field(max_length=500)
    observed: str = Field(max_length=2_000)


class VisibilityTemplateCellObservation(ContractModel):
    address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    owner_address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    raw_text: str = Field(max_length=200_000)
    compact_text: str = Field(max_length=200_000)
    is_owner: bool
    text_matches: bool
    span_ok: bool
    is_candidate: bool
    is_header: bool


class VisibilityTemplatePosition(ContractModel):
    address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    row: int = Field(ge=0)
    column: int = Field(ge=0)


class VisibilityTemplateObservation(ContractModel):
    table_ref: str = Field(min_length=16, max_length=80)
    control_instance_id: str | None = Field(default=None, max_length=100)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    owner_cells: tuple[VisibilityTemplateCellObservation, ...] = Field(
        max_length=20_000
    )
    missing_owner_addresses: tuple[str, ...] = Field(default=(), max_length=20_000)
    header_positions: tuple[VisibilityTemplatePosition, ...] = Field(
        default=(), max_length=500
    )
    slot_positions: tuple[VisibilityTemplatePosition, ...] = Field(
        default=(), max_length=500
    )
    slot_height: int | None = Field(default=None, ge=1)
    gaps: tuple[int, ...] = Field(default=(), max_length=500)
    condition_owner_cells_complete: bool
    condition_header_text_found: bool
    condition_header_span_found: bool
    condition_slot_height_minimum: bool
    condition_slot_gaps_uniform: bool
    condition_unambiguous: bool = True
    recognized: bool
    mismatch_codes: tuple[VisibilityTemplateMismatchCode, ...] = Field(
        default=(), max_length=20
    )
    mismatches: tuple[VisibilityTemplateMismatch, ...] = Field(
        default=(), max_length=20
    )

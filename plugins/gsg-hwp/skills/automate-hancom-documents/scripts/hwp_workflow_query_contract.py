from __future__ import annotations

import ntpath
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError
from typing_extensions import override

from hwp_live_values import ContractModel
from hwp_patch_plan_projection import PatchPlanComplete
from hwp_public_action_contract import PublicTextFormattingInput


class WorkflowLimits(ContractModel):
    workbooks: int = Field(default=8, ge=1, le=32)
    sheets_per_workbook: int = Field(default=128, ge=1, le=512)
    queries: int = Field(default=64, ge=1, le=256)
    rows_per_query: int = Field(default=10_000, ge=1, le=100_000)
    columns_per_query: int = Field(default=256, ge=1, le=16_384)
    cells: int = Field(default=50_000, ge=1, le=1_000_000)
    stdin_utf8_bytes: int = Field(default=2_000_000, ge=8_192, le=8_000_000)
    zip_members: int = Field(default=2_048, ge=1, le=10_000)
    zip_entry_bytes: int = Field(default=32_000_000, ge=1_024, le=256_000_000)
    zip_total_bytes: int = Field(default=128_000_000, ge=8_192, le=1_000_000_000)
    zip_compression_ratio: int = Field(default=200, ge=1, le=10_000)
    shared_strings: int = Field(default=200_000, ge=1, le=1_000_000)
    shared_string_bytes: int = Field(default=32_000_000, ge=1_024, le=256_000_000)
    xml_depth: int = Field(default=64, ge=4, le=256)
    xml_text_bytes: int = Field(default=64_000_000, ge=8_192, le=256_000_000)
    cell_chars: int = Field(default=200_000, ge=1, le=1_000_000)
    output_utf8_bytes: int = Field(default=1_000_000, ge=8_192, le=7_000_000)


class WorkbookInput(ContractModel):
    alias: str = Field(min_length=1, max_length=100)
    path: Path


class QueryInput(ContractModel):
    alias: str = Field(min_length=1, max_length=100)
    workbook: str = Field(min_length=1, max_length=100)
    sheet: str = Field(min_length=1, max_length=200)
    sheet_aliases: tuple[str, ...] = Field(default=(), max_length=100)
    header_row: int = Field(ge=1)
    candidate_header_rows: tuple[int, ...] = Field(default=(), max_length=100)
    key_column: str = Field(min_length=1, max_length=500)
    key_column_aliases: tuple[str, ...] = Field(default=(), max_length=100)
    key_value: str = Field(max_length=200_000)
    key_value_aliases: tuple[str, ...] = Field(default=(), max_length=100)
    columns: dict[str, tuple[str, ...]] = Field(min_length=1, max_length=256)


class BindingInput(ContractModel):
    query: str = Field(min_length=1, max_length=100)
    column: str = Field(min_length=1, max_length=100)
    target_id: str = Field(min_length=1, max_length=300)
    replacement: str = Field(default="{value}", min_length=1, max_length=1_000_000)


class WorkflowRequest(ContractModel):
    projection: PatchPlanComplete
    workbooks: tuple[WorkbookInput, ...] = Field(min_length=1, max_length=32)
    queries: tuple[QueryInput, ...] = Field(min_length=1, max_length=256)
    bindings: tuple[BindingInput, ...] = Field(min_length=1, max_length=1_000)
    formatting: PublicTextFormattingInput | None = None
    document_path: str | None = Field(default=None, max_length=32_767)
    limits: WorkflowLimits = WorkflowLimits()

    @model_validator(mode="after")
    def unique_aliases(self) -> WorkflowRequest:
        if len({item.alias for item in self.workbooks}) != len(self.workbooks):
            raise PydanticCustomError(
                "workflow_workbook_alias",
                "workbook aliases must be unique",
            )
        if len({item.alias for item in self.queries}) != len(self.queries):
            raise PydanticCustomError(
                "workflow_query_alias",
                "query aliases must be unique",
            )
        if len({item.id for item in self.projection.items}) != len(
            self.projection.items
        ):
            raise PydanticCustomError(
                "workflow_projection_id",
                "projection target IDs must be unique",
            )
        if len({item.target_id for item in self.bindings}) != len(self.bindings):
            raise PydanticCustomError(
                "workflow_binding_target",
                "binding target IDs must be unique",
            )
        if self.document_path is not None and ntpath.normcase(
            ntpath.normpath(self.document_path)
        ) != ntpath.normcase(ntpath.normpath(self.projection.document.full_name)):
            raise PydanticCustomError(
                "workflow_document_path",
                "document_path must match the projected document full_name",
            )
        return self


@dataclass(frozen=True, slots=True)
class QueryResult:
    workbook_alias: str
    workbook_path: str
    sheet: str
    headers: tuple[str, ...]
    row: int
    values: tuple[tuple[str, str], ...]
    # ``headers`` stays exactly what the sheet writes, blanks included. A blank
    # that a <mergeCell> explains is reported here as (column, label, ref) so
    # the filled label always travels with the merge it came from.
    merged_headers: tuple[tuple[int, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowQueryError(Exception):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from hwp_live_structure_contract import StructurePosition
from hwp_live_values import ContractModel


class PatchRangeTarget(ContractModel):
    kind: Literal["range"] = "range"
    start: StructurePosition
    end: StructurePosition


class PatchCellTarget(ContractModel):
    kind: Literal["table_cell"] = "table_cell"
    table_instance_id: str = Field(min_length=1, max_length=100)
    cell: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")


class ParagraphPatchFact(ContractModel):
    kind: Literal["paragraph"] = "paragraph"
    id: str = Field(min_length=1, max_length=200)
    page: int = Field(ge=1)
    text: str = Field(max_length=200_000)
    target: PatchRangeTarget
    heading_type: int | None = Field(default=None, ge=0, le=3)
    heading_level: int | None = Field(default=None, ge=0, le=6)


class CellPatchFact(ContractModel):
    kind: Literal["table_cell"] = "table_cell"
    id: str = Field(min_length=1, max_length=300)
    page: int = Field(ge=1)
    text: str = Field(max_length=200_000)
    target: PatchCellTarget


type PatchFact = Annotated[
    ParagraphPatchFact | CellPatchFact, Field(discriminator="kind")
]


class PatchPlanDocument(ContractModel):
    document_id: int
    full_name: str = Field(max_length=1_000)
    content_revision: str = Field(min_length=1, max_length=500)
    page_count: int = Field(ge=1)
    state_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_state_tokens: tuple[tuple[int, str], ...]
    #: (page, error) for every page whose table scan failed on *some* table.
    #: The failed tables are absent from ``items`` -- the detailed structure
    #: reader already drops them and keeps the healthy ones -- so this is what
    #: tells the caller that the plan is a partial view of that page's tables
    #: rather than a complete one. A page with no healthy table at all never
    #: reaches here: the reader raises on it.
    table_scan_errors: tuple[tuple[int, str], ...] = ()


class PatchPlanComplete(ContractModel):
    status: Literal["complete"] = "complete"
    document: PatchPlanDocument
    items: tuple[PatchFact, ...]
    item_count: int = Field(ge=0)
    utf8_bytes: int = Field(ge=0)


class PatchPlanOverflow(ContractModel):
    status: Literal["overflow"] = "overflow"
    document: PatchPlanDocument
    items: tuple[()] = ()
    required_items: int = Field(ge=0)
    required_utf8_bytes: int = Field(ge=0)
    max_items: int = Field(ge=1)
    max_utf8_bytes: int = Field(ge=1)
    page_partitions: tuple[tuple[int, ...], ...]
    unpartitionable_pages: tuple[int, ...] = ()


type PatchPlanResult = PatchPlanComplete | PatchPlanOverflow

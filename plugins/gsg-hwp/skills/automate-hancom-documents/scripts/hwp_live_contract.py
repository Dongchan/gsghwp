from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from hwp_live_layout_contract import (
    ImageBlock as ImageBlock,
    LayoutBlock as LayoutBlock,
    LayoutPlan as LayoutPlan,
    PageBreakBlock as PageBreakBlock,
    ParagraphBlock as ParagraphBlock,
)
from hwp_live_table_contract import (
    TableBlock as TableBlock,
    TableCell as TableCell,
)
from hwp_live_structure_contract import CreatedControl as CreatedControl
from hwp_live_values import Alignment as Alignment
from hwp_live_values import ContractModel as ContractModel
from hwp_live_values import Rgb as Rgb
from hwp_reference_layout_patch import (
    ReferenceLayoutPatchBlock as ReferenceLayoutPatchBlock,
)


class OpenDocument(ContractModel):
    selector: str
    title: str
    full_name: str
    document_id: int
    format: str
    edit_mode: int
    modified: bool
    page_count: int
    active: bool
    window_handle: int = Field(ge=1)


class OpenDocumentList(ContractModel):
    documents: tuple[OpenDocument, ...]


class ConnectedDocument(ContractModel):
    session_id: str
    document: OpenDocument


class CursorPosition(ContractModel):
    list_id: int
    paragraph: int
    character: int


class SelectionPosition(ContractModel):
    selected: bool
    start_list: int
    start_paragraph: int
    start_character: int
    end_list: int
    end_paragraph: int
    end_character: int


type ActiveTargetKind = Literal[
    "caret",
    "table_cell",
    "selected_text",
    "column_selection",
    "selected_cells",
    "selected_table",
    "selected_control",
    "unknown",
]
type SelectionModeName = Literal[
    "none",
    "text",
    "column",
    "cells",
    "control",
    "unknown",
]


class ActiveHwpTarget(ContractModel):
    basis: Literal["current_or_last_hwp_position"] = "current_or_last_hwp_position"
    kind: ActiveTargetKind
    selection_mode_raw: int = Field(ge=0)
    selection_mode: SelectionModeName
    strict_selection: bool
    multiple_cells: bool
    control_type: str | None = None
    control_instance_id: str | None = None
    cell_address: str | None = Field(default=None, pattern=r"^[A-Z]+[1-9][0-9]*$")
    cell_addresses: tuple[str, ...] = ()
    cell_address_error: str | None = None


class CharacterStyle(ContractModel):
    face_name: str
    height_hwpunit: int
    bold: bool
    text_color: int


class ParagraphStyle(ContractModel):
    align_type: int
    line_spacing: int
    left_margin_hwpunit: int
    right_margin_hwpunit: int
    indentation_hwpunit: int
    previous_spacing_hwpunit: int
    next_spacing_hwpunit: int


class PageSetup(ContractModel):
    paper_width_mm: float
    paper_height_mm: float
    landscape: int
    top_margin_mm: float
    bottom_margin_mm: float
    left_margin_mm: float
    right_margin_mm: float
    header_mm: float = 0
    footer_mm: float = 0
    gutter_mm: float = 0
    gutter_type: int = Field(default=0, ge=0, le=2)


class LiveContext(ContractModel):
    document: OpenDocument
    current_page: int
    cursor: CursorPosition
    selection: SelectionPosition
    active_target: ActiveHwpTarget
    selected_text: str
    page_text: str
    character_style: CharacterStyle
    paragraph_style: ParagraphStyle
    page_setup: PageSetup


class DocumentStyle(ContractModel):
    style_id: int = Field(ge=0, le=4095)
    name: str = Field(min_length=1, max_length=100)
    english_name: str | None = Field(default=None, max_length=100)


class DocumentStyleList(ContractModel):
    styles: tuple[DocumentStyle, ...]


class MutationResult(ContractModel):
    action: str
    current_page: int
    modified: bool


class PreviewResult(ContractModel):
    page: int
    path: Path


class LayoutResult(ContractModel):
    blocks_applied: int
    current_page: int
    modified: bool
    created_controls: tuple[CreatedControl, ...] = ()
    created_control_ids: tuple[str, ...] = ()
    execution_mode: Literal["native_in_process"] = "native_in_process"
    native_elapsed_microseconds: int = Field(ge=0)

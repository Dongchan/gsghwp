from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal
from unicodedata import normalize

from pydantic import Field, model_validator

from hwp_image_fit import fit_image_in_box
from hwp_live_table_contract import (
    CellBorder,
    CellBorders,
    CellPadding,
    TableBlock as TableBlock,
    TableCell as TableCell,
)
from hwp_live_structure_contract import CreatedControl as CreatedControl
from hwp_live_values import Alignment as Alignment
from hwp_live_values import ContractModel as ContractModel
from hwp_live_values import Rgb as Rgb


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


class ParagraphBlock(ContractModel):
    kind: Literal["paragraph"]
    text: str = Field(min_length=1, max_length=50_000)
    style_id: int | None = Field(default=None, ge=0, le=4095)
    style_name: str | None = Field(default=None, min_length=1, max_length=100)
    style_role: Literal[
        "auto",
        "body",
        "heading",
        "table_title",
        "figure_title",
    ] = "auto"
    bold: bool | None = None
    font_name: str | None = Field(default=None, max_length=100)
    font_size_pt: float | None = Field(default=None, ge=1, le=96)
    text_color: Rgb | None = None
    alignment: Alignment = "inherit"
    line_spacing_percent: int | None = Field(default=None, ge=50, le=500)
    space_before_mm: float | None = Field(default=None, ge=0, le=100)
    space_after_mm: float | None = Field(default=None, ge=0, le=100)
    left_margin_mm: float | None = Field(default=None, ge=0, le=100)
    right_margin_mm: float | None = Field(default=None, ge=0, le=100)
    indentation_mm: float | None = Field(default=None, ge=-100, le=100)

    @model_validator(mode="after")
    def validate_style(self) -> ParagraphBlock:
        if self.style_id is not None and self.style_name is not None:
            raise ValueError("paragraph style name and id are mutually exclusive")
        return self


class ImageBlock(ContractModel):
    kind: Literal["image"]
    path: Path
    width_mm: float = Field(ge=1, le=250)
    height_mm: float = Field(ge=1, le=350)
    alignment: Alignment = "center"
    container: Literal["paragraph", "table_cell"] = "paragraph"
    caption: str | None = Field(default=None, max_length=2_000)
    caption_style_id: int | None = Field(default=None, ge=0, le=4095)
    caption_style_name: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_caption_style(self) -> ImageBlock:
        if self.caption is None and (
            self.caption_style_id is not None or self.caption_style_name is not None
        ):
            raise ValueError("caption style requires caption")
        if self.caption_style_id is not None and self.caption_style_name is not None:
            raise ValueError("caption style name and id are mutually exclusive")
        if self.caption is not None and re.match(
            r"^\s*[\(\[]?\s*그림\s*(?:제\s*)?\d",
            normalize("NFKC", self.caption),
        ):
            raise ValueError("caption must omit automatic figure numbering")
        return self


class PageBreakBlock(ContractModel):
    kind: Literal["page_break"]


LayoutBlock = Annotated[
    ParagraphBlock | TableBlock | ImageBlock | PageBreakBlock,
    Field(discriminator="kind"),
]


class LayoutPlan(ContractModel):
    target: Literal["current", "document_end", "after_page"] = "current"
    page: int | None = Field(default=None, ge=1)
    replace_selection: bool = False
    blocks: tuple[LayoutBlock, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_size(self) -> LayoutPlan:
        if self.target == "after_page" and self.page is None:
            raise ValueError("after_page target requires page")
        if self.target != "after_page" and self.page is not None:
            raise ValueError("page is only valid with after_page target")
        if self.target != "current" and self.replace_selection:
            raise ValueError(f"{self.target} target cannot replace a selection")
        cells = sum(
            len(row)
            for block in self.blocks
            if isinstance(block, TableBlock)
            for row in block.rows
        )
        if cells > 1_000:
            raise ValueError("layout contains more than 1000 table cells")
        return self

    def expand_image_frames(
        self,
        content_width_mm: float | None = None,
    ) -> LayoutPlan:
        border = CellBorder(style="solid", width="0.12mm", color=(0, 0, 0))
        borders = CellBorders(left=border, right=border, top=border, bottom=border)
        padding = CellPadding(left_mm=1, right_mm=1, top_mm=1, bottom_mm=1)
        expanded: list[LayoutBlock] = []
        for block in self.blocks:
            match block:
                case ImageBlock():
                    if block.container == "paragraph":
                        expanded.append(block)
                        continue
                    width_limit = (
                        block.width_mm
                        if content_width_mm is None
                        else min(block.width_mm, content_width_mm - 2)
                    )
                    scale = min(
                        1.0,
                        width_limit / block.width_mm,
                        248 / block.height_mm,
                    )
                    box_width = block.width_mm * scale
                    box_height = block.height_mm * scale
                    fitted_width, fitted_height = fit_image_in_box(
                        block.path,
                        width_mm=box_width,
                        height_mm=box_height,
                    )
                    width = round(fitted_width, 4)
                    height = round(fitted_height, 4)
                    expanded.append(
                        TableBlock(
                            kind="table",
                            rows=(
                                (
                                    TableCell(
                                        image_path=block.path,
                                        image_width_mm=width,
                                        image_height_mm=height,
                                        alignment=block.alignment,
                                        vertical_alignment="center",
                                        padding=padding,
                                        borders=borders,
                                    ),
                                ),
                            ),
                            column_width_weights=(1.0,),
                            row_heights_mm=(height + 2,),
                        )
                    )
                    if block.caption is not None:
                        expanded.append(
                            ParagraphBlock(
                                kind="paragraph",
                                text=block.caption,
                                style_id=block.caption_style_id,
                                style_name=block.caption_style_name,
                                style_role="figure_title",
                                alignment="center",
                            )
                        )
                case ParagraphBlock() | TableBlock() | PageBreakBlock():
                    expanded.append(block)
        return LayoutPlan(
            target=self.target,
            page=self.page,
            replace_selection=self.replace_selection,
            blocks=tuple(expanded),
        )

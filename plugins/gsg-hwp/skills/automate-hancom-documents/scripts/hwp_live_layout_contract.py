from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal
from unicodedata import normalize

from pydantic import Field, model_validator

from hwp_color_normalization import ColorInput
from hwp_image_fit import fit_image_in_box
from hwp_live_table_contract import (
    CellBorder,
    CellBorders,
    CellPadding,
    TableBlock,
    TableCell,
)
from hwp_live_values import Alignment, ContractModel
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_patch import ReferenceLayoutPatchBlock


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
    text_color: ColorInput | None = None
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
    ParagraphBlock
    | TableBlock
    | ImageBlock
    | PageBreakBlock
    | ReferenceLayoutBlock
    | ReferenceLayoutPatchBlock,
    Field(discriminator="kind"),
]


class LayoutPlan(ContractModel):
    target: Literal["current", "document_end", "after_page"] = Field(
        default="current",
        description=(
            "current는 현재 커서, document_end는 문서 끝, after_page는 page로 "
            "지정한 쪽 다음의 격리된 새 쪽에 삽입합니다"
        ),
    )
    page: int | None = Field(
        default=None,
        ge=1,
        description="target=after_page일 때만 사용하는 1부터 시작하는 기준 쪽 번호",
    )
    replace_selection: bool = Field(
        default=False,
        description="target=current에서 현재 선택 영역을 레이아웃으로 교체할지 여부",
    )
    blocks: tuple[LayoutBlock, ...] = Field(
        min_length=1,
        max_length=100,
        description=(
            "kind로 구분되는 paragraph, table, image, reference_layout, "
            "reference_layout_patch, page_break 블록의 실행 순서"
        ),
    )

    @model_validator(mode="after")
    def validate_size(self) -> LayoutPlan:
        if self.target == "after_page" and self.page is None:
            raise ValueError("after_page target requires page")
        if self.target != "after_page" and self.page is not None:
            raise ValueError("page is only valid with after_page target")
        if self.target != "current" and self.replace_selection:
            raise ValueError(f"{self.target} target cannot replace a selection")
        if self.target != "current" and any(
            isinstance(block, ReferenceLayoutPatchBlock) for block in self.blocks
        ):
            raise ValueError("reference layout patch requires target=current")
        cells = sum(
            len(row)
            for block in self.blocks
            if isinstance(block, TableBlock)
            for row in block.rows
        )
        if cells > 1_000:
            raise ValueError("layout contains more than 1000 expanded table cells")
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
                case (
                    ParagraphBlock()
                    | TableBlock()
                    | PageBreakBlock()
                    | ReferenceLayoutBlock()
                    | ReferenceLayoutPatchBlock()
                ):
                    expanded.append(block)
        return LayoutPlan(
            target=self.target,
            page=self.page,
            replace_selection=self.replace_selection,
            blocks=tuple(expanded),
        )

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

from pydantic import Field, model_validator

from hwp_live_contract import (
    ImageBlock,
    LayoutBlock,
    LayoutPlan,
    PageBreakBlock,
    ParagraphBlock,
)
from hwp_live_layout_contract import ImageFrame
from hwp_live_table_contract import (
    TableBlock,
    TableCell,
    TableCellStyle,
    apply_cell_style,
)
from hwp_live_values import Alignment, ContractModel


class ReportTable(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=2_000)
    headers: tuple[str, ...] = Field(min_length=1, max_length=20)
    rows: tuple[tuple[str, ...], ...] = Field(default=(), max_length=49)
    column_weights: tuple[float, ...] | None = None
    column_widths_mm: tuple[float, ...] | None = None
    target_width_mm: float | None = Field(
        default=None,
        ge=1,
        le=250,
        description=(
            "모델이 공개 구조 조회에서 선택한 표 전체 폭. 열 비율과 함께 "
            "제공하며, 관측하지 못했으면 생략한다."
        ),
    )
    row_heights_mm: tuple[float, ...] | None = Field(
        default=None,
        description=(
            "고정 행높이 관례가 확인된 경우의 머리글 포함 각 행 높이. 실제 "
            "높이만 보이고 고정/자동 정책이 안 보이면 생략한다."
        ),
    )
    header_style: TableCellStyle | None = Field(
        default=None,
        description="모델이 머리글 셀이라고 선택한 공개 CELLFMT 표본.",
    )
    body_style: TableCellStyle | None = Field(
        default=None,
        description="모델이 본문 셀이라고 선택한 공개 CELLFMT 표본.",
    )
    alignment: Alignment = Field(
        default="inherit",
        description=(
            "모델이 원문 표의 페이지 좌표와 본문 폭에서 선택한 표 앵커 정렬. "
            "관측하지 못했으면 생략한다."
        ),
    )
    left_margin_mm: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="모델이 표 앞 문단에서 선택한 왼쪽 여백.",
    )
    right_margin_mm: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="모델이 표 앞 문단에서 선택한 오른쪽 여백.",
    )
    indentation_mm: float | None = Field(
        default=None,
        ge=-100,
        le=100,
        description="모델이 표 앞 문단에서 선택한 들여쓰기.",
    )
    repeat_key_columns: int = Field(default=0, ge=0, le=5)

    @model_validator(mode="after")
    def validate_shape(self) -> ReportTable:
        columns = len(self.headers)
        if any(len(row) != columns for row in self.rows):
            raise ValueError("report table rows must match the header count")
        if self.column_weights is not None:
            if len(self.column_weights) != columns:
                raise ValueError("column weights must match the header count")
            if any(weight <= 0 for weight in self.column_weights):
                raise ValueError("column weights must be positive")
        if self.column_widths_mm is not None:
            if len(self.column_widths_mm) != columns:
                raise ValueError("column widths must match the header count")
            if any(width < 1 or width > 250 for width in self.column_widths_mm):
                raise ValueError("column width is outside the supported range")
        if self.column_weights is not None and self.column_widths_mm is not None:
            raise ValueError("column widths and weights are mutually exclusive")
        if self.target_width_mm is not None:
            if self.column_widths_mm is not None:
                raise ValueError(
                    "target table width and explicit column widths are mutually exclusive"
                )
            if self.column_weights is None:
                raise ValueError("target table width requires column weights")
        if self.row_heights_mm is not None:
            if len(self.row_heights_mm) != len(self.rows) + 1:
                raise ValueError("row heights must include the header and every row")
            if any(height < 1 or height > 250 for height in self.row_heights_mm):
                raise ValueError("row height is outside the supported range")
        if self.repeat_key_columns >= columns and self.repeat_key_columns != 0:
            raise ValueError("repeated key columns must leave at least one data column")
        return self


class ReportFigure(ContractModel):
    path: Path
    width_mm: float = Field(ge=1, le=250)
    height_mm: float = Field(ge=1, le=350)
    caption: str | None = Field(default=None, max_length=2_000)
    caption_style_id: int | None = Field(default=None, ge=0, le=4095)
    container: Literal["paragraph", "table_cell"] = "paragraph"
    frame: ImageFrame | None = Field(
        default=None,
        description=(
            "모델이 공개 구조 조회에서 선택한 표 셀 그림 틀. 관례가 관측되지 "
            "않았으면 생략하여 맨 그림을 넣는다."
        ),
    )

    @model_validator(mode="after")
    def use_selected_frame_container(self) -> ReportFigure:
        if self.frame is None:
            return self
        if "container" in self.model_fields_set and self.container != "table_cell":
            raise ValueError("a selected figure frame requires container=table_cell")
        object.__setattr__(self, "container", "table_cell")
        return self


class ReportSection(ContractModel):
    title: str = Field(min_length=1, max_length=2_000)
    title_style_id: int | None = Field(default=None, ge=0, le=4095)
    paragraphs: tuple[str, ...] = Field(default=(), max_length=30)
    bullets: tuple[str, ...] = Field(default=(), max_length=50)
    tables: tuple[ReportTable, ...] = Field(default=(), max_length=10)
    figures: tuple[ReportFigure, ...] = Field(default=(), max_length=20)
    page_break_before: bool = False


class ReportPlan(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=2_000)
    title_style_id: int | None = Field(default=None, ge=0, le=4095)
    introduction: tuple[str, ...] = Field(default=(), max_length=30)
    sections: tuple[ReportSection, ...] = Field(default=(), max_length=30)
    start_on_new_page: bool = False

    @model_validator(mode="after")
    def validate_content(self) -> ReportPlan:
        if self.title is None and not self.introduction and not self.sections:
            raise ValueError("report must contain a title, introduction, or section")
        return self


class ReportListItemBlock(ParagraphBlock):
    """A report list item whose marker must come from the open document.

    The public report contract already says which strings are list items through
    ``ReportSection.bullets``. Keeping that fact as the block's Python type lets
    the document-style resolver choose an observed convention later, without
    planting a glyph in the text or changing the public layout schema.
    """


def _header(value: str, style: TableCellStyle | None) -> TableCell:
    return apply_cell_style(TableCell(text=value), style)


def _body(value: str, style: TableCellStyle | None) -> TableCell:
    return apply_cell_style(TableCell(text=value), style)


def _paragraph(
    text: str,
    *,
    style_role: Literal["body", "heading"],
    style_id: int | None = None,
) -> ParagraphBlock:
    """One report paragraph, described only by the role it plays.

    This used to ship 맑은 고딕 10pt, black, left, 160% line spacing and a
    left margin, indentation and hanging indent all pinned to 0 -- our own
    house style, stamped over whatever the open document does. That is what a
    reader sees as a generated document that does not belong in the file: the
    heading sizes were ours, and the pinned zeros flattened exactly the hanging
    indent the document's list styles depend on.

    Leaving every appearance field unset lets the resolver fill them from the
    document's own paragraphs of the style this role resolves to
    (``hwp_document_style_profile._replicated``); when nothing was observed the
    paragraph simply inherits the style, which is still the document's answer
    rather than ours.

    A title can carry an explicit style id selected from ``hwp_list_styles``.
    When the document exposes no heading style, the builder leaves the title
    inherited instead of inventing a bold flag, point size, or spacing scale.
    """
    return ParagraphBlock(
        kind="paragraph",
        text=text,
        style_role=style_role,
        style_id=style_id,
    )


def _list_item(text: str) -> ReportListItemBlock:
    """Keep list semantics without inventing a visible marker."""
    return ReportListItemBlock(kind="paragraph", text=text, style_role="body")


class _AnchorFormat(TypedDict, total=False):
    """The table-anchor paragraph fields, each present only when observed."""

    alignment: Alignment
    left_margin_mm: float
    right_margin_mm: float
    indentation_mm: float


def _table(table: ReportTable) -> TableBlock:
    rows = (
        tuple(_header(value, table.header_style) for value in table.headers),
        *(tuple(_body(value, table.body_style) for value in row) for row in table.rows),
    )
    # A TypedDict rather than a plain mapping so the ``**`` below still tells a
    # type checker which key feeds which parameter. Keys stay absent when the
    # caller said nothing, which is what leaves the block on the document's own
    # value instead of a value we chose.
    anchor_format: _AnchorFormat = {
        "alignment": (
            table.alignment if "alignment" in table.model_fields_set else "inherit"
        )
    }
    if table.left_margin_mm is not None:
        anchor_format["left_margin_mm"] = table.left_margin_mm
    if table.right_margin_mm is not None:
        anchor_format["right_margin_mm"] = table.right_margin_mm
    if table.indentation_mm is not None:
        anchor_format["indentation_mm"] = table.indentation_mm
    return TableBlock(
        kind="table",
        caption=table.title,
        rows=rows,
        column_widths_mm=table.column_widths_mm,
        column_width_weights=table.column_weights,
        target_width_mm=table.target_width_mm,
        row_heights_mm=table.row_heights_mm,
        split_wide_table=table.repeat_key_columns > 0,
        repeat_key_columns=table.repeat_key_columns,
        **anchor_format,
    )


def report_layout_plan(report: ReportPlan) -> LayoutPlan:
    blocks: list[LayoutBlock] = []
    if report.start_on_new_page:
        blocks.append(PageBreakBlock(kind="page_break"))
    if report.title is not None:
        blocks.append(
            _paragraph(
                report.title,
                style_role="heading",
                style_id=report.title_style_id,
            )
        )
    blocks.extend(_paragraph(text, style_role="body") for text in report.introduction)
    for section in report.sections:
        if section.page_break_before and blocks:
            blocks.append(PageBreakBlock(kind="page_break"))
        blocks.append(
            _paragraph(
                section.title,
                style_role="heading",
                style_id=section.title_style_id,
            )
        )
        blocks.extend(
            _paragraph(text, style_role="body") for text in section.paragraphs
        )
        blocks.extend(_list_item(text) for text in section.bullets)
        blocks.extend(_table(table) for table in section.tables)
        blocks.extend(
            ImageBlock(
                kind="image",
                path=figure.path,
                width_mm=figure.width_mm,
                height_mm=figure.height_mm,
                caption=figure.caption,
                caption_style_id=figure.caption_style_id,
                container=figure.container,
                frame=figure.frame,
            )
            for figure in section.figures
        )
    return LayoutPlan(target="document_end", blocks=tuple(blocks))

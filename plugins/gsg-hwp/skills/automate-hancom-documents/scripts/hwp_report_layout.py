from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from hwp_live_contract import (
    ImageBlock,
    LayoutBlock,
    LayoutPlan,
    PageBreakBlock,
    ParagraphBlock,
)
from hwp_live_table_contract import (
    CellBorder,
    CellBorders,
    CellPadding,
    TableBlock,
    TableCell,
)
from hwp_live_values import ContractModel
from hwp_table_readability import (
    display_width,
    recommended_column_minimums,
    recommended_row_heights,
)


class ReportTable(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=2_000)
    headers: tuple[str, ...] = Field(min_length=1, max_length=20)
    rows: tuple[tuple[str, ...], ...] = Field(default=(), max_length=49)
    column_weights: tuple[float, ...] | None = None
    repeat_key_columns: int = Field(default=1, ge=0, le=5)

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
        if self.repeat_key_columns >= columns and self.repeat_key_columns != 0:
            raise ValueError("repeated key columns must leave at least one data column")
        return self


class ReportFigure(ContractModel):
    path: Path
    width_mm: float = Field(ge=1, le=250)
    height_mm: float = Field(ge=1, le=350)
    caption: str | None = Field(default=None, max_length=2_000)
    container: Literal["paragraph", "table_cell"] = "table_cell"


class ReportSection(ContractModel):
    title: str = Field(min_length=1, max_length=2_000)
    paragraphs: tuple[str, ...] = Field(default=(), max_length=30)
    bullets: tuple[str, ...] = Field(default=(), max_length=50)
    tables: tuple[ReportTable, ...] = Field(default=(), max_length=10)
    figures: tuple[ReportFigure, ...] = Field(default=(), max_length=20)
    page_break_before: bool = False


class ReportPlan(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=2_000)
    introduction: tuple[str, ...] = Field(default=(), max_length=30)
    sections: tuple[ReportSection, ...] = Field(default=(), max_length=30)
    start_on_new_page: bool = False

    @model_validator(mode="after")
    def validate_content(self) -> ReportPlan:
        if self.title is None and not self.introduction and not self.sections:
            raise ValueError("report must contain a title, introduction, or section")
        return self


_NUMBER = re.compile(
    r"^\s*[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|원|억원|㎡|m²|m|km)?\s*$",
    re.IGNORECASE,
)
_BORDER = CellBorder(style="solid", width="0.12mm", color=(150, 160, 156))
_BORDERS = CellBorders(left=_BORDER, right=_BORDER, top=_BORDER, bottom=_BORDER)
_PADDING = CellPadding(left_mm=1.2, right_mm=1.2, top_mm=0.4, bottom_mm=0.4)


def _display_width(value: str) -> int:
    return display_width(value)


def _column_weights(table: ReportTable) -> tuple[float, ...]:
    if table.column_weights is not None:
        return table.column_weights
    values = (table.headers, *table.rows)
    return tuple(
        float(max(4, min(40, max(_display_width(row[column]) for row in values) + 2)))
        for column in range(len(table.headers))
    )


def _header(value: str) -> TableCell:
    return TableCell(
        text=value,
        bold=True,
        alignment="center",
        vertical_alignment="center",
        fill_color=(231, 239, 236),
        padding=_PADDING,
        borders=_BORDERS,
    )


def _body(value: str, row: int) -> TableCell:
    return TableCell(
        text=value,
        alignment="right" if _NUMBER.fullmatch(value) else "left",
        vertical_alignment="center",
        fill_color=(247, 249, 248) if row % 2 else None,
        padding=_PADDING,
        borders=_BORDERS,
    )


def _table(table: ReportTable) -> TableBlock:
    text_rows = (table.headers, *table.rows)
    minimums = recommended_column_minimums(table.headers, table.rows)
    rows = (
        tuple(_header(value) for value in table.headers),
        *(
            tuple(_body(value, row_index) for value in row)
            for row_index, row in enumerate(table.rows, start=1)
        ),
    )
    return TableBlock(
        kind="table",
        caption=table.title,
        rows=rows,
        column_width_weights=_column_weights(table),
        minimum_column_widths_mm=minimums,
        row_heights_mm=recommended_row_heights(text_rows, minimums),
        auto_fit_row_heights=True,
        repeat_header=True,
        split_wide_table=len(table.headers) > 1,
        repeat_key_columns=(
            table.repeat_key_columns if len(table.headers) > 1 else 0
        ),
    )


def report_layout_plan(report: ReportPlan) -> LayoutPlan:
    blocks: list[LayoutBlock] = []
    if report.start_on_new_page:
        blocks.append(PageBreakBlock(kind="page_break"))
    if report.title is not None:
        blocks.append(
            ParagraphBlock(
                kind="paragraph",
                text=report.title,
                style_role="heading",
                bold=True,
            )
        )
    blocks.extend(
        ParagraphBlock(kind="paragraph", text=text, style_role="body")
        for text in report.introduction
    )
    for section in report.sections:
        if section.page_break_before and blocks:
            blocks.append(PageBreakBlock(kind="page_break"))
        blocks.append(
            ParagraphBlock(
                kind="paragraph",
                text=section.title,
                style_role="heading",
                bold=True,
            )
        )
        blocks.extend(
            ParagraphBlock(kind="paragraph", text=text, style_role="body")
            for text in section.paragraphs
        )
        blocks.extend(
            ParagraphBlock(kind="paragraph", text=f"○ {text}", style_role="body")
            for text in section.bullets
        )
        blocks.extend(_table(table) for table in section.tables)
        blocks.extend(
            ImageBlock(
                kind="image",
                path=figure.path,
                width_mm=figure.width_mm,
                height_mm=figure.height_mm,
                caption=figure.caption,
                container=figure.container,
            )
            for figure in section.figures
        )
    return LayoutPlan(target="document_end", blocks=tuple(blocks))

from __future__ import annotations

from functools import partial
from pathlib import Path
import tempfile
from typing import Literal, Protocol, cast, final

from anyio import to_thread

import hwp_public_action_metadata as metadata
from hwp_current_format_insert_contract import (
    CurrentFormatEditorialObservation,
    CurrentFormatExcelSource,
    CurrentFormatFitObservation,
    CurrentFormatImageSource,
    CurrentFormatInsertEvidence,
    CurrentFormatNumberingObservation,
    CurrentFormatPptxSemanticObservation,
    CurrentFormatPptxSlideSource,
    CurrentFormatPptxSource,
    CurrentFormatSource,
    CurrentFormatStyleObservation,
    CurrentFormatTextSource,
)
from hwp_errors import HwpLiveError
from hwp_image_fit import fit_image_in_box
from hwp_layout_preflight import LayoutPreflightResult
from hwp_live_contract import (
    CharacterStyle,
    LayoutPlan,
    LiveContext,
    ParagraphStyle,
)
from hwp_live_layout_contract import (
    ImageBlock,
    ImageFrame,
    LayoutBlock,
    PageBreakBlock,
    ParagraphBlock,
    ParagraphRun,
)
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    OperationResult,
)
from hwp_operation_registry import operation_registry
from hwp_public_action_contract import (
    DOCUMENT_INPUT_ALIASES,
    PublicActionExecutor,
    PublicOperationId,
)
from hwp_public_contract import PublicActionResult, to_public_action_result
from hwp_office_excel_layout import table_block_from_excel, table_blocks_from_excel
from hwp_office_pptx import (
    PptxBorderData,
    PptxImageShapeData,
    PptxSlideContent,
    PptxTableShapeData,
    PptxTextParagraphData,
    PptxTextShapeData,
    read_pptx,
    read_pptx_slide,
)
from hwp_report_layout import ReportPlan, report_layout_plan
from hwp_reference_layout_geometry import HWPUNITS_PER_INCH, MILLIMETERS_PER_INCH
from hwp_live_table_contract import (
    CellBorder,
    CellBorders,
    CellPadding,
    TableBlock,
    TableCell,
    TableCellStyle,
    TableMerge,
)
from hwp_table_readability import recommended_row_heights
from hwp_live_values import Alignment


class PublicDocumentExecutor(PublicActionExecutor, Protocol):
    async def inspect_context(self, document_selector: str | None) -> LiveContext: ...

    async def preflight_layout(
        self,
        document_selector: str | None,
        plan: LayoutPlan,
    ) -> LayoutPreflightResult: ...


def _hwpunit_to_mm(value: int) -> float:
    return value * MILLIMETERS_PER_INCH / HWPUNITS_PER_INCH / 2


def _color_tuple(value: int) -> tuple[int, int, int]:
    return (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)


def _content_box(context: LiveContext) -> tuple[float, float]:
    setup = context.page_setup
    width = max(
        1.0,
        setup.paper_width_mm
        - setup.left_margin_mm
        - setup.right_margin_mm
        - setup.gutter_mm,
    )
    height = max(
        1.0,
        setup.paper_height_mm
        - setup.top_margin_mm
        - setup.bottom_margin_mm
        - setup.header_mm
        - setup.footer_mm,
    )
    return width, height


def _numbering(context: LiveContext) -> CurrentFormatNumberingObservation:
    paragraph = context.paragraph_style
    if paragraph.marker_is_automatic is True:
        return CurrentFormatNumberingObservation(
            heading_type=paragraph.heading_type,
            heading_level=paragraph.heading_level,
            marker_is_automatic=True,
            numeric_marker_value="unavailable_for_automatic_numbering",
            comparison="automatic_heading_level_and_render_only",
        )
    if paragraph.marker_is_automatic is False and paragraph.manual_marker_value:
        return CurrentFormatNumberingObservation(
            heading_type=paragraph.heading_type,
            heading_level=paragraph.heading_level,
            marker_is_automatic=False,
            manual_marker_before=paragraph.manual_marker_value,
            comparison="manual_marker_value_compared",
        )
    if paragraph.marker_is_automatic is False:
        return CurrentFormatNumberingObservation(
            heading_type=paragraph.heading_type,
            heading_level=paragraph.heading_level,
            marker_is_automatic=False,
            comparison="unobserved",
        )
    return CurrentFormatNumberingObservation(comparison="no_numbering_observed")


def _style_evidence(
    before: LiveContext,
    after: LiveContext | None,
) -> CurrentFormatStyleObservation:
    return CurrentFormatStyleObservation(
        character_style_before=before.character_style,
        paragraph_style_before=before.paragraph_style,
        page_setup_before=before.page_setup,
        character_style_after=None if after is None else after.character_style,
        paragraph_style_after=None if after is None else after.paragraph_style,
        page_setup_after=None if after is None else after.page_setup,
        inserted_character_style=before.character_style,
        inserted_paragraph_style=before.paragraph_style,
        page_setup_unchanged=None
        if after is None
        else before.page_setup == after.page_setup,
    )


def _pptx_paragraph_block(
    shape: PptxTextShapeData,
    paragraph: PptxTextParagraphData,
    *,
    paragraph_index: int,
    character_style: CharacterStyle,
    paragraph_style: ParagraphStyle,
    font_size_pt: float,
    text_color: tuple[int, int, int],
    left_margin_mm: float,
    right_margin_mm: float,
    indentation_mm: float,
    space_before_mm: float,
    space_after_mm: float,
    style_role: Literal[
        "auto", "body", "heading", "table_title", "figure_title"
    ] = "body",
) -> ParagraphBlock | None:
    _ = (
        character_style,
        font_size_pt,
        text_color,
        left_margin_mm,
        right_margin_mm,
        indentation_mm,
        space_before_mm,
        space_after_mm,
    )
    if not paragraph.text:
        return None
    runs = tuple(
        ParagraphRun(
            text=run.text,
            # The destination role supplies the font family, size, color, and
            # paragraph shape. Preserve source run-level bold only; never copy
            # the current cursor's character style into the whole paragraph.
            bold=run.bold,
            font_name=None,
            font_size_pt=None,
            text_color=None,
        )
        for run in paragraph.runs
        if run.text
    )
    if not runs:
        return None
    is_heading = shape.is_heading and paragraph_index == 0
    heading_type = (
        paragraph_style.heading_type
        if is_heading and paragraph_style.heading_type not in (None, 0)
        else None
    )
    heading_level = (
        paragraph_style.heading_level
        if is_heading and paragraph_style.heading_level not in (None, 0)
        else None
    )
    return ParagraphBlock(
        kind="paragraph",
        text=paragraph.text,
        runs=runs,
        style_role="heading" if is_heading else style_role,
        preserve_source_text=True,
        bold=None,
        font_name=None,
        font_size_pt=None,
        text_color=None,
        align_type_raw=None,
        line_spacing_percent=None,
        left_margin_mm=None,
        right_margin_mm=None,
        indentation_mm=None,
        space_before_mm=None,
        space_after_mm=None,
        heading_type=heading_type,
        heading_level=heading_level,
    )


def _pptx_border(value: PptxBorderData | None) -> CellBorder | None:
    if value is None:
        return None
    widths = (
        "0.1mm",
        "0.12mm",
        "0.15mm",
        "0.2mm",
        "0.25mm",
        "0.3mm",
        "0.4mm",
        "0.5mm",
        "0.6mm",
        "0.7mm",
        "1.0mm",
        "1.5mm",
        "2.0mm",
        "3.0mm",
        "4.0mm",
        "5.0mm",
    )
    selected = min(
        widths,
        key=lambda item: abs(
            float(item.removesuffix("mm")) - value.width_pt * 0.352778
        ),
    )
    return CellBorder(
        style="solid",
        width=selected,
        color=value.color or (0, 0, 0),
    )


def _pptx_table_block(
    shape: PptxTableShapeData,
    content: PptxSlideContent,
    *,
    character_style: CharacterStyle,
    paragraph_style: ParagraphStyle,
    font_size_pt: float,
    text_color: tuple[int, int, int],
    content_width: float,
    content_height: float,
) -> TableBlock:
    source_width = sum(shape.column_widths_emu) or len(shape.rows[0])
    widths = tuple(
        max(1.0, content_width * width / source_width)
        for width in shape.column_widths_emu
    )
    row_heights = tuple(
        max(
            3.0,
            min(
                30.0,
                content_height * height / content.slide_height_emu,
            ),
        )
        for height in shape.row_heights_emu
    )
    if len(row_heights) != len(shape.rows):
        row_heights = tuple(
            max(3.0, min(30.0, content_height / len(shape.rows))) for _ in shape.rows
        )
    covered_positions = {
        (covered_row, covered_column)
        for merge in shape.merges
        for covered_row in range(merge[0], merge[0] + merge[2])
        for covered_column in range(merge[1], merge[1] + merge[3])
        if (covered_row, covered_column) != (merge[0], merge[1])
    }
    cells: list[tuple[TableCell, ...]] = []
    has_borders = False
    for row_index, row in enumerate(shape.rows):
        converted: list[TableCell] = []
        for column_index, source_cell in enumerate(row):
            if (row_index, column_index) in covered_positions:
                converted.append(TableCell())
                continue
            borders = tuple(_pptx_border(border) for border in source_cell.borders)
            cell_borders = None
            if any(border is not None for border in borders):
                has_borders = True
                cell_borders = CellBorders(
                    left=borders[0],
                    right=borders[1],
                    top=borders[2],
                    bottom=borders[3],
                )
            cell_paragraphs = tuple(
                paragraph_block
                for paragraph_index, paragraph in enumerate(source_cell.paragraphs)
                if (
                    paragraph_block := _pptx_paragraph_block(
                        PptxTextShapeData(
                            source_order=shape.source_order,
                            geometry=shape.geometry,
                            paragraphs=source_cell.paragraphs,
                        ),
                        paragraph,
                        paragraph_index=paragraph_index,
                        character_style=character_style,
                        paragraph_style=paragraph_style,
                        font_size_pt=font_size_pt,
                        text_color=text_color,
                        left_margin_mm=0,
                        right_margin_mm=0,
                        indentation_mm=0,
                        space_before_mm=0,
                        space_after_mm=0,
                        style_role="body",
                    )
                )
                is not None
            )
            converted.append(
                TableCell(
                    text=source_cell.text.replace("\r\n", "\n"),
                    paragraphs=cell_paragraphs,
                    bold=None,
                    font_name=None,
                    font_size_pt=None,
                    text_color=None,
                    alignment=(
                        cast(
                            Alignment,
                            source_cell.alignment
                            if source_cell.alignment
                            in {"left", "center", "right", "justify"}
                            else "inherit",
                        )
                    ),
                    line_spacing_percent=None,
                    fill_color=source_cell.fill_color,
                    borders=cell_borders,
                )
            )
        cells.append(tuple(converted))
    return TableBlock(
        kind="table",
        border_mode="explicit" if has_borders else "inherit",
        rows=tuple(cells),
        column_widths_mm=widths,
        row_heights_mm=row_heights,
        merges=tuple(
            TableMerge(
                row=row, column=column, row_span=row_span, column_span=column_span
            )
            for row, column, row_span, column_span in shape.merges
        ),
    )


def _editorial_paragraph(
    shape: PptxTextShapeData,
    paragraph: PptxTextParagraphData,
    *,
    paragraph_index: int,
    character_style: CharacterStyle,
    paragraph_style: ParagraphStyle,
    font_size_pt: float,
    text_color: tuple[int, int, int],
    style_role: Literal["body", "heading", "table_title", "figure_title"],
) -> ParagraphBlock | None:
    return _pptx_paragraph_block(
        shape,
        paragraph,
        paragraph_index=paragraph_index,
        character_style=character_style,
        paragraph_style=paragraph_style,
        font_size_pt=font_size_pt,
        text_color=text_color,
        left_margin_mm=0,
        right_margin_mm=0,
        indentation_mm=0,
        space_before_mm=0,
        space_after_mm=0,
        style_role=style_role,
    )


def _editorial_cell(
    paragraphs: tuple[ParagraphBlock, ...],
    *,
    header: bool = False,
    key_column: bool = False,
) -> TableCell:
    navy = (31, 78, 121)
    light_blue = (221, 235, 247)
    dark_body = (31, 31, 31)
    border = CellBorder(style="solid", width="0.12mm", color=navy)
    if header:
        paragraphs = tuple(
            paragraph.model_copy(update={"bold": True, "text_color": (255, 255, 255)})
            for paragraph in paragraphs
        )
    return TableCell(
        paragraphs=paragraphs,
        bold=True if header else None,
        text_color=(255, 255, 255) if header else dark_body,
        fill_color=navy if header else light_blue if key_column else (255, 255, 255),
        alignment="left",
        vertical_alignment="top",
        padding=CellPadding(left_mm=1.8, right_mm=1.8, top_mm=0.8, bottom_mm=0.8),
        borders=CellBorders(
            left=border,
            right=border,
            top=border,
            bottom=border,
        ),
    )


def _editorial_table(
    group_title: str,
    rows: tuple[tuple[TableCell, ...], ...],
    *,
    widths: tuple[float, float, float],
) -> TableBlock:
    _ = group_title
    row_text = tuple(tuple(cell.text for cell in row) for row in rows)
    return TableBlock(
        kind="table",
        border_mode="explicit",
        rows=rows,
        column_widths_mm=widths,
        row_heights_mm=recommended_row_heights(row_text, widths),
        base_style_name="표내용",
        alignment="left",
    )


def pptx_editorial_blocks(
    content: PptxSlideContent,
    *,
    source_order: int,
    character_style: CharacterStyle,
    paragraph_style: ParagraphStyle,
    font_size_pt: float,
    text_color: tuple[int, int, int],
    content_width: float,
    content_height: float,
) -> tuple[
    tuple[LayoutBlock, ...],
    CurrentFormatFitObservation,
    CurrentFormatPptxSemanticObservation,
    CurrentFormatEditorialObservation,
]:
    text_shapes = tuple(
        element
        for element in content.elements
        if isinstance(element, PptxTextShapeData)
    )
    images = tuple(
        element
        for element in content.elements
        if isinstance(element, PptxImageShapeData)
    )
    tables = tuple(
        element
        for element in content.elements
        if isinstance(element, PptxTableShapeData)
    )
    if len(text_shapes) < 3 or len(images) != 2 or tables:
        raise HwpLiveError(
            "editorial PPTX 재구성은 표 없는 다중 텍스트·두 원본 그림 슬라이드만 지원합니다"
        )
    title_shape = next(
        (shape for shape in text_shapes if shape.is_heading), text_shapes[0]
    )

    def first_text(shape: PptxTextShapeData) -> str:
        return shape.paragraphs[0].text.strip() if shape.paragraphs else ""

    excluded_shape = next(
        (shape for shape in text_shapes if "총 수량에서 제외" in first_text(shape)),
        None,
    )
    horizontal_shape = next(
        (shape for shape in text_shapes if "건물의 3층 이하" in first_text(shape)),
        None,
    )
    total_shapes = tuple(
        shape
        for shape in text_shapes
        if shape not in {title_shape, excluded_shape, horizontal_shape}
        and (
            "간판의 총수량" in first_text(shape)
            or "1개 업소 1간판" in first_text(shape)
            or "다음 사항 중 어느 하나" in first_text(shape)
        )
    )
    intro_shapes = tuple(
        shape
        for shape in text_shapes
        if shape not in {title_shape, excluded_shape, horizontal_shape, *total_shapes}
        and "설치 예시" not in first_text(shape)
    )
    figure_title_shape = next(
        (shape for shape in text_shapes if "설치 예시" in first_text(shape)), None
    )
    if excluded_shape is None or horizontal_shape is None or len(total_shapes) != 3:
        raise HwpLiveError(
            "PPTX 원문 그룹을 간판 총수량·가로형간판·제외 간판으로 확정하지 못했습니다"
        )
    if figure_title_shape is None:
        raise HwpLiveError("PPTX 설치 예시 제목을 확인하지 못했습니다")

    blocks: list[LayoutBlock] = []
    mapping: list[str] = []

    def append_shape(
        shape: PptxTextShapeData,
        role: Literal["body", "heading", "table_title", "figure_title"],
    ) -> tuple[ParagraphBlock, ...]:
        converted: list[ParagraphBlock] = []
        for paragraph_index, paragraph in enumerate(shape.paragraphs):
            block = _editorial_paragraph(
                shape,
                paragraph,
                paragraph_index=paragraph_index,
                character_style=character_style,
                paragraph_style=paragraph_style,
                font_size_pt=font_size_pt,
                text_color=text_color,
                style_role=role,
            )
            if block is not None:
                converted.append(block)
                mapping.append(
                    f"shape[{shape.source_order}].paragraph[{paragraph_index}]"
                )
        return tuple(converted)

    title_blocks = append_shape(title_shape, "heading")
    blocks.extend(title_blocks)
    for shape in intro_shapes:
        blocks.extend(append_shape(shape, "heading"))

    def generated_title(text: str) -> ParagraphBlock:
        return ParagraphBlock(kind="paragraph", text=text, style_role="table_title")

    def make_group_table(
        group_title: str,
        group_label: str,
        shapes: tuple[PptxTextShapeData, ...],
        *,
        skip_first_paragraph: bool = False,
    ) -> TableBlock:
        headers = tuple(
            _editorial_cell(
                (ParagraphBlock(kind="paragraph", text=header, style_role="body"),),
                header=True,
            )
            for header in ("구분", "기준", "추가/비고")
        )
        table_rows: list[tuple[TableCell, ...]] = [headers]
        for shape in shapes:
            if not shape.paragraphs:
                continue
            start = 1
            key_block = (
                ParagraphBlock(kind="paragraph", text=group_label)
                if skip_first_paragraph
                else _editorial_paragraph(
                    shape,
                    shape.paragraphs[0],
                    paragraph_index=0,
                    character_style=character_style,
                    paragraph_style=paragraph_style,
                    font_size_pt=font_size_pt,
                    text_color=text_color,
                    style_role="body",
                )
            )
            source_paragraphs: list[ParagraphBlock] = []
            source_indexes: list[int] = []
            for paragraph_index, paragraph in enumerate(shape.paragraphs):
                if paragraph_index < start:
                    continue
                block = _editorial_paragraph(
                    shape,
                    paragraph,
                    paragraph_index=paragraph_index,
                    character_style=character_style,
                    paragraph_style=paragraph_style,
                    font_size_pt=font_size_pt,
                    text_color=text_color,
                    style_role="body",
                )
                if block is not None:
                    source_paragraphs.append(block)
                    source_indexes.append(paragraph_index)
            if key_block is None or not source_paragraphs:
                continue
            criterion: list[tuple[ParagraphBlock, int]] = []
            additional: list[tuple[ParagraphBlock, int]] = []
            for block, paragraph_index in zip(
                source_paragraphs, source_indexes, strict=True
            ):
                if block.text.strip() and not block.text.lstrip().startswith(
                    ("■", "ㆍ", "◦", "•", "-")
                ):
                    additional.append((block, paragraph_index))
                else:
                    criterion.append((block, paragraph_index))
            if not criterion:
                criterion, additional = additional, []
            row_index = len(table_rows)
            if not skip_first_paragraph:
                mapping.append(
                    f"shape[{shape.source_order}].paragraph[0]->{group_title}.row[{row_index}].cell[0]"
                )
            for block, paragraph_index in criterion:
                mapping.append(
                    f"shape[{shape.source_order}].paragraph[{paragraph_index}]->{group_title}.row[{row_index}].cell[1]"
                )
            for block, paragraph_index in additional:
                mapping.append(
                    f"shape[{shape.source_order}].paragraph[{paragraph_index}]->{group_title}.row[{row_index}].cell[2]"
                )
            label_cell = _editorial_cell((key_block,), key_column=True)
            table_rows.append(
                (
                    label_cell,
                    _editorial_cell(tuple(block for block, _ in criterion)),
                    _editorial_cell(tuple(block for block, _ in additional)),
                )
            )
        if len(table_rows) == 1:
            raise HwpLiveError(f"{group_title} 표에 원문 행이 없습니다")
        table = _editorial_table(
            group_title,
            tuple(table_rows),
            widths=(25.0, 122.0, 23.0),
        )
        return table

    total_table = make_group_table(
        "간판 총수량 산정 기준",
        "간판 총수량 산정 기준",
        total_shapes,
    )
    blocks.extend((generated_title("간판 총수량 산정 기준"), total_table))

    horizontal_table = make_group_table(
        "가로형간판 표시방법",
        "가로형간판 표시방법",
        (horizontal_shape,),
    )
    blocks.extend((generated_title("가로형간판 표시방법"), horizontal_table))

    excluded_title_block = _editorial_paragraph(
        excluded_shape,
        excluded_shape.paragraphs[0],
        paragraph_index=0,
        character_style=character_style,
        paragraph_style=paragraph_style,
        font_size_pt=font_size_pt,
        text_color=text_color,
        style_role="table_title",
    )
    if excluded_title_block is None:
        raise HwpLiveError("제외 간판 제목 문단을 만들지 못했습니다")
    mapping.append(f"shape[{excluded_shape.source_order}].paragraph[0]")
    blocks.append(excluded_title_block)
    excluded_table = make_group_table(
        "총수량에서 제외되는 간판",
        "총수량에서 제외되는 간판",
        (excluded_shape,),
        skip_first_paragraph=True,
    )
    blocks.append(excluded_table)

    blocks.append(PageBreakBlock(kind="page_break"))
    blocks.extend(append_shape(figure_title_shape, "figure_title"))
    image_widths: list[float] = []
    image_border = CellBorder(style="solid", width="0.12mm", color=(31, 52, 111))
    for image_index, image in enumerate(images, start=1):
        width, height = fit_image_in_box(
            image.path,
            width_mm=min(content_width - 10.0, 160.0),
            height_mm=content_height,
        )
        image_widths.append(width)
        blocks.append(
            ImageBlock(
                kind="image",
                path=image.path,
                width_mm=width,
                height_mm=height,
                alignment="center",
                caption=f"설치 예시 {image_index}",
                container="table_cell",
                frame=ImageFrame(
                    container="table_cell",
                    width_mm=160.0,
                    row_height_mm=min(250.0, height + 3.6),
                    alignment="center",
                    cell_style=TableCellStyle(
                        alignment="center",
                        vertical_alignment="center",
                        padding=CellPadding(
                            left_mm=1.8,
                            right_mm=1.8,
                            top_mm=1.8,
                            bottom_mm=1.8,
                        ),
                        borders=CellBorders(
                            left=image_border,
                            right=image_border,
                            top=image_border,
                            bottom=image_border,
                        ),
                    ),
                ),
            )
        )
    blocks.append(PageBreakBlock(kind="page_break"))

    if len(mapping) != content.text_paragraph_count:
        raise HwpLiveError(
            f"PPTX 원문 문단 매핑이 완전하지 않습니다: mapped={len(mapping)} source={content.text_paragraph_count}"
        )
    observation = CurrentFormatPptxSemanticObservation(
        source_order=source_order,
        slide_number=content.slide_number,
        slide_width_emu=content.slide_width_emu,
        slide_height_emu=content.slide_height_emu,
        slide_aspect_ratio=content.aspect_ratio,
        element_order=tuple(
            "text" if isinstance(element, PptxTextShapeData) else "image"
            for element in content.elements
        ),
        text_shape_count=content.text_shape_count,
        text_paragraph_count=content.text_paragraph_count,
        text_run_count=content.text_run_count,
        table_count=content.table_count,
        table_row_counts=(),
        table_column_counts=(),
        source_image_count=content.source_image_count,
        extracted_image_paths=tuple(image.path for image in images),
        unsupported_elements=content.unsupported_elements,
        excluded_presentation_furniture=content.excluded_presentation_furniture,
        conversion="editorial_hwp_sections",
    )
    editorial = CurrentFormatEditorialObservation(
        source_order=source_order,
        slide_number=content.slide_number,
        target_page_count=3,
        section_titles=(
            "간판 총수량 산정 기준",
            "가로형간판 표시방법",
            "총수량에서 제외되는 간판",
            "설치 예시",
        ),
        table_group_row_counts=(
            len(total_table.rows),
            len(horizontal_table.rows),
            len(excluded_table.rows),
        ),
        source_paragraph_mapping=tuple(mapping),
        source_image_widths_mm=tuple(image_widths),
        excluded_presentation_furniture=content.excluded_presentation_furniture,
    )
    fit = CurrentFormatFitObservation(
        source_order=source_order,
        source_type="pptx_slide",
        content_box_width_mm=content_width,
        content_box_height_mm=content_height,
        requested_width_mm=content_width,
        requested_height_mm=content_width / content.aspect_ratio,
    )
    return tuple(blocks), fit, observation, editorial


def _destination_excel_table(block: TableBlock) -> TableBlock:
    navy = (31, 78, 121)
    dark_body = (31, 31, 31)
    border = CellBorder(style="solid", width="0.12mm", color=navy)
    rows: list[tuple[TableCell, ...]] = []
    for row_index, row in enumerate(block.rows):
        converted: list[TableCell] = []
        for cell in row:
            paragraphs = (
                (
                    ParagraphBlock(
                        kind="paragraph",
                        text=cell.text,
                        style_role="body",
                        preserve_source_text=True,
                    ),
                )
                if cell.text
                else ()
            )
            header = row_index == 0
            if header:
                paragraphs = tuple(
                    paragraph.model_copy(
                        update={"bold": True, "text_color": (255, 255, 255)}
                    )
                    for paragraph in paragraphs
                )
            converted.append(
                TableCell(
                    text=cell.text,
                    paragraphs=paragraphs,
                    bold=True if header else None,
                    text_color=(255, 255, 255) if header else dark_body,
                    fill_color=navy if header else (255, 255, 255),
                    alignment="left",
                    vertical_alignment="top",
                    padding=CellPadding(
                        left_mm=1.8,
                        right_mm=1.8,
                        top_mm=0.6,
                        bottom_mm=0.6,
                    ),
                    borders=CellBorders(
                        left=border,
                        right=border,
                        top=border,
                        bottom=border,
                    ),
                )
            )
        rows.append(tuple(converted))
    return block.model_copy(
        update={
            "border_mode": "explicit",
            "base_style_name": "표내용",
            "rows": tuple(rows),
        }
    )


def _semantic_block_height(block: LayoutBlock, content_width: float) -> float:
    if isinstance(block, ParagraphBlock):
        font_size = block.font_size_pt or 10.0
        width = max(
            1.0,
            content_width - (block.left_margin_mm or 0) - (block.right_margin_mm or 0),
        )
        glyph_width = max(0.8, font_size * 0.352778 * 0.92)
        characters_per_line = max(1, int(width / glyph_width))
        lines = sum(
            max(1, (len(line) + characters_per_line - 1) // characters_per_line)
            for line in block.text.splitlines() or ("",)
        )
        line_spacing = (block.line_spacing_percent or 160) / 100
        return (
            lines * font_size * 0.352778 * line_spacing
            + (block.space_before_mm or 0)
            + (block.space_after_mm or 0)
        )
    if isinstance(block, ImageBlock):
        return block.height_mm
    if isinstance(block, TableBlock):
        return sum(block.row_heights_mm or (5.0 for _ in block.rows)) + (
            7.0 if block.caption is not None else 0.0
        )
    return 0.0


def _pptx_semantic_blocks(
    content: PptxSlideContent,
    *,
    source_order: int,
    character_style: CharacterStyle,
    paragraph_style: ParagraphStyle,
    font_size_pt: float,
    text_color: tuple[int, int, int],
    left_margin_mm: float,
    right_margin_mm: float,
    indentation_mm: float,
    space_before_mm: float,
    space_after_mm: float,
    content_width: float,
    content_height: float,
) -> tuple[
    tuple[LayoutBlock, ...],
    CurrentFormatFitObservation,
    CurrentFormatPptxSemanticObservation,
]:
    blocks: list[LayoutBlock] = []
    element_order: list[Literal["text", "image", "table"]] = []
    page_height = 0.0

    def append_paged(block: LayoutBlock) -> None:
        nonlocal page_height
        block_height = _semantic_block_height(block, content_width)
        if page_height > 0 and page_height + block_height > content_height:
            blocks.append(PageBreakBlock(kind="page_break"))
            page_height = 0.0
        blocks.append(block)
        page_height += block_height

    extracted_images: list[Path] = []
    table_row_counts: list[int] = []
    table_column_counts: list[int] = []
    for element in content.elements:
        if isinstance(element, PptxTextShapeData):
            element_order.append("text")
            for paragraph_index, paragraph in enumerate(element.paragraphs):
                block = _pptx_paragraph_block(
                    element,
                    paragraph,
                    paragraph_index=paragraph_index,
                    character_style=character_style,
                    paragraph_style=paragraph_style,
                    font_size_pt=font_size_pt,
                    text_color=text_color,
                    left_margin_mm=left_margin_mm,
                    right_margin_mm=right_margin_mm,
                    indentation_mm=indentation_mm,
                    space_before_mm=space_before_mm,
                    space_after_mm=space_after_mm,
                )
                if block is not None:
                    append_paged(block)
            continue
        if isinstance(element, PptxImageShapeData):
            element_order.append("image")
            extracted_images.append(element.path)
            requested_width = max(
                1.0,
                content_width * element.geometry.width_emu / content.slide_width_emu,
            )
            requested_height = max(
                1.0,
                content_height * element.geometry.height_emu / content.slide_height_emu,
            )
            width, height = fit_image_in_box(
                element.path,
                width_mm=min(content_width, requested_width),
                height_mm=min(content_height, requested_height),
            )
            append_paged(
                ImageBlock(
                    kind="image",
                    path=element.path,
                    width_mm=width,
                    height_mm=height,
                )
            )
            continue
        element_order.append("table")
        table_row_counts.append(len(element.rows))
        table_column_counts.append(len(element.rows[0]))
        append_paged(
            _pptx_table_block(
                element,
                content,
                character_style=character_style,
                paragraph_style=paragraph_style,
                font_size_pt=font_size_pt,
                text_color=text_color,
                content_width=content_width,
                content_height=content_height,
            )
        )
    if not blocks:
        raise HwpLiveError(
            "PPTX 슬라이드에서 editable text, table, image을 찾지 못했습니다"
        )
    observation = CurrentFormatPptxSemanticObservation(
        source_order=source_order,
        slide_number=content.slide_number,
        slide_width_emu=content.slide_width_emu,
        slide_height_emu=content.slide_height_emu,
        slide_aspect_ratio=content.aspect_ratio,
        element_order=tuple(element_order),
        text_shape_count=content.text_shape_count,
        text_paragraph_count=content.text_paragraph_count,
        text_run_count=content.text_run_count,
        table_count=content.table_count,
        table_row_counts=tuple(table_row_counts),
        table_column_counts=tuple(table_column_counts),
        source_image_count=content.source_image_count,
        extracted_image_paths=tuple(extracted_images),
        unsupported_elements=content.unsupported_elements,
        excluded_presentation_furniture=content.excluded_presentation_furniture,
    )
    fit = CurrentFormatFitObservation(
        source_order=source_order,
        source_type="pptx_slide",
        content_box_width_mm=content_width,
        content_box_height_mm=content_height,
        requested_width_mm=content_width,
        requested_height_mm=content_width / content.aspect_ratio,
    )
    return tuple(blocks), fit, observation


@final
class HwpPublicDocumentTools:
    __slots__ = ("_executor",)

    def __init__(self, executor: PublicDocumentExecutor) -> None:
        self._executor = executor

    async def hwp_preflight_layout(
        self,
        *,
        layout: LayoutPlan,
        document_path: str | None = None,
    ) -> LayoutPreflightResult:
        return await self._executor.preflight_layout(document_path, layout)

    async def hwp_append_layout(
        self,
        *,
        operation_id: PublicOperationId,
        layout: LayoutPlan,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.append_layout",
            layout=layout.model_copy(
                update={
                    "target": "document_end",
                    "page": None,
                    "replace_selection": False,
                }
            ),
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(
            metadata.APPEND_LAYOUT_INTENT, inputs, None
        )
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

    async def hwp_insert_layout(
        self,
        *,
        operation_id: PublicOperationId,
        layout: LayoutPlan,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.insert_layout",
            layout=layout,
            policy=HwpOperatePolicy(
                ambiguity="return_candidates",
                atomic=False,
            ),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(
            metadata.INSERT_LAYOUT_INTENT, inputs, None
        )
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

    async def hwp_append_report(
        self,
        *,
        operation_id: PublicOperationId,
        report: ReportPlan,
        document_path: str | None = None,
    ) -> PublicActionResult:
        return await self.hwp_append_layout(
            operation_id=operation_id,
            layout=report_layout_plan(report),
            document_path=document_path,
        )

    async def hwp_append_excel_table(
        self,
        *,
        operation_id: PublicOperationId,
        excel_path: str,
        sheet_name: str | None = None,
        sheet_index: int = 0,
        cell_range: str | None = None,
        title: str | None = None,
        repeat_header: bool = True,
        preserve_excel_font: bool = False,
        preserve_excel_row_heights: bool = True,
        document_path: str | None = None,
    ) -> PublicActionResult:
        blocks = await to_thread.run_sync(
            partial(
                table_blocks_from_excel,
                Path(excel_path),
                sheet_name=sheet_name,
                sheet_index=sheet_index,
                cell_range=cell_range,
                caption=title,
                repeat_header=repeat_header,
                preserve_font=preserve_excel_font,
                preserve_row_heights=preserve_excel_row_heights,
                trim_unused_edges=True,
                split_gutters=True,
            )
        )
        if not blocks:
            not_found = OperationResult(
                request_id=operation_id,
                status="not_found",
                query=metadata.APPEND_LAYOUT_INTENT,
                registry_entries=operation_registry().count,
                lookup_microseconds=0,
                message="엑셀에서 넣을 표를 찾지 못했습니다.",
                verified=False,
                commands_executed=0,
                current_page=None,
                page_count=None,
                modified=False,
                partial_mutation=False,
                retry_safe=True,
            )
            return to_public_action_result(not_found, (), DOCUMENT_INPUT_ALIASES)
        layout_blocks: list[LayoutBlock] = []
        for index, block in enumerate(blocks):
            if index:
                layout_blocks.append(PageBreakBlock(kind="page_break"))
            layout_blocks.append(block)
        return await self.hwp_append_layout(
            operation_id=operation_id,
            layout=LayoutPlan(target="document_end", blocks=tuple(layout_blocks)),
            document_path=document_path,
        )

    async def hwp_insert_current_format_content(
        self,
        *,
        operation_id: PublicOperationId,
        sources: tuple[CurrentFormatSource, ...],
        document_path: str | None = None,
        target: Literal["current", "document_end", "after_page"] = "current",
        page: int | None = None,
        replace_selection: bool = False,
    ) -> PublicActionResult:
        if not sources:
            failed = OperationResult(
                request_id=operation_id,
                status="needs_input",
                query=metadata.INSERT_CURRENT_FORMAT_CONTENT_INTENT,
                registry_entries=operation_registry().count,
                lookup_microseconds=0,
                message="sources must contain at least one current-format source",
                verified=False,
                commands_executed=0,
                current_page=None,
                page_count=None,
                modified=False,
                partial_mutation=False,
                retry_safe=True,
            )
            return to_public_action_result(failed, (), DOCUMENT_INPUT_ALIASES)
        before = await self._executor.inspect_context(document_path)
        content_width, content_height = _content_box(before)
        paragraph_style = before.paragraph_style
        character_style = before.character_style
        font_size_pt = character_style.height_hwpunit / 100
        text_color = _color_tuple(character_style.text_color)
        left_margin_mm = _hwpunit_to_mm(paragraph_style.left_margin_hwpunit)
        right_margin_mm = _hwpunit_to_mm(paragraph_style.right_margin_hwpunit)
        indentation_mm = _hwpunit_to_mm(paragraph_style.indentation_hwpunit)
        space_before_mm = _hwpunit_to_mm(paragraph_style.previous_spacing_hwpunit)
        space_after_mm = _hwpunit_to_mm(paragraph_style.next_spacing_hwpunit)
        blocks: list[LayoutBlock] = []
        fits: list[CurrentFormatFitObservation] = []
        order: list[str] = []
        pptx_semantic_observations: list[CurrentFormatPptxSemanticObservation] = []
        editorial_observations: list[CurrentFormatEditorialObservation] = []
        semantic_pptx_seen = False
        temporary_asset_directory = tempfile.TemporaryDirectory(
            prefix="hwp-current-format-pptx-assets-"
        )

        def cleanup_temporary_asset_directory() -> None:
            temporary_asset_directory.cleanup()

        try:
            for index, source in enumerate(sources, start=1):
                order.append(source.kind)
                if isinstance(source, CurrentFormatTextSource):
                    for text in source.paragraphs:
                        blocks.append(
                            ParagraphBlock(
                                kind="paragraph",
                                text=text,
                                font_name=character_style.face_name,
                                font_size_pt=font_size_pt,
                                bold=character_style.bold,
                                text_color=text_color,
                                align_type_raw=paragraph_style.align_type,
                                line_spacing_percent=paragraph_style.line_spacing,
                                heading_type=paragraph_style.heading_type,
                                heading_level=paragraph_style.heading_level,
                                left_margin_mm=left_margin_mm,
                                right_margin_mm=right_margin_mm,
                                indentation_mm=indentation_mm,
                                space_before_mm=space_before_mm,
                                space_after_mm=space_after_mm,
                            )
                        )
                    fits.append(
                        CurrentFormatFitObservation(
                            source_order=index,
                            source_type="text",
                            content_box_width_mm=content_width,
                            content_box_height_mm=content_height,
                        )
                    )
                    continue
                if isinstance(source, CurrentFormatImageSource):
                    width, height = fit_image_in_box(
                        source.path,
                        width_mm=content_width,
                        height_mm=content_height,
                    )
                    blocks.append(
                        ImageBlock(
                            kind="image",
                            path=source.path,
                            width_mm=width,
                            height_mm=height,
                            caption=source.caption,
                        )
                    )
                    fits.append(
                        CurrentFormatFitObservation(
                            source_order=index,
                            source_type="image",
                            content_box_width_mm=content_width,
                            content_box_height_mm=content_height,
                            requested_width_mm=content_width,
                            requested_height_mm=content_height,
                            fitted_width_mm=width,
                            fitted_height_mm=height,
                            preserves_aspect_ratio=True,
                        )
                    )
                    continue
                if isinstance(source, CurrentFormatPptxSlideSource):
                    semantic_pptx_seen = True
                    asset_directory = (
                        Path(source.asset_output_directory)
                        if source.asset_output_directory is not None
                        else Path(temporary_asset_directory.name) / f"slide-{index}"
                    )
                    content = await to_thread.run_sync(
                        partial(
                            read_pptx_slide,
                            source.path,
                            slide_number=source.slide_number,
                            output_directory=asset_directory,
                        )
                    )
                    if (
                        content.table_count == 0
                        and content.text_shape_count > 1
                        and content.source_image_count == 2
                    ):
                        (
                            semantic_blocks,
                            fit,
                            observation,
                            editorial,
                        ) = pptx_editorial_blocks(
                            content,
                            source_order=index,
                            character_style=character_style,
                            paragraph_style=paragraph_style,
                            font_size_pt=font_size_pt,
                            text_color=text_color,
                            content_width=content_width,
                            content_height=content_height,
                        )
                        blocks.extend(semantic_blocks)
                        editorial_observations.append(editorial)
                    else:
                        semantic_blocks, fit, observation = _pptx_semantic_blocks(
                            content,
                            source_order=index,
                            character_style=character_style,
                            paragraph_style=paragraph_style,
                            font_size_pt=font_size_pt,
                            text_color=text_color,
                            left_margin_mm=left_margin_mm,
                            right_margin_mm=right_margin_mm,
                            indentation_mm=indentation_mm,
                            space_before_mm=space_before_mm,
                            space_after_mm=space_after_mm,
                            content_width=content_width,
                            content_height=content_height,
                        )
                        blocks.extend(semantic_blocks)
                    fits.append(fit)
                    pptx_semantic_observations.append(observation)
                    continue
                if isinstance(source, CurrentFormatPptxSource):
                    rows = await to_thread.run_sync(
                        partial(read_pptx, source.path, table_index=source.table_index)
                    )
                    block_width = min(content_width, content_height * 16 / 9)
                    block_height = block_width * 9 / 16
                    table = TableBlock(
                        kind="table",
                        caption=source.caption,
                        rows=tuple(
                            tuple(
                                TableCell(
                                    text=cell,
                                    font_name=character_style.face_name,
                                    font_size_pt=font_size_pt,
                                    bold=character_style.bold,
                                    text_color=text_color,
                                    line_spacing_percent=paragraph_style.line_spacing,
                                )
                                for cell in row
                            )
                            for row in rows
                        ),
                        column_widths_mm=tuple(
                            block_width / len(rows[0]) for _ in rows[0]
                        ),
                        row_heights_mm=tuple(block_height / len(rows) for _ in rows),
                    )
                    blocks.append(table)
                    fits.append(
                        CurrentFormatFitObservation(
                            source_order=index,
                            source_type="pptx_table",
                            content_box_width_mm=content_width,
                            content_box_height_mm=content_height,
                            requested_width_mm=content_width,
                            requested_height_mm=content_height,
                            fitted_width_mm=block_width,
                            fitted_height_mm=block_height,
                            preserves_aspect_ratio=True,
                        )
                    )
                    continue
                if (
                    semantic_pptx_seen
                    and blocks
                    and not isinstance(blocks[-1], PageBreakBlock)
                ):
                    blocks.append(PageBreakBlock(kind="page_break"))
                excel_table = await to_thread.run_sync(
                    partial(
                        table_block_from_excel,
                        source.path,
                        sheet_name=source.sheet_name,
                        sheet_index=source.sheet_index,
                        cell_range=source.cell_range,
                        target_width_mm=content_width,
                        caption=source.caption,
                        repeat_header=source.repeat_header,
                        preserve_font=source.preserve_excel_font,
                        preserve_row_heights=source.preserve_excel_row_heights,
                        trim_unused_edges=True,
                    )
                )
                table = _destination_excel_table(excel_table)
                blocks.append(table)
                fits.append(
                    CurrentFormatFitObservation(
                        source_order=index,
                        source_type="excel_table",
                        content_box_width_mm=content_width,
                        content_box_height_mm=content_height,
                        requested_width_mm=content_width,
                    )
                )
        except HwpLiveError as error:
            evidence = CurrentFormatInsertEvidence(
                source_count=len(sources),
                insertion_order=tuple(order),
                style=_style_evidence(before, None),
                numbering=_numbering(before),
                fit=tuple(fits),
                pptx_semantic=tuple(pptx_semantic_observations),
                editorial=tuple(editorial_observations),
                planned_inserted_page_count=(
                    1 + sum(isinstance(block, PageBreakBlock) for block in blocks)
                    if blocks
                    else None
                ),
                page_break_count=sum(
                    isinstance(block, PageBreakBlock) for block in blocks
                ),
            )
            failed = OperationResult(
                request_id=operation_id,
                status="needs_input",
                query=metadata.INSERT_CURRENT_FORMAT_CONTENT_INTENT,
                registry_entries=operation_registry().count,
                lookup_microseconds=0,
                message=str(error),
                verified=False,
                partial_mutation=False,
                retry_safe=True,
                current_format_insert=evidence,
            )
            cleanup_temporary_asset_directory()
            return to_public_action_result(failed, (), DOCUMENT_INPUT_ALIASES)
        except Exception:
            cleanup_temporary_asset_directory()
            raise
        layout = LayoutPlan(
            target=target,
            page=page,
            replace_selection=replace_selection,
            blocks=tuple(blocks),
        )
        if editorial_observations:
            planned_pages = 1 + sum(
                isinstance(block, PageBreakBlock) for block in blocks
            )
            expected_pages = (
                3
                if any(
                    isinstance(source, CurrentFormatExcelSource) for source in sources
                )
                else 2
            )
            if planned_pages != expected_pages:
                preflight_message = (
                    "editorial page plan is not deterministic: "
                    f"planned={planned_pages} expected={expected_pages}"
                )
            else:
                preflight_message = ""
            try:
                preflight = await self._executor.preflight_layout(document_path, layout)
            except HwpLiveError as error:
                preflight_message = str(error)
            else:
                if not preflight_message:
                    preflight_message = (
                        "editorial preflight must be exact and safe: "
                        f"overflow={preflight.overflow}, "
                        f"estimated={preflight.estimated_width_mm}x"
                        f"{preflight.estimated_height_mm}mm, "
                        f"usable={preflight.usable_width_mm}x"
                        f"{preflight.usable_height_mm}mm"
                    )
                    # The plan's own stated geometry, deliberately not the
                    # band-aware ``overflow``. The uncertainty band is new
                    # information for the model, not a new reason to refuse an
                    # insert that used to go through.
                    if preflight.stated_geometry_overflow == "none":
                        preflight_message = ""
            if preflight_message:
                evidence = CurrentFormatInsertEvidence(
                    source_count=len(sources),
                    insertion_order=tuple(order),
                    style=_style_evidence(before, None),
                    numbering=_numbering(before),
                    fit=tuple(fits),
                    pptx_semantic=tuple(pptx_semantic_observations),
                    editorial=tuple(editorial_observations),
                    planned_inserted_page_count=1
                    + sum(isinstance(block, PageBreakBlock) for block in blocks),
                    page_break_count=sum(
                        isinstance(block, PageBreakBlock) for block in blocks
                    ),
                )
                failed = OperationResult(
                    request_id=operation_id,
                    status="needs_input",
                    query=metadata.INSERT_CURRENT_FORMAT_CONTENT_INTENT,
                    registry_entries=operation_registry().count,
                    lookup_microseconds=0,
                    message=preflight_message,
                    verified=False,
                    commands_executed=0,
                    modified=False,
                    partial_mutation=False,
                    retry_safe=True,
                    current_format_insert=evidence,
                )
                cleanup_temporary_asset_directory()
                return to_public_action_result(failed, (), DOCUMENT_INPUT_ALIASES)
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.insert_layout",
            parameters={"file_checkpoint_history": "i09_current_format_insert"},
            layout=layout,
            policy=HwpOperatePolicy(ambiguity="return_candidates", atomic=False),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        try:
            result = await self._executor.execute(
                metadata.INSERT_CURRENT_FORMAT_CONTENT_INTENT,
                inputs,
                None,
            )
            after = (
                await self._executor.inspect_context(document_path)
                if result.status == "executed" and result.verified is True
                else None
            )
            evidence = CurrentFormatInsertEvidence(
                source_count=len(sources),
                insertion_order=tuple(order),
                style=_style_evidence(before, after),
                numbering=_numbering(before),
                fit=tuple(fits),
                pptx_semantic=tuple(pptx_semantic_observations),
                editorial=tuple(editorial_observations),
                planned_inserted_page_count=(
                    1 + sum(isinstance(block, PageBreakBlock) for block in blocks)
                ),
                page_break_count=sum(
                    isinstance(block, PageBreakBlock) for block in blocks
                ),
                live_readback=(
                    "current_context_read_after_write"
                    if after is not None
                    else "not_available_because_write_did_not_succeed"
                ),
            )
            return to_public_action_result(
                result.model_copy(update={"current_format_insert": evidence}),
                (),
                DOCUMENT_INPUT_ALIASES,
            )
        finally:
            cleanup_temporary_asset_directory()

    async def hwp_save_reopen_verify(
        self,
        *,
        operation_id: PublicOperationId,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.save_reopen_verify",
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(
            metadata.SAVE_REOPEN_VERIFY_INTENT,
            inputs,
            None,
        )
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

    async def hwp_save(
        self,
        *,
        operation_id: PublicOperationId,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.save",
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(metadata.SAVE_INTENT, inputs, None)
        return to_public_action_result(result, (), DOCUMENT_INPUT_ALIASES)

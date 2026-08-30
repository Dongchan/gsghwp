from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from hwp_live_contract import CharacterStyle, PageSetup, ParagraphStyle
from hwp_live_values import ContractModel


class CurrentFormatTextSource(ContractModel):
    kind: Literal["text"]
    paragraphs: tuple[str, ...] = Field(min_length=1, max_length=100)


class CurrentFormatImageSource(ContractModel):
    kind: Literal["image"]
    path: Path
    caption: str | None = Field(default=None, max_length=2_000)


class CurrentFormatPptxSource(ContractModel):
    kind: Literal["pptx_table"]
    path: Path
    table_index: int = Field(default=0, ge=0)
    caption: str | None = Field(default=None, max_length=2_000)


class CurrentFormatPptxSlideSource(ContractModel):
    kind: Literal["pptx_slide"]
    path: Path
    slide_number: int = Field(ge=1)
    asset_output_directory: Path | None = Field(
        default=None,
        description=(
            "선택한 슬라이드의 원본 그림 파일을 추출해 보존할 작업 소유 "
            "디렉터리. 슬라이드 전체를 렌더링한 파일은 만들지 않는다."
        ),
    )
    caption: str | None = Field(default=None, max_length=2_000)


class CurrentFormatExcelSource(ContractModel):
    kind: Literal["excel_table"]
    path: Path
    sheet_name: str | None = Field(default=None, max_length=200)
    sheet_index: int = Field(default=0, ge=0)
    cell_range: str | None = Field(default=None, max_length=100)
    caption: str | None = Field(default=None, max_length=2_000)
    repeat_header: bool = True
    preserve_excel_font: bool = False
    preserve_excel_row_heights: bool = True


CurrentFormatSource = Annotated[
    CurrentFormatTextSource
    | CurrentFormatImageSource
    | CurrentFormatPptxSource
    | CurrentFormatPptxSlideSource
    | CurrentFormatExcelSource,
    Field(discriminator="kind"),
]


class CurrentFormatNumberingObservation(ContractModel):
    heading_type: int | None = None
    heading_level: int | None = None
    marker_is_automatic: bool | None = None
    manual_marker_before: str | None = Field(default=None, max_length=64)
    manual_marker_after: str | None = Field(default=None, max_length=64)
    numeric_marker_value: Literal["unavailable_for_automatic_numbering"] | None = None
    comparison: Literal[
        "automatic_heading_level_and_render_only",
        "manual_marker_value_compared",
        "no_numbering_observed",
        "unobserved",
    ] = "unobserved"


class CurrentFormatFitObservation(ContractModel):
    source_order: int = Field(ge=1)
    source_type: Literal["text", "image", "pptx_table", "pptx_slide", "excel_table"]
    content_box_width_mm: float = Field(ge=0)
    content_box_height_mm: float = Field(ge=0)
    requested_width_mm: float | None = Field(default=None, ge=0)
    requested_height_mm: float | None = Field(default=None, ge=0)
    fitted_width_mm: float | None = Field(default=None, ge=0)
    fitted_height_mm: float | None = Field(default=None, ge=0)
    preserves_aspect_ratio: bool | None = None


class CurrentFormatPptxSemanticObservation(ContractModel):
    source_order: int = Field(ge=1)
    slide_number: int = Field(ge=1)
    slide_width_emu: int = Field(gt=0)
    slide_height_emu: int = Field(gt=0)
    slide_aspect_ratio: float = Field(gt=0)
    element_order: tuple[Literal["text", "image", "table"], ...] = Field(max_length=100)
    text_shape_count: int = Field(ge=0)
    text_paragraph_count: int = Field(ge=0)
    text_run_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    table_row_counts: tuple[int, ...] = Field(max_length=100)
    table_column_counts: tuple[int, ...] = Field(max_length=100)
    source_image_count: int = Field(ge=0)
    extracted_image_paths: tuple[Path, ...] = Field(max_length=100)
    unsupported_elements: tuple[str, ...] = Field(max_length=100)
    excluded_presentation_furniture: tuple[str, ...] = Field(max_length=20)
    conversion: Literal["semantic_hwp_blocks", "editorial_hwp_sections"] = (
        "semantic_hwp_blocks"
    )


class CurrentFormatEditorialObservation(ContractModel):
    source_order: int = Field(ge=1)
    slide_number: int = Field(ge=1)
    target_page_count: int = Field(ge=1)
    section_titles: tuple[str, ...] = Field(min_length=1, max_length=10)
    table_group_row_counts: tuple[int, ...] = Field(max_length=10)
    source_paragraph_mapping: tuple[str, ...] = Field(min_length=1, max_length=100)
    source_image_widths_mm: tuple[float, ...] = Field(max_length=10)
    excluded_presentation_furniture: tuple[str, ...] = Field(max_length=20)


class CurrentFormatStyleObservation(ContractModel):
    character_style_before: CharacterStyle
    paragraph_style_before: ParagraphStyle
    page_setup_before: PageSetup
    character_style_after: CharacterStyle | None = None
    paragraph_style_after: ParagraphStyle | None = None
    page_setup_after: PageSetup | None = None
    inserted_character_style: CharacterStyle
    inserted_paragraph_style: ParagraphStyle
    page_setup_unchanged: bool | None = None
    paragraph_margins_are_not_page_margins: bool = True


class CurrentFormatInsertEvidence(ContractModel):
    source_count: int = Field(ge=0, le=100)
    insertion_order: tuple[str, ...] = Field(max_length=100)
    style: CurrentFormatStyleObservation
    numbering: CurrentFormatNumberingObservation
    fit: tuple[CurrentFormatFitObservation, ...] = Field(max_length=100)
    pptx_semantic: tuple[CurrentFormatPptxSemanticObservation, ...] = Field(
        default=(), max_length=100
    )
    editorial: tuple[CurrentFormatEditorialObservation, ...] = Field(
        default=(), max_length=10
    )
    planned_inserted_page_count: int | None = Field(default=None, ge=1)
    page_break_count: int = Field(default=0, ge=0)
    unobserved_axes: tuple[str, ...] = ("columns",)
    unobserved_reasons: tuple[str, ...] = (
        "column_count_is_not_reported_by_current_live_contract",
    )
    live_preflight: Literal["current_context_read_before_write"] = (
        "current_context_read_before_write"
    )
    live_readback: Literal[
        "current_context_read_after_write",
        "not_available_because_write_did_not_succeed",
    ] = "not_available_because_write_did_not_succeed"

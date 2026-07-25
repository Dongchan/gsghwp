from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from hwp_live_table_contract import BorderStyle, BorderWidth, CellPadding
from hwp_live_values import Alignment, ContractModel, Rgb
from hwp_reference_layout_gap import ProtectedGap
from hwp_reference_layout_gap_validation import validate_protected_gaps


EdgeOrientation = Literal["horizontal", "vertical"]
OcrMode = Literal["off", "auto"]
ReferenceVerticalAlignment = Literal["inherit", "top", "center", "bottom"]
ReferenceLineSpacingType = Literal["percent", "fixed", "margin"]
ReferenceBreakMode = Literal["line", "paragraph"]


class ReferenceMerge(ContractModel):
    row: int = Field(ge=0, le=49)
    column: int = Field(ge=0, le=49)
    row_span: int = Field(default=1, ge=1, le=50)
    column_span: int = Field(default=1, ge=1, le=50)

    @model_validator(mode="after")
    def validate_span(self) -> ReferenceMerge:
        if self.row_span == 1 and self.column_span == 1:
            raise ValueError("merge must span more than one cell")
        return self


class VisibleEdge(ContractModel):
    orientation: EdgeOrientation
    line: int = Field(ge=0, le=50)
    start: int = Field(ge=0, le=49)
    end: int = Field(ge=1, le=50)
    style: BorderStyle = "solid"
    width: BorderWidth = "0.12mm"
    color: Rgb = (0, 0, 0)

    @model_validator(mode="after")
    def validate_interval(self) -> VisibleEdge:
        if self.start >= self.end:
            raise ValueError("visible edge interval must be increasing")
        return self


class ReferenceStyle(ContractModel):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    font_name: str | None = Field(default=None, max_length=100)
    font_size_pt: float | None = Field(default=None, ge=1, le=96)
    bold: bool | None = None
    text_color: Rgb | None = None
    fill_color: Rgb | None = None
    alignment: Alignment = "inherit"
    vertical_alignment: ReferenceVerticalAlignment = "inherit"
    width_ratio_percent: int = Field(default=100, ge=50, le=200)
    letter_spacing_percent: int = Field(default=0, ge=-50, le=50)
    line_spacing_type: ReferenceLineSpacingType = "percent"
    line_spacing_percent: int | None = Field(default=None, ge=50, le=500)
    line_spacing_hwpunit: int | None = Field(default=None, ge=0, le=100_000)
    paragraph_before_mm: float = Field(default=0, ge=0, le=100)
    paragraph_after_mm: float = Field(default=0, ge=0, le=100)
    padding: CellPadding | None = None

    @model_validator(mode="after")
    def validate_line_spacing(self) -> ReferenceStyle:
        if self.line_spacing_type == "percent":
            if self.line_spacing_hwpunit is not None:
                raise ValueError("percent line spacing cannot use line_spacing_hwpunit")
            return self
        if self.line_spacing_percent is not None:
            raise ValueError(
                "fixed or margin line spacing cannot use line_spacing_percent"
            )
        if self.line_spacing_hwpunit is None:
            raise ValueError(
                "fixed or margin line spacing requires line_spacing_hwpunit"
            )
        if self.line_spacing_type == "fixed" and self.line_spacing_hwpunit == 0:
            raise ValueError("fixed line spacing must be positive")
        return self


class StyleRegion(ContractModel):
    top: int = Field(ge=0, le=49)
    left: int = Field(ge=0, le=49)
    bottom: int = Field(ge=1, le=50)
    right: int = Field(ge=1, le=50)
    style_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

    @model_validator(mode="after")
    def validate_rectangle(self) -> StyleRegion:
        if self.top >= self.bottom or self.left >= self.right:
            raise ValueError("style region must be a non-empty rectangle")
        return self


class TextAnchor(ContractModel):
    row: int = Field(ge=0, le=49)
    column: int = Field(ge=0, le=49)
    text: str = Field(min_length=1, max_length=20_000)
    style_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )
    break_mode: ReferenceBreakMode = "line"


class OcrWordEvidence(ContractModel):
    text: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)
    rotation_degrees: float = Field(default=0, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_box(self) -> OcrWordEvidence:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("OCR evidence box must have positive area")
        return self


class ReferenceLayoutBlock(ContractModel):
    kind: Literal["reference_layout"]
    source_image: Path | None = Field(
        default=None,
        description="정규화 좌표와 비율을 검증할 원본 참고 이미지 경로",
    )
    analysis_id: str | None = Field(
        default=None,
        pattern=r"^ria-[0-9a-f]{16}$",
        description="hwp_analyze_reference_image가 발급한 변경 불가능한 분석 ID",
    )
    row_breakpoints: tuple[float, ...] = Field(
        min_length=2,
        max_length=51,
        description="0과 1을 포함해 엄격히 증가하는 정규화 행 경계",
    )
    column_breakpoints: tuple[float, ...] = Field(
        min_length=2,
        max_length=51,
        description="0과 1을 포함해 엄격히 증가하는 정규화 열 경계",
    )
    merges: tuple[ReferenceMerge, ...] = Field(
        default=(),
        max_length=200,
        description="겹치지 않는 병합 영역; 병합 내부의 비기준 셀에는 내용을 두지 않습니다",
    )
    visible_edges: tuple[VisibleEdge, ...] = Field(
        default=(),
        max_length=5_000,
        description="원본에 실제 보이는 수평·수직 경계 구간만 선언한 목록",
    )
    styles: tuple[ReferenceStyle, ...] = Field(
        default=(),
        max_length=256,
        description="style_regions와 text_anchors에서 키로 참조하는 공유 서식",
    )
    style_regions: tuple[StyleRegion, ...] = Field(
        default=(),
        max_length=1_000,
        description="공유 서식을 적용할 반개구간 행·열 사각형",
    )
    text_anchors: tuple[TextAnchor, ...] = Field(
        default=(),
        max_length=2_500,
        description="병합 기준 셀 또는 일반 셀에 넣을 편집 가능한 텍스트",
    )
    protected_gaps: tuple[ProtectedGap, ...] = Field(default=(), max_length=1_000)
    ocr_mode: OcrMode = "off"
    ocr_words: tuple[OcrWordEvidence, ...] = Field(default=(), max_length=10_000)

    @model_validator(mode="after")
    def validate_topology(self) -> ReferenceLayoutBlock:
        if self.analysis_id is not None and self.source_image is None:
            raise ValueError("analysis_id requires source_image")
        self._validate_breakpoints(self.row_breakpoints, "row")
        self._validate_breakpoints(self.column_breakpoints, "column")
        rows = len(self.row_breakpoints) - 1
        columns = len(self.column_breakpoints) - 1
        occupied: set[tuple[int, int]] = set()
        for merge in self.merges:
            if (
                merge.row + merge.row_span > rows
                or merge.column + merge.column_span > columns
            ):
                raise ValueError("merge is outside reference layout bounds")
            region = {
                (row, column)
                for row in range(merge.row, merge.row + merge.row_span)
                for column in range(merge.column, merge.column + merge.column_span)
            }
            if occupied.intersection(region):
                raise ValueError("merge regions overlap")
            occupied.update(region)
        self._validate_references(rows, columns)
        self._validate_edges(rows, columns)
        validate_protected_gaps(self, rows, columns)
        return self

    @staticmethod
    def _validate_breakpoints(values: tuple[float, ...], axis: str) -> None:
        if values[0] != 0 or values[-1] != 1:
            raise ValueError(f"{axis} breakpoints must start at 0 and end at 1")
        if any(value < 0 or value > 1 for value in values):
            raise ValueError(f"{axis} breakpoints must be normalized")
        if any(left >= right for left, right in zip(values, values[1:])):
            raise ValueError(f"{axis} breakpoints must be strictly increasing")

    def _validate_references(self, rows: int, columns: int) -> None:
        keys = [style.key for style in self.styles]
        if len(set(keys)) != len(keys):
            raise ValueError("reference style keys must be unique")
        known = set(keys)
        for region in self.style_regions:
            if region.bottom > rows or region.right > columns:
                raise ValueError("style region is outside reference layout bounds")
            if region.style_key not in known:
                raise ValueError("style region references an unknown style")
        merge_anchors = {(merge.row, merge.column) for merge in self.merges}
        covered = {
            (row, column)
            for merge in self.merges
            for row in range(merge.row, merge.row + merge.row_span)
            for column in range(merge.column, merge.column + merge.column_span)
        } - merge_anchors
        for anchor in self.text_anchors:
            if anchor.row >= rows or anchor.column >= columns:
                raise ValueError("text anchor is outside reference layout bounds")
            if (anchor.row, anchor.column) in covered:
                raise ValueError("text anchor cannot target a merge-covered cell")
            if anchor.style_key is not None and anchor.style_key not in known:
                raise ValueError("text anchor references an unknown style")

    def _validate_edges(self, rows: int, columns: int) -> None:
        for edge in self.visible_edges:
            limit = rows if edge.orientation == "vertical" else columns
            lines = columns if edge.orientation == "vertical" else rows
            if edge.end > limit or edge.line > lines:
                raise ValueError("visible edge is outside reference layout bounds")
            for merge in self.merges:
                crosses = (
                    edge.orientation == "vertical"
                    and merge.column < edge.line < merge.column + merge.column_span
                    and edge.start < merge.row + merge.row_span
                    and merge.row < edge.end
                ) or (
                    edge.orientation == "horizontal"
                    and merge.row < edge.line < merge.row + merge.row_span
                    and edge.start < merge.column + merge.column_span
                    and merge.column < edge.end
                )
                if crosses:
                    raise ValueError("visible edge crosses a merge")

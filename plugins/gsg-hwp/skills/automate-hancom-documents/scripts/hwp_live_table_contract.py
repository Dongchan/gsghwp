from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Final, Literal
from unicodedata import category, normalize

from pydantic import Field, model_validator

from hwp_color_normalization import ColorInput
from hwp_live_values import Alignment, ContractModel


VerticalAlignment = Literal["inherit", "top", "center", "bottom"]
BorderMode = Literal["inherit", "explicit"]
BorderStyle = Literal[
    "none",
    "solid",
    "dash",
    "dot",
    "dash_dot",
    "dash_dot_dot",
    "long_dash",
    "circle",
    "double_slim",
    "slim_thick",
    "thick_slim",
    "slim_thick_slim",
]
BorderWidth = Literal[
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
]

_HWPUNIT_PER_INCH: Final = Decimal(7_200)
_MILLIMETERS_PER_INCH: Final = Decimal("25.4")
_HWPUNIT_PER_POINT: Final = Decimal(100)


@dataclass(frozen=True, slots=True)
class BorderWidthSnapTarget:
    width: BorderWidth
    millimeters: Decimal

    @property
    def hwpunit(self) -> Decimal:
        return self.millimeters * _HWPUNIT_PER_INCH / _MILLIMETERS_PER_INCH

    def payload(self) -> dict[str, str | float]:
        return {
            "border_width": self.width,
            "millimeters": float(self.millimeters),
            "hwpunit": round(float(self.hwpunit), 10),
        }


@dataclass(frozen=True, slots=True)
class BorderWidthSnap:
    source_linewidth_pt: float
    source_hwpunit: int
    hwpunit_quantization_error_pt: float
    source_mm: float
    width: BorderWidth
    snapped_mm: float
    snapped_hwpunit: float
    residual_mm: float
    absolute_error_mm: float
    residual_hwpunit: float
    absolute_error_hwpunit: float

    def payload(self) -> dict[str, str | int | float]:
        return {
            "source_linewidth_pt": self.source_linewidth_pt,
            "source_hwpunit": self.source_hwpunit,
            "source_linewidth_hwpunit": self.source_hwpunit,
            "hwpunit_quantization_error_pt": self.hwpunit_quantization_error_pt,
            "source_mm": self.source_mm,
            "snapped_border_width": self.width,
            "border_width": self.width,
            "snapped_mm": self.snapped_mm,
            "snapped_hwpunit": self.snapped_hwpunit,
            "residual_mm": self.residual_mm,
            "border_width_snap_error_mm": self.residual_mm,
            "absolute_error_mm": self.absolute_error_mm,
            "border_width_snap_abs_error_mm": self.absolute_error_mm,
            "residual_hwpunit": self.residual_hwpunit,
            "absolute_error_hwpunit": self.absolute_error_hwpunit,
        }


_BORDER_WIDTH_VALUES: Final[tuple[BorderWidth, ...]] = (
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
BORDER_WIDTH_SNAP_TARGETS: Final[tuple[BorderWidthSnapTarget, ...]] = tuple(
    BorderWidthSnapTarget(
        width=width,
        millimeters=Decimal(width.removesuffix("mm")),
    )
    for width in _BORDER_WIDTH_VALUES
)


def snap_pdf_linewidth(linewidth_pt: float) -> BorderWidthSnap:
    points = Decimal(str(linewidth_pt))
    if not points.is_finite() or points < 0:
        raise ValueError("PDF linewidth must be a finite non-negative point value")
    source_hwpunit = int(
        (points * _HWPUNIT_PER_POINT).to_integral_value(rounding=ROUND_HALF_UP)
    )
    quantization_error_pt = Decimal(source_hwpunit) / _HWPUNIT_PER_POINT - points
    source_mm = Decimal(source_hwpunit) * _MILLIMETERS_PER_INCH / _HWPUNIT_PER_INCH
    target = min(
        BORDER_WIDTH_SNAP_TARGETS,
        key=lambda candidate: (
            abs(source_mm - candidate.millimeters),
            candidate.millimeters,
        ),
    )
    residual_mm = source_mm - target.millimeters
    residual_hwpunit = Decimal(source_hwpunit) - target.hwpunit
    return BorderWidthSnap(
        source_linewidth_pt=float(points),
        source_hwpunit=source_hwpunit,
        hwpunit_quantization_error_pt=round(float(quantization_error_pt), 10),
        source_mm=round(float(source_mm), 10),
        width=target.width,
        snapped_mm=float(target.millimeters),
        snapped_hwpunit=round(float(target.hwpunit), 10),
        residual_mm=round(float(residual_mm), 10),
        absolute_error_mm=round(float(abs(residual_mm)), 10),
        residual_hwpunit=round(float(residual_hwpunit), 10),
        absolute_error_hwpunit=round(float(abs(residual_hwpunit)), 10),
    )


def _caption_separator(character: str) -> bool:
    character_category = category(character)
    return (
        character.isspace()
        or character_category.startswith(("P", "S"))
        or character_category == "Cf"
    )


def _trim_caption_separators(value: str) -> str:
    index = 0
    while index < len(value) and _caption_separator(value[index]):
        index += 1
    return value[index:]


def _starts_with_manual_table_number(value: str) -> bool:
    normalized = _trim_caption_separators(normalize("NFKC", value))
    if not normalized.startswith("표"):
        return False
    suffix = _trim_caption_separators(normalized[1:])
    if suffix.startswith("제"):
        suffix = _trim_caption_separators(suffix[1:])
    elif suffix.casefold().startswith("no"):
        suffix = _trim_caption_separators(suffix[2:])
    return bool(suffix) and suffix[0].isnumeric()


class CellBorder(ContractModel):
    style: BorderStyle = "solid"
    width: BorderWidth = "0.12mm"
    color: ColorInput = (0, 0, 0)


class CellBorders(ContractModel):
    left: CellBorder | None = None
    right: CellBorder | None = None
    top: CellBorder | None = None
    bottom: CellBorder | None = None


class CellPadding(ContractModel):
    left_mm: float = Field(default=1.8, ge=0, le=20)
    right_mm: float = Field(default=1.8, ge=0, le=20)
    top_mm: float = Field(default=0.5, ge=0, le=20)
    bottom_mm: float = Field(default=0.5, ge=0, le=20)


class TableMerge(ContractModel):
    row: int = Field(ge=0, le=49)
    column: int = Field(ge=0, le=49)
    row_span: int = Field(default=1, ge=1, le=50)
    column_span: int = Field(default=1, ge=1, le=50)

    @model_validator(mode="after")
    def validate_span(self) -> TableMerge:
        if self.row_span == 1 and self.column_span == 1:
            raise ValueError("merge must span more than one cell")
        return self


class TableCell(ContractModel):
    text: str = Field(default="", max_length=20_000)
    style_id: int | None = Field(default=None, ge=0, le=4095)
    image_path: Path | None = None
    image_width_mm: float | None = Field(default=None, ge=1, le=250)
    image_height_mm: float | None = Field(default=None, ge=1, le=350)
    bold: bool | None = None
    font_name: str | None = Field(default=None, max_length=100)
    font_size_pt: float | None = Field(default=None, ge=1, le=96)
    text_color: ColorInput | None = None
    alignment: Alignment = "inherit"
    vertical_alignment: VerticalAlignment = "inherit"
    line_spacing_percent: int | None = Field(default=None, ge=50, le=500)
    fill_color: ColorInput | None = None
    padding: CellPadding | None = None
    borders: CellBorders | None = None

    @model_validator(mode="after")
    def validate_image(self) -> TableCell:
        dimensions = self.image_width_mm is not None or self.image_height_mm is not None
        if self.image_path is None and dimensions:
            raise ValueError("image dimensions require image_path")
        if self.image_path is not None and (
            self.image_width_mm is None or self.image_height_mm is None
        ):
            raise ValueError("image_path requires width and height")
        return self


class TableBlock(ContractModel):
    kind: Literal["table"]
    border_mode: BorderMode = Field(
        default="inherit",
        description=(
            "Use explicit for visual layout grids: omitted edges become no-border and "
            "a declared shared edge is mirrored to its adjacent cell."
        ),
    )
    caption: str | None = Field(default=None, min_length=1, max_length=2_000)
    caption_style_name: str | None = Field(default=None, min_length=1, max_length=100)
    base_style_name: str | None = Field(default=None, min_length=1, max_length=100)
    caption_style_id: int | None = Field(default=None, ge=0, le=4095)
    base_style_id: int | None = Field(default=None, ge=0, le=4095)
    # 50 의 근거는 ReferenceLayoutValidation.cpp:27 이다. 네이티브가 표 모양에
    # 두는 유일한 명시 상한이고 행·열 모두 1..50 이다.
    # 주의: TableBlock 은 참조 레이아웃이 아니라 TableCreate/HTableCreation 로
    # 만들어지며(hwp_live_native_table_layout.py:61), 그 경로에는 50 검사가 없다.
    # 그 경로의 진짜 천장은 CellTopology.cpp:13 kMaximumPhysicalSlots(200,000)
    # 물리 슬롯이다. 50 을 넘기려면 그 근거만으로는 부족하다 — TableMerge 의
    # row/column(ge=0 le=49)·span(le=50), LayoutPlan 셀 예산, 그리고 셀당 명령
    # 수가 함께 움직여야 한다. 근거가 갖춰지기 전까지 50 을 유지한다.
    rows: tuple[tuple[TableCell, ...], ...] = Field(min_length=1, max_length=50)
    column_widths_mm: tuple[float, ...] | None = None
    column_width_weights: tuple[float, ...] | None = None
    minimum_column_widths_mm: tuple[float, ...] | None = None
    row_heights_mm: tuple[float, ...] | None = None
    auto_fit_row_heights: bool = False
    repeat_header: bool = False
    split_wide_table: bool = False
    repeat_key_columns: int = Field(default=0, ge=0, le=5)
    merges: tuple[TableMerge, ...] = Field(default=(), max_length=100)
    alignment: Alignment = "left"
    left_margin_mm: float = Field(default=0, ge=0, le=100)
    right_margin_mm: float = Field(default=0, ge=0, le=100)
    indentation_mm: float = Field(default=0, ge=-100, le=100)

    @model_validator(mode="after")
    def validate_shape(self) -> TableBlock:
        if self.caption is None and self.caption_style_name is not None:
            raise ValueError("caption_style_name requires caption")
        if self.caption is None and self.caption_style_id is not None:
            raise ValueError("caption_style_id requires caption")
        if self.caption_style_name is not None and self.caption_style_id is not None:
            raise ValueError("caption style name and id are mutually exclusive")
        if self.base_style_name is not None and self.base_style_id is not None:
            raise ValueError("base style name and id are mutually exclusive")
        if self.caption is not None and _starts_with_manual_table_number(self.caption):
            raise ValueError(
                "caption must contain only the title; HWP inserts the table number"
            )
        columns = len(self.rows[0])
        # rows 와 같은 근거: ReferenceLayoutValidation.cpp:27 의 열 상한 50.
        if columns < 1 or columns > 50:
            raise ValueError("table must contain 1 to 50 columns")
        if any(len(row) != columns for row in self.rows):
            raise ValueError("table rows must have equal column counts")
        if self.column_widths_mm is not None:
            if len(self.column_widths_mm) != columns:
                raise ValueError("column widths must match the column count")
            if any(width < 1 or width > 250 for width in self.column_widths_mm):
                raise ValueError("column width is outside the supported range")
        if self.column_width_weights is not None:
            if self.column_widths_mm is not None:
                raise ValueError("column widths and weights are mutually exclusive")
            if len(self.column_width_weights) != columns:
                raise ValueError("column width weights must match the column count")
            if any(weight <= 0 for weight in self.column_width_weights):
                raise ValueError("column width weights must be positive")
        if self.minimum_column_widths_mm is not None:
            if len(self.minimum_column_widths_mm) != columns:
                raise ValueError("minimum column widths must match the column count")
            if any(width < 1 or width > 250 for width in self.minimum_column_widths_mm):
                raise ValueError("minimum column width is outside the supported range")
            if self.column_widths_mm is not None and any(
                width < minimum
                for width, minimum in zip(
                    self.column_widths_mm,
                    self.minimum_column_widths_mm,
                    strict=True,
                )
            ):
                raise ValueError("column width cannot be below its readable minimum")
        if self.repeat_key_columns >= columns:
            if self.repeat_key_columns != 0:
                raise ValueError(
                    "repeated key columns must leave at least one data column"
                )
        if self.repeat_key_columns and not self.split_wide_table:
            raise ValueError("repeated key columns require wide-table splitting")
        if self.row_heights_mm is not None:
            if len(self.row_heights_mm) != len(self.rows):
                raise ValueError("row heights must match the row count")
            if any(height < 1 or height > 250 for height in self.row_heights_mm):
                raise ValueError("row height is outside the supported range")
        _ = self._validate_merges(columns)
        return self

    def _validate_merges(self, columns: int) -> dict[tuple[int, int], tuple[int, int]]:
        occupied: set[tuple[int, int]] = set()
        owners: dict[tuple[int, int], tuple[int, int]] = {}
        for merge in self.merges:
            if (
                merge.row + merge.row_span > len(self.rows)
                or merge.column + merge.column_span > columns
            ):
                raise ValueError("merge is outside table bounds")
            region = {
                (row, column)
                for row in range(merge.row, merge.row + merge.row_span)
                for column in range(merge.column, merge.column + merge.column_span)
            }
            if occupied.intersection(region):
                raise ValueError("merge regions overlap")
            occupied.update(region)
            anchor = (merge.row, merge.column)
            for coordinate in region:
                owners[coordinate] = anchor
            for row, column in region - {anchor}:
                cell = self.rows[row][column]
                if cell.model_dump(exclude_defaults=True):
                    raise ValueError(
                        "merge covered cells must be empty and unformatted"
                    )
        return owners

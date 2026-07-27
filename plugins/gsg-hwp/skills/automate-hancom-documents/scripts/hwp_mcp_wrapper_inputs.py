from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from hwp_color_normalization import ColorInput
from hwp_live_contract import LayoutBlock, LayoutPlan
from hwp_live_values import Alignment, ContractModel, Rgb, RgbObject
from hwp_operation_contract import HwpOperateAssets, OperationInputValue
from hwp_priority_recipe_contract import RecipePosition


type HwpStyleId = Annotated[int, Field(ge=0, le=4_095)]
type HwpVerticalAlignment = Literal["inherit", "top", "center", "bottom"]
type HwpBorderStyle = Literal[
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
type HwpBorderWidth = Literal[
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


class HwpWrapperInputError(ValueError):
    pass


HwpRgbObject = RgbObject
type HwpRgbObjectValue = ColorInput
type HwpRgb = ColorInput


def _rgb_parameter(value: Rgb) -> str:
    return ",".join(str(channel) for channel in value)


class HwpTableImages(ContractModel):
    images: dict[str, Path] = Field(min_length=1, max_length=20_000)

    def to_assets(self) -> HwpOperateAssets:
        return HwpOperateAssets(images=self.images)


class HwpCaptionInput(ContractModel):
    caption_text: str = Field(min_length=1, max_length=2_000)
    caption_style_id: int = Field(default=0, ge=0, le=4_095)


class HwpAppendLayout(ContractModel):
    blocks: tuple[LayoutBlock, ...] = Field(min_length=1, max_length=100)

    def to_layout(self) -> LayoutPlan:
        return LayoutPlan(target="document_end", blocks=self.blocks)


class HwpStyleCopyInput(ContractModel):
    source_position: RecipePosition
    target_position: RecipePosition
    style_copy_type: Literal[0, 1, 2] = Field(
        default=2,
        description="0은 글자 모양, 1은 문단 모양, 2는 둘 다 복사합니다.",
    )


class HwpTextFormatFields(ContractModel):
    bold: bool | None = None
    font_name: str | None = Field(default=None, min_length=1, max_length=100)
    font_size_pt: int | None = Field(default=None, ge=1, le=96)
    text_color: HwpRgb | None = None
    alignment: Alignment | None = None
    line_spacing: int | None = Field(default=None, ge=50, le=500)

    def text_parameters(self) -> Mapping[str, OperationInputValue]:
        parameters: dict[str, OperationInputValue] = {}
        if self.bold is not None:
            parameters["bold"] = self.bold
        if self.font_name is not None:
            parameters["font_name"] = self.font_name
        if self.font_size_pt is not None:
            parameters["font_size_pt"] = self.font_size_pt
        if self.text_color is not None:
            parameters["text_color"] = _rgb_parameter(self.text_color)
        if self.alignment is not None:
            parameters["alignment"] = self.alignment
        if self.line_spacing is not None:
            parameters["line_spacing"] = self.line_spacing
        return parameters


class HwpTextFormatting(HwpTextFormatFields):
    @model_validator(mode="after")
    def require_format(self) -> HwpTextFormatting:
        if not self.text_parameters():
            raise HwpWrapperInputError("at least one text format field is required")
        return self


class HwpTableFormatting(HwpTextFormatFields):
    cell: str = Field(pattern=r"^[A-Za-z]+[1-9][0-9]*$")
    vertical_alignment: HwpVerticalAlignment | None = None
    fill_color: HwpRgb | None = None
    border_style: HwpBorderStyle | None = None
    border_width: HwpBorderWidth | None = None
    border_color: HwpRgb | None = None

    @model_validator(mode="after")
    def require_format(self) -> HwpTableFormatting:
        if not self.text_parameters() and all(
            value is None
            for value in (
                self.vertical_alignment,
                self.fill_color,
                self.border_style,
                self.border_width,
                self.border_color,
            )
        ):
            raise HwpWrapperInputError("at least one table format field is required")
        return self

    def to_parameters(self) -> Mapping[str, OperationInputValue]:
        parameters = dict(self.text_parameters())
        parameters["cell"] = self.cell.upper()
        if self.vertical_alignment is not None:
            parameters["vertical_alignment"] = self.vertical_alignment
        if self.fill_color is not None:
            parameters["fill_color"] = _rgb_parameter(self.fill_color)
        if self.border_style is not None:
            parameters["border_style"] = self.border_style
        if self.border_width is not None:
            parameters["border_width"] = self.border_width
        if self.border_color is not None:
            parameters["border_color"] = _rgb_parameter(self.border_color)
        return parameters


class HwpCellRange(ContractModel):
    start: str = Field(pattern=r"^[A-Za-z]+[1-9][0-9]*$")
    end: str = Field(pattern=r"^[A-Za-z]+[1-9][0-9]*$")

    def to_parameters(self) -> Mapping[str, OperationInputValue]:
        return {"start": self.start.upper(), "end": self.end.upper()}


class HwpCellSplit(ContractModel):
    cell: str = Field(pattern=r"^[A-Za-z]+[1-9][0-9]*$")
    columns: int = Field(ge=1, le=65_535)
    rows: int = Field(ge=1, le=65_535)
    distribute_height: bool = False
    merge: bool = False
    split_mode: Literal["equal", "existing_grid"] = "equal"

    def to_parameters(self) -> Mapping[str, OperationInputValue]:
        return {
            "cell": self.cell.upper(),
            "columns": self.columns,
            "rows": self.rows,
            "distribute_height": self.distribute_height,
            "merge": self.merge,
            "split_mode": self.split_mode,
        }

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from hwp_live_values import ContractModel
from hwp_mcp_wrapper_inputs import HwpBorderStyle, HwpBorderWidth
from hwp_operation_contract import OperationInputValue
from hwp_public_cell_selector import PublicCellAddress as PublicCellAddress


type PublicTableAlignment = Literal["left", "center", "right", "justify", "inherit"]
type PublicTableVerticalAlignment = Literal["top", "center", "bottom", "inherit"]
type PublicTableColor = Annotated[str, Field(min_length=1, max_length=50)]
type PublicTableBorderStyle = HwpBorderStyle
type PublicTableBorderWidth = HwpBorderWidth


class PublicTableFormattingInput(ContractModel):
    cell: PublicCellAddress
    row_height_mm: float | None = Field(default=None, ge=1, le=250)
    column_width_mm: float | None = Field(default=None, ge=1, le=250)
    bold: bool | None = None
    font_name: str | None = Field(default=None, min_length=1, max_length=100)
    font_size_pt: float | None = Field(default=None, ge=1, le=96)
    text_color: PublicTableColor | None = None
    alignment: PublicTableAlignment = "inherit"
    vertical_alignment: PublicTableVerticalAlignment = "inherit"
    line_spacing: int | None = Field(default=None, ge=50, le=500)
    fill_color: PublicTableColor | None = None
    border_style: PublicTableBorderStyle | None = None
    border_width: PublicTableBorderWidth | None = None
    border_color: PublicTableColor | None = None

    def to_parameters(self) -> Mapping[str, OperationInputValue]:
        parameters: dict[str, OperationInputValue] = {
            "cell": self.cell.upper(),
            "alignment": self.alignment,
            "vertical_alignment": self.vertical_alignment,
        }
        if self.row_height_mm is not None:
            parameters["row_height_mm"] = self.row_height_mm
        if self.column_width_mm is not None:
            parameters["column_width_mm"] = self.column_width_mm
        if self.bold is not None:
            parameters["bold"] = self.bold
        if self.font_name is not None:
            parameters["font_name"] = self.font_name
        if self.font_size_pt is not None:
            parameters["font_size_pt"] = self.font_size_pt
        if self.text_color is not None:
            parameters["text_color"] = self.text_color
        if self.line_spacing is not None:
            parameters["line_spacing"] = self.line_spacing
        if self.fill_color is not None:
            parameters["fill_color"] = self.fill_color
        if self.border_style is not None:
            parameters["border_style"] = self.border_style
        if self.border_width is not None:
            parameters["border_width"] = self.border_width
        if self.border_color is not None:
            parameters["border_color"] = self.border_color
        return parameters


class PublicMergeTableCellsInput(ContractModel):
    start_cell: PublicCellAddress
    end_cell: PublicCellAddress

    @model_validator(mode="after")
    def require_distinct_cells(self) -> PublicMergeTableCellsInput:
        if self.start_cell.casefold() == self.end_cell.casefold():
            raise PydanticCustomError(
                "public_merge_single_cell",
                "병합 시작 셀과 끝 셀은 달라야 합니다",
            )
        return self

    def to_parameters(self) -> Mapping[str, OperationInputValue]:
        return {"start": self.start_cell.upper(), "end": self.end_cell.upper()}


class PublicSplitTableCellInput(ContractModel):
    cell: PublicCellAddress
    columns: int = Field(ge=1, le=65_535)
    rows: int = Field(ge=1, le=65_535)
    distribute_height: bool = False

    @model_validator(mode="after")
    def require_meaningful_split(self) -> PublicSplitTableCellInput:
        if self.columns == 1 and self.rows == 1:
            raise PydanticCustomError(
                "public_split_one_by_one",
                "셀 분할은 칸 또는 줄 수가 2 이상이어야 합니다",
            )
        return self

    def to_parameters(self) -> Mapping[str, OperationInputValue]:
        return {
            "cell": self.cell.upper(),
            "columns": self.columns,
            "rows": self.rows,
            "distribute_height": self.distribute_height,
            "merge": False,
            "mode2": False,
        }

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from hwp_live_values import ContractModel


class StructurePosition(ContractModel):
    list_id: int
    paragraph: int
    character: int


class FastParagraphFormat(ContractModel):
    alignment: int
    line_spacing: int
    left_margin_hwpunit: int
    right_margin_hwpunit: int
    indentation_hwpunit: int
    previous_spacing_hwpunit: int
    next_spacing_hwpunit: int


class FastPageControl(ContractModel):
    control_type: str = Field(max_length=20)
    instance_id: str = Field(max_length=100)
    anchor: StructurePosition
    rows: int | None = Field(default=None, ge=1)
    columns: int | None = Field(default=None, ge=1)
    width_hwpunit: int | None = Field(default=None, ge=0)
    height_hwpunit: int | None = Field(default=None, ge=0)
    width_mm: float | None = Field(default=None, ge=0)
    height_mm: float | None = Field(default=None, ge=0)
    anchor_style_id: int | None = None
    anchor_paragraph_format: FastParagraphFormat | None = None
    parent_table_instance_id: str | None = Field(default=None, max_length=100)
    parent_cell_address: str | None = Field(
        default=None,
        pattern=r"^[A-Z]+[1-9][0-9]*$",
    )


class CellBorderObservation(ContractModel):
    """One side of one cell as the open document carries it.

    ``None`` is "the bridge did not report it" (older bridge, or a property
    this HWP build does not expose), never "there is no border": HWP's own
    "no line" is ``line_type`` 0 and is a real observation.
    """

    line_type: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    color: int | None = Field(default=None, ge=0)


class CellFormatObservation(ContractModel):
    """How some sampled cells of an existing table actually look.

    Read from the document, not derived. The sample is chosen by position
    alone -- first row first column, first row last column, second row first
    column, last row first column -- so nothing here asserts which row is a
    header. ``addresses`` lists the sampled cells that reported these exact
    values; they share a record only because the document gave them the same
    numbers. Compare the addresses and decide.

    **A cell that is not listed was not looked at.** At most four cells of a
    table are sampled, so a table whose third row is shaded reports nothing
    about that row -- absence here is not evidence of a plain cell. A live run
    read a table whose A1/A3/A5 are all grey and returned records for A1 and
    A2 only; A3 and A5 were never sampled. Read ``addresses`` as the whole of
    what was measured.
    """

    addresses: tuple[str, ...] = Field(min_length=1)
    fill_color: int | None = Field(default=None, ge=0)
    fill_brush: int | None = Field(default=None, ge=0)
    border_left: CellBorderObservation = CellBorderObservation()
    border_right: CellBorderObservation = CellBorderObservation()
    border_top: CellBorderObservation = CellBorderObservation()
    border_bottom: CellBorderObservation = CellBorderObservation()
    margin_left_hwpunit: int | None = Field(default=None, ge=0)
    margin_right_hwpunit: int | None = Field(default=None, ge=0)
    margin_top_hwpunit: int | None = Field(default=None, ge=0)
    margin_bottom_hwpunit: int | None = Field(default=None, ge=0)
    vertical_align: int | None = Field(default=None, ge=0)
    alignment: int | None = Field(default=None, ge=0)
    face_name: str | None = Field(default=None, max_length=100)
    character_height: int | None = Field(default=None, ge=0)
    bold: bool | None = None


class FastPageCellFormat(CellFormatObservation):
    table_instance_id: str = Field(max_length=100)


class FastPageCell(ContractModel):
    table_instance_id: str = Field(max_length=100)
    address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    list_id: int
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    text: str = Field(max_length=200_000)
    width_hwpunit: int | None = Field(default=None, ge=0)
    height_hwpunit: int | None = Field(default=None, ge=0)
    width_mm: float | None = Field(default=None, ge=0)
    height_mm: float | None = Field(default=None, ge=0)


class FastControlInspectionError(ContractModel):
    control_instance_id: str = Field(max_length=100)
    code: str = Field(max_length=100)
    message: str = Field(max_length=2_000)


class FastPageInspection(ContractModel):
    document_id: int
    full_name: str = Field(max_length=1_000)
    page: int = Field(ge=1)
    page_count: int = Field(ge=1)
    text: str = Field(max_length=200_000)
    controls: tuple[FastPageControl, ...]
    cells: tuple[FastPageCell, ...] = ()
    inspection_errors: tuple[FastControlInspectionError, ...] = ()
    cell_formats: tuple[FastPageCellFormat, ...] = ()

    @model_validator(mode="after")
    def link_control_cells(self) -> FastPageInspection:
        cells_by_list = {cell.list_id: cell for cell in self.cells}
        controls = tuple(
            control
            if (cell := cells_by_list.get(control.anchor.list_id)) is None
            else control.model_copy(
                update={
                    "parent_table_instance_id": cell.table_instance_id,
                    "parent_cell_address": cell.address,
                }
            )
            for control in self.controls
        )
        object.__setattr__(self, "controls", controls)
        return self


class PageCharacterStyle(ContractModel):
    face_name: str | None = Field(default=None, max_length=100)
    height_hwpunit: int | None = Field(default=None, ge=0)
    bold: bool | None = None
    text_color: int | None = Field(default=None, ge=0, le=0xFF_FF_FF)


class PageParagraphStyle(ContractModel):
    alignment: int | None = Field(default=None, ge=0)
    line_spacing: int | None = Field(default=None, ge=0)
    left_margin_hwpunit: int | None = None
    right_margin_hwpunit: int | None = None
    indentation_hwpunit: int | None = None
    previous_spacing_hwpunit: int | None = None
    next_spacing_hwpunit: int | None = None
    heading_type: int | None = Field(default=None, ge=0, le=3)
    heading_level: int | None = Field(default=None, ge=0, le=6)


class PageParagraph(ContractModel):
    index: int = Field(ge=0)
    text: str = Field(max_length=200_000)
    position: StructurePosition | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    text_available: bool = False
    style_id: int | None = Field(default=None, ge=0, le=4095)
    character_style: PageCharacterStyle = PageCharacterStyle()
    paragraph_style: PageParagraphStyle = PageParagraphStyle()


class UnsupportedStructureRecord(ContractModel):
    record_type: str = Field(min_length=1, max_length=100)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StructureCaption(ContractModel):
    text: str = Field(max_length=2_000)
    automatic_number: bool
    style_id: int | None = Field(default=None, ge=0, le=4095)
    style_name: str | None = Field(default=None, max_length=100)


class StructureCell(ContractModel):
    address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    owner_address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    text: str = Field(max_length=200_000)
    width_hwpunit: int | None = Field(default=None, ge=0)
    height_hwpunit: int | None = Field(default=None, ge=0)
    width_mm: float | None = Field(default=None, ge=0)
    height_mm: float | None = Field(default=None, ge=0)
    has_picture: bool = False
    has_nested_table: bool = False


class StructureMerge(ContractModel):
    owner_address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)


class StructureTable(ContractModel):
    table_ref: str = Field(min_length=16, max_length=80)
    control_instance_id: str | None = Field(default=None, exclude=True, max_length=100)
    anchor: StructurePosition
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    merges: tuple[StructureMerge, ...]
    cells: tuple[StructureCell, ...]
    caption: StructureCaption | None = None
    preceding_paragraph: StructurePosition | None = None
    following_paragraph: StructurePosition | None = None
    # At most four cells of this table, with the appearance they actually
    # carry. A cell missing from here was not sampled, not proven plain --
    # see CellFormatObservation. Empty when the bridge reports no cell
    # appearance at all; when it tried and could not, the table carries a
    # CELL_FORMAT inspection error instead of falling silent.
    cell_formats: tuple[CellFormatObservation, ...] = ()


ControlKind = Literal["table", "picture", "shape", "unknown"]


class StructureControl(ContractModel):
    control_ref: str = Field(min_length=16, max_length=80)
    ctrl_id: str = Field(min_length=1, max_length=20)
    kind: ControlKind
    user_description: str = Field(max_length=100)
    anchor: StructurePosition
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    width_hwpunit: int | None = Field(default=None, ge=0)
    height_hwpunit: int | None = Field(default=None, ge=0)
    width_mm: float | None = Field(default=None, ge=0)
    height_mm: float | None = Field(default=None, ge=0)
    preceding_paragraph: StructurePosition | None = None
    following_paragraph: StructurePosition | None = None


class DocumentStructure(ContractModel):
    selector: str = Field(min_length=1, max_length=500)
    document_id: int
    full_name: str = Field(max_length=1_000)
    window_handle: int = Field(ge=1)
    page: int = Field(ge=1)
    page_count: int = Field(ge=1)
    state_token: str = Field(min_length=16, max_length=80)
    page_text: str = Field(max_length=200_000)
    paragraphs: tuple[PageParagraph, ...]
    paragraphs_complete: bool = False
    paragraph_scan_error: str | None = Field(default=None, max_length=2_000)
    tables_complete: bool = True
    table_scan_error: str | None = Field(default=None, max_length=2_000)
    unsupported_records: tuple[UnsupportedStructureRecord, ...] = ()
    controls: tuple[StructureControl, ...]
    tables: tuple[StructureTable, ...]


class TableCellUpdate(ContractModel):
    address: str
    expected_text: str = Field(max_length=200_000)
    replacement: str = Field(max_length=200_000)

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        normalized = value.strip().upper()
        if re.fullmatch(r"[A-Z]+[1-9][0-9]*", normalized) is None:
            raise ValueError("address must be an HWP cell address such as B3")
        return normalized


class TableUpdateResult(ContractModel):
    table_ref: str = Field(min_length=16, max_length=80)
    state_token: str = Field(min_length=16, max_length=80)
    current_page: int = Field(ge=1)
    modified: bool
    updated_addresses: tuple[str, ...]
    table: StructureTable
    execution_mode: Literal["native_in_process"] = "native_in_process"
    native_elapsed_microseconds: int = Field(ge=0)


class TableImageUpdate(ContractModel):
    address: str = Field(max_length=20)
    expected_text: str = Field(max_length=200_000)
    path: Path

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        normalized = value.strip().upper()
        if re.fullmatch(r"[A-Z]+[1-9][0-9]*", normalized) is None:
            raise ValueError("address must be an HWP cell address such as B3")
        return normalized


class TableImageResult(ContractModel):
    table_ref: str = Field(min_length=16, max_length=80)
    state_token: str = Field(min_length=16, max_length=80)
    current_page: int = Field(ge=1)
    modified: bool
    inserted_addresses: tuple[str, ...]
    inserted_paths: tuple[Path, ...]
    execution_mode: Literal["native_in_process"] = "native_in_process"
    native_elapsed_microseconds: int = Field(ge=0)
    table: StructureTable


class CreatedControl(ContractModel):
    control_ref: str = Field(min_length=16, max_length=80)
    ctrl_id: str = Field(min_length=1, max_length=20)
    kind: ControlKind
    anchor: StructurePosition
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    table_ref: str | None = Field(default=None, min_length=16, max_length=80)

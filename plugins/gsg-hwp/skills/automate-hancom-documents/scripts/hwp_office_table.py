from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from hwp_errors import HwpLiveError
from hwp_live_structure_contract import StructureCell, StructureTable, TableCellUpdate
from hwp_live_values import ContractModel
from hwp_office_pptx import read_pptx
from hwp_office_excel_com import read_excel_matrix
from hwp_office_xlsx import read_xlsx


class OfficeTableSource(ContractModel):
    path: Path
    sheet_name: str | None = Field(default=None, min_length=1, max_length=200)
    sheet_index: int = Field(default=0, ge=0, le=1023)
    table_index: int = Field(default=0, ge=0, le=4095)
    cell_range: str | None = Field(default=None, min_length=2, max_length=40)

    @field_validator("cell_range")
    @classmethod
    def normalize_range(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @model_validator(mode="after")
    def validate_options(self) -> OfficeTableSource:
        suffix = self.path.suffix.casefold()
        if suffix == ".pptx" and (self.sheet_name is not None or self.cell_range is not None):
            raise ValueError("PPTX source does not use sheet_name or cell_range")
        if suffix in {".xls", ".xlsx"} and self.table_index != 0:
            raise ValueError("Excel source does not use table_index")
        if suffix not in {".pptx", ".xls", ".xlsx"}:
            raise ValueError("source path must end with .pptx, .xls, or .xlsx")
        return self


def read_office_table(source: OfficeTableSource) -> tuple[tuple[str, ...], ...]:
    path = source.path.expanduser().resolve()
    if not path.is_file():
        raise HwpLiveError(f"PPTX/Excel 입력 파일이 없습니다: {path}")
    if path.suffix.casefold() == ".xlsx":
        return read_xlsx(
            path,
            sheet_name=source.sheet_name,
            sheet_index=source.sheet_index,
            cell_range=source.cell_range,
        )
    if path.suffix.casefold() == ".xls":
        return read_excel_matrix(
            path,
            sheet_name=source.sheet_name,
            sheet_index=source.sheet_index,
            cell_range=source.cell_range,
        )
    return read_pptx(path, table_index=source.table_index)


def _coordinate(address: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", address.strip().upper())
    if match is None:
        raise HwpLiveError("한컴 표 시작 셀은 B3 같은 주소여야 합니다")
    column = 0
    for character in match.group(1):
        column = column * 26 + ord(character) - ord("A") + 1
    return int(match.group(2)) - 1, column - 1


def _address(row: int, column: int) -> str:
    letters = ""
    current = column + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{row + 1}"


def _target_cell(table: StructureTable, address: str) -> StructureCell:
    cell = next((item for item in table.cells if item.address == address), None)
    if cell is None:
        raise HwpLiveError(f"Office 표 값이 한컴 표 범위를 벗어났습니다: {address}")
    if cell.owner_address != cell.address:
        raise HwpLiveError(f"Office 표 값 대상 {address}은 병합된 {cell.owner_address} 셀입니다")
    if cell.has_picture or cell.has_nested_table:
        raise HwpLiveError(f"Office 표 값 대상 {address} 셀에 개체가 있습니다")
    return cell


def table_updates_from_matrix(
    table: StructureTable,
    matrix: tuple[tuple[str, ...], ...],
    *,
    target_start: str,
) -> tuple[TableCellUpdate, ...]:
    if not matrix or not matrix[0] or any(len(row) != len(matrix[0]) for row in matrix):
        raise HwpLiveError("PPTX/XLSX 표가 비어 있거나 직사각형이 아닙니다")
    start_row, start_column = _coordinate(target_start)
    updates: list[TableCellUpdate] = []
    for row_offset, row in enumerate(matrix):
        for column_offset, value in enumerate(row):
            address = _address(start_row + row_offset, start_column + column_offset)
            cell = _target_cell(table, address)
            updates.append(
                TableCellUpdate(
                    address=address,
                    expected_text=cell.text,
                    replacement=value,
                )
            )
    return tuple(updates)

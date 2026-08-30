from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hwp_live_structure_contract import StructureCell, StructureTable
from hwp_public_cell_selector import PublicCellSelector


_ADDRESS = re.compile(r"^[A-Z]+[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class TablePlanInputFailure:
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class RequestedImage:
    key: str
    path: Path


@dataclass(frozen=True, slots=True)
class MappedTableImages:
    images: dict[str, Path]


@dataclass(frozen=True, slots=True)
class MappedTableCells:
    by_key: dict[str, StructureCell]


def map_table_images(
    table: StructureTable,
    requested: tuple[RequestedImage, ...],
) -> MappedTableImages | TablePlanInputFailure:
    mapped_result = map_table_cells(table, tuple(item.key for item in requested))
    if isinstance(mapped_result, TablePlanInputFailure):
        return prefix_failure(mapped_result, "images")
    return MappedTableImages(
        images={mapped_result.by_key[item.key].address: item.path for item in requested}
    )


def map_table_cells(
    table: StructureTable,
    keys: tuple[str, ...],
) -> MappedTableCells | TablePlanInputFailure:
    by_address = {cell.address.upper(): cell for cell in table.cells}
    owners = tuple(cell for cell in table.cells if cell.owner_address == cell.address)
    mapped: dict[str, StructureCell] = {}
    for key in keys:
        normalized = normalize_cell_label(key)
        address = normalized.upper()
        address_shaped = _ADDRESS.fullmatch(address) is not None
        if address_shaped:
            cell = by_address.get(address)
            if cell is not None:
                owner = by_address.get(cell.owner_address)
                if owner is None:
                    return TablePlanInputFailure(
                        key,
                        f"{address} 셀의 병합 소유 셀을 찾지 못했습니다",
                    )
                mapped[key] = owner
                continue
        matches = tuple(
            cell
            for cell in sorted(owners, key=lambda owner: (owner.row, owner.column))
            if normalize_cell_label(cell.text) == normalized
        )
        if len(matches) != 1:
            return TablePlanInputFailure(
                key,
                f"머리글 {key!r}을 고유한 셀 하나로 찾지 못했습니다",
            )
        mapped[key] = matches[0]
    return MappedTableCells(mapped)


def select_table_cell(
    table: StructureTable,
    selector: PublicCellSelector,
) -> StructureCell | TablePlanInputFailure:
    by_address = {cell.address.upper(): cell for cell in table.cells}
    by_position = {(cell.row, cell.column): cell for cell in table.cells}
    owners = tuple(
        sorted(
            (cell for cell in table.cells if cell.owner_address == cell.address),
            key=lambda cell: (cell.row, cell.column),
        )
    )
    if selector.address is not None:
        anchor = by_address.get(selector.address)
        if anchor is None:
            return TablePlanInputFailure(
                "address",
                f"대상 표에 {selector.address} 셀이 없습니다",
            )
    else:
        assert selector.label is not None
        normalized = normalize_cell_label(selector.label)
        matches = tuple(
            cell for cell in owners if normalize_cell_label(cell.text) == normalized
        )
        if selector.occurrence > len(matches):
            return TablePlanInputFailure(
                "occurrence",
                (
                    f"라벨 {selector.label!r}은 {len(matches)}개뿐이라 "
                    f"{selector.occurrence}번째 셀을 선택할 수 없습니다"
                ),
            )
        anchor = matches[selector.occurrence - 1]
    row = anchor.row + selector.row_offset
    column = anchor.column + selector.column_offset
    if not 0 <= row < table.rows:
        return TablePlanInputFailure(
            "row_offset",
            f"대상 행 {row + 1}이 표 범위를 벗어납니다",
        )
    if not 0 <= column < table.columns:
        return TablePlanInputFailure(
            "column_offset",
            f"대상 열 {column + 1}이 표 범위를 벗어납니다",
        )
    cell = by_position.get((row, column))
    if cell is None:
        return TablePlanInputFailure(
            "row_offset",
            f"대상 위치 ({row + 1}, {column + 1})의 셀 구조가 없습니다",
        )
    owner = by_address.get(cell.owner_address)
    if owner is None:
        return TablePlanInputFailure(
            "address",
            f"{cell.address} 셀의 병합 소유 셀을 찾지 못했습니다",
        )
    return owner


def normalize_cell_label(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def prefix_failure(
    failure: TablePlanInputFailure,
    prefix: str,
) -> TablePlanInputFailure:
    return TablePlanInputFailure(f"{prefix}.{failure.field}", failure.message)

from __future__ import annotations

import re
from collections.abc import Callable

from hwp_live_structure_contract import StructureCell, StructureTable
from hwp_visibility_series_contract import VisibilitySeriesPlanError


def compact_cell_text(value: str) -> str:
    return re.sub(r"[\s()]+", "", value).casefold()


def owner_cells(table: StructureTable) -> tuple[StructureCell, ...]:
    return tuple(cell for cell in table.cells if cell.address == cell.owner_address)


def required_cell(
    cells: tuple[StructureCell, ...],
    label: str,
    predicate: Callable[[StructureCell], bool],
) -> StructureCell:
    matches = tuple(cell for cell in cells if predicate(cell))
    if len(matches) != 1:
        raise VisibilitySeriesPlanError(
            f"{label} 셀은 하나여야 합니다: {tuple(cell.address for cell in matches)}"
        )
    return matches[0]


def logical_cells(table: StructureTable) -> dict[tuple[int, int], StructureCell]:
    owners = {cell.address: cell for cell in owner_cells(table)}
    values: dict[tuple[int, int], StructureCell] = {}
    for cell in table.cells:
        owner = owners.get(cell.owner_address)
        if owner is None:
            raise VisibilitySeriesPlanError(
                f"{cell.address} 셀의 병합 소유자를 찾지 못했습니다"
            )
        values[(cell.row, cell.column)] = owner
    if len(values) != len(table.cells):
        raise VisibilitySeriesPlanError("표의 논리 셀 좌표가 중복됩니다")
    return values


def logical_value(
    cells: dict[tuple[int, int], StructureCell],
    row: int,
    column: int,
    label: str,
) -> str:
    cell = cells.get((row, column))
    if cell is None or not cell.text.strip():
        raise VisibilitySeriesPlanError(f"{row + 1}행의 {label} 값이 비어 있습니다")
    return cell.text.strip()


def cell_at(
    cells: dict[tuple[int, int], StructureCell],
    row: int,
    column: int,
    label: str,
) -> StructureCell:
    cell = cells.get((row, column))
    if cell is None:
        raise VisibilitySeriesPlanError(f"템플릿의 {label} 셀을 찾지 못했습니다")
    return cell

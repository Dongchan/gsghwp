from __future__ import annotations

from typing import Literal

from hwp_live_table_contract import CellBorders, TableBlock, TableCell


type _BorderEdge = Literal["left", "right", "top", "bottom"]
type _Coordinate = tuple[int, int]


def _merge_owners(block: TableBlock) -> dict[_Coordinate, _Coordinate]:
    owners: dict[_Coordinate, _Coordinate] = {}
    for merge in block.merges:
        anchor = (merge.row, merge.column)
        for row in range(merge.row, merge.row + merge.row_span):
            for column in range(merge.column, merge.column + merge.column_span):
                owners[(row, column)] = anchor
    return owners


def _edge(cell: TableCell, edge: _BorderEdge) -> object | None:
    borders = cell.borders
    return None if borders is None else getattr(borders, edge)


def _inherit_edge(
    updates: dict[_Coordinate, set[_BorderEdge]],
    coordinate: _Coordinate,
    edge: _BorderEdge,
) -> None:
    updates.setdefault(coordinate, set()).add(edge)


def _normalized_borders(
    borders: CellBorders,
    inherited: set[_BorderEdge],
) -> CellBorders | None:
    resolved = borders.model_copy(update={edge: None for edge in inherited})
    if all(
        getattr(resolved, edge) is None
        for edge in ("left", "right", "top", "bottom")
    ):
        return None
    return resolved


def inherit_conflicting_shared_borders(block: TableBlock) -> TableBlock:
    owners = _merge_owners(block)
    updates: dict[_Coordinate, set[_BorderEdge]] = {}

    def owner(row: int, column: int) -> _Coordinate:
        return owners.get((row, column), (row, column))

    columns = len(block.rows[0])
    for row in range(len(block.rows)):
        for column in range(columns - 1):
            left = owner(row, column)
            right = owner(row, column + 1)
            if left == right:
                continue
            left_line = _edge(block.rows[left[0]][left[1]], "right")
            right_line = _edge(block.rows[right[0]][right[1]], "left")
            if left_line is not None and right_line is not None and left_line != right_line:
                _inherit_edge(updates, left, "right")
                _inherit_edge(updates, right, "left")

    for row in range(len(block.rows) - 1):
        for column in range(columns):
            top = owner(row, column)
            bottom = owner(row + 1, column)
            if top == bottom:
                continue
            top_line = _edge(block.rows[top[0]][top[1]], "bottom")
            bottom_line = _edge(block.rows[bottom[0]][bottom[1]], "top")
            if top_line is not None and bottom_line is not None and top_line != bottom_line:
                _inherit_edge(updates, top, "bottom")
                _inherit_edge(updates, bottom, "top")

    if not updates:
        return block
    rows = [list(row) for row in block.rows]
    for (row, column), inherited in updates.items():
        cell = rows[row][column]
        if cell.borders is None:
            continue
        rows[row][column] = cell.model_copy(
            update={"borders": _normalized_borders(cell.borders, inherited)}
        )
    return block.model_copy(update={"rows": tuple(tuple(row) for row in rows)})

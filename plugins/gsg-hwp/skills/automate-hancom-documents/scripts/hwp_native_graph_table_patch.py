from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Final

_ADDRESS_LETTERS: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class TablePatchKind(IntEnum):
    CREATE = 0
    DELETE = 1
    CLONE = 2
    INSERT_ROW = 3
    DELETE_ROW = 4
    INSERT_COLUMN = 5
    DELETE_COLUMN = 6
    SET_CELL_TEXT = 7
    SET_CELL_PROPERTY = 8
    RESIZE = 9
    SELECT = 10
    MERGE = 11
    SPLIT = 12
    SET_CAPTION = 13
    NEST = 14


class TablePatchError(ValueError):
    code: str

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CellSnapshot:
    address: str
    row: int
    column: int
    row_span: int
    column_span: int
    text: bytes
    properties: dict[int, bytes]
    stories: tuple[bytes, ...] = ()
    nested_table: bytes | None = None


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    table_id: bytes
    rows: int
    columns: int
    cells: tuple[CellSnapshot, ...]
    caption: bytes = b""
    selected: str | None = None
    widths: tuple[int, ...] = ()
    heights: tuple[int, ...] = ()

    def cell_map(self) -> dict[str, CellSnapshot]:
        return {cell.address: cell for cell in self.cells}

    def digest_material(self) -> bytes:
        parts = [
            self.table_id,
            self.rows.to_bytes(4, "little"),
            self.columns.to_bytes(4, "little"),
            self.caption,
            (self.selected or "").encode("ascii"),
        ]
        for cell in sorted(self.cells, key=lambda item: (item.row, item.column)):
            parts.append(cell.address.encode("ascii"))
            parts.append(cell.text)
            parts.append(cell.row_span.to_bytes(2, "little"))
            parts.append(cell.column_span.to_bytes(2, "little"))
            for key in sorted(cell.properties):
                parts.append(key.to_bytes(4, "little"))
                parts.append(cell.properties[key])
            parts.append(b"".join(cell.stories))
            if cell.nested_table is not None:
                parts.append(cell.nested_table)
        return b"\0".join(parts)


@dataclass(frozen=True, slots=True)
class TablePatchOp:
    kind: TablePatchKind
    table_id: bytes
    address: str | None = None
    end_address: str | None = None
    index: int | None = None
    count: int = 1
    text: bytes = b""
    property_key: int | None = None
    property_value: bytes = b""
    rows: int | None = None
    columns: int | None = None
    caption: bytes = b""
    nested_table: bytes | None = None
    widths: tuple[int, ...] = ()
    heights: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class TableAction:
    name: str
    kind: TablePatchKind
    table_id: bytes
    before: TableSnapshot
    after: TableSnapshot


@dataclass(frozen=True, slots=True)
class TablePatchPlan:
    actions: tuple[TableAction, ...]
    inverse: tuple[TableAction, ...]
    affected: tuple[bytes, ...]


def cell_address(row: int, column: int) -> str:
    if row < 0 or column < 0:
        raise TablePatchError("TABLE_ADDRESS")
    label = ""
    value = column + 1
    while value:
        value, rem = divmod(value - 1, 26)
        label = _ADDRESS_LETTERS[rem] + label
    return f"{label}{row + 1}"


def parse_address(address: str) -> tuple[int, int]:
    letters = ""
    digits = ""
    for char in address:
        if char.isalpha() and not digits:
            letters += char.upper()
            continue
        if char.isdigit() and letters:
            digits += char
            continue
        raise TablePatchError("TABLE_ADDRESS")
    if not letters or not digits:
        raise TablePatchError("TABLE_ADDRESS")
    column = 0
    for char in letters:
        column = column * 26 + (ord(char) - 64)
    return int(digits) - 1, column - 1


def empty_cell(row: int, column: int) -> CellSnapshot:
    return CellSnapshot(
        address=cell_address(row, column),
        row=row,
        column=column,
        row_span=1,
        column_span=1,
        text=b"",
        properties={},
    )


def create_table(table_id: bytes, rows: int, columns: int) -> TableSnapshot:
    if rows <= 0 or columns <= 0:
        raise TablePatchError("TABLE_TOPOLOGY")
    cells = tuple(
        empty_cell(row, column) for row in range(rows) for column in range(columns)
    )
    return TableSnapshot(
        table_id=table_id,
        rows=rows,
        columns=columns,
        cells=cells,
        widths=tuple(1000 for _ in range(columns)),
        heights=tuple(400 for _ in range(rows)),
    )


def _require_bounds(table: TableSnapshot, row: int, column: int) -> None:
    if not (0 <= row < table.rows and 0 <= column < table.columns):
        raise TablePatchError("TABLE_TOPOLOGY")


def _owner_at(table: TableSnapshot, row: int, column: int) -> CellSnapshot:
    _require_bounds(table, row, column)
    for cell in table.cells:
        if (
            cell.row <= row < cell.row + cell.row_span
            and cell.column <= column < cell.column + cell.column_span
        ):
            return cell
    raise TablePatchError("TABLE_TOPOLOGY")


def _replace_cells(
    table: TableSnapshot, cells: tuple[CellSnapshot, ...]
) -> TableSnapshot:
    return replace(
        table, cells=tuple(sorted(cells, key=lambda item: (item.row, item.column)))
    )


def _merge(table: TableSnapshot, start: str, end: str) -> TableSnapshot:
    start_row, start_col = parse_address(start)
    end_row, end_col = parse_address(end)
    top, left = min(start_row, end_row), min(start_col, end_col)
    bottom, right = max(start_row, end_row), max(start_col, end_col)
    _require_bounds(table, top, left)
    _require_bounds(table, bottom, right)
    covered = [
        cell
        for cell in table.cells
        if not (
            cell.row > bottom
            or cell.row + cell.row_span - 1 < top
            or cell.column > right
            or cell.column + cell.column_span - 1 < left
        )
    ]
    if not covered:
        raise TablePatchError("TABLE_TOPOLOGY")
    texts = [cell.text for cell in covered if cell.text]
    if len(texts) > 1:
        raise TablePatchError("TABLE_MERGE_TEXT")
    properties: dict[int, bytes] = {}
    for cell in covered:
        properties.update(cell.properties)
    stories = tuple(story for cell in covered for story in cell.stories)
    nested = next((cell.nested_table for cell in covered if cell.nested_table), None)
    merged = CellSnapshot(
        address=cell_address(top, left),
        row=top,
        column=left,
        row_span=bottom - top + 1,
        column_span=right - left + 1,
        text=texts[0] if texts else b"",
        properties=properties,
        stories=stories,
        nested_table=nested,
    )
    remaining = tuple(cell for cell in table.cells if cell not in covered)
    return _replace_cells(table, remaining + (merged,))


def _split(table: TableSnapshot, address: str) -> TableSnapshot:
    owner = table.cell_map()[address] if address in table.cell_map() else None
    if owner is None:
        raise TablePatchError("TABLE_TOPOLOGY")
    if owner.row_span == 1 and owner.column_span == 1:
        raise TablePatchError("TABLE_TOPOLOGY")
    restored: list[CellSnapshot] = []
    for row in range(owner.row, owner.row + owner.row_span):
        for column in range(owner.column, owner.column + owner.column_span):
            if row == owner.row and column == owner.column:
                restored.append(
                    replace(
                        owner,
                        row_span=1,
                        column_span=1,
                        address=cell_address(row, column),
                    )
                )
                continue
            restored.append(empty_cell(row, column))
    remaining = tuple(cell for cell in table.cells if cell.address != owner.address)
    return _replace_cells(table, remaining + tuple(restored))


def _insert_row(table: TableSnapshot, index: int) -> TableSnapshot:
    if not 0 <= index <= table.rows:
        raise TablePatchError("TABLE_TOPOLOGY")
    shifted: list[CellSnapshot] = []
    for cell in table.cells:
        if cell.row >= index:
            shifted.append(
                replace(
                    cell,
                    row=cell.row + 1,
                    address=cell_address(cell.row + 1, cell.column),
                )
            )
        else:
            shifted.append(cell)
    inserted = tuple(empty_cell(index, column) for column in range(table.columns))
    return replace(
        table,
        rows=table.rows + 1,
        cells=tuple(
            sorted(shifted + list(inserted), key=lambda item: (item.row, item.column))
        ),
        heights=table.heights[:index] + (400,) + table.heights[index:],
    )


def _delete_row(table: TableSnapshot, index: int) -> TableSnapshot:
    if not 0 <= index < table.rows:
        raise TablePatchError("TABLE_TOPOLOGY")
    remaining: list[CellSnapshot] = []
    for cell in table.cells:
        if cell.row <= index < cell.row + cell.row_span and cell.row_span > 1:
            remaining.append(replace(cell, row_span=cell.row_span - 1))
            continue
        if cell.row == index:
            continue
        if cell.row > index:
            remaining.append(
                replace(
                    cell,
                    row=cell.row - 1,
                    address=cell_address(cell.row - 1, cell.column),
                )
            )
        else:
            remaining.append(cell)
    return replace(
        table,
        rows=table.rows - 1,
        cells=tuple(sorted(remaining, key=lambda item: (item.row, item.column))),
        heights=table.heights[:index] + table.heights[index + 1 :],
    )


def _insert_column(table: TableSnapshot, index: int) -> TableSnapshot:
    if not 0 <= index <= table.columns:
        raise TablePatchError("TABLE_TOPOLOGY")
    shifted: list[CellSnapshot] = []
    for cell in table.cells:
        if cell.column >= index:
            shifted.append(
                replace(
                    cell,
                    column=cell.column + 1,
                    address=cell_address(cell.row, cell.column + 1),
                )
            )
        else:
            shifted.append(cell)
    inserted = tuple(empty_cell(row, index) for row in range(table.rows))
    return replace(
        table,
        columns=table.columns + 1,
        cells=tuple(
            sorted(shifted + list(inserted), key=lambda item: (item.row, item.column))
        ),
        widths=table.widths[:index] + (1000,) + table.widths[index:],
    )


def _delete_column(table: TableSnapshot, index: int) -> TableSnapshot:
    if not 0 <= index < table.columns:
        raise TablePatchError("TABLE_TOPOLOGY")
    remaining: list[CellSnapshot] = []
    for cell in table.cells:
        if (
            cell.column <= index < cell.column + cell.column_span
            and cell.column_span > 1
        ):
            remaining.append(replace(cell, column_span=cell.column_span - 1))
            continue
        if cell.column == index:
            continue
        if cell.column > index:
            remaining.append(
                replace(
                    cell,
                    column=cell.column - 1,
                    address=cell_address(cell.row, cell.column - 1),
                )
            )
        else:
            remaining.append(cell)
    return replace(
        table,
        columns=table.columns - 1,
        cells=tuple(sorted(remaining, key=lambda item: (item.row, item.column))),
        widths=table.widths[:index] + table.widths[index + 1 :],
    )


def apply_table_op(table: TableSnapshot, op: TablePatchOp) -> TableSnapshot:
    if op.table_id != table.table_id:
        raise TablePatchError("TABLE_IDENTITY")
    if op.kind is TablePatchKind.SET_CELL_TEXT:
        if op.address is None:
            raise TablePatchError("TABLE_ADDRESS")
        owner = _owner_at(table, *parse_address(op.address))
        updated = replace(owner, text=op.text)
        rest = tuple(cell for cell in table.cells if cell.address != owner.address)
        return _replace_cells(table, rest + (updated,))
    if op.kind is TablePatchKind.SET_CELL_PROPERTY:
        if op.address is None or op.property_key is None:
            raise TablePatchError("TABLE_ADDRESS")
        owner = _owner_at(table, *parse_address(op.address))
        properties = dict(owner.properties)
        properties[op.property_key] = op.property_value
        updated = replace(owner, properties=properties)
        rest = tuple(cell for cell in table.cells if cell.address != owner.address)
        return _replace_cells(table, rest + (updated,))
    if op.kind is TablePatchKind.SET_CAPTION:
        return replace(table, caption=op.caption)
    if op.kind is TablePatchKind.SELECT:
        if op.address is None:
            raise TablePatchError("TABLE_ADDRESS")
        _ = _owner_at(table, *parse_address(op.address))
        return replace(table, selected=op.address)
    if op.kind is TablePatchKind.RESIZE:
        widths = op.widths or table.widths
        heights = op.heights or table.heights
        if len(widths) != table.columns or len(heights) != table.rows:
            raise TablePatchError("TABLE_TOPOLOGY")
        return replace(table, widths=widths, heights=heights)
    if op.kind is TablePatchKind.MERGE:
        if op.address is None or op.end_address is None:
            raise TablePatchError("TABLE_ADDRESS")
        return _merge(table, op.address, op.end_address)
    if op.kind is TablePatchKind.SPLIT:
        if op.address is None:
            raise TablePatchError("TABLE_ADDRESS")
        return _split(table, op.address)
    if op.kind is TablePatchKind.INSERT_ROW:
        if op.index is None:
            raise TablePatchError("TABLE_TOPOLOGY")
        return _insert_row(table, op.index)
    if op.kind is TablePatchKind.DELETE_ROW:
        if op.index is None:
            raise TablePatchError("TABLE_TOPOLOGY")
        return _delete_row(table, op.index)
    if op.kind is TablePatchKind.INSERT_COLUMN:
        if op.index is None:
            raise TablePatchError("TABLE_TOPOLOGY")
        return _insert_column(table, op.index)
    if op.kind is TablePatchKind.DELETE_COLUMN:
        if op.index is None:
            raise TablePatchError("TABLE_TOPOLOGY")
        return _delete_column(table, op.index)
    if op.kind is TablePatchKind.NEST:
        if op.address is None or op.nested_table is None:
            raise TablePatchError("TABLE_ADDRESS")
        owner = _owner_at(table, *parse_address(op.address))
        updated = replace(owner, nested_table=op.nested_table)
        rest = tuple(cell for cell in table.cells if cell.address != owner.address)
        return _replace_cells(table, rest + (updated,))
    if op.kind is TablePatchKind.CLONE:
        return replace(table, table_id=op.nested_table or table.table_id)
    raise TablePatchError("TABLE_OP")


def compile_table_patch(
    before: TableSnapshot, ops: tuple[TablePatchOp, ...]
) -> TablePatchPlan:
    current = before
    actions: list[TableAction] = []
    for op in ops:
        try:
            nxt = apply_table_op(current, op)
        except TablePatchError:
            raise
        actions.append(
            TableAction(
                name=op.kind.name.title().replace("_", ""),
                kind=op.kind,
                table_id=op.table_id,
                before=current,
                after=nxt,
            )
        )
        current = nxt
    inverse = tuple(
        TableAction(
            name=action.name,
            kind=action.kind,
            table_id=action.table_id,
            before=action.after,
            after=action.before,
        )
        for action in reversed(actions)
    )
    return TablePatchPlan(
        actions=tuple(actions),
        inverse=inverse,
        affected=(before.table_id,),
    )


def apply_table_plan(plan: TablePatchPlan, table: TableSnapshot) -> TableSnapshot:
    current = table
    for action in plan.actions:
        if current.digest_material() != action.before.digest_material():
            raise TablePatchError("TABLE_BEFORE")
        current = action.after
    return current


def rollback_table_plan(plan: TablePatchPlan, table: TableSnapshot) -> TableSnapshot:
    current = table
    for action in plan.inverse:
        if current.digest_material() != action.before.digest_material():
            raise TablePatchError("TABLE_BEFORE")
        current = action.after
    return current


__all__ = [
    "CellSnapshot",
    "TableAction",
    "TablePatchError",
    "TablePatchKind",
    "TablePatchOp",
    "TablePatchPlan",
    "TableSnapshot",
    "apply_table_op",
    "apply_table_plan",
    "cell_address",
    "compile_table_patch",
    "create_table",
    "parse_address",
    "rollback_table_plan",
]

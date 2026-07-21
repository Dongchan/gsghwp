from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from hwp_errors import HwpLiveError
from hwp_live_structure_contract import (
    StructureCaption,
    StructureCell,
    StructureMerge,
)


@dataclass(frozen=True, slots=True)
class ParsedTable:
    rows: int
    columns: int
    cells: tuple[StructureCell, ...]
    merges: tuple[StructureMerge, ...]
    caption: StructureCaption | None


def _name(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1].upper()


def _descendants(element: Element, name: str) -> tuple[Element, ...]:
    return tuple(item for item in element.iter() if _name(item) == name)


def _cell_text(cell: Element) -> str:
    paragraphs: list[str] = []
    for text in _descendants(cell, "TEXT"):
        value = "".join(char.text or "" for char in _descendants(text, "CHAR"))
        paragraphs.append(value)
    return "\r\n".join(paragraphs)


def _address(row: int, column: int) -> str:
    letters = ""
    current = column + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"


def _caption(table: Element) -> StructureCaption | None:
    captions = _descendants(table, "CAPTION")
    if not captions:
        return None
    caption = captions[0]
    text = _cell_text(caption).strip()
    automatic = any(
        _name(item) in {"AUTONUM", "AUTONUMBER"}
        or any("num" in key.casefold() for key in item.attrib)
        for item in caption.iter()
    )
    style_name = next(
        (
            value
            for item in caption.iter()
            for key, value in item.attrib.items()
            if key.casefold() in {"stylename", "style-name"} and value
        ),
        None,
    )
    return StructureCaption(
        text=text,
        automatic_number=automatic,
        style_name=style_name,
    )


def parse_table_hwpml(xml: str) -> ParsedTable:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise HwpLiveError("한컴 표 구조 XML을 해석하지 못했습니다") from error
    table = next((item for item in root.iter() if _name(item) == "TABLE"), None)
    if table is None:
        raise HwpLiveError("선택한 한컴 개체에서 표 구조를 찾지 못했습니다")
    rows = int(table.attrib.get("RowCount", "0"))
    columns = int(table.attrib.get("ColCount", "0"))
    row_elements = tuple(item for item in table if _name(item) == "ROW")
    if not row_elements:
        row_elements = _descendants(table, "ROW")
    if rows < 1:
        rows = len(row_elements)
    if columns < 1:
        columns = max(
            (len(tuple(item for item in row if _name(item) == "CELL")) for row in row_elements),
            default=0,
        )
    if rows < 1 or columns < 1:
        raise HwpLiveError("한컴 표의 행·열 수를 확인하지 못했습니다")

    owners: dict[tuple[int, int], StructureCell] = {}
    merges: list[StructureMerge] = []
    occupied: set[tuple[int, int]] = set()
    for row_index, row in enumerate(row_elements[:rows]):
        column_index = 0
        cells = tuple(item for item in row if _name(item) == "CELL")
        if not cells:
            cells = _descendants(row, "CELL")
        for cell in cells:
            while (row_index, column_index) in occupied and column_index < columns:
                column_index += 1
            if column_index >= columns:
                break
            row_span = int(cell.attrib.get("RowSpan", "1"))
            column_span = int(cell.attrib.get("ColSpan", "1"))
            owner = _address(row_index, column_index)
            owner_cell = StructureCell(
                address=owner,
                row=row_index,
                column=column_index,
                owner_address=owner,
                row_span=row_span,
                column_span=column_span,
                text=_cell_text(cell),
                has_picture=bool(_descendants(cell, "PICTURE")),
                has_nested_table=len(_descendants(cell, "TABLE")) > 0,
            )
            owners[(row_index, column_index)] = owner_cell
            for row_offset in range(row_span):
                for column_offset in range(column_span):
                    occupied.add((row_index + row_offset, column_index + column_offset))
            if row_span > 1 or column_span > 1:
                merges.append(
                    StructureMerge(
                        owner_address=owner,
                        row=row_index,
                        column=column_index,
                        row_span=row_span,
                        column_span=column_span,
                    )
                )
            column_index += column_span

    logical: list[StructureCell] = []
    for row_index in range(rows):
        for column_index in range(columns):
            owner = next(
                (
                    candidate
                    for candidate in owners.values()
                    if candidate.row <= row_index < candidate.row + candidate.row_span
                    and candidate.column <= column_index < candidate.column + candidate.column_span
                ),
                None,
            )
            if owner is None:
                raise HwpLiveError("한컴 표의 병합 셀 구조가 올바르지 않습니다")
            address = _address(row_index, column_index)
            logical.append(
                owner
                if address == owner.address
                else StructureCell(
                    address=address,
                    row=row_index,
                    column=column_index,
                    owner_address=owner.address,
                    text="",
                    has_picture=owner.has_picture,
                    has_nested_table=owner.has_nested_table,
                )
            )
    return ParsedTable(
        rows=rows,
        columns=columns,
        cells=tuple(logical),
        merges=tuple(merges),
        caption=_caption(table),
    )

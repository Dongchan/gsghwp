from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree
from xml.etree.ElementTree import Element
from zipfile import BadZipFile, ZipFile

from hwp_errors import HwpLiveError


def _name(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _slide_number(path: str) -> int:
    match = re.search(r"slide([0-9]+)\.xml$", path)
    return int(match.group(1)) if match is not None else 0


def _cell_text(cell: Element) -> str:
    paragraphs = tuple(item for item in cell.iter() if _name(item) == "p")
    return "\r\n".join(
        "".join(item.text or "" for item in paragraph.iter() if _name(item) == "t")
        for paragraph in paragraphs
    )


def _table(element: Element) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for row in (item for item in element if _name(item) == "tr"):
        cells = tuple(_cell_text(item) for item in row if _name(item) == "tc")
        if cells:
            rows.append(cells)
    if not rows or any(len(row) != len(rows[0]) for row in rows):
        raise HwpLiveError("PPTX 표의 행과 열 구조가 올바르지 않습니다")
    return tuple(rows)


def read_pptx(path: Path, *, table_index: int) -> tuple[tuple[str, ...], ...]:
    try:
        with ZipFile(path) as archive:
            slide_paths = sorted(
                (
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", name)
                ),
                key=_slide_number,
            )
            tables: list[tuple[tuple[str, ...], ...]] = []
            for slide_path in slide_paths:
                root = ElementTree.fromstring(archive.read(slide_path))
                tables.extend(_table(item) for item in root.iter() if _name(item) == "tbl")
    except (BadZipFile, KeyError, OSError, ElementTree.ParseError) as error:
        raise HwpLiveError(f"PPTX 표를 읽지 못했습니다: {path}") from error
    if table_index >= len(tables):
        raise HwpLiveError("요청한 PPTX 표 번호가 없습니다")
    return tables[table_index]

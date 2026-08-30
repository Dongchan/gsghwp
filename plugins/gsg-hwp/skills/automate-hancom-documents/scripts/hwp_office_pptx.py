from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from pathlib import Path
from xml.etree import ElementTree
from xml.etree.ElementTree import Element
from zipfile import BadZipFile, ZipFile

from hwp_errors import HwpLiveError


PptxColor = tuple[int, int, int]


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
                tables.extend(
                    _table(item) for item in root.iter() if _name(item) == "tbl"
                )
    except (BadZipFile, KeyError, OSError, ElementTree.ParseError) as error:
        raise HwpLiveError(f"PPTX 표를 읽지 못했습니다: {path}") from error
    if table_index >= len(tables):
        raise HwpLiveError("요청한 PPTX 표 번호가 없습니다")
    return tables[table_index]


@dataclass(frozen=True, slots=True)
class PptxGeometry:
    left_emu: int
    top_emu: int
    width_emu: int
    height_emu: int


@dataclass(frozen=True, slots=True)
class PptxTextRunData:
    text: str
    bold: bool | None = None
    font_name: str | None = None
    font_size_pt: float | None = None
    text_color: PptxColor | None = None


@dataclass(frozen=True, slots=True)
class PptxTextParagraphData:
    runs: tuple[PptxTextRunData, ...]

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass(frozen=True, slots=True)
class PptxTextShapeData:
    source_order: int
    geometry: PptxGeometry
    paragraphs: tuple[PptxTextParagraphData, ...]
    is_heading: bool = False


@dataclass(frozen=True, slots=True)
class PptxBorderData:
    width_pt: float
    color: PptxColor | None


@dataclass(frozen=True, slots=True)
class PptxTableCellData:
    text: str = ""
    paragraphs: tuple[PptxTextParagraphData, ...] = ()
    bold: bool | None = None
    font_size_pt: float | None = None
    text_color: PptxColor | None = None
    alignment: str | None = None
    fill_color: PptxColor | None = None
    borders: tuple[
        PptxBorderData | None,
        PptxBorderData | None,
        PptxBorderData | None,
        PptxBorderData | None,
    ] = (None, None, None, None)


@dataclass(frozen=True, slots=True)
class PptxTableShapeData:
    source_order: int
    geometry: PptxGeometry
    rows: tuple[tuple[PptxTableCellData, ...], ...]
    merges: tuple[tuple[int, int, int, int], ...]
    column_widths_emu: tuple[int, ...]
    row_heights_emu: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PptxImageShapeData:
    source_order: int
    geometry: PptxGeometry
    path: Path
    source_name: str


PptxSlideElement = PptxTextShapeData | PptxTableShapeData | PptxImageShapeData


@dataclass(frozen=True, slots=True)
class PptxSlideContent:
    slide_number: int
    slide_path: str
    slide_width_emu: int
    slide_height_emu: int
    elements: tuple[PptxSlideElement, ...]
    unsupported_elements: tuple[str, ...] = ()
    # The slide shape tree is content. Inherited master/layout furniture is
    # intentionally not materialized as destination content.
    excluded_presentation_furniture: tuple[str, ...] = (
        "slide_master_chrome",
        "navigation",
        "slide_number_footer",
    )

    @property
    def aspect_ratio(self) -> float:
        return self.slide_width_emu / self.slide_height_emu

    @property
    def text_shape_count(self) -> int:
        return sum(isinstance(element, PptxTextShapeData) for element in self.elements)

    @property
    def text_paragraph_count(self) -> int:
        return sum(
            len(element.paragraphs)
            for element in self.elements
            if isinstance(element, PptxTextShapeData)
        )

    @property
    def text_run_count(self) -> int:
        return sum(
            len(paragraph.runs)
            for element in self.elements
            if isinstance(element, PptxTextShapeData)
            for paragraph in element.paragraphs
        )

    @property
    def table_count(self) -> int:
        return sum(isinstance(element, PptxTableShapeData) for element in self.elements)

    @property
    def source_image_count(self) -> int:
        return sum(isinstance(element, PptxImageShapeData) for element in self.elements)


def _integer(element: Element | None, attribute: str, default: int = 0) -> int:
    if element is None:
        return default
    value = element.attrib.get(attribute)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise HwpLiveError(f"PPTX 좌표가 정수가 아닙니다: {value}") from error


def _geometry(element: Element) -> PptxGeometry:
    transform = next((item for item in element.iter() if _name(item) == "xfrm"), None)
    offset = (
        None
        if transform is None
        else next((item for item in transform if _name(item) == "off"), None)
    )
    extent = (
        None
        if transform is None
        else next((item for item in transform if _name(item) == "ext"), None)
    )
    return PptxGeometry(
        left_emu=_integer(offset, "x"),
        top_emu=_integer(offset, "y"),
        width_emu=max(1, _integer(extent, "cx", 1)),
        height_emu=max(1, _integer(extent, "cy", 1)),
    )


def _srgb_color(element: Element | None) -> PptxColor | None:
    if element is None:
        return None
    color = next((item for item in element.iter() if _name(item) == "srgbClr"), None)
    if color is None:
        return None
    raw = color.attrib.get("val", "")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", raw):
        return None
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _solid_fill_color(element: Element | None) -> PptxColor | None:
    if element is None:
        return None
    fill = next((item for item in element if _name(item) == "solidFill"), None)
    return _srgb_color(fill)


def _run(element: Element) -> PptxTextRunData | None:
    text = "".join(item.text or "" for item in element.iter() if _name(item) == "t")
    if not text:
        return None
    properties = next(
        (item for item in element if _name(item) in {"rPr", "endParaRPr"}), None
    )
    bold: bool | None = None
    font_size_pt: float | None = None
    font_name: str | None = None
    if properties is not None:
        if "b" in properties.attrib:
            bold = properties.attrib["b"] not in {"0", "false"}
        if "sz" in properties.attrib:
            try:
                font_size_pt = int(properties.attrib["sz"]) / 100
            except ValueError:
                font_size_pt = None
        font = next(
            (
                item
                for item in properties
                if _name(item) in {"latin", "ea", "cs"} and item.attrib.get("typeface")
            ),
            None,
        )
        if font is not None:
            font_name = font.attrib.get("typeface")
    return PptxTextRunData(
        text=text,
        bold=bold,
        font_name=font_name,
        font_size_pt=font_size_pt,
        text_color=_solid_fill_color(properties),
    )


def _paragraph(element: Element) -> PptxTextParagraphData:
    runs: list[PptxTextRunData] = []
    for child in element:
        if _name(child) in {"r", "fld"}:
            value = _run(child)
            if value is not None:
                runs.append(value)
        elif _name(child) == "br":
            runs.append(PptxTextRunData(text="\n"))
    return PptxTextParagraphData(runs=tuple(runs))


def _text_shape(element: Element, source_order: int) -> PptxTextShapeData | None:
    body = next((item for item in element if _name(item) == "txBody"), None)
    if body is None:
        return None
    paragraphs = tuple(
        paragraph
        for paragraph in (_paragraph(item) for item in body if _name(item) == "p")
        if paragraph.text
    )
    if not paragraphs:
        return None
    placeholder = next((item for item in element.iter() if _name(item) == "ph"), None)
    placeholder_type = None if placeholder is None else placeholder.attrib.get("type")
    return PptxTextShapeData(
        source_order=source_order,
        geometry=_geometry(element),
        paragraphs=paragraphs,
        is_heading=placeholder_type in {"title", "ctrTitle", "subTitle"},
    )


def _alignment(value: str | None) -> str | None:
    return {
        "l": "left",
        "ctr": "center",
        "r": "right",
        "just": "justify",
    }.get(value or "")


def _line(cell_properties: Element | None, name: str) -> PptxBorderData | None:
    if cell_properties is None:
        return None
    line = next((item for item in cell_properties if _name(item) == name), None)
    if line is None:
        return None
    width = float(line.attrib.get("w", "15240")) / 12700
    return PptxBorderData(width_pt=max(0.1, width), color=_solid_fill_color(line))


def _table_cell(element: Element) -> PptxTableCellData:
    text = _cell_text(element)
    paragraphs = tuple(
        _paragraph(item) for item in element.iter() if _name(item) == "p"
    )
    first_run = next((run for paragraph in paragraphs for run in paragraph.runs), None)
    properties = next((item for item in element if _name(item) == "tcPr"), None)
    paragraph_properties = next(
        (item for item in element.iter() if _name(item) == "pPr"), None
    )
    return PptxTableCellData(
        text=text,
        paragraphs=paragraphs,
        bold=None if first_run is None else first_run.bold,
        font_size_pt=None if first_run is None else first_run.font_size_pt,
        text_color=None if first_run is None else first_run.text_color,
        alignment=(
            None
            if paragraph_properties is None
            else _alignment(paragraph_properties.attrib.get("algn"))
        ),
        fill_color=_solid_fill_color(properties),
        borders=(
            _line(properties, "lnL"),
            _line(properties, "lnR"),
            _line(properties, "lnT"),
            _line(properties, "lnB"),
        ),
    )


def _table_shape(element: Element, source_order: int) -> PptxTableShapeData | None:
    table = next((item for item in element.iter() if _name(item) == "tbl"), None)
    if table is None:
        return None
    grid = next((item for item in table if _name(item) == "tblGrid"), None)
    column_widths = (
        tuple(_integer(item, "w") for item in grid if _name(item) == "gridCol")
        if grid is not None
        else ()
    )
    raw_rows = [item for item in table if _name(item) == "tr"]
    row_heights = tuple(_integer(item, "h") for item in raw_rows)
    occupied: set[tuple[int, int]] = set()
    values: dict[tuple[int, int], PptxTableCellData] = {}
    merges: list[tuple[int, int, int, int]] = []
    max_column = len(column_widths)
    for row_index, row in enumerate(raw_rows):
        column = 0
        for cell in (item for item in row if _name(item) == "tc"):
            while (row_index, column) in occupied:
                column += 1
            column_span = max(1, _integer(cell, "gridSpan", 1))
            row_span = max(1, _integer(cell, "rowSpan", 1))
            value = _table_cell(cell)
            values[(row_index, column)] = value
            if row_span > 1 or column_span > 1:
                merges.append((row_index, column, row_span, column_span))
            for covered_row in range(row_index, row_index + row_span):
                for covered_column in range(column, column + column_span):
                    occupied.add((covered_row, covered_column))
            column += column_span
            max_column = max(max_column, column)
    if not raw_rows or max_column == 0:
        return None
    rows = tuple(
        tuple(
            values.get((row, column), PptxTableCellData())
            for column in range(max_column)
        )
        for row in range(len(raw_rows))
    )
    if not column_widths:
        column_widths = tuple(1 for _ in range(max_column))
    elif len(column_widths) < max_column:
        column_widths += (1,) * (max_column - len(column_widths))
    return PptxTableShapeData(
        source_order=source_order,
        geometry=_geometry(element),
        rows=rows,
        merges=tuple(merges),
        column_widths_emu=column_widths,
        row_heights_emu=row_heights,
    )


def _relationship_target(source_part: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _relationships(archive: ZipFile, part: str) -> dict[str, str]:
    relationship_part = posixpath.join(
        posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
    )
    if relationship_part not in archive.namelist():
        return {}
    root = ElementTree.fromstring(archive.read(relationship_part))
    return {
        item.attrib["Id"]: _relationship_target(part, item.attrib["Target"])
        for item in root
        if _name(item) == "Relationship"
        and "Id" in item.attrib
        and "Target" in item.attrib
    }


def _visible_slide_part(archive: ZipFile, slide_number: int) -> str:
    presentation_part = "ppt/presentation.xml"
    if presentation_part not in archive.namelist():
        fallback = f"ppt/slides/slide{slide_number}.xml"
        if fallback not in archive.namelist():
            raise HwpLiveError(f"요청한 PPTX 슬라이드 번호가 없습니다: {slide_number}")
        return fallback
    presentation_root = ElementTree.fromstring(archive.read(presentation_part))
    slide_ids = next(
        (item for item in presentation_root.iter() if _name(item) == "sldIdLst"),
        None,
    )
    if slide_ids is None:
        raise HwpLiveError("PPTX의 표시 슬라이드 목록을 읽지 못했습니다")
    visible_ids = [item for item in slide_ids if _name(item) == "sldId"]
    if slide_number > len(visible_ids):
        raise HwpLiveError(f"요청한 PPTX 슬라이드 번호가 없습니다: {slide_number}")
    relationship_id = next(
        (
            value
            for key, value in visible_ids[slide_number - 1].attrib.items()
            if key != "id" and key.rsplit("}", 1)[-1] == "id"
        ),
        None,
    )
    if relationship_id is None:
        raise HwpLiveError("PPTX 표시 슬라이드 관계를 읽지 못했습니다")
    target = _relationships(archive, presentation_part).get(relationship_id)
    if target is None or target not in archive.namelist():
        raise HwpLiveError("PPTX 표시 슬라이드 대상이 없습니다")
    return target


def _slide_size(archive: ZipFile) -> tuple[int, int]:
    root = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
    size = next((item for item in root.iter() if _name(item) == "sldSz"), None)
    return (
        max(1, _integer(size, "cx", 15_119_350)),
        max(1, _integer(size, "cy", 10_691_813)),
    )


def _image_path(
    archive: ZipFile,
    slide_part: str,
    relationship_id: str,
    output_directory: Path,
    source_order: int,
) -> tuple[Path, str]:
    target = _relationships(archive, slide_part).get(relationship_id)
    if target is None or target not in archive.namelist():
        raise HwpLiveError(f"PPTX 원본 그림 관계를 읽지 못했습니다: {relationship_id}")
    source_name = Path(target).name
    output_path = output_directory / f"source-image-{source_order:03d}-{source_name}"
    if output_path.exists():
        raise HwpLiveError(f"PPTX 원본 그림 출력 파일이 이미 있습니다: {output_path}")
    _ = output_path.write_bytes(archive.read(target))
    return output_path, source_name


def read_pptx_slide(
    path: Path,
    *,
    slide_number: int,
    output_directory: Path,
) -> PptxSlideContent:
    if slide_number < 1:
        raise HwpLiveError("PPTX 슬라이드 번호는 1 이상이어야 합니다")
    output_directory.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(path) as archive:
            slide_part = _visible_slide_part(archive, slide_number)
            slide_root = ElementTree.fromstring(archive.read(slide_part))
            slide_width, slide_height = _slide_size(archive)
            shape_tree = next(
                (item for item in slide_root.iter() if _name(item) == "spTree"),
                None,
            )
            if shape_tree is None:
                raise HwpLiveError("PPTX 슬라이드 도형 목록을 읽지 못했습니다")
            elements: list[PptxSlideElement] = []
            unsupported: list[str] = []
            for source_order, child in enumerate(shape_tree):
                kind = _name(child)
                if kind in {"nvGrpSpPr", "grpSpPr"}:
                    continue
                if kind == "sp":
                    text_shape = _text_shape(child, source_order)
                    if text_shape is None:
                        unsupported.append("shape_without_text")
                    else:
                        elements.append(text_shape)
                    continue
                if kind == "pic":
                    blip = next(
                        (item for item in child.iter() if _name(item) == "blip"), None
                    )
                    relationship_id = (
                        None
                        if blip is None
                        else next(
                            (
                                value
                                for key, value in blip.attrib.items()
                                if key.rsplit("}", 1)[-1] == "embed"
                            ),
                            None,
                        )
                    )
                    if relationship_id is None:
                        unsupported.append("picture_without_relationship")
                        continue
                    image_path, source_name = _image_path(
                        archive,
                        slide_part,
                        relationship_id,
                        output_directory,
                        source_order,
                    )
                    elements.append(
                        PptxImageShapeData(
                            source_order=source_order,
                            geometry=_geometry(child),
                            path=image_path,
                            source_name=source_name,
                        )
                    )
                    continue
                if kind == "graphicFrame":
                    table_shape = _table_shape(child, source_order)
                    if table_shape is None:
                        unsupported.append("graphic_frame_without_table")
                    else:
                        elements.append(table_shape)
                    continue
                if kind == "grpSp":
                    unsupported.append("group_shape")
                    continue
                unsupported.append(kind)
            text_elements = [
                element
                for element in elements
                if isinstance(element, PptxTextShapeData)
            ]
            if text_elements and not any(
                element.is_heading for element in text_elements
            ):
                first = min(
                    text_elements,
                    key=lambda element: (
                        element.geometry.top_emu,
                        element.geometry.left_emu,
                        element.source_order,
                    ),
                )
                elements[elements.index(first)] = replace(first, is_heading=True)
            elements.sort(
                key=lambda element: (
                    element.geometry.top_emu,
                    element.geometry.left_emu,
                    element.source_order,
                )
            )
            return PptxSlideContent(
                slide_number=slide_number,
                slide_path=slide_part,
                slide_width_emu=slide_width,
                slide_height_emu=slide_height,
                elements=tuple(elements),
                unsupported_elements=tuple(unsupported),
            )
    except HwpLiveError:
        raise
    except (BadZipFile, KeyError, OSError, ElementTree.ParseError, ValueError) as error:
        raise HwpLiveError(
            f"PPTX 슬라이드의 의미 구조를 읽지 못했습니다: {path}"
        ) from error

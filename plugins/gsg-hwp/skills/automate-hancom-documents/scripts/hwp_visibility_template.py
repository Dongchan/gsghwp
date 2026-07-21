from __future__ import annotations

from pathlib import Path

from hwp_live_structure_contract import StructureTable
from hwp_live_native_template_repeat import caption_literal_text
from hwp_live_template_repeat import (
    TableTemplateRepeatPlan,
    TemplateImageCell,
    TemplateTableBlock,
    TemplateTextCell,
)
from hwp_visibility_series_contract import (
    IMAGE_HEIGHT_MM,
    IMAGE_WIDTH_MM,
    VisibilityRecord,
    VisibilitySeriesPlanError,
    VisibilitySlot,
)
from hwp_visibility_source import extract_visibility_records, numbered_image
from hwp_visibility_table_cells import (
    cell_at,
    compact_cell_text,
    logical_cells,
    owner_cells,
    required_cell,
)


def _caption_title(table: StructureTable) -> str | None:
    if table.caption is None:
        return None
    title = caption_literal_text(
        table.caption.text,
        automatic_number=table.caption.automatic_number,
    )
    return title or None


def visibility_template_slots(
    table: StructureTable,
) -> tuple[tuple[VisibilitySlot, ...], int]:
    owners = owner_cells(table)
    headers = tuple(
        cell
        for cell in owners
        if compact_cell_text(cell.text) == "구분" and cell.column_span >= 2
    )
    if not headers:
        raise VisibilitySeriesPlanError("템플릿에서 반복 구분 머리글을 찾지 못했습니다")
    ordered = tuple(sorted(headers, key=lambda cell: cell.row))
    gaps = tuple(right.row - left.row for left, right in zip(ordered, ordered[1:]))
    slot_height = table.rows - ordered[0].row if not gaps else gaps[0]
    if slot_height < 5 or any(gap != slot_height for gap in gaps):
        raise VisibilitySeriesPlanError("템플릿의 반복 슬롯 높이가 일정하지 않습니다")
    logical = logical_cells(table)
    slots: list[VisibilitySlot] = []
    for header in ordered:
        row = header.row
        location_header = required_cell(
            owners,
            "조망위치 머리글",
            lambda cell: cell.row == row and compact_cell_text(cell.text) == "조망위치",
        )
        distance_header = required_cell(
            owners,
            "이격거리 머리글",
            lambda cell: cell.row == row and compact_cell_text(cell.text) == "이격거리",
        )
        elevation_header = required_cell(
            owners,
            "표고 머리글",
            lambda cell: cell.row == row and compact_cell_text(cell.text).startswith("표고"),
        )
        visibility_label = required_cell(
            owners,
            "가시권 분석 라벨",
            lambda cell: cell.row == row + 2
            and compact_cell_text(cell.text) == "가시권분석",
        )
        current_label = required_cell(
            owners,
            "현황사진 라벨",
            lambda cell: cell.row == row + 2 and compact_cell_text(cell.text) == "현황사진",
        )
        result_label = required_cell(
            owners,
            "분석결과 라벨",
            lambda cell: cell.row == row + 4 and compact_cell_text(cell.text) == "분석결과",
        )
        slots.append(
            VisibilitySlot(
                header=header,
                category=cell_at(logical, row + 1, header.column, "구분 값"),
                label=cell_at(logical, row + 1, header.column + 1, "조망점 번호"),
                location=cell_at(logical, row + 1, location_header.column, "조망위치 값"),
                distance=cell_at(logical, row + 1, distance_header.column, "이격거리 값"),
                elevation=cell_at(logical, row + 1, elevation_header.column, "표고 값"),
                visibility_image=cell_at(
                    logical, row + 3, visibility_label.column, "가시권 사진"
                ),
                current_image=cell_at(
                    logical, row + 3, current_label.column, "현황사진"
                ),
                result=cell_at(
                    logical,
                    row + 4,
                    result_label.column + result_label.column_span,
                    "분석결과 값",
                ),
            )
        )
    return tuple(slots), slot_height


def _text(slot: VisibilitySlot, record: VisibilityRecord) -> tuple[TemplateTextCell, ...]:
    values = (
        (slot.category, record.category),
        (slot.label, record.label),
        (slot.location, record.location),
        (slot.distance, record.distance),
        (slot.elevation, record.elevation),
        (slot.result, record.result),
    )
    return tuple(
        TemplateTextCell(
            address=cell.address,
            expected_text=cell.text,
            replacement=value,
        )
        for cell, value in values
    )


def build_visibility_series_plan(
    source: StructureTable,
    template: StructureTable,
    visibility_folder: Path,
    current_folder: Path,
) -> TableTemplateRepeatPlan:
    if template.control_instance_id is None:
        raise VisibilitySeriesPlanError("템플릿 표의 네이티브 식별자를 읽지 못했습니다")
    records = extract_visibility_records(source)
    slots, slot_height = visibility_template_slots(template)
    blocks: list[TemplateTableBlock] = []
    for start in range(0, len(records), len(slots)):
        group = records[start : start + len(slots)]
        texts: list[TemplateTextCell] = []
        images: list[TemplateImageCell] = []
        for slot, record in zip(slots, group, strict=False):
            texts.extend(_text(slot, record))
            images.extend(
                (
                    TemplateImageCell(
                        address=slot.visibility_image.address,
                        expected_text=slot.visibility_image.text,
                        path=numbered_image(visibility_folder, record.number),
                        width_mm=IMAGE_WIDTH_MM,
                        height_mm=IMAGE_HEIGHT_MM,
                    ),
                    TemplateImageCell(
                        address=slot.current_image.address,
                        expected_text=slot.current_image.text,
                        path=numbered_image(current_folder, record.number),
                        width_mm=IMAGE_WIDTH_MM,
                        height_mm=IMAGE_HEIGHT_MM,
                    ),
                )
            )
        unused = len(slots) - len(group)
        blocks.append(
            TemplateTableBlock(
                text_cells=tuple(texts),
                images=tuple(images),
                delete_rows_from=(
                    None if unused == 0 else slots[len(group)].header.address
                ),
                delete_row_count=unused * slot_height,
            )
        )
    return TableTemplateRepeatPlan(
        source_page=template.page_start,
        source_control_id=template.control_instance_id,
        caption_title=_caption_title(template),
        blocks=tuple(blocks),
    )

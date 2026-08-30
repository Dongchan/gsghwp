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
from hwp_visibility_template_observation import (
    VisibilityTemplateCellObservation,
    VisibilityTemplateMismatch,
    VisibilityTemplateObservation,
    VisibilityTemplatePosition,
)
from hwp_visibility_source import extract_visibility_records, optional_numbered_image
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


def analyze_visibility_template(table: StructureTable) -> VisibilityTemplateObservation:
    owners = owner_cells(table)
    owner_addresses = frozenset(cell.address for cell in owners)
    missing_owner_addresses = tuple(
        sorted(
            {
                cell.owner_address
                for cell in table.cells
                if cell.owner_address not in owner_addresses
            }
        )
    )
    cells: list[VisibilityTemplateCellObservation] = []
    for cell in owners:
        compact = compact_cell_text(cell.text)
        text_matches = compact == "구분"
        span_ok = cell.column_span >= 2
        cells.append(
            VisibilityTemplateCellObservation(
                address=cell.address,
                owner_address=cell.owner_address,
                row=cell.row,
                column=cell.column,
                row_span=cell.row_span,
                column_span=cell.column_span,
                raw_text=cell.text,
                compact_text=compact,
                is_owner=True,
                text_matches=text_matches,
                span_ok=span_ok,
                is_candidate=text_matches or span_ok,
                is_header=text_matches and span_ok,
            )
        )
    headers = tuple(
        cell
        for cell in owners
        if compact_cell_text(cell.text) == "구분" and cell.column_span >= 2
    )
    ordered = tuple(sorted(headers, key=lambda cell: cell.row))
    gaps = tuple(right.row - left.row for left, right in zip(ordered, ordered[1:]))
    slot_height = (
        None if not ordered else table.rows - ordered[0].row if not gaps else gaps[0]
    )
    condition_owner_cells_complete = not missing_owner_addresses
    condition_header_text_found = any(
        compact_cell_text(cell.text) == "구분" for cell in owners
    )
    condition_header_span_found = any(
        compact_cell_text(cell.text) == "구분" and cell.column_span >= 2
        for cell in owners
    )
    condition_slot_height_minimum = slot_height is not None and slot_height >= 5
    condition_slot_gaps_uniform = bool(ordered) and all(
        gap == slot_height for gap in gaps
    )
    mismatches: list[VisibilityTemplateMismatch] = []
    if not condition_owner_cells_complete:
        mismatches.append(
            VisibilityTemplateMismatch(
                code="missing_owner_cells",
                reason="일부 논리 셀의 병합 소유자 셀이 owner cell 목록에 없습니다",
                expected="every cell.owner_address appears as an owner cell address",
                observed=", ".join(missing_owner_addresses),
            )
        )
    if not condition_header_text_found:
        observed = ", ".join(
            f"{cell.address}:{compact_cell_text(cell.text)}" for cell in owners
        )
        mismatches.append(
            VisibilityTemplateMismatch(
                code="header_text_mismatch",
                reason="반복 머리글 텍스트가 정확히 구분이어야 합니다",
                expected="compact_text == 구분",
                observed=observed,
            )
        )
    elif not condition_header_span_found:
        observed = ", ".join(
            f"{cell.address}:column_span={cell.column_span}"
            for cell in owners
            if compact_cell_text(cell.text) == "구분"
        )
        mismatches.append(
            VisibilityTemplateMismatch(
                code="header_span_too_small",
                reason="구분 머리글 셀은 최소 2열 이상 병합되어야 합니다",
                expected="column_span >= 2",
                observed=observed,
            )
        )
    if ordered and slot_height is not None and slot_height < 5:
        mismatches.append(
            VisibilityTemplateMismatch(
                code="slot_height_too_small",
                reason="반복 슬롯 높이는 최소 5행이어야 합니다",
                expected="slot_height >= 5",
                observed=str(slot_height),
            )
        )
    if ordered and slot_height is not None and any(gap != slot_height for gap in gaps):
        mismatches.append(
            VisibilityTemplateMismatch(
                code="slot_gaps_nonuniform",
                reason="반복 머리글 사이 간격은 계산된 슬롯 높이와 모두 같아야 합니다",
                expected=f"every gap == {slot_height}",
                observed=", ".join(str(gap) for gap in gaps),
            )
        )
    recognized = (
        condition_owner_cells_complete
        and condition_header_span_found
        and condition_slot_height_minimum
        and condition_slot_gaps_uniform
    )
    positions = tuple(
        VisibilityTemplatePosition(
            address=cell.address, row=cell.row, column=cell.column
        )
        for cell in ordered
    )
    return VisibilityTemplateObservation(
        table_ref=table.table_ref,
        control_instance_id=table.control_instance_id,
        page_start=table.page_start,
        page_end=table.page_end,
        rows=table.rows,
        columns=table.columns,
        owner_cells=tuple(cells),
        missing_owner_addresses=missing_owner_addresses,
        header_positions=positions,
        slot_positions=positions,
        slot_height=slot_height,
        gaps=gaps,
        condition_owner_cells_complete=condition_owner_cells_complete,
        condition_header_text_found=condition_header_text_found,
        condition_header_span_found=condition_header_span_found,
        condition_slot_height_minimum=condition_slot_height_minimum,
        condition_slot_gaps_uniform=condition_slot_gaps_uniform,
        recognized=recognized,
        mismatch_codes=tuple(mismatch.code for mismatch in mismatches),
        mismatches=tuple(mismatches),
    )


def visibility_template_slots(
    table: StructureTable,
) -> tuple[tuple[VisibilitySlot, ...], int]:
    observation = analyze_visibility_template(table)
    if not observation.header_positions:
        raise VisibilitySeriesPlanError(
            "템플릿에서 반복 구분 머리글을 찾지 못했습니다", observation
        )
    if not observation.recognized:
        raise VisibilitySeriesPlanError(
            "템플릿의 반복 슬롯 구조가 표준 조건과 일치하지 않습니다", observation
        )
    owners = owner_cells(table)
    ordered = tuple(
        sorted(
            (
                cell
                for cell in owners
                if compact_cell_text(cell.text) == "구분" and cell.column_span >= 2
            ),
            key=lambda cell: cell.row,
        )
    )
    slot_height = observation.slot_height
    if slot_height is None:
        raise VisibilitySeriesPlanError(
            "템플릿에서 반복 구분 머리글을 찾지 못했습니다", observation
        )
    try:
        logical = logical_cells(table)
    except VisibilitySeriesPlanError as error:
        raise VisibilitySeriesPlanError(error.message, observation) from error
    slots: list[VisibilitySlot] = []
    for header in ordered:
        row = header.row
        try:
            location_header = required_cell(
                owners,
                "조망위치 머리글",
                lambda cell: (
                    cell.row == row and compact_cell_text(cell.text) == "조망위치"
                ),
            )
            distance_header = required_cell(
                owners,
                "이격거리 머리글",
                lambda cell: (
                    cell.row == row and compact_cell_text(cell.text) == "이격거리"
                ),
            )
            elevation_header = required_cell(
                owners,
                "표고 머리글",
                lambda cell: (
                    cell.row == row and compact_cell_text(cell.text).startswith("표고")
                ),
            )
            visibility_label = required_cell(
                owners,
                "가시권 분석 라벨",
                lambda cell: (
                    cell.row == row + 2 and compact_cell_text(cell.text) == "가시권분석"
                ),
            )
            current_label = required_cell(
                owners,
                "현황사진 라벨",
                lambda cell: (
                    cell.row == row + 2 and compact_cell_text(cell.text) == "현황사진"
                ),
            )
            result_label = required_cell(
                owners,
                "분석결과 라벨",
                lambda cell: (
                    cell.row == row + 4 and compact_cell_text(cell.text) == "분석결과"
                ),
            )
        except VisibilitySeriesPlanError as error:
            raise VisibilitySeriesPlanError(error.message, observation) from error
        slots.append(
            VisibilitySlot(
                header=header,
                category=cell_at(logical, row + 1, header.column, "구분 값"),
                label=cell_at(logical, row + 1, header.column + 1, "조망점 번호"),
                location=cell_at(
                    logical, row + 1, location_header.column, "조망위치 값"
                ),
                distance=cell_at(
                    logical, row + 1, distance_header.column, "이격거리 값"
                ),
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


def _text(
    slot: VisibilitySlot, record: VisibilityRecord
) -> tuple[TemplateTextCell, ...]:
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
    plan, _observation = build_visibility_series_plan_with_observation(
        source,
        template,
        visibility_folder,
        current_folder,
    )
    return plan


def build_visibility_series_plan_with_observation(
    source: StructureTable,
    template: StructureTable,
    visibility_folder: Path,
    current_folder: Path,
) -> tuple[TableTemplateRepeatPlan, VisibilityTemplateObservation]:
    observation = analyze_visibility_template(template)
    if template.control_instance_id is None:
        raise VisibilitySeriesPlanError(
            "템플릿 표의 네이티브 식별자를 읽지 못했습니다", observation
        )
    records = extract_visibility_records(source)
    try:
        slots, slot_height = visibility_template_slots(template)
    except VisibilitySeriesPlanError as error:
        raise VisibilitySeriesPlanError(
            error.message, error.template_observation or observation
        ) from error
    blocks: list[TemplateTableBlock] = []
    for start in range(0, len(records), len(slots)):
        group = records[start : start + len(slots)]
        texts: list[TemplateTextCell] = []
        images: list[TemplateImageCell] = []
        for slot, record in zip(slots, group, strict=False):
            texts.extend(_text(slot, record))
            visibility_path = optional_numbered_image(visibility_folder, record.number)
            current_path = optional_numbered_image(current_folder, record.number)
            if visibility_path is not None:
                images.append(
                    TemplateImageCell(
                        address=slot.visibility_image.address,
                        expected_text=slot.visibility_image.text,
                        path=visibility_path,
                        width_mm=IMAGE_WIDTH_MM,
                        height_mm=IMAGE_HEIGHT_MM,
                    )
                )
            if current_path is not None:
                images.append(
                    TemplateImageCell(
                        address=slot.current_image.address,
                        expected_text=slot.current_image.text,
                        path=current_path,
                        width_mm=IMAGE_WIDTH_MM,
                        height_mm=IMAGE_HEIGHT_MM,
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
    plan = TableTemplateRepeatPlan(
        source_page=template.page_start,
        source_control_id=template.control_instance_id,
        caption_title=_caption_title(template),
        blocks=tuple(blocks),
    )
    return plan, observation

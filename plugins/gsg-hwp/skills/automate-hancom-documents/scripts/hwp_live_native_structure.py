from __future__ import annotations

import re
from collections import defaultdict

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import NativeDetailedCell, NativeDetailedInspection
from hwp_live_structure_contract import (
    DocumentStructure,
    PageParagraph,
    StructureCaption,
    StructureCell,
    StructureControl,
    StructureMerge,
    StructurePosition,
    StructureTable,
)
from hwp_live_structure_identity import (
    control_kind,
    control_ref,
    structure_token,
    table_ref,
)


_ADDRESS = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")


def _hwpunit_to_mm(value: int | None) -> float | None:
    return None if value is None else round(value / 283.4645669, 3)


def _cell_text(value: str) -> str:
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n"):
        return value[:-1]
    return value


def _column_index(value: str) -> int:
    result = 0
    for character in value:
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _column_name(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _address(value: str) -> tuple[int, int]:
    match = _ADDRESS.fullmatch(value)
    if match is None:
        raise HwpLiveError(f"네이티브 셀 주소가 올바르지 않습니다: {value}")
    return int(match.group(2)) - 1, _column_index(match.group(1))


def _position(list_id: int, paragraph: int, character: int) -> StructurePosition:
    return StructurePosition(
        list_id=list_id,
        paragraph=paragraph,
        character=character,
    )


def document_structure_from_native(
    native: NativeDetailedInspection,
    *,
    selector: str,
    window_handle: int,
) -> DocumentStructure:
    failed_controls = {
        error.control_instance_id for error in native.inspection_errors
    }
    has_healthy_table = any(
        control.top_level
        and control.control_type == "tbl"
        and control.page_start <= native.page <= control.page_end
        and control.instance_id not in failed_controls
        for control in native.controls
    )
    if native.inspection_errors and not has_healthy_table:
        first = native.inspection_errors[0]
        raise HwpLiveError(
            f"네이티브 상세 구조 조회 실패: {first.code}: {first.message}"
        )
    if window_handle < 1:
        raise HwpLiveError("한컴 창 핸들이 올바르지 않습니다")

    cells_by_table: defaultdict[str, list[NativeDetailedCell]] = defaultdict(list)
    for cell in native.cells:
        cells_by_table[cell.table_instance_id].append(cell)
    captions = {caption.table_instance_id: caption for caption in native.captions}
    if len(captions) != len(native.captions):
        raise HwpLiveError("네이티브 상세 구조에 표 캡션이 중복되었습니다")
    picture_lists = {
        control.anchor.list_id
        for control in native.controls
        if not control.top_level and control.control_type == "gso"
    }
    nested_table_lists = {
        control.anchor.list_id
        for control in native.controls
        if not control.top_level and control.control_type == "tbl"
    }

    controls: list[StructureControl] = []
    tables: list[StructureTable] = []
    for control in native.controls:
        if (
            control.instance_id in failed_controls
            or not control.top_level
            or control.page_start > native.page
            or control.page_end < native.page
        ):
            continue
        reference = control_ref(
            native.document_id,
            control.control_type,
            control.instance_id,
        )
        controls.append(
            StructureControl(
                control_ref=reference,
                ctrl_id=control.control_type,
                kind=control_kind(control.control_type, control.user_description),
                user_description=control.user_description,
                anchor=_position(
                    control.anchor.list_id,
                    control.anchor.paragraph,
                    control.anchor.character,
                ),
                page_start=control.page_start,
                page_end=control.page_end,
                width_hwpunit=control.width_hwpunit,
                height_hwpunit=control.height_hwpunit,
                width_mm=_hwpunit_to_mm(control.width_hwpunit),
                height_mm=_hwpunit_to_mm(control.height_hwpunit),
            )
        )
        if control.control_type != "tbl":
            continue
        if control.rows is None or control.columns is None:
            raise HwpLiveError("네이티브 상세 구조 표의 행·열 수가 없습니다")

        owners: dict[tuple[int, int], NativeDetailedCell] = {}
        covered_by: dict[tuple[int, int], tuple[int, int]] = {}
        merges: list[StructureMerge] = []
        for cell in cells_by_table[control.instance_id]:
            row, column = _address(cell.address)
            if (
                row < 0
                or column < 0
                or row + cell.row_span > control.rows
                or column + cell.column_span > control.columns
            ):
                raise HwpLiveError(f"{cell.address} 셀 병합 범위가 표 밖입니다")
            if (row, column) in owners:
                raise HwpLiveError(f"{cell.address} 셀 소유자가 중복되었습니다")
            owners[(row, column)] = cell
            for covered_row in range(row, row + cell.row_span):
                for covered_column in range(column, column + cell.column_span):
                    key = (covered_row, covered_column)
                    if key in covered_by:
                        raise HwpLiveError(f"{cell.address} 셀 병합 범위가 겹칩니다")
                    covered_by[key] = (row, column)
            if cell.row_span > 1 or cell.column_span > 1:
                merges.append(
                    StructureMerge(
                        owner_address=cell.address,
                        row=row,
                        column=column,
                        row_span=cell.row_span,
                        column_span=cell.column_span,
                    )
                )

        expected = control.rows * control.columns
        if len(covered_by) != expected:
            raise HwpLiveError("네이티브 상세 구조 표에 누락된 셀이 있습니다")
        structure_cells: list[StructureCell] = []
        for row in range(control.rows):
            for column in range(control.columns):
                owner_key = covered_by[(row, column)]
                owner = owners.get(owner_key)
                if owner is None:
                    raise HwpLiveError("네이티브 상세 구조 셀 소유자를 찾지 못했습니다")
                owner_row, owner_column = owner_key
                owner_address = f"{_column_name(owner_column)}{owner_row + 1}"
                address = f"{_column_name(column)}{row + 1}"
                is_owner = owner_key == (row, column)
                structure_cells.append(
                    StructureCell(
                        address=address,
                        row=row,
                        column=column,
                        owner_address=owner_address,
                        row_span=owner.row_span if is_owner else 1,
                        column_span=owner.column_span if is_owner else 1,
                        text=_cell_text(owner.text) if is_owner else "",
                        width_hwpunit=owner.width_hwpunit,
                        height_hwpunit=owner.height_hwpunit,
                        width_mm=_hwpunit_to_mm(owner.width_hwpunit),
                        height_mm=_hwpunit_to_mm(owner.height_hwpunit),
                        has_picture=is_owner and owner.list_id in picture_lists,
                        has_nested_table=is_owner and owner.list_id in nested_table_lists,
                    )
                )

        native_caption = captions.get(control.instance_id)
        caption = (
            StructureCaption(
                text=_cell_text(native_caption.text),
                automatic_number=native_caption.automatic_number,
                style_id=native_caption.style_id,
                style_name=native_caption.style_name,
            )
            if native_caption is not None
            else None
        )
        tables.append(
            StructureTable(
                table_ref=table_ref(reference),
                control_instance_id=control.instance_id,
                anchor=_position(
                    control.anchor.list_id,
                    control.anchor.paragraph,
                    control.anchor.character,
                ),
                page_start=control.page_start,
                page_end=control.page_end,
                rows=control.rows,
                columns=control.columns,
                merges=tuple(merges),
                cells=tuple(structure_cells),
                caption=caption,
            )
        )

    page_text = native.text[:200_000]
    structure = DocumentStructure(
        selector=selector,
        document_id=native.document_id,
        full_name=native.full_name,
        window_handle=window_handle,
        page=native.page,
        page_count=native.page_count,
        state_token="0" * 64,
        page_text=page_text,
        paragraphs=tuple(
            PageParagraph(index=index, text=text)
            for index, text in enumerate(page_text.splitlines())
        ),
        controls=tuple(controls),
        tables=tuple(tables),
    )
    return structure.model_copy(update={"state_token": structure_token(structure)})

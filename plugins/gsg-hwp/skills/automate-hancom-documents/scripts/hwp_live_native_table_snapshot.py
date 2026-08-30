from __future__ import annotations

import ntpath

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import is_structural_inspection_error
from hwp_live_native_batch import inspect_native_page
from hwp_live_structure_contract import (
    DocumentStructure,
    PageParagraph,
    StructureCell,
    StructureTable,
)
from hwp_live_structure_identity import structure_token


def _normalized_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value)) if value else ""


def _cell_text(value: str) -> str:
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n"):
        return value[:-1]
    return value


def refresh_native_table_snapshot(
    snapshot: DocumentStructure,
    table: StructureTable,
) -> tuple[DocumentStructure, StructureTable] | None:
    inspected = inspect_native_page(
        snapshot.window_handle,
        table.page_start,
        include_cells=True,
    )
    if inspected is None:
        return None
    if (
        inspected.document_id != snapshot.document_id
        or _normalized_path(inspected.full_name) != _normalized_path(snapshot.full_name)
    ):
        raise HwpLiveError("네이티브 표 구조 조회 문서가 현재 연결 문서와 다릅니다")

    if table.control_instance_id is None:
        raise HwpLiveError("네이티브 표 제어 식별자가 구조 스냅샷에 없습니다")
    control = next(
        (
            item
            for item in inspected.controls
            if item.control_type == "tbl"
            and item.instance_id == table.control_instance_id
        ),
        None,
    )
    if control is None:
        raise HwpLiveError("네이티브 구조에서 수정 대상 표를 다시 찾지 못했습니다")
    if (
        control.anchor.list_id != table.anchor.list_id
        or control.anchor.paragraph != table.anchor.paragraph
        or control.anchor.character != table.anchor.character
    ):
        raise HwpLiveError("한컴 표 앵커가 조회 이후 바뀌었습니다")
    if control.rows != table.rows or control.columns != table.columns:
        raise HwpLiveError("한컴 표 행·열 구조가 조회 이후 바뀌었습니다")
    # Only an error about the table's own records may stop the refresh. An
    # unreadable appearance sample leaves every cell record usable.
    control_error = next(
        (
            error
            for error in inspected.inspection_errors
            if error.control_instance_id == control.instance_id
            and is_structural_inspection_error(error.code)
        ),
        None,
    )
    if control_error is not None:
        raise HwpLiveError(
            f"네이티브 표 구조 조회 실패: {control_error.code}: {control_error.message}"
        )

    native_cells = {
        cell.address: cell
        for cell in inspected.cells
        if cell.table_instance_id == control.instance_id
    }
    picture_lists = {
        item.anchor.list_id
        for item in inspected.controls
        if item.control_type == "gso"
    }
    nested_table_lists = {
        item.anchor.list_id
        for item in inspected.controls
        if item.control_type == "tbl" and item.instance_id != control.instance_id
    }
    cells: list[StructureCell] = []
    for cell in table.cells:
        native = native_cells.get(cell.address)
        if native is None:
            if cell.owner_address != cell.address:
                cells.append(cell)
                continue
            raise HwpLiveError(f"네이티브 구조에서 {cell.address} 셀을 다시 찾지 못했습니다")
        if (
            native.row_span != cell.row_span
            or native.column_span != cell.column_span
        ):
            raise HwpLiveError(f"{cell.address} 셀 병합 구조가 조회 이후 바뀌었습니다")
        cells.append(
            cell.model_copy(
                update={
                    "text": _cell_text(native.text),
                    "width_hwpunit": native.width_hwpunit,
                    "height_hwpunit": native.height_hwpunit,
                    "width_mm": None
                    if native.width_hwpunit is None
                    else round(native.width_hwpunit / 283.4645669, 3),
                    "height_mm": None
                    if native.height_hwpunit is None
                    else round(native.height_hwpunit / 283.4645669, 3),
                    "has_picture": native.list_id in picture_lists,
                    "has_nested_table": native.list_id in nested_table_lists,
                }
            )
        )

    refreshed_table = table.model_copy(update={"cells": tuple(cells)})
    refreshed_tables = tuple(
        refreshed_table if item.table_ref == table.table_ref else item
        for item in snapshot.tables
    )
    page_text = inspected.text if inspected.page == snapshot.page else snapshot.page_text
    paragraphs = (
        tuple(
            PageParagraph(index=index, text=text)
            for index, text in enumerate(page_text.splitlines())
        )
        if inspected.page == snapshot.page
        else snapshot.paragraphs
    )
    refreshed = snapshot.model_copy(
        update={
            "page_count": inspected.page_count,
            "state_token": "pending-native-token",
            "page_text": page_text,
            "paragraphs": paragraphs,
            "tables": refreshed_tables,
        }
    )
    return (
        refreshed.model_copy(update={"state_token": structure_token(refreshed)}),
        refreshed_table,
    )


def retain_table_result_snapshot(
    snapshot: DocumentStructure | None,
    table: StructureTable,
    state_token: str,
) -> DocumentStructure | None:
    if snapshot is None:
        return None
    tables = tuple(
        table if item.table_ref == table.table_ref else item
        for item in snapshot.tables
    )
    if all(item.table_ref != table.table_ref for item in snapshot.tables):
        return None
    return snapshot.model_copy(update={"tables": tables, "state_token": state_token})

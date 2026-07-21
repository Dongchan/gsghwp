from __future__ import annotations

from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import HwpControl, LiveHwpApplication
from hwp_live_control import control_anchor_position, control_instance_id
from hwp_live_inspection import inspect_styles
from hwp_live_state_guard import move_to_control, preserved_live_state
from hwp_live_structure_control import (
    describe_created_control as describe_created_control,
    find_table_control as find_table_control,
)
from hwp_live_structure_contract import (
    DocumentStructure,
    PageParagraph,
    StructureControl,
    StructureTable,
)
from hwp_live_structure_identity import (
    control_kind,
    control_ref,
    structure_position,
    structure_token,
)
from hwp_live_structure_table import inspect_table_control

def inspect_document_structure(
    hwp: LiveHwpApplication,
    *,
    selector: str,
    document_id: int,
    full_name: str,
    window_handle: int,
    page: int,
    guard: Callable[[], None],
) -> DocumentStructure:
    guard()
    if page == 0:
        target_page = hwp.current_page
        guard()
    else:
        target_page = page
    page_count = hwp.PageCount
    guard()
    if target_page < 1 or target_page > page_count:
        raise HwpLiveError("한컴 구조 조회 쪽 번호가 문서 범위를 벗어났습니다")
    with preserved_live_state(hwp, guard=guard):
        controls: list[StructureControl] = []
        tables: list[StructureTable] = []
        _ = hwp.goto_page(target_page)
        guard()
        if hwp.current_page != target_page:
            raise HwpLiveError("요청한 한컴 쪽으로 바로 이동하지 못했습니다")
        guard()
        page_start_position = hwp.get_pos()
        guard()
        page_end_position: tuple[int, int, int] | None = None
        if target_page < page_count:
            _ = hwp.goto_page(target_page + 1)
            guard()
            if hwp.current_page != target_page + 1:
                raise HwpLiveError("요청한 한컴 쪽의 끝 위치를 확인하지 못했습니다")
            guard()
            candidate_end = hwp.get_pos()
            guard()
            if (
                candidate_end[0] == page_start_position[0]
                and candidate_end > page_start_position
            ):
                page_end_position = candidate_end
        control_list = hwp.ctrl_list
        guard()
        control_records: list[
            tuple[int, HwpControl, str, tuple[int, int, int]]
        ] = []
        for ordinal, control in enumerate(control_list):
            guard()
            ctrl_id = control.CtrlID
            guard()
            anchor = control_anchor_position(control, guard)
            control_records.append((ordinal, control, ctrl_id, anchor))

        if page_end_position is not None or target_page == page_count:
            direct_records = [
                record
                for record in control_records
                if record[3][0] == page_start_position[0]
                and record[3] >= page_start_position
                and (
                    page_end_position is None
                    or record[3] < page_end_position
                )
            ]
            if not any(record[2] == "tbl" for record in direct_records):
                preceding_tables = [
                    record
                    for record in control_records
                    if record[2] == "tbl"
                    and record[3][0] == page_start_position[0]
                    and record[3] < page_start_position
                ]
                if preceding_tables:
                    direct_records.append(max(preceding_tables, key=lambda item: item[3]))
            records_to_inspect = sorted(direct_records, key=lambda item: item[0])
        else:
            records_by_anchor_page: list[
                tuple[int, HwpControl, str, tuple[int, int, int], int]
            ] = []
            for ordinal, control, ctrl_id, anchor in control_records:
                move_to_control(hwp, control, guard)
                anchor_page = hwp.current_page
                guard()
                records_by_anchor_page.append(
                    (ordinal, control, ctrl_id, anchor, anchor_page)
                )
            direct_records = [
                record[:4]
                for record in records_by_anchor_page
                if record[4] == target_page
            ]
            if not any(record[2] == "tbl" for record in direct_records):
                table_anchor_pages = {
                    record[4]
                    for record in records_by_anchor_page
                    if record[2] == "tbl"
                }
                preceding_page = max(
                    (page for page in table_anchor_pages if page < target_page),
                    default=None,
                )
                following_page = min(
                    (page for page in table_anchor_pages if page > target_page),
                    default=None,
                )
                nearby_pages = {preceding_page, following_page} - {None}
                direct_records.extend(
                    record[:4]
                    for record in records_by_anchor_page
                    if record[2] == "tbl" and record[4] in nearby_pages
                )
            records_to_inspect = sorted(
                {record[0]: record for record in direct_records}.values(),
                key=lambda item: item[0],
            )

        for ordinal, control, ctrl_id, anchor in records_to_inspect:
            _ = ordinal
            reference = control_ref(
                document_id,
                ctrl_id,
                control_instance_id(control, guard),
            )
            if ctrl_id == "tbl":
                table = inspect_table_control(
                    hwp,
                    control,
                    reference,
                    anchor,
                    guard,
                    window_handle,
                )
                page_start, page_end = table.page_start, table.page_end
                if page_start <= target_page <= page_end:
                    tables.append(table)
            else:
                move_to_control(hwp, control, guard)
                page_start = hwp.current_page
                guard()
                page_end = page_start
            if page_start <= target_page <= page_end:
                user_description = control.UserDesc
                guard()
                controls.append(
                    StructureControl(
                        control_ref=reference,
                        ctrl_id=ctrl_id,
                        kind=control_kind(ctrl_id, user_description),
                        user_description=user_description,
                        anchor=structure_position(anchor),
                        page_start=page_start,
                        page_end=page_end,
                    )
                )
        caption_style_ids = {
            table.caption.style_id
            for table in tables
            if table.caption is not None and table.caption.style_id is not None
        }
        if caption_style_ids:
            style_names = {
                style.style_id: style.name
                for style in inspect_styles(hwp, guard).styles
            }
            tables = [
                table.model_copy(
                    update={
                        "caption": table.caption.model_copy(
                            update={
                                "style_name": style_names.get(table.caption.style_id)
                            }
                        )
                    }
                )
                if table.caption is not None
                and table.caption.style_id is not None
                else table
                for table in tables
            ]
        page_text = hwp.get_page_text(target_page - 1)[:200_000]
        guard()
        paragraphs = tuple(
            PageParagraph(index=index, text=text)
            for index, text in enumerate(page_text.splitlines())
        )
        page_count = hwp.PageCount
        guard()
        structure = DocumentStructure(
            selector=selector,
            document_id=document_id,
            full_name=full_name,
            window_handle=window_handle,
            page=target_page,
            page_count=page_count,
            state_token="0" * 64,
            page_text=page_text,
            paragraphs=paragraphs,
            controls=tuple(controls),
            tables=tuple(tables),
        )
    return structure.model_copy(update={"state_token": structure_token(structure)})

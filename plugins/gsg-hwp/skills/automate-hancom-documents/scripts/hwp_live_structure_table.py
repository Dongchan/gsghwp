from __future__ import annotations

from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import HwpControl, LiveHwpApplication
from hwp_live_control import (
    control_instance_id,
    enter_exact_table_control,
    require_exact_control_context,
    require_exact_table_cell_context,
    select_exact_control,
)
from hwp_live_native_batch import read_native_snapshot
from hwp_live_structure_contract import StructureTable
from hwp_live_structure_identity import structure_position, table_ref
from hwp_live_structure_xml import ParsedTable, parse_table_hwpml


def _table_xml(
    hwp: LiveHwpApplication,
    control: HwpControl,
    guard: Callable[[], None],
) -> str:
    _ = select_exact_control(hwp, control, guard)
    xml = hwp.get_text_file(format="HWPML2X", option="saveblock:true")
    guard()
    if not xml:
        raise HwpLiveError("한컴 표 구조 XML이 비어 있습니다")
    _ = hwp.Cancel()
    guard()
    return xml


def _caption_pages(
    hwp: LiveHwpApplication,
    control: HwpControl,
    window_handle: int | None,
    guard: Callable[[], None],
) -> tuple[int, int, int | None]:
    anchor = select_exact_control(hwp, control, guard)
    guard()
    table_list_id = hwp.get_pos()[0]
    guard()
    if not hwp.HAction.Run("ShapeObjAttachCaption"):
        raise HwpLiveError("기존 한컴 표 캡션에 들어가지 못했습니다")
    guard()
    caption_list_id = hwp.get_pos()[0]
    guard()
    if caption_list_id == table_list_id:
        raise HwpLiveError("기존 한컴 표 캡션 위치를 확인하지 못했습니다")
    try:
        require_exact_control_context(
            hwp,
            control,
            anchor,
            guard,
            expected_list_id=caption_list_id,
            reject_cell=True,
            error_message="기존 한컴 표 캡션 위치를 확인하지 못했습니다",
        )
        start = hwp.current_page
        guard()
        snapshot = (
            read_native_snapshot(window_handle)
            if window_handle is not None
            else None
        )
        if not hwp.MoveListEnd():
            raise HwpLiveError("기존 한컴 표 캡션 끝으로 이동하지 못했습니다")
        guard()
        end = hwp.current_page
        guard()
        return (
            min(start, end),
            max(start, end),
            None if snapshot is None else snapshot.style_id,
        )
    finally:
        require_exact_control_context(
            hwp,
            control,
            anchor,
            guard,
            expected_list_id=caption_list_id,
            reject_cell=True,
            error_message="기존 한컴 표 캡션 위치를 확인하지 못했습니다",
        )
        if not hwp.CloseEx():
            raise HwpLiveError("기존 한컴 표 캡션 읽기를 끝내지 못했습니다")
        guard()


def _table_pages(
    hwp: LiveHwpApplication,
    control: HwpControl,
    parsed: ParsedTable,
    window_handle: int | None,
    guard: Callable[[], None],
) -> tuple[int, int, int | None]:
    owners = tuple(cell.address for cell in parsed.cells if cell.address == cell.owner_address)
    if not owners:
        raise HwpLiveError("한컴 표에서 실제 셀을 찾지 못했습니다")
    boundary_addresses = owners[:1] if len(owners) == 1 else (owners[0], owners[-1])
    anchor = enter_exact_table_control(hwp, control, guard)
    pages: list[int] = []
    for address in boundary_addresses:
        guard()
        if not hwp.goto_addr(address):
            raise HwpLiveError(f"한컴 표의 {address} 셀로 이동하지 못했습니다")
        guard()
        require_exact_table_cell_context(
            hwp,
            control,
            anchor,
            guard,
            expected_address=address,
        )
        pages.append(hwp.current_page)
        guard()
        if not hwp.MoveListEnd():
            raise HwpLiveError(f"한컴 표의 {address} 셀 끝으로 이동하지 못했습니다")
        guard()
        require_exact_table_cell_context(
            hwp,
            control,
            anchor,
            guard,
            expected_address=address,
        )
        pages.append(hwp.current_page)
        guard()
    caption_style_id: int | None = None
    if parsed.caption is not None:
        caption_start, caption_end, caption_style_id = _caption_pages(
            hwp,
            control,
            window_handle,
            guard,
        )
        pages.extend((caption_start, caption_end))
    return min(pages), max(pages), caption_style_id


def _structure_table(
    parsed: ParsedTable,
    reference: str,
    instance_id: str,
    anchor: tuple[int, int, int],
    page_start: int,
    page_end: int,
) -> StructureTable:
    return StructureTable(
        table_ref=table_ref(reference),
        control_instance_id=instance_id,
        anchor=structure_position(anchor),
        page_start=page_start,
        page_end=page_end,
        rows=parsed.rows,
        columns=parsed.columns,
        merges=parsed.merges,
        cells=parsed.cells,
        caption=parsed.caption,
    )


def inspect_table_content(
    hwp: LiveHwpApplication,
    control: HwpControl,
    reference: str,
    anchor: tuple[int, int, int],
    page_start: int,
    page_end: int,
    guard: Callable[[], None],
) -> StructureTable:
    parsed = parse_table_hwpml(_table_xml(hwp, control, guard))
    return _structure_table(
        parsed,
        reference,
        control_instance_id(control, guard),
        anchor,
        page_start,
        page_end,
    )


def inspect_table_control(
    hwp: LiveHwpApplication,
    control: HwpControl,
    reference: str,
    anchor: tuple[int, int, int],
    guard: Callable[[], None],
    window_handle: int | None = None,
) -> StructureTable:
    parsed = parse_table_hwpml(_table_xml(hwp, control, guard))
    page_start, page_end, caption_style_id = _table_pages(
        hwp,
        control,
        parsed,
        window_handle,
        guard,
    )
    if parsed.caption is not None and caption_style_id is not None:
        parsed = ParsedTable(
            rows=parsed.rows,
            columns=parsed.columns,
            cells=parsed.cells,
            merges=parsed.merges,
            caption=parsed.caption.model_copy(
                update={"style_id": caption_style_id}
            ),
        )
    return _structure_table(
        parsed,
        reference,
        control_instance_id(control, guard),
        anchor,
        page_start,
        page_end,
    )

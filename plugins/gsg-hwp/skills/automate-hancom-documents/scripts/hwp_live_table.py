from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hwp_errors import HwpLiveError
from hwp_image_fit import fit_image_in_box
from hwp_live_api import HwpControl, LiveHwpApplication
from hwp_live_anchor import require_empty_paragraph
from hwp_live_caption import attach_table_caption
from hwp_live_control import (
    control_instance_ids,
    enter_exact_table_control,
    find_single_new_table_control,
    require_exact_table_cell_context,
)
from hwp_live_formatting import apply_paragraph_style, apply_text_style
from hwp_live_table_contract import TableBlock, TableCell
from hwp_live_table_format import apply_cell_geometry, apply_merges
from hwp_picture_placement import enforce_picture_size
from hwp_table_address import cell_address


def _base_style_key(name: str) -> int | str:
    compact = "".join(character.casefold() for character in name if character.isalnum())
    return 0 if compact in {"바탕글", "normal"} else name


def _insert_cell(
    hwp: LiveHwpApplication,
    cell: TableCell,
    assets: dict[Path, Path],
    base_style: str,
    guard: Callable[[], None],
) -> None:
    guard()
    if not hwp.set_style(_base_style_key(base_style)):
        raise HwpLiveError("한컴 표 셀의 기준 스타일을 초기화하지 못했습니다")
    guard()
    if cell.style_id is not None and not hwp.set_style(cell.style_id):
        raise HwpLiveError("한컴 표 셀 스타일을 적용하지 못했습니다")
    guard()
    apply_text_style(
        hwp,
        bold=cell.bold,
        font_name=cell.font_name,
        font_size_pt=cell.font_size_pt,
        text_color=cell.text_color,
    )
    guard()
    apply_paragraph_style(
        hwp,
        alignment=cell.alignment,
        line_spacing=cell.line_spacing_percent,
        left_margin=0 if cell.image_path is not None else None,
        right_margin=0 if cell.image_path is not None else None,
        indentation=0 if cell.image_path is not None else None,
    )
    guard()
    if cell.fill_color is not None and not hwp.cell_fill(cell.fill_color):
        raise HwpLiveError("표 셀 배경색을 적용하지 못했습니다")
    guard()
    apply_cell_geometry(hwp, cell, guard)
    guard()
    if cell.text and not hwp.insert_text(cell.text):
        raise HwpLiveError("표 셀 텍스트를 삽입하지 못했습니다")
    guard()
    if cell.image_path is None:
        return
    if cell.text and not hwp.BreakPara():
        raise HwpLiveError("표 셀 안에서 문단을 나누지 못했습니다")
    guard()
    width = cell.image_width_mm
    height = cell.image_height_mm
    if width is None or height is None:
        raise HwpLiveError("표 셀 그림 크기가 없습니다")
    guard()
    control = hwp.insert_picture(str(assets[cell.image_path]))
    guard()
    fitted_width, fitted_height = fit_image_in_box(
        assets[cell.image_path],
        width_mm=width,
        height_mm=height,
    )
    enforce_picture_size(
        hwp,
        control,
        width_mm=fitted_width,
        height_mm=fitted_height,
        guard=guard,
    )
    guard()


def insert_table(
    hwp: LiveHwpApplication,
    block: TableBlock,
    assets: dict[Path, Path],
    *,
    check_anchor: bool,
    guard: Callable[[], None],
) -> HwpControl:
    if check_anchor:
        require_empty_paragraph(hwp, guard)
    guard()
    before_control_ids = control_instance_ids(hwp, guard)
    base_style = block.base_style_name
    if base_style is None:
        raise HwpLiveError("표 기준 스타일이 자동 해석되지 않았습니다")
    if not hwp.set_style(_base_style_key(base_style)):
        raise HwpLiveError("한컴 표 기준 문단 스타일을 초기화하지 못했습니다")
    guard()
    apply_paragraph_style(
        hwp,
        alignment="left",
        left_margin=block.left_margin_mm,
        right_margin=block.right_margin_mm,
        indentation=block.indentation_mm,
    )
    guard()
    parent_position = hwp.get_pos()
    guard()
    row_count = len(block.rows)
    column_count = len(block.rows[0])
    if not hwp.create_table(
        rows=row_count,
        cols=column_count,
        treat_as_char=True,
        header=block.repeat_header,
    ):
        raise HwpLiveError("한컴 표를 만들지 못했습니다")
    guard()
    control = find_single_new_table_control(hwp, before_control_ids, guard)
    anchor = enter_exact_table_control(hwp, control, guard)

    def require_cell(expected_address: str | None = None) -> None:
        require_exact_table_cell_context(
            hwp,
            control,
            anchor,
            guard,
            expected_address=expected_address,
        )

    def table_guard() -> None:
        require_cell()

    require_cell("A1")
    if block.column_widths_mm is not None and not hwp.set_col_width(
        block.column_widths_mm,
        as_="mm",
    ):
        raise HwpLiveError("한컴 표 열 너비를 적용하지 못했습니다")
    require_cell("A1")
    for row_index, row in enumerate(block.rows):
        for column_index, cell in enumerate(row):
            address = cell_address(row_index, column_index)
            table_guard()
            if not hwp.goto_addr(address):
                raise HwpLiveError(f"한컴 표의 {address} 셀로 이동하지 못했습니다")
            require_cell(address)

            def cell_guard(expected_address: str = address) -> None:
                require_cell(expected_address)

            if (
                column_index == 0
                and block.row_heights_mm is not None
                and not hwp.set_row_height(
                    block.row_heights_mm[row_index],
                    as_="mm",
                )
            ):
                raise HwpLiveError("한컴 표 행 높이를 적용하지 못했습니다")
            cell_guard()
            _insert_cell(hwp, cell, assets, base_style, cell_guard)
            cell_guard()
    apply_merges(hwp, block, table_guard, require_cell)
    if block.caption is not None:
        guard()
        style_name = block.caption_style_name
        if style_name is None:
            raise HwpLiveError("표 캡션 스타일이 자동 해석되지 않았습니다")
        attach_table_caption(
            hwp,
            control,
            block.caption,
            style_name,
            guard,
        )
        guard()
    for _ in range(16):
        guard()
        current_list_id = hwp.get_pos()[0]
        guard()
        if current_list_id == parent_position[0]:
            break
        if not hwp.MoveParentList():
            raise HwpLiveError("한컴 표의 상위 본문으로 이동하지 못했습니다")
        guard()
    current_list_id = hwp.get_pos()[0]
    guard()
    if current_list_id != parent_position[0]:
        raise HwpLiveError("한컴 표의 원래 본문 위치를 찾지 못했습니다")
    if not hwp.set_pos(
        anchor[0],
        anchor[1],
        anchor[2] + 1,
    ):
        raise HwpLiveError("한컴 표 다음 위치로 이동하지 못했습니다")
    guard()
    if not hwp.BreakPara():
        raise HwpLiveError("한컴 표 다음 위치로 이동하지 못했습니다")
    guard()
    return control

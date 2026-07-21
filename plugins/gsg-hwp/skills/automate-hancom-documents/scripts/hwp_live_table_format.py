from __future__ import annotations

from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_formatting import apply_paragraph_style, hwp_color
from hwp_live_table_contract import BorderStyle, CellBorders, TableBlock, TableCell, TableMerge


_LINE_TYPES: dict[BorderStyle, str] = {
    "none": "None",
    "solid": "Solid",
    "dash": "Dash",
    "dot": "Dot",
    "dash_dot": "DashDot",
    "dash_dot_dot": "DashDotDot",
    "long_dash": "LongDash",
    "circle": "Circle",
    "double_slim": "DoubleSlim",
    "slim_thick": "SlimThick",
    "thick_slim": "ThickSlim",
    "slim_thick_slim": "SlimThickSlim",
}


def _apply_cell_borders(
    hwp: LiveHwpApplication,
    borders: CellBorders | None,
    guard: Callable[[], None],
) -> None:
    if borders is None:
        return
    parameters = hwp.HParameterSet.HCellBorderFill.HSet
    if not hwp.HAction.GetDefault("CellBorder", parameters):
        raise HwpLiveError("표 셀 테두리 기본값을 읽지 못했습니다")
    guard()
    sides = (
        ("Left", "BorderCorlorLeft", borders.left),
        ("Right", "BorderColorRight", borders.right),
        ("Top", "BorderColorTop", borders.top),
        ("Bottom", "BorderColorBottom", borders.bottom),
    )
    for side, color_key, border in sides:
        if border is None:
            continue
        parameters.SetItem("BorderType" + side, hwp.HwpLineType(_LINE_TYPES[border.style]))
        parameters.SetItem("BorderWidth" + side, hwp.HwpLineWidth(border.width))
        parameters.SetItem(color_key, hwp_color(border.color))
    guard()
    if not hwp.HAction.Execute("CellBorder", parameters):
        raise HwpLiveError("표 셀 테두리를 적용하지 못했습니다")
    guard()


def apply_cell_geometry(
    hwp: LiveHwpApplication,
    cell: TableCell,
    guard: Callable[[], None],
) -> None:
    padding = cell.padding
    if padding is not None and not hwp.set_cell_margin(
        padding.left_mm,
        padding.right_mm,
        padding.top_mm,
        padding.bottom_mm,
        as_="mm",
    ):
        raise HwpLiveError("표 셀 안 여백을 적용하지 못했습니다")
    guard()
    match cell.vertical_alignment:
        case "inherit":
            pass
        case "top":
            if not hwp.TableVAlignTop():
                raise HwpLiveError("표 셀 위쪽 정렬을 적용하지 못했습니다")
        case "center":
            if not hwp.TableVAlignCenter():
                raise HwpLiveError("표 셀 가운데 정렬을 적용하지 못했습니다")
        case "bottom":
            if not hwp.TableVAlignBottom():
                raise HwpLiveError("표 셀 아래쪽 정렬을 적용하지 못했습니다")
    guard()
    _apply_cell_borders(hwp, cell.borders, guard)


def _select_merge(
    hwp: LiveHwpApplication,
    merge: TableMerge,
    guard: Callable[[], None],
    require_cell: Callable[[str | None], None],
) -> None:
    def address(row: int, column: int) -> str:
        return f"{chr(65 + column)}{row + 1}"

    anchor_address = address(merge.row, merge.column)
    guard()
    if not hwp.goto_addr(anchor_address):
        raise HwpLiveError("한컴 병합 시작 셀로 이동하지 못했습니다")
    require_cell(anchor_address)
    if not hwp.TableCellBlock():
        raise HwpLiveError("한컴 병합 셀 선택을 시작하지 못했습니다")
    require_cell(anchor_address)
    if not hwp.TableCellBlockExtend():
        raise HwpLiveError("한컴 병합 셀 선택을 시작하지 못했습니다")
    require_cell(anchor_address)
    endpoint_column = merge.column
    for offset in range(1, merge.column_span):
        guard()
        if not hwp.TableRightCell():
            raise HwpLiveError("한컴 병합 열 범위를 선택하지 못했습니다")
        endpoint_column = merge.column + offset
        require_cell(address(merge.row, endpoint_column))
    for offset in range(1, merge.row_span):
        guard()
        if not hwp.TableLowerCell():
            raise HwpLiveError("한컴 병합 행 범위를 선택하지 못했습니다")
        require_cell(address(merge.row + offset, endpoint_column))


def apply_merges(
    hwp: LiveHwpApplication,
    block: TableBlock,
    guard: Callable[[], None],
    require_cell: Callable[[str | None], None],
) -> None:
    columns = len(block.rows[0])
    ordered = sorted(
        block.merges,
        key=lambda merge: merge.row * columns + merge.column,
        reverse=True,
    )
    for merge in ordered:
        _select_merge(hwp, merge, guard, require_cell)
        guard()
        if not hwp.TableMergeCell():
            raise HwpLiveError("한컴 표 셀을 병합하지 못했습니다")
        guard()
        anchor_address = f"{chr(65 + merge.column)}{merge.row + 1}"
        if not hwp.goto_addr(anchor_address):
            raise HwpLiveError("한컴 병합 셀로 다시 이동하지 못했습니다")
        require_cell(anchor_address)

        def anchor_guard() -> None:
            require_cell(anchor_address)

        anchor = block.rows[merge.row][merge.column]
        apply_paragraph_style(
            hwp,
            alignment=anchor.alignment,
            line_spacing=anchor.line_spacing_percent,
        )
        anchor_guard()
        if anchor.fill_color is not None and not hwp.cell_fill(anchor.fill_color):
            raise HwpLiveError("병합 셀 배경색을 다시 적용하지 못했습니다")
        anchor_guard()
        apply_cell_geometry(hwp, anchor, anchor_guard)
        anchor_guard()

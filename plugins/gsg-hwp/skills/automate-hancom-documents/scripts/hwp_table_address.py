from __future__ import annotations

from hwp_errors import HwpLiveError


def cell_address(row: int, column: int) -> str:
    if row < 0 or column < 0:
        raise HwpLiveError("표 셀 행과 열은 음수일 수 없습니다")
    letters = ""
    value = column + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"

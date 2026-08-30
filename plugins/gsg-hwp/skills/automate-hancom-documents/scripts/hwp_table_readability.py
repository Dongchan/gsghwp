from __future__ import annotations

import math
import re
from collections.abc import Sequence
from unicodedata import east_asian_width


_NUMBER = re.compile(
    r"^\s*(?:-|[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|원|억원|㎡|m²|m|km)?)\s*$",
    re.IGNORECASE,
)
# 아래 셋과 recommended_row_heights 의 9.0/6.0/2.0+4.0 은 출처가 기록된 적이
# 없다. 2026-08-23 감사에서 역산한 결과만 남긴다 — 고치지는 않았으므로, 읽는
# 쪽이 무엇을 믿고 있는지는 알 수 있어야 한다.
#
# _CHARACTER_WIDTH_MM 은 display_width 의 반각 단위당 폭이므로 한글 한 자는
# 3.4mm, 곧 1em = 9.64pt 인 글꼴을 가정한다. 10pt 와는 -3.6% 로 가깝지만
# 사용자 문서 test.hwp 의 지배 본문 11pt(문단의 56%)와는 -12.4% 다. 좁게
# 가정하면 한 줄에 글자가 더 들어간다고 보고 → 줄 수를 적게 세고 → 행 높이를
# 낮게 권한다. 안전한 쪽이 아니라 위험한 쪽이다.
#
# recommended_row_heights 의 4.0mm/줄은 역산하면 줄간격 100% 근처다(11pt 단행
# 3.88mm 대비 +3%). 같은 문서의 지배 줄간격은 200%(문단의 50%)이고 그 한 줄은
# 7.76mm 라, 여러 줄 셀에서 -48% 로 절반가량 모자란다. 본문 바닥 6.0mm 도
# 11pt/200% 한 줄 + 안쪽 여백(8.76mm)보다 31% 낮다. 머리행 9.0mm 만 그 한 줄과
# 얼추 맞는다.
#
# 이것이 사전 검사와 만나는 지점: 여기서 나온 값은 row_heights_mm 로 들어가고
# (hwp_public_document_tools·hwp_document_table_profile), hwp_layout_preflight
# ._table_estimate 는 row_heights_mm 가 있으면 그 표를 uncertain=False 로,
# 넘침 바닥(minimum)까지 그 숫자로 잡는다. 즉 근거 없는 계수에서 나온 높이가
# 사전 검사에서는 "확정된 치수" 자격을 얻는다.
_CHARACTER_WIDTH_MM = 1.7
_HORIZONTAL_PADDING_MM = 3.0
_MINIMUM_COLUMN_MM = 16.0


def display_width(value: str) -> int:
    return sum(
        2 if east_asian_width(character) in {"W", "F"} else 1
        for character in value
    )


def _line_width_mm(value: str) -> float:
    lines = value.replace("\r", "").split("\n")
    return max(
        (_CHARACTER_WIDTH_MM * display_width(line) + _HORIZONTAL_PADDING_MM for line in lines),
        default=_HORIZONTAL_PADDING_MM,
    )


def recommended_cell_width(value: str) -> float:
    text = value.strip()
    if not text:
        return 5.0
    if _NUMBER.fullmatch(text):
        return round(min(30.0, max(18.0, _line_width_mm(text))), 2)
    return round(min(48.0, max(_MINIMUM_COLUMN_MM, _line_width_mm(text))), 2)


def recommended_column_minimums(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
) -> tuple[float, ...]:
    columns = len(headers)
    if columns < 1 or any(len(row) != columns for row in rows):
        raise ValueError("table rows must match the header count")
    minimums: list[float] = []
    for column, header in enumerate(headers):
        values = tuple(row[column].strip() for row in rows if row[column].strip())
        numeric = bool(values) and all(_NUMBER.fullmatch(value) for value in values)
        header_width = min(38.0, max(_MINIMUM_COLUMN_MM, _line_width_mm(header)))
        if numeric:
            body_width = min(
                30.0,
                max(18.0, *(_line_width_mm(value) for value in values)),
            )
        elif values:
            body_width = min(
                48.0,
                max(20.0, *(_line_width_mm(value) for value in values)),
            )
        else:
            body_width = _MINIMUM_COLUMN_MM
        minimums.append(round(max(header_width, body_width), 2))
    return tuple(minimums)


def _estimated_lines(value: str, width_mm: float) -> int:
    capacity = max(1.0, (width_mm - _HORIZONTAL_PADDING_MM) / _CHARACTER_WIDTH_MM)
    return max(
        1,
        sum(
            max(1, math.ceil(display_width(line) / capacity))
            for line in value.replace("\r", "").split("\n")
        ),
    )


def recommended_row_heights(
    rows: Sequence[Sequence[str]],
    minimum_widths_mm: Sequence[float],
) -> tuple[float, ...]:
    if not rows:
        raise ValueError("table must contain at least one row")
    columns = len(minimum_widths_mm)
    if columns < 1 or any(len(row) != columns for row in rows):
        raise ValueError("table rows must match the width count")
    heights: list[float] = []
    for row_index, row in enumerate(rows):
        lines = max(
            _estimated_lines(value, minimum_widths_mm[column])
            for column, value in enumerate(row)
        )
        floor = 9.0 if row_index == 0 else 6.0
        heights.append(round(max(floor, 2.0 + 4.0 * lines), 2))
    return tuple(heights)

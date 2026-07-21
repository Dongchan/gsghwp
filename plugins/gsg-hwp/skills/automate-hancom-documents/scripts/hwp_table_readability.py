from __future__ import annotations

import math
import re
from collections.abc import Sequence
from unicodedata import east_asian_width


_NUMBER = re.compile(
    r"^\s*(?:-|[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|원|억원|㎡|m²|m|km)?)\s*$",
    re.IGNORECASE,
)
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

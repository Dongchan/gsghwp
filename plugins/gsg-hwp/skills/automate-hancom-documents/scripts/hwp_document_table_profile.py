from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_table_contract import TableBlock
from hwp_table_readability import recommended_row_heights


def _fit_weights(
    weights: tuple[float, ...],
    total_mm: float,
    minimums: tuple[float, ...] | None = None,
) -> tuple[float, ...]:
    floors = minimums or tuple(5.0 for _ in weights)
    if len(floors) != len(weights):
        raise HwpLiveError("표의 열 너비와 최소 가독 폭 개수가 일치하지 않습니다")
    if total_mm + 0.0001 < sum(floors):
        raise HwpLiveError("현재 문서 본문 폭으로는 표의 최소 가독 폭을 확보할 수 없습니다")
    fitted = [0.0] * len(weights)
    active = set(range(len(weights)))
    remaining = total_mm
    while active:
        active_total = sum(weights[index] for index in active)
        scaled = {
            index: remaining * weights[index] / active_total for index in active
        }
        narrow = {index for index, width in scaled.items() if width < floors[index]}
        if not narrow:
            for index, width in scaled.items():
                fitted[index] = width
            break
        for index in narrow:
            fitted[index] = floors[index]
        active.difference_update(narrow)
        remaining = total_mm - sum(fitted)
    correction = total_mm - sum(fitted)
    fitted[max(range(len(fitted)), key=fitted.__getitem__)] += correction
    return tuple(round(width, 4) for width in fitted)


def _slice_values(
    values: tuple[float, ...] | None,
    columns: tuple[int, ...],
) -> tuple[float, ...] | None:
    if values is None:
        return None
    return tuple(values[column] for column in columns)


def split_wide_table(
    block: TableBlock,
    available_mm: float,
) -> tuple[TableBlock, ...]:
    minimums = block.minimum_column_widths_mm
    if minimums is None or sum(minimums) <= available_mm + 0.0001:
        return (block,)
    if not block.split_wide_table:
        raise HwpLiveError(
            "표 가독성을 확보할 수 없을 만큼 열이 좁아집니다. 의미 단위로 열을 나누어야 합니다"
        )
    if block.merges:
        raise HwpLiveError(
            "병합 셀이 있는 넓은 표는 자동 압축하지 않습니다. 원본 병합 구역을 보존해 의미 단위 범위로 나누어야 합니다"
        )
    columns = len(block.rows[0])
    key_count = block.repeat_key_columns
    key_columns = tuple(range(key_count))
    key_width = sum(minimums[column] for column in key_columns)
    groups: list[tuple[int, ...]] = []
    current = list(key_columns)
    current_width = key_width
    for column in range(key_count, columns):
        width = minimums[column]
        if current[key_count:] and current_width + width > available_mm + 0.0001:
            groups.append(tuple(current))
            current = list(key_columns)
            current_width = key_width
        if current_width + width > available_mm + 0.0001:
            raise HwpLiveError(
                "반복 식별 열과 데이터 열 하나도 최소 가독 폭으로 배치할 수 없습니다"
            )
        current.append(column)
        current_width += width
    if current[key_count:]:
        groups.append(tuple(current))
    if len(groups) < 2:
        return (block,)
    parts: list[TableBlock] = []
    for index, group in enumerate(groups, start=1):
        caption = block.caption
        if caption is not None:
            caption = f"{caption[:1988]} ({index}/{len(groups)})"
        parts.append(
            block.model_copy(
                update={
                    "caption": caption,
                    "rows": tuple(
                        tuple(row[column] for column in group) for row in block.rows
                    ),
                    "column_widths_mm": _slice_values(block.column_widths_mm, group),
                    "column_width_weights": _slice_values(
                        block.column_width_weights,
                        group,
                    ),
                    "minimum_column_widths_mm": _slice_values(minimums, group),
                    "split_wide_table": False,
                    "repeat_key_columns": 0,
                }
            )
        )
    return tuple(parts)


def resolve_table_geometry(block: TableBlock, content_width_mm: float) -> TableBlock:
    available = content_width_mm - block.left_margin_mm - block.right_margin_mm
    minimums = block.minimum_column_widths_mm
    widths = block.column_widths_mm
    if widths is None:
        weights = block.column_width_weights or tuple(1.0 for _ in block.rows[0])
        widths = _fit_weights(weights, available, minimums)
    elif sum(widths) > available:
        widths = _fit_weights(widths, available, minimums)
    updates: dict[str, tuple[float, ...] | None] = {
        "column_widths_mm": widths,
        "column_width_weights": None,
    }
    if block.auto_fit_row_heights:
        text_rows = tuple(tuple(cell.text for cell in row) for row in block.rows)
        updates["row_heights_mm"] = recommended_row_heights(text_rows, widths)
    return block.model_copy(update=updates)

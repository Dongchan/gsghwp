from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_table_contract import TableBlock, TableMerge
from hwp_table_readability import recommended_row_heights


def _fit_weights(
    weights: tuple[float, ...],
    total_mm: float,
    minimums: tuple[float, ...] | None = None,
) -> tuple[float, ...]:
    # TableBlock's public geometry contract requires every explicit width to
    # remain at least 1 mm. This is a protocol invariant, not a visual default.
    floors = minimums or tuple(1.0 for _ in weights)
    if len(floors) != len(weights):
        raise HwpLiveError("표의 열 너비와 최소 가독 폭 개수가 일치하지 않습니다")
    if total_mm + 0.0001 < sum(floors):
        raise HwpLiveError(
            "현재 문서 본문 폭으로는 표의 최소 가독 폭을 확보할 수 없습니다"
        )
    fitted = [0.0] * len(weights)
    active = set(range(len(weights)))
    remaining = total_mm
    while active:
        active_total = sum(weights[index] for index in active)
        scaled = {index: remaining * weights[index] / active_total for index in active}
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


def _lift_full_width_title(block: TableBlock) -> TableBlock:
    if len(block.rows) < 2:
        # 유일한 행이 전폭 제목이면 승격 후 남는 표 몸통이 없다. 그대로
        # 두면 병합 분할 경로가 절단선 없음으로 거부해 안내한다.
        return block
    columns = len(block.rows[0])
    title_merge = next(
        (
            merge
            for merge in block.merges
            if merge.row == 0
            and merge.column == 0
            and merge.row_span == 1
            and merge.column_span == columns
        ),
        None,
    )
    if title_merge is None:
        return block
    title = block.rows[0][0].text.strip()
    if not title:
        return block
    if block.caption is not None and block.caption.strip() != title:
        raise HwpLiveError(
            "캡션과 전폭 제목 행이 서로 다른 넓은 표는 자동 분할하지 않습니다. "
            "분할하려면 제목 행을 캡션으로 승격해야 하는데 캡션이 이미 다른 "
            "텍스트를 담고 있어 제목이 소실됩니다 — 제목을 캡션에 합치거나 "
            "표를 의미 단위로 직접 나누어야 합니다"
        )
    heights = block.row_heights_mm[1:] if block.row_heights_mm is not None else None
    return block.model_copy(
        update={
            "caption": block.caption or title,
            "rows": block.rows[1:],
            "row_heights_mm": heights,
            "merges": tuple(
                merge.model_copy(update={"row": merge.row - 1})
                for merge in block.merges
                if merge.row > 0
            ),
        }
    )


def _safe_cut_after(block: TableBlock) -> tuple[bool, ...]:
    columns = len(block.rows[0])
    if columns < 2:
        return ()
    can_cut = [True] * (columns - 1)
    for merge in block.merges:
        for column in range(merge.column, merge.column + merge.column_span - 1):
            if 0 <= column < columns - 1:
                can_cut[column] = False
    return tuple(can_cut)


def _column_segments(block: TableBlock) -> tuple[tuple[int, int], ...]:
    columns = len(block.rows[0])
    cuts = _safe_cut_after(block)
    segments: list[tuple[int, int]] = []
    start = 0
    for column, allowed in enumerate(cuts):
        if allowed:
            segments.append((start, column))
            start = column + 1
    segments.append((start, columns - 1))
    return tuple(segments)


def _flatten_segments(
    segments: tuple[tuple[int, int], ...],
) -> tuple[int, ...]:
    columns: list[int] = []
    for start, end in segments:
        columns.extend(range(start, end + 1))
    return tuple(columns)


def _slice_merges(
    block: TableBlock, columns: tuple[int, ...]
) -> tuple[TableMerge, ...]:
    index_of = {column: index for index, column in enumerate(columns)}
    selected = set(columns)
    sliced: list[TableMerge] = []
    for merge in block.merges:
        covered = [
            column
            for column in range(merge.column, merge.column + merge.column_span)
            if column in selected
        ]
        if not covered:
            continue
        new_columns = [index_of[column] for column in covered]
        if new_columns != list(range(min(new_columns), max(new_columns) + 1)):
            continue
        sliced.append(
            merge.model_copy(
                update={
                    "column": min(new_columns),
                    "column_span": len(new_columns),
                }
            )
        )
    return tuple(sliced)


def _part_from_columns(block: TableBlock, columns: tuple[int, ...]) -> TableBlock:
    return block.model_copy(
        update={
            "rows": tuple(
                tuple(row[column] for column in columns) for row in block.rows
            ),
            "column_widths_mm": _slice_values(block.column_widths_mm, columns),
            "column_width_weights": _slice_values(block.column_width_weights, columns),
            "minimum_column_widths_mm": _slice_values(
                block.minimum_column_widths_mm, columns
            ),
            "merges": _slice_merges(block, columns),
            "split_wide_table": False,
            "repeat_key_columns": 0,
        }
    )


def _split_merged_at_safe_cuts(
    block: TableBlock,
    available_mm: float,
    minimums: tuple[float, ...],
) -> tuple[TableBlock, ...]:
    segments = _column_segments(block)
    if len(segments) < 2:
        raise HwpLiveError(
            "병합 셀이 있는 넓은 표는 자동 압축하지 않습니다. 원본 병합 구역을 보존해 의미 단위 범위로 나누어야 합니다"
        )

    def segment_width(start: int, end: int) -> float:
        return sum(minimums[start : end + 1])

    key = (segments[0],)
    key_width = segment_width(*segments[0])
    groups: list[tuple[tuple[int, int], ...]] = []
    current = key
    current_width = key_width
    for segment in segments[1:]:
        extra = segment_width(*segment)
        if len(current) > 1 and current_width + extra > available_mm + 0.0001:
            groups.append(current)
            current = (*key, segment)
            current_width = key_width + extra
        else:
            current = (*current, segment)
            current_width += extra
        if current_width > available_mm + 0.0001 and len(current) == 2:
            raise HwpLiveError(
                "반복 식별 열과 데이터 열 하나도 최소 가독 폭으로 배치할 수 없습니다"
            )
    if len(current) > 1:
        groups.append(current)
    if len(groups) < 2:
        return (block,)
    return tuple(
        _part_from_columns(block, _flatten_segments(group)) for group in groups
    )


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
    # 전폭 제목 행은 열을 가로지르므로 실제로 쪼갤 때에만 캡션으로 올린다.
    # 쪼갤 필요가 없는 표에서 올리면 제목 행과 그 테두리가 통째로 사라진다.
    lifted = _lift_full_width_title(block)
    if lifted.merges:
        return _split_merged_at_safe_cuts(lifted, available_mm, minimums)
    columns = len(lifted.rows[0])
    key_count = lifted.repeat_key_columns
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
        return (lifted,)
    return tuple(_part_from_columns(lifted, group) for group in groups)


def resolve_table_geometry(block: TableBlock, content_width_mm: float) -> TableBlock:
    available = content_width_mm - block.left_margin_mm - block.right_margin_mm
    weighted_width = (
        available
        if block.target_width_mm is None
        else min(block.target_width_mm, available)
    )
    minimums = block.minimum_column_widths_mm
    widths = block.column_widths_mm
    if widths is None:
        weights = block.column_width_weights
        if weights is None:
            return block
        widths = _fit_weights(weights, weighted_width, minimums)
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

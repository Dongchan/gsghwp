from __future__ import annotations

from statistics import median
from typing import cast

from PIL import Image

from hwp_reference_layout_contract import ReferenceLayoutBlock


def _sample_positions(start: int, end: int) -> tuple[int, ...]:
    if end <= start:
        return ()
    count = min(11, end - start)
    return tuple(
        min(end - 1, start + (end - start) * index // count)
        for index in range(count)
    )


def _matching_fill_run(
    image: Image.Image,
    fill: tuple[int, int, int],
    *,
    top: float,
    bottom: float,
    left: float,
    right: float,
    search_top: float,
    search_bottom: float,
) -> tuple[float, float] | None:
    width, height = image.size
    columns = _sample_positions(round(left * width), round(right * width))
    if len(columns) < 2:
        return None
    first = max(0, round(search_top * height))
    last = min(height, round(search_bottom * height))
    required = max(1, len(columns) // 5)
    matches: list[int] = []
    for row in range(first, last):
        count = sum(
            max(
                abs(channel - expected)
                for channel, expected in zip(
                    cast(
                        tuple[int, int, int],
                        image.getpixel((column, row)),
                    ),
                    fill,
                    strict=True,
                )
            )
            <= 45
            for column in columns
        )
        if count >= required:
            matches.append(row)
    groups: list[list[int]] = []
    for row in matches:
        if not groups or row > groups[-1][-1] + 1:
            groups.append([row])
        else:
            groups[-1].append(row)
    expected_height = bottom - top
    choices: list[tuple[float, float, float, float]] = []
    for group in groups:
        run_top = group[0] / height
        run_bottom = (group[-1] + 1) / height
        run_height = run_bottom - run_top
        overlap = max(0.0, min(bottom, run_bottom) - max(top, run_top))
        midpoint_distance = abs(
            (run_top + run_bottom - top - bottom) / 2
        )
        if (
            len(group) >= 3
            and expected_height * 0.4 <= run_height <= expected_height * 3
            and overlap >= expected_height * 0.3
        ):
            choices.append(
                (-overlap, midpoint_distance, run_top, run_bottom)
            )
    if not choices:
        return None
    _, _, run_top, run_bottom = min(choices)
    return run_top, run_bottom


def snap_filled_region_rows(
    image: Image.Image,
    block: ReferenceLayoutBlock,
    row_breakpoints: tuple[float, ...],
    column_breakpoints: tuple[float, ...],
) -> tuple[float, ...]:
    styles = {style.key: style for style in block.styles}
    candidates: dict[int, list[float]] = {}
    row_count = len(row_breakpoints) - 1
    for region in block.style_regions:
        fill = styles[region.style_key].fill_color
        if fill is None or min(fill) > 240:
            continue
        run = _matching_fill_run(
            image,
            fill,
            top=row_breakpoints[region.top],
            bottom=row_breakpoints[region.bottom],
            left=column_breakpoints[region.left],
            right=column_breakpoints[region.right],
            search_top=row_breakpoints[max(0, region.top - 1)],
            search_bottom=row_breakpoints[min(row_count, region.bottom + 1)],
        )
        if run is None:
            continue
        candidates.setdefault(region.top, []).append(run[0])
        candidates.setdefault(region.bottom, []).append(run[1])
    snapped = list(row_breakpoints)
    for boundary, values in candidates.items():
        if (
            boundary in (0, row_count)
            or max(values) - min(values) > 0.015
        ):
            continue
        value = median(values)
        if snapped[boundary - 1] < value < snapped[boundary + 1]:
            snapped[boundary] = value
    return tuple(snapped)

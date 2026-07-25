from __future__ import annotations

from typing import cast

from PIL import Image

from hwp_reference_layout_contract import ReferenceLayoutBlock


def _samples(start: int, end: int) -> tuple[int, ...]:
    if end <= start:
        return ()
    count = min(9, end - start)
    return tuple(
        min(end - 1, start + (end - start) * index // count)
        for index in range(count)
    )


def gap_overlaps_nonwhite_fill(
    image: Image.Image,
    block: ReferenceLayoutBlock,
    column_breakpoints: tuple[float, ...],
    *,
    boundary: int,
    spans: tuple[tuple[int, int], ...],
    start: int,
    end: int,
) -> bool:
    width, _ = image.size
    rows = _samples(start, end)
    if not rows:
        return False
    styles = {style.key: style for style in block.styles}
    for region in block.style_regions:
        fill = styles[region.style_key].fill_color
        if (
            fill is None
            or min(fill) > 240
            or boundary not in (region.top, region.bottom)
        ):
            continue
        for left, right in spans:
            overlap_left = max(left, region.left)
            overlap_right = min(right, region.right)
            if overlap_left >= overlap_right:
                continue
            columns = _samples(
                round(column_breakpoints[overlap_left] * width),
                round(column_breakpoints[overlap_right] * width),
            )
            if not columns:
                continue
            matches = sum(
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
                for row in rows
                for column in columns
            )
            if matches * 5 >= len(rows) * len(columns):
                return True
    return False

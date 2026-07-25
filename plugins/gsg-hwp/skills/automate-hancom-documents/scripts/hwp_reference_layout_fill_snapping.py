from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import cast

from PIL import Image

from hwp_reference_layout_contract import ReferenceLayoutBlock, StyleRegion
from hwp_reference_layout_gap_pixels import estimate_background_color


@dataclass(frozen=True, slots=True)
class FillRegionTarget:
    top: int
    left: int
    bottom: int
    right: int
    style_key: str


@dataclass(frozen=True, slots=True)
class FillColumnExtension:
    column: int
    position: float
    targets: tuple[FillRegionTarget, ...]


@dataclass(frozen=True, slots=True)
class FillColumnAnalysis:
    breakpoints: tuple[float, ...]
    extensions: tuple[FillColumnExtension, ...]


@dataclass(frozen=True, slots=True)
class _FillEvidence:
    region: StyleRegion
    left: float
    right: float


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
    search_left: float,
    search_right: float,
) -> tuple[float, float] | None:
    width, height = image.size
    rows = _sample_positions(round(top * height), round(bottom * height))
    if len(rows) < 2:
        return None
    expected_width = right - left
    search_padding = expected_width * 0.5
    first = max(
        0,
        round(min(search_left, left - search_padding) * width),
    )
    last = min(
        width,
        round(max(search_right, right + search_padding) * width),
    )
    matches: list[int] = []
    required = max(1, len(rows) // 5)
    for column in range(first, last):
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
            for row in rows
        )
        if count >= required:
            matches.append(column)
    groups: list[list[int]] = []
    for column in matches:
        if not groups or column > groups[-1][-1] + 1:
            groups.append([column])
        else:
            groups[-1].append(column)
    choices: list[tuple[float, float, float, float]] = []
    for group in groups:
        run_left = group[0] / width
        run_right = (group[-1] + 1) / width
        run_width = run_right - run_left
        overlap = max(0.0, min(right, run_right) - max(left, run_left))
        midpoint_distance = abs(
            (run_left + run_right - left - right) / 2
        )
        if (
            len(group) >= 3
            and expected_width * 0.4 <= run_width <= expected_width * 3
            and overlap >= expected_width * 0.3
        ):
            choices.append(
                (-overlap, midpoint_distance, run_left, run_right)
            )
    if not choices:
        return None
    _, _, run_left, run_right = min(choices)
    return run_left, run_right


def _evidence(
    image: Image.Image,
    block: ReferenceLayoutBlock,
    rows: tuple[float, ...],
    columns: tuple[float, ...],
) -> tuple[_FillEvidence, ...]:
    styles = {style.key: style for style in block.styles}
    column_count = len(columns) - 1
    result: list[_FillEvidence] = []
    for region in block.style_regions:
        fill = styles[region.style_key].fill_color
        if fill is None:
            continue
        run = _matching_fill_run(
            image,
            fill,
            top=rows[region.top],
            bottom=rows[region.bottom],
            left=columns[region.left],
            right=columns[region.right],
            search_left=columns[max(0, region.left - 1)],
            search_right=columns[min(column_count, region.right + 1)],
        )
        if run is not None:
            result.append(_FillEvidence(region, *run))
    return tuple(result)


def _matching_merge(
    block: ReferenceLayoutBlock,
    region: StyleRegion,
) -> bool:
    return any(
        merge.row == region.top
        and merge.column == region.left
        and merge.row_span == region.bottom - region.top
        and merge.column_span == region.right - region.left
        for merge in block.merges
    )


def _extensions(
    block: ReferenceLayoutBlock,
    evidence: tuple[_FillEvidence, ...],
    snapped: tuple[float, ...],
) -> tuple[FillColumnExtension, ...]:
    grouped: dict[int, list[_FillEvidence]] = {}
    column_count = len(snapped) - 1
    styles = {style.key: style for style in block.styles}
    for item in evidence:
        boundary = item.region.right
        fill = styles[item.region.style_key].fill_color
        if (
            boundary >= column_count
            or item.right <= snapped[boundary] + 0.015
            or item.right >= snapped[boundary + 1] - 0.001
            or not _matching_merge(block, item.region)
            or fill is None
            or min(fill) > 240
            or not any(
                anchor.row == item.region.top
                and anchor.column == item.region.left
                and len(anchor.text.strip()) <= 12
                for anchor in block.text_anchors
            )
            or any(
                item.region.top <= anchor.row < item.region.bottom
                and anchor.column == boundary
                for anchor in block.text_anchors
            )
        ):
            continue
        grouped.setdefault(boundary, []).append(item)
    result: list[FillColumnExtension] = []
    for boundary, items in grouped.items():
        positions = [item.right for item in items]
        if max(positions) - min(positions) > 0.01:
            continue
        result.append(
            FillColumnExtension(
                column=boundary,
                position=median(positions),
                targets=tuple(
                    FillRegionTarget(
                        top=item.region.top,
                        left=item.region.left,
                        bottom=item.region.bottom,
                        right=item.region.right,
                        style_key=item.region.style_key,
                    )
                    for item in items
                ),
            )
        )
    if column_count + len(result) > 50:
        return ()
    return tuple(sorted(result, key=lambda item: item.column, reverse=True))


def analyze_filled_region_columns(
    image: Image.Image,
    block: ReferenceLayoutBlock,
    row_breakpoints: tuple[float, ...],
    column_breakpoints: tuple[float, ...],
) -> FillColumnAnalysis:
    evidence = _evidence(
        image,
        block,
        row_breakpoints,
        column_breakpoints,
    )
    background = estimate_background_color(image)
    styles = {style.key: style for style in block.styles}
    candidates: dict[int, list[float]] = {}
    for item in evidence:
        fill = styles[item.region.style_key].fill_color
        if (
            fill is None
            or max(
                abs(channel - expected)
                for channel, expected in zip(
                    fill,
                    background,
                    strict=True,
                )
            )
            <= 45
        ):
            continue
        candidates.setdefault(item.region.left, []).append(item.left)
        candidates.setdefault(item.region.right, []).append(item.right)
    snapped = list(column_breakpoints)
    column_count = len(snapped) - 1
    for boundary, values in candidates.items():
        if (
            boundary in (0, column_count)
            or max(values) - min(values) > 0.015
        ):
            continue
        value = median(values)
        local_extent = max(
            snapped[boundary] - snapped[boundary - 1],
            snapped[boundary + 1] - snapped[boundary],
        )
        if (
            abs(value - snapped[boundary])
            <= max(2 / image.size[0], local_extent * 0.75)
            and snapped[boundary - 1] < value < snapped[boundary + 1]
        ):
            snapped[boundary] = value
    breakpoints = tuple(snapped)
    return FillColumnAnalysis(
        breakpoints=breakpoints,
        extensions=_extensions(block, evidence, breakpoints),
    )

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from PIL import Image, ImageChops, ImageStat

from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_gap_row_fill_guard import (
    gap_overlaps_nonwhite_fill,
)


@dataclass(frozen=True, slots=True)
class RowGapInsertion:
    boundary: int
    start: float
    end: float
    spans: tuple[tuple[int, int], ...]
    duplicate_edge: bool


@dataclass(frozen=True, slots=True)
class RowGapScan:
    image: Image.Image
    block: ReferenceLayoutBlock
    row_breakpoints: tuple[float, ...]
    column_breakpoints: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _RowEvidence:
    position: int
    activity: float
    variance: float
    color: tuple[float, float, float]


def _occupied_rows(block: ReferenceLayoutBlock) -> tuple[frozenset[int], ...]:
    occupied = [set[int]() for _ in range(len(block.row_breakpoints) - 1)]
    styles = {style.key: style for style in block.styles}
    merges = {(merge.row, merge.column): merge for merge in block.merges}
    for anchor in block.text_anchors:
        merge = merges.get((anchor.row, anchor.column))
        row_span = 1 if merge is None else merge.row_span
        column_span = 1 if merge is None else merge.column_span
        for row in range(anchor.row, anchor.row + row_span):
            occupied[row].update(
                range(anchor.column, anchor.column + column_span)
            )
    for region in block.style_regions:
        if styles[region.style_key].fill_color is None:
            continue
        for row in range(region.top, region.bottom):
            occupied[row].update(range(region.left, region.right))
    return tuple(frozenset(columns) for columns in occupied)


def _candidate_spans(
    block: ReferenceLayoutBlock,
    boundary: int,
    occupied: tuple[frozenset[int], ...],
) -> tuple[tuple[int, int], ...]:
    marker_rows = {
        anchor.row
        for anchor in block.text_anchors
        if (
            len(anchor.text.strip()) == 1
            and not anchor.text.strip().isalnum()
        )
    }
    if boundary - 1 in marker_rows or boundary in marker_rows:
        return ()
    merged_columns = {
        column
        for merge in block.merges
        if merge.row < boundary < merge.row + merge.row_span
        for column in range(
            merge.column,
            merge.column + merge.column_span,
        )
    }
    columns = sorted(
        occupied[boundary - 1]
        .intersection(occupied[boundary])
        .difference(merged_columns)
    )
    groups: list[list[int]] = []
    for column in columns:
        if not groups or column != groups[-1][-1] + 1:
            groups.append([column])
        else:
            groups[-1].append(column)
    return tuple((group[0], group[-1] + 1) for group in groups)


def _pixel_spans(
    scan: RowGapScan,
    spans: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    width, _ = scan.image.size
    pixels: list[tuple[int, int]] = []
    for left, right in spans:
        start = round(scan.column_breakpoints[left] * width)
        end = round(scan.column_breakpoints[right] * width)
        inset = max(1, (end - start) // 20)
        if start + inset < end - inset:
            pixels.append((start + inset, end - inset))
    return tuple(pixels)


def _row_profile(
    scan: RowGapScan,
    boundary: int,
    spans: tuple[tuple[int, int], ...],
) -> tuple[tuple[_RowEvidence, ...], int, int]:
    _, height = scan.image.size
    center = round(scan.row_breakpoints[boundary] * height)
    adjacent = min(
        (scan.row_breakpoints[boundary] - scan.row_breakpoints[boundary - 1])
        * height,
        (scan.row_breakpoints[boundary + 1] - scan.row_breakpoints[boundary])
        * height,
    )
    radius = max(6, min(48, round(adjacent * 0.65)))
    pixel_spans = _pixel_spans(scan, spans)
    evidence: list[_RowEvidence] = []
    for position in range(
        max(1, center - radius),
        min(height - 1, center + radius + 1),
    ):
        activities: list[float] = []
        variances: list[float] = []
        colors: list[tuple[float, float, float]] = []
        for left, right in pixel_spans:
            current = scan.image.crop((left, position, right, position + 1))
            previous = scan.image.crop(
                (left, position - 1, right, position)
            )
            activities.append(
                sum(ImageStat.Stat(ImageChops.difference(current, previous)).mean)
                / 3
            )
            statistics = ImageStat.Stat(current)
            variances.append(sum(statistics.var) / 3)
            colors.append(
                (
                    statistics.mean[0],
                    statistics.mean[1],
                    statistics.mean[2],
                )
            )
        if activities:
            evidence.append(
                _RowEvidence(
                    position=position,
                    activity=median(activities),
                    variance=median(variances),
                    color=(
                        median(color[0] for color in colors),
                        median(color[1] for color in colors),
                        median(color[2] for color in colors),
                    ),
                )
            )
    return tuple(evidence), center, radius


def _activity_bands(
    profile: tuple[_RowEvidence, ...],
) -> tuple[tuple[int, int], ...]:
    active = [item.position for item in profile if item.activity >= 4]
    bands: list[list[int]] = []
    for position in active:
        if not bands or position > bands[-1][-1] + 1:
            bands.append([position])
        else:
            bands[-1].append(position)
    return tuple((band[0], band[-1] + 1) for band in bands)


def _dark_neutral_coverage(
    scan: RowGapScan,
    spans: tuple[tuple[int, int], ...],
    position: int,
) -> float:
    dark = 0
    total = 0
    for left, right in _pixel_spans(scan, spans):
        payload = scan.image.crop(
            (left, position, right, position + 1)
        ).convert("RGB").tobytes()
        for offset in range(0, len(payload), 3):
            red, green, blue = payload[offset : offset + 3]
            total += 1
            if (
                max(red, green, blue) <= 190
                and max(red, green, blue) - min(red, green, blue) <= 24
            ):
                dark += 1
    return dark / max(1, total)


def _best_gap(
    scan: RowGapScan,
    boundary: int,
    spans: tuple[tuple[int, int], ...],
) -> RowGapInsertion | None:
    profile, center, radius = _row_profile(scan, boundary, spans)
    declared_boundary = any(
        edge.orientation == "horizontal"
        and edge.line == boundary
        and any(
            edge.start < right and left < edge.end
            for left, right in spans
        )
        for edge in scan.block.visible_edges
    )
    if (
        declared_boundary
        and max(
            _dark_neutral_coverage(scan, spans, position)
            for position in range(
                max(0, center - 2),
                min(scan.image.height, center + 3),
            )
        )
        >= 0.35
    ):
        return None
    by_position = {item.position: item for item in profile}
    maximum_distance = max(4, min(18, round(radius * 0.46)))
    candidates: list[tuple[float, float, float, int, int]] = []
    bands = _activity_bands(profile)
    for first, second in zip(bands, bands[1:], strict=False):
        start = first[1]
        end = second[0]
        if end - start < 2:
            continue
        interior = [
            by_position[position]
            for position in range(start, end)
            if position in by_position
        ]
        if not interior:
            continue
        distance = abs((start + end) / 2 - center)
        variance = median(item.variance for item in interior)
        activity = median(item.activity for item in interior)
        color_range = max(
            max(item.color[channel] for item in interior)
            - min(item.color[channel] for item in interior)
            for channel in range(3)
        )
        if (
            distance <= maximum_distance
            and variance <= 250
            and activity <= 3
            and color_range <= 18
            and not gap_overlaps_nonwhite_fill(
                scan.image,
                scan.block,
                scan.column_breakpoints,
                boundary=boundary,
                spans=spans,
                start=start,
                end=end,
            )
        ):
            candidates.append((variance, activity, distance, start, end))
    if not candidates:
        return None
    _, _, _, start, end = min(candidates)
    _, height = scan.image.size
    return RowGapInsertion(
        boundary=boundary,
        start=start / height,
        end=end / height,
        spans=spans,
        duplicate_edge=_dark_neutral_coverage(scan, spans, end) >= 0.35,
    )


def detect_row_gap_insertions(
    scan: RowGapScan,
) -> tuple[RowGapInsertion, ...]:
    occupied = _occupied_rows(scan.block)
    insertions = tuple(
        insertion
        for boundary in range(1, len(scan.row_breakpoints) - 1)
        if (
            insertion := _best_gap(
                scan,
                boundary,
                _candidate_spans(scan.block, boundary, occupied),
            )
        )
        is not None
    )
    if len(scan.row_breakpoints) + len(insertions) > 51:
        raise ValueError("detected row gaps exceed the 50-row HWP limit")
    return insertions

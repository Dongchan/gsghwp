#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "pdfplumber==0.11.9",
#     "pydantic==2.12.5",
#     "pymupdf==1.28.0",
#     "rich==14.3.2",
#     "typer==0.23.1",
# ]
# ///

# ─── How to run ───
# 1. Install uv through your organization's approved package source.
# 2. Extract one PDF page to JSON and print its vector summary:
#      uv run pdf_vector_extract.py extract source.pdf --output page.json --page 1
# ──────────────────

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Annotated, Final, Literal, final, override

import pdfplumber
import pymupdf
import typer
from hwp_live_table_contract import (
    BORDER_WIDTH_SNAP_TARGETS,
    snap_pdf_linewidth,
)
from pdf_vector_contract import (
    AxisSegment,
    CoordinateCluster,
    ExtractionReport,
    JsonInput,
    JsonObject,
    MuPdfDrawing,
    MuPdfObservation,
    PlumberPage,
    PlumberObservation,
    RectGeometry,
    ThinRectObservation,
    as_mupdf_page,
    drawing_json,
    frequency,
    json_float,
    matrix_values,
    parse_mupdf_drawing,
    parse_mupdf_page_metadata,
    point_values,
    pt_to_hwpunit,
    quad_values,
    rect_gaps,
    rect_size,
    selected_fields,
)
from rich.console import Console
from typer.models import OptionInfo


app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()

_ROUND_DIGITS: Final = 6
_WIDTH_MATCH_TOLERANCE_PT: Final = 0.001
_COORDINATE_TOLERANCE_PT: Final = 0.5
_AXIS_TOLERANCE_PT: Final = 0.5
_EDGE_COVERAGE_THRESHOLD: Final = 0.95
_EDGE_MAX_UNCOVERED_GAP_PT: Final = 0.5
_BEZIER_STRAIGHTNESS_TOLERANCE_PT: Final = 0.5
_THIN_RECT_MIN_ASPECT_RATIO: Final = 8.0
_THIN_RECT_SHORT_LENGTH_PT: Final = 6.0
_THIN_RECT_MATCH_TOLERANCE_PT: Final = 0.001
_VECTOR_FIELDS: Final[tuple[str, ...]] = tuple(
    (
        "object_type x0 x1 y0 y1 top bottom width height linewidth "
        + "stroking_color non_stroking_color dash stroke fill path pts"
    ).split()
)
_CHAR_FIELDS: Final[tuple[str, ...]] = tuple(
    "text fontname size x0 top upright".split()
)
_IMAGE_FIELDS: Final[tuple[str, ...]] = tuple("x0 x1 top bottom width height".split())
_OUTPUT_OPTION: Final[OptionInfo] = OptionInfo(
    default=...,
    param_decls=(),
    help="추출 결과 JSON 경로",
)
_PAGE_OPTION: Final[OptionInfo] = OptionInfo(
    default=...,
    param_decls=(),
    min=1,
    help="추출할 1-based 쪽 번호",
)


@final
@dataclass(frozen=True, slots=True)
class PdfVectorExtractError(Exception):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def _point(value: JsonInput) -> tuple[float, float]:
    return point_values(value)


def _color(value: JsonInput | None) -> tuple[float, ...] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return (float(value),)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(json_float(component) for component in value)
    return None


def _drawing_has_stroke(drawing: MuPdfDrawing) -> bool:
    return "s" in drawing.type


def _drawing_has_fill(drawing: MuPdfDrawing) -> bool:
    return "f" in drawing.type


def _axis_segment(
    start: JsonInput,
    end: JsonInput,
    *,
    linewidth_pt: float,
    color: tuple[float, ...] | None,
    source: str,
    source_kind: Literal["line", "rectangle", "quad", "bezier", "thin_rect"],
    paint_order: int,
) -> AxisSegment | None:
    x0, y0 = _point(start)
    x1, y1 = _point(end)
    delta_x = abs(x1 - x0)
    delta_y = abs(y1 - y0)
    if delta_x <= _AXIS_TOLERANCE_PT and delta_y > _AXIS_TOLERANCE_PT:
        return AxisSegment(
            orientation="vertical",
            coordinate=(x0 + x1) / 2,
            start=min(y0, y1),
            end=max(y0, y1),
            linewidth_pt=linewidth_pt,
            color=color,
            source=source,
            source_kind=source_kind,
            paint_order=paint_order,
        )
    if delta_y <= _AXIS_TOLERANCE_PT and delta_x > _AXIS_TOLERANCE_PT:
        return AxisSegment(
            orientation="horizontal",
            coordinate=(y0 + y1) / 2,
            start=min(x0, x1),
            end=max(x0, x1),
            linewidth_pt=linewidth_pt,
            color=color,
            source=source,
            source_kind=source_kind,
            paint_order=paint_order,
        )
    return None


def _rectangle_axis_segments(
    rectangle: RectGeometry,
    *,
    linewidth_pt: float,
    color: tuple[float, ...] | None,
    source: str,
    paint_order: int,
) -> tuple[AxisSegment, ...]:
    points = (
        (rectangle.x0, rectangle.top),
        (rectangle.x1, rectangle.top),
        (rectangle.x1, rectangle.bottom),
        (rectangle.x0, rectangle.bottom),
    )
    segments = tuple(
        segment
        for index in range(4)
        if (
            segment := _axis_segment(
                points[index],
                points[(index + 1) % 4],
                linewidth_pt=linewidth_pt,
                color=color,
                source=f"{source}:side:{index}",
                source_kind="rectangle",
                paint_order=paint_order,
            )
        )
        is not None
    )
    return segments


def _quad_points(value: JsonInput) -> tuple[JsonInput, ...]:
    if isinstance(value, pymupdf.Quad):
        return quad_values(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _straight_bezier_segment(
    item: tuple[JsonInput, ...],
    *,
    linewidth_pt: float,
    color: tuple[float, ...] | None,
    source: str,
    paint_order: int,
) -> AxisSegment | None:
    if len(item) != 5:
        return None
    start_x, start_y = _point(item[1])
    control1_x, control1_y = _point(item[2])
    control2_x, control2_y = _point(item[3])
    end_x, end_y = _point(item[4])
    delta_x = abs(end_x - start_x)
    delta_y = abs(end_y - start_y)
    tolerance = _BEZIER_STRAIGHTNESS_TOLERANCE_PT
    if delta_y <= _AXIS_TOLERANCE_PT and delta_x > _AXIS_TOLERANCE_PT:
        line_y = (start_y + end_y) / 2
        minimum = min(start_x, end_x) - tolerance
        maximum = max(start_x, end_x) + tolerance
        is_straight = (
            abs(control1_y - line_y) <= tolerance
            and abs(control2_y - line_y) <= tolerance
            and minimum <= control1_x <= maximum
            and minimum <= control2_x <= maximum
        )
    elif delta_x <= _AXIS_TOLERANCE_PT and delta_y > _AXIS_TOLERANCE_PT:
        line_x = (start_x + end_x) / 2
        minimum = min(start_y, end_y) - tolerance
        maximum = max(start_y, end_y) + tolerance
        is_straight = (
            abs(control1_x - line_x) <= tolerance
            and abs(control2_x - line_x) <= tolerance
            and minimum <= control1_y <= maximum
            and minimum <= control2_y <= maximum
        )
    else:
        is_straight = False
    if not is_straight:
        return None
    return _axis_segment(
        item[1],
        item[4],
        linewidth_pt=linewidth_pt,
        color=color,
        source=source,
        source_kind="bezier",
        paint_order=paint_order,
    )


def _cluster_coordinates(
    values: Sequence[float],
    *,
    tolerance_pt: float,
) -> tuple[CoordinateCluster, ...]:
    ordered = sorted(values)
    if not ordered:
        return ()
    groups: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        current = groups[-1]
        if value - current[0] <= tolerance_pt:
            current.append(value)
        else:
            groups.append([value])
    return tuple(
        CoordinateCluster(
            coordinate=float(median(group)),
            minimum=group[0],
            maximum=group[-1],
            member_count=len(group),
        )
        for group in groups
    )


def _thin_rect_segment(observation: ThinRectObservation) -> AxisSegment:
    rectangle = observation.geometry
    if rectangle.width >= rectangle.height:
        orientation = "horizontal"
        coordinate = (rectangle.top + rectangle.bottom) / 2
        start = rectangle.x0
        end = rectangle.x1
    else:
        orientation = "vertical"
        coordinate = (rectangle.x0 + rectangle.x1) / 2
        start = rectangle.top
        end = rectangle.bottom
    return AxisSegment(
        orientation=orientation,
        coordinate=coordinate,
        start=start,
        end=end,
        linewidth_pt=rectangle.thickness,
        color=observation.color,
        source=observation.source,
        source_kind="thin_rect",
        paint_order=observation.paint_order,
    )


def _colors_match(
    first: tuple[float, ...] | None,
    second: tuple[float, ...] | None,
) -> bool:
    if first is None or second is None or len(first) != len(second):
        return False
    return all(abs(left - right) <= 0.01 for left, right in zip(first, second))


def _thin_rect_support(
    candidate: AxisSegment,
    segments: Sequence[AxisSegment],
) -> tuple[int, int, int]:
    endpoints = (candidate.start, candidate.end)
    endpoint_supported = [False, False]
    same_color_support: set[str] = set()
    collinear_support: set[str] = set()
    for segment in segments:
        if segment.source == candidate.source:
            continue
        if segment.orientation != candidate.orientation:
            for index, endpoint in enumerate(endpoints):
                if (
                    abs(segment.coordinate - endpoint) <= _COORDINATE_TOLERANCE_PT
                    and segment.start - _COORDINATE_TOLERANCE_PT
                    <= candidate.coordinate
                    <= segment.end + _COORDINATE_TOLERANCE_PT
                ):
                    endpoint_supported[index] = True
                    if _colors_match(candidate.color, segment.color):
                        same_color_support.add(segment.source)
            continue
        if abs(segment.coordinate - candidate.coordinate) > _COORDINATE_TOLERANCE_PT:
            continue
        if (
            segment.end + _COORDINATE_TOLERANCE_PT >= candidate.start
            and candidate.end + _COORDINATE_TOLERANCE_PT >= segment.start
        ):
            collinear_support.add(segment.source)
            if _colors_match(candidate.color, segment.color):
                same_color_support.add(segment.source)
    return (
        sum(endpoint_supported),
        len(collinear_support),
        len(same_color_support),
    )


def _rect_coordinate_difference(
    first: RectGeometry,
    second: RectGeometry,
) -> float:
    return max(
        abs(first.x0 - second.x0),
        abs(first.top - second.top),
        abs(first.x1 - second.x1),
        abs(first.bottom - second.bottom),
    )


def _reconcile_thin_rectangles(
    plumber: Sequence[ThinRectObservation],
    mupdf: Sequence[ThinRectObservation],
) -> tuple[
    tuple[ThinRectObservation, ...],
    tuple[JsonObject, ...],
    tuple[ThinRectObservation, ...],
    float | None,
]:
    remaining = list(mupdf)
    matches: list[JsonObject] = []
    unmatched: list[ThinRectObservation] = []
    differences: list[float] = []
    for plumber_candidate in plumber:
        if not remaining:
            unmatched.append(plumber_candidate)
            continue
        closest = min(
            remaining,
            key=lambda candidate: _rect_coordinate_difference(
                plumber_candidate.geometry,
                candidate.geometry,
            ),
        )
        difference = _rect_coordinate_difference(
            plumber_candidate.geometry,
            closest.geometry,
        )
        if difference > _THIN_RECT_MATCH_TOLERANCE_PT:
            unmatched.append(plumber_candidate)
            continue
        remaining.remove(closest)
        matches.append(
            {
                "pdfplumber_source": plumber_candidate.source,
                "pymupdf_source": closest.source,
                "maximum_coordinate_difference_pt": round(
                    difference,
                    _ROUND_DIGITS,
                ),
            }
        )
        differences.append(difference)
    maximum_difference = max(differences, default=None)
    return tuple(mupdf), tuple(matches), tuple(unmatched), maximum_difference


def _stroke_axis_segments(
    drawings: Sequence[MuPdfDrawing],
) -> tuple[tuple[AxisSegment, ...], JsonObject]:
    segments: list[AxisSegment] = []
    total_beziers = 0
    stroke_beziers = 0
    straight_beziers = 0
    for drawing_index, drawing in enumerate(drawings):
        has_stroke = _drawing_has_stroke(drawing)
        linewidth = float(drawing.width or 0)
        stroke_color = drawing.color
        for item_index, item in enumerate(drawing.items):
            kind = str(item[0])
            source = f"path:{drawing_index}:item:{item_index}"
            if kind == "c":
                total_beziers += 1
                if not has_stroke:
                    continue
                stroke_beziers += 1
                segment = _straight_bezier_segment(
                    item,
                    linewidth_pt=linewidth,
                    color=stroke_color,
                    source=source,
                    paint_order=drawing.seqno,
                )
                if segment is not None:
                    segments.append(segment)
                    straight_beziers += 1
                continue
            if not has_stroke:
                continue
            if kind == "l" and len(item) >= 3:
                segment = _axis_segment(
                    item[1],
                    item[2],
                    linewidth_pt=linewidth,
                    color=stroke_color,
                    source=source,
                    source_kind="line",
                    paint_order=drawing.seqno,
                )
                if segment is not None:
                    segments.append(segment)
            elif kind == "re" and len(item) >= 2:
                segments.extend(
                    _rectangle_axis_segments(
                        RectGeometry.from_pymupdf(item[1]),
                        linewidth_pt=linewidth,
                        color=stroke_color,
                        source=source,
                        paint_order=drawing.seqno,
                    )
                )
            elif kind == "qu" and len(item) >= 2:
                points = _quad_points(item[1])
                if len(points) != 4:
                    continue
                for side_index in range(4):
                    segment = _axis_segment(
                        points[side_index],
                        points[(side_index + 1) % 4],
                        linewidth_pt=linewidth,
                        color=stroke_color,
                        source=f"{source}:side:{side_index}",
                        source_kind="quad",
                        paint_order=drawing.seqno,
                    )
                    if segment is not None:
                        segments.append(segment)
    policy: JsonObject = {
        "total_bezier_count": total_beziers,
        "fill_only_bezier_count": total_beziers - stroke_beziers,
        "stroke_bezier_count": stroke_beziers,
        "straight_approximation_count": straight_beziers,
        "curved_excluded_count": stroke_beziers - straight_beziers,
        "straightness_tolerance_pt": _BEZIER_STRAIGHTNESS_TOLERANCE_PT,
        "rule": (
            "ignore fill-only paths; approximate only stroke cubics whose endpoints "
            "are axis-aligned and whose controls stay within tolerance of the chord"
        ),
    }
    return tuple(segments), policy


def _coverage(
    segments: Sequence[AxisSegment],
    *,
    orientation: str,
    coordinate: float,
    start: float,
    end: float,
) -> tuple[float, float, float, tuple[AxisSegment, ...]]:
    contributors: list[tuple[float, float, AxisSegment]] = []
    for segment in segments:
        if (
            segment.orientation != orientation
            or abs(segment.coordinate - coordinate) > _COORDINATE_TOLERANCE_PT
        ):
            continue
        overlap_start = max(start, segment.start)
        overlap_end = min(end, segment.end)
        if overlap_start < overlap_end:
            contributors.append((overlap_start, overlap_end, segment))
    if not contributors:
        return 0.0, 0.0, end - start, ()
    intervals = sorted((left, right) for left, right, _ in contributors)
    merged: list[tuple[float, float]] = []
    for left, right in intervals:
        if not merged or left > merged[-1][1]:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
    covered = sum(right - left for left, right in merged)
    interval_length = end - start
    ratio = covered / interval_length if interval_length > 0 else 0.0
    uncovered_gaps = [max(0.0, merged[0][0] - start)]
    uncovered_gaps.extend(
        max(0.0, right[0] - left[1]) for left, right in zip(merged, merged[1:])
    )
    uncovered_gaps.append(max(0.0, end - merged[-1][1]))
    maximum_uncovered_gap = max(uncovered_gaps, default=0.0)
    unique = tuple(dict.fromkeys(segment for _, _, segment in contributors))
    return ratio, covered, maximum_uncovered_gap, unique


def _dominant_linewidth(
    segments: Sequence[AxisSegment],
    *,
    start: float,
    end: float,
) -> float:
    boundaries = {start, end}
    for segment in segments:
        boundaries.add(max(start, segment.start))
        boundaries.add(min(end, segment.end))
    ordered = sorted(boundaries)
    overlap_by_width: dict[float, float] = {}
    latest_by_width: dict[float, int] = {}
    for left, right in zip(ordered, ordered[1:]):
        if left >= right:
            continue
        midpoint = (left + right) / 2
        covering = tuple(
            segment for segment in segments if segment.start <= midpoint <= segment.end
        )
        if not covering:
            continue
        visible = max(covering, key=lambda segment: segment.paint_order)
        width = round(visible.linewidth_pt, _ROUND_DIGITS)
        overlap_by_width[width] = overlap_by_width.get(width, 0.0) + right - left
        latest_by_width[width] = max(
            latest_by_width.get(width, visible.paint_order),
            visible.paint_order,
        )
    return min(
        overlap_by_width,
        key=lambda width: (
            -overlap_by_width[width],
            -latest_by_width[width],
            width,
        ),
    )


def _grid_edges(
    segments: Sequence[AxisSegment],
    column_clusters: Sequence[CoordinateCluster],
    row_clusters: Sequence[CoordinateCluster],
) -> tuple[JsonObject, ...]:
    edges: list[JsonObject] = []
    columns = tuple(cluster.coordinate for cluster in column_clusters)
    rows = tuple(cluster.coordinate for cluster in row_clusters)
    for line, coordinate in enumerate(columns):
        for start_index, (start, end) in enumerate(zip(rows, rows[1:])):
            ratio, covered, maximum_gap, contributors = _coverage(
                segments,
                orientation="vertical",
                coordinate=coordinate,
                start=start,
                end=end,
            )
            if (
                ratio + 1e-12 < _EDGE_COVERAGE_THRESHOLD
                or maximum_gap > _EDGE_MAX_UNCOVERED_GAP_PT
            ):
                continue
            widths = tuple(
                sorted(
                    {
                        round(segment.linewidth_pt, _ROUND_DIGITS)
                        for segment in contributors
                    }
                )
            )
            dominant_width = _dominant_linewidth(
                contributors,
                start=start,
                end=end,
            )
            edges.append(
                {
                    "orientation": "vertical",
                    "line": line,
                    "start": start_index,
                    "end": start_index + 1,
                    "coordinate_pt": round(coordinate, _ROUND_DIGITS),
                    "interval_start_pt": round(start, _ROUND_DIGITS),
                    "interval_end_pt": round(end, _ROUND_DIGITS),
                    "coverage_ratio": round(ratio, _ROUND_DIGITS),
                    "covered_length_pt": round(covered, _ROUND_DIGITS),
                    "maximum_uncovered_gap_pt": round(maximum_gap, _ROUND_DIGITS),
                    "coordinate_hwpunit": pt_to_hwpunit(coordinate),
                    "interval_start_hwpunit": pt_to_hwpunit(start),
                    "interval_end_hwpunit": pt_to_hwpunit(end),
                    "covered_length_hwpunit": pt_to_hwpunit(covered),
                    "linewidths_pt": widths,
                    "dominant_linewidth_pt": dominant_width,
                    "border_width_snap": snap_pdf_linewidth(dominant_width).payload(),
                    "sources": tuple(segment.source for segment in contributors),
                }
            )
    for line, coordinate in enumerate(rows):
        for start_index, (start, end) in enumerate(zip(columns, columns[1:])):
            ratio, covered, maximum_gap, contributors = _coverage(
                segments,
                orientation="horizontal",
                coordinate=coordinate,
                start=start,
                end=end,
            )
            if (
                ratio + 1e-12 < _EDGE_COVERAGE_THRESHOLD
                or maximum_gap > _EDGE_MAX_UNCOVERED_GAP_PT
            ):
                continue
            widths = tuple(
                sorted(
                    {
                        round(segment.linewidth_pt, _ROUND_DIGITS)
                        for segment in contributors
                    }
                )
            )
            dominant_width = _dominant_linewidth(
                contributors,
                start=start,
                end=end,
            )
            edges.append(
                {
                    "orientation": "horizontal",
                    "line": line,
                    "start": start_index,
                    "end": start_index + 1,
                    "coordinate_pt": round(coordinate, _ROUND_DIGITS),
                    "interval_start_pt": round(start, _ROUND_DIGITS),
                    "interval_end_pt": round(end, _ROUND_DIGITS),
                    "coverage_ratio": round(ratio, _ROUND_DIGITS),
                    "covered_length_pt": round(covered, _ROUND_DIGITS),
                    "maximum_uncovered_gap_pt": round(maximum_gap, _ROUND_DIGITS),
                    "coordinate_hwpunit": pt_to_hwpunit(coordinate),
                    "interval_start_hwpunit": pt_to_hwpunit(start),
                    "interval_end_hwpunit": pt_to_hwpunit(end),
                    "covered_length_hwpunit": pt_to_hwpunit(covered),
                    "linewidths_pt": widths,
                    "dominant_linewidth_pt": dominant_width,
                    "border_width_snap": snap_pdf_linewidth(dominant_width).payload(),
                    "sources": tuple(segment.source for segment in contributors),
                }
            )
    return tuple(edges)


def _grid_payload(
    plumber: PlumberObservation,
    mupdf: MuPdfObservation,
) -> JsonObject:
    stroke_segments, bezier_policy = _stroke_axis_segments(mupdf.drawings)
    plumber_rectangles = tuple(
        ThinRectObservation(
            geometry=observation.geometry.transformed(mupdf.derotation_matrix),
            source=observation.source,
            engine=observation.engine,
            stroke=observation.stroke,
            fill=observation.fill,
            linewidth_pt=observation.linewidth_pt,
            color=observation.color,
            paint_order=observation.paint_order,
        )
        for observation in plumber.thin_rectangles
    )
    primary, matches, unmatched_plumber, maximum_match_difference = (
        _reconcile_thin_rectangles(
            plumber_rectangles,
            mupdf.thin_rectangles,
        )
    )
    candidate_pool = tuple(
        observation
        for observation in (*primary, *unmatched_plumber)
        if observation.fill
    )
    geometry_candidates = tuple(
        observation
        for observation in candidate_pool
        if observation.geometry.aspect_ratio >= _THIN_RECT_MIN_ASPECT_RATIO
    )
    plumber_rule_candidates = {
        observation.source
        for observation in plumber_rectangles
        if observation.geometry.aspect_ratio >= _THIN_RECT_MIN_ASPECT_RATIO
    }
    mupdf_rule_candidates = {
        observation.source
        for observation in mupdf.thin_rectangles
        if observation.geometry.aspect_ratio >= _THIN_RECT_MIN_ASPECT_RATIO
    }
    matched_rule_candidates = sum(
        match["pdfplumber_source"] in plumber_rule_candidates
        and match["pymupdf_source"] in mupdf_rule_candidates
        for match in matches
    )
    prospective_segments = tuple(
        _thin_rect_segment(observation) for observation in geometry_candidates
    )
    support_segments = (*stroke_segments, *prospective_segments)
    accepted_thin_segments: list[AxisSegment] = []
    classification: list[JsonObject] = []
    geometry_sources = {observation.source for observation in geometry_candidates}
    for observation in candidate_pool:
        rectangle = observation.geometry
        detail: JsonObject = {
            "source": observation.source,
            "engine": observation.engine,
            "orientation": (
                "horizontal" if rectangle.width >= rectangle.height else "vertical"
            ),
            "length_pt": round(rectangle.major_length, _ROUND_DIGITS),
            "thickness_pt": round(rectangle.thickness, _ROUND_DIGITS),
            "aspect_ratio": round(rectangle.aspect_ratio, _ROUND_DIGITS),
            "stroke": observation.stroke,
            "fill": observation.fill,
            "color": observation.color,
        }
        if observation.source not in geometry_sources:
            detail["classification"] = "short_or_low_aspect_fill"
            classification.append(detail)
            continue
        segment = _thin_rect_segment(observation)
        endpoint_support, collinear_support, same_color_support = _thin_rect_support(
            segment,
            support_segments,
        )
        detail.update(
            {
                "endpoint_support_count": endpoint_support,
                "collinear_support_count": collinear_support,
                "same_color_support_count": same_color_support,
            }
        )
        topology_supported = endpoint_support >= 2 or (
            endpoint_support >= 1 and collinear_support >= 1
        )
        if topology_supported:
            detail["classification"] = (
                "supported_short_grid_segment"
                if rectangle.major_length < _THIN_RECT_SHORT_LENGTH_PT
                else "grid_segment"
            )
            accepted_thin_segments.append(segment)
        elif rectangle.major_length < _THIN_RECT_SHORT_LENGTH_PT:
            detail["classification"] = "short_or_low_aspect_fill"
        else:
            detail["classification"] = "elongated_fill_without_grid_support"
        classification.append(detail)
    segments = (*stroke_segments, *accepted_thin_segments)
    vertical_segments = tuple(
        segment for segment in segments if segment.orientation == "vertical"
    )
    horizontal_segments = tuple(
        segment for segment in segments if segment.orientation == "horizontal"
    )
    column_clusters = _cluster_coordinates(
        tuple(segment.coordinate for segment in vertical_segments),
        tolerance_pt=_COORDINATE_TOLERANCE_PT,
    )
    row_clusters = _cluster_coordinates(
        tuple(segment.coordinate for segment in horizontal_segments),
        tolerance_pt=_COORDINATE_TOLERANCE_PT,
    )
    columns = tuple(cluster.coordinate for cluster in column_clusters)
    rows = tuple(cluster.coordinate for cluster in row_clusters)
    visible_edges = _grid_edges(
        segments,
        column_clusters,
        row_clusters,
    )
    column_boundaries_hwpunit = tuple(pt_to_hwpunit(value) for value in columns)
    row_boundaries_hwpunit = tuple(pt_to_hwpunit(value) for value in rows)
    candidate_edge_count = len(columns) * max(len(rows) - 1, 0) + len(rows) * max(
        len(columns) - 1,
        0,
    )
    return {
        "source_engine": "pymupdf",
        "auxiliary_engine": "pdfplumber thin rectangles",
        "coordinate_tolerance_pt": _COORDINATE_TOLERANCE_PT,
        "axis_tolerance_pt": _AXIS_TOLERANCE_PT,
        "edge_coverage_threshold": _EDGE_COVERAGE_THRESHOLD,
        "edge_max_uncovered_gap_pt": _EDGE_MAX_UNCOVERED_GAP_PT,
        "edge_initial_state": "none",
        "column_boundaries_pt": tuple(round(value, _ROUND_DIGITS) for value in columns),
        "row_boundaries_pt": tuple(round(value, _ROUND_DIGITS) for value in rows),
        "column_widths_pt": tuple(
            round(right - left, _ROUND_DIGITS)
            for left, right in zip(columns, columns[1:])
        ),
        "row_heights_pt": tuple(
            round(bottom - top, _ROUND_DIGITS) for top, bottom in zip(rows, rows[1:])
        ),
        "column_boundaries_hwpunit": column_boundaries_hwpunit,
        "row_boundaries_hwpunit": row_boundaries_hwpunit,
        "column_widths_hwpunit": tuple(
            right - left
            for left, right in zip(
                column_boundaries_hwpunit,
                column_boundaries_hwpunit[1:],
            )
        ),
        "row_heights_hwpunit": tuple(
            bottom - top
            for top, bottom in zip(
                row_boundaries_hwpunit,
                row_boundaries_hwpunit[1:],
            )
        ),
        "column_boundary_quantization_error_pt": tuple(
            round(hwpunit / 100 - point, 10)
            for point, hwpunit in zip(
                columns,
                column_boundaries_hwpunit,
                strict=True,
            )
        ),
        "row_boundary_quantization_error_pt": tuple(
            round(hwpunit / 100 - point, 10)
            for point, hwpunit in zip(
                rows,
                row_boundaries_hwpunit,
                strict=True,
            )
        ),
        "column_clusters": tuple(cluster.payload() for cluster in column_clusters),
        "row_clusters": tuple(cluster.payload() for cluster in row_clusters),
        "horizontal_segment_count": len(horizontal_segments),
        "vertical_segment_count": len(vertical_segments),
        "segments": tuple(segment.payload() for segment in segments),
        "edge_candidate_count": candidate_edge_count,
        "visible_edge_count": len(visible_edges),
        "omitted_edge_count": candidate_edge_count - len(visible_edges),
        "visible_edges": visible_edges,
        "bezier_policy": bezier_policy,
        "thin_rect_classification": {
            "pdfplumber_candidate_count": len(plumber_rectangles),
            "pymupdf_candidate_count": len(mupdf.thin_rectangles),
            "matched_cross_engine_count": len(matches),
            "unmatched_pdfplumber_count": len(unmatched_plumber),
            "match_tolerance_pt": _THIN_RECT_MATCH_TOLERANCE_PT,
            "maximum_match_coordinate_difference_pt": maximum_match_difference,
            "pdfplumber_rule_candidate_count": len(plumber_rule_candidates),
            "pymupdf_rule_candidate_count": len(mupdf_rule_candidates),
            "matched_rule_candidate_count": matched_rule_candidates,
            "cross_engine_rule_precision": (
                matched_rule_candidates / len(plumber_rule_candidates)
                if plumber_rule_candidates
                else None
            ),
            "cross_engine_rule_recall": (
                matched_rule_candidates / len(mupdf_rule_candidates)
                if mupdf_rule_candidates
                else None
            ),
            "human_labeled_detection_rate": None,
            "geometry_candidate_count": len(geometry_candidates),
            "grid_segment_count": len(accepted_thin_segments),
            "supported_short_rule_count": sum(
                item["classification"] == "supported_short_grid_segment"
                for item in classification
            ),
            "rejected_short_or_low_aspect_count": sum(
                item["classification"] == "short_or_low_aspect_fill"
                for item in classification
            ),
            "rejected_without_grid_support_count": sum(
                item["classification"] == "elongated_fill_without_grid_support"
                for item in classification
            ),
            "short_length_threshold_pt": _THIN_RECT_SHORT_LENGTH_PT,
            "minimum_aspect_ratio": _THIN_RECT_MIN_ASPECT_RATIO,
            "rule": (
                "require aspect ratio first, then two endpoint junctions or one "
                "endpoint junction plus a collinear continuation; topology may "
                "admit a short rule, while color is recorded but never used as a "
                "sole veto"
            ),
            "matches": matches,
            "candidates": tuple(classification),
        },
    }


def _unit_alignment(widths: Sequence[float]) -> JsonObject:
    rounded_widths = tuple(round(width, _ROUND_DIGITS) for width in widths)
    counts = Counter(rounded_widths)
    snaps = tuple(
        (linewidth, counts[linewidth], snap_pdf_linewidth(linewidth))
        for linewidth in sorted(counts)
    )
    observation_count = sum(count for _, count, _ in snaps)
    observed_absolute_errors = tuple(
        snap.absolute_error_mm for _, count, snap in snaps for _ in range(count)
    )
    observed_residuals = tuple(
        snap.residual_mm for _, count, snap in snaps for _ in range(count)
    )
    observed_quantization_errors = tuple(
        snap.hwpunit_quantization_error_pt
        for _, count, snap in snaps
        for _ in range(count)
    )
    weighted_absolute_error = sum(
        snap.absolute_error_mm * count for _, count, snap in snaps
    )
    error_frequency = Counter(
        snap.absolute_error_mm for _, count, snap in snaps for _ in range(count)
    )
    ordered_absolute_errors = tuple(sorted(observed_absolute_errors))
    p95_index = (95 * observation_count + 99) // 100 - 1 if observation_count else 0
    return {
        "pt_to_hwpunit": "round_half_up(Decimal(str(pt)) * 100)",
        "geometry_conversion_uses_mm": False,
        "snap_source": "integer HWPUNIT after direct point conversion",
        "border_width_snap_targets": tuple(
            target.payload() for target in BORDER_WIDTH_SNAP_TARGETS
        ),
        "linewidth_snap_table": tuple(
            {
                **snap.payload(),
                "observation_count": count,
            }
            for _, count, snap in snaps
        ),
        "snap_error_distribution": {
            "observation_count": observation_count,
            "unique_linewidth_count": len(snaps),
            "zero_error_observation_count": sum(
                count for _, count, snap in snaps if snap.absolute_error_mm <= 1e-10
            ),
            "mean_absolute_error_mm": (
                round(weighted_absolute_error / observation_count, 10)
                if observation_count
                else 0.0
            ),
            "mean_signed_error_mm": (
                round(sum(observed_residuals) / observation_count, 10)
                if observation_count
                else 0.0
            ),
            "median_absolute_error_mm": (
                round(float(median(ordered_absolute_errors)), 10)
                if observation_count
                else 0.0
            ),
            "p95_absolute_error_mm": (
                ordered_absolute_errors[p95_index] if observation_count else 0.0
            ),
            "maximum_absolute_error_mm": max(
                (snap.absolute_error_mm for _, _, snap in snaps),
                default=0.0,
            ),
            "minimum_residual_mm": min(observed_residuals, default=0.0),
            "maximum_residual_mm": max(observed_residuals, default=0.0),
            "maximum_absolute_hwpunit_quantization_error_pt": max(
                (abs(error) for error in observed_quantization_errors),
                default=0.0,
            ),
            "absolute_error_mm_frequency": tuple(
                {"absolute_error_mm": error, "count": count}
                for error, count in sorted(error_frequency.items())
            ),
        },
    }


def _aligned_widths(
    plumber_widths: tuple[float, ...],
    mupdf_widths: tuple[float, ...],
) -> tuple[JsonObject, ...]:
    remaining = list(mupdf_widths)
    aligned: list[JsonObject] = []
    for plumber_width in plumber_widths:
        if not remaining:
            break
        mupdf_width = min(remaining, key=lambda value: abs(value - plumber_width))
        difference = abs(mupdf_width - plumber_width)
        if difference > _WIDTH_MATCH_TOLERANCE_PT:
            continue
        remaining.remove(mupdf_width)
        aligned.append(
            {
                "pdfplumber_pt": plumber_width,
                "pymupdf_pt": mupdf_width,
                "difference_pt": round(difference, _ROUND_DIGITS),
            }
        )
    return tuple(aligned)


def _plumber_payload(page: PlumberPage) -> PlumberObservation:
    vectors = (*page.lines, *page.rects, *page.curves)
    widths = tuple(
        json_float(linewidth)
        for vector in vectors
        if (linewidth := vector.get("linewidth")) is not None
    )
    rectangles = tuple(
        RectGeometry(
            x0=json_float(rectangle["x0"]),
            top=json_float(rectangle["top"]),
            x1=json_float(rectangle["x1"]),
            bottom=json_float(rectangle["bottom"]),
        )
        for rectangle in page.rects
    )
    thin_rectangles = tuple(
        ThinRectObservation(
            geometry=rectangle,
            source=f"pdfplumber:rect:{index}",
            engine="pdfplumber",
            stroke=bool(page.rects[index].get("stroke")),
            fill=bool(page.rects[index].get("fill")),
            linewidth_pt=(
                json_float(linewidth)
                if (linewidth := page.rects[index].get("linewidth")) is not None
                else None
            ),
            color=_color(
                page.rects[index].get(
                    "non_stroking_color"
                    if bool(page.rects[index].get("fill"))
                    else "stroking_color"
                )
            ),
            paint_order=-1,
        )
        for index, rectangle in enumerate(rectangles)
        if rectangle.is_thin
    )
    thin_segments = tuple(
        observation.geometry.centerline(observation.source)
        for observation in thin_rectangles
    )
    payload: JsonObject = {
        "line_count": len(page.lines),
        "rect_count": len(page.rects),
        "curve_count": len(page.curves),
        "thin_rect_segment_count": len(thin_segments),
        "char_count": len(page.chars),
        "image_count": len(page.images),
        "lines": tuple(selected_fields(item, _VECTOR_FIELDS) for item in page.lines),
        "rects": tuple(selected_fields(item, _VECTOR_FIELDS) for item in page.rects),
        "curves": tuple(selected_fields(item, _VECTOR_FIELDS) for item in page.curves),
        "thin_rect_segments": thin_segments,
        "chars": tuple(selected_fields(item, _CHAR_FIELDS) for item in page.chars),
        "images": tuple(selected_fields(item, _IMAGE_FIELDS) for item in page.images),
        "rect_gaps_pt": rect_gaps(
            tuple(rectangle for rectangle in rectangles if not rectangle.is_thin)
        ),
    }
    return PlumberObservation(
        payload=payload,
        widths=widths,
        line_count=len(page.lines),
        rect_count=len(page.rects),
        curve_count=len(page.curves),
        thin_rect_count=len(thin_segments),
        char_count=len(page.chars),
        rotation=int(page.rotation or 0),
        size=(float(page.width), float(page.height)),
        thin_rectangles=thin_rectangles,
    )


def _pymupdf_payload(page: pymupdf.Page) -> MuPdfObservation:
    drawing_source = as_mupdf_page(page)
    drawings = tuple(
        parse_mupdf_drawing(drawing) for drawing in drawing_source.get_drawings()
    )
    metadata = parse_mupdf_page_metadata(page)
    item_kinds: list[str] = []
    thin_segments: list[JsonObject] = []
    thin_rectangles: list[ThinRectObservation] = []
    for drawing_index, drawing in enumerate(drawings):
        for item_index, item in enumerate(drawing.items):
            kind = str(item[0])
            item_kinds.append(kind)
            if kind != "re":
                continue
            rectangle = RectGeometry.from_pymupdf(item[1])
            if rectangle.is_thin:
                source = f"path:{drawing_index}:item:{item_index}"
                thin_segments.append(rectangle.centerline(source))
                thin_rectangles.append(
                    ThinRectObservation(
                        geometry=rectangle,
                        source=f"pymupdf:{source}",
                        engine="pymupdf",
                        stroke=_drawing_has_stroke(drawing),
                        fill=_drawing_has_fill(drawing),
                        linewidth_pt=drawing.width,
                        color=(
                            drawing.fill
                            if _drawing_has_fill(drawing)
                            else drawing.color
                        ),
                        paint_order=drawing.seqno,
                    )
                )
    counts = Counter(item_kinds)
    widths = tuple(
        json_float(width)
        for drawing in drawings
        if (width := drawing.width) is not None
    )
    known_count = sum(counts.get(kind, 0) for kind in ("l", "re", "c", "qu"))
    item_type_counts: JsonObject = {
        "l": counts.get("l", 0),
        "re": counts.get("re", 0),
        "c": counts.get("c", 0),
        "qu": counts.get("qu", 0),
        "other": len(item_kinds) - known_count,
    }
    payload: JsonObject = {
        "path_count": len(drawings),
        "item_count": len(item_kinds),
        "item_type_counts": item_type_counts,
        "thin_rect_segment_count": len(thin_segments),
        "thin_rect_segments": tuple(thin_segments),
        "drawings": tuple(drawing_json(drawing) for drawing in drawings),
    }
    return MuPdfObservation(
        payload=payload,
        widths=widths,
        path_count=len(drawings),
        item_count=len(item_kinds),
        thin_rect_count=len(thin_segments),
        bezier_count=counts.get("c", 0),
        rotation=metadata.rotation,
        size=rect_size(metadata.rect),
        unrotated_size=rect_size(metadata.cropbox),
        rotation_matrix=matrix_values(metadata.rotation_matrix),
        derotation_matrix=page.derotation_matrix,
        drawings=drawings,
        thin_rectangles=tuple(thin_rectangles),
    )


def extract_pdf_page(source_pdf: Path, page_number: int) -> ExtractionReport:
    if not source_pdf.is_file():
        raise PdfVectorExtractError(f"PDF 파일이 없습니다: {source_pdf}")
    with pdfplumber.open(source_pdf) as plumber_document:
        if page_number < 1 or page_number > len(plumber_document.pages):
            raise PdfVectorExtractError(f"쪽 번호가 범위를 벗어났습니다: {page_number}")
        plumber_page: PlumberPage = plumber_document.pages[page_number - 1]
        plumber = _plumber_payload(plumber_page)
    with pymupdf.open(source_pdf) as pymupdf_document:
        pymupdf_page = pymupdf_document[page_number - 1]
        mupdf = _pymupdf_payload(pymupdf_page)

    plumber_unique = tuple(
        sorted(set(round(value, _ROUND_DIGITS) for value in plumber.widths))
    )
    mupdf_unique = tuple(
        sorted(set(round(value, _ROUND_DIGITS) for value in mupdf.widths))
    )
    aligned_widths = _aligned_widths(plumber_unique, mupdf_unique)
    summary: JsonObject = {
        "pdfplumber_linewidths_pt": plumber_unique,
        "pdfplumber_linewidth_frequency": frequency(plumber.widths),
        "pymupdf_widths_pt": mupdf_unique,
        "pymupdf_width_frequency": frequency(mupdf.widths),
        "rect_gaps_pt": plumber.payload["rect_gaps_pt"],
        "chars_are_zero": plumber.char_count == 0,
    }
    comparison: JsonObject = {
        "pdfplumber_line_count": plumber.line_count,
        "pdfplumber_rect_count": plumber.rect_count,
        "pdfplumber_curve_count": plumber.curve_count,
        "pymupdf_path_count": mupdf.path_count,
        "pymupdf_item_count": mupdf.item_count,
        "pdfplumber_thin_rect_segment_count": plumber.thin_rect_count,
        "pymupdf_thin_rect_segment_count": mupdf.thin_rect_count,
        "thin_rect_counts_match": plumber.thin_rect_count == mupdf.thin_rect_count,
        "linewidth_values_match": (
            len(aligned_widths) == len(plumber_unique) == len(mupdf_unique)
        ),
        "linewidth_values_match_exact": plumber_unique == mupdf_unique,
        "linewidth_match_tolerance_pt": _WIDTH_MATCH_TOLERANCE_PT,
        "aligned_linewidths": aligned_widths,
        "shared_linewidths_pt": tuple(sorted(set(plumber_unique) & set(mupdf_unique))),
        "pymupdf_bezier_item_count": mupdf.bezier_count,
        "pymupdf_bezier_item_ratio": (
            mupdf.bezier_count / mupdf.item_count if mupdf.item_count else 0.0
        ),
    }
    grid_reconstruction = _grid_payload(plumber, mupdf)
    unit_alignment = _unit_alignment(mupdf.widths)
    page: JsonObject = {
        "rotation": plumber.rotation,
        "pdfplumber_rotation": plumber.rotation,
        "pymupdf_rotation": mupdf.rotation,
        "rotation_matches": plumber.rotation == mupdf.rotation,
        "pdfplumber_size_pt": plumber.size,
        "pymupdf_size_pt": mupdf.size,
        "pymupdf_unrotated_size_pt": mupdf.unrotated_size,
        "pymupdf_rotation_matrix": mupdf.rotation_matrix,
        "coordinate_system": "top-left origin, points",
        "pdfplumber_coordinate_frame": "displayed page (page rotation applied)",
        "pymupdf_drawing_coordinate_frame": (
            "unrotated page; apply pymupdf_rotation_matrix for displayed coordinates"
        ),
        "rotation_requires_coordinate_transform": (
            plumber.rotation != 0 or mupdf.rotation != 0
        ),
    }
    return ExtractionReport(
        source_pdf=str(source_pdf.resolve()),
        page_number=page_number,
        page=page,
        pdfplumber=plumber.payload,
        pymupdf=mupdf.payload,
        summary=summary,
        comparison=comparison,
        grid_reconstruction=grid_reconstruction,
        unit_alignment=unit_alignment,
    )


def _print_summary(report: ExtractionReport) -> None:
    console.print(
        f"[bold]page[/bold] {report.page_number}:",
        f"rotation={report.page['rotation']},",
        f"chars_are_zero={report.summary['chars_are_zero']}",
    )
    console.print(
        "[bold]pdfplumber[/bold]:",
        f"lines={report.pdfplumber['line_count']},",
        f"thin-rect segments={report.pdfplumber['thin_rect_segment_count']},",
        f"linewidths={report.summary['pdfplumber_linewidth_frequency']}",
    )
    gaps = report.summary["rect_gaps_pt"]
    if not isinstance(gaps, dict):
        raise PdfVectorExtractError("rect gap summary has an invalid shape")
    console.print(
        "rect gap frequencies (pt):",
        {
            "horizontal": gaps["horizontal_frequency"],
            "vertical": gaps["vertical_frequency"],
        },
    )
    console.print(
        "[bold]PyMuPDF[/bold]:",
        f"paths={report.pymupdf['path_count']},",
        f"thin-rect segments={report.pymupdf['thin_rect_segment_count']},",
        f"widths={report.summary['pymupdf_width_frequency']}",
    )
    console.print(f"comparison: {report.comparison}")


def _root_command() -> None:
    return


_ = app.callback()(_root_command)


@app.command("extract")
def extract_command(
    source_pdf: Path,
    output: Annotated[Path, _OUTPUT_OPTION],
    page: Annotated[int, _PAGE_OPTION] = 1,
) -> None:
    """Extract one PDF page with pdfplumber and PyMuPDF."""
    report = extract_pdf_page(source_pdf, page)
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _print_summary(report)


if __name__ == "__main__":
    app()

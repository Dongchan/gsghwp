from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from PIL import Image

from hwp_reference_layout_contract import ReferenceLayoutBlock, VisibleEdge
from hwp_live_values import Rgb


_COLOR_TOLERANCE = 55
_MAXIMUM_SAMPLES = 192
_SPARSE_STYLES = {"dash", "dot", "dash_dot", "dash_dot_dot", "long_dash"}
_SOLID_SUPPORT_RATIO = 0.55
_SPARSE_SUPPORT_RATIO = 0.08


def _color_distance(left: Rgb, right: Rgb) -> int:
    return max(abs(left[index] - right[index]) for index in range(3))


def _sample_positions(start: int, end: int) -> Iterator[int]:
    span = max(0, end - start)
    samples = min(_MAXIMUM_SAMPLES, span)
    if samples <= 1:
        if span:
            yield start
        return
    for index in range(samples):
        yield start + round((span - 1) * index / (samples - 1))


def _edge_coordinates(
    image: Image.Image,
    block: ReferenceLayoutBlock,
    edge: VisibleEdge,
) -> tuple[int, int, int, int]:
    width, height = image.size
    if edge.orientation == "horizontal":
        line = round(block.row_breakpoints[edge.line] * (height - 1))
        start = round(block.column_breakpoints[edge.start] * (width - 1))
        end = round(block.column_breakpoints[edge.end] * (width - 1))
        return line, start, end, height - 1
    line = round(block.column_breakpoints[edge.line] * (width - 1))
    start = round(block.row_breakpoints[edge.start] * (height - 1))
    end = round(block.row_breakpoints[edge.end] * (height - 1))
    return line, start, end, width - 1


def _pixel(
    image: Image.Image,
    edge: VisibleEdge,
    tangent: int,
    perpendicular: int,
) -> Rgb:
    if edge.orientation == "horizontal":
        value = image.getpixel((tangent, perpendicular))
    else:
        value = image.getpixel((perpendicular, tangent))
    return cast(Rgb, value)


def _is_thin_stroke_sample(
    image: Image.Image,
    edge: VisibleEdge,
    *,
    tangent: int,
    line: int,
    perpendicular_limit: int,
    search_radius: int,
    maximum_stroke_pixels: int,
) -> bool:
    candidate_start = max(0, line - search_radius)
    candidate_end = min(perpendicular_limit, line + search_radius)
    candidate = min(
        range(candidate_start, candidate_end + 1),
        key=lambda position: _color_distance(
            _pixel(image, edge, tangent, position),
            edge.color,
        ),
    )
    if (
        _color_distance(
            _pixel(image, edge, tangent, candidate),
            edge.color,
        )
        > _COLOR_TOLERANCE
    ):
        return False

    run_start = candidate
    run_end = candidate
    scan_limit = maximum_stroke_pixels * 2
    while (
        run_start > max(0, candidate - scan_limit)
        and _color_distance(
            _pixel(image, edge, tangent, run_start - 1),
            edge.color,
        )
        <= _COLOR_TOLERANCE
    ):
        run_start -= 1
    while (
        run_end < min(perpendicular_limit, candidate + scan_limit)
        and _color_distance(
            _pixel(image, edge, tangent, run_end + 1),
            edge.color,
        )
        <= _COLOR_TOLERANCE
    ):
        run_end += 1
    return run_end - run_start + 1 <= maximum_stroke_pixels


def visible_edge_support_ratio(
    image: Image.Image,
    block: ReferenceLayoutBlock,
    edge: VisibleEdge,
) -> float:
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    line, start, end, perpendicular_limit = _edge_coordinates(rgb, block, edge)
    if end <= start:
        return 0
    short_side = min(rgb.size)
    search_radius = max(2, min(4, round(short_side * 0.002)))
    maximum_stroke_pixels = max(2, min(6, round(short_side * 0.003)))
    supported = 0
    samples = 0
    for tangent in _sample_positions(start, end):
        samples += 1
        supported += _is_thin_stroke_sample(
            rgb,
            edge,
            tangent=tangent,
            line=line,
            perpendicular_limit=perpendicular_limit,
            search_radius=search_radius,
            maximum_stroke_pixels=maximum_stroke_pixels,
        )
    return supported / max(1, samples)


def filter_unsupported_visible_edges(
    image: Image.Image,
    block: ReferenceLayoutBlock,
) -> ReferenceLayoutBlock:
    if not block.visible_edges:
        return block
    retained = tuple(
        edge
        for edge in block.visible_edges
        if edge.style != "none"
        and visible_edge_support_ratio(image, block, edge)
        >= (
            _SPARSE_SUPPORT_RATIO
            if edge.style in _SPARSE_STYLES
            else _SOLID_SUPPORT_RATIO
        )
    )
    return block.model_copy(update={"visible_edges": retained})

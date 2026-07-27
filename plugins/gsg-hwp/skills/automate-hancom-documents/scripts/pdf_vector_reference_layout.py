from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from hwp_color_normalization import ColorInput
from hwp_live_table_contract import BorderStyle, BorderWidth, snap_pdf_linewidth
from hwp_reference_layout_contract import (
    EdgeOrientation,
    ReferenceLayoutBlock,
    ReferenceMerge,
    VisibleEdge,
)
from pdf_vector_contract import ExtractionReport


class PdfGridUnsupportedError(ValueError):
    """The extracted PDF grid cannot be represented without guessing."""


@dataclass(frozen=True, slots=True)
class AxisHwpunitAllocation:
    source_boundaries_pt: tuple[Fraction, ...]
    origin_hwpunit: int
    extent_hwpunit: int
    sizes_hwpunit: tuple[int, ...]
    normalized_breakpoints: tuple[float, ...]


def _fraction(value: object) -> Fraction:
    if isinstance(value, bool):
        raise PdfGridUnsupportedError("PDF grid coordinates must be numeric")
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise PdfGridUnsupportedError(
            "PDF grid coordinates must be finite numeric values"
        ) from error
    return result


def _round_half_up(value: Fraction) -> int:
    if value >= 0:
        return (2 * value.numerator + value.denominator) // (2 * value.denominator)
    positive = -value
    return -(
        (2 * positive.numerator + positive.denominator) // (2 * positive.denominator)
    )


def allocate_axis_hwpunits(
    boundaries_pt: Sequence[object],
) -> AxisHwpunitAllocation:
    """Quantize one PDF axis once and preserve its exact integer total.

    Point inputs are converted to ``Fraction`` immediately. Interval ideals are
    accumulated without rounding; only the final integer HWPUNIT allocation is
    rounded. Any remainder is assigned by descending fractional part with a
    stable lower-index tie break.
    """

    boundaries = tuple(_fraction(value) for value in boundaries_pt)
    if len(boundaries) < 2:
        raise PdfGridUnsupportedError("PDF grid axes need at least two boundaries")
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        raise PdfGridUnsupportedError("PDF grid boundaries must be strictly increasing")

    interval_ideals = tuple(
        (right - left) * 100 for left, right in zip(boundaries, boundaries[1:])
    )
    extent_ideal = (boundaries[-1] - boundaries[0]) * 100
    extent_hwpunit = _round_half_up(extent_ideal)
    floors = tuple(value.numerator // value.denominator for value in interval_ideals)
    remainder = extent_hwpunit - sum(floors)
    if remainder < 0 or remainder > len(floors):
        raise PdfGridUnsupportedError(
            "PDF grid HWPUNIT allocation has an inconsistent remainder"
        )
    order = sorted(
        range(len(floors)),
        key=lambda index: (
            interval_ideals[index] - floors[index],
            -index,
        ),
        reverse=True,
    )
    sizes = list(floors)
    for index in order[:remainder]:
        sizes[index] += 1
    if extent_hwpunit <= 0 or any(size <= 0 for size in sizes):
        raise PdfGridUnsupportedError(
            "a PDF grid interval collapses during final HWPUNIT quantization"
        )
    if sum(sizes) != extent_hwpunit:
        raise PdfGridUnsupportedError(
            "PDF grid HWPUNIT intervals do not exactly fill their axis"
        )

    cumulative = [0]
    for size in sizes:
        cumulative.append(cumulative[-1] + size)
    normalized = tuple(float(Fraction(value, extent_hwpunit)) for value in cumulative)
    return AxisHwpunitAllocation(
        source_boundaries_pt=boundaries,
        origin_hwpunit=_round_half_up(boundaries[0] * 100),
        extent_hwpunit=extent_hwpunit,
        sizes_hwpunit=tuple(sizes),
        normalized_breakpoints=normalized,
    )


def _grid_mapping(
    payload: ExtractionReport | Mapping[str, object],
) -> Mapping[str, object]:
    if isinstance(payload, ExtractionReport):
        return cast(Mapping[str, object], payload.grid_reconstruction)
    nested = payload.get("grid_reconstruction")
    if isinstance(nested, Mapping):
        return cast(Mapping[str, object], nested)
    return payload


def _sequence(
    payload: Mapping[str, object],
    key: str,
) -> Sequence[object]:
    value = payload.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PdfGridUnsupportedError(f"PDF grid is missing {key}")
    return value


def _merge(value: ReferenceMerge | Mapping[str, object]) -> ReferenceMerge:
    if isinstance(value, ReferenceMerge):
        return value
    try:
        return ReferenceMerge.model_validate(value)
    except ValidationError as error:
        raise PdfGridUnsupportedError(
            "PDF grid contains an invalid explicit rectangular merge"
        ) from error


def _edge_width(payload: Mapping[str, object]) -> BorderWidth:
    direct = payload.get("width")
    if isinstance(direct, str):
        return cast(BorderWidth, direct)
    snap = payload.get("border_width_snap")
    if isinstance(snap, Mapping):
        snap_payload = cast(Mapping[str, object], snap)
        snapped = snap_payload.get(
            "snapped_border_width",
            snap_payload.get("border_width"),
        )
        if isinstance(snapped, str):
            return cast(BorderWidth, snapped)
    linewidth = payload.get("dominant_linewidth_pt")
    if isinstance(linewidth, int | float) and not isinstance(linewidth, bool):
        return snap_pdf_linewidth(float(linewidth)).width
    raise PdfGridUnsupportedError(
        "PDF visible edge has no representable snapped border width"
    )


def _visible_edge(
    payload: Mapping[str, object],
    *,
    default_color: ColorInput,
) -> VisibleEdge:
    try:
        return VisibleEdge(
            orientation=cast(EdgeOrientation, payload["orientation"]),
            line=cast(int, payload["line"]),
            start=cast(int, payload["start"]),
            end=cast(int, payload["end"]),
            style=cast(BorderStyle, payload.get("style", "solid")),
            width=_edge_width(payload),
            color=cast(ColorInput, payload.get("color", default_color)),
        )
    except (KeyError, ValidationError, TypeError, ValueError) as error:
        raise PdfGridUnsupportedError(
            "PDF visible edge is outside the extracted orthogonal grid"
        ) from error


def _same_edge_paint(left: VisibleEdge, right: VisibleEdge) -> bool:
    return (
        left.orientation == right.orientation
        and left.line == right.line
        and left.style == right.style
        and left.width == right.width
        and left.color == right.color
    )


def _coalesce_edges(edges: Sequence[VisibleEdge]) -> tuple[VisibleEdge, ...]:
    ordered = sorted(
        edges,
        key=lambda edge: (
            0 if edge.orientation == "horizontal" else 1,
            edge.line,
            edge.start,
            edge.end,
            edge.style,
            edge.width,
            edge.color,
        ),
    )
    coalesced: list[VisibleEdge] = []
    for edge in ordered:
        if not coalesced:
            coalesced.append(edge)
            continue
        previous = coalesced[-1]
        same_line = (
            previous.orientation == edge.orientation and previous.line == edge.line
        )
        if (
            same_line
            and edge.start < previous.end
            and not _same_edge_paint(
                previous,
                edge,
            )
        ):
            raise PdfGridUnsupportedError(
                "overlapping PDF edge styles cannot share one grid interval"
            )
        if (
            same_line
            and edge.start <= previous.end
            and _same_edge_paint(previous, edge)
        ):
            coalesced[-1] = previous.model_copy(
                update={"end": max(previous.end, edge.end)}
            )
            continue
        coalesced.append(edge)
    return tuple(coalesced)


def _crosses_merge(edge: VisibleEdge, merge: ReferenceMerge) -> bool:
    if edge.orientation == "vertical":
        return (
            merge.column < edge.line < merge.column + merge.column_span
            and edge.start < merge.row + merge.row_span
            and merge.row < edge.end
        )
    return (
        merge.row < edge.line < merge.row + merge.row_span
        and edge.start < merge.column + merge.column_span
        and merge.column < edge.end
    )


def pdf_grid_to_reference_layout_block(
    payload: ExtractionReport | Mapping[str, object],
    *,
    source_image: Path | None = None,
    analysis_id: str | None = None,
    merges: Sequence[ReferenceMerge | Mapping[str, object]] = (),
    default_edge_color: ColorInput = (0, 0, 0),
) -> ReferenceLayoutBlock:
    """Translate extracted vector-grid evidence to the existing public block.

    Missing interior rules are kept as omitted borders. They are not interpreted
    as semantic cell merges; callers may supply only explicit rectangular merges
    established by independent evidence.
    """

    if (source_image is None) != (analysis_id is None):
        raise PdfGridUnsupportedError(
            "source_image and analysis_id must be provided together so raster "
            + "preparation cannot discard authoritative PDF vector edges"
        )

    grid = _grid_mapping(payload)
    columns = allocate_axis_hwpunits(_sequence(grid, "column_boundaries_pt"))
    rows = allocate_axis_hwpunits(_sequence(grid, "row_boundaries_pt"))
    if len(columns.sizes_hwpunit) > 50 or len(rows.sizes_hwpunit) > 50:
        raise PdfGridUnsupportedError(
            "ReferenceLayoutBulk supports at most 50 rows and 50 columns"
        )

    explicit_merges = tuple(_merge(value) for value in merges)
    raw_edges = _sequence(grid, "visible_edges")
    parsed_edges = tuple(
        _visible_edge(
            cast(Mapping[str, object], value),
            default_color=default_edge_color,
        )
        for value in raw_edges
        if isinstance(value, Mapping)
    )
    if len(parsed_edges) != len(raw_edges):
        raise PdfGridUnsupportedError("PDF visible edges must be object payloads")
    visible_edges = _coalesce_edges(parsed_edges)
    if any(
        _crosses_merge(edge, merge)
        for merge in explicit_merges
        for edge in visible_edges
    ):
        raise PdfGridUnsupportedError("a PDF visible edge crosses an explicit merge")

    try:
        return ReferenceLayoutBlock(
            kind="reference_layout",
            source_image=source_image,
            analysis_id=analysis_id,
            row_breakpoints=rows.normalized_breakpoints,
            column_breakpoints=columns.normalized_breakpoints,
            merges=explicit_merges,
            visible_edges=visible_edges,
        )
    except ValidationError as error:
        raise PdfGridUnsupportedError(
            "PDF grid is not representable by ReferenceLayoutBulk"
        ) from error

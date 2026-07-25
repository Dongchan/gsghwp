from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_evidence import prepare_reference_layout
from hwp_reference_layout_geometry import SectionPageGeometry
from hwp_reference_layout_image_evidence import (
    ProjectionLine,
    detect_layout_lines,
    detect_layout_lines_from_image,
)
from hwp_reference_layout_patch_builder import (
    build_reference_layout_patch as _build_reference_layout_patch,
)
from hwp_reference_layout_render_alignment import (
    RenderedPage,
    difference_metrics,
    rendered_placement,
)

build_reference_layout_patch = _build_reference_layout_patch


@dataclass(frozen=True, slots=True)
class CellDifference:
    row: int
    column: int
    changed_pixel_ratio: float


@dataclass(frozen=True, slots=True)
class EdgeSegmentDifference:
    orientation: str
    line: int
    start: int
    end: int
    source_coverage: float
    rendered_coverage: float
    source_position: int
    rendered_position: int


@dataclass(frozen=True, slots=True)
class NegativeSpaceDifference:
    row: int
    column: int
    source_white_ratio: float
    rendered_white_ratio: float


@dataclass(frozen=True, slots=True)
class ProtectedGapDifference:
    axis: str
    top: int
    left: int
    bottom: int
    right: int
    mean_absolute_error: float
    changed_pixel_ratio: float
    source_edge_ratio: float
    rendered_edge_ratio: float


@dataclass(frozen=True, slots=True)
class ReferenceRenderGate:
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceRenderDifference:
    mean_absolute_error: float
    changed_pixel_ratio: float
    row_breakpoints: tuple[float, ...]
    column_breakpoints: tuple[float, ...]
    changed_row_boundaries: tuple[int, ...]
    changed_column_boundaries: tuple[int, ...]
    changed_cells: tuple[CellDifference, ...]
    missing_visible_edges: tuple[EdgeSegmentDifference, ...]
    unexpected_edges: tuple[EdgeSegmentDifference, ...]
    negative_space_differences: tuple[NegativeSpaceDifference, ...]
    protected_gap_differences: tuple[ProtectedGapDifference, ...]


def _nearest_line(
    expected: float,
    lines: tuple[ProjectionLine, ...],
    *,
    tolerance: float,
) -> float:
    nearby = [line for line in lines if abs(line.position - expected) <= tolerance]
    if not nearby:
        return expected
    return min(
        nearby,
        key=lambda line: (abs(line.position - expected), -line.strength),
    ).position


def _axis_difference(
    breakpoints: tuple[float, ...],
    source_lines: tuple[ProjectionLine, ...],
    rendered_lines: tuple[ProjectionLine, ...],
    *,
    pixel_tolerance: float,
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    target = list(breakpoints)
    changed: list[int] = []
    for index, expected in enumerate(breakpoints[1:-1], start=1):
        spacing = min(
            expected - breakpoints[index - 1],
            breakpoints[index + 1] - expected,
        )
        search_tolerance = min(0.08, spacing * 0.4)
        source = _nearest_line(expected, source_lines, tolerance=search_tolerance)
        rendered = _nearest_line(source, rendered_lines, tolerance=search_tolerance)
        target[index] = source
        if abs(source - rendered) > pixel_tolerance:
            changed.append(index)
    return tuple(target), tuple(changed)


def _changed_cells(
    difference: Image.Image,
    row_breakpoints: tuple[float, ...],
    column_breakpoints: tuple[float, ...],
) -> tuple[CellDifference, ...]:
    gray = difference.convert("L")
    cells: list[CellDifference] = []
    for row, (top, bottom) in enumerate(zip(row_breakpoints, row_breakpoints[1:])):
        y0 = round(top * gray.height)
        y1 = max(y0 + 1, round(bottom * gray.height))
        for column, (left, right) in enumerate(
            zip(column_breakpoints, column_breakpoints[1:])
        ):
            x0 = round(left * gray.width)
            x1 = max(x0 + 1, round(right * gray.width))
            crop = gray.crop((x0, y0, x1, y1))
            histogram = crop.histogram()
            ratio = sum(histogram[13:]) / max(1, crop.width * crop.height)
            if ratio >= 0.01:
                cells.append(CellDifference(row, column, ratio))
    return tuple(
        sorted(
            cells,
            key=lambda cell: (-cell.changed_pixel_ratio, cell.row, cell.column),
        )
    )


def _normal_gradient_masks(image: Image.Image) -> tuple[Image.Image, Image.Image]:
    width, height = image.size
    horizontal = Image.new("L", image.size, 0)
    vertical = Image.new("L", image.size, 0)
    if height > 2:
        upper = image.crop((0, 0, width, height - 2))
        lower = image.crop((0, 2, width, height))
        gradient = ImageChops.difference(upper, lower).convert("L")
        horizontal.paste(gradient, (0, 1))
    if width > 2:
        left = image.crop((0, 0, width - 2, height))
        right = image.crop((2, 0, width, height))
        gradient = ImageChops.difference(left, right).convert("L")
        vertical.paste(gradient, (1, 0))
    threshold = tuple(255 if value >= 32 else 0 for value in range(256))
    return horizontal.point(threshold), vertical.point(threshold)


def _dark_neutral_mask(image: Image.Image) -> Image.Image:
    red, green, blue = image.convert("RGB").split()
    dark_threshold = tuple(255 if value <= 180 else 0 for value in range(256))
    neutral_threshold = tuple(255 if value <= 24 else 0 for value in range(256))
    dark = ImageChops.multiply(
        red.point(dark_threshold),
        ImageChops.multiply(
            green.point(dark_threshold),
            blue.point(dark_threshold),
        ),
    )
    neutral = ImageChops.multiply(
        ImageChops.difference(red, green).point(neutral_threshold),
        ImageChops.multiply(
            ImageChops.difference(red, blue).point(neutral_threshold),
            ImageChops.difference(green, blue).point(neutral_threshold),
        ),
    )
    return ImageChops.multiply(dark, neutral)


def _segment_coverage(
    mask: Image.Image,
    breakpoints: tuple[float, ...],
    other_breakpoints: tuple[float, ...],
    *,
    line: int,
    start: int,
    orientation: str,
    contiguous: bool = False,
    radius_limit: int | None = None,
) -> tuple[float, int]:
    axis_extent = mask.height if orientation == "horizontal" else mask.width
    segment_extent = mask.width if orientation == "horizontal" else mask.height
    position = round(breakpoints[line] * (axis_extent - 1))
    segment_start = round(other_breakpoints[start] * (segment_extent - 1))
    segment_end = round(other_breakpoints[start + 1] * (segment_extent - 1))
    trim = min(3, max(0, (segment_end - segment_start) // 10))
    segment_start += trim
    segment_end = max(segment_start + 1, segment_end - trim)
    if 0 < line < len(breakpoints) - 1:
        spacing = min(
            breakpoints[line] - breakpoints[line - 1],
            breakpoints[line + 1] - breakpoints[line],
        )
    else:
        spacing = 0.02
    radius = max(3, min(14, round(spacing * axis_extent * 0.35)))
    if radius_limit is not None:
        radius = min(radius, radius_limit)
    first = max(0, position - radius)
    last = min(axis_extent - 1, position + radius)
    best = (0.0, position)
    for candidate in range(first, last + 1):
        if orientation == "horizontal":
            crop = mask.crop((segment_start, candidate, segment_end, candidate + 1))
        else:
            crop = mask.crop((candidate, segment_start, candidate + 1, segment_end))
        pixels = crop.tobytes()
        if contiguous:
            longest = 0
            current = 0
            for pixel in pixels:
                current = current + 1 if pixel == 255 else 0
                longest = max(longest, current)
            coverage = longest / max(1, len(pixels))
        else:
            coverage = sum(pixel == 255 for pixel in pixels) / max(1, len(pixels))
        if coverage > best[0]:
            best = (coverage, candidate)
    return best


def _declared_atomic_edges(block: ReferenceLayoutBlock) -> set[tuple[str, int, int]]:
    return {
        (edge.orientation, edge.line, segment)
        for edge in block.visible_edges
        if edge.style != "none"
        for segment in range(edge.start, edge.end)
    }


def _edge_differences(
    block: ReferenceLayoutBlock,
    source: Image.Image,
    rendered: Image.Image,
    *,
    rendered_row_breakpoints: tuple[float, ...] | None = None,
    rendered_column_breakpoints: tuple[float, ...] | None = None,
) -> tuple[tuple[EdgeSegmentDifference, ...], tuple[EdgeSegmentDifference, ...]]:
    source_horizontal, source_vertical = _normal_gradient_masks(source)
    rendered_horizontal, rendered_vertical = _normal_gradient_masks(rendered)
    source_dark = _dark_neutral_mask(source)
    rendered_dark = _dark_neutral_mask(rendered)
    rendered_rows = rendered_row_breakpoints or block.row_breakpoints
    rendered_columns = rendered_column_breakpoints or block.column_breakpoints
    if len(rendered_rows) != len(block.row_breakpoints):
        raise ValueError("rendered row breakpoints do not match the reference grid")
    if len(rendered_columns) != len(block.column_breakpoints):
        raise ValueError("rendered column breakpoints do not match the reference grid")
    declared = _declared_atomic_edges(block)
    missing: list[EdgeSegmentDifference] = []
    unexpected: list[EdgeSegmentDifference] = []

    def inspect(
        orientation: str,
        line: int,
        segment: int,
        source_mask: Image.Image,
        rendered_mask: Image.Image,
        source_breakpoints: tuple[float, ...],
        source_other_breakpoints: tuple[float, ...],
        rendered_breakpoints: tuple[float, ...],
        rendered_other_breakpoints: tuple[float, ...],
    ) -> None:
        key = (orientation, line, segment)
        if key not in declared:
            source_mask = source_dark
            rendered_mask = rendered_dark
        source_coverage, source_position = _segment_coverage(
            source_mask,
            source_breakpoints,
            source_other_breakpoints,
            line=line,
            start=segment,
            orientation=orientation,
            contiguous=key not in declared,
            radius_limit=2 if key not in declared else None,
        )
        rendered_coverage, rendered_position = _segment_coverage(
            rendered_mask,
            rendered_breakpoints,
            rendered_other_breakpoints,
            line=line,
            start=segment,
            orientation=orientation,
            contiguous=key not in declared,
            radius_limit=2 if key not in declared else None,
        )
        difference = EdgeSegmentDifference(
            orientation=orientation,
            line=line,
            start=segment,
            end=segment + 1,
            source_coverage=source_coverage,
            rendered_coverage=rendered_coverage,
            source_position=source_position,
            rendered_position=rendered_position,
        )
        if (
            key in declared
            and source_coverage >= 0.5
            and rendered_coverage < 0.35
        ):
            missing.append(difference)
        elif (
            key not in declared
            and source_coverage < 0.35
            and rendered_coverage >= 0.85
        ):
            unexpected.append(difference)

    for line in range(1, len(block.row_breakpoints) - 1):
        for segment in range(len(block.column_breakpoints) - 1):
            inspect(
                "horizontal",
                line,
                segment,
                source_horizontal,
                rendered_horizontal,
                block.row_breakpoints,
                block.column_breakpoints,
                rendered_rows,
                rendered_columns,
            )
    for line in range(1, len(block.column_breakpoints) - 1):
        for segment in range(len(block.row_breakpoints) - 1):
            inspect(
                "vertical",
                line,
                segment,
                source_vertical,
                rendered_vertical,
                block.column_breakpoints,
                block.row_breakpoints,
                rendered_columns,
                rendered_rows,
            )
    def order(edge: EdgeSegmentDifference) -> tuple[str, int, int]:
        return edge.orientation, edge.line, edge.start

    return tuple(sorted(missing, key=order)), tuple(sorted(unexpected, key=order))


def _white_ratio(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    crop = image.crop(box).convert("RGB")
    white_threshold = tuple(255 if value >= 245 else 0 for value in range(256))
    red, green, blue = crop.split()
    white = ImageChops.multiply(
        red.point(white_threshold),
        ImageChops.multiply(
            green.point(white_threshold),
            blue.point(white_threshold),
        ),
    )
    histogram = white.histogram()
    return histogram[255] / max(1, crop.width * crop.height)


def _negative_space_differences(
    source: Image.Image,
    rendered: Image.Image,
    row_breakpoints: tuple[float, ...],
    column_breakpoints: tuple[float, ...],
) -> tuple[NegativeSpaceDifference, ...]:
    differences: list[NegativeSpaceDifference] = []
    for row, (top, bottom) in enumerate(zip(row_breakpoints, row_breakpoints[1:])):
        y0 = round(top * source.height)
        y1 = max(y0 + 1, round(bottom * source.height))
        for column, (left, right) in enumerate(
            zip(column_breakpoints, column_breakpoints[1:])
        ):
            x0 = round(left * source.width)
            x1 = max(x0 + 1, round(right * source.width))
            box = (x0, y0, x1, y1)
            source_white = _white_ratio(source, box)
            rendered_white = _white_ratio(rendered, box)
            if source_white >= 0.2 and source_white - rendered_white >= 0.15:
                differences.append(
                    NegativeSpaceDifference(
                        row=row,
                        column=column,
                        source_white_ratio=source_white,
                        rendered_white_ratio=rendered_white,
                    )
                )
    return tuple(
        sorted(
            differences,
            key=lambda cell: (
                -(cell.source_white_ratio - cell.rendered_white_ratio),
                cell.row,
                cell.column,
            ),
        )
    )


def _edge_ratio(image: Image.Image) -> float:
    horizontal, vertical = _normal_gradient_masks(image)
    combined = ImageChops.lighter(horizontal, vertical)
    histogram = combined.histogram()
    return histogram[255] / max(1, combined.width * combined.height)


def _protected_gap_differences(
    block: ReferenceLayoutBlock,
    source: Image.Image,
    rendered: Image.Image,
) -> tuple[ProtectedGapDifference, ...]:
    differences: list[ProtectedGapDifference] = []
    for gap in block.protected_gaps:
        left = round(block.column_breakpoints[gap.left] * source.width)
        right = round(block.column_breakpoints[gap.right] * source.width)
        top = round(block.row_breakpoints[gap.top] * source.height)
        bottom = round(block.row_breakpoints[gap.bottom] * source.height)
        box = (
            max(0, min(source.width - 1, left)),
            max(0, min(source.height - 1, top)),
            max(1, min(source.width, right)),
            max(1, min(source.height, bottom)),
        )
        if box[0] >= box[2] or box[1] >= box[3]:
            continue
        source_crop = source.crop(box)
        rendered_crop = rendered.crop(box)
        pixel_difference = ImageChops.difference(source_crop, rendered_crop).convert("L")
        histogram = pixel_difference.histogram()
        pixels = max(1, pixel_difference.width * pixel_difference.height)
        mean = sum(value * count for value, count in enumerate(histogram)) / pixels
        changed_ratio = sum(histogram[13:]) / pixels
        source_edges = _edge_ratio(source_crop)
        rendered_edges = _edge_ratio(rendered_crop)
        if changed_ratio < 0.05 or mean < 4:
            continue
        differences.append(
            ProtectedGapDifference(
                axis=gap.axis,
                top=gap.top,
                left=gap.left,
                bottom=gap.bottom,
                right=gap.right,
                mean_absolute_error=mean,
                changed_pixel_ratio=changed_ratio,
                source_edge_ratio=source_edges,
                rendered_edge_ratio=rendered_edges,
            )
        )
    return tuple(
        sorted(
            differences,
            key=lambda gap: (
                -gap.changed_pixel_ratio,
                gap.axis,
                gap.top,
                gap.left,
            ),
        )
    )


def evaluate_reference_render(
    difference: ReferenceRenderDifference,
    *,
    maximum_mean_absolute_error: float = 5,
    maximum_changed_pixel_ratio: float = 0.05,
) -> ReferenceRenderGate:
    failures: list[str] = []
    if difference.changed_row_boundaries or difference.changed_column_boundaries:
        failures.append("boundary_shift")
    if difference.missing_visible_edges:
        failures.append("missing_edge")
    if difference.unexpected_edges:
        failures.append("unexpected_edge")
    if difference.negative_space_differences:
        failures.append("negative_space_loss")
    if difference.protected_gap_differences:
        failures.append("protected_gap_intrusion")
    if difference.mean_absolute_error > maximum_mean_absolute_error:
        failures.append("mean_absolute_error")
    if difference.changed_pixel_ratio > maximum_changed_pixel_ratio:
        failures.append("changed_pixel_ratio")
    ordered = tuple(dict.fromkeys(failures))
    return ReferenceRenderGate(passed=not ordered, failures=ordered)


def compare_reference_render(
    block: ReferenceLayoutBlock,
    rendered_image: Path,
    *,
    page_geometry: SectionPageGeometry | None = None,
    page_number: int = 1,
    rendered_row_breakpoints: tuple[float, ...] | None = None,
    rendered_column_breakpoints: tuple[float, ...] | None = None,
) -> ReferenceRenderDifference:
    if block.source_image is None:
        raise ValueError("render comparison requires source_image")
    prepared = prepare_reference_layout(block)
    rendered_frame = rendered_placement(
        RenderedPage(
            path=rendered_image,
            geometry=page_geometry,
            number=page_number,
        ),
        block.source_image,
        rows=len(block.row_breakpoints) - 1,
        columns=len(block.column_breakpoints) - 1,
        visible_edges=block.visible_edges,
        styles=block.styles,
        style_regions=block.style_regions,
    )
    source_rows, source_columns = detect_layout_lines(block.source_image)
    rendered_rows, rendered_columns = detect_layout_lines_from_image(rendered_frame)
    with Image.open(block.source_image) as source:
        pixel_tolerance = max(0.002, 1.5 / max(source.size))
    rows, changed_rows = _axis_difference(
        prepared.row_breakpoints,
        source_rows,
        rendered_rows,
        pixel_tolerance=pixel_tolerance,
    )
    columns, changed_columns = _axis_difference(
        prepared.column_breakpoints,
        source_columns,
        rendered_columns,
        pixel_tolerance=pixel_tolerance,
    )
    mean, changed_ratio, difference, source, rendered = difference_metrics(
        block.source_image,
        rendered_frame,
    )
    missing_edges, unexpected_edges = _edge_differences(
        prepared,
        source,
        rendered,
        rendered_row_breakpoints=rendered_row_breakpoints,
        rendered_column_breakpoints=rendered_column_breakpoints,
    )
    return ReferenceRenderDifference(
        mean_absolute_error=mean,
        changed_pixel_ratio=changed_ratio,
        row_breakpoints=rows,
        column_breakpoints=columns,
        changed_row_boundaries=changed_rows,
        changed_column_boundaries=changed_columns,
        changed_cells=_changed_cells(difference, rows, columns),
        missing_visible_edges=missing_edges,
        unexpected_edges=unexpected_edges,
        negative_space_differences=_negative_space_differences(
            source,
            rendered,
            rows,
            columns,
        ),
        protected_gap_differences=_protected_gap_differences(
            prepared,
            source,
            rendered,
        ),
    )

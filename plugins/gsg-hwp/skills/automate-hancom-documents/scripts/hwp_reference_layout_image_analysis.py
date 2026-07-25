from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_edge_validation import (
    filter_unsupported_visible_edges,
)
from hwp_reference_layout_fill_column_topology import (
    extend_filled_regions_with_columns,
)
from hwp_reference_layout_fill_row_snapping import (
    snap_filled_region_rows,
)
from hwp_reference_layout_fill_snapping import (
    analyze_filled_region_columns,
)
from hwp_reference_layout_gap_detection import detect_protected_gaps
from hwp_reference_layout_gap_row_detection import (
    RowGapScan,
    detect_row_gap_insertions,
)
from hwp_reference_layout_gap_row_topology import insert_protected_row_gaps
from hwp_reference_layout_gap_rules import protect_detected_gaps
from hwp_reference_layout_gap_snapping import (
    preserve_occupied_narrow_columns,
    preserve_occupied_narrow_rows,
    snap_column_gap_breakpoints,
)
from hwp_reference_layout_image_evidence import (
    snap_layout_breakpoints_from_image,
)


@dataclass(frozen=True, slots=True)
class LayoutImageAnalysis:
    layout: ReferenceLayoutBlock


def analyze_layout_image(
    block: ReferenceLayoutBlock,
) -> LayoutImageAnalysis:
    source = block.source_image
    if source is None or not source.is_file():
        raise ValueError(f"reference image does not exist: {source}")
    try:
        with Image.open(source) as opened:
            rgba = opened.convert("RGBA")
            flattened = Image.new("RGBA", rgba.size, "white")
            flattened.alpha_composite(rgba)
            image = flattened.convert("RGB")
            rows, columns = snap_layout_breakpoints_from_image(
                image,
                block.row_breakpoints,
                block.column_breakpoints,
            )
            rows = snap_filled_region_rows(
                image,
                block,
                rows,
                columns,
            )
            rows = preserve_occupied_narrow_rows(block, rows)
            columns = preserve_occupied_narrow_columns(block, columns)
            fill_analysis = analyze_filled_region_columns(
                image,
                block,
                rows,
                columns,
            )
            columns = fill_analysis.breakpoints
            columns = preserve_occupied_narrow_columns(block, columns)
            columns = snap_column_gap_breakpoints(
                image,
                block,
                rows,
                columns,
            )
            snapped = block.model_copy(
                update={
                    "row_breakpoints": rows,
                    "column_breakpoints": columns,
                }
            )
            snapped = extend_filled_regions_with_columns(
                snapped,
                fill_analysis.extensions,
            )
            columns = snapped.column_breakpoints
            gaps = detect_protected_gaps(
                image,
                snapped,
                rows,
                columns,
            )
            protected = protect_detected_gaps(snapped, gaps)
            insertions = detect_row_gap_insertions(
                RowGapScan(
                    image=image,
                    block=protected,
                    row_breakpoints=rows,
                    column_breakpoints=columns,
                )
            )
            prepared = insert_protected_row_gaps(
                protected,
                rows,
                insertions,
            )
            prepared = filter_unsupported_visible_edges(image, prepared)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(
            f"reference image cannot be decoded: {source}"
        ) from error
    return LayoutImageAnalysis(layout=prepared)

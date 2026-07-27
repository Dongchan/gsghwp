from __future__ import annotations

import sys
from pathlib import Path
from typing import Never, cast

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_action_models import IntegerValue  # noqa: E402
import hwp_reference_layout_evidence  # noqa: E402
from hwp_reference_layout_contract import ReferenceMerge  # noqa: E402
from hwp_reference_layout_geometry import SectionPageGeometry  # noqa: E402
from hwp_reference_layout_native import (  # noqa: E402
    compile_reference_layout_command,
)
from pdf_vector_reference_layout import (  # noqa: E402
    PdfGridUnsupportedError,
    allocate_axis_hwpunits,
    pdf_grid_to_reference_layout_block,
)


def _edge(
    orientation: str,
    line: int,
    start: int,
    end: int,
    *,
    width: str = "0.2mm",
) -> dict[str, object]:
    return {
        "orientation": orientation,
        "line": line,
        "start": start,
        "end": end,
        "border_width_snap": {"snapped_border_width": width},
    }


def _grid(
    *,
    columns: tuple[float, ...] = (10.0, 40.0, 100.0),
    rows: tuple[float, ...] = (20.0, 45.0, 100.0),
    edges: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    return {
        "column_boundaries_pt": columns,
        "row_boundaries_pt": rows,
        "visible_edges": edges,
    }


def test_axis_allocation_uses_fraction_and_largest_remainder() -> None:
    allocation = allocate_axis_hwpunits((10.004, 10.338, 10.671, 11.004))

    assert allocation.origin_hwpunit == 1000
    assert allocation.extent_hwpunit == 100
    assert allocation.sizes_hwpunit == (34, 33, 33)
    assert sum(allocation.sizes_hwpunit) == allocation.extent_hwpunit
    assert allocation.normalized_breakpoints == (0.0, 0.34, 0.67, 1.0)


def test_grid_converts_irregular_axes_and_coalesces_edge_coverage() -> None:
    block = pdf_grid_to_reference_layout_block(
        _grid(
            edges=(
                _edge("horizontal", 0, 0, 1),
                _edge("horizontal", 0, 1, 2),
                _edge("vertical", 0, 0, 2, width="0.5mm"),
            )
        )
    )

    assert block.model_dump(mode="json", exclude_none=True)["kind"] == (
        "reference_layout"
    )
    assert block.column_breakpoints == (0.0, 1 / 3, 1.0)
    assert block.row_breakpoints == (0.0, 0.3125, 1.0)
    assert tuple(
        (
            edge.orientation,
            edge.line,
            edge.start,
            edge.end,
            edge.style,
            edge.width,
            edge.color,
        )
        for edge in block.visible_edges
    ) == (
        ("horizontal", 0, 0, 2, "solid", "0.2mm", (0, 0, 0)),
        ("vertical", 0, 0, 2, "solid", "0.5mm", (0, 0, 0)),
    )


def test_explicit_rectangular_merge_is_preserved() -> None:
    block = pdf_grid_to_reference_layout_block(
        _grid(
            edges=(
                _edge("horizontal", 0, 0, 2),
                _edge("horizontal", 1, 0, 2),
                _edge("horizontal", 2, 0, 2),
                _edge("vertical", 0, 0, 2),
                _edge("vertical", 2, 0, 2),
                _edge("vertical", 1, 1, 2),
            )
        ),
        merges=(ReferenceMerge(row=0, column=0, column_span=2),),
    )

    assert block.merges == (ReferenceMerge(row=0, column=0, row_span=1, column_span=2),)


def test_edge_crossing_explicit_merge_is_reported_as_unsupported() -> None:
    with pytest.raises(
        PdfGridUnsupportedError,
        match="crosses an explicit merge",
    ):
        _ = pdf_grid_to_reference_layout_block(
            _grid(edges=(_edge("vertical", 1, 0, 1),)),
            merges=(ReferenceMerge(row=0, column=0, column_span=2),),
        )


def test_grid_over_native_axis_limit_is_reported_as_unsupported() -> None:
    with pytest.raises(PdfGridUnsupportedError, match="50"):
        _ = pdf_grid_to_reference_layout_block(
            _grid(columns=tuple(float(value) for value in range(52)))
        )


def test_quantized_axis_collapse_is_reported_as_unsupported() -> None:
    with pytest.raises(PdfGridUnsupportedError, match="collapses"):
        _ = allocate_axis_hwpunits((0.0, 0.004, 1.0))


def test_converted_block_compiles_through_reference_layout_bulk() -> None:
    block = pdf_grid_to_reference_layout_block(
        _grid(
            edges=(
                _edge("horizontal", 0, 0, 2),
                _edge("horizontal", 2, 0, 2),
                _edge("vertical", 0, 0, 2),
                _edge("vertical", 2, 0, 2),
            )
        )
    )
    page = SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=15,
        header_mm=0,
        footer_mm=0,
        gutter_mm=0,
        gutter_type=0,
    )

    command = compile_reference_layout_command(
        block,
        page,
        page_number=1,
        base_style_id=0,
    )

    assert command.action == "ReferenceLayoutBulk"
    arrays = {array.name: array.count for array in command.arrays}
    assert arrays["ColumnWidths"] == 2
    assert arrays["RowHeights"] == 2
    setters = {
        setter.path: cast(IntegerValue, setter.value).value
        for setter in command.setters
    }
    column_widths = tuple(
        cast(IntegerValue, value.value).value
        for value in command.array_values
        if value.name == "ColumnWidths"
    )
    row_heights = tuple(
        cast(IntegerValue, value.value).value
        for value in command.array_values
        if value.name == "RowHeights"
    )
    assert sum(column_widths) == setters["BodyWidth"]
    assert sum(row_heights) == setters["BodyHeight"]
    assert column_widths[0] != column_widths[1]
    assert row_heights[0] != row_heights[1]


def test_source_image_requires_analysis_id_for_vector_edge_authority(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        PdfGridUnsupportedError,
        match="analysis_id",
    ):
        _ = pdf_grid_to_reference_layout_block(
            _grid(edges=(_edge("horizontal", 0, 0, 2),)),
            source_image=tmp_path / "grid.png",
        )


def test_analysis_id_routes_preparation_without_raster_edge_filtering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    analysis = object()
    aligned: list[object] = []

    def load_cached_analysis(source_image: Path, analysis_id: str) -> object:
        _ = source_image, analysis_id
        return analysis

    monkeypatch.setattr(
        hwp_reference_layout_evidence,
        "load_cached_reference_image_analysis",
        load_cached_analysis,
    )

    def preserve_vector_layout(block: object, cached: object) -> object:
        assert cached is analysis
        aligned.append(block)
        return block

    monkeypatch.setattr(
        hwp_reference_layout_evidence,
        "align_reference_layout_to_analysis",
        preserve_vector_layout,
    )

    def reject_raster_analysis(block: object) -> Never:
        _ = block
        pytest.fail("analysis_id must bypass raster edge filtering")

    monkeypatch.setattr(
        hwp_reference_layout_evidence,
        "analyze_layout_image",
        reject_raster_analysis,
    )

    block = pdf_grid_to_reference_layout_block(
        _grid(
            edges=(
                _edge("horizontal", 0, 0, 1),
                _edge("horizontal", 0, 1, 2),
            )
        ),
        source_image=tmp_path / "grid.png",
        analysis_id="ria-0123456789abcdef",
    )
    prepared = hwp_reference_layout_evidence.prepare_reference_layout(block)

    assert block.analysis_id == "ria-0123456789abcdef"
    assert prepared.visible_edges == block.visible_edges
    assert len(prepared.visible_edges) == 1
    assert aligned == [block]

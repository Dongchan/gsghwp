from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_reference_layout_contract import ReferenceLayoutBlock  # noqa: E402
from hwp_reference_layout_evidence import prepare_reference_layout  # noqa: E402
from hwp_reference_layout_fill_snapping import (  # noqa: E402
    analyze_filled_region_columns,
)
from hwp_reference_layout_geometry import SectionPageGeometry  # noqa: E402
from hwp_reference_layout_native import compile_reference_layout_command  # noqa: E402


FIXTURES = (
    "reference_layout_vision_goals.json",
    "reference_layout_namwon_strategy.json",
    "reference_layout_three_theme_flow.json",
    "reference_layout_carbon_org_chart.json",
    "reference_layout_building_overview.json",
)

MINIMUM_GAP_COUNTS = {
    "reference_layout_vision_goals.json": {"column": 2, "row": 3},
    "reference_layout_namwon_strategy.json": {"column": 0, "row": 2},
    "reference_layout_three_theme_flow.json": {"column": 2, "row": 2},
    "reference_layout_carbon_org_chart.json": {"column": 3, "row": 1},
    "reference_layout_building_overview.json": {"column": 0, "row": 1},
}


def _page() -> SectionPageGeometry:
    return SectionPageGeometry.from_mm(
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


def _integer_arrays(
    block: ReferenceLayoutBlock,
) -> tuple[dict[str, int], dict[str, list[int]]]:
    command = compile_reference_layout_command(
        block,
        _page(),
        page_number=1,
        base_style_id=0,
    )
    setters: dict[str, int] = {}
    for setter in command.setters:
        if not hasattr(setter.value, "value"):
            continue
        raw = setter.value.value
        if isinstance(raw, int):
            setters[setter.path] = raw
    arrays: dict[str, list[int]] = {
        array.name: [0] * array.count
        for array in command.arrays
    }
    for value in command.array_values:
        if value.name in arrays and hasattr(value.value, "value"):
            raw = value.value.value
            if isinstance(raw, int):
                arrays[value.name][value.index] = raw
    return setters, arrays


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_sample_gutter_and_section_spacing_round_trip_within_one_pixel(
    fixture_name: str,
) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / fixture_name
    block = ReferenceLayoutBlock.model_validate(
        json.loads(fixture_path.read_text(encoding="utf-8"))
    )

    prepared = prepare_reference_layout(block)
    setters, arrays = _integer_arrays(prepared)

    assert prepared.protected_gaps, f"{fixture_name} detected no protected gap"
    counts = {
        "column": len(
            {
                (gap.left, gap.right)
                for gap in prepared.protected_gaps
                if gap.axis == "column"
            }
        ),
        "row": len(
            {
                (gap.top, gap.bottom)
                for gap in prepared.protected_gaps
                if gap.axis == "row"
            }
        ),
    }
    for axis, minimum in MINIMUM_GAP_COUNTS[fixture_name].items():
        assert counts[axis] >= minimum, (
            f"{fixture_name} detected {counts[axis]} {axis} gaps; "
            f"expected at least {minimum}"
        )
    assert len(arrays["GapMinimums"]) == len(prepared.protected_gaps)
    assert prepared.source_image is not None
    with Image.open(prepared.source_image) as opened:
        image = opened.convert("RGB")
        width_px, height_px = image.size
        measured_columns = analyze_filled_region_columns(
            image,
            block,
            block.row_breakpoints,
            block.column_breakpoints,
        ).breakpoints
    for _, right in {
        (gap.left, gap.right)
        for gap in prepared.protected_gaps
        if gap.axis == "column"
    }:
        prepared_right = prepared.column_breakpoints[right]
        original_right = min(
            range(len(block.column_breakpoints)),
            key=lambda index: abs(
                block.column_breakpoints[index] - prepared_right
            ),
        )
        if (
            original_right + 1 >= len(block.column_breakpoints)
            or abs(
                block.column_breakpoints[original_right] - prepared_right
            )
            * width_px
            > 1
        ):
            continue
        original_neighbor = (
            block.column_breakpoints[original_right + 1]
            - block.column_breakpoints[original_right]
        )
        prepared_next = min(
            range(right + 1, len(prepared.column_breakpoints)),
            key=lambda index: abs(
                prepared.column_breakpoints[index]
                - block.column_breakpoints[original_right + 1]
            ),
        )
        prepared_neighbor = (
            prepared.column_breakpoints[prepared_next]
            - prepared.column_breakpoints[right]
        )
        neighbor_error_px = (
            abs(prepared_neighbor - original_neighbor) * width_px
        )
        assert neighbor_error_px <= 1.0 or any(
            abs(
                candidate
                - prepared.column_breakpoints[prepared_next]
            )
            * width_px
            <= 1.0
            for candidate in measured_columns
        )
    for gap, minimum in zip(
        prepared.protected_gaps,
        arrays["GapMinimums"],
        strict=True,
    ):
        if gap.axis == "column":
            source_extent = (
                prepared.column_breakpoints[gap.right]
                - prepared.column_breakpoints[gap.left]
            )
            error_px = abs(
                minimum / setters["BodyWidth"] * width_px
                - source_extent * width_px
            )
        else:
            source_extent = (
                prepared.row_breakpoints[gap.bottom]
                - prepared.row_breakpoints[gap.top]
            )
            error_px = abs(
                minimum / setters["BodyHeight"] * height_px
                - source_extent * height_px
            )
        assert error_px <= 1.0, (
            f"{fixture_name} {gap.axis} gap round-trip error={error_px:.3f}px"
        )

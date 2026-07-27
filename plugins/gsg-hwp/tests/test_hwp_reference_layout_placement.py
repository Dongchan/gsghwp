from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image, ImageDraw


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_action_models import ParameterActionCommand  # noqa: E402
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    VisibleEdge,
)
from hwp_reference_layout_geometry import (  # noqa: E402
    SectionPageGeometry,
    mm_to_hwpunit,
)
from hwp_reference_layout_native import compile_reference_layout_command  # noqa: E402
from hwp_reference_layout_patch import (  # noqa: E402
    ReferenceLayoutPatchBlock,
    compile_reference_layout_patch_command,
)
from hwp_reference_layout_patch_builder import (  # noqa: E402
    build_reference_layout_patch,
)
from hwp_reference_layout_placement import PlacementFrame  # noqa: E402
from hwp_reference_layout_refinement import compare_reference_render  # noqa: E402


SAMPLE_FIXTURES = (
    "reference_layout_vision_goals.json",
    "reference_layout_namwon_strategy.json",
    "reference_layout_three_theme_flow.json",
    "reference_layout_carbon_org_chart.json",
    "reference_layout_building_overview.json",
)


@dataclass(slots=True)
class _ReferenceRenderDifference:
    row_breakpoints: tuple[float, ...]
    column_breakpoints: tuple[float, ...]
    changed_row_boundaries: tuple[int, ...] = ()
    changed_column_boundaries: tuple[int, ...] = ()


def _page() -> SectionPageGeometry:
    return SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=10.5,
        header_mm=0,
        footer_mm=0,
        gutter_mm=0,
        gutter_type=0,
    )


def _integer_setter(command: ParameterActionCommand, path: str) -> int:
    setter = next(item for item in command.setters if item.path == path)
    value = setter.value.value
    assert type(value) is int
    return value


def _integer_array_sum(command: ParameterActionCommand, name: str) -> int:
    total = 0
    for item in command.array_values:
        if item.name != name:
            continue
        value = item.value.value
        assert type(value) is int
        total += value
    return total


@pytest.mark.parametrize("fixture_name", SAMPLE_FIXTURES)
def test_sample_layout_uses_top_center_contain_frame(fixture_name: str) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / fixture_name
    block = ReferenceLayoutBlock.model_validate(
        json.loads(fixture_path.read_text(encoding="utf-8"))
    )
    assert block.source_image is not None
    page = _page()
    area = page.usable_area(page_number=1)
    expected = PlacementFrame.from_reference(
        area,
        block.source_image,
        rows=len(block.row_breakpoints) - 1,
        columns=len(block.column_breakpoints) - 1,
        visible_edges=block.visible_edges,
        styles=block.styles,
        style_regions=block.style_regions,
    )

    command = compile_reference_layout_command(
        block,
        page,
        page_number=1,
        base_style_id=0,
    )

    assert _integer_setter(command, "BodyLeft") == expected.left
    assert _integer_setter(command, "BodyTop") == expected.top
    assert _integer_setter(command, "BodyWidth") == expected.width
    assert _integer_setter(command, "BodyHeight") == expected.height
    assert _integer_array_sum(command, "ColumnWidths") == expected.width
    assert _integer_array_sum(command, "RowHeights") == expected.height
    assert expected.width <= area.width
    assert expected.height <= area.height


def test_outer_edge_paint_stays_inside_reserved_body_area(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tall.png"
    Image.new("RGB", (100, 200), "white").save(source)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        visible_edges=(
            VisibleEdge(
                orientation="horizontal",
                line=1,
                start=0,
                end=1,
                width="0.3mm",
            ),
            VisibleEdge(
                orientation="vertical",
                line=0,
                start=0,
                end=1,
                width="0.3mm",
            ),
            VisibleEdge(
                orientation="vertical",
                line=1,
                start=0,
                end=1,
                width="0.3mm",
            ),
        ),
    )
    page = _page()
    area = page.usable_area(page_number=1)
    command = compile_reference_layout_command(
        block,
        page,
        page_number=1,
        base_style_id=0,
    )
    left = _integer_setter(command, "BodyLeft")
    top = _integer_setter(command, "BodyTop")
    width = _integer_setter(command, "BodyWidth")
    height = _integer_setter(command, "BodyHeight")
    clearance = mm_to_hwpunit(0.3)

    assert left >= area.left + clearance
    assert left + width <= area.left + area.width - clearance
    assert top + height <= area.top + area.height - clearance


def test_outer_fill_paint_stays_inside_reserved_body_area(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tall.png"
    Image.new("RGB", (100, 200), "white").save(source)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(
            ReferenceStyle(
                key="painted",
                fill_color=(253, 243, 239),
            ),
        ),
        style_regions=(
            StyleRegion(
                top=0,
                left=0,
                bottom=1,
                right=1,
                style_key="painted",
            ),
        ),
    )
    page = _page()
    area = page.usable_area(page_number=1)
    command = compile_reference_layout_command(
        block,
        page,
        page_number=1,
        base_style_id=0,
    )
    top = _integer_setter(command, "BodyTop")
    height = _integer_setter(command, "BodyHeight")

    assert top + height <= area.top + area.height - mm_to_hwpunit(0.3)


def test_patch_reuses_source_placement_frame(tmp_path: Path) -> None:
    source = tmp_path / "wide.png"
    Image.new("RGB", (200, 100), "white").save(source)
    page = _page()
    area = page.usable_area(page_number=1)
    patch = ReferenceLayoutPatchBlock(
        kind="reference_layout_patch",
        target_control_id="table-1",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 0.5, 1.0),
        changed_rows=(0, 1),
        changed_columns=(0, 1),
    )

    command = compile_reference_layout_patch_command(
        patch,
        page,
        page_number=1,
    )

    assert _integer_setter(command, "BodyWidth") == area.width
    assert _integer_setter(command, "BodyHeight") == round(area.width / 2)
    assert _integer_array_sum(command, "ColumnWidths") == area.width
    assert _integer_array_sum(command, "RowHeights") == round(area.width / 2)


def test_patch_reuses_outer_fill_placement_frame(tmp_path: Path) -> None:
    source = tmp_path / "tall.png"
    Image.new("RGB", (100, 200), "white").save(source)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(
            ReferenceStyle(
                key="painted",
                fill_color=(253, 243, 239),
            ),
        ),
        style_regions=(
            StyleRegion(
                top=0,
                left=0,
                bottom=1,
                right=1,
                style_key="painted",
            ),
        ),
    )
    page = _page()
    bulk = compile_reference_layout_command(
        block,
        page,
        page_number=1,
        base_style_id=0,
    )
    patch = build_reference_layout_patch(
        block,
        _ReferenceRenderDifference(
            row_breakpoints=block.row_breakpoints,
            column_breakpoints=block.column_breakpoints,
        ),
        target_control_id="table-1",
        styles=block.styles,
        style_regions=block.style_regions,
    )
    patched = compile_reference_layout_patch_command(
        patch,
        page,
        page_number=1,
    )

    for path in ("BodyLeft", "BodyTop", "BodyWidth", "BodyHeight"):
        assert _integer_setter(patched, path) == _integer_setter(bulk, path)


def test_render_comparison_accepts_uniform_top_center_containment(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.png"
    rendered_path = tmp_path / "rendered.png"
    source = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(source).rectangle((0, 0, 199, 49), fill="black")
    source.save(source_path)
    rendered = Image.new("RGB", (100, 100), "white")
    rendered.paste(source.resize((100, 50), Image.Resampling.NEAREST), (0, 0))
    rendered.save(rendered_path)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source_path,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
    )

    difference = compare_reference_render(block, rendered_path)

    assert difference.mean_absolute_error < 2
    assert difference.changed_pixel_ratio <= 0.02


def test_render_comparison_exposes_non_uniform_stretch(tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    rendered_path = tmp_path / "rendered.png"
    source = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(source).rectangle((0, 0, 199, 49), fill="black")
    source.save(source_path)
    source.resize((100, 100), Image.Resampling.NEAREST).save(rendered_path)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source_path,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
    )

    difference = compare_reference_render(block, rendered_path)

    assert difference.mean_absolute_error > 100
    assert difference.changed_pixel_ratio > 0.45

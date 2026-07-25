from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_contract import LayoutPlan  # noqa: E402
from hwp_live_native_action_contract import encode_action_request  # noqa: E402
from hwp_live_native_action_models import ParameterActionCommand  # noqa: E402
from hwp_live_native_action_models import NativeActionRequest  # noqa: E402
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceMerge,
    ReferenceStyle,
    StyleRegion,
    VisibleEdge,
)
from hwp_reference_layout_geometry import SectionPageGeometry  # noqa: E402
from hwp_reference_layout_gap import ProtectedGap  # noqa: E402
from hwp_reference_layout_patch import (  # noqa: E402
    ReferenceLayoutPatchBlock,
    compile_reference_layout_patch_command,
)
from hwp_reference_layout_refinement import (  # noqa: E402
    _edge_differences,
    compare_reference_render,
    evaluate_reference_render,
)


def _grid(
    path: Path,
    *,
    vertical: int,
    horizontal: int,
    width: int = 101,
    height: int = 101,
) -> None:
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    draw.line((vertical, 0, vertical, height - 1), fill=0, width=2)
    draw.line((0, horizontal, width - 1, horizontal), fill=0, width=2)
    image.save(path)


def test_render_diff_reports_only_displaced_grid_boundaries(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    _grid(source, vertical=50, horizontal=50)
    _grid(rendered, vertical=56, horizontal=47)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 0.5, 1.0),
    )

    difference = compare_reference_render(block, rendered)

    assert difference.changed_column_boundaries == (1,)
    assert difference.changed_row_boundaries == (1,)
    assert difference.column_breakpoints == pytest.approx((0.0, 0.5, 1.0), abs=0.006)
    assert difference.row_breakpoints == pytest.approx((0.0, 0.5, 1.0), abs=0.006)
    assert difference.changed_pixel_ratio > 0


def test_render_diff_crops_to_the_computed_body_area(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered-page.png"
    _grid(source, vertical=35, horizontal=30, width=70, height=60)
    with Image.open(source) as opened:
        source_body = opened.convert("RGB")
    page_image = Image.new("RGB", (100, 100), "white")
    page_image.paste(source_body, (10, 15))
    page_image.save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 0.5, 1.0),
    )
    page = SectionPageGeometry.from_mm(
        paper_width_mm=100,
        paper_height_mm=100,
        landscape=False,
        left_margin_mm=10,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=25,
        header_mm=0,
        footer_mm=0,
        gutter_mm=0,
        gutter_type=0,
    )

    difference = compare_reference_render(
        block,
        rendered,
        page_geometry=page,
        page_number=1,
    )

    assert difference.changed_row_boundaries == ()
    assert difference.changed_column_boundaries == ()
    assert difference.mean_absolute_error < 1
    assert difference.changed_pixel_ratio == 0


def test_render_diff_reports_missing_declared_edge(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    source_image = Image.new("RGB", (101, 101), "white")
    ImageDraw.Draw(source_image).line((0, 50, 100, 50), fill="black", width=2)
    source_image.save(source)
    Image.new("RGB", (101, 101), "white").save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
        visible_edges=(
            VisibleEdge(
                orientation="horizontal",
                line=1,
                start=0,
                end=1,
            ),
        ),
    )

    difference = compare_reference_render(block, rendered)

    assert [
        (edge.orientation, edge.line, edge.start, edge.end)
        for edge in difference.missing_visible_edges
    ] == [("horizontal", 1, 0, 1)]
    assert difference.unexpected_edges == ()


def test_render_diff_uses_inspected_rendered_edge_geometry(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    source_image = Image.new("RGB", (101, 101), "white")
    ImageDraw.Draw(source_image).line((0, 50, 100, 50), fill="black", width=2)
    source_image.save(source)
    rendered_image = Image.new("RGB", (101, 101), "white")
    ImageDraw.Draw(rendered_image).line((0, 60, 100, 60), fill="black", width=2)
    rendered_image.save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
        visible_edges=(
            VisibleEdge(
                orientation="horizontal",
                line=1,
                start=0,
                end=1,
            ),
        ),
    )

    difference = compare_reference_render(
        block,
        rendered,
        rendered_row_breakpoints=(0.0, 0.6, 1.0),
    )

    assert difference.missing_visible_edges == ()


def test_render_diff_reports_undeclared_extra_edge(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    Image.new("RGB", (101, 101), "white").save(source)
    rendered_image = Image.new("RGB", (101, 101), "white")
    ImageDraw.Draw(rendered_image).line((0, 50, 100, 50), fill="black", width=2)
    rendered_image.save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
    )

    difference = compare_reference_render(block, rendered)

    assert difference.missing_visible_edges == ()
    assert [
        (edge.orientation, edge.line, edge.start, edge.end)
        for edge in difference.unexpected_edges
    ] == [("horizontal", 1, 0, 1)]


def test_render_diff_does_not_report_colored_fill_transition_as_extra_edge(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    Image.new("RGB", (101, 101), "white").save(source)
    rendered_image = Image.new("RGB", (101, 101), "white")
    ImageDraw.Draw(rendered_image).rectangle((0, 0, 100, 49), fill="#1c94cf")
    rendered_image.save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
    )

    difference = compare_reference_render(block, rendered)

    assert difference.unexpected_edges == ()


def test_render_diff_does_not_report_disconnected_dark_text_as_extra_edge(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    Image.new("RGB", (101, 101), "white").save(source)
    rendered_image = Image.new("RGB", (101, 101), "white")
    draw = ImageDraw.Draw(rendered_image)
    for start in range(0, 100, 10):
        draw.line((start, 50, start + 6, 50), fill="black", width=2)
    rendered_image.save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
    )

    difference = compare_reference_render(block, rendered)

    assert difference.unexpected_edges == ()


def test_render_diff_does_not_treat_offset_shadow_as_the_expected_border(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    source_image = Image.new("RGB", (101, 101), "#eef6ec")
    ImageDraw.Draw(source_image).rectangle((0, 0, 100, 49), fill="white")
    ImageDraw.Draw(source_image).line((0, 54, 100, 54), fill="#777777", width=2)
    source_image.save(source)
    rendered_image = Image.new("RGB", (101, 101), "#eef6ec")
    ImageDraw.Draw(rendered_image).rectangle((0, 0, 100, 49), fill="white")
    ImageDraw.Draw(rendered_image).line((0, 50, 100, 50), fill="#777777", width=2)
    rendered_image.save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
    )

    missing, unexpected = _edge_differences(
        block,
        source_image,
        rendered_image,
    )
    gate = evaluate_reference_render(compare_reference_render(block, rendered))

    assert missing == ()
    assert [
        (edge.orientation, edge.line, edge.start, edge.end) for edge in unexpected
    ] == [("horizontal", 1, 0, 1)]
    assert gate.passed is False


def test_render_diff_reports_lost_negative_space(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    source_image = Image.new("RGB", (100, 100), "#dbe5ef")
    ImageDraw.Draw(source_image).rectangle((0, 35, 99, 64), fill="white")
    source_image.save(source)
    Image.new("RGB", (100, 100), "#dbe5ef").save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
    )

    difference = compare_reference_render(block, rendered)

    assert len(difference.negative_space_differences) == 1
    gap = difference.negative_space_differences[0]
    assert (gap.row, gap.column) == (0, 0)
    assert gap.source_white_ratio >= 0.29
    assert gap.rendered_white_ratio == 0


def test_render_diff_reports_intrusion_inside_a_protected_gap(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "rendered.png"
    Image.new("RGB", (100, 100), "#eef6ec").save(source)
    rendered_image = Image.new("RGB", (100, 100), "#eef6ec")
    ImageDraw.Draw(rendered_image).line((0, 50, 99, 50), fill="#777777", width=2)
    rendered_image.save(rendered)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.4, 0.6, 1.0),
        column_breakpoints=(0.0, 1.0),
        protected_gaps=(
            ProtectedGap(
                axis="row",
                top=1,
                left=0,
                bottom=2,
                right=1,
            ),
        ),
    )

    difference = compare_reference_render(block, rendered)
    gate = evaluate_reference_render(difference)

    assert len(difference.protected_gap_differences) == 1
    assert difference.protected_gap_differences[0].changed_pixel_ratio >= 0.09
    assert gate.passed is False
    assert "protected_gap_intrusion" in gate.failures


def test_patch_compiles_without_recreating_the_table() -> None:
    patch = ReferenceLayoutPatchBlock(
        kind="reference_layout_patch",
        target_control_id="123456",
        row_breakpoints=(0.0, 0.45, 1.0),
        column_breakpoints=(0.0, 0.4, 1.0),
        changed_rows=(0, 1),
        changed_columns=(0, 1),
        styles=(
            ReferenceStyle(
                key="body",
                font_name="함초롬바탕",
                font_size_pt=9,
                line_spacing_percent=100,
            ),
        ),
        style_regions=(
            StyleRegion(top=0, left=0, bottom=1, right=2, style_key="body"),
        ),
        edges=(
            VisibleEdge(
                orientation="horizontal",
                line=1,
                start=0,
                end=2,
                style="solid",
            ),
        ),
    )
    page = SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=15,
        header_mm=10,
        footer_mm=10,
        gutter_mm=0,
        gutter_type=0,
    )

    command = compile_reference_layout_patch_command(
        patch,
        page,
        page_number=1,
    )

    assert isinstance(command, ParameterActionCommand)
    assert command.action == "ReferenceLayoutPatch"
    assert command.action not in {"TableCreate", "ReferenceLayoutBulk"}
    arrays = {array.name: array.count for array in command.arrays}
    assert arrays["PatchColumnIndexes"] == 2
    assert arrays["PatchRowIndexes"] == 2
    payload = encode_action_request(
        NativeActionRequest(
            document_id=1,
            full_name=r"C:\GSG_HWP_QA\sample.hwp",
            commands=(command,),
        )
    )
    assert "ACTION\tReferenceLayoutPatch\t" in payload
    assert "ACTION\tTableCreate\t" not in payload


def test_patch_cannot_be_routed_as_a_new_page_insertion() -> None:
    patch = ReferenceLayoutPatchBlock(
        kind="reference_layout_patch",
        target_control_id="123456",
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        changed_rows=(0,),
    )

    with pytest.raises(ValidationError, match="target=current"):
        LayoutPlan(target="after_page", page=1, blocks=(patch,))


def test_patch_style_region_cannot_cut_through_an_existing_merge() -> None:
    with pytest.raises(ValidationError, match="partially cover a merged cell"):
        ReferenceLayoutPatchBlock(
            kind="reference_layout_patch",
            target_control_id="123456",
            row_breakpoints=(0.0, 0.5, 1.0),
            column_breakpoints=(0.0, 0.5, 1.0),
            merges=(ReferenceMerge(row=0, column=0, column_span=2),),
            styles=(ReferenceStyle(key="body"),),
            style_regions=(
                StyleRegion(
                    top=0,
                    left=1,
                    bottom=1,
                    right=2,
                    style_key="body",
                ),
            ),
        )

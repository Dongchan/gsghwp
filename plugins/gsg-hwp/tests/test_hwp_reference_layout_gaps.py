from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
    VisibleEdge,
)
from hwp_reference_layout_evidence import prepare_reference_layout  # noqa: E402
from hwp_reference_layout_gap import ProtectedGap  # noqa: E402
from hwp_reference_layout_geometry import (  # noqa: E402
    SectionPageGeometry,
    map_normalized_breakpoints,
)
from hwp_reference_layout_native import compile_reference_layout_command  # noqa: E402


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


def test_image_evidence_detects_textless_row_and_column_gaps(
    tmp_path: Path,
) -> None:
    source = tmp_path / "boxes-with-gaps.png"
    image = Image.new("RGB", (300, 180), "white")
    draw = ImageDraw.Draw(image)
    for box in (
        (4, 4, 126, 68),
        (174, 4, 295, 68),
        (4, 103, 126, 175),
        (174, 103, 295, 175),
    ):
        draw.rectangle(box, fill=(220, 235, 245), outline=(20, 60, 100), width=2)
    image.save(source)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.4, 0.55, 1.0),
        column_breakpoints=(0.0, 0.42, 0.58, 1.0),
        visible_edges=(
            VisibleEdge(orientation="vertical", line=1, start=0, end=1),
            VisibleEdge(orientation="vertical", line=2, start=0, end=1),
            VisibleEdge(orientation="vertical", line=1, start=2, end=3),
            VisibleEdge(orientation="vertical", line=2, start=2, end=3),
        ),
        styles=(ReferenceStyle(key="panel", fill_color=(220, 235, 245)),),
        style_regions=(
            StyleRegion(top=0, left=0, bottom=3, right=3, style_key="panel"),
        ),
        text_anchors=(
            TextAnchor(row=0, column=0, text="왼쪽 위"),
            TextAnchor(row=0, column=2, text="오른쪽 위"),
            TextAnchor(row=2, column=0, text="왼쪽 아래"),
            TextAnchor(row=2, column=2, text="오른쪽 아래"),
        ),
    )

    prepared = prepare_reference_layout(block)

    assert any(
        gap.axis == "row" and gap.top == 1 and gap.bottom == 2
        for gap in prepared.protected_gaps
    )
    assert any(
        gap.axis == "column" and gap.left == 1 and gap.right == 2
        for gap in prepared.protected_gaps
    )
    assert all(
        not (
            region.top < gap.bottom
            and gap.top < region.bottom
            and region.left < gap.right
            and gap.left < region.right
        )
        for gap in prepared.protected_gaps
        for region in prepared.style_regions
    )


def test_protected_row_gap_is_not_a_text_height_donor() -> None:
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.15, 0.25, 1.0),
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
        styles=(
            ReferenceStyle(
                key="body",
                font_size_pt=9,
                line_spacing_percent=100,
            ),
        ),
        text_anchors=(
            TextAnchor(row=0, column=0, text="첫째 줄\n둘째 줄", style_key="body"),
        ),
    )

    page = _page()
    command = compile_reference_layout_command(
        block,
        page,
        page_number=1,
        base_style_id=0,
    )
    adjusted = tuple(
        value.value.value
        for value in command.array_values
        if value.name == "RowHeights"
    )
    expected = map_normalized_breakpoints(
        block.row_breakpoints,
        origin=page.usable_area(page_number=1).top,
        extent=page.usable_area(page_number=1).height,
    ).sizes

    assert adjusted == expected
    assert adjusted[1] == expected[1]


def test_bulk_payload_carries_gap_geometry_and_minimum() -> None:
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.25, 0.4, 1.0),
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

    command = compile_reference_layout_command(
        block,
        _page(),
        page_number=1,
        base_style_id=0,
    )
    arrays = {array.name: array.count for array in command.arrays}
    values = {
        (value.name, value.index): value.value.value
        for value in command.array_values
    }

    assert arrays["GapAxes"] == 1
    assert arrays["GapTop"] == 1
    assert arrays["GapMinimums"] == 1
    assert values["GapAxes", 0] == 0
    assert values["GapMinimums", 0] == values["RowHeights", 1]

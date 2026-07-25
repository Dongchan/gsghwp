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
    VisibleEdge,
)
from hwp_reference_layout_edge_validation import (  # noqa: E402
    filter_unsupported_visible_edges,
)
from hwp_reference_layout_image_analysis import analyze_layout_image  # noqa: E402


def _single_vertical_edge(
    path: Path,
    *,
    color: tuple[int, int, int],
) -> ReferenceLayoutBlock:
    return ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=path,
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 0.5, 1.0),
        visible_edges=(
            VisibleEdge(
                orientation="vertical",
                line=1,
                start=0,
                end=1,
                color=color,
            ),
        ),
    )


def test_filter_keeps_a_real_thin_border(tmp_path: Path) -> None:
    source = tmp_path / "thin-border.png"
    image = Image.new("RGB", (121, 81), "white")
    ImageDraw.Draw(image).line((60, 0, 60, 80), fill=(40, 40, 40), width=2)
    image.save(source)
    block = _single_vertical_edge(source, color=(40, 40, 40))

    filtered = filter_unsupported_visible_edges(image, block)

    assert filtered.visible_edges == block.visible_edges


def test_filter_rejects_a_fill_boundary_as_a_border(tmp_path: Path) -> None:
    source = tmp_path / "fill-boundary.png"
    image = Image.new("RGB", (121, 81), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 60, 80), fill=(28, 148, 207))
    image.save(source)
    block = _single_vertical_edge(source, color=(28, 148, 207))

    filtered = filter_unsupported_visible_edges(image, block)

    assert filtered.visible_edges == ()


def test_filter_rejects_a_partially_supported_solid_edge(tmp_path: Path) -> None:
    source = tmp_path / "partial-border.png"
    image = Image.new("RGB", (121, 81), "white")
    ImageDraw.Draw(image).line((60, 0, 60, 31), fill=(40, 40, 40), width=2)
    image.save(source)
    block = _single_vertical_edge(source, color=(40, 40, 40))

    filtered = filter_unsupported_visible_edges(image, block)

    assert filtered.visible_edges == ()


def test_analysis_rejects_a_wide_drop_shadow_as_a_border(tmp_path: Path) -> None:
    source = tmp_path / "shadow.png"
    image = Image.new("RGB", (121, 81), (220, 242, 248))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 8, 59, 72), fill="white")
    shadow_colors = (
        (230, 234, 235),
        (205, 211, 212),
        (170, 178, 180),
        (155, 164, 166),
        (180, 192, 196),
        (205, 220, 225),
    )
    for offset, color in enumerate(shadow_colors):
        draw.line((60 + offset, 8, 60 + offset, 72), fill=color)
    image.save(source)
    block = _single_vertical_edge(source, color=(190, 190, 190))

    analyzed = analyze_layout_image(block).layout

    assert analyzed.visible_edges == ()

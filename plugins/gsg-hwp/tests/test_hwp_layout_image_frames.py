from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_contract import (  # noqa: E402
    ImageBlock,
    LayoutPlan,
    ParagraphBlock,
    TableBlock,
)


def test_table_cell_image_expands_to_a_bordered_native_table(tmp_path: Path) -> None:
    image_path = tmp_path / "source.png"
    with Image.new("RGB", (150, 70)) as image:
        image.save(image_path)
    plan = LayoutPlan.model_validate(
        {
            "target": "document_end",
            "blocks": [
                {
                    "kind": "image",
                    "path": str(image_path),
                    "width_mm": 150,
                    "height_mm": 70,
                    "container": "table_cell",
                    "caption": "현황 및 적용 사례",
                }
            ],
        }
    )

    expanded = plan.expand_image_frames()

    frame, caption = expanded.blocks
    assert isinstance(frame, TableBlock)
    assert frame.column_width_weights == (1.0,)
    assert frame.row_heights_mm == (72.0,)
    cell = frame.rows[0][0]
    assert cell.image_path == image_path
    assert cell.image_width_mm == 150
    assert cell.image_height_mm == 70
    assert cell.borders is not None
    assert cell.borders.left is not None
    assert cell.borders.right is not None
    assert cell.borders.top is not None
    assert cell.borders.bottom is not None
    assert isinstance(caption, ParagraphBlock)
    assert caption.text == "현황 및 적용 사례"
    assert caption.style_role == "figure_title"


def test_paragraph_image_remains_an_image_block() -> None:
    image = ImageBlock(
        kind="image",
        path=Path("source.png"),
        width_mm=100,
        height_mm=50,
    )
    plan = LayoutPlan(blocks=(image,))

    expanded = plan.expand_image_frames()

    assert expanded.blocks == (image,)


def test_table_cell_height_tracks_the_fitted_picture_not_the_requested_box(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "wide.png"
    with Image.new("RGB", (230, 100)) as image:
        image.save(image_path)
    plan = LayoutPlan(
        blocks=(
            ImageBlock(
                kind="image",
                path=image_path,
                width_mm=155,
                height_mm=79.583,
                container="table_cell",
            ),
        )
    )

    expanded = plan.expand_image_frames()

    frame = expanded.blocks[0]
    assert isinstance(frame, TableBlock)
    assert frame.row_heights_mm == pytest.approx((69.3913,), abs=0.001)


def test_figure_caption_rejects_manual_number_prefix_to_avoid_duplicate_auto_number() -> None:
    with pytest.raises(ValidationError, match="automatic figure numbering"):
        ImageBlock(
            kind="image",
            path=Path("source.png"),
            width_mm=100,
            height_mm=50,
            caption="(그림 5.5.3-38) 현황 및 적용 사례",
        )

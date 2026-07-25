from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_layout_preflight import preflight_layout  # noqa: E402
from hwp_live_contract import (  # noqa: E402
    ImageBlock,
    LayoutPlan,
    ParagraphBlock,
    TableBlock,
    TableCell,
)
from hwp_reference_layout_geometry import SectionPageGeometry  # noqa: E402


def _page() -> SectionPageGeometry:
    return SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=20,
        bottom_margin_mm=20,
        header_mm=0,
        footer_mm=0,
        gutter_mm=0,
        gutter_type=0,
    )


def test_preflight_rejects_definite_overflow_before_document_mutation(
    tmp_path: Path,
) -> None:
    image = tmp_path / "map.png"
    _ = image.write_bytes(b"placeholder")
    plan = LayoutPlan(
        blocks=(
            ParagraphBlock(
                kind="paragraph",
                text="제목",
                font_size_pt=16,
                space_after_mm=5,
            ),
            TableBlock(
                kind="table",
                rows=tuple((TableCell(text=str(index)),) for index in range(5)),
                row_heights_mm=(40, 40, 40, 40, 40),
            ),
            ImageBlock(
                kind="image",
                path=image,
                width_mm=120,
                height_mm=80,
            ),
        )
    )

    result = preflight_layout(plan, _page(), page_number=1)

    assert result.overflow == "definite"
    assert result.estimated_height_mm > result.usable_height_mm
    assert result.problem_blocks
    assert {item.block_kind for item in result.problem_blocks} >= {"image", "table"}
    assert result.adjustable_items


def test_preflight_marks_content_driven_table_height_as_possible_not_definite() -> None:
    plan = LayoutPlan(
        blocks=(
            TableBlock(
                kind="table",
                rows=(
                    (TableCell(text="가변 높이 본문 " * 20),),
                    (TableCell(text="두 번째 행"),),
                ),
                auto_fit_row_heights=True,
            ),
        )
    )

    result = preflight_layout(plan, _page(), page_number=1)

    assert result.overflow == "possible"
    assert "AUTO_FIT_TABLE_HEIGHT" in result.reason_codes
    assert "confirm_table_row_heights" in result.adjustable_items

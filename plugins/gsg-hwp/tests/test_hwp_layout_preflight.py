from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


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
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceStyle,
    TextAnchor,
)
from hwp_reference_layout_native import compile_reference_layout_command  # noqa: E402
from hwp_live_native_action_models import IntegerValue  # noqa: E402


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


def _reference_block(
    source: Path,
    *,
    rows: int,
    columns: int,
) -> ReferenceLayoutBlock:
    return ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=tuple(index / rows for index in range(rows + 1)),
        column_breakpoints=tuple(index / columns for index in range(columns + 1)),
    )


def _command_dimension_mm(
    block: ReferenceLayoutBlock,
    path: str,
) -> float:
    command = compile_reference_layout_command(
        block,
        _page(),
        page_number=1,
        base_style_id=0,
    )
    value = next(setter.value for setter in command.setters if setter.path == path)
    assert isinstance(value, IntegerValue)
    return value.value * 25.4 / 7_200


def test_reference_preflight_uses_the_execution_placement_frame_for_varied_aspects(
    tmp_path: Path,
) -> None:
    cases = (
        ("wide.png", (900, 300), 3, 5),
        ("classic.png", (800, 600), 4, 4),
        ("square.png", (600, 600), 6, 4),
    )

    for name, size, rows, columns in cases:
        source = tmp_path / name
        Image.new("RGB", size, "white").save(source)
        block = _reference_block(source, rows=rows, columns=columns)

        result = preflight_layout(
            LayoutPlan(blocks=(block,)),
            _page(),
            page_number=1,
        )

        assert result.estimated_width_mm == round(
            _command_dimension_mm(block, "BodyWidth"),
            3,
        )
        assert result.estimated_height_mm == round(
            _command_dimension_mm(block, "BodyHeight"),
            3,
        )


def test_reference_preflight_rejects_geometry_that_execution_cannot_fit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "extremely-wide.png"
    Image.new("RGB", (2_000, 5), "white").save(source)
    block = _reference_block(source, rows=1, columns=1).model_copy(
        update={
            "styles": (
                ReferenceStyle(
                    key="body",
                    font_size_pt=96,
                    line_spacing_percent=500,
                ),
            ),
            "text_anchors": (
                TextAnchor(
                    row=0,
                    column=0,
                    text="실행 높이를 초과하는 문단 " * 200,
                    style_key="body",
                ),
            ),
        }
    )

    result = preflight_layout(
        LayoutPlan(blocks=(block,)),
        _page(),
        page_number=1,
    )

    assert result.safe_to_write is False
    assert result.overflow == "definite"
    assert "REFERENCE_LAYOUT_EXECUTION_REJECTED" in result.reason_codes

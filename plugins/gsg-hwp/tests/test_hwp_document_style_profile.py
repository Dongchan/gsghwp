from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_document_style_profile import resolve_layout_style_profile  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_contract import (  # noqa: E402
    DocumentStyle,
    ImageBlock,
    LayoutPlan,
    ParagraphBlock,
)
from hwp_live_table_contract import TableBlock, TableCell  # noqa: E402


STYLES = (
    DocumentStyle(style_id=0, name="바탕글", english_name="Normal"),
    DocumentStyle(style_id=4, name="표타이틀"),
    DocumentStyle(style_id=8, name="표내용"),
    DocumentStyle(style_id=11, name="그림타이틀"),
    DocumentStyle(style_id=12, name="제1장"),
    DocumentStyle(style_id=13, name="1.1"),
    DocumentStyle(style_id=14, name="1.1.1"),
    DocumentStyle(style_id=15, name="가."),
    DocumentStyle(style_id=16, name="1)"),
    DocumentStyle(style_id=17, name="가)"),
    DocumentStyle(style_id=18, name="(1)"),
    DocumentStyle(style_id=19, name="DS-표 내용"),
    DocumentStyle(style_id=21, name="DS-표 제목"),
)


def test_layout_uses_existing_document_roles_and_content_width(tmp_path: Path) -> None:
    plan = LayoutPlan(
        target="document_end",
        blocks=(
            ParagraphBlock(kind="paragraph", text="다) 자연경관 분석"),
            ParagraphBlock(kind="paragraph", text="분석 결과를 정리하였다."),
            TableBlock(
                kind="table",
                caption="분석 결과",
                rows=((TableCell(text="항목"), TableCell(text="내용")),),
                column_width_weights=(1.0, 3.0),
            ),
            ImageBlock(
                kind="image",
                path=tmp_path / "figure.png",
                width_mm=80,
                height_mm=50,
                caption="현황 분석도",
            ),
        ),
    )

    resolved = resolve_layout_style_profile(
        plan,
        STYLES,
        fallback_style_id=99,
        content_width_mm=170.0,
    )

    heading, body, table, image = resolved.blocks
    assert isinstance(heading, ParagraphBlock) and heading.style_id == 17
    assert isinstance(body, ParagraphBlock) and body.style_id == 0
    assert isinstance(table, TableBlock)
    assert table.base_style_id == 8
    assert table.caption_style_id == 4
    assert table.column_widths_mm == (42.5, 127.5)
    assert isinstance(image, ImageBlock) and image.caption_style_id == 11


def test_explicit_style_names_win_over_automatic_roles(tmp_path: Path) -> None:
    plan = LayoutPlan(
        blocks=(
            ParagraphBlock(
                kind="paragraph",
                text="사용자 지정 문단",
                style_name="1.1.1",
                style_role="body",
            ),
            TableBlock(
                kind="table",
                caption="사용자 지정 표",
                base_style_name="DS-표 내용",
                caption_style_name="DS-표 제목",
                rows=((TableCell(text="값"),),),
            ),
            ImageBlock(
                kind="image",
                path=tmp_path / "figure.png",
                width_mm=80,
                height_mm=50,
                caption="사용자 지정 그림",
                caption_style_name="DS-표 제목",
            ),
        )
    )

    resolved = resolve_layout_style_profile(
        plan,
        STYLES,
        fallback_style_id=99,
        content_width_mm=170.0,
    )

    paragraph, table, image = resolved.blocks
    assert isinstance(paragraph, ParagraphBlock) and paragraph.style_id == 14
    assert isinstance(table, TableBlock)
    assert table.base_style_id == 19 and table.caption_style_id == 21
    assert isinstance(image, ImageBlock) and image.caption_style_id == 21


def test_symbolic_role_names_resolve_to_existing_document_styles(
    tmp_path: Path,
) -> None:
    plan = LayoutPlan(
        blocks=(
            TableBlock(
                kind="table",
                caption="역할 기반 표",
                base_style_name="table_body",
                caption_style_name="table_title",
                rows=((TableCell(text="값"),),),
            ),
            ImageBlock(
                kind="image",
                path=tmp_path / "figure.png",
                width_mm=80,
                height_mm=50,
                caption="역할 기반 그림",
                caption_style_name="figure_title",
            ),
        )
    )

    resolved = resolve_layout_style_profile(
        plan,
        STYLES,
        fallback_style_id=99,
        content_width_mm=170.0,
    )

    table, image = resolved.blocks
    assert isinstance(table, TableBlock)
    assert table.base_style_id == 8 and table.caption_style_id == 4
    assert isinstance(image, ImageBlock) and image.caption_style_id == 11


def test_wide_table_is_split_without_crushing_numeric_columns() -> None:
    headers = tuple(TableCell(text=value) for value in (
        "형",
        "세대수",
        "전용면적",
        "공용소계",
        "공급m²",
        "공급평",
        "기타공유",
        "계약m²",
        "계약평",
        "전용율",
        "비고비율",
    ))
    values = tuple(TableCell(text=value) for value in (
        "27A",
        "15",
        "38.402",
        "21.287",
        "59.689",
        "18.056",
        "29.725",
        "89.414",
        "27.048",
        "42.95%",
        "60.58%",
    ))
    plan = LayoutPlan(
        blocks=(
            TableBlock(
                kind="table",
                caption="용도별 면적표 — 오피스텔",
                rows=(headers, values),
                column_width_weights=tuple(1.0 for _ in headers),
                minimum_column_widths_mm=tuple(18.0 for _ in headers),
                row_heights_mm=(10.0, 7.5),
                split_wide_table=True,
                repeat_key_columns=1,
            ),
        )
    )

    resolved = resolve_layout_style_profile(
        plan,
        STYLES,
        fallback_style_id=99,
        content_width_mm=170.0,
    )

    tables = tuple(block for block in resolved.blocks if isinstance(block, TableBlock))
    assert len(tables) == 2
    assert tuple(table.rows[0][0].text for table in tables) == ("형", "형")
    assert tuple(table.rows[1][0].text for table in tables) == ("27A", "27A")
    assert tuple(table.caption for table in tables) == (
        "용도별 면적표 — 오피스텔 (1/2)",
        "용도별 면적표 — 오피스텔 (2/2)",
    )
    for table in tables:
        assert table.column_widths_mm is not None
        assert table.minimum_column_widths_mm is not None
        assert sum(table.column_widths_mm) == pytest.approx(170.0)
        assert all(
            width >= minimum
            for width, minimum in zip(
                table.column_widths_mm,
                table.minimum_column_widths_mm,
                strict=True,
            )
        )


def test_unsplittable_table_fails_instead_of_compressing_below_readable_width() -> None:
    plan = LayoutPlan(
        blocks=(
            TableBlock(
                kind="table",
                rows=(tuple(TableCell(text=f"열 {index}") for index in range(8)),),
                column_width_weights=tuple(1.0 for _ in range(8)),
                minimum_column_widths_mm=tuple(24.0 for _ in range(8)),
            ),
        )
    )

    with pytest.raises(HwpLiveError, match="가독성"):
        resolve_layout_style_profile(
            plan,
            STYLES,
            fallback_style_id=99,
            content_width_mm=170.0,
        )

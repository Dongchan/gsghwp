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

from hwp_live_contract import ImageBlock, ParagraphBlock  # noqa: E402
from hwp_live_table_contract import TableBlock  # noqa: E402
from hwp_report_layout import (  # noqa: E402
    ReportPlan,
    ReportSection,
    ReportTable,
    report_layout_plan,
)


def test_report_builder_turns_text_and_records_into_compact_native_layout() -> None:
    report = ReportPlan(
        title="사업 검토 보고",
        introduction=("요청받은 원문을 검토 항목별로 재구성하였다.",),
        sections=(
            ReportSection(
                title="핵심 검토사항",
                paragraphs=("수치와 서술을 분리하여 가독성을 높였다.",),
                bullets=("일정 준수", "비용 절감"),
                tables=(
                    ReportTable(
                        title="대안 비교",
                        headers=("대안", "사업비(억원)", "검토 의견"),
                        rows=(
                            ("A안", "125.4", "단기 시행에 유리"),
                            ("B안", "142.0", "장기 확장성이 우수"),
                        ),
                    ),
                ),
            ),
        ),
    )

    layout = report_layout_plan(report)

    paragraphs = tuple(
        block for block in layout.blocks if isinstance(block, ParagraphBlock)
    )
    tables = tuple(block for block in layout.blocks if isinstance(block, TableBlock))
    assert paragraphs[0].text == "사업 검토 보고"
    assert paragraphs[0].style_role == "heading"
    assert any(block.text == "○ 일정 준수" for block in paragraphs)
    assert len(tables) == 1
    table = tables[0]
    assert table.caption == "대안 비교"
    assert table.rows[0][0].bold is True
    assert table.rows[1][1].alignment == "right"
    assert table.column_width_weights is not None
    assert table.column_widths_mm is None


def test_report_builder_overrides_template_styles_with_readable_paragraph_metrics() -> (
    None
):
    layout = report_layout_plan(
        ReportPlan(
            title="변환 보고서",
            introduction=("보고서 도입 문단",),
            sections=(
                ReportSection(
                    title="1. 변환 결과",
                    paragraphs=("보고서 본문 문단",),
                    bullets=("보고서 글머리 문단",),
                ),
            ),
        )
    )

    paragraphs = tuple(
        block for block in layout.blocks if isinstance(block, ParagraphBlock)
    )
    title, introduction, section, body, bullet = paragraphs

    assert title.font_size_pt == 16
    assert title.bold is True
    assert section.font_size_pt == 13
    assert section.bold is True
    for paragraph in (introduction, body, bullet):
        assert paragraph.font_size_pt == 10
        assert paragraph.bold is False
    for paragraph in paragraphs:
        assert paragraph.font_name == "맑은 고딕"
        assert paragraph.text_color == (0, 0, 0)
        assert paragraph.alignment == "left"
        assert paragraph.line_spacing_percent is not None
        assert paragraph.line_spacing_percent >= 150
        assert paragraph.left_margin_mm == 0
        assert paragraph.right_margin_mm == 0
        assert paragraph.indentation_mm == 0


def test_report_builder_handles_public_policy_and_research_scenarios() -> None:
    for title, headers, row in (
        ("민원 처리 현황", ("구분", "접수", "완료"), ("도시환경", "18", "16")),
        ("실험 결과", ("시료", "농도(mg/L)", "판정"), ("S-01", "3.21", "적합")),
        ("프로젝트 위험", ("위험", "가능성", "대응"), ("일정 지연", "중", "주간 점검")),
    ):
        layout = report_layout_plan(
            ReportPlan(
                title=title,
                sections=(
                    ReportSection(
                        title="요약",
                        tables=(
                            ReportTable(title=title, headers=headers, rows=(row,)),
                        ),
                    ),
                ),
            )
        )
        table = next(block for block in layout.blocks if isinstance(block, TableBlock))
        assert len(table.rows) == 2
        assert len(table.rows[0]) == len(headers)
        assert table.column_width_weights is not None


def test_report_builder_sets_readable_width_and_height_floors_for_dense_tables() -> (
    None
):
    headers = (
        "층",
        "근린생활시설(m²)",
        "공공기여(m²)",
        "오피스텔(m²)",
        "공용/기타(m²)",
        "합계(m²)",
        "합계(평)",
        "비고",
    )
    layout = report_layout_plan(
        ReportPlan(
            sections=(
                ReportSection(
                    title="층별 면적",
                    tables=(
                        ReportTable(
                            title="층별 면적표",
                            headers=headers,
                            rows=(
                                (
                                    "B1",
                                    "479.039",
                                    "-",
                                    "-",
                                    "1,318.763",
                                    "1,797.802",
                                    "543.833",
                                    "근린생활시설·주차 19대",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )

    table = next(block for block in layout.blocks if isinstance(block, TableBlock))
    assert table.split_wide_table is True
    assert table.repeat_key_columns == 1
    assert table.minimum_column_widths_mm is not None
    assert min(table.minimum_column_widths_mm) >= 16.0
    assert sum(table.minimum_column_widths_mm) > 170.0
    assert table.row_heights_mm is not None
    assert table.row_heights_mm[0] >= 9.0
    assert table.row_heights_mm[1] >= 6.0


def test_report_builder_can_repeat_multiple_identifier_columns_when_split() -> None:
    layout = report_layout_plan(
        ReportPlan(
            sections=(
                ReportSection(
                    title="면적 검토",
                    tables=(
                        ReportTable(
                            headers=("용도", "형/층", "전용", "공용", "공급", "계약"),
                            rows=(
                                (
                                    "오피스텔",
                                    "27A",
                                    "38.402",
                                    "21.287",
                                    "59.689",
                                    "89.414",
                                ),
                            ),
                            repeat_key_columns=2,
                        ),
                    ),
                ),
            ),
        )
    )

    table = next(block for block in layout.blocks if isinstance(block, TableBlock))
    assert table.repeat_key_columns == 2


def test_report_builder_routes_figures_into_table_cells() -> None:
    report = ReportPlan.model_validate(
        {
            "sections": [
                {
                    "title": "설치 예시",
                    "figures": [
                        {
                            "path": "example.png",
                            "width_mm": 150,
                            "height_mm": 70,
                            "caption": "건축물 적용 사례",
                        }
                    ],
                }
            ]
        }
    )

    layout = report_layout_plan(report)

    image = next(block for block in layout.blocks if isinstance(block, ImageBlock))
    assert image.path == Path("example.png")
    assert image.container == "table_cell"
    assert image.caption == "건축물 적용 사례"

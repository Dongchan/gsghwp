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

from hwp_document_style_profile import resolve_layout_style_profile  # noqa: E402
from hwp_live_contract import DocumentStyle  # noqa: E402
from hwp_live_table_contract import TableBlock  # noqa: E402
from hwp_report_layout import (  # noqa: E402
    ReportPlan,
    ReportSection,
    ReportTable,
    report_layout_plan,
)


def test_report_row_height_uses_final_fitted_column_widths() -> None:
    description = (
        "도시환경 정비사업 추진 과정에서 기존 문서 양식과 수치 근거를 함께 "
        "검토하여 보고서 내용을 이해하기 쉽게 재구성하였다"
    )
    plan = report_layout_plan(
        ReportPlan(
            sections=(
                ReportSection(
                    title="검토",
                    tables=(
                        ReportTable(
                            headers=("ID", "검토 의견"),
                            rows=(("A-01", description),),
                        ),
                    ),
                ),
            ),
        )
    )

    resolved = resolve_layout_style_profile(
        plan,
        (DocumentStyle(style_id=1, name="본문"),),
        fallback_style_id=1,
        content_width_mm=170.0,
    )

    table = next(block for block in resolved.blocks if isinstance(block, TableBlock))
    assert table.column_widths_mm is not None
    assert table.column_widths_mm[1] > 100.0
    assert table.row_heights_mm == (9.0, 10.0)

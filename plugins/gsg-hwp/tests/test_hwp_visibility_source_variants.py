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

from hwp_live_structure_contract import (  # noqa: E402
    StructureCaption,
    StructureCell,
    StructureMerge,
    StructurePosition,
    StructureTable,
)
from hwp_visibility_source import extract_visibility_records  # noqa: E402
from hwp_visibility_template import build_visibility_series_plan  # noqa: E402


def _source_table(number_text: str) -> StructureTable:
    cells = (
        StructureCell(
            address="A1",
            row=0,
            column=0,
            owner_address="A1",
            column_span=2,
            text="구분",
        ),
        StructureCell(
            address="B1",
            row=0,
            column=1,
            owner_address="A1",
            text="",
        ),
        StructureCell(address="C1", row=0, column=2, owner_address="C1", text="조망위치"),
        StructureCell(address="D1", row=0, column=3, owner_address="D1", text="이격거리"),
        StructureCell(address="E1", row=0, column=4, owner_address="E1", text="표고(m)"),
        StructureCell(address="F1", row=0, column=5, owner_address="F1", text="조망방향"),
        StructureCell(address="A2", row=1, column=0, owner_address="A2", text="근경"),
        StructureCell(address="B2", row=1, column=1, owner_address="B2", text=number_text),
        StructureCell(address="C2", row=1, column=2, owner_address="C2", text="북측 교차로"),
        StructureCell(address="D2", row=1, column=3, owner_address="D2", text="3m"),
        StructureCell(address="E2", row=1, column=4, owner_address="E2", text="EL.+39.3m"),
        StructureCell(address="F2", row=1, column=5, owner_address="F2", text="대상지 북동측"),
    )
    return StructureTable(
        table_ref="table-ref-visibility-source",
        control_instance_id="visibility-source",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=9,
        page_end=9,
        rows=2,
        columns=6,
        merges=(
            StructureMerge(
                owner_address="A1",
                row=0,
                column=0,
                row_span=1,
                column_span=2,
            ),
        ),
        cells=cells,
    )


def _template_table(
    caption: StructureCaption | None = None,
) -> StructureTable:
    cells = (
        StructureCell(address="A1", row=0, column=0, owner_address="A1", column_span=2, text="구분"),
        StructureCell(address="C1", row=0, column=2, owner_address="C1", column_span=2, text="조망위치"),
        StructureCell(address="E1", row=0, column=4, owner_address="E1", text="이격거리"),
        StructureCell(address="F1", row=0, column=5, owner_address="F1", text="표고"),
        StructureCell(address="A2", row=1, column=0, owner_address="A2", text="근경"),
        StructureCell(address="B2", row=1, column=1, owner_address="B2", text="예비조망점 ①"),
        StructureCell(address="C2", row=1, column=2, owner_address="C2", column_span=2, text="기존 위치"),
        StructureCell(address="E2", row=1, column=4, owner_address="E2", text="1m"),
        StructureCell(address="F2", row=1, column=5, owner_address="F2", text="EL.+1m"),
        StructureCell(address="A3", row=2, column=0, owner_address="A3", column_span=3, text="가시권분석"),
        StructureCell(address="D3", row=2, column=3, owner_address="D3", column_span=3, text="현황사진"),
        StructureCell(address="A4", row=3, column=0, owner_address="A4", column_span=3, text=""),
        StructureCell(address="D4", row=3, column=3, owner_address="D4", column_span=3, text=""),
        StructureCell(address="A5", row=4, column=0, owner_address="A5", column_span=2, text="분석결과"),
        StructureCell(address="C5", row=4, column=2, owner_address="C5", column_span=4, text="기존 분석"),
    )
    return StructureTable(
        table_ref="table-ref-visibility-template",
        control_instance_id="visibility-template",
        anchor=StructurePosition(list_id=0, paragraph=1, character=0),
        page_start=10,
        page_end=10,
        rows=5,
        columns=6,
        merges=(),
        cells=cells,
        caption=caption,
    )


@pytest.mark.parametrize("number_text", ("조망점01", "예비조망점 01"))
def test_extracts_prefixed_viewpoint_number(number_text: str) -> None:
    records = extract_visibility_records(_source_table(number_text))

    assert tuple(record.number for record in records) == (1,)


def test_series_plan_derives_missing_caption_from_template(tmp_path: Path) -> None:
    visibility = tmp_path / "visibility"
    current = tmp_path / "current"
    visibility.mkdir()
    current.mkdir()
    _ = (visibility / "1.jpg").write_bytes(b"visibility")
    _ = (current / "1_사업전.jpg").write_bytes(b"current")

    plan = build_visibility_series_plan(
        _source_table("01"),
        _template_table(),
        visibility,
        current,
    )

    assert plan.caption_title is None


def test_series_plan_does_not_copy_automatic_caption_label_as_literal_text(
    tmp_path: Path,
) -> None:
    visibility = tmp_path / "visibility"
    current = tmp_path / "current"
    visibility.mkdir()
    current.mkdir()
    _ = (visibility / "1.jpg").write_bytes(b"visibility")
    _ = (current / "1_사업전.jpg").write_bytes(b"current")

    plan = build_visibility_series_plan(
        _source_table("01"),
        _template_table(
            StructureCaption(
                text="표  가시권분석 -1",
                automatic_number=True,
                style_id=0,
                style_name="바탕글",
            )
        ),
        visibility,
        current,
    )

    assert plan.caption_title == "가시권분석 -1"

from __future__ import annotations

import sys
import inspect
from dataclasses import replace
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_office_excel_com import (  # noqa: E402
    ExcelBorderData,
    ExcelCellData,
    ExcelMergeData,
    ExcelRangeData,
    _Borders as _ExcelBorders,
    _Font as _ExcelFont,
    _Interior as _ExcelInterior,
)
from hwp_office_excel_com import _cell as _read_cell  # noqa: E402
from hwp_office_excel_com import _clip_merge  # noqa: E402
from hwp_office_excel_layout import _cell as _layout_cell  # noqa: E402
from hwp_office_excel_layout import _excel_column_minimums  # noqa: E402
from hwp_office_excel_layout import _fit_column_widths  # noqa: E402
from hwp_office_excel_layout import trim_blank_edges  # noqa: E402
from hwp_public_document_tools import HwpPublicDocumentTools  # noqa: E402


class _FakeBorder:
    LineStyle = -4142
    Weight = 2
    Color = 0


class _FakeBorders:
    def __call__(self, edge: int) -> _FakeBorder:
        _ = edge
        return _FakeBorder()


class _FakeInterior:
    Color = 0xFFFFFF
    Pattern = -4142


class _FakeFont:
    Bold = False
    Size: float | None = None
    Color = 0
    Name = "서울남산체 B"


class _FakeCell:
    Text: str | int | float | bool | None = "용도별 면적표"
    Interior: _ExcelInterior = _FakeInterior()
    Font: _ExcelFont = _FakeFont()
    HorizontalAlignment = -4108
    VerticalAlignment = -4108
    WrapText = True
    Borders: _ExcelBorders = _FakeBorders()


def test_nullable_excel_font_size_falls_back_to_hwp_base_style() -> None:
    source = _read_cell(_FakeCell(), covered=False)

    assert source.font_size is None
    assert _layout_cell(source, preserve_font=True).font_size_pt is None


def test_narrow_excel_columns_are_fitted_without_changing_total_width() -> None:
    widths = _fit_column_widths((2.0, 20.0, 20.0), target_width_mm=30.0)

    assert widths == (5.0, 12.5, 12.5)
    assert sum(widths) == 30.0


def test_merge_is_clipped_to_requested_excel_range() -> None:
    assert _clip_merge(
        top=38,
        left=0,
        bottom=38,
        right=22,
        first_row=38,
        first_column=0,
        last_row=60,
        last_column=18,
    ) == (38, 0, 38, 18)


def _excel_value(text: str) -> ExcelCellData:
    border = ExcelBorderData(line_style=-4142, weight=2, color=0)
    return ExcelCellData(
        text=text,
        covered=False,
        fill_color=0xFFFFFF,
        fill_pattern=-4142,
        bold=False,
        font_size=11.0,
        font_color=0x123456,
        font_name="맑은 고딕",
        horizontal_alignment=-4131,
        vertical_alignment=-4108,
        wrap_text=True,
        borders=(border, border, border, border),
    )


def test_excel_import_removes_only_unused_outer_rows_and_columns() -> None:
    blank = _excel_value("")
    source = ExcelRangeData(
        rows=(
            (blank, blank, blank, blank),
            (blank, _excel_value("항목"), _excel_value("값"), blank),
            (blank, _excel_value("대지면적"), _excel_value("1,250.00"), blank),
            (blank, blank, blank, blank),
        ),
        column_width_points=(5.0, 30.0, 50.0, 5.0),
        row_height_points=(12.0, 18.0, 18.0, 12.0),
        merges=(),
    )

    trimmed = trim_blank_edges(source)

    assert tuple(tuple(cell.text for cell in row) for row in trimmed.rows) == (
        ("항목", "값"),
        ("대지면적", "1,250.00"),
    )
    assert trimmed.column_width_points == (30.0, 50.0)
    assert trimmed.row_height_points == (18.0, 18.0)


def test_disabling_excel_font_preservation_uses_document_table_style() -> None:
    cell = _layout_cell(_excel_value("내용"), preserve_font=False)

    assert cell.font_name is None
    assert cell.font_size_pt is None
    assert cell.text_color is None


def test_excel_public_tool_preserves_source_row_heights_by_default() -> None:
    parameter = inspect.signature(
        HwpPublicDocumentTools.hwp_append_excel_table
    ).parameters["preserve_excel_row_heights"]

    assert parameter.default is True


def test_excel_column_fitting_honors_readable_minimums() -> None:
    widths = _fit_column_widths(
        (10.0, 20.0, 20.0, 20.0),
        target_width_mm=100.0,
        minimum_widths_mm=(16.0, 22.0, 22.0, 22.0),
    )

    assert sum(widths) == 100.0
    assert all(
        width >= minimum
        for width, minimum in zip(widths, (16.0, 22.0, 22.0, 22.0), strict=True)
    )


def test_merged_excel_heading_uses_combined_width_instead_of_each_column() -> None:
    source = ExcelRangeData(
        rows=((
            _excel_value("용도별 면적표 (오피스텔·근린생활시설·공공기여)"),
            replace(_excel_value(""), covered=True),
        ),),
        column_width_points=(50.0, 50.0),
        row_height_points=(20.0,),
        merges=(ExcelMergeData(row=0, column=0, row_span=1, column_span=2),),
    )

    minimums = _excel_column_minimums(source)

    assert minimums == (24.0, 24.0)

from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_office_excel_com import _range as _xls_range  # noqa: E402
from hwp_office_xlsx import read_xlsx  # noqa: E402


_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

_WORKBOOK = (
    f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}">'
    '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
    "</workbook>"
)
_RELS = (
    f'<Relationships xmlns="{_PKG_REL}">'
    '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
    "</Relationships>"
)


def _sheet() -> str:
    rows = []
    for row in (1, 2, 3):
        cells = "".join(
            f'<c r="{column}{row}" t="inlineStr"><is><t>{column}{row}</t></is></c>'
            for column in ("A", "B", "C")
        )
        rows.append(f'<row r="{row}">{cells}</row>')
    return f'<worksheet xmlns="{_MAIN}"><sheetData>{"".join(rows)}</sheetData></worksheet>'


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    path = tmp_path / "book.xlsx"
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _RELS)
        archive.writestr("xl/worksheets/sheet1.xml", _sheet())
    return path


def _read(path: Path, cell_range: str | None) -> tuple[tuple[str, ...], ...]:
    return read_xlsx(path, sheet_name=None, sheet_index=0, cell_range=cell_range)


def test_xlsx_accepts_absolute_reference_like_xls(workbook: Path) -> None:
    # hwp_office_excel_com._range strips "$" for the .xls path, so the same
    # public cell_range string must not depend on the workbook format.
    assert _xls_range("$A$1:$B$2") == (0, 0, 1, 1)

    assert _read(workbook, "$A$1:$B$2") == _read(workbook, "A1:B2")


def test_xlsx_accepts_mixed_absolute_reference(workbook: Path) -> None:
    assert _read(workbook, "A$1:$B2") == _read(workbook, "A1:B2")


def test_xlsx_accepts_single_absolute_cell(workbook: Path) -> None:
    assert _read(workbook, "$B$2") == (("B2",),)


# --- safety: normalising "$" must not widen anything else --------------------


def test_xlsx_still_rejects_sheet_qualified_range(workbook: Path) -> None:
    with pytest.raises(HwpLiveError):
        _ = _read(workbook, "Sheet1!$A$1:$B$2")


def test_xlsx_still_rejects_reversed_range(workbook: Path) -> None:
    with pytest.raises(HwpLiveError):
        _ = _read(workbook, "$B$2:$A$1")


def test_xlsx_still_rejects_garbage_range(workbook: Path) -> None:
    with pytest.raises(HwpLiveError):
        _ = _read(workbook, "$A$:$B$2")


def test_xlsx_still_rejects_three_part_range(workbook: Path) -> None:
    with pytest.raises(HwpLiveError):
        _ = _read(workbook, "A1:B2:C3")


def test_xlsx_plain_ranges_are_unchanged(workbook: Path) -> None:
    assert _read(workbook, "A1:C3") == (
        ("A1", "B1", "C1"),
        ("A2", "B2", "C2"),
        ("A3", "B3", "C3"),
    )
    assert _read(workbook, None) == (
        ("A1", "B1", "C1"),
        ("A2", "B2", "C2"),
        ("A3", "B3", "C3"),
    )

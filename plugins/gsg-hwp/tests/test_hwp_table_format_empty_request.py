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

from hwp_live_native_format_inputs import (  # noqa: E402
    InputFailure,
    TableFormatSpec,
    parse_table_format,
)
from hwp_public_table_edit_contract import PublicTableFormattingInput  # noqa: E402


def _parsed(**fields: object) -> TableFormatSpec | InputFailure:
    requested = PublicTableFormattingInput.model_validate(fields)
    return parse_table_format(requested.to_parameters())


def test_empty_table_format_request_is_refused() -> None:
    # `alignment` and `vertical_alignment` default to "inherit". Emitting them
    # unconditionally made every request look like it carried formatting, so a
    # request with nothing to apply ran SelectControl/Capture with no setter and
    # still reported every cell as updated and verified.
    parsed = _parsed()

    assert isinstance(parsed, InputFailure)
    assert parsed.status == "needs_input"


def test_cell_only_table_format_request_is_refused() -> None:
    parsed = _parsed(cell="A1")

    assert isinstance(parsed, InputFailure)
    assert parsed.status == "needs_input"


def test_explicit_inherit_is_still_not_formatting() -> None:
    parsed = _parsed(alignment="inherit", vertical_alignment="inherit")

    assert isinstance(parsed, InputFailure)
    assert parsed.status == "needs_input"


# --- safety: every real formatting request must still be accepted ------------


def test_alignment_only_request_is_accepted() -> None:
    parsed = _parsed(alignment="center")

    assert isinstance(parsed, TableFormatSpec)
    assert parsed.formatting.alignment == "center"
    assert parsed.formatting.vertical_alignment == "inherit"


def test_vertical_alignment_only_request_is_accepted() -> None:
    parsed = _parsed(vertical_alignment="top")

    assert isinstance(parsed, TableFormatSpec)
    assert parsed.formatting.vertical_alignment == "top"
    assert parsed.formatting.alignment == "inherit"


def test_fill_only_request_keeps_neutral_alignment_defaults() -> None:
    parsed = _parsed(fill_color="#DDEEFF")

    assert isinstance(parsed, TableFormatSpec)
    assert parsed.formatting.fill_color is not None
    assert parsed.formatting.alignment == "inherit"
    assert parsed.formatting.vertical_alignment == "inherit"


def test_other_single_field_requests_are_accepted() -> None:
    for field, value in (
        ("bold", True),
        ("font_name", "함초롬돋움"),
        ("font_size_pt", 11.0),
        ("text_color", "#112233"),
        ("line_spacing", 160),
        ("row_height_mm", 12.0),
        ("column_width_mm", 30.0),
        ("border_width", "0.5mm"),
    ):
        parsed = _parsed(**{field: value})
        assert isinstance(parsed, TableFormatSpec), field

from __future__ import annotations

import sys
from math import isclose
from pathlib import Path

import pytest
from pydantic import ValidationError


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_table_contract import (  # noqa: E402
    BORDER_WIDTH_SNAP_TARGETS,
    snap_pdf_linewidth,
)
from hwp_reference_layout_contract import ReferenceStyle  # noqa: E402
from pdf_vector_contract import pt_to_hwpunit  # noqa: E402


@pytest.mark.parametrize(
    ("points", "expected_hwpunit"),
    (
        (0.25, 25),
        (0.3, 30),
        (0.5, 50),
        (0.75, 75),
        (1.5, 150),
        (2.0, 200),
        (2.25, 225),
        (3.0, 300),
        (1.005, 101),
    ),
)
def test_pdf_points_convert_directly_to_integer_hwpunit(
    points: float,
    expected_hwpunit: int,
) -> None:
    assert pt_to_hwpunit(points) == expected_hwpunit


def test_border_width_snap_uses_all_literals_and_records_signed_residual() -> None:
    assert len(BORDER_WIDTH_SNAP_TARGETS) == 16
    assert tuple(target.width for target in BORDER_WIDTH_SNAP_TARGETS) == (
        "0.1mm",
        "0.12mm",
        "0.15mm",
        "0.2mm",
        "0.25mm",
        "0.3mm",
        "0.4mm",
        "0.5mm",
        "0.6mm",
        "0.7mm",
        "1.0mm",
        "1.5mm",
        "2.0mm",
        "3.0mm",
        "4.0mm",
        "5.0mm",
    )

    snapped = snap_pdf_linewidth(1.5)

    assert snapped.source_hwpunit == 150
    assert snapped.width == "0.5mm"
    assert isclose(snapped.source_mm, 0.5291666667, abs_tol=1e-10)
    assert isclose(snapped.residual_mm, 0.0291666667, abs_tol=1e-10)
    assert isclose(snapped.absolute_error_mm, 0.0291666667, abs_tol=1e-10)


@pytest.mark.parametrize(
    ("points", "expected_width", "expected_residual_mm"),
    (
        (0.25, "0.1mm", -0.0118055556),
        (0.3, "0.1mm", 0.0058333333),
        (0.5, "0.2mm", -0.0236111111),
        (0.75, "0.25mm", 0.0145833333),
        (1.5, "0.5mm", 0.0291666667),
        (2.0, "0.7mm", 0.0055555556),
        (2.25, "0.7mm", 0.09375),
        (3.0, "1.0mm", 0.0583333333),
    ),
)
def test_sample_linewidths_snap_with_reported_error(
    points: float,
    expected_width: str,
    expected_residual_mm: float,
) -> None:
    snapped = snap_pdf_linewidth(points)
    payload = snapped.payload()

    assert snapped.width == expected_width
    assert isclose(snapped.residual_mm, expected_residual_mm, abs_tol=1e-10)
    assert payload["source_linewidth_hwpunit"] == snapped.source_hwpunit
    assert payload["border_width"] == expected_width
    assert payload["border_width_snap_error_mm"] == snapped.residual_mm
    assert payload["border_width_snap_abs_error_mm"] == snapped.absolute_error_mm


def test_non_integral_point_conversion_records_quantization_error() -> None:
    snapped = snap_pdf_linewidth(1.005)

    assert snapped.source_hwpunit == 101
    assert isclose(snapped.hwpunit_quantization_error_pt, 0.005, abs_tol=1e-12)
    assert snapped.payload()["hwpunit_quantization_error_pt"] == 0.005


def test_reference_style_keeps_mm_fields_and_accepts_optional_hwpunit_fields() -> None:
    legacy = ReferenceStyle(
        key="legacy",
        paragraph_before_mm=0.5,
        paragraph_after_mm=0.25,
    )
    direct = ReferenceStyle(
        key="direct",
        paragraph_before_hwpunit=142,
        paragraph_after_hwpunit=71,
    )

    assert legacy.paragraph_before_mm == 0.5
    assert legacy.paragraph_after_mm == 0.25
    assert legacy.paragraph_before_hwpunit is None
    assert legacy.paragraph_after_hwpunit is None
    assert direct.paragraph_before_mm == 0
    assert direct.paragraph_after_mm == 0
    assert direct.paragraph_before_hwpunit == 142
    assert direct.paragraph_after_hwpunit == 71
    legacy_payload = legacy.model_dump(exclude_none=True)
    assert "paragraph_before_hwpunit" not in legacy_payload
    assert "paragraph_after_hwpunit" not in legacy_payload

    with pytest.raises(ValidationError):
        _ = ReferenceStyle(key="too-large", paragraph_before_hwpunit=28_347)

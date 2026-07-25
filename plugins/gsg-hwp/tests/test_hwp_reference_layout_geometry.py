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

from hwp_reference_layout_geometry import (  # noqa: E402
    SectionPageGeometry,
    map_normalized_breakpoints,
    mm_to_hwpunit,
)


def test_body_area_uses_orientation_margins_header_footer_and_gutter() -> None:
    page = SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=True,
        left_margin_mm=20,
        right_margin_mm=15,
        top_margin_mm=12,
        bottom_margin_mm=10,
        header_mm=18,
        footer_mm=14,
        gutter_mm=5,
        gutter_type=2,
    )

    area = page.usable_area(page_number=1)

    assert area.width == page.paper_height - page.left_margin - page.right_margin
    assert area.height == (
        page.paper_width
        - page.top_margin
        - page.header
        - page.bottom_margin
        - page.footer
        - page.gutter
    )
    assert area.left == mm_to_hwpunit(20)
    assert area.top == page.top_margin + page.header + page.gutter


def test_portrait_body_area_reserves_margins_and_header_footer_additively() -> None:
    page = SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=10.5,
        header_mm=18.5,
        footer_mm=18.5,
        gutter_mm=0,
        gutter_type=0,
    )

    area = page.usable_area(page_number=29)

    assert area.left == mm_to_hwpunit(20)
    assert area.top == mm_to_hwpunit(15) + mm_to_hwpunit(18.5)
    assert area.width == page.paper_width - page.left_margin - page.right_margin
    assert area.height == (
        page.paper_height
        - mm_to_hwpunit(15)
        - mm_to_hwpunit(18.5)
        - mm_to_hwpunit(10.5)
        - mm_to_hwpunit(18.5)
    )


def test_facing_page_gutter_changes_origin_without_changing_width() -> None:
    page = SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=15,
        header_mm=10,
        footer_mm=10,
        gutter_mm=6,
        gutter_type=1,
    )

    odd = page.usable_area(page_number=1)
    even = page.usable_area(page_number=2)

    expected_width = (
        page.paper_width - page.left_margin - page.right_margin - page.gutter
    )
    assert odd.width == even.width == expected_width
    assert odd.left == mm_to_hwpunit(26)
    assert even.left == mm_to_hwpunit(20)


def test_breakpoint_rounding_pins_both_ends_and_distributes_internal_error() -> None:
    mapped = map_normalized_breakpoints(
        (0.0, 0.111, 0.333, 0.667, 0.889, 1.0),
        origin=123,
        extent=47_113,
    )

    assert mapped.boundaries[0] == 123
    assert mapped.boundaries[-1] == 47_236
    assert sum(mapped.sizes) == 47_113
    ideals = [
        47_113 * (right - left)
        for left, right in zip(
            (0.0, 0.111, 0.333, 0.667, 0.889),
            (0.111, 0.333, 0.667, 0.889, 1.0),
            strict=True,
        )
    ]
    assert max(abs(actual - ideal) for actual, ideal in zip(mapped.sizes, ideals, strict=True)) < 1


def test_breakpoint_mapping_respects_hwp_minimum_without_moving_ends() -> None:
    mapped = map_normalized_breakpoints(
        (0.0, 0.001, 0.25, 0.251, 1.0),
        origin=77,
        extent=10_000,
    )

    assert mapped.boundaries[0] == 77
    assert mapped.boundaries[-1] == 10_077
    assert sum(mapped.sizes) == 10_000
    assert min(mapped.sizes) == 100

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

from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
)
from hwp_reference_layout_evidence import prepare_reference_layout  # noqa: E402
from hwp_reference_layout_text_fitting import (  # noqa: E402
    fit_reference_text_styles,
)
from hwp_reference_layout_text_fit_metrics import (  # noqa: E402
    wrapped_lines_in_cell,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> ReferenceLayoutBlock:
    return ReferenceLayoutBlock.model_validate_json(
        (FIXTURES / f"reference_layout_{name}.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.parametrize(
    ("name", "minimum_inserted_rows"),
    (
        ("vision_goals", 5),
        ("three_theme_flow", 3),
        ("carbon_org_chart", 8),
    ),
)
def test_source_box_gutters_create_protected_spacer_rows(
    name: str,
    minimum_inserted_rows: int,
) -> None:
    block = _fixture(name)

    prepared = prepare_reference_layout(block)

    protected_row_count = len(
        {
            (gap.top, gap.bottom)
            for gap in prepared.protected_gaps
            if gap.axis == "row"
        }
    )
    assert protected_row_count >= minimum_inserted_rows
    styles = {style.key: style for style in prepared.styles}
    for gap in prepared.protected_gaps:
        if gap.axis != "row":
            continue
        region = next(
            item
            for item in prepared.style_regions
            if (
                item.top == gap.top
                and item.left == gap.left
                and item.bottom == gap.bottom
                and item.right == gap.right
            )
        )
        style = styles[region.style_key]
        assert style.fill_color is None
        assert style.font_size_pt == 1
        assert style.padding is not None
        assert style.padding.top_mm == style.padding.bottom_mm == 0


def test_image_snapping_does_not_narrow_an_occupied_number_column() -> None:
    block = _fixture("vision_goals")

    prepared = prepare_reference_layout(block)

    proposed = block.column_breakpoints[2] - block.column_breakpoints[1]
    measured = prepared.column_breakpoints[2] - prepared.column_breakpoints[1]
    assert measured >= proposed * 0.9


def test_colored_strategy_columns_snap_to_equal_source_box_widths() -> None:
    block = _fixture("vision_goals")

    prepared = prepare_reference_layout(block)

    widths = []
    for style_key in ("blue_header", "green_header", "orange_header"):
        region = next(
            item
            for item in prepared.style_regions
            if item.style_key == style_key
        )
        widths.append(
            prepared.column_breakpoints[region.right]
            - prepared.column_breakpoints[region.left]
        )
    assert min(widths) / max(widths) >= 0.9


def test_local_box_gap_is_preserved_without_splitting_a_neighboring_tall_box() -> None:
    block = _fixture("vision_goals")

    prepared = prepare_reference_layout(block)

    anchors = {item.text: item for item in prepared.text_anchors}
    green_top = anchors["소하천 환경정화 기능 강화"]
    green_bottom = anchors["하천변 커뮤니티 녹지거점공간 확보"]
    orange_top = anchors["생애주기를 고려한\n라이프사이클 주거단지 조성"]
    orange_bottom = anchors["사회초년생 특화 공공임대주택 공급"]
    blue = anchors[
        "생활·여가·행정 거점을 연결하는\n중심거리 가로환경 개선"
    ]
    local_gaps = tuple(
        gap
        for gap in prepared.protected_gaps
        if gap.axis == "row"
        and green_top.row < gap.top < green_bottom.row
    )

    assert local_gaps
    assert any(
        gap.left <= green_top.column < gap.right
        for gap in local_gaps
    )
    assert any(
        gap.left <= orange_top.column < gap.right
        for gap in local_gaps
    )
    assert all(not (gap.left <= blue.column < gap.right) for gap in local_gaps)
    assert green_top.row < green_bottom.row
    assert orange_top.row < orange_bottom.row


def test_filled_label_width_snaps_to_its_source_box() -> None:
    block = _fixture("vision_goals")

    prepared = prepare_reference_layout(block)

    proposed = block.column_breakpoints[3] - block.column_breakpoints[1]
    anchor = next(
        item for item in prepared.text_anchors if item.text == "전략 및 과제"
    )
    merge = next(
        item
        for item in prepared.merges
        if (item.row, item.column) == (anchor.row, anchor.column)
    )
    measured = (
        prepared.column_breakpoints[merge.column + merge.column_span]
        - prepared.column_breakpoints[merge.column]
    )
    assert measured >= proposed * 1.5


def test_filled_banner_height_snaps_to_its_source_box() -> None:
    block = _fixture("namwon_strategy")

    prepared = prepare_reference_layout(block)

    original = block.row_breakpoints[3] - block.row_breakpoints[2]
    anchor = next(
        item
        for item in prepared.text_anchors
        if item.style_key == "banner"
    )
    merge = next(
        item
        for item in prepared.merges
        if (item.row, item.column) == (anchor.row, anchor.column)
    )
    measured = (
        prepared.row_breakpoints[merge.row + merge.row_span]
        - prepared.row_breakpoints[merge.row]
    )
    assert measured >= original * 0.8


def test_implicit_source_line_is_not_automatically_wrapped() -> None:
    style = ReferenceStyle(
        key="tag",
        font_name="맑은 고딕",
        font_size_pt=12,
        fill_color=(31, 52, 111),
        line_spacing_percent=100,
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(style,),
        style_regions=(
            StyleRegion(
                top=0,
                left=0,
                bottom=1,
                right=1,
                style_key="tag",
            ),
        ),
        text_anchors=(
            TextAnchor(
                row=0,
                column=0,
                text="전략 및 과제",
                style_key="tag",
            ),
        ),
    )

    fitted = fit_reference_text_styles(
        block,
        row_heights=(5_000,),
        column_widths=(3_051,),
    )

    anchor = fitted.text_anchors[0]
    fitted_style = {item.key: item for item in fitted.styles}[anchor.style_key or ""]
    assert fitted_style.font_size_pt is not None
    assert fitted_style.font_size_pt >= 9
    assert fitted_style.width_ratio_percent < 100
    assert (
        wrapped_lines_in_cell(
            anchor,
            fitted_style,
            None,
            available_width=3_051,
        )
        == 1
    )


def test_flow_arrow_rows_are_not_collapsed_by_image_snapping() -> None:
    block = _fixture("three_theme_flow")
    prepared = prepare_reference_layout(block)
    source_arrows = [
        anchor for anchor in block.text_anchors if anchor.style_key == "arrow"
    ]
    prepared_arrows = [
        anchor for anchor in prepared.text_anchors if anchor.style_key == "arrow"
    ]

    assert len(prepared_arrows) == len(source_arrows)
    for source, snapped in zip(source_arrows, prepared_arrows, strict=True):
        source_height = (
            block.row_breakpoints[source.row + 1]
            - block.row_breakpoints[source.row]
        )
        snapped_height = (
            prepared.row_breakpoints[snapped.row + 1]
            - prepared.row_breakpoints[snapped.row]
        )
        assert snapped_height >= source_height

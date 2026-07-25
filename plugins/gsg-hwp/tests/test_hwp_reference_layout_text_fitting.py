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

from hwp_live_table_contract import CellPadding  # noqa: E402
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
)
from hwp_reference_layout_text_fitting import (  # noqa: E402
    fit_reference_text_styles,
)
from hwp_reference_layout_text_fit_metrics import (  # noqa: E402
    wrapped_lines_in_cell,
)


def _style_map(
    block: ReferenceLayoutBlock,
) -> dict[str, ReferenceStyle]:
    return {style.key: style for style in block.styles}


def test_fitting_preserves_geometry_and_explicit_line_count() -> None:
    style = ReferenceStyle(
        key="body",
        font_name="맑은 고딕",
        font_size_pt=10,
        line_spacing_percent=100,
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.2, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(style,),
        style_regions=(
            StyleRegion(
                top=0,
                left=0,
                bottom=2,
                right=1,
                style_key="body",
            ),
        ),
        text_anchors=(
            TextAnchor(
                row=0,
                column=0,
                text="가나다라\n마바사아",
                style_key="body",
            ),
        ),
    )

    fitted = fit_reference_text_styles(
        block,
        row_heights=(2_000, 8_000),
        column_widths=(2_500,),
    )

    anchor = fitted.text_anchors[0]
    assert anchor.text == block.text_anchors[0].text
    assert anchor.style_key != "body"
    fitted_style = _style_map(fitted)[anchor.style_key or ""]
    assert fitted_style.letter_spacing_percent < 0
    assert fitted_style.font_size_pt is not None
    assert fitted_style.font_size_pt < 10


def test_fitting_reduces_padding_before_typography() -> None:
    padding = CellPadding(
        left_mm=0,
        right_mm=0,
        top_mm=1,
        bottom_mm=1,
    )
    style = ReferenceStyle(
        key="body",
        font_name="맑은 고딕",
        font_size_pt=10,
        line_spacing_percent=100,
        padding=padding,
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
                style_key="body",
            ),
        ),
        text_anchors=(
            TextAnchor(
                row=0,
                column=0,
                text="한 줄",
                style_key="body",
            ),
        ),
    )

    fitted = fit_reference_text_styles(
        block,
        row_heights=(1_500,),
        column_widths=(10_000,),
    )

    anchor = fitted.text_anchors[0]
    fitted_style = _style_map(fitted)[anchor.style_key or ""]
    assert fitted_style.font_size_pt == 10
    assert fitted_style.line_spacing_percent == 100
    assert fitted_style.padding is not None
    assert fitted_style.padding.top_mm < 1
    assert fitted_style.padding.bottom_mm < 1
    assert fitted.style_regions[-1].style_key == anchor.style_key


def test_fitting_preserves_implicit_single_line_in_tall_cell() -> None:
    style = ReferenceStyle(
        key="body",
        font_name="맑은 고딕",
        font_size_pt=15,
        line_spacing_percent=100,
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(style,),
        text_anchors=(
            TextAnchor(
                row=0,
                column=0,
                text="미래 도시변화에 능동적 대처 가능한 도시공간구조 정립",
                style_key="body",
            ),
        ),
    )

    fitted = fit_reference_text_styles(
        block,
        row_heights=(5_000,),
        column_widths=(39_423,),
    )

    anchor = fitted.text_anchors[0]
    fitted_style = _style_map(fitted)[anchor.style_key or ""]
    assert wrapped_lines_in_cell(
        anchor,
        fitted_style,
        None,
        available_width=39_423,
    ) == 1
    assert fitted_style.font_size_pt == 15
    assert fitted_style.letter_spacing_percent == -5
    assert fitted_style.width_ratio_percent == 96

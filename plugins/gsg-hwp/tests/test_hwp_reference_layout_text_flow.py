from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_table_contract import CellPadding  # noqa: E402
from hwp_live_native_action_models import NativeArrayValue  # noqa: E402
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
)
from hwp_reference_layout_geometry import SectionPageGeometry, mm_to_hwpunit  # noqa: E402
from hwp_reference_layout_native import (  # noqa: E402
    compile_reference_layout_command,
)
from hwp_reference_layout_patch import (  # noqa: E402
    ReferenceLayoutPatchBlock,
    compile_reference_layout_patch_command,
)
from hwp_reference_layout_payload import compile_reference_layout_payload  # noqa: E402
from hwp_reference_layout_text_flow import (  # noqa: E402
    ReferenceTextHeightError,
    reference_line_height,
)


def _integers(
    items: Sequence[NativeArrayValue],
    name: str,
) -> tuple[int, ...]:
    values: list[tuple[int, int]] = []
    for item in items:
        if item.name != name:
            continue
        value = item.value.value
        assert type(value) is int
        values.append((item.index, value))
    return tuple(value for _, value in sorted(values))


def _small_page() -> SectionPageGeometry:
    return SectionPageGeometry(
        paper_width=5_000,
        paper_height=5_000,
        landscape=False,
        left_margin=0,
        right_margin=0,
        top_margin=0,
        bottom_margin=0,
        header=0,
        footer=0,
        gutter=0,
        gutter_type=0,
    )


def test_text_anchor_uses_line_break_unless_paragraph_break_is_explicit() -> None:
    default = TextAnchor(row=0, column=0, text="첫째\n둘째")
    paragraph = TextAnchor(
        row=0,
        column=0,
        text="첫째\n둘째",
        break_mode="paragraph",
    )

    assert default.break_mode == "line"
    assert paragraph.break_mode == "paragraph"


def test_percent_line_spacing_cannot_shrink_below_glyph_height() -> None:
    style = ReferenceStyle(
        key="body",
        font_size_pt=16,
        line_spacing_percent=50,
    )

    assert reference_line_height(style) == 1_600


def test_text_metrics_and_break_mode_are_explicit_in_native_payload() -> None:
    style = ReferenceStyle(
        key="body",
        font_name="맑은 고딕",
        font_size_pt=9,
        width_ratio_percent=93,
        letter_spacing_percent=-2,
        line_spacing_type="fixed",
        line_spacing_hwpunit=1_200,
        paragraph_before_mm=0.1,
        paragraph_after_mm=0.2,
        padding=CellPadding(
            left_mm=0.3,
            right_mm=0.4,
            top_mm=0.5,
            bottom_mm=0.6,
        ),
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(style,),
        style_regions=(
            StyleRegion(top=0, left=0, bottom=1, right=1, style_key="body"),
        ),
        text_anchors=(
            TextAnchor(
                row=0,
                column=0,
                text="첫째\n둘째",
                style_key="body",
                break_mode="paragraph",
            ),
        ),
    )

    payload = compile_reference_layout_payload(block)

    assert _integers(payload.values, "StyleWidthRatios") == (93,)
    assert _integers(payload.values, "StyleLetterSpacings") == (-2,)
    assert _integers(payload.values, "StyleLineSpacingTypes") == (1,)
    assert _integers(payload.values, "StyleLineSpacings") == (1_200,)
    assert _integers(payload.values, "StylePreviousSpacings") == (
        mm_to_hwpunit(0.1),
    )
    assert _integers(payload.values, "StyleNextSpacings") == (
        mm_to_hwpunit(0.2),
    )
    assert _integers(payload.values, "StylePaddingTop") == (
        mm_to_hwpunit(0.5),
    )
    assert _integers(payload.values, "StylePaddingBottom") == (
        mm_to_hwpunit(0.6),
    )
    assert _integers(payload.values, "TextBreakModes") == (1,)


def test_impossible_two_line_row_is_raised_before_table_creation() -> None:
    zero_padding = CellPadding(
        left_mm=0,
        right_mm=0,
        top_mm=0,
        bottom_mm=0,
    )
    style = ReferenceStyle(
        key="body",
        font_name="맑은 고딕",
        font_size_pt=9,
        line_spacing_percent=100,
        padding=zero_padding,
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.01, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(style,),
        style_regions=(
            StyleRegion(top=0, left=0, bottom=2, right=1, style_key="body"),
        ),
        text_anchors=(
            TextAnchor(row=0, column=0, text="첫째 줄\n둘째 줄", style_key="body"),
            TextAnchor(row=1, column=0, text="다음 행", style_key="body"),
        ),
    )

    with pytest.raises(ReferenceTextHeightError) as raised:
        _ = compile_reference_layout_command(
            block,
            _small_page(),
            page_number=1,
            base_style_id=0,
        )

    assert raised.value.available == 100
    assert raised.value.required == 1_350


def test_text_fitting_does_not_hide_bad_ir_with_extreme_font_shrink() -> None:
    style = ReferenceStyle(
        key="body",
        font_name="맑은 고딕",
        font_size_pt=14,
        line_spacing_percent=100,
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.05, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(style,),
        text_anchors=(
            TextAnchor(
                row=0,
                column=0,
                text="원본 첫째 줄\n원본 둘째 줄",
                style_key="body",
            ),
        ),
    )

    with pytest.raises(ReferenceTextHeightError) as raised:
        _ = compile_reference_layout_command(
            block,
            _small_page(),
            page_number=1,
            base_style_id=0,
        )

    assert raised.value.available == 250
    assert raised.value.required == 2_100


def test_single_visual_symbol_keeps_source_size_in_a_narrow_marker_row() -> None:
    style = ReferenceStyle(
        key="arrow",
        font_name="맑은 고딕",
        font_size_pt=16,
        line_spacing_percent=100,
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.05, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(style,),
        text_anchors=(
            TextAnchor(row=0, column=0, text="▼", style_key="arrow"),
        ),
    )

    command = compile_reference_layout_command(
        block,
        _small_page(),
        page_number=1,
        base_style_id=0,
    )

    assert _integers(command.array_values, "StyleFontSizes") == (1_600,)
    assert _integers(command.array_values, "RowHeights") == (1_600, 3_400)

    patch = ReferenceLayoutPatchBlock(
        kind="reference_layout_patch",
        target_control_id="marker-table",
        row_breakpoints=block.row_breakpoints,
        column_breakpoints=block.column_breakpoints,
        changed_rows=(0,),
        styles=block.styles,
        text_anchors=block.text_anchors,
    )
    patch_command = compile_reference_layout_patch_command(
        patch,
        _small_page(),
        page_number=1,
    )

    assert _integers(patch_command.array_values, "RowHeights") == (1_600, 3_400)

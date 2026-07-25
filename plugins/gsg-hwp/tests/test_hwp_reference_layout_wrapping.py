from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_action_models import NativeArrayValue  # noqa: E402
from hwp_live_table_contract import CellPadding  # noqa: E402
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceMerge,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
)
from hwp_reference_layout_geometry import SectionPageGeometry  # noqa: E402
from hwp_reference_layout_native import (  # noqa: E402
    compile_reference_layout_command,
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


def _page(width: int) -> SectionPageGeometry:
    return SectionPageGeometry(
        paper_width=width,
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


def test_automatic_wrap_uses_merged_cell_width_before_table_creation() -> None:
    style = ReferenceStyle(
        key="body",
        font_name="맑은 고딕",
        font_size_pt=10,
        line_spacing_percent=100,
        padding=CellPadding(
            left_mm=0,
            right_mm=0,
            top_mm=0,
            bottom_mm=0,
        ),
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.3, 1.0),
        column_breakpoints=(0.0, 0.5, 1.0),
        merges=(ReferenceMerge(row=0, column=0, column_span=2),),
        styles=(style,),
        style_regions=(
            StyleRegion(top=0, left=0, bottom=2, right=2, style_key="body"),
        ),
        text_anchors=(
            TextAnchor(row=0, column=0, text="가나다", style_key="body"),
        ),
    )

    command = compile_reference_layout_command(
        block,
        _page(2_000),
        page_number=1,
        base_style_id=0,
    )

    assert _integers(command.array_values, "RowHeights") == (1_500, 3_500)
    assert min(_integers(command.array_values, "StyleLetterSpacings")) < 0


def test_automatic_wrap_preserves_hwp_word_boundaries() -> None:
    style = ReferenceStyle(
        key="tag",
        font_name="맑은 고딕",
        font_size_pt=10,
        line_spacing_percent=100,
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.482, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(style,),
        style_regions=(
            StyleRegion(top=0, left=0, bottom=2, right=1, style_key="tag"),
        ),
        text_anchors=(
            TextAnchor(row=0, column=0, text="전략 및 과제", style_key="tag"),
        ),
    )

    command = compile_reference_layout_command(
        block,
        _page(3_051),
        page_number=1,
        base_style_id=0,
    )

    assert _integers(command.array_values, "RowHeights") == (2_410, 2_590)

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

from hwp_document_style_profile import resolve_layout_style_profile  # noqa: E402
from hwp_live_contract import DocumentStyle, LayoutPlan  # noqa: E402
from hwp_live_native_action_models import CellCommand  # noqa: E402
from hwp_live_native_table_layout import table_commands  # noqa: E402
from hwp_live_table_contract import (  # noqa: E402
    CellBorder,
    CellBorders,
    CellPadding,
    TableBlock,
    TableCell,
)


def test_visual_grid_clears_blank_edges_and_keeps_only_declared_connector() -> None:
    connector = CellBorder(style="solid", width="0.3mm", color=(128, 128, 128))
    table = TableBlock(
        kind="table",
        border_mode="explicit",
        rows=(
            (
                TableCell(),
                TableCell(borders=CellBorders(right=connector)),
                TableCell(),
            ),
            (TableCell(), TableCell(), TableCell()),
        ),
    )

    resolved = resolve_layout_style_profile(
        LayoutPlan(blocks=(table,)),
        (DocumentStyle(style_id=0, name="바탕글"),),
        fallback_style_id=0,
        content_width_mm=170.0,
    )

    styled = resolved.blocks[0]
    assert isinstance(styled, TableBlock)
    for row in styled.rows:
        for cell in row:
            assert cell.borders is not None
            assert all(
                edge is not None
                for edge in (
                    cell.borders.left,
                    cell.borders.right,
                    cell.borders.top,
                    cell.borders.bottom,
                )
            )
    assert styled.rows[0][1].borders is not None
    assert styled.rows[0][1].borders.right == connector
    assert styled.rows[0][2].borders is not None
    assert styled.rows[0][2].borders.left == connector
    assert styled.rows[0][0].borders is not None
    assert styled.rows[0][0].borders.right is not None
    assert styled.rows[0][0].borders.right.style == "none"
    assert styled.rows[1][1].borders is not None
    assert styled.rows[1][1].borders.top is not None
    assert styled.rows[1][1].borders.top.style == "none"


def test_visual_grid_accepts_one_mm_subdivisions_past_column_z() -> None:
    cell = TableCell(
        font_size_pt=1,
        line_spacing_percent=50,
        padding=CellPadding(left_mm=0, right_mm=0, top_mm=0, bottom_mm=0),
    )
    table = TableBlock(
        kind="table",
        border_mode="explicit",
        base_style_id=0,
        rows=(tuple(cell for _ in range(32)),),
        column_widths_mm=tuple(1.0 for _ in range(32)),
        row_heights_mm=(1.0,),
    )

    commands = table_commands(table, {}, {}, {}, {})

    assert CellCommand("Z1") in commands
    assert CellCommand("AA1") in commands
    assert CellCommand("AF1") in commands


def test_ordinary_table_keeps_border_inheritance_by_default() -> None:
    table = TableBlock(kind="table", rows=((TableCell(),),))

    resolved = resolve_layout_style_profile(
        LayoutPlan(blocks=(table,)),
        (DocumentStyle(style_id=0, name="바탕글"),),
        fallback_style_id=0,
        content_width_mm=170.0,
    )

    styled = resolved.blocks[0]
    assert isinstance(styled, TableBlock)
    assert styled.border_mode == "inherit"
    assert styled.rows[0][0].borders is None

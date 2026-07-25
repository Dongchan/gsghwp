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
    ProtectedGap,
    ReferenceLayoutBlock,
    ReferenceMerge,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
    VisibleEdge,
)


def _column_gap() -> ProtectedGap:
    return ProtectedGap(
        axis="column",
        top=0,
        left=1,
        bottom=2,
        right=2,
    )


@pytest.mark.parametrize(
    "conflict",
    (
        {
            "merges": (
                ReferenceMerge(row=0, column=0, column_span=3),
            )
        },
        {
            "styles": (
                ReferenceStyle(
                    key="filled",
                    fill_color=(220, 220, 220),
                ),
            ),
            "style_regions": (
                StyleRegion(
                    top=0,
                    left=0,
                    bottom=2,
                    right=3,
                    style_key="filled",
                ),
            ),
        },
        {
            "visible_edges": (
                VisibleEdge(
                    orientation="horizontal",
                    line=1,
                    start=0,
                    end=3,
                ),
            )
        },
        {"text_anchors": (TextAnchor(row=0, column=1, text="침범"),)},
    ),
)
def test_protected_gap_rejects_merge_fill_edge_and_text(
    conflict: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="protected gap"):
        ReferenceLayoutBlock(
            kind="reference_layout",
            row_breakpoints=(0.0, 0.5, 1.0),
            column_breakpoints=(0.0, 0.4, 0.6, 1.0),
            protected_gaps=(_column_gap(),),
            **conflict,
        )

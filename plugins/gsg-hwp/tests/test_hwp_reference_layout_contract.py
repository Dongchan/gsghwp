from __future__ import annotations

import sys
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

from hwp_reference_layout_contract import (  # noqa: E402
    OcrWordEvidence,
    ReferenceLayoutBlock,
    ReferenceMerge,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
    VisibleEdge,
)


def _layout() -> ReferenceLayoutBlock:
    return ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 0.2, 0.7, 1.0),
        column_breakpoints=(0.0, 0.25, 0.6, 1.0),
        merges=(ReferenceMerge(row=0, column=0, row_span=1, column_span=3),),
        visible_edges=(
            VisibleEdge(
                orientation="horizontal",
                line=0,
                start=0,
                end=3,
                width="0.3mm",
                color=(80, 80, 80),
            ),
            VisibleEdge(
                orientation="vertical",
                line=1,
                start=1,
                end=3,
                style="dot",
            ),
        ),
        styles=(
            ReferenceStyle(
                key="header",
                font_name="맑은 고딕",
                font_size_pt=11,
                bold=True,
                fill_color=(250, 225, 216),
            ),
        ),
        style_regions=(
            StyleRegion(top=0, left=0, bottom=1, right=3, style_key="header"),
        ),
        text_anchors=(
            TextAnchor(row=0, column=0, text="건 축 개 요", style_key="header"),
        ),
    )


def test_reference_layout_payload_is_component_compressed() -> None:
    layout = _layout()

    payload = layout.model_dump(mode="json", exclude_none=True)

    assert "rows" not in payload
    assert "cells" not in payload
    assert len(payload["row_breakpoints"]) == 4
    assert len(payload["column_breakpoints"]) == 4
    assert len(payload["merges"]) == 1
    assert len(payload["visible_edges"]) == 2
    assert len(payload["style_regions"]) == 1
    assert len(payload["text_anchors"]) == 1


def test_reference_layout_rejects_edges_inside_a_merged_cell() -> None:
    with pytest.raises(ValidationError, match="visible edge crosses a merge"):
        ReferenceLayoutBlock(
            kind="reference_layout",
            row_breakpoints=(0.0, 0.5, 1.0),
            column_breakpoints=(0.0, 0.5, 1.0),
            merges=(ReferenceMerge(row=0, column=0, row_span=2, column_span=2),),
            visible_edges=(
                VisibleEdge(
                    orientation="vertical",
                    line=1,
                    start=0,
                    end=2,
                ),
            ),
        )


def test_ocr_evidence_is_optional_and_does_not_replace_model_text_by_default() -> None:
    layout = _layout().model_copy(
        update={
            "ocr_words": (
                OcrWordEvidence(
                    text="건축개요",
                    confidence=0.99,
                    left=0.1,
                    top=0.02,
                    right=0.3,
                    bottom=0.05,
                ),
            ),
        }
    )

    assert layout.ocr_mode == "off"
    assert layout.text_anchors[0].text == "건 축 개 요"

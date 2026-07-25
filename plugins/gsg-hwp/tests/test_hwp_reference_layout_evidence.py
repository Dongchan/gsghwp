from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw


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
    TextAnchor,
)
from hwp_reference_layout_evidence import prepare_reference_layout  # noqa: E402


def _line_image(path: Path) -> None:
    image = Image.new("L", (101, 101), 255)
    draw = ImageDraw.Draw(image)
    draw.line((47, 0, 47, 100), fill=0, width=2)
    draw.line((0, 54, 100, 54), fill=0, width=2)
    image.save(path)


def test_image_evidence_snaps_only_internal_breakpoints(tmp_path: Path) -> None:
    source = tmp_path / "grid.png"
    _line_image(source)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 0.5, 1.0),
    )

    prepared = prepare_reference_layout(block)

    assert prepared.row_breakpoints[0] == 0.0
    assert prepared.row_breakpoints[-1] == 1.0
    assert prepared.column_breakpoints[0] == 0.0
    assert prepared.column_breakpoints[-1] == 1.0
    assert prepared.row_breakpoints[1] == pytest.approx(0.54, abs=0.015)
    assert prepared.column_breakpoints[1] == pytest.approx(0.47, abs=0.015)


def test_image_evidence_does_not_snap_to_fragmented_text_band(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fragmented-text.png"
    image = Image.new("L", (201, 201), 255)
    draw = ImageDraw.Draw(image)
    for left in range(10, 190, 20):
        draw.rectangle((left, 106, left + 8, 110), fill=0)
    image.save(source)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 1.0),
    )

    prepared = prepare_reference_layout(block)

    assert prepared.row_breakpoints == (0.0, 0.5, 1.0)


def test_optional_ocr_only_fills_model_anchor_gaps(tmp_path: Path) -> None:
    source = tmp_path / "blank.png"
    Image.new("L", (100, 100), 255).save(source)
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        row_breakpoints=(0.0, 0.5, 1.0),
        column_breakpoints=(0.0, 0.5, 1.0),
        text_anchors=(TextAnchor(row=0, column=0, text="모델 우선"),),
        ocr_mode="auto",
        ocr_words=(
            OcrWordEvidence(
                text="덮어쓰지 않음",
                confidence=0.99,
                left=0.05,
                top=0.05,
                right=0.25,
                bottom=0.08,
            ),
            OcrWordEvidence(
                text="소형한글",
                confidence=0.95,
                left=0.6,
                top=0.1,
                right=0.78,
                bottom=0.125,
            ),
            OcrWordEvidence(
                text="2026",
                confidence=0.96,
                left=0.1,
                top=0.7,
                right=0.3,
                bottom=0.78,
            ),
            OcrWordEvidence(
                text="ROTATED",
                confidence=0.93,
                left=0.6,
                top=0.65,
                right=0.75,
                bottom=0.75,
                rotation_degrees=90,
            ),
            OcrWordEvidence(
                text="ordinary latin",
                confidence=0.99,
                left=0.6,
                top=0.3,
                right=0.85,
                bottom=0.4,
            ),
            OcrWordEvidence(
                text="낮은신뢰도",
                confidence=0.4,
                left=0.6,
                top=0.2,
                right=0.8,
                bottom=0.23,
            ),
        ),
    )

    prepared = prepare_reference_layout(block)
    anchors = {(anchor.row, anchor.column): anchor.text for anchor in prepared.text_anchors}

    assert anchors[(0, 0)] == "모델 우선"
    assert anchors[(0, 1)] == "소형한글"
    assert anchors[(1, 0)] == "2026"
    assert anchors[(1, 1)] == "ROTATED"
    assert "ordinary latin" not in anchors.values()
    assert "낮은신뢰도" not in anchors.values()


def test_ocr_evidence_is_ignored_when_mode_is_off() -> None:
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        ocr_mode="off",
        ocr_words=(
            OcrWordEvidence(
                text="123",
                confidence=0.99,
                left=0.1,
                top=0.1,
                right=0.2,
                bottom=0.2,
            ),
        ),
    )

    assert prepare_reference_layout(block).text_anchors == ()

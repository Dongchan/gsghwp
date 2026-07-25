from __future__ import annotations

import sys
from pathlib import Path

import anyio
from PIL import Image, ImageDraw
from pydantic import JsonValue
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_reference_image_analyzer import (  # noqa: E402
    analyze_reference_image,
    load_cached_reference_image_analysis,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_forward import HwpExecuteArguments  # noqa: E402
from hwp_reference_image_contract import (  # noqa: E402
    ReferenceBreakpointCandidate,
    ReferenceImageAnalysis,
)
from hwp_reference_image_layout_bridge import (  # noqa: E402
    align_reference_layout_to_analysis,
)
from hwp_reference_image_summary import (  # noqa: E402
    CompactReferenceImageAnalysis,
    ReferenceAnalysisDetail,
)
from hwp_reference_image_pixels import PixelCanvas  # noqa: E402
from hwp_reference_image_segments import detect_visible_segments  # noqa: E402
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    TextAnchor,
)
from hwp_reference_layout_evidence import prepare_reference_layout  # noqa: E402


def _near(value: float, expected: float, tolerance: float = 0.01) -> bool:
    return abs(value - expected) <= tolerance


def test_analysis_alignment_preserves_occupied_narrow_rows_and_columns(
    tmp_path: Path,
) -> None:
    source = tmp_path / "narrow-marker.png"
    analysis = ReferenceImageAnalysis(
        analysis_id="ria-0123456789abcdef",
        analyzer_version="test",
        image_hash="0" * 64,
        source_image=source,
        image_width=100,
        image_height=100,
        analysis_time_ms=0,
        cache_hit=False,
        objects=(),
        text_regions=(),
        protected_gaps=(),
        visible_segments=(),
        breakpoint_candidates=(
            ReferenceBreakpointCandidate(
                axis="row",
                position=0.21,
                source="segment",
                source_id="row-start",
            ),
            ReferenceBreakpointCandidate(
                axis="row",
                position=0.235,
                source="segment",
                source_id="row-end",
            ),
            ReferenceBreakpointCandidate(
                axis="column",
                position=0.03,
                source="segment",
                source_id="column-end",
            ),
        ),
        tiles=(),
        contact_sheet_paths=(),
        overlay_path=tmp_path / "overlay.png",
    )
    proposal = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        analysis_id=analysis.analysis_id,
        row_breakpoints=(0.0, 0.2, 0.24, 0.6, 1.0),
        column_breakpoints=(0.0, 0.05, 0.5, 1.0),
        text_anchors=(TextAnchor(row=1, column=0, text="▼"),),
    )

    aligned = align_reference_layout_to_analysis(proposal, analysis)

    assert aligned.row_breakpoints[1:3] == proposal.row_breakpoints[1:3]
    assert aligned.column_breakpoints[:2] == proposal.column_breakpoints[:2]


def test_image_only_analysis_preserves_gap_and_global_tile_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    source = tmp_path / "two-column-cards.png"
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 280, 150), fill=(224, 242, 250), outline=(20, 80, 130))
    draw.rectangle((360, 40, 600, 150), fill=(250, 236, 226), outline=(160, 70, 20))
    draw.text((80, 85), "LEFT CARD", fill=(20, 20, 20))
    draw.text((400, 85), "RIGHT CARD", fill=(20, 20, 20))
    draw.line((280, 240, 360, 240), fill=(90, 90, 90), width=1)
    image.save(source)

    result = analyze_reference_image(source)

    assert result.cache_hit is False
    assert result.image_width == 640
    assert result.image_height == 360
    assert any(
        candidate.axis == "column" and _near(candidate.position, 280 / 640)
        for candidate in result.breakpoint_candidates
    )
    assert any(
        candidate.axis == "column" and _near(candidate.position, 360 / 640)
        for candidate in result.breakpoint_candidates
    )
    assert any(
        gap.orientation == "vertical"
        and gap.bbox.left <= 280 / 640
        and gap.bbox.right >= 360 / 640
        for gap in result.protected_gaps
    )
    assert any(
        segment.orientation == "horizontal"
        and _near(segment.start_y, 40 / 360)
        and segment.start_x <= 40 / 640
        and segment.end_x >= 280 / 640
        for segment in result.visible_segments
    )
    assert result.text_regions
    assert len(result.tiles) > 1
    assert any(
        left.bbox.right > right.bbox.left and left.bbox.left < right.bbox.left
        for left in result.tiles
        for right in result.tiles
    )
    assert result.overlay_path.is_file()
    assert result.contact_sheet_paths
    assert all(path.is_file() for path in result.contact_sheet_paths)

    cached = analyze_reference_image(source)

    assert cached.analysis_id == result.analysis_id
    assert cached.cache_hit is True
    assert (
        load_cached_reference_image_analysis(
            source,
            result.analysis_id,
        ).cache_hit
        is True
    )

    proposal = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=source,
        analysis_id=result.analysis_id,
        row_breakpoints=(0.0, 0.11, 0.42, 1.0),
        column_breakpoints=(0.0, 0.44, 0.56, 1.0),
    )
    aligned = align_reference_layout_to_analysis(proposal, result)
    prepared = prepare_reference_layout(proposal)

    assert _near(aligned.column_breakpoints[1], 280 / 640)
    assert _near(aligned.column_breakpoints[2], 360 / 640)
    assert any(gap.axis == "column" for gap in aligned.protected_gaps)
    assert any(gap.axis == "column" for gap in prepared.protected_gaps)

    mismatched = proposal.model_copy(update={"analysis_id": "ria-0000000000000000"})
    with pytest.raises(ValueError, match="does not match"):
        _ = align_reference_layout_to_analysis(mismatched, result)


def test_edge_drawing_stitches_a_slightly_skewed_box_edge() -> None:
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    color = (30, 80, 130)
    draw.line((40, 40, 520, 40), fill=color, width=1)
    draw.line((40, 240, 524, 240), fill=color, width=1)
    draw.line((40, 40, 40, 240), fill=color, width=1)
    draw.line((520, 40, 524, 240), fill=color, width=1)

    segments = detect_visible_segments(PixelCanvas.from_image(image))
    right_edges = tuple(
        segment
        for segment in segments
        if segment.orientation == "vertical"
        and segment.position >= 518
        and segment.start <= 42
        and segment.end >= 238
    )

    assert len(right_edges) == 1


def test_registered_image_analyzer_is_forwardable_without_hwp_session(
    tmp_path: Path,
) -> None:
    source = tmp_path / "forwarded.png"
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 140, 100), fill=(220, 240, 250), outline="navy")
    draw.rectangle((180, 30, 290, 100), fill=(250, 235, 220), outline="maroon")
    draw.text((55, 55), "LEFT", fill="black")
    draw.text((205, 55), "RIGHT", fill="black")
    image.save(source)
    artifact_root = tmp_path / "mcp-analysis"
    arguments: dict[str, JsonValue] = {
        "image_path": str(source),
        "artifact_root": str(artifact_root),
    }
    forwarded_arguments: dict[str, JsonValue] = {
        "tool_name": "hwp_analyze_reference_image",
        "arguments": arguments,
    }
    server = build_server(LiveHwpController(), profile="production")

    async def call_tools() -> tuple[
        CompactReferenceImageAnalysis,
        CompactReferenceImageAnalysis,
        ReferenceAnalysisDetail,
    ]:
        direct = await server.call_unconverted_tool(
            "hwp_analyze_reference_image",
            HwpExecuteArguments(arguments),
        )
        direct_result = CompactReferenceImageAnalysis.model_validate(direct)
        forwarded = await server.call_unconverted_tool(
            "hwp_execute",
            HwpExecuteArguments(forwarded_arguments),
        )
        detail = await server.call_unconverted_tool(
            "hwp_get_reference_image_analysis_section",
            HwpExecuteArguments(
                {
                    "analysis_id": direct_result.analysis_id,
                    "section": "visible_segments",
                    "artifact_root": str(artifact_root),
                    "limit": 1,
                }
            ),
        )
        return (
            direct_result,
            CompactReferenceImageAnalysis.model_validate(forwarded),
            ReferenceAnalysisDetail.model_validate(detail),
        )

    direct, forwarded, detail = anyio.run(call_tools)

    assert direct.analysis_id == forwarded.analysis_id
    assert direct.cache_hit is False
    assert forwarded.cache_hit is True
    assert len(direct.model_dump_json().encode("utf-8")) < 100_000
    assert direct.recommended_execution_mode in {
        "ordinary_layout",
        "reference_layout_bulk",
    }
    assert detail.analysis_id == direct.analysis_id
    assert detail.section == "visible_segments"
    assert len(detail.items) <= 1

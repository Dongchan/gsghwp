from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

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
from hwp_reference_image_cache import (  # noqa: E402
    ReferenceImageResourceLimits,
    acquire_reference_image_lock,
    prune_reference_image_cache,
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
    compact_reference_image_analysis,
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


def test_varied_grid_line_widths_route_to_reference_layout_bulk(
    tmp_path: Path,
) -> None:
    cases = (
        ("thin.png", (640, 420), 3, 3, 2, 48),
        ("medium.png", (900, 600), 4, 5, 6, 72),
        ("thick.png", (720, 720), 6, 4, 12, 14),
    )
    artifact_root = tmp_path / "analysis"

    for name, size, rows, columns, stroke, margin in cases:
        source = tmp_path / name
        image = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(image)
        left = top = margin
        right = size[0] - margin - 1
        bottom = size[1] - margin - 1
        for column in range(columns + 1):
            x = round(left + (right - left) * column / columns)
            draw.line((x, top, x, bottom), fill=(28, 46, 62), width=stroke)
        for row in range(rows + 1):
            y = round(top + (bottom - top) * row / rows)
            draw.line((left, y, right, y), fill=(28, 46, 62), width=stroke)
        image.save(source)

        compact = compact_reference_image_analysis(
            analyze_reference_image(source, artifact_root=artifact_root)
        )

        assert compact.recommended_execution_mode == "reference_layout_bulk", (
            name,
            compact.structure,
            compact.reason_codes,
        )
        assert compact.structure.horizontal_segments >= rows + 1
        assert compact.structure.vertical_segments >= columns + 1


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


def test_large_reference_image_is_bounded_and_resource_profile_changes_cache_key(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large.png"
    Image.new("RGB", (1200, 900), "white").save(source)
    compact_limits = ReferenceImageResourceLimits(
        max_source_bytes=16 * 1024 * 1024,
        max_source_pixels=2_000_000,
        max_analysis_pixels=120_000,
        max_analysis_artifact_bytes=32 * 1024 * 1024,
        max_cache_bytes=64 * 1024 * 1024,
        max_cache_entries=8,
        ttl_seconds=3600,
        lock_timeout_seconds=5,
        stale_lock_seconds=60,
    )
    larger_limits = ReferenceImageResourceLimits(
        max_source_bytes=16 * 1024 * 1024,
        max_source_pixels=2_000_000,
        max_analysis_pixels=240_000,
        max_analysis_artifact_bytes=32 * 1024 * 1024,
        max_cache_bytes=64 * 1024 * 1024,
        max_cache_entries=8,
        ttl_seconds=3600,
        lock_timeout_seconds=5,
        stale_lock_seconds=60,
    )

    compact = analyze_reference_image(
        source,
        artifact_root=tmp_path / "artifacts",
        limits=compact_limits,
    )
    larger = analyze_reference_image(
        source,
        artifact_root=tmp_path / "artifacts",
        limits=larger_limits,
    )

    assert compact.image_width == 1200
    assert compact.image_height == 900
    assert compact.analysis_width * compact.analysis_height <= 120_000
    assert compact.analysis_downsampled is True
    assert compact.analysis_id != larger.analysis_id


def test_source_pixel_limit_rejects_before_pixel_canvas_allocation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "too-many-pixels.png"
    Image.new("RGB", (101, 101), "white").save(source)
    limits = ReferenceImageResourceLimits(
        max_source_bytes=1024 * 1024,
        max_source_pixels=10_000,
        max_analysis_pixels=10_000,
        max_analysis_artifact_bytes=1024 * 1024,
        max_cache_bytes=4 * 1024 * 1024,
        max_cache_entries=4,
        ttl_seconds=3600,
        lock_timeout_seconds=5,
        stale_lock_seconds=60,
    )

    with (
        patch.object(
            PixelCanvas,
            "from_image",
            side_effect=AssertionError("pixel canvas must not be allocated"),
        ),
        pytest.raises(ValueError, match="pixel limit"),
    ):
        _ = analyze_reference_image(
            source,
            artifact_root=tmp_path / "artifacts",
            limits=limits,
        )


def test_incomplete_cached_analysis_is_rebuilt_instead_of_reported_as_hit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incomplete.png"
    Image.new("RGB", (160, 120), "white").save(source)
    artifact_root = tmp_path / "artifacts"
    first = analyze_reference_image(source, artifact_root=artifact_root)
    first.overlay_path.unlink()

    rebuilt = analyze_reference_image(source, artifact_root=artifact_root)

    assert rebuilt.analysis_id == first.analysis_id
    assert rebuilt.cache_hit is False
    assert rebuilt.overlay_path.is_file()


def test_concurrent_same_image_analysis_publishes_one_complete_cache_entry(
    tmp_path: Path,
) -> None:
    source = tmp_path / "concurrent.png"
    image = Image.new("RGB", (320, 180), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 300, 160), outline="black")
    image.save(source)
    artifact_root = tmp_path / "artifacts"

    with ThreadPoolExecutor(max_workers=2) as calls:
        results = tuple(
            call.result(timeout=10)
            for call in (
                calls.submit(
                    analyze_reference_image,
                    source,
                    artifact_root=artifact_root,
                ),
                calls.submit(
                    analyze_reference_image,
                    source,
                    artifact_root=artifact_root,
                ),
            )
        )

    assert {result.cache_hit for result in results} == {False, True}
    assert results[0].analysis_id == results[1].analysis_id
    result_path = artifact_root / results[0].analysis_id / "result.json"
    assert (
        ReferenceImageAnalysis.model_validate_json(
            result_path.read_text(encoding="utf-8")
        ).analysis_id
        == results[0].analysis_id
    )


def test_separate_processes_share_one_atomic_reference_image_cache_entry(
    tmp_path: Path,
) -> None:
    source = tmp_path / "process-concurrent.png"
    image = Image.new("RGB", (240, 140), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 220, 120), outline="black")
    image.save(source)
    artifact_root = tmp_path / "artifacts"
    script = (
        "from pathlib import Path;"
        "from hwp_reference_image_analyzer import analyze_reference_image;"
        "import sys;"
        "result=analyze_reference_image("
        "Path(sys.argv[1]),artifact_root=Path(sys.argv[2]));"
        "print(str(result.cache_hit).lower())"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SCRIPTS)

    def invoke() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (
                sys.executable,
                "-c",
                script,
                str(source),
                str(artifact_root),
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )

    with ThreadPoolExecutor(max_workers=2) as calls:
        completed = tuple(
            call.result(timeout=35)
            for call in (calls.submit(invoke), calls.submit(invoke))
        )

    assert tuple(result.returncode for result in completed) == (0, 0)
    assert {result.stdout.strip() for result in completed} == {"false", "true"}
    assert not tuple(artifact_root.glob(".partial-*"))


def test_failed_analysis_does_not_publish_a_valid_cache_hit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "failed.png"
    Image.new("RGB", (160, 120), "white").save(source)
    artifact_root = tmp_path / "artifacts"

    with (
        patch(
            "hwp_reference_image_analyzer.save_overlay",
            side_effect=OSError("simulated artifact failure"),
        ),
        pytest.raises(OSError, match="simulated artifact failure"),
    ):
        _ = analyze_reference_image(source, artifact_root=artifact_root)

    assert not tuple(artifact_root.glob("ria-*/result.json"))
    recovered = analyze_reference_image(source, artifact_root=artifact_root)
    assert recovered.cache_hit is False


def test_cache_pruning_enforces_entry_and_byte_limits_oldest_first(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    for index in range(3):
        directory = artifact_root / f"ria-{index:016x}"
        directory.mkdir()
        result = directory / "result.json"
        _ = result.write_bytes(b"x" * 48)
        os.utime(result, (100 + index, 100 + index))
    limits = ReferenceImageResourceLimits(
        max_source_bytes=1024,
        max_source_pixels=10_000,
        max_analysis_pixels=10_000,
        max_analysis_artifact_bytes=64,
        max_cache_bytes=96,
        max_cache_entries=2,
        ttl_seconds=10_000_000_000,
        lock_timeout_seconds=5,
        stale_lock_seconds=60,
    )

    prune_reference_image_cache(artifact_root, limits)

    assert tuple(path.name for path in sorted(artifact_root.glob("ria-*"))) == (
        "ria-0000000000000001",
        "ria-0000000000000002",
    )


def test_cache_pruning_does_not_remove_in_progress_analysis(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    protected_id = "ria-0000000000000001"
    removable_id = "ria-0000000000000002"
    for analysis_id in (protected_id, removable_id):
        directory = artifact_root / analysis_id
        directory.mkdir(parents=True)
        _ = (directory / "result.json").write_text("{}", encoding="utf-8")
    limits = ReferenceImageResourceLimits(
        max_source_bytes=1024,
        max_source_pixels=10_000,
        max_analysis_pixels=10_000,
        max_analysis_artifact_bytes=1024,
        max_cache_bytes=1024,
        max_cache_entries=1,
        ttl_seconds=10_000_000_000,
        lock_timeout_seconds=5,
        stale_lock_seconds=60,
    )
    lock = acquire_reference_image_lock(artifact_root, protected_id, limits)
    try:
        prune_reference_image_cache(artifact_root, limits)
        assert (artifact_root / protected_id).is_dir()
    finally:
        lock.close()

    prune_reference_image_cache(artifact_root, limits)
    assert len(tuple(artifact_root.glob("ria-*"))) == 1

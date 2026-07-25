from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import JsonValue, TypeAdapter
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_reference_image_analyzer import analyze_reference_image  # noqa: E402
from hwp_reference_image_contract import (  # noqa: E402
    NormalizedBox,
    ReferenceImageAnalysis,
    ReferenceImageObject,
    ReferenceImageSegment,
)
from hwp_reference_image_summary import (  # noqa: E402
    ReferenceArtifact,
    compact_reference_image_analysis,
    reference_image_analysis_section,
)


def _segment(
    index: int,
    orientation: Literal["horizontal", "vertical"],
    start: tuple[float, float],
    end: tuple[float, float],
) -> ReferenceImageSegment:
    return ReferenceImageSegment(
        segment_id=f"seg-{index:012x}",
        orientation=orientation,
        start_x=start[0],
        start_y=start[1],
        end_x=end[0],
        end_y=end[1],
        width_px=1,
        color=(0, 0, 0),
    )


def _analysis(
    tmp_path: Path,
    *,
    segments: tuple[ReferenceImageSegment, ...],
    objects: tuple[ReferenceImageObject, ...] = (),
) -> ReferenceImageAnalysis:
    source = tmp_path / "fixture.png"
    _ = source.write_bytes(b"fixture")
    overlay = tmp_path / "overlay.png"
    _ = overlay.write_bytes(b"overlay")
    return ReferenceImageAnalysis(
        analysis_id="ria-0123456789abcdef",
        analyzer_version="test",
        image_hash="0" * 64,
        source_image=source,
        image_width=1000,
        image_height=1200,
        analysis_time_ms=1,
        cache_hit=False,
        objects=objects,
        text_regions=(),
        protected_gaps=(),
        visible_segments=segments,
        breakpoint_candidates=(),
        tiles=(),
        contact_sheet_paths=(),
        overlay_path=overlay,
    )


def _grid_segments(
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
    rows: int,
    columns: int,
) -> tuple[ReferenceImageSegment, ...]:
    result: list[ReferenceImageSegment] = []
    for row in range(rows + 1):
        y = top + (bottom - top) * row / rows
        result.append(_segment(len(result), "horizontal", (left, y), (right, y)))
    for column in range(columns + 1):
        x = left + (right - left) * column / columns
        result.append(_segment(len(result), "vertical", (x, top), (x, bottom)))
    return tuple(result)


def test_small_table_with_independent_page_regions_keeps_ordinary_layout(
    tmp_path: Path,
) -> None:
    map_region = ReferenceImageObject(
        region_id="obj-000000000001",
        kind="filled_region",
        bbox=NormalizedBox(left=0.08, top=0.42, right=0.92, bottom=0.86),
        fill=(220, 235, 220),
    )
    analysis = _analysis(
        tmp_path,
        segments=_grid_segments(
            left=0.08,
            top=0.12,
            right=0.46,
            bottom=0.31,
            rows=3,
            columns=4,
        ),
        objects=(map_region,),
    )

    compact = compact_reference_image_analysis(analysis)

    assert compact.recommended_execution_mode == "ordinary_layout"
    assert compact.draft_reference_layout is None
    assert "GRID_DOES_NOT_DOMINATE_PAGE" in compact.reason_codes


def test_isolated_reference_lines_remain_a_valid_ordinary_summary(
    tmp_path: Path,
) -> None:
    analysis = _analysis(
        tmp_path,
        segments=(
            _segment(1, "horizontal", (0.1, 0.2), (0.8, 0.2)),
            _segment(2, "vertical", (0.9, 0.3), (0.9, 0.7)),
        ),
    )

    compact = compact_reference_image_analysis(analysis)

    assert compact.recommended_execution_mode == "ordinary_layout"
    assert compact.structure.orthogonal_intersections == 0
    assert compact.draft_reference_layout is None


def test_dense_page_grid_produces_a_direct_reference_layout_draft(
    tmp_path: Path,
) -> None:
    analysis = _analysis(
        tmp_path,
        segments=_grid_segments(
            left=0.04,
            top=0.05,
            right=0.96,
            bottom=0.94,
            rows=9,
            columns=7,
        ),
    )

    compact = compact_reference_image_analysis(analysis)

    assert compact.recommended_execution_mode == "reference_layout_bulk"
    assert "DOMINANT_ORTHOGONAL_GRID" in compact.reason_codes
    assert compact.draft_reference_layout is not None
    assert compact.draft_reference_layout.analysis_id == analysis.analysis_id
    assert compact.draft_reference_layout.visible_edges
    assert compact.draft_reference_layout.model_dump(mode="json")["kind"] == (
        "reference_layout"
    )


def test_default_analysis_response_is_compact_and_details_are_paged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    source = tmp_path / "grid.png"
    image = Image.new("RGB", (420, 300), "white")
    draw = ImageDraw.Draw(image)
    for y in (30, 90, 150, 210, 270):
        draw.line((30, y, 390, y), fill="black", width=1)
    for x in (30, 120, 210, 300, 390):
        draw.line((x, 30, x, 270), fill="black", width=1)
    image.save(source)
    artifact_root = tmp_path / "artifacts"
    analysis = analyze_reference_image(source, artifact_root=artifact_root)

    compact = compact_reference_image_analysis(analysis)
    encoded = compact.model_dump_json()
    detail = reference_image_analysis_section(
        analysis.analysis_id,
        "visible_segments",
        artifact_root=artifact_root,
        offset=0,
        limit=2,
    )

    assert len(encoded.encode("utf-8")) < 100_000
    response = TypeAdapter(dict[str, JsonValue]).validate_json(encoded)
    assert not {"content", "structuredContent", "isError"} & set(response)
    assert detail.section == "visible_segments"
    assert detail.total == len(analysis.visible_segments)
    assert len(detail.items) <= 2
    assert compact.artifacts
    assert set(ReferenceArtifact.model_fields) == {
        "kind",
        "path",
        "size_bytes",
        "sha256",
    }

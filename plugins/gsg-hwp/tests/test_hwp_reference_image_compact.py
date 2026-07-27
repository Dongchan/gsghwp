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
    CompactReferenceImageAnalysis,
    ReferenceAnalysisCounts,
    ReferenceArtifact,
    ReferenceStructureSummary,
    compact_reference_image_analysis,
    enforce_compact_response_budget,
    reference_image_analysis_section,
)
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
    VisibleEdge,
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
    draft_detail = reference_image_analysis_section(
        analysis.analysis_id,
        "draft_reference_layout",
        artifact_root=artifact_root,
        offset=0,
        limit=1,
    )

    assert len(encoded.encode("utf-8")) < 100_000
    response = TypeAdapter(dict[str, JsonValue]).validate_json(encoded)
    assert not {"content", "structuredContent", "isError"} & set(response)
    assert detail.section == "visible_segments"
    assert detail.total == len(analysis.visible_segments)
    assert len(detail.items) <= 2
    assert draft_detail.total == 1
    assert ReferenceLayoutBlock.model_validate(
        draft_detail.items[0]
    ).analysis_id == analysis.analysis_id
    assert compact.artifacts
    assert set(ReferenceArtifact.model_fields) == {
        "kind",
        "path",
        "size_bytes",
        "sha256",
    }


def test_compact_response_enforces_actual_utf8_budget_for_maximal_layout(
    tmp_path: Path,
) -> None:
    breakpoints = tuple(index / 50 for index in range(51))
    layout = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=tmp_path / "한글 참고 이미지.png",
        analysis_id="ria-0123456789abcdef",
        row_breakpoints=breakpoints,
        column_breakpoints=breakpoints,
        visible_edges=tuple(
            VisibleEdge(
                orientation="horizontal",
                line=index % 51,
                start=0,
                end=50,
            )
            for index in range(5_000)
        ),
        styles=(
            ReferenceStyle(
                key="body",
                font_name="한글 본문 글꼴",
                font_size_pt=10,
            ),
        ),
        style_regions=tuple(
            StyleRegion(
                top=index % 50,
                left=(index // 50) % 50,
                bottom=index % 50 + 1,
                right=(index // 50) % 50 + 1,
                style_key="body",
            )
            for index in range(1_000)
        ),
        text_anchors=tuple(
            TextAnchor(
                row=index // 50,
                column=index % 50,
                text=f"한글 편집 가능 셀 {index:04d}",
                style_key="body",
            )
            for index in range(2_500)
        ),
    )
    oversized = CompactReferenceImageAnalysis(
        analysis_id="ria-0123456789abcdef",
        analyzer_version="test",
        source_image=tmp_path / "한글 참고 이미지.png",
        source_hash="0" * 64,
        source_size_bytes=1,
        image_width=1000,
        image_height=1000,
        analysis_time_ms=1,
        cache_hit=False,
        counts=ReferenceAnalysisCounts(
            objects=1_000,
            text_regions=2_500,
            protected_gaps=0,
            visible_segments=5_000,
            breakpoint_candidates=0,
            tiles=0,
            contact_sheets=0,
        ),
        structure=ReferenceStructureSummary(
            horizontal_segments=2_500,
            vertical_segments=2_500,
            orthogonal_intersections=2_500,
            dominant_segment_ratio=1,
            dominant_grid_area_ratio=1,
            partial_edge_ratio=1,
            repeated_style_regions=1_000,
            grid_fixed_text_regions=2_500,
        ),
        recommended_execution_mode="reference_layout_bulk",
        reason_codes=("DOMINANT_ORTHOGONAL_GRID",),
        row_breakpoints=breakpoints,
        column_breakpoints=breakpoints,
        draft_reference_layout=layout,
        detail_sections=(
            "objects",
            "text_regions",
            "protected_gaps",
            "visible_segments",
            "breakpoint_candidates",
            "tiles",
            "artifacts",
        ),
        analysis_result_path=tmp_path / "result.json",
        artifacts=(),
    )

    assert len(oversized.model_dump_json().encode("utf-8")) > 100_000
    bounded = enforce_compact_response_budget(oversized)
    repeated = enforce_compact_response_budget(oversized)
    encoded = bounded.model_dump_json().encode("utf-8")

    assert len(encoded) <= 100_000
    assert bounded.response_size_bytes == len(encoded)
    assert bounded.draft_reference_layout is None
    assert any(
        omission.section == "draft_reference_layout"
        and omission.omitted_count == 8_501
        for omission in bounded.omissions
    )
    assert bounded.model_dump_json() == repeated.model_dump_json()

    artifact_heavy = oversized.model_copy(
        update={
            "draft_reference_layout": None,
            "artifacts": tuple(
                ReferenceArtifact(
                    kind="contact_sheet",
                    path=tmp_path
                    / f"아주 긴 한글 산출물 경로 {index:04d} {'가' * 120}.png",
                    size_bytes=index,
                    sha256=f"{index:064x}",
                )
                for index in range(500)
            ),
        }
    )
    artifact_bounded = enforce_compact_response_budget(artifact_heavy)

    assert len(artifact_bounded.model_dump_json().encode("utf-8")) <= 100_000
    assert artifact_bounded.artifacts == ()
    assert any(
        omission.section == "artifacts"
        and omission.omitted_count == 500
        for omission in artifact_bounded.omissions
    )

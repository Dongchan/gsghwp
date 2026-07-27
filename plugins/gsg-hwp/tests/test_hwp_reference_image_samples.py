from __future__ import annotations

import sys
import os
from pathlib import Path

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
    ReferenceImageSegment,
    ReferenceProtectedGap,
)


# 개발자 로컬 샘플 폴더는 환경변수로 받는다.
# 배포본에 개인 경로를 넣지 않기 위해서다 (릴리스 QA 가 개인 경로·용어를 거부한다).
# 지정하지 않으면 이 파일의 표본 테스트는 건너뛴다.
REFERENCE_ROOT = Path(os.environ.get("HWP_REFERENCE_IMAGE_ROOT", ""))
ARTIFACT_ROOT = REFERENCE_ROOT.parent / "reference-image-analysis-regression"
SAMPLES = (
    "PureRef-copy-2026.07.23-16.02.38.png",
    "PureRef-copy-2026.07.23-16.02.44.png",
    "PureRef-copy-2026.07.23-16.02.48.png",
    "PureRef-copy-2026.07.23-16.02.52.png",
    "PureRef-copy-2026.07.23-16.03.01.png",
)


def _segment_crosses_gap(
    segment: ReferenceImageSegment,
    gap: ReferenceProtectedGap,
) -> bool:
    if gap.orientation == "vertical":
        return (
            segment.orientation == "horizontal"
            and gap.bbox.top < segment.start_y < gap.bbox.bottom
            and segment.start_x < gap.bbox.left
            and segment.end_x > gap.bbox.right
        )
    return (
        segment.orientation == "vertical"
        and gap.bbox.left < segment.start_x < gap.bbox.right
        and segment.start_y < gap.bbox.top
        and segment.end_y > gap.bbox.bottom
    )


@pytest.mark.parametrize("filename", SAMPLES)
def test_reference_image_samples_produce_reviewable_global_evidence(
    filename: str,
) -> None:
    if not REFERENCE_ROOT.name:
        pytest.skip("HWP_REFERENCE_IMAGE_ROOT 가 지정되지 않았습니다")
    source = REFERENCE_ROOT / filename
    assert source.is_file()

    result = analyze_reference_image(source, artifact_root=ARTIFACT_ROOT)

    assert result.image_width > 0
    assert result.image_height > 0
    assert result.objects
    assert result.text_regions
    assert result.visible_segments
    assert result.breakpoint_candidates
    assert len(result.tiles) > 1
    assert result.overlay_path.is_file()
    assert result.contact_sheet_paths
    assert all(path.is_file() for path in result.contact_sheet_paths)
    for gap in result.protected_gaps:
        assert gap.minimum_size_px > 0
        assert not any(
            _segment_crosses_gap(segment, gap)
            for segment in result.visible_segments
        )
        positions = {
            candidate.position
            for candidate in result.breakpoint_candidates
            if candidate.source == "gap"
            and candidate.source_id == gap.gap_id
        }
        expected = (
            {round(gap.bbox.left, 8), round(gap.bbox.right, 8)}
            if gap.orientation == "vertical"
            else {round(gap.bbox.top, 8), round(gap.bbox.bottom, 8)}
        )
        assert positions == expected

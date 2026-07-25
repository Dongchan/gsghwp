from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Annotated, final

from anyio import to_thread
from pydantic import Field

from hwp_reference_image_analyzer import analyze_reference_image
from hwp_reference_image_summary import (
    CompactReferenceImageAnalysis,
    ReferenceAnalysisDetail,
    ReferenceDetailSection,
    compact_reference_image_analysis,
    reference_image_analysis_section,
)


def _analyze_compact(
    source: Path,
    artifact_root: Path | None,
) -> CompactReferenceImageAnalysis:
    return compact_reference_image_analysis(
        analyze_reference_image(
            source,
            artifact_root=artifact_root,
        )
    )


@final
class HwpReferenceImageTools:
    async def hwp_analyze_reference_image(
        self,
        *,
        image_path: str,
        artifact_root: str | None = None,
    ) -> CompactReferenceImageAnalysis:
        source = Path(image_path)
        root = Path(artifact_root) if artifact_root is not None else None
        return await to_thread.run_sync(
            partial(
                _analyze_compact,
                source,
                root,
            )
        )

    async def hwp_get_reference_image_analysis_section(
        self,
        *,
        analysis_id: Annotated[
            str,
            Field(pattern=r"^ria-[0-9a-f]{16}$"),
        ],
        section: ReferenceDetailSection,
        artifact_root: str | None = None,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> ReferenceAnalysisDetail:
        root = Path(artifact_root) if artifact_root is not None else None
        return await to_thread.run_sync(
            partial(
                reference_image_analysis_section,
                analysis_id,
                section,
                artifact_root=root,
                offset=offset,
                limit=limit,
            )
        )

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Annotated, final

from anyio import to_thread
from pydantic import Field

from hwp_reference_image_analyzer import analyze_reference_image
from hwp_reference_image_crop import (
    PreparedReferenceImageCrops,
    ReferenceImageCropRequest,
    prepare_reference_image_crops,
)
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
    full_scan: bool,
) -> CompactReferenceImageAnalysis:
    return compact_reference_image_analysis(
        analyze_reference_image(
            source,
            artifact_root=artifact_root,
            full_scan=full_scan,
        )
    )


@final
class HwpReferenceImageTools:
    async def hwp_prepare_image_crops(
        self,
        *,
        image_path: str,
        crops: Annotated[
            tuple[ReferenceImageCropRequest, ...],
            Field(
                min_length=1,
                max_length=64,
                description=(
                    "Model-selected semantic raster assets. Give one rough box per "
                    "asset and include every attached label, callout, and legend; "
                    "the analyzer, not the model, resolves the final pixel edges."
                ),
            ),
        ],
    ) -> PreparedReferenceImageCrops:
        return await to_thread.run_sync(
            partial(prepare_reference_image_crops, Path(image_path), crops)
        )

    async def hwp_analyze_reference_image(
        self,
        *,
        image_path: str,
        artifact_root: str | None = None,
        full_scan: Annotated[
            bool,
            Field(
                description=(
                    "기본값 False 는 시간 예산을 지키고 마감을 넘길 부하는 몇 초 "
                    "안에 미완 표시와 함께 돌아옵니다. True 는 예산 없이 전체를 "
                    "훑습니다 — 명시적 좌표 증거가 꼭 필요할 때만 쓰고, 240초 "
                    "작업자 마감을 넘겨 결과를 못 받을 수 있습니다."
                )
            ),
        ] = False,
    ) -> CompactReferenceImageAnalysis:
        source = Path(image_path)
        root = Path(artifact_root) if artifact_root is not None else None
        return await to_thread.run_sync(
            partial(
                _analyze_compact,
                source,
                root,
                full_scan,
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

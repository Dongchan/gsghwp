from __future__ import annotations

from hwp_reference_image_analyzer import load_cached_reference_image_analysis
from hwp_reference_image_layout_bridge import align_reference_layout_to_analysis
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_image_analysis import analyze_layout_image
from hwp_reference_layout_ocr_evidence import supplement_text_anchors


def prepare_reference_layout(block: ReferenceLayoutBlock) -> ReferenceLayoutBlock:
    prepared = block
    if block.source_image is not None:
        if block.analysis_id is not None:
            analysis = load_cached_reference_image_analysis(
                block.source_image,
                block.analysis_id,
            )
            prepared = align_reference_layout_to_analysis(block, analysis)
        else:
            prepared = analyze_layout_image(prepared).layout
    return ReferenceLayoutBlock.model_validate(
        {
            **prepared.model_dump(),
            "text_anchors": supplement_text_anchors(prepared),
        }
    )

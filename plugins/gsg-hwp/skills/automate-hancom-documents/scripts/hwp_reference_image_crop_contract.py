from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from hwp_live_values import ContractModel
from hwp_reference_image_pixels import PixelBox


CropBoundaryMode = Literal["tight_raster", "annotated_group"]
CropAnalysisMethod = Literal[
    "exact_full_image",
    "dominant_connected_content",
    "texture_rectangle",
    "requested_fallback",
]


class ReferenceImagePixelBox(ContractModel):
    left: int = Field(ge=0)
    top: int = Field(ge=0)
    right: int = Field(ge=1)
    bottom: int = Field(ge=1)

    def as_pixel_box(self) -> PixelBox:
        return PixelBox(
            left=self.left,
            top=self.top,
            right=self.right,
            bottom=self.bottom,
        )

    @classmethod
    def from_pixel_box(cls, box: PixelBox) -> ReferenceImagePixelBox:
        return cls(
            left=box.left,
            top=box.top,
            right=box.right,
            bottom=box.bottom,
        )


@dataclass(frozen=True, slots=True)
class CropBoundaryAnalysis:
    requested: PixelBox
    refined: PixelBox
    method: CropAnalysisMethod
    confidence: float
    needs_review: bool
    component_count: int


def box_intersection_area(left: PixelBox, right: PixelBox) -> int:
    width = max(0, min(left.right, right.right) - max(left.left, right.left))
    height = max(0, min(left.bottom, right.bottom) - max(left.top, right.top))
    return width * height


def union_boxes(left: PixelBox, right: PixelBox) -> PixelBox:
    return PixelBox(
        left=min(left.left, right.left),
        top=min(left.top, right.top),
        right=max(left.right, right.right),
        bottom=max(left.bottom, right.bottom),
    )


def expanded_search_box(
    requested: PixelBox,
    *,
    width: int,
    height: int,
) -> PixelBox:
    x_margin = max(8, round(width * 0.035), round(requested.width * 0.2))
    y_margin = max(8, round(height * 0.035), round(requested.height * 0.2))
    return PixelBox(
        left=max(0, requested.left - x_margin),
        top=max(0, requested.top - y_margin),
        right=min(width, requested.right + x_margin),
        bottom=min(height, requested.bottom + y_margin),
    )

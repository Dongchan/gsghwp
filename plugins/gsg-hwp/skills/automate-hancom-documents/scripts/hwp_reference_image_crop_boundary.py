from __future__ import annotations

from typing import assert_never

from PIL import Image

from hwp_reference_image_crop_analysis import refine_crop_boundary
from hwp_reference_image_crop_contract import (
    CropBoundaryAnalysis,
    CropBoundaryMode,
)
from hwp_reference_image_crop_texture import refine_tight_raster_boundary
from hwp_reference_image_pixels import PixelBox


def resolve_crop_boundary(
    image: Image.Image,
    requested: PixelBox,
    *,
    mode: CropBoundaryMode,
) -> CropBoundaryAnalysis:
    if mode == "tight_raster":
        return refine_tight_raster_boundary(image, requested)
    if mode == "annotated_group":
        return refine_crop_boundary(image, requested)
    assert_never(mode)

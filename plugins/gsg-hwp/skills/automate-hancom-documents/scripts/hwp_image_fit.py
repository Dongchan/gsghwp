from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from hwp_errors import HwpLiveError


def fit_image_in_box(
    path: Path,
    *,
    width_mm: float,
    height_mm: float,
) -> tuple[float, float]:
    if width_mm <= 0 or height_mm <= 0:
        raise HwpLiveError("그림 배치 영역의 가로와 세로는 0보다 커야 합니다")
    try:
        with Image.open(path) as image:
            oriented = ImageOps.exif_transpose(image)
            try:
                pixel_width, pixel_height = oriented.size
            finally:
                if oriented is not image:
                    oriented.close()
    except (OSError, UnidentifiedImageError) as error:
        raise HwpLiveError(f"그림 크기를 읽지 못했습니다: {path}") from error
    if pixel_width < 1 or pixel_height < 1:
        raise HwpLiveError(f"그림의 픽셀 크기가 올바르지 않습니다: {path}")
    scale = min(width_mm / pixel_width, height_mm / pixel_height)
    return pixel_width * scale, pixel_height * scale

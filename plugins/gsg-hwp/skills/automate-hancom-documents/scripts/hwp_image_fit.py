from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from hwp_errors import HwpLiveError
from hwp_live_native_action_commands import PictureCrop


def _pixel_size(path: Path) -> tuple[int, int]:
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
    return pixel_width, pixel_height


def _require_box(width_mm: float, height_mm: float) -> None:
    if width_mm <= 0 or height_mm <= 0:
        raise HwpLiveError("그림 배치 영역의 가로와 세로는 0보다 커야 합니다")


def fit_image_in_box(
    path: Path,
    *,
    width_mm: float,
    height_mm: float,
) -> tuple[float, float]:
    """상자 안에 원본 비율 그대로 넣을 때의 크기 (여백이 남는 쪽이 생긴다)."""
    _require_box(width_mm, height_mm)
    pixel_width, pixel_height = _pixel_size(path)
    scale = min(width_mm / pixel_width, height_mm / pixel_height)
    return pixel_width * scale, pixel_height * scale


def cover_crop_in_box(
    path: Path,
    *,
    width_mm: float,
    height_mm: float,
) -> PictureCrop:
    """상자를 꽉 채우려면 원본에서 감춰야 하는 가장자리 비율.

    넘치는 쪽을 가운데 기준으로 반씩 나눠 감춘다. 값은 비율이므로 이 함수는
    원본 파일을 열어 크기만 읽고, 실제 자르기는 한/글 SkipLeft/SkipTop/
    SkipRight/SkipBottom 이 C++/ATL 브리지에서 수행한다. 사본을 만들지 않는다.
    상자 비율이 이미 원본과 같으면 네 값이 모두 0인 자르기를 돌려준다.
    """
    _require_box(width_mm, height_mm)
    pixel_width, pixel_height = _pixel_size(path)
    box_ratio = width_mm / height_mm
    image_ratio = pixel_width / pixel_height
    if image_ratio > box_ratio:
        # 원본이 더 넓다. 세로를 다 쓰고 좌우를 감춘다.
        hidden = 1.0 - box_ratio / image_ratio
        edge = max(0.0, hidden) / 2.0
        return PictureCrop(left=edge, right=edge)
    # 원본이 더 높다. 가로를 다 쓰고 위아래를 감춘다.
    hidden = 1.0 - image_ratio / box_ratio
    edge = max(0.0, hidden) / 2.0
    return PictureCrop(top=edge, bottom=edge)


def fit_cropped_image_in_box(
    path: Path,
    *,
    width_mm: float,
    height_mm: float,
    crop: PictureCrop,
) -> tuple[float, float]:
    """자르고 남은 부분을 상자에 넣을 때의 크기.

    C++/ATL 쪽 FitImageInBox 와 같은 식이다. 여기서 계산한 값은 삽입 결과를
    대조하는 기대치로만 쓰고, 실제 크기는 네이티브가 정한다.
    """
    _require_box(width_mm, height_mm)
    pixel_width, pixel_height = _pixel_size(path)
    visible_width = pixel_width * (1.0 - crop.left - crop.right)
    visible_height = pixel_height * (1.0 - crop.top - crop.bottom)
    if visible_width <= 0 or visible_height <= 0:
        raise HwpLiveError(f"그림 자르기 후 남는 부분이 없습니다: {path}")
    scale = min(width_mm / visible_width, height_mm / visible_height)
    return visible_width * scale, visible_height * scale

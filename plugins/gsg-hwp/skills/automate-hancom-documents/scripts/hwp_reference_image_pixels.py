from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageChops, ImageFilter

from hwp_live_values import Rgb


PixelOrientation = Literal["horizontal", "vertical"]


@dataclass(frozen=True, slots=True)
class PixelBox:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class PixelSegment:
    orientation: PixelOrientation
    start: int
    end: int
    position: int
    width: int
    color: Rgb


@dataclass(frozen=True, slots=True)
class PixelObject:
    bbox: PixelBox
    fill: Rgb | None
    edges: tuple[Literal["top", "right", "bottom", "left"], ...]


@dataclass(frozen=True, slots=True)
class PixelGap:
    orientation: PixelOrientation
    bbox: PixelBox
    minimum_size: int
    bounded_by: tuple[int, int]


@dataclass(frozen=True, slots=True)
class PixelEvidence:
    objects: tuple[PixelObject, ...]
    text_regions: tuple[PixelBox, ...]
    gaps: tuple[PixelGap, ...]
    segments: tuple[PixelSegment, ...]


@dataclass(frozen=True, slots=True)
class PixelCanvas:
    image: Image.Image
    width: int
    height: int
    rgb: bytes
    contrast: bytes

    @classmethod
    def from_image(cls, image: Image.Image) -> PixelCanvas:
        rgb_image = image.convert("RGB")
        try:
            gray = rgb_image.convert("L")
            try:
                local_average = gray.filter(ImageFilter.BoxBlur(2))
                try:
                    difference = ImageChops.difference(gray, local_average)
                    try:
                        contrast = difference.point(
                            tuple(
                                255 if value >= 12 else 0
                                for value in range(256)
                            )
                        )
                        try:
                            contrast_bytes = contrast.tobytes()
                        finally:
                            contrast.close()
                    finally:
                        difference.close()
                finally:
                    local_average.close()
            finally:
                gray.close()
            return cls(
                image=rgb_image,
                width=rgb_image.width,
                height=rgb_image.height,
                rgb=rgb_image.tobytes(),
                contrast=contrast_bytes,
            )
        except BaseException:
            rgb_image.close()
            raise

    def close(self) -> None:
        self.image.close()

    def rgb_array(self) -> NDArray[np.uint8]:
        """RGB 바이트를 (height, width, 3) uint8 배열로 본다.

        np.frombuffer 는 복사하지 않는 뷰라서 호출 비용이 사실상 없다. 픽셀
        재측정 루프가 이 배열 위에서 정수 연산으로 돌아 파이썬 회전 없이도
        스칼라 구현과 같은 값을 낸다 — uint8 뺄셈은 반올림이 없으므로 두
        경로의 결과가 근사치가 아니라 정확히 같다.
        """
        return np.frombuffer(self.rgb, dtype=np.uint8).reshape(
            self.height,
            self.width,
            3,
        )

    def contrast_array(self) -> NDArray[np.uint8]:
        return np.frombuffer(self.contrast, dtype=np.uint8).reshape(
            self.height,
            self.width,
        )

    def color(self, x: int, y: int) -> Rgb:
        offset = (y * self.width + x) * 3
        return (
            self.rgb[offset],
            self.rgb[offset + 1],
            self.rgb[offset + 2],
        )

    def contrast_ratio(self, bbox: PixelBox) -> float:
        changed = 0
        area = max(1, bbox.width * bbox.height)
        for y_position in range(bbox.top, bbox.bottom):
            start = y_position * self.width + bbox.left
            end = y_position * self.width + bbox.right
            changed += self.contrast[start:end].count(255)
        return changed / area

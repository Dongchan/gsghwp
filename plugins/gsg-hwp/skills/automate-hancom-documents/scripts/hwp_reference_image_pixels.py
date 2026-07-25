from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
        gray = rgb_image.convert("L")
        local_average = gray.filter(ImageFilter.BoxBlur(2))
        difference = ImageChops.difference(gray, local_average)
        contrast = difference.point(
            tuple(255 if value >= 12 else 0 for value in range(256))
        )
        return cls(
            image=rgb_image,
            width=rgb_image.width,
            height=rgb_image.height,
            rgb=rgb_image.tobytes(),
            contrast=contrast.tobytes(),
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

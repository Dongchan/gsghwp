from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import override

from PIL import Image, ImageChops

from hwp_reference_layout_contract import (
    ReferenceStyle,
    StyleRegion,
    VisibleEdge,
)
from hwp_reference_layout_geometry import SectionPageGeometry, UsablePageArea
from hwp_reference_layout_placement import PlacementFrame


@dataclass(frozen=True, slots=True)
class RenderedPage:
    path: Path
    geometry: SectionPageGeometry | None
    number: int


@dataclass(frozen=True, slots=True)
class RenderedBodyBoundsError(ValueError):
    bounds: tuple[int, int, int, int]
    rendered_size: tuple[int, int]

    @override
    def __str__(self) -> str:
        return (
            "usable body area falls outside the rendered page: "
            f"{self.bounds} not within {self.rendered_size}"
        )


def _body_image(
    rendered_page: RenderedPage,
) -> Image.Image:
    with Image.open(rendered_page.path) as rendered_opened:
        rendered = rendered_opened.convert("RGB")
    if rendered_page.geometry is None:
        return rendered
    paper_width, paper_height = rendered_page.geometry.oriented_paper_size()
    area = rendered_page.geometry.usable_area(page_number=rendered_page.number)
    left = round(area.left * rendered.width / paper_width)
    top = round(area.top * rendered.height / paper_height)
    right = round((area.left + area.width) * rendered.width / paper_width)
    bottom = round((area.top + area.height) * rendered.height / paper_height)
    if not (0 <= left < right <= rendered.width and 0 <= top < bottom <= rendered.height):
        raise RenderedBodyBoundsError(
            bounds=(left, top, right, bottom),
            rendered_size=rendered.size,
        )
    return rendered.crop((left, top, right, bottom))


def rendered_placement(
    rendered_page: RenderedPage,
    source_path: Path,
    *,
    rows: int,
    columns: int,
    visible_edges: Sequence[VisibleEdge],
    styles: Sequence[ReferenceStyle] = (),
    style_regions: Sequence[StyleRegion] = (),
) -> Image.Image:
    body = _body_image(rendered_page)
    if rendered_page.geometry is None:
        frame = PlacementFrame.from_source(
            UsablePageArea(left=0, top=0, width=body.width, height=body.height),
            source_path,
        )
    else:
        area = rendered_page.geometry.usable_area(page_number=rendered_page.number)
        mapped = PlacementFrame.from_reference(
            area,
            source_path,
            rows=rows,
            columns=columns,
            visible_edges=visible_edges,
            styles=styles,
            style_regions=style_regions,
        )
        frame = PlacementFrame(
            left=round((mapped.left - area.left) * body.width / area.width),
            top=round((mapped.top - area.top) * body.height / area.height),
            width=round(mapped.width * body.width / area.width),
            height=round(mapped.height * body.height / area.height),
        )
    return body.crop(
        (
            frame.left,
            frame.top,
            frame.left + frame.width,
            frame.top + frame.height,
        )
    )


def _uniform_top_center(
    rendered: Image.Image,
    target_size: tuple[int, int],
) -> Image.Image:
    target_width, target_height = target_size
    scale = min(target_width / rendered.width, target_height / rendered.height)
    offset_x = (target_width - rendered.width * scale) / 2
    inverse_scale = 1 / scale
    return rendered.transform(
        target_size,
        Image.Transform.AFFINE,
        (
            inverse_scale,
            0,
            -offset_x * inverse_scale,
            0,
            inverse_scale,
            0,
        ),
        resample=Image.Resampling.BICUBIC,
        fillcolor="white",
    )


def difference_metrics(
    source_path: Path,
    rendered_frame: Image.Image,
) -> tuple[float, float, Image.Image, Image.Image, Image.Image]:
    with Image.open(source_path) as source_opened:
        source = source_opened.convert("RGB")
    rendered = _uniform_top_center(rendered_frame, source.size)
    difference = ImageChops.difference(source, rendered)
    histogram = difference.convert("L").histogram()
    pixels = difference.width * difference.height
    mean = sum(value * count for value, count in enumerate(histogram)) / max(1, pixels)
    changed = sum(histogram[13:])
    return mean, changed / max(1, pixels), difference, source, rendered

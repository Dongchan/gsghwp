from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import override

from PIL import Image

from hwp_reference_layout_contract import (
    ReferenceStyle,
    StyleRegion,
    VisibleEdge,
)
from hwp_reference_layout_geometry import (
    HWPUNITS_PER_INCH,
    MILLIMETERS_PER_INCH,
    MappedAxis,
    UsablePageArea,
    map_normalized_breakpoints,
)

PERIMETER_PAINT_GUARD = HWPUNITS_PER_INCH // 72


@dataclass(frozen=True, slots=True)
class InvalidSourceImageSizeError(ValueError):
    width: int
    height: int

    @override
    def __str__(self) -> str:
        return f"source image dimensions must be positive: {self.width}x{self.height}"


@dataclass(frozen=True, slots=True)
class PerimeterPaintInsets:
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @classmethod
    def from_edges(
        cls,
        edges: Sequence[VisibleEdge],
        *,
        rows: int,
        columns: int,
    ) -> PerimeterPaintInsets:
        values = {"left": 0, "top": 0, "right": 0, "bottom": 0}
        for edge in edges:
            if edge.style == "none":
                continue
            side = (
                "top"
                if edge.orientation == "horizontal" and edge.line == 0
                else "bottom"
                if edge.orientation == "horizontal" and edge.line == rows
                else "left"
                if edge.orientation == "vertical" and edge.line == 0
                else "right"
                if edge.orientation == "vertical" and edge.line == columns
                else None
            )
            if side is not None:
                width_mm = float(edge.width.removesuffix("mm"))
                width = ceil(
                    width_mm * HWPUNITS_PER_INCH / MILLIMETERS_PER_INCH
                ) + PERIMETER_PAINT_GUARD
                values[side] = max(values[side], width)
        return cls(**values)

    @classmethod
    def from_reference(
        cls,
        edges: Sequence[VisibleEdge],
        styles: Sequence[ReferenceStyle],
        style_regions: Sequence[StyleRegion],
        *,
        rows: int,
        columns: int,
    ) -> PerimeterPaintInsets:
        edge_insets = cls.from_edges(
            edges,
            rows=rows,
            columns=columns,
        )
        values = {
            "left": edge_insets.left,
            "top": edge_insets.top,
            "right": edge_insets.right,
            "bottom": edge_insets.bottom,
        }
        styles_by_key = {style.key: style for style in styles}
        for region in style_regions:
            style = styles_by_key.get(region.style_key)
            if (
                style is None
                or style.fill_color is None
                or style.fill_color == (255, 255, 255)
            ):
                continue
            if region.left == 0:
                values["left"] = max(values["left"], PERIMETER_PAINT_GUARD)
            if region.top == 0:
                values["top"] = max(values["top"], PERIMETER_PAINT_GUARD)
            if region.right == columns:
                values["right"] = max(values["right"], PERIMETER_PAINT_GUARD)
            if region.bottom == rows:
                values["bottom"] = max(values["bottom"], PERIMETER_PAINT_GUARD)
        return cls(**values)

    def apply(self, area: UsablePageArea) -> UsablePageArea:
        width = area.width - self.left - self.right
        height = area.height - self.top - self.bottom
        if width <= 0 or height <= 0:
            raise ValueError("perimeter edges leave no usable placement area")
        return UsablePageArea(
            left=area.left + self.left,
            top=area.top + self.top,
            width=width,
            height=height,
        )


@dataclass(frozen=True, slots=True)
class PlacementFrame:
    left: int
    top: int
    width: int
    height: int

    @classmethod
    def contain_top_center(
        cls,
        area: UsablePageArea,
        source_size: tuple[int, int],
    ) -> PlacementFrame:
        source_width, source_height = source_size
        if source_width <= 0 or source_height <= 0:
            raise InvalidSourceImageSizeError(source_width, source_height)
        scale = min(area.width / source_width, area.height / source_height)
        width = min(area.width, max(1, round(source_width * scale)))
        height = min(area.height, max(1, round(source_height * scale)))
        return cls(
            left=area.left + (area.width - width) // 2,
            top=area.top,
            width=width,
            height=height,
        )

    @classmethod
    def from_source(
        cls,
        area: UsablePageArea,
        source_image: Path | None,
    ) -> PlacementFrame:
        if source_image is None:
            return cls(
                left=area.left,
                top=area.top,
                width=area.width,
                height=area.height,
            )
        with Image.open(source_image) as source:
            return cls.contain_top_center(area, source.size)

    @classmethod
    def from_reference(
        cls,
        area: UsablePageArea,
        source_image: Path | None,
        *,
        rows: int,
        columns: int,
        visible_edges: Sequence[VisibleEdge],
        styles: Sequence[ReferenceStyle] = (),
        style_regions: Sequence[StyleRegion] = (),
    ) -> PlacementFrame:
        safe_area = PerimeterPaintInsets.from_reference(
            visible_edges,
            styles,
            style_regions,
            rows=rows,
            columns=columns,
        ).apply(area)
        return cls.from_source(safe_area, source_image)

    def map_columns(self, breakpoints: tuple[float, ...]) -> MappedAxis:
        return map_normalized_breakpoints(
            breakpoints,
            origin=self.left,
            extent=self.width,
        )

    def map_rows(self, breakpoints: tuple[float, ...]) -> MappedAxis:
        return map_normalized_breakpoints(
            breakpoints,
            origin=self.top,
            extent=self.height,
        )

from __future__ import annotations

from dataclasses import dataclass
from math import floor


HWPUNITS_PER_INCH = 7_200
MILLIMETERS_PER_INCH = 25.4
MINIMUM_HWP_GRID_INTERVAL = 100


def mm_to_hwpunit(value: float) -> int:
    return round(value * HWPUNITS_PER_INCH / MILLIMETERS_PER_INCH)


# ParaShape's length fields -- LeftMargin, RightMargin, Indentation,
# PrevSpacing, NextSpacing, and LineSpacing when the type is fixed or
# margin-only -- are URC, not plain HWPUNIT. Bit 0 selects the representation:
# 0 is an absolute length in HWPUNIT shifted up one bit, 1 is relative to the
# character size. Writing a plain HWPUNIT therefore lands at half the requested
# length, or lands in the relative representation whenever the value is odd.
# Reading one back as a plain HWPUNIT doubles it. Both directions have to agree
# or an agent that reads a document's own margin and asks for the same margin
# gets twice it.
def absolute_urc(millimeters: float) -> int:
    return mm_to_hwpunit(millimeters) << 1


def urc_to_mm(value: int | None) -> float | None:
    """An absolute URC length in mm, or ``None`` when it is a relative one.

    A relative length is a multiple of the character size, which is not a
    length in millimetres at all. Answering with a number would be inventing
    one, so callers are told there is nothing to report instead.
    """
    if value is None or value & 1:
        return None
    return round((value >> 1) * MILLIMETERS_PER_INCH / HWPUNITS_PER_INCH, 3)


@dataclass(frozen=True, slots=True)
class UsablePageArea:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class MappedAxis:
    boundaries: tuple[int, ...]
    sizes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SectionPageGeometry:
    paper_width: int
    paper_height: int
    landscape: bool
    left_margin: int
    right_margin: int
    top_margin: int
    bottom_margin: int
    header: int
    footer: int
    gutter: int
    gutter_type: int

    @classmethod
    def from_mm(
        cls,
        *,
        paper_width_mm: float,
        paper_height_mm: float,
        landscape: bool,
        left_margin_mm: float,
        right_margin_mm: float,
        top_margin_mm: float,
        bottom_margin_mm: float,
        header_mm: float,
        footer_mm: float,
        gutter_mm: float,
        gutter_type: int,
    ) -> SectionPageGeometry:
        if gutter_type not in (0, 1, 2):
            raise ValueError("gutter_type must be 0, 1, or 2")
        return cls(
            paper_width=mm_to_hwpunit(paper_width_mm),
            paper_height=mm_to_hwpunit(paper_height_mm),
            landscape=landscape,
            left_margin=mm_to_hwpunit(left_margin_mm),
            right_margin=mm_to_hwpunit(right_margin_mm),
            top_margin=mm_to_hwpunit(top_margin_mm),
            bottom_margin=mm_to_hwpunit(bottom_margin_mm),
            header=mm_to_hwpunit(header_mm),
            footer=mm_to_hwpunit(footer_mm),
            gutter=mm_to_hwpunit(gutter_mm),
            gutter_type=gutter_type,
        )

    def oriented_paper_size(self) -> tuple[int, int]:
        paper_width = self.paper_width
        paper_height = self.paper_height
        if self.landscape and paper_width < paper_height:
            paper_width, paper_height = paper_height, paper_width
        if not self.landscape and paper_width > paper_height:
            paper_width, paper_height = paper_height, paper_width
        return paper_width, paper_height

    def usable_area(self, *, page_number: int) -> UsablePageArea:
        if page_number < 1:
            raise ValueError("page_number must be positive")
        paper_width, paper_height = self.oriented_paper_size()

        left = self.left_margin
        top = self.top_margin + self.header
        right_reserved = self.right_margin
        bottom_reserved = self.bottom_margin + self.footer
        if self.gutter_type == 2:
            top += self.gutter
        elif self.gutter_type == 0 or page_number % 2 == 1:
            left += self.gutter
        else:
            right_reserved += self.gutter

        width = paper_width - left - right_reserved
        height = paper_height - top - bottom_reserved
        if width <= 0 or height <= 0:
            raise ValueError("page margins leave no usable body area")
        return UsablePageArea(left=left, top=top, width=width, height=height)


def map_normalized_breakpoints(
    breakpoints: tuple[float, ...],
    *,
    origin: int,
    extent: int,
) -> MappedAxis:
    intervals = len(breakpoints) - 1
    if extent < intervals * MINIMUM_HWP_GRID_INTERVAL:
        raise ValueError("axis extent is too small for the HWP grid minimum")
    if (
        len(breakpoints) < 2
        or breakpoints[0] != 0
        or breakpoints[-1] != 1
        or any(left >= right for left, right in zip(breakpoints, breakpoints[1:]))
    ):
        raise ValueError("breakpoints must increase from 0 to 1")

    ideals = [
        extent * (right - left)
        for left, right in zip(breakpoints, breakpoints[1:])
    ]
    available = extent - intervals * MINIMUM_HWP_GRID_INTERVAL
    desired = [
        max(0.0, value - MINIMUM_HWP_GRID_INTERVAL)
        for value in ideals
    ]
    desired_total = sum(desired)
    scaled = (
        [available / intervals] * intervals
        if desired_total == 0
        else [value * available / desired_total for value in desired]
    )
    sizes = [
        MINIMUM_HWP_GRID_INTERVAL + floor(value)
        for value in scaled
    ]
    remainder = extent - sum(sizes)
    order = sorted(
        range(len(sizes)),
        key=lambda index: (scaled[index] - floor(scaled[index]), -index),
        reverse=True,
    )
    for index in order[:remainder]:
        sizes[index] += 1
    if any(size < MINIMUM_HWP_GRID_INTERVAL for size in sizes):
        raise ValueError("breakpoints produce an unsupported HWP grid interval")

    boundaries = [origin]
    for size in sizes:
        boundaries.append(boundaries[-1] + size)
    boundaries[-1] = origin + extent
    return MappedAxis(boundaries=tuple(boundaries), sizes=tuple(sizes))

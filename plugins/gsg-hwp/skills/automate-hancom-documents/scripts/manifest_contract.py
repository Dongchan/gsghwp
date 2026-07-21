from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    StringConstraints,
    model_validator,
)

PageMode = Literal["editable", "hybrid", "raster"]
Orientation = Literal["portrait", "landscape", "square"]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$")]
NonEmptyPositiveInts = Annotated[list[PositiveInt], Field(min_length=1)]

_PAGE_TOKEN: Final[re.Pattern[str]] = re.compile(r"([0-9]+)(?:-([0-9]+))?")


class PageSelectionError(ValueError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ManifestInvariantError(ValueError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class PageRange(StrictModel):
    start: PositiveInt
    end: PositiveInt

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end < self.start:
            raise ManifestInvariantError(reason="page range end precedes start")
        return self


class ManifestTimings(StrictModel):
    metadata_seconds: NonNegativeFloat
    render_seconds: NonNegativeFloat
    analysis_seconds: NonNegativeFloat
    total_seconds: NonNegativeFloat


class ModeSummary(StrictModel):
    editable: NonNegativeInt
    hybrid: NonNegativeInt
    raster: NonNegativeInt


class PageRecord(StrictModel):
    number: PositiveInt
    mode: PageMode
    width_pt: PositiveFloat
    height_pt: PositiveFloat
    orientation: Orientation
    raster_file: NonEmptyString
    text_file: NonEmptyString | None = None
    media_files: list[NonEmptyString]

    @model_validator(mode="after")
    def validate_orientation(self) -> Self:
        if self.height_pt > self.width_pt:
            expected_orientation: Orientation = "portrait"
        elif self.width_pt > self.height_pt:
            expected_orientation = "landscape"
        else:
            expected_orientation = "square"
        if self.orientation != expected_orientation:
            raise ManifestInvariantError(
                reason="page orientation contradicts its dimensions"
            )
        return self


def contiguous_ranges(pages: Sequence[int]) -> list[PageRange]:
    if not pages:
        return []
    if len(set(pages)) != len(pages):
        raise PageSelectionError(reason="page numbers must be unique")

    ordered = sorted(pages)
    if ordered[0] <= 0:
        raise PageSelectionError(reason="page numbers must be positive")

    ranges: list[PageRange] = []
    start = ordered[0]
    previous = start
    for page in ordered[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(PageRange(start=start, end=previous))
        start = page
        previous = page
    ranges.append(PageRange(start=start, end=previous))
    return ranges


def resolve_page_selection(spec: str | None, source_page_count: int) -> list[int]:
    if source_page_count <= 0:
        raise PageSelectionError(reason="source page count must be positive")
    if spec is None:
        return list(range(1, source_page_count + 1))
    if not spec.strip():
        raise PageSelectionError(reason="page selection cannot be empty")

    selected: set[int] = set()
    for raw_token in spec.split(","):
        token = raw_token.strip()
        if not token:
            raise PageSelectionError(reason="page selection contains an empty token")
        token_match = _PAGE_TOKEN.fullmatch(token)
        if token_match is None:
            raise PageSelectionError(reason=f"malformed page token: {token}")

        start = int(token_match.group(1))
        end_group = token_match.group(2)
        end = start if end_group is None else int(end_group)
        if start <= 0:
            raise PageSelectionError(reason="page numbers must be positive")
        if end < start:
            raise PageSelectionError(reason=f"reversed page range: {token}")
        if end > source_page_count:
            raise PageSelectionError(reason=f"page selection is out of range: {token}")

        for page in range(start, end + 1):
            if page in selected:
                raise PageSelectionError(reason=f"duplicate page number: {page}")
            selected.add(page)
    return sorted(selected)


class ManifestV2(StrictModel):
    schema_version: Literal[2]
    source_pdf: NonEmptyString
    source_sha256: Sha256
    source_page_count: PositiveInt
    selected_page_count: PositiveInt
    selected_pages: NonEmptyPositiveInts
    dpi: PositiveInt
    render_batches: list[PageRange]
    timings: ManifestTimings
    summary: ModeSummary
    pages: list[PageRecord]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if not (
            self.selected_page_count == len(self.selected_pages) == len(self.pages)
        ):
            raise ManifestInvariantError(
                reason="selected page count, selection, and records must have equal lengths"
            )
        if len(set(self.selected_pages)) != len(self.selected_pages):
            raise ManifestInvariantError(reason="selected pages must be unique")
        if self.selected_pages != sorted(self.selected_pages):
            raise ManifestInvariantError(reason="selected pages must be sorted")
        if self.selected_pages[-1] > self.source_page_count:
            raise ManifestInvariantError(reason="selected page is outside the source")
        if [page.number for page in self.pages] != self.selected_pages:
            raise ManifestInvariantError(
                reason="page records must exactly match selected pages"
            )

        expected_summary = ModeSummary(
            editable=sum(page.mode == "editable" for page in self.pages),
            hybrid=sum(page.mode == "hybrid" for page in self.pages),
            raster=sum(page.mode == "raster" for page in self.pages),
        )
        if self.summary != expected_summary:
            raise ManifestInvariantError(reason="summary does not match page modes")
        if self.render_batches != contiguous_ranges(self.selected_pages):
            raise ManifestInvariantError(
                reason="render batches must be the maximal contiguous selected-page spans"
            )
        return self

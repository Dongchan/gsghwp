from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self, override

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from manifest_contract import ManifestV2, Orientation, PageRecord, Sha256

VerificationStatus = Literal["pass", "fail"]
ClaimScope = Literal["operational_only"]
EdgeTouch = Literal["left", "top", "right", "bottom"]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
UnitRatio = Annotated[float, Field(ge=0.0, le=1.0)]


class VerificationInvariantError(ValueError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    document: Path
    review_dir: Path
    pages: str | None = None
    dpi: int = 180
    manifest: Path | None = None
    expected_page_count: int | None = None


@dataclass(frozen=True, slots=True)
class PdfInspectionRequest:
    source_document: Path
    exported_pdf: Path
    review_dir: Path
    hwp_output_page_count: int
    pages: str | None = None
    dpi: int = 180
    manifest: Path | None = None
    expected_page_count: int | None = None


@dataclass(frozen=True, slots=True)
class LoadedManifest:
    path: Path
    sha256: str
    data: ManifestV2


@dataclass(frozen=True, slots=True)
class PdfGeometry:
    width_pt: float
    height_pt: float
    orientation: Orientation


@dataclass(frozen=True, slots=True)
class InspectionState:
    request: PdfInspectionRequest
    manifest: LoadedManifest | None
    selected_pages: tuple[int, ...]
    source_sha256: str
    validation_seconds: float
    export_seconds: float


class StrictVerificationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ContentBoundingBoxPixels(StrictVerificationModel):
    left: NonNegativeInt
    top: NonNegativeInt
    right: PositiveInt
    bottom: PositiveInt

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.right <= self.left or self.bottom <= self.top:
            raise VerificationInvariantError(
                reason="content bounding box must have positive area"
            )
        return self


class VerificationTimings(StrictVerificationModel):
    validation_seconds: NonNegativeFloat
    export_seconds: NonNegativeFloat
    pdf_read_seconds: NonNegativeFloat
    render_seconds: NonNegativeFloat
    analysis_seconds: NonNegativeFloat
    total_seconds: NonNegativeFloat


class VerificationPageRecord(StrictVerificationModel):
    output_page_number: PositiveInt
    source_page_number: PositiveInt | None = None
    rendered_image: NonEmptyString
    width_pt: PositiveFloat
    height_pt: PositiveFloat
    orientation: Orientation
    nonblank_ratio: UnitRatio
    content_bbox_pixels: ContentBoundingBoxPixels | None = None
    edge_touch_warnings: list[EdgeTouch]
    geometry_match: bool | None = None

    @field_validator("rendered_image")
    @classmethod
    def validate_rendered_image(cls, value: str) -> str:
        if Path(value).is_absolute():
            raise VerificationInvariantError(
                reason="rendered image path must be report-relative"
            )
        return value

class VerificationReport(StrictVerificationModel):
    schema_version: Literal[2]
    status: VerificationStatus
    claim_scope: ClaimScope
    source_document: NonEmptyString
    source_sha256: Sha256
    exported_pdf: NonEmptyString
    exported_pdf_sha256: Sha256
    manifest: NonEmptyString | None = None
    manifest_sha256: Sha256 | None = None
    hwp_output_page_count: PositiveInt
    expected_page_count: PositiveInt | None = None
    manifest_source_page_count: PositiveInt | None = None
    manifest_selected_page_count: PositiveInt | None = None
    selected_output_pages: list[PositiveInt]
    timings: VerificationTimings
    pages: list[VerificationPageRecord]
    errors: list[NonEmptyString]
    warnings: list[NonEmptyString]

    @field_validator("source_document", "exported_pdf", "manifest")
    @classmethod
    def validate_relative_paths(cls, value: str | None) -> str | None:
        if value is not None and Path(value).is_absolute():
            raise VerificationInvariantError(
                reason="report file paths must be report-relative"
            )
        return value

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if (self.status == "pass") == bool(self.errors):
            raise VerificationInvariantError(
                reason="verification status must agree with errors"
            )
        if [page.output_page_number for page in self.pages] != self.selected_output_pages:
            raise VerificationInvariantError(
                reason="page records must match selected output pages"
            )
        manifest_values = (
            self.manifest_sha256,
            self.manifest_source_page_count,
            self.manifest_selected_page_count,
        )
        if self.manifest is None and any(value is not None for value in manifest_values):
            raise VerificationInvariantError(
                reason="manifest metadata requires a manifest path"
            )
        if self.manifest is not None and any(value is None for value in manifest_values):
            raise VerificationInvariantError(
                reason="manifest path requires complete manifest metadata"
            )
        if self.manifest is None and any(
            page.source_page_number is not None or page.geometry_match is not None
            for page in self.pages
        ):
            raise VerificationInvariantError(
                reason="source mapping and geometry match require a manifest"
            )
        return self


@dataclass(frozen=True, slots=True)
class ReportInputs:
    state: InspectionState
    exported_pdf_sha256: str
    pdf_read_seconds: float
    render_seconds: float
    analysis_seconds: float
    records: tuple[VerificationPageRecord, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def pdf_geometry(width: float, height: float) -> PdfGeometry:
    if width > height:
        orientation: Orientation = "landscape"
    elif height > width:
        orientation = "portrait"
    else:
        orientation = "square"
    return PdfGeometry(width, height, orientation)


def geometry_matches(actual: PdfGeometry, expected: PageRecord) -> bool:
    tolerance_pt = 1.0
    return (
        abs(actual.width_pt - expected.width_pt) <= tolerance_pt
        and abs(actual.height_pt - expected.height_pt) <= tolerance_pt
        and actual.orientation == expected.orientation
    )


def _relative(path: Path, report_dir: Path) -> str:
    return Path(os.path.relpath(path, report_dir)).as_posix()


def build_report(inputs: ReportInputs) -> VerificationReport:
    state = inputs.state
    request = state.request
    manifest = state.manifest
    total_seconds = sum(
        (
            state.validation_seconds,
            state.export_seconds,
            inputs.pdf_read_seconds,
            inputs.render_seconds,
            inputs.analysis_seconds,
        )
    )
    return VerificationReport(
        schema_version=2,
        status="fail" if inputs.errors else "pass",
        claim_scope="operational_only",
        source_document=_relative(request.source_document, request.review_dir),
        source_sha256=state.source_sha256,
        exported_pdf=_relative(request.exported_pdf, request.review_dir),
        exported_pdf_sha256=inputs.exported_pdf_sha256,
        manifest=None if manifest is None else _relative(manifest.path, request.review_dir),
        manifest_sha256=None if manifest is None else manifest.sha256,
        hwp_output_page_count=request.hwp_output_page_count,
        expected_page_count=request.expected_page_count,
        manifest_source_page_count=None if manifest is None else manifest.data.source_page_count,
        manifest_selected_page_count=None if manifest is None else manifest.data.selected_page_count,
        selected_output_pages=list(state.selected_pages),
        timings=VerificationTimings(
            validation_seconds=state.validation_seconds,
            export_seconds=state.export_seconds,
            pdf_read_seconds=inputs.pdf_read_seconds,
            render_seconds=inputs.render_seconds,
            analysis_seconds=inputs.analysis_seconds,
            total_seconds=total_seconds,
        ),
        pages=list(inputs.records),
        errors=list(inputs.errors),
        warnings=list(inputs.warnings),
    )

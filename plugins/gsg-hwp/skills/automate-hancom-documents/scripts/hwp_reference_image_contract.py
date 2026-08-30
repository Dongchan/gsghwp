from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from hwp_live_values import ContractModel, Rgb
from hwp_reference_image_budget import ReferenceAnalysisStopReason


AnalysisAxis = Literal["row", "column"]
SegmentOrientation = Literal["horizontal", "vertical"]
GapOrientation = Literal["horizontal", "vertical"]
ReferenceObjectKind = Literal["filled_region", "outlined_box"]
BreakpointSource = Literal["frame", "segment", "object", "gap", "text"]


class NormalizedBox(ContractModel):
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_area(self) -> NormalizedBox:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("normalized box must have positive area")
        return self


class ReferenceImageSegment(ContractModel):
    segment_id: str = Field(pattern=r"^seg-[0-9a-f]{12}$")
    orientation: SegmentOrientation
    start_x: float = Field(ge=0, le=1)
    start_y: float = Field(ge=0, le=1)
    end_x: float = Field(ge=0, le=1)
    end_y: float = Field(ge=0, le=1)
    width_px: int = Field(ge=1)
    color: Rgb

    @model_validator(mode="after")
    def validate_direction(self) -> ReferenceImageSegment:
        if self.orientation == "horizontal":
            if self.start_x >= self.end_x or self.start_y != self.end_y:
                raise ValueError("horizontal segment coordinates are invalid")
            return self
        if self.start_y >= self.end_y or self.start_x != self.end_x:
            raise ValueError("vertical segment coordinates are invalid")
        return self


class ReferenceImageObject(ContractModel):
    region_id: str = Field(pattern=r"^obj-[0-9a-f]{12}$")
    kind: ReferenceObjectKind
    bbox: NormalizedBox
    fill: Rgb | None = None
    edges: tuple[Literal["top", "right", "bottom", "left"], ...] = ()


class ReferenceTextRegion(ContractModel):
    region_id: str = Field(pattern=r"^txt-[0-9a-f]{12}$")
    bbox: NormalizedBox
    rotation_degrees: int = Field(default=0, ge=-180, le=180)
    line_count: int = Field(default=1, ge=1)
    crop_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    crop_path: Path


class ReferenceProtectedGap(ContractModel):
    gap_id: str = Field(pattern=r"^gap-[0-9a-f]{12}$")
    orientation: GapOrientation
    bbox: NormalizedBox
    minimum_size_px: int = Field(ge=1)
    bounded_by: tuple[str, str]


class ReferenceBreakpointCandidate(ContractModel):
    axis: AnalysisAxis
    position: float = Field(ge=0, le=1)
    source: BreakpointSource
    source_id: str


class ReferenceImageTile(ContractModel):
    tile_id: str = Field(pattern=r"^tile-[0-9]{4}$")
    bbox: NormalizedBox
    pixel_width: int = Field(ge=1)
    pixel_height: int = Field(ge=1)
    image_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: Path


class ReferenceImageAnalysis(ContractModel):
    analysis_id: str = Field(pattern=r"^ria-[0-9a-f]{16}$")
    analyzer_version: str
    image_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_image: Path
    image_width: int = Field(ge=2)
    image_height: int = Field(ge=2)
    source_bytes: int = Field(default=0, ge=0)
    analysis_width: int = Field(default=2, ge=2)
    analysis_height: int = Field(default=2, ge=2)
    analysis_downsampled: bool = False
    resource_fingerprint: str = Field(
        default="0000000000000000",
        pattern=r"^[0-9a-f]{16}$",
    )
    analysis_time_ms: float = Field(ge=0)
    cache_hit: bool
    # 조기 종료는 오류가 아니라 정직한 구조화 응답이다. 미완 결과는 캐시에
    # 쓰지 않으므로 analysis_id 로 다시 집을 수 없고, 좌표 증거로 쓰여서도
    # 안 된다 — 소비자는 이 값을 보고 모델 직접 판독으로 넘어가면 된다.
    analysis_complete: bool = True
    stop_reason: ReferenceAnalysisStopReason | None = None
    predicted_seconds: float | None = Field(default=None, ge=0)
    time_budget_seconds: float | None = Field(default=None, gt=0)
    objects: tuple[ReferenceImageObject, ...]
    text_regions: tuple[ReferenceTextRegion, ...]
    protected_gaps: tuple[ReferenceProtectedGap, ...]
    visible_segments: tuple[ReferenceImageSegment, ...]
    breakpoint_candidates: tuple[ReferenceBreakpointCandidate, ...]
    tiles: tuple[ReferenceImageTile, ...]
    contact_sheet_paths: tuple[Path, ...]
    overlay_path: Path

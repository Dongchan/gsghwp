from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Literal, cast, final

from pydantic import Field, JsonValue

from hwp_live_values import ContractModel, Rgb
from hwp_reference_image_analyzer import default_artifact_root
from hwp_reference_image_contract import (
    NormalizedBox,
    ReferenceImageAnalysis,
    ReferenceImageObject,
    ReferenceImageSegment,
)
from hwp_reference_image_layout_bridge import align_reference_layout_to_analysis
from hwp_reference_layout_contract import (
    ReferenceLayoutBlock,
    ReferenceStyle,
    StyleRegion,
    VisibleEdge,
)


ReferenceExecutionMode = Literal["ordinary_layout", "reference_layout_bulk"]
ReferenceDetailSection = Literal[
    "draft_reference_layout",
    "objects",
    "text_regions",
    "protected_gaps",
    "visible_segments",
    "breakpoint_candidates",
    "tiles",
    "artifacts",
]
ReferenceArtifactKind = Literal[
    "analysis",
    "overlay",
    "contact_sheet",
    "tile",
    "text_crop",
]


class ReferenceAnalysisCounts(ContractModel):
    objects: int = Field(ge=0)
    text_regions: int = Field(ge=0)
    protected_gaps: int = Field(ge=0)
    visible_segments: int = Field(ge=0)
    breakpoint_candidates: int = Field(ge=0)
    tiles: int = Field(ge=0)
    contact_sheets: int = Field(ge=0)


class ReferenceStructureSummary(ContractModel):
    horizontal_segments: int = Field(ge=0)
    vertical_segments: int = Field(ge=0)
    orthogonal_intersections: int = Field(ge=0)
    dominant_segment_ratio: float = Field(ge=0, le=1)
    dominant_grid_area_ratio: float = Field(ge=0, le=1)
    partial_edge_ratio: float = Field(ge=0, le=1)
    repeated_style_regions: int = Field(ge=0)
    grid_fixed_text_regions: int = Field(ge=0)


class ReferenceArtifact(ContractModel):
    kind: ReferenceArtifactKind
    path: Path
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReferenceCompactOmission(ContractModel):
    section: ReferenceDetailSection
    omitted_count: int = Field(ge=1)
    reason: Literal["utf8_byte_budget"] = "utf8_byte_budget"


class CompactReferenceImageAnalysis(ContractModel):
    analysis_id: str = Field(pattern=r"^ria-[0-9a-f]{16}$")
    analyzer_version: str
    source_image: Path | None
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_size_bytes: int = Field(ge=0)
    image_width: int = Field(ge=2)
    image_height: int = Field(ge=2)
    analysis_time_ms: float = Field(ge=0)
    cache_hit: bool
    counts: ReferenceAnalysisCounts
    structure: ReferenceStructureSummary
    recommended_execution_mode: ReferenceExecutionMode
    reason_codes: tuple[str, ...]
    row_breakpoints: tuple[float, ...] = Field(min_length=2, max_length=51)
    column_breakpoints: tuple[float, ...] = Field(min_length=2, max_length=51)
    draft_reference_layout: ReferenceLayoutBlock | None = None
    detail_sections: tuple[ReferenceDetailSection, ...]
    analysis_result_path: Path | None
    artifacts: tuple[ReferenceArtifact, ...]
    response_budget_bytes: int = Field(default=100_000, ge=1)
    response_size_bytes: int = Field(default=0, ge=0)
    omissions: tuple[ReferenceCompactOmission, ...] = ()


class ReferenceAnalysisDetail(ContractModel):
    analysis_id: str = Field(pattern=r"^ria-[0-9a-f]{16}$")
    section: ReferenceDetailSection
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    items: tuple[JsonValue, ...]


def _with_response_size(
    result: CompactReferenceImageAnalysis,
) -> CompactReferenceImageAnalysis:
    sized = result
    for _ in range(4):
        encoded_size = len(sized.model_dump_json().encode("utf-8"))
        if sized.response_size_bytes == encoded_size:
            return sized
        sized = sized.model_copy(update={"response_size_bytes": encoded_size})
    return sized


def enforce_compact_response_budget(
    result: CompactReferenceImageAnalysis,
) -> CompactReferenceImageAnalysis:
    bounded = _with_response_size(result)
    if bounded.response_size_bytes <= bounded.response_budget_bytes:
        return bounded
    omissions = list(bounded.omissions)
    draft = bounded.draft_reference_layout
    if draft is not None:
        omissions.append(
            ReferenceCompactOmission(
                section="draft_reference_layout",
                omitted_count=max(
                    1,
                    len(draft.visible_edges)
                    + len(draft.styles)
                    + len(draft.style_regions)
                    + len(draft.text_anchors),
                ),
            )
        )
        bounded = _with_response_size(
            bounded.model_copy(
                update={
                    "draft_reference_layout": None,
                    "omissions": tuple(omissions),
                }
            )
        )
    if (
        bounded.response_size_bytes > bounded.response_budget_bytes
        and bounded.artifacts
    ):
        omissions.append(
            ReferenceCompactOmission(
                section="artifacts",
                omitted_count=len(bounded.artifacts),
            )
        )
        bounded = _with_response_size(
            bounded.model_copy(
                update={
                    "artifacts": (),
                    "omissions": tuple(omissions),
                }
            )
        )
    if (
        bounded.response_size_bytes > bounded.response_budget_bytes
        and (
            bounded.source_image is not None
            or bounded.analysis_result_path is not None
        )
    ):
        omissions.append(
            ReferenceCompactOmission(
                section="artifacts",
                omitted_count=sum(
                    path is not None
                    for path in (
                        bounded.source_image,
                        bounded.analysis_result_path,
                    )
                ),
            )
        )
        bounded = _with_response_size(
            bounded.model_copy(
                update={
                    "source_image": None,
                    "analysis_result_path": None,
                    "omissions": tuple(omissions),
                }
            )
        )
    if bounded.response_size_bytes > bounded.response_budget_bytes:
        raise ValueError("compact reference image response exceeds UTF-8 byte budget")
    return bounded


@final
class _SegmentComponents:
    segments: tuple[ReferenceImageSegment, ...]
    parents: list[int]

    def __init__(self, segments: tuple[ReferenceImageSegment, ...]) -> None:
        self.segments = segments
        self.parents = list(range(len(segments)))

    def find(self, index: int) -> int:
        parent = self.parents[index]
        while parent != self.parents[parent]:
            parent = self.parents[parent]
        while index != parent:
            next_index = self.parents[index]
            self.parents[index] = parent
            index = next_index
        return parent

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root

    def groups(self) -> tuple[tuple[ReferenceImageSegment, ...], ...]:
        grouped: dict[int, list[ReferenceImageSegment]] = {}
        for index, segment in enumerate(self.segments):
            grouped.setdefault(self.find(index), []).append(segment)
        return tuple(tuple(items) for items in grouped.values())


def _segment_length(segment: ReferenceImageSegment) -> float:
    return (
        segment.end_x - segment.start_x
        if segment.orientation == "horizontal"
        else segment.end_y - segment.start_y
    )


def _segments_touch(
    left: ReferenceImageSegment,
    right: ReferenceImageSegment,
    tolerance: float,
) -> bool:
    if left.orientation == right.orientation == "horizontal":
        return (
            abs(left.start_y - right.start_y) <= tolerance
            and left.start_x <= right.end_x + tolerance
            and right.start_x <= left.end_x + tolerance
        )
    if left.orientation == right.orientation == "vertical":
        return (
            abs(left.start_x - right.start_x) <= tolerance
            and left.start_y <= right.end_y + tolerance
            and right.start_y <= left.end_y + tolerance
        )
    horizontal, vertical = (
        (left, right) if left.orientation == "horizontal" else (right, left)
    )
    return (
        horizontal.start_x - tolerance
        <= vertical.start_x
        <= horizontal.end_x + tolerance
        and vertical.start_y - tolerance
        <= horizontal.start_y
        <= vertical.end_y + tolerance
    )


def _connected_components(
    analysis: ReferenceImageAnalysis,
) -> tuple[tuple[ReferenceImageSegment, ...], ...]:
    selected = tuple(
        sorted(
            analysis.visible_segments,
            key=_segment_length,
            reverse=True,
        )[:600]
    )
    components = _SegmentComponents(selected)
    tolerance = max(2 / analysis.image_width, 2 / analysis.image_height, 0.001)
    for left in range(len(selected)):
        for right in range(left + 1, len(selected)):
            if _segments_touch(selected[left], selected[right], tolerance):
                components.union(left, right)
    return components.groups()


def _component_box(
    segments: tuple[ReferenceImageSegment, ...],
) -> NormalizedBox:
    left = min(segment.start_x for segment in segments)
    top = min(segment.start_y for segment in segments)
    right = max(segment.end_x for segment in segments)
    bottom = max(segment.end_y for segment in segments)
    if left >= right:
        left = max(0.0, left - 0.0005)
        right = min(1.0, right + 0.0005)
    if top >= bottom:
        top = max(0.0, top - 0.0005)
        bottom = min(1.0, bottom + 0.0005)
    return NormalizedBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
    )


def _contains(box: NormalizedBox, left: float, top: float) -> bool:
    return box.left <= left <= box.right and box.top <= top <= box.bottom


def _intersection_count(
    segments: tuple[ReferenceImageSegment, ...],
    tolerance: float,
) -> int:
    horizontal = tuple(
        segment for segment in segments if segment.orientation == "horizontal"
    )
    vertical = tuple(
        segment for segment in segments if segment.orientation == "vertical"
    )
    return sum(
        _segments_touch(left, right, tolerance)
        for left in horizontal
        for right in vertical
    )


def _repeated_style_regions(
    analysis: ReferenceImageAnalysis,
    box: NormalizedBox,
) -> int:
    fills = Counter(
        item.fill
        for item in analysis.objects
        if item.fill is not None
        and _contains(
            box,
            (item.bbox.left + item.bbox.right) / 2,
            (item.bbox.top + item.bbox.bottom) / 2,
        )
    )
    return sum(count for count in fills.values() if count > 1)


def _dominant_component(
    analysis: ReferenceImageAnalysis,
) -> tuple[
    tuple[ReferenceImageSegment, ...],
    NormalizedBox,
    ReferenceStructureSummary,
]:
    components = _connected_components(analysis)
    if not components:
        empty_box = NormalizedBox(left=0, top=0, right=1, bottom=1)
        return (
            (),
            empty_box,
            ReferenceStructureSummary(
                horizontal_segments=0,
                vertical_segments=0,
                orthogonal_intersections=0,
                dominant_segment_ratio=0,
                dominant_grid_area_ratio=0,
                partial_edge_ratio=0,
                repeated_style_regions=0,
                grid_fixed_text_regions=0,
            ),
        )
    tolerance = max(2 / analysis.image_width, 2 / analysis.image_height, 0.001)
    ranked: list[
        tuple[
            int,
            int,
            float,
            tuple[ReferenceImageSegment, ...],
            NormalizedBox,
        ]
    ] = []
    for component in components:
        intersections = _intersection_count(component, tolerance)
        box = _component_box(component)
        area = (box.right - box.left) * (box.bottom - box.top)
        ranked.append((intersections, len(component), area, component, box))
    _, _, _, dominant, box = max(ranked, key=lambda item: item[:3])
    horizontal = tuple(
        segment for segment in dominant if segment.orientation == "horizontal"
    )
    vertical = tuple(
        segment for segment in dominant if segment.orientation == "vertical"
    )
    intersections = _intersection_count(dominant, tolerance)
    box_width = max(box.right - box.left, tolerance)
    box_height = max(box.bottom - box.top, tolerance)
    partial = sum(
        _segment_length(segment)
        < (box_width if segment.orientation == "horizontal" else box_height) * 0.8
        for segment in dominant
    )
    text_regions = sum(
        _contains(
            box,
            (item.bbox.left + item.bbox.right) / 2,
            (item.bbox.top + item.bbox.bottom) / 2,
        )
        for item in analysis.text_regions
    )
    total = max(1, min(len(analysis.visible_segments), 600))
    summary = ReferenceStructureSummary(
        horizontal_segments=len(horizontal),
        vertical_segments=len(vertical),
        orthogonal_intersections=intersections,
        dominant_segment_ratio=round(len(dominant) / total, 4),
        dominant_grid_area_ratio=round(box_width * box_height, 4),
        partial_edge_ratio=round(partial / max(1, len(dominant)), 4),
        repeated_style_regions=_repeated_style_regions(analysis, box),
        grid_fixed_text_regions=text_regions,
    )
    return dominant, box, summary


def _cluster_coordinates(
    values: list[float],
    *,
    tolerance: float,
) -> tuple[float, ...]:
    ordered = sorted(min(1.0, max(0.0, value)) for value in values)
    groups: list[list[float]] = []
    for value in ordered:
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    clustered = [round(sum(group) / len(group), 6) for group in groups]
    if clustered[0] != 0:
        clustered.insert(0, 0.0)
    else:
        clustered[0] = 0.0
    if clustered[-1] != 1:
        clustered.append(1.0)
    else:
        clustered[-1] = 1.0
    unique = list(dict.fromkeys(clustered))
    if len(unique) <= 51:
        return tuple(unique)
    selected = {round(index * (len(unique) - 1) / 50) for index in range(51)}
    return tuple(unique[index] for index in sorted(selected))


def _breakpoints(
    analysis: ReferenceImageAnalysis,
    segments: tuple[ReferenceImageSegment, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    rows = [0.0, 1.0]
    columns = [0.0, 1.0]
    for segment in segments:
        if segment.orientation == "horizontal":
            rows.append(segment.start_y)
            columns.extend((segment.start_x, segment.end_x))
        else:
            columns.append(segment.start_x)
            rows.extend((segment.start_y, segment.end_y))
    row_tolerance = max(2 / analysis.image_height, 0.001)
    column_tolerance = max(2 / analysis.image_width, 0.001)
    return (
        _cluster_coordinates(rows, tolerance=row_tolerance),
        _cluster_coordinates(columns, tolerance=column_tolerance),
    )


def _nearest(values: tuple[float, ...], coordinate: float) -> int:
    return min(range(len(values)), key=lambda index: abs(values[index] - coordinate))


def _edge_width(
    width_px: int,
) -> Literal[
    "0.12mm",
    "0.2mm",
    "0.3mm",
    "0.5mm",
    "1.0mm",
]:
    if width_px <= 1:
        return "0.12mm"
    if width_px <= 2:
        return "0.2mm"
    if width_px <= 4:
        return "0.3mm"
    if width_px <= 7:
        return "0.5mm"
    return "1.0mm"


def _visible_edges(
    segments: tuple[ReferenceImageSegment, ...],
    rows: tuple[float, ...],
    columns: tuple[float, ...],
) -> tuple[VisibleEdge, ...]:
    edges: dict[tuple[str, int, int, int, Rgb, str], VisibleEdge] = {}
    for segment in segments:
        width = _edge_width(segment.width_px)
        if segment.orientation == "horizontal":
            line = _nearest(rows, segment.start_y)
            start = _nearest(columns, segment.start_x)
            end = _nearest(columns, segment.end_x)
        else:
            line = _nearest(columns, segment.start_x)
            start = _nearest(rows, segment.start_y)
            end = _nearest(rows, segment.end_y)
        if start >= end:
            continue
        key = (segment.orientation, line, start, end, segment.color, width)
        edges[key] = VisibleEdge(
            orientation=segment.orientation,
            line=line,
            start=start,
            end=end,
            width=width,
            color=segment.color,
        )
    return tuple(edges.values())


def _object_styles(
    objects: tuple[ReferenceImageObject, ...],
    rows: tuple[float, ...],
    columns: tuple[float, ...],
    image_width: int,
    image_height: int,
) -> tuple[tuple[ReferenceStyle, ...], tuple[StyleRegion, ...]]:
    colors: tuple[Rgb, ...] = tuple(
        dict.fromkeys(item.fill for item in objects if item.fill is not None)
    )[:64]
    styles = tuple(
        ReferenceStyle(
            key=f"fill_{color[0]:02x}{color[1]:02x}{color[2]:02x}",
            fill_color=color,
        )
        for color in colors
    )
    keys = {
        style.fill_color: style.key for style in styles if style.fill_color is not None
    }
    row_tolerance = max(3 / image_height, 0.008)
    column_tolerance = max(3 / image_width, 0.008)
    regions: list[StyleRegion] = []
    for item in objects:
        if item.fill is None or item.fill not in keys:
            continue
        top = _nearest(rows, item.bbox.top)
        bottom = _nearest(rows, item.bbox.bottom)
        left = _nearest(columns, item.bbox.left)
        right = _nearest(columns, item.bbox.right)
        if (
            top >= bottom
            or left >= right
            or abs(rows[top] - item.bbox.top) > row_tolerance
            or abs(rows[bottom] - item.bbox.bottom) > row_tolerance
            or abs(columns[left] - item.bbox.left) > column_tolerance
            or abs(columns[right] - item.bbox.right) > column_tolerance
        ):
            continue
        regions.append(
            StyleRegion(
                top=top,
                left=left,
                bottom=bottom,
                right=right,
                style_key=keys[item.fill],
            )
        )
    used = {region.style_key for region in regions}
    return (
        tuple(style for style in styles if style.key in used),
        tuple(regions[:1000]),
    )


def _draft_layout(
    analysis: ReferenceImageAnalysis,
    segments: tuple[ReferenceImageSegment, ...],
    rows: tuple[float, ...],
    columns: tuple[float, ...],
) -> ReferenceLayoutBlock:
    styles, regions = _object_styles(
        analysis.objects,
        rows,
        columns,
        analysis.image_width,
        analysis.image_height,
    )
    block = ReferenceLayoutBlock(
        kind="reference_layout",
        source_image=analysis.source_image,
        analysis_id=analysis.analysis_id,
        row_breakpoints=rows,
        column_breakpoints=columns,
        visible_edges=_visible_edges(segments, rows, columns),
        styles=styles,
        style_regions=regions,
    )
    return align_reference_layout_to_analysis(block, analysis)


def _artifact(path: Path, kind: ReferenceArtifactKind) -> ReferenceArtifact | None:
    if not path.is_file():
        return None
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return ReferenceArtifact(
        kind=kind,
        path=path,
        size_bytes=path.stat().st_size,
        sha256=checksum.hexdigest(),
    )


def _compact_artifacts(
    analysis: ReferenceImageAnalysis,
) -> tuple[ReferenceArtifact, ...]:
    result_path = analysis.overlay_path.parent / "result.json"
    candidates: list[tuple[Path, ReferenceArtifactKind]] = [
        (result_path, "analysis"),
        (analysis.overlay_path, "overlay"),
    ]
    candidates.extend((path, "contact_sheet") for path in analysis.contact_sheet_paths)
    return tuple(
        artifact
        for path, kind in candidates
        if (artifact := _artifact(path, kind)) is not None
    )


def _build_compact_reference_image_analysis(
    analysis: ReferenceImageAnalysis,
) -> CompactReferenceImageAnalysis:
    dominant, _, structure = _dominant_component(analysis)
    rows, columns = _breakpoints(analysis, dominant)
    coherent = (
        structure.horizontal_segments >= 3
        and structure.vertical_segments >= 3
        and structure.orthogonal_intersections
        >= max(
            4,
            min(
                structure.horizontal_segments,
                structure.vertical_segments,
            ),
        )
    )
    dominant_grid = (
        coherent
        and structure.dominant_segment_ratio >= 0.55
        and structure.dominant_grid_area_ratio >= 0.35
        and (
            structure.horizontal_segments + structure.vertical_segments >= 10
            or structure.partial_edge_ratio >= 0.2
            or structure.repeated_style_regions >= 2
            or structure.grid_fixed_text_regions >= 6
        )
    )
    reasons: list[str] = []
    if dominant_grid:
        reasons.append("DOMINANT_ORTHOGONAL_GRID")
        if structure.partial_edge_ratio >= 0.2:
            reasons.append("DENSE_PARTIAL_EDGES")
        if structure.repeated_style_regions >= 2:
            reasons.append("REPEATED_STYLE_REGIONS")
        if structure.grid_fixed_text_regions >= 6:
            reasons.append("GRID_FIXED_TEXT_REGIONS")
    else:
        if structure.dominant_grid_area_ratio < 0.35:
            reasons.append("GRID_DOES_NOT_DOMINATE_PAGE")
        if analysis.objects or analysis.text_regions:
            reasons.append("INDEPENDENT_PAGE_REGIONS")
        if not coherent:
            reasons.append("LOW_ORTHOGONAL_CONNECTIVITY")
    mode: ReferenceExecutionMode = (
        "reference_layout_bulk" if dominant_grid else "ordinary_layout"
    )
    draft = _draft_layout(analysis, dominant, rows, columns) if dominant_grid else None
    result_path = analysis.overlay_path.parent / "result.json"
    return CompactReferenceImageAnalysis(
        analysis_id=analysis.analysis_id,
        analyzer_version=analysis.analyzer_version,
        source_image=analysis.source_image,
        source_hash=analysis.image_hash,
        source_size_bytes=analysis.source_image.stat().st_size,
        image_width=analysis.image_width,
        image_height=analysis.image_height,
        analysis_time_ms=analysis.analysis_time_ms,
        cache_hit=analysis.cache_hit,
        counts=ReferenceAnalysisCounts(
            objects=len(analysis.objects),
            text_regions=len(analysis.text_regions),
            protected_gaps=len(analysis.protected_gaps),
            visible_segments=len(analysis.visible_segments),
            breakpoint_candidates=len(analysis.breakpoint_candidates),
            tiles=len(analysis.tiles),
            contact_sheets=len(analysis.contact_sheet_paths),
        ),
        structure=structure,
        recommended_execution_mode=mode,
        reason_codes=tuple(reasons),
        row_breakpoints=rows,
        column_breakpoints=columns,
        draft_reference_layout=draft,
        detail_sections=(
            "draft_reference_layout",
            "objects",
            "text_regions",
            "protected_gaps",
            "visible_segments",
            "breakpoint_candidates",
            "tiles",
            "artifacts",
        ),
        analysis_result_path=result_path,
        artifacts=_compact_artifacts(analysis),
    )


def compact_reference_image_analysis(
    analysis: ReferenceImageAnalysis,
) -> CompactReferenceImageAnalysis:
    return enforce_compact_response_budget(
        _build_compact_reference_image_analysis(analysis)
    )


def _load_analysis_by_id(
    analysis_id: str,
    artifact_root: Path | None,
) -> ReferenceImageAnalysis:
    if re.fullmatch(r"ria-[0-9a-f]{16}", analysis_id) is None:
        raise ValueError("analysis_id is invalid")
    root = (artifact_root or default_artifact_root()).resolve()
    result_path = (root / analysis_id / "result.json").resolve()
    if result_path.parent.parent != root or not result_path.is_file():
        raise ValueError("reference image analysis is not cached")
    analysis = ReferenceImageAnalysis.model_validate_json(
        result_path.read_text(encoding="utf-8")
    )
    if analysis.analysis_id != analysis_id:
        raise ValueError("reference image analysis cache identity is invalid")
    return analysis.model_copy(update={"cache_hit": True})


def _all_artifacts(
    analysis: ReferenceImageAnalysis,
) -> tuple[ReferenceArtifact, ...]:
    candidates: list[tuple[Path, ReferenceArtifactKind]] = [
        (analysis.overlay_path.parent / "result.json", "analysis"),
        (analysis.overlay_path, "overlay"),
    ]
    candidates.extend((path, "contact_sheet") for path in analysis.contact_sheet_paths)
    candidates.extend((item.path, "tile") for item in analysis.tiles)
    candidates.extend((item.crop_path, "text_crop") for item in analysis.text_regions)
    return tuple(
        artifact
        for path, kind in candidates
        if (artifact := _artifact(path, kind)) is not None
    )


def _detail_models(
    analysis: ReferenceImageAnalysis,
    section: ReferenceDetailSection,
) -> tuple[ContractModel, ...]:
    if section == "objects":
        return analysis.objects
    if section == "text_regions":
        return analysis.text_regions
    if section == "protected_gaps":
        return analysis.protected_gaps
    if section == "visible_segments":
        return analysis.visible_segments
    if section == "breakpoint_candidates":
        return analysis.breakpoint_candidates
    if section == "tiles":
        return analysis.tiles
    raise ValueError("artifacts are not contract-model detail items")


def reference_image_analysis_section(
    analysis_id: str,
    section: ReferenceDetailSection,
    *,
    artifact_root: Path | None = None,
    offset: int = 0,
    limit: int = 100,
) -> ReferenceAnalysisDetail:
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    analysis = _load_analysis_by_id(analysis_id, artifact_root)
    if section == "draft_reference_layout":
        draft = _build_compact_reference_image_analysis(
            analysis
        ).draft_reference_layout
        values = (
            ()
            if draft is None
            else (cast(JsonValue, draft.model_dump(mode="json")),)
        )
    elif section == "artifacts":
        values = tuple(
            cast(JsonValue, item.model_dump(mode="json"))
            for item in _all_artifacts(analysis)
        )
    else:
        values = tuple(
            cast(JsonValue, item.model_dump(mode="json"))
            for item in _detail_models(analysis, section)
        )
    items = values[offset : offset + limit]
    next_offset = offset + len(items)
    return ReferenceAnalysisDetail(
        analysis_id=analysis_id,
        section=section,
        offset=offset,
        limit=limit,
        total=len(values),
        next_offset=next_offset if next_offset < len(values) else None,
        items=items,
    )

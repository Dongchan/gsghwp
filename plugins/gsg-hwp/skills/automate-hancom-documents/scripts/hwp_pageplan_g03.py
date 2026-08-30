from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from threading import RLock
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from hwp_live_contract import PageSetup
from hwp_pageplan_assets import SourceRegistry
from hwp_pageplan_contract import (
    ImagePlanBlock,
    MmBox,
    PagePlan,
    PagePlanBlock,
    PagePlanPage,
    build_page_plan,
    canonical_json_bytes,
    canonical_page_plan_sha256,
    sha256_bytes,
    validate_page_plan_for_compiler,
)
from hwp_pageplan_g03_contract import (
    G03Accepted,
    G03Branch,
    G03AnalysisReceipt,
    G03BlockBoxMapping,
    G03BranchReceipt,
    G03CompileRequest,
    G03CompileResponse,
    G03Error,
    G03ErrorCode,
    G03GroundingProvenance,
    GeneratedG03Request,
    G03GuideMaskReceipt,
    G03MappingInput,
    G03MappingReceipt,
    G03ReferencePage,
    G03Rejected,
    G03SnapReceipt,
    StructuredG03Request,
    accepted_hash_payload,
    canonical_accepted_evidence_sha256,
    canonical_compile_request_sha256,
    canonical_mapping_sha256,
    grounding_matches_observation,
    rejected_hash_payload,
)
from hwp_reference_image_analyzer import (
    ANALYZER_VERSION,
    analyze_reference_image,
    load_cached_reference_image_analysis,
)
from hwp_reference_image_cache import DEFAULT_REFERENCE_IMAGE_LIMITS
from hwp_reference_image_contract import NormalizedBox, ReferenceImageAnalysis
from hwp_reference_image_layout_bridge import (
    snap_reference_breakpoints as _snap_breakpoints,
)
from hwp_reference_image_guide_mask import mask_reference_guides
from hwp_reference_layout_geometry import (
    HWPUNITS_PER_INCH,
    MILLIMETERS_PER_INCH,
    map_normalized_breakpoints,
    mm_to_hwpunit,
)


_ANALYSIS_ID_LENGTH = 16
_EPSILON = 1e-6
_MAX_ROUND_TRIP_ERROR_PX = 1.0
_REPLAY_EVIDENCE_SEALS: dict[tuple[str, int], str] = {}
_REPLAY_EVIDENCE_SEALS_LOCK = RLock()
# 강등 페이지의 재생 봉인 값. 해시가 아니라 읽히는 표인 이유는 봉인 불일치
# 오류의 expected/actual 에 그대로 실려서, 무엇이 달라졌는지를 사람이 바로
# 읽을 수 있어야 하기 때문이다.
_DEGRADED_EVIDENCE_SEAL = "degraded-no-analysis-evidence"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hwp_to_mm(value: int) -> float:
    return value * MILLIMETERS_PER_INCH / HWPUNITS_PER_INCH


def _oriented_paper_dimensions(page_setup: PageSetup) -> tuple[float, float]:
    width = page_setup.paper_width_mm
    height = page_setup.paper_height_mm
    if page_setup.landscape and width < height:
        width, height = height, width
    if not page_setup.landscape and width > height:
        width, height = height, width
    return width, height


def _error(
    code: G03ErrorCode,
    message: str,
    *,
    page: int | None = None,
    block_id: str | None = None,
    slot_id: int | None = None,
    box_id: str | None = None,
    source_ref: str | None = None,
    analysis_id: str | None = None,
    expected: object = None,
    actual: object = None,
) -> G03Error:
    return G03Error(
        code=code,
        message=message,
        page=page,
        block_id=block_id,
        slot_id=slot_id,
        box_id=box_id,
        source_ref=source_ref,
        analysis_id=analysis_id,
        expected=cast(JsonValue | None, expected),
        actual=cast(JsonValue | None, actual),
    )


def _response_size(result: G03Accepted | G03Rejected) -> int:
    return len(result.model_dump_json().encode("utf-8"))


def _expected_analysis_id(image_hash: str) -> str:
    analysis_key = hashlib.sha256(
        (
            f"{ANALYZER_VERSION}|{image_hash}|"
            f"{DEFAULT_REFERENCE_IMAGE_LIMITS.analysis_fingerprint}"
        ).encode("ascii")
    ).hexdigest()
    return f"ria-{analysis_key[:_ANALYSIS_ID_LENGTH]}"


def _canonical_analysis_result_sha256(
    analysis: ReferenceImageAnalysis,
) -> str:
    payload = cast(
        dict[str, JsonValue],
        analysis.model_dump(mode="json", by_alias=True),
    )
    for key in (
        "source_image",
        "analysis_time_ms",
        "cache_hit",
        "contact_sheet_paths",
        "overlay_path",
    ):
        _ = payload.pop(key, None)
    for key in ("text_regions", "tiles"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for value in cast(list[JsonValue], values):
            if isinstance(value, dict):
                value_dict = cast(dict[str, JsonValue], value)
                _ = value_dict.pop("crop_path", None)
                _ = value_dict.pop("path", None)
    return sha256_bytes(canonical_json_bytes(payload))


def _replay_evidence_error(
    input_sha256: str,
    page: int,
    evidence_sha256: str,
) -> G03Error | None:
    key = (input_sha256, page)
    with _REPLAY_EVIDENCE_SEALS_LOCK:
        expected = _REPLAY_EVIDENCE_SEALS.get(key)
        if expected is None:
            _REPLAY_EVIDENCE_SEALS[key] = evidence_sha256
            return None
    if expected == evidence_sha256:
        return None
    return _error(
        "ANALYSIS_RESULT_SEAL_MISMATCH",
        "reference analysis evidence changed for the same canonical request",
        page=page,
        expected=expected,
        actual=evidence_sha256,
    )


def _rejected(branch: G03Branch, error: G03Error) -> G03Rejected:
    draft = G03Rejected(
        branch=branch,
        error=error,
        deterministic_result_sha256="0" * 64,
        response_size_bytes=0,
    )
    result_hash = hashlib.sha256(
        canonical_json_bytes(rejected_hash_payload(draft))
    ).hexdigest()
    result = draft.model_copy(update={"deterministic_result_sha256": result_hash})
    return result.model_copy(update={"response_size_bytes": _response_size(result)})


def _accepted(
    *,
    branch: G03Branch,
    plan: PagePlan,
    source_registry: SourceRegistry,
    grounding: G03GroundingProvenance,
    branch_receipt: G03BranchReceipt,
    guide_masks: tuple[G03GuideMaskReceipt, ...],
    analyses: tuple[G03AnalysisReceipt, ...],
    snaps: tuple[G03SnapReceipt, ...],
    mapping_receipt: G03MappingReceipt,
    canonical_input_sha256: str,
) -> G03Accepted:
    draft = G03Accepted(
        branch=branch,
        plan=plan,
        source_manifest_sha256=source_registry.manifest_sha256,
        style_roles_sha256=source_registry.style_roles_sha256,
        grounding=grounding,
        branch_receipt=branch_receipt,
        guide_masks=guide_masks,
        analyses=analyses,
        snaps=snaps,
        mapping_receipt=mapping_receipt,
        canonical_input_sha256=canonical_input_sha256,
        canonical_evidence_sha256="0" * 64,
        deterministic_result_sha256="0" * 64,
        response_size_bytes=0,
    )
    evidence_hash = canonical_accepted_evidence_sha256(draft)
    sealed = draft.model_copy(update={"canonical_evidence_sha256": evidence_hash})
    result_hash = hashlib.sha256(
        canonical_json_bytes(accepted_hash_payload(sealed))
    ).hexdigest()
    result = sealed.model_copy(update={"deterministic_result_sha256": result_hash})
    return result.model_copy(update={"response_size_bytes": _response_size(result)})


def _branch_name(request: object) -> G03Branch:
    raw_branch: object | None = getattr(request, "branch", None)
    if raw_branch is None and isinstance(request, dict):
        raw_branch = cast(dict[str, object], request).get("branch")
    if raw_branch == "structured":
        return "structured"
    if raw_branch == "generated":
        return "generated"
    if raw_branch == "reference_image":
        return "reference_image"
    return "structured"


def _revalidate_structured(request: StructuredG03Request) -> StructuredG03Request:
    return StructuredG03Request.model_validate(
        request.model_dump(mode="json", by_alias=True)
    )


def _revalidate_generated(request: GeneratedG03Request) -> GeneratedG03Request:
    return GeneratedG03Request.model_validate(
        request.model_dump(mode="json", by_alias=True)
    )


def _revalidate_plan_sources(
    page_plan: PagePlan,
    source_registry: SourceRegistry,
) -> tuple[PagePlan, SourceRegistry]:
    plan = PagePlan.model_validate(page_plan.model_dump(mode="json", by_alias=True))
    registry = SourceRegistry.model_validate(
        source_registry.model_dump(mode="json", by_alias=True)
    )
    _ = validate_page_plan_for_compiler(plan, registry)
    return plan, registry


def _validate_grounding(
    page_plan: PagePlan,
    grounding: G03GroundingProvenance,
) -> G03Error | None:
    if not grounding_matches_observation(grounding, page_plan.g01_observation):
        return _error(
            "GROUNDING_PROVENANCE_MISMATCH",
            "G01 grounding provenance does not match the PagePlan observation",
            expected=page_plan.g01_observation.model_dump(mode="json"),
            actual=grounding.model_dump(mode="json"),
        )
    return None


def _structured_mapping_receipt(plan: PagePlan) -> G03MappingReceipt:
    blocks = tuple(
        block
        for page in plan.pages
        for block in page.blocks
        if not block.kind == "page_break"
    )
    images = tuple(block for block in blocks if isinstance(block, ImagePlanBlock))
    payload = {
        "candidate": plan.candidate,
        "blocks": [
            {
                "page": page.page,
                "block_id": block.block_id,
                "slot_id": block.slot_id if isinstance(block, ImagePlanBlock) else None,
            }
            for page in plan.pages
            for block in page.blocks
            if block.kind != "page_break"
        ],
    }
    mapping_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return G03MappingReceipt(
        producer="g02_pageplan",
        method="pageplan_slot_map",
        mapping_sha256=mapping_hash,
        mapped_block_count=len(blocks),
        mapped_image_slot_count=len(images),
        expected_block_count=len(blocks),
        expected_image_slot_count=len(images),
        analysis_region_ids=(),
    )


def _compile_structured(request: StructuredG03Request) -> G03Accepted | G03Rejected:
    request = _revalidate_structured(request)
    canonical_input_sha256 = canonical_compile_request_sha256(request)
    plan, registry = _revalidate_plan_sources(
        request.page_plan,
        request.source_registry,
    )
    grounding_error = _validate_grounding(plan, request.grounding)
    if grounding_error is not None:
        return _rejected(request.branch, grounding_error)
    mapping = _structured_mapping_receipt(plan)
    result = _accepted(
        branch=request.branch,
        plan=plan,
        source_registry=registry,
        grounding=request.grounding,
        branch_receipt=G03BranchReceipt(
            branch="structured",
            analysis_skipped=True,
            image_analyzer_called=False,
            snap_skipped=True,
            body_boxes_verified=True,
            source_refs_verified=True,
        ),
        guide_masks=(),
        analyses=(),
        snaps=(),
        mapping_receipt=mapping,
        canonical_input_sha256=canonical_input_sha256,
    )
    if result.response_size_bytes > result.response_budget_bytes:
        return _rejected(
            request.branch,
            _error(
                "RESPONSE_BUDGET_EXCEEDED",
                "G03 accepted response exceeds the UTF-8 response budget",
                expected=result.response_budget_bytes,
                actual=result.response_size_bytes,
            ),
        )
    return result


def _expected_pages(candidate: str) -> tuple[int, ...]:
    return (1,) if candidate == "A" else (1, 2)


def _page_blocks(plan: PagePlan) -> tuple[tuple[int, PagePlanBlock], ...]:
    return tuple(
        (page.page, block)
        for page in plan.pages
        for block in page.blocks
        if block.kind != "page_break"
    )


def _image_block(block: PagePlanBlock) -> ImagePlanBlock | None:
    return block if isinstance(block, ImagePlanBlock) else None


def _reference_page_map(
    request: GeneratedG03Request,
) -> tuple[dict[int, G03ReferencePage], G03Error | None]:
    expected = _expected_pages(request.candidate)
    pages = {item.page: item for item in request.reference_pages}
    if tuple(sorted(pages)) != expected or len(pages) != len(request.reference_pages):
        return {}, _error(
            "REFERENCE_PAGE_COUNT_MISMATCH",
            "generated/reference pages must match the candidate page sequence",
            expected=list(expected),
            actual=sorted(pages),
        )
    return pages, None


def _mapping_by_key(
    plan: PagePlan,
    mapping: G03MappingInput,
) -> tuple[dict[tuple[int, str], G03BlockBoxMapping], G03Error | None]:
    expected = {(page, block.block_id): block for page, block in _page_blocks(plan)}
    actual: dict[tuple[int, str], G03BlockBoxMapping] = {}
    for item in mapping.block_box_mapping:
        key = (item.page, item.block_id)
        if key in actual:
            return {}, _error(
                "BLOCK_MAPPING_DUPLICATE",
                "block mapping contains a duplicate page/block key",
                page=item.page,
                block_id=item.block_id,
                box_id=item.analysis_region_id,
            )
        actual[key] = item
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        page, block_id = missing[0]
        return {}, _error(
            "BLOCK_MAPPING_MISSING",
            "block mapping is missing a PagePlan block",
            page=page,
            block_id=block_id,
            expected=[list(item) for item in missing],
            actual=[list(item) for item in sorted(actual)],
        )
    if extra:
        page, block_id = extra[0]
        return {}, _error(
            "BLOCK_MAPPING_ORDER_MISMATCH",
            "block mapping contains a block that is not in the PagePlan",
            page=page,
            block_id=block_id,
            expected=[list(item) for item in sorted(expected)],
            actual=[list(item) for item in sorted(actual)],
        )
    if canonical_mapping_sha256(mapping) != mapping.mapping_sha256:
        return {}, _error(
            "MAPPING_HASH_MISMATCH",
            "host-recorded block mapping SHA-256 does not match its contents",
            expected=canonical_mapping_sha256(mapping),
            actual=mapping.mapping_sha256,
        )
    for page, block in expected.items():
        item = actual[page]
        image = _image_block(block)
        if image is not None and item.slot_id != image.slot_id:
            return {}, _error(
                "SLOT_COUNT_MISMATCH",
                "image block mapping slot does not match the PagePlan slot",
                page=page[0],
                block_id=page[1],
                slot_id=image.slot_id,
                expected=image.slot_id,
                actual=item.slot_id,
            )
        if image is None and item.slot_id is not None:
            return {}, _error(
                "SLOT_COUNT_MISMATCH",
                "non-image block mapping cannot carry an image slot",
                page=page[0],
                block_id=page[1],
                slot_id=item.slot_id,
            )
    expected_images = sum(
        isinstance(block, ImagePlanBlock) for _, block in _page_blocks(plan)
    )
    actual_images = sum(item.slot_id is not None for item in actual.values())
    if actual_images != expected_images:
        return {}, _error(
            "SLOT_COUNT_MISMATCH",
            "mapped image slot count does not match the PagePlan image slot count",
            expected=expected_images,
            actual=actual_images,
        )
    return actual, None


def _image_dimensions(path: Path) -> tuple[int, int]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            _ = image.load()
            return image.size
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"reference image cannot be decoded: {path}") from error


def _expected_edges(
    plan: PagePlanPage, width: int, height: int
) -> tuple[int, int, int, int]:
    paper_width, paper_height = _oriented_paper_dimensions(plan.page_setup)
    body = plan.body_box
    return (
        round(body.left_mm / paper_width * width),
        round(body.top_mm / paper_height * height),
        round((body.left_mm + body.width_mm) / paper_width * width),
        round((body.top_mm + body.height_mm) / paper_height * height),
    )


def _guide_collision(
    item: G03BlockBoxMapping,
    guide: G03ReferencePage,
) -> bool:
    width = guide.guide.image_width_px
    height = guide.guide.image_height_px
    left = item.box.left * width
    right = item.box.right * width
    top = item.box.top * height
    bottom = item.box.bottom * height
    thickness = guide.guide.thickness_px
    strips = (
        (guide.guide.left_px - thickness, guide.guide.left_px + thickness, 0, height),
        (guide.guide.right_px - thickness, guide.guide.right_px + thickness, 0, height),
        (0, width, guide.guide.top_px - thickness, guide.guide.top_px + thickness),
        (
            0,
            width,
            guide.guide.bottom_px - thickness,
            guide.guide.bottom_px + thickness,
        ),
    )
    for strip_left, strip_right, strip_top, strip_bottom in strips:
        if (
            left < strip_right
            and right > strip_left
            and top < strip_bottom
            and bottom > strip_top
        ):
            return True
    return False


def _fit_box_to_body(
    box: NormalizedBox,
    page: PagePlanPage,
) -> tuple[NormalizedBox, bool] | G03Error:
    paper_width, paper_height = _oriented_paper_dimensions(page.page_setup)
    body = page.body_box
    body_left = body.left_mm / paper_width
    body_top = body.top_mm / paper_height
    body_right = (body.left_mm + body.width_mm) / paper_width
    body_bottom = (body.top_mm + body.height_mm) / paper_height
    box_width = box.right - box.left
    box_height = box.bottom - box.top
    body_width = body_right - body_left
    body_height = body_bottom - body_top
    if box_width > body_width + _EPSILON or box_height > body_height + _EPSILON:
        return _error(
            "BOX_EXCEEDS_BODY",
            "mapped analysis box is larger than the observed document body",
            page=page.page,
            expected={"body_width_norm": body_width, "body_height_norm": body_height},
            actual={"box_width_norm": box_width, "box_height_norm": box_height},
        )
    left = min(max(box.left, body_left), body_right - box_width)
    top = min(max(box.top, body_top), body_bottom - box_height)
    fitted = NormalizedBox(
        left=left,
        top=top,
        right=left + box_width,
        bottom=top + box_height,
    )
    return fitted, fitted != box


def _analysis_regions(
    analysis: ReferenceImageAnalysis,
) -> dict[str, NormalizedBox]:
    regions: dict[str, NormalizedBox] = {}
    for item in analysis.objects:
        regions[item.region_id] = item.bbox
    for item in analysis.text_regions:
        regions[item.region_id] = item.bbox
    return regions


def _nearest_index(values: tuple[float, ...], value: float) -> int:
    return min(
        range(len(values)), key=lambda index: (abs(values[index] - value), index)
    )


def _snap_box(
    box: NormalizedBox,
    rows: tuple[float, ...],
    columns: tuple[float, ...],
) -> NormalizedBox:
    left = columns[_nearest_index(columns, box.left)]
    right = columns[_nearest_index(columns, box.right)]
    top = rows[_nearest_index(rows, box.top)]
    bottom = rows[_nearest_index(rows, box.bottom)]
    if right <= left:
        right = min(1.0, max(left + _EPSILON, box.right))
    if bottom <= top:
        bottom = min(1.0, max(top + _EPSILON, box.bottom))
    return NormalizedBox(left=left, top=top, right=right, bottom=bottom)


def _axis_mm_boxes(
    boxes: Iterable[NormalizedBox],
    *,
    axis: str,
    body: MmBox,
    page_setup: PageSetup,
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    values = {0.0, 1.0}
    paper_width, paper_height = _oriented_paper_dimensions(page_setup)
    if axis == "column":
        for box in boxes:
            values.add((box.left * paper_width - body.left_mm) / body.width_mm)
            values.add((box.right * paper_width - body.left_mm) / body.width_mm)
        origin = mm_to_hwpunit(body.left_mm)
        extent = mm_to_hwpunit(body.width_mm)
    else:
        for box in boxes:
            values.add((box.top * paper_height - body.top_mm) / body.height_mm)
            values.add((box.bottom * paper_height - body.top_mm) / body.height_mm)
        origin = mm_to_hwpunit(body.top_mm)
        extent = mm_to_hwpunit(body.height_mm)
    normalized = tuple(sorted(max(0.0, min(1.0, value)) for value in values))
    if len(set(normalized)) != len(normalized):
        normalized = tuple(dict.fromkeys(normalized))
    mapped = map_normalized_breakpoints(normalized, origin=origin, extent=extent)
    return normalized, mapped.boundaries


def _box_to_mm(
    box: NormalizedBox,
    page: PagePlanPage,
    row_values: tuple[float, ...],
    row_boundaries: tuple[int, ...],
    column_values: tuple[float, ...],
    column_boundaries: tuple[int, ...],
) -> MmBox:
    body = page.body_box
    paper_width, paper_height = _oriented_paper_dimensions(page.page_setup)
    left_rel = max(
        0.0, min(1.0, (box.left * paper_width - body.left_mm) / body.width_mm)
    )
    right_rel = max(
        0.0, min(1.0, (box.right * paper_width - body.left_mm) / body.width_mm)
    )
    top_rel = max(
        0.0, min(1.0, (box.top * paper_height - body.top_mm) / body.height_mm)
    )
    bottom_rel = max(
        0.0, min(1.0, (box.bottom * paper_height - body.top_mm) / body.height_mm)
    )
    left = _hwp_to_mm(column_boundaries[_nearest_index(column_values, left_rel)])
    right = _hwp_to_mm(column_boundaries[_nearest_index(column_values, right_rel)])
    top = _hwp_to_mm(row_boundaries[_nearest_index(row_values, top_rel)])
    bottom = _hwp_to_mm(row_boundaries[_nearest_index(row_values, bottom_rel)])
    left = max(body.left_mm, min(body.right_mm, left))
    right = max(left + 0.001, min(body.right_mm, right))
    top = max(body.top_mm, min(body.bottom_mm, top))
    bottom = max(top + 0.001, min(body.bottom_mm, bottom))
    return MmBox(
        left_mm=round(left, 6),
        top_mm=round(top, 6),
        width_mm=round(right - left, 6),
        height_mm=round(bottom - top, 6),
    )


def _round_trip_error(
    original: Iterable[NormalizedBox],
    output: Iterable[MmBox],
    page: PagePlanPage,
    width: int,
    height: int,
) -> float:
    errors: list[float] = []
    paper_width, paper_height = _oriented_paper_dimensions(page.page_setup)
    for source, result in zip(original, output, strict=True):
        expected = (
            source.left * width,
            source.top * height,
            source.right * width,
            source.bottom * height,
        )
        actual = (
            result.left_mm / paper_width * width,
            result.top_mm / paper_height * height,
            result.right_mm / paper_width * width,
            result.bottom_mm / paper_height * height,
        )
        errors.extend(
            abs(left - right) for left, right in zip(expected, actual, strict=True)
        )
    return max(errors, default=0.0)


def _page_snap(
    page: PagePlanPage,
    mappings: tuple[G03BlockBoxMapping, ...],
    analysis: ReferenceImageAnalysis,
    width: int,
    height: int,
) -> tuple[dict[str, MmBox], G03SnapReceipt] | G03Error:
    fitted: dict[str, NormalizedBox] = {}
    adjusted: list[NormalizedBox] = []
    for item in mappings:
        result = _fit_box_to_body(item.box, page)
        if isinstance(result, G03Error):
            return result.model_copy(
                update={"block_id": item.block_id, "box_id": item.analysis_region_id}
            )
        fitted[item.block_id], _ = result
        adjusted.append(fitted[item.block_id])
    raw_rows = tuple(
        sorted(
            {
                0.0,
                1.0,
                *(box.top for box in adjusted),
                *(box.bottom for box in adjusted),
            }
        )
    )
    raw_columns = tuple(
        sorted(
            {
                0.0,
                1.0,
                *(box.left for box in adjusted),
                *(box.right for box in adjusted),
            }
        )
    )
    row_candidates = tuple(
        candidate
        for candidate in analysis.breakpoint_candidates
        if candidate.axis == "row" and candidate.source != "text"
    )
    column_candidates = tuple(
        candidate
        for candidate in analysis.breakpoint_candidates
        if candidate.axis == "column" and candidate.source != "text"
    )
    snapped_rows = _snap_breakpoints(raw_rows, row_candidates)
    snapped_columns = _snap_breakpoints(raw_columns, column_candidates)
    snapped_boxes = {
        block_id: _snap_box(box, snapped_rows, snapped_columns)
        for block_id, box in fitted.items()
    }
    try:
        row_values, row_boundaries = _axis_mm_boxes(
            snapped_boxes.values(),
            axis="row",
            body=page.body_box,
            page_setup=page.page_setup,
        )
        column_values, column_boundaries = _axis_mm_boxes(
            snapped_boxes.values(),
            axis="column",
            body=page.body_box,
            page_setup=page.page_setup,
        )
    except ValueError as error:
        return _error(
            "UNSUPPORTED_LAYOUT_VOCABULARY",
            f"snapped boxes cannot be represented by the HWP grid: {error}",
            page=page.page,
        )
    output_boxes = {
        block_id: _box_to_mm(
            box,
            page,
            row_values,
            row_boundaries,
            column_values,
            column_boundaries,
        )
        for block_id, box in snapped_boxes.items()
    }
    error_px = _round_trip_error(
        snapped_boxes.values(), output_boxes.values(), page, width, height
    )
    if error_px > _MAX_ROUND_TRIP_ERROR_PX:
        return _error(
            "ROUND_TRIP_ERROR_EXCEEDED",
            "snapped PagePlan boxes exceed the one-pixel round-trip tolerance",
            page=page.page,
            expected=_MAX_ROUND_TRIP_ERROR_PX,
            actual=error_px,
        )
    return output_boxes, G03SnapReceipt(
        page=page.page,
        raw_row_breakpoints=raw_rows,
        raw_column_breakpoints=raw_columns,
        snapped_row_breakpoints=snapped_rows,
        snapped_column_breakpoints=snapped_columns,
        row_grid_boundaries_hwp=row_boundaries,
        column_grid_boundaries_hwp=column_boundaries,
        grid_interval_hwp=min(
            min(
                right - left for left, right in zip(row_boundaries, row_boundaries[1:])
            ),
            min(
                right - left
                for left, right in zip(column_boundaries, column_boundaries[1:])
            ),
        ),
        round_trip_max_error_px=error_px,
    )


def _updated_plan(
    template: PagePlan,
    boxes: dict[tuple[int, str], MmBox],
) -> PagePlan:
    pages: list[PagePlanPage] = []
    for page in template.pages:
        blocks: list[PagePlanBlock] = []
        for block in page.blocks:
            if block.kind == "page_break":
                blocks.append(block)
            else:
                blocks.append(
                    block.model_copy(update={"box": boxes[(page.page, block.block_id)]})
                )
        pages.append(page.model_copy(update={"blocks": tuple(blocks)}))
    labels = tuple(
        {
            "page": page.page,
            "slot_id": block.slot_id,
            "width_mm": block.box.width_mm,
            "body_width_mm": page.body_box.width_mm,
            "body_fraction": block.box.width_mm / page.body_box.width_mm,
        }
        for page in pages
        for block in page.blocks
        if isinstance(block, ImagePlanBlock)
    )
    return build_page_plan(
        candidate=template.candidate,
        pages=tuple(pages),
        slot_map=template.slot_map,
        source_manifest_sha256=template.source_manifest_sha256,
        style_roles_sha256=template.style_roles_sha256,
        observation=template.g01_observation,
        observed_width_labels=labels,
    )


def _compile_generated(request: GeneratedG03Request) -> G03Accepted | G03Rejected:
    request = _revalidate_generated(request)
    canonical_input_sha256 = canonical_compile_request_sha256(request)
    template, registry = _revalidate_plan_sources(
        request.page_plan_template,
        request.source_registry,
    )
    grounding_error = _validate_grounding(template, request.grounding)
    if grounding_error is not None:
        return _rejected(request.branch, grounding_error)
    reference_pages, page_error = _reference_page_map(request)
    if page_error is not None:
        return _rejected(request.branch, page_error)
    mapping, mapping_error = _mapping_by_key(template, request.mapping)
    if mapping_error is not None:
        return _rejected(request.branch, mapping_error)
    assert mapping
    analyses: list[G03AnalysisReceipt] = []
    guide_masks: list[G03GuideMaskReceipt] = []
    snaps: list[G03SnapReceipt] = []
    degraded_pages: list[int] = []
    output_boxes: dict[tuple[int, str], MmBox] = {}
    artifact_root = (
        request.artifact_root.expanduser().resolve()
        if request.artifact_root is not None
        else Path.home() / ".gsg_hwp_beta" / "g03-pageplan"
    )
    for page in template.pages:
        reference = reference_pages[page.page]
        source = reference.clean_image_path.expanduser().resolve()
        if not source.is_file():
            return _rejected(
                request.branch,
                _error(
                    "REFERENCE_IMAGE_NOT_FOUND",
                    f"reference image does not exist: {source}",
                    page=page.page,
                    source_ref=str(source),
                ),
            )
        actual_hash = _sha256_file(source)
        if actual_hash != reference.clean_image_sha256:
            return _rejected(
                request.branch,
                _error(
                    "REFERENCE_IMAGE_SHA256_MISMATCH",
                    "clean reference image SHA-256 does not match the request",
                    page=page.page,
                    expected=reference.clean_image_sha256,
                    actual=actual_hash,
                ),
            )
        try:
            width, height = _image_dimensions(source)
        except ValueError as error:
            return _rejected(
                request.branch,
                _error("REFERENCE_IMAGE_NOT_FOUND", str(error), page=page.page),
            )
        if (width, height) != (
            reference.guide.image_width_px,
            reference.guide.image_height_px,
        ):
            return _rejected(
                request.branch,
                _error(
                    "GUIDE_METADATA_MISMATCH",
                    "guide metadata dimensions do not match the clean image",
                    page=page.page,
                    expected=[width, height],
                    actual=[
                        reference.guide.image_width_px,
                        reference.guide.image_height_px,
                    ],
                ),
            )
        if (
            reference.guide.source_body_geometry_hash
            != template.g01_observation.body_geometry_hash
        ):
            return _rejected(
                request.branch,
                _error(
                    "GUIDE_METADATA_MISMATCH",
                    "guide metadata is not bound to the PagePlan G01 body geometry",
                    page=page.page,
                    expected=template.g01_observation.body_geometry_hash,
                    actual=reference.guide.source_body_geometry_hash,
                ),
            )
        expected_edges = _expected_edges(page, width, height)
        actual_edges = (
            reference.guide.left_px,
            reference.guide.top_px,
            reference.guide.right_px,
            reference.guide.bottom_px,
        )
        if expected_edges != actual_edges:
            return _rejected(
                request.branch,
                _error(
                    "GUIDE_METADATA_MISMATCH",
                    "guide coordinates do not match the observed G01 body geometry",
                    page=page.page,
                    expected=list(expected_edges),
                    actual=list(actual_edges),
                ),
            )
        page_mappings = tuple(
            item for item in mapping.values() if item.page == page.page
        )
        for item in page_mappings:
            fit_result = _fit_box_to_body(item.box, page)
            if isinstance(fit_result, G03Error):
                return _rejected(
                    request.branch,
                    fit_result.model_copy(
                        update={
                            "block_id": item.block_id,
                            "box_id": item.analysis_region_id,
                        }
                    ),
                )
            if _guide_collision(item, reference):
                return _rejected(
                    request.branch,
                    _error(
                        "GUIDE_MASK_SOURCE_COLLISION",
                        "coordinate guide masking would destroy a mapped content region",
                        page=page.page,
                        block_id=item.block_id,
                        slot_id=item.slot_id,
                        box_id=item.analysis_region_id,
                        actual=item.box.model_dump(mode="json"),
                    ),
                )
        mask_path = (
            artifact_root
            / "masks"
            / (
                f"page-{page.page}-{actual_hash[:16]}-"
                f"{template.g01_observation.body_geometry_hash[:16]}.png"
            )
        )
        try:
            guide_receipt = mask_reference_guides(source, mask_path, reference.guide)
        except ValueError as error:
            return _rejected(
                request.branch,
                _error("GUIDE_MASK_FAILED", str(error), page=page.page),
            )
        guide_masks.append(guide_receipt)
        try:
            if reference.analysis_id is None:
                analysis = analyze_reference_image(
                    mask_path,
                    artifact_root=artifact_root / "analysis",
                )
                cache_reused = False
            else:
                analysis = load_cached_reference_image_analysis(
                    mask_path,
                    reference.analysis_id,
                    artifact_root=artifact_root / "analysis",
                )
                cache_reused = True
        except (OSError, ValueError) as error:
            code = (
                "STALE_ANALYSIS_ID"
                if reference.analysis_id is not None
                else "ANALYSIS_UNSUPPORTED"
            )
            return _rejected(
                request.branch,
                _error(
                    code,
                    str(error),
                    page=page.page,
                    analysis_id=reference.analysis_id,
                ),
            )
        # 조기 종료는 거절 사유가 아니라 "픽셀 증거가 없다"는 사실이다. 분석기
        # 기본 시간 예산이 여기에도 그대로 걸려 source_image 첨부가 쓰기 호출을
        # 240초 작업자 마감까지 끌고 가지 않는다. 미완 분석은 검출 묶음이 비어
        # 있으므로(hwp_reference_image_analyzer._stopped_analysis) 좌표를 쓰는
        # 단계 — 영역 대조와 분할점 스냅 — 를 건너뛰고 모델이 준 상자를 그대로
        # 본문 격자로 옮긴다. 그 계산은 source_image 를 아예 붙이지 않은 호출과
        # 같다. 재생 봉인만은 건너뛰지 않는다: 강등 페이지도 안정된 표로 봉인해
        # 같은 요청이 두 번 다른 계획을 내는 것을 잡는다.
        #
        # 좌표가 꼭 필요하면 guide_masks[].masked_image_path 로 돌아오는 안내선
        # 제거 사본에 hwp_analyze_reference_image(full_scan=True) 를 돌려 얻은
        # analysis_id 를 첨부하면 증거 경로가 그대로 돌아온다. 사본은 G03 이
        # artifact_root 밑에 스스로 만들기 때문에, 그 경로를 내지 않던 동안 이
        # 탈출구는 말만 있고 실행할 수 없었다.
        analysis_degraded = not analysis.analysis_complete
        if analysis_degraded:
            degraded_pages.append(page.page)
        if analysis.image_hash != guide_receipt.masked_image_sha256:
            return _rejected(
                request.branch,
                _error(
                    "ANALYSIS_SOURCE_HASH_MISMATCH",
                    "reference analysis does not match the guide-masked image",
                    page=page.page,
                    analysis_id=analysis.analysis_id,
                    expected=guide_receipt.masked_image_sha256,
                    actual=analysis.image_hash,
                ),
            )
        if analysis.analyzer_version != ANALYZER_VERSION:
            return _rejected(
                request.branch,
                _error(
                    "STALE_ANALYSIS_ID",
                    "reference analysis analyzer version is stale",
                    page=page.page,
                    analysis_id=analysis.analysis_id,
                    expected=ANALYZER_VERSION,
                    actual=analysis.analyzer_version,
                ),
            )
        expected_analysis_id = _expected_analysis_id(analysis.image_hash)
        if analysis.analysis_id != expected_analysis_id:
            return _rejected(
                request.branch,
                _error(
                    "ANALYSIS_RESULT_SEAL_MISMATCH",
                    "reference analysis identity is not derived from its canonical input",
                    page=page.page,
                    analysis_id=analysis.analysis_id,
                    expected=expected_analysis_id,
                    actual=analysis.analysis_id,
                ),
            )
        analysis_result_sha256 = _canonical_analysis_result_sha256(analysis)
        if (
            reference.expected_analysis_result_sha256 is not None
            and reference.expected_analysis_result_sha256 != analysis_result_sha256
        ):
            return _rejected(
                request.branch,
                _error(
                    "ANALYSIS_RESULT_SEAL_MISMATCH",
                    "reference analysis result seal does not match the request",
                    page=page.page,
                    analysis_id=analysis.analysis_id,
                    expected=reference.expected_analysis_result_sha256,
                    actual=analysis_result_sha256,
                ),
            )
        # 강등 페이지도 봉인에 남긴다. 미완 결과의 해시는 실측 predicted_seconds
        # 를 담아 호출마다 달라지므로 그것을 그대로 실으면 같은 요청의 두 번째
        # 호출이 거절된다. 대신 "이 페이지는 분석 증거를 한 톨도 쓰지 않았다"는
        # 안정된 표를 봉인한다 — 강등끼리는 언제나 맞고, 같은 요청이 한 번은 강등
        # 계획을 한 번은 증거 계획을 내면(조기 종료는 캐시에 남지 않아 재실행이
        # 완주할 수 있다) 봉인이 그 상이를 잡는다. 영수증은 그대로 증거를 쓴
        # 페이지에만 남는다. 호출자가 직접 못박은
        # expected_analysis_result_sha256 은 위에서 그대로 대조했다.
        replay_error = _replay_evidence_error(
            canonical_input_sha256,
            page.page,
            _DEGRADED_EVIDENCE_SEAL if analysis_degraded else analysis_result_sha256,
        )
        if replay_error is not None:
            return _rejected(request.branch, replay_error)
        if not analysis_degraded:
            analyses.append(
                G03AnalysisReceipt(
                    page=page.page,
                    analysis_id=analysis.analysis_id,
                    analyzer_version=analysis.analyzer_version,
                    source_image_sha256=analysis.image_hash,
                    analysis_result_sha256=analysis_result_sha256,
                    cache_reused=cache_reused,
                )
            )
        regions = _analysis_regions(analysis)
        # 미완 분석에는 대조할 영역이 없다. 없는 증거와 맞춰 보다가 계획을
        # 떨어뜨리지 않도록 대조 자체를 건너뛴다.
        verified_mappings = () if analysis_degraded else page_mappings
        for item in verified_mappings:
            region = regions.get(item.analysis_region_id)
            if region is None:
                return _rejected(
                    request.branch,
                    _error(
                        "ANALYSIS_REGION_NOT_FOUND",
                        "host mapping references an analysis region that does not exist",
                        page=page.page,
                        block_id=item.block_id,
                        box_id=item.analysis_region_id,
                        analysis_id=analysis.analysis_id,
                    ),
                )
            region_values = (
                region.left,
                region.top,
                region.right,
                region.bottom,
            )
            mapping_values = (
                item.box.left,
                item.box.top,
                item.box.right,
                item.box.bottom,
            )
            if any(
                abs(region_value - mapping_value) > _EPSILON
                for region_value, mapping_value in zip(
                    region_values,
                    mapping_values,
                    strict=True,
                )
            ):
                return _rejected(
                    request.branch,
                    _error(
                        "ANALYSIS_REGION_BOX_MISMATCH",
                        "host mapping box differs from the analyzer region box",
                        page=page.page,
                        block_id=item.block_id,
                        box_id=item.analysis_region_id,
                        expected=region.model_dump(mode="json"),
                        actual=item.box.model_dump(mode="json"),
                    ),
                )
        snap_result = _page_snap(page, page_mappings, analysis, width, height)
        if isinstance(snap_result, G03Error):
            return _rejected(request.branch, snap_result)
        page_boxes, snap_receipt = snap_result
        for block_id, box in page_boxes.items():
            output_boxes[(page.page, block_id)] = box
        # 미완 분석은 breakpoint_candidates 가 비어 있어 _snap_breakpoints 가
        # 값을 그대로 돌려준다 — 상자는 모델이 준 자리 그대로 격자로만 옮겨진다.
        # 증거로 옮긴 좌표가 없으므로 스냅 영수증도 남기지 않는다.
        if not analysis_degraded:
            snaps.append(snap_receipt)
    try:
        final_plan = _updated_plan(template, output_boxes)
        _ = canonical_page_plan_sha256(final_plan)
        _ = validate_page_plan_for_compiler(final_plan, registry)
    except ValueError as error:
        message = str(error)
        if "final-insertable" in message:
            code = "NON_FINAL_INSERTABLE_ASSET"
        elif (
            "source SHA" in message
            or "asset SHA" in message
            or "hash mismatch" in message
            or "provenance mismatch" in message
        ):
            code = "SOURCE_HASH_MISMATCH"
        else:
            code = "COMPILATION_FAILED"
        return _rejected(request.branch, _error(code, message))
    image_blocks = tuple(
        block
        for _, block in _page_blocks(final_plan)
        if isinstance(block, ImagePlanBlock)
    )
    mapping_receipt = G03MappingReceipt(
        producer=request.mapping.producer,
        method=request.mapping.method,
        mapping_sha256=request.mapping.mapping_sha256,
        mapped_block_count=len(mapping),
        mapped_image_slot_count=sum(
            item.slot_id is not None for item in mapping.values()
        ),
        expected_block_count=len(_page_blocks(final_plan)),
        expected_image_slot_count=len(image_blocks),
        analysis_region_ids=tuple(
            item.analysis_region_id
            for item in sorted(
                mapping.values(), key=lambda item: (item.page, item.block_id)
            )
        ),
    )
    result = _accepted(
        branch=request.branch,
        plan=final_plan,
        source_registry=registry,
        grounding=request.grounding,
        branch_receipt=G03BranchReceipt(
            branch=request.branch,
            # 분석기는 불렀지만(image_analyzer_called=True) 조기 종료해 증거를
            # 쓰지 않은 계획은 analysis_skipped/snap_skipped 가 참이다. 세 갈래가
            # 이 두 값으로 구분된다: structured=(True, 안 부름),
            # 증거 사용=(False, 부름), 강등=(True, 부름).
            #
            # 두 값은 계획 하나에 대한 값이라 "한 페이지라도 건너뛰었다"는 뜻으로
            # 읽어야 한다. 페이지마다 갈릴 수 있기 때문이다 — 후보 B 에서 1쪽이
            # 조기 종료하고 2쪽이 완주하면 두 값이 True 인데 analyses/snaps 에는
            # 2쪽 영수증이 남는다(실측). 그 모순처럼 보이는 상태를 읽을 수 있게
            # 강등된 페이지를 degraded_pages 가 직접 이름 붙인다.
            analysis_skipped=bool(degraded_pages),
            image_analyzer_called=True,
            snap_skipped=bool(degraded_pages),
            # 이 둘은 강등 여부와 무관하게 참이다. 강등 계획도 매핑 상자마다
            # _fit_box_to_body 를 지나고(본문보다 큰 상자가 강등 페이지에서
            # BOX_EXCEEDS_BODY 로 거절되는 것을 실측했다), 최종 계획이
            # validate_page_plan_for_compiler 를 지난다(등록부 manifest 봉인이
            # 어긋나면 재검증에서 거절된다). 강등이 건너뛰는 것은 분석 영역
            # 대조와 분할점 스냅이고, 그것은 위 세 값이 말한다.
            body_boxes_verified=True,
            source_refs_verified=True,
            degraded_pages=tuple(degraded_pages),
        ),
        guide_masks=tuple(guide_masks),
        analyses=tuple(analyses),
        snaps=tuple(snaps),
        mapping_receipt=mapping_receipt,
        canonical_input_sha256=canonical_input_sha256,
    )
    if result.response_size_bytes > result.response_budget_bytes:
        return _rejected(
            request.branch,
            _error(
                "RESPONSE_BUDGET_EXCEEDED",
                "G03 accepted response exceeds the UTF-8 response budget",
                expected=result.response_budget_bytes,
                actual=result.response_size_bytes,
            ),
        )
    return result


def compile_page_plan(request: G03CompileRequest | object) -> G03CompileResponse:
    try:
        if isinstance(request, (StructuredG03Request, GeneratedG03Request)):
            parsed = request
        else:
            parsed = cast(
                StructuredG03Request | GeneratedG03Request,
                TypeAdapter(G03CompileRequest).validate_python(request),
            )
        if isinstance(parsed, StructuredG03Request):
            return _compile_structured(parsed)
        return _compile_generated(parsed)
    except ValidationError as error:
        error_message = str(error)
        validation_code: G03ErrorCode = "INVALID_BRANCH_INPUT"
        if "grounding" in error_message and "Field required" in error_message:
            validation_code = "MISSING_G01_GROUNDING"
        return _rejected(
            _branch_name(request),
            _error(validation_code, error_message),
        )
    except (OSError, TypeError, ValueError) as error:
        message = str(error)
        compile_code: G03ErrorCode
        if "final-insertable" in message:
            compile_code = "NON_FINAL_INSERTABLE_ASSET"
        elif (
            "source SHA" in message
            or "asset SHA" in message
            or "source hash" in message
            or "manifest hash" in message
            or "provenance mismatch" in message
        ):
            compile_code = "SOURCE_HASH_MISMATCH"
        elif "body box" in message or "body geometry" in message:
            compile_code = "BODY_BOX_OVERFLOW"
        else:
            compile_code = "COMPILATION_FAILED"
        return _rejected(_branch_name(request), _error(compile_code, message))


__all__ = ["compile_page_plan"]

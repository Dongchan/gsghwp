from __future__ import annotations

from typing import Annotated, Literal
from pathlib import Path

from pydantic import Field, JsonValue, model_validator

from hwp_live_values import ContractModel
from hwp_pageplan_assets import SourceRegistry
from hwp_pageplan_contract import (
    CandidateId,
    G01Observation,
    PagePlan,
    Sha256,
    canonical_json_bytes,
    sha256_bytes,
)
from hwp_reference_image_contract import NormalizedBox


G03_SCHEMA_VERSION = "gsg.hwp.page-plan-compile.v1"
G03_RESPONSE_BUDGET_BYTES = 100_000
G03Branch = Literal["structured", "generated", "reference_image"]


class G03GroundingProvenance(ContractModel):
    document_selector: str = Field(min_length=1, max_length=500)
    document_revision_hash: Sha256
    page_setup_hash: Sha256
    convention_profile_hash: Sha256
    body_geometry_hash: Sha256


class G03GuideMetadata(ContractModel):
    page: int = Field(ge=1)
    image_width_px: int = Field(ge=2)
    image_height_px: int = Field(ge=2)
    left_px: int = Field(ge=0)
    top_px: int = Field(ge=0)
    right_px: int = Field(ge=1)
    bottom_px: int = Field(ge=1)
    thickness_px: int = Field(ge=1, le=64)
    source_body_geometry_hash: Sha256

    @model_validator(mode="after")
    def validate_edges(self) -> G03GuideMetadata:
        if self.left_px >= self.right_px or self.top_px >= self.bottom_px:
            raise ValueError("guide edge coordinates must form a rectangle")
        if self.right_px >= self.image_width_px:
            raise ValueError("guide right edge is outside the image")
        if self.bottom_px >= self.image_height_px:
            raise ValueError("guide bottom edge is outside the image")
        return self


class G03ReferencePage(ContractModel):
    page: int = Field(ge=1)
    clean_image_path: Path
    clean_image_sha256: Sha256
    guide: G03GuideMetadata
    analysis_id: str | None = Field(
        default=None,
        pattern=r"^ria-[0-9a-f]{16}$",
    )
    expected_analysis_result_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_page(self) -> G03ReferencePage:
        if self.guide.page != self.page:
            raise ValueError("guide page does not match reference page")
        return self


class G03BlockBoxMapping(ContractModel):
    page: int = Field(ge=1)
    block_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    analysis_region_id: str = Field(min_length=1, max_length=100)
    box: NormalizedBox
    slot_id: int | None = Field(default=None, ge=1)


class G03MappingInput(ContractModel):
    producer: Literal["host_recorded"] = "host_recorded"
    method: Literal["explicit_analysis_region"] = "explicit_analysis_region"
    block_box_mapping: tuple[G03BlockBoxMapping, ...] = Field(max_length=1_000)
    mapping_sha256: Sha256


def canonical_mapping_sha256(mapping: G03MappingInput) -> str:
    payload = {
        "producer": mapping.producer,
        "method": mapping.method,
        "block_box_mapping": [
            item.model_dump(mode="json", by_alias=True)
            for item in sorted(
                mapping.block_box_mapping,
                key=lambda item: (item.page, item.block_id, item.analysis_region_id),
            )
        ],
    }
    return sha256_bytes(canonical_json_bytes(payload))


class StructuredG03Request(ContractModel):
    schema_id: Literal["gsg.hwp.page-plan-compile.v1"] = Field(
        default=G03_SCHEMA_VERSION,
        alias="schema",
    )
    branch: Literal["structured"]
    candidate: CandidateId
    page_plan: PagePlan
    source_registry: SourceRegistry
    grounding: G03GroundingProvenance

    @model_validator(mode="after")
    def validate_candidate(self) -> StructuredG03Request:
        if self.page_plan.candidate != self.candidate:
            raise ValueError("request candidate does not match PagePlan")
        return self


class GeneratedG03Request(ContractModel):
    schema_id: Literal["gsg.hwp.page-plan-compile.v1"] = Field(
        default=G03_SCHEMA_VERSION,
        alias="schema",
    )
    branch: Literal["generated", "reference_image"]
    candidate: CandidateId
    page_plan_template: PagePlan
    source_registry: SourceRegistry
    grounding: G03GroundingProvenance
    reference_pages: tuple[G03ReferencePage, ...] = Field(max_length=2)
    mapping: G03MappingInput
    artifact_root: Path | None = None

    @model_validator(mode="after")
    def validate_candidate(self) -> GeneratedG03Request:
        if self.page_plan_template.candidate != self.candidate:
            raise ValueError("request candidate does not match PagePlan template")
        return self


G03CompileRequest = Annotated[
    StructuredG03Request | GeneratedG03Request,
    Field(discriminator="branch"),
]


class G03BranchReceipt(ContractModel):
    branch: Literal["structured", "generated", "reference_image"]
    # analysis_skipped/snap_skipped 는 계획 하나에 대한 값이라 "한 페이지라도
    # 건너뛰었다"는 뜻이다. 그 둘만으로는 혼합 페이지를 읽을 수 없어서
    # (실측: 후보 B 에서 1쪽이 조기 종료하고 2쪽이 완주하면 두 불리언이 True 인데
    # analyses/snaps 에는 2쪽 영수증이 들어 있다) 어느 쪽이 강등됐는지를 아래
    # 목록이 직접 이름 붙인다. 증거를 쓴 페이지는 analyses/snaps 에 남는다.
    analysis_skipped: bool
    image_analyzer_called: bool
    snap_skipped: bool
    # 이 둘은 강등 여부와 무관하게 참이다. 강등 계획도 매핑 상자마다
    # _fit_box_to_body 를 지나고(본문보다 큰 상자는 BOX_EXCEEDS_BODY 로 거절되는
    # 것을 실측했다) 최종 계획이 validate_page_plan_for_compiler 를 지난다
    # (등록부 manifest 봉인 불일치는 재검증에서 거절된다). 강등이 건너뛰는 것은
    # 분석 영역 대조와 분할점 스냅이고, 그 사실은 위 세 값이 말한다.
    body_boxes_verified: bool
    source_refs_verified: bool
    degraded_pages: tuple[int, ...] = ()


class G03GuideMaskReceipt(ContractModel):
    page: int = Field(ge=1)
    source_image_sha256: Sha256
    masked_image_sha256: Sha256
    image_width_px: int = Field(ge=2)
    image_height_px: int = Field(ge=2)
    masked_pixel_count: int = Field(ge=0)
    edge_coordinates_px: tuple[int, int, int, int]
    clean_source_preserved: Literal[True] = True
    # 안내선을 지운 사본의 경로. 강등된 계획에서 좌표 증거가 필요하면 호출자는
    # 이 경로로 hwp_analyze_reference_image(full_scan=True) 를 돌려 analysis_id 를
    # 얻고 다시 붙이면 된다. 경로를 내지 않던 동안 그 탈출구는 말만 있고 실행할
    # 수 없었다 — 사본은 G03 이 artifact_root 밑에 스스로 만들기 때문이다.
    masked_image_path: Path | None = None


class G03AnalysisReceipt(ContractModel):
    page: int = Field(ge=1)
    analysis_id: str = Field(pattern=r"^ria-[0-9a-f]{16}$")
    analyzer_version: str = Field(min_length=1, max_length=64)
    source_image_sha256: Sha256
    analysis_result_sha256: Sha256
    cache_reused: bool


class G03SnapReceipt(ContractModel):
    page: int = Field(ge=1)
    raw_row_breakpoints: tuple[float, ...] = Field(min_length=2, max_length=51)
    raw_column_breakpoints: tuple[float, ...] = Field(min_length=2, max_length=51)
    snapped_row_breakpoints: tuple[float, ...] = Field(min_length=2, max_length=51)
    snapped_column_breakpoints: tuple[float, ...] = Field(min_length=2, max_length=51)
    row_grid_boundaries_hwp: tuple[int, ...] = Field(min_length=2, max_length=51)
    column_grid_boundaries_hwp: tuple[int, ...] = Field(min_length=2, max_length=51)
    grid_interval_hwp: int = Field(ge=100)
    round_trip_max_error_px: float = Field(ge=0, le=1)


class G03MappingReceipt(ContractModel):
    producer: Literal["host_recorded", "g02_pageplan"]
    method: Literal["explicit_analysis_region", "pageplan_slot_map"]
    mapping_sha256: Sha256
    mapped_block_count: int = Field(ge=0)
    mapped_image_slot_count: int = Field(ge=0)
    expected_block_count: int = Field(ge=0)
    expected_image_slot_count: int = Field(ge=0)
    analysis_region_ids: tuple[str, ...]


G03ErrorCode = Literal[
    "INVALID_BRANCH_INPUT",
    "MISSING_G01_GROUNDING",
    "GROUNDING_PROVENANCE_MISMATCH",
    "REFERENCE_IMAGE_NOT_FOUND",
    "REFERENCE_IMAGE_SHA256_MISMATCH",
    "REFERENCE_PAGE_COUNT_MISMATCH",
    "GUIDE_METADATA_MISMATCH",
    "GUIDE_MASK_SOURCE_COLLISION",
    "GUIDE_MASK_FAILED",
    "STALE_ANALYSIS_ID",
    "ANALYSIS_SOURCE_HASH_MISMATCH",
    "ANALYSIS_RESULT_SEAL_MISMATCH",
    "ANALYSIS_REGION_NOT_FOUND",
    "ANALYSIS_REGION_BOX_MISMATCH",
    "BODY_BOX_OVERFLOW",
    "BOX_EXCEEDS_BODY",
    "SLOT_COUNT_MISMATCH",
    "MAPPING_COUNT_MISMATCH",
    "MAPPING_HASH_MISMATCH",
    "BLOCK_MAPPING_MISSING",
    "BLOCK_MAPPING_DUPLICATE",
    "BLOCK_MAPPING_ORDER_MISMATCH",
    "SOURCE_HASH_MISMATCH",
    "NON_FINAL_INSERTABLE_ASSET",
    "UNSUPPORTED_LAYOUT_VOCABULARY",
    "ANALYSIS_UNSUPPORTED",
    "ROUND_TRIP_ERROR_EXCEEDED",
    "RESPONSE_BUDGET_EXCEEDED",
    "COMPILATION_FAILED",
]


class G03Error(ContractModel):
    code: G03ErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    page: int | None = Field(default=None, ge=1)
    block_id: str | None = None
    slot_id: int | None = Field(default=None, ge=1)
    box_id: str | None = None
    source_ref: str | None = None
    analysis_id: str | None = None
    expected: JsonValue | None = None
    actual: JsonValue | None = None


class G03Accepted(ContractModel):
    schema_id: Literal["gsg.hwp.page-plan-compile-result.v1"] = Field(
        default="gsg.hwp.page-plan-compile-result.v1",
        alias="schema",
    )
    status: Literal["compiled"] = "compiled"
    branch: G03Branch
    plan: PagePlan
    source_manifest_sha256: Sha256
    style_roles_sha256: Sha256
    grounding: G03GroundingProvenance
    branch_receipt: G03BranchReceipt
    guide_masks: tuple[G03GuideMaskReceipt, ...] = ()
    analyses: tuple[G03AnalysisReceipt, ...] = ()
    snaps: tuple[G03SnapReceipt, ...] = ()
    mapping_receipt: G03MappingReceipt
    canonical_input_sha256: Sha256
    canonical_evidence_sha256: Sha256
    deterministic_result_sha256: Sha256
    response_budget_bytes: int = Field(default=G03_RESPONSE_BUDGET_BYTES, ge=1)
    response_size_bytes: int = Field(ge=0)


class G03Rejected(ContractModel):
    schema_id: Literal["gsg.hwp.page-plan-compile-result.v1"] = Field(
        default="gsg.hwp.page-plan-compile-result.v1",
        alias="schema",
    )
    status: Literal["rejected"] = "rejected"
    branch: G03Branch
    plan: None = None
    error: G03Error
    deterministic_result_sha256: Sha256
    response_budget_bytes: int = Field(default=G03_RESPONSE_BUDGET_BYTES, ge=1)
    response_size_bytes: int = Field(ge=0)


G03CompileResponse = Annotated[
    G03Accepted | G03Rejected,
    Field(discriminator="status"),
]


def grounding_matches_observation(
    grounding: G03GroundingProvenance,
    observation: G01Observation,
) -> bool:
    return (
        grounding.document_revision_hash == observation.document_revision_hash
        and grounding.page_setup_hash == observation.page_setup_hash
        and grounding.convention_profile_hash == observation.convention_profile_hash
        and grounding.body_geometry_hash == observation.body_geometry_hash
    )


def canonical_compile_request_sha256(request: G03CompileRequest) -> str:
    return sha256_bytes(
        canonical_json_bytes(request.model_dump(mode="json", by_alias=True))
    )


def accepted_evidence_hash_payload(result: G03Accepted) -> dict[str, JsonValue]:
    payload = result.model_dump(mode="json", by_alias=True)
    return {
        "branch": payload["branch"],
        "branch_receipt": payload["branch_receipt"],
        "guide_masks": payload["guide_masks"],
        "analyses": payload["analyses"],
        "snaps": payload["snaps"],
        "mapping_receipt": payload["mapping_receipt"],
    }


def canonical_accepted_evidence_sha256(result: G03Accepted) -> str:
    return sha256_bytes(canonical_json_bytes(accepted_evidence_hash_payload(result)))


def accepted_hash_payload(result: G03Accepted) -> dict[str, JsonValue]:
    payload = result.model_dump(mode="json", by_alias=True)
    payload.pop("deterministic_result_sha256", None)
    payload.pop("response_size_bytes", None)
    return payload


def rejected_hash_payload(result: G03Rejected) -> dict[str, JsonValue]:
    payload = result.model_dump(mode="json", by_alias=True)
    payload.pop("deterministic_result_sha256", None)
    payload.pop("response_size_bytes", None)
    return payload


__all__ = [
    "G03Accepted",
    "G03AnalysisReceipt",
    "G03BlockBoxMapping",
    "G03Branch",
    "G03BranchReceipt",
    "G03CompileRequest",
    "G03CompileResponse",
    "G03Error",
    "G03ErrorCode",
    "GeneratedG03Request",
    "G03GroundingProvenance",
    "G03GuideMaskReceipt",
    "G03GuideMetadata",
    "G03MappingInput",
    "G03MappingReceipt",
    "G03ReferencePage",
    "G03Rejected",
    "G03SnapReceipt",
    "StructuredG03Request",
    "GeneratedG03Request",
    "StructuredG03Request",
    "accepted_hash_payload",
    "canonical_accepted_evidence_sha256",
    "canonical_compile_request_sha256",
    "canonical_mapping_sha256",
    "grounding_matches_observation",
    "rejected_hash_payload",
]

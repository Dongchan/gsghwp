from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import Field, JsonValue, model_validator

from hwp_live_values import ContractModel
from hwp_pageplan_assets import SourceRegistry
from hwp_pageplan_contract import Sha256
from hwp_pageplan_g03_contract import G03Accepted


G04_SCHEMA_VERSION = "gsg.hwp.page-plan-apply.v1"
G04_ATOMIC_BOUNDARY = "one_native_append_batch"


G04ErrorCode = Literal[
    "MULTI_PAGE_REQUIRES_G05",
    "G03_PROVENANCE_INVALID",
    "DOCUMENT_SELECTOR_MISMATCH",
    "DOCUMENT_UNAVAILABLE",
    "STALE_GROUNDING",
    "PAGE_SETUP_MISMATCH",
    "BODY_GEOMETRY_MISMATCH",
    "CONVENTION_MISMATCH",
    "SOURCE_HASH_MISMATCH",
    "NON_FINAL_INSERTABLE_ASSET",
    "UNSUPPORTED_LAYOUT_VOCABULARY",
    "LAYOUT_OVERFLOW",
    "ATOMIC_EXECUTION_FAILED",
    "EMBEDDED_BYTES_MISMATCH",
    "ASPECT_RATIO_MISMATCH",
    "ROLLBACK_FAILED",
    "UNDO_ENTRY_MISSING",
    "RENDER_FAILED",
    "CHECKPOINT_REQUIRED",
    "NATIVE_ERROR",
]


class G04Error(ContractModel):
    code: G04ErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    block_id: str | None = None
    slot_id: int | None = Field(default=None, ge=1)
    source_ref: str | None = None
    expected: JsonValue | None = None
    actual: JsonValue | None = None


class G04ApplyRequest(ContractModel):
    schema_id: Literal["gsg.hwp.page-plan-apply.v1"] = Field(
        default=G04_SCHEMA_VERSION,
        alias="schema",
    )
    document_selector: str = Field(min_length=1, max_length=32_767)
    operation_id: str = Field(min_length=1, max_length=128)
    compiled: G03Accepted = Field(alias="g03_result")
    source_registry: SourceRegistry
    render_dpi: int = Field(default=144, ge=72, le=600)

    @model_validator(mode="before")
    @classmethod
    def accept_compiled_result_aliases(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw = cast(dict[object, object], value)
        payload: dict[str, object] = {}
        for key, item in raw.items():
            if not isinstance(key, str):
                return cast(object, value)
            payload[key] = item
        if "g03_result" not in payload:
            for alias in ("compile_result", "compiled_result", "compiled"):
                if alias in payload:
                    payload["g03_result"] = payload.pop(alias)
                    break
        return payload


class G04SourceEvidence(ContractModel):
    source_ref: str
    source_kind: Literal["text", "table", "image"]
    expected_sha256: Sha256
    verified_sha256: Sha256
    final_insertable: bool = True


class G04EmbeddedAssetEvidence(ContractModel):
    source_ref: str
    expected_sha256: Sha256
    embedded_sha256s: tuple[Sha256, ...] = ()
    new_bin_items: tuple[int, ...] = ()
    byte_fidelity: bool
    expected_aspect_ratio: float = Field(gt=0)
    observed_aspect_ratios: tuple[float, ...] = ()
    aspect_preserved: bool


class G04RenderEvidence(ContractModel):
    path: Path
    sha256: Sha256
    page: int = Field(ge=1)
    dpi: int = Field(ge=72, le=600)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    render_count: Literal[1] = 1


# 적용된 쓰기가 MCP 되돌리기 이력에 남긴 것. 있으면 있는 대로, 없으면 없는 대로.
#
# `entry_created`/`one_step_supported` 가 false 이고 `capture_method` 가 null 인
# 것은 실패가 아니라 완전하고 정직한 답이다. 레이아웃 쓰기는 선불 문서
# 체크포인트를 뜨지 않으므로(b733c58) 되짚을 MCP 기록 자체가 없고, 복구 경로는
# 한/글 자신의 이력(Ctrl+Z)이다. `applied` 는 이 값들이 참일 것을 요구하지 않는다
# — `validate_status` 를 보라.
#
# 여기에 독스트링을 달면 안 된다. 모델 독스트링은 공개 JSON 스키마의
# `description` 이 되고, `hwp_apply_page_plan` 의 outputSchema 는
# compatibility-manifest.json 의 `legacy_tool_schema_hashes` 로 봉인돼 있다.
class G04UndoEvidence(ContractModel):
    entry_created: bool
    one_step_supported: bool
    capture_method: Literal["document_file", "encoded_block"] | None = None
    document_identity_restored: bool | None = None


class G04RollbackEvidence(ContractModel):
    attempted: bool
    complete: bool
    content_restored: bool | None = None
    structure_restored: bool | None = None
    page_count_restored: bool | None = None
    embedded_bytes_restored: bool | None = None


class G04ApplyResponse(ContractModel):
    schema_id: Literal["gsg.hwp.page-plan-apply-result.v1"] = Field(
        default="gsg.hwp.page-plan-apply-result.v1",
        alias="schema",
    )
    status: Literal["applied", "rejected", "rolled_back"]
    operation_id: str = Field(min_length=1, max_length=128)
    candidate: Literal["A", "B"]
    plan_sha256: Sha256
    source_manifest_sha256: Sha256
    changed: bool = False
    atomic_boundary: Literal["one_native_append_batch"] | None = None
    native_batch_count: int = Field(default=0, ge=0)
    commands_executed: int = Field(default=0, ge=0)
    page_count_before: int | None = Field(default=None, ge=1)
    page_count_after: int | None = Field(default=None, ge=1)
    before_content_signature: str | None = None
    after_content_signature: str | None = None
    source_evidence: tuple[G04SourceEvidence, ...] = ()
    embedded_assets: tuple[G04EmbeddedAssetEvidence, ...] = ()
    render: G04RenderEvidence | None = None
    undo: G04UndoEvidence | None = None
    rollback: G04RollbackEvidence | None = None
    error: G04Error | None = None

    @model_validator(mode="after")
    def validate_status(self) -> G04ApplyResponse:
        # `applied` 는 쓰기가 실제로 일어났다는 증거(한 쪽 증가, 원자 경계, 최종
        # 렌더, 그림 바이트)를 요구한다. MCP 되돌리기 기록은 그 증거가 아니라
        # 부수 장부라서, `undo.entry_created` 가 거짓이어도 성공은 성공이다.
        # 이 관문이 그것을 요구하던 동안 G04 는 성공을 한 번도 반환하지 못했다:
        # 증거원인 선불 체크포인트가 b733c58 에서 사라졌기 때문이다.
        if self.status == "applied":
            if (
                not self.changed
                or self.error is not None
                or self.rollback is not None
                or self.atomic_boundary != G04_ATOMIC_BOUNDARY
                or self.native_batch_count != 1
                or self.render is None
                or self.undo is None
            ):
                raise ValueError(
                    "applied G04 result must contain complete success evidence"
                )
            if (
                self.page_count_before is None
                or self.page_count_after != self.page_count_before + 1
            ):
                raise ValueError("applied G04 result must add exactly one page")
            if any(
                not asset.byte_fidelity or not asset.aspect_preserved
                for asset in self.embedded_assets
            ):
                raise ValueError("applied G04 result contains failed image evidence")
        elif self.status == "rejected":
            if (
                self.changed
                or self.error is None
                or self.rollback is not None
                or self.native_batch_count != 0
                or self.commands_executed != 0
                or self.render is not None
                or self.undo is not None
            ):
                raise ValueError("rejected G04 result must be a pre-write failure")
        elif self.error is None or self.rollback is None:
            # `rollback.attempted` 는 더 이상 여기서 요구되지 않는다. 요구하던
            # 동안 그 값은 상수 True 였고 — 거짓이어서는 응답을 만들 수 없었으니
            # — 편집 전 체크포인트가 없어 복원이 아예 돌지 않은 실패까지
            # "되돌리기를 시도했다"고 말했다. 관문이 요구하는 것은 실패 증거와
            # 되돌리기 증거가 **있다**는 것이고, 그 증거가 "시도하지 못했다"고
            # 말하는 것은 빠진 증거가 아니라 하나의 답이다.
            raise ValueError(
                "rolled_back G04 result must contain failure and rollback evidence"
            )
        return self


G04ApplyResult = Annotated[
    G04ApplyResponse,
    Field(discriminator="status"),
]


__all__ = [
    "G04_ATOMIC_BOUNDARY",
    "G04ApplyRequest",
    "G04ApplyResponse",
    "G04ApplyResult",
    "G04EmbeddedAssetEvidence",
    "G04Error",
    "G04ErrorCode",
    "G04RenderEvidence",
    "G04RollbackEvidence",
    "G04_SCHEMA_VERSION",
    "G04SourceEvidence",
    "G04UndoEvidence",
]

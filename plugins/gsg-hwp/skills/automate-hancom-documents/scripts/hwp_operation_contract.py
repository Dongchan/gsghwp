from __future__ import annotations

# noqa: SIZE_OK — public declarative contract surface; splitting would add re-export-only modules

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, RootModel

from hwp_live_contract import LayoutPlan
from hwp_live_native_action_models import NativeActionCommand
from hwp_live_values import ContractModel
from hwp_operation_route_contract import (
    HwpWorkflowId as HwpWorkflowId,
    OperationRouteMetadata,
)
from hwp_operation_descriptor import OperationVerificationMode
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_runtime_identity import RUNTIME_BUILD_INFO, RuntimeBuildInfo


OperationCategory = Literal["action", "parameter_set", "automation"]
OperationMatchKind = Literal["operation_id", "name", "alias", "search"]
OperationExecutionPolicy = Literal[
    "automatic",
    "document_change",
    "blocked",
    "catalog_only",
]
OperationNativeExecution = Literal[
    "run_action",
    "parameter_action",
    "blocked",
    "catalog_only",
]
OperationLiveStatus = Literal["passed", "known_failure"]
OperationResolutionStatus = Literal["resolved", "ambiguous", "not_found"]
WorkflowResolutionStatus = Literal[
    "resolved",
    "ambiguous",
    "not_found",
    "schema_conflict",
]
HwpRecipeFamilyId = Literal[
    "table.fill_with_images",
    "image.insert_or_replace",
    "caption.add_or_update",
    "style.copy_and_apply",
    "page.append_from_template",
]
type HwpRequestedOperation = HwpWorkflowId | HwpRecipeFamilyId
WorkflowMatchKind = Literal["explicit", "alias", "semantic"]
WorkflowExecution = Literal["recipe", "official", "catalog_only"]
OperationStatus = Literal[
    "executed",
    "resolved",
    "ambiguous",
    "not_found",
    "needs_guard",
    "needs_input",
    "confirmation_required",
    "blocked",
    "known_failure",
    "unsupported",
    "schema_conflict",
    "operation_in_progress",
    "operation_stale",
    "operation_failed",
    "operation_aborted",
    "operation_reconciled",
    "request_id_conflict",
    "partial_change",
    "transport_error",
]
IdempotencyStatus = Literal[
    "not_requested",
    "committed",
    "replayed",
    "in_progress",
    "stale",
    "failed",
    "aborted",
    "reconciled",
    "conflict",
]
OperationJournalState = Literal[
    "accepted",
    "executing",
    "verified",
    "committed",
    "failed",
    "aborted",
]
type OperationInputValue = str | int | float | bool


class OperationNextArguments(RootModel[dict[str, JsonValue]]):
    pass


class OperationInputSpec(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    value_type: str = Field(min_length=1, max_length=50)
    subtype: str | None = Field(default=None, max_length=100)
    description: str = Field(max_length=20_000)
    supported: bool


class OperationCandidate(ContractModel):
    operation_id: str = Field(min_length=1, max_length=300)
    category: OperationCategory
    ordinal: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    owner: str | None = Field(default=None, max_length=100)
    member_kind: str | None = Field(default=None, max_length=30)
    description: str = Field(max_length=20_000)
    declaration: str | None = Field(default=None, max_length=20_000)
    parameter_set: str | None = Field(default=None, max_length=100)
    inputs: tuple[OperationInputSpec, ...] = ()
    execution_policy: OperationExecutionPolicy
    native_execution: OperationNativeExecution
    latest_live_status: OperationLiveStatus = "passed"
    known_failure_reason: str | None = Field(default=None, max_length=2_000)
    recommended_tool: str | None = Field(default=None, max_length=100)
    source_document: str = Field(min_length=1, max_length=200)
    source_page_start: int = Field(ge=1)
    source_page_end: int = Field(ge=1)
    confidence: float = Field(default=0, ge=0, le=1)
    match_kind: OperationMatchKind = "search"


class OperationResolution(ContractModel):
    query: str = Field(min_length=1, max_length=500)
    status: OperationResolutionStatus
    registry_entries: int = Field(ge=1)
    lookup_microseconds: int = Field(ge=0)
    operation: OperationCandidate | None = None
    candidates: tuple[OperationCandidate, ...] = Field(max_length=24)


class OperationIndexResource(ContractModel):
    schema_version: Literal[1]
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[OperationCandidate, ...] = Field(min_length=1)


class OperationPosition(ContractModel):
    list_id: int = Field(ge=0)
    paragraph: int = Field(ge=0)
    character: int = Field(ge=0)


class WorkflowCandidate(ContractModel):
    workflow_id: HwpWorkflowId
    description: str = Field(min_length=1, max_length=500)
    steps: tuple[str, ...] = Field(max_length=20)
    execution: WorkflowExecution
    confidence: float = Field(ge=0, le=1)
    match_kind: WorkflowMatchKind


class WorkflowResolution(ContractModel):
    query: str = Field(min_length=1, max_length=500)
    status: WorkflowResolutionStatus
    lookup_microseconds: int = Field(ge=0)
    workflow_id: HwpWorkflowId | None = None
    candidates: tuple[WorkflowCandidate, ...] = Field(default=(), max_length=24)
    steps: tuple[str, ...] = Field(default=(), max_length=20)
    match_kind: WorkflowMatchKind | None = None


class HwpOperateTarget(ContractModel):
    kind: Literal["document", "page", "selection", "table", "picture", "control"]
    binding: Literal["active", "selection", "previous_result", "below_selection"] = (
        "active"
    )
    scope: Literal["target", "page", "selection", "document"] = "target"
    page_hint: int | None = Field(default=None, ge=1)
    table_index: int | None = Field(default=None, ge=1)
    caption_contains: str | None = Field(default=None, min_length=1, max_length=500)
    header_signature: tuple[str, ...] = Field(default=(), max_length=100)
    match_policy: Literal["unique", "first", "all", "return_candidates"] = "unique"
    control_instance_id: str | None = Field(default=None, min_length=1, max_length=100)
    control_instance_ids: tuple[str, ...] = Field(default=(), max_length=100)


class HwpOperateData(ContractModel):
    cells: dict[str, str] = Field(default_factory=dict, max_length=20_000)
    rows: tuple[tuple[str, ...], ...] = Field(default=(), max_length=20_000)
    records: tuple[dict[str, str], ...] = Field(default=(), max_length=20_000)
    start_cell: str | None = Field(default=None, min_length=2, max_length=20)


class HwpOperateAssets(ContractModel):
    images: dict[str, Path] = Field(default_factory=dict, max_length=20_000)


class HwpOperatePolicy(ContractModel):
    preserve_style: bool = True
    preserve_existing_images: bool = True
    fill_blanks_only: bool = False
    allow_row_expansion: bool = False
    ambiguity: Literal["return_candidates", "unsupported"] = "return_candidates"
    numeric_value_mode: Literal["infer", "display", "base"] = "infer"
    atomic: bool = False


class HwpOperateRecovery(ContractModel):
    action: Literal["recover", "reconcile"]
    confirmed: Literal[True]


class HwpOperatePostconditions(ContractModel):
    record_count: int | None = Field(default=None, ge=0, le=20_000)
    verify_structure: bool = True
    preserve_page_count: bool = False


class OperationRoutingContext(ContractModel):
    document_id: int = Field(ge=0)
    full_name: str = Field(max_length=32_767)
    page: int = Field(ge=1)
    page_count: int = Field(ge=1)
    text_characters: int = Field(ge=0)
    control_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    picture_count: int = Field(ge=0)
    table_instance_ids: tuple[str, ...] = Field(default=(), max_length=20_000)
    native_elapsed_microseconds: int = Field(ge=0)


class WorkflowTargetCandidate(ContractModel):
    candidate_id: str | None = Field(
        default=None,
        pattern=r"^(table|picture):[1-9][0-9]*:[1-9][0-9]*$",
    )
    kind: Literal["table", "picture"] = "table"
    page: int = Field(ge=1)
    table_index: int | None = Field(default=None, ge=1)
    picture_index: int | None = Field(default=None, ge=1)
    rows: int | None = Field(default=None, ge=1)
    columns: int | None = Field(default=None, ge=1)
    caption: str | None = Field(default=None, max_length=2_000)
    header_preview: tuple[str, ...] = Field(default=(), max_length=100)


class TableFormatCandidate(ContractModel):
    address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    numeric_value_mode: Literal["display", "base"]
    replacement: str = Field(max_length=200_000)
    inferred_scale: int = Field(ge=1)
    evidence: tuple[str, ...] = Field(default=(), max_length=20)


class TextMatchCandidate(ContractModel):
    occurrence: int = Field(ge=1)
    start: OperationPosition
    end: OperationPosition
    matched_text: str = Field(max_length=1_000_000)


class HwpOperateInputs(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    document: str | None = Field(default=None, max_length=32_767)
    operation: HwpRequestedOperation | None = Field(
        default=None,
        description="Authoritative canonical operation. When omitted, only an exact deterministic route may execute a certified recipe.",
    )
    target: HwpOperateTarget | None = None
    data: HwpOperateData | None = None
    assets: HwpOperateAssets | None = None
    policy: HwpOperatePolicy = Field(default_factory=HwpOperatePolicy)
    postconditions: HwpOperatePostconditions = Field(
        default_factory=HwpOperatePostconditions
    )
    parameters: dict[str, OperationInputValue] = Field(
        default_factory=dict,
        max_length=500,
    )
    layout: LayoutPlan | None = None
    recipe: HwpPriorityRecipeInputs | None = None
    use_defaults: bool = False
    recovery: HwpOperateRecovery | None = None


def canonical_workflow(inputs: HwpOperateInputs) -> HwpWorkflowId | None:
    operation = inputs.operation
    if operation == "table.fill_with_images":
        return "table.insert_images"
    if operation == "caption.add_or_update":
        return "caption.add"
    if operation == "page.append_from_template":
        return "document.append_layout"
    if operation == "image.insert_or_replace":
        target = inputs.target
        return "image.replace" if target is not None and target.kind == "picture" else "image.insert"
    if operation == "style.copy_and_apply":
        recipe = inputs.recipe
        return "style.copy" if recipe is not None and recipe.source_position is not None else "style.apply"
    return operation


class HwpOperateGuards(ContractModel):
    cursor: OperationPosition | None = None


class OperationResult(OperationRouteMetadata):
    schema_version: Literal["1.0"] = "1.0"
    runtime: RuntimeBuildInfo = RUNTIME_BUILD_INFO
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    idempotency_status: IdempotencyStatus = "not_requested"
    result_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    journal_state: OperationJournalState | None = None
    journal_attempt: int | None = Field(default=None, ge=1)
    started_at: datetime | None = None
    updated_at: datetime | None = None
    stale_after_seconds: int | None = Field(default=None, ge=1)
    journal_failure_code: str | None = Field(default=None, min_length=1, max_length=200)
    status: OperationStatus
    changed: bool = False
    query: str = Field(min_length=1, max_length=500)
    registry_entries: int = Field(ge=1)
    lookup_microseconds: int = Field(ge=0)
    operation: OperationCandidate | None = None
    candidates: tuple[OperationCandidate, ...] = Field(default=(), max_length=24)
    workflow_candidates: tuple[WorkflowCandidate, ...] = Field(
        default=(),
        max_length=24,
    )
    target_candidates: tuple[WorkflowTargetCandidate, ...] = Field(
        default=(),
        max_length=3,
    )
    format_candidates: tuple[TableFormatCandidate, ...] = Field(
        default=(),
        max_length=24,
    )
    text_candidates: tuple[TextMatchCandidate, ...] = Field(default=(), max_length=24)
    required_inputs: tuple[str, ...] = Field(default=(), max_length=20)
    missing_fields: tuple[str, ...] = Field(default=(), max_length=20)
    routing_context: OperationRoutingContext | None = None
    failure_stage: str | None = Field(default=None, max_length=200)
    next_tool: str | None = Field(default=None, min_length=1, max_length=100)
    next_arguments: OperationNextArguments | None = None
    message: str = Field(max_length=4_000)
    execution_mode: Literal["native_in_process"] | None = None
    native_protocol: Literal[9, 10, 11, 12] | None = None
    verification: OperationVerificationMode | None = None
    verified: bool | None = None
    commands_executed: int | None = Field(default=None, ge=0)
    native_actions_executed: int | None = Field(default=None, ge=0)
    native_elapsed_microseconds: int | None = Field(default=None, ge=0)
    caption_profile_elapsed_microseconds: int | None = Field(default=None, ge=0)
    clone_elapsed_microseconds: int | None = Field(default=None, ge=0)
    caption_elapsed_microseconds: int | None = Field(default=None, ge=0)
    content_elapsed_microseconds: int | None = Field(default=None, ge=0)
    image_timing_count: int | None = Field(default=None, ge=0)
    image_maximum_microseconds: int | None = Field(default=None, ge=0)
    image_total_microseconds: int | None = Field(default=None, ge=0)
    current_page: int | None = Field(default=None, ge=1)
    page_count: int | None = Field(default=None, ge=1)
    modified: bool | None = None
    saved_path: str | None = Field(default=None, max_length=32_767)
    reopened_path: str | None = Field(default=None, max_length=32_767)
    before_page_count: int | None = Field(default=None, ge=1)
    before_modified: bool | None = None
    before_control_count: int | None = Field(default=None, ge=0)
    before_control_hash: str | None = Field(default=None, max_length=500)
    before_text_hash: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    before_document_hash: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    save_hresult: int | None = None
    save_return: int | None = Field(default=None, ge=-1, le=1)
    post_save_modified: bool | None = None
    clear_hresult: int | None = None
    clear_return: int | None = Field(default=None, ge=-1, le=1)
    open_hresult: int | None = None
    open_return: int | None = Field(default=None, ge=-1, le=1)
    session_recovered: bool | None = None
    recovery_hresult: int | None = None
    recovery_return: int | None = Field(default=None, ge=-1, le=1)
    after_page_count: int | None = Field(default=None, ge=1)
    after_modified: bool | None = None
    after_control_count: int | None = Field(default=None, ge=0)
    after_control_hash: str | None = Field(default=None, max_length=500)
    after_text_hash: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    after_document_hash: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    save_baseline_file_size: int | None = Field(default=None, ge=0)
    save_baseline_file_mtime_ns: int | None = Field(default=None, ge=0)
    save_baseline_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    saved_file_size: int | None = Field(default=None, ge=0)
    saved_file_write_time_100ns: int | None = Field(default=None, ge=0)
    saved_file_mtime_ns: int | None = Field(default=None, ge=0)
    saved_file_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    save_fingerprint_stable: bool | None = None
    save_fingerprint_changed: bool | None = None
    save_fingerprint_verified: bool | None = None
    live_state_preserved_after_save: bool | None = None
    disk_persistence_verified: bool | None = None
    structure_digest_before: str | None = Field(default=None, max_length=500)
    structure_digest_after: str | None = Field(default=None, max_length=500)
    partial_change: bool = False
    partial_mutation: bool | None = None
    retry_safe: bool | None = None
    reconcile_required: bool = False
    failed_step: str | None = Field(default=None, max_length=200)
    commands_completed: int | None = Field(default=None, ge=0)
    resolved_target_id: str | None = Field(default=None, max_length=500)
    target_resolution_basis: str | None = Field(default=None, max_length=500)
    cursor_before: OperationPosition | None = None
    cursor_after: OperationPosition | None = None
    recipe_id: str | None = Field(default=None, max_length=100)
    recipe_steps: tuple[str, ...] = Field(default=(), max_length=20)
    blocks_applied: int | None = Field(default=None, ge=0)
    created_control_ids: tuple[str, ...] = Field(default=(), max_length=500)
    updated_addresses: tuple[str, ...] = Field(default=(), max_length=20_000)


@dataclass(frozen=True, slots=True)
class OperationCommandPlan:
    status: Literal["ready", "needs_input", "unsupported"]
    command: NativeActionCommand | None
    message: str

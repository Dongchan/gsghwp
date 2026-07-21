from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeGuard

from hwp_live_native_action_models import NativePageInspection
from hwp_live_native_format_commands import NativeFormatCommandPlan
from hwp_live_rot import HwpDocumentCandidate
from hwp_operation_contract import (
    HwpOperatePostconditions,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationInputValue,
    WorkflowResolution,
)


type NativeFormatWorkflow = Literal[
    "text.format",
    "table.format",
    "table.merge_cells",
    "table.split_cells",
]

_WORKFLOWS = frozenset[NativeFormatWorkflow](
    ("text.format", "table.format", "table.merge_cells", "table.split_cells")
)


@dataclass(frozen=True, slots=True)
class NativeFormatRecipeRequest:
    candidate: HwpDocumentCandidate
    routing_page: NativePageInspection
    resolution: WorkflowResolution
    target: HwpOperateTarget | None
    parameters: Mapping[str, OperationInputValue]
    postconditions: HwpOperatePostconditions
    resolve_only: bool
    allow_document_change: bool


@dataclass(frozen=True, slots=True)
class PreparedFormatOperation:
    plan: NativeFormatCommandPlan
    target_id: str
    target_basis: str
    updated_addresses: tuple[str, ...]


def is_native_format_workflow(
    workflow: HwpWorkflowId | None,
) -> TypeGuard[NativeFormatWorkflow]:
    return workflow in _WORKFLOWS

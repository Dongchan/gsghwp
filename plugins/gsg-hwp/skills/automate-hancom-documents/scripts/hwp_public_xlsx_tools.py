from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import final

from pydantic import JsonValue, TypeAdapter

from hwp_public_action_contract import (
    PublicOperationId,
    PublicTextPatchBatch,
    PublicTextPatchItem,
)
from hwp_public_contract import PublicActionResult
from hwp_workflow_query import build_workflow_plan
from hwp_workflow_query_contract import WorkflowRequest


_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_PATCH_BATCH = TypeAdapter[tuple[PublicTextPatchItem, ...]](PublicTextPatchBatch)
type BatchMutation = Callable[..., Awaitable[PublicActionResult]]


@final
class HwpPublicXlsxTools:
    """Resolve workbook values and consume them without crossing the MCP boundary."""

    __slots__ = ("_patch_batch",)

    def __init__(self, patch_batch: BatchMutation) -> None:
        self._patch_batch = patch_batch

    async def hwp_patch_text_from_xlsx(
        self,
        *,
        operation_id: PublicOperationId,
        workflow: WorkflowRequest,
    ) -> PublicActionResult:
        plan = build_workflow_plan(workflow)
        payload = _JSON_OBJECT.validate_python(plan["payload"])
        patches = _PATCH_BATCH.validate_python(payload["patches"])
        document = workflow.projection.document
        return await self._patch_batch(
            operation_id=operation_id,
            patches=patches,
            formatting=workflow.formatting,
            document_path=workflow.document_path or document.full_name,
            expected_document_id=document.document_id,
            expected_document_full_name=document.full_name,
            expected_content_revision=document.content_revision,
        )

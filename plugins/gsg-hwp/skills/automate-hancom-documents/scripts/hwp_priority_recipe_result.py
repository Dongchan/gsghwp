from __future__ import annotations

from hwp_operation_certification import certified_recipe
from hwp_operation_contract import (
    OperationResult,
    OperationStatus,
    WorkflowResolution,
)
from hwp_operation_registry import operation_registry


def priority_recipe_result(
    resolution: WorkflowResolution,
    status: OperationStatus,
    message: str,
    *,
    required_inputs: tuple[str, ...] = (),
) -> OperationResult:
    workflow = resolution.workflow_id
    recipe = None if workflow is None else certified_recipe(workflow)
    return OperationResult(
        status=status,
        query=resolution.query,
        registry_entries=operation_registry().count,
        lookup_microseconds=resolution.lookup_microseconds,
        workflow_candidates=resolution.candidates,
        required_inputs=required_inputs,
        message=message,
        recipe_id=None if recipe is None else f"recipe:{recipe.recipe_id}",
        recipe_steps=resolution.steps,
    )

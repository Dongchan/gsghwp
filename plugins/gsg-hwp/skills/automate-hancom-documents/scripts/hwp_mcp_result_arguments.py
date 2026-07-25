from __future__ import annotations

from typing import Literal

from pydantic import TypeAdapter

from hwp_mcp_registry import tool_specs
from hwp_operation_contract import (
    HwpOperateInputs,
    canonical_workflow,
)


type ProductionWorkflowId = Literal[
    "table.fill_existing",
    "table.expand_and_fill",
    "table.repeat_template",
    "image.insert",
    "image.replace",
    "table.build_series",
    "table.insert_images",
    "caption.add",
    "style.copy",
    "style.apply",
    "text.format",
    "table.format",
    "table.merge_cells",
    "table.split_cells",
    "document.append_layout",
    "document.insert_layout",
    "document.save",
    "document.save_reopen_verify",
]


_WORKFLOW: TypeAdapter[ProductionWorkflowId] = TypeAdapter(ProductionWorkflowId)


class ProductionResultContractError(RuntimeError):
    pass


def production_workflow(inputs: HwpOperateInputs) -> ProductionWorkflowId:
    workflow = canonical_workflow(inputs)
    if workflow is None:
        raise ProductionResultContractError(
            "production wrapper has no canonical workflow"
        )
    return _WORKFLOW.validate_python(workflow)


def production_tool_name(workflow: ProductionWorkflowId) -> str:
    matches = tuple(
        spec.name for spec in tool_specs("production") if spec.workflow == workflow
    )
    if len(matches) != 1:
        raise ProductionResultContractError(
            f"production workflow has {len(matches)} tools: {workflow}"
        )
    return matches[0]


def _public_field(
    workflow: ProductionWorkflowId,
    canonical: str,
) -> tuple[str, ...]:
    if canonical in {"inputs.target", "inputs.target.control_instance_id"}:
        return ("target.candidate_id",)
    if canonical == "inputs.data":
        return ("data",)
    if canonical in {"inputs.assets", "inputs.assets.images"}:
        return ("images.images",) if workflow == "table.insert_images" else ("image",)
    if canonical in {"inputs.layout"}:
        return ("layout",)
    if canonical in {"inputs.recipe.table_template", "inputs.recipe"}:
        if workflow in {"table.repeat_template", "table.build_series"}:
            return ("table_template",)
        if workflow == "caption.add":
            return ("caption",)
        if workflow == "style.copy":
            return ("style",)
        if workflow == "style.apply":
            return ("style_id", "target_position")
    if canonical == "inputs.recipe.table_template.blocks":
        return ("table_template.blocks",)
    if canonical == "inputs.recipe.caption_text":
        return ("caption.caption_text",)
    if canonical == "inputs.recipe.style_id":
        return ("style_id",)
    if canonical == "inputs.recipe.source_position":
        return ("style.source_position",)
    if canonical == "inputs.recipe.target_position":
        return (
            ("style.target_position",)
            if workflow == "style.copy"
            else ("target_position",)
        )
    if canonical == "inputs.parameters":
        if workflow == "text.format" or workflow == "table.format":
            return ("formatting",)
        if workflow == "table.merge_cells":
            return ("cells",)
        if workflow == "table.split_cells":
            return ("split",)
    if canonical.startswith("inputs.parameters."):
        name = canonical.removeprefix("inputs.parameters.")
        if workflow == "text.format" or workflow == "table.format":
            return (f"formatting.{name}",)
        if workflow == "table.merge_cells":
            return (f"cells.{name}",)
        if workflow == "table.split_cells":
            return (f"split.{name}",)
    return (canonical.removeprefix("inputs."),)


def public_missing_fields(
    workflow: ProductionWorkflowId,
    canonical_fields: tuple[str, ...],
) -> tuple[str, ...]:
    fields: list[str] = []
    for canonical in canonical_fields:
        for field in _public_field(workflow, canonical):
            if field not in fields:
                fields.append(field)
    return tuple(fields)

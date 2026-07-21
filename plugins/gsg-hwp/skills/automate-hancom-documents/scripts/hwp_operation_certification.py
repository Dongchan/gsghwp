from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — declarative certification registry is one reviewable data table

from dataclasses import dataclass
from typing import Final, Literal

from hwp_operation_contract import HwpWorkflowId, OperationCandidate


RollbackBehavior = Literal[
    "none",
    "no_automatic_rollback",
    "automatic_undo_on_postcondition_failure",
]
RecipeAtomicity = Literal["native_batch", "transactional"]


@dataclass(frozen=True, slots=True)
class CertifiedPrimitive:
    primitive_id: str
    preconditions: tuple[str, ...]
    effects: tuple[str, ...]
    input_schema: tuple[str, ...]
    supported_hwp_builds: tuple[str, ...]
    supported_formats: tuple[str, ...]
    rollback_behavior: RollbackBehavior
    postconditions: tuple[str, ...]
    known_failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CertifiedRecipe:
    recipe_id: str
    workflow_id: HwpWorkflowId
    version: int
    steps: tuple[str, ...]
    atomicity: RecipeAtomicity


_BUILDS: Final = ("Hancom Office Hwp 2024+ Win32 Automation",)
_FORMATS: Final = ("hwp", "hwpx")

CERTIFIED_PRIMITIVES: Final = {
    "ResolveTable": CertifiedPrimitive(
        "ResolveTable",
        ("one active editable document",),
        ("unique table identity or bounded candidates",),
        ("target",),
        _BUILDS,
        _FORMATS,
        "none",
        ("table identity belongs to the connected document",),
    ),
    "MapRowsToCells": CertifiedPrimitive(
        "MapRowsToCells",
        ("resolved table structure",),
        ("canonical cell-value pairs",),
        ("data", "policy.fill_blanks_only"),
        _BUILDS,
        _FORMATS,
        "none",
        ("every mapped address exists in the resolved table",),
    ),
    "FillCells": CertifiedPrimitive(
        "FillCells",
        ("resolved table control", "canonical cell-value pairs"),
        ("native cell text mutation",),
        ("cell-value pairs",),
        _BUILDS,
        _FORMATS,
        "no_automatic_rollback",
        ("one protocol-9 native batch returns command results",),
    ),
    "VerifyStructure": CertifiedPrimitive(
        "VerifyStructure",
        ("completed native mutation",),
        ("post-mutation structure evidence",),
        ("postconditions",),
        _BUILDS,
        _FORMATS,
        "none",
        ("requested values and structural conditions match",),
    ),
    "MoveDocEnd": CertifiedPrimitive(
        "MoveDocEnd",
        ("one active editable document",),
        ("cursor moves to document end",),
        (),
        _BUILDS,
        _FORMATS,
        "none",
        ("native action succeeds",),
    ),
    "ApplyLayout": CertifiedPrimitive(
        "ApplyLayout",
        ("validated native layout plan",),
        ("native paragraphs, tables, pictures, or page breaks",),
        ("layout",),
        _BUILDS,
        _FORMATS,
        "no_automatic_rollback",
        ("native batch reports every applied block",),
    ),
    "RunPassedOfficialAction": CertifiedPrimitive(
        "RunPassedOfficialAction",
        ("latest live status is passed", "one official Action only"),
        ("one native official Action execution",),
        ("operation_id", "official parameters"),
        _BUILDS,
        _FORMATS,
        "no_automatic_rollback",
        ("protocol-9 native action result is returned",),
    ),
    "ExpandRows": CertifiedPrimitive(
        "ExpandRows", ("resolved table",), ("native styled rows appended",),
        ("data",), _BUILDS, _FORMATS, "no_automatic_rollback",
        ("requested row capacity exists",),
    ),
    "RepeatTemplate": CertifiedPrimitive(
        "RepeatTemplate", ("resolved source table",), ("native table copies",),
        ("recipe.table_template",), _BUILDS, _FORMATS, "no_automatic_rollback",
        ("requested table count exists",),
    ),
    "MapImagesToCells": CertifiedPrimitive(
        "MapImagesToCells", ("resolved table",), ("cell-image mapping",),
        ("assets.images",), _BUILDS, _FORMATS, "none",
        ("every image address exists",),
    ),
    "InsertImages": CertifiedPrimitive(
        "InsertImages", ("resolved native target",), ("native embedded pictures",),
        ("assets.images",), _BUILDS, _FORMATS, "no_automatic_rollback",
        ("inserted picture controls are observable",),
    ),
    "ResolvePicture": CertifiedPrimitive(
        "ResolvePicture", ("one active editable document",), ("one picture target",),
        ("target",), _BUILDS, _FORMATS, "none", ("picture identity is unique",),
    ),
    "PictureChange": CertifiedPrimitive(
        "PictureChange", ("one resolved picture",), ("native picture replacement",),
        ("assets.images",), _BUILDS, _FORMATS, "no_automatic_rollback",
        ("official PictureChange succeeds",),
    ),
    "ResolveControl": CertifiedPrimitive(
        "ResolveControl", ("one active editable document",), ("one control target",),
        ("target",), _BUILDS, _FORMATS, "none", ("control identity is unique",),
    ),
    "ResolvePage": CertifiedPrimitive(
        "ResolvePage", ("one active editable document",), ("one physical page target",),
        ("target.page_hint",), _BUILDS, _FORMATS, "none", ("page is inside the document",),
    ),
    "DeletePage": CertifiedPrimitive(
        "DeletePage", ("one resolved physical page",), ("native page deletion",),
        ("target.page_hint",), _BUILDS, _FORMATS, "no_automatic_rollback",
        ("page count decreases by one",),
    ),
    "DeleteControl": CertifiedPrimitive(
        "DeleteControl", ("resolved control identities on one page",),
        ("native control deletion",), ("target.control_instance_ids",),
        _BUILDS, _FORMATS, "no_automatic_rollback",
        ("deleted control identities are absent",),
    ),
    "ResolveHistory": CertifiedPrimitive(
        "ResolveHistory", ("one active editable document",), ("bounded history direction",),
        ("parameters.steps",), _BUILDS, _FORMATS, "none", ("steps are between one and twenty",),
    ),
    "Undo": CertifiedPrimitive(
        "Undo", ("available edit history",), ("native undo",), ("parameters.steps",),
        _BUILDS, _FORMATS, "no_automatic_rollback", ("native action result succeeds",),
    ),
    "Redo": CertifiedPrimitive(
        "Redo", ("available redo history",), ("native redo",), ("parameters.steps",),
        _BUILDS, _FORMATS, "no_automatic_rollback", ("native action result succeeds",),
    ),
    "VerifySnapshot": CertifiedPrimitive(
        "VerifySnapshot", ("completed native mutation",), ("before and after snapshots",),
        (), _BUILDS, _FORMATS, "none", ("document identity remains stable",),
    ),
    "ApplyCaption": CertifiedPrimitive(
        "ApplyCaption", ("resolved table control",), ("native caption reset and attach",),
        ("recipe.caption_text",), _BUILDS, _FORMATS, "no_automatic_rollback",
        ("caption text is observable",),
    ),
    "ResolvePosition": CertifiedPrimitive(
        "ResolvePosition", ("one active editable document",), ("native text position",),
        ("recipe positions",), _BUILDS, _FORMATS, "none", ("position is valid",),
    ),
    "CopyAndApplyStyle": CertifiedPrimitive(
        "CopyAndApplyStyle", ("resolved source and target positions",),
        ("native character or paragraph style transfer",), ("recipe.style_copy_type",),
        _BUILDS, _FORMATS, "no_automatic_rollback", ("native style action succeeds",),
    ),
    "ApplyStyle": CertifiedPrimitive(
        "ApplyStyle", ("resolved target position",), ("native named style application",),
        ("recipe.style_id",), _BUILDS, _FORMATS, "no_automatic_rollback",
        ("official Style action succeeds",),
    ),
    "ResolveTextRange": CertifiedPrimitive("ResolveTextRange", ("native text selection",), ("one explicit text range",), ("target",), _BUILDS, _FORMATS, "none", ("selection remains in the connected document",)),
    "ApplyTextFormat": CertifiedPrimitive("ApplyTextFormat", ("resolved native text range",), ("native character or paragraph formatting",), ("parameters",), _BUILDS, _FORMATS, "no_automatic_rollback", ("protocol-9 native action result is returned",)),
    "ApplyTableFormat": CertifiedPrimitive("ApplyTableFormat", ("resolved native table and cell",), ("native cell border, fill, or text formatting",), ("target", "parameters"), _BUILDS, _FORMATS, "no_automatic_rollback", ("target table remains structurally observable",)),
    "ResolveCells": CertifiedPrimitive("ResolveCells", ("resolved native table",), ("validated cell address or range",), ("target", "parameters"), _BUILDS, _FORMATS, "none", ("every requested address belongs to the resolved table",)),
    "TableMergeCell": CertifiedPrimitive("TableMergeCell", ("validated rectangular cell range",), ("native merged cell",), ("parameters.start", "parameters.end"), _BUILDS, _FORMATS, "automatic_undo_on_postcondition_failure", ("target table remains structurally observable",)),
    "TableSplitCell": CertifiedPrimitive("TableSplitCell", ("validated native table cell",), ("native split rows and columns",), ("parameters.cell", "parameters.columns", "parameters.rows"), _BUILDS, _FORMATS, "automatic_undo_on_postcondition_failure", ("target table remains structurally observable",)),
}

CERTIFIED_RECIPES: Final = {
    "document.delete_page": CertifiedRecipe(
        "document.delete_page.v1", "document.delete_page", 1,
        ("ResolvePage", "DeletePage", "VerifyStructure"), "native_batch",
    ),
    "document.undo": CertifiedRecipe(
        "document.undo.v1", "document.undo", 1,
        ("ResolveHistory", "Undo", "VerifySnapshot"), "native_batch",
    ),
    "document.redo": CertifiedRecipe(
        "document.redo.v1", "document.redo", 1,
        ("ResolveHistory", "Redo", "VerifySnapshot"), "native_batch",
    ),
    "control.delete": CertifiedRecipe(
        "control.delete.v1", "control.delete", 1,
        ("ResolveControl", "DeleteControl", "VerifyStructure"), "native_batch",
    ),
    "document.append_layout": CertifiedRecipe(
        "page.append_from_template.v1",
        "document.append_layout",
        1,
        ("MoveDocEnd", "ApplyLayout"),
        "transactional",
    ),
    "document.insert_layout": CertifiedRecipe(
        "document.insert_layout.v1",
        "document.insert_layout",
        1,
        ("ApplyLayout",),
        "native_batch",
    ),
    "table.fill_existing": CertifiedRecipe(
        "table.fill_existing.v1",
        "table.fill_existing",
        1,
        ("ResolveTable", "MapRowsToCells", "FillCells", "VerifyStructure"),
        "native_batch",
    ),
    "table.expand_and_fill": CertifiedRecipe(
        "table.expand_and_fill.v1", "table.expand_and_fill", 1,
        ("ResolveTable", "ExpandRows", "MapRowsToCells", "FillCells", "VerifyStructure"),
        "native_batch",
    ),
    "table.repeat_template": CertifiedRecipe(
        "table.repeat_template.v1", "table.repeat_template", 1,
        ("ResolveTable", "RepeatTemplate", "VerifyStructure"), "native_batch",
    ),
    "table.build_series": CertifiedRecipe(
        "table.build_series.v1", "table.build_series", 1,
        ("ResolveTable", "RepeatTemplate", "FillCells", "InsertImages", "VerifyStructure"),
        "native_batch",
    ),
    "table.insert_images": CertifiedRecipe(
        "table.fill_with_images.v1", "table.insert_images", 1,
        ("ResolveTable", "MapImagesToCells", "InsertImages", "VerifyStructure"),
        "native_batch",
    ),
    "image.insert": CertifiedRecipe(
        "image.insert_or_replace.v1", "image.insert", 1,
        ("ResolvePosition", "InsertImages", "VerifyStructure"), "native_batch",
    ),
    "image.replace": CertifiedRecipe(
        "image.insert_or_replace.v1", "image.replace", 1,
        ("ResolvePicture", "PictureChange", "VerifyStructure"), "native_batch",
    ),
    "caption.add": CertifiedRecipe(
        "caption.add_or_update.v1", "caption.add", 1,
        ("ResolveControl", "ApplyCaption", "VerifyStructure"), "native_batch",
    ),
    "style.copy": CertifiedRecipe(
        "style.copy_and_apply.v1", "style.copy", 1,
        ("ResolvePosition", "CopyAndApplyStyle", "VerifyStructure"), "native_batch",
    ),
    "style.apply": CertifiedRecipe(
        "style.copy_and_apply.v1", "style.apply", 1,
        ("ResolvePosition", "ApplyStyle", "VerifyStructure"), "native_batch",
    ),
    "text.format": CertifiedRecipe("text.format.v1", "text.format", 1, ("ResolveTextRange", "ApplyTextFormat"), "native_batch"),
    "table.format": CertifiedRecipe("table.format.v1", "table.format", 1, ("ResolveTable", "ApplyTableFormat", "VerifyStructure"), "native_batch"),
    "table.merge_cells": CertifiedRecipe("table.merge_cells.v1", "table.merge_cells", 1, ("ResolveTable", "ResolveCells", "TableMergeCell", "VerifyStructure"), "native_batch"),
    "table.split_cells": CertifiedRecipe("table.split_cells.v1", "table.split_cells", 1, ("ResolveTable", "ResolveCells", "TableSplitCell", "VerifyStructure"), "native_batch"),
}


def certified_recipe(workflow_id: HwpWorkflowId) -> CertifiedRecipe | None:
    recipe = CERTIFIED_RECIPES.get(workflow_id)
    if recipe is None:
        return None
    if any(step not in CERTIFIED_PRIMITIVES for step in recipe.steps):
        return None
    return recipe


def certified_atomic_action(
    operation: OperationCandidate,
) -> CertifiedPrimitive | None:
    if (
        operation.category != "action"
        or operation.latest_live_status != "passed"
        or operation.native_execution not in {"run_action", "parameter_action"}
        or operation.execution_policy in {"blocked", "catalog_only"}
    ):
        return None
    return CERTIFIED_PRIMITIVES["RunPassedOfficialAction"]

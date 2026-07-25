from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — declarative certification registry is one reviewable data table

from dataclasses import dataclass
from typing import Final, Literal

from hwp_operation_contract import HwpWorkflowId, OperationCandidate
from hwp_operation_descriptor import operation_descriptors


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
    "ApplyTextFormat": CertifiedPrimitive("ApplyTextFormat", ("resolved native text range",), ("native character or paragraph formatting",), ("parameters",), _BUILDS, _FORMATS, "no_automatic_rollback", ("requested character and paragraph properties match native readback",)),
    "ApplyTableFormat": CertifiedPrimitive("ApplyTableFormat", ("resolved native table and cell",), ("native cell border, fill, or text formatting",), ("target", "parameters"), _BUILDS, _FORMATS, "no_automatic_rollback", ("target table remains structurally observable",)),
    "ResolveCells": CertifiedPrimitive("ResolveCells", ("resolved native table",), ("validated cell address or range",), ("target", "parameters"), _BUILDS, _FORMATS, "none", ("every requested address belongs to the resolved table",)),
    "TableMergeCell": CertifiedPrimitive("TableMergeCell", ("validated rectangular cell range",), ("native merged cell",), ("parameters.start", "parameters.end"), _BUILDS, _FORMATS, "automatic_undo_on_postcondition_failure", ("target table remains structurally observable",)),
    "TableSplitCell": CertifiedPrimitive("TableSplitCell", ("validated native table cell",), ("native split rows and columns",), ("parameters.cell", "parameters.columns", "parameters.rows"), _BUILDS, _FORMATS, "automatic_undo_on_postcondition_failure", ("target table remains structurally observable",)),
    "ResolveTextPatch": CertifiedPrimitive("ResolveTextPatch", ("one connected editable document",), ("one cursor, range, search match, or table cell target",), ("target", "expected_text"), _BUILDS, _FORMATS, "none", ("ambiguous matches are returned without mutation",)),
    "PatchText": CertifiedPrimitive("PatchText", ("resolved text patch target and verified original text",), ("one native text replacement and optional format application",), ("replacement", "formatting"), _BUILDS, _FORMATS, "no_automatic_rollback", ("native protocol 11 executes the patch once",)),
    "VerifyTextPatch": CertifiedPrimitive("VerifyTextPatch", ("completed native text patch",), ("operation-specific text and format readback",), (), _BUILDS, _FORMATS, "none", ("replacement range and requested formatting match native readback",)),
    "SaveDocument": CertifiedPrimitive("SaveDocument", ("one connected saved editable document",), ("native save without clear or reopen",), (), _BUILDS, _FORMATS, "none", ("save reports success and modified state is clear",)),
    "ReopenDiagnostic": CertifiedPrimitive("ReopenDiagnostic", ("successful native save with recovery snapshot",), ("isolated reopen persistence diagnostic",), (), _BUILDS, _FORMATS, "none", ("failed reopen restores the existing document session",)),
    "VerifySavedDocument": CertifiedPrimitive("VerifySavedDocument", ("completed native save",), ("saved document persistence evidence",), (), _BUILDS, _FORMATS, "none", ("body, tables, and character formatting hashes match readback",)),
}

CERTIFIED_RECIPES: Final = {
    descriptor.workflow_id: CertifiedRecipe(
        descriptor.recipe.recipe_id,
        descriptor.workflow_id,
        descriptor.recipe.version,
        descriptor.recipe.steps,
        descriptor.recipe.atomicity,
    )
    for descriptor in operation_descriptors()
    if descriptor.recipe is not None
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

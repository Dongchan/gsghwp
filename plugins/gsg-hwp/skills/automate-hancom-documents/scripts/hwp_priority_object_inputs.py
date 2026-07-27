from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hwp_live_native_action_models import (
    NativePageInspection,
    NativePosition,
    NativeSnapshot,
)
from hwp_operation_contract import HwpOperateAssets, HwpOperateTarget, OperationStatus
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs, RecipePosition


@dataclass(frozen=True, slots=True)
class ControlTargetRequest:
    routing_page: NativePageInspection
    target: HwpOperateTarget | None
    control_types: frozenset[str]
    snapshot: NativeSnapshot | None


@dataclass(frozen=True, slots=True)
class ResolvedObjectControl:
    instance_id: str
    basis: str
    control_type: str


@dataclass(frozen=True, slots=True)
class ControlTargetFailure:
    status: OperationStatus
    message: str
    required_inputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedStylePosition:
    position: RecipePosition
    basis: str


def native_position(value: RecipePosition) -> NativePosition:
    return NativePosition(value.list_id, value.paragraph, value.character)


def single_image(assets: HwpOperateAssets | None) -> Path | None:
    if assets is None or len(assets.images) != 1:
        return None
    return next(iter(assets.images.values()))


# Object kinds that name a concrete control type. "document", "page",
# "selection" and "control" describe where to look rather than what to edit, so
# they leave the workflow's own candidate types untouched.
_KIND_CONTROL_TYPES: dict[str, frozenset[str]] = {
    "table": frozenset(("tbl",)),
    "picture": frozenset(("gso", "pic", "picture")),
}

_KIND_LABELS: dict[str, str] = {"table": "표", "picture": "그림"}


def _candidate_control_types(
    target: HwpOperateTarget | None,
    control_types: frozenset[str],
) -> frozenset[str] | None:
    """Narrow the workflow's candidate types with ``target.kind``.

    Returns ``None`` when the requested kind names an object type that this
    workflow never edits, which is a contradiction rather than a miss.
    """
    if target is None:
        return control_types
    restriction = _KIND_CONTROL_TYPES.get(target.kind)
    if restriction is None:
        return control_types
    narrowed = control_types & restriction
    return narrowed if narrowed else None


def _candidate_label(target: HwpOperateTarget | None) -> str:
    if target is None:
        return "개체"
    return _KIND_LABELS.get(target.kind, "개체")


def resolve_control_target(
    request: ControlTargetRequest,
) -> ResolvedObjectControl | ControlTargetFailure | None:
    target = request.target
    page = request.routing_page
    control_types = _candidate_control_types(target, request.control_types)
    if control_types is None:
        # _candidate_control_types only narrows to nothing for a concrete kind.
        kind = "" if target is None else target.kind
        return ControlTargetFailure(
            "schema_conflict",
            f"이 작업은 {_candidate_label(target)} 종류의 개체를 편집하지 않습니다 "
            f"(target.kind={kind})",
        )
    if target is not None and target.control_instance_id is not None:
        instance_id = target.control_instance_id
        present = tuple(
            control for control in page.controls if control.instance_id == instance_id
        )
        if not present:
            return ControlTargetFailure(
                "not_found",
                f"{page.page}쪽에 개체 ID {instance_id}가 없습니다",
            )
        matching = tuple(
            control for control in present if control.control_type in control_types
        )
        if not matching:
            return ControlTargetFailure(
                "schema_conflict",
                f"개체 ID {instance_id}의 종류는 {present[0].control_type}이며 "
                f"{_candidate_label(target)} 대상이 아닙니다",
            )
        return ResolvedObjectControl(
            instance_id,
            "explicit_control_instance_id",
            matching[0].control_type,
        )
    if target is not None and target.binding == "selection":
        snapshot = request.snapshot
        if (
            snapshot is not None
            and snapshot.control_type in control_types
            and snapshot.control_instance_id
        ):
            return ResolvedObjectControl(
                snapshot.control_instance_id,
                "native.selection",
                snapshot.control_type,
            )
        return None
    matches = tuple(
        control
        for control in page.controls
        if control.control_type in control_types and control.instance_id
    )
    if target is not None and target.table_index is not None:
        index = target.table_index - 1
        if index < len(matches):
            selected = matches[index]
            return ResolvedObjectControl(
                selected.instance_id,
                "explicit_table_index",
                selected.control_type,
            )
        label = _candidate_label(target)
        return ControlTargetFailure(
            "not_found",
            f"{page.page}쪽의 {label}은 {len(matches)}개이며 "
            f"{target.table_index}번째 {label}이 없습니다",
        )
    if len(matches) != 1:
        return None
    selected = matches[0]
    return ResolvedObjectControl(
        selected.instance_id,
        "unique_native_page_control",
        selected.control_type,
    )


def resolve_style_position(
    recipe_inputs: HwpPriorityRecipeInputs | None,
    target: HwpOperateTarget | None,
    snapshot: NativeSnapshot | None,
) -> ResolvedStylePosition | None:
    if recipe_inputs is not None and recipe_inputs.target_position is not None:
        return ResolvedStylePosition(
            recipe_inputs.target_position,
            "explicit_recipe_target_position",
        )
    if (
        target is None
        or target.kind != "selection"
        or target.binding != "selection"
        or snapshot is None
        or not snapshot.selection.selected
    ):
        return None
    start = snapshot.selection.start
    return ResolvedStylePosition(
        RecipePosition(
            list_id=start.list_id,
            paragraph=start.paragraph,
            character=start.character,
        ),
        "native.selection",
    )

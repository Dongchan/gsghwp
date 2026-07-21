from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hwp_live_native_action_models import (
    NativePageInspection,
    NativePosition,
    NativeSnapshot,
)
from hwp_operation_contract import HwpOperateAssets, HwpOperateTarget
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
class ResolvedStylePosition:
    position: RecipePosition
    basis: str


def native_position(value: RecipePosition) -> NativePosition:
    return NativePosition(value.list_id, value.paragraph, value.character)


def single_image(assets: HwpOperateAssets | None) -> Path | None:
    if assets is None or len(assets.images) != 1:
        return None
    return next(iter(assets.images.values()))


def resolve_control_target(
    request: ControlTargetRequest,
) -> ResolvedObjectControl | None:
    target = request.target
    if target is not None and target.control_instance_id is not None:
        control_type = next(
            (
                control.control_type
                for control in request.routing_page.controls
                if control.instance_id == target.control_instance_id
            ),
            "",
        )
        return ResolvedObjectControl(
            target.control_instance_id,
            "explicit_control_instance_id",
            control_type,
        )
    if target is not None and target.binding == "selection":
        snapshot = request.snapshot
        if (
            snapshot is not None
            and snapshot.control_type in request.control_types
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
        for control in request.routing_page.controls
        if control.control_type in request.control_types and control.instance_id
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
        return None
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

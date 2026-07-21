from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, JsonValue, TypeAdapter

from hwp_mcp_result_arguments import ProductionWorkflowId
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperateTarget,
    OperationInputValue,
    OperationNextArguments,
)


_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_RGB_FIELDS = frozenset(("text_color", "fill_color", "border_color"))


def _model_payload(model: BaseModel) -> dict[str, JsonValue]:
    return _JSON_OBJECT.validate_python(model.model_dump(mode="json"))


def _table_target(target: HwpOperateTarget | None) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {}
    if target is None:
        return payload
    if target.page_hint is not None:
        payload["page_hint"] = target.page_hint
    if target.table_index is not None:
        payload["table_index"] = target.table_index
    if target.caption_contains is not None:
        payload["caption_contains"] = target.caption_contains
    if target.header_signature:
        payload["header_signature"] = _JSON_VALUE.validate_python(
            target.header_signature
        )
    return payload


def _picture_target(target: HwpOperateTarget | None) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {}
    if target is None:
        return payload
    if target.page_hint is not None:
        payload["page_hint"] = target.page_hint
    if target.table_index is not None:
        payload["picture_index"] = target.table_index
    if target.control_instance_id is not None:
        payload["control_instance_id"] = target.control_instance_id
    return payload


def _formatting(parameters: Mapping[str, OperationInputValue]) -> dict[str, JsonValue]:
    payload = _JSON_OBJECT.validate_python(parameters)
    for name in _RGB_FIELDS:
        value = payload.get(name)
        if isinstance(value, str):
            pieces = value.split(",")
            if len(pieces) == 3 and all(piece.strip().isdigit() for piece in pieces):
                payload[name] = _JSON_VALUE.validate_python(
                    [int(piece.strip()) for piece in pieces]
                )
    return payload


def _add_table_data(
    payload: dict[str, JsonValue],
    inputs: HwpOperateInputs,
) -> None:
    if inputs.data is None:
        return
    data = _model_payload(inputs.data)
    populated = {
        name: value
        for name, value in data.items()
        if value not in ({}, [], None)
    }
    if populated:
        payload["data"] = _JSON_VALUE.validate_python(populated)


def _add_template(
    payload: dict[str, JsonValue],
    inputs: HwpOperateInputs,
) -> None:
    recipe = inputs.recipe
    if recipe is not None and recipe.table_template is not None:
        payload["table_template"] = _model_payload(recipe.table_template)


def _add_single_image(
    payload: dict[str, JsonValue],
    inputs: HwpOperateInputs,
) -> None:
    if inputs.assets is None or not inputs.assets.images:
        return
    payload["image"] = str(next(iter(inputs.assets.images.values())))


def _arguments(payload: dict[str, JsonValue]) -> OperationNextArguments:
    return OperationNextArguments(root=payload)


def wrapper_arguments(
    workflow: ProductionWorkflowId,
    inputs: HwpOperateInputs,
) -> OperationNextArguments:
    payload: dict[str, JsonValue] = {}
    if workflow == "table.fill_existing" or workflow == "table.expand_and_fill":
        _add_table_data(payload, inputs)
        payload["target"] = _table_target(inputs.target)
        return _arguments(payload)
    if workflow == "table.repeat_template" or workflow == "table.build_series":
        _add_template(payload, inputs)
        return _arguments(payload)
    if workflow == "image.insert":
        _add_single_image(payload, inputs)
        recipe = inputs.recipe
        if recipe is not None and recipe.target_position is not None:
            payload["target"] = _model_payload(recipe.target_position)
        return _arguments(payload)
    if workflow == "image.replace":
        _add_single_image(payload, inputs)
        payload["target"] = _picture_target(inputs.target)
        return _arguments(payload)
    if workflow == "table.insert_images":
        if inputs.assets is not None:
            payload["images"] = _model_payload(inputs.assets)
        payload["target"] = _table_target(inputs.target)
        return _arguments(payload)
    if workflow == "caption.add":
        recipe = inputs.recipe
        if recipe is not None:
            payload["caption"] = _JSON_VALUE.validate_python(
                {
                    "caption_text": recipe.caption_text,
                    "caption_style_id": recipe.caption_style_id,
                }
            )
        payload["target"] = _table_target(inputs.target)
        return _arguments(payload)
    if workflow == "style.copy":
        recipe = inputs.recipe
        if recipe is not None:
            payload["style"] = _JSON_VALUE.validate_python(
                {
                    "source_position": (
                        None
                        if recipe.source_position is None
                        else _model_payload(recipe.source_position)
                    ),
                    "target_position": (
                        None
                        if recipe.target_position is None
                        else _model_payload(recipe.target_position)
                    ),
                    "style_copy_type": recipe.style_copy_type,
                }
            )
        return _arguments(payload)
    if workflow == "style.apply":
        recipe = inputs.recipe
        if recipe is not None:
            if recipe.style_id is not None:
                payload["style_id"] = recipe.style_id
            if recipe.target_position is not None:
                payload["target_position"] = _model_payload(recipe.target_position)
        return _arguments(payload)
    if workflow == "text.format":
        payload["formatting"] = _formatting(inputs.parameters)
        return _arguments(payload)
    if workflow == "table.format":
        payload["formatting"] = _formatting(inputs.parameters)
        payload["target"] = _table_target(inputs.target)
        return _arguments(payload)
    if workflow == "table.merge_cells":
        payload["cells"] = _JSON_VALUE.validate_python(inputs.parameters)
        payload["target"] = _table_target(inputs.target)
        return _arguments(payload)
    if workflow == "table.split_cells":
        payload["split"] = _JSON_VALUE.validate_python(inputs.parameters)
        payload["target"] = _table_target(inputs.target)
        return _arguments(payload)
    if workflow == "document.append_layout":
        if inputs.layout is not None:
            layout = _model_payload(inputs.layout)
            payload["layout"] = _JSON_VALUE.validate_python(
                {"blocks": layout["blocks"]}
            )
        return _arguments(payload)
    return _arguments(payload)

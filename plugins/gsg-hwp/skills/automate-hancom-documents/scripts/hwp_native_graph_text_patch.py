from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

from _hwp_native_graph_property_registry import NATIVE_PROPERTY_REGISTRY
from hwp_native_graph_patch import (
    PatchDocument,
    PatchKind,
    PatchOperation,
    canonicalize_patch,
)
from hwp_native_graph_resolve import GraphSnapshot, LocatorKind, resolve_node

_TEXT_KEYS: Final = frozenset(range(1000, 2000))
_STYLE_KEYS: Final = frozenset(range(2000, 4000))
_SECTION_KEYS: Final = frozenset(range(4000, 7000))
_PAGE_KEYS: Final = frozenset(range(7000, 8000))


class TextPatchFamily(IntEnum):
    TEXT = 0
    STYLE = 1
    SECTION = 2
    PAGE = 3
    STRUCTURE = 4


class TextPatchError(ValueError):
    code: str

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CompiledAction:
    name: str
    family: TextPatchFamily
    node_id: bytes
    property_key: int | None
    before: bytes
    after: bytes
    readback_schema: str
    quantized: bytes
    caret_units: str


@dataclass(frozen=True, slots=True)
class TextPatchPlan:
    actions: tuple[CompiledAction, ...]
    affected: tuple[bytes, ...]
    inverse: tuple[CompiledAction, ...]
    unrelated_digest: bytes


def _family_of(operation: PatchOperation) -> TextPatchFamily:
    if operation.kind is PatchKind.REPLACE_TEXT:
        return TextPatchFamily.TEXT
    if operation.kind in (PatchKind.REPLACE_CHILDREN, PatchKind.REPLACE_REFERENCES):
        return TextPatchFamily.STRUCTURE
    key = operation.property_key
    if key is None:
        raise TextPatchError("TEXT_PATCH_FAMILY")
    if key in _TEXT_KEYS:
        return TextPatchFamily.TEXT
    if key in _STYLE_KEYS:
        return TextPatchFamily.STYLE
    if key in _PAGE_KEYS:
        return TextPatchFamily.PAGE
    if key in _SECTION_KEYS:
        return TextPatchFamily.SECTION
    raise TextPatchError("TEXT_PATCH_FAMILY")


def _readback_schema(operation: PatchOperation) -> str:
    if operation.kind is PatchKind.REPLACE_TEXT:
        return "UTF16"
    if operation.property_key is None:
        if operation.kind in (PatchKind.REPLACE_CHILDREN, PatchKind.REPLACE_REFERENCES):
            return "NodeId"
        raise TextPatchError("TEXT_PATCH_SCHEMA")
    try:
        scalar, _shape, origin = NATIVE_PROPERTY_REGISTRY[operation.property_key]
    except KeyError as error:
        raise TextPatchError("TEXT_PATCH_SCHEMA") from error
    if origin == "Generated":
        raise TextPatchError("TEXT_PATCH_SCHEMA")
    return scalar


def _quantize(schema: str, value: bytes) -> bytes:
    if schema == "HWPUNIT64" and len(value) == 8:
        raw = int.from_bytes(value, "little")
        return ((raw + 25) // 50 * 50).to_bytes(8, "little")
    return value


def _caret_units(kind: LocatorKind) -> str:
    if kind is LocatorKind.RUN:
        return "code_unit"
    if kind is LocatorKind.CELL:
        return "cell"
    if kind is LocatorKind.CONTROL:
        return "control"
    return "paragraph"


def _action_name(family: TextPatchFamily, operation: PatchOperation) -> str:
    if operation.kind is PatchKind.REPLACE_TEXT:
        return "ReplaceText"
    if operation.kind is PatchKind.REPLACE_CHILDREN:
        if len(operation.after) > len(operation.before):
            return "InsertBlock"
        if len(operation.after) < len(operation.before):
            return "DeleteBlock"
        return "MoveBlock"
    if operation.kind is PatchKind.REPLACE_REFERENCES:
        return "RetargetReference"
    names = {
        TextPatchFamily.STYLE: "ReplaceStyle",
        TextPatchFamily.SECTION: "ReplaceSection",
        TextPatchFamily.PAGE: "ReplacePageSetup",
        TextPatchFamily.TEXT: "ReplaceTextProperty",
    }
    return names[family]


def compile_text_style_section_patch(
    patch: PatchDocument,
    snapshot: GraphSnapshot,
    *,
    observed: Mapping[bytes, Mapping[int | str, bytes]] | None = None,
    unrelated_digest: bytes = b"",
) -> TextPatchPlan:
    canonical = canonicalize_patch(patch)
    views = observed or {}
    actions: list[CompiledAction] = []
    affected: list[bytes] = []
    inverse: list[CompiledAction] = []
    for operation in canonical.operations:
        node = resolve_node(snapshot, operation.target)
        family = _family_of(operation)
        schema = _readback_schema(operation)
        quantized = _quantize(schema, operation.after)
        if quantized == operation.before:
            continue
        key = (
            operation.property_key if operation.property_key is not None else "payload"
        )
        current = views.get(operation.target, {}).get(key)
        if current is not None and current != operation.before:
            raise TextPatchError("TEXT_PATCH_BEFORE")
        action = CompiledAction(
            name=_action_name(family, operation),
            family=family,
            node_id=operation.target,
            property_key=operation.property_key,
            before=operation.before,
            after=operation.after,
            readback_schema=schema,
            quantized=quantized,
            caret_units=_caret_units(node.locator.kind),
        )
        actions.append(action)
        affected.append(operation.target)
        inverse.append(
            CompiledAction(
                name=action.name,
                family=action.family,
                node_id=action.node_id,
                property_key=action.property_key,
                before=action.quantized,
                after=action.before,
                readback_schema=action.readback_schema,
                quantized=action.before,
                caret_units=action.caret_units,
            )
        )
    if not unrelated_digest:
        unrelated_digest = b"\x00" * 32
    return TextPatchPlan(
        actions=tuple(actions),
        affected=tuple(dict.fromkeys(affected)),
        inverse=tuple(reversed(inverse)),
        unrelated_digest=unrelated_digest,
    )


def apply_text_patch_plan(
    plan: TextPatchPlan,
    observed: Mapping[bytes, Mapping[int | str, bytes]],
) -> dict[bytes, dict[int | str, bytes]]:
    next_state = {node_id: dict(values) for node_id, values in observed.items()}
    for action in plan.actions:
        slot = next_state.setdefault(action.node_id, {})
        key: int | str = (
            action.property_key if action.property_key is not None else "payload"
        )
        if slot.get(key, action.before) != action.before:
            raise TextPatchError("TEXT_PATCH_BEFORE")
        slot[key] = action.quantized
    return next_state


__all__ = [
    "CompiledAction",
    "TextPatchError",
    "TextPatchFamily",
    "TextPatchPlan",
    "apply_text_patch_plan",
    "compile_text_style_section_patch",
]

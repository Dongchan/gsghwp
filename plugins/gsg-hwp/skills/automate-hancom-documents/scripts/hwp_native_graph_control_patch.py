from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Final

from _hwp_native_graph_property_registry import NATIVE_PROPERTY_REGISTRY
from hwp_native_graph_patch import writability_of

_CHUNK: Final = 1_048_576
_NOT_EXPOSED: Final = frozenset({12000, 12001, 12002, 12003})


class AssetKind(IntEnum):
    EMBEDDED = 0
    LINKED = 1
    UNEXPOSED = 2


class ControlPatchKind(IntEnum):
    INSERT = 0
    REPLACE = 1
    DELETE = 2
    CLONE = 3
    SET_PROPERTY = 4
    SET_CROP = 5
    SET_ANCHOR = 6
    SET_WRAP = 7
    SET_ZORDER = 8
    SET_CAPTION = 9


class ControlPatchError(ValueError):
    code: str

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AssetBlob:
    digest: bytes
    kind: AssetKind
    chunks: tuple[bytes, ...]
    path: str = ""

    @property
    def payload(self) -> bytes:
        return b"".join(self.chunks)

    @property
    def size(self) -> int:
        return len(self.payload)


def stage_asset(
    data: bytes, *, kind: AssetKind = AssetKind.EMBEDDED, path: str = ""
) -> AssetBlob:
    if kind is AssetKind.UNEXPOSED:
        raise ControlPatchError("CONTROL_ASSET_UNEXPOSED")
    if kind is AssetKind.LINKED and not path:
        raise ControlPatchError("CONTROL_ASSET_LINK")
    digest = hashlib.sha256(data).digest()
    chunks = tuple(
        data[index : index + _CHUNK] for index in range(0, len(data), _CHUNK) or [0]
    )
    if not data:
        chunks = (b"",)
    return AssetBlob(digest=digest, kind=kind, chunks=chunks, path=path)


def verify_asset(asset: AssetBlob) -> None:
    if asset.kind is AssetKind.UNEXPOSED:
        raise ControlPatchError("CONTROL_ASSET_UNEXPOSED")
    if hashlib.sha256(asset.payload).digest() != asset.digest:
        raise ControlPatchError("CONTROL_ASSET_DIGEST")


@dataclass(frozen=True, slots=True)
class ControlFrame:
    displayed_width: int = 0
    displayed_height: int = 0
    original_width: int = 0
    original_height: int = 0
    crop_left: int = 0
    crop_top: int = 0
    crop_right: int = 0
    crop_bottom: int = 0
    wrap: int = 0
    anchor: int = 0
    offset_x: int = 0
    offset_y: int = 0
    rotation: int = 0
    flip_h: bool = False
    flip_v: bool = False
    z_order: int = 0
    margin: int = 0
    transparency: int = 0
    line: bytes = b""
    fill: bytes = b""
    effects: bytes = b""
    caption: bytes = b""


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    node_id: bytes
    frame: ControlFrame
    asset: AssetBlob | None
    properties: dict[int, bytes]
    client_local_id: bytes | None = None

    def digest_material(self) -> bytes:
        asset = self.asset.digest if self.asset is not None else b""
        props = b"".join(
            key.to_bytes(4, "little") + self.properties[key]
            for key in sorted(self.properties)
        )
        frame = self.frame
        return b"\0".join(
            (
                self.node_id,
                asset,
                props,
                frame.caption,
                frame.displayed_width.to_bytes(8, "little", signed=True),
                frame.displayed_height.to_bytes(8, "little", signed=True),
                frame.crop_left.to_bytes(8, "little", signed=True),
                frame.crop_top.to_bytes(8, "little", signed=True),
                frame.crop_right.to_bytes(8, "little", signed=True),
                frame.crop_bottom.to_bytes(8, "little", signed=True),
                frame.wrap.to_bytes(4, "little"),
                frame.anchor.to_bytes(4, "little"),
                frame.z_order.to_bytes(4, "little", signed=True),
            )
        )


@dataclass(frozen=True, slots=True)
class ControlPatchOp:
    kind: ControlPatchKind
    node_id: bytes
    asset: AssetBlob | None = None
    property_key: int | None = None
    property_value: bytes = b""
    frame: ControlFrame | None = None
    client_local_id: bytes | None = None
    checkpoint: ControlSnapshot | None = None


@dataclass(frozen=True, slots=True)
class ControlAction:
    name: str
    kind: ControlPatchKind
    before: ControlSnapshot | None
    after: ControlSnapshot | None


@dataclass(frozen=True, slots=True)
class ControlPatchPlan:
    actions: tuple[ControlAction, ...]
    inverse: tuple[ControlAction, ...]
    affected: tuple[bytes, ...]


def _require_writable(key: int) -> None:
    if key in _NOT_EXPOSED or key not in NATIVE_PROPERTY_REGISTRY:
        raise ControlPatchError("CONTROL_NOT_EXPOSED")
    if writability_of(key) != "Writable":
        raise ControlPatchError("CONTROL_NOT_EXPOSED")


def apply_control_op(
    current: ControlSnapshot | None, op: ControlPatchOp
) -> ControlSnapshot | None:
    if op.kind is ControlPatchKind.INSERT:
        if current is not None:
            raise ControlPatchError("CONTROL_EXISTS")
        if op.asset is None or op.frame is None:
            raise ControlPatchError("CONTROL_ASSET")
        verify_asset(op.asset)
        return ControlSnapshot(
            node_id=op.node_id,
            frame=op.frame,
            asset=op.asset,
            properties={},
            client_local_id=op.client_local_id,
        )
    if current is None:
        raise ControlPatchError("CONTROL_MISSING")
    if current.node_id != op.node_id:
        raise ControlPatchError("CONTROL_IDENTITY")
    if op.kind is ControlPatchKind.DELETE:
        return None
    if op.kind is ControlPatchKind.CLONE:
        if op.client_local_id is None:
            raise ControlPatchError("CONTROL_CLIENT_LOCAL")
        return replace(current, node_id=op.node_id, client_local_id=op.client_local_id)
    if op.kind is ControlPatchKind.REPLACE:
        if op.asset is None:
            raise ControlPatchError("CONTROL_ASSET")
        verify_asset(op.asset)
        return replace(current, asset=op.asset)
    if op.kind is ControlPatchKind.SET_PROPERTY:
        if op.property_key is None:
            raise ControlPatchError("CONTROL_NOT_EXPOSED")
        _require_writable(op.property_key)
        properties = dict(current.properties)
        properties[op.property_key] = op.property_value
        return replace(current, properties=properties)
    if op.kind is ControlPatchKind.SET_CROP and op.frame is not None:
        return replace(
            current,
            frame=replace(
                current.frame,
                crop_left=op.frame.crop_left,
                crop_top=op.frame.crop_top,
                crop_right=op.frame.crop_right,
                crop_bottom=op.frame.crop_bottom,
            ),
        )
    if op.kind is ControlPatchKind.SET_ANCHOR and op.frame is not None:
        return replace(
            current,
            frame=replace(
                current.frame,
                anchor=op.frame.anchor,
                offset_x=op.frame.offset_x,
                offset_y=op.frame.offset_y,
            ),
        )
    if op.kind is ControlPatchKind.SET_WRAP and op.frame is not None:
        return replace(current, frame=replace(current.frame, wrap=op.frame.wrap))
    if op.kind is ControlPatchKind.SET_ZORDER and op.frame is not None:
        return replace(current, frame=replace(current.frame, z_order=op.frame.z_order))
    if op.kind is ControlPatchKind.SET_CAPTION and op.frame is not None:
        return replace(current, frame=replace(current.frame, caption=op.frame.caption))
    raise ControlPatchError("CONTROL_OP")


def compile_control_patch(
    before: ControlSnapshot | None, ops: tuple[ControlPatchOp, ...]
) -> ControlPatchPlan:
    current = before
    actions: list[ControlAction] = []
    for op in ops:
        nxt = apply_control_op(current, op)
        actions.append(
            ControlAction(
                name=op.kind.name.title(), kind=op.kind, before=current, after=nxt
            )
        )
        current = nxt
    inverse: list[ControlAction] = []
    for action in reversed(actions):
        if action.before is None and action.after is not None:
            inverse.append(
                ControlAction(
                    name="Delete",
                    kind=ControlPatchKind.DELETE,
                    before=action.after,
                    after=None,
                )
            )
            continue
        if action.after is None and action.before is not None:
            inverse.append(
                ControlAction(
                    name="Insert",
                    kind=ControlPatchKind.INSERT,
                    before=None,
                    after=action.before,
                )
            )
            continue
        inverse.append(
            ControlAction(
                name=action.name,
                kind=action.kind,
                before=action.after,
                after=action.before,
            )
        )
    affected = ()
    if before is not None:
        affected = (before.node_id,)
    elif actions and actions[0].after is not None:
        affected = (actions[0].after.node_id,)
    return ControlPatchPlan(
        actions=tuple(actions), inverse=tuple(inverse), affected=affected
    )


def apply_control_plan(
    plan: ControlPatchPlan, current: ControlSnapshot | None
) -> ControlSnapshot | None:
    state = current
    for action in plan.actions:
        before = action.before.digest_material() if action.before is not None else b""
        have = state.digest_material() if state is not None else b""
        if before != have:
            raise ControlPatchError("CONTROL_BEFORE")
        state = action.after
    return state


def rollback_control_plan(
    plan: ControlPatchPlan, current: ControlSnapshot | None
) -> ControlSnapshot | None:
    state = current
    for action in plan.inverse:
        before = action.before.digest_material() if action.before is not None else b""
        have = state.digest_material() if state is not None else b""
        if before != have:
            raise ControlPatchError("CONTROL_BEFORE")
        state = action.after
    return state


def checkpoint_inverse(before: ControlSnapshot) -> ControlAction:
    return ControlAction(
        name="CheckpointRestore",
        kind=ControlPatchKind.REPLACE,
        before=None,
        after=before,
    )


__all__ = [
    "AssetBlob",
    "AssetKind",
    "ControlAction",
    "ControlFrame",
    "ControlPatchError",
    "ControlPatchKind",
    "ControlPatchOp",
    "ControlPatchPlan",
    "ControlSnapshot",
    "apply_control_plan",
    "checkpoint_inverse",
    "compile_control_patch",
    "rollback_control_plan",
    "stage_asset",
    "verify_asset",
]

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Final

from _hwp_native_graph_wire import GraphVersion

_UUID_SIZE: Final = 16


class LocatorKind(IntEnum):
    PARAGRAPH = 0
    RUN = 1
    CONTROL = 2
    CELL = 3


class ResolveError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _uuid(value: bytes) -> None:
    if len(value) != _UUID_SIZE or value[6] & 0xF0 != 0x40 or value[8] & 0xC0 != 0x80:
        raise ResolveError("RESOLVE_UUID")


def _require_certified(version: GraphVersion) -> None:
    if not version.semantic_certified:
        raise ResolveError("RESOLVE_UNCERTIFIED")
    _uuid(version.session)
    _uuid(version.graph)
    if version.session == version.graph:
        raise ResolveError("RESOLVE_VERSION")


@dataclass(frozen=True, slots=True)
class NativeLocator:
    kind: LocatorKind
    section: int
    paragraph: int
    run: int = 0
    control: int = 0
    row: int = 0
    column: int = 0
    sibling_ordinal: int = 0
    locator_epoch: int = 0
    semantic_revision: int = 0

    def bind(self, version: GraphVersion) -> NativeLocator:
        return replace(
            self,
            locator_epoch=version.locator_epoch,
            semantic_revision=version.semantic_revision,
        )


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: bytes
    locator: NativeLocator
    fingerprint: bytes = b""


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    version: GraphVersion
    nodes: tuple[GraphNode, ...]
    path: str = ""
    document_id: int = 0

    def by_id(self) -> dict[bytes, GraphNode]:
        return {node.node_id: node for node in self.nodes}

    def by_locator_key(self) -> dict[tuple[object, ...], tuple[GraphNode, ...]]:
        grouped: dict[tuple[object, ...], list[GraphNode]] = {}
        for node in self.nodes:
            grouped.setdefault(_stable_key(node.locator), []).append(node)
        return {key: tuple(values) for key, values in grouped.items()}


def _stable_key(locator: NativeLocator) -> tuple[object, ...]:
    return (
        int(locator.kind),
        locator.section,
        locator.paragraph,
        locator.run,
        locator.control,
        locator.row,
        locator.column,
        locator.sibling_ordinal,
    )


def resolve_node(snapshot: GraphSnapshot, node_id: bytes) -> GraphNode:
    _require_certified(snapshot.version)
    _uuid(node_id)
    try:
        node = snapshot.by_id()[node_id]
    except KeyError as error:
        raise ResolveError("RESOLVE_MISSING") from error
    if (
        node.locator.locator_epoch != snapshot.version.locator_epoch
        or node.locator.semantic_revision != snapshot.version.semantic_revision
    ):
        raise ResolveError("RESOLVE_STALE")
    return node


def resolve_locator(snapshot: GraphSnapshot, locator: NativeLocator) -> GraphNode:
    _require_certified(snapshot.version)
    if (
        locator.locator_epoch != snapshot.version.locator_epoch
        or locator.semantic_revision != snapshot.version.semantic_revision
    ):
        raise ResolveError("RESOLVE_STALE")
    matches = snapshot.by_locator_key().get(_stable_key(locator), ())
    if not matches:
        raise ResolveError("RESOLVE_MISSING")
    if len(matches) != 1:
        raise ResolveError("RESOLVE_AMBIGUOUS")
    return matches[0]


def roundtrip(snapshot: GraphSnapshot, node_id: bytes) -> GraphNode:
    node = resolve_node(snapshot, node_id)
    located = resolve_locator(snapshot, node.locator)
    if located.node_id != node.node_id:
        raise ResolveError("RESOLVE_ROUNDTRIP")
    return located


class RemapKind(IntEnum):
    KEEP = 0
    INSERT = 1
    SPLIT = 2
    COALESCE = 3
    DELETE = 4
    ORDINAL = 5


@dataclass(frozen=True, slots=True)
class RemapRule:
    kind: RemapKind
    source: bytes = b""
    target: bytes = b""
    extra: bytes = b""


@dataclass(frozen=True, slots=True)
class RemapPlan:
    before: GraphSnapshot
    after: GraphSnapshot
    mapping: dict[bytes, bytes]
    reserved_client_ids: tuple[bytes, ...] = ()


def reserve_client_ids(ids: tuple[bytes, ...]) -> tuple[bytes, ...]:
    reserved: list[bytes] = []
    seen: set[bytes] = set()
    for item in ids:
        _uuid(item)
        if item in seen:
            raise ResolveError("RESOLVE_CLIENT_LOCAL")
        seen.add(item)
        reserved.append(item)
    return tuple(reserved)


def remap_identities(
    before: GraphSnapshot,
    after: GraphSnapshot,
    rules: tuple[RemapRule, ...] = (),
    reserved_client_ids: tuple[bytes, ...] = (),
) -> RemapPlan:
    _require_certified(before.version)
    _require_certified(after.version)
    reserved = reserve_client_ids(reserved_client_ids)
    reserved_set = set(reserved)
    after_ids = {node.node_id for node in after.nodes}
    if reserved_set & after_ids:
        raise ResolveError("RESOLVE_CLIENT_LOCAL")

    mapping: dict[bytes, bytes] = {}
    consumed: set[bytes] = set()
    claimed: set[bytes] = set()

    def assign(source: bytes, target: bytes) -> None:
        _uuid(source)
        _uuid(target)
        if source in mapping and mapping[source] != target:
            raise ResolveError("REMAP_PARTIAL")
        mapping[source] = target
        consumed.add(target)
        claimed.add(source)
        claimed.add(target)

    for rule in rules:
        if rule.kind is RemapKind.DELETE:
            _uuid(rule.source)
            if rule.source in mapping:
                raise ResolveError("REMAP_PARTIAL")
            claimed.add(rule.source)
            continue
        if rule.kind is RemapKind.INSERT:
            _uuid(rule.target)
            if rule.target not in after_ids:
                raise ResolveError("REMAP_MISSING")
            claimed.add(rule.target)
            continue
        if rule.kind is RemapKind.SPLIT:
            assign(rule.source, rule.target)
            _uuid(rule.extra)
            if rule.extra not in after_ids:
                raise ResolveError("REMAP_MISSING")
            continue
        if rule.kind is RemapKind.COALESCE:
            assign(rule.source, rule.target)
            if rule.extra:
                assign(rule.extra, rule.target)
            continue
        if rule.kind is RemapKind.KEEP or rule.kind is RemapKind.ORDINAL:
            assign(rule.source, rule.target)

    before_by_key = before.by_locator_key()
    after_by_key = after.by_locator_key()
    for key, before_nodes in before_by_key.items():
        leftover_before = tuple(
            node for node in before_nodes if node.node_id not in claimed
        )
        leftover_after = tuple(
            node for node in after_by_key.get(key, ()) if node.node_id not in claimed
        )
        if not leftover_before:
            continue
        if len(leftover_before) == 1 and len(leftover_after) == 1:
            assign(leftover_before[0].node_id, leftover_after[0].node_id)
            continue
        if len(leftover_before) == len(leftover_after) and leftover_before:
            for source, target in zip(leftover_before, leftover_after, strict=True):
                assign(source.node_id, target.node_id)
            continue
        if leftover_before and not leftover_after:
            continue
        if len(leftover_before) != len(leftover_after):
            raise ResolveError("REMAP_AMBIGUOUS")

    unmapped = [node.node_id for node in before.nodes if node.node_id not in mapping]
    deleted = {rule.source for rule in rules if rule.kind is RemapKind.DELETE}
    leftover = [node_id for node_id in unmapped if node_id not in deleted]
    if leftover:
        raise ResolveError("REMAP_PARTIAL")
    return RemapPlan(
        before=before, after=after, mapping=mapping, reserved_client_ids=reserved
    )


def remap_after_save_reopen(before: GraphSnapshot, after: GraphSnapshot) -> RemapPlan:
    _require_certified(before.version)
    _require_certified(after.version)
    if before.version.semantic_root != after.version.semantic_root:
        raise ResolveError("REMAP_LINEAGE")
    if (
        before.path
        and after.path
        and before.path == after.path
        and before.version.semantic_root != after.version.semantic_root
    ):
        raise ResolveError("REMAP_LINEAGE")
    if (
        before.document_id
        and after.document_id
        and before.document_id == after.document_id
        and before.version.graph != after.version.graph
    ):
        raise ResolveError("REMAP_LINEAGE")
    if after.version.locator_epoch < before.version.locator_epoch:
        raise ResolveError("RESOLVE_STALE")
    return remap_identities(before, after)


__all__ = [
    "GraphNode",
    "GraphSnapshot",
    "LocatorKind",
    "NativeLocator",
    "RemapKind",
    "RemapPlan",
    "RemapRule",
    "ResolveError",
    "remap_after_save_reopen",
    "remap_identities",
    "reserve_client_ids",
    "resolve_locator",
    "resolve_node",
    "roundtrip",
]

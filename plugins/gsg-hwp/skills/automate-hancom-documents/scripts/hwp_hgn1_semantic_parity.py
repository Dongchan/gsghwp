"""Strict deterministic semantic comparison for authenticated HGN1 captures."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path

from _hwp_native_graph_errors import GraphProtocolError
from _hwp_native_graph_stream import GraphStreamDecoder
from _hwp_native_graph_wire import decode_frame
from hwp_native_graph_cache import _record_blobs
from hwp_native_graph_models import (
    NativeEdgeRecord,
    NativeManifestRecord,
    NativeNodeRecord,
    NativePropertyRecord,
    NodeKind,
    TypedNativeRecord,
)

from _hwp_hgn1_semantic_parity_codec import (
    _Component,
    _components,
    _frame_components,
    _index_integrity,
    _normalized_record,
    _stored_frames,
    _typed_components,
)
from _hwp_hgn1_semantic_parity_models import (
    ArtifactIntegrity,
    ClassificationError,
    FirstDifference,
    Inventory,
    NodeIdAlphaBijection,
    ParityReport,
    SemanticInventories,
)


_NORMALIZED_FIELDS = (
    "frame.version.session",
    "frame.cursor",
    "frame.previous_chain",
    "frame.chunk_digest",
    "frame.chain_digest",
    "manifest.version.session",
    "manifest.index_digest",
    "terminal.stream_digest",
    "record.graph_node_ids",
)
_NORMALIZATION_SOURCES = (
    "DocumentGraphProtocol.h GraphVersionV1 session; _hwp_native_graph_wire.GraphVersion.serialized",
    "DocumentGraphProtocol.cpp cursor and HWPGRAPH CHUNK/CHAIN V1 transport bindings",
    "DocumentGraphCaptureRecords.cpp ManifestRecordBytes fields 1 and 5",
    "_hwp_native_graph_stream.GraphStreamDecoder terminal authenticated digest",
    "hwp-native-document-dom NodeId session-lineage UUID and canonical record order",
)


@dataclass(frozen=True, slots=True)
class _RecordItem:
    encoded: bytes
    components: tuple[_Component, ...]
    record: TypedNativeRecord | None = None


@dataclass(frozen=True, slots=True)
class _Snapshot:
    integrity: ArtifactIntegrity
    frames: tuple[tuple[_Component, ...], ...]
    records: tuple[_RecordItem, ...]
    inventory: SemanticInventories
    stream_digest: str


@dataclass(frozen=True, slots=True)
class _Comparison:
    left_records: tuple[bytes, ...]
    right_records: tuple[bytes, ...]
    difference: FirstDifference | None
    bijection: NodeIdAlphaBijection


def _sha_items(items: list[bytes]) -> str:
    digest = hashlib.sha256(b"HGN1-SEMANTIC-INVENTORY-V1\0")
    for item in items:
        digest.update(len(item).to_bytes(8, "little"))
        digest.update(item)
    return digest.hexdigest()


def _inventory(items: list[bytes]) -> Inventory:
    return Inventory(count=len(items), sha256=_sha_items(items))


def _snapshot(source: Path) -> _Snapshot:
    frame_path = source / "frames.bin" if source.is_dir() else source
    raw = frame_path.read_bytes()
    frames = _stored_frames(raw, frame_path)
    decoded = tuple(decode_frame(item, validate_graph_payload=False) for item in frames)
    names = (
        "nodes",
        "properties",
        "edges",
        "controls",
        "tables",
        "matrix",
        "topology",
        "blobs",
    )
    categories: dict[str, list[bytes]] = {name: [] for name in names}
    counts: Counter[str] = Counter()
    items: list[_RecordItem] = []
    decoder = GraphStreamDecoder(frames)
    with decoder.records() as records:
        for record in records:
            normalized = _normalized_record(record)
            encoded = repr(normalized).encode()
            items.append(_RecordItem(encoded, _components(normalized), normalized))
            counts[record.kind.name] += 1
            if isinstance(record, NativeNodeRecord):
                categories["nodes"].append(encoded)
                categories["matrix"].append(encoded)
                if record.node_kind in (
                    NodeKind.GENERIC_CONTROL,
                    NodeKind.TABLE,
                    NodeKind.IMAGE,
                ):
                    categories["controls"].append(encoded)
                if record.node_kind in (NodeKind.TABLE, NodeKind.TABLE_CELL):
                    categories["tables"].append(encoded)
                if record.node_kind in (
                    NodeKind.STORY,
                    NodeKind.TABLE,
                    NodeKind.TABLE_CELL,
                ):
                    categories["topology"].append(encoded)
            if isinstance(record, NativePropertyRecord):
                categories["properties"].append(encoded)
                categories["matrix"].append(encoded)
            if isinstance(record, NativeEdgeRecord):
                categories["edges"].append(encoded)
                categories["topology"].append(encoded)
            blobs = sorted(
                _record_blobs(record),
                key=lambda item: (
                    item.content_id,
                    item.offset,
                    item.length,
                    item.digest,
                ),
            )
            categories["blobs"] += [
                blob.content_id
                + blob.offset.to_bytes(8, "little")
                + blob.length.to_bytes(8, "little")
                + blob.digest
                for blob in blobs
            ]
    inventory = SemanticInventories(
        record_kind_counts=dict(sorted(counts.items())),
        stable_nodes=_inventory(categories["nodes"]),
        properties=_inventory(categories["properties"]),
        edges=_inventory(categories["edges"]),
        controls=_inventory(categories["controls"]),
        tables=_inventory(categories["tables"]),
        owner_address_mode_matrix=_inventory(categories["matrix"]),
        caption_table_topology=_inventory(categories["topology"]),
        blob_references=_inventory(categories["blobs"]),
    )
    integrity = ArtifactIntegrity(
        path=str(frame_path.resolve()),
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        frame_count=len(frames),
        frame_integrity="authenticated",
        record_integrity="authenticated",
        index_integrity=_index_integrity(source),
    )
    stream_digest = _sha_items([item.encoded for item in items])
    return _Snapshot(
        integrity,
        tuple(_frame_components(item) for item in decoded),
        tuple(items),
        inventory,
        stream_digest,
    )


def _component_difference(
    prefix: str,
    left: tuple[_Component, ...],
    right: tuple[_Component, ...],
) -> FirstDifference | None:
    for a, b in zip(left, right, strict=False):
        if a == b:
            continue
        node_left, node_right = a.value.startswith("node:"), b.value.startswith("node:")
        reason = (
            "NODE_ID_REFERENCE_MISMATCH"
            if node_left and node_right
            else "NODE_ID_REFERENCE_DOMAIN_MISMATCH"
            if node_left != node_right
            else "SEMANTIC_VALUE_MISMATCH"
        )
        return FirstDifference(
            path=f"{prefix}/{a.path}", left=a.value, right=b.value, reason=reason
        )
    if len(left) != len(right):
        return FirstDifference(path=prefix, left=str(len(left)), right=str(len(right)))
    return None


def _without_semantic_fingerprint(
    components: tuple[_Component, ...],
) -> tuple[_Component, ...]:
    return tuple(
        component
        for component in components
        if component.path != "fields/1/fields/8/value"
    )


def _direct_record_components(
    record: TypedNativeRecord,
    components: tuple[_Component, ...],
) -> tuple[_Component, ...]:
    without_fingerprint = _without_semantic_fingerprint(components)
    if not isinstance(record, NativeManifestRecord):
        return without_fingerprint
    return tuple(
        component
        for component in without_fingerprint
        if component.path != "fields/1/value"
    )


def _bijection_digest(mapping: dict[bytes, bytes]) -> str:
    digest = hashlib.sha256(b"HGN1-NODE-ID-ALPHA-BIJECTION-V1\0")
    for left, right in mapping.items():
        digest.update(left)
        digest.update(right)
    return digest.hexdigest()


def _comparison(left: _Snapshot, right: _Snapshot) -> _Comparison:
    frame_difference: FirstDifference | None = None
    for ordinal, pair in enumerate(zip(left.frames, right.frames, strict=False)):
        frame_difference = _component_difference(f"/frames/{ordinal}", *pair)
        if frame_difference is not None:
            break
    if frame_difference is None and len(left.frames) != len(right.frames):
        frame_difference = FirstDifference(
            path="/frames/count",
            left=str(len(left.frames)),
            right=str(len(right.frames)),
        )
    if len(left.records) != len(right.records):
        difference = FirstDifference(
            path="/records/count",
            left=str(len(left.records)),
            right=str(len(right.records)),
        )
        bijection = NodeIdAlphaBijection(
            count=0, sha256=_bijection_digest({}), first_mismatch=difference
        )
        return _Comparison(
            tuple(item.encoded for item in left.records),
            tuple(item.encoded for item in right.records),
            difference,
            bijection,
        )

    left_typed = tuple(item.record for item in left.records)
    right_typed = tuple(item.record for item in right.records)
    if any(item is None for item in (*left_typed, *right_typed)):
        difference = frame_difference
        if difference is None:
            for ordinal, (a, b) in enumerate(
                zip(left.records, right.records, strict=True)
            ):
                difference = _component_difference(
                    f"/records/{ordinal}", a.components, b.components
                )
                if difference is not None:
                    break
        bijection = NodeIdAlphaBijection(
            count=0, sha256=_bijection_digest({}), first_mismatch=difference
        )
        return _Comparison(
            tuple(item.encoded for item in left.records),
            tuple(item.encoded for item in right.records),
            difference,
            bijection,
        )

    left_records = tuple(item for item in left_typed if item is not None)
    right_records = tuple(item for item in right_typed if item is not None)
    left_ids = frozenset(
        item.node_id for item in left_records if isinstance(item, NativeNodeRecord)
    )
    right_ids = frozenset(
        item.node_id for item in right_records if isinstance(item, NativeNodeRecord)
    )
    left_shape = {item: "*" for item in left_ids}
    right_shape = {item: "*" for item in right_ids}
    forward: dict[bytes, bytes] = {}
    reverse: dict[bytes, bytes] = {}
    tokens_left: dict[bytes, str] = {}
    tokens_right: dict[bytes, str] = {}
    for ordinal, (a, b) in enumerate(zip(left_records, right_records, strict=True)):
        if not isinstance(a, NativeNodeRecord) and not isinstance(b, NativeNodeRecord):
            continue
        shape_difference = _component_difference(
            f"/records/{ordinal}",
            _without_semantic_fingerprint(_typed_components(a, left_ids, left_shape)),
            _without_semantic_fingerprint(_typed_components(b, right_ids, right_shape)),
        )
        if shape_difference is not None:
            bijection = NodeIdAlphaBijection(
                count=len(forward),
                sha256=_bijection_digest(forward),
                first_mismatch=shape_difference,
            )
            return _Comparison(
                tuple(item.encoded for item in left.records),
                tuple(item.encoded for item in right.records),
                shape_difference,
                bijection,
            )
        assert isinstance(a, NativeNodeRecord) and isinstance(b, NativeNodeRecord)
        if a.node_id in forward and forward[a.node_id] != b.node_id:
            difference = FirstDifference(
                path=f"/records/{ordinal}/node_id",
                left=a.node_id.hex(),
                right=b.node_id.hex(),
                reason="NODE_ID_ALPHA_LEFT_CONFLICT",
            )
            bijection = NodeIdAlphaBijection(
                count=len(forward),
                sha256=_bijection_digest(forward),
                first_mismatch=difference,
            )
            return _Comparison(
                tuple(item.encoded for item in left.records),
                tuple(item.encoded for item in right.records),
                difference,
                bijection,
            )
        if b.node_id in reverse and reverse[b.node_id] != a.node_id:
            difference = FirstDifference(
                path=f"/records/{ordinal}/node_id",
                left=a.node_id.hex(),
                right=b.node_id.hex(),
                reason="NODE_ID_ALPHA_RIGHT_CONFLICT",
            )
            bijection = NodeIdAlphaBijection(
                count=len(forward),
                sha256=_bijection_digest(forward),
                first_mismatch=difference,
            )
            return _Comparison(
                tuple(item.encoded for item in left.records),
                tuple(item.encoded for item in right.records),
                difference,
                bijection,
            )
        if a.node_id not in forward:
            token = str(len(forward))
            forward[a.node_id], reverse[b.node_id] = b.node_id, a.node_id
            tokens_left[a.node_id] = token
            tokens_right[b.node_id] = token

    left_components = tuple(
        _typed_components(item, left_ids, tokens_left) for item in left_records
    )
    right_components = tuple(
        _typed_components(item, right_ids, tokens_right) for item in right_records
    )
    record_difference = None
    for ordinal, (left_record, right_record, left_part, right_part) in enumerate(
        zip(
            left_records,
            right_records,
            left_components,
            right_components,
            strict=True,
        )
    ):
        record_difference = _component_difference(
            f"/records/{ordinal}",
            _direct_record_components(left_record, left_part),
            _direct_record_components(right_record, right_part),
        )
        if record_difference is not None:
            break
    if record_difference is None:
        for ordinal, pair in enumerate(
            zip(left_components, right_components, strict=True)
        ):
            record_difference = _component_difference(f"/records/{ordinal}", *pair)
            if record_difference is not None:
                break
    if record_difference is None:
        record_difference = frame_difference
    left_encoded = tuple(repr(item).encode() for item in left_components)
    right_encoded = tuple(repr(item).encode() for item in right_components)
    bijection = NodeIdAlphaBijection(
        count=len(forward),
        sha256=_bijection_digest(forward),
        first_mismatch=record_difference,
    )
    return _Comparison(left_encoded, right_encoded, record_difference, bijection)


def _first(left: _Snapshot, right: _Snapshot) -> FirstDifference | None:
    return _comparison(left, right).difference


def _load(path: Path) -> _Snapshot:
    try:
        return _snapshot(path)
    except (OSError, GraphProtocolError, ValueError) as error:
        raise ClassificationError("INPUT_REJECTED", path, str(error)) from error


def _canonical_inventory(
    snapshot: _Snapshot, encoded: tuple[bytes, ...]
) -> SemanticInventories:
    categories: dict[str, list[bytes]] = {
        name: []
        for name in (
            "nodes",
            "properties",
            "edges",
            "controls",
            "tables",
            "matrix",
            "topology",
        )
    }
    for item, normalized in zip(snapshot.records, encoded, strict=True):
        record = item.record
        if isinstance(record, NativeNodeRecord):
            categories["nodes"].append(normalized)
            categories["matrix"].append(normalized)
            if record.node_kind in {
                NodeKind.GENERIC_CONTROL,
                NodeKind.TABLE,
                NodeKind.IMAGE,
            }:
                categories["controls"].append(normalized)
            if record.node_kind in {NodeKind.TABLE, NodeKind.TABLE_CELL}:
                categories["tables"].append(normalized)
            if record.node_kind in {
                NodeKind.STORY,
                NodeKind.TABLE,
                NodeKind.TABLE_CELL,
            }:
                categories["topology"].append(normalized)
        if isinstance(record, NativePropertyRecord):
            categories["properties"].append(normalized)
            categories["matrix"].append(normalized)
        if isinstance(record, NativeEdgeRecord):
            categories["edges"].append(normalized)
            categories["topology"].append(normalized)
    return SemanticInventories(
        record_kind_counts=snapshot.inventory.record_kind_counts,
        stable_nodes=_inventory(categories["nodes"]),
        properties=_inventory(categories["properties"]),
        edges=_inventory(categories["edges"]),
        controls=_inventory(categories["controls"]),
        tables=_inventory(categories["tables"]),
        owner_address_mode_matrix=_inventory(categories["matrix"]),
        caption_table_topology=_inventory(categories["topology"]),
        blob_references=snapshot.inventory.blob_references,
    )


def classify(left_path: Path, right_path: Path) -> ParityReport:
    """Authenticate both captures and classify only allowlisted run fields."""
    left, right = _load(left_path), _load(right_path)
    comparison = _comparison(left, right)
    difference = comparison.difference
    left_stream_digest = _sha_items(list(comparison.left_records))
    right_stream_digest = _sha_items(list(comparison.right_records))
    left_inventory = _canonical_inventory(left, comparison.left_records)
    right_inventory = _canonical_inventory(right, comparison.right_records)
    equivalent = difference is None and left_stream_digest == right_stream_digest
    semantic_left = _sha_items(
        [left_stream_digest.encode(), left_inventory.model_dump_json().encode()]
    )
    semantic_right = _sha_items(
        [right_stream_digest.encode(), right_inventory.model_dump_json().encode()]
    )
    return ParityReport(
        left=left.integrity,
        right=right.integrity,
        left_inventory=left_inventory,
        right_inventory=right_inventory,
        raw_digests_equal=left.integrity.sha256 == right.integrity.sha256,
        normalized_stream_digest_left=left_stream_digest,
        normalized_stream_digest_right=right_stream_digest,
        semantic_digest_left=semantic_left,
        semantic_digest_right=semantic_right,
        normalized_fields=_NORMALIZED_FIELDS,
        normalization_sources=_NORMALIZATION_SOURCES,
        node_id_alpha_bijection=comparison.bijection,
        first_difference=difference,
        equivalent=equivalent,
        reason="SEMANTIC_EQUIVALENT" if equivalent else "SEMANTIC_DELTA",
    )

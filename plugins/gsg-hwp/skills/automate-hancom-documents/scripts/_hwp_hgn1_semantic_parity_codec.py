"""Owning-decoder adapters for HGN1 semantic parity."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Literal, assert_never

from _hwp_hgn1_semantic_parity_models import ClassificationError
from _hwp_native_graph_errors import GraphProtocolError
from _hwp_native_graph_wire import NativeFrame, content_read, decode_nested_fields
from hwp_native_graph_cache import _Metadata, _read_generation_index
from hwp_native_graph_models import (
    NativeDigest,
    NativeField,
    NativeGraphPresent,
    NativeGraphUnavailable,
    NativeIdentifier,
    NativeInteger,
    NativeManifestRecord,
    NativeNodeRecord,
    NativeOpaque,
    NativePropertyRecord,
    NativeRawUtf16,
    NativeSpooledBytes,
    NodeKind,
    RecordKind,
    ScalarTag,
    TypedNativeRecord,
)


@dataclass(frozen=True, slots=True)
class _Component:
    path: str
    value: str


def _stored_frames(raw: bytes, path: Path) -> tuple[bytes, ...]:
    if raw[:5] != b"HGNC1":
        raise ClassificationError("BAD_CONTAINER", path, "missing HGNC1 header")
    at, result = 5, []
    while at < len(raw):
        if len(raw) - at < 4:
            raise ClassificationError("BAD_CONTAINER", path, "truncated length")
        length = int.from_bytes(raw[at : at + 4], "little")
        at += 4
        if length > len(raw) - at:
            raise ClassificationError("BAD_CONTAINER", path, "truncated frame")
        result.append(raw[at : at + length])
        at += length
    return tuple(result)


def _normalized_record(record: TypedNativeRecord) -> TypedNativeRecord:
    if not isinstance(record, NativeManifestRecord):
        return record
    fields = list(record.fields)
    version, index_digest = fields[0].value, fields[4].value
    if not isinstance(version, NativeOpaque) or not isinstance(
        index_digest, NativeDigest
    ):
        raise GraphProtocolError("PARITY_MANIFEST_SHAPE")
    match version.raw:
        case bytes() as raw:
            version_raw = bytearray(raw)
        case NativeSpooledBytes() as raw:
            version_raw = bytearray(b"".join(raw.chunks()))
        case unreachable:
            assert_never(unreachable)
    if len(version_raw) != 168:
        raise GraphProtocolError("PARITY_MANIFEST_VERSION")
    version_raw[16:32] = bytes(16)
    fields[0] = fields[0].model_copy(
        update={"value": version.model_copy(update={"raw": bytes(version_raw)})}
    )
    fields[4] = fields[4].model_copy(
        update={
            "value": index_digest.model_copy(
                update={"raw": bytes(32), "value": bytes(32)}
            )
        }
    )
    return record.model_copy(update={"fields": tuple(fields)})


def _components(record: TypedNativeRecord) -> tuple[_Component, ...]:
    result = [
        _Component("kind", record.kind.name),
        _Component("record_id", str(record.record_id)),
        _Component("flags", str(record.flags)),
    ]
    if isinstance(record, NativeNodeRecord):
        result += [
            _Component("node_id", record.node_id.hex()),
            _Component("node_kind", record.node_kind.name),
        ]
    if isinstance(record, NativePropertyRecord):
        result += [
            _Component("owner_node_id", record.owner_node_id.hex()),
            _Component("owner_field_tag", str(record.owner_field_tag)),
            _Component("property_key", str(record.property_key)),
        ]
    result += [
        _Component(
            f"fields/{field.tag}", hashlib.sha256(repr(field).encode()).hexdigest()
        )
        for field in record.fields
    ]
    return tuple(result)


def _raw(value: bytes | NativeSpooledBytes) -> bytes:
    return value if isinstance(value, bytes) else content_read(value)


def _bytes_component(path: str, value: bytes) -> list[_Component]:
    digest = hashlib.sha256(value).hexdigest()
    return [_Component(path, f"bytes:{len(value)}:{digest}")]


def _node_value(
    path: str,
    value: bytes,
    node_ids: frozenset[bytes],
    tokens: dict[bytes, str],
) -> list[_Component]:
    if value in node_ids:
        return [_Component(path, f"node:{tokens[value]}")]
    return [_Component(path, f"uuid:{value.hex()}")]


def _value_components(
    path: str,
    value: object,
    node_ids: frozenset[bytes],
    tokens: dict[bytes, str],
    *,
    node_reference: bool = False,
    struct_schema: Literal["fields", "locator", "paragraph_range"] | None = None,
) -> list[_Component]:
    if isinstance(value, NativeIdentifier):
        if node_reference:
            return _node_value(path, value.value, node_ids, tokens)
        return [_Component(path, f"uuid:{value.value.hex()}")]
    if isinstance(value, NativeInteger):
        return [_Component(path, f"{value.scalar.name}:{value.value}")]
    if isinstance(value, NativeRawUtf16):
        return _bytes_component(path, _raw(value.raw))
    if isinstance(value, NativeGraphPresent):
        return [
            _Component(f"{path}/availability", "present"),
            *_value_components(
                f"{path}/value",
                value.value,
                node_ids,
                tokens,
                node_reference=node_reference,
                struct_schema=struct_schema,
            ),
        ]
    if isinstance(value, NativeGraphUnavailable):
        return [
            _Component(f"{path}/availability", "unavailable"),
            _Component(f"{path}/reason", value.reason.name),
            _Component(f"{path}/hresult", str(value.hresult)),
            *_bytes_component(f"{path}/detail", _raw(value.detail.raw)),
        ]
    if isinstance(value, NativeOpaque):
        raw = _raw(value.raw)
        if struct_schema == "fields":
            return _fields_components(
                path, decode_nested_fields(value), node_ids, tokens
            )
        if struct_schema == "paragraph_range" and len(raw) == 32:
            return [
                *_node_value(f"{path}/paragraph_node_id", raw[:16], node_ids, tokens),
                _Component(
                    f"{path}/begin",
                    str(int.from_bytes(raw[16:24], "little", signed=True)),
                ),
                _Component(
                    f"{path}/end",
                    str(int.from_bytes(raw[24:32], "little", signed=True)),
                ),
            ]
        if struct_schema == "locator" and len(raw) >= 8:
            locator = int.from_bytes(raw[:2], "little")
            result = [_Component(f"{path}/locator_tag", str(locator))]
            if locator == 5 and len(raw) >= 24:
                result += _node_value(
                    f"{path}/table_node_id", raw[8:24], node_ids, tokens
                )
                result += _bytes_component(f"{path}/remainder", raw[24:])
                return result
            return [*result, *_bytes_component(f"{path}/payload", raw[8:])]
        return _bytes_component(path, raw)
    raw = getattr(value, "raw", None)
    if isinstance(raw, (bytes, NativeSpooledBytes)):
        return _bytes_component(path, _raw(raw))
    return [_Component(path, repr(value))]


def _field_role(
    record: TypedNativeRecord,
    outer_tag: int,
    field: NativeField,
) -> tuple[bool, Literal["fields", "locator", "paragraph_range"] | None]:
    if not isinstance(record, NativeNodeRecord):
        references = {
            RecordKind.EDGE: {2, 3},
            RecordKind.PROPERTY: {1},
            RecordKind.COVERAGE: {1},
            RecordKind.DIAGNOSTIC: {3},
            RecordKind.TOMBSTONE: {1},
            RecordKind.REMAP: {2},
        }
        return field.tag in references.get(record.kind, set()), None
    if outer_tag == 1:
        if field.tag in {1, 3}:
            return True, None
        if field.tag == 7:
            return False, "locator"
        return False, None
    if (
        record.node_kind in {NodeKind.CHARACTER_RUN, NodeKind.SPECIAL_CHARACTER}
        and field.tag == 100
    ):
        return False, "paragraph_range"
    if record.node_kind is NodeKind.GENERATED_TEXT and field.tag == 100:
        return False, "paragraph_range"
    if record.node_kind is NodeKind.STORY and field.tag == 101:
        return True, None
    if record.node_kind is NodeKind.TABLE_CELL and field.tag in {100, 107}:
        return True, None
    return False, None


def _fields_components(
    path: str,
    fields: tuple[NativeField, ...],
    node_ids: frozenset[bytes],
    tokens: dict[bytes, str],
    record: TypedNativeRecord | None = None,
    outer_tag: int = 0,
) -> list[_Component]:
    result: list[_Component] = []
    for field in fields:
        field_path = f"{path}/{field.tag}"
        result += [
            _Component(f"{field_path}/flags", str(field.flags)),
            _Component(f"{field_path}/scalar", field.scalar.name),
            _Component(f"{field_path}/element_count", str(field.element_count)),
        ]
        reference, schema = (
            _field_role(record, outer_tag, field)
            if record is not None
            else (False, None)
        )
        result += _value_components(
            f"{field_path}/value",
            field.value,
            node_ids,
            tokens,
            node_reference=reference,
            struct_schema=schema,
        )
    return result


def _typed_components(
    record: TypedNativeRecord,
    node_ids: frozenset[bytes],
    tokens: dict[bytes, str],
) -> tuple[_Component, ...]:
    result = [
        _Component("kind", record.kind.name),
        _Component("record_id", str(record.record_id)),
        _Component("flags", str(record.flags)),
    ]
    if isinstance(record, NativeNodeRecord):
        result += _node_value("node_id", record.node_id, node_ids, tokens)
        result.append(_Component("node_kind", record.node_kind.name))
    if isinstance(record, NativePropertyRecord):
        result += _node_value("owner_node_id", record.owner_node_id, node_ids, tokens)
        result += [
            _Component("owner_field_tag", str(record.owner_field_tag)),
            _Component("property_key", str(record.property_key)),
        ]
    for field in record.fields:
        field_path = f"fields/{field.tag}"
        result += [
            _Component(f"{field_path}/flags", str(field.flags)),
            _Component(f"{field_path}/scalar", field.scalar.name),
            _Component(f"{field_path}/element_count", str(field.element_count)),
        ]
        if isinstance(record, NativeNodeRecord) and field.scalar is ScalarTag.STRUCT:
            if not isinstance(field.value, NativeOpaque):
                raise GraphProtocolError("PARITY_NODE_STRUCT")
            result += _fields_components(
                f"{field_path}/fields",
                decode_nested_fields(field.value),
                node_ids,
                tokens,
                record,
                field.tag,
            )
            continue
        reference, schema = _field_role(record, 0, field)
        result += _value_components(
            f"{field_path}/value",
            field.value,
            node_ids,
            tokens,
            node_reference=reference,
            struct_schema=schema,
        )
    return tuple(result)


def _frame_components(frame: NativeFrame) -> tuple[_Component, ...]:
    version = frame.version
    values = (
        ("message", int(frame.message)),
        ("flags", frame.flags),
        ("sequence", frame.sequence),
        ("fragment_offset", frame.fragment_offset),
        ("fragment_total", frame.fragment_total),
        ("version/graph", version.graph.hex()),
        ("version/profile_bits", version.profile_bits),
        ("version/semantic_revision", version.semantic_revision),
        ("version/layout_revision", version.layout_revision),
        ("version/locator_epoch", version.locator_epoch),
        ("version/semantic_root", version.semantic_root.hex()),
        ("version/layout_root", version.layout_root.hex()),
        ("version/capture_root", version.capture_root.hex()),
        ("version/semantic_certified", version.semantic_certified),
        ("version/layout_present", version.layout_present),
    )
    return tuple(_Component(path, str(value)) for path, value in values)


def _index_integrity(source: Path) -> Literal["authenticated", "not_supplied"]:
    if not source.is_dir():
        return "not_supplied"
    metadata = _Metadata.model_validate_json(
        (source / "metadata.json").read_bytes(), strict=True
    )
    records = (source / "records.bin").read_bytes()
    if hashlib.sha256(records).hexdigest() != metadata.logical_records_sha256:
        raise GraphProtocolError("CACHE_LOGICAL_RECORD_AUTHENTICITY")
    _ = _read_generation_index(source, metadata)
    return "authenticated"

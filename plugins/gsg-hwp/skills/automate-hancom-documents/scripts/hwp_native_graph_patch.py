from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Final, cast

from _hwp_native_graph_property_registry import NATIVE_PROPERTY_REGISTRY
from _hwp_native_graph_wire import MAX_PAYLOAD_BYTES, GraphVersion

_FIELD = struct.Struct("<HHHHQQ")
_ARRAY = struct.Struct("<HHIQ")
_PATCH_SCHEMA: Final = 1
_REQUIRED: Final = 1
_ARRAY_FLAG: Final = 2


class PatchKind(IntEnum):
    REPLACE_TEXT = 0
    REPLACE_PROPERTIES = 1
    REPLACE_REFERENCES = 2
    REPLACE_CHILDREN = 3
    REPLACE_BLOB = 4


@dataclass(frozen=True, slots=True)
class PatchOperation:
    kind: PatchKind
    target: bytes
    subject: int
    scalar_tag: int
    before: bytes
    after: bytes
    property_key: int | None = None


@dataclass(frozen=True, slots=True)
class PatchDocument:
    version: GraphVersion
    operations: tuple[PatchOperation, ...] = ()
    client_local_ids: tuple[bytes, ...] = ()
    schema: int = _PATCH_SCHEMA
    atomic: bool = True


@dataclass(frozen=True, slots=True)
class PatchValidationReceipt:
    upload_id: bytes
    state: int
    canonical_digest: bytes
    operation_count: int
    inverse_digest: bytes

    def encode_payload(self) -> bytes:
        _uuid(self.upload_id)
        _digest(self.canonical_digest)
        _digest(self.inverse_digest)
        if self.state != 2 or not 0 <= self.operation_count <= 0xFFFF_FFFF_FFFF_FFFF:
            raise ValueError("PATCH_RECEIPT")
        return b"".join(
            (
                _field(1, 9, self.upload_id),
                _field(2, 14, bytes((self.state,))),
                _field(3, 10, self.canonical_digest),
                _field(4, 1, struct.pack("<Q", self.operation_count)),
                _field(5, 10, self.inverse_digest),
            )
        )


def _uuid(value: bytes) -> None:
    if len(value) != 16 or value[6] & 0xF0 != 0x40 or value[8] & 0xC0 != 0x80:
        raise ValueError("PATCH_UUID")


def _digest(value: bytes) -> None:
    if len(value) != 32:
        raise ValueError("PATCH_DIGEST")


def _field(
    tag: int, scalar: int, value: bytes, *, array_count: int | None = None
) -> bytes:
    flags = _REQUIRED | (_ARRAY_FLAG if array_count is not None else 0)
    count = array_count if array_count is not None else 1
    return _FIELD.pack(tag, flags, scalar, 0, count, len(value)) + value


def _bytes(value: bytes) -> bytes:
    return struct.pack("<Q", len(value)) + value


def _array(scalar: int, values: tuple[bytes, ...]) -> bytes:
    return _ARRAY.pack(scalar, 0, 0, len(values)) + b"".join(
        struct.pack("<Q", len(value)) + value for value in values
    )


def _parse_fields(raw: bytes | memoryview) -> tuple[tuple[int, int, int, bytes], ...]:
    view = memoryview(raw)
    result: list[tuple[int, int, int, bytes]] = []
    at = 0
    prior = 0
    while at < len(view):
        if len(view) - at < _FIELD.size:
            raise ValueError("PATCH_FIELD")
        tag, flags, scalar, reserved, count, size = cast(
            tuple[int, int, int, int, int, int], _FIELD.unpack_from(view, at)
        )
        at += _FIELD.size
        if (
            tag <= prior
            or tag == 0
            or flags & ~3
            or not flags & _REQUIRED
            or reserved
            or (not flags & _ARRAY_FLAG and count != 1)
            or size > len(view) - at
        ):
            raise ValueError("PATCH_FIELD")
        value = bytes(view[at : at + size])
        at += size
        if flags & _ARRAY_FLAG:
            _ = _parse_array(value, scalar, count)
        result.append((tag, scalar, count, value))
        prior = tag
    return tuple(result)


def _parse_array(raw: bytes, scalar: int, count: int) -> tuple[bytes, ...]:
    if len(raw) < _ARRAY.size:
        raise ValueError("PATCH_ARRAY")
    actual_scalar, flags, reserved, actual_count = cast(
        tuple[int, int, int, int], _ARRAY.unpack_from(raw)
    )
    if actual_scalar != scalar or flags or reserved or actual_count != count:
        raise ValueError("PATCH_ARRAY")
    values: list[bytes] = []
    at = _ARRAY.size
    for _ in range(count):
        if len(raw) - at < 8:
            raise ValueError("PATCH_ARRAY")
        size = int.from_bytes(raw[at : at + 8], "little")
        at += 8
        if size > len(raw) - at:
            raise ValueError("PATCH_ARRAY")
        values.append(raw[at : at + size])
        at += size
    if at != len(raw):
        raise ValueError("PATCH_ARRAY")
    return tuple(values)


def _field_map(raw: bytes) -> dict[int, tuple[int, int, bytes]]:
    return {
        tag: (scalar, count, value) for tag, scalar, count, value in _parse_fields(raw)
    }


def _raw_bytes(value: bytes) -> bytes:
    if len(value) < 8 or int.from_bytes(value[:8], "little") != len(value) - 8:
        raise ValueError("PATCH_BYTES")
    return value[8:]


def _validate_scalar(tag: int, raw: bytes) -> None:
    fixed = {
        0: 8,
        1: 8,
        2: 1,
        3: 8,
        5: 8,
        6: 4,
        8: 64,
        9: 16,
        10: 32,
        12: 4,
        14: 1,
        15: 2,
        16: 4,
        17: 4,
    }
    if tag in fixed and len(raw) != fixed[tag]:
        raise ValueError("PATCH_SCALAR")
    if tag == 2 and raw not in (b"\x00", b"\x01"):
        raise ValueError("PATCH_SCALAR")
    if tag == 3:
        value = cast(tuple[float], struct.unpack("<d", raw))[0]
        if not math.isfinite(value) or (value == 0 and any(raw)):
            raise ValueError("PATCH_SCALAR")
    if tag in (4, 13) and (
        len(raw) < 8
        or int.from_bytes(raw[:8], "little") * (2 if tag == 4 else 1) != len(raw) - 8
    ):
        raise ValueError("PATCH_SCALAR")
    if tag == 7:
        if len(raw) < 16 or raw[8] > 1 or any(raw[9:16]):
            raise ValueError("PATCH_SCALAR")
        if raw[8] == 0 and len(raw) != 16:
            raise ValueError("PATCH_SCALAR")
        if raw[8] == 1:
            _validate_scalar(4, raw[16:])


def _operation_key(operation: PatchOperation) -> tuple[bytes, int, int, int]:
    return (
        operation.target,
        int(operation.kind),
        operation.subject,
        operation.property_key if operation.property_key is not None else 0,
    )


def _validate_version(version: GraphVersion) -> None:
    if len(version.serialized) != 168 or not version.semantic_certified:
        raise ValueError("PATCH_VERSION")
    _uuid(version.session)
    _uuid(version.graph)
    if version.session == version.graph:
        raise ValueError("PATCH_VERSION")


def canonicalize_patch(patch: PatchDocument) -> PatchDocument:
    if patch.schema != _PATCH_SCHEMA:
        raise ValueError("PATCH_SCHEMA")
    if not patch.atomic:
        raise ValueError("PATCH_ATOMIC")
    _validate_version(patch.version)
    if len(set(patch.client_local_ids)) != len(patch.client_local_ids):
        raise ValueError("PATCH_CLIENT_LOCAL")
    for local in patch.client_local_ids:
        _uuid(local)

    indexed = tuple(enumerate(op for op in patch.operations if op.before != op.after))
    ordered = tuple(
        op
        for _, op in sorted(
            indexed, key=lambda item: (_operation_key(item[1]), item[0])
        )
    )
    local_set = set(patch.client_local_ids)
    mapped: set[bytes] = set()
    prior_after: dict[tuple[bytes, int, int, int], bytes] = {}
    for operation in ordered:
        _uuid(operation.target)
        if not 0 <= operation.subject <= 0xFFFF or not 0 <= operation.scalar_tag <= 17:
            raise ValueError("PATCH_OPERATION")
        if operation.target in local_set:
            raise ValueError("PATCH_CLIENT_LOCAL")
        if operation.kind is PatchKind.REPLACE_PROPERTIES:
            if operation.property_key not in NATIVE_PROPERTY_REGISTRY:
                raise ValueError("PATCH_PROPERTY_KEY")
            if writability_of(operation.property_key) != "Writable":
                raise ValueError("PATCH_PROPERTY_READ_ONLY")
        elif operation.property_key is not None:
            raise ValueError("PATCH_PROPERTY_KEY")
        key = _operation_key(operation)
        if key in prior_after and prior_after[key] != operation.before:
            raise ValueError("PATCH_SEQUENTIAL_CAS")
        prior_after[key] = operation.after
        if operation.kind in (PatchKind.REPLACE_REFERENCES, PatchKind.REPLACE_CHILDREN):
            if (
                operation.scalar_tag != 9
                or len(operation.before) % 16
                or len(operation.after) % 16
            ):
                raise ValueError("PATCH_REFERENCE")
            mapped.update(
                value[at : at + 16]
                for value in (operation.before, operation.after)
                for at in range(0, len(value), 16)
                if value[at : at + 16] in local_set
            )
        elif operation.kind is PatchKind.REPLACE_BLOB:
            if (
                operation.scalar_tag != 11
                or len(operation.before) != 56
                or len(operation.after) != 56
            ):
                raise ValueError("PATCH_OPERATION")
        else:
            _validate_scalar(operation.scalar_tag, operation.before)
            _validate_scalar(operation.scalar_tag, operation.after)
    if mapped != local_set:
        raise ValueError("PATCH_CLIENT_LOCAL")
    return replace(patch, operations=ordered, client_local_ids=tuple(sorted(local_set)))


def _encode_operation(operation: PatchOperation) -> bytes:
    fields = [
        _field(1, 14, bytes((int(operation.kind),))),
        _field(2, 9, operation.target),
        _field(3, 15, struct.pack("<H", operation.subject)),
    ]
    if operation.property_key is not None:
        fields.append(_field(4, 16, struct.pack("<I", operation.property_key)))
    fields.extend(
        (
            _field(5, 15, struct.pack("<H", operation.scalar_tag)),
            _field(6, 13, _bytes(operation.before)),
            _field(7, 13, _bytes(operation.after)),
        )
    )
    return b"".join(fields)


def encode_patch(patch: PatchDocument) -> bytes:
    canonical = canonicalize_patch(patch)
    locals_raw = _array(9, canonical.client_local_ids)
    operation_raw = tuple(
        _encode_operation(operation) for operation in canonical.operations
    )
    operations = _array(11, operation_raw)
    return b"".join(
        (
            _field(1, 15, struct.pack("<H", canonical.schema)),
            _field(2, 2, bytes((canonical.atomic,))),
            _field(3, 11, canonical.version.serialized),
            _field(4, 9, locals_raw, array_count=len(canonical.client_local_ids)),
            _field(5, 11, operations, array_count=len(operation_raw)),
        )
    )


def _decode_operation(raw: bytes) -> PatchOperation:
    fields = _field_map(raw)
    if set(fields) not in ({1, 2, 3, 5, 6, 7}, {1, 2, 3, 4, 5, 6, 7}):
        raise ValueError("PATCH_OPERATION_FIELDS")
    try:
        kind = PatchKind(fields[1][2][0])
    except (KeyError, IndexError, ValueError) as error:
        raise ValueError("PATCH_OPERATION") from error
    if fields[1][:2] != (14, 1) or len(fields[1][2]) != 1:
        raise ValueError("PATCH_OPERATION")
    target = fields[2][2]
    if fields[2][:2] != (9, 1) or fields[3][:2] != (15, 1) or len(fields[3][2]) != 2:
        raise ValueError("PATCH_OPERATION")
    prop = None
    if 4 in fields:
        if fields[4][:2] != (16, 1) or len(fields[4][2]) != 4:
            raise ValueError("PATCH_OPERATION")
        prop = int.from_bytes(fields[4][2], "little")
    if fields[5][:2] != (15, 1) or len(fields[5][2]) != 2:
        raise ValueError("PATCH_OPERATION")
    return PatchOperation(
        kind=kind,
        target=target,
        subject=int.from_bytes(fields[3][2], "little"),
        property_key=prop,
        scalar_tag=int.from_bytes(fields[5][2], "little"),
        before=_raw_bytes(fields[6][2]),
        after=_raw_bytes(fields[7][2]),
    )


def _version(raw: bytes) -> GraphVersion:
    if len(raw) != 168 or int.from_bytes(raw[:2], "little") != 1 or any(raw[12:16]):
        raise ValueError("PATCH_VERSION")
    return GraphVersion(
        session=raw[16:32],
        graph=raw[32:48],
        profile_bits=int.from_bytes(raw[2:10], "little"),
        semantic_revision=int.from_bytes(raw[48:56], "little"),
        layout_revision=int.from_bytes(raw[56:64], "little"),
        locator_epoch=int.from_bytes(raw[64:72], "little"),
        semantic_root=raw[72:104],
        layout_root=raw[104:136],
        capture_root=raw[136:168],
        semantic_certified=raw[10] == 1,
        layout_present=raw[11] == 1,
    )


def decode_patch(raw: bytes) -> PatchDocument:
    fields = _field_map(raw)
    if set(fields) != {1, 2, 3, 4, 5}:
        raise ValueError("PATCH_FIELDS")
    if fields[1][:2] != (15, 1) or len(fields[1][2]) != 2:
        raise ValueError("PATCH_SCHEMA")
    schema = int.from_bytes(fields[1][2], "little")
    if schema != _PATCH_SCHEMA:
        raise ValueError("PATCH_SCHEMA")
    if fields[2][:2] != (2, 1) or fields[2][2] != b"\x01":
        raise ValueError("PATCH_ATOMIC")
    if fields[3][:2] != (11, 1):
        raise ValueError("PATCH_VERSION")
    locals_raw = _parse_array(fields[4][2], 9, fields[4][1])
    operations_raw = _parse_array(fields[5][2], 11, fields[5][1])
    patch = PatchDocument(
        schema=schema,
        atomic=True,
        version=_version(fields[3][2]),
        client_local_ids=locals_raw,
        operations=tuple(_decode_operation(item) for item in operations_raw),
    )
    canonical = canonicalize_patch(patch)
    if encode_patch(canonical) != raw:
        raise ValueError("PATCH_NOT_CANONICAL")
    return canonical


def invert_patch(patch: PatchDocument) -> PatchDocument:
    canonical = canonicalize_patch(patch)
    inverse = tuple(
        replace(operation, before=operation.after, after=operation.before)
        for operation in reversed(canonical.operations)
    )
    # Preserve reverse order within each CAS key while restoring canonical key order.
    groups: dict[tuple[bytes, int, int, int], list[PatchOperation]] = {}
    for operation in inverse:
        groups.setdefault(_operation_key(operation), []).append(operation)
    ordered = tuple(operation for key in sorted(groups) for operation in groups[key])
    return replace(canonical, operations=ordered)


def patch_digest(patch: PatchDocument) -> bytes:
    return hashlib.sha256(b"HWPGRAPH\0PATCH\0V1" + encode_patch(patch)).digest()


def writability_of(property_key: int) -> str:
    if property_key not in NATIVE_PROPERTY_REGISTRY:
        raise ValueError("PATCH_PROPERTY_KEY")
    _scalar, _shape, origin = NATIVE_PROPERTY_REGISTRY[property_key]
    return "Writable" if origin == "Direct" else "ReadOnly"


def iter_sealed_patch_chunks(
    payload: bytes, *, max_payload_bytes: int = MAX_PAYLOAD_BYTES
) -> tuple[tuple[int, int, int, bytes], ...]:
    if max_payload_bytes <= 0:
        raise ValueError("PATCH_CHUNK")
    total = len(payload)
    if total == 0:
        return ((0, 0, 0, b""),)
    chunks: list[tuple[int, int, int, bytes]] = []
    offset = 0
    while offset < total:
        piece = payload[offset : offset + max_payload_bytes]
        nxt = offset + len(piece)
        more = 1 if nxt < total else 0
        chunks.append((offset, total, more, piece))
        offset = nxt
    return tuple(chunks)


def reassemble_sealed_patch_chunks(
    chunks: tuple[tuple[int, int, int, bytes], ...],
) -> bytes:
    if not chunks:
        raise ValueError("PATCH_OUT_OF_ORDER")
    expected_total = chunks[0][1]
    expected_offset = 0
    parts: list[bytes] = []
    for index, (offset, total, more, piece) in enumerate(chunks):
        last = index == len(chunks) - 1
        if (
            total != expected_total
            or offset != expected_offset
            or more != (0 if last else 1)
            or (last and offset + len(piece) != total)
            or (not last and offset + len(piece) >= total)
        ):
            raise ValueError("PATCH_OUT_OF_ORDER")
        parts.append(piece)
        expected_offset = offset + len(piece)
    return b"".join(parts)


def decode_validation_receipt_payload(raw: bytes) -> PatchValidationReceipt:
    fields = _field_map(raw)
    if set(fields) != {1, 2, 3, 4, 5}:
        raise ValueError("PATCH_RECEIPT")
    receipt = PatchValidationReceipt(
        upload_id=fields[1][2],
        state=fields[2][2][0],
        canonical_digest=fields[3][2],
        operation_count=int.from_bytes(fields[4][2], "little"),
        inverse_digest=fields[5][2],
    )
    if receipt.encode_payload() != raw:
        raise ValueError("PATCH_RECEIPT")
    return receipt


__all__ = [
    "PatchDocument",
    "PatchKind",
    "PatchOperation",
    "PatchValidationReceipt",
    "canonicalize_patch",
    "decode_patch",
    "decode_validation_receipt_payload",
    "encode_patch",
    "invert_patch",
    "iter_sealed_patch_chunks",
    "patch_digest",
    "reassemble_sealed_patch_chunks",
    "writability_of",
]

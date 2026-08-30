from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, cast

from _hwp_native_graph_errors import GraphProtocolError
from _hwp_native_graph_stream import AuthenticatedGraphFrame
from _hwp_native_graph_wire import GraphVersion, MessageKind
from _hwp_native_graph_property_registry import NATIVE_PROPERTY_REGISTRY
from hwp_native_graph_models import RecordKind, ScalarTag


_FRAGMENT = struct.Struct("<HHHHHHIQQQQQ")
_RECORD_HEADER = struct.Struct("<HHHHQQ")
_FIELD_HEADER = struct.Struct("<HHHHQQ")
_OBSERVATION_HEADER = struct.Struct("<BBHiQQ")
_ARRAY_HEADER = struct.Struct("<HHIQ")
_STREAM_DOMAIN: Final = b"HWPGRAPH\0STREAM\0V1"
_ZERO_DIGEST: Final = bytes(32)
_REGISTRY_SCALARS: Final = {
    "Sint64": int(ScalarTag.SINT64),
    "Uint64": int(ScalarTag.UINT64),
    "Bool": int(ScalarTag.BOOL),
    "UTF16": int(ScalarTag.UTF16),
    "HWPUNIT64": int(ScalarTag.HWPUNIT64),
    "BGR": int(ScalarTag.BGR),
    "Enum": int(ScalarTag.ENUM),
    "Struct": int(ScalarTag.STRUCT),
    "RawURC32": int(ScalarTag.RAW_URC32),
    "Uint8": int(ScalarTag.UINT8),
    "Uint16": int(ScalarTag.UINT16),
}
_RECORD_KINDS: Final = tuple(RecordKind(value) for value in range(9))


def _fail(condition: bool, code: str) -> None:
    if condition:
        raise GraphProtocolError(code)


def _u32(raw: bytes | bytearray | memoryview, offset: int = 0) -> int:
    return int.from_bytes(raw[offset : offset + 4], "little")


def _u64(raw: bytes | bytearray | memoryview, offset: int = 0) -> int:
    return int.from_bytes(raw[offset : offset + 8], "little")


def _validate_scalar_raw(tag: int, raw: bytes | bytearray | memoryview) -> None:
    length = len(raw)
    invalid = False
    if tag in (0, 1, 5):
        invalid = length != 8
    elif tag in (6, 12, 16, 17):
        invalid = length != 4
    elif tag == 14:
        invalid = length != 1
    elif tag == 15:
        invalid = length != 2
    elif tag == 2:
        invalid = length != 1 or raw[0] > 1
    elif tag == 3:
        if length != 8:
            invalid = True
        else:
            value = cast(tuple[float], struct.unpack_from("<d", raw))[0]
            invalid = not math.isfinite(value) or (value == 0 and any(raw))
    elif tag in (4, 13):
        invalid = length < 8 or _u64(raw) * (2 if tag == 4 else 1) != length - 8
    elif tag == 9:
        invalid = length != 16
    elif tag == 10:
        invalid = length != 32
    elif tag == 7:
        invalid = length < 16 or raw[8] > 1 or any(raw[9:16])
        if not invalid:
            if raw[8] == 0:
                invalid = length != 16
            else:
                _validate_scalar_raw(4, raw[16:])
    elif tag == 8:
        invalid = length != 64
    if invalid:
        raise GraphProtocolError("BAD_SCALAR")
    # STRUCT is deliberately opaque here. Known node/property structures are
    # validated by their sealed semantic consumers; unknown structures retain
    # the public decoder's intentionally ignored shape behavior.


def _validate_array(
    scalar: int, count: int, raw: bytes | bytearray | memoryview
) -> tuple[bytes, ...]:
    raw_length = len(raw)
    if raw_length < 16:
        raise GraphProtocolError("BAD_ARRAY")
    array_scalar, flags, reserved, array_count = cast(
        tuple[int, int, int, int], _ARRAY_HEADER.unpack_from(raw)
    )
    if array_scalar != scalar or flags != 0 or reserved != 0 or array_count != count:
        raise GraphProtocolError("BAD_ARRAY")
    at = 16
    blobs: list[bytes] = []
    for _ in range(count):
        if raw_length - at < 8:
            raise GraphProtocolError("BAD_ARRAY")
        length = _u64(raw, at)
        at += 8
        if length > raw_length - at:
            raise GraphProtocolError("BAD_ARRAY")
        value = raw[at : at + length]
        _validate_scalar_raw(scalar, value)
        if scalar == int(ScalarTag.BLOB_SLICE):
            blobs.append(bytes(value))
        at += length
    if at != raw_length:
        raise GraphProtocolError("BAD_ARRAY")
    return tuple(blobs)


def _validate_observation(
    scalar: int, raw: bytes | bytearray | memoryview, *, array: bool = False
) -> tuple[bytes, ...]:
    raw_length = len(raw)
    if raw_length < 24:
        raise GraphProtocolError("BAD_OBSERVATION")
    state, present, reserved, hresult, detail_count, value_bytes = cast(
        tuple[int, int, int, int, int, int],
        _OBSERVATION_HEADER.unpack_from(raw),
    )
    detail_end = 24 + detail_count * 2
    if (
        state > 6
        or present > 1
        or reserved != 0
        or detail_end > raw_length
        or detail_end + value_bytes != raw_length
    ):
        raise GraphProtocolError("BAD_OBSERVATION")
    value = raw[detail_end:]
    if state == 0:
        if present != 1 or hresult != 0:
            raise GraphProtocolError("BAD_OBSERVATION")
        if array:
            return _validate_array(scalar, _u64(value, 8), value)
        _validate_scalar_raw(scalar, value)
        return (bytes(value),) if scalar == int(ScalarTag.BLOB_SLICE) else ()
    if present != 0 or value_bytes != 0:
        raise GraphProtocolError("BAD_OBSERVATION")
    return ()


@dataclass(frozen=True, slots=True)
class PrimitiveRecordFacts:
    kind: RecordKind
    record_id: int
    logical_offset: int
    logical_length: int
    logical_digest: bytes
    node_id: bytes | None = None
    parent_id: bytes | None = None
    property_owner: bytes | None = None
    property_key: int | None = None
    blobs: tuple[bytes, ...] = ()


@dataclass(frozen=True, slots=True)
class PrimitiveSeal:
    version: GraphVersion
    record_count: int
    logical_bytes: int
    fragment_count: int
    field_count: int
    stream_digest: bytes


def _looks_like_observation(raw: bytes | bytearray | memoryview) -> bool:
    raw_length = len(raw)
    if raw_length < 24:
        return False
    state, present, reserved, hresult, detail_count, value_bytes = cast(
        tuple[int, int, int, int, int, int],
        _OBSERVATION_HEADER.unpack_from(raw),
    )
    if state > 6 or present > 1 or reserved != 0:
        return False
    detail_end = 24 + detail_count * 2
    if detail_end > raw_length or detail_end + value_bytes != raw_length:
        return False
    if state == 0:
        return present == 1 and hresult == 0
    return present == 0 and value_bytes == 0


def _nested_fields(
    raw: bytes | bytearray | memoryview, *, strict: bool
) -> tuple[tuple[int, int, int, int, memoryview], ...]:
    view = raw if isinstance(raw, memoryview) else memoryview(raw)
    result: list[tuple[int, int, int, int, memoryview]] = []
    at = 0
    prior = 0
    while at < len(view):
        _fail(len(view) - at < 24, "BAD_FIELD_LENGTH")
        tag, flags, scalar_raw, reserved, count, length = cast(
            tuple[int, int, int, int, int, int],
            _FIELD_HEADER.unpack_from(view, at),
        )
        at += 24
        _fail(
            tag == 0
            or tag <= prior
            or flags & ~3 != 0
            or reserved != 0
            or (flags & 2 == 0 and count != 1)
            or length > len(view) - at,
            "BAD_FIELD",
        )
        if scalar_raw > int(ScalarTag.SINT32):
            raise GraphProtocolError("BAD_SCALAR_TAG")
        value = view[at : at + length]
        if flags & 2:
            _ = _validate_array(scalar_raw, count, value)
        elif _looks_like_observation(value):
            _ = _validate_observation(scalar_raw, value)
        else:
            _validate_scalar_raw(scalar_raw, value)
        result.append((tag, flags, scalar_raw, count, value))
        prior = tag
        at += length
    if strict and not result:
        raise GraphProtocolError("BAD_FIELD")
    return tuple(result)


def _discover_blobs(
    scalar: int, flags: int, count: int, raw: bytes | bytearray | memoryview
) -> tuple[bytes, ...]:
    if flags & 2:
        return _validate_array(scalar, count, raw)
    if _looks_like_observation(raw):
        return _validate_observation(scalar, raw)
    _validate_scalar_raw(scalar, raw)
    if scalar == int(ScalarTag.BLOB_SLICE):
        return (bytes(raw),)
    if scalar != int(ScalarTag.STRUCT):
        return ()
    view = raw if isinstance(raw, memoryview) else memoryview(raw)
    blobs: list[bytes] = []
    at = 0
    prior = 0
    try:
        while at < len(view):
            _fail(len(view) - at < 24, "BAD_FIELD_LENGTH")
            tag, nested_flags, scalar_raw, reserved, nested_count, length = cast(
                tuple[int, int, int, int, int, int],
                _FIELD_HEADER.unpack_from(view, at),
            )
            at += 24
            _fail(
                tag == 0
                or tag <= prior
                or nested_flags & ~3 != 0
                or reserved != 0
                or (nested_flags & 2 == 0 and nested_count != 1)
                or length > len(view) - at,
                "BAD_FIELD",
            )
            if scalar_raw > int(ScalarTag.SINT32):
                raise GraphProtocolError("BAD_SCALAR_TAG")
            value = view[at : at + length]
            blobs.extend(_discover_blobs(scalar_raw, nested_flags, nested_count, value))
            prior = tag
            at += length
    except (GraphProtocolError, ValueError):
        # Unknown Struct shapes are intentionally opaque in the public oracle.
        return ()
    return tuple(blobs)


def consume_primitive_stream(
    frames: Iterable[AuthenticatedGraphFrame],
    *,
    on_logical_bytes: Callable[[bytes], None],
    on_record: Callable[[PrimitiveRecordFacts], None],
    on_fragments: Callable[[int, int], None] | None = None,
) -> PrimitiveSeal:
    expected_sequence = 0
    previous = _ZERO_DIGEST
    cursor: bytes | None = None
    version: GraphVersion | None = None
    reconstructed = 0
    record_count = 0
    fragment_count = 0
    field_count_total = 0
    stream_hash = hashlib.sha256(_STREAM_DOMAIN)
    logical_buffer = bytearray()
    terminal = False
    last_raw: bytes | None = None
    pending_kind = -1
    pending_record_flags = 0
    pending_field_count = 0
    pending_tag = 0
    pending_flags = 0
    pending_scalar = 0
    pending_elements = 0
    pending_total = 0
    pending_logical_offset = 0
    pending_payload_bytes = 0
    pending_value: bytearray | None = None
    pending_fields: list[tuple[int, int, int, int, bytes | bytearray | memoryview]] = []

    for source in frames:
        raw, frame = source.raw, source.frame
        if frame.sequence + 1 == expected_sequence:
            _fail(last_raw is None or raw != last_raw, "DUPLICATE_DIFFERENT")
            continue
        _fail(frame.sequence != expected_sequence, "SEQUENCE_MISMATCH")
        _fail(frame.previous_chain != previous, "CHAIN_MISMATCH")
        if version is None:
            version, cursor = source.version, frame.cursor
        else:
            _fail(
                source.version.serialized != version.serialized,
                "GRAPH_VERSION_MISMATCH",
            )
            _fail(frame.cursor != cursor, "CURSOR_MISMATCH")
        expected_sequence += 1
        previous, last_raw = frame.chain_digest, raw
        if int(frame.message) == int(MessageKind.OPEN_RECEIPT):
            _fail(frame.fragment_offset != 0 or frame.fragment_total != 0, "BAD_OPEN")
            continue
        if int(frame.message) == int(MessageKind.ERROR):
            raise GraphProtocolError("NATIVE_ERROR")
        if int(frame.message) == int(MessageKind.GRAPH_TERMINAL):
            if logical_buffer:
                stream_hash.update(logical_buffer)
                on_logical_bytes(bytes(logical_buffer))
                logical_buffer.clear()
            _fail(
                pending_kind >= 0
                or frame.fragment_offset != reconstructed
                or frame.fragment_total != reconstructed,
                "INCOMPLETE_STREAM",
            )
            fields = _nested_fields(frame.payload, strict=True)
            _fail(len(fields) != 3, "BAD_TERMINAL")
            _fail(
                fields[0][2] != int(ScalarTag.UINT64)
                or _u64(fields[0][4]) != record_count,
                "BAD_TERMINAL",
            )
            _fail(
                fields[1][2] != int(ScalarTag.UINT64)
                or _u64(fields[1][4]) != reconstructed,
                "BAD_TERMINAL",
            )
            _fail(
                fields[2][2] != int(ScalarTag.SHA256)
                or bytes(fields[2][4]) != stream_hash.digest(),
                "STREAM_DIGEST_MISMATCH",
            )
            terminal = True
            continue
        _fail(
            int(frame.message) != int(MessageKind.GRAPH_CHUNK) or terminal,
            "BAD_MESSAGE",
        )
        _fail(frame.fragment_offset != reconstructed, "FRAGMENT_OFFSET")
        payload = memoryview(frame.payload)
        payload_length = len(payload)
        at = 0
        frame_fragments = 0
        while at < payload_length:
            if payload_length - at < _FRAGMENT.size:
                raise GraphProtocolError("BAD_FRAGMENT_LENGTH")
            (
                kind_raw,
                record_flags,
                tag,
                marked,
                scalar_raw,
                field_count,
                header_bytes,
                record_id,
                count,
                total,
                field_offset,
                size,
            ) = cast(
                tuple[int, int, int, int, int, int, int, int, int, int, int, int],
                _FRAGMENT.unpack_from(payload, at),
            )
            at += _FRAGMENT.size
            if header_bytes != 56 or size > payload_length - at:
                raise GraphProtocolError("BAD_FRAGMENT_LENGTH")
            data = payload[at : at + size]
            at += size
            frame_fragments += 1
            fragment_count += 1
            if kind_raw > int(RecordKind.REMAP) or scalar_raw > int(ScalarTag.SINT32):
                raise GraphProtocolError("BAD_FRAGMENT_TAG")
            first_field = bool(marked & 4)
            last_field = bool(marked & 8)
            first_record = bool(marked & 16)
            last_record = bool(marked & 32)
            base_flags = marked & 3
            if marked & ~0x3F != 0 or record_flags & ~1 != 0:
                raise GraphProtocolError("BAD_FRAGMENT_ORDER")
            if pending_kind < 0:
                if (
                    not first_field
                    or not first_record
                    or record_id != record_count
                    or field_count == 0
                ):
                    raise GraphProtocolError("BAD_FRAGMENT_ORDER")
                pending_kind = kind_raw
                pending_record_flags = record_flags
                pending_field_count = field_count
                pending_tag = tag
                pending_flags = base_flags
                pending_scalar = scalar_raw
                pending_elements = count
                pending_total = total
                pending_logical_offset = reconstructed
                pending_payload_bytes = 0
                pending_value = None
                pending_fields = []
                reconstructed += 48
            elif first_field:
                if first_record or tag <= pending_tag or pending_value is not None:
                    raise GraphProtocolError("BAD_FRAGMENT_ORDER")
                pending_tag = tag
                pending_flags = base_flags
                pending_scalar = scalar_raw
                pending_elements = count
                pending_total = total
                reconstructed += 24
            else:
                if (
                    kind_raw != pending_kind
                    or record_id != record_count
                    or tag != pending_tag
                    or base_flags != pending_flags
                    or scalar_raw != pending_scalar
                    or count != pending_elements
                    or total != pending_total
                ):
                    raise GraphProtocolError("BAD_FRAGMENT_ORDER")
            accumulated = 0 if pending_value is None else len(pending_value)
            if field_offset != accumulated or (total > 0 and size == 0):
                raise GraphProtocolError("FRAGMENT_OFFSET")
            if last_field and pending_value is None:
                complete_value: bytes | bytearray | memoryview = data
            else:
                if pending_value is None:
                    pending_value = bytearray()
                pending_value.extend(data)
                complete_value = pending_value
            reconstructed += size
            if last_field:
                if len(complete_value) != total:
                    raise GraphProtocolError("BAD_FRAGMENT_LENGTH")
                if (
                    pending_kind == int(RecordKind.PROPERTY)
                    and pending_tag == 4
                    and pending_scalar != int(ScalarTag.STRUCT)
                ):
                    raise GraphProtocolError("BAD_SCALAR")
                pending_fields.append(
                    (
                        pending_tag,
                        pending_flags,
                        pending_scalar,
                        pending_elements,
                        complete_value,
                    )
                )
                pending_payload_bytes += 24 + len(complete_value)
                field_count_total += 1
                if last_record:
                    if len(pending_fields) != pending_field_count:
                        raise GraphProtocolError("BAD_FIELD_COUNT")
                    pkind = pending_kind
                    prflags = pending_record_flags
                    pcount = pending_field_count
                    fields = pending_fields
                    logical_offset = pending_logical_offset
                    logical_length = 24 + pending_payload_bytes
                    record_bytes = bytearray(logical_length)
                    _RECORD_HEADER.pack_into(
                        record_bytes,
                        0,
                        pkind,
                        1,
                        prflags,
                        pcount,
                        record_count,
                        logical_length - 24,
                    )
                    record_at = 24
                    blobs: list[bytes] = []
                    node_id: bytes | None = None
                    parent_id: bytes | None = None
                    property_owner: bytes | None = None
                    property_key: int | None = None
                    for ftag, fflags, fscalar_raw, elements, fvalue in fields:
                        value_length = len(fvalue)
                        _FIELD_HEADER.pack_into(
                            record_bytes,
                            record_at,
                            ftag,
                            fflags,
                            fscalar_raw,
                            0,
                            elements,
                            value_length,
                        )
                        record_at += 24
                        record_bytes[record_at : record_at + value_length] = fvalue
                        record_at += value_length
                        if not (pkind == int(RecordKind.PROPERTY) and ftag == 4):
                            if fflags & 2:
                                blobs.extend(
                                    _validate_array(fscalar_raw, elements, fvalue)
                                )
                            elif fscalar_raw == int(ScalarTag.BLOB_SLICE):
                                _validate_scalar_raw(fscalar_raw, fvalue)
                                blobs.append(bytes(fvalue))
                            elif fscalar_raw == int(ScalarTag.STRUCT):
                                _validate_scalar_raw(fscalar_raw, fvalue)
                                if not (pkind == int(RecordKind.NODE) and ftag == 1):
                                    blobs.extend(
                                        _discover_blobs(
                                            int(ScalarTag.STRUCT), 0, 1, fvalue
                                        )
                                    )
                            else:
                                _validate_scalar_raw(fscalar_raw, fvalue)
                    if pkind == int(RecordKind.NODE):
                        if not fields or fields[0][2] != int(ScalarTag.STRUCT):
                            raise GraphProtocolError("NODE_ID_MISSING")
                        try:
                            common = _nested_fields(fields[0][4], strict=True)
                        except GraphProtocolError as error:
                            raise GraphProtocolError("NODE_ID_INVALID") from error
                        if len(common) < 2 or common[0][2] != int(ScalarTag.UUID128):
                            raise GraphProtocolError("NODE_ID_MISSING")
                        if common[1][2] not in (15, 16, 1):
                            raise GraphProtocolError("NODE_KIND")
                        node_id = bytes(common[0][4])
                        if (
                            not any(node_id)
                            or node_id[6] & 0xF0 != 0x40
                            or node_id[8] & 0xC0 != 0x80
                        ):
                            raise GraphProtocolError("NODE_ID_INVALID")
                        node_kind = int.from_bytes(common[1][4], "little")
                        if node_kind > 12:
                            raise GraphProtocolError("MODEL")
                        parent = next((item for item in common if item[0] == 3), None)
                        if parent is not None and parent[2] == int(ScalarTag.UUID128):
                            parent_id = bytes(parent[4])
                    elif pkind == int(RecordKind.PROPERTY):
                        if len(fields) != 5:
                            raise GraphProtocolError("PROPERTY_SHAPE")
                        if (
                            fields[0][2] != int(ScalarTag.UUID128)
                            or fields[1][2] not in (15, 16)
                            or fields[2][2] != int(ScalarTag.UINT32)
                        ):
                            raise GraphProtocolError("PROPERTY_SHAPE")
                        property_owner = bytes(fields[0][4])
                        property_key = _u32(fields[2][4])
                        rule = NATIVE_PROPERTY_REGISTRY.get(property_key)
                        observation_scalar = int(ScalarTag.STRUCT)
                        observation_array = False
                        if rule is not None:
                            try:
                                observation_scalar = _REGISTRY_SCALARS[rule[0]]
                            except KeyError as error:
                                raise GraphProtocolError(
                                    "PROPERTY_REGISTRY_SCALAR"
                                ) from error
                            observation_array = rule[1] != "Scalar"
                        blobs.extend(
                            _validate_observation(
                                observation_scalar,
                                fields[3][4],
                                array=observation_array,
                            )
                        )
                    logical_buffer.extend(record_bytes)
                    on_record(
                        PrimitiveRecordFacts(
                            _RECORD_KINDS[pkind],
                            record_count,
                            logical_offset,
                            logical_length,
                            hashlib.sha256(record_bytes).digest(),
                            node_id,
                            parent_id,
                            property_owner,
                            property_key,
                            tuple(blobs),
                        )
                    )
                    record_count += 1
                    pending_kind = -1
                    if len(logical_buffer) >= 4_193_984:
                        stream_hash.update(logical_buffer)
                        on_logical_bytes(bytes(logical_buffer))
                        logical_buffer.clear()
                else:
                    if len(pending_fields) >= pending_field_count:
                        raise GraphProtocolError("BAD_FIELD_COUNT")
                    pending_value = None
            elif last_record:
                raise GraphProtocolError("BAD_FRAGMENT_ORDER")
        if on_fragments is not None:
            on_fragments(len(raw), frame_fragments)
        _fail(
            reconstructed > frame.fragment_total
            or bool(frame.flags & 8) != (reconstructed < frame.fragment_total),
            "FRAGMENT_TOTAL",
        )
    _fail(not terminal or version is None, "INCOMPLETE_TERMINAL")
    if logical_buffer:
        stream_hash.update(logical_buffer)
        on_logical_bytes(bytes(logical_buffer))
    assert version is not None
    return PrimitiveSeal(
        version,
        record_count,
        reconstructed,
        fragment_count,
        field_count_total,
        stream_hash.digest(),
    )


__all__ = ["PrimitiveRecordFacts", "PrimitiveSeal", "consume_primitive_stream"]

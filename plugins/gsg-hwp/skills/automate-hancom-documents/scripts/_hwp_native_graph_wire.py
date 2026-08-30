from __future__ import annotations

import ctypes
import hashlib
import math
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Final, cast

from pydantic import TypeAdapter, ValidationError

from _hwp_native_graph_errors import GraphProtocolError
from hwp_native_graph_models import (
    NativeBlobChunkPayload,
    NativeBlobPackPayload,
    NativeBlobPackRequestPayload,
    NativeBlobReadRequestPayload,
    NativeBlobSlice,
    NativeBoolean,
    NativeCapabilitiesPayload,
    NativeContent,
    NativeCursorClosedReceiptPayload,
    NativeDigest,
    NativeErrorPayload,
    NativeField,
    NativeFloat,
    NativeFramePayload,
    NativeFrameVersion,
    NativeGraphCancelRequestPayload,
    NativeGraphChunkPayload,
    NativeGraphCloseRequestPayload,
    NativeGraphFragment,
    NativeGraphNextRequestPayload,
    NativeGraphOpenRequestPayload,
    NativeGraphPresent,
    NativeGraphTerminalPayload,
    NativeGraphUnavailable,
    NativeIdentifier,
    NativeInteger,
    NativeOpenReceiptPayload,
    NativeOpaque,
    NativePatchAbortReceiptPayload,
    NativePatchAbortRequestPayload,
    NativePatchValidationReceiptPayload,
    NativePatchValidateRequestPayload,
    NativePatchBeginReceiptPayload,
    NativePatchBeginRequestPayload,
    NativePatchChunkReceiptPayload,
    NativePatchChunkRequestPayload,
    NativePatchCommitRequestPayload,
    NativePatchSealReceiptPayload,
    NativeRawUtf16,
    NativeScalar,
    NativeScalarValue,
    NativeSpooledBytes,
    RecordKind,
    ScalarTag,
    SupportedNativeFrame,
    UnavailableReason,
)

_HEADER_BYTES: Final = 320
_MAX_FRAME_BYTES: Final = 4_194_304
_MAX_PAYLOAD_BYTES: Final = 4_193_984
_KNOWN_FLAGS: Final = 0x3F
_ZERO_DIGEST: Final = bytes(32)


class MessageKind(IntEnum):
    CAPABILITIES = 1
    OPEN_RECEIPT = 2
    GRAPH_CHUNK = 3
    GRAPH_TERMINAL = 4
    ERROR = 5
    PATCH_BEGIN_RECEIPT = 6
    PATCH_CHUNK_RECEIPT = 7
    PATCH_SEAL_RECEIPT = 8
    CURSOR_CLOSED_RECEIPT = 9
    PATCH_ABORT_RECEIPT = 10
    PATCH_VALIDATION_RECEIPT = 11
    PATCH_APPLY_RECEIPT = 12
    BLOB_CHUNK = 13
    BLOB_PACK = 14
    GRAPH_OPEN_REQUEST = 101
    GRAPH_NEXT_REQUEST = 102
    GRAPH_CANCEL_REQUEST = 103
    GRAPH_CLOSE_REQUEST = 104
    PATCH_BEGIN_REQUEST = 105
    PATCH_CHUNK_REQUEST = 106
    PATCH_COMMIT_REQUEST = 107
    PATCH_ABORT_REQUEST = 108
    PATCH_VALIDATE_REQUEST = 109
    PATCH_APPLY_REQUEST = 110
    BLOB_READ_REQUEST = 111
    BLOB_PACK_REQUEST = 112


@dataclass(frozen=True, slots=True)
class GraphVersion:
    session: bytes
    graph: bytes
    profile_bits: int
    semantic_revision: int
    layout_revision: int
    locator_epoch: int
    semantic_root: bytes
    layout_root: bytes
    capture_root: bytes
    semantic_certified: bool
    layout_present: bool

    @property
    def serialized(self) -> bytes:
        value = bytearray(168)
        struct.pack_into(
            "<HQBB",
            value,
            0,
            1,
            self.profile_bits,
            self.semantic_certified,
            self.layout_present,
        )
        value[16:32], value[32:48] = self.session, self.graph
        struct.pack_into(
            "<QQQ",
            value,
            48,
            self.semantic_revision,
            self.layout_revision,
            self.locator_epoch,
        )
        value[72:104], value[104:136], value[136:168] = (
            self.semantic_root,
            self.layout_root,
            self.capture_root,
        )
        return bytes(value)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.serialized).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeFrame:
    raw: bytes
    message: MessageKind
    flags: int
    sequence: int
    fragment_offset: int
    fragment_total: int
    cursor: bytes
    previous_chain: bytes
    chunk_digest: bytes
    chain_digest: bytes
    version: GraphVersion
    payload: bytes | memoryview


def _fail(condition: bool, code: str, detail: str = "") -> None:
    if condition:
        raise GraphProtocolError(code, detail)


def _u16(raw: bytes | memoryview, offset: int = 0) -> int:
    return int.from_bytes(raw[offset : offset + 2], "little")


def _u32(raw: bytes | memoryview, offset: int = 0) -> int:
    return int.from_bytes(raw[offset : offset + 4], "little")


def _i32(raw: bytes, offset: int = 0) -> int:
    return int.from_bytes(raw[offset : offset + 4], "little", signed=True)


def _u64(raw: bytes | memoryview, offset: int = 0) -> int:
    return int.from_bytes(raw[offset : offset + 8], "little")


def _f64(raw: bytes) -> float:
    return ctypes.c_double.from_buffer_copy(raw).value


def _is_uuid_v4(value: bytes) -> bool:
    return len(value) == 16 and value[6] & 0xF0 == 0x40 and value[8] & 0xC0 == 0x80


def _profiles_are_closed(bits: int) -> bool:
    if bits & ~0x1F != 0:
        return False
    closed = bits
    if closed & 0x04:
        closed |= 0x02
    if closed & (0x02 | 0x08 | 0x10):
        closed |= 0x01
    return closed == bits


def _valid_version(version: GraphVersion) -> bool:
    return (
        _is_uuid_v4(version.session)
        and _is_uuid_v4(version.graph)
        and version.session != version.graph
        and version.profile_bits != 0
        and _profiles_are_closed(version.profile_bits)
        and min(
            version.semantic_revision,
            version.layout_revision,
            version.locator_epoch,
        )
        > 0
        and (
            (
                version.semantic_certified
                and bool(version.profile_bits & 0x08)
                and any(version.layout_root)
            )
            if version.layout_present
            else not any(version.layout_root)
        )
    )


def _has_version(version: GraphVersion) -> bool:
    return (
        any(version.graph)
        or version.semantic_revision != 0
        or version.layout_revision != 0
        or version.locator_epoch != 0
        or any(version.semantic_root)
        or any(version.layout_root)
        or any(version.capture_root)
        or version.semantic_certified
        or version.layout_present
    )


def _version_fields_zero(version: GraphVersion) -> bool:
    return (
        not any(version.graph)
        and version.profile_bits == 0
        and version.semantic_revision == 0
        and version.layout_revision == 0
        and version.locator_epoch == 0
        and not any(version.semantic_root)
        and not any(version.layout_root)
        and not any(version.capture_root)
        and not version.semantic_certified
        and not version.layout_present
    )


def _message_shape(
    message: MessageKind,
    flags: int,
    sequence: int,
    offset: int,
    total: int,
    session: bytes,
    cursor: bytes,
    previous: bytes,
    version: GraphVersion,
) -> bool:
    version_flags = flags & 0x21
    independent = sequence == 0 and previous == _ZERO_DIGEST
    zero_range = offset == 0 and total == 0
    full_shape = (
        any(session)
        and any(version.graph)
        and version.profile_bits != 0
        and min(
            version.semantic_revision, version.layout_revision, version.locator_epoch
        )
        > 0
    )
    absent = _version_fields_zero(version)
    match message:
        case MessageKind.CAPABILITIES:
            return (
                flags == 2
                and not any(session)
                and absent
                and not any(cursor)
                and independent
                and zero_range
            )
        case MessageKind.GRAPH_OPEN_REQUEST:
            graph_open_absent = not _has_version(version)
            return (
                flags == version_flags
                and any(session)
                and not any(cursor)
                and independent
                and zero_range
                and (graph_open_absent or full_shape)
            )
        case MessageKind.OPEN_RECEIPT:
            return (
                flags == version_flags
                and full_shape
                and any(cursor)
                and independent
                and zero_range
            )
        case MessageKind.GRAPH_NEXT_REQUEST:
            return flags == version_flags and full_shape and any(cursor) and zero_range
        case MessageKind.GRAPH_CHUNK:
            return (
                flags in {version_flags, version_flags | 8}
                and full_shape
                and any(cursor)
                and offset <= total
            )
        case MessageKind.GRAPH_TERMINAL:
            return (
                flags == version_flags | 2
                and full_shape
                and any(cursor)
                and offset == total
            )
        case MessageKind.BLOB_READ_REQUEST | MessageKind.BLOB_PACK_REQUEST:
            return (
                flags == version_flags
                and full_shape
                and any(cursor)
                and independent
                and zero_range
            )
        case MessageKind.BLOB_CHUNK:
            return (
                flags in {version_flags, version_flags | 2}
                and full_shape
                and any(cursor)
                and independent
                and offset <= total
            )
        case MessageKind.BLOB_PACK:
            return (
                flags == version_flags | 2
                and full_shape
                and any(cursor)
                and independent
                and zero_range
            )
        case MessageKind.ERROR:
            return flags == version_flags | 4 and zero_range and (absent or full_shape)
        case MessageKind.GRAPH_CANCEL_REQUEST | MessageKind.GRAPH_CLOSE_REQUEST:
            return (
                flags == 0
                and any(session)
                and any(cursor)
                and absent
                and independent
                and zero_range
            )
        case MessageKind.CURSOR_CLOSED_RECEIPT:
            return (
                flags == 2
                and any(session)
                and any(cursor)
                and absent
                and independent
                and zero_range
            )
        case MessageKind.PATCH_BEGIN_REQUEST | MessageKind.PATCH_BEGIN_RECEIPT:
            return (
                flags == version_flags | 16
                and full_shape
                and (
                    message is MessageKind.PATCH_BEGIN_REQUEST
                    or bool(version_flags & 32)
                )
                and (
                    not any(cursor)
                    if message is MessageKind.PATCH_BEGIN_REQUEST
                    else any(cursor)
                )
                and independent
                and offset == 0
            )
        case MessageKind.PATCH_CHUNK_REQUEST:
            return (
                flags in {version_flags | 16, version_flags | 16 | 8}
                and full_shape
                and bool(version_flags & 32)
                and any(cursor)
                and offset <= total
            )
        case MessageKind.PATCH_CHUNK_RECEIPT:
            return (
                flags == version_flags | 16
                and full_shape
                and bool(version_flags & 32)
                and any(cursor)
                and offset <= total
            )
        case MessageKind.PATCH_COMMIT_REQUEST | MessageKind.PATCH_SEAL_RECEIPT:
            return (
                flags == version_flags | 16 | 2
                and full_shape
                and bool(version_flags & 32)
                and any(cursor)
                and offset == total
            )
        case MessageKind.PATCH_ABORT_REQUEST | MessageKind.PATCH_ABORT_RECEIPT:
            return (
                flags == 18 and any(session) and any(cursor) and absent and offset == 0
            )
        case MessageKind.PATCH_VALIDATE_REQUEST:
            return (
                flags == version_flags | 16
                and full_shape
                and bool(version_flags & 32)
                and any(cursor)
                and independent
                and zero_range
            )
        case MessageKind.PATCH_VALIDATION_RECEIPT:
            return (
                flags == version_flags | 18
                and full_shape
                and bool(version_flags & 32)
                and any(cursor)
                and independent
                and zero_range
            )
        case _:
            return False


def decode_frame(raw: bytes, *, validate_graph_payload: bool = True) -> NativeFrame:
    _fail(len(raw) < _HEADER_BYTES or len(raw) > _MAX_FRAME_BYTES, "BAD_LENGTH")
    _fail(raw[:4] != b"HGN1" or _u16(raw, 4) != 1, "BAD_HEADER")
    try:
        message = MessageKind(_u16(raw, 6))
    except ValueError as error:
        raise GraphProtocolError("BAD_MESSAGE") from error
    flags = _u32(raw, 8)
    header_bytes = _u32(raw, 12)
    payload_bytes = _u64(raw, 16)
    sequence = _u64(raw, 24)
    offset = _u64(raw, 32)
    total = _u64(raw, 40)
    _fail(flags & ~_KNOWN_FLAGS != 0 or header_bytes != _HEADER_BYTES, "BAD_HEADER")
    _fail(
        payload_bytes > _MAX_PAYLOAD_BYTES or payload_bytes != len(raw) - _HEADER_BYTES,
        "BAD_LENGTH",
    )
    version = GraphVersion(
        session=raw[48:64],
        graph=raw[64:80],
        profile_bits=_u64(raw, 80),
        semantic_revision=_u64(raw, 88),
        layout_revision=_u64(raw, 96),
        locator_epoch=_u64(raw, 104),
        semantic_root=raw[112:144],
        layout_root=raw[144:176],
        capture_root=raw[176:208],
        semantic_certified=bool(flags & 32),
        layout_present=bool(flags & 1),
    )
    cursor = raw[208:224]
    previous = raw[224:256]
    _fail(
        not _message_shape(
            message,
            flags,
            sequence,
            offset,
            total,
            version.session,
            cursor,
            previous,
            version,
        ),
        "BAD_HEADER",
    )
    has_version = _has_version(version)
    _fail(has_version and not _valid_version(version), "BAD_VERSION")
    payload: bytes | memoryview
    if not validate_graph_payload and message is MessageKind.GRAPH_CHUNK:
        # The frame owns ``raw`` for the lifetime of this view. Avoid copying
        # every full graph chunk solely to expose its authenticated payload.
        payload = memoryview(raw)[320:]
    else:
        payload = raw[320:]
    if validate_graph_payload or message is not MessageKind.GRAPH_CHUNK:
        _fail(
            not _payload_shape(
                message,
                flags,
                sequence,
                offset,
                total,
                cursor,
                previous,
                version,
                cast(bytes, payload),
            ),
            "BAD_FIELD",
        )
    chunk_hash = hashlib.sha256(b"HWPGRAPH\0CHUNK\0V1")
    chunk_hash.update(memoryview(raw)[:256])
    chunk_hash.update(bytes(64))
    chunk_hash.update(payload)
    chunk = chunk_hash.digest()
    chain_hash = hashlib.sha256(b"HWPGRAPH\0CHAIN\0V1")
    chain_hash.update(previous)
    chain_hash.update(chunk)
    chain_hash.update(memoryview(raw)[24:32])
    chain = chain_hash.digest()
    _fail(chunk != raw[256:288] or chain != raw[288:320], "DIGEST_MISMATCH")
    return NativeFrame(
        raw,
        message,
        flags,
        sequence,
        offset,
        total,
        cursor,
        previous,
        chunk,
        chain,
        version,
        payload,
    )


_PAYLOAD_MODELS: Final[dict[MessageKind, type[NativeFramePayload]]] = {
    MessageKind.CAPABILITIES: NativeCapabilitiesPayload,
    MessageKind.OPEN_RECEIPT: NativeOpenReceiptPayload,
    MessageKind.GRAPH_TERMINAL: NativeGraphTerminalPayload,
    MessageKind.ERROR: NativeErrorPayload,
    MessageKind.PATCH_BEGIN_RECEIPT: NativePatchBeginReceiptPayload,
    MessageKind.PATCH_CHUNK_RECEIPT: NativePatchChunkReceiptPayload,
    MessageKind.PATCH_SEAL_RECEIPT: NativePatchSealReceiptPayload,
    MessageKind.CURSOR_CLOSED_RECEIPT: NativeCursorClosedReceiptPayload,
    MessageKind.PATCH_ABORT_RECEIPT: NativePatchAbortReceiptPayload,
    MessageKind.PATCH_VALIDATION_RECEIPT: NativePatchValidationReceiptPayload,
    MessageKind.BLOB_CHUNK: NativeBlobChunkPayload,
    MessageKind.BLOB_PACK: NativeBlobPackPayload,
    MessageKind.GRAPH_OPEN_REQUEST: NativeGraphOpenRequestPayload,
    MessageKind.GRAPH_NEXT_REQUEST: NativeGraphNextRequestPayload,
    MessageKind.GRAPH_CANCEL_REQUEST: NativeGraphCancelRequestPayload,
    MessageKind.GRAPH_CLOSE_REQUEST: NativeGraphCloseRequestPayload,
    MessageKind.PATCH_BEGIN_REQUEST: NativePatchBeginRequestPayload,
    MessageKind.PATCH_CHUNK_REQUEST: NativePatchChunkRequestPayload,
    MessageKind.PATCH_COMMIT_REQUEST: NativePatchCommitRequestPayload,
    MessageKind.PATCH_ABORT_REQUEST: NativePatchAbortRequestPayload,
    MessageKind.PATCH_VALIDATE_REQUEST: NativePatchValidateRequestPayload,
    MessageKind.BLOB_READ_REQUEST: NativeBlobReadRequestPayload,
    MessageKind.BLOB_PACK_REQUEST: NativeBlobPackRequestPayload,
}


def count_graph_fragments(payload: bytes | memoryview) -> int:
    """Count bounded fragment descriptors without materializing typed models."""
    count = 0
    offset = 0
    while offset < len(payload):
        _fail(len(payload) - offset < 56, "BAD_FRAGMENT_LENGTH")
        size = _u64(payload, offset + 48)
        _fail(size > len(payload) - offset - 56, "BAD_FRAGMENT_LENGTH")
        offset += 56 + size
        count += 1
    return count


def _parsed_fragments(payload: bytes) -> tuple[NativeGraphFragment, ...]:
    fragments: list[NativeGraphFragment] = []
    offset = 0
    while offset < len(payload):
        size = _u64(payload, offset + 48)
        fragments.append(
            NativeGraphFragment(
                kind=RecordKind(_u16(payload, offset)),
                record_flags=_u16(payload, offset + 2),
                tag=_u16(payload, offset + 4),
                marked_flags=_u16(payload, offset + 6),
                scalar=ScalarTag(_u16(payload, offset + 8)),
                field_count=_u16(payload, offset + 10),
                record_id=_u64(payload, offset + 16),
                element_count=_u64(payload, offset + 24),
                field_total=_u64(payload, offset + 32),
                field_offset=_u64(payload, offset + 40),
                data=payload[offset + 56 : offset + 56 + size],
            )
        )
        offset += 56 + size
    return tuple(fragments)


def _parsed_payload(frame: NativeFrame) -> NativeFramePayload:
    payload = bytes(frame.payload)
    if frame.message is MessageKind.GRAPH_CHUNK:
        return NativeGraphChunkPayload(
            raw=payload,
            fields=(),
            fragments=_parsed_fragments(payload),
        )
    return _PAYLOAD_MODELS[frame.message](
        raw=payload,
        fields=_fields(payload),
    )


def authenticate_supported_frame_model(model: SupportedNativeFrame) -> None:
    """Authenticate a typed model against its raw frame without constructing a model."""
    frame = decode_frame(model.raw)
    mirrors_match = (
        int(frame.message) == model.message
        and frame.flags == model.flags
        and frame.sequence == model.sequence
        and frame.fragment_offset == model.fragment_offset
        and frame.fragment_total == model.fragment_total
        and frame.cursor == model.cursor
        and frame.previous_chain == model.previous_chain
        and frame.chunk_digest == model.chunk_digest
        and frame.chain_digest == model.chain_digest
        and frame.version.serialized == model.version.serialized
        and frame.payload == model.payload
        and _parsed_payload(frame) == model.parsed_payload
    )
    _fail(not mirrors_match, "MODEL_MISMATCH")


def validate_supported_frame(
    raw: bytes, *, validate_graph_payload: bool = True
) -> SupportedNativeFrame:
    """Decode and construct the strict discriminated model for a supported frame."""
    frame = decode_frame(raw, validate_graph_payload=validate_graph_payload)
    version = NativeFrameVersion(
        session=frame.version.session,
        graph=frame.version.graph,
        profile_bits=frame.version.profile_bits,
        semantic_revision=frame.version.semantic_revision,
        layout_revision=frame.version.layout_revision,
        locator_epoch=frame.version.locator_epoch,
        semantic_root=frame.version.semantic_root,
        layout_root=frame.version.layout_root,
        capture_root=frame.version.capture_root,
        semantic_certified=frame.version.semantic_certified,
        layout_present=frame.version.layout_present,
    )
    return cast(
        SupportedNativeFrame,
        TypeAdapter(SupportedNativeFrame).validate_python(
            {
                "message": int(frame.message),
                "raw": frame.raw,
                "flags": frame.flags,
                "sequence": frame.sequence,
                "fragment_offset": frame.fragment_offset,
                "fragment_total": frame.fragment_total,
                "cursor": frame.cursor,
                "previous_chain": frame.previous_chain,
                "chunk_digest": frame.chunk_digest,
                "chain_digest": frame.chain_digest,
                "version": version,
                "payload": frame.payload,
                "parsed_payload": _parsed_payload(frame),
            },
            strict=True,
        ),
    )


def _raw_value(field: NativeField) -> bytes:
    if not isinstance(field.value, NativeScalar):
        return b""
    return field.value.raw if isinstance(field.value.raw, bytes) else b""


def _exact(
    field: NativeField | None, scalar: ScalarTag, size: int, required: bool = True
) -> bool:
    return (
        field is not None
        and field.scalar is scalar
        and field.flags == (1 if required else 0)
        and field.element_count == 1
        and len(_raw_value(field)) == size
    )


def _fragment_payload_shape(
    payload: bytes, fragment_offset: int, fragment_total: int, fragmented: bool
) -> bool:
    if len(payload) < 56:
        return False
    at = 0
    reconstructed = 0
    have_record = False
    record_started = False
    record_ended = False
    current_record = 0
    current_kind = 0
    current_field = 0
    field_count = fields_seen = 0
    current_scalar = current_flags = 0
    current_elements = current_total = current_bytes = 0
    while at != len(payload):
        if len(payload) - at < 56 or _u32(payload, at + 12) != 56:
            return False
        kind = _u16(payload, at)
        record_flags = _u16(payload, at + 2)
        tag = _u16(payload, at + 4)
        marked = _u16(payload, at + 6)
        scalar = _u16(payload, at + 8)
        count = _u16(payload, at + 10)
        record = _u64(payload, at + 16)
        elements = _u64(payload, at + 24)
        field_total = _u64(payload, at + 32)
        field_offset = _u64(payload, at + 40)
        size = _u64(payload, at + 48)
        if (
            kind > 8
            or record_flags & ~1
            or tag == 0
            or marked & ~0x3F
            or scalar > 17
            or count == 0
            or size > len(payload) - at - 56
            or field_offset > field_total
            or size > field_total - field_offset
            or (field_total > 0 and size == 0)
            or (field_total == 0 and (field_offset or size or marked & 12 != 12))
        ):
            return False
        first_field, last_field, first_record, last_record = (
            bool(marked & bit) for bit in (4, 8, 16, 32)
        )
        if (
            first_field != (field_offset == 0)
            or last_field != (field_offset + size == field_total)
            or (first_record and not first_field)
            or (last_record and not last_field)
        ):
            return False
        if not have_record or record != current_record:
            if (
                not have_record
                and fragment_offset == 0
                and (not first_record or record != 0)
            ) or (
                have_record
                and (
                    not record_ended or record != current_record + 1 or not first_record
                )
            ):
                return False
            current_record, current_kind, current_field, field_count, fields_seen = (
                record,
                kind,
                0,
                count,
                0,
            )
            have_record, record_started, record_ended = True, first_record, False
            if not first_field:
                current_field, current_scalar, current_flags = tag, scalar, marked & 3
                current_elements, current_total, current_bytes = (
                    elements,
                    field_total,
                    field_offset,
                )
        elif (
            record_ended or kind != current_kind or count != field_count or first_record
        ):
            return False
        if first_field:
            if tag <= current_field:
                return False
            current_field, current_scalar, current_flags = tag, scalar, marked & 3
            current_elements, current_total, current_bytes = elements, field_total, 0
            fields_seen += 1
            reconstructed += 24
        elif (tag, scalar, marked & 3, elements, field_total, field_offset) != (
            current_field,
            current_scalar,
            current_flags,
            current_elements,
            current_total,
            current_bytes,
        ):
            return False
        if first_record:
            reconstructed += 24
        current_bytes += size
        reconstructed += size
        if last_field and field_offset == 0:
            if (
                current_kind == RecordKind.PROPERTY
                and current_field == 4
                and scalar != ScalarTag.STRUCT
            ):
                raise GraphProtocolError("BAD_SCALAR")
            try:
                raw_value = payload[at + 56 : at + 56 + size]
                scalar_tag = ScalarTag(scalar)
                if current_flags & 2:
                    _ = _array_value(scalar_tag, elements, raw_value)
                elif current_kind == RecordKind.PROPERTY and current_field == 4:
                    _ = _observation(ScalarTag.STRUCT, raw_value)
                else:
                    _ = _scalar(scalar_tag, raw_value)
            except (GraphProtocolError, ValueError):
                return False
        if last_record:
            if record_started and fields_seen != field_count:
                return False
            record_ended = True
        at += 56 + size
    if (
        not have_record
        or (fragment_offset == 0 and not record_started)
        or fragment_offset > fragment_total
        or reconstructed > fragment_total - fragment_offset
    ):
        return False
    more = fragment_offset + reconstructed < fragment_total
    return fragmented == more and (more or record_ended)


def _payload_shape(
    message: MessageKind,
    flags: int,
    sequence: int,
    offset: int,
    total: int,
    cursor: bytes,
    previous: bytes,
    version: GraphVersion,
    payload: bytes,
) -> bool:
    if message is MessageKind.GRAPH_CHUNK:
        return _fragment_payload_shape(payload, offset, total, bool(flags & 8))
    try:
        fields = _fields(payload)
    except GraphProtocolError:
        return False
    by_tag = {field.tag: field for field in fields}
    tags = set(by_tag)

    def exact_tags(required: set[int], optional: set[int] | None = None) -> bool:
        allowed_optional = set[int]() if optional is None else optional
        return (
            tags <= required | allowed_optional
            and required <= tags
            and all(by_tag[tag].flags & 1 for tag in required)
        )

    if message is MessageKind.CAPABILITIES:
        expected = (
            (1, ScalarTag.UINT32, 4, 15),
            (2, ScalarTag.UINT16, 2, 1),
            (3, ScalarTag.UINT64, 8, _MAX_FRAME_BYTES),
            (4, ScalarTag.UINT64, 8, _MAX_PAYLOAD_BYTES),
            (5, ScalarTag.UINT64, 8, 2**64 - 1),
        )
        return (
            exact_tags(set(range(1, 7)))
            and all(
                _exact(by_tag.get(tag), scalar, size)
                and int.from_bytes(_raw_value(by_tag[tag]), "little") == value
                for tag, scalar, size, value in expected
            )
            and _exact(by_tag.get(6), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[6]), "little") in {16, 17, 48, 49, 57}
        )
    if message is MessageKind.OPEN_RECEIPT:
        return (
            exact_tags({1, 2})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.STRUCT, 168)
            and _raw_value(by_tag[2]) == version.serialized
        )
    if message is MessageKind.GRAPH_NEXT_REQUEST:
        return (
            exact_tags({1, 2, 3, 4, 5})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.STRUCT, 168)
            and _raw_value(by_tag[2]) == version.serialized
            and _exact(by_tag.get(3), ScalarTag.UINT64, 8)
            and _exact(by_tag.get(4), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[4]), "little") == sequence
            and _exact(by_tag.get(5), ScalarTag.SHA256, 32)
            and _raw_value(by_tag[5]) == previous
        )
    if message is MessageKind.GRAPH_TERMINAL:
        return (
            exact_tags({1, 2, 3})
            and _exact(by_tag.get(1), ScalarTag.UINT64, 8)
            and _exact(by_tag.get(2), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[2]), "little") == total
            and _exact(by_tag.get(3), ScalarTag.SHA256, 32)
        )
    if message is MessageKind.BLOB_READ_REQUEST:
        return (
            exact_tags(set(range(1, 9)))
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.STRUCT, 168)
            and _raw_value(by_tag[2]) == version.serialized
            and _exact(by_tag.get(3), ScalarTag.UUID128, 16)
            and _exact(by_tag.get(4), ScalarTag.UINT64, 8)
            and _exact(by_tag.get(5), ScalarTag.UINT64, 8)
            and _exact(by_tag.get(6), ScalarTag.SHA256, 32)
            and _exact(by_tag.get(7), ScalarTag.UINT64, 8)
            and _exact(by_tag.get(8), ScalarTag.UINT64, 8)
            and 1 <= int.from_bytes(_raw_value(by_tag[8]), "little") <= 32768
        )
    if message is MessageKind.BLOB_PACK_REQUEST:
        tuples = _raw_value(by_tag[4]) if 4 in by_tag else b""
        return (
            exact_tags({1, 2, 3, 4, 5})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.STRUCT, 168)
            and _raw_value(by_tag[2]) == version.serialized
            and _exact(by_tag.get(3), ScalarTag.UUID128, 16)
            and by_tag[4].scalar is ScalarTag.BYTES
            and by_tag[4].flags == 1
            and len(tuples) >= 8
            and int.from_bytes(tuples[:8], "little") == len(tuples) - 8
            and _exact(by_tag.get(5), ScalarTag.UINT64, 8)
        )
    if message is MessageKind.BLOB_CHUNK:
        content = _raw_value(by_tag[6]) if 6 in by_tag else b""
        chunk_length = _u64(content) if len(content) >= 8 else -1
        relative = (
            int.from_bytes(_raw_value(by_tag[5]), "little")
            if _exact(by_tag.get(5), ScalarTag.UINT64, 8)
            else -1
        )
        return (
            exact_tags(set(range(1, 7)))
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _exact(by_tag.get(2), ScalarTag.UINT64, 8)
            and _exact(by_tag.get(3), ScalarTag.UINT64, 8)
            and _exact(by_tag.get(4), ScalarTag.SHA256, 32)
            and relative == offset
            and by_tag[6].scalar is ScalarTag.BYTES
            and by_tag[6].flags == 1
            and len(content) == 8 + chunk_length
            and 0 <= chunk_length <= 32768
            and offset + chunk_length <= total
            and bool(flags & 2) == (offset + chunk_length == total)
        )
    if message is MessageKind.BLOB_PACK:
        content = _raw_value(by_tag[3]) if 3 in by_tag else b""
        return (
            exact_tags({1, 2, 3, 4})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _exact(by_tag.get(2), ScalarTag.UINT64, 8)
            and by_tag[3].scalar is ScalarTag.BYTES
            and by_tag[3].flags == 1
            and len(content) >= 8
            and int.from_bytes(content[:8], "little") == len(content) - 8
            and _exact(by_tag.get(4), ScalarTag.SHA256, 32)
        )
    if message is MessageKind.ERROR:
        return (
            exact_tags({1, 2, 3, 4})
            and _exact(by_tag.get(1), ScalarTag.UINT32, 4)
            and _exact(by_tag.get(2), ScalarTag.SINT32, 4)
            and _exact(by_tag.get(3), ScalarTag.UINT64, 8)
            and by_tag[4].scalar is ScalarTag.UTF16
            and by_tag[4].flags == 1
        )
    if message in {MessageKind.GRAPH_CANCEL_REQUEST, MessageKind.GRAPH_CLOSE_REQUEST}:
        return (
            exact_tags({1, 2})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.UUID128, 16)
        )
    if message in {MessageKind.CURSOR_CLOSED_RECEIPT, MessageKind.PATCH_ABORT_RECEIPT}:
        return (
            exact_tags({1, 2})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.UINT8, 1)
        )
    if message is MessageKind.PATCH_BEGIN_RECEIPT:
        return (
            exact_tags({1, 2, 3})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[2]), "little") == total
            and _exact(by_tag.get(3), ScalarTag.SHA256, 32)
        )
    if message is MessageKind.PATCH_CHUNK_RECEIPT:
        return (
            exact_tags({1, 2})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[2]), "little") == offset
        )
    if message is MessageKind.PATCH_SEAL_RECEIPT:
        return (
            exact_tags({1, 2, 3})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[2]), "little") == total
            and _exact(by_tag.get(3), ScalarTag.SHA256, 32)
        )
    if message is MessageKind.GRAPH_OPEN_REQUEST:
        return (
            exact_tags({1, 3, 4, 5}, {2})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == version.session
            and _exact(by_tag.get(3), ScalarTag.UINT64, 8)
            and by_tag[4].scalar is ScalarTag.STRUCT
            and by_tag[4].flags == 1
            and _exact(by_tag.get(5), ScalarTag.UINT64, 8)
            and _query_shape(
                _raw_value(by_tag[4]), int.from_bytes(_raw_value(by_tag[3]), "little")
            )
            and (
                2 not in by_tag
                or (
                    _exact(by_tag.get(2), ScalarTag.STRUCT, 168, False)
                    and _raw_value(by_tag[2]) == version.serialized
                )
            )
        )
    if message is MessageKind.PATCH_BEGIN_REQUEST:
        return (
            exact_tags({1, 2, 3, 4})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == version.session
            and _exact(by_tag.get(2), ScalarTag.STRUCT, 168)
            and _raw_value(by_tag[2]) == version.serialized
            and _exact(by_tag.get(3), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[3]), "little") == total
            and _exact(by_tag.get(4), ScalarTag.SHA256, 32)
        )
    if message is MessageKind.PATCH_CHUNK_REQUEST:
        value = _raw_value(by_tag[3]) if 3 in by_tag else b""
        declared = _u64(value) if len(value) >= 8 else -1
        return (
            exact_tags({1, 2, 3})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[2]), "little") == offset
            and by_tag[3].flags == 1
            and by_tag[3].scalar is ScalarTag.BYTES
            and declared == len(value) - 8
            and offset <= total
            and declared <= total - offset
            and bool(flags & 8) == (offset + declared < total)
        )
    if message is MessageKind.PATCH_COMMIT_REQUEST:
        return (
            exact_tags({1, 2, 3})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.UINT64, 8)
            and int.from_bytes(_raw_value(by_tag[2]), "little") == total
            and _exact(by_tag.get(3), ScalarTag.SHA256, 32)
        )
    if message is MessageKind.PATCH_ABORT_REQUEST:
        return (
            exact_tags({1})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
        )
    if message is MessageKind.PATCH_VALIDATE_REQUEST:
        return (
            exact_tags({1, 2})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.STRUCT, 168)
            and _raw_value(by_tag[2]) == version.serialized
            and version.semantic_certified
        )
    if message is MessageKind.PATCH_VALIDATION_RECEIPT:
        return (
            exact_tags({1, 2, 3, 4, 5})
            and _exact(by_tag.get(1), ScalarTag.UUID128, 16)
            and _raw_value(by_tag[1]) == cursor
            and _exact(by_tag.get(2), ScalarTag.UINT8, 1)
            and _raw_value(by_tag[2]) == b"\x02"
            and _exact(by_tag.get(3), ScalarTag.SHA256, 32)
            and _exact(by_tag.get(4), ScalarTag.UINT64, 8)
            and _exact(by_tag.get(5), ScalarTag.SHA256, 32)
        )
    return False


def _query_shape(raw: bytes, closed_profiles: int) -> bool:
    try:
        fields = _fields(raw)
    except GraphProtocolError:
        return False
    by_tag = {field.tag: field for field in fields}
    if set(by_tag) - set(range(1, 10)) or not {2, 3, 4, 7, 8, 9} <= set(by_tag):
        return False
    if (
        not _exact(by_tag.get(2), ScalarTag.UINT8, 1)
        or _raw_value(by_tag[2])[0] > 4
        or not _exact(by_tag.get(3), ScalarTag.UINT64, 8)
        or not _exact(by_tag.get(4), ScalarTag.UINT64, 8)
        or not _exact(by_tag.get(8), ScalarTag.UINT64, 8)
        or not _exact(by_tag.get(9), ScalarTag.UINT8, 1)
        or _raw_value(by_tag[9]) != b"\0"
    ):
        return False
    predicates = by_tag[7]
    if predicates.flags != 3 or predicates.scalar is not ScalarTag.STRUCT:
        return False
    predicate_raw = _raw_value(predicates)
    if (
        len(predicate_raw) < 16
        or _u16(predicate_raw) != ScalarTag.STRUCT
        or _u16(predicate_raw, 2) != 0
        or _u32(predicate_raw, 4) != 0
        or _u64(predicate_raw, 8) != predicates.element_count
    ):
        return False
    nodes = int.from_bytes(_raw_value(by_tag[3]), "little")
    edges = int.from_bytes(_raw_value(by_tag[4]), "little")
    projections = int.from_bytes(_raw_value(by_tag[8]), "little")
    axis = _raw_value(by_tag[2])[0]
    if (
        projections & ~0xFFF
        or nodes & ~0x1FFF
        or edges & ~0x3FFF
        or (axis != 4 and edges)
    ):
        return False
    required = 0
    if (
        projections & ((1 << 0) | (1 << 10) | (1 << 11))
        or nodes & 0x78F
        or edges & 0x3383
    ):
        required |= 1
    if projections & ((1 << 1) | (1 << 2) | (1 << 3)) or nodes & 0x70 or edges & 0xC2C:
        required |= 2
    if (
        projections & ((1 << 4) | (1 << 5) | (1 << 6) | (1 << 7))
        or nodes & 0x1000
        or edges & 0x10
    ):
        required |= 4
    if projections & (1 << 8) or nodes & (1 << 11) or edges & (1 << 6):
        required |= 16
    if projections & (1 << 9) or 5 in by_tag:
        required |= 8
    if required & 4:
        required |= 3
    if required & (2 | 8 | 16):
        required |= 1
    return required == closed_profiles and _profiles_are_closed(closed_profiles)


def _content_length(raw: NativeContent) -> int:
    return len(raw) if isinstance(raw, bytes) else raw.length


def _content_read(
    raw: NativeContent, offset: int = 0, length: int | None = None
) -> bytes:
    if isinstance(raw, bytes):
        return raw[offset:] if length is None else raw[offset : offset + length]
    actual = raw.length - offset if length is None else length
    with raw.path.open("rb") as source:
        _ = source.seek(raw.offset + offset)
        value = source.read(actual)
    _fail(len(value) != actual, "SPOOL_TRUNCATED")
    return value


def _content_slice(raw: NativeContent, offset: int, length: int) -> NativeContent:
    if isinstance(raw, bytes):
        return raw[offset : offset + length]
    digest = hashlib.sha256()
    with raw.path.open("rb") as source:
        _ = source.seek(raw.offset + offset)
        remaining = length
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            _fail(not chunk, "SPOOL_TRUNCATED")
            remaining -= len(chunk)
            digest.update(chunk)
    return NativeSpooledBytes(
        path=raw.path,
        offset=raw.offset + offset,
        length=length,
        sha256=digest.hexdigest(),
    )


def _scalar(tag: ScalarTag, raw: NativeContent) -> NativeScalarValue:
    try:
        raw_length = _content_length(raw)
        fixed = _content_read(raw) if raw_length <= 64 else b""
        if tag in {ScalarTag.SINT64, ScalarTag.UINT64, ScalarTag.HWPUNIT64}:
            _fail(raw_length != 8, "BAD_SCALAR")
            return NativeInteger.model_construct(
                scalar=tag,
                raw=raw,
                value=int.from_bytes(fixed, "little", signed=tag is ScalarTag.SINT64),
            )
        if tag in {
            ScalarTag.BGR,
            ScalarTag.RAW_URC32,
            ScalarTag.UINT32,
            ScalarTag.SINT32,
        }:
            _fail(raw_length != 4, "BAD_SCALAR")
            return NativeInteger.model_construct(
                scalar=tag,
                raw=raw,
                value=int.from_bytes(fixed, "little", signed=tag is ScalarTag.SINT32),
            )
        if tag in {ScalarTag.UINT8, ScalarTag.UINT16}:
            size = 1 if tag is ScalarTag.UINT8 else 2
            _fail(raw_length != size, "BAD_SCALAR")
            return NativeInteger.model_construct(
                scalar=tag, raw=raw, value=int.from_bytes(fixed, "little")
            )
        if tag is ScalarTag.BOOL:
            _fail(fixed not in {b"\0", b"\1"}, "BAD_SCALAR")
            return NativeBoolean.model_construct(
                scalar=tag, raw=raw, value=fixed == b"\1"
            )
        if tag is ScalarTag.FLOAT64:
            _fail(raw_length != 8, "BAD_SCALAR")
            value = _f64(fixed)
            _fail(
                not math.isfinite(value) or (value == 0 and fixed != bytes(8)),
                "BAD_SCALAR",
            )
            return NativeFloat.model_construct(scalar=tag, raw=raw, value=value)
        if tag in {ScalarTag.UTF16, ScalarTag.BYTES}:
            _fail(raw_length < 8, "BAD_SCALAR")
            prefix = _content_read(raw, 0, 8)
            count = _u64(prefix)
            width = 2 if tag is ScalarTag.UTF16 else 1
            _fail(
                count > (2**64 - 1) // width or 8 + count * width != raw_length,
                "BAD_SCALAR",
            )
            if tag is ScalarTag.UTF16:
                return NativeRawUtf16.model_construct(
                    scalar=tag, raw=_content_slice(raw, 8, count * 2), code_units=count
                )
            return NativeOpaque.model_construct(scalar=tag, raw=raw)
        if tag is ScalarTag.UUID128:
            _fail(raw_length != 16, "BAD_SCALAR")
            return NativeIdentifier.model_construct(scalar=tag, raw=raw, value=fixed)
        if tag is ScalarTag.SHA256:
            _fail(raw_length != 32, "BAD_SCALAR")
            return NativeDigest.model_construct(scalar=tag, raw=raw, value=fixed)
        if tag is ScalarTag.ENUM:
            _fail(raw_length < 16, "BAD_SCALAR")
            header = _content_read(raw, 0, 16)
            _fail(header[8] > 1 or any(header[9:16]), "BAD_SCALAR")
            if header[8] == 0:
                _fail(raw_length != 16, "BAD_SCALAR")
            else:
                _ = _scalar(ScalarTag.UTF16, _content_slice(raw, 16, raw_length - 16))
            return NativeOpaque.model_construct(scalar=tag, raw=raw)
        if tag is ScalarTag.BLOB_SLICE:
            _fail(raw_length != 64, "BAD_SCALAR")
            return NativeBlobSlice.model_construct(
                scalar=tag,
                raw=raw,
                content_id=fixed[:16],
                offset=_u64(fixed, 16),
                length=_u64(fixed, 24),
                digest=fixed[32:],
            )
        return NativeOpaque.model_construct(scalar=tag, raw=raw)
    except ValidationError as error:
        raise GraphProtocolError("CONTENT_LIMIT", str(error)) from error


def _observation(
    tag: ScalarTag, raw: NativeContent
) -> NativeGraphPresent | NativeGraphUnavailable:
    raw_length = _content_length(raw)
    _fail(raw_length < 24, "BAD_OBSERVATION")
    header = _content_read(raw, 0, 24)
    state = header[0]
    present = header[1]
    reserved = _u16(header, 2)
    hresult = _i32(header, 4)
    detail_count = _u64(header, 8)
    value_bytes = _u64(header, 16)
    _fail(state > 6 or present > 1 or reserved != 0, "BAD_OBSERVATION")
    detail_end = 24 + detail_count * 2
    _fail(
        detail_count > (2**64 - 1) // 2
        or detail_end > raw_length
        or detail_end + value_bytes != raw_length,
        "BAD_OBSERVATION",
    )
    detail = NativeRawUtf16.model_construct(
        scalar=ScalarTag.UTF16,
        raw=_content_slice(raw, 24, detail_count * 2),
        code_units=detail_count,
    )
    if state == 0:
        _fail(present != 1 or hresult != 0, "BAD_OBSERVATION")
        return NativeGraphPresent.model_construct(
            value=_scalar(tag, _content_slice(raw, detail_end, value_bytes))
        )
    _fail(present != 0 or value_bytes != 0, "BAD_OBSERVATION")
    return NativeGraphUnavailable.model_construct(
        reason=UnavailableReason(state), hresult=hresult, detail=detail
    )


def _array_value(scalar: ScalarTag, count: int, raw: bytes) -> NativeOpaque:
    _fail(
        len(raw) < 16
        or _u16(raw) != scalar
        or _u16(raw, 2) != 0
        or _u32(raw, 4) != 0
        or _u64(raw, 8) != count,
        "BAD_ARRAY",
    )
    offset = 16
    for _ in range(count):
        _fail(len(raw) - offset < 8, "BAD_ARRAY")
        length = _u64(raw, offset)
        offset += 8
        _fail(length > len(raw) - offset, "BAD_ARRAY")
        _ = _scalar(scalar, raw[offset : offset + length])
        offset += length
    _fail(offset != len(raw), "BAD_ARRAY")
    return NativeOpaque.model_construct(scalar=scalar, raw=raw)


def decode_nested_fields(value: NativeOpaque) -> tuple[NativeField, ...]:
    """Decode one authenticated STRUCT payload into typed nested fields."""
    if value.scalar is not ScalarTag.STRUCT or not isinstance(value.raw, bytes):
        raise GraphProtocolError("BAD_STRUCT")
    return _fields(value.raw)


def _fields(
    raw: bytes | memoryview, *, observation_tag: int | None = None
) -> tuple[NativeField, ...]:
    result: list[NativeField] = []
    offset = 0
    prior_tag = 0
    while offset < len(raw):
        _fail(len(raw) - offset < 24, "BAD_FIELD_LENGTH")
        tag = _u16(raw, offset)
        flags = _u16(raw, offset + 2)
        scalar_raw = _u16(raw, offset + 4)
        reserved = _u16(raw, offset + 6)
        count = _u64(raw, offset + 8)
        length = _u64(raw, offset + 16)
        offset += 24
        _fail(
            tag == 0
            or tag <= prior_tag
            or flags & ~3 != 0
            or reserved != 0
            or (flags & 2 == 0 and count != 1)
            or length > len(raw) - offset,
            "BAD_FIELD",
        )
        try:
            scalar = ScalarTag(scalar_raw)
        except ValueError as error:
            raise GraphProtocolError("BAD_SCALAR_TAG") from error
        value = bytes(raw[offset : offset + length])
        offset += length
        if flags & 2:
            decoded = _array_value(scalar, count, value)
        elif tag == observation_tag:
            decoded = _observation(scalar, value)
        elif len(value) >= 24:
            try:
                decoded = _observation(scalar, value)
            except GraphProtocolError:
                decoded = _scalar(scalar, value)
        else:
            decoded = _scalar(scalar, value)
        result.append(
            NativeField.model_construct(
                tag=tag, flags=flags, scalar=scalar, element_count=count, value=decoded
            )
        )
        prior_tag = tag
    return tuple(result)


MAX_PAYLOAD_BYTES = _MAX_PAYLOAD_BYTES
ZERO_DIGEST = _ZERO_DIGEST
decode_array = _array_value
decode_fields = _fields
decode_scalar = _scalar
decode_observation = _observation
content_read = _content_read
fail = _fail
read_u16 = _u16
read_u32 = _u32
read_u64 = _u64


__all__ = [
    "GraphProtocolError",
    "GraphVersion",
    "MessageKind",
    "NativeFrame",
    "decode_nested_fields",
    "decode_array",
    "decode_frame",
    "decode_observation",
    "decode_scalar",
]

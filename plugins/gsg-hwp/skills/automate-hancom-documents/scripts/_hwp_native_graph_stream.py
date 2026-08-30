from __future__ import annotations

import hashlib
import struct
import tempfile
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import assert_never, final, override

from pydantic import ValidationError

from hwp_native_graph_models import (
    NativeAssetChunkRecord,
    NativeContent,
    NativeCoverageRecord,
    NativeDiagnosticRecord,
    NativeDigest,
    NativeEdgeRecord,
    NativeField,
    NativeGraphPresent,
    NativeGraphUnavailable,
    NativeIdentifier,
    NativeInteger,
    NativeManifestRecord,
    NativeNodeRecord,
    NativeOpaque,
    NativePropertyRecord,
    NativeRemapRecord,
    NativeSpooledBytes,
    NativeTombstoneRecord,
    NodeKind,
    RecordKind,
    ScalarTag,
    SupportedNativeFrame,
    TypedNativeRecord,
)
from _hwp_native_graph_property_registry import NATIVE_PROPERTY_REGISTRY
from _hwp_native_graph_spool import FieldSpool
from _hwp_native_graph_wire import (
    ZERO_DIGEST,
    GraphProtocolError,
    GraphVersion,
    MessageKind,
    NativeFrame,
    content_read,
    decode_array,
    decode_fields,
    decode_observation,
    decode_scalar,
    decode_frame,
    fail,
    read_u16,
    read_u32,
    read_u64,
)


@dataclass(frozen=True, slots=True)
class AuthenticatedGraphFrame:
    raw: bytes
    frame: NativeFrame | SupportedNativeFrame
    version: GraphVersion


@dataclass(slots=True)
class _Pending:
    kind: RecordKind
    record_id: int
    record_flags: int
    field_count: int
    fields: list[tuple[int, int, ScalarTag, int, NativeContent]]
    tag: int
    flags: int
    scalar: ScalarTag
    count: int
    total: int
    value: FieldSpool
    retained: list[FieldSpool]
    logical_offset: int


@final
class GraphDecodeSession(Iterator[TypedNativeRecord]):
    __slots__ = ("_closed", "_decoder", "_entered", "_iterator", "_paths")

    def __init__(self, decoder: GraphStreamDecoder) -> None:
        self._closed = False
        self._decoder = decoder
        self._entered = False
        self._paths: set[Path] = set()
        self._iterator = decoder.decode_records(self)

    def adopt(self, path: Path) -> None:
        if self._closed:
            path.unlink(missing_ok=True)
            raise GraphProtocolError("SPOOL_CLOSED")
        self._paths.add(path)

    @override
    def __iter__(self) -> GraphDecodeSession:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        if not self._entered:
            raise GraphProtocolError("SCOPE_REQUIRED")
        return self

    @override
    def __next__(self) -> TypedNativeRecord:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        if not self._entered:
            raise GraphProtocolError("SCOPE_REQUIRED")
        try:
            return next(self._iterator)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._iterator.close()
        self._decoder.close_untransferred()
        for path in self._paths:
            path.unlink(missing_ok=True)
        self._paths.clear()
        self._decoder.release(self)

    def __enter__(self) -> GraphDecodeSession:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc_value, traceback)
        self.close()


class GraphStreamDecoder:
    _closed: bool
    _frames: Iterable[bytes | AuthenticatedGraphFrame]
    _fragment_observer: Callable[[int, int], None] | None
    _record_observer: Callable[[RecordKind, int, int, int], None] | None
    _logical_record_observer: Callable[[bytes], None] | None
    _owns_spool_root: bool
    _spool_root: Path
    version: GraphVersion | None
    record_count: int
    logical_bytes: int
    fragment_count: int
    field_count: int
    stream_digest: bytes

    def __init__(
        self,
        frames: Iterable[bytes | AuthenticatedGraphFrame],
        *,
        spool_root: Path | None = None,
        fragment_observer: Callable[[int, int], None] | None = None,
        record_observer: Callable[[RecordKind, int, int, int], None] | None = None,
        logical_record_observer: Callable[[bytes], None] | None = None,
    ) -> None:
        self._frames = frames
        self._fragment_observer = fragment_observer
        self._record_observer = record_observer
        self._logical_record_observer = logical_record_observer
        self._spools: set[FieldSpool] = set()
        self._sessions: set[GraphDecodeSession] = set()
        self._closed = False
        self._owns_spool_root = spool_root is None
        self._spool_root = (
            Path(tempfile.mkdtemp(prefix="hgn1-session-"))
            if spool_root is None
            else spool_root
        )
        self.version = None
        self.record_count = 0
        self.logical_bytes = 0
        self.fragment_count = 0
        self.field_count = 0
        self.stream_digest = b""

    def _new_spool(self) -> FieldSpool:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        return FieldSpool(self._spool_root)

    def records(self) -> GraphDecodeSession:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        session = GraphDecodeSession(self)
        self._sessions.add(session)
        return session

    def release(self, session: GraphDecodeSession) -> None:
        self._sessions.discard(session)
        if self._owns_spool_root and not self._sessions and self._spool_root.exists():
            self._spool_root.rmdir()

    def close_untransferred(self) -> None:
        for spool in self._spools:
            spool.close()
        self._spools.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for session in tuple(self._sessions):
            session.close()
        self.close_untransferred()
        if self._owns_spool_root and self._spool_root.exists():
            self._spool_root.rmdir()

    def __enter__(self) -> GraphStreamDecoder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc_value, traceback)
        self.close()

    def decode_records(
        self, owner: GraphDecodeSession
    ) -> Generator[TypedNativeRecord, None, None]:
        expected_sequence = 0
        previous = ZERO_DIGEST
        cursor: bytes | None = None
        reconstructed = 0
        record_count = 0
        stream_hash = hashlib.sha256(b"HWPGRAPH\0STREAM\0V1")
        pending: _Pending | None = None
        terminal = False
        last_raw: bytes | None = None
        for source in self._frames:
            # Private live transfers can pass the already authenticated frame;
            # public/fallback byte iterables retain the exact old decode path.
            if isinstance(source, AuthenticatedGraphFrame):
                raw = source.raw
                frame = source.frame
                frame_version = source.version
            else:
                raw = source
                frame = decode_frame(raw, validate_graph_payload=False)
                frame_version = frame.version
            if frame.sequence + 1 == expected_sequence:
                fail(last_raw is None or raw != last_raw, "DUPLICATE_DIFFERENT")
                continue
            fail(frame.sequence != expected_sequence, "SEQUENCE_MISMATCH")
            fail(frame.previous_chain != previous, "CHAIN_MISMATCH")
            if self.version is None:
                self.version = frame_version
                cursor = frame.cursor
            else:
                fail(
                    frame_version.serialized != self.version.serialized,
                    "GRAPH_VERSION_MISMATCH",
                )
                fail(frame.cursor != cursor, "CURSOR_MISMATCH")
            expected_sequence += 1
            previous = frame.chain_digest
            last_raw = raw
            if frame.message == MessageKind.OPEN_RECEIPT:
                fail(
                    frame.fragment_offset != 0 or frame.fragment_total != 0, "BAD_OPEN"
                )
                continue
            if frame.message == MessageKind.ERROR:
                raise GraphProtocolError("NATIVE_ERROR")
            if frame.message == MessageKind.GRAPH_TERMINAL:
                fail(
                    pending is not None
                    or frame.fragment_offset != reconstructed
                    or frame.fragment_total != reconstructed,
                    "INCOMPLETE_STREAM",
                )
                control = decode_fields(frame.payload)
                fail(len(control) != 3, "BAD_TERMINAL")
                values = [item.value for item in control]
                fail(
                    not isinstance(values[0], NativeInteger)
                    or values[0].value != record_count,
                    "BAD_TERMINAL",
                )
                fail(
                    not isinstance(values[1], NativeInteger)
                    or values[1].value != reconstructed,
                    "BAD_TERMINAL",
                )
                terminal_digest = values[2]
                fail(
                    not isinstance(terminal_digest, NativeDigest)
                    or terminal_digest.value != stream_hash.digest(),
                    "STREAM_DIGEST_MISMATCH",
                )
                if not isinstance(terminal_digest, NativeDigest):
                    raise GraphProtocolError("STREAM_DIGEST_MISMATCH")
                self.stream_digest = terminal_digest.value
                terminal = True
                continue
            fail(frame.message != MessageKind.GRAPH_CHUNK or terminal, "BAD_MESSAGE")
            fail(frame.fragment_offset != reconstructed, "FRAGMENT_OFFSET")
            at = 0
            frame_fragments = 0
            while at < len(frame.payload):
                frame_fragments += 1
                self.fragment_count += 1
                fail(len(frame.payload) - at < 56, "BAD_FRAGMENT_LENGTH")
                kind_raw = read_u16(frame.payload, at)
                record_flags = read_u16(frame.payload, at + 2)
                tag = read_u16(frame.payload, at + 4)
                marked_flags = read_u16(frame.payload, at + 6)
                scalar_raw = read_u16(frame.payload, at + 8)
                field_count = read_u16(frame.payload, at + 10)
                header_bytes = read_u32(frame.payload, at + 12)
                record_id = read_u64(frame.payload, at + 16)
                count = read_u64(frame.payload, at + 24)
                total = read_u64(frame.payload, at + 32)
                field_offset = read_u64(frame.payload, at + 40)
                size = read_u64(frame.payload, at + 48)
                at += 56
                fail(
                    header_bytes != 56 or size > len(frame.payload) - at,
                    "BAD_FRAGMENT_LENGTH",
                )
                data = frame.payload[at : at + size]
                at += size
                try:
                    kind, scalar = RecordKind(kind_raw), ScalarTag(scalar_raw)
                except ValueError as error:
                    raise GraphProtocolError("BAD_FRAGMENT_TAG") from error
                first_field, last_field, first_record, last_record = (
                    bool(marked_flags & bit) for bit in (4, 8, 16, 32)
                )
                base_flags = marked_flags & 3
                if pending is None:
                    fail(
                        not first_field
                        or not first_record
                        or record_id != record_count
                        or field_count == 0,
                        "BAD_FRAGMENT_ORDER",
                    )
                    pending = _Pending(
                        kind,
                        record_id,
                        record_flags,
                        field_count,
                        [],
                        tag,
                        base_flags,
                        scalar,
                        count,
                        total,
                        self._new_spool(),
                        [],
                        reconstructed,
                    )
                    reconstructed += 48
                elif first_field:
                    fail(
                        first_record or tag <= pending.tag or pending.value.length != 0,
                        "BAD_FRAGMENT_ORDER",
                    )
                    (
                        pending.tag,
                        pending.flags,
                        pending.scalar,
                        pending.count,
                        pending.total,
                    ) = tag, base_flags, scalar, count, total
                    reconstructed += 24
                else:
                    fail(
                        (kind, record_id, tag, base_flags, scalar, count, total)
                        != (
                            pending.kind,
                            pending.record_id,
                            pending.tag,
                            pending.flags,
                            pending.scalar,
                            pending.count,
                            pending.total,
                        ),
                        "BAD_FRAGMENT_ORDER",
                    )
                fail(
                    field_offset != pending.value.length or (total > 0 and size == 0),
                    "FRAGMENT_OFFSET",
                )
                pending.value.append(data)
                if pending.value.is_spooled:
                    self._spools.add(pending.value)
                reconstructed += size
                if last_field:
                    fail(pending.value.length != total, "BAD_FRAGMENT_LENGTH")
                    value = pending.value.content()
                    pending.fields.append((tag, base_flags, scalar, count, value))
                    self.field_count += 1
                    if isinstance(value, NativeSpooledBytes):
                        pending.retained.append(pending.value)
                    else:
                        pending.value.close()
                        self._spools.discard(pending.value)
                    if last_record:
                        fail(len(pending.fields) != field_count, "BAD_FIELD_COUNT")
                        fields_bytes = sum(
                            24 + _content_size(item[4]) for item in pending.fields
                        )
                        record_header = struct.pack(
                            "<HHHHQQ",
                            kind,
                            1,
                            record_flags,
                            field_count,
                            record_id,
                            fields_bytes,
                        )
                        if all(isinstance(item[4], bytes) for item in pending.fields):
                            logical_record = bytearray(record_header)
                            for (
                                ftag,
                                fflags,
                                fscalar,
                                fcount,
                                field_value,
                            ) in pending.fields:
                                assert isinstance(field_value, bytes)
                                logical_record.extend(
                                    struct.pack(
                                        "<HHHHQQ",
                                        ftag,
                                        fflags,
                                        fscalar,
                                        0,
                                        fcount,
                                        len(field_value),
                                    )
                                )
                                logical_record.extend(field_value)
                            logical_bytes = bytes(logical_record)
                            stream_hash.update(logical_bytes)
                            if self._logical_record_observer is not None:
                                self._logical_record_observer(logical_bytes)
                        else:
                            stream_hash.update(record_header)
                            if self._logical_record_observer is not None:
                                self._logical_record_observer(record_header)
                            for (
                                ftag,
                                fflags,
                                fscalar,
                                fcount,
                                field_value,
                            ) in pending.fields:
                                field_header = struct.pack(
                                    "<HHHHQQ",
                                    ftag,
                                    fflags,
                                    fscalar,
                                    0,
                                    fcount,
                                    _content_size(field_value),
                                )
                                stream_hash.update(field_header)
                                if self._logical_record_observer is not None:
                                    self._logical_record_observer(field_header)
                                if isinstance(field_value, bytes):
                                    chunks = (field_value,)
                                else:
                                    chunks = field_value.chunks()
                                for logical_chunk in chunks:
                                    stream_hash.update(logical_chunk)
                                    if self._logical_record_observer is not None:
                                        self._logical_record_observer(logical_chunk)
                        record = _record(pending)
                        if self._record_observer is not None:
                            self._record_observer(
                                pending.kind,
                                pending.record_id,
                                pending.logical_offset,
                                reconstructed - pending.logical_offset,
                            )
                        for spool in pending.retained:
                            spool.transfer(owner)
                            self._spools.discard(spool)
                        record_count += 1
                        yield record
                        pending = None
                    else:
                        fail(len(pending.fields) >= field_count, "BAD_FIELD_COUNT")
                        pending.value = self._new_spool()
                else:
                    fail(last_record, "BAD_FRAGMENT_ORDER")
            if self._fragment_observer is not None:
                self._fragment_observer(len(raw), frame_fragments)
            fail(
                reconstructed > frame.fragment_total
                or bool(frame.flags & 8) != (reconstructed < frame.fragment_total),
                "FRAGMENT_TOTAL",
            )
        fail(not terminal, "INCOMPLETE_TERMINAL")
        self.record_count = record_count
        self.logical_bytes = reconstructed


def _content_size(value: NativeContent) -> int:
    return len(value) if isinstance(value, bytes) else value.length


_REGISTRY_SCALARS = {
    "Sint64": ScalarTag.SINT64,
    "Uint64": ScalarTag.UINT64,
    "Bool": ScalarTag.BOOL,
    "UTF16": ScalarTag.UTF16,
    "HWPUNIT64": ScalarTag.HWPUNIT64,
    "BGR": ScalarTag.BGR,
    "Enum": ScalarTag.ENUM,
    "Struct": ScalarTag.STRUCT,
    "RawURC32": ScalarTag.RAW_URC32,
    "Uint8": ScalarTag.UINT8,
    "Uint16": ScalarTag.UINT16,
}


def registered_property_observation(
    key: int, raw: NativeContent
) -> NativeGraphPresent | NativeGraphUnavailable:
    rule = NATIVE_PROPERTY_REGISTRY.get(key)
    if rule is None:
        return decode_observation(ScalarTag.STRUCT, raw)
    scalar_name, shape, _origin = rule
    try:
        scalar = _REGISTRY_SCALARS[scalar_name]
    except KeyError as error:
        raise GraphProtocolError("PROPERTY_REGISTRY_SCALAR") from error
    if shape == "Scalar":
        return decode_observation(scalar, raw)
    envelope = decode_observation(ScalarTag.STRUCT, raw)
    if isinstance(envelope, NativeGraphUnavailable):
        return envelope
    value = envelope.value
    if not isinstance(value, NativeOpaque):
        raise GraphProtocolError("PROPERTY_REGISTRY_ARRAY")
    encoded = content_read(value.raw)
    if len(encoded) < 16:
        raise GraphProtocolError("PROPERTY_REGISTRY_ARRAY")
    count = read_u64(encoded, 8)
    return NativeGraphPresent.model_construct(
        value=decode_array(scalar, count, encoded)
    )


def decode_logical_record(
    raw: bytes, *, spool_root: Path | None = None
) -> TypedNativeRecord:
    """Decode one independently authenticated LogicalRecordV1 range."""
    if len(raw) < 24:
        raise GraphProtocolError("BAD_LOGICAL_RECORD")
    kind_raw = int.from_bytes(raw[0:2], "little")
    version = int.from_bytes(raw[2:4], "little")
    flags = int.from_bytes(raw[4:6], "little")
    field_count = int.from_bytes(raw[6:8], "little")
    record_id = int.from_bytes(raw[8:16], "little")
    payload_bytes = int.from_bytes(raw[16:24], "little")
    if version != 1 or payload_bytes != len(raw) - 24 or field_count == 0:
        raise GraphProtocolError("BAD_LOGICAL_RECORD")
    try:
        kind = RecordKind(kind_raw)
    except ValueError as error:
        raise GraphProtocolError("BAD_LOGICAL_RECORD") from error
    fields: list[tuple[int, int, ScalarTag, int, NativeContent]] = []
    at = 24
    previous_tag = 0
    for _ in range(field_count):
        if len(raw) - at < 24:
            raise GraphProtocolError("BAD_LOGICAL_RECORD")
        tag = int.from_bytes(raw[at : at + 2], "little")
        field_flags = int.from_bytes(raw[at + 2 : at + 4], "little")
        scalar_raw = int.from_bytes(raw[at + 4 : at + 6], "little")
        reserved = int.from_bytes(raw[at + 6 : at + 8], "little")
        count = int.from_bytes(raw[at + 8 : at + 16], "little")
        value_bytes = int.from_bytes(raw[at + 16 : at + 24], "little")
        at += 24
        if reserved != 0 or tag <= previous_tag or value_bytes > len(raw) - at:
            raise GraphProtocolError("BAD_LOGICAL_RECORD")
        try:
            scalar = ScalarTag(scalar_raw)
        except ValueError as error:
            raise GraphProtocolError("BAD_LOGICAL_RECORD") from error
        fields.append((tag, field_flags, scalar, count, raw[at : at + value_bytes]))
        at += value_bytes
        previous_tag = tag
    if at != len(raw):
        raise GraphProtocolError("BAD_LOGICAL_RECORD")
    root = spool_root or Path(tempfile.mkdtemp(prefix="hgn1-record-"))
    owns_root = spool_root is None
    value = FieldSpool(root)
    pending = _Pending(
        kind,
        record_id,
        flags,
        field_count,
        fields,
        previous_tag,
        0,
        fields[-1][2],
        fields[-1][3],
        _content_size(fields[-1][4]),
        value,
        [],
        0,
    )
    try:
        return _record(pending)
    finally:
        value.close()
        if owns_root:
            root.rmdir()


def _record(pending: _Pending) -> TypedNativeRecord:
    if pending.kind is RecordKind.PROPERTY:
        property_observation = next(
            (field for field in pending.fields if field[0] == 4), None
        )
        if (
            property_observation is not None
            and property_observation[2] is not ScalarTag.STRUCT
        ):
            raise GraphProtocolError("BAD_SCALAR")
    fields = tuple(
        NativeField.model_construct(
            tag=tag,
            flags=flags,
            scalar=scalar,
            element_count=count,
            value=(
                decode_array(scalar, count, content_read(value))
                if flags & 2
                else decode_scalar(scalar, value)
            ),
        )
        for tag, flags, scalar, count, value in pending.fields
    )
    record_id, flags = pending.record_id, pending.record_flags
    try:
        match pending.kind:
            case RecordKind.MANIFEST:
                return NativeManifestRecord.model_construct(
                    record_id=record_id, flags=flags, fields=fields
                )
            case RecordKind.NODE:
                if not pending.fields or pending.fields[0][2] is not ScalarTag.STRUCT:
                    raise GraphProtocolError("NODE_ID_MISSING")
                try:
                    nested = decode_fields(content_read(pending.fields[0][4]))
                except GraphProtocolError as error:
                    raise GraphProtocolError("NODE_ID_INVALID") from error
                if len(nested) < 2:
                    raise GraphProtocolError("NODE_ID_MISSING")
                node_id = nested[0].value
                node_kind = nested[1].value
                if not isinstance(node_id, NativeIdentifier):
                    raise GraphProtocolError("NODE_ID_MISSING")
                if not isinstance(node_kind, NativeInteger):
                    raise GraphProtocolError("NODE_KIND")
                if (
                    not any(node_id.value)
                    or node_id.value[6] & 0xF0 != 0x40
                    or node_id.value[8] & 0xC0 != 0x80
                ):
                    raise GraphProtocolError("NODE_ID_INVALID")
                return NativeNodeRecord.model_construct(
                    record_id=record_id,
                    flags=flags,
                    fields=fields,
                    node_id=node_id.value,
                    node_kind=NodeKind(node_kind.value),
                )
            case RecordKind.EDGE:
                return NativeEdgeRecord.model_construct(
                    record_id=record_id, flags=flags, fields=fields
                )
            case RecordKind.PROPERTY:
                if len(fields) != 5:
                    raise GraphProtocolError("PROPERTY_SHAPE")
                owner = fields[0].value
                owner_tag = fields[1].value
                key = fields[2].value
                observation = (
                    registered_property_observation(
                        key.value if isinstance(key, NativeInteger) else -1,
                        pending.fields[3][4],
                    )
                    if pending.fields[3][2] is ScalarTag.STRUCT
                    else fields[3].value
                )
                fields = (
                    *fields[:3],
                    NativeField.model_construct(
                        tag=fields[3].tag,
                        flags=fields[3].flags,
                        scalar=fields[3].scalar,
                        element_count=fields[3].element_count,
                        value=observation,
                    ),
                    *fields[4:],
                )
                if not isinstance(owner, NativeIdentifier):
                    raise GraphProtocolError("PROPERTY_SHAPE")
                if not isinstance(owner_tag, NativeInteger):
                    raise GraphProtocolError("PROPERTY_SHAPE")
                if not isinstance(key, NativeInteger):
                    raise GraphProtocolError("PROPERTY_SHAPE")
                if not isinstance(
                    observation, (NativeGraphPresent, NativeGraphUnavailable)
                ):
                    raise GraphProtocolError("PROPERTY_SHAPE")
                return NativePropertyRecord.model_construct(
                    record_id=record_id,
                    flags=flags,
                    fields=fields,
                    owner_node_id=owner.value,
                    owner_field_tag=owner_tag.value,
                    property_key=key.value,
                    observation=observation,
                )
            case RecordKind.COVERAGE:
                return NativeCoverageRecord.model_construct(
                    record_id=record_id, flags=flags, fields=fields
                )
            case RecordKind.DIAGNOSTIC:
                return NativeDiagnosticRecord.model_construct(
                    record_id=record_id, flags=flags, fields=fields
                )
            case RecordKind.ASSET_CHUNK:
                return NativeAssetChunkRecord.model_construct(
                    record_id=record_id, flags=flags, fields=fields
                )
            case RecordKind.TOMBSTONE:
                return NativeTombstoneRecord.model_construct(
                    record_id=record_id, flags=flags, fields=fields
                )
            case RecordKind.REMAP:
                return NativeRemapRecord.model_construct(
                    record_id=record_id, flags=flags, fields=fields
                )
    except (ValidationError, ValueError) as error:
        if isinstance(error, GraphProtocolError):
            raise
        raise GraphProtocolError("MODEL", str(error)) from error
    assert_never(pending.kind)


__all__ = ["GraphDecodeSession", "GraphStreamDecoder"]

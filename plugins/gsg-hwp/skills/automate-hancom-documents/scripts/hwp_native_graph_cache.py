from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import struct
import tempfile
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from types import TracebackType
from typing import BinaryIO, ClassVar, Final, Literal, Protocol, cast, final

from pydantic import ConfigDict, Field, ValidationError

from hwp_live_preview_lock import PreviewFileLock
from hwp_live_values import ContractModel
from hwp_native_graph_models import (
    NativeBlobSlice,
    NativeGraphPresent,
    NativeGraphQuery,
    NativeGraphUnavailable,
    NativeNodeRecord,
    NativeOpaque,
    NativePropertyRecord,
    RecordKind,
    ScalarTag,
    TypedNativeRecord,
    close_record_content,
)
from _hwp_native_graph_primitive import PrimitiveRecordFacts
from _hwp_native_graph_stream import decode_logical_record
from _hwp_native_graph_wire import decode_nested_fields
from hwp_native_graph_protocol import GraphProtocolError, GraphStreamDecoder


_CACHE_SCHEMA: Final = b"HGNC1"
_RECORD_INDEX_SCHEMA: Final = b"HGNIDX2\0"
_RECORD_INDEX_VERSION: Final = 2
_RECORD_INDEX_ENDIAN: Final = 0x4C45  # ASCII LE
_INDEX_HEADER = struct.Struct("<8sHHIQQ32s32s32s32s168s")
_RECORD_INDEX_DIRECTORY = struct.Struct("<16sQQQ32s")
_RECORD_INDEX_FOOTER = struct.Struct("<8sQQ32s32s")
_RECORD_RANGE = struct.Struct("<QHQQ32s")
_RECORD_INDEX_ENTRY = _RECORD_RANGE
_FRAME_RANGE = struct.Struct("<QQQ32s")
_KIND_POSTING = struct.Struct("<HQ")
_NODE_POSTING = struct.Struct("<16sQ")
_PROPERTY_POSTING = struct.Struct("<16sIIQ")
_OWNER_POSTING = struct.Struct("<16s16sQ")
_BLOB_POSTING = struct.Struct("<32sQ")
_INDEX_FOOTER_SCHEMA: Final = b"HGNIDXF2"
_INDEX_DOMAIN: Final = b"HGNIDX2\0DOMAIN\0V1"
_SECTION_NAMES: Final = (
    b"ranges",
    b"frames",
    b"kinds",
    b"nodes",
    b"properties",
    b"owners",
    b"blobs",
)
# Compatibility inspection size: sectioned v2 ranges begin after the complete
# header+directory, where flat-v1 readers previously expected entries.
_RECORD_INDEX_HEADER = struct.Struct(
    f"<{_INDEX_HEADER.size + len(_SECTION_NAMES) * _RECORD_INDEX_DIRECTORY.size}s"
)
_MAX_STORED_FRAME: Final = 4_194_304
_CLIENT_PROGRESS_START_NS: Final = perf_counter_ns()


def _record_client_progress(stage: str, first: int = 0, second: int = 0) -> None:
    path = os.environ.get("TODO18_CLIENT_PROGRESS_PATH")
    if not path:
        return
    now = perf_counter_ns()
    line = (
        f"{(now - _CLIENT_PROGRESS_START_NS) // 1_000_000}\t{now}\t"
        f"{stage}\t{first}\t{second}\r\n"
    ).encode("ascii")
    descriptor = os.open(
        path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        # Telemetry is append-only diagnostic evidence, not publication state;
        # one bounded kernel write avoids putting an fsync in the record loop.
        _ = os.write(descriptor, line)
    finally:
        os.close(descriptor)


class _StrictMetadata(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class _Metadata(_StrictMetadata):
    schema_name: Literal["HGNC1"] = Field(default="HGNC1", alias="schema")
    generation: str
    key: str
    query_json: str
    frame_count: int
    byte_count: int
    frames_sha256: str
    version_digest: str
    record_count: int
    logical_bytes: int
    stream_digest: str
    records_index_bytes: int
    records_index_sha256: str
    logical_records_bytes: int = 0
    logical_records_sha256: str = ""
    index_domain_sha256: str = ""
    blob_count: int = 0
    blobs_sha256: str = hashlib.sha256(b"").hexdigest()
    blob_pack_sha256: str = ""
    blob_index_sha256: str = ""

    def encoded(self) -> bytes:
        return self.model_dump_json(by_alias=True).encode("utf-8")


class _Pointer(_StrictMetadata):
    schema_name: Literal["HGNC1-POINTER"] = Field(
        default="HGNC1-POINTER", alias="schema"
    )
    generation: str
    metadata_sha256: str

    def encoded(self) -> bytes:
        return self.model_dump_json(by_alias=True).encode("utf-8")


class NativeGraphTransferProtocol(Protocol):
    def frames(self) -> Iterable[bytes]: ...
    def read_blob(self, blob: NativeBlobSlice) -> bytes: ...
    def read_blobs(
        self, blobs: tuple[NativeBlobSlice, ...]
    ) -> dict[NativeBlobSlice, bytes]: ...
    def close(self) -> None: ...


class NativeGraphInstrumentationProtocol(Protocol):
    def add_disk_root(self, root: Path) -> None: ...
    def note_cache(self, hit: bool) -> None: ...
    def observe_record(self, record: TypedNativeRecord) -> None: ...
    def note_spool_bytes(self, value: int) -> None: ...


def _blob_key(blob: NativeBlobSlice) -> str:
    binding = (
        blob.content_id
        + blob.offset.to_bytes(8, "little")
        + blob.length.to_bytes(8, "little")
        + blob.digest
    )
    return hashlib.sha256(binding).hexdigest()


def _record_blobs(record: TypedNativeRecord) -> set[NativeBlobSlice]:
    values: set[NativeBlobSlice] = set()

    def visit(value: object) -> None:
        if isinstance(value, NativeGraphPresent):
            visit(value.value)
        elif isinstance(value, NativeGraphUnavailable):
            visit(value.detail)
        elif isinstance(value, NativeBlobSlice):
            values.add(value)
        elif isinstance(value, NativeOpaque) and value.scalar is ScalarTag.STRUCT:
            try:
                nested = decode_nested_fields(value)
            except GraphProtocolError:
                return
            for field in nested:
                visit(field.value)

    for field in record.fields:
        visit(field.value)
    return values


def _raw_scalar(value: object, length: int) -> bytes | None:
    raw = getattr(value, "raw", None)
    if isinstance(raw, bytes) and len(raw) == length:
        return raw
    return None


def _node_parent(record: TypedNativeRecord) -> bytes | None:
    if not isinstance(record, NativeNodeRecord) or not record.fields:
        return None
    common = record.fields[0].value
    if not isinstance(common, NativeOpaque) or common.scalar is not ScalarTag.STRUCT:
        raise GraphProtocolError("CACHE_TYPED_INDEX_NODE")
    nested = decode_nested_fields(common)
    parent = next((field.value for field in nested if field.tag == 3), None)
    return _raw_scalar(parent, 16)


@dataclass(frozen=True, slots=True)
class _RecordRange:
    record_id: int
    kind: RecordKind
    offset: int
    length: int
    digest: bytes


@final
class _GenerationQueryIndex:
    __slots__ = ("blobs", "by_kind", "nodes", "owners", "properties", "ranges")

    def __init__(self) -> None:
        self.ranges: tuple[_RecordRange, ...] = ()
        self.by_kind: dict[RecordKind, tuple[int, ...]] = {}
        self.nodes: dict[bytes, int] = {}
        self.properties: dict[tuple[bytes, int, int], int] = {}
        self.owners: dict[bytes, tuple[int, ...]] = {}
        self.blobs: dict[bytes, tuple[int, ...]] = {}

    def selected_ids(
        self, kinds: tuple[RecordKind, ...], offset: int, limit: int
    ) -> tuple[tuple[int, ...], int]:
        if not kinds:
            total = len(self.ranges)
            return tuple(range(offset, min(total, offset + limit))), total
        ordered = sorted(
            record_id for kind in set(kinds) for record_id in self.by_kind.get(kind, ())
        )
        return tuple(ordered[offset : offset + limit]), len(ordered)


def _primitive_query_index(
    ranges: list[_RecordRange],
    postings: list[tuple[int, int]],
    nodes: list[tuple[bytes, int]],
    properties: list[tuple[bytes, int, int]],
    owners: list[tuple[bytes, bytes, int]],
    blobs: list[tuple[bytes, int]],
) -> _GenerationQueryIndex:
    result = _GenerationQueryIndex()
    result.ranges = tuple(ranges)
    by_kind: dict[RecordKind, list[int]] = {}
    for kind_raw, record_id in postings:
        by_kind.setdefault(RecordKind(kind_raw), []).append(record_id)
    result.by_kind = {kind: tuple(ids) for kind, ids in by_kind.items()}
    result.nodes = dict(nodes)
    occurrences: dict[tuple[bytes, int], int] = {}
    for owner, key, record_id in sorted(properties):
        identity = (owner, key)
        occurrence = occurrences.get(identity, 0) + 1
        occurrences[identity] = occurrence
        result.properties[(owner, key, occurrence)] = record_id
    owner_map: dict[bytes, list[int]] = {}
    for owner, _child, record_id in sorted(owners):
        owner_map.setdefault(owner, []).append(record_id)
    result.owners = {owner: tuple(ids) for owner, ids in owner_map.items()}
    blob_map: dict[bytes, list[int]] = {}
    for key, record_id in sorted(blobs):
        blob_map.setdefault(key, []).append(record_id)
    result.blobs = {key: tuple(ids) for key, ids in blob_map.items()}
    return result


def _section_digest(name: bytes, value: bytes) -> bytes:
    return hashlib.sha256(b"HGNIDX2\0SECTION\0" + name + value).digest()


def _read_generation_index(
    generation: Path, metadata: _Metadata
) -> _GenerationQueryIndex:
    path = generation / "records.hgi"
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise GraphProtocolError("CACHE_TYPED_INDEX_MISSING", str(error)) from error
    if len(raw) != metadata.records_index_bytes:
        raise GraphProtocolError(
            "CACHE_TYPED_INDEX_TRUNCATED"
            if len(raw) < metadata.records_index_bytes
            else "CACHE_TYPED_INDEX_TRAILING"
        )
    if len(raw) < _INDEX_HEADER.size + _RECORD_INDEX_FOOTER.size:
        raise GraphProtocolError("CACHE_TYPED_INDEX_TRUNCATED")
    header = cast(
        tuple[bytes, int, int, int, int, int, bytes, bytes, bytes, bytes, bytes],
        _INDEX_HEADER.unpack_from(raw),
    )
    (
        schema,
        version,
        endian,
        section_count,
        record_count,
        logical_bytes,
        stream_digest,
        version_digest,
        frames_digest,
        query_key_digest,
        graph_version,
    ) = header
    if (
        schema != _RECORD_INDEX_SCHEMA
        or version != _RECORD_INDEX_VERSION
        or endian != _RECORD_INDEX_ENDIAN
        or section_count != len(_SECTION_NAMES)
        or record_count != metadata.record_count
        or logical_bytes != metadata.logical_bytes
        or stream_digest.hex() != metadata.stream_digest
        or version_digest.hex() != metadata.version_digest
        or frames_digest.hex() != metadata.frames_sha256
        or query_key_digest.hex() != metadata.key
        or hashlib.sha256(graph_version).hexdigest() != metadata.version_digest
    ):
        raise GraphProtocolError("CACHE_TYPED_INDEX_BINDING")
    directory_start = _INDEX_HEADER.size
    directory_end = directory_start + section_count * _RECORD_INDEX_DIRECTORY.size
    footer_start = len(raw) - _RECORD_INDEX_FOOTER.size
    if directory_end > footer_start:
        raise GraphProtocolError("CACHE_TYPED_INDEX_TRUNCATED")
    sections: dict[bytes, bytes] = {}
    expected_offset = directory_end
    for ordinal, expected_name in enumerate(_SECTION_NAMES):
        encoded = cast(
            tuple[bytes, int, int, int, bytes],
            _RECORD_INDEX_DIRECTORY.unpack_from(
                raw, directory_start + ordinal * _RECORD_INDEX_DIRECTORY.size
            ),
        )
        name = encoded[0].rstrip(b"\0")
        offset, length, count, digest = encoded[1:]
        if (
            name != expected_name
            or offset != expected_offset
            or length > footer_start - offset
        ):
            raise GraphProtocolError("CACHE_TYPED_INDEX_SECTION")
        value = raw[offset : offset + length]
        if _section_digest(name, value) != digest:
            raise GraphProtocolError(
                "CACHE_TYPED_INDEX_RECORD"
                if name == b"ranges"
                else "CACHE_TYPED_INDEX_SECTION_DIGEST"
            )
        unit = {
            b"frames": _FRAME_RANGE.size,
            b"ranges": _RECORD_RANGE.size,
            b"kinds": _KIND_POSTING.size,
            b"nodes": _NODE_POSTING.size,
            b"properties": _PROPERTY_POSTING.size,
            b"owners": _OWNER_POSTING.size,
            b"blobs": _BLOB_POSTING.size,
        }[name]
        if length % unit or count != length // unit:
            raise GraphProtocolError("CACHE_TYPED_INDEX_SECTION_COUNT")
        sections[name] = value
        expected_offset += length
    if expected_offset != footer_start:
        raise GraphProtocolError("CACHE_TYPED_INDEX_TRAILING")
    footer = cast(
        tuple[bytes, int, int, bytes, bytes],
        _RECORD_INDEX_FOOTER.unpack_from(raw, footer_start),
    )
    directory = raw[directory_start:directory_end]
    domain_input = raw[:footer_start]
    if (
        footer[0] != _INDEX_FOOTER_SCHEMA
        or footer[1] != len(raw)
        or footer[2] != section_count
        or footer[3] != hashlib.sha256(directory).digest()
        or footer[4] != hashlib.sha256(_INDEX_DOMAIN + domain_input).digest()
        or (
            metadata.index_domain_sha256
            and footer[4].hex() != metadata.index_domain_sha256
        )
    ):
        raise GraphProtocolError("CACHE_TYPED_INDEX_FOOTER")

    result = _GenerationQueryIndex()
    ranges: list[_RecordRange] = []
    terminal = 0
    for at in range(0, len(sections[b"ranges"]), _RECORD_RANGE.size):
        record_id, kind_raw, offset, length, digest = cast(
            tuple[int, int, int, int, bytes],
            _RECORD_RANGE.unpack_from(sections[b"ranges"], at),
        )
        try:
            kind = RecordKind(kind_raw)
        except ValueError as error:
            raise GraphProtocolError("CACHE_TYPED_INDEX_RECORD") from error
        if record_id != len(ranges) or offset != terminal or length < 48:
            raise GraphProtocolError("CACHE_TYPED_INDEX_RECORD")
        ranges.append(_RecordRange(record_id, kind, offset, length, digest))
        terminal += length
    if len(ranges) != record_count or terminal != logical_bytes:
        raise GraphProtocolError("CACHE_TYPED_INDEX_RECORD")
    result.ranges = tuple(ranges)

    prior: tuple[int, int] | None = None
    kinds: dict[RecordKind, list[int]] = {}
    for at in range(0, len(sections[b"kinds"]), _KIND_POSTING.size):
        kind_raw, record_id = cast(
            tuple[int, int], _KIND_POSTING.unpack_from(sections[b"kinds"], at)
        )
        key = (kind_raw, record_id)
        if prior is not None and key <= prior:
            raise GraphProtocolError("CACHE_TYPED_INDEX_ORDER")
        prior = key
        if record_id >= record_count or int(ranges[record_id].kind) != kind_raw:
            raise GraphProtocolError("CACHE_TYPED_INDEX_REFERENCE")
        kinds.setdefault(RecordKind(kind_raw), []).append(record_id)
    if sum(map(len, kinds.values())) != record_count:
        raise GraphProtocolError("CACHE_TYPED_INDEX_COUNT")
    result.by_kind = {kind: tuple(ids) for kind, ids in kinds.items()}

    prior_node: bytes | None = None
    for at in range(0, len(sections[b"nodes"]), _NODE_POSTING.size):
        node, record_id = cast(
            tuple[bytes, int], _NODE_POSTING.unpack_from(sections[b"nodes"], at)
        )
        if prior_node is not None and node <= prior_node:
            raise GraphProtocolError("CACHE_TYPED_INDEX_ORDER")
        if record_id >= record_count or ranges[record_id].kind is not RecordKind.NODE:
            raise GraphProtocolError("CACHE_TYPED_INDEX_REFERENCE")
        result.nodes[node] = record_id
        prior_node = node

    prior_property: tuple[bytes, int, int] | None = None
    for at in range(0, len(sections[b"properties"]), _PROPERTY_POSTING.size):
        owner, key, occurrence, record_id = cast(
            tuple[bytes, int, int, int],
            _PROPERTY_POSTING.unpack_from(sections[b"properties"], at),
        )
        identity = (owner, key, occurrence)
        if prior_property is not None and identity <= prior_property:
            raise GraphProtocolError("CACHE_TYPED_INDEX_ORDER")
        if (
            owner not in result.nodes
            or occurrence == 0
            or record_id >= record_count
            or ranges[record_id].kind is not RecordKind.PROPERTY
        ):
            raise GraphProtocolError("CACHE_TYPED_INDEX_REFERENCE")
        result.properties[identity] = record_id
        prior_property = identity

    owners: dict[bytes, list[int]] = {}
    prior_owner: tuple[bytes, bytes] | None = None
    for at in range(0, len(sections[b"owners"]), _OWNER_POSTING.size):
        owner, child, record_id = cast(
            tuple[bytes, bytes, int],
            _OWNER_POSTING.unpack_from(sections[b"owners"], at),
        )
        identity = (owner, child)
        if prior_owner is not None and identity <= prior_owner:
            raise GraphProtocolError("CACHE_TYPED_INDEX_ORDER")
        if owner not in result.nodes or result.nodes.get(child) != record_id:
            raise GraphProtocolError("CACHE_TYPED_INDEX_REFERENCE")
        owners.setdefault(owner, []).append(record_id)
        prior_owner = identity
    result.owners = {owner: tuple(ids) for owner, ids in owners.items()}

    blobs: dict[bytes, list[int]] = {}
    prior_blob: tuple[bytes, int] | None = None
    for at in range(0, len(sections[b"blobs"]), _BLOB_POSTING.size):
        key, record_id = cast(
            tuple[bytes, int], _BLOB_POSTING.unpack_from(sections[b"blobs"], at)
        )
        identity = (key, record_id)
        if prior_blob is not None and identity <= prior_blob:
            raise GraphProtocolError("CACHE_TYPED_INDEX_ORDER")
        if record_id >= record_count:
            raise GraphProtocolError("CACHE_TYPED_INDEX_REFERENCE")
        blobs.setdefault(key, []).append(record_id)
        prior_blob = identity
    result.blobs = {key: tuple(ids) for key, ids in blobs.items()}
    if hashlib.sha256(raw).hexdigest() != metadata.records_index_sha256:
        raise GraphProtocolError("CACHE_TYPED_INDEX_AUTHENTICITY")
    return result


def _write_sectioned_index(
    destination: Path,
    staging: Path,
    database: sqlite3.Connection,
    ranges_path: Path,
    frames: list[tuple[int, int, int, bytes]],
    *,
    record_count: int,
    logical_bytes: int,
    stream_digest: bytes,
    version_digest: bytes,
    frames_digest: bytes,
    query_key: str,
    graph_version: bytes,
) -> tuple[str, int, str]:
    _ = staging
    section_values: dict[bytes, bytes] = {b"ranges": ranges_path.read_bytes()}

    def section(name: bytes, rows: Iterable[bytes]) -> None:
        section_values[name] = b"".join(rows)

    section(
        b"frames",
        (
            _FRAME_RANGE.pack(offset, length, sequence, digest)
            for offset, length, sequence, digest in frames
        ),
    )
    section(
        b"kinds",
        (
            _KIND_POSTING.pack(*row)
            for row in cast(
                Iterable[tuple[int, int]],
                database.execute(
                    "SELECT kind, record_id FROM postings ORDER BY kind, record_id"
                ),
            )
        ),
    )
    section(
        b"nodes",
        (
            _NODE_POSTING.pack(*row)
            for row in cast(
                Iterable[tuple[bytes, int]],
                database.execute("SELECT node, record_id FROM nodes ORDER BY node"),
            )
        ),
    )

    def property_rows() -> Iterator[bytes]:
        prior: tuple[bytes, int] | None = None
        occurrence = 0
        query = (
            "SELECT owner, property_key, record_id FROM properties "
            + "ORDER BY owner, property_key, record_id"
        )
        for owner, key, record_id in cast(
            Iterable[tuple[bytes, int, int]], database.execute(query)
        ):
            identity = (owner, key)
            occurrence = occurrence + 1 if identity == prior else 1
            prior = identity
            yield _PROPERTY_POSTING.pack(owner, key, occurrence, record_id)

    section(b"properties", property_rows())
    section(
        b"owners",
        (
            _OWNER_POSTING.pack(*row)
            for row in cast(
                Iterable[tuple[bytes, bytes, int]],
                database.execute(
                    "SELECT owner, child, record_id FROM owners ORDER BY owner, child"
                ),
            )
        ),
    )
    section(
        b"blobs",
        (
            _BLOB_POSTING.pack(*row)
            for row in cast(
                Iterable[tuple[bytes, int]],
                database.execute(
                    "SELECT blob, record_id FROM blobs ORDER BY blob, record_id"
                ),
            )
        ),
    )

    header = _INDEX_HEADER.pack(
        _RECORD_INDEX_SCHEMA,
        _RECORD_INDEX_VERSION,
        _RECORD_INDEX_ENDIAN,
        len(_SECTION_NAMES),
        record_count,
        logical_bytes,
        stream_digest,
        version_digest,
        frames_digest,
        bytes.fromhex(query_key),
        graph_version,
    )
    directory_bytes = bytearray()
    offset = _INDEX_HEADER.size + len(_SECTION_NAMES) * _RECORD_INDEX_DIRECTORY.size
    section_facts: list[tuple[bytes, int]] = []
    units = {
        b"frames": _FRAME_RANGE.size,
        b"ranges": _RECORD_RANGE.size,
        b"kinds": _KIND_POSTING.size,
        b"nodes": _NODE_POSTING.size,
        b"properties": _PROPERTY_POSTING.size,
        b"owners": _OWNER_POSTING.size,
        b"blobs": _BLOB_POSTING.size,
    }
    for name in _SECTION_NAMES:
        value = section_values[name]
        length = len(value)
        if length % units[name]:
            raise GraphProtocolError("CACHE_TYPED_INDEX_SECTION_COUNT")
        directory_bytes.extend(
            _RECORD_INDEX_DIRECTORY.pack(
                name.ljust(16, b"\0"),
                offset,
                length,
                length // units[name],
                _section_digest(name, value),
            )
        )
        section_facts.append((value, length))
        offset += length
    footer_bytes = _RECORD_INDEX_FOOTER.size
    total_bytes = offset + footer_bytes
    domain = hashlib.sha256(_INDEX_DOMAIN)
    domain.update(header)
    domain.update(directory_bytes)
    for value, _length in section_facts:
        domain.update(value)
    domain_digest = domain.digest()
    footer = _RECORD_INDEX_FOOTER.pack(
        _INDEX_FOOTER_SCHEMA,
        total_bytes,
        len(_SECTION_NAMES),
        hashlib.sha256(directory_bytes).digest(),
        domain_digest,
    )
    file_digest = hashlib.sha256()
    with destination.open("xb") as output:
        for value in (header, directory_bytes):
            _ = output.write(value)
            file_digest.update(value)
        for value, _length in section_facts:
            _ = output.write(value)
            file_digest.update(value)
        _ = output.write(footer)
        file_digest.update(footer)
        output.flush()
        os.fsync(output.fileno())
    return domain_digest.hex(), total_bytes, file_digest.hexdigest()


@final
class NativeGraphSnapshot:
    __slots__ = (
        "_closed",
        "_decoders",
        "_frames_path",
        "_index",
        "_records_file",
        "_records_path",
        "_spool_root",
        "metadata",
    )

    def __init__(
        self,
        frames_path: Path,
        metadata: _Metadata,
        *,
        spool_root: Path | None = None,
        query_index: _GenerationQueryIndex | None = None,
    ) -> None:
        self._closed = False
        self._decoders: set[GraphStreamDecoder] = set()
        self._frames_path = frames_path
        self._records_path = frames_path.parent / "records.bin"
        self._records_file: BinaryIO | None = (
            self._records_path.open("rb") if self._records_path.is_file() else None
        )
        self._spool_root = spool_root
        self._index = query_index
        self.metadata = metadata

    def _frames(self) -> Iterator[bytes]:
        digest = hashlib.sha256()
        count = 0
        byte_count = 0
        with self._frames_path.open("rb") as source:
            if source.read(5) != _CACHE_SCHEMA:
                raise GraphProtocolError("CACHE_HEADER")
            while length_raw := source.read(4):
                if len(length_raw) != 4:
                    raise GraphProtocolError("CACHE_LENGTH")
                length = int.from_bytes(length_raw, "little")
                if length < 320 or length > _MAX_STORED_FRAME:
                    raise GraphProtocolError("CACHE_LENGTH")
                frame = source.read(length)
                if len(frame) != length:
                    raise GraphProtocolError("CACHE_TRUNCATED")
                digest.update(length_raw)
                digest.update(frame)
                count += 1
                byte_count += length
                yield frame
            if (
                count != self.metadata.frame_count
                or byte_count != self.metadata.byte_count
                or digest.hexdigest() != self.metadata.frames_sha256
            ):
                raise GraphProtocolError("CACHE_AUTHENTICITY")

    def frame_bytes(self) -> Iterator[bytes]:
        """Yield authenticated immutable source frames without decoding them."""
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        yield from self._frames()

    def read_blob(self, blob: NativeBlobSlice) -> bytes:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        generation = self._frames_path.parent
        pack_path = generation / "blobs.pack"
        index_path = generation / "blobs.index"
        if pack_path.is_file() and index_path.is_file():
            try:
                index = index_path.read_bytes()
                if (
                    hashlib.sha256(index).hexdigest() != self.metadata.blob_index_sha256
                    or len(index) % 80 != 0
                ):
                    raise GraphProtocolError("CACHE_BLOB_INDEX_AUTHENTICITY")
                key = bytes.fromhex(_blob_key(blob))
                found: tuple[int, int] | None = None
                for at in range(0, len(index), 80):
                    if index[at : at + 32] == key:
                        found = (
                            int.from_bytes(index[at + 32 : at + 40], "little"),
                            int.from_bytes(index[at + 40 : at + 48], "little"),
                        )
                        break
                if found is None:
                    raise GraphProtocolError("CACHE_BLOB_MISSING")
                with pack_path.open("rb") as source:
                    _ = source.seek(found[0])
                    value = source.read(found[1])
            except GraphProtocolError:
                raise
            except OSError as error:
                raise GraphProtocolError("CACHE_BLOB_MISSING") from error
        else:
            path = generation / "blobs" / _blob_key(blob)
            try:
                value = path.read_bytes()
            except OSError as error:
                raise GraphProtocolError("CACHE_BLOB_MISSING") from error
        if len(value) != blob.length or hashlib.sha256(value).digest() != blob.digest:
            raise GraphProtocolError("CACHE_BLOB_AUTHENTICITY")
        return value

    def _read_indexed_record(self, record_id: int) -> TypedNativeRecord:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        if self._index is None or record_id < 0 or record_id >= len(self._index.ranges):
            raise GraphProtocolError("CACHE_TYPED_INDEX_REFERENCE")
        record_range = self._index.ranges[record_id]
        try:
            if self._records_file is None:
                raise OSError("logical record lease is absent")
            with os.fdopen(os.dup(self._records_file.fileno()), "rb") as source:
                _ = source.seek(record_range.offset)
                raw = source.read(record_range.length)
        except OSError as error:
            raise GraphProtocolError("CACHE_LOGICAL_RECORDS_MISSING") from error
        if (
            len(raw) != record_range.length
            or hashlib.sha256(raw).digest() != record_range.digest
        ):
            raise GraphProtocolError("CACHE_LOGICAL_RECORD_AUTHENTICITY")
        record = decode_logical_record(raw, spool_root=self._spool_root)
        if record.record_id != record_id or record.kind is not record_range.kind:
            close_record_content(record)
            raise GraphProtocolError("CACHE_TYPED_INDEX_REFERENCE")
        return record

    def indexed_records(
        self, kinds: tuple[RecordKind, ...], offset: int, limit: int
    ) -> tuple[Iterator[TypedNativeRecord], int]:
        if type(self).records is not _CANONICAL_RECORDS_METHOD:
            # Preserve explicit test/adapter overrides without weakening the
            # production indexed path.
            overridden = tuple(
                record for record in self.records() if not kinds or record.kind in kinds
            )
            return iter(overridden[offset : offset + limit]), len(overridden)
        if self._index is None or offset < 0 or limit < 0:
            raise GraphProtocolError("CACHE_TYPED_INDEX_MISSING")
        record_ids, total = self._index.selected_ids(kinds, offset, limit)
        return (self._read_indexed_record(record_id) for record_id in record_ids), total

    def node_record(self, node_id: bytes) -> TypedNativeRecord | None:
        record_id = None if self._index is None else self._index.nodes.get(node_id)
        return None if record_id is None else self._read_indexed_record(record_id)

    def property_record(
        self, owner: bytes, property_key: int, occurrence: int
    ) -> TypedNativeRecord | None:
        record_id = (
            None
            if self._index is None
            else self._index.properties.get((owner, property_key, occurrence))
        )
        return None if record_id is None else self._read_indexed_record(record_id)

    def record(self, record_id: int) -> TypedNativeRecord | None:
        if self._index is None or record_id < 0 or record_id >= len(self._index.ranges):
            return None
        return self._read_indexed_record(record_id)

    def records(self) -> Iterator[TypedNativeRecord]:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        decoder = GraphStreamDecoder(self._frames(), spool_root=self._spool_root)
        self._decoders.add(decoder)
        return self._record_iterator(decoder)

    def _record_iterator(
        self, decoder: GraphStreamDecoder
    ) -> Iterator[TypedNativeRecord]:
        try:
            with decoder.records() as records:
                yield from records
            if (
                decoder.version is None
                or decoder.version.digest != self.metadata.version_digest
            ):
                raise GraphProtocolError("CACHE_VERSION")
        finally:
            decoder.close()
            self._decoders.discard(decoder)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for decoder in tuple(self._decoders):
            decoder.close()
        self._decoders.clear()
        if self._records_file is not None:
            self._records_file.close()
            self._records_file = None

    def __enter__(self) -> NativeGraphSnapshot:
        if self._closed:
            raise GraphProtocolError("SPOOL_CLOSED")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc_value, traceback)
        self.close()


_CANONICAL_RECORDS_METHOD = NativeGraphSnapshot.records


@final
class NativeGraphCache:
    __slots__ = (
        "_failure_hook",
        "_generations",
        "_instrumentation",
        "_locks",
        "_pointers",
        "_root",
        "_spool_root",
    )

    def __init__(
        self,
        root: Path,
        *,
        failure_hook: Callable[[str], None] | None = None,
        spool_root: Path | None = None,
        instrumentation: NativeGraphInstrumentationProtocol | None = None,
    ) -> None:
        self._root = root
        self._failure_hook = failure_hook
        self._spool_root = spool_root
        self._instrumentation = instrumentation
        if instrumentation is not None:
            instrumentation.add_disk_root(root)
        self._generations = root / "generations"
        self._pointers = root / "pointers"
        self._locks = root / "locks"
        self._generations.mkdir(parents=True, exist_ok=True)
        self._pointers.mkdir(parents=True, exist_ok=True)
        self._locks.mkdir(parents=True, exist_ok=True)

    def _boundary(self, name: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(name)

    @staticmethod
    def _query_json(query: NativeGraphQuery) -> str:
        return query.model_dump_json(exclude_none=False)

    @classmethod
    def _key(cls, query: NativeGraphQuery) -> str:
        return hashlib.sha256(cls._query_json(query).encode("utf-8")).hexdigest()

    def _pointer_path(self, key: str) -> Path:
        return self._pointers / f"{key}.json"

    def lock_path(self, query: NativeGraphQuery) -> Path:
        return self._locks / f"{self._key(query)}.lock"

    @staticmethod
    def _authenticate_file(path: Path, expected_digest: str) -> None:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                while chunk := source.read(1 << 20):
                    digest.update(chunk)
        except OSError as error:
            raise GraphProtocolError("CACHE_FILE_MISSING", str(error)) from error
        if digest.hexdigest() != expected_digest:
            raise GraphProtocolError("CACHE_FILE_AUTHENTICITY")

    @staticmethod
    def _authenticate_record_index(
        generation: Path, metadata: _Metadata
    ) -> _GenerationQueryIndex:
        if (
            metadata.logical_records_bytes != metadata.logical_bytes
            or not metadata.logical_records_sha256
        ):
            raise GraphProtocolError("CACHE_LOGICAL_RECORDS_BINDING")
        NativeGraphCache._authenticate_file(
            generation / "records.bin", metadata.logical_records_sha256
        )
        return _read_generation_index(generation, metadata)

    def load(self, query: NativeGraphQuery) -> NativeGraphSnapshot | None:
        key = self._key(query)
        pointer_path = self._pointer_path(key)
        if not pointer_path.is_file():
            if self._instrumentation is not None:
                self._instrumentation.note_cache(False)
            return None
        if self._instrumentation is not None:
            self._instrumentation.note_cache(True)
        try:
            pointer = _Pointer.model_validate_json(
                pointer_path.read_bytes(), strict=True
            )
            generation = self._generations / pointer.generation
            metadata_bytes = (generation / "metadata.json").read_bytes()
            if hashlib.sha256(metadata_bytes).hexdigest() != pointer.metadata_sha256:
                raise GraphProtocolError("CACHE_METADATA_DIGEST")
            metadata = _Metadata.model_validate_json(metadata_bytes, strict=True)
        except GraphProtocolError:
            raise
        except (OSError, ValidationError) as error:
            raise GraphProtocolError("CACHE_METADATA", str(error)) from error
        if (
            metadata.generation != pointer.generation
            or metadata.key != key
            or metadata.query_json != self._query_json(query)
        ):
            raise GraphProtocolError("CACHE_BINDING")
        # One fused generation authentication owns the immutable handles and
        # reusable typed postings. No semantic model or full record walk occurs
        # on subsequent public queries against this lease.
        frames_path = generation / "frames.bin"
        authentication_probe = NativeGraphSnapshot(
            frames_path, metadata, spool_root=self._spool_root
        )
        try:
            for _frame in authentication_probe.frame_bytes():
                pass
        finally:
            authentication_probe.close()
        query_index = self._authenticate_record_index(generation, metadata)
        snapshot = NativeGraphSnapshot(
            frames_path,
            metadata,
            spool_root=self._spool_root,
            query_index=query_index,
        )
        if metadata.blob_count:
            self._authenticate_file(
                generation / "blobs.pack", metadata.blob_pack_sha256
            )
            self._authenticate_file(
                generation / "blobs.index", metadata.blob_index_sha256
            )
        return snapshot

    def publish(
        self, query: NativeGraphQuery, frames: Iterable[bytes]
    ) -> NativeGraphSnapshot:
        key = self._key(query)
        with PreviewFileLock.acquire(self.lock_path(query)):
            return self._publish_locked(query, frames, key)

    def load_or_publish(
        self,
        query: NativeGraphQuery,
        producer: Callable[[], Iterable[bytes]],
    ) -> NativeGraphSnapshot:
        key = self._key(query)
        with PreviewFileLock.acquire(self.lock_path(query)):
            try:
                cached = self.load(query)
            except GraphProtocolError as error:
                if not error.code.startswith(
                    "CACHE_TYPED_INDEX"
                ) and not error.code.startswith("CACHE_LOGICAL_RECORD"):
                    raise
                return self._rebuild_cold_locked(query, key)
            if cached is not None:
                return cached
            return self._publish_locked(query, producer(), key)

    def load_or_publish_transfer(
        self,
        query: NativeGraphQuery,
        producer: Callable[[], NativeGraphTransferProtocol],
    ) -> NativeGraphSnapshot:
        key = self._key(query)
        with PreviewFileLock.acquire(self.lock_path(query)):
            try:
                cached = self.load(query)
            except GraphProtocolError as error:
                if not error.code.startswith(
                    "CACHE_TYPED_INDEX"
                ) and not error.code.startswith("CACHE_LOGICAL_RECORD"):
                    raise
                return self._rebuild_cold_locked(query, key)
            if cached is not None:
                return cached
            return self._publish_transfer_locked(query, producer, key)

    def _rebuild_cold_locked(
        self, query: NativeGraphQuery, key: str
    ) -> NativeGraphSnapshot:
        pointer = _Pointer.model_validate_json(
            self._pointer_path(key).read_bytes(), strict=True
        )
        generation = self._generations / pointer.generation
        metadata_bytes = (generation / "metadata.json").read_bytes()
        if hashlib.sha256(metadata_bytes).hexdigest() != pointer.metadata_sha256:
            raise GraphProtocolError("CACHE_METADATA_DIGEST")
        metadata = _Metadata.model_validate_json(metadata_bytes, strict=True)
        source = NativeGraphSnapshot(
            generation / "frames.bin", metadata, spool_root=self._spool_root
        )
        try:
            # Full strict decoder validation and sidecar construction occur in
            # the ordinary candidate path; the corrupt generation is never
            # modified or warm-accepted.
            return self._publish_locked(
                query,
                source.frame_bytes(),
                key,
                blob_reader=source.read_blob,
                generation_suffix=f"-rebuild-{perf_counter_ns():x}",
            )
        finally:
            source.close()

    def publish_open_transfer(
        self,
        query: NativeGraphQuery,
        transfer: NativeGraphTransferProtocol,
    ) -> NativeGraphSnapshot:
        """Publish from a caller-owned transfer without closing its native pin."""
        key = self._key(query)
        with PreviewFileLock.acquire(self.lock_path(query)):
            return self._publish_locked(
                query,
                transfer.frames(),
                key,
                blob_reader=transfer.read_blob,
                blob_batch_reader=getattr(transfer, "read_blobs", None),
                authenticated_consumer=getattr(
                    transfer, "consume_authenticated_stream", None
                ),
                primitive_consumer=getattr(transfer, "consume_primitive_stream", None),
            )

    def publish_transfer(
        self,
        query: NativeGraphQuery,
        producer: Callable[[], NativeGraphTransferProtocol],
    ) -> NativeGraphSnapshot:
        """Publish a fresh live transfer, never serving an older generation."""
        key = self._key(query)
        with PreviewFileLock.acquire(self.lock_path(query)):
            return self._publish_transfer_locked(query, producer, key)

    def _publish_transfer_locked(
        self,
        query: NativeGraphQuery,
        producer: Callable[[], NativeGraphTransferProtocol],
        key: str,
    ) -> NativeGraphSnapshot:
        _record_client_progress("TransferProducerStart")
        transfer = producer()
        _record_client_progress("TransferProducerEnd")
        try:
            return self._publish_locked(
                query,
                transfer.frames(),
                key,
                blob_reader=transfer.read_blob,
                blob_batch_reader=getattr(transfer, "read_blobs", None),
                authenticated_consumer=getattr(
                    transfer, "consume_authenticated_stream", None
                ),
                primitive_consumer=getattr(transfer, "consume_primitive_stream", None),
            )
        finally:
            transfer.close()

    def _publish_locked(
        self,
        query: NativeGraphQuery,
        frames: Iterable[bytes],
        key: str,
        *,
        blob_reader: Callable[[NativeBlobSlice], bytes] | None = None,
        blob_batch_reader: Callable[
            [tuple[NativeBlobSlice, ...]], dict[NativeBlobSlice, bytes]
        ]
        | None = None,
        generation_suffix: str = "",
        authenticated_consumer: Callable[
            [
                Callable[[bytes], None],
                Callable[[TypedNativeRecord], None],
                Callable[[RecordKind, int, int, int], None],
                Callable[[bytes], None] | None,
            ],
            tuple[object, int, int, int, int, bytes],
        ]
        | None = None,
        primitive_consumer: Callable[
            [
                Callable[[bytes], None],
                Callable[[PrimitiveRecordFacts], None],
                Callable[[bytes], None],
            ],
            tuple[object, int, int, int, int, bytes],
        ]
        | None = None,
    ) -> NativeGraphSnapshot:
        _record_client_progress("CachePublishStart")
        for stale in self._generations.glob(f".{key}.*"):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
        for stale in self._pointers.glob(f".{key}.*.pointer.tmp"):
            stale.unlink(missing_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{key}.", dir=self._generations))
        pointer_temporary = ""
        generation_path: Path | None = None
        generation_created = False
        committed = False
        digest = hashlib.sha256()
        frame_count = 0
        byte_count = 0
        stored_frame_bytes = len(_CACHE_SCHEMA)
        frames_path = staging / "frames.bin"
        logical_records_path = staging / "records.bin"
        record_index_path = staging / "records.hgi"
        ranges_path = staging / ".ranges.section"
        postings_path = staging / ".postings.sqlite"
        frame_ranges: list[tuple[int, int, int, bytes]] = []
        blob_slices: set[NativeBlobSlice] = set()

        def authenticated_input() -> Generator[bytes, None, None]:
            nonlocal frame_count, byte_count, stored_frame_bytes
            with frames_path.open("wb") as output:
                self._boundary("candidate_frame_spool_write")
                _ = output.write(_CACHE_SCHEMA)
                for frame in frames:
                    if len(frame) < 320 or len(frame) > _MAX_STORED_FRAME:
                        raise GraphProtocolError("CACHE_FRAME_LENGTH")
                    length = struct.pack("<I", len(frame))
                    frame_ranges.append(
                        (
                            stored_frame_bytes + len(length),
                            len(frame),
                            int.from_bytes(frame[24:32], "little"),
                            hashlib.sha256(frame).digest(),
                        )
                    )
                    _ = output.write(length)
                    _ = output.write(frame)
                    digest.update(length)
                    digest.update(frame)
                    frame_count += 1
                    byte_count += len(frame)
                    stored_frame_bytes += len(length) + len(frame)
                    if frame_count == 1 or (frame_count & 1023) == 0:
                        _record_client_progress(
                            "FragmentMaterialization", frame_count, byte_count
                        )
                    if self._instrumentation is not None:
                        self._instrumentation.note_spool_bytes(stored_frame_bytes)
                    yield frame
                output.flush()
                self._boundary("frame_fsync")
                os.fsync(output.fileno())

        stream = authenticated_input()
        ranges_output: BinaryIO | None = None
        logical_output: BinaryIO | None = None
        postings: sqlite3.Connection | None = None
        try:
            _record_client_progress("StreamDecodeStart")
            materialized_records = 0
            ranges_output = ranges_path.open("xb")
            logical_output = logical_records_path.open("xb")
            logical_digest = hashlib.sha256()
            logical_record_digest = hashlib.sha256()
            if primitive_consumer is None:
                postings = sqlite3.connect(postings_path)
                schema = (
                    "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE;"
                    + "CREATE TABLE postings(kind INTEGER NOT NULL, record_id INTEGER PRIMARY KEY);"
                    + "CREATE TABLE nodes(node BLOB PRIMARY KEY, record_id INTEGER UNIQUE NOT NULL);"
                    + "CREATE TABLE properties(owner BLOB NOT NULL, property_key INTEGER NOT NULL, record_id INTEGER UNIQUE NOT NULL);"
                    + "CREATE TABLE owners(owner BLOB NOT NULL, child BLOB UNIQUE NOT NULL, record_id INTEGER UNIQUE NOT NULL);"
                    + "CREATE TABLE blobs(blob BLOB NOT NULL, record_id INTEGER NOT NULL, PRIMARY KEY(blob, record_id));"
                )
                _ = postings.executescript(schema)
            indexed_records = 0
            primitive_ranges: list[_RecordRange] = []
            primitive_range_bytes = bytearray()
            posting_rows: list[tuple[int, int]] = []
            node_rows: list[tuple[bytes, int]] = []
            property_rows: list[tuple[bytes, int, int]] = []
            owner_rows: list[tuple[bytes, bytes, int]] = []
            blob_rows: list[tuple[bytes, int]] = []

            def flush_posting_rows() -> None:
                if postings is None or not posting_rows:
                    return
                _ = postings.executemany(
                    "INSERT INTO postings(kind, record_id) VALUES (?, ?)",
                    posting_rows,
                )
                _ = postings.executemany(
                    "INSERT INTO nodes(node, record_id) VALUES (?, ?)", node_rows
                )
                _ = postings.executemany(
                    "INSERT INTO properties(owner, property_key, record_id) VALUES (?, ?, ?)",
                    property_rows,
                )
                _ = postings.executemany(
                    "INSERT INTO owners(owner, child, record_id) VALUES (?, ?, ?)",
                    owner_rows,
                )
                _ = postings.executemany(
                    "INSERT INTO blobs(blob, record_id) VALUES (?, ?)", blob_rows
                )
                posting_rows.clear()
                node_rows.clear()
                property_rows.clear()
                owner_rows.clear()
                blob_rows.clear()

            def consume_logical_bytes(value: bytes) -> None:
                if logical_output is None:
                    raise GraphProtocolError("CACHE_LOGICAL_RECORDS_MISSING")
                _ = logical_output.write(value)
                logical_digest.update(value)
                logical_record_digest.update(value)

            def consume_record_range(
                kind: RecordKind, record_id: int, offset: int, length: int
            ) -> None:
                nonlocal indexed_records, logical_record_digest
                if (
                    record_id != indexed_records
                    or logical_output is None
                    or logical_output.tell() != offset + length
                    or ranges_output is None
                ):
                    raise GraphProtocolError("CACHE_RECORD_INDEX_RANGE")
                _ = ranges_output.write(
                    _RECORD_RANGE.pack(
                        record_id,
                        int(kind),
                        offset,
                        length,
                        logical_record_digest.digest(),
                    )
                )
                logical_record_digest = hashlib.sha256()
                indexed_records += 1

            def consume_primitive_record(fact: PrimitiveRecordFacts) -> None:
                nonlocal materialized_records, indexed_records
                if fact.record_id != materialized_records:
                    raise GraphProtocolError("CACHE_TYPED_INDEX_RECORD")
                posting_rows.append((int(fact.kind), fact.record_id))
                if fact.node_id is not None:
                    node_rows.append((fact.node_id, fact.record_id))
                    if fact.parent_id is not None:
                        owner_rows.append(
                            (fact.parent_id, fact.node_id, fact.record_id)
                        )
                if fact.property_owner is not None and fact.property_key is not None:
                    property_rows.append(
                        (fact.property_owner, fact.property_key, fact.record_id)
                    )
                for raw_blob in fact.blobs:
                    blob = NativeBlobSlice.model_construct(
                        scalar=ScalarTag.BLOB_SLICE,
                        raw=raw_blob,
                        content_id=raw_blob[:16],
                        offset=int.from_bytes(raw_blob[16:24], "little"),
                        length=int.from_bytes(raw_blob[24:32], "little"),
                        digest=raw_blob[32:64],
                    )
                    blob_slices.add(blob)
                    blob_rows.append((bytes.fromhex(_blob_key(blob)), fact.record_id))
                if ranges_output is None:
                    raise GraphProtocolError("CACHE_RECORD_INDEX_RANGE")
                primitive_range_bytes.extend(
                    _RECORD_RANGE.pack(
                        fact.record_id,
                        int(fact.kind),
                        fact.logical_offset,
                        fact.logical_length,
                        fact.logical_digest,
                    )
                )
                primitive_ranges.append(
                    _RecordRange(
                        fact.record_id,
                        fact.kind,
                        fact.logical_offset,
                        fact.logical_length,
                        fact.logical_digest,
                    )
                )
                indexed_records += 1
                materialized_records += 1
                if self._instrumentation is not None:
                    observer = getattr(
                        self._instrumentation, "observe_primitive_record", None
                    )
                    if observer is not None:
                        observer(fact.record_id, fact.kind)

            def consume_record(record: TypedNativeRecord) -> None:
                nonlocal materialized_records
                if record.record_id != materialized_records or postings is None:
                    raise GraphProtocolError("CACHE_TYPED_INDEX_RECORD")
                posting_rows.append((int(record.kind), record.record_id))
                if isinstance(record, NativeNodeRecord):
                    node_rows.append((record.node_id, record.record_id))
                    parent = _node_parent(record)
                    if parent is not None:
                        owner_rows.append((parent, record.node_id, record.record_id))
                elif isinstance(record, NativePropertyRecord):
                    property_rows.append(
                        (record.owner_node_id, record.property_key, record.record_id)
                    )
                record_blobs = _record_blobs(record)
                blob_rows.extend(
                    (bytes.fromhex(_blob_key(blob)), record.record_id)
                    for blob in record_blobs
                )
                if len(posting_rows) >= 2048:
                    flush_posting_rows()
                materialized_records += 1
                if materialized_records == 1 or (materialized_records & 1023) == 0:
                    _record_client_progress(
                        "RecordMaterialization",
                        materialized_records,
                        len(blob_slices),
                    )
                if self._instrumentation is not None:
                    self._instrumentation.observe_record(record)
                blob_slices.update(record_blobs)
                close_record_content(record)

            if authenticated_consumer is None and primitive_consumer is None:
                decoder = GraphStreamDecoder(
                    stream,
                    record_observer=consume_record_range,
                    logical_record_observer=consume_logical_bytes,
                )
                with decoder.records() as records:
                    for record in records:
                        consume_record(record)
                if decoder.version is None:
                    raise GraphProtocolError("EMPTY_STREAM")
                version_serialized = decoder.version.serialized
                version_session = decoder.version.session
                version_digest = decoder.version.digest
                logical_bytes = decoder.logical_bytes
                stream_digest = decoder.stream_digest
            else:
                # The live producer owns network iteration while this callback
                # persists the exact authenticated bytes. No model survives
                # the callback and no publication record replay is required.
                stream.close()
                with frames_path.open("wb") as output:
                    self._boundary("candidate_frame_spool_write")
                    _ = output.write(_CACHE_SCHEMA)

                    def consume_frame(frame: bytes) -> None:
                        nonlocal frame_count, byte_count, stored_frame_bytes
                        if len(frame) < 320 or len(frame) > _MAX_STORED_FRAME:
                            raise GraphProtocolError("CACHE_FRAME_LENGTH")
                        length = struct.pack("<I", len(frame))
                        frame_ranges.append(
                            (
                                stored_frame_bytes + len(length),
                                len(frame),
                                int.from_bytes(frame[24:32], "little"),
                                hashlib.sha256(frame).digest(),
                            )
                        )
                        _ = output.write(length)
                        _ = output.write(frame)
                        digest.update(length)
                        digest.update(frame)
                        frame_count += 1
                        byte_count += len(frame)
                        stored_frame_bytes += len(length) + len(frame)
                        if self._instrumentation is not None:
                            self._instrumentation.note_spool_bytes(stored_frame_bytes)

                    if primitive_consumer is not None:
                        seal = primitive_consumer(
                            consume_frame,
                            consume_primitive_record,
                            consume_logical_bytes,
                        )
                    elif authenticated_consumer is not None:
                        seal = authenticated_consumer(
                            consume_frame,
                            consume_record,
                            consume_record_range,
                            consume_logical_bytes,
                        )
                    else:
                        raise GraphProtocolError("CACHE_CONSUMER")
                    output.flush()
                    self._boundary("frame_fsync")
                    os.fsync(output.fileno())
                version = seal[0]
                version_serialized = getattr(version, "serialized", None)
                version_session = getattr(version, "session", None)
                if not isinstance(version_serialized, bytes) or not isinstance(
                    version_session, bytes
                ):
                    raise GraphProtocolError("CACHE_VERSION")
                if seal[1] != materialized_records or len(seal) != 6:
                    raise GraphProtocolError("BAD_TERMINAL")
                version_digest = hashlib.sha256(version_serialized).hexdigest()
                logical_bytes = seal[2]
                stream_digest = seal[5]
            flush_posting_rows()
            if (
                len(stream_digest) != 32
                or indexed_records != materialized_records
                or logical_output.tell() != logical_bytes
            ):
                raise GraphProtocolError("BAD_TERMINAL")
            logical_output.flush()
            os.fsync(logical_output.fileno())
            logical_output.close()
            logical_output = None
            if primitive_range_bytes:
                _ = ranges_output.write(primitive_range_bytes)
            ranges_output.flush()
            if primitive_consumer is None:
                os.fsync(ranges_output.fileno())
            ranges_output.close()
            ranges_output = None
            if postings is not None:
                postings.commit()
                index_source = postings
            else:

                class PrimitiveIndexSource:
                    def execute(self, query_text: str) -> Iterable[tuple[object, ...]]:
                        if "FROM postings" in query_text:
                            return sorted(posting_rows)
                        if "FROM nodes" in query_text:
                            return sorted(node_rows)
                        if "FROM properties" in query_text:
                            return sorted(property_rows)
                        if "FROM owners" in query_text:
                            return sorted(owner_rows)
                        if "FROM blobs" in query_text:
                            return sorted(blob_rows)
                        raise GraphProtocolError("CACHE_TYPED_INDEX_SECTION")

                index_source = cast(
                    sqlite3.Connection, cast(object, PrimitiveIndexSource())
                )
            (
                index_domain_sha256,
                records_index_bytes,
                records_index_sha256,
            ) = _write_sectioned_index(
                record_index_path,
                staging,
                index_source,
                ranges_path,
                frame_ranges,
                record_count=materialized_records,
                logical_bytes=logical_bytes,
                stream_digest=stream_digest,
                version_digest=bytes.fromhex(version_digest),
                frames_digest=bytes.fromhex(digest.hexdigest()),
                query_key=key,
                graph_version=version_serialized,
            )
            if postings is not None:
                postings.close()
                postings = None
            postings_path.unlink(missing_ok=True)
            ranges_path.unlink(missing_ok=True)
            self._boundary("record_index_fsync")
            published_query_index = (
                _primitive_query_index(
                    primitive_ranges,
                    posting_rows,
                    node_rows,
                    property_rows,
                    owner_rows,
                    blob_rows,
                )
                if primitive_consumer is not None
                else None
            )
            logical_records_sha256 = logical_digest.hexdigest()
            _record_client_progress("StreamDecodeEnd", materialized_records, byte_count)
            if query.session_id and version_session.hex() != query.session_id:
                raise GraphProtocolError("GRAPH_SESSION_MISMATCH")
            if (
                query.expected_version_digest
                and query.expected_version_digest != version_digest
            ):
                raise GraphProtocolError("GRAPH_VERSION_MISMATCH")
            blobs_digest = hashlib.sha256()
            stored_blob_bytes = 0
            blob_pack_sha256 = ""
            blob_index_sha256 = ""
            if blob_slices:
                if blob_reader is None:
                    raise GraphProtocolError("CACHE_BLOB_READER")
                ordered_blobs = tuple(sorted(blob_slices, key=_blob_key))
                packed_values = (
                    blob_batch_reader(ordered_blobs)
                    if blob_batch_reader is not None
                    else None
                )
                if packed_values is not None and set(packed_values) != set(
                    ordered_blobs
                ):
                    raise GraphProtocolError("CACHE_BLOB_PACK_BINDING")
                pack_digest = hashlib.sha256()
                index = bytearray()
                with (staging / "blobs.pack").open("xb") as output:
                    for blob_ordinal, blob in enumerate(ordered_blobs, start=1):
                        value = (
                            packed_values[blob]
                            if packed_values is not None
                            else blob_reader(blob)
                        )
                        if blob_ordinal == 1 or (blob_ordinal & 1023) == 0:
                            _record_client_progress(
                                "BlobCacheWrite", blob_ordinal, len(value)
                            )
                        if (
                            len(value) != blob.length
                            or hashlib.sha256(value).digest() != blob.digest
                        ):
                            raise GraphProtocolError("CACHE_BLOB_AUTHENTICITY")
                        name = _blob_key(blob)
                        blobs_digest.update(name.encode("ascii"))
                        blobs_digest.update(value)
                        index.extend(bytes.fromhex(name))
                        index.extend(stored_blob_bytes.to_bytes(8, "little"))
                        index.extend(len(value).to_bytes(8, "little"))
                        index.extend(blob.digest)
                        _ = output.write(value)
                        pack_digest.update(value)
                        stored_blob_bytes += len(value)
                        if self._instrumentation is not None and (
                            blob_ordinal == 1 or (blob_ordinal & 1023) == 0
                        ):
                            self._instrumentation.note_spool_bytes(
                                stored_frame_bytes + stored_blob_bytes
                            )
                    output.flush()
                    self._boundary("blob_fsync")
                    os.fsync(output.fileno())
                blob_pack_sha256 = pack_digest.hexdigest()
                with (staging / "blobs.index").open("xb") as output:
                    _ = output.write(index)
                    output.flush()
                    self._boundary("blob_index_fsync")
                    os.fsync(output.fileno())
                blob_index_sha256 = hashlib.sha256(index).hexdigest()
            generation_name = f"{key}-{digest.hexdigest()}{generation_suffix}"
            metadata = _Metadata(
                generation=generation_name,
                key=key,
                query_json=self._query_json(query),
                frame_count=frame_count,
                byte_count=byte_count,
                frames_sha256=digest.hexdigest(),
                version_digest=version_digest,
                record_count=materialized_records,
                logical_bytes=logical_bytes,
                stream_digest=stream_digest.hex(),
                records_index_bytes=records_index_bytes,
                records_index_sha256=records_index_sha256,
                logical_records_bytes=logical_bytes,
                logical_records_sha256=logical_records_sha256,
                index_domain_sha256=index_domain_sha256,
                blob_count=len(blob_slices),
                blobs_sha256=blobs_digest.hexdigest(),
                blob_pack_sha256=blob_pack_sha256,
                blob_index_sha256=blob_index_sha256,
            )
            metadata_bytes = metadata.encoded()
            metadata_path = staging / "metadata.json"
            self._boundary("generation_metadata_write")
            with metadata_path.open("wb") as output:
                _ = output.write(metadata_bytes)
                output.flush()
                self._boundary("metadata_fsync")
                os.fsync(output.fileno())
            generation_path = self._generations / generation_name
            if generation_path.exists():
                if (generation_path / "metadata.json").read_bytes() != metadata_bytes:
                    raise GraphProtocolError("CACHE_GENERATION_COLLISION")
                shutil.rmtree(staging)
            else:
                self._boundary("generation_directory_rename")
                os.replace(staging, generation_path)
                generation_created = True
            pointer = _Pointer(
                generation=generation_name,
                metadata_sha256=hashlib.sha256(metadata_bytes).hexdigest(),
            )
            pointer_fd, pointer_temporary = tempfile.mkstemp(
                prefix=f".{key}.", suffix=".pointer.tmp", dir=self._pointers
            )
            with os.fdopen(pointer_fd, "wb") as output:
                self._boundary("pointer_temp_write")
                _ = output.write(pointer.encoded())
                output.flush()
                self._boundary("pointer_fsync")
                os.fsync(output.fileno())
            self._boundary("pointer_replace")
            os.replace(pointer_temporary, self._pointer_path(key))
            pointer_temporary = ""
            committed = True
            query_index = (
                published_query_index
                if published_query_index is not None
                else _read_generation_index(generation_path, metadata)
            )
            snapshot = NativeGraphSnapshot(
                generation_path / "frames.bin",
                metadata,
                spool_root=self._spool_root,
                query_index=query_index,
            )
            self._boundary("post_pointer_return")
            _record_client_progress("CachePublishEnd", frame_count, byte_count)
            return snapshot
        finally:
            stream.close()
            if logical_output is not None:
                logical_output.close()
            if ranges_output is not None:
                ranges_output.close()
            if postings is not None:
                postings.close()
            if generation_path is not None and generation_created and not committed:
                shutil.rmtree(generation_path, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)
            if pointer_temporary:
                Path(pointer_temporary).unlink(missing_ok=True)


__all__ = ["NativeGraphCache", "NativeGraphSnapshot"]

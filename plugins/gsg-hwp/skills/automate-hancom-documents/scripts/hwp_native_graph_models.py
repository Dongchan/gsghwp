from __future__ import annotations

from collections.abc import Iterator
from enum import IntEnum
from importlib import import_module
from pathlib import Path
from typing import (
    Annotated,
    Callable,
    ClassVar,
    Literal,
    Self,
    TypeAlias,
    cast,
    override,
)

from pydantic import ConfigDict, Field, model_validator

from _hwp_native_graph_errors import GraphProtocolError
from hwp_live_values import ContractModel


_TRUSTED_DEFAULTS: dict[type[object], dict[str, object]] = {}


class NativeContractModel(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", strict=True
    )

    @classmethod
    @override
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: object
    ) -> Self:
        """Construct only after an authenticated parser proved model invariants."""
        defaults = _TRUSTED_DEFAULTS.get(cls)
        if defaults is None:
            defaults = {
                name: cast(object, field.default)
                for name, field in cls.model_fields.items()
                if not field.is_required()
            }
            _TRUSTED_DEFAULTS[cls] = defaults
        instance = cls.__new__(cls)
        object.__setattr__(instance, "__dict__", defaults | values)
        object.__setattr__(
            instance,
            "__pydantic_fields_set__",
            set(values) if _fields_set is None else _fields_set,
        )
        object.__setattr__(instance, "__pydantic_extra__", None)
        object.__setattr__(instance, "__pydantic_private__", None)
        return instance


class NativeSpooledBytes(NativeContractModel):
    """Authenticated field content retained on disk instead of in Python memory."""

    kind: Literal["spooled_bytes"] = "spooled_bytes"
    path: Path
    offset: Annotated[int, Field(ge=0)] = 0
    length: Annotated[int, Field(ge=0)]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def chunks(self, chunk_bytes: int = 1024 * 1024) -> Iterator[bytes]:
        if chunk_bytes <= 0:
            raise GraphProtocolError("CHUNK_BYTES")
        try:
            source = self.path.open("rb")
        except FileNotFoundError as error:
            raise GraphProtocolError("SPOOL_CLOSED") from error
        with source:
            _ = source.seek(self.offset)
            remaining = self.length
            while remaining:
                chunk = source.read(min(chunk_bytes, remaining))
                if not chunk:
                    raise GraphProtocolError("SPOOL_TRUNCATED")
                remaining -= len(chunk)
                yield chunk

    def close(self) -> None:
        self.path.unlink(missing_ok=True)


class RecordKind(IntEnum):
    MANIFEST = 0
    NODE = 1
    EDGE = 2
    PROPERTY = 3
    COVERAGE = 4
    DIAGNOSTIC = 5
    ASSET_CHUNK = 6
    TOMBSTONE = 7
    REMAP = 8


class NodeKind(IntEnum):
    DOCUMENT = 0
    STORY = 1
    SECTION = 2
    PARAGRAPH = 3
    CHARACTER_RUN = 4
    SPECIAL_CHARACTER = 5
    GENERATED_TEXT = 6
    GENERIC_CONTROL = 7
    TABLE = 8
    TABLE_CELL = 9
    IMAGE = 10
    BINARY_DATA = 11
    DEFINITION = 12


class ScalarTag(IntEnum):
    SINT64 = 0
    UINT64 = 1
    BOOL = 2
    FLOAT64 = 3
    UTF16 = 4
    HWPUNIT64 = 5
    BGR = 6
    ENUM = 7
    BLOB_SLICE = 8
    UUID128 = 9
    SHA256 = 10
    STRUCT = 11
    RAW_URC32 = 12
    BYTES = 13
    UINT8 = 14
    UINT16 = 15
    UINT32 = 16
    SINT32 = 17


class UnavailableReason(IntEnum):
    NOT_APPLICABLE = 1
    NOT_EXPOSED = 2
    READ_FAILED = 3
    INCONSISTENT_NATIVE_STATE = 4
    NOT_REQUESTED = 5
    PROJECTION_OMITTED = 6


NativeId = Annotated[bytes, Field(min_length=16, max_length=16)]
Digest = Annotated[bytes, Field(min_length=32, max_length=32)]
NativeContent: TypeAlias = bytes | NativeSpooledBytes


class NativeScalar(NativeContractModel):
    scalar: ScalarTag
    raw: NativeContent


class NativeInteger(NativeScalar):
    kind: Literal["integer"] = "integer"
    value: int


class NativeBoolean(NativeScalar):
    kind: Literal["boolean"] = "boolean"
    value: bool


class NativeFloat(NativeScalar):
    kind: Literal["float"] = "float"
    value: float


class NativeRawUtf16(NativeScalar):
    kind: Literal["raw_utf16"] = "raw_utf16"
    code_units: Annotated[int, Field(ge=0)]


class NativeIdentifier(NativeScalar):
    kind: Literal["identifier"] = "identifier"
    value: NativeId


class NativeDigest(NativeScalar):
    kind: Literal["digest"] = "digest"
    value: Digest


class NativeBlobSlice(NativeScalar):
    kind: Literal["blob_slice"] = "blob_slice"
    content_id: NativeId
    offset: Annotated[int, Field(ge=0)]
    length: Annotated[int, Field(ge=0)]
    digest: Digest


class NativeOpaque(NativeScalar):
    kind: Literal["opaque"] = "opaque"


NativeScalarValue: TypeAlias = Annotated[
    NativeInteger
    | NativeBoolean
    | NativeFloat
    | NativeRawUtf16
    | NativeIdentifier
    | NativeDigest
    | NativeBlobSlice
    | NativeOpaque,
    Field(discriminator="kind"),
]


class NativeGraphPresent(NativeContractModel):
    availability: Literal["present"] = "present"
    value: NativeScalarValue


class NativeGraphUnavailable(NativeContractModel):
    availability: Literal["unavailable"] = "unavailable"
    reason: UnavailableReason
    hresult: int
    detail: NativeRawUtf16
    value: Literal["unavailable"] = "unavailable"


NativeObservation: TypeAlias = Annotated[
    NativeGraphPresent | NativeGraphUnavailable, Field(discriminator="availability")
]


class NativeField(NativeContractModel):
    tag: Annotated[int, Field(ge=1, le=65_535)]
    flags: Annotated[int, Field(ge=0, le=3)]
    scalar: ScalarTag
    element_count: Annotated[int, Field(ge=0)]
    value: NativeScalarValue | NativeObservation


class NativeGraphRecord(NativeContractModel):
    record_id: Annotated[int, Field(ge=0)]
    flags: Annotated[int, Field(ge=0, le=1)]
    fields: Annotated[tuple[NativeField, ...], Field(max_length=65_535)]


class NativeManifestRecord(NativeGraphRecord):
    kind: Literal[RecordKind.MANIFEST] = RecordKind.MANIFEST


class NativeNodeRecord(NativeGraphRecord):
    kind: Literal[RecordKind.NODE] = RecordKind.NODE
    node_id: NativeId
    node_kind: NodeKind

    @model_validator(mode="after")
    def reject_zero_native_identity(self) -> NativeNodeRecord:
        if (
            not any(self.node_id)
            or self.node_id[6] & 0xF0 != 0x40
            or self.node_id[8] & 0xC0 != 0x80
        ):
            raise ValueError("HGN1_NODE_ID_INVALID")
        return self


class NativeEdgeRecord(NativeGraphRecord):
    kind: Literal[RecordKind.EDGE] = RecordKind.EDGE


class NativePropertyRecord(NativeGraphRecord):
    kind: Literal[RecordKind.PROPERTY] = RecordKind.PROPERTY
    owner_node_id: NativeId
    owner_field_tag: Annotated[int, Field(ge=1, le=65_535)]
    property_key: Annotated[int, Field(ge=0, le=0xFFFF_FFFF)]
    observation: NativeObservation


class NativeTablePageSpan(NativeContractModel):
    """One-based inclusive TABLE page range authenticated by layout properties."""

    node_id: NativeId
    page_start: Annotated[int, Field(ge=1)]
    page_end: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def require_multiple_pages(self) -> Self:
        if self.page_end <= self.page_start:
            raise ValueError("HGN1_TABLE_NOT_PAGE_SPANNING")
        return self


def _exact_content_bytes(value: NativeContent, expected: int) -> bytes:
    if isinstance(value, bytes):
        raw = value
    else:
        if value.length != expected:
            raise GraphProtocolError("TABLE_PAGE_SPAN_VALUE")
        raw = b"".join(value.chunks(expected))
    if len(raw) != expected:
        raise GraphProtocolError("TABLE_PAGE_SPAN_VALUE")
    return raw


def authenticated_table_page_span(
    records: tuple[TypedNativeRecord, ...] | list[TypedNativeRecord],
    node_id: bytes,
) -> NativeTablePageSpan:
    """Decode required 12000/12001 UINT64 Generated Values, failing closed."""

    tables = [
        record
        for record in records
        if isinstance(record, NativeNodeRecord)
        and record.node_id == node_id
        and record.node_kind is NodeKind.TABLE
    ]
    if len(tables) != 1:
        raise GraphProtocolError("TABLE_PAGE_SPAN_NODE")
    properties = [
        record
        for record in records
        if isinstance(record, NativePropertyRecord)
        and record.owner_node_id == node_id
        and record.property_key in (12000, 12001)
    ]
    by_key = {record.property_key: record for record in properties}
    if len(properties) != 2 or len(by_key) != 2:
        raise GraphProtocolError("TABLE_PAGE_SPAN_CARDINALITY")

    values: dict[int, int] = {}
    for key in (12000, 12001):
        record = by_key[key]
        observation = record.observation
        origin = record.fields[4].value if len(record.fields) == 5 else None
        if (
            record.owner_field_tag != 10
            or not isinstance(observation, NativeGraphPresent)
            or not isinstance(observation.value, NativeInteger)
            or observation.value.scalar is not ScalarTag.UINT64
            or _exact_content_bytes(observation.value.raw, 8)
            != observation.value.value.to_bytes(8, "little")
            or not isinstance(origin, NativeInteger)
            or origin.scalar is not ScalarTag.UINT8
            or origin.value != 2
        ):
            raise GraphProtocolError("TABLE_PAGE_SPAN_CONTRACT")
        values[key] = observation.value.value
    try:
        return NativeTablePageSpan(
            node_id=node_id,
            page_start=values[12000],
            page_end=values[12001],
        )
    except ValueError as error:
        raise GraphProtocolError("TABLE_PAGE_SPAN_RANGE") from error


class NativeCoverageRecord(NativeGraphRecord):
    kind: Literal[RecordKind.COVERAGE] = RecordKind.COVERAGE


class NativeDiagnosticRecord(NativeGraphRecord):
    kind: Literal[RecordKind.DIAGNOSTIC] = RecordKind.DIAGNOSTIC


class NativeAssetChunkRecord(NativeGraphRecord):
    kind: Literal[RecordKind.ASSET_CHUNK] = RecordKind.ASSET_CHUNK


class NativeTombstoneRecord(NativeGraphRecord):
    kind: Literal[RecordKind.TOMBSTONE] = RecordKind.TOMBSTONE


class NativeRemapRecord(NativeGraphRecord):
    kind: Literal[RecordKind.REMAP] = RecordKind.REMAP


TypedNativeRecord: TypeAlias = Annotated[
    NativeManifestRecord
    | NativeNodeRecord
    | NativeEdgeRecord
    | NativePropertyRecord
    | NativeCoverageRecord
    | NativeDiagnosticRecord
    | NativeAssetChunkRecord
    | NativeTombstoneRecord
    | NativeRemapRecord,
    Field(discriminator="kind"),
]


def close_record_content(record: NativeGraphRecord) -> None:
    paths: set[Path] = set()
    for field in record.fields:
        value = field.value
        if isinstance(value, NativeGraphPresent):
            raw = value.value.raw
        elif isinstance(value, NativeGraphUnavailable):
            raw = value.detail.raw
        else:
            raw = value.raw
        if isinstance(raw, NativeSpooledBytes):
            paths.add(raw.path)
    for path in paths:
        path.unlink(missing_ok=True)


class NativeFrameVersion(NativeContractModel):
    session: NativeId
    graph: NativeId
    profile_bits: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    semantic_revision: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    layout_revision: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    locator_epoch: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    semantic_root: Digest
    layout_root: Digest
    capture_root: Digest
    semantic_certified: bool
    layout_present: bool

    @property
    def serialized(self) -> bytes:
        value = bytearray(168)
        value[0:2] = (1).to_bytes(2, "little")
        value[2:10] = self.profile_bits.to_bytes(8, "little")
        value[10] = self.semantic_certified
        value[11] = self.layout_present
        value[16:32] = self.session
        value[32:48] = self.graph
        value[48:56] = self.semantic_revision.to_bytes(8, "little")
        value[56:64] = self.layout_revision.to_bytes(8, "little")
        value[64:72] = self.locator_epoch.to_bytes(8, "little")
        value[72:104] = self.semantic_root
        value[104:136] = self.layout_root
        value[136:168] = self.capture_root
        return bytes(value)


class NativeFramePayload(NativeContractModel):
    raw: Annotated[bytes, Field(max_length=4_193_984)]
    fields: tuple[NativeField, ...]


class NativeCapabilitiesPayload(NativeFramePayload):
    pass


class NativeOpenReceiptPayload(NativeFramePayload):
    pass


class NativeGraphFragment(NativeContractModel):
    kind: RecordKind
    record_flags: Annotated[int, Field(ge=0, le=1)]
    tag: Annotated[int, Field(ge=1, le=65_535)]
    marked_flags: Annotated[int, Field(ge=0, le=0x3F)]
    scalar: ScalarTag
    field_count: Annotated[int, Field(ge=1, le=65_535)]
    record_id: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    element_count: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    field_total: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    field_offset: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    data: bytes


class NativeGraphChunkPayload(NativeFramePayload):
    fragments: Annotated[tuple[NativeGraphFragment, ...], Field(min_length=1)]


class NativeGraphTerminalPayload(NativeFramePayload):
    pass


class NativeErrorPayload(NativeFramePayload):
    pass


class NativePatchBeginReceiptPayload(NativeFramePayload):
    pass


class NativePatchChunkReceiptPayload(NativeFramePayload):
    pass


class NativePatchSealReceiptPayload(NativeFramePayload):
    pass


class NativeCursorClosedReceiptPayload(NativeFramePayload):
    pass


class NativePatchAbortReceiptPayload(NativeFramePayload):
    pass


class NativePatchValidationReceiptPayload(NativeFramePayload):
    pass


class NativeBlobChunkPayload(NativeFramePayload):
    pass


class NativeBlobPackPayload(NativeFramePayload):
    pass


class NativeGraphOpenRequestPayload(NativeFramePayload):
    pass


class NativeGraphNextRequestPayload(NativeFramePayload):
    pass


class NativeGraphCancelRequestPayload(NativeFramePayload):
    pass


class NativeGraphCloseRequestPayload(NativeFramePayload):
    pass


class NativePatchBeginRequestPayload(NativeFramePayload):
    pass


class NativePatchChunkRequestPayload(NativeFramePayload):
    pass


class NativePatchCommitRequestPayload(NativeFramePayload):
    pass


class NativePatchAbortRequestPayload(NativeFramePayload):
    pass


class NativePatchValidateRequestPayload(NativeFramePayload):
    pass


class NativeBlobReadRequestPayload(NativeFramePayload):
    pass


class NativeBlobPackRequestPayload(NativeFramePayload):
    pass


class NativeFrameModel(NativeContractModel):
    raw: Annotated[bytes, Field(min_length=320, max_length=4_194_304)]
    flags: Annotated[int, Field(ge=0, le=0x3F)]
    sequence: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    fragment_offset: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    fragment_total: Annotated[int, Field(ge=0, le=0xFFFF_FFFF_FFFF_FFFF)]
    cursor: NativeId
    previous_chain: Digest
    chunk_digest: Digest
    chain_digest: Digest
    version: NativeFrameVersion
    payload: Annotated[bytes, Field(max_length=4_193_984)]

    @model_validator(mode="after")
    def authenticate_raw_and_mirrors(self) -> Self:
        authenticate = cast(
            Callable[[object], None],
            getattr(
                import_module("_hwp_native_graph_wire"),
                "authenticate_supported_frame_model",
            ),
        )
        authenticate(self)
        return self


class NativeCapabilitiesFrame(NativeFrameModel):
    message: Literal[1] = 1
    parsed_payload: NativeCapabilitiesPayload


class NativeOpenReceiptFrame(NativeFrameModel):
    message: Literal[2] = 2
    parsed_payload: NativeOpenReceiptPayload


class NativeGraphChunkFrame(NativeFrameModel):
    message: Literal[3] = 3
    parsed_payload: NativeGraphChunkPayload


class NativeGraphTerminalFrame(NativeFrameModel):
    message: Literal[4] = 4
    parsed_payload: NativeGraphTerminalPayload


class NativeErrorFrame(NativeFrameModel):
    message: Literal[5] = 5
    parsed_payload: NativeErrorPayload


class NativePatchBeginReceiptFrame(NativeFrameModel):
    message: Literal[6] = 6
    parsed_payload: NativePatchBeginReceiptPayload


class NativePatchChunkReceiptFrame(NativeFrameModel):
    message: Literal[7] = 7
    parsed_payload: NativePatchChunkReceiptPayload


class NativePatchSealReceiptFrame(NativeFrameModel):
    message: Literal[8] = 8
    parsed_payload: NativePatchSealReceiptPayload


class NativeCursorClosedReceiptFrame(NativeFrameModel):
    message: Literal[9] = 9
    parsed_payload: NativeCursorClosedReceiptPayload


class NativePatchAbortReceiptFrame(NativeFrameModel):
    message: Literal[10] = 10
    parsed_payload: NativePatchAbortReceiptPayload


class NativePatchValidationReceiptFrame(NativeFrameModel):
    message: Literal[11] = 11
    parsed_payload: NativePatchValidationReceiptPayload


class NativeBlobChunkFrame(NativeFrameModel):
    message: Literal[13] = 13
    parsed_payload: NativeBlobChunkPayload


class NativeBlobPackFrame(NativeFrameModel):
    message: Literal[14] = 14
    parsed_payload: NativeBlobPackPayload


class NativeGraphOpenRequestFrame(NativeFrameModel):
    message: Literal[101] = 101
    parsed_payload: NativeGraphOpenRequestPayload


class NativeGraphNextRequestFrame(NativeFrameModel):
    message: Literal[102] = 102
    parsed_payload: NativeGraphNextRequestPayload


class NativeGraphCancelRequestFrame(NativeFrameModel):
    message: Literal[103] = 103
    parsed_payload: NativeGraphCancelRequestPayload


class NativeGraphCloseRequestFrame(NativeFrameModel):
    message: Literal[104] = 104
    parsed_payload: NativeGraphCloseRequestPayload


class NativePatchBeginRequestFrame(NativeFrameModel):
    message: Literal[105] = 105
    parsed_payload: NativePatchBeginRequestPayload


class NativePatchChunkRequestFrame(NativeFrameModel):
    message: Literal[106] = 106
    parsed_payload: NativePatchChunkRequestPayload


class NativePatchCommitRequestFrame(NativeFrameModel):
    message: Literal[107] = 107
    parsed_payload: NativePatchCommitRequestPayload


class NativePatchAbortRequestFrame(NativeFrameModel):
    message: Literal[108] = 108
    parsed_payload: NativePatchAbortRequestPayload


class NativePatchValidateRequestFrame(NativeFrameModel):
    message: Literal[109] = 109
    parsed_payload: NativePatchValidateRequestPayload


class NativeBlobReadRequestFrame(NativeFrameModel):
    message: Literal[111] = 111
    parsed_payload: NativeBlobReadRequestPayload


class NativeBlobPackRequestFrame(NativeFrameModel):
    message: Literal[112] = 112
    parsed_payload: NativeBlobPackRequestPayload


SupportedNativeFrame: TypeAlias = Annotated[
    NativeCapabilitiesFrame
    | NativeOpenReceiptFrame
    | NativeGraphChunkFrame
    | NativeGraphTerminalFrame
    | NativeErrorFrame
    | NativePatchBeginReceiptFrame
    | NativePatchChunkReceiptFrame
    | NativePatchSealReceiptFrame
    | NativeCursorClosedReceiptFrame
    | NativePatchAbortReceiptFrame
    | NativePatchValidationReceiptFrame
    | NativeBlobChunkFrame
    | NativeBlobPackFrame
    | NativeGraphOpenRequestFrame
    | NativeGraphNextRequestFrame
    | NativeGraphCancelRequestFrame
    | NativeGraphCloseRequestFrame
    | NativePatchBeginRequestFrame
    | NativePatchChunkRequestFrame
    | NativePatchCommitRequestFrame
    | NativePatchAbortRequestFrame
    | NativePatchValidateRequestFrame
    | NativeBlobReadRequestFrame
    | NativeBlobPackRequestFrame,
    Field(discriminator="message"),
]


class NativeGraphQuery(NativeContractModel):
    store_id: Annotated[str, Field(min_length=1, max_length=256)]
    projection_bits: Annotated[int, Field(ge=0)]
    query_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    graph_id: Annotated[str, Field(max_length=64)] = ""
    session_id: Annotated[str, Field(max_length=64)] = ""
    store_epoch: Annotated[str, Field(max_length=64)] = ""
    expected_version_digest: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{64})?$")] = ""
    digest_contract: Literal["HGN1-SHA256-V1"] = "HGN1-SHA256-V1"


__all__ = [
    "Digest",
    "NativeAssetChunkRecord",
    "NativeBlobSlice",
    "NativeBoolean",
    "NativeCoverageRecord",
    "NativeDiagnosticRecord",
    "NativeDigest",
    "NativeEdgeRecord",
    "NativeField",
    "NativeFloat",
    "NativeFrameVersion",
    "NativeGraphPresent",
    "NativeGraphQuery",
    "NativeGraphRecord",
    "NativeGraphUnavailable",
    "NativeIdentifier",
    "NativeInteger",
    "NativeManifestRecord",
    "NativeNodeRecord",
    "NativeOpaque",
    "NativePropertyRecord",
    "NativeRawUtf16",
    "NativeRemapRecord",
    "NativeSpooledBytes",
    "NativeScalarValue",
    "NativeTablePageSpan",
    "NativeTombstoneRecord",
    "NodeKind",
    "RecordKind",
    "ScalarTag",
    "SupportedNativeFrame",
    "TypedNativeRecord",
    "UnavailableReason",
    "authenticated_table_page_span",
    "close_record_content",
]

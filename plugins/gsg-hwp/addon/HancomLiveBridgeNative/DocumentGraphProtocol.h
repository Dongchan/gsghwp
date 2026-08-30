#pragma once

#include "DocumentGraphCodec.h"
#include "DocumentGraphIdentity.h"
#include "DocumentGraphStore.h"

#include <Windows.h>
#include <OleAuto.h>

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace hancom::graph::layout {
struct LayoutEnvironmentPlatformV1;
}
namespace hancom::graph::query {
struct BlobSlice;
struct QueryView;
}

namespace hancom::graph::protocol {

inline constexpr std::uint16_t kGraphProtocolVersion = 15;
inline constexpr std::uint16_t kFrameSchema = 1;
inline constexpr std::uint32_t kHeaderBytes = 320U;
inline constexpr std::uint64_t kMaximumFrameBytes = 4194304ULL;
inline constexpr std::uint64_t kMaximumPayloadBytes = 4193984ULL;
inline constexpr std::uint64_t kMaximumTotalBytes = UINT64_MAX;
inline constexpr std::uint64_t kCapabilityGraphRead = 1ULL;
inline constexpr std::uint64_t kCapabilityAssetRead = 8ULL;
inline constexpr std::uint64_t kCapabilityPatchWrite = 16ULL;
inline constexpr std::uint64_t kCapabilityPatchValidate = 32ULL;
inline constexpr std::uint64_t kCapabilityBits = kCapabilityGraphRead |
    kCapabilityAssetRead | kCapabilityPatchWrite | kCapabilityPatchValidate;
inline constexpr std::uint32_t kMaximumErrorFrameBytes = 4096U;

inline constexpr std::uint32_t kFlagLayoutPresent = 1U << 0;
inline constexpr std::uint32_t kFlagTerminal = 1U << 1;
inline constexpr std::uint32_t kFlagError = 1U << 2;
inline constexpr std::uint32_t kFlagFragmented = 1U << 3;
inline constexpr std::uint32_t kFlagPatch = 1U << 4;
inline constexpr std::uint32_t kFlagSemanticCertified = 1U << 5;
inline constexpr std::uint32_t kKnownFlags = 0x3fU;

enum class MessageKind : std::uint16_t {
    Capabilities = 1,
    OpenReceipt = 2,
    GraphChunk = 3,
    GraphTerminal = 4,
    Error = 5,
    PatchBeginReceipt = 6,
    PatchChunkReceipt = 7,
    PatchSealReceipt = 8,
    CursorClosedReceipt = 9,
    PatchAbortReceipt = 10,
    PatchValidationReceipt = 11,
    PatchApplyReceipt = 12,
    BlobChunk = 13,
    BlobPack = 14,
    GraphOpenRequest = 101,
    GraphNextRequest = 102,
    GraphCancelRequest = 103,
    GraphCloseRequest = 104,
    PatchBeginRequest = 105,
    PatchChunkRequest = 106,
    PatchCommitRequest = 107,
    PatchAbortRequest = 108,
    PatchValidateRequest = 109,
    PatchApplyRequest = 110,
    BlobReadRequest = 111,
    BlobPackRequest = 112,
};

enum class ErrorCode : std::uint32_t {
    BufferTooSmall = 0,
    BadArray = 1,
    BadHeader = 2,
    BadSchema = 3,
    BadMessageKind = 4,
    BadField = 5,
    DigestMismatch = 6,
    SequenceMismatch = 7,
    CursorNotFound = 8,
    CursorClosed = 9,
    RouteChanged = 10,
    StaleGraph = 11,
    UploadNotFound = 12,
    UploadState = 13,
    LengthMismatch = 14,
    FinalDigestMismatch = 15,
    Cancelled = 16,
    StorageFailure = 17,
    Internal = 18,
    UnsupportedCapability = 19,
};

enum class CursorState : std::uint8_t {
    Active = 0, Terminal = 1, Cancelled = 2, Closed = 3, Invalidated = 4,
};
enum class UploadState : std::uint8_t {
    Open = 0, Sealed = 1, Validated = 2, Applying = 3, Applied = 4,
    Failed = 5, RolledBack = 6, Aborted = 7,
};

enum class Projection : std::uint8_t {
    Structure = 0, Text = 1, CharacterFormat = 2, ParagraphFormat = 3,
    ControlProperties = 4, TableTopology = 5, CellFormat = 6,
    ImageMetadata = 7, BinaryContent = 8, Layout = 9, Coverage = 10,
    Diagnostics = 11,
};

enum class Axis : std::uint8_t {
    Self = 0, Children = 1, Descendants = 2, DocumentOrder = 3, References = 4,
};
enum class QueryOperator : std::uint8_t {
    Eq = 0, Ne = 1, Lt = 2, Le = 3, Gt = 4, Ge = 5, ContainsUTF16 = 6,
};

struct Route final {
    LONG documentId = 0;
    std::uintptr_t windowHandle = 0;
};

struct Header final {
    MessageKind message = MessageKind::Capabilities;
    std::uint32_t flags = 0;
    std::uint64_t payloadBytes = 0;
    std::uint64_t sequence = 0;
    std::uint64_t fragmentOffset = 0;
    std::uint64_t fragmentTotal = 0;
    identity::DocumentSessionId session{};
    identity::GraphId graph{};
    std::uint64_t profileBits = 0;
    std::uint64_t semanticRevision = 0;
    std::uint64_t layoutRevision = 0;
    std::uint64_t locatorEpoch = 0;
    Sha256 observedSemanticRoot{};
    Sha256 layoutRoot{};
    Sha256 captureRoot{};
    Uuid128 cursorOrUpload{};
    Sha256 previousChain{};
    Sha256 chunkDigest{};
    Sha256 chainDigest{};
};

struct ParsedField final {
    FieldTag tag = 0;
    std::uint16_t flags = 0;
    ScalarTag scalar = ScalarTag::Bytes;
    std::uint64_t elementCount = 0;
    codec::ByteView value{};
};

struct QueryPredicateV1 final {
    bool propertyKeyPresent = false;
    PropertyKeyId propertyKey = 0;
    QueryOperator operation = QueryOperator::Eq;
    ScalarTag scalar = ScalarTag::Bytes;
    codec::Bytes canonicalScalar{};
};
struct QueryV1 final {
    bool rootPresent = false;
    NodeId root{};
    Axis axis = Axis::Self;
    std::uint64_t nodeKindBits = 0;
    std::uint64_t edgeKindBits = 0;
    bool firstPagePresent = false;
    std::uint64_t firstPage = 0;
    bool lastPagePresent = false;
    std::uint64_t lastPage = 0;
    std::vector<QueryPredicateV1> predicates{};
    std::uint64_t projectionBits = 0;
    std::uint8_t order = 0;
};

bool EncodeCanonicalFieldHeaderV1(
    FieldTag tag, std::uint16_t flags, ScalarTag scalar,
    std::uint64_t elementCount, std::uint64_t valueBytes,
    codec::Bytes* header) noexcept;
codec::Bytes EncodeField(FieldTag tag, std::uint16_t flags, ScalarTag scalar,
                         codec::ByteView value,
                         std::uint64_t elementCount = 1);
bool ParseFields(codec::ByteView payload, std::vector<ParsedField>* fields,
                 ErrorCode* error) noexcept;
bool EncodeFrame(const Header& header, codec::ByteView payload,
                 codec::Bytes* frame) noexcept;
bool DecodeFrame(codec::ByteView frame, Header* header,
                 codec::ByteView* payload, ErrorCode* error) noexcept;
codec::Bytes CapabilitiesFrame();
codec::Bytes ErrorFrame(ErrorCode code, std::int32_t hresult,
                        std::uint64_t requiredBytes,
                        const std::u16string& detail,
                        const Header* request = nullptr);

bool ApplyVersionToHeader(const identity::GraphVersionV1& version,
                          Header* header) noexcept;
bool HeaderMatchesVersion(const Header& header,
                          const identity::GraphVersionV1& version) noexcept;
bool DecodeQueryV1(codec::ByteView bytes, QueryV1* query,
                   ErrorCode* error) noexcept;
bool RequiredClosedProfileBits(const QueryV1& query, std::uint64_t* bits,
                               ErrorCode* error) noexcept;
bool ValidateQueryV1(const QueryV1& query, std::uint64_t closedProfileBits,
                     ErrorCode* error) noexcept;

struct FragmentHeader final {
    RecordKind recordKind = RecordKind::Manifest;
    std::uint16_t logicalRecordFlags = 0;
    FieldTag fieldTag = 0;
    std::uint16_t fieldFlags = 0;
    ScalarTag scalar = ScalarTag::Bytes;
    std::uint16_t logicalRecordFieldCount = 0;
    RecordId recordId = 0;
    std::uint64_t elementCount = 0;
    std::uint64_t totalFieldBytes = 0;
    std::uint64_t fragmentOffset = 0;
    std::uint64_t fragmentBytes = 0;
};
bool EncodeFragment(const FragmentHeader& header, codec::ByteView bytes,
                    codec::Bytes* encoded) noexcept;
bool ReassembleFragments(const std::vector<codec::Bytes>& fragments,
                         RecordId expectedRecordId,
                         codec::Bytes* logicalRecord,
                         ErrorCode* error) noexcept;

bool EncodeCanonicalRecordHeaderV1(
    RecordKind kind, std::uint16_t recordFlags, std::uint16_t fieldCount,
    RecordId recordId, std::uint64_t encodedFieldBytes,
    codec::Bytes* header) noexcept;

struct GraphStreamSha256V1 final {
    std::array<std::uint32_t, 8> state{};
    std::array<std::uint8_t, 64> block{};
    std::uint64_t totalBytes = 0;
    std::uint32_t bufferedBytes = 0;
    bool initialized = false;
};
void InitializeGraphStreamHashV1(GraphStreamSha256V1* hash) noexcept;
bool UpdateGraphStreamHashV1(GraphStreamSha256V1* hash,
                             codec::ByteView bytes) noexcept;
bool FinalizeGraphStreamHashV1(const GraphStreamSha256V1& hash,
                               Sha256* digest) noexcept;

struct GraphFragmentContinuationV1 final {
    bool active = false;
    bool terminal = false;
    RecordId currentRecordId = 0;
    RecordId nextRecordId = 0;
    RecordKind currentRecordKind = RecordKind::Manifest;
    std::uint16_t logicalRecordFlags = 0;
    std::uint16_t logicalRecordFieldCount = 0;
    std::uint16_t currentFieldOrdinal = 0;
    FieldTag currentFieldTag = 0;
    std::uint16_t currentFieldFlags = 0;
    ScalarTag currentFieldScalar = ScalarTag::Bytes;
    std::uint64_t currentElementCount = 0;
    std::uint64_t totalFieldBytes = 0;
    std::uint64_t nextFieldFragmentOffset = 0;
    std::uint64_t nextReconstructedOffset = 0;
    bool expectFirstFieldMarker = true;
    bool expectLastFieldMarker = false;
    bool expectFirstRecordMarker = true;
    bool expectLastRecordMarker = false;
    std::uint64_t nextResponseSequence = 0;
    Sha256 previousChain{};
    bool identityBound = false;
    Uuid128 boundCursor{};
    identity::SerializedGraphVersionV1 boundVersion{};
    std::uint64_t completedRecordCount = 0;
    std::uint64_t completedLogicalBytes = 0;
    GraphStreamSha256V1 streamHash{};
    codec::Bytes currentFieldValue{};
    codec::Bytes currentRecordFieldBytes{};
    codec::Bytes cachedFrame{};
};

bool AcceptGraphResponseFrame(codec::ByteView frame, std::uint64_t callerBudget,
                              GraphFragmentContinuationV1* continuation,
                              ErrorCode* error,
                              std::uint64_t* requiredBytes) noexcept;

// Process-global transport. Graph reads remain capability-gated in Todo 5;
// patch upload is active and only seals opaque bytes.
enum class CapabilityNegotiationStatus : std::uint8_t {
    Negotiated = 0, InvalidSession, UnknownRequestedBits, AlreadyOpen,
    StorageFailure,
};
CapabilityNegotiationStatus NegotiateCapabilities(
    const identity::DocumentSessionId& session, std::uint64_t requestedBits,
    Route route, std::uint64_t* negotiatedBits) noexcept;
bool ReadNegotiatedCapabilities(const identity::DocumentSessionId& session,
                                Route route, std::uint64_t* requestedBits,
                                std::uint64_t* negotiatedBits) noexcept;
codec::Bytes ProcessRequest(MessageKind expected, codec::ByteView request,
                            Route route);
codec::Bytes ProcessRequest(MessageKind expected, codec::ByteView request,
                            Route route, IDispatch* captureProvider);
codec::Bytes ProcessRequest(
    MessageKind expected, codec::ByteView request, Route route,
    IDispatch* captureProvider,
    const layout::LayoutEnvironmentPlatformV1* environmentPlatform);
void InvalidateRoute(Route route) noexcept;
void NoteRouteOwnerDestruction(Route route) noexcept;
void CleanupProcessState() noexcept;

enum class DebugLifecycleEventKind : std::uint32_t {
    NegotiationCall = 1, RegistryInsert = 2, RegistryFindHit = 3,
    RegistryFindMiss = 4, RouteOwnerDestruction = 5,
    RouteInvalidated = 6, GraphOpenHandled = 7, GraphCloseHandled = 8,
};
struct DebugLifecycleCounters final {
    std::uint64_t negotiationCalls = 0;
    std::uint64_t registryInserts = 0;
    std::uint64_t registryFindHits = 0;
    std::uint64_t registryFindMisses = 0;
    std::uint64_t registryEraseCalls = 0;
    std::uint64_t registrySessionsErased = 0;
    std::uint64_t routeOwnerDestructions = 0;
    std::uint64_t routeInvalidations = 0;
    std::uint64_t graphOpenCalls = 0;
    std::uint64_t graphCloseCalls = 0;
    std::uint64_t producerCalls = 0;
    std::uint64_t publicationCalls = 0;
    std::uint64_t cursorAllocations = 0;
    std::uint64_t uploadAllocations = 0;
};
struct DebugLifecycleEvent final {
    std::uint64_t sequence = 0;
    DebugLifecycleEventKind kind = DebugLifecycleEventKind::NegotiationCall;
    Route route{};
    identity::DocumentSessionId session{};
    std::uint64_t requestedBits = 0;
    std::uint64_t negotiatedBits = 0;
    std::uint64_t affectedSessions = 0;
};
void ResetDebugLifecycleInstrumentation() noexcept;
bool ReadDebugLifecycleInstrumentation(
    DebugLifecycleCounters* counters, DebugLifecycleEvent* events,
    std::uint32_t capacity, std::uint32_t* eventCount) noexcept;
bool DebugQueryCapabilitySession(
    const identity::DocumentSessionId& session, Route route,
    std::uint64_t* requestedBits, std::uint64_t* negotiatedBits) noexcept;
std::size_t DebugCapabilitySessionCount() noexcept;
std::size_t DebugCapabilityLookupHits() noexcept;
std::size_t DebugCapabilityLookupMisses() noexcept;
std::size_t DebugCursorCount() noexcept;
bool DebugBlobClosure(const Uuid128& cursor,
                      std::vector<query::BlobSlice>* closure) noexcept;
bool DebugReadBlob(const Uuid128& cursor, const query::BlobSlice& slice,
                   codec::Bytes* content) noexcept;
bool DebugInstallBlobCursor(
    const Uuid128& cursor, const identity::GraphVersionV1& version, Route route,
    query::QueryView view) noexcept;
bool DebugInstallGraphCursor(
    const Uuid128& cursor, const identity::GraphVersionV1& version, Route route,
    query::QueryView view, const Sha256& previousResponseChain = {}) noexcept;
void DebugFailNextBlobResponseEncoding() noexcept;
void DebugFailNextGraphResponseEncoding() noexcept;
struct FrameEnvelopeDebugCounters final {
    std::uint64_t queryFramePlans = 0;
    std::uint64_t recordReadBytes = 0;
    std::uint64_t fragmentValueBytes = 0;
    std::uint64_t fragmentTemporaryCopyBytes = 0;
    std::uint64_t streamHashStagingBytes = 0;
    std::uint64_t framePayloadCopyBytes = 0;
    std::uint64_t responseAuthenticationPasses = 0;
    std::uint64_t responseAuthenticationBytes = 0;
    std::uint64_t safeArrayCopyBytes = 0;
    std::uint64_t queryFramePlanTicks = 0;
    std::uint64_t recordReadTicks = 0;
    std::uint64_t digestTicks = 0;
    std::uint64_t frameEncodeTicks = 0;
    std::uint64_t safeArrayTicks = 0;
};
void ResetFrameEnvelopeDebugCounters() noexcept;
FrameEnvelopeDebugCounters ReadFrameEnvelopeDebugCounters() noexcept;
std::size_t DebugUploadCount() noexcept;
std::size_t DebugUploadFileCount() noexcept;
bool DebugUploadDirectoryExists() noexcept;
bool DebugReadSealed(const Uuid128& id, codec::Bytes* bytes) noexcept;

HRESULT ReadByteArrayArgument(const VARIANT& value, codec::Bytes* bytes) noexcept;
HRESULT ReturnByteArray(codec::ByteView bytes, VARIANT* result) noexcept;
HRESULT InvokeGraphMember(DISPID memberId, WORD flags, DISPPARAMS* parameters,
                          VARIANT* result, Route route,
                          IDispatch* captureProvider = nullptr) noexcept;

} // namespace hancom::graph::protocol

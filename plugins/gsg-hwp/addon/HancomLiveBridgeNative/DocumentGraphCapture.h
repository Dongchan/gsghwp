#pragma once

#include "DocumentGraphStore.h"

#include <Windows.h>

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace hancom::graph::capture {

enum class CaptureStatus : std::uint8_t {
    Complete = 0,
    InvalidArgument,
    IncompleteCapture,
    StateChanged,
    TraversalMismatch,
    RestoreFailed,
    Cancelled,
    PublishFailed,
    CleanupFailed,
};

enum AttemptMismatch : std::uint64_t {
    MismatchNone = 0,
    MismatchSemanticRoot = UINT64_C(1) << 0,
    MismatchLayoutRoot = UINT64_C(1) << 1,
    MismatchCaptureRoot = UINT64_C(1) << 2,
    MismatchSemanticCertified = UINT64_C(1) << 3,
    MismatchLayoutPresent = UINT64_C(1) << 4,
    MismatchClosedProfiles = UINT64_C(1) << 5,
    MismatchIntegrity = UINT64_C(1) << 6,
    MismatchCoverage = UINT64_C(1) << 7,
    MismatchUnavailable = UINT64_C(1) << 8,
    MismatchDiagnostics = UINT64_C(1) << 9,
    MismatchLayoutEnvironment = UINT64_C(1) << 10,
    MismatchObservedControlCount = UINT64_C(1) << 11,
    MismatchLegacyHwpmlDiagnostic = UINT64_C(1) << 12,
    MismatchFinalRecordStream = UINT64_C(1) << 13,
    MismatchIndexDigest = UINT64_C(1) << 14,
    MismatchBlobClosure = UINT64_C(1) << 15,
    MismatchNodeFingerprints = UINT64_C(1) << 16,
};

enum class FailureStage : std::uint8_t {
    None = 0,
    BeginSession,
    Baseline,
    BeginAttempt,
    Reader,
    FinishAttempt,
    ReleaseScans,
    RestoreState,
    RestoredState,
    CompareAttempts,
    PreparePublication,
    Publish,
    AbortSession,
    Exception,
};

struct CaptureDiagnostics final {
    FailureStage stage = FailureStage::None;
    size_t attempt = 0;
    size_t reader = 0;
    std::uint64_t mismatchBits = MismatchNone;
    bool firstDivergencePresent = false;
    RecordKind firstRecordKind = RecordKind::Manifest;
    NodeKind firstNodeKind = NodeKind::Document;
    NodeId firstNodeId{};
    std::uint64_t firstRecordOrdinal = 0;
    FieldTag firstField = 0;
    Sha256 firstDigest{};
    Sha256 secondDigest{};
    std::wstring failureDetail{};
};

enum class FailurePoint : std::uint8_t {
    None = 0,
    BeforeBaseline,
    AfterBaseline,
    BeforeAttempt,
    BeforeReader,
    AfterReader,
    BeforeScanRelease,
    AfterScanRelease,
    BeforeRestore,
    AfterRestore,
    BeforeComparison,
    AfterComparison,
    BeforePublish,
};

enum class QualifiedReader : std::uint8_t {
    StorySpine = 0,
    TextAtoms,
    EffectiveProperties,
    ControlAdapters,
    TableTopology,
    ImagesShapesCaptions,
    StableLayout,
    Count,
};

inline constexpr size_t kQualifiedReaderCount =
    static_cast<size_t>(QualifiedReader::Count);

struct FailureInjection final {
    FailurePoint point = FailurePoint::None;
    size_t attempt = 0;
    size_t reader = 0;
};

struct AttemptFacts final {
    std::array<codec::CanonicalizationResult, 3> canonicalPasses{};
    codec::CanonicalStreamResult finalRecords{};
    codec::CanonicalStreamResult index{};
    codec::CanonicalStreamResult blobClosure{};
    Sha256 layoutEnvironmentDigest{};
};

using EmitAttemptDiagnosticCallback = bool (*)(
    void* context, const std::filesystem::path& destination) noexcept;

struct AttemptResult final {
    store::TraversalManifest manifest{};
    AttemptFacts facts{};
    store::FileSlice records{};
    store::FileSlice index{};
    void* blobContext = nullptr;
    codec::BlobReadCallback readBlob = nullptr;
    std::vector<std::uint8_t> layoutEnvironment{};
    std::uint64_t observedControlCount = 0;
    bool legacyHwpmlDiagnosticFailed = false;
    std::vector<codec::NodeFingerprintResult> nodeFingerprints{};
    codec::CanonicalArtifactPin canonicalArtifact{};
    store::PreparedGenerationPin preparedCandidate{};
    void* diagnosticContext = nullptr;
    EmitAttemptDiagnosticCallback emitDiagnostic = nullptr;
};

std::uint64_t AttemptMismatchBits(
    const AttemptResult& first, const AttemptResult& second) noexcept;
bool FindFirstFingerprintDivergence(
    const std::vector<codec::NodeFingerprintResult>& first,
    const std::vector<codec::NodeFingerprintResult>& second,
    NodeId* node,
    size_t* examined) noexcept;

using CaptureStateCallback = bool (*)(
    void* context,
    store::CapturedState* output) noexcept;
using BeginAttemptCallback = bool (*)(
    void* context,
    size_t attempt) noexcept;
using RunReaderCallback = bool (*)(
    void* context,
    size_t attempt,
    size_t reader) noexcept;
// A detached attempt input owns every value reachable by its worker. The
// counters are enforced at the coordinator boundary and make accidental COM
// or mutable-session ownership observable in tests and production.
struct AttemptArtifactInput final {
    using BuildCallback = bool (*)(
        const std::shared_ptr<void>& owner,
        AttemptResult* output) noexcept;
    using PrepareReplayCallback = bool (*)(
        const std::shared_ptr<void>& firstOwner,
        const std::shared_ptr<void>& secondOwner) noexcept;
    using FailureDetailCallback = bool (*)(
        const std::shared_ptr<void>& owner,
        std::wstring* output) noexcept;

    std::shared_ptr<void> owner{};
    BuildCallback build = nullptr;
    PrepareReplayCallback prepareReplay = nullptr;
    FailureDetailCallback failureDetail = nullptr;
    std::uint32_t comInterfacePointers = 0;
    std::uint32_t mutableContextReferences = 0;
};

using FinishAttemptCallback = bool (*)(
    void* context,
    size_t attempt,
    AttemptResult* output) noexcept;
using DetachAttemptArtifactCallback = bool (*)(
    void* context,
    size_t attempt,
    AttemptArtifactInput* output) noexcept;
using RetainAttemptArtifactCallback = bool (*)(
    void* context,
    size_t attempt,
    const std::shared_ptr<void>& owner) noexcept;
using ReleaseScansCallback = bool (*)(void* context) noexcept;
using RestoreStateCallback = bool (*)(
    void* context,
    const store::CapturedState& baseline) noexcept;
using CancelledCallback = bool (*)(void* context) noexcept;
using PublishCallback = bool (*)(
    void* context,
    const store::PublicationInput& input) noexcept;
using BeginSessionCallback = bool (*)(
    void* context,
    std::uint64_t sessionSerial) noexcept;
using PreparePublicationCallback = bool (*)(
    void* context,
    std::uint64_t sessionSerial) noexcept;
using AbortSessionCallback = bool (*)(
    void* context,
    std::uint64_t sessionSerial) noexcept;
using CommitSessionCallback = void (*)(
    void* context,
    std::uint64_t sessionSerial) noexcept;

enum class CaptureProgressPoint : std::uint8_t {
    CaptureBegin = 0,
    CancellationCheckpoint,
    AttemptStart,
    AttemptEnd,
    ReaderStart,
    ReaderEnd,
    FinishStart,
    ReferenceClosureStart,
    ReferenceClosureEnd,
    GraphAssemblyStart,
    GraphAssemblyEnd,
    CanonicalizeStart,
    CanonicalPass1Start,
    CanonicalPass1End,
    CanonicalPass2Start,
    CanonicalPass2End,
    CanonicalPass3Start,
    CanonicalPass3End,
    CanonicalizeEnd,
    FinishEnd,
    CompareStart,
    CompareEnd,
    FirstDivergenceStart,
    FirstDivergenceEnd,
    PublishStart,
    PublishEnd,
    CoordinatorReturn,
    SpoolCleanupStart,
    SpoolCleanupEnd,
    CaptureProviderReturn,
    CaptureReturned,
    BuildQueryViewStart,
    ParseIndexStart,
    ParseIndexBlobValidationStart,
    ParseIndexBlobValidationProgress,
    ParseIndexBlobValidationEnd,
    QueryMaterializationStart,
    QueryMaterializationProgress,
    QueryMaterializationEnd,
    QueryClosureStart,
    QueryClosureEnd,
    QueryCanonicalizationStart,
    QueryCanonicalizationEnd,
    BuildQueryViewEnd,
    GraphOpenNativeReturn,
    ComReturnStart,
    ComReturnEnd,
    TypedBuildStart,
    TypedGraphPayloadValidation,
    TypedGraphCoveragePreparation,
    TypedGraphStructureFamilies,
    TypedGraphControlFamily,
    TypedGraphTableFamilies,
    TypedGraphImageFamily,
    TypedOwnerResolution,
    TypedPropertyFamilies,
    TypedLayoutFamily,
    TypedCoverageDiagnostics,
    TypedIdentityReconciliation,
    TypedRecordIdAssignment,
    TypedPayloadFinalization,
    TypedEncodingRewrite,
    TypedBuildEnd,
    TableControlCalls,
    TableRangeCalls,
    TableRangeDispatchReasons,
    TableRangeResultReasons,
    TableRangeGeometryReasons,
    TableExactCover,
    TableExactCoverReasons,
    TableDimensionSources,
    TypedDefinitionReferencesStart,
    TypedDefinitionReferencesEnd,
    TypedCoverageFactsEnd,
    TypedRunQualificationEnd,
    TypedLayoutOwnersEnd,
    ReferenceSitesByTarget,
    CellReferencePath,
    CellReferenceProofReads,
    CellReferenceFastReads,
    CellReferenceFallbackCalls,
    CellReferenceFallbackReads,
    CellReferencePositionWall,
    CellReferenceFastWall,
    CellReferenceFallbackWall,
    TableHeadCtrlAcquisition,
    TableNextTraversal,
    TableGraphProbe,
    TableCaptionInspection,
    TableReconciliationMerge,
    AttemptArtifactWorkerStart,
    AttemptArtifactWorkerJoined,
    AttemptArtifactRetained,
    GraphOpenRequestDecode,
    GraphOpenDocumentAccess,
    GraphOpenStabilization,
    GraphOpenSerialization,
    GraphOpenResponseMarshal,
};
using ProgressCallback = void (*)(
    void* context,
    CaptureProgressPoint point,
    std::uint64_t attempt,
    std::uint64_t reader) noexcept;

// Process-wide sidecar telemetry used after the coordinator callback returns.
// Values are bounded stage-specific counters, never document payloads.
void RecordCaptureProgressPoint(
    CaptureProgressPoint point,
    std::uint64_t first = 0,
    std::uint64_t second = 0) noexcept;

struct ReaderSuite final {
    size_t readerCount = 0;
    CaptureStateCallback captureState = nullptr;
    BeginAttemptCallback beginAttempt = nullptr;
    RunReaderCallback runReader = nullptr;
    FinishAttemptCallback finishAttempt = nullptr;
    ReleaseScansCallback releaseScans = nullptr;
    RestoreStateCallback restoreState = nullptr;
    CancelledCallback cancelled = nullptr;
    BeginSessionCallback beginSession = nullptr;
    PreparePublicationCallback preparePublication = nullptr;
    AbortSessionCallback abortSession = nullptr;
    CommitSessionCallback commitSession = nullptr;
    ProgressCallback progress = nullptr;
    // When both callbacks are present the coordinator uses the immutable
    // overlap path. The legacy finish callback remains for focused callers.
    DetachAttemptArtifactCallback detachAttemptArtifact = nullptr;
    RetainAttemptArtifactCallback retainAttemptArtifact = nullptr;
};

class CaptureCoordinator final {
public:
    CaptureCoordinator() noexcept = default;
    CaptureCoordinator(const CaptureCoordinator&) = delete;
    CaptureCoordinator& operator=(const CaptureCoordinator&) = delete;

    CaptureStatus Capture(
        const ReaderSuite& suite,
        void* readerContext,
        PublishCallback publish,
        void* publishContext,
        FailureInjection injection = {},
        CaptureDiagnostics* diagnostics = nullptr) noexcept;

    CaptureStatus CaptureToStore(
        const ReaderSuite& suite,
        void* readerContext,
        store::GraphStore* graphStore,
        FailureInjection injection = {},
        store::FailurePoint storeFailure = store::FailurePoint::None,
        CaptureDiagnostics* diagnostics = nullptr) noexcept;

private:
};

} // namespace hancom::graph::capture

#pragma once

#include "DocumentGraphCaptureSpool.h"
#include "DocumentGraphIdentity.h"

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace hancom::graph::capture {

class CaptureIdentityArena final {
public:
    CaptureIdentityArena() noexcept;
    explicit CaptureIdentityArena(identity::UuidSource source) noexcept;
    CaptureIdentityArena(const CaptureIdentityArena&) = delete;
    CaptureIdentityArena& operator=(const CaptureIdentityArena&) = delete;

    // Produces an attempt-local, value-only identity snapshot. It contains no
    // COM interfaces or references to capture-session state.
    std::unique_ptr<CaptureIdentityArena> CloneForLocalBuild() const noexcept;

    bool Acquire(const std::wstring& key, NodeId* id) noexcept;
    bool AcquireParagraph(
        const NodeId& owningStory,
        const NativePosition& canonicalStart,
        NodeId* id) noexcept;
    bool Seed(const std::wstring& key, const NodeId& id) noexcept;
    void ResetSession() noexcept;
    void BeginLogicalAttempt() noexcept {
        // A caller may predeclare that this traversal replays the immediately
        // preceding attempt. Preserve that request until reconciliation
        // consumes it; only the immutable encoding plan is attempt-local.
        frozenPlan_.reset();
    }
    void ReplayNextEncodingPass() noexcept { replayPass_ = true; }
    const std::shared_ptr<void>& FrozenPlan() const noexcept {
        return frozenPlan_;
    }
    void SetFrozenPlan(std::shared_ptr<void> plan) noexcept {
        frozenPlan_ = std::move(plan);
    }
    bool FinalizeReconciliation() noexcept;
    size_t RemapCount() const noexcept { return remapCount_; }
    size_t AmbiguousRemapCount() const noexcept {
        return ambiguousRemaps_.size();
    }
    const std::vector<identity::RemapEntry>& AmbiguousRemaps() const noexcept {
        return ambiguousRemaps_;
    }
    const identity::IdentityReceipt& Receipt() const noexcept {
        return receipt_;
    }
    size_t TombstoneCount() const noexcept { return tombstoneCount_; }
    const std::vector<std::wstring>& AcquiredKeys() const noexcept {
        return usedKeys_;
    }
    const std::vector<std::wstring>& SeededKeys() const noexcept {
        return seededKeys_;
    }

private:
    struct CloneTag final {};
    CaptureIdentityArena(
        CloneTag, const CaptureIdentityArena& source);

    friend class CaptionLocationQualification;
    friend class NativeCaptionLocationIssuer;

    identity::UuidSource source_{};
    std::uint64_t captionProvenance_ = 0;
    std::vector<std::pair<std::wstring, NodeId>> entries_{};
    std::vector<std::wstring> seededKeys_{};
    std::vector<std::wstring> usedKeys_{};
    size_t remapCount_ = 0;
    std::vector<identity::RemapEntry> ambiguousRemaps_{};
    size_t tombstoneCount_ = 0;
    identity::IdentityReceipt receipt_{};
    std::shared_ptr<void> frozenPlan_{};
    bool replayPass_ = false;
};

enum class TypedBuildSubstage : std::uint8_t {
    GraphConstruction = 0,
    OwnerResolution,
    CoverageDiagnostics,
    IdentityReconciliation,
    RecordIdAssignment,
    PayloadFinalization,
    EmissionRewrite,
    Count,
};

struct TypedBuildTiming final {
    std::uint64_t wallQpc = 0;
    std::uint64_t cpu100ns = 0;
};

struct TypedBuildProfile final {
    std::array<TypedBuildTiming,
               static_cast<std::size_t>(TypedBuildSubstage::Count)> stages{};
    std::uint64_t nodes = 0;
    std::uint64_t recordsBytes = 0;
};

void ResetTypedBuildProfiles() noexcept;
std::vector<TypedBuildProfile> ReadTypedBuildProfiles();
void SetTypedGraphReuseForTesting(bool enabled) noexcept;

struct RecordBlob final {
    ContentId id{};
    codec::Bytes bytes{};
};

bool IsQualifiedNativeCaptionLocation(
    const CaptureIdentityArena& capture,
    const ImageObservation& image) noexcept;

bool CaptionLocationsReadyForGraphPublication(
    const CaptureIdentityArena& capture,
    const std::vector<ImageObservation>& images) noexcept;

std::wstring CanonicalNativeSiteIdentity(
    PropertyTarget target,
    const std::wstring& ctrlId,
    std::uint64_t ordinal,
    const NativePosition& anchor,
    bool instanceIdPresent,
    const std::wstring& rawInstanceId);

using TypedRecordPlanPin = std::shared_ptr<void>;
using TypedRecordSink = bool (*)(
    void* context, codec::ByteView record) noexcept;

// Builds the immutable semantic plan once from one attempt's independently
// observed reader payloads. Encoding is deliberately separate so a caller can
// stream the rooted result without ever owning a whole capture byte vector.
bool BuildTypedRecordPlan(
    const std::vector<ReaderPayload>& payloads,
    CaptureIdentityArena& arena,
    TypedRecordPlanPin* plan,
    std::vector<RecordBlob>* blobs,
    std::wstring* failure = nullptr);

bool EmitTypedRecordPlan(
    const TypedRecordPlanPin& plan,
    const codec::CanonicalizationResult* canonical,
    const Sha256& indexDigest,
    TypedRecordSink sink,
    void* sinkContext,
    std::uint64_t* recordCount = nullptr,
    std::uint64_t* byteLength = nullptr,
    std::wstring* failure = nullptr) noexcept;

bool BuildTypedRecordStream(
    const std::vector<ReaderPayload>& payloads,
    CaptureIdentityArena& arena,
    const codec::CanonicalizationResult* canonical,
    const Sha256& indexDigest,
    codec::Bytes* records,
    std::vector<RecordBlob>* blobs,
    std::wstring* failure = nullptr);

struct CanonicalRecordPatch final {
    std::uint64_t offset = 0;
    codec::Bytes bytes{};
};

bool BuildCanonicalRecordPatches(
    codec::ByteView records,
    const codec::CanonicalizationResult& canonical,
    std::vector<CanonicalRecordPatch>* patches,
    std::wstring* failure = nullptr);

bool SeedArenaFromRecordStream(
    codec::ByteView records,
    CaptureIdentityArena* arena);

} // namespace hancom::graph::capture

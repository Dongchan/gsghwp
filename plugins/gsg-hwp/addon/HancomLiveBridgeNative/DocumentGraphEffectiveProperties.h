#pragma once

#include "DocumentGraphCaptureModel.h"
#include "DocumentGraphProperties.h"

#include <Windows.h>
#include <oaidl.h>

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace hancom::graph::properties {

enum class ReadStatus : std::uint8_t {
    Value = 0,
    NotApplicable,
    NotExposed,
    ReadFailed,
};

enum class CaptureStatus : std::uint8_t {
    Complete = 0,
    InvalidArgument,
    SourceFailed,
    SinkFailed,
    ResourceExhausted,
};

struct EffectivePropertyContext final {
    capture::PropertyTarget target = capture::PropertyTarget::Document;
    std::wstring targetIdentity{};
    bool hasDefinitionKind = false;
    DefinitionKind definitionKind = DefinitionKind::Style;
};

struct PropertyObservation final {
    PropertyKeyId key = 0;
    ScalarTag scalar = ScalarTag::Sint64;
    ReadStatus status = ReadStatus::ReadFailed;
    std::wstring canonicalValue{};
    PropertyOrigin origin = PropertyOrigin::Unavailable;
    capture::PropertyTarget target = capture::PropertyTarget::Document;
    std::wstring targetIdentity{};
    FieldTag ownerField = 0;
};

struct CatalogCoverage final {
    bool styleDefinitionsNotExposed = true;
    bool numberingDefinitionsNotExposed = true;
    bool bulletDefinitionsNotExposed = true;
    bool tabDefinitionCatalogNotExposed = true;
    bool directInheritedOriginNotExposed = true;
};

struct CaptureDiagnostics final {
    CaptureStatus status = CaptureStatus::InvalidArgument;
    std::uint64_t valueCount = 0;
    std::uint64_t notApplicableCount = 0;
    std::uint64_t notExposedCount = 0;
    std::uint64_t readFailedCount = 0;
    bool catalogCoverageEmitted = false;
};

struct ReferenceSite final {
    capture::PropertyTarget target = capture::PropertyTarget::Document;
    std::wstring identity{};
    capture::NativePosition position{};
    std::uint64_t sectionOrdinal = 0;
    // Canonical locator addresses the native site; ownerIdentity resolves the
    // emitted graph node and may be empty when the native ID is absent.
    std::wstring ownerIdentity{};
    // Exact authoritative table owner used only to qualify Cell fact reuse;
    // it is not the emitted graph owner identity.
    std::wstring tableOwnerIdentity{};
};

struct ReferencedDefinition final {
    DefinitionKind kind = DefinitionKind::Style;
    std::int64_t nativeId = 0;
    ReadStatus nativeIdStatus = ReadStatus::NotExposed;
    ReadStatus bodyStatus = ReadStatus::NotExposed;
    std::vector<PropertyObservation> properties{};
};

struct SiteReference final {
    EdgeKind edge = EdgeKind::StyleRef;
    size_t definition = 0;
    std::uint64_t ordinal = 0;
};

struct ReferenceSiteObservation final {
    std::vector<ReferencedDefinition> definitions{};
    std::vector<SiteReference> references{};
    std::vector<PropertyObservation> properties{};
    std::vector<capture::CoverageObservation> coverageFacts{};
};

struct ReferenceClosureDiagnostics final {
    CaptureStatus status = CaptureStatus::InvalidArgument;
    std::uint64_t expectedSites = 0;
    std::uint64_t visitedSites = 0;
    std::uint64_t uniqueDefinitions = 0;
    std::array<std::uint64_t, 10> sitesByTarget{};
    std::uint64_t cellSites = 0;
    std::uint64_t cellFastMode0Attempts = 0;
    std::uint64_t cellFastMode0Hits = 0;
    std::uint64_t cellFastMode0Rejections = 0;
    std::uint64_t cellAddressReads = 0;
    std::uint64_t cellModeReads = 0;
    std::uint64_t cellFastGetDefaultCalls = 0;
    std::uint64_t cellDiscardedProvisionalSets = 0;
    std::uint64_t cellQualifiedFallbackCalls = 0;
    std::uint64_t cellFallbackSetPosCalls = 0;
    std::uint64_t cellTableCellBlockCalls = 0;
    std::uint64_t cellFallbackAddressReads = 0;
    std::uint64_t cellFallbackModeReads = 0;
    std::uint64_t cellFallbackGetDefaultCalls = 0;
    std::uint64_t cellPositionWallNanoseconds = 0;
    std::uint64_t cellFastProofWallNanoseconds = 0;
    std::uint64_t cellFastReadWallNanoseconds = 0;
    std::uint64_t cellFallbackWallNanoseconds = 0;
    bool globalStyleCatalogNotExposed = false;
};

bool IsStructurallyCompleteTerminalCellBorder(
    const ReferenceSiteObservation& observation) noexcept;

void ResetEffectivePropertyVirtualSlotCache() noexcept;

class ReferenceClosureSource {
public:
    virtual ~ReferenceClosureSource() = default;
    virtual bool ReadSite(
        const ReferenceSite& site,
        ReferenceSiteObservation* observation) noexcept = 0;
};

class EffectivePropertySource {
public:
    virtual ~EffectivePropertySource() = default;
    virtual ReadStatus Read(
        const EffectivePropertyContext& context,
        const PropertyRule& rule,
        PropertyObservation* observation) noexcept = 0;
};

class EffectivePropertySink {
public:
    virtual ~EffectivePropertySink() = default;
    virtual bool Append(const PropertyObservation& observation) noexcept = 0;
    virtual bool MarkCatalogCoverage(
        const CatalogCoverage& coverage) noexcept = 0;
    virtual bool Commit() noexcept = 0;
    virtual void Abort() noexcept = 0;
};

CaptureStatus CaptureEffectiveProperties(
    const EffectivePropertyContext& context,
    EffectivePropertySource& source,
    EffectivePropertySink& sink,
    CaptureDiagnostics* diagnostics) noexcept;

ReadStatus ReadCurrentEffectiveProperty(
    IDispatch* hwp,
    const EffectivePropertyContext& context,
    const PropertyRule& rule,
    PropertyObservation* observation) noexcept;

CaptureStatus CaptureCurrentEffectiveProperties(
    IDispatch* hwp,
    const EffectivePropertyContext& context,
    EffectivePropertySink& sink,
    CaptureDiagnostics* diagnostics) noexcept;

CaptureStatus CaptureCurrentReferenceClosure(
    IDispatch* hwp,
    const std::vector<ReferenceSite>& sites,
    capture::ReaderPayload* output,
    ReferenceClosureDiagnostics* diagnostics) noexcept;

CaptureStatus CaptureReferenceClosure(
    ReferenceClosureSource& source,
    const std::vector<ReferenceSite>& sites,
    capture::ReaderPayload* output,
    ReferenceClosureDiagnostics* diagnostics) noexcept;

bool AccountCaptionReferenceTerminals(
    const capture::CaptureIdentityArena& capture,
    const std::vector<capture::ImageObservation>& images,
    capture::ReaderPayload* output,
    ReferenceClosureDiagnostics* diagnostics = nullptr) noexcept;

bool DerivePageSetupDigest(
    const capture::ReaderPayload& referenceClosure,
    Sha256* digest) noexcept;

} // namespace hancom::graph::properties

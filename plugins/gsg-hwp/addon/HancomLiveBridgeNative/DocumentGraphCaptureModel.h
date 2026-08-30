#pragma once

#include "DocumentGraphCapture.h"

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace hancom::graph::capture {

class CaptureIdentityArena;
class NativeCaptionLocationIssuer;
struct ImageObservation;

enum class ReaderOutcome : std::uint8_t {
    NotRun = 0,
    Complete,
    Inconclusive,
    Failed,
};

struct NativePosition final {
    std::int64_t list = 0;
    std::int64_t paragraph = 0;
    std::int64_t character = 0;
};

class CaptionLocationQualification final {
public:
    CaptionLocationQualification() noexcept = default;

    bool Matches(
        const CaptureIdentityArena& capture,
        const ImageObservation& image) const noexcept;

private:
    friend class NativeCaptionLocationIssuer;

    CaptionLocationQualification(
        std::uint64_t captureProvenance,
        std::wstring ctrlId,
        bool instanceIdPresent,
        std::wstring instanceId,
        std::uint64_t headCtrlOrdinal,
        NativePosition anchor,
        NativePosition captionStart);

    std::uint64_t captureProvenance_ = 0;
    std::wstring ctrlId_{};
    std::wstring instanceId_{};
    std::uint64_t headCtrlOrdinal_ = 0;
    NativePosition anchor_{};
    NativePosition captionStart_{};
    bool instanceIdPresent_ = false;
    bool issued_ = false;
};

struct ScalarObservation final {
    ObservationState state = ObservationState::NotRequested;
    std::int64_t value = 0;
};

struct TextObservation final {
    ObservationState state = ObservationState::NotRequested;
    std::wstring value{};
};

struct SectionObservation final {
    std::uint64_t ordinal = 0;
    NativePosition start{};
};

struct ControlObservation final {
    std::wstring ctrlId{};
    std::wstring instanceId{};
    std::uint64_t headCtrlOrdinal = 0;
    NativePosition anchor{};
    bool hasList = false;
    std::uint8_t adaptedKind = 0xff;
    bool unknownTypeDiagnostic = false;
    ObservationState anchorState = ObservationState::Value;
    bool instanceIdPresent = true;
};

struct RunObservation final {
    NativePosition start{};
    NativePosition end{};
    std::wstring text{};
};

struct ParagraphObservation final {
    std::uint64_t sectionOrdinal = 0;
    NativePosition start{};
    std::vector<RunObservation> runs{};
};

enum class PropertyTarget : std::uint8_t {
    Document = 0,
    Section,
    Run,
    Paragraph,
    Control,
    Table,
    Cell,
    Image,
    Definition,
    Story,
};

struct PropertyObservation final {
    PropertyTarget target = PropertyTarget::Document;
    std::wstring targetIdentity{};
    FieldTag ownerField = 0;
    PropertyKeyId key = 0;
    ScalarTag scalar = ScalarTag::Bytes;
    ObservationState state = ObservationState::NotRequested;
    PropertyOrigin origin = PropertyOrigin::Unknown;
    std::wstring textValue{};
    std::int64_t integerValue = 0;
};

struct CellObservation final {
    std::wstring address{};
    ObservationState listState = ObservationState::Value;
    std::int64_t listId = 0;
    std::uint64_t row1 = 0;
    std::uint64_t column1 = 0;
    std::uint64_t rowSpan = 1;
    std::uint64_t columnSpan = 1;
    ScalarObservation width{};
    ScalarObservation height{};
    ScalarObservation pageStart{};
    ScalarObservation pageEnd{};
    TextObservation text{};
    std::vector<ParagraphObservation> paragraphs{};
    std::vector<PropertyObservation> properties{};
};

struct TableObservation final {
    std::wstring instanceId{};
    std::uint64_t headCtrlOrdinal = 0;
    NativePosition anchor{};
    std::uint64_t rowCount = 0;
    std::uint64_t columnCount = 0;
    std::vector<CellObservation> cells{};
    std::wstring hostTableInstanceId{};
    bool hostTableInstanceIdPresent = false;
    std::wstring hostCellAddress{};
    std::vector<PropertyObservation> properties{};
    ScalarObservation captionPresent{};
    TextObservation captionText{};
    ScalarObservation captionAutomaticNumber{};
    ScalarObservation captionStyleId{};
    TextObservation captionStyleName{};
    ScalarObservation captionList{};
    NativePosition captionStart{};
    ScalarObservation captionPageStart{};
    ScalarObservation captionPageEnd{};
    ObservationState anchorState = ObservationState::Value;
    bool instanceIdPresent = true;
    bool locatorComplete = false;
};

inline constexpr size_t kImageScalarObservationCount = 50;
inline constexpr size_t kImageTextObservationCount = 3;

struct ImageAssetObservation final {
    ObservationState storageState = ObservationState::NotRequested;
    std::uint8_t storage = 0;
    ObservationState binaryState = ObservationState::NotRequested;
    std::uint64_t byteLength = 0;
    std::wstring sha256{};
};

struct ImageObservation final {
    std::wstring instanceId{};
    std::wstring ctrlId{L"$pic"};
    std::uint64_t headCtrlOrdinal = 0;
    NativePosition anchor{};
    std::array<ScalarObservation, kImageScalarObservationCount> scalar{};
    std::array<TextObservation, kImageTextObservationCount> text{};
    ImageAssetObservation asset{};
    ScalarObservation captionList{};
    NativePosition captionStart{};
    CaptionLocationQualification captionLocationQualification{};
    std::vector<PropertyObservation> properties{};
    ObservationState anchorState = ObservationState::Value;
    bool instanceIdPresent = true;
    bool locatorComplete = false;
};

struct CoverageObservation final {
    PropertyTarget target = PropertyTarget::Document;
    std::wstring targetIdentity{};
    CoverageCoordinateKind coordinate = CoverageCoordinateKind::NodeField;
    FieldTag ownerField = 0;
    std::wstring detail{};
    bool propertyKeyPresent = false;
    PropertyKeyId propertyKey = 0;
    ProfileId profile = ProfileId::Structure;
    CoverageState state = CoverageState::NotRequested;
};

struct DiagnosticObservation final {
    PropertyTarget target = PropertyTarget::Document;
    std::wstring targetIdentity{};
    DiagnosticCode code = DiagnosticCode::NativeReadFailure;
    Severity severity = Severity::Info;
    bool propertyKeyPresent = false;
    PropertyKeyId propertyKey = 0;
    std::int32_t hresult = 0;
    std::wstring detail{};
};

// Definitions are the reference closure observed at actual semantic sites,
// not a claim that an unavailable document-global catalog was enumerated.
struct DefinitionObservation final {
    DefinitionKind kind = DefinitionKind::Style;
    std::int64_t nativeId = 0;
    ObservationState nativeIdState = ObservationState::NotRequested;
    ObservationState bodyState = ObservationState::NotRequested;
    std::wstring identity{};
    Sha256 propertyDigest{};
    std::vector<PropertyObservation> properties{};
};

struct DefinitionReferenceObservation final {
    PropertyTarget source = PropertyTarget::Document;
    std::wstring sourceIdentity{};
    EdgeKind edge = EdgeKind::StyleRef;
    std::wstring definitionIdentity{};
    std::uint64_t ordinal = 0;
};

struct ReferenceTraversalObservation final {
    std::uint64_t expectedSites = 0;
    std::uint64_t visitedSites = 0;
    bool globalStyleCatalogNotExposed = true;
    bool globalNumberingCatalogNotExposed = true;
    bool globalBulletCatalogNotExposed = true;
    bool globalTabDefCatalogNotExposed = true;
};

// One payload is the structured output of exactly one qualified reader.
// Rendered capability text is never consulted by graph publication.
struct ReaderPayload final {
    QualifiedReader reader = QualifiedReader::StorySpine;
    ReaderOutcome outcome = ReaderOutcome::NotRun;
    CoverageState coverage = CoverageState::NotRequested;
    ObservationV1<std::int64_t> bodyList{};
    std::vector<SectionObservation> sections{};
    std::vector<ControlObservation> controls{};
    std::vector<ParagraphObservation> paragraphs{};
    std::vector<PropertyObservation> properties{};
    std::vector<TableObservation> tables{};
    std::vector<ImageObservation> images{};
    std::vector<PropertyObservation> layoutProperties{};
    std::vector<DefinitionObservation> definitions{};
    std::vector<DefinitionReferenceObservation> definitionReferences{};
    ReferenceTraversalObservation referenceTraversal{};
    std::vector<CoverageObservation> coverageFacts{};
    std::vector<DiagnosticObservation> diagnostics{};
    std::vector<std::uint8_t> layoutEnvironment{};
    // Derived producer certificate in Paragraph, GenericControl, Table,
    // TableCell, Image order. The requested Layout profile may publish only
    // when these counts exactly match the assembled graph.
    std::array<std::uint64_t, 5> layoutObservedKindCounts{};
    bool layoutPerKindComplete = false;
};

} // namespace hancom::graph::capture

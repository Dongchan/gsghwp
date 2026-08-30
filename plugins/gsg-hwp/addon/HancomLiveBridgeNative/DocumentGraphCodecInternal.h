#pragma once

// Private canonical-root machinery. Production callers enter through
// CanonicalizeRecordStream; these types are intentionally absent from the
// public codec header.
namespace hancom::graph::codec {
struct PathSegment final {
  NodeKind kind = NodeKind::Document;
  std::uint64_t siblingOrdinal = 0;
};
using CanonicalPath = std::vector<PathSegment>;
Bytes EncodePath(const CanonicalPath &path);

struct SemanticPropertyInput final {
  PropertyKeyId key = 0;
  PropertyOrigin origin = PropertyOrigin::Unknown;
  Bytes canonicalValue{};
};
Bytes SemanticPropertyBytes(const SemanticPropertyInput &property);
Sha256 SemanticPropertyDigest(const SemanticPropertyInput &property);
Sha256
DefinitionPropertyAggregate(std::vector<SemanticPropertyInput> properties);

inline bool DefinitionOrderLess(
    const DefinitionKind leftKind,
    const bool leftNativeIdPresent,
    const std::int64_t leftNativeId,
    const Sha256& leftAggregate,
    const DefinitionKind rightKind,
    const bool rightNativeIdPresent,
    const std::int64_t rightNativeId,
    const Sha256& rightAggregate) noexcept {
  if (leftKind != rightKind)
    return leftKind < rightKind;
  if (leftNativeIdPresent != rightNativeIdPresent)
    return leftNativeIdPresent < rightNativeIdPresent;
  if (leftNativeIdPresent && leftNativeId != rightNativeId)
    return leftNativeId < rightNativeId;
  return leftAggregate.bytes < rightAggregate.bytes;
}

struct SemanticPayloadInput final {
  FieldTag fieldTag = 0;
  ScalarTag scalar = ScalarTag::Bytes;
  Bytes transformedValue{};
};
Bytes SemanticPayloadElement(const SemanticPayloadInput &payload);
struct ReferenceInput final {
  EdgeKind edge = EdgeKind::Contains;
  NodeKind targetKind = NodeKind::Document;
  bool ordinalTarget = false;
  std::uint64_t targetOrdinal = 0;
  CanonicalPath targetPath{};
};
Bytes ReferenceDescriptor(const ReferenceInput &reference);

Sha256 DefinitionFingerprint(DefinitionKind kind,
                             const SemanticPayloadInput *nativeId,
                             const Sha256 &propertyAggregate,
                             std::vector<ReferenceInput> references);
Sha256 BinaryFingerprint(std::uint64_t byteLength, const Sha256 &contentDigest);
struct NodeFingerprintInput final {
  CanonicalPath path{};
  NodeKind kind = NodeKind::Document;
  std::uint64_t siblingOrdinal = 0;
  std::vector<SemanticPayloadInput> payloads{};
  std::vector<SemanticPropertyInput> properties{};
  std::vector<Sha256> children{};
  std::vector<ReferenceInput> references{};
};
Sha256 NodeFingerprint(NodeFingerprintInput input);
Sha256 ObservedSemanticRoot(const Sha256 &documentFingerprint);

struct CaptureObservationInput final {
  CanonicalPath ownerPath{};
  RecordKind ownerRecordKind = RecordKind::Node;
  FieldTag ownerFieldTag = 0;
  bool propertyKeyPresent = false;
  PropertyKeyId propertyKey = 0;
  ObservationState state = ObservationState::NotExposed;
  PropertyOrigin origin = PropertyOrigin::Unknown;
  std::int32_t hresult = 0;
  Bytes detailUtf16{};
  Bytes canonicalValue{};
};
struct CoverageInput final {
  bool ownerPresent = false;
  CanonicalPath ownerPath{};
  RecordKind ownerRecordKind = RecordKind::Manifest;
  FieldTag ownerFieldTag = 0;
  std::uint8_t profile = kNoProfile;
  bool propertyKeyPresent = false;
  PropertyKeyId propertyKey = 0;
  CoverageState state = CoverageState::Complete;
  Bytes detailUtf16{};
};
struct DiagnosticInput final {
  bool ownerPresent = false;
  CanonicalPath ownerPath{};
  DiagnosticCode code = DiagnosticCode::NativeReadFailure;
  Severity severity = Severity::Info;
  bool propertyKeyPresent = false;
  PropertyKeyId propertyKey = 0;
  std::int32_t hresult = 0;
  Bytes detailUtf16{};
};
Bytes CapturePropertyBytes(const CaptureObservationInput &value);
Sha256 CapturePropertyDigest(const CaptureObservationInput &value);
Bytes AvailableValueBytes(const CaptureObservationInput &value);
Bytes CoverageBytes(const CoverageInput &coverage);
Bytes UnavailableBytes(const CaptureObservationInput &unavailable);
Bytes DiagnosticBytes(const DiagnosticInput &diagnostic);

struct LayoutFactInput final {
  CanonicalPath ownerPath{};
  PropertyKeyId propertyKey = 0;
  Bytes canonicalValue{};
};
struct LayoutRootInput final {
  Sha256 observedSemanticRoot{};
  std::vector<LayoutFactInput> layoutFacts{};
  Bytes layoutEnvironment{};
};
Sha256 LayoutRoot(LayoutRootInput input);
struct CaptureRootInput final {
  std::uint64_t closedProfileBits = 0;
  CaptureIntegrity integrity = CaptureIntegrity::Complete;
  bool semanticCertified = false;
  Sha256 observedSemanticRoot{};
  bool layoutPresent = false;
  Sha256 layoutRoot{};
  std::vector<CaptureObservationInput> layoutValues{};
  std::vector<CaptureObservationInput> captureOnlyValues{};
  std::vector<CoverageInput> coverage{};
  std::vector<CaptureObservationInput> unavailable{};
  std::vector<DiagnosticInput> diagnostics{};
};
Error CaptureRoot(CaptureRootInput input, Sha256 *root) noexcept;
Sha256 CaptureRoot(CaptureRootInput input);

} // namespace hancom::graph::codec

#pragma once

#include "DocumentGraphSchema.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

namespace hancom::graph::codec {

inline constexpr std::uint16_t kCodecVersion = 1;
using Bytes = std::vector<std::uint8_t>;

struct ByteView final {
  const std::uint8_t *data = nullptr;
  std::uint64_t size = 0;
};
ByteView View(const Bytes &bytes) noexcept;

enum class Error : std::uint8_t {
  None = 0,
  Truncated,
  LengthOverflow,
  BadRecordVersion,
  BadFlags,
  DuplicateField,
  OutOfOrderField,
  UnknownRequiredField,
  ReservedNonzero,
  BadScalar,
  NonFiniteFloat,
  BadObservation,
  BadArray,
  RecordIdMismatch,
  MissingRequiredField,
  BadNestedStruct,
  BadRecordOrder,
  IllegalStreamState,
  IllegalLayoutState,
};

struct Field final {
  FieldTag tag = 0;
  std::uint16_t flags = 0;
  ScalarTag scalar = ScalarTag::Bytes;
  std::uint64_t elementCount = 1;
  Bytes value{};
};
struct LogicalRecord final {
  RecordKind kind = RecordKind::Manifest;
  std::uint16_t flags = kFieldFlagRequired;
  RecordId id = 0;
  std::vector<Field> fields{};
};

Error EncodeField(const Field &field, Bytes *output) noexcept;
Error EncodeLogicalRecord(const LogicalRecord &record, Bytes *output) noexcept;
Error ValidateLogicalRecord(ByteView encoded, const FieldTag *knownTags,
                            std::size_t knownTagCount, RecordId expectedId,
                            bool requireExactId = true) noexcept;
Error ValidateCanonicalScalar(ScalarTag scalar, ByteView value) noexcept;
Error ValidateObservation(ByteView envelope, ScalarTag valueScalar) noexcept;
enum class NestedSchema : std::uint8_t {
  Position,
  ParagraphRange,
  Size,
  Crop,
  BlobRef,
  NativeLocator,
  NumberingLevel,
  TabItem,
  ColumnWidth,
};
Error ValidateNestedStructure(NestedSchema schema, ByteView bytes) noexcept;

Bytes Sint64(std::int64_t value);
Bytes Uint64(std::uint64_t value);
Bytes Bool(bool value);
Bytes Float64(double value, Error *error = nullptr);
Bytes Utf16(const std::uint16_t *codeUnits, std::uint64_t count);
Bytes Uint32(std::uint32_t value);
Bytes Sint32(std::int32_t value);
Bytes Uint16(std::uint16_t value);
Bytes Uint8(std::uint8_t value);
Bytes Enum(std::int64_t raw, const std::uint16_t *symbol,
           std::uint64_t symbolCount);
Bytes BlobSliceValue(const ContentId &id, std::uint64_t offset,
                     std::uint64_t length, const Sha256 &digest);
Bytes BytesValue(ByteView value);
Bytes ArrayValue(ScalarTag elementScalar, std::uint16_t elementFlags,
                 const std::vector<Bytes> &elements);
Bytes Observation(ObservationState state, std::int32_t hresult,
                  const std::uint16_t *detail, std::uint64_t detailCount,
                  ByteView canonicalValue, Error *error = nullptr);

Sha256 Hash(ByteView bytes) noexcept;
Sha256 DomainHash(ByteView domain, ByteView bytes) noexcept;
template <std::size_t N>
Sha256 DomainHash(const char (&domain)[N], ByteView bytes) noexcept {
  static_assert(N != 0);
  return DomainHash({reinterpret_cast<const std::uint8_t *>(domain), N - 1},
                    bytes);
}
bool Equal(const Sha256 &left, const Sha256 &right) noexcept;

// Validates complete record grammar, contiguous IDs, and capture block order.
// The callback is invoked with DWORD-bounded requests and permits file/spool
// sources without a resident whole-graph byte vector.
using ReadCallback = bool (*)(void *context, std::uint64_t offset,
                              std::uint8_t *buffer, std::uint32_t requested,
                              std::uint32_t *actual) noexcept;
using RecordViewCallback = bool (*)(void *context, std::uint64_t offset,
                                    ByteView *record) noexcept;
using StreamLengthCallback = std::uint64_t (*)(void *context) noexcept;
using StreamCompleteCallback = bool (*)(void *context) noexcept;
struct RecordStream final {
  void *context = nullptr;
  ReadCallback read = nullptr;
  std::uint64_t length = 0;
  StreamKind kind = StreamKind::Capture;
  // Optional immutable resident source. When exact, canonicalization retains
  // bounded record views instead of allocating and rereading every record.
  ByteView resident{};
  // Optional immutable record-granular source for segmented spools. The view
  // must begin at offset and remain valid for the canonicalization call.
  RecordViewCallback viewRecord = nullptr;
  // Optional producer-backed stream. read/viewRecord block on the exact next
  // immutable record; currentLength/complete let canonical validation advance
  // concurrently with emission without authenticating provisional bytes.
  StreamLengthCallback currentLength = nullptr;
  StreamCompleteCallback complete = nullptr;
  bool incremental = false;
};
Error ValidateRecordStream(const RecordStream &stream,
                           std::uint64_t *recordCount) noexcept;

struct CanonicalBlobSlice;
using BlobReadCallback = bool (*)(void *context, const ContentId &contentId,
                                  std::uint64_t offset, std::uint8_t *buffer,
                                  std::uint32_t requested,
                                  std::uint32_t *actual) noexcept;
struct CanonicalizationInput final {
  RecordStream records{};
  void *blobContext = nullptr;
  BlobReadCallback readBlob = nullptr;
  ByteView layoutEnvironment{};
  std::vector<CanonicalBlobSlice> *blobClosure = nullptr;
  // Canonicalizer-owned scratch; callers leave this null.
  void *blobHashWorkspace = nullptr;
};
struct CanonicalStreamResult final {
  std::uint64_t itemCount = 0;
  std::uint64_t byteLength = 0;
  Sha256 digest{};
};
struct NodeFingerprintResult final {
  NodeId nodeId{};
  Sha256 fingerprint{};
};
struct CanonicalPatchDescriptor final {
  std::uint64_t valueOffset = 0;
  FieldTag fieldTag = 0;
  ScalarTag scalar = ScalarTag::Bytes;
  std::uint64_t valueBytes = 0;
  bool manifestVersion = false;
  NodeId nodeId{};
};
struct CanonicalBlobSlice final {
  ContentId contentId{};
  std::uint64_t offset = 0;
  std::uint64_t length = 0;
  Sha256 digest{};
};
struct CanonicalizationResult final {
  StreamKind streamKind = StreamKind::Capture;
  bool rootsPresent = false;
  Sha256 viewIndexDigest{};
  CanonicalStreamResult recordStream{};
  Sha256 observedSemanticRoot{};
  bool semanticCertified = false;
  bool layoutPresent = false;
  Sha256 layoutRoot{};
  Sha256 captureRoot{};
  CanonicalStreamResult coverage{};
  CanonicalStreamResult unavailable{};
  CanonicalStreamResult diagnostics{};
  std::uint64_t closedProfileBits = 0;
  CaptureIntegrity integrity = CaptureIntegrity::Complete;
  // Authoritative Common.8 values computed in the exact canonicalizer
  // domain. Publication builders use these for their sealing pass.
  std::vector<NodeFingerprintResult> nodeFingerprints{};
  // Verified Manifest.1/Common.8 locations returned by this parsed walk. Seal
  // application validates these bounded descriptors instead of rescanning the
  // full stream between fixed-point passes.
  std::vector<CanonicalPatchDescriptor> patchDescriptors{};
  // Exact source blob ranges dereferenced by the final canonical pass. Store
  // publication copies and rehashes this closure without replaying records.
  std::vector<CanonicalBlobSlice> blobClosure{};
  // True only when a tombstone/remap source must be proven against the active
  // generation. Fresh captures can skip the publication-time lineage replay.
  bool requiresActiveLineage = false;
};
Error CanonicalizeRecordStream(const CanonicalizationInput &input,
                               CanonicalizationResult *result) noexcept;

class CanonicalArtifact;
using CanonicalArtifactPin = std::shared_ptr<const CanonicalArtifact>;
Error CreateCanonicalArtifact(const CanonicalizationInput &input,
                              CanonicalArtifactPin *artifact) noexcept;
Error CreateCanonicalArtifactFromFacts(
    const RecordStream &source,
    CanonicalizationResult facts,
    CanonicalArtifactPin *artifact) noexcept;
const CanonicalizationResult *CanonicalArtifactFacts(
    const CanonicalArtifactPin &artifact) noexcept;
bool CanonicalArtifactSourceMatches(const CanonicalArtifactPin &artifact,
                                    const RecordStream &records) noexcept;
void *CanonicalArtifactSourceContext(
    const CanonicalArtifactPin &artifact) noexcept;

struct CodecDebugCounters final {
  std::uint64_t parsedRecordWalks = 0;
  std::uint64_t legacyValidatorWalks = 0;
  std::uint64_t sourceBytesRead = 0;
  std::uint64_t retainedRecordCopies = 0;
  std::uint64_t blobHashOperations = 0;
  std::uint64_t blobHashBufferBytes = 0;
  // The validated walk builds one immutable descriptor set per record. The
  // authority and semantic passes consume that set without decoding fields.
  std::uint64_t descriptorRecordBuilds = 0;
  std::uint64_t descriptorRecordReuses = 0;
  std::uint64_t descriptorFields = 0;
  std::uint64_t fieldDecodeWalks = 0;
  std::uint64_t descriptorArenaGrowths = 0;
  std::uint64_t canonicalWalkQpc = 0;
  std::uint64_t authorityPassQpc = 0;
  std::uint64_t semanticPassQpc = 0;
  std::uint64_t validatedCanonicalPostWalks = 0;
  std::uint64_t incrementalAccumulatorFinalizations = 0;
  std::uint64_t publishValidationWalks = 0;
  std::uint64_t publishCanonicalWalks = 0;
  std::uint64_t publishRecordCopyPasses = 0;
  std::uint64_t publishBlobClosureReplays = 0;
};
void ResetCodecDebugCounters() noexcept;
CodecDebugCounters ReadCodecDebugCounters() noexcept;
void SetCanonicalDescriptorReuseForTesting(bool enabled) noexcept;

} // namespace hancom::graph::codec

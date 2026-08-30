#pragma once

#include "DocumentGraphCodec.h"
#include "DocumentGraphIdentity.h"
#include "DocumentGraphStore.h"

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace hancom::graph::query {

inline constexpr std::uint64_t kMinimumByteBudget = 512;
inline constexpr std::uint64_t kFragmentMetadataBytes = 128;

enum class Status : std::uint8_t {
    Ok = 0,
    Terminal,
    Empty,
    StaleGraph,
    MalformedCursor,
    CursorClosed,
    Cancelled,
    InvalidQuery,
    BudgetTooSmall,
    DigestMismatch,
    SequenceMismatch,
    StorageFailure,
};

enum class Axis : std::uint8_t {
    Self = 0,
    Parent,
    Children,
    Descendants,
    DocumentOrder,
    References,
};

enum class PageRelation : std::uint8_t {
    Overlaps = 0,
    ContainedBy,
    Contains,
};

enum class Operator : std::uint8_t {
    Eq = 0,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
    ContainsUtf16,
};

enum Projection : std::uint64_t {
    ProjectStructure = UINT64_C(1) << 0,
    ProjectText = UINT64_C(1) << 1,
    ProjectProperties = UINT64_C(1) << 2,
    ProjectAssets = UINT64_C(1) << 3,
    ProjectReferences = UINT64_C(1) << 4,
    ProjectLayout = UINT64_C(1) << 5,
};
inline constexpr std::uint64_t kKnownProjectionBits =
    ProjectStructure | ProjectText | ProjectProperties | ProjectAssets |
    ProjectReferences | ProjectLayout;

enum IndexPath : std::uint64_t {
    IndexKind = UINT64_C(1) << 0,
    IndexParent = UINT64_C(1) << 1,
    IndexChildren = UINT64_C(1) << 2,
    IndexDescendants = UINT64_C(1) << 3,
    IndexDocumentOrder = UINT64_C(1) << 4,
    IndexReferences = UINT64_C(1) << 5,
    IndexPageSpan = UINT64_C(1) << 6,
    IndexNativeProperty = UINT64_C(1) << 7,
    IndexText = UINT64_C(1) << 8,
};

enum class ValueKind : std::uint8_t {
    Structure = 0,
    Text,
    Property,
    Asset,
    Reference,
    Layout,
};

struct PropertyPredicate final {
    PropertyKeyId key = 0;
    Operator operation = Operator::Eq;
    ScalarTag scalar = ScalarTag::Bytes;
    codec::Bytes canonicalValue{};
    bool originPresent = false;
    PropertyOrigin origin = PropertyOrigin::Unknown;
};

struct Query final {
    bool rootPresent = false;
    NodeId root{};
    Axis axis = Axis::DocumentOrder;
    std::uint64_t nodeKindBits = 0;
    std::uint64_t edgeKindBits = 0;
    bool pageRangePresent = false;
    std::uint64_t firstPage = 0;
    std::uint64_t lastPage = 0;
    PageRelation pageRelation = PageRelation::Overlaps;
    std::vector<PropertyPredicate> properties{};
    std::u16string textContains{};
    bool captionedOnly = false;
    std::uint64_t projectionBits = ProjectStructure;
};

struct GenerationKey final {
    std::array<std::uint8_t, 16> storeEpoch{};
    std::uint64_t serial = 0;
    Sha256 captureRoot{};
};

struct GenerationSnapshot final {
    std::wstring path{};
    GenerationKey key{};
    // Keeps the immutable generation directory and its authenticated files
    // pinned for every cursor chunk. Test sources provide equivalent expected
    // streams when they do not use a GraphStore lease.
    store::GenerationPin lease{};
    store::AuthenticatedGenerationPin authenticatedFiles{};
    store::CanonicalStream manifestStream{};
    store::CanonicalStream recordsStream{};
    store::CanonicalStream blobStream{};
};

class GenerationSource {
public:
    virtual ~GenerationSource() noexcept = default;
    virtual bool Pin(GenerationSnapshot* snapshot) noexcept = 0;
    virtual bool IsCurrent(const GenerationKey& key) noexcept = 0;
    virtual store::GenerationQueryIndexPin CachedQueryIndex(
        const GenerationKey&) noexcept { return {}; }
    virtual void CacheQueryIndex(
        const GenerationKey&,
        const store::GenerationQueryIndexPin&) noexcept {}
};

class GraphStoreGenerationSource final : public GenerationSource {
public:
    explicit GraphStoreGenerationSource(store::GraphStore* store) noexcept;
    bool Pin(GenerationSnapshot* snapshot) noexcept override;
    bool IsCurrent(const GenerationKey& key) noexcept override;
    store::GenerationQueryIndexPin CachedQueryIndex(
        const GenerationKey& key) noexcept override;
    void CacheQueryIndex(
        const GenerationKey& key,
        const store::GenerationQueryIndexPin& index) noexcept override;

private:
    store::GraphStore* store_ = nullptr;
    SRWLOCK queryIndexLock_ = SRWLOCK_INIT;
    GenerationKey queryIndexKey_{};
    store::GenerationQueryIndexPin queryIndex_{};
};

struct Fragment final {
    NodeId node{};
    NodeKind nodeKind = NodeKind::Document;
    ValueKind valueKind = ValueKind::Structure;
    FieldTag field = 0;
    PropertyKeyId propertyKey = 0;
    ObservationState observation = ObservationState::Value;
    std::uint64_t valueOffset = 0;
    std::uint64_t valueTotal = 0;
    codec::Bytes bytes{};
};

struct Chunk final {
    Status status = Status::StorageFailure;
    Uuid128 cursor{};
    GenerationKey generation{};
    std::uint64_t sequence = 0;
    std::uint64_t matchedNodes = 0;
    std::uint64_t chargedBytes = 0;
    std::uint64_t indexPathBits = 0;
    std::uint64_t axisCandidateNodes = 0;
    std::uint64_t evaluatedNodes = 0;
    bool terminal = false;
    Sha256 previousChain{};
    Sha256 chunkDigest{};
    Sha256 chainDigest{};
    std::vector<Fragment> fragments{};
};

struct NextRequest final {
    Uuid128 cursor{};
    GenerationKey generation{};
    std::uint64_t sequence = 0;
    std::uint64_t byteBudget = 0;
    Sha256 previousChain{};
};

// Immutable, decoder-ready HGN1 projection of one Todo14 query selection.
// The adapter uses the same indexes and NodeMatches pipeline as DocumentGraphQuery;
// it never publishes another GraphStore generation.
struct BlobSlice final {
    ContentId id{};
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
    Sha256 digest{};
};

class QueryViewBlobReader {
public:
    virtual ~QueryViewBlobReader() noexcept = default;
    virtual bool Read(const BlobSlice& slice, std::uint64_t relativeOffset,
                      std::uint8_t* output,
                      std::uint64_t length) const noexcept = 0;
};

class RangeBackedQueryPlan;

struct QueryView final {
    GenerationKey generation{};
    Sha256 queryDigest{};
    Sha256 viewIndexDigest{};
    // The eager byte vector remains the differential oracle for native tests.
    // Production protocol cursors use rangePlan and never populate records.
    codec::Bytes records{};
    codec::CanonicalizationResult canonical{};
    GenerationSnapshot snapshot{};
    std::vector<BlobSlice> blobClosure{};
    std::shared_ptr<const QueryViewBlobReader> blobReader{};
    std::shared_ptr<const RangeBackedQueryPlan> rangePlan{};
    std::uint64_t logicalBytes = 0;
    std::uint64_t logicalRecords = 0;
};

bool BuildPreparedGenerationQueryIndex(
    HANDLE records, const store::CanonicalStream& recordsStream,
    HANDLE blobs, const store::CanonicalStream& blobStream,
    store::GenerationQueryIndexPin* index) noexcept;
Status PrepareGenerationQueryIndex(GenerationSource* source) noexcept;
Status BuildQueryPlan(
    GenerationSource* source, const Query& query, QueryView* view,
    const identity::DocumentSessionId* session = nullptr) noexcept;
bool MaterializeQueryPlanRecord(const QueryView& view,
                                std::uint64_t recordOrdinal,
                                codec::Bytes* record) noexcept;

bool BlobSliceEqual(const BlobSlice& left, const BlobSlice& right) noexcept;
bool ReadQueryViewBlob(const QueryView& view, const BlobSlice& slice,
                       std::uint64_t relativeOffset, std::uint8_t* output,
                       std::uint64_t length) noexcept;

Status BuildQueryView(
    GenerationSource* source, const Query& query, QueryView* view,
    const identity::DocumentSessionId* session = nullptr) noexcept;

class DocumentGraphQuery final {
public:
    explicit DocumentGraphQuery(GenerationSource* source) noexcept;
    ~DocumentGraphQuery() noexcept;
    DocumentGraphQuery(const DocumentGraphQuery&) = delete;
    DocumentGraphQuery& operator=(const DocumentGraphQuery&) = delete;

    Status Open(const Query& query, std::uint64_t byteBudget,
                Chunk* chunk) noexcept;
    Status Next(const NextRequest& request, Chunk* chunk) noexcept;
    Status Cancel(const Uuid128& cursor) noexcept;
    Status Close(const Uuid128& cursor) noexcept;
    std::size_t CursorCount() const noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> impl_{};
};

bool Equal(const GenerationKey& left, const GenerationKey& right) noexcept;

struct QueryDebugCounters final {
    std::uint64_t blobEntryProbes = 0;
    std::uint64_t hashOperations = 0;
    std::uint64_t hashedBytes = 0;
    std::uint64_t sliceValidations = 0;
    std::uint64_t recordTraversals = 0;
};
void ResetQueryDebugCounters() noexcept;
QueryDebugCounters ReadQueryDebugCounters() noexcept;

} // namespace hancom::graph::query

#include "DocumentGraphQuery.h"

#include "DocumentGraphCapture.h"
#include "DocumentGraphIdentity.h"
#include "DocumentGraphProperties.h"

#include <Windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <cstring>
#include <iterator>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <tuple>
#include <utility>

namespace hancom::graph::query {
namespace {

using codec::ByteView;
using codec::Bytes;

bool gDebugCountersEnabled = false;
QueryDebugCounters gDebugCounters{};

std::uint16_t Read16(const std::uint8_t* p) noexcept {
    return static_cast<std::uint16_t>(p[0] |
        (static_cast<std::uint16_t>(p[1]) << 8));
}
std::uint32_t Read32(const std::uint8_t* p) noexcept {
    std::uint32_t value = 0;
    for (unsigned index = 0; index != 4; ++index) {
        value |= static_cast<std::uint32_t>(p[index]) << (index * 8);
    }
    return value;
}
std::uint64_t Read64(const std::uint8_t* p) noexcept {
    std::uint64_t value = 0;
    for (unsigned index = 0; index != 8; ++index) {
        value |= static_cast<std::uint64_t>(p[index]) << (index * 8);
    }
    return value;
}
void Append64(Bytes* bytes, const std::uint64_t value) {
    for (unsigned index = 0; index != 8; ++index) {
        bytes->push_back(static_cast<std::uint8_t>(value >> (index * 8)));
    }
}
void Append16(Bytes* bytes, const std::uint16_t value) {
    bytes->push_back(static_cast<std::uint8_t>(value));
    bytes->push_back(static_cast<std::uint8_t>(value >> 8));
}
void Append(Bytes* bytes, const void* value, const std::size_t count) {
    const auto* first = static_cast<const std::uint8_t*>(value);
    bytes->insert(bytes->end(), first, first + count);
}

bool Zero(const Uuid128& id) noexcept {
    return std::all_of(id.bytes.begin(), id.bytes.end(),
                       [](const std::uint8_t value) { return value == 0; });
}

bool ReadWholeHandle(const HANDLE file, Bytes* bytes) noexcept {
    if (file == INVALID_HANDLE_VALUE || bytes == nullptr) return false;
    LARGE_INTEGER size{};
    bool ok = GetFileSizeEx(file, &size) != FALSE && size.QuadPart >= 0 &&
        static_cast<unsigned long long>(size.QuadPart) <=
            static_cast<unsigned long long>(
                (std::numeric_limits<std::size_t>::max)());
    if (ok) {
        try {
            bytes->resize(static_cast<std::size_t>(size.QuadPart));
        } catch (...) {
            ok = false;
        }
    }
    LARGE_INTEGER beginning{};
    ok = ok && SetFilePointerEx(file, beginning, nullptr, FILE_BEGIN) != FALSE;
    std::size_t offset = 0;
    while (ok && offset != bytes->size()) {
        const DWORD wanted = static_cast<DWORD>((std::min<std::size_t>)(
            bytes->size() - offset, MAXDWORD));
        DWORD actual = 0;
        ok = ReadFile(file, bytes->data() + offset, wanted, &actual, nullptr) !=
                 FALSE && actual == wanted;
        offset += ok ? actual : 0;
    }
    if (!ok) bytes->clear();
    return ok;
}

bool StreamMatches(const HANDLE file,
                   const store::CanonicalStream& expected) noexcept {
    LARGE_INTEGER size{};
    Sha256 digest{};
    return file != INVALID_HANDLE_VALUE &&
        GetFileSizeEx(file, &size) != FALSE && size.QuadPart >= 0 &&
        static_cast<std::uint64_t>(size.QuadPart) == expected.length &&
        store::HashFileRangeChunked(file, 0, expected.length, &digest) &&
        codec::Equal(digest, expected.digest);
}

struct Slice final {
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
    std::uint16_t flags = 0;
    ScalarTag scalar = ScalarTag::Bytes;
};
using FieldMap = std::map<FieldTag, Slice>;

bool ParseFields(const Bytes& bytes, const std::uint64_t begin,
                 const std::uint64_t length, FieldMap* fields) {
    if (fields == nullptr || begin > bytes.size() ||
        length > bytes.size() - begin) {
        return false;
    }
    fields->clear();
    std::uint64_t at = begin;
    const std::uint64_t end = begin + length;
    while (at != end) {
        if (end - at < kFieldHeaderBytesV1) return false;
        const FieldTag tag = Read16(bytes.data() + at);
        const std::uint16_t flags = Read16(bytes.data() + at + 2);
        const auto scalar = static_cast<ScalarTag>(
            Read16(bytes.data() + at + 4));
        const std::uint64_t valueBytes = Read64(bytes.data() + at + 16);
        if (Read16(bytes.data() + at + 6) != 0 ||
            valueBytes > end - at - kFieldHeaderBytesV1 ||
            !fields->emplace(tag, Slice{at + kFieldHeaderBytesV1,
                                        valueBytes, flags, scalar}).second) {
            return false;
        }
        at += kFieldHeaderBytesV1 + valueBytes;
    }
    return true;
}

const Slice* Find(const FieldMap& fields, const FieldTag tag) noexcept {
    const auto found = fields.find(tag);
    return found == fields.end() ? nullptr : &found->second;
}

bool ObservationValue(const Bytes& records, const Slice& observation,
                      ObservationState* state, Slice* value) noexcept {
    if (state == nullptr || value == nullptr || observation.length < 24 ||
        observation.offset > records.size() ||
        observation.length > records.size() - observation.offset) {
        return false;
    }
    const std::uint8_t* p = records.data() + observation.offset;
    const auto parsedState = static_cast<ObservationState>(p[0]);
    const std::uint64_t detailUnits = Read64(p + 8);
    const std::uint64_t valueBytes = Read64(p + 16);
    if (detailUnits > (std::numeric_limits<std::uint64_t>::max)() / 2 ||
        24 + detailUnits * 2 > observation.length ||
        valueBytes != observation.length - 24 - detailUnits * 2 ||
        (parsedState == ObservationState::Value) != (p[1] != 0)) {
        return false;
    }
    *state = parsedState;
    *value = {observation.offset + 24 + detailUnits * 2, valueBytes, 0,
              observation.scalar};
    return true;
}

bool DecodeBlobSlice(const Bytes& records, const Slice& value,
                     BlobSlice* blob) noexcept {
    if (blob == nullptr || value.length != 64 ||
        value.offset > records.size() ||
        value.length > records.size() - value.offset) {
        return false;
    }
    const std::uint8_t* p = records.data() + value.offset;
    std::copy_n(p, 16, blob->id.bytes.begin());
    blob->offset = Read64(p + 16);
    blob->length = Read64(p + 24);
    std::copy_n(p + 32, 32, blob->digest.bytes.begin());
    return blob->offset <=
        (std::numeric_limits<std::uint64_t>::max)() - blob->length;
}

struct Property final {
    PropertyKeyId key = 0;
    FieldTag ownerField = 0;
    ScalarTag scalar = ScalarTag::Bytes;
    PropertyOrigin origin = PropertyOrigin::Unknown;
    ObservationState state = ObservationState::NotRequested;
    Slice value{};
    Bytes canonicalValue{};
};

struct Edge final {
    EdgeKind kind = EdgeKind::Contains;
    std::size_t source = 0;
    std::size_t target = 0;
    std::uint64_t ordinal = 0;
};

struct Node final {
    NodeId id{};
    NodeKind kind = NodeKind::Document;
    bool parentPresent = false;
    std::size_t parent = 0;
    std::uint64_t sibling = 0;
    std::size_t order = 0;
    std::size_t subtreeEnd = 0;
    std::vector<std::size_t> children{};
    std::vector<std::size_t> outgoing{};
    std::vector<Property> properties{};
    bool pageStartPresent = false;
    bool pageEndPresent = false;
    std::uint64_t pageStart = 0;
    std::uint64_t pageEnd = 0;
    bool captioned = false;
    bool textPresent = false;
    BlobSlice text{};
    bool assetPresent = false;
    BlobSlice asset{};
};

struct BlobEntry final {
    ContentId id{};
    std::uint64_t logicalOffset = 0;
    std::uint64_t fileOffset = 0;
    std::uint32_t length = 0;
};

struct BlobSliceKey final {
    std::array<std::uint8_t, 16> id{};
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
    std::array<std::uint8_t, 32> digest{};
    bool operator<(const BlobSliceKey& other) const noexcept {
        return std::tie(id, offset, length, digest) <
            std::tie(other.id, other.offset, other.length, other.digest);
    }
};

struct AuthenticatedBlob final {
    std::uint64_t length = 0;
    std::array<std::uint8_t, 32> digest{};
    std::shared_ptr<const Bytes> bytes{};
};

struct PageSpanIndexEntry final {
    std::uint64_t first = 0;
    std::uint64_t last = 0;
    std::size_t node = 0;
};

struct IndexedRecord final {
    RecordKind kind = RecordKind::Manifest;
    Slice range{};
    std::size_t node = (std::numeric_limits<std::size_t>::max)();
    bool edgeTargetPresent = false;
    std::array<std::uint8_t, 16> edgeTarget{};
    bool closureBlobPresent = false;
    BlobSlice closureBlob{};
};

struct Index final : public store::GenerationQueryIndex {
    ~Index() noexcept {
        if (recordsView != nullptr) UnmapViewOfFile(recordsView);
        if (recordsMapping != nullptr) CloseHandle(recordsMapping);
        if (hashAlgorithm != nullptr)
            BCryptCloseAlgorithmProvider(hashAlgorithm, 0);
        if (ownsHandles) {
            if (manifestFile != INVALID_HANDLE_VALUE) CloseHandle(manifestFile);
            if (recordsFile != INVALID_HANDLE_VALUE) CloseHandle(recordsFile);
            if (blobFile != INVALID_HANDLE_VALUE) CloseHandle(blobFile);
        }
    }
    GenerationSnapshot generation{};
    HANDLE manifestFile = INVALID_HANDLE_VALUE;
    HANDLE recordsFile = INVALID_HANDLE_VALUE;
    HANDLE blobFile = INVALID_HANDLE_VALUE;
    bool ownsHandles = false;
    HANDLE recordsMapping = nullptr;
    mutable SRWLOCK recordsViewLock = SRWLOCK_INIT;
    mutable const std::uint8_t* recordsView = nullptr;
    mutable std::uint64_t recordsViewOffset = 0;
    mutable std::uint64_t recordsViewBytes = 0;
    std::uint64_t recordsFileBytes = 0;
    std::vector<Node> nodes{};
    std::vector<Edge> edges{};
    Slice manifestRecord{};
    std::vector<std::vector<std::size_t>> nodeRecords{};
    std::vector<IndexedRecord> records{};
    std::map<std::array<std::uint8_t, 16>, std::size_t> byId{};
    std::vector<std::size_t> documentOrder{};
    std::array<std::vector<std::size_t>, 13> byKind{};
    std::map<PropertyKeyId, std::vector<std::size_t>> byProperty{};
    std::map<std::pair<PropertyKeyId, PropertyOrigin>,
             std::vector<std::size_t>> byNativeProperty{};
    std::vector<PageSpanIndexEntry> byPageSpan{};
    std::vector<std::size_t> byText{};
    std::vector<BlobEntry> blobs{};
    std::map<std::array<std::uint8_t, 16>, std::vector<BlobEntry>>
        blobRanges{};
    std::map<BlobSliceKey, std::shared_ptr<const Bytes>> authenticated{};
    std::map<std::array<std::uint8_t, 16>,
             std::map<std::uint64_t, std::vector<AuthenticatedBlob>>>
        authenticatedRanges{};
    BCRYPT_ALG_HANDLE hashAlgorithm = nullptr;
    std::vector<std::uint8_t> hashObject{};
};

bool ReadBlobRange(const Index& index, const ContentId& id,
                   const std::uint64_t offset, std::uint8_t* output,
                   const std::uint64_t length) noexcept {
    if ((length != 0 && output == nullptr) ||
        offset > (std::numeric_limits<std::uint64_t>::max)() - length) {
        return false;
    }
    if (index.blobFile == INVALID_HANDLE_VALUE) return false;
    const auto content = index.blobRanges.find(id.bytes);
    if (content == index.blobRanges.end()) return length == 0;
    const std::vector<BlobEntry>& ranges = content->second;
    std::uint64_t copied = 0;
    while (copied != length) {
        const std::uint64_t logical = offset + copied;
        std::size_t first = 0;
        std::size_t last = ranges.size();
        while (first != last) {
            if (gDebugCountersEnabled) ++gDebugCounters.blobEntryProbes;
            const std::size_t middle = first + (last - first) / 2;
            if (ranges[middle].logicalOffset <= logical)
                first = middle + 1;
            else
                last = middle;
        }
        if (first == 0) return false;
        const BlobEntry* const matched = &ranges[first - 1];
        if (matched->logicalOffset >
                (std::numeric_limits<std::uint64_t>::max)() - matched->length ||
            logical < matched->logicalOffset ||
            logical >= matched->logicalOffset + matched->length ||
            matched->fileOffset >
                (std::numeric_limits<std::uint64_t>::max)() -
                    (logical - matched->logicalOffset)) {
            return false;
        }
        const std::uint64_t available = matched->length -
            (logical - matched->logicalOffset);
        const DWORD wanted = static_cast<DWORD>((std::min)(
            length - copied, available));
        LARGE_INTEGER position{};
        position.QuadPart = static_cast<LONGLONG>(
            matched->fileOffset + logical - matched->logicalOffset);
        DWORD actual = 0;
        if (SetFilePointerEx(index.blobFile, position, nullptr, FILE_BEGIN) ==
                FALSE ||
            ReadFile(index.blobFile, output + copied, wanted, &actual, nullptr) ==
                FALSE || actual != wanted) {
            return false;
        }
        copied += actual;
    }
    return true;
}

BlobSliceKey Key(const BlobSlice& slice) noexcept {
    return {slice.id.bytes, slice.offset, slice.length, slice.digest.bytes};
}

bool ReadAuthenticatedBlob(const Index& index, const ContentId& id,
                           const std::uint64_t offset,
                           std::uint8_t* const output,
                           const std::uint64_t length) noexcept {
    if ((length != 0 && output == nullptr) ||
        offset > (std::numeric_limits<std::uint64_t>::max)() - length)
        return false;
    const auto content = index.authenticatedRanges.find(id.bytes);
    if (content == index.authenticatedRanges.end()) return false;
    auto range = content->second.upper_bound(offset);
    if (range == content->second.begin()) return false;
    --range;
    for (const AuthenticatedBlob& candidate : range->second) {
        if (offset < range->first || candidate.length < offset - range->first ||
            length > candidate.length - (offset - range->first))
            continue;
        if (length != 0)
            std::memcpy(output,
                        candidate.bytes->data() + (offset - range->first),
                        static_cast<std::size_t>(length));
        return true;
    }
    return false;
}

bool ValidateBlobSlice(Index& index, const BlobSlice& slice) noexcept {
    if (gDebugCountersEnabled) ++gDebugCounters.sliceValidations;
    const BlobSliceKey key = Key(slice);
    if (index.authenticated.find(key) != index.authenticated.end()) return true;
    if (slice.offset > (std::numeric_limits<std::uint64_t>::max)() -
                           slice.length ||
        slice.length > (std::numeric_limits<std::size_t>::max)())
        return false;
    if (gDebugCountersEnabled) {
        ++gDebugCounters.hashOperations;
        gDebugCounters.hashedBytes += slice.length;
    }
    try {
        auto bytes = std::make_shared<Bytes>(
            static_cast<std::size_t>(slice.length));
        if (!ReadBlobRange(index, slice.id, slice.offset, bytes->data(),
                           slice.length))
            return false;
        DWORD objectBytes = 0;
        DWORD returned = 0;
        if (index.hashAlgorithm == nullptr &&
            BCryptOpenAlgorithmProvider(&index.hashAlgorithm,
                                        BCRYPT_SHA256_ALGORITHM,
                                        nullptr, 0) < 0)
            return false;
        if (index.hashObject.empty()) {
            if (BCryptGetProperty(
                    index.hashAlgorithm, BCRYPT_OBJECT_LENGTH,
                    reinterpret_cast<PUCHAR>(&objectBytes),
                    sizeof(objectBytes), &returned, 0) < 0 ||
                objectBytes == 0)
                return false;
            index.hashObject.resize(objectBytes);
        }
        BCRYPT_HASH_HANDLE hash = nullptr;
        bool ok = BCryptCreateHash(
            index.hashAlgorithm, &hash, index.hashObject.data(),
            static_cast<ULONG>(index.hashObject.size()), nullptr, 0, 0) >= 0;
        std::uint64_t at = 0;
        while (ok && at != bytes->size()) {
            const ULONG count = static_cast<ULONG>((std::min<std::uint64_t>)(
                bytes->size() - at, ULONG_MAX));
            ok = BCryptHashData(hash, bytes->data() + at, count, 0) >= 0;
            at += ok ? count : 0;
        }
        Sha256 actual{};
        ok = ok && BCryptFinishHash(
            hash, actual.bytes.data(), static_cast<ULONG>(actual.bytes.size()),
            0) >= 0 && codec::Equal(actual, slice.digest);
        if (hash != nullptr) BCryptDestroyHash(hash);
        if (!ok) return false;
        index.authenticated.emplace(key, bytes);
        index.authenticatedRanges[slice.id.bytes][slice.offset].push_back(
            {slice.length, slice.digest.bytes, std::move(bytes)});
        return true;
    } catch (...) {
        return false;
    }
}

class IndexBlobReader final : public QueryViewBlobReader {
public:
    explicit IndexBlobReader(std::shared_ptr<const Index> index) noexcept
        : index_(std::move(index)) {}

    bool Read(const BlobSlice& slice, const std::uint64_t relativeOffset,
              std::uint8_t* const output,
              const std::uint64_t length) const noexcept override {
        if (relativeOffset > slice.length ||
            length > slice.length - relativeOffset ||
            slice.offset > (std::numeric_limits<std::uint64_t>::max)() -
                relativeOffset) {
            return false;
        }
        const auto authenticated = index_->authenticated.find(Key(slice));
        if (authenticated == index_->authenticated.end()) return false;
        if (length != 0)
            std::memcpy(output,
                        authenticated->second->data() + relativeOffset,
                        static_cast<std::size_t>(length));
        return true;
    }

private:
    std::shared_ptr<const Index> index_{};
};

bool CanonicalBlobRead(void* const context, const ContentId& contentId,
                       const std::uint64_t offset, std::uint8_t* const buffer,
                       const std::uint32_t requested,
                       std::uint32_t* const actual) noexcept {
    const auto* const index = static_cast<const Index*>(context);
    if (index == nullptr || actual == nullptr ||
        !ReadAuthenticatedBlob(*index, contentId, offset, buffer, requested)) {
        return false;
    }
    *actual = requested;
    return true;
}

bool ParseBlobIndex(
    const HANDLE file, std::vector<BlobEntry>* entries,
    std::map<std::array<std::uint8_t, 16>, std::vector<BlobEntry>>* ranges)
    noexcept {
    Bytes bytes;
    if (entries == nullptr || ranges == nullptr ||
        !ReadWholeHandle(file, &bytes)) return false;
    entries->clear();
    ranges->clear();
    std::map<std::tuple<std::array<std::uint8_t, 16>, std::uint64_t,
                        std::uint32_t>, std::size_t> unique;
    std::uint64_t at = 0;
    while (at != bytes.size()) {
        if (bytes.size() - at < 28) return false;
        BlobEntry entry{};
        std::copy_n(bytes.data() + at, 16, entry.id.bytes.begin());
        entry.logicalOffset = Read64(bytes.data() + at + 16);
        entry.length = Read32(bytes.data() + at + 24);
        entry.fileOffset = at + 28;
        if (entry.length > bytes.size() - entry.fileOffset) return false;
        const auto key = std::make_tuple(
            entry.id.bytes, entry.logicalOffset, entry.length);
        const auto duplicate = unique.find(key);
        if (duplicate != unique.end()) {
            const BlobEntry& prior = entries->at(duplicate->second);
            if (!std::equal(
                    bytes.begin() + static_cast<std::size_t>(prior.fileOffset),
                    bytes.begin() + static_cast<std::size_t>(
                        prior.fileOffset + prior.length),
                    bytes.begin() + static_cast<std::size_t>(entry.fileOffset)))
                return false;
        } else {
            unique.emplace(key, entries->size());
            entries->push_back(entry);
            (*ranges)[entry.id.bytes].push_back(entry);
        }
        at = entry.fileOffset + entry.length;
    }
    for (auto& content : *ranges) {
        std::vector<BlobEntry>& contentRanges = content.second;
        std::sort(contentRanges.begin(), contentRanges.end(),
                  [](const BlobEntry& left, const BlobEntry& right) {
                      if (left.logicalOffset != right.logicalOffset)
                          return left.logicalOffset < right.logicalOffset;
                      return left.length < right.length;
                  });
        std::uint64_t previousEnd = 0;
        bool first = true;
        for (const BlobEntry& entry : contentRanges) {
            if (entry.logicalOffset >
                    (std::numeric_limits<std::uint64_t>::max)() - entry.length ||
                (!first && entry.logicalOffset < previousEnd))
                return false;
            previousEnd = entry.logicalOffset + entry.length;
            first = false;
        }
    }
    return true;
}

bool ParseIndex(const GenerationSnapshot& generation,
                std::shared_ptr<Index>* result) {
    if (result == nullptr || generation.path.empty() ||
        generation.key.serial == 0) {
        return false;
    }
    auto index = std::make_shared<Index>();
    index->generation = generation;
    if (generation.authenticatedFiles != nullptr) {
        index->manifestFile = generation.authenticatedFiles->manifest;
        index->recordsFile = generation.authenticatedFiles->records;
        index->blobFile = generation.authenticatedFiles->blobs;
    } else {
        index->ownsHandles = true;
        index->manifestFile = CreateFileW(
            (generation.path + L"\\manifest.hgm").c_str(), GENERIC_READ,
            FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
            nullptr);
        index->recordsFile = CreateFileW(
            (generation.path + L"\\records.hgn").c_str(), GENERIC_READ,
            FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
            nullptr);
        index->blobFile = CreateFileW(
            (generation.path + L"\\blobs.hgb").c_str(), GENERIC_READ,
            FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
            nullptr);
    }
    if ((generation.manifestStream.length != 0 &&
         !StreamMatches(index->manifestFile, generation.manifestStream)) ||
        !StreamMatches(index->recordsFile, generation.recordsStream) ||
        !StreamMatches(index->blobFile, generation.blobStream)) {
        return false;
    }
    Bytes records;
    if (!ReadWholeHandle(index->recordsFile, &records) ||
        !ParseBlobIndex(index->blobFile, &index->blobs,
                        &index->blobRanges)) {
        return false;
    }
    struct PendingParent final {
        std::size_t node = 0;
        NodeId parent{};
    };
    struct PendingEdge final {
        EdgeKind kind = EdgeKind::Contains;
        NodeId source{};
        NodeId target{};
        std::uint64_t ordinal = 0;
    };
    struct PendingProperty final {
        NodeId owner{};
        Property property{};
    };
    std::vector<PendingParent> parents;
    std::vector<PendingEdge> edges;
    std::vector<PendingProperty> properties;
    std::uint64_t at = 0;
    std::size_t currentNode = (std::numeric_limits<std::size_t>::max)();
    while (at != records.size()) {
        if (gDebugCountersEnabled) ++gDebugCounters.recordTraversals;
        if (records.size() - at < kLogicalRecordHeaderBytesV1) return false;
        const auto kind = static_cast<RecordKind>(Read16(records.data() + at));
        const std::uint64_t payloadBytes = Read64(records.data() + at + 16);
        if (Read16(records.data() + at + 2) != kRecordVersionV1 ||
            payloadBytes > records.size() - at - kLogicalRecordHeaderBytesV1) {
            return false;
        }
        FieldMap fields;
        if (!ParseFields(records, at + kLogicalRecordHeaderBytesV1,
                         payloadBytes, &fields)) {
            return false;
        }
        if (kind == RecordKind::Node) {
            const Slice* commonSlice = Find(fields, 1);
            const Slice* payloadSlice = Find(fields, 2);
            FieldMap common;
            FieldMap payload;
            if (commonSlice == nullptr || payloadSlice == nullptr ||
                !ParseFields(records, commonSlice->offset,
                             commonSlice->length, &common) ||
                !ParseFields(records, payloadSlice->offset,
                             payloadSlice->length, &payload)) {
                return false;
            }
            const Slice* id = Find(common, 1);
            const Slice* nodeKind = Find(common, 2);
            const Slice* sibling = Find(common, 4);
            if (id == nullptr || id->length != 16 || nodeKind == nullptr ||
                nodeKind->length != 2 || sibling == nullptr ||
                sibling->length != 8) {
                return false;
            }
            Node node{};
            std::copy_n(records.data() + id->offset, 16,
                        node.id.bytes.begin());
            node.kind = static_cast<NodeKind>(
                Read16(records.data() + nodeKind->offset));
            if (static_cast<std::size_t>(node.kind) >= index->byKind.size() ||
                !identity::IsRfc4122V4(node.id)) {
                return false;
            }
            node.sibling = Read64(records.data() + sibling->offset);
            node.order = index->nodes.size();
            node.subtreeEnd = node.order + 1;
            const Slice* parent = Find(common, 3);
            if (parent != nullptr) {
                if (parent->length != 16) return false;
                PendingParent pending{};
                pending.node = node.order;
                std::copy_n(records.data() + parent->offset, 16,
                            pending.parent.bytes.begin());
                parents.push_back(pending);
            }
            if (node.kind == NodeKind::CharacterRun) {
                const Slice* textField = Find(payload, 101);
                Slice textValue{};
                ObservationState state{};
                if (textField != nullptr &&
                    ObservationValue(records, *textField, &state, &textValue) &&
                    state == ObservationState::Value) {
                    node.textPresent = DecodeBlobSlice(
                        records, textValue, &node.text);
                    if (!node.textPresent) return false;
                }
            } else if (node.kind == NodeKind::BinaryData) {
                const Slice* content = Find(payload, 100);
                const Slice* length = Find(payload, 101);
                const Slice* digest = Find(payload, 102);
                if (content != nullptr && content->length == 16 &&
                    length != nullptr && length->length == 8 &&
                    digest != nullptr && digest->length == 32) {
                    node.assetPresent = true;
                    std::copy_n(records.data() + content->offset, 16,
                                node.asset.id.bytes.begin());
                    node.asset.length = Read64(records.data() + length->offset);
                    std::copy_n(records.data() + digest->offset, 32,
                                node.asset.digest.bytes.begin());
                }
            }
            if (!index->byId.emplace(node.id.bytes, node.order).second) {
                return false;
            }
            currentNode = node.order;
            index->nodeRecords.emplace_back();
            index->documentOrder.push_back(node.order);
            index->byKind[static_cast<std::size_t>(node.kind)].push_back(
                node.order);
            if (node.textPresent) index->byText.push_back(node.order);
            index->nodes.push_back(std::move(node));
        } else if (kind == RecordKind::Edge) {
            const Slice* edgeKind = Find(fields, 1);
            const Slice* source = Find(fields, 2);
            const Slice* target = Find(fields, 3);
            const Slice* ordinal = Find(fields, 4);
            if (edgeKind == nullptr || edgeKind->length != 2 ||
                source == nullptr || source->length != 16 || target == nullptr ||
                target->length != 16 || ordinal == nullptr ||
                ordinal->length != 8) {
                return false;
            }
            PendingEdge edge{};
            edge.kind = static_cast<EdgeKind>(
                Read16(records.data() + edgeKind->offset));
            std::copy_n(records.data() + source->offset, 16,
                        edge.source.bytes.begin());
            std::copy_n(records.data() + target->offset, 16,
                        edge.target.bytes.begin());
            edge.ordinal = Read64(records.data() + ordinal->offset);
            edges.push_back(edge);
        } else if (kind == RecordKind::Property) {
            const Slice* owner = Find(fields, 1);
            const Slice* ownerField = Find(fields, 2);
            const Slice* key = Find(fields, 3);
            const Slice* observation = Find(fields, 4);
            const Slice* origin = Find(fields, 5);
            if (owner == nullptr || owner->length != 16 ||
                ownerField == nullptr || ownerField->length != 2 ||
                key == nullptr || key->length != 4 || observation == nullptr ||
                origin == nullptr || origin->length != 1) {
                return false;
            }
            PendingProperty pending{};
            std::copy_n(records.data() + owner->offset, 16,
                        pending.owner.bytes.begin());
            pending.property.ownerField = Read16(
                records.data() + ownerField->offset);
            pending.property.key = Read32(records.data() + key->offset);
            const PropertyRule* rule = FindPropertyRule(pending.property.key);
            if (rule == nullptr || !ObservationValue(
                    records, *observation, &pending.property.state,
                    &pending.property.value)) {
                return false;
            }
            pending.property.scalar = rule->scalar;
            pending.property.origin = static_cast<PropertyOrigin>(
                records[static_cast<std::size_t>(origin->offset)]);
            if (pending.property.state == ObservationState::Value) {
                pending.property.canonicalValue.assign(
                    records.begin() + static_cast<std::size_t>(
                        pending.property.value.offset),
                    records.begin() + static_cast<std::size_t>(
                        pending.property.value.offset +
                        pending.property.value.length));
            }
            properties.push_back(std::move(pending));
        }
        const Slice recordRange{
            at, kLogicalRecordHeaderBytesV1 + payloadBytes,
            0, ScalarTag::Bytes};
        IndexedRecord indexedRecord{};
        indexedRecord.kind = kind;
        indexedRecord.range = recordRange;
        indexedRecord.node = currentNode;
        if (kind == RecordKind::Edge) {
            const Slice* const target = Find(fields, 3);
            if (target == nullptr || target->length != 16) return false;
            indexedRecord.edgeTargetPresent = true;
            std::copy_n(records.data() + target->offset, 16,
                        indexedRecord.edgeTarget.begin());
        }
        const FieldTag closureTag = kind == RecordKind::Coverage
            ? 5 : kind == RecordKind::Diagnostic ? 6 : 0;
        if (closureTag != 0) {
            const Slice* const encodedBlob = Find(fields, closureTag);
            if (encodedBlob == nullptr ||
                !DecodeBlobSlice(records, *encodedBlob,
                                 &indexedRecord.closureBlob)) return false;
            indexedRecord.closureBlobPresent = true;
        }
        const std::size_t indexedRecordNumber = index->records.size();
        if (kind == RecordKind::Manifest) {
            if (at != 0 || index->manifestRecord.length != 0) return false;
            index->manifestRecord = recordRange;
        } else if (currentNode !=
                       (std::numeric_limits<std::size_t>::max)() &&
                   kind != RecordKind::Tombstone &&
                   kind != RecordKind::Remap) {
            index->nodeRecords[currentNode].push_back(indexedRecordNumber);
        }
        index->records.push_back(std::move(indexedRecord));
        at += kLogicalRecordHeaderBytesV1 + payloadBytes;
    }
    for (const PendingParent& pending : parents) {
        const auto found = index->byId.find(pending.parent.bytes);
        if (found == index->byId.end()) return false;
        Node& node = index->nodes[pending.node];
        node.parentPresent = true;
        node.parent = found->second;
        index->nodes[found->second].children.push_back(pending.node);
    }
    for (const PendingProperty& pending : properties) {
        const auto found = index->byId.find(pending.owner.bytes);
        if (found == index->byId.end()) return false;
        Node& node = index->nodes[found->second];
        node.properties.push_back(pending.property);
        index->byProperty[pending.property.key].push_back(found->second);
        index->byNativeProperty[
            {pending.property.key, pending.property.origin}].push_back(
                found->second);
        if (pending.property.state == ObservationState::Value &&
            pending.property.value.length == 8 &&
            pending.property.key == 12000) {
            node.pageStartPresent = true;
            node.pageStart = Read64(records.data() +
                                    pending.property.value.offset);
        }
        if (pending.property.state == ObservationState::Value &&
            pending.property.value.length == 8 &&
            pending.property.key == 12001) {
            node.pageEndPresent = true;
            node.pageEnd = Read64(records.data() +
                                  pending.property.value.offset);
        }
    }
    for (std::size_t reverse = index->nodes.size(); reverse != 0; --reverse) {
        Node& node = index->nodes[reverse - 1];
        if (node.parentPresent) {
            index->nodes[node.parent].subtreeEnd = (std::max)(
                index->nodes[node.parent].subtreeEnd, node.subtreeEnd);
        }
        if (node.pageStartPresent && node.pageEndPresent) {
            index->byPageSpan.push_back(
                {node.pageStart, node.pageEnd, reverse - 1});
        }
    }
    std::sort(index->byPageSpan.begin(), index->byPageSpan.end(),
              [](const PageSpanIndexEntry& left,
                 const PageSpanIndexEntry& right) {
                  if (left.first != right.first) return left.first < right.first;
                  if (left.last != right.last) return left.last < right.last;
                  return left.node < right.node;
              });
    for (const PendingEdge& pending : edges) {
        const auto source = index->byId.find(pending.source.bytes);
        const auto target = index->byId.find(pending.target.bytes);
        if (source == index->byId.end() || target == index->byId.end()) {
            return false;
        }
        const std::size_t edgeIndex = index->edges.size();
        index->edges.push_back(
            {pending.kind, source->second, target->second, pending.ordinal});
        index->nodes[source->second].outgoing.push_back(edgeIndex);
        if (pending.kind == EdgeKind::CaptionOf &&
            index->nodes[target->second].kind == NodeKind::Table) {
            index->nodes[target->second].captioned = true;
        }
    }
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::ParseIndexBlobValidationStart,
        index->nodes.size(), index->blobs.size());
    std::uint64_t validatedSlices = 0;
    std::uint64_t validatedBytes = 0;
    for (const Node& node : index->nodes) {
        const BlobSlice* slices[]{
            node.textPresent ? &node.text : nullptr,
            node.assetPresent ? &node.asset : nullptr,
        };
        for (const BlobSlice* const slice : slices) {
            if (slice == nullptr) continue;
            if (!ValidateBlobSlice(*index, *slice)) return false;
            ++validatedSlices;
            validatedBytes += slice->length;
            if ((validatedSlices & 1023U) == 0) {
                capture::RecordCaptureProgressPoint(
                    capture::CaptureProgressPoint::
                        ParseIndexBlobValidationProgress,
                    validatedSlices, validatedBytes);
            }
        }
    }
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::ParseIndexBlobValidationEnd,
        validatedSlices, validatedBytes);
    // The immutable generation remains authenticated and write-denied for the
    // index lifetime. GraphNext maps one bounded window, not the complete
    // record stream, avoiding both per-record seeks and whole-view RSS.
    index->recordsFileBytes = generation.recordsStream.length;
    index->recordsMapping = CreateFileMappingW(
        index->recordsFile, nullptr, PAGE_READONLY, 0, 0, nullptr);
    *result = std::move(index);
    return true;
}

bool ReadRecordSlice(const Index& index, const Slice& slice,
                     const std::uint64_t offset, std::uint8_t* output,
                     const std::uint64_t length) noexcept {
    if (offset > slice.length || length > slice.length - offset ||
        (length != 0 && output == nullptr)) {
        return false;
    }
    if (slice.offset > (std::numeric_limits<std::uint64_t>::max)() - offset)
        return false;
    const std::uint64_t absolute = slice.offset + offset;
    if (index.recordsMapping != nullptr && absolute <= index.recordsFileBytes &&
        length <= index.recordsFileBytes - absolute) {
        AcquireSRWLockExclusive(&index.recordsViewLock);
        const std::uint64_t currentDelta = absolute >= index.recordsViewOffset
            ? absolute - index.recordsViewOffset
            : (std::numeric_limits<std::uint64_t>::max)();
        bool mapped = index.recordsView != nullptr &&
            currentDelta <= index.recordsViewBytes &&
            length <= index.recordsViewBytes - currentDelta;
        if (!mapped) {
            if (index.recordsView != nullptr) UnmapViewOfFile(index.recordsView);
            index.recordsView = nullptr;
            index.recordsViewBytes = 0;
            constexpr std::uint64_t kWindowBytes = UINT64_C(4) * 1024 * 1024;
            constexpr std::uint64_t kAllocationGranularity = 65536;
            const std::uint64_t viewOffset =
                absolute & ~(kAllocationGranularity - 1);
            const std::uint64_t required = absolute - viewOffset + length;
            const std::uint64_t viewBytes = (std::min)(
                index.recordsFileBytes - viewOffset,
                (std::max)(kWindowBytes, required));
            index.recordsView = static_cast<const std::uint8_t*>(MapViewOfFile(
                index.recordsMapping, FILE_MAP_READ,
                static_cast<DWORD>(viewOffset >> 32),
                static_cast<DWORD>(viewOffset),
                static_cast<SIZE_T>(viewBytes)));
            if (index.recordsView != nullptr) {
                index.recordsViewOffset = viewOffset;
                index.recordsViewBytes = viewBytes;
                mapped = true;
            }
        }
        if (mapped && length != 0)
            std::memcpy(output,
                        index.recordsView + (absolute - index.recordsViewOffset),
                        static_cast<std::size_t>(length));
        ReleaseSRWLockExclusive(&index.recordsViewLock);
        if (mapped) return true;
    }
    if (index.recordsFile == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER position{};
    position.QuadPart = static_cast<LONGLONG>(absolute);
    DWORD actual = 0;
    return length <= MAXDWORD &&
        SetFilePointerEx(index.recordsFile, position, nullptr, FILE_BEGIN) !=
            FALSE &&
        ReadFile(index.recordsFile, output, static_cast<DWORD>(length),
                 &actual, nullptr) != FALSE && actual == length;
}

int CompareCanonical(const ScalarTag scalar, const ByteView left,
                     const ByteView right) noexcept {
    if (left.size != right.size && FixedScalarBytes(scalar) != 0) return 2;
    if (scalar == ScalarTag::Sint64 || scalar == ScalarTag::HWPUNIT64) {
        if (left.size != 8 || right.size != 8) return 2;
        const auto a = static_cast<std::int64_t>(Read64(left.data));
        const auto b = static_cast<std::int64_t>(Read64(right.data));
        return a < b ? -1 : a > b ? 1 : 0;
    }
    if (scalar == ScalarTag::Uint64 && left.size == 8 && right.size == 8) {
        const auto a = Read64(left.data);
        const auto b = Read64(right.data);
        return a < b ? -1 : a > b ? 1 : 0;
    }
    const std::uint64_t common = (std::min)(left.size, right.size);
    const int compared = common == 0 ? 0 : std::memcmp(
        left.data, right.data, static_cast<std::size_t>(common));
    if (compared != 0) return compared < 0 ? -1 : 1;
    return left.size < right.size ? -1 : left.size > right.size ? 1 : 0;
}

bool ContainsBytes(const ByteView haystack, const ByteView needle) noexcept {
    if (needle.size == 0) return true;
    if (needle.size > haystack.size) return false;
    return std::search(haystack.data, haystack.data + haystack.size,
                       needle.data, needle.data + needle.size) !=
        haystack.data + haystack.size;
}

bool PredicateMatches(const Node& node,
                      const PropertyPredicate& predicate) {
    for (const Property& property : node.properties) {
        if (property.key != predicate.key || property.scalar != predicate.scalar ||
            property.state != ObservationState::Value ||
            (predicate.originPresent && property.origin != predicate.origin)) {
            continue;
        }
        const Bytes& value = property.canonicalValue;
        if (predicate.operation == Operator::ContainsUtf16) {
            if (predicate.scalar != ScalarTag::UTF16 || value.size() < 8 ||
                predicate.canonicalValue.size() < 8) {
                return false;
            }
            return ContainsBytes(
                {value.data() + 8, value.size() - 8},
                {predicate.canonicalValue.data() + 8,
                 predicate.canonicalValue.size() - 8});
        }
        const int compared = CompareCanonical(
            predicate.scalar, codec::View(value),
            codec::View(predicate.canonicalValue));
        switch (predicate.operation) {
        case Operator::Eq: if (compared == 0) return true; break;
        case Operator::Ne: if (compared != 0) return true; break;
        case Operator::Lt: if (compared < 0) return true; break;
        case Operator::Le: if (compared <= 0) return true; break;
        case Operator::Gt: if (compared > 0) return true; break;
        case Operator::Ge: if (compared >= 0) return true; break;
        case Operator::ContainsUtf16: break;
        }
    }
    return false;
}

Bytes Utf16Bytes(const std::u16string& text) {
    Bytes bytes;
    bytes.reserve(text.size() * 2);
    for (const char16_t unit : text) Append16(&bytes, unit);
    return bytes;
}

bool TextMatches(const Index& index, const Node& node,
                 const std::u16string& needle) {
    if (needle.empty()) return true;
    const Bytes encoded = Utf16Bytes(needle);
    const auto direct = [&](const Node& candidate) {
        if (!candidate.textPresent || candidate.text.length < encoded.size()) {
            return false;
        }
        Bytes text(static_cast<std::size_t>(candidate.text.length));
        return ReadBlobRange(index, candidate.text.id, candidate.text.offset,
                             text.data(), candidate.text.length) &&
            ContainsBytes(codec::View(text), codec::View(encoded));
    };
    if (direct(node)) return true;
    for (const std::size_t textNode : index.byText) {
        if (textNode > node.order && textNode < node.subtreeEnd &&
            direct(index.nodes[textNode])) {
            return true;
        }
    }
    return false;
}

bool ValidateQuery(const Query& query) noexcept {
    if (query.projectionBits == 0 ||
        (query.projectionBits & ~kKnownProjectionBits) != 0 ||
        (query.nodeKindBits & ~((UINT64_C(1) << 13) - 1)) != 0 ||
        (query.edgeKindBits & ~((UINT64_C(1) << 14) - 1)) != 0 ||
        (query.pageRangePresent &&
         (query.firstPage == 0 || query.firstPage > query.lastPage)) ||
        (query.axis != Axis::DocumentOrder && !query.rootPresent) ||
        (query.rootPresent && Zero(query.root))) {
        return false;
    }
    for (const PropertyPredicate& predicate : query.properties) {
        const PropertyRule* rule = FindPropertyRule(predicate.key);
        if (rule == nullptr || rule->scalar != predicate.scalar ||
            codec::ValidateCanonicalScalar(predicate.scalar,
                codec::View(predicate.canonicalValue)) != codec::Error::None ||
            (predicate.operation == Operator::ContainsUtf16 &&
             predicate.scalar != ScalarTag::UTF16)) {
            return false;
        }
    }
    return true;
}

std::vector<std::size_t> AxisCandidates(
    const Index& index, const Query& query, std::uint64_t* const pathBits,
    std::uint64_t* const axisCount) {
    std::vector<std::size_t> candidates;
    if (query.axis == Axis::DocumentOrder) {
        *pathBits |= IndexDocumentOrder;
        const auto root = query.rootPresent
            ? index.byId.find(query.root.bytes) : index.byId.end();
        if (query.rootPresent && root == index.byId.end()) return {};
        const std::size_t first = query.rootPresent ? root->second : 0;
        const auto firstInOrder = std::lower_bound(
            index.documentOrder.begin(), index.documentOrder.end(), first);
        *axisCount = static_cast<std::uint64_t>(
            std::distance(firstInOrder, index.documentOrder.end()));
        if (query.nodeKindBits == 0) {
            candidates.assign(firstInOrder, index.documentOrder.end());
        } else {
            *pathBits |= IndexKind;
            for (std::size_t kind = 0; kind != index.byKind.size(); ++kind) {
                if ((query.nodeKindBits & (UINT64_C(1) << kind)) != 0) {
                    const auto begin = std::lower_bound(
                        index.byKind[kind].begin(), index.byKind[kind].end(),
                        first);
                    candidates.insert(candidates.end(), begin,
                                      index.byKind[kind].end());
                }
            }
            std::sort(candidates.begin(), candidates.end());
        }
        return candidates;
    }
    const auto root = index.byId.find(query.root.bytes);
    if (root == index.byId.end()) return {};
    const Node& node = index.nodes[root->second];
    if (query.axis == Axis::Self) {
        candidates.push_back(root->second);
    } else if (query.axis == Axis::Parent) {
        *pathBits |= IndexParent;
        if (node.parentPresent) candidates.push_back(node.parent);
    } else if (query.axis == Axis::Children) {
        *pathBits |= IndexChildren;
        candidates = node.children;
    } else if (query.axis == Axis::Descendants) {
        *pathBits |= IndexDescendants;
        for (std::size_t at = root->second + 1;
             at < node.subtreeEnd; ++at) {
            candidates.push_back(at);
        }
    } else if (query.axis == Axis::References) {
        *pathBits |= IndexReferences;
        for (const std::size_t edgeIndex : node.outgoing) {
            const Edge& edge = index.edges[edgeIndex];
            if (query.edgeKindBits == 0 ||
                (query.edgeKindBits &
                 (UINT64_C(1) << static_cast<std::uint16_t>(edge.kind))) != 0) {
                candidates.push_back(edge.target);
            }
        }
        std::sort(candidates.begin(), candidates.end());
        candidates.erase(std::unique(candidates.begin(), candidates.end()),
                         candidates.end());
    }
    if (query.nodeKindBits != 0) *pathBits |= IndexKind;
    *axisCount = candidates.size();
    return candidates;
}

void IntersectCandidates(std::vector<std::size_t>* const candidates,
                         std::vector<std::size_t> eligible) {
    std::sort(eligible.begin(), eligible.end());
    eligible.erase(std::unique(eligible.begin(), eligible.end()),
                   eligible.end());
    std::vector<std::size_t> intersection;
    intersection.reserve((std::min)(candidates->size(), eligible.size()));
    std::set_intersection(candidates->begin(), candidates->end(),
                          eligible.begin(), eligible.end(),
                          std::back_inserter(intersection));
    *candidates = std::move(intersection);
}

void ApplyFilterIndexes(const Index& index, const Query& query,
                        std::vector<std::size_t>* const candidates,
                        std::uint64_t* const pathBits) {
    if (query.pageRangePresent) {
        *pathBits |= IndexPageSpan;
        std::vector<std::size_t> eligible;
        for (const PageSpanIndexEntry& span : index.byPageSpan) {
            bool match = false;
            switch (query.pageRelation) {
            case PageRelation::Overlaps:
                match = span.last >= query.firstPage &&
                    span.first <= query.lastPage;
                break;
            case PageRelation::ContainedBy:
                match = span.first >= query.firstPage &&
                    span.last <= query.lastPage;
                break;
            case PageRelation::Contains:
                match = span.first <= query.firstPage &&
                    span.last >= query.lastPage;
                break;
            }
            if (match) eligible.push_back(span.node);
        }
        IntersectCandidates(candidates, std::move(eligible));
    }
    for (const PropertyPredicate& predicate : query.properties) {
        *pathBits |= IndexNativeProperty;
        std::vector<std::size_t> eligible;
        if (predicate.originPresent) {
            const auto found = index.byNativeProperty.find(
                {predicate.key, predicate.origin});
            if (found != index.byNativeProperty.end()) eligible = found->second;
        } else {
            const auto found = index.byProperty.find(predicate.key);
            if (found != index.byProperty.end()) eligible = found->second;
        }
        IntersectCandidates(candidates, std::move(eligible));
    }
    if (!query.textContains.empty()) {
        *pathBits |= IndexText;
        std::vector<std::size_t> owners;
        for (const std::size_t textNode : index.byText) {
            if (!TextMatches(index, index.nodes[textNode], query.textContains)) {
                continue;
            }
            std::size_t owner = textNode;
            owners.push_back(owner);
            while (index.nodes[owner].parentPresent) {
                owner = index.nodes[owner].parent;
                owners.push_back(owner);
            }
        }
        IntersectCandidates(candidates, std::move(owners));
    }
}

bool NodeMatches(const Node& node, const Query& query) {
    if (query.nodeKindBits != 0 &&
        (query.nodeKindBits &
         (UINT64_C(1) << static_cast<std::uint16_t>(node.kind))) == 0) {
        return false;
    }
    if (query.captionedOnly && !node.captioned) return false;
    if (query.pageRangePresent) {
        if (!node.pageStartPresent || !node.pageEndPresent ||
            node.pageStart == 0 || node.pageStart > node.pageEnd) {
            return false;
        }
        bool pageMatch = false;
        switch (query.pageRelation) {
        case PageRelation::Overlaps:
            pageMatch = node.pageEnd >= query.firstPage &&
                node.pageStart <= query.lastPage;
            break;
        case PageRelation::ContainedBy:
            pageMatch = node.pageStart >= query.firstPage &&
                node.pageEnd <= query.lastPage;
            break;
        case PageRelation::Contains:
            pageMatch = node.pageStart <= query.firstPage &&
                node.pageEnd >= query.lastPage;
            break;
        }
        if (!pageMatch) return false;
    }
    for (const PropertyPredicate& predicate : query.properties) {
        if (!PredicateMatches(node, predicate)) return false;
    }
    return true;
}

struct Segment final {
    enum class Source : std::uint8_t { Inline, Record, Blob };
    std::size_t node = 0;
    ValueKind kind = ValueKind::Structure;
    FieldTag field = 0;
    PropertyKeyId propertyKey = 0;
    ObservationState observation = ObservationState::Value;
    Source source = Source::Inline;
    Bytes inlineBytes{};
    Slice record{};
    BlobSlice blob{};
    std::uint64_t Length() const noexcept {
        if (source == Source::Inline) return inlineBytes.size();
        if (source == Source::Record) return record.length;
        return blob.length;
    }
};

Bytes StructureValue(const Index& index, const Node& node) {
    Bytes bytes;
    bytes.reserve(58);
    Append(&bytes, node.id.bytes.data(), node.id.bytes.size());
    Append16(&bytes, static_cast<std::uint16_t>(node.kind));
    bytes.push_back(node.parentPresent ? 1 : 0);
    bytes.push_back(0);
    if (node.parentPresent) {
        Append(&bytes, index.nodes[node.parent].id.bytes.data(), 16);
    } else {
        bytes.insert(bytes.end(), 16, 0);
    }
    Append64(&bytes, node.sibling);
    Append64(&bytes, node.order);
    Append64(&bytes, node.children.size());
    return bytes;
}

void AddSegments(const Index& index, const Query& query,
                 const std::vector<std::size_t>& matches,
                 std::vector<Segment>* segments) {
    for (const std::size_t nodeIndex : matches) {
        const Node& node = index.nodes[nodeIndex];
        if ((query.projectionBits & ProjectStructure) != 0) {
            Segment segment{};
            segment.node = nodeIndex;
            segment.kind = ValueKind::Structure;
            segment.inlineBytes = StructureValue(index, node);
            segments->push_back(std::move(segment));
        }
        if ((query.projectionBits & ProjectText) != 0 && node.textPresent) {
            Segment segment{};
            segment.node = nodeIndex;
            segment.kind = ValueKind::Text;
            segment.field = 101;
            segment.source = Segment::Source::Blob;
            segment.blob = node.text;
            segments->push_back(std::move(segment));
        }
        for (const Property& property : node.properties) {
            const bool layout = property.key == 12000 || property.key == 12001 ||
                property.key == 12002 || property.key == 12003;
            if ((!layout && (query.projectionBits & ProjectProperties) == 0) ||
                (layout && (query.projectionBits & ProjectLayout) == 0)) {
                continue;
            }
            Segment segment{};
            segment.node = nodeIndex;
            segment.kind = layout ? ValueKind::Layout : ValueKind::Property;
            segment.field = property.ownerField;
            segment.propertyKey = property.key;
            segment.observation = property.state;
            segment.source = Segment::Source::Record;
            segment.record = property.value;
            segments->push_back(std::move(segment));
        }
        if ((query.projectionBits & ProjectAssets) != 0 && node.assetPresent) {
            Segment segment{};
            segment.node = nodeIndex;
            segment.kind = ValueKind::Asset;
            segment.field = 100;
            segment.source = Segment::Source::Blob;
            segment.blob = node.asset;
            segments->push_back(std::move(segment));
        }
        if ((query.projectionBits & ProjectReferences) != 0) {
            for (const std::size_t edgeIndex : node.outgoing) {
                const Edge& edge = index.edges[edgeIndex];
                Segment segment{};
                segment.node = nodeIndex;
                segment.kind = ValueKind::Reference;
                Append16(&segment.inlineBytes,
                         static_cast<std::uint16_t>(edge.kind));
                Append(&segment.inlineBytes,
                       index.nodes[edge.target].id.bytes.data(), 16);
                Append64(&segment.inlineBytes, edge.ordinal);
                segments->push_back(std::move(segment));
            }
        }
    }
}

Sha256 QueryDigest(const Query& query, const std::uint64_t budget) {
    Bytes bytes;
    bytes.push_back(query.rootPresent ? 1 : 0);
    if (query.rootPresent) Append(&bytes, query.root.bytes.data(), 16);
    bytes.push_back(static_cast<std::uint8_t>(query.axis));
    Append64(&bytes, query.nodeKindBits);
    Append64(&bytes, query.edgeKindBits);
    bytes.push_back(query.pageRangePresent ? 1 : 0);
    Append64(&bytes, query.firstPage);
    Append64(&bytes, query.lastPage);
    bytes.push_back(static_cast<std::uint8_t>(query.pageRelation));
    bytes.push_back(query.captionedOnly ? 1 : 0);
    Append64(&bytes, query.projectionBits);
    Append64(&bytes, budget);
    const Bytes text = Utf16Bytes(query.textContains);
    Append64(&bytes, text.size());
    Append(&bytes, text.data(), text.size());
    Append64(&bytes, query.properties.size());
    for (const PropertyPredicate& predicate : query.properties) {
        Append64(&bytes, predicate.key);
        bytes.push_back(static_cast<std::uint8_t>(predicate.operation));
        Append16(&bytes, static_cast<std::uint16_t>(predicate.scalar));
        bytes.push_back(predicate.originPresent ? 1 : 0);
        bytes.push_back(static_cast<std::uint8_t>(predicate.origin));
        Append64(&bytes, predicate.canonicalValue.size());
        Append(&bytes, predicate.canonicalValue.data(),
               predicate.canonicalValue.size());
    }
    return codec::DomainHash("HWPGRAPH\0QUERY\0V1", codec::View(bytes));
}

Sha256 ChunkDigest(const Chunk& chunk, const Sha256& queryDigest) {
    Bytes bytes;
    Append(&bytes, queryDigest.bytes.data(), queryDigest.bytes.size());
    Append(&bytes, chunk.cursor.bytes.data(), chunk.cursor.bytes.size());
    Append(&bytes, chunk.generation.storeEpoch.data(),
           chunk.generation.storeEpoch.size());
    Append64(&bytes, chunk.generation.serial);
    Append(&bytes, chunk.generation.captureRoot.bytes.data(),
           chunk.generation.captureRoot.bytes.size());
    Append64(&bytes, chunk.sequence);
    Append64(&bytes, chunk.matchedNodes);
    Append64(&bytes, chunk.indexPathBits);
    Append64(&bytes, chunk.axisCandidateNodes);
    Append64(&bytes, chunk.evaluatedNodes);
    bytes.push_back(static_cast<std::uint8_t>(chunk.status));
    bytes.push_back(chunk.terminal ? 1 : 0);
    for (const Fragment& fragment : chunk.fragments) {
        Append(&bytes, fragment.node.bytes.data(), 16);
        Append16(&bytes, static_cast<std::uint16_t>(fragment.nodeKind));
        bytes.push_back(static_cast<std::uint8_t>(fragment.valueKind));
        Append16(&bytes, fragment.field);
        Append64(&bytes, fragment.propertyKey);
        Append64(&bytes, fragment.valueOffset);
        Append64(&bytes, fragment.valueTotal);
        Append(&bytes, fragment.bytes.data(), fragment.bytes.size());
    }
    return codec::DomainHash("HWPGRAPH\0QUERYCHUNK\0V1", codec::View(bytes));
}

Sha256 ChainDigest(const Sha256& previous, const Sha256& chunk,
                   const std::uint64_t sequence) {
    Bytes bytes;
    Append(&bytes, previous.bytes.data(), previous.bytes.size());
    Append(&bytes, chunk.bytes.data(), chunk.bytes.size());
    Append64(&bytes, sequence);
    return codec::DomainHash("HWPGRAPH\0QUERYCHAIN\0V1", codec::View(bytes));
}

Uuid128 MintCursor() noexcept {
    Uuid128 id{};
    if (!identity::SystemRandomBytes(nullptr, id.bytes.data(),
                                    static_cast<std::uint32_t>(id.bytes.size()))) {
        return {};
    }
    id.bytes[6] = static_cast<std::uint8_t>((id.bytes[6] & 0x0fU) | 0x40U);
    id.bytes[8] = static_cast<std::uint8_t>((id.bytes[8] & 0x3fU) | 0x80U);
    return id;
}

} // namespace

struct PlannedRecord final {
    Slice source{};
    RecordKind kind = RecordKind::Manifest;
    RecordId outputId = 0;
    bool omitCharacterRunText = false;
};

class RangeBackedQueryPlan final {
public:
    std::shared_ptr<Index> index{};
    std::vector<PlannedRecord> records{};
    identity::DocumentSessionId session{};
    bool sessionPresent = false;
    Sha256 viewIndexDigest{};
    std::uint64_t logicalBytes = 0;
};

bool Equal(const GenerationKey& left, const GenerationKey& right) noexcept {
    return left.storeEpoch == right.storeEpoch && left.serial == right.serial &&
        codec::Equal(left.captureRoot, right.captureRoot);
}

GraphStoreGenerationSource::GraphStoreGenerationSource(
    store::GraphStore* const store) noexcept : store_(store) {}

bool GraphStoreGenerationSource::Pin(
    GenerationSnapshot* const snapshot) noexcept {
    if (store_ == nullptr || snapshot == nullptr) return false;
    const store::GenerationPin pin = store_->PinActive();
    if (pin == nullptr || pin->Serial() == 0) return false;
    GenerationSnapshot value{};
    value.path = pin->Path();
    value.lease = pin;
    value.key.serial = pin->Serial();
    value.key.storeEpoch = pin->StoreEpoch();
    value.authenticatedFiles = pin->AuthenticatedFiles();
    if (value.authenticatedFiles == nullptr) return false;
    value.manifestStream = value.authenticatedFiles->manifestStream;
    value.recordsStream = value.authenticatedFiles->recordsStream;
    value.blobStream = value.authenticatedFiles->blobStream;
    Bytes manifest;
    if (!ReadWholeHandle(value.authenticatedFiles->manifest, &manifest) ||
        manifest.size() != 580) {
        return false;
    }
    std::copy_n(manifest.data() + 168, 32,
                value.key.captureRoot.bytes.begin());
    *snapshot = std::move(value);
    return true;
}

bool GraphStoreGenerationSource::IsCurrent(
    const GenerationKey& key) noexcept {
    if (store_ == nullptr || key.serial == 0) return false;
    const store::GenerationPin active = store_->PinActive();
    return active != nullptr && active->Serial() == key.serial &&
        active->StoreEpoch() == key.storeEpoch;
}
store::GenerationQueryIndexPin
GraphStoreGenerationSource::CachedQueryIndex(
    const GenerationKey& key) noexcept {
    if (store_ == nullptr) return {};
    const store::GenerationPin generation = store_->PinActive();
    return generation != nullptr && generation->Serial() == key.serial &&
        generation->StoreEpoch() == key.storeEpoch
        ? generation->QueryIndex() : store::GenerationQueryIndexPin{};
}
void GraphStoreGenerationSource::CacheQueryIndex(
    const GenerationKey& key,
    const store::GenerationQueryIndexPin& index) noexcept {
    if (store_ == nullptr || index == nullptr) return;
    const store::GenerationPin generation = store_->PinActive();
    if (generation != nullptr && generation->Serial() == key.serial &&
        generation->StoreEpoch() == key.storeEpoch)
        generation->InstallQueryIndex(index);
}

namespace {

void Write64(std::uint8_t* const output, const std::uint64_t value) noexcept {
    for (unsigned index = 0; index != 8; ++index) {
        output[index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

bool ReadResidentStream(void* const context, const std::uint64_t offset,
                        std::uint8_t* const output,
                        const std::uint32_t requested,
                        std::uint32_t* const actual) noexcept {
    if (context == nullptr || actual == nullptr) return false;
    const auto& bytes = *static_cast<const Bytes*>(context);
    if (offset > bytes.size() || requested > bytes.size() - offset ||
        (requested != 0 && output == nullptr)) {
        *actual = 0;
        return false;
    }
    if (requested != 0) {
        std::copy_n(bytes.data() + offset, requested, output);
    }
    *actual = requested;
    return true;
}

bool OmitCharacterRunText(Bytes* const record) noexcept {
    if (record == nullptr || record->size() < kLogicalRecordHeaderBytesV1)
        return false;
    FieldMap outer;
    if (!ParseFields(*record, kLogicalRecordHeaderBytesV1,
                     record->size() - kLogicalRecordHeaderBytesV1, &outer))
        return false;
    const Slice* const payload = Find(outer, 2);
    if (payload == nullptr || payload->offset < 24 || payload->length < 24)
        return false;
    FieldMap nodeFields;
    if (!ParseFields(*record, payload->offset, payload->length, &nodeFields))
        return false;
    const Slice* const text = Find(nodeFields, 101);
    if (text == nullptr || text->offset < 24 ||
        text->scalar != ScalarTag::BlobSlice || text->length != 88 ||
        text->offset + text->length > record->size())
        return false;
    std::uint8_t* const observation = record->data() + text->offset;
    if (observation[0] != 0 || observation[1] != 1 ||
        Read64(observation + 8) != 0 || Read64(observation + 16) != 64)
        return false;
    observation[0] = static_cast<std::uint8_t>(
        ObservationState::ProjectionOmitted);
    observation[1] = 0;
    std::fill(observation + 2, observation + 24, std::uint8_t{0});
    record->erase(record->begin() + static_cast<std::size_t>(text->offset + 24),
                  record->begin() + static_cast<std::size_t>(text->offset + 88));
    Write64(record->data() + text->offset - 8, 24);
    Write64(record->data() + payload->offset - 8, payload->length - 64);
    Write64(record->data() + 16,
            Read64(record->data() + 16) - 64);
    return true;
}

bool SelectedId(const std::set<std::array<std::uint8_t, 16>>& selected,
                const Bytes& records, const Slice* const id) noexcept {
    if (id == nullptr || id->length != 16 || id->offset > records.size() ||
        id->length > records.size() - id->offset) {
        return false;
    }
    std::array<std::uint8_t, 16> key{};
    std::copy_n(records.data() + id->offset, key.size(), key.begin());
    return selected.find(key) != selected.end();
}

} // namespace

bool BlobSliceEqual(const BlobSlice& left, const BlobSlice& right) noexcept {
    return left.id.bytes == right.id.bytes && left.offset == right.offset &&
        left.length == right.length && left.digest.bytes == right.digest.bytes;
}

bool ReadQueryViewBlob(const QueryView& view, const BlobSlice& slice,
                       const std::uint64_t relativeOffset,
                       std::uint8_t* const output,
                       const std::uint64_t length) noexcept {
    if (view.blobReader == nullptr ||
        std::none_of(view.blobClosure.begin(), view.blobClosure.end(),
                     [&slice](const BlobSlice& authorized) {
                         return BlobSliceEqual(authorized, slice);
                     })) {
        return false;
    }
    return view.blobReader->Read(slice, relativeOffset, output, length);
}

bool BuildPreparedGenerationQueryIndex(
    const HANDLE records, const store::CanonicalStream& recordsStream,
    const HANDLE blobs, const store::CanonicalStream& blobStream,
    store::GenerationQueryIndexPin* const result) noexcept {
    if (result == nullptr || records == INVALID_HANDLE_VALUE ||
        blobs == INVALID_HANDLE_VALUE) return false;
    *result = {};
    try {
        auto files = std::make_shared<store::AuthenticatedGenerationFiles>();
        HANDLE process = GetCurrentProcess();
        if (!DuplicateHandle(process, records, process, &files->records,
                             GENERIC_READ, FALSE, 0) ||
            !DuplicateHandle(process, blobs, process, &files->blobs,
                             GENERIC_READ, FALSE, 0)) return false;
        files->recordsStream = recordsStream;
        files->blobStream = blobStream;
        GenerationSnapshot generation{};
        generation.path = L"prepared-generation-query-index";
        generation.key.serial = 1;
        generation.authenticatedFiles = files;
        generation.recordsStream = recordsStream;
        generation.blobStream = blobStream;
        std::shared_ptr<Index> index;
        if (!ParseIndex(generation, &index)) return false;
        *result = std::move(index);
        return true;
    } catch (...) {
        *result = {};
        return false;
    }
}

Status PrepareGenerationQueryIndex(GenerationSource* const source) noexcept {
    if (source == nullptr) return Status::StorageFailure;
    try {
        GenerationSnapshot generation{};
        if (!source->Pin(&generation)) return Status::StorageFailure;
        if (source->CachedQueryIndex(generation.key) != nullptr)
            return source->IsCurrent(generation.key)
                ? Status::Terminal : Status::StaleGraph;
        std::shared_ptr<Index> index;
        if (!ParseIndex(generation, &index)) return Status::StorageFailure;
        if (!source->IsCurrent(generation.key)) return Status::StaleGraph;
        source->CacheQueryIndex(generation.key, index);
        return source->CachedQueryIndex(generation.key) != nullptr
            ? Status::Terminal : Status::StorageFailure;
    } catch (...) {
        return Status::StorageFailure;
    }
}

Status BuildQueryPlan(
    GenerationSource* const source, const Query& query, QueryView* const view,
    const identity::DocumentSessionId* const session) noexcept {
    if (source == nullptr || view == nullptr || !ValidateQuery(query))
        return Status::InvalidQuery;
    *view = {};
    try {
        GenerationSnapshot generation{};
        if (!source->Pin(&generation)) return Status::StorageFailure;
        std::shared_ptr<Index> index = std::static_pointer_cast<Index>(
            source->CachedQueryIndex(generation.key));
        if (index == nullptr) {
            if (!ParseIndex(generation, &index)) return Status::StorageFailure;
            if (!source->IsCurrent(generation.key)) return Status::StaleGraph;
            source->CacheQueryIndex(generation.key, index);
            index = std::static_pointer_cast<Index>(
                source->CachedQueryIndex(generation.key));
            if (index == nullptr) return Status::StorageFailure;
        }
        if (!source->IsCurrent(generation.key)) return Status::StaleGraph;

        std::uint64_t indexPathBits = 0;
        std::uint64_t axisCandidateNodes = 0;
        std::vector<std::size_t> candidates = AxisCandidates(
            *index, query, &indexPathBits, &axisCandidateNodes);
        ApplyFilterIndexes(*index, query, &candidates, &indexPathBits);
        std::set<std::array<std::uint8_t, 16>> selected;
        for (const std::size_t candidate : candidates)
            if (NodeMatches(index->nodes[candidate], query))
                selected.insert(index->nodes[candidate].id.bytes);

        auto plan = std::make_shared<RangeBackedQueryPlan>();
        plan->index = index;
        plan->sessionPresent = session != nullptr;
        if (session != nullptr) plan->session = *session;
        view->generation = generation.key;
        view->queryDigest = QueryDigest(query, 0);
        Bytes binding;
        Append64(&binding, generation.key.serial);
        Append(&binding, generation.key.captureRoot.bytes.data(), 32);
        Append(&binding, view->queryDigest.bytes.data(), 32);
        if (session != nullptr) Append(&binding, session->bytes.data(), 16);
        for (const auto& id : selected) Append(&binding, id.data(), id.size());
        plan->viewIndexDigest = codec::DomainHash(
            "HGN1-QUERY-VIEW-INDEX-V1", codec::View(binding));
        view->viewIndexDigest = plan->viewIndexDigest;

        const auto addRecord = [&plan](const IndexedRecord& sourceRecord,
                                      const bool omitText) {
            PlannedRecord record{};
            record.source = sourceRecord.range;
            record.kind = sourceRecord.kind;
            record.outputId = plan->records.size();
            record.omitCharacterRunText = omitText;
            const std::uint64_t outputBytes = sourceRecord.range.length -
                (omitText ? UINT64_C(64) : UINT64_C(0));
            plan->logicalBytes += outputBytes;
            plan->records.push_back(record);
        };
        if (index->records.empty() ||
            index->records.front().kind != RecordKind::Manifest)
            return Status::StorageFailure;
        addRecord(index->records.front(), false);
        for (std::size_t nodeIndex = 0; nodeIndex != index->nodes.size();
             ++nodeIndex) {
            const Node& node = index->nodes[nodeIndex];
            if (selected.find(node.id.bytes) == selected.end()) continue;
            for (const std::size_t recordIndex : index->nodeRecords[nodeIndex]) {
                const IndexedRecord& record = index->records[recordIndex];
                bool keep = record.kind == RecordKind::Node ||
                    record.kind == RecordKind::Coverage ||
                    record.kind == RecordKind::Diagnostic;
                if (record.kind == RecordKind::Property)
                    keep = (query.projectionBits & ProjectProperties) != 0;
                else if (record.kind == RecordKind::AssetChunk)
                    keep = (query.projectionBits & ProjectAssets) != 0;
                else if (record.kind == RecordKind::Edge)
                    keep = (query.projectionBits &
                            (ProjectStructure | ProjectReferences)) != 0 &&
                        record.edgeTargetPresent &&
                        selected.find(record.edgeTarget) != selected.end();
                if (!keep) continue;
                addRecord(record,
                    record.kind == RecordKind::Node &&
                    node.kind == NodeKind::CharacterRun &&
                    (query.projectionBits & ProjectText) == 0);
            }
        }

        std::vector<BlobSlice> closure;
        for (std::size_t nodeIndex = 0; nodeIndex != index->nodes.size();
             ++nodeIndex) {
            const Node& node = index->nodes[nodeIndex];
            if (selected.find(node.id.bytes) == selected.end()) continue;
            if ((query.projectionBits & ProjectText) != 0 && node.textPresent)
                closure.push_back(node.text);
            if ((query.projectionBits & ProjectAssets) != 0 && node.assetPresent)
                closure.push_back(node.asset);
            for (const std::size_t recordIndex : index->nodeRecords[nodeIndex]) {
                const IndexedRecord& record = index->records[recordIndex];
                if (record.closureBlobPresent) closure.push_back(record.closureBlob);
            }
        }
        const auto blobLess = [](const BlobSlice& left, const BlobSlice& right) {
            return std::tie(left.id.bytes, left.offset, left.length,
                            left.digest.bytes) <
                   std::tie(right.id.bytes, right.offset, right.length,
                            right.digest.bytes);
        };
        std::sort(closure.begin(), closure.end(), blobLess);
        closure.erase(std::unique(closure.begin(), closure.end(), BlobSliceEqual),
                      closure.end());
        if (std::any_of(closure.begin(), closure.end(),
                        [&index](const BlobSlice& slice) {
                            return !ValidateBlobSlice(*index, slice);
                        })) return Status::StorageFailure;
        if (!source->IsCurrent(generation.key)) return Status::StaleGraph;

        view->snapshot = generation;
        view->blobClosure = std::move(closure);
        view->blobReader = std::make_shared<IndexBlobReader>(index);
        view->logicalBytes = plan->logicalBytes;
        view->logicalRecords = plan->records.size();
        view->canonical.streamKind = StreamKind::QueryView;
        view->canonical.recordStream.itemCount = view->logicalRecords;
        view->canonical.recordStream.byteLength = view->logicalBytes;
        view->canonical.viewIndexDigest = view->viewIndexDigest;
        view->rangePlan = std::move(plan);
        return selected.empty() ? Status::Empty : Status::Terminal;
    } catch (...) {
        *view = {};
        return Status::StorageFailure;
    }
}

bool MaterializeQueryPlanRecord(const QueryView& view,
                                const std::uint64_t recordOrdinal,
                                Bytes* const record) noexcept {
    if (record == nullptr || view.rangePlan == nullptr ||
        recordOrdinal >= view.rangePlan->records.size()) return false;
    try {
        const PlannedRecord& planned =
            view.rangePlan->records[static_cast<std::size_t>(recordOrdinal)];
        if (planned.source.length >
            static_cast<std::uint64_t>((std::numeric_limits<std::size_t>::max)()))
            return false;
        record->resize(static_cast<std::size_t>(planned.source.length));
        if (!ReadRecordSlice(*view.rangePlan->index, planned.source, 0,
                             record->data(), planned.source.length)) {
            record->clear();
            return false;
        }
        if (planned.omitCharacterRunText && !OmitCharacterRunText(record)) {
            record->clear();
            return false;
        }
        if (record->size() < kLogicalRecordHeaderBytesV1) return false;
        Write64(record->data() + 8, planned.outputId);
        if (planned.kind == RecordKind::Manifest) {
            FieldMap fields;
            if (!ParseFields(*record, kLogicalRecordHeaderBytesV1,
                             record->size() - kLogicalRecordHeaderBytesV1,
                             &fields)) return false;
            const Slice* version = Find(fields, 1);
            const Slice* count = Find(fields, 4);
            const Slice* digest = Find(fields, 5);
            const Slice* streamKind = Find(fields, 6);
            if (version == nullptr || version->length != kGraphVersionBytesV1 ||
                count == nullptr || count->length != 8 || digest == nullptr ||
                digest->length != 32 || streamKind == nullptr ||
                streamKind->length != 1) return false;
            if (view.rangePlan->sessionPresent) {
                std::uint8_t* serialized = record->data() + version->offset;
                std::copy(view.rangePlan->session.bytes.begin(),
                          view.rangePlan->session.bytes.end(), serialized + 16);
                std::copy(view.generation.captureRoot.bytes.begin(),
                          view.generation.captureRoot.bytes.begin() + 16,
                          serialized + 32);
                serialized[38] = static_cast<std::uint8_t>(
                    (serialized[38] & 0x0fU) | 0x40U);
                serialized[40] = static_cast<std::uint8_t>(
                    (serialized[40] & 0x3fU) | 0x80U);
                Write64(serialized + 48, view.generation.serial);
                Write64(serialized + 56, view.generation.serial);
                Write64(serialized + 64, view.generation.serial);
            }
            Write64(record->data() + count->offset,
                    view.rangePlan->records.size());
            std::copy(view.rangePlan->viewIndexDigest.bytes.begin(),
                      view.rangePlan->viewIndexDigest.bytes.end(),
                      record->data() + digest->offset);
            (*record)[static_cast<std::size_t>(streamKind->offset)] =
                static_cast<std::uint8_t>(StreamKind::QueryView);
        }
        return record->size() == planned.source.length -
            (planned.omitCharacterRunText ? 64U : 0U);
    } catch (...) {
        record->clear();
        return false;
    }
}

Status BuildQueryView(
    GenerationSource* const source, const Query& query, QueryView* const view,
    const identity::DocumentSessionId* const session) noexcept {
    if (source == nullptr || view == nullptr || !ValidateQuery(query)) {
        return Status::InvalidQuery;
    }
    *view = {};
    try {
        GenerationSnapshot generation{};
        if (!source->Pin(&generation)) return Status::StorageFailure;
        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::ParseIndexStart,
            generation.recordsStream.length, generation.blobStream.length);
        std::shared_ptr<Index> index = std::static_pointer_cast<Index>(
            source->CachedQueryIndex(generation.key));
        if (index == nullptr) {
            if (!ParseIndex(generation, &index)) return Status::StorageFailure;
            if (!source->IsCurrent(generation.key)) return Status::StaleGraph;
            source->CacheQueryIndex(generation.key, index);
            index = std::static_pointer_cast<Index>(
                source->CachedQueryIndex(generation.key));
            if (index == nullptr) return Status::StorageFailure;
        }
        if (!source->IsCurrent(generation.key)) return Status::StaleGraph;

        std::uint64_t indexPathBits = 0;
        std::uint64_t axisCandidateNodes = 0;
        std::vector<std::size_t> candidates = AxisCandidates(
            *index, query, &indexPathBits, &axisCandidateNodes);
        ApplyFilterIndexes(*index, query, &candidates, &indexPathBits);
        std::set<std::array<std::uint8_t, 16>> selected;
        for (const std::size_t candidate : candidates) {
            if (NodeMatches(index->nodes[candidate], query)) {
                selected.insert(index->nodes[candidate].id.bytes);
            }
        }

        std::vector<Bytes> retained;
        std::uint64_t copiedRecords = 0;
        std::uint64_t retainedBytes = 0;
        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::QueryMaterializationStart,
            generation.recordsStream.length, selected.size());
        const auto readRange = [&index](const Slice &range, Bytes *bytes) {
            if (bytes == nullptr || range.length > static_cast<std::uint64_t>(
                    (std::numeric_limits<std::size_t>::max)()))
                return false;
            bytes->resize(static_cast<std::size_t>(range.length));
            return ReadRecordSlice(*index, range, 0, bytes->data(),
                                   range.length);
        };
        retained.emplace_back();
        if (!readRange(index->manifestRecord, &retained.back()))
            return Status::StorageFailure;
        retainedBytes = retained.back().size();
        ++copiedRecords;
        for (std::size_t nodeIndex = 0; nodeIndex != index->nodes.size();
             ++nodeIndex) {
            const Node &node = index->nodes[nodeIndex];
            if (selected.find(node.id.bytes) == selected.end()) continue;
            for (const std::size_t recordIndex : index->nodeRecords[nodeIndex]) {
                const Slice& range = index->records[recordIndex].range;
                Bytes record;
                if (!readRange(range, &record) ||
                    record.size() < kLogicalRecordHeaderBytesV1)
                    return Status::StorageFailure;
                const auto kind =
                    static_cast<RecordKind>(Read16(record.data()));
                FieldMap fields;
                if (!ParseFields(record, kLogicalRecordHeaderBytesV1,
                                 record.size() - kLogicalRecordHeaderBytesV1,
                                 &fields))
                    return Status::StorageFailure;
                bool keep = kind == RecordKind::Node ||
                    kind == RecordKind::Coverage ||
                    kind == RecordKind::Diagnostic;
                if (kind == RecordKind::Property)
                    keep = (query.projectionBits & ProjectProperties) != 0;
                else if (kind == RecordKind::AssetChunk)
                    keep = (query.projectionBits & ProjectAssets) != 0;
                else if (kind == RecordKind::Edge)
                    keep = (query.projectionBits &
                            (ProjectStructure | ProjectReferences)) != 0 &&
                        SelectedId(selected, record, Find(fields, 3));
                if (!keep) continue;
                if (kind == RecordKind::Node &&
                    node.kind == NodeKind::CharacterRun &&
                    (query.projectionBits & ProjectText) == 0 &&
                    !OmitCharacterRunText(&record))
                    return Status::StorageFailure;
                retainedBytes += record.size();
                retained.push_back(std::move(record));
                ++copiedRecords;
                if ((copiedRecords & 1023U) == 0)
                    capture::RecordCaptureProgressPoint(
                        capture::CaptureProgressPoint::
                            QueryMaterializationProgress,
                        copiedRecords, retainedBytes);
            }
        }
        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::QueryMaterializationEnd,
            retained.size(), retainedBytes);

        view->generation = generation.key;
        view->queryDigest = QueryDigest(query, 0);
        Bytes viewBinding;
        Append64(&viewBinding, generation.key.serial);
        Append(&viewBinding, generation.key.captureRoot.bytes.data(),
               generation.key.captureRoot.bytes.size());
        Append(&viewBinding, view->queryDigest.bytes.data(),
               view->queryDigest.bytes.size());
        if (session != nullptr)
            Append(&viewBinding, session->bytes.data(), session->bytes.size());
        for (const auto& id : selected) Append(&viewBinding, id.data(), id.size());
        view->viewIndexDigest = codec::DomainHash(
            "HGN1-QUERY-VIEW-INDEX-V1", codec::View(viewBinding));

        FieldMap manifestFields;
        if (!ParseFields(retained.front(), kLogicalRecordHeaderBytesV1,
                         retained.front().size() - kLogicalRecordHeaderBytesV1,
                         &manifestFields)) {
            return Status::StorageFailure;
        }
        const Slice* version = Find(manifestFields, 1);
        const Slice* count = Find(manifestFields, 4);
        const Slice* indexDigest = Find(manifestFields, 5);
        const Slice* streamKind = Find(manifestFields, 6);
        if (version == nullptr || version->length != kGraphVersionBytesV1 ||
            count == nullptr || count->length != 8 || indexDigest == nullptr ||
            indexDigest->length != view->viewIndexDigest.bytes.size() ||
            streamKind == nullptr || streamKind->length != 1) {
            return Status::StorageFailure;
        }
        if (session != nullptr) {
            std::uint8_t* const serialized =
                retained.front().data() + version->offset;
            std::copy(session->bytes.begin(), session->bytes.end(),
                      serialized + 16);
            std::copy(generation.key.captureRoot.bytes.begin(),
                      generation.key.captureRoot.bytes.begin() + 16,
                      serialized + 32);
            serialized[32 + 6] = static_cast<std::uint8_t>(
                (serialized[32 + 6] & 0x0fU) | 0x40U);
            serialized[32 + 8] = static_cast<std::uint8_t>(
                (serialized[32 + 8] & 0x3fU) | 0x80U);
            Write64(serialized + 48, generation.key.serial);
            Write64(serialized + 56, generation.key.serial);
            Write64(serialized + 64, generation.key.serial);
        }
        Write64(retained.front().data() + count->offset, retained.size());
        retained.front()[static_cast<std::size_t>(streamKind->offset)] =
            static_cast<std::uint8_t>(StreamKind::QueryView);
        std::copy(view->viewIndexDigest.bytes.begin(),
                  view->viewIndexDigest.bytes.end(),
                  retained.front().data() + indexDigest->offset);

        view->records.reserve(static_cast<std::size_t>(retainedBytes));
        RecordId nextId = 0;
        for (Bytes& record : retained) {
            Write64(record.data() + 8, nextId++);
            view->records.insert(view->records.end(), record.begin(), record.end());
        }
        codec::RecordStream stream{
            &view->records, ReadResidentStream, view->records.size(),
            StreamKind::QueryView, codec::View(view->records)};
        // CanonicalizeRecordStream below is the single encoded-byte semantic
        // verifier. Running ValidateRecordStream immediately before it was a
        // second full 22.7MB parse of the same locally assembled bytes.
        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::QueryClosureStart,
            selected.size(), retained.size());
        std::vector<BlobSlice> closure;
        for (const Node& node : index->nodes) {
            if (selected.find(node.id.bytes) == selected.end()) continue;
            if ((query.projectionBits & ProjectText) != 0 && node.textPresent) {
                closure.push_back(node.text);
            }
            if ((query.projectionBits & ProjectAssets) != 0 && node.assetPresent) {
                closure.push_back(node.asset);
            }
        }
        for (const Bytes& record : retained) {
            const auto kind = static_cast<RecordKind>(Read16(record.data()));
            const FieldTag blobTag = kind == RecordKind::Coverage
                ? 5
                : kind == RecordKind::Diagnostic ? 6 : 0;
            if (blobTag == 0) continue;
            FieldMap recordFields;
            BlobSlice blob{};
            if (!ParseFields(record, kLogicalRecordHeaderBytesV1,
                             record.size() - kLogicalRecordHeaderBytesV1,
                             &recordFields)) {
                *view = {};
                return Status::StorageFailure;
            }
            const Slice* const encodedBlob = Find(recordFields, blobTag);
            if (encodedBlob == nullptr ||
                !DecodeBlobSlice(record, *encodedBlob, &blob)) {
                *view = {};
                return Status::StorageFailure;
            }
            closure.push_back(blob);
        }
        const auto blobLess = [](const BlobSlice& left,
                                 const BlobSlice& right) {
            if (left.id.bytes != right.id.bytes)
                return left.id.bytes < right.id.bytes;
            if (left.offset != right.offset) return left.offset < right.offset;
            if (left.length != right.length) return left.length < right.length;
            return left.digest.bytes < right.digest.bytes;
        };
        std::sort(closure.begin(), closure.end(), blobLess);
        closure.erase(
            std::unique(closure.begin(), closure.end(), BlobSliceEqual),
            closure.end());
        if (std::any_of(closure.begin(), closure.end(),
                        [&index](const BlobSlice& slice) {
                            return !ValidateBlobSlice(*index, slice);
                        })) {
            *view = {};
            return Status::StorageFailure;
        }
        std::uint64_t closureBytes = 0;
        for (const BlobSlice& slice : closure) closureBytes += slice.length;
        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::QueryClosureEnd,
            closure.size(), closureBytes);

        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::QueryCanonicalizationStart,
            view->records.size(), closure.size());
        codec::CanonicalizationInput canonicalInput{};
        canonicalInput.records = stream;
        canonicalInput.blobContext = index.get();
        canonicalInput.readBlob = CanonicalBlobRead;
        if (codec::CanonicalizeRecordStream(canonicalInput,
                                             &view->canonical) !=
            codec::Error::None) {
            *view = {};
            return Status::StorageFailure;
        }
        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::QueryCanonicalizationEnd,
            view->canonical.recordStream.itemCount,
            view->canonical.recordStream.byteLength);
        if (!source->IsCurrent(generation.key)) {
            *view = {};
            return Status::StaleGraph;
        }
        view->snapshot = generation;
        view->blobClosure = std::move(closure);
        view->blobReader = std::make_shared<IndexBlobReader>(index);
        return selected.empty() ? Status::Empty : Status::Terminal;
    } catch (...) {
        *view = {};
        return Status::StorageFailure;
    }
}

class DocumentGraphQuery::Impl final {
public:
    explicit Impl(GenerationSource* const source) noexcept : source_(source) {}

    struct Cursor final {
        Uuid128 id{};
        GenerationKey generation{};
        std::shared_ptr<Index> index{};
        Sha256 queryDigest{};
        Sha256 previousChain{};
        std::uint64_t budget = 0;
        std::uint64_t nextSequence = 0;
        std::uint64_t matchedNodes = 0;
        std::uint64_t indexPathBits = 0;
        std::uint64_t axisCandidateNodes = 0;
        std::uint64_t evaluatedNodes = 0;
        std::size_t segment = 0;
        std::uint64_t segmentOffset = 0;
        std::vector<Segment> segments{};
        Status state = Status::Ok;
    };

    Status Open(const Query& query, const std::uint64_t byteBudget,
                Chunk* const chunk) noexcept {
        if (chunk == nullptr || source_ == nullptr) return Status::InvalidQuery;
        *chunk = {};
        if (byteBudget < kMinimumByteBudget) {
            chunk->status = Status::BudgetTooSmall;
            return chunk->status;
        }
        if (!ValidateQuery(query)) {
            chunk->status = Status::InvalidQuery;
            return chunk->status;
        }
        try {
            GenerationSnapshot generation{};
            if (!source_->Pin(&generation)) {
                chunk->status = Status::StorageFailure;
                return chunk->status;
            }
            std::shared_ptr<Index> index = std::static_pointer_cast<Index>(
                source_->CachedQueryIndex(generation.key));
            {
                std::lock_guard<std::mutex> guard(lock_);
                if (index == nullptr && cached_ != nullptr &&
                    Equal(cached_->generation.key, generation.key)) {
                    index = cached_;
                }
            }
            if (index == nullptr) {
                if (!ParseIndex(generation, &index)) {
                    chunk->status = Status::StorageFailure;
                    return chunk->status;
                }
                if (!source_->IsCurrent(generation.key)) {
                    chunk->status = Status::StaleGraph;
                    return chunk->status;
                }
                source_->CacheQueryIndex(generation.key, index);
                std::lock_guard<std::mutex> guard(lock_);
                cached_ = index;
            }
            std::uint64_t indexPathBits = 0;
            std::uint64_t axisCandidateNodes = 0;
            std::vector<std::size_t> candidates = AxisCandidates(
                *index, query, &indexPathBits, &axisCandidateNodes);
            ApplyFilterIndexes(
                *index, query, &candidates, &indexPathBits);
            std::vector<std::size_t> matches;
            for (const std::size_t candidate : candidates) {
                if (NodeMatches(index->nodes[candidate], query)) {
                    matches.push_back(candidate);
                }
            }
            Cursor cursor{};
            cursor.id = MintCursor();
            if (Zero(cursor.id)) {
                chunk->status = Status::StorageFailure;
                return chunk->status;
            }
            cursor.generation = generation.key;
            cursor.index = index;
            cursor.queryDigest = QueryDigest(query, byteBudget);
            cursor.budget = byteBudget;
            cursor.matchedNodes = matches.size();
            cursor.indexPathBits = indexPathBits;
            cursor.axisCandidateNodes = axisCandidateNodes;
            cursor.evaluatedNodes = candidates.size();
            AddSegments(*index, query, matches, &cursor.segments);
            {
                std::lock_guard<std::mutex> guard(lock_);
                while (cursors_.find(cursor.id.bytes) != cursors_.end()) {
                    cursor.id = MintCursor();
                    if (Zero(cursor.id)) {
                        chunk->status = Status::StorageFailure;
                        return chunk->status;
                    }
                }
                cursors_.emplace(cursor.id.bytes, std::move(cursor));
            }
            NextRequest first{};
            first.cursor = cursor.id;
            first.generation = generation.key;
            first.byteBudget = byteBudget;
            return Next(first, chunk, true);
        } catch (...) {
            chunk->status = Status::StorageFailure;
            return chunk->status;
        }
    }

    Status Next(const NextRequest& request, Chunk* const chunk,
                const bool opening = false) noexcept {
        if (chunk == nullptr || source_ == nullptr) return Status::InvalidQuery;
        *chunk = {};
        std::lock_guard<std::mutex> guard(lock_);
        const auto found = cursors_.find(request.cursor.bytes);
        if (found == cursors_.end() || Zero(request.cursor)) {
            chunk->status = Status::MalformedCursor;
            return chunk->status;
        }
        Cursor& cursor = found->second;
        chunk->cursor = cursor.id;
        chunk->generation = cursor.generation;
        if (cursor.state == Status::CursorClosed ||
            cursor.state == Status::Cancelled) {
            chunk->status = cursor.state;
            return chunk->status;
        }
        if (!Equal(request.generation, cursor.generation)) {
            chunk->status = Status::StaleGraph;
            return chunk->status;
        }
        if (request.byteBudget != cursor.budget ||
            request.byteBudget < kMinimumByteBudget) {
            chunk->status = Status::BudgetTooSmall;
            return chunk->status;
        }
        if (request.sequence != cursor.nextSequence) {
            chunk->status = Status::SequenceMismatch;
            return chunk->status;
        }
        if (!codec::Equal(request.previousChain, cursor.previousChain)) {
            chunk->status = Status::DigestMismatch;
            return chunk->status;
        }
        if (!source_->IsCurrent(cursor.generation)) {
            cursor.state = Status::StaleGraph;
            chunk->status = Status::StaleGraph;
            return chunk->status;
        }
        chunk->sequence = cursor.nextSequence;
        chunk->matchedNodes = cursor.matchedNodes;
        chunk->indexPathBits = cursor.indexPathBits;
        chunk->axisCandidateNodes = cursor.axisCandidateNodes;
        chunk->evaluatedNodes = cursor.evaluatedNodes;
        chunk->previousChain = cursor.previousChain;
        chunk->chargedBytes = 0;
        std::size_t nextSegment = cursor.segment;
        std::uint64_t nextSegmentOffset = cursor.segmentOffset;
        while (nextSegment < cursor.segments.size() &&
               chunk->chargedBytes + kFragmentMetadataBytes <= cursor.budget) {
            const Segment& segment = cursor.segments[nextSegment];
            const std::uint64_t total = segment.Length();
            const std::uint64_t room = cursor.budget - chunk->chargedBytes -
                kFragmentMetadataBytes;
            const std::uint64_t remaining = total - nextSegmentOffset;
            const std::uint64_t take = (std::min)(room, remaining);
            if (remaining != 0 && take == 0) break;
            Fragment fragment{};
            const Node& node = cursor.index->nodes[segment.node];
            fragment.node = node.id;
            fragment.nodeKind = node.kind;
            fragment.valueKind = segment.kind;
            fragment.field = segment.field;
            fragment.propertyKey = segment.propertyKey;
            fragment.observation = segment.observation;
            fragment.valueOffset = nextSegmentOffset;
            fragment.valueTotal = total;
            fragment.bytes.resize(static_cast<std::size_t>(take));
            bool read = true;
            if (take != 0 && segment.source == Segment::Source::Inline) {
                std::copy_n(segment.inlineBytes.data() + nextSegmentOffset,
                            static_cast<std::size_t>(take),
                            fragment.bytes.data());
            } else if (take != 0 &&
                       segment.source == Segment::Source::Record) {
                read = ReadRecordSlice(*cursor.index, segment.record,
                    nextSegmentOffset, fragment.bytes.data(), take);
            } else if (take != 0) {
                read = ReadBlobRange(*cursor.index, segment.blob.id,
                    segment.blob.offset + nextSegmentOffset,
                    fragment.bytes.data(), take);
            }
            if (!read) {
                chunk->fragments.clear();
                chunk->chargedBytes = 0;
                chunk->status = Status::StorageFailure;
                if (opening) cursors_.erase(found);
                return chunk->status;
            }
            chunk->chargedBytes += kFragmentMetadataBytes + take;
            chunk->fragments.push_back(std::move(fragment));
            nextSegmentOffset += take;
            if (nextSegmentOffset == total) {
                ++nextSegment;
                nextSegmentOffset = 0;
            }
        }
        if (!source_->IsCurrent(cursor.generation)) {
            chunk->fragments.clear();
            chunk->chargedBytes = 0;
            cursor.state = Status::StaleGraph;
            chunk->status = Status::StaleGraph;
            return chunk->status;
        }
        cursor.segment = nextSegment;
        cursor.segmentOffset = nextSegmentOffset;
        chunk->terminal = cursor.segment == cursor.segments.size();
        chunk->status = chunk->terminal
            ? (cursor.matchedNodes == 0 ? Status::Empty : Status::Terminal)
            : Status::Ok;
        chunk->chunkDigest = ChunkDigest(*chunk, cursor.queryDigest);
        chunk->chainDigest = ChainDigest(chunk->previousChain,
                                         chunk->chunkDigest,
                                         chunk->sequence);
        cursor.previousChain = chunk->chainDigest;
        ++cursor.nextSequence;
        if (chunk->terminal) cursor.state = Status::Terminal;
        static_cast<void>(opening);
        return chunk->status;
    }

    Status SetState(const Uuid128& id, const Status state) noexcept {
        if (Zero(id)) return Status::MalformedCursor;
        std::lock_guard<std::mutex> guard(lock_);
        const auto found = cursors_.find(id.bytes);
        if (found == cursors_.end()) return Status::MalformedCursor;
        if (found->second.state == Status::CursorClosed) {
            return Status::CursorClosed;
        }
        if (found->second.state == Status::Cancelled) return Status::Cancelled;
        found->second.state = state;
        found->second.segments.clear();
        found->second.index.reset();
        return state;
    }

    std::size_t CursorCount() const noexcept {
        std::lock_guard<std::mutex> guard(lock_);
        return cursors_.size();
    }

private:
    GenerationSource* source_ = nullptr;
    mutable std::mutex lock_{};
    std::shared_ptr<Index> cached_{};
    std::map<std::array<std::uint8_t, 16>, Cursor> cursors_{};
};

DocumentGraphQuery::DocumentGraphQuery(GenerationSource* const source) noexcept
    : impl_(new (std::nothrow) Impl(source)) {}
DocumentGraphQuery::~DocumentGraphQuery() noexcept = default;

Status DocumentGraphQuery::Open(const Query& query,
                                const std::uint64_t byteBudget,
                                Chunk* const chunk) noexcept {
    if (impl_ == nullptr) return Status::StorageFailure;
    return impl_->Open(query, byteBudget, chunk);
}
Status DocumentGraphQuery::Next(const NextRequest& request,
                                Chunk* const chunk) noexcept {
    if (impl_ == nullptr) return Status::StorageFailure;
    return impl_->Next(request, chunk);
}
Status DocumentGraphQuery::Cancel(const Uuid128& cursor) noexcept {
    return impl_ == nullptr ? Status::StorageFailure
                            : impl_->SetState(cursor, Status::Cancelled);
}
Status DocumentGraphQuery::Close(const Uuid128& cursor) noexcept {
    return impl_ == nullptr ? Status::StorageFailure
                            : impl_->SetState(cursor, Status::CursorClosed);
}
std::size_t DocumentGraphQuery::CursorCount() const noexcept {
    return impl_ == nullptr ? 0 : impl_->CursorCount();
}

void ResetQueryDebugCounters() noexcept {
    gDebugCounters = {};
    gDebugCountersEnabled = true;
}

QueryDebugCounters ReadQueryDebugCounters() noexcept {
    return gDebugCounters;
}

} // namespace hancom::graph::query

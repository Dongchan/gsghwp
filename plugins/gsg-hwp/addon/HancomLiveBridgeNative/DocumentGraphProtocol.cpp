#include "DocumentGraphProtocol.h"

#include "DocumentGraphCapture.h"
#include "DocumentGraphProperties.h"
#include "DocumentGraphPatch.h"
#include "DocumentGraphQuery.h"
#include "OfficialApiCapability.h"

#include <bcrypt.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <limits>
#include <map>
#include <memory>
#include <mutex>

namespace hancom::graph::protocol {
namespace {

using codec::ByteView;
using codec::Bytes;

constexpr char kChunkDomain[] = "HWPGRAPH\0CHUNK\0V1";
constexpr char kChainDomain[] = "HWPGRAPH\0CHAIN\0V1";
constexpr char kStreamDomain[] = "HWPGRAPH\0STREAM\0V1";
constexpr char kPatchRawDomain[] = "HWPGRAPH\0PATCHRAW\0V1";

struct AtomicFrameEnvelopeCounters final {
    std::atomic_uint64_t queryFramePlans{};
    std::atomic_uint64_t recordReadBytes{};
    std::atomic_uint64_t fragmentValueBytes{};
    std::atomic_uint64_t fragmentTemporaryCopyBytes{};
    std::atomic_uint64_t streamHashStagingBytes{};
    std::atomic_uint64_t framePayloadCopyBytes{};
    std::atomic_uint64_t responseAuthenticationPasses{};
    std::atomic_uint64_t responseAuthenticationBytes{};
    std::atomic_uint64_t safeArrayCopyBytes{};
    std::atomic_uint64_t queryFramePlanTicks{};
    std::atomic_uint64_t recordReadTicks{};
    std::atomic_uint64_t digestTicks{};
    std::atomic_uint64_t frameEncodeTicks{};
    std::atomic_uint64_t safeArrayTicks{};
};
AtomicFrameEnvelopeCounters gFrameEnvelopeCounters{};
std::uint64_t PerformanceTick() noexcept {
    LARGE_INTEGER value{};
    return QueryPerformanceCounter(&value) == FALSE ? 0 :
        static_cast<std::uint64_t>(value.QuadPart);
}
void AddTicks(std::atomic_uint64_t& counter,
              const std::uint64_t started) noexcept {
    const std::uint64_t ended = PerformanceTick();
    if (started != 0 && ended >= started)
        counter.fetch_add(ended - started, std::memory_order_relaxed);
}
struct ScopedCounterTick final {
    explicit ScopedCounterTick(std::atomic_uint64_t* value) noexcept
        : counter(value), started(PerformanceTick()) {}
    ~ScopedCounterTick() noexcept {
        if (counter != nullptr) AddTicks(*counter, started);
    }
    std::atomic_uint64_t* counter = nullptr;
    std::uint64_t started = 0;
};

void Put16(std::uint8_t* p, std::uint16_t v) noexcept {
    p[0] = static_cast<std::uint8_t>(v);
    p[1] = static_cast<std::uint8_t>(v >> 8);
}
void Put32(std::uint8_t* p, std::uint32_t v) noexcept {
    for (unsigned index = 0; index != 4; ++index)
        p[index] = static_cast<std::uint8_t>(v >> (index * 8));
}
void Put64(std::uint8_t* p, std::uint64_t v) noexcept {
    for (unsigned index = 0; index != 8; ++index)
        p[index] = static_cast<std::uint8_t>(v >> (index * 8));
}
std::uint16_t Get16(const std::uint8_t* p) noexcept {
    return static_cast<std::uint16_t>(p[0] | (static_cast<std::uint16_t>(p[1]) << 8));
}
std::uint32_t Get32(const std::uint8_t* p) noexcept {
    std::uint32_t value = 0;
    for (unsigned index = 0; index != 4; ++index)
        value |= static_cast<std::uint32_t>(p[index]) << (index * 8);
    return value;
}
std::uint64_t Get64(const std::uint8_t* p) noexcept {
    std::uint64_t value = 0;
    for (unsigned index = 0; index != 8; ++index)
        value |= static_cast<std::uint64_t>(p[index]) << (index * 8);
    return value;
}
bool Fits(std::uint64_t offset, std::uint64_t length, std::uint64_t total) noexcept {
    return offset <= total && length <= total - offset;
}
bool Zero(const Uuid128& value) noexcept {
    return std::all_of(value.bytes.begin(), value.bytes.end(), [](std::uint8_t byte) { return byte == 0; });
}
bool Zero(const Sha256& value) noexcept {
    return std::all_of(value.bytes.begin(), value.bytes.end(), [](std::uint8_t byte) { return byte == 0; });
}
bool Equal(const Uuid128& left, const Uuid128& right) noexcept {
    return left.bytes == right.bytes;
}
bool Equal(const Route left, const Route right) noexcept {
    return left.documentId == right.documentId && left.windowHandle == right.windowHandle;
}
void Add(Bytes* output, ByteView bytes) {
    if (bytes.size != 0)
        output->insert(output->end(), bytes.data, bytes.data + static_cast<std::size_t>(bytes.size));
}
Bytes U64(std::uint64_t value) {
    Bytes bytes(8);
    Put64(bytes.data(), value);
    return bytes;
}
Bytes U32(std::uint32_t value) {
    Bytes bytes(4);
    Put32(bytes.data(), value);
    return bytes;
}
Bytes U16(std::uint16_t value) {
    Bytes bytes(2);
    Put16(bytes.data(), value);
    return bytes;
}
Bytes U8(std::uint8_t value) { return {value}; }
Bytes Id(const Uuid128& value) { return {value.bytes.begin(), value.bytes.end()}; }
Bytes Digest(const Sha256& value) { return {value.bytes.begin(), value.bytes.end()}; }
Bytes CanonicalBytes(ByteView value) {
    Bytes bytes(8);
    Put64(bytes.data(), value.size);
    Add(&bytes, value);
    return bytes;
}
Bytes CanonicalUtf16(const std::u16string& value) {
    Bytes bytes(8 + value.size() * 2);
    Put64(bytes.data(), static_cast<std::uint64_t>(value.size()));
    for (std::size_t index = 0; index != value.size(); ++index)
        Put16(bytes.data() + 8 + index * 2, static_cast<std::uint16_t>(value[index]));
    return bytes;
}

Sha256 DomainParts(const char* domain, const std::size_t domainBytes,
                   const std::vector<ByteView>& parts) noexcept {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    PUCHAR object = nullptr;
    Sha256 digest{};
    DWORD objectBytes = 0;
    DWORD actual = 0;
    bool ok = BCryptOpenAlgorithmProvider(
                  &algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0 &&
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<PUCHAR>(&objectBytes),
                          sizeof(objectBytes), &actual, 0) >= 0;
    if (ok) {
        object = static_cast<PUCHAR>(
            HeapAlloc(GetProcessHeap(), 0, objectBytes));
        ok = object != nullptr &&
            BCryptCreateHash(algorithm, &hash, object, objectBytes,
                             nullptr, 0, 0) >= 0;
    }
    const auto add = [&hash](const void* data, std::uint64_t bytes) noexcept {
        const auto* at = static_cast<const std::uint8_t*>(data);
        while (bytes != 0) {
            const ULONG count = static_cast<ULONG>(
                (std::min<std::uint64_t>)(bytes, MAXDWORD));
            if (BCryptHashData(hash, const_cast<PUCHAR>(at), count, 0) < 0)
                return false;
            at += count;
            bytes -= count;
        }
        return true;
    };
    ok = ok && add(domain, domainBytes);
    for (const ByteView part : parts)
        ok = ok && (part.size == 0 || part.data != nullptr) &&
            add(part.data, part.size);
    ok = ok && BCryptFinishHash(hash, digest.bytes.data(),
                                static_cast<ULONG>(digest.bytes.size()), 0) >= 0;
    if (hash != nullptr) BCryptDestroyHash(hash);
    if (object != nullptr) HeapFree(GetProcessHeap(), 0, object);
    if (algorithm != nullptr) BCryptCloseAlgorithmProvider(algorithm, 0);
    return ok ? digest : Sha256{};
}
Sha256 ChunkDigest(const Bytes& header, ByteView payload) noexcept {
    Bytes zeroed = header;
    std::fill(zeroed.begin() + 256, zeroed.begin() + 320, std::uint8_t{0});
    return DomainParts(kChunkDomain, sizeof(kChunkDomain) - 1,
                       {codec::View(zeroed), payload});
}
Sha256 ChainDigest(const Sha256& previous, const Sha256& chunk,
                   std::uint64_t sequence) noexcept {
    const Bytes encodedSequence = U64(sequence);
    return DomainParts(kChainDomain, sizeof(kChainDomain) - 1,
                       {{previous.bytes.data(), previous.bytes.size()},
                        {chunk.bytes.data(), chunk.bytes.size()},
                        codec::View(encodedSequence)});
}

bool KnownMessage(std::uint16_t raw) noexcept {
    return (raw >= 1 && raw <= 14) || (raw >= 101 && raw <= 112);
}
bool VersionFieldsZero(const Header& header) noexcept {
    return Zero(header.graph) && header.profileBits == 0 &&
        header.semanticRevision == 0 && header.layoutRevision == 0 &&
        header.locatorEpoch == 0 && Zero(header.observedSemanticRoot) &&
        Zero(header.layoutRoot) && Zero(header.captureRoot) &&
        (header.flags & (kFlagLayoutPresent | kFlagSemanticCertified)) == 0;
}
bool HasVersion(const Header& header) noexcept {
    return !Zero(header.graph) || header.semanticRevision != 0 || header.layoutRevision != 0 ||
        header.locatorEpoch != 0 || !Zero(header.observedSemanticRoot) ||
        !Zero(header.layoutRoot) || !Zero(header.captureRoot) ||
        (header.flags & (kFlagLayoutPresent | kFlagSemanticCertified)) != 0;
}
std::uint32_t VersionFlags(const Header& header) noexcept {
    return header.flags & (kFlagLayoutPresent | kFlagSemanticCertified);
}
bool FullVersionShape(const Header& h) noexcept {
    return !Zero(h.session) && !Zero(h.graph) && h.profileBits != 0 &&
        h.semanticRevision != 0 && h.layoutRevision != 0 && h.locatorEpoch != 0;
}
bool FullVersionHeader(const Header& h) noexcept {
    identity::GraphVersionV1 version{};
    version.profileBits = h.profileBits;
    version.semanticCertified = (h.flags & kFlagSemanticCertified) != 0;
    version.layoutPresent = (h.flags & kFlagLayoutPresent) != 0;
    version.documentSessionId = h.session;
    version.graphId = h.graph;
    version.semanticRevision = h.semanticRevision;
    version.layoutRevision = h.layoutRevision;
    version.locatorEpoch = h.locatorEpoch;
    version.observedSemanticRoot = h.observedSemanticRoot;
    version.layoutRoot = h.layoutRoot;
    version.captureRoot = h.captureRoot;
    return identity::ValidateGraphVersion(version) == identity::GraphVersionError::None;
}
bool GraphOpenVersionAbsent(const Header& h) noexcept {
    return Zero(h.graph) && h.semanticRevision == 0 && h.layoutRevision == 0 &&
        h.locatorEpoch == 0 && Zero(h.observedSemanticRoot) && Zero(h.layoutRoot) &&
        Zero(h.captureRoot) && VersionFlags(h) == 0;
}
bool Independent(const Header& h) noexcept {
    return h.sequence == 0 && Zero(h.previousChain);
}
bool ZeroRange(const Header& h) noexcept {
    return h.fragmentOffset == 0 && h.fragmentTotal == 0;
}

bool ValidateMessageShape(const Header& h) noexcept {
    const std::uint32_t version = VersionFlags(h);
    switch (h.message) {
    case MessageKind::Capabilities:
        return h.flags == kFlagTerminal && Zero(h.session) && VersionFieldsZero(h) &&
            Zero(h.cursorOrUpload) && Independent(h) && ZeroRange(h);
    case MessageKind::GraphOpenRequest:
        return h.flags == version && !Zero(h.session) && Zero(h.cursorOrUpload) &&
            Independent(h) && ZeroRange(h) &&
            (GraphOpenVersionAbsent(h) || FullVersionShape(h));
    case MessageKind::OpenReceipt:
        return h.flags == version && FullVersionShape(h) && !Zero(h.cursorOrUpload) &&
            Independent(h) && ZeroRange(h);
    case MessageKind::GraphNextRequest:
        return h.flags == version && FullVersionShape(h) && !Zero(h.cursorOrUpload) && ZeroRange(h);
    case MessageKind::GraphChunk:
        return (h.flags == version || h.flags == (version | kFlagFragmented)) &&
            FullVersionShape(h) && !Zero(h.cursorOrUpload) &&
            h.fragmentOffset <= h.fragmentTotal;
    case MessageKind::GraphTerminal:
        return h.flags == (version | kFlagTerminal) && FullVersionShape(h) &&
            !Zero(h.cursorOrUpload) && h.fragmentOffset == h.fragmentTotal;
    case MessageKind::BlobReadRequest:
    case MessageKind::BlobPackRequest:
        return h.flags == version && FullVersionShape(h) &&
            !Zero(h.cursorOrUpload) && Independent(h) && ZeroRange(h);
    case MessageKind::BlobChunk:
        return (h.flags == version || h.flags == (version | kFlagTerminal)) &&
            FullVersionShape(h) && !Zero(h.cursorOrUpload) && Independent(h) &&
            h.fragmentOffset <= h.fragmentTotal;
    case MessageKind::BlobPack:
        return h.flags == (version | kFlagTerminal) && FullVersionShape(h) &&
            !Zero(h.cursorOrUpload) && Independent(h) && ZeroRange(h);
    case MessageKind::Error:
        return h.flags == (version | kFlagError) && ZeroRange(h) &&
            (VersionFieldsZero(h) || FullVersionShape(h));
    case MessageKind::GraphCancelRequest:
    case MessageKind::GraphCloseRequest:
        return h.flags == 0 && !Zero(h.session) && !Zero(h.cursorOrUpload) &&
            VersionFieldsZero(h) && Independent(h) && ZeroRange(h);
    case MessageKind::CursorClosedReceipt:
        return h.flags == kFlagTerminal && !Zero(h.session) && !Zero(h.cursorOrUpload) &&
            VersionFieldsZero(h) && Independent(h) && ZeroRange(h);
    case MessageKind::PatchBeginRequest:
    case MessageKind::PatchBeginReceipt:
        return h.flags == (version | kFlagPatch) && FullVersionShape(h) &&
            (h.message == MessageKind::PatchBeginRequest ||
             (version & kFlagSemanticCertified) != 0) &&
            (h.message == MessageKind::PatchBeginRequest ? Zero(h.cursorOrUpload) : !Zero(h.cursorOrUpload)) &&
            Independent(h) && h.fragmentOffset == 0;
    case MessageKind::PatchChunkRequest:
        return (h.flags == (version | kFlagPatch) ||
                h.flags == (version | kFlagPatch | kFlagFragmented)) &&
            FullVersionShape(h) && (version & kFlagSemanticCertified) != 0 &&
            !Zero(h.cursorOrUpload) && h.fragmentOffset <= h.fragmentTotal;
    case MessageKind::PatchChunkReceipt:
        return h.flags == (version | kFlagPatch) && FullVersionShape(h) &&
            (version & kFlagSemanticCertified) != 0 &&
            !Zero(h.cursorOrUpload) && h.fragmentOffset <= h.fragmentTotal;
    case MessageKind::PatchCommitRequest:
    case MessageKind::PatchSealReceipt:
        return h.flags == (version | kFlagPatch | kFlagTerminal) && FullVersionShape(h) &&
            (version & kFlagSemanticCertified) != 0 && !Zero(h.cursorOrUpload) &&
            h.fragmentOffset == h.fragmentTotal;
    case MessageKind::PatchAbortRequest:
    case MessageKind::PatchAbortReceipt:
        return h.flags == (kFlagPatch | kFlagTerminal) && !Zero(h.session) &&
            !Zero(h.cursorOrUpload) && VersionFieldsZero(h) && h.fragmentOffset == 0;
    case MessageKind::PatchValidateRequest:
        return h.flags == (version | kFlagPatch) && FullVersionShape(h) &&
            (version & kFlagSemanticCertified) != 0 && !Zero(h.cursorOrUpload) &&
            Independent(h) && h.fragmentOffset == 0 && h.fragmentTotal == 0;
    case MessageKind::PatchValidationReceipt:
        return h.flags == (version | kFlagPatch | kFlagTerminal) &&
            FullVersionShape(h) && (version & kFlagSemanticCertified) != 0 &&
            !Zero(h.cursorOrUpload) && Independent(h) &&
            h.fragmentOffset == 0 && h.fragmentTotal == 0;
    case MessageKind::PatchApplyReceipt:
    case MessageKind::PatchApplyRequest:
    default:
        return false;
    }
}

const ParsedField* Find(const std::vector<ParsedField>& fields, FieldTag tag) noexcept {
    const auto found = std::find_if(fields.begin(), fields.end(),
        [tag](const ParsedField& field) { return field.tag == tag; });
    return found == fields.end() ? nullptr : &*found;
}
bool ExactField(const ParsedField* field, ScalarTag scalar, std::uint64_t bytes,
                bool required = true) noexcept {
    return field != nullptr && field->scalar == scalar && field->value.size == bytes &&
        field->elementCount == 1 && field->flags == (required ? 1U : 0U);
}
bool ExactTags(const std::vector<ParsedField>& fields,
               std::initializer_list<FieldTag> required,
               std::initializer_list<FieldTag> optional = {}) noexcept {
    for (const FieldTag tag : required) {
        const ParsedField* field = Find(fields, tag);
        if (field == nullptr || (field->flags & 1U) == 0) return false;
    }
    for (const ParsedField& field : fields) {
        const bool known = std::find(required.begin(), required.end(), field.tag) != required.end() ||
            std::find(optional.begin(), optional.end(), field.tag) != optional.end();
        if (!known) return false;
    }
    return true;
}

bool ReadVersion(const ParsedField* field, identity::GraphVersionV1* version,
                 const bool required = true) noexcept {
    return field != nullptr && field->scalar == ScalarTag::Struct &&
        field->flags == (required ? 1U : 0U) &&
        identity::DeserializeGraphVersion(field->value.data, field->value.size, version) ==
            identity::GraphVersionError::None;
}
bool ReadId(const ParsedField* field, Uuid128* id) noexcept {
    if (!ExactField(field, ScalarTag::UUID128, 16) || id == nullptr) return false;
    std::copy(field->value.data, field->value.data + 16, id->bytes.begin());
    return !Zero(*id);
}
bool ReadDigest(const ParsedField* field, Sha256* digest) noexcept {
    if (!ExactField(field, ScalarTag::SHA256, 32) || digest == nullptr) return false;
    std::copy(field->value.data, field->value.data + 32, digest->bytes.begin());
    return true;
}

Uuid128 MintUuid() noexcept {
    Uuid128 id{};
    if (!identity::SystemRandomBytes(nullptr, id.bytes.data(),
                                    static_cast<std::uint32_t>(id.bytes.size()))) return {};
    id.bytes[6] = static_cast<std::uint8_t>((id.bytes[6] & 0x0fU) | 0x40U);
    id.bytes[8] = static_cast<std::uint8_t>((id.bytes[8] & 0x3fU) | 0x80U);
    return id;
}

std::wstring UploadRoot() {
    wchar_t temporary[MAX_PATH + 1]{};
    const DWORD count = GetTempPathW(MAX_PATH, temporary);
    if (count == 0 || count > MAX_PATH) return L"";
    return std::wstring(temporary, count) + L"HancomGraphPatchUploads";
}

class StreamingSha256 final {
public:
    ~StreamingSha256() noexcept {
        if (hash_ != nullptr) BCryptDestroyHash(hash_);
        if (object_ != nullptr) HeapFree(GetProcessHeap(), 0, object_);
        if (algorithm_ != nullptr) BCryptCloseAlgorithmProvider(algorithm_, 0);
    }
    bool Open() noexcept {
        DWORD objectBytes = 0, actual = 0;
        if (BCryptOpenAlgorithmProvider(&algorithm_, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0 ||
            BCryptGetProperty(algorithm_, BCRYPT_OBJECT_LENGTH,
                reinterpret_cast<PUCHAR>(&objectBytes), sizeof(objectBytes), &actual, 0) < 0)
            return false;
        object_ = static_cast<PUCHAR>(HeapAlloc(GetProcessHeap(), 0, objectBytes));
        return object_ != nullptr &&
            BCryptCreateHash(algorithm_, &hash_, object_, objectBytes, nullptr, 0, 0) >= 0;
    }
    bool Add(const void* data, std::uint64_t bytes) noexcept {
        const auto* at = static_cast<const std::uint8_t*>(data);
        while (bytes != 0) {
            const ULONG chunk = static_cast<ULONG>((std::min<std::uint64_t>)(bytes, MAXDWORD));
            if (BCryptHashData(hash_, const_cast<PUCHAR>(at), chunk, 0) < 0) return false;
            at += chunk;
            bytes -= chunk;
        }
        return true;
    }
    bool Finish(Sha256* digest) noexcept {
        return digest != nullptr && BCryptFinishHash(hash_, digest->bytes.data(),
            static_cast<ULONG>(digest->bytes.size()), 0) >= 0;
    }
private:
    BCRYPT_ALG_HANDLE algorithm_ = nullptr;
    BCRYPT_HASH_HANDLE hash_ = nullptr;
    PUCHAR object_ = nullptr;
};

bool ReadPatchSource(void* const context, const std::uint64_t offset,
                     std::uint8_t* const buffer, const std::uint32_t requested,
                     std::uint32_t* const actual) noexcept {
    if (context == nullptr || buffer == nullptr || actual == nullptr) return false;
    LARGE_INTEGER position{};
    position.QuadPart = static_cast<LONGLONG>(offset);
    DWORD read = 0;
    if (SetFilePointerEx(*static_cast<HANDLE*>(context), position, nullptr,
                         FILE_BEGIN) == FALSE ||
        ReadFile(*static_cast<HANDLE*>(context), buffer, requested, &read,
                 nullptr) == FALSE) return false;
    *actual = read;
    return true;
}

bool PatchDigestFile(const std::wstring& path, std::uint64_t length, Sha256* digest) noexcept {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    StreamingSha256 hash;
    bool success = hash.Open() && hash.Add(kPatchRawDomain, sizeof(kPatchRawDomain) - 1);
    Bytes buffer(1U << 20);
    std::uint64_t remaining = length;
    while (success && remaining != 0) {
        const DWORD wanted = static_cast<DWORD>((std::min<std::uint64_t>)(remaining, buffer.size()));
        DWORD actual = 0;
        success = ReadFile(file, buffer.data(), wanted, &actual, nullptr) != FALSE && actual == wanted &&
            hash.Add(buffer.data(), actual);
        remaining -= success ? actual : 0;
    }
    if (success) success = hash.Finish(digest);
    CloseHandle(file);
    return success;
}

struct CapabilitySession final {
    identity::DocumentSessionId id{};
    Route route{};
    std::uint64_t requestedBits = 0;
    std::uint64_t negotiatedBits = 0;
};

struct BlobConsumption final {
    query::BlobSlice slice{};
    std::uint64_t nextOffset = 0;
    bool completed = false;
};

struct GraphCursor final {
    Uuid128 id{};
    identity::DocumentSessionId session{};
    identity::GraphVersionV1 version{};
    Route route{};
    query::QueryView view{};
    std::vector<BlobConsumption> blobConsumption{};
    Sha256 streamDigest{};
    GraphStreamSha256V1 streamHash{};
    std::wstring storageRoot{};
    CursorState state = CursorState::Active;
    std::uint64_t nextSequence = 1;
    std::uint64_t recordOffset = 0;
    std::uint16_t fieldOrdinal = 0;
    std::uint64_t fieldOffset = 0;
    std::uint64_t reconstructedOffset = 0;
    std::uint64_t planRecordOrdinal = 0;
    Bytes plannedRecord{};
    Sha256 previousResponseChain{};
    bool blobPackCompleted = false;
    Sha256 blobPackRequestDigest{};
    Bytes blobPackResponse{};
};

struct Upload final {
    Uuid128 id{};
    identity::GraphVersionV1 version{};
    Route route{};
    std::uint64_t declaredTotal = 0;
    Sha256 expectedRaw{};
    std::uint64_t accepted = 0;
    std::uint64_t nextRequestSequence = 1;
    Sha256 previousRequestChain{};
    std::uint64_t nextResponseSequence = 1;
    Sha256 previousResponseChain{};
    UploadState state = UploadState::Open;
    std::wstring path{};
};

class Registry final {
public:
    Registry() noexcept { StartupCleanup(); }
    ~Registry() noexcept { Clear(); }
    void StartupCleanup() noexcept {
        try {
            root_ = UploadRoot();
            if (root_.empty()) return;
            std::error_code ignored;
            std::filesystem::remove_all(root_, ignored);
        } catch (...) { root_.clear(); }
    }
    bool EnsureStorage() noexcept {
        if (root_.empty()) return false;
        std::error_code error;
        std::filesystem::create_directories(root_, error);
        return !error && std::filesystem::is_directory(root_, error) && !error;
    }
    void RemoveStorageIfIdle() noexcept {
        if (!uploads_.empty() || !cursors_.empty() || root_.empty()) return;
        std::error_code ignored;
        std::filesystem::remove_all(root_, ignored);
    }
    void Clear() noexcept {
        std::lock_guard<std::mutex> guard(lock_);
        for (const auto& item : uploads_) DeleteFileW(item.second.path.c_str());
        uploads_.clear();
        cursors_.clear();
        capabilitySessions_.clear();
        capabilityLookupHits_ = 0;
        capabilityLookupMisses_ = 0;
        failNextBlobResponseEncoding_ = false;
        failNextGraphResponseEncoding_ = false;
        RemoveStorageIfIdle();
    }
    std::mutex lock_{};
    std::wstring root_{};
    std::map<std::array<std::uint8_t, 16>, Upload> uploads_{};
    std::map<std::array<std::uint8_t, 16>, GraphCursor> cursors_{};
    std::vector<CapabilitySession> capabilitySessions_{};
    std::size_t capabilityLookupHits_ = 0;
    std::size_t capabilityLookupMisses_ = 0;
    bool failNextBlobResponseEncoding_ = false;
    bool failNextGraphResponseEncoding_ = false;
    DebugLifecycleCounters lifecycleCounters_{};
    std::vector<DebugLifecycleEvent> lifecycleEvents_{};
    std::uint64_t nextLifecycleSequence_ = 1;
};
Registry& Global() { static Registry registry; return registry; }

void RecordLifecycleEvent(Registry& registry,
                          const DebugLifecycleEventKind kind,
                          const Route route,
                          const identity::DocumentSessionId& session = {},
                          const std::uint64_t requestedBits = 0,
                          const std::uint64_t negotiatedBits = 0,
                          const std::uint64_t affectedSessions = 0) noexcept {
    DebugLifecycleEvent event{};
    event.sequence = registry.nextLifecycleSequence_++;
    event.kind = kind;
    event.route = route;
    event.session = session;
    event.requestedBits = requestedBits;
    event.negotiatedBits = negotiatedBits;
    event.affectedSessions = affectedSessions;
    try { registry.lifecycleEvents_.push_back(event); } catch (...) {}
}

Header ResponseHeader(MessageKind kind, const Upload& upload,
                      std::uint64_t sequence, const Sha256& previous) noexcept {
    Header header{};
    header.message = kind;
    header.flags = kFlagPatch;
    header.sequence = sequence;
    header.fragmentTotal = upload.declaredTotal;
    header.cursorOrUpload = upload.id;
    header.previousChain = previous;
    static_cast<void>(ApplyVersionToHeader(upload.version, &header));
    return header;
}

Bytes Receipt(Header header, const std::vector<Bytes>& fields) {
    Bytes payload;
    for (const Bytes& field : fields) Add(&payload, codec::View(field));
    Bytes frame;
    if (!EncodeFrame(header, codec::View(payload), &frame)) return {};
    return frame;
}

Bytes Unsupported(const Header* request) {
    return ErrorFrame(ErrorCode::UnsupportedCapability, E_NOTIMPL, 0,
                      u"capability is not active in protocol-15 Todo 5", request);
}

bool ParseRequest(ByteView frame, MessageKind expected, Header* header,
                  ByteView* payload, std::vector<ParsedField>* fields,
                  ErrorCode* error) noexcept {
    if (!DecodeFrame(frame, header, payload, error)) return false;
    if (header->message != expected) {
        *error = ErrorCode::BadMessageKind;
        return false;
    }
    return ParseFields(*payload, fields, error);
}

Bytes Failure(ErrorCode error, const Header* header, const char16_t* detail) {
    return ErrorFrame(error, E_INVALIDARG, 0, detail, header);
}

Bytes HandlePatchBegin(ByteView request, Route route) {
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, MessageKind::PatchBeginRequest, &header, &payload, &fields, &error))
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header, u"invalid PatchBegin request");
    if (!ExactTags(fields, {1, 2, 3, 4})) return Failure(ErrorCode::BadField, &header, u"PatchBegin fields");
    Uuid128 session{};
    identity::GraphVersionV1 version{};
    Sha256 expected{};
    const ParsedField* totalField = Find(fields, 3);
    if (!ReadId(Find(fields, 1), &session) || !ReadVersion(Find(fields, 2), &version) ||
        !ExactField(totalField, ScalarTag::Uint64, 8) || !ReadDigest(Find(fields, 4), &expected) ||
        !Equal(session, version.documentSessionId) || !version.semanticCertified ||
        !HeaderMatchesVersion(header, version) ||
        header.sequence != 0 || !Zero(header.previousChain) || !Zero(header.cursorOrUpload) ||
        header.fragmentOffset != 0 || header.fragmentTotal != Get64(totalField->value.data))
        return Failure(ErrorCode::BadField, &header, u"PatchBegin mirror");
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    Upload upload{};
    upload.id = MintUuid();
    if (Zero(upload.id)) return Failure(ErrorCode::Internal, &header, u"upload UUID failure");
    if (!registry.EnsureStorage())
        return Failure(ErrorCode::StorageFailure, &header, u"upload storage unavailable");
    upload.version = version;
    upload.route = route;
    upload.declaredTotal = Get64(totalField->value.data);
    upload.expectedRaw = expected;
    upload.previousRequestChain = header.chainDigest;
    upload.path = registry.root_ + L"\\" + identity::FormatCanonicalUuid(upload.id) + L".open";
    HANDLE file = CreateFileW(upload.path.c_str(), GENERIC_WRITE | GENERIC_READ, 0, nullptr,
                              CREATE_NEW, FILE_ATTRIBUTE_TEMPORARY, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        registry.RemoveStorageIfIdle();
        return Failure(ErrorCode::StorageFailure, &header, u"upload create failed");
    }
    CloseHandle(file);
    Header response = ResponseHeader(MessageKind::PatchBeginReceipt, upload, 0, {});
    response.fragmentOffset = 0;
    const Bytes responseBytes = Receipt(response,
        {EncodeField(1, 1, ScalarTag::UUID128, codec::View(Id(upload.id))),
         EncodeField(2, 1, ScalarTag::Uint64, codec::View(U64(upload.declaredTotal))),
         EncodeField(3, 1, ScalarTag::SHA256, codec::View(Digest(upload.expectedRaw)))});
    Header parsed{};
    ByteView ignored{};
    if (responseBytes.empty() || !DecodeFrame(codec::View(responseBytes), &parsed, &ignored, &error)) {
        DeleteFileW(upload.path.c_str());
        registry.RemoveStorageIfIdle();
        return Failure(ErrorCode::Internal, &header, u"receipt encoding failed");
    }
    upload.previousResponseChain = parsed.chainDigest;
    registry.uploads_.emplace(upload.id.bytes, std::move(upload));
    return responseBytes;
}

Bytes HandlePatchChunk(ByteView request, Route route) {
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, MessageKind::PatchChunkRequest, &header, &payload, &fields, &error))
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header, u"invalid PatchChunk request");
    if (!ExactTags(fields, {1, 2, 3})) return Failure(ErrorCode::BadField, &header, u"PatchChunk fields");
    Uuid128 id{};
    const ParsedField* offsetField = Find(fields, 2);
    const ParsedField* bytesField = Find(fields, 3);
    if (!ReadId(Find(fields, 1), &id) || !ExactField(offsetField, ScalarTag::Uint64, 8) ||
        bytesField == nullptr || bytesField->flags != 1 || bytesField->scalar != ScalarTag::Bytes ||
        bytesField->value.size < 8 || Get64(bytesField->value.data) != bytesField->value.size - 8)
        return Failure(ErrorCode::BadField, &header, u"PatchChunk values");
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.uploads_.find(id.bytes);
    if (found == registry.uploads_.end()) return Failure(ErrorCode::UploadNotFound, &header, u"upload not found");
    Upload& upload = found->second;
    if (!Equal(upload.route, route)) {
        DeleteFileW(upload.path.c_str());
        registry.uploads_.erase(found);
        registry.RemoveStorageIfIdle();
        return Failure(ErrorCode::RouteChanged, &header, u"route changed");
    }
    if (upload.state != UploadState::Open) return Failure(ErrorCode::UploadState, &header, u"upload is not open");
    if (!HeaderMatchesVersion(header, upload.version) || !Equal(header.cursorOrUpload, upload.id))
        return Failure(ErrorCode::BadField, &header, u"PatchChunk mirror");
    if (header.sequence != upload.nextRequestSequence ||
        !codec::Equal(header.previousChain, upload.previousRequestChain))
        return Failure(ErrorCode::SequenceMismatch, &header, u"PatchChunk sequence");
    const std::uint64_t offset = Get64(offsetField->value.data);
    const std::uint64_t length = bytesField->value.size - 8;
    if (offset != upload.accepted || header.fragmentOffset != offset ||
        header.fragmentTotal != upload.declaredTotal ||
        length > upload.declaredTotal - upload.accepted ||
        (length == 0 && upload.accepted != upload.declaredTotal))
        return Failure(ErrorCode::LengthMismatch, &header, u"PatchChunk range");
    const bool more = offset + length < upload.declaredTotal;
    if (((header.flags & kFlagFragmented) != 0) != more)
        return Failure(ErrorCode::BadHeader, &header, u"PatchChunk fragmented flag");
    HANDLE file = CreateFileW(upload.path.c_str(), FILE_APPEND_DATA, 0, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_TEMPORARY, nullptr);
    if (file == INVALID_HANDLE_VALUE) return Failure(ErrorCode::StorageFailure, &header, u"upload open failed");
    DWORD written = 0;
    const bool wrote = length <= MAXDWORD &&
        WriteFile(file, bytesField->value.data + 8, static_cast<DWORD>(length), &written, nullptr) != FALSE &&
        written == length && FlushFileBuffers(file) != FALSE;
    CloseHandle(file);
    if (!wrote) return Failure(ErrorCode::StorageFailure, &header, u"upload write failed");
    upload.accepted += length;
    upload.previousRequestChain = header.chainDigest;
    ++upload.nextRequestSequence;
    Header response = ResponseHeader(MessageKind::PatchChunkReceipt, upload,
                                     upload.nextResponseSequence, upload.previousResponseChain);
    response.fragmentOffset = upload.accepted;
    const Bytes responseBytes = Receipt(response,
        {EncodeField(1, 1, ScalarTag::UUID128, codec::View(Id(upload.id))),
         EncodeField(2, 1, ScalarTag::Uint64, codec::View(U64(upload.accepted)))});
    Header parsed{};
    ByteView ignored{};
    if (!DecodeFrame(codec::View(responseBytes), &parsed, &ignored, &error))
        return Failure(ErrorCode::Internal, &header, u"receipt encoding failed");
    upload.previousResponseChain = parsed.chainDigest;
    ++upload.nextResponseSequence;
    return responseBytes;
}

Bytes HandlePatchCommit(ByteView request, Route route) {
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, MessageKind::PatchCommitRequest, &header, &payload, &fields, &error))
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header, u"invalid PatchCommit request");
    if (!ExactTags(fields, {1, 2, 3})) return Failure(ErrorCode::BadField, &header, u"PatchCommit fields");
    Uuid128 id{};
    Sha256 expected{};
    const ParsedField* totalField = Find(fields, 2);
    if (!ReadId(Find(fields, 1), &id) || !ExactField(totalField, ScalarTag::Uint64, 8) ||
        !ReadDigest(Find(fields, 3), &expected))
        return Failure(ErrorCode::BadField, &header, u"PatchCommit values");
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.uploads_.find(id.bytes);
    if (found == registry.uploads_.end()) return Failure(ErrorCode::UploadNotFound, &header, u"upload not found");
    Upload& upload = found->second;
    if (!Equal(upload.route, route)) {
        DeleteFileW(upload.path.c_str());
        registry.uploads_.erase(found);
        registry.RemoveStorageIfIdle();
        return Failure(ErrorCode::RouteChanged, &header, u"route changed");
    }
    if (upload.state != UploadState::Open) return Failure(ErrorCode::UploadState, &header, u"upload is not open");
    if (!HeaderMatchesVersion(header, upload.version) || !Equal(header.cursorOrUpload, upload.id))
        return Failure(ErrorCode::BadField, &header, u"PatchCommit mirror");
    if (header.sequence != upload.nextRequestSequence ||
        !codec::Equal(header.previousChain, upload.previousRequestChain))
        return Failure(ErrorCode::SequenceMismatch, &header, u"PatchCommit sequence");
    const std::uint64_t declared = Get64(totalField->value.data);
    if (declared != upload.declaredTotal || upload.accepted != upload.declaredTotal ||
        header.fragmentOffset != declared || header.fragmentTotal != declared)
        return Failure(ErrorCode::LengthMismatch, &header, u"PatchCommit length");
    if (!codec::Equal(expected, upload.expectedRaw))
        return Failure(ErrorCode::FinalDigestMismatch, &header, u"PatchCommit declared digest");
    Sha256 actual{};
    if (!PatchDigestFile(upload.path, upload.accepted, &actual))
        return Failure(ErrorCode::StorageFailure, &header, u"PatchCommit read failure");
    if (!codec::Equal(actual, upload.expectedRaw))
        return Failure(ErrorCode::FinalDigestMismatch, &header, u"PatchCommit raw digest");
    upload.previousRequestChain = header.chainDigest;
    ++upload.nextRequestSequence;
    upload.state = UploadState::Sealed;
    const std::wstring sealed = upload.path.substr(0, upload.path.size() - 5) + L".sealed";
    if (MoveFileExW(upload.path.c_str(), sealed.c_str(), MOVEFILE_WRITE_THROUGH) == FALSE) {
        upload.state = UploadState::Open;
        return Failure(ErrorCode::StorageFailure, &header, u"PatchCommit seal failure");
    }
    upload.path = sealed;
    Header response = ResponseHeader(MessageKind::PatchSealReceipt, upload,
                                     upload.nextResponseSequence, upload.previousResponseChain);
    response.flags |= kFlagTerminal;
    response.fragmentOffset = upload.accepted;
    const Bytes responseBytes = Receipt(response,
        {EncodeField(1, 1, ScalarTag::UUID128, codec::View(Id(upload.id))),
         EncodeField(2, 1, ScalarTag::Uint64, codec::View(U64(upload.accepted))),
         EncodeField(3, 1, ScalarTag::SHA256, codec::View(Digest(actual)))});
    Header parsed{};
    ByteView ignored{};
    if (!DecodeFrame(codec::View(responseBytes), &parsed, &ignored, &error))
        return Failure(ErrorCode::Internal, &header, u"receipt encoding failed");
    upload.previousResponseChain = parsed.chainDigest;
    ++upload.nextResponseSequence;
    return responseBytes;
}

Bytes HandlePatchValidate(const ByteView request, const Route route) {
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, MessageKind::PatchValidateRequest, &header,
                      &payload, &fields, &error))
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header,
                       u"invalid PatchValidate request");
    if (!ExactTags(fields, {1, 2}))
        return Failure(ErrorCode::BadField, &header, u"PatchValidate fields");
    Uuid128 id{};
    identity::GraphVersionV1 expected{};
    if (!ReadId(Find(fields, 1), &id) ||
        !ReadVersion(Find(fields, 2), &expected) ||
        !expected.semanticCertified || !HeaderMatchesVersion(header, expected) ||
        !Equal(header.cursorOrUpload, id) || header.sequence != 0 ||
        !Zero(header.previousChain) || header.fragmentOffset != 0 ||
        header.fragmentTotal != 0)
        return Failure(ErrorCode::BadField, &header, u"PatchValidate mirror");

    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto capability = std::find_if(
        registry.capabilitySessions_.begin(), registry.capabilitySessions_.end(),
        [&](const CapabilitySession& current) {
            return Equal(current.id, expected.documentSessionId) &&
                   Equal(current.route, route);
        });
    if (capability == registry.capabilitySessions_.end() ||
        (capability->negotiatedBits & kCapabilityPatchValidate) == 0)
        return Failure(ErrorCode::UnsupportedCapability, &header,
                       u"PatchValidate capability session is not open");
    const auto found = registry.uploads_.find(id.bytes);
    if (found == registry.uploads_.end())
        return Failure(ErrorCode::UploadNotFound, &header, u"upload not found");
    Upload& upload = found->second;
    if (!Equal(upload.route, route)) {
        DeleteFileW(upload.path.c_str());
        registry.uploads_.erase(found);
        registry.RemoveStorageIfIdle();
        return Failure(ErrorCode::RouteChanged, &header, u"route changed");
    }
    if (upload.state != UploadState::Sealed)
        return Failure(ErrorCode::UploadState, &header,
                       u"upload is not sealed");
    if (!identity::PatchVersionCas(expected, upload.version))
        return Failure(ErrorCode::StaleGraph, &header,
                       u"PatchValidate version changed");

    HANDLE file = CreateFileW(upload.path.c_str(), GENERIC_READ, FILE_SHARE_READ,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return Failure(ErrorCode::StorageFailure, &header,
                       u"sealed patch open failed");
    patch::SealedPatchValidationV1 parsed{};
    const patch::Error parsedError = patch::ParseSealedPatchV1(
        {&file, ReadPatchSource, upload.declaredTotal}, expected, &parsed);
    CloseHandle(file);
    if (parsedError != patch::Error::None) {
        const ErrorCode mapped = parsedError == patch::Error::StorageFailure
            ? ErrorCode::StorageFailure
            : (parsedError == patch::Error::CrossVersion
                ? ErrorCode::StaleGraph : ErrorCode::BadField);
        return Failure(mapped, &header, u"sealed patch validation failed");
    }
    patch::ValidationReceiptV1 validation{};
    validation.upload = upload.id;
    validation.canonicalDigest = parsed.canonicalDigest;
    validation.operationCount = parsed.operationCount;
    validation.inverseDigest = parsed.inverseDigest;
    Bytes receiptPayload;
    if (!patch::EncodeValidationReceiptPayloadV1(validation, &receiptPayload))
        return Failure(ErrorCode::Internal, &header,
                       u"PatchValidate receipt encoding failed");
    Header response{};
    response.message = MessageKind::PatchValidationReceipt;
    response.flags = kFlagPatch | kFlagTerminal;
    response.cursorOrUpload = upload.id;
    if (!ApplyVersionToHeader(upload.version, &response))
        return Failure(ErrorCode::Internal, &header,
                       u"PatchValidate version encoding failed");
    Bytes responseBytes;
    if (!EncodeFrame(response, codec::View(receiptPayload), &responseBytes))
        return Failure(ErrorCode::Internal, &header,
                       u"PatchValidate frame encoding failed");
    upload.state = UploadState::Validated;
    return responseBytes;
}

Bytes HandlePatchAbort(ByteView request, Route route) {
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, MessageKind::PatchAbortRequest, &header, &payload, &fields, &error))
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header, u"invalid PatchAbort request");
    if (!ExactTags(fields, {1})) return Failure(ErrorCode::BadField, &header, u"PatchAbort fields");
    Uuid128 id{};
    if (!ReadId(Find(fields, 1), &id)) return Failure(ErrorCode::BadField, &header, u"PatchAbort UUID");
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.uploads_.find(id.bytes);
    if (found == registry.uploads_.end()) return Failure(ErrorCode::UploadNotFound, &header, u"upload not found");
    Upload& upload = found->second;
    if (!Equal(upload.route, route)) {
        DeleteFileW(upload.path.c_str());
        registry.uploads_.erase(found);
        registry.RemoveStorageIfIdle();
        return Failure(ErrorCode::RouteChanged, &header, u"route changed");
    }
    if (upload.state != UploadState::Open && upload.state != UploadState::Sealed &&
        upload.state != UploadState::Validated)
        return Failure(ErrorCode::UploadState, &header, u"upload cannot be aborted");
    if (!Equal(header.session, upload.version.documentSessionId) ||
        !Equal(header.cursorOrUpload, upload.id) || !VersionFieldsZero(header) ||
        header.sequence != upload.nextRequestSequence ||
        !codec::Equal(header.previousChain, upload.previousRequestChain) ||
        header.fragmentOffset != 0 || header.fragmentTotal != upload.declaredTotal)
        return Failure(ErrorCode::SequenceMismatch, &header, u"PatchAbort chain");
    Header response{};
    response.message = MessageKind::PatchAbortReceipt;
    response.flags = kFlagPatch | kFlagTerminal;
    response.sequence = upload.nextResponseSequence;
    response.fragmentTotal = upload.declaredTotal;
    response.session = upload.version.documentSessionId;
    response.cursorOrUpload = upload.id;
    response.previousChain = upload.previousResponseChain;
    const Bytes responseBytes = Receipt(response,
        {EncodeField(1, 1, ScalarTag::UUID128, codec::View(Id(upload.id))),
         EncodeField(2, 1, ScalarTag::Uint8, codec::View(U8(static_cast<std::uint8_t>(UploadState::Aborted))))});
    DeleteFileW(upload.path.c_str());
    registry.uploads_.erase(found);
    registry.RemoveStorageIfIdle();
    return responseBytes;
}

} // namespace

namespace {
bool ValidateFieldValue(const std::uint16_t flags, const ScalarTag scalar,
                        const std::uint64_t elementCount, const ByteView value) noexcept {
    if ((flags & 2U) == 0)
        return elementCount == 1 &&
            (codec::ValidateCanonicalScalar(scalar, value) == codec::Error::None ||
             codec::ValidateObservation(value, scalar) == codec::Error::None);
    if (value.size < 16 || Get16(value.data) != static_cast<std::uint16_t>(scalar) ||
        Get16(value.data + 2) != 0 || Get32(value.data + 4) != 0 ||
        Get64(value.data + 8) != elementCount)
        return false;
    std::uint64_t offset = 16;
    for (std::uint64_t index = 0; index != elementCount; ++index) {
        if (!Fits(offset, 8, value.size)) return false;
        const std::uint64_t length = Get64(value.data + offset);
        offset += 8;
        if (!Fits(offset, length, value.size) ||
            codec::ValidateCanonicalScalar(scalar, {value.data + offset, length}) != codec::Error::None)
            return false;
        offset += length;
    }
    return offset == value.size;
}
} // namespace

bool EncodeCanonicalFieldHeaderV1(
    const FieldTag tag, const std::uint16_t flags, const ScalarTag scalar,
    const std::uint64_t elementCount, const std::uint64_t valueBytes,
    Bytes* const header) noexcept {
    if (header == nullptr || tag == 0 || (flags & ~3U) != 0 ||
        static_cast<std::uint16_t>(scalar) > 17 ||
        ((flags & 2U) == 0 && elementCount != 1))
        return false;
    try {
        header->assign(24, 0);
        Put16(header->data(), tag);
        Put16(header->data() + 2, flags);
        Put16(header->data() + 4, static_cast<std::uint16_t>(scalar));
        Put64(header->data() + 8, elementCount);
        Put64(header->data() + 16, valueBytes);
        return true;
    } catch (...) {
        header->clear();
        return false;
    }
}

codec::Bytes EncodeField(const FieldTag tag, const std::uint16_t flags,
                         const ScalarTag scalar, const ByteView value,
                         const std::uint64_t elementCount) {
    if (!ValidateFieldValue(flags, scalar, elementCount, value)) return {};
    Bytes bytes;
    if (!EncodeCanonicalFieldHeaderV1(tag, flags, scalar, elementCount,
                                      value.size, &bytes))
        return {};
    Add(&bytes, value);
    return bytes;
}

bool ParseFields(const ByteView payload, std::vector<ParsedField>* fields,
                 ErrorCode* error) noexcept {
    if (fields == nullptr || error == nullptr) return false;
    fields->clear();
    std::uint64_t offset = 0;
    FieldTag prior = 0;
    try {
        while (offset != payload.size) {
            if (!Fits(offset, 24, payload.size)) { *error = ErrorCode::BadField; return false; }
            const std::uint8_t* p = payload.data + offset;
            const FieldTag tag = Get16(p);
            const std::uint16_t flags = Get16(p + 2);
            const std::uint16_t scalar = Get16(p + 4);
            const std::uint64_t count = Get64(p + 8);
            const std::uint64_t length = Get64(p + 16);
            if (tag == 0 || (flags & ~3U) != 0 || Get16(p + 6) != 0 || scalar > 17 ||
                (!fields->empty() && tag <= prior) || ((flags & 2U) == 0 && count != 1)) {
                *error = ErrorCode::BadField; return false;
            }
            offset += 24;
            if (!Fits(offset, length, payload.size) ||
                !ValidateFieldValue(flags, static_cast<ScalarTag>(scalar), count,
                    {payload.data + offset, length})) {
                *error = ErrorCode::BadField; return false;
            }
            fields->push_back({tag, flags, static_cast<ScalarTag>(scalar), count,
                               {payload.data + offset, length}});
            offset += length;
            prior = tag;
        }
    } catch (...) { *error = ErrorCode::Internal; return false; }
    return true;
}

namespace {
bool FieldIdEquals(const ParsedField* field, const Uuid128& id) noexcept {
    return ExactField(field, ScalarTag::UUID128, 16) &&
        std::memcmp(field->value.data, id.bytes.data(), id.bytes.size()) == 0;
}
bool FieldU64Equals(const ParsedField* field, const std::uint64_t value) noexcept {
    return ExactField(field, ScalarTag::Uint64, 8) && Get64(field->value.data) == value;
}
bool PayloadVersion(const ParsedField* field, const Header& header,
                    identity::GraphVersionV1* version = nullptr,
                    const bool required = true) noexcept {
    identity::GraphVersionV1 parsed{};
    if (!ReadVersion(field, &parsed, required) || !HeaderMatchesVersion(header, parsed)) return false;
    if (version != nullptr) *version = parsed;
    return true;
}
bool ValidateFragmentStream(const Header& header, const ByteView payload) noexcept {
    if (payload.size < 56) return false;
    std::uint64_t at = 0, reconstructedBytes = 0, currentFieldBytes = 0;
    RecordId currentRecord = 0;
    FieldTag currentField = 0;
    std::uint16_t fieldCount = 0, fieldsSeen = 0;
    ScalarTag currentScalar = ScalarTag::Bytes;
    std::uint16_t currentValueFlags = 0;
    std::uint64_t currentElementCount = 0, currentFieldTotal = 0;
    bool haveRecord = false, recordStartedHere = false, recordEnded = false;
    std::vector<Bytes> completeRecordFragments;
    try {
        while (at != payload.size) {
            if (!Fits(at, 56, payload.size) || Get32(payload.data + at + 12) != 56) return false;
            const std::uint8_t* p = payload.data + at;
            const RecordKind kind = static_cast<RecordKind>(Get16(p));
            const std::uint16_t recordFlags = Get16(p + 2);
            const FieldTag fieldTag = Get16(p + 4);
            const std::uint16_t flags = Get16(p + 6);
            const ScalarTag scalar = static_cast<ScalarTag>(Get16(p + 8));
            const std::uint16_t count = Get16(p + 10);
            const RecordId record = Get64(p + 16);
            const std::uint64_t elements = Get64(p + 24);
            const std::uint64_t fieldTotal = Get64(p + 32);
            const std::uint64_t fieldOffset = Get64(p + 40);
            const std::uint64_t bytes = Get64(p + 48);
            if (static_cast<std::uint16_t>(kind) > 8 || (recordFlags & ~1U) != 0 ||
                fieldTag == 0 || (flags & ~0x3fU) != 0 ||
                static_cast<std::uint16_t>(scalar) > 17 || count == 0 ||
                !Fits(at + 56, bytes, payload.size) || !Fits(fieldOffset, bytes, fieldTotal) ||
                (fieldTotal > 0 && bytes == 0) ||
                (fieldTotal == 0 && (fieldOffset != 0 || bytes != 0 ||
                 (flags & (4U | 8U)) != (4U | 8U))))
                return false;
            const bool firstField = (flags & 4U) != 0;
            const bool lastField = (flags & 8U) != 0;
            const bool firstRecord = (flags & 16U) != 0;
            const bool lastRecord = (flags & 32U) != 0;
            if (firstField != (fieldOffset == 0) ||
                lastField != (fieldOffset + bytes == fieldTotal) ||
                (firstRecord && !firstField) || (lastRecord && !lastField)) return false;
            if (!haveRecord || record != currentRecord) {
                if ((!haveRecord && header.fragmentOffset == 0 &&
                     (!firstRecord || record != 0)) ||
                    (haveRecord && (!recordEnded || record != currentRecord + 1 || !firstRecord)))
                    return false;
                currentRecord = record;
                currentField = 0;
                fieldCount = count;
                fieldsSeen = 0;
                haveRecord = true;
                recordStartedHere = firstRecord;
                recordEnded = false;
                completeRecordFragments.clear();
                if (!firstField) {
                    currentField = fieldTag;
                    currentScalar = scalar;
                    currentValueFlags = flags & 3U;
                    currentElementCount = elements;
                    currentFieldTotal = fieldTotal;
                    currentFieldBytes = fieldOffset;
                }
            } else if (recordEnded || count != fieldCount || firstRecord) {
                return false;
            }
            if (firstField) {
                if (fieldTag <= currentField) return false;
                currentField = fieldTag;
                currentScalar = scalar;
                currentValueFlags = flags & 3U;
                currentElementCount = elements;
                currentFieldTotal = fieldTotal;
                currentFieldBytes = 0;
                ++fieldsSeen;
                reconstructedBytes += 24;
            } else if (fieldTag != currentField || scalar != currentScalar ||
                       (flags & 3U) != currentValueFlags || elements != currentElementCount ||
                       fieldTotal != currentFieldTotal || fieldOffset != currentFieldBytes) {
                return false;
            }
            if (firstRecord) reconstructedBytes += 24;
            currentFieldBytes += bytes;
            reconstructedBytes += bytes;
            completeRecordFragments.emplace_back(p, p + 56 + static_cast<std::size_t>(bytes));
            if (lastField) {
                if (fieldOffset == 0 && EncodeField(fieldTag, flags & 3U, scalar,
                    {p + 56, fieldTotal}, elements).empty()) return false;
            }
            if (lastRecord) {
                if (recordStartedHere && fieldsSeen != fieldCount) return false;
                if (recordStartedHere) {
                    Bytes logical;
                    ErrorCode error{};
                    if (!ReassembleFragments(completeRecordFragments, currentRecord, &logical, &error))
                        return false;
                }
                recordEnded = true;
            }
            at += 56 + bytes;
        }
    } catch (...) { return false; }
    if (!haveRecord || (header.fragmentOffset == 0 && !recordStartedHere)) return false;
    if (!Fits(header.fragmentOffset, reconstructedBytes, header.fragmentTotal)) return false;
    const bool more = header.fragmentOffset + reconstructedBytes < header.fragmentTotal;
    if (((header.flags & kFlagFragmented) != 0) != more) return false;
    return more || recordEnded;
}
bool ValidatePayloadShape(const Header& h, const ByteView payload) noexcept {
    const bool versionBound = h.message == MessageKind::OpenReceipt ||
        h.message == MessageKind::GraphNextRequest || h.message == MessageKind::GraphChunk ||
        h.message == MessageKind::GraphTerminal ||
        h.message == MessageKind::BlobReadRequest ||
        h.message == MessageKind::BlobPackRequest ||
        h.message == MessageKind::BlobChunk || h.message == MessageKind::BlobPack ||
        h.message == MessageKind::PatchBeginRequest ||
        h.message == MessageKind::PatchBeginReceipt || h.message == MessageKind::PatchChunkRequest ||
        h.message == MessageKind::PatchChunkReceipt || h.message == MessageKind::PatchCommitRequest ||
        h.message == MessageKind::PatchSealReceipt ||
        h.message == MessageKind::PatchValidateRequest ||
        h.message == MessageKind::PatchValidationReceipt ||
        (h.message == MessageKind::GraphOpenRequest && HasVersion(h)) ||
        (h.message == MessageKind::Error && HasVersion(h));
    if (versionBound && !FullVersionHeader(h)) return false;
    if (h.message == MessageKind::GraphChunk) return ValidateFragmentStream(h, payload);
    std::vector<ParsedField> fields;
    ErrorCode error{};
    if (!ParseFields(payload, &fields, &error)) return false;
    identity::GraphVersionV1 version{};
    switch (h.message) {
    case MessageKind::Capabilities:
        return ExactTags(fields, {1, 2, 3, 4, 5, 6}) &&
            ExactField(Find(fields, 1), ScalarTag::Uint32, 4) && Get32(Find(fields, 1)->value.data) == kGraphProtocolVersion &&
            ExactField(Find(fields, 2), ScalarTag::Uint16, 2) && Get16(Find(fields, 2)->value.data) == kFrameSchema &&
            FieldU64Equals(Find(fields, 3), kMaximumFrameBytes) &&
            FieldU64Equals(Find(fields, 4), kMaximumPayloadBytes) &&
            FieldU64Equals(Find(fields, 5), kMaximumTotalBytes) &&
            FieldU64Equals(Find(fields, 6), kCapabilityBits);
    case MessageKind::GraphOpenRequest: {
        const ParsedField* profileField = Find(fields, 3);
        const ParsedField* queryField = Find(fields, 4);
        if (!ExactTags(fields, {1, 3, 4, 5}, {2}) ||
            !FieldIdEquals(Find(fields, 1), h.session) ||
            !ExactField(profileField, ScalarTag::Uint64, 8) || queryField == nullptr ||
            queryField->flags != 1 || queryField->scalar != ScalarTag::Struct ||
            !ExactField(Find(fields, 5), ScalarTag::Uint64, 8)) return false;
        const std::uint64_t closedProfiles = Get64(profileField->value.data);
        QueryV1 query{};
        if (!DecodeQueryV1(queryField->value, &query, &error) ||
            !ValidateQueryV1(query, closedProfiles, &error)) return false;
        const ParsedField* expected = Find(fields, 2);
        if (expected == nullptr)
            return GraphOpenVersionAbsent(h) && h.profileBits == closedProfiles;
        identity::GraphVersionV1 source{};
        return PayloadVersion(expected, h, &source, false) &&
            (closedProfiles & ~source.profileBits) == 0;
    }
    case MessageKind::OpenReceipt:
        return ExactTags(fields, {1, 2}) && FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            PayloadVersion(Find(fields, 2), h);
    case MessageKind::GraphNextRequest:
        return ExactTags(fields, {1, 2, 3, 4, 5}) &&
            FieldIdEquals(Find(fields, 1), h.cursorOrUpload) && PayloadVersion(Find(fields, 2), h) &&
            ExactField(Find(fields, 3), ScalarTag::Uint64, 8) &&
            FieldU64Equals(Find(fields, 4), h.sequence) &&
            ExactField(Find(fields, 5), ScalarTag::SHA256, 32) &&
            std::memcmp(Find(fields, 5)->value.data, h.previousChain.bytes.data(), 32) == 0;
    case MessageKind::GraphTerminal:
        return ExactTags(fields, {1, 2, 3}) &&
            ExactField(Find(fields, 1), ScalarTag::Uint64, 8) &&
            FieldU64Equals(Find(fields, 2), h.fragmentTotal) &&
            ExactField(Find(fields, 3), ScalarTag::SHA256, 32);
    case MessageKind::BlobReadRequest:
        return ExactTags(fields, {1, 2, 3, 4, 5, 6, 7, 8}) &&
            FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            PayloadVersion(Find(fields, 2), h) &&
            ExactField(Find(fields, 3), ScalarTag::UUID128, 16) &&
            ExactField(Find(fields, 4), ScalarTag::Uint64, 8) &&
            ExactField(Find(fields, 5), ScalarTag::Uint64, 8) &&
            ExactField(Find(fields, 6), ScalarTag::SHA256, 32) &&
            ExactField(Find(fields, 7), ScalarTag::Uint64, 8) &&
            ExactField(Find(fields, 8), ScalarTag::Uint64, 8) &&
            Get64(Find(fields, 8)->value.data) >= 1 &&
            Get64(Find(fields, 8)->value.data) <= 32768;
    case MessageKind::BlobPackRequest:
        return ExactTags(fields, {1, 2, 3, 4, 5}) &&
            FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            PayloadVersion(Find(fields, 2), h) &&
            ExactField(Find(fields, 3), ScalarTag::UUID128, 16) &&
            Find(fields, 4) != nullptr && Find(fields, 4)->flags == 1 &&
            Find(fields, 4)->scalar == ScalarTag::Bytes &&
            Find(fields, 4)->value.size >= 8 &&
            ExactField(Find(fields, 5), ScalarTag::Uint64, 8) &&
            Get64(Find(fields, 5)->value.data) <= kMaximumPayloadBytes;
    case MessageKind::BlobChunk: {
        const ParsedField* bytes = Find(fields, 6);
        if (!ExactTags(fields, {1, 2, 3, 4, 5, 6}) ||
            !ExactField(Find(fields, 1), ScalarTag::UUID128, 16) ||
            !ExactField(Find(fields, 2), ScalarTag::Uint64, 8) ||
            !FieldU64Equals(Find(fields, 3), h.fragmentTotal) ||
            !ExactField(Find(fields, 4), ScalarTag::SHA256, 32) ||
            !FieldU64Equals(Find(fields, 5), h.fragmentOffset) ||
            bytes == nullptr || bytes->flags != 1 ||
            bytes->scalar != ScalarTag::Bytes || bytes->value.size < 8)
            return false;
        const std::uint64_t length = Get64(bytes->value.data);
        return length <= 32768 && length == bytes->value.size - 8 &&
            Fits(h.fragmentOffset, length, h.fragmentTotal) &&
            (((h.flags & kFlagTerminal) != 0) ==
             (h.fragmentOffset + length == h.fragmentTotal));
    }
    case MessageKind::BlobPack: {
        const ParsedField* bytes = Find(fields, 3);
        return ExactTags(fields, {1, 2, 3, 4}) &&
            ExactField(Find(fields, 1), ScalarTag::UUID128, 16) &&
            ExactField(Find(fields, 2), ScalarTag::Uint64, 8) &&
            bytes != nullptr && bytes->flags == 1 &&
            bytes->scalar == ScalarTag::Bytes && bytes->value.size >= 8 &&
            ExactField(Find(fields, 4), ScalarTag::SHA256, 32);
    }
    case MessageKind::Error:
        return ExactTags(fields, {1, 2, 3, 4}) &&
            ExactField(Find(fields, 1), ScalarTag::Uint32, 4) &&
            ExactField(Find(fields, 2), ScalarTag::Sint32, 4) &&
            ExactField(Find(fields, 3), ScalarTag::Uint64, 8) &&
            ExactField(Find(fields, 4), ScalarTag::UTF16, Find(fields, 4)->value.size);
    case MessageKind::GraphCancelRequest:
    case MessageKind::GraphCloseRequest:
        return ExactTags(fields, {1, 2}) && FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            FieldIdEquals(Find(fields, 2), h.session);
    case MessageKind::CursorClosedReceipt:
    case MessageKind::PatchAbortReceipt:
        return ExactTags(fields, {1, 2}) && FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            ExactField(Find(fields, 2), ScalarTag::Uint8, 1);
    case MessageKind::PatchBeginRequest:
        return ExactTags(fields, {1, 2, 3, 4}) && FieldIdEquals(Find(fields, 1), h.session) &&
            PayloadVersion(Find(fields, 2), h, &version) &&
            FieldU64Equals(Find(fields, 3), h.fragmentTotal) &&
            ExactField(Find(fields, 4), ScalarTag::SHA256, 32);
    case MessageKind::PatchBeginReceipt:
        return ExactTags(fields, {1, 2, 3}) && FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            FieldU64Equals(Find(fields, 2), h.fragmentTotal) &&
            ExactField(Find(fields, 3), ScalarTag::SHA256, 32);
    case MessageKind::PatchChunkRequest: {
        const ParsedField* bytes = Find(fields, 3);
        if (!ExactTags(fields, {1, 2, 3}) || !FieldIdEquals(Find(fields, 1), h.cursorOrUpload) ||
            !FieldU64Equals(Find(fields, 2), h.fragmentOffset) || bytes == nullptr ||
            bytes->flags != 1 || bytes->scalar != ScalarTag::Bytes || bytes->value.size < 8)
            return false;
        const std::uint64_t length = Get64(bytes->value.data);
        return length == bytes->value.size - 8 && Fits(h.fragmentOffset, length, h.fragmentTotal) &&
            (((h.flags & kFlagFragmented) != 0) == (h.fragmentOffset + length < h.fragmentTotal));
    }
    case MessageKind::PatchChunkReceipt:
        return ExactTags(fields, {1, 2}) && FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            FieldU64Equals(Find(fields, 2), h.fragmentOffset);
    case MessageKind::PatchCommitRequest:
        return ExactTags(fields, {1, 2, 3}) && FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            FieldU64Equals(Find(fields, 2), h.fragmentTotal) &&
            ExactField(Find(fields, 3), ScalarTag::SHA256, 32);
    case MessageKind::PatchSealReceipt:
        return ExactTags(fields, {1, 2, 3}) && FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            FieldU64Equals(Find(fields, 2), h.fragmentTotal) &&
            ExactField(Find(fields, 3), ScalarTag::SHA256, 32);
    case MessageKind::PatchAbortRequest:
        return ExactTags(fields, {1}) && FieldIdEquals(Find(fields, 1), h.cursorOrUpload);
    case MessageKind::PatchValidateRequest:
        return ExactTags(fields, {1, 2}) &&
            FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            PayloadVersion(Find(fields, 2), h, &version) &&
            version.semanticCertified;
    case MessageKind::PatchValidationReceipt:
        return ExactTags(fields, {1, 2, 3, 4, 5}) &&
            FieldIdEquals(Find(fields, 1), h.cursorOrUpload) &&
            ExactField(Find(fields, 2), ScalarTag::Uint8, 1) &&
            Find(fields, 2)->value.data[0] ==
                static_cast<std::uint8_t>(UploadState::Validated) &&
            ExactField(Find(fields, 3), ScalarTag::SHA256, 32) &&
            ExactField(Find(fields, 4), ScalarTag::Uint64, 8) &&
            ExactField(Find(fields, 5), ScalarTag::SHA256, 32);
    default:
        return false;
    }
}
} // namespace

namespace {
bool FinalizeFrameInPlace(const Header& source, Bytes* const frame) noexcept {
    if (frame == nullptr || frame->size() < kHeaderBytes ||
        frame->size() > kMaximumFrameBytes) return false;
    const ByteView payload{frame->data() + kHeaderBytes,
                           frame->size() - kHeaderBytes};
    if (source.payloadBytes != 0 && source.payloadBytes != payload.size)
        return false;
    Header header = source;
    header.payloadBytes = payload.size;
    if (!ValidateMessageShape(header) ||
        !ValidatePayloadShape(header, payload)) return false;
    std::uint8_t* const bytes = frame->data();
    std::fill_n(bytes, kHeaderBytes, std::uint8_t{0});
    std::copy_n(reinterpret_cast<const std::uint8_t*>("HGN1"), 4, bytes);
    Put16(bytes + 4, kFrameSchema);
    Put16(bytes + 6, static_cast<std::uint16_t>(header.message));
    Put32(bytes + 8, header.flags);
    Put32(bytes + 12, kHeaderBytes);
    Put64(bytes + 16, payload.size);
    Put64(bytes + 24, header.sequence);
    Put64(bytes + 32, header.fragmentOffset);
    Put64(bytes + 40, header.fragmentTotal);
    std::copy(header.session.bytes.begin(), header.session.bytes.end(), bytes + 48);
    std::copy(header.graph.bytes.begin(), header.graph.bytes.end(), bytes + 64);
    Put64(bytes + 80, header.profileBits);
    Put64(bytes + 88, header.semanticRevision);
    Put64(bytes + 96, header.layoutRevision);
    Put64(bytes + 104, header.locatorEpoch);
    std::copy(header.observedSemanticRoot.bytes.begin(), header.observedSemanticRoot.bytes.end(), bytes + 112);
    std::copy(header.layoutRoot.bytes.begin(), header.layoutRoot.bytes.end(), bytes + 144);
    std::copy(header.captureRoot.bytes.begin(), header.captureRoot.bytes.end(), bytes + 176);
    std::copy(header.cursorOrUpload.bytes.begin(), header.cursorOrUpload.bytes.end(), bytes + 208);
    std::copy(header.previousChain.bytes.begin(), header.previousChain.bytes.end(), bytes + 224);
    const std::uint8_t zeros[64]{};
    const Sha256 chunk = DomainParts(
        kChunkDomain, sizeof(kChunkDomain) - 1,
        {{bytes, 256}, {zeros, sizeof(zeros)}, payload});
    const Sha256 chain = ChainDigest(
        header.previousChain, chunk, header.sequence);
    std::copy(chunk.bytes.begin(), chunk.bytes.end(), bytes + 256);
    std::copy(chain.bytes.begin(), chain.bytes.end(), bytes + 288);
    return true;
}
} // namespace

bool EncodeFrame(const Header& source, const ByteView payload, Bytes* frame) noexcept {
    if (frame == nullptr || payload.size > kMaximumPayloadBytes ||
        (payload.size != 0 && payload.data == nullptr) ||
        payload.size > static_cast<std::uint64_t>(SIZE_MAX - kHeaderBytes))
        return false;
    try {
        Bytes bytes(static_cast<std::size_t>(kHeaderBytes + payload.size), 0);
        if (payload.size != 0)
            std::memcpy(bytes.data() + kHeaderBytes, payload.data,
                        static_cast<std::size_t>(payload.size));
        if (!FinalizeFrameInPlace(source, &bytes)) return false;
        *frame = std::move(bytes);
        return true;
    } catch (...) { return false; }
}

bool DecodeFrame(const ByteView frame, Header* header, ByteView* payload,
                 ErrorCode* error) noexcept {
    if (header == nullptr || payload == nullptr || error == nullptr) return false;
    *header = {};
    *payload = {};
    if (frame.size < kHeaderBytes || frame.size > kMaximumFrameBytes || frame.data == nullptr ||
        std::memcmp(frame.data, "HGN1", 4) != 0) { *error = ErrorCode::BadHeader; return false; }
    if (Get16(frame.data + 4) != kFrameSchema) { *error = ErrorCode::BadSchema; return false; }
    const std::uint16_t rawMessage = Get16(frame.data + 6);
    if (!KnownMessage(rawMessage)) { *error = ErrorCode::BadMessageKind; return false; }
    if (Get32(frame.data + 12) != kHeaderBytes) { *error = ErrorCode::BadHeader; return false; }
    Header parsed{};
    parsed.message = static_cast<MessageKind>(rawMessage);
    parsed.flags = Get32(frame.data + 8);
    parsed.payloadBytes = Get64(frame.data + 16);
    parsed.sequence = Get64(frame.data + 24);
    parsed.fragmentOffset = Get64(frame.data + 32);
    parsed.fragmentTotal = Get64(frame.data + 40);
    if ((parsed.flags & ~kKnownFlags) != 0 || parsed.payloadBytes > kMaximumPayloadBytes ||
        parsed.payloadBytes != frame.size - kHeaderBytes) { *error = ErrorCode::BadHeader; return false; }
    std::copy(frame.data + 48, frame.data + 64, parsed.session.bytes.begin());
    std::copy(frame.data + 64, frame.data + 80, parsed.graph.bytes.begin());
    parsed.profileBits = Get64(frame.data + 80);
    parsed.semanticRevision = Get64(frame.data + 88);
    parsed.layoutRevision = Get64(frame.data + 96);
    parsed.locatorEpoch = Get64(frame.data + 104);
    std::copy(frame.data + 112, frame.data + 144, parsed.observedSemanticRoot.bytes.begin());
    std::copy(frame.data + 144, frame.data + 176, parsed.layoutRoot.bytes.begin());
    std::copy(frame.data + 176, frame.data + 208, parsed.captureRoot.bytes.begin());
    std::copy(frame.data + 208, frame.data + 224, parsed.cursorOrUpload.bytes.begin());
    std::copy(frame.data + 224, frame.data + 256, parsed.previousChain.bytes.begin());
    std::copy(frame.data + 256, frame.data + 288, parsed.chunkDigest.bytes.begin());
    std::copy(frame.data + 288, frame.data + 320, parsed.chainDigest.bytes.begin());
    if (!ValidateMessageShape(parsed)) { *error = ErrorCode::BadHeader; return false; }
    const ByteView parsedPayload{frame.data + kHeaderBytes, parsed.payloadBytes};
    if (!ValidatePayloadShape(parsed, parsedPayload)) { *error = ErrorCode::BadField; return false; }
    Bytes headerBytes(frame.data, frame.data + kHeaderBytes);
    const Sha256 chunk = ChunkDigest(headerBytes, parsedPayload);
    const Sha256 chain = ChainDigest(parsed.previousChain, chunk, parsed.sequence);
    if (!codec::Equal(chunk, parsed.chunkDigest) || !codec::Equal(chain, parsed.chainDigest)) {
        *error = ErrorCode::DigestMismatch; return false;
    }
    *header = parsed;
    *payload = parsedPayload;
    return true;
}

Bytes CapabilitiesFrame() {
    const Bytes protocol = U32(kGraphProtocolVersion);
    const Bytes schema = U16(kFrameSchema);
    const Bytes maxFrame = U64(kMaximumFrameBytes);
    const Bytes maxPayload = U64(kMaximumPayloadBytes);
    const Bytes maxTotal = U64(kMaximumTotalBytes);
    const Bytes capability = U64(kCapabilityBits);
    Bytes payload;
    for (const Bytes& field : {
        EncodeField(1, 1, ScalarTag::Uint32, codec::View(protocol)),
        EncodeField(2, 1, ScalarTag::Uint16, codec::View(schema)),
        EncodeField(3, 1, ScalarTag::Uint64, codec::View(maxFrame)),
        EncodeField(4, 1, ScalarTag::Uint64, codec::View(maxPayload)),
        EncodeField(5, 1, ScalarTag::Uint64, codec::View(maxTotal)),
        EncodeField(6, 1, ScalarTag::Uint64, codec::View(capability))}) Add(&payload, codec::View(field));
    Header header{};
    header.message = MessageKind::Capabilities;
    header.flags = kFlagTerminal;
    Bytes frame;
    static_cast<void>(EncodeFrame(header, codec::View(payload), &frame));
    return frame;
}

Bytes ErrorFrame(const ErrorCode code, const std::int32_t hresult,
                 const std::uint64_t requiredBytes, const std::u16string& detail,
                 const Header* request) {
    const Bytes codeBytes = U32(static_cast<std::uint32_t>(code));
    const Bytes hrBytes = U32(static_cast<std::uint32_t>(hresult));
    const Bytes required = U64(requiredBytes);
    const Bytes text = CanonicalUtf16(detail);
    Bytes payload;
    for (const Bytes& field : {
        EncodeField(1, 1, ScalarTag::Uint32, codec::View(codeBytes)),
        EncodeField(2, 1, ScalarTag::Sint32, codec::View(hrBytes)),
        EncodeField(3, 1, ScalarTag::Uint64, codec::View(required)),
        EncodeField(4, 1, ScalarTag::UTF16, codec::View(text))}) Add(&payload, codec::View(field));
    Header header{};
    header.message = MessageKind::Error;
    header.flags = kFlagError;
    if (request != nullptr) {
        header.sequence = request->sequence;
        header.previousChain = request->previousChain;
        header.session = request->session;
        if (HasVersion(*request)) {
            header.graph = request->graph;
            header.profileBits = request->profileBits;
            header.semanticRevision = request->semanticRevision;
            header.layoutRevision = request->layoutRevision;
            header.locatorEpoch = request->locatorEpoch;
            header.observedSemanticRoot = request->observedSemanticRoot;
            header.layoutRoot = request->layoutRoot;
            header.captureRoot = request->captureRoot;
            header.flags |= VersionFlags(*request);
        }
        header.cursorOrUpload = request->cursorOrUpload;
    }
    Bytes frame;
    if (!EncodeFrame(header, codec::View(payload), &frame) || frame.size() > kMaximumErrorFrameBytes)
        return {};
    return frame;
}

bool ApplyVersionToHeader(const identity::GraphVersionV1& version,
                          Header* header) noexcept {
    if (header == nullptr || identity::ValidateGraphVersion(version) != identity::GraphVersionError::None)
        return false;
    header->session = version.documentSessionId;
    header->graph = version.graphId;
    header->profileBits = version.profileBits;
    header->semanticRevision = version.semanticRevision;
    header->layoutRevision = version.layoutRevision;
    header->locatorEpoch = version.locatorEpoch;
    header->observedSemanticRoot = version.observedSemanticRoot;
    header->layoutRoot = version.layoutRoot;
    header->captureRoot = version.captureRoot;
    header->flags &= ~(kFlagSemanticCertified | kFlagLayoutPresent);
    if (version.semanticCertified) header->flags |= kFlagSemanticCertified;
    if (version.layoutPresent) header->flags |= kFlagLayoutPresent;
    return true;
}

bool HeaderMatchesVersion(const Header& header,
                          const identity::GraphVersionV1& version) noexcept {
    Header expected{};
    return ApplyVersionToHeader(version, &expected) && Equal(header.session, expected.session) &&
        Equal(header.graph, expected.graph) && header.profileBits == expected.profileBits &&
        header.semanticRevision == expected.semanticRevision &&
        header.layoutRevision == expected.layoutRevision &&
        header.locatorEpoch == expected.locatorEpoch &&
        codec::Equal(header.observedSemanticRoot, expected.observedSemanticRoot) &&
        codec::Equal(header.layoutRoot, expected.layoutRoot) &&
        codec::Equal(header.captureRoot, expected.captureRoot) &&
        VersionFlags(header) == VersionFlags(expected);
}

bool DecodeQueryV1(const ByteView bytes, QueryV1* query,
                   ErrorCode* error) noexcept {
    if (query == nullptr || error == nullptr) return false;
    std::vector<ParsedField> fields;
    if (!ParseFields(bytes, &fields, error) ||
        !ExactTags(fields, {2, 3, 4, 7, 8, 9}, {1, 5, 6})) {
        *error = ErrorCode::BadField;
        return false;
    }
    QueryV1 parsed{};
    const ParsedField* root = Find(fields, 1);
    if (root != nullptr) {
        if (!ExactField(root, ScalarTag::UUID128, 16, false)) return false;
        std::copy(root->value.data, root->value.data + 16, parsed.root.bytes.begin());
        parsed.rootPresent = true;
    }
    const ParsedField* axis = Find(fields, 2);
    const ParsedField* nodes = Find(fields, 3);
    const ParsedField* edges = Find(fields, 4);
    const ParsedField* predicates = Find(fields, 7);
    const ParsedField* projection = Find(fields, 8);
    const ParsedField* order = Find(fields, 9);
    if (!ExactField(axis, ScalarTag::Uint8, 1) || axis->value.data[0] > 4 ||
        !ExactField(nodes, ScalarTag::Uint64, 8) ||
        !ExactField(edges, ScalarTag::Uint64, 8) ||
        predicates == nullptr || predicates->flags != 3 ||
        predicates->scalar != ScalarTag::Struct || predicates->value.size < 16 ||
        !ExactField(projection, ScalarTag::Uint64, 8) ||
        !ExactField(order, ScalarTag::Uint8, 1) || order->value.data[0] != 0)
        return false;
    parsed.axis = static_cast<Axis>(axis->value.data[0]);
    parsed.nodeKindBits = Get64(nodes->value.data);
    parsed.edgeKindBits = Get64(edges->value.data);
    parsed.projectionBits = Get64(projection->value.data);
    parsed.order = order->value.data[0];
    const ParsedField* first = Find(fields, 5);
    const ParsedField* last = Find(fields, 6);
    if (first != nullptr) {
        if (!ExactField(first, ScalarTag::Uint64, 8, false)) return false;
        parsed.firstPagePresent = true;
        parsed.firstPage = Get64(first->value.data);
    }
    if (last != nullptr) {
        if (!ExactField(last, ScalarTag::Uint64, 8, false)) return false;
        parsed.lastPagePresent = true;
        parsed.lastPage = Get64(last->value.data);
    }
    const std::uint8_t* array = predicates->value.data;
    if (Get16(array) != static_cast<std::uint16_t>(ScalarTag::Struct) ||
        Get16(array + 2) != 0 || Get32(array + 4) != 0 ||
        Get64(array + 8) != predicates->elementCount) return false;
    std::uint64_t at = 16;
    try {
        for (std::uint64_t index = 0; index != predicates->elementCount; ++index) {
            if (!Fits(at, 8, predicates->value.size)) return false;
            const std::uint64_t length = Get64(array + at);
            at += 8;
            if (!Fits(at, length, predicates->value.size)) return false;
            std::vector<ParsedField> predicateFields;
            if (!ParseFields({array + at, length}, &predicateFields, error) ||
                !ExactTags(predicateFields, {2, 3}, {1})) return false;
            QueryPredicateV1 predicate{};
            const ParsedField* key = Find(predicateFields, 1);
            if (key != nullptr) {
                if (!ExactField(key, ScalarTag::Uint32, 4, false)) return false;
                predicate.propertyKeyPresent = true;
                predicate.propertyKey = Get32(key->value.data);
            }
            const ParsedField* operation = Find(predicateFields, 2);
            const ParsedField* scalar = Find(predicateFields, 3);
            if (!ExactField(operation, ScalarTag::Uint8, 1) || operation->value.data[0] > 6 ||
                scalar == nullptr || scalar->flags != 1 || scalar->elementCount != 1)
                return false;
            predicate.operation = static_cast<QueryOperator>(operation->value.data[0]);
            predicate.scalar = scalar->scalar;
            predicate.canonicalScalar.assign(scalar->value.data,
                scalar->value.data + static_cast<std::size_t>(scalar->value.size));
            parsed.predicates.push_back(std::move(predicate));
            at += length;
        }
    } catch (...) { *error = ErrorCode::Internal; return false; }
    if (at != predicates->value.size) return false;
    *query = std::move(parsed);
    return true;
}

bool RequiredClosedProfileBits(const QueryV1& query, std::uint64_t* bits,
                               ErrorCode* error) noexcept {
    if (bits == nullptr || error == nullptr) return false;
    constexpr std::uint64_t knownProjection = (std::uint64_t{1} << 12) - 1;
    constexpr std::uint64_t knownNodes = (std::uint64_t{1} << 13) - 1;
    constexpr std::uint64_t knownEdges = (std::uint64_t{1} << 14) - 1;
    if ((query.projectionBits & ~knownProjection) != 0 ||
        (query.nodeKindBits & ~knownNodes) != 0 ||
        (query.edgeKindBits & ~knownEdges) != 0 || query.order != 0 ||
        static_cast<std::uint8_t>(query.axis) > 4 ||
        (query.axis != Axis::References && query.edgeKindBits != 0) ||
        query.firstPagePresent != query.lastPagePresent ||
        (query.firstPagePresent && (query.firstPage == 0 || query.lastPage < query.firstPage))) {
        *error = ErrorCode::BadField;
        return false;
    }
    std::uint64_t required = 0;
    const auto add = [&required](ProfileId profile) { required |= ProfileBit(profile); };
    const auto projection = [&query](Projection value) {
        return (query.projectionBits & (std::uint64_t{1} << static_cast<std::uint8_t>(value))) != 0;
    };
    if (projection(Projection::Structure) || projection(Projection::Coverage) ||
        projection(Projection::Diagnostics) || query.nodeKindBits != 0 ||
        query.edgeKindBits != 0) add(ProfileId::Structure);
    if (projection(Projection::Text) || projection(Projection::CharacterFormat) ||
        projection(Projection::ParagraphFormat)) add(ProfileId::EditableText);
    if (projection(Projection::ControlProperties) || projection(Projection::TableTopology) ||
        projection(Projection::CellFormat) || projection(Projection::ImageMetadata))
        add(ProfileId::EditableObjects);
    if (projection(Projection::BinaryContent)) add(ProfileId::BinaryContent);
    if (projection(Projection::Layout) || query.firstPagePresent) add(ProfileId::Layout);

    constexpr std::uint64_t structuralNodes =
        (std::uint64_t{1} << 0) | (std::uint64_t{1} << 1) | (std::uint64_t{1} << 2) |
        (std::uint64_t{1} << 3) | (std::uint64_t{1} << 7) | (std::uint64_t{1} << 8) |
        (std::uint64_t{1} << 9) | (std::uint64_t{1} << 10);
    constexpr std::uint64_t textNodes =
        (std::uint64_t{1} << 4) | (std::uint64_t{1} << 5) | (std::uint64_t{1} << 6);
    if ((query.nodeKindBits & structuralNodes) != 0) add(ProfileId::Structure);
    if ((query.nodeKindBits & textNodes) != 0) add(ProfileId::EditableText);
    if ((query.nodeKindBits & (std::uint64_t{1} << 11)) != 0) add(ProfileId::BinaryContent);
    if ((query.nodeKindBits & (std::uint64_t{1} << 12)) != 0) {
        add(ProfileId::Structure); add(ProfileId::EditableText); add(ProfileId::EditableObjects);
    }
    constexpr std::uint64_t structuralEdges =
        (std::uint64_t{1} << 0) | (std::uint64_t{1} << 1) | (std::uint64_t{1} << 7) |
        (std::uint64_t{1} << 8) | (std::uint64_t{1} << 9) | (std::uint64_t{1} << 12) |
        (std::uint64_t{1} << 13);
    constexpr std::uint64_t textEdges =
        (std::uint64_t{1} << 2) | (std::uint64_t{1} << 3) | (std::uint64_t{1} << 5) |
        (std::uint64_t{1} << 10) | (std::uint64_t{1} << 11);
    if ((query.edgeKindBits & structuralEdges) != 0) add(ProfileId::Structure);
    if ((query.edgeKindBits & textEdges) != 0) add(ProfileId::EditableText);
    if ((query.edgeKindBits & (std::uint64_t{1} << 4)) != 0) add(ProfileId::EditableObjects);
    if ((query.edgeKindBits & (std::uint64_t{1} << 6)) != 0) add(ProfileId::BinaryContent);

    for (const QueryPredicateV1& predicate : query.predicates) {
        if (static_cast<std::uint8_t>(predicate.operation) > 6 ||
            codec::ValidateCanonicalScalar(predicate.scalar,
                codec::View(predicate.canonicalScalar)) != codec::Error::None) {
            *error = ErrorCode::BadField; return false;
        }
        if (predicate.propertyKeyPresent) {
            const PropertyRule* rule = FindPropertyRule(predicate.propertyKey);
            if (rule == nullptr || predicate.scalar != rule->scalar) {
                *error = ErrorCode::BadField; return false;
            }
            required |= rule->profileBits;
        } else if (predicate.operation == QueryOperator::ContainsUTF16 &&
                   predicate.scalar == ScalarTag::UTF16) {
            add(ProfileId::EditableText);
        } else {
            *error = ErrorCode::BadField; return false;
        }
        const bool relational = predicate.operation >= QueryOperator::Lt &&
            predicate.operation <= QueryOperator::Ge;
        const bool numeric = predicate.scalar == ScalarTag::Sint64 ||
            predicate.scalar == ScalarTag::Uint64 || predicate.scalar == ScalarTag::Float64 ||
            predicate.scalar == ScalarTag::HWPUNIT64 || predicate.scalar == ScalarTag::BGR ||
            predicate.scalar == ScalarTag::RawURC32 || predicate.scalar == ScalarTag::Enum;
        if ((relational && !numeric) ||
            (predicate.operation == QueryOperator::ContainsUTF16 &&
             predicate.scalar != ScalarTag::UTF16)) {
            *error = ErrorCode::BadField; return false;
        }
    }
    *bits = CloseProfileBits(required);
    return true;
}

bool ValidateQueryV1(const QueryV1& query, const std::uint64_t closedProfileBits,
                     ErrorCode* error) noexcept {
    std::uint64_t required = 0;
    if (!RequiredClosedProfileBits(query, &required, error)) return false;
    if (required != closedProfileBits || !HasOnlyKnownProfiles(closedProfileBits) ||
        CloseProfileBits(closedProfileBits) != closedProfileBits) {
        *error = ErrorCode::BadField;
        return false;
    }
    return true;
}


namespace {
bool AppendEncodedFragment(const FragmentHeader& h, const ByteView bytes,
                           Bytes* const encoded) noexcept {
    if (encoded == nullptr || static_cast<std::uint16_t>(h.recordKind) > 8 ||
        (h.logicalRecordFlags & ~1U) != 0 || h.fieldTag == 0 ||
        (h.fieldFlags & ~0x3fU) != 0 ||
        static_cast<std::uint16_t>(h.scalar) > 17 || h.logicalRecordFieldCount == 0 ||
        h.fragmentBytes != bytes.size || h.fragmentOffset > h.totalFieldBytes ||
        h.fragmentBytes > h.totalFieldBytes - h.fragmentOffset ||
        (h.totalFieldBytes > 0 && h.fragmentBytes == 0) ||
        (h.totalFieldBytes == 0 && (h.fragmentOffset != 0 || h.fragmentBytes != 0 ||
         (h.fieldFlags & (4U | 8U)) != (4U | 8U)))) return false;
    try {
        const std::size_t at = encoded->size();
        if (bytes.size > SIZE_MAX - at - 56) return false;
        encoded->resize(at + 56 + static_cast<std::size_t>(bytes.size));
        std::uint8_t* const output = encoded->data() + at;
        std::fill_n(output, 56, std::uint8_t{0});
        Put16(output, static_cast<std::uint16_t>(h.recordKind));
        Put16(output + 2, h.logicalRecordFlags);
        Put16(output + 4, h.fieldTag);
        Put16(output + 6, h.fieldFlags);
        Put16(output + 8, static_cast<std::uint16_t>(h.scalar));
        Put16(output + 10, h.logicalRecordFieldCount);
        Put32(output + 12, 56);
        Put64(output + 16, h.recordId);
        Put64(output + 24, h.elementCount);
        Put64(output + 32, h.totalFieldBytes);
        Put64(output + 40, h.fragmentOffset);
        Put64(output + 48, h.fragmentBytes);
        if (bytes.size != 0)
            std::memcpy(output + 56, bytes.data,
                        static_cast<std::size_t>(bytes.size));
        return true;
    } catch (...) { return false; }
}
} // namespace

bool EncodeFragment(const FragmentHeader& h, const ByteView bytes,
                    Bytes* encoded) noexcept {
    if (encoded == nullptr) return false;
    encoded->clear();
    return AppendEncodedFragment(h, bytes, encoded);
}

bool ReassembleFragments(const std::vector<Bytes>& fragments,
                         const RecordId expectedRecordId,
                         Bytes* logicalRecord, ErrorCode* error) noexcept {
    if (logicalRecord == nullptr || error == nullptr || fragments.empty()) return false;
    struct AssembledField { FragmentHeader header{}; Bytes value{}; };
    std::vector<AssembledField> fields;
    RecordKind kind = RecordKind::Manifest;
    std::uint16_t recordFlags = 0, fieldCount = 0;
    bool firstRecord = false, lastRecord = false;
    try {
        for (std::size_t fragmentIndex = 0; fragmentIndex != fragments.size(); ++fragmentIndex) {
            const Bytes& fragment = fragments[fragmentIndex];
            if (fragment.size() < 56 || Get32(fragment.data() + 12) != 56) {
                *error = ErrorCode::BadField; return false;
            }
            FragmentHeader h{};
            h.recordKind = static_cast<RecordKind>(Get16(fragment.data()));
            h.logicalRecordFlags = Get16(fragment.data() + 2);
            h.fieldTag = Get16(fragment.data() + 4);
            h.fieldFlags = Get16(fragment.data() + 6);
            h.scalar = static_cast<ScalarTag>(Get16(fragment.data() + 8));
            h.logicalRecordFieldCount = Get16(fragment.data() + 10);
            h.recordId = Get64(fragment.data() + 16);
            h.elementCount = Get64(fragment.data() + 24);
            h.totalFieldBytes = Get64(fragment.data() + 32);
            h.fragmentOffset = Get64(fragment.data() + 40);
            h.fragmentBytes = Get64(fragment.data() + 48);
            if (h.recordId != expectedRecordId || static_cast<std::uint16_t>(h.recordKind) > 8 ||
                (h.logicalRecordFlags & ~1U) != 0 || (h.fieldFlags & ~0x3fU) != 0 ||
                static_cast<std::uint16_t>(h.scalar) > 17 || h.fieldTag == 0 ||
                h.logicalRecordFieldCount == 0 || h.fragmentBytes != fragment.size() - 56 ||
                h.fragmentOffset > h.totalFieldBytes ||
                h.fragmentBytes > h.totalFieldBytes - h.fragmentOffset ||
                (h.totalFieldBytes > 0 && h.fragmentBytes == 0) ||
                (h.totalFieldBytes == 0 && (h.fragmentOffset != 0 || h.fragmentBytes != 0 ||
                 (h.fieldFlags & (4U | 8U)) != (4U | 8U)))) {
                *error = ErrorCode::BadField; return false;
            }
            if (fields.empty()) {
                kind = h.recordKind;
                recordFlags = h.logicalRecordFlags;
                fieldCount = h.logicalRecordFieldCount;
            }
            if (h.recordKind != kind || h.logicalRecordFlags != recordFlags ||
                h.logicalRecordFieldCount != fieldCount) {
                *error = ErrorCode::BadField; return false;
            }
            const bool firstField = (h.fieldFlags & 4U) != 0;
            const bool lastField = (h.fieldFlags & 8U) != 0;
            if (firstField != (h.fragmentOffset == 0) ||
                lastField != (h.fragmentOffset + h.fragmentBytes == h.totalFieldBytes)) {
                *error = ErrorCode::LengthMismatch; return false;
            }
            if (firstField) {
                if (!fields.empty() && h.fieldTag <= fields.back().header.fieldTag) {
                    *error = ErrorCode::BadField; return false;
                }
                AssembledField field{};
                field.header = h;
                fields.push_back(std::move(field));
            }
            if (fields.empty()) { *error = ErrorCode::BadField; return false; }
            AssembledField& field = fields.back();
            if (!firstField && field.value.size() == field.header.totalFieldBytes) {
                *error = ErrorCode::LengthMismatch; return false;
            }
            if (field.header.fieldTag != h.fieldTag || field.header.scalar != h.scalar ||
                field.header.elementCount != h.elementCount ||
                field.header.totalFieldBytes != h.totalFieldBytes ||
                h.fragmentOffset != field.value.size() ||
                ((h.fieldFlags ^ field.header.fieldFlags) & 3U) != 0) {
                *error = ErrorCode::LengthMismatch; return false;
            }
            field.value.insert(field.value.end(), fragment.begin() + 56, fragment.end());
            if (lastField != (field.value.size() == h.totalFieldBytes)) {
                *error = ErrorCode::LengthMismatch; return false;
            }
            const bool hasFirstRecord = (h.fieldFlags & 16U) != 0;
            const bool hasLastRecord = (h.fieldFlags & 32U) != 0;
            const bool exactFirstRecord = fragmentIndex == 0 && firstField && fields.size() == 1;
            const bool exactLastRecord = fragmentIndex + 1 == fragments.size() && lastField &&
                fields.size() == fieldCount;
            if (hasFirstRecord != exactFirstRecord || hasLastRecord != exactLastRecord) {
                *error = ErrorCode::BadField; return false;
            }
            firstRecord = firstRecord || hasFirstRecord;
            lastRecord = lastRecord || hasLastRecord;
        }
        if (!firstRecord || !lastRecord || fields.size() != fieldCount) {
            *error = ErrorCode::LengthMismatch; return false;
        }
        Bytes fieldBytes;
        for (const AssembledField& field : fields) {
            const Bytes encoded = EncodeField(field.header.fieldTag,
                field.header.fieldFlags & 3U, field.header.scalar,
                codec::View(field.value), field.header.elementCount);
            if (encoded.empty()) { *error = ErrorCode::BadField; return false; }
            Add(&fieldBytes, codec::View(encoded));
        }
        Bytes result;
        if (!EncodeCanonicalRecordHeaderV1(kind, recordFlags, fieldCount,
                                           expectedRecordId, fieldBytes.size(),
                                           &result)) {
            *error = ErrorCode::Internal;
            return false;
        }
        Add(&result, codec::View(fieldBytes));
        *logicalRecord = std::move(result);
        return true;
    } catch (...) { *error = ErrorCode::Internal; return false; }
}

namespace {
constexpr std::uint32_t kSha256Round[64]{
    0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
    0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
    0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
    0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
    0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
    0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
    0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
    0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U,
};
std::uint32_t RotateRight(const std::uint32_t value, const unsigned bits) noexcept {
    return (value >> bits) | (value << (32U - bits));
}
void TransformStreamHash(GraphStreamSha256V1* hash, const std::uint8_t* block) noexcept {
    std::uint32_t words[64]{};
    for (unsigned index = 0; index != 16; ++index) {
        const std::uint8_t* p = block + index * 4;
        words[index] = (static_cast<std::uint32_t>(p[0]) << 24) |
            (static_cast<std::uint32_t>(p[1]) << 16) |
            (static_cast<std::uint32_t>(p[2]) << 8) | p[3];
    }
    for (unsigned index = 16; index != 64; ++index) {
        const std::uint32_t s0 = RotateRight(words[index - 15], 7) ^
            RotateRight(words[index - 15], 18) ^ (words[index - 15] >> 3);
        const std::uint32_t s1 = RotateRight(words[index - 2], 17) ^
            RotateRight(words[index - 2], 19) ^ (words[index - 2] >> 10);
        words[index] = words[index - 16] + s0 + words[index - 7] + s1;
    }
    std::uint32_t a=hash->state[0], b=hash->state[1], c=hash->state[2], d=hash->state[3];
    std::uint32_t e=hash->state[4], f=hash->state[5], g=hash->state[6], h=hash->state[7];
    for (unsigned index = 0; index != 64; ++index) {
        const std::uint32_t sum1 = RotateRight(e,6)^RotateRight(e,11)^RotateRight(e,25);
        const std::uint32_t choose = (e&f)^((~e)&g);
        const std::uint32_t temporary1 = h + sum1 + choose + kSha256Round[index] + words[index];
        const std::uint32_t sum0 = RotateRight(a,2)^RotateRight(a,13)^RotateRight(a,22);
        const std::uint32_t majority = (a&b)^(a&c)^(b&c);
        const std::uint32_t temporary2 = sum0 + majority;
        h=g; g=f; f=e; e=d+temporary1; d=c; c=b; b=a; a=temporary1+temporary2;
    }
    hash->state[0]+=a; hash->state[1]+=b; hash->state[2]+=c; hash->state[3]+=d;
    hash->state[4]+=e; hash->state[5]+=f; hash->state[6]+=g; hash->state[7]+=h;
}
void UpdateStreamHash(GraphStreamSha256V1* hash, const ByteView bytes) noexcept {
    const std::uint8_t* input = bytes.data;
    std::uint64_t remaining = bytes.size;
    hash->totalBytes += remaining;
    while (remaining != 0) {
        const std::uint32_t available = 64U - hash->bufferedBytes;
        const std::uint32_t count = static_cast<std::uint32_t>((std::min<std::uint64_t>)(remaining, available));
        std::memcpy(hash->block.data() + hash->bufferedBytes, input, count);
        hash->bufferedBytes += count;
        input += count;
        remaining -= count;
        if (hash->bufferedBytes == 64) {
            TransformStreamHash(hash, hash->block.data());
            hash->bufferedBytes = 0;
        }
    }
}
void InitializeStreamHash(GraphStreamSha256V1* hash) noexcept {
    hash->state = {0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,
                   0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U};
    hash->block.fill(0);
    hash->totalBytes = 0;
    hash->bufferedBytes = 0;
    hash->initialized = true;
    UpdateStreamHash(hash, {reinterpret_cast<const std::uint8_t*>(kStreamDomain),
                            sizeof(kStreamDomain) - 1});
}
Sha256 FinalizeStreamHash(const GraphStreamSha256V1& source) noexcept {
    GraphStreamSha256V1 hash = source;
    const std::uint64_t bitLength = hash.totalBytes * 8;
    const std::uint8_t marker = 0x80;
    UpdateStreamHash(&hash, {&marker, 1});
    const std::uint8_t zero = 0;
    while (hash.bufferedBytes != 56) UpdateStreamHash(&hash, {&zero, 1});
    std::uint8_t length[8]{};
    for (unsigned index = 0; index != 8; ++index)
        length[7 - index] = static_cast<std::uint8_t>(bitLength >> (index * 8));
    UpdateStreamHash(&hash, {length, 8});
    Sha256 digest{};
    for (unsigned word = 0; word != 8; ++word)
        for (unsigned byte = 0; byte != 4; ++byte)
            digest.bytes[word * 4 + byte] = static_cast<std::uint8_t>(hash.state[word] >> (24 - byte * 8));
    return digest;
}
bool SerializeHeaderVersion(const Header& header,
                            identity::SerializedGraphVersionV1* bytes) noexcept {
    identity::GraphVersionV1 version{};
    version.profileBits = header.profileBits;
    version.semanticCertified = (header.flags & kFlagSemanticCertified) != 0;
    version.layoutPresent = (header.flags & kFlagLayoutPresent) != 0;
    version.documentSessionId = header.session;
    version.graphId = header.graph;
    version.semanticRevision = header.semanticRevision;
    version.layoutRevision = header.layoutRevision;
    version.locatorEpoch = header.locatorEpoch;
    version.observedSemanticRoot = header.observedSemanticRoot;
    version.layoutRoot = header.layoutRoot;
    version.captureRoot = header.captureRoot;
    return identity::SerializeGraphVersion(version, bytes) == identity::GraphVersionError::None;
}
} // namespace

bool EncodeCanonicalRecordHeaderV1(
    const RecordKind kind, const std::uint16_t recordFlags,
    const std::uint16_t fieldCount, const RecordId recordId,
    const std::uint64_t encodedFieldBytes, Bytes* const header) noexcept {
    if (header == nullptr || fieldCount == 0) return false;
    try {
        header->assign(24, 0);
        Put16(header->data(), static_cast<std::uint16_t>(kind));
        Put16(header->data() + 2, 1);
        Put16(header->data() + 4, recordFlags);
        Put16(header->data() + 6, fieldCount);
        Put64(header->data() + 8, recordId);
        Put64(header->data() + 16, encodedFieldBytes);
        return true;
    } catch (...) {
        header->clear();
        return false;
    }
}

void InitializeGraphStreamHashV1(GraphStreamSha256V1* const hash) noexcept {
    if (hash != nullptr) InitializeStreamHash(hash);
}

bool UpdateGraphStreamHashV1(GraphStreamSha256V1* const hash,
                             const ByteView bytes) noexcept {
    if (hash == nullptr || !hash->initialized ||
        (bytes.size != 0 && bytes.data == nullptr) ||
        bytes.size > UINT64_MAX - hash->totalBytes)
        return false;
    UpdateStreamHash(hash, bytes);
    return true;
}

bool FinalizeGraphStreamHashV1(const GraphStreamSha256V1& hash,
                               Sha256* const digest) noexcept {
    if (!hash.initialized || digest == nullptr) return false;
    *digest = FinalizeStreamHash(hash);
    return true;
}

bool AcceptGraphResponseFrame(const ByteView frame, const std::uint64_t callerBudget,
                              GraphFragmentContinuationV1* continuation,
                              ErrorCode* error, std::uint64_t* requiredBytes) noexcept {
    if (continuation == nullptr || error == nullptr || requiredBytes == nullptr) return false;
    *requiredBytes = frame.size;
    if (callerBudget < kHeaderBytes || callerBudget > kMaximumFrameBytes) {
        *error = ErrorCode::BadField;
        return false;
    }
    if (frame.size > callerBudget) {
        *error = ErrorCode::BufferTooSmall;
        return false;
    }
    if (!continuation->cachedFrame.empty() && continuation->cachedFrame.size() == frame.size &&
        std::equal(continuation->cachedFrame.begin(), continuation->cachedFrame.end(), frame.data))
        return true;
    Header header{};
    ByteView payload{};
    if (!DecodeFrame(frame, &header, &payload, error)) return false;
    if (continuation->terminal) { *error = ErrorCode::CursorClosed; return false; }
    if (header.sequence != continuation->nextResponseSequence ||
        !codec::Equal(header.previousChain, continuation->previousChain)) {
        *error = ErrorCode::SequenceMismatch;
        return false;
    }
    if (header.fragmentOffset != continuation->nextReconstructedOffset) {
        *error = ErrorCode::LengthMismatch;
        return false;
    }
    try {
        GraphFragmentContinuationV1 next = *continuation;
        identity::SerializedGraphVersionV1 serializedVersion{};
        if (!SerializeHeaderVersion(header, &serializedVersion)) {
            *error = ErrorCode::BadField;
            return false;
        }
        if (!next.identityBound) {
            next.identityBound = true;
            next.boundCursor = header.cursorOrUpload;
            next.boundVersion = serializedVersion;
            InitializeGraphStreamHashV1(&next.streamHash);
        } else if (!Equal(next.boundCursor, header.cursorOrUpload)) {
            *error = ErrorCode::CursorNotFound;
            return false;
        } else if (next.boundVersion != serializedVersion) {
            *error = ErrorCode::StaleGraph;
            return false;
        }
        if (header.message == MessageKind::OpenReceipt) {
            if (continuation->identityBound || header.fragmentOffset != 0 ||
                header.fragmentTotal != 0) {
                *error = ErrorCode::BadField;
                return false;
            }
        } else if (header.message == MessageKind::GraphTerminal) {
            std::vector<ParsedField> fields;
            ErrorCode parseError{};
            if (next.active || header.fragmentOffset != header.fragmentTotal ||
                next.completedLogicalBytes != next.nextReconstructedOffset ||
                !ParseFields(payload, &fields, &parseError) || !ExactTags(fields, {1, 2, 3}) ||
                !FieldU64Equals(Find(fields, 1), next.completedRecordCount) ||
                !FieldU64Equals(Find(fields, 2), next.completedLogicalBytes) ||
                next.completedLogicalBytes != header.fragmentTotal ||
                !ExactField(Find(fields, 3), ScalarTag::SHA256, 32)) {
                *error = ErrorCode::LengthMismatch;
                return false;
            }
            Sha256 streamDigest{};
            if (!FinalizeGraphStreamHashV1(next.streamHash, &streamDigest)) {
                *error = ErrorCode::Internal;
                return false;
            }
            if (std::memcmp(Find(fields, 3)->value.data, streamDigest.bytes.data(), 32) != 0) {
                *error = ErrorCode::DigestMismatch;
                return false;
            }
            next.terminal = true;
        } else if (header.message == MessageKind::GraphChunk) {
            std::uint64_t at = 0;
            while (at != payload.size) {
                const std::uint8_t* p = payload.data + at;
                const RecordKind kind = static_cast<RecordKind>(Get16(p));
                const std::uint16_t recordFlags = Get16(p + 2);
                const FieldTag fieldTag = Get16(p + 4);
                const std::uint16_t flags = Get16(p + 6);
                const ScalarTag scalar = static_cast<ScalarTag>(Get16(p + 8));
                const std::uint16_t fieldCount = Get16(p + 10);
                const RecordId recordId = Get64(p + 16);
                const std::uint64_t elementCount = Get64(p + 24);
                const std::uint64_t totalFieldBytes = Get64(p + 32);
                const std::uint64_t fieldOffset = Get64(p + 40);
                const std::uint64_t fragmentBytes = Get64(p + 48);
                const bool firstField = (flags & 4U) != 0;
                const bool lastField = (flags & 8U) != 0;
                const bool firstRecord = (flags & 16U) != 0;
                const bool lastRecord = (flags & 32U) != 0;
                if (!next.active) {
                    if (recordId != next.nextRecordId || !firstRecord || !firstField) {
                        *error = ErrorCode::BadField; return false;
                    }
                    next.active = true;
                    next.currentRecordId = recordId;
                    next.currentRecordKind = kind;
                    next.logicalRecordFlags = recordFlags;
                    next.logicalRecordFieldCount = fieldCount;
                    next.currentFieldOrdinal = 0;
                    next.currentFieldTag = fieldTag;
                    next.currentFieldFlags = flags & 3U;
                    next.currentFieldScalar = scalar;
                    next.currentElementCount = elementCount;
                    next.totalFieldBytes = totalFieldBytes;
                    next.nextFieldFragmentOffset = 0;
                    next.currentFieldValue.clear();
                    next.currentRecordFieldBytes.clear();
                } else if (next.expectFirstFieldMarker) {
                    if (recordId != next.currentRecordId || kind != next.currentRecordKind ||
                        recordFlags != next.logicalRecordFlags ||
                        fieldCount != next.logicalRecordFieldCount || !firstField || firstRecord ||
                        fieldTag <= next.currentFieldTag) {
                        *error = ErrorCode::BadField; return false;
                    }
                    next.currentFieldTag = fieldTag;
                    next.currentFieldFlags = flags & 3U;
                    next.currentFieldScalar = scalar;
                    next.currentElementCount = elementCount;
                    next.totalFieldBytes = totalFieldBytes;
                    next.nextFieldFragmentOffset = 0;
                    next.currentFieldValue.clear();
                } else if (recordId != next.currentRecordId || kind != next.currentRecordKind ||
                    recordFlags != next.logicalRecordFlags ||
                    fieldCount != next.logicalRecordFieldCount || fieldTag != next.currentFieldTag ||
                    (flags & 3U) != next.currentFieldFlags || scalar != next.currentFieldScalar ||
                    elementCount != next.currentElementCount ||
                    totalFieldBytes != next.totalFieldBytes || firstField || firstRecord ||
                    fieldOffset != next.nextFieldFragmentOffset) {
                    *error = ErrorCode::BadField; return false;
                }
                if (fieldOffset != next.nextFieldFragmentOffset ||
                    (totalFieldBytes > 0 && fragmentBytes == 0)) {
                    *error = ErrorCode::LengthMismatch; return false;
                }
                next.currentFieldValue.insert(next.currentFieldValue.end(), p + 56,
                    p + 56 + static_cast<std::size_t>(fragmentBytes));
                next.nextFieldFragmentOffset += fragmentBytes;
                next.nextReconstructedOffset += fragmentBytes;
                if (firstField) next.nextReconstructedOffset += 24;
                if (firstRecord) next.nextReconstructedOffset += 24;
                if (lastField) {
                    const Bytes encodedField = EncodeField(next.currentFieldTag,
                        next.currentFieldFlags, next.currentFieldScalar,
                        codec::View(next.currentFieldValue), next.currentElementCount);
                    if (next.nextFieldFragmentOffset != next.totalFieldBytes || encodedField.empty()) {
                        *error = ErrorCode::BadField; return false;
                    }
                    Add(&next.currentRecordFieldBytes, codec::View(encodedField));
                    if (lastRecord) {
                        if (next.currentFieldOrdinal + 1 != next.logicalRecordFieldCount) {
                            *error = ErrorCode::BadField; return false;
                        }
                        Bytes recordHeader;
                        if (!EncodeCanonicalRecordHeaderV1(
                                next.currentRecordKind, next.logicalRecordFlags,
                                next.logicalRecordFieldCount, next.currentRecordId,
                                next.currentRecordFieldBytes.size(), &recordHeader)) {
                            *error = ErrorCode::Internal;
                            return false;
                        }
                        if (next.completedLogicalBytes > kMaximumTotalBytes - recordHeader.size() ||
                            next.completedLogicalBytes + recordHeader.size() >
                                kMaximumTotalBytes - next.currentRecordFieldBytes.size()) {
                            *error = ErrorCode::LengthMismatch; return false;
                        }
                        if (!UpdateGraphStreamHashV1(&next.streamHash, codec::View(recordHeader)) ||
                            !UpdateGraphStreamHashV1(
                                &next.streamHash, codec::View(next.currentRecordFieldBytes))) {
                            *error = ErrorCode::Internal;
                            return false;
                        }
                        next.completedLogicalBytes += recordHeader.size() +
                            next.currentRecordFieldBytes.size();
                        ++next.completedRecordCount;
                        next.active = false;
                        next.nextRecordId = next.currentRecordId + 1;
                        next.expectFirstRecordMarker = true;
                        next.expectFirstFieldMarker = true;
                        next.currentFieldValue.clear();
                        next.currentRecordFieldBytes.clear();
                    } else {
                        if (next.currentFieldOrdinal + 1 >= next.logicalRecordFieldCount) {
                            *error = ErrorCode::BadField; return false;
                        }
                        ++next.currentFieldOrdinal;
                        next.expectFirstFieldMarker = true;
                        next.expectFirstRecordMarker = false;
                        next.currentFieldValue.clear();
                    }
                } else {
                    if (lastRecord) { *error = ErrorCode::BadField; return false; }
                    next.expectFirstFieldMarker = false;
                    next.expectFirstRecordMarker = false;
                }
                next.expectLastFieldMarker = next.nextFieldFragmentOffset == next.totalFieldBytes;
                next.expectLastRecordMarker = next.expectLastFieldMarker &&
                    next.currentFieldOrdinal + 1 == next.logicalRecordFieldCount;
                at += 56 + fragmentBytes;
            }
            if (next.nextReconstructedOffset > header.fragmentTotal ||
                (((header.flags & kFlagFragmented) != 0) !=
                 (next.nextReconstructedOffset < header.fragmentTotal))) {
                *error = ErrorCode::LengthMismatch; return false;
            }
        } else {
            *error = ErrorCode::BadMessageKind;
            return false;
        }
        ++next.nextResponseSequence;
        next.previousChain = header.chainDigest;
        next.cachedFrame.assign(frame.data, frame.data + static_cast<std::size_t>(frame.size));
        *continuation = std::move(next);
        return true;
    } catch (...) {
        *error = ErrorCode::Internal;
        return false;
    }
}

namespace {
bool ReadCapabilityNegotiation(const identity::DocumentSessionId& session,
                               const Route route,
                               const std::uint64_t capabilityBit,
                               bool* const negotiated) noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = std::find_if(
        registry.capabilitySessions_.begin(), registry.capabilitySessions_.end(),
        [&](const CapabilitySession& current) {
            return Equal(current.id, session) && Equal(current.route, route);
        });
    if (found == registry.capabilitySessions_.end()) {
        ++registry.capabilityLookupMisses_;
        ++registry.lifecycleCounters_.registryFindMisses;
        RecordLifecycleEvent(registry, DebugLifecycleEventKind::RegistryFindMiss,
                             route, session);
        return false;
    }
    ++registry.capabilityLookupHits_;
    ++registry.lifecycleCounters_.registryFindHits;
    RecordLifecycleEvent(registry, DebugLifecycleEventKind::RegistryFindHit,
                         route, session, found->requestedBits,
                         found->negotiatedBits);
    *negotiated = (found->negotiatedBits & capabilityBit) != 0;
    return true;
}

bool QueryForView(const QueryV1& wire, query::Query* const native) {
    if (native == nullptr) return false;
    *native = {};
    native->rootPresent = wire.rootPresent;
    native->root = wire.root;
    switch (wire.axis) {
    case Axis::Self: native->axis = query::Axis::Self; break;
    case Axis::Children: native->axis = query::Axis::Children; break;
    case Axis::Descendants: native->axis = query::Axis::Descendants; break;
    case Axis::DocumentOrder: native->axis = query::Axis::DocumentOrder; break;
    case Axis::References: native->axis = query::Axis::References; break;
    default: return false;
    }
    native->nodeKindBits = wire.nodeKindBits;
    native->edgeKindBits = wire.edgeKindBits;
    native->pageRangePresent = wire.firstPagePresent || wire.lastPagePresent;
    native->firstPage = wire.firstPagePresent ? wire.firstPage : 0;
    native->lastPage = wire.lastPagePresent ? wire.lastPage : UINT64_MAX;
    native->projectionBits = query::ProjectStructure;
    if ((wire.projectionBits & (UINT64_C(1) << 1)) != 0)
        native->projectionBits |= query::ProjectText;
    if ((wire.projectionBits & UINT64_C(0x1fc)) != 0)
        native->projectionBits |= query::ProjectProperties;
    if ((wire.projectionBits & (UINT64_C(1) << 8)) != 0)
        native->projectionBits |= query::ProjectAssets;
    if ((wire.projectionBits & (UINT64_C(1) << 9)) != 0)
        native->projectionBits |= query::ProjectLayout;
    if (wire.axis == Axis::References)
        native->projectionBits |= query::ProjectReferences;
    for (const QueryPredicateV1& predicate : wire.predicates) {
        if (!predicate.propertyKeyPresent) return false;
        query::PropertyPredicate converted{};
        converted.key = predicate.propertyKey;
        converted.operation = static_cast<query::Operator>(predicate.operation);
        converted.scalar = predicate.scalar;
        converted.canonicalValue = predicate.canonicalScalar;
        native->properties.push_back(std::move(converted));
    }
    return true;
}

bool VersionFromQueryView(const query::QueryView& view,
                          identity::GraphVersionV1* const version,
                          Bytes* const plannedManifest) noexcept {
    Bytes manifest;
    const Bytes* records = &view.records;
    if (view.rangePlan != nullptr) {
        if (!query::MaterializeQueryPlanRecord(view, 0, &manifest)) return false;
        records = &manifest;
    }
    if (version == nullptr || records->size() < kLogicalRecordHeaderBytesV1)
        return false;
    const std::uint64_t payloadBytes = Get64(records->data() + 16);
    if (payloadBytes > records->size() - kLogicalRecordHeaderBytesV1)
        return false;
    std::vector<ParsedField> fields;
    ErrorCode error{};
    if (!ParseFields({records->data() + kLogicalRecordHeaderBytesV1,
                      payloadBytes}, &fields, &error) ||
        !ReadVersion(Find(fields, 1), version))
        return false;
    if (plannedManifest != nullptr && view.rangePlan != nullptr)
        *plannedManifest = std::move(manifest);
    return true;
}

std::u16string CaptureFailureDetail(
    const capture::CaptureStatus status,
    const capture::CaptureDiagnostics& diagnostics) {
    std::u16string detail = u"authoritative graph capture failed;status=";
    const auto appendUnsigned = [&detail](std::uint64_t value) {
        char16_t digits[20]{};
        size_t count = 0;
        do {
            digits[count++] = static_cast<char16_t>(u'0' + value % 10);
            value /= 10;
        } while (value != 0);
        while (count != 0) detail.push_back(digits[--count]);
    };
    appendUnsigned(static_cast<std::uint8_t>(status));
    detail += u";stage=";
    appendUnsigned(static_cast<std::uint8_t>(diagnostics.stage));
    detail += u";attempt=";
    appendUnsigned(diagnostics.attempt);
    detail += u";reader=";
    appendUnsigned(diagnostics.reader);
    detail += u";mismatch_bits=";
    appendUnsigned(diagnostics.mismatchBits);
    const auto appendWide = [&detail](const std::wstring& value,
                                      const size_t maximum) {
        const size_t limit = (std::min)(value.size(), maximum);
        for (size_t index = 0; index < limit; ++index)
            detail.push_back(static_cast<char16_t>(value[index]));
    };
    const auto appendDigest = [&detail](const Sha256& digest) {
        constexpr char16_t hex[] = u"0123456789abcdef";
        for (const std::uint8_t value : digest.bytes) {
            detail.push_back(hex[value >> 4]);
            detail.push_back(hex[value & 0x0f]);
        }
    };
    if (diagnostics.firstDivergencePresent) {
        detail += u";first_record=";
        appendUnsigned(static_cast<std::uint16_t>(
            diagnostics.firstRecordKind));
        detail += u";node_kind=";
        appendUnsigned(static_cast<std::uint16_t>(
            diagnostics.firstNodeKind));
        detail += u";node=";
        appendWide(identity::FormatCanonicalUuid(
            diagnostics.firstNodeId), 36);
        detail += u";ordinal=";
        appendUnsigned(diagnostics.firstRecordOrdinal);
        detail += u";field=";
        appendUnsigned(diagnostics.firstField);
        detail += u";first_digest=";
        appendDigest(diagnostics.firstDigest);
        detail += u";second_digest=";
        appendDigest(diagnostics.secondDigest);
    }
    if (!diagnostics.failureDetail.empty()) {
        detail += u";failure=";
        appendWide(diagnostics.failureDetail, 160);
    }
    return detail;
}

Bytes HandleGraphOpen(
    ByteView request, const Route route, IDispatch* const captureProvider,
    const layout::LayoutEnvironmentPlatformV1* const environmentPlatform) {
    using Clock = std::chrono::steady_clock;
    const auto elapsedMicroseconds = [](const Clock::time_point started) {
        return static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::microseconds>(
                Clock::now() - started).count());
    };
    const Clock::time_point requestStarted = Clock::now();
    Registry& registry = Global();
    {
        std::lock_guard<std::mutex> guard(registry.lock_);
        ++registry.lifecycleCounters_.graphOpenCalls;
        RecordLifecycleEvent(registry, DebugLifecycleEventKind::GraphOpenHandled,
                             route);
    }
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, MessageKind::GraphOpenRequest, &header, &payload, &fields, &error))
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header, u"invalid GraphOpen request");
    if (!ExactTags(fields, {1, 3, 4, 5}, {2}))
        return Failure(ErrorCode::BadField, &header, u"GraphOpen fields");
    Uuid128 session{};
    const ParsedField* profiles = Find(fields, 3);
    const ParsedField* queryField = Find(fields, 4);
    const ParsedField* budget = Find(fields, 5);
    if (!ReadId(Find(fields, 1), &session) || !Equal(session, header.session) ||
        !ExactField(profiles, ScalarTag::Uint64, 8) || queryField == nullptr ||
        queryField->flags != 1 || queryField->scalar != ScalarTag::Struct ||
        !ExactField(budget, ScalarTag::Uint64, 8) || Get64(budget->value.data) < kHeaderBytes ||
        Get64(budget->value.data) > kMaximumFrameBytes)
        return Failure(ErrorCode::BadField, &header, u"GraphOpen values");
    const ParsedField* expected = Find(fields, 2);
    identity::GraphVersionV1 expectedVersion{};
    const bool hasExpectedVersion = expected != nullptr;
    if (!hasExpectedVersion) {
        if (!GraphOpenVersionAbsent(header) || header.profileBits != Get64(profiles->value.data))
            return Failure(ErrorCode::BadField, &header, u"unexpected GraphVersion");
    } else if (!ReadVersion(expected, &expectedVersion, false) ||
               !HeaderMatchesVersion(header, expectedVersion) ||
               (Get64(profiles->value.data) & ~expectedVersion.profileBits) != 0) {
        return Failure(ErrorCode::BadField, &header, u"GraphVersion mirror");
    }
    QueryV1 query{};
    if (!DecodeQueryV1(queryField->value, &query, &error) ||
        !ValidateQueryV1(query, Get64(profiles->value.data), &error))
        return Failure(ErrorCode::BadField, &header, u"QueryV1 closure");
    bool graphReadNegotiated = false;
    if (!ReadCapabilityNegotiation(
            session, route, kCapabilityGraphRead, &graphReadNegotiated))
        return Failure(ErrorCode::BadField, &header, u"capability session is not open");
    if (!graphReadNegotiated) return Unsupported(&header);
    if (captureProvider == nullptr)
        return Failure(ErrorCode::StorageFailure, &header,
                       u"GraphRead capture provider is unavailable");

    query::Query nativeQuery{};
    if (!QueryForView(query, &nativeQuery))
        return Failure(ErrorCode::BadField, &header, u"QueryV1 adapter");
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::GraphOpenRequestDecode,
        elapsedMicroseconds(requestStarted), request.size);
    const Uuid128 cursorId = MintUuid();
    if (Zero(cursorId))
        return Failure(ErrorCode::Internal, &header, u"cursor UUID failure");
    std::wstring graphRoot;
    bool reusedGeneration = false;
    {
        Registry& state = Global();
        std::lock_guard<std::mutex> guard(state.lock_);
        if (!state.EnsureStorage())
            return Failure(ErrorCode::StorageFailure, &header,
                           u"graph storage unavailable");
        identity::SerializedGraphVersionV1 expectedBytes{};
        if (hasExpectedVersion &&
            identity::SerializeGraphVersion(expectedVersion, &expectedBytes) ==
                identity::GraphVersionError::None) {
            for (const auto& item : state.cursors_) {
                identity::SerializedGraphVersionV1 candidateBytes{};
                if (Equal(item.second.session, session) &&
                    item.second.route.documentId == route.documentId &&
                    item.second.route.windowHandle == route.windowHandle &&
                    identity::SerializeGraphVersion(
                        item.second.version, &candidateBytes) ==
                        identity::GraphVersionError::None &&
                    candidateBytes == expectedBytes) {
                    graphRoot = item.second.storageRoot;
                    reusedGeneration = true;
                    break;
                }
            }
        }
        if (!reusedGeneration) {
            graphRoot = state.root_ + L"\\graph-" +
                identity::FormatCanonicalUuid(cursorId);
            ++state.lifecycleCounters_.producerCalls;
        }
    }
    struct StorageGuard final {
        Registry* registry = nullptr;
        const std::wstring* root = nullptr;
        bool retained = false;
        ~StorageGuard() noexcept {
            if (retained || registry == nullptr || root == nullptr) return;
            std::error_code ignored;
            std::filesystem::remove_all(*root, ignored);
            std::lock_guard<std::mutex> guard(registry->lock_);
            registry->RemoveStorageIfIdle();
        }
    } storageGuard{&registry, &graphRoot, reusedGeneration};
    auto store = std::make_unique<store::GraphStore>(graphRoot);
    if (!store->Initialize())
        return Failure(ErrorCode::StorageFailure, &header,
                       u"graph store initialization failed");
    const Clock::time_point documentAccessStarted = Clock::now();
    if (!reusedGeneration) {
        capture::CaptureDiagnostics captureDiagnostics;
        const capture::CaptureStatus captureStatus =
            official_api::capability::CaptureDocumentGraphToStore(
                captureProvider, store.get(), environmentPlatform, &session,
                &captureDiagnostics);
        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::CaptureReturned,
            static_cast<std::uint64_t>(captureStatus), 0);
        if (captureStatus == capture::CaptureStatus::Cancelled)
            return Failure(ErrorCode::Cancelled, &header,
                           u"authoritative graph capture cancelled");
        if (captureStatus != capture::CaptureStatus::Complete) {
            const std::u16string detail = CaptureFailureDetail(
                captureStatus, captureDiagnostics);
            return Failure(ErrorCode::StorageFailure, &header,
                           detail.c_str());
        }
    }
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::GraphOpenDocumentAccess,
        elapsedMicroseconds(documentAccessStarted), reusedGeneration ? 1 : 0);
    const Clock::time_point stabilizationStarted = Clock::now();
    const store::GenerationPin published = store->PinActive();
    if (published == nullptr)
        return Failure(ErrorCode::StorageFailure, &header,
                       u"captured graph was not published");
    query::GraphStoreGenerationSource source(store.get());
    if (query::PrepareGenerationQueryIndex(&source) != query::Status::Terminal)
        return Failure(ErrorCode::StorageFailure, &header,
                       u"generation query index authentication failed");
    query::DocumentGraphQuery graphQuery(&source);
    query::QueryView view{};
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::BuildQueryViewStart,
        published->RecordsBytes(), published->IndexBytes());
    const query::Status viewStatus =
        query::BuildQueryPlan(&source, nativeQuery, &view, &session);
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::BuildQueryViewEnd,
        static_cast<std::uint64_t>(viewStatus), view.logicalBytes);
    if (viewStatus != query::Status::Terminal &&
        viewStatus != query::Status::Empty)
        return Failure(ErrorCode::StorageFailure, &header,
                       u"canonical query view failed");
    identity::GraphVersionV1 version{};
    Bytes plannedManifest;
    if (!VersionFromQueryView(view, &version, &plannedManifest) ||
        !Equal(version.documentSessionId, session))
        return Failure(ErrorCode::StorageFailure, &header,
                       u"query view version mismatch");
    if (hasExpectedVersion) {
        identity::SerializedGraphVersionV1 actualBytes{}, expectedBytes{};
        if (identity::SerializeGraphVersion(version, &actualBytes) !=
                identity::GraphVersionError::None ||
            identity::SerializeGraphVersion(expectedVersion, &expectedBytes) !=
                identity::GraphVersionError::None ||
            actualBytes != expectedBytes) {
            return Failure(ErrorCode::StaleGraph, &header,
                           u"query view version changed");
        }
    }

    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::GraphOpenStabilization,
        elapsedMicroseconds(stabilizationStarted), view.logicalBytes);
    const Clock::time_point serializationStarted = Clock::now();
    GraphCursor cursor{};
    cursor.id = cursorId;
    cursor.session = session;
    cursor.version = version;
    cursor.route = route;
    cursor.view = std::move(view);
    InitializeGraphStreamHashV1(&cursor.streamHash);
    cursor.plannedRecord = std::move(plannedManifest);
    cursor.storageRoot = graphRoot;
    const std::uint64_t viewBytes = cursor.view.logicalBytes;
    Header response{};
    response.message = MessageKind::OpenReceipt;
    response.cursorOrUpload = cursor.id;
    if (!ApplyVersionToHeader(version, &response))
        return Failure(ErrorCode::Internal, &header, u"version encoding failed");
    identity::SerializedGraphVersionV1 serialized{};
    if (identity::SerializeGraphVersion(version, &serialized) !=
        identity::GraphVersionError::None)
        return Failure(ErrorCode::Internal, &header, u"version serialization failed");
    const Bytes responseBytes = Receipt(
        response,
        {EncodeField(1, 1, ScalarTag::UUID128, codec::View(Id(cursor.id))),
         EncodeField(2, 1, ScalarTag::Struct,
                     {serialized.data(), serialized.size()})});
    Header parsed{};
    ByteView ignored{};
    if (responseBytes.empty() ||
        !DecodeFrame(codec::View(responseBytes), &parsed, &ignored, &error))
        return Failure(ErrorCode::Internal, &header, u"open receipt failed");
    cursor.previousResponseChain = parsed.chainDigest;
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::GraphOpenSerialization,
        elapsedMicroseconds(serializationStarted), responseBytes.size());
    {
        Registry& state = Global();
        std::lock_guard<std::mutex> guard(state.lock_);
        state.cursors_.emplace(cursor.id.bytes, std::move(cursor));
        ++state.lifecycleCounters_.publicationCalls;
        ++state.lifecycleCounters_.cursorAllocations;
        storageGuard.retained = true;
    }
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::GraphOpenNativeReturn,
        responseBytes.size(), viewBytes);
    return responseBytes;
}
Bytes HandleBlobPack(const ByteView request, const Route route) {
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, MessageKind::BlobPackRequest, &header, &payload,
                      &fields, &error)) {
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header,
                       u"invalid BlobPack request");
    }
    if (!ExactTags(fields, {1, 2, 3, 4, 5}))
        return Failure(ErrorCode::BadField, &header, u"BlobPack fields");
    Uuid128 cursorId{};
    Uuid128 transferId{};
    identity::GraphVersionV1 version{};
    const ParsedField* tuplesField = Find(fields, 4);
    const ParsedField* maximumField = Find(fields, 5);
    if (!ReadId(Find(fields, 1), &cursorId) ||
        !ReadVersion(Find(fields, 2), &version) ||
        !ReadId(Find(fields, 3), &transferId) ||
        !HeaderMatchesVersion(header, version) ||
        !Equal(cursorId, header.cursorOrUpload) || tuplesField == nullptr ||
        tuplesField->flags != 1 || tuplesField->scalar != ScalarTag::Bytes ||
        tuplesField->value.size < 8 ||
        !ExactField(maximumField, ScalarTag::Uint64, 8)) {
        return Failure(ErrorCode::BadField, &header, u"BlobPack values");
    }
    const std::uint64_t encodedBytes = Get64(tuplesField->value.data);
    const std::uint64_t maximum = Get64(maximumField->value.data);
    if (encodedBytes != tuplesField->value.size - 8 || encodedBytes < 8 ||
        maximum == 0 || maximum > kMaximumPayloadBytes) {
        return Failure(ErrorCode::BadField, &header, u"BlobPack range");
    }
    const std::uint8_t* const encoded = tuplesField->value.data + 8;
    const std::uint64_t count = Get64(encoded);
    if (count == 0 || count > (encodedBytes - 8) / 64 ||
        encodedBytes != 8 + count * 64) {
        return Failure(ErrorCode::BadField, &header, u"BlobPack tuples");
    }
    std::vector<query::BlobSlice> slices;
    try { slices.resize(static_cast<std::size_t>(count)); }
    catch (...) { return Failure(ErrorCode::Internal, &header, u"BlobPack allocation"); }
    for (std::uint64_t index = 0; index < count; ++index) {
        const std::uint8_t* const tuple = encoded + 8 + index * 64;
        query::BlobSlice& slice = slices[static_cast<std::size_t>(index)];
        std::copy_n(tuple, 16, slice.id.bytes.begin());
        slice.offset = Get64(tuple + 16);
        slice.length = Get64(tuple + 24);
        std::copy_n(tuple + 32, 32, slice.digest.bytes.begin());
        if (slice.offset > (std::numeric_limits<std::uint64_t>::max)() -
                               slice.length ||
            (index != 0 && std::memcmp(tuple - 64, tuple, 64) >= 0)) {
            return Failure(ErrorCode::BadField, &header,
                           u"BlobPack tuples are not sorted unique");
        }
    }
    bool graphReadNegotiated = false;
    if (!ReadCapabilityNegotiation(
            version.documentSessionId, route, kCapabilityAssetRead,
            &graphReadNegotiated) || !graphReadNegotiated) {
        return Failure(ErrorCode::UnsupportedCapability, &header,
                       u"BlobPack capability session is not open");
    }
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.cursors_.find(cursorId.bytes);
    if (found == registry.cursors_.end())
        return Failure(ErrorCode::CursorNotFound, &header, u"BlobPack cursor not found");
    GraphCursor& cursor = found->second;
    if (!Equal(cursor.route, route) ||
        !Equal(cursor.session, version.documentSessionId))
        return Failure(ErrorCode::RouteChanged, &header, u"BlobPack route changed");
    if (!HeaderMatchesVersion(header, cursor.version))
        return Failure(ErrorCode::StaleGraph, &header, u"BlobPack version changed");
    if (cursor.state != CursorState::Terminal)
        return Failure(ErrorCode::CursorClosed, &header, u"BlobPack cursor not terminal");
    const Sha256 requestDigest = codec::Hash(request);
    if (cursor.blobPackCompleted) {
        return cursor.blobPackRequestDigest.bytes == requestDigest.bytes
            ? cursor.blobPackResponse
            : Failure(ErrorCode::BadField, &header, u"BlobPack changed replay");
    }
    Bytes packed(8);
    Put64(packed.data(), count);
    for (std::uint64_t index = 0; index < count; ++index) {
        const query::BlobSlice& slice = slices[static_cast<std::size_t>(index)];
        if (std::none_of(cursor.view.blobClosure.begin(),
                         cursor.view.blobClosure.end(),
                         [&slice](const query::BlobSlice& authorized) {
                             return query::BlobSliceEqual(authorized, slice);
                         })) {
            return Failure(ErrorCode::BadField, &header,
                           u"BlobPack slice is not authorized");
        }
        if (slice.length > static_cast<std::uint64_t>(
                (std::numeric_limits<std::size_t>::max)()))
            return Failure(ErrorCode::BadField, &header, u"BlobPack slice too large");
        Bytes content(static_cast<std::size_t>(slice.length));
        if (!query::ReadQueryViewBlob(cursor.view, slice, 0,
                                      content.data(), slice.length) ||
            codec::Hash(codec::View(content)).bytes != slice.digest.bytes) {
            return Failure(ErrorCode::StorageFailure, &header,
                           u"BlobPack authenticated read failed");
        }
        const std::uint64_t entryBytes = 72 + slice.length;
        if (packed.size() > maximum || entryBytes > maximum - packed.size())
            return Failure(ErrorCode::BufferTooSmall, &header,
                           u"BlobPack response budget exceeded");
        Add(&packed, {slice.id.bytes.data(), slice.id.bytes.size()});
        Add(&packed, codec::View(U64(slice.offset)));
        Add(&packed, codec::View(U64(slice.length)));
        Add(&packed, {slice.digest.bytes.data(), slice.digest.bytes.size()});
        Add(&packed, codec::View(U64(slice.length)));
        Add(&packed, codec::View(content));
    }
    Header response{};
    response.message = MessageKind::BlobPack;
    response.flags = kFlagTerminal;
    response.cursorOrUpload = cursor.id;
    if (!ApplyVersionToHeader(cursor.version, &response))
        return Failure(ErrorCode::Internal, &header, u"BlobPack version encoding");
    const Sha256 packDigest = codec::Hash(codec::View(packed));
    Bytes responseBytes = Receipt(
        response,
        {EncodeField(1, 1, ScalarTag::UUID128,
                     {transferId.bytes.data(), transferId.bytes.size()}),
         EncodeField(2, 1, ScalarTag::Uint64, codec::View(U64(count))),
         EncodeField(3, 1, ScalarTag::Bytes,
                     codec::View(CanonicalBytes(codec::View(packed)))),
         EncodeField(4, 1, ScalarTag::SHA256,
                     {packDigest.bytes.data(), packDigest.bytes.size()})});
    if (registry.failNextBlobResponseEncoding_) {
        registry.failNextBlobResponseEncoding_ = false;
        responseBytes.clear();
    }
    if (responseBytes.empty())
        return Failure(ErrorCode::Internal, &header, u"BlobPack response encoding failed");
    cursor.blobPackRequestDigest = requestDigest;
    cursor.blobPackResponse = responseBytes;
    cursor.blobPackCompleted = true;
    return responseBytes;
}

Bytes HandleBlobRead(const ByteView request, const Route route) {
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, MessageKind::BlobReadRequest, &header, &payload,
                      &fields, &error)) {
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header,
                       u"invalid BlobRead request");
    }
    if (!ExactTags(fields, {1, 2, 3, 4, 5, 6, 7, 8}))
        return Failure(ErrorCode::BadField, &header, u"BlobRead fields");
    Uuid128 cursorId{};
    identity::GraphVersionV1 version{};
    query::BlobSlice slice{};
    const ParsedField* contentId = Find(fields, 3);
    const ParsedField* sourceOffset = Find(fields, 4);
    const ParsedField* length = Find(fields, 5);
    const ParsedField* digest = Find(fields, 6);
    const ParsedField* relativeOffset = Find(fields, 7);
    const ParsedField* maximumBytes = Find(fields, 8);
    if (!ReadId(Find(fields, 1), &cursorId) ||
        !ReadVersion(Find(fields, 2), &version) ||
        !HeaderMatchesVersion(header, version) ||
        !ExactField(contentId, ScalarTag::UUID128, 16) ||
        !ExactField(sourceOffset, ScalarTag::Uint64, 8) ||
        !ExactField(length, ScalarTag::Uint64, 8) ||
        !ExactField(digest, ScalarTag::SHA256, 32) ||
        !ExactField(relativeOffset, ScalarTag::Uint64, 8) ||
        !ExactField(maximumBytes, ScalarTag::Uint64, 8) ||
        !Equal(cursorId, header.cursorOrUpload)) {
        return Failure(ErrorCode::BadField, &header, u"BlobRead values");
    }
    std::copy_n(contentId->value.data, 16, slice.id.bytes.begin());
    slice.offset = Get64(sourceOffset->value.data);
    slice.length = Get64(length->value.data);
    std::copy_n(digest->value.data, 32, slice.digest.bytes.begin());
    const std::uint64_t relative = Get64(relativeOffset->value.data);
    const std::uint64_t maximum = Get64(maximumBytes->value.data);
    if (maximum == 0 || maximum > 32768 ||
        slice.offset > (std::numeric_limits<std::uint64_t>::max)() -
                           slice.length ||
        relative > slice.length) {
        return Failure(ErrorCode::BadField, &header, u"BlobRead range");
    }

    bool graphReadNegotiated = false;
    if (!ReadCapabilityNegotiation(
            version.documentSessionId, route, kCapabilityAssetRead,
            &graphReadNegotiated)) {
        return Failure(ErrorCode::UnsupportedCapability, &header,
                       u"AssetRead capability session is not open");
    }
    if (!graphReadNegotiated) return Unsupported(&header);

    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.cursors_.find(cursorId.bytes);
    if (found == registry.cursors_.end())
        return Failure(ErrorCode::CursorNotFound, &header,
                       u"blob cursor not found");
    GraphCursor& cursor = found->second;
    if (!Equal(cursor.route, route) ||
        !Equal(cursor.session, version.documentSessionId)) {
        return Failure(ErrorCode::RouteChanged, &header,
                       u"blob cursor route changed");
    }
    if (!HeaderMatchesVersion(header, cursor.version))
        return Failure(ErrorCode::StaleGraph, &header,
                       u"blob cursor version changed");
    if (cursor.state == CursorState::Cancelled ||
        cursor.state == CursorState::Closed ||
        cursor.state == CursorState::Invalidated) {
        return Failure(ErrorCode::CursorClosed, &header,
                       u"blob cursor is closed");
    }
    if (std::none_of(cursor.view.blobClosure.begin(),
                     cursor.view.blobClosure.end(),
                     [&slice](const query::BlobSlice& authorized) {
                         return query::BlobSliceEqual(authorized, slice);
                     })) {
        return Failure(ErrorCode::BadField, &header,
                       u"blob slice is not authorized");
    }
    auto consumption = std::find_if(
        cursor.blobConsumption.begin(), cursor.blobConsumption.end(),
        [&slice](const BlobConsumption& value) {
            return query::BlobSliceEqual(value.slice, slice);
        });
    if (consumption == cursor.blobConsumption.end()) {
        cursor.blobConsumption.push_back(BlobConsumption{slice});
        consumption = std::prev(cursor.blobConsumption.end());
    }
    if (consumption->completed || relative != consumption->nextOffset) {
        return Failure(ErrorCode::BadField, &header,
                       u"blob offset is not the exact next offset");
    }
    const std::uint64_t chunkBytes =
        (std::min)(maximum, slice.length - relative);
    Bytes content(static_cast<std::size_t>(chunkBytes));
    if (!query::ReadQueryViewBlob(cursor.view, slice, relative,
                                  content.data(), chunkBytes)) {
        return Failure(ErrorCode::StorageFailure, &header,
                       u"authenticated blob read failed");
    }
    Header response{};
    response.message = MessageKind::BlobChunk;
    response.flags = relative + chunkBytes == slice.length ? kFlagTerminal : 0;
    response.cursorOrUpload = cursor.id;
    response.fragmentOffset = relative;
    response.fragmentTotal = slice.length;
    if (!ApplyVersionToHeader(cursor.version, &response))
        return Failure(ErrorCode::Internal, &header,
                       u"blob version encoding failed");
    Bytes responseBytes = Receipt(
        response,
        {EncodeField(1, 1, ScalarTag::UUID128,
                     {slice.id.bytes.data(), slice.id.bytes.size()}),
         EncodeField(2, 1, ScalarTag::Uint64,
                     codec::View(U64(slice.offset))),
         EncodeField(3, 1, ScalarTag::Uint64,
                     codec::View(U64(slice.length))),
         EncodeField(4, 1, ScalarTag::SHA256,
                     {slice.digest.bytes.data(), slice.digest.bytes.size()}),
         EncodeField(5, 1, ScalarTag::Uint64,
                     codec::View(U64(relative))),
         EncodeField(6, 1, ScalarTag::Bytes,
                     codec::View(CanonicalBytes(codec::View(content))))});
    if (registry.failNextBlobResponseEncoding_) {
        registry.failNextBlobResponseEncoding_ = false;
        responseBytes.clear();
    }
    if (responseBytes.empty())
        return Failure(ErrorCode::Internal, &header,
                       u"blob response encoding failed");
    consumption->nextOffset += chunkBytes;
    consumption->completed = consumption->nextOffset == slice.length;
    return responseBytes;
}

Bytes HandleUnsupportedGraph(ByteView request, MessageKind expected,
                             const Route route) {
    if (expected == MessageKind::GraphCloseRequest) {
        Registry& registry = Global();
        std::lock_guard<std::mutex> guard(registry.lock_);
        ++registry.lifecycleCounters_.graphCloseCalls;
        RecordLifecycleEvent(registry, DebugLifecycleEventKind::GraphCloseHandled,
                             route);
    }
    Header header{};
    ByteView payload{};
    std::vector<ParsedField> fields;
    ErrorCode error = ErrorCode::BadHeader;
    if (!ParseRequest(request, expected, &header, &payload, &fields, &error))
        return Failure(error, error == ErrorCode::BadHeader ? nullptr : &header, u"invalid graph request");
    bool valid = false;
    identity::DocumentSessionId session{};
    if (expected == MessageKind::GraphNextRequest) {
        identity::GraphVersionV1 version{};
        const ParsedField* budget = Find(fields, 3);
        valid = ExactTags(fields, {1, 2, 3, 4, 5}) &&
            ExactField(Find(fields, 1), ScalarTag::UUID128, 16) &&
            ReadVersion(Find(fields, 2), &version) && HeaderMatchesVersion(header, version) &&
            ExactField(budget, ScalarTag::Uint64, 8) &&
            Get64(budget->value.data) >= kHeaderBytes &&
            Get64(budget->value.data) <= kMaximumFrameBytes &&
            ExactField(Find(fields, 4), ScalarTag::Uint64, 8) &&
            ExactField(Find(fields, 5), ScalarTag::SHA256, 32) &&
            Get64(Find(fields, 4)->value.data) == header.sequence &&
            std::memcmp(Find(fields, 5)->value.data, header.previousChain.bytes.data(), 32) == 0;
        if (valid) session = version.documentSessionId;
    } else {
        valid = ExactTags(fields, {1, 2}) &&
            ExactField(Find(fields, 1), ScalarTag::UUID128, 16) &&
            ReadId(Find(fields, 2), &session) && Equal(session, header.session) &&
            VersionFieldsZero(header);
    }
    if (!valid) return Failure(ErrorCode::BadField, &header, u"graph request fields");
    bool graphReadNegotiated = false;
    if (!ReadCapabilityNegotiation(
            session, route, kCapabilityGraphRead, &graphReadNegotiated))
        return Failure(ErrorCode::BadField, &header, u"capability session is not open");
    if (!graphReadNegotiated) return Unsupported(&header);

    Uuid128 cursorId{};
    if (!ReadId(Find(fields, 1), &cursorId))
        return Failure(ErrorCode::BadField, &header, u"cursor identifier");
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.cursors_.find(cursorId.bytes);
    if (found == registry.cursors_.end())
        return Failure(ErrorCode::CursorNotFound, &header, u"cursor not found");
    GraphCursor& cursor = found->second;
    if (!Equal(cursor.route, route) || !Equal(cursor.session, session))
        return Failure(ErrorCode::RouteChanged, &header, u"cursor route changed");

    if (expected != MessageKind::GraphNextRequest) {
        Header closed{};
        closed.message = MessageKind::CursorClosedReceipt;
        closed.flags = kFlagTerminal;
        closed.session = session;
        closed.cursorOrUpload = cursorId;
        const std::uint8_t disposition =
            expected == MessageKind::GraphCancelRequest ? 2U : 1U;
        const Bytes response = Receipt(
            closed,
            {EncodeField(1, 1, ScalarTag::UUID128, codec::View(Id(cursorId))),
             EncodeField(2, 1, ScalarTag::Uint8, codec::View(U8(disposition)))});
        const std::wstring storageRoot = cursor.storageRoot;
        registry.cursors_.erase(found);
        const bool stillReferenced = std::any_of(
            registry.cursors_.begin(), registry.cursors_.end(),
            [&storageRoot](const auto& item) {
                return item.second.storageRoot == storageRoot;
            });
        if (!stillReferenced) {
            std::error_code ignoredStorage;
            std::filesystem::remove_all(storageRoot, ignoredStorage);
        }
        registry.RemoveStorageIfIdle();
        return response;
    }
    if (!HeaderMatchesVersion(header, cursor.version) ||
        header.sequence != cursor.nextSequence ||
        !codec::Equal(header.previousChain, cursor.previousResponseChain))
        return Failure(ErrorCode::SequenceMismatch, &header,
                       u"cursor continuation mismatch");

    const std::uint64_t budget = Get64(Find(fields, 3)->value.data);
    const bool rangeBacked = cursor.view.rangePlan != nullptr;
    const std::uint64_t logicalBytes = rangeBacked
        ? cursor.view.logicalBytes : cursor.view.records.size();
    const std::uint64_t logicalRecords = rangeBacked
        ? cursor.view.logicalRecords
        : cursor.view.canonical.recordStream.itemCount;
    const bool exhausted = rangeBacked
        ? cursor.planRecordOrdinal == logicalRecords
        : cursor.recordOffset == cursor.view.records.size();
    if (exhausted) {
        Sha256 terminalDigest = cursor.streamDigest;
        if (rangeBacked &&
            !FinalizeGraphStreamHashV1(cursor.streamHash, &terminalDigest))
            return Failure(ErrorCode::Internal, &header,
                           u"query plan stream digest failed");
        Header terminal{};
        terminal.message = MessageKind::GraphTerminal;
        terminal.flags = kFlagTerminal;
        terminal.sequence = cursor.nextSequence;
        terminal.fragmentOffset = logicalBytes;
        terminal.fragmentTotal = logicalBytes;
        terminal.cursorOrUpload = cursor.id;
        terminal.previousChain = cursor.previousResponseChain;
        static_cast<void>(ApplyVersionToHeader(cursor.version, &terminal));
        const Bytes response = Receipt(
            terminal,
            {EncodeField(1, 1, ScalarTag::Uint64,
                         codec::View(U64(logicalRecords))),
             EncodeField(2, 1, ScalarTag::Uint64,
                         codec::View(U64(logicalBytes))),
             EncodeField(3, 1, ScalarTag::SHA256,
                         codec::View(Digest(terminalDigest)))});
        cursor.streamDigest = terminalDigest;
        cursor.state = CursorState::Terminal;
        ++cursor.nextSequence;
        return response;
    }
    if (budget <= kHeaderBytes + 56)
        return ErrorFrame(ErrorCode::BufferTooSmall, E_BOUNDS,
                          kHeaderBytes + 57, u"GraphNext budget", &header);

    gFrameEnvelopeCounters.queryFramePlans.fetch_add(
        1, std::memory_order_relaxed);
    ScopedCounterTick framePlanTimer(
        &gFrameEnvelopeCounters.queryFramePlanTicks);
    std::uint64_t nextRecordOffset = cursor.recordOffset;
    std::uint64_t nextPlanRecord = cursor.planRecordOrdinal;
    Bytes nextPlannedBytes = cursor.plannedRecord;
    std::uint16_t nextFieldOrdinal = cursor.fieldOrdinal;
    std::uint64_t nextFieldOffset = cursor.fieldOffset;
    std::uint64_t nextReconstructedOffset = cursor.reconstructedOffset;
    const std::uint64_t responseOffset = nextReconstructedOffset;
    const std::uint64_t payloadBudget = budget - kHeaderBytes;
    Bytes response(kHeaderBytes, 0);
    response.reserve(static_cast<std::size_t>(budget));
    GraphStreamSha256V1 nextStreamHash = cursor.streamHash;
    while ((rangeBacked ? nextPlanRecord != logicalRecords
                        : nextRecordOffset != cursor.view.records.size()) &&
           response.size() - kHeaderBytes + 56 <= payloadBudget) {
        if (rangeBacked && nextPlannedBytes.empty()) {
            const std::uint64_t readStarted = PerformanceTick();
            if (!query::MaterializeQueryPlanRecord(
                    cursor.view, nextPlanRecord, &nextPlannedBytes))
                return Failure(ErrorCode::StorageFailure, &header,
                               u"query plan record read");
            AddTicks(gFrameEnvelopeCounters.recordReadTicks, readStarted);
            gFrameEnvelopeCounters.recordReadBytes.fetch_add(
                nextPlannedBytes.size(), std::memory_order_relaxed);
        }
        const Bytes& stream = rangeBacked
            ? nextPlannedBytes : cursor.view.records;
        const std::uint64_t recordAt = rangeBacked ? 0 : nextRecordOffset;
        if (recordAt > stream.size() ||
            stream.size() - recordAt < kLogicalRecordHeaderBytesV1)
            return Failure(ErrorCode::StorageFailure, &header,
                           u"query view record");
        const RecordKind recordKind =
            static_cast<RecordKind>(Get16(stream.data() + recordAt));
        const std::uint16_t recordFlags = Get16(stream.data() + recordAt + 4);
        const std::uint16_t fieldCount = Get16(stream.data() + recordAt + 6);
        const RecordId recordId = Get64(stream.data() + recordAt + 8);
        const std::uint64_t payloadBytes = Get64(stream.data() + recordAt + 16);
        if (payloadBytes > stream.size() - recordAt - kLogicalRecordHeaderBytesV1 ||
            fieldCount == 0)
            return Failure(ErrorCode::StorageFailure, &header,
                           u"query view record range");
        std::uint64_t fieldAt = recordAt + kLogicalRecordHeaderBytesV1;
        for (std::uint16_t ordinal = 0; ordinal != nextFieldOrdinal; ++ordinal) {
            if (fieldAt > stream.size() ||
                stream.size() - fieldAt < kFieldHeaderBytesV1)
                return Failure(ErrorCode::StorageFailure, &header,
                               u"query view field");
            const std::uint64_t priorBytes = Get64(stream.data() + fieldAt + 16);
            if (priorBytes > stream.size() - fieldAt - kFieldHeaderBytesV1)
                return Failure(ErrorCode::StorageFailure, &header,
                               u"query view prior field range");
            fieldAt += kFieldHeaderBytesV1 + priorBytes;
        }
        if (nextFieldOrdinal >= fieldCount || fieldAt > stream.size() ||
            stream.size() - fieldAt < kFieldHeaderBytesV1)
            return Failure(ErrorCode::StorageFailure, &header,
                           u"query view field");
        const FieldTag fieldTag = Get16(stream.data() + fieldAt);
        const std::uint16_t fieldFlags = Get16(stream.data() + fieldAt + 2);
        const ScalarTag scalar =
            static_cast<ScalarTag>(Get16(stream.data() + fieldAt + 4));
        const std::uint64_t elementCount = Get64(stream.data() + fieldAt + 8);
        const std::uint64_t fieldBytes = Get64(stream.data() + fieldAt + 16);
        if (nextFieldOffset > fieldBytes ||
            fieldBytes > stream.size() - fieldAt - kFieldHeaderBytesV1)
            return Failure(ErrorCode::StorageFailure, &header,
                           u"query view field range");
        const std::uint64_t room =
            payloadBudget - (response.size() - kHeaderBytes) - 56;
        const std::uint64_t remaining = fieldBytes - nextFieldOffset;
        if (remaining != 0 && room == 0) break;
        const std::uint64_t take = (std::min)(room, remaining);
        FragmentHeader fragment{};
        fragment.recordKind = recordKind;
        fragment.logicalRecordFlags = recordFlags;
        fragment.fieldTag = fieldTag;
        fragment.fieldFlags = fieldFlags;
        if (nextFieldOffset == 0) fragment.fieldFlags |= 4;
        if (nextFieldOffset + take == fieldBytes) fragment.fieldFlags |= 8;
        if (nextFieldOrdinal == 0 && nextFieldOffset == 0)
            fragment.fieldFlags |= 16;
        if (nextFieldOrdinal + 1 == fieldCount &&
            nextFieldOffset + take == fieldBytes)
            fragment.fieldFlags |= 32;
        fragment.scalar = scalar;
        fragment.logicalRecordFieldCount = fieldCount;
        fragment.recordId = recordId;
        fragment.elementCount = elementCount;
        fragment.totalFieldBytes = fieldBytes;
        fragment.fragmentOffset = nextFieldOffset;
        fragment.fragmentBytes = take;
        const ByteView value{
            stream.data() + fieldAt + kFieldHeaderBytesV1 + nextFieldOffset,
            take};
        if (!AppendEncodedFragment(fragment, value, &response))
            return Failure(ErrorCode::Internal, &header,
                           u"fragment encoding failed");
        gFrameEnvelopeCounters.fragmentValueBytes.fetch_add(
            take, std::memory_order_relaxed);
        if (rangeBacked) {
            if (nextFieldOrdinal == 0 && nextFieldOffset == 0 &&
                !UpdateGraphStreamHashV1(
                    &nextStreamHash,
                    {stream.data() + recordAt, kLogicalRecordHeaderBytesV1}))
                return Failure(ErrorCode::Internal, &header,
                               u"query plan record hash update failed");
            if (nextFieldOffset == 0 &&
                !UpdateGraphStreamHashV1(
                    &nextStreamHash,
                    {stream.data() + fieldAt, kFieldHeaderBytesV1}))
                return Failure(ErrorCode::Internal, &header,
                               u"query plan field hash update failed");
            if (!UpdateGraphStreamHashV1(&nextStreamHash, value))
                return Failure(ErrorCode::Internal, &header,
                               u"query plan value hash update failed");
        }
        nextReconstructedOffset += take;
        if (nextFieldOffset == 0)
            nextReconstructedOffset += kFieldHeaderBytesV1;
        if (nextFieldOrdinal == 0 && nextFieldOffset == 0)
            nextReconstructedOffset += kLogicalRecordHeaderBytesV1;
        nextFieldOffset += take;
        if (nextFieldOffset == fieldBytes) {
            nextFieldOffset = 0;
            ++nextFieldOrdinal;
            if (nextFieldOrdinal == fieldCount) {
                nextFieldOrdinal = 0;
                if (rangeBacked) {
                    ++nextPlanRecord;
                    nextPlannedBytes.clear();
                } else {
                    nextRecordOffset = recordAt +
                        kLogicalRecordHeaderBytesV1 + payloadBytes;
                }
            }
        }
    }
    if (response.size() == kHeaderBytes)
        return ErrorFrame(ErrorCode::BufferTooSmall, E_BOUNDS,
                          kHeaderBytes + 57, u"GraphNext fragment budget",
                          &header);
    Header chunk{};
    chunk.message = MessageKind::GraphChunk;
    if (nextReconstructedOffset != logicalBytes) chunk.flags |= kFlagFragmented;
    chunk.sequence = cursor.nextSequence;
    chunk.fragmentOffset = responseOffset;
    chunk.fragmentTotal = logicalBytes;
    chunk.cursorOrUpload = cursor.id;
    chunk.previousChain = cursor.previousResponseChain;
    static_cast<void>(ApplyVersionToHeader(cursor.version, &chunk));
    const std::uint64_t encodeStarted = PerformanceTick();
    if (!FinalizeFrameInPlace(chunk, &response))
        return Failure(ErrorCode::Internal, &header, u"GraphChunk encoding failed");
    AddTicks(gFrameEnvelopeCounters.frameEncodeTicks, encodeStarted);
    if (registry.failNextGraphResponseEncoding_) {
        registry.failNextGraphResponseEncoding_ = false;
        response.clear();
    }
    if (response.empty())
        return Failure(ErrorCode::Internal, &header,
                       u"GraphChunk injected encoding failure");
    const std::uint64_t digestStarted = PerformanceTick();
    const Sha256 responseChain = [] (const Bytes& bytes) noexcept {
        Sha256 digest{};
        if (bytes.size() >= kHeaderBytes)
            std::copy_n(bytes.data() + 288, digest.bytes.size(),
                        digest.bytes.begin());
        return digest;
    }(response);
    if (Zero(responseChain))
        return Failure(ErrorCode::Internal, &header,
                       u"GraphChunk authentication failed");
    AddTicks(gFrameEnvelopeCounters.digestTicks, digestStarted);
    cursor.recordOffset = nextRecordOffset;
    cursor.planRecordOrdinal = nextPlanRecord;
    cursor.plannedRecord = std::move(nextPlannedBytes);
    cursor.fieldOrdinal = nextFieldOrdinal;
    cursor.fieldOffset = nextFieldOffset;
    cursor.reconstructedOffset = nextReconstructedOffset;
    if (rangeBacked) cursor.streamHash = nextStreamHash;
    cursor.previousResponseChain = responseChain;
    ++cursor.nextSequence;
    return response;
}
} // namespace

CapabilityNegotiationStatus NegotiateCapabilities(
    const identity::DocumentSessionId& session, const std::uint64_t requestedBits,
    const Route route, std::uint64_t* const negotiatedBits) noexcept {
    constexpr std::uint64_t knownRequestBits = UINT64_C(0x1ff);
    if (negotiatedBits == nullptr) return CapabilityNegotiationStatus::StorageFailure;
    *negotiatedBits = 0;
    if (!identity::IsRfc4122V4(session))
        return CapabilityNegotiationStatus::InvalidSession;
    if ((requestedBits & ~knownRequestBits) != 0)
        return CapabilityNegotiationStatus::UnknownRequestedBits;
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    ++registry.lifecycleCounters_.negotiationCalls;
    RecordLifecycleEvent(registry, DebugLifecycleEventKind::NegotiationCall,
                         route, session, requestedBits,
                         requestedBits & kCapabilityBits);
    if (std::any_of(registry.capabilitySessions_.begin(),
                    registry.capabilitySessions_.end(),
                    [&](const CapabilitySession& current) {
                        return Equal(current.id, session);
                    })) return CapabilityNegotiationStatus::AlreadyOpen;
    CapabilitySession created{};
    created.id = session;
    created.route = route;
    created.requestedBits = requestedBits;
    created.negotiatedBits = requestedBits & kCapabilityBits;
    try { registry.capabilitySessions_.push_back(created); }
    catch (...) { return CapabilityNegotiationStatus::StorageFailure; }
    ++registry.lifecycleCounters_.registryInserts;
    RecordLifecycleEvent(registry, DebugLifecycleEventKind::RegistryInsert,
                         route, session, requestedBits, created.negotiatedBits);
    *negotiatedBits = created.negotiatedBits;
    return CapabilityNegotiationStatus::Negotiated;
}

bool ReadNegotiatedCapabilities(
    const identity::DocumentSessionId& session, const Route route,
    std::uint64_t* const requestedBits,
    std::uint64_t* const negotiatedBits) noexcept {
    if (requestedBits == nullptr || negotiatedBits == nullptr) return false;
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = std::find_if(
        registry.capabilitySessions_.begin(), registry.capabilitySessions_.end(),
        [&](const CapabilitySession& current) {
            return Equal(current.id, session) && Equal(current.route, route);
        });
    if (found == registry.capabilitySessions_.end()) return false;
    *requestedBits = found->requestedBits;
    *negotiatedBits = found->negotiatedBits;
    return true;
}

Bytes ProcessRequest(const MessageKind expected, const ByteView request,
                     const Route route) {
    return ProcessRequest(expected, request, route, nullptr);
}

Bytes ProcessRequest(const MessageKind expected, const ByteView request,
                     const Route route, IDispatch* const captureProvider) {
    return ProcessRequest(expected, request, route, captureProvider, nullptr);
}

Bytes ProcessRequest(
    const MessageKind expected, const ByteView request, const Route route,
    IDispatch* const captureProvider,
    const layout::LayoutEnvironmentPlatformV1* const environmentPlatform) {
    switch (expected) {
    case MessageKind::GraphOpenRequest:
        return HandleGraphOpen(
            request, route, captureProvider, environmentPlatform);
    case MessageKind::BlobReadRequest:
        return HandleBlobRead(request, route);
    case MessageKind::BlobPackRequest:
        return HandleBlobPack(request, route);
    case MessageKind::GraphNextRequest:
    case MessageKind::GraphCancelRequest:
    case MessageKind::GraphCloseRequest:
        return HandleUnsupportedGraph(request, expected, route);
    case MessageKind::PatchBeginRequest: return HandlePatchBegin(request, route);
    case MessageKind::PatchChunkRequest: return HandlePatchChunk(request, route);
    case MessageKind::PatchCommitRequest: return HandlePatchCommit(request, route);
    case MessageKind::PatchAbortRequest: return HandlePatchAbort(request, route);
    case MessageKind::PatchValidateRequest: return HandlePatchValidate(request, route);
    default: return ErrorFrame(ErrorCode::BadMessageKind, E_INVALIDARG, 0,
                               u"not a Todo 5 request");
    }
}

void InvalidateRoute(const Route route) noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    ++registry.lifecycleCounters_.routeInvalidations;
    ++registry.lifecycleCounters_.registryEraseCalls;
    const std::size_t sessionsBefore = registry.capabilitySessions_.size();
    for (auto current = registry.uploads_.begin(); current != registry.uploads_.end();) {
        if (Equal(current->second.route, route)) {
            DeleteFileW(current->second.path.c_str());
            current = registry.uploads_.erase(current);
        } else ++current;
    }
    for (auto current = registry.cursors_.begin(); current != registry.cursors_.end();) {
        if (Equal(current->second.route, route)) {
            std::error_code ignoredStorage;
            std::filesystem::remove_all(
                current->second.storageRoot, ignoredStorage);
            current = registry.cursors_.erase(current);
        } else {
            ++current;
        }
    }
    registry.capabilitySessions_.erase(
        std::remove_if(registry.capabilitySessions_.begin(),
                       registry.capabilitySessions_.end(),
                       [&](const CapabilitySession& current) {
                           return Equal(current.route, route);
                       }),
        registry.capabilitySessions_.end());
    const std::uint64_t erased = static_cast<std::uint64_t>(
        sessionsBefore - registry.capabilitySessions_.size());
    registry.lifecycleCounters_.registrySessionsErased += erased;
    RecordLifecycleEvent(registry, DebugLifecycleEventKind::RouteInvalidated,
                         route, {}, 0, 0, erased);
    registry.RemoveStorageIfIdle();
}
void NoteRouteOwnerDestruction(const Route route) noexcept {
    Registry& registry = Global();
    {
        std::lock_guard<std::mutex> guard(registry.lock_);
        ++registry.lifecycleCounters_.routeOwnerDestructions;
        RecordLifecycleEvent(registry,
                             DebugLifecycleEventKind::RouteOwnerDestruction,
                             route);
    }
    InvalidateRoute(route);
}
void CleanupProcessState() noexcept { Global().Clear(); }
void ResetDebugLifecycleInstrumentation() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    registry.lifecycleCounters_ = {};
    registry.lifecycleEvents_.clear();
    registry.nextLifecycleSequence_ = 1;
}
bool ReadDebugLifecycleInstrumentation(
    DebugLifecycleCounters* const counters, DebugLifecycleEvent* const events,
    const std::uint32_t capacity, std::uint32_t* const eventCount) noexcept {
    if (counters == nullptr || eventCount == nullptr ||
        (capacity != 0 && events == nullptr)) return false;
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    if (registry.lifecycleEvents_.size() > capacity) {
        *eventCount = static_cast<std::uint32_t>(registry.lifecycleEvents_.size());
        return false;
    }
    *counters = registry.lifecycleCounters_;
    *eventCount = static_cast<std::uint32_t>(registry.lifecycleEvents_.size());
    std::copy(registry.lifecycleEvents_.begin(), registry.lifecycleEvents_.end(),
              events);
    return true;
}
bool DebugQueryCapabilitySession(
    const identity::DocumentSessionId& session, const Route route,
    std::uint64_t* const requestedBits,
    std::uint64_t* const negotiatedBits) noexcept {
    return ReadNegotiatedCapabilities(
        session, route, requestedBits, negotiatedBits);
}
std::size_t DebugCapabilitySessionCount() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    return registry.capabilitySessions_.size();
}
std::size_t DebugCapabilityLookupHits() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    return registry.capabilityLookupHits_;
}
std::size_t DebugCapabilityLookupMisses() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    return registry.capabilityLookupMisses_;
}
std::size_t DebugCursorCount() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    return registry.cursors_.size();
}
bool DebugBlobClosure(
    const Uuid128& cursor,
    std::vector<query::BlobSlice>* const closure) noexcept {
    if (closure == nullptr) return false;
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.cursors_.find(cursor.bytes);
    if (found == registry.cursors_.end()) return false;
    try {
        *closure = found->second.view.blobClosure;
        return true;
    } catch (...) {
        closure->clear();
        return false;
    }
}
bool DebugInstallGraphCursor(
    const Uuid128& cursor, const identity::GraphVersionV1& version,
    const Route route, query::QueryView view,
    const Sha256& previousResponseChain) noexcept {
    if (cursor.bytes == Uuid128{}.bytes || view.records.empty() ||
        version.documentSessionId.bytes == identity::DocumentSessionId{}.bytes)
        return false;
    GraphStreamSha256V1 streamHash{};
    InitializeGraphStreamHashV1(&streamHash);
    Sha256 streamDigest{};
    if (!UpdateGraphStreamHashV1(&streamHash, codec::View(view.records)) ||
        !FinalizeGraphStreamHashV1(streamHash, &streamDigest))
        return false;
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    try {
        GraphCursor value{};
        value.id = cursor;
        value.session = version.documentSessionId;
        value.version = version;
        value.route = route;
        value.view = std::move(view);
        value.streamDigest = streamDigest;
        value.previousResponseChain = previousResponseChain;
        return registry.cursors_.emplace(cursor.bytes, std::move(value)).second;
    } catch (...) {
        return false;
    }
}

bool DebugInstallBlobCursor(
    const Uuid128& cursor, const identity::GraphVersionV1& version,
    const Route route, query::QueryView view) noexcept {
    if (cursor.bytes == Uuid128{}.bytes ||
        version.documentSessionId.bytes == identity::DocumentSessionId{}.bytes)
        return false;
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    try {
        GraphCursor value{};
        value.id = cursor;
        value.session = version.documentSessionId;
        value.version = version;
        value.route = route;
        value.view = std::move(view);
        value.state = CursorState::Terminal;
        return registry.cursors_.emplace(cursor.bytes, std::move(value)).second;
    } catch (...) {
        return false;
    }
}
void DebugFailNextGraphResponseEncoding() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    registry.failNextGraphResponseEncoding_ = true;
}

void DebugFailNextBlobResponseEncoding() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    registry.failNextBlobResponseEncoding_ = true;
}
void ResetFrameEnvelopeDebugCounters() noexcept {
    gFrameEnvelopeCounters.queryFramePlans.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.recordReadBytes.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.fragmentValueBytes.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.fragmentTemporaryCopyBytes.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.streamHashStagingBytes.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.framePayloadCopyBytes.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.responseAuthenticationPasses.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.responseAuthenticationBytes.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.safeArrayCopyBytes.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.queryFramePlanTicks.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.recordReadTicks.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.digestTicks.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.frameEncodeTicks.store(0, std::memory_order_relaxed);
    gFrameEnvelopeCounters.safeArrayTicks.store(0, std::memory_order_relaxed);
}
FrameEnvelopeDebugCounters ReadFrameEnvelopeDebugCounters() noexcept {
    FrameEnvelopeDebugCounters result{};
    result.queryFramePlans = gFrameEnvelopeCounters.queryFramePlans.load(std::memory_order_relaxed);
    result.recordReadBytes = gFrameEnvelopeCounters.recordReadBytes.load(std::memory_order_relaxed);
    result.fragmentValueBytes = gFrameEnvelopeCounters.fragmentValueBytes.load(std::memory_order_relaxed);
    result.fragmentTemporaryCopyBytes = gFrameEnvelopeCounters.fragmentTemporaryCopyBytes.load(std::memory_order_relaxed);
    result.streamHashStagingBytes = gFrameEnvelopeCounters.streamHashStagingBytes.load(std::memory_order_relaxed);
    result.framePayloadCopyBytes = gFrameEnvelopeCounters.framePayloadCopyBytes.load(std::memory_order_relaxed);
    result.responseAuthenticationPasses = gFrameEnvelopeCounters.responseAuthenticationPasses.load(std::memory_order_relaxed);
    result.responseAuthenticationBytes = gFrameEnvelopeCounters.responseAuthenticationBytes.load(std::memory_order_relaxed);
    result.safeArrayCopyBytes = gFrameEnvelopeCounters.safeArrayCopyBytes.load(std::memory_order_relaxed);
    result.queryFramePlanTicks = gFrameEnvelopeCounters.queryFramePlanTicks.load(std::memory_order_relaxed);
    result.recordReadTicks = gFrameEnvelopeCounters.recordReadTicks.load(std::memory_order_relaxed);
    result.digestTicks = gFrameEnvelopeCounters.digestTicks.load(std::memory_order_relaxed);
    result.frameEncodeTicks = gFrameEnvelopeCounters.frameEncodeTicks.load(std::memory_order_relaxed);
    result.safeArrayTicks = gFrameEnvelopeCounters.safeArrayTicks.load(std::memory_order_relaxed);
    return result;
}
bool DebugReadBlob(const Uuid128& cursor, const query::BlobSlice& slice,
                   Bytes* const content) noexcept {
    if (content == nullptr || slice.length > SIZE_MAX) return false;
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.cursors_.find(cursor.bytes);
    if (found == registry.cursors_.end()) return false;
    try {
        content->assign(static_cast<std::size_t>(slice.length), 0);
    } catch (...) {
        return false;
    }
    return query::ReadQueryViewBlob(found->second.view, slice, 0,
                                    content->data(), slice.length);
}
std::size_t DebugUploadCount() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    return registry.uploads_.size();
}
std::size_t DebugUploadFileCount() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    if (registry.root_.empty()) return 0;
    std::error_code error;
    if (!std::filesystem::exists(registry.root_, error)) return error ?
        (std::numeric_limits<std::size_t>::max)() : 0;
    std::size_t count = 0;
    try {
        for (const auto& entry : std::filesystem::directory_iterator(registry.root_))
            if (entry.is_regular_file()) ++count;
    } catch (...) { return (std::numeric_limits<std::size_t>::max)(); }
    return count;
}
bool DebugUploadDirectoryExists() noexcept {
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    if (registry.root_.empty()) return false;
    std::error_code error;
    const bool exists = std::filesystem::exists(registry.root_, error);
    return !error && exists;
}
bool DebugReadSealed(const Uuid128& id, Bytes* bytes) noexcept {
    if (bytes == nullptr) return false;
    Registry& registry = Global();
    std::lock_guard<std::mutex> guard(registry.lock_);
    const auto found = registry.uploads_.find(id.bytes);
    if (found == registry.uploads_.end() || found->second.state != UploadState::Sealed ||
        found->second.accepted > static_cast<std::uint64_t>((std::numeric_limits<std::size_t>::max)()))
        return false;
    HANDLE file = CreateFileW(found->second.path.c_str(), GENERIC_READ, FILE_SHARE_READ,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    try { bytes->assign(static_cast<std::size_t>(found->second.accepted), 0); }
    catch (...) { CloseHandle(file); return false; }
    std::uint64_t at = 0;
    bool success = true;
    while (at != bytes->size()) {
        const DWORD wanted = static_cast<DWORD>((std::min<std::uint64_t>)(bytes->size() - at, MAXDWORD));
        DWORD actual = 0;
        if (ReadFile(file, bytes->data() + static_cast<std::size_t>(at), wanted,
                     &actual, nullptr) == FALSE || actual != wanted) {
            success = false;
            break;
        }
        at += actual;
    }
    CloseHandle(file);
    return success;
}

HRESULT ReadByteArrayArgument(const VARIANT& value, Bytes* bytes) noexcept {
    if (bytes == nullptr) return E_POINTER;
    if (value.vt != (VT_ARRAY | VT_UI1) || value.parray == nullptr ||
        SafeArrayGetDim(value.parray) != 1 || SafeArrayGetElemsize(value.parray) != sizeof(BYTE))
        return DISP_E_TYPEMISMATCH;
    VARTYPE descriptorType = VT_EMPTY;
    if (FAILED(SafeArrayGetVartype(value.parray, &descriptorType)) || descriptorType != VT_UI1)
        return DISP_E_TYPEMISMATCH;
    LONG lower = 0, upper = -1;
    if (FAILED(SafeArrayGetLBound(value.parray, 1, &lower)) || lower != 0 ||
        FAILED(SafeArrayGetUBound(value.parray, 1, &upper)) || upper < -1)
        return DISP_E_TYPEMISMATCH;
    const std::uint64_t count = upper < lower ? 0 :
        static_cast<std::uint64_t>(static_cast<unsigned long long>(upper - lower) + 1ULL);
    if (count > kMaximumFrameBytes) return DISP_E_OVERFLOW;
    void* raw = nullptr;
    HRESULT status = SafeArrayAccessData(value.parray, &raw);
    if (FAILED(status)) return status;
    Bytes copied;
    try {
        const auto* first = static_cast<const std::uint8_t*>(raw);
        if (count != 0) copied.assign(first, first + static_cast<std::size_t>(count));
    } catch (...) { status = E_OUTOFMEMORY; }
    const HRESULT unaccess = SafeArrayUnaccessData(value.parray);
    if (FAILED(status)) return status;
    if (FAILED(unaccess)) return unaccess;
    *bytes = std::move(copied);
    return S_OK;
}
HRESULT ReturnByteArray(const ByteView bytes, VARIANT* result) noexcept {
    const std::uint64_t started = PerformanceTick();
    if (result == nullptr) return E_POINTER;
    if (bytes.size > ULONG_MAX) return DISP_E_OVERFLOW;
    VariantInit(result);
    SAFEARRAY* array = SafeArrayCreateVector(VT_UI1, 0, static_cast<ULONG>(bytes.size));
    if (array == nullptr) return E_OUTOFMEMORY;
    void* raw = nullptr;
    HRESULT status = SafeArrayAccessData(array, &raw);
    if (SUCCEEDED(status)) {
        if (bytes.size != 0) std::memcpy(raw, bytes.data, static_cast<std::size_t>(bytes.size));
        status = SafeArrayUnaccessData(array);
    }
    if (FAILED(status)) { SafeArrayDestroy(array); return status; }
    result->vt = VT_ARRAY | VT_UI1;
    result->parray = array;
    gFrameEnvelopeCounters.safeArrayCopyBytes.fetch_add(
        bytes.size, std::memory_order_relaxed);
    AddTicks(gFrameEnvelopeCounters.safeArrayTicks, started);
    return S_OK;
}
HRESULT InvokeGraphMember(const DISPID memberId, const WORD flags,
                          DISPPARAMS* parameters, VARIANT* result,
                          const Route route,
                          IDispatch* const captureProvider) noexcept {
    const UINT count = parameters == nullptr ? 0 : parameters->cArgs;
    if (memberId == 25) {
        if (flags != DISPATCH_PROPERTYGET || count != 0 || result == nullptr)
            return DISP_E_BADPARAMCOUNT;
        VariantInit(result);
        result->vt = VT_I4;
        result->lVal = kGraphProtocolVersion;
        return S_OK;
    }
    if (memberId == 26) {
        if (result == nullptr) return E_POINTER;
        if (flags == DISPATCH_PROPERTYGET && count == 0) {
            const Bytes capabilities = CapabilitiesFrame();
            return ReturnByteArray(codec::View(capabilities), result);
        }
        if (flags != DISPATCH_METHOD || parameters == nullptr || count != 2)
            return DISP_E_BADPARAMCOUNT;
        const VARIANTARG& requestedArgument = parameters->rgvarg[0];
        const VARIANTARG& sessionArgument = parameters->rgvarg[1];
        if (requestedArgument.vt != VT_UI8 || sessionArgument.vt != VT_BSTR ||
            sessionArgument.bstrVal == nullptr)
            return DISP_E_TYPEMISMATCH;
        identity::DocumentSessionId session{};
        if (!identity::ParseCanonicalUuid(sessionArgument.bstrVal, &session))
            return E_INVALIDARG;
        std::uint64_t negotiated = 0;
        const CapabilityNegotiationStatus status = NegotiateCapabilities(
            session, requestedArgument.ullVal, route, &negotiated);
        if (status == CapabilityNegotiationStatus::InvalidSession ||
            status == CapabilityNegotiationStatus::UnknownRequestedBits)
            return E_INVALIDARG;
        if (status == CapabilityNegotiationStatus::AlreadyOpen)
            return HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS);
        if (status != CapabilityNegotiationStatus::Negotiated)
            return E_OUTOFMEMORY;
        const Bytes capabilities = CapabilitiesFrame();
        return ReturnByteArray(codec::View(capabilities), result);
    }
    if ((memberId < 27 || memberId > 35) && memberId != 37)
        return DISP_E_MEMBERNOTFOUND;
    if (flags != DISPATCH_METHOD || parameters == nullptr || count != 1 || result == nullptr)
        return DISP_E_BADPARAMCOUNT;
    Bytes request;
    const HRESULT read = ReadByteArrayArgument(parameters->rgvarg[0], &request);
    if (FAILED(read)) return read;
    static constexpr MessageKind kinds[]{
        MessageKind::GraphOpenRequest, MessageKind::GraphNextRequest,
        MessageKind::GraphCancelRequest, MessageKind::GraphCloseRequest,
        MessageKind::PatchBeginRequest, MessageKind::PatchChunkRequest,
        MessageKind::PatchCommitRequest, MessageKind::PatchAbortRequest,
        MessageKind::PatchValidateRequest,
    };
    MessageKind expected = memberId == 37 ? MessageKind::BlobReadRequest
                                          : kinds[memberId - 27];
    if (memberId == 37) {
        Header requestHeader{};
        ByteView requestPayload{};
        ErrorCode decodeError{};
        if (DecodeFrame(codec::View(request), &requestHeader, &requestPayload,
                        &decodeError) &&
            requestHeader.message == MessageKind::BlobPackRequest) {
            expected = MessageKind::BlobPackRequest;
        }
    }
    const Bytes response = ProcessRequest(
        expected, codec::View(request), route, captureProvider);
    if (response.empty()) return E_FAIL;
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::ComReturnStart,
        response.size(), static_cast<std::uint64_t>(memberId));
    const auto marshalStarted = std::chrono::steady_clock::now();
    const HRESULT returned = ReturnByteArray(codec::View(response), result);
    const std::uint64_t marshalMicroseconds = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - marshalStarted).count());
    capture::RecordCaptureProgressPoint(
        capture::CaptureProgressPoint::ComReturnEnd,
        static_cast<std::uint32_t>(returned), response.size());
    if (memberId == 27) {
        capture::RecordCaptureProgressPoint(
            capture::CaptureProgressPoint::GraphOpenResponseMarshal,
            marshalMicroseconds, response.size());
    }
    return returned;
}

} // namespace hancom::graph::protocol

#include "DocumentGraphCaptureSpool.h"

#include "DocumentGraphCaptureRecords.h"
#include "DocumentGraphLayout.h"
#include "DocumentGraphQuery.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <condition_variable>
#include <map>
#include <mutex>
#include <system_error>
#include <thread>

namespace hancom::graph::capture {
namespace {

using codec::Bytes;
using codec::Error;

std::atomic<std::uint64_t> gSemanticPlans{0};
std::atomic<std::uint64_t> gCompactPasses{0};
std::atomic<std::uint64_t> gFinalEncodes{0};
std::atomic<std::uint64_t> gRootedPlanEncodes{0};
std::atomic<std::uint64_t> gAttemptFiles{0};
std::atomic<std::uint64_t> gByteVerifiers{0};
std::atomic<AttemptStreamFailurePoint> gAttemptFailure{
    AttemptStreamFailurePoint::None};

bool WriteAll(
    HANDLE file,
    const Bytes& bytes) noexcept {
    if (file == INVALID_HANDLE_VALUE || bytes.size() > MAXDWORD) {
        return false;
    }
    DWORD written = 0;
    return WriteFile(
               file,
               bytes.data(),
               static_cast<DWORD>(bytes.size()),
               &written,
               nullptr) != FALSE &&
        written == bytes.size();
}

std::uint64_t FileLength(HANDLE file) noexcept {
    LARGE_INTEGER length{};
    return file != INVALID_HANDLE_VALUE &&
        GetFileSizeEx(file, &length) && length.QuadPart >= 0
        ? static_cast<std::uint64_t>(length.QuadPart)
        : 0;
}

bool ReadFile(
    void* context,
    const std::uint64_t offset,
    std::uint8_t* buffer,
    const std::uint32_t requested,
    std::uint32_t* actual) noexcept {
    HANDLE file = static_cast<HANDLE>(context);
    if (file == INVALID_HANDLE_VALUE || buffer == nullptr ||
        actual == nullptr) {
        return false;
    }
    OVERLAPPED overlapped{};
    overlapped.Offset = static_cast<DWORD>(offset);
    overlapped.OffsetHigh = static_cast<DWORD>(offset >> 32);
    DWORD read = 0;
    const BOOL status = ::ReadFile(
        file, buffer, requested, &read, &overlapped);
    *actual = read;
    return status != FALSE;
}

struct SegmentedRecords final {
    std::vector<Bytes> records{};
    std::vector<std::uint64_t> offsets{};
    std::uint64_t length = 0;
    bool complete = false;
    mutable std::mutex mutex{};
    std::condition_variable changed{};
};

bool CollectRecord(void* context, const codec::ByteView record) noexcept {
    auto* const stream = static_cast<SegmentedRecords*>(context);
    if (stream == nullptr || record.data == nullptr || record.size == 0)
        return false;
    try {
        {
            const std::lock_guard<std::mutex> lock(stream->mutex);
            stream->offsets.push_back(stream->length);
            stream->records.emplace_back(record.data, record.data + record.size);
            stream->length += record.size;
        }
        stream->changed.notify_all();
        return true;
    } catch (...) {
        return false;
    }
}

bool ReadSegments(void* context, const std::uint64_t offset,
                  std::uint8_t* buffer, const std::uint32_t requested,
                  std::uint32_t* actual) noexcept {
    auto* const stream = static_cast<SegmentedRecords*>(context);
    if (stream == nullptr || buffer == nullptr || actual == nullptr)
        return false;
    std::unique_lock<std::mutex> lock(stream->mutex);
    stream->changed.wait(lock, [stream, offset, requested]() {
        return stream->complete ||
            (offset <= stream->length &&
             requested <= stream->length - offset);
    });
    if (offset > stream->length ||
        requested > stream->length - offset) return false;
    *actual = 0;
    if (requested == 0) return true;
    const auto upper = std::upper_bound(
        stream->offsets.begin(), stream->offsets.end(), offset);
    size_t at = upper == stream->offsets.begin()
        ? 0 : static_cast<size_t>(upper - stream->offsets.begin() - 1);
    std::uint64_t position = offset;
    while (*actual != requested && at < stream->records.size()) {
        const Bytes& record = stream->records[at];
        const size_t within = static_cast<size_t>(
            position - stream->offsets[at]);
        const size_t count = (std::min)(
            static_cast<size_t>(requested - *actual), record.size() - within);
        std::copy_n(record.data() + within, count, buffer + *actual);
        *actual += static_cast<std::uint32_t>(count);
        position += count;
        ++at;
    }
    return true;
}

bool ViewSegmentRecord(
    void* context,
    const std::uint64_t offset,
    codec::ByteView* const record) noexcept {
    auto* const stream = static_cast<SegmentedRecords*>(context);
    if (stream == nullptr || record == nullptr) return false;
    const std::lock_guard<std::mutex> lock(stream->mutex);
    const auto found = std::lower_bound(
        stream->offsets.begin(), stream->offsets.end(), offset);
    if (found == stream->offsets.end() || *found != offset) return false;
    const size_t index = static_cast<size_t>(found - stream->offsets.begin());
    if (index >= stream->records.size()) return false;
    *record = codec::View(stream->records[index]);
    return true;
}

std::uint64_t SegmentLength(void* context) noexcept {
    const auto* const stream = static_cast<const SegmentedRecords*>(context);
    if (stream == nullptr) return 0;
    const std::lock_guard<std::mutex> lock(stream->mutex);
    return stream->length;
}

bool SegmentComplete(void* context) noexcept {
    const auto* const stream = static_cast<const SegmentedRecords*>(context);
    if (stream == nullptr) return true;
    const std::lock_guard<std::mutex> lock(stream->mutex);
    return stream->complete;
}

void CompleteSegments(SegmentedRecords* const stream) noexcept {
    if (stream == nullptr) return;
    {
        const std::lock_guard<std::mutex> lock(stream->mutex);
        stream->complete = true;
    }
    stream->changed.notify_all();
}

bool SealSegmentedRecords(
    SegmentedRecords* const stream,
    const codec::CanonicalizationResult& canonical) noexcept {
    if (stream == nullptr || !canonical.rootsPresent ||
        canonical.patchDescriptors.empty()) return false;
    const auto read16 = [](const std::uint8_t* const data) noexcept {
        return static_cast<std::uint16_t>(data[0]) |
            static_cast<std::uint16_t>(data[1]) << 8;
    };
    const auto read64 = [](const std::uint8_t* const data) noexcept {
        std::uint64_t value = 0;
        for (unsigned shift = 0; shift != 64; shift += 8)
            value |= static_cast<std::uint64_t>(data[shift / 8]) << shift;
        return value;
    };
    std::map<std::array<std::uint8_t, 16>, Sha256> fingerprints;
    for (const auto& fingerprint : canonical.nodeFingerprints) {
        if (!fingerprints.emplace(
                fingerprint.nodeId.bytes, fingerprint.fingerprint).second)
            return false;
    }
    bool manifestSealed = false;
    size_t fingerprintSeals = 0;
    for (const auto& descriptor : canonical.patchDescriptors) {
        const auto upper = std::upper_bound(
            stream->offsets.begin(), stream->offsets.end(),
            descriptor.valueOffset);
        if (upper == stream->offsets.begin()) return false;
        const size_t index = static_cast<size_t>(
            upper - stream->offsets.begin() - 1);
        Bytes& record = stream->records[index];
        const std::uint64_t recordOffset = stream->offsets[index];
        if (descriptor.valueOffset < recordOffset + 24 ||
            descriptor.valueOffset - recordOffset > record.size() ||
            descriptor.valueBytes >
                record.size() - static_cast<size_t>(
                    descriptor.valueOffset - recordOffset)) return false;
        const size_t valueOffset = static_cast<size_t>(
            descriptor.valueOffset - recordOffset);
        const size_t headerOffset = valueOffset - 24;
        if (read16(record.data() + headerOffset) != descriptor.fieldTag ||
            static_cast<ScalarTag>(
                read16(record.data() + headerOffset + 4)) !=
                descriptor.scalar ||
            read64(record.data() + headerOffset + 16) !=
                descriptor.valueBytes) return false;
        if (descriptor.manifestVersion) {
            if (manifestSealed || descriptor.fieldTag != 1 ||
                descriptor.scalar != ScalarTag::Struct ||
                descriptor.valueBytes != kGraphVersionBytesV1) return false;
            record[valueOffset + 10] = static_cast<std::uint8_t>(
                canonical.semanticCertified);
            record[valueOffset + 11] = static_cast<std::uint8_t>(
                canonical.layoutPresent);
            std::copy(canonical.observedSemanticRoot.bytes.begin(),
                      canonical.observedSemanticRoot.bytes.end(),
                      record.begin() + valueOffset + 72);
            std::copy(canonical.layoutRoot.bytes.begin(),
                      canonical.layoutRoot.bytes.end(),
                      record.begin() + valueOffset + 104);
            std::copy(canonical.captureRoot.bytes.begin(),
                      canonical.captureRoot.bytes.end(),
                      record.begin() + valueOffset + 136);
            manifestSealed = true;
        } else {
            if (descriptor.fieldTag != 8 ||
                descriptor.scalar != ScalarTag::SHA256 ||
                descriptor.valueBytes != 32) return false;
            const auto fingerprint = fingerprints.find(
                descriptor.nodeId.bytes);
            if (fingerprint == fingerprints.end()) return false;
            std::copy(fingerprint->second.bytes.begin(),
                      fingerprint->second.bytes.end(),
                      record.begin() + valueOffset);
            ++fingerprintSeals;
        }
    }
    return manifestSealed && fingerprintSeals == fingerprints.size() &&
        canonical.patchDescriptors.size() == fingerprints.size() + 1;
}

struct EncodingSink final {
    HANDLE file = INVALID_HANDLE_VALUE;
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    std::vector<std::uint8_t> hashObject{};
    std::uint64_t bytes = 0;
    std::uint64_t records = 0;

    ~EncodingSink() noexcept {
        if (hash != nullptr) BCryptDestroyHash(hash);
        if (algorithm != nullptr) BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    bool Begin() noexcept {
        DWORD objectBytes = 0;
        DWORD returned = 0;
        if (BCryptOpenAlgorithmProvider(
                &algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) != 0 ||
            BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                              reinterpret_cast<PUCHAR>(&objectBytes),
                              sizeof(objectBytes), &returned, 0) != 0)
            return false;
        try { hashObject.resize(objectBytes); } catch (...) { return false; }
        return BCryptCreateHash(algorithm, &hash, hashObject.data(),
                                objectBytes, nullptr, 0, 0) == 0;
    }
    bool Write(const codec::ByteView record) noexcept {
        if (hash == nullptr || record.data == nullptr || record.size == 0 ||
            record.size > MAXDWORD) return false;
        if (file != INVALID_HANDLE_VALUE) {
            DWORD written = 0;
            if (WriteFile(file, record.data, static_cast<DWORD>(record.size),
                          &written, nullptr) == FALSE || written != record.size)
                return false;
        }
        if (BCryptHashData(hash, const_cast<PUCHAR>(record.data),
                           static_cast<ULONG>(record.size), 0) != 0)
            return false;
        bytes += record.size;
        ++records;
        return true;
    }
    bool Finish(Sha256* digest) noexcept {
        return digest != nullptr && hash != nullptr &&
            BCryptFinishHash(hash, digest->bytes.data(),
                             static_cast<ULONG>(digest->bytes.size()), 0) == 0;
    }
};

bool WriteEncodedRecord(void* context, const codec::ByteView record) noexcept {
    auto* const sink = static_cast<EncodingSink*>(context);
    return sink != nullptr && sink->Write(record);
}

bool ReadMemory(
    void* context,
    const std::uint64_t offset,
    std::uint8_t* buffer,
    const std::uint32_t requested,
    std::uint32_t* actual) noexcept {
    const auto* const bytes = static_cast<const Bytes*>(context);
    if (bytes == nullptr || buffer == nullptr || actual == nullptr ||
        offset > bytes->size()) {
        return false;
    }
    const std::size_t count = (std::min)(
        static_cast<std::size_t>(requested),
        bytes->size() - static_cast<std::size_t>(offset));
    std::copy_n(bytes->data() + static_cast<std::size_t>(offset), count,
                buffer);
    *actual = static_cast<std::uint32_t>(count);
    return true;
}

bool Rewrite(
    HANDLE file,
    const Bytes& bytes,
    const bool durable) noexcept {
    LARGE_INTEGER start{};
    return SetFilePointerEx(file, start, nullptr, FILE_BEGIN) &&
        SetEndOfFile(file) && WriteAll(file, bytes) &&
        (!durable || FlushFileBuffers(file));
}

bool BindFileArtifact(HANDLE file, const std::uint64_t length,
                      const Sha256& digest,
                      store::PreparedFileArtifact* const artifact) noexcept {
    FILE_ID_INFO identity{};
    LARGE_INTEGER actual{};
    if (file == INVALID_HANDLE_VALUE || artifact == nullptr ||
        GetFileSizeEx(file, &actual) == FALSE || actual.QuadPart < 0 ||
        static_cast<std::uint64_t>(actual.QuadPart) != length ||
        GetFileInformationByHandleEx(file, FileIdInfo, &identity,
                                     sizeof(identity)) == FALSE)
        return false;
    artifact->file = file;
    artifact->length = length;
    artifact->digest = digest;
    artifact->volumeSerial = identity.VolumeSerialNumber;
    std::copy(std::begin(identity.FileId.Identifier),
              std::end(identity.FileId.Identifier), artifact->fileId.begin());
    return true;
}

codec::CanonicalStreamResult BlobClosureFacts(
    const std::vector<codec::CanonicalBlobSlice>& closure) {
    Bytes encoded;
    encoded.reserve(closure.size() * 64);
    for (const auto& item : closure) {
        encoded.insert(encoded.end(), item.contentId.bytes.begin(),
                       item.contentId.bytes.end());
        for (unsigned shift = 0; shift != 64; shift += 8)
            encoded.push_back(static_cast<std::uint8_t>(item.offset >> shift));
        for (unsigned shift = 0; shift != 64; shift += 8)
            encoded.push_back(static_cast<std::uint8_t>(item.length >> shift));
        encoded.insert(encoded.end(), item.digest.bytes.begin(),
                       item.digest.bytes.end());
    }
    return {closure.size(), encoded.size(), codec::Hash(codec::View(encoded))};
}

bool PatchesMatch(
    const std::vector<CanonicalRecordPatch>& patches,
    const Bytes& bytes) noexcept {
    return !patches.empty() && std::all_of(
        patches.begin(), patches.end(), [&bytes](const auto& patch) {
            return patch.offset <= bytes.size() &&
                patch.bytes.size() <= bytes.size() - patch.offset &&
                std::equal(patch.bytes.begin(), patch.bytes.end(),
                           bytes.begin() +
                               static_cast<std::size_t>(patch.offset));
        });
}

bool ApplyPatches(
    HANDLE file,
    const std::vector<CanonicalRecordPatch>& patches,
    Bytes* const bytes,
    const bool durable) noexcept {
    if (file == INVALID_HANDLE_VALUE || bytes == nullptr || patches.empty())
        return false;
    std::uint64_t priorEnd = 0;
    bool first = true;
    for (const CanonicalRecordPatch& patch : patches) {
        if (patch.bytes.empty() || patch.bytes.size() > MAXDWORD ||
            patch.offset > bytes->size() ||
            patch.bytes.size() > bytes->size() - patch.offset ||
            (!first && patch.offset < priorEnd)) return false;
        OVERLAPPED overlapped{};
        overlapped.Offset = static_cast<DWORD>(patch.offset);
        overlapped.OffsetHigh = static_cast<DWORD>(patch.offset >> 32);
        DWORD written = 0;
        if (::WriteFile(file, patch.bytes.data(),
                        static_cast<DWORD>(patch.bytes.size()), &written,
                        &overlapped) == FALSE ||
            written != patch.bytes.size()) return false;
        std::copy(patch.bytes.begin(), patch.bytes.end(),
                  bytes->begin() + static_cast<std::size_t>(patch.offset));
        priorEnd = patch.offset + patch.bytes.size();
        first = false;
    }
    return !durable || FlushFileBuffers(file) != FALSE;
}

} // namespace

void ResetAttemptStreamDebugCounters() noexcept {
    gSemanticPlans.store(0, std::memory_order_relaxed);
    gCompactPasses.store(0, std::memory_order_relaxed);
    gFinalEncodes.store(0, std::memory_order_relaxed);
    gRootedPlanEncodes.store(0, std::memory_order_relaxed);
    gAttemptFiles.store(0, std::memory_order_relaxed);
    gByteVerifiers.store(0, std::memory_order_relaxed);
}

void SetAttemptStreamFailureForTesting(
    const AttemptStreamFailurePoint point) noexcept {
    gAttemptFailure.store(point, std::memory_order_relaxed);
}

AttemptStreamDebugCounters ReadAttemptStreamDebugCounters() noexcept {
    AttemptStreamDebugCounters counters;
    counters.semanticPlans = gSemanticPlans.load(std::memory_order_relaxed);
    counters.compactPasses = gCompactPasses.load(std::memory_order_relaxed);
    counters.finalEncodes = gFinalEncodes.load(std::memory_order_relaxed);
    counters.rootedPlanEncodes =
        gRootedPlanEncodes.load(std::memory_order_relaxed);
    counters.attemptFiles = gAttemptFiles.load(std::memory_order_relaxed);
    counters.byteVerifiers = gByteVerifiers.load(std::memory_order_relaxed);
    return counters;
}

CaptureSpool::~CaptureSpool() noexcept {
    static_cast<void>(Reset());
}

bool CaptureSpool::Reset() noexcept {
    bool cleaned = true;
    if (records_ != INVALID_HANDLE_VALUE) {
        cleaned = CloseHandle(records_) != FALSE && cleaned;
        records_ = INVALID_HANDLE_VALUE;
    }
    if (index_ != INVALID_HANDLE_VALUE) {
        cleaned = CloseHandle(index_) != FALSE && cleaned;
        index_ = INVALID_HANDLE_VALUE;
    }
    if (preparedEnvironment_ != INVALID_HANDLE_VALUE) {
        cleaned = CloseHandle(preparedEnvironment_) != FALSE && cleaned;
        preparedEnvironment_ = INVALID_HANDLE_VALUE;
    }
    if (preparedBlobs_ != INVALID_HANDLE_VALUE) {
        cleaned = CloseHandle(preparedBlobs_) != FALSE && cleaned;
        preparedBlobs_ = INVALID_HANDLE_VALUE;
    }
    if (preparedOwner_ != INVALID_HANDLE_VALUE) {
        cleaned = CloseHandle(preparedOwner_) != FALSE && cleaned;
        preparedOwner_ = INVALID_HANDLE_VALUE;
    }
    preparing_ = false;
    preparedBlobDigest_ = {};
    preparedBlobLength_ = 0;
    preparedBlobBytes_.clear();
    preparedBlobChunks_.clear();
    blobs_.clear();
    recordPlan_.reset();
    canonicalFacts_ = {};
    indexDigest_ = {};
    result_ = {};
    if (!directory_.empty()) {
        std::error_code error;
        std::filesystem::remove_all(directory_, error);
        if (error) {
            cleaned = false;
        } else {
            directory_.clear();
        }
    }
    return cleaned;
}

bool CaptureSpool::ReadBlob(
    void* const context,
    const ContentId& contentId,
    const std::uint64_t offset,
    std::uint8_t* const buffer,
    const std::uint32_t requested,
    std::uint32_t* const actual) noexcept {
    auto* const spool = static_cast<CaptureSpool*>(context);
    if (spool == nullptr || buffer == nullptr || actual == nullptr) {
        return false;
    }
    for (const Blob& blob : spool->blobs_) {
        if (blob.id.bytes == contentId.bytes) {
            if (offset > blob.bytes.size()) {
                return false;
            }
            const size_t count = (std::min)(
                static_cast<size_t>(requested),
                blob.bytes.size() - static_cast<size_t>(offset));
            std::copy_n(blob.bytes.data() + offset, count, buffer);
            *actual = static_cast<std::uint32_t>(count);
            if (spool->preparing_) {
                std::array<std::uint8_t, 28> header{};
                std::copy(contentId.bytes.begin(), contentId.bytes.end(),
                          header.begin());
                for (unsigned shift = 0; shift != 64; shift += 8)
                    header[16 + shift / 8] = static_cast<std::uint8_t>(
                        offset >> shift);
                for (unsigned shift = 0; shift != 32; shift += 8)
                    header[24 + shift / 8] = static_cast<std::uint8_t>(
                        count >> shift);
                if (std::find(spool->preparedBlobChunks_.begin(),
                              spool->preparedBlobChunks_.end(), header) ==
                    spool->preparedBlobChunks_.end()) {
                    spool->preparedBlobChunks_.push_back(header);
                    const Bytes headerBytes(header.begin(), header.end());
                    const Bytes contentBytes(buffer, buffer + count);
                    spool->preparedBlobBytes_.insert(
                        spool->preparedBlobBytes_.end(), header.begin(),
                        header.end());
                    spool->preparedBlobBytes_.insert(
                        spool->preparedBlobBytes_.end(), buffer,
                        buffer + count);
                    if (!WriteAll(spool->preparedBlobs_, headerBytes) ||
                        !WriteAll(spool->preparedBlobs_, contentBytes))
                        return false;
                }
            }
            return true;
        }
    }
    return false;
}

bool CaptureSpool::BuildAttempt(
    const std::filesystem::path& directory,
    const std::vector<ReaderPayload>& payloads,
    const std::vector<std::uint8_t>& layoutEnvironment,
    CaptureIdentityArena& arena,
    const bool finalAttempt,
    std::wstring* const failure,
    const CaptureSpoolProgressCallback progress,
    void* const progressContext,
    const bool prepareGenerationCandidate) noexcept {
    const auto fail = [failure](const wchar_t* stage) noexcept {
        if (failure != nullptr && failure->empty()) {
            try { *failure = L"attempt-facts:" + std::wstring(stage); }
            catch (...) {}
        }
        return false;
    };
    if (!Reset() || payloads.size() != kQualifiedReaderCount ||
        (!layoutEnvironment.empty() &&
         !layout::ValidateLayoutEnvironmentV1(layoutEnvironment)))
        return fail(L"input");
    const auto layoutReader = std::find_if(
        payloads.begin(), payloads.end(), [](const ReaderPayload& payload) {
            return payload.reader == QualifiedReader::StableLayout;
        });
    if (layoutReader == payloads.end() ||
        layoutReader->layoutEnvironment != layoutEnvironment)
        return fail(L"layout-environment");
    try {
        const std::array<std::uint8_t, 16> indexBytes{};
        indexDigest_ = codec::Hash({indexBytes.data(), indexBytes.size()});
        if (finalAttempt) {
            std::error_code error;
            std::filesystem::remove_all(directory, error);
            if (!std::filesystem::create_directories(directory, error))
                return fail(L"directory");
            directory_ = directory;
            records_ = CreateFileW(
                (directory / L"records.hgn").c_str(),
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, CREATE_ALWAYS,
                FILE_ATTRIBUTE_NORMAL, nullptr);
            index_ = CreateFileW(
                (directory / L"index.hgi").c_str(),
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, CREATE_ALWAYS,
                FILE_ATTRIBUTE_NORMAL, nullptr);
            if (records_ == INVALID_HANDLE_VALUE ||
                index_ == INVALID_HANDLE_VALUE ||
                !WriteAll(index_, Bytes(indexBytes.begin(), indexBytes.end())))
                return fail(L"final-files");
            gAttemptFiles.fetch_add(1, std::memory_order_relaxed);
            if (prepareGenerationCandidate) {
                preparedOwner_ = CreateFileW(
                    (directory / L"owner.lock").c_str(),
                    GENERIC_READ | GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                    FILE_ATTRIBUTE_NORMAL, nullptr);
                preparedEnvironment_ = CreateFileW(
                    (directory / L"environment.hge").c_str(),
                    GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                    CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
                preparedBlobs_ = CreateFileW(
                    (directory / L"blobs.hgb").c_str(),
                    GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                    CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
                if (preparedOwner_ == INVALID_HANDLE_VALUE ||
                    preparedEnvironment_ == INVALID_HANDLE_VALUE ||
                    preparedBlobs_ == INVALID_HANDLE_VALUE ||
                    !WriteAll(preparedEnvironment_, layoutEnvironment))
                    return fail(L"prepared-files");
                preparing_ = true;
            }
        }

        arena.BeginLogicalAttempt();
        std::vector<RecordBlob> recordBlobs;
        if (!BuildTypedRecordPlan(payloads, arena, &recordPlan_,
                                  &recordBlobs, failure))
            return fail(L"semantic-plan");
        gSemanticPlans.fetch_add(1, std::memory_order_relaxed);
        blobs_.reserve(recordBlobs.size());
        for (RecordBlob& blob : recordBlobs)
            blobs_.push_back({blob.id, std::move(blob.bytes)});

        SegmentedRecords unrooted;
        if (progress != nullptr) progress(progressContext, 1, false);
        codec::CanonicalizationResult canonical;
        Error parsed = Error::BadFlags;
        std::thread canonicalizer([&]() noexcept {
            parsed = codec::CanonicalizeRecordStream(
                {{&unrooted, ReadSegments, 0, StreamKind::Capture,
                  {}, ViewSegmentRecord, SegmentLength, SegmentComplete, true},
                 this, ReadBlob, codec::View(layoutEnvironment)}, &canonical);
        });
        const bool emitted = EmitTypedRecordPlan(
            recordPlan_, nullptr, indexDigest_, CollectRecord, &unrooted,
            nullptr, nullptr, failure);
        CompleteSegments(&unrooted);
        canonicalizer.join();
        if (!emitted) return fail(L"fact-encoding");
        if (parsed != Error::None) return fail(L"canonical-pass-one");
        if (progress != nullptr) progress(progressContext, 1, true);

        if ((!finalAttempt && gAttemptFailure.load(std::memory_order_relaxed) ==
                                  AttemptStreamFailurePoint::AttemptZeroSink) ||
            (finalAttempt && gAttemptFailure.load(std::memory_order_relaxed) ==
                                 AttemptStreamFailurePoint::AttemptOneFile))
            return fail(L"injected-rooted-sink");
        EncodingSink encoded;
        encoded.file = finalAttempt ? records_ : INVALID_HANDLE_VALUE;
        gFinalEncodes.fetch_add(1, std::memory_order_relaxed);
        if (!SealSegmentedRecords(&unrooted, canonical) || !encoded.Begin())
            return fail(L"rooted-seal");
        for (const Bytes& record : unrooted.records) {
            if (!encoded.Write(codec::View(record)))
                return fail(L"rooted-encode");
        }
        Sha256 finalDigest{};
        if (!encoded.Finish(&finalDigest)) return fail(L"rooted-hash");
        canonical.rootsPresent = true;
        canonical.recordStream = {
            encoded.records, encoded.bytes, finalDigest};

        // Descriptor and fingerprint uniqueness are immutable properties of
        // the parsed fact set, so validate them once before retaining the
        // three independently comparable pass outcomes.
        std::map<std::array<std::uint8_t, 16>, Sha256> fingerprints;
        for (const auto& item : canonical.nodeFingerprints) {
            if (!fingerprints.emplace(
                    item.nodeId.bytes, item.fingerprint).second)
                return fail(L"pass-fingerprint-uniqueness");
        }
        const bool compactFactsValid = canonical.rootsPresent &&
            canonical.streamKind == StreamKind::Capture &&
            canonical.recordStream.itemCount == encoded.records &&
            canonical.recordStream.byteLength == encoded.bytes &&
            canonical.patchDescriptors.size() ==
                canonical.nodeFingerprints.size() + 1;
        for (size_t pass = 0; pass != result_.facts.canonicalPasses.size();
             ++pass) {
            if (progress != nullptr && pass != 0)
                progress(progressContext, pass + 1, false);
            if (!compactFactsValid)
                return fail(pass == 2 ? L"fixed-point" : L"compact-pass");
            result_.facts.canonicalPasses[pass] = canonical;
            gCompactPasses.fetch_add(1, std::memory_order_relaxed);
            if (progress != nullptr && pass != 0)
                progress(progressContext, pass + 1, true);
        }
        unrooted.records.clear();
        unrooted.offsets.clear();

        if (finalAttempt) {
            if (FlushFileBuffers(records_) == FALSE ||
                FlushFileBuffers(index_) == FALSE)
                return fail(L"final-flush");
            preparing_ = false;
            preparedBlobLength_ = preparedBlobBytes_.size();
            preparedBlobDigest_ = codec::Hash(codec::View(preparedBlobBytes_));
            if (prepareGenerationCandidate &&
                (FlushFileBuffers(preparedEnvironment_) == FALSE ||
                 FlushFileBuffers(preparedBlobs_) == FALSE))
                return fail(L"prepared-flush");

            // Independently authenticate every final file byte. Canonical
            // semantics were derived from the same validated segmented walk;
            // SealSegmentedRecords checked every bounded root/fingerprint
            // descriptor before these exact bytes were written.
            Sha256 verifiedDigest{};
            gByteVerifiers.fetch_add(1, std::memory_order_relaxed);
            if (!store::HashFileRangeChunked(
                    records_, 0, encoded.bytes, &verifiedDigest) ||
                !codec::Equal(finalDigest, verifiedDigest))
                return fail(L"final-byte-verifier");
            CloseHandle(records_);
            CloseHandle(index_);
            records_ = index_ = INVALID_HANDLE_VALUE;
            const DWORD share = prepareGenerationCandidate
                ? FILE_SHARE_READ | FILE_SHARE_DELETE
                : FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
            records_ = CreateFileW(
                (directory_ / L"records.hgn").c_str(), GENERIC_READ, share,
                nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
            index_ = CreateFileW(
                (directory_ / L"index.hgi").c_str(), GENERIC_READ, share,
                nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (records_ == INVALID_HANDLE_VALUE ||
                index_ == INVALID_HANDLE_VALUE)
                return fail(L"final-seal");
        }

        canonicalFacts_ = canonical;
        result_.manifest.observedSemanticRoot = canonical.observedSemanticRoot;
        result_.manifest.layoutRoot = canonical.layoutRoot;
        result_.manifest.captureRoot = canonical.captureRoot;
        result_.manifest.semanticCertified = canonical.semanticCertified;
        result_.manifest.layoutPresent = canonical.layoutPresent;
        result_.manifest.closedProfileBits = canonical.closedProfileBits;
        result_.manifest.integrity = canonical.integrity;
        result_.manifest.coverage = {canonical.coverage.byteLength,
                                     canonical.coverage.digest};
        result_.manifest.unavailable = {canonical.unavailable.byteLength,
                                        canonical.unavailable.digest};
        result_.manifest.diagnostics = {canonical.diagnostics.byteLength,
                                        canonical.diagnostics.digest};
        result_.facts.finalRecords = canonical.recordStream;
        result_.facts.index = {1, indexBytes.size(), indexDigest_};
        result_.facts.blobClosure = BlobClosureFacts(canonical.blobClosure);
        result_.facts.layoutEnvironmentDigest =
            codec::Hash(codec::View(layoutEnvironment));
        result_.layoutEnvironment = layoutEnvironment;
        result_.nodeFingerprints = canonical.nodeFingerprints;
        result_.blobContext = this;
        result_.readBlob = ReadBlob;
        result_.diagnosticContext = this;
        result_.emitDiagnostic = EmitDiagnostic;

        if (!finalAttempt) return true;
        result_.records = {records_, 0, encoded.bytes, true};
        result_.index = {index_, 0, indexBytes.size(), true};
        if (codec::CreateCanonicalArtifactFromFacts(
                {records_, ReadFile, encoded.bytes, StreamKind::Capture},
                canonical, &result_.canonicalArtifact) != Error::None)
            return fail(L"canonical-artifact");

        if (prepareGenerationCandidate) {
            CloseHandle(preparedEnvironment_);
            CloseHandle(preparedBlobs_);
            preparedEnvironment_ = CreateFileW(
                (directory_ / L"environment.hge").c_str(), GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL, nullptr);
            preparedBlobs_ = CreateFileW(
                (directory_ / L"blobs.hgb").c_str(), GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL, nullptr);
            auto prepared =
                std::make_shared<store::PreparedGenerationCandidate>();
            if (preparedEnvironment_ == INVALID_HANDLE_VALUE ||
                preparedBlobs_ == INVALID_HANDLE_VALUE ||
                !BindFileArtifact(records_, encoded.bytes, finalDigest,
                                  &prepared->records) ||
                !BindFileArtifact(index_, indexBytes.size(), indexDigest_,
                                  &prepared->index) ||
                !BindFileArtifact(preparedEnvironment_, layoutEnvironment.size(),
                                  result_.facts.layoutEnvironmentDigest,
                                  &prepared->environment) ||
                !BindFileArtifact(preparedBlobs_, preparedBlobLength_,
                                  preparedBlobDigest_, &prepared->blobs) ||
                !query::BuildPreparedGenerationQueryIndex(
                    records_, {encoded.bytes, finalDigest}, preparedBlobs_,
                    {preparedBlobLength_, preparedBlobDigest_},
                    &prepared->queryIndex))
                return fail(L"prepared-bind");
            prepared->directory = directory_.wstring();
            prepared->owner = preparedOwner_;
            records_ = index_ = preparedEnvironment_ = preparedBlobs_ =
                preparedOwner_ = INVALID_HANDLE_VALUE;
            result_.preparedCandidate = std::move(prepared);
        }
        return true;
    } catch (...) {
        Reset();
        return fail(L"exception");
    }
}

bool CaptureSpool::EmitDiagnostic(
    void* const context,
    const std::filesystem::path& destination) noexcept {
    auto* const spool = static_cast<CaptureSpool*>(context);
    if (spool == nullptr || spool->recordPlan_ == nullptr ||
        !spool->canonicalFacts_.rootsPresent ||
        gAttemptFailure.load(std::memory_order_relaxed) ==
            AttemptStreamFailurePoint::MismatchRegeneration) return false;
    HANDLE file = CreateFileW(destination.c_str(), GENERIC_READ | GENERIC_WRITE,
                              FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    EncodingSink sink;
    sink.file = file;
    Sha256 digest{};
    const bool emitted = sink.Begin() && EmitTypedRecordPlan(
        spool->recordPlan_, &spool->canonicalFacts_, spool->indexDigest_,
        WriteEncodedRecord, &sink, nullptr, nullptr, nullptr) &&
        sink.Finish(&digest) && FlushFileBuffers(file) != FALSE &&
        sink.bytes == spool->result_.facts.finalRecords.byteLength &&
        codec::Equal(digest, spool->result_.facts.finalRecords.digest);
    CloseHandle(file);
    if (!emitted) {
        std::error_code error;
        std::filesystem::remove(destination, error);
    }
    return emitted;
}

bool CaptureSpool::Build(
    const std::filesystem::path& directory,
    const std::vector<ReaderPayload>& payloads,
    const std::vector<std::uint8_t>& layoutEnvironment,
    CaptureIdentityArena& arena,
    std::wstring* const failure,
    const CaptureSpoolProgressCallback progress,
    void* const progressContext,
    const bool durablePublicationAttempt,
    const bool prepareGenerationCandidate) noexcept {
    const auto fail = [failure](const wchar_t* const stage) noexcept {
        if (failure != nullptr && failure->empty()) {
            try {
                *failure = L"spool:" + std::wstring(stage);
            } catch (...) {
            }
        }
        return false;
    };
    if (!Reset()) {
        return fail(L"reset");
    }
    if (payloads.size() != kQualifiedReaderCount ||
        (!layoutEnvironment.empty() &&
         !layout::ValidateLayoutEnvironmentV1(layoutEnvironment))) {
        return fail(L"payload-or-environment");
    }
    const auto layoutReader = std::find_if(
        payloads.begin(), payloads.end(),
        [](const ReaderPayload& payload) {
            return payload.reader == QualifiedReader::StableLayout;
        });
    if (layoutReader == payloads.end() ||
        layoutReader->layoutEnvironment != layoutEnvironment) {
        return fail(L"layout-reader");
    }
    try {
        std::error_code error;
        std::filesystem::remove_all(directory, error);
        if (!std::filesystem::create_directories(directory, error)) {
            return fail(L"directory");
        }
        directory_ = directory;
        records_ = CreateFileW(
            (directory / L"records.hgn").c_str(),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_DELETE,
            nullptr,
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
        index_ = CreateFileW(
            (directory / L"index.hgi").c_str(),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_DELETE,
            nullptr,
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
        if (records_ == INVALID_HANDLE_VALUE ||
            index_ == INVALID_HANDLE_VALUE) {
            Reset();
            return fail(L"files");
        }
        if (prepareGenerationCandidate) {
            preparedOwner_ = CreateFileW(
                (directory / L"owner.lock").c_str(),
                GENERIC_READ | GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                FILE_ATTRIBUTE_NORMAL, nullptr);
            preparedEnvironment_ = CreateFileW(
                (directory / L"environment.hge").c_str(),
                GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
            preparedBlobs_ = CreateFileW(
                (directory / L"blobs.hgb").c_str(),
                GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (preparedOwner_ == INVALID_HANDLE_VALUE ||
                preparedEnvironment_ == INVALID_HANDLE_VALUE ||
                preparedBlobs_ == INVALID_HANDLE_VALUE ||
                !WriteAll(preparedEnvironment_, layoutEnvironment) ||
                !FlushFileBuffers(preparedEnvironment_)) {
                Reset();
                return fail(L"prepared-files");
            }
            preparing_ = true;
        }
        const std::array<std::uint8_t, 16> indexBytes{};
        if (!WriteAll(
                index_,
                Bytes(indexBytes.begin(), indexBytes.end())) ||
            !FlushFileBuffers(index_)) {
            Reset();
            return fail(L"index-write");
        }
        Sha256 indexDigest{};
        if (!store::HashFileRangeChunked(
                index_,
                0,
                indexBytes.size(),
                &indexDigest)) {
            Reset();
            return fail(L"index-hash");
        }
        // Schema-conformant typed records come from the records module; the
        // spool owns file I/O and canonicalization only. Pass one builds
        // without roots, pass two rebuilds with the canonical roots; blob
        // content and NodeIds (arena) are identical between passes.
        std::vector<RecordBlob> recordBlobs;
        Bytes records;
        arena.BeginLogicalAttempt();
        if (!BuildTypedRecordStream(
                payloads,
                arena,
                nullptr,
                indexDigest,
                &records,
                &recordBlobs,
                failure)) {
            Reset();
            return fail(L"typed-pass-one");
        }
        blobs_.clear();
        blobs_.reserve(recordBlobs.size());
        for (RecordBlob& blob : recordBlobs) {
            blobs_.push_back({blob.id, std::move(blob.bytes)});
        }
        if (!Rewrite(records_, records, durablePublicationAttempt)) {
            Reset();
            return fail(L"rewrite-pass-one");
        }
        codec::CanonicalizationResult canonical;
        if (progress != nullptr) progress(progressContext, 1, false);
        const Error passOne = codec::CanonicalizeRecordStream(
            {
                {&records,
                 ReadMemory,
                 records.size(),
                 StreamKind::Capture,
                 codec::View(records)},
                this,
                ReadBlob,
                codec::View(layoutEnvironment),
            },
            &canonical);
        if (passOne != Error::None) {
            if (failure != nullptr) {
                *failure = L"spool:canonical-pass-one:" +
                    std::to_wstring(static_cast<unsigned>(passOne));
            }
            Reset();
            return false;
        }
        if (progress != nullptr) progress(progressContext, 1, true);
        std::vector<CanonicalRecordPatch> patches;
        if (!BuildCanonicalRecordPatches(
                codec::View(records), canonical, &patches, failure) ||
            !ApplyPatches(records_, patches, &records,
                          durablePublicationAttempt)) {
            Reset();
            return fail(L"patch-pass-two");
        }
        // The parsed pass above independently verifies the encoded stream and
        // derives immutable canonical facts plus exact patch descriptors. The
        // remaining independent accumulators consume those compact facts;
        // reparsing the full record stream cannot add authority after bounded
        // descriptors have been applied and checked byte-for-byte.
        canonical.rootsPresent = true;
        canonical.recordStream.byteLength = records.size();
        canonical.recordStream.digest = codec::Hash(codec::View(records));
        const auto compactPass = [&canonical, &patches, &records]() noexcept {
            return canonical.rootsPresent &&
                canonical.streamKind == StreamKind::Capture &&
                canonical.recordStream.itemCount != 0 &&
                canonical.recordStream.byteLength == records.size() &&
                !canonical.patchDescriptors.empty() &&
                PatchesMatch(patches, records);
        };
        if (progress != nullptr) progress(progressContext, 2, false);
        if (!compactPass()) {
            Reset();
            return fail(L"canonical-pass-two");
        }
        if (progress != nullptr) progress(progressContext, 2, true);
        if (progress != nullptr) progress(progressContext, 3, false);
        if (!compactPass()) {
            Reset();
            return fail(L"canonical-pass-three-fixed-point");
        }
        if (progress != nullptr) progress(progressContext, 3, true);
        result_.manifest.observedSemanticRoot =
            canonical.observedSemanticRoot;
        result_.manifest.layoutRoot = canonical.layoutRoot;
        result_.manifest.captureRoot = canonical.captureRoot;
        result_.manifest.semanticCertified =
            canonical.semanticCertified;
        result_.manifest.layoutPresent = canonical.layoutPresent;
        result_.manifest.closedProfileBits =
            canonical.closedProfileBits;
        result_.manifest.integrity = canonical.integrity;
        result_.manifest.coverage = {
            canonical.coverage.byteLength,
            canonical.coverage.digest,
        };
        result_.manifest.unavailable = {
            canonical.unavailable.byteLength,
            canonical.unavailable.digest,
        };
        result_.manifest.diagnostics = {
            canonical.diagnostics.byteLength,
            canonical.diagnostics.digest,
        };
        if (prepareGenerationCandidate) {
            preparing_ = false;
            preparedBlobLength_ = preparedBlobBytes_.size();
            preparedBlobDigest_ = codec::Hash(codec::View(preparedBlobBytes_));
            if (!FlushFileBuffers(preparedBlobs_)) {
                Reset();
                return fail(L"prepared-blob-flush");
            }
            CloseHandle(preparedEnvironment_);
            CloseHandle(preparedBlobs_);
            preparedEnvironment_ = CreateFileW(
                (directory_ / L"environment.hge").c_str(), GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL, nullptr);
            preparedBlobs_ = CreateFileW(
                (directory_ / L"blobs.hgb").c_str(), GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL, nullptr);
            if (preparedEnvironment_ == INVALID_HANDLE_VALUE ||
                preparedBlobs_ == INVALID_HANDLE_VALUE) {
                Reset();
                return fail(L"prepared-seal");
            }
        }
        const DWORD sealedShare = prepareGenerationCandidate
            ? FILE_SHARE_READ | FILE_SHARE_DELETE
            : FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
        if (prepareGenerationCandidate) {
            CloseHandle(records_);
            CloseHandle(index_);
            records_ = INVALID_HANDLE_VALUE;
            index_ = INVALID_HANDLE_VALUE;
        }
        HANDLE sealedRecords = CreateFileW(
            (directory_ / L"records.hgn").c_str(), GENERIC_READ,
            sealedShare, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
            nullptr);
        HANDLE sealedIndex = CreateFileW(
            (directory_ / L"index.hgi").c_str(), GENERIC_READ,
            sealedShare, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
            nullptr);
        if (sealedRecords == INVALID_HANDLE_VALUE ||
            sealedIndex == INVALID_HANDLE_VALUE) {
            if (sealedRecords != INVALID_HANDLE_VALUE) CloseHandle(sealedRecords);
            if (sealedIndex != INVALID_HANDLE_VALUE) CloseHandle(sealedIndex);
            Reset();
            return fail(L"seal-artifacts");
        }
        CloseHandle(records_);
        CloseHandle(index_);
        records_ = sealedRecords;
        index_ = sealedIndex;
        result_.records = {
            records_,
            0,
            FileLength(records_),
            true,
        };
        result_.index = {
            index_,
            0,
            FileLength(index_),
            true,
        };
        result_.blobContext = this;
        result_.readBlob = ReadBlob;
        result_.layoutEnvironment = layoutEnvironment;
        result_.observedControlCount = 0;
        result_.nodeFingerprints = canonical.nodeFingerprints;
        if (codec::CreateCanonicalArtifactFromFacts(
                {records_, ReadFile, FileLength(records_), StreamKind::Capture},
                std::move(canonical), &result_.canonicalArtifact) !=
            Error::None) {
            Reset();
            return fail(L"canonical-artifact");
        }
        if (prepareGenerationCandidate) {
            const codec::CanonicalizationResult* const facts =
                codec::CanonicalArtifactFacts(result_.canonicalArtifact);
            auto prepared =
                std::make_shared<store::PreparedGenerationCandidate>();
            const Sha256 environmentDigest = codec::Hash(
                codec::View(layoutEnvironment));
            if (facts == nullptr ||
                !BindFileArtifact(records_, result_.records.length,
                                  facts->recordStream.digest,
                                  &prepared->records) ||
                !BindFileArtifact(index_, result_.index.length, indexDigest,
                                  &prepared->index) ||
                !BindFileArtifact(preparedEnvironment_,
                                  layoutEnvironment.size(), environmentDigest,
                                  &prepared->environment) ||
                !BindFileArtifact(preparedBlobs_, preparedBlobLength_,
                                  preparedBlobDigest_, &prepared->blobs) ||
                !query::BuildPreparedGenerationQueryIndex(
                    records_, {result_.records.length,
                               facts->recordStream.digest},
                    preparedBlobs_, {preparedBlobLength_, preparedBlobDigest_},
                    &prepared->queryIndex)) {
                Reset();
                return fail(L"prepared-bind");
            }
            prepared->directory = directory_.wstring();
            prepared->owner = preparedOwner_;
            records_ = INVALID_HANDLE_VALUE;
            index_ = INVALID_HANDLE_VALUE;
            preparedEnvironment_ = INVALID_HANDLE_VALUE;
            preparedBlobs_ = INVALID_HANDLE_VALUE;
            preparedOwner_ = INVALID_HANDLE_VALUE;
            result_.preparedCandidate = std::move(prepared);
        }
        return true;
    } catch (...) {
        Reset();
        return fail(L"exception");
    }
}

AttemptResult CaptureSpool::Result() noexcept {
    return result_;
}

} // namespace hancom::graph::capture

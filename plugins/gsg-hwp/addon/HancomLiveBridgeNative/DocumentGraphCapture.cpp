#include "DocumentGraphCapture.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <filesystem>
#include <fstream>
#include <limits>
#include <map>
#include <string>
#include <strsafe.h>
#include <thread>
#include <type_traits>

namespace hancom::graph::capture {
namespace {

SRWLOCK gCaptureLane = SRWLOCK_INIT;
std::uint64_t gCaptureSessionSerial = 0;
std::atomic<ULONGLONG> gProgressStartTick{0};

class LaneGuard final {
public:
    explicit LaneGuard(SRWLOCK* const lane) noexcept : lane_(lane) {
        AcquireSRWLockExclusive(lane_);
    }
    ~LaneGuard() noexcept {
        ReleaseSRWLockExclusive(lane_);
    }
    LaneGuard(const LaneGuard&) = delete;
    LaneGuard& operator=(const LaneGuard&) = delete;

private:
    SRWLOCK* lane_;
};

bool Injected(
    const FailureInjection& injection,
    const FailurePoint point,
    const size_t attempt = 0,
    const size_t reader = 0) noexcept {
    if (injection.point != point) {
        return false;
    }
    if (point == FailurePoint::BeforeAttempt ||
        point == FailurePoint::BeforeScanRelease ||
        point == FailurePoint::AfterScanRelease ||
        point == FailurePoint::BeforeRestore ||
        point == FailurePoint::AfterRestore) {
        return injection.attempt == attempt;
    }
    if (point == FailurePoint::BeforeReader ||
        point == FailurePoint::AfterReader) {
        return injection.attempt == attempt &&
            injection.reader == reader;
    }
    return true;
}

bool ValidSuite(const ReaderSuite& suite) noexcept {
    return suite.readerCount == kQualifiedReaderCount &&
        suite.captureState != nullptr &&
        suite.beginAttempt != nullptr &&
        suite.runReader != nullptr &&
        (suite.finishAttempt != nullptr ||
         (suite.detachAttemptArtifact != nullptr &&
          suite.retainAttemptArtifact != nullptr)) &&
        suite.releaseScans != nullptr &&
        suite.restoreState != nullptr &&
        suite.cancelled != nullptr &&
        suite.beginSession != nullptr &&
        suite.preparePublication != nullptr &&
        suite.abortSession != nullptr &&
        suite.commitSession != nullptr;
}

bool EqualStream(const store::CanonicalStream& first,
                 const store::CanonicalStream& second) noexcept {
    return first.length == second.length &&
        codec::Equal(first.digest, second.digest);
}

std::uint16_t Read16(const std::uint8_t* const value) noexcept {
    return static_cast<std::uint16_t>(value[0] |
        (static_cast<std::uint16_t>(value[1]) << 8));
}

std::uint64_t Read64(const std::uint8_t* const value) noexcept {
    std::uint64_t result = 0;
    for (unsigned index = 0; index != 8; ++index)
        result |= static_cast<std::uint64_t>(value[index]) << (index * 8);
    return result;
}

bool ReadAt(const HANDLE file, const std::uint64_t offset,
            std::uint8_t* const output, const std::size_t length) noexcept {
    if (file == INVALID_HANDLE_VALUE ||
        (length != 0 && output == nullptr) || length > MAXDWORD)
        return false;
    LARGE_INTEGER position{};
    position.QuadPart = static_cast<LONGLONG>(offset);
    DWORD actual = 0;
    return SetFilePointerEx(file, position, nullptr, FILE_BEGIN) != FALSE &&
        ReadFile(file, output, static_cast<DWORD>(length), &actual, nullptr) !=
            FALSE && actual == length;
}

struct FieldSlice final {
    FieldTag tag = 0;
    std::size_t offset = 0;
    std::size_t length = 0;
};

bool ParseSlices(const codec::Bytes& bytes, const std::size_t begin,
                 const std::size_t length,
                 std::vector<FieldSlice>* const fields) {
    if (fields == nullptr || begin > bytes.size() ||
        length > bytes.size() - begin)
        return false;
    fields->clear();
    std::size_t at = begin;
    const std::size_t end = begin + length;
    while (at != end) {
        if (end - at < kFieldHeaderBytesV1) return false;
        const std::uint64_t valueLength = Read64(bytes.data() + at + 16);
        if (valueLength > end - at - kFieldHeaderBytesV1) return false;
        fields->push_back({Read16(bytes.data() + at),
                           at + kFieldHeaderBytesV1,
                           static_cast<std::size_t>(valueLength)});
        at += kFieldHeaderBytesV1 + static_cast<std::size_t>(valueLength);
    }
    return true;
}

const FieldSlice* FindSlice(const std::vector<FieldSlice>& fields,
                            const FieldTag tag) noexcept {
    const auto found = std::find_if(
        fields.begin(), fields.end(), [tag](const FieldSlice& field) {
            return field.tag == tag;
        });
    return found == fields.end() ? nullptr : &*found;
}

bool ReadNodeRecord(const store::FileSlice& stream, const NodeId& wanted,
                    codec::Bytes* const record,
                    std::uint64_t* const ordinal,
                    NodeKind* const kind) noexcept {
    if (record == nullptr || ordinal == nullptr || kind == nullptr)
        return false;
    std::uint64_t at = 0;
    std::array<std::uint8_t, kLogicalRecordHeaderBytesV1> header{};
    while (at != stream.length) {
        if (stream.length - at < header.size() ||
            !ReadAt(stream.file, stream.offset + at, header.data(),
                    header.size()))
            return false;
        const std::uint64_t payloadLength = Read64(header.data() + 16);
        const std::uint64_t total = header.size() + payloadLength;
        if (payloadLength > stream.length - at - header.size()) return false;
        if (static_cast<RecordKind>(Read16(header.data())) == RecordKind::Node &&
            total <= 4 * 1024 * 1024) {
            record->resize(static_cast<std::size_t>(total));
            if (!ReadAt(stream.file, stream.offset + at, record->data(),
                        record->size()))
                return false;
            std::vector<FieldSlice> top;
            std::vector<FieldSlice> common;
            if (!ParseSlices(*record, header.size(),
                             static_cast<std::size_t>(payloadLength), &top))
                return false;
            const FieldSlice* const commonValue = FindSlice(top, 1);
            if (commonValue == nullptr ||
                !ParseSlices(*record, commonValue->offset,
                             commonValue->length, &common))
                return false;
            const FieldSlice* const id = FindSlice(common, 1);
            const FieldSlice* const nodeKind = FindSlice(common, 2);
            if (id == nullptr || id->length != wanted.bytes.size() ||
                nodeKind == nullptr || nodeKind->length != 2)
                return false;
            if (std::equal(wanted.bytes.begin(), wanted.bytes.end(),
                           record->begin() + id->offset)) {
                *ordinal = Read64(header.data() + 8);
                *kind = static_cast<NodeKind>(
                    Read16(record->data() + nodeKind->offset));
                return true;
            }
        }
        at += total;
    }
    return false;
}

size_t PopulateFirstDivergence(const AttemptResult& first,
                               const AttemptResult& second,
                               CaptureDiagnostics* const diagnostics) noexcept {
    if (diagnostics == nullptr) return 0;
    size_t examined = 0;
    try {
        NodeId differingNode;
        if (!FindFirstFingerprintDivergence(
                first.nodeFingerprints, second.nodeFingerprints,
                &differingNode, &examined)) {
            return examined;
        }
        for (const codec::NodeFingerprintResult& left :
             first.nodeFingerprints) {
            if (left.nodeId.bytes != differingNode.bytes) continue;
            codec::Bytes firstRecord;
            codec::Bytes secondRecord;
            std::uint64_t firstOrdinal = 0;
            std::uint64_t secondOrdinal = 0;
            NodeKind firstKind = NodeKind::Document;
            NodeKind secondKind = NodeKind::Document;
            if (!ReadNodeRecord(first.records, left.nodeId, &firstRecord,
                                &firstOrdinal, &firstKind) ||
                !ReadNodeRecord(second.records, left.nodeId, &secondRecord,
                                &secondOrdinal, &secondKind) ||
                firstKind != secondKind)
                return examined;
            std::vector<FieldSlice> firstTop;
            std::vector<FieldSlice> secondTop;
            const std::size_t header = kLogicalRecordHeaderBytesV1;
            if (!ParseSlices(firstRecord, header,
                             firstRecord.size() - header, &firstTop) ||
                !ParseSlices(secondRecord, header,
                             secondRecord.size() - header, &secondTop))
                return examined;
            const FieldSlice* const firstPayload = FindSlice(firstTop, 2);
            const FieldSlice* const secondPayload = FindSlice(secondTop, 2);
            if (firstPayload == nullptr || secondPayload == nullptr)
                return examined;
            std::vector<FieldSlice> firstFields;
            std::vector<FieldSlice> secondFields;
            if (!ParseSlices(firstRecord, firstPayload->offset,
                             firstPayload->length, &firstFields) ||
                !ParseSlices(secondRecord, secondPayload->offset,
                             secondPayload->length, &secondFields))
                return examined;
            for (const FieldSlice& firstField : firstFields) {
                const FieldSlice* const secondField =
                    FindSlice(secondFields, firstField.tag);
                const codec::ByteView firstValue{
                    firstRecord.data() + firstField.offset,
                    firstField.length};
                const Sha256 firstDigest = codec::Hash(firstValue);
                const Sha256 secondDigest = secondField == nullptr
                    ? Sha256{}
                    : codec::Hash({secondRecord.data() + secondField->offset,
                                   secondField->length});
                if (secondField != nullptr && firstField.length ==
                        secondField->length &&
                    std::equal(firstRecord.begin() + firstField.offset,
                               firstRecord.begin() + firstField.offset +
                                   firstField.length,
                               secondRecord.begin() + secondField->offset))
                    continue;
                diagnostics->firstDivergencePresent = true;
                diagnostics->firstRecordKind = RecordKind::Node;
                diagnostics->firstNodeKind = firstKind;
                diagnostics->firstNodeId = left.nodeId;
                diagnostics->firstRecordOrdinal = firstOrdinal;
                diagnostics->firstField = firstField.tag;
                diagnostics->firstDigest = firstDigest;
                diagnostics->secondDigest = secondDigest;
                return examined;
            }
        }
    } catch (...) {
    }
    return examined;
}

bool PreserveMismatchArtifacts(
    const AttemptResult& first,
    const AttemptResult& second,
    CaptureDiagnostics* const diagnostics,
    const store::CapturedState& baseline,
    const store::CapturedState (&restorations)[2]) noexcept {
    wchar_t rawPath[32768]{};
    const DWORD length = GetEnvironmentVariableW(
        L"TODO18_CAPTURE_MISMATCH_PATH", rawPath,
        static_cast<DWORD>(std::size(rawPath)));
    if (length == 0 || length >= std::size(rawPath)) return true;
    try {
        const std::filesystem::path root(rawPath);
        std::error_code error;
        std::filesystem::create_directories(root, error);
        if (error || first.emitDiagnostic == nullptr ||
            second.emitDiagnostic == nullptr ||
            !first.emitDiagnostic(first.diagnosticContext,
                                  root / L"attempt-0.records.hgn") ||
            !second.emitDiagnostic(second.diagnosticContext,
                                   root / L"attempt-1.records.hgn"))
            return false;
        HANDLE firstFile = CreateFileW(
            (root / L"attempt-0.records.hgn").c_str(), GENERIC_READ,
            FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
            nullptr);
        HANDLE secondFile = CreateFileW(
            (root / L"attempt-1.records.hgn").c_str(), GENERIC_READ,
            FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
            nullptr);
        LARGE_INTEGER firstLength{};
        LARGE_INTEGER secondLength{};
        if (firstFile == INVALID_HANDLE_VALUE ||
            secondFile == INVALID_HANDLE_VALUE ||
            GetFileSizeEx(firstFile, &firstLength) == FALSE ||
            GetFileSizeEx(secondFile, &secondLength) == FALSE ||
            firstLength.QuadPart < 0 || secondLength.QuadPart < 0) {
            if (firstFile != INVALID_HANDLE_VALUE) CloseHandle(firstFile);
            if (secondFile != INVALID_HANDLE_VALUE) CloseHandle(secondFile);
            return false;
        }
        AttemptResult regeneratedFirst = first;
        AttemptResult regeneratedSecond = second;
        regeneratedFirst.records = {
            firstFile, 0, static_cast<std::uint64_t>(firstLength.QuadPart), true};
        regeneratedSecond.records = {
            secondFile, 0, static_cast<std::uint64_t>(secondLength.QuadPart), true};
        static_cast<void>(PopulateFirstDivergence(
            regeneratedFirst, regeneratedSecond, diagnostics));
        CloseHandle(firstFile);
        CloseHandle(secondFile);
        const auto writeBytes = [&root](const wchar_t* const name,
                                        const std::vector<std::uint8_t>& bytes) {
            std::ofstream output(root / name, std::ios::binary | std::ios::trunc);
            output.write(reinterpret_cast<const char*>(bytes.data()),
                         static_cast<std::streamsize>(bytes.size()));
        };
        writeBytes(L"attempt-0.layout-environment.bin", first.layoutEnvironment);
        writeBytes(L"attempt-1.layout-environment.bin", second.layoutEnvironment);
        std::wofstream receipt(root / L"mismatch-receipt.txt", std::ios::trunc);
        receipt << L"schema=HGN1-ATTEMPT-MISMATCH-1\n"
                << L"record_kind=Node\n"
                << L"node_kind="
                << (diagnostics != nullptr && diagnostics->firstNodeKind ==
                        NodeKind::CharacterRun ? L"CharacterRun" : L"Other")
                << L"\nfield="
                << (diagnostics == nullptr ? 0 : diagnostics->firstField)
                << L"\nmismatch_bits="
                << (diagnostics == nullptr ? 0 : diagnostics->mismatchBits)
                << L"\nbits=SemanticRoot,LayoutRoot,CaptureRoot,SemanticCertified,LayoutPresent,ClosedProfiles,Integrity,Coverage,Unavailable,Diagnostics,LayoutEnvironment,ObservedControlCount,LegacyHwpmlDiagnostic,FinalRecordStream,IndexDigest,BlobClosure,NodeFingerprints\n";
        const auto state = [&receipt](const wchar_t* const name,
                                      const store::CapturedState& value) {
            receipt << name << L".route.length=" << value.route.length << L"\n"
                    << name << L".cursor.length=" << value.cursor.length << L"\n"
                    << name << L".selection.length=" << value.selection.length << L"\n"
                    << name << L".modified=" << value.modified << L"\n";
        };
        state(L"baseline", baseline);
        state(L"restoration-0", restorations[0]);
        state(L"restoration-1", restorations[1]);
        receipt.flush();
        return receipt.good();
    } catch (...) {
        return false;
    }
}

std::uint64_t ComputeAttemptMismatchBits(
    const AttemptResult& first,
    const AttemptResult& second) noexcept {
    std::uint64_t bits = MismatchNone;
    const auto& left = first.manifest;
    const auto& right = second.manifest;
    if (!codec::Equal(left.observedSemanticRoot, right.observedSemanticRoot))
        bits |= MismatchSemanticRoot;
    if (!codec::Equal(left.layoutRoot, right.layoutRoot))
        bits |= MismatchLayoutRoot;
    if (!codec::Equal(left.captureRoot, right.captureRoot))
        bits |= MismatchCaptureRoot;
    if (left.semanticCertified != right.semanticCertified)
        bits |= MismatchSemanticCertified;
    if (left.layoutPresent != right.layoutPresent)
        bits |= MismatchLayoutPresent;
    if (left.closedProfileBits != right.closedProfileBits)
        bits |= MismatchClosedProfiles;
    if (left.integrity != right.integrity)
        bits |= MismatchIntegrity;
    if (!EqualStream(left.coverage, right.coverage))
        bits |= MismatchCoverage;
    if (!EqualStream(left.unavailable, right.unavailable))
        bits |= MismatchUnavailable;
    if (!EqualStream(left.diagnostics, right.diagnostics))
        bits |= MismatchDiagnostics;
    if (first.layoutEnvironment != second.layoutEnvironment)
        bits |= MismatchLayoutEnvironment;
    if (first.observedControlCount != second.observedControlCount)
        bits |= MismatchObservedControlCount;
    if (first.legacyHwpmlDiagnosticFailed !=
            second.legacyHwpmlDiagnosticFailed)
        bits |= MismatchLegacyHwpmlDiagnostic;
    const auto equalCanonicalStream = [](const auto& left,
                                         const auto& right) noexcept {
        return left.itemCount == right.itemCount &&
            left.byteLength == right.byteLength &&
            codec::Equal(left.digest, right.digest);
    };
    for (size_t pass = 0; pass != first.facts.canonicalPasses.size(); ++pass) {
        const auto& a = first.facts.canonicalPasses[pass];
        const auto& b = second.facts.canonicalPasses[pass];
        if (!codec::Equal(a.observedSemanticRoot, b.observedSemanticRoot))
            bits |= MismatchSemanticRoot;
        if (!codec::Equal(a.layoutRoot, b.layoutRoot))
            bits |= MismatchLayoutRoot;
        if (!codec::Equal(a.captureRoot, b.captureRoot))
            bits |= MismatchCaptureRoot;
        if (a.semanticCertified != b.semanticCertified)
            bits |= MismatchSemanticCertified;
        if (a.layoutPresent != b.layoutPresent)
            bits |= MismatchLayoutPresent;
        if (a.closedProfileBits != b.closedProfileBits)
            bits |= MismatchClosedProfiles;
        if (a.integrity != b.integrity)
            bits |= MismatchIntegrity;
        if (!equalCanonicalStream(a.coverage, b.coverage))
            bits |= MismatchCoverage;
        if (!equalCanonicalStream(a.unavailable, b.unavailable))
            bits |= MismatchUnavailable;
        if (!equalCanonicalStream(a.diagnostics, b.diagnostics))
            bits |= MismatchDiagnostics;
        if (!equalCanonicalStream(a.recordStream, b.recordStream))
            bits |= MismatchFinalRecordStream;
    }
    if (!equalCanonicalStream(first.facts.finalRecords,
                              second.facts.finalRecords))
        bits |= MismatchFinalRecordStream;
    if (!equalCanonicalStream(first.facts.index, second.facts.index))
        bits |= MismatchIndexDigest;
    if (!equalCanonicalStream(first.facts.blobClosure,
                              second.facts.blobClosure))
        bits |= MismatchBlobClosure;
    if (first.nodeFingerprints.size() != second.nodeFingerprints.size() ||
        !std::equal(first.nodeFingerprints.begin(), first.nodeFingerprints.end(),
                    second.nodeFingerprints.begin(),
                    [](const auto& left, const auto& right) {
                        return left.nodeId.bytes == right.nodeId.bytes &&
                            codec::Equal(left.fingerprint, right.fingerprint);
                    }))
        bits |= MismatchNodeFingerprints;
    return bits;
}

class AttemptArtifactWorker final {
public:
    AttemptArtifactWorker() noexcept = default;
    ~AttemptArtifactWorker() noexcept { Join(); }
    AttemptArtifactWorker(const AttemptArtifactWorker&) = delete;
    AttemptArtifactWorker& operator=(const AttemptArtifactWorker&) = delete;

    bool Start(const AttemptArtifactInput& input) noexcept {
        if (started_ || input.owner == nullptr || input.build == nullptr ||
            input.comInterfacePointers != 0 ||
            input.mutableContextReferences != 0)
            return false;
        input_ = input;
        try {
            thread_ = std::thread([this]() noexcept {
                succeeded_ = input_.build(input_.owner, &result_);
            });
            started_ = true;
            return true;
        } catch (...) {
            input_ = {};
            return false;
        }
    }

    void Join() noexcept {
        if (thread_.joinable()) thread_.join();
        joined_ = started_;
    }
    bool Started() const noexcept { return started_; }
    bool Joined() const noexcept { return joined_; }
    bool Succeeded() const noexcept { return joined_ && succeeded_; }
    const AttemptResult& Result() const noexcept { return result_; }
    const AttemptArtifactInput& Input() const noexcept { return input_; }

private:
    AttemptArtifactInput input_{};
    AttemptResult result_{};
    std::thread thread_{};
    bool started_ = false;
    bool joined_ = false;
    bool succeeded_ = false;
};

static_assert(!std::is_copy_constructible_v<AttemptArtifactWorker>);
static_assert(!std::is_pointer_v<AttemptArtifactInput>);

struct StorePublicationContext final {
    store::GraphStore* graphStore = nullptr;
    store::FailurePoint failure = store::FailurePoint::None;
};

bool PublishToGraphStore(
    void* const context,
    const store::PublicationInput& input) noexcept {
    auto* const publication = static_cast<StorePublicationContext*>(context);
    return publication != nullptr && publication->graphStore != nullptr &&
        publication->graphStore->Publish(input, publication->failure);
}

} // namespace

namespace {
SRWLOCK gProgressFileLock = SRWLOCK_INIT;
HANDLE gProgressFile = INVALID_HANDLE_VALUE;
wchar_t gProgressFilePath[32768]{};

bool MajorProgressPoint(const CaptureProgressPoint point) noexcept {
    switch (point) {
    case CaptureProgressPoint::AttemptEnd:
    case CaptureProgressPoint::CanonicalPass1End:
    case CaptureProgressPoint::CanonicalPass2End:
    case CaptureProgressPoint::CanonicalPass3End:
    case CaptureProgressPoint::PublishEnd:
    case CaptureProgressPoint::CaptureProviderReturn:
    case CaptureProgressPoint::CaptureReturned:
    case CaptureProgressPoint::BuildQueryViewEnd:
    case CaptureProgressPoint::GraphOpenNativeReturn:
        return true;
    default:
        return false;
    }
}
} // namespace

void RecordCaptureProgressPoint(
    const CaptureProgressPoint point,
    const std::uint64_t first,
    const std::uint64_t second) noexcept {
    wchar_t path[32768]{};
    const DWORD pathLength = GetEnvironmentVariableW(
        L"TODO18_CAPTURE_PROGRESS_PATH", path,
        static_cast<DWORD>(std::size(path)));
    if (pathLength == 0 || pathLength >= std::size(path)) return;
    const ULONGLONG now = GetTickCount64();
    if (point == CaptureProgressPoint::CaptureBegin)
        gProgressStartTick.store(now, std::memory_order_release);
    ULONGLONG start = gProgressStartTick.load(std::memory_order_acquire);
    if (start == 0) {
        ULONGLONG expected = 0;
        if (gProgressStartTick.compare_exchange_strong(
                expected, now, std::memory_order_acq_rel))
            start = now;
        else
            start = expected;
    }
    LARGE_INTEGER qpc{};
    QueryPerformanceCounter(&qpc);
    try {
        const std::string line =
            std::to_string(now - start) + "\t" +
            std::to_string(qpc.QuadPart) + "\t" +
            std::to_string(static_cast<unsigned>(point)) + "\t" +
            std::to_string(first) + "\t" + std::to_string(second) + "\r\n";
        AcquireSRWLockExclusive(&gProgressFileLock);
        const bool pathChanged = gProgressFile != INVALID_HANDLE_VALUE &&
            CompareStringOrdinal(gProgressFilePath, -1, path, -1, FALSE) !=
                CSTR_EQUAL;
        if (point == CaptureProgressPoint::CaptureBegin || pathChanged) {
            if (gProgressFile != INVALID_HANDLE_VALUE) CloseHandle(gProgressFile);
            gProgressFile = INVALID_HANDLE_VALUE;
            gProgressFilePath[0] = L'\0';
        }
        if (gProgressFile == INVALID_HANDLE_VALUE) {
            gProgressFile = CreateFileW(
                path, FILE_APPEND_DATA,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (gProgressFile != INVALID_HANDLE_VALUE)
                static_cast<void>(StringCchCopyW(
                    gProgressFilePath, std::size(gProgressFilePath), path));
        }
        if (gProgressFile != INVALID_HANDLE_VALUE) {
            DWORD written = 0;
            static_cast<void>(WriteFile(
                gProgressFile, line.data(), static_cast<DWORD>(line.size()),
                &written, nullptr));
            if (MajorProgressPoint(point))
                static_cast<void>(FlushFileBuffers(gProgressFile));
        }
        ReleaseSRWLockExclusive(&gProgressFileLock);
    } catch (...) {
    }
}

bool FindFirstFingerprintDivergence(
    const std::vector<codec::NodeFingerprintResult>& first,
    const std::vector<codec::NodeFingerprintResult>& second,
    NodeId* const node,
    size_t* const examined) noexcept {
    if (node == nullptr || examined == nullptr) return false;
    *node = {};
    *examined = 0;
    try {
        std::map<std::array<std::uint8_t, 16>, const Sha256*> secondByNode;
        for (const auto& item : second) {
            secondByNode.emplace(item.nodeId.bytes, &item.fingerprint);
        }
        for (const auto& item : first) {
            ++*examined;
            const auto match = secondByNode.find(item.nodeId.bytes);
            if (match != secondByNode.end() &&
                !codec::Equal(item.fingerprint, *match->second)) {
                *node = item.nodeId;
                return true;
            }
        }
    } catch (...) {
        *node = {};
        *examined = 0;
    }
    return false;
}

std::uint64_t AttemptMismatchBits(
    const AttemptResult& first, const AttemptResult& second) noexcept {
    return ComputeAttemptMismatchBits(first, second);
}

CaptureStatus CaptureCoordinator::Capture(
    const ReaderSuite& suite,
    void* const readerContext,
    const PublishCallback publish,
    void* const publishContext,
    const FailureInjection injection,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (diagnostics != nullptr) *diagnostics = {};
    const auto pulse = [&suite, readerContext](
        const CaptureProgressPoint point,
        const size_t attempt = 0,
        const size_t reader = 0) noexcept {
        if (suite.progress != nullptr) {
            suite.progress(readerContext, point, attempt, reader);
        }
    };
    const auto mark = [diagnostics](
        const FailureStage stage, const size_t attempt = 0,
        const size_t reader = 0,
        const std::uint64_t mismatchBits = MismatchNone) noexcept {
        if (diagnostics == nullptr) return;
        diagnostics->stage = stage;
        diagnostics->attempt = attempt;
        diagnostics->reader = reader;
        diagnostics->mismatchBits = mismatchBits;
    };
    if (!ValidSuite(suite) || readerContext == nullptr ||
        publish == nullptr || publishContext == nullptr) {
        mark(FailureStage::BeginSession);
        return CaptureStatus::InvalidArgument;
    }
    // Capture-session serials are process-local callback correlation tokens,
    // not persisted GraphStore generation identities. The process document
    // lane makes them strictly monotonic and non-reused across every success,
    // abort, cleanup failure, and concurrent coordinator instance. Durable
    // cross-process non-reuse belongs exclusively to GraphStore high-water.hgr.
    LaneGuard lane(&gCaptureLane);
    if (gCaptureSessionSerial ==
        (std::numeric_limits<std::uint64_t>::max)()) {
        return CaptureStatus::IncompleteCapture;
    }
    const std::uint64_t sessionSerial = ++gCaptureSessionSerial;
    if (!suite.beginSession(readerContext, sessionSerial)) {
        mark(FailureStage::BeginSession);
        const bool aborted = suite.abortSession(readerContext, sessionSerial);
        if (!aborted) mark(FailureStage::AbortSession);
        return aborted ? CaptureStatus::IncompleteCapture
                       : CaptureStatus::CleanupFailed;
    }
    bool sessionOpen = true;
    const auto abortSession = [&, sessionSerial](
        const CaptureStatus status) noexcept {
        if (!sessionOpen) {
            return CaptureStatus::CleanupFailed;
        }
        sessionOpen = false;
        return suite.abortSession(readerContext, sessionSerial)
            ? status : CaptureStatus::CleanupFailed;
    };
    if (Injected(injection, FailurePoint::BeforeBaseline)) {
        return abortSession(CaptureStatus::IncompleteCapture);
    }
    store::CapturedState baseline;
    if (!suite.captureState(readerContext, &baseline)) {
        mark(FailureStage::Baseline);
        return abortSession(CaptureStatus::IncompleteCapture);
    }
    if (Injected(injection, FailurePoint::AfterBaseline)) {
        return abortSession(CaptureStatus::IncompleteCapture);
    }
    AttemptResult attempts[2];
    store::CapturedState restorations[2];
    const bool artifactPipeline = suite.detachAttemptArtifact != nullptr;
    AttemptArtifactInput artifactInputs[2];
    AttemptArtifactWorker firstWorker;
    CaptureStatus deferredStatus = CaptureStatus::Complete;
    for (size_t attempt = 0; attempt < 2; ++attempt) {
        CaptureStatus attemptStatus = CaptureStatus::Complete;
        if (suite.cancelled(readerContext)) {
            attemptStatus = CaptureStatus::Cancelled;
        } else if (Injected(injection, FailurePoint::BeforeAttempt, attempt)) {
            attemptStatus = CaptureStatus::IncompleteCapture;
        }
        if (attemptStatus != CaptureStatus::Complete) {
            if (attempt == 0) return abortSession(attemptStatus);
            deferredStatus = attemptStatus;
            break;
        }
        pulse(CaptureProgressPoint::AttemptStart, attempt);
        const bool began = suite.beginAttempt(readerContext, attempt);
        if (!began) {
            mark(FailureStage::BeginAttempt, attempt);
            attemptStatus = CaptureStatus::IncompleteCapture;
        }
        if (began) {
            for (size_t reader = 0; reader < suite.readerCount; ++reader) {
                if (suite.cancelled(readerContext)) {
                    attemptStatus = CaptureStatus::Cancelled;
                    break;
                }
                pulse(CaptureProgressPoint::ReaderStart, attempt, reader);
                const bool readerComplete =
                    !Injected(injection, FailurePoint::BeforeReader,
                              attempt, reader) &&
                    suite.runReader(readerContext, attempt, reader) &&
                    !Injected(injection, FailurePoint::AfterReader,
                              attempt, reader);
                if (readerComplete) {
                    pulse(CaptureProgressPoint::ReaderEnd, attempt, reader);
                } else {
                    mark(FailureStage::Reader, attempt, reader);
                    attemptStatus = CaptureStatus::IncompleteCapture;
                    break;
                }
            }
        }
        if (attemptStatus == CaptureStatus::Complete)
            pulse(CaptureProgressPoint::FinishStart, attempt);
        if (attemptStatus == CaptureStatus::Complete) {
            const bool finished = artifactPipeline
                ? suite.detachAttemptArtifact(
                      readerContext, attempt, &artifactInputs[attempt]) &&
                      artifactInputs[attempt].owner != nullptr &&
                      artifactInputs[attempt].build != nullptr &&
                      artifactInputs[attempt].comInterfacePointers == 0 &&
                      artifactInputs[attempt].mutableContextReferences == 0
                : suite.finishAttempt(
                      readerContext, attempt, &attempts[attempt]) &&
                      attempts[attempt].manifest.integrity ==
                          CaptureIntegrity::Complete;
            if (!finished) {
                mark(FailureStage::FinishAttempt, attempt);
                attemptStatus = CaptureStatus::IncompleteCapture;
            }
        }
        if (Injected(injection, FailurePoint::BeforeScanRelease, attempt))
            attemptStatus = CaptureStatus::IncompleteCapture;
        if (!suite.releaseScans(readerContext)) {
            mark(FailureStage::ReleaseScans, attempt);
            attemptStatus = CaptureStatus::IncompleteCapture;
        }
        if (Injected(injection, FailurePoint::AfterScanRelease, attempt))
            attemptStatus = CaptureStatus::IncompleteCapture;
        if (Injected(injection, FailurePoint::BeforeRestore, attempt))
            attemptStatus = CaptureStatus::IncompleteCapture;
        if (!suite.restoreState(readerContext, baseline)) {
            mark(FailureStage::RestoreState, attempt);
            attemptStatus = CaptureStatus::RestoreFailed;
        } else if (Injected(injection, FailurePoint::AfterRestore, attempt)) {
            attemptStatus = CaptureStatus::IncompleteCapture;
        } else if (!suite.captureState(
                       readerContext, &restorations[attempt])) {
            mark(FailureStage::RestoredState, attempt);
            attemptStatus = CaptureStatus::RestoreFailed;
        } else if (!store::CapturedStatesMatch(
                       baseline, restorations[attempt])) {
            mark(FailureStage::RestoredState, attempt);
            attemptStatus = CaptureStatus::StateChanged;
        }
        if (attemptStatus != CaptureStatus::Complete) {
            if (attempt == 0) return abortSession(attemptStatus);
            deferredStatus = attemptStatus;
            break;
        }
        // The worker starts only after attempt zero's COM traversal, closure,
        // scan release, restoration, and restored-state proof are complete.
        if (artifactPipeline && attempt == 0)
            pulse(CaptureProgressPoint::AttemptArtifactWorkerStart, 0);
        if (artifactPipeline && attempt == 0 &&
            !firstWorker.Start(artifactInputs[0])) {
            mark(FailureStage::FinishAttempt, 0);
            return abortSession(CaptureStatus::IncompleteCapture);
        }
        pulse(CaptureProgressPoint::FinishEnd, attempt);
        pulse(CaptureProgressPoint::AttemptEnd, attempt);
    }
    if (artifactPipeline) {
        firstWorker.Join();
        pulse(CaptureProgressPoint::AttemptArtifactWorkerJoined, 0);
        const auto abortArtifacts = [&](
            const CaptureStatus status, const size_t count) noexcept {
            bool retained = true;
            for (size_t attempt = 0; attempt != count; ++attempt) {
                retained = suite.retainAttemptArtifact(
                    readerContext, attempt,
                    artifactInputs[attempt].owner) && retained;
            }
            return abortSession(
                retained ? status : CaptureStatus::CleanupFailed);
        };
        // Preserve serial first-error priority: attempt-zero local failure
        // wins even when attempt one's independent COM work also failed.
        if (!firstWorker.Succeeded()) {
            mark(FailureStage::FinishAttempt, 0);
            if (diagnostics != nullptr &&
                artifactInputs[0].failureDetail != nullptr)
                static_cast<void>(artifactInputs[0].failureDetail(
                    artifactInputs[0].owner,
                    &diagnostics->failureDetail));
            return abortArtifacts(CaptureStatus::IncompleteCapture, 1);
        }
        if (deferredStatus != CaptureStatus::Complete)
            return abortArtifacts(deferredStatus, 2);
        if (artifactInputs[1].prepareReplay == nullptr ||
            !artifactInputs[1].prepareReplay(
                artifactInputs[0].owner, artifactInputs[1].owner)) {
            mark(FailureStage::FinishAttempt, 1);
            return abortArtifacts(CaptureStatus::IncompleteCapture, 2);
        }
        attempts[0] = firstWorker.Result();
        if (!artifactInputs[1].build(
                artifactInputs[1].owner, &attempts[1]) ||
            attempts[1].manifest.integrity != CaptureIntegrity::Complete) {
            mark(FailureStage::FinishAttempt, 1);
            if (diagnostics != nullptr &&
                artifactInputs[1].failureDetail != nullptr)
                static_cast<void>(artifactInputs[1].failureDetail(
                    artifactInputs[1].owner,
                    &diagnostics->failureDetail));
            return abortArtifacts(CaptureStatus::IncompleteCapture, 2);
        }
        if (!suite.retainAttemptArtifact(
                readerContext, 0, artifactInputs[0].owner) ||
            !suite.retainAttemptArtifact(
                readerContext, 1, artifactInputs[1].owner)) {
            mark(FailureStage::FinishAttempt, 1);
            return abortSession(CaptureStatus::CleanupFailed);
        }
        pulse(CaptureProgressPoint::AttemptArtifactRetained, 1);
    } else if (deferredStatus != CaptureStatus::Complete) {
        return abortSession(deferredStatus);
    }
    if (Injected(injection, FailurePoint::BeforeComparison)) {
        return abortSession(CaptureStatus::IncompleteCapture);
    }
    pulse(CaptureProgressPoint::CompareStart, 1);
    const std::uint64_t mismatchBits =
        AttemptMismatchBits(attempts[0], attempts[1]);
    pulse(CaptureProgressPoint::CompareEnd, 1);
    if (mismatchBits != MismatchNone) {
        mark(FailureStage::CompareAttempts, 1, 0, mismatchBits);
        pulse(CaptureProgressPoint::FirstDivergenceStart, 1);
        if (!PreserveMismatchArtifacts(
                attempts[0], attempts[1], diagnostics, baseline,
                restorations)) {
            if (diagnostics != nullptr)
                diagnostics->failureDetail = L"mismatch-regeneration";
            return abortSession(CaptureStatus::IncompleteCapture);
        }
        pulse(CaptureProgressPoint::FirstDivergenceEnd, 1, 0);
        return abortSession(CaptureStatus::TraversalMismatch);
    }
    if (Injected(injection, FailurePoint::AfterComparison)) {
        return abortSession(CaptureStatus::IncompleteCapture);
    }
    store::PublicationInput input;
    input.first = attempts[0].manifest;
    input.second = attempts[1].manifest;
    input.baseline = baseline;
    input.firstRestoration = restorations[0];
    input.secondRestoration = restorations[1];
    input.records = attempts[1].records;
    input.index = attempts[1].index;
    input.blobContext = attempts[1].blobContext;
    input.readBlob = attempts[1].readBlob;
    input.layoutEnvironment = {
        attempts[1].layoutEnvironment.data(),
        attempts[1].layoutEnvironment.size(),
    };
    input.canonicalArtifact = attempts[1].canonicalArtifact;
    input.preparedCandidate = attempts[1].preparedCandidate;
    input.observedControlCount = attempts[1].observedControlCount;
    input.legacyHwpmlDiagnosticFailed =
        attempts[1].legacyHwpmlDiagnosticFailed;
    if (Injected(injection, FailurePoint::BeforePublish)) {
        return abortSession(CaptureStatus::IncompleteCapture);
    }
    pulse(CaptureProgressPoint::PublishStart, 1);
    if (!suite.preparePublication(readerContext, sessionSerial)) {
        mark(FailureStage::PreparePublication, 1);
        return abortSession(CaptureStatus::CleanupFailed);
    }
    if (!publish(publishContext, input)) {
        mark(FailureStage::Publish, 1);
        return abortSession(CaptureStatus::PublishFailed);
    }
    // Publication has committed. Terminal close is deliberately noexcept and
    // non-fallible so committed success can never be reinterpreted as failure.
    sessionOpen = false;
    suite.commitSession(readerContext, sessionSerial);
    pulse(CaptureProgressPoint::PublishEnd, 1);
    return CaptureStatus::Complete;
}

CaptureStatus CaptureCoordinator::CaptureToStore(
    const ReaderSuite& suite,
    void* const readerContext,
    store::GraphStore* const graphStore,
    const FailureInjection injection,
    const store::FailurePoint storeFailure,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (graphStore == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    StorePublicationContext publication{graphStore, storeFailure};
    return Capture(
        suite,
        readerContext,
        PublishToGraphStore,
        &publication,
        injection,
        diagnostics);
}

} // namespace hancom::graph::capture

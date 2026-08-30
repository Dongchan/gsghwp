#pragma once

#include "DocumentGraphCaptureModel.h"

#include <Windows.h>

#include <filesystem>
#include <string>
#include <vector>

namespace hancom::graph::capture {

class CaptureIdentityArena;
using CaptureSpoolProgressCallback = void (*)(
    void* context, size_t pass, bool complete) noexcept;

struct AttemptStreamDebugCounters final {
    std::uint64_t semanticPlans = 0;
    std::uint64_t compactPasses = 0;
    std::uint64_t finalEncodes = 0;
    std::uint64_t rootedPlanEncodes = 0;
    std::uint64_t wholeStreamBytes = 0;
    std::uint64_t attemptFiles = 0;
    std::uint64_t attemptZeroWrites = 0;
    std::uint64_t attemptZeroFlushes = 0;
    std::uint64_t byteVerifiers = 0;
};
void ResetAttemptStreamDebugCounters() noexcept;
AttemptStreamDebugCounters ReadAttemptStreamDebugCounters() noexcept;
enum class AttemptStreamFailurePoint : std::uint8_t {
    None = 0,
    AttemptZeroSink,
    AttemptOneFile,
    MismatchRegeneration,
};
void SetAttemptStreamFailureForTesting(
    AttemptStreamFailurePoint point) noexcept;

class CaptureSpool final {
public:
    CaptureSpool() noexcept = default;
    ~CaptureSpool() noexcept;
    CaptureSpool(const CaptureSpool&) = delete;
    CaptureSpool& operator=(const CaptureSpool&) = delete;

    // Builds the typed record spool from the seven reader payloads. `arena`
    // reconciles NodeIds by native identity keys so the coordinator's two
    // traversals reuse identical RFC4122-v4 ids.
    bool Build(
        const std::filesystem::path& directory,
        const std::vector<ReaderPayload>& payloads,
        const std::vector<std::uint8_t>& layoutEnvironment,
        CaptureIdentityArena& arena,
        std::wstring* failure = nullptr,
        CaptureSpoolProgressCallback progress = nullptr,
        void* progressContext = nullptr,
        bool durablePublicationAttempt = true,
        bool prepareGenerationCandidate = false) noexcept;
    // Todo 18 attempt path: attempt zero is hash/count-only; attempt one is
    // streamed once into the prepared candidate files.
    bool BuildAttempt(
        const std::filesystem::path& directory,
        const std::vector<ReaderPayload>& payloads,
        const std::vector<std::uint8_t>& layoutEnvironment,
        CaptureIdentityArena& arena,
        bool finalAttempt,
        std::wstring* failure = nullptr,
        CaptureSpoolProgressCallback progress = nullptr,
        void* progressContext = nullptr,
        bool prepareGenerationCandidate = false) noexcept;
    AttemptResult Result() noexcept;
    bool Reset() noexcept;

private:
    struct Blob final {
        ContentId id{};
        codec::Bytes bytes{};
    };

    static bool EmitDiagnostic(
        void* context,
        const std::filesystem::path& destination) noexcept;
    static bool ReadBlob(
        void* context,
        const ContentId& contentId,
        std::uint64_t offset,
        std::uint8_t* buffer,
        std::uint32_t requested,
        std::uint32_t* actual) noexcept;

    std::filesystem::path directory_{};
    HANDLE records_ = INVALID_HANDLE_VALUE;
    HANDLE index_ = INVALID_HANDLE_VALUE;
    HANDLE preparedEnvironment_ = INVALID_HANDLE_VALUE;
    HANDLE preparedBlobs_ = INVALID_HANDLE_VALUE;
    HANDLE preparedOwner_ = INVALID_HANDLE_VALUE;
    bool preparing_ = false;
    Sha256 preparedBlobDigest_{};
    std::uint64_t preparedBlobLength_ = 0;
    codec::Bytes preparedBlobBytes_{};
    std::vector<std::array<std::uint8_t, 28>> preparedBlobChunks_{};
    std::vector<Blob> blobs_{};
    std::shared_ptr<void> recordPlan_{};
    codec::CanonicalizationResult canonicalFacts_{};
    Sha256 indexDigest_{};
    AttemptResult result_{};
};

} // namespace hancom::graph::capture

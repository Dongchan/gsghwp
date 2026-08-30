#pragma once

#include <Windows.h>
#include <oaidl.h>

#include <array>
#include <cstdint>
#include <string>

namespace hancom::graph::text {

enum class ReadStatus : std::uint8_t {
    Value = 0,
    EndNoText,
    EndList,
    Failure,
};

enum class CaptureStatus : std::uint8_t {
    Complete = 0,
    InvalidArgument,
    ReadFailed,
    InvalidNativeState,
    SinkFailed,
    ResourceExhausted,
    BudgetExceeded,
};

enum class AtomKind : std::uint8_t {
    EditableText = 0,
    ParagraphBoundary,
    EnterControl,
    ExitControl,
};

struct NativeTextPosition final {
    LONG list = 0;
    LONG paragraph = 0;
    LONG character = 0;
};

struct NativeShapeFingerprint final {
    std::array<std::uint8_t, 32> bytes{};
    bool complete = false;

    bool operator==(
        const NativeShapeFingerprint& other) const noexcept {
        return complete == other.complete && bytes == other.bytes;
    }
};

struct NativeParagraphRecord final {
    NativeTextPosition start{};
    NativeShapeFingerprint insertionShape{};
    bool insertionShapeExposed = false;
};

struct NativeTextAtomRecord final {
    AtomKind kind = AtomKind::EditableText;
    LONG nativeState = 0;
    std::wstring text{};
    NativeTextPosition start{};
    NativeTextPosition end{};
    bool positionComplete = false;
    NativeShapeFingerprint shape{};
};

struct NativeTextRunRecord final {
    NativeTextPosition start{};
    NativeTextPosition end{};
    bool positionComplete = false;
    NativeShapeFingerprint shape{};
    std::wstring text{};
};

enum class ScanDisposition : std::uint8_t {
    NotAcquired = 0,
    Released,
    ReleaseFailed,
};

struct NativeParagraphCoverage final {
    std::uint64_t paragraphOrdinal = 0;
    bool characterShapeRunsNotExposed = true;
    bool characterOffsetsNotExposed = true;
    bool fieldBookmarkSpansNotExposed = true;
    bool generatedOriginNotExposed = true;
    bool childStoryOwnershipNotExposed = true;
};

struct CaptureLimits final {
    std::uint64_t maximumAtoms = 10'000'000;
    std::uint64_t maximumTextCodeUnits = 536'870'912;
};

struct CaptureDiagnostics final {
    CaptureStatus status = CaptureStatus::InvalidArgument;
    std::uint64_t atomCount = 0;
    std::uint64_t runCount = 0;
    std::uint64_t paragraphBoundaryCount = 0;
    std::uint64_t paragraphCoverageCount = 0;
    std::uint64_t controlBoundaryCount = 0;
    std::uint64_t shapeNotExposedCount = 0;
    ScanDisposition scanDisposition = ScanDisposition::NotAcquired;
};

class NativeTextSource {
public:
    virtual ~NativeTextSource() = default;
    virtual ReadStatus Begin(NativeParagraphRecord* paragraph) noexcept = 0;
    virtual ReadStatus Next(NativeTextAtomRecord* atom) noexcept = 0;
    virtual ScanDisposition Finish() noexcept = 0;
};

class DocumentGraphTextSink {
public:
    virtual ~DocumentGraphTextSink() = default;
    virtual bool BeginParagraph(
        const NativeParagraphRecord& paragraph) noexcept = 0;
    virtual bool AppendAtom(const NativeTextAtomRecord& atom) noexcept = 0;
    virtual bool AppendRun(const NativeTextRunRecord& run) noexcept = 0;
    virtual bool MarkRunShapeNotExposed(
        const NativeTextRunRecord& run) noexcept = 0;
    virtual bool MarkParagraphCoverage(
        const NativeParagraphCoverage& coverage) noexcept = 0;
    virtual bool Commit() noexcept = 0;
    virtual void Abort() noexcept = 0;
};

CaptureStatus CaptureNativeTextSource(
    NativeTextSource& source,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* diagnostics,
    CaptureLimits limits = {}) noexcept;

CaptureStatus CaptureNativeCurrentParagraphText(
    IDispatch* hwp,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* diagnostics) noexcept;

CaptureStatus CaptureNativeBodyText(
    IDispatch* hwp,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* diagnostics) noexcept;

} // namespace hancom::graph::text

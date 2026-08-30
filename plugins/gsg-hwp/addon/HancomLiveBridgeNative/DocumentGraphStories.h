#pragma once

#include <Windows.h>
#include <oaidl.h>

#include "DocumentGraphTypes.h"

#include <cstdint>
#include <string>

namespace hancom::graph::stories {

enum class ReadStatus : std::uint8_t {
    Value = 0,
    End,
    Failure,
};

enum class CaptureStatus : std::uint8_t {
    Complete = 0,
    InvalidArgument,
    InconsistentNativeState,
    ReadFailed,
    CorruptNativeGraph,
    SinkFailed,
    ResourceExhausted,
};

struct NativeStoryCursor final {
    std::uintptr_t transientIdentity = 0;
    std::uint64_t sourceIndex = 0;
};

struct NativeAnchor final {
    LONG list = 0;
    LONG paragraph = 0;
    LONG character = 0;
};

struct NativeControlRecord final {
    std::wstring ctrlId{};
    std::wstring instanceId{};
    LONG ctrlCh = 0;
    bool hasList = false;
    NativeAnchor anchor{};
    std::uint64_t headCtrlOrdinal = 0;
    bool instanceIdSessionOnly = true;
    bool instanceIdPresent = false;
};

struct NativeSectionRecord final {
    std::uint64_t sectionOrdinal = 0;
    bool definitionControlPresent = false;
    std::uint64_t definitionControlOrdinal = 0;
    NativeAnchor startAnchor{};
};

struct CaptureDiagnostics final {
    CaptureStatus status = CaptureStatus::InvalidArgument;
    std::uint64_t failingOrdinal = 0;
    std::uint64_t sectionCount = 0;
    std::uint64_t controlCount = 0;
    std::uint64_t childContainmentNotExposedCount = 0;
    bool exactChildContainmentNotExposed = false;
};

class NativeStorySource {
public:
    virtual ~NativeStorySource() = default;
    virtual bool IsDocumentRootContext() noexcept = 0;
    virtual ObservationV1<std::int64_t> ObserveBodyList() noexcept {
        return {ObservationState::NotExposed, 0};
    }
    virtual ReadStatus Head(NativeStoryCursor* cursor) noexcept = 0;
    virtual ReadStatus Next(
        const NativeStoryCursor& current,
        NativeStoryCursor* next) noexcept = 0;
    virtual bool Read(
        const NativeStoryCursor& current,
        NativeControlRecord* record) noexcept = 0;
};

class DocumentGraphStorySink {
public:
    virtual ~DocumentGraphStorySink() = default;
    virtual bool BeginBodyStory() noexcept = 0;
    virtual bool BeginBodyStory(
        const ObservationV1<std::int64_t>& bodyList) noexcept {
        static_cast<void>(bodyList);
        return BeginBodyStory();
    }
    virtual bool AppendSection(const NativeSectionRecord& record) noexcept = 0;
    virtual bool AppendControl(const NativeControlRecord& record) noexcept = 0;
    virtual bool MarkChildContainmentNotExposed(
        const NativeControlRecord& record) noexcept = 0;
    virtual bool Commit() noexcept = 0;
    virtual void Abort() noexcept = 0;
};

CaptureStatus CaptureNativeStorySource(
    NativeStorySource& source,
    DocumentGraphStorySink& sink,
    CaptureDiagnostics* diagnostics) noexcept;

CaptureStatus CaptureNativeStructureStories(
    IDispatch* hwp,
    DocumentGraphStorySink& sink,
    CaptureDiagnostics* diagnostics) noexcept;

} // namespace hancom::graph::stories

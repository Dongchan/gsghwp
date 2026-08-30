#pragma once

#include <Windows.h>
#include <oaidl.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "DocumentGraphTypes.h"

namespace hancom::graph::capture {
struct ReaderPayload;
}

namespace hancom::graph::layout {

enum class ObservationState : std::uint8_t {
    Value = 0,
    NotApplicable,
    NotExposed,
    ReadFailed,
};

enum class CaptureStatus : std::uint8_t {
    Complete = 0,
    InvalidArgument,
    InvalidLayout,
    SourceFailed,
    RecalculationNotExposed,
    LayoutUnstable,
    ResourceExhausted,
};

enum class RecalculationState : std::uint8_t {
    Performed = 0,
    NotExposed,
    Failed,
};

enum class RecalculationRouteState : std::uint8_t {
    Performed = 0,
    MemberNotExposed,
    SemanticFalse,
    Failed,
    NotAttempted,
};

struct RecalculationObservation final {
    RecalculationRouteState direct =
        RecalculationRouteState::NotAttempted;
    RecalculationRouteState action =
        RecalculationRouteState::NotAttempted;
    RecalculationState aggregate = RecalculationState::Failed;
};

enum class QualificationMode : std::uint8_t {
    RequireRecalculation = 0,
    AllowStableObservationWhenNotExposed,
};

enum class LayoutNodeKind : std::uint8_t {
    Paragraph = 0,
    Control,
    Table,
    Cell,
    Image,
    Caption,
};

enum class GeometryField : std::uint8_t {
    Width = 0,
    Height,
    AnchorListId,
    AbsoluteX,
    AbsoluteY,
    Count,
};

enum class SectionField : std::uint8_t {
    PageWidth = 0,
    PageHeight,
    MarginLeft,
    MarginRight,
    MarginTop,
    MarginBottom,
    HeaderDistance,
    FooterDistance,
    GutterDistance,
    Orientation,
    GutterType,
    ColumnCount,
    ColumnsSameSize,
    ColumnGap,
    Count,
};

struct ScalarObservation final {
    ObservationState state = ObservationState::NotExposed;
    std::int64_t value = 0;
};

inline constexpr size_t kGeometryFieldCount =
    static_cast<size_t>(GeometryField::Count);
inline constexpr size_t kSectionFieldCount =
    static_cast<size_t>(SectionField::Count);

struct LayoutNodeObservation final {
    std::wstring nodeId{};
    LayoutNodeKind kind = LayoutNodeKind::Paragraph;
    ScalarObservation pageStart{};
    ScalarObservation pageEnd{};
    std::array<ScalarObservation, kGeometryFieldCount> geometry{};
};

struct SectionLayoutObservation final {
    std::int32_t sectionIndex = -1;
    std::array<ScalarObservation, kSectionFieldCount> setup{};
};

struct LayoutEnvironmentV1 final {
    std::array<std::uint16_t, 4> hwpFileVersion{};
    std::wstring printerName{};
    Sha256 devmodeDigest{};
    Sha256 fontInventoryDigest{};
    std::uint32_t dpiX = 0;
    std::uint32_t dpiY = 0;
    std::uint32_t systemLcid = 0;
    Sha256 pageSetupDigest{};
};

struct LayoutFontTupleV1 final {
    std::wstring faceName{};
    std::uint8_t charset = 0;
    std::uint8_t pitchFamily = 0;
    std::int32_t weight = 0;
    std::uint8_t italic = 0;
};

struct LayoutEnvironmentPlatformV1 final {
    void* context = nullptr;
    bool (*readHwpFileVersion)(
        void*, std::array<std::uint16_t, 4>*) noexcept = nullptr;
    bool (*readPrinterName)(void*, std::wstring*) noexcept = nullptr;
    bool (*readPrinterDevMode)(
        void*, const std::wstring&, std::vector<std::uint8_t>*) noexcept =
        nullptr;
    bool (*readFontInventory)(
        void*, std::vector<LayoutFontTupleV1>*) noexcept = nullptr;
    bool (*readDpi)(void*, std::uint32_t*, std::uint32_t*) noexcept = nullptr;
    bool (*readSystemLcid)(void*, std::uint32_t*) noexcept = nullptr;
};

bool CollectLayoutEnvironmentV1(
    const LayoutEnvironmentPlatformV1& platform,
    const Sha256& pageSetupDigest,
    std::vector<std::uint8_t>* output) noexcept;
bool CollectProductionLayoutEnvironmentV1(
    const Sha256& pageSetupDigest,
    std::vector<std::uint8_t>* output) noexcept;

bool SerializeLayoutEnvironmentV1(
    const LayoutEnvironmentV1& environment,
    std::vector<std::uint8_t>* output) noexcept;
bool ValidateLayoutEnvironmentV1(
    const std::vector<std::uint8_t>& serialized) noexcept;
bool ReplacePageSetupDigestV1(
    const std::vector<std::uint8_t>& serialized,
    const Sha256& pageSetupDigest,
    std::vector<std::uint8_t>* output) noexcept;

struct LayoutSnapshot final {
    std::vector<std::uint8_t> environment{};
    std::int32_t pageCount = 0;
    std::vector<SectionLayoutObservation> sections{};
    std::vector<LayoutNodeObservation> nodes{};
};

// Qualified native observations before the pinned layout environment and
// page-setup digest are attached. HWPUNIT values remain integral end to end.
struct NativeLayoutObservationV1 final {
    std::int32_t pageCount = 0;
    std::vector<SectionLayoutObservation> sections{};
    std::vector<LayoutNodeObservation> nodes{};
};

bool BuildSectionLayoutObservationsV1(
    const capture::ReaderPayload& referenceClosure,
    std::vector<SectionLayoutObservation>* output) noexcept;

bool BuildNativeLayoutSnapshotV1(
    const NativeLayoutObservationV1& observation,
    const LayoutEnvironmentPlatformV1& platform,
    LayoutSnapshot* output) noexcept;
bool BuildProductionNativeLayoutSnapshotV1(
    const NativeLayoutObservationV1& observation,
    LayoutSnapshot* output) noexcept;

struct StableLayoutRecord final {
    LayoutSnapshot snapshot{};
    std::wstring layoutRoot{};
    std::array<RecalculationObservation, 2> settleAttempts{};
};

using RecalculateCallback = RecalculationObservation (*)(
    void* context) noexcept;
using CaptureCallback = bool (*)(
    void* context,
    LayoutSnapshot* output) noexcept;
using NativeLayoutObservationCallback = bool (*)(
    void* context,
    NativeLayoutObservationV1* output) noexcept;

CaptureStatus ComputeLayoutRoot(
    const LayoutSnapshot& snapshot,
    std::wstring* output) noexcept;

CaptureStatus CaptureStableLayout(
    RecalculateCallback recalculate,
    CaptureCallback capture,
    void* context,
    QualificationMode mode,
    StableLayoutRecord* output) noexcept;

// Stable full-document reader boundary. Both callbacks receive the original
// caller context; each qualified native observation is built before the
// existing two-capture root gate evaluates it.
CaptureStatus CaptureStableNativeLayoutV1(
    RecalculateCallback recalculate,
    NativeLayoutObservationCallback observe,
    void* context,
    QualificationMode mode,
    StableLayoutRecord* output) noexcept;
CaptureStatus CaptureStableNativeLayoutV1(
    RecalculateCallback recalculate,
    NativeLayoutObservationCallback observe,
    void* context,
    QualificationMode mode,
    const LayoutEnvironmentPlatformV1& platform,
    StableLayoutRecord* output) noexcept;

CaptureStatus UnionPageSpans(
    const std::vector<LayoutNodeObservation>& children,
    ScalarObservation* pageStart,
    ScalarObservation* pageEnd) noexcept;

CaptureStatus CaptureCurrentTableLayoutFromNative(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    StableLayoutRecord* output) noexcept;
CaptureStatus CaptureCurrentTableLayoutFromNative(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    const LayoutEnvironmentPlatformV1& platform,
    StableLayoutRecord* output) noexcept;

} // namespace hancom::graph::layout

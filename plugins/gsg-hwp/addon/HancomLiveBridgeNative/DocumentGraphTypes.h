#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <type_traits>

namespace hancom::graph {

using RecordId = std::uint64_t;
using PropertyKeyId = std::uint32_t;
using FieldTag = std::uint16_t;

struct Uuid128 final { std::array<std::uint8_t, 16> bytes{}; };
struct Sha256 final { std::array<std::uint8_t, 32> bytes{}; };
using NodeId = Uuid128;
using ContentId = Uuid128;

// Variable-sized values are immutable references into the graph spool. No
// document text, diagnostic detail, or binary asset is retained resident here.
struct SpoolSlice final {
    ContentId contentId{};
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
    Sha256 digest{};
};

struct Utf16SpoolSlice final {
    SpoolSlice bytes{};
    std::uint64_t codeUnitCount = 0;
};

constexpr bool IsValidUtf16Slice(const Utf16SpoolSlice& value) noexcept {
    return value.codeUnitCount <= (std::numeric_limits<std::uint64_t>::max)() / 2U &&
        value.bytes.length == value.codeUnitCount * 2U;
}

struct RawUtf16CodeUnitView final {
    const std::uint16_t* codeUnits = nullptr;
    std::uint64_t count = 0;
};
constexpr bool IsValidRawUtf16(const RawUtf16CodeUnitView value) noexcept {
    // Paired and unpaired surrogates are preserved as raw UTF-16 code units.
    return value.count == 0 || value.codeUnits != nullptr;
}

struct PositionV1 final {
    std::int64_t list = 0;
    std::int64_t paragraph = 0;
    std::int64_t character = 0;
};
static_assert(sizeof(PositionV1) == 24, "PositionV1 wire shape changed");

struct ParagraphRangeV1 final {
    NodeId paragraphNodeId{};
    std::int64_t begin = 0;
    std::int64_t end = 0;
};
static_assert(sizeof(ParagraphRangeV1) == 32, "ParagraphRangeV1 wire shape changed");

struct SizeV1 final { std::int64_t width = 0; std::int64_t height = 0; };
static_assert(sizeof(SizeV1) == 16, "SizeV1 wire shape changed");

struct CropV1 final {
    std::int64_t left = 0;
    std::int64_t top = 0;
    std::int64_t right = 0;
    std::int64_t bottom = 0;
};
static_assert(sizeof(CropV1) == 32, "CropV1 wire shape changed");

struct BlobRefV1 final {
    ContentId contentId{};
    std::uint64_t byteLength = 0;
    Sha256 digest{};
};
static_assert(sizeof(BlobRefV1) == 56, "BlobRefV1 wire shape changed");

struct HalfOpenRange final { std::uint64_t begin = 0; std::uint64_t end = 0; };
constexpr bool IsValid(const HalfOpenRange value) noexcept { return value.begin <= value.end; }

struct CellCoordinateV1 final {
    std::uint64_t row1 = 1;
    std::uint64_t column1 = 1;
    std::uint64_t rowSpan = 1;
    std::uint64_t columnSpan = 1;
};
constexpr bool IsValid(const CellCoordinateV1 value) noexcept {
    return value.row1 >= 1 && value.column1 >= 1 &&
        value.rowSpan >= 1 && value.columnSpan >= 1;
}

constexpr bool IsValidPublicCoordinate(const std::int64_t value) noexcept {
    return value >= 0;
}

constexpr bool FitsUnsignedPersistedWidth(
    const std::uint64_t value,
    const std::uint8_t widthBytes) noexcept {
    return widthBytes == 8 ||
        (widthBytes == 4 && value <= (std::numeric_limits<std::uint32_t>::max)()) ||
        (widthBytes == 2 && value <= (std::numeric_limits<std::uint16_t>::max)()) ||
        (widthBytes == 1 && value <= (std::numeric_limits<std::uint8_t>::max)());
}

constexpr bool IsFiniteFloat64(const double value) noexcept {
    return value == value && value <= (std::numeric_limits<double>::max)() &&
        value >= -(std::numeric_limits<double>::max)();
}

enum class ObservationState : std::uint8_t {
    Value = 0,
    NotApplicable = 1,
    NotExposed = 2,
    ReadFailed = 3,
    InconsistentNativeState = 4,
    NotRequested = 5,
    ProjectionOmitted = 6,
};

enum class StreamKind : std::uint8_t { Capture = 0, QueryView = 1 };

constexpr bool IsObservationStateLegal(
    const StreamKind stream,
    const ObservationState state) noexcept {
    return state != ObservationState::ProjectionOmitted || stream == StreamKind::QueryView;
}

template <typename T>
struct ObservationV1 final {
    ObservationState state = ObservationState::NotRequested;
    bool valuePresent = false;
    std::int32_t hresult = 0;
    Utf16SpoolSlice detail{};
    T value{};
};

template <typename T>
constexpr bool IsValidObservation(
    const StreamKind stream,
    const ObservationV1<T>& observation) noexcept {
    if (!IsObservationStateLegal(stream, observation.state)) return false;
    if (observation.state == ObservationState::Value) {
        return observation.valuePresent && observation.hresult == 0;
    }
    return !observation.valuePresent;
}

enum class LocatorTag : std::uint16_t {
    None = 0,
    Story = 1,
    Paragraph = 2,
    Run = 3,
    Control = 4,
    Cell = 5,
};

struct StoryLocatorV1 final { std::int64_t list = 0; };
struct ParagraphLocatorV1 final { std::int64_t list = 0; std::int64_t paragraph = 0; };
struct RunLocatorV1 final {
    std::int64_t list = 0;
    std::int64_t paragraph = 0;
    std::int64_t begin = 0;
    std::int64_t end = 0;
};
struct ControlLocatorV1 final {
    Utf16SpoolSlice ctrlId{};
    bool instancePresent = false;
    Utf16SpoolSlice instanceId{};
    std::uint64_t headCtrlOrdinal = 0;
    PositionV1 anchor{};
};
struct CellLocatorV1 final {
    NodeId tableNodeId{};
    Utf16SpoolSlice address{};
    std::int64_t list = 0;
};

// A locator is a tagged spool-backed value, not a COM identity or pointer.
struct NativeLocatorV1 final {
    LocatorTag tag = LocatorTag::None;
    StoryLocatorV1 story{};
    ParagraphLocatorV1 paragraph{};
    RunLocatorV1 run{};
    ControlLocatorV1 control{};
    CellLocatorV1 cell{};
};

struct NativeNodeCommonV1 final {
    NodeId nodeId{};
    std::uint16_t nodeKind = 0;
    bool parentPresent = false;
    NodeId parentNodeId{};
    std::uint64_t siblingOrdinal = 0;
    std::uint64_t createdSemanticRevision = 0;
    std::uint64_t changedSemanticRevision = 0;
    ObservationV1<NativeLocatorV1> nativeLocator{};
    Sha256 semanticFingerprint{};
    SpoolSlice layoutPropertyRecordIds{};
    SpoolSlice binaryPropertyRecordIds{};
};

static_assert(std::is_standard_layout_v<Uuid128>);
static_assert(std::is_standard_layout_v<SpoolSlice>);
static_assert(std::is_standard_layout_v<NativeNodeCommonV1>);
static_assert(sizeof(std::uint64_t) == 8, "persisted sizes require uint64_t");

} // namespace hancom::graph

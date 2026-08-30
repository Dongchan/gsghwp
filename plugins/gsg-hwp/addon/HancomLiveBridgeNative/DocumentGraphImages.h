#pragma once

#include <Windows.h>
#include <oaidl.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

namespace hancom::graph::images {

enum class ObservationState : std::uint8_t {
    Value = 0,
    NotApplicable,
    NotExposed,
    ReadFailed,
};

enum class CaptureStatus : std::uint8_t {
    Complete = 0,
    InvalidArgument,
    SourceFailed,
    DigestFailed,
    ResourceExhausted,
};

enum class ImageControlKind : std::uint8_t {
    Picture = 0,
    ImageShape,
    GenericShape,
};

enum class AssetStorage : std::uint8_t {
    Linked = 0,
    Embedded,
    NotExposed,
    NotApplicable,
};

enum class ImageScalarField : std::uint8_t {
    DisplayedWidth = 0,
    DisplayedHeight,
    OriginalWidth,
    OriginalHeight,
    CropLeft,
    CropRight,
    CropTop,
    CropBottom,
    TreatAsChar,
    AffectsLine,
    VerticalRelation,
    VerticalAlign,
    VerticalOffset,
    HorizontalRelation,
    HorizontalAlign,
    HorizontalOffset,
    FlowWithText,
    AllowOverlap,
    WidthRelation,
    HeightRelation,
    TextWrap,
    TextFlow,
    OutsideMarginLeft,
    OutsideMarginRight,
    OutsideMarginTop,
    OutsideMarginBottom,
    InsideMarginLeft,
    InsideMarginRight,
    InsideMarginTop,
    InsideMarginBottom,
    RotationAngle,
    Reverse,
    PictureEffect,
    Brightness,
    Contrast,
    ImageAlpha,
    LineType,
    LineWidth,
    LineColor,
    LineAlpha,
    FillType,
    FillColor,
    FillAlpha,
    ZOrder,
    FlipHorizontal,
    FlipVertical,
    CaptionSide,
    CaptionWidth,
    CaptionGap,
    CaptionFullSize,
    Count,
};

enum class ImageTextField : std::uint8_t {
    SourceName = 0,
    Description,
    CaptionText,
    Count,
};

struct ScalarObservation final {
    ObservationState state = ObservationState::NotExposed;
    std::int64_t value = 0;
};

struct TextObservation final {
    ObservationState state = ObservationState::NotExposed;
    std::wstring value{};
};

struct AssetDigest final {
    ObservationState state = ObservationState::NotExposed;
    std::uint64_t byteLength = 0;
    std::wstring sha256{};
};

struct ImageAssetRecord final {
    ObservationState storageState = ObservationState::NotExposed;
    AssetStorage storage = AssetStorage::NotExposed;
    TextObservation sourceName{};
    ObservationState binaryContentState = ObservationState::NotExposed;
    std::uint64_t byteLength = 0;
    std::wstring sha256{};
};

inline constexpr size_t kImageScalarFieldCount =
    static_cast<size_t>(ImageScalarField::Count);
inline constexpr size_t kImageTextFieldCount =
    static_cast<size_t>(ImageTextField::Count);

struct ImageNativeObservation final {
    std::wstring sessionInstanceId{};
    std::wstring ctrlId{};
    bool imageAttributesExposed = false;
    std::array<ScalarObservation, kImageScalarFieldCount> scalar{};
    std::array<TextObservation, kImageTextFieldCount> text{};
    ScalarObservation embedded{};
    AssetDigest assetDigest{};
};

struct ImageGraphRecord final {
    std::wstring sessionInstanceId{};
    std::wstring ctrlId{};
    ImageControlKind kind = ImageControlKind::GenericShape;
    std::array<ScalarObservation, kImageScalarFieldCount> scalar{};
    std::array<TextObservation, kImageTextFieldCount> text{};
    ImageAssetRecord asset{};
};

using ChunkReader = bool (*)(
    void* context,
    std::uint8_t* buffer,
    size_t capacity,
    size_t* count,
    bool* done) noexcept;

CaptureStatus DigestAssetStream(
    ChunkReader reader,
    void* context,
    size_t chunkSize,
    AssetDigest* output) noexcept;

bool ImageSourceNameNeedsFallback(
    const TextObservation& sourceName) noexcept;

CaptureStatus BuildImageGraphRecord(
    const ImageNativeObservation& source,
    ImageGraphRecord* output) noexcept;

CaptureStatus CaptureImageGraphFromNative(
    IDispatch* control,
    const std::wstring& sessionInstanceId,
    ImageGraphRecord* output) noexcept;

} // namespace hancom::graph::images

#include "DocumentGraphImages.h"

#include "DispatchInvoke.h"

#include <atlbase.h>
#include <bcrypt.h>

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <new>
#include <sstream>
#include <utility>
#include <vector>

namespace hancom::graph::images {
namespace {

using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

struct ScalarBinding final {
    ImageScalarField field;
    const wchar_t* name;
};

struct FileSource final {
    std::ifstream stream;
};

constexpr size_t Index(const ImageScalarField field) noexcept {
    return static_cast<size_t>(field);
}

constexpr size_t Index(const ImageTextField field) noexcept {
    return static_cast<size_t>(field);
}

bool ReadFileChunk(
    void* const context,
    std::uint8_t* const buffer,
    const size_t capacity,
    size_t* const count,
    bool* const done) noexcept {
    auto* const source = static_cast<FileSource*>(context);
    if (source == nullptr || buffer == nullptr || count == nullptr ||
        done == nullptr || capacity == 0) {
        return false;
    }
    source->stream.read(
        reinterpret_cast<char*>(buffer),
        static_cast<std::streamsize>(capacity));
    const std::streamsize read = source->stream.gcount();
    if (read < 0 || source->stream.bad()) {
        return false;
    }
    *count = static_cast<size_t>(read);
    *done = source->stream.eof();
    return *count != 0 || *done;
}

ObservationState ReadItem(
    IDispatch* const set,
    const wchar_t* const name,
    CComVariant* const value) noexcept {
    if (set == nullptr || name == nullptr || value == nullptr) {
        return ObservationState::ReadFailed;
    }
    return SUCCEEDED(Method(
        set,
        L"Item",
        {CComVariant(name)},
        value))
        ? ObservationState::Value
        : ObservationState::NotExposed;
}

ScalarObservation ReadScalar(
    IDispatch* const set,
    const wchar_t* const name) noexcept {
    CComVariant value;
    const ObservationState state = ReadItem(set, name, &value);
    if (state != ObservationState::Value) {
        return {state, 0};
    }
    LONG converted = 0;
    return SUCCEEDED(AsLong(value, &converted))
        ? ScalarObservation{ObservationState::Value, converted}
        : ScalarObservation{ObservationState::ReadFailed, 0};
}

TextObservation ReadText(
    IDispatch* const set,
    const wchar_t* const name) noexcept {
    CComVariant value;
    const ObservationState state = ReadItem(set, name, &value);
    if (state != ObservationState::Value) {
        return {state, {}};
    }
    std::wstring converted;
    return SUCCEEDED(AsString(value, &converted))
        ? TextObservation{ObservationState::Value, std::move(converted)}
        : TextObservation{ObservationState::ReadFailed, {}};
}

bool ReadNested(
    IDispatch* const set,
    const wchar_t* const name,
    CComPtr<IDispatch>& nested) noexcept {
    CComVariant value;
    return ReadItem(set, name, &value) == ObservationState::Value &&
        SUCCEEDED(AsDispatch(value, nested));
}

void ReadBindings(
    IDispatch* const set,
    const ScalarBinding* const bindings,
    const size_t count,
    ImageNativeObservation* const output) noexcept {
    if (set == nullptr || bindings == nullptr || output == nullptr) {
        return;
    }
    for (size_t index = 0; index < count; ++index) {
        output->scalar[Index(bindings[index].field)] =
            ReadScalar(set, bindings[index].name);
    }
}

CaptureStatus DigestLinkedFile(
    const std::wstring& path,
    AssetDigest* const output) noexcept {
    try {
        FileSource source{
            std::ifstream(std::filesystem::path(path), std::ios::binary),
        };
        if (!source.stream) {
            output->state = ObservationState::ReadFailed;
            return CaptureStatus::SourceFailed;
        }
        return DigestAssetStream(
            ReadFileChunk,
            &source,
            1024U * 1024U,
            output);
    } catch (const std::bad_alloc&) {
        return CaptureStatus::ResourceExhausted;
    } catch (...) {
        output->state = ObservationState::ReadFailed;
        return CaptureStatus::SourceFailed;
    }
}

void MarkGenericShapeFields(
    ImageGraphRecord* const record) noexcept {
    const ImageScalarField imageOnly[] = {
        ImageScalarField::OriginalWidth,
        ImageScalarField::OriginalHeight,
        ImageScalarField::CropLeft,
        ImageScalarField::CropRight,
        ImageScalarField::CropTop,
        ImageScalarField::CropBottom,
        ImageScalarField::InsideMarginLeft,
        ImageScalarField::InsideMarginRight,
        ImageScalarField::InsideMarginTop,
        ImageScalarField::InsideMarginBottom,
        ImageScalarField::Reverse,
        ImageScalarField::PictureEffect,
        ImageScalarField::Brightness,
        ImageScalarField::Contrast,
        ImageScalarField::ImageAlpha,
        ImageScalarField::FlipHorizontal,
        ImageScalarField::FlipVertical,
    };
    for (const ImageScalarField field : imageOnly) {
        record->scalar[Index(field)] =
            {ObservationState::NotApplicable, 0};
    }
}

} // namespace

CaptureStatus DigestAssetStream(
    const ChunkReader reader,
    void* const context,
    const size_t chunkSize,
    AssetDigest* const output) noexcept {
    if (reader == nullptr || context == nullptr || chunkSize == 0 ||
        chunkSize > (std::numeric_limits<ULONG>::max)() ||
        output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    *output = {};
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    try {
        if (BCryptOpenAlgorithmProvider(
                &algorithm,
                BCRYPT_SHA256_ALGORITHM,
                nullptr,
                0) < 0) {
            output->state = ObservationState::ReadFailed;
            return CaptureStatus::DigestFailed;
        }
        DWORD objectSize = 0;
        DWORD transferred = 0;
        if (BCryptGetProperty(
                algorithm,
                BCRYPT_OBJECT_LENGTH,
                reinterpret_cast<PUCHAR>(&objectSize),
                sizeof(objectSize),
                &transferred,
                0) < 0) {
            output->state = ObservationState::ReadFailed;
            BCryptCloseAlgorithmProvider(algorithm, 0);
            return CaptureStatus::DigestFailed;
        }
        std::vector<std::uint8_t> object(objectSize);
        std::vector<std::uint8_t> chunk(chunkSize);
        std::array<std::uint8_t, 32> digest{};
        if (BCryptCreateHash(
                algorithm,
                &hash,
                object.data(),
                objectSize,
                nullptr,
                0,
                0) < 0) {
            output->state = ObservationState::ReadFailed;
            BCryptCloseAlgorithmProvider(algorithm, 0);
            return CaptureStatus::DigestFailed;
        }
        std::uint64_t total = 0;
        bool done = false;
        while (!done) {
            size_t count = 0;
            if (!reader(
                    context,
                    chunk.data(),
                    chunk.size(),
                    &count,
                    &done) ||
                count > chunk.size() || (count == 0 && !done)) {
                output->state = ObservationState::ReadFailed;
                BCryptDestroyHash(hash);
                BCryptCloseAlgorithmProvider(algorithm, 0);
                return CaptureStatus::SourceFailed;
            }
            if (total >
                (std::numeric_limits<std::uint64_t>::max)() - count) {
                output->state = ObservationState::ReadFailed;
                BCryptDestroyHash(hash);
                BCryptCloseAlgorithmProvider(algorithm, 0);
                return CaptureStatus::SourceFailed;
            }
            if (count != 0 && BCryptHashData(
                    hash,
                    chunk.data(),
                    static_cast<ULONG>(count),
                    0) < 0) {
                output->state = ObservationState::ReadFailed;
                BCryptDestroyHash(hash);
                BCryptCloseAlgorithmProvider(algorithm, 0);
                return CaptureStatus::DigestFailed;
            }
            total += count;
        }
        if (BCryptFinishHash(
                hash,
                digest.data(),
                static_cast<ULONG>(digest.size()),
                0) < 0) {
            output->state = ObservationState::ReadFailed;
            BCryptDestroyHash(hash);
            BCryptCloseAlgorithmProvider(algorithm, 0);
            return CaptureStatus::DigestFailed;
        }
        BCryptDestroyHash(hash);
        BCryptCloseAlgorithmProvider(algorithm, 0);
        std::wostringstream encoded;
        encoded << std::hex << std::setfill(L'0');
        for (const std::uint8_t value : digest) {
            encoded << std::setw(2) << static_cast<unsigned>(value);
        }
        output->state = ObservationState::Value;
        output->byteLength = total;
        output->sha256 = encoded.str();
        return CaptureStatus::Complete;
    } catch (const std::bad_alloc&) {
        if (hash != nullptr) {
            BCryptDestroyHash(hash);
        }
        if (algorithm != nullptr) {
            BCryptCloseAlgorithmProvider(algorithm, 0);
        }
        return CaptureStatus::ResourceExhausted;
    } catch (...) {
        if (hash != nullptr) {
            BCryptDestroyHash(hash);
        }
        if (algorithm != nullptr) {
            BCryptCloseAlgorithmProvider(algorithm, 0);
        }
        output->state = ObservationState::ReadFailed;
        return CaptureStatus::DigestFailed;
    }
}

bool ImageSourceNameNeedsFallback(
    const TextObservation& sourceName) noexcept {
    return sourceName.state != ObservationState::Value ||
        sourceName.value.empty();
}

CaptureStatus BuildImageGraphRecord(
    const ImageNativeObservation& source,
    ImageGraphRecord* const output) noexcept {
    if (output == nullptr ||
        (source.ctrlId != L"$pic" && source.ctrlId != L"gso")) {
        return CaptureStatus::InvalidArgument;
    }
    try {
        ImageGraphRecord record;
        record.sessionInstanceId = source.sessionInstanceId;
        record.ctrlId = source.ctrlId;
        record.kind = source.ctrlId == L"$pic"
            ? ImageControlKind::Picture
            : source.imageAttributesExposed
            ? ImageControlKind::ImageShape
            : ImageControlKind::GenericShape;
        record.scalar = source.scalar;
        record.text = source.text;
        record.asset.sourceName =
            source.text[Index(ImageTextField::SourceName)];
        if (record.kind == ImageControlKind::GenericShape) {
            MarkGenericShapeFields(&record);
            record.asset.storageState = ObservationState::NotApplicable;
            record.asset.storage = AssetStorage::NotApplicable;
            record.asset.binaryContentState =
                ObservationState::NotApplicable;
        } else if (source.embedded.state != ObservationState::Value) {
            record.asset.storageState = source.embedded.state;
            record.asset.storage = AssetStorage::NotExposed;
            record.asset.binaryContentState =
                ObservationState::NotExposed;
        } else if (source.embedded.value != 0) {
            record.asset.storageState = ObservationState::Value;
            record.asset.storage = AssetStorage::Embedded;
            record.asset.binaryContentState =
                ObservationState::NotExposed;
        } else {
            record.asset.storageState = ObservationState::Value;
            record.asset.storage = AssetStorage::Linked;
            record.asset.binaryContentState = source.assetDigest.state;
            if (source.assetDigest.state == ObservationState::Value) {
                record.asset.byteLength = source.assetDigest.byteLength;
                record.asset.sha256 = source.assetDigest.sha256;
            }
        }
        *output = std::move(record);
        return CaptureStatus::Complete;
    } catch (const std::bad_alloc&) {
        return CaptureStatus::ResourceExhausted;
    } catch (...) {
        return CaptureStatus::SourceFailed;
    }
}

CaptureStatus CaptureImageGraphFromNative(
    IDispatch* const control,
    const std::wstring& sessionInstanceId,
    ImageGraphRecord* const output) noexcept {
    if (control == nullptr || output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    try {
        CComVariant rawCtrlId;
        std::wstring ctrlId;
        if (FAILED(PropertyGet(control, L"CtrlID", &rawCtrlId)) ||
            FAILED(AsString(rawCtrlId, &ctrlId)) ||
            (ctrlId != L"$pic" && ctrlId != L"gso")) {
            return CaptureStatus::InvalidArgument;
        }
        ImageNativeObservation observation;
        observation.sessionInstanceId = sessionInstanceId;
        observation.ctrlId = ctrlId;
        CComVariant rawProperties;
        CComPtr<IDispatch> properties;
        if (FAILED(PropertyGet(control, L"Properties", &rawProperties)) ||
            FAILED(AsDispatch(rawProperties, properties))) {
            return ctrlId == L"$pic"
                ? BuildImageGraphRecord(observation, output)
                : CaptureStatus::SourceFailed;
        }
        static constexpr ScalarBinding common[] = {
            {ImageScalarField::DisplayedWidth, L"Width"},
            {ImageScalarField::DisplayedHeight, L"Height"},
            {ImageScalarField::TreatAsChar, L"TreatAsChar"},
            {ImageScalarField::AffectsLine, L"AffectsLine"},
            {ImageScalarField::VerticalRelation, L"VertRelTo"},
            {ImageScalarField::VerticalAlign, L"VertAlign"},
            {ImageScalarField::VerticalOffset, L"VertOffset"},
            {ImageScalarField::HorizontalRelation, L"HorzRelTo"},
            {ImageScalarField::HorizontalAlign, L"HorzAlign"},
            {ImageScalarField::HorizontalOffset, L"HorzOffset"},
            {ImageScalarField::FlowWithText, L"FlowWithText"},
            {ImageScalarField::AllowOverlap, L"AllowOverlap"},
            {ImageScalarField::WidthRelation, L"WidthRelTo"},
            {ImageScalarField::HeightRelation, L"HeightRelTo"},
            {ImageScalarField::TextWrap, L"TextWrap"},
            {ImageScalarField::TextFlow, L"TextFlow"},
            {ImageScalarField::OutsideMarginLeft, L"OutsideMarginLeft"},
            {ImageScalarField::OutsideMarginRight, L"OutsideMarginRight"},
            {ImageScalarField::OutsideMarginTop, L"OutsideMarginTop"},
            {ImageScalarField::OutsideMarginBottom, L"OutsideMarginBottom"},
        };
        ReadBindings(
            properties,
            common,
            std::size(common),
            &observation);
        CComPtr<IDispatch> image;
        observation.imageAttributesExposed =
            ReadNested(properties, L"ShapeDrawImageAttr", image);
        if (image != nullptr) {
            static constexpr ScalarBinding imageFields[] = {
                {ImageScalarField::OriginalWidth, L"OriginalSizeX"},
                {ImageScalarField::OriginalHeight, L"OriginalSizeY"},
                {ImageScalarField::CropLeft, L"SkipLeft"},
                {ImageScalarField::CropRight, L"SkipRight"},
                {ImageScalarField::CropTop, L"SkipTop"},
                {ImageScalarField::CropBottom, L"SkipBottom"},
                {ImageScalarField::InsideMarginLeft, L"InsideMarginLeft"},
                {ImageScalarField::InsideMarginRight, L"InsideMarginRight"},
                {ImageScalarField::InsideMarginTop, L"InsideMarginTop"},
                {ImageScalarField::InsideMarginBottom, L"InsideMarginBottom"},
                {ImageScalarField::Reverse, L"Reverse"},
                {ImageScalarField::PictureEffect, L"PicEffect"},
                {ImageScalarField::Brightness, L"Brightness"},
                {ImageScalarField::Contrast, L"Contrast"},
                {ImageScalarField::ImageAlpha, L"ImageAlphaEffect"},
            };
            ReadBindings(
                image,
                imageFields,
                std::size(imageFields),
                &observation);
            observation.embedded = ReadScalar(image, L"Embedded");
            observation.text[Index(ImageTextField::SourceName)] =
                ReadText(image, L"FileNameStr");
            if (ImageSourceNameNeedsFallback(
                    observation.text[Index(ImageTextField::SourceName)])) {
                observation.text[Index(ImageTextField::SourceName)] =
                    ReadText(image, L"FileName");
            }
        }
        CComPtr<IDispatch> rotate;
        if (ReadNested(properties, L"ShapeDrawRotate", rotate)) {
            observation.scalar[Index(ImageScalarField::RotationAngle)] =
                ReadScalar(rotate, L"Angle");
        }
        CComPtr<IDispatch> line;
        if (ReadNested(properties, L"ShapeDrawLineAttr", line)) {
            static constexpr ScalarBinding lineFields[] = {
                {ImageScalarField::LineType, L"Style"},
                {ImageScalarField::LineWidth, L"Width"},
                {ImageScalarField::LineColor, L"Color"},
                {ImageScalarField::LineAlpha, L"Alpha"},
            };
            ReadBindings(
                line,
                lineFields,
                std::size(lineFields),
                &observation);
        }
        CComPtr<IDispatch> fill;
        if (ReadNested(properties, L"ShapeDrawFillAttr", fill)) {
            static constexpr ScalarBinding fillFields[] = {
                {ImageScalarField::FillType, L"Type"},
                {ImageScalarField::FillColor, L"WinBrushFaceColor"},
                {ImageScalarField::FillAlpha, L"WinBrushAlpha"},
            };
            ReadBindings(
                fill,
                fillFields,
                std::size(fillFields),
                &observation);
        }
        CComPtr<IDispatch> caption;
        if (ReadNested(properties, L"ShapeCaption", caption)) {
            static constexpr ScalarBinding captionFields[] = {
                {ImageScalarField::CaptionSide, L"Side"},
                {ImageScalarField::CaptionWidth, L"Width"},
                {ImageScalarField::CaptionGap, L"Gap"},
                {ImageScalarField::CaptionFullSize, L"CapFullSize"},
            };
            ReadBindings(
                caption,
                captionFields,
                std::size(captionFields),
                &observation);
            observation.text[Index(ImageTextField::CaptionText)] =
                ReadText(caption, L"Text");
        }
        const TextObservation& source =
            observation.text[Index(ImageTextField::SourceName)];
        if (observation.embedded.state == ObservationState::Value &&
            observation.embedded.value == 0 &&
            source.state == ObservationState::Value &&
            !source.value.empty()) {
            static_cast<void>(DigestLinkedFile(
                source.value,
                &observation.assetDigest));
        }
        return BuildImageGraphRecord(observation, output);
    } catch (const std::bad_alloc&) {
        return CaptureStatus::ResourceExhausted;
    } catch (...) {
        return CaptureStatus::SourceFailed;
    }
}

} // namespace hancom::graph::images

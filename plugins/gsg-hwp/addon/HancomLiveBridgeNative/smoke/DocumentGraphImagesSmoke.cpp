#include "../DocumentGraphImages.h"

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <string>

namespace {

using hancom::graph::images::AssetDigest;
using hancom::graph::images::AssetStorage;
using hancom::graph::images::BuildImageGraphRecord;
using hancom::graph::images::CaptureStatus;
using hancom::graph::images::DigestAssetStream;
using hancom::graph::images::ImageControlKind;
using hancom::graph::images::ImageGraphRecord;
using hancom::graph::images::ImageNativeObservation;
using hancom::graph::images::ImageScalarField;
using hancom::graph::images::ImageSourceNameNeedsFallback;
using hancom::graph::images::ImageTextField;
using hancom::graph::images::ObservationState;
using hancom::graph::images::ScalarObservation;
using hancom::graph::images::TextObservation;

struct PatternSource final {
    std::uint64_t total = 0;
    std::uint64_t offset = 0;
    bool fail = false;
};

bool ReadPattern(
    void* const context,
    std::uint8_t* const buffer,
    const size_t capacity,
    size_t* const count,
    bool* const done) noexcept {
    auto* const source = static_cast<PatternSource*>(context);
    if (source == nullptr || buffer == nullptr || count == nullptr ||
        done == nullptr || source->fail) {
        return false;
    }
    const std::uint64_t remaining = source->total - source->offset;
    const size_t current = static_cast<size_t>(
        (std::min)(remaining, static_cast<std::uint64_t>(capacity)));
    for (size_t index = 0; index < current; ++index) {
        buffer[index] = static_cast<std::uint8_t>(
            (source->offset + index) * 31U + 7U);
    }
    source->offset += current;
    *count = current;
    *done = source->offset == source->total;
    return true;
}

ScalarObservation Scalar(const std::int64_t value) {
    return {ObservationState::Value, value};
}

TextObservation Text(std::wstring value) {
    return {ObservationState::Value, std::move(value)};
}

ImageNativeObservation BaseImage(std::wstring ctrlId) {
    ImageNativeObservation image;
    image.sessionInstanceId = L"session-image";
    image.ctrlId = std::move(ctrlId);
    image.imageAttributesExposed = true;
    image.embedded = Scalar(0);
    image.text[static_cast<size_t>(ImageTextField::SourceName)] =
        Text(L"C:\\fixture\\source.png");
    image.assetDigest.state = ObservationState::Value;
    image.assetDigest.byteLength = 4096;
    image.assetDigest.sha256 = L"abcd";
    return image;
}

bool ClassifiesNativeTypesWithoutDescriptionsSmoke() {
    ImageGraphRecord picture;
    ImageGraphRecord imageShape;
    ImageGraphRecord genericShape;
    ImageNativeObservation pic = BaseImage(L"$pic");
    ImageNativeObservation gso = BaseImage(L"gso");
    ImageNativeObservation generic = BaseImage(L"gso");
    generic.imageAttributesExposed = false;
    pic.text[static_cast<size_t>(ImageTextField::Description)] =
        Text(L"same");
    gso.text[static_cast<size_t>(ImageTextField::Description)] =
        Text(L"same");
    return BuildImageGraphRecord(pic, &picture) == CaptureStatus::Complete &&
        BuildImageGraphRecord(gso, &imageShape) == CaptureStatus::Complete &&
        BuildImageGraphRecord(generic, &genericShape) ==
            CaptureStatus::Complete &&
        picture.kind == ImageControlKind::Picture &&
        imageShape.kind == ImageControlKind::ImageShape &&
        genericShape.kind == ImageControlKind::GenericShape;
}

bool PreservesEveryObservedFieldSmoke() {
    ImageNativeObservation source = BaseImage(L"$pic");
    for (size_t index = 0; index < source.scalar.size(); ++index) {
        source.scalar[index] = Scalar(static_cast<std::int64_t>(index + 1));
    }
    source.text[static_cast<size_t>(ImageTextField::CaptionText)] =
        {ObservationState::NotExposed, {}};
    ImageGraphRecord graph;
    return BuildImageGraphRecord(source, &graph) == CaptureStatus::Complete &&
        graph.scalar[static_cast<size_t>(ImageScalarField::DisplayedWidth)]
                .value == 1 &&
        graph.scalar[static_cast<size_t>(ImageScalarField::CaptionFullSize)]
                .state == ObservationState::Value &&
        graph.text[static_cast<size_t>(ImageTextField::CaptionText)].state ==
            ObservationState::NotExposed;
}

bool DistinguishesAssetAvailabilitySmoke() {
    ImageNativeObservation linked = BaseImage(L"$pic");
    ImageNativeObservation embedded = BaseImage(L"$pic");
    ImageNativeObservation unavailable = BaseImage(L"$pic");
    embedded.embedded = Scalar(1);
    unavailable.embedded = {ObservationState::NotExposed, 0};
    unavailable.assetDigest = {};
    ImageGraphRecord linkedGraph;
    ImageGraphRecord embeddedGraph;
    ImageGraphRecord unavailableGraph;
    return BuildImageGraphRecord(linked, &linkedGraph) ==
            CaptureStatus::Complete &&
        BuildImageGraphRecord(embedded, &embeddedGraph) ==
            CaptureStatus::Complete &&
        BuildImageGraphRecord(unavailable, &unavailableGraph) ==
            CaptureStatus::Complete &&
        linkedGraph.asset.storage == AssetStorage::Linked &&
        linkedGraph.asset.binaryContentState == ObservationState::Value &&
        embeddedGraph.asset.storage == AssetStorage::Embedded &&
        embeddedGraph.asset.binaryContentState ==
            ObservationState::NotExposed &&
        unavailableGraph.asset.storageState ==
            ObservationState::NotExposed &&
        unavailableGraph.asset.binaryContentState ==
            ObservationState::NotExposed;
}

bool HugeDigestIsChunkIndependentSmoke() {
    constexpr std::uint64_t total = 20ULL * 1024ULL * 1024ULL + 13ULL;
    PatternSource first{total, 0, false};
    PatternSource second{total, 0, false};
    AssetDigest firstDigest;
    AssetDigest secondDigest;
    return DigestAssetStream(
               ReadPattern,
               &first,
               17,
               &firstDigest) == CaptureStatus::Complete &&
        DigestAssetStream(
               ReadPattern,
               &second,
               1024 * 1024,
               &secondDigest) == CaptureStatus::Complete &&
        firstDigest.state == ObservationState::Value &&
        firstDigest.byteLength == total &&
        firstDigest.sha256.size() == 64 &&
        firstDigest.sha256 == secondDigest.sha256;
}

bool DigestFailureDoesNotBecomeEmptySmoke() {
    PatternSource source{100, 0, true};
    AssetDigest digest;
    return DigestAssetStream(
               ReadPattern,
               &source,
               64,
               &digest) == CaptureStatus::SourceFailed &&
        digest.state == ObservationState::ReadFailed &&
        digest.sha256.empty();
}

bool EmptySourceNameFallsBackSmoke() {
    return ImageSourceNameNeedsFallback(Text(L"")) &&
        ImageSourceNameNeedsFallback(
            {ObservationState::NotExposed, {}}) &&
        !ImageSourceNameNeedsFallback(Text(L"C:\\fixture\\source.png"));
}

} // namespace

bool DocumentGraphImagesSmoke() {
    const bool types = ClassifiesNativeTypesWithoutDescriptionsSmoke();
    const bool fields = PreservesEveryObservedFieldSmoke();
    const bool assets = DistinguishesAssetAvailabilitySmoke();
    const bool digest = HugeDigestIsChunkIndependentSmoke();
    const bool failure = DigestFailureDoesNotBecomeEmptySmoke();
    const bool sourceNameFallback = EmptySourceNameFallsBackSmoke();
    std::wcout << L"IMAGE_GRAPH_NATIVE_TYPES " << types << L'\n'
               << L"IMAGE_GRAPH_ALL_FIELDS " << fields << L'\n'
               << L"IMAGE_GRAPH_ASSET_AVAILABILITY " << assets << L'\n'
               << L"IMAGE_GRAPH_HUGE_DIGEST " << digest << L'\n'
               << L"IMAGE_GRAPH_DIGEST_FAILURE " << failure << L'\n'
               << L"IMAGE_GRAPH_SOURCE_NAME_FALLBACK "
               << sourceNameFallback << L'\n';
    return types && fields && assets && digest && failure &&
        sourceNameFallback;
}

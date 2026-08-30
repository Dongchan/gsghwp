#pragma once

#include "DocumentGraphStories.h"

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace hancom::graph::controls {

enum class ControlKind : std::uint8_t {
    SectionDefinition = 0,
    ColumnDefinition,
    Header,
    Footer,
    Footnote,
    Endnote,
    Table,
    GeneralShape,
    AutoNumber,
    NewNumber,
    PageNumberPosition,
    Unknown,
};

struct AdaptedControlRecord final {
    ControlKind kind = ControlKind::Unknown;
    std::wstring nativeTypeId{};
    std::wstring sessionInstanceId{};
    LONG ctrlCh = 0;
    bool hasList = false;
    hancom::graph::stories::NativeAnchor anchor{};
    std::uint64_t headCtrlOrdinal = 0;
    bool instanceIdSessionOnly = true;
    bool instanceIdPresent = false;
    bool unknownTypeDiagnostic = false;
};

ControlKind ClassifyNativeControl(std::wstring_view nativeTypeId) noexcept;

bool AdaptNativeControl(
    const hancom::graph::stories::NativeControlRecord& source,
    AdaptedControlRecord* target) noexcept;

bool CanonicalAnchorLess(
    const AdaptedControlRecord& left,
    const AdaptedControlRecord& right) noexcept;

struct NativeAnchorOrderRecord final {
    std::int64_t list = 0;
    std::int64_t paragraph = 0;
    std::int64_t character = 0;
    std::uint64_t headCtrlOrdinal = 0;
    size_t sourceIndex = 0;
};

bool OrderByAuthoritativeNativeAnchor(
    std::vector<NativeAnchorOrderRecord>* records) noexcept;

bool OrderByAuthoritativeNativeAnchor(
    std::vector<AdaptedControlRecord>* records) noexcept;

} // namespace hancom::graph::controls

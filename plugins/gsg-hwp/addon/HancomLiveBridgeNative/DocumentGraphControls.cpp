#include "DocumentGraphControls.h"

#include <algorithm>
#include <array>
#include <new>
#include <tuple>
#include <utility>

namespace hancom::graph::controls {
namespace {

struct ControlTypeRule final {
    std::wstring_view nativeTypeId;
    ControlKind kind;
};

constexpr std::array<ControlTypeRule, 11> kControlTypes{{
    {L"secd", ControlKind::SectionDefinition},
    {L"cold", ControlKind::ColumnDefinition},
    {L"head", ControlKind::Header},
    {L"foot", ControlKind::Footer},
    {L"fn", ControlKind::Footnote},
    {L"en", ControlKind::Endnote},
    {L"tbl", ControlKind::Table},
    {L"gso", ControlKind::GeneralShape},
    {L"atno", ControlKind::AutoNumber},
    {L"nwno", ControlKind::NewNumber},
    {L"pgnp", ControlKind::PageNumberPosition},
}};

} // namespace

ControlKind ClassifyNativeControl(
    const std::wstring_view nativeTypeId) noexcept {
    for (const ControlTypeRule& rule : kControlTypes) {
        if (rule.nativeTypeId == nativeTypeId) {
            return rule.kind;
        }
    }
    return ControlKind::Unknown;
}

bool AdaptNativeControl(
    const hancom::graph::stories::NativeControlRecord& source,
    AdaptedControlRecord* const target) noexcept {
    if (target == nullptr) {
        return false;
    }
    try {
        AdaptedControlRecord adapted;
        adapted.kind = ClassifyNativeControl(source.ctrlId);
        adapted.nativeTypeId = source.ctrlId;
        adapted.sessionInstanceId = source.instanceId;
        adapted.ctrlCh = source.ctrlCh;
        adapted.hasList = source.hasList;
        adapted.anchor = source.anchor;
        adapted.headCtrlOrdinal = source.headCtrlOrdinal;
        adapted.instanceIdSessionOnly = source.instanceIdSessionOnly;
        adapted.instanceIdPresent = source.instanceIdPresent;
        adapted.unknownTypeDiagnostic =
            adapted.kind == ControlKind::Unknown;
        *target = std::move(adapted);
        return true;
    } catch (const std::bad_alloc&) {
        return false;
    } catch (...) {
        return false;
    }
}

bool CanonicalAnchorLess(
    const AdaptedControlRecord& left,
    const AdaptedControlRecord& right) noexcept {
    return std::tie(
               left.anchor.list,
               left.anchor.paragraph,
               left.anchor.character,
               left.headCtrlOrdinal) <
        std::tie(
               right.anchor.list,
               right.anchor.paragraph,
               right.anchor.character,
               right.headCtrlOrdinal);
}

bool OrderByAuthoritativeNativeAnchor(
    std::vector<NativeAnchorOrderRecord>* const records) noexcept {
    if (records == nullptr) return false;
    try {
        std::stable_sort(
            records->begin(), records->end(),
            [](const NativeAnchorOrderRecord& left,
               const NativeAnchorOrderRecord& right) {
                return std::tie(left.list, left.paragraph, left.character,
                                left.headCtrlOrdinal) <
                    std::tie(right.list, right.paragraph, right.character,
                             right.headCtrlOrdinal);
            });
        return true;
    } catch (...) {
        return false;
    }
}

bool OrderByAuthoritativeNativeAnchor(
    std::vector<AdaptedControlRecord>* const records) noexcept {
    if (records == nullptr) return false;
    try {
        std::stable_sort(records->begin(), records->end(), CanonicalAnchorLess);
        return true;
    } catch (...) {
        return false;
    }
}

} // namespace hancom::graph::controls

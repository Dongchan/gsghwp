#include "../DocumentGraphControls.h"

#include <algorithm>
#include <array>
#include <iostream>
#include <utility>
#include <vector>

namespace {

using hancom::graph::controls::AdaptNativeControl;
using hancom::graph::controls::AdaptedControlRecord;
using hancom::graph::controls::CanonicalAnchorLess;
using hancom::graph::controls::ClassifyNativeControl;
using hancom::graph::controls::ControlKind;
using hancom::graph::stories::NativeControlRecord;

bool KnownTypeRegistrySmoke() {
    constexpr std::array<std::pair<std::wstring_view, ControlKind>, 11>
        expected{{
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
    return std::all_of(
        expected.begin(),
        expected.end(),
        [](const auto& item) {
            return ClassifyNativeControl(item.first) == item.second;
        });
}

bool UnknownControlPreservesOpaqueFactsSmoke() {
    NativeControlRecord source;
    source.ctrlId = L"vendor-extension";
    source.instanceId = L"session-id";
    source.ctrlCh = 77;
    source.hasList = true;
    source.anchor = {4, 5, 6};
    source.headCtrlOrdinal = 99;
    AdaptedControlRecord adapted;
    return AdaptNativeControl(source, &adapted) &&
        adapted.kind == ControlKind::Unknown &&
        adapted.nativeTypeId == source.ctrlId &&
        adapted.sessionInstanceId == source.instanceId &&
        adapted.ctrlCh == source.ctrlCh &&
        adapted.hasList == source.hasList &&
        adapted.anchor.list == source.anchor.list &&
        adapted.anchor.paragraph == source.anchor.paragraph &&
        adapted.anchor.character == source.anchor.character &&
        adapted.headCtrlOrdinal == source.headCtrlOrdinal &&
        adapted.instanceIdSessionOnly &&
        adapted.unknownTypeDiagnostic;
}

bool SameAnchorOrdersByHeadControlOrdinalSmoke() {
    std::vector<AdaptedControlRecord> records(3);
    records[0].anchor = {1, 2, 3};
    records[0].headCtrlOrdinal = 8;
    records[1].anchor = {1, 2, 3};
    records[1].headCtrlOrdinal = 2;
    records[2].anchor = {1, 2, 3};
    records[2].headCtrlOrdinal = 5;
    std::sort(records.begin(), records.end(), CanonicalAnchorLess);
    return records[0].headCtrlOrdinal == 2 &&
        records[1].headCtrlOrdinal == 5 &&
        records[2].headCtrlOrdinal == 8;
}

bool MixedControlKindsFollowAuthoritativeNativeAnchorOrderSmoke() {
    const auto verify = [](std::vector<AdaptedControlRecord> records,
                           const std::array<std::wstring_view, 6>& expected) {
        if (!hancom::graph::controls::OrderByAuthoritativeNativeAnchor(
                &records) || records.size() != expected.size()) {
            return false;
        }
        for (size_t index = 0; index < expected.size(); ++index) {
            if (records[index].sessionInstanceId != expected[index]) {
                return false;
            }
        }
        return true;
    };
    const auto record = [](const ControlKind kind, const wchar_t* const id,
                           const LONG character,
                           const std::uint64_t ordinal) {
        AdaptedControlRecord value;
        value.kind = kind;
        value.sessionInstanceId = id;
        value.anchor = {7, 4, character};
        value.headCtrlOrdinal = ordinal;
        return value;
    };

    // Both inputs are deliberately grouped by family. IDs and native
    // positions disagree, and the second permutation reverses each family's
    // apparent fixed order. Only native position then HeadCtrl ordinal wins.
    const std::vector<AdaptedControlRecord> first{
        record(ControlKind::GeneralShape, L"z-generic", 80, 5),
        record(ControlKind::GeneralShape, L"a-generic", 10, 4),
        record(ControlKind::Table, L"z-table", 40, 2),
        record(ControlKind::Table, L"a-table", 10, 9),
        record(ControlKind::Unknown, L"z-image", 60, 1),
        record(ControlKind::Unknown, L"a-image", 10, 1),
    };
    const std::vector<AdaptedControlRecord> second{
        record(ControlKind::Table, L"a-table-2", 70, 8),
        record(ControlKind::Table, L"z-table-2", 20, 7),
        record(ControlKind::Unknown, L"a-image-2", 20, 3),
        record(ControlKind::Unknown, L"z-image-2", 90, 2),
        record(ControlKind::GeneralShape, L"a-generic-2", 50, 6),
        record(ControlKind::GeneralShape, L"z-generic-2", 20, 1),
    };
    return verify(first, {L"a-image", L"a-generic", L"a-table",
                          L"z-table", L"z-image", L"z-generic"}) &&
        verify(second, {L"z-generic-2", L"a-image-2", L"z-table-2",
                        L"a-generic-2", L"a-table-2", L"z-image-2"});
}

bool DuplicateAndEmptySessionIdsRemainDistinctSmoke() {
    NativeControlRecord first;
    first.ctrlId = L"tbl";
    first.instanceId = L"duplicate";
    first.headCtrlOrdinal = 1;
    NativeControlRecord second = first;
    second.headCtrlOrdinal = 2;
    NativeControlRecord third = first;
    third.instanceId.clear();
    third.headCtrlOrdinal = 3;
    AdaptedControlRecord a;
    AdaptedControlRecord b;
    AdaptedControlRecord c;
    return AdaptNativeControl(first, &a) &&
        AdaptNativeControl(second, &b) &&
        AdaptNativeControl(third, &c) &&
        a.sessionInstanceId == b.sessionInstanceId &&
        a.headCtrlOrdinal != b.headCtrlOrdinal &&
        c.sessionInstanceId.empty() &&
        c.headCtrlOrdinal == 3;
}

} // namespace

bool DocumentGraphControlsSmoke() {
    const bool known = KnownTypeRegistrySmoke();
    const bool unknown = UnknownControlPreservesOpaqueFactsSmoke();
    const bool equalAnchor = SameAnchorOrdersByHeadControlOrdinalSmoke();
    const bool mixedOrder =
        MixedControlKindsFollowAuthoritativeNativeAnchorOrderSmoke();
    const bool identities = DuplicateAndEmptySessionIdsRemainDistinctSmoke();
    std::wcout << L"CONTROLS_KNOWN_TYPE_REGISTRY " << known << L'\n'
               << L"CONTROLS_UNKNOWN_OPAQUE_FACTS " << unknown << L'\n'
               << L"CONTROLS_EQUAL_ANCHOR_ORDINAL_ORDER " << equalAnchor
               << L'\n'
               << L"CONTROLS_MIXED_NATIVE_ANCHOR_ORDER " << mixedOrder
               << L'\n'
               << L"CONTROLS_SESSION_IDS_NOT_IDENTITY " << identities << L'\n';
    return known && unknown && equalAnchor && mixedOrder && identities;
}

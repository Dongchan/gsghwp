#include "../DocumentGraphLayout.h"
#include "../DocumentGraphCaptureModel.h"

#include <algorithm>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

using hancom::graph::layout::CaptureStableLayout;
using hancom::graph::layout::CaptureStatus;
using hancom::graph::layout::ComputeLayoutRoot;
using hancom::graph::layout::GeometryField;
using hancom::graph::layout::LayoutEnvironmentPlatformV1;
using hancom::graph::layout::LayoutEnvironmentV1;
using hancom::graph::layout::LayoutFontTupleV1;
using hancom::graph::layout::LayoutNodeKind;
using hancom::graph::layout::LayoutNodeObservation;
using hancom::graph::layout::LayoutSnapshot;
using hancom::graph::layout::NativeLayoutObservationV1;
using hancom::graph::layout::ObservationState;
using hancom::graph::layout::QualificationMode;
using hancom::graph::layout::RecalculationState;
using hancom::graph::layout::RecalculationObservation;
using hancom::graph::layout::RecalculationRouteState;
using hancom::graph::layout::ScalarObservation;
using hancom::graph::layout::SectionField;
using hancom::graph::layout::SectionLayoutObservation;
using hancom::graph::layout::StableLayoutRecord;
using hancom::graph::layout::UnionPageSpans;

struct FakeSource final {
    std::vector<LayoutSnapshot> captures{};
    size_t nextCapture = 0;
    size_t recalculations = 0;
    RecalculationObservation recalculation{
        RecalculationRouteState::Performed,
        RecalculationRouteState::SemanticFalse,
        RecalculationState::Performed,
    };
    bool upgradeOnSecondSettle = false;
};

struct FakeNativeSource final {
    std::vector<NativeLayoutObservationV1> observations{};
    size_t nextObservation = 0;
    size_t recalculations = 0;
    RecalculationObservation recalculation{
        RecalculationRouteState::Performed,
        RecalculationRouteState::SemanticFalse,
        RecalculationState::Performed,
    };
    bool upgradeOnSecondSettle = false;
};

struct FakeEnvironmentPlatform final {
    int failedStage = 0;
    bool reverseFonts = false;
    std::uint8_t devmodeByte = 0x5a;
    std::array<size_t, 6> calls{};
    bool printerMatched = true;
};

bool ReadVersion(
    void* const raw,
    std::array<std::uint16_t, 4>* const output) noexcept {
    auto* const fake = static_cast<FakeEnvironmentPlatform*>(raw);
    if (fake == nullptr || output == nullptr) return false;
    ++fake->calls[0];
    if (fake->failedStage == 1) return false;
    *output = {13, 0, 4, 2};
    return true;
}

bool ReadPrinter(void* const raw, std::wstring* const output) noexcept {
    auto* const fake = static_cast<FakeEnvironmentPlatform*>(raw);
    if (fake == nullptr || output == nullptr) return false;
    ++fake->calls[1];
    if (fake->failedStage == 2) return false;
    try {
        *output = L"Injected Printer";
        return true;
    } catch (...) {
        return false;
    }
}

bool ReadDevMode(
    void* const raw,
    const std::wstring& printer,
    std::vector<std::uint8_t>* const output) noexcept {
    auto* const fake = static_cast<FakeEnvironmentPlatform*>(raw);
    if (fake == nullptr || output == nullptr) return false;
    ++fake->calls[2];
    fake->printerMatched = fake->printerMatched &&
        printer == L"Injected Printer";
    if (fake->failedStage == 3) return false;
    try {
        *output = {0x44, 0x4d, fake->devmodeByte, 0x00, 0xff};
        return true;
    } catch (...) {
        return false;
    }
}

bool ReadFonts(
    void* const raw,
    std::vector<LayoutFontTupleV1>* const output) noexcept {
    auto* const fake = static_cast<FakeEnvironmentPlatform*>(raw);
    if (fake == nullptr || output == nullptr) return false;
    ++fake->calls[3];
    if (fake->failedStage == 4) return false;
    try {
        const LayoutFontTupleV1 first{L"Zeta", 129, 2, 400, 0};
        const LayoutFontTupleV1 second{L"Alpha", 0, 1, 700, 1};
        *output = fake->reverseFonts
            ? std::vector<LayoutFontTupleV1>{second, first, second}
            : std::vector<LayoutFontTupleV1>{first, second, first};
        return true;
    } catch (...) {
        return false;
    }
}

bool ReadDpi(
    void* const raw,
    std::uint32_t* const x,
    std::uint32_t* const y) noexcept {
    auto* const fake = static_cast<FakeEnvironmentPlatform*>(raw);
    if (fake == nullptr || x == nullptr || y == nullptr) return false;
    ++fake->calls[4];
    if (fake->failedStage == 5) return false;
    *x = 144;
    *y = 120;
    return true;
}

bool ReadLcid(void* const raw, std::uint32_t* const output) noexcept {
    auto* const fake = static_cast<FakeEnvironmentPlatform*>(raw);
    if (fake == nullptr || output == nullptr) return false;
    ++fake->calls[5];
    if (fake->failedStage == 6) return false;
    *output = 0x0412;
    return true;
}

LayoutEnvironmentPlatformV1 FakePlatform(
    FakeEnvironmentPlatform* const fake) {
    return {
        fake, ReadVersion, ReadPrinter, ReadDevMode, ReadFonts, ReadDpi,
        ReadLcid,
    };
}

RecalculationObservation Recalculate(void* const context) noexcept {
    auto* const source = static_cast<FakeSource*>(context);
    if (source == nullptr) {
        return {
            RecalculationRouteState::Failed,
            RecalculationRouteState::Failed,
            RecalculationState::Failed,
        };
    }
    ++source->recalculations;
    if (source->upgradeOnSecondSettle && source->recalculations == 2) {
        return {
            RecalculationRouteState::Performed,
            RecalculationRouteState::SemanticFalse,
            RecalculationState::Performed,
        };
    }
    return source->recalculation;
}

bool Capture(void* const context, LayoutSnapshot* const output) noexcept {
    auto* const source = static_cast<FakeSource*>(context);
    if (source == nullptr || output == nullptr ||
        source->nextCapture >= source->captures.size()) {
        return false;
    }
    *output = source->captures[source->nextCapture++];
    return true;
}

RecalculationObservation RecalculateNative(void* const context) noexcept {
    auto* const source = static_cast<FakeNativeSource*>(context);
    if (source == nullptr) {
        return {
            RecalculationRouteState::Failed,
            RecalculationRouteState::Failed,
            RecalculationState::Failed,
        };
    }
    ++source->recalculations;
    if (source->upgradeOnSecondSettle && source->recalculations == 2) {
        return {
            RecalculationRouteState::Performed,
            RecalculationRouteState::SemanticFalse,
            RecalculationState::Performed,
        };
    }
    return source->recalculation;
}

bool ObserveNative(
    void* const context,
    NativeLayoutObservationV1* const output) noexcept {
    auto* const source = static_cast<FakeNativeSource*>(context);
    if (source == nullptr || output == nullptr ||
        source->nextObservation >= source->observations.size()) {
        return false;
    }
    *output = source->observations[source->nextObservation++];
    return true;
}

std::vector<std::uint8_t> TestEnvironment(
    const std::uint32_t dpiX = 96) {
    LayoutEnvironmentV1 environment;
    environment.hwpFileVersion = {13, 0, 0, 1};
    environment.printerName = L"Fixture Printer";
    environment.devmodeDigest.bytes.fill(0x11);
    environment.fontInventoryDigest.bytes.fill(0x22);
    environment.dpiX = dpiX;
    environment.dpiY = 96;
    environment.systemLcid = 0x409;
    environment.pageSetupDigest.bytes.fill(0x33);
    std::vector<std::uint8_t> serialized;
    if (!hancom::graph::layout::SerializeLayoutEnvironmentV1(
            environment, &serialized)) {
        return {};
    }
    return serialized;
}

ScalarObservation Value(const std::int64_t value) {
    return {ObservationState::Value, value};
}

LayoutNodeObservation Node(
    std::wstring id,
    const LayoutNodeKind kind,
    const long firstPage,
    const long lastPage) {
    LayoutNodeObservation node;
    node.nodeId = std::move(id);
    node.kind = kind;
    node.pageStart = Value(firstPage);
    node.pageEnd = Value(lastPage);
    node.geometry[static_cast<size_t>(GeometryField::Width)] = Value(7200);
    node.geometry[static_cast<size_t>(GeometryField::Height)] = Value(3600);
    return node;
}

LayoutSnapshot CompleteSnapshot() {
    LayoutSnapshot snapshot;
    snapshot.environment = TestEnvironment();
    snapshot.pageCount = 3;
    SectionLayoutObservation section;
    section.sectionIndex = 0;
    for (size_t field = 0; field < section.setup.size(); ++field) {
        section.setup[field] = Value(static_cast<std::int64_t>(field + 1));
    }
    snapshot.sections.push_back(section);
    snapshot.nodes = {
        Node(L"paragraph-1", LayoutNodeKind::Paragraph, 1, 1),
        Node(L"control-1", LayoutNodeKind::Control, 1, 2),
        Node(L"table-1", LayoutNodeKind::Table, 1, 3),
        Node(L"cell-1", LayoutNodeKind::Cell, 1, 1),
        Node(L"caption-1", LayoutNodeKind::Caption, 2, 2),
    };
    snapshot.nodes[0]
        .geometry[static_cast<size_t>(GeometryField::AbsoluteX)] =
        {ObservationState::NotExposed, 0};
    snapshot.nodes[0]
        .geometry[static_cast<size_t>(GeometryField::AbsoluteY)] =
        {ObservationState::NotExposed, 0};
    return snapshot;
}

bool NativeEnvironmentCollectorSmoke() {
    hancom::graph::Sha256 pendingPageSetup{};
    FakeEnvironmentPlatform firstSource;
    std::vector<std::uint8_t> first;
    std::vector<std::uint8_t> second;
    const bool firstCollected =
        hancom::graph::layout::CollectLayoutEnvironmentV1(
            FakePlatform(&firstSource), pendingPageSetup, &first) &&
        hancom::graph::layout::CollectLayoutEnvironmentV1(
            FakePlatform(&firstSource), pendingPageSetup, &second);

    FakeEnvironmentPlatform reorderedSource;
    reorderedSource.reverseFonts = true;
    std::vector<std::uint8_t> reordered;
    const bool reorderedCollected =
        hancom::graph::layout::CollectLayoutEnvironmentV1(
            FakePlatform(&reorderedSource), pendingPageSetup, &reordered);

    FakeEnvironmentPlatform changedDevModeSource;
    changedDevModeSource.devmodeByte = 0x5b;
    std::vector<std::uint8_t> changedDevMode;
    const bool changedCollected =
        hancom::graph::layout::CollectLayoutEnvironmentV1(
            FakePlatform(&changedDevModeSource), pendingPageSetup,
            &changedDevMode);

    bool allStagesFailClosed = true;
    for (int stage = 1; stage <= 6; ++stage) {
        FakeEnvironmentPlatform failedSource;
        failedSource.failedStage = stage;
        std::vector<std::uint8_t> unavailable{0xff};
        allStagesFailClosed = allStagesFailClosed &&
            !hancom::graph::layout::CollectLayoutEnvironmentV1(
                FakePlatform(&failedSource), pendingPageSetup,
                &unavailable) &&
            unavailable.empty();
    }

    const std::array<size_t, 6> expectedCalls{2, 2, 2, 2, 2, 2};
    const bool exactVersionWords = first.size() >= 16 &&
        first[0] == 8 && first[1] == 0 &&
        first[8] == 13 && first[9] == 0 &&
        first[10] == 0 && first[11] == 0 &&
        first[12] == 4 && first[13] == 0 &&
        first[14] == 2 && first[15] == 0;
    return firstCollected && reorderedCollected && changedCollected &&
        first == second && first == reordered && first != changedDevMode &&
        firstSource.calls == expectedCalls && firstSource.printerMatched &&
        reorderedSource.printerMatched &&
        hancom::graph::layout::ValidateLayoutEnvironmentV1(first) &&
        exactVersionWords && allStagesFailClosed;
}

const LayoutNodeObservation* FindNode(
    const LayoutSnapshot& snapshot,
    const LayoutNodeKind kind) {
    const auto found = std::find_if(
        snapshot.nodes.begin(), snapshot.nodes.end(),
        [kind](const LayoutNodeObservation& node) {
            return node.kind == kind;
        });
    return found == snapshot.nodes.end() ? nullptr : &*found;
}

bool QualifiedNativeObservationSmoke(std::array<bool, 7>* const checks) {
    if (checks == nullptr) return false;
    checks->fill(false);
    const LayoutSnapshot fixture = CompleteSnapshot();
    NativeLayoutObservationV1 native;
    native.pageCount = fixture.pageCount;
    native.sections = fixture.sections;
    native.nodes = fixture.nodes;
    native.nodes[1].geometry[static_cast<size_t>(GeometryField::Width)] =
        Value(7201);
    native.nodes[1].geometry[static_cast<size_t>(GeometryField::Height)] =
        Value(3601);
    native.nodes[1].geometry[
        static_cast<size_t>(GeometryField::AnchorListId)] = Value(101);
    native.nodes[2].geometry[static_cast<size_t>(GeometryField::Width)] =
        Value(14401);
    native.nodes[2].geometry[static_cast<size_t>(GeometryField::Height)] =
        Value(10801);
    native.nodes[2].geometry[
        static_cast<size_t>(GeometryField::AnchorListId)] = Value(202);
    native.nodes[4].geometry[static_cast<size_t>(GeometryField::Width)] =
        Value(5401);
    native.nodes[4].geometry[static_cast<size_t>(GeometryField::Height)] =
        Value(1801);
    native.nodes[4].geometry[
        static_cast<size_t>(GeometryField::AnchorListId)] = Value(303);

    FakeEnvironmentPlatform environment;
    LayoutSnapshot captured;
    if (!hancom::graph::layout::BuildNativeLayoutSnapshotV1(
            native, FakePlatform(&environment), &captured)) {
        return false;
    }
    const LayoutNodeObservation* const paragraph =
        FindNode(captured, LayoutNodeKind::Paragraph);
    const LayoutNodeObservation* const control =
        FindNode(captured, LayoutNodeKind::Control);
    const LayoutNodeObservation* const table =
        FindNode(captured, LayoutNodeKind::Table);
    const LayoutNodeObservation* const caption =
        FindNode(captured, LayoutNodeKind::Caption);
    if (paragraph == nullptr || control == nullptr || table == nullptr ||
        caption == nullptr) {
        return false;
    }
    const auto exactGeometry = [](const LayoutNodeObservation& node,
                                  const std::int64_t width,
                                  const std::int64_t height,
                                  const std::int64_t anchor) {
        return node.geometry[static_cast<size_t>(GeometryField::Width)].value ==
                width &&
            node.geometry[static_cast<size_t>(GeometryField::Height)].value ==
                height &&
            node.geometry[
                static_cast<size_t>(GeometryField::AnchorListId)].value ==
                anchor;
    };
    const bool controlGeometry = exactGeometry(*control, 7201, 3601, 101);
    const bool tableGeometry = exactGeometry(*table, 14401, 10801, 202);
    const bool captionGeometry = exactGeometry(*caption, 5401, 1801, 303);
    const bool nonzeroPageSetupDigest = captured.environment.size() >= 32 &&
        std::any_of(
            captured.environment.end() - 32, captured.environment.end(),
            [](const std::uint8_t value) { return value != 0; });

    NativeLayoutObservationV1 oneUnitChanged = native;
    oneUnitChanged.nodes[1].geometry[
        static_cast<size_t>(GeometryField::Width)] = Value(7202);
    LayoutSnapshot changed;
    std::wstring firstRoot;
    std::wstring changedRoot;
    const bool oneUnitChangesRoot =
        hancom::graph::layout::BuildNativeLayoutSnapshotV1(
            oneUnitChanged, FakePlatform(&environment), &changed) &&
        ComputeLayoutRoot(captured, &firstRoot) == CaptureStatus::Complete &&
        ComputeLayoutRoot(changed, &changedRoot) == CaptureStatus::Complete &&
        firstRoot != changedRoot;
    (*checks)[0] = captured.sections.size() == 1 &&
        captured.sections[0].setup[
            static_cast<size_t>(SectionField::PageWidth)].value == 1 &&
        nonzeroPageSetupDigest;
    (*checks)[1] = paragraph->pageStart.value == 1 &&
        paragraph->pageEnd.value == 1;
    (*checks)[2] = control->pageStart.value == 1 &&
        control->pageEnd.value == 2 && controlGeometry;
    (*checks)[3] = table->pageStart.value == 1 &&
        table->pageEnd.value == 3 && tableGeometry;
    (*checks)[4] = caption->pageStart.value == 2 &&
        caption->pageEnd.value == 2 && captionGeometry;
    (*checks)[5] = controlGeometry && tableGeometry && captionGeometry;
    (*checks)[6] = oneUnitChangesRoot;
    return std::all_of(
        checks->begin(), checks->end(), [](const bool value) { return value; });
}

NativeLayoutObservationV1 QualifiedNativeFixture() {
    const LayoutSnapshot fixture = CompleteSnapshot();
    NativeLayoutObservationV1 native;
    native.pageCount = fixture.pageCount;
    native.sections = fixture.sections;
    native.nodes = fixture.nodes;
    native.nodes[1].geometry[static_cast<size_t>(GeometryField::Width)] =
        Value(7201);
    native.nodes[1].geometry[static_cast<size_t>(GeometryField::Height)] =
        Value(3601);
    native.nodes[1].geometry[
        static_cast<size_t>(GeometryField::AnchorListId)] = Value(101);
    native.nodes[2].geometry[static_cast<size_t>(GeometryField::Width)] =
        Value(14401);
    native.nodes[2].geometry[static_cast<size_t>(GeometryField::Height)] =
        Value(10801);
    native.nodes[2].geometry[
        static_cast<size_t>(GeometryField::AnchorListId)] = Value(202);
    native.nodes[3].pageStart = Value(2);
    native.nodes[3].pageEnd = Value(3);
    native.nodes[3].geometry[static_cast<size_t>(GeometryField::Width)] =
        Value(9001);
    native.nodes[3].geometry[static_cast<size_t>(GeometryField::Height)] =
        Value(4501);
    native.nodes[3].geometry[
        static_cast<size_t>(GeometryField::AnchorListId)] = Value(252);
    native.nodes[4].geometry[static_cast<size_t>(GeometryField::Width)] =
        Value(5401);
    native.nodes[4].geometry[static_cast<size_t>(GeometryField::Height)] =
        Value(1801);
    native.nodes[4].geometry[
        static_cast<size_t>(GeometryField::AnchorListId)] = Value(303);
    return native;
}

bool ProductionNativeCallbackSmoke(
    bool* const directCell,
    bool* const oneUnitUnstable) {
    if (directCell == nullptr || oneUnitUnstable == nullptr) return false;
    *directCell = false;
    *oneUnitUnstable = false;
    const NativeLayoutObservationV1 native = QualifiedNativeFixture();
    FakeNativeSource stableSource{{native, native}};
    FakeEnvironmentPlatform stableEnvironment;
    StableLayoutRecord stable;
    const CaptureStatus stableStatus =
        hancom::graph::layout::CaptureStableNativeLayoutV1(
            RecalculateNative,
            ObserveNative,
            &stableSource,
            QualificationMode::RequireRecalculation,
            FakePlatform(&stableEnvironment),
            &stable);
    const LayoutNodeObservation* const paragraph =
        FindNode(stable.snapshot, LayoutNodeKind::Paragraph);
    const LayoutNodeObservation* const control =
        FindNode(stable.snapshot, LayoutNodeKind::Control);
    const LayoutNodeObservation* const table =
        FindNode(stable.snapshot, LayoutNodeKind::Table);
    const LayoutNodeObservation* const cell =
        FindNode(stable.snapshot, LayoutNodeKind::Cell);
    const LayoutNodeObservation* const caption =
        FindNode(stable.snapshot, LayoutNodeKind::Caption);
    const auto exactGeometry = [](const LayoutNodeObservation* const node,
                                  const std::int64_t width,
                                  const std::int64_t height,
                                  const std::int64_t anchor) {
        return node != nullptr &&
            node->geometry[static_cast<size_t>(GeometryField::Width)].state ==
                ObservationState::Value &&
            node->geometry[static_cast<size_t>(GeometryField::Width)].value ==
                width &&
            node->geometry[static_cast<size_t>(GeometryField::Height)].state ==
                ObservationState::Value &&
            node->geometry[static_cast<size_t>(GeometryField::Height)].value ==
                height &&
            node->geometry[static_cast<size_t>(
                GeometryField::AnchorListId)].state ==
                ObservationState::Value &&
            node->geometry[static_cast<size_t>(
                GeometryField::AnchorListId)].value == anchor;
    };
    *directCell = cell != nullptr &&
        cell->pageStart.state == ObservationState::Value &&
        cell->pageStart.value == 2 &&
        cell->pageEnd.state == ObservationState::Value &&
        cell->pageEnd.value == 3 && exactGeometry(cell, 9001, 4501, 252);
    const bool sectionAndDigest = stable.snapshot.sections.size() == 1 &&
        stable.snapshot.sections[0].setup[static_cast<size_t>(
            SectionField::PageWidth)].value == 1 &&
        stable.snapshot.environment.size() >= 32 &&
        std::any_of(
            stable.snapshot.environment.end() - 32,
            stable.snapshot.environment.end(),
            [](const std::uint8_t value) { return value != 0; });
    const bool allKindsAndExactUnits = paragraph != nullptr &&
        exactGeometry(control, 7201, 3601, 101) &&
        exactGeometry(table, 14401, 10801, 202) && *directCell &&
        exactGeometry(caption, 5401, 1801, 303);

    NativeLayoutObservationV1 changed = native;
    changed.nodes[1].geometry[static_cast<size_t>(GeometryField::Width)] =
        Value(7202);
    FakeNativeSource changingSource{{native, changed}};
    FakeEnvironmentPlatform changingEnvironment;
    StableLayoutRecord rejected;
    const CaptureStatus changingStatus =
        hancom::graph::layout::CaptureStableNativeLayoutV1(
            RecalculateNative,
            ObserveNative,
            &changingSource,
            QualificationMode::RequireRecalculation,
            FakePlatform(&changingEnvironment),
            &rejected);
    *oneUnitUnstable = changingStatus == CaptureStatus::LayoutUnstable &&
        changingSource.nextObservation == 2 && rejected.layoutRoot.empty();
    return stableStatus == CaptureStatus::Complete &&
        stableSource.recalculations == 2 &&
        stableSource.nextObservation == 2 && stable.layoutRoot.size() == 64 &&
        sectionAndDigest && allKindsAndExactUnits && *oneUnitUnstable;
}

bool TypedSectionPageSetupSmoke(
    bool* const distinctSections,
    bool* const deterministicSensitivity) {
    if (distinctSections == nullptr || deterministicSensitivity == nullptr) {
        return false;
    }
    *distinctSections = false;
    *deterministicSensitivity = false;
    constexpr std::array<hancom::graph::PropertyKeyId, 11> keys{
        7000, 7001, 7002, 7003, 7004, 7005, 7006, 7007, 7008, 7009,
        7010};
    const auto definition = [&keys](
        const std::wstring& identity,
        const std::int64_t base) {
        hancom::graph::capture::DefinitionObservation result;
        result.kind = hancom::graph::DefinitionKind::PageDef;
        result.bodyState = hancom::graph::ObservationState::Value;
        result.identity = identity;
        for (size_t index = 0; index < keys.size(); ++index) {
            hancom::graph::capture::PropertyObservation property;
            property.target =
                hancom::graph::capture::PropertyTarget::Definition;
            property.targetIdentity = identity;
            property.key = keys[index];
            property.state = hancom::graph::ObservationState::Value;
            property.integerValue = base + static_cast<std::int64_t>(index);
            result.properties.push_back(std::move(property));
        }
        return result;
    };
    const auto columnDefinition = [](
        const std::wstring& identity,
        const std::int64_t count) {
        hancom::graph::capture::DefinitionObservation result;
        result.kind = hancom::graph::DefinitionKind::ColumnDef;
        result.bodyState = hancom::graph::ObservationState::Value;
        result.identity = identity;
        const std::array<std::pair<
            hancom::graph::PropertyKeyId, std::int64_t>, 3> properties{{
            {7100, count}, {7101, 1}, {7102, 850}}};
        for (const auto property : properties) {
            hancom::graph::capture::PropertyObservation observed;
            observed.target =
                hancom::graph::capture::PropertyTarget::Definition;
            observed.targetIdentity = identity;
            observed.key = property.first;
            observed.state = hancom::graph::ObservationState::Value;
            observed.integerValue = property.second;
            result.properties.push_back(std::move(observed));
        }
        return result;
    };
    hancom::graph::capture::ReaderPayload references;
    references.definitions = {
        definition(L"page-wide", 20000), definition(L"page-a4", 10000),
        columnDefinition(L"column-wide", 2),
        columnDefinition(L"column-a4", 1)};
    references.definitionReferences = {
        {hancom::graph::capture::PropertyTarget::Section, L"1",
         hancom::graph::EdgeKind::PageDefRef, L"page-wide", 1},
        {hancom::graph::capture::PropertyTarget::Section, L"0",
         hancom::graph::EdgeKind::PageDefRef, L"page-a4", 0},
        {hancom::graph::capture::PropertyTarget::Section, L"1",
         hancom::graph::EdgeKind::ColumnDefRef, L"column-wide", 1},
        {hancom::graph::capture::PropertyTarget::Section, L"0",
         hancom::graph::EdgeKind::ColumnDefRef, L"column-a4", 0},
    };
    std::vector<SectionLayoutObservation> sections;
    if (!hancom::graph::layout::BuildSectionLayoutObservationsV1(
            references, &sections)) {
        return false;
    }
    *distinctSections = sections.size() == 2 &&
        sections[0].sectionIndex == 0 && sections[1].sectionIndex == 1 &&
        sections[0].setup[static_cast<size_t>(SectionField::PageWidth)].state ==
            ObservationState::Value &&
        sections[0].setup[static_cast<size_t>(SectionField::PageWidth)].value ==
            10000 &&
        sections[1].setup[static_cast<size_t>(SectionField::PageWidth)].value ==
            20000 &&
        sections[0].setup[static_cast<size_t>(SectionField::GutterDistance)].value ==
            10008 &&
        sections[1].setup[static_cast<size_t>(SectionField::GutterDistance)].value ==
            20008 &&
        sections[0].setup[static_cast<size_t>(SectionField::Orientation)].value ==
            10009 &&
        sections[1].setup[static_cast<size_t>(SectionField::Orientation)].value ==
            20009 &&
        sections[0].setup[static_cast<size_t>(SectionField::GutterType)].value ==
            10010 &&
        sections[1].setup[static_cast<size_t>(SectionField::GutterType)].value ==
            20010;

    NativeLayoutObservationV1 first;
    first.pageCount = 2;
    first.sections = sections;
    first.nodes = {Node(L"paragraph", LayoutNodeKind::Paragraph, 1, 2)};
    NativeLayoutObservationV1 reordered = first;
    std::reverse(reordered.sections.begin(), reordered.sections.end());
    NativeLayoutObservationV1 changed = first;
    changed.sections[1].setup[static_cast<size_t>(SectionField::MarginRight)] =
        Value(22222);
    FakeEnvironmentPlatform environment;
    LayoutSnapshot firstSnapshot;
    LayoutSnapshot reorderedSnapshot;
    LayoutSnapshot changedSnapshot;
    std::wstring firstRoot;
    std::wstring reorderedRoot;
    std::wstring changedRoot;
    const bool built =
        hancom::graph::layout::BuildNativeLayoutSnapshotV1(
            first, FakePlatform(&environment), &firstSnapshot) &&
        hancom::graph::layout::BuildNativeLayoutSnapshotV1(
            reordered, FakePlatform(&environment), &reorderedSnapshot) &&
        hancom::graph::layout::BuildNativeLayoutSnapshotV1(
            changed, FakePlatform(&environment), &changedSnapshot) &&
        ComputeLayoutRoot(firstSnapshot, &firstRoot) == CaptureStatus::Complete &&
        ComputeLayoutRoot(reorderedSnapshot, &reorderedRoot) ==
            CaptureStatus::Complete &&
        ComputeLayoutRoot(changedSnapshot, &changedRoot) ==
            CaptureStatus::Complete;
    const bool sameDigest = built && firstSnapshot.environment.size() >= 32 &&
        reorderedSnapshot.environment.size() >= 32 &&
        std::equal(firstSnapshot.environment.end() - 32,
                   firstSnapshot.environment.end(),
                   reorderedSnapshot.environment.end() - 32);
    const bool changedDigest = built && changedSnapshot.environment.size() >= 32 &&
        !std::equal(firstSnapshot.environment.end() - 32,
                    firstSnapshot.environment.end(),
                    changedSnapshot.environment.end() - 32);
    *deterministicSensitivity = sameDigest && changedDigest &&
        firstRoot == reorderedRoot && firstRoot != changedRoot;
    return *distinctSections && *deterministicSensitivity;
}

bool PageSetupDigestReplacementSmoke() {
    const std::vector<std::uint8_t> original = TestEnvironment();
    hancom::graph::Sha256 digest;
    digest.bytes.fill(0x7a);
    std::vector<std::uint8_t> replaced;
    return hancom::graph::layout::ReplacePageSetupDigestV1(
               original, digest, &replaced) &&
        replaced != original &&
        hancom::graph::layout::ValidateLayoutEnvironmentV1(replaced) &&
        std::equal(
            digest.bytes.rbegin(), digest.bytes.rend(), replaced.rbegin());
}

bool StableBackToBackCaptureSmoke() {
    LayoutSnapshot first = CompleteSnapshot();
    LayoutSnapshot second = first;
    std::reverse(second.nodes.begin(), second.nodes.end());
    FakeSource source{{first, second}};
    StableLayoutRecord stable;
    return CaptureStableLayout(
               Recalculate,
               Capture,
               &source,
               QualificationMode::RequireRecalculation,
               &stable) == CaptureStatus::Complete &&
        source.recalculations == 2 &&
        source.nextCapture == 2 &&
        stable.layoutRoot.size() == 64 &&
        stable.snapshot.nodes.size() == 5;
}

bool ChangingLayoutNeverVerifiesSmoke() {
    LayoutSnapshot first = CompleteSnapshot();
    LayoutSnapshot second = first;
    second.nodes[1]
        .geometry[static_cast<size_t>(GeometryField::Width)] = Value(7201);
    FakeSource source{{first, second}};
    StableLayoutRecord stable;
    return CaptureStableLayout(
               Recalculate,
               Capture,
               &source,
               QualificationMode::RequireRecalculation,
               &stable) == CaptureStatus::LayoutUnstable &&
        stable.layoutRoot.empty();
}

bool SplitTablePageUnionSmoke() {
    const std::vector<LayoutNodeObservation> cells{
        Node(L"cell-a", LayoutNodeKind::Cell, 1, 1),
        Node(L"cell-b", LayoutNodeKind::Cell, 2, 3),
    };
    ScalarObservation first;
    ScalarObservation last;
    return UnionPageSpans(cells, &first, &last) ==
            CaptureStatus::Complete &&
        first.state == ObservationState::Value && first.value == 1 &&
        last.state == ObservationState::Value && last.value == 3;
}

bool InvalidPageBoundsFailClosedSmoke() {
    LayoutSnapshot invalid = CompleteSnapshot();
    invalid.nodes[0].pageEnd = Value(4);
    std::wstring root;
    return ComputeLayoutRoot(invalid, &root) ==
            CaptureStatus::InvalidLayout &&
        root.empty();
}

bool UnavailableGeometryDiffersFromZeroSmoke() {
    LayoutSnapshot unavailable = CompleteSnapshot();
    LayoutSnapshot zero = unavailable;
    zero.nodes[0]
        .geometry[static_cast<size_t>(GeometryField::AbsoluteX)] = Value(0);
    std::wstring firstRoot;
    std::wstring secondRoot;
    return ComputeLayoutRoot(unavailable, &firstRoot) ==
            CaptureStatus::Complete &&
        ComputeLayoutRoot(zero, &secondRoot) == CaptureStatus::Complete &&
        firstRoot != secondRoot;
}

bool NormativeEnvironmentMutationSmoke() {
    LayoutSnapshot first = CompleteSnapshot();
    LayoutSnapshot second = first;
    second.environment = TestEnvironment(120);
    std::wstring firstRoot;
    std::wstring secondRoot;
    std::vector<std::uint8_t> malformed = first.environment;
    malformed.pop_back();
    return hancom::graph::layout::ValidateLayoutEnvironmentV1(
               first.environment) &&
        !hancom::graph::layout::ValidateLayoutEnvironmentV1(malformed) &&
        ComputeLayoutRoot(first, &firstRoot) == CaptureStatus::Complete &&
        ComputeLayoutRoot(second, &secondRoot) == CaptureStatus::Complete &&
        firstRoot != secondRoot;
}

bool RecalculateFailureStopsCaptureSmoke() {
    FakeSource source{{CompleteSnapshot(), CompleteSnapshot()}};
    source.recalculation = {
        RecalculationRouteState::Failed,
        RecalculationRouteState::Failed,
        RecalculationState::Failed,
    };
    StableLayoutRecord stable;
    return CaptureStableLayout(
               Recalculate,
               Capture,
               &source,
               QualificationMode::RequireRecalculation,
               &stable) == CaptureStatus::SourceFailed &&
        source.recalculations == 1 &&
        source.nextCapture == 0;
}

bool RouteUpgradeStillRequiresTwoCapturesSmoke() {
    const NativeLayoutObservationV1 native = QualifiedNativeFixture();
    FakeNativeSource source{{native, native}};
    source.recalculation = {
        RecalculationRouteState::MemberNotExposed,
        RecalculationRouteState::SemanticFalse,
        RecalculationState::NotExposed,
    };
    source.upgradeOnSecondSettle = true;
    FakeEnvironmentPlatform environment;
    StableLayoutRecord stable;
    return hancom::graph::layout::CaptureStableNativeLayoutV1(
               RecalculateNative,
               ObserveNative,
               &source,
               QualificationMode::AllowStableObservationWhenNotExposed,
               FakePlatform(&environment),
               &stable) == CaptureStatus::Complete &&
        source.recalculations == 2 && source.nextObservation == 2 &&
        stable.settleAttempts[0].direct ==
            RecalculationRouteState::MemberNotExposed &&
        stable.settleAttempts[1].direct ==
            RecalculationRouteState::Performed &&
        stable.settleAttempts[1].aggregate ==
            RecalculationState::Performed &&
        stable.layoutRoot.size() == 64;
}

bool NotExposedRecalculationIsExplicitSmoke() {
    FakeSource required{{CompleteSnapshot(), CompleteSnapshot()}};
    required.recalculation = {
        RecalculationRouteState::MemberNotExposed,
        RecalculationRouteState::SemanticFalse,
        RecalculationState::NotExposed,
    };
    StableLayoutRecord rejected;
    FakeSource observed{{CompleteSnapshot(), CompleteSnapshot()}};
    observed.recalculation = required.recalculation;
    StableLayoutRecord stable;
    return CaptureStableLayout(
               Recalculate,
               Capture,
               &required,
               QualificationMode::RequireRecalculation,
               &rejected) ==
            CaptureStatus::RecalculationNotExposed &&
        CaptureStableLayout(
               Recalculate,
               Capture,
               &observed,
               QualificationMode::AllowStableObservationWhenNotExposed,
               &stable) == CaptureStatus::Complete &&
        stable.settleAttempts[0].direct ==
            RecalculationRouteState::MemberNotExposed &&
        stable.settleAttempts[0].action ==
            RecalculationRouteState::SemanticFalse &&
        stable.settleAttempts[1].aggregate ==
            RecalculationState::NotExposed &&
        stable.layoutRoot.size() == 64;
}

} // namespace

bool DocumentGraphLayoutSmoke() {
    const bool stable = StableBackToBackCaptureSmoke();
    const bool unstable = ChangingLayoutNeverVerifiesSmoke();
    const bool split = SplitTablePageUnionSmoke();
    const bool bounds = InvalidPageBoundsFailClosedSmoke();
    const bool unavailable = UnavailableGeometryDiffersFromZeroSmoke();
    const bool environment = NormativeEnvironmentMutationSmoke();
    const bool recalc = RecalculateFailureStopsCaptureSmoke();
    const bool boundary = NotExposedRecalculationIsExplicitSmoke();
    const bool upgrade = RouteUpgradeStillRequiresTwoCapturesSmoke();
    const bool collector = NativeEnvironmentCollectorSmoke();
    std::array<bool, 7> nativeChecks{};
    const bool nativeObservation = QualifiedNativeObservationSmoke(
        &nativeChecks);
    bool distinctSectionSetups = false;
    bool pageSetupSensitivity = false;
    const bool typedSectionPageSetup = TypedSectionPageSetupSmoke(
        &distinctSectionSetups, &pageSetupSensitivity);
    const bool pageSetup = PageSetupDigestReplacementSmoke();
    bool directCell = false;
    bool oneUnitUnstable = false;
    const bool productionCallback = ProductionNativeCallbackSmoke(
        &directCell, &oneUnitUnstable);
    std::wcout << L"LAYOUT_GRAPH_STABLE_BACK_TO_BACK " << stable << L'\n'
               << L"LAYOUT_GRAPH_UNSTABLE_REJECTED " << unstable << L'\n'
               << L"LAYOUT_GRAPH_SPLIT_TABLE_UNION " << split << L'\n'
               << L"LAYOUT_GRAPH_PAGE_BOUNDS " << bounds << L'\n'
               << L"LAYOUT_GRAPH_UNAVAILABLE_GEOMETRY " << unavailable << L'\n'
               << L"LAYOUT_GRAPH_NORMATIVE_ENVIRONMENT " << environment
               << L'\n'
               << L"LAYOUT_GRAPH_RECALC_FAILURE " << recalc << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_ENVIRONMENT_COLLECTOR "
               << collector << L'\n';
    std::wcout << L"LAYOUT_GRAPH_RECALC_NOT_EXPOSED " << boundary << L'\n'
               << L"LAYOUT_GRAPH_ROUTE_UPGRADE_TWO_CAPTURE_GATE "
               << upgrade << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_SECTION_PAGE_SETUP "
               << nativeChecks[0] << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_PARAGRAPH_SPAN "
               << nativeChecks[1] << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_CONTROL_SPAN_SIZE_ANCHOR "
               << nativeChecks[2] << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_TABLE_SPAN_SIZE_ANCHOR "
               << nativeChecks[3] << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_CAPTION_SPAN_SIZE_ANCHOR "
               << nativeChecks[4] << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_HWPUNIT_EXACT "
               << nativeChecks[5] << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_HWPUNIT_ONE_UNIT_ROOT_CHANGE "
               << nativeChecks[6] << L'\n'
               << L"LAYOUT_GRAPH_QUALIFIED_NATIVE_OBSERVATIONS "
               << nativeObservation << L'\n'
               << L"LAYOUT_GRAPH_TYPED_TWO_SECTION_PAGE_SETUP "
               << distinctSectionSetups << L'\n'
               << L"LAYOUT_GRAPH_PAGE_SETUP_DIGEST_ROOT_SENSITIVITY "
               << pageSetupSensitivity << L'\n'
               << L"LAYOUT_GRAPH_PAGE_SETUP_DIGEST_REPLACED "
               << pageSetup << L'\n'
               << L"LAYOUT_GRAPH_PRODUCTION_NATIVE_CALLBACK_PATH "
               << productionCallback << L'\n'
               << L"LAYOUT_GRAPH_NATIVE_CELL_DIRECT_SPAN_SIZE_ANCHOR "
               << directCell << L'\n'
               << L"LAYOUT_GRAPH_PRODUCTION_NATIVE_ONE_UNIT_UNSTABLE "
               << oneUnitUnstable << L'\n';
    return stable && unstable && split && bounds && unavailable &&
        environment && recalc && boundary && upgrade && collector &&
        nativeObservation && typedSectionPageSetup && pageSetup &&
        productionCallback && directCell && oneUnitUnstable;
}

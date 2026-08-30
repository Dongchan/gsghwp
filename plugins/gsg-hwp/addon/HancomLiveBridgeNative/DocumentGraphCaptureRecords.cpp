#include "DocumentGraphCaptureRecords.h"
#include "DocumentGraphControls.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <limits>
#include <map>
#include <numeric>
#include <memory>
#include <set>
#include <sstream>
#include <tuple>

namespace hancom::graph::capture {
namespace {

thread_local bool gTypedProfilesEnabled = false;
thread_local bool gTypedGraphReuseEnabled = true;
thread_local std::vector<TypedBuildProfile> gTypedProfiles{};
thread_local std::size_t gTypedProfileIndex =
    (std::numeric_limits<std::size_t>::max)();

std::uint64_t ThreadCpu100ns() noexcept {
    FILETIME creation{}, exit{}, kernel{}, user{};
    if (GetThreadTimes(GetCurrentThread(), &creation, &exit, &kernel, &user) ==
        FALSE) return 0;
    ULARGE_INTEGER kernelValue{}, userValue{};
    kernelValue.LowPart = kernel.dwLowDateTime;
    kernelValue.HighPart = kernel.dwHighDateTime;
    userValue.LowPart = user.dwLowDateTime;
    userValue.HighPart = user.dwHighDateTime;
    return kernelValue.QuadPart + userValue.QuadPart;
}

class TypedPhaseClock final {
public:
    explicit TypedPhaseClock(const TypedBuildSubstage stage) noexcept
        : stage_(stage) {
        Restart();
    }
    ~TypedPhaseClock() noexcept { Accumulate(); }
    void Transition(const TypedBuildSubstage stage) noexcept {
        Accumulate();
        stage_ = stage;
        Restart();
    }
private:
    void Restart() noexcept {
        LARGE_INTEGER qpc{};
        QueryPerformanceCounter(&qpc);
        wall_ = static_cast<std::uint64_t>(qpc.QuadPart);
        cpu_ = ThreadCpu100ns();
    }
    void Accumulate() noexcept {
        if (!gTypedProfilesEnabled ||
            gTypedProfileIndex >= gTypedProfiles.size()) return;
        LARGE_INTEGER qpc{};
        QueryPerformanceCounter(&qpc);
        const std::uint64_t cpu = ThreadCpu100ns();
        TypedBuildTiming& timing = gTypedProfiles[gTypedProfileIndex].stages[
            static_cast<std::size_t>(stage_)];
        timing.wallQpc += static_cast<std::uint64_t>(qpc.QuadPart) - wall_;
        timing.cpu100ns += cpu >= cpu_ ? cpu - cpu_ : 0;
    }
    TypedBuildSubstage stage_;
    std::uint64_t wall_ = 0;
    std::uint64_t cpu_ = 0;
};

void BeginTypedProfile() {
    if (!gTypedProfilesEnabled) return;
    gTypedProfiles.emplace_back();
    gTypedProfileIndex = gTypedProfiles.size() - 1;
}

using codec::Bytes;
using codec::Error;
using codec::Field;
using codec::LogicalRecord;

constexpr std::uint64_t kTypedProfileBits = 0x1f;
constexpr std::uint16_t kRequiredArray = static_cast<std::uint16_t>(
    kFieldFlagRequired | kFieldFlagArray);

std::uint64_t NextCaptionCaptureProvenance() noexcept {
    static std::atomic<std::uint64_t> next{1};
    return next.fetch_add(1, std::memory_order_relaxed);
}

Bytes UuidValue(const Uuid128& id) {
    return {id.bytes.begin(), id.bytes.end()};
}

Bytes Sha256Value(const Sha256& digest) {
    return {digest.bytes.begin(), digest.bytes.end()};
}

Bytes Utf16Value(const std::wstring& text) {
    return codec::Utf16(
        reinterpret_cast<const std::uint16_t*>(text.data()), text.size());
}

Bytes Observe(const ObservationState state, const Bytes& value = {}) {
    Error error = Error::None;
    const Bytes encoded = codec::Observation(
        state,
        state == ObservationState::ReadFailed ? E_FAIL : S_OK,
        nullptr,
        0,
        codec::View(value),
        &error);
    return error == Error::None ? encoded : Bytes{};
}

Bytes ObserveScalar(const ScalarObservation& observation) {
    return observation.state == ObservationState::Value
        ? Observe(observation.state, codec::Sint64(observation.value))
        : Observe(observation.state);
}

Bytes ObserveText(const TextObservation& observation) {
    return observation.state == ObservationState::Value
        ? Observe(observation.state, Utf16Value(observation.value))
        : Observe(observation.state);
}

bool ParseSha256(const std::wstring& text, Sha256* const digest) {
    if (digest == nullptr || text.size() != 64) {
        return false;
    }
    for (size_t index = 0; index < digest->bytes.size(); ++index) {
        const auto nibble = [](const wchar_t unit) -> int {
            if (unit >= L'0' && unit <= L'9') return unit - L'0';
            if (unit >= L'a' && unit <= L'f') return unit - L'a' + 10;
            if (unit >= L'A' && unit <= L'F') return unit - L'A' + 10;
            return -1;
        };
        const int high = nibble(text[index * 2]);
        const int low = nibble(text[index * 2 + 1]);
        if (high < 0 || low < 0) {
            return false;
        }
        digest->bytes[index] = static_cast<std::uint8_t>((high << 4) | low);
    }
    return true;
}

void Add(Bytes* const target, const Bytes& value) {
    target->insert(target->end(), value.begin(), value.end());
}

// Capture identity must be a pure function of the observed document, never of
// wall clock, process, capture ordinal, or system entropy. Two captures of the
// same unmodified document therefore emit identical node-id bytes, which is
// what keeps the recorder stream_digest equal across equivalent captures.
bool DeterministicNodeId(
    const std::wstring& key,
    const std::uint64_t attempt,
    NodeId* const id) noexcept {
    if (id == nullptr) {
        return false;
    }
    try {
        Bytes value;
        Add(&value, codec::Uint64(attempt));
        Add(&value, codec::Utf16(
            reinterpret_cast<const std::uint16_t*>(key.data()), key.size()));
        const Sha256 digest = codec::DomainHash(
            "HGN1-TODO18-CAPTURE-NODEID-V1", codec::View(value));
        std::copy(
            digest.bytes.begin(), digest.bytes.begin() + id->bytes.size(),
            id->bytes.begin());
        id->bytes[6] = static_cast<std::uint8_t>((id->bytes[6] & 0x0fU) | 0x40U);
        id->bytes[8] = static_cast<std::uint8_t>((id->bytes[8] & 0x3fU) | 0x80U);
        return true;
    } catch (...) {
        return false;
    }
}

// The default-constructed arena mints from system entropy; tests may inject a
// scripted source and still expect that source to be honored.
bool UsesInjectedUuidSource(const identity::UuidSource& source) noexcept {
    return source.fill != nullptr &&
        source.fill != &identity::SystemRandomBytes;
}

void AppendField(Bytes* const stream, const Field& field) {
    Bytes encoded;
    static_cast<void>(codec::EncodeField(field, &encoded));
    Add(stream, encoded);
}

Bytes EncodeRecord(const LogicalRecord& record) {
    Bytes encoded;
    static_cast<void>(codec::EncodeLogicalRecord(record, &encoded));
    return encoded;
}

Bytes EmptyArray() {
    return codec::ArrayValue(ScalarTag::Uint64, 0, {});
}

Bytes RecordIdArray(const std::vector<RecordId>& ids) {
    std::vector<Bytes> values;
    values.reserve(ids.size());
    for (const RecordId id : ids) {
        values.push_back(codec::Uint64(id));
    }
    return codec::ArrayValue(ScalarTag::Uint64, 0, values);
}

Bytes PositionValue(const NativePosition& position) {
    Bytes value = codec::Sint64(position.list);
    Add(&value, codec::Sint64(position.paragraph));
    Add(&value, codec::Sint64(position.character));
    return value;
}

Bytes LocatorHeader(const LocatorTag tag) {
    Bytes value = codec::Uint16(static_cast<std::uint16_t>(tag));
    value.resize(8, 0);
    return value;
}

Bytes StoryLocator(const std::int64_t list) {
    Bytes value = LocatorHeader(LocatorTag::Story);
    Add(&value, codec::Sint64(list));
    return value;
}

Bytes ParagraphLocator(const NativePosition& position) {
    Bytes value = LocatorHeader(LocatorTag::Paragraph);
    Add(&value, codec::Sint64(position.list));
    Add(&value, codec::Sint64(position.paragraph));
    return value;
}

Bytes RunLocator(const RunObservation& run) {
    Bytes value = LocatorHeader(LocatorTag::Run);
    Add(&value, codec::Sint64(run.start.list));
    Add(&value, codec::Sint64(run.start.paragraph));
    Add(&value, codec::Sint64(run.start.character));
    Add(&value, codec::Sint64(run.end.character));
    return value;
}

Bytes ControlLocator(
    const std::wstring& ctrlId,
    const bool instanceIdPresent,
    const std::wstring& instanceId,
    const std::uint64_t ordinal,
    const NativePosition& anchor) {
    Bytes value = LocatorHeader(LocatorTag::Control);
    Add(&value, Utf16Value(ctrlId));
    value.push_back(instanceIdPresent ? 1 : 0);
    if (instanceIdPresent) {
        Add(&value, Utf16Value(instanceId));
    }
    Add(&value, codec::Uint64(ordinal));
    Add(&value, PositionValue(anchor));
    return value;
}

std::wstring NativeSiteIdentity(
    const PropertyTarget target,
    const std::wstring& ctrlId,
    const std::uint64_t ordinal,
    const NativePosition& anchor,
    const bool instanceIdPresent,
    const std::wstring& rawInstanceId) {
    return std::to_wstring(static_cast<unsigned>(target)) + L":" + ctrlId +
        L":" + (instanceIdPresent ? L"1" : L"0") + L":" +
        std::to_wstring(ordinal) + L":" +
        std::to_wstring(anchor.list) + L":" +
        std::to_wstring(anchor.paragraph) + L":" +
        std::to_wstring(anchor.character) + L":" + rawInstanceId;
}

Bytes CellLocator(
    const NodeId& table,
    const CellObservation& cell) {
    Bytes value = LocatorHeader(LocatorTag::Cell);
    Add(&value, UuidValue(table));
    Add(&value, Utf16Value(cell.address));
    Add(&value, codec::Sint64(cell.listId));
    return value;
}

std::wstring Hex(const Bytes& value) {
    static constexpr wchar_t digits[] = L"0123456789abcdef";
    std::wstring text;
    text.reserve(value.size() * 2);
    for (const std::uint8_t unit : value) {
        text.push_back(digits[unit >> 4]);
        text.push_back(digits[unit & 0x0f]);
    }
    return text;
}

std::wstring IdentityKey(const NodeKind kind, const Bytes& locator) {
    return L"native:" +
        std::to_wstring(static_cast<std::uint16_t>(kind)) + L":" +
        Hex(locator);
}

std::wstring StoryIdentityKey(
    const NodeId& owner,
    const Bytes& locator) {
    return L"story-owner:" + Hex(UuidValue(owner)) + L":" + Hex(locator);
}

std::wstring ParagraphIdentityKey(
    const NodeId& owningStory,
    const Bytes& locator) {
    return L"paragraph-story:" + Hex(UuidValue(owningStory)) + L":" +
        Hex(locator);
}

std::wstring ControlCompleteKey(
    const NodeKind kind,
    const std::wstring& ctrlId,
    const bool instanceIdPresent,
    const std::wstring& instanceId) {
    return std::to_wstring(static_cast<std::uint16_t>(kind)) + L":" +
        Hex(Utf16Value(ctrlId)) + L":" +
        (instanceIdPresent ? L"present:" + Hex(Utf16Value(instanceId))
                           : L"absent");
}

std::wstring ControlOccurrenceKey(
    const NodeKind kind,
    const std::wstring& ctrlId,
    const bool instanceIdPresent,
    const std::wstring& instanceId,
    const std::uint64_t occurrence) {
    return L"control-occurrence:" + ControlCompleteKey(
        kind, ctrlId, instanceIdPresent, instanceId) + L":" +
        std::to_wstring(occurrence);
}

std::wstring TableStableKey(
    const NodeId& owningStory,
    const bool instanceIdPresent,
    const std::wstring& instanceId,
    const Bytes& locator,
    const size_t matchingInstances) {
    const std::wstring owner =
        L"table-owner:" + Hex(UuidValue(owningStory));
    return matchingInstances == 1
        ? owner + L":identity:" + ControlCompleteKey(
              NodeKind::Table, L"tbl", instanceIdPresent, instanceId)
        : owner + L":locator:" + Hex(locator);
}

ProfileId ReaderProfile(const QualifiedReader reader) noexcept {
    switch (reader) {
    case QualifiedReader::TextAtoms:
        return ProfileId::EditableText;
    case QualifiedReader::EffectiveProperties:
    case QualifiedReader::ImagesShapesCaptions:
        return ProfileId::EditableObjects;
    case QualifiedReader::StableLayout:
        return ProfileId::Layout;
    default:
        return ProfileId::Structure;
    }
}

Bytes SerializePayload(const ReaderPayload& payload) {
    Bytes value;
    value.push_back(static_cast<std::uint8_t>(payload.reader));
    value.push_back(static_cast<std::uint8_t>(payload.coverage));
    const auto count = [&value](const size_t size) {
        Add(&value, codec::Uint64(static_cast<std::uint64_t>(size)));
    };
    count(payload.sections.size());
    for (const SectionObservation& section : payload.sections) {
        Add(&value, codec::Uint64(section.ordinal));
        Add(&value, PositionValue(section.start));
    }
    count(payload.controls.size());
    for (const ControlObservation& control : payload.controls) {
        Add(&value, Utf16Value(control.ctrlId));
        value.push_back(control.instanceIdPresent ? 1 : 0);
        Add(&value, Utf16Value(control.instanceId));
        Add(&value, codec::Uint64(control.headCtrlOrdinal));
        value.push_back(static_cast<std::uint8_t>(control.anchorState));
        Add(&value, PositionValue(control.anchor));
    }
    count(payload.paragraphs.size());
    for (const ParagraphObservation& paragraph : payload.paragraphs) {
        Add(&value, codec::Uint64(paragraph.sectionOrdinal));
        Add(&value, PositionValue(paragraph.start));
        count(paragraph.runs.size());
        for (const RunObservation& run : paragraph.runs) {
            Add(&value, PositionValue(run.start));
            Add(&value, PositionValue(run.end));
            Add(&value, Utf16Value(run.text));
        }
    }
    count(payload.properties.size());
    count(payload.tables.size());
    for (const TableObservation& table : payload.tables) {
        value.push_back(table.instanceIdPresent ? 1 : 0);
        Add(&value, Utf16Value(table.instanceId));
        Add(&value, codec::Uint64(table.headCtrlOrdinal));
        value.push_back(static_cast<std::uint8_t>(table.anchorState));
        Add(&value, PositionValue(table.anchor));
        count(table.cells.size());
        for (const CellObservation& cell : table.cells) {
            Add(&value, Utf16Value(cell.address));
            value.push_back(static_cast<std::uint8_t>(cell.listState));
            Add(&value, codec::Sint64(cell.listId));
            Add(&value, codec::Uint64(cell.row1));
            Add(&value, codec::Uint64(cell.column1));
        }
    }
    count(payload.images.size());
    for (const ImageObservation& image : payload.images) {
        value.push_back(image.instanceIdPresent ? 1 : 0);
        Add(&value, Utf16Value(image.instanceId));
        Add(&value, Utf16Value(image.ctrlId));
        Add(&value, codec::Uint64(image.headCtrlOrdinal));
        value.push_back(static_cast<std::uint8_t>(image.anchorState));
        Add(&value, PositionValue(image.anchor));
    }
    count(payload.layoutProperties.size());
    for (const std::uint64_t observed : payload.layoutObservedKindCounts) {
        Add(&value, codec::Uint64(observed));
    }
    value.push_back(payload.layoutPerKindComplete ? 1 : 0);
    count(payload.definitions.size());
    for (const DefinitionObservation& definition : payload.definitions) {
        Add(&value, codec::Uint16(static_cast<std::uint16_t>(definition.kind)));
        Add(&value, codec::Sint64(definition.nativeId));
        value.push_back(static_cast<std::uint8_t>(definition.nativeIdState));
        value.push_back(static_cast<std::uint8_t>(definition.bodyState));
        Add(&value, Utf16Value(definition.identity));
        Add(&value, Sha256Value(definition.propertyDigest));
        count(definition.properties.size());
    }
    count(payload.definitionReferences.size());
    for (const DefinitionReferenceObservation& reference :
         payload.definitionReferences) {
        value.push_back(static_cast<std::uint8_t>(reference.source));
        Add(&value, Utf16Value(reference.sourceIdentity));
        Add(&value, codec::Uint16(static_cast<std::uint16_t>(reference.edge)));
        Add(&value, Utf16Value(reference.definitionIdentity));
        Add(&value, codec::Uint64(reference.ordinal));
    }
    Add(&value, codec::Uint64(payload.referenceTraversal.expectedSites));
    Add(&value, codec::Uint64(payload.referenceTraversal.visitedSites));
    value.push_back(payload.referenceTraversal.globalStyleCatalogNotExposed ? 1 : 0);
    value.push_back(payload.referenceTraversal.globalNumberingCatalogNotExposed ? 1 : 0);
    value.push_back(payload.referenceTraversal.globalBulletCatalogNotExposed ? 1 : 0);
    value.push_back(payload.referenceTraversal.globalTabDefCatalogNotExposed ? 1 : 0);
    if ((value.size() & 1U) != 0) {
        value.push_back(0); // Coverage evidence is a UTF-16 byte stream.
    }
    return value;
}

RecordBlob MakeBlob(const Bytes& bytes, const std::uint8_t discriminator) {
    RecordBlob blob;
    blob.bytes = bytes;
    const Sha256 digest = codec::Hash(codec::View(blob.bytes));
    std::copy_n(
        digest.bytes.begin(), blob.id.bytes.size(), blob.id.bytes.begin());
    blob.id.bytes.back() ^= discriminator;
    return blob;
}

struct Property final {
    FieldTag ownerField = 0;
    PropertyKeyId key = 0;
    ScalarTag scalar = ScalarTag::Bytes;
    ObservationState state = ObservationState::NotExposed;
    PropertyOrigin origin = PropertyOrigin::Unknown;
    Bytes value{};
    RecordId recordId = 0;
};

struct Edge final {
    EdgeKind kind = EdgeKind::Contains;
    size_t target = 0;
    std::uint64_t ordinal = 0;
    RecordId recordId = 0;
};

struct Node final {
    NodeId id{};
    NodeKind kind = NodeKind::Document;
    size_t parent = (std::numeric_limits<size_t>::max)();
    std::uint64_t sibling = 0;
    Bytes locator{};
    bool locatorAvailable = true;
    ObservationState locatorUnavailableState =
        ObservationState::NotApplicable;
    Bytes payload{};
    std::vector<Property> properties{};
    std::vector<Edge> edges{};
    std::vector<size_t> children{};
    bool controlFamily = false;
    ScalarObservation storyNativeList{};
    ScalarObservation captionAutomaticNumber{};
    ScalarObservation captionStyleId{};
    TextObservation captionStyleName{};
    ScalarObservation captionPageStart{};
    ScalarObservation captionPageEnd{};
    NativePosition anchor{};
    std::uint64_t headCtrlOrdinal = 0;
    RecordId recordId = 0;
};

struct GranularCoverage final {
    size_t owner = 0;
    CoverageCoordinateKind coordinate = CoverageCoordinateKind::NodeField;
    ProfileId profile = ProfileId::Structure;
    CoverageState state = CoverageState::NotExposed;
    FieldTag ownerField = 0;
    bool propertyKeyPresent = false;
    PropertyKeyId propertyKey = 0;
    size_t blob = 0;
    RecordId recordId = 0;
};

struct Diagnostic final {
    size_t owner = 0;
    DiagnosticCode code = DiagnosticCode::NativeReadFailure;
    Severity severity = Severity::Error;
    bool propertyKeyPresent = false;
    PropertyKeyId propertyKey = 0;
    std::int32_t nativeStatus = 0;
    size_t blob = 0;
    RecordId recordId = 0;
};

struct Graph final {
    std::vector<Node> nodes{};
    std::vector<RecordBlob> blobs{};
    std::array<size_t, kQualifiedReaderCount> coverageBlob{};
    size_t binaryCoverageBlob = 0;
    CoverageState binaryCoverageState = CoverageState::NotExposed;
    std::vector<GranularCoverage> granularCoverage{};
    std::vector<Diagnostic> diagnostics{};
};

struct FrozenTypedPlan final {
    // Retained attempt-local observations are immutable after construction and
    // are never borrowed by the other traversal.
    std::vector<ReaderPayload> payloads{};
    Graph graph{};
    std::array<RecordId, kQualifiedReaderCount> coverageIds{};
    RecordId binaryCoverageId = 0;
    RecordId recordCount = 1;
    std::vector<identity::TombstoneEntry> tombstones{};
    std::vector<identity::RemapEntry> remaps{};
};

bool OwningStoryId(
    const Graph& graph,
    size_t node,
    NodeId* const owningStory) {
    if (owningStory == nullptr) return false;
    while (node != (std::numeric_limits<size_t>::max)()) {
        if (node >= graph.nodes.size()) return false;
        if (graph.nodes[node].kind == NodeKind::Story) {
            *owningStory = graph.nodes[node].id;
            return true;
        }
        node = graph.nodes[node].parent;
    }
    return false;
}

bool TableOwnerScopeId(
    const Graph& graph,
    size_t node,
    NodeId* const ownerScope) {
    if (ownerScope == nullptr) return false;
    while (node != (std::numeric_limits<size_t>::max)()) {
        if (node >= graph.nodes.size()) return false;
        if (graph.nodes[node].kind == NodeKind::Story) {
            const size_t owner = graph.nodes[node].parent;
            *ownerScope = owner < graph.nodes.size() &&
                    graph.nodes[owner].kind == NodeKind::TableCell
                ? graph.nodes[owner].id
                : graph.nodes[node].id;
            return true;
        }
        node = graph.nodes[node].parent;
    }
    return false;
}

bool SameParagraph(const NativePosition& left, const NativePosition& right) {
    return left.list == right.list &&
        left.paragraph == right.paragraph;
}

bool SameAnchor(const NativePosition& left, const NativePosition& right) {
    return SameParagraph(left, right) &&
        left.character == right.character;
}

size_t AddNode(
    Graph* const graph,
    CaptureIdentityArena& arena,
    const NodeKind kind,
    const size_t parent,
    const std::uint64_t sibling,
    const Bytes& locator,
    const std::wstring& fallbackKey = {}) {
    Node node;
    node.kind = kind;
    node.parent = parent;
    node.sibling = sibling;
    node.locator = locator;
    node.locatorAvailable = !locator.empty();
    const std::wstring key = !fallbackKey.empty()
        ? fallbackKey
        : IdentityKey(kind, locator);
    if (key.empty() || !arena.Acquire(key, &node.id)) {
        return (std::numeric_limits<size_t>::max)();
    }
    const size_t index = graph->nodes.size();
    graph->nodes.push_back(std::move(node));
    if (parent != (std::numeric_limits<size_t>::max)()) {
        graph->nodes[parent].children.push_back(index);
        graph->nodes[parent].edges.push_back(
            {EdgeKind::Contains, index, sibling, 0});
    }
    return index;
}

size_t AddParagraphNode(
    Graph* const graph,
    CaptureIdentityArena& arena,
    const size_t parent,
    const std::uint64_t sibling,
    const NodeId& owningStory,
    const NativePosition& canonicalStart) {
    Node node;
    node.kind = NodeKind::Paragraph;
    node.parent = parent;
    node.sibling = sibling;
    node.locator = ParagraphLocator(canonicalStart);
    if (!arena.AcquireParagraph(owningStory, canonicalStart, &node.id)) {
        return (std::numeric_limits<size_t>::max)();
    }
    const size_t index = graph->nodes.size();
    graph->nodes.push_back(std::move(node));
    graph->nodes[parent].children.push_back(index);
    graph->nodes[parent].edges.push_back(
        {EdgeKind::Contains, index, sibling, 0});
    return index;
}

Bytes PropertyValue(const PropertyObservation& source) {
    switch (source.scalar) {
    case ScalarTag::UTF16:
        return Utf16Value(source.textValue);
    case ScalarTag::Uint8:
        return codec::Uint8(static_cast<std::uint8_t>(source.integerValue));
    case ScalarTag::Uint16:
        return codec::Uint16(static_cast<std::uint16_t>(source.integerValue));
    case ScalarTag::Uint32:
    case ScalarTag::BGR:
        return codec::Uint32(static_cast<std::uint32_t>(source.integerValue));
    case ScalarTag::Uint64:
        return codec::Uint64(static_cast<std::uint64_t>(source.integerValue));
    case ScalarTag::Sint32:
        return codec::Sint32(static_cast<std::int32_t>(source.integerValue));
    case ScalarTag::RawURC32:
        return codec::Uint32(static_cast<std::uint32_t>(source.integerValue));
    case ScalarTag::Enum:
        return codec::Enum(source.integerValue, nullptr, 0);
    case ScalarTag::HWPUNIT64:
    case ScalarTag::Sint64:
        return codec::Sint64(source.integerValue);
    case ScalarTag::Bool:
        return codec::Bool(source.integerValue != 0);
    default:
        return {};
    }
}

void AddProperty(Node* const node, const PropertyObservation& source) {
    Property property;
    property.ownerField = source.ownerField;
    property.key = source.key;
    property.scalar = source.scalar;
    property.state = source.state;
    property.origin = source.origin;
    if (source.state == ObservationState::Value) {
        property.value = PropertyValue(source);
    }
    node->properties.push_back(std::move(property));
}

std::uint64_t ReadUnsigned64(const Bytes& value) noexcept {
    if (value.size() != 8) return 0;
    std::uint64_t result = 0;
    for (size_t byte = 0; byte < 8; ++byte) {
        result |= static_cast<std::uint64_t>(value[byte]) << (byte * 8);
    }
    return result;
}

bool ValidRequiredLayoutState(const Property& property) noexcept {
    if (property.state == ObservationState::Value) {
        return property.origin == PropertyOrigin::Generated &&
            property.value.size() == 8;
    }
    return (property.state == ObservationState::NotExposed ||
            property.state == ObservationState::ReadFailed) &&
        property.origin == PropertyOrigin::Unavailable &&
        property.value.empty();
}

bool ValidatePerKindLayoutProperties(
    const Graph& graph,
    const std::array<std::uint64_t, 5>& certifiedCounts) noexcept {
    std::array<std::uint64_t, 5> assembledCounts{};
    for (const Node& node : graph.nodes) {
        const bool hasLayoutProperty = std::any_of(
            node.properties.begin(), node.properties.end(),
            [](const Property& property) {
                return property.key >= 12000 && property.key <= 12003;
            });
        if (!hasLayoutProperty) continue;
        switch (node.kind) {
        case NodeKind::Paragraph: ++assembledCounts[0]; break;
        case NodeKind::GenericControl: ++assembledCounts[1]; break;
        case NodeKind::Table: ++assembledCounts[2]; break;
        case NodeKind::TableCell: ++assembledCounts[3]; break;
        case NodeKind::Image: ++assembledCounts[4]; break;
        default: return false;
        }
        const bool spanKind = node.kind == NodeKind::Paragraph ||
            node.kind == NodeKind::GenericControl ||
            node.kind == NodeKind::Table ||
            node.kind == NodeKind::TableCell ||
            node.kind == NodeKind::Image;
        const bool geometryKind = node.kind == NodeKind::GenericControl ||
            node.kind == NodeKind::Table || node.kind == NodeKind::Image;
        std::array<const Property*, 4> fields{};
        for (const Property& property : node.properties) {
            if (property.key < 12000 || property.key > 12003) continue;
            const size_t index = static_cast<size_t>(property.key - 12000);
            if (!spanKind || fields[index] != nullptr ||
                property.ownerField != 10 ||
                property.scalar != (index < 2 ? ScalarTag::Uint64
                                             : ScalarTag::HWPUNIT64) ||
                (!geometryKind && index >= 2) ||
                !ValidRequiredLayoutState(property)) {
                return false;
            }
            fields[index] = &property;
        }
        if (!spanKind) continue;
        if (fields[0] == nullptr || fields[1] == nullptr ||
            (geometryKind && (fields[2] == nullptr || fields[3] == nullptr)) ||
            (!geometryKind && (fields[2] != nullptr || fields[3] != nullptr))) {
            return false;
        }
        if (fields[0]->state != fields[1]->state ||
            (fields[0]->state == ObservationState::Value &&
             (ReadUnsigned64(fields[0]->value) < 1 ||
              ReadUnsigned64(fields[1]->value) <
                  ReadUnsigned64(fields[0]->value)))) {
            return false;
        }
        if (geometryKind && fields[2]->state != fields[3]->state) {
            return false;
        }
        if (node.kind == NodeKind::TableCell &&
            (node.parent >= graph.nodes.size() ||
             graph.nodes[node.parent].kind != NodeKind::Table)) {
            return false;
        }
        if ((node.kind == NodeKind::GenericControl ||
             node.kind == NodeKind::Table || node.kind == NodeKind::Image) &&
            (node.parent >= graph.nodes.size() ||
             graph.nodes[node.parent].kind != NodeKind::Paragraph)) {
            return false;
        }
    }
    return assembledCounts == certifiedCounts &&
        std::accumulate(
            certifiedCounts.begin(), certifiedCounts.end(),
            std::uint64_t{0}) != 0;
}

bool ValidParagraphRuns(const ParagraphObservation& paragraph) noexcept {
    std::int64_t previousEnd = 0;
    bool first = true;
    for (const RunObservation& run : paragraph.runs) {
        if (!SameParagraph(run.start, paragraph.start) ||
            !SameParagraph(run.end, paragraph.start) ||
            run.start.character < 0 || run.end.character < run.start.character ||
            (!first && run.start.character < previousEnd) ||
            static_cast<std::uint64_t>(run.end.character - run.start.character) !=
                run.text.size()) {
            return false;
        }
        first = false;
        previousEnd = run.end.character;
    }
    return true;
}

std::uint64_t BagCount(const Node& node, const FieldTag field) {
    return static_cast<std::uint64_t>(std::count_if(
        node.properties.begin(),
        node.properties.end(),
        [field](const Property& property) {
            return property.ownerField == field;
        }));
}

Bytes Bag(const Node& node, const FieldTag field) {
    std::vector<RecordId> ids;
    for (const Property& property : node.properties) {
        if (property.ownerField == field) {
            ids.push_back(property.recordId);
        }
    }
    return RecordIdArray(ids);
}

Bytes ControlPayload(
    const Node& node,
    const std::wstring& ctrlId,
    const bool instanceIdPresent,
    const std::wstring& instanceId,
    const std::uint64_t ordinal,
    const NativePosition& anchor) {
    Bytes payload;
    AppendField(&payload, {100, kFieldFlagRequired, ScalarTag::UTF16, 1,
                           Observe(ObservationState::Value,
                                   Utf16Value(ctrlId))});
    AppendField(&payload, {101, kFieldFlagRequired, ScalarTag::UTF16, 1,
                           instanceIdPresent
                               ? Observe(ObservationState::Value,
                                         Utf16Value(instanceId))
                               : Observe(ObservationState::NotExposed)});
    AppendField(&payload, {102, kFieldFlagRequired, ScalarTag::UTF16, 1,
                           Observe(ObservationState::NotExposed)});
    AppendField(&payload, {103, kFieldFlagRequired, ScalarTag::Struct, 1,
                           Observe(ObservationState::Value,
                                   PositionValue(anchor))});
    AppendField(&payload, {104, kFieldFlagRequired, ScalarTag::Struct, 1,
                           Observe(ObservationState::NotExposed)});
    AppendField(&payload, {105, kFieldFlagRequired, ScalarTag::Uint64, 1,
                           codec::Uint64(ordinal)});
    AppendField(&payload, {106, kRequiredArray, ScalarTag::Uint64,
                           BagCount(node, 106), Bag(node, 106)});
    return payload;
}

Bytes TopologyDigestValue(const TableObservation& table) {
    Bytes value;
    Add(&value, codec::Uint64(table.rowCount));
    Add(&value, codec::Uint64(table.columnCount));
    for (const CellObservation& cell : table.cells) {
        Add(&value, Utf16Value(cell.address));
        Add(&value, codec::Uint64(cell.row1));
        Add(&value, codec::Uint64(cell.column1));
        Add(&value, codec::Uint64(cell.rowSpan));
        Add(&value, codec::Uint64(cell.columnSpan));
    }
    return Sha256Value(codec::DomainHash(
        "HWPGRAPH\0TABLETOPOLOGY\0V1", codec::View(value)));
}

Bytes PropertyRecordBytes(const Property& property, const NodeId& owner) {
    const Bytes observation = property.state == ObservationState::Value
        ? Observe(property.state, property.value)
        : Observe(property.state);
    return EncodeRecord({
        RecordKind::Property,
        kFieldFlagRequired,
        property.recordId,
        {
            {1, kFieldFlagRequired, ScalarTag::UUID128, 1,
             UuidValue(owner)},
            {2, kFieldFlagRequired, ScalarTag::Uint16, 1,
             codec::Uint16(property.ownerField)},
            {3, kFieldFlagRequired, ScalarTag::Uint32, 1,
             codec::Uint32(property.key)},
            {4, kFieldFlagRequired, ScalarTag::Struct, 1, observation},
            {5, kFieldFlagRequired, ScalarTag::Uint8, 1,
             codec::Uint8(static_cast<std::uint8_t>(property.origin))},
        },
    });
}

using FingerprintIndex =
    std::map<std::array<std::uint8_t, 16>, Sha256>;

Sha256 FingerprintFor(
    const NodeId& id,
    const FingerprintIndex* const fingerprints) {
    if (fingerprints != nullptr) {
        const auto found = fingerprints->find(id.bytes);
        if (found != fingerprints->end()) return found->second;
    }
    return {};
}

Bytes NodeRecordBytes(
    const Node& node,
    const NodeId* const parent,
    const FingerprintIndex* const fingerprints) {
    Bytes common;
    AppendField(&common, {1, kFieldFlagRequired, ScalarTag::UUID128, 1,
                          UuidValue(node.id)});
    AppendField(&common, {2, kFieldFlagRequired, ScalarTag::Uint16, 1,
                          codec::Uint16(
                              static_cast<std::uint16_t>(node.kind))});
    if (parent != nullptr) {
        AppendField(&common, {3, 0, ScalarTag::UUID128, 1,
                              UuidValue(*parent)});
    }
    AppendField(&common, {4, kFieldFlagRequired, ScalarTag::Uint64, 1,
                          codec::Uint64(node.sibling)});
    AppendField(&common, {5, kFieldFlagRequired, ScalarTag::Uint64, 1,
                          codec::Uint64(1)});
    AppendField(&common, {6, kFieldFlagRequired, ScalarTag::Uint64, 1,
                          codec::Uint64(1)});
    AppendField(&common, {7, kFieldFlagRequired, ScalarTag::Struct, 1,
                          node.locatorAvailable
                              ? Observe(ObservationState::Value, node.locator)
                              : Observe(node.locatorUnavailableState)});
    AppendField(&common, {8, kFieldFlagRequired, ScalarTag::SHA256, 1,
                          Sha256Value(FingerprintFor(node.id, fingerprints))});
    AppendField(&common, {10, kRequiredArray, ScalarTag::Uint64,
                          BagCount(node, 10), Bag(node, 10)});
    AppendField(&common, {11, kRequiredArray, ScalarTag::Uint64,
                          BagCount(node, 11), Bag(node, 11)});
    return EncodeRecord({
        RecordKind::Node,
        kFieldFlagRequired,
        node.recordId,
        {
            {1, kFieldFlagRequired, ScalarTag::Struct, 1, common},
            {2, kFieldFlagRequired, ScalarTag::Struct, 1, node.payload},
        },
    });
}

Bytes EdgeRecordBytes(
    const Edge& edge,
    const NodeId& source,
    const NodeId& target) {
    return EncodeRecord({
        RecordKind::Edge,
        kFieldFlagRequired,
        edge.recordId,
        {
            {1, kFieldFlagRequired, ScalarTag::Uint16, 1,
             codec::Uint16(static_cast<std::uint16_t>(edge.kind))},
            {2, kFieldFlagRequired, ScalarTag::UUID128, 1,
             UuidValue(source)},
            {3, kFieldFlagRequired, ScalarTag::UUID128, 1,
             UuidValue(target)},
            {4, kFieldFlagRequired, ScalarTag::Uint64, 1,
             codec::Uint64(edge.ordinal)},
        },
    });
}

Bytes CoverageRecordBytes(
    const RecordId id,
    const NodeId& owner,
    const CoverageCoordinateKind coordinate,
    const ProfileId profile,
    const CoverageState state,
    const RecordBlob& blob,
    const FieldTag ownerField = 100,
    const bool propertyKeyPresent = false,
    const PropertyKeyId propertyKey = 0) {
    const bool ownerPresent =
        coordinate != CoverageCoordinateKind::Global &&
        coordinate != CoverageCoordinateKind::Profile;
    const std::uint8_t encodedProfile =
        coordinate == CoverageCoordinateKind::Global ||
                coordinate == CoverageCoordinateKind::Node
            ? kNoProfile
            : static_cast<std::uint8_t>(profile);
    const RecordKind ownerKind =
        coordinate == CoverageCoordinateKind::Global ? RecordKind::Manifest :
        coordinate == CoverageCoordinateKind::Profile ? RecordKind::Coverage :
        coordinate == CoverageCoordinateKind::Property ? RecordKind::Property :
        RecordKind::Node;
    const FieldTag encodedOwnerField =
        coordinate == CoverageCoordinateKind::Global ||
                coordinate == CoverageCoordinateKind::Node
            ? 0
            : coordinate == CoverageCoordinateKind::Profile
                ? static_cast<FieldTag>(encodedProfile)
                : ownerField;
    std::vector<Field> fields;
    if (ownerPresent) {
        fields.push_back(
            {1, 0, ScalarTag::UUID128, 1, UuidValue(owner)});
    }
    fields.push_back({2, kFieldFlagRequired, ScalarTag::Uint8, 1,
                      codec::Uint8(encodedProfile)});
    fields.push_back({3, kFieldFlagRequired, ScalarTag::Uint8, 1,
                      codec::Uint8(static_cast<std::uint8_t>(state))});
    if (propertyKeyPresent) {
        fields.push_back({4, 0, ScalarTag::Uint32, 1,
                          codec::Uint32(propertyKey)});
    }
    fields.push_back({5, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
                      codec::BlobSliceValue(
                          blob.id, 0, blob.bytes.size(),
                          codec::Hash(codec::View(blob.bytes)))});
    fields.push_back({6, kFieldFlagRequired, ScalarTag::Uint16, 1,
                      codec::Uint16(static_cast<std::uint16_t>(ownerKind))});
    fields.push_back({7, kFieldFlagRequired, ScalarTag::Uint16, 1,
                      codec::Uint16(encodedOwnerField)});
    return EncodeRecord({
        RecordKind::Coverage, kFieldFlagRequired, id, std::move(fields)});
}

Bytes TombstoneRecordBytes(
    const RecordId id,
    const identity::TombstoneEntry& entry) {
    return EncodeRecord({
        RecordKind::Tombstone, kFieldFlagRequired, id,
        {
            {1, kFieldFlagRequired, ScalarTag::UUID128, 1,
             UuidValue(entry.node)},
            {2, kFieldFlagRequired, ScalarTag::Uint64, 1,
             codec::Uint64(entry.semanticRevision)},
            {3, kFieldFlagRequired, ScalarTag::Uint16, 1,
             codec::Uint16(static_cast<std::uint16_t>(entry.reason))},
        },
    });
}

Bytes RemapRecordBytes(
    const RecordId id,
    const identity::RemapEntry& entry) {
    std::vector<Field> fields{
        {1, kFieldFlagRequired, ScalarTag::UUID128, 1,
         UuidValue(entry.source)},
    };
    if (entry.targetPresent) {
        fields.push_back({2, 0, ScalarTag::UUID128, 1,
                          UuidValue(entry.target)});
    }
    fields.push_back({3, kFieldFlagRequired, ScalarTag::Uint8, 1,
                      codec::Uint8(static_cast<std::uint8_t>(
                          entry.disposition))});
    fields.push_back({4, kFieldFlagRequired, ScalarTag::Uint16, 1,
                      codec::Uint16(static_cast<std::uint16_t>(
                          entry.reason))});
    return EncodeRecord({
        RecordKind::Remap, kFieldFlagRequired, id, std::move(fields)});
}

Bytes DiagnosticRecordBytes(
    const Diagnostic& diagnostic,
    const NodeId& owner,
    const RecordBlob& blob) {
    std::vector<Field> fields{
        {1, kFieldFlagRequired, ScalarTag::Uint32, 1,
         codec::Uint32(static_cast<std::uint32_t>(diagnostic.code))},
        {2, kFieldFlagRequired, ScalarTag::Uint8, 1,
         codec::Uint8(static_cast<std::uint8_t>(diagnostic.severity))},
        {3, 0, ScalarTag::UUID128, 1, UuidValue(owner)},
        {5, kFieldFlagRequired, ScalarTag::Sint32, 1,
         codec::Sint32(diagnostic.nativeStatus)},
        {6, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
         codec::BlobSliceValue(
             blob.id, 0, blob.bytes.size(),
             codec::Hash(codec::View(blob.bytes)))},
    };
    if (diagnostic.propertyKeyPresent) {
        fields.insert(fields.begin() + 3,
                      {4, 0, ScalarTag::Uint32, 1,
                       codec::Uint32(diagnostic.propertyKey)});
    }
    return EncodeRecord({
        RecordKind::Diagnostic, kFieldFlagRequired,
        diagnostic.recordId, std::move(fields)});
}

Bytes ManifestRecordBytes(
    const std::uint64_t count,
    const Sha256& indexDigest,
    const codec::CanonicalizationResult* const canonical) {
    Bytes version(kGraphVersionBytesV1, 0);
    const Bytes schema = codec::Uint16(codec::kCodecVersion);
    const Bytes profiles = codec::Uint64(kTypedProfileBits);
    std::copy(schema.begin(), schema.end(), version.begin());
    std::copy(profiles.begin(), profiles.end(), version.begin() + 2);
    if (canonical != nullptr) {
        version[10] = static_cast<std::uint8_t>(
            canonical->semanticCertified);
        version[11] = static_cast<std::uint8_t>(canonical->layoutPresent);
        std::copy(
            canonical->observedSemanticRoot.bytes.begin(),
            canonical->observedSemanticRoot.bytes.end(),
            version.begin() + 72);
        std::copy(
            canonical->layoutRoot.bytes.begin(),
            canonical->layoutRoot.bytes.end(),
            version.begin() + 104);
        std::copy(
            canonical->captureRoot.bytes.begin(),
            canonical->captureRoot.bytes.end(),
            version.begin() + 136);
    }
    return EncodeRecord({
        RecordKind::Manifest,
        kFieldFlagRequired,
        0,
        {
            {1, kFieldFlagRequired, ScalarTag::Struct, 1, version},
            {2, kFieldFlagRequired, ScalarTag::Uint8, 1,
             codec::Uint8(static_cast<std::uint8_t>(
                 CaptureIntegrity::Complete))},
            {3, kFieldFlagRequired, ScalarTag::Uint64, 1,
             codec::Uint64(kTypedProfileBits)},
            {4, kFieldFlagRequired, ScalarTag::Uint64, 1,
             codec::Uint64(count)},
            {5, kFieldFlagRequired, ScalarTag::SHA256, 1,
             Sha256Value(indexDigest)},
            {6, kFieldFlagRequired, ScalarTag::Uint8, 1,
             codec::Uint8(static_cast<std::uint8_t>(StreamKind::Capture))},
        },
    });
}

const ReaderPayload* Payload(
    const std::array<const ReaderPayload*, kQualifiedReaderCount>& payloads,
    const QualifiedReader reader) {
    return payloads[static_cast<size_t>(reader)];
}

bool BuildGraph(
    const std::vector<ReaderPayload>& source,
    CaptureIdentityArena& arena,
    Graph* const graph,
    std::wstring* const failure) {
    TypedPhaseClock profile(TypedBuildSubstage::GraphConstruction);
    const auto stage = [failure](const wchar_t* const value) {
        if (failure != nullptr) *failure = L"graph:" + std::wstring(value);
    };
    stage(L"payload-validation");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedGraphPayloadValidation,
        source.size(), 0);
    std::array<const ReaderPayload*, kQualifiedReaderCount> payloads{};
    for (const ReaderPayload& payload : source) {
        const size_t index = static_cast<size_t>(payload.reader);
        if (index >= payloads.size() || payloads[index] != nullptr) {
            return false;
        }
        payloads[index] = &payload;
    }
    for (const ReaderPayload* const payload : payloads) {
        if (payload == nullptr ||
            payload->outcome != ReaderOutcome::Complete ||
            payload->coverage == CoverageState::NotRequested) {
            return false;
        }
    }
    stage(L"coverage-blobs");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedGraphCoveragePreparation,
        payloads.size(), 0);
    graph->blobs.clear();
    for (size_t index = 0; index < payloads.size(); ++index) {
        graph->coverageBlob[index] = graph->blobs.size();
        graph->blobs.push_back(MakeBlob(
            SerializePayload(*payloads[index]),
            static_cast<std::uint8_t>(index + 1)));
    }
    graph->binaryCoverageBlob = graph->blobs.size();
    graph->blobs.push_back(MakeBlob({}, 0xfe));

    const ReaderPayload& story = *Payload(payloads, QualifiedReader::StorySpine);
    const ReaderPayload& text = *Payload(payloads, QualifiedReader::TextAtoms);
    const ReaderPayload& controls = *Payload(payloads, QualifiedReader::ControlAdapters);
    const ReaderPayload& tables = *Payload(payloads, QualifiedReader::TableTopology);
    const ReaderPayload& images = *Payload(payloads, QualifiedReader::ImagesShapesCaptions);
    const ReaderPayload& effective = *Payload(payloads, QualifiedReader::EffectiveProperties);
    const ReaderPayload& layout = *Payload(payloads, QualifiedReader::StableLayout);
    if (!CaptionLocationsReadyForGraphPublication(arena, images.images)) {
        return false;
    }
    graph->binaryCoverageState = images.images.empty()
        ? CoverageState::NotApplicable
        : std::all_of(
              images.images.begin(), images.images.end(),
              [](const ImageObservation& image) {
                  return image.asset.binaryState == ObservationState::Value;
              })
            ? CoverageState::Complete
            : CoverageState::NotExposed;
    if (story.sections.empty() || text.paragraphs.empty()) {
        return false;
    }

    stage(L"document-definitions");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedGraphStructureFamilies,
        story.sections.size(), text.paragraphs.size());
    const size_t none = (std::numeric_limits<size_t>::max)();
    const size_t document = AddNode(
        graph, arena, NodeKind::Document, none, 0, {}, L"document");
    if (document == none ||
        effective.referenceTraversal.expectedSites !=
            effective.referenceTraversal.visitedSites) {
        return false;
    }
    std::vector<size_t> definitionNodes;
    definitionNodes.reserve(effective.definitions.size());
    std::map<std::wstring, std::pair<size_t, DefinitionKind>>
        definitionByIdentity;
    for (size_t index = 0; index < effective.definitions.size(); ++index) {
        const DefinitionObservation& definition = effective.definitions[index];
        if (definition.identity.empty() ||
            definition.nativeIdState == ObservationState::NotRequested ||
            definition.bodyState == ObservationState::NotRequested) {
            return false;
        }
        const size_t node = AddNode(
            graph, arena, NodeKind::Definition, document, index, {},
            L"definition:" + definition.identity);
        if (node == none) {
            return false;
        }
        for (const PropertyObservation& property : definition.properties) {
            if (property.target != PropertyTarget::Definition ||
                property.targetIdentity != definition.identity ||
                property.ownerField != 102) {
                return false;
            }
            AddProperty(&graph->nodes[node], property);
        }
        definitionNodes.push_back(node);
        if (!definitionByIdentity.emplace(
                definition.identity,
                std::make_pair(node, definition.kind)).second) {
            return false;
        }
    }
    if (story.bodyList.state != ObservationState::Value ||
        !story.bodyList.valuePresent) {
        return false;
    }
    const size_t bodyStory = AddNode(
        graph,
        arena,
        NodeKind::Story,
        document,
        effective.definitions.size(),
        StoryLocator(story.bodyList.value),
        StoryIdentityKey(
            graph->nodes[document].id, StoryLocator(story.bodyList.value)));
    if (bodyStory == none) {
        return false;
    }
    graph->nodes[bodyStory].storyNativeList = {
        story.bodyList.state, story.bodyList.value};
    std::vector<size_t> sections;
    sections.reserve(story.sections.size());
    for (const SectionObservation& section : story.sections) {
        const size_t node = AddNode(
            graph,
            arena,
            NodeKind::Section,
            bodyStory,
            section.ordinal,
            {},
            L"section:" + std::to_wstring(section.ordinal));
        if (node == none) {
            return false;
        }
        sections.push_back(node);
    }

    stage(L"body-paragraphs");
    std::vector<size_t> paragraphNodes;
    std::vector<size_t> runNodes;
    std::vector<std::pair<std::wstring, size_t>> allParagraphNodes;
    std::vector<std::pair<std::wstring, size_t>> allRunNodes;
    for (const ParagraphObservation& paragraph : text.paragraphs) {
        if (paragraph.sectionOrdinal >= sections.size() ||
            !ValidParagraphRuns(paragraph)) {
            if (failure != nullptr) {
                *failure = L"graph:body-paragraph-invalid:" +
                    std::to_wstring(
                        static_cast<size_t>(
                            &paragraph - text.paragraphs.data())) + L":" +
                    std::to_wstring(paragraph.sectionOrdinal) + L":" +
                    std::to_wstring(paragraph.runs.size());
            }
            return false;
        }
        const size_t parent = sections[static_cast<size_t>(paragraph.sectionOrdinal)];
        const size_t paragraphNode = AddParagraphNode(
            graph,
            arena,
            parent,
            graph->nodes[parent].children.size(),
            graph->nodes[bodyStory].id,
            paragraph.start);
        if (paragraphNode == none) {
            stage(L"body-paragraph-node");
            return false;
        }
        paragraphNodes.push_back(paragraphNode);
        allParagraphNodes.push_back({
            std::to_wstring(paragraph.start.list) + L":" +
                std::to_wstring(paragraph.start.paragraph),
            paragraphNode});
        for (const RunObservation& run : paragraph.runs) {
            const size_t runNode = AddNode(
                graph,
                arena,
                NodeKind::CharacterRun,
                paragraphNode,
                graph->nodes[paragraphNode].children.size(),
                RunLocator(run));
            if (runNode == none) {
                stage(L"body-run-node");
                return false;
            }
            runNodes.push_back(runNode);
            allRunNodes.push_back({
                std::to_wstring(run.start.list) + L":" +
                    std::to_wstring(run.start.paragraph) + L":" +
                    std::to_wstring(run.start.character) + L":" +
                    std::to_wstring(run.end.character),
                runNode});
            graph->blobs.push_back(MakeBlob(
                Utf16Value(run.text),
                static_cast<std::uint8_t>(0x40 + graph->blobs.size())));
            Node& node = graph->nodes[runNode];
            Bytes range = UuidValue(graph->nodes[paragraphNode].id);
            Add(&range, codec::Sint64(run.start.character));
            Add(&range, codec::Sint64(run.end.character));
            const RecordBlob& blob = graph->blobs.back();
            const Bytes slice = codec::BlobSliceValue(
                blob.id,
                0,
                blob.bytes.size(),
                codec::Hash(codec::View(blob.bytes)));
            AppendField(&node.payload, {100, kFieldFlagRequired,
                                        ScalarTag::Struct, 1, range});
            AppendField(&node.payload, {101, kFieldFlagRequired,
                                        ScalarTag::BlobSlice, 1,
                                        Observe(ObservationState::Value,
                                                slice)});
            // Field 102 is appended after property IDs are assigned.
        }
    }

    std::map<std::pair<std::int64_t, std::int64_t>, size_t>
        anchorOnlyParagraphs;
    const auto paragraphFor = [&](const NativePosition& anchor) -> size_t {
        for (size_t index = 0; index < text.paragraphs.size(); ++index) {
            if (SameParagraph(text.paragraphs[index].start, anchor)) {
                return paragraphNodes[index];
            }
        }
        const auto key = std::make_pair(anchor.list, anchor.paragraph);
        const auto known = anchorOnlyParagraphs.find(key);
        if (known != anchorOnlyParagraphs.end()) {
            return known->second;
        }
        const size_t parent = sections.front();
        const size_t node = AddParagraphNode(
            graph,
            arena,
            parent,
            graph->nodes[parent].children.size(),
            graph->nodes[bodyStory].id,
            {anchor.list, anchor.paragraph, 0});
        if (node != none) {
            anchorOnlyParagraphs.emplace(key, node);
            allParagraphNodes.push_back({
                std::to_wstring(anchor.list) + L":" +
                    std::to_wstring(anchor.paragraph),
                node});
        }
        return node;
    };

    if (controls.controls.size() != story.controls.size() ||
        std::any_of(
            controls.controls.begin(), controls.controls.end(),
            [](const ControlObservation& control) {
                return !control.instanceIdPresent &&
                    !control.instanceId.empty();
            }) ||
        std::any_of(
            tables.tables.begin(), tables.tables.end(),
            [](const TableObservation& table) {
                return !table.instanceIdPresent && !table.instanceId.empty();
            }) ||
        std::any_of(
            images.images.begin(), images.images.end(),
            [](const ImageObservation& image) {
                return !image.instanceIdPresent && !image.instanceId.empty();
            })) {
        return false;
    }
    for (const ControlObservation& adapted : controls.controls) {
        if (adapted.anchorState != ObservationState::Value) {
            return false;
        }
        const bool present = std::any_of(
            story.controls.begin(), story.controls.end(),
            [&adapted](const ControlObservation& native) {
                return native.ctrlId == adapted.ctrlId &&
                    native.instanceIdPresent == adapted.instanceIdPresent &&
                    native.instanceId == adapted.instanceId &&
                    native.headCtrlOrdinal == adapted.headCtrlOrdinal &&
                    SameAnchor(native.anchor, adapted.anchor);
            });
        if (!present) {
            return false;
        }
    }
    stage(L"controls");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedGraphControlFamily,
        controls.controls.size(), graph->nodes.size());
    std::vector<size_t> controlNodes;
    std::map<std::wstring, std::uint64_t> controlOccurrences;
    for (const ControlObservation& control : controls.controls) {
        const bool capturedImage = std::any_of(
            images.images.begin(), images.images.end(),
            [&control](const ImageObservation& image) {
                return image.ctrlId == control.ctrlId &&
                    image.headCtrlOrdinal == control.headCtrlOrdinal &&
                    SameAnchor(image.anchor, control.anchor);
            });
        if (control.ctrlId == L"tbl" || control.ctrlId == L"$pic" ||
            capturedImage) {
            continue;
        }
        const size_t parent = paragraphFor(control.anchor);
        if (parent == none) {
            return false;
        }
        const Bytes locator = ControlLocator(
            control.ctrlId,
            control.instanceIdPresent,
            control.instanceId,
            control.headCtrlOrdinal,
            control.anchor);
        const size_t matchingInstances = static_cast<size_t>(std::count_if(
            controls.controls.begin(), controls.controls.end(),
            [&control](const ControlObservation& candidate) {
                return candidate.ctrlId != L"tbl" &&
                    candidate.ctrlId != L"$pic" &&
                    candidate.ctrlId == control.ctrlId &&
                    candidate.instanceIdPresent == control.instanceIdPresent &&
                    (!control.instanceIdPresent ||
                     candidate.instanceId == control.instanceId);
            }));
        const std::wstring occurrenceFamily = ControlCompleteKey(
            NodeKind::GenericControl, control.ctrlId,
            control.instanceIdPresent, control.instanceId);
        const std::uint64_t occurrence =
            controlOccurrences[occurrenceFamily]++;
        const std::wstring stableKey = matchingInstances == 1
            ? L"control:" + occurrenceFamily
            : ControlOccurrenceKey(
                  NodeKind::GenericControl, control.ctrlId,
                  control.instanceIdPresent, control.instanceId, occurrence);
        const size_t node = AddNode(
            graph,
            arena,
            NodeKind::GenericControl,
            parent,
            graph->nodes[parent].children.size(),
            locator,
            stableKey);
        if (node == none) {
            return false;
        }
        graph->nodes[node].payload = ControlPayload(
            graph->nodes[node],
            control.ctrlId,
            control.instanceIdPresent,
            control.instanceId,
            control.headCtrlOrdinal,
            control.anchor);
        graph->nodes[node].controlFamily = true;
        graph->nodes[node].anchor = control.anchor;
        graph->nodes[node].headCtrlOrdinal = control.headCtrlOrdinal;
        graph->nodes[node].edges.push_back(
            {EdgeKind::Anchors, parent, 0, 0});
        controlNodes.push_back(node);
    }

    stage(L"root-tables");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedGraphTableFamilies,
        tables.tables.size(), graph->nodes.size());
    std::vector<size_t> tableNodes(tables.tables.size(), none);
    for (size_t index = 0; index < tables.tables.size(); ++index) {
        const TableObservation& table = tables.tables[index];
        if (table.anchorState != ObservationState::Value) {
            return false;
        }
        if (table.hostTableInstanceIdPresent) {
            continue;
        }
        const size_t parent = paragraphFor(table.anchor);
        if (parent == none) {
            return false;
        }
        const size_t matchingInstances = static_cast<size_t>(std::count_if(
            tables.tables.begin(), tables.tables.end(),
            [&table](const TableObservation& candidate) {
                return candidate.instanceIdPresent == table.instanceIdPresent &&
                    candidate.instanceId == table.instanceId;
            }));
        const Bytes locator = ControlLocator(
            L"tbl", table.instanceIdPresent, table.instanceId,
            table.headCtrlOrdinal, table.anchor);
        NodeId ownerScope;
        if (!TableOwnerScopeId(*graph, parent, &ownerScope)) return false;
        const std::wstring stableKey = TableStableKey(
            ownerScope, table.instanceIdPresent, table.instanceId,
            locator, matchingInstances);
        const size_t node = AddNode(
            graph,
            arena,
            NodeKind::Table,
            parent,
            graph->nodes[parent].children.size(),
            locator,
            stableKey);
        if (node == none) {
            return false;
        }
        tableNodes[index] = node;
        graph->nodes[node].controlFamily = true;
        graph->nodes[node].anchor = table.anchor;
        graph->nodes[node].headCtrlOrdinal = table.headCtrlOrdinal;
        graph->nodes[node].edges.push_back(
            {EdgeKind::Anchors, parent, 0, 0});
        for (const PropertyObservation& property : table.properties) {
            AddProperty(&graph->nodes[node], property);
        }
    }

    std::vector<std::vector<size_t>> cellNodes(tables.tables.size());
    const auto emitCells = [&](const size_t tableIndex,
                               const size_t tableNode) -> bool {
        const TableObservation& table = tables.tables[tableIndex];
        for (size_t cellIndex = 0; cellIndex < table.cells.size(); ++cellIndex) {
            const CellObservation& cell = table.cells[cellIndex];
            if (cell.row1 == 0 || cell.column1 == 0 ||
                cell.rowSpan == 0 || cell.columnSpan == 0) {
                return false;
            }
            const size_t cellNode = AddNode(
                graph,
                arena,
                NodeKind::TableCell,
                tableNode,
                cellIndex,
                CellLocator(graph->nodes[tableNode].id, cell),
                L"cell:" + Hex(UuidValue(graph->nodes[tableNode].id)) +
                    L":" + cell.address);
            if (cellNode == none) {
                return false;
            }
            cellNodes[tableIndex].push_back(cellNode);
            Node& node = graph->nodes[cellNode];
            AppendField(&node.payload, {100, kFieldFlagRequired,
                                        ScalarTag::UUID128, 1,
                                        UuidValue(graph->nodes[tableNode].id)});
            AppendField(&node.payload, {101, kFieldFlagRequired,
                                        ScalarTag::UTF16, 1,
                                        Observe(ObservationState::Value,
                                                Utf16Value(cell.address))});
            AppendField(&node.payload, {102, kFieldFlagRequired,
                                        ScalarTag::Uint64, 1,
                                        Observe(ObservationState::Value,
                                                codec::Uint64(cell.row1))});
            AppendField(&node.payload, {103, kFieldFlagRequired,
                                        ScalarTag::Uint64, 1,
                                        Observe(ObservationState::Value,
                                                codec::Uint64(cell.column1))});
            AppendField(&node.payload, {104, kFieldFlagRequired,
                                        ScalarTag::Uint64, 1,
                                        Observe(ObservationState::Value,
                                                codec::Uint64(cell.rowSpan))});
            AppendField(&node.payload, {105, kFieldFlagRequired,
                                        ScalarTag::Uint64, 1,
                                        Observe(ObservationState::Value,
                                                codec::Uint64(cell.columnSpan))});
            AppendField(&node.payload, {106, kFieldFlagRequired,
                                        ScalarTag::Sint64, 1,
                                        ObserveScalar(
                                            {cell.listState, cell.listId})});
            for (const PropertyObservation& property : cell.properties) {
                AddProperty(&node, property);
            }
            // Story reference and remaining fields are appended after any
            // nested ownership subtree is created.
        }
        return true;
    };

    for (size_t index = 0; index < tables.tables.size(); ++index) {
        if (tableNodes[index] != none && !emitCells(index, tableNodes[index])) {
            return false;
        }
    }

    std::vector<std::vector<size_t>> cellStoryNodes(tables.tables.size());
    std::vector<std::vector<size_t>> cellParagraphNodes(tables.tables.size());
    for (size_t table = 0; table < tables.tables.size(); ++table) {
        cellStoryNodes[table].assign(tables.tables[table].cells.size(), none);
        cellParagraphNodes[table].assign(tables.tables[table].cells.size(), none);
    }
    const auto emitCellStory = [&](const size_t tableIndex,
                                   const size_t cellIndex) -> bool {
        const CellObservation& cell = tables.tables[tableIndex].cells[cellIndex];
        if (cellIndex >= cellNodes[tableIndex].size() ||
            cell.listState != ObservationState::Value || cell.listId <= 0) {
            return false;
        }
        const size_t cellNode = cellNodes[tableIndex][cellIndex];
        const size_t storyNode = AddNode(
            graph, arena, NodeKind::Story, cellNode, 0,
            StoryLocator(cell.listId),
            StoryIdentityKey(
                graph->nodes[cellNode].id, StoryLocator(cell.listId)));
        if (storyNode == none) {
            return false;
        }
        graph->nodes[storyNode].storyNativeList = {
            cell.listState, cell.listId};
        cellStoryNodes[tableIndex][cellIndex] = storyNode;
        graph->nodes[storyNode].edges.push_back(
            {EdgeKind::OwnerStory, cellNode, 0, 0});
        AppendField(&graph->nodes[cellNode].payload,
                    {107, kFieldFlagRequired, ScalarTag::UUID128, 1,
                     Observe(ObservationState::Value,
                             UuidValue(graph->nodes[storyNode].id))});
        const std::vector<ParagraphObservation>& paragraphs = cell.paragraphs;
        if (paragraphs.empty()) {
            const NativePosition start{cell.listId, 0, 0};
            const size_t paragraphNode = AddParagraphNode(
                graph, arena, storyNode, 0,
                graph->nodes[storyNode].id, start);
            if (paragraphNode == none) return false;
            cellParagraphNodes[tableIndex][cellIndex] = paragraphNode;
            allParagraphNodes.push_back({
                std::to_wstring(start.list) + L":0", paragraphNode});
        }
        for (size_t paragraphIndex = 0;
             paragraphIndex < paragraphs.size(); ++paragraphIndex) {
            const ParagraphObservation& paragraph = paragraphs[paragraphIndex];
            if (!ValidParagraphRuns(paragraph)) {
                return false;
            }
            const size_t paragraphNode = AddParagraphNode(
                graph, arena, storyNode, paragraphIndex,
                graph->nodes[storyNode].id, paragraph.start);
            if (paragraphNode == none) {
                return false;
            }
            if (paragraphIndex == 0) {
                cellParagraphNodes[tableIndex][cellIndex] = paragraphNode;
            }
            allParagraphNodes.push_back({
                std::to_wstring(paragraph.start.list) + L":" +
                    std::to_wstring(paragraph.start.paragraph),
                paragraphNode});
            for (const RunObservation& run : paragraph.runs) {
                const size_t runNode = AddNode(
                    graph, arena, NodeKind::CharacterRun, paragraphNode,
                    graph->nodes[paragraphNode].children.size(),
                    RunLocator(run));
                if (runNode == none) {
                    return false;
                }
                allRunNodes.push_back({
                    std::to_wstring(run.start.list) + L":" +
                        std::to_wstring(run.start.paragraph) + L":" +
                        std::to_wstring(run.start.character) + L":" +
                        std::to_wstring(run.end.character),
                    runNode});
                graph->blobs.push_back(MakeBlob(
                    Utf16Value(run.text),
                    static_cast<std::uint8_t>(0x40 + graph->blobs.size())));
                Node& node = graph->nodes[runNode];
                Bytes range = UuidValue(graph->nodes[paragraphNode].id);
                Add(&range, codec::Sint64(run.start.character));
                Add(&range, codec::Sint64(run.end.character));
                const RecordBlob& blob = graph->blobs.back();
                const Bytes slice = codec::BlobSliceValue(
                    blob.id, 0, blob.bytes.size(),
                    codec::Hash(codec::View(blob.bytes)));
                AppendField(&node.payload,
                            {100, kFieldFlagRequired, ScalarTag::Struct, 1,
                             range});
                AppendField(&node.payload,
                            {101, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
                             Observe(ObservationState::Value, slice)});
            }
        }
        return true;
    };

    // Root-table cells own their real list stories before nested ownership
    // is resolved. Empty text is still an observed Value, never fabricated.
    for (size_t table = 0; table < tables.tables.size(); ++table) {
        if (tableNodes[table] == none) {
            continue;
        }
        for (size_t cell = 0; cell < tables.tables[table].cells.size(); ++cell) {
            if (!emitCellStory(table, cell)) {
                return false;
            }
        }
    }

    stage(L"nested-tables");
    // Attach nested tables to the already observed host-cell paragraph, then
    // emit their independently captured physical cells and list stories.
    for (size_t index = 0; index < tables.tables.size(); ++index) {
        const TableObservation& nested = tables.tables[index];
        if (!nested.hostTableInstanceIdPresent) {
            continue;
        }
        size_t outerIndex = none;
        size_t hostOffset = none;
        for (size_t candidate = 0; candidate < tables.tables.size(); ++candidate) {
            if (tables.tables[candidate].instanceId !=
                nested.hostTableInstanceId) {
                continue;
            }
            outerIndex = candidate;
            for (size_t cell = 0;
                 cell < tables.tables[candidate].cells.size(); ++cell) {
                if (tables.tables[candidate].cells[cell].address ==
                    nested.hostCellAddress) {
                    hostOffset = cell;
                }
            }
        }
        if (outerIndex == none || hostOffset == none ||
            cellParagraphNodes[outerIndex][hostOffset] == none) {
            if (failure != nullptr) {
                *failure = L"graph:nested-host:" + std::to_wstring(index) +
                    L":" + std::to_wstring(outerIndex) + L":" +
                    std::to_wstring(hostOffset);
            }
            return false;
        }
        const size_t parent = cellParagraphNodes[outerIndex][hostOffset];
        const size_t matchingInstances = static_cast<size_t>(std::count_if(
            tables.tables.begin(), tables.tables.end(),
            [&nested](const TableObservation& candidate) {
                return candidate.instanceIdPresent == nested.instanceIdPresent &&
                    candidate.instanceId == nested.instanceId;
            }));
        const Bytes locator = ControlLocator(
            L"tbl", nested.instanceIdPresent, nested.instanceId,
            nested.headCtrlOrdinal, nested.anchor);
        NodeId ownerScope;
        if (!TableOwnerScopeId(*graph, parent, &ownerScope)) {
            stage(L"nested-owner-scope");
            return false;
        }
        const std::wstring stableKey = TableStableKey(
            ownerScope, nested.instanceIdPresent, nested.instanceId,
            locator, matchingInstances);
        const size_t nestedNode = AddNode(
            graph, arena, NodeKind::Table, parent,
            graph->nodes[parent].children.size(), locator, stableKey);
        if (nestedNode == none) {
            stage(L"nested-node");
            return false;
        }
        tableNodes[index] = nestedNode;
        graph->nodes[nestedNode].controlFamily = true;
        graph->nodes[nestedNode].anchor = nested.anchor;
        graph->nodes[nestedNode].headCtrlOrdinal = nested.headCtrlOrdinal;
        graph->nodes[nestedNode].edges.push_back(
            {EdgeKind::Anchors, parent, 0, 0});
        for (const PropertyObservation& property : nested.properties) {
            AddProperty(&graph->nodes[nestedNode], property);
        }
        if (!emitCells(index, nestedNode)) {
            stage(L"nested-cells");
            return false;
        }
        for (size_t cell = 0; cell < nested.cells.size(); ++cell) {
            if (!emitCellStory(index, cell)) {
                stage(L"nested-cell-story");
                return false;
            }
        }
    }

    stage(L"table-cell-availability");
    // A missing list story is an unavailable native fact, never NotApplicable.
    for (Node& node : graph->nodes) {
        if (node.kind != NodeKind::TableCell) {
            continue;
        }
        bool hasStoryField = false;
        // Host cells have children; no other legal child exists here.
        hasStoryField = !node.children.empty();
        if (!hasStoryField) {
            AppendField(&node.payload, {107, kFieldFlagRequired,
                                        ScalarTag::UUID128, 1,
                                        Observe(ObservationState::NotExposed)});
        }
        AppendField(&node.payload, {108, kFieldFlagRequired,
                                    ScalarTag::Struct, 1,
                                    Observe(ObservationState::NotExposed)});
        // Field 109 is appended after record IDs are assigned.
    }

    stage(L"table-captions");
    for (size_t tableIndex = 0; tableIndex < tables.tables.size(); ++tableIndex) {
        const TableObservation& table = tables.tables[tableIndex];
        if (table.captionPresent.state != ObservationState::Value ||
            table.captionPresent.value == 0) {
            continue;
        }
        const size_t tableNode = tableNodes[tableIndex];
        if (tableNode == none ||
            table.captionList.state != ObservationState::Value ||
            table.captionList.value <= 0 ||
            table.captionStart.list != table.captionList.value ||
            table.captionStart.paragraph < 0 ||
            table.captionStart.character < 0) {
            return false;
        }
        const Bytes captionLocator = StoryLocator(table.captionList.value);
        const size_t storyNode = AddNode(
            graph, arena, NodeKind::Story, tableNode,
            graph->nodes[tableNode].children.size(), captionLocator,
            StoryIdentityKey(graph->nodes[tableNode].id, captionLocator));
        if (storyNode == none) return false;
        Node& captionStoryNode = graph->nodes[storyNode];
        captionStoryNode.storyNativeList = table.captionList;
        captionStoryNode.captionAutomaticNumber = table.captionAutomaticNumber;
        captionStoryNode.captionStyleId = table.captionStyleId;
        captionStoryNode.captionStyleName = table.captionStyleName;
        captionStoryNode.captionPageStart = table.captionPageStart;
        captionStoryNode.captionPageEnd = table.captionPageEnd;
        captionStoryNode.edges.push_back(
            {EdgeKind::CaptionOf, tableNode, 0, 0});
        captionStoryNode.edges.push_back(
            {EdgeKind::OwnerStory, tableNode, 0, 0});

        std::vector<std::wstring> paragraphs;
        if (table.captionText.state == ObservationState::Value) {
            size_t begin = 0;
            for (size_t cursor = 0; cursor < table.captionText.value.size();) {
                if (table.captionText.value[cursor] != L'\r' &&
                    table.captionText.value[cursor] != L'\n') {
                    ++cursor;
                    continue;
                }
                size_t end = cursor + 1;
                if (table.captionText.value[cursor] == L'\r' &&
                    end < table.captionText.value.size() &&
                    table.captionText.value[end] == L'\n') ++end;
                paragraphs.push_back(
                    table.captionText.value.substr(begin, end - begin));
                begin = end;
                cursor = end;
            }
            paragraphs.push_back(table.captionText.value.substr(begin));
        }
        for (size_t paragraphIndex = 0;
             paragraphIndex < paragraphs.size(); ++paragraphIndex) {
            NativePosition start{
                table.captionStart.list,
                table.captionStart.paragraph +
                    static_cast<std::int64_t>(paragraphIndex),
                paragraphIndex == 0 ? table.captionStart.character : 0};
            const size_t paragraphNode = AddParagraphNode(
                graph, arena, storyNode, paragraphIndex,
                graph->nodes[storyNode].id, start);
            if (paragraphNode == none) return false;
            allParagraphNodes.push_back({
                std::to_wstring(start.list) + L":" +
                    std::to_wstring(start.paragraph), paragraphNode});

            const std::wstring& captionTextChunk = paragraphs[paragraphIndex];
            if (captionTextChunk.empty()) continue;
            RunObservation captionRun;
            captionRun.start = start;
            captionRun.end = {
                start.list, start.paragraph,
                start.character +
                    static_cast<std::int64_t>(captionTextChunk.size())};
            captionRun.text = captionTextChunk;
            const size_t runNode = AddNode(
                graph, arena, NodeKind::CharacterRun, paragraphNode, 0,
                RunLocator(captionRun));
            if (runNode == none) return false;
            allRunNodes.push_back({
                std::to_wstring(start.list) + L":" +
                    std::to_wstring(start.paragraph) + L":" +
                    std::to_wstring(start.character) + L":" +
                    std::to_wstring(captionRun.end.character), runNode});
            graph->blobs.push_back(MakeBlob(
                Utf16Value(captionTextChunk),
                static_cast<std::uint8_t>(0x40 + graph->blobs.size())));
            Node& run = graph->nodes[runNode];
            Bytes range = UuidValue(graph->nodes[paragraphNode].id);
            Add(&range, codec::Sint64(start.character));
            Add(&range, codec::Sint64(captionRun.end.character));
            const RecordBlob& blob = graph->blobs.back();
            const Bytes slice = codec::BlobSliceValue(
                blob.id, 0, blob.bytes.size(),
                codec::Hash(codec::View(blob.bytes)));
            AppendField(&run.payload,
                        {100, kFieldFlagRequired, ScalarTag::Struct, 1, range});
            AppendField(&run.payload,
                        {101, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
                         Observe(table.captionText.state, slice)});

        }
    }

    stage(L"images");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedGraphImageFamily,
        images.images.size(), graph->nodes.size());
    std::vector<size_t> imageNodes;
    std::map<std::wstring, std::uint64_t> imageOccurrences;
    for (const ImageObservation& image : images.images) {
        if (image.anchorState != ObservationState::Value) {
            return false;
        }
        const size_t parent = paragraphFor(image.anchor);
        if (parent == none) {
            return false;
        }
        const size_t matchingInstances = static_cast<size_t>(std::count_if(
            images.images.begin(), images.images.end(),
            [&image](const ImageObservation& candidate) {
                return candidate.ctrlId == image.ctrlId &&
                    candidate.instanceId == image.instanceId;
            }));
        const std::wstring stableKey = image.instanceIdPresent &&
                matchingInstances == 1
            ? L"control:" + ControlCompleteKey(
                  NodeKind::Image, image.ctrlId, image.instanceIdPresent,
                  image.instanceId)
            : ControlOccurrenceKey(
                  NodeKind::Image, image.ctrlId, image.instanceIdPresent,
                  image.instanceId, imageOccurrences[
                      Hex(Utf16Value(image.ctrlId)) + L":" +
                      Hex(Utf16Value(image.instanceId))]++);
        const size_t node = AddNode(
            graph,
            arena,
            NodeKind::Image,
            parent,
            graph->nodes[parent].children.size(),
            ControlLocator(image.ctrlId, image.instanceIdPresent,
                           image.instanceId, image.headCtrlOrdinal,
                           image.anchor),
            stableKey);
        if (node == none) {
            return false;
        }
        graph->nodes[node].controlFamily = true;
        graph->nodes[node].anchor = image.anchor;
        graph->nodes[node].headCtrlOrdinal = image.headCtrlOrdinal;
        graph->nodes[node].edges.push_back(
            {EdgeKind::Anchors, parent, 0, 0});
        for (const PropertyObservation& property : image.properties) {
            AddProperty(&graph->nodes[node], property);
        }
        if (image.text[2].state == ObservationState::Value) {
            const bool nativeListPresent =
                image.captionList.state == ObservationState::Value;
            if (image.captionList.state == ObservationState::NotRequested ||
                image.captionList.state == ObservationState::NotApplicable ||
                (nativeListPresent &&
                 (!IsQualifiedNativeCaptionLocation(arena, image) ||
                  image.captionList.value <= 0 ||
                  image.captionStart.list != image.captionList.value ||
                  image.captionStart.paragraph < 0 ||
                  image.captionStart.character < 0))) {
                return false;
            }
            const Bytes captionLocator = nativeListPresent
                ? StoryLocator(image.captionList.value)
                : Bytes{};
            const size_t storyNode = AddNode(
                graph, arena, NodeKind::Story, node, 0,
                captionLocator,
                StoryIdentityKey(graph->nodes[node].id, captionLocator));
            if (storyNode == none) {
                return false;
            }
            graph->nodes[storyNode].storyNativeList = image.captionList;
            if (!nativeListPresent) {
                graph->nodes[storyNode].locatorUnavailableState =
                    image.captionList.state;
            }
            graph->nodes[storyNode].edges.push_back(
                {EdgeKind::CaptionOf, node, 0, 0});
            graph->nodes[storyNode].edges.push_back(
                {EdgeKind::OwnerStory, node, 0, 0});
            if (nativeListPresent) {
                const std::int64_t captionList = image.captionList.value;
                const NativePosition start = image.captionStart;
                const size_t paragraphNode = AddParagraphNode(
                    graph, arena, storyNode, 0,
                    graph->nodes[storyNode].id, start);
                if (paragraphNode == none) {
                    return false;
                }
                if (image.text[2].value.empty()) {
                    imageNodes.push_back(node);
                    continue;
                }
                RunObservation captionRun;
                captionRun.start = start;
                captionRun.end = {
                    captionList, start.paragraph,
                    start.character +
                        static_cast<std::int64_t>(image.text[2].value.size())};
                captionRun.text = image.text[2].value;
                const size_t runNode = AddNode(
                    graph, arena, NodeKind::CharacterRun, paragraphNode, 0,
                    RunLocator(captionRun));
                if (runNode == none) {
                    return false;
                }
                allParagraphNodes.push_back({
                    std::to_wstring(start.list) + L":" +
                        std::to_wstring(start.paragraph), paragraphNode});
                allRunNodes.push_back({
                    std::to_wstring(captionRun.start.list) + L":" +
                        std::to_wstring(captionRun.start.paragraph) + L":" +
                        std::to_wstring(captionRun.start.character) + L":" +
                        std::to_wstring(captionRun.end.character), runNode});
                graph->blobs.push_back(MakeBlob(
                    Utf16Value(captionRun.text),
                    static_cast<std::uint8_t>(0x40 + graph->blobs.size())));
                Node& run = graph->nodes[runNode];
                Bytes range = UuidValue(graph->nodes[paragraphNode].id);
                Add(&range, codec::Sint64(0));
                Add(&range, codec::Sint64(captionRun.end.character));
                const RecordBlob& blob = graph->blobs.back();
                const Bytes slice = codec::BlobSliceValue(
                    blob.id, 0, blob.bytes.size(),
                    codec::Hash(codec::View(blob.bytes)));
                AppendField(&run.payload,
                            {100, kFieldFlagRequired, ScalarTag::Struct, 1,
                             range});
                AppendField(&run.payload,
                            {101, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
                             Observe(ObservationState::Value, slice)});
            }
        }
        imageNodes.push_back(node);
    }

    std::map<std::pair<PropertyTarget, std::wstring>, size_t>
        propertyOwnerByIdentity;
    const auto indexPropertyOwner = [&propertyOwnerByIdentity, none](
        const PropertyTarget target, std::wstring identity,
        const size_t node) {
        const auto key = std::make_pair(target, std::move(identity));
        const auto [known, inserted] =
            propertyOwnerByIdentity.emplace(key, node);
        if (!inserted && known->second != node) known->second = none;
    };
    size_t controlNode = 0;
    for (const ControlObservation& control : controls.controls) {
        const bool capturedImage = std::any_of(
            images.images.begin(), images.images.end(),
            [&control](const ImageObservation& image) {
                return image.ctrlId == control.ctrlId &&
                    image.headCtrlOrdinal == control.headCtrlOrdinal &&
                    SameAnchor(image.anchor, control.anchor);
            });
        if (control.ctrlId == L"tbl" || control.ctrlId == L"$pic" ||
            capturedImage) {
            continue;
        }
        if (controlNode >= controlNodes.size()) return false;
        const size_t node = controlNodes[controlNode++];
        indexPropertyOwner(
            PropertyTarget::Control,
            NativeSiteIdentity(
                PropertyTarget::Control, control.ctrlId,
                control.headCtrlOrdinal, control.anchor,
                control.instanceIdPresent, control.instanceId),
            node);
        if (control.instanceIdPresent && !control.instanceId.empty()) {
            indexPropertyOwner(
                PropertyTarget::Control, control.instanceId, node);
        }
    }
    for (size_t tableIndex = 0;
         tableIndex < tables.tables.size(); ++tableIndex) {
        const TableObservation& table = tables.tables[tableIndex];
        const size_t tableNode = tableNodes[tableIndex];
        indexPropertyOwner(
            PropertyTarget::Table,
            NativeSiteIdentity(
                PropertyTarget::Table, L"tbl", table.headCtrlOrdinal,
                table.anchor, table.instanceIdPresent, table.instanceId),
            tableNode);
        if (table.instanceIdPresent && !table.instanceId.empty()) {
            indexPropertyOwner(
                PropertyTarget::Table, table.instanceId, tableNode);
        }
        for (size_t cellIndex = 0;
             cellIndex < table.cells.size(); ++cellIndex) {
            if (cellIndex >= cellNodes[tableIndex].size()) return false;
            const CellObservation& cell = table.cells[cellIndex];
            const size_t cellNode = cellNodes[tableIndex][cellIndex];
            const std::wstring exact = NativeSiteIdentity(
                PropertyTarget::Cell, L"tbl", table.headCtrlOrdinal,
                {cell.listId, 0, 0}, table.instanceIdPresent,
                table.instanceId) + L":" + cell.address;
            indexPropertyOwner(PropertyTarget::Cell, exact, cellNode);
            if (table.instanceIdPresent && !table.instanceId.empty()) {
                indexPropertyOwner(
                    PropertyTarget::Cell,
                    table.instanceId + L":" + cell.address, cellNode);
            }
        }
    }

    const auto resolveTargetNode =
        [&](const PropertyObservation& property) -> size_t {
        if (property.target == PropertyTarget::Document) {
            return document;
        }
        if (property.target == PropertyTarget::Story) {
            for (size_t node = 0; node < graph->nodes.size(); ++node) {
                if (graph->nodes[node].kind != NodeKind::Story ||
                    graph->nodes[node].locator.size() != 16) {
                    continue;
                }
                std::uint64_t list = 0;
                for (size_t byte = 0; byte < 8; ++byte) {
                    list |= static_cast<std::uint64_t>(
                        graph->nodes[node].locator[8 + byte]) << (byte * 8);
                }
                if (property.targetIdentity ==
                    std::to_wstring(static_cast<std::int64_t>(list))) {
                    return node;
                }
            }
        }
        if (property.target == PropertyTarget::Section) {
            for (size_t index = 0; index < story.sections.size(); ++index) {
                if (property.targetIdentity ==
                    std::to_wstring(story.sections[index].ordinal)) {
                    return sections[index];
                }
            }
        }
        if (property.target == PropertyTarget::Definition) {
            for (size_t index = 0; index < effective.definitions.size(); ++index) {
                if (effective.definitions[index].identity ==
                    property.targetIdentity) {
                    return definitionNodes[index];
                }
            }
        }
        if (property.target == PropertyTarget::Run) {
            if (property.targetIdentity.empty()) {
                return runNodes.empty() ? none : runNodes.front();
            }
            for (const auto& entry : allRunNodes) {
                if (entry.first == property.targetIdentity) {
                    return entry.second;
                }
            }
        }
        if (property.target == PropertyTarget::Paragraph) {
            for (const auto& entry : allParagraphNodes) {
                if (entry.first == property.targetIdentity) {
                    return entry.second;
                }
            }
            return none;
        }

        size_t resolved = none;
        size_t matches = 0;
        const auto accept = [&](const bool matched, const size_t node) {
            if (!matched || node == none) return;
            ++matches;
            resolved = node;
        };
        if (property.target == PropertyTarget::Control) {
            size_t nodeIndex = 0;
            for (const ControlObservation& control : controls.controls) {
                const bool capturedImage = std::any_of(
                    images.images.begin(), images.images.end(),
                    [&control](const ImageObservation& image) {
                        return image.ctrlId == control.ctrlId &&
                            image.headCtrlOrdinal == control.headCtrlOrdinal &&
                            SameAnchor(image.anchor, control.anchor);
                    });
                if (control.ctrlId == L"tbl" || control.ctrlId == L"$pic" ||
                    capturedImage) {
                    continue;
                }
                if (nodeIndex >= controlNodes.size()) return none;
                const bool uniqueLegacy = control.instanceIdPresent &&
                    std::count_if(
                        controls.controls.begin(), controls.controls.end(),
                        [&control](const ControlObservation& candidate) {
                            return candidate.ctrlId == control.ctrlId &&
                                candidate.instanceIdPresent &&
                                candidate.instanceId == control.instanceId;
                        }) == 1;
                accept(
                    NativeSiteIdentity(
                        PropertyTarget::Control, control.ctrlId,
                        control.headCtrlOrdinal, control.anchor,
                        control.instanceIdPresent, control.instanceId) ==
                            property.targetIdentity ||
                    (uniqueLegacy && !control.instanceId.empty() &&
                     control.instanceId == property.targetIdentity),
                    controlNodes[nodeIndex]);
                ++nodeIndex;
            }
        } else if (property.target == PropertyTarget::Table) {
            for (size_t index = 0; index < tables.tables.size(); ++index) {
                const TableObservation& table = tables.tables[index];
                const bool uniqueLegacy = table.instanceIdPresent &&
                    std::count_if(
                        tables.tables.begin(), tables.tables.end(),
                        [&table](const TableObservation& candidate) {
                            return candidate.instanceIdPresent &&
                                candidate.instanceId == table.instanceId;
                        }) == 1;
                accept(
                    NativeSiteIdentity(
                        PropertyTarget::Table, L"tbl",
                        table.headCtrlOrdinal, table.anchor,
                        table.instanceIdPresent, table.instanceId) ==
                            property.targetIdentity ||
                    (uniqueLegacy && !table.instanceId.empty() &&
                     table.instanceId == property.targetIdentity),
                    tableNodes[index]);
            }
        } else if (property.target == PropertyTarget::Cell) {
            for (size_t tableIndex = 0;
                 tableIndex < tables.tables.size(); ++tableIndex) {
                const TableObservation& table = tables.tables[tableIndex];
                const bool uniqueLegacy = table.instanceIdPresent &&
                    std::count_if(
                        tables.tables.begin(), tables.tables.end(),
                        [&table](const TableObservation& candidate) {
                            return candidate.instanceIdPresent &&
                                candidate.instanceId == table.instanceId;
                        }) == 1;
                for (size_t cellIndex = 0;
                     cellIndex < table.cells.size(); ++cellIndex) {
                    const CellObservation& cell = table.cells[cellIndex];
                    if (cellIndex >= cellNodes[tableIndex].size()) return none;
                    const std::wstring exact = NativeSiteIdentity(
                        PropertyTarget::Cell, L"tbl",
                        table.headCtrlOrdinal, {cell.listId, 0, 0},
                        table.instanceIdPresent, table.instanceId) + L":" +
                        cell.address;
                    const std::wstring legacy =
                        table.instanceId + L":" + cell.address;
                    accept(
                        exact == property.targetIdentity ||
                        (uniqueLegacy && !table.instanceId.empty() &&
                         legacy == property.targetIdentity),
                        cellNodes[tableIndex][cellIndex]);
                }
            }
        } else if (property.target == PropertyTarget::Image) {
            for (size_t index = 0; index < images.images.size(); ++index) {
                const ImageObservation& image = images.images[index];
                const bool uniqueLegacy = image.instanceIdPresent &&
                    std::count_if(
                        images.images.begin(), images.images.end(),
                        [&image](const ImageObservation& candidate) {
                            return candidate.ctrlId == image.ctrlId &&
                                candidate.instanceIdPresent &&
                                candidate.instanceId == image.instanceId;
                        }) == 1;
                const std::wstring exact = NativeSiteIdentity(
                    PropertyTarget::Image, image.ctrlId,
                    image.headCtrlOrdinal, image.anchor,
                    image.instanceIdPresent, image.instanceId);
                const size_t exactControl = exact.find(
                    L':', exact.find(L':') + 1);
                const size_t targetControl = property.targetIdentity.find(
                    L':', property.targetIdentity.find(L':') + 1);
                const size_t exactInstance = exact.rfind(L':');
                const size_t targetInstance = property.targetIdentity.rfind(L':');
                const bool sameNativeSite =
                    exactControl != std::wstring::npos &&
                    targetControl != std::wstring::npos &&
                    exactInstance > exactControl &&
                    targetInstance > targetControl &&
                    exact.substr(
                        exactControl + 1,
                        exactInstance - exactControl) ==
                    property.targetIdentity.substr(
                        targetControl + 1,
                        targetInstance - targetControl);
                const std::wstring targetRawInstance =
                    targetInstance == std::wstring::npos
                        ? std::wstring{}
                        : property.targetIdentity.substr(targetInstance + 1);
                accept(
                    exact == property.targetIdentity || sameNativeSite ||
                    (uniqueLegacy && !image.instanceId.empty() &&
                     (image.instanceId == property.targetIdentity ||
                      image.instanceId == targetRawInstance)),
                    imageNodes[index]);
            }
            size_t controlNode = 0;
            for (const ControlObservation& control : controls.controls) {
                const bool capturedImage = std::any_of(
                    images.images.begin(), images.images.end(),
                    [&control](const ImageObservation& image) {
                        return image.ctrlId == control.ctrlId &&
                            image.headCtrlOrdinal == control.headCtrlOrdinal &&
                            SameAnchor(image.anchor, control.anchor);
                    });
                if (control.ctrlId == L"tbl" || control.ctrlId == L"$pic" ||
                    capturedImage) {
                    continue;
                }
                const std::wstring exact = NativeSiteIdentity(
                    PropertyTarget::Image, control.ctrlId,
                    control.headCtrlOrdinal, control.anchor,
                    control.instanceIdPresent, control.instanceId);
                const size_t exactInstance = exact.rfind(L':');
                const size_t targetInstance = property.targetIdentity.rfind(L':');
                const bool sameNativeSite = exactInstance != std::wstring::npos &&
                    targetInstance != std::wstring::npos &&
                    exact.substr(0, exactInstance + 1) ==
                        property.targetIdentity.substr(0, targetInstance + 1);
                accept(
                    exact == property.targetIdentity || sameNativeSite,
                    controlNodes[controlNode]);
                ++controlNode;
            }
        }
        return matches == 1 ? resolved : none;
    };
    const auto targetNode = [&](const PropertyObservation& property) -> size_t {
        const auto key = std::make_pair(
            property.target, property.targetIdentity);
        const auto known = propertyOwnerByIdentity.find(key);
        if (known != propertyOwnerByIdentity.end()) return known->second;
        const size_t resolved = resolveTargetNode(property);
        propertyOwnerByIdentity.emplace(std::move(key), resolved);
        return resolved;
    };
    profile.Transition(TypedBuildSubstage::OwnerResolution);
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedOwnerResolution,
        effective.properties.size(), graph->nodes.size());
    stage(L"effective-properties");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedPropertyFamilies,
        effective.properties.size(), graph->nodes.size());
    for (const PropertyObservation& property : effective.properties) {
        const size_t owner = targetNode(property);
        if (owner == none) {
            if (failure != nullptr) {
                *failure = L"graph:effective-owner:" +
                    std::to_wstring(static_cast<size_t>(property.target)) +
                    L":" + std::to_wstring(property.key) + L":" +
                    property.targetIdentity;
            }
            return false;
        }
        AddProperty(&graph->nodes[owner], property);
    }
    stage(L"layout-properties");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedLayoutFamily,
        layout.layoutProperties.size(), graph->nodes.size());
    for (const PropertyObservation& property : layout.layoutProperties) {
        const size_t owner = targetNode(property);
        if (owner == none) {
            if (failure != nullptr) {
                *failure = L"graph:layout-owner:" +
                    std::to_wstring(static_cast<size_t>(
                        &property - layout.layoutProperties.data())) + L":" +
                    property.targetIdentity;
                if (property.target == PropertyTarget::Image) {
                    for (const ImageObservation& image : images.images) {
                        *failure += L"|i=" + NativeSiteIdentity(
                            PropertyTarget::Image, image.ctrlId,
                            image.headCtrlOrdinal, image.anchor,
                            image.instanceIdPresent, image.instanceId);
                    }
                    for (const ControlObservation& control : controls.controls) {
                        *failure += L"|c=" + NativeSiteIdentity(
                            PropertyTarget::Image, control.ctrlId,
                            control.headCtrlOrdinal, control.anchor,
                            control.instanceIdPresent, control.instanceId);
                    }
                }
            }
            return false;
        }
        AddProperty(&graph->nodes[owner], property);
    }
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedLayoutOwnersEnd,
        layout.layoutProperties.size(), propertyOwnerByIdentity.size());
    // This typed capture always advertises the Layout profile (0x1f). There
    // is no no-layout mode in this spool contract: absent/default/false
    // certification is an incomplete capture, never a validation bypass.
    stage(L"layout-validation");
    if (!layout.layoutPerKindComplete ||
        layout.coverage != CoverageState::Complete ||
        !ValidatePerKindLayoutProperties(
            *graph, layout.layoutObservedKindCounts)) {
        return false;
    }
    stage(L"definition-references");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedDefinitionReferencesStart,
        effective.definitionReferences.size(), definitionByIdentity.size());
    for (const DefinitionReferenceObservation& reference :
         effective.definitionReferences) {
        PropertyObservation coordinate;
        coordinate.target = reference.source;
        coordinate.targetIdentity = reference.sourceIdentity;
        const size_t owner = targetNode(coordinate);
        const auto targetDefinition =
            definitionByIdentity.find(reference.definitionIdentity);
        if (owner == none || targetDefinition == definitionByIdentity.end() ||
            !IsLegalReferenceEdge(
                reference.edge,
                graph->nodes[owner].kind,
                NodeKind::Definition,
                DefinitionKind::Style,
                targetDefinition->second.second)) {
            return false;
        }
        const size_t target = targetDefinition->second.first;
        graph->nodes[owner].edges.push_back(
            {reference.edge, target, reference.ordinal, 0});
    }
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedDefinitionReferencesEnd,
        effective.definitionReferences.size(), graph->nodes.size());
    profile.Transition(TypedBuildSubstage::CoverageDiagnostics);
    stage(L"coverage-diagnostics");
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedCoverageDiagnostics,
        graph->nodes.size(), graph->blobs.size());
    using CoverageKey = std::tuple<
        size_t, CoverageCoordinateKind, ProfileId, FieldTag, bool,
        PropertyKeyId>;
    std::set<CoverageKey> coverageKeys;
    std::set<CoverageKey> sourceCoverageKeys;
    std::map<std::wstring, std::vector<size_t>> runOwnersByIdentity;
    for (const auto& entry : allRunNodes) {
        std::vector<size_t>& owners = runOwnersByIdentity[entry.first];
        if (owners.empty() || owners.back() != entry.second) {
            owners.push_back(entry.second);
        }
    }
    for (const GranularCoverage& fact : graph->granularCoverage) {
        coverageKeys.emplace(
            fact.owner, fact.coordinate, fact.profile, fact.ownerField,
            fact.propertyKeyPresent,
            fact.propertyKeyPresent ? fact.propertyKey : 0);
    }
    for (const ReaderPayload* const payload : payloads) {
        for (const CoverageObservation& observation : payload->coverageFacts) {
            PropertyObservation coordinate;
            coordinate.target = observation.target;
            coordinate.targetIdentity = observation.targetIdentity;
            std::vector<size_t> owners;
            if (observation.target == PropertyTarget::Run) {
                const auto known = runOwnersByIdentity.find(
                    observation.targetIdentity);
                if (known != runOwnersByIdentity.end()) {
                    owners = known->second;
                }
            } else {
                const size_t owner = targetNode(coordinate);
                if (owner != none) owners.push_back(owner);
            }
            if (owners.empty()) {
                return false;
            }
            Bytes evidence = Utf16Value(observation.detail);
            if (evidence.empty()) {
                evidence.push_back(static_cast<std::uint8_t>(payload->reader));
                Add(&evidence, codec::Uint16(observation.ownerField));
                Add(&evidence, codec::Uint32(observation.propertyKey));
                evidence.push_back(0);
            }
            // Coverage details are UTF-16 byte streams.
            graph->blobs.push_back(MakeBlob(
                evidence,
                static_cast<std::uint8_t>(0x80 + graph->blobs.size())));
            const CoverageCoordinateKind coverageCoordinate =
                observation.propertyKeyPresent
                    ? CoverageCoordinateKind::Property
                    : observation.coordinate;
            for (const size_t owner : owners) {
                const CoverageKey key{
                    owner, coverageCoordinate, observation.profile,
                    observation.ownerField, observation.propertyKeyPresent,
                    observation.propertyKeyPresent
                        ? observation.propertyKey : 0};
                // Four global native-catalog terminal flags intentionally
                // fold into one manifest coordinate.
                const bool globalCatalogAggregate =
                    owner == 0 &&
                    observation.target == PropertyTarget::Document &&
                    observation.targetIdentity.empty() &&
                    static_cast<std::uint8_t>(observation.profile) ==
                        kNoProfile &&
                    observation.ownerField == 0 &&
                    observation.state == CoverageState::NotExposed;
                const bool firstSource = sourceCoverageKeys.insert(key).second;
                if (!firstSource && globalCatalogAggregate) continue;
                const bool firstCoordinate = coverageKeys.insert(key).second;
                if (!firstSource || !firstCoordinate) {
                    const auto prior = std::find_if(
                        graph->granularCoverage.begin(),
                        graph->granularCoverage.end(),
                        [&key](const GranularCoverage& candidate) {
                            return CoverageKey{
                                candidate.owner, candidate.coordinate,
                                candidate.profile, candidate.ownerField,
                                candidate.propertyKeyPresent,
                                candidate.propertyKeyPresent
                                    ? candidate.propertyKey : 0} == key;
                        });
                    // A derived aggregate may already own this coordinate;
                    // one explicit source can confirm the same state. Two
                    // explicit sources or contradictory states fail closed.
                    if (firstSource && prior != graph->granularCoverage.end() &&
                        prior->state == observation.state) {
                        continue;
                    }
                    if (failure != nullptr) {
                        *failure = L"graph:coverage-diagnostics:duplicate:owner=" +
                            std::to_wstring(owner) + L":field=" +
                            std::to_wstring(observation.ownerField) +
                            L":profile=" + std::to_wstring(
                                static_cast<unsigned>(observation.profile)) +
                            L":state=" + std::to_wstring(
                                static_cast<unsigned>(observation.state)) +
                            L":target=" + std::to_wstring(
                                static_cast<unsigned>(observation.target)) +
                            L":identity=" + observation.targetIdentity;
                    }
                    return false;
                }
                graph->granularCoverage.push_back({
                    owner, coverageCoordinate, observation.profile,
                    observation.state,
                    observation.ownerField,
                    observation.propertyKeyPresent, observation.propertyKey,
                    graph->blobs.size() - 1, 0,
                });
            }
        }
        for (const DiagnosticObservation& observation : payload->diagnostics) {
            PropertyObservation coordinate;
            coordinate.target = observation.target;
            coordinate.targetIdentity = observation.targetIdentity;
            const size_t owner = targetNode(coordinate);
            if (owner == none) {
                return false;
            }
            const Bytes detail = Utf16Value(observation.detail);
            graph->blobs.push_back(MakeBlob(
                detail,
                static_cast<std::uint8_t>(0xC0 + graph->blobs.size())));
            graph->diagnostics.push_back({
                owner,
                observation.code,
                observation.severity,
                observation.propertyKeyPresent,
                observation.propertyKey,
                observation.hresult,
                graph->blobs.size() - 1,
                0,
            });
        }
    }
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedCoverageFactsEnd,
        graph->granularCoverage.size(), graph->diagnostics.size());
    stage(L"run-qualification");
    struct RunCoverageCounts final {
        size_t relevant = 0;
        size_t complete = 0;
        size_t notExposed = 0;
        size_t readFailed = 0;
        size_t contradictory = 0;
    };
    std::vector<RunCoverageCounts> runCoverage(graph->nodes.size());
    for (const GranularCoverage& coverage : graph->granularCoverage) {
        if (coverage.owner >= runCoverage.size() ||
            coverage.coordinate != CoverageCoordinateKind::NodeField ||
            coverage.profile != ProfileId::EditableText ||
            coverage.ownerField != 102) {
            continue;
        }
        RunCoverageCounts& counts = runCoverage[coverage.owner];
        ++counts.relevant;
        switch (coverage.state) {
        case CoverageState::Complete:
            ++counts.complete;
            break;
        case CoverageState::NotExposed:
            ++counts.notExposed;
            break;
        case CoverageState::ReadFailed:
            ++counts.readFailed;
            break;
        case CoverageState::NotApplicable:
        case CoverageState::NotRequested:
        case CoverageState::ProjectionOmitted:
            ++counts.contradictory;
            break;
        }
    }
    for (size_t nodeIndex = 0; nodeIndex < graph->nodes.size(); ++nodeIndex) {
        const Node& node = graph->nodes[nodeIndex];
        if (node.kind != NodeKind::CharacterRun) {
            continue;
        }
        const size_t references = static_cast<size_t>(std::count_if(
            node.edges.begin(), node.edges.end(), [](const Edge& edge) {
                return edge.kind == EdgeKind::CharacterShapeRef;
            }));
        const bool shapePropertiesObserved = std::any_of(
            node.properties.begin(), node.properties.end(),
            [](const Property& property) {
                return property.ownerField == 102 &&
                    property.key >= 1000 && property.key <= 1040 &&
                    (property.key < 1015 || property.key > 1017);
            });
        const RunCoverageCounts& counts = runCoverage[nodeIndex];
        const size_t relevantCoverageCount = counts.relevant;
        const size_t completeCoverageCount = counts.complete;
        const size_t notExposedCoverageCount = counts.notExposed;
        const size_t readFailedCoverageCount = counts.readFailed;
        const size_t contradictoryCoverageCount = counts.contradictory;
        const bool completeMode = relevantCoverageCount == 1 &&
            completeCoverageCount == 1 &&
            contradictoryCoverageCount == 0;
        const bool terminalMode = relevantCoverageCount == 1 &&
            (notExposedCoverageCount == 1 ||
             readFailedCoverageCount == 1) &&
            contradictoryCoverageCount == 0;
        if ((!completeMode && !terminalMode) ||
            (completeMode &&
             (references != 1 || !shapePropertiesObserved)) ||
            (terminalMode &&
             (references != 0 || shapePropertiesObserved))) {
            if (failure != nullptr) {
                *failure = L"graph:run-qualification:" +
                    std::to_wstring(nodeIndex) + L":" +
                    std::to_wstring(references) + L":" +
                    std::to_wstring(shapePropertiesObserved ? 1 : 0) + L":" +
                    std::to_wstring(relevantCoverageCount) + L":" +
                    std::to_wstring(completeCoverageCount) + L":" +
                    std::to_wstring(notExposedCoverageCount) + L":" +
                    std::to_wstring(readFailedCoverageCount) + L":" +
                    std::to_wstring(contradictoryCoverageCount);
                const auto locator = std::find_if(
                    allRunNodes.begin(), allRunNodes.end(),
                    [nodeIndex](const auto& candidate) {
                        return candidate.second == nodeIndex;
                    });
                if (locator != allRunNodes.end()) {
                    *failure += L":" + locator->first;
                    size_t sourceFacts = 0;
                    for (const ReaderPayload* const payload : payloads) {
                        sourceFacts += static_cast<size_t>(std::count_if(
                            payload->coverageFacts.begin(),
                            payload->coverageFacts.end(),
                            [&locator](const CoverageObservation& fact) {
                                return fact.target == PropertyTarget::Run &&
                                    fact.targetIdentity == locator->first;
                            }));
                    }
                    *failure += L":facts=" + std::to_wstring(sourceFacts);
                }
                size_t ancestor = node.parent;
                for (size_t depth = 0;
                     depth < 4 && ancestor < graph->nodes.size(); ++depth) {
                    *failure += L":k=" + std::to_wstring(
                        static_cast<unsigned>(graph->nodes[ancestor].kind));
                    ancestor = graph->nodes[ancestor].parent;
                }
            }
            return false;
        }
    }
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedRunQualificationEnd,
        allRunNodes.size(), graph->nodes.size());
    stage(L"native-order");
    // Reader families are independent; physical NodeBlocks are not. Merge
    // control-family children by the one native order shared by all adapters.
    for (Node& parent : graph->nodes) {
        std::vector<controls::NativeAnchorOrderRecord> nativeOrder;
        for (const size_t child : parent.children) {
            if (graph->nodes[child].controlFamily) {
                const Node& node = graph->nodes[child];
                nativeOrder.push_back({
                    node.anchor.list, node.anchor.paragraph,
                    node.anchor.character, node.headCtrlOrdinal, child});
            }
        }
        if (!controls::OrderByAuthoritativeNativeAnchor(&nativeOrder)) {
            return false;
        }
        size_t controlAt = 0;
        for (size_t& child : parent.children) {
            if (graph->nodes[child].controlFamily) {
                child = nativeOrder[controlAt++].sourceIndex;
            }
        }
        for (size_t ordinal = 0; ordinal < parent.children.size(); ++ordinal) {
            const size_t child = parent.children[ordinal];
            graph->nodes[child].sibling = ordinal;
            for (Edge& edge : parent.edges) {
                if (edge.kind == EdgeKind::Contains && edge.target == child) {
                    edge.ordinal = ordinal;
                }
            }
        }
    }
    stage(L"coverage-order");
    std::sort(
        graph->granularCoverage.begin(),
        graph->granularCoverage.end(),
        [](const GranularCoverage& left, const GranularCoverage& right) {
            return left.owner < right.owner ||
                (left.owner == right.owner &&
                 (left.coordinate < right.coordinate ||
                  (left.coordinate == right.coordinate &&
                   (static_cast<std::uint8_t>(left.profile) <
                        static_cast<std::uint8_t>(right.profile) ||
                    (left.profile == right.profile &&
                     (left.ownerField < right.ownerField ||
                      (left.ownerField == right.ownerField &&
                       (left.propertyKeyPresent < right.propertyKeyPresent ||
                        (left.propertyKeyPresent == right.propertyKeyPresent &&
                         left.propertyKey < right.propertyKey)))))))));
        });
    for (Node& node : graph->nodes) {
        std::sort(
            node.properties.begin(),
            node.properties.end(),
            [](const Property& left, const Property& right) {
                return left.ownerField < right.ownerField ||
                    (left.ownerField == right.ownerField &&
                     left.key < right.key);
            });
        std::sort(
            node.edges.begin(),
            node.edges.end(),
            [](const Edge& left, const Edge& right) {
                return left.kind < right.kind ||
                    (left.kind == right.kind &&
                     left.ordinal < right.ordinal);
            });
    }
    profile.Transition(TypedBuildSubstage::IdentityReconciliation);
    RecordCaptureProgressPoint(
        CaptureProgressPoint::TypedIdentityReconciliation,
        arena.AcquiredKeys().size(), graph->nodes.size());
    if (!arena.FinalizeReconciliation()) {
        if (failure != nullptr) {
            *failure = L"graph:identity-finalize:" +
                std::to_wstring(arena.AcquiredKeys().size()) + L":" +
                std::to_wstring(arena.SeededKeys().size());
        }
        return false;
    }
    return true;
}

struct CoverageEmission final {
    RecordId* recordId = nullptr;
    CoverageCoordinateKind coordinate = CoverageCoordinateKind::NodeField;
    ProfileId profile = ProfileId::Structure;
    CoverageState state = CoverageState::NotExposed;
    FieldTag ownerField = 0;
    bool propertyKeyPresent = false;
    PropertyKeyId propertyKey = 0;
    size_t blob = 0;
};

Bytes CoverageEmissionKey(
    const Graph& graph,
    const CoverageEmission& emission) {
    const bool ownerPresent =
        emission.coordinate != CoverageCoordinateKind::Global &&
        emission.coordinate != CoverageCoordinateKind::Profile;
    const RecordKind ownerKind =
        emission.coordinate == CoverageCoordinateKind::Global
            ? RecordKind::Manifest
        : emission.coordinate == CoverageCoordinateKind::Profile
            ? RecordKind::Coverage
        : emission.coordinate == CoverageCoordinateKind::Property
            ? RecordKind::Property
            : RecordKind::Node;
    const std::uint8_t profile =
        emission.coordinate == CoverageCoordinateKind::Global ||
                emission.coordinate == CoverageCoordinateKind::Node
            ? kNoProfile
            : static_cast<std::uint8_t>(emission.profile);
    const FieldTag ownerField =
        emission.coordinate == CoverageCoordinateKind::Global ||
                emission.coordinate == CoverageCoordinateKind::Node
            ? 0
        : emission.coordinate == CoverageCoordinateKind::Profile
            ? static_cast<FieldTag>(profile)
            : emission.ownerField;
    Bytes key;
    key.push_back(ownerPresent ? 1U : 0U);
    Add(&key, codec::Uint16(static_cast<std::uint16_t>(ownerKind)));
    Add(&key, codec::Uint16(ownerField));
    key.push_back(profile);
    key.push_back(emission.propertyKeyPresent ? 1U : 0U);
    if (emission.propertyKeyPresent) {
        Add(&key, codec::Uint32(emission.propertyKey));
    }
    key.push_back(static_cast<std::uint8_t>(emission.state));
    const RecordBlob& blob = graph.blobs[emission.blob];
    Add(&key, codec::BlobSliceValue(
        blob.id, 0, blob.bytes.size(),
        codec::Hash(codec::View(blob.bytes))));
    return key;
}

std::vector<CoverageEmission> CoverageEmissions(
    Graph* const graph,
    const size_t nodeIndex,
    std::array<RecordId, kQualifiedReaderCount>* const coverage,
    RecordId* const binaryCoverage,
    const std::vector<ReaderPayload>& payloads) {
    std::vector<CoverageEmission> emissions;
    if (nodeIndex == 0) {
        std::array<const ReaderPayload*, kQualifiedReaderCount> ordered{};
        for (const ReaderPayload& payload : payloads) {
            ordered[static_cast<size_t>(payload.reader)] = &payload;
        }
        for (size_t index = 0; index < ordered.size(); ++index) {
            emissions.push_back({
                &(*coverage)[index], CoverageCoordinateKind::NodeField,
                ReaderProfile(ordered[index]->reader),
                ordered[index]->coverage, 100, false, 0,
                graph->coverageBlob[index]});
        }
        emissions.push_back({
            binaryCoverage, CoverageCoordinateKind::NodeField,
            ProfileId::BinaryContent, graph->binaryCoverageState,
            100, false, 0, graph->binaryCoverageBlob});
    }
    for (GranularCoverage& fact : graph->granularCoverage) {
        if (fact.owner == nodeIndex) {
            emissions.push_back({
                &fact.recordId, fact.coordinate, fact.profile, fact.state,
                fact.ownerField, fact.propertyKeyPresent, fact.propertyKey,
                fact.blob});
        }
    }
    std::sort(
        emissions.begin(), emissions.end(),
        [graph](const CoverageEmission& left,
                const CoverageEmission& right) {
            return CoverageEmissionKey(*graph, left) <
                CoverageEmissionKey(*graph, right);
        });
    return emissions;
}

void AssignRecordIds(
    Graph* const graph,
    const size_t nodeIndex,
    RecordId* const next,
    std::array<RecordId, kQualifiedReaderCount>* const coverage,
    RecordId* const binaryCoverage,
    const std::vector<ReaderPayload>& payloads) {
    Node& node = graph->nodes[nodeIndex];
    node.recordId = (*next)++;
    for (Property& property : node.properties) {
        property.recordId = (*next)++;
    }
    for (CoverageEmission& emission : CoverageEmissions(
             graph, nodeIndex, coverage, binaryCoverage, payloads)) {
        *emission.recordId = (*next)++;
    }
    for (Diagnostic& diagnostic : graph->diagnostics) {
        if (diagnostic.owner == nodeIndex) {
            diagnostic.recordId = (*next)++;
        }
    }
    for (Edge& edge : node.edges) {
        edge.recordId = (*next)++;
    }
    for (const size_t child : node.children) {
        AssignRecordIds(
            graph, child, next, coverage, binaryCoverage, payloads);
    }
}

void FinalizePayloads(Graph* const graph, const std::vector<ReaderPayload>& source) {
    std::array<const ReaderPayload*, kQualifiedReaderCount> payloads{};
    for (const ReaderPayload& payload : source) {
        payloads[static_cast<size_t>(payload.reader)] = &payload;
    }
    const ReaderPayload& story = *payloads[static_cast<size_t>(QualifiedReader::StorySpine)];
    const ReaderPayload& tables = *payloads[static_cast<size_t>(QualifiedReader::TableTopology)];
    const ReaderPayload& images = *payloads[static_cast<size_t>(QualifiedReader::ImagesShapesCaptions)];
    const ReaderPayload& effective = *payloads[static_cast<size_t>(QualifiedReader::EffectiveProperties)];
    size_t definitionAt = 0;
    size_t sectionAt = 0;
    size_t tableAt = 0;
    size_t imageAt = 0;
    for (Node& node : graph->nodes) {
        if (node.kind == NodeKind::Document) {
            AppendField(&node.payload, {100, kRequiredArray,
                                        ScalarTag::Uint64,
                                        BagCount(node, 100), Bag(node, 100)});
        } else if (node.kind == NodeKind::Definition) {
            if (definitionAt >= effective.definitions.size()) {
                continue;
            }
            const DefinitionObservation& definition =
                effective.definitions[definitionAt++];
            AppendField(&node.payload, {100, kFieldFlagRequired,
                                        ScalarTag::Uint16, 1,
                                        codec::Uint16(static_cast<std::uint16_t>(
                                            definition.kind))});
            AppendField(&node.payload, {101, kFieldFlagRequired,
                                        ScalarTag::Sint64, 1,
                                        definition.nativeIdState ==
                                                ObservationState::Value
                                            ? Observe(ObservationState::Value,
                                                      codec::Sint64(
                                                          definition.nativeId))
                                            : Observe(
                                                  definition.nativeIdState)});
            AppendField(&node.payload, {102, kRequiredArray,
                                        ScalarTag::Uint64,
                                        BagCount(node, 102), Bag(node, 102)});
            AppendField(&node.payload, {103, kFieldFlagRequired,
                                        ScalarTag::SHA256, 1,
                                        Sha256Value(
                                            definition.propertyDigest)});
        } else if (node.kind == NodeKind::Story) {
            const bool body = node.parent == 0;
            const bool caption = std::any_of(
                node.edges.begin(), node.edges.end(), [](const Edge& edge) {
                    return edge.kind == EdgeKind::CaptionOf;
                });
            const StoryKind storyKind = body
                ? StoryKind::Body
                : caption
                    ? StoryKind::Caption
                    : StoryKind::TableCell;
            AppendField(&node.payload, {100, kFieldFlagRequired,
                                        ScalarTag::Uint16, 1,
                                        codec::Uint16(static_cast<std::uint16_t>(
                                            storyKind))});
            if (body) {
                AppendField(&node.payload, {101, kFieldFlagRequired,
                                            ScalarTag::UUID128, 1,
                                            Observe(ObservationState::NotApplicable)});
            } else {
                AppendField(&node.payload, {101, kFieldFlagRequired,
                                            ScalarTag::UUID128, 1,
                                            Observe(ObservationState::Value,
                                                    UuidValue(graph->nodes[node.parent].id))});
            }
            AppendField(&node.payload, {102, kFieldFlagRequired,
                                        ScalarTag::Sint64, 1,
                                        ObserveScalar(node.storyNativeList)});
            if (storyKind == StoryKind::Caption) {
                AppendField(&node.payload, {103, 0,
                                            ScalarTag::Bool, 1,
                                            Observe(
                                                node.captionAutomaticNumber.state,
                                                codec::Bool(
                                                    node.captionAutomaticNumber.value != 0))});
                AppendField(&node.payload, {104, 0,
                                            ScalarTag::Sint64, 1,
                                            ObserveScalar(node.captionStyleId)});
                AppendField(&node.payload, {105, 0,
                                            ScalarTag::UTF16, 1,
                                            ObserveText(node.captionStyleName)});
            }
        } else if (node.kind == NodeKind::Section) {
            const SectionObservation& section = story.sections[sectionAt++];
            AppendField(&node.payload, {100, kFieldFlagRequired,
                                        ScalarTag::Uint64, 1,
                                        codec::Uint64(section.ordinal)});
            AppendField(&node.payload, {101, kFieldFlagRequired,
                                        ScalarTag::Struct, 1,
                                        Observe(ObservationState::Value,
                                                PositionValue(section.start))});
            AppendField(&node.payload, {102, kFieldFlagRequired,
                                        ScalarTag::Struct, 1,
                                        Observe(ObservationState::NotExposed)});
            AppendField(&node.payload, {103, kRequiredArray,
                                        ScalarTag::Uint64, 0, EmptyArray()});
        } else if (node.kind == NodeKind::Paragraph) {
            NativePosition start{};
            const Bytes& locator = node.locator;
            if (locator.size() == 24) {
                auto read64 = [](const std::uint8_t* bytes) {
                    std::uint64_t value = 0;
                    for (size_t i = 0; i < 8; ++i) {
                        value |= static_cast<std::uint64_t>(bytes[i]) << (i * 8);
                    }
                    return static_cast<std::int64_t>(value);
                };
                start.list = read64(locator.data() + 8);
                start.paragraph = read64(locator.data() + 16);
            }
            AppendField(&node.payload, {100, kFieldFlagRequired,
                                        ScalarTag::Struct, 1,
                                        Observe(ObservationState::Value,
                                                PositionValue(start))});
            AppendField(&node.payload, {101, kFieldFlagRequired,
                                        ScalarTag::Struct, 1,
                                        Observe(ObservationState::NotExposed)});
            AppendField(&node.payload, {102, kFieldFlagRequired,
                                        ScalarTag::Sint64, 1,
                                        Observe(ObservationState::NotExposed)});
            AppendField(&node.payload, {103, kRequiredArray,
                                        ScalarTag::Uint64,
                                        BagCount(node, 103), Bag(node, 103)});
            AppendField(&node.payload, {104, kRequiredArray,
                                        ScalarTag::Uint64,
                                        BagCount(node, 104), Bag(node, 104)});
        } else if (node.kind == NodeKind::CharacterRun) {
            AppendField(&node.payload, {102, kRequiredArray,
                                        ScalarTag::Uint64,
                                        BagCount(node, 102), Bag(node, 102)});
        } else if (node.kind == NodeKind::Table) {
            const TableObservation* table = nullptr;
            for (const TableObservation& candidate : tables.tables) {
                const Bytes wanted = ControlLocator(
                    L"tbl", candidate.instanceIdPresent,
                    candidate.instanceId, candidate.headCtrlOrdinal,
                    candidate.anchor);
                if (wanted == node.locator) {
                    table = &candidate;
                }
            }
            if (table == nullptr) {
                continue;
            }
            node.payload = ControlPayload(
                node, L"tbl", table->instanceIdPresent, table->instanceId,
                table->headCtrlOrdinal, table->anchor);
            AppendField(&node.payload, {107, kFieldFlagRequired,
                                        ScalarTag::Uint64, 1,
                                        table->rowCount == 0
                                            ? Observe(ObservationState::NotExposed)
                                            : Observe(ObservationState::Value,
                                                      codec::Uint64(table->rowCount))});
            AppendField(&node.payload, {108, kFieldFlagRequired,
                                        ScalarTag::Uint64, 1,
                                        table->columnCount == 0
                                            ? Observe(ObservationState::NotExposed)
                                            : Observe(ObservationState::Value,
                                                      codec::Uint64(table->columnCount))});
            AppendField(&node.payload, {109, kFieldFlagRequired,
                                        ScalarTag::SHA256, 1,
                                        table->cells.empty()
                                            ? Observe(ObservationState::NotExposed)
                                            : Observe(ObservationState::Value,
                                                      TopologyDigestValue(*table))});
            ++tableAt;
        } else if (node.kind == NodeKind::TableCell) {
            AppendField(&node.payload, {109, kRequiredArray,
                                        ScalarTag::Uint64,
                                        BagCount(node, 109), Bag(node, 109)});
        } else if (node.kind == NodeKind::Image) {
            const ImageObservation* image = nullptr;
            for (const ImageObservation& candidate : images.images) {
                const Bytes wanted = ControlLocator(
                    candidate.ctrlId, candidate.instanceIdPresent,
                    candidate.instanceId, candidate.headCtrlOrdinal,
                    candidate.anchor);
                if (wanted == node.locator) {
                    image = &candidate;
                }
            }
            if (image == nullptr) {
                continue;
            }
            node.payload = ControlPayload(
                node, image->ctrlId, image->instanceIdPresent,
                image->instanceId, image->headCtrlOrdinal, image->anchor);
            const auto pairObservation = [](const ScalarObservation& first,
                                            const ScalarObservation& second) {
                if (first.state != ObservationState::Value ||
                    second.state != ObservationState::Value) {
                    return Observe(
                        first.state == ObservationState::ReadFailed ||
                                second.state == ObservationState::ReadFailed
                            ? ObservationState::ReadFailed
                            : ObservationState::NotExposed);
                }
                Bytes value = codec::Sint64(first.value);
                Add(&value, codec::Sint64(second.value));
                return Observe(ObservationState::Value, value);
            };
            AppendField(&node.payload, {107, kFieldFlagRequired,
                                        ScalarTag::Struct, 1,
                                        pairObservation(image->scalar[0],
                                                        image->scalar[1])});
            AppendField(&node.payload, {108, kFieldFlagRequired,
                                        ScalarTag::Struct, 1,
                                        pairObservation(image->scalar[2],
                                                        image->scalar[3])});
            if (image->scalar[4].state == ObservationState::Value &&
                image->scalar[5].state == ObservationState::Value &&
                image->scalar[6].state == ObservationState::Value &&
                image->scalar[7].state == ObservationState::Value) {
                Bytes crop = codec::Sint64(image->scalar[4].value);
                Add(&crop, codec::Sint64(image->scalar[6].value));
                Add(&crop, codec::Sint64(image->scalar[5].value));
                Add(&crop, codec::Sint64(image->scalar[7].value));
                AppendField(&node.payload, {109, kFieldFlagRequired,
                                            ScalarTag::Struct, 1,
                                            Observe(ObservationState::Value,
                                                    crop)});
            } else {
                AppendField(&node.payload, {109, kFieldFlagRequired,
                                            ScalarTag::Struct, 1,
                                            Observe(ObservationState::NotExposed)});
            }
            Sha256 assetDigest{};
            if (image->asset.binaryState == ObservationState::Value &&
                ParseSha256(image->asset.sha256, &assetDigest)) {
                Bytes reference(16, 0);
                Add(&reference, codec::Uint64(image->asset.byteLength));
                Add(&reference, Sha256Value(assetDigest));
                AppendField(&node.payload, {110, kFieldFlagRequired,
                                            ScalarTag::Struct, 1,
                                            Observe(ObservationState::Value,
                                                    reference)});
            } else {
                AppendField(&node.payload, {110, kFieldFlagRequired,
                                            ScalarTag::Struct, 1,
                                            Observe(image->asset.binaryState)});
            }
            AppendField(&node.payload, {111, kFieldFlagRequired,
                                        ScalarTag::UTF16, 1,
                                        ObserveText(image->text[0])});
            AppendField(&node.payload, {112, kRequiredArray,
                                        ScalarTag::Uint64,
                                        BagCount(node, 112), Bag(node, 112)});
            ++imageAt;
        }
    }
    static_cast<void>(tableAt);
    static_cast<void>(imageAt);
}

using RecordEmitter = bool (*)(void*, const Bytes&) noexcept;

bool EmitNode(
    const Graph& graph,
    const size_t nodeIndex,
    const FingerprintIndex* const fingerprints,
    const std::array<RecordId, kQualifiedReaderCount>& coverageIds,
    const RecordId binaryCoverageId,
    const std::vector<ReaderPayload>& payloads,
    const RecordEmitter emit,
    void* const emitContext) {
    const Node& node = graph.nodes[nodeIndex];
    const NodeId* parent = node.parent == (std::numeric_limits<size_t>::max)()
        ? nullptr
        : &graph.nodes[node.parent].id;
    if (!emit(emitContext, NodeRecordBytes(node, parent, fingerprints)))
        return false;
    for (const Property& property : node.properties) {
        if (!emit(emitContext, PropertyRecordBytes(property, node.id)))
            return false;
    }
    auto mutableCoverageIds = coverageIds;
    RecordId mutableBinaryCoverageId = binaryCoverageId;
    Graph* const mutableGraph = const_cast<Graph*>(&graph);
    for (const CoverageEmission& emission : CoverageEmissions(
             mutableGraph, nodeIndex, &mutableCoverageIds,
             &mutableBinaryCoverageId, payloads)) {
        if (!emit(emitContext, CoverageRecordBytes(
                *emission.recordId,
                node.id,
                emission.coordinate,
                emission.profile,
                emission.state,
                graph.blobs[emission.blob],
                emission.ownerField,
                emission.propertyKeyPresent,
                emission.propertyKey))) return false;
    }
    for (const Diagnostic& diagnostic : graph.diagnostics) {
        if (diagnostic.owner == nodeIndex &&
            !emit(emitContext, DiagnosticRecordBytes(
                diagnostic, node.id, graph.blobs[diagnostic.blob])))
            return false;
    }
    for (const Edge& edge : node.edges) {
        if (!emit(emitContext, EdgeRecordBytes(
                edge, node.id, graph.nodes[edge.target].id))) return false;
    }
    for (const size_t child : node.children) {
        if (!EmitNode(
                graph, child, fingerprints, coverageIds, binaryCoverageId,
                payloads, emit, emitContext)) return false;
    }
    return true;
}

std::uint16_t Read16(const std::uint8_t* const bytes) noexcept {
    return static_cast<std::uint16_t>(
        bytes[0] | (static_cast<std::uint16_t>(bytes[1]) << 8));
}

std::uint32_t Read32(const std::uint8_t* const bytes) noexcept {
    return static_cast<std::uint32_t>(bytes[0]) |
        (static_cast<std::uint32_t>(bytes[1]) << 8) |
        (static_cast<std::uint32_t>(bytes[2]) << 16) |
        (static_cast<std::uint32_t>(bytes[3]) << 24);
}

std::uint64_t Read64(const std::uint8_t* const bytes) noexcept {
    std::uint64_t value = 0;
    for (size_t index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(bytes[index]) << (index * 8);
    }
    return value;
}

struct FieldView final {
    std::uint16_t tag = 0;
    codec::ByteView value{};
};

bool Fields(codec::ByteView bytes, std::vector<FieldView>* const fields) {
    std::uint64_t offset = 0;
    while (offset != bytes.size) {
        if (bytes.size - offset < 24) {
            return false;
        }
        const std::uint64_t size = Read64(bytes.data + offset + 16);
        if (size > bytes.size - offset - 24) {
            return false;
        }
        fields->push_back({
            Read16(bytes.data + offset),
            {bytes.data + offset + 24, size},
        });
        offset += 24 + size;
    }
    return true;
}

std::wstring DecodeUtf16(const codec::ByteView value) {
    if (value.size < 8) {
        return {};
    }
    const std::uint64_t count = Read64(value.data);
    if (count > (value.size - 8) / 2 || value.size != 8 + count * 2) {
        return {};
    }
    std::wstring text;
    text.reserve(static_cast<size_t>(count));
    for (std::uint64_t index = 0; index < count; ++index) {
        text.push_back(static_cast<wchar_t>(
            Read16(value.data + 8 + index * 2)));
    }
    return text;
}

bool DecodeControlInstanceObservation(
    const FieldView& field,
    bool* const present,
    std::wstring* const value) {
    if (present == nullptr || value == nullptr || field.value.size < 24) {
        return false;
    }
    const std::uint8_t state = field.value.data[0];
    const std::uint8_t valuePresent = field.value.data[1];
    if (state > static_cast<std::uint8_t>(
                    ObservationState::ProjectionOmitted) ||
        valuePresent > 1 || Read16(field.value.data + 2) != 0) {
        return false;
    }
    const std::uint64_t detailUnits = Read64(field.value.data + 8);
    const std::uint64_t valueBytes = Read64(field.value.data + 16);
    if (detailUnits > (std::numeric_limits<std::uint64_t>::max)() / 2) {
        return false;
    }
    const std::uint64_t valueAt = 24 + detailUnits * 2;
    if (valueAt > field.value.size ||
        valueBytes != field.value.size - valueAt) {
        return false;
    }
    const bool hasValue = state == static_cast<std::uint8_t>(
        ObservationState::Value);
    const std::uint32_t canonicalHresult =
        state == static_cast<std::uint8_t>(ObservationState::ReadFailed)
            ? static_cast<std::uint32_t>(E_FAIL)
            : static_cast<std::uint32_t>(S_OK);
    if (Read32(field.value.data + 4) != canonicalHresult) {
        return false;
    }
    if (!hasValue) {
        if (valuePresent != 0 || valueBytes != 0) {
            return false;
        }
        *present = false;
        value->clear();
        return true;
    }
    if (valuePresent != 1 || valueBytes < 8) {
        return false;
    }
    const codec::ByteView encoded{
        field.value.data + valueAt, valueBytes};
    const std::uint64_t units = Read64(encoded.data);
    if (units > (encoded.size - 8) / 2 ||
        encoded.size != 8 + units * 2) {
        return false;
    }
    *present = true;
    *value = DecodeUtf16(encoded);
    return true;
}

const FieldView* Find(
    const std::vector<FieldView>& fields,
    const std::uint16_t tag) {
    for (const FieldView& field : fields) {
        if (field.tag == tag) {
            return &field;
        }
    }
    return nullptr;
}

} // namespace

void ResetTypedBuildProfiles() noexcept {
    gTypedProfiles.clear();
    gTypedProfileIndex = (std::numeric_limits<std::size_t>::max)();
    gTypedProfilesEnabled = true;
}

std::vector<TypedBuildProfile> ReadTypedBuildProfiles() {
    return gTypedProfiles;
}

void SetTypedGraphReuseForTesting(const bool enabled) noexcept {
    gTypedGraphReuseEnabled = enabled;
}

CaptionLocationQualification::CaptionLocationQualification(
    const std::uint64_t captureProvenance,
    std::wstring ctrlId,
    const bool instanceIdPresent,
    std::wstring instanceId,
    const std::uint64_t headCtrlOrdinal,
    const NativePosition anchor,
    const NativePosition captionStart)
    : captureProvenance_(captureProvenance),
      ctrlId_(std::move(ctrlId)),
      instanceId_(std::move(instanceId)),
      headCtrlOrdinal_(headCtrlOrdinal),
      anchor_(anchor),
      captionStart_(captionStart),
      instanceIdPresent_(instanceIdPresent),
      issued_(true) {}

bool CaptionLocationQualification::Matches(
    const CaptureIdentityArena& capture,
    const ImageObservation& image) const noexcept {
    return issued_ && captureProvenance_ != 0 &&
        captureProvenance_ == capture.captionProvenance_ &&
        ctrlId_ == image.ctrlId &&
        instanceIdPresent_ == image.instanceIdPresent &&
        instanceId_ == image.instanceId &&
        headCtrlOrdinal_ == image.headCtrlOrdinal &&
        anchor_.list == image.anchor.list &&
        anchor_.paragraph == image.anchor.paragraph &&
        anchor_.character == image.anchor.character &&
        captionStart_.list == image.captionStart.list &&
        captionStart_.paragraph == image.captionStart.paragraph &&
        captionStart_.character == image.captionStart.character &&
        image.captionList.state == ObservationState::Value &&
        captionStart_.list == image.captionList.value;
}

bool IsQualifiedNativeCaptionLocation(
    const CaptureIdentityArena& capture,
    const ImageObservation& image) noexcept {
    return image.captionLocationQualification.Matches(capture, image);
}

bool CaptionLocationsReadyForGraphPublication(
    const CaptureIdentityArena& capture,
    const std::vector<ImageObservation>& images) noexcept {
    return std::all_of(
        images.begin(), images.end(), [&capture](const auto& image) {
            return image.captionList.state != ObservationState::Value ||
                IsQualifiedNativeCaptionLocation(capture, image);
        });
}

std::wstring CanonicalNativeSiteIdentity(
    const PropertyTarget target,
    const std::wstring& ctrlId,
    const std::uint64_t ordinal,
    const NativePosition& anchor,
    const bool instanceIdPresent,
    const std::wstring& rawInstanceId) {
    return NativeSiteIdentity(
        target, ctrlId, ordinal, anchor, instanceIdPresent, rawInstanceId);
}

CaptureIdentityArena::CaptureIdentityArena() noexcept
    : source_{nullptr, identity::SystemRandomBytes},
      captionProvenance_(NextCaptionCaptureProvenance()) {}

CaptureIdentityArena::CaptureIdentityArena(
    const identity::UuidSource source) noexcept
    : source_(source),
      captionProvenance_(NextCaptionCaptureProvenance()) {}

CaptureIdentityArena::CaptureIdentityArena(
    CloneTag, const CaptureIdentityArena& source)
    : source_(source.source_),
      captionProvenance_(source.captionProvenance_),
      entries_(source.entries_),
      seededKeys_(source.seededKeys_),
      usedKeys_(source.usedKeys_),
      remapCount_(source.remapCount_),
      ambiguousRemaps_(source.ambiguousRemaps_),
      tombstoneCount_(source.tombstoneCount_),
      receipt_(source.receipt_),
      frozenPlan_(source.frozenPlan_),
      replayPass_(source.replayPass_) {}

std::unique_ptr<CaptureIdentityArena>
CaptureIdentityArena::CloneForLocalBuild() const noexcept {
    try {
        return std::unique_ptr<CaptureIdentityArena>(
            new CaptureIdentityArena(CloneTag{}, *this));
    } catch (...) {
        return nullptr;
    }
}

void CaptureIdentityArena::ResetSession() noexcept {
    captionProvenance_ = NextCaptionCaptureProvenance();
    entries_.clear();
    seededKeys_.clear();
    usedKeys_.clear();
    remapCount_ = 0;
    ambiguousRemaps_.clear();
    tombstoneCount_ = 0;
    receipt_ = {};
    replayPass_ = false;
}

bool CaptureIdentityArena::Acquire(
    const std::wstring& key,
    NodeId* const id) noexcept {
    if (id == nullptr || key.empty()) {
        return false;
    }
    try {
        const auto rememberUse = [this, &key]() {
            if (std::find(usedKeys_.begin(), usedKeys_.end(), key) ==
                usedKeys_.end()) {
                usedKeys_.push_back(key);
            }
        };
        if (key == L"document") {
            usedKeys_.clear();
            if (!replayPass_) {
                seededKeys_.clear();
                receipt_ = {};
                ambiguousRemaps_.clear();
                remapCount_ = 0;
                tombstoneCount_ = 0;
                for (const auto& entry : entries_) {
                    seededKeys_.push_back(entry.first);
                }
            }
        }
        if (key.rfind(L"control-occurrence:", 0) == 0) {
            const auto found = std::find_if(
                entries_.rbegin(), entries_.rend(),
                [&key](const auto& entry) { return entry.first == key; });
            if (replayPass_) {
                if (found == entries_.rend()) {
                    return false;
                }
                *id = found->second;
                rememberUse();
                return true;
            }
            const size_t occurrenceSeparator = key.rfind(L':');
            if (occurrenceSeparator == std::wstring::npos ||
                occurrenceSeparator <= 19) {
                return false;
            }
            NodeId minted;
            bool distinct = false;
            const bool injected = UsesInjectedUuidSource(source_);
            for (size_t attempt = 0; attempt < 128 && !distinct; ++attempt) {
                if (injected
                        ? !identity::MintUuidV4(source_, &minted)
                        : !DeterministicNodeId(
                              key, static_cast<std::uint64_t>(attempt),
                              &minted)) {
                    return false;
                }
                distinct = std::none_of(
                    entries_.begin(), entries_.end(),
                    [&minted](const auto& entry) {
                        return entry.second.bytes == minted.bytes;
                    });
            }
            if (!distinct) {
                return false;
            }
            identity::RemapEntry ambiguous;
            ambiguous.source = minted;
            ambiguous.targetPresent = true;
            ambiguous.target = minted;
            ambiguous.disposition = RemapDisposition::New;
            ambiguous.reason = RemapReason::Ambiguous;
            entries_.emplace_back(key, minted);
            ambiguousRemaps_.push_back(ambiguous);
            receipt_.remaps.push_back(ambiguous);
            *id = minted;
            ++remapCount_;
            return true;
        }
        if (key == L"document" || entries_.empty()) {
            for (const auto& entry : entries_) {
                if (entry.first == key) {
                    *id = entry.second;
                    rememberUse();
                    return true;
                }
            }
            NodeId minted;
            if (UsesInjectedUuidSource(source_)
                    ? !identity::MintUuidV4(source_, &minted)
                    : !DeterministicNodeId(key, 0, &minted)) {
                return false;
            }
            entries_.emplace_back(key, minted);
            *id = minted;
            rememberUse();
            return true;
        }
        NodeKind kind = NodeKind::Document;
        if (key.rfind(L"control:", 0) == 0) {
            const size_t end = key.find(L':', 8);
            if (end == std::wstring::npos) return false;
            kind = static_cast<NodeKind>(std::stoul(key.substr(8, end - 8)));
        } else if (key.rfind(L"control-occurrence:", 0) == 0) {
            constexpr size_t prefix = 19;
            const size_t end = key.find(L':', prefix);
            if (end == std::wstring::npos) return false;
            kind = static_cast<NodeKind>(
                std::stoul(key.substr(prefix, end - prefix)));
        } else if (key.rfind(L"control-empty-instance:", 0) == 0) {
            constexpr size_t prefix = 23;
            const size_t end = key.find(L':', prefix);
            if (end == std::wstring::npos) return false;
            kind = static_cast<NodeKind>(
                std::stoul(key.substr(prefix, end - prefix)));
        } else if (key.rfind(L"cell:", 0) == 0) {
            kind = NodeKind::TableCell;
        } else if (key.rfind(L"native:", 0) == 0) {
            const size_t end = key.find(L':', 7);
            if (end == std::wstring::npos) return false;
            kind = static_cast<NodeKind>(std::stoul(key.substr(7, end - 7)));
        } else if (key.rfind(L"section:", 0) == 0) {
            kind = NodeKind::Section;
        } else if (key.rfind(L"paragraph-story:", 0) == 0) {
            kind = NodeKind::Paragraph;
        } else if (key.rfind(L"definition:", 0) == 0) {
            kind = NodeKind::Definition;
        }
        std::vector<identity::ReconcileCandidate> existing;
        for (const auto& entry : entries_) {
            if (entry.first == key) {
                identity::ReconcileCandidate candidate;
                candidate.id = entry.second;
                candidate.kind = kind;
                candidate.primaryKey = key;
                candidate.topologyUnchanged = true;
                existing.push_back(std::move(candidate));
            }
        }
        identity::ReconcileCandidate fresh;
        fresh.kind = kind;
        fresh.primaryKey = key;
        fresh.topologyUnchanged = true;
        identity::ReconcileResult reconciled;
        if (!identity::ReconcileChildren(
                entries_.front().second,
                existing,
                {fresh},
                identity::ReconcileMode::External,
                source_,
                {},
                &reconciled) ||
            reconciled.nodes.size() != 1) {
            return false;
        }
        *id = existing.empty()
            ? reconciled.nodes.front().id
            : existing.front().id;
        remapCount_ += reconciled.receipt.remaps.size();
        if (existing.empty()) {
            entries_.emplace_back(key, *id);
            receipt_.remaps.insert(
                receipt_.remaps.end(), reconciled.receipt.remaps.begin(),
                reconciled.receipt.remaps.end());
            receipt_.tombstones.insert(
                receipt_.tombstones.end(), reconciled.receipt.tombstones.begin(),
                reconciled.receipt.tombstones.end());
        }
        rememberUse();
        return true;
    } catch (...) {
        return false;
    }
}

bool CaptureIdentityArena::AcquireParagraph(
    const NodeId& owningStory,
    const NativePosition& canonicalStart,
    NodeId* const id) noexcept {
    if (!identity::IsRfc4122V4(owningStory) ||
        canonicalStart.list < 0 || canonicalStart.paragraph < 0) {
        return false;
    }
    try {
        return Acquire(
            ParagraphIdentityKey(
                owningStory, ParagraphLocator(canonicalStart)),
            id);
    } catch (...) {
        return false;
    }
}

bool CaptureIdentityArena::Seed(
    const std::wstring& key,
    const NodeId& id) noexcept {
    if (key.empty() || !identity::IsRfc4122V4(id)) {
        return false;
    }
    try {
        for (const auto& entry : entries_) {
            if (entry.first == key) {
                return entry.second.bytes == id.bytes;
            }
            if (entry.second.bytes == id.bytes && entry.first != key) {
                return false;
            }
        }
        entries_.emplace_back(key, id);
        if (std::find(seededKeys_.begin(), seededKeys_.end(), key) ==
            seededKeys_.end()) {
            seededKeys_.push_back(key);
        }
        return true;
    } catch (...) {
        return false;
    }
}

bool CaptureIdentityArena::FinalizeReconciliation() noexcept {
    if (entries_.empty()) {
        return false;
    }
    if (replayPass_) {
        replayPass_ = false;
        return true;
    }
    try {
        for (const std::wstring& key : seededKeys_) {
            if (std::find(usedKeys_.begin(), usedKeys_.end(), key) !=
                usedKeys_.end()) {
                continue;
            }
            const auto entry = std::find_if(
                entries_.begin(), entries_.end(),
                [&key](const auto& candidate) {
                    return candidate.first == key;
                });
            if (entry == entries_.end() || key == L"document") {
                return false;
            }
            identity::ReconcileCandidate old;
            old.id = entry->second;
            old.primaryKey = key;
            old.kind = key.rfind(L"cell:", 0) == 0
                ? NodeKind::TableCell
                : key.rfind(L"definition:", 0) == 0
                    ? NodeKind::Definition
                    : NodeKind::GenericControl;
            old.topologyUnchanged = true;
            identity::ReconcileResult reconciled;
            if (!identity::ReconcileChildren(
                    entries_.front().second,
                    {old},
                    {},
                    identity::ReconcileMode::External,
                    source_,
                    {},
                    &reconciled)) {
                return false;
            }
            if (key.rfind(L"control-occurrence:", 0) == 0) {
                for (auto& remap : reconciled.receipt.remaps) {
                    remap.disposition = RemapDisposition::Ambiguous;
                    remap.reason = RemapReason::Ambiguous;
                }
            }
            // A later-attempt deletion supersedes this session's original New
            // remap. Emitting both for one NodeId is an illegal stream state.
            for (const auto& tombstone : reconciled.receipt.tombstones) {
                receipt_.remaps.erase(
                    std::remove_if(
                        receipt_.remaps.begin(), receipt_.remaps.end(),
                        [&tombstone](const auto& remap) {
                            return remap.source.bytes == tombstone.node.bytes;
                        }),
                    receipt_.remaps.end());
            }
            receipt_.remaps.insert(
                receipt_.remaps.end(), reconciled.receipt.remaps.begin(),
                reconciled.receipt.remaps.end());
            receipt_.tombstones.insert(
                receipt_.tombstones.end(), reconciled.receipt.tombstones.begin(),
                reconciled.receipt.tombstones.end());
            remapCount_ += reconciled.receipt.remaps.size();
            tombstoneCount_ += reconciled.receipt.tombstones.size();
        }
        return true;
    } catch (...) {
        return false;
    }
}

bool BuildTypedRecordPlan(
    const std::vector<ReaderPayload>& payloads,
    CaptureIdentityArena& arena,
    TypedRecordPlanPin* const output,
    std::vector<RecordBlob>* const blobs,
    std::wstring* const failure) {
    if (output == nullptr || blobs == nullptr ||
        payloads.size() != kQualifiedReaderCount) return false;
    try {
        BeginTypedProfile();
        auto plan = std::make_shared<FrozenTypedPlan>();
        // Build directly from the attempt's immutable observations. Copying
        // every observation into the plan before reducing it duplicated the
        // largest typed pre-pass allocation; only the compact coverage tuple
        // is retained below for deterministic emission.
        if (!BuildGraph(payloads, arena, &plan->graph, failure) ||
            plan->graph.nodes.empty()) return false;
        {
            TypedPhaseClock profile(TypedBuildSubstage::RecordIdAssignment);
            AssignRecordIds(
                &plan->graph, 0, &plan->recordCount, &plan->coverageIds,
                &plan->binaryCoverageId, payloads);
        }
        {
            TypedPhaseClock profile(TypedBuildSubstage::PayloadFinalization);
            FinalizePayloads(&plan->graph, payloads);
        }
        // Emission only needs the reader/profile coverage tuple. Drop the
        // independently observed payload bodies after they have been reduced
        // into the immutable graph so mismatch retention is compact rather
        // than a second copy of the attempt's observations.
        std::vector<ReaderPayload> compactPayloads;
        compactPayloads.reserve(payloads.size());
        for (const ReaderPayload& payload : payloads) {
            ReaderPayload compact;
            compact.reader = payload.reader;
            compact.coverage = payload.coverage;
            compactPayloads.push_back(std::move(compact));
        }
        plan->payloads = std::move(compactPayloads);
        const identity::IdentityReceipt& receipt = arena.Receipt();
        plan->tombstones = receipt.tombstones;
        plan->remaps = receipt.remaps;
        std::sort(plan->tombstones.begin(), plan->tombstones.end(),
                  [](const auto& left, const auto& right) {
                      if (left.node.bytes != right.node.bytes)
                          return left.node.bytes < right.node.bytes;
                      if (left.semanticRevision != right.semanticRevision)
                          return left.semanticRevision < right.semanticRevision;
                      return left.reason < right.reason;
                  });
        std::sort(plan->remaps.begin(), plan->remaps.end(),
                  [](const auto& left, const auto& right) {
                      if (left.source.bytes != right.source.bytes)
                          return left.source.bytes < right.source.bytes;
                      if (left.disposition != right.disposition)
                          return left.disposition < right.disposition;
                      if (left.targetPresent != right.targetPresent)
                          return left.targetPresent < right.targetPresent;
                      if (left.target.bytes != right.target.bytes)
                          return left.target.bytes < right.target.bytes;
                      return left.reason < right.reason;
                  });
        plan->tombstones.erase(
            std::unique(plan->tombstones.begin(), plan->tombstones.end(),
                        [](const auto& left, const auto& right) {
                            return left.node.bytes == right.node.bytes &&
                                left.semanticRevision == right.semanticRevision &&
                                left.reason == right.reason;
                        }), plan->tombstones.end());
        plan->remaps.erase(
            std::unique(plan->remaps.begin(), plan->remaps.end(),
                        [](const auto& left, const auto& right) {
                            return left.source.bytes == right.source.bytes &&
                                left.targetPresent == right.targetPresent &&
                                left.target.bytes == right.target.bytes &&
                                left.disposition == right.disposition &&
                                left.reason == right.reason;
                        }), plan->remaps.end());
        *blobs = plan->graph.blobs;
        *output = std::static_pointer_cast<void>(plan);
        if (failure != nullptr) failure->clear();
        return true;
    } catch (...) {
        return false;
    }
}

bool EmitTypedRecordPlan(
    const TypedRecordPlanPin& opaque,
    const codec::CanonicalizationResult* const canonical,
    const Sha256& indexDigest,
    const TypedRecordSink sink,
    void* const sinkContext,
    std::uint64_t* const recordCount,
    std::uint64_t* const byteLength,
    std::wstring* const failure) noexcept {
    if (opaque == nullptr || sink == nullptr) return false;
    try {
        const auto plan = std::static_pointer_cast<FrozenTypedPlan>(opaque);
        FingerprintIndex fingerprints;
        if (canonical != nullptr) {
            for (const auto& fingerprint : canonical->nodeFingerprints) {
                if (!fingerprints.emplace(
                        fingerprint.nodeId.bytes,
                        fingerprint.fingerprint).second) return false;
            }
        }
        struct SinkState final {
            TypedRecordSink sink = nullptr;
            void* context = nullptr;
            std::uint64_t records = 0;
            std::uint64_t bytes = 0;
        } state{sink, sinkContext};
        const auto emit = [](void* raw, const Bytes& record) noexcept {
            auto* const state = static_cast<SinkState*>(raw);
            if (state == nullptr || record.empty() ||
                !state->sink(state->context, codec::View(record))) return false;
            ++state->records;
            state->bytes += record.size();
            return true;
        };
        RecordId next = plan->recordCount;
        const std::uint64_t total = next + plan->tombstones.size() +
            plan->remaps.size();
        if (!emit(&state, ManifestRecordBytes(total, indexDigest, canonical)) ||
            !EmitNode(plan->graph, 0,
                      canonical == nullptr ? nullptr : &fingerprints,
                      plan->coverageIds, plan->binaryCoverageId,
                      plan->payloads, emit, &state)) return false;
        for (const auto& tombstone : plan->tombstones) {
            if (!emit(&state, TombstoneRecordBytes(next++, tombstone)))
                return false;
        }
        for (const auto& remap : plan->remaps) {
            if (!emit(&state, RemapRecordBytes(next++, remap))) return false;
        }
        if (state.records != total) return false;
        if (recordCount != nullptr) *recordCount = state.records;
        if (byteLength != nullptr) *byteLength = state.bytes;
        if (failure != nullptr) failure->clear();
        return true;
    } catch (...) {
        return false;
    }
}

bool BuildTypedRecordStream(
    const std::vector<ReaderPayload>& payloads,
    CaptureIdentityArena& arena,
    const codec::CanonicalizationResult* const canonical,
    const Sha256& indexDigest,
    Bytes* const records,
    std::vector<RecordBlob>* const blobs,
    std::wstring* const failure) {
    if (records == nullptr || blobs == nullptr ||
        payloads.size() != kQualifiedReaderCount) {
        return false;
    }
    try {
        BeginTypedProfile();
        const auto typedStage = [failure](const wchar_t* const value) {
            if (failure != nullptr) *failure = L"typed:" + std::wstring(value);
        };
        RecordCaptureProgressPoint(
            CaptureProgressPoint::TypedBuildStart,
            payloads.size(), canonical == nullptr ? 0 : 1);
        std::shared_ptr<FrozenTypedPlan> plan = gTypedGraphReuseEnabled
            ? std::static_pointer_cast<FrozenTypedPlan>(arena.FrozenPlan())
            : nullptr;
        if (plan == nullptr) {
            plan = std::make_shared<FrozenTypedPlan>();
            if (!BuildGraph(payloads, arena, &plan->graph, failure) ||
                plan->graph.nodes.empty()) {
                return false;
            }
            typedStage(L"assign-record-ids");
            RecordCaptureProgressPoint(
                CaptureProgressPoint::TypedRecordIdAssignment,
                plan->graph.nodes.size(), plan->graph.blobs.size());
            {
                TypedPhaseClock profile(TypedBuildSubstage::RecordIdAssignment);
                AssignRecordIds(
                    &plan->graph, 0, &plan->recordCount,
                    &plan->coverageIds, &plan->binaryCoverageId, payloads);
            }
            typedStage(L"finalize-payloads");
            RecordCaptureProgressPoint(
                CaptureProgressPoint::TypedPayloadFinalization,
                plan->graph.nodes.size(), plan->recordCount);
            {
                TypedPhaseClock profile(TypedBuildSubstage::PayloadFinalization);
                FinalizePayloads(&plan->graph, payloads);
            }
            const identity::IdentityReceipt& receipt = arena.Receipt();
            plan->tombstones = receipt.tombstones;
            plan->remaps = receipt.remaps;
            std::sort(
                plan->tombstones.begin(), plan->tombstones.end(),
                [](const auto& left, const auto& right) {
                    if (left.node.bytes != right.node.bytes)
                        return left.node.bytes < right.node.bytes;
                    if (left.semanticRevision != right.semanticRevision)
                        return left.semanticRevision < right.semanticRevision;
                    return left.reason < right.reason;
                });
            std::sort(
                plan->remaps.begin(), plan->remaps.end(),
                [](const auto& left, const auto& right) {
                    if (left.source.bytes != right.source.bytes)
                        return left.source.bytes < right.source.bytes;
                    if (left.disposition != right.disposition)
                        return left.disposition < right.disposition;
                    if (left.targetPresent != right.targetPresent)
                        return left.targetPresent < right.targetPresent;
                    if (left.target.bytes != right.target.bytes)
                        return left.target.bytes < right.target.bytes;
                    return left.reason < right.reason;
                });
            plan->tombstones.erase(
                std::unique(
                    plan->tombstones.begin(), plan->tombstones.end(),
                    [](const auto& left, const auto& right) {
                        return left.node.bytes == right.node.bytes &&
                            left.semanticRevision == right.semanticRevision &&
                            left.reason == right.reason;
                    }),
                plan->tombstones.end());
            plan->remaps.erase(
                std::unique(
                    plan->remaps.begin(), plan->remaps.end(),
                    [](const auto& left, const auto& right) {
                        return left.source.bytes == right.source.bytes &&
                            left.targetPresent == right.targetPresent &&
                            left.target.bytes == right.target.bytes &&
                            left.disposition == right.disposition &&
                            left.reason == right.reason;
                    }),
                plan->remaps.end());
            *blobs = plan->graph.blobs;
            if (gTypedGraphReuseEnabled) arena.SetFrozenPlan(plan);
        }

        typedStage(L"emit-node");
        RecordCaptureProgressPoint(
            CaptureProgressPoint::TypedEncodingRewrite,
            plan->graph.nodes.size(), plan->recordCount);
        TypedPhaseClock emissionProfile(TypedBuildSubstage::EmissionRewrite);
        FingerprintIndex fingerprints;
        if (canonical != nullptr) {
            for (const codec::NodeFingerprintResult& fingerprint :
                 canonical->nodeFingerprints) {
                if (!fingerprints.emplace(
                        fingerprint.nodeId.bytes,
                        fingerprint.fingerprint).second)
                    return false;
            }
        }
        Bytes body;
        const auto appendRecord = [](void* raw, const Bytes& record) noexcept {
            try {
                Add(static_cast<Bytes*>(raw), record);
                return true;
            } catch (...) {
                return false;
            }
        };
        if (!EmitNode(
                plan->graph, 0,
                canonical == nullptr ? nullptr : &fingerprints,
                plan->coverageIds, plan->binaryCoverageId, payloads,
                appendRecord, &body)) return false;
        typedStage(L"identity-receipt");
        RecordId next = plan->recordCount;
        for (const identity::TombstoneEntry& tombstone : plan->tombstones)
            Add(&body, TombstoneRecordBytes(next++, tombstone));
        for (const identity::RemapEntry& remap : plan->remaps)
            Add(&body, RemapRecordBytes(next++, remap));
        typedStage(L"manifest");
        Bytes stream = ManifestRecordBytes(next, indexDigest, canonical);
        typedStage(L"manifest-add-body");
        Add(&stream, body);
        typedStage(L"publish-records");
        *records = std::move(stream);
        typedStage(L"complete");
        RecordCaptureProgressPoint(
            CaptureProgressPoint::TypedBuildEnd,
            records->size(), plan->graph.blobs.size());
        if (gTypedProfilesEnabled &&
            gTypedProfileIndex < gTypedProfiles.size()) {
            gTypedProfiles[gTypedProfileIndex].nodes = plan->graph.nodes.size();
            gTypedProfiles[gTypedProfileIndex].recordsBytes = records->size();
        }
        if (failure != nullptr) failure->clear();
        return true;
    } catch (...) {
        return false;
    }
}

bool BuildCanonicalRecordPatches(
    const codec::ByteView records,
    const codec::CanonicalizationResult& canonical,
    std::vector<CanonicalRecordPatch>* const patches,
    std::wstring* const failure) {
    if (records.data == nullptr || patches == nullptr ||
        !canonical.rootsPresent) {
        return false;
    }
    patches->clear();
    const auto fail = [failure](const wchar_t* const detail) {
        if (failure != nullptr) *failure = L"patch:" + std::wstring(detail);
        return false;
    };
    if (!canonical.patchDescriptors.empty()) {
        std::map<std::array<std::uint8_t, 16>, Sha256> fingerprints;
        for (const codec::NodeFingerprintResult& item :
             canonical.nodeFingerprints) {
            if (!fingerprints.emplace(
                    item.nodeId.bytes, item.fingerprint).second) {
                return fail(L"duplicate-fingerprint");
            }
        }
        bool manifestPatched = false;
        size_t patchedFingerprints = 0;
        for (const codec::CanonicalPatchDescriptor& descriptor :
             canonical.patchDescriptors) {
            if (descriptor.valueOffset < 24 ||
                descriptor.valueOffset > records.size ||
                descriptor.valueBytes > records.size - descriptor.valueOffset) {
                return fail(L"descriptor-bounds");
            }
            const std::uint64_t header = descriptor.valueOffset - 24;
            if (Read16(records.data + header) != descriptor.fieldTag ||
                static_cast<ScalarTag>(Read16(records.data + header + 4)) !=
                    descriptor.scalar ||
                Read64(records.data + header + 16) != descriptor.valueBytes) {
                return fail(L"descriptor-field");
            }
            if (descriptor.manifestVersion) {
                if (manifestPatched || descriptor.fieldTag != 1 ||
                    descriptor.scalar != ScalarTag::Struct ||
                    descriptor.valueBytes != kGraphVersionBytesV1) {
                    return fail(L"manifest-version");
                }
                Bytes seal(
                    records.data + descriptor.valueOffset,
                    records.data + descriptor.valueOffset +
                        descriptor.valueBytes);
                seal[10] = static_cast<std::uint8_t>(
                    canonical.semanticCertified);
                seal[11] = static_cast<std::uint8_t>(canonical.layoutPresent);
                std::copy(canonical.observedSemanticRoot.bytes.begin(),
                          canonical.observedSemanticRoot.bytes.end(),
                          seal.begin() + 72);
                std::copy(canonical.layoutRoot.bytes.begin(),
                          canonical.layoutRoot.bytes.end(), seal.begin() + 104);
                std::copy(canonical.captureRoot.bytes.begin(),
                          canonical.captureRoot.bytes.end(), seal.begin() + 136);
                patches->push_back({descriptor.valueOffset, std::move(seal)});
                manifestPatched = true;
                continue;
            }
            if (descriptor.fieldTag != 8 ||
                descriptor.scalar != ScalarTag::SHA256 ||
                descriptor.valueBytes != 32) {
                return fail(L"node-descriptor");
            }
            const auto found = fingerprints.find(descriptor.nodeId.bytes);
            if (found == fingerprints.end()) {
                return fail(L"missing-fingerprint");
            }
            patches->push_back({
                descriptor.valueOffset,
                Bytes(found->second.bytes.begin(), found->second.bytes.end())});
            ++patchedFingerprints;
        }
        if (!manifestPatched || patchedFingerprints != fingerprints.size() ||
            canonical.patchDescriptors.size() != fingerprints.size() + 1) {
            return fail(L"cardinality");
        }
        if (failure != nullptr) failure->clear();
        return true;
    }
    struct LocatedField final {
        std::uint64_t valueOffset = 0;
        std::uint64_t valueBytes = 0;
        ScalarTag scalar = ScalarTag::Bytes;
    };
    const auto locate = [&records](
        const std::uint64_t begin, const std::uint64_t length,
        const FieldTag wanted, LocatedField* const output) {
        if (output == nullptr || begin > records.size ||
            length > records.size - begin) return false;
        std::uint64_t at = begin;
        const std::uint64_t end = begin + length;
        while (at != end) {
            if (end - at < 24) return false;
            const std::uint64_t valueBytes = Read64(records.data + at + 16);
            if (valueBytes > end - at - 24) return false;
            const FieldTag tag = Read16(records.data + at);
            if (tag == wanted) {
                output->valueOffset = at + 24;
                output->valueBytes = valueBytes;
                output->scalar = static_cast<ScalarTag>(
                    Read16(records.data + at + 4));
                return true;
            }
            at += 24 + valueBytes;
        }
        return false;
    };
    std::map<std::array<std::uint8_t, 16>, Sha256> fingerprints;
    for (const codec::NodeFingerprintResult& item :
         canonical.nodeFingerprints) {
        if (!fingerprints.emplace(item.nodeId.bytes, item.fingerprint).second)
            return fail(L"duplicate-fingerprint");
    }
    bool manifestPatched = false;
    std::size_t patchedFingerprints = 0;
    std::uint64_t recordOffset = 0;
    while (recordOffset != records.size) {
        if (records.size - recordOffset < kLogicalRecordHeaderBytesV1)
            return fail(L"record-header");
        const std::uint64_t payloadBytes =
            Read64(records.data + recordOffset + 16);
        if (payloadBytes > records.size - recordOffset -
                               kLogicalRecordHeaderBytesV1)
            return fail(L"record-length");
        const auto kind = static_cast<RecordKind>(
            Read16(records.data + recordOffset));
        const std::uint64_t fieldsAt =
            recordOffset + kLogicalRecordHeaderBytesV1;
        if (kind == RecordKind::Manifest) {
            LocatedField version{};
            if (manifestPatched ||
                !locate(fieldsAt, payloadBytes, 1, &version) ||
                version.scalar != ScalarTag::Struct ||
                version.valueBytes != kGraphVersionBytesV1)
                return fail(L"manifest-version");
            Bytes seal(records.data + version.valueOffset,
                       records.data + version.valueOffset +
                           version.valueBytes);
            seal[10] = static_cast<std::uint8_t>(
                canonical.semanticCertified);
            seal[11] = static_cast<std::uint8_t>(canonical.layoutPresent);
            std::copy(canonical.observedSemanticRoot.bytes.begin(),
                      canonical.observedSemanticRoot.bytes.end(),
                      seal.begin() + 72);
            std::copy(canonical.layoutRoot.bytes.begin(),
                      canonical.layoutRoot.bytes.end(), seal.begin() + 104);
            std::copy(canonical.captureRoot.bytes.begin(),
                      canonical.captureRoot.bytes.end(), seal.begin() + 136);
            patches->push_back({version.valueOffset, std::move(seal)});
            manifestPatched = true;
        } else if (kind == RecordKind::Node) {
            LocatedField common{};
            if (!locate(fieldsAt, payloadBytes, 1, &common) ||
                common.scalar != ScalarTag::Struct)
                return fail(L"node-common");
            LocatedField nodeId{}, fingerprint{};
            if (!locate(common.valueOffset, common.valueBytes, 1, &nodeId) ||
                !locate(common.valueOffset, common.valueBytes, 8,
                        &fingerprint) ||
                nodeId.scalar != ScalarTag::UUID128 ||
                nodeId.valueBytes != 16 ||
                fingerprint.scalar != ScalarTag::SHA256 ||
                fingerprint.valueBytes != 32)
                return fail(L"node-descriptor");
            std::array<std::uint8_t, 16> id{};
            std::copy_n(records.data + nodeId.valueOffset, id.size(),
                        id.begin());
            const auto found = fingerprints.find(id);
            if (found == fingerprints.end())
                return fail(L"missing-fingerprint");
            patches->push_back({
                fingerprint.valueOffset,
                Bytes(found->second.bytes.begin(),
                      found->second.bytes.end()),
            });
            ++patchedFingerprints;
        }
        recordOffset += kLogicalRecordHeaderBytesV1 + payloadBytes;
    }
    if (!manifestPatched || patchedFingerprints != fingerprints.size())
        return fail(L"cardinality");
    if (failure != nullptr) failure->clear();
    return true;
}

bool SeedArenaFromRecordStream(
    const codec::ByteView records,
    CaptureIdentityArena* const arena) {
    if (arena == nullptr || records.data == nullptr) {
        return false;
    }
    try {
        std::map<std::wstring, size_t> controlInstanceCounts;
        std::uint64_t countOffset = 0;
        while (countOffset != records.size) {
            if (records.size - countOffset < 24) return false;
            const std::uint8_t* const header = records.data + countOffset;
            const std::uint64_t payloadBytes = Read64(header + 16);
            if (payloadBytes > records.size - countOffset - 24) return false;
            if (static_cast<RecordKind>(Read16(header)) == RecordKind::Node) {
                std::vector<FieldView> outer;
                std::vector<FieldView> common;
                std::vector<FieldView> nodePayload;
                if (!Fields({header + 24, payloadBytes}, &outer) ||
                    Find(outer, 1) == nullptr || Find(outer, 2) == nullptr ||
                    !Fields(Find(outer, 1)->value, &common) ||
                    !Fields(Find(outer, 2)->value, &nodePayload)) {
                    return false;
                }
                const FieldView* const kindField = Find(common, 2);
                const FieldView* const ctrlId = Find(nodePayload, 100);
                const FieldView* const instance = Find(nodePayload, 101);
                if (kindField != nullptr && kindField->value.size == 2) {
                    const NodeKind kind = static_cast<NodeKind>(
                        Read16(kindField->value.data));
                    if (kind != NodeKind::GenericControl &&
                        kind != NodeKind::Table && kind != NodeKind::Image) {
                        countOffset += 24 + payloadBytes;
                        continue;
                    }
                    if (ctrlId == nullptr || ctrlId->value.size < 24 ||
                        instance == nullptr || instance->value.size < 24) {
                        return false;
                    }
                    const std::uint64_t ctrlDetail =
                        Read64(ctrlId->value.data + 8);
                    const std::uint64_t ctrlSize =
                        Read64(ctrlId->value.data + 16);
                    const std::uint64_t ctrlStart = 24 + ctrlDetail * 2;
                    if (ctrlId->value.data[0] != static_cast<std::uint8_t>(
                            ObservationState::Value) ||
                        ctrlStart > ctrlId->value.size ||
                        ctrlSize != ctrlId->value.size - ctrlStart) {
                        return false;
                    }
                    const std::uint8_t instanceState =
                        instance->value.data[0];
                    bool instancePresent = false;
                    std::wstring instanceValue;
                    if (!DecodeControlInstanceObservation(
                            *instance, &instancePresent, &instanceValue) ||
                        (!instancePresent && instanceState !=
                            static_cast<std::uint8_t>(
                                ObservationState::NotExposed))) {
                        return false;
                    }
                    const std::wstring ctrlValue = DecodeUtf16(
                        {ctrlId->value.data + ctrlStart, ctrlSize});
                    ++controlInstanceCounts[ControlCompleteKey(
                        kind, ctrlValue, instancePresent, instanceValue)];
                }
            }
            countOffset += 24 + payloadBytes;
        }
        std::uint64_t offset = 0;
        size_t seeded = 0;
        std::map<std::wstring, std::uint64_t> controlOccurrences;
        std::map<std::wstring, NodeId> owningStoryByNode;
        std::map<std::wstring, NodeId> tableOwnerScopeByNode;
        std::map<std::wstring, NodeKind> kindByNode;
        while (offset != records.size) {
            if (records.size - offset < 24) {
                return false;
            }
            const std::uint8_t* const header = records.data + offset;
            const std::uint64_t payloadBytes = Read64(header + 16);
            if (payloadBytes > records.size - offset - 24) {
                return false;
            }
            if (static_cast<RecordKind>(Read16(header)) == RecordKind::Node) {
                std::vector<FieldView> outer;
                if (!Fields({header + 24, payloadBytes}, &outer)) {
                    return false;
                }
                const FieldView* const commonValue = Find(outer, 1);
                const FieldView* const payloadValue = Find(outer, 2);
                std::vector<FieldView> common;
                std::vector<FieldView> nodePayload;
                if (commonValue == nullptr || payloadValue == nullptr ||
                    !Fields(commonValue->value, &common) ||
                    !Fields(payloadValue->value, &nodePayload)) {
                    return false;
                }
                const FieldView* const idField = Find(common, 1);
                const FieldView* const kindField = Find(common, 2);
                const FieldView* const locatorField = Find(common, 7);
                if (idField == nullptr || idField->value.size != 16 ||
                    kindField == nullptr || kindField->value.size != 2 ||
                    locatorField == nullptr || locatorField->value.size < 24) {
                    return false;
                }
                NodeId id;
                std::copy_n(idField->value.data, 16, id.bytes.begin());
                const NodeKind kind =
                    static_cast<NodeKind>(Read16(kindField->value.data));
                const FieldView* const parentField = Find(common, 3);
                const std::wstring nodeKey = Hex(UuidValue(id));
                kindByNode[nodeKey] = kind;
                if (kind == NodeKind::Story) {
                    owningStoryByNode[nodeKey] = id;
                    tableOwnerScopeByNode[nodeKey] = id;
                    if (parentField != nullptr && parentField->value.size == 16) {
                        const Bytes parentBytes(
                            parentField->value.data,
                            parentField->value.data + parentField->value.size);
                        const std::wstring parentKey = Hex(parentBytes);
                        const auto parentKind = kindByNode.find(parentKey);
                        if (parentKind != kindByNode.end() &&
                            parentKind->second == NodeKind::TableCell) {
                            NodeId parent;
                            std::copy_n(
                                parentField->value.data, 16,
                                parent.bytes.begin());
                            tableOwnerScopeByNode[nodeKey] = parent;
                        }
                    }
                } else if (parentField != nullptr &&
                           parentField->value.size == 16) {
                    const Bytes parentBytes(
                        parentField->value.data,
                        parentField->value.data + parentField->value.size);
                    const std::wstring parentKey = Hex(parentBytes);
                    const auto owner = owningStoryByNode.find(parentKey);
                    if (owner != owningStoryByNode.end()) {
                        owningStoryByNode[nodeKey] = owner->second;
                    }
                    const auto scope = tableOwnerScopeByNode.find(parentKey);
                    if (scope != tableOwnerScopeByNode.end()) {
                        tableOwnerScopeByNode[nodeKey] = scope->second;
                    }
                }
                std::wstring key;
                if (kind == NodeKind::Document) {
                    key = L"document";
                } else if (kind == NodeKind::Story) {
                    const std::uint8_t state = locatorField->value.data[0];
                    const std::uint64_t detail =
                        Read64(locatorField->value.data + 8);
                    const std::uint64_t size =
                        Read64(locatorField->value.data + 16);
                    const std::uint64_t start = 24 + detail * 2;
                    if (parentField == nullptr ||
                        parentField->value.size != 16 ||
                        state != static_cast<std::uint8_t>(
                            ObservationState::Value) ||
                        start > locatorField->value.size ||
                        size != locatorField->value.size - start) {
                        return false;
                    }
                    NodeId owner;
                    std::copy_n(
                        parentField->value.data, 16, owner.bytes.begin());
                    const Bytes locator(
                        locatorField->value.data + start,
                        locatorField->value.data + start + size);
                    key = StoryIdentityKey(owner, locator);
                } else if (kind == NodeKind::Section) {
                    const FieldView* const ordinal = Find(nodePayload, 100);
                    if (ordinal == nullptr || ordinal->value.size != 8) {
                        return false;
                    }
                    key = L"section:" +
                        std::to_wstring(Read64(ordinal->value.data));
                } else if (kind == NodeKind::Paragraph) {
                    const auto owner = owningStoryByNode.find(nodeKey);
                    const std::uint8_t state = locatorField->value.data[0];
                    const std::uint64_t detail =
                        Read64(locatorField->value.data + 8);
                    const std::uint64_t size =
                        Read64(locatorField->value.data + 16);
                    const std::uint64_t start = 24 + detail * 2;
                    if (owner == owningStoryByNode.end() ||
                        state != static_cast<std::uint8_t>(
                            ObservationState::Value) ||
                        start > locatorField->value.size ||
                        size != locatorField->value.size - start) {
                        return false;
                    }
                    const Bytes locator(
                        locatorField->value.data + start,
                        locatorField->value.data + start + size);
                    key = ParagraphIdentityKey(owner->second, locator);
                } else if (kind == NodeKind::Definition) {
                    const FieldView* const definitionKind =
                        Find(nodePayload, 100);
                    const FieldView* const nativeId = Find(nodePayload, 101);
                    const FieldView* const aggregate = Find(nodePayload, 103);
                    if (definitionKind == nullptr ||
                        definitionKind->value.size != 2 ||
                        nativeId == nullptr || nativeId->value.size < 24 ||
                        aggregate == nullptr || aggregate->value.size != 32) {
                        return false;
                    }
                    const auto state = static_cast<ObservationState>(
                        nativeId->value.data[0]);
                    const std::uint64_t detailUnits =
                        Read64(nativeId->value.data + 8);
                    const std::uint64_t valueOffset = 24 + detailUnits * 2;
                    if (valueOffset > nativeId->value.size) return false;
                    std::wostringstream identity;
                    identity << Read16(definitionKind->value.data) << L':'
                             << (state == ObservationState::Value
                                     ? L"id:" : L"absent:");
                    if (state == ObservationState::Value) {
                        if (nativeId->value.size - valueOffset != 8) {
                            return false;
                        }
                        identity << static_cast<std::int64_t>(
                            Read64(nativeId->value.data + valueOffset)) << L':';
                    } else {
                        identity << static_cast<unsigned>(state) << L':';
                    }
                    identity << Hex(Bytes(
                        aggregate->value.data,
                        aggregate->value.data + aggregate->value.size));
                    key = L"definition:" + identity.str();
                } else if (
                    kind == NodeKind::GenericControl ||
                    kind == NodeKind::Table || kind == NodeKind::Image) {
                    const std::uint8_t state = locatorField->value.data[0];
                    const std::uint64_t detail =
                        Read64(locatorField->value.data + 8);
                    const std::uint64_t size =
                        Read64(locatorField->value.data + 16);
                    const std::uint64_t start = 24 + detail * 2;
                    if (state != static_cast<std::uint8_t>(
                                     ObservationState::Value) ||
                        start > locatorField->value.size ||
                        size != locatorField->value.size - start) {
                        return false;
                    }
                    const Bytes locator(
                        locatorField->value.data + start,
                        locatorField->value.data + start + size);
                    key = IdentityKey(kind, locator);
                    const FieldView* const ctrlIdField = Find(nodePayload, 100);
                    const FieldView* const instance = Find(nodePayload, 101);
                    if (ctrlIdField == nullptr || ctrlIdField->value.size < 24 ||
                        ctrlIdField->value.data[0] != static_cast<std::uint8_t>(
                            ObservationState::Value) ||
                        instance == nullptr || instance->value.size < 24) {
                        return false;
                    }
                    const std::uint64_t ctrlDetail =
                        Read64(ctrlIdField->value.data + 8);
                    const std::uint64_t ctrlSize =
                        Read64(ctrlIdField->value.data + 16);
                    const std::uint64_t ctrlStart = 24 + ctrlDetail * 2;
                    if (ctrlStart > ctrlIdField->value.size ||
                        ctrlSize != ctrlIdField->value.size - ctrlStart) {
                        return false;
                    }
                    const std::wstring ctrlIdValue = DecodeUtf16(
                        {ctrlIdField->value.data + ctrlStart, ctrlSize});
                    const std::uint8_t instanceState =
                        instance->value.data[0];
                    bool instancePresent = false;
                    std::wstring instanceValue;
                    if (!DecodeControlInstanceObservation(
                            *instance, &instancePresent, &instanceValue) ||
                        (!instancePresent && instanceState !=
                            static_cast<std::uint8_t>(
                                ObservationState::NotExposed))) {
                        return false;
                    }
                    const std::wstring countKey = ControlCompleteKey(
                        kind, ctrlIdValue, instancePresent, instanceValue);
                    if (kind == NodeKind::Table) {
                        const auto owner = tableOwnerScopeByNode.find(nodeKey);
                        if (owner == tableOwnerScopeByNode.end()) return false;
                        key = TableStableKey(
                            owner->second, instancePresent, instanceValue,
                            locator, controlInstanceCounts[countKey]);
                    } else if (controlInstanceCounts[countKey] == 1) {
                        key = L"control:" + countKey;
                    } else {
                        const std::uint64_t occurrence =
                            controlOccurrences[countKey]++;
                        key = ControlOccurrenceKey(
                            kind, ctrlIdValue, instancePresent,
                            instanceValue, occurrence);
                    }
                } else if (kind == NodeKind::TableCell) {
                    const FieldView* const addressField = Find(nodePayload, 101);
                    if (parentField == nullptr || parentField->value.size != 16 ||
                        addressField == nullptr || addressField->value.size < 24) {
                        return false;
                    }
                    const std::uint64_t detail =
                        Read64(addressField->value.data + 8);
                    const std::uint64_t valueSize =
                        Read64(addressField->value.data + 16);
                    const std::uint64_t start = 24 + detail * 2;
                    if (addressField->value.data[0] !=
                            static_cast<std::uint8_t>(ObservationState::Value) ||
                        start > addressField->value.size ||
                        valueSize != addressField->value.size - start) {
                        return false;
                    }
                    const Bytes parentBytes(
                        parentField->value.data,
                        parentField->value.data + parentField->value.size);
                    key = L"cell:" + Hex(parentBytes) + L":" +
                        DecodeUtf16({addressField->value.data + start,
                                     valueSize});
                } else {
                    const std::uint8_t state = locatorField->value.data[0];
                    const std::uint64_t detail =
                        Read64(locatorField->value.data + 8);
                    const std::uint64_t size =
                        Read64(locatorField->value.data + 16);
                    const std::uint64_t start = 24 + detail * 2;
                    if (state != static_cast<std::uint8_t>(
                                     ObservationState::Value) ||
                        start > locatorField->value.size ||
                        size != locatorField->value.size - start) {
                        return false;
                    }
                    const Bytes locator(
                        locatorField->value.data + start,
                        locatorField->value.data + start + size);
                    key = IdentityKey(kind, locator);
                }
                if (!arena->Seed(key, id)) {
                    return false;
                }
                ++seeded;
            }
            offset += 24 + payloadBytes;
        }
        return offset == records.size && seeded != 0;
    } catch (...) {
        return false;
    }
}

} // namespace hancom::graph::capture

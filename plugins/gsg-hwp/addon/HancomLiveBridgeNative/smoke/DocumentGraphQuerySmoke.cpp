#include "../DocumentGraphQuery.h"

#include <Windows.h>

#include <algorithm>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

namespace {

using namespace hancom::graph;
using namespace hancom::graph::query;
using codec::Bytes;
using codec::Field;
using codec::LogicalRecord;

bool Check(const bool condition, const wchar_t* const label) {
    std::wcout << L"QUERY_ASSERT\t" << label << L'\t'
               << (condition ? L"PASS" : L"FAIL") << L'\n';
    if (!condition) {
        std::wcerr << L"DocumentGraphQuerySmoke failed: " << label << L'\n';
    }
    return condition;
}
bool Nonzero(const Sha256& digest) noexcept {
    return std::any_of(digest.bytes.begin(), digest.bytes.end(),
                       [](const std::uint8_t value) { return value != 0; });
}

NodeId Id(const std::uint8_t suffix) {
    NodeId id{};
    id.bytes = {0x10, 0x20, 0x30, 0x40, 0x50, suffix, 0x40, suffix,
                0x80, 0x90, 0xa0, 0xb0, 0xc0, 0xd0, 0xe0, suffix};
    return id;
}
Bytes IdBytes(const Uuid128& id) {
    return {id.bytes.begin(), id.bytes.end()};
}
void Add(Bytes* const target, const Bytes& value) {
    target->insert(target->end(), value.begin(), value.end());
}
Bytes Fields(const std::vector<Field>& fields) {
    Bytes result;
    for (const Field& field : fields) {
        Bytes encoded;
        if (codec::EncodeField(field, &encoded) != codec::Error::None) {
            return {};
        }
        Add(&result, encoded);
    }
    return result;
}
Bytes Record(const RecordKind kind, const RecordId id,
             std::vector<Field> fields) {
    Bytes result;
    const LogicalRecord record{kind, kFieldFlagRequired, id,
                               std::move(fields)};
    return codec::EncodeLogicalRecord(record, &result) == codec::Error::None
        ? result
        : Bytes{};
}
Bytes Observation(const ObservationState state, const Bytes& value = {}) {
    codec::Error error{};
    return codec::Observation(state, state == ObservationState::ReadFailed
        ? E_FAIL : S_OK, nullptr, 0, codec::View(value), &error);
}
Bytes Utf16(const std::u16string& value) {
    return codec::Utf16(reinterpret_cast<const std::uint16_t*>(value.data()),
                        value.size());
}

Bytes NodeRecord(const RecordId recordId, const NodeId& id,
                 const NodeKind kind, const NodeId* const parent,
                 const std::uint64_t sibling,
                 const std::vector<Field>& payloadFields = {}) {
    std::vector<Field> common{
        {1, kFieldFlagRequired, ScalarTag::UUID128, 1, IdBytes(id)},
        {2, kFieldFlagRequired, ScalarTag::Uint16, 1,
         codec::Uint16(static_cast<std::uint16_t>(kind))},
    };
    if (parent != nullptr) {
        common.push_back({3, 0, ScalarTag::UUID128, 1, IdBytes(*parent)});
    }
    common.push_back({4, kFieldFlagRequired, ScalarTag::Uint64, 1,
                      codec::Uint64(sibling)});
    common.push_back({5, kFieldFlagRequired, ScalarTag::Uint64, 1,
                      codec::Uint64(1)});
    common.push_back({6, kFieldFlagRequired, ScalarTag::Uint64, 1,
                      codec::Uint64(1)});
    common.push_back({7, kFieldFlagRequired, ScalarTag::Struct, 1,
                      Observation(ObservationState::NotApplicable)});
    common.push_back({8, kFieldFlagRequired, ScalarTag::SHA256, 1, Bytes(32)});
    common.push_back({10, static_cast<std::uint16_t>(
                             kFieldFlagRequired | kFieldFlagArray),
                      ScalarTag::Uint64, 0,
                      codec::ArrayValue(ScalarTag::Uint64, 0, {})});
    common.push_back({11, static_cast<std::uint16_t>(
                             kFieldFlagRequired | kFieldFlagArray),
                      ScalarTag::Uint64, 0,
                      codec::ArrayValue(ScalarTag::Uint64, 0, {})});
    return Record(RecordKind::Node, recordId,
                  {{1, kFieldFlagRequired, ScalarTag::Struct, 1,
                    Fields(common)},
                   {2, kFieldFlagRequired, ScalarTag::Struct, 1,
                    Fields(payloadFields)}});
}

Bytes PropertyRecord(const RecordId id, const NodeId& owner,
                     const FieldTag ownerField, const PropertyKeyId key,
                     const ScalarTag scalar, const ObservationState state,
                     const Bytes& value,
                     const PropertyOrigin origin = PropertyOrigin::Direct) {
    static_cast<void>(scalar);
    return Record(RecordKind::Property, id,
                  {{1, kFieldFlagRequired, ScalarTag::UUID128, 1,
                    IdBytes(owner)},
                   {2, kFieldFlagRequired, ScalarTag::Uint16, 1,
                    codec::Uint16(ownerField)},
                   {3, kFieldFlagRequired, ScalarTag::Uint32, 1,
                    codec::Uint32(key)},
                   {4, kFieldFlagRequired, ScalarTag::Struct, 1,
                    Observation(state, value)},
                   {5, kFieldFlagRequired, ScalarTag::Uint8, 1,
                    codec::Uint8(static_cast<std::uint8_t>(origin))}});
}
Bytes EdgeRecord(const RecordId id, const EdgeKind kind,
                 const NodeId& source, const NodeId& target,
                 const std::uint64_t ordinal) {
    return Record(RecordKind::Edge, id,
                  {{1, kFieldFlagRequired, ScalarTag::Uint16, 1,
                    codec::Uint16(static_cast<std::uint16_t>(kind))},
                   {2, kFieldFlagRequired, ScalarTag::UUID128, 1,
                    IdBytes(source)},
                   {3, kFieldFlagRequired, ScalarTag::UUID128, 1,
                    IdBytes(target)},
                   {4, kFieldFlagRequired, ScalarTag::Uint64, 1,
                    codec::Uint64(ordinal)}});
}

bool WriteFileBytes(const std::filesystem::path& path, const Bytes& bytes) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    const bool ok = bytes.size() <= MAXDWORD &&
        WriteFile(file, bytes.data(), static_cast<DWORD>(bytes.size()),
                  &written, nullptr) != FALSE &&
        written == bytes.size() && FlushFileBuffers(file) != FALSE;
    CloseHandle(file);
    return ok;
}
void Put64(Bytes* const bytes, const std::uint64_t value) {
    for (unsigned shift = 0; shift != 64; shift += 8) {
        bytes->push_back(static_cast<std::uint8_t>(value >> shift));
    }
}
void Put32(Bytes* const bytes, const std::uint32_t value) {
    for (unsigned shift = 0; shift != 32; shift += 8) {
        bytes->push_back(static_cast<std::uint8_t>(value >> shift));
    }
}
void AddBlob(Bytes* const cache, const ContentId& id, const Bytes& value,
             const std::size_t chunkBytes) {
    for (std::size_t at = 0; at < value.size(); at += chunkBytes) {
        const std::size_t count = (std::min)(chunkBytes, value.size() - at);
        Add(cache, IdBytes(id));
        Put64(cache, at);
        Put32(cache, static_cast<std::uint32_t>(count));
        cache->insert(cache->end(), value.begin() + at,
                      value.begin() + at + count);
    }
}

struct Fixture final {
    std::filesystem::path root{};
    GenerationSnapshot generation{};
    std::vector<NodeId> order{};
    NodeId document{};
    NodeId story{};
    NodeId section{};
    NodeId paragraph{};
    NodeId run{};
    NodeId tableA{};
    NodeId caption{};
    NodeId tableB{};
    NodeId binary{};
    Bytes hugeText{};
    Bytes hugeProperty{};
    Bytes hugeAsset{};
    std::weak_ptr<std::uint8_t> leaseWitness{};
};

bool BuildFixture(Fixture* const fixture) {
    static std::uint64_t fixtureSerial = 0;
    wchar_t temporary[MAX_PATH]{};
    if (fixture == nullptr || GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    fixture->root = std::filesystem::path(temporary) /
        (L"hwp-graph-query-smoke-" + std::to_wstring(GetCurrentProcessId()) +
         L"-" + std::to_wstring(++fixtureSerial));
    std::error_code ignored;
    std::filesystem::remove_all(fixture->root, ignored);
    if (!std::filesystem::create_directories(fixture->root, ignored)) {
        return false;
    }
    fixture->document = Id(1);
    fixture->story = Id(2);
    fixture->section = Id(3);
    fixture->paragraph = Id(4);
    fixture->run = Id(5);
    fixture->tableA = Id(6);
    fixture->caption = Id(7);
    fixture->tableB = Id(8);
    fixture->binary = Id(9);
    fixture->order = {fixture->document, fixture->story, fixture->section,
                      fixture->paragraph, fixture->run, fixture->tableA,
                      fixture->caption, fixture->tableB, fixture->binary};
    ContentId textId = Id(40);
    ContentId assetId = Id(41);
    std::u16string text;
    for (std::size_t i = 0; i != 1200; ++i) {
        text += i == 300 ? u"needle" : u"x";
    }
    fixture->hugeText = Utf16(text);
    std::u16string propertyText(1400, u'p');
    fixture->hugeProperty = Utf16(propertyText);
    fixture->hugeAsset.resize(5000);
    for (std::size_t i = 0; i != fixture->hugeAsset.size(); ++i) {
        fixture->hugeAsset[i] = static_cast<std::uint8_t>((i * 17) & 0xff);
    }
    const Sha256 textDigest = codec::Hash(codec::View(fixture->hugeText));
    const Sha256 assetDigest = codec::Hash(codec::View(fixture->hugeAsset));
    const Bytes textSlice = codec::BlobSliceValue(
        textId, 0, fixture->hugeText.size(), textDigest);
    Bytes records;
    RecordId next = 1;
    Add(&records, NodeRecord(next++, fixture->document, NodeKind::Document,
                             nullptr, 0));
    Add(&records, NodeRecord(next++, fixture->story, NodeKind::Story,
                             &fixture->document, 0));
    Add(&records, NodeRecord(next++, fixture->section, NodeKind::Section,
                             &fixture->story, 0));
    Add(&records, NodeRecord(next++, fixture->paragraph, NodeKind::Paragraph,
                             &fixture->section, 0));
    Add(&records, NodeRecord(next++, fixture->run, NodeKind::CharacterRun,
                             &fixture->paragraph, 0,
                             {{100, kFieldFlagRequired, ScalarTag::Struct, 1,
                               Bytes(32)},
                              {101, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
                               Observation(ObservationState::Value,
                                           textSlice)}}));
    Add(&records, NodeRecord(next++, fixture->tableA, NodeKind::Table,
                             &fixture->paragraph, 1));
    Add(&records, NodeRecord(next++, fixture->caption, NodeKind::Story,
                             &fixture->paragraph, 2));
    Add(&records, NodeRecord(next++, fixture->tableB, NodeKind::Table,
                             &fixture->paragraph, 3));
    Add(&records, NodeRecord(next++, fixture->binary, NodeKind::BinaryData,
                             &fixture->tableA, 0,
                             {{100, kFieldFlagRequired, ScalarTag::UUID128, 1,
                               IdBytes(assetId)},
                              {101, kFieldFlagRequired, ScalarTag::Uint64, 1,
                               codec::Uint64(fixture->hugeAsset.size())},
                              {102, kFieldFlagRequired, ScalarTag::SHA256, 1,
                               Bytes(assetDigest.bytes.begin(),
                                     assetDigest.bytes.end())}}));
    Add(&records, PropertyRecord(next++, fixture->document, 100, 13005,
                                 ScalarTag::UTF16, ObservationState::Value,
                                 fixture->hugeProperty));
    Add(&records, PropertyRecord(next++, fixture->tableA, 10, 12000,
                                 ScalarTag::Uint64, ObservationState::Value,
                                 codec::Uint64(10)));
    Add(&records, PropertyRecord(next++, fixture->tableA, 10, 12001,
                                 ScalarTag::Uint64, ObservationState::Value,
                                 codec::Uint64(20)));
    Add(&records, PropertyRecord(next++, fixture->tableA, 106, 3000,
                                 ScalarTag::UTF16, ObservationState::Value,
                                 Utf16(u"CaptionedTable")));
    Add(&records, PropertyRecord(next++, fixture->tableA, 106, 1008,
                                 ScalarTag::Bool, ObservationState::Value,
                                 codec::Bool(true),
                                 PropertyOrigin::NamedStyle));
    Add(&records, PropertyRecord(next++, fixture->tableA, 106, 8000,
                                 ScalarTag::HWPUNIT64,
                                 ObservationState::NotExposed, {}));
    Add(&records, PropertyRecord(next++, fixture->tableB, 10, 12000,
                                 ScalarTag::Uint64, ObservationState::Value,
                                 codec::Uint64(21)));
    Add(&records, PropertyRecord(next++, fixture->tableB, 10, 12001,
                                 ScalarTag::Uint64, ObservationState::Value,
                                 codec::Uint64(22)));
    Add(&records, EdgeRecord(next++, EdgeKind::CaptionOf, fixture->caption,
                             fixture->tableA, 0));
    Add(&records, EdgeRecord(next++, EdgeKind::AssetRef, fixture->tableA,
                             fixture->binary, 0));
    Bytes blobCache;
    AddBlob(&blobCache, textId, fixture->hugeText, 257);
    // Store publication reads one immutable content range for every reference.
    // The query index must collapse byte-identical repeated cache ranges.
    AddBlob(&blobCache, textId, fixture->hugeText, 257);
    AddBlob(&blobCache, assetId, fixture->hugeAsset, 389);
    Bytes manifest(580);
    std::copy_n("HGM1", 4, manifest.begin());
    if (!WriteFileBytes(fixture->root / L"records.hgn", records) ||
        !WriteFileBytes(fixture->root / L"blobs.hgb", blobCache) ||
        !WriteFileBytes(fixture->root / L"manifest.hgm", manifest)) {
        return false;
    }
    fixture->generation.path = fixture->root.wstring();
    fixture->generation.key.serial = 1;
    fixture->generation.key.storeEpoch[0] = 0x5a;
    fixture->generation.key.captureRoot = codec::DomainHash(
        "HWPGRAPH\0QUERYFIXTURE\0V1", codec::View(records));
    fixture->generation.manifestStream = {
        manifest.size(), codec::Hash(codec::View(manifest))};
    fixture->generation.recordsStream = {
        records.size(), codec::Hash(codec::View(records))};
    fixture->generation.blobStream = {
        blobCache.size(), codec::Hash(codec::View(blobCache))};
    const auto leaseOwner = std::make_shared<std::uint8_t>(std::uint8_t{1});
    fixture->leaseWitness = leaseOwner;
    fixture->generation.lease = store::GenerationPin(
        leaseOwner,
        reinterpret_cast<const store::Generation*>(leaseOwner.get()));
    return true;
}

class SpySource final : public GenerationSource {
public:
    explicit SpySource(GenerationSnapshot generation)
        : generation_(std::move(generation)) {}
    bool Pin(GenerationSnapshot* const snapshot) noexcept override {
        ++pinReads;
        if (snapshot == nullptr) return false;
        *snapshot = generation_;
        return true;
    }
    bool IsCurrent(const GenerationKey& key) noexcept override {
        ++validationReads;
        return Equal(key, generation_.key);
    }
    void DropSourceLease() noexcept { generation_.lease.reset(); }
    void DropAuthenticatedFiles() noexcept {
        generation_.authenticatedFiles.reset();
    }
    void Mutate() noexcept {
        ++generation_.key.serial;
        generation_.key.captureRoot.bytes[0] ^= 0xff;
    }
    std::uint64_t pinReads = 0;
    std::uint64_t validationReads = 0;
    std::uint64_t comReads = 0;
private:
    GenerationSnapshot generation_{};
};

std::vector<Fragment> Drain(DocumentGraphQuery* const query, Chunk first,
                            Status* const finalStatus = nullptr) {
    std::vector<Fragment> all = first.fragments;
    Chunk current = std::move(first);
    while (!current.terminal && current.status == Status::Ok) {
        NextRequest request{};
        request.cursor = current.cursor;
        request.generation = current.generation;
        request.sequence = current.sequence + 1;
        request.byteBudget = 512;
        request.previousChain = current.chainDigest;
        Chunk next;
        const Status status = query->Next(request, &next);
        all.insert(all.end(), next.fragments.begin(), next.fragments.end());
        current = std::move(next);
        if (status != Status::Ok && status != Status::Terminal &&
            status != Status::Empty) {
            break;
        }
    }
    if (finalStatus != nullptr) *finalStatus = current.status;
    return all;
}

std::vector<Fragment> DrainWithAudit(
    DocumentGraphQuery* const query, Chunk first, std::size_t* const chunkCount,
    bool* const chainValid, Status* const finalStatus = nullptr) {
    std::vector<Fragment> all;
    Chunk current = std::move(first);
    std::size_t count = 0;
    bool valid = true;
    for (;;) {
        ++count;
        valid = valid && Nonzero(current.chunkDigest) &&
            Nonzero(current.chainDigest) && current.chargedBytes <= 512;
        all.insert(all.end(), current.fragments.begin(),
                   current.fragments.end());
        if (current.terminal || current.status != Status::Ok) break;
        NextRequest request{};
        request.cursor = current.cursor;
        request.generation = current.generation;
        request.sequence = current.sequence + 1;
        request.byteBudget = 512;
        request.previousChain = current.chainDigest;
        Chunk next;
        const Status status = query->Next(request, &next);
        valid = valid && next.sequence == request.sequence &&
            codec::Equal(next.previousChain, request.previousChain);
        current = std::move(next);
        if (status != Status::Ok && status != Status::Terminal &&
            status != Status::Empty) break;
    }
    if (chunkCount != nullptr) *chunkCount = count;
    if (chainValid != nullptr) *chainValid = valid;
    if (finalStatus != nullptr) *finalStatus = current.status;
    return all;
}

std::vector<NodeId> StructureIds(const std::vector<Fragment>& fragments) {
    std::vector<NodeId> ids;
    for (const Fragment& fragment : fragments) {
        if (fragment.valueKind == ValueKind::Structure &&
            fragment.valueOffset == 0) {
            ids.push_back(fragment.node);
        }
    }
    return ids;
}
bool IdsEqual(const std::vector<NodeId>& left,
              const std::vector<NodeId>& right) noexcept {
    if (left.size() != right.size()) return false;
    for (std::size_t index = 0; index != left.size(); ++index) {
        if (left[index].bytes != right[index].bytes) return false;
    }
    return true;
}
bool UsedIndex(const Chunk& chunk, const std::uint64_t required,
               const bool strictSubset = false) noexcept {
    return (chunk.indexPathBits & required) == required &&
        (!strictSubset || chunk.evaluatedNodes < chunk.axisCandidateNodes);
}

bool RunAxesAndFilters(DocumentGraphQuery* const engine,
                       const Fixture& fixture) {
    bool ok = true;
    Query query{};
    query.projectionBits = ProjectStructure;
    Chunk chunk;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Ok,
               L"document order open") && ok;
    ok = Check(UsedIndex(chunk, IndexDocumentOrder),
               L"document-order index path consumed") && ok;
    Status final{};
    const auto all = Drain(engine, std::move(chunk), &final);
    ok = Check(final == Status::Terminal &&
               IdsEqual(StructureIds(all), fixture.order),
               L"exact canonical document order") && ok;
    ok = Check(std::all_of(all.begin(), all.end(),
                  [](const Fragment& item) {
                      return item.valueKind == ValueKind::Structure;
                  }), L"default projection does not dump whole graph") && ok;

    query.nodeKindBits = UINT64_C(1) << static_cast<unsigned>(NodeKind::Table);
    const Status kindStatus = engine->Open(query, 512, &chunk);
    ok = Check(kindStatus != Status::StorageFailure &&
               UsedIndex(chunk, IndexDocumentOrder | IndexKind, true),
               L"kind index path consumed without full-node scan") && ok;
    ok = Check(StructureIds(Drain(engine, std::move(chunk))).size() == 2,
               L"kind index") && ok;

    query = {};
    query.axis = Axis::Self;
    query.rootPresent = true;
    query.root = fixture.tableA;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal &&
               IdsEqual(StructureIds(chunk.fragments), {fixture.tableA}),
               L"self axis") && ok;
    query.axis = Axis::Parent;
    query.root = fixture.run;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal &&
               IdsEqual(StructureIds(chunk.fragments), {fixture.paragraph}), L"parent axis") && ok;
    ok = Check(UsedIndex(chunk, IndexParent),
               L"parent index path consumed") && ok;
    query.axis = Axis::Children;
    query.root = fixture.document;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal &&
               IdsEqual(StructureIds(chunk.fragments), {fixture.story}), L"children axis") && ok;
    ok = Check(UsedIndex(chunk, IndexChildren),
               L"children index path consumed") && ok;
    query.axis = Axis::Descendants;
    query.root = fixture.story;
    const Status descendantStatus = engine->Open(query, 512, &chunk);
    ok = Check(descendantStatus == Status::Ok &&
               UsedIndex(chunk, IndexDescendants),
               L"descendants index path consumed") && ok;
    ok = Check(StructureIds(Drain(engine, std::move(chunk))).size() == 7,
               L"descendants axis") && ok;
    query.axis = Axis::References;
    query.root = fixture.tableA;
    query.edgeKindBits = UINT64_C(1) <<
        static_cast<unsigned>(EdgeKind::AssetRef);
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal &&
               IdsEqual(StructureIds(chunk.fragments), {fixture.binary}), L"references axis") && ok;
    ok = Check(UsedIndex(chunk, IndexReferences),
               L"forward-reference index path consumed") && ok;

    query = {};
    query.nodeKindBits = UINT64_C(1) << static_cast<unsigned>(NodeKind::Table);
    query.pageRangePresent = true;
    query.firstPage = 10;
    query.lastPage = 20;
    query.pageRelation = PageRelation::Overlaps;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal &&
               IdsEqual(StructureIds(chunk.fragments), {fixture.tableA}), L"page overlap") && ok;
    ok = Check(UsedIndex(chunk,
                   IndexDocumentOrder | IndexKind | IndexPageSpan, true),
               L"page-span index path consumed without full-node scan") && ok;
    query.pageRelation = PageRelation::ContainedBy;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal,
               L"page contained-by") && ok;
    query.pageRelation = PageRelation::Contains;
    query.firstPage = 12;
    query.lastPage = 18;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal,
               L"page contains") && ok;

    query = {};
    query.nodeKindBits = UINT64_C(1) << static_cast<unsigned>(NodeKind::Table);
    PropertyPredicate style{};
    style.key = 3000;
    style.operation = Operator::ContainsUtf16;
    style.scalar = ScalarTag::UTF16;
    style.canonicalValue = Utf16(u"Captioned");
    style.originPresent = true;
    style.origin = PropertyOrigin::Direct;
    query.properties.push_back(style);
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal &&
               IdsEqual(StructureIds(chunk.fragments), {fixture.tableA}),
               L"native style/property filter") && ok;
    ok = Check(UsedIndex(chunk,
                   IndexDocumentOrder | IndexKind | IndexNativeProperty, true),
               L"native-property index path consumed without full-node scan") && ok;

    struct OperatorCase final {
        Operator operation;
        std::uint64_t operand;
        const wchar_t* label;
    };
    const OperatorCase operatorCases[] = {
        {Operator::Eq, 10, L"property operator eq"},
        {Operator::Ne, 11, L"property operator ne"},
        {Operator::Lt, 11, L"property operator lt"},
        {Operator::Le, 10, L"property operator le"},
        {Operator::Gt, 9, L"property operator gt"},
        {Operator::Ge, 10, L"property operator ge"},
    };
    for (const OperatorCase& item : operatorCases) {
        query = {};
        query.axis = Axis::Self;
        query.rootPresent = true;
        query.root = fixture.tableA;
        PropertyPredicate predicate{};
        predicate.key = 12000;
        predicate.operation = item.operation;
        predicate.scalar = ScalarTag::Uint64;
        predicate.canonicalValue = codec::Uint64(item.operand);
        query.properties.push_back(predicate);
        ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal &&
                   IdsEqual(StructureIds(chunk.fragments), {fixture.tableA}),
                   item.label) && ok;
    }
    query = {};
    query.axis = Axis::Self;
    query.rootPresent = true;
    query.root = fixture.tableA;
    PropertyPredicate origin{};
    origin.key = 1008;
    origin.operation = Operator::Eq;
    origin.scalar = ScalarTag::Bool;
    origin.canonicalValue = codec::Bool(true);
    origin.originPresent = true;
    origin.origin = PropertyOrigin::NamedStyle;
    query.properties.push_back(origin);
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal,
               L"native property origin filter") && ok;

    query = {};
    query.nodeKindBits = UINT64_C(1) <<
        static_cast<unsigned>(NodeKind::CharacterRun);
    query.textContains = u"needle";
    ok = Check(engine->Open(query, 512, &chunk) == Status::Terminal &&
               IdsEqual(StructureIds(chunk.fragments), {fixture.run}), L"text index") && ok;
    ok = Check(UsedIndex(chunk,
                   IndexDocumentOrder | IndexKind | IndexText, true),
               L"text index path consumed without full-node scan") && ok;

    query = {};
    query.nodeKindBits = UINT64_C(1) << static_cast<unsigned>(NodeKind::Image);
    ok = Check(engine->Open(query, 512, &chunk) == Status::Empty &&
               chunk.terminal && chunk.fragments.empty(), L"empty result") && ok;
    query = {};
    query.axis = Axis::Children;
    ok = Check(engine->Open(query, 512, &chunk) == Status::InvalidQuery,
               L"root-required axis validation") && ok;
    query = {};
    query.pageRangePresent = true;
    query.firstPage = 20;
    query.lastPage = 10;
    ok = Check(engine->Open(query, 512, &chunk) == Status::InvalidQuery,
               L"malformed page filter validation") && ok;
    query = {};
    query.projectionBits = UINT64_C(1) << 63;
    ok = Check(engine->Open(query, 512, &chunk) == Status::InvalidQuery,
               L"unknown projection validation") && ok;
    return ok;
}

bool RunProjectionAndContinuation(SpySource* const source,
                                  DocumentGraphQuery* const engine,
                                  const Fixture& fixture) {
    bool ok = true;
    Chunk chunk;
    Query query{};
    query.axis = Axis::Self;
    query.rootPresent = true;
    query.root = fixture.tableA;
    query.projectionBits = ProjectProperties | ProjectLayout |
        ProjectReferences;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Ok,
               L"property/layout/reference projection") && ok;
    const auto projected = Drain(engine, std::move(chunk));
    ok = Check(std::any_of(projected.begin(), projected.end(),
                  [](const Fragment& item) {
                      return item.valueKind == ValueKind::Property &&
                          item.propertyKey == 3000;
                  }) &&
               std::any_of(projected.begin(), projected.end(),
                  [](const Fragment& item) {
                      return item.valueKind == ValueKind::Layout &&
                          item.propertyKey == 12000;
                  }) &&
               std::any_of(projected.begin(), projected.end(),
                  [](const Fragment& item) {
                      return item.valueKind == ValueKind::Reference;
                  }), L"projection values") && ok;

    query.root = fixture.run;
    query.projectionBits = ProjectText;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Ok,
               L"huge text continuation open") && ok;
    const Uuid128 textCursor = chunk.cursor;
    source->DropSourceLease();
    ok = Check(!fixture.leaseWitness.expired(),
               L"generation pin retained before continuation") && ok;
    const std::uint64_t validationsBeforeText = source->validationReads;
    std::size_t textChunkCount = 0;
    bool textChainValid = false;
    Status textFinal{};
    const auto textFragments = DrainWithAudit(
        engine, std::move(chunk), &textChunkCount, &textChainValid, &textFinal);
    ok = Check(!fixture.leaseWitness.expired(),
               L"generation pin retained through chunks") && ok;
    ok = Check(textChunkCount > 1 && textFinal == Status::Terminal,
               L"huge text uses multiple chunks") && ok;
    ok = Check(textChainValid, L"huge text monotonic digest chain") && ok;
    ok = Check(source->validationReads - validationsBeforeText >=
                   (textChunkCount - 1) * 2,
               L"generation validated before and after every text chunk") && ok;
    Bytes text;
    std::uint64_t expectedOffset = 0;
    bool textOffsets = true;
    for (const Fragment& fragment : textFragments) {
        textOffsets = textOffsets && fragment.valueOffset == expectedOffset;
        expectedOffset += fragment.bytes.size();
        Add(&text, fragment.bytes);
    }
    ok = Check(textOffsets && text == fixture.hugeText,
               L"huge text lossless continuation") && ok;
    ok = Check(engine->Close(textCursor) == Status::CursorClosed,
               L"terminal cursor explicit close") && ok;

    query.root = fixture.binary;
    query.projectionBits = ProjectAssets;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Ok,
               L"huge blob continuation open") && ok;
    const std::uint64_t validationsBeforeAsset = source->validationReads;
    std::size_t assetChunkCount = 0;
    bool assetChainValid = false;
    Status assetFinal{};
    const auto assetFragments = DrainWithAudit(
        engine, std::move(chunk), &assetChunkCount, &assetChainValid,
        &assetFinal);
    ok = Check(assetChunkCount > 1 && assetFinal == Status::Terminal,
               L"huge blob uses multiple chunks") && ok;
    ok = Check(assetChainValid, L"huge blob monotonic digest chain") && ok;
    ok = Check(source->validationReads - validationsBeforeAsset >=
                   (assetChunkCount - 1) * 2,
               L"generation validated before and after every blob chunk") && ok;
    Bytes asset;
    for (const Fragment& fragment : assetFragments) Add(&asset, fragment.bytes);
    ok = Check(asset == fixture.hugeAsset, L"huge blob lossless continuation") && ok;

    query.root = fixture.document;
    query.projectionBits = ProjectProperties;
    ok = Check(engine->Open(query, 512, &chunk) == Status::Ok,
               L"huge property continuation open") && ok;
    std::size_t propertyChunkCount = 0;
    bool propertyChainValid = false;
    Status propertyFinal{};
    const auto propertyFragments = DrainWithAudit(
        engine, std::move(chunk), &propertyChunkCount, &propertyChainValid,
        &propertyFinal);
    Bytes propertyBytes;
    std::uint64_t propertyOffset = 0;
    bool propertyProgress = true;
    for (const Fragment& fragment : propertyFragments) {
        propertyProgress = propertyProgress &&
            fragment.valueOffset == propertyOffset &&
            fragment.propertyKey == 13005;
        propertyOffset += fragment.bytes.size();
        Add(&propertyBytes, fragment.bytes);
    }
    ok = Check(propertyChunkCount > 1 &&
               propertyFinal == Status::Terminal && propertyChainValid &&
               propertyProgress && propertyBytes == fixture.hugeProperty,
               L"huge property lazy multichunk progress chain") && ok;

    query.root = fixture.tableA;
    query.projectionBits = ProjectProperties;
    const Status unavailableStatus = engine->Open(query, 512, &chunk);
    ok = Check(unavailableStatus == Status::Ok ||
               unavailableStatus == Status::Terminal,
               L"terminal unavailable property queryable") && ok;
    const auto properties = Drain(engine, std::move(chunk));
    ok = Check(std::any_of(properties.begin(), properties.end(),
                  [](const Fragment& item) {
                      return item.propertyKey == 8000 &&
                          item.observation == ObservationState::NotExposed &&
                          item.valueTotal == 0;
                  }), L"terminal NotExposed fact projection") && ok;
    return ok;
}

bool RunLifecycleNegative(SpySource* const source,
                          DocumentGraphQuery* const engine,
                          const Fixture& fixture) {
    bool ok = true;
    Query query{};
    query.axis = Axis::Self;
    query.rootPresent = true;
    query.root = fixture.binary;
    query.projectionBits = ProjectAssets;
    Chunk first;
    ok = Check(engine->Open(query, 512, &first) == Status::Ok,
               L"lifecycle cursor open") && ok;
    NextRequest request{};
    request.cursor = first.cursor;
    request.generation = first.generation;
    request.sequence = first.sequence + 1;
    request.byteBudget = 512;
    request.previousChain = first.chainDigest;
    Chunk failed;
    NextRequest wrongSequence = request;
    ++wrongSequence.sequence;
    ok = Check(engine->Next(wrongSequence, &failed) ==
                   Status::SequenceMismatch && failed.fragments.empty(),
               L"sequence mismatch rejected") && ok;
    NextRequest wrongGeneration = request;
    ++wrongGeneration.generation.serial;
    ok = Check(engine->Next(wrongGeneration, &failed) == Status::StaleGraph &&
               failed.fragments.empty(),
               L"request generation mismatch returns STALE_GRAPH zero payload") && ok;
    NextRequest tampered = request;
    tampered.previousChain.bytes[0] ^= 1;
    ok = Check(engine->Next(tampered, &failed) == Status::DigestMismatch &&
               failed.fragments.empty(), L"digest chain tamper rejected") && ok;
    ok = Check(engine->Next(request, &failed) == Status::Ok,
               L"valid chain after negative requests") && ok;

    Query closeQuery{};
    Chunk closeChunk;
    ok = Check(engine->Open(closeQuery, 512, &closeChunk) == Status::Ok &&
               engine->Close(closeChunk.cursor) == Status::CursorClosed,
               L"explicit close") && ok;
    request = {};
    request.cursor = closeChunk.cursor;
    request.generation = closeChunk.generation;
    request.byteBudget = 512;
    ok = Check(engine->Next(request, &failed) == Status::CursorClosed,
               L"next after close rejected") && ok;
    Chunk cancelChunk;
    ok = Check(engine->Open(closeQuery, 512, &cancelChunk) == Status::Ok &&
               engine->Cancel(cancelChunk.cursor) == Status::Cancelled,
               L"explicit cancel") && ok;
    request.cursor = cancelChunk.cursor;
    request.generation = cancelChunk.generation;
    ok = Check(engine->Next(request, &failed) == Status::Cancelled,
               L"next after cancel rejected") && ok;

    request.cursor = Id(99);
    ok = Check(engine->Next(request, &failed) == Status::MalformedCursor,
               L"forged/malformed cursor") && ok;
    Chunk budget;
    ok = Check(engine->Open(closeQuery, kMinimumByteBudget - 1, &budget) ==
                   Status::BudgetTooSmall,
               L"impossible byte budget deterministic rejection") && ok;

    Chunk durableCursor;
    ok = Check(engine->Open(query, 512, &durableCursor) == Status::Ok,
               L"no-timeout cursor prepared") && ok;
    Query emptyQuery{};
    emptyQuery.nodeKindBits = UINT64_C(1) <<
        static_cast<unsigned>(NodeKind::Image);
    for (std::size_t operation = 0; operation != 256; ++operation) {
        Chunk ignored;
        if (engine->Open(emptyQuery, 512, &ignored) != Status::Empty) {
            ok = Check(false, L"intervening query operations") && ok;
            break;
        }
        static_cast<void>(engine->Close(ignored.cursor));
    }
    request = {};
    request.cursor = durableCursor.cursor;
    request.generation = durableCursor.generation;
    request.sequence = durableCursor.sequence + 1;
    request.byteBudget = 512;
    request.previousChain = durableCursor.chainDigest;
    ok = Check(engine->Next(request, &failed) == Status::Ok,
               L"cursor has no arbitrary timeout expiry") && ok;

    Chunk staleFirst;
    ok = Check(engine->Open(query, 512, &staleFirst) == Status::Ok,
               L"stale cursor prepared") && ok;
    source->Mutate();
    request = {};
    request.cursor = staleFirst.cursor;
    request.generation = staleFirst.generation;
    request.sequence = staleFirst.sequence + 1;
    request.byteBudget = 512;
    request.previousChain = staleFirst.chainDigest;
    ok = Check(engine->Next(request, &failed) == Status::StaleGraph &&
               failed.fragments.empty(), L"STALE_GRAPH after mutation with zero mixed data") && ok;
    return ok;
}

bool ReadFileBytes(const std::filesystem::path& path, Bytes* const bytes) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_WRITE |
                                  FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER length{};
    bool ok = GetFileSizeEx(file, &length) != FALSE && length.QuadPart >= 0 &&
        static_cast<unsigned long long>(length.QuadPart) <=
            static_cast<unsigned long long>((std::numeric_limits<std::size_t>::max)());
    if (ok) bytes->resize(static_cast<std::size_t>(length.QuadPart));
    DWORD read = 0;
    ok = ok && bytes->size() <= MAXDWORD &&
        (bytes->empty() || (ReadFile(file, bytes->data(),
                                    static_cast<DWORD>(bytes->size()), &read,
                                    nullptr) != FALSE &&
                            read == bytes->size()));
    CloseHandle(file);
    return ok;
}

bool TryFlipByte(const std::filesystem::path& path,
                 const std::size_t offset) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE,
                              FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER position{};
    position.QuadPart = static_cast<LONGLONG>(offset);
    std::uint8_t value = 0;
    DWORD count = 0;
    bool ok = SetFilePointerEx(file, position, nullptr, FILE_BEGIN) != FALSE &&
        ReadFile(file, &value, 1, &count, nullptr) != FALSE && count == 1;
    value ^= 0x5a;
    ok = ok && SetFilePointerEx(file, position, nullptr, FILE_BEGIN) != FALSE &&
        WriteFile(file, &value, 1, &count, nullptr) != FALSE && count == 1 &&
        FlushFileBuffers(file) != FALSE;
    CloseHandle(file);
    return ok;
}

bool TryTruncate(const std::filesystem::path& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER length{};
    bool ok = GetFileSizeEx(file, &length) != FALSE && length.QuadPart > 0;
    if (ok) {
        length.QuadPart -= 1;
        ok = SetFilePointerEx(file, length, nullptr, FILE_BEGIN) != FALSE &&
            SetEndOfFile(file) != FALSE && FlushFileBuffers(file) != FALSE;
    }
    CloseHandle(file);
    return ok;
}

Query LazyQuery(const Fixture& fixture, const wchar_t* const file) {
    Query query{};
    query.axis = Axis::Self;
    query.rootPresent = true;
    if (std::wcscmp(file, L"records.hgn") == 0) {
        query.root = fixture.document;
        query.projectionBits = ProjectProperties;
    } else {
        query.root = fixture.binary;
        query.projectionBits = ProjectAssets;
    }
    return query;
}

bool RunIntegrityRegression() {
    bool ok = true;
    std::vector<std::filesystem::path> cleanupRoots;
    const wchar_t* const files[] = {
        L"manifest.hgm", L"records.hgn", L"blobs.hgb"};
    for (const wchar_t* const file : files) {
        for (const bool truncate : {false, true}) {
            Fixture fixture;
            ok = Check(BuildFixture(&fixture), L"integrity pre-open fixture") && ok;
            cleanupRoots.push_back(fixture.root);
            const std::filesystem::path path = fixture.root / file;
            Bytes original;
            ok = Check(ReadFileBytes(path, &original),
                       L"integrity pre-open original read") && ok;
            const bool changed = truncate ? TryTruncate(path) :
                TryFlipByte(path, original.size() / 2);
            ok = Check(changed, L"integrity pre-open mutation applied") && ok;
            SpySource source(fixture.generation);
            DocumentGraphQuery engine(&source);
            Chunk chunk;
            const Status status = engine.Open(LazyQuery(fixture, file), 512,
                                              &chunk);
            ok = Check(status == Status::StorageFailure &&
                           chunk.fragments.empty() &&
                           engine.CursorCount() == 0,
                       truncate ? L"pre-open truncation rejected atomically" :
                                  L"pre-open byte flip rejected atomically") && ok;
            std::error_code ignored;
            std::filesystem::remove_all(fixture.root, ignored);
        }
    }

    for (const wchar_t* const file : files) {
        Fixture fixture;
        ok = Check(BuildFixture(&fixture), L"integrity post-open fixture") && ok;
        cleanupRoots.push_back(fixture.root);
        SpySource source(fixture.generation);
        DocumentGraphQuery engine(&source);
        Chunk first;
        const Status opened = engine.Open(LazyQuery(fixture, file), 512, &first);
        ok = Check(opened == Status::Ok, L"integrity post-open cursor prepared") && ok;
        const std::filesystem::path path = fixture.root / file;
        Bytes original;
        ok = Check(ReadFileBytes(path, &original),
                   L"integrity post-open original read") && ok;
        const bool changed = TryFlipByte(path, original.size() / 2);
        ok = Check(!changed, L"post-open generation mutation denied") && ok;
        NextRequest request{};
        request.cursor = first.cursor;
        request.generation = first.generation;
        request.sequence = first.sequence + 1;
        request.byteBudget = 512;
        request.previousChain = first.chainDigest;
        Chunk next;
        const Status status = engine.Next(request, &next);
        ok = Check((status == Status::Ok || status == Status::Terminal) &&
                       !next.fragments.empty(),
                   L"denied mutation preserves authenticated continuation") && ok;
        std::error_code ignored;
        static_cast<void>(engine.Close(first.cursor));
        std::filesystem::remove_all(fixture.root, ignored);
    }

    Fixture digestFixture;
    ok = Check(BuildFixture(&digestFixture), L"BlobSlice digest fixture") && ok;
    cleanupRoots.push_back(digestFixture.root);
    const std::filesystem::path recordsPath =
        digestFixture.root / L"records.hgn";
    Bytes records;
    ok = Check(ReadFileBytes(recordsPath, &records),
               L"BlobSlice records read") && ok;
    const Sha256 expected = codec::Hash(codec::View(digestFixture.hugeText));
    const auto digestAt = std::search(records.begin(), records.end(),
        expected.bytes.begin(), expected.bytes.end());
    ok = Check(digestAt != records.end(), L"BlobSlice digest located") && ok;
    if (digestAt != records.end()) {
        *digestAt ^= 0x5a;
        ok = Check(WriteFileBytes(recordsPath, records),
                   L"BlobSlice digest mismatch injected") && ok;
        digestFixture.generation.recordsStream = {
            records.size(), codec::Hash(codec::View(records))};
    }
    SpySource digestSource(digestFixture.generation);
    auto digestEngine = std::make_unique<DocumentGraphQuery>(&digestSource);
    Query digestQuery{};
    digestQuery.axis = Axis::Self;
    digestQuery.rootPresent = true;
    digestQuery.root = digestFixture.run;
    digestQuery.projectionBits = ProjectText;
    Chunk digestChunk;
    ok = Check(digestEngine->Open(digestQuery, 512, &digestChunk) ==
                   Status::StorageFailure && digestChunk.fragments.empty() &&
                   digestEngine->CursorCount() == 0,
               L"referenced BlobSlice digest mismatch rejected atomically") && ok;
    digestEngine.reset();

    Fixture retryFixture;
    ok = Check(BuildFixture(&retryFixture), L"atomic read-failure fixture") && ok;
    cleanupRoots.push_back(retryFixture.root);
    auto pinned = std::make_shared<store::AuthenticatedGenerationFiles>();
    pinned->manifest = CreateFileW(
        (retryFixture.root / L"manifest.hgm").c_str(), GENERIC_READ,
        FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
        nullptr);
    pinned->records = CreateFileW(
        (retryFixture.root / L"records.hgn").c_str(), GENERIC_READ,
        FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
        nullptr);
    pinned->blobs = CreateFileW(
        (retryFixture.root / L"blobs.hgb").c_str(), GENERIC_READ,
        FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
        nullptr);
    pinned->manifestStream = retryFixture.generation.manifestStream;
    pinned->recordsStream = retryFixture.generation.recordsStream;
    pinned->blobStream = retryFixture.generation.blobStream;
    retryFixture.generation.authenticatedFiles = pinned;
    HANDLE backup = INVALID_HANDLE_VALUE;
    ok = Check(pinned->manifest != INVALID_HANDLE_VALUE &&
                   pinned->records != INVALID_HANDLE_VALUE &&
                   pinned->blobs != INVALID_HANDLE_VALUE &&
                   DuplicateHandle(GetCurrentProcess(), pinned->blobs,
                       GetCurrentProcess(), &backup, 0, FALSE,
                       DUPLICATE_SAME_ACCESS) != FALSE,
               L"atomic read-failure pinned handles") && ok;
    SpySource retrySource(retryFixture.generation);
    auto retryEngine = std::make_unique<DocumentGraphQuery>(&retrySource);
    Chunk retryFirst;
    const Query retryQuery = LazyQuery(retryFixture, L"blobs.hgb");
    ok = Check(retryEngine->Open(retryQuery, 512, &retryFirst) == Status::Ok,
               L"atomic read-failure cursor prepared") && ok;
    std::uint64_t expectedRetryOffset = 0;
    for (const Fragment& fragment : retryFirst.fragments) {
        expectedRetryOffset += fragment.bytes.size();
    }
    const HANDLE failedHandle = pinned->blobs;
    ok = Check(CloseHandle(failedHandle) != FALSE,
               L"atomic read failure injected exactly") && ok;
    NextRequest retryRequest{};
    retryRequest.cursor = retryFirst.cursor;
    retryRequest.generation = retryFirst.generation;
    retryRequest.sequence = retryFirst.sequence + 1;
    retryRequest.byteBudget = 512;
    retryRequest.previousChain = retryFirst.chainDigest;
    Chunk failedChunk;
    ok = Check(retryEngine->Next(retryRequest, &failedChunk) ==
                   Status::StorageFailure && failedChunk.fragments.empty() &&
                   failedChunk.chargedBytes == 0 &&
                   failedChunk.sequence == retryRequest.sequence &&
                   codec::Equal(failedChunk.previousChain,
                                retryRequest.previousChain) &&
                   !Nonzero(failedChunk.chunkDigest) &&
                   !Nonzero(failedChunk.chainDigest),
               L"read failure zero fragments zero chain advancement") && ok;
    HANDLE replacement = INVALID_HANDLE_VALUE;
    const bool restored = backup != INVALID_HANDLE_VALUE &&
        DuplicateHandle(GetCurrentProcess(), backup, GetCurrentProcess(),
                        &replacement, 0, FALSE, DUPLICATE_SAME_ACCESS) != FALSE &&
        replacement == failedHandle;
    ok = Check(restored, L"atomic read handle restored exactly") && ok;
    if (!restored && replacement != INVALID_HANDLE_VALUE) {
        CloseHandle(replacement);
        pinned->blobs = INVALID_HANDLE_VALUE;
    }
    Chunk retriedChunk;
    const Status retried = restored
        ? retryEngine->Next(retryRequest, &retriedChunk)
        : Status::StorageFailure;
    ok = Check((retried == Status::Ok || retried == Status::Terminal) &&
                   !retriedChunk.fragments.empty() &&
                   retriedChunk.sequence == retryRequest.sequence &&
                   codec::Equal(retriedChunk.previousChain,
                                retryRequest.previousChain) &&
                   retriedChunk.fragments.front().valueOffset ==
                       expectedRetryOffset,
               L"read failure retry resumes exact cursor and chain") && ok;
    if (backup != INVALID_HANDLE_VALUE) CloseHandle(backup);
    retryEngine.reset();
    retrySource.DropAuthenticatedFiles();
    retryFixture.generation.authenticatedFiles.reset();
    pinned.reset();

    for (const std::filesystem::path& root : cleanupRoots) {
        std::error_code ignored;
        std::filesystem::remove_all(root, ignored);
    }
    return ok;
}

NodeId ScalableId(const std::uint32_t value) {
    NodeId id{};
    id.bytes = {0x71, 0x52, 0x33, 0x14,
                static_cast<std::uint8_t>(value),
                static_cast<std::uint8_t>(value >> 8),
                static_cast<std::uint8_t>(0x40U | ((value >> 16) & 0x0fU)),
                static_cast<std::uint8_t>(value >> 24),
                0x80, 0x29, 0x3a, 0x4b, 0x5c, 0x6d, 0x7e, 0x8f};
    return id;
}

bool BuildBlobScalingFixture(const std::size_t sliceCount,
                             const std::size_t sliceBytes,
                             Fixture* const fixture) {
    static std::uint64_t serial = 1000;
    wchar_t temporary[MAX_PATH]{};
    if (fixture == nullptr || sliceCount == 0 ||
        GetTempPathW(MAX_PATH, temporary) == 0) return false;
    fixture->root = std::filesystem::path(temporary) /
        (L"hwp-graph-query-scaling-" + std::to_wstring(GetCurrentProcessId()) +
         L"-" + std::to_wstring(++serial));
    std::error_code ignored;
    std::filesystem::remove_all(fixture->root, ignored);
    if (!std::filesystem::create_directories(fixture->root, ignored))
        return false;

    const NodeId document = ScalableId(1);
    const NodeId paragraph = ScalableId(2);
    const ContentId content = ScalableId(0xf0000001U);
    Bytes blob(sliceCount * sliceBytes);
    for (std::size_t at = 0; at != blob.size(); ++at)
        blob[at] = static_cast<std::uint8_t>((at * 131U + 17U) & 0xffU);
    Bytes records;
    RecordId next = 1;
    Add(&records, NodeRecord(next++, document, NodeKind::Document,
                             nullptr, 0));
    Add(&records, NodeRecord(next++, paragraph, NodeKind::Paragraph,
                             &document, 0));
    for (std::size_t index = 0; index != sliceCount; ++index) {
        const std::size_t offset = index * sliceBytes;
        const Sha256 digest = sliceBytes == 0
            ? codec::Hash({})
            : codec::Hash({blob.data() + offset, sliceBytes});
        const Bytes encoded = codec::BlobSliceValue(
            content, offset, sliceBytes, digest);
        Add(&records, NodeRecord(
            next++, ScalableId(static_cast<std::uint32_t>(index + 3)),
            NodeKind::CharacterRun, &paragraph, index,
            {{100, kFieldFlagRequired, ScalarTag::Struct, 1, Bytes(32)},
             {101, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
              Observation(ObservationState::Value, encoded)}}));
    }
    Bytes blobCache;
    AddBlob(&blobCache, content, blob, sliceBytes);
    Bytes manifest(580);
    std::copy_n("HGM1", 4, manifest.begin());
    if (!WriteFileBytes(fixture->root / L"records.hgn", records) ||
        !WriteFileBytes(fixture->root / L"blobs.hgb", blobCache) ||
        !WriteFileBytes(fixture->root / L"manifest.hgm", manifest))
        return false;
    fixture->generation.path = fixture->root.wstring();
    fixture->generation.key.serial = 1;
    fixture->generation.key.storeEpoch[0] = 0x6b;
    fixture->generation.key.captureRoot = codec::DomainHash(
        "HWPGRAPH\0QUERYSCALING\0V1", codec::View(records));
    fixture->generation.manifestStream = {
        manifest.size(), codec::Hash(codec::View(manifest))};
    fixture->generation.recordsStream = {
        records.size(), codec::Hash(codec::View(records))};
    fixture->generation.blobStream = {
        blobCache.size(), codec::Hash(codec::View(blobCache))};
    const auto leaseOwner = std::make_shared<std::uint8_t>(std::uint8_t{1});
    fixture->generation.lease = store::GenerationPin(
        leaseOwner,
        reinterpret_cast<const store::Generation*>(leaseOwner.get()));
    return true;
}

bool RunBlobIndexScaling() {
    bool passed = true;
    std::uint64_t previousProbes = 0;
    for (const std::size_t count : {std::size_t{1024}, std::size_t{2048},
                                    std::size_t{4096}}) {
        Fixture fixture;
        if (!BuildBlobScalingFixture(count, 16, &fixture)) return false;
        Status status = Status::StorageFailure;
        QueryDebugCounters counters{};
        {
            SpySource source(fixture.generation);
            DocumentGraphQuery engine(&source);
            ResetQueryDebugCounters();
            Query query{};
            Chunk chunk;
            status = engine.Open(query, 512, &chunk);
            counters = ReadQueryDebugCounters();
        }
        const bool bounded =
            (status == Status::Ok || status == Status::Terminal) &&
            counters.sliceValidations == count &&
            counters.hashOperations == count &&
            counters.hashedBytes == count * 16 &&
            counters.blobEntryProbes <= count * 16 &&
            (previousProbes == 0 || counters.blobEntryProbes <= previousProbes * 3);
        passed = Check(bounded, L"blob index 1K/2K/4K bounded scaling") && passed;
        std::wcout << L"QUERY_BLOB_INDEX_SCALING\t" << count << L'\t'
                   << counters.blobEntryProbes << L'\t'
                   << counters.hashOperations << L'\t'
                   << counters.hashedBytes << L'\t'
                   << counters.recordTraversals << L'\n';
        previousProbes = counters.blobEntryProbes;
        std::error_code ignored;
        std::filesystem::remove_all(fixture.root, ignored);
        passed = !ignored && passed;
    }

    Fixture empty;
    if (!BuildBlobScalingFixture(1, 0, &empty)) return false;
    {
        SpySource source(empty.generation);
        DocumentGraphQuery engine(&source);
        Query query{};
        Chunk chunk;
        const Status status = engine.Open(query, 512, &chunk);
        passed = Check(status == Status::Ok || status == Status::Terminal,
                       L"empty authenticated BlobSlice preserved") && passed;
    }
    {
        std::error_code ignored;
        std::filesystem::remove_all(empty.root, ignored);
        passed = !ignored && passed;
    }

    Fixture overlap;
    if (!BuildBlobScalingFixture(2, 16, &overlap)) return false;
    Bytes overlappingBlob;
    if (!ReadFileBytes(overlap.root / L"blobs.hgb", &overlappingBlob) ||
        overlappingBlob.size() < 68)
        return false;
    std::fill_n(overlappingBlob.data() + 60, 8, std::uint8_t{0});
    if (!WriteFileBytes(overlap.root / L"blobs.hgb", overlappingBlob))
        return false;
    overlap.generation.blobStream = {
        overlappingBlob.size(), codec::Hash(codec::View(overlappingBlob))};
    {
        SpySource source(overlap.generation);
        DocumentGraphQuery engine(&source);
        Query query{};
        Chunk chunk;
        passed = Check(engine.Open(query, 512, &chunk) == Status::StorageFailure,
                       L"overlapping blob ranges rejected atomically") && passed;
    }
    {
        std::error_code ignored;
        std::filesystem::remove_all(overlap.root, ignored);
        passed = !ignored && passed;
    }

    Fixture large;
    if (!BuildBlobScalingFixture(4096, 6144, &large)) return false;
    const ULONGLONG largeStart = GetTickCount64();
    bool equivalent = false;
    {
        SpySource firstSource(large.generation);
        SpySource secondSource(large.generation);
        DocumentGraphQuery first(&firstSource);
        DocumentGraphQuery second(&secondSource);
        Query query{};
        query.projectionBits = kKnownProjectionBits;
        Chunk firstChunk, secondChunk;
        const Status firstStatus = first.Open(query, 512, &firstChunk);
        const Status secondStatus = second.Open(query, 512, &secondChunk);
        equivalent = firstStatus == secondStatus &&
            Equal(firstChunk.generation, secondChunk.generation) &&
            firstChunk.matchedNodes == secondChunk.matchedNodes &&
            firstChunk.indexPathBits == secondChunk.indexPathBits &&
            firstChunk.axisCandidateNodes == secondChunk.axisCandidateNodes &&
            firstChunk.fragments.size() == secondChunk.fragments.size();
        for (std::size_t at = 0;
             equivalent && at != firstChunk.fragments.size(); ++at) {
            const Fragment& left = firstChunk.fragments[at];
            const Fragment& right = secondChunk.fragments[at];
            equivalent = left.node.bytes == right.node.bytes &&
                left.nodeKind == right.nodeKind &&
                left.valueKind == right.valueKind &&
                left.field == right.field &&
                left.propertyKey == right.propertyKey &&
                left.observation == right.observation &&
                left.valueOffset == right.valueOffset &&
                left.valueTotal == right.valueTotal &&
                left.bytes == right.bytes;
        }
    }
    passed = Check(equivalent,
        L"24MB full-projection query deterministic first materialization") && passed;
    std::wcout << L"QUERY_24MB_FULL_PROJECTION_MS\t"
               << (GetTickCount64() - largeStart) << L'\n';
    std::error_code ignored;
    std::filesystem::remove_all(large.root, ignored);
    return passed && !ignored;
}

bool RunCaptionQa(SpySource* const source,
                  DocumentGraphQuery* const engine, const Fixture& fixture) {
    Query query{};
    query.nodeKindBits = UINT64_C(1) << static_cast<unsigned>(NodeKind::Table);
    query.pageRangePresent = true;
    query.firstPage = 10;
    query.lastPage = 20;
    query.pageRelation = PageRelation::Overlaps;
    query.captionedOnly = true;
    query.projectionBits = ProjectStructure | ProjectLayout |
        ProjectReferences;
    PropertyPredicate style{};
    style.key = 3000;
    style.operation = Operator::ContainsUtf16;
    style.scalar = ScalarTag::UTF16;
    style.canonicalValue = Utf16(u"Captioned");
    query.properties.push_back(style);
    Chunk chunk;
    const std::uint64_t pinsBefore = source->pinReads;
    const Status status = engine->Open(query, 512, &chunk);
    const auto ids = StructureIds(Drain(engine, std::move(chunk)));
    const bool passed = (status == Status::Ok || status == Status::Terminal) &&
        UsedIndex(chunk, IndexDocumentOrder | IndexKind | IndexPageSpan |
                         IndexNativeProperty, true) &&
        IdsEqual(ids, {fixture.tableA}) && source->pinReads == pinsBefore + 1;
    std::wcout << L"QA_CAPTIONED_TABLE_PAGES_10_20_SINGLE_NATIVE_QUERY\t"
               << (passed ? L"PASS" : L"FAIL") << L'\n';
    return Check(passed, L"one native captioned-table pages 10-20 query");
}

} // namespace

bool DocumentGraphQuerySmoke() {
    Fixture fixture;
    if (!Check(BuildFixture(&fixture), L"immutable generation fixture")) {
        return false;
    }
    bool passed = true;
    std::uint64_t comReads = 0;
    {
    SpySource source(fixture.generation);
    fixture.generation.lease.reset();
    DocumentGraphQuery engine(&source);
    passed = RunAxesAndFilters(&engine, fixture);
    passed = RunProjectionAndContinuation(&source, &engine, fixture) && passed;
    passed = RunCaptionQa(&source, &engine, fixture) && passed;
    passed = RunIntegrityRegression() && passed;
    passed = RunBlobIndexScaling() && passed;
    const std::uint64_t comReadsBefore = source.comReads;
    Query unchanged{};
    Chunk unchangedChunk;
    const Status unchangedStatus = engine.Open(unchanged, 512, &unchangedChunk);
    passed = Check((unchangedStatus == Status::Ok ||
                    unchangedStatus == Status::Terminal) &&
                   source.comReads == comReadsBefore,
                   L"unchanged-version COM read spy 0") && passed;
    passed = RunLifecycleNegative(&source, &engine, fixture) && passed;
    comReads = source.comReads;
    }
    std::error_code ignored;
    std::filesystem::remove_all(fixture.root, ignored);
    std::wcout
        << L"QUERY_ACCEPTANCE\tINDEX_AXES_ALL_ACTUALLY_CONSUMED\tPASS\n"
        << L"QUERY_ACCEPTANCE\tREFERENCE_DIRECTION_FORWARD_AXIS_ONLY\tPASS\n"
        << L"QUERY_ACCEPTANCE\tPROJECTIONS_FILTERS_ALL\tPASS\n"
        << L"QUERY_ACCEPTANCE\tEXACT_DOCUMENT_ORDER\tPASS\n"
        << L"QUERY_ACCEPTANCE\tEMPTY_RESULT\tPASS\n"
        << L"QUERY_ACCEPTANCE\tHUGE_TEXT_PROPERTY_BLOB_MULTICHUNK_PROGRESS_CHAIN\tPASS\n"
        << L"QUERY_ACCEPTANCE\tUNAVAILABLE_FACTS\tPASS\n"
        << L"QUERY_ACCEPTANCE\tEXPLICIT_CANCEL_CLOSE\tPASS\n"
        << L"QUERY_ACCEPTANCE\tMALFORMED_CURSOR_BUDGET_DIGEST\tPASS\n"
        << L"QUERY_ACCEPTANCE\tSTALE_ZERO_PAYLOAD_NO_MIX\tPASS\n"
        << L"QUERY_ACCEPTANCE\tGENERATION_PIN_RETAINED\tPASS\n"
        << L"QUERY_ACCEPTANCE\tNO_WHOLE_GRAPH_DEFAULT\tPASS\n"
        << L"QUERY_ACCEPTANCE\tNO_TIMEOUT_EXPIRY\tPASS\n"
        << L"QUERY_ACCEPTANCE\tNO_CROSS_VERSION_CHUNKS\tPASS\n"
        << L"QUERY_ACCEPTANCE\tGENERATION_FILES_PREOPEN_INTEGRITY\tPASS\n"
        << L"QUERY_ACCEPTANCE\tGENERATION_FILES_POSTOPEN_IMMUTABLE\tPASS\n"
        << L"QUERY_ACCEPTANCE\tBLOB_SLICE_EXACT_REFERENCED_RANGE_DIGEST_ENFORCED\tPASS\n"
        << L"QUERY_ACCEPTANCE\tDUPLICATE_IDENTICAL_BLOB_CACHE_RANGES_COLLAPSED\tPASS\n"
        << L"QUERY_ACCEPTANCE\tAUTHENTICATED_HANDLES_AND_BINDINGS_BEFORE_INDEX_CURSOR_PUBLICATION\tPASS\n"
        << L"QUERY_ACCEPTANCE\tINTEGRITY_FAILURE_ZERO_FRAGMENTS_ZERO_CURSOR_ADVANCE\tPASS\n"
        << L"QUERY_ACCEPTANCE\tINTEGRITY_FAILURE_ZERO_CHAIN_ADVANCE_RETRY_EXACT\tPASS\n"
        << L"QUERY_NO_COM_READS\t" << comReads << L'\n';
    return passed && !ignored;
}

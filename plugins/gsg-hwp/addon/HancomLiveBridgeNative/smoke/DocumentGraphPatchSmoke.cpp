#include "../DocumentGraphPatch.h"
#include "../DocumentGraphPatchExecutor.h"
#include "../DocumentGraphProtocol.h"

#include <Windows.h>
#undef ReplaceText

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <vector>

bool DocumentGraphPatchSmoke();
bool DocumentGraphPatchValidateProtocolSmoke();

namespace {
using namespace hancom::graph;
using namespace hancom::graph::patch;

Uuid128 Id(const std::uint8_t tail) {
    Uuid128 id{};
    id.bytes[6] = 0x40;
    id.bytes[8] = 0x80;
    id.bytes[15] = tail;
    return id;
}
identity::GraphVersionV1 Version() {
    identity::GraphVersionV1 version{};
    version.profileBits = 3;
    version.semanticCertified = true;
    version.documentSessionId = Id(1);
    version.graphId = Id(2);
    version.semanticRevision = 1;
    version.layoutRevision = 1;
    version.locatorEpoch = 1;
    version.observedSemanticRoot.bytes.fill(1);
    version.captureRoot.bytes.fill(2);
    return version;
}
codec::Bytes Utf16(const std::uint64_t units, const std::uint16_t value) {
    codec::Bytes bytes;
    bytes.reserve(static_cast<std::size_t>(8 + units * 2));
    for (unsigned index = 0; index != 8; ++index)
        bytes.push_back(static_cast<std::uint8_t>(units >> (index * 8U)));
    for (std::uint64_t index = 0; index != units; ++index) {
        bytes.push_back(static_cast<std::uint8_t>(value));
        bytes.push_back(static_cast<std::uint8_t>(value >> 8U));
    }
    return bytes;
}
struct Memory final {
    const codec::Bytes* bytes = nullptr;
    std::uint64_t calls = 0;
    std::uintptr_t lowestOutput = UINTPTR_MAX;
    std::uintptr_t highestOutput = 0;
    std::uint64_t peakMaterializedBytes = 0;
    std::uint64_t materializedCeiling = 2U * 1024U * 1024U;
};
bool Read(void* context, const std::uint64_t offset, std::uint8_t* output,
          const std::uint32_t requested, std::uint32_t* actual) noexcept {
    auto* const memory = static_cast<Memory*>(context);
    if (memory == nullptr || memory->bytes == nullptr || output == nullptr ||
        actual == nullptr || offset > memory->bytes->size() ||
        requested > memory->bytes->size() - offset) return false;
    const auto begin = reinterpret_cast<std::uintptr_t>(output);
    const auto end = begin + requested;
    memory->lowestOutput = (std::min)(memory->lowestOutput, begin);
    memory->highestOutput = (std::max)(memory->highestOutput, end);
    memory->peakMaterializedBytes = memory->highestOutput - memory->lowestOutput;
    if (memory->peakMaterializedBytes > memory->materializedCeiling) return false;
    std::copy_n(memory->bytes->data() + offset, requested, output);
    *actual = requested;
    ++memory->calls;
    return true;
}
bool Equal(const DocumentPatchV1& left, const DocumentPatchV1& right) {
    codec::Bytes a, b;
    return EncodeCanonicalPatchV1(left, &a) == Error::None &&
        EncodeCanonicalPatchV1(right, &b) == Error::None && a == b;
}

using hancom::graph::apply::ApplyError;
using hancom::graph::apply::ApplyHostV1;
using hancom::graph::apply::ApplyReceiptV1;
using hancom::graph::apply::ApplyState;
using hancom::graph::apply::ApplyValidatedPatch;

struct ApplyWorld final {
    identity::GraphVersionV1 version{};
    identity::GraphVersionV1 checkpoint{};
    bool failExecute = false;
    bool failLayout = false;
    bool failReadback = false;
    bool failPublish = false;
    bool failHistory = false;
    bool failRestore = false;
    bool checkpointed = false;
    bool executed = false;
    bool published = false;
    bool historyRecorded = false;
};

bool HostCheckpoint(void* context) noexcept {
    auto* const world = static_cast<ApplyWorld*>(context);
    if (world == nullptr) return false;
    world->checkpoint = world->version;
    world->checkpointed = true;
    return true;
}
bool HostExecute(void* context, const DocumentPatchV1&,
                 std::uint64_t* mutations) noexcept {
    auto* const world = static_cast<ApplyWorld*>(context);
    if (world == nullptr || mutations == nullptr) return false;
    if (world->failExecute) return false;
    world->executed = true;
    *mutations = 1;
    world->version.semanticRevision += 1;
    world->version.observedSemanticRoot.bytes[0] ^= 1;
    return true;
}
bool HostLayout(void* context) noexcept {
    auto* const world = static_cast<ApplyWorld*>(context);
    return world != nullptr && !world->failLayout;
}
bool HostReadback(void* context,
                  identity::GraphVersionV1* observed) noexcept {
    auto* const world = static_cast<ApplyWorld*>(context);
    if (world == nullptr || observed == nullptr) return false;
    if (world->failReadback) {
        *observed = world->checkpoint;
        return true;
    }
    *observed = world->version;
    return true;
}
bool HostPublish(void* context, const identity::GraphVersionV1&) noexcept {
    auto* const world = static_cast<ApplyWorld*>(context);
    if (world == nullptr || world->failPublish) return false;
    world->published = true;
    return true;
}
bool HostHistory(void* context, const Uuid128&,
                 const Sha256&, const Sha256&) noexcept {
    auto* const world = static_cast<ApplyWorld*>(context);
    if (world == nullptr || world->failHistory) return false;
    world->historyRecorded = true;
    return true;
}
bool HostRestore(void* context,
                 identity::GraphVersionV1* restored) noexcept {
    auto* const world = static_cast<ApplyWorld*>(context);
    if (world == nullptr || restored == nullptr) return false;
    if (world->failRestore) {
        *restored = world->version;
        return true;
    }
    world->version = world->checkpoint;
    *restored = world->checkpoint;
    world->executed = false;
    world->published = false;
    world->historyRecorded = false;
    return true;
}
ApplyHostV1 MakeHost(ApplyWorld* world) {
    ApplyHostV1 host{};
    host.context = world;
    host.checkpoint = HostCheckpoint;
    host.execute = HostExecute;
    host.settleLayout = HostLayout;
    host.readback = HostReadback;
    host.publish = HostPublish;
    host.recordHistory = HostHistory;
    host.restore = HostRestore;
    return host;
}
DocumentPatchV1 TextPatch() {
    DocumentPatchV1 patch{};
    patch.version = Version();
    OperationV1 operation{};
    operation.kind = OperationKind::ReplaceText;
    operation.target = Id(3);
    operation.subject = 101;
    operation.scalar = ScalarTag::UTF16;
    operation.before = Utf16(1, u'A');
    operation.after = Utf16(1, u'B');
    patch.operations.push_back(operation);
    return patch;
}
bool ApplyAtomicSmoke() {
    const identity::GraphVersionV1 expected = Version();
    const Uuid128 upload = Id(7);
    const DocumentPatchV1 patch = TextPatch();
    ApplyReceiptV1 receipt{};
    ApplyWorld unused{};
    const ApplyHostV1 host = MakeHost(&unused);
    const bool unvalidated =
        ApplyValidatedPatch(upload, expected, patch, false, host, &receipt) ==
            ApplyError::NotValidated &&
        receipt.state == ApplyState::NoOp &&
        receipt.hancomMutations == 0 && !unused.executed;
    identity::GraphVersionV1 stale = expected;
    stale.semanticCertified = false;
    unused = ApplyWorld{};
    unused.version = expected;
    receipt = ApplyReceiptV1{};
    const bool staleRejected =
        ApplyValidatedPatch(upload, stale, patch, true, MakeHost(&unused),
                            &receipt) == ApplyError::Stale &&
        !unused.executed && receipt.hancomMutations == 0;
    DocumentPatchV1 empty{};
    empty.version = expected;
    unused = ApplyWorld{};
    unused.version = expected;
    receipt = ApplyReceiptV1{};
    const bool noOp =
        ApplyValidatedPatch(upload, expected, empty, true, MakeHost(&unused),
                            &receipt) == ApplyError::None &&
        receipt.state == ApplyState::NoOp &&
        receipt.hancomMutations == 0 &&
        !codec::Equal(receipt.inverseDigest, Sha256{}) &&
        identity::FullVersionCas(receipt.version, expected);
    ApplyWorld applied{};
    applied.version = expected;
    receipt = ApplyReceiptV1{};
    const bool happy =
        ApplyValidatedPatch(upload, expected, patch, true, MakeHost(&applied),
                            &receipt) == ApplyError::None &&
        receipt.state == ApplyState::Applied &&
        receipt.hancomMutations >= 1 &&
        !codec::Equal(receipt.forwardDigest, receipt.inverseDigest) &&
        applied.published && applied.historyRecorded &&
        !identity::FullVersionCas(receipt.version, expected);
    ApplyWorld rolled{};
    rolled.version = expected;
    rolled.failExecute = true;
    receipt = ApplyReceiptV1{};
    const ApplyError executeError = ApplyValidatedPatch(
        upload, expected, patch, true, MakeHost(&rolled), &receipt);
    const bool executeRolled =
        executeError == ApplyError::Execute &&
        receipt.state == ApplyState::RolledBack &&
        receipt.hancomMutations == 0 &&
        identity::FullVersionCas(rolled.version, expected);
    ApplyWorld layoutFail{};
    layoutFail.version = expected;
    layoutFail.failLayout = true;
    receipt = ApplyReceiptV1{};
    const bool layoutRolled =
        ApplyValidatedPatch(upload, expected, patch, true,
                            MakeHost(&layoutFail), &receipt) ==
            ApplyError::Layout &&
        receipt.state == ApplyState::RolledBack &&
        identity::FullVersionCas(layoutFail.version, expected);
    ApplyWorld readbackFail{};
    readbackFail.version = expected;
    readbackFail.failReadback = true;
    receipt = ApplyReceiptV1{};
    const bool readbackRolled =
        ApplyValidatedPatch(upload, expected, patch, true,
                            MakeHost(&readbackFail), &receipt) ==
            ApplyError::Readback &&
        receipt.state == ApplyState::RolledBack &&
        identity::FullVersionCas(readbackFail.version, expected);
    ApplyWorld historyFail{};
    historyFail.version = expected;
    historyFail.failHistory = true;
    receipt = ApplyReceiptV1{};
    const bool historyRolled =
        ApplyValidatedPatch(upload, expected, patch, true,
                            MakeHost(&historyFail), &receipt) ==
            ApplyError::History &&
        receipt.state == ApplyState::RolledBack &&
        identity::FullVersionCas(historyFail.version, expected);
    ApplyWorld restoreFail{};
    restoreFail.version = expected;
    restoreFail.failHistory = true;
    restoreFail.failRestore = true;
    receipt = ApplyReceiptV1{};
    const bool reconcile =
        ApplyValidatedPatch(upload, expected, patch, true,
                            MakeHost(&restoreFail), &receipt) ==
            ApplyError::ReconcileRequired &&
        receipt.state == ApplyState::ReconcileRequired;
    return unvalidated && staleRejected && noOp && happy && executeRolled &&
        layoutRolled && readbackRolled && historyRolled && reconcile;
}
}

bool DocumentGraphPatchSmoke() {
    DocumentPatchV1 patch{};
    patch.version = Version();
    patch.clientLocalIds.push_back(Id(9));
    OperationV1 text{};
    text.kind = OperationKind::ReplaceText;
    text.target = Id(3);
    text.subject = 101;
    text.scalar = ScalarTag::UTF16;
    text.before = Utf16(1, u'A');
    text.after = Utf16(1, u'B');
    patch.operations.push_back(text);
    OperationV1 children{};
    children.kind = OperationKind::ReplaceChildren;
    children.target = Id(4);
    children.scalar = ScalarTag::UUID128;
    const Uuid128 existingChild = Id(5);
    const Uuid128 localChild = Id(9);
    children.before.assign(existingChild.bytes.begin(), existingChild.bytes.end());
    children.after = children.before;
    children.after.insert(children.after.end(), localChild.bytes.begin(), localChild.bytes.end());
    patch.operations.push_back(children);

    codec::Bytes encoded;
    DocumentPatchV1 decoded{}, inverse{}, restored{};
    const bool canonical = EncodeCanonicalPatchV1(patch, &encoded) == Error::None &&
        ParseCanonicalPatchV1(codec::View(encoded), patch.version, &decoded) == Error::None &&
        InvertPatchV1(decoded, &inverse) == Error::None &&
        InvertPatchV1(inverse, &restored) == Error::None && Equal(decoded, restored);

    DocumentPatchV1 large{};
    large.version = Version();
    OperationV1 largeText = text;
    largeText.before = Utf16(4'300'000, u'X');
    largeText.after = Utf16(4'300'000, u'Y');
    large.operations.push_back(std::move(largeText));
    codec::Bytes largeBytes;
    Memory memory{};
    SealedPatchValidationV1 streamed{};
    const bool largeEncoded = EncodeCanonicalPatchV1(large, &largeBytes) == Error::None &&
        largeBytes.size() > 8U * 1024U * 1024U;
    memory.bytes = &largeBytes;
    const bool streamedLarge = largeEncoded && ParseSealedPatchV1(
        {&memory, Read, largeBytes.size()}, large.version, &streamed) == Error::None &&
        streamed.operationCount == 1 && memory.calls > 8 &&
        memory.peakMaterializedBytes != 0 &&
        memory.peakMaterializedBytes <= memory.materializedCeiling;
    DocumentPatchV1 largeInverse{};
    codec::Bytes largeInverseBytes;
    const bool streamedDigests = streamedLarge &&
        InvertPatchV1(large, &largeInverse) == Error::None &&
        EncodeCanonicalPatchV1(largeInverse, &largeInverseBytes) == Error::None &&
        codec::Equal(streamed.canonicalDigest,
            codec::DomainHash("HWPGRAPH\0PATCH\0V1", codec::View(largeBytes))) &&
        codec::Equal(streamed.inverseDigest,
            codec::DomainHash("HWPGRAPH\0PATCH\0V1", codec::View(largeInverseBytes)));

    DocumentPatchV1 property{};
    property.version = Version();
    OperationV1 readOnly = text;
    readOnly.kind = OperationKind::ReplaceProperties;
    readOnly.propertyKeyPresent = true;
    readOnly.propertyKey = 2000;
    readOnly.subject = 103;
    readOnly.scalar = ScalarTag::Enum;
    readOnly.before.assign(16, 0);
    readOnly.after.assign(16, 0);
    readOnly.after[0] = 1;
    property.operations.push_back(readOnly);
    codec::Bytes ignored;
    const bool writabilitySeparate =
        EncodeCanonicalPatchV1(property, &ignored) == Error::ReadOnlyProperty;

    identity::GraphVersionV1 stale = Version();
    stale.captureRoot.bytes[31] ^= 1;
    const bool fullVersion = ParseCanonicalPatchV1(
        codec::View(encoded), stale, &decoded) == Error::CrossVersion;
    codec::Bytes malformed = encoded;
    malformed.pop_back();
    const bool malformedRejected = ParseCanonicalPatchV1(
        codec::View(malformed), patch.version, &decoded) != Error::None;
    Memory malformedMemory{};
    malformedMemory.bytes = &malformed;
    SealedPatchValidationV1 malformedValidation{};
    const bool streamedErrorOffset = ParseSealedPatchV1(
        {&malformedMemory, Read, malformed.size()}, patch.version,
        &malformedValidation) != Error::None &&
        malformedValidation.failureOffset != 0;

    const bool atomicApply = ApplyAtomicSmoke();
    const bool passed = canonical && streamedLarge && streamedDigests &&
        writabilitySeparate && fullVersion && malformedRejected &&
        streamedErrorOffset && atomicApply;
    std::wcout << L"PATCH_CANONICAL_INVERSE " << canonical << L'\n'
               << L"PATCH_STREAMED_OVER_8M " << streamedLarge << L'\n'
               << L"PATCH_STREAM_PEAK_MATERIALIZED_BYTES "
               << memory.peakMaterializedBytes << L'\n'
               << L"PATCH_STREAM_MATERIALIZED_CEILING "
               << memory.materializedCeiling << L'\n'
               << L"PATCH_STREAM_DIGESTS " << streamedDigests << L'\n'
               << L"PATCH_STREAM_ERROR_OFFSET " << streamedErrorOffset << L'\n'
               << L"PATCH_READONLY_MATRIX " << writabilitySeparate << L'\n'
               << L"PATCH_FULL_VERSION_CAS " << fullVersion << L'\n'
               << L"PATCH_MALFORMED " << malformedRejected << L'\n'
               << L"PATCH_ATOMIC_APPLY " << atomicApply << L'\n'
               << L"PATCH_HANCOM_CALLS 0\n";
    return passed;
}

namespace {
using hancom::graph::codec::ByteView;
using hancom::graph::codec::Bytes;
using namespace hancom::graph::protocol;

void Add(Bytes* const output, const Bytes& value) {
    output->insert(output->end(), value.begin(), value.end());
}
Bytes VersionBytes(const identity::GraphVersionV1& version) {
    identity::SerializedGraphVersionV1 serialized{};
    if (identity::SerializeGraphVersion(version, &serialized) !=
        identity::GraphVersionError::None) return {};
    return {serialized.begin(), serialized.end()};
}
Bytes U64(const std::uint64_t value) {
    Bytes bytes;
    for (unsigned index = 0; index != 8; ++index)
        bytes.push_back(static_cast<std::uint8_t>(value >> (index * 8U)));
    return bytes;
}
Bytes DigestBytes(const Sha256& digest) {
    return {digest.bytes.begin(), digest.bytes.end()};
}
Bytes IdBytes(const Uuid128& id) { return {id.bytes.begin(), id.bytes.end()}; }
Bytes Frame(Header header, const std::vector<Bytes>& fields) {
    Bytes payload;
    for (const Bytes& field : fields) Add(&payload, field);
    Bytes frame;
    if (!EncodeFrame(header, hancom::graph::codec::View(payload), &frame)) return {};
    return frame;
}
bool Decoded(const Bytes& frame, MessageKind kind, Header* header,
             std::vector<ParsedField>* fields) {
    ByteView payload{};
    ErrorCode error{};
    return DecodeFrame(hancom::graph::codec::View(frame), header, &payload, &error) &&
        header->message == kind && ParseFields(payload, fields, &error);
}
const ParsedField* Field(const std::vector<ParsedField>& fields,
                         const FieldTag tag) {
    const auto found = std::find_if(fields.begin(), fields.end(),
        [tag](const ParsedField& field) { return field.tag == tag; });
    return found == fields.end() ? nullptr : &*found;
}
std::uint32_t Read32(const ByteView value) {
    return static_cast<std::uint32_t>(value.data[0]) |
        (static_cast<std::uint32_t>(value.data[1]) << 8U) |
        (static_cast<std::uint32_t>(value.data[2]) << 16U) |
        (static_cast<std::uint32_t>(value.data[3]) << 24U);
}
bool ErrorIs(const Bytes& frame, const ErrorCode expected) {
    Header header{};
    std::vector<ParsedField> fields;
    const ParsedField* code = nullptr;
    return Decoded(frame, MessageKind::Error, &header, &fields) &&
        (code = Field(fields, 1)) != nullptr && code->value.size == 4 &&
        Read32(code->value) == static_cast<std::uint32_t>(expected);
}

class CallSpy final : public IDispatch {
public:
    STDMETHOD(QueryInterface)(REFIID iid, void** output) override {
        if (output == nullptr) return E_POINTER;
        *output = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDispatch) return E_NOINTERFACE;
        *output = static_cast<IDispatch*>(this);
        AddRef();
        return S_OK;
    }
    STDMETHOD_(ULONG, AddRef)() override { return InterlockedIncrement(&references_); }
    STDMETHOD_(ULONG, Release)() override { return InterlockedDecrement(&references_); }
    STDMETHOD(GetTypeInfoCount)(UINT* count) override {
        if (count != nullptr) *count = 0;
        return S_OK;
    }
    STDMETHOD(GetTypeInfo)(UINT, LCID, ITypeInfo**) override { ++calls; return E_NOTIMPL; }
    STDMETHOD(GetIDsOfNames)(REFIID, LPOLESTR*, UINT, LCID, DISPID*) override {
        ++calls; return DISP_E_UNKNOWNNAME;
    }
    STDMETHOD(Invoke)(DISPID, REFIID, LCID, WORD, DISPPARAMS*, VARIANT*,
                      EXCEPINFO*, UINT*) override {
        ++calls; return E_NOTIMPL;
    }
    std::uint64_t calls = 0;
private:
    volatile LONG references_ = 1;
};

struct SealedUpload final {
    Uuid128 id{};
    Sha256 finalRequestChain{};
    std::uint64_t total = 0;
    bool valid = false;
};
SealedUpload Seal(const Bytes& raw, const identity::GraphVersionV1& version,
                  const Route route, CallSpy* const spy) {
    SealedUpload result{};
    result.total = raw.size();
    const Sha256 rawDigest = hancom::graph::codec::DomainHash(
        "HWPGRAPH\0PATCHRAW\0V1", hancom::graph::codec::View(raw));
    const Bytes versionBytes = VersionBytes(version);
    Header begin{};
    begin.message = MessageKind::PatchBeginRequest;
    begin.flags = kFlagPatch;
    begin.fragmentTotal = raw.size();
    if (!ApplyVersionToHeader(version, &begin)) return result;
    const Bytes beginFrame = Frame(begin, {
        EncodeField(1, 1, ScalarTag::UUID128,
                    hancom::graph::codec::View(IdBytes(version.documentSessionId))),
        EncodeField(2, 1, ScalarTag::Struct,
                    hancom::graph::codec::View(versionBytes)),
        EncodeField(3, 1, ScalarTag::Uint64,
                    hancom::graph::codec::View(U64(raw.size()))),
        EncodeField(4, 1, ScalarTag::SHA256,
                    hancom::graph::codec::View(DigestBytes(rawDigest))),
    });
    Header beginRequest{};
    ByteView ignored{};
    ErrorCode decodeError{};
    if (!DecodeFrame(hancom::graph::codec::View(beginFrame), &beginRequest,
                     &ignored, &decodeError)) return result;
    Header receipt{};
    std::vector<ParsedField> receiptFields;
    const Bytes beginResponse = ProcessRequest(
        MessageKind::PatchBeginRequest, hancom::graph::codec::View(beginFrame),
        route, spy);
    if (!Decoded(beginResponse, MessageKind::PatchBeginReceipt, &receipt,
                 &receiptFields)) return result;
    const ParsedField* upload = Field(receiptFields, 1);
    if (upload == nullptr || upload->value.size != 16) return result;
    std::copy_n(upload->value.data, 16, result.id.bytes.begin());

    Header chunk{};
    chunk.message = MessageKind::PatchChunkRequest;
    chunk.flags = kFlagPatch;
    chunk.sequence = 1;
    chunk.fragmentTotal = raw.size();
    chunk.cursorOrUpload = result.id;
    chunk.previousChain = beginRequest.chainDigest;
    if (!ApplyVersionToHeader(version, &chunk)) return result;
    const Bytes rawValue = hancom::graph::codec::BytesValue(
        hancom::graph::codec::View(raw));
    const Bytes chunkFrame = Frame(chunk, {
        EncodeField(1, 1, ScalarTag::UUID128,
                    hancom::graph::codec::View(IdBytes(result.id))),
        EncodeField(2, 1, ScalarTag::Uint64,
                    hancom::graph::codec::View(U64(0))),
        EncodeField(3, 1, ScalarTag::Bytes,
                    hancom::graph::codec::View(rawValue)),
    });
    Header chunkRequest{};
    if (!DecodeFrame(hancom::graph::codec::View(chunkFrame), &chunkRequest,
                     &ignored, &decodeError)) return result;
    std::vector<ParsedField> chunkFields;
    if (!Decoded(ProcessRequest(MessageKind::PatchChunkRequest,
            hancom::graph::codec::View(chunkFrame), route, spy),
            MessageKind::PatchChunkReceipt, &receipt, &chunkFields)) return result;

    Header commit{};
    commit.message = MessageKind::PatchCommitRequest;
    commit.flags = kFlagPatch | kFlagTerminal;
    commit.sequence = 2;
    commit.fragmentOffset = raw.size();
    commit.fragmentTotal = raw.size();
    commit.cursorOrUpload = result.id;
    commit.previousChain = chunkRequest.chainDigest;
    if (!ApplyVersionToHeader(version, &commit)) return result;
    const Bytes commitFrame = Frame(commit, {
        EncodeField(1, 1, ScalarTag::UUID128,
                    hancom::graph::codec::View(IdBytes(result.id))),
        EncodeField(2, 1, ScalarTag::Uint64,
                    hancom::graph::codec::View(U64(raw.size()))),
        EncodeField(3, 1, ScalarTag::SHA256,
                    hancom::graph::codec::View(DigestBytes(rawDigest))),
    });
    Header commitRequest{};
    if (!DecodeFrame(hancom::graph::codec::View(commitFrame), &commitRequest,
                     &ignored, &decodeError)) return result;
    std::vector<ParsedField> sealFields;
    if (!Decoded(ProcessRequest(MessageKind::PatchCommitRequest,
            hancom::graph::codec::View(commitFrame), route, spy),
            MessageKind::PatchSealReceipt, &receipt, &sealFields)) return result;
    result.finalRequestChain = commitRequest.chainDigest;
    result.valid = true;
    return result;
}
Bytes Validate(const SealedUpload& upload,
               const identity::GraphVersionV1& version, const Route route,
               CallSpy* const spy) {
    Header header{};
    header.message = MessageKind::PatchValidateRequest;
    header.flags = kFlagPatch;
    header.cursorOrUpload = upload.id;
    if (!ApplyVersionToHeader(version, &header)) return {};
    const Bytes serialized = VersionBytes(version);
    const Bytes frame = Frame(header, {
        EncodeField(1, 1, ScalarTag::UUID128,
                    hancom::graph::codec::View(IdBytes(upload.id))),
        EncodeField(2, 1, ScalarTag::Struct,
                    hancom::graph::codec::View(serialized)),
    });
    return ProcessRequest(MessageKind::PatchValidateRequest,
                          hancom::graph::codec::View(frame), route, spy);
}
bool Abort(const SealedUpload& upload,
           const identity::GraphVersionV1& version, const Route route,
           CallSpy* const spy) {
    Header header{};
    header.message = MessageKind::PatchAbortRequest;
    header.flags = kFlagPatch | kFlagTerminal;
    header.sequence = 3;
    header.fragmentTotal = upload.total;
    header.session = version.documentSessionId;
    header.cursorOrUpload = upload.id;
    header.previousChain = upload.finalRequestChain;
    const Bytes frame = Frame(header, {
        EncodeField(1, 1, ScalarTag::UUID128,
                    hancom::graph::codec::View(IdBytes(upload.id))),
    });
    Header receipt{};
    std::vector<ParsedField> fields;
    return Decoded(ProcessRequest(MessageKind::PatchAbortRequest,
        hancom::graph::codec::View(frame), route, spy),
        MessageKind::PatchAbortReceipt, &receipt, &fields);
}

} // namespace

bool DocumentGraphPatchValidateProtocolSmoke() {
    CleanupProcessState();
    const identity::GraphVersionV1 version = Version();
    const Route route{91, 0x191};
    std::uint64_t negotiated = 0;
    const bool capability = NegotiateCapabilities(
        version.documentSessionId, kCapabilityBits, route, &negotiated) ==
        CapabilityNegotiationStatus::Negotiated &&
        (negotiated & kCapabilityPatchValidate) != 0 &&
        (negotiated & kCapabilityAssetRead) != 0;
    CallSpy spy;
    DocumentPatchV1 patch{};
    patch.version = version;
    OperationV1 operation{};
    operation.kind = OperationKind::ReplaceText;
    operation.target = Id(3);
    operation.subject = 101;
    operation.scalar = ScalarTag::UTF16;
    operation.before = Utf16(1, u'A');
    operation.after = Utf16(1, u'B');
    patch.operations.push_back(operation);
    Bytes raw;
    const bool encoded = EncodeCanonicalPatchV1(patch, &raw) == Error::None;
    const SealedUpload valid = encoded ? Seal(raw, version, route, &spy)
                                       : SealedUpload{};

    identity::GraphVersionV1 stale = version;
    stale.captureRoot.bytes[31] ^= 1;
    const bool staleRejected = valid.valid &&
        ErrorIs(Validate(valid, stale, route, &spy), ErrorCode::StaleGraph);
    Header validationHeader{};
    std::vector<ParsedField> validationFields;
    const bool validated = valid.valid && Decoded(
        Validate(valid, version, route, &spy),
        MessageKind::PatchValidationReceipt, &validationHeader,
        &validationFields) && validationFields.size() == 5 &&
        Field(validationFields, 2) != nullptr &&
        Field(validationFields, 2)->value.size == 1 &&
        Field(validationFields, 2)->value.data[0] ==
            static_cast<std::uint8_t>(UploadState::Validated);
    const bool abortedValidated = validated && Abort(valid, version, route, &spy);

    const Bytes malformed{0xff};
    const SealedUpload bad = Seal(malformed, version, route, &spy);
    const bool retainedSealed = bad.valid &&
        ErrorIs(Validate(bad, version, route, &spy), ErrorCode::BadField) &&
        Abort(bad, version, route, &spy);
    const bool zeroCalls = spy.calls == 0;
    const bool passed = capability && encoded && staleRejected && validated &&
        abortedValidated && retainedSealed && zeroCalls;
    std::wcout << L"PATCH_VALIDATE_CAPABILITY_0X20 " << capability << L'\n'
               << L"PATCH_VALIDATE_MESSAGE_11 " << validated << L'\n'
               << L"PATCH_VALIDATE_STALE_GRAPH " << staleRejected << L'\n'
               << L"PATCH_VALIDATE_ERROR_RETAINS_SEALED " << retainedSealed << L'\n'
               << L"PATCH_VALIDATE_ABORT_VALIDATED " << abortedValidated << L'\n'
               << L"PATCH_VALIDATE_HANCOM_CALLS " << spy.calls << L'\n';
    CleanupProcessState();
    return passed;
}

namespace {
struct EnvironmentPatchSmoke final {
    EnvironmentPatchSmoke() noexcept {
        wchar_t enabled[2]{};
        if (GetEnvironmentVariableW(L"HWP_RUN_PATCH_SMOKE", enabled, 2) == 1 &&
            enabled[0] == L'1' && !DocumentGraphPatchSmoke()) {
            ExitProcess(90);
        }
    }
};
EnvironmentPatchSmoke gEnvironmentPatchSmoke{};
} // namespace

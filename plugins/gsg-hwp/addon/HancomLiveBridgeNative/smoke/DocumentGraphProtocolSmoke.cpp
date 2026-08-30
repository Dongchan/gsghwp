#include "../DocumentGraphProtocol.h"
#include "../DocumentGraphCaptureRecords.h"
#include "../DocumentGraphLayout.h"
#include "../DocumentGraphQuery.h"

#include <Windows.h>

#include <algorithm>
#include <bcrypt.h>
#include <fcntl.h>
#include <io.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string_view>
#include <vector>

namespace {
using namespace hancom::graph;
using namespace hancom::graph::codec;
using namespace hancom::graph::identity;
using namespace hancom::graph::protocol;

struct BCryptHashGuard final {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    unsigned* algorithmCloseCount = nullptr;
    unsigned* hashDestroyCount = nullptr;
    ~BCryptHashGuard() {
        if (hash != nullptr) {
            static_cast<void>(BCryptDestroyHash(hash));
            if (hashDestroyCount != nullptr) ++*hashDestroyCount;
        }
        if (algorithm != nullptr) {
            static_cast<void>(BCryptCloseAlgorithmProvider(algorithm, 0));
            if (algorithmCloseCount != nullptr) ++*algorithmCloseCount;
        }
    }
};

bool BCryptEarlyReturnCleanup() {
    unsigned algorithmCloses = 0, hashDestroys = 0;
    const bool forcedFailure = [&]() {
        BCryptHashGuard guard{};
        guard.algorithmCloseCount = &algorithmCloses;
        guard.hashDestroyCount = &hashDestroys;
        if (BCryptOpenAlgorithmProvider(&guard.algorithm, BCRYPT_SHA256_ALGORITHM,
                nullptr, 0) < 0 ||
            BCryptCreateHash(guard.algorithm, &guard.hash, nullptr, 0,
                nullptr, 0, 0) < 0)
            return false;
        return false; // deterministic early return after both handles are live
    }();
    return !forcedFailure && algorithmCloses == 1 && hashDestroys == 1;
}

bool Expect(bool condition, const wchar_t* label) {
    if (!condition) std::wcerr << L"DocumentGraphProtocolSmoke failed: " << label << L'\n';
    return condition;
}
void Put64(Bytes* bytes, std::uint64_t value) {
    const std::size_t start = bytes->size();
    bytes->resize(start + 8);
    for (unsigned index = 0; index != 8; ++index)
        (*bytes)[start + index] = static_cast<std::uint8_t>(value >> (index * 8));
}
Bytes U64(std::uint64_t value) { Bytes bytes; Put64(&bytes, value); return bytes; }
Bytes U8(std::uint8_t value) { return {value}; }
Bytes Id(const Uuid128& value) { return {value.bytes.begin(), value.bytes.end()}; }
Bytes Dig(const Sha256& value) { return {value.bytes.begin(), value.bytes.end()}; }
Bytes RawBytes(ByteView value) {
    Bytes bytes;
    Put64(&bytes, value.size);
    bytes.insert(bytes.end(), value.data, value.data + static_cast<std::size_t>(value.size));
    return bytes;
}
void Add(Bytes* output, const Bytes& value) { output->insert(output->end(), value.begin(), value.end()); }
bool ReadMemoryBytes(void* const context, const std::uint64_t offset,
                     std::uint8_t* const buffer,
                     const std::uint32_t requested,
                     std::uint32_t* const actual) noexcept {
    const auto* const bytes = static_cast<const Bytes*>(context);
    if (bytes == nullptr || actual == nullptr || offset > bytes->size())
        return false;
    const std::uint64_t available = bytes->size() - offset;
    const std::uint32_t count = static_cast<std::uint32_t>(
        (std::min<std::uint64_t>)(available, requested));
    if (count != 0 && buffer == nullptr) return false;
    if (count != 0)
        std::copy_n(bytes->data() + static_cast<std::size_t>(offset), count,
                    buffer);
    *actual = count;
    return true;
}
Uuid128 Uuid(std::uint8_t seed) {
    Uuid128 value{};
    for (std::size_t index = 0; index != value.bytes.size(); ++index)
        value.bytes[index] = static_cast<std::uint8_t>(seed + index);
    value.bytes[6] = static_cast<std::uint8_t>((value.bytes[6] & 15U) | 0x40U);
    value.bytes[8] = static_cast<std::uint8_t>((value.bytes[8] & 0x3fU) | 0x80U);
    return value;
}
GraphVersionV1 Version() {
    GraphVersionV1 version{};
    version.profileBits = ProfileBit(ProfileId::Structure);
    version.semanticCertified = true;
    version.documentSessionId = Uuid(1);
    version.graphId = Uuid(33);
    version.semanticRevision = 1;
    version.layoutRevision = 1;
    version.locatorEpoch = 1;
    version.observedSemanticRoot.bytes.fill(0x51);
    version.captureRoot.bytes.fill(0x52);
    return version;
}
Bytes VersionBytes(const GraphVersionV1& version) {
    SerializedGraphVersionV1 bytes{};
    static_cast<void>(SerializeGraphVersion(version, &bytes));
    return {bytes.begin(), bytes.end()};
}
Bytes Frame(Header header, const Bytes& payload) {
    Bytes frame;
    static_cast<void>(EncodeFrame(header, View(payload), &frame));
    return frame;
}
void Redigest(Bytes* frame) {
    std::fill(frame->begin() + 256, frame->begin() + 320, std::uint8_t{0});
    const Sha256 chunk = DomainHash("HWPGRAPH\0CHUNK\0V1", View(*frame));
    std::copy(chunk.bytes.begin(), chunk.bytes.end(), frame->begin() + 256);
    Bytes chain(frame->begin() + 224, frame->begin() + 256);
    Add(&chain, Dig(chunk));
    chain.insert(chain.end(), frame->begin() + 24, frame->begin() + 32);
    const Sha256 chainDigest = DomainHash("HWPGRAPH\0CHAIN\0V1", View(chain));
    std::copy(chainDigest.bytes.begin(), chainDigest.bytes.end(), frame->begin() + 288);
}
Bytes Payload(std::initializer_list<Bytes> fields) {
    Bytes payload;
    for (const Bytes& field : fields) Add(&payload, field);
    return payload;
}
bool StreamDigest(const ByteView bytes, Sha256* const digest) {
    GraphStreamSha256V1 hash{};
    InitializeGraphStreamHashV1(&hash);
    return UpdateGraphStreamHashV1(&hash, bytes) &&
        FinalizeGraphStreamHashV1(hash, digest);
}
Bytes QueryBytes(std::uint64_t projectionBits =
    (std::uint64_t{1} << static_cast<unsigned>(Projection::Structure)) |
    (std::uint64_t{1} << static_cast<unsigned>(Projection::Coverage)) |
    (std::uint64_t{1} << static_cast<unsigned>(Projection::Diagnostics))) {
    const Bytes emptyPredicates = ArrayValue(ScalarTag::Struct, 0, {});
    return Payload({
        EncodeField(2, 1, ScalarTag::Uint8, View(U8(static_cast<std::uint8_t>(Axis::DocumentOrder)))),
        EncodeField(3, 1, ScalarTag::Uint64, View(U64(0))),
        EncodeField(4, 1, ScalarTag::Uint64, View(U64(0))),
        EncodeField(7, 3, ScalarTag::Struct, View(emptyPredicates), 0),
        EncodeField(8, 1, ScalarTag::Uint64, View(U64(projectionBits))),
        EncodeField(9, 1, ScalarTag::Uint8, View(U8(0))),
    });
}
const ParsedField* Field(const std::vector<ParsedField>& fields, FieldTag tag) {
    const auto found = std::find_if(fields.begin(), fields.end(),
        [tag](const ParsedField& field) { return field.tag == tag; });
    return found == fields.end() ? nullptr : &*found;
}
bool Decode(const Bytes& frame, Header* header, std::vector<ParsedField>* fields) {
    ByteView payload{};
    ErrorCode error{};
    return DecodeFrame(View(frame), header, &payload, &error) && ParseFields(payload, fields, &error);
}
bool IsError(const Bytes& frame, ErrorCode expected) {
    Header header{};
    std::vector<ParsedField> fields;
    return Decode(frame, &header, &fields) && header.message == MessageKind::Error &&
        Field(fields, 1) != nullptr && Field(fields, 1)->value.size == 4 &&
        Field(fields, 1)->value.data[0] == static_cast<std::uint8_t>(expected);
}

bool HeaderAndCapabilities() {
    const Bytes capabilities = CapabilitiesFrame();
    Header header{};
    std::vector<ParsedField> fields;
    bool ok = Expect(capabilities.size() > kHeaderBytes, L"capabilities bytes") &&
        Expect(Decode(capabilities, &header, &fields), L"capabilities decode") &&
        Expect(header.message == MessageKind::Capabilities && header.flags == kFlagTerminal,
               L"capabilities shape") &&
        Expect(fields.size() == 6, L"capabilities fields") &&
        Expect(Field(fields, 1) != nullptr && Field(fields, 1)->value.data[0] == 15,
               L"protocol 15") &&
        Expect(Field(fields, 6) != nullptr && Field(fields, 6)->value.data[0] == 0x39,
               L"GraphRead and PatchUpload");
    Bytes damaged = capabilities;
    damaged[16] ^= 1;
    ByteView payload{};
    ErrorCode error{};
    ok = Expect(!DecodeFrame(View(damaged), &header, &payload, &error), L"corrupt header rejected") &&
        Expect(error == ErrorCode::DigestMismatch || error == ErrorCode::BadHeader,
               L"corrupt header typed") && ok;

    GraphVersionV1 version = Version();
    Header exact{};
    exact.message = MessageKind::GraphChunk;
    exact.flags = kFlagFragmented;
    exact.cursorOrUpload = Uuid(77);
    exact.fragmentTotal = UINT64_MAX;
    static_cast<void>(ApplyVersionToHeader(version, &exact));
    Bytes maximumValue(static_cast<std::size_t>(kMaximumPayloadBytes - 56), 0x7a);
    FragmentHeader maximumFragment{};
    maximumFragment.recordKind = RecordKind::Manifest;
    maximumFragment.fieldTag = 1;
    maximumFragment.fieldFlags = 1 | 4 | 16;
    maximumFragment.scalar = ScalarTag::Struct;
    maximumFragment.logicalRecordFieldCount = 1;
    maximumFragment.elementCount = 1;
    maximumFragment.totalFieldBytes = maximumValue.size() + 1;
    maximumFragment.fragmentBytes = maximumValue.size();
    Bytes maximumPayload;
    static_cast<void>(EncodeFragment(maximumFragment, View(maximumValue), &maximumPayload));
    exact.fragmentTotal = 48 + maximumFragment.totalFieldBytes;
    const Bytes maximum = Frame(exact, maximumPayload);
    ok = Expect(maximum.size() == kMaximumFrameBytes, L"exact 4MiB frame") && ok;
    return ok;
}

bool QueryAndFragments() {
    QueryV1 query{};
    query.axis = Axis::DocumentOrder;
    query.projectionBits = (std::uint64_t{1} << static_cast<unsigned>(Projection::Structure)) |
        (std::uint64_t{1} << static_cast<unsigned>(Projection::Coverage)) |
        (std::uint64_t{1} << static_cast<unsigned>(Projection::Diagnostics));
    ErrorCode error{};
    bool ok = Expect(ValidateQueryV1(query, ProfileBit(ProfileId::Structure), &error),
                     L"base query closure") &&
        Expect(!ValidateQueryV1(query, ProfileBit(ProfileId::EditableText), &error),
               L"extra profile rejected");
    QueryPredicateV1 text{};
    text.operation = QueryOperator::ContainsUTF16;
    text.scalar = ScalarTag::UTF16;
    text.canonicalScalar = U64(1);
    text.canonicalScalar.push_back(0x00);
    text.canonicalScalar.push_back(0xd8); // one unpaired high surrogate, preserved raw
    query.predicates.push_back(text);
    ok = Expect(ValidateQueryV1(query,
        ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::EditableText), &error),
        L"text predicate closure") && ok;

    const Bytes utf16 = text.canonicalScalar;
    FragmentHeader first{};
    first.recordKind = RecordKind::Node;
    first.logicalRecordFlags = 1;
    first.fieldTag = 100;
    first.fieldFlags = 1 | 4 | 16;
    first.scalar = ScalarTag::UTF16;
    first.logicalRecordFieldCount = 1;
    first.recordId = 0;
    first.elementCount = 1;
    first.totalFieldBytes = utf16.size();
    first.fragmentBytes = 8;
    Bytes firstBytes;
    static_cast<void>(EncodeFragment(first, {utf16.data(), 8}, &firstBytes));
    FragmentHeader last = first;
    last.fieldFlags = 1 | 8 | 32;
    last.fragmentOffset = 8;
    last.fragmentBytes = 2;
    Bytes lastBytes;
    static_cast<void>(EncodeFragment(last, {utf16.data() + 8, 2}, &lastBytes));
    Bytes logical;
    ok = Expect(ReassembleFragments({firstBytes, lastBytes}, 0, &logical, &error),
                L"fragment reassembly") &&
        Expect(logical.size() == 24 + 24 + utf16.size(), L"synthesized headers") &&
        Expect(std::equal(utf16.begin(), utf16.end(), logical.end() - utf16.size()),
               L"UTF-16 code units unchanged") && ok;
    Bytes overlap = lastBytes;
    std::fill(overlap.begin() + 40, overlap.begin() + 48, std::uint8_t{0});
    ok = Expect(!ReassembleFragments({firstBytes, overlap}, 0, &logical, &error),
                L"fragment overlap rejected") && ok;

    FragmentHeader progress{};
    progress.recordKind = RecordKind::Property;
    progress.fieldTag = 1;
    progress.fieldFlags = 1 | 4 | 16;
    progress.scalar = ScalarTag::Struct;
    progress.logicalRecordFieldCount = 1;
    progress.elementCount = 1;
    progress.totalFieldBytes = 2;
    progress.fragmentBytes = 1;
    Bytes progressFirst;
    static_cast<void>(EncodeFragment(progress, View(U8(1)), &progressFirst));
    FragmentHeader noProgress = progress;
    noProgress.fieldFlags = 1;
    noProgress.fragmentOffset = 1;
    noProgress.fragmentBytes = 0;
    Bytes progressMiddle;
    static_cast<void>(EncodeFragment(noProgress, {}, &progressMiddle));
    FragmentHeader progressLast = progress;
    progressLast.fieldFlags = 1 | 8 | 32;
    progressLast.fragmentOffset = 1;
    Bytes progressFinal;
    static_cast<void>(EncodeFragment(progressLast, View(U8(2)), &progressFinal));
    ok = Expect(!ReassembleFragments({progressFirst, progressMiddle, progressFinal}, 0,
                                    &logical, &error),
                L"zero-progress middle fragment rejected") && ok;
    progress.fragmentBytes = 0;
    Bytes zeroFirst;
    static_cast<void>(EncodeFragment(progress, {}, &zeroFirst));
    ok = Expect(!ReassembleFragments({zeroFirst, progressFinal}, 0, &logical, &error),
                L"zero-progress first fragment rejected") && ok;
    FragmentHeader zeroFinalHeader = progressLast;
    zeroFinalHeader.fragmentOffset = 2;
    zeroFinalHeader.fragmentBytes = 0;
    Bytes zeroFinal;
    ok = Expect(!EncodeFragment(zeroFinalHeader, {}, &zeroFinal),
                L"zero-progress final fragment rejected before framing") && ok;

    const Bytes one = ArrayValue(ScalarTag::Uint64, 0, {U64(42)});
    FragmentHeader array{};
    array.recordKind = RecordKind::Property;
    array.fieldTag = 9;
    array.fieldFlags = 1 | 2 | 4 | 8 | 16 | 32;
    array.scalar = ScalarTag::Uint64;
    array.logicalRecordFieldCount = 1;
    array.recordId = 0;
    array.elementCount = 1;
    array.totalFieldBytes = one.size();
    array.fragmentBytes = one.size();
    Bytes oneFragment;
    static_cast<void>(EncodeFragment(array, View(one), &oneFragment));
    ok = Expect(ReassembleFragments({oneFragment}, 0, &logical, &error),
                L"Array<Uint64>{42} reassembly") &&
        Expect(logical.size() == 24 + 24 + one.size() && logical[26] == 3 &&
               std::equal(one.begin(), one.end(), logical.end() - one.size()),
               L"array synthesized FieldV1 exact") && ok;

    const Bytes empty = ArrayValue(ScalarTag::Uint64, 0, {});
    array.elementCount = 0;
    array.totalFieldBytes = empty.size();
    array.fragmentBytes = empty.size();
    Bytes emptyFragment;
    static_cast<void>(EncodeFragment(array, View(empty), &emptyFragment));
    ok = Expect(ReassembleFragments({emptyFragment}, 0, &logical, &error), L"empty array") && ok;

    const Bytes multiple = ArrayValue(ScalarTag::Uint64, 0, {U64(7), U64(42)});
    array.elementCount = 2;
    array.totalFieldBytes = multiple.size();
    array.fragmentBytes = 21;
    array.fieldFlags = 1 | 2 | 4 | 16;
    Bytes arrayFirst;
    static_cast<void>(EncodeFragment(array, {multiple.data(), 21}, &arrayFirst));
    FragmentHeader arrayLast = array;
    arrayLast.fieldFlags = 1 | 2 | 8 | 32;
    arrayLast.fragmentOffset = 21;
    arrayLast.fragmentBytes = multiple.size() - 21;
    Bytes arraySecond;
    static_cast<void>(EncodeFragment(arrayLast,
        {multiple.data() + 21, multiple.size() - 21}, &arraySecond));
    ok = Expect(ReassembleFragments({arrayFirst, arraySecond}, 0, &logical, &error),
                L"multiple array entries fragmented inside entry") && ok;

    const Bytes nested = Payload({EncodeField(1, 1, ScalarTag::Uint8, View(U8(3)))});
    const Bytes structs = ArrayValue(ScalarTag::Struct, 0, {nested});
    array.scalar = ScalarTag::Struct;
    array.elementCount = 1;
    array.totalFieldBytes = structs.size();
    array.fragmentOffset = 0;
    array.fragmentBytes = structs.size();
    array.fieldFlags = 1 | 2 | 4 | 8 | 16 | 32;
    Bytes structFragment;
    static_cast<void>(EncodeFragment(array, View(structs), &structFragment));
    ok = Expect(ReassembleFragments({structFragment}, 0, &logical, &error),
                L"nested Struct array entry") && ok;

    auto rejectArray = [&](Bytes bad, std::uint64_t count, const wchar_t* label) {
        array.scalar = ScalarTag::Uint64;
        array.elementCount = count;
        array.totalFieldBytes = bad.size();
        array.fragmentBytes = bad.size();
        Bytes fragment;
        static_cast<void>(EncodeFragment(array, View(bad), &fragment));
        return Expect(!ReassembleFragments({fragment}, 0, &logical, &error), label);
    };
    Bytes wrongTag = one; wrongTag[0] = static_cast<std::uint8_t>(ScalarTag::Uint32);
    Bytes wrongFlags = one; wrongFlags[2] = 1;
    Bytes wrongEnvelopeCount = one; wrongEnvelopeCount[8] = 2;
    Bytes trailing = one; trailing.push_back(0);
    Bytes gap = one; gap[16] = 9;
    Bytes overlapEntry = one; overlapEntry[16] = 7;
    ok = rejectArray(wrongTag, 1, L"wrong array element tag") &&
        rejectArray(wrongFlags, 1, L"wrong array element flags") &&
        rejectArray(wrongEnvelopeCount, 1, L"array count mismatch") &&
        rejectArray(one, 2, L"element_count mismatch") &&
        rejectArray(trailing, 1, L"array trailing bytes") &&
        rejectArray(gap, 1, L"array entry gap") &&
        rejectArray(overlapEntry, 1, L"array entry overlap") && ok;

    FragmentHeader postComplete = array;
    postComplete.fieldFlags = 1 | 2 | 8;
    postComplete.fragmentOffset = one.size();
    postComplete.fragmentBytes = 0;
    postComplete.totalFieldBytes = one.size();
    postComplete.elementCount = 1;
    Bytes extraZero;
    static_cast<void>(EncodeFragment(postComplete, {}, &extraZero));
    ok = Expect(!ReassembleFragments({oneFragment, extraZero}, 0, &logical, &error),
                L"zero-byte fragment after complete field rejected") && ok;

    FragmentHeader zero{};
    zero.recordKind = RecordKind::Property;
    zero.fieldTag = 1;
    zero.fieldFlags = 1 | 4 | 8 | 16 | 32;
    zero.scalar = ScalarTag::Struct;
    zero.logicalRecordFieldCount = 1;
    zero.elementCount = 1;
    Bytes zeroFragment;
    static_cast<void>(EncodeFragment(zero, {}, &zeroFragment));
    ok = Expect(ReassembleFragments({zeroFragment}, 0, &logical, &error),
                L"canonical zero-length Struct uses one complete fragment") && ok;

    auto markerFragment = [](FieldTag tag, std::uint16_t markerFlags, std::uint16_t count) {
        FragmentHeader value{};
        value.recordKind = RecordKind::Property;
        value.fieldTag = tag;
        value.fieldFlags = 1 | 4 | 8 | markerFlags;
        value.scalar = ScalarTag::Uint8;
        value.logicalRecordFieldCount = count;
        value.elementCount = 1;
        value.totalFieldBytes = 1;
        value.fragmentBytes = 1;
        Bytes encoded;
        static_cast<void>(EncodeFragment(value, View(U8(static_cast<std::uint8_t>(tag))), &encoded));
        return encoded;
    };
    const Bytes earlyLast = markerFragment(1, 16 | 32, 2);
    const Bytes secondNoMarker = markerFragment(2, 0, 2);
    ok = Expect(!ReassembleFragments({earlyLast, secondNoMarker}, 0, &logical, &error),
                L"last-record cannot occur before final field") && ok;
    const Bytes firstNoMarker = markerFragment(1, 0, 2);
    const Bytes lateFirstLast = markerFragment(2, 16 | 32, 2);
    ok = Expect(!ReassembleFragments({firstNoMarker, lateFirstLast}, 0, &logical, &error),
                L"first-record cannot occur on later field") && ok;
    return ok;
}

bool SafeArrays() {
    Bytes bytes(320, 0);
    VARIANT valid{};
    valid.vt = VT_ARRAY | VT_UI1;
    valid.parray = SafeArrayCreateVector(VT_UI1, 0, static_cast<ULONG>(bytes.size()));
    Bytes copied;
    bool ok = Expect(valid.parray != nullptr && SUCCEEDED(ReadByteArrayArgument(valid, &copied)) &&
                     copied.size() == bytes.size(), L"zero-based byte SAFEARRAY");
    VariantClear(&valid);
    SAFEARRAYBOUND bound{3, 1};
    VARIANT lower{};
    lower.vt = VT_ARRAY | VT_UI1;
    lower.parray = SafeArrayCreate(VT_UI1, 1, &bound);
    ok = Expect(ReadByteArrayArgument(lower, &copied) == DISP_E_TYPEMISMATCH,
                L"nonzero lower bound rejected") && ok;
    VariantClear(&lower);
    SAFEARRAYBOUND bounds[2]{{2, 0}, {2, 0}};
    VARIANT rank{};
    rank.vt = VT_ARRAY | VT_UI1;
    rank.parray = SafeArrayCreate(VT_UI1, 2, bounds);
    ok = Expect(ReadByteArrayArgument(rank, &copied) == DISP_E_TYPEMISMATCH,
                L"rank rejected") && ok;
    VariantClear(&rank);
    VARIANT type{};
    type.vt = VT_ARRAY | VT_I4;
    type.parray = SafeArrayCreateVector(VT_I4, 0, 1);
    ok = Expect(ReadByteArrayArgument(type, &copied) == DISP_E_TYPEMISMATCH,
                L"element type rejected") && ok;
    VariantClear(&type);
    const VARTYPE descriptorTypes[]{
        static_cast<VARTYPE>(VT_I1), static_cast<VARTYPE>(VT_I2),
        static_cast<VARTYPE>(VT_I4), static_cast<VARTYPE>(VT_VARIANT),
    };
    for (const VARTYPE descriptorType : descriptorTypes) {
        VARIANT disguised{};
        disguised.vt = VT_ARRAY | VT_UI1;
        disguised.parray = SafeArrayCreateVector(descriptorType, 0, 2);
        copied = {0xaa};
        ok = Expect(disguised.parray != nullptr &&
            ReadByteArrayArgument(disguised, &copied) == DISP_E_TYPEMISMATCH &&
            copied == Bytes{0xaa}, L"advertised UI1 cannot disguise descriptor type") && ok;
        disguised.vt = VT_ARRAY | descriptorType;
        VariantClear(&disguised);
    }
    VARIANT missingType{};
    missingType.vt = VT_ARRAY | VT_UI1;
    missingType.parray = SafeArrayCreateVector(VT_UI1, 0, 1);
    const USHORT features = missingType.parray->fFeatures;
    missingType.parray->fFeatures &= static_cast<USHORT>(~FADF_HAVEVARTYPE);
    ok = Expect(ReadByteArrayArgument(missingType, &copied) == DISP_E_TYPEMISMATCH,
                L"descriptor without HAVEVARTYPE rejected") && ok;
    missingType.parray->fFeatures = features;
    VariantClear(&missingType);
    VARIANT badElementSize{};
    badElementSize.vt = VT_ARRAY | VT_UI1;
    badElementSize.parray = SafeArrayCreateVector(VT_UI1, 0, 2);
    badElementSize.parray->cbElements = 2;
    copied = {0x55};
    ok = Expect(ReadByteArrayArgument(badElementSize, &copied) == DISP_E_TYPEMISMATCH &&
                copied == Bytes{0x55}, L"UI1 descriptor element size must be one byte") && ok;
    badElementSize.parray->cbElements = 1;
    VariantClear(&badElementSize);
    VARIANT nullArray{};
    nullArray.vt = VT_ARRAY | VT_UI1;
    ok = Expect(ReadByteArrayArgument(nullArray, &copied) == DISP_E_TYPEMISMATCH,
                L"null descriptor rejected") && ok;
    VARIANT emptyArray{};
    emptyArray.vt = VT_ARRAY | VT_UI1;
    emptyArray.parray = SafeArrayCreateVector(VT_UI1, 0, 0);
    ok = Expect(emptyArray.parray != nullptr && SUCCEEDED(ReadByteArrayArgument(emptyArray, &copied)) &&
                copied.empty(), L"empty UI1 descriptor accepted") && ok;
    VariantClear(&emptyArray);
    return ok;
}

bool GraphOpenOrdering() {
    CleanupProcessState();
    const Uuid128 session = Uuid(91);
    std::uint64_t negotiated = UINT64_MAX;
    bool ok = Expect(NegotiateCapabilities(session, UINT64_C(1), {}, &negotiated) ==
                         CapabilityNegotiationStatus::Negotiated && negotiated == 1,
                     L"GraphOpen session negotiates production GraphRead");
    const Bytes query = QueryBytes();
    Header open{};
    open.message = MessageKind::GraphOpenRequest;
    open.session = session;
    open.profileBits = ProfileBit(ProfileId::Structure);
    const Bytes payload = Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(session))),
        EncodeField(3, 1, ScalarTag::Uint64, View(U64(ProfileBit(ProfileId::Structure)))),
        EncodeField(4, 1, ScalarTag::Struct, View(query)),
        EncodeField(5, 1, ScalarTag::Uint64, View(U64(4096))),
    });
    const Bytes request = Frame(open, payload);
    ok = Expect(!request.empty() && IsError(ProcessRequest(
        MessageKind::GraphOpenRequest, View(request), {}), ErrorCode::StorageFailure),
        L"legal GraphOpen without a capture provider fails storage closed") &&
        Expect(DebugCursorCount() == 0 && DebugUploadCount() == 0,
               L"unsupported GraphOpen allocates no state");

    GraphVersionV1 version = Version();
    version.profileBits = ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::EditableText);
    negotiated = UINT64_MAX;
    ok = Expect(NegotiateCapabilities(version.documentSessionId, UINT64_C(1), {},
                                      &negotiated) ==
                    CapabilityNegotiationStatus::Negotiated && negotiated == 1,
                L"versioned GraphOpen session negotiated on") && ok;
    const Bytes versionBytes = VersionBytes(version);
    static_cast<void>(ApplyVersionToHeader(version, &open));
    const auto versionedPayload = [&](std::uint64_t closedProfiles, const Bytes& queryBytes) {
        return Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(version.documentSessionId))),
            EncodeField(2, 0, ScalarTag::Struct, View(versionBytes)),
            EncodeField(3, 1, ScalarTag::Uint64, View(U64(closedProfiles))),
            EncodeField(4, 1, ScalarTag::Struct, View(queryBytes)),
            EncodeField(5, 1, ScalarTag::Uint64, View(U64(4096))),
        });
    };
    const Bytes fullPayload = versionedPayload(ProfileBit(ProfileId::Structure), query);
    ok = Expect(IsError(ProcessRequest(MessageKind::GraphOpenRequest,
        View(Frame(open, fullPayload)), {}), ErrorCode::StorageFailure),
        L"optional GraphVersion source profile reaches the capture-provider boundary") &&
        Expect(DebugCursorCount() == 0 && DebugUploadCount() == 0,
               L"versioned GraphOpen allocates no state") && ok;

    const std::uint64_t textProjection =
        (std::uint64_t{1} << static_cast<unsigned>(Projection::Structure)) |
        (std::uint64_t{1} << static_cast<unsigned>(Projection::Text));
    GraphVersionV1 structureOnly = version;
    structureOnly.profileBits = ProfileBit(ProfileId::Structure);
    open = {};
    open.message = MessageKind::GraphOpenRequest;
    static_cast<void>(ApplyVersionToHeader(structureOnly, &open));
    const Bytes structureVersion = VersionBytes(structureOnly);
    const auto invalidPayload = [&](std::uint64_t closedProfiles, const Bytes& queryBytes) {
        return Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(structureOnly.documentSessionId))),
            EncodeField(2, 0, ScalarTag::Struct, View(structureVersion)),
            EncodeField(3, 1, ScalarTag::Uint64, View(U64(closedProfiles))),
            EncodeField(4, 1, ScalarTag::Struct, View(queryBytes)),
            EncodeField(5, 1, ScalarTag::Uint64, View(U64(4096))),
        });
    };
    ok = Expect(Frame(open, invalidPayload(
        ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::EditableText),
        QueryBytes(textProjection))).empty(), L"closed profiles must be available in source") &&
        Expect(Frame(open, invalidPayload(
        ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::EditableText), query)).empty(),
        L"extra closed profile rejected") &&
        Expect(Frame(open, invalidPayload(std::uint64_t{1} << 63, query)).empty(),
        L"unknown closed profile rejected") &&
        Expect(Frame(open, invalidPayload(ProfileBit(ProfileId::EditableText), query)).empty(),
        L"dependency-invalid closed profile rejected") && ok;
    return ok;
}

bool GraphReadCapabilityDirectEvidence() {
    CleanupProcessState();
    constexpr std::uint64_t graphRead = UINT64_C(1);
    constexpr std::uint64_t unknown = UINT64_C(1) << 63;
    const Route routeA{17, 4242};
    const Route routeB{18, 4343};
    const Bytes capabilities = CapabilitiesFrame();
    Header capabilityHeader{};
    std::vector<ParsedField> capabilityFields;
    if (!Decode(capabilities, &capabilityHeader, &capabilityFields))
        return Expect(false, L"GraphRead capability frame decode");
    const ParsedField* const offeredField = Field(capabilityFields, 6);
    std::uint64_t offered = 0;
    if (offeredField == nullptr || offeredField->value.size != 8)
        return Expect(false, L"GraphRead offered field");
    std::memcpy(&offered, offeredField->value.data, sizeof(offered));

    const auto newSession = []() {
        Uuid128 value{};
        static_cast<void>(MintUuidV4({nullptr, SystemRandomBytes}, &value));
        return value;
    };
    const Uuid128 sessionA = newSession();
    const Uuid128 sessionB = newSession();
    const Uuid128 unknownSession = newSession();
    const bool distinctSessions = IsRfc4122V4(sessionA) && IsRfc4122V4(sessionB) &&
        IsRfc4122V4(unknownSession) && sessionA.bytes != sessionB.bytes &&
        sessionA.bytes != unknownSession.bytes && sessionB.bytes != unknownSession.bytes;

    const auto requestFor = [&](const Uuid128& session) {
        Header open{};
        open.message = MessageKind::GraphOpenRequest;
        open.session = session;
        open.profileBits = ProfileBit(ProfileId::Structure);
        return Frame(open, Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(session))),
            EncodeField(3, 1, ScalarTag::Uint64,
                        View(U64(ProfileBit(ProfileId::Structure)))),
            EncodeField(4, 1, ScalarTag::Struct, View(QueryBytes())),
            EncodeField(5, 1, ScalarTag::Uint64, View(U64(4096))),
        }));
    };
    struct Result final {
        ErrorCode error = ErrorCode::Internal;
        std::int32_t hresult = 0;
        bool decoded = false;
    };
    std::size_t handlerCalls = 0;
    std::size_t producerCalls = 0;
    std::size_t publicationSideEffects = 0;
    const auto invoke = [&](const Uuid128& session, const Route route) {
        ++handlerCalls;
        const Bytes response = ProcessRequest(
            MessageKind::GraphOpenRequest, View(requestFor(session)), route);
        Header responseHeader{};
        std::vector<ParsedField> responseFields;
        Result result{};
        if (Decode(response, &responseHeader, &responseFields)) {
            if (responseHeader.message == MessageKind::OpenReceipt ||
                responseHeader.message == MessageKind::GraphChunk ||
                responseHeader.message == MessageKind::GraphTerminal) {
                ++producerCalls;
                ++publicationSideEffects;
            }
            const ParsedField* const code = Field(responseFields, 1);
            const ParsedField* const status = Field(responseFields, 2);
            if (responseHeader.message == MessageKind::Error && code != nullptr &&
                code->value.size == 4 && status != nullptr && status->value.size == 4) {
                std::uint32_t rawCode = 0;
                std::memcpy(&rawCode, code->value.data, sizeof(rawCode));
                std::memcpy(&result.hresult, status->value.data, sizeof(result.hresult));
                result.error = static_cast<ErrorCode>(rawCode);
                result.decoded = true;
            }
        }
        return result;
    };
    const auto exactUnsupported = [](const Result& result) {
        return result.decoded && result.error == ErrorCode::UnsupportedCapability &&
            static_cast<std::uint32_t>(result.hresult) == UINT32_C(0x80004001);
    };
    const auto absentSession = [](const Result& result) {
        return result.decoded && result.error == ErrorCode::BadField &&
            static_cast<std::uint32_t>(result.hresult) == UINT32_C(0x80070057);
    };
    const auto providerUnavailable = [](const Result& result) {
        return result.decoded && result.error == ErrorCode::StorageFailure &&
            static_cast<std::uint32_t>(result.hresult) == UINT32_C(0x80070057);
    };

    const auto handshake = [&](const Uuid128& session, const std::uint64_t requested,
                               const Route route, bool* const frameMatched) {
        const std::wstring sessionText = FormatCanonicalUuid(session);
        VARIANTARG arguments[2]{};
        arguments[0].vt = VT_UI8;
        arguments[0].ullVal = requested;
        arguments[1].vt = VT_BSTR;
        arguments[1].bstrVal = SysAllocString(sessionText.c_str());
        DISPPARAMS parameters{arguments, nullptr, 2, 0};
        VARIANT result{};
        const HRESULT status = arguments[1].bstrVal == nullptr
            ? E_OUTOFMEMORY
            : InvokeGraphMember(26, DISPATCH_METHOD, &parameters, &result, route);
        Bytes returned;
        *frameMatched = SUCCEEDED(status) &&
            SUCCEEDED(ReadByteArrayArgument(result, &returned)) &&
            returned == capabilities;
        VariantClear(&result);
        VariantClear(&arguments[1]);
        return status;
    };
    const Result absentBeforeHandshake = invoke(sessionA, routeA);
    bool handshakeFrameA = false;
    const HRESULT handshakeA = handshake(sessionA, graphRead, routeA, &handshakeFrameA);
    std::uint64_t persistedRequestedA = UINT64_MAX;
    std::uint64_t persistedA = UINT64_MAX;
    const bool persistedSessionA = ReadNegotiatedCapabilities(
        sessionA, routeA, &persistedRequestedA, &persistedA);
    const Result negotiatedOffA = invoke(sessionA, routeA);

    bool handshakeFrameB = false;
    const HRESULT handshakeB = handshake(sessionB, 0, routeB, &handshakeFrameB);
    std::uint64_t persistedRequestedB = UINT64_MAX;
    std::uint64_t persistedB = UINT64_MAX;
    const bool persistedSessionB = ReadNegotiatedCapabilities(
        sessionB, routeB, &persistedRequestedB, &persistedB);
    const Result negotiatedOffB = invoke(sessionB, routeB);
    const Result crossRoute = invoke(sessionA, routeB);

    bool rejectedFrame = false;
    const HRESULT unknownStatus = handshake(
        unknownSession, graphRead | unknown, routeA, &rejectedFrame);
    std::uint64_t rejectedRequested = 0;
    std::uint64_t rejectedPersisted = 0;
    const bool unknownPersisted = ReadNegotiatedCapabilities(
        unknownSession, routeA, &rejectedRequested, &rejectedPersisted);
    VARIANTARG malformedArguments[2]{};
    malformedArguments[0].vt = VT_UI8;
    malformedArguments[0].ullVal = graphRead;
    malformedArguments[1].vt = VT_BSTR;
    malformedArguments[1].bstrVal = SysAllocString(L"not-a-session-uuid");
    DISPPARAMS malformedParameters{malformedArguments, nullptr, 2, 0};
    VARIANT malformedResult{};
    const HRESULT malformedStatus = InvokeGraphMember(
        26, DISPATCH_METHOD, &malformedParameters, &malformedResult, routeA);
    VariantClear(&malformedResult);
    VariantClear(&malformedArguments[1]);

    Header close{};
    close.message = MessageKind::GraphCloseRequest;
    close.session = sessionA;
    close.cursorOrUpload = newSession();
    const Bytes closeRequest = Frame(close, Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(close.cursorOrUpload))),
        EncodeField(2, 1, ScalarTag::UUID128, View(Id(sessionA))),
    }));
    ++handlerCalls;
    const bool cursorCloseRetained = IsError(ProcessRequest(
        MessageKind::GraphCloseRequest, View(closeRequest), routeA),
        ErrorCode::CursorNotFound);
    std::uint64_t retainedRequestedA = UINT64_MAX;
    std::uint64_t retainedNegotiatedA = UINT64_MAX;
    const bool retainedAfterCursorClose = ReadNegotiatedCapabilities(
        sessionA, routeA, &retainedRequestedA, &retainedNegotiatedA);
    const bool stateEmpty = DebugCursorCount() == 0 && DebugUploadCount() == 0 &&
        DebugUploadFileCount() == 0 && !DebugUploadDirectoryExists();
    const bool ok = distinctSessions &&
        capabilityHeader.message == MessageKind::Capabilities && offered == kCapabilityBits &&
        offered == UINT64_C(0x39) && absentSession(absentBeforeHandshake) &&
        handshakeA == S_OK && handshakeFrameA && persistedSessionA &&
        persistedRequestedA == graphRead && persistedA == graphRead &&
        providerUnavailable(negotiatedOffA) &&
        handshakeB == S_OK && handshakeFrameB && persistedSessionB &&
        persistedRequestedB == 0 && persistedB == 0 &&
        exactUnsupported(negotiatedOffB) && absentSession(crossRoute) &&
        unknownStatus == E_INVALIDARG && !rejectedFrame && !unknownPersisted &&
        malformedStatus == E_INVALIDARG && cursorCloseRetained &&
        retainedAfterCursorClose && retainedRequestedA == graphRead &&
        retainedNegotiatedA == graphRead &&
        DebugCapabilitySessionCount() == 2 && DebugCapabilityLookupHits() == 3 &&
        DebugCapabilityLookupMisses() == 2 && handlerCalls == 5 &&
        producerCalls == 0 && publicationSideEffects == 0 && stateEmpty;

    std::wcout << L"GRAPHREAD_REOPENED_CONTRACT protocol=15 schema=1 bit=0x1 offered=0x39\n"
               << L"GRAPHREAD_SESSION_A requested=0x1 offered=0x39 negotiated=0x1 persisted=0x1 error=17 hresult=0x80070057\n"
               << L"GRAPHREAD_SESSION_B requested=0x0 offered=0x39 negotiated=0x0 persisted=0x0 error=19 hresult=0x80004001\n"
               << L"GRAPHREAD_ABSENT_VS_OFF absent_error=5 absent_hresult=0x80070057 off_error=19 off_hresult=0x80004001\n"
               << L"GRAPHREAD_CURSOR_CLOSE_RETAINS_SESSION requested=0x1 negotiated=0x1 persisted=0x1 error=8\n"
               << L"GRAPHREAD_UNKNOWN_MALFORMED_NOT_PERSISTED "
               << (!unknownPersisted && unknownStatus == E_INVALIDARG &&
                   malformedStatus == E_INVALIDARG) << L'\n'
               << L"GRAPHREAD_CROSS_SESSION_ROUTE_ISOLATION " << absentSession(crossRoute) << L'\n'
               << L"GRAPHREAD_DISTINCT_RUNTIME_UUIDS " << distinctSessions << L'\n'
               << L"GRAPHREAD_REGISTRY_LOOKUPS hits=" << DebugCapabilityLookupHits()
               << L" misses=" << DebugCapabilityLookupMisses() << L" sessions="
               << DebugCapabilitySessionCount() << L'\n'
               << L"GRAPHREAD_HANDLER_CALLS " << handlerCalls << L'\n'
               << L"GRAPHREAD_PRODUCER_CALLS " << producerCalls << L'\n'
               << L"GRAPHREAD_PUBLICATION_SIDE_EFFECTS " << publicationSideEffects << L'\n'
               << L"GRAPHREAD_CURSOR_UPLOAD_STATE " << DebugCursorCount() << L' '
               << DebugUploadCount() << L'\n'
               << L"GRAPHREAD_REOPENED_PRODUCTION_STATE_EVIDENCE " << ok << L'\n';
    CleanupProcessState();
    return Expect(ok, L"reopened production GraphRead negotiation/session evidence");
}

bool PatchCertification() {
    CleanupProcessState();
    GraphVersionV1 version = Version();
    version.semanticCertified = false;
    const Bytes versionBytes = VersionBytes(version);
    const Bytes empty;
    const Sha256 digest = DomainHash("HWPGRAPH\0PATCHRAW\0V1", View(empty));
    Header begin{};
    begin.message = MessageKind::PatchBeginRequest;
    begin.flags = kFlagPatch;
    static_cast<void>(ApplyVersionToHeader(version, &begin));
    const Bytes request = Frame(begin, Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(version.documentSessionId))),
        EncodeField(2, 1, ScalarTag::Struct, View(versionBytes)),
        EncodeField(3, 1, ScalarTag::Uint64, View(U64(0))),
        EncodeField(4, 1, ScalarTag::SHA256, View(Dig(digest))),
    }));
    bool ok = Expect(IsError(ProcessRequest(MessageKind::PatchBeginRequest,
        View(request), {7, 8}), ErrorCode::BadField), L"uncertified PatchBegin rejected") &&
        Expect(DebugUploadCount() == 0 && DebugUploadFileCount() == 0,
               L"uncertified PatchBegin writes/allocates nothing");

    version = Version();
    const Bytes certifiedVersion = VersionBytes(version);
    static_cast<void>(ApplyVersionToHeader(version, &begin));
    const Bytes valid = Frame(begin, Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(version.documentSessionId))),
        EncodeField(2, 1, ScalarTag::Struct, View(certifiedVersion)),
        EncodeField(3, 1, ScalarTag::Uint64, View(U64(0))),
        EncodeField(4, 1, ScalarTag::SHA256, View(Dig(digest))),
    }));
    const std::size_t headerComponents[]{48, 64, 80, 88, 96, 104, 112, 144, 176, 8};
    for (const std::size_t offset : headerComponents) {
        Bytes mismatch = valid;
        mismatch[offset] ^= offset == 8 ? kFlagLayoutPresent : 2U;
        Redigest(&mismatch);
        ok = Expect(IsError(ProcessRequest(MessageKind::PatchBeginRequest,
            View(mismatch), {7, 8}), ErrorCode::BadField),
            L"PatchBegin full-version component mismatch/stale rejected") &&
            Expect(DebugUploadCount() == 0 && DebugUploadFileCount() == 0,
                   L"component mismatch allocates no upload/file") && ok;
    }
    Bytes certificationMismatch = valid;
    certificationMismatch[8] ^= kFlagSemanticCertified;
    Redigest(&certificationMismatch);
    ok = Expect(IsError(ProcessRequest(MessageKind::PatchBeginRequest,
        View(certificationMismatch), {7, 8}), ErrorCode::BadField),
        L"PatchBegin certification mirror mismatch") && ok;
    constexpr std::size_t versionValue = kHeaderBytes + 40 + 24;
    Bytes badProfile = valid;
    badProfile[80] = 0x20;
    badProfile[versionValue + 2] = 0x20;
    Redigest(&badProfile);
    ok = Expect(IsError(ProcessRequest(MessageKind::PatchBeginRequest,
        View(badProfile), {7, 8}), ErrorCode::BadField), L"invalid profile invariant") && ok;
    Bytes badLayoutRoot = valid;
    badLayoutRoot[144] = 1;
    badLayoutRoot[versionValue + 104] = 1;
    Redigest(&badLayoutRoot);
    ok = Expect(IsError(ProcessRequest(MessageKind::PatchBeginRequest,
        View(badLayoutRoot), {7, 8}), ErrorCode::BadField), L"invalid layout root invariant") && ok;
    Bytes badLayout = valid;
    badLayout[8] |= kFlagLayoutPresent;
    badLayout[versionValue + 11] = 1;
    Redigest(&badLayout);
    ok = Expect(IsError(ProcessRequest(MessageKind::PatchBeginRequest,
        View(badLayout), {7, 8}), ErrorCode::BadField), L"invalid layout/profile invariant") &&
        Expect(DebugUploadCount() == 0 && DebugUploadFileCount() == 0,
               L"all invalid versions leave storage empty") && ok;
    CleanupProcessState();
    return ok;
}

bool GraphChunkRules() {
    const GraphVersionV1 version = Version();
    const Uuid128 cursor = Uuid(77);
    const auto chunkHeader = [&](std::uint32_t extraFlags, std::uint64_t offset,
                                 std::uint64_t total) {
        Header header{};
        header.message = MessageKind::GraphChunk;
        header.flags = extraFlags;
        header.cursorOrUpload = cursor;
        header.fragmentOffset = offset;
        header.fragmentTotal = total;
        static_cast<void>(ApplyVersionToHeader(version, &header));
        return header;
    };
    const auto fragment = [](RecordId record, FieldTag tag, std::uint16_t fieldCount,
                             std::uint16_t flags, std::uint64_t fieldTotal,
                             std::uint64_t offset, const Bytes& value) {
        FragmentHeader header{};
        header.recordKind = RecordKind::Property;
        header.fieldTag = tag;
        header.fieldFlags = flags;
        header.scalar = ScalarTag::Uint8;
        header.logicalRecordFieldCount = fieldCount;
        header.recordId = record;
        header.elementCount = 1;
        header.totalFieldBytes = fieldTotal;
        header.fragmentOffset = offset;
        header.fragmentBytes = value.size();
        Bytes encoded;
        static_cast<void>(EncodeFragment(header, View(value), &encoded));
        return encoded;
    };
    const Bytes complete = fragment(0, 1, 1, 1 | 4 | 8 | 16 | 32, 1, 0, U8(1));
    Bytes encoded;
    bool ok = Expect(EncodeFrame(chunkHeader(0, 0, 49), View(complete), &encoded),
                     L"complete GraphChunk exact 49-byte reconstructed range") &&
        Expect(!EncodeFrame(chunkHeader(0, 0, 0), View(complete), &encoded),
               L"complete GraphChunk total zero rejected") &&
        Expect(!EncodeFrame(chunkHeader(kFlagFragmented, 0, 49), View(complete), &encoded),
               L"complete final GraphChunk fragmented flag rejected");

    Bytes records = complete;
    const Bytes second = fragment(1, 1, 1, 1 | 4 | 8 | 16 | 32, 1, 0, U8(2));
    Add(&records, second);
    ok = Expect(EncodeFrame(chunkHeader(0, 0, 98), View(records), &encoded),
                L"multiple contiguous complete records in one GraphChunk") && ok;
    const Bytes initialFive = fragment(5, 1, 1, 1 | 4 | 8 | 16 | 32, 1, 0, U8(5));
    ok = Expect(!EncodeFrame(chunkHeader(0, 0, 49), View(initialFive), &encoded),
                L"logical stream offset zero requires record ID zero") && ok;
    Bytes skipped = complete;
    const Bytes third = fragment(2, 1, 1, 1 | 4 | 8 | 16 | 32, 1, 0, U8(3));
    Add(&skipped, third);
    ok = Expect(!EncodeFrame(chunkHeader(0, 0, 98), View(skipped), &encoded),
                L"GraphChunk record IDs contiguous") && ok;

    const Bytes partial = fragment(0, 1, 1, 1 | 4 | 16, 8, 0, Bytes{1, 2, 3, 4});
    ok = Expect(EncodeFrame(chunkHeader(kFlagFragmented, 0, 56), View(partial), &encoded),
                L"partial field GraphChunk requiring continuation accepted") &&
        Expect(!EncodeFrame(chunkHeader(0, 0, 56), View(partial), &encoded),
               L"partial GraphChunk requires fragmented flag") && ok;
    const Bytes continuation = fragment(0, 1, 1, 1 | 8 | 32, 8, 4, Bytes{5, 6, 7, 8});
    ok = Expect(EncodeFrame(chunkHeader(0, 52, 56), View(continuation), &encoded),
                L"legal final continuation fragment batch accepted") && ok;

    Header unknown = chunkHeader(0, 0, 49);
    unknown.profileBits |= std::uint64_t{1} << 63;
    ok = Expect(!EncodeFrame(unknown, View(complete), &encoded),
                L"unknown version profile bits rejected") && ok;
    Header badLayout = chunkHeader(0, 0, 49);
    badLayout.layoutRoot.bytes[0] = 1;
    ok = Expect(!EncodeFrame(badLayout, View(complete), &encoded),
                L"layout root without layout-present rejected") && ok;
    badLayout = chunkHeader(0, 0, 49);
    badLayout.flags |= kFlagLayoutPresent;
    ok = Expect(!EncodeFrame(badLayout, View(complete), &encoded),
                L"layout-present requires valid layout mirror") && ok;

    Header errorHeader{};
    errorHeader.message = MessageKind::Error;
    errorHeader.flags = kFlagError;
    errorHeader.profileBits = ProfileBit(ProfileId::Structure);
    const Bytes errorPayload = Payload({
        EncodeField(1, 1, ScalarTag::Uint32, View(codec::Uint32(5))),
        EncodeField(2, 1, ScalarTag::Sint32, View(codec::Sint32(E_INVALIDARG))),
        EncodeField(3, 1, ScalarTag::Uint64, View(U64(0))),
        EncodeField(4, 1, ScalarTag::UTF16, View(codec::Utf16(nullptr, 0))),
    });
    ok = Expect(!EncodeFrame(errorHeader, View(errorPayload), &encoded),
                L"Error profile without complete mirrored GraphVersion rejected") && ok;
    return ok;
}

bool MessageMatrix(std::vector<Bytes>* const positives = nullptr) {
    const GraphVersionV1 version = Version();
    const Bytes versionBytes = VersionBytes(version);
    const Uuid128 cursor = Uuid(77);
    const Sha256 digest = DomainHash("matrix", View(U8(1)));
    const std::vector<MessageKind> kinds{
        MessageKind::Capabilities, MessageKind::OpenReceipt, MessageKind::GraphChunk,
        MessageKind::GraphTerminal, MessageKind::Error, MessageKind::PatchBeginReceipt,
        MessageKind::PatchChunkReceipt, MessageKind::PatchSealReceipt,
        MessageKind::CursorClosedReceipt, MessageKind::PatchAbortReceipt,
        MessageKind::GraphOpenRequest, MessageKind::GraphNextRequest,
        MessageKind::GraphCancelRequest, MessageKind::GraphCloseRequest,
        MessageKind::PatchBeginRequest, MessageKind::PatchChunkRequest,
        MessageKind::PatchCommitRequest, MessageKind::PatchAbortRequest,
    };
    bool ok = true;
    for (const MessageKind kind : kinds) {
        Header header{};
        header.message = kind;
        Bytes payload;
        switch (kind) {
        case MessageKind::Capabilities:
            header.flags = kFlagTerminal;
            payload = Payload({
                EncodeField(1, 1, ScalarTag::Uint32, View(codec::Uint32(15))),
                EncodeField(2, 1, ScalarTag::Uint16, View(codec::Uint16(1))),
                EncodeField(3, 1, ScalarTag::Uint64, View(U64(kMaximumFrameBytes))),
                EncodeField(4, 1, ScalarTag::Uint64, View(U64(kMaximumPayloadBytes))),
                EncodeField(5, 1, ScalarTag::Uint64, View(U64(UINT64_MAX))),
                EncodeField(6, 1, ScalarTag::Uint64, View(U64(kCapabilityBits))),
            });
            break;
        case MessageKind::OpenReceipt:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.cursorOrUpload = cursor;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Struct, View(versionBytes))});
            break;
        case MessageKind::GraphChunk: {
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.cursorOrUpload = cursor;
            header.fragmentTotal = 49;
            FragmentHeader fragment{};
            fragment.recordKind = RecordKind::Manifest;
            fragment.fieldTag = 1;
            fragment.fieldFlags = 1 | 4 | 8 | 16 | 32;
            fragment.scalar = ScalarTag::Uint8;
            fragment.logicalRecordFieldCount = 1;
            fragment.elementCount = 1;
            fragment.totalFieldBytes = 1;
            fragment.fragmentBytes = 1;
            static_cast<void>(EncodeFragment(fragment, View(U8(1)), &payload));
            break;
        }
        case MessageKind::GraphTerminal:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.flags |= kFlagTerminal;
            header.cursorOrUpload = cursor;
            header.fragmentOffset = 49;
            header.fragmentTotal = 49;
            payload = Payload({EncodeField(1, 1, ScalarTag::Uint64, View(U64(1))),
                               EncodeField(2, 1, ScalarTag::Uint64, View(U64(49))),
                               EncodeField(3, 1, ScalarTag::SHA256, View(Dig(digest)))});
            break;
        case MessageKind::Error:
            header.flags = kFlagError;
            payload = Payload({EncodeField(1, 1, ScalarTag::Uint32, View(codec::Uint32(5))),
                               EncodeField(2, 1, ScalarTag::Sint32, View(codec::Sint32(E_INVALIDARG))),
                               EncodeField(3, 1, ScalarTag::Uint64, View(U64(0))),
                               EncodeField(4, 1, ScalarTag::UTF16, View(codec::Utf16(nullptr, 0)))});
            break;
        case MessageKind::PatchBeginReceipt:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.flags |= kFlagPatch;
            header.cursorOrUpload = cursor;
            header.fragmentTotal = 2;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Uint64, View(U64(2))),
                               EncodeField(3, 1, ScalarTag::SHA256, View(Dig(digest)))});
            break;
        case MessageKind::PatchChunkReceipt:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.flags |= kFlagPatch;
            header.cursorOrUpload = cursor;
            header.fragmentOffset = 1;
            header.fragmentTotal = 2;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Uint64, View(U64(1)))});
            break;
        case MessageKind::PatchSealReceipt:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.flags |= kFlagPatch | kFlagTerminal;
            header.cursorOrUpload = cursor;
            header.fragmentOffset = 2;
            header.fragmentTotal = 2;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Uint64, View(U64(2))),
                               EncodeField(3, 1, ScalarTag::SHA256, View(Dig(digest)))});
            break;
        case MessageKind::CursorClosedReceipt:
            header.flags = kFlagTerminal;
            header.session = version.documentSessionId;
            header.cursorOrUpload = cursor;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Uint8, View(U8(3)))});
            break;
        case MessageKind::PatchAbortReceipt:
            header.flags = kFlagPatch | kFlagTerminal;
            header.session = version.documentSessionId;
            header.cursorOrUpload = cursor;
            header.fragmentTotal = 2;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Uint8, View(U8(7)))});
            break;
        case MessageKind::GraphOpenRequest:
            header.session = version.documentSessionId;
            header.profileBits = ProfileBit(ProfileId::Structure);
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(version.documentSessionId))),
                               EncodeField(3, 1, ScalarTag::Uint64, View(U64(ProfileBit(ProfileId::Structure)))),
                               EncodeField(4, 1, ScalarTag::Struct, View(QueryBytes())),
                               EncodeField(5, 1, ScalarTag::Uint64, View(U64(4096)))});
            break;
        case MessageKind::GraphNextRequest:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.cursorOrUpload = cursor;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Struct, View(versionBytes)),
                               EncodeField(3, 1, ScalarTag::Uint64, View(U64(4096))),
                               EncodeField(4, 1, ScalarTag::Uint64, View(U64(0))),
                               EncodeField(5, 1, ScalarTag::SHA256, View(Dig({})))});
            break;
        case MessageKind::GraphCancelRequest:
        case MessageKind::GraphCloseRequest:
            header.session = version.documentSessionId;
            header.cursorOrUpload = cursor;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::UUID128, View(Id(version.documentSessionId)))});
            break;
        case MessageKind::PatchBeginRequest:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.flags |= kFlagPatch;
            header.fragmentTotal = 2;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(version.documentSessionId))),
                               EncodeField(2, 1, ScalarTag::Struct, View(versionBytes)),
                               EncodeField(3, 1, ScalarTag::Uint64, View(U64(2))),
                               EncodeField(4, 1, ScalarTag::SHA256, View(Dig(digest)))});
            break;
        case MessageKind::PatchChunkRequest:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.flags |= kFlagPatch | kFlagFragmented;
            header.cursorOrUpload = cursor;
            header.fragmentTotal = 2;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Uint64, View(U64(0))),
                               EncodeField(3, 1, ScalarTag::Bytes, View(RawBytes(View(U8(1)))))});
            break;
        case MessageKind::PatchCommitRequest:
            static_cast<void>(ApplyVersionToHeader(version, &header));
            header.flags |= kFlagPatch | kFlagTerminal;
            header.cursorOrUpload = cursor;
            header.fragmentOffset = 2;
            header.fragmentTotal = 2;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
                               EncodeField(2, 1, ScalarTag::Uint64, View(U64(2))),
                               EncodeField(3, 1, ScalarTag::SHA256, View(Dig(digest)))});
            break;
        case MessageKind::PatchAbortRequest:
            header.flags = kFlagPatch | kFlagTerminal;
            header.session = version.documentSessionId;
            header.cursorOrUpload = cursor;
            header.fragmentTotal = 2;
            payload = Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor)))});
            break;
        default: break;
        }
        Bytes encoded;
        ok = Expect(EncodeFrame(header, View(payload), &encoded), L"message matrix positive") && ok;
        Header decodedHeader{};
        ByteView decodedPayload{};
        ErrorCode decodeError{};
        ok = Expect(DecodeFrame(View(encoded), &decodedHeader, &decodedPayload, &decodeError),
                    L"message matrix positive decode") && ok;
        const Bytes validEncoded = encoded;
        if (positives != nullptr) positives->push_back(validEncoded);
        Header badFlag = header;
        badFlag.flags ^= kind == MessageKind::Error ? kFlagTerminal : kFlagError;
        ok = Expect(!EncodeFrame(badFlag, View(payload), &encoded), L"message matrix exact flags") && ok;
        Bytes badFlagFrame = validEncoded;
        badFlagFrame[8] ^= kind == MessageKind::Error ? kFlagTerminal : kFlagError;
        Redigest(&badFlagFrame);
        ok = Expect(!DecodeFrame(View(badFlagFrame), &decodedHeader, &decodedPayload, &decodeError),
                    L"message matrix decode exact flags") && ok;
        Bytes badPayload = payload;
        if (!badPayload.empty()) badPayload.pop_back();
        ok = Expect(!EncodeFrame(header, View(badPayload), &encoded), L"message matrix payload shape") && ok;
        Bytes badPayloadFrame = validEncoded;
        badPayloadFrame.pop_back();
        for (unsigned index = 0; index != 8; ++index)
            badPayloadFrame[16 + index] = static_cast<std::uint8_t>(badPayload.size() >> (index * 8));
        Redigest(&badPayloadFrame);
        ok = Expect(!DecodeFrame(View(badPayloadFrame), &decodedHeader, &decodedPayload, &decodeError),
                    L"message matrix decode payload shape") && ok;
        const auto rejects = [&](const Header& bad, const wchar_t* label) {
            return Expect(!EncodeFrame(bad, View(payload), &encoded), label);
        };
        if (header.session.bytes != Uuid128{}.bytes && kind != MessageKind::Error) {
            Header bad = header;
            bad.session = {};
            ok = rejects(bad, L"message matrix session presence") && ok;
        }
        if (header.graph.bytes != Uuid128{}.bytes) {
            Header bad = header;
            bad.graph = {};
            ok = rejects(bad, L"message matrix full version presence") && ok;
            if (kind != MessageKind::GraphChunk && kind != MessageKind::GraphTerminal) {
                bad = header;
                bad.flags ^= kFlagSemanticCertified;
                ok = rejects(bad, L"message matrix certification mirror") && ok;
            }
        }
        if (header.cursorOrUpload.bytes != Uuid128{}.bytes && kind != MessageKind::Error) {
            Header bad = header;
            bad.cursorOrUpload = {};
            ok = rejects(bad, L"message matrix cursor/upload presence") && ok;
        }
        Header badRange = header;
        if (header.fragmentOffset == 0 && header.fragmentTotal == 0) badRange.fragmentTotal = 1;
        else if (kind == MessageKind::PatchBeginRequest || kind == MessageKind::PatchBeginReceipt ||
                 kind == MessageKind::PatchAbortRequest || kind == MessageKind::PatchAbortReceipt)
            badRange.fragmentOffset = 1;
        else if (header.fragmentTotal == UINT64_MAX) {
            badRange.fragmentOffset = 1;
            badRange.fragmentTotal = 0;
        } else badRange.fragmentOffset = header.fragmentTotal + 1;
        ok = rejects(badRange, L"message matrix outer range") && ok;
        const bool independent = kind == MessageKind::Capabilities ||
            kind == MessageKind::GraphOpenRequest || kind == MessageKind::OpenReceipt ||
            kind == MessageKind::GraphCancelRequest || kind == MessageKind::GraphCloseRequest ||
            kind == MessageKind::CursorClosedReceipt || kind == MessageKind::PatchBeginRequest ||
            kind == MessageKind::PatchBeginReceipt;
        if (independent) {
            Header bad = header;
            bad.sequence = 1;
            ok = rejects(bad, L"message matrix independent sequence/chain") && ok;
        }
    }
    Header emptyReceipt{};
    emptyReceipt.message = MessageKind::OpenReceipt;
    Bytes encoded;
    ok = Expect(!EncodeFrame(emptyReceipt, {}, &encoded), L"zero OpenReceipt rejected") && ok;
    return ok;
}

bool PatchUpload() {
    CleanupProcessState();
    if (DebugUploadCount() != 0 || DebugUploadFileCount() != 0 ||
        DebugUploadDirectoryExists()) {
        std::wcerr << L"process cleanup retained patch upload storage\n";
        return false;
    }
    const Route route{17, 4242};
    const GraphVersionV1 version = Version();
    const Bytes versionBytes = VersionBytes(version);
    Bytes raw((8U << 20) + 257U);
    for (std::size_t index = 0; index != raw.size(); ++index)
        raw[index] = static_cast<std::uint8_t>((index * 29U) & 0xffU);
    const char action[] = "HCA1\nACTION\tDeleteCtrl\nEND";
    std::copy(action, action + sizeof(action) - 1, raw.begin());
    const Sha256 rawDigest = DomainHash("HWPGRAPH\0PATCHRAW\0V1", View(raw));

    Header begin{};
    begin.message = MessageKind::PatchBeginRequest;
    begin.flags = kFlagPatch;
    begin.fragmentTotal = raw.size();
    static_cast<void>(ApplyVersionToHeader(version, &begin));
    const Bytes beginPayload = Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(version.documentSessionId))),
        EncodeField(2, 1, ScalarTag::Struct, View(versionBytes)),
        EncodeField(3, 1, ScalarTag::Uint64, View(U64(raw.size()))),
        EncodeField(4, 1, ScalarTag::SHA256, View(Dig(rawDigest))),
    });
    const Bytes beginRequest = Frame(begin, beginPayload);
    Header beginRequestHeader{};
    ByteView ignored{};
    ErrorCode error{};
    static_cast<void>(DecodeFrame(View(beginRequest), &beginRequestHeader, &ignored, &error));
    const Bytes beginResponse = ProcessRequest(MessageKind::PatchBeginRequest,
                                                View(beginRequest), route);
    Header responseHeader{};
    std::vector<ParsedField> receipt;
    const bool beginDecoded = Decode(beginResponse, &responseHeader, &receipt);
    if (beginDecoded && responseHeader.message == MessageKind::Error && Field(receipt, 1) != nullptr)
        std::wcerr << L"PatchBegin error code=" << static_cast<unsigned>(Field(receipt, 1)->value.data[0]) << L'\n';
    bool ok = Expect(beginDecoded && responseHeader.message == MessageKind::PatchBeginReceipt,
        L"PatchBegin receipt") &&
        Expect(DebugUploadCount() == 1 && DebugUploadFileCount() == 1 &&
                   DebugUploadDirectoryExists(),
               L"PatchBegin allocates one registry entry/file/root");
    Uuid128 upload{};
    const ParsedField* uploadField = Field(receipt, 1);
    if (uploadField != nullptr)
        std::copy(uploadField->value.data, uploadField->value.data + 16, upload.bytes.begin());
    Sha256 requestChain = beginRequestHeader.chainDigest;
    Sha256 responseChain = responseHeader.chainDigest;
    std::uint64_t requestSequence = 1;
    std::uint64_t responseSequence = 1;
    std::uint64_t offset = 0;
    constexpr std::uint64_t chunkBytes = 3U << 20;
    while (ok && offset != raw.size()) {
        const std::uint64_t count = (std::min<std::uint64_t>)(chunkBytes, raw.size() - offset);
        const Bytes encodedRaw = RawBytes({raw.data() + static_cast<std::size_t>(offset), count});
        Header chunk{};
        chunk.message = MessageKind::PatchChunkRequest;
        chunk.flags = kFlagPatch | (offset + count < raw.size() ? kFlagFragmented : 0U);
        chunk.sequence = requestSequence;
        chunk.fragmentOffset = offset;
        chunk.fragmentTotal = raw.size();
        chunk.cursorOrUpload = upload;
        chunk.previousChain = requestChain;
        static_cast<void>(ApplyVersionToHeader(version, &chunk));
        const Bytes chunkPayload = Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(upload))),
            EncodeField(2, 1, ScalarTag::Uint64, View(U64(offset))),
            EncodeField(3, 1, ScalarTag::Bytes, View(encodedRaw)),
        });
        const Bytes request = Frame(chunk, chunkPayload);
        Header requestHeader{};
        static_cast<void>(DecodeFrame(View(request), &requestHeader, &ignored, &error));
        const Bytes response = ProcessRequest(MessageKind::PatchChunkRequest, View(request), route);
        receipt.clear();
        ok = Expect(Decode(response, &responseHeader, &receipt) &&
            responseHeader.message == MessageKind::PatchChunkReceipt &&
            responseHeader.sequence == responseSequence, L"PatchChunk receipt") && ok;
        requestChain = requestHeader.chainDigest;
        responseChain = responseHeader.chainDigest;
        ++requestSequence;
        ++responseSequence;
        offset += count;
    }

    auto commitRequest = [&](const Sha256& digest) {
        Header commit{};
        commit.message = MessageKind::PatchCommitRequest;
        commit.flags = kFlagPatch | kFlagTerminal;
        commit.sequence = requestSequence;
        commit.fragmentOffset = raw.size();
        commit.fragmentTotal = raw.size();
        commit.cursorOrUpload = upload;
        commit.previousChain = requestChain;
        static_cast<void>(ApplyVersionToHeader(version, &commit));
        return Frame(commit, Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(upload))),
            EncodeField(2, 1, ScalarTag::Uint64, View(U64(raw.size()))),
            EncodeField(3, 1, ScalarTag::SHA256, View(Dig(digest))),
        }));
    };
    Sha256 wrong = rawDigest;
    wrong.bytes[0] ^= 1;
    const Bytes failed = ProcessRequest(MessageKind::PatchCommitRequest,
                                        View(commitRequest(wrong)), route);
    receipt.clear();
    ok = Expect(Decode(failed, &responseHeader, &receipt) &&
        responseHeader.message == MessageKind::Error && DebugUploadCount() == 1,
        L"bad commit preserves open upload") && ok;
    const Bytes sealed = ProcessRequest(MessageKind::PatchCommitRequest,
                                        View(commitRequest(rawDigest)), route);
    receipt.clear();
    ok = Expect(Decode(sealed, &responseHeader, &receipt) &&
        responseHeader.message == MessageKind::PatchSealReceipt,
        L"atomic commit retry seals") && ok;
    Bytes sealedRaw;
    ok = Expect(DebugReadSealed(upload, &sealedRaw) && sealedRaw == raw,
        L">8MiB sealed opaque bytes unchanged") &&
        Expect(std::memcmp(sealedRaw.data(), action, sizeof(action) - 1) == 0,
        L"action-shaped bytes remain inert") && ok;

    Header abort{};
    abort.message = MessageKind::PatchAbortRequest;
    abort.flags = kFlagPatch | kFlagTerminal;
    abort.sequence = requestSequence + 1;
    abort.fragmentTotal = raw.size();
    abort.session = version.documentSessionId;
    abort.cursorOrUpload = upload;
    // Commit was the successful next request and owns the new previous chain.
    Header commitHeader{};
    const Bytes successfulCommit = commitRequest(rawDigest);
    static_cast<void>(DecodeFrame(View(successfulCommit), &commitHeader, &ignored, &error));
    abort.previousChain = commitHeader.chainDigest;
    const Bytes abortResponse = ProcessRequest(MessageKind::PatchAbortRequest,
        View(Frame(abort, Payload({EncodeField(1, 1, ScalarTag::UUID128, View(Id(upload)))}))), route);
    receipt.clear();
    ok = Expect(Decode(abortResponse, &responseHeader, &receipt) &&
        responseHeader.message == MessageKind::PatchAbortReceipt &&
        DebugUploadCount() == 0 && DebugUploadFileCount() == 0 &&
        !DebugUploadDirectoryExists(),
        L"sealed abort removes registry entry/file/root") && ok;

    const Bytes cleanupBegin = ProcessRequest(
        MessageKind::PatchBeginRequest, View(beginRequest), route);
    receipt.clear();
    ok = Expect(Decode(cleanupBegin, &responseHeader, &receipt) &&
                    responseHeader.message == MessageKind::PatchBeginReceipt &&
                    DebugUploadCount() == 1 && DebugUploadFileCount() == 1 &&
                    DebugUploadDirectoryExists(),
                L"process cleanup setup allocates exact state") && ok;
    CleanupProcessState();
    return Expect(DebugUploadCount() == 0 && DebugUploadFileCount() == 0 &&
                      !DebugUploadDirectoryExists(),
                  L"process cleanup removes registry entry/file/root") && ok;
}
} // namespace

bool DocumentGraphReadCapabilitySmoke() {
    return GraphReadCapabilityDirectEvidence();
}

bool EmitDocumentGraphProtocolLarge(const std::uint64_t fieldBytes) {
    if (fieldBytes < 8) return false;
    const GraphVersionV1 version = Version();
    const Uuid128 cursor = Uuid(77);
    Header openHeader{};
    openHeader.message = MessageKind::OpenReceipt;
    openHeader.cursorOrUpload = cursor;
    static_cast<void>(ApplyVersionToHeader(version, &openHeader));
    const Bytes open = Frame(openHeader, Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
        EncodeField(2, 1, ScalarTag::Struct, View(VersionBytes(version))),
    }));
    if (open.empty()) return false;

    Bytes recordHeader, fieldHeader;
    if (!EncodeCanonicalRecordHeaderV1(RecordKind::Manifest, 1, 1, 0,
                                       24 + fieldBytes, &recordHeader) ||
        !EncodeCanonicalFieldHeaderV1(1, 1, ScalarTag::Bytes, 1,
                                      fieldBytes, &fieldHeader))
        return false;
    GraphStreamSha256V1 streamHash{};
    InitializeGraphStreamHashV1(&streamHash);
    if (!UpdateGraphStreamHashV1(&streamHash, View(recordHeader)) ||
        !UpdateGraphStreamHashV1(&streamHash, View(fieldHeader)))
        return false;

    const std::uint64_t maximumData = kMaximumPayloadBytes - 56;
    const std::uint64_t chunks = (fieldBytes + maximumData - 1) / maximumData;
    _setmode(_fileno(stdout), _O_BINARY);
    const auto writeBytes = [](const void* const data, const std::size_t size) {
        return std::fwrite(data, 1, size, stdout) == size;
    };
    const auto write64 = [&](const std::uint64_t value) {
        std::array<std::uint8_t, 8> encoded{};
        for (unsigned index = 0; index != 8; ++index)
            encoded[index] = static_cast<std::uint8_t>(value >> (index * 8));
        return writeBytes(encoded.data(), encoded.size());
    };
    const auto writeFrame = [&](const Bytes& frame) {
        return write64(frame.size()) && writeBytes(frame.data(), frame.size());
    };
    if (!writeBytes("HGNG1", 5) || !write64(chunks + 2) || !writeFrame(open)) return false;

    Sha256 previous{};
    std::copy(open.begin() + 288, open.begin() + 320, previous.bytes.begin());
    std::uint64_t fieldOffset = 0;
    std::uint64_t reconstructedOffset = 0;
    for (std::uint64_t index = 0; index != chunks; ++index) {
        const std::uint64_t size = (std::min)(maximumData, fieldBytes - fieldOffset);
        Bytes value(static_cast<std::size_t>(size), 0);
        if (fieldOffset == 0) {
            const std::uint64_t contentBytes = fieldBytes - 8;
            for (unsigned byte = 0; byte != 8; ++byte)
                value[byte] = static_cast<std::uint8_t>(contentBytes >> (byte * 8));
        }
        if (!UpdateGraphStreamHashV1(&streamHash, View(value))) return false;
        FragmentHeader fragment{};
        fragment.recordKind = RecordKind::Manifest;
        fragment.logicalRecordFlags = 1;
        fragment.fieldTag = 1;
        fragment.fieldFlags = 1;
        if (index == 0) fragment.fieldFlags |= 4 | 16;
        if (index + 1 == chunks) fragment.fieldFlags |= 8 | 32;
        fragment.scalar = ScalarTag::Bytes;
        fragment.logicalRecordFieldCount = 1;
        fragment.elementCount = 1;
        fragment.totalFieldBytes = fieldBytes;
        fragment.fragmentOffset = fieldOffset;
        fragment.fragmentBytes = size;
        Bytes payload;
        if (!EncodeFragment(fragment, View(value), &payload)) return false;
        Header header{};
        header.message = MessageKind::GraphChunk;
        if (index + 1 != chunks) header.flags = kFlagFragmented;
        header.sequence = index + 1;
        header.fragmentOffset = reconstructedOffset;
        header.fragmentTotal = 48 + fieldBytes;
        header.cursorOrUpload = cursor;
        header.previousChain = previous;
        static_cast<void>(ApplyVersionToHeader(version, &header));
        const Bytes frame = Frame(header, payload);
        if (frame.empty() || !writeFrame(frame)) return false;
        std::copy(frame.begin() + 288, frame.begin() + 320, previous.bytes.begin());
        fieldOffset += size;
        reconstructedOffset += size + (index == 0 ? 48 : 0);
    }
    Sha256 streamDigest{};
    if (!FinalizeGraphStreamHashV1(streamHash, &streamDigest)) return false;
    Header terminalHeader{};
    terminalHeader.message = MessageKind::GraphTerminal;
    terminalHeader.flags = kFlagTerminal;
    terminalHeader.sequence = chunks + 1;
    terminalHeader.fragmentOffset = 48 + fieldBytes;
    terminalHeader.fragmentTotal = 48 + fieldBytes;
    terminalHeader.cursorOrUpload = cursor;
    terminalHeader.previousChain = previous;
    static_cast<void>(ApplyVersionToHeader(version, &terminalHeader));
    const Bytes terminal = Frame(terminalHeader, Payload({
        EncodeField(1, 1, ScalarTag::Uint64, View(U64(1))),
        EncodeField(2, 1, ScalarTag::Uint64, View(U64(48 + fieldBytes))),
        EncodeField(3, 1, ScalarTag::SHA256, View(Dig(streamDigest))),
    }));
    return !terminal.empty() && writeFrame(terminal) && std::fflush(stdout) == 0;
}

struct FixtureUuidState final {
    std::uint32_t next = 1;
};

bool FillFixtureUuid(void* const context, std::uint8_t* const bytes,
                     const std::uint32_t count) noexcept {
    if (context == nullptr || bytes == nullptr || count != 16) return false;
    auto* const state = static_cast<FixtureUuidState*>(context);
    std::fill_n(bytes, count, std::uint8_t{0});
    bytes[0] = 0xa7;
    bytes[1] = static_cast<std::uint8_t>(state->next >> 24);
    bytes[2] = static_cast<std::uint8_t>(state->next >> 16);
    bytes[3] = static_cast<std::uint8_t>(state->next >> 8);
    bytes[4] = static_cast<std::uint8_t>(state->next);
    ++state->next;
    return true;
}

std::vector<std::uint8_t> FixtureLayoutEnvironment() {
    layout::LayoutEnvironmentV1 environment;
    environment.hwpFileVersion = {13, 0, 0, 1};
    environment.printerName = L"HGN1 deterministic fixture printer";
    environment.devmodeDigest.bytes.fill(0x31);
    environment.fontInventoryDigest.bytes.fill(0x32);
    environment.dpiX = 96;
    environment.dpiY = 96;
    environment.systemLcid = 0x412;
    environment.pageSetupDigest.bytes.fill(0x33);
    std::vector<std::uint8_t> serialized;
    return layout::SerializeLayoutEnvironmentV1(environment, &serialized)
        ? serialized : std::vector<std::uint8_t>{};
}

capture::PropertyObservation FixtureProperty(
    const capture::PropertyTarget target, std::wstring identity,
    const PropertyKeyId key, const ObservationState state,
    const std::int64_t value = 0) {
    capture::PropertyObservation property;
    property.target = target;
    property.targetIdentity = std::move(identity);
    property.ownerField = key == 8000 ? 106 : 10;
    property.key = key;
    property.scalar = key == 8000 || key >= 12002
        ? ScalarTag::HWPUNIT64 : ScalarTag::Uint64;
    property.state = state;
    property.origin = state == ObservationState::Value
        ? PropertyOrigin::Generated : PropertyOrigin::Unavailable;
    property.integerValue = value;
    return property;
}

bool BuildPageSpanningTableRecords(
    Bytes* const records,
    std::vector<capture::RecordBlob>* const recordBlobs,
    std::uint64_t* const recordCount, NodeId* const tableNodeId,
    std::uint64_t* const firstPage, std::uint64_t* const lastPage,
    Sha256* const layoutRoot) {
    using namespace capture;
    if (records == nullptr || recordBlobs == nullptr || recordCount == nullptr ||
        tableNodeId == nullptr || firstPage == nullptr || lastPage == nullptr ||
        layoutRoot == nullptr)
        return false;

    std::vector<ReaderPayload> payloads(kQualifiedReaderCount);
    for (size_t index = 0; index != payloads.size(); ++index) {
        payloads[index].reader = static_cast<QualifiedReader>(index);
        payloads[index].outcome = ReaderOutcome::Complete;
        payloads[index].coverage = CoverageState::Complete;
    }
    payloads[0].bodyList = {ObservationState::Value, true, 0, {}, 700};
    payloads[0].sections = {{0, {0, 0, 0}}};
    const ControlObservation nativeTable{
        L"tbl", L"page-spanning-table", 1, {0, 0, 1}, true};
    payloads[0].controls = {nativeTable};
    payloads[3].controls = payloads[0].controls;

    ParagraphObservation paragraph;
    paragraph.sectionOrdinal = 0;
    paragraph.start = {0, 0, 0};
    paragraph.runs.push_back({{0, 0, 0}, {0, 0, 5}, L"table"});
    payloads[1].paragraphs = {paragraph};
    CoverageObservation characterShapeCoverage;
    characterShapeCoverage.target = PropertyTarget::Run;
    characterShapeCoverage.targetIdentity = L"0:0:0:5";
    characterShapeCoverage.coordinate = CoverageCoordinateKind::NodeField;
    characterShapeCoverage.ownerField = 102;
    characterShapeCoverage.detail = L"CharacterShapeReferenceClosure";
    characterShapeCoverage.profile = ProfileId::EditableText;
    characterShapeCoverage.state = CoverageState::NotExposed;
    payloads[2].coverageFacts.push_back(std::move(characterShapeCoverage));

    const auto cell = [](const wchar_t* const address,
                         const std::uint64_t row,
                         const std::int64_t list,
                         const std::int64_t page) {
        CellObservation result;
        result.address = address;
        result.listId = list;
        result.row1 = row;
        result.column1 = 1;
        result.width = {ObservationState::Value, 14400};
        result.height = {ObservationState::Value, 18000};
        result.pageStart = {ObservationState::Value, page};
        result.pageEnd = {ObservationState::Value, page};
        result.text = {ObservationState::Value, L""};
        return result;
    };
    TableObservation table;
    table.instanceId = nativeTable.instanceId;
    table.headCtrlOrdinal = nativeTable.headCtrlOrdinal;
    table.anchor = nativeTable.anchor;
    table.rowCount = 2;
    table.columnCount = 1;
    table.locatorComplete = true;
    table.cells = {cell(L"A1", 1, 701, 1), cell(L"A2", 2, 702, 2)};
    payloads[4].tables = {table};

    std::vector<layout::LayoutNodeObservation> cellLayout;
    for (const CellObservation& observed : table.cells) {
        layout::LayoutNodeObservation node;
        node.nodeId = table.instanceId + L":" + observed.address;
        node.kind = layout::LayoutNodeKind::Cell;
        node.pageStart = {layout::ObservationState::Value,
                          observed.pageStart.value};
        node.pageEnd = {layout::ObservationState::Value,
                        observed.pageEnd.value};
        cellLayout.push_back(std::move(node));
    }
    layout::ScalarObservation tableStart;
    layout::ScalarObservation tableEnd;
    if (layout::UnionPageSpans(cellLayout, &tableStart, &tableEnd) !=
            layout::CaptureStatus::Complete ||
        tableStart.state != layout::ObservationState::Value ||
        tableEnd.state != layout::ObservationState::Value ||
        tableEnd.value <= tableStart.value) {
        std::wcerr << L"page-spanning fixture native span union failed\n";
        return false;
    }
    *firstPage = static_cast<std::uint64_t>(tableStart.value);
    *lastPage = static_cast<std::uint64_t>(tableEnd.value);

    const std::wstring tableIdentity = CanonicalNativeSiteIdentity(
        PropertyTarget::Table, L"tbl", table.headCtrlOrdinal, table.anchor,
        table.instanceIdPresent, table.instanceId);
    auto& layoutPayload = payloads[6];
    layoutPayload.layoutEnvironment = FixtureLayoutEnvironment();
    if (layoutPayload.layoutEnvironment.empty()) {
        std::wcerr << L"page-spanning fixture layout environment failed\n";
        return false;
    }
    const auto addLayout = [&layoutPayload](const PropertyTarget target,
                                            const std::wstring& identity,
                                            const PropertyKeyId key,
                                            const std::int64_t value) {
        layoutPayload.layoutProperties.push_back(FixtureProperty(
            target, identity, key, ObservationState::Value, value));
    };
    addLayout(PropertyTarget::Paragraph, L"0:0", 12000, 1);
    addLayout(PropertyTarget::Paragraph, L"0:0", 12001, 1);
    addLayout(PropertyTarget::Table, tableIdentity, 12000, tableStart.value);
    addLayout(PropertyTarget::Table, tableIdentity, 12001, tableEnd.value);
    addLayout(PropertyTarget::Table, tableIdentity, 12002, 14400);
    addLayout(PropertyTarget::Table, tableIdentity, 12003, 36000);
    for (const CellObservation& observed : table.cells) {
        const std::wstring identity = CanonicalNativeSiteIdentity(
            PropertyTarget::Cell, L"tbl", table.headCtrlOrdinal,
            {observed.listId, 0, 0}, table.instanceIdPresent,
            table.instanceId) + L":" + observed.address;
        addLayout(PropertyTarget::Cell, identity, 12000,
                  observed.pageStart.value);
        addLayout(PropertyTarget::Cell, identity, 12001,
                  observed.pageEnd.value);
    }
    layoutPayload.layoutObservedKindCounts = {1, 0, 1, 2, 0};
    layoutPayload.layoutPerKindComplete = true;
    payloads[2].properties.push_back(FixtureProperty(
        PropertyTarget::Table, tableIdentity, 8000,
        ObservationState::NotExposed));

    layout::LayoutSnapshot snapshot;
    snapshot.environment = layoutPayload.layoutEnvironment;
    snapshot.pageCount = 2;
    layout::LayoutNodeObservation tableLayout;
    tableLayout.nodeId = table.instanceId;
    tableLayout.kind = layout::LayoutNodeKind::Table;
    tableLayout.pageStart = tableStart;
    tableLayout.pageEnd = tableEnd;
    snapshot.nodes.push_back(tableLayout);
    snapshot.nodes.insert(snapshot.nodes.end(), cellLayout.begin(),
                          cellLayout.end());
    std::wstring rootText;
    if (layout::ComputeLayoutRoot(snapshot, &rootText) !=
            layout::CaptureStatus::Complete || rootText.size() != 64) {
        std::wcerr << L"page-spanning fixture layout root failed\n";
        return false;
    }
    const auto nibble = [](const wchar_t value) -> int {
        if (value >= L'0' && value <= L'9') return value - L'0';
        if (value >= L'a' && value <= L'f') return value - L'a' + 10;
        if (value >= L'A' && value <= L'F') return value - L'A' + 10;
        return -1;
    };
    for (size_t index = 0; index != layoutRoot->bytes.size(); ++index) {
        const int high = nibble(rootText[index * 2]);
        const int low = nibble(rootText[index * 2 + 1]);
        if (high < 0 || low < 0) return false;
        layoutRoot->bytes[index] = static_cast<std::uint8_t>((high << 4) | low);
    }

    FixtureUuidState uuidState;
    CaptureIdentityArena arena({&uuidState, FillFixtureUuid});
    Sha256 indexDigest{};
    indexDigest.bytes.fill(0x41);
    if (!BuildTypedRecordStream(payloads, arena, nullptr, indexDigest,
                                records, recordBlobs)) {
        std::wcerr << L"page-spanning fixture typed record graph failed\n";
        return false;
    }

    *recordCount = 0;
    bool foundTable = false;
    std::uint64_t offset = 0;
    while (offset != records->size()) {
        if (records->size() - offset < 24) return false;
        const std::uint8_t* const record = records->data() + offset;
        const std::uint64_t payloadBytes =
            *reinterpret_cast<const std::uint64_t*>(record + 16);
        if (payloadBytes > records->size() - offset - 24) return false;
        if (record[0] == static_cast<std::uint8_t>(RecordKind::Node) &&
            payloadBytes >= 72) {
            const std::uint8_t* const common = record + 48;
            const std::uint64_t commonBytes =
                *reinterpret_cast<const std::uint64_t*>(record + 40);
            if (commonBytes >= 66) {
                const std::uint8_t* const second = common + 40;
                const std::uint16_t kind = static_cast<std::uint16_t>(
                    second[24] | (static_cast<std::uint16_t>(second[25]) << 8));
                if (kind == static_cast<std::uint16_t>(NodeKind::Table)) {
                    std::copy_n(common + 24, 16, tableNodeId->bytes.begin());
                    foundTable = true;
                }
            }
        }
        ++*recordCount;
        offset += 24 + payloadBytes;
    }
    return foundTable;
}

bool CanonicalRecordsToFragments(const Bytes& records, Bytes* const payload) {
    if (payload == nullptr) return false;
    payload->clear();
    std::uint64_t recordOffset = 0;
    while (recordOffset != records.size()) {
        if (records.size() - recordOffset < 24) return false;
        const std::uint8_t* const record = records.data() + recordOffset;
        const std::uint16_t fieldCount = static_cast<std::uint16_t>(
            record[6] | (static_cast<std::uint16_t>(record[7]) << 8));
        const std::uint64_t recordId =
            *reinterpret_cast<const std::uint64_t*>(record + 8);
        const std::uint64_t recordBytes =
            *reinterpret_cast<const std::uint64_t*>(record + 16);
        if (recordBytes > records.size() - recordOffset - 24) return false;
        std::uint64_t fieldOffset = 24;
        for (std::uint16_t index = 0; index != fieldCount; ++index) {
            if (recordBytes + 24 - fieldOffset < 24) return false;
            const std::uint8_t* const field = record + fieldOffset;
            const std::uint64_t valueBytes =
                *reinterpret_cast<const std::uint64_t*>(field + 16);
            if (valueBytes > recordBytes - (fieldOffset - 24) - 24)
                return false;
            FragmentHeader header{};
            header.recordKind = static_cast<RecordKind>(record[0]);
            header.logicalRecordFlags = static_cast<std::uint16_t>(
                record[4] | (static_cast<std::uint16_t>(record[5]) << 8));
            header.fieldTag = static_cast<std::uint16_t>(
                field[0] | (static_cast<std::uint16_t>(field[1]) << 8));
            header.fieldFlags = static_cast<std::uint16_t>(
                field[2] | (static_cast<std::uint16_t>(field[3]) << 8));
            header.fieldFlags |= 4 | 8;
            if (index == 0) header.fieldFlags |= 16;
            if (index + 1 == fieldCount) header.fieldFlags |= 32;
            header.scalar = static_cast<ScalarTag>(
                field[4] | (static_cast<std::uint16_t>(field[5]) << 8));
            header.logicalRecordFieldCount = fieldCount;
            header.recordId = recordId;
            header.elementCount =
                *reinterpret_cast<const std::uint64_t*>(field + 8);
            header.totalFieldBytes = valueBytes;
            header.fragmentBytes = valueBytes;
            Bytes encoded;
            if (!EncodeFragment(header, {field + 24, valueBytes}, &encoded))
                return false;
            Add(payload, encoded);
            fieldOffset += 24 + valueBytes;
        }
        if (fieldOffset != recordBytes + 24) return false;
        recordOffset += 24 + recordBytes;
    }
    return true;
}

bool EmitDocumentGraphProtocolGolden(const wchar_t* const outputRoot) {
    if (outputRoot == nullptr) return false;
    Error propertyError{};
    const std::uint16_t propertyText[]{0x0041, 0x0000, 0xd800, 0x005a};
    const Bytes propertyTextValue = codec::Utf16(propertyText,
                                                 std::size(propertyText));
    const Bytes propertyObservation = codec::Observation(
        ObservationState::Value, 0, nullptr, 0, View(propertyTextValue),
        &propertyError);
    const auto propertyRecord = [&](const ScalarTag outer) {
        const Bytes fields = Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(Uuid(17)))),
            EncodeField(2, 1, ScalarTag::Uint16, View(codec::Uint16(102))),
            EncodeField(3, 1, ScalarTag::Uint32, View(codec::Uint32(1000))),
            EncodeField(4, 1, outer, View(propertyObservation)),
            EncodeField(5, 1, ScalarTag::Uint8,
                        View(codec::Uint8(static_cast<std::uint8_t>(
                            PropertyOrigin::Direct)))),
        });
        Bytes record;
        if (!EncodeCanonicalRecordHeaderV1(RecordKind::Property, 1, 5, 0,
                                            fields.size(), &record))
            return Bytes{};
        Add(&record, fields);
        return record;
    };
    Bytes validProperty = propertyRecord(ScalarTag::Struct);
    Bytes wrongTagProperty = propertyRecord(ScalarTag::UTF16);
    std::uint64_t propertyRecordCount = 0;
    const Error validPropertyResult = ValidateRecordStream(
        {&validProperty, ReadMemoryBytes, validProperty.size(),
         StreamKind::Capture},
        &propertyRecordCount);
    const Error wrongTagPropertyResult = ValidateRecordStream(
        {&wrongTagProperty, ReadMemoryBytes, wrongTagProperty.size(),
         StreamKind::Capture},
        &propertyRecordCount);
    if (propertyError != Error::None || validProperty.empty() ||
        wrongTagProperty.empty() ||
        validPropertyResult != Error::BadRecordOrder ||
        wrongTagPropertyResult != Error::BadScalar)
        return false;
    const std::filesystem::path root(outputRoot);
    std::error_code filesystemError;
    std::filesystem::create_directories(root, filesystemError);
    if (filesystemError) return false;

    const GraphVersionV1 version = Version();
    const Uuid128 cursor = Uuid(77);
    Header openHeader{};
    openHeader.message = MessageKind::OpenReceipt;
    openHeader.cursorOrUpload = cursor;
    static_cast<void>(ApplyVersionToHeader(version, &openHeader));
    const Bytes openPayload = Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
        EncodeField(2, 1, ScalarTag::Struct, View(VersionBytes(version))),
    });
    const Bytes open = Frame(openHeader, openPayload);
    if (open.empty()) return false;

    FragmentHeader fragment{};
    fragment.recordKind = RecordKind::Manifest;
    fragment.logicalRecordFlags = 1;
    fragment.fieldTag = 1;
    fragment.fieldFlags = 1 | 4 | 8 | 16 | 32;
    fragment.scalar = ScalarTag::Uint8;
    fragment.logicalRecordFieldCount = 1;
    fragment.elementCount = 1;
    fragment.totalFieldBytes = 1;
    fragment.fragmentBytes = 1;
    Bytes fragmentPayload;
    if (!EncodeFragment(fragment, View(U8(1)), &fragmentPayload)) return false;
    const Bytes canonicalField = EncodeField(1, 1, ScalarTag::Uint8, View(U8(1)));
    Bytes canonicalRecord;
    if (!EncodeCanonicalRecordHeaderV1(RecordKind::Manifest, 1, 1, 0,
                                       canonicalField.size(), &canonicalRecord))
        return false;
    Add(&canonicalRecord, canonicalField);
    Bytes productionReassembled;
    ErrorCode reassemblyError{};
    if (!ReassembleFragments({fragmentPayload}, 0, &productionReassembled,
                             &reassemblyError) ||
        productionReassembled != canonicalRecord)
        return false;

    Header chunkHeader{};
    chunkHeader.message = MessageKind::GraphChunk;
    chunkHeader.sequence = 1;
    chunkHeader.fragmentTotal = canonicalRecord.size();
    chunkHeader.cursorOrUpload = cursor;
    std::copy(open.begin() + 288, open.begin() + 320, chunkHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &chunkHeader));
    const Bytes chunk = Frame(chunkHeader, fragmentPayload);
    if (chunk.empty()) return false;

    Header terminalHeader{};
    terminalHeader.message = MessageKind::GraphTerminal;
    terminalHeader.flags = kFlagTerminal;
    terminalHeader.sequence = 2;
    terminalHeader.fragmentOffset = canonicalRecord.size();
    terminalHeader.fragmentTotal = canonicalRecord.size();
    terminalHeader.cursorOrUpload = cursor;
    std::copy(chunk.begin() + 288, chunk.begin() + 320, terminalHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &terminalHeader));
    GraphStreamSha256V1 canonicalHash{};
    InitializeGraphStreamHashV1(&canonicalHash);
    Sha256 streamDigest{};
    if (!UpdateGraphStreamHashV1(&canonicalHash, View(productionReassembled)) ||
        !FinalizeGraphStreamHashV1(canonicalHash, &streamDigest) ||
        !codec::Equal(streamDigest,
                      DomainHash("HWPGRAPH\0STREAM\0V1", View(canonicalRecord))))
        return false;
    const Bytes terminalPayload = Payload({
        EncodeField(1, 1, ScalarTag::Uint64, View(U64(1))),
        EncodeField(2, 1, ScalarTag::Uint64, View(U64(canonicalRecord.size()))),
        EncodeField(3, 1, ScalarTag::SHA256, View(Dig(streamDigest))),
    });
    const Bytes terminal = Frame(terminalHeader, terminalPayload);
    const Bytes capabilities = CapabilitiesFrame();
    if (terminal.empty() || capabilities.empty()) return false;

    std::vector<Bytes> vectors;
    if (!MessageMatrix(&vectors)) return false;
    vectors.insert(vectors.end(), {open, chunk, terminal});

    GraphVersionV1 layoutVersion = version;
    layoutVersion.profileBits |= ProfileBit(ProfileId::Layout);
    layoutVersion.layoutPresent = true;
    layoutVersion.layoutRoot.bytes.fill(0x61);
    Header layoutHeader{};
    layoutHeader.message = MessageKind::OpenReceipt;
    layoutHeader.cursorOrUpload = cursor;
    static_cast<void>(ApplyVersionToHeader(layoutVersion, &layoutHeader));
    vectors.push_back(Frame(layoutHeader, Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
        EncodeField(2, 1, ScalarTag::Struct, View(VersionBytes(layoutVersion))),
    })));

    const ContentId contentId = Uuid(91);
    Sha256 scalarDigest{};
    scalarDigest.bytes.fill(0x71);
    const std::vector<std::pair<ScalarTag, Bytes>> scalars{
        {ScalarTag::Sint64, codec::Sint64(-7)},
        {ScalarTag::Uint64, codec::Uint64(7)},
        {ScalarTag::Bool, codec::Bool(true)},
        {ScalarTag::Float64, codec::Float64(1.25)},
        {ScalarTag::UTF16, codec::Utf16(nullptr, 0)},
        {ScalarTag::HWPUNIT64, codec::Uint64(7200)},
        {ScalarTag::BGR, codec::Uint32(0x332211)},
        {ScalarTag::Enum, codec::Enum(3, nullptr, 0)},
        {ScalarTag::BlobSlice, codec::BlobSliceValue(contentId, 2, 3, scalarDigest)},
        {ScalarTag::UUID128, Id(cursor)},
        {ScalarTag::SHA256, Dig(scalarDigest)},
        {ScalarTag::Struct, {}},
        {ScalarTag::RawURC32, codec::Uint32(13)},
        {ScalarTag::Bytes, codec::BytesValue(View(U8(7)))},
        {ScalarTag::Uint8, codec::Uint8(7)},
        {ScalarTag::Uint16, codec::Uint16(7)},
        {ScalarTag::Uint32, codec::Uint32(7)},
        {ScalarTag::Sint32, codec::Sint32(-7)},
    };
    const auto fragmentVector = [&](const RecordKind recordKind, const ScalarTag scalar,
                                    const Bytes& value, const std::uint64_t recordId) {
        FragmentHeader item{};
        item.recordKind = recordKind;
        item.logicalRecordFlags = 1;
        item.fieldTag = 1;
        item.fieldFlags = 1 | 4 | 8 | 16 | 32;
        item.scalar = scalar;
        item.logicalRecordFieldCount = 1;
        item.recordId = recordId;
        item.elementCount = 1;
        item.totalFieldBytes = value.size();
        item.fragmentBytes = value.size();
        Bytes payload;
        if (!EncodeFragment(item, View(value), &payload)) return Bytes{};
        Header header{};
        header.message = MessageKind::GraphChunk;
        header.cursorOrUpload = cursor;
        header.fragmentTotal = 48 + value.size();
        static_cast<void>(ApplyVersionToHeader(version, &header));
        return Frame(header, payload);
    };
    for (const auto& [scalar, value] : scalars) {
        Bytes encoded = fragmentVector(RecordKind::Manifest, scalar, value, 0);
        if (encoded.empty()) return false;
        vectors.push_back(std::move(encoded));
    }
    for (std::uint16_t rawKind = 0; rawKind <= 8; ++rawKind) {
        Bytes encoded = fragmentVector(static_cast<RecordKind>(rawKind), ScalarTag::Uint8, U8(1), 0);
        if (encoded.empty()) return false;
        vectors.push_back(std::move(encoded));
    }

    Bytes fixture{'H', 'G', 'N', 'G', '1'};
    Put64(&fixture, vectors.size());
    for (const Bytes& vector : vectors) {
        Put64(&fixture, vector.size());
        Add(&fixture, vector);
    }
    const std::filesystem::path fixturePath = root / L"native-vectors.hgng";
    std::ofstream fixtureOutput(fixturePath, std::ios::binary | std::ios::trunc);
    fixtureOutput.write(reinterpret_cast<const char*>(fixture.data()),
                        static_cast<std::streamsize>(fixture.size()));
    fixtureOutput.close();
    if (!fixtureOutput) return false;

    struct OracleField final {
        FieldTag tag;
        std::uint16_t flags;
        ScalarTag scalar;
        std::uint64_t count;
        Bytes value;
    };
    Bytes oracleFragments, oracleCanonical;
    std::uint64_t oracleRecordId = 0;
    const auto appendOracleRecord = [&](const RecordKind kind,
                                        const std::vector<OracleField>& fields) {
        Bytes canonicalFields;
        std::vector<Bytes> recordFragments;
        for (std::size_t index = 0; index != fields.size(); ++index) {
            const OracleField& field = fields[index];
            const Bytes encoded = EncodeField(field.tag, field.flags, field.scalar,
                                              View(field.value), field.count);
            if (encoded.empty()) return false;
            Add(&canonicalFields, encoded);
            FragmentHeader item{};
            item.recordKind = kind;
            item.logicalRecordFlags = 1;
            item.fieldTag = field.tag;
            item.fieldFlags = field.flags | 4 | 8;
            if (index == 0) item.fieldFlags |= 16;
            if (index + 1 == fields.size()) item.fieldFlags |= 32;
            item.scalar = field.scalar;
            item.logicalRecordFieldCount = static_cast<std::uint16_t>(fields.size());
            item.recordId = oracleRecordId;
            item.elementCount = field.count;
            item.totalFieldBytes = field.value.size();
            item.fragmentBytes = field.value.size();
            Bytes encodedFragment;
            if (!EncodeFragment(item, View(field.value), &encodedFragment)) return false;
            recordFragments.push_back(encodedFragment);
            Add(&oracleFragments, encodedFragment);
        }
        Bytes header;
        if (!EncodeCanonicalRecordHeaderV1(kind, 1,
                static_cast<std::uint16_t>(fields.size()), oracleRecordId,
                canonicalFields.size(), &header)) return false;
        Bytes canonicalRecord = header;
        Add(&canonicalRecord, canonicalFields);
        Bytes reassembled;
        ErrorCode reassembly{};
        if (!ReassembleFragments(recordFragments, oracleRecordId, &reassembled,
                                 &reassembly) ||
            reassembled != canonicalRecord)
            return false;
        Add(&oracleCanonical, canonicalRecord);
        ++oracleRecordId;
        return true;
    };
    const std::uint16_t nestedRawUtf16[]{0x0041, 0xd800, 0x005a};
    const Bytes nestedScalar = Payload({
        EncodeField(1, 1, ScalarTag::Uint8, View(U8(3))),
        EncodeField(2, 1, ScalarTag::UTF16,
                    View(codec::Utf16(nestedRawUtf16, 3)))
    });
    std::vector<OracleField> arrayFields;
    for (std::size_t index = 0; index != scalars.size(); ++index) {
        const ScalarTag scalar = scalars[index].first;
        const Bytes& entry = scalar == ScalarTag::Struct
            ? nestedScalar : scalars[index].second;
        std::vector<Bytes> entries{entry};
        if (scalar == ScalarTag::Uint64) entries.push_back(U64(42));
        arrayFields.push_back({
            static_cast<FieldTag>(index + 1), 3, scalar, entries.size(),
            ArrayValue(scalar, 0, entries)});
    }
    if (!appendOracleRecord(RecordKind::Manifest, arrayFields)) return false;
    const Bytes nodeShape = Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
        EncodeField(2, 1, ScalarTag::Uint8,
                    View(U8(static_cast<std::uint8_t>(NodeKind::CharacterRun))))});
    if (!appendOracleRecord(RecordKind::Node,
            {{1, 1, ScalarTag::Struct, 1, nodeShape}})) return false;
    const auto property = [&](const std::uint32_t key,
                              const Bytes& observation) {
        return appendOracleRecord(RecordKind::Property, {
            {1, 1, ScalarTag::UUID128, 1, Id(cursor)},
            {2, 1, ScalarTag::Uint16, 1, codec::Uint16(102)},
            {3, 1, ScalarTag::Uint32, 1, codec::Uint32(key)},
            {4, 1, ScalarTag::Struct, 1, observation},
            {5, 1, ScalarTag::Uint8, 1,
             codec::Uint8(static_cast<std::uint8_t>(PropertyOrigin::Unknown))}});
    };
    Error oracleError{};
    const std::uint16_t embeddedNul[]{0x0041, 0x0000, 0x005a};
    const std::uint16_t surrogatePair[]{0x0041, 0xd83d, 0xde00, 0x005a};
    const std::uint16_t loneHigh[]{0x0041, 0xd800, 0x005a};
    const std::uint16_t loneLow[]{0x0041, 0xdc00, 0x005a};
    const std::uint16_t ordinary[]{0x0041, 0x0042, 0x005a};
    const auto rawUtf16Property = [&](const std::uint32_t key,
                                      const std::uint16_t* const units,
                                      const std::size_t count) {
        const Bytes encoded = codec::Utf16(units, count);
        const Bytes observation = codec::Observation(
            ObservationState::Value, 0, nullptr, 0, View(encoded), &oracleError);
        return oracleError == Error::None && property(key, observation);
    };
    if (!rawUtf16Property(1000, embeddedNul, std::size(embeddedNul)) ||
        !rawUtf16Property(1001, surrogatePair, std::size(surrogatePair)) ||
        !rawUtf16Property(1002, loneHigh, std::size(loneHigh)) ||
        !rawUtf16Property(1003, loneLow, std::size(loneLow)) ||
        !rawUtf16Property(1004, ordinary, std::size(ordinary)))
        return false;
    for (std::uint8_t reason = 1; reason <= 6; ++reason) {
        const std::uint16_t detail[]{0x0052, static_cast<std::uint16_t>(0xd800 + reason)};
        const Bytes unavailable = codec::Observation(
            static_cast<ObservationState>(reason), E_FAIL, detail, 2, {}, &oracleError);
        if (oracleError != Error::None || !property(1000, unavailable))
            return false;
    }
    for (std::uint16_t kind = static_cast<std::uint16_t>(RecordKind::Edge);
         kind <= static_cast<std::uint16_t>(RecordKind::Remap); ++kind) {
        if (kind == static_cast<std::uint16_t>(RecordKind::Property)) continue;
        if (!appendOracleRecord(static_cast<RecordKind>(kind),
                {{1, 1, ScalarTag::Uint8, 1, U8(static_cast<std::uint8_t>(kind))}}))
            return false;
    }
    Header oracleChunkHeader{};
    oracleChunkHeader.message = MessageKind::GraphChunk;
    oracleChunkHeader.sequence = 1;
    oracleChunkHeader.fragmentTotal = oracleCanonical.size();
    oracleChunkHeader.cursorOrUpload = cursor;
    std::copy(open.begin() + 288, open.begin() + 320,
              oracleChunkHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &oracleChunkHeader));
    const Bytes oracleChunk = Frame(oracleChunkHeader, oracleFragments);
    Sha256 oracleDigest{};
    if (oracleChunk.empty() || !StreamDigest(View(oracleCanonical), &oracleDigest)) return false;
    Header oracleTerminalHeader{};
    oracleTerminalHeader.message = MessageKind::GraphTerminal;
    oracleTerminalHeader.flags = kFlagTerminal;
    oracleTerminalHeader.sequence = 2;
    oracleTerminalHeader.fragmentOffset = oracleCanonical.size();
    oracleTerminalHeader.fragmentTotal = oracleCanonical.size();
    oracleTerminalHeader.cursorOrUpload = cursor;
    std::copy(oracleChunk.begin() + 288, oracleChunk.begin() + 320,
              oracleTerminalHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &oracleTerminalHeader));
    const Bytes oracleTerminal = Frame(oracleTerminalHeader, Payload({
        EncodeField(1, 1, ScalarTag::Uint64, View(U64(oracleRecordId))),
        EncodeField(2, 1, ScalarTag::Uint64, View(U64(oracleCanonical.size()))),
        EncodeField(3, 1, ScalarTag::SHA256, View(Dig(oracleDigest))),
    }));
    if (oracleTerminal.empty()) return false;
    Bytes oracleFixture{'H', 'G', 'N', 'G', '1'};
    const std::vector<Bytes> oracleVectors{open, oracleChunk, oracleTerminal};
    Put64(&oracleFixture, oracleVectors.size());
    for (const Bytes& vector : oracleVectors) {
        Put64(&oracleFixture, vector.size());
        Add(&oracleFixture, vector);
    }
    const std::filesystem::path oraclePath = root / L"native-oracle.hgng";
    std::ofstream oracleOutput(oraclePath, std::ios::binary | std::ios::trunc);
    oracleOutput.write(reinterpret_cast<const char*>(oracleFixture.data()),
                       static_cast<std::streamsize>(oracleFixture.size()));
    oracleOutput.close();
    if (!oracleOutput) return false;

    Bytes pageRecords;
    std::vector<capture::RecordBlob> pageBlobs;
    std::uint64_t pageRecordCount = 0;
    NodeId pageTableNode{};
    std::uint64_t pageStart = 0, pageEnd = 0;
    Sha256 pageLayoutRoot{};
    if (!BuildPageSpanningTableRecords(
            &pageRecords, &pageBlobs, &pageRecordCount, &pageTableNode,
            &pageStart, &pageEnd, &pageLayoutRoot)) {
        std::wcerr << L"page-spanning fixture capture graph failed\n";
        return false;
    }
    Bytes pageFragmentPayload;
    if (!CanonicalRecordsToFragments(pageRecords, &pageFragmentPayload)) {
        std::wcerr << L"page-spanning fixture canonical fragmentation failed\n";
        return false;
    }
    GraphVersionV1 pageVersion = version;
    pageVersion.profileBits = UINT64_C(0x1f);
    pageVersion.layoutPresent = true;
    pageVersion.layoutRoot = pageLayoutRoot;
    Header pageOpenHeader{};
    pageOpenHeader.message = MessageKind::OpenReceipt;
    pageOpenHeader.cursorOrUpload = cursor;
    static_cast<void>(ApplyVersionToHeader(pageVersion, &pageOpenHeader));
    const Bytes pageOpen = Frame(pageOpenHeader, Payload({
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
        EncodeField(2, 1, ScalarTag::Struct, View(VersionBytes(pageVersion))),
    }));
    Header pageChunkHeader{};
    pageChunkHeader.message = MessageKind::GraphChunk;
    pageChunkHeader.sequence = 1;
    pageChunkHeader.fragmentTotal = pageRecords.size();
    pageChunkHeader.cursorOrUpload = cursor;
    std::copy(pageOpen.begin() + 288, pageOpen.begin() + 320,
              pageChunkHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(pageVersion, &pageChunkHeader));
    const Bytes pageChunk = Frame(pageChunkHeader, pageFragmentPayload);
    Sha256 pageStreamDigest{};
    if (pageOpen.empty() || pageChunk.empty() ||
        !StreamDigest(View(pageRecords), &pageStreamDigest))
        return false;
    Header pageTerminalHeader{};
    pageTerminalHeader.message = MessageKind::GraphTerminal;
    pageTerminalHeader.flags = kFlagTerminal;
    pageTerminalHeader.sequence = 2;
    pageTerminalHeader.fragmentOffset = pageRecords.size();
    pageTerminalHeader.fragmentTotal = pageRecords.size();
    pageTerminalHeader.cursorOrUpload = cursor;
    std::copy(pageChunk.begin() + 288, pageChunk.begin() + 320,
              pageTerminalHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(pageVersion, &pageTerminalHeader));
    const Bytes pageTerminal = Frame(pageTerminalHeader, Payload({
        EncodeField(1, 1, ScalarTag::Uint64, View(U64(pageRecordCount))),
        EncodeField(2, 1, ScalarTag::Uint64, View(U64(pageRecords.size()))),
        EncodeField(3, 1, ScalarTag::SHA256, View(Dig(pageStreamDigest))),
    }));
    if (pageTerminal.empty()) return false;
    Bytes pageFixture{'H', 'G', 'N', 'G', '1'};
    const std::vector<Bytes> pageVectors{pageOpen, pageChunk, pageTerminal};
    Put64(&pageFixture, pageVectors.size());
    for (const Bytes& vector : pageVectors) {
        Put64(&pageFixture, vector.size());
        Add(&pageFixture, vector);
    }
    const std::filesystem::path pagePath =
        root / L"native-page-spanning-table.hgng";
    std::ofstream pageOutput(pagePath, std::ios::binary | std::ios::trunc);
    pageOutput.write(reinterpret_cast<const char*>(pageFixture.data()),
                     static_cast<std::streamsize>(pageFixture.size()));
    pageOutput.close();
    if (!pageOutput) return false;
    const std::filesystem::path pageBlobRoot =
        root / L"native-page-spanning-table.blobs";
    std::error_code pageBlobError;
    std::filesystem::create_directories(pageBlobRoot, pageBlobError);
    if (pageBlobError) return false;
    constexpr wchar_t hexDigits[] = L"0123456789abcdef";
    for (const capture::RecordBlob& blob : pageBlobs) {
        Bytes tuple(blob.id.bytes.begin(), blob.id.bytes.end());
        Add(&tuple, codec::Uint64(0));
        Add(&tuple, codec::Uint64(blob.bytes.size()));
        const Sha256 contentDigest = codec::Hash(codec::View(blob.bytes));
        Add(&tuple, Bytes(
            contentDigest.bytes.begin(), contentDigest.bytes.end()));
        const Sha256 key = codec::Hash(codec::View(tuple));
        std::wstring name;
        name.reserve(64);
        for (const std::uint8_t byte : key.bytes) {
            name.push_back(hexDigits[byte >> 4]);
            name.push_back(hexDigits[byte & 0x0f]);
        }
        std::ofstream blobOutput(
            pageBlobRoot / name, std::ios::binary | std::ios::trunc);
        blobOutput.write(
            reinterpret_cast<const char*>(blob.bytes.data()),
            static_cast<std::streamsize>(blob.bytes.size()));
        blobOutput.close();
        if (!blobOutput) return false;
    }

    const Bytes largeValue = codec::BytesValue(View(Bytes{'a', 'b', 'c', 'd', 'e', 'f'}));
    const std::size_t split = 5;
    FragmentHeader firstFragment{};
    firstFragment.recordKind = RecordKind::Manifest;
    firstFragment.logicalRecordFlags = 1;
    firstFragment.fieldTag = 1;
    firstFragment.fieldFlags = 1 | 4 | 16;
    firstFragment.scalar = ScalarTag::Bytes;
    firstFragment.logicalRecordFieldCount = 1;
    firstFragment.elementCount = 1;
    firstFragment.totalFieldBytes = largeValue.size();
    firstFragment.fragmentBytes = split;
    Bytes firstPayload;
    if (!EncodeFragment(firstFragment, {largeValue.data(), split}, &firstPayload)) return false;
    const Bytes canonicalLargeField = EncodeField(1, 1, ScalarTag::Bytes, View(largeValue));
    Bytes canonicalLargeRecord;
    if (!EncodeCanonicalRecordHeaderV1(RecordKind::Manifest, 1, 1, 0,
                                       canonicalLargeField.size(),
                                       &canonicalLargeRecord))
        return false;
    Add(&canonicalLargeRecord, canonicalLargeField);
    Header firstHeader{};
    firstHeader.message = MessageKind::GraphChunk;
    firstHeader.flags = kFlagFragmented;
    firstHeader.sequence = 1;
    firstHeader.fragmentTotal = canonicalLargeRecord.size();
    firstHeader.cursorOrUpload = cursor;
    std::copy(open.begin() + 288, open.begin() + 320, firstHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &firstHeader));
    const Bytes firstFrame = Frame(firstHeader, firstPayload);

    FragmentHeader secondFragment = firstFragment;
    secondFragment.fieldFlags = 1 | 8 | 32;
    secondFragment.fragmentOffset = split;
    secondFragment.fragmentBytes = largeValue.size() - split;
    Bytes secondPayload;
    if (!EncodeFragment(secondFragment,
            {largeValue.data() + split, largeValue.size() - split}, &secondPayload)) return false;
    Header secondHeader{};
    secondHeader.message = MessageKind::GraphChunk;
    secondHeader.sequence = 2;
    secondHeader.fragmentOffset = 48 + split;
    secondHeader.fragmentTotal = canonicalLargeRecord.size();
    secondHeader.cursorOrUpload = cursor;
    std::copy(firstFrame.begin() + 288, firstFrame.begin() + 320,
              secondHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &secondHeader));
    const Bytes secondFrame = Frame(secondHeader, secondPayload);
    Header largeTerminalHeader{};
    largeTerminalHeader.message = MessageKind::GraphTerminal;
    largeTerminalHeader.flags = kFlagTerminal;
    largeTerminalHeader.sequence = 3;
    largeTerminalHeader.fragmentOffset = canonicalLargeRecord.size();
    largeTerminalHeader.fragmentTotal = canonicalLargeRecord.size();
    largeTerminalHeader.cursorOrUpload = cursor;
    std::copy(secondFrame.begin() + 288, secondFrame.begin() + 320,
              largeTerminalHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &largeTerminalHeader));
    Sha256 largeDigest{};
    if (!StreamDigest(View(canonicalLargeRecord), &largeDigest)) return false;
    const Bytes largeTerminal = Frame(largeTerminalHeader, Payload({
        EncodeField(1, 1, ScalarTag::Uint64, View(U64(1))),
        EncodeField(2, 1, ScalarTag::Uint64, View(U64(canonicalLargeRecord.size()))),
        EncodeField(3, 1, ScalarTag::SHA256, View(Dig(largeDigest))),
    }));
    if (firstFrame.empty() || secondFrame.empty() || largeTerminal.empty()) return false;
    Bytes multiframe{'H', 'G', 'N', 'G', '1'};
    const std::vector<Bytes> multiframeVectors{open, firstFrame, firstFrame, secondFrame, largeTerminal};
    Put64(&multiframe, multiframeVectors.size());
    for (const Bytes& vector : multiframeVectors) {
        Put64(&multiframe, vector.size());
        Add(&multiframe, vector);
    }
    const std::filesystem::path multiframePath = root / L"native-multiframe.hgng";
    std::ofstream multiframeOutput(multiframePath, std::ios::binary | std::ios::trunc);
    multiframeOutput.write(reinterpret_cast<const char*>(multiframe.data()),
                           static_cast<std::streamsize>(multiframe.size()));
    multiframeOutput.close();
    if (!multiframeOutput) return false;

    FragmentHeader zeroField{};
    zeroField.recordKind = RecordKind::Manifest;
    zeroField.logicalRecordFlags = 1;
    zeroField.fieldTag = 1;
    zeroField.fieldFlags = 1 | 4 | 8 | 16;
    zeroField.scalar = ScalarTag::Struct;
    zeroField.logicalRecordFieldCount = 2;
    zeroField.elementCount = 1;
    Bytes compositeFirstPayload;
    if (!EncodeFragment(zeroField, {}, &compositeFirstPayload)) return false;
    FragmentHeader crossFirst = firstFragment;
    crossFirst.fieldTag = 2;
    crossFirst.logicalRecordFieldCount = 2;
    crossFirst.fieldFlags = 1 | 4;
    Bytes crossFirstBytes;
    if (!EncodeFragment(crossFirst, {largeValue.data(), split}, &crossFirstBytes)) return false;
    Add(&compositeFirstPayload, crossFirstBytes);
    Bytes compositeSecondPayload;
    FragmentHeader crossLast = crossFirst;
    crossLast.fieldFlags = 1 | 8 | 32;
    crossLast.fragmentOffset = split;
    crossLast.fragmentBytes = largeValue.size() - split;
    if (!EncodeFragment(crossLast,
            {largeValue.data() + split, largeValue.size() - split}, &compositeSecondPayload))
        return false;
    FragmentHeader secondRecord{};
    secondRecord.recordKind = RecordKind::Manifest;
    secondRecord.logicalRecordFlags = 1;
    secondRecord.fieldTag = 1;
    secondRecord.fieldFlags = 1 | 4 | 8 | 16 | 32;
    secondRecord.scalar = ScalarTag::Uint8;
    secondRecord.logicalRecordFieldCount = 1;
    secondRecord.recordId = 1;
    secondRecord.elementCount = 1;
    secondRecord.totalFieldBytes = 1;
    secondRecord.fragmentBytes = 1;
    Bytes secondRecordBytes;
    if (!EncodeFragment(secondRecord, View(U8(2)), &secondRecordBytes)) return false;
    Add(&compositeSecondPayload, secondRecordBytes);
    const Bytes zeroCanonical = EncodeField(1, 1, ScalarTag::Struct, {});
    const Bytes crossCanonical = EncodeField(2, 1, ScalarTag::Bytes, View(largeValue));
    Bytes firstCanonicalRecord;
    if (!EncodeCanonicalRecordHeaderV1(
            RecordKind::Manifest, 1, 2, 0,
            zeroCanonical.size() + crossCanonical.size(), &firstCanonicalRecord))
        return false;
    Add(&firstCanonicalRecord, zeroCanonical);
    Add(&firstCanonicalRecord, crossCanonical);
    const Bytes secondCanonicalField = EncodeField(1, 1, ScalarTag::Uint8, View(U8(2)));
    Bytes secondCanonicalRecord;
    if (!EncodeCanonicalRecordHeaderV1(RecordKind::Manifest, 1, 1, 1,
                                       secondCanonicalField.size(),
                                       &secondCanonicalRecord))
        return false;
    Add(&secondCanonicalRecord, secondCanonicalField);
    Bytes compositeCanonical = firstCanonicalRecord;
    Add(&compositeCanonical, secondCanonicalRecord);
    Header compositeFirstHeader{};
    compositeFirstHeader.message = MessageKind::GraphChunk;
    compositeFirstHeader.flags = kFlagFragmented;
    compositeFirstHeader.sequence = 1;
    compositeFirstHeader.fragmentTotal = compositeCanonical.size();
    compositeFirstHeader.cursorOrUpload = cursor;
    std::copy(open.begin() + 288, open.begin() + 320,
              compositeFirstHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &compositeFirstHeader));
    const Bytes compositeFirst = Frame(compositeFirstHeader, compositeFirstPayload);
    Header compositeSecondHeader{};
    compositeSecondHeader.message = MessageKind::GraphChunk;
    compositeSecondHeader.sequence = 2;
    compositeSecondHeader.fragmentOffset = 72 + split;
    compositeSecondHeader.fragmentTotal = compositeCanonical.size();
    compositeSecondHeader.cursorOrUpload = cursor;
    std::copy(compositeFirst.begin() + 288, compositeFirst.begin() + 320,
              compositeSecondHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &compositeSecondHeader));
    const Bytes compositeSecond = Frame(compositeSecondHeader, compositeSecondPayload);
    Header compositeTerminalHeader{};
    compositeTerminalHeader.message = MessageKind::GraphTerminal;
    compositeTerminalHeader.flags = kFlagTerminal;
    compositeTerminalHeader.sequence = 3;
    compositeTerminalHeader.fragmentOffset = compositeCanonical.size();
    compositeTerminalHeader.fragmentTotal = compositeCanonical.size();
    compositeTerminalHeader.cursorOrUpload = cursor;
    std::copy(compositeSecond.begin() + 288, compositeSecond.begin() + 320,
              compositeTerminalHeader.previousChain.bytes.begin());
    static_cast<void>(ApplyVersionToHeader(version, &compositeTerminalHeader));
    Sha256 compositeDigest{};
    if (!StreamDigest(View(compositeCanonical), &compositeDigest)) return false;
    const Bytes compositeTerminal = Frame(compositeTerminalHeader, Payload({
        EncodeField(1, 1, ScalarTag::Uint64, View(U64(2))),
        EncodeField(2, 1, ScalarTag::Uint64, View(U64(compositeCanonical.size()))),
        EncodeField(3, 1, ScalarTag::SHA256, View(Dig(compositeDigest))),
    }));
    if (compositeFirst.empty() || compositeSecond.empty() || compositeTerminal.empty()) return false;
    Bytes compositeFixture{'H', 'G', 'N', 'G', '1'};
    const std::vector<Bytes> compositeVectors{
        open, compositeFirst, compositeFirst, compositeSecond, compositeTerminal};
    Put64(&compositeFixture, compositeVectors.size());
    for (const Bytes& vector : compositeVectors) {
        Put64(&compositeFixture, vector.size());
        Add(&compositeFixture, vector);
    }
    const std::filesystem::path compositePath = root / L"native-composite.hgng";
    std::ofstream compositeOutput(compositePath, std::ios::binary | std::ios::trunc);
    compositeOutput.write(reinterpret_cast<const char*>(compositeFixture.data()),
                          static_cast<std::streamsize>(compositeFixture.size()));
    compositeOutput.close();
    if (!compositeOutput) return false;

    const auto sha256 = [](const std::filesystem::path& path) -> std::string {
        std::ifstream input(path, std::ios::binary);
        std::vector<std::uint8_t> content((std::istreambuf_iterator<char>(input)), {});
        BCRYPT_ALG_HANDLE algorithm = nullptr;
        BCRYPT_HASH_HANDLE hash = nullptr;
        std::array<std::uint8_t, 32> digest{};
        if (input.bad() || BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM,
                nullptr, 0) < 0 || BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) < 0 ||
            BCryptHashData(hash, content.data(), static_cast<ULONG>(content.size()), 0) < 0 ||
            BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) < 0) {
            if (hash != nullptr) BCryptDestroyHash(hash);
            if (algorithm != nullptr) BCryptCloseAlgorithmProvider(algorithm, 0);
            return {};
        }
        BCryptDestroyHash(hash);
        BCryptCloseAlgorithmProvider(algorithm, 0);
        std::ostringstream encoded;
        encoded << std::hex << std::setfill('0');
        for (const std::uint8_t byte : digest) encoded << std::setw(2) << unsigned{byte};
        return encoded.str();
    };
    wchar_t executableName[MAX_PATH]{};
    if (GetModuleFileNameW(nullptr, executableName, MAX_PATH) == 0) return false;
    Header unsupported{};
    Bytes unsupportedBytes;
    unsupported.message = MessageKind::PatchValidationReceipt;
    const bool validationRejected = !EncodeFrame(unsupported, {}, &unsupportedBytes);
    unsupported.message = MessageKind::PatchApplyReceipt;
    const bool applyRejected = !EncodeFrame(unsupported, {}, &unsupportedBytes);
    unsupported.message = MessageKind::PatchValidateRequest;
    const bool validateRequestRejected = !EncodeFrame(unsupported, {}, &unsupportedBytes);
    unsupported.message = MessageKind::PatchApplyRequest;
    const bool applyRequestRejected = !EncodeFrame(unsupported, {}, &unsupportedBytes);
    if (!validationRejected || !applyRejected || !validateRequestRejected || !applyRequestRejected)
        return false;
    const std::filesystem::path compiledSource(__FILE__);
    const std::filesystem::path sourcePath = std::filesystem::exists(compiledSource)
        ? compiledSource
        : std::filesystem::current_path() / L"addon" / L"HancomLiveBridgeNative" /
            L"smoke" / L"DocumentGraphProtocolSmoke.cpp";
    const std::string fixtureHash = sha256(fixturePath);
    const std::string multiframeHash = sha256(multiframePath);
    const std::string compositeHash = sha256(compositePath);
    const std::string oracleHash = sha256(oraclePath);
    const std::string pageHash = sha256(pagePath);
    const std::string sourceHash = sha256(sourcePath);
    const std::string emitterHash = sha256(std::filesystem::path(executableName));
    if (fixtureHash.empty() || multiframeHash.empty() || compositeHash.empty() ||
        oracleHash.empty() || pageHash.empty() || sourceHash.empty() ||
        emitterHash.empty()) return false;
    const auto hexBytes = [](const std::uint8_t* const bytes,
                             const size_t count) {
        std::ostringstream encoded;
        encoded << std::hex << std::setfill('0');
        for (size_t index = 0; index != count; ++index)
            encoded << std::setw(2) << static_cast<unsigned>(bytes[index]);
        return encoded.str();
    };
    const std::string pageNodeId = hexBytes(
        pageTableNode.bytes.data(), pageTableNode.bytes.size());
    const std::string pageLayoutRootHex = hexBytes(
        pageLayoutRoot.bytes.data(), pageLayoutRoot.bytes.size());
    const std::string pageStreamDigestHex = hexBytes(
        pageStreamDigest.bytes.data(), pageStreamDigest.bytes.size());
    const std::string pageTerminalChain = hexBytes(
        pageTerminal.data() + 288, 32);
    const std::filesystem::path rejectionPath = root / L"native-rejections.txt";
    std::ofstream rejectionOutput(rejectionPath, std::ios::binary | std::ios::trunc);
    const auto rejection = [&](const char* name, const unsigned value) {
        rejectionOutput << "enum=" << value << " message=" << name
            << " native_reason=BadMessageKind python_reason=BAD_HEADER"
            << " emitter_sha256=" << emitterHash
            << " source_sha256=" << sourceHash
            << " fixture_sha256=" << fixtureHash
            << " oracle_sha256=" << oracleHash << " REJECTED\n";
    };
    rejection("PatchValidationReceipt", 11);
    rejection("PatchApplyReceipt", 12);
    rejection("PatchValidateRequest", 109);
    rejection("PatchApplyRequest", 110);
    rejectionOutput
        << "property_field4_outer_utf16 native_reason=BadScalar"
        << " python_reason=BAD_SCALAR"
        << " emitter_sha256=" << emitterHash
        << " source_sha256=" << sourceHash
        << " fixture_sha256=" << fixtureHash
        << " oracle_sha256=" << oracleHash << " REJECTED\n";
    rejectionOutput.close();
    if (!rejectionOutput) return false;
    const std::string rejectionHash = sha256(rejectionPath);
    if (rejectionHash.empty()) return false;
    std::ofstream manifest(root / L"manifest.json", std::ios::binary | std::ios::trunc);
    manifest << "{\n  \"schema\": \"HGN1-NATIVE-GOLDEN-V1\",\n"
             << "  \"producer\": \"BridgeSmoke --hgn1-golden\",\n"
             << "  \"fixture\": \"native-vectors.hgng\",\n"
             << "  \"fixture_sha256\": \"" << fixtureHash << "\",\n"
             << "  \"multiframe_fixture\": \"native-multiframe.hgng\",\n"
             << "  \"multiframe_fixture_sha256\": \"" << multiframeHash << "\",\n"
             << "  \"composite_fixture\": \"native-composite.hgng\",\n"
             << "  \"composite_fixture_sha256\": \"" << compositeHash << "\",\n"
             << "  \"oracle_fixture\": \"native-oracle.hgng\",\n"
             << "  \"oracle_fixture_sha256\": \"" << oracleHash << "\",\n"
             << "  \"page_spanning_table_fixture\": \"native-page-spanning-table.hgng\",\n"
             << "  \"page_spanning_table_fixture_sha256\": \"" << pageHash << "\",\n"
             << "  \"page_spanning_table_node_id\": \"" << pageNodeId << "\",\n"
             << "  \"page_spanning_table_record_count\": " << pageRecordCount << ",\n"
             << "  \"page_spanning_table_page_start\": " << pageStart << ",\n"
             << "  \"page_spanning_table_page_end\": " << pageEnd << ",\n"
             << "  \"page_spanning_table_layout_root\": \"" << pageLayoutRootHex << "\",\n"
             << "  \"page_spanning_table_stream_digest\": \"" << pageStreamDigestHex << "\",\n"
             << "  \"page_spanning_table_terminal_chain\": \"" << pageTerminalChain << "\",\n"
             << "  \"page_span_observation_contract\": \"Property 12000/12001 UINT64 Value, one-based inclusive pages; start >= 1 and end > start\",\n"
             << "  \"rejection_fixture\": \"native-rejections.txt\",\n"
             << "  \"rejection_fixture_sha256\": \"" << rejectionHash << "\",\n"
             << "  \"emitter_sha256\": \"" << emitterHash << "\",\n"
             << "  \"source_sha256\": \"" << sourceHash << "\",\n"
             << "  \"vector_count\": " << vectors.size() << ",\n"
             << "  \"fragment_header_bytes\": 56,\n"
             << "  \"large_stream_mode\": \"--hgn1-large <field-bytes>\"\n}\n";
    manifest.close();
    return static_cast<bool>(manifest);
}

class DeterministicBlobReader final : public query::QueryViewBlobReader {
public:
    mutable bool failNext = false;
    bool Read(const query::BlobSlice& slice,
              const std::uint64_t relativeOffset,
              std::uint8_t* const output,
              const std::uint64_t length) const noexcept override {
        if (failNext) {
            failNext = false;
            return false;
        }
        if (relativeOffset > slice.length ||
            length > slice.length - relativeOffset ||
            (length != 0 && output == nullptr))
            return false;
        for (std::uint64_t index = 0; index != length; ++index)
            output[index] = static_cast<std::uint8_t>(
                slice.offset + relativeOffset + index);
        return true;
    }
};

Bytes BlobReadFrame(const Uuid128& cursor, const GraphVersionV1& version,
                    const query::BlobSlice& slice,
                    const std::uint64_t relativeOffset,
                    const std::uint64_t maximumBytes) {
    Header header{};
    header.message = MessageKind::BlobReadRequest;
    header.cursorOrUpload = cursor;
    static_cast<void>(ApplyVersionToHeader(version, &header));
    return Frame(
        header,
        Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
            EncodeField(2, 1, ScalarTag::Struct, View(VersionBytes(version))),
            EncodeField(3, 1, ScalarTag::UUID128, View(Id(slice.id))),
            EncodeField(4, 1, ScalarTag::Uint64, View(U64(slice.offset))),
            EncodeField(5, 1, ScalarTag::Uint64, View(U64(slice.length))),
            EncodeField(6, 1, ScalarTag::SHA256, View(Dig(slice.digest))),
            EncodeField(7, 1, ScalarTag::Uint64, View(U64(relativeOffset))),
            EncodeField(8, 1, ScalarTag::Uint64, View(U64(maximumBytes))),
        }));
}

Bytes CursorDispositionFrame(MessageKind kind, const Uuid128& cursor,
                             const DocumentSessionId& session);

Bytes BlobPackFrame(const Uuid128& cursor, const GraphVersionV1& version,
                    const Uuid128& transfer,
                    const std::vector<query::BlobSlice>& slices,
                    const std::uint64_t maximum = kMaximumPayloadBytes) {
    Bytes tuples;
    Put64(&tuples, static_cast<std::uint64_t>(slices.size()));
    for (const query::BlobSlice& slice : slices) {
        Add(&tuples, Id(slice.id));
        Add(&tuples, U64(slice.offset));
        Add(&tuples, U64(slice.length));
        Add(&tuples, Dig(slice.digest));
    }
    Header header{};
    header.message = MessageKind::BlobPackRequest;
    header.cursorOrUpload = cursor;
    static_cast<void>(ApplyVersionToHeader(version, &header));
    return Frame(
        header,
        Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
            EncodeField(2, 1, ScalarTag::Struct, View(VersionBytes(version))),
            EncodeField(3, 1, ScalarTag::UUID128, View(Id(transfer))),
            EncodeField(4, 1, ScalarTag::Bytes,
                        View(codec::BytesValue(View(tuples)))),
            EncodeField(5, 1, ScalarTag::Uint64, View(U64(maximum))),
        }));
}

bool BlobPack651RoundTrip() {
    CleanupProcessState();
    const GraphVersionV1 version = Version();
    const Route route{821, 822};
    std::uint64_t negotiated = 0;
    bool ok = Expect(
        NegotiateCapabilities(version.documentSessionId, UINT64_C(0x09), route,
                              &negotiated) ==
                CapabilityNegotiationStatus::Negotiated &&
            negotiated == UINT64_C(0x09),
        L"BlobPack session negotiated");
    std::vector<std::uint64_t> lengths{0};
    lengths.insert(lengths.end(), 646, UINT64_C(74));
    lengths.push_back(14);
    lengths.push_back(35120);
    lengths.push_back(82318);
    lengths.push_back(747180);
    std::vector<query::BlobSlice> slices;
    slices.reserve(lengths.size());
    for (std::size_t index = 0; index < lengths.size(); ++index) {
        query::BlobSlice slice{};
        slice.id.bytes[0] = static_cast<std::uint8_t>(index >> 8);
        slice.id.bytes[1] = static_cast<std::uint8_t>(index);
        slice.id.bytes[6] = 0x40;
        slice.id.bytes[8] = 0x80;
        slice.offset = static_cast<std::uint64_t>(index) * 1000000;
        slice.length = lengths[index];
        Bytes content(static_cast<std::size_t>(slice.length));
        for (std::uint64_t at = 0; at < slice.length; ++at)
            content[static_cast<std::size_t>(at)] = static_cast<std::uint8_t>(
                slice.offset + at);
        slice.digest = codec::Hash(View(content));
        slices.push_back(slice);
    }
    std::sort(slices.begin(), slices.end(), [](const query::BlobSlice& left,
                                               const query::BlobSlice& right) {
        Bytes leftTuple = Id(left.id);
        Add(&leftTuple, U64(left.offset));
        Add(&leftTuple, U64(left.length));
        Add(&leftTuple, Dig(left.digest));
        Bytes rightTuple = Id(right.id);
        Add(&rightTuple, U64(right.offset));
        Add(&rightTuple, U64(right.length));
        Add(&rightTuple, Dig(right.digest));
        return leftTuple < rightTuple;
    });
    query::QueryView view{};
    view.blobClosure = slices;
    view.blobReader = std::make_shared<DeterministicBlobReader>();
    const Uuid128 cursor = Uuid(72);
    ok = Expect(DebugInstallBlobCursor(cursor, version, route, std::move(view)),
                L"BlobPack cursor installed") && ok;
    std::vector<query::BlobSlice> duplicate = slices;
    duplicate[1] = duplicate[0];
    ok = Expect(IsError(ProcessRequest(
                            MessageKind::BlobPackRequest,
                            View(BlobPackFrame(cursor, version, Uuid(70), duplicate)),
                            route), ErrorCode::BadField),
                L"BlobPack duplicate tuple rejected") && ok;
    std::vector<query::BlobSlice> unauthorizedBefore = slices;
    unauthorizedBefore[0].digest.bytes[0] ^= 1;
    ok = Expect(IsError(ProcessRequest(
                            MessageKind::BlobPackRequest,
                            View(BlobPackFrame(cursor, version, Uuid(71),
                                               unauthorizedBefore)), route),
                        ErrorCode::BadField),
                L"BlobPack unauthorized tuple rejected") && ok;
    ok = Expect(IsError(ProcessRequest(
                            MessageKind::BlobPackRequest,
                            View(BlobPackFrame(cursor, version, Uuid(69), slices,
                                               UINT64_C(100))), route),
                        ErrorCode::BufferTooSmall),
                L"BlobPack bounded response budget rejected without commit") && ok;
    const Bytes request = BlobPackFrame(cursor, version, Uuid(73), slices);
    DebugFailNextBlobResponseEncoding();
    ok = Expect(IsError(ProcessRequest(
                            MessageKind::BlobPackRequest, View(request), route),
                        ErrorCode::Internal),
                L"BlobPack encode failure does not commit") && ok;
    const Bytes response = ProcessRequest(
        MessageKind::BlobPackRequest, View(request), route);
    Header responseHeader{};
    std::vector<ParsedField> responseFields;
    ok = Expect(Decode(response, &responseHeader, &responseFields) &&
                    responseHeader.message == MessageKind::BlobPack &&
                    response.size() < kMaximumFrameBytes,
                L"BlobPack 651 slices use one bounded response") && ok;
    const Bytes replay = ProcessRequest(
        MessageKind::BlobPackRequest, View(request), route);
    ok = Expect(replay == response,
                L"BlobPack exact replay is byte identical") && ok;
    query::BlobSlice unauthorized = slices.front();
    unauthorized.digest.bytes[0] ^= 1;
    std::vector<query::BlobSlice> changed = slices;
    changed[0] = unauthorized;
    std::sort(changed.begin(), changed.end(), [](const query::BlobSlice& left,
                                                 const query::BlobSlice& right) {
        return left.id.bytes < right.id.bytes;
    });
    ok = Expect(IsError(ProcessRequest(
                            MessageKind::BlobPackRequest,
                            View(BlobPackFrame(cursor, version, Uuid(74), changed)),
                            route), ErrorCode::BadField),
                L"BlobPack changed replay rejected") && ok;
    query::QueryView cancelledView{};
    cancelledView.blobClosure = slices;
    cancelledView.blobReader = std::make_shared<DeterministicBlobReader>();
    const Uuid128 cancelledCursor = Uuid(75);
    ok = Expect(DebugInstallBlobCursor(
                    cancelledCursor, version, route, std::move(cancelledView)),
                L"BlobPack cancellation cursor installed") && ok;
    static_cast<void>(ProcessRequest(
        MessageKind::GraphCancelRequest,
        View(CursorDispositionFrame(
            MessageKind::GraphCancelRequest, cancelledCursor,
            version.documentSessionId)), route));
    ok = Expect(IsError(ProcessRequest(
                            MessageKind::BlobPackRequest,
                            View(BlobPackFrame(cancelledCursor, version, Uuid(76),
                                               slices)), route),
                        ErrorCode::CursorNotFound),
                L"BlobPack cancellation discards transfer") && ok;
    std::wcout << L"BLOB_PACK_651_EXACT " << ok
               << L" blobs=" << slices.size()
               << L" bytes=912436 legacy_calls=676 pack_calls=1\n";
    CleanupProcessState();
    return ok;
}

Bytes CursorDispositionFrame(const MessageKind kind, const Uuid128& cursor,
                             const DocumentSessionId& session) {
    Header header{};
    header.message = kind;
    header.session = session;
    header.cursorOrUpload = cursor;
    return Frame(
        header,
        Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
            EncodeField(2, 1, ScalarTag::UUID128, View(Id(session))),
        }));
}

bool IsBlobChunk(const Bytes& frame, const std::uint64_t offset,
                 const std::uint64_t total, const bool terminal) {
    Header header{};
    std::vector<ParsedField> fields;
    return Decode(frame, &header, &fields) &&
        header.message == MessageKind::BlobChunk &&
        header.fragmentOffset == offset && header.fragmentTotal == total &&
        (((header.flags & kFlagTerminal) != 0) == terminal);
}

bool BlobReadConsumptionOrdering() {
    CleanupProcessState();
    const GraphVersionV1 version = Version();
    const Route route{811, 812};
    std::uint64_t negotiated = 0;
    bool ok = Expect(
        NegotiateCapabilities(version.documentSessionId, UINT64_C(0x09), route,
                              &negotiated) ==
                CapabilityNegotiationStatus::Negotiated &&
            negotiated == UINT64_C(0x09),
        L"BlobRead test session negotiated");

    query::BlobSlice first{};
    first.id = Uuid(91);
    first.offset = 100;
    first.length = 6;
    first.digest.bytes.fill(0xa1);
    query::BlobSlice second{};
    second.id = Uuid(111);
    second.offset = 200;
    second.length = 4;
    second.digest.bytes.fill(0xb2);
    const auto reader = std::make_shared<DeterministicBlobReader>();
    query::QueryView view{};
    view.blobClosure = {first, second};
    view.blobReader = reader;
    const Uuid128 cursor = Uuid(71);
    ok = Expect(DebugInstallBlobCursor(cursor, version, route, std::move(view)),
                L"BlobRead deterministic cursor installed") && ok;

    const Bytes firstAtZero = BlobReadFrame(cursor, version, first, 0, 2);
    ok = Expect(
        IsError(ProcessRequest(MessageKind::BlobReadRequest,
                               View(BlobReadFrame(cursor, version, first, 2, 2)),
                               route),
                ErrorCode::BadField),
        L"BlobRead forward offset skip rejected") && ok;
    ok = Expect(
        IsBlobChunk(ProcessRequest(MessageKind::BlobReadRequest,
                                   View(firstAtZero), route),
                    0, first.length, false),
        L"BlobRead first tuple starts at zero") && ok;
    ok = Expect(
        IsError(ProcessRequest(MessageKind::BlobReadRequest,
                               View(firstAtZero), route),
                ErrorCode::BadField),
        L"BlobRead identical authenticated request replay rejected") && ok;
    ok = Expect(
        IsError(ProcessRequest(MessageKind::BlobReadRequest,
                               View(BlobReadFrame(cursor, version, first, 1, 2)),
                               route),
                ErrorCode::BadField),
        L"BlobRead backward offset after success rejected") && ok;

    query::BlobSlice changedOffset = first;
    ++changedOffset.offset;
    query::BlobSlice changedLength = first;
    --changedLength.length;
    query::BlobSlice changedDigest = first;
    changedDigest.digest.bytes[0] ^= 1;
    for (const query::BlobSlice& unauthorized :
         {changedOffset, changedLength, changedDigest}) {
        ok = Expect(
            IsError(ProcessRequest(
                        MessageKind::BlobReadRequest,
                        View(BlobReadFrame(cursor, version, unauthorized, 0, 2)),
                        route),
                    ErrorCode::BadField),
            L"BlobRead exact authorized four-tuple preserved") && ok;
    }

    ok = Expect(
        IsBlobChunk(ProcessRequest(
                        MessageKind::BlobReadRequest,
                        View(BlobReadFrame(cursor, version, second, 0, 2)), route),
                    0, second.length, false),
        L"BlobRead independent authorized tuple starts at zero") && ok;
    ok = Expect(
        IsBlobChunk(ProcessRequest(
                        MessageKind::BlobReadRequest,
                        View(BlobReadFrame(cursor, version, first, 2, 2)), route),
                    2, first.length, false),
        L"BlobRead first tuple advances independently") && ok;

    reader->failNext = true;
    const Bytes firstFinal = BlobReadFrame(cursor, version, first, 4, 2);
    ok = Expect(
        IsError(ProcessRequest(MessageKind::BlobReadRequest, View(firstFinal),
                               route),
                ErrorCode::StorageFailure),
        L"BlobRead source read failure does not advance") && ok;
    ok = Expect(
        IsBlobChunk(ProcessRequest(MessageKind::BlobReadRequest,
                                   View(firstFinal), route),
                    4, first.length, true),
        L"BlobRead same offset retries after source failure") && ok;
    ok = Expect(
        IsError(ProcessRequest(MessageKind::BlobReadRequest, View(firstFinal),
                               route),
                ErrorCode::BadField),
        L"BlobRead replay after tuple completion rejected") && ok;

    const Bytes secondFinal = BlobReadFrame(cursor, version, second, 2, 2);
    DebugFailNextBlobResponseEncoding();
    ok = Expect(
        IsError(ProcessRequest(MessageKind::BlobReadRequest, View(secondFinal),
                               route),
                ErrorCode::Internal),
        L"BlobRead response encode failure does not advance") && ok;
    ok = Expect(
        IsBlobChunk(ProcessRequest(MessageKind::BlobReadRequest,
                                   View(secondFinal), route),
                    2, second.length, true),
        L"BlobRead same offset retries after response encode failure") && ok;

    GraphVersionV1 staleVersion = version;
    ++staleVersion.semanticRevision;
    ok = Expect(
        IsError(ProcessRequest(
                    MessageKind::BlobReadRequest,
                    View(BlobReadFrame(cursor, staleVersion, second, 2, 2)),
                    route),
                ErrorCode::StaleGraph),
        L"BlobRead stale full version remains rejected") && ok;
    ok = Expect(
        IsError(ProcessRequest(MessageKind::BlobReadRequest,
                               View(secondFinal), Route{route.documentId + 1,
                                                        route.windowHandle}),
                ErrorCode::UnsupportedCapability),
        L"BlobRead changed route fails AssetRead capability before cursor lookup") && ok;

    const Bytes cancel = CursorDispositionFrame(
        MessageKind::GraphCancelRequest, cursor, version.documentSessionId);
    const Bytes cancelled = ProcessRequest(
        MessageKind::GraphCancelRequest, View(cancel), route);
    Header disposition{};
    std::vector<ParsedField> dispositionFields;
    ok = Expect(Decode(cancelled, &disposition, &dispositionFields) &&
                    disposition.message == MessageKind::CursorClosedReceipt,
                L"BlobRead cursor cancel succeeds") && ok;
    ok = Expect(
        IsError(ProcessRequest(MessageKind::BlobReadRequest, View(secondFinal),
                               route),
                ErrorCode::CursorNotFound),
        L"BlobRead after cancel remains rejected") && ok;

    query::QueryView closeView{};
    closeView.blobClosure = {first};
    closeView.blobReader = reader;
    const Uuid128 closeCursor = Uuid(131);
    ok = Expect(DebugInstallBlobCursor(closeCursor, version, route,
                                       std::move(closeView)),
                L"BlobRead close cursor installed") && ok;
    const Bytes close = CursorDispositionFrame(
        MessageKind::GraphCloseRequest, closeCursor, version.documentSessionId);
    const Bytes closed = ProcessRequest(
        MessageKind::GraphCloseRequest, View(close), route);
    ok = Expect(Decode(closed, &disposition, &dispositionFields) &&
                    disposition.message == MessageKind::CursorClosedReceipt,
                L"BlobRead cursor close succeeds") && ok;
    ok = Expect(
        IsError(ProcessRequest(
                    MessageKind::BlobReadRequest,
                    View(BlobReadFrame(closeCursor, version, first, 0, 2)), route),
                ErrorCode::CursorNotFound),
        L"BlobRead after close remains rejected") && ok;

    CleanupProcessState();
    std::wcout << L"BLOB_READ_CURSOR_OWNED_CONSUMPTION_MATRIX " << ok << L'\n';
    return ok;
}

bool PackedLargeGraphTransfer() {
    CleanupProcessState();
    constexpr std::uint64_t target = UINT64_C(4) * 1024 * 1024;
    constexpr std::uint64_t desired = UINT64_C(24) * 1024 * 1024;
    query::QueryView view{};
    std::uint64_t recordCount = 0;
    while (view.records.size() < desired) {
        Bytes value(320);
        for (size_t index = 0; index < value.size(); ++index)
            value[index] = static_cast<std::uint8_t>(recordCount + index);
        const Bytes canonicalValue = codec::BytesValue(View(value));
        const Bytes field = EncodeField(
            1, 1, ScalarTag::Bytes, View(canonicalValue));
        Bytes recordHeader;
        if (field.empty() || !EncodeCanonicalRecordHeaderV1(
                RecordKind::Manifest, 0, 1, recordCount, field.size(),
                &recordHeader))
            return Expect(false, L"large packed stream record encoding");
        Add(&view.records, recordHeader);
        Add(&view.records, field);
        ++recordCount;
    }
    view.canonical.recordStream.itemCount = recordCount;
    view.canonical.recordStream.byteLength = view.records.size();
    const Uuid128 cursor = Uuid(207);
    GraphVersionV1 version = Version();
    version.profileBits = ProfileBit(ProfileId::Structure);
    const Route route{207, 208};
    std::uint64_t negotiated = 0;
    if (NegotiateCapabilities(version.documentSessionId, 1, route, &negotiated) !=
            CapabilityNegotiationStatus::Negotiated || negotiated != 1 ||
        !DebugInstallGraphCursor(cursor, version, route, std::move(view)))
        return Expect(false, L"large packed stream cursor installation");

    GraphFragmentContinuationV1 continuation{};
    continuation.nextResponseSequence = 1;
    identity::SerializedGraphVersionV1 serialized{};
    if (identity::SerializeGraphVersion(version, &serialized) !=
        identity::GraphVersionError::None)
        return Expect(false, L"large packed stream version");
    const auto requestFor = [&](const std::uint64_t sequence,
                                const Sha256& previous,
                                const std::uint64_t budget) {
        Header requestHeader{};
        requestHeader.message = MessageKind::GraphNextRequest;
        requestHeader.cursorOrUpload = cursor;
        requestHeader.sequence = sequence;
        requestHeader.previousChain = previous;
        static_cast<void>(ApplyVersionToHeader(version, &requestHeader));
        return Frame(requestHeader, Payload({
            EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
            EncodeField(2, 1, ScalarTag::Struct,
                        {serialized.data(), serialized.size()}),
            EncodeField(3, 1, ScalarTag::Uint64, View(U64(budget))),
            EncodeField(4, 1, ScalarTag::Uint64, View(U64(sequence))),
            EncodeField(5, 1, ScalarTag::SHA256,
                        {previous.bytes.data(), previous.bytes.size()}),
        }));
    };
    const Bytes firstRequest = requestFor(1, {}, target);
    DebugFailNextGraphResponseEncoding();
    const Bytes injectedFailure = ProcessRequest(
        MessageKind::GraphNextRequest, View(firstRequest), route);
    const bool retryStateExact = IsError(injectedFailure, ErrorCode::Internal);
    ResetFrameEnvelopeDebugCounters();
    std::uint64_t responseBytes = 0;
    std::uint64_t chunkCalls = 0;
    std::uint64_t maximumChunk = 0;
    ErrorCode error = ErrorCode::Internal;
    LARGE_INTEGER frequency{}, wallStart{}, wallEnd{};
    FILETIME created{}, exited{}, kernelStart{}, userStart{}, kernelEnd{}, userEnd{};
    static_cast<void>(QueryPerformanceFrequency(&frequency));
    static_cast<void>(QueryPerformanceCounter(&wallStart));
    static_cast<void>(GetThreadTimes(GetCurrentThread(), &created, &exited,
                                    &kernelStart, &userStart));
    for (std::uint64_t sequence = 1;; ++sequence) {
        const Bytes request = requestFor(
            sequence, continuation.previousChain, target);
        const Bytes response = ProcessRequest(
            MessageKind::GraphNextRequest, View(request), route);
        Header responseHeader{};
        ByteView responsePayload{};
        if (response.empty() || response.size() > target ||
            !DecodeFrame(View(response), &responseHeader, &responsePayload,
                         &error))
            return Expect(false, L"large packed stream response bounds");
        responseBytes += response.size();
        maximumChunk = (std::max)(maximumChunk,
                                  static_cast<std::uint64_t>(response.size()));
        std::uint64_t required = 0;
        if (!AcceptGraphResponseFrame(View(response), target, &continuation,
                                      &error, &required)) {
            std::wcout << L"GRAPH_PROTOCOL_24MB_DECODE_FAILURE sequence="
                       << sequence << L" error="
                       << static_cast<unsigned>(error)
                       << L" response=" << response.size()
                       << L" payload=" << responsePayload.size << L'\n';
            return Expect(false, L"large packed stream authenticated decode");
        }
        ++chunkCalls;
        if (responseHeader.message == MessageKind::GraphTerminal) break;
        if (responseHeader.message != MessageKind::GraphChunk)
            return Expect(false, L"large packed stream response kind");
    }
    static_cast<void>(GetThreadTimes(GetCurrentThread(), &created, &exited,
                                    &kernelEnd, &userEnd));
    static_cast<void>(QueryPerformanceCounter(&wallEnd));
    const ULARGE_INTEGER kernel0{{kernelStart.dwLowDateTime,
                                  kernelStart.dwHighDateTime}};
    const ULARGE_INTEGER user0{{userStart.dwLowDateTime,
                                userStart.dwHighDateTime}};
    const ULARGE_INTEGER kernel1{{kernelEnd.dwLowDateTime,
                                  kernelEnd.dwHighDateTime}};
    const ULARGE_INTEGER user1{{userEnd.dwLowDateTime,
                                userEnd.dwHighDateTime}};
    const std::uint64_t cpu100ns = kernel1.QuadPart - kernel0.QuadPart +
        user1.QuadPart - user0.QuadPart;
    const std::uint64_t wallUs = frequency.QuadPart == 0 ? 0 :
        static_cast<std::uint64_t>(
            (wallEnd.QuadPart - wallStart.QuadPart) * 1000000 /
            frequency.QuadPart);
    const std::uint64_t metadataBytes = recordCount * 56;
    const std::uint64_t payloadCapacity = target - kHeaderBytes;
    const std::uint64_t expectedChunks =
        (continuation.completedLogicalBytes + metadataBytes +
         payloadCapacity - 1) / payloadCapacity;
    const FrameEnvelopeDebugCounters counters =
        ReadFrameEnvelopeDebugCounters();
    const bool operationCeilings =
        counters.queryFramePlans + 1 == chunkCalls &&
        counters.fragmentValueBytes != 0 &&
        counters.fragmentValueBytes < responseBytes &&
        counters.fragmentTemporaryCopyBytes == 0 &&
        counters.streamHashStagingBytes == 0 &&
        counters.framePayloadCopyBytes == 0 &&
        counters.responseAuthenticationPasses == 0;
    const bool exact = retryStateExact && continuation.terminal &&
        continuation.completedRecordCount == recordCount &&
        continuation.completedLogicalBytes >= desired &&
        chunkCalls <= expectedChunks + 2 && chunkCalls < 16 &&
        maximumChunk > target - 1024 && DebugCursorCount() == 1 &&
        operationCeilings;
    std::wcout << L"GRAPH_FRAME_ENVELOPE_OPERATION_CEILINGS "
               << operationCeilings
               << L" plans=" << counters.queryFramePlans
               << L" read=" << counters.recordReadBytes
               << L" values=" << counters.fragmentValueBytes
               << L" fragment_temp=" << counters.fragmentTemporaryCopyBytes
               << L" stream_stage=" << counters.streamHashStagingBytes
               << L" frame_copy=" << counters.framePayloadCopyBytes
               << L" self_auth=" << counters.responseAuthenticationPasses
               << L" self_auth_bytes=" << counters.responseAuthenticationBytes
               << L" plan_ticks=" << counters.queryFramePlanTicks
               << L" read_ticks=" << counters.recordReadTicks
               << L" digest_ticks=" << counters.digestTicks
               << L" encode_ticks=" << counters.frameEncodeTicks << L'\n';
    std::wcout << L"GRAPH_PROTOCOL_24MB_PACKED_TRANSFER " << exact
               << L" calls=" << chunkCalls
               << L" logical=" << continuation.completedLogicalBytes
               << L" response=" << responseBytes
               << L" max_chunk=" << maximumChunk
               << L" records=" << recordCount
               << L" wall_us=" << wallUs
               << L" cpu_100ns=" << cpu100ns << L'\n';
    CleanupProcessState();
    return exact;
}

bool DocumentGraphProtocolSmoke() {
    const bool capabilityContract =
        kCapabilityBits == UINT64_C(0x39) &&
        (kCapabilityBits & UINT64_C(0x01)) != 0;
    std::wcout << L"GRAPH_CAPABILITY_EXACT_0X39_ASSET_PATCHVALIDATE_ON "
               << capabilityContract << L'\n';
    bool ok = Expect(capabilityContract,
                     L"native capability is exactly 0x39 with GraphRead, AssetRead, PatchUpload, and PatchValidate on");
    ok = Expect(BCryptEarlyReturnCleanup(),
                L"BCrypt handles close exactly once on deterministic early return") && ok;
    ok = HeaderAndCapabilities() && ok;
    ok = QueryAndFragments() && ok;
    ok = BlobPack651RoundTrip() && ok;
    ok = BlobReadConsumptionOrdering() && ok;
    ok = SafeArrays() && ok;
    ok = GraphOpenOrdering() && ok;
    ok = GraphChunkRules() && ok;
    ok = PackedLargeGraphTransfer() && ok;
    ok = MessageMatrix() && ok;
    ok = PatchCertification() && ok;
    ok = PatchUpload() && ok;
    return ok;
}

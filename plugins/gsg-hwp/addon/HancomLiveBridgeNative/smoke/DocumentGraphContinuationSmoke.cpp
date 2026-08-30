#include "../DocumentGraphProtocol.h"

#include <Windows.h>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <vector>

namespace {
using namespace hancom::graph;
using namespace hancom::graph::codec;
using namespace hancom::graph::identity;
using namespace hancom::graph::protocol;

bool Expect(bool condition, const wchar_t* label) {
    if (!condition) std::wcerr << L"DocumentGraphContinuationSmoke failed: " << label << L'\n';
    return condition;
}
Bytes U8(std::uint8_t value) { return {value}; }
Bytes U64(std::uint64_t value) {
    Bytes bytes(8);
    for (unsigned index = 0; index != 8; ++index)
        bytes[index] = static_cast<std::uint8_t>(value >> (index * 8));
    return bytes;
}
Bytes Dig(const Sha256& value) { return {value.bytes.begin(), value.bytes.end()}; }
Bytes Id(const Uuid128& value) { return {value.bytes.begin(), value.bytes.end()}; }
Bytes VersionBytes(const GraphVersionV1& version) {
    SerializedGraphVersionV1 serialized{};
    static_cast<void>(SerializeGraphVersion(version, &serialized));
    return {serialized.begin(), serialized.end()};
}
void Put16(Bytes* bytes, std::uint16_t value) {
    bytes->push_back(static_cast<std::uint8_t>(value));
    bytes->push_back(static_cast<std::uint8_t>(value >> 8));
}
void Put64(Bytes* bytes, std::uint64_t value) {
    for (unsigned index = 0; index != 8; ++index)
        bytes->push_back(static_cast<std::uint8_t>(value >> (index * 8)));
}
Bytes LogicalRecordBytes(RecordId id, std::uint8_t value) {
    const Bytes field = EncodeField(1, 1, ScalarTag::Uint8, View(U8(value)));
    Bytes record;
    Put16(&record, static_cast<std::uint16_t>(RecordKind::Property));
    Put16(&record, 1);
    Put16(&record, 0);
    Put16(&record, 1);
    Put64(&record, id);
    Put64(&record, field.size());
    record.insert(record.end(), field.begin(), field.end());
    return record;
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
Bytes Fragment(RecordId record, RecordKind kind, FieldTag tag,
               std::uint16_t flags, ScalarTag scalar, std::uint64_t total,
               std::uint64_t offset, ByteView value,
               std::uint16_t fieldCount = 1) {
    FragmentHeader header{};
    header.recordKind = kind;
    header.fieldTag = tag;
    header.fieldFlags = flags;
    header.scalar = scalar;
    header.logicalRecordFieldCount = fieldCount;
    header.recordId = record;
    header.elementCount = 1;
    header.totalFieldBytes = total;
    header.fragmentOffset = offset;
    header.fragmentBytes = value.size;
    Bytes encoded;
    static_cast<void>(EncodeFragment(header, value, &encoded));
    return encoded;
}
Bytes Frame(const GraphVersionV1& version, const Uuid128& cursor,
            const Bytes& payload, std::uint32_t flags, std::uint64_t sequence,
            std::uint64_t offset, std::uint64_t total, const Sha256& previous) {
    Header header{};
    header.message = MessageKind::GraphChunk;
    header.flags = flags;
    header.sequence = sequence;
    header.fragmentOffset = offset;
    header.fragmentTotal = total;
    header.cursorOrUpload = cursor;
    header.previousChain = previous;
    static_cast<void>(ApplyVersionToHeader(version, &header));
    Bytes encoded;
    static_cast<void>(EncodeFrame(header, View(payload), &encoded));
    return encoded;
}
bool HeaderOf(const Bytes& frame, Header* header) {
    ByteView payload{};
    ErrorCode error{};
    return DecodeFrame(View(frame), header, &payload, &error);
}
} // namespace

bool DocumentGraphContinuationSmoke() {
    using namespace hancom::graph;
    using namespace hancom::graph::codec;
    using namespace hancom::graph::identity;
    using namespace hancom::graph::protocol;
    const GraphVersionV1 version = Version();
    const Uuid128 cursor = Uuid(77);
    ErrorCode error{};
    std::uint64_t required = 0;

    Header openHeader{};
    openHeader.message = MessageKind::OpenReceipt;
    openHeader.cursorOrUpload = cursor;
    static_cast<void>(ApplyVersionToHeader(version, &openHeader));
    Bytes openPayload;
    for (const Bytes& field : {
        EncodeField(1, 1, ScalarTag::UUID128, View(Id(cursor))),
        EncodeField(2, 1, ScalarTag::Struct, View(VersionBytes(version)))})
        openPayload.insert(openPayload.end(), field.begin(), field.end());
    Bytes openFrame;
    static_cast<void>(EncodeFrame(openHeader, View(openPayload), &openFrame));
    GraphFragmentContinuationV1 openState{};
    bool ok = Expect(AcceptGraphResponseFrame(View(openFrame), openFrame.size(),
        &openState, &error, &required) && openState.identityBound &&
        openState.boundCursor.bytes == cursor.bytes && openState.nextResponseSequence == 1,
        L"OpenReceipt binds cursor/full GraphVersion") &&
        Expect(AcceptGraphResponseFrame(View(openFrame), openFrame.size(),
        &openState, &error, &required) && openState.nextResponseSequence == 1,
        L"OpenReceipt immediate replay is inert");

    const Bytes firstPayload = Fragment(0, RecordKind::Property, 1, 1 | 4 | 16,
                                        ScalarTag::Struct, 2, 0, View(U8(1)));
    const Bytes firstFrame = Frame(version, cursor, firstPayload, kFlagFragmented,
                                   0, 0, 50, {});
    GraphFragmentContinuationV1 state{};
    ok = Expect(!AcceptGraphResponseFrame(View(firstFrame), firstFrame.size() - 1,
        &state, &error, &required) && error == ErrorCode::BufferTooSmall &&
        required == firstFrame.size() && state.nextResponseSequence == 0,
        L"BufferTooSmall leaves state unchanged") &&
        Expect(AcceptGraphResponseFrame(View(firstFrame), firstFrame.size(),
        &state, &error, &required) && state.active && state.currentRecordId == 0 &&
        state.currentRecordKind == RecordKind::Property && state.currentFieldTag == 1 &&
        state.nextFieldFragmentOffset == 1 && state.nextReconstructedOffset == 49,
        L"partial continuation state captured");
    const GraphFragmentContinuationV1 afterFirst = state;
    ok = Expect(AcceptGraphResponseFrame(View(firstFrame), firstFrame.size(),
        &state, &error, &required) && state.nextResponseSequence == afterFirst.nextResponseSequence &&
        state.nextFieldFragmentOffset == afterFirst.nextFieldFragmentOffset,
        L"immediate replay is inert") && ok;

    Header firstHeader{};
    static_cast<void>(HeaderOf(firstFrame, &firstHeader));
    const auto finalFrame = [&](RecordId record, RecordKind kind, FieldTag tag) {
        const Bytes payload = Fragment(record, kind, tag, 1 | 8 | 32,
            ScalarTag::Struct, 2, 1, View(U8(2)));
        return Frame(version, cursor, payload, 0, 1, 49, 50, firstHeader.chainDigest);
    };
    for (const Bytes& invalid : {
        finalFrame(1, RecordKind::Property, 1),
        finalFrame(0, RecordKind::Node, 1),
        finalFrame(0, RecordKind::Property, 2)}) {
        ok = Expect(!AcceptGraphResponseFrame(View(invalid), invalid.size(),
            &state, &error, &required) && error == ErrorCode::BadField &&
            state.currentRecordId == afterFirst.currentRecordId &&
            state.currentRecordKind == afterFirst.currentRecordKind &&
            state.currentFieldTag == afterFirst.currentFieldTag &&
            state.nextFieldFragmentOffset == afterFirst.nextFieldFragmentOffset &&
            state.nextResponseSequence == afterFirst.nextResponseSequence &&
            state.previousChain.bytes == afterFirst.previousChain.bytes &&
            state.cachedFrame == afterFirst.cachedFrame,
            L"invalid continuation identity is atomic") && ok;
    }
    const Bytes correctPayload = Fragment(0, RecordKind::Property, 1, 1 | 8 | 32,
        ScalarTag::Struct, 2, 1, View(U8(2)));
    Sha256 wrongPrevious{};
    wrongPrevious.bytes.fill(0x77);
    const Bytes wrongChain = Frame(version, cursor, correctPayload, 0, 1, 49, 50,
                                   wrongPrevious);
    const Bytes wrongSequence = Frame(version, cursor, correctPayload, 0, 2, 49, 50,
                                      firstHeader.chainDigest);
    for (const Bytes& invalidChain : {wrongChain, wrongSequence}) {
        ok = Expect(!AcceptGraphResponseFrame(View(invalidChain), invalidChain.size(),
            &state, &error, &required) && error == ErrorCode::SequenceMismatch &&
            state.nextResponseSequence == afterFirst.nextResponseSequence &&
            state.previousChain.bytes == afterFirst.previousChain.bytes &&
            state.cachedFrame == afterFirst.cachedFrame,
            L"invalid continuation sequence/chain is atomic") && ok;
    }
    std::vector<GraphVersionV1> mismatchedVersions;
    GraphVersionV1 mismatch = version;
    mismatch.profileBits |= ProfileBit(ProfileId::EditableText);
    mismatchedVersions.push_back(mismatch);
    mismatch = version; mismatch.semanticCertified = false; mismatchedVersions.push_back(mismatch);
    mismatch = version; mismatch.documentSessionId = Uuid(90); mismatchedVersions.push_back(mismatch);
    mismatch = version; mismatch.graphId = Uuid(91); mismatchedVersions.push_back(mismatch);
    mismatch = version; mismatch.semanticRevision = 2; mismatchedVersions.push_back(mismatch);
    mismatch = version; mismatch.layoutRevision = 2; mismatchedVersions.push_back(mismatch);
    mismatch = version; mismatch.locatorEpoch = 2; mismatchedVersions.push_back(mismatch);
    mismatch = version; mismatch.observedSemanticRoot.bytes[0] ^= 1; mismatchedVersions.push_back(mismatch);
    mismatch = version; mismatch.captureRoot.bytes[0] ^= 1; mismatchedVersions.push_back(mismatch);
    mismatch = version;
    mismatch.profileBits = CloseProfileBits(ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout));
    mismatch.layoutPresent = true;
    mismatch.layoutRoot.bytes.fill(0x66);
    mismatchedVersions.push_back(mismatch);
    for (const GraphVersionV1& wrongVersion : mismatchedVersions) {
        GraphFragmentContinuationV1 candidate = afterFirst;
        const Bytes invalid = Frame(wrongVersion, cursor, correctPayload, 0, 1, 49, 50,
                                    firstHeader.chainDigest);
        ok = Expect(!AcceptGraphResponseFrame(View(invalid), invalid.size(),
            &candidate, &error, &required) && candidate.cachedFrame == afterFirst.cachedFrame,
            L"continuation full GraphVersion identity mismatch is atomic") && ok;
    }
    GraphFragmentContinuationV1 wrongCursorState = afterFirst;
    const Bytes wrongCursorFrame = Frame(version, Uuid(88), correctPayload, 0, 1, 49, 50,
                                         firstHeader.chainDigest);
    ok = Expect(!AcceptGraphResponseFrame(View(wrongCursorFrame), wrongCursorFrame.size(),
        &wrongCursorState, &error, &required) &&
        wrongCursorState.cachedFrame == afterFirst.cachedFrame,
        L"continuation cursor identity mismatch is atomic") && ok;

    const Bytes correct = finalFrame(0, RecordKind::Property, 1);
    ok = Expect(AcceptGraphResponseFrame(View(correct), correct.size(),
        &state, &error, &required) && !state.active && state.nextRecordId == 1 &&
        state.nextReconstructedOffset == 50, L"invalid-then-correct continuation") && ok;

    const Bytes record0Payload = Fragment(0, RecordKind::Property, 1,
        1 | 4 | 8 | 16 | 32, ScalarTag::Uint8, 1, 0, View(U8(1)));
    const Bytes record0 = Frame(version, cursor, record0Payload, kFlagFragmented,
                                0, 0, 98, {});
    Header record0Header{};
    GraphFragmentContinuationV1 records{};
    ok = Expect(AcceptGraphResponseFrame(View(record0), record0.size(),
        &records, &error, &required), L"record0 closes before next frame") && ok;
    static_cast<void>(HeaderOf(record0, &record0Header));
    const Bytes record1Payload = Fragment(1, RecordKind::Property, 1,
        1 | 4 | 8 | 16 | 32, ScalarTag::Uint8, 1, 0, View(U8(2)));
    const Bytes record1 = Frame(version, cursor, record1Payload, 0, 1, 49, 98,
                                record0Header.chainDigest);
    ok = Expect(AcceptGraphResponseFrame(View(record1), record1.size(),
        &records, &error, &required) && records.nextRecordId == 2,
        L"cross-frame record0 to record1") && ok;
    GraphFragmentContinuationV1 skipped{};
    static_cast<void>(AcceptGraphResponseFrame(View(record0), record0.size(),
        &skipped, &error, &required));
    const Bytes record2Payload = Fragment(2, RecordKind::Property, 1,
        1 | 4 | 8 | 16 | 32, ScalarTag::Uint8, 1, 0, View(U8(2)));
    const Bytes record2 = Frame(version, cursor, record2Payload, 0, 1, 49, 98,
                                record0Header.chainDigest);
    ok = Expect(!AcceptGraphResponseFrame(View(record2), record2.size(),
        &skipped, &error, &required) && skipped.nextRecordId == 1,
        L"cross-frame record0 to record2 rejected") && ok;

    GraphFragmentContinuationV1 spanning{};
    const Bytes spanningFirst = Frame(version, cursor, firstPayload, kFlagFragmented,
                                      0, 0, 99, {});
    Header spanningHeader{};
    ok = Expect(AcceptGraphResponseFrame(View(spanningFirst), spanningFirst.size(),
        &spanning, &error, &required), L"two-record boundary partial first") && ok;
    static_cast<void>(HeaderOf(spanningFirst, &spanningHeader));
    Bytes spanningPayload = Fragment(0, RecordKind::Property, 1, 1 | 8 | 32,
        ScalarTag::Struct, 2, 1, View(U8(2)));
    spanningPayload.insert(spanningPayload.end(), record1Payload.begin(), record1Payload.end());
    const Bytes spanningLast = Frame(version, cursor, spanningPayload, 0, 1, 49, 99,
                                     spanningHeader.chainDigest);
    ok = Expect(AcceptGraphResponseFrame(View(spanningLast), spanningLast.size(),
        &spanning, &error, &required) && spanning.nextRecordId == 2 && !spanning.active,
        L"two records span one continuation boundary") && ok;

    Bytes canonical = LogicalRecordBytes(0, 1);
    const Bytes canonical1 = LogicalRecordBytes(1, 2);
    canonical.insert(canonical.end(), canonical1.begin(), canonical1.end());
    const Sha256 streamDigest = DomainHash("HWPGRAPH\0STREAM\0V1", View(canonical));
    const Sha256 rawFragmentDigest = DomainHash("HWPGRAPH\0STREAM\0V1", View(record0Payload));
    const auto terminal = [&](const GraphVersionV1& terminalVersion,
                              const Uuid128& terminalCursor, std::uint64_t count,
                              std::uint64_t bytes, const Sha256& digest,
                              std::uint32_t extraFlags = 0) {
        Header header{};
        header.message = MessageKind::GraphTerminal;
        header.flags = kFlagTerminal | extraFlags;
        header.sequence = 2;
        header.fragmentOffset = 98;
        header.fragmentTotal = 98;
        header.cursorOrUpload = terminalCursor;
        header.previousChain = records.previousChain;
        static_cast<void>(ApplyVersionToHeader(terminalVersion, &header));
        Bytes payload;
        for (const Bytes& field : {
            EncodeField(1, 1, ScalarTag::Uint64, View(U64(count))),
            EncodeField(2, 1, ScalarTag::Uint64, View(U64(bytes))),
            EncodeField(3, 1, ScalarTag::SHA256, View(Dig(digest)))})
            payload.insert(payload.end(), field.begin(), field.end());
        Bytes encoded;
        static_cast<void>(EncodeFrame(header, View(payload), &encoded));
        return encoded;
    };
    Sha256 falseDigest = streamDigest;
    falseDigest.bytes[0] ^= 1;
    for (const Bytes& invalidTerminal : {
        terminal(version, cursor, 0, 98, streamDigest),
        terminal(version, cursor, 1, 98, streamDigest),
        terminal(version, cursor, 99, 98, streamDigest),
        terminal(version, cursor, 2, 97, streamDigest),
        terminal(version, cursor, 2, 99, streamDigest),
        terminal(version, cursor, 2, UINT64_MAX, streamDigest),
        terminal(version, cursor, 2, 98, falseDigest),
        terminal(version, cursor, 2, 98, rawFragmentDigest),
        terminal(version, Uuid(88), 2, 98, streamDigest)}) {
        const GraphFragmentContinuationV1 before = records;
        ok = Expect(!AcceptGraphResponseFrame(View(invalidTerminal), invalidTerminal.size(),
            &records, &error, &required) && records.cachedFrame == before.cachedFrame &&
            records.nextResponseSequence == before.nextResponseSequence &&
            records.completedRecordCount == before.completedRecordCount &&
            records.completedLogicalBytes == before.completedLogicalBytes &&
            records.streamHash.state == before.streamHash.state &&
            records.streamHash.totalBytes == before.streamHash.totalBytes,
            L"invalid terminal claim/identity is atomic") && ok;
    }
    for (const GraphVersionV1& wrongVersion : mismatchedVersions) {
        const GraphFragmentContinuationV1 before = records;
        const Bytes invalid = terminal(wrongVersion, cursor, 2, 98, streamDigest);
        ok = Expect(!AcceptGraphResponseFrame(View(invalid), invalid.size(),
            &records, &error, &required) && records.cachedFrame == before.cachedFrame,
            L"terminal full GraphVersion mismatch is atomic") && ok;
    }
    const Bytes pendingTerminal = terminal(version, cursor, 0, 49, streamDigest);
    const GraphFragmentContinuationV1 pendingBefore = afterFirst;
    GraphFragmentContinuationV1 pending = afterFirst;
    ok = Expect(!AcceptGraphResponseFrame(View(pendingTerminal), pendingTerminal.size(),
        &pending, &error, &required) && pending.cachedFrame == pendingBefore.cachedFrame,
        L"terminal with pending record rejected") && ok;
    const Bytes fragmentedTerminal = terminal(version, cursor, 2, 98, streamDigest,
                                               kFlagFragmented);
    ok = Expect(fragmentedTerminal.empty(), L"terminal fragmented flag rejected") && ok;

    const Bytes terminalFrame = terminal(version, cursor, 2, 98, streamDigest);
    ok = Expect(AcceptGraphResponseFrame(View(terminalFrame), terminalFrame.size(),
        &records, &error, &required) && records.terminal && records.nextResponseSequence == 3,
        L"terminal canonical claims accepted after invalid retries") &&
        Expect(AcceptGraphResponseFrame(View(terminalFrame), terminalFrame.size(),
        &records, &error, &required) && records.nextResponseSequence == 3,
        L"terminal replay is inert") && ok;

    const std::uint16_t surrogatePair[]{0xd800, 0xdc00};
    const Bytes utf16 = codec::Utf16(surrogatePair, 2);
    const Bytes utfFirstPayload = Fragment(0, RecordKind::Property, 1, 1 | 4 | 16,
        ScalarTag::UTF16, utf16.size(), 0, {utf16.data(), 10});
    const Bytes utfFirst = Frame(version, cursor, utfFirstPayload, kFlagFragmented,
                                 0, 0, 60, {});
    GraphFragmentContinuationV1 utfState{};
    ok = Expect(AcceptGraphResponseFrame(View(utfFirst), utfFirst.size(),
        &utfState, &error, &required) && utfState.currentFieldValue.size() == 10,
        L"raw UTF16 boundary preserved") && ok;
    Header utfHeader{};
    static_cast<void>(HeaderOf(utfFirst, &utfHeader));
    const Bytes utfLastPayload = Fragment(0, RecordKind::Property, 1, 1 | 8 | 32,
        ScalarTag::UTF16, utf16.size(), 10, {utf16.data() + 10, 2});
    const Bytes utfLast = Frame(version, cursor, utfLastPayload, 0, 1, 58, 60,
                                utfHeader.chainDigest);
    ok = Expect(AcceptGraphResponseFrame(View(utfLast), utfLast.size(),
        &utfState, &error, &required) && !utfState.active,
        L"raw UTF16 continuation completes") && ok;

    const std::size_t firstValueBytes = static_cast<std::size_t>(kMaximumPayloadBytes - 56);
    Bytes largeValue(firstValueBytes + 2, 0x5a);
    const Bytes largeFirstPayload = Fragment(0, RecordKind::Property, 1, 1 | 4 | 16,
        ScalarTag::Struct, largeValue.size(), 0, {largeValue.data(), firstValueBytes});
    const std::uint64_t largeTotal = 48 + largeValue.size();
    const Bytes largeFirst = Frame(version, cursor, largeFirstPayload, kFlagFragmented,
                                   0, 0, largeTotal, {});
    GraphFragmentContinuationV1 largeState{};
    ok = Expect(largeFirst.size() == kMaximumFrameBytes &&
        AcceptGraphResponseFrame(View(largeFirst), largeFirst.size(),
        &largeState, &error, &required), L">4MiB field first frame") && ok;
    Header largeHeader{};
    static_cast<void>(HeaderOf(largeFirst, &largeHeader));
    const Bytes largeLastPayload = Fragment(0, RecordKind::Property, 1, 1 | 8 | 32,
        ScalarTag::Struct, largeValue.size(), firstValueBytes,
        {largeValue.data() + firstValueBytes, 2});
    const Bytes largeLast = Frame(version, cursor, largeLastPayload, 0, 1,
        48 + firstValueBytes, largeTotal, largeHeader.chainDigest);
    ok = Expect(AcceptGraphResponseFrame(View(largeLast), largeLast.size(),
        &largeState, &error, &required) && !largeState.active,
        L">4MiB field continuation") && ok;
    return ok;
}

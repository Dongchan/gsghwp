#include <Windows.h>

#include "../DocumentGraphIdentity.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace {
using namespace hancom::graph;
using namespace hancom::graph::identity;

bool Expect(const bool condition, const wchar_t* const label) {
    if (!condition) std::wcerr << L"DocumentGraphIdentitySmoke failed: " << label << L'\n';
    return condition;
}
struct CounterSource final { std::uint8_t next = 1; };
bool FillCounter(void* const context, std::uint8_t* const bytes,
                 const std::uint32_t count) noexcept {
    auto* const source = static_cast<CounterSource*>(context);
    for (std::uint32_t index = 0; index != count; ++index)
        bytes[index] = static_cast<std::uint8_t>(source->next + index);
    source->next = static_cast<std::uint8_t>(source->next + 19U);
    return true;
}
UuidSource Source(CounterSource* const source) { return {source, FillCounter}; }
struct ScriptSource final {
    std::vector<Uuid128> values{};
    std::size_t index = 0;
    bool repeatLast = true;
};
bool FillScript(void* const context, std::uint8_t* const bytes,
                const std::uint32_t count) noexcept {
    auto* const source = static_cast<ScriptSource*>(context);
    if (count != 16 || source->values.empty()) return false;
    const std::size_t selected = source->index < source->values.size() ?
        source->index : source->values.size()-1;
    if (source->index < source->values.size() || source->repeatLast) ++source->index;
    else return false;
    std::copy(source->values[selected].bytes.begin(),source->values[selected].bytes.end(),bytes);
    return true;
}
UuidSource Source(ScriptSource* const source) { return {source,FillScript}; }
Uuid128 Id(const std::uint8_t seed) {
    Uuid128 value{};
    for (std::size_t index = 0; index != value.bytes.size(); ++index)
        value.bytes[index] = static_cast<std::uint8_t>(seed + index);
    value.bytes[6] = static_cast<std::uint8_t>((value.bytes[6] & 0x0fU) | 0x40U);
    value.bytes[8] = static_cast<std::uint8_t>((value.bytes[8] & 0x3fU) | 0x80U);
    return value;
}
Sha256 Hash(const std::uint8_t seed) {
    Sha256 value{};
    for (std::size_t index = 0; index != value.bytes.size(); ++index)
        value.bytes[index] = static_cast<std::uint8_t>(seed + index);
    return value;
}
bool SameHash(const Sha256& left, const Sha256& right) { return left.bytes == right.bytes; }
std::uint64_t Read64(const SerializedGraphVersionV1& bytes, const std::size_t offset) {
    std::uint64_t value = 0;
    for (unsigned index = 0; index != 8; ++index)
        value |= static_cast<std::uint64_t>(bytes[offset + index]) << (index * 8);
    return value;
}
GraphVersionV1 Version() {
    GraphVersionV1 value{};
    value.profileBits = CloseProfileBits(ProfileBit(ProfileId::Layout));
    value.semanticCertified = true;
    value.layoutPresent = true;
    value.documentSessionId = Id(1);
    value.graphId = Id(21);
    value.semanticRevision = UINT64_C(0x0102030405060708);
    value.layoutRevision = UINT64_C(0x1112131415161718);
    value.locatorEpoch = UINT64_C(0x2122232425262728);
    value.observedSemanticRoot = Hash(31);
    value.layoutRoot = Hash(71);
    value.captureRoot = Hash(111);
    return value;
}
PublishIdentity Publish(const std::uint8_t semantic, const std::uint8_t layout,
                        const std::uint8_t capture) {
    PublishIdentity value{};
    value.profileBits = CloseProfileBits(ProfileBit(ProfileId::Layout));
    value.semanticCertified = true;
    value.layoutPresent = true;
    value.semanticRoot = Hash(semantic);
    value.layoutRoot = Hash(layout);
    value.captureRoot = Hash(capture);
    return value;
}
bool HasRemap(const IdentityReceipt& receipt, const Uuid128& source,
              const RemapDisposition disposition, const RemapReason reason) {
    return std::any_of(receipt.remaps.begin(), receipt.remaps.end(),
        [&](const RemapEntry& entry) {
            return EqualUuid(entry.source, source) && entry.disposition == disposition &&
                entry.reason == reason;
        });
}
bool HasTombstone(const IdentityReceipt& receipt, const NodeId& node,
                  const TombstoneReason reason) {
    return std::any_of(receipt.tombstones.begin(), receipt.tombstones.end(),
        [&](const TombstoneEntry& entry) {
            return EqualUuid(entry.node, node) && entry.semanticRevision != 0 &&
                entry.reason == reason;
        });
}
bool SameNodes(const std::vector<IdentityNode>& left,
               const std::vector<IdentityNode>& right) {
    if (left.size() != right.size()) return false;
    for (std::size_t index = 0; index != left.size(); ++index) {
        if (!EqualUuid(left[index].id,right[index].id) ||
            !EqualUuid(left[index].parent,right[index].parent) ||
            left[index].kind != right[index].kind ||
            left[index].cell.row1 != right[index].cell.row1 ||
            left[index].cell.column1 != right[index].cell.column1 ||
            left[index].cell.rowSpan != right[index].cell.rowSpan ||
            left[index].cell.columnSpan != right[index].cell.columnSpan) return false;
    }
    return true;
}
bool SameIds(const std::vector<NodeId>& left, const std::vector<NodeId>& right) {
    if (left.size() != right.size()) return false;
    for (std::size_t index = 0; index != left.size(); ++index)
        if (!EqualUuid(left[index],right[index])) return false;
    return true;
}

bool UuidAndSessionSmoke() {
    bool ok = true;
    CounterSource random{};
    Uuid128 first{};
    Uuid128 second{};
    ok &= Expect(MintUuidV4(Source(&random), &first) && MintUuidV4(Source(&random), &second), L"mint UUIDs");
    ok &= Expect(IsRfc4122V4(first) && IsRfc4122V4(second) && !EqualUuid(first, second), L"UUID v4 version variant uniqueness");
    const std::wstring canonical = FormatCanonicalUuid(first);
    Uuid128 parsed{};
    ok &= Expect(canonical.size() == 36 && ParseCanonicalUuid(canonical.c_str(), &parsed) && EqualUuid(first, parsed), L"canonical UUID roundtrip");
    std::wstring upper = canonical;
    std::transform(upper.begin(), upper.end(), upper.begin(), ::towupper);
    ok &= Expect(ParseCanonicalUuid(upper.c_str(), &parsed), L"canonical uppercase UUID accepted");
    Uuid128 bad = first;
    bad.bytes[6] = 0x30;
    ok &= Expect(!IsRfc4122V4(bad) && !ParseCanonicalUuid(L"00000000-0000-0000-0000-000000000000", &parsed) &&
        !ParseCanonicalUuid(L"001122334455-4677-8899-aabbccddeeff", &parsed), L"UUID invalid forms rejected");

    std::wstring pathA;
    std::wstring pathB;
    ok &= Expect(NormalizeDocumentPath(L"C:\\Temp\\folder\\..\\File.HWP", &pathA) &&
        NormalizeDocumentPath(L"c:\\temp\\file.hwp", &pathB) &&
        CompareStringOrdinal(pathA.c_str(), -1, pathB.c_str(), -1, TRUE) == CSTR_EQUAL,
        L"normalized path ordinal comparison");
    std::wstring unsaved = L"bad";
    ok &= Expect(NormalizeDocumentPath(L"", &unsaved) && unsaved.empty(), L"unsaved empty path valid");

    SessionAliasRegistry registry(Source(&random));
    const DocumentLocator locator{44, 55, L"C:\\Temp\\folder\\..\\File.HWP"};
    DocumentSessionId session{};
    ok &= Expect(registry.Open(100, 200, locator, &session) == AliasStatus::Ok && IsRfc4122V4(session), L"open canonical session");
    ok &= Expect(registry.AddAlias(100, 201) == AliasStatus::Ok &&
        registry.AddAlias(100, 201) == AliasStatus::AliasAlreadyBound, L"shared aliases and duplicate guard");
    DocumentSessionId aliasSession{};
    ok &= Expect(registry.Resolve(201, {44,55,L"c:\\temp\\file.hwp"}, &aliasSession) == AliasStatus::Ok &&
        EqualUuid(session, aliasSession), L"all aliases share session");
    ok &= Expect(registry.Resolve(201, {45,55,L"c:\\temp\\file.hwp"}, nullptr) == AliasStatus::LocatorMismatch &&
        registry.Resolve(201, {44,56,L"c:\\temp\\file.hwp"}, nullptr) == AliasStatus::LocatorMismatch &&
        registry.Resolve(201, {44,55,L"c:\\temp\\other.hwp"}, nullptr) == AliasStatus::LocatorMismatch,
        L"DocumentID HWND path are validation only");
    ok &= Expect(registry.Close(100) && registry.Resolve(200, locator, nullptr) == AliasStatus::UnknownAlias &&
        registry.Resolve(201, locator, nullptr) == AliasStatus::UnknownAlias, L"close invalidates all aliases");
    DocumentSessionId restarted{};
    ok &= Expect(registry.Open(101, 202, {0,0,L""}, &restarted) == AliasStatus::Ok,
        L"unsaved session open");
    registry.BridgeRestart();
    ok &= Expect(registry.Resolve(202, {0,0,L""}, nullptr) == AliasStatus::UnknownAlias,
        L"bridge restart invalidates registry");

    ScriptSource sessionRetry{{Id(30),Id(30),Id(31)}};
    SessionAliasRegistry retryRegistry(Source(&sessionRetry));
    DocumentSessionId sessionA{};
    DocumentSessionId sessionB{};
    ok &= Expect(retryRegistry.Open(300,400,{0,0,L""},&sessionA) == AliasStatus::Ok &&
        retryRegistry.Open(301,401,{0,0,L""},&sessionB) == AliasStatus::Ok &&
        EqualUuid(sessionA,Id(30)) && EqualUuid(sessionB,Id(31)),
        L"session collision retries then unique");
    ScriptSource sessionConstant{{Id(40)}};
    SessionAliasRegistry exhaustedRegistry(Source(&sessionConstant));
    DocumentSessionId liveSession{};
    DocumentSessionId untouched = Id(99);
    ok &= Expect(exhaustedRegistry.Open(500,600,{0,0,L""},&liveSession) == AliasStatus::Ok &&
        exhaustedRegistry.Open(501,601,{0,0,L""},&untouched) == AliasStatus::CollisionExhausted &&
        EqualUuid(untouched,Id(99)) && sessionConstant.index == 1+kUuidCollisionAttempts &&
        exhaustedRegistry.Resolve(600,{0,0,L""},&parsed) == AliasStatus::Ok &&
        exhaustedRegistry.Resolve(601,{0,0,L""},nullptr) == AliasStatus::UnknownAlias,
        L"session constant collision exhaustion is typed and atomic");
    return ok;
}

bool VersionSmoke() {
    bool ok = true;
    const GraphVersionV1 base = Version();
    SerializedGraphVersionV1 bytes{};
    ok &= Expect(SerializeGraphVersion(base, &bytes) == GraphVersionError::None && bytes.size() == 168,
        L"exact GraphVersion 168 bytes");
    ok &= Expect(bytes[0] == 1 && bytes[1] == 0 && Read64(bytes,2) == base.profileBits &&
        bytes[10] == 1 && bytes[11] == 1 && bytes[12] == 0 && bytes[15] == 0,
        L"schema profile certification layout reserved offsets");
    ok &= Expect(std::equal(base.documentSessionId.bytes.begin(), base.documentSessionId.bytes.end(), bytes.begin()+16) &&
        std::equal(base.graphId.bytes.begin(), base.graphId.bytes.end(), bytes.begin()+32) &&
        Read64(bytes,48) == base.semanticRevision && Read64(bytes,56) == base.layoutRevision &&
        Read64(bytes,64) == base.locatorEpoch, L"identity revision epoch offsets");
    ok &= Expect(std::equal(base.observedSemanticRoot.bytes.begin(), base.observedSemanticRoot.bytes.end(), bytes.begin()+72) &&
        std::equal(base.layoutRoot.bytes.begin(), base.layoutRoot.bytes.end(), bytes.begin()+104) &&
        std::equal(base.captureRoot.bytes.begin(), base.captureRoot.bytes.end(), bytes.begin()+136),
        L"all root offsets");
    GraphVersionV1 decoded{};
    ok &= Expect(DeserializeGraphVersion(bytes.data(), bytes.size(), &decoded) == GraphVersionError::None &&
        FullVersionCas(base, decoded) && CursorVersionCas(base, decoded) && PatchVersionCas(base, decoded), L"GraphVersion roundtrip and certified CAS");

    std::vector<GraphVersionV1> mismatches;
    auto changed = base; changed.profileBits |= ProfileBit(ProfileId::BinaryContent); mismatches.push_back(changed);
    changed = base; changed.semanticCertified = false; mismatches.push_back(changed);
    changed = base; changed.layoutPresent = false; changed.layoutRoot = {}; mismatches.push_back(changed);
    changed = base; changed.documentSessionId = Id(2); mismatches.push_back(changed);
    changed = base; changed.graphId = Id(22); mismatches.push_back(changed);
    changed = base; ++changed.semanticRevision; mismatches.push_back(changed);
    changed = base; ++changed.layoutRevision; mismatches.push_back(changed);
    changed = base; ++changed.locatorEpoch; mismatches.push_back(changed);
    changed = base; changed.observedSemanticRoot.bytes[0] ^= 1; mismatches.push_back(changed);
    changed = base; changed.layoutRoot.bytes[0] ^= 1; mismatches.push_back(changed);
    changed = base; changed.captureRoot.bytes[0] ^= 1; mismatches.push_back(changed);
    for (const GraphVersionV1& mismatch : mismatches)
        ok &= Expect(!FullVersionCas(base, mismatch) && !CursorVersionCas(base, mismatch) &&
            !PatchVersionCas(base, mismatch), L"cursor and patch full-byte CAS component mismatch");
    changed = base; ++changed.layoutRevision;
    ok &= Expect(!PatchVersionCas(base, changed), L"stale layout-only patch full-CAS failure");
    changed = base; changed.semanticCertified = false;
    ok &= Expect(ValidateGraphVersion(changed) != GraphVersionError::None &&
        !FullVersionCas(changed,changed) && !CursorVersionCas(changed,changed) &&
        !PatchVersionCas(changed,changed),
        L"layout-present version requires semantic certification");
    changed = base; changed.profileBits = ProfileBit(ProfileId::Structure);
    ok &= Expect(ValidateGraphVersion(changed) != GraphVersionError::None &&
        !FullVersionCas(changed,changed) && !CursorVersionCas(changed,changed) &&
        !PatchVersionCas(changed,changed), L"layout-present version requires Layout profile");
    changed = base; changed.layoutRoot = {};
    ok &= Expect(ValidateGraphVersion(changed) != GraphVersionError::None &&
        !FullVersionCas(changed,changed) && !CursorVersionCas(changed,changed) &&
        !PatchVersionCas(changed,changed), L"layout-present version requires nonzero layout root");
    changed = base; changed.layoutPresent = false; changed.layoutRoot = {};
    changed.profileBits = ProfileBit(ProfileId::Structure); changed.semanticCertified = false;
    ok &= Expect(ValidateGraphVersion(changed) == GraphVersionError::None &&
        FullVersionCas(changed,changed) && CursorVersionCas(changed,changed) &&
        !PatchVersionCas(changed,changed),
        L"layout-absent zero-root uncertified version remains valid");

    auto corrupt = bytes;
    corrupt[12] = 1;
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::ReservedNonzero,
        L"reserved bytes rejected");
    corrupt = bytes; corrupt[10] = 2;
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::BadBoolean,
        L"certification bool rejected");
    corrupt = bytes; corrupt[11] = 2;
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::BadBoolean,
        L"layout bool rejected");
    corrupt = bytes; corrupt[0] = 2;
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::BadSchema,
        L"schema rejected");
    corrupt = bytes; corrupt[6] |= 0x80;
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::BadProfiles,
        L"unknown profiles rejected");
    corrupt = bytes; std::fill(corrupt.begin()+16, corrupt.begin()+32, std::uint8_t{0});
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::BadDocumentSessionId,
        L"session UUID rejected");
    corrupt = bytes; std::fill(corrupt.begin()+32, corrupt.begin()+48, std::uint8_t{0});
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::BadGraphId,
        L"graph UUID rejected");
    corrupt = bytes; std::fill(corrupt.begin()+48, corrupt.begin()+56, std::uint8_t{0});
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::BadRevision,
        L"zero revision rejected");
    corrupt = bytes; corrupt[11] = 0;
    ok &= Expect(DeserializeGraphVersion(corrupt.data(), corrupt.size(), &decoded) == GraphVersionError::BadLayoutRoot,
        L"layout absence requires zero root");
    ok &= Expect(DeserializeGraphVersion(bytes.data(), bytes.size()-1, &decoded) == GraphVersionError::BadSize,
        L"GraphVersion size rejected");
    return ok;
}

bool LifecycleSmoke() {
    bool ok = true;
    CounterSource random{};
    GraphLifecycle lifecycle(Source(&random));
    const PublishIdentity initial = Publish(1,41,81);
    PublishIdentity invalidLayoutPublish=initial;
    invalidLayoutPublish.semanticCertified=false;
    ok &= Expect(lifecycle.StartNewLineage(Id(9),invalidLayoutPublish)==LineageResult::InvalidInput,
        L"initial publish rejects uncertified layout-bearing version");
    ok &= Expect(lifecycle.StartNewLineage(Id(1), initial) == LineageResult::Started, L"initial publish");
    GraphVersionV1 current = lifecycle.version();
    ok &= Expect(lifecycle.ApplyPublish(invalidLayoutPublish,PublishOutcome::Success)==
            LifecycleResult::InvalidPublish && FullVersionCas(current,lifecycle.version()),
        L"publish rejects uncertified layout-bearing version atomically");
    ok &= Expect(current.semanticRevision == 1 && current.layoutRevision == 1 && current.locatorEpoch == 1,
        L"initial revisions and epoch one");
    const GraphId firstGraph = current.graphId;
    const std::array<PublishOutcome,6> unchanged{{PublishOutcome::NoOp,PublishOutcome::Failed,
        PublishOutcome::ExactRollback,PublishOutcome::Save,PublishOutcome::CaretOnly,
        PublishOutcome::ModifiedFlagOnly}};
    for (const PublishOutcome event : unchanged) {
        ok &= Expect(lifecycle.ApplyPublish(Publish(9,49,89), event) == LifecycleResult::Unchanged &&
            FullVersionCas(current, lifecycle.version()), L"no increment event table");
    }
    PublishIdentity captureOnly = initial; captureOnly.captureRoot = Hash(82);
    ok &= Expect(lifecycle.ApplyPublish(captureOnly, PublishOutcome::Success) == LifecycleResult::Applied &&
        lifecycle.version().semanticRevision == 1 && lifecycle.version().layoutRevision == 1 &&
        !SameHash(current.captureRoot, lifecycle.version().captureRoot), L"capture-only root no revision increment");
    current = lifecycle.version();
    PublishIdentity semanticOnly = captureOnly; semanticOnly.semanticRoot = Hash(2); semanticOnly.captureRoot = Hash(83);
    ok &= Expect(lifecycle.ApplyPublish(semanticOnly, PublishOutcome::Success) == LifecycleResult::Applied &&
        lifecycle.version().semanticRevision == 2 && lifecycle.version().layoutRevision == 1,
        L"semantic-only exact increment");
    PublishIdentity layoutOnly = semanticOnly; layoutOnly.layoutRoot = Hash(42); layoutOnly.captureRoot = Hash(84);
    ok &= Expect(lifecycle.ApplyPublish(layoutOnly, PublishOutcome::Success) == LifecycleResult::Applied &&
        lifecycle.version().semanticRevision == 2 && lifecycle.version().layoutRevision == 2,
        L"layout-only exact increment");
    PublishIdentity both = layoutOnly; both.semanticRoot = Hash(3); both.layoutRoot = Hash(43);
    ok &= Expect(lifecycle.ApplyPublish(both, PublishOutcome::Success) == LifecycleResult::Applied &&
        lifecycle.version().semanticRevision == 3 && lifecycle.version().layoutRevision == 3,
        L"semantic and layout each increment once");

    const GraphVersionV1 beforeReopen = lifecycle.version();
    PublishIdentity fresh = both; fresh.captureRoot = Hash(200); fresh.layoutRoot = Hash(201);
    ok &= Expect(lifecycle.AuthorizedReopen(Id(2), fresh, true, true) == ReopenResult::Retained,
        L"authorized certified equal-root reopen");
    const GraphVersionV1 reopened = lifecycle.version();
    ok &= Expect(EqualUuid(firstGraph, reopened.graphId) && EqualUuid(reopened.documentSessionId, Id(2)) &&
        reopened.locatorEpoch == beforeReopen.locatorEpoch + 1 &&
        reopened.semanticRevision == beforeReopen.semanticRevision && reopened.layoutRevision == beforeReopen.layoutRevision &&
        SameHash(reopened.captureRoot, beforeReopen.captureRoot) && SameHash(reopened.layoutRoot, beforeReopen.layoutRoot),
        L"reopen retains lineage and changes only session epoch");
    GraphVersionV1 rejectedBase = lifecycle.version();
    fresh.semanticCertified = false;
    ok &= Expect(lifecycle.AuthorizedReopen(Id(3), fresh, true, true) == ReopenResult::RejectedUncertified &&
        FullVersionCas(rejectedBase, lifecycle.version()), L"uncertified reopen rejected atomically");
    fresh.semanticCertified = true; fresh.semanticRoot = Hash(99);
    ok &= Expect(lifecycle.AuthorizedReopen(Id(3), fresh, true, true) == ReopenResult::RejectedDifferentRoot &&
        FullVersionCas(rejectedBase, lifecycle.version()), L"different-root reopen rejected atomically");
    fresh.semanticRoot = reopened.observedSemanticRoot;
    ok &= Expect(lifecycle.AuthorizedReopen(Id(3), fresh, true, false) == ReopenResult::RejectedCanonicalPairing &&
        FullVersionCas(rejectedBase, lifecycle.version()), L"non-exact canonical pairing rejected");

    ok &= Expect(lifecycle.StartNewLineage(Id(4), initial) == LineageResult::Started, L"unauthorized reopen starts new lineage");
    ok &= Expect(!EqualUuid(firstGraph, lifecycle.version().graphId) && lifecycle.version().semanticRevision == 1 &&
        lifecycle.version().layoutRevision == 1 && lifecycle.version().locatorEpoch == 1,
        L"new session/new document resets graph lineage");
    const GraphId secondGraph = lifecycle.version().graphId;
    ok &= Expect(lifecycle.StartNewLineage(Id(5), initial) == LineageResult::Started &&
        !EqualUuid(secondGraph, lifecycle.version().graphId), L"bridge restart creates GraphId");

    std::vector<Uuid128> graphSequence{Id(20)};
    for (unsigned index = 0; index != kUuidCollisionAttempts; ++index) graphSequence.push_back(Id(20));
    graphSequence.push_back(Id(21));
    ScriptSource graphRetry{graphSequence};
    GraphLifecycle collisionLifecycle(Source(&graphRetry));
    ok &= Expect(collisionLifecycle.StartNewLineage(Id(10),initial) == LineageResult::Started,
        L"graph collision lifecycle seed");
    const GraphVersionV1 collisionBefore = collisionLifecycle.version();
    ok &= Expect(collisionLifecycle.StartNewLineage(Id(11),initial) == LineageResult::CollisionExhausted &&
        FullVersionCas(collisionBefore,collisionLifecycle.version()),
        L"GraphId constant collision exhaustion leaves version unchanged");
    ok &= Expect(collisionLifecycle.StartNewLineage(Id(11),initial) == LineageResult::Started &&
        EqualUuid(collisionLifecycle.version().graphId,Id(21)),
        L"GraphId exhaustion recovery uses next unique ID");
    ScriptSource graphSessionRetry{{Id(12),Id(22)}};
    GraphLifecycle graphSessionCollision(Source(&graphSessionRetry));
    ok &= Expect(graphSessionCollision.StartNewLineage(Id(12),initial) == LineageResult::Started &&
        EqualUuid(graphSessionCollision.version().graphId,Id(22)),
        L"GraphId retries collision with session UUID");

    ScriptSource reopenReservationGraphs{{Id(20),Id(21)}};
    GraphLifecycle reopenReservations(Source(&reopenReservationGraphs));
    ok &= Expect(reopenReservations.StartNewLineage(Id(10),initial) == LineageResult::Started &&
        reopenReservations.StartNewLineage(Id(11),initial) == LineageResult::Started &&
        EqualUuid(reopenReservations.version().graphId,Id(21)) &&
        reopenReservations.ReserveIdentityIds({Id(30),Id(31)}),
        L"reopen reservation lifecycle seed");
    const PublishIdentity reservedFresh = initial;
    const auto rejectReservedReopen = [&](const DocumentSessionId& reserved,
                                           const wchar_t* const label) {
        const GraphVersionV1 before = reopenReservations.version();
        return Expect(reopenReservations.AuthorizedReopen(reserved,reservedFresh,true,true) ==
                ReopenResult::RejectedIdentityCollision &&
            FullVersionCas(before,reopenReservations.version()) &&
            ValidateGraphVersion(reopenReservations.version()) == GraphVersionError::None,
            label);
    };
    ok &= rejectReservedReopen(Id(21),
        L"authorized reopen SessionId equal active GraphId rejects atomically");
    ok &= rejectReservedReopen(Id(20),
        L"authorized reopen SessionId equal prior GraphId rejects atomically");
    ok &= rejectReservedReopen(Id(11),
        L"authorized reopen SessionId equal live session rejects atomically");
    ok &= rejectReservedReopen(Id(10),
        L"authorized reopen SessionId equal used lineage ID rejects atomically");
    ok &= rejectReservedReopen(Id(30),
        L"authorized reopen SessionId equal tombstone or node reservation rejects atomically");
    ok &= rejectReservedReopen(Id(31),
        L"authorized reopen SessionId equal client reservation rejects atomically");
    const GraphVersionV1 beforeUniqueReopen = reopenReservations.version();
    ok &= Expect(reopenReservations.AuthorizedReopen(Id(40),reservedFresh,true,true) ==
            ReopenResult::Retained &&
        EqualUuid(reopenReservations.version().documentSessionId,Id(40)) &&
        EqualUuid(reopenReservations.version().graphId,beforeUniqueReopen.graphId) &&
        reopenReservations.version().locatorEpoch == beforeUniqueReopen.locatorEpoch+1 &&
        ValidateGraphVersion(reopenReservations.version()) == GraphVersionError::None,
        L"collide then unique authorized reopen succeeds with valid GraphVersion");

    const auto validUnchanged = [&](const GraphVersionV1& before, const wchar_t* const label) {
        return Expect(ValidateGraphVersion(lifecycle.version()) == GraphVersionError::None &&
            FullVersionCas(before, lifecycle.version()), label);
    };
    GraphVersionV1 boundary = lifecycle.version();
    boundary.semanticRevision = (std::numeric_limits<std::uint64_t>::max)() - 1;
    ok &= Expect(lifecycle.Resume(boundary) == GraphVersionError::None, L"resume semantic MAX-1");
    PublishIdentity boundaryPublish = initial;
    boundaryPublish.semanticRoot = Hash(2);
    ok &= Expect(lifecycle.ApplyPublish(boundaryPublish, PublishOutcome::Success) == LifecycleResult::Applied &&
        lifecycle.version().semanticRevision == (std::numeric_limits<std::uint64_t>::max)(),
        L"semantic MAX-1 to MAX succeeds");
    GraphVersionV1 beforeOverflow = lifecycle.version();
    boundaryPublish.semanticRoot = Hash(3);
    ok &= Expect(lifecycle.ApplyPublish(boundaryPublish, PublishOutcome::Success) == LifecycleResult::CounterOverflow,
        L"semantic MAX rejects");
    ok &= validUnchanged(beforeOverflow, L"semantic overflow atomically unchanged");

    boundary = lifecycle.version();
    boundary.semanticRevision = 9;
    boundary.layoutRevision = (std::numeric_limits<std::uint64_t>::max)() - 1;
    ok &= Expect(lifecycle.Resume(boundary) == GraphVersionError::None, L"resume layout MAX-1");
    boundaryPublish.semanticRoot = boundary.observedSemanticRoot;
    boundaryPublish.layoutRoot = Hash(44);
    ok &= Expect(lifecycle.ApplyPublish(boundaryPublish, PublishOutcome::Success) == LifecycleResult::Applied &&
        lifecycle.version().layoutRevision == (std::numeric_limits<std::uint64_t>::max)(),
        L"layout MAX-1 to MAX succeeds");
    beforeOverflow = lifecycle.version();
    boundaryPublish.layoutRoot = Hash(45);
    ok &= Expect(lifecycle.ApplyPublish(boundaryPublish, PublishOutcome::Success) == LifecycleResult::CounterOverflow,
        L"layout MAX rejects");
    ok &= validUnchanged(beforeOverflow, L"layout overflow atomically unchanged");

    boundary = lifecycle.version();
    boundary.semanticRevision = (std::numeric_limits<std::uint64_t>::max)() - 1;
    boundary.layoutRevision = (std::numeric_limits<std::uint64_t>::max)() - 1;
    ok &= Expect(lifecycle.Resume(boundary) == GraphVersionError::None, L"resume simultaneous MAX-1");
    boundaryPublish.semanticRoot = Hash(4);
    boundaryPublish.layoutRoot = Hash(46);
    ok &= Expect(lifecycle.ApplyPublish(boundaryPublish, PublishOutcome::Success) == LifecycleResult::Applied &&
        lifecycle.version().semanticRevision == (std::numeric_limits<std::uint64_t>::max)() &&
        lifecycle.version().layoutRevision == (std::numeric_limits<std::uint64_t>::max)(),
        L"simultaneous MAX-1 to MAX succeeds");
    boundary = lifecycle.version();
    boundary.semanticRevision = (std::numeric_limits<std::uint64_t>::max)() - 1;
    ok &= Expect(lifecycle.Resume(boundary) == GraphVersionError::None, L"resume mixed simultaneous overflow");
    beforeOverflow = lifecycle.version();
    boundaryPublish.semanticRoot = Hash(5);
    boundaryPublish.layoutRoot = Hash(47);
    ok &= Expect(lifecycle.ApplyPublish(boundaryPublish, PublishOutcome::Success) == LifecycleResult::CounterOverflow,
        L"simultaneous rejects when either counter MAX");
    ok &= validUnchanged(beforeOverflow, L"layout-side simultaneous overflow has no partial change");
    boundary = lifecycle.version();
    boundary.semanticRevision = (std::numeric_limits<std::uint64_t>::max)();
    boundary.layoutRevision = (std::numeric_limits<std::uint64_t>::max)() - 1;
    ok &= Expect(lifecycle.Resume(boundary) == GraphVersionError::None, L"resume converse mixed simultaneous overflow");
    beforeOverflow = lifecycle.version();
    boundaryPublish.semanticRoot = Hash(6);
    boundaryPublish.layoutRoot = Hash(48);
    ok &= Expect(lifecycle.ApplyPublish(boundaryPublish, PublishOutcome::Success) == LifecycleResult::CounterOverflow,
        L"simultaneous rejects when semantic counter MAX");
    ok &= validUnchanged(beforeOverflow, L"semantic-side simultaneous overflow has no partial change");

    boundary = lifecycle.version();
    boundary.semanticRevision = 10;
    boundary.layoutRevision = 11;
    boundary.locatorEpoch = (std::numeric_limits<std::uint64_t>::max)() - 1;
    ok &= Expect(lifecycle.Resume(boundary) == GraphVersionError::None, L"resume locator MAX-1");
    PublishIdentity reopenFresh{};
    reopenFresh.profileBits = boundary.profileBits;
    reopenFresh.semanticCertified = true;
    reopenFresh.layoutPresent = boundary.layoutPresent;
    reopenFresh.semanticRoot = boundary.observedSemanticRoot;
    reopenFresh.layoutRoot = boundary.layoutRoot;
    reopenFresh.captureRoot = boundary.captureRoot;
    ok &= Expect(lifecycle.AuthorizedReopen(Id(6),reopenFresh,true,true) == ReopenResult::Retained &&
        lifecycle.version().locatorEpoch == (std::numeric_limits<std::uint64_t>::max)(),
        L"locator MAX-1 to MAX succeeds");
    beforeOverflow = lifecycle.version();
    ok &= Expect(lifecycle.AuthorizedReopen(Id(7),reopenFresh,true,true) == ReopenResult::RejectedCounterOverflow,
        L"locator MAX rejects");
    ok &= validUnchanged(beforeOverflow, L"locator overflow leaves session epoch and roots unchanged");

    boundary = lifecycle.version();
    boundary.semanticRevision = (std::numeric_limits<std::uint64_t>::max)();
    boundary.layoutRevision = (std::numeric_limits<std::uint64_t>::max)();
    ok &= Expect(lifecycle.Resume(boundary) == GraphVersionError::None, L"resume all counters MAX");
    for (const PublishOutcome event : std::array<PublishOutcome,3>{{
        PublishOutcome::NoOp,PublishOutcome::Failed,PublishOutcome::ExactRollback}}) {
        beforeOverflow = lifecycle.version();
        ok &= Expect(lifecycle.ApplyPublish(Publish(99,199,220),event) == LifecycleResult::Unchanged,
            L"unchanged path at MAX does not attempt increment");
        ok &= validUnchanged(beforeOverflow, L"unchanged path at MAX remains valid and exact");
    }
    return ok;
}

std::vector<IdentityNode> SeedTree() {
    return {
        {Id(1),{},NodeKind::Document,{}},
        {Id(2),Id(1),NodeKind::Paragraph,{}},
        {Id(3),Id(2),NodeKind::CharacterRun,{}},
        {Id(4),Id(2),NodeKind::CharacterRun,{}},
        {Id(5),Id(2),NodeKind::GenericControl,{}},
        {Id(6),Id(2),NodeKind::Table,{}},
        {Id(7),Id(6),NodeKind::TableCell,{1,1,1,1}},
        {Id(8),Id(6),NodeKind::TableCell,{1,2,1,1}},
    };
}
bool MutationSmoke() {
    bool ok = true;
    CounterSource random{100};
    MutationIdentity mutations(Source(&random));
    ok &= Expect(mutations.Seed(SeedTree()), L"seed synthetic identity tree");
    IdentityReceipt receipt{};
    ok &= Expect(mutations.Split(Id(2), 3, &receipt) && receipt.remaps.size() == 3 &&
        HasRemap(receipt, Id(2), RemapDisposition::Retained, RemapReason::Split),
        L"paragraph split start survivor and later new IDs");
    receipt = {};
    ok &= Expect(mutations.Split(Id(3), 2, &receipt) && receipt.remaps.size() == 2 &&
        HasRemap(receipt, Id(3), RemapDisposition::Retained, RemapReason::Split),
        L"run split start survivor");

    CounterSource coalesceRandom{130};
    MutationIdentity coalesce(Source(&coalesceRandom));
    ok &= Expect(coalesce.Seed(SeedTree()), L"coalesce seed");
    CounterSource versionRandom{12};
    GraphLifecycle mutationVersion(Source(&versionRandom));
    ok &= Expect(mutationVersion.StartNewLineage(Id(70),Publish(1,41,81)) == LineageResult::Started,
        L"mutation rejection GraphVersion seed");
    const GraphVersionV1 mutationVersionBefore = mutationVersion.version();
    const auto rejectCoalesce = [&](const std::vector<IdentityNode>& seed,
                                    const std::vector<NodeId>& ids,
                                    const wchar_t* const label) {
        CounterSource rejectedRandom{132};
        MutationIdentity rejected(Source(&rejectedRandom));
        IdentityReceipt rejectedReceipt{};
        if (!rejected.Seed(seed)) return Expect(false,label);
        const std::vector<IdentityNode> nodesBefore = rejected.nodes();
        const std::vector<NodeId> tombstonesBefore = rejected.tombstonedIds();
        return Expect(!rejected.Coalesce(ids,&rejectedReceipt) && rejectedReceipt.remaps.empty() &&
            rejectedReceipt.tombstones.empty() && SameNodes(nodesBefore,rejected.nodes()) &&
            SameIds(tombstonesBefore,rejected.tombstonedIds()) &&
            FullVersionCas(mutationVersionBefore,mutationVersion.version()), label);
    };
    ok &= rejectCoalesce(SeedTree(),{Id(3),Id(3)},L"duplicate coalesce IDs reject atomically");
    ok &= rejectCoalesce(SeedTree(),{Id(4),Id(3)},L"reversed coalesce order rejects atomically");
    ok &= rejectCoalesce(SeedTree(),{Id(3),Id(99)},L"missing coalesce ID rejects atomically");
    ok &= rejectCoalesce(SeedTree(),{Id(2),Id(3)},L"cross-kind coalesce rejects atomically");
    auto crossParentTree = SeedTree();
    crossParentTree.push_back({Id(9),Id(1),NodeKind::Paragraph,{}});
    crossParentTree.push_back({Id(10),Id(9),NodeKind::CharacterRun,{}});
    ok &= rejectCoalesce(crossParentTree,{Id(3),Id(10)},L"cross-parent coalesce rejects atomically");
    CounterSource tombstonedRandom{133};
    MutationIdentity tombstonedCoalesce(Source(&tombstonedRandom));
    ok &= Expect(tombstonedCoalesce.Seed(SeedTree()),L"tombstoned coalesce seed");
    IdentityReceipt deletionReceipt{};
    ok &= Expect(tombstonedCoalesce.DeleteSubtree(Id(4),&deletionReceipt),L"tombstoned coalesce setup");
    const std::vector<IdentityNode> tombstonedNodesBefore = tombstonedCoalesce.nodes();
    const std::vector<NodeId> tombstonedIdsBefore = tombstonedCoalesce.tombstonedIds();
    receipt = {};
    ok &= Expect(!tombstonedCoalesce.Coalesce({Id(3),Id(4)},&receipt) && receipt.remaps.empty() &&
        receipt.tombstones.empty() && SameNodes(tombstonedNodesBefore,tombstonedCoalesce.nodes()) &&
        SameIds(tombstonedIdsBefore,tombstonedCoalesce.tombstonedIds()) &&
        FullVersionCas(mutationVersionBefore,mutationVersion.version()),
        L"tombstoned coalesce ID rejects atomically");
    receipt = {};
    ok &= Expect(coalesce.Coalesce({Id(3),Id(4)}, &receipt) &&
        HasRemap(receipt,Id(3),RemapDisposition::Retained,RemapReason::Coalesce) &&
        HasTombstone(receipt,Id(4),TombstoneReason::Coalesce), L"run coalesce earliest start survives");
    auto paragraphTree = SeedTree();
    paragraphTree.push_back({Id(9),Id(1),NodeKind::Paragraph,{}});
    paragraphTree.push_back({Id(10),Id(9),NodeKind::CharacterRun,{}});
    CounterSource paragraphRandom{135};
    MutationIdentity paragraphCoalesce(Source(&paragraphRandom));
    ok &= Expect(paragraphCoalesce.Seed(paragraphTree), L"paragraph coalesce seed");
    receipt = {};
    ok &= Expect(paragraphCoalesce.Coalesce({Id(2),Id(9)}, &receipt) &&
        HasRemap(receipt,Id(2),RemapDisposition::Retained,RemapReason::Coalesce) &&
        HasTombstone(receipt,Id(9),TombstoneReason::Coalesce) &&
        std::any_of(paragraphCoalesce.nodes().begin(),paragraphCoalesce.nodes().end(),
            [](const IdentityNode& node) {
                return EqualUuid(node.id,Id(10)) && EqualUuid(node.parent,Id(2));
            }), L"paragraph coalesce earliest surviving start ID and reparents children");

    CounterSource moveRandom{140};
    MutationIdentity moving(Source(&moveRandom));
    ok &= Expect(moving.Seed(SeedTree()), L"move seed");
    receipt = {};
    ok &= Expect(moving.MoveSubtree(Id(6),Id(1),&receipt) && receipt.remaps.size() == 3 &&
        HasRemap(receipt,Id(7),RemapDisposition::Retained,RemapReason::Move),
        L"subtree move retains complete subtree");
    receipt = {};
    ok &= Expect(moving.MoveSubtree(Id(2),Id(1),&receipt) && receipt.remaps.size() == 4 &&
        HasRemap(receipt,Id(3),RemapDisposition::Retained,RemapReason::Move),
        L"paragraph move retains paragraph and descendants");
    receipt = {};
    ok &= Expect(moving.Insert(Id(40),Id(2),NodeKind::Paragraph,&receipt) && receipt.remaps.size() == 1 &&
        HasRemap(receipt,Id(40),RemapDisposition::New,RemapReason::ClientInsert) &&
        !EqualUuid(receipt.remaps[0].target,Id(40)) &&
        !moving.Insert(Id(40),Id(2),NodeKind::Paragraph,&receipt),
        L"client UUID maps once to fresh native ID");

    ScriptSource priorClientRetry{{Id(90),Id(80),Id(91)}};
    MutationIdentity priorClientReserved(Source(&priorClientRetry));
    ok &= Expect(priorClientReserved.Seed(SeedTree()),L"prior client reservation seed");
    receipt={};
    ok &= Expect(priorClientReserved.Insert(Id(80),Id(2),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(90)),L"client mapping seed uses scripted NodeId");
    receipt={};
    ok &= Expect(priorClientReserved.Insert(Id(81),Id(2),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(91)),
        L"historical client mapping key is never minted as NodeId");

    ScriptSource receiptSourceRetry{{Id(85),Id(86),Id(85),Id(87)}};
    MutationIdentity receiptSourceReserved(Source(&receiptSourceRetry));
    ok &= Expect(receiptSourceReserved.Seed(SeedTree()),L"receipt source reservation seed");
    receipt={};
    receipt.remaps.push_back({Id(85),false,{},RemapDisposition::Tombstoned,RemapReason::Delete});
    ok &= Expect(receiptSourceReserved.Insert(Id(82),Id(2),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(86)),
        L"current receipt source is never minted as NodeId");
    receipt={};
    ok &= Expect(receiptSourceReserved.Insert(Id(83),Id(2),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(87)),
        L"historical receipt source is never minted as NodeId");

    ScriptSource priorClientConstant{{Id(90),Id(80)}};
    MutationIdentity priorClientExhausted(Source(&priorClientConstant));
    ok &= Expect(priorClientExhausted.Seed(SeedTree()),L"prior client exhaustion seed");
    receipt={};
    ok &= Expect(priorClientExhausted.Insert(Id(80),Id(2),NodeKind::Paragraph,&receipt),
        L"prior client exhaustion mapping seed");
    const std::vector<IdentityNode> priorClientNodesBefore=priorClientExhausted.nodes();
    const std::vector<NodeId> priorClientTombstonesBefore=priorClientExhausted.tombstonedIds();
    receipt={};
    ok &= Expect(!priorClientExhausted.Insert(Id(81),Id(2),NodeKind::Paragraph,&receipt) &&
        priorClientExhausted.lastResult()==MutationResult::CollisionExhausted &&
        priorClientConstant.index==1+kUuidCollisionAttempts &&
        SameNodes(priorClientNodesBefore,priorClientExhausted.nodes()) &&
        SameIds(priorClientTombstonesBefore,priorClientExhausted.tombstonedIds()) &&
        receipt.remaps.empty() && receipt.tombstones.empty(),
        L"prior client collision exhaustion is bounded and atomic");

    ScriptSource reseedRetry{{Id(90),Id(80),Id(90),Id(85),Id(3),Id(91),
        Id(80),Id(90),Id(85),Id(3),Id(91),Id(92),Id(110)}};
    MutationIdentity reseededReservations(Source(&reseedRetry));
    ok &= Expect(reseededReservations.Seed(SeedTree()),L"all-time reseed reservation seed");
    receipt={};
    receipt.remaps.push_back({Id(85),false,{},RemapDisposition::Tombstoned,RemapReason::Delete});
    ok &= Expect(reseededReservations.Insert(Id(80),Id(2),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(90)),
        L"all-time reseed client node and receipt source setup");
    IdentityReceipt reseedDeleteReceipt{};
    ok &= Expect(reseededReservations.DeleteSubtree(Id(3),&reseedDeleteReceipt),
        L"all-time reseed tombstone setup");
    const std::vector<IdentityNode> firstReseedNodes=reseededReservations.nodes();
    ok &= Expect(reseededReservations.Seed(firstReseedNodes),L"second Seed succeeds");
    receipt={};
    ok &= Expect(reseededReservations.Insert(Id(81),Id(2),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(91)) && reseedRetry.index==6,
        L"second Seed preserves client key value receipt source and tombstone reservations");
    const std::vector<IdentityNode> repeatedReseedNodes=reseededReservations.nodes();
    ok &= Expect(reseededReservations.Seed(repeatedReseedNodes) &&
        reseededReservations.Seed(repeatedReseedNodes) && reseededReservations.Seed({}) &&
        reseededReservations.Seed({{Id(100),{},NodeKind::Document,{}},
            {Id(101),Id(100),NodeKind::Paragraph,{}}}),
        L"multiple and empty reseeds preserve all-time reservations");
    receipt={};
    ok &= Expect(reseededReservations.Insert(Id(82),Id(101),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(92)) && reseedRetry.index==12,
        L"multiple empty reseed collide then unique skips every former identity class");
    const std::vector<IdentityNode> beforeFailedSeed=reseededReservations.nodes();
    ok &= Expect(!reseededReservations.Seed({{Id(110),{},NodeKind::Document,{}},
            {Id(110),Id(110),NodeKind::Paragraph,{}}}) &&
        SameNodes(beforeFailedSeed,reseededReservations.nodes()),
        L"failed Seed is atomic and reserves no rejected UUID");
    receipt={};
    ok &= Expect(reseededReservations.Insert(Id(83),Id(101),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(110)),
        L"UUID seen only by failed Seed remains available");

    ScriptSource reseedConstant{{Id(90),Id(80)}};
    MutationIdentity reseedExhausted(Source(&reseedConstant));
    ok &= Expect(reseedExhausted.Seed(SeedTree()),L"reseed exhaustion initial seed");
    receipt={};
    ok &= Expect(reseedExhausted.Insert(Id(80),Id(2),NodeKind::Paragraph,&receipt),
        L"reseed exhaustion client mapping setup");
    IdentityReceipt reseedExhaustDelete{};
    ok &= Expect(reseedExhausted.DeleteSubtree(Id(3),&reseedExhaustDelete),
        L"reseed exhaustion tombstone setup");
    const std::vector<IdentityNode> reseedExhaustLive=reseedExhausted.nodes();
    ok &= Expect(reseedExhausted.Seed(reseedExhaustLive),L"reseed exhaustion second Seed");
    const std::vector<IdentityNode> reseedExhaustBefore=reseedExhausted.nodes();
    receipt={};
    ok &= Expect(!reseedExhausted.Insert(Id(81),Id(2),NodeKind::Paragraph,&receipt) &&
        reseedExhausted.lastResult()==MutationResult::CollisionExhausted &&
        reseedConstant.index==1+kUuidCollisionAttempts &&
        SameNodes(reseedExhaustBefore,reseedExhausted.nodes()) && receipt.remaps.empty() &&
        receipt.tombstones.empty(),
        L"all-time reseed constant collision exhaustion is typed bounded and atomic");

    CounterSource deleteRandom{150};
    MutationIdentity deleting(Source(&deleteRandom), 7);
    ok &= Expect(deleting.Seed(SeedTree()), L"delete seed");
    receipt = {};
    ok &= Expect(deleting.DeleteSubtree(Id(6),&receipt) && receipt.tombstones.size() == 3 &&
        HasTombstone(receipt,Id(6),TombstoneReason::Delete) && HasTombstone(receipt,Id(8),TombstoneReason::Delete) &&
        std::all_of(receipt.tombstones.begin(),receipt.tombstones.end(),[](const TombstoneEntry& entry) {
            return entry.semanticRevision == 7;
        }), L"delete tombstones complete subtree at semantic revision");
    CounterSource paragraphDeleteRandom{155};
    MutationIdentity paragraphDeleting(Source(&paragraphDeleteRandom));
    ok &= Expect(paragraphDeleting.Seed(SeedTree()), L"paragraph delete seed");
    receipt = {};
    ok &= Expect(paragraphDeleting.DeleteSubtree(Id(2),&receipt) && receipt.tombstones.size() == 7,
        L"paragraph deletion tombstones every removed descendant");
    CounterSource reuseRandom{7};
    MutationIdentity noReuse(Source(&reuseRandom));
    ok &= Expect(noReuse.Seed(SeedTree()), L"tombstone reuse seed");
    receipt = {};
    ok &= Expect(noReuse.DeleteSubtree(Id(7),&receipt), L"create tombstone collision");
    receipt = {};
    ok &= Expect(noReuse.Insert(Id(60),Id(1),NodeKind::Paragraph,&receipt) &&
        !EqualUuid(receipt.remaps[0].target,Id(7)), L"mutation mint never reuses tombstone UUID");

    CounterSource cloneRandom{170};
    MutationIdentity cloning(Source(&cloneRandom));
    ok &= Expect(cloning.Seed(SeedTree()), L"clone seed");
    receipt = {};
    ok &= Expect(cloning.CloneSubtree(Id(6),Id(2),&receipt) && receipt.remaps.size() == 3 &&
        std::all_of(receipt.remaps.begin(),receipt.remaps.end(),[](const RemapEntry& entry) {
            return entry.disposition == RemapDisposition::New && entry.reason == RemapReason::Clone &&
                entry.targetPresent && !EqualUuid(entry.source,entry.target);
        }), L"clone assigns entirely new subtree");
    CounterSource paragraphCloneRandom{180};
    MutationIdentity paragraphCloning(Source(&paragraphCloneRandom));
    ok &= Expect(paragraphCloning.Seed(SeedTree()), L"paragraph clone seed");
    receipt = {};
    ok &= Expect(paragraphCloning.CloneSubtree(Id(2),Id(1),&receipt) && receipt.remaps.size() == 7 &&
        std::all_of(receipt.remaps.begin(),receipt.remaps.end(),[](const RemapEntry& entry) {
            return entry.disposition == RemapDisposition::New && entry.reason == RemapReason::Clone;
        }), L"paragraph clone assigns entirely new subtree");

    CounterSource tableRandom{190};
    MutationIdentity table(Source(&tableRandom));
    ok &= Expect(table.Seed(SeedTree()), L"table seed");
    const auto rejectTable = [&](const std::vector<MutationIdentity::CellTransition>& transitions,
                                 const wchar_t* const label) {
        CounterSource rejectedRandom{191};
        MutationIdentity rejected(Source(&rejectedRandom));
        IdentityReceipt rejectedReceipt{};
        if (!rejected.Seed(SeedTree())) return Expect(false,label);
        const std::vector<IdentityNode> nodesBefore = rejected.nodes();
        const std::vector<NodeId> tombstonesBefore = rejected.tombstonedIds();
        return Expect(!rejected.ApplyTableCellTransitions(Id(6),transitions,&rejectedReceipt) &&
            rejectedReceipt.remaps.empty() && rejectedReceipt.tombstones.empty() &&
            SameNodes(nodesBefore,rejected.nodes()) && SameIds(tombstonesBefore,rejected.tombstonedIds()) &&
            FullVersionCas(mutationVersionBefore,mutationVersion.version()),label);
    };
    ok &= rejectTable({{MutationIdentity::CellAction::MergeSurvivor,Id(7),{}, {1,1,1,2}},
        {MutationIdentity::CellAction::MergeAbsorbed,Id(7),{}, {}}},
        L"duplicate merge cell ID rejects atomically");
    ok &= rejectTable({{MutationIdentity::CellAction::Retain,Id(7),{}, {1,1,1,1}},
        {MutationIdentity::CellAction::Delete,Id(7),{}, {}}},
        L"duplicate destructive table ID rejects atomically");
    ok &= rejectTable({{MutationIdentity::CellAction::Delete,Id(7),{}, {}},
        {MutationIdentity::CellAction::Clone,Id(7),{}, {2,1,1,1}}},
        L"deleted table source cannot also be cloned");
    ok &= rejectTable({{MutationIdentity::CellAction::Insert,{},Id(41), {2,1,1,1}},
        {MutationIdentity::CellAction::Insert,{},Id(41), {2,2,1,1}}},
        L"duplicate table client ID rejects before partial insert");
    ok &= rejectTable({{MutationIdentity::CellAction::Delete,Id(99),{}, {}}},
        L"missing table cell ID rejects atomically");
    ok &= rejectTable({{MutationIdentity::CellAction::Retain,Id(7),{}, {1,1,1,2}},
        {MutationIdentity::CellAction::Retain,Id(8),{}, {1,2,1,1}}},
        L"overlapping post-transition physical cells reject atomically");
    ok &= rejectTable({{MutationIdentity::CellAction::Retain,Id(8),{}, {1,2,1,1}},
        {MutationIdentity::CellAction::Retain,Id(7),{}, {1,1,1,1}}},
        L"reversed output transition coordinates reject atomically");
    ok &= rejectTable({{MutationIdentity::CellAction::Retain,Id(7),{}, {1,1,1,1}},
        {MutationIdentity::CellAction::Retain,Id(8),{}, {1,3,1,1}}},
        L"gapped post-transition physical topology rejects atomically");
    ok &= rejectTable({{MutationIdentity::CellAction::Retain,Id(7),{},
            {(std::numeric_limits<std::uint64_t>::max)(),1,2,1}},
        {MutationIdentity::CellAction::Retain,Id(8),{}, {1,2,1,1}}},
        L"overflowing output cell rectangle rejects atomically");
    ok &= rejectTable({{MutationIdentity::CellAction::Retain,Id(3),{}, {1,1,1,1}}},
        L"cross-kind table transition rejects atomically");
    ok &= rejectTable({{MutationIdentity::CellAction::MergeSurvivor,Id(8),{}, {1,1,1,2}},
        {MutationIdentity::CellAction::MergeAbsorbed,Id(7),{}, {}}},
        L"merge survivor must own top-left source coordinate");

    auto crossParentTableTree=SeedTree();
    crossParentTableTree.push_back({Id(9),Id(2),NodeKind::Table,{}});
    crossParentTableTree.push_back({Id(10),Id(9),NodeKind::TableCell,{2,1,1,1}});
    CounterSource crossParentTableRandom{192};
    MutationIdentity crossParentTable(Source(&crossParentTableRandom));
    IdentityReceipt crossParentTableReceipt{};
    ok &= Expect(crossParentTable.Seed(crossParentTableTree) &&
        !crossParentTable.ApplyTableCellTransitions(Id(6),{{
            MutationIdentity::CellAction::Retain,Id(10),{}, {2,1,1,1}}},
            &crossParentTableReceipt) && crossParentTableReceipt.remaps.empty() &&
        crossParentTableReceipt.tombstones.empty(),
        L"cross-parent table transition rejects atomically");

    auto gapMergeTree=SeedTree();
    for (IdentityNode& node : gapMergeTree)
        if (EqualUuid(node.id,Id(8))) node.cell={1,3,1,1};
    CounterSource gapMergeRandom{193};
    MutationIdentity gapMerge(Source(&gapMergeRandom));
    IdentityReceipt gapMergeReceipt{};
    const std::vector<IdentityNode> gapMergeBefore=gapMergeTree;
    ok &= Expect(gapMerge.Seed(gapMergeTree) &&
        !gapMerge.ApplyTableCellTransitions(Id(6),{{
            MutationIdentity::CellAction::MergeSurvivor,Id(7),{}, {1,1,1,3}}, {
            MutationIdentity::CellAction::MergeAbsorbed,Id(8),{}, {}}},&gapMergeReceipt) &&
        gapMergeReceipt.remaps.empty() && gapMergeReceipt.tombstones.empty() &&
        SameNodes(gapMergeBefore,gapMerge.nodes()),
        L"gapped merge affected region rejects atomically");

    CounterSource tombstonedTableRandom{194};
    MutationIdentity tombstonedTable(Source(&tombstonedTableRandom));
    IdentityReceipt tombstonedTableReceipt{};
    ok &= Expect(tombstonedTable.Seed(SeedTree()) &&
        tombstonedTable.DeleteSubtree(Id(7),&tombstonedTableReceipt),
        L"tombstoned table transition setup");
    const std::vector<IdentityNode> tombstonedTableBefore=tombstonedTable.nodes();
    const std::vector<NodeId> tombstonedTableIdsBefore=tombstonedTable.tombstonedIds();
    tombstonedTableReceipt={};
    ok &= Expect(!tombstonedTable.ApplyTableCellTransitions(Id(6),{{
            MutationIdentity::CellAction::Retain,Id(7),{}, {1,1,1,1}}},
            &tombstonedTableReceipt) && tombstonedTableReceipt.remaps.empty() &&
        tombstonedTableReceipt.tombstones.empty() &&
        SameNodes(tombstonedTableBefore,tombstonedTable.nodes()) &&
        SameIds(tombstonedTableIdsBefore,tombstonedTable.tombstonedIds()),
        L"tombstoned table transition rejects atomically");

    auto mergedTree = SeedTree();
    mergedTree.erase(std::remove_if(mergedTree.begin(),mergedTree.end(),[](const IdentityNode& node) {
        return EqualUuid(node.id,Id(8));
    }),mergedTree.end());
    for (IdentityNode& node : mergedTree)
        if (EqualUuid(node.id,Id(7))) node.cell = {1,1,2,2};
    const auto rejectSplitTable = [&](const std::vector<MutationIdentity::CellTransition>& transitions,
                                      const wchar_t* const label) {
        CounterSource rejectedRandom{193};
        MutationIdentity rejected(Source(&rejectedRandom));
        IdentityReceipt rejectedReceipt{};
        if (!rejected.Seed(mergedTree)) return Expect(false,label);
        const std::vector<IdentityNode> nodesBefore = rejected.nodes();
        const std::vector<NodeId> tombstonesBefore = rejected.tombstonedIds();
        return Expect(!rejected.ApplyTableCellTransitions(Id(6),transitions,&rejectedReceipt) &&
            rejectedRandom.next == 193 && rejectedReceipt.remaps.empty() &&
            rejectedReceipt.tombstones.empty() && SameNodes(nodesBefore,rejected.nodes()) &&
            SameIds(tombstonesBefore,rejected.tombstonedIds()) &&
            FullVersionCas(mutationVersionBefore,mutationVersion.version()),label);
    };
    const auto topLeft = MutationIdentity::CellTransition{
        MutationIdentity::CellAction::SplitTopLeft,Id(7),{}, {1,1,1,1}};
    ok &= rejectSplitTable({topLeft,
        {MutationIdentity::CellAction::SplitNew,Id(7),Id(50), {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),Id(50), {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,2}}},
        L"exact duplicate SplitNew verifier input rejects atomically");
    ok &= rejectSplitTable({topLeft,
        {MutationIdentity::CellAction::SplitNew,Id(7),Id(50), {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),Id(51), {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,2}}},
        L"duplicate split coordinates with differing metadata reject");
    ok &= rejectSplitTable({
        {MutationIdentity::CellAction::SplitTopLeft,Id(7),{}, {1,1,1,2}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,2}}},
        L"overlapping split outputs reject atomically");
    ok &= rejectSplitTable({topLeft,
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,1}}},
        L"gapped split outputs reject atomically");
    ok &= rejectSplitTable({topLeft,
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {3,2,1,1}}},
        L"out-of-bounds split output rejects atomically");
    ok &= rejectSplitTable({topLeft,
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,2,1,1}}},
        L"noncanonical split output order rejects atomically");
    ok &= rejectSplitTable({
        {MutationIdentity::CellAction::SplitTopLeft,Id(7),{}, {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {1,1,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,2}}},
        L"split survivor must own old top-left");
    CounterSource multiSplitRandom{194};
    MutationIdentity multiSplit(Source(&multiSplitRandom));
    ok &= Expect(multiSplit.Seed(mergedTree),L"valid multi-cell split seed");
    receipt = {};
    ok &= Expect(multiSplit.ApplyTableCellTransitions(Id(6),{
        topLeft,
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,2,1,1}}},&receipt) &&
        receipt.remaps.size() == 4 && multiSplit.nodes().size() == mergedTree.size()+3,
        L"valid canonical nonoverlapping exact-cover multi-cell split");
    CounterSource splitRejectRandom{192};
    MutationIdentity splitReject(Source(&splitRejectRandom));
    ok &= Expect(splitReject.Seed(SeedTree()),L"split negative seed");
    const std::vector<IdentityNode> splitNodesBefore = splitReject.nodes();
    receipt = {};
    ok &= Expect(!splitReject.Split(Id(99),2,&receipt) && receipt.remaps.empty() &&
        SameNodes(splitNodesBefore,splitReject.nodes()),L"missing split source rejects atomically");
    ok &= Expect(!splitReject.Split(Id(3),1,&receipt) && receipt.remaps.empty() &&
        SameNodes(splitNodesBefore,splitReject.nodes()),L"invalid split count rejects atomically");
    IdentityReceipt splitDeletion{};
    ok &= Expect(splitReject.DeleteSubtree(Id(3),&splitDeletion),L"tombstoned split setup");
    const std::vector<IdentityNode> splitTombstonedBefore = splitReject.nodes();
    const std::vector<NodeId> splitTombstonesBefore = splitReject.tombstonedIds();
    receipt = {};
    ok &= Expect(!splitReject.Split(Id(3),2,&receipt) && receipt.remaps.empty() &&
        SameNodes(splitTombstonedBefore,splitReject.nodes()) &&
        SameIds(splitTombstonesBefore,splitReject.tombstonedIds()),
        L"tombstoned split source rejects atomically");
    receipt = {};
    const std::vector<MutationIdentity::CellTransition> rowColumnInsert{{
        MutationIdentity::CellAction::Insert,{},Id(41), {1,1,1,1}}, {
        MutationIdentity::CellAction::Insert,{},Id(42), {1,2,1,1}}, {
        MutationIdentity::CellAction::Retain,Id(7),{}, {2,1,1,1}}, {
        MutationIdentity::CellAction::Retain,Id(8),{}, {2,2,1,1}}};
    ok &= Expect(table.ApplyTableCellTransitions(Id(6),rowColumnInsert,&receipt) &&
        HasRemap(receipt,Id(7),RemapDisposition::Retained,RemapReason::IdentityRetained) &&
        HasRemap(receipt,Id(41),RemapDisposition::New,RemapReason::ClientInsert),
        L"row/column insertion explicit coordinate map retains old creates inserted");
    receipt = {};
    const std::vector<MutationIdentity::CellTransition> merge{{
        MutationIdentity::CellAction::MergeSurvivor,Id(7),{}, {2,1,1,2}}, {
        MutationIdentity::CellAction::MergeAbsorbed,Id(8),{}, {}}};
    ok &= Expect(table.ApplyTableCellTransitions(Id(6),merge,&receipt) &&
        HasRemap(receipt,Id(7),RemapDisposition::Retained,RemapReason::Merge) &&
        HasTombstone(receipt,Id(8),TombstoneReason::MergeAbsorbed),
        L"merge top-left owner survives absorbed tombstoned");
    receipt = {};
    const std::vector<MutationIdentity::CellTransition> split{{
        MutationIdentity::CellAction::SplitTopLeft,Id(7),{}, {2,1,1,1}}, {
        MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,2,1,1}}, {
        MutationIdentity::CellAction::Clone,Id(7),{}, {3,1,1,2}}};
    ok &= Expect(table.ApplyTableCellTransitions(Id(6),split,&receipt) &&
        HasRemap(receipt,Id(7),RemapDisposition::Retained,RemapReason::TableCellSplit) &&
        std::count_if(receipt.remaps.begin(),receipt.remaps.end(),[](const RemapEntry& entry) {
            return entry.disposition == RemapDisposition::New;
        }) == 2, L"cell split top-left survives and clone/new cells mint IDs");
    receipt = {};
    ok &= Expect(table.ApplyTableCellTransitions(Id(6),{{MutationIdentity::CellAction::Delete,
        receipt.remaps.empty() ? table.nodes().back().id : Id(0),{}, {}}},&receipt) &&
        receipt.tombstones.size() == 1, L"row/column deletion tombstones removed cell");

    const auto unchangedMutation = [&](const MutationIdentity& value,
                                       const std::vector<IdentityNode>& beforeNodes,
                                       const std::vector<NodeId>& beforeTombstones,
                                       const IdentityReceipt& valueReceipt) {
        return SameNodes(beforeNodes,value.nodes()) &&
            SameIds(beforeTombstones,value.tombstonedIds()) &&
            valueReceipt.remaps.empty() && valueReceipt.tombstones.empty() &&
            FullVersionCas(mutationVersionBefore,mutationVersion.version());
    };
    ScriptSource insertConstant{{Id(3)}};
    MutationIdentity insertExhausted(Source(&insertConstant));
    ok &= Expect(insertExhausted.Seed(SeedTree()),L"insert collision seed");
    auto beforeNodes = insertExhausted.nodes(); auto beforeTombstones = insertExhausted.tombstonedIds();
    receipt = {};
    ok &= Expect(!insertExhausted.Insert(Id(80),Id(2),NodeKind::Paragraph,&receipt) &&
        unchangedMutation(insertExhausted,beforeNodes,beforeTombstones,receipt),
        L"generic insert collision exhaustion atomic");
    ScriptSource splitConstant{{Id(90),Id(3)}};
    MutationIdentity splitExhausted(Source(&splitConstant));
    ok &= Expect(splitExhausted.Seed(SeedTree()),L"split collision seed");
    beforeNodes=splitExhausted.nodes(); beforeTombstones=splitExhausted.tombstonedIds(); receipt={};
    ok &= Expect(!splitExhausted.Split(Id(3),3,&receipt) &&
        unchangedMutation(splitExhausted,beforeNodes,beforeTombstones,receipt),
        L"run split collision exhaustion has no first-piece side effect");
    std::vector<Uuid128> stagedSequence{Id(90)};
    for (unsigned attempt=0; attempt!=kUuidCollisionAttempts; ++attempt)
        stagedSequence.push_back(Id(3));
    stagedSequence.push_back(Id(90));
    stagedSequence.push_back(Id(91));
    ScriptSource stagedHistorySource{stagedSequence};
    MutationIdentity stagedHistory(Source(&stagedHistorySource));
    ok &= Expect(stagedHistory.Seed(SeedTree()),L"staged all-time reservation seed");
    beforeNodes=stagedHistory.nodes(); beforeTombstones=stagedHistory.tombstonedIds(); receipt={};
    ok &= Expect(!stagedHistory.Split(Id(3),3,&receipt) &&
        stagedHistory.lastResult()==MutationResult::CollisionExhausted &&
        unchangedMutation(stagedHistory,beforeNodes,beforeTombstones,receipt),
        L"failed multi-mint has no partial live mutation");
    receipt={};
    ok &= Expect(stagedHistory.Insert(Id(86),Id(2),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.back().target,Id(91)) && stagedHistorySource.index==19,
        L"minted staged UUID from failed operation remains reserved all-time");
    ScriptSource cloneConstant{{Id(90),Id(3)}};
    MutationIdentity cloneExhausted(Source(&cloneConstant));
    ok &= Expect(cloneExhausted.Seed(SeedTree()),L"clone collision seed");
    beforeNodes=cloneExhausted.nodes(); beforeTombstones=cloneExhausted.tombstonedIds(); receipt={};
    ok &= Expect(!cloneExhausted.CloneSubtree(Id(6),Id(2),&receipt) &&
        unchangedMutation(cloneExhausted,beforeNodes,beforeTombstones,receipt),
        L"clone collision exhaustion has no partial subtree");
    ScriptSource tableInsertConstant{{Id(7)}};
    MutationIdentity tableInsertExhausted(Source(&tableInsertConstant));
    ok &= Expect(tableInsertExhausted.Seed(SeedTree()),L"table client insert collision seed");
    beforeNodes=tableInsertExhausted.nodes(); beforeTombstones=tableInsertExhausted.tombstonedIds(); receipt={};
    ok &= Expect(!tableInsertExhausted.ApplyTableCellTransitions(Id(6),{{
        MutationIdentity::CellAction::Insert,{},Id(81),{2,1,1,2}}},&receipt) &&
        unchangedMutation(tableInsertExhausted,beforeNodes,beforeTombstones,receipt),
        L"table client mapping collision exhaustion atomic");
    ScriptSource tableSplitConstant{{Id(90),Id(7)}};
    MutationIdentity tableSplitExhausted(Source(&tableSplitConstant));
    ok &= Expect(tableSplitExhausted.Seed(mergedTree),L"table split collision seed");
    beforeNodes=tableSplitExhausted.nodes(); beforeTombstones=tableSplitExhausted.tombstonedIds(); receipt={};
    ok &= Expect(!tableSplitExhausted.ApplyTableCellTransitions(Id(6),{
        topLeft,{MutationIdentity::CellAction::SplitNew,Id(7),{}, {1,2,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,1,1,1}},
        {MutationIdentity::CellAction::SplitNew,Id(7),{}, {2,2,1,1}}},&receipt) &&
        unchangedMutation(tableSplitExhausted,beforeNodes,beforeTombstones,receipt),
        L"table split collision exhaustion mints no partial outputs");
    ScriptSource reservedRetry{{Id(90),Id(91)}};
    MutationIdentity reservedMutation(Source(&reservedRetry));
    ok &= Expect(reservedMutation.ReserveIdentityIds({Id(90),Id(92)}) &&
        reservedMutation.Seed(SeedTree()),L"graph and session UUID reservations");
    receipt={};
    ok &= Expect(reservedMutation.Insert(Id(82),Id(2),NodeKind::Paragraph,&receipt) &&
        EqualUuid(receipt.remaps.front().target,Id(91)),
        L"NodeId collides with reserved graph ID then succeeds uniquely");
    ScriptSource receiptRetry{{Id(93),Id(94)}};
    MutationIdentity receiptReserved(Source(&receiptRetry));
    ok &= Expect(receiptReserved.Seed(SeedTree()),L"receipt reservation seed");
    receipt={};
    receipt.remaps.push_back({Id(83),true,Id(93),RemapDisposition::New,RemapReason::ClientInsert});
    ok &= Expect(receiptReserved.Insert(Id(84),Id(2),NodeKind::Paragraph,&receipt) &&
        receipt.remaps.size()==2 && EqualUuid(receipt.remaps.back().target,Id(94)),
        L"NodeId retries ID already reserved by current receipt");
    return ok;
}

ReconcileCandidate Candidate(const NodeId id, const NodeKind kind,
    const std::uint64_t ordinal, const std::uint8_t fingerprint,
    const wchar_t* const primary = L"", const wchar_t* const secondary = L"",
    const std::uint64_t partition = 0) {
    ReconcileCandidate value{};
    value.id=id; value.kind=kind; value.ordinal=ordinal; value.fingerprint=Hash(fingerprint);
    value.primaryKey=primary; value.secondaryKey=secondary; value.partition=partition;
    value.canonicalPayload=Hash(static_cast<std::uint8_t>(fingerprint+90U));
    return value;
}
bool ReconciliationSmoke() {
    bool ok = true;
    CounterSource random{20};
    ReconcileResult result{};
    const NodeId parent=Id(1);
    auto oldControl=Candidate(Id(2),NodeKind::GenericControl,0,1,L"tbl",L"instance-1");
    auto newControl=oldControl; newControl.id={};
    ok &= Expect(ReconcileChildren(parent,{oldControl},{newControl},ReconcileMode::External,
        Source(&random),{},&result) && result.nodes.size()==1 && result.nodes[0].retained &&
        EqualUuid(result.nodes[0].id,Id(2)), L"external unique control CtrlID instance match");
    auto oldStory=Candidate(Id(3),NodeKind::Story,0,2,L"owner-2",L"Header");
    auto newStory=oldStory; newStory.id={};
    ok &= Expect(ReconcileChildren(parent,{oldStory},{newStory},ReconcileMode::External,
        Source(&random),{},&result) && result.nodes[0].retained, L"external unique story owner role match");
    auto oldCell=Candidate(Id(4),NodeKind::TableCell,0,3,L"list-77",L"A1");
    auto newCell=oldCell; newCell.id={}; newCell.secondaryKey=L"B2";
    ok &= Expect(ReconcileChildren(parent,{oldCell},{newCell},ReconcileMode::External,
        Source(&random),{},&result) && result.nodes[0].retained, L"external cell unique list ID match");
    oldCell.primaryKey.clear(); newCell.primaryKey.clear(); oldCell.topologyUnchanged=true; newCell.topologyUnchanged=true;
    newCell.secondaryKey=L"A1";
    ok &= Expect(ReconcileChildren(parent,{oldCell},{newCell},ReconcileMode::External,
        Source(&random),{},&result) && result.nodes[0].retained, L"cell address fallback only unchanged topology");
    newCell.topologyUnchanged=false;
    ok &= Expect(ReconcileChildren(parent,{oldCell},{newCell},ReconcileMode::External,
        Source(&random),{},&result) && !result.nodes[0].retained, L"cell address rejected after topology change");

    auto oldParagraph=Candidate(Id(5),NodeKind::Paragraph,0,8,L"",L"",7);
    auto newParagraph=oldParagraph; newParagraph.id={};
    ok &= Expect(ReconcileChildren(parent,{oldParagraph},{newParagraph},ReconcileMode::External,
        Source(&random),{},&result) && result.nodes[0].retained, L"paragraph unique exact fingerprint in partition");
    auto oldRun=Candidate(Id(6),NodeKind::CharacterRun,0,9,L"",L"",8);
    auto newRun=oldRun; newRun.id={};
    ok &= Expect(ReconcileChildren(parent,{oldRun},{newRun},ReconcileMode::External,
        Source(&random),{},&result) && result.nodes[0].retained, L"run unique exact fingerprint in partition");
    auto changedGap=oldParagraph; changedGap.id={}; changedGap.fingerprint=Hash(77);
    ok &= Expect(ReconcileChildren(parent,{oldParagraph},{changedGap},ReconcileMode::External,
        Source(&random),{},&result) && result.nodes[0].retained, L"one-old one-new bounded gap exception");

    auto duplicateOld=oldParagraph; duplicateOld.id=Id(7);
    auto duplicateFresh=newParagraph;
    ok &= Expect(ReconcileChildren(parent,{oldParagraph,duplicateOld},{newParagraph,duplicateFresh},
        ReconcileMode::External,Source(&random),{},&result) &&
        std::all_of(result.nodes.begin(),result.nodes.end(),[](const ReconciledNode& node){return !node.retained;}) &&
        std::count_if(result.receipt.remaps.begin(),result.receipt.remaps.end(),[](const RemapEntry& entry){
            return entry.disposition==RemapDisposition::Ambiguous;
        })==2, L"duplicate paragraphs get new IDs and RemapAmbiguous");
    auto duplicateControl=oldControl; duplicateControl.id=Id(8);
    ok &= Expect(ReconcileChildren(parent,{oldControl,duplicateControl},{newControl},ReconcileMode::External,
        Source(&random),{},&result) && !result.nodes[0].retained &&
        HasRemap(result.receipt,Id(2),RemapDisposition::Ambiguous,RemapReason::Ambiguous),
        L"duplicate controls never arbitrary match");
    auto duplicateStory=oldStory; duplicateStory.id=Id(9);
    ok &= Expect(ReconcileChildren(parent,{oldStory,duplicateStory},{newStory},ReconcileMode::External,
        Source(&random),{},&result) && !result.nodes[0].retained, L"duplicate stories ambiguous");
    auto duplicateCell=oldCell; duplicateCell.id=Id(10);
    ok &= Expect(ReconcileChildren(parent,{oldCell,duplicateCell},{newCell},ReconcileMode::External,
        Source(&random),{},&result) && !result.nodes[0].retained, L"duplicate cells ambiguous");

    auto ordinalA=Candidate(Id(11),NodeKind::Paragraph,0,12); auto ordinalB=Candidate(Id(12),NodeKind::Paragraph,1,13);
    auto freshA=ordinalA; freshA.id=Id(51); auto freshB=ordinalB; freshB.id=Id(52);
    const auto rejectAuthorizedPairing = [&](const std::vector<ReconcileCandidate>& oldNodes,
        const std::vector<ReconcileCandidate>& newNodes,
        const std::vector<NodeId>& tombstones, const wchar_t* const label) {
        ScriptSource unusedSource{{Id(200)}};
        ReconcileResult unchanged{};
        unchanged.nodes.push_back({99,Id(99),true});
        unchanged.receipt.remaps.push_back(
            {Id(98),true,Id(97),RemapDisposition::Retained,RemapReason::IdentityRetained});
        return Expect(!ReconcileChildren(parent,oldNodes,newNodes,
                ReconcileMode::AuthorizedEqualRoot,Source(&unusedSource),tombstones,&unchanged) &&
            unusedSource.index==0 && unchanged.nodes.size()==1 &&
            unchanged.nodes[0].freshIndex==99 && EqualUuid(unchanged.nodes[0].id,Id(99)) &&
            unchanged.receipt.remaps.size()==1 &&
            EqualUuid(unchanged.receipt.remaps[0].source,Id(98)),label);
    };
    auto duplicateOrdinal=ordinalB; duplicateOrdinal.ordinal=0;
    auto duplicateOrdinalFresh=duplicateOrdinal; duplicateOrdinalFresh.id=Id(53);
    ok &= rejectAuthorizedPairing({ordinalA,duplicateOrdinal},
        {freshA,duplicateOrdinalFresh},{},
        L"authorized equal-root duplicate ordinal in one partition rejects atomically");
    auto duplicateOldId=ordinalB; duplicateOldId.id=ordinalA.id;
    auto duplicateOldFresh=duplicateOldId; duplicateOldFresh.id=Id(54);
    ok &= rejectAuthorizedPairing({ordinalA,duplicateOldId},{freshA,duplicateOldFresh},{},
        L"authorized equal-root duplicate old pair ID rejects atomically");
    auto identifiedFreshA=freshA; identifiedFreshA.id=Id(50);
    auto identifiedFreshB=freshB; identifiedFreshB.id=Id(50);
    ok &= rejectAuthorizedPairing({ordinalA,ordinalB},{identifiedFreshA,identifiedFreshB},{},
        L"authorized equal-root duplicate nonzero new pair ID rejects atomically");
    ok &= rejectAuthorizedPairing({ordinalB,ordinalA},{freshB,freshA},{},
        L"authorized equal-root reversed canonical pairing rejects atomically");
    auto crossParentFresh=freshA; crossParentFresh.partition=1;
    ok &= rejectAuthorizedPairing({ordinalA},{crossParentFresh},{},
        L"authorized equal-root cross-parent partition rejects atomically");
    auto crossKindFresh=freshA; crossKindFresh.kind=NodeKind::CharacterRun;
    ok &= rejectAuthorizedPairing({ordinalA},{crossKindFresh},{},
        L"authorized equal-root cross-kind pairing rejects atomically");
    auto missingOld=ordinalA; missingOld.id={};
    ok &= rejectAuthorizedPairing({missingOld},{freshA},{},
        L"authorized equal-root missing old identity rejects atomically");
    auto missingFresh=freshA; missingFresh.id={};
    ok &= rejectAuthorizedPairing({ordinalA},{missingFresh},{},
        L"authorized equal-root missing new identity rejects atomically");
    ok &= rejectAuthorizedPairing({ordinalA},{freshA},{ordinalA.id},
        L"authorized equal-root tombstoned old identity rejects atomically");
    ok &= rejectAuthorizedPairing({ordinalA,ordinalB},{freshA},{},
        L"authorized equal-root cardinality mismatch rejects atomically");
    auto parentAliasedFresh=freshA; parentAliasedFresh.id=parent;
    ok &= rejectAuthorizedPairing({ordinalA},{parentAliasedFresh},{},
        L"authorized equal-root fresh child equal mapped parent rejects atomically");
    auto ownOldAliasedFresh=freshA; ownOldAliasedFresh.id=ordinalA.id;
    ok &= rejectAuthorizedPairing({ordinalA},{ownOldAliasedFresh},{},
        L"authorized equal-root fresh child equal own old ID rejects atomically");
    auto earlierOldAliasedFreshB=freshB; earlierOldAliasedFreshB.id=ordinalA.id;
    ok &= rejectAuthorizedPairing({ordinalA,ordinalB},{freshA,earlierOldAliasedFreshB},{},
        L"authorized equal-root fresh ID equal earlier old ID rejects atomically");
    auto laterOldAliasedFreshA=freshA; laterOldAliasedFreshA.id=ordinalB.id;
    ok &= rejectAuthorizedPairing({ordinalA,ordinalB},{laterOldAliasedFreshA,freshB},{},
        L"authorized equal-root fresh ID equal later old ID rejects atomically");
    auto swappedOldFreshA=freshA; swappedOldFreshA.id=ordinalB.id;
    auto swappedOldFreshB=freshB; swappedOldFreshB.id=ordinalA.id;
    ok &= rejectAuthorizedPairing({ordinalA,ordinalB},{swappedOldFreshA,swappedOldFreshB},{},
        L"authorized equal-root cross-set swapped aliases reject atomically");
    auto ancestorOld=ordinalA; ancestorOld.mappedAuthorityIds={Id(70)};
    auto ancestorAliasedFresh=freshA;
    ancestorAliasedFresh.mappedAuthorityIds={Id(70)};
    ancestorAliasedFresh.id=Id(70);
    ok &= rejectAuthorizedPairing({ancestorOld},{ancestorAliasedFresh},{},
        L"authorized equal-root fresh child equal mapped ancestor authority rejects atomically");
    auto mismatchedAuthorityFresh=freshA; mismatchedAuthorityFresh.mappedAuthorityIds={Id(71)};
    ok &= rejectAuthorizedPairing({ancestorOld},{mismatchedAuthorityFresh},{},
        L"authorized equal-root mismatched mapped authority chain rejects atomically");

    ok &= Expect(ReconcileChildren(parent,{ordinalA,ordinalB},{freshA,freshB},
        ReconcileMode::AuthorizedEqualRoot,Source(&random),{},&result) &&
        result.nodes[0].retained && result.nodes[1].retained &&
        HasRemap(result.receipt,Id(11),RemapDisposition::Retained,RemapReason::SaveReopenEqual),
        L"authorized equal-root exact canonical multi-pair ordinal pairing");
    auto partitionA=ordinalA; partitionA.partition=1;
    auto partitionB=ordinalB; partitionB.partition=2; partitionB.ordinal=0;
    auto partitionFreshA=partitionA; partitionFreshA.id=Id(61);
    auto partitionFreshB=partitionB; partitionFreshB.id=Id(62);
    ok &= Expect(ReconcileChildren(parent,{partitionA,partitionB},
        {partitionFreshA,partitionFreshB},ReconcileMode::AuthorizedEqualRoot,
        Source(&random),{},&result) && result.nodes.size()==2 &&
        result.nodes[0].retained && result.nodes[1].retained,
        L"same ordinal in different legal parent partitions is canonical");
    auto authorizedOld=ordinalA; authorizedOld.mappedAuthorityIds={Id(70),Id(71)};
    auto authorizedFresh=freshA; authorizedFresh.mappedAuthorityIds={Id(70),Id(71)};
    ok &= Expect(ReconcileChildren(parent,{authorizedOld},{authorizedFresh},
        ReconcileMode::AuthorizedEqualRoot,Source(&random),{},&result) &&
        result.nodes.size()==1 && result.nodes[0].retained,
        L"disjoint child and mapped ancestor identities pair canonically");
    freshB.canonicalPayload=Hash(250);
    ok &= rejectAuthorizedPairing({ordinalA,ordinalB},{freshA,freshB},{},
        L"equal-root ordinal payload mismatch rejected atomically");

    const NodeId forbidden=Id(20);
    CounterSource collision{20}; // First minted UUID is exactly Id(20).
    ok &= Expect(ReconcileChildren(parent,{}, {Candidate({},NodeKind::Paragraph,0,1)},
        ReconcileMode::External,Source(&collision),{forbidden},&result) &&
        !EqualUuid(result.nodes[0].id,forbidden) && IsRfc4122V4(result.nodes[0].id),
        L"tombstone IDs never reused");
    ScriptSource externalConstant{{Id(92),parent}};
    ReconcileResult untouchedResult{};
    untouchedResult.nodes.push_back({99,Id(99),true});
    ok &= Expect(!ReconcileChildren(parent,{}, {Candidate({},NodeKind::Paragraph,0,2),
        Candidate({},NodeKind::Paragraph,1,3)},ReconcileMode::External,
        Source(&externalConstant),{Id(90),Id(91)},&untouchedResult) &&
        untouchedResult.nodes.size()==1 && untouchedResult.nodes[0].freshIndex==99 &&
        EqualUuid(untouchedResult.nodes[0].id,Id(99)) && untouchedResult.receipt.remaps.empty(),
        L"external reconciliation constant collision exhaustion is atomic");
    ScriptSource externalRetry{{parent,Id(90),Id(92)}};
    ReconcileResult externalUnique{};
    ok &= Expect(ReconcileChildren(parent,{}, {Candidate({},NodeKind::Paragraph,0,3)},
        ReconcileMode::External,Source(&externalRetry),{Id(90),Id(91)},&externalUnique) &&
        externalUnique.nodes.size()==1 && EqualUuid(externalUnique.nodes[0].id,Id(92)),
        L"external reconciliation rejects parent graph session IDs then unique");
    return ok;
}

} // namespace

bool DocumentGraphIdentitySmoke() {
    const bool uuid = UuidAndSessionSmoke();
    const bool version = VersionSmoke();
    const bool lifecycle = LifecycleSmoke();
    const bool mutation = MutationSmoke();
    const bool reconciliation = ReconciliationSmoke();
    const bool passed = uuid && version && lifecycle && mutation && reconciliation;
    std::wcout << L"IDENTITY_TABLES uuid=" << uuid << L" version=" << version
        << L" lifecycle=" << lifecycle << L" mutation=" << mutation
        << L" reconciliation=" << reconciliation << L'\n';
    return passed;
}

#include "DocumentGraphIdentity.h"

#include "DocumentGraphCodec.h"

#include <Windows.h>
#include <bcrypt.h>
#include <pathcch.h>

#include <algorithm>
#include <cwchar>
#include <limits>
#include <utility>

namespace hancom::graph::identity {
namespace {

bool EqualHash(const Sha256& left, const Sha256& right) noexcept {
    return left.bytes == right.bytes;
}
bool ZeroHash(const Sha256& value) noexcept {
    for (const std::uint8_t byte : value.bytes) if (byte != 0) return false;
    return true;
}
bool ValidPublishIdentity(const PublishIdentity& publish) noexcept {
    if (!HasOnlyKnownProfiles(publish.profileBits) ||
        CloseProfileBits(publish.profileBits)!=publish.profileBits) return false;
    if (publish.layoutPresent)
        return publish.semanticCertified &&
            (publish.profileBits&ProfileBit(ProfileId::Layout))!=0 &&
            !ZeroHash(publish.layoutRoot);
    return ZeroHash(publish.layoutRoot);
}
void Put16(std::uint8_t* const out, const std::uint16_t value) noexcept {
    out[0] = static_cast<std::uint8_t>(value);
    out[1] = static_cast<std::uint8_t>(value >> 8);
}
void Put64(std::uint8_t* const out, std::uint64_t value) noexcept {
    for (unsigned index = 0; index != 8; ++index) {
        out[index] = static_cast<std::uint8_t>(value);
        value >>= 8;
    }
}
std::uint16_t Get16(const std::uint8_t* const in) noexcept {
    return static_cast<std::uint16_t>(in[0]) |
        static_cast<std::uint16_t>(in[1]) << 8;
}
std::uint64_t Get64(const std::uint8_t* const in) noexcept {
    std::uint64_t value = 0;
    for (unsigned index = 0; index != 8; ++index) {
        value |= static_cast<std::uint64_t>(in[index]) << (index * 8);
    }
    return value;
}
int HexValue(const wchar_t character) noexcept {
    if (character >= L'0' && character <= L'9') return character - L'0';
    if (character >= L'a' && character <= L'f') return character - L'a' + 10;
    if (character >= L'A' && character <= L'F') return character - L'A' + 10;
    return -1;
}
bool ContainsId(const std::vector<NodeId>& values, const NodeId& wanted) noexcept {
    return std::any_of(values.begin(), values.end(), [&](const NodeId& value) {
        return EqualUuid(value, wanted);
    });
}
enum class MintResult : std::uint8_t { Minted = 0, SourceFailure, CollisionExhausted };
MintResult MintDistinctUuid(const UuidSource& source,
                            const std::vector<Uuid128>& forbidden,
                            Uuid128* const value) noexcept {
    for (unsigned attempt = 0; attempt != kUuidCollisionAttempts; ++attempt) {
        if (!MintUuidV4(source,value)) return MintResult::SourceFailure;
        if (!ContainsId(forbidden,*value)) return MintResult::Minted;
    }
    return MintResult::CollisionExhausted;
}
// The live default path must never draw system entropy for freshly reconciled
// node ids: two captures of the same unmodified document would then emit
// different bytes and require_equivalent_captures fails. Ids stay RFC-4122 v4
// but are derived from the candidate's stable keys plus the collision attempt
// counter. An injected (test) source is still honored verbatim.
bool UsesDefaultUuidSource(const UuidSource& source) noexcept {
    return source.fill == nullptr || source.fill == &SystemRandomBytes;
}
bool DeterministicReconcileNodeId(const ReconcileCandidate& candidate,
                                  const std::uint64_t attempt,
                                  Uuid128* const value) noexcept {
    if (value == nullptr) return false;
    try {
        codec::Bytes buffer;
        const auto add = [&buffer](const codec::Bytes& part) {
            buffer.insert(buffer.end(), part.begin(), part.end());
        };
        const auto addText = [&add](const std::wstring& text) {
            add(codec::Utf16(
                reinterpret_cast<const std::uint16_t*>(text.data()), text.size()));
        };
        add(codec::Uint64(attempt));
        add(codec::Uint64(static_cast<std::uint64_t>(candidate.kind)));
        add(codec::Uint64(candidate.ordinal));
        add(codec::Uint64(candidate.partition));
        addText(candidate.primaryKey);
        addText(candidate.secondaryKey);
        const Sha256 digest = codec::DomainHash(
            "HGN1-TODO18-RECONCILE-NODEID-V1", codec::View(buffer));
        std::copy(digest.bytes.begin(),
                  digest.bytes.begin() + value->bytes.size(),
                  value->bytes.begin());
        value->bytes[6] = static_cast<std::uint8_t>((value->bytes[6] & 0x0fU) | 0x40U);
        value->bytes[8] = static_cast<std::uint8_t>((value->bytes[8] & 0x3fU) | 0x80U);
        return true;
    } catch (...) {
        return false;
    }
}
MintResult MintReconciledUuid(const UuidSource& source,
                              const ReconcileCandidate& candidate,
                              const std::vector<Uuid128>& forbidden,
                              Uuid128* const value) noexcept {
    if (!UsesDefaultUuidSource(source))
        return MintDistinctUuid(source,forbidden,value);
    for (unsigned attempt = 0; attempt != kUuidCollisionAttempts; ++attempt) {
        if (!DeterministicReconcileNodeId(candidate,attempt,value))
            return MintResult::SourceFailure;
        if (!ContainsId(forbidden,*value)) return MintResult::Minted;
    }
    return MintResult::CollisionExhausted;
}
bool IsDescendant(const std::vector<IdentityNode>& nodes, const NodeId& node,
                  const NodeId& possibleAncestor) noexcept {
    const IdentityNode* current = nullptr;
    for (const IdentityNode& candidate : nodes) {
        if (EqualUuid(candidate.id, node)) { current = &candidate; break; }
    }
    for (std::size_t guard = 0; current != nullptr && guard <= nodes.size(); ++guard) {
        if (EqualUuid(current->id, possibleAncestor)) return true;
        const IdentityNode* parent = nullptr;
        for (const IdentityNode& candidate : nodes) {
            if (EqualUuid(candidate.id, current->parent)) { parent = &candidate; break; }
        }
        current = parent;
    }
    return false;
}
std::vector<NodeId> ReceiptReservedIds(const IdentityReceipt& receipt) {
    std::vector<NodeId> ids;
    for (const RemapEntry& entry : receipt.remaps) {
        if (!ContainsId(ids,entry.source)) ids.push_back(entry.source);
        if (entry.targetPresent && !ContainsId(ids,entry.target)) ids.push_back(entry.target);
    }
    for (const TombstoneEntry& entry : receipt.tombstones)
        if (!ContainsId(ids,entry.node)) ids.push_back(entry.node);
    return ids;
}
RemapEntry Remap(const Uuid128& source, const NodeId* const target,
                 const RemapDisposition disposition,
                 const RemapReason reason) noexcept {
    RemapEntry entry{};
    entry.source = source;
    entry.targetPresent = target != nullptr;
    if (target != nullptr) entry.target = *target;
    entry.disposition = disposition;
    entry.reason = reason;
    return entry;
}

} // namespace

bool EqualUuid(const Uuid128& left, const Uuid128& right) noexcept {
    return left.bytes == right.bytes;
}
bool IsZeroUuid(const Uuid128& value) noexcept {
    for (const std::uint8_t byte : value.bytes) if (byte != 0) return false;
    return true;
}
bool IsRfc4122V4(const Uuid128& value) noexcept {
    return !IsZeroUuid(value) && (value.bytes[6] & 0xf0U) == 0x40U &&
        (value.bytes[8] & 0xc0U) == 0x80U;
}
bool ParseCanonicalUuid(const wchar_t* const text, Uuid128* const value) noexcept {
    if (text == nullptr || value == nullptr || std::wcslen(text) != 36) return false;
    Uuid128 parsed{};
    std::size_t output = 0;
    for (std::size_t index = 0; index != 36;) {
        if (index == 8 || index == 13 || index == 18 || index == 23) {
            if (text[index++] != L'-') return false;
            continue;
        }
        const int high = HexValue(text[index++]);
        const int low = HexValue(text[index++]);
        if (high < 0 || low < 0 || output >= parsed.bytes.size()) return false;
        parsed.bytes[output++] = static_cast<std::uint8_t>((high << 4) | low);
    }
    if (output != parsed.bytes.size() || !IsRfc4122V4(parsed)) return false;
    *value = parsed;
    return true;
}
std::wstring FormatCanonicalUuid(const Uuid128& value) {
    constexpr wchar_t hex[] = L"0123456789abcdef";
    std::wstring text;
    text.reserve(36);
    for (std::size_t index = 0; index != value.bytes.size(); ++index) {
        if (index == 4 || index == 6 || index == 8 || index == 10) text.push_back(L'-');
        text.push_back(hex[value.bytes[index] >> 4]);
        text.push_back(hex[value.bytes[index] & 0x0fU]);
    }
    return text;
}
bool SystemRandomBytes(void*, std::uint8_t* const bytes,
                       const std::uint32_t count) noexcept {
    return bytes != nullptr && BCRYPT_SUCCESS(BCryptGenRandom(
        nullptr, bytes, count, BCRYPT_USE_SYSTEM_PREFERRED_RNG));
}
bool MintUuidV4(const UuidSource& source, Uuid128* const value) noexcept {
    if (value == nullptr || source.fill == nullptr ||
        !source.fill(source.context, value->bytes.data(),
                     static_cast<std::uint32_t>(value->bytes.size()))) return false;
    value->bytes[6] = static_cast<std::uint8_t>((value->bytes[6] & 0x0fU) | 0x40U);
    value->bytes[8] = static_cast<std::uint8_t>((value->bytes[8] & 0x3fU) | 0x80U);
    return true;
}

bool NormalizeDocumentPath(const std::wstring& path,
                           std::wstring* const normalized) noexcept {
    if (normalized == nullptr) return false;
    if (path.empty()) { normalized->clear(); return true; }
    try {
        const DWORD required = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
        if (required == 0) return false;
        std::wstring full(required, L'\0');
        const DWORD copied = GetFullPathNameW(path.c_str(), required, full.data(), nullptr);
        if (copied == 0 || copied >= required) return false;
        full.resize(copied);
        std::wstring canonical(32768, L'\0');
        const HRESULT result = PathCchCanonicalizeEx(
            canonical.data(), canonical.size(), full.c_str(), PATHCCH_ALLOW_LONG_PATHS);
        if (FAILED(result)) return false;
        canonical.resize(std::wcslen(canonical.c_str()));
        constexpr wchar_t uncPrefix[] = L"\\\\?\\UNC\\";
        constexpr wchar_t longPrefix[] = L"\\\\?\\";
        if (canonical.rfind(uncPrefix, 0) == 0) {
            canonical = L"\\\\" + canonical.substr((std::size(uncPrefix) - 1));
        } else if (canonical.rfind(longPrefix, 0) == 0) {
            canonical.erase(0, std::size(longPrefix) - 1);
        }
        *normalized = std::move(canonical);
        return true;
    } catch (...) {
        return false;
    }
}
bool LocatorMatches(const DocumentLocator& expected,
                    const DocumentLocator& presented) noexcept {
    if (presented.documentId != 0 && expected.documentId != presented.documentId) return false;
    if (presented.hwnd != 0 && expected.hwnd != presented.hwnd) return false;
    std::wstring expectedPath;
    std::wstring presentedPath;
    if (!NormalizeDocumentPath(expected.path, &expectedPath) ||
        !NormalizeDocumentPath(presented.path, &presentedPath)) return false;
    if (expectedPath.empty() || presentedPath.empty()) return expectedPath == presentedPath;
    const int compared = CompareStringOrdinal(
        expectedPath.c_str(), static_cast<int>(expectedPath.size()),
        presentedPath.c_str(), static_cast<int>(presentedPath.size()), TRUE);
    return compared == CSTR_EQUAL;
}

SessionAliasRegistry::SessionAliasRegistry(const UuidSource source) noexcept : source_(source) {}
AliasStatus SessionAliasRegistry::Open(const std::uint64_t canonicalIdentity,
    const std::uint64_t aliasIdentity, const DocumentLocator& locator,
    DocumentSessionId* const session) noexcept {
    if (session == nullptr || source_.fill == nullptr) return AliasStatus::InvalidUuidSource;
    for (const Session& current : sessions_) {
        if (current.canonicalIdentity == canonicalIdentity) return AliasStatus::AliasAlreadyBound;
        if (std::find(current.aliases.begin(), current.aliases.end(), aliasIdentity) != current.aliases.end())
            return AliasStatus::AliasAlreadyBound;
    }
    Session created{};
    created.canonicalIdentity = canonicalIdentity;
    created.locator = locator;
    created.aliases.push_back(aliasIdentity);
    std::vector<Uuid128> forbidden;
    forbidden.reserve(sessions_.size());
    for (const Session& current : sessions_) forbidden.push_back(current.id);
    const MintResult mint = MintDistinctUuid(source_,forbidden,&created.id);
    if (mint == MintResult::SourceFailure) return AliasStatus::InvalidUuidSource;
    if (mint == MintResult::CollisionExhausted) return AliasStatus::CollisionExhausted;
    *session = created.id;
    sessions_.push_back(std::move(created));
    return AliasStatus::Ok;
}
AliasStatus SessionAliasRegistry::AddAlias(const std::uint64_t canonicalIdentity,
                                            const std::uint64_t aliasIdentity) noexcept {
    for (const Session& current : sessions_) {
        if (std::find(current.aliases.begin(), current.aliases.end(), aliasIdentity) != current.aliases.end())
            return AliasStatus::AliasAlreadyBound;
    }
    for (Session& current : sessions_) {
        if (current.canonicalIdentity == canonicalIdentity) {
            current.aliases.push_back(aliasIdentity);
            return AliasStatus::Ok;
        }
    }
    return AliasStatus::UnknownCanonical;
}
AliasStatus SessionAliasRegistry::Resolve(const std::uint64_t aliasIdentity,
    const DocumentLocator& locator, DocumentSessionId* const session) const noexcept {
    for (const Session& current : sessions_) {
        if (std::find(current.aliases.begin(), current.aliases.end(), aliasIdentity) != current.aliases.end()) {
            if (!LocatorMatches(current.locator, locator)) return AliasStatus::LocatorMismatch;
            if (session != nullptr) *session = current.id;
            return AliasStatus::Ok;
        }
    }
    return AliasStatus::UnknownAlias;
}
bool SessionAliasRegistry::Close(const std::uint64_t canonicalIdentity) noexcept {
    const auto found = std::find_if(sessions_.begin(), sessions_.end(), [&](const Session& current) {
        return current.canonicalIdentity == canonicalIdentity;
    });
    if (found == sessions_.end()) return false;
    sessions_.erase(found);
    return true;
}
void SessionAliasRegistry::BridgeRestart() noexcept { sessions_.clear(); }

GraphVersionError ValidateGraphVersion(const GraphVersionV1& version) noexcept {
    if (version.schema != kSchemaVersionV1) return GraphVersionError::BadSchema;
    if (!HasOnlyKnownProfiles(version.profileBits) ||
        CloseProfileBits(version.profileBits) != version.profileBits)
        return GraphVersionError::BadProfiles;
    if (!IsRfc4122V4(version.documentSessionId)) return GraphVersionError::BadDocumentSessionId;
    if (!IsRfc4122V4(version.graphId) ||
        EqualUuid(version.graphId,version.documentSessionId)) return GraphVersionError::BadGraphId;
    if (version.semanticRevision == 0 || version.layoutRevision == 0 || version.locatorEpoch == 0)
        return GraphVersionError::BadRevision;
    if (version.layoutPresent) {
        if (!version.semanticCertified ||
            (version.profileBits & ProfileBit(ProfileId::Layout)) == 0 ||
            ZeroHash(version.layoutRoot)) return GraphVersionError::BadLayoutRoot;
    } else if (!ZeroHash(version.layoutRoot)) {
        return GraphVersionError::BadLayoutRoot;
    }
    return GraphVersionError::None;
}
GraphVersionError SerializeGraphVersion(const GraphVersionV1& version,
    SerializedGraphVersionV1* const bytes) noexcept {
    if (bytes == nullptr) return GraphVersionError::BadSize;
    const GraphVersionError valid = ValidateGraphVersion(version);
    if (valid != GraphVersionError::None) return valid;
    bytes->fill(0);
    Put16(bytes->data(), version.schema);
    Put64(bytes->data() + 2, version.profileBits);
    (*bytes)[10] = version.semanticCertified ? 1U : 0U;
    (*bytes)[11] = version.layoutPresent ? 1U : 0U;
    std::copy(version.documentSessionId.bytes.begin(), version.documentSessionId.bytes.end(), bytes->begin() + 16);
    std::copy(version.graphId.bytes.begin(), version.graphId.bytes.end(), bytes->begin() + 32);
    Put64(bytes->data() + 48, version.semanticRevision);
    Put64(bytes->data() + 56, version.layoutRevision);
    Put64(bytes->data() + 64, version.locatorEpoch);
    std::copy(version.observedSemanticRoot.bytes.begin(), version.observedSemanticRoot.bytes.end(), bytes->begin() + 72);
    std::copy(version.layoutRoot.bytes.begin(), version.layoutRoot.bytes.end(), bytes->begin() + 104);
    std::copy(version.captureRoot.bytes.begin(), version.captureRoot.bytes.end(), bytes->begin() + 136);
    return GraphVersionError::None;
}
GraphVersionError DeserializeGraphVersion(const std::uint8_t* const bytes,
    const std::uint64_t size, GraphVersionV1* const version) noexcept {
    if (bytes == nullptr || version == nullptr || size != kGraphVersionBytesV1)
        return GraphVersionError::BadSize;
    if (Get16(bytes) != kSchemaVersionV1) return GraphVersionError::BadSchema;
    if (bytes[10] > 1 || bytes[11] > 1) return GraphVersionError::BadBoolean;
    for (std::size_t index = 12; index != 16; ++index)
        if (bytes[index] != 0) return GraphVersionError::ReservedNonzero;
    GraphVersionV1 parsed{};
    parsed.schema = Get16(bytes);
    parsed.profileBits = Get64(bytes + 2);
    parsed.semanticCertified = bytes[10] != 0;
    parsed.layoutPresent = bytes[11] != 0;
    std::copy(bytes + 16, bytes + 32, parsed.documentSessionId.bytes.begin());
    std::copy(bytes + 32, bytes + 48, parsed.graphId.bytes.begin());
    parsed.semanticRevision = Get64(bytes + 48);
    parsed.layoutRevision = Get64(bytes + 56);
    parsed.locatorEpoch = Get64(bytes + 64);
    std::copy(bytes + 72, bytes + 104, parsed.observedSemanticRoot.bytes.begin());
    std::copy(bytes + 104, bytes + 136, parsed.layoutRoot.bytes.begin());
    std::copy(bytes + 136, bytes + 168, parsed.captureRoot.bytes.begin());
    const GraphVersionError valid = ValidateGraphVersion(parsed);
    if (valid != GraphVersionError::None) return valid;
    *version = parsed;
    return GraphVersionError::None;
}
bool FullVersionCas(const GraphVersionV1& expected, const GraphVersionV1& current) noexcept {
    SerializedGraphVersionV1 expectedBytes{};
    SerializedGraphVersionV1 currentBytes{};
    return SerializeGraphVersion(expected, &expectedBytes) == GraphVersionError::None &&
        SerializeGraphVersion(current, &currentBytes) == GraphVersionError::None &&
        expectedBytes == currentBytes;
}
bool CursorVersionCas(const GraphVersionV1& expected, const GraphVersionV1& current) noexcept {
    return FullVersionCas(expected, current);
}
bool PatchVersionCas(const GraphVersionV1& expected, const GraphVersionV1& current) noexcept {
    return expected.semanticCertified && current.semanticCertified && FullVersionCas(expected, current);
}

GraphLifecycle::GraphLifecycle(const UuidSource source) noexcept : source_(source) {}
LineageResult GraphLifecycle::StartNewLineage(const DocumentSessionId& session,
    const PublishIdentity& publish) noexcept {
    if (!IsRfc4122V4(session) || !ValidPublishIdentity(publish))
        return LineageResult::InvalidInput;
    if (ContainsId(usedIdentityIds_,session)) return LineageResult::IdentityCollision;
    GraphId graph{};
    std::vector<Uuid128> forbidden = usedIdentityIds_;
    if (!ContainsId(forbidden,session)) forbidden.push_back(session);
    const MintResult mint = MintDistinctUuid(source_,forbidden,&graph);
    if (mint == MintResult::SourceFailure) return LineageResult::InvalidInput;
    if (mint == MintResult::CollisionExhausted) return LineageResult::CollisionExhausted;
    GraphVersionV1 next{};
    next.profileBits = publish.profileBits;
    next.semanticCertified = publish.semanticCertified;
    next.layoutPresent = publish.layoutPresent;
    next.documentSessionId = session;
    next.graphId = graph;
    next.semanticRevision = 1;
    next.layoutRevision = 1;
    next.locatorEpoch = 1;
    next.observedSemanticRoot = publish.semanticRoot;
    next.layoutRoot = publish.layoutRoot;
    next.captureRoot = publish.captureRoot;
    std::vector<Uuid128> nextUsed = usedIdentityIds_;
    if (active_) {
        if (!ContainsId(nextUsed,version_.documentSessionId)) nextUsed.push_back(version_.documentSessionId);
        if (!ContainsId(nextUsed,version_.graphId)) nextUsed.push_back(version_.graphId);
    }
    if (!ContainsId(nextUsed,session)) nextUsed.push_back(session);
    nextUsed.push_back(graph);
    version_ = next;
    usedIdentityIds_ = std::move(nextUsed);
    active_ = true;
    return LineageResult::Started;
}
bool GraphLifecycle::ReserveIdentityIds(const std::vector<Uuid128>& ids) noexcept {
    std::vector<Uuid128> nextUsed = usedIdentityIds_;
    for (const Uuid128& id : ids) {
        if (!IsRfc4122V4(id) || ContainsId(nextUsed,id)) return false;
        nextUsed.push_back(id);
    }
    usedIdentityIds_ = std::move(nextUsed);
    return true;
}
GraphVersionError GraphLifecycle::Resume(const GraphVersionV1& version) noexcept {
    const GraphVersionError valid = ValidateGraphVersion(version);
    if (valid != GraphVersionError::None) return valid;
    const bool sameActivePair = active_ &&
        EqualUuid(version.documentSessionId,version_.documentSessionId) &&
        EqualUuid(version.graphId,version_.graphId);
    if (!sameActivePair &&
        (ContainsId(usedIdentityIds_,version.documentSessionId) ||
         ContainsId(usedIdentityIds_,version.graphId)))
        return GraphVersionError::ReservedIdentityCollision;
    std::vector<Uuid128> nextUsed = usedIdentityIds_;
    if (!ContainsId(nextUsed,version.documentSessionId)) nextUsed.push_back(version.documentSessionId);
    if (!ContainsId(nextUsed,version.graphId)) nextUsed.push_back(version.graphId);
    version_ = version;
    usedIdentityIds_ = std::move(nextUsed);
    active_ = true;
    return GraphVersionError::None;
}
LifecycleResult GraphLifecycle::ApplyPublish(const PublishIdentity& publish,
                                             const PublishOutcome outcome) noexcept {
    if (!active_) return LifecycleResult::Inactive;
    if (outcome != PublishOutcome::Success) return LifecycleResult::Unchanged;
    if (!ValidPublishIdentity(publish)) return LifecycleResult::InvalidPublish;
    const bool semanticChanged = !EqualHash(version_.observedSemanticRoot, publish.semanticRoot);
    const bool layoutChanged = version_.layoutPresent != publish.layoutPresent ||
        !EqualHash(version_.layoutRoot, publish.layoutRoot);
    const std::uint64_t maximum = (std::numeric_limits<std::uint64_t>::max)();
    if ((semanticChanged && version_.semanticRevision == maximum) ||
        (layoutChanged && version_.layoutRevision == maximum))
        return LifecycleResult::CounterOverflow;
    if (semanticChanged) ++version_.semanticRevision;
    if (layoutChanged) ++version_.layoutRevision;
    version_.profileBits = publish.profileBits;
    version_.semanticCertified = publish.semanticCertified;
    version_.layoutPresent = publish.layoutPresent;
    version_.observedSemanticRoot = publish.semanticRoot;
    version_.layoutRoot = publish.layoutRoot;
    version_.captureRoot = publish.captureRoot;
    return LifecycleResult::Applied;
}
ReopenResult GraphLifecycle::AuthorizedReopen(const DocumentSessionId& newSession,
    const PublishIdentity& fresh, const bool completeGraph,
    const bool exactCanonicalTreeAndPayload) noexcept {
    if (!active_ || !IsRfc4122V4(newSession) || !completeGraph ||
        !version_.semanticCertified || !fresh.semanticCertified)
        return ReopenResult::RejectedUncertified;
    if (ContainsId(usedIdentityIds_,newSession))
        return ReopenResult::RejectedIdentityCollision;
    if (!ValidPublishIdentity(fresh) || fresh.profileBits != version_.profileBits)
        return ReopenResult::RejectedCanonicalPairing;
    if (!EqualHash(version_.observedSemanticRoot, fresh.semanticRoot))
        return ReopenResult::RejectedDifferentRoot;
    if (!exactCanonicalTreeAndPayload)
        return ReopenResult::RejectedCanonicalPairing;
    if (version_.locatorEpoch == (std::numeric_limits<std::uint64_t>::max)())
        return ReopenResult::RejectedCounterOverflow;
    std::vector<Uuid128> nextUsed = usedIdentityIds_;
    if (!ContainsId(nextUsed,newSession)) nextUsed.push_back(newSession);
    version_.documentSessionId = newSession;
    ++version_.locatorEpoch;
    usedIdentityIds_ = std::move(nextUsed);
    return ReopenResult::Retained;
}

MutationIdentity::MutationIdentity(const UuidSource source,
    const std::uint64_t semanticRevision) noexcept
    : source_(source), semanticRevision_(semanticRevision) {}
IdentityNode* MutationIdentity::Find(const NodeId& id) noexcept {
    const auto found = std::find_if(nodes_.begin(), nodes_.end(), [&](const IdentityNode& node) {
        return EqualUuid(node.id, id);
    });
    return found == nodes_.end() ? nullptr : &*found;
}
const IdentityNode* MutationIdentity::Find(const NodeId& id) const noexcept {
    const auto found = std::find_if(nodes_.begin(), nodes_.end(), [&](const IdentityNode& node) {
        return EqualUuid(node.id, id);
    });
    return found == nodes_.end() ? nullptr : &*found;
}
bool MutationIdentity::Seed(const std::vector<IdentityNode>& nodes) noexcept {
    lastResult_=MutationResult::InvalidInput;
    std::vector<NodeId> ids;
    std::vector<Uuid128> nextAllTime=allTimeIdentityIds_;
    for (const IdentityNode& node : nodes) {
        const bool retained=Find(node.id)!=nullptr;
        if (!IsRfc4122V4(node.id) || ContainsId(ids,node.id) ||
            ContainsId(reservedIdentityIds_,node.id) ||
            (ContainsId(allTimeIdentityIds_,node.id) && !retained)) return false;
        ids.push_back(node.id);
    }
    for (const IdentityNode& node : nodes) {
        if (!IsZeroUuid(node.parent) && !IsRfc4122V4(node.parent)) return false;
        if (!ContainsId(nextAllTime,node.id)) nextAllTime.push_back(node.id);
        if (!IsZeroUuid(node.parent) && !ContainsId(nextAllTime,node.parent))
            nextAllTime.push_back(node.parent);
    }
    nodes_ = nodes;
    tombstoned_.clear();
    clientIds_.clear();
    receiptIdentityIds_.clear();
    allTimeIdentityIds_=std::move(nextAllTime);
    lastResult_ = MutationResult::Applied;
    return true;
}
bool MutationIdentity::ReserveIdentityIds(const std::vector<Uuid128>& ids) noexcept {
    std::vector<Uuid128> next=reservedIdentityIds_;
    std::vector<Uuid128> nextAllTime=allTimeIdentityIds_;
    for (const Uuid128& id : ids) {
        if (!IsRfc4122V4(id) || Find(id)!=nullptr) return false;
        if (ContainsId(next,id)) continue;
        if (ContainsId(nextAllTime,id)) return false;
        next.push_back(id);
        nextAllTime.push_back(id);
    }
    reservedIdentityIds_=std::move(next);
    allTimeIdentityIds_=std::move(nextAllTime);
    return true;
}
MutationResult MutationIdentity::MintUnused(
    const std::vector<NodeId>& operationReserved, NodeId* const id) noexcept {
    std::vector<Uuid128> forbidden=allTimeIdentityIds_;
    forbidden.insert(forbidden.end(),reservedIdentityIds_.begin(),reservedIdentityIds_.end());
    forbidden.insert(forbidden.end(),tombstoned_.begin(),tombstoned_.end());
    forbidden.insert(forbidden.end(),clientIds_.begin(),clientIds_.end());
    forbidden.insert(forbidden.end(),receiptIdentityIds_.begin(),receiptIdentityIds_.end());
    forbidden.insert(forbidden.end(),operationReserved.begin(),operationReserved.end());
    for (const IdentityNode& node : nodes_) forbidden.push_back(node.id);
    const MintResult result=MintDistinctUuid(source_,forbidden,id);
    if (result==MintResult::Minted) {
        if (!ContainsId(allTimeIdentityIds_,*id)) allTimeIdentityIds_.push_back(*id);
        return MutationResult::Applied;
    }
    return result==MintResult::CollisionExhausted ? MutationResult::CollisionExhausted :
        MutationResult::InvalidInput;
}
void MutationIdentity::RememberReceiptIds(const IdentityReceipt& receipt) {
    for (const NodeId& id : ReceiptReservedIds(receipt)) {
        if (!ContainsId(receiptIdentityIds_,id)) receiptIdentityIds_.push_back(id);
        if (!ContainsId(allTimeIdentityIds_,id)) allTimeIdentityIds_.push_back(id);
    }
}
bool MutationIdentity::Tombstone(const NodeId& id, const TombstoneReason reason,
                                  IdentityReceipt* const receipt) noexcept {
    if (receipt == nullptr || Find(id) == nullptr || ContainsId(tombstoned_, id)) return false;
    tombstoned_.push_back(id);
    receipt->tombstones.push_back({id, semanticRevision_, reason});
    return true;
}
bool MutationIdentity::Insert(const ClientId& client, const NodeId& parent,
    const NodeKind kind, IdentityReceipt* const receipt) noexcept {
    lastResult_=MutationResult::InvalidInput;
    if (receipt == nullptr || !IsRfc4122V4(client) || Find(parent) == nullptr ||
        ContainsId(clientIds_, client)) return false;
    std::vector<NodeId> reserved = ReceiptReservedIds(*receipt);
    reserved.push_back(client);
    NodeId id{};
    const MutationResult mint=MintUnused(reserved,&id);
    if (mint!=MutationResult::Applied) { lastResult_=mint; return false; }
    clientIds_.push_back(client);
    nodes_.push_back({id, parent, kind, {}});
    receipt->remaps.push_back(Remap(client, &id, RemapDisposition::New, RemapReason::ClientInsert));
    RememberReceiptIds(*receipt);
    lastResult_=MutationResult::Applied;
    return true;
}
bool MutationIdentity::Split(const NodeId& original, const std::uint64_t pieceCount,
                              IdentityReceipt* const receipt) noexcept {
    lastResult_=MutationResult::InvalidInput;
    const IdentityNode* source = Find(original);
    if (receipt == nullptr || source == nullptr || pieceCount < 2 ||
        (source->kind != NodeKind::Paragraph && source->kind != NodeKind::CharacterRun)) return false;
    const IdentityNode snapshot = *source;
    if (pieceCount - 1 > (std::numeric_limits<std::size_t>::max)()) return false;
    std::vector<NodeId> reserved = ReceiptReservedIds(*receipt);
    std::vector<NodeId> minted;
    minted.reserve(static_cast<std::size_t>(pieceCount - 1));
    for (std::uint64_t piece = 1; piece != pieceCount; ++piece) {
        NodeId id{};
        const MutationResult mint=MintUnused(reserved,&id);
        if (mint!=MutationResult::Applied) { lastResult_=mint; return false; }
        reserved.push_back(id);
        minted.push_back(id);
    }
    receipt->remaps.push_back(Remap(original, &original, RemapDisposition::Retained, RemapReason::Split));
    for (const NodeId& id : minted) {
        nodes_.push_back({id, snapshot.parent, snapshot.kind, {}});
        receipt->remaps.push_back(Remap(id, &id, RemapDisposition::New, RemapReason::Split));
    }
    RememberReceiptIds(*receipt);
    lastResult_=MutationResult::Applied;
    return true;
}
bool MutationIdentity::Coalesce(const std::vector<NodeId>& orderedNodes,
                                 IdentityReceipt* const receipt) noexcept {
    lastResult_=MutationResult::InvalidInput;
    if (receipt == nullptr || orderedNodes.size() < 2) return false;
    std::vector<std::size_t> positions;
    positions.reserve(orderedNodes.size());
    NodeKind survivorKind = NodeKind::Document;
    NodeId commonParent{};
    for (std::size_t input = 0; input != orderedNodes.size(); ++input) {
        if (ContainsId(tombstoned_,orderedNodes[input])) return false;
        const auto found = std::find_if(nodes_.begin(),nodes_.end(),[&](const IdentityNode& node) {
            return EqualUuid(node.id,orderedNodes[input]);
        });
        if (found == nodes_.end()) return false;
        const std::size_t position = static_cast<std::size_t>(found - nodes_.begin());
        if (input == 0) {
            survivorKind = found->kind;
            commonParent = found->parent;
            if (survivorKind != NodeKind::Paragraph &&
                survivorKind != NodeKind::CharacterRun) return false;
        } else if (found->kind != survivorKind || !EqualUuid(found->parent,commonParent) ||
                   position <= positions.back()) {
            return false;
        }
        positions.push_back(position);
    }
    const NodeId survivorId = orderedNodes.front();
    receipt->remaps.push_back(Remap(survivorId, &survivorId,
        RemapDisposition::Retained, RemapReason::Coalesce));
    for (std::size_t index = 1; index != orderedNodes.size(); ++index) {
        const IdentityNode* absorbed = Find(orderedNodes[index]);
        if (absorbed == nullptr || absorbed->kind != survivorKind) return false;
        const NodeId absorbedId = absorbed->id;
        if (survivorKind == NodeKind::Paragraph) {
            for (IdentityNode& child : nodes_) {
                if (EqualUuid(child.parent, absorbedId)) {
                    child.parent = survivorId;
                    receipt->remaps.push_back(Remap(child.id, &child.id,
                        RemapDisposition::Retained, RemapReason::Coalesce));
                }
            }
        }
        if (!Tombstone(absorbedId, TombstoneReason::Coalesce, receipt)) return false;
        receipt->remaps.push_back(Remap(absorbedId, nullptr,
            RemapDisposition::Tombstoned, RemapReason::Coalesce));
    }
    nodes_.erase(std::remove_if(nodes_.begin(), nodes_.end(), [&](const IdentityNode& node) {
        return ContainsId(tombstoned_, node.id);
    }), nodes_.end());
    RememberReceiptIds(*receipt);
    lastResult_=MutationResult::Applied;
    return true;
}
bool MutationIdentity::MoveSubtree(const NodeId& root, const NodeId& newParent,
                                    IdentityReceipt* const receipt) noexcept {
    lastResult_=MutationResult::InvalidInput;
    IdentityNode* moved = Find(root);
    if (receipt == nullptr || moved == nullptr || Find(newParent) == nullptr ||
        IsDescendant(nodes_, newParent, root)) return false;
    moved->parent = newParent;
    for (const IdentityNode& node : nodes_) {
        if (IsDescendant(nodes_, node.id, root)) receipt->remaps.push_back(
            Remap(node.id, &node.id, RemapDisposition::Retained, RemapReason::Move));
    }
    RememberReceiptIds(*receipt);
    lastResult_=MutationResult::Applied;
    return true;
}
bool MutationIdentity::DeleteSubtree(const NodeId& root,
                                      IdentityReceipt* const receipt) noexcept {
    lastResult_=MutationResult::InvalidInput;
    if (receipt == nullptr || Find(root) == nullptr) return false;
    std::vector<NodeId> removed;
    for (const IdentityNode& node : nodes_)
        if (IsDescendant(nodes_, node.id, root)) removed.push_back(node.id);
    for (const NodeId& id : removed) {
        if (!Tombstone(id, TombstoneReason::Delete, receipt)) return false;
        receipt->remaps.push_back(Remap(id, nullptr, RemapDisposition::Tombstoned, RemapReason::Delete));
    }
    nodes_.erase(std::remove_if(nodes_.begin(), nodes_.end(), [&](const IdentityNode& node) {
        return ContainsId(removed, node.id);
    }), nodes_.end());
    RememberReceiptIds(*receipt);
    lastResult_=MutationResult::Applied;
    return true;
}
bool MutationIdentity::CloneSubtree(const NodeId& root, const NodeId& newParent,
                                     IdentityReceipt* const receipt) noexcept {
    lastResult_=MutationResult::InvalidInput;
    if (receipt == nullptr || Find(root) == nullptr || Find(newParent) == nullptr) return false;
    std::vector<IdentityNode> originals;
    for (const IdentityNode& node : nodes_)
        if (IsDescendant(nodes_, node.id, root)) originals.push_back(node);
    std::vector<NodeId> reserved = ReceiptReservedIds(*receipt);
    std::vector<NodeId> minted;
    minted.reserve(originals.size());
    for (std::size_t index = 0; index != originals.size(); ++index) {
        NodeId id{};
        const MutationResult mint=MintUnused(reserved,&id);
        if (mint!=MutationResult::Applied) { lastResult_=mint; return false; }
        reserved.push_back(id);
        minted.push_back(id);
    }
    std::vector<std::pair<NodeId, NodeId>> mappings;
    for (std::size_t index = 0; index != originals.size(); ++index) {
        const IdentityNode& original = originals[index];
        const NodeId id = minted[index];
        mappings.push_back({original.id, id});
        NodeId parent = EqualUuid(original.id, root) ? newParent : original.parent;
        for (const auto& mapping : mappings)
            if (EqualUuid(mapping.first, original.parent)) parent = mapping.second;
        nodes_.push_back({id, parent, original.kind, original.cell});
        receipt->remaps.push_back(Remap(original.id, &id, RemapDisposition::New, RemapReason::Clone));
    }
    RememberReceiptIds(*receipt);
    lastResult_=MutationResult::Applied;
    return true;
}
bool MutationIdentity::ApplyTableCellTransitions(const NodeId& table,
    const std::vector<CellTransition>& transitions, IdentityReceipt* const receipt) noexcept {
    lastResult_=MutationResult::InvalidInput;
    const IdentityNode* tableNode = Find(table);
    if (receipt == nullptr || tableNode == nullptr || tableNode->kind != NodeKind::Table) return false;
    const std::uint64_t maximum=(std::numeric_limits<std::uint64_t>::max)();
    const auto sameCoordinate = [](const CellCoordinateV1& left,
                                   const CellCoordinateV1& right) noexcept {
        return left.row1 == right.row1 && left.column1 == right.column1 &&
            left.rowSpan == right.rowSpan && left.columnSpan == right.columnSpan;
    };
    const auto coordinateLess = [](const CellCoordinateV1& left,
                                   const CellCoordinateV1& right) noexcept {
        if (left.row1 != right.row1) return left.row1 < right.row1;
        if (left.column1 != right.column1) return left.column1 < right.column1;
        if (left.rowSpan != right.rowSpan) return left.rowSpan < right.rowSpan;
        return left.columnSpan < right.columnSpan;
    };
    const auto validRectangle = [&](const CellCoordinateV1& value) noexcept {
        return IsValid(value) && value.rowSpan-1 <= maximum-value.row1 &&
            value.columnSpan-1 <= maximum-value.column1;
    };
    const auto overlaps = [](const CellCoordinateV1& left,
                             const CellCoordinateV1& right) noexcept {
        const std::uint64_t leftRowLast=left.row1+left.rowSpan-1;
        const std::uint64_t rightRowLast=right.row1+right.rowSpan-1;
        const std::uint64_t leftColumnLast=left.column1+left.columnSpan-1;
        const std::uint64_t rightColumnLast=right.column1+right.columnSpan-1;
        return left.row1<=rightRowLast && right.row1<=leftRowLast &&
            left.column1<=rightColumnLast && right.column1<=leftColumnLast;
    };
    for (std::size_t left = 0; left != transitions.size(); ++left) {
        for (std::size_t right = left + 1; right != transitions.size(); ++right) {
            if (transitions[left].action == transitions[right].action &&
                EqualUuid(transitions[left].oldCell,transitions[right].oldCell) &&
                EqualUuid(transitions[left].client,transitions[right].client) &&
                sameCoordinate(transitions[left].after,transitions[right].after)) return false;
        }
    }
    std::vector<NodeId> consumedOldCells;
    std::vector<NodeId> clonedOldCells;
    std::vector<ClientId> insertedClients;
    std::size_t mergeSurvivors = 0;
    std::size_t mergeAbsorbed = 0;
    std::size_t splitTopLeft = 0;
    const auto validOldCell = [&](const NodeId& id) {
        const IdentityNode* const old = Find(id);
        return old != nullptr && old->kind == NodeKind::TableCell &&
            EqualUuid(old->parent,table) && !ContainsId(tombstoned_,id) &&
            validRectangle(old->cell);
    };
    for (const CellTransition& transition : transitions) {
        if (!validRectangle(transition.after) && transition.action != CellAction::Delete &&
            transition.action != CellAction::MergeAbsorbed) return false;
        switch (transition.action) {
        case CellAction::Retain:
        case CellAction::MergeSurvivor:
        case CellAction::MergeAbsorbed:
        case CellAction::SplitTopLeft:
        case CellAction::Delete:
            if (!validOldCell(transition.oldCell) ||
                ContainsId(consumedOldCells,transition.oldCell)) return false;
            consumedOldCells.push_back(transition.oldCell);
            if (transition.action == CellAction::MergeSurvivor) ++mergeSurvivors;
            if (transition.action == CellAction::MergeAbsorbed) ++mergeAbsorbed;
            if (transition.action == CellAction::SplitTopLeft) ++splitTopLeft;
            break;
        case CellAction::Insert:
            if (!IsRfc4122V4(transition.client) || ContainsId(clientIds_,transition.client) ||
                ContainsId(insertedClients,transition.client)) return false;
            insertedClients.push_back(transition.client);
            break;
        case CellAction::SplitNew:
            if (!validOldCell(transition.oldCell)) return false;
            break;
        case CellAction::Clone:
            if (!validOldCell(transition.oldCell) ||
                ContainsId(clonedOldCells,transition.oldCell)) return false;
            clonedOldCells.push_back(transition.oldCell);
            break;
        }
    }
    if ((mergeAbsorbed != 0 && mergeSurvivors != 1) || mergeSurvivors > 1 ||
        splitTopLeft > 1) return false;
    bool previousCoordinatePresent=false;
    CellCoordinateV1 previousCoordinate{};
    for (const CellTransition& transition : transitions) {
        CellCoordinateV1 coordinate=transition.after;
        if (transition.action==CellAction::Delete ||
            transition.action==CellAction::MergeAbsorbed) {
            const IdentityNode* const old=Find(transition.oldCell);
            if (old==nullptr) return false;
            coordinate=old->cell;
        }
        if (previousCoordinatePresent && !coordinateLess(previousCoordinate,coordinate)) return false;
        previousCoordinate=coordinate;
        previousCoordinatePresent=true;
    }
    if (mergeSurvivors==1) {
        const CellTransition* survivor=nullptr;
        std::uint64_t minimumRow=maximum;
        std::uint64_t minimumColumn=maximum;
        std::uint64_t maximumRowLast=0;
        std::uint64_t maximumColumnLast=0;
        std::uint64_t sourceArea=0;
        for (const CellTransition& transition : transitions) {
            if (transition.action!=CellAction::MergeSurvivor &&
                transition.action!=CellAction::MergeAbsorbed) continue;
            const IdentityNode* const old=Find(transition.oldCell);
            if (old==nullptr || !validRectangle(old->cell) ||
                old->cell.rowSpan>maximum/old->cell.columnSpan) return false;
            if (transition.action==CellAction::MergeSurvivor) survivor=&transition;
            minimumRow=(std::min)(minimumRow,old->cell.row1);
            minimumColumn=(std::min)(minimumColumn,old->cell.column1);
            maximumRowLast=(std::max)(maximumRowLast,old->cell.row1+old->cell.rowSpan-1);
            maximumColumnLast=(std::max)(maximumColumnLast,old->cell.column1+old->cell.columnSpan-1);
            const std::uint64_t area=old->cell.rowSpan*old->cell.columnSpan;
            if (sourceArea>maximum-area) return false;
            sourceArea+=area;
        }
        if (survivor==nullptr || maximumRowLast<minimumRow ||
            maximumColumnLast<minimumColumn) return false;
        const IdentityNode* const survivorOld=Find(survivor->oldCell);
        if (survivorOld==nullptr || minimumRow<survivorOld->cell.row1 ||
            minimumRow>survivorOld->cell.row1+survivorOld->cell.rowSpan-1 ||
            minimumColumn<survivorOld->cell.column1 ||
            minimumColumn>survivorOld->cell.column1+survivorOld->cell.columnSpan-1) return false;
        const CellCoordinateV1 mergedRectangle{minimumRow,minimumColumn,
            maximumRowLast-minimumRow+1,maximumColumnLast-minimumColumn+1};
        if (!sameCoordinate(survivor->after,mergedRectangle) ||
            mergedRectangle.rowSpan>maximum/mergedRectangle.columnSpan ||
            sourceArea!=mergedRectangle.rowSpan*mergedRectangle.columnSpan) return false;
    }
    std::vector<const CellTransition*> splitOutputs;
    for (const CellTransition& transition : transitions) {
        if (transition.action == CellAction::SplitTopLeft ||
            transition.action == CellAction::SplitNew) splitOutputs.push_back(&transition);
    }
    if (!splitOutputs.empty()) {
        if (splitTopLeft != 1 || splitOutputs.size() < 2 ||
            splitOutputs.front()->action != CellAction::SplitTopLeft) return false;
        const IdentityNode* const oldMergedCell = Find(splitOutputs.front()->oldCell);
        if (oldMergedCell == nullptr || !IsValid(oldMergedCell->cell)) return false;
        const CellCoordinateV1 oldRectangle = oldMergedCell->cell;
        if (splitOutputs.front()->after.row1 != oldRectangle.row1 ||
            splitOutputs.front()->after.column1 != oldRectangle.column1) return false;
        if (oldRectangle.rowSpan > maximum / oldRectangle.columnSpan) return false;
        const std::uint64_t oldArea = oldRectangle.rowSpan * oldRectangle.columnSpan;
        std::uint64_t coveredArea = 0;
        struct RelativeRectangle final {
            std::uint64_t row = 0;
            std::uint64_t column = 0;
            std::uint64_t rowSpan = 0;
            std::uint64_t columnSpan = 0;
        };
        std::vector<RelativeRectangle> relativeOutputs;
        relativeOutputs.reserve(splitOutputs.size());
        for (std::size_t index = 0; index != splitOutputs.size(); ++index) {
            const CellTransition& output = *splitOutputs[index];
            if (!EqualUuid(output.oldCell,splitOutputs.front()->oldCell) ||
                (index != 0 && !coordinateLess(splitOutputs[index-1]->after,output.after)) ||
                output.after.row1 < oldRectangle.row1 ||
                output.after.column1 < oldRectangle.column1) return false;
            const std::uint64_t rowOffset = output.after.row1 - oldRectangle.row1;
            const std::uint64_t columnOffset = output.after.column1 - oldRectangle.column1;
            if (rowOffset >= oldRectangle.rowSpan || columnOffset >= oldRectangle.columnSpan ||
                output.after.rowSpan > oldRectangle.rowSpan - rowOffset ||
                output.after.columnSpan > oldRectangle.columnSpan - columnOffset ||
                output.after.rowSpan > maximum / output.after.columnSpan) return false;
            const std::uint64_t area = output.after.rowSpan * output.after.columnSpan;
            if (coveredArea > maximum - area) return false;
            coveredArea += area;
            relativeOutputs.push_back(
                {rowOffset,columnOffset,output.after.rowSpan,output.after.columnSpan});
        }
        for (std::size_t left = 0; left != relativeOutputs.size(); ++left) {
            for (std::size_t right = left + 1; right != relativeOutputs.size(); ++right) {
                const RelativeRectangle& a = relativeOutputs[left];
                const RelativeRectangle& b = relativeOutputs[right];
                const bool rowsOverlap = a.row < b.row + b.rowSpan &&
                    b.row < a.row + a.rowSpan;
                const bool columnsOverlap = a.column < b.column + b.columnSpan &&
                    b.column < a.column + a.columnSpan;
                if (rowsOverlap && columnsOverlap) return false;
            }
        }
        if (coveredArea != oldArea) return false;
    }
    for (const CellTransition& transition : transitions) {
        if (transition.action == CellAction::SplitNew) {
            const bool hasTopLeft = std::any_of(transitions.begin(),transitions.end(),
                [&](const CellTransition& candidate) {
                    return candidate.action == CellAction::SplitTopLeft &&
                        EqualUuid(candidate.oldCell,transition.oldCell);
                });
            if (!hasTopLeft) return false;
        }
        if (transition.action == CellAction::Clone) {
            const bool sourceDeleted = std::any_of(transitions.begin(),transitions.end(),
                [&](const CellTransition& candidate) {
                    return (candidate.action == CellAction::Delete ||
                            candidate.action == CellAction::MergeAbsorbed) &&
                        EqualUuid(candidate.oldCell,transition.oldCell);
                });
            if (sourceDeleted) return false;
        }
    }
    std::vector<CellCoordinateV1> resultingCells;
    for (const IdentityNode& node : nodes_) {
        if (node.kind!=NodeKind::TableCell || !EqualUuid(node.parent,table) ||
            ContainsId(tombstoned_,node.id)) continue;
        const auto transition=std::find_if(transitions.begin(),transitions.end(),
            [&](const CellTransition& candidate) {
                return (candidate.action==CellAction::Retain ||
                        candidate.action==CellAction::Delete ||
                        candidate.action==CellAction::MergeSurvivor ||
                        candidate.action==CellAction::MergeAbsorbed ||
                        candidate.action==CellAction::SplitTopLeft) &&
                    EqualUuid(candidate.oldCell,node.id);
            });
        if (transition==transitions.end()) resultingCells.push_back(node.cell);
        else if (transition->action==CellAction::Retain ||
                transition->action==CellAction::MergeSurvivor ||
                transition->action==CellAction::SplitTopLeft)
            resultingCells.push_back(transition->after);
    }
    for (const CellTransition& transition : transitions) {
        if (transition.action==CellAction::Insert ||
            transition.action==CellAction::SplitNew ||
            transition.action==CellAction::Clone)
            resultingCells.push_back(transition.after);
    }
    for (const CellCoordinateV1& cell : resultingCells)
        if (!validRectangle(cell)) return false;
    for (std::size_t left=0; left!=resultingCells.size(); ++left)
        for (std::size_t right=left+1; right!=resultingCells.size(); ++right)
            if (overlaps(resultingCells[left],resultingCells[right])) return false;
    if (resultingCells.empty()) return false;
    std::uint64_t maximumRowLast=0;
    std::uint64_t maximumColumnLast=0;
    std::uint64_t physicalArea=0;
    for (const CellCoordinateV1& cell : resultingCells) {
        maximumRowLast=(std::max)(maximumRowLast,cell.row1+cell.rowSpan-1);
        maximumColumnLast=(std::max)(maximumColumnLast,cell.column1+cell.columnSpan-1);
        if (cell.rowSpan>maximum/cell.columnSpan) return false;
        const std::uint64_t area=cell.rowSpan*cell.columnSpan;
        if (physicalArea>maximum-area) return false;
        physicalArea+=area;
    }
    if (maximumRowLast>maximum/maximumColumnLast ||
        physicalArea!=maximumRowLast*maximumColumnLast) return false;

    std::vector<NodeId> reserved = ReceiptReservedIds(*receipt);
    for (const ClientId& client : insertedClients) reserved.push_back(client);
    std::vector<NodeId> minted;
    for (const CellTransition& transition : transitions) {
        if (transition.action == CellAction::Insert ||
            transition.action == CellAction::SplitNew ||
            transition.action == CellAction::Clone) {
            NodeId id{};
            const MutationResult mint=MintUnused(reserved,&id);
            if (mint!=MutationResult::Applied) { lastResult_=mint; return false; }
            reserved.push_back(id);
            minted.push_back(id);
        }
    }
    std::size_t mintedIndex = 0;
    for (const CellTransition& transition : transitions) {
        IdentityNode* old = Find(transition.oldCell);
        switch (transition.action) {
        case CellAction::Retain:
        case CellAction::MergeSurvivor:
        case CellAction::SplitTopLeft:
            if (old == nullptr || old->kind != NodeKind::TableCell || !EqualUuid(old->parent, table)) return false;
            old->cell = transition.after;
            receipt->remaps.push_back(Remap(old->id, &old->id, RemapDisposition::Retained,
                transition.action == CellAction::MergeSurvivor ? RemapReason::Merge :
                transition.action == CellAction::SplitTopLeft ? RemapReason::TableCellSplit :
                RemapReason::IdentityRetained));
            break;
        case CellAction::Delete:
        case CellAction::MergeAbsorbed: {
            if (old == nullptr || old->kind != NodeKind::TableCell ||
                !Tombstone(old->id, transition.action == CellAction::Delete ?
                    TombstoneReason::Delete : TombstoneReason::MergeAbsorbed, receipt)) return false;
            receipt->remaps.push_back(Remap(old->id, nullptr, RemapDisposition::Tombstoned,
                transition.action == CellAction::Delete ? RemapReason::Delete : RemapReason::Merge));
            break;
        }
        case CellAction::Insert:
        case CellAction::SplitNew:
        case CellAction::Clone: {
            const NodeId id = minted[mintedIndex++];
            if (transition.action == CellAction::Insert) clientIds_.push_back(transition.client);
            nodes_.push_back({id, table, NodeKind::TableCell, transition.after});
            const Uuid128& source = transition.action == CellAction::Insert ? transition.client : transition.oldCell;
            const RemapReason reason = transition.action == CellAction::Insert ? RemapReason::ClientInsert :
                transition.action == CellAction::SplitNew ? RemapReason::TableCellSplit : RemapReason::Clone;
            receipt->remaps.push_back(Remap(source, &id, RemapDisposition::New, reason));
            break;
        }
        }
    }
    nodes_.erase(std::remove_if(nodes_.begin(), nodes_.end(), [&](const IdentityNode& node) {
        return ContainsId(tombstoned_, node.id);
    }), nodes_.end());
    RememberReceiptIds(*receipt);
    lastResult_=MutationResult::Applied;
    return true;
}

bool ReconcileChildren(const NodeId& mappedParent,
    const std::vector<ReconcileCandidate>& existing,
    const std::vector<ReconcileCandidate>& fresh, const ReconcileMode mode,
    const UuidSource source, const std::vector<NodeId>& forbiddenTombstones,
    ReconcileResult* const result, const std::uint64_t semanticRevision) noexcept {
    if (result == nullptr || !IsRfc4122V4(mappedParent)) return false;
    ReconcileResult staged{};
    std::vector<bool> oldUsed(existing.size(), false);
    std::vector<bool> freshUsed(fresh.size(), false);
    std::vector<bool> oldAmbiguous(existing.size(), false);
    std::vector<bool> freshAmbiguous(fresh.size(), false);
    auto mint = [&](const ReconcileCandidate& candidate, NodeId* const id) {
        std::vector<Uuid128> forbidden = forbiddenTombstones;
        forbidden.push_back(mappedParent);
        for (const ReconcileCandidate& old : existing) forbidden.push_back(old.id);
        for (const ReconciledNode& assigned : staged.nodes) forbidden.push_back(assigned.id);
        return MintReconciledUuid(source,candidate,forbidden,id) == MintResult::Minted;
    };
    if (mode == ReconcileMode::AuthorizedEqualRoot) {
        if (existing.size()!=fresh.size()) return false;
        const auto canonicalLess=[](const ReconcileCandidate& left,
                                    const ReconcileCandidate& right) noexcept {
            if (left.partition!=right.partition) return left.partition<right.partition;
            if (left.kind!=right.kind)
                return static_cast<std::uint16_t>(left.kind)<
                    static_cast<std::uint16_t>(right.kind);
            return left.ordinal<right.ordinal;
        };
        std::vector<NodeId> oldIds;
        std::vector<NodeId> freshIds;
        std::vector<NodeId> mappedAuthorityIds{mappedParent};
        oldIds.reserve(existing.size());
        freshIds.reserve(fresh.size());
        for (std::size_t index=0; index!=existing.size(); ++index) {
            const ReconcileCandidate& old=existing[index];
            const ReconcileCandidate& now=fresh[index];
            if (!IsRfc4122V4(old.id) || ContainsId(forbiddenTombstones,old.id) ||
                ContainsId(oldIds,old.id) || !IsRfc4122V4(now.id) ||
                ContainsId(forbiddenTombstones,now.id) || ContainsId(freshIds,now.id) ||
                old.kind!=now.kind || old.partition!=now.partition ||
                old.ordinal!=now.ordinal ||
                !EqualHash(old.canonicalPayload,now.canonicalPayload) ||
                old.mappedAuthorityIds.size()!=now.mappedAuthorityIds.size()) return false;
            std::vector<NodeId> pairAuthorities;
            for (std::size_t authority=0; authority!=old.mappedAuthorityIds.size(); ++authority) {
                if (!IsRfc4122V4(old.mappedAuthorityIds[authority]) ||
                    !EqualUuid(old.mappedAuthorityIds[authority],now.mappedAuthorityIds[authority]) ||
                    ContainsId(forbiddenTombstones,old.mappedAuthorityIds[authority]) ||
                    ContainsId(pairAuthorities,old.mappedAuthorityIds[authority])) return false;
                pairAuthorities.push_back(old.mappedAuthorityIds[authority]);
                if (!ContainsId(mappedAuthorityIds,old.mappedAuthorityIds[authority]))
                    mappedAuthorityIds.push_back(old.mappedAuthorityIds[authority]);
            }
            if (index!=0 &&
                (!canonicalLess(existing[index-1],old) ||
                 !canonicalLess(fresh[index-1],now))) return false;
            oldIds.push_back(old.id);
            freshIds.push_back(now.id);
        }
        for (const NodeId& oldId : oldIds)
            if (ContainsId(freshIds,oldId) || ContainsId(mappedAuthorityIds,oldId)) return false;
        for (const NodeId& freshId : freshIds)
            if (ContainsId(oldIds,freshId) || ContainsId(mappedAuthorityIds,freshId)) return false;
        for (std::size_t index=0; index!=existing.size(); ++index) {
            staged.nodes.push_back({index,existing[index].id,true});
            staged.receipt.remaps.push_back(Remap(existing[index].id,&existing[index].id,
                RemapDisposition::Retained,RemapReason::SaveReopenEqual));
        }
        *result=std::move(staged);
        return true;
    }
    auto keyEqual = [](const ReconcileCandidate& old, const ReconcileCandidate& now) {
        if (old.kind != now.kind || old.partition != now.partition) return false;
        if (IsControlKind(old.kind) || old.kind == NodeKind::Story)
            return old.primaryKey == now.primaryKey && old.secondaryKey == now.secondaryKey;
        if (old.kind == NodeKind::TableCell) {
            if (!old.primaryKey.empty() && old.primaryKey == now.primaryKey) return true;
            return old.topologyUnchanged && now.topologyUnchanged &&
                !old.secondaryKey.empty() && old.secondaryKey == now.secondaryKey;
        }
        return EqualHash(old.fingerprint, now.fingerprint);
    };
    for (std::size_t freshIndex = 0; freshIndex != fresh.size(); ++freshIndex) {
        std::vector<std::size_t> oldMatches;
        std::size_t freshCount = 0;
        for (std::size_t oldIndex = 0; oldIndex != existing.size(); ++oldIndex)
            if (keyEqual(existing[oldIndex], fresh[freshIndex])) oldMatches.push_back(oldIndex);
        for (const ReconcileCandidate& candidate : fresh)
            if (keyEqual(existing.empty() ? fresh[freshIndex] :
                (oldMatches.empty() ? fresh[freshIndex] : existing[oldMatches.front()]), candidate)) ++freshCount;
        if (oldMatches.size() == 1 && freshCount == 1 && !oldUsed[oldMatches.front()]) {
            const std::size_t oldIndex = oldMatches.front();
            oldUsed[oldIndex] = true;
            freshUsed[freshIndex] = true;
            staged.nodes.push_back({freshIndex, existing[oldIndex].id, true});
            staged.receipt.remaps.push_back(Remap(existing[oldIndex].id, &existing[oldIndex].id,
                RemapDisposition::Retained, RemapReason::ExternalUnique));
        } else if (oldMatches.size() > 1 || freshCount > 1) {
            freshAmbiguous[freshIndex] = true;
            for (const std::size_t oldIndex : oldMatches) oldAmbiguous[oldIndex] = true;
        }
    }
    // Between retained anchors, one unmatched old and one unmatched fresh in the
    // same paragraph/run partition form the sole conservative gap exception.
    std::vector<std::uint64_t> partitions;
    for (const ReconcileCandidate& candidate : existing) partitions.push_back(candidate.partition);
    for (const ReconcileCandidate& candidate : fresh) partitions.push_back(candidate.partition);
    std::sort(partitions.begin(), partitions.end());
    partitions.erase(std::unique(partitions.begin(), partitions.end()), partitions.end());
    for (const std::uint64_t partition : partitions) {
        std::vector<std::size_t> oldGap;
        std::vector<std::size_t> freshGap;
        for (std::size_t index = 0; index != existing.size(); ++index)
            if (!oldUsed[index] && !oldAmbiguous[index] && existing[index].partition == partition &&
                (existing[index].kind == NodeKind::Paragraph || existing[index].kind == NodeKind::CharacterRun)) oldGap.push_back(index);
        for (std::size_t index = 0; index != fresh.size(); ++index)
            if (!freshUsed[index] && !freshAmbiguous[index] && fresh[index].partition == partition &&
                (fresh[index].kind == NodeKind::Paragraph || fresh[index].kind == NodeKind::CharacterRun)) freshGap.push_back(index);
        if (oldGap.size() == 1 && freshGap.size() == 1 && existing[oldGap[0]].kind == fresh[freshGap[0]].kind) {
            oldUsed[oldGap[0]] = true;
            freshUsed[freshGap[0]] = true;
            staged.nodes.push_back({freshGap[0], existing[oldGap[0]].id, true});
            staged.receipt.remaps.push_back(Remap(existing[oldGap[0]].id, &existing[oldGap[0]].id,
                RemapDisposition::Retained, RemapReason::ExternalUnique));
        }
    }
    for (std::size_t index = 0; index != fresh.size(); ++index) {
        if (freshUsed[index]) continue;
        NodeId id{};
        if (!mint(fresh[index],&id)) return false;
        staged.nodes.push_back({index, id, false});
        staged.receipt.remaps.push_back(Remap(id, &id, RemapDisposition::New,
            freshAmbiguous[index] ? RemapReason::Ambiguous : RemapReason::ExternalUnique));
    }
    for (std::size_t index = 0; index != existing.size(); ++index) {
        if (oldUsed[index]) continue;
        staged.receipt.tombstones.push_back(
            {existing[index].id, semanticRevision, TombstoneReason::ExternalUnmatched});
        staged.receipt.remaps.push_back(Remap(existing[index].id, nullptr,
            oldAmbiguous[index] ? RemapDisposition::Ambiguous : RemapDisposition::Tombstoned,
            oldAmbiguous[index] ? RemapReason::Ambiguous : RemapReason::Delete));
    }
    std::sort(staged.nodes.begin(), staged.nodes.end(), [](const ReconciledNode& left, const ReconciledNode& right) {
        return left.freshIndex < right.freshIndex;
    });
    *result = std::move(staged);
    return true;
}

} // namespace hancom::graph::identity

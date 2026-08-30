#pragma once

#include "DocumentGraphSchema.h"

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace hancom::graph::identity {

using GraphId = Uuid128;
using DocumentSessionId = Uuid128;
using ClientId = Uuid128;
inline constexpr unsigned kUuidCollisionAttempts = 16;

bool EqualUuid(const Uuid128& left, const Uuid128& right) noexcept;
bool IsZeroUuid(const Uuid128& value) noexcept;
bool IsRfc4122V4(const Uuid128& value) noexcept;
bool ParseCanonicalUuid(const wchar_t* text, Uuid128* value) noexcept;
std::wstring FormatCanonicalUuid(const Uuid128& value);

using FillRandomBytes = bool (*)(void* context, std::uint8_t* bytes,
                                 std::uint32_t count) noexcept;
struct UuidSource final {
    void* context = nullptr;
    FillRandomBytes fill = nullptr;
};
bool SystemRandomBytes(void*, std::uint8_t* bytes, std::uint32_t count) noexcept;
bool MintUuidV4(const UuidSource& source, Uuid128* value) noexcept;

struct DocumentLocator final {
    std::uint64_t documentId = 0;
    std::uintptr_t hwnd = 0;
    std::wstring path{};
};
bool NormalizeDocumentPath(const std::wstring& path, std::wstring* normalized) noexcept;
bool LocatorMatches(const DocumentLocator& expected,
                    const DocumentLocator& presented) noexcept;

enum class AliasStatus : std::uint8_t {
    Ok = 0, InvalidUuidSource, UnknownCanonical, UnknownAlias,
    LocatorMismatch, AliasAlreadyBound, CollisionExhausted,
};
class SessionAliasRegistry final {
public:
    explicit SessionAliasRegistry(UuidSource source) noexcept;
    AliasStatus Open(std::uint64_t canonicalIdentity, std::uint64_t aliasIdentity,
                     const DocumentLocator& locator,
                     DocumentSessionId* session) noexcept;
    AliasStatus AddAlias(std::uint64_t canonicalIdentity,
                         std::uint64_t aliasIdentity) noexcept;
    AliasStatus Resolve(std::uint64_t aliasIdentity,
                        const DocumentLocator& locator,
                        DocumentSessionId* session) const noexcept;
    bool Close(std::uint64_t canonicalIdentity) noexcept;
    void BridgeRestart() noexcept;

private:
    struct Session final {
        std::uint64_t canonicalIdentity = 0;
        DocumentSessionId id{};
        DocumentLocator locator{};
        std::vector<std::uint64_t> aliases{};
    };
    UuidSource source_{};
    std::vector<Session> sessions_{};
};

struct GraphVersionV1 final {
    std::uint16_t schema = kSchemaVersionV1;
    std::uint64_t profileBits = 0;
    bool semanticCertified = false;
    bool layoutPresent = false;
    DocumentSessionId documentSessionId{};
    GraphId graphId{};
    std::uint64_t semanticRevision = 0;
    std::uint64_t layoutRevision = 0;
    std::uint64_t locatorEpoch = 0;
    Sha256 observedSemanticRoot{};
    Sha256 layoutRoot{};
    Sha256 captureRoot{};
};
using SerializedGraphVersionV1 = std::array<std::uint8_t, kGraphVersionBytesV1>;

enum class GraphVersionError : std::uint8_t {
    None = 0, BadSize, BadSchema, BadProfiles, BadBoolean, ReservedNonzero,
    BadDocumentSessionId, BadGraphId, BadRevision, BadLayoutRoot,
    ReservedIdentityCollision,
};
GraphVersionError ValidateGraphVersion(const GraphVersionV1& version) noexcept;
GraphVersionError SerializeGraphVersion(const GraphVersionV1& version,
                                        SerializedGraphVersionV1* bytes) noexcept;
GraphVersionError DeserializeGraphVersion(const std::uint8_t* bytes,
                                          std::uint64_t size,
                                          GraphVersionV1* version) noexcept;
bool FullVersionCas(const GraphVersionV1& expected,
                    const GraphVersionV1& current) noexcept;
bool CursorVersionCas(const GraphVersionV1& expected,
                      const GraphVersionV1& current) noexcept;
bool PatchVersionCas(const GraphVersionV1& expected,
                     const GraphVersionV1& current) noexcept;

struct PublishIdentity final {
    std::uint64_t profileBits = 0;
    bool semanticCertified = false;
    bool layoutPresent = false;
    Sha256 semanticRoot{};
    Sha256 layoutRoot{};
    Sha256 captureRoot{};
};
enum class PublishOutcome : std::uint8_t {
    Success = 0, NoOp, Failed, ExactRollback, Save, CaretOnly, ModifiedFlagOnly,
};
enum class LifecycleResult : std::uint8_t {
    Applied = 0, Unchanged, Inactive, InvalidPublish, CounterOverflow,
};
enum class LineageResult : std::uint8_t {
    Started = 0, InvalidInput, CollisionExhausted, IdentityCollision,
};
enum class ReopenResult : std::uint8_t {
    Retained = 0, RejectedUncertified, RejectedDifferentRoot,
    RejectedCanonicalPairing, RejectedCounterOverflow,
    RejectedIdentityCollision,
};
class GraphLifecycle final {
public:
    explicit GraphLifecycle(UuidSource source) noexcept;
    LineageResult StartNewLineage(const DocumentSessionId& session,
                                  const PublishIdentity& publish) noexcept;
    bool ReserveIdentityIds(const std::vector<Uuid128>& ids) noexcept;
    GraphVersionError Resume(const GraphVersionV1& version) noexcept;
    LifecycleResult ApplyPublish(const PublishIdentity& publish,
                                 PublishOutcome outcome) noexcept;
    ReopenResult AuthorizedReopen(const DocumentSessionId& newSession,
                                  const PublishIdentity& fresh,
                                  bool completeGraph,
                                  bool exactCanonicalTreeAndPayload) noexcept;
    const GraphVersionV1& version() const noexcept { return version_; }

private:
    UuidSource source_{};
    GraphVersionV1 version_{};
    bool active_ = false;
    std::vector<Uuid128> usedIdentityIds_{};
};

struct RemapEntry final {
    Uuid128 source{};
    bool targetPresent = false;
    NodeId target{};
    RemapDisposition disposition = RemapDisposition::Retained;
    RemapReason reason = RemapReason::IdentityRetained;
};
struct TombstoneEntry final {
    NodeId node{};
    std::uint64_t semanticRevision = 0;
    TombstoneReason reason = TombstoneReason::Delete;
};
struct IdentityReceipt final {
    std::vector<RemapEntry> remaps{};
    std::vector<TombstoneEntry> tombstones{};
};
struct IdentityNode final {
    NodeId id{};
    NodeId parent{};
    NodeKind kind = NodeKind::Document;
    CellCoordinateV1 cell{};
};
enum class MutationResult : std::uint8_t {
    Applied = 0, InvalidInput, CollisionExhausted,
};
class MutationIdentity final {
public:
    explicit MutationIdentity(UuidSource source,
                              std::uint64_t semanticRevision = 1) noexcept;
    bool Seed(const std::vector<IdentityNode>& nodes) noexcept;
    bool ReserveIdentityIds(const std::vector<Uuid128>& ids) noexcept;
    bool Insert(const ClientId& client, const NodeId& parent, NodeKind kind,
                IdentityReceipt* receipt) noexcept;
    bool Split(const NodeId& original, std::uint64_t pieceCount,
               IdentityReceipt* receipt) noexcept;
    bool Coalesce(const std::vector<NodeId>& orderedNodes,
                  IdentityReceipt* receipt) noexcept;
    bool MoveSubtree(const NodeId& root, const NodeId& newParent,
                     IdentityReceipt* receipt) noexcept;
    bool DeleteSubtree(const NodeId& root, IdentityReceipt* receipt) noexcept;
    bool CloneSubtree(const NodeId& root, const NodeId& newParent,
                      IdentityReceipt* receipt) noexcept;

    enum class CellAction : std::uint8_t {
        Retain = 0, Insert, Delete, MergeSurvivor, MergeAbsorbed,
        SplitTopLeft, SplitNew, Clone,
    };
    struct CellTransition final {
        CellAction action = CellAction::Retain;
        NodeId oldCell{};
        ClientId client{};
        CellCoordinateV1 after{};
    };
    bool ApplyTableCellTransitions(const NodeId& table,
        const std::vector<CellTransition>& transitions,
        IdentityReceipt* receipt) noexcept;

    const std::vector<IdentityNode>& nodes() const noexcept { return nodes_; }
    const std::vector<NodeId>& tombstonedIds() const noexcept { return tombstoned_; }
    MutationResult lastResult() const noexcept { return lastResult_; }

private:
    IdentityNode* Find(const NodeId& id) noexcept;
    const IdentityNode* Find(const NodeId& id) const noexcept;
    MutationResult MintUnused(const std::vector<NodeId>& operationReserved,
                              NodeId* id) noexcept;
    void RememberReceiptIds(const IdentityReceipt& receipt);
    bool Tombstone(const NodeId& id, TombstoneReason reason,
                   IdentityReceipt* receipt) noexcept;
    UuidSource source_{};
    std::vector<IdentityNode> nodes_{};
    std::vector<NodeId> tombstoned_{};
    std::vector<ClientId> clientIds_{};
    std::vector<Uuid128> receiptIdentityIds_{};
    std::vector<Uuid128> reservedIdentityIds_{};
    std::vector<Uuid128> allTimeIdentityIds_{};
    std::uint64_t semanticRevision_ = 1;
    MutationResult lastResult_ = MutationResult::InvalidInput;
};

enum class ReconcileMode : std::uint8_t { External = 0, AuthorizedEqualRoot };
struct ReconcileCandidate final {
    NodeId id{}; // Required for existing and authorized-reopen fresh candidates.
    NodeKind kind = NodeKind::Document;
    std::uint64_t ordinal = 0;
    Sha256 fingerprint{};
    std::wstring primaryKey{};   // CtrlID, story owner, or cell list ID.
    std::wstring secondaryKey{}; // instance ID, story role, or cell address.
    std::uint64_t partition = 0;
    bool topologyUnchanged = false;
    Sha256 canonicalPayload{};
    std::vector<NodeId> mappedAuthorityIds{}; // Ancestor/partition authorities after mapping.
};
struct ReconciledNode final {
    std::uint64_t freshIndex = 0;
    NodeId id{};
    bool retained = false;
};
struct ReconcileResult final {
    std::vector<ReconciledNode> nodes{};
    IdentityReceipt receipt{};
};
bool ReconcileChildren(const NodeId& mappedParent,
    const std::vector<ReconcileCandidate>& existing,
    const std::vector<ReconcileCandidate>& fresh,
    ReconcileMode mode, UuidSource source,
    const std::vector<NodeId>& forbiddenTombstones,
    ReconcileResult* result,
    std::uint64_t semanticRevision = 1) noexcept;

} // namespace hancom::graph::identity

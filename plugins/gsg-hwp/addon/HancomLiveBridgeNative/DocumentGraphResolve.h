#pragma once

#include "DocumentGraphIdentity.h"
#include "DocumentGraphTypes.h"

#include <cstdint>
#include <string>
#include <vector>

namespace hancom::graph::resolve {

enum class LocatorKind : std::uint8_t {
    Paragraph = 0,
    Run = 1,
    Control = 2,
    Cell = 3,
};

enum class Error : std::uint8_t {
    None = 0,
    BadUuid,
    Uncertified,
    BadVersion,
    Missing,
    Stale,
    Ambiguous,
    Roundtrip,
    ClientLocal,
    Partial,
    Lineage,
};

enum class RemapKind : std::uint8_t {
    Keep = 0,
    Insert = 1,
    Split = 2,
    Coalesce = 3,
    Delete = 4,
    Ordinal = 5,
};

struct NativeLocator final {
    LocatorKind kind = LocatorKind::Paragraph;
    std::uint64_t section = 0;
    std::uint64_t paragraph = 0;
    std::uint64_t run = 0;
    std::uint64_t control = 0;
    std::uint64_t row = 0;
    std::uint64_t column = 0;
    std::uint64_t siblingOrdinal = 0;
    std::uint64_t locatorEpoch = 0;
    std::uint64_t semanticRevision = 0;
};

struct GraphNode final {
    NodeId id{};
    NativeLocator locator{};
};

struct GraphSnapshot final {
    identity::GraphVersionV1 version{};
    std::vector<GraphNode> nodes{};
    std::wstring path{};
    std::uint64_t documentId = 0;
};

struct RemapRule final {
    RemapKind kind = RemapKind::Keep;
    NodeId source{};
    NodeId target{};
    NodeId extra{};
};

struct RemapPlan final {
    std::vector<identity::RemapEntry> mapping{};
    std::vector<identity::ClientId> reservedClientIds{};
};

NativeLocator BindLocator(const NativeLocator& locator,
                          const identity::GraphVersionV1& version) noexcept;
Error ResolveNode(const GraphSnapshot& snapshot, const NodeId& id,
                  GraphNode* node) noexcept;
Error ResolveLocator(const GraphSnapshot& snapshot, const NativeLocator& locator,
                     GraphNode* node) noexcept;
Error Roundtrip(const GraphSnapshot& snapshot, const NodeId& id,
                GraphNode* node) noexcept;
Error ReserveClientIds(const std::vector<identity::ClientId>& ids,
                       std::vector<identity::ClientId>* reserved) noexcept;
Error RemapIdentities(const GraphSnapshot& before, const GraphSnapshot& after,
                      const std::vector<RemapRule>& rules,
                      const std::vector<identity::ClientId>& reservedClientIds,
                      RemapPlan* plan) noexcept;
Error RemapAfterSaveReopen(const GraphSnapshot& before,
                           const GraphSnapshot& after,
                           RemapPlan* plan) noexcept;

}

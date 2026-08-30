#include "DocumentGraphResolve.h"

#include <algorithm>
#include <utility>

namespace hancom::graph::resolve {
namespace {

using identity::ClientId;
using identity::EqualUuid;
using identity::GraphVersionV1;
using identity::IsRfc4122V4;

struct LocatorKey final {
    LocatorKind kind = LocatorKind::Paragraph;
    std::uint64_t section = 0;
    std::uint64_t paragraph = 0;
    std::uint64_t run = 0;
    std::uint64_t control = 0;
    std::uint64_t row = 0;
    std::uint64_t column = 0;
    std::uint64_t siblingOrdinal = 0;
};

bool SameLocator(const NativeLocator& left, const NativeLocator& right) noexcept {
    return left.kind == right.kind && left.section == right.section &&
        left.paragraph == right.paragraph && left.run == right.run &&
        left.control == right.control && left.row == right.row &&
        left.column == right.column && left.siblingOrdinal == right.siblingOrdinal;
}

LocatorKey KeyOf(const NativeLocator& locator) noexcept {
    return LocatorKey{
        locator.kind,
        locator.section,
        locator.paragraph,
        locator.run,
        locator.control,
        locator.row,
        locator.column,
        locator.siblingOrdinal,
    };
}

bool SameKey(const LocatorKey& left, const LocatorKey& right) noexcept {
    return left.kind == right.kind && left.section == right.section &&
        left.paragraph == right.paragraph && left.run == right.run &&
        left.control == right.control && left.row == right.row &&
        left.column == right.column && left.siblingOrdinal == right.siblingOrdinal;
}

bool SameSha(const Sha256& left, const Sha256& right) noexcept {
    return left.bytes == right.bytes;
}

Error RequireUuid(const Uuid128& value) noexcept {
    return IsRfc4122V4(value) ? Error::None : Error::BadUuid;
}

Error RequireCertified(const GraphVersionV1& version) noexcept {
    if (!version.semanticCertified) {
        return Error::Uncertified;
    }
    if (RequireUuid(version.documentSessionId) != Error::None ||
        RequireUuid(version.graphId) != Error::None ||
        EqualUuid(version.documentSessionId, version.graphId)) {
        return Error::BadVersion;
    }
    return Error::None;
}

bool Stale(const NativeLocator& locator, const GraphVersionV1& version) noexcept {
    return locator.locatorEpoch != version.locatorEpoch ||
        locator.semanticRevision != version.semanticRevision;
}

}

NativeLocator BindLocator(const NativeLocator& locator,
                          const GraphVersionV1& version) noexcept {
    NativeLocator bound = locator;
    bound.locatorEpoch = version.locatorEpoch;
    bound.semanticRevision = version.semanticRevision;
    return bound;
}

Error ResolveNode(const GraphSnapshot& snapshot, const NodeId& id,
                  GraphNode* node) noexcept {
    if (node == nullptr) {
        return Error::Missing;
    }
    const Error certified = RequireCertified(snapshot.version);
    if (certified != Error::None) {
        return certified;
    }
    const Error uuid = RequireUuid(id);
    if (uuid != Error::None) {
        return uuid;
    }
    for (const GraphNode& candidate : snapshot.nodes) {
        if (!EqualUuid(candidate.id, id)) {
            continue;
        }
        if (Stale(candidate.locator, snapshot.version)) {
            return Error::Stale;
        }
        *node = candidate;
        return Error::None;
    }
    return Error::Missing;
}

Error ResolveLocator(const GraphSnapshot& snapshot, const NativeLocator& locator,
                     GraphNode* node) noexcept {
    if (node == nullptr) {
        return Error::Missing;
    }
    const Error certified = RequireCertified(snapshot.version);
    if (certified != Error::None) {
        return certified;
    }
    if (Stale(locator, snapshot.version)) {
        return Error::Stale;
    }
    const GraphNode* match = nullptr;
    for (const GraphNode& candidate : snapshot.nodes) {
        if (!SameLocator(candidate.locator, locator)) {
            continue;
        }
        if (match != nullptr) {
            return Error::Ambiguous;
        }
        match = &candidate;
    }
    if (match == nullptr) {
        return Error::Missing;
    }
    *node = *match;
    return Error::None;
}

Error Roundtrip(const GraphSnapshot& snapshot, const NodeId& id,
                GraphNode* node) noexcept {
    GraphNode resolved{};
    const Error first = ResolveNode(snapshot, id, &resolved);
    if (first != Error::None) {
        return first;
    }
    GraphNode located{};
    const Error second = ResolveLocator(snapshot, resolved.locator, &located);
    if (second != Error::None) {
        return second;
    }
    if (!EqualUuid(located.id, resolved.id)) {
        return Error::Roundtrip;
    }
    if (node != nullptr) {
        *node = located;
    }
    return Error::None;
}

Error ReserveClientIds(const std::vector<ClientId>& ids,
                       std::vector<ClientId>* reserved) noexcept {
    if (reserved == nullptr) {
        return Error::Missing;
    }
    reserved->clear();
    for (const ClientId& id : ids) {
        if (RequireUuid(id) != Error::None) {
            return Error::BadUuid;
        }
        for (const ClientId& seen : *reserved) {
            if (EqualUuid(seen, id)) {
                return Error::ClientLocal;
            }
        }
        reserved->push_back(id);
    }
    return Error::None;
}

Error RemapIdentities(const GraphSnapshot& before, const GraphSnapshot& after,
                      const std::vector<RemapRule>& rules,
                      const std::vector<ClientId>& reservedClientIds,
                      RemapPlan* plan) noexcept {
    if (plan == nullptr) {
        return Error::Missing;
    }
    const Error beforeOk = RequireCertified(before.version);
    if (beforeOk != Error::None) {
        return beforeOk;
    }
    const Error afterOk = RequireCertified(after.version);
    if (afterOk != Error::None) {
        return afterOk;
    }
    plan->mapping.clear();
    plan->reservedClientIds.clear();
    const Error reserved = ReserveClientIds(reservedClientIds, &plan->reservedClientIds);
    if (reserved != Error::None) {
        return reserved;
    }
    for (const ClientId& client : plan->reservedClientIds) {
        for (const GraphNode& node : after.nodes) {
            if (EqualUuid(node.id, client)) {
                return Error::ClientLocal;
            }
        }
    }

    std::vector<NodeId> claimed;
    std::vector<std::pair<NodeId, NodeId>> mapping;
    const auto hasId = [](const std::vector<NodeId>& ids, const NodeId& id) {
        return std::any_of(ids.begin(), ids.end(), [&](const NodeId& item) {
            return EqualUuid(item, id);
        });
    };
    const auto mappedTarget = [&](const NodeId& source, NodeId* target) {
        for (const auto& entry : mapping) {
            if (EqualUuid(entry.first, source)) {
                *target = entry.second;
                return true;
            }
        }
        return false;
    };
    const auto assign = [&](const NodeId& source, const NodeId& target) -> Error {
        if (RequireUuid(source) != Error::None || RequireUuid(target) != Error::None) {
            return Error::BadUuid;
        }
        NodeId existing{};
        if (mappedTarget(source, &existing) && !EqualUuid(existing, target)) {
            return Error::Partial;
        }
        if (!mappedTarget(source, &existing)) {
            mapping.emplace_back(source, target);
        }
        if (!hasId(claimed, source)) {
            claimed.push_back(source);
        }
        if (!hasId(claimed, target)) {
            claimed.push_back(target);
        }
        return Error::None;
    };

    for (const RemapRule& rule : rules) {
        if (rule.kind == RemapKind::Delete) {
            if (RequireUuid(rule.source) != Error::None) {
                return Error::BadUuid;
            }
            NodeId existing{};
            if (mappedTarget(rule.source, &existing)) {
                return Error::Partial;
            }
            if (!hasId(claimed, rule.source)) {
                claimed.push_back(rule.source);
            }
            continue;
        }
        if (rule.kind == RemapKind::Insert) {
            if (RequireUuid(rule.target) != Error::None) {
                return Error::BadUuid;
            }
            const bool present = std::any_of(
                after.nodes.begin(), after.nodes.end(),
                [&](const GraphNode& node) { return EqualUuid(node.id, rule.target); });
            if (!present) {
                return Error::Missing;
            }
            if (!hasId(claimed, rule.target)) {
                claimed.push_back(rule.target);
            }
            continue;
        }
        if (rule.kind == RemapKind::Split) {
            const Error first = assign(rule.source, rule.target);
            if (first != Error::None) {
                return first;
            }
            if (RequireUuid(rule.extra) != Error::None) {
                return Error::BadUuid;
            }
            const bool present = std::any_of(
                after.nodes.begin(), after.nodes.end(),
                [&](const GraphNode& node) { return EqualUuid(node.id, rule.extra); });
            if (!present) {
                return Error::Missing;
            }
            if (!hasId(claimed, rule.extra)) {
                claimed.push_back(rule.extra);
            }
            continue;
        }
        if (rule.kind == RemapKind::Coalesce) {
            const Error first = assign(rule.source, rule.target);
            if (first != Error::None) {
                return first;
            }
            if (!IsRfc4122V4(rule.extra) && !identity::IsZeroUuid(rule.extra)) {
                return Error::BadUuid;
            }
            if (IsRfc4122V4(rule.extra)) {
                const Error second = assign(rule.extra, rule.target);
                if (second != Error::None) {
                    return second;
                }
            }
            continue;
        }
        const Error keep = assign(rule.source, rule.target);
        if (keep != Error::None) {
            return keep;
        }
    }

    std::vector<const GraphNode*> leftoverBefore;
    leftoverBefore.reserve(before.nodes.size());
    for (const GraphNode& node : before.nodes) {
        if (!hasId(claimed, node.id)) {
            leftoverBefore.push_back(&node);
        }
    }
    std::vector<const GraphNode*> leftoverAfter;
    leftoverAfter.reserve(after.nodes.size());
    for (const GraphNode& node : after.nodes) {
        if (!hasId(claimed, node.id)) {
            leftoverAfter.push_back(&node);
        }
    }

    for (const GraphNode* source : leftoverBefore) {
        std::vector<const GraphNode*> beforeGroup;
        std::vector<const GraphNode*> afterGroup;
        const LocatorKey key = KeyOf(source->locator);
        for (const GraphNode* candidate : leftoverBefore) {
            if (SameKey(KeyOf(candidate->locator), key)) {
                beforeGroup.push_back(candidate);
            }
        }
        for (const GraphNode* candidate : leftoverAfter) {
            if (SameKey(KeyOf(candidate->locator), key)) {
                afterGroup.push_back(candidate);
            }
        }
        if (beforeGroup.empty()) {
            continue;
        }
        if (beforeGroup.size() == afterGroup.size()) {
            for (std::size_t index = 0; index != beforeGroup.size(); ++index) {
                if (hasId(claimed, beforeGroup[index]->id)) {
                    continue;
                }
                const Error assigned =
                    assign(beforeGroup[index]->id, afterGroup[index]->id);
                if (assigned != Error::None) {
                    return assigned;
                }
            }
            continue;
        }
        if (afterGroup.empty()) {
            continue;
        }
        return Error::Ambiguous;
    }

    for (const GraphNode& node : before.nodes) {
        if (hasId(claimed, node.id)) {
            continue;
        }
        return Error::Partial;
    }

    plan->mapping.clear();
    plan->mapping.reserve(mapping.size());
    for (const auto& entry : mapping) {
        identity::RemapEntry item{};
        item.source = entry.first;
        item.targetPresent = true;
        item.target = entry.second;
        plan->mapping.push_back(item);
    }
    return Error::None;
}

Error RemapAfterSaveReopen(const GraphSnapshot& before, const GraphSnapshot& after,
                           RemapPlan* plan) noexcept {
    const Error beforeOk = RequireCertified(before.version);
    if (beforeOk != Error::None) {
        return beforeOk;
    }
    const Error afterOk = RequireCertified(after.version);
    if (afterOk != Error::None) {
        return afterOk;
    }
    if (!SameSha(before.version.observedSemanticRoot,
                 after.version.observedSemanticRoot)) {
        return Error::Lineage;
    }
    if (before.documentId != 0 && after.documentId != 0 &&
        before.documentId == after.documentId &&
        !EqualUuid(before.version.graphId, after.version.graphId)) {
        return Error::Lineage;
    }
    if (after.version.locatorEpoch < before.version.locatorEpoch) {
        return Error::Stale;
    }
    return RemapIdentities(before, after, {}, {}, plan);
}

}

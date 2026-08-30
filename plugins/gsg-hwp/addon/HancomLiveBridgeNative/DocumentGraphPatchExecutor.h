#pragma once

#include "DocumentGraphIdentity.h"
#include "DocumentGraphPatch.h"
#include "DocumentGraphTypes.h"

#include <cstdint>

namespace hancom::graph::apply {

inline constexpr std::uint16_t kPatchApplyDispid = 36;
inline constexpr std::uint16_t kPatchApplyMessageKind = 12;

enum class ApplyState : std::uint8_t {
    Applied = 2,
    NoOp = 3,
    RolledBack = 4,
    ReconcileRequired = 5,
};

enum class ApplyError : std::uint8_t {
    None = 0,
    NotValidated,
    Stale,
    Execute,
    Layout,
    Readback,
    Publish,
    History,
    ReconcileRequired,
};

enum class ApplyStep : std::uint8_t {
    Checkpoint = 0,
    Execute,
    Layout,
    Readback,
    Publish,
    History,
    Restore,
};

struct ApplyReceiptV1 final {
    Uuid128 upload{};
    ApplyState state = ApplyState::NoOp;
    identity::GraphVersionV1 version{};
    Sha256 forwardDigest{};
    Sha256 inverseDigest{};
    std::uint64_t hancomMutations = 0;
};

// COM-free seam onto the existing signed document checkpoint, action
// executor, layout settle, affected-node readback, graph publish, and edit
// history. No undo store lives here; every verb is delegated to the host.
struct ApplyHostV1 final {
    void* context = nullptr;
    // Creates the existing signed document checkpoint.
    bool (*checkpoint)(void* context) noexcept = nullptr;
    // Executes the topologically ordered adapters; reports Hancom mutations.
    bool (*execute)(void* context, const patch::DocumentPatchV1& patch,
                    std::uint64_t* mutations) noexcept = nullptr;
    // Settles layout after structural/text/table/image mutation.
    bool (*settleLayout)(void* context) noexcept = nullptr;
    // Captures the affected closure and reports the observed post-apply
    // version.
    bool (*readback)(void* context,
                     identity::GraphVersionV1* observed) noexcept = nullptr;
    // Publishes the new graph generation.
    bool (*publish)(void* context,
                    const identity::GraphVersionV1& observed) noexcept = nullptr;
    // Records the forward/inverse receipt in the existing history.
    bool (*recordHistory)(void* context, const Uuid128& upload,
                          const Sha256& forwardDigest,
                          const Sha256& inverseDigest) noexcept = nullptr;
    // Restores the checkpoint and reports the restored version.
    bool (*restore)(void* context,
                    identity::GraphVersionV1* restored) noexcept = nullptr;
};

ApplyError ApplyValidatedPatch(const Uuid128& upload,
                               const identity::GraphVersionV1& expected,
                               const patch::DocumentPatchV1& patch,
                               bool validated,
                               const ApplyHostV1& host,
                               ApplyReceiptV1* receipt) noexcept;

}

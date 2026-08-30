#include "DocumentGraphPatchExecutor.h"

#include "DocumentGraphCodec.h"

namespace hancom::graph::apply {
namespace {

bool DigestPatch(const patch::DocumentPatchV1& patch, Sha256* digest) noexcept {
    if (digest == nullptr) {
        return false;
    }
    codec::Bytes encoded;
    if (patch::EncodeCanonicalPatchV1(patch, &encoded) != patch::Error::None) {
        return false;
    }
    *digest = codec::DomainHash("HWPGRAPH\0PATCH\0V1", codec::View(encoded));
    return true;
}

ApplyError FailAndRestore(const ApplyHostV1& host,
                          const identity::GraphVersionV1& expected,
                          const Uuid128& upload,
                          const Sha256& forwardDigest,
                          const Sha256& inverseDigest,
                          const ApplyError cause,
                          ApplyReceiptV1* const receipt) noexcept {
    identity::GraphVersionV1 restored{};
    const bool restoredOk = host.restore != nullptr &&
        host.restore(host.context, &restored) &&
        identity::FullVersionCas(restored, expected);
    receipt->upload = upload;
    receipt->forwardDigest = forwardDigest;
    receipt->inverseDigest = inverseDigest;
    receipt->hancomMutations = 0;
    if (!restoredOk) {
        receipt->state = ApplyState::ReconcileRequired;
        receipt->version = expected;
        return ApplyError::ReconcileRequired;
    }
    receipt->state = ApplyState::RolledBack;
    receipt->version = restored;
    return cause;
}

}

ApplyError ApplyValidatedPatch(const Uuid128& upload,
                               const identity::GraphVersionV1& expected,
                               const patch::DocumentPatchV1& patch,
                               const bool validated,
                               const ApplyHostV1& host,
                               ApplyReceiptV1* const receipt) noexcept {
    if (receipt == nullptr) {
        return ApplyError::Readback;
    }
    *receipt = ApplyReceiptV1{};
    receipt->upload = upload;
    if (!validated) {
        return ApplyError::NotValidated;
    }
    if (!expected.semanticCertified ||
        !identity::PatchVersionCas(expected, patch.version)) {
        return ApplyError::Stale;
    }

    Sha256 forward{};
    Sha256 inverse{};
    patch::DocumentPatchV1 inverted{};
    if (!DigestPatch(patch, &forward) ||
        patch::InvertPatchV1(patch, &inverted) != patch::Error::None ||
        !DigestPatch(inverted, &inverse)) {
        return ApplyError::Execute;
    }
    receipt->forwardDigest = forward;
    receipt->inverseDigest = inverse;

    if (patch.operations.empty()) {
        receipt->state = ApplyState::NoOp;
        receipt->version = expected;
        receipt->hancomMutations = 0;
        return ApplyError::None;
    }

    if (host.checkpoint == nullptr || host.execute == nullptr ||
        host.settleLayout == nullptr || host.readback == nullptr ||
        host.publish == nullptr || host.recordHistory == nullptr ||
        host.restore == nullptr) {
        return ApplyError::Execute;
    }
    if (!host.checkpoint(host.context)) {
        return ApplyError::Execute;
    }

    std::uint64_t mutations = 0;
    if (!host.execute(host.context, patch, &mutations) || mutations == 0) {
        return FailAndRestore(host, expected, upload, forward, inverse,
                              ApplyError::Execute, receipt);
    }
    if (!host.settleLayout(host.context)) {
        return FailAndRestore(host, expected, upload, forward, inverse,
                              ApplyError::Layout, receipt);
    }
    identity::GraphVersionV1 observed{};
    if (!host.readback(host.context, &observed) ||
        !observed.semanticCertified ||
        identity::FullVersionCas(observed, expected)) {
        return FailAndRestore(host, expected, upload, forward, inverse,
                              ApplyError::Readback, receipt);
    }
    if (!host.publish(host.context, observed)) {
        return FailAndRestore(host, expected, upload, forward, inverse,
                              ApplyError::Publish, receipt);
    }
    if (!host.recordHistory(host.context, upload, forward, inverse)) {
        return FailAndRestore(host, expected, upload, forward, inverse,
                              ApplyError::History, receipt);
    }

    receipt->state = ApplyState::Applied;
    receipt->version = observed;
    receipt->hancomMutations = mutations;
    return ApplyError::None;
}

}

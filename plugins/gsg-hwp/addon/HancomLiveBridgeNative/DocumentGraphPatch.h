#pragma once

#include "DocumentGraphCodec.h"
#include "DocumentGraphIdentity.h"

#include <cstdint>
#include <vector>

namespace hancom::graph::patch {

inline constexpr std::uint16_t kPatchSchemaV1 = 1;

enum class OperationKind : std::uint8_t {
    ReplaceText = 0,
    ReplaceProperties = 1,
    ReplaceReferences = 2,
    ReplaceChildren = 3,
    ReplaceBlob = 4,
};

enum class Error : std::uint8_t {
    None = 0,
    StorageFailure,
    LengthOverflow,
    BadField,
    BadSchema,
    CrossVersion,
    NotAtomic,
    BadOperation,
    MissingBefore,
    OutOfOrder,
    ReadOnlyProperty,
    SequentialCas,
    BadReference,
    BadClientLocalId,
};

struct OperationV1 final {
    OperationKind kind = OperationKind::ReplaceText;
    NodeId target{};
    FieldTag subject = 0;
    bool propertyKeyPresent = false;
    PropertyKeyId propertyKey = 0;
    ScalarTag scalar = ScalarTag::Bytes;
    codec::Bytes before{};
    codec::Bytes after{};
};

struct DocumentPatchV1 final {
    identity::GraphVersionV1 version{};
    std::vector<identity::ClientId> clientLocalIds{};
    std::vector<OperationV1> operations{};
};

using ReadCallback = bool (*)(void* context, std::uint64_t offset,
                              std::uint8_t* buffer, std::uint32_t requested,
                              std::uint32_t* actual) noexcept;
struct SealedSource final {
    void* context = nullptr;
    ReadCallback read = nullptr;
    std::uint64_t length = 0;
};

Error ParseCanonicalPatchV1(codec::ByteView bytes,
                            const identity::GraphVersionV1& expectedVersion,
                            DocumentPatchV1* patch) noexcept;
struct SealedPatchValidationV1 final {
    Sha256 canonicalDigest{};
    Sha256 inverseDigest{};
    std::uint64_t operationCount = 0;
    std::uint64_t failureOffset = 0;
};
Error ParseSealedPatchV1(const SealedSource& source,
                         const identity::GraphVersionV1& expectedVersion,
                         SealedPatchValidationV1* validation) noexcept;
Error EncodeCanonicalPatchV1(const DocumentPatchV1& patch,
                             codec::Bytes* bytes) noexcept;
Error InvertPatchV1(const DocumentPatchV1& patch,
                    DocumentPatchV1* inverse) noexcept;

struct ValidationReceiptV1 final {
    Uuid128 upload{};
    Sha256 canonicalDigest{};
    std::uint64_t operationCount = 0;
    Sha256 inverseDigest{};
};
bool EncodeValidationReceiptPayloadV1(const ValidationReceiptV1& receipt,
                                      codec::Bytes* payload) noexcept;

} // namespace hancom::graph::patch

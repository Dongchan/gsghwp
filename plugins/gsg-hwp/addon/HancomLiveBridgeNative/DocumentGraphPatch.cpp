#include "DocumentGraphPatch.h"

#include "DocumentGraphProperties.h"
#include "DocumentGraphProtocol.h"

#include <Windows.h>
#include <bcrypt.h>
#undef ReplaceText

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <map>
#include <set>
#include <tuple>

namespace hancom::graph::patch {
namespace {
using codec::ByteView;
using codec::Bytes;
using protocol::ParsedField;

std::uint16_t U16(const std::uint8_t* value) noexcept {
    return static_cast<std::uint16_t>(value[0]) |
           static_cast<std::uint16_t>(value[1] << 8U);
}
std::uint32_t U32(const std::uint8_t* value) noexcept {
    return static_cast<std::uint32_t>(value[0]) |
           (static_cast<std::uint32_t>(value[1]) << 8U) |
           (static_cast<std::uint32_t>(value[2]) << 16U) |
           (static_cast<std::uint32_t>(value[3]) << 24U);
}
std::uint64_t U64(const std::uint8_t* value) noexcept {
    std::uint64_t result = 0;
    for (unsigned index = 0; index != 8; ++index)
        result |= static_cast<std::uint64_t>(value[index]) << (index * 8U);
    return result;
}
void Put16(Bytes* output, const std::uint16_t value) {
    output->push_back(static_cast<std::uint8_t>(value));
    output->push_back(static_cast<std::uint8_t>(value >> 8U));
}
void Put32(Bytes* output, const std::uint32_t value) {
    for (unsigned index = 0; index != 4; ++index)
        output->push_back(static_cast<std::uint8_t>(value >> (index * 8U)));
}
void Put64(Bytes* output, const std::uint64_t value) {
    for (unsigned index = 0; index != 8; ++index)
        output->push_back(static_cast<std::uint8_t>(value >> (index * 8U)));
}
Bytes Scalar16(const std::uint16_t value) { Bytes bytes; Put16(&bytes, value); return bytes; }
Bytes Scalar32(const std::uint32_t value) { Bytes bytes; Put32(&bytes, value); return bytes; }
Bytes Scalar64(const std::uint64_t value) { Bytes bytes; Put64(&bytes, value); return bytes; }
Bytes Id(const Uuid128& value) { return Bytes(value.bytes.begin(), value.bytes.end()); }
Bytes Digest(const Sha256& value) { return Bytes(value.bytes.begin(), value.bytes.end()); }
Bytes BytesScalar(const Bytes& value) {
    Bytes encoded;
    encoded.reserve(8 + value.size());
    Put64(&encoded, value.size());
    encoded.insert(encoded.end(), value.begin(), value.end());
    return encoded;
}
void Append(Bytes* output, const Bytes& value) {
    output->insert(output->end(), value.begin(), value.end());
}
const ParsedField* Find(const std::vector<ParsedField>& fields,
                        const FieldTag tag) noexcept {
    const auto found = std::find_if(fields.begin(), fields.end(),
        [tag](const ParsedField& field) { return field.tag == tag; });
    return found == fields.end() ? nullptr : &*found;
}
bool Exact(const ParsedField* field, const ScalarTag scalar,
           const std::uint64_t size) noexcept {
    return field != nullptr && field->flags == kFieldFlagRequired &&
           field->scalar == scalar && field->elementCount == 1 &&
           field->value.size == size;
}
bool ExactTags(const std::vector<ParsedField>& fields,
               const std::initializer_list<FieldTag> tags) noexcept {
    if (fields.size() != tags.size()) return false;
    return std::equal(fields.begin(), fields.end(), tags.begin(),
        [](const ParsedField& field, const FieldTag tag) {
            return field.tag == tag;
        });
}
bool ValidId(const Uuid128& value) noexcept {
    return identity::IsRfc4122V4(value);
}
bool ReadId(const ParsedField* field, Uuid128* id) noexcept {
    if (!Exact(field, ScalarTag::UUID128, 16) || id == nullptr) return false;
    std::copy_n(field->value.data, 16, id->bytes.begin());
    return ValidId(*id);
}

bool ParseArray(const ParsedField* field, const ScalarTag scalar,
                std::vector<Bytes>* values) {
    if (field == nullptr || values == nullptr || field->flags != 3 ||
        field->scalar != scalar || field->value.size < 16 ||
        U16(field->value.data) != static_cast<std::uint16_t>(scalar) ||
        U16(field->value.data + 2) != 0 || U32(field->value.data + 4) != 0 ||
        U64(field->value.data + 8) != field->elementCount)
        return false;
    values->clear();
    std::uint64_t at = 16;
    try {
        values->reserve(static_cast<std::size_t>(field->elementCount));
        for (std::uint64_t index = 0; index != field->elementCount; ++index) {
            if (at > field->value.size || field->value.size - at < 8) return false;
            const std::uint64_t length = U64(field->value.data + at);
            at += 8;
            if (length > field->value.size - at ||
                length > (std::numeric_limits<std::size_t>::max)()) return false;
            values->emplace_back(field->value.data + at,
                                 field->value.data + at + length);
            at += length;
        }
    } catch (...) { return false; }
    return at == field->value.size;
}
Bytes Array(const ScalarTag scalar, const std::vector<Bytes>& values) {
    Bytes encoded;
    Put16(&encoded, static_cast<std::uint16_t>(scalar));
    Put16(&encoded, 0);
    Put32(&encoded, 0);
    Put64(&encoded, values.size());
    for (const Bytes& value : values) {
        Put64(&encoded, value.size());
        Append(&encoded, value);
    }
    return encoded;
}

using Key = std::tuple<std::array<std::uint8_t, 16>, std::uint8_t,
                       std::uint16_t, std::uint32_t>;
Key OperationKey(const OperationV1& operation) noexcept {
    return {operation.target.bytes, static_cast<std::uint8_t>(operation.kind),
            operation.subject,
            operation.propertyKeyPresent ? operation.propertyKey : 0};
}
bool SameOperation(const OperationV1& left, const OperationV1& right) noexcept {
    return OperationKey(left) == OperationKey(right) && left.scalar == right.scalar &&
           left.propertyKeyPresent == right.propertyKeyPresent &&
           left.before == right.before && left.after == right.after;
}
const PropertyRule* Property(const PropertyKeyId key) noexcept {
    const auto found = std::find_if(kPropertyRegistryV1.begin(),
        kPropertyRegistryV1.end(), [key](const PropertyRule& rule) {
            return rule.id == key;
        });
    return found == kPropertyRegistryV1.end() ? nullptr : &*found;
}

Error Normalize(DocumentPatchV1* patch) {
    if (patch == nullptr || !patch->version.semanticCertified)
        return Error::CrossVersion;
    std::sort(patch->clientLocalIds.begin(), patch->clientLocalIds.end(),
        [](const Uuid128& left, const Uuid128& right) {
            return left.bytes < right.bytes;
        });
    for (std::size_t index = 0; index != patch->clientLocalIds.size(); ++index) {
        if (!ValidId(patch->clientLocalIds[index]) ||
            (index != 0 && identity::EqualUuid(patch->clientLocalIds[index - 1],
                                               patch->clientLocalIds[index])))
            return Error::BadClientLocalId;
    }
    patch->operations.erase(std::remove_if(patch->operations.begin(),
        patch->operations.end(), [](const OperationV1& operation) {
            return operation.before == operation.after;
        }), patch->operations.end());
    std::stable_sort(patch->operations.begin(), patch->operations.end(),
        [](const OperationV1& left, const OperationV1& right) {
            return OperationKey(left) < OperationKey(right);
        });
    std::map<Key, Bytes> prior;
    std::set<std::array<std::uint8_t, 16>> locals;
    std::set<std::array<std::uint8_t, 16>> observedLocals;
    for (const Uuid128& local : patch->clientLocalIds) locals.insert(local.bytes);
    for (const OperationV1& operation : patch->operations) {
        if (!ValidId(operation.target) || locals.count(operation.target.bytes) != 0 ||
            static_cast<std::uint8_t>(operation.kind) >
                static_cast<std::uint8_t>(OperationKind::ReplaceBlob) ||
            static_cast<std::uint16_t>(operation.scalar) >
                static_cast<std::uint16_t>(ScalarTag::Sint32))
            return Error::BadOperation;
        if (operation.kind == OperationKind::ReplaceProperties) {
            const PropertyRule* const property = operation.propertyKeyPresent
                ? Property(operation.propertyKey) : nullptr;
            if (property == nullptr) return Error::BadOperation;
            if (property->writability != Writability::Writable)
                return Error::ReadOnlyProperty;
        } else if (operation.propertyKeyPresent) return Error::BadOperation;
        const Key key = OperationKey(operation);
        const auto found = prior.find(key);
        if (found != prior.end() && found->second != operation.before)
            return Error::SequentialCas;
        prior[key] = operation.after;
        if (operation.kind == OperationKind::ReplaceReferences ||
            operation.kind == OperationKind::ReplaceChildren) {
            if (operation.scalar != ScalarTag::UUID128 ||
                operation.before.size() % 16 != 0 || operation.after.size() % 16 != 0)
                return Error::BadReference;
            for (const Bytes* value : {&operation.before, &operation.after}) {
                for (std::size_t at = 0; at != value->size(); at += 16) {
                    Uuid128 id{};
                    std::copy_n(value->data() + at, 16, id.bytes.begin());
                    if (!ValidId(id)) return Error::BadReference;
                    if (locals.count(id.bytes) != 0) observedLocals.insert(id.bytes);
                }
            }
        } else if (operation.kind == OperationKind::ReplaceBlob) {
            if (operation.scalar != ScalarTag::Struct ||
                operation.before.size() != 56 || operation.after.size() != 56)
                return Error::BadOperation;
        } else if (codec::ValidateCanonicalScalar(operation.scalar,
                    codec::View(operation.before)) != codec::Error::None ||
                   codec::ValidateCanonicalScalar(operation.scalar,
                    codec::View(operation.after)) != codec::Error::None) {
            return Error::BadOperation;
        }
    }
    return observedLocals == locals ? Error::None : Error::BadClientLocalId;
}

Bytes EncodeOperation(const OperationV1& operation) {
    Bytes encoded;
    const Bytes kind{static_cast<std::uint8_t>(operation.kind)};
    const Bytes target = Id(operation.target);
    const Bytes subject = Scalar16(operation.subject);
    const Bytes scalar = Scalar16(static_cast<std::uint16_t>(operation.scalar));
    const Bytes before = BytesScalar(operation.before);
    const Bytes after = BytesScalar(operation.after);
    Append(&encoded, protocol::EncodeField(1, 1, ScalarTag::Uint8, codec::View(kind)));
    Append(&encoded, protocol::EncodeField(2, 1, ScalarTag::UUID128, codec::View(target)));
    Append(&encoded, protocol::EncodeField(3, 1, ScalarTag::Uint16, codec::View(subject)));
    if (operation.propertyKeyPresent) {
        const Bytes key = Scalar32(operation.propertyKey);
        Append(&encoded, protocol::EncodeField(4, 1, ScalarTag::Uint32, codec::View(key)));
    }
    Append(&encoded, protocol::EncodeField(5, 1, ScalarTag::Uint16, codec::View(scalar)));
    Append(&encoded, protocol::EncodeField(6, 1, ScalarTag::Bytes, codec::View(before)));
    Append(&encoded, protocol::EncodeField(7, 1, ScalarTag::Bytes, codec::View(after)));
    return encoded;
}

Error DecodeOperation(const Bytes& encoded, OperationV1* operation) {
    std::vector<ParsedField> fields;
    protocol::ErrorCode ignored{};
    if (operation == nullptr ||
        !protocol::ParseFields(codec::View(encoded), &fields, &ignored) ||
        (!ExactTags(fields, {1,2,3,5,6,7}) &&
         !ExactTags(fields, {1,2,3,4,5,6,7})))
        return Error::BadOperation;
    const ParsedField* const kind = Find(fields, 1);
    const ParsedField* const subject = Find(fields, 3);
    const ParsedField* const scalar = Find(fields, 5);
    const ParsedField* const before = Find(fields, 6);
    const ParsedField* const after = Find(fields, 7);
    if (!Exact(kind, ScalarTag::Uint8, 1) || !ReadId(Find(fields, 2), &operation->target) ||
        !Exact(subject, ScalarTag::Uint16, 2) || !Exact(scalar, ScalarTag::Uint16, 2) ||
        before == nullptr || after == nullptr || before->scalar != ScalarTag::Bytes ||
        after->scalar != ScalarTag::Bytes || before->flags != 1 || after->flags != 1 ||
        before->value.size < 8 || after->value.size < 8 ||
        U64(before->value.data) != before->value.size - 8 ||
        U64(after->value.data) != after->value.size - 8 || kind->value.data[0] > 4)
        return Error::MissingBefore;
    operation->kind = static_cast<OperationKind>(kind->value.data[0]);
    operation->subject = U16(subject->value.data);
    const std::uint16_t scalarValue = U16(scalar->value.data);
    if (scalarValue > static_cast<std::uint16_t>(ScalarTag::Sint32))
        return Error::BadOperation;
    operation->scalar = static_cast<ScalarTag>(scalarValue);
    const ParsedField* const property = Find(fields, 4);
    operation->propertyKeyPresent = property != nullptr;
    if (property != nullptr) {
        if (!Exact(property, ScalarTag::Uint32, 4)) return Error::BadOperation;
        operation->propertyKey = U32(property->value.data);
    }
    try {
        operation->before.assign(before->value.data + 8,
                                 before->value.data + before->value.size);
        operation->after.assign(after->value.data + 8,
                                after->value.data + after->value.size);
    } catch (...) { return Error::StorageFailure; }
    return Error::None;
}

struct StreamSlice final { std::uint64_t offset = 0, length = 0; };
struct StreamField final {
    FieldTag tag = 0;
    std::uint16_t flags = 0;
    ScalarTag scalar = ScalarTag::Bytes;
    std::uint64_t count = 0;
    StreamSlice value{};
    std::uint64_t headerOffset = 0;
};

class SourceReader final {
public:
    explicit SourceReader(const SealedSource& source) noexcept : source_(source) {}
    bool Read(const std::uint64_t offset, void* output,
              const std::uint64_t length) noexcept {
        if (output == nullptr || offset > source_.length ||
            length > source_.length - offset || length > UINT32_MAX) {
            failureOffset_ = offset;
            return false;
        }
        std::uint32_t actual = 0;
        if (!source_.read(source_.context, offset,
                          static_cast<std::uint8_t*>(output),
                          static_cast<std::uint32_t>(length), &actual) ||
            actual != length) {
            failureOffset_ = offset;
            return false;
        }
        return true;
    }
    bool Copy(const StreamSlice slice, BCRYPT_HASH_HANDLE hash) noexcept {
        std::uint64_t done = 0;
        while (done != slice.length) {
            const std::uint32_t chunk = static_cast<std::uint32_t>(
                (std::min<std::uint64_t>)(slice.length - done, buffer_.size()));
            if (!Read(slice.offset + done, buffer_.data(), chunk) ||
                BCryptHashData(hash, buffer_.data(), chunk, 0) < 0) {
                failureOffset_ = slice.offset + done;
                return false;
            }
            done += chunk;
        }
        return true;
    }
    bool Equal(const StreamSlice left, const StreamSlice right,
               bool* equal) noexcept {
        if (equal == nullptr) return false;
        if (left.length != right.length) { *equal = false; return true; }
        std::uint64_t done = 0;
        const std::size_t half = buffer_.size() / 2;
        while (done != left.length) {
            const std::uint32_t chunk = static_cast<std::uint32_t>(
                (std::min<std::uint64_t>)(left.length - done, half));
            if (!Read(left.offset + done, buffer_.data(), chunk) ||
                !Read(right.offset + done, buffer_.data() + half, chunk))
                return false;
            if (std::memcmp(buffer_.data(), buffer_.data() + half, chunk) != 0) {
                *equal = false;
                return true;
            }
            done += chunk;
        }
        *equal = true;
        return true;
    }
    void FailAt(const std::uint64_t offset) noexcept { failureOffset_ = offset; }
    std::uint64_t failureOffset() const noexcept { return failureOffset_; }
private:
    const SealedSource& source_;
    std::array<std::uint8_t, 64U * 1024U> buffer_{};
    std::uint64_t failureOffset_ = 0;
};

class PatchHash final {
public:
    ~PatchHash() noexcept {
        if (hash_ != nullptr) BCryptDestroyHash(hash_);
        if (algorithm_ != nullptr) BCryptCloseAlgorithmProvider(algorithm_, 0);
    }
    bool Open() {
        DWORD objectBytes = 0, actual = 0;
        return BCryptOpenAlgorithmProvider(&algorithm_, BCRYPT_SHA256_ALGORITHM,
                    nullptr, 0) >= 0 &&
            BCryptGetProperty(algorithm_, BCRYPT_OBJECT_LENGTH,
                reinterpret_cast<PUCHAR>(&objectBytes), sizeof(objectBytes),
                &actual, 0) >= 0 &&
            (object_.resize(objectBytes), true) &&
            BCryptCreateHash(algorithm_, &hash_, object_.data(), objectBytes,
                             nullptr, 0, 0) >= 0;
    }
    bool Add(const void* bytes, const std::size_t length) noexcept {
        return length <= ULONG_MAX && BCryptHashData(hash_,
            const_cast<PUCHAR>(static_cast<const std::uint8_t*>(bytes)),
            static_cast<ULONG>(length), 0) >= 0;
    }
    BCRYPT_HASH_HANDLE handle() const noexcept { return hash_; }
    bool Finish(Sha256* digest) noexcept {
        return digest != nullptr && BCryptFinishHash(hash_, digest->bytes.data(),
            static_cast<ULONG>(digest->bytes.size()), 0) >= 0;
    }
private:
    BCRYPT_ALG_HANDLE algorithm_ = nullptr;
    BCRYPT_HASH_HANDLE hash_ = nullptr;
    std::vector<std::uint8_t> object_{};
};

bool Add16(PatchHash* hash, const std::uint16_t value) noexcept {
    const std::uint8_t bytes[2]{static_cast<std::uint8_t>(value),
        static_cast<std::uint8_t>(value >> 8U)};
    return hash->Add(bytes, sizeof(bytes));
}
bool Add64(PatchHash* hash, const std::uint64_t value) noexcept {
    std::uint8_t bytes[8]{};
    for (unsigned index = 0; index != 8; ++index)
        bytes[index] = static_cast<std::uint8_t>(value >> (index * 8U));
    return hash->Add(bytes, sizeof(bytes));
}
bool AddFieldHeader(PatchHash* hash, const FieldTag tag,
                    const std::uint16_t flags, const ScalarTag scalar,
                    const std::uint64_t count,
                    const std::uint64_t length) noexcept {
    return Add16(hash, tag) && Add16(hash, flags) &&
        Add16(hash, static_cast<std::uint16_t>(scalar)) && Add16(hash, 0) &&
        Add64(hash, count) && Add64(hash, length);
}

bool StreamFieldAt(SourceReader* reader, std::uint64_t* at,
                   const std::uint64_t end, StreamField* field) noexcept {
    std::array<std::uint8_t, 24> header{};
    if (reader == nullptr || at == nullptr || field == nullptr) return false;
    if (*at > end || end - *at < header.size()) {
        reader->FailAt(*at);
        return false;
    }
    if (!reader->Read(*at, header.data(), header.size())) return false;
    field->headerOffset = *at;
    field->tag = U16(header.data());
    field->flags = U16(header.data() + 2);
    const std::uint16_t scalar = U16(header.data() + 4);
    field->count = U64(header.data() + 8);
    field->value = {*at + header.size(), U64(header.data() + 16)};
    if (field->tag == 0 || (field->flags & ~3U) != 0 ||
        U16(header.data() + 6) != 0 ||
        scalar > static_cast<std::uint16_t>(ScalarTag::Sint32) ||
        ((field->flags & kFieldFlagArray) == 0 && field->count != 1) ||
        field->value.offset > end || field->value.length > end - field->value.offset) {
        reader->FailAt(field->headerOffset);
        return false;
    }
    field->scalar = static_cast<ScalarTag>(scalar);
    *at = field->value.offset + field->value.length;
    return true;
}
bool Required(const StreamField& field, const FieldTag tag,
              const ScalarTag scalar, const std::uint64_t length) noexcept {
    return field.tag == tag && field.flags == kFieldFlagRequired &&
        field.scalar == scalar && field.count == 1 && field.value.length == length;
}
bool ValidStreamId(SourceReader* reader, const StreamSlice slice,
                   Uuid128* id = nullptr) noexcept {
    Uuid128 value{};
    if (slice.length != value.bytes.size() ||
        !reader->Read(slice.offset, value.bytes.data(), value.bytes.size()) ||
        !ValidId(value)) return false;
    if (id != nullptr) *id = value;
    return true;
}
struct StreamArray final {
    ScalarTag scalar = ScalarTag::Bytes;
    std::uint64_t count = 0;
    std::uint64_t elementsOffset = 0;
    std::uint64_t end = 0;
};
bool StreamArrayValue(SourceReader* reader, const StreamField& field,
                      const ScalarTag scalar, StreamArray* array) noexcept {
    std::array<std::uint8_t, 16> header{};
    if (array == nullptr || field.flags !=
            (kFieldFlagRequired | kFieldFlagArray) || field.scalar != scalar ||
        field.value.length < header.size() ||
        !reader->Read(field.value.offset, header.data(), header.size()) ||
        U16(header.data()) != static_cast<std::uint16_t>(scalar) ||
        U16(header.data() + 2) != 0 || U32(header.data() + 4) != 0 ||
        U64(header.data() + 8) != field.count) return false;
    *array = {scalar, field.count, field.value.offset + header.size(),
              field.value.offset + field.value.length};
    return true;
}
bool StreamArrayElement(SourceReader* reader, std::uint64_t* at,
                        const std::uint64_t end, StreamSlice* value) noexcept {
    std::array<std::uint8_t, 8> length{};
    if (at == nullptr || value == nullptr || *at > end || end - *at < 8 ||
        !reader->Read(*at, length.data(), length.size())) return false;
    const std::uint64_t bytes = U64(length.data());
    if (bytes > end - *at - 8) return false;
    *value = {*at + 8, bytes};
    *at += 8 + bytes;
    return true;
}

struct StreamOperation final {
    OperationKind kind = OperationKind::ReplaceText;
    Uuid128 target{};
    FieldTag subject = 0;
    bool propertyPresent = false;
    PropertyKeyId property = 0;
    ScalarTag scalar = ScalarTag::Bytes;
    StreamSlice encoded{};
    StreamSlice before{};
    StreamSlice after{};
};
Key StreamKey(const StreamOperation& operation) noexcept {
    return {operation.target.bytes, static_cast<std::uint8_t>(operation.kind),
            operation.subject, operation.propertyPresent ? operation.property : 0};
}
bool StreamScalarCanonical(SourceReader* reader, const ScalarTag scalar,
                           const StreamSlice value) noexcept {
    const std::uint8_t fixed = FixedScalarBytes(scalar);
    if (fixed != 0) {
        std::array<std::uint8_t, 32> bytes{};
        return value.length == fixed &&
            reader->Read(value.offset, bytes.data(), fixed) &&
            codec::ValidateCanonicalScalar(scalar, {bytes.data(), fixed}) ==
                codec::Error::None;
    }
    if (scalar == ScalarTag::Struct) return true;
    std::array<std::uint8_t, 24> prefix{};
    if ((scalar == ScalarTag::UTF16 || scalar == ScalarTag::Bytes) &&
        value.length >= 8 && reader->Read(value.offset, prefix.data(), 8)) {
        const std::uint64_t count = U64(prefix.data());
        const std::uint64_t width = scalar == ScalarTag::UTF16 ? 2 : 1;
        return count <= UINT64_MAX / width && count * width == value.length - 8;
    }
    if (scalar == ScalarTag::Enum && value.length >= 16 &&
        reader->Read(value.offset, prefix.data(), 16)) {
        if (prefix[8] > 1) return false;
        for (unsigned index = 9; index != 16; ++index)
            if (prefix[index] != 0) return false;
        if (prefix[8] == 0) return value.length == 16;
        if (value.length < 24 ||
            !reader->Read(value.offset + 16, prefix.data(), 8)) return false;
        const std::uint64_t units = U64(prefix.data());
        return units <= UINT64_MAX / 2 && units * 2 == value.length - 24;
    }
    return scalar == ScalarTag::BlobSlice && value.length == 64;
}

Error ParseStreamOperation(SourceReader* reader, const StreamSlice encoded,
                           StreamOperation* operation) noexcept {
    if (operation == nullptr) return Error::StorageFailure;
    std::uint64_t at = encoded.offset;
    const std::uint64_t end = encoded.offset + encoded.length;
    StreamField kind{}, target{}, subject{}, next{}, scalar{}, before{}, after{};
    std::array<std::uint8_t, 8> scalarBytes{};
    if (!StreamFieldAt(reader, &at, end, &kind) ||
        !Required(kind, 1, ScalarTag::Uint8, 1) ||
        !reader->Read(kind.value.offset, scalarBytes.data(), 1) ||
        scalarBytes[0] > 4)
        return Error::BadOperation;
    const std::uint8_t kindValue = scalarBytes[0];
    if (!StreamFieldAt(reader, &at, end, &target) ||
        !Required(target, 2, ScalarTag::UUID128, 16) ||
        !ValidStreamId(reader, target.value, &operation->target) ||
        !StreamFieldAt(reader, &at, end, &subject) ||
        !Required(subject, 3, ScalarTag::Uint16, 2) ||
        !reader->Read(subject.value.offset, scalarBytes.data(), 2) ||
        !StreamFieldAt(reader, &at, end, &next)) return Error::BadOperation;
    operation->kind = static_cast<OperationKind>(kindValue);
    operation->subject = U16(scalarBytes.data());
    if (next.tag == 4) {
        if (!Required(next, 4, ScalarTag::Uint32, 4) ||
            !reader->Read(next.value.offset, scalarBytes.data(), 4) ||
            !StreamFieldAt(reader, &at, end, &scalar)) return Error::BadOperation;
        operation->propertyPresent = true;
        operation->property = U32(scalarBytes.data());
    } else scalar = next;
    if (!Required(scalar, 5, ScalarTag::Uint16, 2) ||
        !reader->Read(scalar.value.offset, scalarBytes.data(), 2))
        return Error::BadOperation;
    const std::uint16_t scalarValue = U16(scalarBytes.data());
    if (scalarValue > static_cast<std::uint16_t>(ScalarTag::Sint32) ||
        !StreamFieldAt(reader, &at, end, &before) || before.tag != 6 ||
        before.flags != kFieldFlagRequired || before.scalar != ScalarTag::Bytes ||
        before.count != 1 || before.value.length < 8 ||
        !reader->Read(before.value.offset, scalarBytes.data(), 8) ||
        U64(scalarBytes.data()) != before.value.length - 8 ||
        !StreamFieldAt(reader, &at, end, &after) || after.tag != 7 ||
        after.flags != kFieldFlagRequired || after.scalar != ScalarTag::Bytes ||
        after.count != 1 || after.value.length < 8 ||
        !reader->Read(after.value.offset, scalarBytes.data(), 8) ||
        U64(scalarBytes.data()) != after.value.length - 8 || at != end)
        return Error::MissingBefore;
    operation->scalar = static_cast<ScalarTag>(scalarValue);
    operation->encoded = encoded;
    operation->before = {before.value.offset + 8, before.value.length - 8};
    operation->after = {after.value.offset + 8, after.value.length - 8};
    return Error::None;
}

bool ScanLocal(SourceReader* reader, const StreamArray& locals,
               const Uuid128& wanted, bool* present) noexcept {
    if (present == nullptr) return false;
    *present = false;
    std::uint64_t at = locals.elementsOffset;
    Uuid128 prior{};
    bool hasPrior = false;
    for (std::uint64_t index = 0; index != locals.count; ++index) {
        StreamSlice value{};
        Uuid128 id{};
        if (!StreamArrayElement(reader, &at, locals.end, &value) ||
            !ValidStreamId(reader, value, &id) ||
            (hasPrior && !(prior.bytes < id.bytes))) return false;
        if (identity::EqualUuid(id, wanted)) *present = true;
        prior = id;
        hasPrior = true;
    }
    return at == locals.end;
}

bool HashInverseOperation(SourceReader* reader, PatchHash* hash,
                          const StreamOperation& operation) noexcept {
    std::uint64_t prefixEnd = operation.encoded.offset;
    StreamField field{};
    for (unsigned index = 0; index != (operation.propertyPresent ? 5U : 4U); ++index)
        if (!StreamFieldAt(reader, &prefixEnd,
                operation.encoded.offset + operation.encoded.length, &field)) return false;
    if (!reader->Copy({operation.encoded.offset,
                       prefixEnd - operation.encoded.offset}, hash->handle()) ||
        !AddFieldHeader(hash, 6, kFieldFlagRequired, ScalarTag::Bytes, 1,
                        8 + operation.after.length) ||
        !Add64(hash, operation.after.length) ||
        !reader->Copy(operation.after, hash->handle()) ||
        !AddFieldHeader(hash, 7, kFieldFlagRequired, ScalarTag::Bytes, 1,
                        8 + operation.before.length) ||
        !Add64(hash, operation.before.length) ||
        !reader->Copy(operation.before, hash->handle())) return false;
    return true;
}
} // namespace

Error EncodeCanonicalPatchV1(const DocumentPatchV1& patch, Bytes* bytes) noexcept {
    if (bytes == nullptr) return Error::StorageFailure;
    try {
        DocumentPatchV1 canonical = patch;
        const Error normalized = Normalize(&canonical);
        if (normalized != Error::None) return normalized;
        identity::SerializedGraphVersionV1 version{};
        if (identity::SerializeGraphVersion(canonical.version, &version) !=
            identity::GraphVersionError::None) return Error::CrossVersion;
        std::vector<Bytes> locals;
        for (const Uuid128& local : canonical.clientLocalIds) locals.push_back(Id(local));
        std::vector<Bytes> operations;
        for (const OperationV1& operation : canonical.operations)
            operations.push_back(EncodeOperation(operation));
        const Bytes schema = Scalar16(kPatchSchemaV1);
        const Bytes atomic{1};
        const Bytes versionBytes(version.begin(), version.end());
        const Bytes localArray = Array(ScalarTag::UUID128, locals);
        const Bytes operationArray = Array(ScalarTag::Struct, operations);
        bytes->clear();
        Append(bytes, protocol::EncodeField(1, 1, ScalarTag::Uint16, codec::View(schema)));
        Append(bytes, protocol::EncodeField(2, 1, ScalarTag::Bool, codec::View(atomic)));
        Append(bytes, protocol::EncodeField(3, 1, ScalarTag::Struct, codec::View(versionBytes)));
        Append(bytes, protocol::EncodeField(4, 3, ScalarTag::UUID128,
                                             codec::View(localArray), locals.size()));
        Append(bytes, protocol::EncodeField(5, 3, ScalarTag::Struct,
                                             codec::View(operationArray), operations.size()));
        return Error::None;
    } catch (...) { bytes->clear(); return Error::StorageFailure; }
}

Error ParseCanonicalPatchV1(const ByteView bytes,
                            const identity::GraphVersionV1& expectedVersion,
                            DocumentPatchV1* patch) noexcept {
    if (patch == nullptr || bytes.data == nullptr) return Error::BadField;
    try {
        std::vector<ParsedField> fields;
        protocol::ErrorCode ignored{};
        if (!protocol::ParseFields(bytes, &fields, &ignored) ||
            !ExactTags(fields, {1,2,3,4,5})) return Error::BadField;
        const ParsedField* const schema = Find(fields, 1);
        const ParsedField* const atomic = Find(fields, 2);
        const ParsedField* const versionField = Find(fields, 3);
        if (!Exact(schema, ScalarTag::Uint16, 2) || U16(schema->value.data) != 1)
            return Error::BadSchema;
        if (!Exact(atomic, ScalarTag::Bool, 1) || atomic->value.data[0] != 1)
            return Error::NotAtomic;
        if (!Exact(versionField, ScalarTag::Struct, kGraphVersionBytesV1))
            return Error::CrossVersion;
        DocumentPatchV1 parsed{};
        if (identity::DeserializeGraphVersion(versionField->value.data,
                versionField->value.size, &parsed.version) !=
                identity::GraphVersionError::None ||
            !identity::PatchVersionCas(parsed.version, expectedVersion))
            return Error::CrossVersion;
        std::vector<Bytes> locals;
        std::vector<Bytes> operations;
        if (!ParseArray(Find(fields, 4), ScalarTag::UUID128, &locals) ||
            !ParseArray(Find(fields, 5), ScalarTag::Struct, &operations))
            return Error::BadField;
        for (const Bytes& local : locals) {
            if (local.size() != 16) return Error::BadClientLocalId;
            Uuid128 id{};
            std::copy(local.begin(), local.end(), id.bytes.begin());
            parsed.clientLocalIds.push_back(id);
        }
        for (const Bytes& encoded : operations) {
            OperationV1 operation{};
            const Error decoded = DecodeOperation(encoded, &operation);
            if (decoded != Error::None) return decoded;
            parsed.operations.push_back(std::move(operation));
        }
        const DocumentPatchV1 original = parsed;
        const Error normalized = Normalize(&parsed);
        if (normalized != Error::None) return normalized;
        if (original.clientLocalIds.size() != parsed.clientLocalIds.size() ||
            original.operations.size() != parsed.operations.size() ||
            !std::equal(original.clientLocalIds.begin(), original.clientLocalIds.end(),
                parsed.clientLocalIds.begin(), identity::EqualUuid) ||
            !std::equal(original.operations.begin(), original.operations.end(),
                parsed.operations.begin(), SameOperation)) return Error::OutOfOrder;
        Bytes canonical;
        const Error encoded = EncodeCanonicalPatchV1(parsed, &canonical);
        if (encoded != Error::None || canonical.size() != bytes.size ||
            std::memcmp(canonical.data(), bytes.data, canonical.size()) != 0)
            return Error::OutOfOrder;
        *patch = std::move(parsed);
        return Error::None;
    } catch (...) { return Error::StorageFailure; }
}

Error ParseSealedPatchV1(const SealedSource& source,
                         const identity::GraphVersionV1& expectedVersion,
                         SealedPatchValidationV1* validation) noexcept {
    if (validation == nullptr || source.read == nullptr || source.length == 0)
        return source.length == 0 ? Error::BadField : Error::StorageFailure;
    *validation = {};
    try {
        SourceReader reader(source);
        auto fail = [&](const Error error, const std::uint64_t offset) {
            validation->failureOffset = offset;
            return error;
        };
        std::uint64_t at = 0;
        StreamField schema{}, atomic{}, versionField{}, localsField{}, operationsField{};
        if (!StreamFieldAt(&reader, &at, source.length, &schema) ||
            !Required(schema, 1, ScalarTag::Uint16, 2))
            return fail(Error::BadField, reader.failureOffset());
        std::array<std::uint8_t, kGraphVersionBytesV1> fixed{};
        if (!reader.Read(schema.value.offset, fixed.data(), 2) ||
            U16(fixed.data()) != kPatchSchemaV1)
            return fail(Error::BadSchema, schema.value.offset);
        if (!StreamFieldAt(&reader, &at, source.length, &atomic) ||
            !Required(atomic, 2, ScalarTag::Bool, 1) ||
            !reader.Read(atomic.value.offset, fixed.data(), 1) || fixed[0] != 1)
            return fail(Error::NotAtomic, atomic.headerOffset);
        if (!StreamFieldAt(&reader, &at, source.length, &versionField) ||
            !Required(versionField, 3, ScalarTag::Struct, kGraphVersionBytesV1) ||
            !reader.Read(versionField.value.offset, fixed.data(), fixed.size()))
            return fail(Error::CrossVersion, versionField.headerOffset);
        identity::GraphVersionV1 parsedVersion{};
        if (identity::DeserializeGraphVersion(fixed.data(), fixed.size(),
                &parsedVersion) != identity::GraphVersionError::None ||
            !identity::PatchVersionCas(parsedVersion, expectedVersion))
            return fail(Error::CrossVersion, versionField.value.offset);
        if (!StreamFieldAt(&reader, &at, source.length, &localsField) ||
            localsField.tag != 4 ||
            !StreamFieldAt(&reader, &at, source.length, &operationsField) ||
            operationsField.tag != 5 || at != source.length)
            return fail(Error::BadField, reader.failureOffset());
        StreamArray locals{}, operations{};
        if (!StreamArrayValue(&reader, localsField, ScalarTag::UUID128, &locals) ||
            !StreamArrayValue(&reader, operationsField, ScalarTag::Struct,
                              &operations))
            return fail(Error::BadField, reader.failureOffset());

        std::uint64_t localAt = locals.elementsOffset;
        Uuid128 priorLocal{};
        bool hasPriorLocal = false;
        for (std::uint64_t index = 0; index != locals.count; ++index) {
            StreamSlice value{};
            Uuid128 local{};
            if (!StreamArrayElement(&reader, &localAt, locals.end, &value) ||
                !ValidStreamId(&reader, value, &local) ||
                (hasPriorLocal && !(priorLocal.bytes < local.bytes)))
                return fail(Error::BadClientLocalId, localAt);
            priorLocal = local;
            hasPriorLocal = true;
        }
        if (localAt != locals.end)
            return fail(Error::BadField, localAt);

        std::uint64_t operationAt = operations.elementsOffset;
        Key priorKey{};
        bool hasPriorKey = false;
        StreamSlice priorAfter{};
        for (std::uint64_t index = 0; index != operations.count; ++index) {
            const std::uint64_t elementOffset = operationAt;
            StreamSlice encoded{};
            StreamOperation operation{};
            if (!StreamArrayElement(&reader, &operationAt, operations.end,
                                    &encoded))
                return fail(Error::BadField, elementOffset);
            const Error decoded = ParseStreamOperation(&reader, encoded, &operation);
            if (decoded != Error::None) return fail(decoded, encoded.offset);
            const Key key = StreamKey(operation);
            if (hasPriorKey && key < priorKey)
                return fail(Error::OutOfOrder, encoded.offset);
            bool equal = false;
            if (!reader.Equal(operation.before, operation.after, &equal))
                return fail(Error::StorageFailure, reader.failureOffset());
            if (equal) return fail(Error::OutOfOrder, encoded.offset);
            if (hasPriorKey && key == priorKey) {
                if (!reader.Equal(priorAfter, operation.before, &equal))
                    return fail(Error::StorageFailure, reader.failureOffset());
                if (!equal) return fail(Error::SequentialCas, operation.before.offset);
            }
            bool targetIsLocal = false;
            if (!ScanLocal(&reader, locals, operation.target, &targetIsLocal))
                return fail(Error::BadClientLocalId, encoded.offset);
            if (targetIsLocal) return fail(Error::BadOperation, encoded.offset);
            if (operation.kind == OperationKind::ReplaceProperties) {
                const PropertyRule* const property = operation.propertyPresent
                    ? Property(operation.property) : nullptr;
                if (property == nullptr) return fail(Error::BadOperation, encoded.offset);
                if (property->writability != Writability::Writable)
                    return fail(Error::ReadOnlyProperty, encoded.offset);
            } else if (operation.propertyPresent)
                return fail(Error::BadOperation, encoded.offset);
            if (operation.kind == OperationKind::ReplaceReferences ||
                operation.kind == OperationKind::ReplaceChildren) {
                if (operation.scalar != ScalarTag::UUID128 ||
                    operation.before.length % 16 != 0 ||
                    operation.after.length % 16 != 0)
                    return fail(Error::BadReference, encoded.offset);
                for (const StreamSlice value : {operation.before, operation.after}) {
                    for (std::uint64_t offset = 0; offset != value.length; offset += 16)
                        if (!ValidStreamId(&reader, {value.offset + offset, 16}))
                            return fail(Error::BadReference, value.offset + offset);
                }
            } else if (operation.kind == OperationKind::ReplaceBlob) {
                if (operation.scalar != ScalarTag::Struct ||
                    operation.before.length != 56 || operation.after.length != 56)
                    return fail(Error::BadOperation, encoded.offset);
            } else if (!StreamScalarCanonical(&reader, operation.scalar,
                                              operation.before) ||
                       !StreamScalarCanonical(&reader, operation.scalar,
                                              operation.after))
                return fail(Error::BadOperation, encoded.offset);
            priorKey = key;
            priorAfter = operation.after;
            hasPriorKey = true;
        }
        if (operationAt != operations.end)
            return fail(Error::BadField, operationAt);

        localAt = locals.elementsOffset;
        for (std::uint64_t localIndex = 0; localIndex != locals.count; ++localIndex) {
            StreamSlice localValue{};
            Uuid128 local{};
            if (!StreamArrayElement(&reader, &localAt, locals.end, &localValue) ||
                !ValidStreamId(&reader, localValue, &local))
                return fail(Error::BadClientLocalId, localAt);
            bool observed = false;
            operationAt = operations.elementsOffset;
            for (std::uint64_t operationIndex = 0;
                 operationIndex != operations.count && !observed; ++operationIndex) {
                StreamSlice encoded{};
                StreamOperation operation{};
                if (!StreamArrayElement(&reader, &operationAt, operations.end,
                                        &encoded) ||
                    ParseStreamOperation(&reader, encoded, &operation) != Error::None)
                    return fail(Error::BadOperation, operationAt);
                if (operation.kind != OperationKind::ReplaceReferences &&
                    operation.kind != OperationKind::ReplaceChildren) continue;
                for (const StreamSlice value : {operation.before, operation.after}) {
                    for (std::uint64_t offset = 0; offset != value.length; offset += 16) {
                        Uuid128 reference{};
                        if (!ValidStreamId(&reader, {value.offset + offset, 16},
                                           &reference))
                            return fail(Error::BadReference, value.offset + offset);
                        if (identity::EqualUuid(reference, local)) observed = true;
                    }
                }
            }
            if (!observed) return fail(Error::BadClientLocalId, localValue.offset);
        }

        static constexpr char kPatchDomain[] = "HWPGRAPH\0PATCH\0V1";
        PatchHash canonicalHash;
        if (!canonicalHash.Open() ||
            !canonicalHash.Add(kPatchDomain, sizeof(kPatchDomain) - 1) ||
            !reader.Copy({0, source.length}, canonicalHash.handle()) ||
            !canonicalHash.Finish(&validation->canonicalDigest))
            return fail(Error::StorageFailure, reader.failureOffset());

        PatchHash inverseHash;
        if (!inverseHash.Open() ||
            !inverseHash.Add(kPatchDomain, sizeof(kPatchDomain) - 1) ||
            !reader.Copy({0, operations.elementsOffset}, inverseHash.handle()))
            return fail(Error::StorageFailure, reader.failureOffset());
        std::uint64_t groupStart = operations.elementsOffset;
        while (groupStart != operations.end) {
            std::uint64_t scan = groupStart;
            StreamSlice firstEncoded{};
            StreamOperation first{};
            if (!StreamArrayElement(&reader, &scan, operations.end, &firstEncoded) ||
                ParseStreamOperation(&reader, firstEncoded, &first) != Error::None)
                return fail(Error::BadOperation, groupStart);
            const Key groupKey = StreamKey(first);
            std::uint64_t groupCount = 1;
            std::uint64_t groupEnd = scan;
            while (groupEnd != operations.end) {
                std::uint64_t candidateEnd = groupEnd;
                StreamSlice candidateEncoded{};
                StreamOperation candidate{};
                if (!StreamArrayElement(&reader, &candidateEnd, operations.end,
                                        &candidateEncoded) ||
                    ParseStreamOperation(&reader, candidateEncoded, &candidate) !=
                        Error::None)
                    return fail(Error::BadOperation, groupEnd);
                if (StreamKey(candidate) != groupKey) break;
                ++groupCount;
                groupEnd = candidateEnd;
            }
            for (std::uint64_t reverse = groupCount; reverse != 0; --reverse) {
                scan = groupStart;
                StreamSlice encoded{};
                StreamOperation operation{};
                for (std::uint64_t index = 0; index != reverse; ++index) {
                    if (!StreamArrayElement(&reader, &scan, operations.end, &encoded) ||
                        ParseStreamOperation(&reader, encoded, &operation) != Error::None)
                        return fail(Error::BadOperation, scan);
                }
                if (!Add64(&inverseHash, encoded.length) ||
                    !HashInverseOperation(&reader, &inverseHash, operation))
                    return fail(Error::StorageFailure, reader.failureOffset());
            }
            groupStart = groupEnd;
        }
        if (!inverseHash.Finish(&validation->inverseDigest))
            return fail(Error::StorageFailure, source.length);
        validation->operationCount = operations.count;
        validation->failureOffset = 0;
        return Error::None;
    } catch (...) {
        return Error::StorageFailure;
    }
}

Error InvertPatchV1(const DocumentPatchV1& patch,
                    DocumentPatchV1* inverse) noexcept {
    if (inverse == nullptr) return Error::StorageFailure;
    try {
        DocumentPatchV1 canonical = patch;
        Error error = Normalize(&canonical);
        if (error != Error::None) return error;
        std::reverse(canonical.operations.begin(), canonical.operations.end());
        for (OperationV1& operation : canonical.operations)
            operation.before.swap(operation.after);
        std::stable_sort(canonical.operations.begin(), canonical.operations.end(),
            [](const OperationV1& left, const OperationV1& right) {
                return OperationKey(left) < OperationKey(right);
            });
        *inverse = std::move(canonical);
        return Error::None;
    } catch (...) { return Error::StorageFailure; }
}

bool EncodeValidationReceiptPayloadV1(const ValidationReceiptV1& receipt,
                                      Bytes* payload) noexcept {
    if (payload == nullptr || !ValidId(receipt.upload)) return false;
    try {
        const Bytes upload = Id(receipt.upload);
        const Bytes state{static_cast<std::uint8_t>(protocol::UploadState::Validated)};
        const Bytes canonical = Digest(receipt.canonicalDigest);
        const Bytes count = Scalar64(receipt.operationCount);
        const Bytes inverse = Digest(receipt.inverseDigest);
        payload->clear();
        Append(payload, protocol::EncodeField(1, 1, ScalarTag::UUID128, codec::View(upload)));
        Append(payload, protocol::EncodeField(2, 1, ScalarTag::Uint8, codec::View(state)));
        Append(payload, protocol::EncodeField(3, 1, ScalarTag::SHA256, codec::View(canonical)));
        Append(payload, protocol::EncodeField(4, 1, ScalarTag::Uint64, codec::View(count)));
        Append(payload, protocol::EncodeField(5, 1, ScalarTag::SHA256, codec::View(inverse)));
        return true;
    } catch (...) { payload->clear(); return false; }
}

} // namespace hancom::graph::patch

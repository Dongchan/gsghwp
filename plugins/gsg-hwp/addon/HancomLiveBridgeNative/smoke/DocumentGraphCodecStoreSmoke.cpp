#include "../DocumentGraphCodec.h"
#include "../DocumentGraphCodecInternal.h"
#include "../DocumentGraphCaptureRecords.h"
#include "../DocumentGraphProperties.h"
#include "../DocumentGraphLayout.h"
#include "../DocumentGraphQuery.h"
#include "../DocumentGraphStore.h"

#include <Windows.h>
#include <array>
#include <condition_variable>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <limits>
#include <mutex>
#include <numeric>
#include <string>
#include <thread>
#include <vector>

namespace {
using namespace hancom::graph;
using namespace hancom::graph::codec;
using namespace hancom::graph::store;

Bytes LayoutEnvironment(const std::uint32_t dpiX) {
  layout::LayoutEnvironmentV1 environment;
  environment.hwpFileVersion = {13, 0, 0, 1};
  environment.printerName = L"Codec Fixture Printer";
  environment.devmodeDigest.bytes.fill(0x11);
  environment.fontInventoryDigest.bytes.fill(0x22);
  environment.dpiX = dpiX;
  environment.dpiY = 96;
  environment.systemLcid = 0x409;
  environment.pageSetupDigest.bytes.fill(0x33);
  Bytes serialized;
  if (!layout::SerializeLayoutEnvironmentV1(environment, &serialized))
    return {};
  return serialized;
}

bool Expect(bool condition, const wchar_t *label) {
  if (!condition)
    std::wcerr << L"DocumentGraphCodecStoreSmoke failed: " << label << L'\n';
  return condition;
}
std::string Hex(ByteView bytes) {
  constexpr char digits[] = "0123456789abcdef";
  std::string result;
  for (std::uint64_t index = 0; index < bytes.size; ++index) {
    result.push_back(digits[bytes.data[index] >> 4]);
    result.push_back(digits[bytes.data[index] & 15]);
  }
  return result;
}
std::string Hex(const Sha256 &digest) {
  return Hex({digest.bytes.data(), digest.bytes.size()});
}
Bytes EncodedField(const Field &field) {
  Bytes bytes;
  static_cast<void>(EncodeField(field, &bytes));
  return bytes;
}
Bytes Nested(std::initializer_list<Field> fields) {
  Bytes bytes;
  for (const Field &field : fields) {
    const Bytes encoded = EncodedField(field);
    bytes.insert(bytes.end(), encoded.begin(), encoded.end());
  }
  return bytes;
}
Bytes Obs(ObservationState state, ScalarTag scalar, const Bytes &value = {},
          std::int32_t hresult = 0, const Bytes &detail = {}) {
  Error error = Error::None;
  const Bytes result = Observation(
      state, hresult,
      detail.empty() ? nullptr
                     : reinterpret_cast<const std::uint16_t *>(detail.data()),
      detail.size() / 2, View(value), &error);
  static_cast<void>(scalar);
  return error == Error::None ? result : Bytes{};
}
Bytes EmptyArray(ScalarTag scalar) { return ArrayValue(scalar, 0, {}); }
Uuid128 Uuid(std::uint32_t value) {
  Uuid128 id{};
  id.bytes[0] = static_cast<std::uint8_t>(value);
  id.bytes[1] = static_cast<std::uint8_t>(value >> 8);
  id.bytes[2] = static_cast<std::uint8_t>(value >> 16);
  id.bytes[3] = static_cast<std::uint8_t>(value >> 24);
  return id;
}
Uuid128 RfcUuid(std::uint32_t value) {
  Uuid128 id = Uuid(value);
  id.bytes[6] = 0x40;
  id.bytes[8] = 0x80;
  return id;
}
Bytes UuidBytes(const Uuid128 &id) {
  return {id.bytes.begin(), id.bytes.end()};
}
std::vector<std::uint64_t> NonzeroIds(
    const std::initializer_list<std::uint64_t> ids) {
  std::vector<std::uint64_t> result;
  for (const std::uint64_t id : ids)
    if (id != 0)
      result.push_back(id);
  return result;
}

Bytes ManifestRecord(std::uint64_t total,
                     std::uint64_t profiles = ProfileBit(ProfileId::Structure),
                     StreamKind streamKind = StreamKind::Capture,
                     const Sha256 *curatedRoot = nullptr,
                     CaptureIntegrity integrity = CaptureIntegrity::Complete) {
  Bytes version(kGraphVersionBytesV1, 0);
  const Bytes schema = Uint16(kCodecVersion);
  const Bytes profileBytes = Uint64(profiles);
  std::copy(schema.begin(), schema.end(), version.begin());
  std::copy(profileBytes.begin(), profileBytes.end(), version.begin() + 2);
  if (curatedRoot != nullptr) {
    version[10] = 1;
    std::copy(curatedRoot->bytes.begin(), curatedRoot->bytes.end(),
              version.begin() + 72);
    std::copy(curatedRoot->bytes.begin(), curatedRoot->bytes.end(),
              version.begin() + 136);
  }
  LogicalRecord record{
      RecordKind::Manifest,
      kFieldFlagRequired,
      0,
      {{1, kFieldFlagRequired, ScalarTag::Struct, 1, version},
       {2, kFieldFlagRequired, ScalarTag::Uint8, 1,
        Uint8(static_cast<std::uint8_t>(integrity))},
       {3, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(profiles)},
       {4, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(total)},
       {5, kFieldFlagRequired, ScalarTag::SHA256, 1, Bytes(32, 0)},
       {6, kFieldFlagRequired, ScalarTag::Uint8, 1,
        Uint8(static_cast<std::uint8_t>(streamKind))}}};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
struct BlobNodeInput final {
  ContentId contentId{};
  std::uint64_t physicalOffset = 0;
  Bytes content{};
  std::uint64_t callbackCount = 0;
};
Bytes NodeRecord(
    std::uint64_t recordId, NodeKind kind, const Uuid128 &nodeId,
    const Uuid128 *parent, std::uint64_t ordinal,
    const std::vector<std::uint64_t> &layoutIds = {},
    const std::vector<std::uint64_t> &propertyIds = {},
    const BlobNodeInput *blob = nullptr, const Bytes &captureUtf16 = {},
    const Bytes &unavailableDetail = {},
    ObservationState unavailableState = ObservationState::NotExposed,
    std::int64_t definitionNative = 0,
    DefinitionKind definitionKind = DefinitionKind::Style,
    const Sha256* definitionAggregate = nullptr,
    ObservationState definitionNativeState = ObservationState::Value) {
  const Bytes locator =
      Obs(ObservationState::Value, ScalarTag::Struct, Bytes(8, 0));
  Bytes common;
  const auto append = [&](const Field &field) {
    const Bytes encoded = EncodedField(field);
    common.insert(common.end(), encoded.begin(), encoded.end());
  };
  append({1, kFieldFlagRequired, ScalarTag::UUID128, 1, UuidBytes(nodeId)});
  append({2, kFieldFlagRequired, ScalarTag::Uint16, 1,
          Uint16(static_cast<std::uint16_t>(kind))});
  if (parent != nullptr)
    append({3, 0, ScalarTag::UUID128, 1, UuidBytes(*parent)});
  append({4, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(ordinal)});
  append({5, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(1)});
  append({6, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(1)});
  append({7, kFieldFlagRequired, ScalarTag::Struct, 1, locator});
  append({8, kFieldFlagRequired, ScalarTag::SHA256, 1, Bytes(32, 0)});
  std::vector<Bytes> layoutElements;
  for (std::uint64_t id : layoutIds)
    layoutElements.push_back(Uint64(id));
  append({10, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
          ScalarTag::Uint64, layoutIds.size(),
          ArrayValue(ScalarTag::Uint64, 0, layoutElements)});
  append({11, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
          ScalarTag::Uint64, 0, EmptyArray(ScalarTag::Uint64)});

  Bytes payload;
  const auto payloadAppend = [&](const Field &field) {
    const Bytes encoded = EncodedField(field);
    payload.insert(payload.end(), encoded.begin(), encoded.end());
  };
  if (kind == NodeKind::Document) {
    std::vector<Bytes> metadataProperties;
    for (std::uint64_t id : propertyIds)
      metadataProperties.push_back(Uint64(id));
    payloadAppend(
        {100, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
         ScalarTag::Uint64, propertyIds.size(),
         ArrayValue(ScalarTag::Uint64, 0, metadataProperties)});
  } else if (kind == NodeKind::Story) {
    payloadAppend({100, kFieldFlagRequired, ScalarTag::Uint16, 1,
                   Uint16(static_cast<std::uint16_t>(StoryKind::Body))});
    payloadAppend({101, kFieldFlagRequired, ScalarTag::UUID128, 1,
                   Obs(ObservationState::NotApplicable, ScalarTag::UUID128)});
    payloadAppend({102, kFieldFlagRequired, ScalarTag::Sint64, 1,
                   Obs(ObservationState::Value, ScalarTag::Sint64, Sint64(0))});
  } else if (kind == NodeKind::Section) {
    payloadAppend(
        {100, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(ordinal)});
    payloadAppend(
        {101, kFieldFlagRequired, ScalarTag::Struct, 1,
         Obs(ObservationState::Value, ScalarTag::Struct, Bytes(24, 0))});
    payloadAppend(
        {102, kFieldFlagRequired, ScalarTag::Struct, 1,
         Obs(ObservationState::Value, ScalarTag::Struct, Bytes(24, 0))});
    payloadAppend(
        {103, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
         ScalarTag::Uint64, 0, EmptyArray(ScalarTag::Uint64)});
  } else if (kind == NodeKind::Paragraph) {
    payloadAppend(
        {100, kFieldFlagRequired, ScalarTag::Struct, 1,
         Obs(ObservationState::Value, ScalarTag::Struct, Bytes(24, 0))});
    payloadAppend(
        {101, kFieldFlagRequired, ScalarTag::Struct, 1,
         Obs(ObservationState::Value, ScalarTag::Struct, Bytes(24, 0))});
    payloadAppend({102, kFieldFlagRequired, ScalarTag::Sint64, 1,
                   Obs(ObservationState::NotRequested, ScalarTag::Sint64)});
    std::vector<Bytes> propertyElements;
    for (std::uint64_t id : propertyIds)
      propertyElements.push_back(Uint64(id));
    payloadAppend(
        {103, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
         ScalarTag::Uint64, propertyIds.size(),
         ArrayValue(ScalarTag::Uint64, 0, propertyElements)});
    payloadAppend(
        {104, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
         ScalarTag::Uint64, 0, EmptyArray(ScalarTag::Uint64)});
  } else if (kind == NodeKind::Definition) {
    payloadAppend({100, kFieldFlagRequired, ScalarTag::Uint16, 1,
                   Uint16(static_cast<std::uint16_t>(definitionKind))});
    payloadAppend({101, kFieldFlagRequired, ScalarTag::Sint64, 1,
                   Obs(definitionNativeState, ScalarTag::Sint64,
                       definitionNativeState == ObservationState::Value
                           ? Sint64(definitionNative)
                           : Bytes{})});
    std::vector<Bytes> definitionProperties;
    for (std::uint64_t id : propertyIds)
      definitionProperties.push_back(Uint64(id));
    payloadAppend(
        {102, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
         ScalarTag::Uint64, propertyIds.size(),
         ArrayValue(ScalarTag::Uint64, 0, definitionProperties)});
    const Sha256 emptyAggregate = DefinitionPropertyAggregate({});
    const Sha256& aggregate = definitionAggregate == nullptr
        ? emptyAggregate : *definitionAggregate;
    payloadAppend(
        {103, kFieldFlagRequired, ScalarTag::SHA256, 1,
         Bytes(aggregate.bytes.begin(), aggregate.bytes.end())});
  } else if (kind == NodeKind::BinaryData && blob != nullptr) {
    const Sha256 digest = Hash(View(blob->content));
    payloadAppend({100, kFieldFlagRequired, ScalarTag::UUID128, 1,
                   UuidBytes(blob->contentId)});
    payloadAppend({101, kFieldFlagRequired, ScalarTag::Uint64, 1,
                   Uint64(blob->content.size())});
    payloadAppend({102, kFieldFlagRequired, ScalarTag::SHA256, 1,
                   Bytes(digest.bytes.begin(), digest.bytes.end())});
    payloadAppend(
        {103, kFieldFlagRequired, ScalarTag::UTF16, 1,
         Obs(ObservationState::Value, ScalarTag::UTF16, captureUtf16)});
  } else if (kind == NodeKind::CharacterRun && blob != nullptr &&
             parent != nullptr) {
    Bytes range = UuidBytes(*parent);
    const Bytes offsets = Uint64(0);
    range.insert(range.end(), offsets.begin(), offsets.end());
    const Bytes end = Uint64(blob->content.size());
    range.insert(range.end(), end.begin(), end.end());
    const Sha256 digest = Hash(View(blob->content));
    payloadAppend({100, kFieldFlagRequired, ScalarTag::Struct, 1, range});
    payloadAppend({101, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
                   Obs(ObservationState::Value, ScalarTag::BlobSlice,
                       BlobSliceValue(blob->contentId, blob->physicalOffset,
                                      blob->content.size(), digest))});
    std::vector<Bytes> characterProperties;
    for (std::uint64_t id : propertyIds)
      characterProperties.push_back(Uint64(id));
    payloadAppend(
        {102, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
         ScalarTag::Uint64, propertyIds.size(),
         ArrayValue(ScalarTag::Uint64, 0, characterProperties)});
  } else {
    constexpr std::uint16_t ctrl[]{L'c', L't', L'r', L'l'};
    const Bytes position(24, 0);
    payloadAppend({100, kFieldFlagRequired, ScalarTag::UTF16, 1,
                   Obs(ObservationState::Value, ScalarTag::UTF16,
                       Utf16(ctrl, std::size(ctrl)))});
    payloadAppend(
        {101, kFieldFlagRequired, ScalarTag::UTF16, 1,
         captureUtf16.empty()
             ? Obs(ObservationState::NotApplicable, ScalarTag::UTF16)
             : Obs(ObservationState::Value, ScalarTag::UTF16, captureUtf16)});
    payloadAppend({102, kFieldFlagRequired, ScalarTag::UTF16, 1,
                   Obs(unavailableState, ScalarTag::UTF16, {}, E_NOTIMPL,
                       unavailableDetail)});
    payloadAppend({103, kFieldFlagRequired, ScalarTag::Struct, 1,
                   Obs(ObservationState::Value, ScalarTag::Struct, position)});
    payloadAppend({104, kFieldFlagRequired, ScalarTag::Struct, 1,
                   Obs(ObservationState::NotApplicable, ScalarTag::Struct)});
    payloadAppend(
        {105, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(ordinal)});
    payloadAppend(
        {106, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
         ScalarTag::Uint64, 0, EmptyArray(ScalarTag::Uint64)});
  }
  LogicalRecord record{
      RecordKind::Node,
      kFieldFlagRequired,
      recordId,
      {{1, kFieldFlagRequired, ScalarTag::Struct, 1, common},
       {2, kFieldFlagRequired, ScalarTag::Struct, 1, payload}}};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
Bytes EdgeRecord(std::uint64_t recordId, EdgeKind edge, const Uuid128 &source,
                 const Uuid128 &target, std::uint64_t ordinal = 0) {
  LogicalRecord record{
      RecordKind::Edge,
      kFieldFlagRequired,
      recordId,
      {{1, kFieldFlagRequired, ScalarTag::Uint16, 1,
        Uint16(static_cast<std::uint16_t>(edge))},
       {2, kFieldFlagRequired, ScalarTag::UUID128, 1, UuidBytes(source)},
       {3, kFieldFlagRequired, ScalarTag::UUID128, 1, UuidBytes(target)},
       {4, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(ordinal)}}};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
Bytes PropertyRecord(std::uint64_t recordId, const Uuid128 &owner,
                     FieldTag ownerField, PropertyKeyId key, ScalarTag scalar,
                     const Bytes &value, PropertyOrigin origin,
                     ObservationState state = ObservationState::Value) {
  LogicalRecord record{
      RecordKind::Property,
      kFieldFlagRequired,
      recordId,
      {{1, kFieldFlagRequired, ScalarTag::UUID128, 1, UuidBytes(owner)},
       {2, kFieldFlagRequired, ScalarTag::Uint16, 1, Uint16(ownerField)},
       {3, kFieldFlagRequired, ScalarTag::Uint32, 1, Uint32(key)},
       {4, kFieldFlagRequired, ScalarTag::Struct, 1,
        Obs(state, scalar, state == ObservationState::Value ? value : Bytes{},
            state == ObservationState::ReadFailed ||
                    state == ObservationState::NotExposed
                ? E_NOTIMPL
                : 0)},
       {5, kFieldFlagRequired, ScalarTag::Uint8, 1,
        Uint8(static_cast<std::uint8_t>(origin))}}};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
Bytes AssetChunkRecord(std::uint64_t recordId, const BlobNodeInput &asset,
                       const Bytes &content) {
  const Sha256 digest = Hash(View(asset.content));
  LogicalRecord record{
      RecordKind::AssetChunk,
      kFieldFlagRequired,
      recordId,
      {{1, kFieldFlagRequired, ScalarTag::UUID128, 1,
        UuidBytes(asset.contentId)},
       {2, kFieldFlagRequired, ScalarTag::Uint64, 1,
        Uint64(asset.content.size())},
       {3, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(0)},
       {4, kFieldFlagRequired, ScalarTag::Bytes, 1, BytesValue(View(content))},
       {5, kFieldFlagRequired, ScalarTag::SHA256, 1,
        Bytes(digest.bytes.begin(), digest.bytes.end())}}};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
Bytes CoverageRecord(std::uint64_t recordId, const Uuid128 &owner,
                     const BlobNodeInput &detail,
                     ProfileId profile = ProfileId::Structure,
                     CoverageState state = CoverageState::Complete,
                     PropertyKeyId propertyKey = 0,
                     RecordKind ownerKind = RecordKind::Node,
                     FieldTag ownerField = 100,
                     bool ownerPresent = true) {
  const Sha256 digest = Hash(View(detail.content));
  std::vector<Field> fields;
  if (ownerPresent)
    fields.push_back({1, 0, ScalarTag::UUID128, 1, UuidBytes(owner)});
  fields.push_back(
      {2, kFieldFlagRequired, ScalarTag::Uint8, 1,
       Uint8(static_cast<std::uint8_t>(profile))});
  fields.push_back(
      {3, kFieldFlagRequired, ScalarTag::Uint8, 1,
       Uint8(static_cast<std::uint8_t>(state))});
  if (propertyKey != 0)
    fields.push_back({4, 0, ScalarTag::Uint32, 1, Uint32(propertyKey)});
  fields.push_back({5, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
                    BlobSliceValue(detail.contentId, detail.physicalOffset,
                                   detail.content.size(), digest)});
  fields.push_back({6, kFieldFlagRequired, ScalarTag::Uint16, 1,
                    Uint16(static_cast<std::uint16_t>(ownerKind))});
  fields.push_back({7, kFieldFlagRequired, ScalarTag::Uint16, 1,
                    Uint16(ownerField)});
  LogicalRecord record{RecordKind::Coverage,kFieldFlagRequired,recordId,
                       std::move(fields)};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
Bytes TombstoneRecord(std::uint64_t recordId, const Uuid128 &source,
                      std::uint64_t revision, TombstoneReason reason) {
  LogicalRecord record{
      RecordKind::Tombstone, kFieldFlagRequired, recordId,
      {{1, kFieldFlagRequired, ScalarTag::UUID128, 1, UuidBytes(source)},
       {2, kFieldFlagRequired, ScalarTag::Uint64, 1, Uint64(revision)},
       {3, kFieldFlagRequired, ScalarTag::Uint16, 1,
        Uint16(static_cast<std::uint16_t>(reason))}}};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
Bytes RemapRecord(std::uint64_t recordId, const Uuid128 &source,
                  const Uuid128 *target, RemapDisposition disposition,
                  RemapReason reason) {
  std::vector<Field> fields{
      {1, kFieldFlagRequired, ScalarTag::UUID128, 1, UuidBytes(source)}};
  if (target != nullptr)
    fields.push_back({2, 0, ScalarTag::UUID128, 1, UuidBytes(*target)});
  fields.push_back({3, kFieldFlagRequired, ScalarTag::Uint8, 1,
                    Uint8(static_cast<std::uint8_t>(disposition))});
  fields.push_back({4, kFieldFlagRequired, ScalarTag::Uint16, 1,
                    Uint16(static_cast<std::uint16_t>(reason))});
  LogicalRecord record{RecordKind::Remap, kFieldFlagRequired, recordId,
                       std::move(fields)};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
Uuid128 ReceiptUuid(std::uint8_t value) {
  Uuid128 id{};
  id.bytes[0] = value;
  id.bytes[6] = 0x40;
  id.bytes[8] = 0x80;
  return id;
}
Bytes DiagnosticRecord(std::uint64_t recordId, const Uuid128 &owner,
                       std::int32_t hresult,
                       const BlobNodeInput *detail = nullptr) {
  const Bytes empty;
  const Bytes &contentBytes = detail == nullptr ? empty : detail->content;
  const Sha256 digest = Hash(View(contentBytes));
  const ContentId content = detail == nullptr ? ContentId{} : detail->contentId;
  const std::uint64_t physicalOffset =
      detail == nullptr ? 0 : detail->physicalOffset;
  LogicalRecord record{
      RecordKind::Diagnostic,
      kFieldFlagRequired,
      recordId,
      {{1, kFieldFlagRequired, ScalarTag::Uint32, 1,
        Uint32(static_cast<std::uint32_t>(DiagnosticCode::NativeReadFailure))},
       {2, kFieldFlagRequired, ScalarTag::Uint8, 1,
        Uint8(static_cast<std::uint8_t>(Severity::Warning))},
       {3, 0, ScalarTag::UUID128, 1, UuidBytes(owner)},
       {5, kFieldFlagRequired, ScalarTag::Sint32, 1, Sint32(hresult)},
       {6, kFieldFlagRequired, ScalarTag::BlobSlice, 1,
        BlobSliceValue(content, physicalOffset, contentBytes.size(), digest)}}};
  Bytes bytes;
  static_cast<void>(EncodeLogicalRecord(record, &bytes));
  return bytes;
}
bool WriteRecord(HANDLE file, const Bytes &record) {
  DWORD written = 0;
  return record.size() <= MAXDWORD &&
         WriteFile(file, record.data(), static_cast<DWORD>(record.size()),
                   &written, nullptr) != FALSE &&
         written == record.size();
}
struct MemoryReader final {
  const Bytes *bytes = nullptr;
};
bool ReadMemory(void *context, std::uint64_t offset, std::uint8_t *buffer,
                std::uint32_t requested, std::uint32_t *actual) noexcept {
  const auto *reader = static_cast<const MemoryReader *>(context);
  if (reader == nullptr || reader->bytes == nullptr || actual == nullptr ||
      offset > reader->bytes->size())
    return false;
  const std::uint64_t available = reader->bytes->size() - offset;
  *actual = static_cast<std::uint32_t>(
      (std::min<std::uint64_t>)(available, requested));
  if (*actual != 0)
    std::memcpy(buffer, reader->bytes->data() + offset, *actual);
  return true;
}

Bytes CanonicalStoryParagraphStream() {
  const Uuid128 document = Uuid(5011), firstStory = Uuid(5012),
                secondStory = Uuid(5013), section = Uuid(5014),
                paragraph = Uuid(5015);
  Bytes stream = ManifestRecord(10);
  const std::array<Bytes, 9> records{
      NodeRecord(1, NodeKind::Document, document, nullptr, 0),
      EdgeRecord(2, EdgeKind::Contains, document, firstStory, 0),
      EdgeRecord(3, EdgeKind::Contains, document, secondStory, 1),
      NodeRecord(4, NodeKind::Story, firstStory, &document, 0),
      EdgeRecord(5, EdgeKind::Contains, firstStory, section),
      NodeRecord(6, NodeKind::Section, section, &firstStory, 0),
      EdgeRecord(7, EdgeKind::Contains, section, paragraph),
      NodeRecord(8, NodeKind::Paragraph, paragraph, &section, 0),
      NodeRecord(9, NodeKind::Story, secondStory, &document, 1)};
  for (const Bytes &record : records)
    stream.insert(stream.end(), record.begin(), record.end());
  return stream;
}

Bytes RewoundStoryParagraphStream() {
  const Uuid128 document = Uuid(5011), firstStory = Uuid(5012),
                secondStory = Uuid(5013), section = Uuid(5014),
                paragraph = Uuid(5015);
  Bytes stream = ManifestRecord(10);
  const std::array<Bytes, 9> records{
      NodeRecord(1, NodeKind::Document, document, nullptr, 0),
      EdgeRecord(2, EdgeKind::Contains, document, firstStory, 0),
      EdgeRecord(3, EdgeKind::Contains, document, secondStory, 1),
      NodeRecord(4, NodeKind::Story, firstStory, &document, 0),
      EdgeRecord(5, EdgeKind::Contains, firstStory, section),
      NodeRecord(6, NodeKind::Story, secondStory, &document, 1),
      NodeRecord(7, NodeKind::Section, section, &firstStory, 0),
      EdgeRecord(8, EdgeKind::Contains, section, paragraph),
      NodeRecord(9, NodeKind::Paragraph, paragraph, &section, 0)};
  for (const Bytes &record : records)
    stream.insert(stream.end(), record.begin(), record.end());
  return stream;
}

Error CanonicalStreamError(const Bytes &stream) {
  MemoryReader reader{&stream};
  CanonicalizationResult result{};
  return CanonicalizeRecordStream(
      {{&reader, ReadMemory, stream.size(), StreamKind::Capture},
       nullptr, nullptr, {}},
      &result);
}

Error ValidatedStreamError(const Bytes& stream) {
  MemoryReader reader{&stream};
  std::uint64_t count = 0;
  return ValidateRecordStream(
      {&reader, ReadMemory, stream.size(), StreamKind::Capture}, &count);
}

bool StoryParagraphOrderSmoke() {
  const Uuid128 document = Uuid(5021), story = Uuid(5022),
                section = Uuid(5023), paragraph = Uuid(5024);
  Bytes storyBeforeDocument = ManifestRecord(3);
  const Bytes earlyStory =
      NodeRecord(1, NodeKind::Story, story, &document, 0);
  const Bytes lateDocument =
      NodeRecord(2, NodeKind::Document, document, nullptr, 0);
  storyBeforeDocument.insert(storyBeforeDocument.end(), earlyStory.begin(),
                             earlyStory.end());
  storyBeforeDocument.insert(storyBeforeDocument.end(), lateDocument.begin(),
                             lateDocument.end());

  Bytes paragraphBeforeOwners = ManifestRecord(5);
  const Bytes firstDocument =
      NodeRecord(1, NodeKind::Document, document, nullptr, 0);
  const Bytes earlyParagraph =
      NodeRecord(2, NodeKind::Paragraph, paragraph, &section, 0);
  const Bytes lateStory =
      NodeRecord(3, NodeKind::Story, story, &document, 0);
  const Bytes lateSection =
      NodeRecord(4, NodeKind::Section, section, &story, 0);
  for (const Bytes *record :
       {&firstDocument, &earlyParagraph, &lateStory, &lateSection})
    paragraphBeforeOwners.insert(paragraphBeforeOwners.end(), record->begin(),
                                 record->end());

  const bool rejected =
      CanonicalStreamError(storyBeforeDocument) == Error::BadRecordOrder &&
      CanonicalStreamError(paragraphBeforeOwners) == Error::BadRecordOrder &&
      CanonicalStreamError(RewoundStoryParagraphStream()) ==
          Error::BadRecordOrder &&
      CanonicalStreamError(CanonicalStoryParagraphStream()) == Error::None;
  std::wcout << L"CODEC_STORY_PARAGRAPH_ORDER_REJECTION " << rejected
             << L'\n';
  return Expect(rejected,
                L"story and paragraph ownership must remain parent-first and "
                L"depth-first canonical");
}

Bytes CharacterShapePersistedStream(
    const size_t referenceCount, const bool secondTarget,
    const DefinitionKind targetKind, const bool dangling,
    const bool includeProperty,
    std::vector<CoverageState> coverageStates,
    const bool irrelevantCoverage = false) {
    const Uuid128 document = Uuid(5100), definition = Uuid(5101),
                  otherDefinition = Uuid(5102), story = Uuid(5103),
                  section = Uuid(5104), paragraph = Uuid(5105),
                  run = Uuid(5106), missing = Uuid(5199);
    BlobNodeInput text{Uuid(5110), 0, Bytes{L'r', 0}, 0};
    const bool twoDefinitions = secondTarget;
    std::sort(coverageStates.begin(), coverageStates.end());
    const std::uint64_t total = 12 + referenceCount +
        (includeProperty ? 1 : 0) + coverageStates.size() +
        (irrelevantCoverage ? 1 : 0) + (twoDefinitions ? 2 : 0);
    Bytes bytes = ManifestRecord(
        total, ProfileBit(ProfileId::Structure) |
                   ProfileBit(ProfileId::EditableText));
    std::uint64_t id = 1;
    const auto add = [&bytes](const Bytes& record) {
      bytes.insert(bytes.end(), record.begin(), record.end());
    };
    add(NodeRecord(id++, NodeKind::Document, document, nullptr, 0));
    add(EdgeRecord(id++, EdgeKind::Contains, document, definition, 0));
    if (twoDefinitions)
      add(EdgeRecord(id++, EdgeKind::Contains, document, otherDefinition, 1));
    add(EdgeRecord(id++, EdgeKind::Contains, document, story,
                   twoDefinitions ? 2 : 1));
    add(NodeRecord(id++, NodeKind::Definition, definition, &document, 0,
                   {}, {}, nullptr, {}, {}, ObservationState::NotExposed, 0,
                   targetKind, nullptr, ObservationState::NotExposed));
    if (twoDefinitions)
      add(NodeRecord(id++, NodeKind::Definition, otherDefinition, &document, 1,
                     {}, {}, nullptr, {}, {}, ObservationState::NotExposed, 1,
                     DefinitionKind::CharacterShape, nullptr,
                     ObservationState::Value));
    add(NodeRecord(id++, NodeKind::Story, story, &document,
                   twoDefinitions ? 2 : 1));
    add(EdgeRecord(id++, EdgeKind::Contains, story, section));
    add(NodeRecord(id++, NodeKind::Section, section, &story, 0));
    add(EdgeRecord(id++, EdgeKind::Contains, section, paragraph));
    add(NodeRecord(id++, NodeKind::Paragraph, paragraph, &section, 0));
    add(EdgeRecord(id++, EdgeKind::Contains, paragraph, run));
    const std::uint64_t propertyId = id + 1;
    add(NodeRecord(
        id++, NodeKind::CharacterRun, run, &paragraph, 0, {},
        includeProperty ? std::vector<std::uint64_t>{propertyId}
                        : std::vector<std::uint64_t>{},
        &text));
    if (includeProperty)
      add(PropertyRecord(id++, run, 102, 1000, ScalarTag::UTF16,
                         Utf16(reinterpret_cast<const std::uint16_t*>(L"Face"),
                               4), PropertyOrigin::Unknown));
    size_t coverageIndex = 0;
    for (const CoverageState state : coverageStates) {
      BlobNodeInput detail{
          Uuid(static_cast<std::uint32_t>(5111 + coverageIndex)), 0,
          Bytes{static_cast<std::uint8_t>('n' + coverageIndex), 0}, 0};
      add(CoverageRecord(id++, run, detail, ProfileId::EditableText,
                         state, 0, RecordKind::Node, 102));
      ++coverageIndex;
    }
    if (irrelevantCoverage) {
      BlobNodeInput irrelevant{Uuid(5198), 0, Bytes{L'i', 0}, 0};
      add(CoverageRecord(id++, run, irrelevant, ProfileId::Layout,
                         CoverageState::Complete, 0, RecordKind::Node, 102));
    }
    for (size_t index = 0; index < referenceCount; ++index) {
      const Uuid128& target = dangling ? missing
          : secondTarget && index != 0 ? otherDefinition : definition;
      add(EdgeRecord(id++, EdgeKind::CharacterShapeRef, run, target, index));
    }
    return bytes;
}

bool CharacterShapePersistedCardinalitySmoke() {
  const auto stream = [](
      const size_t references, const bool secondTarget,
      const DefinitionKind targetKind, const bool dangling,
      const bool property, std::vector<CoverageState> coverage,
      const bool irrelevant = false) {
    return CharacterShapePersistedStream(
        references, secondTarget, targetKind, dangling, property,
        std::move(coverage), irrelevant);
  };
  const Error valid = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, false, true,
             {CoverageState::Complete}));
  const Error absentCoverage = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, false, true, {}));
  const Error zero = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, true,
             {CoverageState::Complete}));
  const Error propertyFreeZero = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::Complete}));
  const Error propertyFreeOne = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::Complete}));
  const Error duplicate = ValidatedStreamError(
      stream(2, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::Complete}));
  const Error twoTarget = ValidatedStreamError(
      stream(2, true, DefinitionKind::CharacterShape, false, false,
             {CoverageState::Complete}));
  const Error dangling = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, true, false,
             {CoverageState::Complete}));
  const Error wrongKind = ValidatedStreamError(
      stream(1, false, DefinitionKind::Style, false, false,
             {CoverageState::Complete}));
  const Error mixedNotExposed = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::Complete, CoverageState::NotExposed}));
  const Error mixedReadFailed = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::Complete, CoverageState::ReadFailed}));
  const Error validReadFailedTerminal = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::ReadFailed}));
  const Error notRequested = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::NotRequested}));
  const Error notApplicable = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::NotApplicable}));
  const Error mixedTerminals = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::ReadFailed, CoverageState::NotExposed}));
  const Error terminalWithProperty = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, true,
             {CoverageState::NotExposed}));
  const Error terminalWithReference = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::NotExposed}));
  const Error terminalWithData = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, false, true,
             {CoverageState::NotExposed}));
  const Error duplicateCompleteCoverage = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, false, true,
             {CoverageState::Complete, CoverageState::Complete}));
  const Error duplicateNotExposedCoverage = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::NotExposed, CoverageState::NotExposed}));
  const Error duplicateReadFailedCoverage = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::ReadFailed, CoverageState::ReadFailed}));
  const Error extraCoverage = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, false, true,
             {CoverageState::ProjectionOmitted, CoverageState::Complete}));
  const Error irrelevantCoverage = ValidatedStreamError(
      stream(1, false, DefinitionKind::CharacterShape, false, true,
             {CoverageState::Complete}, true));
  const Error terminal = ValidatedStreamError(
      stream(0, false, DefinitionKind::CharacterShape, false, false,
             {CoverageState::NotExposed}));
  const bool passed = valid == Error::None &&
      absentCoverage == Error::BadRecordOrder &&
      zero == Error::BadRecordOrder &&
      propertyFreeZero == Error::BadRecordOrder &&
      propertyFreeOne == Error::BadRecordOrder &&
      duplicate == Error::BadRecordOrder &&
      twoTarget == Error::BadRecordOrder &&
      dangling == Error::BadRecordOrder &&
      wrongKind == Error::BadRecordOrder &&
      mixedNotExposed == Error::BadRecordOrder &&
      mixedReadFailed == Error::BadRecordOrder &&
      validReadFailedTerminal == Error::None &&
      notRequested == Error::BadRecordOrder &&
      notApplicable == Error::BadRecordOrder &&
      mixedTerminals == Error::BadRecordOrder &&
      terminalWithProperty == Error::BadRecordOrder &&
      terminalWithReference == Error::BadRecordOrder &&
      terminalWithData == Error::BadRecordOrder &&
      duplicateCompleteCoverage == Error::BadRecordOrder &&
      duplicateNotExposedCoverage == Error::BadRecordOrder &&
      duplicateReadFailedCoverage == Error::BadRecordOrder &&
      extraCoverage == Error::IllegalStreamState &&
      irrelevantCoverage == Error::None && terminal == Error::None;
  std::wcout << L"CODEC_CHARACTER_SHAPE_CARDINALITY_REJECTION "
             << passed << L" errors=" << static_cast<unsigned>(valid)
             << L',' << static_cast<unsigned>(absentCoverage)
             << L',' << static_cast<unsigned>(zero)
             << L',' << static_cast<unsigned>(propertyFreeZero)
             << L',' << static_cast<unsigned>(propertyFreeOne)
             << L',' << static_cast<unsigned>(duplicate)
             << L',' << static_cast<unsigned>(twoTarget)
             << L',' << static_cast<unsigned>(dangling)
             << L',' << static_cast<unsigned>(wrongKind)
             << L',' << static_cast<unsigned>(mixedNotExposed)
             << L',' << static_cast<unsigned>(mixedReadFailed)
             << L',' << static_cast<unsigned>(validReadFailedTerminal)
             << L',' << static_cast<unsigned>(notRequested)
             << L',' << static_cast<unsigned>(notApplicable)
             << L',' << static_cast<unsigned>(mixedTerminals)
             << L',' << static_cast<unsigned>(terminalWithProperty)
             << L',' << static_cast<unsigned>(terminalWithReference)
             << L',' << static_cast<unsigned>(terminalWithData)
             << L',' << static_cast<unsigned>(duplicateCompleteCoverage)
             << L',' << static_cast<unsigned>(duplicateNotExposedCoverage)
             << L',' << static_cast<unsigned>(duplicateReadFailedCoverage)
             << L',' << static_cast<unsigned>(extraCoverage)
             << L',' << static_cast<unsigned>(irrelevantCoverage)
             << L',' << static_cast<unsigned>(terminal) << L'\n';
  return Expect(passed,
      L"complete CharacterRun CharacterShapeRef closure is exact and atomic");
}

bool CodecSmoke() {
  bool ok = StoryParagraphOrderSmoke() &&
      CharacterShapePersistedCardinalitySmoke();
  Error error = Error::None;
  constexpr std::uint16_t rawText[]{0xd800, 0x0041, 0xdc00};
  ok = Expect(Hex(View(Utf16(rawText, 3))) == "030000000000000000d8410000dc",
              L"raw UTF16") &&
       ok;
  static_cast<void>(
      Float64((std::numeric_limits<double>::quiet_NaN)(), &error));
  ok = Expect(error == Error::NonFiniteFloat, L"NaN rejected") && ok;
  const Sha256 exactDomain = DomainHash("A\0B", View(Bytes{}));
  ok = Expect(Hex(exactDomain) ==
                  "76fe3925c7167317f2df68454339f5ec3650e4062178b4f2be219b1"
                  "05a507907",
              L"domain excludes implicit terminator") &&
       ok;

  Field nonArray{1, kFieldFlagRequired, ScalarTag::Uint8, 2, Uint8(1)};
  Bytes encoded;
  ok = Expect(EncodeField(nonArray, &encoded) == Error::BadArray,
              L"non-array count must be one") &&
       ok;
  const Bytes array = ArrayValue(ScalarTag::Uint64, 0, {Uint64(1)});
  Field wrongOuter{
      1, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
      ScalarTag::Sint64, 1, array};
  ok = Expect(EncodeField(wrongOuter, &encoded) == Error::BadArray,
              L"outer/inner scalar equality") &&
       ok;
  Field wrongCount{
      1, static_cast<std::uint16_t>(kFieldFlagRequired | kFieldFlagArray),
      ScalarTag::Uint64, 2, array};
  ok = Expect(EncodeField(wrongCount, &encoded) == Error::BadArray,
              L"outer/inner/actual count equality") &&
       ok;
  for (std::uint8_t state = 0; state <= 6; ++state) {
    const ObservationState observationState =
        static_cast<ObservationState>(state);
    const Bytes value = state == 0 ? Uint8(9) : Bytes{};
    const std::int32_t hr = state == 3 ? E_FAIL : 0;
    const Bytes envelope = Obs(observationState, ScalarTag::Uint8, value, hr);
    ok = Expect(!envelope.empty() &&
                    ValidateObservation(View(envelope), ScalarTag::Uint8) ==
                        Error::None,
                L"all seven observation states") &&
         ok;
  }
  ok = Expect(ValidateNestedStructure(NestedSchema::Position,
                                      View(Bytes(23, 0))) ==
                      Error::BadNestedStruct &&
                  ValidateNestedStructure(NestedSchema::Position,
                                          View(Bytes(24, 0))) == Error::None &&
                  ValidateNestedStructure(NestedSchema::ParagraphRange,
                                          View(Bytes(32, 0))) == Error::None &&
                  ValidateNestedStructure(NestedSchema::Size,
                                          View(Bytes(16, 0))) == Error::None &&
                  ValidateNestedStructure(NestedSchema::Crop,
                                          View(Bytes(32, 0))) == Error::None &&
                  ValidateNestedStructure(NestedSchema::BlobRef,
                                          View(Bytes(56, 0))) == Error::None &&
                  ValidateNestedStructure(NestedSchema::NativeLocator,
                                          View(Bytes(8, 0))) == Error::None,
              L"fixed and locator nested schemas") &&
       ok;
  const Bytes tab = Nested(
      {{1, kFieldFlagRequired, ScalarTag::RawURC32, 1, Uint32(1)},
       {2, kFieldFlagRequired, ScalarTag::Enum, 1, Enum(2, nullptr, 0)},
       {3, kFieldFlagRequired, ScalarTag::Enum, 1, Enum(3, nullptr, 0)}});
  ok = Expect(ValidateNestedStructure(NestedSchema::TabItem, View(tab)) ==
                  Error::None,
              L"TabItem exact schema") &&
       ok;

  Bytes stream = ManifestRecord(2);
  const Uuid128 document = Uuid(1);
  const Bytes documentRecord =
      NodeRecord(1, NodeKind::Document, document, nullptr, 0);
  stream.insert(stream.end(), documentRecord.begin(), documentRecord.end());
  MemoryReader reader{&stream};
  std::uint64_t count = 0;
  ok = Expect(ValidateRecordStream(
                  {&reader, ReadMemory, stream.size(), StreamKind::Capture},
                  &count) == Error::None &&
                  count == 2,
              L"record-driven validation") &&
       ok;
  const Bytes title=Utf16(std::array<std::uint16_t,1>{L'T'}.data(),1);
  Bytes optionalMetadata=ManifestRecord(3);
  const Bytes metadataDocument=NodeRecord(
      1,NodeKind::Document,document,nullptr,0,{},std::vector<std::uint64_t>{2});
  optionalMetadata.insert(optionalMetadata.end(),metadataDocument.begin(),metadataDocument.end());
  const Bytes titleProperty=PropertyRecord(2,document,100,13000,ScalarTag::UTF16,
                                           title,PropertyOrigin::Direct);
  optionalMetadata.insert(optionalMetadata.end(),titleProperty.begin(),titleProperty.end());
  MemoryReader optionalMetadataReader{&optionalMetadata};
  CanonicalizationResult absentMetadataRoot{},presentMetadataRoot{};
  ok=Expect(CanonicalizeRecordStream({{&reader,ReadMemory,stream.size(),StreamKind::Capture},
                                      nullptr,nullptr,{}},&absentMetadataRoot)==Error::None &&
            CanonicalizeRecordStream({{&optionalMetadataReader,ReadMemory,optionalMetadata.size(),StreamKind::Capture},
                                      nullptr,nullptr,{}},&presentMetadataRoot)==Error::None &&
            absentMetadataRoot.semanticCertified && presentMetadataRoot.semanticCertified &&
            !Equal(absentMetadataRoot.observedSemanticRoot,presentMetadataRoot.observedSemanticRoot) &&
            !Equal(absentMetadataRoot.captureRoot,presentMetadataRoot.captureRoot),
            L"optional metadata absence complete and emitted value rooted") && ok;
  Bytes provenanceMetadata=ManifestRecord(3);
  provenanceMetadata.insert(provenanceMetadata.end(),metadataDocument.begin(),metadataDocument.end());
  const Bytes savedByProperty=PropertyRecord(2,document,100,13009,ScalarTag::UTF16,
                                             title,PropertyOrigin::Direct);
  provenanceMetadata.insert(provenanceMetadata.end(),savedByProperty.begin(),savedByProperty.end());
  MemoryReader provenanceMetadataReader{&provenanceMetadata};
  CanonicalizationResult provenanceMetadataRoot{};
  ok=Expect(CanonicalizeRecordStream({{&provenanceMetadataReader,ReadMemory,
                                       provenanceMetadata.size(),StreamKind::Capture},
                                      nullptr,nullptr,{}},&provenanceMetadataRoot)==Error::None &&
            provenanceMetadataRoot.semanticCertified &&
            Equal(absentMetadataRoot.observedSemanticRoot,
                  provenanceMetadataRoot.observedSemanticRoot) &&
            !Equal(absentMetadataRoot.captureRoot,provenanceMetadataRoot.captureRoot),
            L"capture-only optional metadata follows declared root") && ok;

  Bytes unavailableMetadata=ManifestRecord(3);
  unavailableMetadata.insert(unavailableMetadata.end(),metadataDocument.begin(),metadataDocument.end());
  const Bytes unavailableTitle=PropertyRecord(2,document,100,13000,ScalarTag::UTF16,
                                               {},PropertyOrigin::Direct,
                                               ObservationState::NotExposed);
  unavailableMetadata.insert(unavailableMetadata.end(),unavailableTitle.begin(),unavailableTitle.end());
  MemoryReader unavailableMetadataReader{&unavailableMetadata};
  CanonicalizationResult unavailableMetadataRoot{};
  ok=Expect(CanonicalizeRecordStream({{&unavailableMetadataReader,ReadMemory,
                                       unavailableMetadata.size(),StreamKind::Capture},
                                      nullptr,nullptr,{}},&unavailableMetadataRoot)==Error::None &&
            unavailableMetadataRoot.semanticCertified &&
            Equal(absentMetadataRoot.observedSemanticRoot,
                  unavailableMetadataRoot.observedSemanticRoot) &&
            !Equal(absentMetadataRoot.captureRoot,unavailableMetadataRoot.captureRoot),
            L"optional unavailable observation remains complete and capture-rooted") && ok;

  bool allWithdrawnRejected=true;
  bool withdrawn7103Rejected=false;
  for (const PropertyKeyId key:kWithdrawnPropertyKeyIdsV1) {
    Bytes withdrawn=ManifestRecord(3);
    withdrawn.insert(withdrawn.end(),metadataDocument.begin(),metadataDocument.end());
    const Bytes withdrawnProperty=PropertyRecord(
        2,document,100,key,ScalarTag::Sint64,Sint64(0),PropertyOrigin::Direct);
    withdrawn.insert(withdrawn.end(),withdrawnProperty.begin(),withdrawnProperty.end());
    MemoryReader withdrawnReader{&withdrawn};
    const bool rejected=ValidateRecordStream(
        {&withdrawnReader,ReadMemory,withdrawn.size(),StreamKind::Capture},
        &count)==Error::BadScalar;
    allWithdrawnRejected=allWithdrawnRejected && rejected;
    if (key==7103) withdrawn7103Rejected=rejected;
  }
  ok=Expect(allWithdrawnRejected,
            L"every withdrawn PropertyKey rejected by decoder") && ok;
  std::wcout << L"CODEC_PROPERTY_7103_FORGED_RECORD_REJECTED "
             << withdrawn7103Rejected << L'\n';
  Bytes wrongType=ManifestRecord(3);
  wrongType.insert(wrongType.end(),metadataDocument.begin(),metadataDocument.end());
  const Bytes wrongTypeProperty=PropertyRecord(2,document,100,13000,ScalarTag::Uint64,
                                               Uint64(1),PropertyOrigin::Direct);
  wrongType.insert(wrongType.end(),wrongTypeProperty.begin(),wrongTypeProperty.end());
  MemoryReader wrongTypeReader{&wrongType};
  ok=Expect(ValidateRecordStream({&wrongTypeReader,ReadMemory,wrongType.size(),StreamKind::Capture},
                                 &count)==Error::BadScalar,
            L"emitted optional property remains type-strict") && ok;
  const Bytes widthGapValue=ArrayValue(ScalarTag::Uint16,0,{Uint16(10),Uint16(20)});
  Bytes widthGap=ManifestRecord(3);
  widthGap.insert(widthGap.end(),metadataDocument.begin(),metadataDocument.end());
  const Bytes widthGapProperty=PropertyRecord(2,document,100,7104,ScalarTag::Uint16,
                                              widthGapValue,PropertyOrigin::Direct);
  widthGap.insert(widthGap.end(),widthGapProperty.begin(),widthGapProperty.end());
  MemoryReader widthGapReader{&widthGap};
  CanonicalizationResult invalidOwnerResult{};
  ok=Expect(ValidateRecordStream({&widthGapReader,ReadMemory,widthGap.size(),StreamKind::Capture},
                                 &count)==Error::None &&
            CanonicalizeRecordStream({{&widthGapReader,ReadMemory,widthGap.size(),StreamKind::Capture},
                                      nullptr,nullptr,{}},&invalidOwnerResult)==Error::BadScalar,
            L"Uint16 array shape accepted but optional owner remains strict") && ok;

  Bytes badCount = stream;
  badCount[24 + 8] = 2; // Manifest field 1 element_count low byte.
  MemoryReader badReader{&badCount};
  ok = Expect(ValidateRecordStream({&badReader, ReadMemory, badCount.size(),
                                    StreamKind::Capture},
                                   &count) == Error::BadArray,
              L"stream rejects malformed non-array count") &&
       ok;
  Bytes reservedGraphVersion = stream;
  reservedGraphVersion[60] = 1;
  MemoryReader reservedReader{&reservedGraphVersion};
  ok = Expect(ValidateRecordStream({&reservedReader, ReadMemory,
                                    reservedGraphVersion.size(),
                                    StreamKind::Capture},
                                   &count) == Error::IllegalStreamState,
              L"GraphVersion reserved bytes reject in Capture") &&
       ok;
  Bytes missingRequiredFlag = stream;
  missingRequiredFlag[26] = 0;
  MemoryReader missingFlagReader{&missingRequiredFlag};
  ok = Expect(ValidateRecordStream({&missingFlagReader, ReadMemory,
                                    missingRequiredFlag.size(),
                                    StreamKind::Capture},
                                   &count) == Error::BadFlags,
              L"known required field must carry required flag") &&
       ok;
  Bytes illegalArrayFlag = stream;
  illegalArrayFlag[26] =
      static_cast<std::uint8_t>(kFieldFlagRequired | kFieldFlagArray);
  MemoryReader illegalArrayReader{&illegalArrayFlag};
  ok = Expect(
           ValidateRecordStream({&illegalArrayReader, ReadMemory,
                                 illegalArrayFlag.size(), StreamKind::Capture},
                                &count) != Error::None,
           L"schema rejects illegal array field flag") &&
       ok;

  const Sha256 curated = Hash(View(Bytes{1, 2, 3}));
  Bytes query = ManifestRecord(2, ProfileBit(ProfileId::Structure),
                               StreamKind::QueryView, &curated);
  query.insert(query.end(), documentRecord.begin(), documentRecord.end());
  Bytes reservedQuery = query;
  reservedQuery[61] = 1;
  MemoryReader reservedQueryReader{&reservedQuery};
  ok =
      Expect(ValidateRecordStream({&reservedQueryReader, ReadMemory,
                                   reservedQuery.size(), StreamKind::QueryView},
                                  &count) == Error::IllegalStreamState,
             L"GraphVersion reserved bytes reject in QueryView") &&
      ok;
  MemoryReader queryReader{&query};
  CanonicalizationResult queryResult{};
  ok = Expect(CanonicalizeRecordStream({{&queryReader, ReadMemory, query.size(),
                                         StreamKind::QueryView},
                                        nullptr,
                                        nullptr,
                                        {}},
                                       &queryResult) == Error::None &&
                  queryResult.streamKind == StreamKind::QueryView &&
                  !queryResult.rootsPresent &&
                  !Equal(queryResult.observedSemanticRoot, curated) &&
                  !Equal(queryResult.captureRoot, curated) &&
                  queryResult.recordStream.itemCount == 2 &&
                  queryResult.recordStream.byteLength == query.size(),
              L"QueryView returns stream identity without curated roots") &&
       ok;

  // DefinitionKind is a closed uint16 domain. Exercise every value outside
  // 0..7 through the authoritative canonicalization boundary, with a
  // Definition that has no semantic reference edge (only its required
  // Contains parent).
  bool unknownDefinitionKindsRejected = true;
  for (std::uint32_t raw = 8; raw <= UINT16_MAX; ++raw) {
    const Uuid128 unknownDefinition = Uuid(raw + 100);
    Bytes unknown = ManifestRecord(4);
    const Bytes unknownDocument =
        NodeRecord(1, NodeKind::Document, document, nullptr, 0);
    const Bytes unknownContains =
        EdgeRecord(2, EdgeKind::Contains, document, unknownDefinition);
    const Bytes unknownNode = NodeRecord(
        3, NodeKind::Definition, unknownDefinition, &document, 0, {}, {},
        nullptr, {}, {}, ObservationState::NotExposed, 0,
        static_cast<DefinitionKind>(raw));
    unknown.insert(unknown.end(), unknownDocument.begin(), unknownDocument.end());
    unknown.insert(unknown.end(), unknownContains.begin(), unknownContains.end());
    unknown.insert(unknown.end(), unknownNode.begin(), unknownNode.end());
    if (CanonicalStreamError(unknown) != Error::BadScalar) {
      unknownDefinitionKindsRejected = false;
      break;
    }
  }
  std::wcout << L"CODEC_UNKNOWN_DEFINITION_KINDS_REJECTED "
             << unknownDefinitionKindsRejected << L'\n';
  ok = Expect(unknownDefinitionKindsRejected,
              L"all unknown DefinitionKind values rejected by codec") && ok;

  constexpr std::array<DefinitionKind, 8> knownDefinitionKinds{
      DefinitionKind::Style,          DefinitionKind::CharacterShape,
      DefinitionKind::ParagraphShape, DefinitionKind::Numbering,
      DefinitionKind::BorderFill,     DefinitionKind::TabDef,
      DefinitionKind::PageDef,        DefinitionKind::ColumnDef};
  bool knownDefinitionKindsAccepted = true;
  for (std::size_t index = 0; index < knownDefinitionKinds.size(); ++index) {
    const Uuid128 knownDocument = Uuid(70000 + index * 2);
    const Uuid128 knownDefinition = Uuid(70001 + index * 2);
    Bytes known = ManifestRecord(4);
    const std::array<Bytes, 3> records{
        NodeRecord(1, NodeKind::Document, knownDocument, nullptr, 0),
        EdgeRecord(2, EdgeKind::Contains, knownDocument, knownDefinition),
        NodeRecord(3, NodeKind::Definition, knownDefinition, &knownDocument, 0,
                   {}, {}, nullptr, {}, {}, ObservationState::NotExposed, 0,
                   knownDefinitionKinds[index])};
    for (const Bytes &record : records)
      known.insert(known.end(), record.begin(), record.end());
    if (CanonicalStreamError(known) != Error::None) {
      knownDefinitionKindsAccepted = false;
      break;
    }
  }
  std::wcout << L"CODEC_KNOWN_DEFINITION_KINDS_ACCEPTED "
             << knownDefinitionKindsAccepted << L'\n';
  ok = Expect(knownDefinitionKindsAccepted,
              L"all known DefinitionKind values accepted by codec") && ok;

  const Uuid128 referencedDocument = Uuid(70100);
  const Uuid128 referencedDefinition = Uuid(70101);
  const Uuid128 referencedStory = Uuid(70102);
  const Uuid128 referencedSection = Uuid(70103);
  const Uuid128 referencingParagraph = Uuid(70104);
  Bytes referencedUnknown = ManifestRecord(11);
  const std::array<Bytes, 10> referencedRecords{
      NodeRecord(1, NodeKind::Document, referencedDocument, nullptr, 0),
      EdgeRecord(2, EdgeKind::Contains, referencedDocument,
                 referencedDefinition, 0),
      EdgeRecord(3, EdgeKind::Contains, referencedDocument, referencedStory, 1),
      NodeRecord(4, NodeKind::Definition, referencedDefinition,
                 &referencedDocument, 0, {}, {}, nullptr, {}, {},
                 ObservationState::NotExposed, 0,
                 static_cast<DefinitionKind>(UINT16_MAX)),
      NodeRecord(5, NodeKind::Story, referencedStory, &referencedDocument, 0),
      EdgeRecord(6, EdgeKind::Contains, referencedStory, referencedSection),
      NodeRecord(7, NodeKind::Section, referencedSection, &referencedStory, 0),
      EdgeRecord(8, EdgeKind::Contains, referencedSection,
                 referencingParagraph),
      NodeRecord(9, NodeKind::Paragraph, referencingParagraph,
                 &referencedSection, 0),
      EdgeRecord(10, EdgeKind::StyleRef, referencingParagraph,
                 referencedDefinition)};
  for (const Bytes &record : referencedRecords)
    referencedUnknown.insert(
        referencedUnknown.end(), record.begin(), record.end());
  const bool referencedUnknownDefinitionKindRejected =
      CanonicalStreamError(referencedUnknown) == Error::BadScalar;
  std::wcout << L"CODEC_REFERENCED_UNKNOWN_DEFINITION_KIND_REJECTED "
             << referencedUnknownDefinitionKindRejected << L'\n';
  ok = Expect(referencedUnknownDefinitionKindRejected,
              L"referenced unknown DefinitionKind rejected by codec") && ok;

  const Uuid128 receiptDocument = ReceiptUuid(1);
  const Uuid128 oldOne = ReceiptUuid(2);
  const Uuid128 oldTwo = ReceiptUuid(3);
  const Uuid128 absentTarget = ReceiptUuid(4);
  const Uuid128 clientOne = ReceiptUuid(5);
  const Uuid128 clientTwo = ReceiptUuid(6);
  const auto receiptStream = [&](std::vector<Bytes> records) {
    Bytes bytes = ManifestRecord(records.size() + 2);
    const Bytes node = NodeRecord(
        1, NodeKind::Document, receiptDocument, nullptr, 0);
    bytes.insert(bytes.end(), node.begin(), node.end());
    for (const Bytes& receipt : records)
      bytes.insert(bytes.end(), receipt.begin(), receipt.end());
    return bytes;
  };
  const auto receiptError = [&](const Bytes& bytes) {
    MemoryReader receiptReader{&bytes};
    std::uint64_t receiptCount = 0;
    return ValidateRecordStream(
        {&receiptReader, ReadMemory, bytes.size(), StreamKind::Capture},
        &receiptCount);
  };
  const Bytes validReceipts = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::ExternalUnmatched),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Delete)});
  const Bytes wrongPhase = receiptStream({
      RemapRecord(2, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Delete),
      TombstoneRecord(3, oldOne, 1, TombstoneReason::ExternalUnmatched)});
  const Bytes duplicateReceipt = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::ExternalUnmatched),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Delete),
      RemapRecord(4, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Delete)});
  const Bytes missingPair = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::ExternalUnmatched)});
  const Bytes absentNewTarget = receiptStream({
      RemapRecord(2, oldOne, &absentTarget, RemapDisposition::New,
                  RemapReason::ClientInsert)});
  const Bytes retainedCrossId = receiptStream({
      RemapRecord(2, oldOne, &receiptDocument,
                  RemapDisposition::Retained,
                  RemapReason::IdentityRetained)});
  const Bytes impossibleNewSelf = receiptStream({
      RemapRecord(2, oldOne, &oldOne, RemapDisposition::New,
                  RemapReason::ClientInsert)});
  const Bytes wrongTombstoneOrder = receiptStream({
      TombstoneRecord(2, oldTwo, 1, TombstoneReason::ExternalUnmatched),
      TombstoneRecord(3, oldOne, 1, TombstoneReason::ExternalUnmatched),
      RemapRecord(4, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Delete),
      RemapRecord(5, oldTwo, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Delete)});
  const Bytes retainedPositive = receiptStream({RemapRecord(
      2, receiptDocument, &receiptDocument, RemapDisposition::Retained,
      RemapReason::IdentityRetained)});
  const Bytes clientInsertPositive = receiptStream({RemapRecord(
      2, clientOne, &receiptDocument, RemapDisposition::New,
      RemapReason::ClientInsert)});
  const Bytes externalFreshPositive = receiptStream({RemapRecord(
      2, receiptDocument, &receiptDocument, RemapDisposition::New,
      RemapReason::ExternalUnique)});
  const Bytes coalescePositive = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::Coalesce),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Coalesce)});
  const Bytes mergePositive = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::MergeAbsorbed),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Merge)});
  const Bytes ambiguousPositive = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::ExternalUnmatched),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Ambiguous,
                  RemapReason::Ambiguous)});
  const Bytes duplicateClaimedTarget = receiptStream({
      RemapRecord(2, clientOne, &receiptDocument, RemapDisposition::New,
                  RemapReason::ClientInsert),
      RemapRecord(3, clientTwo, &receiptDocument, RemapDisposition::New,
                  RemapReason::ClientInsert)});
  const Bytes invalidDispositionReason = receiptStream({RemapRecord(
      2, receiptDocument, &receiptDocument, RemapDisposition::Retained,
      RemapReason::Delete)});
  const Bytes invalidTombstoneReasonPair = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::ExternalUnmatched),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Coalesce)});
  const Bytes unsupportedReopenPair = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::ReopenNewLineage),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Delete)});
  const Uuid128 collisionTarget = ReceiptUuid(7);
  Bytes currentNodeUsedAsClient = ManifestRecord(5);
  for (const Bytes& record : std::array<Bytes, 4>{
           NodeRecord(1, NodeKind::Document, receiptDocument, nullptr, 0),
           EdgeRecord(2, EdgeKind::Contains, receiptDocument,
                      collisionTarget),
           NodeRecord(3, NodeKind::Definition, collisionTarget,
                      &receiptDocument, 0),
           RemapRecord(4, receiptDocument, &collisionTarget,
                       RemapDisposition::New, RemapReason::ClientInsert)})
    currentNodeUsedAsClient.insert(
        currentNodeUsedAsClient.end(), record.begin(), record.end());
  const auto priorDerivedCurrentSourceStream = [&](RemapReason reason) {
    const Uuid128 original = ReceiptUuid(8);
    const Uuid128 derived = ReceiptUuid(9);
    Bytes bytes = ManifestRecord(7);
    for (const Bytes& record : std::array<Bytes, 6>{
             NodeRecord(1, NodeKind::Document, receiptDocument, nullptr, 0),
             EdgeRecord(2, EdgeKind::Contains, receiptDocument, original, 0),
             EdgeRecord(3, EdgeKind::Contains, receiptDocument, derived, 1),
             NodeRecord(4, NodeKind::Story, original, &receiptDocument, 0),
             NodeRecord(5, NodeKind::Story, derived, &receiptDocument, 1),
             RemapRecord(6, original, &derived, RemapDisposition::New,
                         reason)})
      bytes.insert(bytes.end(), record.begin(), record.end());
    return bytes;
  };
  const Bytes cloneCurrentSource =
      priorDerivedCurrentSourceStream(RemapReason::Clone);
  const auto tableCellSplitGroupStream = [&](bool reverseNewTargets) {
    const Uuid128 original = ReceiptUuid(8);
    const Uuid128 firstDerived = ReceiptUuid(9);
    const Uuid128 secondDerived = ReceiptUuid(10);
    Bytes bytes = ManifestRecord(11);
    std::array<Bytes, 10> records{
        NodeRecord(1, NodeKind::Document, receiptDocument, nullptr, 0),
        EdgeRecord(2, EdgeKind::Contains, receiptDocument, original, 0),
        EdgeRecord(3, EdgeKind::Contains, receiptDocument, firstDerived, 1),
        EdgeRecord(4, EdgeKind::Contains, receiptDocument, secondDerived, 2),
        NodeRecord(5, NodeKind::Story, original, &receiptDocument, 0),
        NodeRecord(6, NodeKind::Story, firstDerived, &receiptDocument, 1),
        NodeRecord(7, NodeKind::Story, secondDerived, &receiptDocument, 2),
        RemapRecord(8, original, &original, RemapDisposition::Retained,
                    RemapReason::TableCellSplit),
        RemapRecord(9, original,
                    reverseNewTargets ? &secondDerived : &firstDerived,
                    RemapDisposition::New, RemapReason::TableCellSplit),
        RemapRecord(10, original,
                    reverseNewTargets ? &firstDerived : &secondDerived,
                    RemapDisposition::New, RemapReason::TableCellSplit)};
    for (const Bytes& record : records)
      bytes.insert(bytes.end(), record.begin(), record.end());
    return bytes;
  };
  const Bytes tableCellSplitCurrentSource =
      tableCellSplitGroupStream(false);
  const Bytes reversedTableCellSplitGroup =
      tableCellSplitGroupStream(true);
  const Bytes duplicateRetainedGroup = receiptStream({
      RemapRecord(2, receiptDocument, &receiptDocument,
                  RemapDisposition::Retained,
                  RemapReason::IdentityRetained),
      RemapRecord(3, receiptDocument, &receiptDocument,
                  RemapDisposition::Retained, RemapReason::Move)});
  const Bytes mixedTerminalGroup = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::ExternalUnmatched),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Tombstoned,
                  RemapReason::Delete),
      RemapRecord(4, oldOne, nullptr, RemapDisposition::Ambiguous,
                  RemapReason::Ambiguous)});
  const Bytes ambiguousTombstoneMismatch = receiptStream({
      TombstoneRecord(2, oldOne, 1, TombstoneReason::Delete),
      RemapRecord(3, oldOne, nullptr, RemapDisposition::Ambiguous,
                  RemapReason::Ambiguous)});
  const Bytes reverseMissingPair = receiptStream({RemapRecord(
      2, oldOne, nullptr, RemapDisposition::Tombstoned,
      RemapReason::Delete)});
  const bool cloneCurrentSourceAccepted =
      receiptError(cloneCurrentSource) == Error::None;
  const bool tableCellSplitCurrentSourceAccepted =
      receiptError(tableCellSplitCurrentSource) == Error::None;
  const bool priorDerivedCurrentSourcesAccepted =
      cloneCurrentSourceAccepted && tableCellSplitCurrentSourceAccepted;
  const bool duplicateFullKeyRejected =
      receiptError(duplicateReceipt) == Error::BadRecordOrder;
  const bool duplicateTargetRejected =
      receiptError(duplicateClaimedTarget) == Error::IllegalStreamState;
  const bool conflictingRetainedRejected =
      receiptError(duplicateRetainedGroup) == Error::IllegalStreamState;
  const bool terminalMixedGroupRejected =
      receiptError(mixedTerminalGroup) == Error::IllegalStreamState;
  const bool invalidGroupReasonRejected =
      receiptError(invalidDispositionReason) == Error::IllegalStreamState;
  const bool ambiguousTombstonePairingRejected =
      receiptError(ambiguousTombstoneMismatch) == Error::IllegalStreamState;
  const bool receiptGroupConflictsRejected =
      receiptError(reversedTableCellSplitGroup) == Error::BadRecordOrder &&
      duplicateFullKeyRejected && duplicateTargetRejected &&
      conflictingRetainedRejected && terminalMixedGroupRejected &&
      invalidGroupReasonRejected && ambiguousTombstonePairingRejected;
  const bool receiptPositiveMatrix =
      receiptError(validReceipts) == Error::None &&
      receiptError(retainedPositive) == Error::None &&
      receiptError(clientInsertPositive) == Error::None &&
      receiptError(externalFreshPositive) == Error::None &&
      receiptError(coalescePositive) == Error::None &&
      receiptError(mergePositive) == Error::None &&
      receiptError(ambiguousPositive) == Error::None;
  const bool receiptGrammar =
      receiptError(wrongPhase) == Error::BadRecordOrder &&
      receiptError(duplicateReceipt) == Error::BadRecordOrder &&
      receiptError(missingPair) == Error::IllegalStreamState &&
      receiptError(reverseMissingPair) == Error::IllegalStreamState &&
      receiptError(absentNewTarget) == Error::IllegalStreamState &&
      receiptError(retainedCrossId) == Error::IllegalStreamState &&
      receiptError(impossibleNewSelf) == Error::IllegalStreamState &&
      receiptError(wrongTombstoneOrder) == Error::BadRecordOrder &&
      receiptError(duplicateClaimedTarget) == Error::IllegalStreamState &&
      receiptError(invalidDispositionReason) == Error::IllegalStreamState &&
      receiptError(invalidTombstoneReasonPair) == Error::IllegalStreamState &&
      receiptError(unsupportedReopenPair) == Error::IllegalStreamState &&
      receiptError(currentNodeUsedAsClient) == Error::IllegalStreamState;
  std::wcout << L"CODEC_RECEIPT_CLONE_CURRENT_SOURCE_ACCEPTED "
             << cloneCurrentSourceAccepted << L'\n';
  std::wcout << L"CODEC_RECEIPT_TABLE_CELL_SPLIT_CURRENT_SOURCE_ACCEPTED "
             << tableCellSplitCurrentSourceAccepted
             << L" retained_self=1 new_distinct_targets=2" << L'\n';
  std::wcout << L"CODEC_RECEIPT_PRIOR_DERIVED_CURRENT_SOURCE_ACCEPTED "
             << priorDerivedCurrentSourcesAccepted << L'\n';
  std::wcout << L"CODEC_RECEIPT_DUPLICATE_FULL_KEY_REJECTED "
             << duplicateFullKeyRejected << L'\n';
  std::wcout << L"CODEC_RECEIPT_DUPLICATE_TARGET_REJECTED "
             << duplicateTargetRejected << L'\n';
  std::wcout << L"CODEC_RECEIPT_CONFLICTING_RETAINED_REJECTED "
             << conflictingRetainedRejected << L'\n';
  std::wcout << L"CODEC_RECEIPT_TERMINAL_MIXED_GROUP_REJECTED "
             << terminalMixedGroupRejected << L'\n';
  std::wcout << L"CODEC_RECEIPT_INVALID_GROUP_REASON_REJECTED "
             << invalidGroupReasonRejected << L'\n';
  std::wcout << L"CODEC_RECEIPT_AMBIGUOUS_TOMBSTONE_PAIRING_REJECTED "
             << ambiguousTombstonePairingRejected << L'\n';
  std::wcout << L"CODEC_RECEIPT_GROUP_CONFLICTS_REJECTED "
             << receiptGroupConflictsRejected << L'\n';
  std::wcout << L"CODEC_RECEIPT_POSITIVE_MATRIX_ACCEPTED "
             << receiptPositiveMatrix << L'\n';
  std::wcout << L"CODEC_RECEIPT_INVARIANT_MALFORMED_REJECTED "
             << receiptGrammar << L'\n';
  ok = Expect(
           priorDerivedCurrentSourcesAccepted,
           L"Clone and complete TableCellSplit group accept current sources") &&
       ok;
  ok = Expect(
           receiptGroupConflictsRejected,
           L"same-source receipt conflicts and full-key reorder reject") &&
       ok;
  ok = Expect(receiptPositiveMatrix,
              L"authoritative receipt producer matrix remains accepted") && ok;
  ok = Expect(receiptGrammar,
              L"authoritative codec rejects malformed receipt invariants") &&
       ok;
  return ok;
}

bool ReadBlobNode(void *context, const ContentId &contentId,
                  std::uint64_t offset, std::uint8_t *buffer,
                  std::uint32_t requested, std::uint32_t *actual) noexcept {
  auto *blob = static_cast<BlobNodeInput *>(context);
  if (blob == nullptr || blob->contentId.bytes != contentId.bytes ||
      offset < blob->physicalOffset ||
      offset - blob->physicalOffset > blob->content.size())
    return false;
  ++blob->callbackCount;
  const std::uint64_t relative = offset - blob->physicalOffset;
  *actual = static_cast<std::uint32_t>(
      (std::min<std::uint64_t>)(requested, blob->content.size() - relative));
  if (*actual != 0)
    std::memcpy(buffer, blob->content.data() + relative, *actual);
  return true;
}

enum class BrushCompletenessCase {
  Missing, NonWindowsNotApplicable, NotExposed, ReadFailed,
  WrongOwner, WrongKey, WrongProfile, Value,
};
bool BrushCaseCertified(
    const BrushCompletenessCase test,
    Error *error,
    const bool corruptDefinitionAggregate = false,
    const PropertyOrigin propertyOrigin = PropertyOrigin::Direct,
    const PropertyOrigin aggregateOrigin = PropertyOrigin::Direct) {
  const Uuid128 document=Uuid(2401),definition=Uuid(2402);
  const bool value=test==BrushCompletenessCase::Value;
  const bool coverage=test!=BrushCompletenessCase::Missing && !value;
  std::uint64_t next=4;
  std::vector<std::uint64_t> propertyIds;
  for (PropertyKeyId key=5000; key<=5014; ++key) propertyIds.push_back(next++);
  if (value) propertyIds.push_back(next++);
  const std::uint64_t coverageId=coverage?next++:0;
  Bytes bytes=ManifestRecord(next,ProfileBit(ProfileId::Structure) |
                                  ProfileBit(ProfileId::EditableText) |
                                  ProfileBit(ProfileId::EditableObjects));
  const auto add=[&](const Bytes& record) {
    bytes.insert(bytes.end(),record.begin(),record.end());
  };
  std::vector<SemanticPropertyInput> semanticProperties;
  for (PropertyKeyId key=5000; key<=5015; ++key) {
    if (key==5015 && !value) break;
    const PropertyRule* const rule=FindPropertyRule(key);
    Bytes propertyValue;
    if (rule->scalar==ScalarTag::Enum) propertyValue=Enum(1,nullptr,0);
    else if (rule->scalar==ScalarTag::BGR)
      propertyValue=Uint32(UINT32_C(0x00123456));
    else if (rule->scalar==ScalarTag::Sint64) propertyValue=Sint64(7);
    else if (rule->scalar==ScalarTag::Uint8) propertyValue=Uint8(1);
    semanticProperties.push_back({key,aggregateOrigin,propertyValue});
  }
  Sha256 definitionAggregate=
      DefinitionPropertyAggregate(semanticProperties);
  if (corruptDefinitionAggregate) definitionAggregate.bytes[0] ^= 0xff;
  add(NodeRecord(1,NodeKind::Document,document,nullptr,0));
  add(EdgeRecord(2,EdgeKind::Contains,document,definition));
  add(NodeRecord(3,NodeKind::Definition,definition,&document,0,{},propertyIds,
                 nullptr,{}, {},ObservationState::NotExposed,0,
                 DefinitionKind::BorderFill,&definitionAggregate));
  std::size_t propertyIndex=0;
  for (PropertyKeyId key=5000; key<=5015; ++key) {
    if (key==5015 && !value) break;
    const PropertyRule* const rule=FindPropertyRule(key);
    Bytes propertyValue;
    if (rule->scalar==ScalarTag::Enum) propertyValue=Enum(1,nullptr,0);
    else if (rule->scalar==ScalarTag::BGR) propertyValue=Uint32(UINT32_C(0x00123456));
    else if (rule->scalar==ScalarTag::Sint64) propertyValue=Sint64(7);
    else if (rule->scalar==ScalarTag::Uint8) propertyValue=Uint8(1);
    add(PropertyRecord(propertyIds[propertyIndex++],definition,102,key,
                       rule->scalar,propertyValue,propertyOrigin));
  }
  BlobNodeInput detail{Uuid(2403),0,Bytes{L'n',0}};
  if (coverage) {
    CoverageState state=CoverageState::NotApplicable;
    if (test==BrushCompletenessCase::NotExposed) state=CoverageState::NotExposed;
    else if (test==BrushCompletenessCase::ReadFailed) state=CoverageState::ReadFailed;
    const Uuid128& owner=test==BrushCompletenessCase::WrongOwner?document:definition;
    const PropertyKeyId key=test==BrushCompletenessCase::WrongKey?5000:5015;
    const ProfileId profile=test==BrushCompletenessCase::WrongProfile
                                ?ProfileId::Structure:ProfileId::EditableObjects;
    add(CoverageRecord(coverageId,owner,detail,profile,state,key,
                       RecordKind::Property,102));
  }
  MemoryReader reader{&bytes};
  CanonicalizationResult result{};
  *error=CanonicalizeRecordStream(
      {{&reader,ReadMemory,bytes.size(),StreamKind::Capture},
       coverage?&detail:nullptr,coverage?ReadBlobNode:nullptr,{}},&result);
  return *error==Error::None && result.semanticCertified;
}
bool WindowsBrushCompletenessSmoke() {
  Error error=Error::None;
  bool ok=Expect(BrushCaseCertified(BrushCompletenessCase::NonWindowsNotApplicable,&error) &&
                     error==Error::None,
                 L"non-Windows brush NotApplicable coverage completes") &&
          Expect(BrushCaseCertified(BrushCompletenessCase::Value,&error) &&
                     error==Error::None,
                 L"Windows brush value completes");
  for (const BrushCompletenessCase test : {
           BrushCompletenessCase::Missing,BrushCompletenessCase::NotExposed,
           BrushCompletenessCase::ReadFailed,BrushCompletenessCase::WrongOwner,
           BrushCompletenessCase::WrongKey,BrushCompletenessCase::WrongProfile}) {
    const bool certified=BrushCaseCertified(test,&error);
    const bool malformedCoordinate =
        test == BrushCompletenessCase::WrongOwner ||
        test == BrushCompletenessCase::WrongProfile;
    ok=Expect(!certified &&
                  (malformedCoordinate
                       ? error == Error::BadRecordOrder
                       : error == Error::None),
              malformedCoordinate
                  ? L"malformed Windows brush coordinate rejected"
                  : L"invalid Windows brush evidence remains incomplete") &&
       ok;
  }
  return ok;
}

bool DefinitionAggregateMismatchSmoke() {
  Error error = Error::None;
  return !BrushCaseCertified(
             BrushCompletenessCase::Value, &error, true) &&
      error == Error::BadScalar;
}

bool DefinitionUnknownOriginSmoke() {
  Error error = Error::None;
  const bool accepted = BrushCaseCertified(
      BrushCompletenessCase::Value, &error, false,
      PropertyOrigin::Unknown, PropertyOrigin::Unknown);
  const bool mismatchRejected = !BrushCaseCertified(
      BrushCompletenessCase::Value, &error, false,
      PropertyOrigin::Unknown, PropertyOrigin::Direct) &&
      error == Error::BadScalar;
  return accepted && mismatchRejected;
}

CanonicalizationResult BlobGraph(std::uint32_t seed, BlobNodeInput *blob,
                                 Error *error) {
  const Uuid128 document = Uuid(seed + 1), story = Uuid(seed + 2),
                section = Uuid(seed + 3), paragraph = Uuid(seed + 4),
                run = Uuid(seed + 5);
  Bytes bytes = ManifestRecord(11, ProfileBit(ProfileId::Structure) |
                                       ProfileBit(ProfileId::EditableText));
  const auto add = [&](const Bytes &record) {
    bytes.insert(bytes.end(), record.begin(), record.end());
  };
  add(NodeRecord(1, NodeKind::Document, document, nullptr, 0));
  add(EdgeRecord(2, EdgeKind::Contains, document, story));
  add(NodeRecord(3, NodeKind::Story, story, &document, 0));
  add(EdgeRecord(4, EdgeKind::Contains, story, section));
  add(NodeRecord(5, NodeKind::Section, section, &story, 0));
  add(EdgeRecord(6, EdgeKind::Contains, section, paragraph));
  add(NodeRecord(7, NodeKind::Paragraph, paragraph, &section, 0));
  add(EdgeRecord(8, EdgeKind::Contains, paragraph, run));
  add(NodeRecord(9, NodeKind::CharacterRun, run, &paragraph, 0, {}, {}, blob));
  add(CoverageRecord(10, run, *blob, ProfileId::EditableText,
                     CoverageState::NotExposed, 0, RecordKind::Node, 102));
  MemoryReader reader{&bytes};
  CanonicalizationResult result{};
  *error = CanonicalizeRecordStream(
      {{&reader, ReadMemory, bytes.size(), StreamKind::Capture},
       blob,
       ReadBlobNode,
       {}},
      &result);
  return result;
}
CanonicalizationResult BinaryGraph(std::uint32_t seed, BlobNodeInput *asset,
                                   const Bytes &sourceName,
                                   const Bytes *chunkOverride, Error *error) {
  const Uuid128 document = Uuid(seed + 1), binary = Uuid(seed + 2);
  Bytes bytes = ManifestRecord(5, ProfileBit(ProfileId::Structure) |
                                      ProfileBit(ProfileId::BinaryContent));
  const auto add = [&](const Bytes &record) {
    bytes.insert(bytes.end(), record.begin(), record.end());
  };
  add(NodeRecord(1, NodeKind::Document, document, nullptr, 0));
  add(EdgeRecord(2, EdgeKind::Contains, document, binary));
  add(NodeRecord(3, NodeKind::BinaryData, binary, &document, 0, {}, {}, asset,
                 sourceName));
  add(AssetChunkRecord(
      4, *asset, chunkOverride == nullptr ? asset->content : *chunkOverride));
  MemoryReader reader{&bytes};
  CanonicalizationResult result{};
  *error = CanonicalizeRecordStream(
      {{&reader, ReadMemory, bytes.size(), StreamKind::Capture},
       nullptr,
       nullptr,
       {}},
      &result);
  return result;
}
CanonicalizationResult
SmallGraph(std::uint32_t seed, bool propertyValue, PropertyOrigin origin,
           bool layoutValue, bool diagnostic, Error *error,
           bool duplicateProperty = false,
           BlobNodeInput *diagnosticDetail = nullptr,
           BlobNodeInput *coverageDetail = nullptr,
           const Bytes &captureUtf16 = {}, const Bytes &unavailableDetail = {},
           ObservationState unavailableState = ObservationState::NotExposed,
           int propertyOrderMode = 0,
           const Bytes &layoutEnvironment = {},
           bool requestLayout = false,
           ObservationState layoutState = ObservationState::Value,
           std::uint8_t omittedLayoutFacts = 0,
           std::uint64_t layoutDelta = 0,
           ProfileId coverageProfile = ProfileId::Structure,
           CoverageState coverageState = CoverageState::Complete) {
  const Uuid128 document = Uuid(seed + 1), story = Uuid(seed + 2),
                section = Uuid(seed + 3), paragraph = Uuid(seed + 4),
                control = Uuid(seed + 5);
  std::uint64_t next = 8;
  const auto hasLayoutFact = [&](const std::uint8_t bit) {
    return layoutValue && (omittedLayoutFacts & bit) == 0;
  };
  const std::uint64_t layoutId = hasLayoutFact(0x01) ? next++ : 0;
  const std::uint64_t layoutEndId = hasLayoutFact(0x02) ? next++ : 0;
  const std::uint64_t semanticId = next++;
  const std::uint64_t orderedPropertyId = propertyOrderMode != 0 ? next++ : 0;
  const std::uint64_t duplicateId = duplicateProperty ? next++ : 0;
  const std::uint64_t diagnosticId = diagnostic ? next++ : 0;
  const std::uint64_t coverageId = coverageDetail != nullptr ? next++ : 0;
  const std::uint64_t controlContainsId = next++;
  const std::uint64_t controlId = next++;
  const std::uint64_t controlLayoutStartId =
      hasLayoutFact(0x01) ? next++ : 0;
  const std::uint64_t controlLayoutEndId =
      hasLayoutFact(0x02) ? next++ : 0;
  const std::uint64_t controlLayoutWidthId =
      hasLayoutFact(0x04) ? next++ : 0;
  const std::uint64_t controlLayoutHeightId =
      hasLayoutFact(0x08) ? next++ : 0;
  const std::uint64_t controlAnchorsId = next++;
  Bytes bytes = ManifestRecord(
      next, ProfileBit(ProfileId::Structure) |
                ((layoutValue || requestLayout)
                     ? ProfileBit(ProfileId::Layout)
                     : 0));
  const auto add = [&](const Bytes &record) {
    bytes.insert(bytes.end(), record.begin(), record.end());
  };
  add(NodeRecord(1, NodeKind::Document, document, nullptr, 0));
  add(EdgeRecord(2, EdgeKind::Contains, document, story));
  add(NodeRecord(3, NodeKind::Story, story, &document, 0));
  add(EdgeRecord(4, EdgeKind::Contains, story, section));
  add(NodeRecord(5, NodeKind::Section, section, &story, 0));
  add(EdgeRecord(6, EdgeKind::Contains, section, paragraph));
  add(NodeRecord(7, NodeKind::Paragraph, paragraph, &section, 0,
                 layoutValue ? NonzeroIds({layoutId, layoutEndId})
                             : std::vector<std::uint64_t>{},
                 duplicateProperty
                     ? std::vector<std::uint64_t>{semanticId, duplicateId}
                 : propertyOrderMode != 0
                     ? std::vector<std::uint64_t>{semanticId, orderedPropertyId}
                     : std::vector<std::uint64_t>{semanticId}));
  if (layoutId != 0)
    add(PropertyRecord(layoutId, paragraph, 10, 12000, ScalarTag::Uint64,
                       Uint64(3 + layoutDelta), PropertyOrigin::Generated,
                       layoutState));
  if (layoutEndId != 0)
    add(PropertyRecord(layoutEndId, paragraph, 10, 12001, ScalarTag::Uint64,
                       Uint64(3 + layoutDelta), PropertyOrigin::Generated,
                       layoutState));
  add(PropertyRecord(semanticId, paragraph, 103,
                     propertyOrderMode == 2 ? 2009 : 2008, ScalarTag::Bool,
                     Bool(propertyValue), origin));
  if (propertyOrderMode != 0)
    add(PropertyRecord(orderedPropertyId, paragraph, 103,
                       propertyOrderMode == 2 ? 2008 : 2009, ScalarTag::Bool,
                       Bool(propertyValue), origin));
  if (duplicateProperty)
    add(PropertyRecord(duplicateId, paragraph, 103, 2008, ScalarTag::Bool,
                       Bool(propertyValue), origin));
  if (coverageDetail != nullptr)
    add(CoverageRecord(coverageId, paragraph, *coverageDetail,
                       coverageProfile, coverageState));
  if (diagnostic)
    add(DiagnosticRecord(diagnosticId, paragraph, E_FAIL, diagnosticDetail));
  add(EdgeRecord(controlContainsId, EdgeKind::Contains, paragraph, control));
  add(NodeRecord(controlId, NodeKind::GenericControl, control, &paragraph, 0,
                 layoutValue
                     ? NonzeroIds({controlLayoutStartId, controlLayoutEndId,
                                   controlLayoutWidthId,
                                   controlLayoutHeightId})
                     : std::vector<std::uint64_t>{},
                 {}, nullptr, captureUtf16, unavailableDetail,
                 unavailableState));
  if (controlLayoutStartId != 0)
    add(PropertyRecord(controlLayoutStartId, control, 10, 12000,
                       ScalarTag::Uint64, Uint64(3 + layoutDelta),
                       PropertyOrigin::Generated, layoutState));
  if (controlLayoutEndId != 0)
    add(PropertyRecord(controlLayoutEndId, control, 10, 12001,
                       ScalarTag::Uint64, Uint64(3 + layoutDelta),
                       PropertyOrigin::Generated, layoutState));
  if (controlLayoutWidthId != 0)
    add(PropertyRecord(controlLayoutWidthId, control, 10, 12002,
                       ScalarTag::HWPUNIT64,
                       Sint64(static_cast<std::int64_t>(10 + layoutDelta)),
                       PropertyOrigin::Generated, layoutState));
  if (controlLayoutHeightId != 0)
    add(PropertyRecord(controlLayoutHeightId, control, 10, 12003,
                       ScalarTag::HWPUNIT64,
                       Sint64(static_cast<std::int64_t>(20 + layoutDelta)),
                       PropertyOrigin::Generated, layoutState));
  add(EdgeRecord(
      controlAnchorsId, EdgeKind::Anchors, control, paragraph));
  MemoryReader reader{&bytes};
  CanonicalizationResult result{};
  *error = CanonicalizeRecordStream(
      {{&reader, ReadMemory, bytes.size(), StreamKind::Capture},
       diagnosticDetail != nullptr ? diagnosticDetail : coverageDetail,
       diagnosticDetail == nullptr && coverageDetail == nullptr ? nullptr
                                                                : ReadBlobNode,
       View(layoutEnvironment)},
      &result);
  return result;
}

bool CertificationDomainSeparationSmoke() {
  Error error = Error::None;
  const Bytes environment = LayoutEnvironment(96);
  const Sha256 zero{};
  const CanonicalizationResult unavailable = SmallGraph(
      6100, true, PropertyOrigin::Direct, false, false, &error,
      false, nullptr, nullptr, {}, {}, ObservationState::NotExposed, 0,
      environment, true);
  bool ok = Expect(error == Error::None && unavailable.semanticCertified &&
                       !unavailable.layoutPresent &&
                       Equal(unavailable.layoutRoot, zero),
                   L"layout unavailable preserves semantic certification") ;

  const CanonicalizationResult missingEnvironment = SmallGraph(
      6200, true, PropertyOrigin::Direct, true, false, &error);
  ok = Expect(error == Error::None && missingEnvironment.semanticCertified &&
                  !missingEnvironment.layoutPresent &&
                  Equal(missingEnvironment.layoutRoot, zero),
              L"missing layout environment suppresses only layout") && ok;

  const CanonicalizationResult complete = SmallGraph(
      6300, true, PropertyOrigin::Direct, true, false, &error,
      false, nullptr, nullptr, {}, {}, ObservationState::NotExposed, 0,
      environment);
  ok = Expect(error == Error::None && complete.semanticCertified &&
                  complete.layoutPresent && !Equal(complete.layoutRoot, zero),
              L"complete layout and environment certify both domains") && ok;

  for (const std::uint8_t missing : {std::uint8_t{0x01},
                                     std::uint8_t{0x02},
                                     std::uint8_t{0x04},
                                     std::uint8_t{0x08}}) {
    const CanonicalizationResult coordinate = SmallGraph(
        6400 + missing, true, PropertyOrigin::Direct, true, false, &error,
        false, nullptr, nullptr, {}, {}, ObservationState::NotExposed, 0,
        environment, false, ObservationState::Value, missing);
    ok = Expect(error == Error::None && coordinate.semanticCertified &&
                    !coordinate.layoutPresent &&
                    Equal(coordinate.layoutRoot, zero),
                L"each applicable layout coordinate is required") && ok;
  }

  const CanonicalizationResult readFailed = SmallGraph(
      6500, true, PropertyOrigin::Direct, true, false, &error,
      false, nullptr, nullptr, {}, {}, ObservationState::NotExposed, 0,
      environment, false, ObservationState::ReadFailed);
  ok = Expect(error == Error::None && readFailed.semanticCertified &&
                  !readFailed.layoutPresent,
              L"layout-only ReadFailed does not lower semantic certification") &&
       ok;

  BlobNodeInput coverageDetail{Uuid(6599), 0, Bytes{L'x', 0}};
  const CanonicalizationResult layoutCoverageFailure = SmallGraph(
      6600, true, PropertyOrigin::Direct, true, false, &error,
      false, nullptr, &coverageDetail, {}, {}, ObservationState::NotExposed,
      0, environment, false, ObservationState::Value, 0, 0,
      ProfileId::Layout, CoverageState::NotExposed);
  ok = Expect(error == Error::None &&
                  layoutCoverageFailure.semanticCertified &&
                  !layoutCoverageFailure.layoutPresent,
              L"Layout coverage failure affects only layout certification") &&
       ok;

  coverageDetail.callbackCount = 0;
  const CanonicalizationResult structureCoverageFailure = SmallGraph(
      6700, true, PropertyOrigin::Direct, true, false, &error,
      false, nullptr, &coverageDetail, {}, {}, ObservationState::NotExposed,
      0, environment, false, ObservationState::Value, 0, 0,
      ProfileId::Structure, CoverageState::ReadFailed);
  ok = Expect(error == Error::None &&
                  !structureCoverageFailure.semanticCertified &&
                  !structureCoverageFailure.layoutPresent,
              L"Structure coverage failure lowers semantic certification") &&
       ok;

  const CanonicalizationResult changedFacts = SmallGraph(
      6300, true, PropertyOrigin::Direct, true, false, &error,
      false, nullptr, nullptr, {}, {}, ObservationState::NotExposed, 0,
      environment, false, ObservationState::Value, 0, 7);
  ok = Expect(error == Error::None && changedFacts.semanticCertified &&
                  changedFacts.layoutPresent &&
                  Equal(complete.observedSemanticRoot,
                        changedFacts.observedSemanticRoot) &&
                  !Equal(complete.layoutRoot, changedFacts.layoutRoot) &&
                  !Equal(complete.captureRoot, changedFacts.captureRoot),
              L"layout facts change only layout and capture roots") && ok;
  std::wcout << L"CODEC_CERTIFICATION_DOMAINS_SEPARATED " << ok << L'\n';
  return ok;
}

CanonicalizationResult DefinitionLayoutGraph(
    const ProfileId coverageProfile, const PropertyKeyId coverageKey,
    const CoverageState coverageState, Error *error,
    const ObservationState nativeState = ObservationState::Value,
    const ObservationState pagePropertyState = ObservationState::Value,
    const ObservationState columnPropertyState = ObservationState::Value) {
  const Uuid128 document = Uuid(6801), pageDefinition = Uuid(6802),
                columnDefinition = Uuid(6803);
  BlobNodeInput detail{Uuid(6804), 0, Bytes{L'd', 0}};
  std::uint64_t next = 1;
  std::vector<Bytes> records;
  const auto add = [&](const Bytes &record) {
    records.push_back(record);
    ++next;
  };
  add(NodeRecord(next, NodeKind::Document, document, nullptr, 0));
  add(EdgeRecord(next, EdgeKind::Contains, document, pageDefinition, 0));
  add(EdgeRecord(next, EdgeKind::Contains, document, columnDefinition, 1));

  const auto propertyValue = [](const PropertyRule &rule,
                                const PropertyKeyId key) {
    if (rule.valueShape == PropertyValueShape::Uint16Array)
      return ArrayValue(ScalarTag::Uint16, 0, {Uint16(10), Uint16(20)});
    if (rule.scalar == ScalarTag::Bool)
      return Bool(true);
    if (rule.scalar == ScalarTag::Enum)
      return Enum(1, nullptr, 0);
    if (rule.scalar == ScalarTag::Uint64)
      return Uint64(2);
    return Sint64(static_cast<std::int64_t>(key));
  };
  const auto addDefinition = [&](const Uuid128 &definition,
                                 const DefinitionKind kind,
                                 const std::uint64_t ordinal,
                                 const std::vector<PropertyKeyId> &keys,
                                 const PropertyKeyId unavailableKey,
                                 const ObservationState propertyState) {
    const std::uint64_t nodeId = next;
    std::vector<std::uint64_t> propertyIds;
    std::vector<SemanticPropertyInput> properties;
    for (const PropertyKeyId key : keys) {
      const PropertyRule *rule = FindPropertyRule(key);
      propertyIds.push_back(nodeId + 1 + propertyIds.size());
      if (key != unavailableKey || propertyState == ObservationState::Value)
        properties.push_back(
            {key, PropertyOrigin::Direct, propertyValue(*rule, key)});
    }
    const Sha256 aggregate = DefinitionPropertyAggregate(properties);
    add(NodeRecord(nodeId, NodeKind::Definition, definition, &document,
                   ordinal, {}, propertyIds, nullptr, {}, {},
                   ObservationState::NotExposed, 1, kind, &aggregate,
                   nativeState));
    for (const PropertyKeyId key : keys) {
      const PropertyRule *rule = FindPropertyRule(key);
      const ObservationState state =
          key == unavailableKey ? propertyState : ObservationState::Value;
      add(PropertyRecord(next, definition, 102, key, rule->scalar,
                         propertyValue(*rule, key), PropertyOrigin::Direct,
                         state));
    }
    if (std::find(keys.begin(), keys.end(), coverageKey) != keys.end())
      add(CoverageRecord(next, definition, detail, coverageProfile,
                         coverageState, coverageKey, RecordKind::Property,
                         102));
  };
  std::vector<PropertyKeyId> pageKeys;
  for (PropertyKeyId key = 7000; key <= 7010; ++key)
    pageKeys.push_back(key);
  const std::vector<PropertyKeyId> columnKeys{7100, 7101, 7102, 7104};
  addDefinition(pageDefinition, DefinitionKind::PageDef, 0, pageKeys, 7000,
                pagePropertyState);
  addDefinition(columnDefinition, DefinitionKind::ColumnDef, 1, columnKeys,
                7100, columnPropertyState);

  Bytes bytes = ManifestRecord(records.size() + 1,
                               ProfileBit(ProfileId::Structure) |
                                   ProfileBit(ProfileId::Layout));
  for (const Bytes &record : records)
    bytes.insert(bytes.end(), record.begin(), record.end());
  MemoryReader reader{&bytes};
  CanonicalizationResult result{};
  *error = CanonicalizeRecordStream(
      {{&reader, ReadMemory, bytes.size(), StreamKind::Capture},
       coverageKey == 0 ? nullptr : &detail,
       coverageKey == 0 ? nullptr : ReadBlobNode,
       View(LayoutEnvironment(96))},
      &result);
  return result;
}

bool DefinitionLayoutCertificationSmoke() {
  Error error = Error::None;
  const CanonicalizationResult complete = DefinitionLayoutGraph(
      ProfileId::Layout, 0, CoverageState::Complete, &error);
  bool ok = Expect(error == Error::None && complete.semanticCertified &&
                       complete.layoutPresent,
                   L"PageDef and ColumnDef layout coordinates complete");
  const CanonicalizationResult unavailableNative = DefinitionLayoutGraph(
      ProfileId::Layout, 0, CoverageState::Complete, &error,
      ObservationState::ReadFailed);
  ok = Expect(error == Error::None && unavailableNative.semanticCertified &&
                  !unavailableNative.layoutPresent,
              L"PageDef and ColumnDef owner fields are Layout coordinates") &&
       ok;
  for (const PropertyKeyId key : {PropertyKeyId{7000},
                                  PropertyKeyId{7100}}) {
    const CanonicalizationResult layoutFailure = DefinitionLayoutGraph(
        ProfileId::Layout, key, CoverageState::ReadFailed, &error);
    ok = Expect(error == Error::None && layoutFailure.semanticCertified &&
                    !layoutFailure.layoutPresent,
                L"dual-profile Layout coordinate failure is layout-only") &&
         ok;
    const CanonicalizationResult structureFailure = DefinitionLayoutGraph(
        ProfileId::Structure, key, CoverageState::NotExposed, &error);
    ok = Expect(error == Error::None &&
                    !structureFailure.semanticCertified &&
                    !structureFailure.layoutPresent,
                L"dual-profile Structure coordinate failure is semantic") &&
         ok;
  }
  const bool registryGap = FindPropertyRule(7103) == nullptr &&
                           FindPropertyRule(7104) != nullptr;
  const CanonicalizationResult unavailableProperties = DefinitionLayoutGraph(
      ProfileId::Layout, 0, CoverageState::Complete, &error,
      ObservationState::Value, ObservationState::NotExposed,
      ObservationState::ReadFailed);
  const bool propertySeam =
      error == Error::None && registryGap &&
      unavailableProperties.semanticCertified &&
      !unavailableProperties.layoutPresent;
  ok = Expect(propertySeam,
              L"PageDef and ColumnDef unavailable PropertyRecords are layout-only") &&
       ok;
  std::wcout << L"CODEC_DEFINITION_PROPERTY_102_KEYS_7103_7104 "
             << propertySeam << L'\n';
  std::wcout << L"CODEC_DEFINITION_LAYOUT_COORDINATES " << ok << L'\n';
  return ok;
}

bool CoverageCoordinateSmoke() {
  BlobNodeInput detail{Uuid(2899), 0, Bytes{}};
  const Uuid128 document = Uuid(2898);
  const auto probe = [&](bool ownerPresent, std::uint8_t profile,
                         RecordKind ownerKind, FieldTag ownerField,
                         PropertyKeyId propertyKey) {
    Bytes bytes = ManifestRecord(3);
    const Bytes node = NodeRecord(
        1, NodeKind::Document, document, nullptr, 0);
    bytes.insert(bytes.end(), node.begin(), node.end());
    const Bytes coverage = CoverageRecord(
        2, document, detail, static_cast<ProfileId>(profile),
        CoverageState::Complete, propertyKey, ownerKind, ownerField,
        ownerPresent);
    bytes.insert(bytes.end(), coverage.begin(), coverage.end());
    MemoryReader reader{&bytes};
    std::uint64_t count = 0;
    return ValidateRecordStream(
        {&reader, ReadMemory, bytes.size(), StreamKind::Capture}, &count);
  };
  struct Form final {
    bool owner;
    std::uint8_t profile;
    RecordKind kind;
    FieldTag field;
    PropertyKeyId property;
  };
  constexpr std::array legal{
      Form{false, kNoProfile, RecordKind::Manifest, 0, 0},
      Form{false, 0, RecordKind::Coverage, 0, 0},
      Form{true, kNoProfile, RecordKind::Node, 0, 0},
      Form{true, kNoProfile, RecordKind::Node, 100, 0},
      Form{true, 0, RecordKind::Node, 100, 0},
      Form{true, 0, RecordKind::Property, 100, 13000},
  };
  bool ok = true;
  for (const Form& form : legal) {
    const Error result = probe(form.owner, form.profile, form.kind,
                               form.field, form.property);
    if (result != Error::None)
      std::wcerr << L"coverage legal failure owner=" << form.owner
                 << L" profile=" << static_cast<unsigned>(form.profile)
                 << L" kind=" << static_cast<unsigned>(form.kind)
                 << L" field=" << form.field << L" property="
                 << form.property << L" error="
                 << static_cast<unsigned>(result) << L'\n';
    ok = result == Error::None && ok;
  }
  constexpr std::array profiles{kNoProfile, std::uint8_t{0},
                                std::uint8_t{5}};
  constexpr std::array kinds{RecordKind::Manifest, RecordKind::Node,
                             RecordKind::Coverage, RecordKind::Property};
  constexpr std::array fields{FieldTag{0}, FieldTag{100}};
  constexpr std::array properties{PropertyKeyId{0}, PropertyKeyId{13000}};
  size_t rejected = 0;
  for (const bool owner : {false, true})
    for (const std::uint8_t profile : profiles)
      for (const RecordKind kind : kinds)
        for (const FieldTag field : fields)
          for (const PropertyKeyId property : properties) {
            const bool isLegal = std::any_of(
                legal.begin(), legal.end(), [&](const Form& form) {
                  return form.owner == owner && form.profile == profile &&
                         form.kind == kind && form.field == field &&
                         form.property == property;
                });
            if (!isLegal &&
                probe(owner, profile, kind, field, property) ==
                    Error::IllegalStreamState)
              ++rejected;
            else if (!isLegal) {
              std::wcerr << L"coverage illegal accepted owner=" << owner
                         << L" profile=" << static_cast<unsigned>(profile)
                         << L" kind=" << static_cast<unsigned>(kind)
                         << L" field=" << field << L" property="
                         << property << L'\n';
              ok = false;
            }
          }
  std::wcout << L"CODEC_COVERAGE_COORDINATES_LEGAL " << ok << L'\n'
             << L"CODEC_COVERAGE_COORDINATES_ILLEGAL_REJECTED "
             << rejected << L'\n';
  return ok && rejected == 90;
}

bool OwnerBlockGrammarSmoke() {
  const Uuid128 document = Uuid(2904), other = Uuid(2905);
  BlobNodeInput detail{Uuid(2906), 0, Bytes{}};
  const auto validate = [](const Bytes& bytes) {
    MemoryReader reader{&bytes};
    std::uint64_t count = 0;
    return ValidateRecordStream(
        {&reader, ReadMemory, bytes.size(), StreamKind::Capture}, &count);
  };
  const auto stream = [&](std::uint64_t count,
                          const std::vector<Bytes>& records) {
    Bytes bytes = ManifestRecord(count);
    for (const Bytes& record : records)
      bytes.insert(bytes.end(), record.begin(), record.end());
    return bytes;
  };
  const Bytes node = NodeRecord(
      1, NodeKind::Document, document, nullptr, 0, {}, {3});
  const std::array<std::uint16_t, 1> x{{L'x'}};
  const Bytes xValue = Utf16(x.data(), x.size());
  const Bytes crossProperty = PropertyRecord(
      2, other, 100, 13000, ScalarTag::UTF16, xValue,
      PropertyOrigin::Direct);
  const Bytes crossCoverage = CoverageRecord(
      2, other, detail, ProfileId::Structure, CoverageState::Complete,
      0, RecordKind::Node, 100);
  const Bytes crossDiagnostic = DiagnosticRecord(2, other, E_FAIL, &detail);
  const Bytes coverageFirst = CoverageRecord(
      2, document, detail, ProfileId::Structure,
      CoverageState::Complete, 0, RecordKind::Node, 100);
  const Bytes propertyAfter = PropertyRecord(
      3, document, 100, 13000, ScalarTag::UTF16, xValue,
      PropertyOrigin::Direct);
  const Bytes highCoverage = CoverageRecord(
      2, document, detail, ProfileId::Structure,
      CoverageState::NotExposed, 0, RecordKind::Node, 100);
  const Bytes lowCoverage = CoverageRecord(
      3, document, detail, ProfileId::Structure,
      CoverageState::Complete, 0, RecordKind::Node, 100);
  const bool ok =
      validate(stream(3, {node, crossProperty})) == Error::BadRecordOrder &&
      validate(stream(3, {node, crossCoverage})) == Error::BadRecordOrder &&
      validate(stream(3, {node, crossDiagnostic})) == Error::BadRecordOrder &&
      validate(stream(4, {node, coverageFirst, propertyAfter})) ==
          Error::BadRecordOrder &&
      validate(stream(4, {node, highCoverage, lowCoverage})) ==
          Error::BadRecordOrder;
  std::wcout << L"CODEC_OWNER_BLOCK_MALFORMED_REJECTED " << ok << L'\n';
  return ok;
}

bool RootSmoke() {
  bool ok = CoverageCoordinateSmoke() && OwnerBlockGrammarSmoke();
  Error error = Error::None;
  const Uuid128 orderedDocument = Uuid(2901), orderedStory = Uuid(2902);
  Bytes parentBlockFirst = ManifestRecord(4);
  const auto addParentBlock = [&](const Bytes &record) {
    parentBlockFirst.insert(parentBlockFirst.end(), record.begin(),
                            record.end());
  };
  addParentBlock(
      NodeRecord(1, NodeKind::Document, orderedDocument, nullptr, 0));
  addParentBlock(
      EdgeRecord(2, EdgeKind::Contains, orderedDocument, orderedStory));
  addParentBlock(
      NodeRecord(3, NodeKind::Story, orderedStory, &orderedDocument, 0));
  MemoryReader parentBlockReader{&parentBlockFirst};
  CanonicalizationResult parentBlockResult{};
  ok = Expect(CanonicalizeRecordStream(
                  {{&parentBlockReader, ReadMemory, parentBlockFirst.size(),
                    StreamKind::Capture},
                   nullptr,
                   nullptr,
                   {}},
                  &parentBlockResult) == Error::None &&
                  parentBlockResult.rootsPresent,
              L"parent NodeBlock Contains edge precedes child NodeBlock") &&
       ok;
  const auto authorityProbe = [](std::uint64_t firstSibling,
                                 std::uint64_t secondSibling, int containsMode,
                                 bool descendingEdges) {
    const Uuid128 document = Uuid(3001), story = Uuid(3002),
                  section = Uuid(3003), paragraph = Uuid(3004),
                  first = Uuid(3005), second = Uuid(3006);
    const std::uint64_t total = containsMode == 1   ? 13
                                : containsMode == 2 ? 15
                                                    : 14;
    Bytes bytes = ManifestRecord(total);
    const auto add = [&](const Bytes &record) {
      bytes.insert(bytes.end(), record.begin(), record.end());
    };
    add(NodeRecord(1, NodeKind::Document, document, nullptr, 0));
    add(EdgeRecord(2, EdgeKind::Contains, document, story));
    add(NodeRecord(3, NodeKind::Story, story, &document, 0));
    add(EdgeRecord(4, EdgeKind::Contains, story, section));
    add(NodeRecord(5, NodeKind::Section, section, &story, 0));
    add(EdgeRecord(6, EdgeKind::Contains, section, paragraph));
    add(NodeRecord(7, NodeKind::Paragraph, paragraph, &section, 0));
    std::uint64_t nextId = 8;
    add(EdgeRecord(nextId++, EdgeKind::Contains, paragraph, first,
                   descendingEdges ? 256 : firstSibling));
    if (containsMode != 1)
      add(EdgeRecord(nextId++, EdgeKind::Contains,
                     containsMode == 3 ? section : paragraph, second,
                     descendingEdges ? 1 : secondSibling));
    if (containsMode == 2)
      add(EdgeRecord(nextId++, EdgeKind::Contains, paragraph, second,
                     secondSibling + 1));
    add(NodeRecord(nextId++, NodeKind::GenericControl, first, &paragraph,
                   firstSibling));
    add(EdgeRecord(nextId++, EdgeKind::Anchors, first, paragraph));
    add(NodeRecord(nextId++, NodeKind::GenericControl, second, &paragraph,
                   secondSibling));
    add(EdgeRecord(nextId, EdgeKind::Anchors, second, paragraph));
    MemoryReader reader{&bytes};
    std::uint64_t count = 0;
    return ValidateRecordStream(
        {&reader, ReadMemory, bytes.size(), StreamKind::Capture}, &count);
  };
  ok = Expect(authorityProbe(1, 256, 0, false) == Error::None &&
                  authorityProbe(256, 1, 0, false) == Error::BadRecordOrder &&
                  authorityProbe(1, 256, 0, true) == Error::BadRecordOrder,
              L"numeric sibling and edge order rejects 256 before 1") &&
       ok;
  ok = Expect(authorityProbe(1, 256, 1, false) == Error::BadRecordOrder &&
                  authorityProbe(1, 256, 2, false) == Error::BadRecordOrder &&
                  authorityProbe(1, 256, 3, false) == Error::BadRecordOrder,
              L"Contains authority rejects missing duplicate and mismatch") &&
       ok;
  const auto anchorsProbe = [](const int mode) {
    const Uuid128 document = Uuid(3051), story = Uuid(3052),
                  section = Uuid(3053), paragraph = Uuid(3054),
                  control = Uuid(3055);
    const std::uint64_t total = mode == 1 ? 10 : mode == 2 ? 12 : 11;
    Bytes bytes = ManifestRecord(total);
    const auto add = [&](const Bytes& record) {
      bytes.insert(bytes.end(), record.begin(), record.end());
    };
    add(NodeRecord(1, NodeKind::Document, document, nullptr, 0));
    add(EdgeRecord(2, EdgeKind::Contains, document, story));
    add(NodeRecord(3, NodeKind::Story, story, &document, 0));
    add(EdgeRecord(4, EdgeKind::Contains, story, section));
    add(NodeRecord(5, NodeKind::Section, section, &story, 0));
    add(EdgeRecord(6, EdgeKind::Contains, section, paragraph));
    add(NodeRecord(7, NodeKind::Paragraph, paragraph, &section, 0));
    add(EdgeRecord(8, EdgeKind::Contains, paragraph, control));
    add(NodeRecord(9, NodeKind::GenericControl, control, &paragraph, 0));
    if (mode != 1)
      add(EdgeRecord(10, EdgeKind::Anchors, control,
                     mode == 3 ? section : paragraph));
    if (mode == 2)
      add(EdgeRecord(11, EdgeKind::Anchors, control, paragraph, 1));
    MemoryReader reader{&bytes};
    std::uint64_t count = 0;
    return ValidateRecordStream(
        {&reader, ReadMemory, bytes.size(), StreamKind::Capture}, &count);
  };
  ok = Expect(anchorsProbe(0) == Error::None &&
                  anchorsProbe(1) == Error::BadRecordOrder &&
                  anchorsProbe(2) == Error::BadRecordOrder &&
                  anchorsProbe(3) == Error::BadRecordOrder &&
                  OutgoingCardinality(EdgeKind::Anchors,
                                      NodeKind::GenericControl, true) ==
                      EdgeCardinality::ExactlyOne &&
                  OutgoingCardinality(EdgeKind::Anchors, NodeKind::Table,
                                      true) == EdgeCardinality::ExactlyOne &&
                  OutgoingCardinality(EdgeKind::Anchors, NodeKind::Image,
                                      true) == EdgeCardinality::ExactlyOne &&
                  OutgoingCardinality(EdgeKind::Anchors, NodeKind::Paragraph,
                                      true) == EdgeCardinality::Zero,
              L"Anchors zero one two cardinality is control-family only") &&
       ok;
  std::wcout << L"CODEC_ANCHORS_EXACTLY_ONE_ZERO_ONE_TWO " << ok << L'\n';
  const auto definitionOrderProbe = [](
      ObservationState firstState, std::int64_t firstNative,
      const Sha256& firstAggregate, ObservationState secondState,
      std::int64_t secondNative, const Sha256& secondAggregate) {
    const Uuid128 document = Uuid(3101), first = Uuid(3102),
                  second = Uuid(3103);
    Bytes bytes = ManifestRecord(6);
    const auto add = [&](const Bytes &record) {
      bytes.insert(bytes.end(), record.begin(), record.end());
    };
    add(NodeRecord(1, NodeKind::Document, document, nullptr, 0));
    add(EdgeRecord(2, EdgeKind::Contains, document, first, 0));
    add(EdgeRecord(3, EdgeKind::Contains, document, second, 1));
    add(NodeRecord(4, NodeKind::Definition, first, &document, 0, {}, {},
                   nullptr, {}, {}, ObservationState::NotExposed, firstNative,
                   DefinitionKind::Style, &firstAggregate, firstState));
    add(NodeRecord(5, NodeKind::Definition, second, &document, 1, {}, {},
                   nullptr, {}, {}, ObservationState::NotExposed,
                   secondNative, DefinitionKind::Style, &secondAggregate,
                   secondState));
    MemoryReader reader{&bytes};
    std::uint64_t count = 0;
    return ValidateRecordStream(
        {&reader, ReadMemory, bytes.size(), StreamKind::Capture}, &count);
  };
  Sha256 aggregateA{};
  Sha256 aggregateB{};
  aggregateB.bytes.back() = 1;
  ok = Expect(
      definitionOrderProbe(
          ObservationState::NotExposed, 999, aggregateA,
          ObservationState::NotExposed, -999, aggregateB) == Error::None &&
      definitionOrderProbe(
          ObservationState::NotExposed, -999, aggregateB,
          ObservationState::NotExposed, 999, aggregateA) ==
              Error::BadRecordOrder &&
      definitionOrderProbe(
          ObservationState::NotExposed, 999, aggregateA,
          ObservationState::Value, 0, aggregateB) == Error::None &&
      definitionOrderProbe(
          ObservationState::Value, 0, aggregateA,
          ObservationState::NotExposed, 999, aggregateB) ==
              Error::BadRecordOrder &&
      definitionOrderProbe(
          ObservationState::Value, 0, aggregateA,
          ObservationState::Value, 0, aggregateB) == Error::BadRecordOrder,
      L"definition order ignores hidden IDs and rejects exposed-ID conflicts") &&
      ok;
  const auto binaryOrderProbe = [](bool reverse) {
    BlobNodeInput a{Uuid(3201), 0, Bytes{'a'}}, b{Uuid(3202), 0, Bytes{'b'}};
    BlobNodeInput *first = &a, *second = &b;
    if ((Hash(View(a.content)).bytes < Hash(View(b.content)).bytes) == reverse)
      std::swap(first, second);
    const Uuid128 document = Uuid(3200), firstNode = Uuid(3203),
                  secondNode = Uuid(3204);
    const Bytes sourceName = Utf16(nullptr, 0);
    Bytes bytes = ManifestRecord(8, ProfileBit(ProfileId::Structure) |
                                        ProfileBit(ProfileId::BinaryContent));
    const auto add = [&](const Bytes &record) {
      bytes.insert(bytes.end(), record.begin(), record.end());
    };
    add(NodeRecord(1, NodeKind::Document, document, nullptr, 0));
    add(EdgeRecord(2, EdgeKind::Contains, document, firstNode, 0));
    add(EdgeRecord(3, EdgeKind::Contains, document, secondNode, 1));
    add(NodeRecord(4, NodeKind::BinaryData, firstNode, &document, 0, {}, {},
                   first, sourceName));
    add(AssetChunkRecord(5, *first, first->content));
    add(NodeRecord(6, NodeKind::BinaryData, secondNode, &document, 1, {}, {},
                   second, sourceName));
    add(AssetChunkRecord(7, *second, second->content));
    MemoryReader reader{&bytes};
    std::uint64_t count = 0;
    return ValidateRecordStream(
        {&reader, ReadMemory, bytes.size(), StreamKind::Capture}, &count);
  };
  ok = Expect(binaryOrderProbe(false) == Error::None &&
                  binaryOrderProbe(true) == Error::BadRecordOrder,
              L"binary digest ordering is canonical") &&
       ok;
  const CanonicalizationResult base =
      SmallGraph(100, true, PropertyOrigin::Direct, false, false, &error);
  std::cout << "CODEC_BASE_SEMANTIC_ROOT "
            << Hex(base.observedSemanticRoot) << '\n'
            << "CODEC_BASE_CAPTURE_ROOT " << Hex(base.captureRoot)
            << '\n';
  ok = Expect(error == Error::None &&
                  Hex(base.observedSemanticRoot) ==
                      "e95b6a42c8c9507f604a83928de2c0045c1c2b93d5a16cf66ca630db"
                      "71e4f8d3" &&
                  Hex(base.captureRoot) ==
                      "13146042c03ab86e8aac08e8a36d9dba72e3adfbc3bd553fe5da9fd6ac"
                      "5a9078",
              L"public record canonicalizer independent Python parity") &&
       ok;
  const CanonicalizationResult differentIds =
      SmallGraph(9000, true, PropertyOrigin::Direct, false, false, &error);
  ok = Expect(error == Error::None &&
                  Equal(base.observedSemanticRoot,
                        differentIds.observedSemanticRoot) &&
                  Equal(base.layoutRoot, differentIds.layoutRoot) &&
                  Equal(base.captureRoot, differentIds.captureRoot),
              L"real UUID variants excluded by public record API") &&
       ok;
  BlobNodeInput blobA{Uuid(700), 17, Bytes{'t', 'e', 'x', 't'}};
  BlobNodeInput blobB{Uuid(800), UINT64_C(0x100000021),
                      Bytes{'t', 'e', 'x', 't'}};
  const CanonicalizationResult blobRootA = BlobGraph(100, &blobA, &error);
  const CanonicalizationResult blobRootB = BlobGraph(9000, &blobB, &error);
  ok = Expect(error == Error::None &&
                  Equal(blobRootA.observedSemanticRoot,
                        blobRootB.observedSemanticRoot) &&
                  Equal(blobRootA.captureRoot, blobRootB.captureRoot),
              L"ContentId and physical BlobSlice offset excluded after "
              L"dereference") &&
       ok;
  BlobNodeInput changedBlob = blobA;
  changedBlob.content = Bytes{'T', 'E', 'X', 'T'};
  const CanonicalizationResult changedText =
      BlobGraph(100, &changedBlob, &error);
  ok = Expect(error == Error::None && !Equal(blobRootA.observedSemanticRoot,
                                             changedText.observedSemanticRoot),
              L"dereferenced text content changes semantic root") &&
       ok;

  BlobNodeInput assetA{Uuid(901), 0, Bytes{'a', 's', 's', 'e', 't'}};
  BlobNodeInput assetIdVariant{Uuid(902), UINT64_C(0x100000051),
                               assetA.content};
  const Bytes sourceA = Utf16(std::array<std::uint16_t, 1>{L'a'}.data(), 1);
  const Bytes sourceB = Utf16(std::array<std::uint16_t, 1>{L'b'}.data(), 1);
  const CanonicalizationResult binaryA =
      BinaryGraph(200, &assetA, sourceA, nullptr, &error);
  const CanonicalizationResult binaryIdVariant =
      BinaryGraph(900, &assetIdVariant, sourceA, nullptr, &error);
  const CanonicalizationResult binarySourceVariant =
      BinaryGraph(200, &assetA, sourceB, nullptr, &error);
  ok = Expect(error == Error::None &&
                  Equal(binaryA.observedSemanticRoot,
                        binaryIdVariant.observedSemanticRoot) &&
                  Equal(binaryA.captureRoot, binaryIdVariant.captureRoot) &&
                  Equal(binaryA.observedSemanticRoot,
                        binarySourceVariant.observedSemanticRoot) &&
                  !Equal(binaryA.captureRoot, binarySourceVariant.captureRoot),
              L"AssetChunk Bytes verified, ContentId excluded, and "
              L"capture-only UTF16 included") &&
       ok;
  const Bytes corruptedAsset{'A', 'S', 'S', 'E', 'T'};
  static_cast<void>(
      BinaryGraph(200, &assetA, sourceA, &corruptedAsset, &error));
  ok = Expect(error == Error::BadScalar,
              L"AssetChunk bytes cannot be skipped behind declared digest") &&
       ok;
  BlobNodeInput changedAsset = assetA;
  changedAsset.content = corruptedAsset;
  const CanonicalizationResult binaryContentVariant =
      BinaryGraph(200, &changedAsset, sourceA, nullptr, &error);
  ok = Expect(error == Error::None &&
                  !Equal(binaryA.observedSemanticRoot,
                         binaryContentVariant.observedSemanticRoot) &&
                  !Equal(binaryA.captureRoot, binaryContentVariant.captureRoot),
              L"AssetChunk content changes semantic and capture roots") &&
       ok;

  const CanonicalizationResult propertyRecordShift = SmallGraph(
      100, true, PropertyOrigin::Direct, true, false, &error, false, nullptr,
      nullptr, {}, {}, ObservationState::NotExposed, 0,
      LayoutEnvironment(96));
  ok = Expect(error == Error::None &&
                  Equal(base.observedSemanticRoot,
                        propertyRecordShift.observedSemanticRoot) &&
                  !Equal(base.layoutRoot, propertyRecordShift.layoutRoot) &&
                  !Equal(base.captureRoot, propertyRecordShift.captureRoot),
              L"PropertyRecordId substitution and layout routing") &&
       ok;
  const CanonicalizationResult changedTextualFact =
      SmallGraph(100, false, PropertyOrigin::Direct, false, false, &error);
  ok = Expect(error == Error::None &&
                  !Equal(base.observedSemanticRoot,
                         changedTextualFact.observedSemanticRoot) &&
                  !Equal(base.captureRoot, changedTextualFact.captureRoot),
              L"semantic property changes semantic and capture roots") &&
       ok;
  const CanonicalizationResult changedOrigin =
      SmallGraph(100, true, PropertyOrigin::Inherited, false, false, &error);
  ok = Expect(error == Error::None &&
                  !Equal(base.observedSemanticRoot,
                         changedOrigin.observedSemanticRoot) &&
                  !Equal(base.captureRoot, changedOrigin.captureRoot),
              L"property origin changes semantic and capture") &&
       ok;
  BlobNodeInput diagnosticEmpty{Uuid(801), 11, {}};
  const CanonicalizationResult changedDiagnostic =
      SmallGraph(100, true, PropertyOrigin::Direct, false, true, &error, false,
                 &diagnosticEmpty);
  ok = Expect(error == Error::None &&
                  Equal(base.observedSemanticRoot,
                        changedDiagnostic.observedSemanticRoot) &&
                  Equal(base.layoutRoot, changedDiagnostic.layoutRoot) &&
                  !Equal(base.captureRoot, changedDiagnostic.captureRoot) &&
                  !Equal(base.diagnostics.digest,
                         changedDiagnostic.diagnostics.digest),
              L"diagnostic changes capture only") &&
       ok;
  BlobNodeInput diagnosticA{Uuid(802), 19, Bytes{'A', 0, 'B', 0}};
  BlobNodeInput diagnosticB{Uuid(803), UINT64_C(0x100000031),
                            Bytes{'C', 0, 'D', 0}};
  const CanonicalizationResult detailA =
      SmallGraph(100, true, PropertyOrigin::Direct, false, true, &error, false,
                 &diagnosticA);
  const CanonicalizationResult detailB =
      SmallGraph(100, true, PropertyOrigin::Direct, false, true, &error, false,
                 &diagnosticB);
  ok = Expect(
           error == Error::None && diagnosticA.callbackCount != 0 &&
               diagnosticB.callbackCount != 0 &&
               Equal(detailA.observedSemanticRoot,
                     detailB.observedSemanticRoot) &&
               Equal(detailA.layoutRoot, detailB.layoutRoot) &&
               !Equal(detailA.diagnostics.digest, detailB.diagnostics.digest) &&
               !Equal(detailA.captureRoot, detailB.captureRoot),
           L"diagnostic detail dereferenced and capture-canonical") &&
       ok;
  BlobNodeInput relocatedDiagnostic{Uuid(804), UINT64_C(0x200000031),
                                    diagnosticA.content};
  const CanonicalizationResult relocatedDetail =
      SmallGraph(100, true, PropertyOrigin::Direct, false, true, &error, false,
                 &relocatedDiagnostic);
  ok = Expect(error == Error::None && relocatedDiagnostic.callbackCount != 0 &&
                  Equal(detailA.diagnostics.digest,
                        relocatedDetail.diagnostics.digest) &&
                  Equal(detailA.captureRoot, relocatedDetail.captureRoot),
              L"diagnostic ContentId and physical offset excluded") &&
       ok;

  BlobNodeInput coverageA{Uuid(805), 23, Bytes{'E', 0, 'F', 0}};
  BlobNodeInput coverageB{Uuid(806), UINT64_C(0x300000041),
                          Bytes{'G', 0, 'H', 0}};
  const CanonicalizationResult coverageRootA =
      SmallGraph(100, true, PropertyOrigin::Direct, false, false, &error, false,
                 nullptr, &coverageA);
  const CanonicalizationResult coverageRootB =
      SmallGraph(100, true, PropertyOrigin::Direct, false, false, &error, false,
                 nullptr, &coverageB);
  ok = Expect(error == Error::None && coverageA.callbackCount != 0 &&
                  coverageB.callbackCount != 0 &&
                  Equal(coverageRootA.observedSemanticRoot,
                        coverageRootB.observedSemanticRoot) &&
                  Equal(coverageRootA.layoutRoot, coverageRootB.layoutRoot) &&
                  !Equal(coverageRootA.coverage.digest,
                         coverageRootB.coverage.digest) &&
                  !Equal(coverageRootA.captureRoot, coverageRootB.captureRoot),
              L"coverage detail dereferenced and capture-canonical") &&
       ok;
  BlobNodeInput relocatedCoverage{Uuid(807), UINT64_C(0x400000041),
                                  coverageA.content};
  const CanonicalizationResult relocatedCoverageRoot =
      SmallGraph(100, true, PropertyOrigin::Direct, false, false, &error, false,
                 nullptr, &relocatedCoverage);
  ok = Expect(error == Error::None && relocatedCoverage.callbackCount != 0 &&
                  Equal(coverageRootA.coverage.digest,
                        relocatedCoverageRoot.coverage.digest) &&
                  Equal(coverageRootA.captureRoot,
                        relocatedCoverageRoot.captureRoot),
              L"coverage ContentId and physical offset excluded") &&
       ok;

  const Bytes captureUtf16A =
      Utf16(std::array<std::uint16_t, 1>{L'A'}.data(), 1);
  const Bytes captureUtf16B =
      Utf16(std::array<std::uint16_t, 1>{L'B'}.data(), 1);
  const CanonicalizationResult captureTextA =
      SmallGraph(100, true, PropertyOrigin::Direct, false, false, &error, false,
                 nullptr, nullptr, captureUtf16A);
  const CanonicalizationResult captureTextB =
      SmallGraph(100, true, PropertyOrigin::Direct, false, false, &error, false,
                 nullptr, nullptr, captureUtf16B);
  ok = Expect(error == Error::None &&
                  Equal(captureTextA.observedSemanticRoot,
                        captureTextB.observedSemanticRoot) &&
                  Equal(captureTextA.layoutRoot, captureTextB.layoutRoot) &&
                  !Equal(captureTextA.captureRoot, captureTextB.captureRoot),
              L"capture-only UTF16 content changes capture only") &&
       ok;
  const Bytes unavailableA{'x', 0}, unavailableB{'y', 0};
  const CanonicalizationResult terminalA =
      SmallGraph(100, true, PropertyOrigin::Direct, false, false, &error, false,
                 nullptr, nullptr, {}, unavailableA);
  const CanonicalizationResult terminalB =
      SmallGraph(100, true, PropertyOrigin::Direct, false, false, &error, false,
                 nullptr, nullptr, {}, unavailableB);
  ok = Expect(error == Error::None &&
                  Equal(terminalA.observedSemanticRoot,
                        terminalB.observedSemanticRoot) &&
                  Equal(terminalA.layoutRoot, terminalB.layoutRoot) &&
                  !Equal(terminalA.unavailable.digest,
                         terminalB.unavailable.digest) &&
                  !Equal(terminalA.captureRoot, terminalB.captureRoot),
              L"unavailable observation detail changes capture only") &&
       ok;
  const CanonicalizationResult inconsistentA = SmallGraph(
      100, true, PropertyOrigin::Direct, false, false, &error, false, nullptr,
      nullptr, {}, unavailableA, ObservationState::InconsistentNativeState);
  const CanonicalizationResult inconsistentB = SmallGraph(
      100, true, PropertyOrigin::Direct, false, false, &error, false, nullptr,
      nullptr, {}, unavailableB, ObservationState::InconsistentNativeState);
  ok = Expect(error == Error::None &&
                  Equal(inconsistentA.observedSemanticRoot,
                        inconsistentB.observedSemanticRoot) &&
                  Equal(inconsistentA.layoutRoot, inconsistentB.layoutRoot) &&
                  !Equal(inconsistentA.unavailable.digest,
                         inconsistentB.unavailable.digest) &&
                  !Equal(inconsistentA.captureRoot, inconsistentB.captureRoot),
              L"inconsistent observation detail changes capture only") &&
       ok;

  static_cast<void>(SmallGraph(100, true, PropertyOrigin::Direct, false, false,
                               &error, false, nullptr, nullptr, {}, {},
                               ObservationState::NotExposed, 1));
  ok = Expect(error == Error::None, L"numeric property order accepted") && ok;
  static_cast<void>(SmallGraph(100, true, PropertyOrigin::Direct, false, false,
                               &error, false, nullptr, nullptr, {}, {},
                               ObservationState::NotExposed, 2));
  ok = Expect(error == Error::BadRecordOrder,
              L"descending numeric property order rejected") &&
       ok;
  static_cast<void>(SmallGraph(100, true, PropertyOrigin::Direct, false, false,
                               &error, true));
  ok = Expect(error == Error::BadRecordOrder,
              L"duplicate semantic property authority rejected") &&
       ok;

  // Broken parent authority is rejected through the same public entry point.
  Bytes broken = ManifestRecord(2);
  const Uuid128 child = Uuid(44), missing = Uuid(45);
  const Bytes brokenNode = NodeRecord(1, NodeKind::Story, child, &missing, 0);
  broken.insert(broken.end(), brokenNode.begin(), brokenNode.end());
  MemoryReader brokenReader{&broken};
  CanonicalizationResult rejected{};
  ok = Expect(CanonicalizeRecordStream({{&brokenReader, ReadMemory,
                                         broken.size(), StreamKind::Capture},
                                        nullptr,
                                        nullptr,
                                        {}},
                                       &rejected) == Error::BadRecordOrder,
              L"broken authority rejected") &&
       ok;

  const Uuid128 cycleDocument = Uuid(71), cycleStory = Uuid(72),
                cycleSection = Uuid(73), cycleParagraph = Uuid(74),
                cycleControl = Uuid(75);
  Bytes cyclic = ManifestRecord(11);
  const auto addCycle = [&](const Bytes &record) {
    cyclic.insert(cyclic.end(), record.begin(), record.end());
  };
  addCycle(NodeRecord(1, NodeKind::Document, cycleDocument, nullptr, 0));
  addCycle(EdgeRecord(2, EdgeKind::Contains, cycleDocument, cycleStory));
  addCycle(NodeRecord(3, NodeKind::Story, cycleStory, &cycleDocument, 0));
  addCycle(EdgeRecord(4, EdgeKind::Contains, cycleStory, cycleSection));
  addCycle(NodeRecord(5, NodeKind::Section, cycleSection, &cycleStory, 0));
  addCycle(EdgeRecord(6, EdgeKind::Contains, cycleSection, cycleParagraph));
  addCycle(
      NodeRecord(7, NodeKind::Paragraph, cycleParagraph, &cycleSection, 0));
  addCycle(EdgeRecord(8, EdgeKind::Contains, cycleParagraph, cycleControl));
  addCycle(NodeRecord(9, NodeKind::GenericControl, cycleControl,
                      &cycleParagraph, 0));
  addCycle(EdgeRecord(10, EdgeKind::AssetRef, cycleControl, cycleControl));
  MemoryReader cyclicReader{&cyclic};
  ok = Expect(CanonicalizeRecordStream({{&cyclicReader, ReadMemory,
                                         cyclic.size(), StreamKind::Capture},
                                        nullptr,
                                        nullptr,
                                        {}},
                                       &rejected) == Error::BadRecordOrder,
              L"semantic reference cycle rejected") &&
       ok;

  const Bytes environment96 = LayoutEnvironment(96);
  const Bytes environment120 = LayoutEnvironment(120);
  const CanonicalizationResult layout96 = SmallGraph(
      5100, true, PropertyOrigin::Direct, true, false, &error,
      false, nullptr, nullptr, {}, {}, ObservationState::NotExposed, 0,
      environment96);
  const bool firstLayoutValid = error == Error::None;
  const CanonicalizationResult layout120 = SmallGraph(
      5100, true, PropertyOrigin::Direct, true, false, &error,
      false, nullptr, nullptr, {}, {}, ObservationState::NotExposed, 0,
      environment120);
  const bool layoutIsolation =
      firstLayoutValid && error == Error::None &&
      layout96.semanticCertified && layout120.semanticCertified &&
      layout96.layoutPresent && layout120.layoutPresent &&
      Equal(layout96.observedSemanticRoot,
            layout120.observedSemanticRoot) &&
      !Equal(layout96.layoutRoot, layout120.layoutRoot) &&
      !Equal(layout96.captureRoot, layout120.captureRoot);
  std::wcout << L"CODEC_LAYOUT_ONLY_MUTATION_ROOT_ISOLATION "
             << layoutIsolation << L'\n';
  ok = Expect(
      layoutIsolation,
      L"normative layout-only mutation isolates semantic root") && ok;
  return ok;
}
bool ReadHandle(void *context, std::uint64_t offset, std::uint8_t *buffer,
                std::uint32_t requested, std::uint32_t *actual) noexcept {
  HANDLE file = context;
  LARGE_INTEGER position{};
  position.QuadPart = static_cast<LONGLONG>(offset);
  DWORD read = 0;
  const bool ok =
      SetFilePointerEx(file, position, nullptr, FILE_BEGIN) != FALSE &&
      ReadFile(file, buffer, requested, &read, nullptr) != FALSE;
  *actual = read;
  return ok;
}
std::uint64_t FileBytes(HANDLE file);

struct IncrementalReplayStream final {
  std::vector<Bytes> records{};
  std::vector<std::uint64_t> offsets{};
  std::uint64_t length = 0;
  bool complete = false;
  std::mutex mutex{};
  std::condition_variable changed{};
};

bool ReadIncrementalReplay(void *context, std::uint64_t offset,
                           std::uint8_t *buffer, std::uint32_t requested,
                           std::uint32_t *actual) noexcept {
  auto *stream = static_cast<IncrementalReplayStream *>(context);
  if (stream == nullptr || buffer == nullptr || actual == nullptr) return false;
  std::unique_lock<std::mutex> lock(stream->mutex);
  stream->changed.wait(lock, [stream, offset, requested]() {
    return stream->complete ||
        (offset <= stream->length && requested <= stream->length - offset);
  });
  if (offset > stream->length || requested > stream->length - offset)
    return false;
  const auto upper = std::upper_bound(
      stream->offsets.begin(), stream->offsets.end(), offset);
  std::size_t index = upper == stream->offsets.begin()
      ? 0 : static_cast<std::size_t>(upper - stream->offsets.begin() - 1);
  std::uint64_t position = offset;
  *actual = 0;
  while (*actual != requested && index < stream->records.size()) {
    const Bytes &record = stream->records[index];
    const std::size_t within = static_cast<std::size_t>(
        position - stream->offsets[index]);
    const std::size_t count = (std::min)(
        static_cast<std::size_t>(requested - *actual), record.size() - within);
    std::copy_n(record.data() + within, count, buffer + *actual);
    *actual += static_cast<std::uint32_t>(count);
    position += count;
    ++index;
  }
  return true;
}

bool ViewIncrementalReplay(void *context, std::uint64_t offset,
                           ByteView *record) noexcept {
  auto *stream = static_cast<IncrementalReplayStream *>(context);
  if (stream == nullptr || record == nullptr) return false;
  const std::lock_guard<std::mutex> lock(stream->mutex);
  const auto found = std::lower_bound(
      stream->offsets.begin(), stream->offsets.end(), offset);
  if (found == stream->offsets.end() || *found != offset) return false;
  const std::size_t index = static_cast<std::size_t>(
      found - stream->offsets.begin());
  *record = View(stream->records[index]);
  return true;
}

std::uint64_t IncrementalReplayLength(void *context) noexcept {
  auto *stream = static_cast<IncrementalReplayStream *>(context);
  if (stream == nullptr) return 0;
  const std::lock_guard<std::mutex> lock(stream->mutex);
  return stream->length;
}

bool IncrementalReplayComplete(void *context) noexcept {
  auto *stream = static_cast<IncrementalReplayStream *>(context);
  if (stream == nullptr) return true;
  const std::lock_guard<std::mutex> lock(stream->mutex);
  return stream->complete;
}

bool ProduceIncrementalReplay(
    HANDLE source, IncrementalReplayStream *stream,
    const std::uint64_t mutationOffset = UINT64_MAX) {
  const std::uint64_t total = FileBytes(source);
  std::uint64_t offset = 0;
  while (offset != total) {
    std::array<std::uint8_t, 24> header{};
    std::uint32_t actual = 0;
    if (!ReadHandle(source, offset, header.data(), header.size(), &actual) ||
        actual != header.size()) return false;
    std::uint64_t payload = 0;
    for (unsigned byte = 0; byte != 8; ++byte)
      payload |= static_cast<std::uint64_t>(header[16 + byte]) << (byte * 8);
    if (payload > MAXDWORD - header.size() ||
        payload > total - offset - header.size()) return false;
    Bytes record(static_cast<std::size_t>(payload + header.size()));
    std::copy(header.begin(), header.end(), record.begin());
    if (payload != 0 &&
        (!ReadHandle(source, offset + header.size(),
                     record.data() + header.size(),
                     static_cast<std::uint32_t>(payload), &actual) ||
         actual != payload)) return false;
    if (mutationOffset >= offset &&
        mutationOffset - offset < record.size())
      record[static_cast<std::size_t>(mutationOffset - offset)] ^= 1;
    {
      const std::lock_guard<std::mutex> lock(stream->mutex);
      stream->offsets.push_back(stream->length);
      stream->length += record.size();
      stream->records.push_back(std::move(record));
    }
    stream->changed.notify_all();
    offset += payload + header.size();
  }
  return true;
}

void CompleteIncrementalReplay(IncrementalReplayStream *stream) {
  {
    const std::lock_guard<std::mutex> lock(stream->mutex);
    stream->complete = true;
  }
  stream->changed.notify_all();
}

bool ReadBlobFixture(void *context, const ContentId &contentId,
                     std::uint64_t offset, std::uint8_t *buffer,
                     std::uint32_t requested, std::uint32_t *actual) noexcept {
  auto *blobs = static_cast<std::vector<BlobNodeInput> *>(context);
  if (blobs == nullptr || buffer == nullptr || actual == nullptr) return false;
  for (BlobNodeInput &blob : *blobs) {
    if (blob.contentId.bytes != contentId.bytes || offset < blob.physicalOffset ||
        offset - blob.physicalOffset > blob.content.size()) continue;
    ++blob.callbackCount;
    const std::uint64_t relative = offset - blob.physicalOffset;
    *actual = static_cast<std::uint32_t>((std::min<std::uint64_t>)(
        requested, blob.content.size() - relative));
    if (*actual != 0)
      std::memcpy(buffer, blob.content.data() + relative, *actual);
    return true;
  }
  return false;
}

bool CreateScaleSpool(const std::filesystem::path &recordsPath,
                      const std::filesystem::path &indexPath, HANDLE *records,
                      HANDLE *index, std::uint64_t controls,
                      std::vector<BlobNodeInput> *blobs = nullptr) {
  *records = CreateFileW(recordsPath.c_str(), GENERIC_READ | GENERIC_WRITE,
                         FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                         FILE_ATTRIBUTE_NORMAL, nullptr);
  *index = CreateFileW(indexPath.c_str(), GENERIC_READ | GENERIC_WRITE,
                       FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, nullptr);
  if (*records == INVALID_HANDLE_VALUE || *index == INVALID_HANDLE_VALUE)
    return false;
  const std::uint64_t blobCount = blobs == nullptr ? 0 : blobs->size();
  if (!WriteRecord(*records, ManifestRecord(controls * 3 + 8 + blobCount)))
    return false;
  const bool queryCompatible = blobs != nullptr;
  const Uuid128 document = queryCompatible ? RfcUuid(0x10000001) : Uuid(1);
  const Uuid128 story = queryCompatible ? RfcUuid(0x20000008) : ReceiptUuid(8);
  const Uuid128 section = queryCompatible ? RfcUuid(0x30000003) : Uuid(3);
  const Uuid128 paragraph = queryCompatible ? RfcUuid(0x40000004) : Uuid(4);
  if (!WriteRecord(*records,
                   NodeRecord(1, NodeKind::Document, document, nullptr, 0)) ||
      !WriteRecord(*records,
                   EdgeRecord(2, EdgeKind::Contains, document, story)) ||
      !WriteRecord(*records,
                   NodeRecord(3, NodeKind::Story, story, &document, 0)) ||
      !WriteRecord(*records,
                   EdgeRecord(4, EdgeKind::Contains, story, section)) ||
      !WriteRecord(*records,
                   NodeRecord(5, NodeKind::Section, section, &story, 0)) ||
      !WriteRecord(*records,
                   EdgeRecord(6, EdgeKind::Contains, section, paragraph)) ||
      !WriteRecord(*records,
                   NodeRecord(7, NodeKind::Paragraph, paragraph, &section, 0)))
    return false;
  for (std::uint64_t ordinal = 0; ordinal < controls; ++ordinal) {
    const Uuid128 control = queryCompatible
        ? RfcUuid(static_cast<std::uint32_t>(0x50000000U + ordinal))
        : Uuid(static_cast<std::uint32_t>(ordinal + 5));
    if (!WriteRecord(*records, EdgeRecord(8 + ordinal, EdgeKind::Contains,
                                          paragraph, control, ordinal)))
      return false;
  }
  std::uint64_t nextRecord = 8 + controls;
  for (std::uint64_t ordinal = 0; ordinal < controls; ++ordinal) {
    const Uuid128 control = queryCompatible
        ? RfcUuid(static_cast<std::uint32_t>(0x50000000U + ordinal))
        : Uuid(static_cast<std::uint32_t>(ordinal + 5));
    if (!WriteRecord(*records, NodeRecord(
            nextRecord++, NodeKind::GenericControl, control, &paragraph,
            ordinal)))
      return false;
    if (ordinal < blobCount &&
        !WriteRecord(*records, CoverageRecord(
            nextRecord++, control, (*blobs)[static_cast<std::size_t>(ordinal)],
            ProfileId::Structure, CoverageState::NotExposed, 0,
            RecordKind::Node, 102)))
      return false;
    if (!WriteRecord(*records, EdgeRecord(
            nextRecord++, EdgeKind::Anchors, control, paragraph)))
      return false;
  }
  const std::array<std::uint8_t, 16> indexBytes{};
  DWORD written = 0;
  if (WriteFile(*index, indexBytes.data(),
                static_cast<DWORD>(indexBytes.size()), &written,
                nullptr) == FALSE ||
      written != indexBytes.size() || !FlushFileBuffers(*records) ||
      !FlushFileBuffers(*index))
    return false;
  Sha256 sourceIndexDigest{};
  if (!HashFileRangeChunked(*index, 0, FileBytes(*index), &sourceIndexDigest))
    return false;
  LARGE_INTEGER indexDigestPosition{};
  indexDigestPosition.QuadPart = 329;
  if (!SetFilePointerEx(*records, indexDigestPosition, nullptr, FILE_BEGIN) ||
      !WriteFile(*records, sourceIndexDigest.bytes.data(),
                 static_cast<DWORD>(sourceIndexDigest.bytes.size()), &written,
                 nullptr) ||
      written != sourceIndexDigest.bytes.size())
    return false;
  CanonicalizationResult canonical{};
  const Error scaleCanonicalError = CanonicalizeRecordStream(
      {{*records, ReadHandle, FileBytes(*records), StreamKind::Capture},
       blobs,
       blobs == nullptr ? nullptr : ReadBlobFixture,
       {}},
      &canonical);
  if (scaleCanonicalError != Error::None) {
    std::wcerr << L"CreateScaleSpool canonical error="
               << static_cast<unsigned>(scaleCanonicalError)
               << L" controls=" << controls << L" blobs=" << blobCount
               << L" bytes=" << FileBytes(*records) << L'\n';
    return false;
  }
  Bytes sourceBytes(static_cast<std::size_t>(FileBytes(*records)));
  LARGE_INTEGER position{};
  position.QuadPart = 0;
  if (!SetFilePointerEx(*records, position, nullptr, FILE_BEGIN) ||
      !ReadFile(*records, sourceBytes.data(),
                static_cast<DWORD>(sourceBytes.size()), &written, nullptr) ||
      written != sourceBytes.size())
    return false;
  std::vector<capture::CanonicalRecordPatch> patches;
  std::wstring patchFailure;
  if (!capture::BuildCanonicalRecordPatches(
          View(sourceBytes), canonical, &patches, &patchFailure)) {
    std::wcerr << L"CreateScaleSpool patch error=" << patchFailure
               << L" controls=" << controls << L" blobs=" << blobCount
               << L'\n';
    return false;
  }
  for (const capture::CanonicalRecordPatch &patch : patches) {
    position.QuadPart = static_cast<LONGLONG>(patch.offset);
    if (!SetFilePointerEx(*records, position, nullptr, FILE_BEGIN) ||
        !WriteFile(*records, patch.bytes.data(),
                   static_cast<DWORD>(patch.bytes.size()), &written,
                   nullptr) ||
        written != patch.bytes.size())
      return false;
  }
  return FlushFileBuffers(*records) != FALSE;
}
std::uint64_t FileBytes(HANDLE file) {
  LARGE_INTEGER size{};
  return GetFileSizeEx(file, &size) && size.QuadPart >= 0
             ? static_cast<std::uint64_t>(size.QuadPart)
             : 0;
}
CanonicalStream StreamFact(std::initializer_list<std::uint8_t> bytes) {
  Bytes value(bytes);
  return {value.size(), Hash(View(value))};
}
PublicationInput Publication(HANDLE records, HANDLE index, std::uint8_t seed,
                             std::uint64_t observedControlCount = 1,
                             std::vector<BlobNodeInput> *blobs = nullptr) {
  PublicationInput input;
  input.first.integrity = CaptureIntegrity::Complete;
  input.first.closedProfileBits = ProfileBit(ProfileId::Structure);
  static_cast<void>(seed);
  CanonicalizationResult canonical{};
  const Error canonicalError = CanonicalizeRecordStream(
      {{records, ReadHandle, FileBytes(records), StreamKind::Capture},
       blobs,
       blobs == nullptr ? nullptr : ReadBlobFixture,
       {}},
      &canonical);
  if (canonicalError == Error::None) {
    input.first.observedSemanticRoot = canonical.observedSemanticRoot;
    input.first.semanticCertified = canonical.semanticCertified;
    input.first.layoutPresent = canonical.layoutPresent;
    input.first.layoutRoot = canonical.layoutRoot;
    input.first.captureRoot = canonical.captureRoot;
    input.first.coverage = {canonical.coverage.byteLength,
                            canonical.coverage.digest};
    input.first.unavailable = {canonical.unavailable.byteLength,
                               canonical.unavailable.digest};
    input.first.diagnostics = {canonical.diagnostics.byteLength,
                               canonical.diagnostics.digest};
    static_cast<void>(CreateCanonicalArtifactFromFacts(
        {records, ReadHandle, FileBytes(records), StreamKind::Capture},
        canonical, &input.canonicalArtifact));
  }
  input.second = input.first;
  input.baseline.route = StreamFact({4});
  input.baseline.cursor = StreamFact({5});
  input.baseline.selection = StreamFact({6});
  input.baseline.modified = true;
  input.firstRestoration = input.baseline;
  input.secondRestoration = input.baseline;
  input.records = {records, 0, FileBytes(records)};
  input.index = {index, 0, FileBytes(index)};
  input.blobContext = blobs;
  input.readBlob = blobs == nullptr ? nullptr : ReadBlobFixture;
  input.observedControlCount = observedControlCount;
  input.legacyHwpmlDiagnosticFailed = true;
  return input;
}
bool BindPreparedArtifact(HANDLE file, const Sha256 &digest,
                          PreparedFileArtifact *artifact) {
  FILE_ID_INFO identity{};
  if (file == INVALID_HANDLE_VALUE || artifact == nullptr ||
      GetFileInformationByHandleEx(file, FileIdInfo, &identity,
                                   sizeof(identity)) == FALSE)
    return false;
  artifact->file = file;
  artifact->length = FileBytes(file);
  artifact->digest = digest;
  artifact->volumeSerial = identity.VolumeSerialNumber;
  std::copy(std::begin(identity.FileId.Identifier),
            std::end(identity.FileId.Identifier), artifact->fileId.begin());
  return true;
}

bool HashWholeFile(HANDLE file, Sha256 *digest) {
  return file != INVALID_HANDLE_VALUE && digest != nullptr &&
      HashFileRangeChunked(file, 0, FileBytes(file), digest);
}

bool FilesEqual(const std::filesystem::path &left,
                const std::filesystem::path &right) {
  HANDLE leftFile = CreateFileW(left.c_str(), GENERIC_READ, FILE_SHARE_READ,
                                nullptr, OPEN_EXISTING,
                                FILE_ATTRIBUTE_NORMAL, nullptr);
  HANDLE rightFile = CreateFileW(right.c_str(), GENERIC_READ, FILE_SHARE_READ,
                                 nullptr, OPEN_EXISTING,
                                 FILE_ATTRIBUTE_NORMAL, nullptr);
  Sha256 leftDigest{}, rightDigest{};
  const bool equal = leftFile != INVALID_HANDLE_VALUE &&
      rightFile != INVALID_HANDLE_VALUE && FileBytes(leftFile) == FileBytes(rightFile) &&
      HashWholeFile(leftFile, &leftDigest) && HashWholeFile(rightFile, &rightDigest) &&
      Equal(leftDigest, rightDigest);
  if (leftFile != INVALID_HANDLE_VALUE) CloseHandle(leftFile);
  if (rightFile != INVALID_HANDLE_VALUE) CloseHandle(rightFile);
  return equal;
}

std::vector<BlobNodeInput> ExactPreparedBlobs() {
  std::vector<BlobNodeInput> blobs(651);
  for (std::uint32_t index = 0; index < blobs.size(); ++index) {
    blobs[index].contentId.bytes[0] = static_cast<std::uint8_t>(index);
    blobs[index].contentId.bytes[1] = static_cast<std::uint8_t>(index >> 8);
    blobs[index].contentId.bytes[15] = 0xa5;
    blobs[index].content = {
        static_cast<std::uint8_t>(index * 37U + 11U), 0};
  }
  return blobs;
}

bool WriteExactBlobCache(HANDLE file,
                         const std::vector<BlobNodeInput> &blobs) {
  for (const BlobNodeInput &blob : blobs) {
    std::array<std::uint8_t, 28> header{};
    std::copy(blob.contentId.bytes.begin(), blob.contentId.bytes.end(),
              header.begin());
    header[24] = static_cast<std::uint8_t>(blob.content.size());
    DWORD written = 0;
    if (WriteFile(file, header.data(), static_cast<DWORD>(header.size()),
                  &written, nullptr) == FALSE || written != header.size() ||
        WriteFile(file, blob.content.data(),
                  static_cast<DWORD>(blob.content.size()), &written,
                  nullptr) == FALSE || written != blob.content.size())
      return false;
  }
  return FlushFileBuffers(file) != FALSE;
}

class FixedGenerationSource final : public query::GenerationSource {
public:
  explicit FixedGenerationSource(query::GenerationSnapshot snapshot)
      : snapshot_(std::move(snapshot)) {}
  bool Pin(query::GenerationSnapshot *snapshot) noexcept override {
    if (snapshot == nullptr) return false;
    *snapshot = snapshot_;
    return true;
  }
  bool IsCurrent(const query::GenerationKey &key) noexcept override {
    return query::Equal(snapshot_.key, key);
  }
  GenerationQueryIndexPin CachedQueryIndex(
      const query::GenerationKey &key) noexcept override {
    return query::Equal(snapshot_.key, key) ? index_ : GenerationQueryIndexPin{};
  }
  void CacheQueryIndex(const query::GenerationKey &key,
                       const GenerationQueryIndexPin &index) noexcept override {
    if (query::Equal(snapshot_.key, key)) index_ = index;
  }
private:
  query::GenerationSnapshot snapshot_{};
  GenerationQueryIndexPin index_{};
};

bool ExactPreparedPublicationSmoke(const std::filesystem::path &root) {
  constexpr std::uint64_t kRecords = 89291;
  constexpr std::uint64_t kBlobs = 651;
  constexpr std::uint64_t kControls = 29544;
  const auto genericRoot = root / L"exact-generic-store";
  const auto preparedRoot = root / L"exact-prepared-store";
  const auto genericSource = root / L"exact-generic-source";
  std::error_code error;
  std::filesystem::create_directories(genericSource, error);
  std::vector<BlobNodeInput> genericBlobs = ExactPreparedBlobs();
  HANDLE genericRecords = INVALID_HANDLE_VALUE;
  HANDLE genericIndex = INVALID_HANDLE_VALUE;
  GraphStore genericStore(genericRoot.wstring());
  const bool genericReady = !error && genericStore.Initialize() &&
      CreateScaleSpool(genericSource / L"records.hgn",
                       genericSource / L"index.hgi", &genericRecords,
                       &genericIndex, kControls, &genericBlobs);
  PublicationInput genericInput = genericReady
      ? Publication(genericRecords, genericIndex, 1, kControls, &genericBlobs)
      : PublicationInput{};
  const CanonicalizationResult *genericFacts =
      CanonicalArtifactFacts(genericInput.canonicalArtifact);
  const bool exactFixture = genericReady && genericFacts != nullptr &&
      genericFacts->recordStream.itemCount == kRecords &&
      genericFacts->blobClosure.size() == kBlobs &&
      !genericFacts->requiresActiveLineage;
  const bool genericPublished = exactFixture && genericStore.Publish(genericInput);

  GraphStore preparedStore(preparedRoot.wstring());
  const bool preparedInitialized = preparedStore.Initialize();
  const auto preparedDirectory = preparedRoot / L"prepared-exact-89291";
  std::filesystem::create_directories(preparedDirectory, error);
  HANDLE owner = CreateFileW((preparedDirectory / L"owner.lock").c_str(),
                             GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                             CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
  std::vector<BlobNodeInput> preparedBlobs = ExactPreparedBlobs();
  HANDLE preparedRecords = INVALID_HANDLE_VALUE;
  HANDLE preparedIndex = INVALID_HANDLE_VALUE;
  bool preparedReady = preparedInitialized && !error &&
      owner != INVALID_HANDLE_VALUE &&
      CreateScaleSpool(preparedDirectory / L"records.hgn",
                       preparedDirectory / L"index.hgi", &preparedRecords,
                       &preparedIndex, kControls, &preparedBlobs);
  HANDLE environment = CreateFileW(
      (preparedDirectory / L"environment.hge").c_str(),
      GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW,
      FILE_ATTRIBUTE_NORMAL, nullptr);
  HANDLE blobCache = CreateFileW((preparedDirectory / L"blobs.hgb").c_str(),
                                 GENERIC_READ | GENERIC_WRITE,
                                 FILE_SHARE_READ, nullptr, CREATE_NEW,
                                 FILE_ATTRIBUTE_NORMAL, nullptr);
  preparedReady = preparedReady && environment != INVALID_HANDLE_VALUE &&
      blobCache != INVALID_HANDLE_VALUE && FlushFileBuffers(environment) &&
      WriteExactBlobCache(blobCache, preparedBlobs);
  if (preparedRecords != INVALID_HANDLE_VALUE) CloseHandle(preparedRecords);
  if (preparedIndex != INVALID_HANDLE_VALUE) CloseHandle(preparedIndex);
  if (environment != INVALID_HANDLE_VALUE) CloseHandle(environment);
  if (blobCache != INVALID_HANDLE_VALUE) CloseHandle(blobCache);
  preparedRecords = CreateFileW((preparedDirectory / L"records.hgn").c_str(),
                                GENERIC_READ,
                                FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr,
                                OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  preparedIndex = CreateFileW((preparedDirectory / L"index.hgi").c_str(),
                              GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  environment = CreateFileW((preparedDirectory / L"environment.hge").c_str(),
                            GENERIC_READ,
                            FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  blobCache = CreateFileW((preparedDirectory / L"blobs.hgb").c_str(),
                          GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
                          nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                          nullptr);
  struct CanonicalReplayProfile final {
    Error error = Error::BadFlags;
    CanonicalizationResult result{};
    CodecDebugCounters counters{};
    std::uint64_t qpc = 0;
  };
  const auto canonicalReplay = [&](const bool descriptorReuse) {
    CanonicalReplayProfile profile;
    SetCanonicalDescriptorReuseForTesting(descriptorReuse);
    ResetCodecDebugCounters();
    LARGE_INTEGER replayStarted{}, replayFinished{};
    QueryPerformanceCounter(&replayStarted);
    profile.error = CanonicalizeRecordStream(
        {{preparedRecords, ReadHandle, FileBytes(preparedRecords),
           StreamKind::Capture},
         &preparedBlobs, ReadBlobFixture, {}},
        &profile.result);
    QueryPerformanceCounter(&replayFinished);
    profile.qpc = static_cast<std::uint64_t>(
        replayFinished.QuadPart - replayStarted.QuadPart);
    profile.counters = ReadCodecDebugCounters();
    return profile;
  };
  const CanonicalReplayProfile redReplay = canonicalReplay(false);
  const CanonicalReplayProfile greenReplay = canonicalReplay(true);
  SetCanonicalDescriptorReuseForTesting(true);
  const bool replayAuthorityEqual = redReplay.error == Error::None &&
      greenReplay.error == Error::None &&
      redReplay.result.recordStream.itemCount == kRecords &&
      greenReplay.result.recordStream.itemCount == kRecords &&
      Equal(redReplay.result.recordStream.digest,
            greenReplay.result.recordStream.digest) &&
      Equal(redReplay.result.observedSemanticRoot,
            greenReplay.result.observedSemanticRoot) &&
      Equal(redReplay.result.layoutRoot, greenReplay.result.layoutRoot) &&
      Equal(redReplay.result.captureRoot, greenReplay.result.captureRoot) &&
      redReplay.result.viewIndexDigest.bytes ==
          greenReplay.result.viewIndexDigest.bytes &&
      redReplay.result.nodeFingerprints.size() ==
          greenReplay.result.nodeFingerprints.size() &&
      std::equal(
          redReplay.result.nodeFingerprints.begin(),
          redReplay.result.nodeFingerprints.end(),
          greenReplay.result.nodeFingerprints.begin(),
          [](const auto& left, const auto& right) {
            return left.nodeId.bytes == right.nodeId.bytes &&
                Equal(left.fingerprint, right.fingerprint);
          });
  const bool descriptorCeiling =
      redReplay.counters.descriptorRecordReuses == 0 &&
      greenReplay.counters.descriptorRecordBuilds == kRecords &&
      greenReplay.counters.descriptorRecordReuses == kRecords * 2 &&
      greenReplay.counters.fieldDecodeWalks <
          redReplay.counters.fieldDecodeWalks &&
      greenReplay.counters.descriptorArenaGrowths <= 3 &&
      greenReplay.counters.retainedRecordCopies == kRecords;
  LARGE_INTEGER replayFrequency{};
  QueryPerformanceFrequency(&replayFrequency);
  const auto replayMilliseconds = [&replayFrequency](
      const std::uint64_t qpc) {
    return replayFrequency.QuadPart <= 0 ? 0.0 :
        1000.0 * static_cast<double>(qpc) /
            static_cast<double>(replayFrequency.QuadPart);
  };
  const double redReplayMs = replayMilliseconds(redReplay.qpc);
  const double greenReplayMs = replayMilliseconds(greenReplay.qpc);
  const auto incrementalReplay = [&](const std::uint64_t mutationOffset =
                                         UINT64_MAX,
                                     const HANDLE source = INVALID_HANDLE_VALUE) {
    CanonicalReplayProfile profile;
    IncrementalReplayStream stream;
    LARGE_INTEGER replayStarted{}, replayFinished{};
    QueryPerformanceCounter(&replayStarted);
    std::thread accumulator([&]() noexcept {
      profile.error = CanonicalizeRecordStream(
          {{&stream, ReadIncrementalReplay, 0, StreamKind::Capture, {},
             ViewIncrementalReplay, IncrementalReplayLength,
             IncrementalReplayComplete, true},
           &preparedBlobs, ReadBlobFixture, {}},
          &profile.result);
    });
    const bool produced = ProduceIncrementalReplay(
        source == INVALID_HANDLE_VALUE ? preparedRecords : source,
        &stream, mutationOffset);
    CompleteIncrementalReplay(&stream);
    accumulator.join();
    QueryPerformanceCounter(&replayFinished);
    if (!produced) profile.error = Error::Truncated;
    profile.qpc = static_cast<std::uint64_t>(
        replayFinished.QuadPart - replayStarted.QuadPart);
    return profile;
  };
  ResetCodecDebugCounters();
  const CanonicalReplayProfile incrementalFirst = incrementalReplay();
  const CanonicalReplayProfile incrementalSecond = incrementalReplay();
  const CodecDebugCounters incrementalCounters = ReadCodecDebugCounters();
  const bool incrementalAuthority = incrementalFirst.error == Error::None &&
      incrementalSecond.error == Error::None &&
      incrementalFirst.result.recordStream.itemCount == kRecords &&
      incrementalSecond.result.recordStream.itemCount == kRecords &&
      Equal(incrementalFirst.result.recordStream.digest,
            greenReplay.result.recordStream.digest) &&
      Equal(incrementalSecond.result.recordStream.digest,
            greenReplay.result.recordStream.digest) &&
      Equal(incrementalFirst.result.observedSemanticRoot,
            greenReplay.result.observedSemanticRoot) &&
      Equal(incrementalSecond.result.observedSemanticRoot,
            greenReplay.result.observedSemanticRoot) &&
      Equal(incrementalFirst.result.layoutRoot,
            greenReplay.result.layoutRoot) &&
      Equal(incrementalSecond.result.captureRoot,
            greenReplay.result.captureRoot);
  const bool zeroPostWalkCeiling = incrementalAuthority &&
      incrementalCounters.validatedCanonicalPostWalks == 0 &&
      incrementalCounters.incrementalAccumulatorFinalizations == 2 &&
      incrementalCounters.retainedRecordCopies == 0;
  std::array<std::uint8_t, 24> firstHeader{};
  std::uint32_t firstHeaderBytes = 0;
  bool firstHeaderReady = ReadHandle(
      preparedRecords, 0, firstHeader.data(), firstHeader.size(),
      &firstHeaderBytes) && firstHeaderBytes == firstHeader.size();
  std::uint64_t firstPayloadBytes = 0;
  for (unsigned byte = 0; byte != 8 && firstHeaderReady; ++byte)
    firstPayloadBytes |= static_cast<std::uint64_t>(firstHeader[16 + byte]) <<
        (byte * 8);
  const std::uint64_t malformedOffset =
      firstHeader.size() + firstPayloadBytes + 24 + 6;
  const auto malformedPath = root / L"incremental-malformed-record.hgn";
  const bool malformedCopied = firstHeaderReady && CopyFileW(
      (preparedDirectory / L"records.hgn").c_str(), malformedPath.c_str(),
      FALSE) != FALSE;
  HANDLE malformedFile = malformedCopied
      ? CreateFileW(malformedPath.c_str(), GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL, nullptr)
      : INVALID_HANDLE_VALUE;
  std::uint8_t malformedByte = 0;
  std::uint32_t malformedRead = 0;
  bool malformedWritten = malformedFile != INVALID_HANDLE_VALUE &&
      ReadHandle(malformedFile, malformedOffset, &malformedByte, 1,
                 &malformedRead) && malformedRead == 1;
  malformedByte ^= 1;
  LARGE_INTEGER malformedWriteOffset{};
  malformedWriteOffset.QuadPart = static_cast<LONGLONG>(malformedOffset);
  DWORD malformedWriteBytes = 0;
  malformedWritten = malformedWritten && SetFilePointerEx(
      malformedFile, malformedWriteOffset, nullptr, FILE_BEGIN) != FALSE &&
      WriteFile(malformedFile, &malformedByte, 1, &malformedWriteBytes,
                nullptr) != FALSE && malformedWriteBytes == 1;
  CanonicalizationResult malformedPostResult{};
  const Error malformedPostError = malformedWritten
      ? CanonicalizeRecordStream(
            {{malformedFile, ReadHandle, FileBytes(malformedFile),
               StreamKind::Capture},
             &preparedBlobs, ReadBlobFixture, {}},
            &malformedPostResult)
      : Error::BadFlags;
  const CanonicalReplayProfile malformedIncremental =
      incrementalReplay(malformedOffset);
  const bool malformedParity = malformedWritten &&
      malformedPostError == Error::ReservedNonzero &&
      malformedIncremental.error == malformedPostError;
  if (malformedFile != INVALID_HANDLE_VALUE) CloseHandle(malformedFile);
  std::filesystem::remove(malformedPath, error);
  const double incrementalAggregateMs = replayMilliseconds(
      incrementalFirst.qpc + incrementalSecond.qpc);
  const double postWalkAggregateMs = greenReplayMs * 2.0;

  // Offline coordinator proxy: one exact 89,291-record local artifact build
  // and one deterministic equal-work traversal schedule. Separate handles
  // preserve independent cursors; the event starts both lanes without sleeps.
  HANDLE benchmarkFirst = CreateFileW(
      (preparedDirectory / L"records.hgn").c_str(), GENERIC_READ,
      FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
      FILE_ATTRIBUTE_NORMAL, nullptr);
  HANDLE benchmarkSecond = CreateFileW(
      (preparedDirectory / L"records.hgn").c_str(), GENERIC_READ,
      FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
      FILE_ATTRIBUTE_NORMAL, nullptr);
  LARGE_INTEGER serialStart{}, serialEnd{}, overlapStart{}, overlapEnd{};
  QueryPerformanceCounter(&serialStart);
  const CanonicalReplayProfile serialLocal =
      incrementalReplay(UINT64_MAX, benchmarkFirst);
  const CanonicalReplayProfile serialTraversal =
      incrementalReplay(UINT64_MAX, benchmarkSecond);
  QueryPerformanceCounter(&serialEnd);
  HANDLE workerReady = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  HANDLE overlapStartEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  CanonicalReplayProfile overlapLocal;
  QueryPerformanceCounter(&overlapStart);
  std::thread overlapWorker([&]() noexcept {
    static_cast<void>(SetEvent(workerReady));
    if (WaitForSingleObject(overlapStartEvent, INFINITE) == WAIT_OBJECT_0)
      overlapLocal = incrementalReplay(UINT64_MAX, benchmarkFirst);
  });
  const bool overlapScheduled =
      WaitForSingleObject(workerReady, INFINITE) == WAIT_OBJECT_0 &&
      SetEvent(overlapStartEvent) != FALSE;
  const CanonicalReplayProfile overlapTraversal =
      incrementalReplay(UINT64_MAX, benchmarkSecond);
  overlapWorker.join();
  QueryPerformanceCounter(&overlapEnd);
  if (workerReady != nullptr) CloseHandle(workerReady);
  if (overlapStartEvent != nullptr) CloseHandle(overlapStartEvent);
  if (benchmarkFirst != INVALID_HANDLE_VALUE) CloseHandle(benchmarkFirst);
  if (benchmarkSecond != INVALID_HANDLE_VALUE) CloseHandle(benchmarkSecond);
  const double coordinatorSerialMs = replayMilliseconds(
      static_cast<std::uint64_t>(serialEnd.QuadPart - serialStart.QuadPart));
  const double coordinatorOverlapMs = replayMilliseconds(
      static_cast<std::uint64_t>(overlapEnd.QuadPart - overlapStart.QuadPart));
  const double coordinatorReductionMs =
      coordinatorSerialMs - coordinatorOverlapMs;
  const double coordinatorOverlapRatio = coordinatorSerialMs <= 0.0
      ? 0.0 : coordinatorReductionMs / coordinatorSerialMs;
  const bool coordinatorAuthority = overlapScheduled &&
      serialLocal.error == Error::None &&
      serialTraversal.error == Error::None &&
      overlapLocal.error == Error::None &&
      overlapTraversal.error == Error::None &&
      Equal(serialLocal.result.recordStream.digest,
            overlapLocal.result.recordStream.digest) &&
      Equal(serialTraversal.result.captureRoot,
            overlapTraversal.result.captureRoot);
  std::wcout << L"CODEC_CANONICAL_89291_DESCRIPTOR_RED_GREEN authority="
             << replayAuthorityEqual << L" ceiling=" << descriptorCeiling
             << L" records=" << kRecords
             << L" red_field_walks="
             << redReplay.counters.fieldDecodeWalks
             << L" green_field_walks="
             << greenReplay.counters.fieldDecodeWalks
             << L" descriptor_fields="
             << greenReplay.counters.descriptorFields
             << L" descriptor_growths="
             << greenReplay.counters.descriptorArenaGrowths
             << L" red_ms=" << redReplayMs
             << L" green_ms=" << greenReplayMs
             << L" reduction_ms=" << (redReplayMs - greenReplayMs)
             << L'\n'
             << L"CODEC_CANONICAL_89291_STAGE_QPC walk="
             << greenReplay.counters.canonicalWalkQpc
             << L" authority=" << greenReplay.counters.authorityPassQpc
             << L" semantic=" << greenReplay.counters.semanticPassQpc
             << L'\n'
             << L"CODEC_CANONICAL_89291_INCREMENTAL authority="
             << incrementalAuthority << L" zero_post_walks="
             << zeroPostWalkCeiling << L" finalizations="
             << incrementalCounters.incrementalAccumulatorFinalizations
             << L" retained_record_copies="
             << incrementalCounters.retainedRecordCopies
             << L" malformed_boundary_parity=" << malformedParity
             << L" malformed_error="
             << static_cast<unsigned>(malformedIncremental.error)
             << L" post_aggregate_ms=" << postWalkAggregateMs
             << L" incremental_aggregate_ms=" << incrementalAggregateMs
             << L" reduction_ms="
             << (postWalkAggregateMs - incrementalAggregateMs) << L'\n'
             << L"CODEC_CANONICAL_89291_COORDINATOR_OVERLAP authority="
             << coordinatorAuthority << L" serial_ms=" << coordinatorSerialMs
             << L" overlap_ms=" << coordinatorOverlapMs
             << L" reduction_ms=" << coordinatorReductionMs
             << L" overlap_ratio=" << coordinatorOverlapRatio
             << L" measured_ceiling_ms=816.3585 live_claim=0\n";
  PublicationInput preparedInput = preparedReady && replayAuthorityEqual &&
          descriptorCeiling && zeroPostWalkCeiling && malformedParity &&
          coordinatorAuthority
      ? Publication(preparedRecords, preparedIndex, 2, kControls,
                    &preparedBlobs)
      : PublicationInput{};
  const CanonicalizationResult *preparedFacts =
      CanonicalArtifactFacts(preparedInput.canonicalArtifact);
  auto candidate = std::make_shared<PreparedGenerationCandidate>();
  Sha256 recordsDigest{}, indexDigest{}, environmentDigest{}, blobDigest{};
  preparedReady = preparedReady && preparedFacts != nullptr &&
      preparedFacts->recordStream.itemCount == kRecords &&
      preparedFacts->blobClosure.size() == kBlobs &&
      !preparedFacts->requiresActiveLineage &&
      HashWholeFile(preparedRecords, &recordsDigest) &&
      HashWholeFile(preparedIndex, &indexDigest) &&
      HashWholeFile(environment, &environmentDigest) &&
      HashWholeFile(blobCache, &blobDigest) &&
      BindPreparedArtifact(preparedRecords, recordsDigest,
                           &candidate->records) &&
      BindPreparedArtifact(preparedIndex, indexDigest, &candidate->index) &&
      BindPreparedArtifact(environment, environmentDigest,
                           &candidate->environment) &&
      BindPreparedArtifact(blobCache, blobDigest, &candidate->blobs);
  candidate->directory = preparedDirectory.wstring();
  candidate->owner = owner;
  preparedInput.preparedCandidate = candidate;
  preparedInput.layoutEnvironment = {};
  ResetCodecDebugCounters();
  ResetPublicationDebugCounters();
  ResetGenerationAuthenticationDebugCount();
  FILETIME creation{}, exit{}, kernelBefore{}, userBefore{}, kernelAfter{},
      userAfter{};
  LARGE_INTEGER frequency{}, started{}, finished{};
  QueryPerformanceFrequency(&frequency);
  GetProcessTimes(GetCurrentProcess(), &creation, &exit, &kernelBefore,
                  &userBefore);
  QueryPerformanceCounter(&started);
  const bool preparedPublished = preparedReady && preparedStore.Publish(preparedInput);
  QueryPerformanceCounter(&finished);
  GetProcessTimes(GetCurrentProcess(), &creation, &exit, &kernelAfter,
                  &userAfter);
  const auto fileTimeValue = [](const FILETIME &value) {
    return (static_cast<std::uint64_t>(value.dwHighDateTime) << 32) |
        value.dwLowDateTime;
  };
  const double wallMs = frequency.QuadPart == 0 ? 0.0 :
      1000.0 * static_cast<double>(finished.QuadPart - started.QuadPart) /
          static_cast<double>(frequency.QuadPart);
  const double cpuMs = static_cast<double>(
      fileTimeValue(kernelAfter) - fileTimeValue(kernelBefore) +
      fileTimeValue(userAfter) - fileTimeValue(userBefore)) / 10000.0;
  const PublicationDebugCounters publicationCounters =
      ReadPublicationDebugCounters();
  const CodecDebugCounters codecCounters = ReadCodecDebugCounters();
  const std::uint64_t authenticationCount =
      ReadGenerationAuthenticationDebugCount();

  const GenerationPin genericGeneration = genericStore.PinActive();
  const GenerationPin preparedGeneration = preparedStore.PinActive();
  const bool fileParity = genericPublished && preparedPublished &&
      genericGeneration && preparedGeneration &&
      FilesEqual(std::filesystem::path(genericGeneration->Path()) /
                     L"records.hgn",
                 std::filesystem::path(preparedGeneration->Path()) /
                     L"records.hgn") &&
      FilesEqual(std::filesystem::path(genericGeneration->Path()) /
                     L"index.hgi",
                 std::filesystem::path(preparedGeneration->Path()) /
                     L"index.hgi") &&
      FilesEqual(std::filesystem::path(genericGeneration->Path()) /
                     L"environment.hge",
                 std::filesystem::path(preparedGeneration->Path()) /
                     L"environment.hge") &&
      FilesEqual(std::filesystem::path(genericGeneration->Path()) /
                     L"blobs.hgb",
                 std::filesystem::path(preparedGeneration->Path()) /
                     L"blobs.hgb");
  const bool rootVersionParity = genericFacts != nullptr &&
      preparedFacts != nullptr &&
      Equal(genericFacts->observedSemanticRoot,
            preparedFacts->observedSemanticRoot) &&
      Equal(genericFacts->layoutRoot, preparedFacts->layoutRoot) &&
      Equal(genericFacts->captureRoot, preparedFacts->captureRoot) &&
      genericFacts->closedProfileBits == preparedFacts->closedProfileBits &&
      genericFacts->semanticCertified == preparedFacts->semanticCertified;
  query::GraphStoreGenerationSource genericStoreSource(&genericStore);
  query::GraphStoreGenerationSource preparedStoreSource(&preparedStore);
  query::GenerationSnapshot genericSnapshot{}, preparedSnapshot{};
  const bool snapshotsPinned = genericStoreSource.Pin(&genericSnapshot) &&
      preparedStoreSource.Pin(&preparedSnapshot);
  if (snapshotsPinned) preparedSnapshot.key = genericSnapshot.key;
  FixedGenerationSource genericSourceAdapter(std::move(genericSnapshot));
  FixedGenerationSource preparedSourceAdapter(std::move(preparedSnapshot));
  query::Query queryRequest{};
  queryRequest.axis = query::Axis::DocumentOrder;
  queryRequest.projectionBits = query::ProjectStructure;
  query::QueryView genericView{}, preparedView{};
  const query::Status genericQueryStatus =
      query::BuildQueryView(&genericSourceAdapter, queryRequest, &genericView);
  const query::Status preparedQueryStatus =
      query::BuildQueryView(&preparedSourceAdapter, queryRequest, &preparedView);
  const bool queryParity = snapshotsPinned &&
      genericQueryStatus == query::Status::Terminal &&
      preparedQueryStatus == query::Status::Terminal &&
      Equal(genericView.canonical.observedSemanticRoot,
            preparedView.canonical.observedSemanticRoot) &&
      genericView.canonical.recordStream.itemCount ==
          preparedView.canonical.recordStream.itemCount &&
      genericView.records.size() == preparedView.records.size() &&
      genericView.records == preparedView.records;
  query::QueryView rangeView{};
  const auto rangeStarted = std::chrono::steady_clock::now();
  const query::Status rangeStatus = query::BuildQueryPlan(
      &preparedSourceAdapter, queryRequest, &rangeView);
  const double rangeOpenMs = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - rangeStarted).count();
  Bytes rangeBytes;
  const auto rangeDrainStarted = std::chrono::steady_clock::now();
  bool rangeMaterialized = rangeStatus == query::Status::Terminal &&
      rangeView.records.empty() && rangeView.rangePlan != nullptr;
  if (rangeMaterialized) {
    rangeBytes.reserve(static_cast<std::size_t>(rangeView.logicalBytes));
    for (std::uint64_t ordinal = 0; ordinal != rangeView.logicalRecords;
         ++ordinal) {
      Bytes record;
      if (!query::MaterializeQueryPlanRecord(rangeView, ordinal, &record)) {
        rangeMaterialized = false;
        break;
      }
      rangeBytes.insert(rangeBytes.end(), record.begin(), record.end());
    }
  }
  const double rangeDrainMs = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - rangeDrainStarted).count();
  const auto unavoidableCopyStarted = std::chrono::steady_clock::now();
  Bytes unavoidableCopy = preparedView.records;
  const double unavoidableCopyMs = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - unavoidableCopyStarted).count();
  const double rangeAddedMs = (std::max)(0.0, rangeDrainMs - unavoidableCopyMs);
  const bool rangeParity = rangeMaterialized &&
      rangeOpenMs <= 100.0 && rangeAddedMs <= 100.0 &&
      unavoidableCopy == preparedView.records &&
      rangeView.logicalBytes == preparedView.records.size() &&
      rangeView.logicalRecords == preparedView.canonical.recordStream.itemCount &&
      rangeBytes == preparedView.records;
  const bool counters = publicationCounters.preparedPromotions == 1 &&
      publicationCounters.manifestWrites == 1 &&
      publicationCounters.manifestFlushes == 1 &&
      publicationCounters.commitWrites == 1 &&
      publicationCounters.commitFlushes == 1 &&
      publicationCounters.destinationFullFileRehashes == 0 &&
      publicationCounters.blobCopyReplays == 0 &&
      publicationCounters.environmentCopyReplays == 0 &&
      publicationCounters.lastPreparedStage == 7 &&
      codecCounters.publishValidationWalks == 0 &&
      codecCounters.publishCanonicalWalks == 0 &&
      codecCounters.publishRecordCopyPasses == 0 &&
      codecCounters.publishBlobClosureReplays == 0 &&
      authenticationCount == 0;
  std::wcout << L"STORE_EXACT_PREPARED_FIXTURE records=" << kRecords
             << L" blobs=" << kBlobs << L" fresh_lineage="
             << exactFixture << L"\nSTORE_EXACT_PREPARED_TIMING wall_ms="
             << wallMs << L" cpu_ms=" << cpuMs
             << L" source_bytes="
             << (preparedFacts == nullptr ? 0 :
                 preparedFacts->recordStream.byteLength)
             << L" blob_bytes=" << (kBlobs * 30) << L'\n'
             << L"STORE_EXACT_PREPARED_COUNTERS promotions="
             << publicationCounters.preparedPromotions << L" manifest="
             << publicationCounters.manifestWrites << L'/'
             << publicationCounters.manifestFlushes << L" commit="
             << publicationCounters.commitWrites << L'/'
             << publicationCounters.commitFlushes << L" canonical="
             << codecCounters.publishCanonicalWalks << L" lineage="
             << codecCounters.publishValidationWalks << L" env_replay="
             << publicationCounters.environmentCopyReplays << L" blob_replay="
             << publicationCounters.blobCopyReplays << L" destination_rehash="
             << publicationCounters.destinationFullFileRehashes << L" auth="
             << authenticationCount << L" stage="
             << publicationCounters.lastPreparedStage << L'\n'
             << L"STORE_EXACT_PREPARED_PARITY files=" << fileParity
             << L" roots_version=" << rootVersionParity << L" query="
             << queryParity << L" query_status="
             << static_cast<unsigned>(genericQueryStatus) << L'/'
             << static_cast<unsigned>(preparedQueryStatus)
             << L" query_bytes=" << genericView.records.size() << L'/'
             << preparedView.records.size() << L'\n'
             << L"STORE_RANGE_QUERY_PLAN parity=" << rangeParity
             << L" open_ms=" << rangeOpenMs
             << L" records=" << rangeView.logicalRecords
             << L" bytes=" << rangeView.logicalBytes
             << L" eager_bytes_at_open=" << rangeView.records.size()
             << L" drain_ms=" << rangeDrainMs
             << L" unavoidable_copy_ms=" << unavoidableCopyMs
             << L" added_ms=" << rangeAddedMs << L'\n';
  if (genericRecords != INVALID_HANDLE_VALUE) CloseHandle(genericRecords);
  if (genericIndex != INVALID_HANDLE_VALUE) CloseHandle(genericIndex);
  return exactFixture && genericPublished && preparedPublished &&
      wallMs <= 350.0 && fileParity && rootVersionParity && queryParity &&
      rangeParity && counters;
}

std::size_t CandidateCount(const std::filesystem::path &root) {
  std::error_code error;
  std::size_t count = 0;
  for (std::filesystem::directory_iterator item(root, error);
       !error && item != std::filesystem::directory_iterator();
       item.increment(error))
    if (item->path().filename().wstring().rfind(L"candidate-", 0) == 0)
      ++count;
  return count;
}
bool ResealManifest(const std::filesystem::path &manifestPath) {
  constexpr DWORD manifestBytes = 580;
  std::array<std::uint8_t, manifestBytes> bytes{};
  SetFileAttributesW(manifestPath.c_str(), FILE_ATTRIBUTE_NORMAL);
  HANDLE file = CreateFileW(manifestPath.c_str(), GENERIC_READ | GENERIC_WRITE,
                            FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                            FILE_ATTRIBUTE_NORMAL, nullptr);
  DWORD actual = 0;
  bool ok = file != INVALID_HANDLE_VALUE &&
            ReadFile(file, bytes.data(), manifestBytes, &actual, nullptr) &&
            actual == manifestBytes;
  if (ok) {
    Bytes integrity(bytes.begin(), bytes.begin() + 540);
    integrity.insert(integrity.end(), bytes.end() - 8, bytes.end());
    const Sha256 digest = Hash(View(integrity));
    LARGE_INTEGER offset{};
    offset.QuadPart = 540;
    DWORD written = 0;
    ok =
        SetFilePointerEx(file, offset, nullptr, FILE_BEGIN) &&
        WriteFile(file, digest.bytes.data(),
                  static_cast<DWORD>(digest.bytes.size()), &written, nullptr) &&
        written == digest.bytes.size() && FlushFileBuffers(file);
  }
  if (file != INVALID_HANDLE_VALUE)
    CloseHandle(file);
  SetFileAttributesW(manifestPath.c_str(), FILE_ATTRIBUTE_READONLY);
  const auto commitPath = manifestPath.parent_path() / L"commit.hgc";
  if (ok && std::filesystem::exists(commitPath)) {
    SetFileAttributesW(commitPath.c_str(), FILE_ATTRIBUTE_NORMAL);
    HANDLE commit = CreateFileW(
        commitPath.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    Bytes marker{'H', 'G', 'C', '1'};
    marker.insert(marker.end(), {1, 0, 0, 0});
    marker.insert(marker.end(), bytes.begin() + 8, bytes.begin() + 16);
    marker.insert(marker.end(), bytes.begin() + 540, bytes.begin() + 572);
    DWORD written = 0;
    ok = commit != INVALID_HANDLE_VALUE && marker.size() == 48 &&
        WriteFile(commit, marker.data(), static_cast<DWORD>(marker.size()),
                  &written, nullptr) &&
        written == marker.size() && FlushFileBuffers(commit);
    if (commit != INVALID_HANDLE_VALUE) CloseHandle(commit);
    SetFileAttributesW(commitPath.c_str(), FILE_ATTRIBUTE_READONLY);
  }
  return ok;
}
bool WriteHighWaterFixture(const std::filesystem::path &path,
                           const std::uint64_t serial,
                           const bool validChecksum = true) {
  Bytes bytes{'H', 'G', 'R', '1', 1, 0, 1, 0};
  const Bytes serialBytes = Uint64(serial);
  bytes.insert(bytes.end(), serialBytes.begin(), serialBytes.end());
  bytes.insert(bytes.end(), 8, 0);
  Sha256 checksum = Hash(View(bytes));
  if (!validChecksum) checksum.bytes[0] ^= 0x80;
  bytes.insert(bytes.end(), checksum.bytes.begin(), checksum.bytes.end());
  HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  DWORD written = 0;
  const bool ok = file != INVALID_HANDLE_VALUE && bytes.size() == 56 &&
      WriteFile(file, bytes.data(), static_cast<DWORD>(bytes.size()),
                &written, nullptr) && written == bytes.size() &&
      FlushFileBuffers(file);
  if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  return ok;
}

bool WriteLegacyJournalFixture(const std::filesystem::path &root,
                               const std::vector<std::uint64_t> &serials) {
  Bytes journal;
  Sha256 previous{};
  for (const std::uint64_t serial : serials) {
    Bytes entry{'H', 'G', 'J', '1', 1, 0, 1, 0};
    const Bytes serialBytes = Uint64(serial);
    entry.insert(entry.end(), serialBytes.begin(), serialBytes.end());
    entry.insert(entry.end(), previous.bytes.begin(), previous.bytes.end());
    const Sha256 checksum = Hash(View(entry));
    entry.insert(entry.end(), checksum.bytes.begin(), checksum.bytes.end());
    previous = Hash(View(entry));
    journal.insert(journal.end(), entry.begin(), entry.end());
  }
  Bytes checkpoint{'H', 'G', 'R', '2', 2, 0, 1, 0};
  const Bytes highWater = Uint64(serials.back());
  checkpoint.insert(checkpoint.end(), highWater.begin(), highWater.end());
  checkpoint.insert(checkpoint.end(), previous.bytes.begin(), previous.bytes.end());
  const Sha256 checkpointChecksum = Hash(View(checkpoint));
  checkpoint.insert(checkpoint.end(), checkpointChecksum.bytes.begin(),
                    checkpointChecksum.bytes.end());
  const auto write = [](const std::filesystem::path &path,
                        const Bytes &bytes) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD written = 0;
    const bool ok = file != INVALID_HANDLE_VALUE &&
        WriteFile(file, bytes.data(), static_cast<DWORD>(bytes.size()),
                  &written, nullptr) && written == bytes.size() &&
        FlushFileBuffers(file);
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    return ok;
  };
  return !serials.empty() && write(root / L"reservations.hgj", journal) &&
      write(root / L"high-water.hgr", checkpoint);
}

Sha256 FileSnapshot(const std::filesystem::path &path) {
  HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ,
                            nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  const std::uint64_t length = file == INVALID_HANDLE_VALUE ? 0 : FileBytes(file);
  Sha256 digest{};
  if (file != INVALID_HANDLE_VALUE)
    HashFileRangeChunked(file, 0, length, &digest);
  if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  return digest;
}

Sha256 TreeSnapshot(const std::filesystem::path &root) {
  Bytes inventory;
  std::error_code error;
  std::vector<std::filesystem::path> paths;
  for (std::filesystem::recursive_directory_iterator item(root, error), end;
       !error && item != end; item.increment(error))
    if (item->is_regular_file(error) &&
        item->path().filename() != L"high-water.lock")
      paths.push_back(item->path());
  std::sort(paths.begin(), paths.end());
  for (const auto &path : paths) {
    const std::string name = path.lexically_relative(root).generic_string();
    inventory.insert(inventory.end(), name.begin(), name.end());
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    const std::uint64_t length = file == INVALID_HANDLE_VALUE ? 0 : FileBytes(file);
    Bytes bytes(static_cast<std::size_t>(length));
    DWORD read = 0;
    if (file != INVALID_HANDLE_VALUE && length <= MAXDWORD)
      ReadFile(file, bytes.data(), static_cast<DWORD>(bytes.size()), &read,
               nullptr);
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    inventory.insert(inventory.end(), bytes.begin(), bytes.end());
  }
  return Hash(View(inventory));
}

bool ReadPrior(const std::wstring &generation) {
  const auto records = std::filesystem::path(generation) / L"records.hgn";
  HANDLE file =
      CreateFileW(records.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  std::array<std::uint8_t, 4> magic{};
  DWORD read = 0;
  const bool ok = file != INVALID_HANDLE_VALUE &&
                  ReadFile(file, magic.data(), static_cast<DWORD>(magic.size()),
                           &read, nullptr) != FALSE &&
                  read == magic.size();
  if (file != INVALID_HANDLE_VALUE)
    CloseHandle(file);
  return ok;
}

struct PinnedGenerationSnapshot final {
  std::uint64_t recordsBytes = 0;
  Sha256 recordsHash{};
  std::uint64_t indexBytes = 0;
  Sha256 indexHash{};
  Sha256 observedSemanticRoot{};
  Sha256 captureRoot{};
  std::vector<std::wstring> publishedFiles;
};

std::uint64_t ReadLittleEndian64(const std::uint8_t *bytes) {
  std::uint64_t value = 0;
  for (unsigned index = 0; index != 8; ++index)
    value |= std::uint64_t{bytes[index]} << (index * 8);
  return value;
}

bool ReadPinnedManifestRoots(HANDLE file, Sha256 *observedSemanticRoot,
                             Sha256 *captureRoot) {
  std::array<std::uint8_t, 24> header{};
  std::uint32_t actual = 0;
  if (!ReadHandle(file, 0, header.data(), static_cast<std::uint32_t>(header.size()),
                  &actual) ||
      actual != header.size())
    return false;
  const std::uint64_t payloadBytes = ReadLittleEndian64(header.data() + 16);
  if (payloadBytes > MAXDWORD - header.size())
    return false;
  Bytes manifest(static_cast<std::size_t>(header.size() + payloadBytes));
  if (!ReadHandle(file, 0, manifest.data(), static_cast<std::uint32_t>(manifest.size()),
                  &actual) ||
      actual != manifest.size())
    return false;

  std::array<FieldTag, 6> manifestTags{};
  std::size_t manifestTagCount = 0;
  for (const RecordFieldRule &rule : kRecordFieldRules) {
    if (rule.record == RecordKind::Manifest)
      manifestTags[manifestTagCount++] = rule.tag;
  }
  if (manifestTagCount != manifestTags.size() ||
      ValidateLogicalRecord(View(manifest), manifestTags.data(),
                            manifestTagCount, 0) != Error::None)
    return false;

  constexpr std::uint64_t kFieldHeaderBytes = 24;
  constexpr std::uint64_t kObservedSemanticRootOffset = 72;
  constexpr std::uint64_t kCaptureRootOffset = 136;
  std::uint64_t at = header.size();
  const std::uint16_t fieldCount = static_cast<std::uint16_t>(
      header[6] | (std::uint16_t{header[7]} << 8));
  for (std::uint16_t index = 0; index != fieldCount; ++index) {
    if (at > manifest.size() || manifest.size() - at < kFieldHeaderBytes)
      return false;
    const std::uint8_t *field = manifest.data() + at;
    const FieldTag tag = static_cast<FieldTag>(
        field[0] | (std::uint16_t{field[1]} << 8));
    const std::uint64_t valueBytes = ReadLittleEndian64(field + 16);
    at += kFieldHeaderBytes;
    if (at > manifest.size() || manifest.size() - at < valueBytes)
      return false;
    if (tag == 1) {
      if (valueBytes != kGraphVersionBytesV1 ||
          kCaptureRootOffset + captureRoot->bytes.size() > valueBytes)
        return false;
      std::copy_n(manifest.data() + at + kObservedSemanticRootOffset,
                  observedSemanticRoot->bytes.size(),
                  observedSemanticRoot->bytes.begin());
      std::copy_n(manifest.data() + at + kCaptureRootOffset,
                  captureRoot->bytes.size(), captureRoot->bytes.begin());
      return true;
    }
    at += valueBytes;
  }
  return false;
}

bool ReadPinnedGenerationSnapshot(
    const std::wstring &generation,
    PinnedGenerationSnapshot *snapshot) {
  if (snapshot == nullptr)
    return false;
  *snapshot = {};
  const auto records = std::filesystem::path(generation) / L"records.hgn";
  HANDLE file = CreateFileW(records.c_str(), GENERIC_READ, FILE_SHARE_READ,
                            nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  if (file == INVALID_HANDLE_VALUE)
    return false;
  snapshot->recordsBytes = FileBytes(file);
  const bool recordsOk = snapshot->recordsBytes != 0 &&
      HashFileRangeChunked(file, 0, snapshot->recordsBytes,
                           &snapshot->recordsHash) &&
      ReadPinnedManifestRoots(file, &snapshot->observedSemanticRoot,
                              &snapshot->captureRoot);
  CloseHandle(file);
  const auto index = std::filesystem::path(generation) / L"index.hgi";
  file = CreateFileW(index.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                     OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (!recordsOk || file == INVALID_HANDLE_VALUE)
    return false;
  snapshot->indexBytes = FileBytes(file);
  const bool indexOk = snapshot->indexBytes != 0 &&
      HashFileRangeChunked(
          file, 0, snapshot->indexBytes, &snapshot->indexHash);
  CloseHandle(file);
  std::error_code error;
  for (const std::filesystem::directory_entry& entry :
       std::filesystem::directory_iterator(generation, error)) {
    if (entry.is_regular_file(error))
      snapshot->publishedFiles.push_back(entry.path().filename().wstring());
    if (error)
      return false;
  }
  std::sort(
      snapshot->publishedFiles.begin(), snapshot->publishedFiles.end());
  return indexOk && !snapshot->publishedFiles.empty();
}

bool DeleteSmokeTree(const std::filesystem::path& root) {
  std::error_code error;
  if (!std::filesystem::exists(root, error))
    return !error;
  for (std::filesystem::recursive_directory_iterator it(
           root, std::filesystem::directory_options::skip_permission_denied,
           error), end;
       !error && it != end; it.increment(error)) {
    SetFileAttributesW(it->path().c_str(), FILE_ATTRIBUTE_NORMAL);
  }
  if (error)
    return false;
  SetFileAttributesW(root.c_str(), FILE_ATTRIBUTE_NORMAL);
  std::filesystem::remove_all(root, error);
  return !error && !std::filesystem::exists(root);
}

struct ConstrainedStoreContext final {
  GraphStore* store = nullptr;
  PublicationInput publication{};
  volatile LONG phase = 0;
};

bool RunConstrainedStorePath(ConstrainedStoreContext* const context) {
  InterlockedExchange(&context->phase, 2);
  if (!context->store->Publish(context->publication)) return false;
  InterlockedExchange(&context->phase, 3);
  AuthenticatedGenerationPin authenticated;
  return AuthenticateGeneration(context->store->PinActive(), &authenticated) &&
      authenticated != nullptr;
}

DWORD WINAPI ConstrainedStoreThread(void* const rawContext) {
  ULONG stackGuarantee = 64U * 1024U;
  static_cast<void>(SetThreadStackGuarantee(&stackGuarantee));
  __try {
    return RunConstrainedStorePath(
        static_cast<ConstrainedStoreContext*>(rawContext)) ? 0U : 1U;
  } __except (GetExceptionCode() == EXCEPTION_STACK_OVERFLOW
                  ? EXCEPTION_EXECUTE_HANDLER
                  : EXCEPTION_CONTINUE_SEARCH) {
    const auto* const context =
        static_cast<const ConstrainedStoreContext*>(rawContext);
    return static_cast<DWORD>(10 + context->phase);
  }
}

bool ConstrainedStorePathSmoke(const std::filesystem::path& root,
                               const PublicationInput& publication) {
  GraphStore store(root.wstring());
  if (!store.Initialize()) return false;
  ConstrainedStoreContext context{&store, publication, 0};
  HANDLE thread = CreateThread(
      nullptr, 640U * 1024U, ConstrainedStoreThread, &context,
      STACK_SIZE_PARAM_IS_A_RESERVATION, nullptr);
  if (thread == nullptr) return false;
  const DWORD wait = WaitForSingleObject(thread, INFINITE);
  DWORD result = UINT32_MAX;
  const bool completed = wait == WAIT_OBJECT_0 &&
      GetExitCodeThread(thread, &result) != FALSE;
  CloseHandle(thread);
  std::wcout << L"STORE_CONSTRAINED_STACK_PATH exit=" << result << L'\n';
  return completed && result == 0;
}

bool StoreSmoke() {
  bool ok = true;
  wchar_t temporary[MAX_PATH]{};
  GetTempPathW(MAX_PATH, temporary);
  const std::filesystem::path tempRoot(temporary);
  const std::filesystem::path root =
      tempRoot /
      (L"hwp-graph-store-smoke-" + std::to_wstring(GetCurrentProcessId()));
  std::error_code error;
  std::vector<std::filesystem::path> abandoned;
  for (std::filesystem::directory_iterator item(tempRoot, error), end;
       !error && item != end; item.increment(error)) {
    if (item->is_directory(error) &&
        item->path().filename().wstring().rfind(
            L"hwp-graph-store-smoke-", 0) == 0) {
      abandoned.push_back(item->path());
    }
  }
  bool abandonedRemoved = !error;
  for (const auto& path : abandoned) {
    const bool removed = DeleteSmokeTree(path);
    std::wcout << L"CODEC_STORE_ABANDONED_ROOT_REMOVED "
               << path.wstring() << L" " << removed << L'\n';
    abandonedRemoved = removed && abandonedRemoved;
  }
  ok = Expect(abandonedRemoved,
              L"attribute-normalized cleanup of prior test-owned roots") && ok;
  ok = Expect(DeleteSmokeTree(root), L"remove prior test-owned store root") &&
       ok;
  std::filesystem::create_directories(root, error);
  HANDLE records = INVALID_HANDLE_VALUE, index = INVALID_HANDLE_VALUE;
  ok = Expect(CreateScaleSpool(root / L"scale-records.bin",
                               root / L"scale-index.bin", &records, &index,
                               100001),
              L"100001 actual control records") &&
       ok;
  const std::uint64_t scaleFixtureBytes = FileBytes(records);
  std::uint64_t scaleValidatedRecords = 0;
  bool scalePublished = false;
  {
    GraphStore scaleStore((root / L"scale-store").wstring());
    const Error validation = ValidateRecordStream(
        {records, ReadHandle, scaleFixtureBytes, StreamKind::Capture},
        &scaleValidatedRecords);
    if (validation != Error::None)
      std::wcerr << L"scale validation error="
                 << static_cast<unsigned>(validation) << L" records="
                 << scaleValidatedRecords << L'\n';
    scalePublished = validation == Error::None &&
        scaleValidatedRecords > 100000 &&
        scaleFixtureBytes > UINT64_C(100000000) && scaleStore.Initialize() &&
        scaleStore.Publish(Publication(records, index, 1, 100001));
    ok = Expect(
             scalePublished,
             L"publish actual >100k controls despite legacy HWPML failure") &&
         ok;
  }
  if (records != INVALID_HANDLE_VALUE) CloseHandle(records);
  if (index != INVALID_HANDLE_VALUE) CloseHandle(index);
  records = INVALID_HANDLE_VALUE;
  index = INVALID_HANDLE_VALUE;
  ok = Expect(DeleteSmokeTree(root), L"reset after isolated scale case") && ok;
  std::filesystem::create_directories(root, error);
  ok = Expect(ExactPreparedPublicationSmoke(root),
              L"exact 89291-record/651-blob prepared publication parity and budget") &&
       ok;
  ok = Expect(DeleteSmokeTree(root), L"reset after exact prepared case") && ok;
  std::filesystem::create_directories(root, error);
  error.clear();
  ok = Expect(CreateScaleSpool(root / L"source-records.bin",
                               root / L"source-index.bin", &records, &index,
                               1),
              L"compact valid store regression source") &&
       ok;
  const std::uint64_t matrixFixtureBytes = FileBytes(records);
  const bool constrainedStackPath = ConstrainedStorePathSmoke(
      root / L"constrained-stack-store", Publication(records, index, 1));
  ok = Expect(constrainedStackPath,
              L"store publish/authentication completes on constrained stack") &&
       ok;
  const bool scaleFixtureIsolated = scalePublished &&
      scaleValidatedRecords > 100000 &&
      scaleFixtureBytes > UINT64_C(100000000) &&
      matrixFixtureBytes != 0 && matrixFixtureBytes < UINT64_C(1000000);
  std::wcout << L"STORE_SCALE_FIXTURE_ISOLATED " << scaleFixtureIsolated
             << L" large_publications=1 matrix_large_publications=0"
             << L" large_bytes=" << scaleFixtureBytes
             << L" matrix_bytes=" << matrixFixtureBytes << L'\n';
  ok = Expect(
           scaleFixtureIsolated,
           L"large fixture executes once and unrelated matrix uses compact "
           L"structurally valid input") &&
       ok;
  {
    GraphStore store(root.wstring());
    ok = Expect(store.Initialize(), L"initialize empty store") && ok;
    PublicationInput input = Publication(records, index, 1);
    ok = Expect(store.Publish(input), L"publish compact baseline") && ok;

    const auto lineagePositiveRoot = root / L"lineage-positive";
    GraphStore lineageStore(lineagePositiveRoot.wstring());
    const bool lineageBasePublished = lineageStore.Initialize() &&
        lineageStore.Publish(Publication(records, index, 1));
    const Uuid128 lineageDocument = ReceiptUuid(1);
    const Uuid128 lineageSource = ReceiptUuid(8);
    const Uuid128 lineageFirstTarget = ReceiptUuid(9);
    const Uuid128 lineageSecondTarget = ReceiptUuid(10);
    Bytes lineageGroupBytes = ManifestRecord(11);
    for (const Bytes& record : std::array<Bytes, 10>{
             NodeRecord(1, NodeKind::Document, lineageDocument, nullptr, 0),
             EdgeRecord(2, EdgeKind::Contains, lineageDocument,
                        lineageSource, 0),
             EdgeRecord(3, EdgeKind::Contains, lineageDocument,
                        lineageFirstTarget, 1),
             EdgeRecord(4, EdgeKind::Contains, lineageDocument,
                        lineageSecondTarget, 2),
             NodeRecord(5, NodeKind::Story, lineageSource,
                        &lineageDocument, 0),
             NodeRecord(6, NodeKind::Story, lineageFirstTarget,
                        &lineageDocument, 1),
             NodeRecord(7, NodeKind::Story, lineageSecondTarget,
                        &lineageDocument, 2),
             RemapRecord(8, lineageSource, &lineageSource,
                         RemapDisposition::Retained,
                         RemapReason::TableCellSplit),
             RemapRecord(9, lineageSource, &lineageFirstTarget,
                         RemapDisposition::New,
                         RemapReason::TableCellSplit),
             RemapRecord(10, lineageSource, &lineageSecondTarget,
                         RemapDisposition::New,
                         RemapReason::TableCellSplit)})
      lineageGroupBytes.insert(
          lineageGroupBytes.end(), record.begin(), record.end());
    const auto lineageGroupPath = root / L"lineage-positive-group.bin";
    HANDLE lineageGroupFile = CreateFileW(
        lineageGroupPath.c_str(), GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
        nullptr);
    DWORD lineageGroupWritten = 0;
    Sha256 lineageIndexDigest{};
    bool lineageGroupReady = lineageGroupFile != INVALID_HANDLE_VALUE &&
        WriteFile(lineageGroupFile, lineageGroupBytes.data(),
                  static_cast<DWORD>(lineageGroupBytes.size()),
                  &lineageGroupWritten, nullptr) &&
        lineageGroupWritten == lineageGroupBytes.size() &&
        HashFileRangeChunked(
            index, 0, FileBytes(index), &lineageIndexDigest);
    LARGE_INTEGER lineageGroupOffset{};
    if (lineageGroupReady) {
      lineageGroupOffset.QuadPart = 329;
      lineageGroupReady = SetFilePointerEx(
              lineageGroupFile, lineageGroupOffset, nullptr, FILE_BEGIN) &&
          WriteFile(lineageGroupFile, lineageIndexDigest.bytes.data(),
                    static_cast<DWORD>(lineageIndexDigest.bytes.size()),
                    &lineageGroupWritten, nullptr) &&
          lineageGroupWritten == lineageIndexDigest.bytes.size();
    }
    CanonicalizationResult lineageGroupCanonical{};
    if (lineageGroupReady)
      lineageGroupReady = CanonicalizeRecordStream(
          {{lineageGroupFile, ReadHandle, FileBytes(lineageGroupFile),
            StreamKind::Capture}, nullptr, nullptr, {}},
          &lineageGroupCanonical) == Error::None;
    if (lineageGroupReady) {
      lineageGroupOffset.QuadPart = 58;
      const std::array<std::uint8_t, 2> flags{
          static_cast<std::uint8_t>(
              lineageGroupCanonical.semanticCertified),
          static_cast<std::uint8_t>(lineageGroupCanonical.layoutPresent)};
      lineageGroupReady = SetFilePointerEx(
              lineageGroupFile, lineageGroupOffset, nullptr, FILE_BEGIN) &&
          WriteFile(lineageGroupFile, flags.data(),
                    static_cast<DWORD>(flags.size()),
                    &lineageGroupWritten, nullptr) &&
          lineageGroupWritten == flags.size();
      for (const auto& rootFact : std::array<
               std::pair<std::uint64_t, const Sha256*>, 3>{
               {{120, &lineageGroupCanonical.observedSemanticRoot},
                {152, &lineageGroupCanonical.layoutRoot},
                {184, &lineageGroupCanonical.captureRoot}}}) {
        lineageGroupOffset.QuadPart =
            static_cast<LONGLONG>(rootFact.first);
        lineageGroupReady = lineageGroupReady && SetFilePointerEx(
            lineageGroupFile, lineageGroupOffset, nullptr, FILE_BEGIN) &&
            WriteFile(lineageGroupFile, rootFact.second->bytes.data(),
                      static_cast<DWORD>(rootFact.second->bytes.size()),
                      &lineageGroupWritten, nullptr) &&
            lineageGroupWritten == rootFact.second->bytes.size();
      }
      lineageGroupReady = lineageGroupReady &&
          FlushFileBuffers(lineageGroupFile);
    }
    const bool lineageGroupPublished = lineageGroupReady &&
        lineageBasePublished &&
        lineageStore.Publish(Publication(lineageGroupFile, index, 2)) &&
        lineageStore.ActiveSerial() == 2 &&
        CandidateCount(lineagePositiveRoot) == 0;
    std::wcout << L"GRAPH_STORE_TABLE_CELL_SPLIT_GROUP_ACTIVE_LINEAGE_PUBLISHED "
               << lineageGroupPublished << L'\n';
    ok = Expect(
             lineageGroupPublished,
             L"store publishes complete TableCellSplit group against active "
             L"source lineage") &&
         ok;
    if (lineageGroupFile != INVALID_HANDLE_VALUE)
      CloseHandle(lineageGroupFile);

    const std::uint64_t activeBeforeOrderReject = store.ActiveSerial();
    GenerationPin pinnedBeforeOrderReject = store.PinActive();
    const std::wstring pinnedPathBeforeOrderReject =
        pinnedBeforeOrderReject ? pinnedBeforeOrderReject->Path() : L"";
    PinnedGenerationSnapshot pinnedSnapshotBeforeOrderReject{};
    const bool pinnedSnapshotReady = pinnedBeforeOrderReject &&
        ReadPinnedGenerationSnapshot(
            pinnedPathBeforeOrderReject,
            &pinnedSnapshotBeforeOrderReject) &&
        Equal(pinnedSnapshotBeforeOrderReject.observedSemanticRoot,
              input.first.observedSemanticRoot) &&
        Equal(pinnedSnapshotBeforeOrderReject.captureRoot,
              input.first.captureRoot);
    const std::vector<Bytes> malformedCharacterShapeStreams{
        CharacterShapePersistedStream(1, false, DefinitionKind::CharacterShape,
                                      false, true, {}),
        CharacterShapePersistedStream(0, false, DefinitionKind::CharacterShape,
                                      false, true, {CoverageState::Complete}),
        CharacterShapePersistedStream(0, false, DefinitionKind::CharacterShape,
                                      false, false, {CoverageState::Complete}),
        CharacterShapePersistedStream(1, false, DefinitionKind::CharacterShape,
                                      false, false, {CoverageState::Complete}),
        CharacterShapePersistedStream(2, false, DefinitionKind::CharacterShape,
                                      false, false, {CoverageState::Complete}),
        CharacterShapePersistedStream(2, true, DefinitionKind::CharacterShape,
                                      false, false, {CoverageState::Complete}),
        CharacterShapePersistedStream(1, false, DefinitionKind::CharacterShape,
                                      true, false, {CoverageState::Complete}),
        CharacterShapePersistedStream(1, false, DefinitionKind::Style,
                                      false, false, {CoverageState::Complete}),
        CharacterShapePersistedStream(
            0, false, DefinitionKind::CharacterShape, false, false,
            {CoverageState::NotExposed, CoverageState::Complete}),
        CharacterShapePersistedStream(
            0, false, DefinitionKind::CharacterShape, false, false,
            {CoverageState::ReadFailed, CoverageState::Complete}),
        CharacterShapePersistedStream(0, false, DefinitionKind::CharacterShape,
                                      false, false,
                                      {CoverageState::NotRequested}),
        CharacterShapePersistedStream(0, false, DefinitionKind::CharacterShape,
                                      false, false,
                                      {CoverageState::NotApplicable}),
        CharacterShapePersistedStream(
            0, false, DefinitionKind::CharacterShape, false, false,
            {CoverageState::ReadFailed, CoverageState::NotExposed}),
        CharacterShapePersistedStream(0, false, DefinitionKind::CharacterShape,
                                      false, true,
                                      {CoverageState::NotExposed}),
        CharacterShapePersistedStream(1, false, DefinitionKind::CharacterShape,
                                      false, false,
                                      {CoverageState::NotExposed}),
        CharacterShapePersistedStream(1, false, DefinitionKind::CharacterShape,
                                      false, true,
                                      {CoverageState::NotExposed}),
        CharacterShapePersistedStream(
            1, false, DefinitionKind::CharacterShape, false, true,
            {CoverageState::Complete, CoverageState::Complete}),
        CharacterShapePersistedStream(
            0, false, DefinitionKind::CharacterShape, false, false,
            {CoverageState::NotExposed, CoverageState::NotExposed}),
        CharacterShapePersistedStream(
            0, false, DefinitionKind::CharacterShape, false, false,
            {CoverageState::ReadFailed, CoverageState::ReadFailed}),
        CharacterShapePersistedStream(
            1, false, DefinitionKind::CharacterShape, false, true,
            {CoverageState::ProjectionOmitted, CoverageState::Complete}),
    };
    Sha256 activeIndexDigest{};
    bool atomicCharacterShapeMatrix = pinnedSnapshotReady &&
        HashFileRangeChunked(
            index, 0, FileBytes(index), &activeIndexDigest);
    for (size_t caseIndex = 0;
         atomicCharacterShapeMatrix &&
         caseIndex < malformedCharacterShapeStreams.size(); ++caseIndex) {
        const auto candidatePath = root /
            (L"malformed-character-shape-" +
             std::to_wstring(caseIndex) + L".bin");
        HANDLE candidate = CreateFileW(
            candidatePath.c_str(), GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL, nullptr);
        DWORD written = 0;
        const Bytes& bytes = malformedCharacterShapeStreams[caseIndex];
        bool ready = candidate != INVALID_HANDLE_VALUE &&
            WriteFile(candidate, bytes.data(), static_cast<DWORD>(bytes.size()),
                      &written, nullptr) &&
            written == bytes.size();
        LARGE_INTEGER digestOffset{};
        digestOffset.QuadPart = 329;
        ready = ready && SetFilePointerEx(
            candidate, digestOffset, nullptr, FILE_BEGIN) &&
            WriteFile(candidate, activeIndexDigest.bytes.data(),
                      static_cast<DWORD>(activeIndexDigest.bytes.size()),
                      &written, nullptr) &&
            written == activeIndexDigest.bytes.size() &&
            FlushFileBuffers(candidate);
        PublicationInput malformed = Publication(candidate, index, 2);
        const bool rejected = ready && !store.Publish(malformed);
        GenerationPin after = store.PinActive();
        PinnedGenerationSnapshot snapshot{};
        const bool unchanged = rejected && after &&
            after.get() == pinnedBeforeOrderReject.get() &&
            after->Path() == pinnedPathBeforeOrderReject &&
            store.ActiveSerial() == activeBeforeOrderReject &&
            ReadPinnedGenerationSnapshot(after->Path(), &snapshot) &&
            snapshot.recordsBytes ==
                pinnedSnapshotBeforeOrderReject.recordsBytes &&
            Equal(snapshot.recordsHash,
                  pinnedSnapshotBeforeOrderReject.recordsHash) &&
            snapshot.indexBytes ==
                pinnedSnapshotBeforeOrderReject.indexBytes &&
            Equal(snapshot.indexHash,
                  pinnedSnapshotBeforeOrderReject.indexHash) &&
            snapshot.publishedFiles ==
                pinnedSnapshotBeforeOrderReject.publishedFiles &&
            Equal(snapshot.observedSemanticRoot,
                  pinnedSnapshotBeforeOrderReject.observedSemanticRoot) &&
            Equal(snapshot.captureRoot,
                  pinnedSnapshotBeforeOrderReject.captureRoot) &&
            CandidateCount(root) == 0;
        atomicCharacterShapeMatrix =
            atomicCharacterShapeMatrix && unchanged;
        if (candidate != INVALID_HANDLE_VALUE)
          CloseHandle(candidate);
        std::filesystem::remove(candidatePath, error);
        atomicCharacterShapeMatrix =
            atomicCharacterShapeMatrix && !error;
        error.clear();
    }
    std::wcout << L"CODEC_CHARACTER_SHAPE_STORE_ATOMIC_MATRIX "
               << atomicCharacterShapeMatrix << L" cases="
               << malformedCharacterShapeStreams.size() << L'\n';
    ok = Expect(
             atomicCharacterShapeMatrix,
             L"every malformed CharacterShape coverage stream leaves the "
             L"active generation and roots unchanged") &&
         ok;

    Bytes orderBytes = RewoundStoryParagraphStream();
    const auto orderPath = root / L"malformed-story-paragraph-order.bin";
    HANDLE orderFile = CreateFileW(
        orderPath.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
        nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD orderWritten = 0;
    Sha256 orderIndexDigest{};
    bool orderReady = orderFile != INVALID_HANDLE_VALUE &&
        WriteFile(orderFile, orderBytes.data(),
                  static_cast<DWORD>(orderBytes.size()), &orderWritten,
                  nullptr) &&
        orderWritten == orderBytes.size() &&
        HashFileRangeChunked(index, 0, FileBytes(index), &orderIndexDigest);
    LARGE_INTEGER orderOffset{};
    if (orderReady) {
      orderOffset.QuadPart = 329;
      orderReady = SetFilePointerEx(
              orderFile, orderOffset, nullptr, FILE_BEGIN) &&
          WriteFile(orderFile, orderIndexDigest.bytes.data(),
                    static_cast<DWORD>(orderIndexDigest.bytes.size()),
                    &orderWritten, nullptr) &&
          orderWritten == orderIndexDigest.bytes.size();
    }
    CanonicalizationResult orderCanonical{};
    const Bytes canonicalOrderBytes = CanonicalStoryParagraphStream();
    MemoryReader canonicalOrderReader{&canonicalOrderBytes};
    if (orderReady) {
      orderReady = CanonicalizeRecordStream(
          {{&canonicalOrderReader, ReadMemory, canonicalOrderBytes.size(),
            StreamKind::Capture}, nullptr, nullptr, {}},
          &orderCanonical) == Error::None;
    }
    if (orderReady) {
      orderOffset.QuadPart = 58;
      const std::array<std::uint8_t, 2> orderFlags{
          static_cast<std::uint8_t>(orderCanonical.semanticCertified),
          static_cast<std::uint8_t>(orderCanonical.layoutPresent)};
      orderReady = SetFilePointerEx(
              orderFile, orderOffset, nullptr, FILE_BEGIN) &&
          WriteFile(orderFile, orderFlags.data(),
                    static_cast<DWORD>(orderFlags.size()), &orderWritten,
                    nullptr) &&
          orderWritten == orderFlags.size();
      for (const auto &rootFact : std::array<
               std::pair<std::uint64_t, const Sha256 *>, 3>{{
               {120, &orderCanonical.observedSemanticRoot},
               {152, &orderCanonical.layoutRoot},
               {184, &orderCanonical.captureRoot}}}) {
        orderOffset.QuadPart = static_cast<LONGLONG>(rootFact.first);
        orderReady = orderReady && SetFilePointerEx(
            orderFile, orderOffset, nullptr, FILE_BEGIN) &&
            WriteFile(orderFile, rootFact.second->bytes.data(),
                      static_cast<DWORD>(rootFact.second->bytes.size()),
                      &orderWritten, nullptr) &&
            orderWritten == rootFact.second->bytes.size();
      }
      orderReady = orderReady && FlushFileBuffers(orderFile);
    }
    PublicationInput orderPublication = input;
    orderPublication.first.observedSemanticRoot =
        orderCanonical.observedSemanticRoot;
    orderPublication.first.semanticCertified = orderCanonical.semanticCertified;
    orderPublication.first.layoutPresent = orderCanonical.layoutPresent;
    orderPublication.first.layoutRoot = orderCanonical.layoutRoot;
    orderPublication.first.captureRoot = orderCanonical.captureRoot;
    orderPublication.first.coverage = {orderCanonical.coverage.byteLength,
                                       orderCanonical.coverage.digest};
    orderPublication.first.unavailable = {
        orderCanonical.unavailable.byteLength,
        orderCanonical.unavailable.digest};
    orderPublication.first.diagnostics = {
        orderCanonical.diagnostics.byteLength,
        orderCanonical.diagnostics.digest};
    orderPublication.second = orderPublication.first;
    orderPublication.records = {orderFile, 0, FileBytes(orderFile)};
    const bool matchingOrderManifest = orderReady &&
        Equal(orderPublication.first.observedSemanticRoot,
              orderCanonical.observedSemanticRoot) &&
        Equal(orderPublication.first.captureRoot, orderCanonical.captureRoot);
    const bool orderPublishRejected =
        matchingOrderManifest && !store.Publish(orderPublication);
    GenerationPin pinnedAfterOrderReject = store.PinActive();
    PinnedGenerationSnapshot pinnedSnapshotAfterOrderReject{};
    const bool pinnedSnapshotUnchanged = pinnedAfterOrderReject &&
        ReadPinnedGenerationSnapshot(
            pinnedAfterOrderReject->Path(),
            &pinnedSnapshotAfterOrderReject) &&
        pinnedSnapshotAfterOrderReject.recordsBytes ==
            pinnedSnapshotBeforeOrderReject.recordsBytes &&
        Equal(pinnedSnapshotAfterOrderReject.recordsHash,
              pinnedSnapshotBeforeOrderReject.recordsHash) &&
        pinnedSnapshotAfterOrderReject.indexBytes ==
            pinnedSnapshotBeforeOrderReject.indexBytes &&
        Equal(pinnedSnapshotAfterOrderReject.indexHash,
              pinnedSnapshotBeforeOrderReject.indexHash) &&
        pinnedSnapshotAfterOrderReject.publishedFiles ==
            pinnedSnapshotBeforeOrderReject.publishedFiles &&
        Equal(pinnedSnapshotAfterOrderReject.observedSemanticRoot,
              pinnedSnapshotBeforeOrderReject.observedSemanticRoot) &&
        Equal(pinnedSnapshotAfterOrderReject.captureRoot,
              pinnedSnapshotBeforeOrderReject.captureRoot) &&
        Equal(pinnedSnapshotAfterOrderReject.observedSemanticRoot,
              input.first.observedSemanticRoot) &&
        Equal(pinnedSnapshotAfterOrderReject.captureRoot,
              input.first.captureRoot);
    const bool atomicOrderReject =
        orderPublishRejected && pinnedSnapshotReady &&
        store.ActiveSerial() == activeBeforeOrderReject &&
        pinnedBeforeOrderReject && pinnedAfterOrderReject &&
        pinnedAfterOrderReject.get() == pinnedBeforeOrderReject.get() &&
        pinnedAfterOrderReject->Path() == pinnedPathBeforeOrderReject &&
        pinnedSnapshotUnchanged && CandidateCount(root) == 0;
    std::wcout << L"CODEC_STORY_PARAGRAPH_ORDER_STORE_ATOMIC_REJECTION "
               << atomicOrderReject << L'\n';
    ok = Expect(
             atomicOrderReject,
             L"matching-manifest malformed ownership order rejects without "
             L"publishing serial, generation, bytes, roots, or path") &&
         ok;
    if (orderFile != INVALID_HANDLE_VALUE)
      CloseHandle(orderFile);
    pinnedBeforeOrderReject.reset();
    pinnedAfterOrderReject.reset();

    const std::uint64_t activeBeforeLineageReject = store.ActiveSerial();
    const Uuid128 candidateDocument = ReceiptUuid(80);
    const Uuid128 unknownPrior = ReceiptUuid(81);
    Bytes lineageBytes = ManifestRecord(4);
    const auto appendLineage = [&lineageBytes](const Bytes& value) {
      lineageBytes.insert(lineageBytes.end(), value.begin(), value.end());
    };
    appendLineage(NodeRecord(
        1, NodeKind::Document, candidateDocument, nullptr, 0));
    appendLineage(TombstoneRecord(
        2, unknownPrior, 1, TombstoneReason::ExternalUnmatched));
    appendLineage(RemapRecord(
        3, unknownPrior, nullptr, RemapDisposition::Tombstoned,
        RemapReason::Delete));
    const auto lineagePath = root / L"unknown-prior-lineage.bin";
    HANDLE lineageFile = CreateFileW(
        lineagePath.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
        nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD lineageWritten = 0;
    Sha256 sourceIndexDigest{};
    bool lineageReady = lineageFile != INVALID_HANDLE_VALUE &&
        WriteFile(lineageFile, lineageBytes.data(),
                  static_cast<DWORD>(lineageBytes.size()),
                  &lineageWritten, nullptr) &&
        lineageWritten == lineageBytes.size() &&
        HashFileRangeChunked(index, 0, FileBytes(index), &sourceIndexDigest);
    LARGE_INTEGER lineageOffset{};
    if (lineageReady) {
      lineageOffset.QuadPart = 329;
      lineageReady = SetFilePointerEx(
              lineageFile, lineageOffset, nullptr, FILE_BEGIN) &&
          WriteFile(lineageFile, sourceIndexDigest.bytes.data(),
                    static_cast<DWORD>(sourceIndexDigest.bytes.size()),
                    &lineageWritten, nullptr) &&
          lineageWritten == sourceIndexDigest.bytes.size();
    }
    CanonicalizationResult lineageCanonical{};
    if (lineageReady) {
      lineageReady = CanonicalizeRecordStream(
          {{lineageFile, ReadHandle, FileBytes(lineageFile),
            StreamKind::Capture}, nullptr, nullptr, {}},
          &lineageCanonical) == Error::None;
    }
    if (lineageReady) {
      lineageOffset.QuadPart = 58;
      const std::array<std::uint8_t, 2> lineageFlags{
          static_cast<std::uint8_t>(lineageCanonical.semanticCertified),
          static_cast<std::uint8_t>(lineageCanonical.layoutPresent)};
      lineageReady = SetFilePointerEx(
              lineageFile, lineageOffset, nullptr, FILE_BEGIN) &&
          WriteFile(lineageFile, lineageFlags.data(),
                    static_cast<DWORD>(lineageFlags.size()),
                    &lineageWritten, nullptr) &&
          lineageWritten == lineageFlags.size();
      for (const auto& rootFact : std::array<
               std::pair<std::uint64_t, const Sha256*>, 3>{{
               {120, &lineageCanonical.observedSemanticRoot},
               {152, &lineageCanonical.layoutRoot},
               {184, &lineageCanonical.captureRoot}}}) {
        lineageOffset.QuadPart = static_cast<LONGLONG>(rootFact.first);
        lineageReady = lineageReady && SetFilePointerEx(
            lineageFile, lineageOffset, nullptr, FILE_BEGIN) &&
            WriteFile(lineageFile, rootFact.second->bytes.data(),
                      static_cast<DWORD>(rootFact.second->bytes.size()),
                      &lineageWritten, nullptr) &&
            lineageWritten == rootFact.second->bytes.size();
      }
      lineageReady = lineageReady && FlushFileBuffers(lineageFile);
    }
    PublicationInput unknownPriorPublication =
        Publication(lineageFile, index, 2);
    const bool atomicLineageReject = lineageReady &&
        !store.Publish(unknownPriorPublication) &&
        store.ActiveSerial() == activeBeforeLineageReject &&
        CandidateCount(root) == 0;
    std::wcout << L"GRAPH_STORE_ACTIVE_LINEAGE_ATOMIC_REJECT "
               << atomicLineageReject << L'\n';
    ok = Expect(
        atomicLineageReject,
        L"store atomically rejects receipt IDs absent from active generation") &&
        ok;
    if (lineageFile != INVALID_HANDLE_VALUE)
      CloseHandle(lineageFile);
    BlobNodeInput malformedDetail{Uuid(4098), 0, Bytes{}};
    const Uuid128 malformedDocument = Uuid(4099);
    Bytes malformedBytes = ManifestRecord(3);
    const Bytes malformedNode = NodeRecord(
        1, NodeKind::Document, malformedDocument, nullptr, 0);
    malformedBytes.insert(
        malformedBytes.end(), malformedNode.begin(), malformedNode.end());
    const Bytes crossOwnerCoverage = CoverageRecord(
        2, Uuid(4100), malformedDetail, ProfileId::Structure,
        CoverageState::Complete, 0, RecordKind::Node, 100);
    malformedBytes.insert(malformedBytes.end(), crossOwnerCoverage.begin(),
                          crossOwnerCoverage.end());
    const auto malformedPath = root / L"malformed-owner-block.bin";
    HANDLE malformedFile = CreateFileW(
        malformedPath.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
        nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD malformedWritten = 0;
    const bool malformedWrittenOk = malformedFile != INVALID_HANDLE_VALUE &&
        WriteFile(malformedFile, malformedBytes.data(),
                  static_cast<DWORD>(malformedBytes.size()),
                  &malformedWritten, nullptr) &&
        malformedWritten == malformedBytes.size() &&
        FlushFileBuffers(malformedFile);
    std::uint64_t malformedCount = 0;
    const Error malformedValidation = malformedWrittenOk
        ? ValidateRecordStream(
              {malformedFile, ReadHandle, malformedBytes.size(),
               StreamKind::Capture},
              &malformedCount)
        : Error::Truncated;
    PublicationInput malformedPublication = input;
    malformedPublication.records = {
        malformedFile, 0, malformedBytes.size()};
    ok = Expect(
             malformedValidation == Error::BadRecordOrder &&
                 !store.Publish(malformedPublication) &&
                 store.ActiveSerial() == 1,
             L"store rejects cross-owner block before publication") &&
         ok;
    if (malformedFile != INVALID_HANDLE_VALUE)
      CloseHandle(malformedFile);

    const Uuid128 unknownDocument = Uuid(4200);
    const Uuid128 unknownDefinition = Uuid(4201);
    Bytes unknownDefinitionBytes = ManifestRecord(4);
    const auto appendUnknown = [&unknownDefinitionBytes](const Bytes& record) {
      unknownDefinitionBytes.insert(
          unknownDefinitionBytes.end(), record.begin(), record.end());
    };
    appendUnknown(NodeRecord(
        1, NodeKind::Document, unknownDocument, nullptr, 0));
    appendUnknown(EdgeRecord(
        2, EdgeKind::Contains, unknownDocument, unknownDefinition));
    appendUnknown(NodeRecord(
        3, NodeKind::Definition, unknownDefinition, &unknownDocument, 0,
        {}, {}, nullptr, {}, {}, ObservationState::NotExposed, 0,
        static_cast<DefinitionKind>(UINT16_MAX)));
    const auto unknownDefinitionPath =
        root / L"unknown-definition-kind.bin";
    HANDLE unknownDefinitionFile = CreateFileW(
        unknownDefinitionPath.c_str(), GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
        nullptr);
    DWORD unknownDefinitionWritten = 0;
    const bool unknownDefinitionWrittenOk =
        unknownDefinitionFile != INVALID_HANDLE_VALUE &&
        WriteFile(
            unknownDefinitionFile, unknownDefinitionBytes.data(),
            static_cast<DWORD>(unknownDefinitionBytes.size()),
            &unknownDefinitionWritten, nullptr) &&
        unknownDefinitionWritten == unknownDefinitionBytes.size() &&
        FlushFileBuffers(unknownDefinitionFile);
    std::uint64_t unknownDefinitionCount = 0;
    const Error unknownDefinitionValidation = unknownDefinitionWrittenOk
        ? ValidateRecordStream(
              {unknownDefinitionFile, ReadHandle,
               unknownDefinitionBytes.size(), StreamKind::Capture},
              &unknownDefinitionCount)
        : Error::Truncated;
    PublicationInput unknownDefinitionPublication = input;
    unknownDefinitionPublication.records = {
        unknownDefinitionFile, 0, unknownDefinitionBytes.size()};
    const bool unknownDefinitionPublicationRejected =
        unknownDefinitionValidation == Error::BadScalar &&
        !store.Publish(unknownDefinitionPublication) &&
        store.ActiveSerial() == 1;
    std::wcout << L"GRAPH_STORE_UNKNOWN_DEFINITION_KIND_REJECTED "
               << unknownDefinitionPublicationRejected << L'\n';
    ok = Expect(
             unknownDefinitionPublicationRejected,
             L"store rejects unreferenced unknown DefinitionKind") && ok;
    if (unknownDefinitionFile != INVALID_HANDLE_VALUE)
      CloseHandle(unknownDefinitionFile);

    Bytes queryBytes = ManifestRecord(2, ProfileBit(ProfileId::Structure),
                                      StreamKind::QueryView);
    const Uuid128 queryDocument = Uuid(4001);
    const Bytes queryNode =
        NodeRecord(1, NodeKind::Document, queryDocument, nullptr, 0);
    queryBytes.insert(queryBytes.end(), queryNode.begin(), queryNode.end());
    queryBytes[58] = static_cast<std::uint8_t>(input.first.semanticCertified);
    queryBytes[59] = static_cast<std::uint8_t>(input.first.layoutPresent);
    std::copy(input.first.observedSemanticRoot.bytes.begin(),
              input.first.observedSemanticRoot.bytes.end(),
              queryBytes.begin() + 120);
    std::copy(input.first.layoutRoot.bytes.begin(),
              input.first.layoutRoot.bytes.end(), queryBytes.begin() + 152);
    std::copy(input.first.captureRoot.bytes.begin(),
              input.first.captureRoot.bytes.end(), queryBytes.begin() + 184);
    const auto queryPath = root / L"query-view.bin";
    HANDLE queryFile = CreateFileW(
        queryPath.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
        nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD queryWritten = 0;
    const bool queryWrittenOk = queryFile != INVALID_HANDLE_VALUE &&
                                WriteFile(queryFile, queryBytes.data(),
                                          static_cast<DWORD>(queryBytes.size()),
                                          &queryWritten, nullptr) &&
                                queryWritten == queryBytes.size() &&
                                FlushFileBuffers(queryFile);
    PublicationInput queryPublication = input;
    queryPublication.records = {queryFile, 0, queryBytes.size()};
    ok = Expect(queryWrittenOk && !store.Publish(queryPublication),
                L"store rejects QueryView even with matching curated roots") &&
         ok;
    if (queryFile != INVALID_HANDLE_VALUE)
      CloseHandle(queryFile);
    PublicationInput profileMismatch = input;
    profileMismatch.first.closedProfileBits |= ProfileBit(ProfileId::Layout);
    profileMismatch.second = profileMismatch.first;
    ok = Expect(!store.Publish(profileMismatch),
                L"publish rejects GraphVersion/profile mismatch") &&
         ok;
    PublicationInput integrityMismatch = input;
    integrityMismatch.first.integrity = CaptureIntegrity::TraversalFailure;
    integrityMismatch.second = integrityMismatch.first;
    ok = Expect(!store.Publish(integrityMismatch),
                L"publish rejects CaptureIntegrity mismatch") &&
         ok;
    GenerationPin prior = store.PinActive();
    const std::wstring priorPath = prior ? prior->Path() : L"";
    ok = Expect(prior && prior->RecordsBytes() == FileBytes(records),
                L"pin published file-backed generation") &&
         ok;
    constexpr std::array failures{
        FailurePoint::OpenOwner,     FailurePoint::OpenRecords,
        FailurePoint::WriteRecords,  FailurePoint::FlushRecords,
        FailurePoint::OpenIndex,     FailurePoint::WriteIndex,
        FailurePoint::FlushIndex,    FailurePoint::OpenManifest,
        FailurePoint::WriteManifest, FailurePoint::SealManifest,
        FailurePoint::Rename,        FailurePoint::Swap,
        FailurePoint::OpenCommit,    FailurePoint::WriteCommit,
        FailurePoint::FlushCommit};
    for (FailurePoint failure : failures) {
      PublicationInput failed = Publication(
          records, index,
          static_cast<std::uint8_t>(10 + static_cast<unsigned>(failure)));
      const bool published = store.Publish(failed, failure);
      GenerationPin activeAfterFailure = store.PinActive();
      GraphStore restartAfterFailure(root.wstring());
      const bool restarted = restartAfterFailure.Initialize();
      GenerationPin restartedPrior = restartAfterFailure.PinActive();
      ok = Expect(!published && prior && activeAfterFailure &&
                      activeAfterFailure.get() == prior.get() &&
                      activeAfterFailure->Path() == priorPath &&
                      CandidateCount(root) == 0 && ReadPrior(priorPath) &&
                      restarted && restartedPrior &&
                      restartAfterFailure.ActiveSerial() == 1 &&
                      restartedPrior->Path() == priorPath &&
                      ReadPrior(restartedPrior->Path()),
                  L"crash boundary preserves readable prior across restart") &&
           ok;
    }
    const auto crashRoot = root / L"restart-crash-window";
    GraphStore crashStore(crashRoot.wstring());
    const bool crashInitialized = crashStore.Initialize() &&
        crashStore.Publish(Publication(records, index, 76)) &&
        crashStore.ActiveSerial() == 1;
    GenerationPin crashPriorPin = crashStore.PinActive();
    const std::wstring crashPriorPath =
        crashPriorPin ? crashPriorPin->Path() : L"";
    PublicationInput crashWindow = Publication(records, index, 77);
    const bool crashPublished = crashInitialized && crashStore.Publish(
        crashWindow, FailurePoint::SwapCleanupFailure);
    const auto abandonedGeneration =
        crashRoot / L"generation-00000000000000000002";
    const bool abandonedRetained =
        std::filesystem::exists(abandonedGeneration);
    GraphStore restarted(crashRoot.wstring());
    const bool restartInitialized = restarted.Initialize();
    GenerationPin restartedActive = restarted.PinActive();
    const bool restartIgnoredUncommitted = !crashPublished &&
        abandonedRetained && restartInitialized && restartedActive &&
        restarted.ActiveSerial() == 1 &&
        restartedActive->Path() == crashPriorPath &&
        ReadPrior(restartedActive->Path());
    const bool retryPublished = restarted.Publish(
        Publication(records, index, 78));
    GenerationPin retryActive = restarted.PinActive();
    std::wcout << L"STORE_RESTART_EPOCH_FACT init=" << restartInitialized
               << L" ignored=" << restartIgnoredUncommitted
               << L" retry=" << retryPublished << L" serial="
               << restarted.ActiveSerial() << L'\n';
    const bool retryReservedPastAbandoned = retryPublished && retryActive &&
        restarted.ActiveSerial() == 3 &&
        retryActive->Path().find(L"generation-00000000000000000003") !=
            std::wstring::npos;
    ok = Expect(
             restartIgnoredUncommitted && retryReservedPastAbandoned,
             L"restart ignores renamed uncommitted generation and retry "
             L"reserves a fresh serial") &&
         ok;
    std::wcout << L"STORE_RESTART_UNCOMMITTED_INVISIBLE "
               << restartIgnoredUncommitted << L'\n'
               << L"STORE_RESTART_ABANDONED_SERIAL_NOT_REUSED "
               << retryReservedPastAbandoned << L'\n';

    const auto deletedRoot = root / L"restart-deleted-reservation";
    bool deletedReservationSurvives = false;
    {
      GraphStore beforeCrash(deletedRoot.wstring());
      const bool beforeInitialized = beforeCrash.Initialize() &&
          beforeCrash.Publish(Publication(records, index, 78)) &&
          beforeCrash.ActiveSerial() == 1;
      const bool failedAfterRename = beforeInitialized && !beforeCrash.Publish(
          Publication(records, index, 79), FailurePoint::SwapCleanupFailure);
      const auto deletedGeneration =
          deletedRoot / L"generation-00000000000000000002";
      const bool residueExistedBeforeRestart =
          std::filesystem::exists(deletedGeneration);
      GraphStore restartOne(deletedRoot.wstring());
      const bool cleanupRestart = failedAfterRename &&
          residueExistedBeforeRestart && restartOne.Initialize() &&
          !std::filesystem::exists(deletedGeneration) &&
          restartOne.ActiveSerial() == 1;
      GraphStore restartTwo(deletedRoot.wstring());
      const bool emptySecondRestart = cleanupRestart &&
          restartTwo.Initialize() && restartTwo.ActiveSerial() == 1 &&
          !std::filesystem::exists(deletedGeneration);
      const bool publishedAfterTwoRestarts = emptySecondRestart &&
          restartTwo.Publish(Publication(records, index, 80));
      GenerationPin afterTwoRestarts = restartTwo.PinActive();
      std::wcout << L"STORE_DELETED_EPOCH_FACT before=" << beforeInitialized
                 << L" residue=" << residueExistedBeforeRestart
                 << L" cleanup=" << cleanupRestart << L" restart2="
                 << emptySecondRestart << L" publish="
                 << publishedAfterTwoRestarts << L" serial="
                 << restartTwo.ActiveSerial() << L'\n';
      deletedReservationSurvives = publishedAfterTwoRestarts &&
          afterTwoRestarts && restartTwo.ActiveSerial() == 3 &&
          afterTwoRestarts->Path().find(
              L"generation-00000000000000000003") != std::wstring::npos;
    }
    ok = Expect(
             deletedReservationSurvives,
             L"durable reservation survives successful deletion and two "
             L"fresh restarts") && ok;
    std::wcout << L"STORE_MULTI_RESTART_DELETED_RESERVATION_NOT_REUSED "
               << deletedReservationSurvives << L'\n';

    const auto rollbackRoot = root / L"stale-valid-main-rollback";
    const auto rollbackMain = rollbackRoot / L"high-water.hgr";
    const auto savedOldMain = rollbackRoot / L"saved-old-high-water.hgr";
    GraphStore rollbackBootstrap(rollbackRoot.wstring());
    const bool rollbackInitialized = rollbackBootstrap.Initialize();
    const bool oldMainSaved = rollbackInitialized && CopyFileW(
        rollbackMain.c_str(), savedOldMain.c_str(), FALSE) != FALSE;
    const bool reservationConsumed = oldMainSaved &&
        !rollbackBootstrap.Publish(
            Publication(records, index, 91), FailurePoint::OpenOwner) &&
        CandidateCount(rollbackRoot) == 0;
    const bool staleValidRestored = reservationConsumed && CopyFileW(
        savedOldMain.c_str(), rollbackMain.c_str(), FALSE) != FALSE;
    GraphStore rollbackRestart(rollbackRoot.wstring());
    const bool rollbackRestarted = staleValidRestored &&
        rollbackRestart.Initialize();
    const bool rollbackRetried = rollbackRestarted && rollbackRestart.Publish(
        Publication(records, index, 92));
    const bool staleValidMainCannotReuse = rollbackRetried &&
        rollbackRestart.ActiveSerial() == 2;
    ok = Expect(
             staleValidMainCannotReuse,
             L"independent witness prevents stale-valid main rollback reuse") &&
         ok;
    std::wcout << L"STORE_STALE_VALID_MAIN_ROLLBACK_NOT_REUSED "
               << staleValidMainCannotReuse << L'\n';

    [&]() __declspec(noinline) {
    const auto epochRootA = root / L"foreign-journal-a";
    const auto epochRootB = root / L"foreign-journal-b";
    GraphStore epochStoreA(epochRootA.wstring());
    GraphStore epochStoreB(epochRootB.wstring());
    const bool epochBases = epochStoreA.Initialize() && epochStoreB.Initialize() &&
        epochStoreA.Publish(Publication(records, index, 97)) &&
        epochStoreB.Publish(Publication(records, index, 98));
    const auto epochSavedMain = epochRootA / L"saved-main.hgr";
    const bool epochMainSaved = epochBases && CopyFileW(
        (epochRootA / L"high-water.hgr").c_str(), epochSavedMain.c_str(),
        FALSE) != FALSE;
    const bool epochReservations = epochMainSaved &&
        !epochStoreA.Publish(Publication(records, index, 99),
                             FailurePoint::OpenOwner) &&
        !epochStoreB.Publish(Publication(records, index, 100),
                             FailurePoint::OpenOwner) &&
        !epochStoreB.Publish(Publication(records, index, 101),
                             FailurePoint::OpenOwner);
    const bool foreignJournalGrafted = epochReservations &&
        CopyFileW(epochSavedMain.c_str(),
                  (epochRootA / L"high-water.hgr").c_str(), FALSE) != FALSE &&
        CopyFileW((epochRootB / L"reservations.hgj").c_str(),
                  (epochRootA / L"reservations.hgj").c_str(), FALSE) != FALSE;
    GraphStore epochRestartA(epochRootA.wstring());
    const bool foreignJournalRejected = foreignJournalGrafted &&
        !epochRestartA.Initialize() &&
        !epochRestartA.Publish(Publication(records, index, 102)) &&
        epochRestartA.ActiveSerial() == 0 &&
        std::filesystem::exists(
            epochRootA / L"generation-00000000000000000001") &&
        CandidateCount(epochRootA) == 0;
    ok = Expect(
             foreignJournalRejected,
             L"foreign journal epoch is rejected before local rewrite") && ok;
    std::wcout << L"STORE_FOREIGN_JOURNAL_EPOCH_REJECTED "
               << foreignJournalRejected << L'\n';
    }();

    [&]() __declspec(noinline) {
    const auto foreignMainRoot = root / L"foreign-main-substitution";
    const auto foreignMainDonor = root / L"foreign-main-donor";
    GraphStore foreignMainLocal(foreignMainRoot.wstring());
    GraphStore foreignMainSource(foreignMainDonor.wstring());
    const bool foreignMainPrepared = foreignMainLocal.Initialize() &&
        foreignMainSource.Initialize() &&
        foreignMainLocal.Publish(Publication(records, index, 103)) &&
        foreignMainSource.Publish(Publication(records, index, 104)) &&
        CopyFileW((foreignMainDonor / L"high-water.hgr").c_str(),
                  (foreignMainRoot / L"high-water.hgr").c_str(), FALSE) != FALSE;
    GraphStore foreignMainRestart(foreignMainRoot.wstring());
    const bool foreignMainRejected = foreignMainPrepared &&
        !foreignMainRestart.Initialize() &&
        !foreignMainRestart.Publish(Publication(records, index, 105)) &&
        CandidateCount(foreignMainRoot) == 0;

    const auto foreignCommitRoot = root / L"foreign-commit-substitution";
    const auto foreignCommitDonor = root / L"foreign-commit-donor";
    GraphStore foreignCommitLocal(foreignCommitRoot.wstring());
    GraphStore foreignCommitSource(foreignCommitDonor.wstring());
    const bool foreignCommitPublished = foreignCommitLocal.Initialize() &&
        foreignCommitSource.Initialize() &&
        foreignCommitLocal.Publish(Publication(records, index, 106)) &&
        foreignCommitSource.Publish(Publication(records, index, 107));
    const auto foreignCommitPath = foreignCommitRoot /
        L"generation-00000000000000000001" / L"commit.hgc";
    SetFileAttributesW(foreignCommitPath.c_str(), FILE_ATTRIBUTE_NORMAL);
    const bool foreignCommitPrepared = foreignCommitPublished && CopyFileW(
        (foreignCommitDonor /
         L"generation-00000000000000000001" / L"commit.hgc").c_str(),
        foreignCommitPath.c_str(), FALSE) != FALSE;
    GraphStore foreignCommitRestart(foreignCommitRoot.wstring());
    const bool foreignCommitRestarted = foreignCommitRestart.Initialize();
    const bool foreignCommitInvisible = foreignCommitPrepared &&
        foreignCommitRestarted && foreignCommitRestart.ActiveSerial() == 0 &&
        !foreignCommitRestart.PinActive();
    std::wcout << L"STORE_FOREIGN_COMMIT_FACT prepared="
               << foreignCommitPrepared << L" restart="
               << foreignCommitRestarted << L" active="
               << foreignCommitRestart.ActiveSerial() << L'\n';

    const auto identityRoot = root / L"identity-validation";
    GraphStore identityBase(identityRoot.wstring());
    const bool identityPrepared = identityBase.Initialize();
    const auto identityPath = identityRoot / L"store-identity.hgs";
    const auto savedIdentity = identityRoot / L"saved-identity.hgs";
    const bool identitySaved = identityPrepared && CopyFileW(
        identityPath.c_str(), savedIdentity.c_str(), FALSE) != FALSE;
    const auto foreignIdentityRoot = root / L"foreign-identity-swap";
    GraphStore foreignIdentityStore(foreignIdentityRoot.wstring());
    const bool foreignIdentityCreated = foreignIdentityStore.Initialize();
    SetFileAttributesW(identityPath.c_str(), FILE_ATTRIBUTE_NORMAL);
    const bool foreignIdentityCopied = foreignIdentityCreated && CopyFileW(
        (foreignIdentityRoot / L"store-identity.hgs").c_str(),
        identityPath.c_str(), FALSE) != FALSE;
    GraphStore foreignIdentityRestart(identityRoot.wstring());
    const bool foreignIdentityRejected = foreignIdentityCopied &&
        !foreignIdentityRestart.Initialize() &&
        !foreignIdentityRestart.Publish(Publication(records, index, 111));
    CopyFileW(savedIdentity.c_str(), identityPath.c_str(), FALSE);
    SetFileAttributesW(identityPath.c_str(), FILE_ATTRIBUTE_NORMAL);
    HANDLE identityCorrupt = CreateFileW(
        identityPath.c_str(), GENERIC_WRITE, 0, nullptr, TRUNCATE_EXISTING,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    const Bytes identityShort{'H', 'G', 'S', '1'};
    DWORD identityWritten = 0;
    const bool identityTruncated = identitySaved &&
        identityCorrupt != INVALID_HANDLE_VALUE &&
        WriteFile(identityCorrupt, identityShort.data(),
                  static_cast<DWORD>(identityShort.size()), &identityWritten,
                  nullptr) && identityWritten == identityShort.size() &&
        FlushFileBuffers(identityCorrupt);
    if (identityCorrupt != INVALID_HANDLE_VALUE) CloseHandle(identityCorrupt);
    GraphStore identityCorruptRestart(identityRoot.wstring());
    const bool corruptIdentityRejected = identityTruncated &&
        !identityCorruptRestart.Initialize() &&
        !identityCorruptRestart.Publish(Publication(records, index, 108));
    CopyFileW(savedIdentity.c_str(), identityPath.c_str(), FALSE);
    DeleteFileW(identityPath.c_str());
    GraphStore identityMissingRestart(identityRoot.wstring());
    const bool missingIdentityRejected =
        !identityMissingRestart.Initialize() &&
        !identityMissingRestart.Publish(Publication(records, index, 109));

    bool identityFailureMatrix = true;
    constexpr std::array identityFailures{
        FailurePoint::OpenIdentity, FailurePoint::WriteIdentity,
        FailurePoint::FlushIdentity, FailurePoint::ReplaceIdentity};
    for (const FailurePoint point : identityFailures) {
      const auto identityFailureRoot = root /
          (L"identity-failure-" +
           std::to_wstring(static_cast<unsigned>(point)));
      GraphStore identityFailure(identityFailureRoot.wstring());
      const bool rejected = !identityFailure.Initialize(point) &&
          !std::filesystem::exists(
              identityFailureRoot / L"store-identity.hgs");
      GraphStore identityRetry(identityFailureRoot.wstring());
      identityFailureMatrix = rejected && identityRetry.Initialize() &&
          identityRetry.Publish(Publication(records, index, 110)) &&
          identityRetry.ActiveSerial() == 1 && identityFailureMatrix;
    }
    ok = Expect(
             foreignMainRejected && foreignCommitInvisible &&
                 foreignIdentityRejected && corruptIdentityRejected &&
                 missingIdentityRejected && identityFailureMatrix,
             L"foreign checkpoint/commit and identity failure matrix are "
             L"epoch-bound and fail closed") && ok;
    std::wcout << L"STORE_FOREIGN_CHECKPOINT_EPOCH_REJECTED "
               << foreignMainRejected << L'\n'
               << L"STORE_FOREIGN_COMMIT_EPOCH_INVISIBLE "
               << foreignCommitInvisible << L'\n'
               << L"STORE_FOREIGN_IDENTITY_REJECTED "
               << foreignIdentityRejected << L'\n'
               << L"STORE_IDENTITY_INVALID_MISSING_REJECTED "
               << (corruptIdentityRejected && missingIdentityRejected) << L'\n'
               << L"STORE_IDENTITY_CREATION_FAILURE_MATRIX "
               << identityFailureMatrix << L" phases=4\n";
    }();

    const auto witnessRollbackRoot = root / L"stale-valid-journal-rollback";
    const auto witnessJournal = witnessRollbackRoot / L"reservations.hgj";
    const auto savedOldJournal = witnessRollbackRoot / L"saved-old-journal.hgj";
    GraphStore witnessBootstrap(witnessRollbackRoot.wstring());
    const bool witnessInitialized = witnessBootstrap.Initialize();
    const bool oldJournalSaved = witnessInitialized && CopyFileW(
        witnessJournal.c_str(), savedOldJournal.c_str(), FALSE) != FALSE;
    const bool witnessReservationConsumed = oldJournalSaved &&
        !witnessBootstrap.Publish(
            Publication(records, index, 93), FailurePoint::OpenOwner);
    const bool staleWitnessRestored = witnessReservationConsumed && CopyFileW(
        savedOldJournal.c_str(), witnessJournal.c_str(), FALSE) != FALSE;
    GraphStore witnessRollbackRestart(witnessRollbackRoot.wstring());
    const bool staleWitnessFailsClosed = staleWitnessRestored &&
        !witnessRollbackRestart.Initialize() &&
        !witnessRollbackRestart.Publish(Publication(records, index, 94)) &&
        CandidateCount(witnessRollbackRoot) == 0;
    ok = Expect(
             staleWitnessFailsClosed,
             L"checkpoint detects stale-valid journal rollback and fails closed") &&
         ok;

    const auto truncatedTailRoot = root / L"truncated-journal-tail";
    GraphStore truncatedTailStore(truncatedTailRoot.wstring());
    const bool truncatedTailInitialized = truncatedTailStore.Initialize();
    const auto truncatedJournal = truncatedTailRoot / L"reservations.hgj";
    HANDLE truncatedFile = CreateFileW(
        truncatedJournal.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    const Bytes tornTail{'H', 'G', 'J', '1', 1, 0, 1, 0};
    DWORD tornWritten = 0;
    const bool tornTailWritten = truncatedTailInitialized &&
        truncatedFile != INVALID_HANDLE_VALUE &&
        WriteFile(truncatedFile, tornTail.data(),
                  static_cast<DWORD>(tornTail.size()), &tornWritten, nullptr) &&
        tornWritten == tornTail.size() && FlushFileBuffers(truncatedFile);
    if (truncatedFile != INVALID_HANDLE_VALUE) CloseHandle(truncatedFile);
    GraphStore truncatedTailRestart(truncatedTailRoot.wstring());
    const bool truncatedTailRecovered = tornTailWritten &&
        truncatedTailRestart.Initialize() &&
        truncatedTailRestart.Publish(Publication(records, index, 95)) &&
        truncatedTailRestart.ActiveSerial() == 1 &&
        std::filesystem::file_size(truncatedJournal, error) == 192;
    ok = Expect(
             truncatedTailRecovered,
             L"truncated final journal append is discarded without rollback") &&
         ok;
    std::wcout << L"STORE_STALE_VALID_WITNESS_ROLLBACK_FAIL_CLOSED "
               << staleWitnessFailsClosed << L'\n'
               << L"STORE_TRUNCATED_JOURNAL_TAIL_RECOVERED "
               << truncatedTailRecovered << L'\n';

    bool reservationFailureMatrix = true;
    constexpr std::array reservationFailures{
        FailurePoint::OpenReservation, FailurePoint::WriteReservation,
        FailurePoint::FlushReservation, FailurePoint::ReplaceReservation};
    for (const FailurePoint reservationFailure : reservationFailures) {
      const auto failureRoot = root /
          (L"reservation-failure-" + std::to_wstring(
              static_cast<unsigned>(reservationFailure)));
      GraphStore failedReservation(failureRoot.wstring());
      const bool initialized = failedReservation.Initialize();
      const bool rejected = initialized && !failedReservation.Publish(
          Publication(records, index, 81), reservationFailure);
      GraphStore retryReservation(failureRoot.wstring());
      const bool retried = rejected && CandidateCount(failureRoot) == 0 &&
          retryReservation.Initialize() &&
          retryReservation.Publish(Publication(records, index, 82)) &&
          retryReservation.ActiveSerial() ==
              ((reservationFailure == FailurePoint::FlushReservation ||
                reservationFailure == FailurePoint::ReplaceReservation)
                   ? 2U : 1U);
      std::wcout << L"STORE_RESERVATION_FAILURE_FACT phase="
                 << static_cast<unsigned>(reservationFailure)
                 << L" initialized=" << initialized << L" rejected="
                 << rejected << L" retried=" << retried << L" serial="
                 << retryReservation.ActiveSerial() << L'\n';
      reservationFailureMatrix = retried && reservationFailureMatrix;
    }
    ok = Expect(
             reservationFailureMatrix,
             L"reservation open/write/flush/replace failures fail before "
             L"candidate mutation") && ok;

    const auto mixedRoot = root / L"mixed-abandoned-high-water";
    GraphStore mixed(mixedRoot.wstring());
    const bool mixedBase = mixed.Initialize() &&
        mixed.Publish(Publication(records, index, 83));
    const std::uint64_t mixedCommitted = mixed.ActiveSerial();
    const std::array mixedFailures{
        FailurePoint::OpenOwner, FailurePoint::SwapCleanupFailure,
        FailurePoint::OpenOwner};
    bool mixedAborts = mixedBase;
    for (const FailurePoint point : mixedFailures)
      mixedAborts = !mixed.Publish(Publication(records, index, 84), point) &&
          mixedAborts;
    GraphStore mixedRestartOne(mixedRoot.wstring());
    const bool mixedCleanup = mixedAborts && mixedRestartOne.Initialize() &&
        mixedRestartOne.ActiveSerial() == mixedCommitted;
    GraphStore mixedRestartTwo(mixedRoot.wstring());
    const bool mixedRetry = mixedCleanup && mixedRestartTwo.Initialize() &&
        mixedRestartTwo.Publish(Publication(records, index, 85));
    const bool mixedStrictlyMonotonic = mixedRetry &&
        mixedRestartTwo.ActiveSerial() ==
            mixedCommitted + mixedFailures.size() + 1;
    ok = Expect(
             mixedStrictlyMonotonic,
             L"committed and multiple abandoned allocations survive repeated "
             L"cleanup without reuse") && ok;

    bool corruptReservationRejected = true;
    for (const bool truncated : {false, true}) {
      const auto corruptReservationRoot = root /
          (truncated ? L"truncated-reservation" : L"corrupt-reservation");
      GraphStore createAuthority(corruptReservationRoot.wstring());
      bool prepared = createAuthority.Initialize();
      const auto authority = corruptReservationRoot / L"high-water.hgr";
      if (truncated) {
        HANDLE file = CreateFileW(authority.c_str(), GENERIC_WRITE, 0,
                                  nullptr, TRUNCATE_EXISTING,
                                  FILE_ATTRIBUTE_NORMAL, nullptr);
        const Bytes shortRecord{'H', 'G', 'R', '1'};
        DWORD written = 0;
        prepared = prepared && file != INVALID_HANDLE_VALUE &&
            WriteFile(file, shortRecord.data(),
                      static_cast<DWORD>(shortRecord.size()), &written,
                      nullptr) && written == shortRecord.size() &&
            FlushFileBuffers(file);
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
      } else {
        prepared = prepared && WriteHighWaterFixture(authority, 99, false);
      }
      GraphStore corruptAuthority(corruptReservationRoot.wstring());
      corruptReservationRejected = prepared &&
          !corruptAuthority.Initialize() &&
          !corruptAuthority.Publish(Publication(records, index, 86)) &&
          CandidateCount(corruptReservationRoot) == 0 &&
          corruptReservationRejected;
    }
    ok = Expect(
             corruptReservationRejected,
             L"corrupt and truncated reservation authority fail closed") &&
         ok;

    const bool coherentLegacySmoke = [&]() __declspec(noinline) {
    const auto coherentLegacyRoot = root / L"coherent-hgj1-migration";
    const auto coherentLegacyGeneration = coherentLegacyRoot /
        L"generation-00000000000000000001";
    std::filesystem::create_directory(coherentLegacyRoot, error);
    std::filesystem::copy(
        priorPath, coherentLegacyGeneration,
        std::filesystem::copy_options::recursive, error);
    const bool coherentLegacyWritten = !error &&
        ResealManifest(coherentLegacyGeneration / L"manifest.hgm") &&
        WriteLegacyJournalFixture(coherentLegacyRoot, {0, 1, 2});
    const auto coherentLegacyCommit =
        coherentLegacyGeneration / L"commit.hgc";
    const DWORD coherentLegacyCommitAttributes =
        GetFileAttributesW(coherentLegacyCommit.c_str());
    const bool legacyHgc1ReadonlyPrecondition = coherentLegacyWritten &&
        coherentLegacyCommitAttributes != INVALID_FILE_ATTRIBUTES &&
        (coherentLegacyCommitAttributes & FILE_ATTRIBUTE_READONLY) != 0;
    const Sha256 coherentLegacyBefore = TreeSnapshot(coherentLegacyRoot);
    GraphStore coherentLegacy(coherentLegacyRoot.wstring());
    const bool coherentLegacyInitialized = coherentLegacyWritten &&
        coherentLegacy.Initialize();
    GenerationPin coherentLegacyActive = coherentLegacy.PinActive();
    const DWORD migratedCommitAttributes =
        GetFileAttributesW(coherentLegacyCommit.c_str());
    const bool migratedHgc2Readonly = coherentLegacyInitialized &&
        migratedCommitAttributes != INVALID_FILE_ATTRIBUTES &&
        (migratedCommitAttributes & FILE_ATTRIBUTE_READONLY) != 0;
    HANDLE migratedCommitWriter = CreateFileW(
        coherentLegacyCommit.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    const bool migratedHgc2WriteReopenDenied = migratedHgc2Readonly &&
        migratedCommitWriter == INVALID_HANDLE_VALUE &&
        GetLastError() == ERROR_ACCESS_DENIED;
    if (migratedCommitWriter != INVALID_HANDLE_VALUE)
      CloseHandle(migratedCommitWriter);
    const bool coherentLegacyMigrated = legacyHgc1ReadonlyPrecondition &&
        coherentLegacyInitialized && coherentLegacyActive &&
        coherentLegacy.ActiveSerial() == 1 &&
        ReadPrior(coherentLegacyActive->Path()) && migratedHgc2Readonly &&
        migratedHgc2WriteReopenDenied &&
        coherentLegacy.Publish(Publication(records, index, 112)) &&
        coherentLegacy.ActiveSerial() == 3 &&
        std::filesystem::exists(
            coherentLegacyRoot / L"store-identity.hgs");
    const bool rejectedLegacyMutated = !coherentLegacyInitialized &&
        !Equal(coherentLegacyBefore, TreeSnapshot(coherentLegacyRoot));
    std::wcout << L"STORE_LEGACY_HGC1_READONLY_PRECONDITION "
               << legacyHgc1ReadonlyPrecondition << L'\n'
               << L"STORE_COHERENT_HGJ1_MIGRATION "
               << coherentLegacyMigrated << L'\n'
               << L"STORE_MIGRATED_HGC2_READONLY "
               << migratedHgc2Readonly << L'\n'
               << L"STORE_MIGRATED_HGC2_WRITE_REOPEN_DENIED "
               << migratedHgc2WriteReopenDenied << L'\n'
               << L"STORE_REJECTED_LEGACY_ZERO_MUTATION "
               << !rejectedLegacyMutated << L'\n';
    return coherentLegacyMigrated;
    }();
    ok = Expect(
             coherentLegacySmoke,
             L"coherent HGJ1/HGR2/HGC1 root migrates transactionally") && ok;

    const auto intentTamperRoot = root / L"intent-source-tamper";
    const auto intentTamperGeneration = intentTamperRoot /
        L"generation-00000000000000000001";
    std::filesystem::create_directory(intentTamperRoot, error);
    std::filesystem::copy(
        priorPath, intentTamperGeneration,
        std::filesystem::copy_options::recursive, error);
    const bool intentTamperPrepared = !error &&
        ResealManifest(intentTamperGeneration / L"manifest.hgm") &&
        WriteLegacyJournalFixture(intentTamperRoot, {0, 1, 2});
    GraphStore interruptedMigration(intentTamperRoot.wstring());
    const bool intentPersisted = intentTamperPrepared &&
        !interruptedMigration.Initialize(FailurePoint::MigrationAfterIntent) &&
        std::filesystem::exists(intentTamperRoot / L"migration.intent") &&
        !std::filesystem::exists(intentTamperRoot / L"store-identity.hgs");
    const auto tamperedLegacyJournal = intentTamperRoot / L"reservations.hgj";
    HANDLE tamperFile = CreateFileW(
        tamperedLegacyJournal.c_str(), FILE_APPEND_DATA, 0, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    const std::uint8_t tamperByte = 0xa5;
    DWORD tamperWritten = 0;
    const bool sourceTampered = intentPersisted &&
        tamperFile != INVALID_HANDLE_VALUE &&
        WriteFile(tamperFile, &tamperByte, 1, &tamperWritten, nullptr) &&
        tamperWritten == 1 && FlushFileBuffers(tamperFile);
    if (tamperFile != INVALID_HANDLE_VALUE) CloseHandle(tamperFile);
    const Sha256 interruptedSnapshot = TreeSnapshot(intentTamperRoot);
    GraphStore rejectedResume(intentTamperRoot.wstring());
    const bool tamperedResumeRejected = sourceTampered &&
        !rejectedResume.Initialize() &&
        !std::filesystem::exists(intentTamperRoot / L"store-identity.hgs");
    const bool rejectedResumeZeroMutation = tamperedResumeRejected &&
        Equal(interruptedSnapshot, TreeSnapshot(intentTamperRoot));
    ok = Expect(
             tamperedResumeRejected && rejectedResumeZeroMutation,
             L"intent rejects modified legacy inventory before resume mutation") &&
         ok;
    std::wcout << L"STORE_MIGRATION_INTENT_SOURCE_TAMPER_REJECTED "
               << tamperedResumeRejected << L'\n'
               << L"STORE_MIGRATION_INTENT_REJECT_ZERO_MUTATION "
               << rejectedResumeZeroMutation << L'\n';

    const auto stageTamperRoot = root / L"intent-stage-tamper";
    const auto stageTamperGeneration = stageTamperRoot /
        L"generation-00000000000000000001";
    std::filesystem::create_directory(stageTamperRoot, error);
    std::filesystem::copy(
        priorPath, stageTamperGeneration,
        std::filesystem::copy_options::recursive, error);
    const bool stageIntentPrepared = !error &&
        ResealManifest(stageTamperGeneration / L"manifest.hgm") &&
        WriteLegacyJournalFixture(stageTamperRoot, {0, 1, 2});
    GraphStore stageInterrupted(stageTamperRoot.wstring());
    const bool stageIntentPersisted = stageIntentPrepared &&
        !stageInterrupted.Initialize(FailurePoint::MigrationAfterIntent);
    const auto stagedCheckpoint =
        stageTamperRoot / L"migration-stage" / L"high-water.hgr";
    HANDLE stageFile = CreateFileW(
        stagedCheckpoint.c_str(), FILE_APPEND_DATA, 0, nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD stageWritten = 0;
    const bool stageTampered = stageIntentPersisted &&
        stageFile != INVALID_HANDLE_VALUE &&
        WriteFile(stageFile, &tamperByte, 1, &stageWritten, nullptr) &&
        stageWritten == 1 && FlushFileBuffers(stageFile);
    if (stageFile != INVALID_HANDLE_VALUE) CloseHandle(stageFile);
    const Sha256 stageTamperBefore = TreeSnapshot(stageTamperRoot);
    GraphStore stageRejected(stageTamperRoot.wstring());
    const bool stageTamperRejected = stageTampered &&
        !stageRejected.Initialize() &&
        Equal(stageTamperBefore, TreeSnapshot(stageTamperRoot)) &&
        !std::filesystem::exists(stageTamperRoot / L"store-identity.hgs");

    const auto copiedIntentRoot = root / L"intent-copy-target";
    std::filesystem::copy(
        stageTamperRoot, copiedIntentRoot,
        std::filesystem::copy_options::recursive, error);
    const Sha256 copiedIntentBefore = TreeSnapshot(copiedIntentRoot);
    GraphStore copiedIntent(copiedIntentRoot.wstring());
    const bool copiedIntentRejected = !error && !copiedIntent.Initialize() &&
        Equal(copiedIntentBefore, TreeSnapshot(copiedIntentRoot)) &&
        !std::filesystem::exists(copiedIntentRoot / L"store-identity.hgs");
    ok = Expect(
             stageTamperRejected && copiedIntentRejected,
             L"intent authenticates exact stage receipts and canonical root") &&
         ok;
    std::wcout << L"STORE_MIGRATION_INTENT_STAGE_TAMPER_REJECTED "
               << stageTamperRejected << L'\n'
               << L"STORE_MIGRATION_INTENT_FOREIGN_ROOT_REJECTED "
               << copiedIntentRejected << L'\n';

    bool validMigrationResumeMatrix = true;
    for (const FailurePoint stage : {
             FailurePoint::MigrationAfterIntent,
             FailurePoint::MigrationAfterJournal,
             FailurePoint::MigrationAfterCheckpoint,
             FailurePoint::MigrationAfterMarkers,
             FailurePoint::MigrationAfterReadonlyClear,
             FailurePoint::MigrationAfterMarkerReplace}) {
      const auto resumeRoot = root /
          (L"valid-migration-resume-" +
           std::to_wstring(static_cast<unsigned>(stage)));
      const auto resumeGeneration = resumeRoot /
          L"generation-00000000000000000001";
      std::filesystem::create_directory(resumeRoot, error);
      std::filesystem::copy(
          priorPath, resumeGeneration,
          std::filesystem::copy_options::recursive, error);
      const bool resumePrepared = !error &&
          ResealManifest(resumeGeneration / L"manifest.hgm") &&
          WriteLegacyJournalFixture(resumeRoot, {0, 1, 2});
      GraphStore interrupted(resumeRoot.wstring());
      const bool interruptedAtStage = resumePrepared &&
          !interrupted.Initialize(stage) &&
          !std::filesystem::exists(resumeRoot / L"store-identity.hgs");
      GraphStore resumed(resumeRoot.wstring());
      const bool resumedInitialized = resumed.Initialize();
      const bool stageResult = interruptedAtStage && resumedInitialized &&
          resumed.ActiveSerial() == 1 && resumed.PinActive() &&
          std::filesystem::exists(resumeRoot / L"store-identity.hgs") &&
          !std::filesystem::exists(resumeRoot / L"migration.intent");
      validMigrationResumeMatrix =
          stageResult && validMigrationResumeMatrix;
    }
    ok = Expect(
             validMigrationResumeMatrix,
             L"every valid migration crash stage resumes one epoch lineage") &&
         ok;
    std::wcout << L"STORE_MIGRATION_INTENT_VALID_RESUME_MATRIX "
               << validMigrationResumeMatrix << L" stages=6\n";

    bool atomicMigrationReplacementMatrix = true;
    for (const FailurePoint phase : {
             FailurePoint::MigrationTempWrite,
             FailurePoint::MigrationTempFlush,
             FailurePoint::MigrationTempReplace}) {
      const auto atomicRoot = root /
          (L"atomic-migration-replace-" +
           std::to_wstring(static_cast<unsigned>(phase)));
      const auto atomicGeneration = atomicRoot /
          L"generation-00000000000000000001";
      std::filesystem::create_directory(atomicRoot, error);
      std::filesystem::copy(
          priorPath, atomicGeneration,
          std::filesystem::copy_options::recursive, error);
      const bool atomicPrepared = !error &&
          ResealManifest(atomicGeneration / L"manifest.hgm") &&
          WriteLegacyJournalFixture(atomicRoot, {0, 1, 2});
      const auto journalPath = atomicRoot / L"reservations.hgj";
      const Sha256 journalBefore = FileSnapshot(journalPath);
      GraphStore interruptedAtomic(atomicRoot.wstring());
      const bool failedWithTemp = atomicPrepared &&
          !interruptedAtomic.Initialize(phase) &&
          Equal(journalBefore, FileSnapshot(journalPath)) &&
          std::filesystem::exists(
              atomicRoot / L"reservations.hgj.migration.tmp") &&
          !std::filesystem::exists(atomicRoot / L"store-identity.hgs");
      GraphStore resumedAtomic(atomicRoot.wstring());
      const bool resumedAfterAtomicFailure = failedWithTemp &&
          resumedAtomic.Initialize() && resumedAtomic.ActiveSerial() == 1 &&
          resumedAtomic.PinActive() &&
          !std::filesystem::exists(
              atomicRoot / L"reservations.hgj.migration.tmp") &&
          !std::filesystem::exists(atomicRoot / L"migration.intent");
      atomicMigrationReplacementMatrix = resumedAfterAtomicFailure &&
          atomicMigrationReplacementMatrix;
      error.clear();
    }
    ok = Expect(
             atomicMigrationReplacementMatrix,
             L"same-directory durable temp write/flush/replace failures keep "
             L"old authority intact and resume atomically") &&
         ok;
    std::wcout << L"STORE_MIGRATION_ATOMIC_REPLACEMENT_MATRIX "
               << atomicMigrationReplacementMatrix << L" phases=3\n";

    const auto invalidLegacyRoot = root / L"invalid-hgj1-zero-mutation";
    std::filesystem::create_directory(invalidLegacyRoot, error);
    const bool invalidLegacyPrepared = WriteLegacyJournalFixture(
        invalidLegacyRoot, {0, 1, 2});
    HANDLE invalidLegacyJournal = CreateFileW(
        (invalidLegacyRoot / L"reservations.hgj").c_str(), GENERIC_WRITE, 0,
        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    LARGE_INTEGER corruptOffset{};
    corruptOffset.QuadPart = 80 + 48;
    std::uint8_t corruptByte = 0xff;
    DWORD corruptWritten = 0;
    const bool invalidLegacyCorrupted = invalidLegacyPrepared &&
        invalidLegacyJournal != INVALID_HANDLE_VALUE &&
        SetFilePointerEx(invalidLegacyJournal, corruptOffset, nullptr,
                         FILE_BEGIN) &&
        WriteFile(invalidLegacyJournal, &corruptByte, 1, &corruptWritten,
                  nullptr) && corruptWritten == 1 &&
        FlushFileBuffers(invalidLegacyJournal);
    if (invalidLegacyJournal != INVALID_HANDLE_VALUE)
      CloseHandle(invalidLegacyJournal);
    const Sha256 invalidLegacyBefore = TreeSnapshot(invalidLegacyRoot);
    GraphStore invalidLegacy(invalidLegacyRoot.wstring());
    const bool invalidLegacyZeroMutation = invalidLegacyCorrupted &&
        !invalidLegacy.Initialize() &&
        Equal(invalidLegacyBefore, TreeSnapshot(invalidLegacyRoot)) &&
        !std::filesystem::exists(
            invalidLegacyRoot / L"store-identity.hgs");
    ok = Expect(
             invalidLegacyZeroMutation,
             L"invalid HGJ1 rejection is byte-for-byte non-mutating") && ok;
    std::wcout << L"STORE_INVALID_HGJ1_ZERO_MUTATION "
               << invalidLegacyZeroMutation << L'\n';

    const auto legacyRoot = root / L"legacy-high-water-bootstrap";
    std::filesystem::create_directory(legacyRoot, error);
    const bool legacyWritten = WriteHighWaterFixture(
        legacyRoot / L"high-water.hgr", 5);
    GraphStore legacyBootstrap(legacyRoot.wstring());
    const bool legacyInitialized = legacyWritten && legacyBootstrap.Initialize();
    const bool legacyPublished = legacyInitialized &&
        legacyBootstrap.Publish(Publication(records, index, 96));
    const auto legacyJournalBytes = std::filesystem::file_size(
        legacyRoot / L"reservations.hgj", error);
    const bool legacyMigrated = legacyPublished &&
        legacyBootstrap.ActiveSerial() == 6 && legacyJournalBytes == 192;
    std::wcout << L"STORE_LEGACY_BOOTSTRAP_FACT init=" << legacyInitialized
               << L" publish=" << legacyPublished << L" serial="
               << legacyBootstrap.ActiveSerial() << L" journal="
               << legacyJournalBytes << L'\n';
    ok = Expect(
             legacyMigrated,
             L"legacy valid high-water bootstraps independent witness without "
             L"regression") && ok;

    const auto staleRoot = root / L"stale-reservation-temp";
    GraphStore staleBase(staleRoot.wstring());
    const bool stalePrepared = staleBase.Initialize() &&
        staleBase.Publish(Publication(records, index, 87)) &&
        WriteHighWaterFixture(staleRoot / L"high-water-stale.tmp", 999);
    GraphStore staleRestart(staleRoot.wstring());
    const bool staleIgnored = stalePrepared && staleRestart.Initialize() &&
        staleRestart.Publish(Publication(records, index, 88)) &&
        staleRestart.ActiveSerial() == 2 &&
        !std::filesystem::exists(staleRoot / L"high-water-stale.tmp");
    ok = Expect(
             staleIgnored,
             L"stale high-water temp cannot advance or regress authority") &&
         ok;

    const auto aliasPhysicalRoot = root / L"alias-concurrent-initialize";
    std::filesystem::create_directory(aliasPhysicalRoot, error);
    std::wstring aliasText = aliasPhysicalRoot.wstring();
    CharUpperBuffW(aliasText.data(), static_cast<DWORD>(aliasText.size()));
    const std::wstring physicalText = aliasPhysicalRoot.wstring();
    const auto directoryIdentity = [](const std::wstring &path,
                                      FILE_ID_INFO *identity) {
      HANDLE directory = CreateFileW(
          path.c_str(), FILE_READ_ATTRIBUTES,
          FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
          OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
      const bool valid = directory != INVALID_HANDLE_VALUE &&
          GetFileInformationByHandleEx(directory, FileIdInfo, identity,
                                       sizeof(*identity)) != FALSE;
      if (directory != INVALID_HANDLE_VALUE) CloseHandle(directory);
      return valid;
    };
    FILE_ID_INFO physicalDirectoryId{}, aliasDirectoryId{};
    Sha256 physicalAuthority{}, aliasAuthority{};
    const bool aliasesSameDirectory = physicalText != aliasText &&
        directoryIdentity(physicalText, &physicalDirectoryId) &&
        directoryIdentity(aliasText, &aliasDirectoryId) &&
        physicalDirectoryId.VolumeSerialNumber ==
            aliasDirectoryId.VolumeSerialNumber &&
        std::equal(std::begin(physicalDirectoryId.FileId.Identifier),
                   std::end(physicalDirectoryId.FileId.Identifier),
                   std::begin(aliasDirectoryId.FileId.Identifier));
    const bool aliasAuthoritySame = aliasesSameDirectory &&
        ResolveStoreAuthorityIdentity(physicalText, &physicalAuthority) &&
        ResolveStoreAuthorityIdentity(aliasText, &aliasAuthority) &&
        Equal(physicalAuthority, aliasAuthority);

    const auto leasePhysicalRoot = root / L"alias-generation-lease";
    std::filesystem::create_directory(leasePhysicalRoot, error);
    std::wstring leaseAliasText = leasePhysicalRoot.wstring();
    CharUpperBuffW(leaseAliasText.data(),
                   static_cast<DWORD>(leaseAliasText.size()));
    const std::wstring leasePhysicalText = leasePhysicalRoot.wstring();
    bool leaseExistingPublished = false;
    {
      GraphStore bootstrap(leasePhysicalText);
      leaseExistingPublished = bootstrap.Initialize() &&
          bootstrap.Publish(Publication(records, index, 115));
    }
    const auto leasedGenerationOne = leasePhysicalRoot /
        L"generation-00000000000000000001";
    const auto leasedGenerationTwo = leasePhysicalRoot /
        L"generation-00000000000000000002";
    bool leaseInitializedA = false, leaseInitializedB = false;
    bool leasePublishedTwo = false, leaseReadableWhilePinned = false;
    std::uint64_t leaseSerialA = 0, leaseSerialB = 0;
    GenerationPin leasePinB;
    {
      GraphStore leaseStoreA(leasePhysicalText);
      GraphStore leaseStoreB(leaseAliasText);
      HANDLE leaseInitializeStart =
          CreateEventW(nullptr, TRUE, FALSE, nullptr);
      std::thread leaseInitializerA([&]() {
        WaitForSingleObject(leaseInitializeStart, INFINITE);
        leaseInitializedA = leaseStoreA.Initialize();
      });
      std::thread leaseInitializerB([&]() {
        WaitForSingleObject(leaseInitializeStart, INFINITE);
        leaseInitializedB = leaseStoreB.Initialize();
      });
      SetEvent(leaseInitializeStart);
      leaseInitializerA.join();
      leaseInitializerB.join();
      if (leaseInitializeStart != nullptr) CloseHandle(leaseInitializeStart);
      leasePinB = leaseStoreB.PinActive();
      const std::wstring pinnedGeneration =
          leasePinB ? leasePinB->Path() : L"";
      PinnedGenerationSnapshot leaseBefore{}, leaseAfter{};
      const bool leaseBeforeReady = leasePinB &&
          ReadPinnedGenerationSnapshot(pinnedGeneration, &leaseBefore);
      leasePublishedTwo = leaseStoreA.Publish(
          Publication(records, index, 116));
      const bool leaseAfterReady = leasePinB &&
          ReadPinnedGenerationSnapshot(pinnedGeneration, &leaseAfter);
      const bool leaseDeferralZeroMutation = leaseBeforeReady &&
          leaseAfterReady &&
          leaseBefore.recordsBytes == leaseAfter.recordsBytes &&
          Equal(leaseBefore.recordsHash, leaseAfter.recordsHash) &&
          leaseBefore.indexBytes == leaseAfter.indexBytes &&
          Equal(leaseBefore.indexHash, leaseAfter.indexHash) &&
          Equal(leaseBefore.observedSemanticRoot,
                leaseAfter.observedSemanticRoot) &&
          Equal(leaseBefore.captureRoot, leaseAfter.captureRoot) &&
          leaseBefore.publishedFiles == leaseAfter.publishedFiles;
      leaseSerialA = leaseStoreA.ActiveSerial();
      leaseSerialB = leaseStoreB.ActiveSerial();
      leaseReadableWhilePinned = leasePinB && leaseDeferralZeroMutation &&
          std::filesystem::exists(pinnedGeneration) &&
          std::filesystem::exists(leasedGenerationOne) &&
          std::filesystem::exists(leasedGenerationTwo) &&
          ReadPrior(pinnedGeneration);
      ok = Expect(
               leaseExistingPublished && leaseInitializedA &&
                   leaseInitializedB && leasePublishedTwo &&
                   leaseSerialA == 2 && leaseSerialB == 1 &&
                   leaseReadableWhilePinned,
               L"alias generation remains readable while another store and "
               L"real pin retain OS leases") && ok;
      std::wcout << L"STORE_ALIAS_GENERATION_LEASE_RETAINED "
                 << leaseReadableWhilePinned << L" init_a="
                 << leaseInitializedA << L" init_b=" << leaseInitializedB
                 << L" publish_2=" << leasePublishedTwo << L" serial_a="
                 << leaseSerialA << L" serial_b=" << leaseSerialB << L'\n'
                 << L"STORE_LEASE_DEFERRAL_ZERO_CHILD_MUTATION "
                 << leaseDeferralZeroMutation << L'\n';
    }
    const bool leasePinOutlivesStores = leasePinB &&
        std::filesystem::exists(leasedGenerationOne) &&
        ReadPrior(leasePinB->Path());
    const auto directLeaseProbe = root / L"directory-lease-probe";
    std::filesystem::create_directory(directLeaseProbe, error);
    HANDLE directLeaseA = CreateFileW(
        directLeaseProbe.c_str(), FILE_LIST_DIRECTORY,
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    HANDLE directLeaseB = CreateFileW(
        directLeaseProbe.c_str(), FILE_LIST_DIRECTORY,
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    HANDLE directDeleteClaim = CreateFileW(
        directLeaseProbe.c_str(), DELETE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
        OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    const bool directBothBlock = directLeaseA != INVALID_HANDLE_VALUE &&
        directLeaseB != INVALID_HANDLE_VALUE &&
        directDeleteClaim == INVALID_HANDLE_VALUE &&
        GetLastError() == ERROR_SHARING_VIOLATION;
    if (directDeleteClaim != INVALID_HANDLE_VALUE) CloseHandle(directDeleteClaim);
    if (directLeaseA != INVALID_HANDLE_VALUE) CloseHandle(directLeaseA);
    directDeleteClaim = CreateFileW(
        directLeaseProbe.c_str(), DELETE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
        OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    const bool directOneBlocks = directDeleteClaim == INVALID_HANDLE_VALUE &&
        GetLastError() == ERROR_SHARING_VIOLATION;
    if (directDeleteClaim != INVALID_HANDLE_VALUE) CloseHandle(directDeleteClaim);
    if (directLeaseB != INVALID_HANDLE_VALUE) CloseHandle(directLeaseB);
    directDeleteClaim = CreateFileW(
        directLeaseProbe.c_str(), DELETE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
        OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    const bool directReleased = directDeleteClaim != INVALID_HANDLE_VALUE;
    if (directDeleteClaim != INVALID_HANDLE_VALUE) CloseHandle(directDeleteClaim);
    std::filesystem::remove(directLeaseProbe, error);
    ok = Expect(leasePinOutlivesStores && directBothBlock && directOneBlocks &&
                    directReleased,
                L"real pin and each independent directory handle deny delete "
                L"sharing until both handles close") && ok;
    std::wcout << L"STORE_ALIAS_GENERATION_PIN_OUTLIVES_STORES "
               << leasePinOutlivesStores << L'\n'
               << L"STORE_DIRECTORY_LEASE_DELETE_SHARING "
               << (directBothBlock && directOneBlocks && directReleased)
               << L'\n';
    leasePinB.reset();
    GraphStore leaseCleanupTrigger(leaseAliasText);
    const bool leaseCleanupInitialized = leaseCleanupTrigger.Initialize();
    const bool leaseDeferredCleanup = leaseCleanupInitialized &&
        leaseCleanupTrigger.ActiveSerial() == 2 &&
        !std::filesystem::exists(leasedGenerationOne) &&
        std::filesystem::exists(leasedGenerationTwo) &&
        ReadPrior(leasedGenerationTwo.wstring());
    ok = Expect(
             leaseDeferredCleanup,
             L"later serialized initialize removes unleased retired "
             L"generation without retiring current generation") && ok;
    std::wcout << L"STORE_ALIAS_GENERATION_DEFERRED_CLEANUP "
               << leaseDeferredCleanup << L" trigger="
               << leaseCleanupInitialized << L" current="
               << leaseCleanupTrigger.ActiveSerial() << L'\n';

    GraphStore aliasStoreA(physicalText);
    GraphStore aliasStoreB(aliasText);
    HANDLE aliasRecordsA = INVALID_HANDLE_VALUE;
    HANDLE aliasIndexA = INVALID_HANDLE_VALUE;
    HANDLE aliasRecordsB = INVALID_HANDLE_VALUE;
    HANDLE aliasIndexB = INVALID_HANDLE_VALUE;
    const bool aliasSources = CreateScaleSpool(
        root / L"alias-records-a.bin", root / L"alias-index-a.bin",
        &aliasRecordsA, &aliasIndexA, 1) && CreateScaleSpool(
        root / L"alias-records-b.bin", root / L"alias-index-b.bin",
        &aliasRecordsB, &aliasIndexB, 1);
    HANDLE aliasInitializeStart = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    bool aliasInitializedA = false, aliasInitializedB = false;
    std::thread aliasInitializerA([&]() {
      WaitForSingleObject(aliasInitializeStart, INFINITE);
      aliasInitializedA = aliasStoreA.Initialize();
    });
    std::thread aliasInitializerB([&]() {
      WaitForSingleObject(aliasInitializeStart, INFINITE);
      aliasInitializedB = aliasStoreB.Initialize();
    });
    SetEvent(aliasInitializeStart);
    aliasInitializerA.join();
    aliasInitializerB.join();
    if (aliasInitializeStart != nullptr) CloseHandle(aliasInitializeStart);
    HANDLE aliasPublishStart = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    bool aliasPublishedA = false, aliasPublishedB = false;
    std::thread aliasPublisherA([&]() {
      WaitForSingleObject(aliasPublishStart, INFINITE);
      aliasPublishedA = aliasStoreA.Publish(
          Publication(aliasRecordsA, aliasIndexA, 113));
    });
    std::thread aliasPublisherB([&]() {
      WaitForSingleObject(aliasPublishStart, INFINITE);
      aliasPublishedB = aliasStoreB.Publish(
          Publication(aliasRecordsB, aliasIndexB, 114));
    });
    SetEvent(aliasPublishStart);
    aliasPublisherA.join();
    aliasPublisherB.join();
    if (aliasPublishStart != nullptr) CloseHandle(aliasPublishStart);
    if (aliasRecordsA != INVALID_HANDLE_VALUE) CloseHandle(aliasRecordsA);
    if (aliasIndexA != INVALID_HANDLE_VALUE) CloseHandle(aliasIndexA);
    if (aliasRecordsB != INVALID_HANDLE_VALUE) CloseHandle(aliasRecordsB);
    if (aliasIndexB != INVALID_HANDLE_VALUE) CloseHandle(aliasIndexB);
    GraphStore aliasRestart(aliasText);
    const bool aliasRestarted = aliasRestart.Initialize();
    const auto aliasJournal = aliasPhysicalRoot / L"reservations.hgj";
    const auto aliasCheckpoint = aliasPhysicalRoot / L"high-water.hgr";
    const auto readEpoch = [](const std::filesystem::path &path,
                              DWORD offset,
                              std::array<std::uint8_t, 16> *epoch) {
      HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ,
                                nullptr, OPEN_EXISTING,
                                FILE_ATTRIBUTE_NORMAL, nullptr);
      LARGE_INTEGER position{};
      position.QuadPart = offset;
      DWORD read = 0;
      const bool valid = file != INVALID_HANDLE_VALUE &&
          SetFilePointerEx(file, position, nullptr, FILE_BEGIN) != FALSE &&
          ReadFile(file, epoch->data(), static_cast<DWORD>(epoch->size()),
                   &read, nullptr) != FALSE && read == epoch->size();
      if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
      return valid;
    };
    std::array<std::uint8_t, 16> aliasIdentityEpoch{}, aliasCommitEpoch1{},
        aliasCommitEpoch2{};
    const bool aliasOneEpoch = readEpoch(
        aliasPhysicalRoot / L"store-identity.hgs", 8, &aliasIdentityEpoch) &&
        readEpoch(aliasPhysicalRoot /
                      L"generation-00000000000000000001" / L"commit.hgc",
                  16, &aliasCommitEpoch1) &&
        readEpoch(aliasPhysicalRoot /
                      L"generation-00000000000000000002" / L"commit.hgc",
                  16, &aliasCommitEpoch2) &&
        aliasIdentityEpoch == aliasCommitEpoch1 &&
        aliasIdentityEpoch == aliasCommitEpoch2;
    const bool aliasNoResidue = CandidateCount(aliasPhysicalRoot) == 0 &&
        !std::filesystem::exists(aliasPhysicalRoot / L"migration.intent") &&
        !std::filesystem::exists(aliasPhysicalRoot / L"migration-stage") &&
        !std::filesystem::exists(
            aliasPhysicalRoot / L"store-identity.hgs.migration.tmp") &&
        !std::filesystem::exists(
            aliasPhysicalRoot / L"reservations.hgj.migration.tmp") &&
        !std::filesystem::exists(
            aliasPhysicalRoot / L"high-water.hgr.migration.tmp");
    const bool aliasConcurrentInitialize = aliasAuthoritySame && aliasSources &&
        aliasInitializedA && aliasInitializedB && aliasPublishedA &&
        aliasPublishedB && aliasRestarted && aliasRestart.ActiveSerial() == 2 &&
        std::filesystem::file_size(aliasJournal, error) == 288 &&
        std::filesystem::file_size(aliasCheckpoint, error) == 96 &&
        std::filesystem::exists(
            aliasPhysicalRoot / L"generation-00000000000000000001" /
            L"commit.hgc") &&
        std::filesystem::exists(
            aliasPhysicalRoot / L"generation-00000000000000000002" /
            L"commit.hgc") && aliasOneEpoch && aliasNoResidue;
    ok = Expect(
             aliasConcurrentInitialize,
             L"canonical aliases serialize concurrent initialization and "
             L"publication into one epoch lineage") && ok;
    std::wcout << L"STORE_ALIAS_DIRECTORY_IDENTITY_EQUAL "
               << aliasesSameDirectory << L'\n'
               << L"STORE_ALIAS_ALLOCATOR_IDENTITY_EQUAL "
               << aliasAuthoritySame << L'\n'
               << L"STORE_ALIAS_CONCURRENT_INITIALIZE_ONE_EPOCH "
               << aliasConcurrentInitialize << L" init_a="
               << aliasInitializedA << L" init_b=" << aliasInitializedB
               << L" publish_a=" << aliasPublishedA << L" publish_b="
               << aliasPublishedB << L" serial="
               << aliasRestart.ActiveSerial() << L" epoch_match="
               << aliasOneEpoch << L" journal="
               << std::filesystem::file_size(aliasJournal, error)
               << L" checkpoint="
               << std::filesystem::file_size(aliasCheckpoint, error)
               << L" residue=" << !aliasNoResidue << L'\n';

    const auto concurrentRoot = root / L"concurrent-reservation";
    GraphStore concurrentA(concurrentRoot.wstring());
    GraphStore concurrentB(concurrentRoot.wstring());
    HANDLE concurrentRecordsA = INVALID_HANDLE_VALUE;
    HANDLE concurrentIndexA = INVALID_HANDLE_VALUE;
    HANDLE concurrentRecordsB = INVALID_HANDLE_VALUE;
    HANDLE concurrentIndexB = INVALID_HANDLE_VALUE;
    const bool concurrentSources = CreateScaleSpool(
        root / L"concurrent-records-a.bin",
        root / L"concurrent-index-a.bin", &concurrentRecordsA,
        &concurrentIndexA, 1) && CreateScaleSpool(
        root / L"concurrent-records-b.bin",
        root / L"concurrent-index-b.bin", &concurrentRecordsB,
        &concurrentIndexB, 1);
    HANDLE concurrentStart = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    bool concurrentResultA = false, concurrentResultB = false;
    const bool concurrentReady = concurrentSources && concurrentA.Initialize() &&
        concurrentB.Initialize() && concurrentStart != nullptr &&
        concurrentRecordsA != INVALID_HANDLE_VALUE &&
        concurrentIndexA != INVALID_HANDLE_VALUE &&
        concurrentRecordsB != INVALID_HANDLE_VALUE &&
        concurrentIndexB != INVALID_HANDLE_VALUE;
    std::thread allocatorA([&]() {
      WaitForSingleObject(concurrentStart, INFINITE);
      concurrentResultA = concurrentA.Publish(
          Publication(concurrentRecordsA, concurrentIndexA, 89));
    });
    std::thread allocatorB([&]() {
      WaitForSingleObject(concurrentStart, INFINITE);
      concurrentResultB = concurrentB.Publish(
          Publication(concurrentRecordsB, concurrentIndexB, 90));
    });
    SetEvent(concurrentStart);
    allocatorA.join();
    allocatorB.join();
    if (concurrentStart != nullptr) CloseHandle(concurrentStart);
    if (concurrentRecordsA != INVALID_HANDLE_VALUE) CloseHandle(concurrentRecordsA);
    if (concurrentIndexA != INVALID_HANDLE_VALUE) CloseHandle(concurrentIndexA);
    if (concurrentRecordsB != INVALID_HANDLE_VALUE) CloseHandle(concurrentRecordsB);
    if (concurrentIndexB != INVALID_HANDLE_VALUE) CloseHandle(concurrentIndexB);
    GraphStore concurrentRestart(concurrentRoot.wstring());
    const bool concurrentRestartInitialized = concurrentRestart.Initialize();
    const bool concurrentUnique = concurrentReady && concurrentResultA &&
        concurrentResultB && concurrentRestartInitialized &&
        concurrentRestart.ActiveSerial() == 2;
    std::wcout << L"STORE_DURABLE_HIGH_WATER_CONCURRENT_FACTS ready="
               << concurrentReady << L" a=" << concurrentResultA << L" b="
               << concurrentResultB << L" restart="
               << concurrentRestartInitialized << L" serial="
               << concurrentRestart.ActiveSerial() << L'\n';
    ok = Expect(
             concurrentUnique,
             L"concurrent GraphStore allocators receive unique durable serials") &&
         ok;
    std::wcout << L"STORE_DURABLE_RESERVATION_FAILURE_MATRIX "
               << reservationFailureMatrix << L" phases=4\n"
               << L"STORE_DURABLE_HIGH_WATER_MIXED_RESTART "
               << mixedStrictlyMonotonic << L'\n'
               << L"STORE_DURABLE_HIGH_WATER_CORRUPTION_FAIL_CLOSED "
               << corruptReservationRejected << L'\n'
               << L"STORE_DURABLE_HIGH_WATER_LEGACY_BOOTSTRAP "
               << legacyMigrated << L'\n'
               << L"STORE_DURABLE_HIGH_WATER_STALE_TEMP_IGNORED "
               << staleIgnored << L'\n'
               << L"STORE_DURABLE_HIGH_WATER_CONCURRENT_UNIQUE "
               << concurrentUnique << L'\n';

    PublicationInput arbitraryEqual = Publication(records, index, 3);
    arbitraryEqual.firstRestoration.route = StreamFact({9});
    arbitraryEqual.secondRestoration.route = StreamFact({9});
    ok = Expect(!store.Publish(arbitraryEqual),
                L"equal restorations differing from baseline rejected") &&
         ok;
    PublicationInput routeVariant = Publication(records, index, 4);
    routeVariant.baseline.route = StreamFact({99, 100});
    routeVariant.firstRestoration = routeVariant.baseline;
    routeVariant.secondRestoration = routeVariant.baseline;
    const bool routePublished = store.Publish(routeVariant);
    GenerationPin activeAfterRoute = store.PinActive();
    ok = Expect(Equal(input.first.observedSemanticRoot,
                      routeVariant.first.observedSemanticRoot) &&
                    routePublished && activeAfterRoute,
                L"captured route bytes differ without entering graph roots") &&
         ok;
    PublicationInput next = Publication(records, index, 5);
    const bool nextPublished = store.Publish(next);
    GenerationPin activeAfterNext = store.PinActive();
    ok = Expect(nextPublished && activeAfterNext &&
                    std::filesystem::exists(priorPath),
                L"pinned prior survives commit") &&
         ok;
    const auto publishedRecords = activeAfterNext
        ? std::filesystem::path(activeAfterNext->Path()) / L"records.hgn"
        : std::filesystem::path{};
    HANDLE writable = activeAfterNext
        ? CreateFileW(publishedRecords.c_str(), GENERIC_WRITE, FILE_SHARE_READ,
                      nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr)
        : INVALID_HANDLE_VALUE;
    ok = Expect(writable == INVALID_HANDLE_VALUE,
                L"published records deny GENERIC_WRITE reopen") &&
         ok;
    if (writable != INVALID_HANDLE_VALUE)
      CloseHandle(writable);
    prior.reset();
    GraphStore retiredCleanup(root.wstring());
    const bool retiredCleanupInitialized = retiredCleanup.Initialize();
    ok = Expect(retiredCleanupInitialized &&
                    !std::filesystem::exists(priorPath),
                L"old generation deleted by later lifecycle cleanup after "
                L"final pin release") && ok;
  }
  std::filesystem::path latest;
  for (const auto &entry : std::filesystem::directory_iterator(root, error))
    if (entry.path().filename().wstring().rfind(L"generation-", 0) == 0)
      latest = entry.path();
  const auto corruptRoot = root / L"corrupt-root-metadata";
  const auto corruptGeneration =
      corruptRoot / L"generation-00000000000000000001";
  if (!latest.empty()) {
    std::filesystem::create_directory(corruptRoot, error);
    std::filesystem::copy(latest, corruptGeneration,
                          std::filesystem::copy_options::recursive, error);
    const auto corruptManifest = corruptGeneration / L"manifest.hgm";
    SetFileAttributesW(corruptManifest.c_str(), FILE_ATTRIBUTE_NORMAL);
    HANDLE corrupt = CreateFileW(
        corruptManifest.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    LARGE_INTEGER offset{};
    DWORD written = 0;
    const Bytes serialOne = Uint64(1);
    offset.QuadPart = 8;
    bool corrupted =
        corrupt != INVALID_HANDLE_VALUE &&
        SetFilePointerEx(corrupt, offset, nullptr, FILE_BEGIN) &&
        WriteFile(corrupt, serialOne.data(),
                  static_cast<DWORD>(serialOne.size()), &written, nullptr) &&
        written == serialOne.size();
    std::uint8_t rootByte = 0;
    DWORD read = 0;
    offset.QuadPart = 104;
    corrupted = corrupted &&
                SetFilePointerEx(corrupt, offset, nullptr, FILE_BEGIN) &&
                ReadFile(corrupt, &rootByte, 1, &read, nullptr) && read == 1;
    rootByte ^= 0x80;
    corrupted = corrupted &&
                SetFilePointerEx(corrupt, offset, nullptr, FILE_BEGIN) &&
                WriteFile(corrupt, &rootByte, 1, &written, nullptr) &&
                written == 1 && FlushFileBuffers(corrupt);
    if (corrupt != INVALID_HANDLE_VALUE)
      CloseHandle(corrupt);
    corrupted = corrupted && ResealManifest(corruptManifest);
    GraphStore corruptedStore(corruptRoot.wstring());
    ok = Expect(corrupted && corruptedStore.Initialize() &&
                    corruptedStore.ActiveSerial() == 0 &&
                    !std::filesystem::exists(corruptGeneration),
                L"resealed corrupt root metadata rejected on initialize") &&
         ok;
  }
  const auto resealedMetadataRejected = [&](const wchar_t *name,
                                            std::uint64_t metadataOffset,
                                            const Bytes &replacement) {
    const auto mismatchRoot = root / name;
    const auto generation = mismatchRoot / L"generation-00000000000000000001";
    std::filesystem::create_directory(mismatchRoot, error);
    std::filesystem::copy(latest, generation,
                          std::filesystem::copy_options::recursive, error);
    const auto manifestPath = generation / L"manifest.hgm";
    SetFileAttributesW(manifestPath.c_str(), FILE_ATTRIBUTE_NORMAL);
    HANDLE file = CreateFileW(
        manifestPath.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    LARGE_INTEGER offset{};
    DWORD written = 0;
    const Bytes serialOne = Uint64(1);
    offset.QuadPart = 8;
    bool prepared =
        file != INVALID_HANDLE_VALUE &&
        SetFilePointerEx(file, offset, nullptr, FILE_BEGIN) &&
        WriteFile(file, serialOne.data(), static_cast<DWORD>(serialOne.size()),
                  &written, nullptr) &&
        written == serialOne.size();
    offset.QuadPart = static_cast<LONGLONG>(metadataOffset);
    prepared =
        prepared && SetFilePointerEx(file, offset, nullptr, FILE_BEGIN) &&
        WriteFile(file, replacement.data(),
                  static_cast<DWORD>(replacement.size()), &written, nullptr) &&
        written == replacement.size() && FlushFileBuffers(file);
    if (file != INVALID_HANDLE_VALUE)
      CloseHandle(file);
    prepared = prepared && ResealManifest(manifestPath);
    GraphStore mismatchStore(mismatchRoot.wstring());
    return prepared && mismatchStore.Initialize() &&
           mismatchStore.ActiveSerial() == 0 &&
           !std::filesystem::exists(generation);
  };
  if (!latest.empty()) {
    ok = Expect(
             resealedMetadataRejected(L"corrupt-profile-metadata", 204,
                                      Uint64(ProfileBit(ProfileId::Structure) |
                                             ProfileBit(ProfileId::Layout))),
             L"resealed profile mismatch rejected on initialize") &&
         ok;
    ok = Expect(
             resealedMetadataRejected(L"corrupt-integrity-metadata", 202,
                                      Uint8(static_cast<std::uint8_t>(
                                          CaptureIntegrity::TraversalFailure))),
             L"resealed CaptureIntegrity mismatch rejected on initialize") &&
         ok;
  }
  const auto maximum = root / L"generation-18446744073709551615";
  if (!latest.empty()) {
    std::filesystem::copy(latest, maximum,
                          std::filesystem::copy_options::recursive, error);
    const auto maximumManifest = maximum / L"manifest.hgm";
    SetFileAttributesW(maximumManifest.c_str(), FILE_ATTRIBUTE_NORMAL);
    HANDLE file =
        CreateFileW(maximumManifest.c_str(), GENERIC_WRITE, FILE_SHARE_READ,
                    nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    LARGE_INTEGER serialOffset{};
    serialOffset.QuadPart = 8;
    SetFilePointerEx(file, serialOffset, nullptr, FILE_BEGIN);
    const Bytes maximumSerial =
        Uint64((std::numeric_limits<std::uint64_t>::max)());
    DWORD written = 0;
    WriteFile(file, maximumSerial.data(),
              static_cast<DWORD>(maximumSerial.size()), &written, nullptr);
    FlushFileBuffers(file);
    CloseHandle(file);
    ResealManifest(maximumManifest);
    GraphStore maximumStore(root.wstring());
    const bool maximumInitialized = maximumStore.Initialize();
    const std::uint64_t maximumActive = maximumStore.ActiveSerial();
    const bool maximumRejected =
        !maximumStore.Publish(Publication(records, index, 8));
    std::wcout << L"STORE_DURABLE_HIGH_WATER_MAXIMUM_FACTS initialized="
               << maximumInitialized << L" active=" << maximumActive
               << L" rejected=" << maximumRejected << L'\n';
    ok = Expect(maximumInitialized &&
                    maximumActive !=
                        (std::numeric_limits<std::uint64_t>::max)() &&
                    maximumRejected,
                L"validated abandoned serial UINT64_MAX is durably reserved "
                L"and rejects publication without overflow") &&
         ok;
  }
  CloseHandle(records);
  CloseHandle(index);

  const auto ownerless = root / L"candidate-ownerless";
  const auto unlocked = root / L"candidate-unlocked";
  const auto active = root / L"candidate-active";
  std::filesystem::create_directory(ownerless, error);
  std::filesystem::create_directory(unlocked, error);
  std::filesystem::create_directory(active, error);
  HANDLE unlockedOwner =
      CreateFileW((unlocked / L"owner.lock").c_str(), GENERIC_WRITE, 0, nullptr,
                  CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (unlockedOwner != INVALID_HANDLE_VALUE)
    CloseHandle(unlockedOwner);
  HANDLE activeOwner = CreateFileW((active / L"owner.lock").c_str(),
                                   GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                                   CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
  {
    GraphStore startup(root.wstring());
    ok = Expect(startup.Initialize() && !std::filesystem::exists(ownerless) &&
                    !std::filesystem::exists(unlocked) &&
                    std::filesystem::exists(active),
                L"cleanup ownerless/unlocked while preserving active lock") &&
         ok;
  }
  CloseHandle(activeOwner);
  {
    GraphStore cleanup(root.wstring());
    ok = Expect(cleanup.Initialize() && !std::filesystem::exists(active),
                L"released owner lock becomes abandoned") &&
         ok;
  }

  const auto garbage = root / L"generation-99999999999999999999";
  std::filesystem::create_directory(garbage, error);
  {
    GraphStore startup(root.wstring());
    ok = Expect(startup.Initialize() && !std::filesystem::exists(garbage),
                L"garbage generation rejected and removed") &&
         ok;
  }

  const auto sparse = root / L"sparse-4g.bin";
  HANDLE sparseFile =
      CreateFileW(sparse.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                  CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  DWORD ignored = 0;
  DeviceIoControl(sparseFile, FSCTL_SET_SPARSE, nullptr, 0, nullptr, 0,
                  &ignored, nullptr);
  LARGE_INTEGER end{};
  end.QuadPart = INT64_C(0x100000001);
  SetFilePointerEx(sparseFile, end, nullptr, FILE_BEGIN);
  SetEndOfFile(sparseFile);
  Sha256 largeDigest{};
  ok = Expect(HashFileRangeChunked(sparseFile, 0, UINT64_C(0x100000001),
                                   &largeDigest) &&
                  Hex(largeDigest) ==
                      "fbb82f7b353676bb562eb82157fcf0ea42c36492ca13ee56d"
                      "bf82c08b6802c5c",
              L"stream/hash actual >4GiB logical bytes") &&
       ok;
  CloseHandle(sparseFile);
  const bool rootRemoved = DeleteSmokeTree(root);
  std::wcout << L"CODEC_STORE_TEST_ROOT_REMOVED " << rootRemoved << L'\n';
  return rootRemoved && ok;
}

bool FusedWalkerCountersSmoke() {
  ResetCodecDebugCounters();
  Error error = Error::None;
  std::array<CanonicalizationResult, 6> results{};
  for (std::size_t pass = 0; pass < results.size(); ++pass) {
    results[pass] = SmallGraph(
        5000 + static_cast<std::uint32_t>(pass), true,
        PropertyOrigin::Direct, false, false, &error);
    if (error != Error::None || !results[pass].rootsPresent)
      return false;
  }
  const CodecDebugCounters counters = ReadCodecDebugCounters();
  const bool rootsEqual = std::all_of(
      results.begin() + 1, results.end(), [&results](const auto &result) {
        return Equal(result.observedSemanticRoot,
                     results.front().observedSemanticRoot) &&
               Equal(result.layoutRoot, results.front().layoutRoot) &&
               Equal(result.captureRoot, results.front().captureRoot) &&
               result.recordStream.byteLength ==
                   results.front().recordStream.byteLength;
      });
  const std::uint64_t descriptorRecords = std::accumulate(
      results.begin(), results.end(), UINT64_C(0),
      [](const std::uint64_t total, const auto& result) {
        return total + result.recordStream.itemCount;
      });
  const bool passed = counters.parsedRecordWalks == 6 &&
      counters.legacyValidatorWalks == 0 && counters.sourceBytesRead != 0 &&
      counters.descriptorRecordBuilds == descriptorRecords &&
      counters.descriptorRecordReuses == descriptorRecords * 2 && rootsEqual;
  std::wcout << L"CODEC_FUSED_WALKER_COUNTERS " << passed
             << L" parsed_record_walks=" << counters.parsedRecordWalks
             << L" legacy_validator_walks=" << counters.legacyValidatorWalks
             << L" descriptor_builds=" << counters.descriptorRecordBuilds
             << L" descriptor_reuses=" << counters.descriptorRecordReuses
             << L" source_bytes=" << counters.sourceBytesRead << L'\n';
  return passed;
}
} // namespace

bool DocumentGraphCodecStoreSmoke() {
  bool ok = CodecSmoke();
  const bool definitionAggregate = DefinitionAggregateMismatchSmoke();
  const bool definitionOrigin = DefinitionUnknownOriginSmoke();
  std::wcout << L"CODEC_DEFINITION_103_MISMATCH_REJECTED "
             << definitionAggregate << L'\n'
             << L"CODEC_DEFINITION_UNKNOWN_ORIGIN_BYTES "
             << definitionOrigin << L'\n';
  ok = definitionAggregate && definitionOrigin && ok;
  ok = WindowsBrushCompletenessSmoke() && ok;
  ok = CertificationDomainSeparationSmoke() && ok;
  ok = DefinitionLayoutCertificationSmoke() && ok;
  ok = RootSmoke() && ok;
  ok = FusedWalkerCountersSmoke() && ok;
  ok = StoreSmoke() && ok;
  return ok;
}

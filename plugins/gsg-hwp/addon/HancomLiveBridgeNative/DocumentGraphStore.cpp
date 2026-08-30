#include "DocumentGraphStore.h"

#include <algorithm>
#include <bcrypt.h>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <cwchar>
#include <filesystem>
#include <limits>
#include <map>
#include <memory>
#include <new>
#include <set>
#include <strsafe.h>
#include <utility>
#include <vector>

namespace hancom::graph::store {
namespace {
using codec::Bytes;
std::atomic<std::uint64_t> gGenerationAuthenticationCount{0};
std::atomic<std::uint64_t> gPreparedPublicationStage{0};
std::atomic<std::uint64_t> gPreparedPromotions{0};
std::atomic<std::uint64_t> gPreparedManifestWrites{0};
std::atomic<std::uint64_t> gPreparedManifestFlushes{0};
std::atomic<std::uint64_t> gPreparedCommitWrites{0};
std::atomic<std::uint64_t> gPreparedCommitFlushes{0};
constexpr std::uint64_t kSeal = UINT64_C(0x314c4145534d4748); // HGMSEAL1
constexpr std::uint64_t kManifestBytes = 580;
constexpr std::uint64_t kLegacyCommitBytes = 48;
constexpr std::uint64_t kCommitBytes = 64;
constexpr std::uint64_t kStoreIdentityBytes = 56;
constexpr std::uint64_t kReservationBytes = 96;
constexpr std::uint64_t kJournalEntryBytes = 96;
constexpr DWORD kIoBytes = 1U << 20;
constexpr std::size_t kPathCharacters = 32768;
using PathBuffer = std::unique_ptr<wchar_t[]>;
PathBuffer NewPathBuffer() noexcept {
  return PathBuffer(new (std::nothrow) wchar_t[kPathCharacters]{});
}

void U8(Bytes *out, std::uint8_t value) { out->push_back(value); }
void U16(Bytes *out, std::uint16_t value) {
  U8(out, static_cast<std::uint8_t>(value));
  U8(out, static_cast<std::uint8_t>(value >> 8));
}
void U32(Bytes *out, std::uint32_t value) {
  for (unsigned shift = 0; shift != 32; shift += 8)
    U8(out, static_cast<std::uint8_t>(value >> shift));
}
void U64(Bytes *out, std::uint64_t value) {
  for (unsigned shift = 0; shift != 64; shift += 8)
    U8(out, static_cast<std::uint8_t>(value >> shift));
}
std::uint16_t R16(const std::uint8_t *p) {
  return static_cast<std::uint16_t>(p[0] | (std::uint16_t{p[1]} << 8));
}
std::uint32_t R32(const std::uint8_t *p) {
  std::uint32_t value = 0;
  for (unsigned index = 0; index != 4; ++index)
    value |= std::uint32_t{p[index]} << (index * 8);
  return value;
}
std::uint64_t R64(const std::uint8_t *p) {
  std::uint64_t value = 0;
  for (unsigned index = 0; index != 8; ++index)
    value |= std::uint64_t{p[index]} << (index * 8);
  return value;
}
void AddDigest(Bytes *out, const Sha256 &digest) {
  out->insert(out->end(), digest.bytes.begin(), digest.bytes.end());
}
Sha256 ReadDigest(const std::uint8_t *bytes) {
  Sha256 digest{};
  std::copy_n(bytes, digest.bytes.size(), digest.bytes.begin());
  return digest;
}
void AddStream(Bytes *out, const CanonicalStream &stream) {
  U64(out, stream.length);
  AddDigest(out, stream.digest);
}
bool EqualStream(const CanonicalStream &a, const CanonicalStream &b) noexcept {
  return a.length == b.length && codec::Equal(a.digest, b.digest);
}
bool Join(const wchar_t *parent, const wchar_t *name, wchar_t *out) noexcept {
  return SUCCEEDED(StringCchPrintfW(out, 32768, L"%s\\%s", parent, name));
}
bool DeleteTree(const wchar_t *path) noexcept {
  wchar_t pattern[32768]{};
  if (!Join(path, L"*", pattern))
    return false;
  WIN32_FIND_DATAW data{};
  HANDLE find = FindFirstFileW(pattern, &data);
  if (find != INVALID_HANDLE_VALUE) {
    do {
      if (std::wcscmp(data.cFileName, L".") == 0 ||
          std::wcscmp(data.cFileName, L"..") == 0)
        continue;
      wchar_t child[32768]{};
      if (!Join(path, data.cFileName, child)) {
        FindClose(find);
        return false;
      }
      if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        if (!DeleteTree(child)) {
          FindClose(find);
          return false;
        }
      } else {
        SetFileAttributesW(child, FILE_ATTRIBUTE_NORMAL);
        if (!DeleteFileW(child)) {
          FindClose(find);
          return false;
        }
      }
    } while (FindNextFileW(find, &data));
    FindClose(find);
  }
  SetFileAttributesW(path, FILE_ATTRIBUTE_NORMAL);
  return RemoveDirectoryW(path) != FALSE ||
         GetLastError() == ERROR_PATH_NOT_FOUND;
}
bool Seek(HANDLE file, std::uint64_t offset) noexcept {
  if (offset >
      static_cast<std::uint64_t>((std::numeric_limits<LONGLONG>::max)()))
    return false;
  LARGE_INTEGER position{};
  position.QuadPart = static_cast<LONGLONG>(offset);
  return SetFilePointerEx(file, position, nullptr, FILE_BEGIN) != FALSE;
}
struct HashContext final {
  BCRYPT_ALG_HANDLE algorithm = nullptr;
  BCRYPT_HASH_HANDLE hash = nullptr;
  PUCHAR object = nullptr;
  DWORD objectBytes = 0;
  bool Open() noexcept {
    DWORD returned = 0;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM,
                                    nullptr, 0) < 0 ||
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<PUCHAR>(&objectBytes),
                          sizeof(objectBytes), &returned, 0) < 0)
      return false;
    object = static_cast<PUCHAR>(HeapAlloc(GetProcessHeap(), 0, objectBytes));
    return object != nullptr &&
           BCryptCreateHash(algorithm, &hash, object, objectBytes, nullptr, 0,
                            0) >= 0;
  }
  bool Add(const std::uint8_t *bytes, DWORD count) noexcept {
    return BCryptHashData(hash, const_cast<PUCHAR>(bytes), count, 0) >= 0;
  }
  bool Finish(Sha256 *digest) noexcept {
    return BCryptFinishHash(hash, digest->bytes.data(),
                            static_cast<ULONG>(digest->bytes.size()), 0) >= 0;
  }
  ~HashContext() noexcept {
    if (hash != nullptr)
      BCryptDestroyHash(hash);
    if (object != nullptr)
      HeapFree(GetProcessHeap(), 0, object);
    if (algorithm != nullptr)
      BCryptCloseAlgorithmProvider(algorithm, 0);
  }
};
bool WriteBytes(HANDLE file, const std::uint8_t *bytes,
                std::uint64_t length) noexcept {
  std::uint64_t done = 0;
  while (done != length) {
    const DWORD wanted = static_cast<DWORD>(std::min<std::uint64_t>(
        length - done, static_cast<std::uint64_t>(MAXDWORD)));
    DWORD written = 0;
    if (WriteFile(file, bytes + done, wanted, &written, nullptr) == FALSE ||
        written != wanted)
      return false;
    done += written;
  }
  return true;
}
bool FileLength(HANDLE file, std::uint64_t *length) noexcept {
  LARGE_INTEGER size{};
  if (GetFileSizeEx(file, &size) == FALSE || size.QuadPart < 0)
    return false;
  *length = static_cast<std::uint64_t>(size.QuadPart);
  return true;
}
bool ReadExact(HANDLE file, std::uint8_t *bytes, DWORD length) noexcept {
  DWORD read = 0;
  return ReadFile(file, bytes, length, &read, nullptr) != FALSE &&
         read == length;
}
bool ReadFileIdentity(HANDLE file, std::uint64_t* const volume,
                      std::array<std::uint8_t, 16>* const id) noexcept {
  FILE_ID_INFO identity{};
  if (file == INVALID_HANDLE_VALUE || volume == nullptr || id == nullptr ||
      GetFileInformationByHandleEx(file, FileIdInfo, &identity,
                                   sizeof(identity)) == FALSE)
    return false;
  *volume = identity.VolumeSerialNumber;
  std::copy(std::begin(identity.FileId.Identifier),
            std::end(identity.FileId.Identifier), id->begin());
  return true;
}
bool PreparedFileMatches(const PreparedFileArtifact& artifact,
                         HANDLE expectedHandle,
                         std::uint64_t expectedLength,
                         const Sha256& expectedDigest) noexcept {
  std::uint64_t length = 0, volume = 0;
  std::array<std::uint8_t, 16> id{};
  return artifact.file != INVALID_HANDLE_VALUE &&
      artifact.file == expectedHandle && artifact.length == expectedLength &&
      codec::Equal(artifact.digest, expectedDigest) &&
      FileLength(artifact.file, &length) && length == artifact.length &&
      ReadFileIdentity(artifact.file, &volume, &id) &&
      volume == artifact.volumeSerial && id == artifact.fileId;
}
bool DestinationIdentityMatches(const wchar_t* const path,
                                const PreparedFileArtifact& artifact,
                                HANDLE* const held) noexcept {
  if (path == nullptr || held == nullptr) return false;
  *held = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                      OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  std::uint64_t length = 0, volume = 0;
  std::array<std::uint8_t, 16> id{};
  if (*held != INVALID_HANDLE_VALUE && FileLength(*held, &length) &&
      ReadFileIdentity(*held, &volume, &id) && length == artifact.length &&
      volume == artifact.volumeSerial && id == artifact.fileId)
    return true;
  if (*held != INVALID_HANDLE_VALUE) CloseHandle(*held);
  *held = INVALID_HANDLE_VALUE;
  return false;
}

bool LinkDurableWholeFile(
    const FileSlice& source,
    const wchar_t* const destination) noexcept {
  std::uint64_t length = 0;
  if (!source.durableWholeFile || source.file == INVALID_HANDLE_VALUE ||
      source.offset != 0 || destination == nullptr ||
      !FileLength(source.file, &length) ||
      length != source.length) {
    return false;
  }
  std::array<wchar_t, 32768> path{};
  const DWORD copied = GetFinalPathNameByHandleW(
      source.file, path.data(), static_cast<DWORD>(path.size()),
      FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  return copied != 0 && copied < path.size() &&
      CreateHardLinkW(destination, path.data(), nullptr) != FALSE;
}

bool FlushClose(HANDLE *file, FailurePoint flushPoint,
                FailurePoint failure) noexcept {
  const bool ok = *file != INVALID_HANDLE_VALUE && failure != flushPoint &&
                  FlushFileBuffers(*file) != FALSE;
  if (*file != INVALID_HANDLE_VALUE)
    CloseHandle(*file);
  *file = INVALID_HANDLE_VALUE;
  return ok;
}
Bytes ManifestPrefix(std::uint64_t serial, const PublicationInput &input,
                     const Sha256 &recordsDigest, const Sha256 &indexDigest,
                     std::uint64_t blobCacheLength,
                     const Sha256 &blobCacheDigest) {
  Bytes bytes;
  bytes.reserve(static_cast<std::size_t>(kManifestBytes));
  bytes.insert(bytes.end(), {'H', 'G', 'M', '1'});
  U16(&bytes, kStoreVersion);
  U16(&bytes, codec::kCodecVersion);
  U64(&bytes, serial);
  U64(&bytes, input.records.length);
  AddDigest(&bytes, recordsDigest);
  U64(&bytes, input.index.length);
  AddDigest(&bytes, indexDigest);
  U64(&bytes, input.observedControlCount);
  AddDigest(&bytes, input.first.observedSemanticRoot);
  AddDigest(&bytes, input.first.layoutRoot);
  AddDigest(&bytes, input.first.captureRoot);
  U8(&bytes, input.first.semanticCertified ? 1U : 0U);
  U8(&bytes, input.first.layoutPresent ? 1U : 0U);
  U8(&bytes, static_cast<std::uint8_t>(input.first.integrity));
  U8(&bytes, 0);
  U64(&bytes, input.first.closedProfileBits);
  AddStream(&bytes, input.first.coverage);
  AddStream(&bytes, input.first.unavailable);
  AddStream(&bytes, input.first.diagnostics);
  AddStream(&bytes, input.baseline.route);
  AddStream(&bytes, input.baseline.cursor);
  AddStream(&bytes, input.baseline.selection);
  U8(&bytes, input.baseline.modified ? 1U : 0U);
  bytes.insert(bytes.end(), 7, 0);
  U64(&bytes, input.layoutEnvironment.size);
  AddDigest(&bytes, codec::Hash(input.layoutEnvironment));
  U64(&bytes, blobCacheLength);
  AddDigest(&bytes, blobCacheDigest);
  return bytes;
}
bool SetImmutable(const wchar_t *path) noexcept {
  return SetFileAttributesW(path, FILE_ATTRIBUTE_READONLY) != FALSE;
}
bool ParseSerial(const wchar_t *name, std::uint64_t *serial) noexcept {
  if (std::wcsncmp(name, L"generation-", 11) != 0)
    return false;
  wchar_t *end = nullptr;
  errno = 0;
  const unsigned long long value = std::wcstoull(name + 11, &end, 10);
  if (errno == ERANGE || end == name + 11 || *end != L'\0' || value == 0)
    return false;
  *serial = static_cast<std::uint64_t>(value);
  return true;
}
struct SourceReader final {
  HANDLE file = INVALID_HANDLE_VALUE;
  std::uint64_t base = 0;
};
bool ReadSource(void *context, std::uint64_t offset, std::uint8_t *buffer,
                std::uint32_t requested, std::uint32_t *actual) noexcept {
  auto *reader = static_cast<SourceReader *>(context);
  if (reader == nullptr || actual == nullptr ||
      offset > (std::numeric_limits<std::uint64_t>::max)() - reader->base ||
      !Seek(reader->file, reader->base + offset))
    return false;
  DWORD read = 0;
  const bool ok =
      ReadFile(reader->file, buffer, requested, &read, nullptr) != FALSE;
  *actual = read;
  return ok;
}
using LineageId = std::array<std::uint8_t, 16>;
struct LineageFacts final {
  std::set<LineageId> nodes{};
  std::set<LineageId> priorSources{};
};
bool ReadLineageFacts(const FileSlice &slice, LineageFacts *facts) noexcept {
  if (facts == nullptr || slice.file == INVALID_HANDLE_VALUE)
    return false;
  try {
    std::uint64_t offset = 0;
    while (offset != slice.length) {
      std::array<std::uint8_t, 24> header{};
      SourceReader reader{slice.file, slice.offset};
      std::uint32_t actual = 0;
      if (slice.length - offset < header.size() ||
          !ReadSource(&reader, offset, header.data(),
                      static_cast<std::uint32_t>(header.size()), &actual) ||
          actual != header.size())
        return false;
      const std::uint64_t payload = R64(header.data() + 16);
      if (payload > MAXDWORD - header.size() ||
          payload > slice.length - offset - header.size())
        return false;
      Bytes record(static_cast<std::size_t>(header.size() + payload));
      if (!ReadSource(&reader, offset, record.data(),
                      static_cast<std::uint32_t>(record.size()), &actual) ||
          actual != record.size())
        return false;
      const auto kind = static_cast<RecordKind>(R16(record.data()));
      if (kind == RecordKind::Node) {
        if (record.size() < 88 || R16(record.data() + 24) != 1 ||
            R64(record.data() + 40) < 40 || R16(record.data() + 48) != 1 ||
            R64(record.data() + 64) != 16)
          return false;
        LineageId id{};
        std::copy_n(record.data() + 72, id.size(), id.begin());
        if (!facts->nodes.insert(id).second)
          return false;
      } else if (kind == RecordKind::Tombstone || kind == RecordKind::Remap) {
        if (record.size() < 64 || R16(record.data() + 24) != 1 ||
            R64(record.data() + 40) != 16)
          return false;
        LineageId source{};
        std::copy_n(record.data() + 48, source.size(), source.begin());
        bool prior = kind == RecordKind::Tombstone;
        if (kind == RecordKind::Remap) {
          std::uint64_t at = 24;
          std::uint8_t disposition = 0;
          std::uint16_t reason = 0;
          for (std::uint16_t field = 0; field < R16(record.data() + 6);
               ++field) {
            if (at > record.size() || record.size() - at < 24)
              return false;
            const std::uint16_t tag = R16(record.data() + at);
            const std::uint64_t length = R64(record.data() + at + 16);
            if (length > record.size() - at - 24)
              return false;
            if (tag == 3 && length == 1)
              disposition = record[static_cast<std::size_t>(at + 24)];
            if (tag == 4 && length == 2)
              reason = R16(record.data() + at + 24);
            at += 24 + length;
          }
          prior = disposition ==
                      static_cast<std::uint8_t>(RemapDisposition::Retained) ||
              disposition ==
                  static_cast<std::uint8_t>(RemapDisposition::Tombstoned) ||
              disposition ==
                  static_cast<std::uint8_t>(RemapDisposition::Ambiguous) ||
              (disposition == static_cast<std::uint8_t>(
                                    RemapDisposition::New) &&
               (reason == static_cast<std::uint16_t>(RemapReason::Clone) ||
                reason == static_cast<std::uint16_t>(
                              RemapReason::TableCellSplit)));
        }
        if (prior)
          facts->priorSources.insert(source);
      }
      offset += record.size();
    }
    return offset == slice.length;
  } catch (...) {
    return false;
  }
}
bool ValidateActiveLineage(const FileSlice &candidate,
                           const GenerationPin &active) noexcept {
  LineageFacts next;
  if (!ReadLineageFacts(candidate, &next))
    return false;
  if (next.priorSources.empty())
    return true;
  if (!active)
    return false;
  const std::filesystem::path records =
      std::filesystem::path(active->Path()) / L"records.hgn";
  HANDLE file = CreateFileW(records.c_str(), GENERIC_READ, FILE_SHARE_READ,
                            nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  std::uint64_t length = 0;
  LineageFacts prior;
  const bool valid = file != INVALID_HANDLE_VALUE && FileLength(file, &length) &&
      ReadLineageFacts({file, 0, length}, &prior) &&
      std::all_of(next.priorSources.begin(), next.priorSources.end(),
                  [&prior](const LineageId &id) {
                    return prior.nodes.find(id) != prior.nodes.end();
                  });
  if (file != INVALID_HANDLE_VALUE)
    CloseHandle(file);
  return valid;
}
bool CopyCanonicalBlobPlan(
    const std::vector<codec::CanonicalBlobSlice> &plan,
    void *sourceContext, codec::BlobReadCallback sourceRead,
    HANDLE destination) noexcept {
  if (destination == INVALID_HANDLE_VALUE ||
      (!plan.empty() && sourceRead == nullptr))
    return false;
  constexpr std::uint32_t kChunk = 1U << 20;
  auto *buffer = static_cast<std::uint8_t *>(
      HeapAlloc(GetProcessHeap(), 0, kChunk));
  bool ok = buffer != nullptr;
  for (const codec::CanonicalBlobSlice &slice : plan) {
    HashContext hash;
    ok = ok && hash.Open();
    std::uint64_t done = 0;
    while (ok && done != slice.length) {
      const std::uint32_t wanted = static_cast<std::uint32_t>(
          (std::min<std::uint64_t>)(slice.length - done, kChunk));
      std::uint32_t actual = 0;
      ok = sourceRead(sourceContext, slice.contentId, slice.offset + done,
                      buffer, wanted, &actual) &&
           actual == wanted && hash.Add(buffer, actual);
      Bytes header(slice.contentId.bytes.begin(), slice.contentId.bytes.end());
      U64(&header, slice.offset + done);
      U32(&header, actual);
      ok = ok && WriteBytes(destination, header.data(), header.size()) &&
           WriteBytes(destination, buffer, actual);
      done += actual;
    }
    Sha256 actualDigest{};
    ok = ok && hash.Finish(&actualDigest) &&
         codec::Equal(actualDigest, slice.digest);
    if (!ok) break;
  }
  if (buffer != nullptr) HeapFree(GetProcessHeap(), 0, buffer);
  return ok;
}
struct BlobCacheReader final {
  HANDLE file = INVALID_HANDLE_VALUE;
  std::uint64_t length = 0;
};
bool ReadCachedBlob(void *context, const ContentId &contentId,
                    std::uint64_t offset, std::uint8_t *buffer,
                    std::uint32_t requested, std::uint32_t *actual) noexcept {
  auto *reader = static_cast<BlobCacheReader *>(context);
  if (reader == nullptr || actual == nullptr)
    return false;
  std::uint64_t at = 0;
  std::array<std::uint8_t, 28> header{};
  while (at != reader->length) {
    if (reader->length - at < header.size() || !Seek(reader->file, at) ||
        !ReadExact(reader->file, header.data(),
                   static_cast<DWORD>(header.size())))
      return false;
    const std::uint32_t length = R32(header.data() + 24);
    at += header.size();
    if (length > reader->length - at)
      return false;
    if (std::equal(contentId.bytes.begin(), contentId.bytes.end(),
                   header.begin()) &&
        R64(header.data() + 16) == offset && length == requested) {
      if (!ReadExact(reader->file, buffer, length))
        return false;
      *actual = length;
      return true;
    }
    at += length;
  }
  return false;
}
bool ValidateGeneration(const wchar_t *path, std::uint64_t expectedSerial,
                        std::uint64_t *recordsLength,
                        std::uint64_t *indexLength,
                        AuthenticatedGenerationFiles *authenticated = nullptr,
                        bool replayCanonical = true) noexcept {
  PathBuffer manifestPath = NewPathBuffer();
  PathBuffer recordsPath = NewPathBuffer();
  PathBuffer indexPath = NewPathBuffer();
  PathBuffer environmentPath = NewPathBuffer();
  PathBuffer blobCachePath = NewPathBuffer();
  if (!manifestPath || !recordsPath || !indexPath || !environmentPath ||
      !blobCachePath || !Join(path, L"manifest.hgm", manifestPath.get()) ||
      !Join(path, L"records.hgn", recordsPath.get()) ||
      !Join(path, L"index.hgi", indexPath.get()) ||
      !Join(path, L"environment.hge", environmentPath.get()) ||
      !Join(path, L"blobs.hgb", blobCachePath.get()))
    return false;
  HANDLE manifest =
      CreateFileW(manifestPath.get(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  std::array<std::uint8_t, kManifestBytes> bytes{};
  std::uint64_t manifestLength = 0;
  const bool readManifest =
      manifest != INVALID_HANDLE_VALUE &&
      FileLength(manifest, &manifestLength) && manifestLength == bytes.size() &&
      Seek(manifest, 0) &&
      ReadExact(manifest, bytes.data(), static_cast<DWORD>(bytes.size()));
  if (!readManifest || std::memcmp(bytes.data(), "HGM1", 4) != 0 ||
      R16(bytes.data() + 4) != kStoreVersion ||
      R16(bytes.data() + 6) != codec::kCodecVersion ||
      R64(bytes.data() + 8) != expectedSerial ||
      R64(bytes.data() + kManifestBytes - 8) != kSeal || bytes[200] > 1 ||
      bytes[201] > 1 ||
      bytes[202] >
          static_cast<std::uint8_t>(CaptureIntegrity::StateRestoreFailure) ||
      bytes[203] != 0 || !HasOnlyKnownProfiles(R64(bytes.data() + 204)) ||
      (bytes[201] != 0 && bytes[200] == 0)) {
    if (manifest != INVALID_HANDLE_VALUE) CloseHandle(manifest);
    return false;
  }
  Bytes manifestIntegrity(bytes.begin(), bytes.begin() + 540);
  manifestIntegrity.insert(manifestIntegrity.end(), bytes.end() - 8,
                           bytes.end());
  const Sha256 expectedManifestDigest =
      codec::Hash(codec::View(manifestIntegrity));
  if (!std::equal(expectedManifestDigest.bytes.begin(),
                  expectedManifestDigest.bytes.end(), bytes.begin() + 540)) {
    CloseHandle(manifest);
    return false;
  }
  *recordsLength = R64(bytes.data() + 16);
  *indexLength = R64(bytes.data() + 56);
  HANDLE records =
      CreateFileW(recordsPath.get(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  HANDLE index = CreateFileW(indexPath.get(), GENERIC_READ, FILE_SHARE_READ,
                             nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                             nullptr);
  HANDLE environment =
      CreateFileW(environmentPath.get(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  HANDLE blobCache =
      CreateFileW(blobCachePath.get(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  std::uint64_t actualRecords = 0, actualIndex = 0, actualEnvironment = 0,
                actualBlobCache = 0;
  Sha256 recordsDigest{}, indexDigest{}, environmentDigest{}, blobCacheDigest{};
  bool valid =
      records != INVALID_HANDLE_VALUE && index != INVALID_HANDLE_VALUE &&
      environment != INVALID_HANDLE_VALUE &&
      blobCache != INVALID_HANDLE_VALUE &&
      FileLength(records, &actualRecords) && FileLength(index, &actualIndex) &&
      FileLength(environment, &actualEnvironment) &&
      FileLength(blobCache, &actualBlobCache) &&
      actualRecords == *recordsLength && actualIndex == *indexLength &&
      actualEnvironment == R64(bytes.data() + 460) &&
      actualBlobCache == R64(bytes.data() + 500) &&
      HashFileRangeChunked(records, 0, actualRecords, &recordsDigest) &&
      HashFileRangeChunked(index, 0, actualIndex, &indexDigest) &&
      HashFileRangeChunked(environment, 0, actualEnvironment,
                           &environmentDigest) &&
      HashFileRangeChunked(blobCache, 0, actualBlobCache, &blobCacheDigest) &&
      std::equal(recordsDigest.bytes.begin(), recordsDigest.bytes.end(),
                 bytes.begin() + 24) &&
      std::equal(indexDigest.bytes.begin(), indexDigest.bytes.end(),
                 bytes.begin() + 64) &&
      std::equal(environmentDigest.bytes.begin(), environmentDigest.bytes.end(),
                 bytes.begin() + 468) &&
      std::equal(blobCacheDigest.bytes.begin(), blobCacheDigest.bytes.end(),
                 bytes.begin() + 508);
  Bytes layoutEnvironment;
  if (valid && actualEnvironment <= MAXDWORD &&
      actualEnvironment <= static_cast<std::uint64_t>(
                               (std::numeric_limits<std::size_t>::max)())) {
    layoutEnvironment.resize(static_cast<std::size_t>(actualEnvironment));
    valid = Seek(environment, 0) &&
            (layoutEnvironment.empty() ||
             ReadExact(environment, layoutEnvironment.data(),
                       static_cast<DWORD>(layoutEnvironment.size())));
  } else {
    valid = false;
  }
  codec::CanonicalizationResult canonical{};
  SourceReader source{records, 0};
  BlobCacheReader cachedBlobs{blobCache, actualBlobCache};
  if (valid && replayCanonical) {
    const codec::CanonicalizationInput canonicalInput{
        {&source, ReadSource, actualRecords, StreamKind::Capture},
        &cachedBlobs,
        ReadCachedBlob,
        codec::View(layoutEnvironment)};
    valid =
        codec::CanonicalizeRecordStream(canonicalInput, &canonical) ==
            codec::Error::None &&
        canonical.rootsPresent && canonical.streamKind == StreamKind::Capture &&
        canonical.recordStream.byteLength == actualRecords &&
        codec::Equal(canonical.recordStream.digest, recordsDigest) &&
        codec::Equal(canonical.viewIndexDigest, indexDigest) &&
        codec::Equal(canonical.observedSemanticRoot,
                     ReadDigest(bytes.data() + 104)) &&
        codec::Equal(canonical.layoutRoot, ReadDigest(bytes.data() + 136)) &&
        codec::Equal(canonical.captureRoot, ReadDigest(bytes.data() + 168)) &&
        canonical.semanticCertified == (bytes[200] != 0) &&
        canonical.layoutPresent == (bytes[201] != 0) &&
        canonical.integrity == static_cast<CaptureIntegrity>(bytes[202]) &&
        canonical.closedProfileBits == R64(bytes.data() + 204) &&
        canonical.coverage.byteLength == R64(bytes.data() + 212) &&
        codec::Equal(canonical.coverage.digest,
                     ReadDigest(bytes.data() + 220)) &&
        canonical.unavailable.byteLength == R64(bytes.data() + 252) &&
        codec::Equal(canonical.unavailable.digest,
                     ReadDigest(bytes.data() + 260)) &&
        canonical.diagnostics.byteLength == R64(bytes.data() + 292) &&
        codec::Equal(canonical.diagnostics.digest,
                     ReadDigest(bytes.data() + 300));
  }
  if (valid && !replayCanonical) canonical.rootsPresent = true;
  const DWORD ra = GetFileAttributesW(recordsPath.get()),
              ia = GetFileAttributesW(indexPath.get()),
              ea = GetFileAttributesW(environmentPath.get()),
              ba = GetFileAttributesW(blobCachePath.get()),
              ma = GetFileAttributesW(manifestPath.get());
  const bool result = valid && ra != INVALID_FILE_ATTRIBUTES &&
         ia != INVALID_FILE_ATTRIBUTES && ea != INVALID_FILE_ATTRIBUTES &&
         ba != INVALID_FILE_ATTRIBUTES && ma != INVALID_FILE_ATTRIBUTES &&
         (ra & FILE_ATTRIBUTE_READONLY) != 0 &&
         (ia & FILE_ATTRIBUTE_READONLY) != 0 &&
         (ea & FILE_ATTRIBUTE_READONLY) != 0 &&
         (ba & FILE_ATTRIBUTE_READONLY) != 0 &&
         (ma & FILE_ATTRIBUTE_READONLY) != 0;
  if (result && authenticated != nullptr) {
    authenticated->manifest = manifest;
    authenticated->records = records;
    authenticated->blobs = blobCache;
    authenticated->manifestStream.length = manifestLength;
    authenticated->manifestStream.digest = codec::Hash(
        {bytes.data(), static_cast<std::uint64_t>(bytes.size())});
    authenticated->recordsStream = {actualRecords, recordsDigest};
    authenticated->blobStream = {actualBlobCache, blobCacheDigest};
    manifest = INVALID_HANDLE_VALUE;
    records = INVALID_HANDLE_VALUE;
    blobCache = INVALID_HANDLE_VALUE;
  }
  if (manifest != INVALID_HANDLE_VALUE) CloseHandle(manifest);
  if (records != INVALID_HANDLE_VALUE) CloseHandle(records);
  if (index != INVALID_HANDLE_VALUE) CloseHandle(index);
  if (environment != INVALID_HANDLE_VALUE) CloseHandle(environment);
  if (blobCache != INVALID_HANDLE_VALUE) CloseHandle(blobCache);
  return result;
}
using StoreEpoch = std::array<std::uint8_t, 16>;

bool ValidateCommitMarker(const wchar_t *path, std::uint64_t serial,
                          const StoreEpoch &epoch, bool allowLegacy = false,
                          bool requireReadonly = true) noexcept {
  PathBuffer markerPath = NewPathBuffer();
  PathBuffer manifestPath = NewPathBuffer();
  if (!markerPath || !manifestPath ||
      !Join(path, L"commit.hgc", markerPath.get()) ||
      !Join(path, L"manifest.hgm", manifestPath.get()))
    return false;
  HANDLE marker = CreateFileW(markerPath.get(), GENERIC_READ, FILE_SHARE_READ,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
  std::array<std::uint8_t, kCommitBytes> bytes{};
  std::uint64_t length = 0;
  const bool markerRead = marker != INVALID_HANDLE_VALUE &&
      FileLength(marker, &length) &&
      (length == bytes.size() || (allowLegacy && length == kLegacyCommitBytes)) &&
      Seek(marker, 0) &&
      ReadExact(marker, bytes.data(), static_cast<DWORD>(length));
  if (marker != INVALID_HANDLE_VALUE) CloseHandle(marker);
  const bool legacy = markerRead && length == kLegacyCommitBytes &&
      std::memcmp(bytes.data(), "HGC1", 4) == 0 &&
      R16(bytes.data() + 4) == 1 && R16(bytes.data() + 6) == 0;
  const bool current = markerRead && length == kCommitBytes &&
      std::memcmp(bytes.data(), "HGC2", 4) == 0 &&
      R16(bytes.data() + 4) == 2 && R16(bytes.data() + 6) == 1 &&
      std::equal(epoch.begin(), epoch.end(), bytes.begin() + 16);
  if ((!legacy && !current) || R64(bytes.data() + 8) != serial) return false;
  HANDLE manifest = CreateFileW(manifestPath.get(), GENERIC_READ,
                                FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                                FILE_ATTRIBUTE_NORMAL, nullptr);
  std::array<std::uint8_t, kManifestBytes> manifestBytes{};
  const bool manifestRead = manifest != INVALID_HANDLE_VALUE &&
      Seek(manifest, 0) && ReadExact(
          manifest, manifestBytes.data(),
          static_cast<DWORD>(manifestBytes.size()));
  if (manifest != INVALID_HANDLE_VALUE) CloseHandle(manifest);
  const DWORD attributes = GetFileAttributesW(markerPath.get());
  const std::size_t digestOffset = current ? 32U : 16U;
  return manifestRead &&
      std::equal(bytes.begin() + digestOffset,
                 bytes.begin() + digestOffset + 32,
                 manifestBytes.begin() + 540) &&
      attributes != INVALID_FILE_ATTRIBUTES &&
      (!requireReadonly || (attributes & FILE_ATTRIBUTE_READONLY) != 0);
}

enum class ReservationRead : std::uint8_t { Missing, Legacy, Valid, Invalid };

struct AllocatorLock final {
  HANDLE mutex = nullptr;
  bool locked = false;
  ~AllocatorLock() noexcept {
    if (locked) ReleaseMutex(mutex);
    if (mutex != nullptr) CloseHandle(mutex);
  }
};

struct StoreRootAuthority final {
  std::wstring canonicalPath{};
  Sha256 identity{};
  HANDLE directory = INVALID_HANDLE_VALUE;
  ~StoreRootAuthority() noexcept {
    if (directory != INVALID_HANDLE_VALUE) CloseHandle(directory);
  }
  HANDLE ReleaseDirectory() noexcept {
    const HANDLE released = directory;
    directory = INVALID_HANDLE_VALUE;
    return released;
  }
};

bool ResolveStoreRoot(const std::wstring &root, bool create,
                      StoreRootAuthority *authority) noexcept {
  if (authority == nullptr || root.empty()) return false;
  if (create && !CreateDirectoryW(root.c_str(), nullptr) &&
      GetLastError() != ERROR_ALREADY_EXISTS)
    return false;
  HANDLE directory = CreateFileW(
      root.c_str(), FILE_READ_ATTRIBUTES,
      FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
      OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
  FILE_STANDARD_INFO standard{};
  FILE_ID_INFO fileId{};
  std::array<wchar_t, 32768> path{};
  const DWORD length = directory == INVALID_HANDLE_VALUE ? 0 :
      GetFinalPathNameByHandleW(directory, path.data(), path.size(),
                                FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  const bool valid = directory != INVALID_HANDLE_VALUE &&
      GetFileInformationByHandleEx(directory, FileStandardInfo, &standard,
                                   sizeof(standard)) != FALSE &&
      standard.Directory != FALSE &&
      GetFileInformationByHandleEx(directory, FileIdInfo, &fileId,
                                   sizeof(fileId)) != FALSE &&
      length != 0 && length < path.size();
  if (!valid) {
    if (directory != INVALID_HANDLE_VALUE) CloseHandle(directory);
    return false;
  }
  Bytes material;
  U64(&material, fileId.VolumeSerialNumber);
  material.insert(material.end(), std::begin(fileId.FileId.Identifier),
                  std::end(fileId.FileId.Identifier));
  authority->canonicalPath.assign(path.data(), length);
  authority->identity = codec::Hash(codec::View(material));
  authority->directory = directory;
  return true;
}

bool AcquireAllocatorLock(const Sha256 &digest,
                          AllocatorLock *lock) noexcept {
  if (lock == nullptr) return false;
  wchar_t name[96]{};
  if (FAILED(StringCchPrintfW(
          name, 96, L"Local\\HancomGraphStore-%08lx%08lx%08lx%08lx",
          static_cast<unsigned long>(R32(digest.bytes.data())),
          static_cast<unsigned long>(R32(digest.bytes.data() + 4)),
          static_cast<unsigned long>(R32(digest.bytes.data() + 8)),
          static_cast<unsigned long>(R32(digest.bytes.data() + 12)))))
    return false;
  lock->mutex = CreateMutexW(nullptr, FALSE, name);
  if (lock->mutex == nullptr) return false;
  const DWORD wait = WaitForSingleObject(lock->mutex, INFINITE);
  lock->locked = wait == WAIT_OBJECT_0 || wait == WAIT_ABANDONED;
  return lock->locked;
}

enum class IdentityRead : std::uint8_t { Missing, Valid, Invalid };

IdentityRead ReadStoreIdentity(const std::wstring &root,
                               StoreEpoch *epoch) noexcept {
  wchar_t path[32768]{};
  if (epoch == nullptr || !Join(root.c_str(), L"store-identity.hgs", path))
    return IdentityRead::Invalid;
  HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE)
    return GetLastError() == ERROR_FILE_NOT_FOUND
        ? IdentityRead::Missing : IdentityRead::Invalid;
  std::array<std::uint8_t, kStoreIdentityBytes> bytes{};
  std::uint64_t length = 0;
  const bool read = FileLength(file, &length) && length == bytes.size() &&
      Seek(file, 0) &&
      ReadExact(file, bytes.data(), static_cast<DWORD>(bytes.size()));
  CloseHandle(file);
  if (!read || std::memcmp(bytes.data(), "HGS1", 4) != 0 ||
      R16(bytes.data() + 4) != 1 || R16(bytes.data() + 6) != 1)
    return IdentityRead::Invalid;
  const Sha256 checksum = codec::Hash({bytes.data(), 24});
  if (!std::equal(checksum.bytes.begin(), checksum.bytes.end(),
                  bytes.begin() + 24) ||
      std::all_of(bytes.begin() + 8, bytes.begin() + 24,
                  [](std::uint8_t value) { return value == 0; }))
    return IdentityRead::Invalid;
  std::copy_n(bytes.begin() + 8, epoch->size(), epoch->begin());
  return IdentityRead::Valid;
}

bool CreateStoreIdentity(const std::wstring &root, StoreEpoch *epoch,
                         FailurePoint failure) noexcept {
  if (epoch == nullptr || failure == FailurePoint::OpenIdentity) return false;
  BCRYPT_ALG_HANDLE random = nullptr;
  StoreEpoch generated{};
  const bool randomOk = BCryptOpenAlgorithmProvider(
      &random, BCRYPT_RNG_ALGORITHM, nullptr, 0) >= 0 &&
      BCryptGenRandom(random, generated.data(),
                      static_cast<ULONG>(generated.size()), 0) >= 0;
  if (random != nullptr) BCryptCloseAlgorithmProvider(random, 0);
  if (!randomOk || std::all_of(generated.begin(), generated.end(),
                               [](std::uint8_t value) { return value == 0; }))
    return false;
  Bytes bytes{'H', 'G', 'S', '1'};
  U16(&bytes, 1); U16(&bytes, 1);
  bytes.insert(bytes.end(), generated.begin(), generated.end());
  AddDigest(&bytes, codec::Hash(codec::View(bytes)));
  wchar_t path[32768]{}, temporary[32768]{}, name[96]{};
  if (FAILED(StringCchPrintfW(name, 96, L"store-identity-%lu-%lu.tmp",
                              GetCurrentProcessId(), GetCurrentThreadId())) ||
      !Join(root.c_str(), L"store-identity.hgs", path) ||
      !Join(root.c_str(), name, temporary)) return false;
  DeleteFileW(temporary);
  HANDLE file = CreateFileW(temporary, GENERIC_READ | GENERIC_WRITE, 0,
                            nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  const bool written = file != INVALID_HANDLE_VALUE &&
      failure != FailurePoint::WriteIdentity &&
      WriteBytes(file, bytes.data(), bytes.size());
  const bool flushed = written && failure != FailurePoint::FlushIdentity &&
      FlushFileBuffers(file) != FALSE;
  if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  const bool replaced = flushed && failure != FailurePoint::ReplaceIdentity &&
      MoveFileExW(temporary, path,
                  MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != FALSE;
  if (!replaced) DeleteFileW(temporary);
  StoreEpoch verified{};
  if (!replaced || ReadStoreIdentity(root, &verified) != IdentityRead::Valid ||
      verified != generated) return false;
  *epoch = generated;
  return true;
}

struct ReservationState final {
  std::uint64_t serial = 0;
  Sha256 tail{};
};

ReservationRead ReadReservation(const std::wstring &root,
                                const StoreEpoch &epoch,
                                ReservationState *state) noexcept {
  wchar_t path[32768]{};
  if (state == nullptr || !Join(root.c_str(), L"high-water.hgr", path))
    return ReservationRead::Invalid;
  HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE)
    return GetLastError() == ERROR_FILE_NOT_FOUND
        ? ReservationRead::Missing : ReservationRead::Invalid;
  std::array<std::uint8_t, kReservationBytes> bytes{};
  std::uint64_t length = 0;
  const bool sized = FileLength(file, &length) &&
      (length == bytes.size() || length == 80 || length == 56) && Seek(file, 0);
  const bool read = sized && ReadExact(
      file, bytes.data(), static_cast<DWORD>(length));
  CloseHandle(file);
  if (read && length == 56 && std::memcmp(bytes.data(), "HGR1", 4) == 0 &&
      R16(bytes.data() + 4) == 1 && R16(bytes.data() + 6) == 1 &&
      R64(bytes.data() + 16) == 0) {
    const Sha256 legacyChecksum = codec::Hash({bytes.data(), 24});
    if (std::equal(legacyChecksum.bytes.begin(), legacyChecksum.bytes.end(),
                   bytes.begin() + 24)) {
      state->serial = R64(bytes.data() + 8);
      return ReservationRead::Legacy;
    }
  }
  if (read && length == 80 && std::memcmp(bytes.data(), "HGR2", 4) == 0 &&
      R16(bytes.data() + 4) == 2 && R16(bytes.data() + 6) == 1) {
    const Sha256 legacyChecksum = codec::Hash({bytes.data(), 48});
    if (std::equal(legacyChecksum.bytes.begin(), legacyChecksum.bytes.end(),
                   bytes.begin() + 48)) {
      state->serial = R64(bytes.data() + 8);
      state->tail = ReadDigest(bytes.data() + 16);
      return ReservationRead::Legacy;
    }
  }
  if (!read || length != bytes.size() ||
      std::memcmp(bytes.data(), "HGR3", 4) != 0 ||
      R16(bytes.data() + 4) != 3 || R16(bytes.data() + 6) != 1 ||
      !std::equal(epoch.begin(), epoch.end(), bytes.begin() + 16))
    return ReservationRead::Invalid;
  const Sha256 checksum = codec::Hash({bytes.data(), 64});
  if (!std::equal(checksum.bytes.begin(), checksum.bytes.end(),
                  bytes.begin() + 64)) return ReservationRead::Invalid;
  state->serial = R64(bytes.data() + 8);
  state->tail = ReadDigest(bytes.data() + 32);
  return ReservationRead::Valid;
}

Bytes JournalEntry(std::uint64_t serial, const StoreEpoch &epoch,
                   const Sha256 &previous) {
  Bytes bytes{'H', 'G', 'J', '2'};
  U16(&bytes, 2);
  U16(&bytes, 1);
  U64(&bytes, serial);
  bytes.insert(bytes.end(), epoch.begin(), epoch.end());
  AddDigest(&bytes, previous);
  const Sha256 checksum = codec::Hash(codec::View(bytes));
  AddDigest(&bytes, checksum);
  return bytes;
}

ReservationRead ReadJournal(const std::wstring &root,
                            const StoreEpoch &epoch,
                            ReservationState *state) noexcept {
  wchar_t path[32768]{};
  if (state == nullptr || !Join(root.c_str(), L"reservations.hgj", path))
    return ReservationRead::Invalid;
  HANDLE file = CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                            FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                            FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE)
    return GetLastError() == ERROR_FILE_NOT_FOUND
        ? ReservationRead::Missing : ReservationRead::Invalid;
  std::uint64_t length = 0;
  if (!FileLength(file, &length)) { CloseHandle(file); return ReservationRead::Invalid; }
  const std::uint64_t complete = length / kJournalEntryBytes;
  const std::uint64_t remainder = length % kJournalEntryBytes;
  Sha256 previous{};
  std::uint64_t serial = 0;
  std::array<std::uint8_t, kJournalEntryBytes> bytes{};
  bool valid = complete != 0 && Seek(file, 0);
  for (std::uint64_t index = 0; valid && index != complete; ++index) {
    valid = ReadExact(file, bytes.data(), static_cast<DWORD>(bytes.size())) &&
        std::memcmp(bytes.data(), "HGJ2", 4) == 0 &&
        R16(bytes.data() + 4) == 2 && R16(bytes.data() + 6) == 1 &&
        std::equal(epoch.begin(), epoch.end(), bytes.begin() + 16) &&
        std::equal(previous.bytes.begin(), previous.bytes.end(),
                   bytes.begin() + 32);
    const std::uint64_t next = R64(bytes.data() + 8);
    const Sha256 checksum = codec::Hash({bytes.data(), 64});
    valid = valid && next >= serial &&
        (index == 0 || next > serial) &&
        std::equal(checksum.bytes.begin(), checksum.bytes.end(),
                   bytes.begin() + 64);
    if (valid) {
      serial = next;
      previous = codec::Hash({bytes.data(), bytes.size()});
    }
  }
  if (valid && remainder != 0) {
    LARGE_INTEGER end{};
    end.QuadPart = static_cast<LONGLONG>(complete * kJournalEntryBytes);
    valid = SetFilePointerEx(file, end, nullptr, FILE_BEGIN) &&
        SetEndOfFile(file) && FlushFileBuffers(file);
  }
  CloseHandle(file);
  if (!valid) return ReservationRead::Invalid;
  state->serial = serial;
  state->tail = previous;
  return ReservationRead::Valid;
}

bool WriteCheckpoint(const std::wstring &root, const StoreEpoch &epoch,
                     const ReservationState &state,
                     FailurePoint failure) noexcept {
  wchar_t path[32768]{}, temporary[32768]{}, name[96]{};
  if (FAILED(StringCchPrintfW(name, 96, L"high-water-%lu-%lu.tmp",
                              GetCurrentProcessId(), GetCurrentThreadId())) ||
      !Join(root.c_str(), L"high-water.hgr", path) ||
      !Join(root.c_str(), name, temporary)) return false;
  DeleteFileW(temporary);
  Bytes bytes{'H', 'G', 'R', '3'};
  U16(&bytes, 3); U16(&bytes, 1); U64(&bytes, state.serial);
  bytes.insert(bytes.end(), epoch.begin(), epoch.end());
  AddDigest(&bytes, state.tail);
  AddDigest(&bytes, codec::Hash(codec::View(bytes)));
  HANDLE file = CreateFileW(temporary, GENERIC_READ | GENERIC_WRITE, 0,
                            nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  const bool written = file != INVALID_HANDLE_VALUE &&
      bytes.size() == kReservationBytes &&
      WriteBytes(file, bytes.data(), bytes.size());
  const bool flushed = written && FlushFileBuffers(file) != FALSE;
  if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  const bool replaced = flushed && failure != FailurePoint::ReplaceReservation &&
      MoveFileExW(temporary, path,
                  MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != FALSE;
  if (!replaced) DeleteFileW(temporary);
  ReservationState verified{};
  return replaced &&
      ReadReservation(root, epoch, &verified) == ReservationRead::Valid &&
      verified.serial == state.serial && codec::Equal(verified.tail, state.tail);
}

bool ReconcileReservation(const std::wstring &root, const StoreEpoch &epoch,
                          ReservationState *state,
                          FailurePoint failure) noexcept {
  ReservationState checkpoint{}, journal{};
  const ReservationRead checkpointRead =
      ReadReservation(root, epoch, &checkpoint);
  const ReservationRead journalRead = ReadJournal(root, epoch, &journal);
  if (checkpointRead == ReservationRead::Invalid ||
      journalRead == ReservationRead::Invalid ||
      (checkpointRead == ReservationRead::Legacy &&
       journalRead != ReservationRead::Missing)) return false;
  if ((checkpointRead == ReservationRead::Missing ||
       checkpointRead == ReservationRead::Legacy) &&
      journalRead == ReservationRead::Missing) {
    wchar_t journalPath[32768]{};
    if (failure == FailurePoint::OpenReservation ||
        !Join(root.c_str(), L"reservations.hgj", journalPath)) return false;
    const std::uint64_t bootstrapSerial =
        checkpointRead == ReservationRead::Legacy ? checkpoint.serial : 0;
    const Bytes genesis = JournalEntry(bootstrapSerial, epoch, {});
    HANDLE file = CreateFileW(journalPath, GENERIC_WRITE, FILE_SHARE_READ,
                              nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    const bool written = file != INVALID_HANDLE_VALUE &&
        failure != FailurePoint::WriteReservation &&
        WriteBytes(file, genesis.data(), genesis.size());
    const bool flushed = written && failure != FailurePoint::FlushReservation &&
        FlushFileBuffers(file) != FALSE;
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (!flushed) { DeleteFileW(journalPath); return false; }
    journal.serial = bootstrapSerial;
    journal.tail = codec::Hash(codec::View(genesis));
    if (!WriteCheckpoint(root, epoch, journal, failure)) return false;
    *state = journal;
    return true;
  }
  if (journalRead != ReservationRead::Valid) return false;
  if (checkpointRead == ReservationRead::Missing ||
      checkpoint.serial < journal.serial) {
    if (!WriteCheckpoint(root, epoch, journal, failure)) return false;
    *state = journal;
    return true;
  }
  if (checkpoint.serial != journal.serial ||
      !codec::Equal(checkpoint.tail, journal.tail)) return false;
  *state = checkpoint;
  return true;
}

bool PersistReservation(const std::wstring &root, const StoreEpoch &epoch,
                        std::uint64_t serial, FailurePoint failure) noexcept {
  ReservationState current{};
  if (!ReconcileReservation(root, epoch, &current, failure) ||
      serial <= current.serial ||
      failure == FailurePoint::OpenReservation) return false;
  wchar_t journalPath[32768]{};
  if (!Join(root.c_str(), L"reservations.hgj", journalPath)) return false;
  const Bytes entry = JournalEntry(serial, epoch, current.tail);
  HANDLE journal = CreateFileW(journalPath, FILE_APPEND_DATA, FILE_SHARE_READ,
                               nullptr, OPEN_EXISTING,
                               FILE_ATTRIBUTE_NORMAL, nullptr);
  const bool written = journal != INVALID_HANDLE_VALUE &&
      failure != FailurePoint::WriteReservation &&
      WriteBytes(journal, entry.data(), entry.size());
  const bool flushed = written && failure != FailurePoint::FlushReservation &&
      FlushFileBuffers(journal) != FALSE;
  if (journal != INVALID_HANDLE_VALUE) CloseHandle(journal);
  if (!flushed) return false;
  ReservationState next{serial, codec::Hash(codec::View(entry))};
  return WriteCheckpoint(root, epoch, next, failure);
}

struct LegacyState final {
  std::vector<std::uint64_t> reservations{};
  std::vector<std::wstring> committed{};
  std::uint64_t highWater = 0;
  Sha256 inventory{};
};

bool ReadWholeFile(const wchar_t *path, Bytes *bytes) noexcept {
  HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  std::uint64_t length = 0;
  const bool sized = file != INVALID_HANDLE_VALUE && FileLength(file, &length) &&
      length <= MAXDWORD &&
      length <= static_cast<std::uint64_t>((std::numeric_limits<std::size_t>::max)());
  if (sized) bytes->resize(static_cast<std::size_t>(length));
  const bool read = sized && Seek(file, 0) &&
      (bytes->empty() || ReadExact(file, bytes->data(),
                                  static_cast<DWORD>(bytes->size())));
  if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  return read;
}

bool WriteDurableFile(const wchar_t *path, const Bytes &bytes) noexcept {
  HANDLE file = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  const bool ok = file != INVALID_HANDLE_VALUE &&
      WriteBytes(file, bytes.data(), bytes.size()) &&
      FlushFileBuffers(file) != FALSE;
  if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  return ok;
}

bool InspectLegacyRoot(const std::wstring &root, LegacyState *legacy) noexcept {
  wchar_t checkpointPath[32768]{}, journalPath[32768]{};
  if (legacy == nullptr || !Join(root.c_str(), L"high-water.hgr", checkpointPath) ||
      !Join(root.c_str(), L"reservations.hgj", journalPath)) return false;
  ReservationState checkpoint{};
  StoreEpoch none{};
  if (ReadReservation(root, none, &checkpoint) != ReservationRead::Legacy)
    return false;
  legacy->highWater = checkpoint.serial;
  Bytes journal;
  const DWORD journalAttributes = GetFileAttributesW(journalPath);
  if (journalAttributes == INVALID_FILE_ATTRIBUTES) {
    if (!codec::Equal(checkpoint.tail, Sha256{})) return false;
    legacy->reservations.push_back(checkpoint.serial);
  } else {
    if (!ReadWholeFile(journalPath, &journal) || journal.empty() ||
        journal.size() % 80 != 0) return false;
    Sha256 previous{};
    std::uint64_t prior = 0;
    for (std::size_t offset = 0; offset != journal.size(); offset += 80) {
      const std::uint8_t *entry = journal.data() + offset;
      const std::uint64_t serial = R64(entry + 8);
      const Sha256 checksum = codec::Hash({entry, 48});
      if (std::memcmp(entry, "HGJ1", 4) != 0 || R16(entry + 4) != 1 ||
          R16(entry + 6) != 1 || (offset != 0 && serial <= prior) ||
          !std::equal(previous.bytes.begin(), previous.bytes.end(), entry + 16) ||
          !std::equal(checksum.bytes.begin(), checksum.bytes.end(), entry + 48))
        return false;
      previous = codec::Hash({entry, 80});
      prior = serial;
      legacy->reservations.push_back(serial);
    }
    if (prior != checkpoint.serial || !codec::Equal(previous, checkpoint.tail))
      return false;
  }

  wchar_t pattern[32768]{};
  if (!Join(root.c_str(), L"generation-*", pattern)) return false;
  WIN32_FIND_DATAW data{};
  HANDLE find = FindFirstFileW(pattern, &data);
  std::uint64_t generationMax = 0;
  if (find != INVALID_HANDLE_VALUE) {
    do {
      if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0) continue;
      std::uint64_t serial = 0, records = 0, index = 0;
      wchar_t path[32768]{};
      if (!ParseSerial(data.cFileName, &serial) ||
          !Join(root.c_str(), data.cFileName, path) ||
          !ValidateGeneration(path, serial, &records, &index) ||
          serial > checkpoint.serial) { FindClose(find); return false; }
      generationMax = (std::max)(generationMax, serial);
      wchar_t commitPath[32768]{};
      if (!Join(path, L"commit.hgc", commitPath)) { FindClose(find); return false; }
      if (GetFileAttributesW(commitPath) != INVALID_FILE_ATTRIBUTES) {
        if (!ValidateCommitMarker(path, serial, none, true)) {
          FindClose(find); return false;
        }
        legacy->committed.emplace_back(path);
      }
    } while (FindNextFileW(find, &data));
    FindClose(find);
  }
  if (generationMax > checkpoint.serial) return false;
  std::sort(legacy->committed.begin(), legacy->committed.end());
  Bytes inventory;
  Bytes checkpointBytes;
  if (!ReadWholeFile(checkpointPath, &checkpointBytes)) return false;
  inventory.insert(inventory.end(), checkpointBytes.begin(), checkpointBytes.end());
  inventory.insert(inventory.end(), journal.begin(), journal.end());
  for (const std::wstring &generation : legacy->committed) {
    wchar_t manifest[32768]{}, commit[32768]{};
    Bytes bytes;
    if (!Join(generation.c_str(), L"manifest.hgm", manifest) ||
        !Join(generation.c_str(), L"commit.hgc", commit) ||
        !ReadWholeFile(manifest, &bytes)) return false;
    inventory.insert(inventory.end(), bytes.begin(), bytes.end());
    bytes.clear();
    if (!ReadWholeFile(commit, &bytes)) return false;
    inventory.insert(inventory.end(), bytes.begin(), bytes.end());
  }
  legacy->inventory = codec::Hash(codec::View(inventory));
  return true;
}

struct InventoryOverride final {
  Bytes bytes{};
  DWORD attributes = FILE_ATTRIBUTE_NORMAL;
};
using InventoryOverrides = std::map<std::wstring, InventoryOverride>;
struct InventoryEntry final {
  std::wstring path{};
  DWORD attributes = 0;
  std::uint64_t length = 0;
  Sha256 digest{};
};
constexpr DWORD kReceiptAttributes = FILE_ATTRIBUTE_READONLY |
    FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT;

bool IsMigrationReplacementTemp(const std::wstring &relative) noexcept {
  if (relative == L"reservations.hgj.migration.tmp" ||
      relative == L"high-water.hgr.migration.tmp" ||
      relative == L"store-identity.hgs.migration.tmp")
    return true;
  const std::filesystem::path path(relative);
  std::uint64_t serial = 0;
  return path.filename() == L"commit.hgc.migration.tmp" &&
      path.has_parent_path() &&
      ParseSerial(path.parent_path().filename().c_str(), &serial) &&
      path.parent_path().parent_path().empty();
}

bool CanonicalInventory(const std::wstring &root, Sha256 *digest,
                        const InventoryOverrides *overrides = nullptr) noexcept {
  try {
    std::vector<InventoryEntry> files;
    std::set<std::wstring> consumedOverrides;
    if (overrides != nullptr) {
      for (const auto &entry : *overrides) {
        const std::filesystem::path relative(entry.first);
        if (entry.first.empty() || relative.is_absolute() ||
            relative.has_root_name() || relative.has_root_directory() ||
            relative.generic_wstring() != entry.first ||
            entry.first == L"migration.intent" ||
            entry.first == L"migration-stage" ||
            entry.first.rfind(L"migration-stage/", 0) == 0)
          return false;
        for (const auto &part : relative)
          if (part.empty() || part == L"." || part == L"..") return false;
      }
    }
    std::error_code error;
    for (std::filesystem::recursive_directory_iterator item(
             root, std::filesystem::directory_options::skip_permission_denied,
             error), end;
         !error && item != end; item.increment(error)) {
      const auto relative = item->path().lexically_relative(root).generic_wstring();
      if (relative == L"migration.intent" ||
          relative.rfind(L"migration-stage/", 0) == 0 ||
          relative == L"migration-stage" ||
          IsMigrationReplacementTemp(relative)) {
        if (item->is_directory(error)) item.disable_recursion_pending();
        continue;
      }
      const DWORD attributes = GetFileAttributesW(item->path().c_str());
      if (attributes == INVALID_FILE_ATTRIBUTES ||
          (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) return false;
      if ((attributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        files.push_back({relative, attributes & kReceiptAttributes, 0, {}});
        continue;
      }
      if (!item->is_regular_file(error)) continue;
      const auto replacement = overrides == nullptr
          ? InventoryOverrides::const_iterator{}
          : overrides->find(relative);
      if (overrides != nullptr && replacement != overrides->end()) {
        if (!consumedOverrides.insert(relative).second) return false;
        files.push_back({relative,
                         replacement->second.attributes & kReceiptAttributes,
                         replacement->second.bytes.size(),
                         codec::Hash(codec::View(replacement->second.bytes))});
        continue;
      }
      HANDLE file = CreateFileW(item->path().c_str(), GENERIC_READ,
                                FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                                FILE_ATTRIBUTE_NORMAL, nullptr);
      std::uint64_t length = 0;
      Sha256 hash{};
      const bool valid = file != INVALID_HANDLE_VALUE &&
          FileLength(file, &length) && HashFileRangeChunked(file, 0, length, &hash);
      if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
      if (!valid) return false;
      files.push_back(
          {relative, attributes & kReceiptAttributes, length, hash});
    }
    if (error) return false;
    if (overrides != nullptr) {
      for (const auto &entry : *overrides) {
        if (consumedOverrides.find(entry.first) != consumedOverrides.end())
          continue;
        const std::filesystem::path target =
            std::filesystem::path(root) / entry.first;
        const DWORD attributes = GetFileAttributesW(target.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES &&
            (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
          return false;
        files.push_back({entry.first,
                         entry.second.attributes & kReceiptAttributes,
                         entry.second.bytes.size(),
                         codec::Hash(codec::View(entry.second.bytes))});
      }
    }
    std::sort(files.begin(), files.end(),
              [](const auto &a, const auto &b) { return a.path < b.path; });
    for (std::size_t index = 1; index != files.size(); ++index)
      if (files[index - 1].path == files[index].path) return false;
    Bytes canonical;
    for (const auto &file : files) {
      const auto *name = reinterpret_cast<const std::uint8_t *>(file.path.data());
      U64(&canonical, file.path.size());
      canonical.insert(canonical.end(), name,
                       name + file.path.size() * sizeof(wchar_t));
      U32(&canonical, file.attributes);
      U64(&canonical, file.length);
      AddDigest(&canonical, file.digest);
    }
    *digest = codec::Hash(codec::View(canonical));
    return true;
  } catch (...) { return false; }
}

bool CanonicalRootBinding(const std::wstring &root, Sha256 *digest) noexcept {
  StoreRootAuthority authority;
  if (digest == nullptr || !ResolveStoreRoot(root, false, &authority))
    return false;
  *digest = authority.identity;
  return true;
}

struct DurableFileFact final {
  std::uint64_t length = 0;
  Sha256 digest{};
};

bool ReadDurableFileFact(const wchar_t *path, DurableFileFact *fact) noexcept {
  HANDLE file = CreateFileW(path, GENERIC_READ,
                            FILE_SHARE_READ | FILE_SHARE_WRITE |
                                FILE_SHARE_DELETE,
                            nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  const bool valid = file != INVALID_HANDLE_VALUE &&
      FileLength(file, &fact->length) &&
      HashFileRangeChunked(file, 0, fact->length, &fact->digest);
  if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  return valid;
}

bool EqualFileFact(const DurableFileFact &a,
                   const DurableFileFact &b) noexcept {
  return a.length == b.length && codec::Equal(a.digest, b.digest);
}

bool ReplaceDurable(const wchar_t *source, const wchar_t *destination,
                    FailurePoint failure, bool immutable = false) noexcept {
  wchar_t temporary[32768]{};
  if (FAILED(StringCchPrintfW(temporary, 32768, L"%ls.migration.tmp",
                              destination)))
    return false;
  SetFileAttributesW(temporary, FILE_ATTRIBUTE_NORMAL);
  DeleteFileW(temporary);
  if (!CopyFileW(source, temporary, TRUE)) return false;
  if (failure == FailurePoint::MigrationTempWrite) return false;
  HANDLE file = CreateFileW(temporary, GENERIC_READ | GENERIC_WRITE,
                            FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                            FILE_ATTRIBUTE_NORMAL, nullptr);
  const bool flushed = file != INVALID_HANDLE_VALUE &&
      failure != FailurePoint::MigrationTempFlush &&
      FlushFileBuffers(file) != FALSE;
  if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  if (!flushed || failure == FailurePoint::MigrationTempReplace) return false;

  DurableFileFact replacementFact{};
  if (!ReadDurableFileFact(temporary, &replacementFact)) return false;
  const DWORD originalAttributes = GetFileAttributesW(destination);
  const bool destinationExists = originalAttributes != INVALID_FILE_ATTRIBUTES;
  if (destinationExists &&
      (originalAttributes & (FILE_ATTRIBUTE_DIRECTORY |
                             FILE_ATTRIBUTE_REPARSE_POINT)) != 0)
    return false;
  DurableFileFact originalFact{};
  if (destinationExists && !ReadDurableFileFact(destination, &originalFact))
    return false;
  DWORD finalAttributes = destinationExists
      ? originalAttributes : GetFileAttributesW(temporary);
  if (finalAttributes == INVALID_FILE_ATTRIBUTES) return false;
  if (immutable) finalAttributes |= FILE_ATTRIBUTE_READONLY;
  if (!SetFileAttributesW(temporary, finalAttributes)) return false;

  if (destinationExists &&
      (originalAttributes & FILE_ATTRIBUTE_READONLY) != 0) {
    DWORD cleared = originalAttributes & ~FILE_ATTRIBUTE_READONLY;
    if (cleared == 0) cleared = FILE_ATTRIBUTE_NORMAL;
    if (!SetFileAttributesW(destination, cleared)) return false;
    if (failure == FailurePoint::MigrationAfterReadonlyClear) return false;
  }
  const DWORD flags = MOVEFILE_WRITE_THROUGH |
      (destinationExists ? MOVEFILE_REPLACE_EXISTING : 0);
  if (!MoveFileExW(temporary, destination, flags)) {
    DurableFileFact afterFailure{};
    if (destinationExists && ReadDurableFileFact(destination, &afterFailure) &&
        EqualFileFact(afterFailure, originalFact))
      SetFileAttributesW(destination, originalAttributes);
    return false;
  }
  if (failure == FailurePoint::MigrationAfterMarkerReplace && immutable)
    return false;
  if (!SetFileAttributesW(destination, finalAttributes)) return false;
  DurableFileFact installed{};
  return ReadDurableFileFact(destination, &installed) &&
      EqualFileFact(installed, replacementFact) &&
      GetFileAttributesW(destination) == finalAttributes;
}

bool RepairMigratedMarkers(const std::wstring &root, const wchar_t *stage,
                           std::size_t count,
                           const StoreEpoch &epoch) noexcept {
  for (std::size_t index = 0; index != count; ++index) {
    wchar_t name[96]{}, source[32768]{}, destination[32768]{};
    Bytes commit;
    if (FAILED(StringCchPrintfW(name, 96, L"commit-%zu.hgc", index)) ||
        !Join(stage, name, source) || !ReadWholeFile(source, &commit) ||
        commit.size() != kCommitBytes ||
        std::memcmp(commit.data(), "HGC2", 4) != 0 ||
        R16(commit.data() + 4) != 2 || R16(commit.data() + 6) != 1 ||
        !std::equal(epoch.begin(), epoch.end(), commit.begin() + 16) ||
        FAILED(StringCchPrintfW(
            name, 96, L"generation-%020llu",
            static_cast<unsigned long long>(R64(commit.data() + 8)))) ||
        !Join(root.c_str(), name, destination))
      return false;
    const DWORD attributes = GetFileAttributesW(destination);
    if (attributes == INVALID_FILE_ATTRIBUTES ||
        (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0)
      return false;
    if (!ValidateCommitMarker(destination, R64(commit.data() + 8), epoch,
                              false, false))
      return false;
    wchar_t marker[32768]{};
    if (!Join(destination, L"commit.hgc", marker)) return false;
    const DWORD markerAttributes = GetFileAttributesW(marker);
    if (markerAttributes == INVALID_FILE_ATTRIBUTES ||
        (markerAttributes & (FILE_ATTRIBUTE_DIRECTORY |
                             FILE_ATTRIBUTE_REPARSE_POINT)) != 0 ||
        !SetFileAttributesW(marker,
                            markerAttributes | FILE_ATTRIBUTE_READONLY) ||
        !ValidateCommitMarker(destination, R64(commit.data() + 8), epoch))
      return false;
  }
  return true;
}

bool CompleteLegacyMigration(const std::wstring &root,
                             const LegacyState &legacy,
                             FailurePoint failure) noexcept {
  wchar_t stage[32768]{}, intentPath[32768]{};
  if (!Join(root.c_str(), L"migration-stage", stage) ||
      !Join(root.c_str(), L"migration.intent", intentPath)) return false;
  if (!CreateDirectoryW(stage, nullptr) && GetLastError() != ERROR_ALREADY_EXISTS)
    return false;
  StoreEpoch epoch{};
  if (!CreateStoreIdentity(stage, &epoch, failure)) {
    DeleteTree(stage);
    return false;
  }
  Bytes journal;
  Sha256 tail{};
  for (const std::uint64_t serial : legacy.reservations) {
    const Bytes entry = JournalEntry(serial, epoch, tail);
    tail = codec::Hash(codec::View(entry));
    journal.insert(journal.end(), entry.begin(), entry.end());
  }
  wchar_t stagedJournal[32768]{}, stagedCheckpoint[32768]{};
  if (!Join(stage, L"reservations.hgj", stagedJournal) ||
      !Join(stage, L"high-water.hgr", stagedCheckpoint) ||
      !WriteDurableFile(stagedJournal, journal)) { DeleteTree(stage); return false; }
  ReservationState state{legacy.highWater, tail};
  Bytes checkpoint{'H', 'G', 'R', '3'};
  U16(&checkpoint, 3); U16(&checkpoint, 1); U64(&checkpoint, state.serial);
  checkpoint.insert(checkpoint.end(), epoch.begin(), epoch.end());
  AddDigest(&checkpoint, state.tail);
  AddDigest(&checkpoint, codec::Hash(codec::View(checkpoint)));
  if (!WriteDurableFile(stagedCheckpoint, checkpoint)) {
    DeleteTree(stage); return false;
  }
  for (std::size_t index = 0; index != legacy.committed.size(); ++index) {
    wchar_t manifestPath[32768]{}, name[96]{}, stagedCommit[32768]{};
    Bytes manifest;
    if (!Join(legacy.committed[index].c_str(), L"manifest.hgm", manifestPath) ||
        !ReadWholeFile(manifestPath, &manifest) || manifest.size() != kManifestBytes ||
        FAILED(StringCchPrintfW(name, 96, L"commit-%zu.hgc", index)) ||
        !Join(stage, name, stagedCommit)) { DeleteTree(stage); return false; }
    Bytes commit{'H', 'G', 'C', '2'};
    U16(&commit, 2); U16(&commit, 1);
    U64(&commit, R64(manifest.data() + 8));
    commit.insert(commit.end(), epoch.begin(), epoch.end());
    commit.insert(commit.end(), manifest.begin() + 540, manifest.begin() + 572);
    if (!WriteDurableFile(stagedCommit, commit)) { DeleteTree(stage); return false; }
  }
  Sha256 rootBinding{}, stageInventory{};
  std::vector<Sha256> sourceStates(3);
  InventoryOverrides overrides;
  Bytes stagedBytes;
  if (!CanonicalRootBinding(root, &rootBinding) ||
      !CanonicalInventory(root, &sourceStates[0]) ||
      !CanonicalInventory(stage, &stageInventory) ||
      !ReadWholeFile(stagedJournal, &stagedBytes)) {
    DeleteTree(stage);
    return false;
  }
  DWORD destinationAttributes = GetFileAttributesW(
      (std::filesystem::path(root) / L"reservations.hgj").c_str());
  if (destinationAttributes == INVALID_FILE_ATTRIBUTES) {
    const DWORD error = GetLastError();
    if (error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND) {
      DeleteTree(stage);
      return false;
    }
    destinationAttributes = GetFileAttributesW(stagedJournal);
    if (destinationAttributes == INVALID_FILE_ATTRIBUTES) {
      DeleteTree(stage);
      return false;
    }
  }
  overrides[L"reservations.hgj"] = {stagedBytes, destinationAttributes};
  if (!CanonicalInventory(root, &sourceStates[1], &overrides) ||
      !ReadWholeFile(stagedCheckpoint, &stagedBytes)) {
    DeleteTree(stage);
    return false;
  }
  destinationAttributes = GetFileAttributesW(
      (std::filesystem::path(root) / L"high-water.hgr").c_str());
  if (destinationAttributes == INVALID_FILE_ATTRIBUTES) {
    DeleteTree(stage);
    return false;
  }
  overrides[L"high-water.hgr"] = {stagedBytes, destinationAttributes};
  if (!CanonicalInventory(root, &sourceStates[2], &overrides)) {
    DeleteTree(stage);
    return false;
  }
  for (std::size_t index = 0; index != legacy.committed.size(); ++index) {
    wchar_t name[96]{}, stagedCommit[32768]{};
    if (FAILED(StringCchPrintfW(name, 96, L"commit-%zu.hgc", index)) ||
        !Join(stage, name, stagedCommit) ||
        !ReadWholeFile(stagedCommit, &stagedBytes)) {
      DeleteTree(stage);
      return false;
    }
    const std::filesystem::path relative =
        std::filesystem::path(legacy.committed[index]).lexically_relative(root);
    const std::filesystem::path commitPath =
        std::filesystem::path(legacy.committed[index]) / L"commit.hgc";
    destinationAttributes = GetFileAttributesW(commitPath.c_str());
    if (destinationAttributes == INVALID_FILE_ATTRIBUTES) {
      DeleteTree(stage);
      return false;
    }
    Bytes legacyCommit;
    if (!ReadWholeFile(commitPath.c_str(), &legacyCommit)) {
      DeleteTree(stage);
      return false;
    }
    DWORD clearedAttributes =
        destinationAttributes & ~FILE_ATTRIBUTE_READONLY;
    if (clearedAttributes == 0) clearedAttributes = FILE_ATTRIBUTE_NORMAL;
    overrides[relative.generic_wstring() + L"/commit.hgc"] = {
        legacyCommit, clearedAttributes};
    Sha256 clearedState{};
    if (!CanonicalInventory(root, &clearedState, &overrides)) {
      DeleteTree(stage);
      return false;
    }
    sourceStates.push_back(clearedState);
    overrides[relative.generic_wstring() + L"/commit.hgc"] = {
        stagedBytes, destinationAttributes | FILE_ATTRIBUTE_READONLY};
    Sha256 markerState{};
    if (!CanonicalInventory(root, &markerState, &overrides)) {
      DeleteTree(stage);
      return false;
    }
    sourceStates.push_back(markerState);
  }
  Bytes intent{'H', 'G', 'X', '2'};
  U16(&intent, 2); U16(&intent, 1);
  U32(&intent, static_cast<std::uint32_t>(legacy.committed.size()));
  U32(&intent, static_cast<std::uint32_t>(sourceStates.size()));
  intent.insert(intent.end(), epoch.begin(), epoch.end());
  AddDigest(&intent, rootBinding);
  for (const Sha256 &sourceState : sourceStates) AddDigest(&intent, sourceState);
  AddDigest(&intent, stageInventory);
  AddDigest(&intent, legacy.inventory);
  AddDigest(&intent, codec::Hash(codec::View(intent)));
  if (!WriteDurableFile(intentPath, intent)) { DeleteTree(stage); return false; }
  if (failure == FailurePoint::MigrationAfterIntent) return false;

  wchar_t destination[32768]{}, source[32768]{};
  Sha256 actual{};
  if (!Join(root.c_str(), L"reservations.hgj", destination) ||
      !ReplaceDurable(stagedJournal, destination, failure)) return false;
  if (!CanonicalInventory(root, &actual) ||
      !codec::Equal(actual, sourceStates[1])) return false;
  if (failure == FailurePoint::MigrationAfterJournal) return false;
  if (!Join(root.c_str(), L"high-water.hgr", destination) ||
      !ReplaceDurable(stagedCheckpoint, destination, failure)) return false;
  if (!CanonicalInventory(root, &actual) ||
      !codec::Equal(actual, sourceStates[2])) return false;
  if (failure == FailurePoint::MigrationAfterCheckpoint) return false;
  for (std::size_t index = 0; index != legacy.committed.size(); ++index) {
    wchar_t name[96]{};
    if (FAILED(StringCchPrintfW(name, 96, L"commit-%zu.hgc", index)) ||
        !Join(stage, name, source) ||
        !Join(legacy.committed[index].c_str(), L"commit.hgc", destination) ||
        !ReplaceDurable(source, destination, failure, true))
      return false;
  }
  if (!CanonicalInventory(root, &actual) ||
      !codec::Equal(actual, sourceStates.back()) ||
      !RepairMigratedMarkers(root, stage, legacy.committed.size(), epoch))
    return false;
  if (failure == FailurePoint::MigrationAfterMarkers) return false;
  wchar_t stagedIdentity[32768]{};
  if (!Join(stage, L"store-identity.hgs", stagedIdentity) ||
      !Join(root.c_str(), L"store-identity.hgs", destination) ||
      !ReplaceDurable(stagedIdentity, destination, failure)) return false;
  DeleteFileW(intentPath);
  DeleteTree(stage);
  return true;
}

bool ValidIntentChecksum(const Bytes &intent) noexcept {
  if (intent.size() < 256 || intent.size() % 32 != 0) return false;
  const std::size_t checksumOffset = intent.size() - 32;
  const Sha256 checksum = codec::Hash({intent.data(), checksumOffset});
  return std::equal(checksum.bytes.begin(), checksum.bytes.end(),
                    intent.begin() + checksumOffset);
}

bool ResumeLegacyMigration(const std::wstring &root) noexcept {
  wchar_t stage[32768]{}, intentPath[32768]{}, identityPath[32768]{};
  if (!Join(root.c_str(), L"migration-stage", stage) ||
      !Join(root.c_str(), L"migration.intent", intentPath) ||
      !Join(stage, L"store-identity.hgs", identityPath)) return false;
  Bytes intent;
  StoreEpoch epoch{}, stagedEpoch{};
  Sha256 rootBinding{}, sourceInventory{}, stageInventory{};
  if (!ReadWholeFile(intentPath, &intent) || intent.size() < 256 ||
      std::memcmp(intent.data(), "HGX2", 4) != 0 ||
      R16(intent.data() + 4) != 2 || R16(intent.data() + 6) != 1 ||
      R32(intent.data() + 12) != 3 + 2 * R32(intent.data() + 8) ||
      intent.size() != 160 + 32ULL * R32(intent.data() + 12) ||
      !ValidIntentChecksum(intent) ||
      !CanonicalRootBinding(root, &rootBinding) ||
      !CanonicalInventory(root, &sourceInventory) ||
      !CanonicalInventory(stage, &stageInventory) ||
      !std::equal(rootBinding.bytes.begin(), rootBinding.bytes.end(),
                  intent.begin() + 32) ||
      !std::equal(stageInventory.bytes.begin(), stageInventory.bytes.end(),
                  intent.begin() + 64 + 32ULL * R32(intent.data() + 12)) ||
      ReadStoreIdentity(stage, &stagedEpoch) != IdentityRead::Valid ||
      !std::equal(stagedEpoch.begin(), stagedEpoch.end(), intent.begin() + 16))
    return false;
  epoch = stagedEpoch;
  const std::size_t stateCount = R32(intent.data() + 12);
  std::vector<Sha256> states(stateCount);
  for (std::size_t index = 0; index != states.size(); ++index)
    states[index] = ReadDigest(intent.data() + 64 + index * 32);
  std::size_t currentStage = states.size();
  for (std::size_t index = 0; index != states.size(); ++index)
    if (codec::Equal(sourceInventory, states[index])) {
      currentStage = index;
      break;
    }
  if (currentStage == states.size()) return false;

  wchar_t source[32768]{}, destination[32768]{};
  const auto verifyState = [&](std::size_t expected) noexcept {
    Sha256 actual{};
    return CanonicalInventory(root, &actual) &&
        codec::Equal(actual, states[expected]);
  };
  if (currentStage < 1) {
    if (!Join(stage, L"reservations.hgj", source) ||
        !Join(root.c_str(), L"reservations.hgj", destination) ||
        !ReplaceDurable(source, destination, FailurePoint::None) ||
        !verifyState(1)) return false;
    currentStage = 1;
  }
  if (currentStage < 2) {
    if (!Join(stage, L"high-water.hgr", source) ||
        !Join(root.c_str(), L"high-water.hgr", destination) ||
        !ReplaceDurable(source, destination, FailurePoint::None) ||
        !verifyState(2)) return false;
    currentStage = 2;
  }
  const std::uint32_t commitCount = R32(intent.data() + 8);
  for (std::uint32_t index = 0; index != commitCount; ++index) {
    const std::size_t priorState = index == 0 ? 2 : 4 + 2 * (index - 1);
    const std::size_t clearedState = 3 + 2 * index;
    const std::size_t finalState = 4 + 2 * index;
    if (currentStage >= finalState) continue;
    wchar_t name[96]{};
    Bytes commit;
    if (FAILED(StringCchPrintfW(name, 96, L"commit-%u.hgc", index)) ||
        !Join(stage, name, source) || !ReadWholeFile(source, &commit) ||
        commit.size() != kCommitBytes ||
        std::memcmp(commit.data(), "HGC2", 4) != 0 ||
        !std::equal(epoch.begin(), epoch.end(), commit.begin() + 16) ||
        FAILED(StringCchPrintfW(
            name, 96, L"generation-%020llu\\commit.hgc",
            static_cast<unsigned long long>(R64(commit.data() + 8)))) ||
        !Join(root.c_str(), name, destination))
      return false;
    if (currentStage == clearedState) {
      const DWORD attributes = GetFileAttributesW(destination);
      if (attributes == INVALID_FILE_ATTRIBUTES ||
          (attributes & (FILE_ATTRIBUTE_DIRECTORY |
                         FILE_ATTRIBUTE_REPARSE_POINT)) != 0 ||
          !SetFileAttributesW(destination,
                              attributes | FILE_ATTRIBUTE_READONLY) ||
          !verifyState(priorState))
        return false;
      currentStage = priorState;
    }
    if (currentStage != priorState ||
        !ReplaceDurable(source, destination, FailurePoint::None, true) ||
        !verifyState(finalState))
      return false;
    currentStage = finalState;
  }
  if (currentStage != states.size() - 1) return false;
  if (!RepairMigratedMarkers(root, stage, R32(intent.data() + 8), epoch))
    return false;
  // Identity is the only authority switch not represented in sourceStates.
  // It is committed only after every receipted source transition verifies.
  if (!Join(root.c_str(), L"store-identity.hgs", destination) ||
      !ReplaceDurable(identityPath, destination, FailurePoint::None))
    return false;
  DeleteFileW(intentPath);
  DeleteTree(stage);
  return ReadStoreIdentity(root, &epoch) == IdentityRead::Valid && epoch == stagedEpoch;
}

bool RootHasPersistentEntries(const std::wstring &root) noexcept {
  wchar_t pattern[32768]{};
  if (!Join(root.c_str(), L"*", pattern)) return true;
  WIN32_FIND_DATAW data{};
  HANDLE find = FindFirstFileW(pattern, &data);
  if (find == INVALID_HANDLE_VALUE) return false;
  bool found = false;
  do {
    if (std::wcscmp(data.cFileName, L"high-water.hgr") == 0 ||
        std::wcscmp(data.cFileName, L"reservations.hgj") == 0 ||
        std::wcscmp(data.cFileName, L"store-identity.hgs") == 0 ||
        std::wcscmp(data.cFileName, L"migration.intent") == 0 ||
        std::wcscmp(data.cFileName, L"migration-stage") == 0 ||
        std::wcsncmp(data.cFileName, L"generation-", 11) == 0 ||
        std::wcsncmp(data.cFileName, L"candidate-", 10) == 0) {
      found = true;
      break;
    }
  } while (FindNextFileW(find, &data));
  FindClose(find);
  return found;
}

void CleanupReservationTemps(const std::wstring &root) noexcept {
  wchar_t pattern[32768]{};
  if (!Join(root.c_str(), L"high-water-*.tmp", pattern)) return;
  WIN32_FIND_DATAW data{};
  HANDLE find = FindFirstFileW(pattern, &data);
  if (find == INVALID_HANDLE_VALUE) return;
  do {
    wchar_t path[32768]{};
    if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0 &&
        Join(root.c_str(), data.cFileName, path))
      DeleteFileW(path);
  } while (FindNextFileW(find, &data));
  FindClose(find);
}

bool ManifestVersionMatches(const FileSlice &records,
                            const TraversalManifest &traversal) noexcept {
  std::array<std::uint8_t, 273> bytes{};
  SourceReader reader{records.file, records.offset};
  std::uint32_t actual = 0;
  if (!ReadSource(&reader, 0, bytes.data(),
                  static_cast<std::uint32_t>(bytes.size()), &actual) ||
      actual != bytes.size() || R16(bytes.data()) != 0 ||
      R16(bytes.data() + 24) != 1 || R16(bytes.data() + 28) != 11 ||
      R64(bytes.data() + 40) != kGraphVersionBytesV1)
    return false;
  const std::uint8_t *version = bytes.data() + 48;
  return R16(version) == codec::kCodecVersion &&
         R64(version + 2) == traversal.closedProfileBits &&
         version[10] == (traversal.semanticCertified ? 1U : 0U) &&
         version[11] == (traversal.layoutPresent ? 1U : 0U) &&
         version[12] == 0 && version[13] == 0 && version[14] == 0 &&
         version[15] == 0 &&
         bytes[240] == static_cast<std::uint8_t>(traversal.integrity) &&
         R64(bytes.data() + 265) == traversal.closedProfileBits &&
         std::equal(version + 72, version + 104,
                    traversal.observedSemanticRoot.bytes.begin()) &&
         std::equal(version + 104, version + 136,
                    traversal.layoutRoot.bytes.begin()) &&
         std::equal(version + 136, version + 168,
                    traversal.captureRoot.bytes.begin());
}
void TryDeleteGeneration(const wchar_t *path) noexcept {
  // A live Generation opens the directory without FILE_SHARE_DELETE. Taking
  // DELETE access first is therefore both the lease probe and the exclusion
  // that prevents a new lease from racing cleanup.
  HANDLE deletion = CreateFileW(
      path, DELETE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
      nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
  if (deletion == INVALID_HANDLE_VALUE) return;
  constexpr std::array<const wchar_t *, 6> children{
      L"records.hgn", L"index.hgi", L"environment.hge", L"blobs.hgb",
      L"manifest.hgm", L"commit.hgc"};
  bool empty = true;
  for (const wchar_t *name : children) {
    wchar_t child[32768]{};
    if (!Join(path, name, child)) {
      empty = false;
      break;
    }
    const DWORD attributes = GetFileAttributesW(child);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
      if (GetLastError() != ERROR_FILE_NOT_FOUND) empty = false;
      continue;
    }
    SetFileAttributesW(child, FILE_ATTRIBUTE_NORMAL);
    if (!DeleteFileW(child) && GetLastError() != ERROR_FILE_NOT_FOUND)
      empty = false;
  }
  FILE_DISPOSITION_INFO disposition{TRUE};
  if (empty)
    SetFileInformationByHandle(deletion, FileDispositionInfo, &disposition,
                               sizeof(disposition));
  CloseHandle(deletion);
}

void CleanupRetiredGenerations(const std::wstring &root,
                               const StoreEpoch &epoch,
                               const std::uint64_t trustedCurrentSerial = 0) noexcept {
  wchar_t pattern[32768]{};
  if (!Join(root.c_str(), L"generation-*", pattern)) return;
  WIN32_FIND_DATAW data{};
  HANDLE find = FindFirstFileW(pattern, &data);
  if (find == INVALID_HANDLE_VALUE) return;
  std::vector<std::pair<std::uint64_t, std::wstring>> committed;
  bool certain = true;
  do {
    if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0) continue;
    std::uint64_t serial = 0, records = 0, index = 0;
    wchar_t path[32768]{};
    if (!ParseSerial(data.cFileName, &serial) ||
        !Join(root.c_str(), data.cFileName, path)) {
      certain = false;
      break;
    }
    if (trustedCurrentSerial != 0 && serial == trustedCurrentSerial) {
      // Prepared promotion still holds the exact immutable record/blob file
      // identities and the commit bytes were flushed immediately before this
      // scan. Re-authenticating the just-published generation here would only
      // duplicate a full-file hash before retirement can inspect older roots.
      committed.emplace_back(serial, path);
      continue;
    }
    if ((trustedCurrentSerial != 0 && serial > trustedCurrentSerial) ||
        !ValidateGeneration(path, serial, &records, &index) ||
        !ValidateCommitMarker(path, serial, epoch)) {
      certain = false;
      break;
    }
    committed.emplace_back(serial, path);
  } while (FindNextFileW(find, &data));
  if (certain && GetLastError() != ERROR_NO_MORE_FILES) certain = false;
  FindClose(find);
  if (!certain || committed.empty()) return;
  const auto current = std::max_element(
      committed.begin(), committed.end(),
      [](const auto &left, const auto &right) { return left.first < right.first; });
  for (const auto &generation : committed)
    if (generation.first < current->first)
      TryDeleteGeneration(generation.second.c_str());
}

bool CleanupCandidatePattern(const std::wstring &root,
                             const wchar_t* const namePattern,
                             FailurePoint failure) noexcept {
  wchar_t pattern[32768]{};
  if (!Join(root.c_str(), namePattern, pattern))
    return false;
  WIN32_FIND_DATAW data{};
  HANDLE find = FindFirstFileW(pattern, &data);
  if (find == INVALID_HANDLE_VALUE)
    return GetLastError() == ERROR_FILE_NOT_FOUND;
  if (failure == FailurePoint::StartupCleanup) {
    FindClose(find);
    return false;
  }
  bool ok = true;
  do {
    if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0)
      continue;
    wchar_t candidate[32768]{}, ownerPath[32768]{};
    if (!Join(root.c_str(), data.cFileName, candidate) ||
        !Join(candidate, L"owner.lock", ownerPath)) {
      ok = false;
      continue;
    }
    HANDLE owner =
        CreateFileW(ownerPath, GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                    OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (owner == INVALID_HANDLE_VALUE &&
        GetLastError() == ERROR_SHARING_VIOLATION)
      continue;
    if (owner != INVALID_HANDLE_VALUE)
      CloseHandle(owner);
    if (!DeleteTree(candidate))
      ok = false;
  } while (FindNextFileW(find, &data));
  FindClose(find);
  return ok;
}
bool CleanupCandidates(const std::wstring &root,
                       FailurePoint failure) noexcept {
  return CleanupCandidatePattern(root, L"candidate-*", failure) &&
      CleanupCandidatePattern(root, L"prepared-*", failure);
}
} // namespace

PreparedGenerationCandidate::~PreparedGenerationCandidate() noexcept {
  if (records.file != INVALID_HANDLE_VALUE) CloseHandle(records.file);
  if (index.file != INVALID_HANDLE_VALUE) CloseHandle(index.file);
  if (environment.file != INVALID_HANDLE_VALUE) CloseHandle(environment.file);
  if (blobs.file != INVALID_HANDLE_VALUE) CloseHandle(blobs.file);
  if (owner != INVALID_HANDLE_VALUE) CloseHandle(owner);
}

Generation::Generation(
    const wchar_t *path, std::uint64_t records, std::uint64_t index,
    std::uint64_t serial,
    const std::array<std::uint8_t, 16>& storeEpoch) noexcept
    : recordsBytes_(records), indexBytes_(index), serial_(serial),
      storeEpoch_(storeEpoch) {
  if (SUCCEEDED(StringCchCopyW(path_.data(), path_.size(), path)))
    // Host validation shows FILE_READ_ATTRIBUTES alone does not participate
    // in directory delete sharing on Windows; FILE_LIST_DIRECTORY does.
    lease_ = CreateFileW(path, FILE_LIST_DIRECTORY,
                         FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                         OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
}
Generation::~Generation() noexcept {
  if (lease_ != INVALID_HANDLE_VALUE) CloseHandle(lease_);
}

AuthenticatedGenerationFiles::~AuthenticatedGenerationFiles() noexcept {
  if (manifest != INVALID_HANDLE_VALUE) CloseHandle(manifest);
  if (records != INVALID_HANDLE_VALUE) CloseHandle(records);
  if (blobs != INVALID_HANDLE_VALUE) CloseHandle(blobs);
}

namespace {
bool AuthenticateGenerationImpl(
    const GenerationPin &generation,
    AuthenticatedGenerationPin *const authenticated,
    const bool replayCanonical) noexcept {
  if (generation == nullptr || authenticated == nullptr ||
      generation->Serial() == 0) {
    return false;
  }
  *authenticated = {};
  try {
    auto files = std::make_shared<AuthenticatedGenerationFiles>();
    std::uint64_t recordsLength = 0;
    std::uint64_t indexLength = 0;
    const std::wstring path = generation->Path();
    if (!ValidateGeneration(path.c_str(), generation->Serial(),
                            &recordsLength, &indexLength, files.get(),
                            replayCanonical) ||
        !ValidateCommitMarker(path.c_str(), generation->Serial(),
                              generation->StoreEpoch())) {
      return false;
    }
    *authenticated = std::move(files);
    return true;
  } catch (...) {
    return false;
  }
}
} // namespace

bool AuthenticateGeneration(
    const GenerationPin &generation,
    AuthenticatedGenerationPin *const authenticated) noexcept {
  gGenerationAuthenticationCount.fetch_add(1, std::memory_order_relaxed);
  return AuthenticateGenerationImpl(generation, authenticated, true);
}

std::wstring Generation::Path() const { return path_.data(); }
GenerationQueryIndexPin Generation::QueryIndex() const noexcept {
  AcquireSRWLockShared(&queryIndexLock_);
  GenerationQueryIndexPin value = queryIndex_;
  ReleaseSRWLockShared(&queryIndexLock_);
  return value;
}
void Generation::InstallQueryIndex(
    const GenerationQueryIndexPin &index) const noexcept {
  AcquireSRWLockExclusive(&queryIndexLock_);
  if (queryIndex_ == nullptr) queryIndex_ = index;
  ReleaseSRWLockExclusive(&queryIndexLock_);
}

GraphStore::GraphStore(std::wstring root) noexcept : root_(std::move(root)) {}
GraphStore::~GraphStore() noexcept {
  AcquireSRWLockExclusive(&lock_);
  active_.reset();
  ReleaseSRWLockExclusive(&lock_);
  if (rootDirectory_ != INVALID_HANDLE_VALUE)
    CloseHandle(rootDirectory_);
}

bool TraversalsMatch(const TraversalManifest &a,
                     const TraversalManifest &b) noexcept {
  return codec::Equal(a.observedSemanticRoot, b.observedSemanticRoot) &&
         codec::Equal(a.layoutRoot, b.layoutRoot) &&
         codec::Equal(a.captureRoot, b.captureRoot) &&
         a.semanticCertified == b.semanticCertified &&
         a.layoutPresent == b.layoutPresent &&
         a.closedProfileBits == b.closedProfileBits &&
         a.integrity == b.integrity && EqualStream(a.coverage, b.coverage) &&
         EqualStream(a.unavailable, b.unavailable) &&
         EqualStream(a.diagnostics, b.diagnostics);
}
bool CapturedStatesMatch(const CapturedState &a,
                         const CapturedState &b) noexcept {
  return EqualStream(a.route, b.route) && EqualStream(a.cursor, b.cursor) &&
         EqualStream(a.selection, b.selection) && a.modified == b.modified;
}

bool ResolveStoreAuthorityIdentity(const std::wstring &root,
                                   Sha256 *identity) noexcept {
  StoreRootAuthority authority;
  if (identity == nullptr || !ResolveStoreRoot(root, false, &authority))
    return false;
  *identity = authority.identity;
  return true;
}

bool HashFileRangeChunked(HANDLE file, std::uint64_t offset,
                          std::uint64_t length, Sha256 *digest) noexcept {
  if (file == INVALID_HANDLE_VALUE || digest == nullptr || !Seek(file, offset))
    return false;
  HashContext hash;
  PUCHAR buffer = static_cast<PUCHAR>(HeapAlloc(GetProcessHeap(), 0, kIoBytes));
  bool ok = buffer != nullptr && hash.Open();
  std::uint64_t done = 0;
  while (ok && done != length) {
    const DWORD wanted =
        static_cast<DWORD>(std::min<std::uint64_t>(length - done, kIoBytes));
    DWORD read = 0;
    ok = ReadFile(file, buffer, wanted, &read, nullptr) != FALSE &&
         read == wanted && hash.Add(buffer, read);
    done += read;
  }
  ok = ok && hash.Finish(digest);
  if (buffer != nullptr)
    HeapFree(GetProcessHeap(), 0, buffer);
  if (!ok)
    *digest = {};
  return ok;
}

bool CopyFileSliceChunked(const FileSlice &source, HANDLE destination,
                          Sha256 *digest) noexcept {
  if (source.file == INVALID_HANDLE_VALUE ||
      destination == INVALID_HANDLE_VALUE || digest == nullptr ||
      !Seek(source.file, source.offset))
    return false;
  HashContext hash;
  PUCHAR buffer = static_cast<PUCHAR>(HeapAlloc(GetProcessHeap(), 0, kIoBytes));
  bool ok = buffer != nullptr && hash.Open();
  std::uint64_t done = 0;
  while (ok && done != source.length) {
    const DWORD wanted = static_cast<DWORD>(
        std::min<std::uint64_t>(source.length - done, kIoBytes));
    DWORD read = 0, written = 0;
    ok = ReadFile(source.file, buffer, wanted, &read, nullptr) != FALSE &&
         read == wanted && hash.Add(buffer, read) &&
         WriteFile(destination, buffer, read, &written, nullptr) != FALSE &&
         written == read;
    done += read;
  }
  ok = ok && hash.Finish(digest);
  if (buffer != nullptr)
    HeapFree(GetProcessHeap(), 0, buffer);
  if (!ok)
    *digest = {};
  return ok;
}

bool GraphStore::Initialize(FailurePoint failure) noexcept {
  try {
    StoreRootAuthority rootAuthority;
    if (!ResolveStoreRoot(root_, true, &rootAuthority)) return false;
    root_ = std::move(rootAuthority.canonicalPath);
    authorityIdentity_ = rootAuthority.identity;
    if (rootDirectory_ != INVALID_HANDLE_VALUE) CloseHandle(rootDirectory_);
    rootDirectory_ = rootAuthority.ReleaseDirectory();
    authorityResolved_ = true;
    AllocatorLock allocator;
    if (!AcquireAllocatorLock(authorityIdentity_, &allocator)) return false;
    wchar_t intent[32768]{};
    if (!Join(root_.c_str(), L"migration.intent", intent)) return false;
    const DWORD intentAttributes = GetFileAttributesW(intent);
    if (intentAttributes != INVALID_FILE_ATTRIBUTES) {
      if ((intentAttributes & (FILE_ATTRIBUTE_DIRECTORY |
                               FILE_ATTRIBUTE_REPARSE_POINT)) != 0 ||
          !ResumeLegacyMigration(root_))
        return false;
    }
    IdentityRead identityRead = ReadStoreIdentity(root_, &storeEpoch_);
    if (identityRead == IdentityRead::Invalid) return false;
    if (identityRead == IdentityRead::Missing) {
      if (!RootHasPersistentEntries(root_)) {
        if (!CreateStoreIdentity(root_, &storeEpoch_, failure)) return false;
      } else {
        LegacyState legacy;
        if (!InspectLegacyRoot(root_, &legacy)) {
          // Invalid roots have no authority and are cleaned only when they do
          // not claim a legacy checkpoint/journal lineage. Claimed but
          // incoherent legacy roots are immutable fail-closed evidence.
          wchar_t checkpoint[32768]{}, journal[32768]{};
          if (!Join(root_.c_str(), L"high-water.hgr", checkpoint) ||
              !Join(root_.c_str(), L"reservations.hgj", journal) ||
              GetFileAttributesW(checkpoint) != INVALID_FILE_ATTRIBUTES ||
              GetFileAttributesW(journal) != INVALID_FILE_ATTRIBUTES)
            return false;
          wchar_t pattern[32768]{};
          if (!Join(root_.c_str(), L"generation-*", pattern)) return false;
          WIN32_FIND_DATAW data{};
          HANDLE find = FindFirstFileW(pattern, &data);
          if (find != INVALID_HANDLE_VALUE) {
            do {
              if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
                wchar_t path[32768]{};
                if (Join(root_.c_str(), data.cFileName, path)) DeleteTree(path);
              }
            } while (FindNextFileW(find, &data));
            FindClose(find);
          }
          if (!CreateStoreIdentity(root_, &storeEpoch_, failure)) return false;
        } else if (!CompleteLegacyMigration(root_, legacy, failure)) {
          return false;
        }
      }
      identityRead = ReadStoreIdentity(root_, &storeEpoch_);
      if (identityRead != IdentityRead::Valid) return false;
    }
    ReservationState authority{};
    if (!ReconcileReservation(root_, storeEpoch_, &authority, failure))
      return false;
    const std::uint64_t durableSerial = authority.serial;

    wchar_t pattern[32768]{};
    if (!Join(root_.c_str(), L"generation-*", pattern)) return false;
    WIN32_FIND_DATAW data{};
    HANDLE find = FindFirstFileW(pattern, &data);
    std::array<wchar_t, 32768> bestPath{};
    std::uint64_t bestSerial = 0, bestRecords = 0, bestIndex = 0;
    std::uint64_t observedSerial = durableSerial;
    std::vector<std::wstring> cleanup;
    if (find != INVALID_HANDLE_VALUE) {
      do {
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0) continue;
        std::uint64_t serial = 0;
        wchar_t path[32768]{};
        if (!Join(root_.c_str(), data.cFileName, path)) continue;
        if (!ParseSerial(data.cFileName, &serial)) {
          cleanup.emplace_back(path);
          continue;
        }
        std::uint64_t records = 0, index = 0;
        if (!ValidateGeneration(path, serial, &records, &index)) {
          cleanup.emplace_back(path);
          continue;
        }
        // Migration/recovery trusts validated generation contents, never the
        // filename alone. Persist this observation before residue deletion.
        observedSerial = (std::max)(observedSerial, serial);
        if (!ValidateCommitMarker(path, serial, storeEpoch_)) {
          cleanup.emplace_back(path);
          continue;
        }
        if (serial > bestSerial) {
          bestSerial = serial;
          bestRecords = records;
          bestIndex = index;
          StringCchCopyW(bestPath.data(), bestPath.size(), path);
        }
      } while (FindNextFileW(find, &data));
      FindClose(find);
    }
    if (observedSerial > durableSerial &&
        !PersistReservation(root_, storeEpoch_, observedSerial, failure))
      return false;
    CleanupReservationTemps(root_);
    if (!CleanupCandidates(root_, failure)) return false;

    std::shared_ptr<Generation> loaded;
    if (bestSerial != 0) {
      loaded.reset(new (std::nothrow) Generation(
          bestPath.data(), bestRecords, bestIndex, bestSerial, storeEpoch_));
      if (!loaded || !loaded->HasLease()) return false;
      AuthenticatedGenerationPin authenticated;
      gGenerationAuthenticationCount.fetch_add(1, std::memory_order_relaxed);
      if (!AuthenticateGenerationImpl(loaded, &authenticated, false))
        return false;
      loaded->authenticatedFiles_ = std::move(authenticated);
    }
    for (const std::wstring &path : cleanup)
      TryDeleteGeneration(path.c_str());
    CleanupRetiredGenerations(root_, storeEpoch_);
    AcquireSRWLockExclusive(&lock_);
    active_ = std::move(loaded);
    serial_ = bestSerial;
    reservationSerial_ = observedSerial;
    ReleaseSRWLockExclusive(&lock_);
    return true;
  } catch (...) {
    return false;
  }
}

void ResetGenerationAuthenticationDebugCount() noexcept {
  gGenerationAuthenticationCount.store(0, std::memory_order_relaxed);
}

std::uint64_t ReadGenerationAuthenticationDebugCount() noexcept {
  return gGenerationAuthenticationCount.load(std::memory_order_relaxed);
}
void ResetPublicationDebugCounters() noexcept {
  gPreparedPublicationStage.store(0, std::memory_order_relaxed);
  gPreparedPromotions.store(0, std::memory_order_relaxed);
  gPreparedManifestWrites.store(0, std::memory_order_relaxed);
  gPreparedManifestFlushes.store(0, std::memory_order_relaxed);
  gPreparedCommitWrites.store(0, std::memory_order_relaxed);
  gPreparedCommitFlushes.store(0, std::memory_order_relaxed);
}
PublicationDebugCounters ReadPublicationDebugCounters() noexcept {
  return {gPreparedPromotions.load(std::memory_order_relaxed),
          gPreparedManifestWrites.load(std::memory_order_relaxed),
          gPreparedManifestFlushes.load(std::memory_order_relaxed),
          gPreparedCommitWrites.load(std::memory_order_relaxed),
          gPreparedCommitFlushes.load(std::memory_order_relaxed),
          0, 0, 0,
          gPreparedPublicationStage.load(std::memory_order_relaxed)};
}

GenerationPin GraphStore::PinActive() const noexcept {
  AcquireSRWLockShared(&lock_);
  GenerationPin pin = active_;
  ReleaseSRWLockShared(&lock_);
  return pin;
}
std::uint64_t GraphStore::ActiveSerial() const noexcept {
  AcquireSRWLockShared(&lock_);
  const std::uint64_t serial = serial_;
  ReleaseSRWLockShared(&lock_);
  return serial;
}

bool GraphStore::Publish(const PublicationInput &input,
                         FailurePoint failure) noexcept {
  if (input.first.integrity != CaptureIntegrity::Complete ||
      input.second.integrity != CaptureIntegrity::Complete ||
      (input.first.layoutPresent && !input.first.semanticCertified) ||
      (input.second.layoutPresent && !input.second.semanticCertified) ||
      !TraversalsMatch(input.first, input.second) ||
      !CapturedStatesMatch(input.baseline, input.firstRestoration) ||
      !CapturedStatesMatch(input.baseline, input.secondRestoration) ||
      input.records.file == INVALID_HANDLE_VALUE ||
      input.index.file == INVALID_HANDLE_VALUE ||
      !ManifestVersionMatches(input.records, input.first))
    return false;
  SourceReader source{input.records.file, input.records.offset};
  const codec::RecordStream stream{&source, ReadSource, input.records.length,
                                   StreamKind::Capture};
  GenerationPin activeLineage = PinActive();
  const codec::CanonicalArtifactPin& artifact = input.canonicalArtifact;
  const codec::CanonicalizationResult *canonicalFacts =
      codec::CanonicalArtifactFacts(artifact);
  if (canonicalFacts == nullptr ||
      (canonicalFacts->requiresActiveLineage &&
       !ValidateActiveLineage(input.records, activeLineage)))
    return false;
  Sha256 sourceIndexDigest{};
  const PreparedGenerationPin& prepared = input.preparedCandidate;
  if (prepared != nullptr)
    sourceIndexDigest = prepared->index.digest;
  if (canonicalFacts->recordStream.byteLength != stream.length ||
      !canonicalFacts->rootsPresent ||
      canonicalFacts->streamKind != StreamKind::Capture ||
      (prepared == nullptr &&
       !HashFileRangeChunked(input.index.file, input.index.offset,
                             input.index.length, &sourceIndexDigest)) ||
      !codec::Equal(canonicalFacts->viewIndexDigest, sourceIndexDigest) ||
      !codec::Equal(canonicalFacts->observedSemanticRoot,
                    input.first.observedSemanticRoot) ||
      canonicalFacts->semanticCertified != input.first.semanticCertified ||
      canonicalFacts->layoutPresent != input.first.layoutPresent ||
      canonicalFacts->closedProfileBits != input.first.closedProfileBits ||
      canonicalFacts->integrity != input.first.integrity ||
      !codec::Equal(canonicalFacts->layoutRoot, input.first.layoutRoot) ||
      !codec::Equal(canonicalFacts->captureRoot, input.first.captureRoot) ||
      canonicalFacts->coverage.byteLength != input.first.coverage.length ||
      !codec::Equal(canonicalFacts->coverage.digest, input.first.coverage.digest) ||
      canonicalFacts->unavailable.byteLength != input.first.unavailable.length ||
      !codec::Equal(canonicalFacts->unavailable.digest,
                    input.first.unavailable.digest) ||
      canonicalFacts->diagnostics.byteLength != input.first.diagnostics.length ||
      !codec::Equal(canonicalFacts->diagnostics.digest,
                    input.first.diagnostics.digest))
    return false;
  const codec::CanonicalizationResult &canonical = *canonicalFacts;
  if (prepared != nullptr) {
    const Sha256 environmentDigest = codec::Hash(input.layoutEnvironment);
    if (codec::CanonicalArtifactSourceContext(artifact) != input.records.file ||
        !PreparedFileMatches(prepared->records, input.records.file,
                             input.records.length,
                             canonical.recordStream.digest) ||
        !PreparedFileMatches(prepared->index, input.index.file,
                             input.index.length, sourceIndexDigest) ||
        !PreparedFileMatches(prepared->environment,
                             prepared->environment.file,
                             input.layoutEnvironment.size,
                             environmentDigest) ||
        prepared->owner == INVALID_HANDLE_VALUE ||
        prepared->directory.empty())
      return false;
  }
  try {
    AllocatorLock allocator;
    if (!authorityResolved_ ||
        !AcquireAllocatorLock(authorityIdentity_, &allocator))
      return false;
    StoreEpoch epoch{};
    if (ReadStoreIdentity(root_, &epoch) != IdentityRead::Valid ||
        epoch != storeEpoch_) return false;
    ReservationState authority{};
    if (!ReconcileReservation(root_, epoch, &authority, failure) ||
        authority.serial == (std::numeric_limits<std::uint64_t>::max)())
      return false;
    const std::uint64_t next = authority.serial + 1;
    // Generation serials are durable store allocation identities. Reserve N
    // before candidate mutation; every later failure consumes N permanently.
    if (!PersistReservation(root_, epoch, next, failure)) return false;
    AcquireSRWLockExclusive(&lock_);
    reservationSerial_ = next;
    ReleaseSRWLockExclusive(&lock_);

    if (prepared != nullptr) {
      gPreparedPublicationStage.store(1, std::memory_order_relaxed);
      std::filesystem::path preparedPath(prepared->directory);
      const std::filesystem::path expectedParent(root_);
      std::uint64_t expectedBlobBytes = 0;
      for (const codec::CanonicalBlobSlice& slice : canonical.blobClosure) {
        const std::uint64_t chunks = slice.length == 0 ? 0 :
            (slice.length + kIoBytes - 1) / kIoBytes;
        if (slice.length > (std::numeric_limits<std::uint64_t>::max)() -
                expectedBlobBytes ||
            chunks > ((std::numeric_limits<std::uint64_t>::max)() -
                expectedBlobBytes - slice.length) / 28) {
          return false;
        }
        expectedBlobBytes += slice.length + chunks * 28;
      }
      std::uint64_t rootVolume = 0;
      std::array<std::uint8_t, 16> rootId{};
      std::uint64_t rejected = 0;
      std::error_code preparedPathError;
      if (!std::filesystem::equivalent(preparedPath.parent_path(),
                                       expectedParent, preparedPathError) ||
          preparedPathError)
        rejected |= 1;
      if (preparedPath.filename().wstring().rfind(L"prepared-", 0) != 0)
        rejected |= 2;
      if (!ReadFileIdentity(rootDirectory_, &rootVolume, &rootId)) rejected |= 4;
      if (prepared->records.volumeSerial != rootVolume) rejected |= 8;
      if (prepared->index.volumeSerial != rootVolume) rejected |= 16;
      if (prepared->environment.volumeSerial != rootVolume) rejected |= 32;
      if (prepared->blobs.volumeSerial != rootVolume) rejected |= 64;
      if (!PreparedFileMatches(prepared->blobs, prepared->blobs.file,
                               expectedBlobBytes, prepared->blobs.digest))
        rejected |= 128;
      if (failure == FailurePoint::PreparedArtifactMismatch) rejected |= 256;
      if (failure == FailurePoint::PreparedFileIdentityMismatch) rejected |= 512;
      if (failure == FailurePoint::PreparedManifestPatchMismatch) rejected |= 1024;
      if (rejected != 0) {
        gPreparedPublicationStage.store(100 + rejected,
                                        std::memory_order_relaxed);
        return false;
      }
      gPreparedPublicationStage.store(2, std::memory_order_relaxed);
      wchar_t generationName[96]{}, promotionName[96]{};
      PathBuffer generation = NewPathBuffer();
      PathBuffer promotion = NewPathBuffer();
      PathBuffer manifestPath = NewPathBuffer();
      PathBuffer recordsPath = NewPathBuffer();
      PathBuffer indexPath = NewPathBuffer();
      PathBuffer environmentPath = NewPathBuffer();
      PathBuffer blobsPath = NewPathBuffer();
      if (!generation || !promotion || !manifestPath || !recordsPath ||
          !indexPath || !environmentPath || !blobsPath ||
          FAILED(StringCchPrintfW(generationName, 96,
                                  L"generation-%020llu",
                                  static_cast<unsigned long long>(next))) ||
          FAILED(StringCchPrintfW(promotionName, 96,
                                  L"candidate-prepared-%lu-%020llu",
                                  GetCurrentProcessId(),
                                  static_cast<unsigned long long>(next))) ||
          !Join(root_.c_str(), generationName, generation.get()) ||
          !Join(root_.c_str(), promotionName, promotion.get()) ||
          !CreateDirectoryW(promotion.get(), nullptr) ||
          !Join(promotion.get(), L"manifest.hgm", manifestPath.get()) ||
          !Join(promotion.get(), L"records.hgn", recordsPath.get()) ||
          !Join(promotion.get(), L"index.hgi", indexPath.get()) ||
          !Join(promotion.get(), L"environment.hge", environmentPath.get()) ||
          !Join(promotion.get(), L"blobs.hgb", blobsPath.get()) ||
          !LinkDurableWholeFile(
              {prepared->records.file, 0, prepared->records.length, true},
              recordsPath.get()) ||
          !LinkDurableWholeFile(
              {prepared->index.file, 0, prepared->index.length, true},
              indexPath.get()) ||
          !LinkDurableWholeFile(
              {prepared->environment.file, 0, prepared->environment.length,
               true}, environmentPath.get()) ||
          !LinkDurableWholeFile(
              {prepared->blobs.file, 0, prepared->blobs.length, true},
              blobsPath.get()))
        return false;
      HANDLE linkedRecords = INVALID_HANDLE_VALUE;
      HANDLE linkedIndex = INVALID_HANDLE_VALUE;
      HANDLE linkedEnvironment = INVALID_HANDLE_VALUE;
      HANDLE linkedBlobs = INVALID_HANDLE_VALUE;
      const bool linksBound =
          DestinationIdentityMatches(recordsPath.get(), prepared->records,
                                     &linkedRecords) &&
          DestinationIdentityMatches(indexPath.get(), prepared->index,
                                     &linkedIndex) &&
          DestinationIdentityMatches(environmentPath.get(),
                                     prepared->environment,
                                     &linkedEnvironment) &&
          DestinationIdentityMatches(blobsPath.get(), prepared->blobs,
                                     &linkedBlobs);
      for (HANDLE file : {linkedRecords, linkedIndex, linkedEnvironment,
                          linkedBlobs})
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
      if (!linksBound) return false;
      const Bytes prefix = ManifestPrefix(
          next, input, prepared->records.digest, prepared->index.digest,
          prepared->blobs.length, prepared->blobs.digest);
      Bytes integrityInput = prefix;
      U64(&integrityInput, kSeal);
      const Sha256 manifestDigest = codec::Hash(codec::View(integrityInput));
      Bytes manifestBytes = prefix;
      manifestBytes.insert(manifestBytes.end(), manifestDigest.bytes.begin(),
                           manifestDigest.bytes.end());
      U64(&manifestBytes, kSeal);
      HANDLE manifest = INVALID_HANDLE_VALUE;
      if (manifestBytes.size() != kManifestBytes ||
          failure == FailurePoint::OpenManifest ||
          (manifest = CreateFileW(
               manifestPath.get(), GENERIC_READ | GENERIC_WRITE,
               FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL,
               nullptr)) == INVALID_HANDLE_VALUE ||
          failure == FailurePoint::WriteManifest ||
          !WriteBytes(manifest, manifestBytes.data(), manifestBytes.size()) ||
          (gPreparedManifestWrites.fetch_add(1, std::memory_order_relaxed),
           false) ||
          failure == FailurePoint::SealManifest ||
          !FlushFileBuffers(manifest) ||
          (gPreparedManifestFlushes.fetch_add(1, std::memory_order_relaxed),
           false)) {
        if (manifest != INVALID_HANDLE_VALUE) CloseHandle(manifest);
        return false;
      }
      CloseHandle(manifest);
      gPreparedPublicationStage.store(3, std::memory_order_relaxed);
      CloseHandle(prepared->owner);
      prepared->owner = INVALID_HANDLE_VALUE;
      PathBuffer ownerPath = NewPathBuffer();
      PathBuffer sourceRecords = NewPathBuffer();
      PathBuffer sourceIndex = NewPathBuffer();
      PathBuffer sourceEnvironment = NewPathBuffer();
      PathBuffer sourceBlobs = NewPathBuffer();
      if (!ownerPath || !sourceRecords || !sourceIndex || !sourceEnvironment ||
          !sourceBlobs ||
          !Join(prepared->directory.c_str(), L"owner.lock", ownerPath.get()) ||
          !Join(prepared->directory.c_str(), L"records.hgn",
                sourceRecords.get()) ||
          !Join(prepared->directory.c_str(), L"index.hgi", sourceIndex.get()) ||
          !Join(prepared->directory.c_str(), L"environment.hge",
                sourceEnvironment.get()) ||
          !Join(prepared->directory.c_str(), L"blobs.hgb", sourceBlobs.get()) ||
          !DeleteFileW(ownerPath.get()) || !DeleteFileW(sourceRecords.get()) ||
          !DeleteFileW(sourceIndex.get()) ||
          !DeleteFileW(sourceEnvironment.get()) ||
          !DeleteFileW(sourceBlobs.get()) ||
          !RemoveDirectoryW(prepared->directory.c_str()))
        return false;
      prepared->directory.clear();
      if (!SetImmutable(recordsPath.get()) || !SetImmutable(indexPath.get()) ||
          !SetImmutable(environmentPath.get()) ||
          !SetImmutable(blobsPath.get()) || !SetImmutable(manifestPath.get()))
        return false;
      gPreparedPublicationStage.store(31, std::memory_order_relaxed);
      if (failure == FailurePoint::Rename) return false;
      gPreparedPublicationStage.store(32, std::memory_order_relaxed);
      if (!MoveFileExW(promotion.get(), generation.get(),
                       MOVEFILE_WRITE_THROUGH)) {
        gPreparedPublicationStage.store(32000 + GetLastError(),
                                        std::memory_order_relaxed);
        return false;
      }
      gPreparedPublicationStage.store(4, std::memory_order_relaxed);
      const auto releasePreparedFiles = [&prepared]() noexcept {
        for (HANDLE* const file : {&prepared->records.file,
                                  &prepared->index.file,
                                  &prepared->environment.file,
                                  &prepared->blobs.file}) {
          if (*file != INVALID_HANDLE_VALUE) CloseHandle(*file);
          *file = INVALID_HANDLE_VALUE;
        }
      };
      if (failure == FailurePoint::PreparedParentBarrier ||
          failure == FailurePoint::Swap ||
          failure == FailurePoint::SwapCleanupFailure) {
        if (failure != FailurePoint::SwapCleanupFailure) {
          releasePreparedFiles();
          DeleteTree(generation.get());
        }
        return false;
      }
      std::shared_ptr<Generation> published(new (std::nothrow) Generation(
          generation.get(), input.records.length, input.index.length, next,
          epoch));
      if (!published || !published->HasLease()) {
        published.reset();
        releasePreparedFiles();
        DeleteTree(generation.get());
        return false;
      }
      if (prepared->queryIndex != nullptr)
        published->InstallQueryIndex(prepared->queryIndex);
      PathBuffer commitPath = NewPathBuffer();
      Bytes commitBytes{'H', 'G', 'C', '2'};
      U16(&commitBytes, 2); U16(&commitBytes, 1); U64(&commitBytes, next);
      commitBytes.insert(commitBytes.end(), epoch.begin(), epoch.end());
      commitBytes.insert(commitBytes.end(), manifestDigest.bytes.begin(),
                         manifestDigest.bytes.end());
      HANDLE commit = INVALID_HANDLE_VALUE;
      if (!commitPath || commitBytes.size() != kCommitBytes ||
          failure == FailurePoint::OpenCommit ||
          !Join(generation.get(), L"commit.hgc", commitPath.get()) ||
          (commit = CreateFileW(
               commitPath.get(), GENERIC_READ | GENERIC_WRITE,
               FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL,
               nullptr)) == INVALID_HANDLE_VALUE ||
          failure == FailurePoint::WriteCommit ||
          !WriteBytes(commit, commitBytes.data(), commitBytes.size()) ||
          (gPreparedCommitWrites.fetch_add(1, std::memory_order_relaxed),
           false) ||
          failure == FailurePoint::FlushCommit || !FlushFileBuffers(commit) ||
          (gPreparedCommitFlushes.fetch_add(1, std::memory_order_relaxed),
           false)) {
        if (commit != INVALID_HANDLE_VALUE) CloseHandle(commit);
        published.reset();
        releasePreparedFiles();
        DeleteTree(generation.get());
        return false;
      }
      CloseHandle(commit);
      gPreparedPublicationStage.store(5, std::memory_order_relaxed);
      if (!SetImmutable(commitPath.get())) {
        published.reset();
        releasePreparedFiles();
        DeleteTree(generation.get());
        return false;
      }
      auto authenticated = std::make_shared<AuthenticatedGenerationFiles>();
      PathBuffer promotedManifest = NewPathBuffer();
      PathBuffer promotedRecords = NewPathBuffer();
      PathBuffer promotedBlobs = NewPathBuffer();
      HANDLE heldManifest = INVALID_HANDLE_VALUE;
      if (!authenticated || !promotedManifest || !promotedRecords ||
          !promotedBlobs ||
          !Join(generation.get(), L"manifest.hgm", promotedManifest.get()) ||
          !Join(generation.get(), L"records.hgn", promotedRecords.get()) ||
          !Join(generation.get(), L"blobs.hgb", promotedBlobs.get()) ||
          !DestinationIdentityMatches(promotedRecords.get(),
                                      prepared->records,
                                      &authenticated->records) ||
          !DestinationIdentityMatches(promotedBlobs.get(), prepared->blobs,
                                      &authenticated->blobs) ||
          (heldManifest = CreateFileW(
               promotedManifest.get(), GENERIC_READ, FILE_SHARE_READ, nullptr,
               OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr)) ==
              INVALID_HANDLE_VALUE) {
        if (heldManifest != INVALID_HANDLE_VALUE) CloseHandle(heldManifest);
        return false;
      }
      gPreparedPublicationStage.store(6, std::memory_order_relaxed);
      authenticated->manifest = heldManifest;      authenticated->manifestStream = {
          kManifestBytes, codec::Hash(codec::View(manifestBytes))};
      authenticated->recordsStream = {
          prepared->records.length, prepared->records.digest};
      authenticated->blobStream = {
          prepared->blobs.length, prepared->blobs.digest};
      published->authenticatedFiles_ = std::move(authenticated);
      AcquireSRWLockExclusive(&lock_);
      active_.swap(published);
      serial_ = next;
      reservationSerial_ = next;
      ReleaseSRWLockExclusive(&lock_);
      activeLineage.reset();
      published.reset();
      CleanupRetiredGenerations(root_, epoch, next);
      gPreparedPublicationStage.store(7, std::memory_order_relaxed);
      gPreparedPromotions.fetch_add(1, std::memory_order_relaxed);
      return true;
    }

    wchar_t candidateName[96]{}, generationName[96]{};
    PathBuffer candidate = NewPathBuffer();
    PathBuffer generation = NewPathBuffer();
    if (!candidate || !generation ||
        FAILED(StringCchPrintfW(candidateName, 96, L"candidate-%lu-%020llu",
                                GetCurrentProcessId(),
                                static_cast<unsigned long long>(next))) ||
        FAILED(StringCchPrintfW(generationName, 96, L"generation-%020llu",
                                static_cast<unsigned long long>(next))) ||
        !Join(root_.c_str(), candidateName, candidate.get()) ||
        !Join(root_.c_str(), generationName, generation.get()) ||
        !CreateDirectoryW(candidate.get(), nullptr))
      return false;
    HANDLE owner = INVALID_HANDLE_VALUE, records = INVALID_HANDLE_VALUE,
           index = INVALID_HANDLE_VALUE, environment = INVALID_HANDLE_VALUE,
           blobCache = INVALID_HANDLE_VALUE, manifest = INVALID_HANDLE_VALUE;
    const auto abandon = [&]() noexcept {
      if (records != INVALID_HANDLE_VALUE)
        CloseHandle(records);
      if (index != INVALID_HANDLE_VALUE)
        CloseHandle(index);
      if (environment != INVALID_HANDLE_VALUE)
        CloseHandle(environment);
      if (blobCache != INVALID_HANDLE_VALUE)
        CloseHandle(blobCache);
      if (manifest != INVALID_HANDLE_VALUE)
        CloseHandle(manifest);
      if (owner != INVALID_HANDLE_VALUE)
        CloseHandle(owner);
      DeleteTree(candidate.get());
    };
    PathBuffer ownerPath = NewPathBuffer();
    PathBuffer recordsPath = NewPathBuffer();
    PathBuffer indexPath = NewPathBuffer();
    PathBuffer environmentPath = NewPathBuffer();
    PathBuffer blobCachePath = NewPathBuffer();
    PathBuffer manifestPath = NewPathBuffer();
    if (!ownerPath || !recordsPath || !indexPath || !environmentPath ||
        !blobCachePath || !manifestPath ||
        failure == FailurePoint::OpenOwner ||
        !Join(candidate.get(), L"owner.lock", ownerPath.get()) ||
        (owner = CreateFileW(ownerPath.get(), GENERIC_READ | GENERIC_WRITE, 0,
                             nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL,
                             nullptr)) == INVALID_HANDLE_VALUE) {
      abandon();
      return false;
    }
    Sha256 recordsDigest = canonical.recordStream.digest;
    Sha256 indexDigest = sourceIndexDigest;
    // CaptureSpool has already flushed its immutable whole-file artifacts.
    // Same-volume hard links preserve those durable bytes without a second
    // copy/hash/fsync cycle. Generic and injected sources retain the original
    // copy path and every named failure boundary.
    if (failure == FailurePoint::OpenRecords ||
        !Join(candidate.get(), L"records.hgn", recordsPath.get()) ||
        failure == FailurePoint::WriteRecords ||
        failure == FailurePoint::FlushRecords) {
      abandon();
      return false;
    }
    if (!LinkDurableWholeFile(input.records, recordsPath.get())) {
      if ((records = CreateFileW(
               recordsPath.get(), GENERIC_READ | GENERIC_WRITE,
               FILE_SHARE_READ, nullptr, CREATE_NEW,
               FILE_ATTRIBUTE_NORMAL, nullptr)) == INVALID_HANDLE_VALUE ||
          !CopyFileSliceChunked(input.records, records, &recordsDigest) ||
          !codec::Equal(recordsDigest, canonical.recordStream.digest) ||
          !FlushClose(&records, FailurePoint::FlushRecords, failure)) {
        abandon();
        return false;
      }
    }
    if (failure == FailurePoint::OpenIndex ||
        !Join(candidate.get(), L"index.hgi", indexPath.get()) ||
        failure == FailurePoint::WriteIndex ||
        failure == FailurePoint::FlushIndex) {
      abandon();
      return false;
    }
    if (!LinkDurableWholeFile(input.index, indexPath.get())) {
      if ((index = CreateFileW(
               indexPath.get(), GENERIC_READ | GENERIC_WRITE,
               FILE_SHARE_READ, nullptr, CREATE_NEW,
               FILE_ATTRIBUTE_NORMAL, nullptr)) == INVALID_HANDLE_VALUE ||
          !CopyFileSliceChunked(input.index, index, &indexDigest) ||
          !codec::Equal(indexDigest, sourceIndexDigest) ||
          !FlushClose(&index, FailurePoint::FlushIndex, failure)) {
        abandon();
        return false;
      }
    }
    if (!Join(candidate.get(), L"environment.hge", environmentPath.get()) ||
        (environment = CreateFileW(
             environmentPath.get(), GENERIC_READ | GENERIC_WRITE,
             FILE_SHARE_READ,
             nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr)) ==
            INVALID_HANDLE_VALUE ||
        !WriteBytes(environment, input.layoutEnvironment.data,
                    input.layoutEnvironment.size) ||
        !FlushClose(&environment, FailurePoint::FlushIndex,
                    FailurePoint::None)) {
      abandon();
      return false;
    }
    std::uint64_t blobCacheLength = 0;
    Sha256 blobCacheDigest{};
    if (!Join(candidate.get(), L"blobs.hgb", blobCachePath.get()) ||
        (blobCache = CreateFileW(blobCachePath.get(),
                                 GENERIC_READ | GENERIC_WRITE,
                                 FILE_SHARE_READ, nullptr, CREATE_NEW,
                                 FILE_ATTRIBUTE_NORMAL, nullptr)) ==
            INVALID_HANDLE_VALUE) {
      abandon();
      return false;
    }
    if (!CopyCanonicalBlobPlan(canonical.blobClosure, input.blobContext,
                               input.readBlob, blobCache) ||
        !FlushFileBuffers(blobCache) ||
        !FileLength(blobCache, &blobCacheLength) ||
        !HashFileRangeChunked(blobCache, 0, blobCacheLength,
                              &blobCacheDigest)) {
      abandon();
      return false;
    }
    CloseHandle(blobCache);
    blobCache = INVALID_HANDLE_VALUE;
    const Bytes prefix = ManifestPrefix(next, input, recordsDigest, indexDigest,
                                        blobCacheLength, blobCacheDigest);
    Bytes integrityInput = prefix;
    U64(&integrityInput, kSeal);
    const Sha256 manifestDigest = codec::Hash(codec::View(integrityInput));
    if (prefix.size() + manifestDigest.bytes.size() + 8 != kManifestBytes ||
        failure == FailurePoint::OpenManifest ||
        !Join(candidate.get(), L"manifest.hgm", manifestPath.get()) ||
        (manifest = CreateFileW(manifestPath.get(),
                                GENERIC_READ | GENERIC_WRITE,
                                FILE_SHARE_READ, nullptr, CREATE_NEW,
                                FILE_ATTRIBUTE_NORMAL, nullptr)) ==
            INVALID_HANDLE_VALUE ||
        failure == FailurePoint::WriteManifest ||
        !WriteBytes(manifest, prefix.data(), prefix.size()) ||
        !WriteBytes(manifest, manifestDigest.bytes.data(),
                    manifestDigest.bytes.size()) ||
        !FlushFileBuffers(manifest) || failure == FailurePoint::SealManifest) {
      abandon();
      return false;
    }
    Bytes seal;
    U64(&seal, kSeal);
    if (!WriteBytes(manifest, seal.data(), seal.size()) ||
        FlushFileBuffers(manifest) == FALSE) {
      abandon();
      return false;
    }
    CloseHandle(manifest);
    manifest = INVALID_HANDLE_VALUE;
    if (!SetImmutable(recordsPath.get()) || !SetImmutable(indexPath.get()) ||
        !SetImmutable(environmentPath.get()) ||
        !SetImmutable(blobCachePath.get()) ||
        !SetImmutable(manifestPath.get())) {
      abandon();
      return false;
    }
    CloseHandle(owner);
    owner = INVALID_HANDLE_VALUE;
    DeleteFileW(ownerPath.get());
    if (failure == FailurePoint::Rename ||
        !MoveFileExW(candidate.get(), generation.get(), MOVEFILE_WRITE_THROUGH)) {
      abandon();
      return false;
    }
    if (failure == FailurePoint::Swap ||
        failure == FailurePoint::SwapCleanupFailure) {
      if (failure != FailurePoint::SwapCleanupFailure)
        DeleteTree(generation.get());
      return false;
    }
    std::shared_ptr<Generation> published(new (std::nothrow) Generation(
        generation.get(), input.records.length, input.index.length, next,
        epoch));
    if (!published || !published->HasLease()) {
      published.reset();
      DeleteTree(generation.get());
      return false;
    }
    PathBuffer commitPath = NewPathBuffer();
    HANDLE commit = INVALID_HANDLE_VALUE;
    Bytes commitBytes{'H', 'G', 'C', '2'};
    U16(&commitBytes, 2);
    U16(&commitBytes, 1);
    U64(&commitBytes, next);
    commitBytes.insert(commitBytes.end(), epoch.begin(), epoch.end());
    commitBytes.insert(commitBytes.end(), manifestDigest.bytes.begin(),
                       manifestDigest.bytes.end());
    if (!commitPath || commitBytes.size() != kCommitBytes ||
        failure == FailurePoint::OpenCommit ||
        !Join(generation.get(), L"commit.hgc", commitPath.get()) ||
        (commit = CreateFileW(commitPath.get(), GENERIC_READ | GENERIC_WRITE,
                              FILE_SHARE_READ, nullptr, CREATE_NEW,
                              FILE_ATTRIBUTE_NORMAL, nullptr)) ==
            INVALID_HANDLE_VALUE ||
        failure == FailurePoint::WriteCommit ||
        !WriteBytes(commit, commitBytes.data(), commitBytes.size()) ||
        failure == FailurePoint::FlushCommit ||
        !FlushFileBuffers(commit)) {
      if (commit != INVALID_HANDLE_VALUE) CloseHandle(commit);
      published.reset();
      DeleteTree(generation.get());
      return false;
    }
    CloseHandle(commit);
    if (!SetImmutable(commitPath.get())) {
      published.reset();
      DeleteTree(generation.get());
      return false;
    }
    AuthenticatedGenerationPin authenticated;
    gGenerationAuthenticationCount.fetch_add(1, std::memory_order_relaxed);
    if (!AuthenticateGenerationImpl(published, &authenticated, false)) {
      // The commit remains durable for startup recovery; publication does not
      // expose a lease until the independently verified destination is pinned.
      return false;
    }
    published->authenticatedFiles_ = std::move(authenticated);
    // commit.hgc is the durable publication point. Everything after this is
    // in-memory, noexcept, and cannot turn committed success into failure.
    AcquireSRWLockExclusive(&lock_);
    active_.swap(published);
    serial_ = next;
    reservationSerial_ = next;
    ReleaseSRWLockExclusive(&lock_);
    // Drop this publication's old-generation references before probing the
    // OS-visible leases. The allocator mutex remains held, so retirement and
    // recursive deletion stay under the same store authority serialization.
    activeLineage.reset();
    published.reset();
    CleanupRetiredGenerations(root_, epoch);
    return true;
  } catch (...) {
    return false;
  }
}

} // namespace hancom::graph::store

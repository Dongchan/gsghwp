#pragma once

#include "DocumentGraphCodec.h"

#include <Windows.h>
#include <array>
#include <cstdint>
#include <memory>
#include <string>

namespace hancom::graph::store {

inline constexpr std::uint16_t kStoreVersion = 1;

enum class FailurePoint : std::uint8_t {
  None = 0,
  StartupCleanup,
  OpenOwner,
  OpenRecords,
  WriteRecords,
  FlushRecords,
  OpenIndex,
  WriteIndex,
  FlushIndex,
  OpenManifest,
  WriteManifest,
  SealManifest,
  Rename,
  Swap,
  SwapCleanupFailure,
  OpenCommit,
  WriteCommit,
  FlushCommit,
  OpenReservation,
  WriteReservation,
  FlushReservation,
  ReplaceReservation,
  OpenIdentity,
  WriteIdentity,
  FlushIdentity,
  ReplaceIdentity,
  MigrationAfterIntent,
  MigrationAfterJournal,
  MigrationAfterCheckpoint,
  MigrationAfterMarkers,
  MigrationAfterReadonlyClear,
  MigrationAfterMarkerReplace,
  MigrationTempWrite,
  MigrationTempFlush,
  MigrationTempReplace,
  PrepareOpenEnvironment,
  PrepareWriteEnvironment,
  PrepareFlushEnvironment,
  PrepareOpenBlobs,
  PrepareWriteBlobs,
  PrepareFlushBlobs,
  PreparedArtifactMismatch,
  PreparedFileIdentityMismatch,
  PreparedManifestPatchMismatch,
  PreparedParentBarrier,
  PostCommitReturn,
};

struct FileSlice final {
  HANDLE file = INVALID_HANDLE_VALUE;
  std::uint64_t offset = 0;
  std::uint64_t length = 0;
  bool durableWholeFile = false;
};

struct CanonicalStream final {
  std::uint64_t length = 0;
  Sha256 digest{};
};

struct CapturedState final {
  CanonicalStream route{};
  CanonicalStream cursor{};
  CanonicalStream selection{};
  bool modified = false;
};

struct TraversalManifest final {
  Sha256 observedSemanticRoot{};
  Sha256 layoutRoot{};
  Sha256 captureRoot{};
  bool semanticCertified = false;
  bool layoutPresent = false;
  std::uint64_t closedProfileBits = 0;
  CaptureIntegrity integrity = CaptureIntegrity::Complete;
  CanonicalStream coverage{};
  CanonicalStream unavailable{};
  CanonicalStream diagnostics{};
};

struct PreparedFileArtifact final {
  HANDLE file = INVALID_HANDLE_VALUE;
  std::uint64_t length = 0;
  Sha256 digest{};
  std::uint64_t volumeSerial = 0;
  std::array<std::uint8_t, 16> fileId{};
};

class GenerationQueryIndex {
public:
  virtual ~GenerationQueryIndex() noexcept = default;
};
using GenerationQueryIndexPin = std::shared_ptr<GenerationQueryIndex>;

// A source-bound candidate produced by the final canonical traversal in the
// store root. Handles deny write/delete sharing until promotion completes.
struct PreparedGenerationCandidate final {
  ~PreparedGenerationCandidate() noexcept;
  PreparedGenerationCandidate() noexcept = default;
  PreparedGenerationCandidate(const PreparedGenerationCandidate &) = delete;
  PreparedGenerationCandidate &operator=(
      const PreparedGenerationCandidate &) = delete;
  std::wstring directory{};
  HANDLE owner = INVALID_HANDLE_VALUE;
  PreparedFileArtifact records{};
  PreparedFileArtifact index{};
  PreparedFileArtifact environment{};
  PreparedFileArtifact blobs{};
  GenerationQueryIndexPin queryIndex{};
};
using PreparedGenerationPin =
    std::shared_ptr<PreparedGenerationCandidate>;

struct PublicationInput final {
  TraversalManifest first{};
  TraversalManifest second{};
  CapturedState baseline{};
  CapturedState firstRestoration{};
  CapturedState secondRestoration{};
  FileSlice records{};
  FileSlice index{};
  void *blobContext = nullptr;
  codec::BlobReadCallback readBlob = nullptr;
  codec::ByteView layoutEnvironment{};
  codec::CanonicalArtifactPin canonicalArtifact{};
  PreparedGenerationPin preparedCandidate{};
  std::uint64_t observedControlCount = 0;
  bool legacyHwpmlDiagnosticFailed = false;
};

struct AuthenticatedGenerationFiles;
using AuthenticatedGenerationPin =
    std::shared_ptr<const AuthenticatedGenerationFiles>;

class Generation final {
public:
  ~Generation() noexcept;
  Generation(const Generation &) = delete;
  Generation &operator=(const Generation &) = delete;
  std::wstring Path() const;
  std::uint64_t RecordsBytes() const noexcept { return recordsBytes_; }
  std::uint64_t IndexBytes() const noexcept { return indexBytes_; }
  std::uint64_t Serial() const noexcept { return serial_; }
  const std::array<std::uint8_t, 16>& StoreEpoch() const noexcept {
    return storeEpoch_;
  }
  AuthenticatedGenerationPin AuthenticatedFiles() const noexcept {
    return authenticatedFiles_;
  }
  GenerationQueryIndexPin QueryIndex() const noexcept;
  void InstallQueryIndex(const GenerationQueryIndexPin &index) const noexcept;

private:
  friend class GraphStore;
  Generation(const wchar_t *path, std::uint64_t records,
             std::uint64_t index, std::uint64_t serial,
             const std::array<std::uint8_t, 16>& storeEpoch) noexcept;
  bool HasLease() const noexcept { return lease_ != INVALID_HANDLE_VALUE; }
  std::array<wchar_t, 32768> path_{};
  std::uint64_t recordsBytes_ = 0;
  std::uint64_t indexBytes_ = 0;
  std::uint64_t serial_ = 0;
  std::array<std::uint8_t, 16> storeEpoch_{};
  HANDLE lease_ = INVALID_HANDLE_VALUE;
  AuthenticatedGenerationPin authenticatedFiles_{};
  mutable SRWLOCK queryIndexLock_ = SRWLOCK_INIT;
  mutable GenerationQueryIndexPin queryIndex_{};
};

using GenerationPin = std::shared_ptr<const Generation>;

// Handles are authenticated against the sealed HGM manifest and remain open
// without write/delete sharing for the consumer lifetime.
struct AuthenticatedGenerationFiles final {
  ~AuthenticatedGenerationFiles() noexcept;
  AuthenticatedGenerationFiles() noexcept = default;
  AuthenticatedGenerationFiles(const AuthenticatedGenerationFiles &) = delete;
  AuthenticatedGenerationFiles &operator=(
      const AuthenticatedGenerationFiles &) = delete;
  HANDLE manifest = INVALID_HANDLE_VALUE;
  HANDLE records = INVALID_HANDLE_VALUE;
  HANDLE blobs = INVALID_HANDLE_VALUE;
  CanonicalStream manifestStream{};
  CanonicalStream recordsStream{};
  CanonicalStream blobStream{};
};

bool AuthenticateGeneration(
    const GenerationPin &generation,
    AuthenticatedGenerationPin *authenticated) noexcept;
void ResetGenerationAuthenticationDebugCount() noexcept;
std::uint64_t ReadGenerationAuthenticationDebugCount() noexcept;
struct PublicationDebugCounters final {
  std::uint64_t preparedPromotions = 0;
  std::uint64_t manifestWrites = 0;
  std::uint64_t manifestFlushes = 0;
  std::uint64_t commitWrites = 0;
  std::uint64_t commitFlushes = 0;
  std::uint64_t destinationFullFileRehashes = 0;
  std::uint64_t blobCopyReplays = 0;
  std::uint64_t environmentCopyReplays = 0;
  std::uint64_t lastPreparedStage = 0;
};
void ResetPublicationDebugCounters() noexcept;
PublicationDebugCounters ReadPublicationDebugCounters() noexcept;

class GraphStore final {
public:
  explicit GraphStore(std::wstring root) noexcept;
  ~GraphStore() noexcept;
  GraphStore(const GraphStore &) = delete;
  GraphStore &operator=(const GraphStore &) = delete;

  bool Initialize(FailurePoint failure = FailurePoint::None) noexcept;
  bool Publish(const PublicationInput &input,
               FailurePoint failure = FailurePoint::None) noexcept;
  GenerationPin PinActive() const noexcept;
  std::uint64_t ActiveSerial() const noexcept;
  const std::wstring &Root() const noexcept { return root_; }

private:
  std::wstring root_;
  mutable SRWLOCK lock_ = SRWLOCK_INIT;
  std::shared_ptr<Generation> active_{};
  std::uint64_t serial_ = 0;
  std::uint64_t reservationSerial_ = 0;
  std::array<std::uint8_t, 16> storeEpoch_{};
  Sha256 authorityIdentity_{};
  HANDLE rootDirectory_ = INVALID_HANDLE_VALUE;
  bool authorityResolved_ = false;
};

bool TraversalsMatch(const TraversalManifest &a,
                     const TraversalManifest &b) noexcept;
bool CapturedStatesMatch(const CapturedState &a,
                         const CapturedState &b) noexcept;
bool CopyFileSliceChunked(const FileSlice &source, HANDLE destination,
                          Sha256 *digest) noexcept;
bool HashFileRangeChunked(HANDLE file, std::uint64_t offset,
                          std::uint64_t length, Sha256 *digest) noexcept;
bool ResolveStoreAuthorityIdentity(const std::wstring &root,
                                   Sha256 *identity) noexcept;

} // namespace hancom::graph::store

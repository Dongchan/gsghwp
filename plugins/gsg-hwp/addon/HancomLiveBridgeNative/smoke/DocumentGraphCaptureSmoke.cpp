#include "../DocumentGraphCapture.h"
#include "../DocumentGraphCaptureRecords.h"
#include "../DocumentGraphCaptureSpool.h"
#include "../DocumentGraphCodecInternal.h"
#include "../DocumentGraphIdentity.h"
#include "../DocumentGraphLayout.h"
#include "../DocumentGraphQuery.h"
#include "../DocumentGraphEffectiveProperties.h"
#include "../OfficialApiCapability.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <filesystem>
#include <map>
#include <memory>
#include <numeric>
#include <set>
#include <sstream>
#include <string>
#include <vector>

bool BuildFakeDispatchReferenceClosure(
    const std::vector<hancom::graph::properties::ReferenceSite>& sites,
    hancom::graph::capture::ReaderPayload* const output);
enum class NativeAnchorCase : std::uint8_t {
    Available,
    DispatchFailure,
    MissingList,
    InvalidList,
    MissingPara,
    InvalidPara,
    MissingPos,
    InvalidPos,
};

IDispatch* CreateNativeAnchorDispatch(
    NativeAnchorCase anchorCase,
    const wchar_t* ctrlId,
    const std::vector<hancom::graph::capture::SectionObservation>& sections,
    const std::vector<hancom::graph::capture::ControlObservation>& controls);
size_t NativeAnchorDispatchReadCount() noexcept;

bool DuplicateEmptyRealTableReaderRecaptureSmoke(
    const std::vector<hancom::graph::capture::ControlObservation>& controls,
    const hancom::graph::capture::ReaderPayload& tables);

namespace {

using hancom::graph::CaptureIntegrity;
using hancom::graph::capture::AttemptResult;
using hancom::graph::capture::AttemptMismatchBits;
using hancom::graph::capture::CaptureCoordinator;
using hancom::graph::capture::CaptureDiagnostics;
using hancom::graph::capture::CaptureProgressPoint;
using hancom::graph::capture::CaptureStatus;
using hancom::graph::capture::FailureStage;
using hancom::graph::capture::FindFirstFingerprintDivergence;
using hancom::graph::NodeId;
using hancom::graph::capture::MismatchCaptureRoot;
using hancom::graph::capture::MismatchClosedProfiles;
using hancom::graph::capture::MismatchCoverage;
using hancom::graph::capture::MismatchDiagnostics;
using hancom::graph::capture::MismatchIntegrity;
using hancom::graph::capture::MismatchLayoutEnvironment;
using hancom::graph::capture::MismatchLayoutPresent;
using hancom::graph::capture::MismatchLayoutRoot;
using hancom::graph::capture::MismatchLegacyHwpmlDiagnostic;
using hancom::graph::capture::MismatchObservedControlCount;
using hancom::graph::capture::MismatchSemanticCertified;
using hancom::graph::capture::MismatchSemanticRoot;
using hancom::graph::capture::MismatchUnavailable;
using hancom::graph::capture::CaptureSpool;
using hancom::graph::capture::FailureInjection;
using hancom::graph::capture::FailurePoint;
using hancom::graph::capture::ReaderSuite;
using hancom::graph::capture::ReaderPayload;
using hancom::graph::capture::ReadTypedBuildProfiles;
using hancom::graph::capture::ResetTypedBuildProfiles;
using hancom::graph::capture::SetTypedGraphReuseForTesting;
using hancom::graph::capture::TypedBuildProfile;
using hancom::graph::capture::TypedBuildSubstage;
using hancom::graph::capture::TypedBuildTiming;
using hancom::graph::capture::kQualifiedReaderCount;
using hancom::graph::store::CapturedState;
using hancom::graph::store::PublicationInput;
using hancom::graph::store::GraphStore;

std::vector<std::uint8_t> NormativeEnvironment(
    const std::uint32_t dpiX = 96,
    const hancom::graph::Sha256* const pageSetupDigest = nullptr) {
    hancom::graph::layout::LayoutEnvironmentV1 environment;
    environment.hwpFileVersion = {13, 0, 0, 1};
    environment.printerName = L"Fixture Printer";
    environment.devmodeDigest.bytes.fill(0x11);
    environment.fontInventoryDigest.bytes.fill(0x22);
    environment.dpiX = dpiX;
    environment.dpiY = 96;
    environment.systemLcid = 0x409;
    if (pageSetupDigest == nullptr) {
        environment.pageSetupDigest.bytes.fill(0x33);
    } else {
        environment.pageSetupDigest = *pageSetupDigest;
    }
    std::vector<std::uint8_t> serialized;
    if (!hancom::graph::layout::SerializeLayoutEnvironmentV1(
            environment, &serialized)) {
        return {};
    }
    return serialized;
}

bool StoryScopedParagraphIdentitySmoke(
    bool* const invalidLocatorFailClosed) {
    using hancom::graph::NodeId;
    using hancom::graph::NodeKind;
    using hancom::graph::StoryKind;
    using hancom::graph::capture::CaptureIdentityArena;
    using hancom::graph::capture::NativePosition;
    using hancom::graph::identity::EqualUuid;

    NodeId bodyStory{};
    NodeId cellStory{};
    bodyStory.bytes[0] = 1;
    bodyStory.bytes[6] = 0x40;
    bodyStory.bytes[8] = 0x80;
    cellStory.bytes[0] = 2;
    cellStory.bytes[6] = 0x40;
    cellStory.bytes[8] = 0x80;

    if (invalidLocatorFailClosed == nullptr) {
        return false;
    }
    *invalidLocatorFailClosed = false;

    CaptureIdentityArena arena;
    NodeId bodyFirst{};
    NodeId cellEqualPosition{};
    NodeId bodySecond{};
    const NativePosition first{7, 3, 0};
    const NativePosition second{7, 4, 0};
    if (!arena.AcquireParagraph(bodyStory, first, &bodyFirst) ||
        !arena.AcquireParagraph(cellStory, first, &cellEqualPosition) ||
        !arena.AcquireParagraph(bodyStory, second, &bodySecond)) {
        return false;
    }

    NodeId rejectedNegativeList = bodyFirst;
    NodeId rejectedNegativeParagraph = bodySecond;
    const bool negativeListRejected = !arena.AcquireParagraph(
        bodyStory, {-1, first.paragraph, 0}, &rejectedNegativeList);
    const bool negativeParagraphRejected = !arena.AcquireParagraph(
        bodyStory, {first.list, -1, 0}, &rejectedNegativeParagraph);

    NodeId bodySecondRecaptured{};
    NodeId cellRecaptured{};
    NodeId bodyFirstRecaptured{};
    const bool permutedRecapture =
        arena.AcquireParagraph(bodyStory, second, &bodySecondRecaptured) &&
        arena.AcquireParagraph(cellStory, first, &cellRecaptured) &&
        arena.AcquireParagraph(bodyStory, first, &bodyFirstRecaptured);
    *invalidLocatorFailClosed = negativeListRejected &&
        negativeParagraphRejected &&
        EqualUuid(rejectedNegativeList, bodyFirst) &&
        EqualUuid(rejectedNegativeParagraph, bodySecond) &&
        permutedRecapture &&
        EqualUuid(bodyFirst, bodyFirstRecaptured) &&
        EqualUuid(bodySecond, bodySecondRecaptured) &&
        EqualUuid(cellEqualPosition, cellRecaptured);
    return *invalidLocatorFailClosed && permutedRecapture &&
        !EqualUuid(bodyFirst, cellEqualPosition) &&
        !EqualUuid(bodyFirst, bodySecond) &&
        EqualUuid(bodyFirst, bodyFirstRecaptured) &&
        EqualUuid(bodySecond, bodySecondRecaptured) &&
        EqualUuid(cellEqualPosition, cellRecaptured) &&
        hancom::graph::IsLegalPrimaryParent(
            NodeKind::Paragraph, NodeKind::Section) &&
        hancom::graph::IsLegalPrimaryParent(
            NodeKind::Paragraph, NodeKind::Story,
            StoryKind::Other, StoryKind::TableCell);
}

void AddCharacterShapeTerminal(
    std::vector<ReaderPayload>* const payloads,
    const wchar_t* const runIdentity) {
    using namespace hancom::graph::capture;
    ReaderPayload& effective = (*payloads)[static_cast<size_t>(
        QualifiedReader::EffectiveProperties)];
    effective.coverage = hancom::graph::CoverageState::ReadFailed;
    CoverageObservation terminal;
    terminal.target = PropertyTarget::Run;
    terminal.targetIdentity = runIdentity;
    terminal.coordinate = hancom::graph::CoverageCoordinateKind::NodeField;
    terminal.ownerField = 102;
    terminal.profile = hancom::graph::ProfileId::EditableText;
    terminal.state = hancom::graph::CoverageState::NotExposed;
    effective.coverageFacts.push_back(std::move(terminal));
}

std::vector<ReaderPayload> CertifiedLayoutPayloads(
    const std::vector<std::uint8_t>& environment) {
    using namespace hancom::graph::capture;
    std::vector<ReaderPayload> payloads(kQualifiedReaderCount);
    for (size_t index = 0; index < payloads.size(); ++index) {
        payloads[index].reader = static_cast<QualifiedReader>(index);
        payloads[index].outcome = ReaderOutcome::Complete;
        payloads[index].coverage = hancom::graph::CoverageState::Complete;
    }
    payloads[0].bodyList = {
        hancom::graph::ObservationState::Value, true, 0, {}, 811};
    payloads[0].sections.push_back({0, {0, 0, 0}});
    ParagraphObservation paragraph;
    paragraph.start = {0, 0, 0};
    paragraph.runs.push_back({{0, 0, 0}, {0, 0, 4}, L"text"});
    payloads[1].paragraphs.push_back(std::move(paragraph));
    AddCharacterShapeTerminal(&payloads, L"0:0:0:4");
    for (const hancom::graph::PropertyKeyId key : {12000U, 12001U}) {
        PropertyObservation property;
        property.target = PropertyTarget::Paragraph;
        property.targetIdentity = L"0:0";
        property.ownerField = 10;
        property.key = key;
        property.scalar = hancom::graph::ScalarTag::Uint64;
        property.state = hancom::graph::ObservationState::Value;
        property.origin = hancom::graph::PropertyOrigin::Generated;
        property.integerValue = 1;
        payloads[6].layoutProperties.push_back(std::move(property));
    }
    payloads[6].layoutEnvironment = environment;
    payloads[6].layoutObservedKindCounts[0] =
        payloads[1].paragraphs.size();
    payloads[6].layoutPerKindComplete =
        payloads[6].layoutProperties.size() == 2;
    return payloads;
}

struct FakeContext {
    CapturedState baseline{};
    AttemptResult attempts[2]{};
    size_t beginCount = 0;
    size_t readerCalls = 0;
    size_t releaseCount = 0;
    size_t restoreCount = 0;
    size_t stateReads = 0;
    size_t publishCalls = 0;
    size_t activeSerial = 0;
    bool readerFails = false;
    bool restoreFails = false;
    bool publishFails = false;
    bool cancelled = false;
    bool cancelAfterFirstReader = false;
    bool mutateRestoredState = false;
    bool sessionActive = false;
    bool sessionCleanupFails = false;
    bool beginSessionFails = false;
    bool beginAttemptFails = false;
    bool partialIdentityState = false;
    std::uint64_t activeSessionSerial = 0;
    std::vector<std::uint64_t> sessionSerials{};
    size_t sessionBeginCount = 0;
    size_t sessionAbortCount = 0;
    size_t sessionCommitReadyCount = 0;
    std::vector<std::array<size_t, 3>> progress{};
};

hancom::graph::Sha256 Digest(const std::uint8_t value) {
    hancom::graph::Sha256 digest;
    digest.bytes.fill(value);
    return digest;
}

void FillManifest(
    hancom::graph::store::TraversalManifest* const manifest,
    const std::uint8_t root) {
    manifest->observedSemanticRoot = Digest(root);
    manifest->layoutRoot = Digest(static_cast<std::uint8_t>(root + 1));
    manifest->captureRoot = Digest(static_cast<std::uint8_t>(root + 2));
    manifest->semanticCertified = true;
    manifest->layoutPresent = true;
    manifest->closedProfileBits = 0x1f;
    manifest->integrity = CaptureIntegrity::Complete;
    manifest->coverage = {10, Digest(10)};
    manifest->unavailable = {11, Digest(11)};
    manifest->diagnostics = {12, Digest(12)};
}

FakeContext Context() {
    FakeContext context;
    context.baseline.route = {1, Digest(1)};
    context.baseline.cursor = {2, Digest(2)};
    context.baseline.selection = {3, Digest(3)};
    context.baseline.modified = false;
    FillManifest(&context.attempts[0].manifest, 20);
    FillManifest(&context.attempts[1].manifest, 20);
    context.attempts[0].layoutEnvironment = NormativeEnvironment();
    context.attempts[1].layoutEnvironment = NormativeEnvironment();
    context.attempts[0].observedControlCount = 32;
    context.attempts[1].observedControlCount = 32;
    return context;
}

bool CaptureState(void* const raw, CapturedState* const output) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr || output == nullptr) {
        return false;
    }
    ++context->stateReads;
    *output = context->baseline;
    if (context->mutateRestoredState && context->stateReads > 1) {
        output->modified = true;
    }
    return true;
}

bool BeginCaptureSession(
    void* const raw,
    const std::uint64_t serial) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr || context->sessionActive || serial == 0) {
        return false;
    }
    context->sessionActive = true;
    context->activeSessionSerial = serial;
    context->partialIdentityState = false;
    context->sessionSerials.push_back(serial);
    ++context->sessionBeginCount;
    return !context->beginSessionFails;
}

bool PrepareCapturePublication(
    void* const raw,
    const std::uint64_t serial) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr || !context->sessionActive ||
        context->activeSessionSerial != serial) {
        return false;
    }
    ++context->sessionCommitReadyCount;
    return !context->sessionCleanupFails;
}

bool AbortCaptureSession(
    void* const raw,
    const std::uint64_t serial) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr || !context->sessionActive ||
        context->activeSessionSerial != serial) {
        return false;
    }
    context->sessionActive = false;
    context->activeSessionSerial = 0;
    context->partialIdentityState = false;
    ++context->sessionAbortCount;
    return !context->sessionCleanupFails;
}

void CommitCaptureSession(
    void* const raw,
    const std::uint64_t serial) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr || !context->sessionActive ||
        context->activeSessionSerial != serial) {
        return;
    }
    context->sessionActive = false;
    context->activeSessionSerial = 0;
    context->partialIdentityState = false;
}

bool BeginAttempt(void* const raw, const size_t) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr) {
        return false;
    }
    ++context->beginCount;
    context->partialIdentityState = true;
    return !context->beginAttemptFails;
}

bool RunReader(
    void* const raw,
    const size_t,
    const size_t) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr) {
        return false;
    }
    ++context->readerCalls;
    if (context->cancelAfterFirstReader && context->readerCalls == 1) {
        context->cancelled = true;
    }
    return !context->readerFails;
}

bool FinishAttempt(
    void* const raw,
    const size_t attempt,
    AttemptResult* const output) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr || output == nullptr || attempt >= 2) {
        return false;
    }
    *output = context->attempts[attempt];
    return true;
}

bool ReleaseScans(void* const raw) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr) {
        return false;
    }
    ++context->releaseCount;
    return true;
}

bool RestoreState(
    void* const raw,
    const CapturedState&) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr) {
        return false;
    }
    ++context->restoreCount;
    return !context->restoreFails;
}

bool Cancelled(void* const raw) noexcept {
    const auto* const context = static_cast<FakeContext*>(raw);
    return context == nullptr || context->cancelled;
}

void CaptureProgress(
    void* const raw,
    const CaptureProgressPoint point,
    const std::uint64_t attempt,
    const std::uint64_t reader) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr) return;
    try {
        context->progress.push_back({
            static_cast<size_t>(point),
            static_cast<size_t>(attempt),
            static_cast<size_t>(reader)});
    } catch (...) {
    }
}

bool Publish(
    void* const raw,
    const PublicationInput&) noexcept {
    auto* const context = static_cast<FakeContext*>(raw);
    if (context == nullptr) {
        return false;
    }
    ++context->publishCalls;
    if (context->publishFails) {
        return false;
    }
    ++context->activeSerial;
    return true;
}

ReaderSuite Suite() {
    return {
        kQualifiedReaderCount,
        CaptureState,
        BeginAttempt,
        RunReader,
        FinishAttempt,
        ReleaseScans,
        RestoreState,
        Cancelled,
        BeginCaptureSession,
        PrepareCapturePublication,
        AbortCaptureSession,
        CommitCaptureSession,
        CaptureProgress,
    };
}

struct ArtifactSchedule final {
    ArtifactSchedule() noexcept {
        workerStarted = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        releaseWorker = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    }
    ~ArtifactSchedule() noexcept {
        if (workerStarted != nullptr) CloseHandle(workerStarted);
        if (releaseWorker != nullptr) CloseHandle(releaseWorker);
    }
    HANDLE workerStarted = nullptr;
    HANDLE releaseWorker = nullptr;
    std::atomic<unsigned> sequence{0};
    std::atomic<unsigned> workerStartOrder{0};
    std::atomic<unsigned> attemptOneStartOrder{0};
    std::atomic<unsigned> workerDoneOrder{0};
    std::atomic<unsigned> retained{0};
    std::atomic<unsigned> workerComCalls{0};
    bool blockWorker = true;
    bool failWorker = false;
};

struct ArtifactFakeContext final : FakeContext {
    std::shared_ptr<ArtifactSchedule> schedule{};
    bool failAttemptOneReader = false;
    bool cancelAttemptOne = false;
    bool mutateAttemptOne = false;
};

struct FakeArtifactOwner final {
    std::shared_ptr<ArtifactSchedule> schedule{};
    AttemptResult result{};
    size_t attempt = 0;
};

bool BuildFakeArtifact(
    const std::shared_ptr<void>& raw,
    AttemptResult* const output) noexcept {
    const auto owner = std::static_pointer_cast<FakeArtifactOwner>(raw);
    if (owner == nullptr || output == nullptr || owner->schedule == nullptr)
        return false;
    if (owner->attempt == 0) {
        owner->schedule->workerStartOrder.store(
            owner->schedule->sequence.fetch_add(1) + 1);
        if (SetEvent(owner->schedule->workerStarted) == FALSE) return false;
        if (owner->schedule->blockWorker &&
            WaitForSingleObject(owner->schedule->releaseWorker, INFINITE) !=
                WAIT_OBJECT_0)
            return false;
        owner->schedule->workerDoneOrder.store(
            owner->schedule->sequence.fetch_add(1) + 1);
        if (owner->schedule->failWorker) return false;
    }
    *output = owner->result;
    return true;
}

bool PrepareFakeReplay(
    const std::shared_ptr<void>& first,
    const std::shared_ptr<void>& second) noexcept {
    return first != nullptr && second != nullptr;
}

bool DetachFakeArtifact(
    void* const raw, const size_t attempt,
    hancom::graph::capture::AttemptArtifactInput* const output) noexcept {
    auto* const context = static_cast<ArtifactFakeContext*>(raw);
    if (context == nullptr || output == nullptr || attempt >= 2 ||
        context->schedule == nullptr)
        return false;
    try {
        auto owner = std::make_shared<FakeArtifactOwner>();
        owner->schedule = context->schedule;
        owner->result = context->attempts[attempt];
        owner->attempt = attempt;
        if (attempt == 1 && context->mutateAttemptOne)
            owner->result.manifest.observedSemanticRoot = Digest(0xee);
        output->owner = std::move(owner);
        output->build = BuildFakeArtifact;
        output->prepareReplay = PrepareFakeReplay;
        output->comInterfacePointers = 0;
        output->mutableContextReferences = 0;
        return true;
    } catch (...) {
        return false;
    }
}

bool RetainFakeArtifact(
    void* const raw, const size_t,
    const std::shared_ptr<void>&) noexcept {
    auto* const context = static_cast<ArtifactFakeContext*>(raw);
    if (context == nullptr || context->schedule == nullptr) return false;
    context->schedule->retained.fetch_add(1);
    return true;
}

bool RunArtifactReader(
    void* const raw, const size_t attempt, const size_t reader) noexcept {
    auto* const context = static_cast<ArtifactFakeContext*>(raw);
    if (context == nullptr || context->schedule == nullptr) return false;
    if (attempt == 1 && reader == 0) {
        if (WaitForSingleObject(
                context->schedule->workerStarted, INFINITE) != WAIT_OBJECT_0)
            return false;
        context->schedule->attemptOneStartOrder.store(
            context->schedule->sequence.fetch_add(1) + 1);
        if ((context->failAttemptOneReader || context->cancelAttemptOne) &&
            SetEvent(context->schedule->releaseWorker) == FALSE)
            return false;
        if (context->failAttemptOneReader) return false;
    }
    const bool result = RunReader(raw, attempt, reader);
    if (attempt == 1 && reader + 1 == kQualifiedReaderCount)
        static_cast<void>(SetEvent(context->schedule->releaseWorker));
    if (attempt == 1 && reader == 0 && context->cancelAttemptOne)
        context->cancelled = true;
    return result;
}

bool PublishArtifact(
    void* const raw, const PublicationInput& input) noexcept {
    auto* const context = static_cast<ArtifactFakeContext*>(raw);
    return context != nullptr && context->schedule != nullptr &&
        context->schedule->workerDoneOrder.load() != 0 &&
        context->schedule->retained.load() == 2 &&
        Publish(raw, input);
}

ReaderSuite ArtifactSuite() {
    ReaderSuite suite = Suite();
    suite.runReader = RunArtifactReader;
    suite.finishAttempt = nullptr;
    suite.detachAttemptArtifact = DetachFakeArtifact;
    suite.retainAttemptArtifact = RetainFakeArtifact;
    return suite;
}

bool SerialCoordinatorOverlapRedSmoke() {
    FakeContext context = Context();
    CaptureCoordinator coordinator;
    if (coordinator.Capture(
            Suite(), &context, Publish, &context, {}) !=
        CaptureStatus::Complete)
        return false;
    size_t finishZero = context.progress.size();
    size_t attemptOneReader = context.progress.size();
    for (size_t index = 0; index != context.progress.size(); ++index) {
        if (context.progress[index][0] == static_cast<size_t>(
                CaptureProgressPoint::FinishEnd) &&
            context.progress[index][1] == 0)
            finishZero = (std::min)(finishZero, index);
        if (context.progress[index][0] == static_cast<size_t>(
                CaptureProgressPoint::ReaderStart) &&
            context.progress[index][1] == 1)
            attemptOneReader = (std::min)(attemptOneReader, index);
    }
    // The overlap acceptance predicate is attemptOneReader < finishZero;
    // retaining the serial coordinator makes that deterministic RED.
    return finishZero < attemptOneReader;
}

bool AttemptArtifactCoordinatorSmoke() {
    const auto run = [](const bool blockWorker, const bool failWorker,
                        const bool failAttemptOne, const bool cancelAttemptOne,
                        const bool mutateAttemptOne,
                        const CaptureStatus expected) {
        ArtifactFakeContext context;
        static_cast<FakeContext&>(context) = Context();
        context.schedule = std::make_shared<ArtifactSchedule>();
        if (context.schedule->workerStarted == nullptr ||
            context.schedule->releaseWorker == nullptr)
            return false;
        context.schedule->blockWorker = blockWorker;
        context.schedule->failWorker = failWorker;
        context.failAttemptOneReader = failAttemptOne;
        context.cancelAttemptOne = cancelAttemptOne;
        context.mutateAttemptOne = mutateAttemptOne;
        CaptureCoordinator coordinator;
        CaptureDiagnostics diagnostics;
        const CaptureStatus status = coordinator.Capture(
            ArtifactSuite(), &context, PublishArtifact, &context, {},
            &diagnostics);
        const unsigned workerStart =
            context.schedule->workerStartOrder.load();
        const unsigned attemptOneStart =
            context.schedule->attemptOneStartOrder.load();
        const bool ordered = workerStart != 0 && attemptOneStart != 0 &&
            workerStart < attemptOneStart;
        const bool joined = context.schedule->workerDoneOrder.load() != 0;
        const bool noWorkerCom =
            context.schedule->workerComCalls.load() == 0;
        const bool publication = expected == CaptureStatus::Complete
            ? context.publishCalls == 1 &&
                context.schedule->retained.load() == 2
            : context.publishCalls == 0 &&
                context.sessionAbortCount == 1;
        return status == expected && ordered && joined && noWorkerCom &&
            publication;
    };
    for (size_t stress = 0; stress != 32; ++stress) {
        const bool attemptOneCompletesBeforeWorker = run(
            true, false, false, false, false, CaptureStatus::Complete);
        const bool workerCompletesBeforeAttemptOne = run(
            false, false, false, false, false, CaptureStatus::Complete);
        const bool workerFailsWhileAttemptOneRuns = run(
            true, true, false, false, false,
            CaptureStatus::IncompleteCapture);
        const bool workerFailsBeforeAttemptOne = run(
            false, true, false, false, false,
            CaptureStatus::IncompleteCapture);
        const bool attemptOneFailsWhileWorkerRuns = run(
            true, false, true, false, false,
            CaptureStatus::IncompleteCapture);
        const bool cancellationJoins = run(
            true, false, false, true, false, CaptureStatus::Cancelled);
        const bool mutationRejects = run(
            true, false, false, false, true,
            CaptureStatus::TraversalMismatch);
        if (!attemptOneCompletesBeforeWorker ||
            !workerCompletesBeforeAttemptOne ||
            !workerFailsWhileAttemptOneRuns || !workerFailsBeforeAttemptOne ||
            !attemptOneFailsWhileWorkerRuns || !cancellationJoins ||
            !mutationRejects)
            return false;
    }
    return true;
}

bool CompleteCapturePublishesOnceSmoke() {
    FakeContext context = Context();
    CaptureCoordinator coordinator;
    const CaptureStatus status = coordinator.Capture(
        Suite(),
        &context,
        Publish,
        &context,
        {});
    return status == CaptureStatus::Complete &&
        context.beginCount == 2 &&
        context.readerCalls == kQualifiedReaderCount * 2 &&
        context.releaseCount == 2 &&
        context.restoreCount == 2 &&
        context.publishCalls == 1 &&
        context.activeSerial == 1;
}

bool TerminalUnavailableStillPublishesSmoke() {
    FakeContext context = Context();
    context.attempts[0].manifest.unavailable.length = 100;
    context.attempts[1].manifest.unavailable.length = 100;
    context.attempts[0].manifest.diagnostics.length = 200;
    context.attempts[1].manifest.diagnostics.length = 200;
    CaptureCoordinator coordinator;
    return coordinator.Capture(
               Suite(),
               &context,
               Publish,
               &context,
               {}) == CaptureStatus::Complete &&
        context.activeSerial == 1;
}

bool RootMismatchPublishesNothingSmoke() {
    FakeContext context = Context();
    context.attempts[1].manifest.captureRoot = Digest(99);
    CaptureCoordinator coordinator;
    return coordinator.Capture(
               Suite(),
               &context,
               Publish,
               &context,
               {}) == CaptureStatus::TraversalMismatch &&
        context.publishCalls == 0 && context.activeSerial == 0;
}

bool AttemptMismatchDiagnosticsSmoke() {
    const FakeContext baseline = Context();
    const auto exact = [&baseline](const std::uint64_t expected,
                                   const auto& mutate) {
        AttemptResult changed = baseline.attempts[1];
        mutate(changed);
        return AttemptMismatchBits(baseline.attempts[0], changed) == expected;
    };
    const bool fields =
        exact(MismatchSemanticRoot, [](auto& value) {
            value.manifest.observedSemanticRoot = Digest(99);
        }) &&
        exact(MismatchLayoutRoot, [](auto& value) {
            value.manifest.layoutRoot = Digest(99);
        }) &&
        exact(MismatchCaptureRoot, [](auto& value) {
            value.manifest.captureRoot = Digest(99);
        }) &&
        exact(MismatchSemanticCertified, [](auto& value) {
            value.manifest.semanticCertified = false;
        }) &&
        exact(MismatchLayoutPresent, [](auto& value) {
            value.manifest.layoutPresent = false;
        }) &&
        exact(MismatchClosedProfiles, [](auto& value) {
            value.manifest.closedProfileBits ^= 1;
        }) &&
        exact(MismatchIntegrity, [](auto& value) {
            value.manifest.integrity = CaptureIntegrity::TraversalFailure;
        }) &&
        exact(MismatchCoverage, [](auto& value) {
            value.manifest.coverage.length++;
        }) &&
        exact(MismatchUnavailable, [](auto& value) {
            value.manifest.unavailable.digest = Digest(99);
        }) &&
        exact(MismatchDiagnostics, [](auto& value) {
            value.manifest.diagnostics.length++;
        }) &&
        exact(MismatchLayoutEnvironment, [](auto& value) {
            value.layoutEnvironment.push_back(0xff);
        }) &&
        exact(MismatchObservedControlCount, [](auto& value) {
            value.observedControlCount++;
        }) &&
        exact(MismatchLegacyHwpmlDiagnostic, [](auto& value) {
            value.legacyHwpmlDiagnosticFailed = true;
        }) &&
        exact(1413, [](auto& value) {
            // Authoritative cv_c capture: a volatile CharacterRun.102
            // reference closure changed semantic/capture roots, coverage,
            // unavailable facts, and layout environment in one replay.
            value.manifest.observedSemanticRoot = Digest(91);
            value.manifest.captureRoot = Digest(92);
            value.manifest.coverage.digest = Digest(93);
            value.manifest.unavailable.digest = Digest(94);
            value.layoutEnvironment.push_back(0xff);
        });
    FakeContext context = baseline;
    context.attempts[1].manifest.captureRoot = Digest(99);
    CaptureDiagnostics diagnostics;
    CaptureCoordinator coordinator;
    const CaptureStatus status = coordinator.Capture(
        Suite(), &context, Publish, &context, {}, &diagnostics);
    return fields && status == CaptureStatus::TraversalMismatch &&
        diagnostics.stage == FailureStage::CompareAttempts &&
        diagnostics.attempt == 1 && diagnostics.reader == 0 &&
        diagnostics.mismatchBits == MismatchCaptureRoot;
}

bool ReaderFailureCleansAndPublishesNothingSmoke() {
    FakeContext context = Context();
    context.readerFails = true;
    CaptureCoordinator coordinator;
    return coordinator.Capture(
               Suite(),
               &context,
               Publish,
               &context,
               {}) == CaptureStatus::IncompleteCapture &&
        context.releaseCount == 1 &&
        context.restoreCount == 1 &&
        context.publishCalls == 0;
}

bool StateChangePublishesNothingSmoke() {
    FakeContext context = Context();
    context.mutateRestoredState = true;
    CaptureCoordinator coordinator;
    return coordinator.Capture(
               Suite(),
               &context,
               Publish,
               &context,
               {}) == CaptureStatus::StateChanged &&
        context.publishCalls == 0 && context.activeSerial == 0;
}

bool CancellationPublishesNothingSmoke() {
    FakeContext context = Context();
    context.cancelAfterFirstReader = true;
    CaptureCoordinator coordinator;
    return coordinator.Capture(
               Suite(),
               &context,
               Publish,
               &context,
               {}) == CaptureStatus::Cancelled &&
        context.readerCalls == 1 && context.releaseCount == 1 &&
        context.restoreCount == 1 && context.sessionAbortCount == 1 &&
        context.sessionCommitReadyCount == 0 && context.publishCalls == 0 &&
        context.activeSerial == 0;
}

bool FailureInjectionMatrixSmoke() {
    const FailurePoint points[] = {
        FailurePoint::BeforeBaseline,
        FailurePoint::AfterBaseline,
        FailurePoint::BeforeAttempt,
        FailurePoint::BeforeReader,
        FailurePoint::AfterReader,
        FailurePoint::BeforeScanRelease,
        FailurePoint::AfterScanRelease,
        FailurePoint::BeforeRestore,
        FailurePoint::AfterRestore,
        FailurePoint::BeforeComparison,
        FailurePoint::AfterComparison,
        FailurePoint::BeforePublish,
    };
    for (const FailurePoint point : points) {
        const bool readerScoped =
            point == FailurePoint::BeforeReader ||
            point == FailurePoint::AfterReader;
        const bool attemptScoped = readerScoped ||
            point == FailurePoint::BeforeAttempt ||
            point == FailurePoint::BeforeScanRelease ||
            point == FailurePoint::AfterScanRelease ||
            point == FailurePoint::BeforeRestore ||
            point == FailurePoint::AfterRestore;
        const size_t attemptCount = attemptScoped ? 2 : 1;
        const size_t readerCount =
            readerScoped ? kQualifiedReaderCount : 1;
        for (size_t attempt = 0; attempt < attemptCount; ++attempt) {
            for (size_t reader = 0; reader < readerCount; ++reader) {
                FakeContext context = Context();
                CaptureCoordinator coordinator;
                const FailureInjection injection{
                    point,
                    attempt,
                    reader,
                };
                const CaptureStatus status = coordinator.Capture(
                    Suite(),
                    &context,
                    Publish,
                    &context,
                    injection);
                if (status == CaptureStatus::Complete ||
                    context.publishCalls != 0 ||
                    context.activeSerial != 0) {
                    return false;
                }
            }
        }
    }
    return true;
}

bool AtomicSessionLifecycleAndFreshRetrySmoke() {
    FakeContext stale = Context();
    stale.beginSessionFails = true;
    CaptureCoordinator coordinator;
    const CaptureStatus staleStatus = coordinator.Capture(
        Suite(), &stale, Publish, &stale, {});
    const bool staleRejected =
        staleStatus == CaptureStatus::IncompleteCapture &&
        stale.sessionBeginCount == 1 && stale.sessionAbortCount == 1 &&
        !stale.sessionActive && !stale.partialIdentityState &&
        stale.publishCalls == 0 && stale.activeSerial == 0;

    FakeContext failed = Context();
    failed.beginAttemptFails = true;
    const CaptureStatus failedStatus = coordinator.Capture(
        Suite(), &failed, Publish, &failed, {});
    const bool failedClosed =
        failedStatus == CaptureStatus::IncompleteCapture &&
        failed.sessionBeginCount == 1 && failed.sessionAbortCount == 1 &&
        failed.sessionCommitReadyCount == 0 && !failed.sessionActive &&
        !failed.partialIdentityState && failed.releaseCount == 1 &&
        failed.restoreCount == 1 && failed.publishCalls == 0 &&
        failed.activeSerial == 0;

    failed.beginAttemptFails = false;
    const CaptureStatus retryStatus = coordinator.Capture(
        Suite(), &failed, Publish, &failed, {});
    const bool freshRetry = retryStatus == CaptureStatus::Complete &&
        failed.sessionBeginCount == 2 && failed.sessionAbortCount == 1 &&
        failed.sessionCommitReadyCount == 1 && !failed.sessionActive &&
        !failed.partialIdentityState && failed.sessionSerials.size() == 2 &&
        failed.sessionSerials[0] != failed.sessionSerials[1] &&
        failed.sessionSerials[0] < failed.sessionSerials[1] &&
        failed.publishCalls == 1 && failed.activeSerial == 1;

    FakeContext cleanupFailure = Context();
    cleanupFailure.sessionCleanupFails = true;
    const CaptureStatus cleanupStatus = coordinator.Capture(
        Suite(), &cleanupFailure, Publish, &cleanupFailure, {});
    const bool cleanupBlocksCommit =
        cleanupStatus == CaptureStatus::CleanupFailed &&
        cleanupFailure.sessionCommitReadyCount == 1 &&
        cleanupFailure.sessionAbortCount == 1 &&
        cleanupFailure.publishCalls == 0 && cleanupFailure.activeSerial == 0 &&
        !cleanupFailure.sessionActive && !cleanupFailure.partialIdentityState;
    return staleRejected && failedClosed && freshRetry && cleanupBlocksCommit;
}

bool PublishFailureDoesNotAdvanceSmoke() {
    FakeContext context = Context();
    context.publishFails = true;
    CaptureCoordinator coordinator;
    return coordinator.Capture(
               Suite(),
               &context,
               Publish,
               &context,
               {}) == CaptureStatus::PublishFailed &&
        context.publishCalls == 1 && context.activeSerial == 0 &&
        context.sessionAbortCount == 1 &&
        context.sessionCommitReadyCount == 1 &&
        !context.sessionActive && !context.partialIdentityState;
}

bool StoreRejectsMalformedCandidateWithoutExposureSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-capture-smoke-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    GraphStore store(root.wstring());
    if (!store.Initialize()) {
        return false;
    }
    FakeContext context = Context();
    CaptureCoordinator coordinator;
    const CaptureStatus status = coordinator.CaptureToStore(
        Suite(),
        &context,
        &store,
        {});
    const bool passed = status == CaptureStatus::PublishFailed &&
        store.ActiveSerial() == 0 && store.PinActive() == nullptr;
    std::filesystem::remove_all(root, error);
    return passed;
}

bool RealSpoolsPublishAtomicallySmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-capture-positive-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> payloads(7);
    for (size_t index = 0; index < payloads.size(); ++index) {
        payloads[index].reader =
            static_cast<hancom::graph::capture::QualifiedReader>(index);
        payloads[index].outcome =
            hancom::graph::capture::ReaderOutcome::Complete;
        payloads[index].coverage = hancom::graph::CoverageState::Complete;
    }
    payloads[0].bodyList = {
        hancom::graph::ObservationState::Value, true, 0, {}, 812};
    payloads[0].sections.push_back({0, {0, 0, 0}});
    hancom::graph::capture::ParagraphObservation paragraph;
    paragraph.start = {0, 0, 0};
    paragraph.runs.push_back({{0, 0, 0}, {0, 0, 4}, L"text"});
    payloads[1].paragraphs.push_back(std::move(paragraph));
    AddCharacterShapeTerminal(&payloads, L"0:0:0:4");
    payloads[6].layoutEnvironment = NormativeEnvironment();
    for (const hancom::graph::PropertyKeyId key : {12000U, 12001U}) {
        hancom::graph::capture::PropertyObservation layout;
        layout.target = hancom::graph::capture::PropertyTarget::Paragraph;
        layout.targetIdentity = L"0:0";
        layout.ownerField = 10;
        layout.key = key;
        layout.scalar = hancom::graph::ScalarTag::Uint64;
        layout.state = hancom::graph::ObservationState::Value;
        layout.origin = hancom::graph::PropertyOrigin::Generated;
        layout.integerValue = 1;
        payloads[6].layoutProperties.push_back(std::move(layout));
    }
    payloads[6].layoutObservedKindCounts[0] = 1;
    payloads[6].layoutPerKindComplete =
        payloads[6].layoutProperties.size() == 2;
    CaptureSpool first;
    CaptureSpool second;
    hancom::graph::capture::CaptureIdentityArena arena;
    const std::vector<std::uint8_t> environment = NormativeEnvironment();
    const bool firstBuilt =
        first.Build(root / L"attempt-0", payloads, environment, arena);
    const bool secondBuilt =
        second.Build(root / L"attempt-1", payloads, environment, arena);
    if (!firstBuilt || !secondBuilt) {
        std::filesystem::remove_all(root, error);
        return false;
    }
    FakeContext context = Context();
    context.attempts[0] = first.Result();
    context.attempts[1] = second.Result();
    bool passed = false;
    {
        GraphStore store((root / L"store").wstring());
        CaptureCoordinator coordinator;
        passed = store.Initialize() &&
            coordinator.CaptureToStore(
                Suite(),
                &context,
                &store,
                {}) == CaptureStatus::Complete &&
            store.ActiveSerial() == 1 &&
            store.PinActive() != nullptr;
    }
    first.Reset();
    second.Reset();
    std::filesystem::remove_all(root, error);
    return passed;
}

bool CurrentIncompleteTypedStreamRejectedSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-capture-negative-current-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> payloads(7);
    for (size_t index = 0; index < payloads.size(); ++index) {
        payloads[index].reader =
            static_cast<hancom::graph::capture::QualifiedReader>(index);
        payloads[index].outcome =
            hancom::graph::capture::ReaderOutcome::Complete;
        payloads[index].coverage = hancom::graph::CoverageState::Complete;
    }
    payloads[0].sections.push_back({0, {0, 0, 0}});
    hancom::graph::capture::ParagraphObservation paragraph;
    paragraph.start = {0, 0, 0};
    paragraph.runs.push_back({{0, 0, 0}, {0, 0, 4}, L"text"});
    payloads[1].paragraphs.push_back(std::move(paragraph));
    // This models an opaque legacy result that rendered text but produced
    // no typed table observations. The production boundary must reject the
    // explicit incomplete outcome rather than parse or trust that text.
    payloads[4].outcome =
        hancom::graph::capture::ReaderOutcome::Inconclusive;
    payloads[4].coverage = hancom::graph::CoverageState::NotExposed;
    hancom::graph::capture::DiagnosticObservation diagnostic;
    diagnostic.target =
        hancom::graph::capture::PropertyTarget::Document;
    diagnostic.targetIdentity = L"document";
    diagnostic.code = hancom::graph::DiagnosticCode::NativeReadFailure;
    diagnostic.detail =
        L"opaque result contained no typed table observations";
    payloads[4].diagnostics.push_back(std::move(diagnostic));
    payloads[6].layoutEnvironment = NormativeEnvironment();
    CaptureSpool spool;
    hancom::graph::capture::CaptureIdentityArena arena;
    const std::vector<std::uint8_t> environment = NormativeEnvironment();
    const bool rejected =
        !spool.Build(root / L"attempt-0", payloads, environment, arena) &&
        !std::filesystem::exists(root / L"attempt-0" / L"records.hgn");
    spool.Reset();
    std::filesystem::remove_all(root, error);
    return rejected;
}

namespace sealed {

// Byte-level decode of a sealed generation's LogicalRecordV1 stream. Every
// check below derives population from the decoded records themselves, never
// from reader input, manifests alone, or log strings.
std::uint16_t R16(const std::uint8_t* const bytes) noexcept {
    return static_cast<std::uint16_t>(
        bytes[0] | (static_cast<unsigned>(bytes[1]) << 8));
}

std::uint64_t R64(const std::uint8_t* const bytes) noexcept {
    std::uint64_t value = 0;
    for (size_t index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(bytes[index]) << (index * 8);
    }
    return value;
}

struct DecodedField final {
    std::uint16_t tag = 0;
    std::uint16_t flags = 0;
    std::uint16_t scalar = 0;
    std::uint64_t elementCount = 0;
    std::vector<std::uint8_t> value{};
};

struct DecodedRecord final {
    std::uint16_t kind = 0;
    std::uint16_t fieldCount = 0;
    std::uint64_t id = 0;
    std::vector<DecodedField> fields{};
};

bool ParseFieldStream(
    const std::uint8_t* const data,
    const size_t size,
    std::vector<DecodedField>* const fields) {
    size_t offset = 0;
    while (offset != size) {
        if (size - offset < 24) {
            return false;
        }
        DecodedField field;
        field.tag = R16(data + offset);
        field.flags = R16(data + offset + 2);
        field.scalar = R16(data + offset + 4);
        field.elementCount = R64(data + offset + 8);
        const std::uint64_t valueBytes = R64(data + offset + 16);
        if (valueBytes > size - offset - 24) {
            return false;
        }
        const size_t count = static_cast<size_t>(valueBytes);
        field.value.assign(data + offset + 24, data + offset + 24 + count);
        fields->push_back(std::move(field));
        offset += 24 + count;
    }
    return true;
}

const DecodedField* FindField(
    const std::vector<DecodedField>& fields,
    const std::uint16_t tag) noexcept {
    for (const DecodedField& field : fields) {
        if (field.tag == tag) {
            return &field;
        }
    }
    return nullptr;
}

bool ReadWholeFile(
    const std::filesystem::path& path,
    std::vector<std::uint8_t>* const bytes) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream) {
        return false;
    }
    const std::streamoff size = stream.tellg();
    if (size < 0) {
        return false;
    }
    bytes->resize(static_cast<size_t>(size));
    if (bytes->empty()) {
        return true;
    }
    stream.seekg(0);
    return static_cast<bool>(
        stream.read(reinterpret_cast<char*>(bytes->data()), size));
}

bool DecodeRecords(
    const std::vector<std::uint8_t>& bytes,
    std::vector<DecodedRecord>* const records,
    size_t* const decodedBytes = nullptr) {
    size_t offset = 0;
    while (offset != bytes.size()) {
        if (bytes.size() - offset < 24) {
            return false;
        }
        const std::uint8_t* const header = bytes.data() + offset;
        DecodedRecord record;
        record.kind = R16(header);
        record.fieldCount = R16(header + 6);
        record.id = R64(header + 8);
        const std::uint64_t payload = R64(header + 16);
        if (payload > bytes.size() - offset - 24) {
            return false;
        }
        if (!ParseFieldStream(
                header + 24, static_cast<size_t>(payload), &record.fields) ||
            record.fields.size() != record.fieldCount) {
            return false;
        }
        records->push_back(std::move(record));
        offset += 24 + static_cast<size_t>(payload);
    }
    if (decodedBytes != nullptr) *decodedBytes = offset;
    return !records->empty() && offset == bytes.size();
}

bool HasPhysicalOwnerBlockGrammar(
    const std::vector<DecodedRecord>& records) {
    std::array<std::uint8_t, 16> currentOwner{};
    bool ownerPresent = false;
    bool currentDocument = false;
    bool receiptPhase = false;
    bool sawRemap = false;
    unsigned phase = 0;
    for (size_t index = 0; index < records.size(); ++index) {
        const DecodedRecord& record = records[index];
        if (record.id != index) {
            return false;
        }
        const auto kind = static_cast<hancom::graph::RecordKind>(record.kind);
        if (kind == hancom::graph::RecordKind::Manifest) {
            if (index != 0) return false;
            continue;
        }
        if (kind == hancom::graph::RecordKind::Tombstone ||
            kind == hancom::graph::RecordKind::Remap) {
            if (!ownerPresent ||
                (kind == hancom::graph::RecordKind::Tombstone && sawRemap)) {
                return false;
            }
            receiptPhase = true;
            sawRemap = sawRemap ||
                kind == hancom::graph::RecordKind::Remap;
            continue;
        }
        if (receiptPhase) return false;
        if (kind == hancom::graph::RecordKind::Node) {
            const DecodedField* const commonField = FindField(record.fields, 1);
            std::vector<DecodedField> common;
            if (commonField == nullptr ||
                !ParseFieldStream(commonField->value.data(),
                                  commonField->value.size(), &common)) {
                return false;
            }
            const DecodedField* const id = FindField(common, 1);
            const DecodedField* const nodeKind = FindField(common, 2);
            if (id == nullptr || id->value.size() != currentOwner.size() ||
                nodeKind == nullptr || nodeKind->value.size() != 2) {
                return false;
            }
            std::copy_n(id->value.begin(), currentOwner.size(),
                        currentOwner.begin());
            currentDocument = R16(nodeKind->value.data()) ==
                static_cast<std::uint16_t>(
                    hancom::graph::NodeKind::Document);
            ownerPresent = true;
            phase = 0;
            continue;
        }
        if (!ownerPresent) return false;
        const unsigned wanted =
            kind == hancom::graph::RecordKind::Property ? 0U :
            kind == hancom::graph::RecordKind::Coverage ? 1U :
            kind == hancom::graph::RecordKind::Diagnostic ? 2U :
            kind == hancom::graph::RecordKind::Edge ? 3U : 4U;
        if (wanted == 4U || wanted < phase) return false;
        phase = wanted;
        const std::uint16_t ownerTag =
            kind == hancom::graph::RecordKind::Diagnostic ? 3U :
            kind == hancom::graph::RecordKind::Edge ? 2U : 1U;
        const DecodedField* const owner =
            FindField(record.fields, ownerTag);
        if (kind == hancom::graph::RecordKind::Coverage &&
            owner == nullptr) {
            const DecodedField* const ownerKind =
                FindField(record.fields, 6);
            if (!currentDocument || ownerKind == nullptr ||
                ownerKind->value.size() != 2 ||
                (R16(ownerKind->value.data()) !=
                     static_cast<std::uint16_t>(
                         hancom::graph::RecordKind::Manifest) &&
                 R16(ownerKind->value.data()) !=
                     static_cast<std::uint16_t>(
                         hancom::graph::RecordKind::Coverage))) {
                return false;
            }
        } else if (owner == nullptr ||
                   owner->value.size() != currentOwner.size() ||
                   !std::equal(owner->value.begin(), owner->value.end(),
                               currentOwner.begin())) {
            return false;
        }
    }
    return ownerPresent;
}

bool RejectsDocumentAggregatedPhysicalOrder(
    const std::vector<DecodedRecord>& records) {
    std::vector<DecodedRecord> prior = records;
    const auto secondNode = std::find_if(
        std::next(prior.begin()), prior.end(), [](const DecodedRecord& record) {
            return record.kind == static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Node);
        });
    if (secondNode == prior.end()) return false;
    const auto coverage = std::find_if(
        secondNode, prior.end(), [](const DecodedRecord& record) {
            return record.kind == static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Coverage);
        });
    if (coverage == prior.end()) return false;
    DecodedRecord moved = *coverage;
    prior.erase(coverage);
    const auto insertion = std::find_if(
        std::next(prior.begin()), prior.end(), [](const DecodedRecord& record) {
            return record.kind == static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Node);
        });
    prior.insert(insertion, std::move(moved));
    return !HasPhysicalOwnerBlockGrammar(prior);
}

using NodeIdBytes = std::array<std::uint8_t, 16>;

struct Population final {
    std::uint64_t manifests = 0;
    std::uint64_t nodes = 0;
    std::uint64_t edges = 0;
    std::uint64_t properties = 0;
    std::uint64_t coverageRecords = 0;
    std::uint64_t containsFromDocument = 0;
    std::array<std::uint64_t, 16> nodeKindCounts{};
    std::vector<NodeIdBytes> nodeIds{};
    bool decodedNodeEnvelopes = true;
    bool allNodeIdsRfc4122V4 = true;
    bool documentPresent = false;
    bool documentFingerprintNonzero = false;
    bool documentLocatorNotApplicable = false;
    bool imageBinaryNotExposed = false;
    bool readFailedPropertyPresent = false;
};

std::uint64_t KindCount(
    const Population& population,
    const hancom::graph::NodeKind kind) noexcept {
    return population.nodeKindCounts[static_cast<size_t>(kind)];
}

bool NonZero(const std::uint8_t* const bytes, const size_t count) noexcept {
    for (size_t index = 0; index < count; ++index) {
        if (bytes[index] != 0) {
            return true;
        }
    }
    return false;
}

Population Analyze(const std::vector<DecodedRecord>& records) {
    using hancom::graph::EdgeKind;
    using hancom::graph::NodeKind;
    using hancom::graph::ObservationState;
    using hancom::graph::RecordKind;
    Population population;
    NodeIdBytes documentId{};
    for (const DecodedRecord& record : records) {
        const auto kind = static_cast<RecordKind>(record.kind);
        if (kind == RecordKind::Manifest) {
            ++population.manifests;
        } else if (kind == RecordKind::Node) {
            ++population.nodes;
            const DecodedField* const commonField =
                FindField(record.fields, 1);
            const DecodedField* const payloadField =
                FindField(record.fields, 2);
            std::vector<DecodedField> common;
            std::vector<DecodedField> payload;
            if (commonField == nullptr || payloadField == nullptr ||
                !ParseFieldStream(
                    commonField->value.data(),
                    commonField->value.size(),
                    &common) ||
                !ParseFieldStream(
                    payloadField->value.data(),
                    payloadField->value.size(),
                    &payload)) {
                population.decodedNodeEnvelopes = false;
                continue;
            }
            const DecodedField* const idField = FindField(common, 1);
            const DecodedField* const kindField = FindField(common, 2);
            if (idField == nullptr || idField->value.size() != 16 ||
                kindField == nullptr || kindField->value.size() != 2) {
                population.decodedNodeEnvelopes = false;
                continue;
            }
            hancom::graph::Uuid128 nodeId;
            std::copy_n(
                idField->value.begin(),
                nodeId.bytes.size(),
                nodeId.bytes.begin());
            population.nodeIds.push_back(nodeId.bytes);
            if (!hancom::graph::identity::IsRfc4122V4(nodeId)) {
                population.allNodeIdsRfc4122V4 = false;
            }
            const std::uint16_t nodeKind = R16(kindField->value.data());
            if (nodeKind < population.nodeKindCounts.size()) {
                ++population.nodeKindCounts[nodeKind];
            }
            if (nodeKind ==
                static_cast<std::uint16_t>(NodeKind::Document)) {
                population.documentPresent = true;
                documentId = nodeId.bytes;
                const DecodedField* const fingerprint = FindField(common, 8);
                population.documentFingerprintNonzero =
                    fingerprint != nullptr &&
                    fingerprint->value.size() == 32 &&
                    NonZero(
                        fingerprint->value.data(),
                        fingerprint->value.size());
                const DecodedField* const locator = FindField(common, 7);
                population.documentLocatorNotApplicable =
                    locator != nullptr && !locator->value.empty() &&
                    locator->value[0] == static_cast<std::uint8_t>(
                        ObservationState::NotApplicable);
            }
            if (nodeKind == static_cast<std::uint16_t>(NodeKind::Image)) {
                const DecodedField* const binary = FindField(payload, 110);
                if (binary != nullptr && !binary->value.empty() &&
                    binary->value[0] ==
                        static_cast<std::uint8_t>(
                            ObservationState::NotExposed)) {
                    population.imageBinaryNotExposed = true;
                }
            }
        } else if (kind == RecordKind::Edge) {
            ++population.edges;
            const DecodedField* const edgeKind = FindField(record.fields, 1);
            const DecodedField* const source = FindField(record.fields, 2);
            if (edgeKind != nullptr && edgeKind->value.size() == 2 &&
                R16(edgeKind->value.data()) ==
                    static_cast<std::uint16_t>(EdgeKind::Contains) &&
                source != nullptr && source->value.size() == 16 &&
                std::equal(
                    source->value.begin(),
                    source->value.end(),
                    documentId.begin(),
                    documentId.end())) {
                ++population.containsFromDocument;
            }
        } else if (kind == RecordKind::Property) {
            ++population.properties;
            const DecodedField* const observation =
                FindField(record.fields, 4);
            if (observation != nullptr && !observation->value.empty() &&
                observation->value[0] ==
                    static_cast<std::uint8_t>(
                        ObservationState::ReadFailed)) {
                population.readFailedPropertyPresent = true;
            }
        } else if (kind == RecordKind::Coverage) {
            ++population.coverageRecords;
        }
    }
    return population;
}

// Strict fixture: two sections, a native control spine whose delegated
// tbl/$pic entries carry native HeadCtrl ordinals (3, 7, 9) plus one
// generic control (5), a 6x6 outer table with its 28 physical owner cells
// (merges A1(1,2), D1(2,1), A3(2,2), E5(2,2)), a nested table hosted in
// D3's cell story, and one image. Fixture-specific counts live only here.
std::vector<ReaderPayload> SealedPayloads() {
    using namespace hancom::graph::capture;
    std::vector<ReaderPayload> payloads(7);
    for (size_t index = 0; index < payloads.size(); ++index) {
        payloads[index].reader = static_cast<QualifiedReader>(index);
        payloads[index].outcome = ReaderOutcome::Complete;
        payloads[index].coverage = hancom::graph::CoverageState::Complete;
    }

    payloads[0].bodyList = {
        hancom::graph::ObservationState::Value, true, 0, {}, 947};
    payloads[0].sections = {
        {0, {0, 0, 0}},
        {1, {0, 2, 0}},
    };
    payloads[0].controls = {
        {L"tbl", L"tbl-outer", 3, {0, 0, 5}, true},
        {L"form", L"form-5", 5, {0, 0, 8}, false},
        {L"tbl", L"tbl-nested", 7, {203, 0, 0}, true},
        {L"$pic", L"img-4633", 9, {0, 0, 12}, false},
    };
    payloads[3].controls = payloads[0].controls;

    const auto paragraph = [](const std::uint64_t section,
                              const std::int64_t number,
                              const wchar_t* const text) {
        ParagraphObservation result;
        result.sectionOrdinal = section;
        result.start = {0, number, 0};
        const std::int64_t length =
            static_cast<std::int64_t>(std::wstring(text).size());
        result.runs.push_back(
            {{0, number, 0}, {0, number, length}, text});
        return result;
    };
    payloads[1].paragraphs = {
        paragraph(0, 0, L"Hello world."),
        paragraph(0, 1, L"Second paragraph text."),
        paragraph(1, 2, L"Table and image paragraph."),
    };

    PropertyObservation face;
    face.target = PropertyTarget::Run;
    face.ownerField = 102;
    face.key = 1000;
    face.scalar = hancom::graph::ScalarTag::UTF16;
    face.state = hancom::graph::ObservationState::Value;
    face.origin = hancom::graph::PropertyOrigin::Unknown;
    face.textValue = L"Hancom Gothic";
    PropertyObservation failed = face;
    failed.key = 1007;
    failed.scalar = hancom::graph::ScalarTag::HWPUNIT64;
    failed.state = hancom::graph::ObservationState::ReadFailed;
    failed.origin = hancom::graph::PropertyOrigin::Unavailable;
    failed.textValue.clear();
    DefinitionObservation characterShape;
    characterShape.kind = hancom::graph::DefinitionKind::CharacterShape;
    characterShape.nativeIdState =
        hancom::graph::ObservationState::NotExposed;
    characterShape.bodyState = hancom::graph::ObservationState::Value;
    const hancom::graph::codec::SemanticPropertyInput faceSemantic{
        1000, hancom::graph::PropertyOrigin::Unknown,
        hancom::graph::codec::Utf16(
            reinterpret_cast<const std::uint16_t*>(L"Hancom Gothic"), 13)};
    characterShape.propertyDigest =
        hancom::graph::codec::DefinitionPropertyAggregate({faceSemantic});
    std::wostringstream characterIdentity;
    characterIdentity << static_cast<unsigned>(characterShape.kind)
                      << L":absent:2:" << std::hex << std::setfill(L'0');
    for (const std::uint8_t byte : characterShape.propertyDigest.bytes) {
        characterIdentity << std::setw(2) << static_cast<unsigned>(byte);
    }
    characterShape.identity = characterIdentity.str();
    PropertyObservation definitionFace = face;
    definitionFace.target = PropertyTarget::Definition;
    definitionFace.targetIdentity = characterShape.identity;
    characterShape.properties.push_back(std::move(definitionFace));
    payloads[2].definitions.push_back(characterShape);
    const auto completeCharacterShape = [&payloads](
        const std::wstring& identity) {
        CoverageObservation coverage;
        coverage.target = PropertyTarget::Run;
        coverage.targetIdentity = identity;
        coverage.coordinate =
            hancom::graph::CoverageCoordinateKind::NodeField;
        coverage.ownerField = 102;
        coverage.detail = L"CharacterShapeReferenceClosure";
        coverage.profile = hancom::graph::ProfileId::EditableText;
        coverage.state = hancom::graph::CoverageState::Complete;
        payloads[2].coverageFacts.push_back(std::move(coverage));
    };
    for (const ParagraphObservation& observedParagraph :
         payloads[1].paragraphs) {
        for (const RunObservation& run : observedParagraph.runs) {
            const std::wstring identity =
                std::to_wstring(run.start.list) + L":" +
                std::to_wstring(run.start.paragraph) + L":" +
                std::to_wstring(run.start.character) + L":" +
                std::to_wstring(run.end.character);
            PropertyObservation runFace = face;
            runFace.targetIdentity = identity;
            PropertyObservation runFailed = failed;
            runFailed.targetIdentity = identity;
            payloads[2].properties.push_back(std::move(runFace));
            payloads[2].properties.push_back(std::move(runFailed));
            payloads[2].definitionReferences.push_back({
                PropertyTarget::Run, identity,
                hancom::graph::EdgeKind::CharacterShapeRef,
                characterShape.identity, 0});
            completeCharacterShape(identity);
        }
    }
    payloads[2].coverage = hancom::graph::CoverageState::ReadFailed;

    const auto cell = [](const wchar_t* const address,
                         const std::int64_t list) {
        CellObservation result;
        result.address = address;
        result.listId = list;
        const wchar_t column = result.address[0];
        result.column1 = static_cast<std::uint64_t>(column - L'A' + 1);
        result.row1 = static_cast<std::uint64_t>(result.address[1] - L'0');
        if (result.address == L"A1") result.columnSpan = 2;
        if (result.address == L"D1") result.rowSpan = 2;
        if (result.address == L"A3" || result.address == L"E5") {
            result.rowSpan = 2;
            result.columnSpan = 2;
        }
        result.width = {hancom::graph::ObservationState::Value, 7200};
        result.height = {hancom::graph::ObservationState::Value, 3600};
        result.pageStart = {hancom::graph::ObservationState::Value, 1};
        result.pageEnd = {hancom::graph::ObservationState::Value, 1};
        result.text.state = hancom::graph::ObservationState::Value;
        result.text.value = std::wstring(L"cell ") + result.address;
        ParagraphObservation paragraph;
        paragraph.start = {list, 0, 0};
        paragraph.runs.push_back({
            paragraph.start,
            {list, 0, static_cast<std::int64_t>(result.text.value.size())},
            result.text.value});
        result.paragraphs.push_back(std::move(paragraph));
        return result;
    };
    TableObservation outer;
    outer.instanceId = L"tbl-outer";
    outer.headCtrlOrdinal = 3;
    outer.anchor = {0, 0, 5};
    outer.rowCount = 6;
    outer.columnCount = 6;
    const std::array<const wchar_t*, 28> outerAddresses{
        L"A1",L"C1",L"D1",L"E1",L"F1",L"A2",L"B2",L"C2",
        L"E2",L"F2",L"A3",L"C3",L"D3",L"E3",L"F3",L"C4",
        L"D4",L"E4",L"F4",L"A5",L"B5",L"C5",L"D5",L"E5",
        L"A6",L"B6",L"C6",L"D6"};
    for (size_t index = 0; index < outerAddresses.size(); ++index) {
        outer.cells.push_back(cell(
            outerAddresses[index],
            outerAddresses[index] == std::wstring(L"D3")
                ? 203
                : static_cast<std::int64_t>(100 + index)));
    }
    TableObservation nested;
    nested.instanceId = L"tbl-nested";
    nested.headCtrlOrdinal = 7;
    nested.anchor = {203, 0, 0};
    nested.rowCount = 2;
    nested.columnCount = 2;
    nested.hostTableInstanceId = L"tbl-outer";
    nested.hostTableInstanceIdPresent = true;
    nested.hostCellAddress = L"D3";
    nested.cells = {
        cell(L"A1", 301), cell(L"B1", 302),
        cell(L"A2", 303), cell(L"B2", 304),
    };
    // The nested A1 is not merged; the helper's outer-table merge rule is
    // corrected here because physical cells never expand a slot grid.
    nested.cells[0].columnSpan = 1;
    payloads[4].tables = {outer, nested};
    for (const TableObservation& observedTable : payloads[4].tables) {
        for (const CellObservation& observedCell : observedTable.cells) {
            for (const ParagraphObservation& observedParagraph :
                 observedCell.paragraphs) {
                for (const RunObservation& run : observedParagraph.runs) {
                    const std::wstring identity =
                        std::to_wstring(run.start.list) + L":" +
                        std::to_wstring(run.start.paragraph) + L":" +
                        std::to_wstring(run.start.character) + L":" +
                        std::to_wstring(run.end.character);
                    PropertyObservation runFace = face;
                    runFace.targetIdentity = identity;
                    PropertyObservation runFailed = failed;
                    runFailed.targetIdentity = identity;
                    payloads[2].properties.push_back(std::move(runFace));
                    payloads[2].properties.push_back(std::move(runFailed));
                    payloads[2].definitionReferences.push_back({
                        PropertyTarget::Run, identity,
                        hancom::graph::EdgeKind::CharacterShapeRef,
                        characterShape.identity, 0});
                    completeCharacterShape(identity);
                }
            }
        }
    }

    ImageObservation image;
    image.instanceId = L"img-4633";
    image.ctrlId = L"$pic";
    image.headCtrlOrdinal = 9;
    image.anchor = {0, 0, 12};
    for (auto& scalar : image.scalar) {
        scalar.state = hancom::graph::ObservationState::NotExposed;
    }
    for (auto& text : image.text) {
        text.state = hancom::graph::ObservationState::NotExposed;
    }
    image.asset.binaryState = hancom::graph::ObservationState::NotExposed;
    image.asset.storageState = hancom::graph::ObservationState::NotExposed;
    payloads[5].images = {std::move(image)};

    const auto layoutProperty = [](const PropertyTarget target,
                                   std::wstring identity,
                                   const hancom::graph::PropertyKeyId key,
                                   const std::int64_t value,
                                   const hancom::graph::ObservationState state =
                                       hancom::graph::ObservationState::Value) {
        PropertyObservation property;
        property.target = target;
        property.targetIdentity = std::move(identity);
        property.ownerField = 10;
        property.key = key;
        property.scalar = key <= 12001
            ? hancom::graph::ScalarTag::Uint64
            : hancom::graph::ScalarTag::HWPUNIT64;
        property.state = state;
        property.origin = state == hancom::graph::ObservationState::Value
            ? hancom::graph::PropertyOrigin::Generated
            : hancom::graph::PropertyOrigin::Unavailable;
        property.integerValue = value;
        return property;
    };
    const auto addSpan = [&payloads, &layoutProperty](
        const PropertyTarget target, std::wstring identity,
        const std::int64_t first, const std::int64_t last) {
        payloads[6].layoutProperties.push_back(
            layoutProperty(target, identity, 12000, first));
        payloads[6].layoutProperties.push_back(
            layoutProperty(target, std::move(identity), 12001, last));
    };
    const auto addControlGeometry = [&payloads, &layoutProperty](
        const PropertyTarget target, std::wstring identity,
        const std::int64_t width, const std::int64_t height,
        const hancom::graph::ObservationState state =
            hancom::graph::ObservationState::Value) {
        payloads[6].layoutProperties.push_back(
            layoutProperty(target, identity, 12002, width, state));
        payloads[6].layoutProperties.push_back(
            layoutProperty(target, std::move(identity), 12003, height, state));
    };
    payloads[6].layoutEnvironment = NormativeEnvironment();
    for (const ParagraphObservation& observed : payloads[1].paragraphs) {
        addSpan(PropertyTarget::Paragraph,
                std::to_wstring(observed.start.list) + L":" +
                    std::to_wstring(observed.start.paragraph),
                observed.sectionOrdinal + 1, observed.sectionOrdinal + 1);
    }
    for (const ControlObservation& observed : payloads[3].controls) {
        if (observed.ctrlId == L"tbl" || observed.ctrlId == L"$pic") continue;
        const std::wstring identity = CanonicalNativeSiteIdentity(
            PropertyTarget::Control, observed.ctrlId,
            observed.headCtrlOrdinal, observed.anchor,
            observed.instanceIdPresent, observed.instanceId);
        addSpan(PropertyTarget::Control, identity, 1, 1);
        addControlGeometry(
            PropertyTarget::Control, identity, 0, 0,
            hancom::graph::ObservationState::NotExposed);
    }
    for (const TableObservation& observedTable : payloads[4].tables) {
        const std::wstring tableIdentity = CanonicalNativeSiteIdentity(
            PropertyTarget::Table, L"tbl", observedTable.headCtrlOrdinal,
            observedTable.anchor, observedTable.instanceIdPresent,
            observedTable.instanceId);
        addSpan(PropertyTarget::Table, tableIdentity, 1,
                observedTable.instanceId == L"tbl-outer" ? 2 : 1);
        addControlGeometry(PropertyTarget::Table, tableIdentity, 14400, 7200);
        for (const CellObservation& observedCell : observedTable.cells) {
            const std::wstring cellIdentity = CanonicalNativeSiteIdentity(
                PropertyTarget::Cell, L"tbl", observedTable.headCtrlOrdinal,
                {observedCell.listId, 0, 0},
                observedTable.instanceIdPresent, observedTable.instanceId) +
                L":" + observedCell.address;
            addSpan(PropertyTarget::Cell, cellIdentity,
                    observedCell.pageStart.value, observedCell.pageEnd.value);
            for (const ParagraphObservation& observedParagraph :
                 observedCell.paragraphs) {
                addSpan(PropertyTarget::Paragraph,
                        std::to_wstring(observedParagraph.start.list) + L":" +
                            std::to_wstring(observedParagraph.start.paragraph),
                        observedCell.pageStart.value,
                        observedCell.pageEnd.value);
            }
        }
    }
    for (const ImageObservation& observed : payloads[5].images) {
        const std::wstring identity = CanonicalNativeSiteIdentity(
            PropertyTarget::Image, observed.ctrlId,
            observed.headCtrlOrdinal, observed.anchor,
            observed.instanceIdPresent, observed.instanceId);
        addSpan(PropertyTarget::Image, identity, 2, 2);
        addControlGeometry(PropertyTarget::Image, identity, 3600, 2400);
    }
    auto& certificate = payloads[6].layoutObservedKindCounts;
    certificate[0] = payloads[1].paragraphs.size();
    for (const auto& observedTable : payloads[4].tables) {
        for (const auto& observedCell : observedTable.cells) {
            certificate[0] += observedCell.paragraphs.size();
        }
    }
    certificate[1] = static_cast<std::uint64_t>(std::count_if(
        payloads[3].controls.begin(), payloads[3].controls.end(),
        [](const auto& control) {
            return control.ctrlId != L"tbl" && control.ctrlId != L"$pic";
        }));
    certificate[2] = payloads[4].tables.size();
    certificate[3] = std::accumulate(
        payloads[4].tables.begin(), payloads[4].tables.end(),
        std::uint64_t{0}, [](const std::uint64_t count, const auto& table) {
            return count + table.cells.size();
        });
    certificate[4] = payloads[5].images.size();
    const std::uint64_t expectedProperties =
        2 * (certificate[0] + certificate[3]) +
        4 * (certificate[1] + certificate[2] + certificate[4]);
    payloads[6].layoutPerKindComplete =
        expectedProperties == payloads[6].layoutProperties.size();
    return payloads;
}

void RecertifySyntheticLayout(std::vector<ReaderPayload>* const payloads) {
    using namespace hancom::graph;
    using namespace hancom::graph::capture;
    if (payloads == nullptr || payloads->size() != 7) return;
    auto& layout = (*payloads)[6];
    layout.layoutProperties.clear();
    const auto add = [&layout](const PropertyTarget target,
                               std::wstring identity,
                               const PropertyKeyId key,
                               const std::int64_t value,
                               const ObservationState state =
                                   ObservationState::Value) {
        PropertyObservation property;
        property.target = target;
        property.targetIdentity = std::move(identity);
        property.ownerField = 10;
        property.key = key;
        property.scalar = key <= 12001 ? ScalarTag::Uint64
                                       : ScalarTag::HWPUNIT64;
        property.state = state;
        property.origin = state == ObservationState::Value
            ? PropertyOrigin::Generated : PropertyOrigin::Unavailable;
        property.integerValue = value;
        layout.layoutProperties.push_back(std::move(property));
    };
    const auto span = [&add](const PropertyTarget target,
                             const std::wstring& identity,
                             const std::int64_t first,
                             const std::int64_t last) {
        add(target, identity, 12000, first);
        add(target, identity, 12001, last);
    };
    const auto geometry = [&add](const PropertyTarget target,
                                 const std::wstring& identity,
                                 const std::int64_t width,
                                 const std::int64_t height,
                                 const ObservationState state =
                                     ObservationState::Value) {
        add(target, identity, 12002, width, state);
        add(target, identity, 12003, height, state);
    };
    layout.layoutObservedKindCounts.fill(0);
    for (const auto& paragraph : (*payloads)[1].paragraphs) {
        span(PropertyTarget::Paragraph,
             std::to_wstring(paragraph.start.list) + L":" +
                 std::to_wstring(paragraph.start.paragraph),
             static_cast<std::int64_t>(paragraph.sectionOrdinal + 1),
             static_cast<std::int64_t>(paragraph.sectionOrdinal + 1));
        ++layout.layoutObservedKindCounts[0];
    }
    for (const auto& control : (*payloads)[3].controls) {
        if (control.ctrlId == L"tbl" || control.ctrlId == L"$pic") continue;
        const auto identity = CanonicalNativeSiteIdentity(
            PropertyTarget::Control, control.ctrlId, control.headCtrlOrdinal,
            control.anchor, control.instanceIdPresent, control.instanceId);
        span(PropertyTarget::Control, identity, 1, 1);
        geometry(PropertyTarget::Control, identity, 0, 0,
                 ObservationState::NotExposed);
        ++layout.layoutObservedKindCounts[1];
    }
    for (const auto& table : (*payloads)[4].tables) {
        const auto tableIdentity = CanonicalNativeSiteIdentity(
            PropertyTarget::Table, L"tbl", table.headCtrlOrdinal,
            table.anchor, table.instanceIdPresent, table.instanceId);
        span(PropertyTarget::Table, tableIdentity, 1, 1);
        geometry(PropertyTarget::Table, tableIdentity, 14400, 7200);
        ++layout.layoutObservedKindCounts[2];
        for (const auto& cell : table.cells) {
            const auto identity = CanonicalNativeSiteIdentity(
                PropertyTarget::Cell, L"tbl", table.headCtrlOrdinal,
                {cell.listId, 0, 0}, table.instanceIdPresent,
                table.instanceId) + L":" + cell.address;
            span(PropertyTarget::Cell, identity,
                 cell.pageStart.value, cell.pageEnd.value);
            ++layout.layoutObservedKindCounts[3];
            for (const auto& paragraph : cell.paragraphs) {
                span(PropertyTarget::Paragraph,
                     std::to_wstring(paragraph.start.list) + L":" +
                         std::to_wstring(paragraph.start.paragraph),
                     cell.pageStart.value, cell.pageEnd.value);
                ++layout.layoutObservedKindCounts[0];
            }
        }
    }
    for (const auto& image : (*payloads)[5].images) {
        const auto identity = CanonicalNativeSiteIdentity(
            PropertyTarget::Image, image.ctrlId, image.headCtrlOrdinal,
            image.anchor, image.instanceIdPresent, image.instanceId);
        span(PropertyTarget::Image, identity, 1, 1);
        geometry(PropertyTarget::Image, identity, 3600, 2400);
        ++layout.layoutObservedKindCounts[4];
    }
    layout.layoutPerKindComplete = std::accumulate(
        layout.layoutObservedKindCounts.begin(),
        layout.layoutObservedKindCounts.end(), std::uint64_t{0}) != 0;
}

struct DescendingUuidSource final {
    std::uint8_t prefix = 0;
    std::uint32_t next = 0;
};

bool FillDescendingUuid(
    void* const context,
    std::uint8_t* const bytes,
    const std::uint32_t count) noexcept {
    if (context == nullptr || bytes == nullptr || count != 16) return false;
    auto* const source = static_cast<DescendingUuidSource*>(context);
    std::fill_n(bytes, count, std::uint8_t{0});
    bytes[0] = source->prefix;
    bytes[1] = static_cast<std::uint8_t>(source->next >> 24);
    bytes[2] = static_cast<std::uint8_t>(source->next >> 16);
    bytes[3] = static_cast<std::uint8_t>(source->next >> 8);
    bytes[4] = static_cast<std::uint8_t>(source->next);
    if (source->next == 0) return false;
    --source->next;
    return true;
}

hancom::graph::identity::UuidSource Source(
    DescendingUuidSource* const source) noexcept {
    return {source, FillDescendingUuid};
}

struct TombstoneOrderKey final {
    NodeIdBytes source{};
    std::uint64_t semanticRevision = 0;
    std::uint16_t reason = 0;
};

struct RemapOrderKey final {
    NodeIdBytes source{};
    std::uint8_t disposition = 0;
    bool targetPresent = false;
    NodeIdBytes target{};
    std::uint16_t reason = 0;
};

bool operator<(
    const TombstoneOrderKey& left,
    const TombstoneOrderKey& right) noexcept {
    if (left.source != right.source) return left.source < right.source;
    if (left.semanticRevision != right.semanticRevision) {
        return left.semanticRevision < right.semanticRevision;
    }
    return left.reason < right.reason;
}

bool operator==(
    const TombstoneOrderKey& left,
    const TombstoneOrderKey& right) noexcept {
    return left.source == right.source &&
        left.semanticRevision == right.semanticRevision &&
        left.reason == right.reason;
}

bool operator==(
    const RemapOrderKey& left,
    const RemapOrderKey& right) noexcept {
    return left.source == right.source &&
        left.disposition == right.disposition &&
        left.targetPresent == right.targetPresent &&
        left.target == right.target && left.reason == right.reason;
}

bool operator<(
    const RemapOrderKey& left,
    const RemapOrderKey& right) noexcept {
    if (left.source != right.source) return left.source < right.source;
    if (left.disposition != right.disposition) {
        return left.disposition < right.disposition;
    }
    if (left.targetPresent != right.targetPresent) {
        return left.targetPresent < right.targetPresent;
    }
    if (left.target != right.target) return left.target < right.target;
    return left.reason < right.reason;
}

std::wstring ReceiptUuidText(const NodeIdBytes& id) {
    std::wostringstream text;
    text << std::hex << std::setfill(L'0');
    for (const std::uint8_t byte : id) {
        text << std::setw(2) << static_cast<unsigned>(byte);
    }
    return text.str();
}

bool DecodeCanonicalReceiptOrder(
    const std::vector<DecodedRecord>& records,
    const std::vector<TombstoneOrderKey>& expectedTombstones,
    const std::vector<RemapOrderKey>& expectedRemaps,
    std::vector<TombstoneOrderKey>* const tombstones,
    std::vector<RemapOrderKey>* const remaps) {
    if (tombstones == nullptr || remaps == nullptr || records.empty()) {
        return false;
    }
    tombstones->clear();
    remaps->clear();
    bool receiptPhase = false;
    bool remapPhase = false;
    size_t manifestCount = 0;
    std::uint64_t declaredCount = 0;
    for (size_t index = 0; index < records.size(); ++index) {
        const DecodedRecord& record = records[index];
        if (record.id != index || record.kind > static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Remap)) {
            return false;
        }
        const auto kind = static_cast<hancom::graph::RecordKind>(record.kind);
        if (kind == hancom::graph::RecordKind::Manifest) {
            const DecodedField* const count = FindField(record.fields, 4);
            if (index != 0 || count == nullptr || count->value.size() != 8) {
                return false;
            }
            ++manifestCount;
            declaredCount = R64(count->value.data());
            continue;
        }
        if (kind == hancom::graph::RecordKind::Tombstone) {
            const DecodedField* const source = FindField(record.fields, 1);
            const DecodedField* const revision = FindField(record.fields, 2);
            const DecodedField* const reason = FindField(record.fields, 3);
            if (remapPhase || source == nullptr || source->value.size() != 16 ||
                revision == nullptr || revision->value.size() != 8 ||
                reason == nullptr || reason->value.size() != 2) {
                return false;
            }
            receiptPhase = true;
            TombstoneOrderKey key;
            std::copy_n(source->value.begin(), key.source.size(),
                        key.source.begin());
            key.semanticRevision = R64(revision->value.data());
            key.reason = R16(reason->value.data());
            tombstones->push_back(key);
            continue;
        }
        if (kind == hancom::graph::RecordKind::Remap) {
            const DecodedField* const source = FindField(record.fields, 1);
            const DecodedField* const target = FindField(record.fields, 2);
            const DecodedField* const disposition = FindField(record.fields, 3);
            const DecodedField* const reason = FindField(record.fields, 4);
            if (source == nullptr || source->value.size() != 16 ||
                (target != nullptr && target->value.size() != 16) ||
                disposition == nullptr || disposition->value.size() != 1 ||
                reason == nullptr || reason->value.size() != 2) {
                return false;
            }
            receiptPhase = true;
            remapPhase = true;
            RemapOrderKey key;
            std::copy_n(source->value.begin(), key.source.size(),
                        key.source.begin());
            key.disposition = disposition->value[0];
            key.targetPresent = target != nullptr;
            if (target != nullptr) {
                std::copy_n(target->value.begin(), key.target.size(),
                            key.target.begin());
            }
            key.reason = R16(reason->value.data());
            remaps->push_back(key);
            continue;
        }
        if (receiptPhase) return false;
    }
    const auto strictlyAscending = [](const auto& keys) {
        return std::adjacent_find(
            keys.begin(), keys.end(), [](const auto& left, const auto& right) {
                return !(left < right);
            }) == keys.end();
    };
    return manifestCount == 1 && declaredCount == 641 &&
        records.size() == 641 && records.back().id == 640 &&
        records.back().kind == static_cast<std::uint16_t>(
            hancom::graph::RecordKind::Remap) &&
        tombstones->size() == 2 && remaps->size() == 4 &&
        *tombstones == expectedTombstones && *remaps == expectedRemaps &&
        remaps->back() == expectedRemaps.back() &&
        strictlyAscending(*tombstones) && strictlyAscending(*remaps);
}

bool CanonicalReceiptUuidOrderingSmoke() {
    using hancom::graph::capture::BuildTypedRecordStream;
    using hancom::graph::capture::CaptureIdentityArena;
    using hancom::graph::capture::ControlObservation;

    std::vector<ReaderPayload> priorPayloads = SealedPayloads();
    const std::array<ControlObservation, 2> removed{{
        {L"gso", L"prior-secondary", 10, {0, 1, 3}, false},
        {L"gso", L"prior-secondary", 11, {0, 1, 4}, false},
    }};
    priorPayloads[0].controls.insert(
        priorPayloads[0].controls.end(), removed.begin(), removed.end());
    priorPayloads[3].controls = priorPayloads[0].controls;
    RecertifySyntheticLayout(&priorPayloads);

    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) return false;
    const std::filesystem::path root = std::filesystem::path(temporary) /
        (L"hwp-graph-canonical-receipt-order-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code cleanupError;
    std::filesystem::remove_all(root, cleanupError);
    DescendingUuidSource source{0xa0, 0x00ffffffU};
    CaptureIdentityArena currentArena(Source(&source));
    CaptureSpool priorSpool;
    std::vector<std::uint8_t> priorBytes;
    const bool priorBuilt = priorSpool.Build(
            root, priorPayloads, NormativeEnvironment(), currentArena) &&
        ReadWholeFile(root / L"records.hgn", &priorBytes);
    if (!priorBuilt) {
        std::wcout << L"GRAPH_CAPTURE_CANONICAL_RECEIPT_SETUP prior_build=0\n";
        return false;
    }

    std::vector<ReaderPayload> currentPayloads = SealedPayloads();
    const std::array<ControlObservation, 2> inserted{{
        {L"gso", L"current-secondary", 10, {0, 1, 3}, false},
        {L"gso", L"current-secondary", 11, {0, 1, 4}, false},
    }};
    currentPayloads[0].controls.insert(
        currentPayloads[0].controls.end(), inserted.begin(), inserted.end());
    currentPayloads[3].controls = currentPayloads[0].controls;
    RecertifySyntheticLayout(&currentPayloads);

    hancom::graph::Sha256 indexDigest{};
    std::vector<std::uint8_t> currentBytes;
    std::vector<hancom::graph::capture::RecordBlob> currentBlobs;
    currentArena.BeginLogicalAttempt();
    const bool built = BuildTypedRecordStream(
        currentPayloads, currentArena, nullptr, indexDigest,
        &currentBytes, &currentBlobs);

    const auto tombstoneInputKey = [](const auto& entry) {
        return TombstoneOrderKey{
            entry.node.bytes, entry.semanticRevision,
            static_cast<std::uint16_t>(entry.reason)};
    };
    const auto remapInputKey = [](const auto& entry) {
        return RemapOrderKey{
            entry.source.bytes, static_cast<std::uint8_t>(entry.disposition),
            entry.targetPresent,
            entry.targetPresent ? entry.target.bytes : NodeIdBytes{},
            static_cast<std::uint16_t>(entry.reason)};
    };
    const auto hasDescendingAdjacent = [](const auto& entries, const auto& key) {
        for (size_t index = 1; index < entries.size(); ++index) {
            if (key(entries[index]) < key(entries[index - 1])) return true;
        }
        return false;
    };
    const auto& inputReceipt = currentArena.Receipt();
    const bool adversarialInsertion = built &&
        hasDescendingAdjacent(inputReceipt.tombstones, tombstoneInputKey) &&
        hasDescendingAdjacent(inputReceipt.remaps, remapInputKey);

    const auto uuid = [](const std::initializer_list<std::uint8_t> bytes) {
        NodeIdBytes value{};
        std::copy(bytes.begin(), bytes.end(), value.begin());
        return value;
    };
    const NodeIdBytes zero{};
    const std::vector<TombstoneOrderKey> expectedTombstones{
        {uuid({0xa0,0x00,0xff,0xff,0xf2,0x00,0x40,0x00,
               0x80,0x00,0x00,0x00,0x00,0x00,0x00,0x00}), 1, 3},
        {uuid({0xa0,0x00,0xff,0xff,0xf3,0x00,0x40,0x00,
               0x80,0x00,0x00,0x00,0x00,0x00,0x00,0x00}), 1, 3},
    };
    const NodeIdBytes freshFirst = uuid({
        0xa0,0x00,0xff,0xff,0x6d,0x00,0x40,0x00,
        0x80,0x00,0x00,0x00,0x00,0x00,0x00,0x00});
    const NodeIdBytes freshSecond = uuid({
        0xa0,0x00,0xff,0xff,0x6e,0x00,0x40,0x00,
        0x80,0x00,0x00,0x00,0x00,0x00,0x00,0x00});
    const std::vector<RemapOrderKey> expectedRemaps{
        {freshFirst, 1, true, freshFirst, 10},
        {freshSecond, 1, true, freshSecond, 10},
        {expectedTombstones[0].source, 3, false, zero, 10},
        {expectedTombstones[1].source, 3, false, zero, 10},
    };

    std::vector<DecodedRecord> records;
    std::vector<TombstoneOrderKey> tombstones;
    std::vector<RemapOrderKey> remaps;
    size_t decodedBytes = 0;
    const bool physicalEof = built &&
        DecodeRecords(currentBytes, &records, &decodedBytes) &&
        decodedBytes == currentBytes.size();
    const bool canonical = physicalEof && DecodeCanonicalReceiptOrder(
        records, expectedTombstones, expectedRemaps, &tombstones, &remaps);

    std::vector<DecodedRecord> reversed = records;
    const auto reverseFirstPair = [&reversed](const hancom::graph::RecordKind kind) {
        auto first = std::find_if(
            reversed.begin(), reversed.end(), [kind](const DecodedRecord& record) {
                return record.kind == static_cast<std::uint16_t>(kind);
            });
        if (first == reversed.end()) return false;
        auto second = std::find_if(
            std::next(first), reversed.end(), [kind](const DecodedRecord& record) {
                return record.kind == static_cast<std::uint16_t>(kind);
            });
        if (second == reversed.end()) return false;
        std::swap(first->fields, second->fields);
        return true;
    };
    const bool reversedPair = reverseFirstPair(
        hancom::graph::RecordKind::Tombstone);
    std::vector<TombstoneOrderKey> rejectedTombstones;
    std::vector<RemapOrderKey> rejectedRemaps;
    const bool reversedRejected = reversedPair &&
        !DecodeCanonicalReceiptOrder(
            reversed, expectedTombstones, expectedRemaps,
            &rejectedTombstones, &rejectedRemaps);
    const auto setManifestCount = [](std::vector<DecodedRecord>* const decoded,
                                     const std::uint64_t count) {
        if (decoded->empty()) return false;
        for (DecodedField& field : decoded->front().fields) {
            if (field.tag != 4 || field.value.size() != 8) continue;
            for (size_t index = 0; index < 8; ++index) {
                field.value[index] = static_cast<std::uint8_t>(
                    count >> (index * 8));
            }
            return true;
        }
        return false;
    };
    std::vector<DecodedRecord> extraReceipt = records;
    DecodedRecord duplicateRemap = extraReceipt.back();
    duplicateRemap.id = 641;
    extraReceipt.push_back(std::move(duplicateRemap));
    const bool extraReceiptRejected = setManifestCount(&extraReceipt, 642) &&
        !DecodeCanonicalReceiptOrder(
            extraReceipt, expectedTombstones, expectedRemaps,
            &rejectedTombstones, &rejectedRemaps);
    std::vector<DecodedRecord> terminalEnvelope = records;
    DecodedRecord syntheticTerminal;
    syntheticTerminal.kind = 9;
    syntheticTerminal.id = 641;
    terminalEnvelope.push_back(std::move(syntheticTerminal));
    const bool terminalEnvelopeRejected =
        setManifestCount(&terminalEnvelope, 642) &&
        !DecodeCanonicalReceiptOrder(
            terminalEnvelope, expectedTombstones, expectedRemaps,
            &rejectedTombstones, &rejectedRemaps);
    std::vector<std::uint8_t> trailingBytes = currentBytes;
    trailingBytes.push_back(0xff);
    std::vector<DecodedRecord> trailingRecords;
    size_t trailingDecodedBytes = 0;
    const bool trailingBytesRejected = !DecodeRecords(
        trailingBytes, &trailingRecords, &trailingDecodedBytes);
    const bool negativeRejected = reversedRejected && extraReceiptRejected &&
        terminalEnvelopeRejected && trailingBytesRejected;
    const bool noTerminalKind = std::all_of(
        records.begin(), records.end(), [](const DecodedRecord& record) {
            return record.kind <= static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Remap);
        });
    const bool exactEof = canonical && noTerminalKind && physicalEof &&
        records.back().kind == static_cast<std::uint16_t>(
            hancom::graph::RecordKind::Remap) &&
        records.back().id == 640;

    std::wcout << L"GRAPH_CAPTURE_CANONICAL_RECEIPT_UUID_ORDERING "
               << (canonical && adversarialInsertion && negativeRejected &&
                   exactEof)
               << L" tombstones=" << tombstones.size()
               << L" remaps=" << remaps.size()
               << L" records=" << records.size()
               << L" manifest_count=" << (records.empty() ? 0 :
                    (FindField(records.front().fields, 4) == nullptr ? 0 :
                     R64(FindField(records.front().fields, 4)->value.data())))
               << L" ids=0.." << (records.empty() ? 0 : records.size() - 1)
               << L" adversarial_insertion=" << adversarialInsertion << L'\n'
               << L"GRAPH_CAPTURE_CANONICAL_RECEIPT_EXACT_EOF " << exactEof
               << L" decoded_bytes=" << decodedBytes
               << L" physical_bytes=" << currentBytes.size()
               << L" schema_terminal_kind_absent=" << noTerminalKind
               << L" final_kind=Remap final_id="
               << (records.empty() ? 0 : records.back().id) << L'\n'
               << L"GRAPH_CAPTURE_CANONICAL_RECEIPT_NEGATIVE_ORACLE "
               << negativeRejected << L" reversed_tombstone_pair="
               << reversedRejected << L" extra_receipt_rejected="
               << extraReceiptRejected << L" terminal_envelope_rejected="
               << terminalEnvelopeRejected << L" trailing_bytes_rejected="
               << trailingBytesRejected << L'\n';
    for (const TombstoneOrderKey& key : tombstones) {
        std::wcout << L"GRAPH_CAPTURE_CANONICAL_TOMBSTONE key=("
                   << ReceiptUuidText(key.source) << L','
                   << key.semanticRevision << L',' << key.reason << L")\n";
    }
    for (const RemapOrderKey& key : remaps) {
        std::wcout << L"GRAPH_CAPTURE_CANONICAL_REMAP key=("
                   << ReceiptUuidText(key.source) << L','
                   << static_cast<unsigned>(key.disposition) << L','
                   << key.targetPresent << L','
                   << ReceiptUuidText(key.target) << L',' << key.reason
                   << L")\n";
    }
    priorSpool.Reset();
    std::filesystem::remove_all(root, cleanupError);
    return canonical && adversarialInsertion && negativeRejected && exactEof &&
        !cleanupError && !std::filesystem::exists(root);
}

std::vector<ReaderPayload> ReferenceClosurePayloads() {
    using namespace hancom::graph::capture;
    std::vector<ReaderPayload> payloads = SealedPayloads();
    ControlObservation absentInstance{
        L"gso", L"", 10, {0, 1, 3}, false};
    absentInstance.instanceIdPresent = false;
    for (const ControlObservation& control : {
             absentInstance,
             ControlObservation{L"gso", L"duplicate", 11,
                                {0, 1, 3}, false},
             ControlObservation{L"gso", L"duplicate", 12,
                                {0, 1, 3}, false}}) {
        payloads[0].controls.push_back(control);
        payloads[3].controls.push_back(control);
    }
    ReaderPayload& effective = payloads[static_cast<size_t>(
        QualifiedReader::EffectiveProperties)];
    effective.coverage = hancom::graph::CoverageState::ReadFailed;
    effective.properties.clear();
    PropertyObservation currentEffective;
    currentEffective.target = PropertyTarget::Run;
    currentEffective.targetIdentity = L"0:0:0:12";
    currentEffective.ownerField = 102;
    currentEffective.key = 1000;
    currentEffective.scalar = hancom::graph::ScalarTag::UTF16;
    currentEffective.state = hancom::graph::ObservationState::Value;
    currentEffective.origin = hancom::graph::PropertyOrigin::Unknown;
    currentEffective.textValue = L"Hancom Gothic";
    effective.properties.push_back(std::move(currentEffective));
    class DefinitionBodyMatrixSource final
        : public hancom::graph::properties::ReferenceClosureSource {
    public:
        bool ReadSite(
            const hancom::graph::properties::ReferenceSite&,
            hancom::graph::properties::ReferenceSiteObservation* const output)
            noexcept override {
            if (output == nullptr) return false;
            using hancom::graph::DefinitionKind;
            using hancom::graph::ScalarTag;
            using hancom::graph::properties::ReadStatus;
            using hancom::graph::properties::ReferencedDefinition;
            const auto add = [output](
                const DefinitionKind kind,
                const std::int64_t nativeId,
                const ReadStatus body) -> ReferencedDefinition& {
                ReferencedDefinition definition;
                definition.kind = kind;
                definition.nativeId = nativeId;
                definition.nativeIdStatus = ReadStatus::Value;
                definition.bodyStatus = body;
                output->definitions.push_back(std::move(definition));
                return output->definitions.back();
            };
            add(DefinitionKind::Style, 1, ReadStatus::ReadFailed);
            add(DefinitionKind::CharacterShape, 2,
                ReadStatus::NotApplicable);
            auto& paragraph = add(
                DefinitionKind::ParagraphShape, 3, ReadStatus::Value);
            paragraph.properties.push_back(
                {2000, ScalarTag::Enum, ReadStatus::Value, L"i:1"});
            add(DefinitionKind::Numbering, 4, ReadStatus::NotApplicable);
            add(DefinitionKind::BorderFill, 5, ReadStatus::NotExposed);
            add(DefinitionKind::TabDef, 6, ReadStatus::NotApplicable);
            auto& page = add(DefinitionKind::PageDef, 7, ReadStatus::Value);
            page.properties.push_back(
                {7000, ScalarTag::HWPUNIT64, ReadStatus::Value, L"i:59528"});
            auto& column = add(
                DefinitionKind::ColumnDef, 8, ReadStatus::Value);
            column.properties.push_back(
                {7100, ScalarTag::Uint64, ReadStatus::Value, L"u:2"});
            return true;
        }
    } source;
    hancom::graph::properties::ReferenceClosureDiagnostics diagnostics;
    const std::vector<hancom::graph::properties::ReferenceSite> matrixSite{{
        PropertyTarget::Paragraph, L"definition-body-matrix", {0, 0, 0}, 0}};
    if (hancom::graph::properties::CaptureReferenceClosure(
            source, matrixSite, &effective, &diagnostics) !=
            hancom::graph::properties::CaptureStatus::Complete ||
        diagnostics.uniqueDefinitions != 8) {
        payloads.clear();
        return payloads;
    }
    PropertyObservation recoveredEffective;
    recoveredEffective.target = PropertyTarget::Run;
    recoveredEffective.targetIdentity = L"0:0:0:12";
    recoveredEffective.ownerField = 102;
    recoveredEffective.key = 1000;
    recoveredEffective.scalar = hancom::graph::ScalarTag::UTF16;
    recoveredEffective.state = hancom::graph::ObservationState::Value;
    recoveredEffective.origin = hancom::graph::PropertyOrigin::Unknown;
    recoveredEffective.textValue = L"Hancom Gothic";
    effective.properties.push_back(std::move(recoveredEffective));
    const auto addReference = [&effective](
        const PropertyTarget target,
        std::wstring identity,
        const hancom::graph::EdgeKind edge,
        const hancom::graph::DefinitionKind definitionKind) {
        const auto definition = std::find_if(
            effective.definitions.begin(), effective.definitions.end(),
            [definitionKind](const DefinitionObservation& candidate) {
                return candidate.kind == definitionKind;
            });
        if (definition == effective.definitions.end()) return false;
        DefinitionReferenceObservation reference;
        reference.source = target;
        reference.sourceIdentity = std::move(identity);
        reference.edge = edge;
        reference.definitionIdentity = definition->identity;
        effective.definitionReferences.push_back(std::move(reference));
        return true;
    };
    std::uint64_t sites = 0;
    for (const SectionObservation& section : payloads[0].sections) {
        const std::wstring identity = std::to_wstring(section.ordinal);
        addReference(
            PropertyTarget::Section, identity,
            hancom::graph::EdgeKind::PageDefRef,
            hancom::graph::DefinitionKind::PageDef);
        addReference(
            PropertyTarget::Section, identity,
            hancom::graph::EdgeKind::ColumnDefRef,
            hancom::graph::DefinitionKind::ColumnDef);
        ++sites;
    }
    const auto addTextSites = [&](const ParagraphObservation& paragraph) {
        const std::wstring paragraphIdentity =
            std::to_wstring(paragraph.start.list) + L":" +
            std::to_wstring(paragraph.start.paragraph);
        addReference(
            PropertyTarget::Paragraph, paragraphIdentity,
            hancom::graph::EdgeKind::StyleRef,
            hancom::graph::DefinitionKind::Style);
        addReference(
            PropertyTarget::Paragraph, paragraphIdentity,
            hancom::graph::EdgeKind::ParagraphShapeRef,
            hancom::graph::DefinitionKind::ParagraphShape);
        ++sites;
        for (const RunObservation& run : paragraph.runs) {
            const std::wstring runIdentity =
                std::to_wstring(run.start.list) + L":" +
                std::to_wstring(run.start.paragraph) + L":" +
                std::to_wstring(run.start.character) + L":" +
                std::to_wstring(run.end.character);
            addReference(
                PropertyTarget::Run, runIdentity,
                hancom::graph::EdgeKind::CharacterShapeRef,
                hancom::graph::DefinitionKind::CharacterShape);
            ++sites;
        }
    };
    for (const ParagraphObservation& paragraph : payloads[1].paragraphs) {
        addTextSites(paragraph);
    }
    for (const TableObservation& table : payloads[4].tables) {
        for (const CellObservation& cell : table.cells) {
            addReference(
                PropertyTarget::Cell,
                std::to_wstring(static_cast<unsigned>(PropertyTarget::Cell)) +
                    (table.instanceIdPresent ? L":tbl:1:" : L":tbl:0:") +
                    std::to_wstring(table.headCtrlOrdinal) + L":" +
                    std::to_wstring(cell.listId) + L":0:0:" +
                    table.instanceId + L":" + cell.address,
                hancom::graph::EdgeKind::BorderFillRef,
                hancom::graph::DefinitionKind::BorderFill);
            ++sites;
            for (const ParagraphObservation& paragraph : cell.paragraphs) {
                addTextSites(paragraph);
            }
        }
    }
    effective.referenceTraversal.expectedSites = sites;
    effective.referenceTraversal.visitedSites = sites;
    effective.referenceTraversal.globalStyleCatalogNotExposed = true;
    effective.referenceTraversal.globalNumberingCatalogNotExposed = true;
    effective.referenceTraversal.globalBulletCatalogNotExposed = true;
    effective.referenceTraversal.globalTabDefCatalogNotExposed = true;
    hancom::graph::Sha256 pageSetupDigest{};
    if (!hancom::graph::properties::DerivePageSetupDigest(
            effective, &pageSetupDigest)) {
        payloads.clear();
        return payloads;
    }
    payloads[6].layoutEnvironment = NormativeEnvironment(
        96, &pageSetupDigest);
    return payloads;
}

bool CharacterShapeReferenceCardinalityFailsClosedSmoke() {
    using namespace hancom::graph::capture;
    const std::filesystem::path root =
        std::filesystem::temp_directory_path() /
        (L"hwp-character-shape-cardinality-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    const auto builds = [&root](
        const wchar_t* const lane,
        std::vector<ReaderPayload> payloads) {
        RecertifySyntheticLayout(&payloads);
        CaptureSpool spool;
        hancom::graph::capture::CaptureIdentityArena arena;
        const std::filesystem::path output = root / lane;
        std::wstring failure;
        const bool built = spool.Build(
            output, payloads, payloads[6].layoutEnvironment, arena,
            &failure);
        if (!built) {
            std::wcout << L"GRAPH_CAPTURE_CHARACTER_SHAPE_BUILD_DETAIL lane="
                       << lane << L" built=0 stage=" << failure << L'\n';
        }
        spool.Reset();
        return built;
    };
    const auto rejected = [&builds, &root](
        const wchar_t* const lane,
        std::vector<ReaderPayload> payloads) {
        const std::filesystem::path output = root / lane;
        const bool buildRejected = !builds(lane, std::move(payloads));
        const bool unpublished =
            !std::filesystem::exists(output / L"records.hgn");
        std::wcout << L"GRAPH_CAPTURE_CHARACTER_SHAPE_NEGATIVE_DETAIL lane="
                   << lane << L" rejected=" << buildRejected
                   << L" unpublished=" << unpublished << L'\n';
        return buildRejected && unpublished;
    };
    const auto mutateFirstRun = [](
        std::vector<ReaderPayload>* const payloads,
        const size_t wanted) {
        ReaderPayload& effective = (*payloads)[static_cast<size_t>(
            QualifiedReader::EffectiveProperties)];
        const auto first = std::find_if(
            effective.definitionReferences.begin(),
            effective.definitionReferences.end(), [](const auto& reference) {
                return reference.source == PropertyTarget::Run &&
                    reference.sourceIdentity == L"0:0:0:12" &&
                    reference.edge ==
                        hancom::graph::EdgeKind::CharacterShapeRef;
            });
        if (first == effective.definitionReferences.end()) return false;
        const DefinitionReferenceObservation original = *first;
        effective.definitionReferences.erase(first);
        for (size_t count = 0; count < wanted; ++count) {
            effective.definitionReferences.push_back(original);
        }
        return true;
    };

    const auto propertyFreeComplete = [](
        std::vector<ReaderPayload>* const payloads) {
        ReaderPayload& effective = (*payloads)[static_cast<size_t>(
            QualifiedReader::EffectiveProperties)];
        effective.coverage = hancom::graph::CoverageState::Complete;
        effective.properties.erase(
            std::remove_if(
                effective.properties.begin(), effective.properties.end(),
                [](const PropertyObservation& property) {
                    return property.target == PropertyTarget::Run &&
                        property.targetIdentity == L"0:0:0:12";
                }),
            effective.properties.end());
    };
    const std::vector<ReaderPayload> validPayloads = SealedPayloads();
    const bool valid = builds(L"valid", validPayloads);
    std::vector<ReaderPayload> zero = SealedPayloads();
    std::vector<ReaderPayload> oneWithoutBag = SealedPayloads();
    std::vector<ReaderPayload> duplicate = SealedPayloads();
    std::vector<ReaderPayload> twoTarget = SealedPayloads();
    std::vector<ReaderPayload> dangling = SealedPayloads();
    std::vector<ReaderPayload> wrongKind = SealedPayloads();
    for (auto* lane : {&zero, &oneWithoutBag, &duplicate, &twoTarget,
                       &dangling, &wrongKind}) {
        propertyFreeComplete(lane);
    }
    const bool preparedZero = mutateFirstRun(&zero, 0);
    const bool preparedOne = mutateFirstRun(&oneWithoutBag, 1);
    const bool preparedDuplicate = mutateFirstRun(&duplicate, 2);
    bool preparedTwoTarget = mutateFirstRun(&twoTarget, 1);
    ReaderPayload& twoEffective = twoTarget[static_cast<size_t>(
        QualifiedReader::EffectiveProperties)];
    const auto character = std::find_if(
        twoEffective.definitions.begin(), twoEffective.definitions.end(),
        [](const auto& definition) {
            return definition.kind ==
                hancom::graph::DefinitionKind::CharacterShape;
        });
    if (character == twoEffective.definitions.end()) {
        preparedTwoTarget = false;
    } else {
        DefinitionObservation second = *character;
        second.identity += L":distinct-target";
        second.propertyDigest.bytes.back() ^= 0x5aU;
        for (PropertyObservation& property : second.properties) {
            property.targetIdentity = second.identity;
        }
        twoEffective.definitions.push_back(second);
        DefinitionReferenceObservation secondReference;
        secondReference.source = PropertyTarget::Run;
        secondReference.sourceIdentity = L"0:0:0:12";
        secondReference.edge =
            hancom::graph::EdgeKind::CharacterShapeRef;
        secondReference.definitionIdentity = second.identity;
        secondReference.ordinal = 1;
        twoEffective.definitionReferences.push_back(
            std::move(secondReference));
    }
    const bool preparedDangling = mutateFirstRun(&dangling, 1);
    const bool preparedWrongKind = mutateFirstRun(&wrongKind, 1);
    ReaderPayload& danglingEffective = dangling[static_cast<size_t>(
        QualifiedReader::EffectiveProperties)];
    ReaderPayload& wrongKindEffective = wrongKind[static_cast<size_t>(
        QualifiedReader::EffectiveProperties)];
    if (preparedDangling) {
        const auto reference = std::find_if(
            danglingEffective.definitionReferences.begin(),
            danglingEffective.definitionReferences.end(), [](const auto& item) {
                return item.sourceIdentity == L"0:0:0:12" &&
                    item.edge == hancom::graph::EdgeKind::CharacterShapeRef;
            });
        reference->definitionIdentity = L"missing-character-shape";
    }
    const auto wrongTarget = std::find_if(
        wrongKindEffective.definitions.begin(),
        wrongKindEffective.definitions.end(), [](const auto& definition) {
            return definition.kind ==
                hancom::graph::DefinitionKind::CharacterShape;
        });
    if (preparedWrongKind && wrongTarget !=
            wrongKindEffective.definitions.end()) {
        wrongTarget->kind = hancom::graph::DefinitionKind::Style;
    }
    std::vector<ReaderPayload> terminal = zero;
    ReaderPayload& terminalEffective = terminal[static_cast<size_t>(
        QualifiedReader::EffectiveProperties)];
    terminalEffective.coverage = hancom::graph::CoverageState::ReadFailed;
    terminalEffective.coverageFacts.erase(
        std::remove_if(
            terminalEffective.coverageFacts.begin(),
            terminalEffective.coverageFacts.end(),
            [](const CoverageObservation& fact) {
                return fact.target == PropertyTarget::Run &&
                    fact.targetIdentity == L"0:0:0:12" &&
                    fact.coordinate ==
                        hancom::graph::CoverageCoordinateKind::NodeField &&
                    fact.ownerField == 102 &&
                    fact.profile == hancom::graph::ProfileId::EditableText;
            }),
        terminalEffective.coverageFacts.end());
    CoverageObservation terminalCoverage;
    terminalCoverage.target = PropertyTarget::Run;
    terminalCoverage.targetIdentity = L"0:0:0:12";
    terminalCoverage.coordinate =
        hancom::graph::CoverageCoordinateKind::NodeField;
    terminalCoverage.ownerField = 102;
    terminalCoverage.profile = hancom::graph::ProfileId::EditableText;
    terminalCoverage.state = hancom::graph::CoverageState::NotExposed;
    terminalEffective.coverageFacts.push_back(std::move(terminalCoverage));
    const auto addCoverage = [](std::vector<ReaderPayload>* const payloads,
                                const hancom::graph::CoverageState state) {
        CoverageObservation coverage;
        coverage.target = PropertyTarget::Run;
        coverage.targetIdentity = L"0:0:0:12";
        coverage.coordinate =
            hancom::graph::CoverageCoordinateKind::NodeField;
        coverage.ownerField = 102;
        coverage.profile = hancom::graph::ProfileId::EditableText;
        coverage.state = state;
        (*payloads)[static_cast<size_t>(
            QualifiedReader::EffectiveProperties)].coverageFacts.push_back(
                std::move(coverage));
    };
    std::vector<ReaderPayload> mixedNotExposed = zero;
    addCoverage(&mixedNotExposed, hancom::graph::CoverageState::Complete);
    addCoverage(&mixedNotExposed, hancom::graph::CoverageState::NotExposed);
    std::vector<ReaderPayload> mixedReadFailed = zero;
    addCoverage(&mixedReadFailed, hancom::graph::CoverageState::Complete);
    addCoverage(&mixedReadFailed, hancom::graph::CoverageState::ReadFailed);
    std::vector<ReaderPayload> terminalWithData = validPayloads;
    addCoverage(&terminalWithData, hancom::graph::CoverageState::NotExposed);
    std::vector<ReaderPayload> duplicateCompleteCoverage = validPayloads;
    addCoverage(
        &duplicateCompleteCoverage, hancom::graph::CoverageState::Complete);
    std::vector<ReaderPayload> duplicateNotExposedCoverage = terminal;
    addCoverage(
        &duplicateNotExposedCoverage,
        hancom::graph::CoverageState::NotExposed);
    std::vector<ReaderPayload> duplicateReadFailedCoverage = terminal;
    ReaderPayload& duplicateReadFailedEffective =
        duplicateReadFailedCoverage[static_cast<size_t>(
            QualifiedReader::EffectiveProperties)];
    for (CoverageObservation& fact :
         duplicateReadFailedEffective.coverageFacts) {
        if (fact.target == PropertyTarget::Run &&
            fact.targetIdentity == L"0:0:0:12" &&
            fact.ownerField == 102) {
            fact.state = hancom::graph::CoverageState::ReadFailed;
        }
    }
    addCoverage(
        &duplicateReadFailedCoverage,
        hancom::graph::CoverageState::ReadFailed);
    std::vector<ReaderPayload> extraCoverage = validPayloads;
    addCoverage(
        &extraCoverage, hancom::graph::CoverageState::ProjectionOmitted);
    const bool result = valid && preparedZero && preparedOne &&
        preparedDuplicate && preparedTwoTarget && preparedDangling &&
        preparedWrongKind &&
        rejected(L"property-free-zero", std::move(zero)) &&
        rejected(L"property-free-one", std::move(oneWithoutBag)) &&
        rejected(L"property-free-duplicate", std::move(duplicate)) &&
        rejected(L"property-free-two-target", std::move(twoTarget)) &&
        rejected(L"property-free-dangling", std::move(dangling)) &&
        rejected(L"property-free-wrong-kind", std::move(wrongKind)) &&
        rejected(L"mixed-complete-not-exposed", std::move(mixedNotExposed)) &&
        rejected(L"mixed-complete-read-failed", std::move(mixedReadFailed)) &&
        rejected(L"terminal-with-data", std::move(terminalWithData)) &&
        rejected(
            L"duplicate-complete-coverage",
            std::move(duplicateCompleteCoverage)) &&
        rejected(
            L"duplicate-not-exposed-coverage",
            std::move(duplicateNotExposedCoverage)) &&
        rejected(
            L"duplicate-read-failed-coverage",
            std::move(duplicateReadFailedCoverage)) &&
        rejected(L"extra-coverage", std::move(extraCoverage)) &&
        builds(L"property-free-terminal", std::move(terminal));
    std::filesystem::remove_all(root, error);
    return result && !std::filesystem::exists(root);
}

struct SealedFixture final {
    bool built = false;
    bool published = false;
    std::uint64_t activeSerial = 0;
    bool decoded = false;
    bool recordIdsContiguous = false;
    std::uint64_t declaredRecordCount = 0;
    std::vector<DecodedRecord> records{};
    std::vector<std::uint8_t> sealedBytes{};
    std::vector<NodeIdBytes> firstAttemptNodeIds{};
    std::vector<NodeIdBytes> secondAttemptNodeIds{};
    hancom::graph::store::TraversalManifest firstManifest{};
    hancom::graph::store::TraversalManifest secondManifest{};
    hancom::graph::query::Status queryViewStatus =
        hancom::graph::query::Status::StorageFailure;
    hancom::graph::query::QueryView queryView{};
    hancom::graph::codec::Error queryViewValidation =
        hancom::graph::codec::Error::BadFlags;
    std::uint64_t queryViewRecordCount = 0;
    hancom::graph::query::Status structureViewStatus =
        hancom::graph::query::Status::StorageFailure;
    hancom::graph::query::QueryView structureView{};
    std::uint64_t generationAuthenticationCalls = 0;
};

bool BuildSealedFixture(
    const wchar_t* const tag,
    SealedFixture* const output) {
    wchar_t temporary[MAX_PATH]{};
    if (output == nullptr || GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (std::wstring(L"hwp-graph-capture-sealed-") + tag + L"-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    CaptureSpool first;
    CaptureSpool second;
    hancom::graph::capture::CaptureIdentityArena arena;
    const std::vector<std::uint8_t> environment = NormativeEnvironment();
    const std::vector<ReaderPayload> payloads = SealedPayloads();
    if (!first.Build(root / L"attempt-0", payloads, environment, arena) ||
        !second.Build(root / L"attempt-1", payloads, environment, arena)) {
        std::filesystem::remove_all(root, error);
        return false;
    }
    output->built = true;
    FakeContext context = Context();
    context.attempts[0] = first.Result();
    context.attempts[1] = second.Result();
    output->firstManifest = context.attempts[0].manifest;
    output->secondManifest = context.attempts[1].manifest;
    {
        GraphStore store((root / L"store").wstring());
        CaptureCoordinator coordinator;
        output->published = store.Initialize() &&
            coordinator.CaptureToStore(Suite(), &context, &store, {}) ==
                CaptureStatus::Complete;
        output->activeSerial = store.ActiveSerial();
        if (output->published) {
            const auto pin = store.PinActive();
            output->decoded = pin != nullptr &&
                ReadWholeFile(
                    std::filesystem::path(pin->Path()) / L"records.hgn",
                    &output->sealedBytes) &&
                DecodeRecords(output->sealedBytes, &output->records);
            hancom::graph::query::GraphStoreGenerationSource source(&store);
            hancom::graph::store::ResetGenerationAuthenticationDebugCount();
            hancom::graph::query::Query query{};
            query.projectionBits =
                hancom::graph::query::ProjectStructure |
                hancom::graph::query::ProjectText |
                hancom::graph::query::ProjectProperties |
                hancom::graph::query::ProjectAssets |
                hancom::graph::query::ProjectReferences |
                hancom::graph::query::ProjectLayout;
            hancom::graph::identity::DocumentSessionId session{};
            session.bytes = {0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x40, 0x70,
                             0x80, 0x90, 0xa0, 0xb0, 0xc0, 0xd0, 0xe0, 0xf0};
            output->queryViewStatus = hancom::graph::query::BuildQueryView(
                &source, query, &output->queryView, &session);
            hancom::graph::query::Query structureQuery{};
            structureQuery.projectionBits =
                hancom::graph::query::ProjectStructure;
            output->structureViewStatus = hancom::graph::query::BuildQueryView(
                &source, structureQuery, &output->structureView, &session);
            output->generationAuthenticationCalls =
                hancom::graph::store::ReadGenerationAuthenticationDebugCount();
            const auto readView = [](void* context, std::uint64_t offset,
                                     std::uint8_t* bytes,
                                     std::uint32_t requested,
                                     std::uint32_t* actual) noexcept {
                if (context == nullptr || actual == nullptr) return false;
                const auto& view = *static_cast<
                    const hancom::graph::codec::Bytes*>(context);
                if (offset > view.size() || requested > view.size() - offset ||
                    (requested != 0 && bytes == nullptr)) {
                    *actual = 0;
                    return false;
                }
                std::copy_n(view.data() + offset, requested, bytes);
                *actual = requested;
                return true;
            };
            hancom::graph::codec::RecordStream viewStream{
                &output->queryView.records, readView,
                output->queryView.records.size(),
                hancom::graph::StreamKind::QueryView};
            output->queryViewValidation =
                hancom::graph::codec::ValidateRecordStream(
                    viewStream, &output->queryViewRecordCount);
        }
    }
    if (output->decoded) {
        output->recordIdsContiguous = true;
        for (size_t index = 0; index < output->records.size(); ++index) {
            if (output->records[index].id != index) {
                output->recordIdsContiguous = false;
            }
        }
        if (output->records[0].kind ==
            static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Manifest)) {
            const DecodedField* const declared =
                FindField(output->records[0].fields, 4);
            if (declared != nullptr && declared->value.size() == 8) {
                output->declaredRecordCount = R64(declared->value.data());
            }
        }
    }
    std::vector<std::uint8_t> attemptBytes;
    std::vector<DecodedRecord> attemptRecords;
    if (ReadWholeFile(root / L"attempt-0" / L"records.hgn", &attemptBytes) &&
        DecodeRecords(attemptBytes, &attemptRecords)) {
        output->firstAttemptNodeIds = Analyze(attemptRecords).nodeIds;
    }
    attemptBytes.clear();
    attemptRecords.clear();
    if (ReadWholeFile(root / L"attempt-1" / L"records.hgn", &attemptBytes) &&
        DecodeRecords(attemptBytes, &attemptRecords)) {
        output->secondAttemptNodeIds = Analyze(attemptRecords).nodeIds;
    }
    first.Reset();
    second.Reset();
    std::filesystem::remove_all(root, error);
    return true;
}

// Baseline characterization: proves the coordinator/store atomic paths and
// the sealed-generation decode machinery work on current behavior. It pins
// only invariants that must survive the typed-records change: one publish,
// serial 1, Manifest first, contiguous record IDs, declared count equals
// decoded count.
bool BaselineSealedGenerationDecodesSmoke() {
    SealedFixture fixture;
    if (!BuildSealedFixture(L"baseline", &fixture)) {
        return false;
    }
    bool queryViewBlobClosure = !fixture.queryView.blobClosure.empty();
    for (const auto& blob : fixture.queryView.blobClosure) {
        std::vector<std::uint8_t> content(
            static_cast<std::size_t>(blob.length));
        queryViewBlobClosure = queryViewBlobClosure &&
            hancom::graph::query::ReadQueryViewBlob(
                fixture.queryView, blob, 0, content.data(), blob.length) &&
            hancom::graph::codec::Hash(hancom::graph::codec::View(content)).bytes ==
                blob.digest.bytes;
    }
    const bool projectionClosure =
        fixture.structureViewStatus ==
            hancom::graph::query::Status::Terminal &&
        fixture.structureView.blobClosure.size() <
            fixture.queryView.blobClosure.size();
    const bool canonicalQueryView =
        fixture.queryViewStatus == hancom::graph::query::Status::Terminal &&
        fixture.queryViewValidation == hancom::graph::codec::Error::None &&
        fixture.queryViewRecordCount ==
            fixture.queryView.canonical.recordStream.itemCount &&
        fixture.queryView.canonical.streamKind ==
            hancom::graph::StreamKind::QueryView &&
        fixture.queryView.canonical.recordStream.byteLength ==
            fixture.queryView.records.size() &&
        !fixture.queryView.records.empty();
    std::wcout << L"CAPTURE_QUERY_VIEW_CANONICAL_NO_STRUCTURE_VALUE\t"
               << canonicalQueryView << L'\n'
               << L"CAPTURE_QUERY_VIEW_AUTHENTICATED_BLOB_CLOSURE\t"
               << queryViewBlobClosure << L'\n'
               << L"CAPTURE_QUERY_VIEW_PROJECTION_BLOB_EXCLUSION\t"
               << projectionClosure << L'\n'
               << L"CAPTURE_QUERY_VIEW_PREPARED_AUTHENTICATION_REUSE\t"
               << (fixture.generationAuthenticationCalls == 0)
               << L" expected=0 actual="
               << fixture.generationAuthenticationCalls << L'\n';
    return canonicalQueryView && queryViewBlobClosure && projectionClosure &&
        fixture.generationAuthenticationCalls == 0 &&
        fixture.built && fixture.published &&
        fixture.activeSerial == 1 &&
        fixture.decoded && fixture.recordIdsContiguous &&
        fixture.records[0].kind ==
            static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Manifest) &&
        fixture.records[0].id == 0 &&
        fixture.declaredRecordCount == fixture.records.size() &&
        Analyze(fixture.records).manifests == 1;
}

struct TypedPopulationChecks final {
    std::uint64_t manifests = 0;
    std::uint64_t nodes = 0;
    std::uint64_t edges = 0;
    std::uint64_t properties = 0;
    std::uint64_t coverageRecords = 0;
    bool recordKinds = false;
    bool nodeKinds = false;
    bool nodeIdsValid = false;
    bool documentEnvelope = false;
    bool terminals = false;
    bool roots = false;
    bool idReuse = false;
    bool notOpaqueOnly = false;

    bool All() const noexcept {
        return recordKinds && nodeKinds && nodeIdsValid && documentEnvelope &&
            terminals && roots && idReuse && notOpaqueOnly;
    }
};

// Failing-first regression for Todo 13's WARN blocker: the sealed generation
// must decode to schema-conformant typed Node/Edge/Property records with
// RFC4122-v4 NodeIds reused across both traversals, a real Document
// envelope, typed terminal facts, and must explicitly fail when the stream
// is only Manifest + nominal Document + Coverage blobs.
TypedPopulationChecks TypedGenerationPopulationSmoke() {
    TypedPopulationChecks checks;
    SealedFixture fixture;
    if (!BuildSealedFixture(L"typed", &fixture) || !fixture.published ||
        !fixture.decoded) {
        return checks;
    }
    const Population population = Analyze(fixture.records);
    checks.manifests = population.manifests;
    checks.nodes = population.nodes;
    checks.edges = population.edges;
    checks.properties = population.properties;
    checks.coverageRecords = population.coverageRecords;
    checks.recordKinds = population.manifests == 1 && population.nodes > 1 &&
        population.edges > 0 && population.properties > 0 &&
        population.coverageRecords > 0;
    using hancom::graph::NodeKind;
    checks.nodeKinds = KindCount(population, NodeKind::Story) >= 1 &&
        KindCount(population, NodeKind::Section) >= 1 &&
        KindCount(population, NodeKind::Paragraph) >= 1 &&
        KindCount(population, NodeKind::CharacterRun) >= 1 &&
        KindCount(population, NodeKind::Table) >= 1 &&
        KindCount(population, NodeKind::TableCell) >= 1 &&
        KindCount(population, NodeKind::Image) >= 1;
    checks.nodeIdsValid = population.decodedNodeEnvelopes &&
        !population.nodeIds.empty() && population.allNodeIdsRfc4122V4;
    checks.documentEnvelope = population.documentPresent &&
        population.documentFingerprintNonzero &&
        population.documentLocatorNotApplicable &&
        population.containsFromDocument > 0;
    checks.terminals = population.imageBinaryNotExposed &&
        population.readFailedPropertyPresent;
    const hancom::graph::Sha256 zero{};
    checks.roots = !hancom::graph::codec::Equal(
                       fixture.firstManifest.observedSemanticRoot, zero) &&
        hancom::graph::codec::Equal(
            fixture.firstManifest.observedSemanticRoot,
            fixture.secondManifest.observedSemanticRoot) &&
        hancom::graph::codec::Equal(
            fixture.firstManifest.captureRoot,
            fixture.secondManifest.captureRoot);
    checks.idReuse = !fixture.firstAttemptNodeIds.empty() &&
        fixture.firstAttemptNodeIds == fixture.secondAttemptNodeIds &&
        fixture.secondAttemptNodeIds == population.nodeIds;
    checks.notOpaqueOnly = !(population.nodes <= 1 &&
                             population.edges == 0 &&
                             population.properties == 0);
    return checks;
}

// ---- Strict typed-emission regression (reopened fix cycle) ----

std::uint32_t R32(const std::uint8_t* const bytes) noexcept {
    return static_cast<std::uint32_t>(
        bytes[0] | (static_cast<std::uint32_t>(bytes[1]) << 8) |
        (static_cast<std::uint32_t>(bytes[2]) << 16) |
        (static_cast<std::uint32_t>(bytes[3]) << 24));
}

std::wstring Utf16Text(const std::vector<std::uint8_t>& value) {
    if (value.size() < 8) {
        return {};
    }
    const std::uint64_t count = R64(value.data());
    if (value.size() != 8 + static_cast<size_t>(count) * 2) {
        return {};
    }
    std::wstring text;
    for (std::uint64_t index = 0; index < count; ++index) {
        text.push_back(static_cast<wchar_t>(
            R16(value.data() + 8 + static_cast<size_t>(index) * 2)));
    }
    return text;
}

bool ObservationPayload(
    const std::vector<std::uint8_t>& envelope,
    std::vector<std::uint8_t>* const value) {
    if (envelope.size() < 24 || envelope[0] != 0) {
        return false;
    }
    const std::uint64_t detail = R64(envelope.data() + 8);
    const std::uint64_t bytes = R64(envelope.data() + 16);
    const size_t start = static_cast<size_t>(24 + detail * 2);
    if (envelope.size() != start + static_cast<size_t>(bytes)) {
        return false;
    }
    value->assign(envelope.begin() + static_cast<std::ptrdiff_t>(start),
                  envelope.end());
    return true;
}

std::vector<std::uint64_t> DecodeIdArray(
    const std::vector<std::uint8_t>& value) {
    std::vector<std::uint64_t> ids;
    if (value.size() < 16) {
        return ids;
    }
    const std::uint64_t count = R64(value.data() + 8);
    size_t at = 16;
    for (std::uint64_t index = 0; index < count; ++index) {
        if (value.size() < at + 16 || R64(value.data() + at) != 8) {
            return {};
        }
        ids.push_back(R64(value.data() + at + 8));
        at += 16;
    }
    return at == value.size() ? ids : std::vector<std::uint64_t>{};
}

struct StrictNode final {
    std::uint64_t recordId = 0;
    NodeIdBytes id{};
    std::uint16_t kind = 0;
    bool parentPresent = false;
    NodeIdBytes parent{};
    std::uint8_t locatorState = 255;
    std::uint16_t locatorTag = 255;
    std::vector<std::uint8_t> fingerprint{};
    std::vector<DecodedField> common{};
    std::vector<DecodedField> payload{};
};

struct StrictEdge final {
    std::uint64_t recordId = 0;
    std::uint16_t kind = 0;
    NodeIdBytes source{};
    NodeIdBytes target{};
    std::uint64_t ordinal = 0;
};

struct StrictProperty final {
    std::uint64_t recordId = 0;
    NodeIdBytes owner{};
    std::uint16_t ownerField = 0;
    std::uint32_t key = 0;
    std::uint8_t state = 255;
    std::uint8_t origin = 255;
    std::uint16_t scalar = 0;
    std::vector<std::uint8_t> semanticValue{};
};

bool ParseStrict(
    const std::vector<DecodedRecord>& records,
    std::vector<StrictNode>* const nodes,
    std::vector<StrictEdge>* const edges,
    std::vector<StrictProperty>* const properties) {
    using hancom::graph::RecordKind;
    for (const DecodedRecord& record : records) {
        const auto kind = static_cast<RecordKind>(record.kind);
        if (kind == RecordKind::Node) {
            const DecodedField* const commonField =
                FindField(record.fields, 1);
            const DecodedField* const payloadField =
                FindField(record.fields, 2);
            StrictNode node;
            node.recordId = record.id;
            if (commonField == nullptr || payloadField == nullptr ||
                !ParseFieldStream(
                    commonField->value.data(),
                    commonField->value.size(),
                    &node.common) ||
                !ParseFieldStream(
                    payloadField->value.data(),
                    payloadField->value.size(),
                    &node.payload)) {
                return false;
            }
            const DecodedField* const id = FindField(node.common, 1);
            const DecodedField* const kindField = FindField(node.common, 2);
            const DecodedField* const parent = FindField(node.common, 3);
            const DecodedField* const locator = FindField(node.common, 7);
            const DecodedField* const fingerprint =
                FindField(node.common, 8);
            if (id == nullptr || id->value.size() != 16 ||
                kindField == nullptr || kindField->value.size() != 2 ||
                locator == nullptr || locator->value.empty() ||
                fingerprint == nullptr || fingerprint->value.size() != 32) {
                return false;
            }
            std::copy_n(id->value.begin(), 16, node.id.begin());
            node.kind = R16(kindField->value.data());
            if (parent != nullptr && parent->value.size() == 16) {
                node.parentPresent = true;
                std::copy_n(parent->value.begin(), 16, node.parent.begin());
            }
            node.locatorState = locator->value[0];
            std::vector<std::uint8_t> locatorValue;
            if (node.locatorState == 0 &&
                ObservationPayload(locator->value, &locatorValue) &&
                locatorValue.size() >= 2) {
                node.locatorTag = R16(locatorValue.data());
            }
            node.fingerprint = fingerprint->value;
            nodes->push_back(std::move(node));
        } else if (kind == RecordKind::Edge) {
            const DecodedField* const kindField =
                FindField(record.fields, 1);
            const DecodedField* const source = FindField(record.fields, 2);
            const DecodedField* const target = FindField(record.fields, 3);
            const DecodedField* const ordinal = FindField(record.fields, 4);
            if (kindField == nullptr || kindField->value.size() != 2 ||
                source == nullptr || source->value.size() != 16 ||
                target == nullptr || target->value.size() != 16 ||
                ordinal == nullptr || ordinal->value.size() != 8) {
                return false;
            }
            StrictEdge edge;
            edge.recordId = record.id;
            edge.kind = R16(kindField->value.data());
            edge.ordinal = R64(ordinal->value.data());
            std::copy_n(source->value.begin(), 16, edge.source.begin());
            std::copy_n(target->value.begin(), 16, edge.target.begin());
            edges->push_back(edge);
        } else if (kind == RecordKind::Property) {
            const DecodedField* const owner = FindField(record.fields, 1);
            const DecodedField* const field = FindField(record.fields, 2);
            const DecodedField* const key = FindField(record.fields, 3);
            const DecodedField* const observation =
                FindField(record.fields, 4);
            const DecodedField* const origin = FindField(record.fields, 5);
            if (owner == nullptr || owner->value.size() != 16 ||
                field == nullptr || field->value.size() != 2 ||
                key == nullptr || key->value.size() != 4 ||
                observation == nullptr || observation->value.empty() ||
                origin == nullptr || origin->value.size() != 1) {
                return false;
            }
            StrictProperty property;
            property.recordId = record.id;
            std::copy_n(owner->value.begin(), 16, property.owner.begin());
            property.ownerField = R16(field->value.data());
            property.key = R32(key->value.data());
            property.state = observation->value[0];
            property.origin = origin->value[0];
            property.scalar = observation->scalar;
            if (property.state == static_cast<std::uint8_t>(
                    hancom::graph::ObservationState::Value) &&
                observation->value.size() >= 24) {
                const std::uint64_t detailUnits =
                    R64(observation->value.data() + 8);
                const size_t valueOffset = static_cast<size_t>(
                    24 + detailUnits * 2);
                if (valueOffset > observation->value.size()) return false;
                property.semanticValue.assign(
                    observation->value.begin() + valueOffset,
                    observation->value.end());
            }
            properties->push_back(property);
        }
    }
    return true;
}

const StrictNode* FindNode(
    const std::vector<StrictNode>& nodes,
    const NodeIdBytes& id) {
    for (const StrictNode& node : nodes) {
        if (node.id == id) {
            return &node;
        }
    }
    return nullptr;
}

std::wstring PayloadObservedText(
    const StrictNode& node,
    const std::uint16_t tag) {
    const DecodedField* const field = FindField(node.payload, tag);
    std::vector<std::uint8_t> value;
    if (field == nullptr || !ObservationPayload(field->value, &value)) {
        return {};
    }
    return Utf16Text(value);
}

bool PayloadUint64(
    const StrictNode& node,
    const std::uint16_t tag,
    std::uint64_t* const value) {
    const DecodedField* const field = FindField(node.payload, tag);
    if (field == nullptr || field->value.size() != 8) {
        return false;
    }
    *value = R64(field->value.data());
    return true;
}

struct StrictTypedChecks final {
    bool built = false;
    bool exactCounts = false;
    bool locators = false;
    bool storyNativeLists = false;
    bool independentBodyProvenance = false;
    bool nativeOrdinals = false;
    bool instanceIds = false;
    bool nestedOwnership = false;
    bool containsCardinality = false;
    bool anchorsCardinality = false;
    std::uint64_t genericControlAnchors = 0;
    std::uint64_t tableAnchors = 0;
    std::uint64_t imageAnchors = 0;
    std::uint64_t nonControlAnchors = 0;
    bool propertyOrdering = false;
    bool layoutProperties = false;
    bool effectiveTerminals = false;
    bool planFingerprint = false;
    bool freshArenaRetention = false;

    bool All() const noexcept {
        return built && exactCounts && locators && storyNativeLists &&
            independentBodyProvenance && nativeOrdinals && instanceIds &&
            nestedOwnership &&
            containsCardinality &&
            anchorsCardinality && propertyOrdering && layoutProperties &&
            effectiveTerminals && planFingerprint && freshArenaRetention;
    }
};

// Reopened-cycle regression: exact fixture population/cardinality, real
// per-kind NativeLocatorV1 tags, native HeadCtrl ordinals, persisted
// instance identity, nested table ownership through D3's cell story,
// bag-indexed property ordering, layout Property 12000/12001 facts,
// honest terminal observations, plan-defined Common.8 Document
// fingerprint, and NodeId retention across an independent recapture with a
// fresh arena seeded from the sealed stream (no shared in-memory arena).
StrictTypedChecks StrictTypedEmissionSmoke() {
    StrictTypedChecks checks;
    SealedFixture fixture;
    if (!BuildSealedFixture(L"strict", &fixture) || !fixture.published ||
        !fixture.decoded) {
        return checks;
    }
    checks.built = true;
    const Population population = Analyze(fixture.records);
    std::vector<StrictNode> nodes;
    std::vector<StrictEdge> edges;
    std::vector<StrictProperty> properties;
    if (!ParseStrict(fixture.records, &nodes, &edges, &properties)) {
        return checks;
    }
    using hancom::graph::EdgeKind;
    using hancom::graph::NodeKind;
    const auto kindCount = [&nodes](const NodeKind kind) {
        std::uint64_t count = 0;
        for (const StrictNode& node : nodes) {
            if (node.kind == static_cast<std::uint16_t>(kind)) {
                ++count;
            }
        }
        return count;
    };
    // Exact fixture population: 2 sections, 3 body paragraphs/runs plus
    // one real story/paragraph/run for each of 32 physical cells, one
    // generic control, two tables and one image. Counts are decoded from
    // records.hgn rather than inferred from the reader fixture.
    checks.exactCounts = fixture.records.size() == 621 &&
        nodes.size() == 143 && edges.size() == 213 &&
        properties.size() == 221 && population.coverageRecords == 43 &&
        kindCount(NodeKind::Document) == 1 &&
        kindCount(NodeKind::Story) == 33 &&
        kindCount(NodeKind::Section) == 2 &&
        kindCount(NodeKind::Paragraph) == 35 &&
        kindCount(NodeKind::CharacterRun) == 35 &&
        kindCount(NodeKind::Definition) == 1 &&
        kindCount(NodeKind::GenericControl) == 1 &&
        kindCount(NodeKind::Table) == 2 &&
        kindCount(NodeKind::TableCell) == 32 &&
        kindCount(NodeKind::Image) == 1;
    // Real NativeLocatorV1 observations with the schema tag per kind.
    checks.locators = true;
    for (const StrictNode& node : nodes) {
        const auto kind = static_cast<NodeKind>(node.kind);
        std::uint16_t wanted = 255;
        if (kind == NodeKind::Story) {
            wanted = 1;
        } else if (kind == NodeKind::Paragraph) {
            wanted = 2;
        } else if (kind == NodeKind::CharacterRun) {
            wanted = 3;
        } else if (kind == NodeKind::GenericControl ||
                   kind == NodeKind::Table || kind == NodeKind::Image) {
            wanted = 4;
        } else if (kind == NodeKind::TableCell) {
            wanted = 5;
        }
        if (wanted == 255) {
            continue;
        }
        if (node.locatorState != 0 || node.locatorTag != wanted) {
            checks.locators = false;
        }
    }
    // Story.102 must be a Value copied from an independently observed native
    // child-list header. The body observation is deliberately nonzero so a
    // hardcoded body locator cannot satisfy this provenance assertion.
    checks.storyNativeLists = true;
    std::uint64_t bodyStories = 0;
    std::uint64_t cellStories = 0;
    std::uint64_t captionStories = 0;
    constexpr std::int64_t observedBodyList = 947;
    for (const StrictNode& node : nodes) {
        if (node.kind != static_cast<std::uint16_t>(NodeKind::Story)) {
            continue;
        }
        const DecodedField* const locator = FindField(node.common, 7);
        const DecodedField* const kind = FindField(node.payload, 100);
        const DecodedField* const observed = FindField(node.payload, 102);
        std::vector<std::uint8_t> locatorValue;
        std::vector<std::uint8_t> observedValue;
        const StrictNode* const parent = node.parentPresent
            ? FindNode(nodes, node.parent)
            : nullptr;
        checks.storyNativeLists = checks.storyNativeLists &&
            locator != nullptr && kind != nullptr &&
            kind->value.size() == 2 && observed != nullptr &&
            ObservationPayload(locator->value, &locatorValue) &&
            ObservationPayload(observed->value, &observedValue) &&
            locatorValue.size() == 16 && R16(locatorValue.data()) == 1 &&
            observedValue.size() == 8 &&
            std::equal(observedValue.begin(), observedValue.end(),
                       locatorValue.begin() + 8) &&
            parent != nullptr;
        if (parent != nullptr && parent->kind ==
                static_cast<std::uint16_t>(NodeKind::Document)) {
            const bool independentlyPreserved =
                observedValue.size() == 8 && observedBodyList != 0 &&
                static_cast<std::int64_t>(R64(observedValue.data())) ==
                    observedBodyList;
            checks.storyNativeLists = checks.storyNativeLists &&
                independentlyPreserved;
            checks.independentBodyProvenance = independentlyPreserved;
            checks.storyNativeLists = checks.storyNativeLists &&
                R16(kind->value.data()) ==
                    static_cast<std::uint16_t>(
                        hancom::graph::StoryKind::Body);
            ++bodyStories;
        } else if (parent != nullptr && parent->kind ==
                       static_cast<std::uint16_t>(NodeKind::TableCell)) {
            checks.storyNativeLists = checks.storyNativeLists &&
                R16(kind->value.data()) ==
                    static_cast<std::uint16_t>(
                        hancom::graph::StoryKind::TableCell);
            ++cellStories;
        } else if (parent != nullptr && parent->kind ==
                       static_cast<std::uint16_t>(NodeKind::Image)) {
            checks.storyNativeLists = checks.storyNativeLists &&
                R16(kind->value.data()) ==
                    static_cast<std::uint16_t>(
                        hancom::graph::StoryKind::Caption);
            ++captionStories;
        } else {
            checks.storyNativeLists = false;
        }
    }
    checks.storyNativeLists = checks.storyNativeLists &&
        bodyStories == 1 && cellStories == 32 && captionStories == 0;
    // Native HeadCtrl ordinals from the story-spine observations.
    std::vector<std::uint64_t> tableOrdinals;
    std::uint64_t imageOrdinal = 0;
    std::uint64_t controlOrdinal = 0;
    bool ordinalsRead = true;
    for (const StrictNode& node : nodes) {
        std::uint64_t ordinal = 0;
        if (node.kind == static_cast<std::uint16_t>(NodeKind::Table)) {
            if (PayloadUint64(node, 105, &ordinal)) {
                tableOrdinals.push_back(ordinal);
            } else {
                ordinalsRead = false;
            }
        } else if (node.kind ==
                   static_cast<std::uint16_t>(NodeKind::Image)) {
            ordinalsRead =
                PayloadUint64(node, 105, &imageOrdinal) && ordinalsRead;
        } else if (node.kind ==
                   static_cast<std::uint16_t>(NodeKind::GenericControl)) {
            ordinalsRead =
                PayloadUint64(node, 105, &controlOrdinal) && ordinalsRead;
        }
    }
    std::sort(tableOrdinals.begin(), tableOrdinals.end());
    checks.nativeOrdinals = ordinalsRead &&
        tableOrdinals == std::vector<std::uint64_t>{3, 7} &&
        imageOrdinal == 9 && controlOrdinal == 5;
    // Observed session instance identity persists as Control.101 values.
    std::vector<std::wstring> tableInstances;
    std::wstring imageInstance;
    for (const StrictNode& node : nodes) {
        if (node.kind == static_cast<std::uint16_t>(NodeKind::Table)) {
            tableInstances.push_back(PayloadObservedText(node, 101));
        } else if (node.kind ==
                   static_cast<std::uint16_t>(NodeKind::Image)) {
            imageInstance = PayloadObservedText(node, 101);
        }
    }
    std::sort(tableInstances.begin(), tableInstances.end());
    checks.instanceIds =
        tableInstances ==
            std::vector<std::wstring>{L"tbl-nested", L"tbl-outer"} &&
        imageInstance == L"img-4633";
    // Nested table ownership: D3 cell -> Contains -> Story(TableCell) ->
    // Contains -> Paragraph -> Contains -> nested table; OwnerStory
    // story -> cell; TableCell.107 carries the story id.
    const StrictNode* outerTable = nullptr;
    const StrictNode* hostCell = nullptr;
    for (const StrictNode& node : nodes) {
        if (node.kind == static_cast<std::uint16_t>(NodeKind::Table) &&
            PayloadObservedText(node, 101) == L"tbl-outer") {
            outerTable = &node;
        }
    }
    for (const StrictNode& node : nodes) {
        if (node.kind == static_cast<std::uint16_t>(NodeKind::TableCell) &&
            outerTable != nullptr && node.parentPresent &&
            node.parent == outerTable->id &&
            PayloadObservedText(node, 101) == L"D3") {
            hostCell = &node;
        }
    }
    const StrictNode* cellStory = nullptr;
    if (hostCell != nullptr) {
        for (const StrictNode& node : nodes) {
            if (node.kind == static_cast<std::uint16_t>(NodeKind::Story) &&
                node.parentPresent && node.parent == hostCell->id) {
                cellStory = &node;
            }
        }
    }
    const StrictNode* cellParagraph = nullptr;
    if (cellStory != nullptr) {
        for (const StrictNode& node : nodes) {
            if (node.kind ==
                    static_cast<std::uint16_t>(NodeKind::Paragraph) &&
                node.parentPresent && node.parent == cellStory->id) {
                cellParagraph = &node;
            }
        }
    }
    const StrictNode* nestedTable = nullptr;
    if (cellParagraph != nullptr) {
        for (const StrictNode& node : nodes) {
            if (node.kind == static_cast<std::uint16_t>(NodeKind::Table) &&
                node.parentPresent && node.parent == cellParagraph->id) {
                nestedTable = &node;
            }
        }
    }
    bool ownerStoryEdge = false;
    if (cellStory != nullptr && hostCell != nullptr) {
        for (const StrictEdge& edge : edges) {
            if (edge.kind ==
                    static_cast<std::uint16_t>(EdgeKind::OwnerStory) &&
                edge.source == cellStory->id &&
                edge.target == hostCell->id) {
                ownerStoryEdge = true;
            }
        }
    }
    bool storyLinked = false;
    if (hostCell != nullptr && cellStory != nullptr) {
        const DecodedField* const storyRef =
            FindField(hostCell->payload, 107);
        std::vector<std::uint8_t> value;
        storyLinked = storyRef != nullptr &&
            ObservationPayload(storyRef->value, &value) &&
            value.size() == 16 &&
            std::equal(value.begin(), value.end(), cellStory->id.begin());
    }
    std::uint64_t outerCells = 0;
    std::uint64_t nestedCells = 0;
    for (const StrictNode& node : nodes) {
        if (node.kind != static_cast<std::uint16_t>(NodeKind::TableCell) ||
            !node.parentPresent) {
            continue;
        }
        if (outerTable != nullptr && node.parent == outerTable->id) {
            ++outerCells;
        }
        if (nestedTable != nullptr && node.parent == nestedTable->id) {
            ++nestedCells;
        }
    }
    checks.nestedOwnership = nestedTable != nullptr && ownerStoryEdge &&
        storyLinked &&
        PayloadObservedText(*nestedTable, 101) == L"tbl-nested" &&
        outerCells == 28 && nestedCells == 4;
    // Exactly one incoming Contains per non-Document node; none for the
    // Document; parent field must agree with the Contains source.
    checks.containsCardinality = true;
    for (const StrictNode& node : nodes) {
        std::uint64_t incoming = 0;
        bool parentAgrees = true;
        for (const StrictEdge& edge : edges) {
            if (edge.kind ==
                    static_cast<std::uint16_t>(EdgeKind::Contains) &&
                edge.target == node.id) {
                ++incoming;
                parentAgrees = parentAgrees && node.parentPresent &&
                    edge.source == node.parent;
            }
        }
        const bool document =
            node.kind == static_cast<std::uint16_t>(NodeKind::Document);
        if (document ? (incoming != 0 || node.parentPresent)
                     : (incoming != 1 || !node.parentPresent ||
                        !parentAgrees)) {
            checks.containsCardinality = false;
        }
    }
    // Every serialized control/table/image has exactly one Anchors edge to
    // its canonical containing Paragraph; every non-control has none.
    checks.anchorsCardinality = true;
    for (const StrictNode& node : nodes) {
        const auto kind = static_cast<NodeKind>(node.kind);
        const bool control = kind == NodeKind::GenericControl ||
            kind == NodeKind::Table || kind == NodeKind::Image;
        std::uint64_t outgoing = 0;
        bool toParentParagraph = node.parentPresent;
        for (const StrictEdge& edge : edges) {
            if (edge.kind ==
                    static_cast<std::uint16_t>(EdgeKind::Anchors) &&
                edge.source == node.id) {
                ++outgoing;
                toParentParagraph = toParentParagraph &&
                    edge.target == node.parent;
            }
        }
        if (kind == NodeKind::GenericControl) {
            checks.genericControlAnchors += outgoing;
        } else if (kind == NodeKind::Table) {
            checks.tableAnchors += outgoing;
        } else if (kind == NodeKind::Image) {
            checks.imageAnchors += outgoing;
        } else {
            checks.nonControlAnchors += outgoing;
        }
        if (control ? (outgoing != 1 || !toParentParagraph)
                    : outgoing != 0) {
            checks.anchorsCardinality = false;
        }
    }
    checks.anchorsCardinality = checks.anchorsCardinality &&
        checks.genericControlAnchors == 1 && checks.tableAnchors == 2 &&
        checks.imageAnchors == 1 && checks.nonControlAnchors == 0;
    // Standalone Property records must equal their owners' bag arrays and
    // sort by (owner field, key).
    checks.propertyOrdering = true;
    for (const StrictNode& node : nodes) {
        std::vector<std::uint64_t> expected;
        const auto appendBag = [&expected](const DecodedField* const field) {
            if (field == nullptr || (field->flags & 0x0002) == 0) {
                return;
            }
            const std::vector<std::uint64_t> ids =
                DecodeIdArray(field->value);
            expected.insert(expected.end(), ids.begin(), ids.end());
        };
        appendBag(FindField(node.common, 10));
        appendBag(FindField(node.common, 11));
        for (const DecodedField& field : node.payload) {
            if ((field.flags & 0x0002) != 0) {
                appendBag(&field);
            }
        }
        std::vector<std::uint64_t> actual;
        std::uint16_t priorField = 0;
        std::uint32_t priorKey = 0;
        bool first = true;
        for (const StrictProperty& property : properties) {
            if (property.owner != node.id) {
                continue;
            }
            actual.push_back(property.recordId);
            if (!first &&
                (property.ownerField < priorField ||
                 (property.ownerField == priorField &&
                  property.key <= priorKey))) {
                checks.propertyOrdering = false;
            }
            first = false;
            priorField = property.ownerField;
            priorKey = property.key;
        }
        std::sort(expected.begin(), expected.end());
        std::sort(actual.begin(), actual.end());
        if (expected != actual) {
            checks.propertyOrdering = false;
        }
    }
    // Complete per-kind 12000-12003 facts under Common.10, including
    // explicit Unavailable geometry terminals.
    std::uint64_t layoutCount = 0;
    bool layoutValid = true;
    bool paragraphSpan = false;
    bool tableSpan = false;
    for (const StrictProperty& property : properties) {
        if (property.key < 12000 || property.key > 12003) continue;
        ++layoutCount;
        const StrictNode* const owner = FindNode(nodes, property.owner);
        const bool value = property.state == 0 && property.origin == 2;
        const bool unavailable = property.state == 2 && property.origin == 10;
        if (property.ownerField != 10 || (!value && !unavailable) ||
            owner == nullptr) {
            layoutValid = false;
            continue;
        }
        paragraphSpan = paragraphSpan ||
            owner->kind == static_cast<std::uint16_t>(NodeKind::Paragraph);
        tableSpan = tableSpan ||
            owner->kind == static_cast<std::uint16_t>(NodeKind::Table);
    }
    checks.layoutProperties =
        layoutValid && layoutCount == 150 && paragraphSpan && tableSpan;
    // Honest per-run closure: every CharacterRun has its own 1000 Value and
    // 1007 terminal observation plus exactly one CharacterShapeRef to the
    // one observed CharacterShape definition body.
    std::uint64_t charShapeValues = 0;
    std::uint64_t readFailed = 0;
    std::uint64_t metadataProperties = 0;
    for (const StrictProperty& property : properties) {
        if (property.ownerField == 100) {
            ++metadataProperties;
        }
        if (property.key == 1000 && property.ownerField == 102 &&
            property.state == 0) {
            const StrictNode* const owner = FindNode(nodes, property.owner);
            if (owner != nullptr &&
                owner->kind ==
                    static_cast<std::uint16_t>(NodeKind::CharacterRun)) {
                ++charShapeValues;
            }
        }
        if (property.key == 1007 && property.state == 3) {
            ++readFailed;
        }
    }
    std::set<NodeIdBytes> characterDefinitions;
    bool observedDefinitionBody = false;
    for (const StrictNode& node : nodes) {
        if (node.kind != static_cast<std::uint16_t>(NodeKind::Definition)) {
            continue;
        }
        const DecodedField* const kind = FindField(node.payload, 100);
        if (kind != nullptr && kind->value.size() == 2 &&
            R16(kind->value.data()) == static_cast<std::uint16_t>(
                hancom::graph::DefinitionKind::CharacterShape)) {
            characterDefinitions.insert(node.id);
        }
    }
    for (const StrictProperty& property : properties) {
        observedDefinitionBody = observedDefinitionBody ||
            characterDefinitions.find(property.owner) !=
                characterDefinitions.end() &&
            property.ownerField == 102 && property.key == 1000 &&
            property.state == static_cast<std::uint8_t>(
                hancom::graph::ObservationState::Value);
    }
    std::map<NodeIdBytes, size_t> runReferenceCounts;
    bool referenceTargetsExact = true;
    size_t characterReferences = 0;
    for (const StrictEdge& edge : edges) {
        if (edge.kind != static_cast<std::uint16_t>(
                EdgeKind::CharacterShapeRef)) {
            continue;
        }
        ++characterReferences;
        ++runReferenceCounts[edge.source];
        referenceTargetsExact = referenceTargetsExact &&
            characterDefinitions.find(edge.target) !=
                characterDefinitions.end();
    }
    for (const StrictNode& node : nodes) {
        if (node.kind == static_cast<std::uint16_t>(NodeKind::CharacterRun)) {
            referenceTargetsExact = referenceTargetsExact &&
                runReferenceCounts[node.id] == 1;
        }
    }
    checks.effectiveTerminals =
        charShapeValues == kindCount(NodeKind::CharacterRun) &&
        readFailed == kindCount(NodeKind::CharacterRun) &&
        characterDefinitions.size() == 1 && observedDefinitionBody &&
        characterReferences == kindCount(NodeKind::CharacterRun) &&
        referenceTargetsExact && metadataProperties == 0 &&
        population.imageBinaryNotExposed;
    // Plan-defined Common.8: the stored Document fingerprint must be the
    // exact preimage of the canonical observed semantic root.
    const StrictNode* documentNode = nullptr;
    for (const StrictNode& node : nodes) {
        if (node.kind == static_cast<std::uint16_t>(NodeKind::Document)) {
            documentNode = &node;
        }
    }
    if (documentNode != nullptr) {
        const hancom::graph::Sha256 derived =
            hancom::graph::codec::DomainHash(
                "HWPGRAPH\0SEMANTIC\0V1",
                {documentNode->fingerprint.data(),
                 documentNode->fingerprint.size()});
        checks.planFingerprint = std::equal(
            derived.bytes.begin(), derived.bytes.end(),
            fixture.firstManifest.observedSemanticRoot.bytes.begin());
    }
    // NodeId retention through the identity/reconciliation contract with a
    // fresh arena seeded from the sealed stream (no shared in-memory arena).
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) != 0) {
        const std::filesystem::path root =
            std::filesystem::path(temporary) /
            (L"hwp-graph-capture-reseed-" +
             std::to_wstring(GetCurrentProcessId()));
        std::error_code error;
        std::filesystem::remove_all(root, error);
        hancom::graph::capture::CaptureIdentityArena fresh;
        CaptureSpool reseeded;
        const std::vector<std::uint8_t> environment = NormativeEnvironment();
        std::vector<std::uint8_t> reseededBytes;
        std::vector<DecodedRecord> reseededRecords;
        const bool seeded =
            hancom::graph::capture::SeedArenaFromRecordStream(
                {fixture.sealedBytes.data(), fixture.sealedBytes.size()},
                &fresh);
        const bool rebuilt = seeded && reseeded.Build(
            root / L"attempt-2", SealedPayloads(), environment, fresh);
        const bool decoded = rebuilt && ReadWholeFile(
                root / L"attempt-2" / L"records.hgn", &reseededBytes) &&
            DecodeRecords(reseededBytes, &reseededRecords);
        checks.freshArenaRetention = decoded &&
            !population.nodeIds.empty() &&
            Analyze(reseededRecords).nodeIds == population.nodeIds;
        if (!checks.freshArenaRetention) {
            const auto now = Analyze(reseededRecords).nodeIds;
            size_t mismatch = 0;
            while (mismatch < now.size() &&
                   mismatch < population.nodeIds.size() &&
                   now[mismatch] == population.nodeIds[mismatch]) {
                ++mismatch;
            }
            std::wcout << L"GRAPH_CAPTURE_RECONCILIATION_DETAIL seeded="
                       << seeded << L" rebuilt=" << rebuilt
                       << L" decoded=" << decoded
                       << L" mismatch=" << mismatch << L'\n';
        }
        reseeded.Reset();
        std::filesystem::remove_all(root, error);
    }
    return checks;
}

bool LocatorMovementReconciliationSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-locator-movement-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    SealedFixture baseline;
    if (!BuildSealedFixture(L"movement-base", &baseline)) {
        return false;
    }
    std::vector<ReaderPayload> moved = SealedPayloads();
    for (auto& control : moved[0].controls) {
        control.headCtrlOrdinal += 100;
        control.anchor.character += 1;
        if (control.instanceId == L"tbl-nested") {
            control.anchor.list += 1000;
        }
    }
    moved[3].controls = moved[0].controls;
    for (auto& table : moved[4].tables) {
        const auto control = std::find_if(
            moved[0].controls.begin(), moved[0].controls.end(),
            [&table](const auto& candidate) {
                return candidate.instanceId == table.instanceId;
            });
        if (control == moved[0].controls.end()) return false;
        table.headCtrlOrdinal = control->headCtrlOrdinal;
        table.anchor = control->anchor;
        for (auto& cell : table.cells) cell.listId += 1000;
    }
    moved[5].images.front().headCtrlOrdinal =
        moved[0].controls.back().headCtrlOrdinal;
    moved[5].images.front().anchor = moved[0].controls.back().anchor;
    RecertifySyntheticLayout(&moved);
    hancom::graph::capture::CaptureIdentityArena arena;
    CaptureSpool movedSpool;
    const std::vector<std::uint8_t> environment = NormativeEnvironment();
    std::vector<std::uint8_t> movedBytes;
    std::vector<DecodedRecord> movedRecords;
    const bool movedBuilt =
        hancom::graph::capture::SeedArenaFromRecordStream(
            {baseline.sealedBytes.data(), baseline.sealedBytes.size()},
            &arena) &&
        movedSpool.Build(root / L"moved", moved, environment, arena) &&
        ReadWholeFile(root / L"moved" / L"records.hgn", &movedBytes) &&
        DecodeRecords(movedBytes, &movedRecords);
    std::vector<StrictNode> baselineNodes;
    std::vector<StrictNode> movedNodes;
    std::vector<StrictEdge> ignoredEdges;
    std::vector<StrictProperty> ignoredProperties;
    const bool parsed = movedBuilt &&
        ParseStrict(baseline.records, &baselineNodes, &ignoredEdges,
                    &ignoredProperties);
    ignoredEdges.clear();
    ignoredProperties.clear();
    const bool movedParsed = parsed &&
        ParseStrict(movedRecords, &movedNodes, &ignoredEdges,
                    &ignoredProperties);
    const auto identities = [](const std::vector<StrictNode>& nodes) {
        std::vector<std::pair<std::wstring, NodeIdBytes>> values;
        const auto bytesKey = [](const NodeIdBytes& bytes) {
            std::wstring key;
            static constexpr wchar_t digits[] = L"0123456789abcdef";
            for (const std::uint8_t byte : bytes) {
                key.push_back(digits[byte >> 4]);
                key.push_back(digits[byte & 0x0f]);
            }
            return key;
        };
        for (const StrictNode& node : nodes) {
            const auto kind = static_cast<hancom::graph::NodeKind>(node.kind);
            if (kind == hancom::graph::NodeKind::GenericControl ||
                kind == hancom::graph::NodeKind::Table ||
                kind == hancom::graph::NodeKind::Image) {
                values.push_back({
                    std::to_wstring(node.kind) + L":" +
                        PayloadObservedText(node, 101),
                    node.id});
            } else if (kind == hancom::graph::NodeKind::TableCell &&
                       node.parentPresent) {
                values.push_back({
                    L"cell:" + bytesKey(node.parent) + L":" +
                        PayloadObservedText(node, 101),
                    node.id});
            }
        }
        std::sort(values.begin(), values.end(),
                  [](const auto& left, const auto& right) {
                      return left.first < right.first;
                  });
        return values;
    };
    const bool retained = movedParsed &&
        identities(baselineNodes) == identities(movedNodes) &&
        arena.RemapCount() != 0;

    std::vector<ReaderPayload> removed = moved;
    removed[0].controls.erase(
        std::remove_if(
            removed[0].controls.begin(), removed[0].controls.end(),
            [](const auto& control) {
                return control.instanceId == L"img-4633";
            }),
        removed[0].controls.end());
    removed[3].controls = removed[0].controls;
    removed[5].images.clear();
    RecertifySyntheticLayout(&removed);
    hancom::graph::capture::CaptureIdentityArena removalArena;
    CaptureSpool removedSpool;
    const bool removedBuilt = movedBuilt &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {movedBytes.data(), movedBytes.size()}, &removalArena) &&
        removedSpool.Build(
            root / L"removed", removed, environment, removalArena);
    const bool tombstoned = removedBuilt &&
        removalArena.TombstoneCount() >= 1;
    std::wcout << L"GRAPH_CAPTURE_RECONCILIATION_REMAP_ASSERTION "
               << retained << L'\n'
               << L"GRAPH_CAPTURE_RECONCILIATION_TOMBSTONE_ASSERTION "
               << tombstoned << L'\n';
    movedSpool.Reset();
    removedSpool.Reset();
    std::filesystem::remove_all(root, error);
    return retained && tombstoned;
}

bool EmptyDuplicateInstanceRecaptureIdentitySmoke() {
    using hancom::graph::EdgeKind;
    using hancom::graph::NodeKind;
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) return false;
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-empty-duplicate-recapture-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);

    std::vector<ReaderPayload> payloads = SealedPayloads();
    payloads[0].controls.push_back(
        {L"empty-native-recapture", L"", 41, {0, 1, 3}});
    payloads[0].controls.push_back(
        {L"empty-native-recapture", L"", 73, {0, 1, 7}});
    payloads[3].controls = payloads[0].controls;

    CaptureSpool first;
    CaptureSpool recaptured;
    hancom::graph::capture::CaptureIdentityArena firstArena;
    hancom::graph::capture::CaptureIdentityArena recaptureArena;
    std::vector<std::uint8_t> firstBytes;
    std::vector<std::uint8_t> recapturedBytes;
    std::vector<DecodedRecord> firstRecords;
    std::vector<DecodedRecord> recapturedRecords;
    const bool built =
        first.Build(root / L"first", payloads, NormativeEnvironment(),
                    firstArena) &&
        ReadWholeFile(root / L"first" / L"records.hgn", &firstBytes) &&
        DecodeRecords(firstBytes, &firstRecords) &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {firstBytes.data(), firstBytes.size()}, &recaptureArena) &&
        recaptured.Build(root / L"recaptured", payloads,
                         NormativeEnvironment(), recaptureArena) &&
        ReadWholeFile(root / L"recaptured" / L"records.hgn",
                      &recapturedBytes) &&
        DecodeRecords(recapturedBytes, &recapturedRecords);

    const auto inspect = [](const std::vector<DecodedRecord>& records) {
        std::map<std::uint64_t, NodeIdBytes> identities;
        std::vector<StrictNode> nodes;
        std::vector<StrictEdge> edges;
        std::vector<StrictProperty> properties;
        bool exactAnchors = ParseStrict(
            records, &nodes, &edges, &properties);
        for (const StrictNode& node : nodes) {
            if (node.kind != static_cast<std::uint16_t>(
                    NodeKind::GenericControl) ||
                PayloadObservedText(node, 100) !=
                    L"empty-native-recapture") {
                continue;
            }
            const DecodedField* const instance =
                FindField(node.payload, 101);
            std::uint64_t ordinal = 0;
            std::uint64_t anchors = 0;
            exactAnchors = exactAnchors && instance != nullptr &&
                !instance->value.empty() &&
                instance->value[0] == static_cast<std::uint8_t>(
                    hancom::graph::ObservationState::Value) &&
                PayloadObservedText(node, 101).empty() &&
                PayloadUint64(node, 105, &ordinal) && node.parentPresent;
            for (const StrictEdge& edge : edges) {
                if (edge.kind == static_cast<std::uint16_t>(
                        EdgeKind::Anchors) && edge.source == node.id) {
                    ++anchors;
                    exactAnchors = exactAnchors && edge.target == node.parent;
                }
            }
            exactAnchors = exactAnchors && anchors == 1 &&
                identities.emplace(ordinal, node.id).second;
        }
        return std::pair{identities, exactAnchors};
    };
    const auto firstView = inspect(firstRecords);
    const auto recapturedView = inspect(recapturedRecords);
    std::set<NodeIdBytes> firstIds;
    std::set<NodeIdBytes> recapturedIds;
    for (const auto& identity : firstView.first) {
        firstIds.insert(identity.second);
    }
    for (const auto& identity : recapturedView.first) {
        recapturedIds.insert(identity.second);
    }
    std::vector<NodeIdBytes> retained;
    std::set_intersection(
        firstIds.begin(), firstIds.end(),
        recapturedIds.begin(), recapturedIds.end(),
        std::back_inserter(retained));
    const bool ambiguousReminted = built && firstView.second &&
        recapturedView.second && firstView.first.size() == 2 &&
        recapturedView.first.size() == 2 && firstIds.size() == 2 &&
        recapturedIds.size() == 2 && retained.empty();
    std::wcout << L"GRAPH_CAPTURE_EMPTY_DUPLICATE_INSTANCE_RECAPTURE_IDENTITY "
               << ambiguousReminted << L" built=" << built
               << L" first_exact=" << firstView.second
               << L" recaptured_exact=" << recapturedView.second
               << L" first_ordinals=" << firstView.first.size()
               << L" recaptured_ordinals=" << recapturedView.first.size()
               << L" first_unique_ids=" << firstIds.size()
               << L" recaptured_unique_ids=" << recapturedIds.size()
               << L" retained_expected=0 retained_actual=" << retained.size()
               << L'\n';
    first.Reset();
    recaptured.Reset();
    std::filesystem::remove_all(root, error);
    return ambiguousReminted && !error && !std::filesystem::exists(root);
}

bool DuplicateEmptyIdentityTraversalReopenMovementSmoke() {
    using hancom::graph::EdgeKind;
    using hancom::graph::NodeKind;
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) return false;
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-duplicate-empty-identity-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code cleanupError;
    std::filesystem::remove_all(root, cleanupError);

    std::vector<ReaderPayload> payloads = SealedPayloads();
    const std::array<hancom::graph::capture::ControlObservation, 5> ambiguous{{
        {L"gso", L"", 10, {0, 1, 3}, false},
        {L"gso", L"duplicate", 11, {0, 1, 3}, false},
        {L"gso", L"duplicate", 12, {0, 1, 3}, false},
        {L"ctrl-a", L"shared-instance", 13, {0, 1, 3}, false},
        {L"ctrl-b", L"shared-instance", 14, {0, 1, 3}, false},
    }};
    payloads[0].controls.insert(
        payloads[0].controls.end(), ambiguous.begin(), ambiguous.end());
    payloads[3].controls = payloads[0].controls;
    const std::vector<std::uint8_t> environment = NormativeEnvironment();
    hancom::graph::capture::CaptureIdentityArena traversalArena;
    CaptureSpool first;
    CaptureSpool second;
    CaptureSpool reopened;
    CaptureSpool moved;
    CaptureSpool reordered;
    CaptureSpool split;
    CaptureSpool coalesced;
    std::vector<std::uint8_t> firstBytes;
    std::vector<std::uint8_t> secondBytes;
    std::vector<std::uint8_t> reopenedBytes;
    std::vector<std::uint8_t> movedBytes;
    std::vector<std::uint8_t> reorderedBytes;
    std::vector<std::uint8_t> splitBytes;
    std::vector<std::uint8_t> coalescedBytes;
    std::vector<DecodedRecord> firstRecords;
    std::vector<DecodedRecord> secondRecords;
    std::vector<DecodedRecord> reopenedRecords;
    std::vector<DecodedRecord> movedRecords;
    std::vector<DecodedRecord> reorderedRecords;
    std::vector<DecodedRecord> splitRecords;
    std::vector<DecodedRecord> coalescedRecords;
    const bool traversedTwice =
        first.Build(root / L"traversal-1", payloads, environment,
                    traversalArena) &&
        second.Build(root / L"traversal-2", payloads, environment,
                     traversalArena) &&
        ReadWholeFile(root / L"traversal-1" / L"records.hgn", &firstBytes) &&
        ReadWholeFile(root / L"traversal-2" / L"records.hgn", &secondBytes) &&
        DecodeRecords(firstBytes, &firstRecords) &&
        DecodeRecords(secondBytes, &secondRecords);

    hancom::graph::capture::CaptureIdentityArena reopenArena;
    const bool reopenedFresh = traversedTwice &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {secondBytes.data(), secondBytes.size()}, &reopenArena) &&
        reopened.Build(root / L"reopened", payloads, environment,
                       reopenArena) &&
        ReadWholeFile(root / L"reopened" / L"records.hgn", &reopenedBytes) &&
        DecodeRecords(reopenedBytes, &reopenedRecords);

    std::vector<ReaderPayload> movedPayloads = payloads;
    for (auto& control : movedPayloads[0].controls) {
        if (control.ctrlId == L"gso" &&
            (control.instanceId.empty() || control.instanceId == L"duplicate")) {
            control.headCtrlOrdinal += 100;
            control.anchor.character += 10;
        }
    }
    movedPayloads[3].controls = movedPayloads[0].controls;
    hancom::graph::capture::CaptureIdentityArena movementArena;
    const bool movedFresh = reopenedFresh &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {reopenedBytes.data(), reopenedBytes.size()}, &movementArena) &&
        moved.Build(root / L"moved", movedPayloads, environment,
                    movementArena) &&
        ReadWholeFile(root / L"moved" / L"records.hgn", &movedBytes) &&
        DecodeRecords(movedBytes, &movedRecords);

    std::vector<ReaderPayload> reorderedPayloads = movedPayloads;
    std::reverse(reorderedPayloads[0].controls.end() - 3,
                 reorderedPayloads[0].controls.end());
    reorderedPayloads[3].controls = reorderedPayloads[0].controls;
    hancom::graph::capture::CaptureIdentityArena reorderArena;
    const bool reorderedFresh = movedFresh &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {movedBytes.data(), movedBytes.size()}, &reorderArena) &&
        reordered.Build(root / L"reordered", reorderedPayloads, environment,
                        reorderArena) &&
        ReadWholeFile(root / L"reordered" / L"records.hgn",
                      &reorderedBytes) &&
        DecodeRecords(reorderedBytes, &reorderedRecords);

    std::vector<ReaderPayload> splitPayloads = reorderedPayloads;
    splitPayloads[0].controls.push_back(
        {L"gso", L"duplicate", 113, {0, 1, 13}, false});
    splitPayloads[3].controls = splitPayloads[0].controls;
    hancom::graph::capture::CaptureIdentityArena splitArena;
    const bool splitFresh = reorderedFresh &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {reorderedBytes.data(), reorderedBytes.size()}, &splitArena) &&
        split.Build(root / L"split", splitPayloads, environment, splitArena) &&
        ReadWholeFile(root / L"split" / L"records.hgn", &splitBytes) &&
        DecodeRecords(splitBytes, &splitRecords);

    std::vector<ReaderPayload> coalescedPayloads = splitPayloads;
    auto& coalescedControls = coalescedPayloads[0].controls;
    bool keptDuplicate = false;
    coalescedControls.erase(
        std::remove_if(
            coalescedControls.begin(), coalescedControls.end(),
            [&keptDuplicate](const auto& control) {
                if (control.ctrlId != L"gso" ||
                    control.instanceId != L"duplicate") {
                    return false;
                }
                if (!keptDuplicate) {
                    keptDuplicate = true;
                    return false;
                }
                return true;
            }),
        coalescedControls.end());
    coalescedPayloads[3].controls = coalescedControls;
    hancom::graph::capture::CaptureIdentityArena coalescedArena;
    const bool coalescedFresh = splitFresh &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {splitBytes.data(), splitBytes.size()}, &coalescedArena) &&
        coalesced.Build(root / L"coalesced", coalescedPayloads, environment,
                        coalescedArena) &&
        ReadWholeFile(root / L"coalesced" / L"records.hgn",
                      &coalescedBytes) &&
        DecodeRecords(coalescedBytes, &coalescedRecords);

    struct IdentityView final {
        std::vector<NodeIdBytes> ids;
        NodeIdBytes uniqueControl{};
        bool uniqueControlPresent = false;
        bool canonicalAnchors = false;
        bool emptyPresent = false;
        std::uint64_t duplicateCount = 0;
    };
    const auto inspect = [](const std::vector<DecodedRecord>& records) {
        IdentityView view;
        std::vector<StrictNode> nodes;
        std::vector<StrictEdge> edges;
        std::vector<StrictProperty> properties;
        if (!ParseStrict(records, &nodes, &edges, &properties)) return view;
        std::vector<std::pair<std::uint64_t, const StrictNode*>> selected;
        for (const StrictNode& node : nodes) {
            if (node.kind != static_cast<std::uint16_t>(
                    NodeKind::GenericControl)) {
                continue;
            }
            const std::wstring ctrlId = PayloadObservedText(node, 100);
            if (ctrlId != L"gso") {
                continue;
            }
            std::uint64_t ordinal = 0;
            if (!PayloadUint64(node, 105, &ordinal)) return IdentityView{};
            const std::wstring instance = PayloadObservedText(node, 101);
            const DecodedField* const instanceField =
                FindField(node.payload, 101);
            const bool emptyIdentity = instance.empty() &&
                instanceField != nullptr && !instanceField->value.empty() &&
                instanceField->value[0] == static_cast<std::uint8_t>(
                    hancom::graph::ObservationState::Value);
            view.emptyPresent = view.emptyPresent || emptyIdentity;
            if (emptyIdentity) {
                if (view.uniqueControlPresent) return IdentityView{};
                view.uniqueControl = node.id;
                view.uniqueControlPresent = true;
            }
            if (instance == L"duplicate") ++view.duplicateCount;
            selected.push_back({ordinal, &node});
        }
        std::sort(selected.begin(), selected.end(),
                  [](const auto& left, const auto& right) {
                      return left.first < right.first;
                  });
        view.canonicalAnchors = !selected.empty();
        for (const auto& item : selected) {
            const StrictNode& node = *item.second;
            view.ids.push_back(node.id);
            std::uint64_t anchorCount = 0;
            for (const StrictEdge& edge : edges) {
                if (edge.kind == static_cast<std::uint16_t>(EdgeKind::Anchors) &&
                    edge.source == node.id) {
                    ++anchorCount;
                    view.canonicalAnchors = view.canonicalAnchors &&
                        node.parentPresent && edge.target == node.parent;
                }
            }
            view.canonicalAnchors =
                view.canonicalAnchors && anchorCount == 1;
        }
        return view;
    };
    const IdentityView firstView = inspect(firstRecords);
    const IdentityView secondView = inspect(secondRecords);
    const IdentityView reopenedView = inspect(reopenedRecords);
    const IdentityView movedView = inspect(movedRecords);
    const IdentityView reorderedView = inspect(reorderedRecords);
    const IdentityView splitView = inspect(splitRecords);
    const IdentityView coalescedView = inspect(coalescedRecords);
    const auto distinct = [](const std::vector<NodeIdBytes>& ids,
                             const size_t expected) {
        std::set<NodeIdBytes> unique(ids.begin(), ids.end());
        return ids.size() == expected && unique.size() == expected;
    };
    const bool noMerge = distinct(firstView.ids, 3) &&
        distinct(secondView.ids, 3) && distinct(reopenedView.ids, 3) &&
        distinct(movedView.ids, 3) && distinct(reorderedView.ids, 3) &&
        distinct(splitView.ids, 4) && distinct(coalescedView.ids, 2) &&
        firstView.emptyPresent && secondView.emptyPresent &&
        reopenedView.emptyPresent && movedView.emptyPresent &&
        reorderedView.emptyPresent && splitView.emptyPresent &&
        coalescedView.emptyPresent && firstView.duplicateCount == 2 &&
        secondView.duplicateCount == 2 && reopenedView.duplicateCount == 2 &&
        movedView.duplicateCount == 2 && reorderedView.duplicateCount == 2 &&
        splitView.duplicateCount == 3 && coalescedView.duplicateCount == 1;
    const auto nodeIdText = [](const NodeIdBytes& id) {
        std::wostringstream text;
        text << std::hex << std::setfill(L'0');
        for (const std::uint8_t value : id) {
            text << std::setw(2) << static_cast<unsigned>(value);
        }
        return text.str();
    };
    const auto retainedUniqueControl = [](
        const IdentityView& prior,
        const IdentityView& current) {
        const std::set<NodeIdBytes> priorIds(prior.ids.begin(), prior.ids.end());
        const std::set<NodeIdBytes> currentIds(
            current.ids.begin(), current.ids.end());
        std::set<NodeIdBytes> retained;
        std::set_intersection(
            priorIds.begin(), priorIds.end(), currentIds.begin(),
            currentIds.end(), std::inserter(retained, retained.end()));
        return prior.uniqueControlPresent && current.uniqueControlPresent &&
            prior.uniqueControl == current.uniqueControl &&
            retained == std::set<NodeIdBytes>{prior.uniqueControl};
    };
    const auto clientVisibleReceipts = [&nodeIdText](
        const wchar_t* const transition,
        const auto& records,
        const IdentityView& prior,
        const IdentityView& current,
        const std::uint64_t expectedFreshAmbiguous,
        const std::uint64_t expectedFreshExternal,
        const std::uint64_t expectedPrior) {
        std::vector<StrictNode> decodedNodes;
        std::vector<StrictEdge> decodedEdges;
        std::vector<StrictProperty> decodedProperties;
        if (!ParseStrict(
                records, &decodedNodes, &decodedEdges, &decodedProperties)) {
            return false;
        }
        const std::set<NodeIdBytes> priorIds(
            prior.ids.begin(), prior.ids.end());
        const std::set<NodeIdBytes> currentIds(
            current.ids.begin(), current.ids.end());
        std::set<NodeIdBytes> expectedFreshIds;
        std::set<NodeIdBytes> expectedPriorIds;
        std::set<NodeIdBytes> expectedRetainedIds;
        std::set_difference(
            currentIds.begin(), currentIds.end(), priorIds.begin(),
            priorIds.end(),
            std::inserter(expectedFreshIds, expectedFreshIds.end()));
        std::set_difference(
            priorIds.begin(), priorIds.end(), currentIds.begin(),
            currentIds.end(),
            std::inserter(expectedPriorIds, expectedPriorIds.end()));
        std::set_intersection(
            priorIds.begin(), priorIds.end(), currentIds.begin(),
            currentIds.end(),
            std::inserter(expectedRetainedIds, expectedRetainedIds.end()));
        const bool retainedExact = prior.uniqueControlPresent &&
            current.uniqueControlPresent &&
            prior.uniqueControl == current.uniqueControl &&
            expectedRetainedIds ==
                std::set<NodeIdBytes>{prior.uniqueControl};

        std::map<NodeIdBytes, std::uint64_t> tombstones;
        std::map<NodeIdBytes, hancom::graph::RemapReason> freshRemaps;
        std::set<NodeIdBytes> priorRemaps;
        std::uint64_t freshAmbiguous = 0;
        std::uint64_t freshExternal = 0;
        std::uint64_t remapRecords = 0;
        std::uint64_t tombstoneRecords = 0;
        bool canonical = true;
        bool haveTombstone = false;
        bool haveRemap = false;
        NodeIdBytes priorTombstone{};
        NodeIdBytes priorRemap{};
        for (const DecodedRecord& record : records) {
            if (record.kind == static_cast<std::uint16_t>(
                    hancom::graph::RecordKind::Tombstone)) {
                ++tombstoneRecords;
                const DecodedField* source = FindField(record.fields, 1);
                const DecodedField* revision = FindField(record.fields, 2);
                const DecodedField* reason = FindField(record.fields, 3);
                if (source == nullptr || source->value.size() != 16 ||
                    revision == nullptr || revision->value.size() != 8 ||
                    reason == nullptr || reason->value.size() != 2) {
                    return false;
                }
                NodeIdBytes id{};
                std::copy_n(source->value.begin(), id.size(), id.begin());
                const std::uint64_t semanticRevision =
                    R64(revision->value.data());
                canonical = canonical && (!haveTombstone ||
                    priorTombstone < id) && semanticRevision != 0 &&
                    R16(reason->value.data()) == static_cast<std::uint16_t>(
                        hancom::graph::TombstoneReason::ExternalUnmatched) &&
                    currentIds.find(id) == currentIds.end() &&
                    tombstones.emplace(id, semanticRevision).second;
                priorTombstone = id;
                haveTombstone = true;
            } else if (record.kind == static_cast<std::uint16_t>(
                           hancom::graph::RecordKind::Remap)) {
                ++remapRecords;
                const DecodedField* source = FindField(record.fields, 1);
                const DecodedField* target = FindField(record.fields, 2);
                const DecodedField* disposition = FindField(record.fields, 3);
                const DecodedField* reason = FindField(record.fields, 4);
                if (source == nullptr || source->value.size() != 16 ||
                    disposition == nullptr || disposition->value.size() != 1 ||
                    reason == nullptr || reason->value.size() != 2) {
                    return false;
                }
                NodeIdBytes id{};
                std::copy_n(source->value.begin(), id.size(), id.begin());
                canonical = canonical && (!haveRemap || priorRemap < id);
                priorRemap = id;
                haveRemap = true;
                const auto decodedDisposition = static_cast<
                    hancom::graph::RemapDisposition>(disposition->value[0]);
                const auto decodedReason = static_cast<
                    hancom::graph::RemapReason>(R16(reason->value.data()));
                if (decodedDisposition ==
                        hancom::graph::RemapDisposition::New) {
                    if (target == nullptr || target->value != source->value ||
                        (decodedReason != hancom::graph::RemapReason::Ambiguous &&
                         decodedReason !=
                             hancom::graph::RemapReason::ExternalUnique) ||
                        currentIds.find(id) == currentIds.end() ||
                        priorIds.find(id) != priorIds.end() ||
                        !freshRemaps.emplace(id, decodedReason).second) {
                        return false;
                    }
                    freshAmbiguous += decodedReason ==
                        hancom::graph::RemapReason::Ambiguous ? 1U : 0U;
                    freshExternal += decodedReason ==
                        hancom::graph::RemapReason::ExternalUnique ? 1U : 0U;
                } else if (decodedDisposition ==
                               hancom::graph::RemapDisposition::Ambiguous) {
                    if (target != nullptr || decodedReason !=
                            hancom::graph::RemapReason::Ambiguous ||
                        currentIds.find(id) != currentIds.end() ||
                        priorIds.find(id) == priorIds.end() ||
                        !priorRemaps.insert(id).second) {
                        return false;
                    }
                } else {
                    return false;
                }
            }
        }
        std::set<NodeIdBytes> freshIds;
        for (const auto& item : freshRemaps) freshIds.insert(item.first);
        std::set<NodeIdBytes> tombstoneIds;
        for (const auto& item : tombstones) tombstoneIds.insert(item.first);
        const std::uint64_t expectedFresh =
            expectedFreshAmbiguous + expectedFreshExternal;
        const bool exact = canonical && retainedExact &&
            expectedFreshIds.size() == expectedFresh &&
            expectedPriorIds.size() == expectedPrior &&
            freshIds == expectedFreshIds &&
            priorRemaps == expectedPriorIds &&
            tombstoneIds == expectedPriorIds &&
            freshAmbiguous == expectedFreshAmbiguous &&
            freshExternal == expectedFreshExternal &&
            remapRecords == expectedFresh + expectedPrior &&
            tombstoneRecords == expectedPrior;
        std::wcout << L"GRAPH_CAPTURE_CLIENT_RECEIPT_TRANSITION "
                   << transition << L" remap=" << remapRecords
                   << L" tombstone=" << tombstoneRecords
                   << L" fresh=" << freshIds.size()
                   << L" fresh_ambiguous=" << freshAmbiguous
                   << L" fresh_external=" << freshExternal
                   << L" prior=" << priorRemaps.size()
                   << L" retained=" << expectedRetainedIds.size()
                   << L" external_unmatched=" << tombstoneIds.size()
                   << L" canonical=" << canonical
                   << L" exact=" << exact << L'\n';
        for (const auto& item : freshRemaps) {
            std::wcout << L"GRAPH_CAPTURE_CLIENT_FRESH_MAPPING "
                       << transition << L' ' << nodeIdText(item.first)
                       << L"->" << nodeIdText(item.first)
                       << L" disposition=New reason="
                       << (item.second == hancom::graph::RemapReason::Ambiguous
                               ? L"Ambiguous" : L"ExternalUnique")
                       << L'\n';
        }
        for (const NodeIdBytes& id : priorRemaps) {
            const auto tombstone = tombstones.find(id);
            std::wcout << L"GRAPH_CAPTURE_CLIENT_PRIOR_MAPPING "
                       << transition << L' ' << nodeIdText(id)
                       << L"->absent disposition=Ambiguous reason=Ambiguous"
                       << L" tombstone=ExternalUnmatched revision="
                       << (tombstone == tombstones.end() ? 0U
                                                        : tombstone->second)
                       << L'\n';
        }
        return exact;
    };

    const std::array<bool, 6> productionReceiptTransitions{{
        clientVisibleReceipts(
            L"first->second", secondRecords, firstView, secondView, 2, 0, 2),
        clientVisibleReceipts(
            L"second->reopened", reopenedRecords, secondView, reopenedView,
            2, 0, 2),
        clientVisibleReceipts(
            L"reopened->moved", movedRecords, reopenedView, movedView,
            2, 0, 2),
        clientVisibleReceipts(
            L"moved->reordered", reorderedRecords, movedView, reorderedView,
            2, 0, 2),
        clientVisibleReceipts(
            L"reordered->split", splitRecords, reorderedView, splitView,
            3, 0, 2),
        clientVisibleReceipts(
            L"split->coalesced", coalescedRecords, splitView, coalescedView,
            0, 1, 3),
    }};
    const bool productionReceiptOracle = std::all_of(
        productionReceiptTransitions.begin(),
        productionReceiptTransitions.end(), [](const bool value) {
            return value;
        });

    const auto replaceFieldUuid = [](
        DecodedRecord* const record,
        const std::uint16_t tag,
        const NodeIdBytes& replacement) {
        for (DecodedField& field : record->fields) {
            if (field.tag == tag && field.value.size() == replacement.size()) {
                field.value.assign(replacement.begin(), replacement.end());
                return true;
            }
        }
        return false;
    };
    std::vector<DecodedRecord> wrongFreshRecords = secondRecords;
    bool wrongFreshSubstituted = false;
    for (DecodedRecord& record : wrongFreshRecords) {
        if (record.kind != static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Remap)) continue;
        const DecodedField* disposition = FindField(record.fields, 3);
        if (disposition != nullptr && disposition->value.size() == 1 &&
            disposition->value[0] == static_cast<std::uint8_t>(
                hancom::graph::RemapDisposition::New)) {
            wrongFreshSubstituted =
                replaceFieldUuid(&record, 1, secondView.uniqueControl) &&
                replaceFieldUuid(&record, 2, secondView.uniqueControl);
            break;
        }
    }
    const bool wrongFreshRejected = wrongFreshSubstituted &&
        !clientVisibleReceipts(
            L"negative-wrong-fresh", wrongFreshRecords, firstView,
            secondView, 2, 0, 2);

    std::vector<DecodedRecord> wrongPriorRecords = secondRecords;
    NodeIdBytes wrongPrior{};
    bool wrongPriorTombstoneSubstituted = false;
    bool wrongPriorRemapSubstituted = false;
    NodeIdBytes originalPrior{};
    for (DecodedRecord& record : wrongPriorRecords) {
        if (record.kind == static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Tombstone)) {
            const DecodedField* source = FindField(record.fields, 1);
            if (source != nullptr && source->value.size() == originalPrior.size()) {
                std::copy_n(
                    source->value.begin(), originalPrior.size(),
                    originalPrior.begin());
                wrongPrior = originalPrior;
                const std::array<size_t, 14> mutableIndexes{{
                    0, 1, 2, 3, 4, 5, 7, 9, 10, 11, 12, 13, 14, 15}};
                for (const size_t index : mutableIndexes) {
                    if (wrongPrior[index] == 0) continue;
                    --wrongPrior[index];
                    for (const size_t suffix : mutableIndexes) {
                        if (suffix > index) wrongPrior[suffix] = 0xff;
                    }
                    break;
                }
                wrongPriorTombstoneSubstituted = wrongPrior != originalPrior &&
                    replaceFieldUuid(&record, 1, wrongPrior);
                break;
            }
        }
    }
    for (DecodedRecord& record : wrongPriorRecords) {
        if (record.kind != static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Remap)) continue;
        const DecodedField* source = FindField(record.fields, 1);
        const DecodedField* disposition = FindField(record.fields, 3);
        if (source != nullptr && source->value.size() == originalPrior.size() &&
            std::equal(source->value.begin(), source->value.end(),
                       originalPrior.begin()) &&
            disposition != nullptr && disposition->value.size() == 1 &&
            disposition->value[0] == static_cast<std::uint8_t>(
                hancom::graph::RemapDisposition::Ambiguous)) {
            wrongPriorRemapSubstituted =
                replaceFieldUuid(&record, 1, wrongPrior);
            break;
        }
    }
    const bool wrongPriorRejected = wrongPriorTombstoneSubstituted &&
        wrongPriorRemapSubstituted && !clientVisibleReceipts(
            L"negative-wrong-prior", wrongPriorRecords, firstView,
            secondView, 2, 0, 2);
    const bool negativeOracle = productionReceiptOracle &&
        wrongFreshRejected && wrongPriorRejected;
    std::wcout << L"GRAPH_CAPTURE_CLIENT_RECEIPT_NEGATIVE_ORACLE "
               << negativeOracle << L" production_oracle="
               << productionReceiptOracle << L" wrong_fresh_substituted="
               << wrongFreshSubstituted << L" wrong_fresh_rejected="
               << wrongFreshRejected << L" prior_tombstone_substituted="
               << wrongPriorTombstoneSubstituted
               << L" prior_remap_substituted="
               << wrongPriorRemapSubstituted << L" wrong_prior_rejected="
               << wrongPriorRejected << L'\n';

    const bool remapped = noMerge && productionReceiptOracle &&
        negativeOracle &&
        retainedUniqueControl(firstView, secondView) &&
        retainedUniqueControl(secondView, reopenedView) &&
        retainedUniqueControl(reopenedView, movedView) &&
        retainedUniqueControl(movedView, reorderedView) &&
        retainedUniqueControl(reorderedView, splitView) &&
        retainedUniqueControl(splitView, coalescedView);
    const bool anchors = firstView.canonicalAnchors &&
        secondView.canonicalAnchors && reopenedView.canonicalAnchors &&
        movedView.canonicalAnchors && reorderedView.canonicalAnchors &&
        splitView.canonicalAnchors && coalescedView.canonicalAnchors;
    std::wcout << L"GRAPH_CAPTURE_DUPLICATE_EMPTY_TWO_TRAVERSALS "
               << traversedTwice << L'\n'
               << L"GRAPH_CAPTURE_DUPLICATE_EMPTY_NO_MERGE " << noMerge
               << L'\n'
               << L"GRAPH_CAPTURE_DUPLICATE_EMPTY_REMAP_AMBIGUOUS "
               << remapped << L'\n'
               << L"GRAPH_CAPTURE_DUPLICATE_EMPTY_REOPEN_REORDER_SPLIT_COALESCE "
               << (reopenedFresh && movedFresh && reorderedFresh && splitFresh &&
                   coalescedFresh) << L'\n'
               << L"GRAPH_CAPTURE_DUPLICATE_EMPTY_CANONICAL_ANCHORS "
               << anchors << L'\n';
    first.Reset();
    second.Reset();
    reopened.Reset();
    moved.Reset();
    reordered.Reset();
    split.Reset();
    coalesced.Reset();
    std::filesystem::remove_all(root, cleanupError);
    return traversedTwice && reopenedFresh && movedFresh && reorderedFresh &&
        splitFresh && coalescedFresh && noMerge && remapped && anchors &&
        !cleanupError && !std::filesystem::exists(root);
}

bool GenericControlCompleteIdentityKeySmoke() {
    using hancom::graph::NodeKind;
    using hancom::graph::ObservationState;
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) return false;
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-generic-control-complete-key-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code cleanupError;
    std::filesystem::remove_all(root, cleanupError);

    const auto control = [](
        const wchar_t* ctrlId,
        const wchar_t* instanceId,
        const bool instanceIdPresent,
        const std::uint64_t ordinal,
        const std::int64_t character) {
        hancom::graph::capture::ControlObservation value;
        value.ctrlId = ctrlId;
        value.instanceId = instanceId;
        value.instanceIdPresent = instanceIdPresent;
        value.headCtrlOrdinal = ordinal;
        value.anchor = {0, 1, character};
        return value;
    };
    std::vector<ReaderPayload> payloads = SealedPayloads();
    const std::array controls{
        control(L"complete-a", L"shared", true, 40, 2),
        control(L"complete-b", L"shared", true, 41, 3),
        control(L"presence", L"", false, 42, 4),
        control(L"presence", L"", true, 43, 5),
        control(L"duplicate", L"", false, 44, 6),
        control(L"duplicate", L"", false, 45, 7),
        control(L"duplicate", L"", true, 46, 8),
        control(L"duplicate", L"", true, 47, 9),
    };
    payloads[0].controls.insert(
        payloads[0].controls.end(), controls.begin(), controls.end());
    payloads[3].controls = payloads[0].controls;

    struct View final {
        std::map<std::wstring, std::vector<NodeIdBytes>> ids{};
        bool fieldsAndEdges = false;
        bool rfc4122 = false;
    };
    const auto inspect = [](const std::vector<DecodedRecord>& records) {
        View view;
        std::vector<StrictNode> nodes;
        std::vector<StrictEdge> edges;
        std::vector<StrictProperty> properties;
        if (!ParseStrict(records, &nodes, &edges, &properties)) return view;
        view.fieldsAndEdges = true;
        view.rfc4122 = true;
        for (const StrictNode& node : nodes) {
            if (node.kind != static_cast<std::uint16_t>(
                    NodeKind::GenericControl)) continue;
            const std::wstring ctrlId = PayloadObservedText(node, 100);
            if (ctrlId != L"complete-a" && ctrlId != L"complete-a-changed" &&
                ctrlId != L"complete-b" && ctrlId != L"presence" &&
                ctrlId != L"duplicate") continue;
            const DecodedField* instance = FindField(node.payload, 101);
            std::uint64_t ordinal = 0;
            if (instance == nullptr || instance->value.empty() ||
                !PayloadUint64(node, 105, &ordinal) || !node.parentPresent) {
                return View{};
            }
            const bool present = instance->value[0] ==
                static_cast<std::uint8_t>(ObservationState::Value);
            if (!present && instance->value[0] !=
                    static_cast<std::uint8_t>(ObservationState::NotExposed)) {
                return View{};
            }
            const std::wstring key = ctrlId + L":" +
                (present ? L"present:" : L"absent:") +
                (present ? PayloadObservedText(node, 101) : L"");
            view.ids[key].push_back(node.id);
            std::uint64_t anchors = 0;
            std::uint64_t contains = 0;
            for (const StrictEdge& edge : edges) {
                if (edge.source == node.id && edge.kind ==
                        static_cast<std::uint16_t>(
                            hancom::graph::EdgeKind::Anchors)) {
                    ++anchors;
                    view.fieldsAndEdges = view.fieldsAndEdges &&
                        edge.target == node.parent;
                }
                if (edge.target == node.id && edge.kind ==
                        static_cast<std::uint16_t>(
                            hancom::graph::EdgeKind::Contains)) {
                    ++contains;
                }
            }
            view.fieldsAndEdges = view.fieldsAndEdges &&
                anchors == 1 && contains == 1 && ordinal >= 40;
            hancom::graph::NodeId id;
            std::copy(node.id.begin(), node.id.end(), id.bytes.begin());
            view.rfc4122 = view.rfc4122 &&
                hancom::graph::identity::IsRfc4122V4(id);
        }
        for (auto& item : view.ids) {
            std::sort(item.second.begin(), item.second.end());
        }
        return view;
    };
    const auto read = [&root](
        const wchar_t* name,
        std::vector<std::uint8_t>* bytes,
        std::vector<DecodedRecord>* records) {
        return ReadWholeFile(root / name / L"records.hgn", bytes) &&
            DecodeRecords(*bytes, records);
    };
    const auto fieldValue = [](
        const std::vector<std::uint8_t>& bytes,
        const size_t begin,
        const size_t size,
        const std::uint16_t wanted,
        size_t* const valueAt,
        size_t* const valueSize) {
        size_t at = begin;
        const size_t end = begin + size;
        while (at != end) {
            if (at > end || end - at < 24) return false;
            const std::uint64_t length = R64(bytes.data() + at + 16);
            if (length > end - at - 24) return false;
            if (R16(bytes.data() + at) == wanted) {
                *valueAt = at + 24;
                *valueSize = static_cast<size_t>(length);
                return true;
            }
            at += 24 + static_cast<size_t>(length);
        }
        return false;
    };
    const auto instanceEnvelope = [&fieldValue](
        const std::vector<std::uint8_t>& bytes,
        const std::uint8_t wantedState,
        size_t* const envelopeAt,
        size_t* const envelopeSize) {
        size_t recordAt = 0;
        while (recordAt != bytes.size()) {
            if (bytes.size() - recordAt < 24) return false;
            const size_t recordSize = static_cast<size_t>(
                R64(bytes.data() + recordAt + 16));
            if (recordSize > bytes.size() - recordAt - 24) return false;
            if (R16(bytes.data() + recordAt) == static_cast<std::uint16_t>(
                    hancom::graph::RecordKind::Node)) {
                size_t payloadAt = 0;
                size_t payloadSize = 0;
                if (!fieldValue(
                        bytes, recordAt + 24, recordSize, 2,
                        &payloadAt, &payloadSize)) return false;
                size_t ctrlAt = 0;
                size_t ctrlSize = 0;
                size_t instanceAt = 0;
                size_t instanceSize = 0;
                if (!fieldValue(bytes, payloadAt, payloadSize, 100,
                                &ctrlAt, &ctrlSize) ||
                    !fieldValue(bytes, payloadAt, payloadSize, 101,
                                &instanceAt, &instanceSize)) {
                    recordAt += 24 + recordSize;
                    continue;
                }
                std::vector<std::uint8_t> ctrlEnvelope(
                    bytes.begin() + static_cast<std::ptrdiff_t>(ctrlAt),
                    bytes.begin() + static_cast<std::ptrdiff_t>(
                        ctrlAt + ctrlSize));
                std::vector<std::uint8_t> ctrlValue;
                if (ObservationPayload(ctrlEnvelope, &ctrlValue) &&
                    Utf16Text(ctrlValue) == L"presence" &&
                    instanceSize >= 24 && bytes[instanceAt] == wantedState) {
                    *envelopeAt = instanceAt;
                    *envelopeSize = instanceSize;
                    return true;
                }
            }
            recordAt += 24 + recordSize;
        }
        return false;
    };

    const std::vector<std::uint8_t> environment = NormativeEnvironment();
    CaptureSpool first;
    CaptureSpool reopened;
    std::vector<std::uint8_t> firstBytes;
    std::vector<std::uint8_t> reopenedBytes;
    std::vector<DecodedRecord> firstRecords;
    std::vector<DecodedRecord> reopenedRecords;
    hancom::graph::capture::CaptureIdentityArena firstArena;
    hancom::graph::capture::CaptureIdentityArena reopenArena;
    const bool built =
        first.Build(root / L"first", payloads, environment, firstArena) &&
        read(L"first", &firstBytes, &firstRecords) &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {firstBytes.data(), firstBytes.size()}, &reopenArena) &&
        reopened.Build(root / L"reopened", payloads, environment, reopenArena) &&
        read(L"reopened", &reopenedBytes, &reopenedRecords);
    std::vector<ReaderPayload> movedPayloads = payloads;
    for (auto& item : movedPayloads[0].controls) {
        if (item.headCtrlOrdinal >= 40 && item.headCtrlOrdinal <= 47) {
            item.headCtrlOrdinal += 100;
            item.anchor.character += 10;
        }
    }
    std::reverse(
        movedPayloads[0].controls.end() - controls.size(),
        movedPayloads[0].controls.end());
    movedPayloads[3].controls = movedPayloads[0].controls;
    CaptureSpool moved;
    hancom::graph::capture::CaptureIdentityArena movedArena;
    std::vector<std::uint8_t> movedBytes;
    std::vector<DecodedRecord> movedRecords;
    const bool movedBuilt = built &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {reopenedBytes.data(), reopenedBytes.size()}, &movedArena) &&
        moved.Build(root / L"moved", movedPayloads, environment, movedArena) &&
        read(L"moved", &movedBytes, &movedRecords);

    std::vector<ReaderPayload> ctrlChangedPayloads = payloads;
    for (auto& item : ctrlChangedPayloads[0].controls) {
        if (item.ctrlId == L"complete-a") item.ctrlId = L"complete-a-changed";
    }
    ctrlChangedPayloads[3].controls = ctrlChangedPayloads[0].controls;
    CaptureSpool ctrlChanged;
    hancom::graph::capture::CaptureIdentityArena ctrlChangedArena;
    std::vector<std::uint8_t> ctrlChangedBytes;
    std::vector<DecodedRecord> ctrlChangedRecords;
    const bool ctrlChangedBuilt = built &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {reopenedBytes.data(), reopenedBytes.size()}, &ctrlChangedArena) &&
        ctrlChanged.Build(
            root / L"ctrl-changed", ctrlChangedPayloads, environment,
            ctrlChangedArena) &&
        read(L"ctrl-changed", &ctrlChangedBytes, &ctrlChangedRecords);

    std::vector<ReaderPayload> presenceChangedPayloads = payloads;
    for (auto& item : presenceChangedPayloads[0].controls) {
        if (item.ctrlId == L"presence" && !item.instanceIdPresent) {
            item.instanceIdPresent = true;
            item.instanceId = L"became-present";
        }
    }
    presenceChangedPayloads[3].controls = presenceChangedPayloads[0].controls;
    CaptureSpool presenceChanged;
    hancom::graph::capture::CaptureIdentityArena presenceChangedArena;
    std::vector<std::uint8_t> presenceChangedBytes;
    std::vector<DecodedRecord> presenceChangedRecords;
    const bool presenceChangedBuilt = built &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {reopenedBytes.data(), reopenedBytes.size()},
            &presenceChangedArena) &&
        presenceChanged.Build(
            root / L"presence-changed", presenceChangedPayloads, environment,
            presenceChangedArena) &&
        read(L"presence-changed", &presenceChangedBytes,
             &presenceChangedRecords);

    size_t absentEnvelopeAt = 0;
    size_t absentEnvelopeSize = 0;
    size_t presentEnvelopeAt = 0;
    size_t presentEnvelopeSize = 0;
    const bool locatedEnvelopes = built &&
        instanceEnvelope(
            firstBytes,
            static_cast<std::uint8_t>(ObservationState::NotExposed),
            &absentEnvelopeAt, &absentEnvelopeSize) &&
        instanceEnvelope(
            firstBytes,
            static_cast<std::uint8_t>(ObservationState::Value),
            &presentEnvelopeAt, &presentEnvelopeSize) &&
        absentEnvelopeSize == 24 && presentEnvelopeSize == 32;
    std::vector<std::uint8_t> terminalWithValue = firstBytes;
    std::vector<std::uint8_t> terminalMarkedPresent = firstBytes;
    std::vector<std::uint8_t> valueMarkedAbsent = firstBytes;
    std::vector<std::uint8_t> valueWithHresult = firstBytes;
    if (locatedEnvelopes) {
        terminalWithValue[presentEnvelopeAt] = static_cast<std::uint8_t>(
            ObservationState::NotExposed);
        terminalWithValue[presentEnvelopeAt + 1] = 0;
        terminalMarkedPresent[absentEnvelopeAt + 1] = 1;
        valueMarkedAbsent[presentEnvelopeAt + 1] = 0;
        valueWithHresult[presentEnvelopeAt + 4] = 1;
    }
    const auto seedRejects = [](const std::vector<std::uint8_t>& stream) {
        hancom::graph::capture::CaptureIdentityArena arena;
        return !hancom::graph::capture::SeedArenaFromRecordStream(
                   {stream.data(), stream.size()}, &arena) &&
            arena.RemapCount() == 0 && arena.TombstoneCount() == 0;
    };
    const auto setHresult = [](
        std::vector<std::uint8_t>* const stream,
        const size_t envelopeAt,
        const std::uint32_t hresult) {
        for (size_t index = 0; index < 4; ++index) {
            (*stream)[envelopeAt + 4 + index] = static_cast<std::uint8_t>(
                hresult >> (index * 8));
        }
    };
    constexpr std::uint32_t successHresult = 0;
    constexpr std::uint32_t canonicalFailureHresult = 0x80004005U;
    constexpr std::uint32_t wrongFailureHresult = 0x80070005U;
    const std::array hresults{
        successHresult, canonicalFailureHresult, wrongFailureHresult};
    bool exactStateHresultMatrix = locatedEnvelopes;
    for (const bool nonemptyPayload : {false, true}) {
        const size_t envelopeAt = nonemptyPayload
            ? presentEnvelopeAt : absentEnvelopeAt;
        for (std::uint8_t state = static_cast<std::uint8_t>(
                 ObservationState::Value);
             state <= static_cast<std::uint8_t>(
                 ObservationState::ProjectionOmitted); ++state) {
            for (const std::uint32_t hresult : hresults) {
                for (const std::uint8_t marker :
                     {std::uint8_t{0}, std::uint8_t{1}}) {
                    std::vector<std::uint8_t> candidate = firstBytes;
                    if (locatedEnvelopes) {
                        candidate[envelopeAt] = state;
                        candidate[envelopeAt + 1] = marker;
                        setHresult(&candidate, envelopeAt, hresult);
                    }
                    hancom::graph::capture::CaptureIdentityArena arena;
                    const bool accepted = locatedEnvelopes &&
                        hancom::graph::capture::SeedArenaFromRecordStream(
                            {candidate.data(), candidate.size()}, &arena);
                    const bool expected =
                        nonemptyPayload && state == static_cast<std::uint8_t>(
                            ObservationState::Value) &&
                            hresult == successHresult && marker == 1 ||
                        !nonemptyPayload && state == static_cast<std::uint8_t>(
                            ObservationState::NotExposed) &&
                            hresult == successHresult && marker == 0;
                    exactStateHresultMatrix = exactStateHresultMatrix &&
                        accepted == expected &&
                        (accepted || (arena.RemapCount() == 0 &&
                                      arena.TombstoneCount() == 0));
                }
            }
        }
    }
    std::vector<std::uint8_t> notExposedWithFailure = firstBytes;
    if (locatedEnvelopes) {
        setHresult(
            &notExposedWithFailure, absentEnvelopeAt,
            canonicalFailureHresult);
    }
    bool allTerminalPayloadsRejected = locatedEnvelopes;
    for (std::uint8_t state = static_cast<std::uint8_t>(
             ObservationState::NotApplicable);
         state <= static_cast<std::uint8_t>(
             ObservationState::ProjectionOmitted); ++state) {
        std::vector<std::uint8_t> malformed = firstBytes;
        if (locatedEnvelopes) {
            malformed[presentEnvelopeAt] = state;
            malformed[presentEnvelopeAt + 1] = 0;
        }
        allTerminalPayloadsRejected =
            allTerminalPayloadsRejected && seedRejects(malformed);
    }
    hancom::graph::capture::CaptureIdentityArena atomicArena;
    CaptureSpool atomicReopen;
    const bool malformedRejectedAtomically = locatedEnvelopes &&
        exactStateHresultMatrix && allTerminalPayloadsRejected &&
        seedRejects(notExposedWithFailure) && seedRejects(terminalWithValue) &&
        seedRejects(terminalMarkedPresent) &&
        seedRejects(valueMarkedAbsent) &&
        seedRejects(valueWithHresult) &&
        !hancom::graph::capture::SeedArenaFromRecordStream(
            {notExposedWithFailure.data(), notExposedWithFailure.size()},
            &atomicArena) &&
        atomicArena.RemapCount() == 0 && atomicArena.TombstoneCount() == 0 &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {firstBytes.data(), firstBytes.size()}, &atomicArena) &&
        atomicReopen.Build(
            root / L"atomic-valid-reopen", payloads, environment,
            atomicArena);

    const View before = inspect(firstRecords);
    const View after = inspect(reopenedRecords);
    const View movedView = inspect(movedRecords);
    const View ctrlChangedView = inspect(ctrlChangedRecords);
    const View presenceChangedView = inspect(presenceChangedRecords);
    const std::array uniqueKeys{
        std::wstring(L"complete-a:present:shared"),
        std::wstring(L"complete-b:present:shared"),
        std::wstring(L"presence:absent:"),
        std::wstring(L"presence:present:"),
    };
    bool uniqueRetained = built;
    std::set<NodeIdBytes> uniqueIds;
    for (const auto& key : uniqueKeys) {
        uniqueRetained = uniqueRetained && before.ids.count(key) == 1 &&
            after.ids.count(key) == 1 && before.ids.at(key).size() == 1 &&
            after.ids.at(key).size() == 1 &&
            before.ids.at(key) == after.ids.at(key);
        if (before.ids.count(key) == 1 && before.ids.at(key).size() == 1) {
            uniqueIds.insert(before.ids.at(key).front());
        }
    }
    uniqueRetained = uniqueRetained && uniqueIds.size() == uniqueKeys.size();
    const std::wstring absentDuplicate = L"duplicate:absent:";
    const std::wstring presentDuplicate = L"duplicate:present:";
    const auto disjoint = [](const auto& left, const auto& right) {
        std::vector<NodeIdBytes> overlap;
        std::set_intersection(
            left.begin(), left.end(), right.begin(), right.end(),
            std::back_inserter(overlap));
        return overlap.empty();
    };
    const bool partitionedDuplicates = built &&
        before.ids.count(absentDuplicate) == 1 &&
        before.ids.count(presentDuplicate) == 1 &&
        after.ids.count(absentDuplicate) == 1 &&
        after.ids.count(presentDuplicate) == 1 &&
        before.ids.at(absentDuplicate).size() == 2 &&
        before.ids.at(presentDuplicate).size() == 2 &&
        after.ids.at(absentDuplicate).size() == 2 &&
        after.ids.at(presentDuplicate).size() == 2 &&
        disjoint(before.ids.at(absentDuplicate),
                 after.ids.at(absentDuplicate)) &&
        disjoint(before.ids.at(presentDuplicate),
                 after.ids.at(presentDuplicate));
    std::uint64_t remaps = 0;
    std::uint64_t tombstones = 0;
    std::uint64_t freshAmbiguous = 0;
    std::uint64_t priorAmbiguous = 0;
    bool canonicalReceipts = true;
    for (const DecodedRecord& record : reopenedRecords) {
        if (record.kind == static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Tombstone)) {
            ++tombstones;
            const DecodedField* reason = FindField(record.fields, 3);
            canonicalReceipts = canonicalReceipts && reason != nullptr &&
                reason->value.size() == 2 && R16(reason->value.data()) ==
                    static_cast<std::uint16_t>(
                        hancom::graph::TombstoneReason::ExternalUnmatched);
        } else if (record.kind == static_cast<std::uint16_t>(
                       hancom::graph::RecordKind::Remap)) {
            ++remaps;
            const DecodedField* target = FindField(record.fields, 2);
            const DecodedField* disposition = FindField(record.fields, 3);
            const DecodedField* reason = FindField(record.fields, 4);
            if (disposition == nullptr || disposition->value.size() != 1 ||
                reason == nullptr || reason->value.size() != 2 ||
                R16(reason->value.data()) != static_cast<std::uint16_t>(
                    hancom::graph::RemapReason::Ambiguous)) {
                canonicalReceipts = false;
                continue;
            }
            if (disposition->value[0] == static_cast<std::uint8_t>(
                    hancom::graph::RemapDisposition::New) &&
                target != nullptr && target->value.size() == 16) {
                ++freshAmbiguous;
            } else if (disposition->value[0] == static_cast<std::uint8_t>(
                           hancom::graph::RemapDisposition::Ambiguous) &&
                       target == nullptr) {
                ++priorAmbiguous;
            } else {
                canonicalReceipts = false;
            }
        }
    }
    canonicalReceipts = canonicalReceipts && remaps == 8 && tombstones == 4 &&
        freshAmbiguous == 4 && priorAmbiguous == 4;
    const bool rootsStable = built && hancom::graph::codec::Equal(
            first.Result().manifest.observedSemanticRoot,
            reopened.Result().manifest.observedSemanticRoot) &&
        hancom::graph::codec::Equal(
            first.Result().manifest.layoutRoot,
            reopened.Result().manifest.layoutRoot) &&
        hancom::graph::codec::Equal(
            first.Result().manifest.captureRoot,
            reopened.Result().manifest.captureRoot);
    bool movementRetained = movedBuilt && movedView.fieldsAndEdges;
    for (const auto& key : uniqueKeys) {
        movementRetained = movementRetained && after.ids.count(key) == 1 &&
            movedView.ids.count(key) == 1 &&
            after.ids.at(key) == movedView.ids.at(key);
    }
    movementRetained = movementRetained &&
        disjoint(after.ids.at(absentDuplicate),
                 movedView.ids.at(absentDuplicate)) &&
        disjoint(after.ids.at(presentDuplicate),
                 movedView.ids.at(presentDuplicate));
    const bool movementRoots = movedBuilt &&
        !hancom::graph::codec::Equal(
            reopened.Result().manifest.observedSemanticRoot,
            moved.Result().manifest.observedSemanticRoot) &&
        !hancom::graph::codec::Equal(
            reopened.Result().manifest.captureRoot,
            moved.Result().manifest.captureRoot) &&
        hancom::graph::codec::Equal(
            reopened.Result().manifest.layoutRoot,
            moved.Result().manifest.layoutRoot);
    const std::wstring changedKey = L"complete-a-changed:present:shared";
    const bool ctrlIdChangeBreaksIdentity = ctrlChangedBuilt &&
        ctrlChangedView.ids.count(L"complete-a:present:shared") == 0 &&
        ctrlChangedView.ids.count(changedKey) == 1 &&
        ctrlChangedView.ids.at(changedKey).size() == 1 &&
        ctrlChangedView.ids.at(changedKey) !=
            after.ids.at(L"complete-a:present:shared") &&
        !hancom::graph::codec::Equal(
            reopened.Result().manifest.observedSemanticRoot,
            ctrlChanged.Result().manifest.observedSemanticRoot) &&
        !hancom::graph::codec::Equal(
            reopened.Result().manifest.captureRoot,
            ctrlChanged.Result().manifest.captureRoot);
    const std::wstring becamePresent = L"presence:present:became-present";
    const bool presenceCaptureOnly = presenceChangedBuilt &&
        presenceChangedView.ids.count(L"presence:absent:") == 0 &&
        presenceChangedView.ids.count(becamePresent) == 1 &&
        hancom::graph::codec::Equal(
            reopened.Result().manifest.observedSemanticRoot,
            presenceChanged.Result().manifest.observedSemanticRoot) &&
        !hancom::graph::codec::Equal(
            reopened.Result().manifest.captureRoot,
            presenceChanged.Result().manifest.captureRoot) &&
        hancom::graph::codec::Equal(
            reopened.Result().manifest.layoutRoot,
            presenceChanged.Result().manifest.layoutRoot);
    const bool exact = uniqueRetained && partitionedDuplicates &&
        canonicalReceipts && rootsStable && movementRetained && movementRoots &&
        ctrlIdChangeBreaksIdentity && presenceCaptureOnly &&
        malformedRejectedAtomically && before.fieldsAndEdges &&
        after.fieldsAndEdges && before.rfc4122 && after.rfc4122;
    std::wcout << L"GRAPH_CAPTURE_GENERIC_CONTROL_COMPLETE_IDENTITY_KEY "
               << exact << L" unique_retained=" << uniqueRetained
               << L" presence_partitions=" << partitionedDuplicates
               << L" receipts=" << canonicalReceipts
               << L" roots=" << rootsStable
               << L" movement=" << (movementRetained && movementRoots)
               << L" ctrlid_change=" << ctrlIdChangeBreaksIdentity
               << L" presence_capture_only=" << presenceCaptureOnly
               << L" malformed_rejected=" << malformedRejectedAtomically
               << L" rfc4122=" << (before.rfc4122 && after.rfc4122)
               << L'\n';
    first.Reset();
    reopened.Reset();
    moved.Reset();
    ctrlChanged.Reset();
    presenceChanged.Reset();
    atomicReopen.Reset();
    std::filesystem::remove_all(root, cleanupError);
    return exact && !cleanupError && !std::filesystem::exists(root);
}

enum class BoundaryScenario : std::uint8_t {
    Complete,
    Inconclusive,
    ReadFailedTerminal,
    MissingTableSelection,
    FalseLayoutCertification,
};

struct BoundaryContext final {
    BoundaryScenario scenario = BoundaryScenario::Complete;
    std::vector<ReaderPayload> templatePayloads{};
    bool nativeStoryReader = false;
    std::vector<hancom::graph::capture::SectionObservation>
        nativeStorySections{};
    std::vector<hancom::graph::capture::ControlObservation>
        nativeStoryControls{};
    NativeAnchorCase nativeAnchorCase = NativeAnchorCase::Available;
    std::wstring nativeCtrlId{};
    size_t nativeAdapterReads = 0;
    size_t nativeAdapterCommits = 0;
    size_t nativeAdapterAborts = 0;
    bool nativeAdapterNoPartialState = true;
    std::vector<ReaderPayload> attemptPayloads[2]{};
    CaptureSpool spools[2]{};
    hancom::graph::capture::CaptureIdentityArena arena{};
    CapturedState baseline{};
    std::wstring renderedProbeText{L"RESULT\tFAIL\tMISLEADING"};
    std::unique_ptr<GraphStore> store{};
    std::filesystem::path storeRoot{};
    hancom::graph::store::FailurePoint storeFailure =
        hancom::graph::store::FailurePoint::None;
    std::uint64_t sessionSerial = 0;
    size_t publishCount = 0;
    size_t abortCount = 0;
    size_t commitCount = 0;
};

bool BoundaryCaptureState(void* raw, CapturedState* output) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || output == nullptr) return false;
    *output = context->baseline;
    return true;
}

bool BoundaryBegin(void* raw, const size_t attempt) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || attempt >= 2) return false;
    context->attemptPayloads[attempt].clear();
    return true;
}

bool BoundaryReader(
    void* raw,
    const size_t attempt,
    const size_t reader) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || attempt >= 2 ||
        reader >= context->templatePayloads.size()) return false;
    ReaderPayload payload;
    if (context->nativeStoryReader && reader == static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::StorySpine)) {
        CComPtr<IDispatch> native;
        native.Attach(CreateNativeAnchorDispatch(
            context->nativeAnchorCase,
            context->nativeCtrlId.c_str(),
            context->nativeStorySections,
            context->nativeStoryControls));
        hancom::official_api::capability::StorySpineReaderDiagnostics
            diagnostics;
        ++context->nativeAdapterReads;
        const bool complete =
            hancom::official_api::capability::CaptureStorySpineReaderPayload(
                native, &payload, &diagnostics);
        context->nativeAdapterCommits += diagnostics.committed ? 1U : 0U;
        context->nativeAdapterAborts += diagnostics.aborted ? 1U : 0U;
        if (!complete) {
            context->nativeAdapterNoPartialState =
                context->nativeAdapterNoPartialState &&
                payload.outcome ==
                    hancom::graph::capture::ReaderOutcome::Failed &&
                payload.coverage == hancom::graph::CoverageState::ReadFailed &&
                payload.sections.empty() && payload.controls.empty() &&
                payload.coverageFacts.empty();
        } else {
            context->templatePayloads[0] = payload;
            context->templatePayloads[3].controls = payload.controls;
            const auto nativeControl = [&payload](
                const std::wstring& ctrlId,
                const std::wstring& instanceId) {
                return std::find_if(
                    payload.controls.begin(), payload.controls.end(),
                    [&ctrlId, &instanceId](const auto& control) {
                        return control.ctrlId == ctrlId &&
                            control.instanceId == instanceId;
                    });
            };
            for (auto& table : context->templatePayloads[4].tables) {
                const auto control = nativeControl(L"tbl", table.instanceId);
                if (control == payload.controls.end()) return false;
                table.headCtrlOrdinal = control->headCtrlOrdinal;
                table.anchor = control->anchor;
            }
            for (auto& image : context->templatePayloads[5].images) {
                const auto control = nativeControl(
                    image.ctrlId, image.instanceId);
                if (control == payload.controls.end()) return false;
                image.headCtrlOrdinal = control->headCtrlOrdinal;
                image.anchor = control->anchor;
            }
            RecertifySyntheticLayout(&context->templatePayloads);
        }
    } else {
        payload = context->templatePayloads[reader];
    }
    if (reader == static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::EffectiveProperties) &&
        context->scenario == BoundaryScenario::Complete) {
        std::vector<hancom::graph::properties::ReferenceSite> sites;
        const auto nativeIdentity = [](
            const hancom::graph::capture::PropertyTarget target,
            const std::wstring& ctrlId,
            const std::uint64_t ordinal,
            const hancom::graph::capture::NativePosition& anchor,
            const bool instanceIdPresent,
            const std::wstring& instanceId) {
            return std::to_wstring(static_cast<unsigned>(target)) + L":" +
                ctrlId + L":" + (instanceIdPresent ? L"1" : L"0") + L":" +
                std::to_wstring(ordinal) + L":" +
                std::to_wstring(anchor.list) + L":" +
                std::to_wstring(anchor.paragraph) + L":" +
                std::to_wstring(anchor.character) + L":" + instanceId;
        };
        const auto addParagraph = [&sites](const auto& paragraph) {
            sites.push_back({hancom::graph::capture::PropertyTarget::Paragraph,
                std::to_wstring(paragraph.start.list) + L":" +
                    std::to_wstring(paragraph.start.paragraph),
                paragraph.start, paragraph.sectionOrdinal});
            for (const auto& run : paragraph.runs) {
                sites.push_back({hancom::graph::capture::PropertyTarget::Run,
                    std::to_wstring(run.start.list) + L":" +
                        std::to_wstring(run.start.paragraph) + L":" +
                        std::to_wstring(run.start.character) + L":" +
                        std::to_wstring(run.end.character),
                    run.start, paragraph.sectionOrdinal});
            }
        };
        for (const auto& section : context->templatePayloads[0].sections) {
            sites.push_back({hancom::graph::capture::PropertyTarget::Section,
                std::to_wstring(section.ordinal), section.start,
                section.ordinal});
        }
        for (const auto& paragraph :
             context->templatePayloads[1].paragraphs) {
            addParagraph(paragraph);
        }
        for (const auto& control : context->templatePayloads[0].controls) {
            const bool tableControl = std::any_of(
                context->templatePayloads[4].tables.begin(),
                context->templatePayloads[4].tables.end(),
                [&control](const auto& table) {
                    return table.headCtrlOrdinal == control.headCtrlOrdinal;
                });
            const bool imageControl = std::any_of(
                context->templatePayloads[5].images.begin(),
                context->templatePayloads[5].images.end(),
                [&control](const auto& image) {
                    return image.headCtrlOrdinal == control.headCtrlOrdinal;
                });
            if (!tableControl && !imageControl) {
                sites.push_back({
                    hancom::graph::capture::PropertyTarget::Control,
                    nativeIdentity(
                        hancom::graph::capture::PropertyTarget::Control, control.ctrlId,
                        control.headCtrlOrdinal, control.anchor,
                        control.instanceIdPresent, control.instanceId),
                    control.anchor, 0});
            }
        }
        for (const auto& table : context->templatePayloads[4].tables) {
            const std::wstring tableIdentity = nativeIdentity(
                hancom::graph::capture::PropertyTarget::Table, L"tbl",
                table.headCtrlOrdinal, table.anchor,
                table.instanceIdPresent, table.instanceId);
            sites.push_back({
                hancom::graph::capture::PropertyTarget::Table,
                tableIdentity, table.anchor, 0});
            for (const auto& cell : table.cells) {
                const std::wstring cellIdentity = nativeIdentity(
                    hancom::graph::capture::PropertyTarget::Cell, L"tbl",
                    table.headCtrlOrdinal, {cell.listId, 0, 0},
                    table.instanceIdPresent, table.instanceId) +
                    L":" + cell.address;
                sites.push_back({
                    hancom::graph::capture::PropertyTarget::Cell,
                    cellIdentity, {cell.listId, 0, 0}, 0,
                    cellIdentity, tableIdentity});
                for (const auto& paragraph : cell.paragraphs) {
                    addParagraph(paragraph);
                }
            }
        }
        for (const auto& image : context->templatePayloads[5].images) {
            sites.push_back({
                hancom::graph::capture::PropertyTarget::Image,
                nativeIdentity(
                    hancom::graph::capture::PropertyTarget::Image, image.ctrlId,
                    image.headCtrlOrdinal, image.anchor,
                    image.instanceIdPresent, image.instanceId),
                image.anchor, 0});
        }
        if (!BuildFakeDispatchReferenceClosure(sites, &payload) ||
            !hancom::graph::properties::AccountCaptionReferenceTerminals(
                context->arena, context->templatePayloads[5].images,
                &payload)) {
            return false;
        }
    }
    if (reader == static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::StableLayout) &&
        context->scenario == BoundaryScenario::Complete) {
        if (context->attemptPayloads[attempt].size() <=
            static_cast<size_t>(
                hancom::graph::capture::QualifiedReader::EffectiveProperties)) {
            return false;
        }
        hancom::graph::Sha256 pageSetup{};
        if (!hancom::graph::properties::DerivePageSetupDigest(
                context->attemptPayloads[attempt][2], &pageSetup)) {
            return false;
        }
        std::vector<std::uint8_t> environment;
        if (!hancom::graph::layout::ReplacePageSetupDigestV1(
                payload.layoutEnvironment, pageSetup, &environment)) {
            return false;
        }
        payload.layoutEnvironment = std::move(environment);
    }
    if (context->scenario == BoundaryScenario::FalseLayoutCertification &&
        reader == static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::StableLayout)) {
        payload.layoutPerKindComplete = false;
    } else if (context->scenario == BoundaryScenario::Inconclusive && reader == 3) {
        payload.outcome =
            hancom::graph::capture::ReaderOutcome::Inconclusive;
        payload.coverage = hancom::graph::CoverageState::NotExposed;
    } else if (context->scenario == BoundaryScenario::MissingTableSelection &&
               reader == 4) {
        payload.tables.clear();
        payload.outcome =
            hancom::graph::capture::ReaderOutcome::Inconclusive;
        payload.coverage = hancom::graph::CoverageState::NotExposed;
    } else if (context->scenario == BoundaryScenario::ReadFailedTerminal &&
               reader == 2) {
        payload.outcome = hancom::graph::capture::ReaderOutcome::Complete;
        payload.coverage = hancom::graph::CoverageState::ReadFailed;
        payload.coverageFacts.erase(
            std::remove_if(
                payload.coverageFacts.begin(), payload.coverageFacts.end(),
                [](const hancom::graph::capture::CoverageObservation& fact) {
                    return fact.target ==
                            hancom::graph::capture::PropertyTarget::Run &&
                        fact.coordinate ==
                            hancom::graph::CoverageCoordinateKind::NodeField &&
                        fact.ownerField == 102 &&
                        fact.profile == hancom::graph::ProfileId::EditableText;
                }),
            payload.coverageFacts.end());
        for (const hancom::graph::capture::DefinitionReferenceObservation&
             reference : payload.definitionReferences) {
            if (reference.source !=
                    hancom::graph::capture::PropertyTarget::Run ||
                reference.edge !=
                    hancom::graph::EdgeKind::CharacterShapeRef) {
                continue;
            }
            hancom::graph::capture::CoverageObservation terminal;
            terminal.target =
                hancom::graph::capture::PropertyTarget::Run;
            terminal.targetIdentity = reference.sourceIdentity;
            terminal.coordinate =
                hancom::graph::CoverageCoordinateKind::NodeField;
            terminal.ownerField = 102;
            terminal.profile = hancom::graph::ProfileId::EditableText;
            terminal.state = hancom::graph::CoverageState::ReadFailed;
            payload.coverageFacts.push_back(std::move(terminal));
        }
        payload.properties.erase(
            std::remove_if(
                payload.properties.begin(), payload.properties.end(),
                [](const hancom::graph::capture::PropertyObservation& item) {
                    return item.target ==
                            hancom::graph::capture::PropertyTarget::Run &&
                        item.ownerField == 102;
                }),
            payload.properties.end());
        payload.definitionReferences.erase(
            std::remove_if(
                payload.definitionReferences.begin(),
                payload.definitionReferences.end(),
                [](const hancom::graph::capture::DefinitionReferenceObservation&
                       reference) {
                    return reference.source ==
                            hancom::graph::capture::PropertyTarget::Run &&
                        reference.edge ==
                            hancom::graph::EdgeKind::CharacterShapeRef;
                }),
            payload.definitionReferences.end());
    }
    // renderedProbeText is deliberately not read here or by the spool.
    context->attemptPayloads[attempt].push_back(std::move(payload));
    return true;
}

bool BoundaryFinish(
    void* raw,
    const size_t attempt,
    AttemptResult* output) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || output == nullptr || attempt >= 2 ||
        context->attemptPayloads[attempt].size() != kQualifiedReaderCount) {
        return false;
    }
    const auto& environment =
        context->attemptPayloads[attempt][6].layoutEnvironment;
    const std::filesystem::path directory =
        std::filesystem::temp_directory_path() /
        (L"hwp-graph-boundary-integration-" +
         std::to_wstring(GetCurrentProcessId()) + L"-" +
         std::to_wstring(attempt));
    if (attempt != 0) {
        context->arena.ReplayNextEncodingPass();
    }
    std::wstring buildFailure;
    if (!context->spools[attempt].Build(
            directory,
            context->attemptPayloads[attempt],
            environment,
            context->arena,
            &buildFailure)) {
        std::wcout << L"GRAPH_CAPTURE_BOUNDARY_BUILD_FAILURE attempt="
                   << attempt << L" scenario="
                   << static_cast<unsigned>(context->scenario)
                   << L" stage=" << buildFailure << L'\n';
        return false;
    }
    *output = context->spools[attempt].Result();
    std::wcout << L"GRAPH_CAPTURE_BOUNDARY_ATTEMPT_RESULT scenario="
               << static_cast<unsigned>(context->scenario)
               << L" attempt=" << attempt << L" integrity="
               << static_cast<unsigned>(output->manifest.integrity)
               << L" semantic_certified="
               << output->manifest.semanticCertified
               << L" layout_present=" << output->manifest.layoutPresent
               << L" closed_profiles=" << output->manifest.closedProfileBits
               << L" observed_controls=" << output->observedControlCount
               << L'\n';
    if (attempt == 1) {
        std::wcout << L"GRAPH_CAPTURE_BOUNDARY_ATTEMPT_COMPARE scenario="
                   << static_cast<unsigned>(context->scenario)
                   << L" mismatch_bits=" << AttemptMismatchBits(
                          context->spools[0].Result(),
                          context->spools[1].Result())
                   << L" first_records="
                   << context->spools[0].Result().facts.finalRecords.itemCount
                   << L" second_records="
                   << context->spools[1].Result().facts.finalRecords.itemCount
                   << L" first_bytes="
                   << context->spools[0].Result().facts.finalRecords.byteLength
                   << L" second_bytes="
                   << context->spools[1].Result().facts.finalRecords.byteLength
                   << L'\n';
    }
    return true;
}

bool BoundaryRelease(void*) noexcept { return true; }
bool BoundaryRestore(void*, const CapturedState&) noexcept { return true; }
bool BoundaryCancelled(void*) noexcept { return false; }
bool BoundaryBeginSession(void* raw, std::uint64_t serial) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || serial == 0 || context->sessionSerial != 0) {
        return false;
    }
    context->sessionSerial = serial;
    context->arena.ResetSession();
    return true;
}
bool BoundaryPreparePublication(void* raw, std::uint64_t serial) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || context->sessionSerial != serial) return false;
    return context->spools[0].Reset();
}
bool BoundaryAbortSession(void* raw, std::uint64_t serial) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || context->sessionSerial != serial) return false;
    const bool cleaned = context->spools[0].Reset() &&
        context->spools[1].Reset();
    context->attemptPayloads[0].clear();
    context->attemptPayloads[1].clear();
    context->arena.ResetSession();
    context->sessionSerial = 0;
    ++context->abortCount;
    return cleaned;
}
void BoundaryCommitSession(void* raw, std::uint64_t serial) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || context->sessionSerial != serial) return;
    static_cast<void>(context->spools[0].Reset());
    static_cast<void>(context->spools[1].Reset());
    context->attemptPayloads[0].clear();
    context->attemptPayloads[1].clear();
    context->arena.ResetSession();
    context->sessionSerial = 0;
    ++context->commitCount;
}

bool BoundaryPublish(
    void* raw,
    const PublicationInput& input) noexcept {
    auto* context = static_cast<BoundaryContext*>(raw);
    if (context == nullptr || context->store == nullptr ||
        !context->store->Publish(input, context->storeFailure)) return false;
    ++context->publishCount;
    return true;
}

struct BoundaryStoreObservation final {
    std::uint64_t generation = 0;
    size_t publishCount = 0;
    size_t nodeCount = 0;
    size_t anchorsCount = 0;
    size_t nativeDispatchReads = 0;
    size_t nativeAdapterReads = 0;
    size_t nativeAdapterCommits = 0;
    size_t nativeAdapterAborts = 0;
    size_t sessionAborts = 0;
    size_t sessionCommits = 0;
    bool sessionClosed = false;
    bool nativeAdapterNoPartialState = true;
};

CaptureStatus RunBoundaryScenario(
    const BoundaryScenario scenario,
    const wchar_t* const rendered,
    std::vector<DecodedRecord>* const decoded = nullptr,
    const NativeAnchorCase* const nativeAnchorCase = nullptr,
    const wchar_t* const nativeCtrlId = nullptr,
    BoundaryStoreObservation* const storeObservation = nullptr,
    const hancom::graph::store::FailurePoint storeFailure =
        hancom::graph::store::FailurePoint::None) {
    BoundaryContext context;
    context.scenario = scenario;
    context.storeFailure = storeFailure;
    context.templatePayloads = ReferenceClosurePayloads();
    if (nativeAnchorCase != nullptr && nativeCtrlId != nullptr) {
        context.nativeStoryReader = true;
        context.nativeStorySections = context.templatePayloads[0].sections;
        context.nativeStoryControls = context.templatePayloads[0].controls;
        context.nativeAnchorCase = *nativeAnchorCase;
        context.nativeCtrlId = nativeCtrlId;
    }
    context.renderedProbeText = rendered;
    context.storeRoot = std::filesystem::temp_directory_path() /
        (L"hwp-graph-boundary-store-" +
         std::to_wstring(GetCurrentProcessId()) + L"-" +
         std::to_wstring(static_cast<unsigned>(scenario)));
    std::error_code cleanupError;
    std::filesystem::remove_all(context.storeRoot, cleanupError);
    context.store = std::make_unique<GraphStore>(context.storeRoot.wstring());
    if (!context.store->Initialize()) return CaptureStatus::PublishFailed;
    ReaderSuite suite;
    suite.readerCount = kQualifiedReaderCount;
    suite.captureState = BoundaryCaptureState;
    suite.beginAttempt = BoundaryBegin;
    suite.runReader = BoundaryReader;
    suite.finishAttempt = BoundaryFinish;
    suite.releaseScans = BoundaryRelease;
    suite.restoreState = BoundaryRestore;
    suite.cancelled = BoundaryCancelled;
    suite.beginSession = BoundaryBeginSession;
    suite.preparePublication = BoundaryPreparePublication;
    suite.abortSession = BoundaryAbortSession;
    suite.commitSession = BoundaryCommitSession;
    CaptureCoordinator coordinator;
    const size_t nativeReadsBefore = NativeAnchorDispatchReadCount();
    CaptureDiagnostics captureDiagnostics;
    const CaptureStatus status = coordinator.Capture(
        suite, &context, BoundaryPublish, &context, {}, &captureDiagnostics);
    std::wcout << L"GRAPH_CAPTURE_BOUNDARY_STATUS scenario="
               << static_cast<unsigned>(scenario) << L" status="
               << static_cast<unsigned>(status) << L" failure_stage="
               << static_cast<unsigned>(captureDiagnostics.stage)
               << L" failure_attempt=" << captureDiagnostics.attempt
               << L" failure_reader=" << captureDiagnostics.reader
               << L" mismatch_bits=" << captureDiagnostics.mismatchBits
               << L" failure_detail=" << captureDiagnostics.failureDetail
               << L'\n';
    if (decoded != nullptr && status == CaptureStatus::Complete) {
        std::vector<std::uint8_t> bytes;
        const auto active = context.store->PinActive();
        const std::filesystem::path records = active == nullptr
            ? std::filesystem::path{}
            : std::filesystem::path(active->Path()) / L"records.hgn";
        if (active == nullptr || !ReadWholeFile(records, &bytes) ||
            !DecodeRecords(bytes, decoded)) {
            decoded->clear();
        }
    }
    context.spools[0].Reset();
    context.spools[1].Reset();
    if (storeObservation != nullptr) {
        storeObservation->generation = context.store->ActiveSerial();
        storeObservation->publishCount = context.publishCount;
        storeObservation->nativeDispatchReads =
            NativeAnchorDispatchReadCount() - nativeReadsBefore;
        storeObservation->nativeAdapterReads = context.nativeAdapterReads;
        storeObservation->nativeAdapterCommits = context.nativeAdapterCommits;
        storeObservation->nativeAdapterAborts = context.nativeAdapterAborts;
        storeObservation->nativeAdapterNoPartialState =
            context.nativeAdapterNoPartialState;
        storeObservation->sessionAborts = context.abortCount;
        storeObservation->sessionCommits = context.commitCount;
        storeObservation->sessionClosed = context.sessionSerial == 0;
        if (decoded != nullptr) {
            std::vector<StrictNode> nodes;
            std::vector<StrictEdge> edges;
            std::vector<StrictProperty> properties;
            if (ParseStrict(*decoded, &nodes, &edges, &properties)) {
                storeObservation->nodeCount = nodes.size();
                storeObservation->anchorsCount = static_cast<size_t>(
                    std::count_if(
                        edges.begin(), edges.end(), [](const StrictEdge& edge) {
                            return edge.kind == static_cast<std::uint16_t>(
                                hancom::graph::EdgeKind::Anchors);
                        }));
            }
        }
    }
    const bool stored = status == CaptureStatus::Complete
        ? context.publishCount == 1 &&
              context.store->ActiveSerial() == 1 &&
              context.store->PinActive() != nullptr
        : context.publishCount == 0 &&
              context.store->ActiveSerial() == 0 &&
              context.store->PinActive() == nullptr;
    context.store.reset();
    std::filesystem::remove_all(context.storeRoot, cleanupError);
    const std::filesystem::path boundaryRoot =
        std::filesystem::temp_directory_path();
    for (size_t attempt = 0; attempt < 2; ++attempt) {
        std::filesystem::remove_all(
            boundaryRoot /
                (L"hwp-graph-boundary-integration-" +
                 std::to_wstring(GetCurrentProcessId()) + L"-" +
                 std::to_wstring(attempt)),
            cleanupError);
    }
    const bool cleaned = !std::filesystem::exists(context.storeRoot) &&
        !std::filesystem::exists(
            boundaryRoot /
                (L"hwp-graph-boundary-integration-" +
                 std::to_wstring(GetCurrentProcessId()) + L"-0")) &&
        !std::filesystem::exists(
            boundaryRoot /
                (L"hwp-graph-boundary-integration-" +
                 std::to_wstring(GetCurrentProcessId()) + L"-1"));
    if (!stored || !cleaned) return CaptureStatus::PublishFailed;
    return status;
}

struct DefinitionIntegrationChecks final {
    bool kinds = false;
    bool referenceKinds = false;
    bool referenceAuthority = false;
    bool bodyStates = false;
    bool bodyNotApplicableWire = false;
    bool coverageCoordinates = false;
    bool originAndAggregateEvidence = false;
};

DefinitionIntegrationChecks DecodeDefinitionIntegration(
    const std::vector<DecodedRecord>& completeRecords,
    const std::vector<DecodedRecord>& terminalRecords) {
    DefinitionIntegrationChecks checks;
    std::vector<StrictNode> nodes;
    std::vector<StrictEdge> edges;
    std::vector<StrictProperty> properties;
    if (!ParseStrict(completeRecords, &nodes, &edges, &properties)) {
        return checks;
    }
    using hancom::graph::DefinitionKind;
    using hancom::graph::EdgeKind;
    using hancom::graph::NodeKind;
    std::map<NodeIdBytes, DefinitionKind> definitions;
    std::map<DefinitionKind, std::uint64_t> definitionCounts;
    std::map<NodeIdBytes, NodeKind> nodeKinds;
    for (const StrictNode& node : nodes) {
        nodeKinds[node.id] = static_cast<NodeKind>(node.kind);
        if (node.kind != static_cast<std::uint16_t>(NodeKind::Definition)) {
            continue;
        }
        const DecodedField* const kind = FindField(node.payload, 100);
        if (kind == nullptr || kind->value.size() != 2) return checks;
        const auto definitionKind =
            static_cast<DefinitionKind>(R16(kind->value.data()));
        definitions[node.id] = definitionKind;
        ++definitionCounts[definitionKind];
    }
    bool unknownEffectiveOrigin = false;
    bool generatedLayoutOrigin = false;
    bool aggregatesMatch = true;
    for (const StrictProperty& property : properties) {
        unknownEffectiveOrigin = unknownEffectiveOrigin ||
            property.key == 1000 && property.origin ==
                static_cast<std::uint8_t>(
                    hancom::graph::PropertyOrigin::Unknown);
        generatedLayoutOrigin = generatedLayoutOrigin ||
            property.key == 12000 && property.origin ==
                static_cast<std::uint8_t>(
                    hancom::graph::PropertyOrigin::Generated);
    }
    for (const StrictNode& node : nodes) {
        if (node.kind != static_cast<std::uint16_t>(NodeKind::Definition)) {
            continue;
        }
        const DecodedField* const declared = FindField(node.payload, 103);
        std::vector<hancom::graph::codec::SemanticPropertyInput> semantic;
        for (const StrictProperty& property : properties) {
            if (property.owner == node.id && property.state ==
                    static_cast<std::uint8_t>(
                        hancom::graph::ObservationState::Value)) {
                semantic.push_back({
                    property.key,
                    static_cast<hancom::graph::PropertyOrigin>(
                        property.origin),
                    property.semanticValue});
            }
        }
        const auto aggregate =
            hancom::graph::codec::DefinitionPropertyAggregate(
                std::move(semantic));
        aggregatesMatch = aggregatesMatch && declared != nullptr &&
            declared->value.size() == aggregate.bytes.size() &&
            std::equal(declared->value.begin(), declared->value.end(),
                       aggregate.bytes.begin());
    }
    checks.originAndAggregateEvidence = unknownEffectiveOrigin &&
        generatedLayoutOrigin && aggregatesMatch;
    checks.kinds = definitions.size() == 7 &&
        definitionCounts[DefinitionKind::Style] == 1 &&
        definitionCounts[DefinitionKind::CharacterShape] == 1 &&
        definitionCounts[DefinitionKind::ParagraphShape] == 1 &&
        definitionCounts[DefinitionKind::BorderFill] == 1 &&
        definitionCounts[DefinitionKind::TabDef] == 1 &&
        definitionCounts[DefinitionKind::PageDef] == 1 &&
        definitionCounts[DefinitionKind::ColumnDef] == 1;
    std::map<EdgeKind, std::uint64_t> referenceCounts;
    std::map<NodeIdBytes, std::vector<const StrictEdge*>> bySource;
    bool legal = true;
    for (const StrictEdge& edge : edges) {
        const auto edgeKind = static_cast<EdgeKind>(edge.kind);
        if (edgeKind != EdgeKind::StyleRef &&
            edgeKind != EdgeKind::CharacterShapeRef &&
            edgeKind != EdgeKind::ParagraphShapeRef &&
            edgeKind != EdgeKind::BorderFillRef &&
            edgeKind != EdgeKind::TabDefRef &&
            edgeKind != EdgeKind::PageDefRef &&
            edgeKind != EdgeKind::ColumnDefRef) continue;
        ++referenceCounts[edgeKind];
        bySource[edge.source].push_back(&edge);
        const auto source = nodeKinds.find(edge.source);
        const auto target = definitions.find(edge.target);
        if (source == nodeKinds.end() || target == definitions.end() ||
            !hancom::graph::IsLegalReferenceEdge(
                edgeKind, source->second, NodeKind::Definition,
                DefinitionKind::Style, target->second)) {
            legal = false;
        }
    }
    checks.referenceKinds =
        referenceCounts[EdgeKind::StyleRef] == 35 &&
        referenceCounts[EdgeKind::CharacterShapeRef] == 35 &&
        referenceCounts[EdgeKind::ParagraphShapeRef] == 35 &&
        referenceCounts[EdgeKind::BorderFillRef] == 32 &&
        referenceCounts[EdgeKind::TabDefRef] == 35 &&
        referenceCounts[EdgeKind::PageDefRef] == 2 &&
        referenceCounts[EdgeKind::ColumnDefRef] == 2;
    for (const auto& source : bySource) {
        std::set<EdgeKind> seen;
        std::uint16_t priorKind = 0;
        std::uint64_t priorOrdinal = 0;
        bool first = true;
        for (const StrictEdge* const edge : source.second) {
            const auto kind = static_cast<EdgeKind>(edge->kind);
            if (!seen.insert(kind).second ||
                (!first && (edge->kind < priorKind ||
                 (edge->kind == priorKind &&
                  edge->ordinal <= priorOrdinal)))) {
                legal = false;
            }
            first = false;
            priorKind = edge->kind;
            priorOrdinal = edge->ordinal;
        }
    }
    for (const StrictNode& node : nodes) {
        const auto nodeKind = static_cast<NodeKind>(node.kind);
        std::set<EdgeKind> actual;
        const auto found = bySource.find(node.id);
        if (found != bySource.end()) {
            for (const StrictEdge* const edge : found->second) {
                actual.insert(static_cast<EdgeKind>(edge->kind));
            }
        }
        if (nodeKind == NodeKind::Paragraph) {
            legal = legal && actual == std::set<EdgeKind>{
                EdgeKind::StyleRef, EdgeKind::ParagraphShapeRef,
                EdgeKind::TabDefRef};
        } else if (nodeKind == NodeKind::CharacterRun) {
            legal = legal && actual ==
                std::set<EdgeKind>{EdgeKind::CharacterShapeRef};
        } else if (nodeKind == NodeKind::TableCell) {
            legal = legal && actual ==
                std::set<EdgeKind>{EdgeKind::BorderFillRef};
        } else if (nodeKind == NodeKind::Section) {
            legal = legal && actual == std::set<EdgeKind>{
                EdgeKind::PageDefRef, EdgeKind::ColumnDefRef};
        }
    }
    checks.referenceAuthority = legal;

    std::vector<StrictNode> terminalNodes;
    std::vector<StrictEdge> terminalEdges;
    std::vector<StrictProperty> terminalProperties;
    if (!ParseStrict(
            terminalRecords, &terminalNodes, &terminalEdges,
            &terminalProperties)) return checks;
    std::map<NodeIdBytes, DefinitionKind> terminalDefinitions;
    std::map<DefinitionKind, std::uint64_t> terminalKindCounts;
    bool propertyBagsExact = true;
    size_t definitionPropertyCount = 0;
    for (const StrictNode& node : terminalNodes) {
        if (node.kind != static_cast<std::uint16_t>(NodeKind::Definition)) {
            continue;
        }
        const DecodedField* const kind = FindField(node.payload, 100);
        const DecodedField* const bag = FindField(node.payload, 102);
        if (kind == nullptr || kind->value.size() != 2 || bag == nullptr) {
            return checks;
        }
        const auto definitionKind =
            static_cast<DefinitionKind>(R16(kind->value.data()));
        terminalDefinitions[node.id] = definitionKind;
        ++terminalKindCounts[definitionKind];
        std::vector<std::uint64_t> expectedIds;
        for (const StrictProperty& property : terminalProperties) {
            if (property.owner != node.id) continue;
            ++definitionPropertyCount;
            expectedIds.push_back(property.recordId);
            const auto* const rule =
                hancom::graph::FindPropertyRule(property.key);
            propertyBagsExact = propertyBagsExact &&
                property.ownerField == 102 && rule != nullptr &&
                hancom::graph::IsPropertyApplicable(
                    *rule, NodeKind::Definition, 102, true, definitionKind);
        }
        propertyBagsExact = propertyBagsExact &&
            DecodeIdArray(bag->value) == expectedIds;
    }
    std::map<DefinitionKind, hancom::graph::CoverageState> bodyCoverage;
    bool coverageShapeExact = true;
    for (const DecodedRecord& record : terminalRecords) {
        if (record.kind != static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Coverage)) continue;
        const DecodedField* const owner = FindField(record.fields, 1);
        const DecodedField* const profile = FindField(record.fields, 2);
        const DecodedField* const state = FindField(record.fields, 3);
        const DecodedField* const ownerKind = FindField(record.fields, 6);
        const DecodedField* const field = FindField(record.fields, 7);
        if (owner == nullptr || owner->value.size() != 16 ||
            profile == nullptr || profile->value.size() != 1 ||
            state == nullptr || state->value.size() != 1 ||
            ownerKind == nullptr || ownerKind->value.size() != 2 ||
            field == nullptr || field->value.size() != 2) continue;
        NodeIdBytes ownerId{};
        std::copy_n(owner->value.begin(), ownerId.size(), ownerId.begin());
        const auto definition = terminalDefinitions.find(ownerId);
        if (definition == terminalDefinitions.end() ||
            R16(field->value.data()) != 102) continue;
        const auto coverageState = static_cast<hancom::graph::CoverageState>(
            state->value[0]);
        coverageShapeExact = coverageShapeExact &&
            R16(ownerKind->value.data()) == static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Node) &&
            profile->value[0] == static_cast<std::uint8_t>(
                hancom::graph::OwningProfile(definition->second)) &&
            bodyCoverage.emplace(definition->second, coverageState).second;
    }
    const auto bodyIs = [&bodyCoverage](
        const DefinitionKind kind,
        const hancom::graph::CoverageState state) {
        const auto found = bodyCoverage.find(kind);
        return found != bodyCoverage.end() && found->second == state;
    };
    const bool allKinds = terminalDefinitions.size() == 8 &&
        terminalKindCounts.size() == 8 &&
        std::all_of(
            terminalKindCounts.begin(), terminalKindCounts.end(),
            [](const auto& item) { return item.second == 1; });
    const bool valueBodiesHaveNoCoverage =
        bodyCoverage.find(DefinitionKind::ParagraphShape) ==
            bodyCoverage.end() &&
        bodyCoverage.find(DefinitionKind::PageDef) == bodyCoverage.end() &&
        bodyCoverage.find(DefinitionKind::ColumnDef) == bodyCoverage.end();
    const bool notApplicableBodies =
        bodyIs(DefinitionKind::CharacterShape,
               hancom::graph::CoverageState::NotApplicable) &&
        bodyIs(DefinitionKind::Numbering,
               hancom::graph::CoverageState::NotApplicable) &&
        bodyIs(DefinitionKind::TabDef,
               hancom::graph::CoverageState::NotApplicable);
    checks.bodyStates = coverageShapeExact &&
        bodyIs(DefinitionKind::Style,
               hancom::graph::CoverageState::ReadFailed) &&
        bodyIs(DefinitionKind::BorderFill,
               hancom::graph::CoverageState::NotExposed) &&
        notApplicableBodies && valueBodiesHaveNoCoverage &&
        bodyCoverage.size() == 5;
    const auto* const characterBodyRule = hancom::graph::FindPropertyRule(1000);
    const auto* const paragraphBodyRule = hancom::graph::FindPropertyRule(2000);
    const bool bodyApplicabilityNonVacuous = characterBodyRule != nullptr &&
        paragraphBodyRule != nullptr &&
        hancom::graph::IsPropertyApplicable(
            *characterBodyRule, NodeKind::Definition, 102, true,
            DefinitionKind::CharacterShape) &&
        !hancom::graph::IsPropertyApplicable(
            *characterBodyRule, NodeKind::Definition, 102, true,
            DefinitionKind::ParagraphShape) &&
        hancom::graph::IsPropertyApplicable(
            *paragraphBodyRule, NodeKind::Definition, 102, true,
            DefinitionKind::ParagraphShape) &&
        !hancom::graph::IsPropertyApplicable(
            *paragraphBodyRule, NodeKind::Definition, 102, true,
            DefinitionKind::CharacterShape);
    checks.bodyNotApplicableWire = checks.bodyStates && allKinds &&
        propertyBagsExact && definitionPropertyCount == 3 &&
        bodyApplicabilityNonVacuous;
    size_t globalCatalogCoordinates = 0;
    std::map<std::tuple<bool, std::uint16_t, std::uint16_t, std::uint8_t>,
             size_t> coverageCoordinateCounts;
    for (const DecodedRecord& record : completeRecords) {
        if (record.kind != static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Coverage)) continue;
        const DecodedField* const owner = FindField(record.fields, 1);
        const DecodedField* const profile = FindField(record.fields, 2);
        const DecodedField* const ownerKind = FindField(record.fields, 6);
        const DecodedField* const ownerField = FindField(record.fields, 7);
        if (profile == nullptr || profile->value.size() != 1 ||
            ownerKind == nullptr || ownerKind->value.size() != 2 ||
            ownerField == nullptr || ownerField->value.size() != 2) continue;
        const std::uint16_t decodedOwnerKind = R16(ownerKind->value.data());
        const std::uint16_t decodedOwnerField = R16(ownerField->value.data());
        ++coverageCoordinateCounts[{owner != nullptr, decodedOwnerKind,
                                    decodedOwnerField, profile->value[0]}];
        if (owner == nullptr &&
            profile->value[0] == hancom::graph::kNoProfile &&
            decodedOwnerKind == static_cast<std::uint16_t>(
                hancom::graph::RecordKind::Manifest) &&
            decodedOwnerField == 0) {
            ++globalCatalogCoordinates;
        }
    }
    // The four native catalog terminal flags are one manifest-owned global
    // catalog aggregate on schema v1 wire, not four duplicate coordinates.
    checks.coverageCoordinates = globalCatalogCoordinates == 1;
    std::wcout << L"GRAPH_CAPTURE_INTEGRATION_COVERAGE_DETAIL expected=1 actual="
               << globalCatalogCoordinates << L'\n';
    for (const auto& [coordinate, count] : coverageCoordinateCounts) {
        std::wcout << L"GRAPH_CAPTURE_INTEGRATION_COVERAGE_COORDINATE owner_present="
                   << std::get<0>(coordinate) << L" owner_kind="
                   << std::get<1>(coordinate) << L" owner_field="
                   << std::get<2>(coordinate) << L" profile="
                   << static_cast<unsigned>(std::get<3>(coordinate))
                   << L" count=" << count << L'\n';
    }
    return checks;
}

bool NativeLocatorControlSites(
    const std::vector<DecodedRecord>& records) {
    std::vector<StrictNode> nodes;
    std::vector<StrictEdge> edges;
    std::vector<StrictProperty> properties;
    if (!ParseStrict(records, &nodes, &edges, &properties)) return false;
    std::set<NodeIdBytes> ids;
    size_t generic = 0;
    size_t gso = 0;
    size_t absent = 0;
    size_t duplicate = 0;
    for (const StrictNode& node : nodes) {
        if (node.kind != static_cast<std::uint16_t>(
                hancom::graph::NodeKind::GenericControl)) continue;
        ++generic;
        ids.insert(node.id);
        if (PayloadObservedText(node, 100) != L"gso") continue;
        ++gso;
        const DecodedField* const instance = FindField(node.payload, 101);
        if (instance == nullptr || instance->value.empty()) return false;
        if (instance->value[0] == static_cast<std::uint8_t>(
                hancom::graph::ObservationState::NotExposed)) {
            ++absent;
        } else if (PayloadObservedText(node, 101) == L"duplicate") {
            ++duplicate;
        }
    }
    return generic == 4 && ids.size() == generic && gso == 3 &&
        absent == 1 && duplicate == 2;
}

bool MixedControlFamilyOrder(
    const std::vector<DecodedRecord>& records) {
    std::vector<StrictNode> nodes;
    std::vector<StrictEdge> edges;
    std::vector<StrictProperty> properties;
    if (!ParseStrict(records, &nodes, &edges, &properties)) return false;
    using Key = std::tuple<std::int64_t, std::uint64_t, std::uint16_t>;
    std::map<NodeIdBytes, Key> priorByParent;
    std::set<std::uint16_t> families;
    bool ordered = true;
    for (const StrictNode& node : nodes) {
        const auto kind = static_cast<hancom::graph::NodeKind>(node.kind);
        if (kind != hancom::graph::NodeKind::GenericControl &&
            kind != hancom::graph::NodeKind::Table &&
            kind != hancom::graph::NodeKind::Image) continue;
        const DecodedField* const anchor = FindField(node.payload, 103);
        const DecodedField* const ordinal = FindField(node.payload, 105);
        if (!node.parentPresent || anchor == nullptr ||
            anchor->value.size() < 48 || anchor->value[0] !=
                static_cast<std::uint8_t>(
                    hancom::graph::ObservationState::Value) ||
            ordinal == nullptr || ordinal->value.size() != 8) return false;
        const std::uint64_t detailUnits = R64(anchor->value.data() + 8);
        const size_t valueAt = 24 + static_cast<size_t>(detailUnits) * 2;
        if (anchor->value.size() < valueAt + 24) return false;
        const Key key{
            static_cast<std::int64_t>(R64(
                anchor->value.data() + valueAt + 16)),
            R64(ordinal->value.data()), node.kind};
        const auto prior = priorByParent.find(node.parent);
        if (prior != priorByParent.end() && key < prior->second)
            ordered = false;
        priorByParent[node.parent] = key;
        families.insert(node.kind);
    }
    return ordered && families.size() == 3;
}

bool ApplicableAnchorAvailabilityBoundarySmoke() {
    const std::array<NativeAnchorCase, 7> unavailableCases{
        NativeAnchorCase::DispatchFailure,
        NativeAnchorCase::MissingList,
        NativeAnchorCase::InvalidList,
        NativeAnchorCase::MissingPara,
        NativeAnchorCase::InvalidPara,
        NativeAnchorCase::MissingPos,
        NativeAnchorCase::InvalidPos,
    };
    const NativeAnchorCase availableCase = NativeAnchorCase::Available;
    bool available = true;
    bool genericUnavailable = true;
    bool tableUnavailable = true;
    bool imageUnavailable = true;
    bool zeroPartialState = true;
    for (const wchar_t* const family : {L"gso", L"tbl", L"$pic"}) {
        std::vector<DecodedRecord> availableRecords;
        BoundaryStoreObservation availableStore;
        const CaptureStatus availableStatus = RunBoundaryScenario(
            BoundaryScenario::Complete, L"native-anchor-available",
            &availableRecords, &availableCase, family, &availableStore);
        const bool familyAvailable =
            availableStatus == CaptureStatus::Complete &&
            availableStore.publishCount == 1 &&
            availableStore.generation == 1 &&
            availableStore.nodeCount != 0 &&
            availableStore.anchorsCount != 0 &&
            availableStore.nativeAdapterReads != 0 &&
            availableStore.nativeDispatchReads ==
                availableStore.nativeAdapterReads &&
            availableStore.nativeAdapterCommits ==
                availableStore.nativeAdapterReads &&
            availableStore.nativeAdapterAborts == 0;
        std::wcout << L"GRAPH_CAPTURE_NATIVE_TO_STORE_AVAILABLE_DETAIL family="
                   << family << L" status="
                   << static_cast<unsigned>(availableStatus)
                   << L" generation=" << availableStore.generation
                   << L" publish=" << availableStore.publishCount
                   << L" nodes=" << availableStore.nodeCount
                   << L" anchors=" << availableStore.anchorsCount
                   << L" adapter=" << availableStore.nativeAdapterReads
                   << L" dispatch=" << availableStore.nativeDispatchReads
                   << L" commit=" << availableStore.nativeAdapterCommits
                   << L" abort=" << availableStore.nativeAdapterAborts
                   << L'\n';
        available = available && familyAvailable;
        for (const NativeAnchorCase nativeFailure : unavailableCases) {
            std::vector<DecodedRecord> records;
            BoundaryStoreObservation failedStore;
            const CaptureStatus status = RunBoundaryScenario(
                BoundaryScenario::Complete, L"native-anchor-read-failed",
                &records, &nativeFailure, family, &failedStore);
            const bool terminal =
                status == CaptureStatus::IncompleteCapture &&
                static_cast<std::uint8_t>(status) == 2 &&
                records.empty() && failedStore.generation == 0 &&
                failedStore.publishCount == 0 &&
                failedStore.nodeCount == 0 &&
                failedStore.anchorsCount == 0 &&
                failedStore.nativeAdapterReads != 0 &&
                failedStore.nativeDispatchReads ==
                    failedStore.nativeAdapterReads &&
                failedStore.nativeAdapterCommits == 0 &&
                failedStore.nativeAdapterAborts ==
                    failedStore.nativeAdapterReads &&
                failedStore.nativeAdapterNoPartialState;
            if (std::wcscmp(family, L"gso") == 0)
                genericUnavailable = genericUnavailable && terminal;
            else if (std::wcscmp(family, L"tbl") == 0)
                tableUnavailable = tableUnavailable && terminal;
            else
                imageUnavailable = imageUnavailable && terminal;
            zeroPartialState = zeroPartialState && terminal;
        }
    }
    const bool matrix = available && genericUnavailable &&
        tableUnavailable && imageUnavailable && zeroPartialState;
    std::wcout << L"GRAPH_CAPTURE_ANCHOR_AVAILABLE_PUBLISHES_EXACTLY_ONCE "
               << available << L'\n'
               << L"GRAPH_CAPTURE_GENERIC_ANCHOR_READ_FAILED_TERMINAL "
               << genericUnavailable << L'\n'
               << L"GRAPH_CAPTURE_TABLE_ANCHOR_READ_FAILED_TERMINAL "
               << tableUnavailable << L'\n'
               << L"GRAPH_CAPTURE_IMAGE_ANCHOR_READ_FAILED_TERMINAL "
               << imageUnavailable << L'\n'
               << L"GRAPH_CAPTURE_NATIVE_TO_STORE_ZERO_GENERATION_PUBLISH_NODE_ANCHORS "
               << zeroPartialState << L'\n'
               << L"GRAPH_CAPTURE_NATIVE_TO_STORE_NO_PARTIAL_STATE "
               << matrix << L'\n'
               << L"GRAPH_CAPTURE_APPLICABLE_ANCHOR_FAIL_CLOSED_MATRIX "
               << matrix << L'\n';
    return matrix;
}

bool StructuredBoundaryIntegrationSmoke() {
    std::vector<DecodedRecord> completeRecords;
    std::vector<DecodedRecord> terminalRecords;
    const CaptureStatus completeStatus = RunBoundaryScenario(
        BoundaryScenario::Complete,
        L"RESULT\tFAIL\tSHOULD_NOT_CONTROL_PUBLICATION",
        &completeRecords);
    const bool complete = completeStatus == CaptureStatus::Complete;
    const CaptureStatus renderedStatus = RunBoundaryScenario(
        BoundaryScenario::Complete,
        L"RESULT\tPASS\tALSO_NOT_AUTHORITY");
    const bool renderedIgnored = renderedStatus == CaptureStatus::Complete;
    const CaptureStatus terminalStatus = RunBoundaryScenario(
        BoundaryScenario::ReadFailedTerminal,
        L"opaque", &terminalRecords);
    const bool terminal = terminalStatus == CaptureStatus::Complete;
    const DefinitionIntegrationChecks definitions =
        DecodeDefinitionIntegration(completeRecords, terminalRecords);
    const bool physicalOwnerBlocks =
        HasPhysicalOwnerBlockGrammar(completeRecords) &&
        RejectsDocumentAggregatedPhysicalOrder(completeRecords);
    const bool nativeLocatorSites =
        NativeLocatorControlSites(completeRecords);
    const bool mixedControlOrder = MixedControlFamilyOrder(completeRecords);
    const bool anchorAvailability =
        ApplicableAnchorAvailabilityBoundarySmoke();
    const bool inconclusive = RunBoundaryScenario(
        BoundaryScenario::Inconclusive,
        L"RESULT\tPASS") == CaptureStatus::IncompleteCapture;
    const bool missingTable = RunBoundaryScenario(
        BoundaryScenario::MissingTableSelection,
        L"RESULT\tPASS") == CaptureStatus::IncompleteCapture;
    std::wcout << L"GRAPH_CAPTURE_INTEGRATION_STATUS "
               << static_cast<unsigned>(completeStatus) << L','
               << static_cast<unsigned>(renderedStatus) << L','
               << static_cast<unsigned>(terminalStatus) << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_COMPLETE " << complete << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_RENDERED_IGNORED "
               << renderedIgnored << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_READ_FAILED_TERMINAL "
               << terminal << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_INCONCLUSIVE "
               << inconclusive << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_MISSING_TABLE "
               << missingTable << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_DEFINITION_KINDS "
               << definitions.kinds << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_REFERENCE_KINDS "
               << definitions.referenceKinds << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_REFERENCE_AUTHORITY "
               << definitions.referenceAuthority << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_DEFINITION_BODY_STATES "
               << definitions.bodyStates << L'\n'
               << L"DEFINITION_BODY_NOT_APPLICABLE_WIRE "
               << definitions.bodyNotApplicableWire << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_COVERAGE_COORDINATES "
               << definitions.coverageCoordinates << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_PHYSICAL_OWNER_BLOCKS "
               << physicalOwnerBlocks << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_ORIGIN_AGGREGATE_EVIDENCE "
               << definitions.originAndAggregateEvidence << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_NATIVE_LOCATOR_SITES "
               << nativeLocatorSites << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_MIXED_CONTROL_ORDER "
               << mixedControlOrder << L'\n'
               << L"GRAPH_CAPTURE_INTEGRATION_ANCHOR_FAIL_CLOSED "
               << anchorAvailability << L'\n';
    return complete && renderedIgnored && terminal && anchorAvailability &&
        inconclusive && missingTable && definitions.kinds && definitions.referenceKinds &&
        definitions.referenceAuthority && definitions.bodyStates &&
        definitions.bodyNotApplicableWire &&
        definitions.coverageCoordinates &&
        definitions.originAndAggregateEvidence && physicalOwnerBlocks &&
        nativeLocatorSites && mixedControlOrder;
}

bool LayoutEnvironmentTransportSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-layout-environment-transport-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    const std::vector<std::uint8_t> environment96 =
        NormativeEnvironment(96);
    const std::vector<std::uint8_t> environment120 =
        NormativeEnvironment(120);
    std::vector<ReaderPayload> payloads =
        CertifiedLayoutPayloads(environment96);
    CaptureSpool mismatched;
    CaptureSpool matched;
    hancom::graph::capture::CaptureIdentityArena arena;
    const bool rejected = !mismatched.Build(
        root / L"mismatched", payloads, environment120, arena);
    const bool accepted = matched.Build(
        root / L"matched", payloads, environment96, arena);
    mismatched.Reset();
    matched.Reset();
    std::filesystem::remove_all(root, error);
    return rejected && accepted;
}

bool CleanupFailureIsObservableSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) return false;
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-cleanup-failure-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    CaptureSpool spool;
    hancom::graph::capture::CaptureIdentityArena arena;
    if (!spool.Build(root, SealedPayloads(), NormativeEnvironment(), arena)) {
        return false;
    }
    HANDLE blocker = CreateFileW(
        (root / L"records.hgn").c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (blocker == INVALID_HANDLE_VALUE) return false;
    const bool firstReset = spool.Reset();
    const bool retained = std::filesystem::exists(root);
    CloseHandle(blocker);
    const bool secondReset = spool.Reset();
    const bool removed = !std::filesystem::exists(root);
    std::wcout << L"GRAPH_CAPTURE_CLEANUP_DETAIL first=" << firstReset
               << L" retained=" << retained
               << L" second=" << secondReset
               << L" removed=" << removed << L'\n';
    return !firstReset && retained && secondReset && removed;
}

bool ProbeTableInstanceIdDecodedNodeSmoke() {
    using hancom::graph::NodeKind;
    using hancom::graph::ObservationState;
    using hancom::official_api::capability::RawCall;
    using hancom::official_api::capability::SemanticStatus;

    struct DecodedInstance final {
        bool adapted = false;
        bool decoded = false;
        bool present = false;
        bool buildSeedExact = false;
        bool receiptExact = false;
        std::wstring value{};
        std::wstring buildKey{};
        std::wstring seedKey{};
        size_t wireBytes = 0;
        NodeIdBytes firstId{};
        NodeIdBytes secondId{};
    };
    size_t serial = 0;
    hancom::graph::capture::CaptureIdentityArena crossStateArena;
    const auto run = [&serial, &crossStateArena](
                         const bool available,
                         const wchar_t* const nativeValue) {
        DecodedInstance result;
        RawCall call;
        call.semantic = available
            ? SemanticStatus::Pass
            : SemanticStatus::NameFailure;
        if (available) {
            call.result = CComVariant(nativeValue);
        }
        hancom::graph::capture::TableObservation observed;
        result.adapted =
            hancom::official_api::capability::AdaptTableInstanceIdObservation(
                call, &observed);
        if (!result.adapted) return result;

        std::vector<ReaderPayload> payloads = SealedPayloads();
        if (payloads[4].tables.empty()) return DecodedInstance{};
        payloads[4].tables.resize(1);
        for (const size_t reader : {size_t{0}, size_t{3}}) {
            auto& controls = payloads[reader].controls;
            controls.erase(
                std::remove_if(
                    controls.begin(), controls.end(),
                    [](const auto& control) {
                        return control.ctrlId == L"tbl" &&
                            control.headCtrlOrdinal == 7;
                    }),
                controls.end());
        }
        const auto nestedRun = [](const std::wstring& identity) {
            return identity.rfind(L"301:", 0) == 0 ||
                identity.rfind(L"302:", 0) == 0 ||
                identity.rfind(L"303:", 0) == 0 ||
                identity.rfind(L"304:", 0) == 0;
        };
        auto& effective = payloads[2];
        effective.properties.erase(
            std::remove_if(
                effective.properties.begin(), effective.properties.end(),
                [&nestedRun](const auto& property) {
                    return nestedRun(property.targetIdentity);
                }),
            effective.properties.end());
        effective.definitionReferences.erase(
            std::remove_if(
                effective.definitionReferences.begin(),
                effective.definitionReferences.end(),
                [&nestedRun](const auto& reference) {
                    return nestedRun(reference.sourceIdentity);
                }),
            effective.definitionReferences.end());
        effective.coverageFacts.erase(
            std::remove_if(
                effective.coverageFacts.begin(),
                effective.coverageFacts.end(),
                [&nestedRun](const auto& coverage) {
                    return nestedRun(coverage.targetIdentity);
                }),
            effective.coverageFacts.end());
        auto& table = payloads[4].tables.front();
        table.instanceIdPresent = observed.instanceIdPresent;
        table.instanceId = observed.instanceId;
        for (auto& property : payloads[6].layoutProperties) {
            if (property.target ==
                    hancom::graph::capture::PropertyTarget::Table &&
                property.targetIdentity == L"tbl-outer") {
                property.targetIdentity =
                    hancom::graph::capture::CanonicalNativeSiteIdentity(
                        hancom::graph::capture::PropertyTarget::Table,
                        L"tbl", table.headCtrlOrdinal, table.anchor,
                        table.instanceIdPresent, table.instanceId);
            }
        }
        for (const size_t reader : {size_t{0}, size_t{3}}) {
            for (auto& control : payloads[reader].controls) {
                if (control.ctrlId == L"tbl" &&
                    control.headCtrlOrdinal == table.headCtrlOrdinal) {
                    control.instanceIdPresent = observed.instanceIdPresent;
                    control.instanceId = observed.instanceId;
                }
            }
        }

        const std::filesystem::path root =
            std::filesystem::temp_directory_path() /
            (L"hwp-graph-probe-table-instance-" +
             std::to_wstring(GetCurrentProcessId()) + L"-" +
             std::to_wstring(serial++));
        std::error_code cleanupError;
        std::filesystem::remove_all(root, cleanupError);
        CaptureSpool spool;
        CaptureSpool recaptured;
        hancom::graph::capture::CaptureIdentityArena seededArena;
        std::vector<std::uint8_t> bytes;
        std::vector<DecodedRecord> records;
        std::vector<StrictNode> nodes;
        std::vector<StrictEdge> edges;
        std::vector<StrictProperty> properties;
        RecertifySyntheticLayout(&payloads);
        const bool built = spool.Build(
                root, payloads, NormativeEnvironment(), crossStateArena) &&
            ReadWholeFile(root / L"records.hgn", &bytes) &&
            DecodeRecords(bytes, &records) &&
            ParseStrict(records, &nodes, &edges, &properties);
        if (built) {
            for (const StrictNode& node : nodes) {
                std::uint64_t ordinal = 0;
                if (node.kind != static_cast<std::uint16_t>(NodeKind::Table) ||
                    !PayloadUint64(node, 105, &ordinal) || ordinal != 3) {
                    continue;
                }
                const DecodedField* const instance =
                    FindField(node.payload, 101);
                if (instance == nullptr || instance->value.size() < 24) {
                    break;
                }
                const auto state = static_cast<ObservationState>(
                    instance->value[0]);
                result.present = state == ObservationState::Value;
                result.value = result.present
                    ? PayloadObservedText(node, 101)
                    : std::wstring();
                result.wireBytes = instance->value.size();
                result.firstId = node.id;
                result.decoded = result.present ||
                    state == ObservationState::NotExposed;
                break;
            }
        }
        const bool seeded = result.decoded &&
            hancom::graph::capture::SeedArenaFromRecordStream(
                {bytes.data(), bytes.size()}, &seededArena);
        const auto tableKey = [](const std::vector<std::wstring>& keys) {
            const std::wstring oldPrefix =
                L"control:" + std::to_wstring(static_cast<std::uint16_t>(
                    hancom::graph::NodeKind::Table)) + L":";
            const auto found = std::find_if(
                keys.begin(), keys.end(), [&oldPrefix](const auto& key) {
                    return key.rfind(L"table-owner:", 0) == 0 ||
                        key.rfind(L"table-story:", 0) == 0 ||
                        key.rfind(oldPrefix, 0) == 0;
                });
            return found == keys.end() ? std::wstring{} : *found;
        };
        result.buildKey = tableKey(crossStateArena.AcquiredKeys());
        result.seedKey = tableKey(seededArena.SeededKeys());
        std::vector<std::uint8_t> recapturedBytes;
        std::vector<DecodedRecord> recapturedRecords;
        std::vector<StrictNode> recapturedNodes;
        const std::filesystem::path recapturedRoot = root.wstring() + L"-seed";
        const bool recapturedBuilt = seeded && recaptured.Build(
                recapturedRoot, payloads, NormativeEnvironment(), seededArena) &&
            ReadWholeFile(
                recapturedRoot / L"records.hgn", &recapturedBytes) &&
            DecodeRecords(recapturedBytes, &recapturedRecords);
        if (recapturedBuilt) {
            std::vector<StrictEdge> recapturedEdges;
            std::vector<StrictProperty> recapturedProperties;
            if (ParseStrict(
                    recapturedRecords, &recapturedNodes, &recapturedEdges,
                    &recapturedProperties)) {
                for (const StrictNode& node : recapturedNodes) {
                    std::uint64_t ordinal = 0;
                    if (node.kind == static_cast<std::uint16_t>(NodeKind::Table) &&
                        PayloadUint64(node, 105, &ordinal) && ordinal == 3) {
                        result.secondId = node.id;
                        break;
                    }
                }
            }
        }
        result.buildSeedExact = recapturedBuilt &&
            result.firstId == result.secondId &&
            !result.buildKey.empty() && result.buildKey == result.seedKey &&
            result.buildKey.rfind(L"control:", 0) != 0;
        result.receiptExact = result.buildSeedExact &&
            seededArena.Receipt().remaps.empty() &&
            seededArena.Receipt().tombstones.empty();
        spool.Reset();
        recaptured.Reset();
        std::filesystem::remove_all(root, cleanupError);
        std::filesystem::remove_all(recapturedRoot, cleanupError);
        if (cleanupError || std::filesystem::exists(root) ||
            std::filesystem::exists(recapturedRoot)) {
            result.decoded = false;
        }
        return result;
    };

    const DecodedInstance absent = run(false, L"ignored");
    const DecodedInstance empty = run(true, L"");
    const DecodedInstance value = run(true, L"probe-table-exact-42");
    const bool absentExact = absent.adapted && absent.decoded &&
        !absent.present && absent.value.empty() && absent.wireBytes == 24;
    const bool emptyExact = empty.adapted && empty.decoded && empty.present &&
        empty.value.empty() && empty.wireBytes == 32;
    const bool valueExact = value.adapted && value.decoded && value.present &&
        value.value == L"probe-table-exact-42" && value.wireBytes > 32;
    const bool negativeCollapse = absentExact && emptyExact && valueExact &&
        absent.present != empty.present && empty.value != value.value;
    const bool unavailableBuildSeedExact =
        absent.buildSeedExact && absent.receiptExact;
    const bool emptyBuildSeedExact = empty.buildSeedExact && empty.receiptExact;
    const bool valueBuildSeedExact = value.buildSeedExact && value.receiptExact;
    const bool stateIdentitySeparation = negativeCollapse &&
        absent.firstId != empty.firstId && absent.firstId != value.firstId &&
        empty.firstId != value.firstId;
    std::wcout << L"PROBE_TABLE_INSTANCE_ID_DETAIL absent="
               << absent.adapted << L"," << absent.decoded << L","
               << absent.present << L"," << absent.wireBytes
               << L" empty=" << empty.adapted << L"," << empty.decoded
               << L"," << empty.present << L"," << empty.wireBytes
               << L" value=" << value.adapted << L"," << value.decoded
               << L"," << value.present << L"," << value.wireBytes << L'\n'
               << L"PROBE_TABLE_INSTANCE_ID_ABSENT " << absentExact << L'\n'
               << L"PROBE_TABLE_INSTANCE_ID_PRESENT_EMPTY " << emptyExact
               << L'\n'
               << L"PROBE_TABLE_INSTANCE_ID_PRESENT_VALUE " << valueExact
               << L'\n'
               << L"PROBE_TABLE_INSTANCE_ID_NEGATIVE_COLLAPSE "
               << negativeCollapse << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_UNAVAILABLE_BUILD_SEED_EXACT "
               << unavailableBuildSeedExact << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_EMPTY_BUILD_SEED_EXACT "
               << emptyBuildSeedExact << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_NONEMPTY_BUILD_SEED_EXACT "
               << valueBuildSeedExact << L'\n'
               << L"TABLE_STABLE_KEY_ABSENCE_EMPTY_VALUE_SEPARATION "
               << stateIdentitySeparation << L'\n'
               << L"TABLE_STABLE_KEY_NO_CROSS_STATE_COLLISION "
               << stateIdentitySeparation << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_UNAVAILABLE_RAW build="
               << absent.buildKey << L" seed=" << absent.seedKey << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_EMPTY_RAW build="
               << empty.buildKey << L" seed=" << empty.seedKey << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_NONEMPTY_RAW build="
               << value.buildKey << L" seed=" << value.seedKey << L'\n';
    return absentExact && emptyExact && valueExact && negativeCollapse &&
        unavailableBuildSeedExact && emptyBuildSeedExact &&
        valueBuildSeedExact && stateIdentitySeparation;
}

bool UniqueCrossOwnerTableStableKeySmoke() {
    using hancom::graph::NodeKind;

    struct Capture final {
        bool complete = false;
        std::wstring buildKey{};
        std::wstring seedKey{};
        NodeIdBytes owner{};
        std::vector<std::uint8_t> locator{};
    };
    const auto payloads = []() {
        std::vector<ReaderPayload> result = SealedPayloads();
        if (result[4].tables.empty()) return std::vector<ReaderPayload>{};
        result[4].tables.resize(1);
        for (const size_t reader : {size_t{0}, size_t{3}}) {
            auto& controls = result[reader].controls;
            controls.erase(
                std::remove_if(
                    controls.begin(), controls.end(), [](const auto& control) {
                        return control.ctrlId == L"tbl" &&
                            control.headCtrlOrdinal == 7;
                    }),
                controls.end());
        }
        const auto nestedRun = [](const std::wstring& identity) {
            return identity.rfind(L"301:", 0) == 0 ||
                identity.rfind(L"302:", 0) == 0 ||
                identity.rfind(L"303:", 0) == 0 ||
                identity.rfind(L"304:", 0) == 0;
        };
        auto& effective = result[2];
        effective.properties.erase(
            std::remove_if(
                effective.properties.begin(), effective.properties.end(),
                [&nestedRun](const auto& property) {
                    return nestedRun(property.targetIdentity);
                }),
            effective.properties.end());
        effective.definitionReferences.erase(
            std::remove_if(
                effective.definitionReferences.begin(),
                effective.definitionReferences.end(),
                [&nestedRun](const auto& reference) {
                    return nestedRun(reference.sourceIdentity);
                }),
            effective.definitionReferences.end());
        effective.coverageFacts.erase(
            std::remove_if(
                effective.coverageFacts.begin(), effective.coverageFacts.end(),
                [&nestedRun](const auto& coverage) {
                    return nestedRun(coverage.targetIdentity);
                }),
            effective.coverageFacts.end());
        return result;
    }();
    if (payloads.size() != kQualifiedReaderCount) return false;

    size_t serial = 0;
    const auto capture = [&payloads, &serial](const std::uint8_t prefix) {
        Capture result;
        DescendingUuidSource source{prefix, 0x00ffffffU};
        hancom::graph::capture::CaptureIdentityArena buildArena(Source(&source));
        hancom::graph::capture::CaptureIdentityArena seedArena;
        const std::filesystem::path root =
            std::filesystem::temp_directory_path() /
            (L"hwp-graph-table-key-cross-owner-" +
             std::to_wstring(GetCurrentProcessId()) + L"-" +
             std::to_wstring(serial++));
        std::error_code error;
        std::filesystem::remove_all(root, error);
        CaptureSpool spool;
        std::vector<std::uint8_t> bytes;
        std::vector<DecodedRecord> records;
        std::vector<StrictNode> nodes;
        std::vector<StrictEdge> edges;
        std::vector<StrictProperty> properties;
        std::vector<ReaderPayload> stagedPayloads = payloads;
        RecertifySyntheticLayout(&stagedPayloads);
        const bool built = spool.Build(
                root, stagedPayloads, NormativeEnvironment(), buildArena) &&
            ReadWholeFile(root / L"records.hgn", &bytes) &&
            DecodeRecords(bytes, &records) &&
            ParseStrict(records, &nodes, &edges, &properties);
        const bool seeded = built &&
            hancom::graph::capture::SeedArenaFromRecordStream(
                {bytes.data(), bytes.size()}, &seedArena);
        const std::wstring oldPrefix = L"control:" + std::to_wstring(
            static_cast<std::uint16_t>(NodeKind::Table)) + L":";
        const auto tableKey = [&oldPrefix](
            const std::vector<std::wstring>& keys) {
            const auto found = std::find_if(
                keys.begin(), keys.end(), [&oldPrefix](const auto& key) {
                    return key.rfind(L"table-owner:", 0) == 0 ||
                        key.rfind(L"table-story:", 0) == 0 ||
                        key.rfind(oldPrefix, 0) == 0;
                });
            return found == keys.end() ? std::wstring{} : *found;
        };
        result.buildKey = tableKey(buildArena.AcquiredKeys());
        result.seedKey = tableKey(seedArena.SeededKeys());
        if (seeded) {
            std::map<NodeIdBytes, const StrictNode*> byId;
            for (const StrictNode& node : nodes) byId[node.id] = &node;
            for (const StrictNode& node : nodes) {
                if (node.kind != static_cast<std::uint16_t>(NodeKind::Table)) {
                    continue;
                }
                const DecodedField* const locator = FindField(node.common, 7);
                if (locator == nullptr) break;
                result.locator = locator->value;
                const StrictNode* owner = &node;
                while (owner->parentPresent) {
                    const auto parent = byId.find(owner->parent);
                    if (parent == byId.end()) break;
                    owner = parent->second;
                    if (owner->kind ==
                        static_cast<std::uint16_t>(NodeKind::Story)) {
                        result.owner = owner->id;
                        break;
                    }
                }
                break;
            }
        }
        result.complete = seeded && !result.buildKey.empty() &&
            !result.seedKey.empty() && !result.locator.empty();
        spool.Reset();
        std::filesystem::remove_all(root, error);
        result.complete = result.complete && !error &&
            !std::filesystem::exists(root);
        return result;
    };

    const Capture first = capture(0xa1);
    const Capture second = capture(0xb2);
    const bool distinctOwners = first.complete && second.complete &&
        first.owner != second.owner;
    const bool sameNativeLocator = first.complete && second.complete &&
        first.locator == second.locator;
    const bool buildSeedExact = first.buildKey == first.seedKey &&
        second.buildKey == second.seedKey;
    const bool ownerScoped = first.buildKey.rfind(L"table-owner:", 0) == 0 &&
        second.buildKey.rfind(L"table-owner:", 0) == 0 &&
        first.buildKey != second.buildKey;
    const bool noControlFallback =
        first.buildKey.rfind(L"control:", 0) != 0 &&
        first.seedKey.rfind(L"control:", 0) != 0 &&
        second.buildKey.rfind(L"control:", 0) != 0 &&
        second.seedKey.rfind(L"control:", 0) != 0;
    std::wcout << L"TABLE_STABLE_KEY_UNIQUE_CROSS_OWNER_RAW first_build="
               << first.buildKey << L" first_seed=" << first.seedKey
               << L" second_build=" << second.buildKey
               << L" second_seed=" << second.seedKey << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_CROSS_OWNER_DISTINCT_OWNERS "
               << distinctOwners << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_CROSS_OWNER_SAME_LOCATOR "
               << sameNativeLocator << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_CROSS_OWNER_BUILD_SEED_EXACT "
               << buildSeedExact << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_CROSS_OWNER_OWNER_SCOPED "
               << ownerScoped << L'\n'
               << L"TABLE_STABLE_KEY_NO_TABLE_CONTROL_PREFIX "
               << noControlFallback << L'\n';
    return distinctOwners && sameNativeLocator && buildSeedExact &&
        ownerScoped && noControlFallback;
}

bool DuplicateUnavailableTableStableKeySmoke(
    const std::vector<hancom::graph::capture::ControlObservation>& firstControls,
    const ReaderPayload& firstAcquired,
    const std::vector<hancom::graph::capture::ControlObservation>& secondControls,
    const ReaderPayload& secondAcquired) {
    using hancom::graph::NodeKind;
    using hancom::graph::ObservationState;
    if (firstControls.size() != 2 || secondControls.size() != 2 ||
        firstAcquired.tables.size() != 2 || secondAcquired.tables.size() != 2) {
        return false;
    }
    std::vector<ReaderPayload> payloads = SealedPayloads();
    for (const size_t reader : {size_t{0}, size_t{3}}) {
        auto& observed = payloads[reader].controls;
        observed.erase(
            std::remove_if(observed.begin(), observed.end(), [](const auto& item) {
                return item.ctrlId == L"tbl";
            }),
            observed.end());
        observed.insert(
            observed.end(), firstControls.begin(), firstControls.end());
    }
    payloads[4] = firstAcquired;
    const auto oldCellRun = [](const std::wstring& identity) {
        const size_t separator = identity.find(L':');
        if (separator == std::wstring::npos) return false;
        try {
            return std::stoll(identity.substr(0, separator)) >= 100;
        } catch (...) {
            return false;
        }
    };
    auto& effective = payloads[2];
    effective.properties.erase(
        std::remove_if(
            effective.properties.begin(), effective.properties.end(),
            [&oldCellRun](const auto& property) {
                return property.target ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    oldCellRun(property.targetIdentity);
            }),
        effective.properties.end());
    effective.definitionReferences.erase(
        std::remove_if(
            effective.definitionReferences.begin(),
            effective.definitionReferences.end(),
            [&oldCellRun](const auto& reference) {
                return reference.source ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    oldCellRun(reference.sourceIdentity);
            }),
        effective.definitionReferences.end());
    effective.coverageFacts.erase(
        std::remove_if(
            effective.coverageFacts.begin(), effective.coverageFacts.end(),
            [&oldCellRun](const auto& coverage) {
                return coverage.target ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    oldCellRun(coverage.targetIdentity);
            }),
        effective.coverageFacts.end());
    auto& layout = payloads[6].layoutProperties;
    layout.erase(
        std::remove_if(layout.begin(), layout.end(), [](const auto& property) {
            return property.target ==
                    hancom::graph::capture::PropertyTarget::Table ||
                property.target ==
                    hancom::graph::capture::PropertyTarget::Cell;
        }),
        layout.end());
    RecertifySyntheticLayout(&payloads);
    std::vector<ReaderPayload> secondPayloads = payloads;
    for (const size_t reader : {size_t{0}, size_t{3}}) {
        auto& observed = secondPayloads[reader].controls;
        observed.erase(
            std::remove_if(observed.begin(), observed.end(), [](const auto& item) {
                return item.ctrlId == L"tbl";
            }),
            observed.end());
        observed.insert(
            observed.end(), secondControls.begin(), secondControls.end());
    }
    secondPayloads[4] = secondAcquired;
    RecertifySyntheticLayout(&secondPayloads);

    const std::filesystem::path root =
        std::filesystem::temp_directory_path() /
        (L"hwp-graph-table-key-duplicate-unavailable-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code cleanupError;
    std::filesystem::remove_all(root, cleanupError);
    CaptureSpool first;
    CaptureSpool second;
    hancom::graph::capture::CaptureIdentityArena firstArena;
    hancom::graph::capture::CaptureIdentityArena secondArena;
    std::vector<std::uint8_t> firstBytes;
    std::vector<std::uint8_t> secondBytes;
    std::vector<DecodedRecord> firstRecords;
    std::vector<DecodedRecord> secondRecords;
    const bool firstBuilt = first.Build(
            root / L"first", payloads, NormativeEnvironment(), firstArena) &&
        ReadWholeFile(root / L"first" / L"records.hgn", &firstBytes) &&
        DecodeRecords(firstBytes, &firstRecords);
    const bool seeded = firstBuilt &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {firstBytes.data(), firstBytes.size()}, &secondArena);
    const bool secondBuilt = seeded && second.Build(
            root / L"second", secondPayloads, NormativeEnvironment(),
            secondArena) &&
        ReadWholeFile(root / L"second" / L"records.hgn", &secondBytes) &&
        DecodeRecords(secondBytes, &secondRecords);

    struct TableView final {
        std::map<std::uint64_t, NodeIdBytes> ids{};
        std::set<NodeIdBytes> owners{};
        bool unavailable = true;
    };
    const auto inspect = [](const std::vector<DecodedRecord>& records) {
        TableView view;
        std::vector<StrictNode> nodes;
        std::vector<StrictEdge> edges;
        std::vector<StrictProperty> properties;
        if (!ParseStrict(records, &nodes, &edges, &properties)) {
            view.unavailable = false;
            return view;
        }
        std::map<NodeIdBytes, const StrictNode*> byId;
        for (const StrictNode& node : nodes) byId[node.id] = &node;
        for (const StrictNode& node : nodes) {
            if (node.kind != static_cast<std::uint16_t>(NodeKind::Table)) {
                continue;
            }
            std::uint64_t ordinal = 0;
            const DecodedField* const instance = FindField(node.payload, 101);
            if (!PayloadUint64(node, 105, &ordinal) || instance == nullptr ||
                instance->value.empty() || instance->value[0] !=
                    static_cast<std::uint8_t>(ObservationState::NotExposed)) {
                view.unavailable = false;
                continue;
            }
            view.ids[ordinal] = node.id;
            const StrictNode* owner = &node;
            while (owner->parentPresent) {
                const auto parent = byId.find(owner->parent);
                if (parent == byId.end()) {
                    view.unavailable = false;
                    break;
                }
                owner = parent->second;
                if (owner->kind == static_cast<std::uint16_t>(NodeKind::Story)) {
                    view.owners.insert(owner->id);
                    break;
                }
            }
        }
        return view;
    };
    const TableView before = inspect(firstRecords);
    const TableView after = inspect(secondRecords);
    const bool buildSeedExact = secondBuilt && before.unavailable &&
        after.unavailable && before.ids.size() == 2 &&
        before.ids == after.ids;
    std::set<NodeIdBytes> tableIds;
    for (const auto& item : before.ids) tableIds.insert(item.second);
    const bool ownerScopedNoCollision = buildSeedExact &&
        tableIds.size() == 2 && before.owners.size() == 1 &&
        after.owners == before.owners;
    const bool receiptExact = buildSeedExact &&
        secondArena.Receipt().remaps.empty() &&
        secondArena.Receipt().tombstones.empty();
    std::wcout << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_DETAIL first="
               << firstBuilt << L" seeded=" << seeded
               << L" second=" << secondBuilt
               << L" before_ids=" << before.ids.size()
               << L" after_ids=" << after.ids.size()
               << L" before_owners=" << before.owners.size()
               << L" after_owners=" << after.owners.size()
               << L" remaps=" << secondArena.Receipt().remaps.size()
               << L" tombstones=" << secondArena.Receipt().tombstones.size()
               << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_CONTRACT 1\n"
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_BUILD_SEED_EXACT "
               << buildSeedExact << L'\n'
               << L"TABLE_STABLE_KEY_OWNER_STORY_SCOPE_NO_COLLISION "
               << ownerScopedNoCollision << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_RECEIPT_EXACT "
               << receiptExact << L'\n';
    first.Reset();
    second.Reset();
    std::filesystem::remove_all(root, cleanupError);
    return buildSeedExact && ownerScopedNoCollision && receiptExact &&
        !cleanupError && !std::filesystem::exists(root);
}

bool DuplicateEmptyRealTableReaderRecaptureOracle(
    const std::vector<hancom::graph::capture::ControlObservation>& firstControls,
    const ReaderPayload& firstAcquired,
    const std::vector<hancom::graph::capture::ControlObservation>& secondControls,
    const ReaderPayload& secondAcquired,
    const bool requireByteParity) {
    using hancom::graph::EdgeKind;
    using hancom::graph::NodeKind;
    using hancom::graph::ObservationState;
    if (firstControls.size() != 2 || secondControls.size() != 2 ||
        firstAcquired.tables.size() != 2 || secondAcquired.tables.size() != 2 ||
        firstAcquired.reader !=
            hancom::graph::capture::QualifiedReader::TableTopology ||
        secondAcquired.reader !=
            hancom::graph::capture::QualifiedReader::TableTopology ||
        firstAcquired.outcome !=
            hancom::graph::capture::ReaderOutcome::Complete ||
        secondAcquired.outcome !=
            hancom::graph::capture::ReaderOutcome::Complete) {
        return false;
    }
    const bool firstNativeOrder =
        firstControls[0].headCtrlOrdinal == 3 &&
        firstControls[1].headCtrlOrdinal == 7 &&
        firstAcquired.tables[0].headCtrlOrdinal == 3 &&
        firstAcquired.tables[1].headCtrlOrdinal == 7;
    const bool secondReversedNativeOrder = requireByteParity
        ? secondControls[0].headCtrlOrdinal == 3 &&
            secondControls[1].headCtrlOrdinal == 7 &&
            secondAcquired.tables[0].headCtrlOrdinal == 3 &&
            secondAcquired.tables[1].headCtrlOrdinal == 7
        : secondControls[0].headCtrlOrdinal == 7 &&
            secondControls[1].headCtrlOrdinal == 3 &&
            secondAcquired.tables[0].headCtrlOrdinal == 7 &&
            secondAcquired.tables[1].headCtrlOrdinal == 3;
    const auto duplicateEmptyPair = [](const ReaderPayload& payload) {
        return std::all_of(
            payload.tables.begin(), payload.tables.end(), [](const auto& table) {
                return table.instanceIdPresent && table.instanceId.empty() &&
                    !table.cells.empty();
            });
    };
    const bool duplicateEmpty =
        duplicateEmptyPair(firstAcquired) && duplicateEmptyPair(secondAcquired);
    const bool distinctLocators =
        firstAcquired.tables[0].headCtrlOrdinal !=
            firstAcquired.tables[1].headCtrlOrdinal &&
        secondAcquired.tables[0].headCtrlOrdinal !=
            secondAcquired.tables[1].headCtrlOrdinal &&
        (requireByteParity
            ? firstAcquired.tables[0].anchor.character ==
                    secondAcquired.tables[0].anchor.character &&
                firstAcquired.tables[1].anchor.character ==
                    secondAcquired.tables[1].anchor.character
            : firstAcquired.tables[0].anchor.character ==
                    secondAcquired.tables[1].anchor.character &&
                firstAcquired.tables[1].anchor.character ==
                    secondAcquired.tables[0].anchor.character);
    for (const auto& table : firstAcquired.tables) {
        const size_t invalidCells = static_cast<size_t>(std::count_if(
            table.cells.begin(), table.cells.end(), [](const auto& cell) {
                return cell.address.empty() || cell.listId <= 0 ||
                    cell.row1 == 0 || cell.column1 == 0 ||
                    cell.rowSpan == 0 || cell.columnSpan == 0;
            }));
        std::wcout << L"REAL_TABLE_READER_NATIVE_TABLE_DETAIL ordinal="
                   << table.headCtrlOrdinal << L" rows=" << table.rowCount
                   << L" columns=" << table.columnCount
                   << L" cells=" << table.cells.size()
                   << L" invalid_cells=" << invalidCells;
        if (!table.cells.empty()) {
            const auto& cell = table.cells.front();
            std::wcout << L" first=" << cell.address << L',' << cell.listId
                       << L',' << cell.row1 << L',' << cell.column1 << L','
                       << cell.rowSpan << L',' << cell.columnSpan;
        }
        std::wcout << L'\n';
    }

    std::vector<ReaderPayload> firstPayloads = SealedPayloads();
    const auto applyAcquisition = [](
        std::vector<ReaderPayload>* const payloads,
        const std::vector<hancom::graph::capture::ControlObservation>& controls,
        const ReaderPayload& acquired) {
        for (const size_t reader : {size_t{0}, size_t{3}}) {
            auto& observed = (*payloads)[reader].controls;
            observed.erase(
                std::remove_if(
                    observed.begin(), observed.end(), [](const auto& item) {
                        return item.ctrlId == L"tbl";
                    }),
                observed.end());
            observed.insert(observed.end(), controls.begin(), controls.end());
        }
        (*payloads)[4] = acquired;
    };
    applyAcquisition(&firstPayloads, firstControls, firstAcquired);
    const auto oldCellRun = [](const std::wstring& identity) {
        const size_t separator = identity.find(L':');
        if (separator == std::wstring::npos) return false;
        try {
            return std::stoll(identity.substr(0, separator)) >= 100;
        } catch (...) {
            return false;
        }
    };
    auto& effective = firstPayloads[2];
    effective.properties.erase(
        std::remove_if(
            effective.properties.begin(), effective.properties.end(),
            [&oldCellRun](const auto& property) {
                return property.target ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    oldCellRun(property.targetIdentity);
            }),
        effective.properties.end());
    effective.definitionReferences.erase(
        std::remove_if(
            effective.definitionReferences.begin(),
            effective.definitionReferences.end(),
            [&oldCellRun](const auto& reference) {
                return reference.source ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    oldCellRun(reference.sourceIdentity);
            }),
        effective.definitionReferences.end());
    effective.coverageFacts.erase(
        std::remove_if(
            effective.coverageFacts.begin(), effective.coverageFacts.end(),
            [&oldCellRun](const auto& coverage) {
                return coverage.target ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    oldCellRun(coverage.targetIdentity);
            }),
        effective.coverageFacts.end());
    auto& layout = firstPayloads[6].layoutProperties;
    layout.erase(
        std::remove_if(layout.begin(), layout.end(), [](const auto& property) {
            return property.target ==
                    hancom::graph::capture::PropertyTarget::Table ||
                property.target ==
                    hancom::graph::capture::PropertyTarget::Cell;
        }),
        layout.end());
    RecertifySyntheticLayout(&firstPayloads);
    std::vector<ReaderPayload> secondPayloads = firstPayloads;
    applyAcquisition(&secondPayloads, secondControls, secondAcquired);
    RecertifySyntheticLayout(&secondPayloads);

    const std::filesystem::path root =
        std::filesystem::temp_directory_path() /
        (L"hwp-graph-real-reader-duplicate-empty-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code cleanupError;
    std::filesystem::remove_all(root, cleanupError);
    CaptureSpool first;
    CaptureSpool second;
    hancom::graph::capture::CaptureIdentityArena firstArena;
    hancom::graph::capture::CaptureIdentityArena secondArena;
    std::vector<std::uint8_t> firstBytes;
    std::vector<std::uint8_t> secondBytes;
    std::vector<DecodedRecord> firstRecords;
    std::vector<DecodedRecord> secondRecords;
    const bool firstBuilt = firstNativeOrder && secondReversedNativeOrder &&
        duplicateEmpty && distinctLocators &&
        first.Build(root / L"first", firstPayloads, NormativeEnvironment(),
                    firstArena);
    const bool firstRead = firstBuilt &&
        ReadWholeFile(root / L"first" / L"records.hgn", &firstBytes);
    const bool firstDecoded = firstRead &&
        DecodeRecords(firstBytes, &firstRecords);
    const bool seeded = firstDecoded &&
        hancom::graph::capture::SeedArenaFromRecordStream(
            {firstBytes.data(), firstBytes.size()}, &secondArena);
    const bool secondBuilt = seeded &&
        second.Build(root / L"second", secondPayloads, NormativeEnvironment(),
                     secondArena);
    const bool secondRead = secondBuilt &&
        ReadWholeFile(root / L"second" / L"records.hgn", &secondBytes);
    const bool secondDecoded = secondRead &&
        DecodeRecords(secondBytes, &secondRecords);
    CaptureSpool parityBaseline;
    hancom::graph::capture::CaptureIdentityArena parityBaselineArena;
    std::vector<std::uint8_t> parityBaselineBytes;
    const bool parityBaselineSeeded = !requireByteParity ||
        (firstDecoded && hancom::graph::capture::SeedArenaFromRecordStream(
            {firstBytes.data(), firstBytes.size()}, &parityBaselineArena));
    const bool parityBaselineBuilt = !requireByteParity ||
        (parityBaselineSeeded && parityBaseline.Build(
            root / L"parity-baseline", firstPayloads,
            NormativeEnvironment(), parityBaselineArena));
    const bool parityBaselineRead = !requireByteParity ||
        (parityBaselineBuilt && ReadWholeFile(
            root / L"parity-baseline" / L"records.hgn",
            &parityBaselineBytes));
    const bool built = firstDecoded && secondDecoded && parityBaselineRead;
    const AttemptResult& parityFirst = requireByteParity
        ? parityBaseline.Result() : first.Result();
    const std::vector<std::uint8_t>& parityFirstBytes = requireByteParity
        ? parityBaselineBytes : firstBytes;
    const bool recordsByteParity = built && parityFirstBytes == secondBytes;
    const std::uint64_t byteParityMismatch = built
        ? AttemptMismatchBits(parityFirst, second.Result())
        : UINT64_MAX;
    const bool finalDigestParity = built && hancom::graph::codec::Equal(
        parityFirst.facts.finalRecords.digest,
        second.Result().facts.finalRecords.digest);
    const bool indexDigestParity = built && hancom::graph::codec::Equal(
        parityFirst.facts.index.digest,
        second.Result().facts.index.digest);
    const bool blobDigestParity = built && hancom::graph::codec::Equal(
        parityFirst.facts.blobClosure.digest,
        second.Result().facts.blobClosure.digest);
    const bool inventoryParity = built &&
        parityFirst.nodeFingerprints.size() ==
            second.Result().nodeFingerprints.size();
    const bool byteParity = recordsByteParity && byteParityMismatch == 0 &&
        finalDigestParity && indexDigestParity && blobDigestParity &&
        inventoryParity;

    struct View final {
        std::vector<std::uint64_t> order{};
        std::map<std::uint64_t, NodeIdBytes> ids{};
        bool fields = true;
        bool ownership = true;
    };
    const auto inspect = [](const std::vector<DecodedRecord>& records) {
        View view;
        std::vector<StrictNode> nodes;
        std::vector<StrictEdge> edges;
        std::vector<StrictProperty> properties;
        if (!ParseStrict(records, &nodes, &edges, &properties)) {
            view.fields = false;
            view.ownership = false;
            return view;
        }
        for (const StrictNode& node : nodes) {
            if (node.kind != static_cast<std::uint16_t>(NodeKind::Table)) {
                continue;
            }
            std::uint64_t ordinal = 0;
            const DecodedField* instance = FindField(node.payload, 101);
            if (!PayloadUint64(node, 105, &ordinal) ||
                (ordinal != 3 && ordinal != 7)) {
                continue;
            }
            view.order.push_back(ordinal);
            view.fields = view.fields && instance != nullptr &&
                !instance->value.empty() &&
                instance->value[0] == static_cast<std::uint8_t>(
                    ObservationState::Value) &&
                PayloadObservedText(node, 101).empty() &&
                node.parentPresent && view.ids.emplace(ordinal, node.id).second;
            std::uint64_t anchors = 0;
            std::uint64_t ownedCells = 0;
            for (const StrictEdge& edge : edges) {
                if (edge.kind == static_cast<std::uint16_t>(EdgeKind::Anchors) &&
                    edge.source == node.id) {
                    ++anchors;
                    view.ownership = view.ownership &&
                        edge.target == node.parent;
                }
            }
            for (const StrictNode& child : nodes) {
                if (child.kind == static_cast<std::uint16_t>(
                        NodeKind::TableCell) &&
                    child.parentPresent && child.parent == node.id) {
                    ++ownedCells;
                }
            }
            view.ownership = view.ownership && anchors == 1 && ownedCells != 0;
        }
        return view;
    };
    const View before = inspect(firstRecords);
    const View after = inspect(secondRecords);
    std::set<NodeIdBytes> beforeIds;
    std::set<NodeIdBytes> afterIds;
    for (const auto& item : before.ids) beforeIds.insert(item.second);
    for (const auto& item : after.ids) afterIds.insert(item.second);
    std::vector<NodeIdBytes> overlap;
    std::set_intersection(
        beforeIds.begin(), beforeIds.end(), afterIds.begin(), afterIds.end(),
        std::back_inserter(overlap));
    const bool decodedTables = built && before.fields && after.fields &&
        before.ids.size() == 2 && after.ids.size() == 2 &&
        beforeIds.size() == 2 && afterIds.size() == 2;
    const bool locator3Preserved = decodedTables &&
        before.ids.at(3) == after.ids.at(3);
    const bool locator7Preserved = decodedTables &&
        before.ids.at(7) == after.ids.at(7);
    const bool negativeCollapse = decodedTables && overlap.size() == 2 &&
        before.ids.at(3) != before.ids.at(7) &&
        after.ids.at(3) != after.ids.at(7);
    const bool sequenceSwapResistance = decodedTables &&
        firstAcquired.tables[0].headCtrlOrdinal == 3 &&
        secondAcquired.tables[0].headCtrlOrdinal == 7 &&
        before.ids.at(firstAcquired.tables[0].headCtrlOrdinal) !=
            after.ids.at(secondAcquired.tables[0].headCtrlOrdinal) &&
        before.ids.at(firstAcquired.tables[1].headCtrlOrdinal) !=
            after.ids.at(secondAcquired.tables[1].headCtrlOrdinal) &&
        locator3Preserved && locator7Preserved;
    const bool negativeCrossObjectLeakage = decodedTables &&
        before.ownership && after.ownership && sequenceSwapResistance;
    const bool tableReceiptExact = decodedTables &&
        secondArena.Receipt().remaps.empty() &&
        secondArena.Receipt().tombstones.empty() &&
        secondArena.AmbiguousRemapCount() == 0 &&
        secondArena.TombstoneCount() == 0;
    const bool recaptureMapping = negativeCollapse && locator3Preserved &&
        locator7Preserved && sequenceSwapResistance &&
        negativeCrossObjectLeakage && tableReceiptExact;
    std::wcout << L"REAL_TABLE_READER_STAGE_DETAIL first_build="
               << firstBuilt << L" first_read=" << firstRead
               << L" first_decode=" << firstDecoded
               << L" seed=" << seeded << L" second_build=" << secondBuilt
               << L" second_read=" << secondRead
               << L" second_decode=" << secondDecoded << L'\n'
               << L"REAL_TABLE_READER_NATIVE_ACQUIRED_COUNT "
               << firstAcquired.tables.size() << L'\n'
               << L"REAL_TABLE_READER_FIRST_NATIVE_ORDER "
               << firstNativeOrder << L'\n'
               << L"REAL_TABLE_READER_SECOND_REVERSED_NATIVE_ORDER "
               << secondReversedNativeOrder << L'\n'
               << L"REAL_TABLE_READER_RECORD_INDEX_BLOB_INVENTORY_BYTE_PARITY "
               << (!requireByteParity || byteParity)
               << L" required=" << requireByteParity
               << L" records=" << recordsByteParity
               << L" mismatch=" << byteParityMismatch
               << L" final=" << finalDigestParity
               << L" index=" << indexDigestParity
               << L" blobs=" << blobDigestParity
               << L" inventory=" << inventoryParity << L'\n'
               << L"REAL_TABLE_READER_DUPLICATE_EMPTY_CARDINALITY "
               << (duplicateEmpty ? firstAcquired.tables.size() : 0) << L'\n'
               << L"REAL_TABLE_READER_DISTINCT_LOCATORS "
               << distinctLocators << L'\n'
               << L"REAL_TABLE_READER_DECODED_TABLE_NODES "
               << decodedTables << L'\n'
               << L"REAL_TABLE_READER_DISTINCT_NODE_IDS "
               << (beforeIds.size() == 2 && afterIds.size() == 2) << L'\n'
               << L"REAL_TABLE_READER_LOCATOR3_NODE_ID_PRESERVED "
               << locator3Preserved << L'\n'
               << L"REAL_TABLE_READER_LOCATOR7_NODE_ID_PRESERVED "
               << locator7Preserved << L'\n'
               << L"REAL_TABLE_READER_SEQUENCE_SWAP_RESISTANCE "
               << sequenceSwapResistance << L'\n'
               << L"REAL_TABLE_READER_RECEIPT_EXACT "
               << tableReceiptExact << L'\n'
               << L"REAL_TABLE_READER_FIRST_TO_SECOND_RECAPTURE_MAPPING "
               << recaptureMapping << L'\n'
               << L"REAL_TABLE_READER_NEGATIVE_COLLAPSE "
               << negativeCollapse << L'\n'
               << L"REAL_TABLE_READER_NEGATIVE_CROSS_OBJECT_LEAKAGE "
               << negativeCrossObjectLeakage << L'\n';
    first.Reset();
    second.Reset();
    parityBaseline.Reset();
    std::filesystem::remove_all(root, cleanupError);
    const bool cleanupComplete =
        !cleanupError && !std::filesystem::exists(root);
    return requireByteParity
        ? byteParity && cleanupComplete
        : recaptureMapping && negativeCollapse && locator3Preserved &&
            locator7Preserved && sequenceSwapResistance && tableReceiptExact &&
            negativeCrossObjectLeakage && cleanupComplete;
}

bool ProductionStoreFailureAbortMatrixSmoke() {
    using hancom::graph::store::FailurePoint;
    const std::array failures{
        FailurePoint::OpenOwner,
        FailurePoint::OpenRecords,
        FailurePoint::WriteRecords,
        FailurePoint::FlushRecords,
        FailurePoint::OpenIndex,
        FailurePoint::WriteIndex,
        FailurePoint::FlushIndex,
        FailurePoint::OpenManifest,
        FailurePoint::WriteManifest,
        FailurePoint::SealManifest,
        FailurePoint::Rename,
        FailurePoint::Swap,
    };
    bool allFailedClosed = true;
    for (const FailurePoint failure : failures) {
        BoundaryStoreObservation observation;
        std::vector<DecodedRecord> records;
        const CaptureStatus status = RunBoundaryScenario(
            BoundaryScenario::Complete, L"store-failure", &records,
            nullptr, nullptr, &observation, failure);
        allFailedClosed = allFailedClosed &&
            status == CaptureStatus::PublishFailed && records.empty() &&
            observation.generation == 0 && observation.publishCount == 0 &&
            observation.sessionAborts == 1 &&
            observation.sessionCommits == 0 && observation.sessionClosed;
    }
    BoundaryStoreObservation retry;
    std::vector<DecodedRecord> records;
    const CaptureStatus retryStatus = RunBoundaryScenario(
        BoundaryScenario::Complete, L"fresh-retry", &records,
        nullptr, nullptr, &retry);
    const bool freshRetry = retryStatus == CaptureStatus::Complete &&
        retry.generation == 1 && retry.publishCount == 1 &&
        retry.sessionAborts == 0 && retry.sessionCommits == 1 &&
        retry.sessionClosed && !records.empty();
    std::wcout << L"GRAPH_CAPTURE_PRODUCTION_STORE_FAILURE_ABORT_MATRIX "
               << allFailedClosed << L" phases=" << failures.size() << L'\n'
               << L"GRAPH_CAPTURE_PRODUCTION_STORE_FRESH_RETRY "
               << freshRetry << L'\n';
    return allFailedClosed && freshRetry;
}

bool UnavailableBodyListFailsClosedSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-body-list-unavailable-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    const std::array<hancom::graph::ObservationState, 3> states{
        hancom::graph::ObservationState::NotExposed,
        hancom::graph::ObservationState::ReadFailed,
        hancom::graph::ObservationState::NotRequested,
    };
    bool rejected = true;
    for (size_t index = 0; index < states.size(); ++index) {
        std::vector<ReaderPayload> payloads = SealedPayloads();
        payloads[0].bodyList = {
            states[index], false,
            states[index] == hancom::graph::ObservationState::ReadFailed
                ? static_cast<std::int32_t>(E_FAIL)
                : 0,
            {}, 0};
        CaptureSpool spool;
        hancom::graph::capture::CaptureIdentityArena arena;
        const std::filesystem::path attempt = root / std::to_wstring(index);
        rejected = rejected &&
            !spool.Build(attempt, payloads, NormativeEnvironment(), arena) &&
            !std::filesystem::exists(attempt / L"records.hgn");
        spool.Reset();
    }
    std::filesystem::remove_all(root, error);
    return rejected && !std::filesystem::exists(root);
}

bool CellStoryListStateFailsClosedSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-cell-list-state-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> payloads = SealedPayloads();
    if (payloads[4].tables.empty() || payloads[4].tables[0].cells.empty()) {
        return false;
    }
    payloads[4].tables[0].cells[0].listState =
        hancom::graph::ObservationState::NotExposed;
    CaptureSpool spool;
    hancom::graph::capture::CaptureIdentityArena arena;
    const bool rejected = !spool.Build(
        root, payloads, NormativeEnvironment(), arena);
    const bool unpublished = !std::filesystem::exists(root / L"records.hgn");
    spool.Reset();
    std::filesystem::remove_all(root, error);
    return rejected && unpublished && !std::filesystem::exists(root);
}

bool UnavailableCaptionListPublishesTerminalSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-caption-list-origin-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> payloads = SealedPayloads();
    if (payloads[5].images.empty()) {
        return false;
    }
    // Payload-level serialization test only: this deliberately carries no
    // native-location provenance and may assert only the terminal path.
    auto& caption = payloads[5].images.front().text[2];
    caption.state = hancom::graph::ObservationState::Value;
    caption.value = L"native child-list required";
    payloads[5].images.front().captionList = {
        hancom::graph::ObservationState::NotExposed, 0};
    CaptureSpool spool;
    hancom::graph::capture::CaptureIdentityArena arena;
    const bool built = spool.Build(
        root, payloads, NormativeEnvironment(), arena);
    std::vector<std::uint8_t> bytes;
    std::vector<DecodedRecord> records;
    std::vector<StrictNode> nodes;
    std::vector<StrictEdge> edges;
    std::vector<StrictProperty> properties;
    bool terminal = false;
    NodeIdBytes terminalStory{};
    if (built && ReadWholeFile(root / L"records.hgn", &bytes) &&
        DecodeRecords(bytes, &records) &&
        ParseStrict(records, &nodes, &edges, &properties)) {
        for (const StrictNode& node : nodes) {
            const StrictNode* const parent = node.parentPresent
                ? FindNode(nodes, node.parent)
                : nullptr;
            const DecodedField* const kind = FindField(node.payload, 100);
            const DecodedField* const nativeList =
                FindField(node.payload, 102);
            const DecodedField* const locator = FindField(node.common, 7);
            if (node.kind == static_cast<std::uint16_t>(
                    hancom::graph::NodeKind::Story) &&
                parent != nullptr &&
                parent->kind == static_cast<std::uint16_t>(
                    hancom::graph::NodeKind::Image) &&
                kind != nullptr && kind->value.size() == 2 &&
                R16(kind->value.data()) == static_cast<std::uint16_t>(
                    hancom::graph::StoryKind::Caption) &&
                nativeList != nullptr && !nativeList->value.empty() &&
                nativeList->value[0] == static_cast<std::uint8_t>(
                    hancom::graph::ObservationState::NotExposed) &&
                locator != nullptr && !locator->value.empty() &&
                locator->value[0] == static_cast<std::uint8_t>(
                    hancom::graph::ObservationState::NotExposed)) {
                terminal = true;
                terminalStory = node.id;
            }
        }
    }
    const bool noSyntheticDescendants = terminal && std::none_of(
        nodes.begin(), nodes.end(),
        [&terminalStory](const StrictNode& node) {
            return node.parentPresent && node.parent == terminalStory;
        });
    std::wcout << L"GRAPH_CAPTURE_CAPTION_NATIVE_LIST_TERMINAL_DETAIL built="
               << built << L" decoded=" << !records.empty()
               << L" nodes=" << nodes.size() << L" terminal=" << terminal
               << L" no_synthetic_descendants=" << noSyntheticDescendants
               << L'\n';
    spool.Reset();
    std::filesystem::remove_all(root, error);
    return built && terminal && noSyntheticDescendants &&
        !std::filesystem::exists(root);
}

bool PayloadOnlyCaptionLocationFailsClosedSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-payload-only-caption-location-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> payloads = SealedPayloads();
    if (payloads[5].images.empty()) {
        return false;
    }
    auto& image = payloads[5].images.front();
    image.text[2].state = hancom::graph::ObservationState::Value;
    image.text[2].value = L"payload-only synthetic caption";
    image.captionList = {hancom::graph::ObservationState::Value, 937};
    image.captionStart = {937, 41, 0};
    CaptureSpool spool;
    hancom::graph::capture::CaptureIdentityArena arena;
    const bool rejected = !spool.Build(
        root, payloads, NormativeEnvironment(), arena);
    const bool unpublished = !std::filesystem::exists(root / L"records.hgn");
    spool.Reset();
    std::filesystem::remove_all(root, error);
    return rejected && unpublished && !std::filesystem::exists(root);
}

bool ManuallyForgedCaptionQualificationFailsClosedSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-owner-scoped-story-list-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> payloads = SealedPayloads();
    if (payloads[4].tables.empty() || payloads[4].tables[0].cells.empty() ||
        payloads[5].images.empty()) {
        return false;
    }
    auto& cell = payloads[4].tables[0].cells[0];
    cell.listId = 2;
    cell.paragraphs[0].start.list = 2;
    cell.paragraphs[0].runs[0].start.list = 2;
    cell.paragraphs[0].runs[0].end.list = 2;
    auto& image = payloads[5].images.front();
    image.text[2].state = hancom::graph::ObservationState::Value;
    image.text[2].value = L"caption list two";
    image.captionList = {hancom::graph::ObservationState::Value, 2};
    image.captionStart = {2, 0, 0};
    // Adversarial payload construction must never be able to mint native
    // owner/session qualification for these otherwise internally consistent
    // locator fields.
    CaptureSpool spool;
    hancom::graph::capture::CaptureIdentityArena arena;
    const bool rejected = !spool.Build(
        root, payloads, NormativeEnvironment(), arena);
    const bool unpublished = !std::filesystem::exists(root / L"records.hgn");
    spool.Reset();
    std::filesystem::remove_all(root, error);
    return rejected && unpublished && !std::filesystem::exists(root);
}

bool CompositePropertyTargetIdentitySmoke() {
    using hancom::graph::ObservationState;
    using hancom::graph::PropertyOrigin;
    using hancom::graph::ScalarTag;
    using hancom::graph::capture::CanonicalNativeSiteIdentity;
    using hancom::graph::capture::ControlObservation;
    using hancom::graph::capture::PropertyObservation;
    using hancom::graph::capture::PropertyTarget;

    const auto property = [](const PropertyTarget target,
                             std::wstring identity,
                             const hancom::graph::PropertyKeyId key) {
        PropertyObservation result;
        result.target = target;
        result.targetIdentity = std::move(identity);
        result.ownerField = 10;
        result.key = key;
        result.scalar = ScalarTag::Uint64;
        result.state = ObservationState::Value;
        result.origin = PropertyOrigin::Generated;
        result.integerValue = 17;
        return result;
    };
    size_t serial = 0;
    const auto builds = [&serial](std::vector<ReaderPayload> payloads) {
        const std::filesystem::path root =
            std::filesystem::temp_directory_path() /
            (L"hwp-graph-composite-target-" +
             std::to_wstring(GetCurrentProcessId()) + L"-" +
             std::to_wstring(serial++));
        std::error_code error;
        std::filesystem::remove_all(root, error);
        CaptureSpool spool;
        hancom::graph::capture::CaptureIdentityArena arena;
        const bool built = spool.Build(
            root, payloads, NormativeEnvironment(), arena);
        spool.Reset();
        std::filesystem::remove_all(root, error);
        return built && !error && !std::filesystem::exists(root);
    };

    std::vector<ReaderPayload> exact = SealedPayloads();
    const auto& control = exact[3].controls[1];
    // SealedPayloads already carries the complete exact owner matrix.
    const std::wstring controlExact = CanonicalNativeSiteIdentity(
        PropertyTarget::Control, control.ctrlId,
        control.headCtrlOrdinal, control.anchor,
        control.instanceIdPresent, control.instanceId);
    const std::wstring tableInstance = exact[4].tables[0].instanceId;
    const bool exactOwners = builds(std::move(exact));

    const auto rejected = [&builds, &property](
        const std::wstring& identity,
        const PropertyTarget target,
        const bool duplicateFull,
        const bool absentWithValue) {
        std::vector<ReaderPayload> payloads = SealedPayloads();
        if (duplicateFull) {
            payloads[0].controls.push_back(payloads[0].controls[1]);
            payloads[3].controls.push_back(payloads[3].controls[1]);
        }
        if (absentWithValue) {
            ControlObservation malformed{
                L"form", L"meaningful-while-absent", 77,
                {0, 0, 9}, false};
            malformed.instanceIdPresent = false;
            payloads[0].controls.push_back(malformed);
            payloads[3].controls.push_back(malformed);
        }
        payloads[6].layoutProperties.push_back(
            property(target, identity, 12002));
        return !builds(std::move(payloads));
    };
    const bool duplicateComposite = rejected(
        controlExact, PropertyTarget::Control, true, false);
    const bool missing = rejected(
        L"missing-composite-target", PropertyTarget::Control, false, false);
    const bool crossFamily = rejected(
        tableInstance, PropertyTarget::Image, false, false);
    const bool absentMeaningful = rejected(
        L"meaningful-while-absent", PropertyTarget::Control, false, true);
    std::vector<ReaderPayload> ambiguousPayloads = SealedPayloads();
    ControlObservation ambiguous = ambiguousPayloads[0].controls[1];
    ambiguous.headCtrlOrdinal = 6;
    ambiguous.anchor.character = 9;
    ambiguousPayloads[0].controls.push_back(ambiguous);
    ambiguousPayloads[3].controls.push_back(ambiguous);
    ambiguousPayloads[6].layoutProperties.push_back(property(
        PropertyTarget::Control, ambiguous.instanceId, 12002));
    const bool ambiguousLegacy = !builds(std::move(ambiguousPayloads));
    std::vector<ReaderPayload> emptyPayloads = SealedPayloads();
    ControlObservation presentEmpty{
        L"form", L"", 77, {0, 0, 9}, false};
    emptyPayloads[0].controls.push_back(presentEmpty);
    emptyPayloads[3].controls.push_back(presentEmpty);
    emptyPayloads[6].layoutProperties.push_back(property(
        PropertyTarget::Control, L"", 12002));
    const bool presentEmptyLegacy = !builds(std::move(emptyPayloads));

    std::wcout << L"GRAPH_CAPTURE_COMPOSITE_TARGET_EXACT_OWNERS "
               << exactOwners << L'\n'
               << L"GRAPH_CAPTURE_COMPOSITE_TARGET_DUPLICATE_REJECTED "
               << duplicateComposite << L'\n'
               << L"GRAPH_CAPTURE_COMPOSITE_TARGET_MISSING_REJECTED "
               << missing << L'\n'
               << L"GRAPH_CAPTURE_COMPOSITE_TARGET_CROSS_FAMILY_REJECTED "
               << crossFamily << L'\n'
               << L"GRAPH_CAPTURE_COMPOSITE_TARGET_ABSENT_RAW_REJECTED "
               << absentMeaningful << L'\n'
               << L"GRAPH_CAPTURE_COMPOSITE_TARGET_AMBIGUOUS_LEGACY_REJECTED "
               << ambiguousLegacy << L'\n'
               << L"GRAPH_CAPTURE_COMPOSITE_TARGET_EMPTY_LEGACY_REJECTED "
               << presentEmptyLegacy << L'\n';
    return exactOwners && duplicateComposite && missing && crossFamily &&
        absentMeaningful && ambiguousLegacy && presentEmptyLegacy;
}

bool PerKindLayoutGeometryContractSmoke() {
    using hancom::graph::ObservationState;
    using hancom::graph::ScalarTag;
    using hancom::graph::capture::PropertyTarget;
    size_t serial = 0;
    const auto builds = [&serial](std::vector<ReaderPayload> payloads) {
        const std::filesystem::path root =
            std::filesystem::temp_directory_path() /
            (L"hwp-graph-per-kind-layout-" +
             std::to_wstring(GetCurrentProcessId()) + L"-" +
             std::to_wstring(serial++));
        std::error_code error;
        std::filesystem::remove_all(root, error);
        CaptureSpool spool;
        hancom::graph::capture::CaptureIdentityArena arena;
        const bool built = spool.Build(
            root, payloads, NormativeEnvironment(), arena);
        spool.Reset();
        std::filesystem::remove_all(root, error);
        return built && !error && !std::filesystem::exists(root);
    };
    const auto mutate = [&builds](const auto& mutation) {
        std::vector<ReaderPayload> payloads = SealedPayloads();
        mutation(payloads[6].layoutProperties);
        return !builds(std::move(payloads));
    };
    const bool complete = builds(SealedPayloads());
    BoundaryStoreObservation incompleteStore;
    std::vector<DecodedRecord> incompleteRecords;
    const CaptureStatus incompleteStatus = RunBoundaryScenario(
        BoundaryScenario::FalseLayoutCertification,
        L"layout-certification-false", &incompleteRecords,
        nullptr, nullptr, &incompleteStore);
    const bool incompleteUnpublished =
        incompleteStatus == CaptureStatus::IncompleteCapture &&
        static_cast<std::uint8_t>(incompleteStatus) == 2 &&
        incompleteRecords.empty() && incompleteStore.generation == 0 &&
        incompleteStore.publishCount == 0 && incompleteStore.nodeCount == 0 &&
        incompleteStore.anchorsCount == 0;
    std::vector<ReaderPayload> falseCertification = SealedPayloads();
    falseCertification[6].layoutPerKindComplete = false;
    const bool falseCertificationRejected =
        !builds(std::move(falseCertification));
    std::vector<ReaderPayload> defaultCertification = SealedPayloads();
    defaultCertification[6].layoutPerKindComplete = ReaderPayload{}.layoutPerKindComplete;
    const bool defaultCertificationRejected =
        !builds(std::move(defaultCertification));
    std::vector<ReaderPayload> countMismatch = SealedPayloads();
    ++countMismatch[6].layoutObservedKindCounts[0];
    const bool countMismatchRejected = !builds(std::move(countMismatch));
    std::vector<ReaderPayload> requestedLayoutFalse = SealedPayloads();
    const bool requestedLayoutAdvertised =
        !requestedLayoutFalse[6].layoutEnvironment.empty();
    requestedLayoutFalse[6].layoutPerKindComplete = false;
    const bool requestedLayoutFalseRejected = requestedLayoutAdvertised &&
        !builds(std::move(requestedLayoutFalse));
    const auto rangeFixture = [](const bool overlap) {
        std::vector<ReaderPayload> payloads = SealedPayloads();
        auto& paragraph = payloads[1].paragraphs.front();
        const auto firstRun = paragraph.runs.front();
        const std::int64_t offset = overlap
            ? firstRun.end.character - 1 : firstRun.end.character;
        paragraph.runs.push_back({
            {paragraph.start.list, paragraph.start.paragraph, offset},
            {paragraph.start.list, paragraph.start.paragraph, offset}, L""});
        const std::wstring sourceIdentity =
            std::to_wstring(firstRun.start.list) + L":" +
            std::to_wstring(firstRun.start.paragraph) + L":" +
            std::to_wstring(firstRun.start.character) + L":" +
            std::to_wstring(firstRun.end.character);
        const std::wstring identity =
            std::to_wstring(paragraph.start.list) + L":" +
            std::to_wstring(paragraph.start.paragraph) + L":" +
            std::to_wstring(offset) + L":" + std::to_wstring(offset);
        std::vector<hancom::graph::capture::PropertyObservation> copied;
        for (const auto& property : payloads[2].properties) {
            if (property.target == PropertyTarget::Run &&
                property.targetIdentity == sourceIdentity) {
                copied.push_back(property);
                copied.back().targetIdentity = identity;
            }
        }
        payloads[2].properties.insert(
            payloads[2].properties.end(), copied.begin(), copied.end());
        const auto reference = std::find_if(
            payloads[2].definitionReferences.begin(),
            payloads[2].definitionReferences.end(),
            [&sourceIdentity](const auto& candidate) {
                return candidate.source == PropertyTarget::Run &&
                    candidate.sourceIdentity == sourceIdentity;
            });
        if (reference != payloads[2].definitionReferences.end()) {
            payloads[2].definitionReferences.push_back(*reference);
            payloads[2].definitionReferences.back().sourceIdentity = identity;
        }
        const auto coverage = std::find_if(
            payloads[2].coverageFacts.begin(), payloads[2].coverageFacts.end(),
            [&sourceIdentity](const auto& candidate) {
                return candidate.target == PropertyTarget::Run &&
                    candidate.targetIdentity == sourceIdentity;
            });
        if (coverage != payloads[2].coverageFacts.end()) {
            payloads[2].coverageFacts.push_back(*coverage);
            payloads[2].coverageFacts.back().targetIdentity = identity;
        }
        return payloads;
    };
    const bool zeroLengthRange = builds(rangeFixture(false));
    const bool overlappingRange = !builds(rangeFixture(true));
    const bool missingRequired = mutate([](auto& properties) {
        const auto found = std::find_if(
            properties.begin(), properties.end(), [](const auto& property) {
                return property.target == PropertyTarget::Image &&
                    property.key == 12003;
            });
        if (found != properties.end()) properties.erase(found);
    });
    const bool reversedSpan = mutate([](auto& properties) {
        const auto found = std::find_if(
            properties.begin(), properties.end(), [](const auto& property) {
                return property.target == PropertyTarget::Table &&
                    property.key == 12000;
            });
        if (found != properties.end()) found->integerValue = 3;
    });
    const bool swappedKindGeometry = mutate([](auto& properties) {
        const auto source = std::find_if(
            properties.begin(), properties.end(), [](const auto& property) {
                return property.target == PropertyTarget::Cell &&
                    property.key == 12000;
            });
        if (source != properties.end()) {
            auto geometry = *source;
            geometry.key = 12002;
            geometry.scalar = ScalarTag::HWPUNIT64;
            properties.push_back(std::move(geometry));
        }
    });
    const bool wrongUnit = mutate([](auto& properties) {
        const auto found = std::find_if(
            properties.begin(), properties.end(), [](const auto& property) {
                return property.target == PropertyTarget::Image &&
                    property.key == 12002;
            });
        if (found != properties.end()) found->scalar = ScalarTag::Uint64;
    });
    const bool terminalCollapse = mutate([](auto& properties) {
        const auto width = std::find_if(
            properties.begin(), properties.end(), [](const auto& property) {
                return property.target == PropertyTarget::Control &&
                    property.key == 12002;
            });
        if (width == properties.end()) return;
        width->state = ObservationState::NotExposed;
        width->origin = hancom::graph::PropertyOrigin::Unavailable;
        const std::wstring identity = width->targetIdentity;
        properties.erase(std::remove_if(
            properties.begin(), properties.end(), [&identity](const auto& property) {
                return property.target == PropertyTarget::Control &&
                    property.targetIdentity == identity &&
                    property.key == 12003;
            }), properties.end());
    });
    const std::vector<ReaderPayload> terminalFixture = SealedPayloads();
    const bool zeroCoordinateAndUnavailable = std::any_of(
        terminalFixture[6].layoutProperties.begin(),
        terminalFixture[6].layoutProperties.end(), [](const auto& property) {
            return property.target == PropertyTarget::Control &&
                property.key == 12002 &&
                property.state == ObservationState::NotExposed &&
                property.integerValue == 0;
        });
    std::set<std::wstring> equalGeometryTableOwners;
    for (const auto& property : terminalFixture[6].layoutProperties) {
        if (property.target == PropertyTarget::Table &&
            property.key == 12002 && property.integerValue == 14400) {
            equalGeometryTableOwners.insert(property.targetIdentity);
        }
    }
    const bool sameGeometryDistinctOwners =
        equalGeometryTableOwners.size() == 2;
    std::wcout << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_COMPLETE " << complete << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_FALSE_CERT_REJECTED "
               << falseCertificationRejected << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_INCOMPLETE_STATUS "
               << static_cast<unsigned>(incompleteStatus) << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_NO_STORE_MUTATION "
               << incompleteUnpublished << L" generation="
               << incompleteStore.generation << L" publish="
               << incompleteStore.publishCount << L" nodes="
               << incompleteStore.nodeCount << L" anchors="
               << incompleteStore.anchorsCount << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_DEFAULT_CERT_REJECTED "
               << defaultCertificationRejected << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_COUNT_MISMATCH_REJECTED "
               << countMismatchRejected << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_REQUESTED_FALSE_REJECTED "
               << requestedLayoutFalseRejected << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_ZERO_LENGTH_RANGE "
               << zeroLengthRange << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_OVERLAP_REJECTED "
               << overlappingRange << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_MISSING_REJECTED "
               << missingRequired << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_REVERSED_REJECTED "
               << reversedSpan << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_SWAPPED_KIND_REJECTED "
               << swappedKindGeometry << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_WRONG_UNIT_REJECTED "
               << wrongUnit << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_TERMINAL_COLLAPSE_REJECTED "
               << terminalCollapse << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_ZERO_UNAVAILABLE_DISTINCT "
               << zeroCoordinateAndUnavailable << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_SAME_GEOMETRY_DISTINCT_OWNERS "
               << sameGeometryDistinctOwners << L'\n';
    return complete && falseCertificationRejected && incompleteUnpublished &&
        defaultCertificationRejected && countMismatchRejected &&
        requestedLayoutFalseRejected && zeroLengthRange && overlappingRange &&
        missingRequired && reversedSpan &&
        swappedKindGeometry && wrongUnit && terminalCollapse &&
        zeroCoordinateAndUnavailable && sameGeometryDistinctOwners;
}

bool UnavailableLayoutIsHonestSmoke() {
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) {
        return false;
    }
    const std::filesystem::path root =
        std::filesystem::path(temporary) /
        (L"hwp-graph-layout-unavailable-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> payloads = SealedPayloads();
    payloads[6].coverage = hancom::graph::CoverageState::NotExposed;
    payloads[6].layoutEnvironment.clear();
    CaptureSpool spool;
    hancom::graph::capture::CaptureIdentityArena arena;
    const bool built = spool.Build(root, payloads, {}, arena);
    const bool unpublished = !std::filesystem::exists(root / L"records.hgn");
    spool.Reset();
    std::filesystem::remove_all(root, error);
    return !built && unpublished && !std::filesystem::exists(root);
}

// Two captures of the same unmodified document must emit identical node-id
// bytes, otherwise the recorder stream_digest diverges and
// require_equivalent_captures fails even though both captures completed.
bool DeterministicAcquireNodeIdsSmoke() {
    using hancom::graph::NodeId;
    using hancom::graph::capture::CaptureIdentityArena;
    using hancom::graph::identity::IsRfc4122V4;

    const std::wstring occurrence =
        L"control-occurrence:6:tbl:0:0:3:0:7";
    const std::wstring otherOccurrence =
        L"control-occurrence:6:tbl:0:0:3:0:8";

    CaptureIdentityArena first;
    CaptureIdentityArena second;
    NodeId firstDocument{};
    NodeId secondDocument{};
    NodeId firstOccurrence{};
    NodeId secondOccurrence{};
    NodeId firstOther{};
    if (!first.Acquire(L"document", &firstDocument) ||
        !second.Acquire(L"document", &secondDocument) ||
        !first.Acquire(occurrence, &firstOccurrence) ||
        !second.Acquire(occurrence, &secondOccurrence) ||
        !first.Acquire(otherOccurrence, &firstOther)) {
        return false;
    }

    const bool documentStable =
        firstDocument.bytes == secondDocument.bytes;
    const bool occurrenceStable =
        firstOccurrence.bytes == secondOccurrence.bytes;
    const bool keysDistinct =
        firstDocument.bytes != firstOccurrence.bytes &&
        firstOccurrence.bytes != firstOther.bytes;
    const bool version4 =
        IsRfc4122V4(firstDocument) && IsRfc4122V4(firstOccurrence) &&
        IsRfc4122V4(firstOther) && IsRfc4122V4(secondDocument) &&
        IsRfc4122V4(secondOccurrence);

    // A second acquire of an already-issued occurrence key inside one capture
    // still mints a distinct id (duplicate occurrences remain separate nodes).
    NodeId duplicate{};
    const bool duplicateDistinct =
        first.Acquire(occurrence, &duplicate) &&
        duplicate.bytes != firstOccurrence.bytes &&
        IsRfc4122V4(duplicate);
    // ... and that duplicate is itself reproducible across captures.
    NodeId secondDuplicate{};
    const bool duplicateStable =
        second.Acquire(occurrence, &secondDuplicate) &&
        secondDuplicate.bytes == duplicate.bytes;

    // Keys that fall through to identity::ReconcileChildren (cell:, section:,
    // control:, ...) must also be content-derived, otherwise the reconcile mint
    // remains a random-v4 firehose and equivalent captures diverge.
    const std::wstring cell = L"cell:77:A1";
    const std::wstring section = L"section:0";
    NodeId firstCell{};
    NodeId secondCell{};
    NodeId firstSection{};
    NodeId secondSection{};
    const bool reconciledAcquired =
        first.Acquire(cell, &firstCell) && second.Acquire(cell, &secondCell) &&
        first.Acquire(section, &firstSection) &&
        second.Acquire(section, &secondSection);
    const bool reconciledStable = reconciledAcquired &&
        firstCell.bytes == secondCell.bytes &&
        firstSection.bytes == secondSection.bytes;
    const bool reconciledDistinct = reconciledAcquired &&
        firstCell.bytes != firstSection.bytes &&
        firstCell.bytes != firstDocument.bytes &&
        firstCell.bytes != firstOccurrence.bytes;
    const bool reconciledVersion4 = reconciledAcquired &&
        IsRfc4122V4(firstCell) && IsRfc4122V4(secondCell) &&
        IsRfc4122V4(firstSection) && IsRfc4122V4(secondSection);

    std::wcout << L"GRAPH_CAPTURE_DETERMINISTIC_NODE_IDS document="
               << documentStable << L" occurrence=" << occurrenceStable
               << L" distinct=" << keysDistinct << L" v4=" << version4
               << L" duplicate_distinct=" << duplicateDistinct
               << L" duplicate_stable=" << duplicateStable
               << L" reconciled_stable=" << reconciledStable
               << L" reconciled_distinct=" << reconciledDistinct
               << L" reconciled_v4=" << reconciledVersion4 << L'\n';
    return documentStable && occurrenceStable && keysDistinct && version4 &&
        duplicateDistinct && duplicateStable && reconciledStable &&
        reconciledDistinct && reconciledVersion4;
}

} // namespace sealed

} // namespace

bool DuplicateUnavailableTableStableKeyRecaptureSmoke(
    const std::vector<hancom::graph::capture::ControlObservation>& firstControls,
    const hancom::graph::capture::ReaderPayload& firstTables,
    const std::vector<hancom::graph::capture::ControlObservation>& secondControls,
    const hancom::graph::capture::ReaderPayload& secondTables) {
    return sealed::DuplicateUnavailableTableStableKeySmoke(
        firstControls, firstTables, secondControls, secondTables);
}

bool DuplicateEmptyRealTableReaderRecaptureSmoke(
    const std::vector<hancom::graph::capture::ControlObservation>& firstControls,
    const hancom::graph::capture::ReaderPayload& firstTables,
    const std::vector<hancom::graph::capture::ControlObservation>& secondControls,
    const hancom::graph::capture::ReaderPayload& secondTables) {
    return sealed::DuplicateEmptyRealTableReaderRecaptureOracle(
        firstControls, firstTables, secondControls, secondTables, false);
}

bool DuplicateEmptyRealTableReaderByteParitySmoke(
    const std::vector<hancom::graph::capture::ControlObservation>& firstControls,
    const hancom::graph::capture::ReaderPayload& firstTables,
    const std::vector<hancom::graph::capture::ControlObservation>& secondControls,
    const hancom::graph::capture::ReaderPayload& secondTables) {
    return sealed::DuplicateEmptyRealTableReaderRecaptureOracle(
        firstControls, firstTables, secondControls, secondTables, true);
}

bool AttemptInputOrderingRootStabilitySmoke() {
    const std::filesystem::path root =
        std::filesystem::temp_directory_path() /
        (L"hwp-graph-attempt-ordering-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> firstPayloads = sealed::SealedPayloads();
    auto& firstCaption = firstPayloads[4].tables.front();
    firstCaption.captionPresent = {
        hancom::graph::ObservationState::Value, 1};
    firstCaption.captionList = {
        hancom::graph::ObservationState::Value, 500};
    firstCaption.captionStart = {500, 0, 0};
    firstCaption.captionText = {
        hancom::graph::ObservationState::Value, L""};
    firstCaption.captionAutomaticNumber = {
        hancom::graph::ObservationState::Value, 0};
    firstCaption.captionStyleId = {
        hancom::graph::ObservationState::NotExposed, 0};
    firstCaption.captionStyleName.state =
        hancom::graph::ObservationState::NotExposed;
    firstCaption.captionPageStart = {
        hancom::graph::ObservationState::Value, 3};
    firstCaption.captionPageEnd = {
        hancom::graph::ObservationState::Value, 3};
    sealed::RecertifySyntheticLayout(&firstPayloads);
    std::vector<ReaderPayload> secondPayloads = firstPayloads;
    secondPayloads[4].tables.front().captionPageStart.value = 4;
    secondPayloads[4].tables.front().captionPageEnd.value = 4;
    CaptureSpool first;
    CaptureSpool second;
    hancom::graph::capture::CaptureIdentityArena arena;
    const bool built = first.Build(
            root / L"attempt-0", firstPayloads, NormativeEnvironment(), arena) &&
        second.Build(
            root / L"attempt-1", secondPayloads, NormativeEnvironment(), arena);
    const std::uint64_t mismatch = built
        ? AttemptMismatchBits(first.Result(), second.Result())
        : UINT64_MAX;
    CaptureDiagnostics diagnostics;
    CaptureStatus status = CaptureStatus::IncompleteCapture;
    if (built) {
        FakeContext context = Context();
        context.attempts[0] = first.Result();
        context.attempts[1] = second.Result();
        CaptureCoordinator coordinator;
        status = coordinator.Capture(
            Suite(), &context, Publish, &context, {}, &diagnostics);
    }
    const bool exactCaptionDivergence =
        status == CaptureStatus::TraversalMismatch &&
        diagnostics.mismatchBits ==
            (MismatchSemanticRoot | MismatchCaptureRoot) &&
        diagnostics.firstDivergencePresent &&
        diagnostics.firstRecordKind == hancom::graph::RecordKind::Node &&
        diagnostics.firstNodeKind == hancom::graph::NodeKind::Story &&
        diagnostics.firstField == 106;
    first.Reset();
    second.Reset();
    std::filesystem::remove_all(root, error);
    std::wcout << L"GRAPH_CAPTURE_ATTEMPT_INPUT_ORDERING_ROOT_STABILITY "
               << (built && mismatch == 0) << L" mismatch=" << mismatch
               << L" exact_caption_divergence=" << exactCaptionDivergence
               << L" first_kind="
               << static_cast<unsigned>(diagnostics.firstNodeKind)
               << L" first_field=" << diagnostics.firstField << L'\n';
    return built && mismatch == 0 && !error &&
        !std::filesystem::exists(root);
}

bool ParagraphNativeStartCanonicalizationSmoke() {
    const std::filesystem::path root =
        std::filesystem::temp_directory_path() /
        (L"hwp-graph-paragraph-start-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> firstPayloads = sealed::SealedPayloads();
    if (firstPayloads[1].paragraphs.empty()) return false;
    const auto addAnchorControl = [&firstPayloads](
        const std::uint64_t ordinal, const std::int64_t paragraph) {
        hancom::graph::capture::ControlObservation control;
        control.ctrlId = L"anchor-only";
        control.instanceId = L"anchor-only-" + std::to_wstring(ordinal);
        control.headCtrlOrdinal = ordinal;
        control.anchor = {0, paragraph, 7};
        control.adaptedKind = 0;
        firstPayloads[0].controls.push_back(control);
        firstPayloads[3].controls.push_back(std::move(control));
    };
    // Exercise both scanned reuse and multiple trailing anchor-only paths.
    addAnchorControl(9001, 0);
    addAnchorControl(9002, 91);
    addAnchorControl(9003, 92);
    addAnchorControl(9004, 93);
    // Independent assembly must not couple Paragraph.100 to a traversal-vector
    // ordinal; both attempts contain the same scanned and anchor-only paths.
    std::vector<ReaderPayload> secondPayloads = firstPayloads;
    CaptureSpool first;
    CaptureSpool second;
    hancom::graph::capture::CaptureIdentityArena arena;
    const bool built = first.Build(
            root / L"attempt-0", firstPayloads, NormativeEnvironment(), arena) &&
        second.Build(
            root / L"attempt-1", secondPayloads, NormativeEnvironment(), arena);
    const std::uint64_t mismatch = built
        ? AttemptMismatchBits(first.Result(), second.Result())
        : UINT64_MAX;
    const auto startsMatchLocators = [](const AttemptResult& result) {
        if (result.records.file == INVALID_HANDLE_VALUE ||
            result.records.length > SIZE_MAX) return false;
        std::vector<std::uint8_t> bytes(
            static_cast<size_t>(result.records.length));
        LARGE_INTEGER offset{};
        offset.QuadPart = static_cast<LONGLONG>(result.records.offset);
        if (!SetFilePointerEx(result.records.file, offset, nullptr, FILE_BEGIN)) {
            return false;
        }
        size_t consumed = 0;
        while (consumed < bytes.size()) {
            const DWORD requested = static_cast<DWORD>((std::min)(
                bytes.size() - consumed, static_cast<size_t>(MAXDWORD)));
            DWORD actual = 0;
            if (!ReadFile(result.records.file, bytes.data() + consumed,
                          requested, &actual, nullptr) || actual == 0) {
                return false;
            }
            consumed += actual;
        }
        std::vector<sealed::DecodedRecord> records;
        if (!sealed::DecodeRecords(bytes, &records)) return false;
        std::array<bool, 3> trailing{};
        size_t paragraphCount = 0;
        for (const sealed::DecodedRecord& record : records) {
            if (static_cast<hancom::graph::RecordKind>(record.kind) !=
                hancom::graph::RecordKind::Node) continue;
            const sealed::DecodedField* commonField =
                sealed::FindField(record.fields, 1);
            const sealed::DecodedField* payloadField =
                sealed::FindField(record.fields, 2);
            std::vector<sealed::DecodedField> common;
            std::vector<sealed::DecodedField> payload;
            if (commonField == nullptr || payloadField == nullptr ||
                !sealed::ParseFieldStream(commonField->value.data(),
                    commonField->value.size(), &common) ||
                !sealed::ParseFieldStream(payloadField->value.data(),
                    payloadField->value.size(), &payload)) return false;
            const sealed::DecodedField* kind = sealed::FindField(common, 2);
            if (kind == nullptr || kind->value.size() != 2 ||
                sealed::R16(kind->value.data()) !=
                    static_cast<std::uint16_t>(
                        hancom::graph::NodeKind::Paragraph)) continue;
            const sealed::DecodedField* locator = sealed::FindField(common, 7);
            const sealed::DecodedField* start = sealed::FindField(payload, 100);
            std::vector<std::uint8_t> locatorValue;
            std::vector<std::uint8_t> startValue;
            if (locator == nullptr || start == nullptr ||
                !sealed::ObservationPayload(locator->value, &locatorValue) ||
                !sealed::ObservationPayload(start->value, &startValue) ||
                locatorValue.size() != 24 || startValue.size() != 24 ||
                !std::equal(startValue.begin(), startValue.begin() + 16,
                            locatorValue.begin() + 8) ||
                sealed::R64(startValue.data() + 16) != 0) return false;
            const std::uint64_t list = sealed::R64(startValue.data());
            const std::uint64_t paragraph =
                sealed::R64(startValue.data() + 8);
            if (list == 0 && paragraph >= 91 && paragraph <= 93) {
                trailing[static_cast<size_t>(paragraph - 91)] = true;
            }
            ++paragraphCount;
        }
        return paragraphCount != 0 &&
            std::all_of(trailing.begin(), trailing.end(), [](const bool value) {
                return value;
            });
    };
    const bool canonicalStarts = built &&
        startsMatchLocators(first.Result()) &&
        startsMatchLocators(second.Result());
    first.Reset();
    second.Reset();
    std::filesystem::remove_all(root, error);
    std::wcout << L"GRAPH_CAPTURE_PARAGRAPH_NATIVE_START_CANONICALIZATION "
               << (built && mismatch == 0 && canonicalStarts)
               << L" mismatch=" << mismatch
               << L" canonical_starts=" << canonicalStarts << L'\n';
    return built && mismatch == 0 && canonicalStarts && !error &&
        !std::filesystem::exists(root);
}

struct TypedPassCapture final {
    std::filesystem::path directory{};
    std::array<hancom::graph::codec::Bytes, 3> passes{};
    bool complete = true;
};

void CaptureTypedPassBytes(
    void* const raw, const size_t pass, const bool complete) noexcept {
    auto* const capture = static_cast<TypedPassCapture*>(raw);
    if (capture == nullptr || complete || pass == 0 || pass > 3) return;
    try {
        capture->complete = capture->complete && sealed::ReadWholeFile(
            capture->directory / L"records.hgn",
            &capture->passes[pass - 1]);
    } catch (...) {
        capture->complete = false;
    }
}

bool SequentialLargeSpoolIsolationSmoke() {
    const std::filesystem::path root =
        std::filesystem::temp_directory_path() /
        (L"hwp-graph-large-sequential-spool-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<ReaderPayload> payloads = sealed::SealedPayloads();
    for (size_t index = 0; index < 200; ++index) {
        hancom::graph::capture::ControlObservation control{
            L"form",
            L"large-" + std::to_wstring(index),
            10'000 + index,
            {0, static_cast<std::int64_t>(10'000 + index), 0},
            false};
        payloads[0].controls.push_back(control);
        payloads[3].controls.push_back(std::move(control));
    }
    sealed::RecertifySyntheticLayout(&payloads);

    sealed::DescendingUuidSource legacySource{0xd1, 0x00ffffffU};
    sealed::DescendingUuidSource reuseSource{0xd1, 0x00ffffffU};
    hancom::graph::capture::CaptureIdentityArena legacyArena(
        sealed::Source(&legacySource));
    hancom::graph::capture::CaptureIdentityArena reuseArena(
        sealed::Source(&reuseSource));
    CaptureSpool legacyGolden;
    CaptureSpool reuseGolden;
    TypedPassCapture legacyPasses{root / L"legacy-golden"};
    TypedPassCapture reusePasses{root / L"reuse-golden"};
    std::wstring legacyFailure;
    std::wstring reuseFailure;
    SetTypedGraphReuseForTesting(false);
    ResetTypedBuildProfiles();
    hancom::graph::codec::ResetCodecDebugCounters();
    const bool legacyBuilt = legacyGolden.Build(
        legacyPasses.directory, payloads, NormativeEnvironment(), legacyArena,
        &legacyFailure, CaptureTypedPassBytes, &legacyPasses);
    const std::vector<TypedBuildProfile> legacyProfiles =
        ReadTypedBuildProfiles();
    const hancom::graph::codec::CodecDebugCounters legacyCodecCounters =
        hancom::graph::codec::ReadCodecDebugCounters();
    SetTypedGraphReuseForTesting(true);
    ResetTypedBuildProfiles();
    hancom::graph::codec::ResetCodecDebugCounters();
    const bool reuseBuilt = reuseGolden.Build(
        reusePasses.directory, payloads, NormativeEnvironment(), reuseArena,
        &reuseFailure, CaptureTypedPassBytes, &reusePasses);
    const std::vector<TypedBuildProfile> reuseProfiles =
        ReadTypedBuildProfiles();
    const hancom::graph::codec::CodecDebugCounters reuseCodecCounters =
        hancom::graph::codec::ReadCodecDebugCounters();
    const AttemptResult legacyResult = legacyGolden.Result();
    const AttemptResult reuseResult = reuseGolden.Result();
    const std::uint64_t mismatchBits =
        AttemptMismatchBits(legacyResult, reuseResult);
    const bool buildsComplete = legacyBuilt && reuseBuilt;
    const bool passReadsComplete =
        legacyPasses.complete && reusePasses.complete;
    const bool threeOutcomesPresent = std::all_of(
        legacyPasses.passes.begin(), legacyPasses.passes.end(),
        [](const auto& pass) { return !pass.empty(); }) &&
        std::all_of(
            reusePasses.passes.begin(), reusePasses.passes.end(),
            [](const auto& pass) { return !pass.empty(); });
    const bool exactPassBytes = legacyPasses.passes == reusePasses.passes;
    const bool exactFinalBytes = exactPassBytes &&
        legacyResult.records.length == legacyPasses.passes[2].size() &&
        reuseResult.records.length == reusePasses.passes[2].size();
    const bool exactRoots = hancom::graph::codec::Equal(
            legacyResult.manifest.observedSemanticRoot,
            reuseResult.manifest.observedSemanticRoot) &&
        hancom::graph::codec::Equal(
            legacyResult.manifest.layoutRoot,
            reuseResult.manifest.layoutRoot) &&
        hancom::graph::codec::Equal(
            legacyResult.manifest.captureRoot,
            reuseResult.manifest.captureRoot);
    const bool exactFingerprints =
        legacyResult.nodeFingerprints.size() ==
            reuseResult.nodeFingerprints.size() &&
        std::equal(
            legacyResult.nodeFingerprints.begin(),
            legacyResult.nodeFingerprints.end(),
            reuseResult.nodeFingerprints.begin(),
            [](const auto& left, const auto& right) {
                return left.nodeId.bytes == right.nodeId.bytes &&
                    hancom::graph::codec::Equal(
                        left.fingerprint, right.fingerprint);
            });
    const bool oneImmutableTypedPlan = legacyProfiles.size() == 1 &&
        reuseProfiles.size() == 1 && legacyProfiles[0].nodes != 0 &&
        legacyProfiles[0].nodes == reuseProfiles[0].nodes &&
        legacyProfiles[0].recordsBytes == legacyPasses.passes[0].size() &&
        reuseProfiles[0].recordsBytes == reusePasses.passes[0].size();
    const bool exactWalkEncodeCounters =
        legacyCodecCounters.parsedRecordWalks == 1 &&
        reuseCodecCounters.parsedRecordWalks == 1 &&
        legacyCodecCounters.legacyValidatorWalks == 0 &&
        reuseCodecCounters.legacyValidatorWalks == 0 &&
        legacyProfiles.size() == 1 && reuseProfiles.size() == 1;
    LARGE_INTEGER profileFrequency{};
    QueryPerformanceFrequency(&profileFrequency);
    const auto profileTimingBounded = [&profileFrequency](
        const std::vector<TypedBuildProfile>& profiles) {
        if (profileFrequency.QuadPart <= 0) return false;
        const std::uint64_t wallLimit =
            static_cast<std::uint64_t>(profileFrequency.QuadPart) * 30;
        constexpr std::uint64_t cpuLimit100ns = 300'000'000;
        return std::all_of(
            profiles.begin(), profiles.end(),
            [wallLimit, cpuLimit100ns](const TypedBuildProfile& profile) {
                return std::all_of(
                    profile.stages.begin(), profile.stages.end(),
                    [wallLimit, cpuLimit100ns](
                        const TypedBuildTiming& timing) {
                        return timing.wallQpc <= wallLimit &&
                            timing.cpu100ns <= cpuLimit100ns;
                    });
            });
    };
    const bool timingBounded = profileTimingBounded(legacyProfiles) &&
        profileTimingBounded(reuseProfiles);
    const bool passGolden = buildsComplete && passReadsComplete &&
        threeOutcomesPresent && exactPassBytes && exactFinalBytes &&
        exactRoots && exactFingerprints && mismatchBits == 0 &&
        oneImmutableTypedPlan && exactWalkEncodeCounters;
    std::wcout << L"GRAPH_CAPTURE_ONE_LOGICAL_GRAPH_SUBCONDITIONS"
               << L" builds_complete=" << buildsComplete
               << L" pass_reads_complete=" << passReadsComplete
               << L" three_outcomes=" << threeOutcomesPresent
               << L" pass_bytes_equal=" << exactPassBytes
               << L" final_bytes_exact=" << exactFinalBytes
               << L" roots_equal=" << exactRoots
               << L" fingerprints_equal=" << exactFingerprints
               << L" mismatch_bits=" << mismatchBits
               << L" immutable_plans=" << oneImmutableTypedPlan
               << L" legacy_profiles=" << legacyProfiles.size()
               << L" reuse_profiles=" << reuseProfiles.size()
               << L" legacy_walks="
               << legacyCodecCounters.parsedRecordWalks
               << L" reuse_walks=" << reuseCodecCounters.parsedRecordWalks
               << L" legacy_encodes=" << legacyProfiles.size()
               << L" reuse_encodes=" << reuseProfiles.size() << L'\n'
               << L"GRAPH_CAPTURE_TYPED_TIMING_BOUNDED " << timingBounded
               << L" legacy_graph_wall_qpc="
               << (legacyProfiles.empty() ? 0 : legacyProfiles[0].stages[
                      static_cast<size_t>(TypedBuildSubstage::GraphConstruction)]
                      .wallQpc)
               << L" reuse_graph_wall_qpc="
               << (reuseProfiles.empty() ? 0 : reuseProfiles[0].stages[
                      static_cast<size_t>(TypedBuildSubstage::GraphConstruction)]
                      .wallQpc) << L'\n'
               << L"GRAPH_CAPTURE_ONE_LOGICAL_GRAPH_THREE_PASS_GOLDEN "
               << passGolden << L" legacy_failure=" << legacyFailure
               << L" reuse_failure=" << reuseFailure << L'\n';

    hancom::graph::capture::CaptureIdentityArena sharedArena;
    CaptureSpool first;
    CaptureSpool second;
    std::wstring firstFailure;
    std::wstring secondFailure;
    ResetTypedBuildProfiles();
    const ULONGLONG started = GetTickCount64();
    const bool firstBuilt = first.Build(
        root / L"attempt-0", payloads, NormativeEnvironment(), sharedArena,
        &firstFailure);
    const std::vector<TypedBuildProfile> firstProfiles =
        ReadTypedBuildProfiles();
    const bool secondBuilt = firstBuilt && second.Build(
        root / L"attempt-1", payloads, NormativeEnvironment(), sharedArena,
        &secondFailure);
    const ULONGLONG sharedElapsed = GetTickCount64() - started;
    std::vector<ReaderPayload> removedPayloads = payloads;
    removedPayloads[0].controls.pop_back();
    removedPayloads[3].controls.pop_back();
    sealed::RecertifySyntheticLayout(&removedPayloads);
    const size_t tombstonesBefore = sharedArena.TombstoneCount();
    CaptureSpool removed;
    std::wstring removedFailure;
    const bool removedBuilt = secondBuilt && removed.Build(
        root / L"removed", removedPayloads, NormativeEnvironment(),
        sharedArena, &removedFailure);
    const size_t tombstonesAdded =
        sharedArena.TombstoneCount() - tombstonesBefore;
    hancom::graph::capture::CaptureIdentityArena freshArena;
    CaptureSpool fresh;
    std::wstring freshFailure;
    const bool freshBuilt = fresh.Build(
        root / L"fresh", payloads, NormativeEnvironment(), freshArena,
        &freshFailure);
    const AttemptResult firstResult = first.Result();
    const AttemptResult secondResult = second.Result();
    const AttemptResult removedResult = removed.Result();
    const bool rootsStable = firstBuilt && secondBuilt &&
        AttemptMismatchBits(firstResult, secondResult) == 0;
    const bool removalObserved = removedBuilt &&
        AttemptMismatchBits(secondResult, removedResult) != 0;
    const bool freshOpen = freshBuilt && freshArena.TombstoneCount() == 0;
    LARGE_INTEGER typedFrequency{};
    QueryPerformanceFrequency(&typedFrequency);
    for (size_t pass = 0; pass < firstProfiles.size(); ++pass) {
        std::wcout << L"GRAPH_CAPTURE_TYPED_PROFILE pass=" << pass;
        for (size_t stage = 0;
             stage < static_cast<size_t>(TypedBuildSubstage::Count); ++stage) {
            const TypedBuildTiming& timing = firstProfiles[pass].stages[stage];
            const std::uint64_t wallUs = typedFrequency.QuadPart == 0 ? 0 :
                timing.wallQpc * 1'000'000 /
                    static_cast<std::uint64_t>(typedFrequency.QuadPart);
            std::wcout << L" s" << stage << L"_wall_us=" << wallUs
                       << L" s" << stage << L"_cpu_us="
                       << timing.cpu100ns / 10;
        }
        std::wcout << L" nodes=" << firstProfiles[pass].nodes
                   << L" bytes=" << firstProfiles[pass].recordsBytes << L'\n';
    }
    const bool oneTypedPlan = firstProfiles.size() == 1 &&
        firstProfiles[0].nodes != 0 && firstProfiles[0].recordsBytes != 0;
    const bool isolation = rootsStable && removedBuilt && removalObserved &&
        tombstonesAdded == 2 && freshOpen && oneTypedPlan;
    std::wcout << L"GRAPH_CAPTURE_LARGE_SEQUENTIAL_FIXTURE_SEMANTICS "
               << passGolden << L'\n'
               << L"GRAPH_CAPTURE_LARGE_SEQUENTIAL_ISOLATION_SUBCONDITIONS"
               << L" roots_stable=" << rootsStable
               << L" removed_built=" << removedBuilt
               << L" removal_observed=" << removalObserved
               << L" tombstones_exact=" << (tombstonesAdded == 2)
               << L" fresh_open=" << freshOpen
               << L" fresh_tombstones=" << freshArena.TombstoneCount()
               << L" one_typed_plan=" << oneTypedPlan << L'\n'
               << L"GRAPH_CAPTURE_LARGE_SEQUENTIAL_SPOOL_ISOLATION "
               << isolation
               << L" first=" << firstBuilt
               << L" second=" << secondBuilt
               << L" removed=" << removedBuilt
               << L" fresh=" << freshBuilt
               << L" tombstones_added=" << tombstonesAdded
               << L" elapsed_ms=" << sharedElapsed
               << L" second_failure=" << secondFailure
               << L" removed_failure=" << removedFailure
               << L" fresh_failure=" << freshFailure << L'\n';
    legacyGolden.Reset();
    reuseGolden.Reset();
    first.Reset();
    second.Reset();
    removed.Reset();
    fresh.Reset();
    std::filesystem::remove_all(root, error);
    return passGolden && timingBounded && isolation && !error;
}

bool CaptureProgressTelemetrySmoke() {
    constexpr size_t count = 70'000;
    std::vector<hancom::graph::codec::NodeFingerprintResult> first(count);
    std::vector<hancom::graph::codec::NodeFingerprintResult> second(count);
    for (size_t index = 0; index < count; ++index) {
        for (size_t byte = 0; byte < 8; ++byte) {
            first[index].nodeId.bytes[byte] = static_cast<std::uint8_t>(
                index >> (byte * 8));
        }
        first[index].nodeId.bytes[8] = 0x80;
        first[index].fingerprint = Digest(7);
        second[index] = first[index];
    }
    second.back().fingerprint = Digest(8);
    LARGE_INTEGER frequency{};
    LARGE_INTEGER started{};
    LARGE_INTEGER finished{};
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&started);
    NodeId lateNode;
    size_t examined = 0;
    const bool lateFound = FindFirstFingerprintDivergence(
        first, second, &lateNode, &examined);
    QueryPerformanceCounter(&finished);
    const std::uint64_t elapsedMilliseconds =
        frequency.QuadPart == 0 ? UINT64_MAX :
        static_cast<std::uint64_t>(
            (finished.QuadPart - started.QuadPart) * 1000 /
            frequency.QuadPart);
    const bool subquadraticLateMismatch = lateFound &&
        lateNode.bytes == first.back().nodeId.bytes && examined == count &&
        elapsedMilliseconds < 2'000;

    FakeContext complete = Context();
    CaptureCoordinator coordinator;
    const CaptureStatus completeStatus = coordinator.Capture(
        Suite(), &complete, Publish, &complete, {});
    const std::array<size_t, 3> expectedFirst{
        static_cast<size_t>(CaptureProgressPoint::AttemptStart), 0, 0};
    const std::array<size_t, 3> expectedLast{
        static_cast<size_t>(CaptureProgressPoint::PublishEnd), 1, 0};
    FakeContext failed = Context();
    failed.readerFails = true;
    const CaptureStatus failedStatus = coordinator.Capture(
        Suite(), &failed, Publish, &failed, {});
    const bool boundedFailure = !failed.progress.empty() &&
        failed.progress.back()[0] ==
            static_cast<size_t>(CaptureProgressPoint::ReaderStart) &&
        failed.progress.back()[1] == 0;
    const bool passed = subquadraticLateMismatch &&
        completeStatus == CaptureStatus::Complete &&
        !complete.progress.empty() &&
        complete.progress.front() == expectedFirst &&
        complete.progress.back() == expectedLast &&
        failedStatus == CaptureStatus::IncompleteCapture && boundedFailure;
    std::wcout << L"GRAPH_CAPTURE_PROGRESS_TELEMETRY " << passed
               << L" late_nodes=" << count
               << L" examined=" << examined
               << L" elapsed_ms=" << elapsedMilliseconds
               << L" complete_events=" << complete.progress.size()
               << L" failed_events=" << failed.progress.size() << L'\n';
    return passed;
}

bool AttemptFactsStreamingSmoke() {
    const auto root = std::filesystem::temp_directory_path() /
        L"hwp-attempt-facts-streaming-smoke";
    std::error_code error;
    std::filesystem::remove_all(root, error);
    auto firstPayloads = sealed::SealedPayloads();
    auto secondPayloads = sealed::SealedPayloads();
    const auto environment = NormativeEnvironment();
    firstPayloads[6].layoutEnvironment = environment;
    secondPayloads[6].layoutEnvironment = environment;
    hancom::graph::capture::CaptureIdentityArena arena;
    hancom::graph::capture::ResetAttemptStreamDebugCounters();
    hancom::graph::codec::ResetCodecDebugCounters();
    CaptureSpool first;
    CaptureSpool second;
    const bool firstBuilt = first.BuildAttempt(
        root / L"attempt-0", firstPayloads, environment, arena, false);
    const bool noAttemptZeroDirectory =
        !std::filesystem::exists(root / L"attempt-0");
    arena.ReplayNextEncodingPass();
    const bool secondBuilt = second.BuildAttempt(
        root / L"attempt-1", secondPayloads, environment, arena, true);
    const AttemptResult firstResult = first.Result();
    const AttemptResult secondResult = second.Result();
    const bool exactFacts = firstBuilt && secondBuilt &&
        AttemptMismatchBits(firstResult, secondResult) == 0 &&
        firstResult.records.file == INVALID_HANDLE_VALUE &&
        firstResult.facts.finalRecords.itemCount != 0 &&
        firstResult.facts.finalRecords.byteLength ==
            secondResult.facts.finalRecords.byteLength &&
        hancom::graph::codec::Equal(
            firstResult.facts.finalRecords.digest,
            secondResult.facts.finalRecords.digest);
    AttemptResult changedSha = secondResult;
    changedSha.facts.finalRecords.digest.bytes[0] ^= 1;
    AttemptResult changedFingerprint = secondResult;
    changedFingerprint.nodeFingerprints.front().fingerprint.bytes[0] ^= 1;
    AttemptResult changedPass = secondResult;
    changedPass.facts.canonicalPasses[1].captureRoot.bytes[0] ^= 1;
    const bool mutationsDetected =
        (AttemptMismatchBits(firstResult, changedSha) &
         hancom::graph::capture::MismatchFinalRecordStream) != 0 &&
        (AttemptMismatchBits(firstResult, changedFingerprint) &
         hancom::graph::capture::MismatchNodeFingerprints) != 0 &&
        (AttemptMismatchBits(firstResult, changedPass) &
         hancom::graph::capture::MismatchCaptureRoot) != 0;
    const auto counters =
        hancom::graph::capture::ReadAttemptStreamDebugCounters();
    const auto codecCounters =
        hancom::graph::codec::ReadCodecDebugCounters();
    const bool exactOperations = counters.semanticPlans == 2 &&
        counters.compactPasses == 6 && counters.finalEncodes == 2 &&
        counters.rootedPlanEncodes == 0 &&
        codecCounters.retainedRecordCopies == 0 &&
        codecCounters.validatedCanonicalPostWalks == 0 &&
        codecCounters.incrementalAccumulatorFinalizations == 2 &&
        codecCounters.blobHashOperations != 0 &&
        codecCounters.blobHashBufferBytes <= 32 * 1024 &&
        counters.wholeStreamBytes == 0 && counters.attemptFiles == 1 &&
        counters.attemptZeroWrites == 0 && counters.attemptZeroFlushes == 0 &&
        counters.byteVerifiers == 1;
    auto changedPropertyPayloads = firstPayloads;
    changedPropertyPayloads[2].properties.front().textValue += L" changed";
    hancom::graph::capture::CaptureIdentityArena independentArena;
    CaptureSpool independentFirst;
    CaptureSpool independentSecond;
    const bool independentFirstBuilt = independentFirst.BuildAttempt(
        root / L"independent-0", firstPayloads, environment,
        independentArena, false);
    independentArena.ReplayNextEncodingPass();
    const bool independentSecondBuilt = independentFirstBuilt &&
        independentSecond.BuildAttempt(
            root / L"independent-1", changedPropertyPayloads, environment,
            independentArena, true);
    const AttemptResult independentFirstResult = independentFirst.Result();
    const AttemptResult independentSecondResult = independentSecond.Result();
    const bool independentPropertyDivergence = independentFirstBuilt &&
        independentSecondBuilt &&
        !hancom::graph::codec::Equal(
            independentFirstResult.manifest.observedSemanticRoot,
            independentSecondResult.manifest.observedSemanticRoot) &&
        (AttemptMismatchBits(
             independentFirstResult, independentSecondResult) &
         hancom::graph::capture::MismatchSemanticRoot) != 0;
    const bool threePasses = std::all_of(
        firstResult.facts.canonicalPasses.begin(),
        firstResult.facts.canonicalPasses.end(), [](const auto& pass) {
            return pass.rootsPresent && pass.recordStream.itemCount != 0 &&
                !pass.nodeFingerprints.empty();
        });
    const bool regenerated = firstResult.emitDiagnostic != nullptr &&
        firstResult.emitDiagnostic(firstResult.diagnosticContext,
                                   root / L"attempt-0-diagnostic.hgn");
    const bool diagnosticExact = regenerated &&
        std::filesystem::file_size(root / L"attempt-0-diagnostic.hgn", error) ==
            firstResult.facts.finalRecords.byteLength && !error;
    using Failure = hancom::graph::capture::AttemptStreamFailurePoint;
    hancom::graph::capture::SetAttemptStreamFailureForTesting(
        Failure::MismatchRegeneration);
    const bool regenerationFailure = !firstResult.emitDiagnostic(
        firstResult.diagnosticContext, root / L"rejected-diagnostic.hgn");
    hancom::graph::capture::CaptureIdentityArena failureArena;
    CaptureSpool failedZero;
    hancom::graph::capture::SetAttemptStreamFailureForTesting(
        Failure::AttemptZeroSink);
    const bool zeroSinkFailure = !failedZero.BuildAttempt(
        root / L"failed-zero", firstPayloads, environment, failureArena,
        false);
    CaptureSpool failedOne;
    hancom::graph::capture::SetAttemptStreamFailureForTesting(
        Failure::AttemptOneFile);
    const bool oneFileFailure = !failedOne.BuildAttempt(
        root / L"failed-one", secondPayloads, environment, failureArena,
        true);
    hancom::graph::capture::SetAttemptStreamFailureForTesting(Failure::None);
    const bool reset = first.Reset() && second.Reset() &&
        independentFirst.Reset() && independentSecond.Reset() &&
        failedZero.Reset() && failedOne.Reset();
    std::filesystem::remove_all(root, error);
    const bool passed = noAttemptZeroDirectory && exactFacts &&
        mutationsDetected && exactOperations && independentPropertyDivergence &&
        threePasses && diagnosticExact && regenerationFailure && zeroSinkFailure &&
        oneFileFailure && reset && !error;
    std::wcout << L"GRAPH_CAPTURE_ATTEMPT_FACTS_STREAMING " << passed
               << L" records=" << firstResult.facts.finalRecords.itemCount
               << L" bytes=" << firstResult.facts.finalRecords.byteLength
               << L" attempt0_file="
               << (firstResult.records.file != INVALID_HANDLE_VALUE)
               << L" plans=" << counters.semanticPlans
               << L" passes=" << counters.compactPasses
               << L" encodes=" << counters.finalEncodes
               << L" rooted_plan_encodes=" << counters.rootedPlanEncodes
               << L" retained_record_copies="
               << codecCounters.retainedRecordCopies
               << L" post_walks="
               << codecCounters.validatedCanonicalPostWalks
               << L" accumulator_finalizations="
               << codecCounters.incrementalAccumulatorFinalizations
               << L" independent_property_divergence="
               << independentPropertyDivergence
               << L" blob_hashes=" << codecCounters.blobHashOperations
               << L" blob_hash_buffer_bytes="
               << codecCounters.blobHashBufferBytes
               << L" files=" << counters.attemptFiles
               << L" verifiers=" << counters.byteVerifiers << L'\n';
    return passed;
}

bool DocumentGraphCaptureMismatchDiagnosticsSmoke() {
    return AttemptFactsStreamingSmoke() &&
        AttemptMismatchDiagnosticsSmoke() &&
        AttemptInputOrderingRootStabilitySmoke() &&
        ParagraphNativeStartCanonicalizationSmoke() &&
        SequentialLargeSpoolIsolationSmoke() &&
        CaptureProgressTelemetrySmoke();
}

bool DocumentGraphCaptureIntegrationSmoke() {
    return sealed::StructuredBoundaryIntegrationSmoke();
}

bool DocumentGraphCaptureNegativeSmoke() {
    const bool rejected = CurrentIncompleteTypedStreamRejectedSmoke();
    std::wcout << L"GRAPH_CAPTURE_CURRENT_NEGATIVE_INCOMPLETE_STREAM "
               << rejected << L'\n';
    return rejected;
}

bool DocumentGraphCaptureSmoke() {
    bool invalidParagraphLocatorFailClosed = false;
    const bool storyScopedParagraphIdentity =
        StoryScopedParagraphIdentitySmoke(
            &invalidParagraphLocatorFailClosed);
    const bool complete = CompleteCapturePublishesOnceSmoke();
    const bool serialOverlapRed = SerialCoordinatorOverlapRedSmoke();
    const bool attemptArtifacts = AttemptArtifactCoordinatorSmoke();
    const bool terminal = TerminalUnavailableStillPublishesSmoke();
    const bool mismatch = RootMismatchPublishesNothingSmoke();
    const bool mismatchDiagnostics = AttemptMismatchDiagnosticsSmoke();
    const bool reader = ReaderFailureCleansAndPublishesNothingSmoke();
    const bool state = StateChangePublishesNothingSmoke();
    const bool cancel = CancellationPublishesNothingSmoke();
    const bool injection = FailureInjectionMatrixSmoke();
    const bool sessionLifecycle = AtomicSessionLifecycleAndFreshRetrySmoke();
    const bool publish = PublishFailureDoesNotAdvanceSmoke();
    const bool store = StoreRejectsMalformedCandidateWithoutExposureSmoke();
    const bool positive = RealSpoolsPublishAtomicallySmoke();
    const bool currentNegative = CurrentIncompleteTypedStreamRejectedSmoke();
    const bool characterShapeCardinality =
        sealed::CharacterShapeReferenceCardinalityFailsClosedSmoke();
    const bool reconciliation =
        sealed::LocatorMovementReconciliationSmoke();
    const bool emptyDuplicateRecaptureIdentity =
        sealed::EmptyDuplicateInstanceRecaptureIdentitySmoke();
    const bool duplicateEmptyIdentity =
        sealed::DuplicateEmptyIdentityTraversalReopenMovementSmoke();
    const bool canonicalReceiptUuidOrdering =
        sealed::CanonicalReceiptUuidOrderingSmoke();
    const bool genericControlCompleteIdentityKey =
        sealed::GenericControlCompleteIdentityKeySmoke();
    const bool probeTableInstanceId =
        sealed::ProbeTableInstanceIdDecodedNodeSmoke();
    const bool uniqueCrossOwnerTableStableKey =
        sealed::UniqueCrossOwnerTableStableKeySmoke();
    const bool layoutRoots = sealed::LayoutEnvironmentTransportSmoke();
    const bool compositeTargets =
        sealed::CompositePropertyTargetIdentitySmoke();
    const bool perKindLayout =
        sealed::PerKindLayoutGeometryContractSmoke();
    const bool layoutUnavailable = sealed::UnavailableLayoutIsHonestSmoke();
    const bool deterministicNodeIds =
        sealed::DeterministicAcquireNodeIdsSmoke();
    const bool bodyListUnavailable =
        sealed::UnavailableBodyListFailsClosedSmoke();
    const bool cellListState =
        sealed::CellStoryListStateFailsClosedSmoke();
    const bool captionListOrigin =
        sealed::UnavailableCaptionListPublishesTerminalSmoke();
    const bool payloadOnlyCaptionLocation =
        sealed::PayloadOnlyCaptionLocationFailsClosedSmoke();
    const bool forgedCaptionQualification =
        sealed::ManuallyForgedCaptionQualificationFailsClosedSmoke();
    const bool cleanupFailure = sealed::CleanupFailureIsObservableSmoke();
    const bool storeAbortMatrix =
        sealed::ProductionStoreFailureAbortMatrixSmoke();
    const bool baseline = sealed::BaselineSealedGenerationDecodesSmoke();
    const sealed::TypedPopulationChecks typed =
        sealed::TypedGenerationPopulationSmoke();
    const sealed::StrictTypedChecks strict =
        sealed::StrictTypedEmissionSmoke();
    std::wcout << L"GRAPH_CAPTURE_STORY_SCOPED_PARAGRAPH_IDENTITY "
               << storyScopedParagraphIdentity << L'\n'
               << L"GRAPH_CAPTURE_INVALID_PARAGRAPH_LOCATOR_FAIL_CLOSED "
               << invalidParagraphLocatorFailClosed << L'\n'
               << L"GRAPH_CAPTURE_COMPLETE " << complete << L'\n'
               << L"GRAPH_CAPTURE_ATTEMPT_ARTIFACT_SERIAL_RED "
               << serialOverlapRed << L'\n'
               << L"GRAPH_CAPTURE_ATTEMPT_ARTIFACT_PIPELINE "
               << attemptArtifacts << L'\n'
               << L"GRAPH_CAPTURE_TERMINAL_FACTS " << terminal << L'\n'
               << L"GRAPH_CAPTURE_ROOT_MISMATCH " << mismatch << L'\n'
               << L"GRAPH_CAPTURE_MISMATCH_DIAGNOSTICS "
               << mismatchDiagnostics << L'\n'
               << L"GRAPH_CAPTURE_READER_FAILURE " << reader << L'\n'
               << L"GRAPH_CAPTURE_STATE_CHANGE " << state << L'\n'
               << L"GRAPH_CAPTURE_CANCELLATION " << cancel << L'\n'
               << L"GRAPH_CAPTURE_FAILURE_MATRIX " << injection << L'\n'
               << L"GRAPH_CAPTURE_ATOMIC_SESSION_LIFECYCLE "
               << sessionLifecycle << L'\n'
               << L"GRAPH_CAPTURE_PUBLISH_FAILURE " << publish << L'\n';
    std::wcout << L"GRAPH_CAPTURE_STORE_ATOMIC_REJECT " << store << L'\n';
    std::wcout << L"GRAPH_CAPTURE_STORE_POSITIVE " << positive << L'\n'
               << L"GRAPH_CAPTURE_CURRENT_NEGATIVE_INCOMPLETE_STREAM "
               << currentNegative << L'\n'
               << L"GRAPH_CAPTURE_CHARACTER_SHAPE_CARDINALITY_REJECT "
               << characterShapeCardinality << L'\n';
    std::wcout << L"GRAPH_CAPTURE_LOCATOR_MOVEMENT_RECONCILIATION "
               << reconciliation << L'\n'
               << L"GRAPH_CAPTURE_CANONICAL_RECEIPT_ORDERING_SENTINEL "
               << canonicalReceiptUuidOrdering << L'\n'
               << L"TABLE_STABLE_KEY_NON_TABLE_CONTROL_REGRESSION "
               << genericControlCompleteIdentityKey << L'\n'
               << L"TABLE_STABLE_KEY_FOCUSED_PRESENCE_MATRIX "
               << (probeTableInstanceId &&
                   genericControlCompleteIdentityKey) << L'\n'
               << L"TABLE_STABLE_KEY_UNIQUE_CROSS_OWNER_MATRIX "
               << uniqueCrossOwnerTableStableKey << L'\n';
    std::wcout << L"GRAPH_CAPTURE_LAYOUT_ENVIRONMENT_TRANSPORT " << layoutRoots
               << L'\n'
               << L"GRAPH_CAPTURE_COMPOSITE_TARGET_MATRIX "
               << compositeTargets << L'\n'
               << L"GRAPH_CAPTURE_PER_KIND_LAYOUT_MATRIX "
               << perKindLayout << L'\n'
               << L"GRAPH_CAPTURE_LAYOUT_UNAVAILABLE_HONEST "
               << layoutUnavailable << L'\n'
               << L"GRAPH_CAPTURE_BODY_LIST_UNAVAILABLE_FAIL_CLOSED "
               << bodyListUnavailable << L'\n'
               << L"GRAPH_CAPTURE_CELL_STORY_LIST_STATE_FAIL_CLOSED "
               << cellListState << L'\n'
               << L"GRAPH_CAPTURE_CAPTION_NATIVE_LIST_TERMINAL "
               << captionListOrigin << L'\n'
               << L"GRAPH_CAPTURE_PAYLOAD_ONLY_CAPTION_LOCATION_FAIL_CLOSED "
               << payloadOnlyCaptionLocation << L'\n'
               << L"GRAPH_CAPTURE_MANUAL_CAPTION_QUALIFICATION_FAIL_CLOSED "
               << forgedCaptionQualification << L'\n'
               << L"GRAPH_CAPTURE_CLEANUP_FAILURE_OBSERVABLE "
               << cleanupFailure << L'\n'
               << L"GRAPH_CAPTURE_STORE_ABORT_MATRIX "
               << storeAbortMatrix << L'\n';
    std::wcout << L"GRAPH_CAPTURE_BASELINE_DECODE " << baseline << L'\n'
               << L"GRAPH_CAPTURE_TYPED_COUNTS manifest=" << typed.manifests
               << L" node=" << typed.nodes << L" edge=" << typed.edges
               << L" property=" << typed.properties << L" coverage="
               << typed.coverageRecords << L'\n'
               << L"GRAPH_CAPTURE_TYPED_RECORD_KINDS " << typed.recordKinds
               << L'\n'
               << L"GRAPH_CAPTURE_TYPED_NODE_KINDS " << typed.nodeKinds
               << L'\n'
               << L"GRAPH_CAPTURE_TYPED_NODE_IDS_RFC4122V4 "
               << typed.nodeIdsValid << L'\n'
               << L"GRAPH_CAPTURE_TYPED_DOCUMENT_ENVELOPE "
               << typed.documentEnvelope << L'\n'
               << L"GRAPH_CAPTURE_TYPED_TERMINAL_FACTS " << typed.terminals
               << L'\n'
               << L"GRAPH_CAPTURE_TYPED_ROOTS_MATCH " << typed.roots << L'\n'
               << L"GRAPH_CAPTURE_TYPED_NODEID_REUSE " << typed.idReuse
               << L'\n'
               << L"GRAPH_CAPTURE_TYPED_NOT_OPAQUE_ONLY "
               << typed.notOpaqueOnly << L'\n';
    std::wcout << L"GRAPH_CAPTURE_STRICT_BUILT " << strict.built << L'\n'
               << L"GRAPH_CAPTURE_STRICT_EXACT_COUNTS "
               << strict.exactCounts << L'\n'
               << L"GRAPH_CAPTURE_STRICT_LOCATORS " << strict.locators
               << L'\n'
               << L"GRAPH_CAPTURE_STRICT_STORY102_NATIVE_LISTS "
               << strict.storyNativeLists << L'\n'
               << L"GRAPH_CAPTURE_INDEPENDENT_BODY_LIST_PROVENANCE "
               << strict.independentBodyProvenance << L'\n'
               << L"GRAPH_CAPTURE_STRICT_NATIVE_ORDINALS "
               << strict.nativeOrdinals << L'\n'
               << L"GRAPH_CAPTURE_STRICT_INSTANCE_IDS "
               << strict.instanceIds << L'\n'
               << L"GRAPH_CAPTURE_STRICT_NESTED_OWNERSHIP "
               << strict.nestedOwnership << L'\n'
               << L"GRAPH_CAPTURE_STRICT_CONTAINS_CARDINALITY "
               << strict.containsCardinality << L'\n'
               << L"GRAPH_CAPTURE_STRICT_ANCHORS_CARDINALITY "
               << strict.anchorsCardinality << L'\n'
               << L"GRAPH_CAPTURE_STRICT_ANCHORS_COUNTS generic="
               << strict.genericControlAnchors << L" table="
               << strict.tableAnchors << L" image=" << strict.imageAnchors
               << L" non_control=" << strict.nonControlAnchors << L'\n'
               << L"GRAPH_CAPTURE_STRICT_PROPERTY_ORDERING "
               << strict.propertyOrdering << L'\n'
               << L"GRAPH_CAPTURE_STRICT_LAYOUT_PROPERTIES "
               << strict.layoutProperties << L'\n'
               << L"GRAPH_CAPTURE_STRICT_EFFECTIVE_TERMINALS "
               << strict.effectiveTerminals << L'\n'
               << L"GRAPH_CAPTURE_STRICT_PLAN_FINGERPRINT "
               << strict.planFingerprint << L'\n'
               << L"GRAPH_CAPTURE_STRICT_FRESH_ARENA_RETENTION "
               << strict.freshArenaRetention << L'\n';
    std::wcout << L"GRAPH_CAPTURE_RETURN_DETAIL empty_duplicate="
               << emptyDuplicateRecaptureIdentity
               << L" duplicate_identity=" << duplicateEmptyIdentity
               << L" canonical_receipt=" << canonicalReceiptUuidOrdering
               << L" probe_table=" << probeTableInstanceId
               << L" unique_cross_owner=" << uniqueCrossOwnerTableStableKey
               << L" typed=" << typed.All() << L" strict=" << strict.All()
               << L'\n';
    return storyScopedParagraphIdentity && complete && serialOverlapRed &&
        attemptArtifacts &&
        terminal && mismatch &&
        mismatchDiagnostics && reader && state && cancel &&
        injection && sessionLifecycle && publish && store && positive && currentNegative &&
        characterShapeCardinality && reconciliation &&
        emptyDuplicateRecaptureIdentity &&
        duplicateEmptyIdentity && canonicalReceiptUuidOrdering &&
        genericControlCompleteIdentityKey && probeTableInstanceId &&
        uniqueCrossOwnerTableStableKey && layoutRoots && compositeTargets &&
        perKindLayout && layoutUnavailable && bodyListUnavailable && cellListState &&
        captionListOrigin && payloadOnlyCaptionLocation &&
        forgedCaptionQualification && cleanupFailure && storeAbortMatrix &&
        baseline && deterministicNodeIds && typed.All() &&
        strict.All();
}

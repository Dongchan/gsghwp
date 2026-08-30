#include "../DocumentGraphText.h"

#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>

namespace {

using hancom::graph::text::AtomKind;
using hancom::graph::text::CaptureDiagnostics;
using hancom::graph::text::CaptureNativeTextSource;
using hancom::graph::text::CaptureStatus;
using hancom::graph::text::CaptureLimits;
using hancom::graph::text::DocumentGraphTextSink;
using hancom::graph::text::NativeParagraphCoverage;
using hancom::graph::text::NativeParagraphRecord;
using hancom::graph::text::NativeShapeFingerprint;
using hancom::graph::text::NativeTextAtomRecord;
using hancom::graph::text::NativeTextRunRecord;
using hancom::graph::text::NativeTextSource;
using hancom::graph::text::ReadStatus;
using hancom::graph::text::ScanDisposition;

NativeShapeFingerprint Shape(const std::uint8_t value, const bool complete = true) {
    NativeShapeFingerprint shape;
    shape.bytes.fill(value);
    shape.complete = complete;
    return shape;
}

NativeTextAtomRecord Text(
    std::wstring value,
    const NativeShapeFingerprint& shape) {
    NativeTextAtomRecord atom;
    atom.kind = AtomKind::EditableText;
    atom.nativeState = 2;
    atom.text = std::move(value);
    atom.shape = shape;
    return atom;
}

NativeTextAtomRecord State(const LONG state) {
    NativeTextAtomRecord atom;
    atom.nativeState = state;
    atom.kind = state == 3
        ? AtomKind::ParagraphBoundary
        : state == 4
        ? AtomKind::EnterControl
        : AtomKind::ExitControl;
    return atom;
}

class FakeSource final : public NativeTextSource {
public:
    ReadStatus Begin(NativeParagraphRecord* const value) noexcept override {
        ++beginCalls;
        *value = paragraph;
        return failBegin ? ReadStatus::Failure : ReadStatus::Value;
    }

    ReadStatus Next(NativeTextAtomRecord* const value) noexcept override {
        if (nextCalls == failAt) {
            ++nextCalls;
            return ReadStatus::Failure;
        }
        if (nextCalls >= atoms.size()) {
            ++nextCalls;
            return terminalStatus;
        }
        *value = atoms[nextCalls++];
        return ReadStatus::Value;
    }

    ScanDisposition Finish() noexcept override {
        ++finishCalls;
        return failFinish
            ? ScanDisposition::ReleaseFailed
            : finishDisposition;
    }

    NativeParagraphRecord paragraph{};
    std::vector<NativeTextAtomRecord> atoms{};
    size_t failAt = static_cast<size_t>(-1);
    bool failBegin = false;
    bool failFinish = false;
    ScanDisposition finishDisposition = ScanDisposition::Released;
    ReadStatus terminalStatus = ReadStatus::EndList;
    size_t beginCalls = 0;
    size_t nextCalls = 0;
    size_t finishCalls = 0;
};

class TransactionalSink final : public DocumentGraphTextSink {
public:
    bool BeginParagraph(
        const NativeParagraphRecord& value) noexcept override {
        pendingParagraph = value;
        began = true;
        return !failBegin;
    }

    bool AppendAtom(const NativeTextAtomRecord& value) noexcept override {
        if (failAtom) {
            return false;
        }
        pendingAtoms.push_back(value);
        return true;
    }

    bool AppendRun(const NativeTextRunRecord& value) noexcept override {
        if (failRun) {
            return false;
        }
        pendingRuns.push_back(value);
        return true;
    }

    bool MarkRunShapeNotExposed(
        const NativeTextRunRecord& value) noexcept override {
        if (failCoverage) {
            return false;
        }
        pendingShapeGaps.push_back(value);
        return true;
    }

    bool MarkParagraphCoverage(
        const NativeParagraphCoverage& value) noexcept override {
        if (failCoverage) {
            return false;
        }
        pendingCoverage.push_back(value);
        return true;
    }

    bool Commit() noexcept override {
        if (failCommit) {
            return false;
        }
        publishedParagraph = pendingParagraph;
        publishedAtoms = pendingAtoms;
        publishedRuns = pendingRuns;
        publishedShapeGaps = pendingShapeGaps;
        publishedCoverage = pendingCoverage;
        committed = true;
        return true;
    }

    void Abort() noexcept override {
        aborted = true;
        pendingAtoms.clear();
        pendingRuns.clear();
        pendingShapeGaps.clear();
        pendingCoverage.clear();
        publishedAtoms.clear();
        publishedRuns.clear();
        publishedShapeGaps.clear();
        publishedCoverage.clear();
    }

    bool failBegin = false;
    bool failAtom = false;
    bool failRun = false;
    bool failCoverage = false;
    bool failCommit = false;
    bool began = false;
    bool committed = false;
    bool aborted = false;
    NativeParagraphRecord pendingParagraph{};
    NativeParagraphRecord publishedParagraph{};
    std::vector<NativeTextAtomRecord> pendingAtoms{};
    std::vector<NativeTextRunRecord> pendingRuns{};
    std::vector<NativeTextRunRecord> pendingShapeGaps{};
    std::vector<NativeParagraphCoverage> pendingCoverage{};
    std::vector<NativeTextAtomRecord> publishedAtoms{};
    std::vector<NativeTextRunRecord> publishedRuns{};
    std::vector<NativeTextRunRecord> publishedShapeGaps{};
    std::vector<NativeParagraphCoverage> publishedCoverage{};
};

bool RunSegmentationSmoke() {
    FakeSource source;
    source.atoms = {
        Text(L"A", Shape(1)),
        Text(L"B", Shape(2)),
        Text(L"C", Shape(1)),
        Text(L"D", Shape(1)),
    };
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete && source.finishCalls == 1 &&
        sink.publishedRuns.size() == 1 &&
        sink.publishedRuns[0].text == L"ABCD" &&
        sink.publishedShapeGaps.size() == 1 &&
        sink.publishedCoverage.size() == 1;
}

bool UnicodeAndNativeStateSmoke() {
    FakeSource source;
    source.atoms = {
        Text(L"\xD83D\xDE00", Shape(7)),
        Text(L"e\u0301", Shape(7)),
        State(4),
        State(5),
        State(3),
    };
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete &&
        sink.publishedRuns.size() == 1 &&
        sink.publishedRuns[0].text == L"\xD83D\xDE00" L"e\u0301" &&
        sink.publishedAtoms.size() == 5 &&
        sink.publishedAtoms[2].kind == AtomKind::EnterControl &&
        sink.publishedAtoms[3].kind == AtomKind::ExitControl &&
        sink.publishedAtoms[4].kind == AtomKind::ParagraphBoundary;
}

bool LoneSurrogateAndSplitCombiningSmoke() {
    FakeSource source;
    source.atoms = {
        Text(L"\xD800", Shape(0, false)),
        Text(L"e", Shape(0, false)),
        Text(L"\u0301", Shape(0, false)),
    };
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete &&
        sink.publishedRuns.size() == 1 &&
        sink.publishedRuns[0].text ==
            std::wstring(L"\xD800") + L"e" + L"\u0301";
}

bool ParagraphBoundaryTextIsPreservedSmoke() {
    FakeSource source;
    NativeTextAtomRecord paragraph = State(3);
    paragraph.text = L"paragraph\r\n";
    paragraph.shape = Shape(0, false);
    source.atoms = {paragraph};
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete &&
        sink.publishedAtoms.size() == 1 &&
        sink.publishedAtoms[0].text == L"paragraph\r\n" &&
        sink.publishedRuns.size() == 1 &&
        sink.publishedRuns[0].text == L"paragraph\r\n" &&
        diagnostics.paragraphBoundaryCount == 1;
}

bool EmptyParagraphInsertionShapeSmoke() {
    FakeSource source;
    source.paragraph.insertionShape = Shape(9);
    source.paragraph.insertionShapeExposed = true;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete && sink.committed &&
        sink.publishedRuns.empty() && sink.publishedAtoms.empty() &&
        sink.publishedParagraph.insertionShapeExposed &&
        sink.publishedParagraph.insertionShape == Shape(9) &&
        sink.publishedCoverage.size() == 1;
}

bool IncompleteShapesNeverCoalesceSmoke() {
    FakeSource source;
    source.atoms = {
        Text(L"A", Shape(0, false)),
        Text(L"B", Shape(0, false)),
    };
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete &&
        sink.publishedRuns.size() == 1 &&
        sink.publishedRuns[0].text == L"AB" &&
        sink.publishedShapeGaps.size() == 1 &&
        diagnostics.shapeNotExposedCount == 1;
}

bool FailureReleasesAndRejectsPartialPublishSmoke() {
    FakeSource source;
    source.atoms = {Text(L"A", Shape(1))};
    source.failAt = 1;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::ReadFailed && source.finishCalls == 1 &&
        diagnostics.scanDisposition == ScanDisposition::Released &&
        sink.aborted && !sink.committed &&
        sink.publishedAtoms.empty() && sink.publishedRuns.empty();
}

bool UnbalancedControlFailsClosedSmoke() {
    FakeSource source;
    source.atoms = {State(4), Text(L"value", Shape(1))};
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::InvalidNativeState &&
        source.finishCalls == 1 && sink.aborted && !sink.committed &&
        sink.publishedAtoms.empty();
}

bool BudgetExceededFailsClosedSmoke() {
    FakeSource source;
    source.atoms = {Text(L"abcd", Shape(1))};
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    CaptureLimits limits;
    limits.maximumAtoms = 10;
    limits.maximumTextCodeUnits = 3;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics, limits);
    return status == CaptureStatus::BudgetExceeded &&
        source.finishCalls == 1 && sink.aborted && !sink.committed &&
        sink.publishedAtoms.empty();
}

bool AtomBudgetDoesNotDependOnDiagnosticsSmoke() {
    FakeSource source;
    source.atoms = {
        Text(L"a", Shape(1)),
        Text(L"b", Shape(1)),
    };
    TransactionalSink sink;
    CaptureLimits limits;
    limits.maximumAtoms = 1;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, nullptr, limits);
    return status == CaptureStatus::BudgetExceeded &&
        source.finishCalls == 1 && sink.aborted && !sink.committed;
}

bool NativeTerminalStatesSmoke() {
    FakeSource noText;
    noText.terminalStatus = ReadStatus::EndNoText;
    TransactionalSink noTextSink;
    CaptureDiagnostics noTextDiagnostics;
    const CaptureStatus noTextStatus =
        CaptureNativeTextSource(noText, noTextSink, &noTextDiagnostics);

    FakeSource listEnd;
    listEnd.terminalStatus = ReadStatus::EndList;
    TransactionalSink listEndSink;
    CaptureDiagnostics listEndDiagnostics;
    const CaptureStatus listEndStatus =
        CaptureNativeTextSource(listEnd, listEndSink, &listEndDiagnostics);

    return noTextStatus == CaptureStatus::Complete &&
        listEndStatus == CaptureStatus::Complete &&
        noText.finishCalls == 1 && listEnd.finishCalls == 1 &&
        noTextSink.publishedCoverage.size() == 1 &&
        listEndSink.publishedCoverage.size() == 1;
}

bool BeginFailureDoesNotClaimReleaseSmoke() {
    FakeSource source;
    source.failBegin = true;
    source.finishDisposition = ScanDisposition::NotAcquired;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::ReadFailed &&
        source.finishCalls == 1 &&
        diagnostics.scanDisposition == ScanDisposition::NotAcquired &&
        sink.aborted;
}

bool SinkAndFinishFailureMatrixSmoke() {
    auto run = [](
                   const bool failBegin,
                   const bool failAtom,
                   const bool failRun,
                   const bool failCoverage,
                   const bool failCommit,
                   const bool failFinish) {
        FakeSource source;
        source.atoms = {Text(L"value", Shape(0, false))};
        source.failBegin = failBegin;
        source.failFinish = failFinish;
        TransactionalSink sink;
        sink.failBegin = failBegin;
        sink.failAtom = failAtom;
        sink.failRun = failRun;
        sink.failCoverage = failCoverage;
        sink.failCommit = failCommit;
        CaptureDiagnostics diagnostics;
        const CaptureStatus status =
            CaptureNativeTextSource(source, sink, &diagnostics);
        return status != CaptureStatus::Complete &&
            source.finishCalls == 1 && sink.aborted && !sink.committed &&
            sink.publishedAtoms.empty() && sink.publishedRuns.empty();
    };
    return run(true, false, false, false, false, false) &&
        run(false, true, false, false, false, false) &&
        run(false, false, true, false, false, false) &&
        run(false, false, false, true, false, false) &&
        run(false, false, false, false, true, false) &&
        run(false, false, false, false, false, true);
}

bool InvalidStateFailsClosedSmoke() {
    FakeSource source;
    NativeTextAtomRecord invalid;
    invalid.nativeState = 6;
    source.atoms = {invalid};
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::InvalidNativeState &&
        source.finishCalls == 1 && sink.aborted && !sink.committed;
}

bool LongParagraphIsNotTruncatedSmoke() {
    FakeSource source;
    source.atoms = {Text(std::wstring(300'000, L'x'), Shape(3))};
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeTextSource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete &&
        sink.publishedRuns.size() == 1 &&
        sink.publishedRuns[0].text.size() == 300'000;
}

} // namespace

bool DocumentGraphTextSmoke() {
    const bool runs = RunSegmentationSmoke();
    const bool unicode = UnicodeAndNativeStateSmoke();
    const bool loneSurrogate = LoneSurrogateAndSplitCombiningSmoke();
    const bool empty = EmptyParagraphInsertionShapeSmoke();
    const bool paragraphBoundary = ParagraphBoundaryTextIsPreservedSmoke();
    const bool incomplete = IncompleteShapesNeverCoalesceSmoke();
    const bool failure = FailureReleasesAndRejectsPartialPublishSmoke();
    const bool unbalanced = UnbalancedControlFailsClosedSmoke();
    const bool budget = BudgetExceededFailsClosedSmoke();
    const bool atomBudget = AtomBudgetDoesNotDependOnDiagnosticsSmoke();
    const bool terminals = NativeTerminalStatesSmoke();
    const bool notAcquired = BeginFailureDoesNotClaimReleaseSmoke();
    const bool failureMatrix = SinkAndFinishFailureMatrixSmoke();
    const bool invalidState = InvalidStateFailsClosedSmoke();
    const bool longParagraph = LongParagraphIsNotTruncatedSmoke();
    std::wcout << L"TEXT_RUN_SEGMENTATION " << runs << L'\n'
               << L"TEXT_UNICODE_NATIVE_STATES " << unicode << L'\n'
               << L"TEXT_LONE_SURROGATE_SPLIT_COMBINING " << loneSurrogate
               << L'\n'
               << L"TEXT_EMPTY_INSERTION_SHAPE " << empty << L'\n'
               << L"TEXT_PARAGRAPH_BOUNDARY_TEXT " << paragraphBoundary << L'\n'
               << L"TEXT_INCOMPLETE_SHAPE_NO_COALESCE " << incomplete << L'\n'
               << L"TEXT_FAILURE_RELEASE_FAIL_CLOSED " << failure << L'\n'
               << L"TEXT_UNBALANCED_CONTROL_FAIL_CLOSED " << unbalanced << L'\n'
               << L"TEXT_BUDGET_EXCEEDED_FAIL_CLOSED " << budget << L'\n'
               << L"TEXT_ATOM_BUDGET_DIAGNOSTICS_INDEPENDENT " << atomBudget
               << L'\n'
               << L"TEXT_NATIVE_TERMINAL_STATES_0_1 " << terminals << L'\n'
               << L"TEXT_NOT_ACQUIRED_NOT_RELEASED " << notAcquired << L'\n'
               << L"TEXT_FAILURE_MATRIX_RELEASE_ONCE " << failureMatrix << L'\n'
               << L"TEXT_INVALID_STATE_FAIL_CLOSED " << invalidState << L'\n'
               << L"TEXT_LONG_PARAGRAPH_NOT_TRUNCATED " << longParagraph
               << L'\n';
    return runs && unicode && loneSurrogate && empty && paragraphBoundary &&
        incomplete && failure && unbalanced && budget && notAcquired &&
        atomBudget && terminals && failureMatrix && invalidState &&
        longParagraph;
}

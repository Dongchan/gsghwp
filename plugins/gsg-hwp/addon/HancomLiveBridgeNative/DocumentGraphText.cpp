#include "DocumentGraphText.h"

#include "ComState.h"
#include "DispatchInvoke.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <new>
#include <utility>

namespace hancom::graph::text {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsLong;
using hancom::dispatch::Method;

void SetDiagnostics(
    CaptureDiagnostics* const diagnostics,
    const CaptureStatus status) noexcept {
    if (diagnostics != nullptr) {
        diagnostics->status = status;
    }
}

CaptureStatus Fail(
    NativeTextSource& source,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* const diagnostics,
    const CaptureStatus status,
    bool* const finished) noexcept {
    if (!*finished) {
        *finished = true;
        const ScanDisposition disposition = source.Finish();
        if (diagnostics != nullptr) {
            diagnostics->scanDisposition = disposition;
        }
    }
    sink.Abort();
    SetDiagnostics(diagnostics, status);
    return status;
}

bool FlushRun(
    NativeTextRunRecord* const run,
    bool* const hasRun,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (!*hasRun) {
        return true;
    }
    if (!sink.AppendRun(*run)) {
        return false;
    }
    if (!run->shape.complete) {
        if (!sink.MarkRunShapeNotExposed(*run)) {
            return false;
        }
        if (diagnostics != nullptr) {
            ++diagnostics->shapeNotExposedCount;
        }
    }
    if (diagnostics != nullptr) {
        ++diagnostics->runCount;
    }
    *run = {};
    *hasRun = false;
    return true;
}

bool MarkCoverage(
    const std::uint64_t paragraphOrdinal,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* const diagnostics) noexcept {
    NativeParagraphCoverage coverage;
    coverage.paragraphOrdinal = paragraphOrdinal;
    if (!sink.MarkParagraphCoverage(coverage)) {
        return false;
    }
    if (diagnostics != nullptr) {
        ++diagnostics->paragraphCoverageCount;
    }
    return true;
}

AtomKind AtomKindForState(const LONG state) noexcept {
    switch (state) {
        case 2:
            return AtomKind::EditableText;
        case 3:
            return AtomKind::ParagraphBoundary;
        case 4:
            return AtomKind::EnterControl;
        default:
            return AtomKind::ExitControl;
    }
}

class ComTextSource final : public NativeTextSource {
public:
    ComTextSource(
        IDispatch* const hwp,
        const LONG option,
        const LONG range,
        const bool documentStart) noexcept
        : hwp_(hwp),
          option_(option),
          range_(range),
          documentStart_(documentStart) {}

    ReadStatus Begin(NativeParagraphRecord* const paragraph) noexcept override {
        if (paragraph == nullptr || hwp_ == nullptr || begun_) {
            return ReadStatus::Failure;
        }
        if (!documentStart_) {
            hancom::com_state::Position position;
            if (FAILED(hancom::com_state::CapturePosition(hwp_, &position))) {
                return ReadStatus::Failure;
            }
            paragraph->start = {
                position.list,
                position.paragraph,
                position.character,
            };
        } else {
            paragraph->start = {0, 0, 0};
        }
        cursor_ = paragraph->start;
        CComVariant raw;
        bool started = false;
        const HRESULT invokeStatus = Method(
            hwp_,
            L"InitScan",
            {
                CComVariant(option_),
                CComVariant(range_),
                CComVariant(0L),
                CComVariant(0L),
                CComVariant(0L),
                CComVariant(0L),
            },
            &raw);
        if (SUCCEEDED(invokeStatus)) {
            scanMayBeOpen_ = true;
        }
        if (FAILED(invokeStatus) || FAILED(AsBool(raw, &started)) || !started) {
            return ReadStatus::Failure;
        }
        begun_ = true;
        return ReadStatus::Value;
    }

    ReadStatus Next(NativeTextAtomRecord* const atom) noexcept override {
        if (atom == nullptr || !begun_ || finished_) {
            return ReadStatus::Failure;
        }
        BSTR text = nullptr;
        CComVariant textArgument;
        textArgument.vt = VT_BSTR | VT_BYREF;
        textArgument.pbstrVal = &text;
        CComVariant rawState;
        const HRESULT invokeStatus =
            Method(hwp_, L"GetText", {textArgument}, &rawState);
        LONG state = 0;
        const HRESULT conversionStatus = AsLong(rawState, &state);
        std::wstring value;
        if (text != nullptr) {
            try {
                value.assign(text, SysStringLen(text));
            } catch (const std::bad_alloc&) {
                SysFreeString(text);
                return ReadStatus::Failure;
            }
            SysFreeString(text);
        }
        if (FAILED(invokeStatus) || FAILED(conversionStatus) ||
            state < 0 || state >= 101) {
            return ReadStatus::Failure;
        }
        if (state <= 1) {
            return state == 0
                ? ReadStatus::EndNoText
                : ReadStatus::EndList;
        }
        if (state > 5) {
            return ReadStatus::Failure;
        }
        *atom = {};
        atom->kind = AtomKindForState(state);
        atom->nativeState = state;
        atom->text = std::move(value);
        atom->shape.complete = false;
        atom->start = cursor_;
        atom->end = cursor_;
        if (atom->kind == AtomKind::EditableText ||
            atom->kind == AtomKind::ParagraphBoundary) {
            atom->end.character +=
                static_cast<std::int64_t>(atom->text.size());
            cursor_ = atom->end;
            if (atom->kind == AtomKind::ParagraphBoundary) {
                ++cursor_.paragraph;
                cursor_.character = 0;
            }
        }
        // InitScan state order is the native authority for these offsets;
        // no sampled caret CharacterShape is projected onto the run.
        atom->positionComplete = true;
        return ReadStatus::Value;
    }

    ScanDisposition Finish() noexcept override {
        if (finished_) {
            return finishDisposition_;
        }
        finished_ = true;
        if (!scanMayBeOpen_) {
            finishDisposition_ = ScanDisposition::NotAcquired;
            return finishDisposition_;
        }
        finishDisposition_ =
            SUCCEEDED(Method(hwp_, L"ReleaseScan", {}, nullptr))
            ? ScanDisposition::Released
            : ScanDisposition::ReleaseFailed;
        return finishDisposition_;
    }

private:
    CComPtr<IDispatch> hwp_{};
    LONG option_ = 0;
    LONG range_ = 0;
    bool documentStart_ = false;
    bool scanMayBeOpen_ = false;
    bool begun_ = false;
    bool finished_ = false;
    ScanDisposition finishDisposition_ = ScanDisposition::NotAcquired;
    NativeTextPosition cursor_{};
};

} // namespace

CaptureStatus CaptureNativeTextSource(
    NativeTextSource& source,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* const diagnostics,
    const CaptureLimits limits) noexcept {
    if (diagnostics != nullptr) {
        *diagnostics = {};
    }
    bool finished = false;
    try {
        NativeParagraphRecord paragraph;
        if (source.Begin(&paragraph) != ReadStatus::Value) {
            return Fail(
                source,
                sink,
                diagnostics,
                CaptureStatus::ReadFailed,
                &finished);
        }
        if (!sink.BeginParagraph(paragraph)) {
            return Fail(
                source,
                sink,
                diagnostics,
                CaptureStatus::SinkFailed,
                &finished);
        }

        NativeTextRunRecord run;
        bool hasRun = false;
        std::uint64_t paragraphOrdinal = 0;
        std::uint64_t controlDepth = 0;
        std::uint64_t atomCount = 0;
        std::uint64_t totalTextCodeUnits = 0;
        for (;;) {
            NativeTextAtomRecord atom;
            const ReadStatus readStatus = source.Next(&atom);
            if (readStatus == ReadStatus::EndNoText ||
                readStatus == ReadStatus::EndList) {
                break;
            }
            if (readStatus == ReadStatus::Failure) {
                return Fail(
                    source,
                    sink,
                    diagnostics,
                    CaptureStatus::ReadFailed,
                    &finished);
            }
            if (readStatus != ReadStatus::Value || atom.nativeState < 2 ||
                atom.nativeState > 5 ||
                atom.kind != AtomKindForState(atom.nativeState)) {
                return Fail(
                    source,
                    sink,
                    diagnostics,
                    CaptureStatus::InvalidNativeState,
                    &finished);
            }
            if (atomCount >= limits.maximumAtoms ||
                atom.text.size() >
                    limits.maximumTextCodeUnits - totalTextCodeUnits) {
                return Fail(
                    source,
                    sink,
                    diagnostics,
                    CaptureStatus::BudgetExceeded,
                    &finished);
            }
            ++atomCount;
            totalTextCodeUnits += atom.text.size();
            if (atom.kind == AtomKind::EnterControl) {
                ++controlDepth;
            } else if (atom.kind == AtomKind::ExitControl) {
                if (controlDepth == 0) {
                    return Fail(
                        source,
                        sink,
                        diagnostics,
                        CaptureStatus::InvalidNativeState,
                        &finished);
                }
                --controlDepth;
            }
            if (!sink.AppendAtom(atom)) {
                return Fail(
                    source,
                    sink,
                    diagnostics,
                    CaptureStatus::SinkFailed,
                    &finished);
            }
            if (diagnostics != nullptr) {
                ++diagnostics->atomCount;
                if (atom.kind == AtomKind::ParagraphBoundary) {
                    ++diagnostics->paragraphBoundaryCount;
                } else if (
                    atom.kind == AtomKind::EnterControl ||
                    atom.kind == AtomKind::ExitControl) {
                    ++diagnostics->controlBoundaryCount;
                }
            }
            const bool editablePayload =
                atom.kind == AtomKind::EditableText ||
                (atom.kind == AtomKind::ParagraphBoundary &&
                 !atom.text.empty());
            if (!editablePayload) {
                if (!FlushRun(&run, &hasRun, sink, diagnostics)) {
                    return Fail(
                        source,
                        sink,
                        diagnostics,
                        CaptureStatus::SinkFailed,
                        &finished);
                }
                continue;
            }
            const bool startsParagraphBoundaryRun =
                atom.kind == AtomKind::ParagraphBoundary &&
                !atom.text.empty() && hasRun && !run.text.empty();
            if (startsParagraphBoundaryRun &&
                !FlushRun(&run, &hasRun, sink, diagnostics)) {
                return Fail(
                    source,
                    sink,
                    diagnostics,
                    CaptureStatus::SinkFailed,
                    &finished);
            }
            if (!hasRun) {
                run.start = atom.start;
                run.end = atom.end;
                run.positionComplete = atom.positionComplete;
                run.shape.complete = false;
                hasRun = true;
            } else {
                run.end = atom.end;
                run.positionComplete =
                    run.positionComplete && atom.positionComplete;
            }
            run.text.append(atom.text);
            if (atom.kind == AtomKind::ParagraphBoundary &&
                !FlushRun(&run, &hasRun, sink, diagnostics)) {
                return Fail(
                    source,
                    sink,
                    diagnostics,
                    CaptureStatus::SinkFailed,
                    &finished);
            }
            if (atom.kind == AtomKind::ParagraphBoundary) {
                if (!MarkCoverage(
                        paragraphOrdinal,
                        sink,
                        diagnostics)) {
                    return Fail(
                        source,
                        sink,
                        diagnostics,
                        CaptureStatus::SinkFailed,
                        &finished);
                }
                ++paragraphOrdinal;
            }
        }
        if (controlDepth != 0) {
            return Fail(
                source,
                sink,
                diagnostics,
                CaptureStatus::InvalidNativeState,
                &finished);
        }
        if (!FlushRun(&run, &hasRun, sink, diagnostics)) {
            return Fail(
                source,
                sink,
                diagnostics,
                CaptureStatus::SinkFailed,
                &finished);
        }
        if (!MarkCoverage(paragraphOrdinal, sink, diagnostics)) {
            return Fail(
                source,
                sink,
                diagnostics,
                CaptureStatus::SinkFailed,
                &finished);
        }
        finished = true;
        const ScanDisposition disposition = source.Finish();
        if (diagnostics != nullptr) {
            diagnostics->scanDisposition = disposition;
        }
        if (disposition == ScanDisposition::ReleaseFailed) {
            sink.Abort();
            SetDiagnostics(diagnostics, CaptureStatus::ReadFailed);
            return CaptureStatus::ReadFailed;
        }
        if (!sink.Commit()) {
            sink.Abort();
            SetDiagnostics(diagnostics, CaptureStatus::SinkFailed);
            return CaptureStatus::SinkFailed;
        }
        SetDiagnostics(diagnostics, CaptureStatus::Complete);
        return CaptureStatus::Complete;
    } catch (const std::bad_alloc&) {
        return Fail(
            source,
            sink,
            diagnostics,
            CaptureStatus::ResourceExhausted,
            &finished);
    } catch (...) {
        return Fail(
            source,
            sink,
            diagnostics,
            CaptureStatus::ReadFailed,
            &finished);
    }
}

CaptureStatus CaptureNativeCurrentParagraphText(
    IDispatch* const hwp,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (hwp == nullptr) {
        if (diagnostics != nullptr) {
            *diagnostics = {};
        }
        SetDiagnostics(diagnostics, CaptureStatus::InvalidArgument);
        return CaptureStatus::InvalidArgument;
    }
    ComTextSource source(hwp, 7L, 0x0033L, false);
    return CaptureNativeTextSource(source, sink, diagnostics);
}

CaptureStatus CaptureNativeBodyText(
    IDispatch* const hwp,
    DocumentGraphTextSink& sink,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (hwp == nullptr) {
        if (diagnostics != nullptr) {
            *diagnostics = {};
        }
        SetDiagnostics(diagnostics, CaptureStatus::InvalidArgument);
        return CaptureStatus::InvalidArgument;
    }
    ComTextSource source(hwp, 0L, 0x0077L, true);
    return CaptureNativeTextSource(source, sink, diagnostics);
}

} // namespace hancom::graph::text

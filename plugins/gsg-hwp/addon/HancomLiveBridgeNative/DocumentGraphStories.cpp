#include "DocumentGraphStories.h"

#include "ComState.h"
#include "DispatchInvoke.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <limits>
#include <new>
#include <set>
#include <utility>
#include <vector>

namespace hancom::graph::stories {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

void SetDiagnostics(
    CaptureDiagnostics* const diagnostics,
    const CaptureStatus status,
    const std::uint64_t ordinal,
    const bool childNotExposed) noexcept {
    if (diagnostics != nullptr) {
        diagnostics->status = status;
        diagnostics->failingOrdinal = ordinal;
        diagnostics->exactChildContainmentNotExposed = childNotExposed;
    }
}

CaptureStatus Fail(
    DocumentGraphStorySink& sink,
    CaptureDiagnostics* const diagnostics,
    const CaptureStatus status,
    const std::uint64_t ordinal) noexcept {
    sink.Abort();
    SetDiagnostics(diagnostics, status, ordinal, false);
    return status;
}

bool ReadItemLong(
    IDispatch* const set,
    const wchar_t* const name,
    LONG* const value) noexcept {
    CComVariant raw;
    return SUCCEEDED(Method(set, L"Item", {CComVariant(name)}, &raw)) &&
        SUCCEEDED(AsLong(raw, value));
}

bool ReadAnchor(
    IDispatch* const control,
    NativeAnchor* const anchor) noexcept {
    CComVariant raw;
    CComPtr<IDispatch> set;
    return anchor != nullptr &&
        SUCCEEDED(Method(control, L"GetAnchorPos", {CComVariant(0L)}, &raw)) &&
        SUCCEEDED(AsDispatch(raw, set)) &&
        ReadItemLong(set, L"List", &anchor->list) &&
        ReadItemLong(set, L"Para", &anchor->paragraph) &&
        ReadItemLong(set, L"Pos", &anchor->character);
}

bool IsNullDispatch(const CComVariant& value) noexcept {
    return value.vt == VT_EMPTY || value.vt == VT_NULL ||
        (value.vt == VT_DISPATCH && value.pdispVal == nullptr) ||
        (value.vt == VT_UNKNOWN && value.punkVal == nullptr);
}

class ComStorySource final : public NativeStorySource {
public:
    explicit ComStorySource(IDispatch* const hwp) noexcept : hwp_(hwp) {}

    bool IsDocumentRootContext() noexcept override {
        hancom::com_state::Position position;
        const HRESULT status =
            hancom::com_state::CapturePosition(hwp_, &position);
        bodyList_ = FAILED(status)
            ? ObservationV1<std::int64_t>{
                  ObservationState::ReadFailed, false,
                  static_cast<std::int32_t>(status), {}, 0}
            : ObservationV1<std::int64_t>{
                  ObservationState::Value, true, 0, {}, position.list};
        return SUCCEEDED(status) && position.list == 0;
    }

    ObservationV1<std::int64_t> ObserveBodyList() noexcept override {
        return bodyList_;
    }

    ReadStatus Head(NativeStoryCursor* const cursor) noexcept override {
        CComVariant raw;
        return FAILED(PropertyGet(hwp_, L"HeadCtrl", &raw))
            ? ReadStatus::Failure
            : Register(raw, cursor);
    }

    ReadStatus Next(
        const NativeStoryCursor& current,
        NativeStoryCursor* const next) noexcept override {
        IDispatch* const control = Resolve(current);
        if (control == nullptr) {
            return ReadStatus::Failure;
        }
        CComVariant raw;
        return FAILED(PropertyGet(control, L"Next", &raw))
            ? ReadStatus::Failure
            : Register(raw, next);
    }

    bool Read(
        const NativeStoryCursor& current,
        NativeControlRecord* const record) noexcept override {
        IDispatch* const control = Resolve(current);
        if (control == nullptr || record == nullptr) {
            return false;
        }
        CComVariant rawId;
        CComVariant rawCh;
        CComVariant rawHasList;
        CComVariant rawInstance;
        bool hasList = false;
        const bool instanceIdPresent =
            SUCCEEDED(Method(control, L"GetCtrlInstID", {}, &rawInstance)) &&
            SUCCEEDED(AsString(rawInstance, &record->instanceId));
        if (!instanceIdPresent) {
            record->instanceId.clear();
        }
        return SUCCEEDED(PropertyGet(control, L"CtrlID", &rawId)) &&
            SUCCEEDED(AsString(rawId, &record->ctrlId)) &&
            SUCCEEDED(PropertyGet(control, L"CtrlCh", &rawCh)) &&
            SUCCEEDED(AsLong(rawCh, &record->ctrlCh)) &&
            SUCCEEDED(PropertyGet(control, L"HasList", &rawHasList)) &&
            SUCCEEDED(AsBool(rawHasList, &hasList)) &&
            ReadAnchor(control, &record->anchor) &&
            AssignHasList(record, hasList, instanceIdPresent);
    }

private:
    bool AssignHasList(
        NativeControlRecord* const record,
        const bool hasList,
        const bool instanceIdPresent) const noexcept {
        record->hasList = hasList;
        record->instanceIdSessionOnly = true;
        record->instanceIdPresent = instanceIdPresent;
        return true;
    }

    IDispatch* Resolve(const NativeStoryCursor& cursor) const noexcept {
        return cursor.sourceIndex == 0 ||
                cursor.sourceIndex != currentSourceIndex_ ||
                cursor.transientIdentity !=
                    reinterpret_cast<std::uintptr_t>(currentIdentity_.p)
            ? nullptr
            : currentControl_.p;
    }

    ReadStatus Register(
        const CComVariant& raw,
        NativeStoryCursor* const cursor) noexcept {
        if (cursor == nullptr) {
            return ReadStatus::Failure;
        }
        if (IsNullDispatch(raw)) {
            currentControl_.Release();
            currentIdentity_.Release();
            currentSourceIndex_ = 0;
            *cursor = {};
            return ReadStatus::End;
        }
        CComPtr<IDispatch> control;
        CComPtr<IUnknown> identity;
        if (FAILED(AsDispatch(raw, control)) ||
            FAILED(control->QueryInterface(
                IID_IUnknown,
                reinterpret_cast<void**>(&identity))) ||
            identity == nullptr ||
            currentSourceIndex_ ==
                (std::numeric_limits<std::uint64_t>::max)()) {
            return ReadStatus::Failure;
        }
        try {
            identities_.push_back(identity);
        } catch (const std::bad_alloc&) {
            return ReadStatus::Failure;
        }
        currentControl_ = control;
        currentIdentity_ = identity;
        ++currentSourceIndex_;
        cursor->sourceIndex = currentSourceIndex_;
        cursor->transientIdentity =
            reinterpret_cast<std::uintptr_t>(identity.p);
        return ReadStatus::Value;
    }

    CComPtr<IDispatch> hwp_{};
    CComPtr<IDispatch> currentControl_{};
    CComPtr<IUnknown> currentIdentity_{};
    std::uint64_t currentSourceIndex_ = 0;
    ObservationV1<std::int64_t> bodyList_{};
    std::vector<CComPtr<IUnknown>> identities_{};
};

} // namespace

CaptureStatus CaptureNativeStorySource(
    NativeStorySource& source,
    DocumentGraphStorySink& sink,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (diagnostics != nullptr) {
        *diagnostics = {};
    }
    SetDiagnostics(diagnostics, CaptureStatus::InvalidArgument, 0, false);
    try {
        if (!source.IsDocumentRootContext()) {
            return Fail(
                sink,
                diagnostics,
                CaptureStatus::InconsistentNativeState,
                0);
        }
        if (!sink.BeginBodyStory(source.ObserveBodyList())) {
            return Fail(sink, diagnostics, CaptureStatus::SinkFailed, 0);
        }
        NativeStoryCursor cursor;
        ReadStatus status = source.Head(&cursor);
        if (status == ReadStatus::Failure) {
            return Fail(sink, diagnostics, CaptureStatus::ReadFailed, 0);
        }
        std::set<std::uintptr_t> seen;
        std::uint64_t ordinal = 0;
        std::uint64_t sectionCount = 0;
        std::uint64_t controlCount = 0;
        std::uint64_t childGapCount = 0;
        bool sectionStarted = false;
        while (status == ReadStatus::Value) {
            if (cursor.transientIdentity == 0 ||
                !seen.insert(cursor.transientIdentity).second) {
                return Fail(
                    sink,
                    diagnostics,
                    CaptureStatus::CorruptNativeGraph,
                    ordinal);
            }
            NativeControlRecord record;
            if (!source.Read(cursor, &record)) {
                return Fail(
                    sink, diagnostics, CaptureStatus::ReadFailed, ordinal);
            }
            record.headCtrlOrdinal = ordinal;
            if (record.ctrlId == L"secd") {
                NativeSectionRecord section;
                section.sectionOrdinal = sectionCount;
                section.definitionControlPresent = true;
                section.definitionControlOrdinal = ordinal;
                section.startAnchor = record.anchor;
                if (!sink.AppendSection(section)) {
                    return Fail(
                        sink,
                        diagnostics,
                        CaptureStatus::SinkFailed,
                        ordinal);
                }
                ++sectionCount;
                sectionStarted = true;
            } else if (!sectionStarted) {
                NativeSectionRecord section;
                section.sectionOrdinal = 0;
                section.startAnchor = {record.anchor.list, 0, 0};
                if (!sink.AppendSection(section)) {
                    return Fail(
                        sink,
                        diagnostics,
                        CaptureStatus::SinkFailed,
                        ordinal);
                }
                sectionCount = 1;
                sectionStarted = true;
            }
            if (!sink.AppendControl(record)) {
                return Fail(
                    sink, diagnostics, CaptureStatus::SinkFailed, ordinal);
            }
            ++controlCount;
            if (record.hasList) {
                if (!sink.MarkChildContainmentNotExposed(record)) {
                    return Fail(
                        sink,
                        diagnostics,
                        CaptureStatus::SinkFailed,
                        ordinal);
                }
                ++childGapCount;
            }
            if (diagnostics != nullptr) {
                diagnostics->sectionCount = sectionCount;
                diagnostics->controlCount = controlCount;
                diagnostics->childContainmentNotExposedCount = childGapCount;
                diagnostics->exactChildContainmentNotExposed =
                    childGapCount != 0;
            }
            NativeStoryCursor next;
            status = source.Next(cursor, &next);
            if (status == ReadStatus::Failure) {
                return Fail(
                    sink, diagnostics, CaptureStatus::ReadFailed, ordinal);
            }
            cursor = next;
            ++ordinal;
        }
        if (!sectionStarted) {
            NativeSectionRecord section;
            if (!sink.AppendSection(section)) {
                return Fail(
                    sink, diagnostics, CaptureStatus::SinkFailed, ordinal);
            }
            sectionCount = 1;
        }
        if (!sink.Commit()) {
            return Fail(
                sink, diagnostics, CaptureStatus::SinkFailed, ordinal);
        }
        if (diagnostics != nullptr) {
            diagnostics->sectionCount = sectionCount;
            diagnostics->controlCount = controlCount;
            diagnostics->childContainmentNotExposedCount = childGapCount;
        }
        SetDiagnostics(
            diagnostics,
            CaptureStatus::Complete,
            0,
            childGapCount != 0);
        return CaptureStatus::Complete;
    } catch (const std::bad_alloc&) {
        return Fail(
            sink, diagnostics, CaptureStatus::ResourceExhausted, 0);
    }
}

CaptureStatus CaptureNativeStructureStories(
    IDispatch* const hwp,
    DocumentGraphStorySink& sink,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (hwp == nullptr) {
        SetDiagnostics(
            diagnostics, CaptureStatus::InvalidArgument, 0, false);
        return CaptureStatus::InvalidArgument;
    }
    ComStorySource source(hwp);
    return CaptureNativeStorySource(source, sink, diagnostics);
}

} // namespace hancom::graph::stories

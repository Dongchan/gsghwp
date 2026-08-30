#include "../DocumentGraphStories.h"
#include "../DocumentGraphCaptureModel.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cwchar>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

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

namespace {

size_t nativeAnchorDispatchReadCount = 0;

using hancom::graph::stories::CaptureDiagnostics;
using hancom::graph::stories::CaptureNativeStorySource;
using hancom::graph::stories::CaptureStatus;
using hancom::graph::stories::DocumentGraphStorySink;
using hancom::graph::stories::NativeAnchor;
using hancom::graph::stories::NativeControlRecord;
using hancom::graph::stories::NativeSectionRecord;
using hancom::graph::stories::NativeStoryCursor;
using hancom::graph::stories::NativeStorySource;
using hancom::graph::stories::ReadStatus;

struct FakeNode final {
    NativeControlRecord record{};
    std::uint64_t next = 0;
    bool failRead = false;
    bool failNext = false;
};

class FakeSource final : public NativeStorySource {
public:
    bool IsDocumentRootContext() noexcept override {
        return rootContext;
    }

    ReadStatus Head(NativeStoryCursor* const cursor) noexcept override {
        return Resolve(head, cursor);
    }

    ReadStatus Next(
        const NativeStoryCursor& current,
        NativeStoryCursor* const next) noexcept override {
        if (current.sourceIndex == 0 || current.sourceIndex > nodes.size()) {
            return ReadStatus::Failure;
        }
        const FakeNode& node = nodes[static_cast<size_t>(current.sourceIndex - 1)];
        return node.failNext ? ReadStatus::Failure : Resolve(node.next, next);
    }

    bool Read(
        const NativeStoryCursor& current,
        NativeControlRecord* const record) noexcept override {
        if (record == nullptr || current.sourceIndex == 0 ||
            current.sourceIndex > nodes.size()) {
            return false;
        }
        const FakeNode& node = nodes[static_cast<size_t>(current.sourceIndex - 1)];
        if (node.failRead) {
            return false;
        }
        *record = node.record;
        return true;
    }

    std::vector<FakeNode> nodes{};
    std::uint64_t head = 0;
    bool rootContext = true;

private:
    ReadStatus Resolve(
        const std::uint64_t index,
        NativeStoryCursor* const cursor) const noexcept {
        if (cursor == nullptr) {
            return ReadStatus::Failure;
        }
        if (index == 0) {
            *cursor = {};
            return ReadStatus::End;
        }
        if (index > nodes.size()) {
            return ReadStatus::Failure;
        }
        cursor->sourceIndex = index;
        cursor->transientIdentity = static_cast<std::uintptr_t>(index);
        return ReadStatus::Value;
    }
};

class TransactionalSink final : public DocumentGraphStorySink {
public:
    bool BeginBodyStory() noexcept override {
        began = true;
        return !failBegin;
    }
    bool AppendSection(const NativeSectionRecord& record) noexcept override {
        if (failSection) {
            return false;
        }
        pendingSections.push_back(record);
        return true;
    }
    bool AppendControl(const NativeControlRecord& record) noexcept override {
        if (failAppend) {
            return false;
        }
        pending.push_back(record);
        return true;
    }
    bool MarkChildContainmentNotExposed(
        const NativeControlRecord& record) noexcept override {
        markedControls.push_back(record);
        return !failMark;
    }
    bool Commit() noexcept override {
        if (failCommit) {
            return false;
        }
        published = pending;
        publishedSections = pendingSections;
        publishedGaps = markedControls;
        committed = true;
        return true;
    }
    void Abort() noexcept override {
        aborted = true;
        pending.clear();
        pendingSections.clear();
        markedControls.clear();
        published.clear();
        publishedSections.clear();
        publishedGaps.clear();
    }

    bool failBegin = false;
    bool failSection = false;
    bool failAppend = false;
    bool failMark = false;
    bool failCommit = false;
    bool began = false;
    bool committed = false;
    bool aborted = false;
    std::vector<NativeControlRecord> pending{};
    std::vector<NativeSectionRecord> pendingSections{};
    std::vector<NativeControlRecord> markedControls{};
    std::vector<NativeControlRecord> published{};
    std::vector<NativeSectionRecord> publishedSections{};
    std::vector<NativeControlRecord> publishedGaps{};
};

class CountingSink final : public DocumentGraphStorySink {
public:
    bool BeginBodyStory() noexcept override {
        began = true;
        return true;
    }
    bool AppendSection(const NativeSectionRecord&) noexcept override {
        ++sectionCount;
        return true;
    }
    bool AppendControl(const NativeControlRecord&) noexcept override {
        ++pendingCount;
        return true;
    }
    bool MarkChildContainmentNotExposed(
        const NativeControlRecord&) noexcept override {
        ++gapCount;
        return true;
    }
    bool Commit() noexcept override {
        publishedCount = pendingCount;
        committed = true;
        return true;
    }
    void Abort() noexcept override {
        aborted = true;
        pendingCount = 0;
        publishedCount = 0;
    }

    bool began = false;
    bool committed = false;
    bool aborted = false;
    std::uint64_t pendingCount = 0;
    std::uint64_t publishedCount = 0;
    std::uint64_t sectionCount = 0;
    std::uint64_t gapCount = 0;
};

NativeControlRecord Record(
    const wchar_t* const id,
    const wchar_t* const instance,
    const LONG paragraph,
    const bool hasList = false) {
    NativeControlRecord record;
    record.ctrlId = id;
    record.instanceId = instance;
    record.ctrlCh = 11;
    record.hasList = hasList;
    record.anchor = {0, paragraph, 0};
    return record;
}

bool EmptyDocumentSmoke() {
    FakeSource source;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeStorySource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete &&
        diagnostics.status == CaptureStatus::Complete &&
        !diagnostics.exactChildContainmentNotExposed &&
        diagnostics.sectionCount == 1 && diagnostics.controlCount == 0 &&
        diagnostics.childContainmentNotExposedCount == 0 &&
        sink.began && sink.committed && !sink.aborted &&
        sink.published.empty() && sink.publishedSections.size() == 1 &&
        sink.publishedGaps.empty();
}

bool OrderedControlsSmoke() {
    FakeSource source;
    source.nodes = {
        {Record(L"gso", L"", 10, true), 2, false, false},
        {Record(L"gso", L"", 10), 0, false, false},
    };
    source.head = 1;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeStorySource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete && sink.committed &&
        sink.published.size() == 2 &&
        sink.published[0].headCtrlOrdinal == 0 &&
        sink.published[1].headCtrlOrdinal == 1 &&
        sink.published[0].instanceId.empty() &&
        sink.published[1].instanceId.empty() &&
        sink.publishedSections.size() == 1 &&
        sink.publishedGaps.size() == 1 &&
        sink.publishedGaps[0].headCtrlOrdinal == 0;
}

bool CycleRejectsPartialPublishSmoke() {
    FakeSource source;
    source.nodes = {
        {Record(L"a", L"one", 1), 2, false, false},
        {Record(L"b", L"two", 2), 3, false, false},
        {Record(L"c", L"three", 3), 2, false, false},
    };
    source.head = 1;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeStorySource(source, sink, &diagnostics);
    return status == CaptureStatus::CorruptNativeGraph && sink.aborted &&
        !sink.committed && sink.published.empty();
}

bool FailedNextRejectsPartialPublishSmoke() {
    FakeSource source;
    source.nodes = {{Record(L"a", L"one", 1), 0, false, true}};
    source.head = 1;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeStorySource(source, sink, &diagnostics);
    return status == CaptureStatus::ReadFailed && sink.aborted &&
        !sink.committed && sink.published.empty();
}

bool DuplicateIdentityAndEqualAnchorSmoke() {
    FakeSource source;
    source.nodes = {
        {Record(L"unknown-native-control", L"duplicate", 7), 2, false, false},
        {Record(L"another-unknown-control", L"duplicate", 7), 0, false, false},
    };
    source.head = 1;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeStorySource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete && sink.committed &&
        sink.published.size() == 2 &&
        sink.published[0].instanceId == L"duplicate" &&
        sink.published[1].instanceId == L"duplicate" &&
        sink.published[0].anchor.paragraph ==
            sink.published[1].anchor.paragraph &&
        sink.published[0].headCtrlOrdinal == 0 &&
        sink.published[1].headCtrlOrdinal == 1;
}

bool SinkFailureRejectsPartialPublishSmoke() {
    FakeSource source;
    source.nodes = {{Record(L"gso", L"one", 1), 0, false, false}};
    source.head = 1;

    TransactionalSink appendFailure;
    appendFailure.failAppend = true;
    CaptureDiagnostics appendDiagnostics;
    const CaptureStatus appendStatus =
        CaptureNativeStorySource(source, appendFailure, &appendDiagnostics);

    TransactionalSink commitFailure;
    commitFailure.failCommit = true;
    CaptureDiagnostics commitDiagnostics;
    const CaptureStatus commitStatus =
        CaptureNativeStorySource(source, commitFailure, &commitDiagnostics);

    return appendStatus == CaptureStatus::SinkFailed &&
        appendFailure.aborted && appendFailure.published.empty() &&
        commitStatus == CaptureStatus::SinkFailed &&
        commitFailure.aborted && commitFailure.published.empty();
}

bool LongChainStreamsWithoutCountStopSmoke() {
    constexpr std::uint64_t kControlCount = 100'001;
    FakeSource source;
    source.nodes.reserve(static_cast<size_t>(kControlCount));
    for (std::uint64_t index = 0; index < kControlCount; ++index) {
        const std::uint64_t next =
            index + 1 < kControlCount ? index + 2 : 0;
        source.nodes.push_back(
            {Record(L"unknown", L"", static_cast<LONG>(index)), next, false, false});
    }
    source.head = 1;
    CountingSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeStorySource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete && sink.began && sink.committed &&
        !sink.aborted && sink.sectionCount == 1 && sink.gapCount == 0 &&
        sink.publishedCount == kControlCount;
}

bool MultipleSectionsAndPerControlCoverageSmoke() {
    FakeSource source;
    source.nodes = {
        {Record(L"secd", L"", 0), 2, false, false},
        {Record(L"head", L"header", 3, true), 3, false, false},
        {Record(L"secd", L"", 20), 4, false, false},
        {Record(L"gso", L"text-box", 25, true), 0, false, false},
    };
    source.head = 1;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeStorySource(source, sink, &diagnostics);
    return status == CaptureStatus::Complete &&
        diagnostics.sectionCount == 2 && diagnostics.controlCount == 4 &&
        diagnostics.childContainmentNotExposedCount == 2 &&
        diagnostics.exactChildContainmentNotExposed &&
        sink.publishedSections.size() == 2 &&
        sink.publishedSections[0].sectionOrdinal == 0 &&
        sink.publishedSections[0].definitionControlOrdinal == 0 &&
        sink.publishedSections[1].sectionOrdinal == 1 &&
        sink.publishedSections[1].definitionControlOrdinal == 2 &&
        sink.publishedGaps.size() == 2 &&
        sink.publishedGaps[0].headCtrlOrdinal == 1 &&
        sink.publishedGaps[1].headCtrlOrdinal == 3;
}

bool NonRootContextFailsClosedSmoke() {
    FakeSource source;
    source.rootContext = false;
    source.nodes = {{Record(L"secd", L"", 0), 0, false, false}};
    source.head = 1;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        CaptureNativeStorySource(source, sink, &diagnostics);
    return status == CaptureStatus::InconsistentNativeState &&
        diagnostics.status == CaptureStatus::InconsistentNativeState &&
        sink.aborted && !sink.began && !sink.committed &&
        sink.published.empty() && sink.publishedSections.empty();
}

class NativeAnchorDispatch final : public IDispatch {
public:
    enum class Role : std::uint8_t { Root, Control, Position };

    NativeAnchorDispatch(
        const Role role,
        const NativeAnchorCase anchorCase,
        const wchar_t* const ctrlId,
        const NativeAnchor anchor = {0, 7, 3}) noexcept
        : role_(role), anchorCase_(anchorCase), ctrlId_(ctrlId),
          anchor_(anchor) {}

    NativeAnchorDispatch(
        const NativeAnchorCase anchorCase,
        const wchar_t* const targetCtrlId,
        const std::vector<hancom::graph::capture::SectionObservation>& sections,
        const std::vector<hancom::graph::capture::ControlObservation>& controls)
        : role_(Role::Root), anchorCase_(anchorCase),
          ctrlId_(targetCtrlId), model_(std::make_shared<Model>()) {
        model_->controls = controls;
        for (size_t sectionIndex = 1;
             sectionIndex < sections.size(); ++sectionIndex) {
            const auto& section = sections[sectionIndex];
            auto following = std::min_element(
                controls.begin(), controls.end(),
                [&section](const auto& left, const auto& right) {
                    const auto follows = [&section](const auto& control) {
                        return std::tie(
                                   control.anchor.list,
                                   control.anchor.paragraph,
                                   control.anchor.character) >=
                            std::tie(
                                   section.start.list,
                                   section.start.paragraph,
                                   section.start.character);
                    };
                    if (follows(left) != follows(right)) return follows(left);
                    return left.headCtrlOrdinal < right.headCtrlOrdinal;
                });
            hancom::graph::capture::ControlObservation definition;
            definition.ctrlId = L"secd";
            definition.instanceId =
                L"native-section-" + std::to_wstring(sectionIndex);
            definition.headCtrlOrdinal = following != controls.end() &&
                    following->headCtrlOrdinal != 0
                ? following->headCtrlOrdinal - 1
                : static_cast<std::uint64_t>(model_->controls.size());
            definition.anchor = section.start;
            model_->controls.push_back(std::move(definition));
        }
        std::sort(
            model_->controls.begin(), model_->controls.end(),
            [](const auto& left, const auto& right) {
                return left.headCtrlOrdinal < right.headCtrlOrdinal;
            });
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid, void** const object) override {
        if (object == nullptr) return E_POINTER;
        if (iid == IID_IUnknown || iid == IID_IDispatch) {
            *object = static_cast<IDispatch*>(this);
            static_cast<void>(AddRef());
            return S_OK;
        }
        *object = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }
    ULONG STDMETHODCALLTYPE Release() override {
        const LONG remaining = InterlockedDecrement(&references_);
        if (remaining == 0) delete this;
        return static_cast<ULONG>(remaining);
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* const count) override {
        if (count == nullptr) return E_POINTER;
        *count = 0;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT, LCID, ITypeInfo**) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID, LPOLESTR* const names, const UINT count, LCID,
        DISPID* const members) override {
        if (names == nullptr || members == nullptr || count != 1)
            return E_INVALIDARG;
        const std::wstring name(names[0]);
        if (name == L"GetPos") members[0] = 1;
        else if (name == L"HeadCtrl") members[0] = 2;
        else if (name == L"CtrlID") members[0] = 3;
        else if (name == L"CtrlCh") members[0] = 4;
        else if (name == L"HasList") members[0] = 5;
        else if (name == L"GetCtrlInstID") members[0] = 6;
        else if (name == L"GetAnchorPos") members[0] = 7;
        else if (name == L"Next") members[0] = 8;
        else if (name == L"Item") members[0] = 9;
        else return DISP_E_UNKNOWNNAME;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Invoke(
        const DISPID member, REFIID, LCID, WORD,
        DISPPARAMS* const parameters, VARIANT* const result,
        EXCEPINFO*, UINT*) override {
        if (result != nullptr) VariantInit(result);
        if (role_ == Role::Root && member == 1 && parameters != nullptr &&
            parameters->cArgs == 3) {
            ++nativeAnchorDispatchReadCount;
            for (UINT index = 0; index < 3; ++index) {
                VARIANT& value = parameters->rgvarg[index];
                if ((value.vt & (VT_BYREF | VT_I4)) != (VT_BYREF | VT_I4) ||
                    value.plVal == nullptr) return DISP_E_TYPEMISMATCH;
                *value.plVal = 0;
            }
            return S_OK;
        }
        if (role_ == Role::Root && member == 2 && result != nullptr) {
            result->vt = VT_DISPATCH;
            result->pdispVal = model_ == nullptr || !model_->controls.empty()
                ? new NativeAnchorDispatch(
                      Role::Control, anchorCase_, ctrlId_, anchor_, model_, 0)
                : nullptr;
            return S_OK;
        }
        if (role_ == Role::Control && member >= 3 && member <= 5 &&
            result != nullptr) {
            const auto* const control = CurrentControl();
            const std::wstring& id = control != nullptr
                ? control->ctrlId : ctrlId_;
            if (member == 3) {
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(id.c_str());
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (member == 4) {
                result->vt = VT_I4;
                result->lVal = 11;
            } else {
                result->vt = VT_BOOL;
                result->boolVal = control != nullptr && control->hasList
                    ? VARIANT_TRUE : VARIANT_FALSE;
            }
            return S_OK;
        }
        if (role_ == Role::Control && member == 6 && result != nullptr) {
            const auto* const control = CurrentControl();
            const wchar_t* const instance = control != nullptr
                ? control->instanceId.c_str() : L"native-anchor-control";
            result->vt = VT_BSTR;
            result->bstrVal = SysAllocString(instance);
            return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
        }
        if (role_ == Role::Control && member == 7 && result != nullptr) {
            if (IsTarget() &&
                anchorCase_ == NativeAnchorCase::DispatchFailure) return E_FAIL;
            if (parameters == nullptr || parameters->cArgs != 1 ||
                parameters->rgvarg[0].vt != VT_I4 ||
                parameters->rgvarg[0].lVal != 0) return DISP_E_TYPEMISMATCH;
            const auto* const control = CurrentControl();
            const NativeAnchor observed = control != nullptr
                ? NativeAnchor{
                      static_cast<LONG>(control->anchor.list),
                      static_cast<LONG>(control->anchor.paragraph),
                      static_cast<LONG>(control->anchor.character)}
                : anchor_;
            result->vt = VT_DISPATCH;
            result->pdispVal = new NativeAnchorDispatch(
                Role::Position, IsTarget() ? anchorCase_
                                          : NativeAnchorCase::Available,
                ctrlId_, observed, model_, index_);
            return S_OK;
        }
        if (role_ == Role::Control && member == 8 && result != nullptr) {
            result->vt = VT_DISPATCH;
            result->pdispVal = model_ != nullptr &&
                    index_ + 1 < model_->controls.size()
                ? new NativeAnchorDispatch(
                      Role::Control, anchorCase_, ctrlId_, {}, model_,
                      index_ + 1)
                : nullptr;
            return S_OK;
        }
        if (role_ == Role::Position && member == 9 && result != nullptr &&
            parameters != nullptr && parameters->cArgs == 1 &&
            parameters->rgvarg[0].vt == VT_BSTR) {
            const std::wstring item(parameters->rgvarg[0].bstrVal);
            const bool list = item == L"List";
            const bool para = item == L"Para";
            const bool pos = item == L"Pos";
            const bool missing =
                (list && anchorCase_ == NativeAnchorCase::MissingList) ||
                (para && anchorCase_ == NativeAnchorCase::MissingPara) ||
                (pos && anchorCase_ == NativeAnchorCase::MissingPos);
            if (!list && !para && !pos || missing) return DISP_E_MEMBERNOTFOUND;
            const bool invalid =
                (list && anchorCase_ == NativeAnchorCase::InvalidList) ||
                (para && anchorCase_ == NativeAnchorCase::InvalidPara) ||
                (pos && anchorCase_ == NativeAnchorCase::InvalidPos);
            if (invalid) {
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"invalid");
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            result->vt = VT_I4;
            result->lVal = list ? anchor_.list
                : para ? anchor_.paragraph : anchor_.character;
            return S_OK;
        }
        return DISP_E_MEMBERNOTFOUND;
    }

private:
    struct Model final {
        std::vector<hancom::graph::capture::ControlObservation> controls{};
    };

    NativeAnchorDispatch(
        const Role role,
        const NativeAnchorCase anchorCase,
        const std::wstring& ctrlId,
        const NativeAnchor anchor,
        std::shared_ptr<Model> model,
        const size_t index) noexcept
        : role_(role), anchorCase_(anchorCase), ctrlId_(ctrlId),
          anchor_(anchor), model_(std::move(model)), index_(index) {}

    const hancom::graph::capture::ControlObservation* CurrentControl()
        const noexcept {
        return model_ != nullptr && index_ < model_->controls.size()
            ? &model_->controls[index_] : nullptr;
    }

    bool IsTarget() const noexcept {
        const auto* const control = CurrentControl();
        return control == nullptr || control->ctrlId == ctrlId_;
    }

    ~NativeAnchorDispatch() = default;
    volatile LONG references_ = 1;
    Role role_;
    NativeAnchorCase anchorCase_;
    std::wstring ctrlId_;
    NativeAnchor anchor_{};
    std::shared_ptr<Model> model_{};
    size_t index_ = 0;
};

bool RunDirectNativeAnchorCase(
    const NativeAnchorCase anchorCase,
    const wchar_t* const ctrlId,
    const bool expectAvailable) {
    CComPtr<IDispatch> root;
    root.Attach(new NativeAnchorDispatch(
        NativeAnchorDispatch::Role::Root, anchorCase, ctrlId));
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        hancom::graph::stories::CaptureNativeStructureStories(
            root, sink, &diagnostics);
    if (expectAvailable) {
        return status == CaptureStatus::Complete &&
            diagnostics.status == CaptureStatus::Complete &&
            sink.committed && !sink.aborted && sink.published.size() == 1 &&
            sink.published[0].ctrlId == ctrlId &&
            sink.published[0].anchor.list == 0 &&
            sink.published[0].anchor.paragraph == 7 &&
            sink.published[0].anchor.character == 3;
    }
    return status == CaptureStatus::ReadFailed &&
        diagnostics.status == CaptureStatus::ReadFailed &&
        sink.aborted && !sink.committed && sink.pending.empty() &&
        sink.published.empty() && sink.publishedSections.empty();
}

} // namespace

size_t NativeAnchorDispatchReadCount() noexcept {
    return nativeAnchorDispatchReadCount;
}

IDispatch* CreateNativeAnchorDispatch(
    const NativeAnchorCase anchorCase,
    const wchar_t* ctrlId,
    const std::vector<hancom::graph::capture::SectionObservation>& sections,
    const std::vector<hancom::graph::capture::ControlObservation>& controls) {
    return new NativeAnchorDispatch(anchorCase, ctrlId, sections, controls);
}

bool DirectNativeApplicableAnchorFailClosedMatrixSmoke() {
    const std::array<NativeAnchorCase, 7> unavailable{
        NativeAnchorCase::DispatchFailure,
        NativeAnchorCase::MissingList,
        NativeAnchorCase::InvalidList,
        NativeAnchorCase::MissingPara,
        NativeAnchorCase::InvalidPara,
        NativeAnchorCase::MissingPos,
        NativeAnchorCase::InvalidPos,
    };
    bool failClosed = RunDirectNativeAnchorCase(
        NativeAnchorCase::Available, L"", true);
    for (const wchar_t* const family : {L"gso", L"tbl", L"$pic"}) {
        failClosed = failClosed &&
            RunDirectNativeAnchorCase(
                NativeAnchorCase::Available, family, true);
        for (const NativeAnchorCase anchorCase : unavailable) {
            failClosed = failClosed &&
                RunDirectNativeAnchorCase(anchorCase, family, false);
        }
    }
    std::wcout << L"GRAPH_CAPTURE_DIRECT_NATIVE_GETANCHORPOS_FAIL_CLOSED_MATRIX "
               << failClosed << L'\n';
    return failClosed;
}

bool DocumentGraphStoriesSmoke() {
    const bool directNativeAnchor =
        DirectNativeApplicableAnchorFailClosedMatrixSmoke();
    const bool empty = EmptyDocumentSmoke();
    const bool ordered = OrderedControlsSmoke();
    const bool cycle = CycleRejectsPartialPublishSmoke();
    const bool failedNext = FailedNextRejectsPartialPublishSmoke();
    const bool duplicateIdentity = DuplicateIdentityAndEqualAnchorSmoke();
    const bool sinkFailure = SinkFailureRejectsPartialPublishSmoke();
    const bool longChain = LongChainStreamsWithoutCountStopSmoke();
    const bool multipleSections = MultipleSectionsAndPerControlCoverageSmoke();
    const bool nonRoot = NonRootContextFailsClosedSmoke();
    std::wcout << L"STORIES_DIRECT_NATIVE_GETANCHORPOS_FAIL_CLOSED "
               << directNativeAnchor << L'\n'
               << L"STORIES_EMPTY " << empty << L'\n'
               << L"STORIES_ORDERED " << ordered << L'\n'
               << L"STORIES_CYCLE_FAIL_CLOSED " << cycle << L'\n'
               << L"STORIES_NEXT_FAILURE_FAIL_CLOSED " << failedNext << L'\n'
               << L"STORIES_DUPLICATE_INSTANCE_EQUAL_ANCHOR "
               << duplicateIdentity << L'\n'
               << L"STORIES_SINK_FAILURE_FAIL_CLOSED " << sinkFailure << L'\n'
               << L"STORIES_LONG_CHAIN_STREAMING " << longChain << L'\n'
               << L"STORIES_MULTI_SECTION_PER_CONTROL_COVERAGE "
               << multipleSections << L'\n'
               << L"STORIES_NON_ROOT_FAIL_CLOSED " << nonRoot << L'\n';
    return directNativeAnchor && empty && ordered && cycle && failedNext && duplicateIdentity &&
        sinkFailure && longChain && multipleSections && nonRoot;
}

#pragma once

#include "DocumentGraphCapture.h"
#include "DocumentGraphCaptureModel.h"
#include "DocumentGraphIdentity.h"
#include "DispatchInvoke.h"

#include <Windows.h>
#include <atlcomcli.h>

#include <string>
#include <vector>

namespace hancom::graph::layout {
struct LayoutEnvironmentPlatformV1;
}

namespace hancom::official_api::capability {

enum class SemanticStatus {
    Pass,
    TypeInfoFailure,
    NameFailure,
    ContractMismatch,
    ComFailure,
    MalformedOutput,
    FalseResult,
    NullResult,
    NoProgress,
    RepeatedState,
    Inconclusive,
    NotObserved,
};

struct CallSpec {
    const wchar_t* name;
    DISPID dispid;
    WORD invkind;
    SHORT arity;
    VARTYPE resultType;
};

struct ExceptionObservation {
    WORD code = 0;
    WORD reserved = 0;
    SCODE scode = 0;
    DWORD helpContext = 0;
    bool reservedPointerPresent = false;
    bool deferredFillPresent = false;
    HRESULT deferredFillStatus = S_FALSE;
    bool sourcePresent = false;
    bool descriptionPresent = false;
    bool helpFilePresent = false;
    std::wstring source;
    std::wstring description;
    std::wstring helpFile;
};

struct ByRefObservation {
    VARTYPE declaredType = VT_EMPTY;
    bool pointerNull = true;
    CComVariant value;
};

struct RawCall {
    SemanticStatus semantic = SemanticStatus::ContractMismatch;
    HRESULT typeInfoStatus = E_UNEXPECTED;
    HRESULT getIdsStatus = E_UNEXPECTED;
    HRESULT invokeStatus = E_UNEXPECTED;
    UINT argumentError = UINT_MAX;
    CComVariant result;
    std::vector<ByRefObservation> byRefOutputs;
    ExceptionObservation exception;
};

RawCall InvokeOneShot(
    IDispatch* target,
    const CallSpec& spec,
    const std::vector<VARIANTARG>& arguments,
    bool validateTypeInfo,
    const dispatch::TypeIdentityToken* prevalidatedType = nullptr) noexcept;

struct FixtureCoverage {
    bool horizontalMerge = false;
    bool verticalMerge = false;
    bool rectangularMerge = false;
    bool terminalMerge = false;
    bool nestedTable = false;
    bool sharedHeaderFooter = false;
    bool notes = false;
    bool textBoxes = false;
    bool captions = false;
};

std::wstring FormatFixturePrerequisites(
    const std::wstring& scenario,
    const FixtureCoverage& coverage);

struct StorySpineReaderDiagnostics final {
    bool began = false;
    bool committed = false;
    bool aborted = false;
};

bool CaptureStorySpineReaderPayload(
    IDispatch* hwp,
    graph::capture::ReaderPayload* output,
    StorySpineReaderDiagnostics* diagnostics = nullptr) noexcept;

bool AdaptTableInstanceIdObservation(
    const RawCall& call,
    graph::capture::TableObservation* output) noexcept;

bool CaptureTableTopologyReaderPayload(
    IDispatch* hwp,
    const std::vector<graph::capture::ControlObservation>& controls,
    graph::capture::ReaderPayload* output,
    std::wstring* failure = nullptr) noexcept;


struct TableGraphDiagnosticCounters final {
    std::uint64_t probes = 0;
    std::uint64_t cellRows = 0;
    std::uint64_t errorSinks = 0;
};

void ResetTableGraphDiagnosticCounters() noexcept;
TableGraphDiagnosticCounters ReadTableGraphDiagnosticCounters() noexcept;

bool ReconcileControlLocators(
    const std::vector<graph::capture::ControlObservation>& controls,
    std::vector<graph::capture::TableObservation>* tables,
    std::vector<graph::capture::ImageObservation>* images) noexcept;

// Production owner for the same seven-reader capture used by qualification.
// CaptureCoordinator publishes atomically, so failure leaves the active
// GraphStore generation untouched.
graph::capture::CaptureStatus CaptureDocumentGraphToStore(
    IDispatch* hwp,
    graph::store::GraphStore* store,
    const graph::layout::LayoutEnvironmentPlatformV1* environmentPlatform =
        nullptr,
    const graph::identity::DocumentSessionId* session = nullptr,
    graph::capture::CaptureDiagnostics* diagnostics = nullptr) noexcept;

std::wstring ProbeCapability(
    IDispatch* hwp,
    const std::wstring& scenario,
    const std::vector<std::wstring>& inputLines);

std::wstring ProbeCapability(
    IDispatch* hwp,
    const std::wstring& scenario,
    const std::vector<std::wstring>& inputLines,
    const graph::layout::LayoutEnvironmentPlatformV1* environmentPlatform);


}

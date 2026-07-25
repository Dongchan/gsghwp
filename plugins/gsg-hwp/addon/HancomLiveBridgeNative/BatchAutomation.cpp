#include "BatchAutomation.h"

#include "ActionExecutor.h"
#include "ActionProtocol.h"
#include "BatchExecutor.h"
#include "BatchProtocol.h"
#include "DispatchInvoke.h"
#include "DocumentLifecycle.h"
#include "LiveInspection.h"
#include "OfficialApiProbe.h"

#include <cwchar>
#include <new>
#include <string>

namespace {

constexpr DISPID kProtocolVersion = 1;
constexpr DISPID kPing = 2;
constexpr DISPID kExecute = 3;
constexpr DISPID kSnapshot = 4;
constexpr DISPID kInspectPage = 5;
constexpr DISPID kExecuteActions = 6;
constexpr DISPID kInspectPageV3 = 7;
constexpr DISPID kInspectPageSummary = 8;
constexpr DISPID kProbeOfficialApi = 9;
constexpr DISPID kInspectStructure = 10;
constexpr DISPID kInspectRoutingContext = 11;
constexpr DISPID kSaveReopenVerify = 12;
constexpr DISPID kInspectPagesV3 = 13;
constexpr DISPID kSaveVerify = 14;
constexpr DISPID kTargetDocumentId = 15;
constexpr DISPID kActivateDocument = 16;
constexpr DISPID kActivationStatus = 17;
constexpr LONG kActivationIdle = 0;
constexpr LONG kActivationPending = 1;
constexpr LONG kActivationSucceeded = 2;
constexpr LONG kActivationFailed = 3;
volatile LONG gActivationSequence = 0;

DISPID MemberId(const wchar_t* const name) noexcept {
    if (_wcsicmp(name, L"ProtocolVersion") == 0) {
        return kProtocolVersion;
    }
    if (_wcsicmp(name, L"Ping") == 0) {
        return kPing;
    }
    if (_wcsicmp(name, L"Execute") == 0) {
        return kExecute;
    }
    if (_wcsicmp(name, L"Snapshot") == 0) {
        return kSnapshot;
    }
    if (_wcsicmp(name, L"InspectPage") == 0) {
        return kInspectPage;
    }
    if (_wcsicmp(name, L"ExecuteActions") == 0) {
        return kExecuteActions;
    }
    if (_wcsicmp(name, L"InspectPageV3") == 0) {
        return kInspectPageV3;
    }
    if (_wcsicmp(name, L"InspectPageSummary") == 0) {
        return kInspectPageSummary;
    }
    if (_wcsicmp(name, L"ProbeOfficialApi") == 0) {
        return kProbeOfficialApi;
    }
    if (_wcsicmp(name, L"InspectStructure") == 0) {
        return kInspectStructure;
    }
    if (_wcsicmp(name, L"InspectRoutingContext") == 0) {
        return kInspectRoutingContext;
    }
    if (_wcsicmp(name, L"SaveReopenVerify") == 0) {
        return kSaveReopenVerify;
    }
    if (_wcsicmp(name, L"InspectPagesV3") == 0) {
        return kInspectPagesV3;
    }
    if (_wcsicmp(name, L"SaveVerify") == 0) {
        return kSaveVerify;
    }
    if (_wcsicmp(name, L"TargetDocumentID") == 0) {
        return kTargetDocumentId;
    }
    if (_wcsicmp(name, L"ActivateDocument") == 0) {
        return kActivateDocument;
    }
    if (_wcsicmp(name, L"ActivationStatus") == 0) {
        return kActivationStatus;
    }
    return DISPID_UNKNOWN;
}

HRESULT ActivateDocumentNow(
    IDispatch* const hwp,
    const LONG documentId) noexcept {
    if (hwp == nullptr || documentId <= 0) {
        return E_INVALIDARG;
    }
    CComVariant rawDocuments;
    HRESULT status = hancom::dispatch::PropertyGet(
        hwp,
        L"XHwpDocuments",
        &rawDocuments);
    CComPtr<IDispatch> documents;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawDocuments, documents);
    }
    CComVariant rawDocument;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::Method(
            documents,
            L"FindItem",
            {CComVariant(documentId)},
            &rawDocument);
    }
    CComPtr<IDispatch> document;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawDocument, document);
    }
    CComVariant ignored;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::Method(
            document,
            L"SetActive_XHwpDocument",
            {},
            &ignored);
    }
    CComVariant rawActive;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            documents,
            L"Active_XHwpDocument",
            &rawActive);
    }
    CComPtr<IDispatch> active;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawActive, active);
    }
    CComVariant rawActiveId;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            active,
            L"DocumentID",
            &rawActiveId);
    }
    LONG activeId = 0;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsLong(rawActiveId, &activeId);
    }
    if (FAILED(status)) {
        return status;
    }
    if (activeId != documentId) {
        return HRESULT_FROM_WIN32(ERROR_INVALID_STATE);
    }
    return S_OK;
}

HRESULT ProbeOfficialApiUnguarded(
    IDispatch* const hwp,
    BSTR const payload,
    VARIANT* const result) {
    const std::wstring response = hancom::official_api::Probe(
        hwp,
        std::wstring(payload, SysStringLen(payload)));
    VariantInit(result);
    result->vt = VT_BSTR;
    result->bstrVal = SysAllocStringLen(
        response.data(),
        static_cast<UINT>(response.size()));
    return result->bstrVal == nullptr && !response.empty() ? E_OUTOFMEMORY : S_OK;
}

HRESULT ProbeOfficialApiGuarded(
    IDispatch* const hwp,
    BSTR const payload,
    VARIANT* const result) noexcept {
    __try {
        return ProbeOfficialApiUnguarded(hwp, payload, result);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        wchar_t response[64]{};
        static_cast<void>(swprintf_s(
            response,
            L"HCV1\tERROR\tSEH\t%08lX",
            GetExceptionCode()));
        VariantInit(result);
        result->vt = VT_BSTR;
        result->bstrVal = SysAllocString(response);
        return result->bstrVal == nullptr ? E_OUTOFMEMORY : S_OK;
    }
}

}

BatchAutomation::BatchAutomation(
    IDispatch* const hwp,
    const LONG targetDocumentId) noexcept
    : hwp_(hwp),
      targetDocumentId_(targetDocumentId) {}

BatchAutomation::~BatchAutomation() {
    if (activationFailureEvent_ != nullptr) {
        CloseHandle(activationFailureEvent_);
    }
    if (activationSuccessEvent_ != nullptr) {
        CloseHandle(activationSuccessEvent_);
    }
}

struct BatchAutomation::ActivationWork {
    BatchAutomation* owner = nullptr;
    IStream* stream = nullptr;
    LONG documentId = 0;
};

HRESULT BatchAutomation::BeginActivation(
    const LONG documentId,
    VARIANT* const result) noexcept {
    if (documentId <= 0 || result == nullptr || hwp_ == nullptr) {
        return E_INVALIDARG;
    }
    const LONG previousState = InterlockedCompareExchange(
        &activationState_,
        kActivationPending,
        kActivationIdle);
    if (previousState == kActivationPending) {
        if (InterlockedCompareExchange(
                &activationDocumentId_,
                0,
                0) != documentId) {
            return HRESULT_FROM_WIN32(ERROR_BUSY);
        }
        VariantInit(result);
        return ReturnString(activationToken_, result);
    }
    if (previousState != kActivationIdle &&
        InterlockedCompareExchange(
            &activationState_,
            kActivationPending,
            previousState) != previousState) {
        return HRESULT_FROM_WIN32(ERROR_BUSY);
    }
    InterlockedExchange(&activationDocumentId_, documentId);
    InterlockedExchange(&activationHresult_, E_PENDING);
    if (activationFailureEvent_ != nullptr) {
        CloseHandle(activationFailureEvent_);
        activationFailureEvent_ = nullptr;
    }
    if (activationSuccessEvent_ != nullptr) {
        CloseHandle(activationSuccessEvent_);
        activationSuccessEvent_ = nullptr;
    }
    const LONG sequence = InterlockedIncrement(&gActivationSequence);
    wchar_t token[128]{};
    if (swprintf_s(
            token,
            L"Local\\HancomLiveActivation.%lu.%ld",
            static_cast<unsigned long>(GetCurrentProcessId()),
            sequence) < 0) {
        FinishActivation(E_FAIL);
        return E_FAIL;
    }
    activationToken_.assign(token);
    const std::wstring successName = activationToken_ + L".Success";
    const std::wstring failureName = activationToken_ + L".Failure";
    activationSuccessEvent_ = CreateEventW(
        nullptr,
        TRUE,
        FALSE,
        successName.c_str());
    activationFailureEvent_ = CreateEventW(
        nullptr,
        TRUE,
        FALSE,
        failureName.c_str());
    if (activationSuccessEvent_ == nullptr ||
        activationFailureEvent_ == nullptr) {
        const HRESULT eventStatus = HRESULT_FROM_WIN32(GetLastError());
        FinishActivation(eventStatus);
        return eventStatus;
    }

    IStream* stream = nullptr;
    HRESULT status = CoMarshalInterThreadInterfaceInStream(
        IID_IDispatch,
        hwp_,
        &stream);
    if (FAILED(status)) {
        FinishActivation(status);
        return status;
    }
    ActivationWork* const work = new (std::nothrow) ActivationWork{
        this,
        stream,
        documentId,
    };
    if (work == nullptr) {
        stream->Release();
        FinishActivation(E_OUTOFMEMORY);
        return E_OUTOFMEMORY;
    }
    static_cast<void>(AddRef());
    HANDLE const thread = CreateThread(
        nullptr,
        0,
        &BatchAutomation::RunActivation,
        work,
        0,
        nullptr);
    if (thread == nullptr) {
        const HRESULT threadStatus = HRESULT_FROM_WIN32(GetLastError());
        static_cast<void>(Release());
        stream->Release();
        delete work;
        FinishActivation(threadStatus);
        return threadStatus;
    }
    CloseHandle(thread);
    return ReturnString(activationToken_, result);
}

HRESULT BatchAutomation::PollActivation(
    const LONG documentId,
    VARIANT* const result) noexcept {
    if (documentId <= 0 || result == nullptr) {
        return E_INVALIDARG;
    }
    if (InterlockedCompareExchange(
            &activationDocumentId_,
            0,
            0) != documentId) {
        return HRESULT_FROM_WIN32(ERROR_INVALID_STATE);
    }
    const LONG state = InterlockedCompareExchange(
        &activationState_,
        0,
        0);
    LONG publicState = 0;
    if (state == kActivationSucceeded) {
        publicState = 1;
    } else if (state == kActivationFailed) {
        publicState = InterlockedCompareExchange(
            &activationHresult_,
            0,
            0);
        if (publicState == 0 || publicState == 1) {
            publicState = E_FAIL;
        }
    } else if (state != kActivationPending) {
        return HRESULT_FROM_WIN32(ERROR_INVALID_STATE);
    }
    VariantInit(result);
    result->vt = VT_I4;
    result->lVal = publicState;
    return S_OK;
}

DWORD WINAPI BatchAutomation::RunActivation(void* const context) noexcept {
    ActivationWork* const work = static_cast<ActivationWork*>(context);
    if (work == nullptr || work->owner == nullptr || work->stream == nullptr) {
        return ERROR_INVALID_PARAMETER;
    }
    const HRESULT initialized = CoInitializeEx(
        nullptr,
        COINIT_MULTITHREADED);
    CComPtr<IDispatch> hwp;
    IDispatch* rawHwp = nullptr;
    HRESULT status = initialized;
    if (SUCCEEDED(status)) {
        status = CoGetInterfaceAndReleaseStream(
            work->stream,
            IID_IDispatch,
            reinterpret_cast<void**>(&rawHwp));
        work->stream = nullptr;
        if (SUCCEEDED(status)) {
            hwp.Attach(rawHwp);
        }
    }
    if (SUCCEEDED(status)) {
        status = ActivateDocumentNow(hwp, work->documentId);
    }
    work->owner->FinishActivation(status);
    if (SUCCEEDED(initialized)) {
        CoUninitialize();
    } else if (work->stream != nullptr) {
        work->stream->Release();
    }
    static_cast<void>(work->owner->Release());
    delete work;
    return FAILED(status)
        ? static_cast<DWORD>(status)
        : ERROR_SUCCESS;
}

void BatchAutomation::FinishActivation(const HRESULT status) noexcept {
    InterlockedExchange(&activationHresult_, status);
    InterlockedExchange(
        &activationState_,
        SUCCEEDED(status) ? kActivationSucceeded : kActivationFailed);
    HANDLE const event = SUCCEEDED(status)
        ? activationSuccessEvent_
        : activationFailureEvent_;
    if (event != nullptr) {
        SetEvent(event);
    }
}

HRESULT STDMETHODCALLTYPE BatchAutomation::QueryInterface(
    REFIID interfaceId,
    void** const object) {
    if (object == nullptr) {
        return E_POINTER;
    }
    if (interfaceId == IID_IUnknown || interfaceId == IID_IDispatch) {
        *object = static_cast<IDispatch*>(this);
        static_cast<void>(AddRef());
        return S_OK;
    }
    *object = nullptr;
    return E_NOINTERFACE;
}

ULONG STDMETHODCALLTYPE BatchAutomation::AddRef() {
    return static_cast<ULONG>(InterlockedIncrement(&references_));
}

ULONG STDMETHODCALLTYPE BatchAutomation::Release() {
    const LONG remaining = InterlockedDecrement(&references_);
    if (remaining == 0) {
        delete this;
    }
    return static_cast<ULONG>(remaining);
}

HRESULT STDMETHODCALLTYPE BatchAutomation::GetTypeInfoCount(UINT* const count) {
    if (count == nullptr) {
        return E_POINTER;
    }
    *count = 0;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE BatchAutomation::GetTypeInfo(
    UINT,
    LCID,
    ITypeInfo**) {
    return E_NOTIMPL;
}

HRESULT STDMETHODCALLTYPE BatchAutomation::GetIDsOfNames(
    REFIID interfaceId,
    LPOLESTR* const names,
    const UINT count,
    LCID,
    DISPID* const memberIds) {
    if (interfaceId != IID_NULL) {
        return DISP_E_UNKNOWNINTERFACE;
    }
    if (names == nullptr || memberIds == nullptr) {
        return E_POINTER;
    }
    HRESULT status = S_OK;
    for (UINT index = 0; index < count; ++index) {
        memberIds[index] = names[index] == nullptr ? DISPID_UNKNOWN : MemberId(names[index]);
        if (memberIds[index] == DISPID_UNKNOWN) {
            status = DISP_E_UNKNOWNNAME;
        }
    }
    return status;
}

HRESULT BatchAutomation::ReturnString(
    const std::wstring& value,
    VARIANT* const result) noexcept {
    if (result == nullptr) {
        return E_POINTER;
    }
    VariantInit(result);
    result->vt = VT_BSTR;
    result->bstrVal = SysAllocStringLen(value.data(), static_cast<UINT>(value.size()));
    return result->bstrVal == nullptr && !value.empty() ? E_OUTOFMEMORY : S_OK;
}

HRESULT BatchAutomation::VerifyTarget() {
    if (targetDocumentId_ <= 0) {
        return S_OK;
    }
    CComVariant rawDocuments;
    HRESULT status = hancom::dispatch::PropertyGet(
        hwp_,
        L"XHwpDocuments",
        &rawDocuments);
    CComPtr<IDispatch> documents;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawDocuments, documents);
    }
    CComVariant rawActive;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            documents,
            L"Active_XHwpDocument",
            &rawActive);
    }
    CComPtr<IDispatch> active;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawActive, active);
    }
    CComVariant rawActiveId;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            active,
            L"DocumentID",
            &rawActiveId);
    }
    LONG activeId = 0;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsLong(rawActiveId, &activeId);
    }
    if (FAILED(status)) {
        return status;
    }
    return activeId == targetDocumentId_
        ? S_OK
        : HRESULT_FROM_WIN32(ERROR_INVALID_STATE);
}

HRESULT STDMETHODCALLTYPE BatchAutomation::Invoke(
    const DISPID memberId,
    REFIID interfaceId,
    LCID,
    const WORD flags,
    DISPPARAMS* const parameters,
    VARIANT* const result,
    EXCEPINFO*,
    UINT* const argumentError) {
    if (interfaceId != IID_NULL) {
        return DISP_E_UNKNOWNINTERFACE;
    }
    if (argumentError != nullptr) {
        *argumentError = 0;
    }
    const UINT argumentCount = parameters == nullptr ? 0 : parameters->cArgs;
    if (memberId == kProtocolVersion) {
        if ((flags & DISPATCH_PROPERTYGET) == 0 || argumentCount != 0 || result == nullptr) {
            return DISP_E_BADPARAMCOUNT;
        }
        VariantInit(result);
        result->vt = VT_I4;
        result->lVal = 12;
        return S_OK;
    }
    if (memberId == kTargetDocumentId) {
        if ((flags & DISPATCH_PROPERTYGET) == 0 || argumentCount != 0 ||
            result == nullptr) {
            return DISP_E_BADPARAMCOUNT;
        }
        VariantInit(result);
        result->vt = VT_I4;
        result->lVal = targetDocumentId_;
        return S_OK;
    }
    if (memberId == kPing) {
        if ((flags & DISPATCH_METHOD) == 0 || argumentCount != 0) {
            return DISP_E_BADPARAMCOUNT;
        }
        return ReturnString(L"HCB12\tPONG\t12", result);
    }
    if (memberId == kActivateDocument) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr ||
            argumentCount != 1 || result == nullptr) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant documentId(parameters->rgvarg[0]);
        const HRESULT converted = documentId.ChangeType(VT_I4);
        if (FAILED(converted) || documentId.lVal <= 0) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        if (targetDocumentId_ > 0 &&
            targetDocumentId_ != documentId.lVal) {
            return HRESULT_FROM_WIN32(ERROR_INVALID_STATE);
        }
        return BeginActivation(documentId.lVal, result);
    }
    if (memberId == kActivationStatus) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr ||
            argumentCount != 1 || result == nullptr) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant documentId(parameters->rgvarg[0]);
        const HRESULT converted = documentId.ChangeType(VT_I4);
        if (FAILED(converted) || documentId.lVal <= 0) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        return PollActivation(documentId.lVal, result);
    }
    if (targetDocumentId_ > 0) {
        const HRESULT status = VerifyTarget();
        if (FAILED(status)) {
            return status;
        }
    }
    if (memberId == kSaveVerify) {
        if ((flags & DISPATCH_METHOD) == 0 || argumentCount != 0) {
            return DISP_E_BADPARAMCOUNT;
        }
        return ReturnString(hancom::lifecycle::SaveVerify(hwp_), result);
    }
    if (memberId == kSaveReopenVerify) {
        if ((flags & DISPATCH_METHOD) == 0 || argumentCount != 0) {
            return DISP_E_BADPARAMCOUNT;
        }
        return ReturnString(hancom::lifecycle::SaveReopenVerify(hwp_), result);
    }
    if (memberId == kSnapshot) {
        if ((flags & DISPATCH_METHOD) == 0 || argumentCount != 0) {
            return DISP_E_BADPARAMCOUNT;
        }
        return ReturnString(hancom::inspection::Snapshot(hwp_), result);
    }
    if (memberId == kInspectPage) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant page(parameters->rgvarg[0]);
        const HRESULT converted = page.ChangeType(VT_I4);
        if (FAILED(converted)) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        return ReturnString(hancom::inspection::InspectPage(hwp_, page.lVal), result);
    }
    if (memberId == kInspectPageV3) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant page(parameters->rgvarg[0]);
        const HRESULT converted = page.ChangeType(VT_I4);
        if (FAILED(converted)) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        return ReturnString(hancom::inspection::InspectPageV3(hwp_, page.lVal), result);
    }
    if (memberId == kInspectPageSummary) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant page(parameters->rgvarg[0]);
        const HRESULT converted = page.ChangeType(VT_I4);
        if (FAILED(converted)) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        return ReturnString(hancom::inspection::InspectPageSummary(hwp_, page.lVal), result);
    }
    if (memberId == kInspectPagesV3) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant pages(parameters->rgvarg[0]);
        const HRESULT converted = pages.ChangeType(VT_BSTR);
        if (FAILED(converted) || pages.bstrVal == nullptr) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        return ReturnString(
            hancom::inspection::InspectPagesV3(
                hwp_,
                std::wstring(pages.bstrVal, SysStringLen(pages.bstrVal))),
            result);
    }
    if (memberId == kInspectRoutingContext) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant pageHint(parameters->rgvarg[0]);
        const HRESULT converted = pageHint.ChangeType(VT_I4);
        if (FAILED(converted)) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        return ReturnString(
            hancom::inspection::InspectRoutingContext(hwp_, pageHint.lVal),
            result);
    }
    if (memberId == kInspectStructure) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant page(parameters->rgvarg[0]);
        const HRESULT converted = page.ChangeType(VT_I4);
        if (FAILED(converted)) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        return ReturnString(hancom::inspection::InspectStructure(hwp_, page.lVal), result);
    }
    if (memberId == kProbeOfficialApi) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant payload(parameters->rgvarg[0]);
        const HRESULT converted = payload.ChangeType(VT_BSTR);
        if (FAILED(converted) || payload.bstrVal == nullptr) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        return ProbeOfficialApiGuarded(hwp_, payload.bstrVal, result);
    }
    if (memberId == kExecuteActions) {
        if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
            return DISP_E_BADPARAMCOUNT;
        }
        CComVariant payload(parameters->rgvarg[0]);
        const HRESULT converted = payload.ChangeType(VT_BSTR);
        if (FAILED(converted) || payload.bstrVal == nullptr) {
            if (argumentError != nullptr) {
                *argumentError = 0;
            }
            return DISP_E_TYPEMISMATCH;
        }
        hancom::actions::Request request;
        hancom::actions::Error parseError;
        std::wstring response;
        const std::wstring requestPayload(payload.bstrVal, SysStringLen(payload.bstrVal));
        if (!hancom::actions::ParseRequest(requestPayload, &request, &parseError)) {
            response = hancom::actions::ErrorResponse(parseError);
        } else {
            const hancom::actions::ExecutionResult execution = hancom::actions::Execute(hwp_, request);
            response = execution.succeeded
                ? hancom::actions::SuccessResponse(execution)
                : hancom::actions::FailureResponse(execution);
        }
        return ReturnString(response, result);
    }
    if (memberId != kExecute) {
        return DISP_E_MEMBERNOTFOUND;
    }
    if ((flags & DISPATCH_METHOD) == 0 || parameters == nullptr || argumentCount != 1) {
        return DISP_E_BADPARAMCOUNT;
    }
    CComVariant payload(parameters->rgvarg[0]);
    const HRESULT converted = payload.ChangeType(VT_BSTR);
    if (FAILED(converted) || payload.bstrVal == nullptr) {
        if (argumentError != nullptr) {
            *argumentError = 0;
        }
        return DISP_E_TYPEMISMATCH;
    }

    hancom::batch::Request request;
    hancom::batch::Error parseError;
    std::wstring response;
    const std::wstring requestPayload(payload.bstrVal, SysStringLen(payload.bstrVal));
    if (!hancom::batch::ParseRequest(requestPayload, &request, &parseError)) {
        response = hancom::batch::ErrorResponse(parseError);
    } else {
        const hancom::batch::ExecutionResult execution = hancom::batch::Execute(hwp_, request);
        response = execution.succeeded
            ? hancom::batch::SuccessResponse(
                  execution.textUpdates,
                  execution.imageUpdates,
                  execution.elapsedMicroseconds)
            : hancom::batch::ErrorResponse(execution.error);
    }
    return ReturnString(response, result);
}

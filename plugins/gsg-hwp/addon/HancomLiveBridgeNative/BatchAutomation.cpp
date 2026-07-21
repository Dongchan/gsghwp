#include "BatchAutomation.h"

#include "ActionExecutor.h"
#include "ActionProtocol.h"
#include "BatchExecutor.h"
#include "BatchProtocol.h"
#include "DocumentLifecycle.h"
#include "LiveInspection.h"
#include "OfficialApiProbe.h"

#include <cwchar>
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
    return DISPID_UNKNOWN;
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

BatchAutomation::BatchAutomation(IDispatch* const hwp) noexcept : hwp_(hwp) {}

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
        result->lVal = 9;
        return S_OK;
    }
    if (memberId == kPing) {
        if ((flags & DISPATCH_METHOD) == 0 || argumentCount != 0) {
            return DISP_E_BADPARAMCOUNT;
        }
        return ReturnString(L"HCB9\tPONG\t9", result);
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

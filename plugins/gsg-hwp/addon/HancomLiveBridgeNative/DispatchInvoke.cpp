#include "DispatchInvoke.h"

#include <algorithm>
#include <string>

namespace hancom::dispatch {
namespace {

constexpr HRESULT kRpcCallRejected = static_cast<HRESULT>(0x80010001UL);
constexpr HRESULT kRpcServerCallRetryLater = static_cast<HRESULT>(0x8001010AUL);
constexpr HRESULT kRpcServerCallRejected = static_cast<HRESULT>(0x8001010BUL);
constexpr DWORD kBusyRetryDelays[] = {25, 50, 100};

HRESULT ExceptionStatus(const DWORD code) noexcept {
    return static_cast<HRESULT>(code | FACILITY_NT_BIT);
}

bool IsComBusy(const HRESULT status) noexcept {
    return status == kRpcCallRejected
        || status == kRpcServerCallRetryLater
        || status == kRpcServerCallRejected;
}

HRESULT GuardedGetIdsOfNamesOnce(
    IDispatch* const object,
    LPOLESTR* const names,
    const UINT count,
    DISPID* const members) noexcept {
    __try {
        return object->GetIDsOfNames(
            IID_NULL,
            names,
            count,
            LOCALE_USER_DEFAULT,
            members);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

HRESULT GuardedGetIdsOfNames(
    IDispatch* const object,
    LPOLESTR* const names,
    const UINT count,
    DISPID* const members) noexcept {
    HRESULT status = E_FAIL;
    for (const DWORD delay : kBusyRetryDelays) {
        status = GuardedGetIdsOfNamesOnce(object, names, count, members);
        if (!IsComBusy(status)) {
            return status;
        }
        Sleep(delay);
    }
    return GuardedGetIdsOfNamesOnce(object, names, count, members);
}

HRESULT GuardedDispatchInvokeOnce(
    IDispatch* const object,
    const DISPID member,
    const WORD flags,
    DISPPARAMS* const parameters,
    VARIANT* const result,
    EXCEPINFO* const exception,
    UINT* const argumentError) noexcept {
    __try {
        return object->Invoke(
            member,
            IID_NULL,
            LOCALE_USER_DEFAULT,
            flags,
            parameters,
            result,
            exception,
            argumentError);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

void ClearExceptionInfo(EXCEPINFO* const exception) noexcept {
    if (exception == nullptr) {
        return;
    }
    if (exception->bstrSource != nullptr) {
        SysFreeString(exception->bstrSource);
    }
    if (exception->bstrDescription != nullptr) {
        SysFreeString(exception->bstrDescription);
    }
    if (exception->bstrHelpFile != nullptr) {
        SysFreeString(exception->bstrHelpFile);
    }
    *exception = {};
}

}

HRESULT Invoke(
    IDispatch* const object,
    LPCOLESTR const name,
    const WORD flags,
    const std::vector<CComVariant>& arguments,
    CComVariant* const result) noexcept {
    if (object == nullptr || name == nullptr) {
        return E_POINTER;
    }

    DISPID member = DISPID_UNKNOWN;
    LPOLESTR mutableName = const_cast<LPOLESTR>(name);
    HRESULT status = GuardedGetIdsOfNames(object, &mutableName, 1, &member);
    if (FAILED(status)) {
        return status;
    }

    std::vector<VARIANTARG> reversed(arguments.size());
    for (VARIANTARG& argument : reversed) {
        VariantInit(&argument);
    }
    for (size_t index = 0; index < arguments.size(); ++index) {
        status = VariantCopy(
            &reversed[index],
            const_cast<VARIANT*>(static_cast<const VARIANT*>(&arguments[arguments.size() - index - 1])));
        if (FAILED(status)) {
            for (VARIANTARG& argument : reversed) {
                VariantClear(&argument);
            }
            return status;
        }
    }

    DISPID propertyPut = DISPID_PROPERTYPUT;
    DISPPARAMS parameters{};
    parameters.rgvarg = reversed.empty() ? nullptr : reversed.data();
    parameters.cArgs = static_cast<UINT>(reversed.size());
    if ((flags & (DISPATCH_PROPERTYPUT | DISPATCH_PROPERTYPUTREF)) != 0) {
        parameters.rgdispidNamedArgs = &propertyPut;
        parameters.cNamedArgs = 1;
    }

    CComVariant localResult;
    EXCEPINFO exception{};
    UINT argumentError = 0;
    if (flags == DISPATCH_PROPERTYGET) {
        for (const DWORD delay : kBusyRetryDelays) {
            status = GuardedDispatchInvokeOnce(
                object,
                member,
                flags,
                &parameters,
                result == nullptr ? nullptr : &localResult,
                &exception,
                &argumentError);
            if (!IsComBusy(status)) {
                break;
            }
            localResult.Clear();
            ClearExceptionInfo(&exception);
            argumentError = 0;
            Sleep(delay);
        }
        if (IsComBusy(status)) {
            status = GuardedDispatchInvokeOnce(
                object,
                member,
                flags,
                &parameters,
                result == nullptr ? nullptr : &localResult,
                &exception,
                &argumentError);
        }
    } else {
        status = GuardedDispatchInvokeOnce(
            object,
            member,
            flags,
            &parameters,
            result == nullptr ? nullptr : &localResult,
            &exception,
            &argumentError);
    }
    for (VARIANTARG& argument : reversed) {
        VariantClear(&argument);
    }
    ClearExceptionInfo(&exception);
    if (SUCCEEDED(status) && result != nullptr) {
        *result = localResult;
    }
    return status;
}

HRESULT Method(
    IDispatch* const object,
    LPCOLESTR const name,
    const std::vector<CComVariant>& arguments,
    CComVariant* const result) noexcept {
    return Invoke(object, name, DISPATCH_METHOD, arguments, result);
}

HRESULT PropertyGet(
    IDispatch* const object,
    LPCOLESTR const name,
    CComVariant* const result) noexcept {
    if (result == nullptr) {
        return E_POINTER;
    }
    return Invoke(object, name, DISPATCH_PROPERTYGET, {}, result);
}

HRESULT PropertyPut(
    IDispatch* const object,
    LPCOLESTR const name,
    const CComVariant& value) noexcept {
    HRESULT status = Invoke(object, name, DISPATCH_PROPERTYPUT, {value}, nullptr);
    if (FAILED(status) && (value.vt == VT_DISPATCH || value.vt == VT_UNKNOWN)) {
        status = Invoke(object, name, DISPATCH_PROPERTYPUTREF, {value}, nullptr);
    }
    return status;
}

HRESULT AsDispatch(
    const CComVariant& value,
    CComPtr<IDispatch>& result) noexcept {
    result.Release();
    if (value.vt == VT_DISPATCH && value.pdispVal != nullptr) {
        result = value.pdispVal;
        return S_OK;
    }
    if (value.vt == VT_UNKNOWN && value.punkVal != nullptr) {
        IDispatch* dispatch = nullptr;
        const HRESULT status = value.punkVal->QueryInterface(
            IID_IDispatch,
            reinterpret_cast<void**>(&dispatch));
        if (SUCCEEDED(status)) {
            result.Attach(dispatch);
        }
        return status;
    }
    return DISP_E_TYPEMISMATCH;
}

HRESULT AsString(
    const CComVariant& value,
    std::wstring* const result) noexcept {
    if (result == nullptr) {
        return E_POINTER;
    }
    CComVariant converted(value);
    const HRESULT status = converted.ChangeType(VT_BSTR);
    if (FAILED(status) || converted.bstrVal == nullptr) {
        return FAILED(status) ? status : DISP_E_TYPEMISMATCH;
    }
    result->assign(converted.bstrVal, SysStringLen(converted.bstrVal));
    return S_OK;
}

HRESULT AsLong(const CComVariant& value, LONG* const result) noexcept {
    if (result == nullptr) {
        return E_POINTER;
    }
    CComVariant converted(value);
    const HRESULT status = converted.ChangeType(VT_I4);
    if (FAILED(status)) {
        return status;
    }
    *result = converted.lVal;
    return S_OK;
}

HRESULT AsBool(const CComVariant& value, bool* const result) noexcept {
    if (result == nullptr) {
        return E_POINTER;
    }
    CComVariant converted(value);
    const HRESULT status = converted.ChangeType(VT_BOOL);
    if (FAILED(status)) {
        return status;
    }
    *result = converted.boolVal != VARIANT_FALSE;
    return S_OK;
}

}

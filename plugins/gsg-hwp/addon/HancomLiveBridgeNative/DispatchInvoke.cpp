#include "DispatchInvoke.h"

#include <algorithm>
#include <cstring>
#include <map>
#include <string>

namespace hancom::dispatch {
namespace {

constexpr HRESULT kRpcCallRejected = static_cast<HRESULT>(0x80010001UL);
constexpr HRESULT kRpcServerCallRetryLater = static_cast<HRESULT>(0x8001010AUL);
constexpr HRESULT kRpcServerCallRejected = static_cast<HRESULT>(0x8001010BUL);
constexpr DWORD kBusyRetryDelays[] = {25, 50, 100};
// The production tree has fewer than 450 dispatch call expressions. This leaves
// room for more than twice the current interface/member surface while placing a
// fixed bound on copied scalar identity and member-name storage per thread.
constexpr size_t kDispidCacheEntryLimit = 1024;

struct DispidCacheKey final {
    TypeIdentityToken type{};
    std::wstring name;

    bool operator<(const DispidCacheKey& other) const noexcept {
        const int guidOrder = std::memcmp(&type.guid, &other.type.guid, sizeof(GUID));
        if (guidOrder != 0) return guidOrder < 0;
        if (type.majorVersion != other.type.majorVersion) {
            return type.majorVersion < other.type.majorVersion;
        }
        if (type.minorVersion != other.type.minorVersion) {
            return type.minorVersion < other.type.minorVersion;
        }
        if (type.lcid != other.type.lcid) return type.lcid < other.type.lcid;
        if (type.kind != other.type.kind) return type.kind < other.type.kind;
        if (type.flags != other.type.flags) return type.flags < other.type.flags;
        return name < other.name;
    }
};

using DispidCache = std::map<DispidCacheKey, DISPID>;

DispidCache& CurrentThreadDispidCache() {
    thread_local DispidCache cache;
    return cache;
}

DispidCacheDiagnostics& CurrentThreadDispidCacheDiagnostics() {
    thread_local DispidCacheDiagnostics diagnostics;
    return diagnostics;
}

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

HRESULT ResolveMember(
    IDispatch* const object,
    const TypeIdentityToken* const type,
    LPCOLESTR const name,
    DISPID* const member) noexcept {
    DispidCacheDiagnostics& diagnostics =
        CurrentThreadDispidCacheDiagnostics();
    if (type == nullptr || InlineIsEqualGUID(type->guid, GUID_NULL)) {
        ++diagnostics.unqualifiedCalls;
        LPOLESTR mutableName = const_cast<LPOLESTR>(name);
        return GuardedGetIdsOfNames(object, &mutableName, 1, member);
    }
    ++diagnostics.qualifiedCalls;

    DispidCacheKey key{};
    key.type = *type;
    try {
        key.name.assign(name);
        const DispidCache& cache = CurrentThreadDispidCache();
        const auto found = cache.find(key);
        if (found != cache.end()) {
            ++diagnostics.hits;
            *member = found->second;
            return S_OK;
        }
        ++diagnostics.misses;
    } catch (...) {
        ++diagnostics.misses;
        LPOLESTR mutableName = const_cast<LPOLESTR>(name);
        return GuardedGetIdsOfNames(object, &mutableName, 1, member);
    }

    LPOLESTR mutableName = const_cast<LPOLESTR>(name);
    const HRESULT status = GuardedGetIdsOfNames(object, &mutableName, 1, member);
    if (SUCCEEDED(status)) {
        try {
            DispidCache& cache = CurrentThreadDispidCache();
            if (cache.size() < kDispidCacheEntryLimit &&
                cache.emplace(std::move(key), *member).second) {
                ++diagnostics.insertions;
            }
        } catch (...) {
        }
    }
    return status;
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

void ResetDispidCacheDiagnostics() noexcept {
    try {
        CurrentThreadDispidCache().clear();
    } catch (...) {
    }
    CurrentThreadDispidCacheDiagnostics() = {};
}

DispidCacheDiagnostics ReadDispidCacheDiagnostics() noexcept {
    DispidCacheDiagnostics diagnostics =
        CurrentThreadDispidCacheDiagnostics();
    diagnostics.keys = CurrentThreadDispidCache().size();
    return diagnostics;
}

HRESULT ResolveDispidQualified(
    IDispatch* const object,
    const TypeIdentityToken& type,
    LPCOLESTR const name,
    DISPID* const member) noexcept {
    if (object == nullptr || name == nullptr || member == nullptr) {
        return E_POINTER;
    }
    return ResolveMember(object, &type, name, member);
}

static HRESULT InvokeWithType(
    IDispatch* const object,
    const TypeIdentityToken* const type,
    LPCOLESTR const name,
    const WORD flags,
    const std::vector<CComVariant>& arguments,
    CComVariant* const result) noexcept {
    if (object == nullptr || name == nullptr) {
        return E_POINTER;
    }

    DISPID member = DISPID_UNKNOWN;
    HRESULT status = ResolveMember(object, type, name, &member);
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

HRESULT Invoke(
    IDispatch* const object,
    LPCOLESTR const name,
    const WORD flags,
    const std::vector<CComVariant>& arguments,
    CComVariant* const result) noexcept {
    return InvokeWithType(object, nullptr, name, flags, arguments, result);
}

HRESULT InvokeQualified(
    IDispatch* const object,
    const TypeIdentityToken& type,
    LPCOLESTR const name,
    const WORD flags,
    const std::vector<CComVariant>& arguments,
    CComVariant* const result) noexcept {
    return InvokeWithType(object, &type, name, flags, arguments, result);
}

HRESULT Method(
    IDispatch* const object,
    LPCOLESTR const name,
    const std::vector<CComVariant>& arguments,
    CComVariant* const result) noexcept {
    return Invoke(object, name, DISPATCH_METHOD, arguments, result);
}

HRESULT MethodQualified(
    IDispatch* const object,
    const TypeIdentityToken& type,
    LPCOLESTR const name,
    const std::vector<CComVariant>& arguments,
    CComVariant* const result) noexcept {
    return InvokeQualified(
        object, type, name, DISPATCH_METHOD, arguments, result);
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

HRESULT PropertyGetQualified(
    IDispatch* const object,
    const TypeIdentityToken& type,
    LPCOLESTR const name,
    CComVariant* const result) noexcept {
    if (result == nullptr) {
        return E_POINTER;
    }
    return InvokeQualified(
        object, type, name, DISPATCH_PROPERTYGET, {}, result);
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

HRESULT PropertyPutQualified(
    IDispatch* const object,
    const TypeIdentityToken& type,
    LPCOLESTR const name,
    const CComVariant& value) noexcept {
    HRESULT status = InvokeQualified(
        object, type, name, DISPATCH_PROPERTYPUT, {value}, nullptr);
    if (FAILED(status) && (value.vt == VT_DISPATCH || value.vt == VT_UNKNOWN)) {
        status = InvokeQualified(
            object, type, name, DISPATCH_PROPERTYPUTREF, {value}, nullptr);
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

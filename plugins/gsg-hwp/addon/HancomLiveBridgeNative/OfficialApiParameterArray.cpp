#include "OfficialApiParameterArray.h"

#include "DispatchInvoke.h"
#include "OfficialApiVirtualMethod.h"

namespace hancom::official_api {
namespace {

HRESULT ExceptionStatus(const DWORD code) noexcept {
    return static_cast<HRESULT>(code | FACILITY_NT_BIT);
}

template<typename Function>
Function VtableFunction(IUnknown* const object, const size_t slot) noexcept {
    void** const table = *reinterpret_cast<void***>(object);
    return reinterpret_cast<Function>(table[slot]);
}

HRESULT GuardedCreateParameterArray(
    IUnknown* const object,
    const size_t slot,
    BSTR const name,
    const LONG count) noexcept {
    using CreateItemArray = HRESULT(STDMETHODCALLTYPE*)(IUnknown*, BSTR, LONG);
    __try {
        return VtableFunction<CreateItemArray>(object, slot)(
            object, name, count);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

}

HRESULT CreateParameterArray(
    IDispatch* const parameter,
    const std::wstring& name,
    const LONG count,
    CComPtr<IDispatch>& result) noexcept {
    result.Release();
    if (parameter == nullptr || name.empty() || count < 1) {
        return E_INVALIDARG;
    }
    static constexpr VARTYPE kParameterTypes[] = {VT_BSTR, VT_I4};
    CComPtr<IUnknown> parameterInterface;
    size_t slot = 0;
    HRESULT status = ResolveVirtualMethod(
        parameter,
        L"CreateItemArray",
        kParameterTypes,
        static_cast<USHORT>(std::size(kParameterTypes)),
        VT_VOID,
        parameterInterface,
        &slot);
    if (SUCCEEDED(status)) {
        CComBSTR itemName(name.c_str());
        status = GuardedCreateParameterArray(
            parameterInterface,
            slot,
            itemName,
            count);
    }
    CComVariant rawArray;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            parameter,
            name.c_str(),
            &rawArray);
    }
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawArray, result);
    }
    return status;
}

}

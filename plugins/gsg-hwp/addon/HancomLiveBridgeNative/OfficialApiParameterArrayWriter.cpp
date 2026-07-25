#include "OfficialApiParameterArray.h"

#include "OfficialApiVirtualMethod.h"

namespace hancom::official_api {
namespace {

constexpr IID kParameterArrayIid = {
    0xCAE59B55,
    0x0F90,
    0x4E9B,
    {0x9F, 0x9E, 0x91, 0xCC, 0x67, 0x1B, 0x7A, 0x49},
};
constexpr size_t kParameterArraySetItemSlot = 13U;

HRESULT ExceptionStatus(const DWORD code) noexcept {
    return static_cast<HRESULT>(code | FACILITY_NT_BIT);
}

HRESULT GuardedQueryParameterArray(
    IDispatch* const array,
    IUnknown** const result) noexcept {
    __try {
        return array->QueryInterface(
            kParameterArrayIid,
            reinterpret_cast<void**>(result));
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

template<typename Function>
Function VtableFunction(IUnknown* const object, const size_t slot) noexcept {
    void** const table = *reinterpret_cast<void***>(object);
    return reinterpret_cast<Function>(table[slot]);
}

HRESULT GuardedArraySetItem(
    IUnknown* const object,
    const size_t slot,
    const LONG index,
    const VARIANT value) noexcept {
    using SetItem = HRESULT(STDMETHODCALLTYPE*)(IUnknown*, LONG, VARIANT);
    __try {
        return VtableFunction<SetItem>(object, slot)(object, index, value);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

HRESULT GuardedGetMemberId(
    IDispatch* const array,
    const wchar_t* const memberName,
    DISPID* const member) noexcept {
    LPOLESTR name = const_cast<LPOLESTR>(memberName);
    __try {
        return array->GetIDsOfNames(
            IID_NULL,
            &name,
            1U,
            LOCALE_USER_DEFAULT,
            member);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

void ClearException(EXCEPINFO* const exception) noexcept {
    SysFreeString(exception->bstrSource);
    SysFreeString(exception->bstrDescription);
    SysFreeString(exception->bstrHelpFile);
    ZeroMemory(exception, sizeof(EXCEPINFO));
}

HRESULT GuardedDispatchInvoke(
    IDispatch* const array,
    const DISPID member,
    const WORD flags,
    DISPPARAMS* const parameters,
    EXCEPINFO* const exception,
    UINT* const argumentError) noexcept {
    __try {
        return array->Invoke(
            member,
            IID_NULL,
            LOCALE_USER_DEFAULT,
            flags,
            parameters,
            nullptr,
            exception,
            argumentError);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

HRESULT GuardedDispatchSetItem(
    IDispatch* const array,
    const DISPID member,
    const LONG index,
    const CComVariant& value,
    const bool propertyPut) noexcept {
    VARIANTARG arguments[2];
    VariantInit(&arguments[0]);
    VariantInit(&arguments[1]);
    HRESULT status = VariantCopy(
        &arguments[0],
        const_cast<VARIANT*>(
            static_cast<const VARIANT*>(&value)));
    const CComVariant indexValue(index);
    if (SUCCEEDED(status)) {
        status = VariantCopy(
            &arguments[1],
            const_cast<VARIANT*>(
                static_cast<const VARIANT*>(&indexValue)));
    }
    if (FAILED(status)) {
        VariantClear(&arguments[0]);
        VariantClear(&arguments[1]);
        return status;
    }
    DISPPARAMS parameters{};
    parameters.rgvarg = arguments;
    parameters.cArgs = 2U;
    DISPID namedArgument = DISPID_PROPERTYPUT;
    if (propertyPut) {
        parameters.rgdispidNamedArgs = &namedArgument;
        parameters.cNamedArgs = 1U;
    }
    EXCEPINFO exception{};
    UINT argumentError = 0;
    status = GuardedDispatchInvoke(
        array,
        member,
        propertyPut ? DISPATCH_PROPERTYPUT : DISPATCH_METHOD,
        &parameters,
        &exception,
        &argumentError);
    ClearException(&exception);
    VariantClear(&arguments[0]);
    VariantClear(&arguments[1]);
    return status;
}

bool MayUseDispatchFallback(const HRESULT status) noexcept {
    return status == DISP_E_MEMBERNOTFOUND ||
        status == DISP_E_TYPEMISMATCH ||
        status == E_NOINTERFACE;
}

bool MayUseOfficialInterfaceFallback(const HRESULT status) noexcept {
    return status == DISP_E_UNKNOWNNAME ||
        status == DISP_E_MEMBERNOTFOUND ||
        status == DISP_E_TYPEMISMATCH ||
        status == E_NOINTERFACE;
}

}

HRESULT ParameterArrayWriter::Bind(IDispatch* const array) noexcept {
    virtualInterface_.Release();
    dispatch_.Release();
    virtualSlot_ = 0;
    dispatchMember_ = DISPID_UNKNOWN;
    dispatchPropertyPut_ = false;
    if (array == nullptr) {
        return E_INVALIDARG;
    }
    static constexpr VARTYPE kParameterTypes[] = {VT_I4, VT_VARIANT};
    HRESULT status = ResolveVirtualMethod(
        array,
        L"SetItem",
        kParameterTypes,
        static_cast<USHORT>(std::size(kParameterTypes)),
        VT_VOID,
        virtualInterface_,
        &virtualSlot_);
    if (SUCCEEDED(status)) {
        return status;
    }
    if (!MayUseDispatchFallback(status)) {
        return status;
    }
    status = ResolveVirtualPropertyPut(
        array,
        L"Item",
        kParameterTypes,
        static_cast<USHORT>(std::size(kParameterTypes)),
        virtualInterface_,
        &virtualSlot_);
    if (SUCCEEDED(status)) {
        return status;
    }
    if (!MayUseDispatchFallback(status)) {
        return status;
    }
    status = GuardedGetMemberId(array, L"SetItem", &dispatchMember_);
    if (SUCCEEDED(status)) {
        dispatch_ = array;
        return status;
    }
    if (!MayUseOfficialInterfaceFallback(status)) {
        return status;
    }
    status = GuardedGetMemberId(array, L"Item", &dispatchMember_);
    if (SUCCEEDED(status)) {
        dispatch_ = array;
        dispatchPropertyPut_ = true;
        return status;
    }
    if (!MayUseOfficialInterfaceFallback(status)) {
        return status;
    }
    IUnknown* rawInterface = nullptr;
    status = GuardedQueryParameterArray(array, &rawInterface);
    if (SUCCEEDED(status)) {
        virtualInterface_.Attach(rawInterface);
        virtualSlot_ = kParameterArraySetItemSlot;
    }
    return status;
}

HRESULT ParameterArrayWriter::SetItem(
    const LONG index,
    const CComVariant& value) noexcept {
    if (index < 0) {
        return E_INVALIDARG;
    }
    if (virtualInterface_ != nullptr) {
        return GuardedArraySetItem(
            virtualInterface_,
            virtualSlot_,
            index,
            static_cast<const VARIANT&>(value));
    }
    if (dispatch_ != nullptr && dispatchMember_ != DISPID_UNKNOWN) {
        return GuardedDispatchSetItem(
            dispatch_,
            dispatchMember_,
            index,
            value,
            dispatchPropertyPut_);
    }
    return CO_E_NOTINITIALIZED;
}

HRESULT SetParameterArrayItem(
    IDispatch* const array,
    const LONG index,
    const CComVariant& value) noexcept {
    ParameterArrayWriter writer;
    HRESULT status = writer.Bind(array);
    if (SUCCEEDED(status)) {
        status = writer.SetItem(index, value);
    }
    return status;
}

}

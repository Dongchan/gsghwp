#include "OfficialApiAutomationInvoke.h"

#include "DispatchInvoke.h"
#include "OfficialApiAutomationArguments.h"

#include <atlbase.h>
#include <atlcomcli.h>
#include <ocidl.h>

#include <string>
#include <iterator>
#include <utility>
#include <vector>

namespace hancom::official_api {
namespace {

constexpr IID kDispatchEventIid = {
    0xF04A09A0,
    0xF319,
    0x40D5,
    {0x88, 0xCA, 0xCA, 0x43, 0x9C, 0xC6, 0x82, 0x0C},
};
constexpr IID kDualEventIid = {
    0xBFCD3DE1,
    0xDE0D,
    0x4E4F,
    {0xAB, 0xFB, 0x66, 0x4F, 0x52, 0x53, 0x35, 0xCB},
};
constexpr IID kHwpObjectIid = {
    0x5E6A8276,
    0xCF1C,
    0x42B8,
    {0xBC, 0xED, 0x31, 0x95, 0x48, 0xB0, 0x2A, 0xF6},
};
constexpr IID kParameterArrayIid = {
    0xCAE59B55,
    0x0F90,
    0x4E9B,
    {0x9F, 0x9E, 0x91, 0xCC, 0x67, 0x1B, 0x7A, 0x49},
};

HRESULT ExceptionStatus(const DWORD code) noexcept {
    return static_cast<HRESULT>(code | FACILITY_NT_BIT);
}

HRESULT GuardedQueryInterface(
    IDispatch* const target,
    REFIID iid,
    IUnknown** const result) noexcept {
    __try {
        return target->QueryInterface(iid, reinterpret_cast<void**>(result));
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

template<typename Function>
Function VtableFunction(IUnknown* const object, const size_t slot) noexcept {
    void** const table = *reinterpret_cast<void***>(object);
    return reinterpret_cast<Function>(table[slot]);
}

HRESULT GuardedStyleTransfer(
    IUnknown* const object,
    const size_t slot,
    IDispatch* const parameter,
    VARIANT_BOOL* const returned) noexcept {
    using StyleTransfer = HRESULT(STDMETHODCALLTYPE*)(
        IUnknown*, IDispatch*, VARIANT_BOOL*);
    __try {
        return VtableFunction<StyleTransfer>(object, slot)(
            object, parameter, returned);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

HRESULT GuardedArrayClone(
    IUnknown* const object,
    IDispatch** const clone) noexcept {
    using Clone = HRESULT(STDMETHODCALLTYPE*)(IUnknown*, IDispatch**);
    __try {
        return VtableFunction<Clone>(object, 10U)(object, clone);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

HRESULT GuardedArrayItem(
    IUnknown* const object,
    const LONG index,
    VARIANT* const value) noexcept {
    using Item = HRESULT(STDMETHODCALLTYPE*)(IUnknown*, LONG, VARIANT*);
    __try {
        return VtableFunction<Item>(object, 12U)(object, index, value);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

HRESULT GuardedArraySetItem(
    IUnknown* const object,
    const LONG index,
    const VARIANT value) noexcept {
    using SetItem = HRESULT(STDMETHODCALLTYPE*)(IUnknown*, LONG, VARIANT);
    __try {
        return VtableFunction<SetItem>(object, 13U)(object, index, value);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

HRESULT DirectStyleTransfer(
    IDispatch* const target,
    const std::wstring& member,
    const std::vector<CComVariant>& arguments,
    CComVariant* const value) noexcept {
    if (arguments.size() != 1 || value == nullptr) {
        return E_INVALIDARG;
    }
    CComPtr<IDispatch> parameter;
    HRESULT status = hancom::dispatch::AsDispatch(arguments[0], parameter);
    IUnknown* rawInterface = nullptr;
    if (SUCCEEDED(status)) {
        status = GuardedQueryInterface(target, kHwpObjectIid, &rawInterface);
    }
    CComPtr<IUnknown> interfaceObject;
    interfaceObject.Attach(rawInterface);
    if (FAILED(status)) {
        return status;
    }
    const size_t slot = member == L"ExportStyle" ? 80U : 81U;
    VARIANT_BOOL returned = VARIANT_FALSE;
    status = GuardedStyleTransfer(
        interfaceObject, slot, parameter, &returned);
    if (SUCCEEDED(status)) {
        value->Clear();
        value->vt = VT_BOOL;
        value->boolVal = returned;
    }
    return status;
}

HRESULT DirectParameterArray(
    IDispatch* const target,
    const std::wstring& member,
    const std::vector<CComVariant>& arguments,
    CComVariant* const value) noexcept {
    IUnknown* rawInterface = nullptr;
    HRESULT status = GuardedQueryInterface(
        target, kParameterArrayIid, &rawInterface);
    CComPtr<IUnknown> interfaceObject;
    interfaceObject.Attach(rawInterface);
    if (FAILED(status)) {
        return status;
    }
    if (member == L"Clone" && arguments.empty() && value != nullptr) {
        IDispatch* clone = nullptr;
        status = GuardedArrayClone(interfaceObject, &clone);
        if (SUCCEEDED(status)) {
            value->Clear();
            value->vt = VT_DISPATCH;
            value->pdispVal = clone;
        }
        return status;
    }
    LONG index = 0;
    if (arguments.empty() ||
        FAILED(hancom::dispatch::AsLong(arguments[0], &index))) {
        return E_INVALIDARG;
    }
    if (member == L"Item" && arguments.size() == 1 && value != nullptr) {
        value->Clear();
        status = GuardedArrayItem(interfaceObject, index, value);
        return status;
    }
    if (member == L"SetItem" && arguments.size() == 2) {
        return GuardedArraySetItem(
            interfaceObject,
            index,
            static_cast<const VARIANT&>(arguments[1]));
    }
    return E_INVALIDARG;
}

HRESULT DirectDualInterfaceFallback(
    IDispatch* const target,
    const std::wstring& owner,
    const std::wstring& member,
    const std::vector<CComVariant>& arguments,
    CComVariant* const value) noexcept {
    if (owner == L"IHwpObject" &&
        (member == L"ExportStyle" || member == L"ImportStyle")) {
        return DirectStyleTransfer(target, member, arguments, value);
    }
    if (owner == L"IDHwpParameterArray" &&
        (member == L"Clone" || member == L"Item" || member == L"SetItem")) {
        return DirectParameterArray(target, member, arguments, value);
    }
    return E_NOTIMPL;
}

LONG EventDispid(const std::wstring& member) noexcept {
    static constexpr const wchar_t* kEvents[] = {
        L"Quit",
        L"CreateXHwpWindow",
        L"CloseXHwpWindow",
        L"NewDocument",
        L"DocumentBeforeClose",
        L"DocumentBeforeOpen",
        L"DocumentAfterOpen",
        L"DocumentBeforeSave",
        L"DocumentAfterSave",
        L"DocumentAfterClose",
        L"DocumentChange",
        L"DocumentBeforePrint",
        L"DocumentAfterPrint",
        L"DocumentClickedHyperlink",
        L"DocumentModifiedHyperlink",
        L"BeforeQuit",
    };
    for (size_t index = 0; index < std::size(kEvents); ++index) {
        if (member == kEvents[index]) {
            return static_cast<LONG>(index + 1);
        }
    }
    return DISPID_UNKNOWN;
}

HRESULT ValidateAutomationEvent(
    IDispatch* const target,
    const std::wstring& member,
    CComVariant* const value) noexcept {
    const LONG dispid = EventDispid(member);
    if (dispid == DISPID_UNKNOWN) {
        return DISP_E_UNKNOWNNAME;
    }
    CComQIPtr<IConnectionPointContainer> container(target);
    if (container == nullptr) {
        return E_NOINTERFACE;
    }
    CComPtr<IConnectionPoint> connectionPoint;
    HRESULT status = container->FindConnectionPoint(
        kDispatchEventIid,
        &connectionPoint);
    if (FAILED(status)) {
        status = container->FindConnectionPoint(
            kDualEventIid,
            &connectionPoint);
    }
    if (SUCCEEDED(status) && value != nullptr) {
        *value = CComVariant(dispid);
    }
    return status;
}

std::wstring RuntimeAutomationMember(
    const std::wstring& member) {
    if (member == L"RenameMetatag") {
        return L"SetCurMetatagName";
    }
    if (member == L"ItemExsit") {
        return L"ItemExist";
    }
    if (member == L"Heigh") {
        return L"Height";
    }
    if (member == L"GetSetID") {
        return L"SetID";
    }
    return member;
}

HRESULT ValidateWriteOnlyProperty(
    IDispatch* const target,
    const std::wstring& member,
    CComVariant* const value) noexcept {
    LPOLESTR name = const_cast<LPOLESTR>(member.c_str());
    DISPID dispid = DISPID_UNKNOWN;
    const HRESULT status = target->GetIDsOfNames(
        IID_NULL,
        &name,
        1,
        LOCALE_USER_DEFAULT,
        &dispid);
    if (SUCCEEDED(status) && value != nullptr) {
        *value = CComVariant(static_cast<LONG>(dispid));
    }
    return status;
}

bool VoidAutomationMethod(
    const std::wstring& owner,
    const std::wstring& member) noexcept {
    return (owner == L"IHwpObject" && member == L"Run") ||
        (owner == L"IDHwpAction" && member == L"Run") ||
        (owner == L"IDHwpParameterArray" && member == L"SetItem");
}

HRESULT WindowDocumentsFallback(
    IDispatch* const window,
    CComVariant* const value) noexcept {
    CComVariant rawApplication;
    HRESULT status = hancom::dispatch::PropertyGet(
        window, L"Application", &rawApplication);
    CComPtr<IDispatch> application;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawApplication, application);
    }
    return SUCCEEDED(status)
        ? hancom::dispatch::PropertyGet(application, L"XHwpDocuments", value)
        : status;
}

}

AutomationInvocation InvokeAutomationMember(
    IDispatch* const hwp,
    IDispatch* const target,
    const std::wstring& owner,
    const std::wstring& member,
    const std::wstring& kind,
    const std::vector<std::wstring>& argumentLines) {
    AutomationInvocation result;
    if (hwp == nullptr || target == nullptr) {
        result.argumentStatus = E_POINTER;
        return result;
    }

    PreparedAutomationArguments prepared;
    result.argumentStatus = prepared.Parse(hwp, target, argumentLines);
    if (FAILED(result.argumentStatus)) {
        return result;
    }
    const std::vector<CComVariant> arguments = prepared.Values();
    const std::wstring runtimeMember = RuntimeAutomationMember(member);
    if (kind == L"property") {
        if (member == L"Password") {
            result.invokeStatus = ValidateWriteOnlyProperty(
                target,
                runtimeMember,
                &result.value);
            result.writeStatus = SUCCEEDED(result.invokeStatus) ? S_FALSE : E_NOTIMPL;
            result.outputs = prepared.Outputs();
            return result;
        }
        result.invokeStatus = hancom::dispatch::Invoke(
            target,
            runtimeMember.c_str(),
            DISPATCH_PROPERTYGET,
            arguments,
            &result.value);
        if (FAILED(result.invokeStatus)) {
            result.invokeStatus = hancom::dispatch::Invoke(
                target,
                runtimeMember.c_str(),
                DISPATCH_METHOD | DISPATCH_PROPERTYGET,
                arguments,
                &result.value);
        }
        if (FAILED(result.invokeStatus) && owner == L"IXHwpWindow" &&
            member == L"XHwpDocuments") {
            const HRESULT directStatus = result.invokeStatus;
            result.invokeStatus = WindowDocumentsFallback(target, &result.value);
            if (SUCCEEDED(result.invokeStatus)) {
                result.writeStatus = directStatus;
                result.outputs = prepared.Outputs();
                return result;
            }
        }
        if (SUCCEEDED(result.invokeStatus)) {
            std::vector<CComVariant> putArguments(arguments);
            putArguments.push_back(result.value);
            result.writeStatus = hancom::dispatch::Invoke(
                target,
                runtimeMember.c_str(),
                DISPATCH_PROPERTYPUT,
                putArguments,
                nullptr);
            if (FAILED(result.writeStatus) &&
                (result.value.vt == VT_DISPATCH || result.value.vt == VT_UNKNOWN)) {
                result.writeStatus = hancom::dispatch::Invoke(
                    target,
                    runtimeMember.c_str(),
                    DISPATCH_PROPERTYPUTREF,
                    putArguments,
                    nullptr);
            }
        }
    } else if (kind == L"method") {
        LONG tabsBefore = -1;
        if (owner == L"IXHwpTabs" && member == L"Add" && arguments.empty()) {
            CComVariant rawCount;
            if (SUCCEEDED(hancom::dispatch::PropertyGet(
                    target, L"Count", &rawCount))) {
                static_cast<void>(hancom::dispatch::AsLong(rawCount, &tabsBefore));
            }
        }
        CComVariant* const invokeValue = VoidAutomationMethod(owner, member)
            ? nullptr
            : &result.value;
        result.invokeStatus = hancom::dispatch::Invoke(
            target,
            runtimeMember.c_str(),
            DISPATCH_METHOD,
            arguments,
            invokeValue);
        if (FAILED(result.invokeStatus)) {
            result.invokeStatus = hancom::dispatch::Invoke(
                target,
                runtimeMember.c_str(),
                DISPATCH_METHOD | DISPATCH_PROPERTYGET,
                arguments,
                invokeValue);
        }
        if (FAILED(result.invokeStatus)) {
            const HRESULT dispatchStatus = result.invokeStatus;
            const HRESULT directStatus = DirectDualInterfaceFallback(
                target,
                owner,
                member,
                arguments,
                invokeValue);
            if (SUCCEEDED(directStatus)) {
                result.invokeStatus = directStatus;
                result.writeStatus = dispatchStatus;
            }
        }
        if (FAILED(result.invokeStatus) && tabsBefore >= 0) {
            CComVariant rawCount;
            LONG tabsAfter = -1;
            if (SUCCEEDED(hancom::dispatch::PropertyGet(
                    target, L"Count", &rawCount)) &&
                SUCCEEDED(hancom::dispatch::AsLong(rawCount, &tabsAfter)) &&
                tabsAfter > tabsBefore) {
                result.writeStatus = result.invokeStatus;
                result.invokeStatus = S_OK;
                result.value = CComVariant(tabsAfter);
            }
        }
    } else if (kind == L"event") {
        result.invokeStatus = ValidateAutomationEvent(
            target,
            member,
            &result.value);
    } else {
        result.invokeStatus = E_INVALIDARG;
    }
    result.outputs = prepared.Outputs();
    return result;
}

}

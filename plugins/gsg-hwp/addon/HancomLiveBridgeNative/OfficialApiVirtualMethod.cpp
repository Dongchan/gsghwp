#include "OfficialApiVirtualMethod.h"

#include <OleAuto.h>

#include <cwchar>

namespace hancom::official_api {
namespace {

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

bool MatchesSignature(
    const FUNCDESC& description,
    const INVOKEKIND invokeKind,
    const VARTYPE* const parameterTypes,
    const USHORT parameterCount,
    const VARTYPE returnType) noexcept {
    if (description.invkind != invokeKind ||
        description.cParams != static_cast<SHORT>(parameterCount) ||
        description.elemdescFunc.tdesc.vt != returnType ||
        description.oVft < static_cast<SHORT>(7U * sizeof(void*)) ||
        description.oVft % static_cast<SHORT>(sizeof(void*)) != 0) {
        return false;
    }
    for (USHORT index = 0; index < parameterCount; ++index) {
        if (description.lprgelemdescParam[index].tdesc.vt != parameterTypes[index]) {
            return false;
        }
    }
    return true;
}

bool MemberMatches(
    ITypeInfo* const typeInfo,
    const MEMBERID memberId,
    const wchar_t* const member) noexcept {
    CComBSTR name;
    const HRESULT status = typeInfo->GetDocumentation(
        memberId,
        &name,
        nullptr,
        nullptr,
        nullptr);
    return SUCCEEDED(status) && name != nullptr &&
        std::wcscmp(name, member) == 0;
}

HRESULT ResolveVirtualMember(
    IDispatch* const target,
    const wchar_t* const member,
    const INVOKEKIND invokeKind,
    const VARTYPE* const parameterTypes,
    const USHORT parameterCount,
    const VARTYPE returnType,
    CComPtr<IUnknown>& interfaceObject,
    size_t* const slot) noexcept {
    interfaceObject.Release();
    if (target == nullptr || member == nullptr || parameterTypes == nullptr ||
        parameterCount == 0U || slot == nullptr) {
        return E_INVALIDARG;
    }
    *slot = 0;

    UINT typeInfoCount = 0;
    HRESULT status = target->GetTypeInfoCount(&typeInfoCount);
    if (FAILED(status) || typeInfoCount == 0U) {
        return FAILED(status) ? status : E_NOINTERFACE;
    }
    CComPtr<ITypeInfo> typeInfo;
    status = target->GetTypeInfo(
        0U,
        LOCALE_USER_DEFAULT,
        &typeInfo);
    if (FAILED(status) || typeInfo == nullptr) {
        return FAILED(status) ? status : E_NOINTERFACE;
    }

    TYPEATTR* attribute = nullptr;
    status = typeInfo->GetTypeAttr(&attribute);
    if (FAILED(status) || attribute == nullptr) {
        return FAILED(status) ? status : E_NOINTERFACE;
    }
    const IID interfaceId = attribute->guid;
    const UINT functionCount = attribute->cFuncs;
    typeInfo->ReleaseTypeAttr(attribute);

    HRESULT signatureStatus = DISP_E_MEMBERNOTFOUND;
    size_t resolvedSlot = 0;
    for (UINT index = 0; index < functionCount; ++index) {
        FUNCDESC* description = nullptr;
        status = typeInfo->GetFuncDesc(index, &description);
        if (FAILED(status) || description == nullptr) {
            continue;
        }
        if (MemberMatches(typeInfo, description->memid, member)) {
            signatureStatus = DISP_E_TYPEMISMATCH;
            if (MatchesSignature(
                    *description,
                    invokeKind,
                    parameterTypes,
                    parameterCount,
                    returnType)) {
                resolvedSlot =
                    static_cast<size_t>(description->oVft) / sizeof(void*);
                signatureStatus = S_OK;
            }
        }
        typeInfo->ReleaseFuncDesc(description);
        if (SUCCEEDED(signatureStatus)) {
            break;
        }
    }
    if (FAILED(signatureStatus)) {
        return signatureStatus;
    }

    IUnknown* rawInterface = nullptr;
    status = GuardedQueryInterface(
        target,
        interfaceId,
        &rawInterface);
    if (SUCCEEDED(status)) {
        interfaceObject.Attach(rawInterface);
        *slot = resolvedSlot;
    }
    return status;
}

}

HRESULT ResolveVirtualMethod(
    IDispatch* const target,
    const wchar_t* const member,
    const VARTYPE* const parameterTypes,
    const USHORT parameterCount,
    const VARTYPE returnType,
    CComPtr<IUnknown>& interfaceObject,
    size_t* const slot) noexcept {
    return ResolveVirtualMember(
        target,
        member,
        INVOKE_FUNC,
        parameterTypes,
        parameterCount,
        returnType,
        interfaceObject,
        slot);
}

HRESULT ResolveVirtualPropertyPut(
    IDispatch* const target,
    const wchar_t* const member,
    const VARTYPE* const parameterTypes,
    const USHORT parameterCount,
    CComPtr<IUnknown>& interfaceObject,
    size_t* const slot) noexcept {
    return ResolveVirtualMember(
        target,
        member,
        INVOKE_PROPERTYPUT,
        parameterTypes,
        parameterCount,
        VT_VOID,
        interfaceObject,
        slot);
}

}

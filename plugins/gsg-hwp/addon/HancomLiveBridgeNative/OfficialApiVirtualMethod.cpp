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

HRESULT GuardedVirtualPropertyGet(
    IUnknown* const interfaceObject,
    const size_t slot,
    IDispatch** const value) noexcept {
    __try {
        void** const vtable =
            *reinterpret_cast<void***>(interfaceObject);
        if (vtable == nullptr || vtable[slot] == nullptr) {
            return E_NOINTERFACE;
        }
        using PropertyGet =
            HRESULT(STDMETHODCALLTYPE*)(IUnknown*, IDispatch**);
        const auto getter =
            reinterpret_cast<PropertyGet>(vtable[slot]);
        return getter(interfaceObject, value);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

HRESULT GuardedVirtualUnsignedLongPropertyGet(
    IUnknown* const interfaceObject,
    const size_t slot,
    ULONG* const value) noexcept {
    __try {
        void** const vtable =
            *reinterpret_cast<void***>(interfaceObject);
        if (vtable == nullptr || vtable[slot] == nullptr) {
            return E_NOINTERFACE;
        }
        using PropertyGet =
            HRESULT(STDMETHODCALLTYPE*)(IUnknown*, ULONG*);
        const auto getter =
            reinterpret_cast<PropertyGet>(vtable[slot]);
        return getter(interfaceObject, value);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return ExceptionStatus(GetExceptionCode());
    }
}

bool HasWin32TypeLibrary(ITypeInfo* const typeInfo) noexcept {
    CComPtr<ITypeLib> library;
    UINT index = 0;
    if (FAILED(typeInfo->GetContainingTypeLib(&library, &index)) ||
        library == nullptr) {
        return false;
    }
    TLIBATTR* attribute = nullptr;
    const HRESULT status = library->GetLibAttr(&attribute);
    if (FAILED(status) || attribute == nullptr) {
        return false;
    }
    const bool win32 = attribute->syskind == SYS_WIN32;
    library->ReleaseTLibAttr(attribute);
    return win32;
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

bool MatchesHiddenDualSignature(
    const FUNCDESC& description,
    const INVOKEKIND invokeKind,
    const VARTYPE* const parameterTypes,
    const USHORT parameterCount,
    const VARTYPE returnType) noexcept {
    const bool hasReturnValue = returnType != VT_VOID;
    const USHORT expectedParameterCount = static_cast<USHORT>(
        parameterCount + (hasReturnValue ? 1U : 0U));
    if (description.invkind != invokeKind ||
        description.funckind != FUNC_PUREVIRTUAL ||
        description.cParams !=
            static_cast<SHORT>(expectedParameterCount) ||
        description.elemdescFunc.tdesc.vt != VT_HRESULT ||
        description.oVft < static_cast<SHORT>(7U * sizeof(void*)) ||
        description.oVft % static_cast<SHORT>(sizeof(void*)) != 0 ||
        (expectedParameterCount != 0U &&
         description.lprgelemdescParam == nullptr)) {
        return false;
    }
    for (USHORT index = 0; index < parameterCount; ++index) {
        if (description.lprgelemdescParam[index].tdesc.vt !=
            parameterTypes[index]) {
            return false;
        }
    }
    if (!hasReturnValue) {
        return true;
    }
    const ELEMDESC& result =
        description.lprgelemdescParam[parameterCount];
    constexpr USHORT resultFlags = PARAMFLAG_FOUT | PARAMFLAG_FRETVAL;
    return (result.paramdesc.wParamFlags & resultFlags) == resultFlags &&
        result.tdesc.vt == VT_PTR &&
        result.tdesc.lptdesc != nullptr &&
        result.tdesc.lptdesc->vt == returnType;
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
    if (target == nullptr || member == nullptr || slot == nullptr ||
        (parameterCount != 0U && parameterTypes == nullptr)) {
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
    const IID dispatchInterfaceId = attribute->guid;
    const bool hasHiddenDual =
        attribute->typekind == TKIND_DISPATCH &&
        (attribute->wTypeFlags & TYPEFLAG_FDUAL) != 0;
    IID interfaceId = attribute->guid;
    UINT functionCount = attribute->cFuncs;
    typeInfo->ReleaseTypeAttr(attribute);

    CComPtr<ITypeInfo> callableTypeInfo = typeInfo;
    if (hasHiddenDual) {
        HREFTYPE reference = 0;
        status = typeInfo->GetRefTypeOfImplType(
            static_cast<UINT>(-1),
            &reference);
        if (FAILED(status)) {
            return status;
        }
        CComPtr<ITypeInfo> hiddenTypeInfo;
        status = typeInfo->GetRefTypeInfo(reference, &hiddenTypeInfo);
        if (FAILED(status) || hiddenTypeInfo == nullptr) {
            return FAILED(status) ? status : E_NOINTERFACE;
        }
        TYPEATTR* hiddenAttribute = nullptr;
        status = hiddenTypeInfo->GetTypeAttr(&hiddenAttribute);
        if (FAILED(status) || hiddenAttribute == nullptr) {
            return FAILED(status) ? status : E_NOINTERFACE;
        }
        const bool callable =
            hiddenAttribute->typekind == TKIND_INTERFACE &&
            InlineIsEqualGUID(
                hiddenAttribute->guid, dispatchInterfaceId);
        interfaceId = hiddenAttribute->guid;
        functionCount = hiddenAttribute->cFuncs;
        hiddenTypeInfo->ReleaseTypeAttr(hiddenAttribute);
        if (!callable) {
            return TYPE_E_WRONGTYPEKIND;
        }
        if (!HasWin32TypeLibrary(hiddenTypeInfo)) {
            return TYPE_E_WRONGTYPEKIND;
        }
        callableTypeInfo = hiddenTypeInfo;
    }

    HRESULT signatureStatus = DISP_E_MEMBERNOTFOUND;
    size_t resolvedSlot = 0;
    for (UINT index = 0; index < functionCount; ++index) {
        FUNCDESC* description = nullptr;
        status = callableTypeInfo->GetFuncDesc(index, &description);
        if (FAILED(status) || description == nullptr) {
            continue;
        }
        if (MemberMatches(
                callableTypeInfo, description->memid, member)) {
            signatureStatus = DISP_E_TYPEMISMATCH;
            const bool matches = hasHiddenDual
                ? MatchesHiddenDualSignature(
                      *description,
                      invokeKind,
                      parameterTypes,
                      parameterCount,
                      returnType)
                : MatchesSignature(
                      *description,
                      invokeKind,
                      parameterTypes,
                      parameterCount,
                      returnType);
            if (matches) {
                resolvedSlot =
                    static_cast<size_t>(description->oVft) / sizeof(void*);
                signatureStatus = S_OK;
            }
        }
        callableTypeInfo->ReleaseFuncDesc(description);
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

HRESULT ResolveVirtualPropertyGet(
    IDispatch* const target,
    const wchar_t* const member,
    const VARTYPE returnType,
    CComPtr<IUnknown>& interfaceObject,
    size_t* const slot) noexcept {
    return ResolveVirtualMember(
        target,
        member,
        INVOKE_PROPERTYGET,
        nullptr,
        0U,
        returnType,
        interfaceObject,
        slot);
}

HRESULT InvokeResolvedVirtualPropertyGet(
    IDispatch* const target,
    REFIID interfaceId,
    const size_t slot,
    CComPtr<IDispatch>& value) noexcept {
    value.Release();
    if (target == nullptr) {
        return E_INVALIDARG;
    }
#if !defined(_M_IX86)
    static_cast<void>(interfaceId);
    static_cast<void>(slot);
    return E_NOTIMPL;
#else
    IUnknown* rawInterface = nullptr;
    HRESULT status =
        GuardedQueryInterface(target, interfaceId, &rawInterface);
    if (FAILED(status)) {
        return status;
    }
    CComPtr<IUnknown> interfaceObject;
    interfaceObject.Attach(rawInterface);

    IDispatch* rawValue = nullptr;
    status =
        GuardedVirtualPropertyGet(interfaceObject.p, slot, &rawValue);
    if (FAILED(status)) {
        if (rawValue != nullptr) {
            rawValue->Release();
        }
        return status;
    }
    if (rawValue == nullptr) {
        return E_POINTER;
    }
    value.Attach(rawValue);
    return S_OK;
#endif
}

HRESULT InvokeResolvedVirtualUnsignedLongPropertyGet(
    IDispatch* const target,
    REFIID interfaceId,
    const size_t slot,
    ULONG* const value) noexcept {
    if (target == nullptr || value == nullptr) {
        return E_INVALIDARG;
    }
#if !defined(_M_IX86)
    static_cast<void>(interfaceId);
    static_cast<void>(slot);
    return E_NOTIMPL;
#else
    IUnknown* rawInterface = nullptr;
    const HRESULT queryStatus =
        GuardedQueryInterface(target, interfaceId, &rawInterface);
    if (FAILED(queryStatus)) {
        return queryStatus;
    }
    CComPtr<IUnknown> interfaceObject;
    interfaceObject.Attach(rawInterface);
    return GuardedVirtualUnsignedLongPropertyGet(
        interfaceObject.p, slot, value);
#endif
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

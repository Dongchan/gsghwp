#pragma once

#include <Windows.h>
#include <OleAuto.h>

#include <cwchar>

class FakeVirtualTypeInfo final : public ITypeInfo {
public:
    enum class Signature {
        CreateArray,
        SetArrayItem,
        SetIndexedProperty,
    };

    FakeVirtualTypeInfo(
        const wchar_t* const memberName,
        const MEMBERID memberId,
        const UINT slot,
        const Signature signature) noexcept
        : memberName_(memberName),
          memberId_(memberId),
          slot_(slot),
          signature_(signature) {
        static_cast<void>(CoCreateGuid(&interfaceId_));
    }

    const IID& InterfaceId() const noexcept {
        return interfaceId_;
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid,
        void** const object) override {
        if (object == nullptr) {
            return E_POINTER;
        }
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_ITypeInfo) {
            *object = static_cast<ITypeInfo*>(this);
            static_cast<void>(AddRef());
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }

    ULONG STDMETHODCALLTYPE Release() override {
        const LONG remaining = InterlockedDecrement(&references_);
        if (remaining == 0) {
            delete this;
        }
        return static_cast<ULONG>(remaining);
    }

    HRESULT STDMETHODCALLTYPE GetTypeAttr(TYPEATTR** const attribute) override {
        if (attribute == nullptr) {
            return E_POINTER;
        }
        *attribute = static_cast<TYPEATTR*>(CoTaskMemAlloc(sizeof(TYPEATTR)));
        if (*attribute == nullptr) {
            return E_OUTOFMEMORY;
        }
        ZeroMemory(*attribute, sizeof(TYPEATTR));
        (*attribute)->guid = interfaceId_;
        (*attribute)->typekind = TKIND_INTERFACE;
        (*attribute)->cFuncs = 1;
        (*attribute)->cbSizeVft = static_cast<WORD>((slot_ + 1U) * sizeof(void*));
        (*attribute)->wTypeFlags = TYPEFLAG_FDUAL | TYPEFLAG_FOLEAUTOMATION;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetTypeComp(ITypeComp**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetFuncDesc(
        const UINT index,
        FUNCDESC** const description) override {
        if (description == nullptr) {
            return E_POINTER;
        }
        *description = nullptr;
        if (index != 0U) {
            return TYPE_E_ELEMENTNOTFOUND;
        }
        FUNCDESC* const value =
            static_cast<FUNCDESC*>(CoTaskMemAlloc(sizeof(FUNCDESC)));
        ELEMDESC* const parameters =
            static_cast<ELEMDESC*>(CoTaskMemAlloc(2U * sizeof(ELEMDESC)));
        if (value == nullptr || parameters == nullptr) {
            CoTaskMemFree(value);
            CoTaskMemFree(parameters);
            return E_OUTOFMEMORY;
        }
        ZeroMemory(value, sizeof(FUNCDESC));
        ZeroMemory(parameters, 2U * sizeof(ELEMDESC));
        value->memid = memberId_;
        value->lprgelemdescParam = parameters;
        value->funckind = FUNC_VIRTUAL;
        value->invkind =
            signature_ == Signature::SetIndexedProperty
            ? INVOKE_PROPERTYPUT
            : INVOKE_FUNC;
        value->callconv = CC_STDCALL;
        value->cParams = 2;
        value->oVft = static_cast<SHORT>(slot_ * sizeof(void*));
        value->elemdescFunc.tdesc.vt = VT_VOID;
        parameters[0].paramdesc.wParamFlags = PARAMFLAG_FIN;
        parameters[1].paramdesc.wParamFlags = PARAMFLAG_FIN;
        if (signature_ == Signature::CreateArray) {
            parameters[0].tdesc.vt = VT_BSTR;
            parameters[1].tdesc.vt = VT_I4;
        } else {
            parameters[0].tdesc.vt = VT_I4;
            parameters[1].tdesc.vt = VT_VARIANT;
        }
        *description = value;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetVarDesc(UINT, VARDESC**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetNames(
        const MEMBERID member,
        BSTR* const names,
        const UINT capacity,
        UINT* const count) override {
        if (names == nullptr || count == nullptr || capacity == 0U) {
            return E_INVALIDARG;
        }
        *count = 0;
        if (member != memberId_) {
            return TYPE_E_ELEMENTNOTFOUND;
        }
        names[0] = SysAllocString(memberName_);
        if (names[0] == nullptr) {
            return E_OUTOFMEMORY;
        }
        *count = 1;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetRefTypeOfImplType(UINT, HREFTYPE*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetImplTypeFlags(UINT, INT*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        LPOLESTR* const names,
        const UINT count,
        MEMBERID* const members) override {
        if (names == nullptr || members == nullptr || count != 1U) {
            return E_INVALIDARG;
        }
        if (std::wcscmp(names[0], memberName_) != 0) {
            return DISP_E_UNKNOWNNAME;
        }
        members[0] = memberId_;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE Invoke(
        PVOID,
        MEMBERID,
        WORD,
        DISPPARAMS*,
        VARIANT*,
        EXCEPINFO*,
        UINT*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetDocumentation(
        const MEMBERID member,
        BSTR* const name,
        BSTR* const documentation,
        DWORD* const helpContext,
        BSTR* const helpFile) override {
        if (documentation != nullptr) {
            *documentation = nullptr;
        }
        if (helpContext != nullptr) {
            *helpContext = 0;
        }
        if (helpFile != nullptr) {
            *helpFile = nullptr;
        }
        if (member != memberId_) {
            return TYPE_E_ELEMENTNOTFOUND;
        }
        if (name != nullptr) {
            *name = SysAllocString(memberName_);
            if (*name == nullptr) {
                return E_OUTOFMEMORY;
            }
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetDllEntry(
        MEMBERID,
        INVOKEKIND,
        BSTR*,
        BSTR*,
        WORD*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetRefTypeInfo(HREFTYPE, ITypeInfo**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE AddressOfMember(
        MEMBERID,
        INVOKEKIND,
        PVOID*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE CreateInstance(
        IUnknown*,
        REFIID,
        PVOID*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetMops(MEMBERID, BSTR*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetContainingTypeLib(
        ITypeLib**,
        UINT*) override {
        return E_NOTIMPL;
    }

    void STDMETHODCALLTYPE ReleaseTypeAttr(TYPEATTR* const attribute) override {
        CoTaskMemFree(attribute);
    }

    void STDMETHODCALLTYPE ReleaseFuncDesc(FUNCDESC* const description) override {
        if (description != nullptr) {
            CoTaskMemFree(description->lprgelemdescParam);
        }
        CoTaskMemFree(description);
    }

    void STDMETHODCALLTYPE ReleaseVarDesc(VARDESC* const description) override {
        CoTaskMemFree(description);
    }

private:
    ~FakeVirtualTypeInfo() = default;

    volatile LONG references_ = 1;
    IID interfaceId_{};
    const wchar_t* memberName_;
    MEMBERID memberId_;
    UINT slot_;
    Signature signature_;
};

#pragma once

#include <Windows.h>
#include <OleAuto.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include "FakeVirtualTypeInfo.h"

#include <map>
#include <string>

namespace fake_parameter_array {

struct ParameterSetInterface : IDispatch {
    virtual HRESULT STDMETHODCALLTYPE Slot7() = 0;
    virtual HRESULT STDMETHODCALLTYPE Slot8() = 0;
    virtual HRESULT STDMETHODCALLTYPE CreateItemArray(
        BSTR name,
        LONG count) = 0;
};

struct ParameterArrayInterface : IDispatch {
    virtual HRESULT STDMETHODCALLTYPE Slot7() = 0;
    virtual HRESULT STDMETHODCALLTYPE Slot8() = 0;
    virtual HRESULT STDMETHODCALLTYPE Item(LONG index, VARIANT* value) = 0;
    virtual HRESULT STDMETHODCALLTYPE PutItem(LONG index, VARIANT value) = 0;
};

struct DispatchPropertyInterface : IDispatch {
    virtual HRESULT STDMETHODCALLTYPE Slot7() = 0;
    virtual HRESULT STDMETHODCALLTYPE Slot8() = 0;
    virtual HRESULT STDMETHODCALLTYPE HAction(IDispatch** value) = 0;
};

template<typename Interface>
class OfficialDispatch : public Interface {
public:
    OfficialDispatch(
        const wchar_t* const memberName,
        const MEMBERID memberId,
        const UINT slot,
        const FakeVirtualTypeInfo::Signature signature) noexcept
        : typeInfo_(new FakeVirtualTypeInfo(
              memberName,
              memberId,
              slot,
              signature)) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid,
        void** const object) override {
        if (object == nullptr) {
            return E_POINTER;
        }
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_IDispatch ||
            iid == typeInfo_->InterfaceId()) {
            *object = static_cast<Interface*>(this);
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

    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* const count) override {
        if (count == nullptr) {
            return E_POINTER;
        }
        *count = 1;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfo(
        const UINT index,
        LCID,
        ITypeInfo** const typeInfo) override {
        if (typeInfo == nullptr) {
            return E_POINTER;
        }
        *typeInfo = nullptr;
        if (index != 0U) {
            return DISP_E_BADINDEX;
        }
        *typeInfo = typeInfo_;
        static_cast<void>(typeInfo_->AddRef());
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID,
        LPOLESTR*,
        UINT,
        LCID,
        DISPID*) override {
        return DISP_E_UNKNOWNNAME;
    }

    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID,
        REFIID,
        LCID,
        WORD,
        DISPPARAMS*,
        VARIANT*,
        EXCEPINFO*,
        UINT*) override {
        return DISP_E_MEMBERNOTFOUND;
    }

protected:
    virtual ~OfficialDispatch() {
        static_cast<void>(typeInfo_->Release());
    }

private:
    volatile LONG references_ = 1;
    FakeVirtualTypeInfo* typeInfo_;
};

class ParameterArray final : public OfficialDispatch<ParameterArrayInterface> {
public:
    ParameterArray() noexcept
        : OfficialDispatch(
              L"Item",
              2,
              10U,
              FakeVirtualTypeInfo::Signature::SetIndexedProperty) {}

    HRESULT STDMETHODCALLTYPE Slot7() override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Slot8() override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Item(LONG, VARIANT*) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE PutItem(const LONG index, VARIANT) override {
        return index >= 0 ? S_OK : E_INVALIDARG;
    }

};

}

class FakeVirtualPropertyGetDispatch final
    : public fake_parameter_array::OfficialDispatch<
          fake_parameter_array::DispatchPropertyInterface> {
public:
    FakeVirtualPropertyGetDispatch()
        : OfficialDispatch(
              L"HAction",
              5,
              9U,
              FakeVirtualTypeInfo::Signature::GetDispatchProperty) {}

    HRESULT STDMETHODCALLTYPE Slot7() override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Slot8() override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE HAction(IDispatch** const value) override {
        if (value == nullptr) {
            return E_POINTER;
        }
        *value = this;
        static_cast<void>(AddRef());
        return S_OK;
    }
};

class FakeParameterArrayDispatch final
    : public fake_parameter_array::OfficialDispatch<
          fake_parameter_array::ParameterSetInterface> {
public:
    FakeParameterArrayDispatch()
        : OfficialDispatch(
              L"CreateItemArray",
              15000,
              9U,
              FakeVirtualTypeInfo::Signature::CreateArray),
          array_(new fake_parameter_array::ParameterArray()) {}

    HRESULT STDMETHODCALLTYPE Slot7() override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Slot8() override { return E_NOTIMPL; }

    void EnableGenericProperties(const bool enabled) noexcept {
        genericProperties_ = enabled;
        if (!enabled) {
            genericValues_.clear();
            genericValueReads_ = 0;
        }
    }

    void ClearGenericValues() noexcept {
        genericValues_.clear();
    }

    void SetGenericLong(const std::wstring& name, const LONG value) {
        genericValues_[name] = CComVariant(value);
    }

    bool TryGetLong(const std::wstring& name, LONG* const value) const noexcept {
        if (value == nullptr) {
            return false;
        }
        const auto found = genericValues_.find(name);
        if (found == genericValues_.end()) {
            return false;
        }
        CComVariant converted(found->second);
        if (FAILED(converted.ChangeType(VT_I4))) {
            return false;
        }
        *value = converted.lVal;
        return true;
    }

    size_t GenericValueReads() const noexcept {
        return genericValueReads_;
    }

    void ResetGenericValueReads() noexcept {
        genericValueReads_ = 0;
    }

    HRESULT STDMETHODCALLTYPE CreateItemArray(
        BSTR const name,
        const LONG count) override {
        if (name == nullptr || count < 1) {
            return E_INVALIDARG;
        }
        arrayName_.assign(name, SysStringLen(name));
        arrayCreated_ = true;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID,
        LPOLESTR* const names,
        const UINT count,
        LCID,
        DISPID* const members) override {
        if (names == nullptr || members == nullptr || count != 1U) {
            return E_INVALIDARG;
        }
        const std::wstring name(names[0]);
        if (name == L"HSet") {
            members[0] = 1;
            return S_OK;
        }
        if (arrayCreated_ && name == arrayName_) {
            members[0] = 2;
            return S_OK;
        }
        if (genericProperties_) {
            const auto found = genericMembers_.find(name);
            if (found != genericMembers_.end()) {
                members[0] = found->second;
                return S_OK;
            }
            const DISPID member = nextGenericMember_++;
            genericMembers_.emplace(name, member);
            genericNames_.emplace(member, name);
            members[0] = member;
            return S_OK;
        }
        return DISP_E_UNKNOWNNAME;
    }

    HRESULT STDMETHODCALLTYPE Invoke(
        const DISPID member,
        REFIID,
        LCID,
        const WORD flags,
        DISPPARAMS* const parameters,
        VARIANT* const result,
        EXCEPINFO*,
        UINT*) override {
        const auto genericName = genericNames_.find(member);
        if ((flags & (DISPATCH_PROPERTYPUT | DISPATCH_PROPERTYPUTREF)) != 0 &&
            genericName != genericNames_.end() && genericProperties_) {
            if (parameters == nullptr || parameters->cArgs != 1) {
                return DISP_E_BADPARAMCOUNT;
            }
            genericValues_[genericName->second] =
                CComVariant(parameters->rgvarg[0]);
            return S_OK;
        }
        if ((flags & DISPATCH_PROPERTYGET) == 0 || result == nullptr) {
            return DISP_E_MEMBERNOTFOUND;
        }
        VariantInit(result);
        result->vt = VT_DISPATCH;
        if (member == 1) {
            result->pdispVal = this;
            static_cast<void>(AddRef());
            return S_OK;
        }
        if (member == 2 && arrayCreated_) {
            result->pdispVal = array_;
            static_cast<void>(array_->AddRef());
            return S_OK;
        }
        if (genericName != genericNames_.end() && genericProperties_) {
            const auto value = genericValues_.find(genericName->second);
            if (value != genericValues_.end()) {
                ++genericValueReads_;
                return VariantCopy(
                    result,
                    const_cast<VARIANT*>(
                        static_cast<const VARIANT*>(&value->second)));
            }
            result->pdispVal = this;
            static_cast<void>(AddRef());
            return S_OK;
        }
        result->vt = VT_EMPTY;
        return DISP_E_MEMBERNOTFOUND;
    }

private:
    ~FakeParameterArrayDispatch() override {
        static_cast<void>(array_->Release());
    }

    fake_parameter_array::ParameterArray* array_;
    std::wstring arrayName_;
    bool arrayCreated_ = false;
    bool genericProperties_ = false;
    DISPID nextGenericMember_ = 100;
    std::map<std::wstring, DISPID> genericMembers_;
    std::map<DISPID, std::wstring> genericNames_;
    std::map<std::wstring, CComVariant> genericValues_;
    size_t genericValueReads_ = 0;
};

#pragma once

#include <Windows.h>
#include <OleAuto.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <string>

class BatchAutomation final : public IDispatch {
public:
    explicit BatchAutomation(IDispatch* hwp) noexcept;

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID interfaceId, void** object) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* count) override;
    HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT index, LCID locale, ITypeInfo** typeInfo) override;
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID interfaceId,
        LPOLESTR* names,
        UINT count,
        LCID locale,
        DISPID* memberIds) override;
    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID memberId,
        REFIID interfaceId,
        LCID locale,
        WORD flags,
        DISPPARAMS* parameters,
        VARIANT* result,
        EXCEPINFO* exception,
        UINT* argumentError) override;

private:
    ~BatchAutomation() = default;

    static HRESULT ReturnString(const std::wstring& value, VARIANT* result) noexcept;

    volatile LONG references_ = 1;
    CComPtr<IDispatch> hwp_;
};

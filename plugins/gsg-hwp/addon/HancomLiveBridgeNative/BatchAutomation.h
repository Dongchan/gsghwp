#pragma once

#include <Windows.h>
#include <OleAuto.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <string>

class BatchAutomation final : public IDispatch {
public:
    explicit BatchAutomation(
        IDispatch* hwp,
        LONG targetDocumentId = 0) noexcept;

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
    struct ActivationWork;

    ~BatchAutomation();

    HRESULT BeginActivation(LONG documentId, VARIANT* result) noexcept;
    HRESULT PollActivation(LONG documentId, VARIANT* result) noexcept;
    static DWORD WINAPI RunActivation(void* context) noexcept;
    void FinishActivation(HRESULT status) noexcept;
    HRESULT VerifyTarget();
    static HRESULT ReturnString(const std::wstring& value, VARIANT* result) noexcept;

    volatile LONG references_ = 1;
    volatile LONG activationDocumentId_ = 0;
    volatile LONG activationHresult_ = E_PENDING;
    volatile LONG activationState_ = 0;
    HANDLE activationFailureEvent_ = nullptr;
    HANDLE activationSuccessEvent_ = nullptr;
    std::wstring activationToken_;
    CComPtr<IDispatch> hwp_;
    LONG targetDocumentId_ = 0;
};

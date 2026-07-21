#include <Windows.h>
#include <ObjIdl.h>
#include <Ole2.h>

#include "BatchAutomation.h"
#include "BridgeStatus.h"

#include <cstring>
#include <cwchar>
#include <new>

namespace {

constexpr char kOnInitialLoad[] = "{B91A2981-A001-44a9-933F-5BF70A747967}";
constexpr char kOnLoad[] = "{3E4DC866-051C-4989-820E-DADC1E6264B9}";
constexpr char kBootstrapAction[] = "{CFB0F99F-3589-4A85-9D8B-2D6BCE5B35D1}";
constexpr wchar_t kMonikerDelimiter[] = L"!";
constexpr wchar_t kMonikerPrefix[] = L"HancomLiveBridge.";
constexpr wchar_t kBatchMonikerPrefix[] = L"HancomLiveBatch.";

struct IHncUserActionModule {
    virtual LPCSTR EnumAction(int iterator) = 0;
    virtual BOOL GetActionImage(
        LPCSTR action,
        UINT state,
        HBITMAP* bitmap,
        int* imageIndex) = 0;
    virtual BOOL UpdateUI(LPCSTR action, LPDISPATCH object, UINT* state) = 0;
    virtual int DoAction(LPCSTR action, LPDISPATCH object) = 0;
};

class UserActionModule final : public IHncUserActionModule {
public:
    LPCSTR EnumAction(const int iterator) override {
        const char* action = nullptr;
        switch (iterator) {
        case 0:
            action = kOnInitialLoad;
            break;
        case 1:
            action = kOnLoad;
            break;
        case 2:
            action = kBootstrapAction;
            break;
        }
        bridge_status::NoteEnum(action);
        return action;
    }

    BOOL GetActionImage(
        LPCSTR,
        UINT,
        HBITMAP* const bitmap,
        int* const imageIndex) override {
        if (bitmap != nullptr) {
            *bitmap = nullptr;
        }
        if (imageIndex != nullptr) {
            *imageIndex = 0;
        }
        return FALSE;
    }

    BOOL UpdateUI(
        LPCSTR const action,
        LPDISPATCH const object,
        UINT* const state) override {
        bridge_status::NoteUpdateUi(action);
        if (!IsRecognizedAction(action) || state == nullptr) {
            return FALSE;
        }
        *state = 0;
        if (registrationCookie_ == 0 || batchRegistrationCookie_ == 0) {
            static_cast<void>(Publish(object));
        }
        return TRUE;
    }

    int DoAction(LPCSTR const action, LPDISPATCH const object) override {
        bridge_status::NoteDoAction(action);
        if (!IsRecognizedAction(action)) {
            return FALSE;
        }
        return SUCCEEDED(Publish(object));
    }

    [[nodiscard]] HRESULT LastResult() const noexcept {
        return lastResult_;
    }

    HRESULT Revoke() noexcept {
        IRunningObjectTable* table = nullptr;
        const HRESULT status = GetRunningObjectTable(0, &table);
        if (FAILED(status)) {
            lastResult_ = status;
            bridge_status::NoteRevoke(status);
            return status;
        }
        RevokeCookie(table, batchRegistrationCookie_);
        RevokeCookie(table, registrationCookie_);
        table->Release();
        lastResult_ = S_OK;
        bridge_status::NoteRevoke(S_OK);
        return S_OK;
    }

private:
    static bool IsRecognizedAction(LPCSTR const action) noexcept {
        return action != nullptr &&
            (std::strcmp(action, kOnInitialLoad) == 0 ||
             std::strcmp(action, kOnLoad) == 0 ||
             std::strcmp(action, kBootstrapAction) == 0);
    }

    HRESULT FinishPublish(const HRESULT result) noexcept {
        lastResult_ = result;
        bridge_status::NotePublish(
            result,
            registrationCookie_,
            batchRegistrationCookie_);
        return result;
    }

    HRESULT Publish(LPDISPATCH const object) noexcept {
        if (object == nullptr) {
            return FinishPublish(E_POINTER);
        }

        IRunningObjectTable* table = nullptr;
        HRESULT result = GetRunningObjectTable(0, &table);
        if (result == CO_E_NOTINITIALIZED) {
            const HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
            if (SUCCEEDED(initialized) || initialized == RPC_E_CHANGED_MODE) {
                result = GetRunningObjectTable(0, &table);
            } else {
                result = initialized;
            }
        }
        if (FAILED(result)) {
            return FinishPublish(result);
        }

        RevokeCookie(table, batchRegistrationCookie_);
        RevokeCookie(table, registrationCookie_);

        result = RegisterObject(table, kMonikerPrefix, object, &registrationCookie_);
        if (FAILED(result)) {
            table->Release();
            return FinishPublish(result);
        }

        BatchAutomation* const batch = new (std::nothrow) BatchAutomation(object);
        if (batch == nullptr) {
            RevokeCookie(table, registrationCookie_);
            table->Release();
            return FinishPublish(E_OUTOFMEMORY);
        }
        result = RegisterObject(
            table,
            kBatchMonikerPrefix,
            batch,
            &batchRegistrationCookie_);
        static_cast<void>(batch->Release());
        if (FAILED(result)) {
            RevokeCookie(table, registrationCookie_);
        }

        table->Release();
        return FinishPublish(result);
    }

    static void RevokeCookie(
        IRunningObjectTable* const table,
        DWORD& cookie) noexcept {
        if (cookie != 0) {
            static_cast<void>(table->Revoke(cookie));
            cookie = 0;
        }
    }

    static HRESULT RegisterObject(
        IRunningObjectTable* const table,
        const wchar_t* const prefix,
        IUnknown* const object,
        DWORD* const cookie) noexcept {
        wchar_t itemName[64] = {};
        const int written = swprintf_s(
            itemName,
            L"%s%lu",
            prefix,
            static_cast<unsigned long>(GetCurrentProcessId()));
        if (written < 0) {
            return E_FAIL;
        }
        IMoniker* moniker = nullptr;
        HRESULT status = CreateItemMoniker(kMonikerDelimiter, itemName, &moniker);
        if (SUCCEEDED(status)) {
            status = table->Register(
                ROTFLAGS_REGISTRATIONKEEPSALIVE,
                object,
                moniker,
                cookie);
            moniker->Release();
        }
        return status;
    }

    DWORD registrationCookie_ = 0;
    DWORD batchRegistrationCookie_ = 0;
    HRESULT lastResult_ = E_UNEXPECTED;
};

UserActionModule& Module() noexcept {
    static UserActionModule module;
    return module;
}

}

extern "C" __declspec(dllexport) IHncUserActionModule* __stdcall
QueryUserActionInterface() noexcept {
    bridge_status::NoteQuery();
    return &Module();
}

extern "C" __declspec(dllexport) BOOL __stdcall IsAccessiblePath(
    HWND,
    LONG,
    LPCTSTR,
    LPCTSTR) noexcept {
    return TRUE;
}

extern "C" __declspec(dllexport) HRESULT __stdcall
GetBridgeLastHRESULT() noexcept {
    return Module().LastResult();
}

extern "C" __declspec(dllexport) HRESULT __stdcall
ReleaseBridgePublication() noexcept {
    return Module().Revoke();
}

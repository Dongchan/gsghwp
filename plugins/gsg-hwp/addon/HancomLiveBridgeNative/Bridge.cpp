#include <Windows.h>
#include <ObjIdl.h>
#include <Ole2.h>

#include "BatchAutomation.h"
#include "BridgeStatus.h"
#include "DispatchInvoke.h"

#include <algorithm>
#include <cstring>
#include <cwchar>
#include <new>
#include <vector>

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
        HWND windowHandle = nullptr;
        LONG documentId = 0;
        const bool windowPublished =
            SUCCEEDED(ReadWindowHandle(object, &windowHandle)) &&
            HasWindowPublication(windowHandle);
        const bool documentPublished =
            windowPublished &&
            SUCCEEDED(ReadDocumentId(object, &documentId)) &&
            HasDocumentPublication(windowHandle, documentId);
        if (registrationCookie_ == 0 || batchRegistrationCookie_ == 0 ||
            !windowPublished || !documentPublished) {
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
        for (auto& publication : documentPublications_) {
            RevokeCookie(table, publication.batchRegistrationCookie);
            RevokeCookie(table, publication.registrationCookie);
        }
        documentPublications_.clear();
        for (auto& publication : windowPublications_) {
            RevokeCookie(table, publication.batchRegistrationCookie);
            RevokeCookie(table, publication.registrationCookie);
        }
        windowPublications_.clear();
        RevokeCookie(table, batchRegistrationCookie_);
        RevokeCookie(table, registrationCookie_);
        table->Release();
        lastResult_ = S_OK;
        bridge_status::NoteRevoke(S_OK);
        return S_OK;
    }

private:
    struct WindowPublication {
        HWND windowHandle = nullptr;
        DWORD registrationCookie = 0;
        DWORD batchRegistrationCookie = 0;
    };

    struct DocumentPublication {
        HWND windowHandle = nullptr;
        LONG documentId = 0;
        DWORD registrationCookie = 0;
        DWORD batchRegistrationCookie = 0;
    };

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

        HWND windowHandle = nullptr;
        const HRESULT windowStatus = ReadWindowHandle(object, &windowHandle);
        LONG documentId = 0;
        const HRESULT documentStatus = ReadDocumentId(object, &documentId);

        RevokeCookie(table, batchRegistrationCookie_);
        RevokeCookie(table, registrationCookie_);
        result = RegisterPair(
            table,
            object,
            nullptr,
            0,
            &registrationCookie_,
            &batchRegistrationCookie_);
        if (FAILED(result)) {
            table->Release();
            return FinishPublish(result);
        }

        if (SUCCEEDED(windowStatus) && windowHandle != nullptr) {
            RemoveWindowPublication(table, windowHandle);
            WindowPublication publication{};
            publication.windowHandle = windowHandle;
            result = RegisterPair(
                table,
                object,
                windowHandle,
                0,
                &publication.registrationCookie,
                &publication.batchRegistrationCookie);
            if (SUCCEEDED(result)) {
                windowPublications_.push_back(publication);
            }
        }
        if (SUCCEEDED(result) && SUCCEEDED(windowStatus) &&
            SUCCEEDED(documentStatus) && windowHandle != nullptr &&
            documentId > 0) {
            RemoveDocumentPublication(table, windowHandle, documentId);
            DocumentPublication publication{};
            publication.windowHandle = windowHandle;
            publication.documentId = documentId;
            result = RegisterPair(
                table,
                object,
                windowHandle,
                documentId,
                &publication.registrationCookie,
                &publication.batchRegistrationCookie);
            if (SUCCEEDED(result)) {
                documentPublications_.push_back(publication);
            }
        }

        table->Release();
        return FinishPublish(result);
    }

    [[nodiscard]] bool HasWindowPublication(
        const HWND windowHandle) const noexcept {
        return std::any_of(
            windowPublications_.cbegin(),
            windowPublications_.cend(),
            [windowHandle](const WindowPublication& publication) {
                return publication.windowHandle == windowHandle &&
                    publication.registrationCookie != 0 &&
                    publication.batchRegistrationCookie != 0;
            });
    }

    [[nodiscard]] bool HasDocumentPublication(
        const HWND windowHandle,
        const LONG documentId) const noexcept {
        return std::any_of(
            documentPublications_.cbegin(),
            documentPublications_.cend(),
            [windowHandle, documentId](const DocumentPublication& publication) {
                return publication.windowHandle == windowHandle &&
                    publication.documentId == documentId &&
                    publication.registrationCookie != 0 &&
                    publication.batchRegistrationCookie != 0;
            });
    }

    void RemoveWindowPublication(
        IRunningObjectTable* const table,
        const HWND windowHandle) noexcept {
        const auto found = std::find_if(
            windowPublications_.begin(),
            windowPublications_.end(),
            [windowHandle](const WindowPublication& publication) {
                return publication.windowHandle == windowHandle;
            });
        if (found == windowPublications_.end()) {
            return;
        }
        RevokeCookie(table, found->batchRegistrationCookie);
        RevokeCookie(table, found->registrationCookie);
        windowPublications_.erase(found);
    }

    void RemoveDocumentPublication(
        IRunningObjectTable* const table,
        const HWND windowHandle,
        const LONG documentId) noexcept {
        const auto found = std::find_if(
            documentPublications_.begin(),
            documentPublications_.end(),
            [windowHandle, documentId](const DocumentPublication& publication) {
                return publication.windowHandle == windowHandle &&
                    publication.documentId == documentId;
            });
        if (found == documentPublications_.end()) {
            return;
        }
        RevokeCookie(table, found->batchRegistrationCookie);
        RevokeCookie(table, found->registrationCookie);
        documentPublications_.erase(found);
    }

    static HRESULT ReadWindowHandle(
        IDispatch* const object,
        HWND* const windowHandle) noexcept {
        if (object == nullptr || windowHandle == nullptr) {
            return E_POINTER;
        }
        *windowHandle = nullptr;
        CComVariant rawWindows;
        HRESULT status = hancom::dispatch::PropertyGet(
            object,
            L"XHwpWindows",
            &rawWindows);
        CComPtr<IDispatch> windows;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::AsDispatch(rawWindows, windows);
        }
        CComVariant rawWindow;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::PropertyGet(
                windows,
                L"Active_XHwpWindow",
                &rawWindow);
        }
        CComPtr<IDispatch> window;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::AsDispatch(rawWindow, window);
        }
        CComVariant rawHandle;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::PropertyGet(
                window,
                L"WindowHandle",
                &rawHandle);
        }
        LONG handle = 0;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::AsLong(rawHandle, &handle);
        }
        if (FAILED(status)) {
            return status;
        }
        if (handle == 0) {
            return E_FAIL;
        }
        *windowHandle = reinterpret_cast<HWND>(
            static_cast<LONG_PTR>(handle));
        return S_OK;
    }

    static HRESULT ReadDocumentId(
        IDispatch* const object,
        LONG* const documentId) noexcept {
        if (object == nullptr || documentId == nullptr) {
            return E_POINTER;
        }
        *documentId = 0;
        CComVariant rawDocuments;
        HRESULT status = hancom::dispatch::PropertyGet(
            object,
            L"XHwpDocuments",
            &rawDocuments);
        CComPtr<IDispatch> documents;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::AsDispatch(rawDocuments, documents);
        }
        CComVariant rawDocument;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::PropertyGet(
                documents,
                L"Active_XHwpDocument",
                &rawDocument);
        }
        CComPtr<IDispatch> document;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::AsDispatch(rawDocument, document);
        }
        CComVariant rawDocumentId;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::PropertyGet(
                document,
                L"DocumentID",
                &rawDocumentId);
        }
        LONG value = 0;
        if (SUCCEEDED(status)) {
            status = hancom::dispatch::AsLong(rawDocumentId, &value);
        }
        if (FAILED(status)) {
            return status;
        }
        if (value <= 0) {
            return E_FAIL;
        }
        *documentId = value;
        return S_OK;
    }

    static HRESULT RegisterPair(
        IRunningObjectTable* const table,
        IDispatch* const object,
        const HWND windowHandle,
        const LONG documentId,
        DWORD* const registrationCookie,
        DWORD* const batchRegistrationCookie) noexcept {
        *registrationCookie = 0;
        *batchRegistrationCookie = 0;
        HRESULT result = RegisterObject(
            table,
            kMonikerPrefix,
            windowHandle,
            documentId,
            object,
            registrationCookie);
        if (FAILED(result)) {
            return result;
        }
        BatchAutomation* const batch =
            new (std::nothrow) BatchAutomation(object, documentId);
        if (batch == nullptr) {
            RevokeCookie(table, *registrationCookie);
            return E_OUTOFMEMORY;
        }
        result = RegisterObject(
            table,
            kBatchMonikerPrefix,
            windowHandle,
            documentId,
            batch,
            batchRegistrationCookie);
        static_cast<void>(batch->Release());
        if (FAILED(result)) {
            RevokeCookie(table, *registrationCookie);
        }
        return result;
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
        const HWND windowHandle,
        const LONG documentId,
        IUnknown* const object,
        DWORD* const cookie) noexcept {
        wchar_t itemName[64] = {};
        const int written = windowHandle == nullptr
            ? swprintf_s(
                itemName,
                L"%s%lu",
                prefix,
                static_cast<unsigned long>(GetCurrentProcessId()))
            : documentId <= 0
                ? swprintf_s(
                itemName,
                L"%s%lu.%lu",
                prefix,
                static_cast<unsigned long>(GetCurrentProcessId()),
                static_cast<unsigned long>(
                    reinterpret_cast<ULONG_PTR>(windowHandle)))
                : swprintf_s(
                    itemName,
                    L"%s%lu.%lu.%ld",
                    prefix,
                    static_cast<unsigned long>(GetCurrentProcessId()),
                    static_cast<unsigned long>(
                        reinterpret_cast<ULONG_PTR>(windowHandle)),
                    documentId);
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
    std::vector<WindowPublication> windowPublications_;
    std::vector<DocumentPublication> documentPublications_;
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

#include "HarnessCom.h"

#include "DispatchInvoke.h"
#include "HarnessFixture.h"
#include "HarnessIo.h"

#include <Windows.h>
#include <ObjIdl.h>
#include <Ole2.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <chrono>
#include <cwchar>
#include <string>
#include <thread>

namespace hancom::api_harness {
namespace {

using hancom::dispatch::AsLong;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

constexpr wchar_t kBatchPrefix[] = L"!HancomLiveBatch.";

HRESULT HwpProcessId(IDispatch* const hwp, DWORD* const processId) noexcept {
    if (hwp == nullptr || processId == nullptr) {
        return E_POINTER;
    }
    CComVariant rawWindows;
    HRESULT status = PropertyGet(hwp, L"XHwpWindows", &rawWindows);
    CComPtr<IDispatch> windows;
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawWindows, windows);
    }
    CComVariant rawWindow;
    if (SUCCEEDED(status)) {
        status = PropertyGet(windows, L"Active_XHwpWindow", &rawWindow);
    }
    CComPtr<IDispatch> window;
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawWindow, window);
    }
    CComVariant rawHandle;
    if (SUCCEEDED(status)) {
        status = PropertyGet(window, L"WindowHandle", &rawHandle);
    }
    LONG windowHandle = 0;
    if (SUCCEEDED(status)) {
        status = AsLong(rawHandle, &windowHandle);
    }
    const HWND nativeWindow = reinterpret_cast<HWND>(static_cast<LONG_PTR>(windowHandle));
    if (FAILED(status) || nativeWindow == nullptr) {
        return FAILED(status) ? status : E_HANDLE;
    }
    if (IsWindowVisible(nativeWindow) != FALSE) {
        return HRESULT_FROM_WIN32(ERROR_BUSY);
    }
    DWORD resolvedProcessId = 0;
    if (GetWindowThreadProcessId(nativeWindow, &resolvedProcessId) == 0 ||
        resolvedProcessId == 0) {
        const DWORD error = GetLastError();
        return HRESULT_FROM_WIN32(error == ERROR_SUCCESS ? ERROR_NOT_FOUND : error);
    }
    *processId = resolvedProcessId;
    return S_OK;
}

HRESULT BatchObject(
    const DWORD processId,
    CComPtr<IDispatch>& result,
    const std::wstring& expectedName) noexcept {
    CComPtr<IRunningObjectTable> table;
    HRESULT status = GetRunningObjectTable(0, &table);
    if (FAILED(status)) {
        return status;
    }
    CComPtr<IEnumMoniker> enumerator;
    status = table->EnumRunning(&enumerator);
    CComPtr<IBindCtx> context;
    if (SUCCEEDED(status)) {
        status = CreateBindCtx(0, &context);
    }
    if (FAILED(status)) {
        return status;
    }
    CComPtr<IMoniker> moniker;
    while (enumerator->Next(1, &moniker, nullptr) == S_OK) {
        LPOLESTR display = nullptr;
        if (SUCCEEDED(moniker->GetDisplayName(context, nullptr, &display)) && display != nullptr) {
            const std::wstring name(display);
            if (name == expectedName) {
                CComPtr<IUnknown> object;
                status = table->GetObject(moniker, &object);
                if (SUCCEEDED(status)) {
                    status = object->QueryInterface(IID_PPV_ARGS(&result));
                }
                if (SUCCEEDED(status)) {
                    static_cast<void>(processId);
                    CoTaskMemFree(display);
                    return S_OK;
                }
            }
            CoTaskMemFree(display);
        }
        moniker.Release();
    }
    return MK_E_UNAVAILABLE;
}

HRESULT WaitForBatch(
    IDispatch* const hwp,
    const wchar_t* const processIdPath,
    CComPtr<IDispatch>& batch,
    DWORD* const processId) noexcept {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
    HRESULT status = MK_E_UNAVAILABLE;
    while (std::chrono::steady_clock::now() < deadline) {
        if (*processId == 0) {
            status = HwpProcessId(hwp, processId);
            if (SUCCEEDED(status) &&
                !WriteUtf8File(processIdPath, std::to_wstring(*processId))) {
                return E_FAIL;
            }
        }
        if (*processId != 0) {
            const std::wstring expectedName =
                std::wstring(kBatchPrefix) + std::to_wstring(*processId);
            status = BatchObject(*processId, batch, expectedName);
        }
        if (SUCCEEDED(status)) {
            return status;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    return status;
}

}

HarnessResult RunOfficialApiCase(
    const std::wstring& request,
    const wchar_t* const processIdPath) noexcept {
    HarnessResult result;
    const HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(initialized)) {
        result.status = initialized;
        return result;
    }
    CLSID classId{};
    result.status = CLSIDFromProgID(L"HWPFrame.HwpObject", &classId);
    CComPtr<IDispatch> hwp;
    if (SUCCEEDED(result.status)) {
        result.status = CoCreateInstance(
            classId, nullptr, CLSCTX_LOCAL_SERVER, IID_PPV_ARGS(&hwp));
    }
    CComPtr<IDispatch> batch;
    if (SUCCEEDED(result.status)) {
        result.status = WaitForBatch(hwp, processIdPath, batch, &result.processId);
    }
    CComVariant rawVersion;
    LONG version = 0;
    if (SUCCEEDED(result.status)) {
        result.status = PropertyGet(batch, L"ProtocolVersion", &rawVersion);
    }
    if (SUCCEEDED(result.status)) {
        result.status = AsLong(rawVersion, &version);
    }
    if (SUCCEEDED(result.status) && version < 6) {
        result.status = E_NOINTERFACE;
    }
    if (SUCCEEDED(result.status)) {
        result.status = PrepareOfficialApiFixture(hwp, request);
    }
    CComVariant rawResponse;
    if (SUCCEEDED(result.status)) {
        result.status = Method(
            batch, L"ProbeOfficialApi", {CComVariant(request.c_str())}, &rawResponse);
    }
    if (SUCCEEDED(result.status)) {
        result.status = AsString(rawResponse, &result.response);
    }
    if (hwp != nullptr && result.processId != 0) {
        CComVariant ignored;
        static_cast<void>(Method(hwp, L"Quit", {}, &ignored));
    }
    CoUninitialize();
    return result;
}

}

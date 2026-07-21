#include <windows.h>
#include <ocidl.h>
#include <oleauto.h>
#include <wrl/client.h>

#include <atomic>
#include <cstdio>
#include <cwchar>
#include <fcntl.h>
#include <io.h>
#include <new>
#include <optional>
#include <string>
#include <string_view>

using Microsoft::WRL::ComPtr;

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
constexpr wchar_t kDispatchEventIidText[] =
    L"{F04A09A0-F319-40D5-88CA-CA439CC6820C}";
constexpr wchar_t kDualEventIidText[] =
    L"{BFCD3DE1-DE0D-4E4F-ABFB-664F525335CB}";

class ComApartment final {
public:
    ComApartment() noexcept
        : result_(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)) {}

    ~ComApartment() noexcept {
        if (SUCCEEDED(result_)) {
            CoUninitialize();
        }
    }

    ComApartment(const ComApartment&) = delete;
    ComApartment& operator=(const ComApartment&) = delete;

    [[nodiscard]] HRESULT Result() const noexcept {
        return result_;
    }

private:
    HRESULT result_;
};

std::string WideToUtf8(const std::wstring_view value) {
    if (value.empty()) {
        return {};
    }
    const int required = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        value.data(),
        static_cast<int>(value.size()),
        nullptr,
        0,
        nullptr,
        nullptr);
    if (required <= 0) {
        return {};
    }
    std::string encoded(static_cast<std::size_t>(required), '\0');
    const int written = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        value.data(),
        static_cast<int>(value.size()),
        encoded.data(),
        required,
        nullptr,
        nullptr);
    return written == required ? encoded : std::string{};
}

std::string JsonEscape(const std::wstring_view value) {
    const std::string encoded = WideToUtf8(value);
    std::string escaped;
    escaped.reserve(encoded.size());
    constexpr char hex[] = "0123456789abcdef";
    for (const unsigned char character : encoded) {
        switch (character) {
        case '"':
            escaped.append("\\\"");
            break;
        case '\\':
            escaped.append("\\\\");
            break;
        case '\b':
            escaped.append("\\b");
            break;
        case '\f':
            escaped.append("\\f");
            break;
        case '\n':
            escaped.append("\\n");
            break;
        case '\r':
            escaped.append("\\r");
            break;
        case '\t':
            escaped.append("\\t");
            break;
        default:
            if (character < 0x20) {
                escaped.append("\\u00");
                escaped.push_back(hex[(character >> 4) & 0x0F]);
                escaped.push_back(hex[character & 0x0F]);
            } else {
                escaped.push_back(static_cast<char>(character));
            }
            break;
        }
    }
    return escaped;
}

void WriteLine(const std::string& line) {
    static_cast<void>(std::fwrite(line.data(), sizeof(char), line.size(), stdout));
    static_cast<void>(std::fwrite("\n", sizeof(char), 1, stdout));
    static_cast<void>(std::fflush(stdout));
}

void WriteReady(const std::wstring_view moniker, const std::wstring_view source_iid) {
    std::string line = "{\"type\":\"ready\",\"protocol\":1,\"moniker\":\"";
    line.append(JsonEscape(moniker));
    line.append("\",\"source_iid\":\"");
    line.append(JsonEscape(source_iid));
    line.append("\"}");
    WriteLine(line);
}

const char* EventName(const DISPID dispid) noexcept {
    switch (dispid) {
    case 1:
        return "Quit";
    case 2:
        return "CreateXHwpWindow";
    case 3:
        return "CloseXHwpWindow";
    case 4:
        return "NewDocument";
    case 5:
        return "DocumentBeforeClose";
    case 6:
        return "DocumentBeforeOpen";
    case 7:
        return "DocumentAfterOpen";
    case 8:
        return "DocumentBeforeSave";
    case 9:
        return "DocumentAfterSave";
    case 10:
        return "DocumentAfterClose";
    case 11:
        return "DocumentChange";
    case 12:
        return "DocumentBeforePrint";
    case 13:
        return "DocumentAfterPrint";
    case 14:
        return "DocumentClickedHyperlink";
    case 15:
        return "DocumentModifiedHyperlink";
    case 16:
        return "BeforeQuit";
    default:
        return "Unknown";
    }
}

std::optional<LONG> EventDocumentId(
    const DISPID dispid,
    const DISPPARAMS* const parameters) {
    if (parameters == nullptr || parameters->cArgs == 0 ||
        !((dispid >= 4 && dispid <= 13) || dispid == 16)) {
        return std::nullopt;
    }
    VARIANT value{};
    VariantInit(&value);
    const HRESULT result = VariantCopyInd(&value, &parameters->rgvarg[0]);
    if (FAILED(result)) {
        return std::nullopt;
    }
    std::optional<LONG> document_id;
    switch (value.vt) {
    case VT_I4:
        document_id = value.lVal;
        break;
    case VT_INT:
        document_id = value.intVal;
        break;
    case VT_UI4:
        if (value.ulVal <= static_cast<ULONG>(LONG_MAX)) {
            document_id = static_cast<LONG>(value.ulVal);
        }
        break;
    default:
        break;
    }
    static_cast<void>(VariantClear(&value));
    return document_id;
}

void WriteEvent(const DISPID dispid, const DISPPARAMS* const parameters) {
    std::string line = "{\"type\":\"event\",\"event\":\"";
    line.append(EventName(dispid));
    line.append("\",\"dispid\":");
    line.append(std::to_string(dispid));
    line.append(",\"document_id\":");
    const std::optional<LONG> document_id = EventDocumentId(dispid, parameters);
    if (document_id.has_value()) {
        line.append(std::to_string(document_id.value()));
    } else {
        line.append("null");
    }
    line.push_back('}');
    WriteLine(line);
}

class EventSink final : public IDispatch {
public:
    EventSink() noexcept = default;

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID interface_id,
        void** const destination) override {
        if (destination == nullptr) {
            return E_POINTER;
        }
        *destination = nullptr;
        if (IsEqualIID(interface_id, IID_IUnknown) ||
            IsEqualIID(interface_id, IID_IDispatch) ||
            IsEqualIID(interface_id, kDispatchEventIid) ||
            IsEqualIID(interface_id, kDualEventIid)) {
            *destination = static_cast<IDispatch*>(this);
            static_cast<void>(AddRef());
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override {
        return ++reference_count_;
    }

    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG remaining = --reference_count_;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* const count) override {
        if (count == nullptr) {
            return E_POINTER;
        }
        *count = 0;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfo(
        UINT,
        LCID,
        ITypeInfo**) override {
        return E_NOTIMPL;
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
        const DISPID member_id,
        REFIID interface_id,
        LCID,
        WORD,
        DISPPARAMS* const parameters,
        VARIANT*,
        EXCEPINFO*,
        UINT*) override {
        if (!IsEqualIID(interface_id, IID_NULL)) {
            return DISP_E_UNKNOWNINTERFACE;
        }
        WriteEvent(member_id, parameters);
        return S_OK;
    }

private:
    ~EventSink() = default;

    std::atomic<ULONG> reference_count_{1};
};

HRESULT FindExactRotObject(
    const std::wstring& requested_moniker,
    ComPtr<IDispatch>* const dispatch) {
    ComPtr<IRunningObjectTable> table;
    HRESULT result = GetRunningObjectTable(0, table.GetAddressOf());
    if (FAILED(result)) {
        return result;
    }
    ComPtr<IBindCtx> context;
    result = CreateBindCtx(0, context.GetAddressOf());
    if (FAILED(result)) {
        return result;
    }
    ComPtr<IEnumMoniker> enumeration;
    result = table->EnumRunning(enumeration.GetAddressOf());
    if (FAILED(result)) {
        return result;
    }
    ComPtr<IMoniker> moniker;
    ULONG fetched = 0;
    while (enumeration->Next(1, moniker.ReleaseAndGetAddressOf(), &fetched) == S_OK) {
        LPOLESTR raw_display_name = nullptr;
        result = moniker->GetDisplayName(context.Get(), nullptr, &raw_display_name);
        if (SUCCEEDED(result) && raw_display_name != nullptr) {
            const std::wstring display_name(raw_display_name);
            CoTaskMemFree(raw_display_name);
            if (display_name == requested_moniker) {
                ComPtr<IUnknown> object;
                result = table->GetObject(moniker.Get(), object.GetAddressOf());
                if (FAILED(result)) {
                    return result;
                }
                return object.As(dispatch);
            }
        } else if (raw_display_name != nullptr) {
            CoTaskMemFree(raw_display_name);
        }
    }
    return MK_E_UNAVAILABLE;
}

HRESULT FindEventConnectionPoint(
    IDispatch* const dispatch,
    ComPtr<IConnectionPoint>* const connection_point,
    std::wstring* const source_iid) {
    ComPtr<IConnectionPointContainer> container;
    HRESULT result = dispatch->QueryInterface(
        IID_PPV_ARGS(container.ReleaseAndGetAddressOf()));
    if (FAILED(result)) {
        return result;
    }
    result = container->FindConnectionPoint(
        kDispatchEventIid,
        connection_point->ReleaseAndGetAddressOf());
    if (SUCCEEDED(result)) {
        *source_iid = kDispatchEventIidText;
        return result;
    }
    result = container->FindConnectionPoint(
        kDualEventIid,
        connection_point->ReleaseAndGetAddressOf());
    if (SUCCEEDED(result)) {
        *source_iid = kDualEventIidText;
    }
    return result;
}

int PumpUntilShutdown() {
    const HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    const bool has_input = input != nullptr && input != INVALID_HANDLE_VALUE;
    std::string command_buffer;
    while (true) {
        const DWORD handle_count = has_input ? 1U : 0U;
        const HANDLE* const handles = has_input ? &input : nullptr;
        const DWORD wait_result = MsgWaitForMultipleObjectsEx(
            handle_count,
            handles,
            INFINITE,
            QS_ALLINPUT,
            MWMO_ALERTABLE | MWMO_INPUTAVAILABLE);
        if (has_input && wait_result == WAIT_OBJECT_0) {
            char buffer[256] = {};
            DWORD byte_count = 0;
            if (ReadFile(input, buffer, sizeof(buffer), &byte_count, nullptr) == FALSE ||
                byte_count == 0) {
                return 0;
            }
            command_buffer.append(buffer, buffer + byte_count);
            std::size_t newline = command_buffer.find('\n');
            while (newline != std::string::npos) {
                std::string command = command_buffer.substr(0, newline);
                if (!command.empty() && command.back() == '\r') {
                    command.pop_back();
                }
                command_buffer.erase(0, newline + 1);
                if (command == "shutdown") {
                    return 0;
                }
                newline = command_buffer.find('\n');
            }
            continue;
        }
        if (wait_result == WAIT_OBJECT_0 + handle_count) {
            MSG message{};
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE) != FALSE) {
                if (message.message == WM_QUIT) {
                    return 0;
                }
                static_cast<void>(TranslateMessage(&message));
                static_cast<void>(DispatchMessageW(&message));
            }
            continue;
        }
        if (wait_result == WAIT_IO_COMPLETION) {
            continue;
        }
        std::fwprintf(stderr, L"HancomEventBridge: message wait failed (%lu)\n", GetLastError());
        return 4;
    }
}

int RunSelfTest() {
    WriteReady(L"self-test", kDispatchEventIidText);
    EventSink* const raw_sink = new (std::nothrow) EventSink();
    if (raw_sink == nullptr) {
        return 5;
    }
    ComPtr<IDispatch> sink;
    sink.Attach(raw_sink);
    VARIANT argument{};
    VariantInit(&argument);
    argument.vt = VT_I4;
    argument.lVal = 3;
    DISPPARAMS parameters{&argument, nullptr, 1, 0};
    return SUCCEEDED(sink->Invoke(
        11,
        IID_NULL,
        LOCALE_USER_DEFAULT,
        DISPATCH_METHOD,
        &parameters,
        nullptr,
        nullptr,
        nullptr))
        ? 0
        : 5;
}

void PrintUsage(FILE* const stream) {
    std::fwprintf(
        stream,
        L"Usage: HancomEventBridge.exe --moniker <exact ROT display name>\n"
        L"       HancomEventBridge.exe --self-test\n");
}

}

int wmain(const int argument_count, wchar_t* arguments[]) {
    static_cast<void>(_setmode(_fileno(stdout), _O_BINARY));
    static_cast<void>(_setmode(_fileno(stderr), _O_U8TEXT));
    if (argument_count == 2 && std::wcscmp(arguments[1], L"--self-test") == 0) {
        return RunSelfTest();
    }
    if (argument_count == 2 && std::wcscmp(arguments[1], L"--help") == 0) {
        static_cast<void>(_setmode(_fileno(stdout), _O_U8TEXT));
        PrintUsage(stdout);
        return 0;
    }
    if (argument_count != 3 || std::wcscmp(arguments[1], L"--moniker") != 0 ||
        arguments[2][0] == L'\0') {
        PrintUsage(stderr);
        return 2;
    }

    const ComApartment apartment;
    if (FAILED(apartment.Result())) {
        std::fwprintf(
            stderr,
            L"HancomEventBridge: CoInitializeEx failed (0x%08lX)\n",
            static_cast<unsigned long>(apartment.Result()));
        return 3;
    }

    const std::wstring requested_moniker(arguments[2]);
    ComPtr<IDispatch> dispatch;
    HRESULT result = FindExactRotObject(requested_moniker, &dispatch);
    if (result == MK_E_UNAVAILABLE) {
        std::fwprintf(stderr, L"HancomEventBridge: exact ROT moniker was not found\n");
        return 3;
    }
    if (FAILED(result)) {
        std::fwprintf(
            stderr,
            L"HancomEventBridge: ROT attachment failed (0x%08lX)\n",
            static_cast<unsigned long>(result));
        return 3;
    }

    ComPtr<IConnectionPoint> connection_point;
    std::wstring source_iid;
    result = FindEventConnectionPoint(dispatch.Get(), &connection_point, &source_iid);
    if (FAILED(result)) {
        std::fwprintf(
            stderr,
            L"HancomEventBridge: IHwpObjectEvents connection point was not found (0x%08lX)\n",
            static_cast<unsigned long>(result));
        return 3;
    }

    EventSink* const raw_sink = new (std::nothrow) EventSink();
    if (raw_sink == nullptr) {
        std::fwprintf(stderr, L"HancomEventBridge: event sink allocation failed\n");
        return 3;
    }
    ComPtr<IDispatch> sink;
    sink.Attach(raw_sink);
    DWORD cookie = 0;
    result = connection_point->Advise(sink.Get(), &cookie);
    if (FAILED(result)) {
        std::fwprintf(
            stderr,
            L"HancomEventBridge: IHwpObjectEvents Advise failed (0x%08lX)\n",
            static_cast<unsigned long>(result));
        return 3;
    }

    WriteReady(requested_moniker, source_iid);
    const int pump_result = PumpUntilShutdown();
    result = connection_point->Unadvise(cookie);
    if (FAILED(result)) {
        std::fwprintf(
            stderr,
            L"HancomEventBridge: IHwpObjectEvents Unadvise failed (0x%08lX)\n",
            static_cast<unsigned long>(result));
        return 4;
    }
    return pump_result;
}

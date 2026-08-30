#include "OfficialApiProbe.h"

#include "DispatchInvoke.h"
#include "OfficialApiAutomationProbe.h"
#include "OfficialApiCapability.h"
#include "OfficialApiFixture.h"
#include "OfficialApiParameterProbe.h"
#include "OfficialApiState.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cwchar>
#include <cwctype>
#include <filesystem>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

namespace hancom::official_api {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

constexpr LONG kAutoYesMessageBoxMode = 0x00011010;

struct BooleanCall {
    HRESULT status = E_UNEXPECTED;
    LONG returned = -2;
};

class MessageBoxScope final {
public:
    explicit MessageBoxScope(IDispatch* const hwp) noexcept : hwp_(hwp) {}

    ~MessageBoxScope() noexcept {
        if (!active_) {
            return;
        }
        CComVariant ignored;
        static_cast<void>(Method(
            hwp_,
            L"SetMessageBoxMode",
            {CComVariant(previous_)},
            &ignored));
    }

    MessageBoxScope(const MessageBoxScope&) = delete;
    MessageBoxScope& operator=(const MessageBoxScope&) = delete;

    HRESULT Activate() noexcept {
        CComVariant raw;
        const HRESULT status = Method(
            hwp_,
            L"SetMessageBoxMode",
            {CComVariant(kAutoYesMessageBoxMode)},
            &raw);
        if (FAILED(status)) {
            return status;
        }
        const HRESULT converted = AsLong(raw, &previous_);
        active_ = SUCCEEDED(converted);
        return converted;
    }

private:
    IDispatch* hwp_ = nullptr;
    LONG previous_ = 0;
    bool active_ = false;
};

std::vector<std::wstring> Lines(const std::wstring& payload) {
    std::vector<std::wstring> lines;
    size_t start = 0;
    for (;;) {
        const size_t end = payload.find(L'\n', start);
        std::wstring line = payload.substr(start, end - start);
        if (!line.empty() && line.back() == L'\r') {
            line.pop_back();
        }
        lines.push_back(std::move(line));
        if (end == std::wstring::npos) {
            if (lines.size() > 1 && lines.back().empty()) {
                lines.pop_back();
            }
            return lines;
        }
        start = end + 1;
    }
}

std::vector<std::wstring> Fields(const std::wstring& line) {
    std::vector<std::wstring> fields;
    size_t start = 0;
    for (;;) {
        const size_t end = line.find(L'\t', start);
        fields.push_back(line.substr(start, end - start));
        if (end == std::wstring::npos) {
            return fields;
        }
        start = end + 1;
    }
}

bool PlainName(const std::wstring& value) noexcept {
    if (value.empty() || value.size() > 128) {
        return false;
    }
    for (const wchar_t character : value) {
        if (!(iswalnum(character) || character == L'_')) {
            return false;
        }
    }
    return true;
}

CComVariant BooleanVariant(const bool value) noexcept {
    CComVariant result;
    result.vt = VT_BOOL;
    result.boolVal = value ? VARIANT_TRUE : VARIANT_FALSE;
    return result;
}

HRESULT CurrentPosition(
    IDispatch* const hwp,
    LONG* const list,
    LONG* const paragraph,
    LONG* const character) noexcept {
    if (hwp == nullptr || list == nullptr || paragraph == nullptr ||
        character == nullptr) {
        return E_POINTER;
    }
    CComVariant listValue;
    listValue.vt = VT_I4 | VT_BYREF;
    listValue.plVal = list;
    CComVariant paragraphValue;
    paragraphValue.vt = VT_I4 | VT_BYREF;
    paragraphValue.plVal = paragraph;
    CComVariant characterValue;
    characterValue.vt = VT_I4 | VT_BYREF;
    characterValue.plVal = character;
    return Method(
        hwp,
        L"GetPos",
        {listValue, paragraphValue, characterValue},
        nullptr);
}

HRESULT InsertTextFixture(
    IDispatch* const hwp,
    const std::wstring& text,
    const bool selectInsertedText) noexcept {
    HRESULT status = Method(
        hwp, L"Run", {CComVariant(L"MoveDocEnd")}, nullptr);
    LONG startList = 0;
    LONG startParagraph = 0;
    LONG startCharacter = 0;
    if (SUCCEEDED(status)) {
        status = CurrentPosition(
            hwp, &startList, &startParagraph, &startCharacter);
    }
    CComVariant rawAction;
    CComPtr<IDispatch> action;
    if (SUCCEEDED(status)) {
        status = Method(
            hwp,
            L"CreateAction",
            {CComVariant(L"InsertText")},
            &rawAction);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawAction, action);
    }
    CComVariant rawSet;
    CComPtr<IDispatch> set;
    if (SUCCEEDED(status)) {
        status = Method(action, L"CreateSet", {}, &rawSet);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawSet, set);
    }
    CComVariant ignored;
    if (SUCCEEDED(status)) {
        status = Method(action, L"GetDefault", {CComVariant(set)}, &ignored);
    }
    if (SUCCEEDED(status)) {
        status = Method(
            set,
            L"SetItem",
            {CComVariant(L"Text"), CComVariant(text.c_str())},
            &ignored);
    }
    if (SUCCEEDED(status)) {
        status = Method(action, L"Execute", {CComVariant(set)}, &ignored);
    }
    if (!selectInsertedText || FAILED(status)) {
        return status;
    }
    LONG endList = 0;
    LONG endParagraph = 0;
    LONG endCharacter = 0;
    status = CurrentPosition(
        hwp, &endList, &endParagraph, &endCharacter);
    if (FAILED(status)) {
        return status;
    }
    if (startList != endList) {
        return E_UNEXPECTED;
    }
    CComVariant selected;
    status = Method(
        hwp,
        L"SelectText",
        {
            CComVariant(startParagraph),
            CComVariant(startCharacter),
            CComVariant(endParagraph),
            CComVariant(endCharacter),
        },
        &selected);
    bool returned = false;
    if (SUCCEEDED(status)) {
        status = AsBool(selected, &returned);
    }
    return SUCCEEDED(status) && returned ? S_OK : E_FAIL;
}

HRESULT WriteFixtureBitmap(const std::wstring& pathText) noexcept {
    const std::filesystem::path path(pathText);
    std::error_code error;
    std::filesystem::create_directories(path.parent_path(), error);
    if (error) {
        return HRESULT_FROM_WIN32(static_cast<DWORD>(error.value()));
    }
    static constexpr std::uint8_t kBitmap[] = {
        0x42, 0x4D, 0x3A, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x36, 0x00, 0x00, 0x00, 0x28, 0x00,
        0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00,
        0x00, 0x00, 0x01, 0x00, 0x18, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x13, 0x0B,
        0x00, 0x00, 0x13, 0x0B, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
        0xFF, 0x00,
    };
    std::ofstream output(
        path, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!output) {
        return HRESULT_FROM_WIN32(ERROR_OPEN_FAILED);
    }
    output.write(
        reinterpret_cast<const char*>(kBitmap),
        static_cast<std::streamsize>(sizeof(kBitmap)));
    return output ? S_OK : HRESULT_FROM_WIN32(ERROR_WRITE_FAULT);
}

HRESULT InsertAndSelectPicture(
    IDispatch* const hwp,
    const std::wstring& path) noexcept {
    HRESULT status = WriteFixtureBitmap(path);
    CComVariant rawPicture;
    if (SUCCEEDED(status)) {
        status = Method(
            hwp,
            L"InsertPicture",
            {
                CComVariant(path.c_str()),
                BooleanVariant(true),
                CComVariant(3L),
                BooleanVariant(false),
                BooleanVariant(false),
                CComVariant(0L),
                CComVariant(0L),
                CComVariant(0L),
            },
            &rawPicture);
    }
    CComPtr<IDispatch> picture;
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawPicture, picture);
    }
    CComVariant rawId;
    std::wstring instanceId;
    if (SUCCEEDED(status)) {
        status = Method(picture, L"GetCtrlInstID", {}, &rawId);
    }
    if (SUCCEEDED(status)) {
        status = AsString(rawId, &instanceId);
    }
    CComVariant rawSelected;
    if (SUCCEEDED(status)) {
        status = Method(
            hwp,
            L"SelectCtrl",
            {CComVariant(instanceId.c_str()), CComVariant(1L)},
            &rawSelected);
    }
    CComVariant rawCurrent;
    CComPtr<IDispatch> current;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            hwp, L"CurSelectedCtrl", &rawCurrent);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawCurrent, current);
    }
    CComVariant rawCurrentId;
    std::wstring currentId;
    if (SUCCEEDED(status)) {
        status = Method(current, L"GetCtrlInstID", {}, &rawCurrentId);
    }
    if (SUCCEEDED(status)) {
        status = AsString(rawCurrentId, &currentId);
    }
    return SUCCEEDED(status) && currentId == instanceId ? S_OK : E_FAIL;
}

HRESULT PrepareActionFixtures(
    IDispatch* const hwp,
    const std::vector<std::wstring>& inputLines) noexcept {
    for (const std::wstring& line : inputLines) {
        const std::vector<std::wstring> fields = Fields(line);
        if (!fields.empty() &&
            (fields[0] == L"SET" || fields[0] == L"OPTIONS")) {
            continue;
        }
        if (fields.size() != 3 || fields[0] != L"FIXTURE" ||
            fields[2].empty()) {
            return E_INVALIDARG;
        }
        HRESULT status = E_INVALIDARG;
        if (fields[1] == L"TEXT_SELECTION") {
            status = InsertTextFixture(hwp, fields[2], true);
        } else if (fields[1] == L"MODIFY_TEXT") {
            status = InsertTextFixture(hwp, fields[2], false);
        } else if (fields[1] == L"PICTURE_CHANGE") {
            status = InsertAndSelectPicture(hwp, fields[2]);
        }
        if (FAILED(status)) {
            return status;
        }
    }
    return S_OK;
}

HRESULT ParseActionValue(
    const std::wstring& type,
    const std::wstring& text,
    CComVariant* const value) noexcept {
    if (value == nullptr) {
        return E_POINTER;
    }
    if (type == L"BSTR") {
        *value = CComVariant(text.c_str());
        return S_OK;
    }
    wchar_t* end = nullptr;
    errno = 0;
    if (type == L"I4") {
        const long parsed = wcstol(text.c_str(), &end, 10);
        if (errno == ERANGE || end == text.c_str() || *end != L'\0') {
            return E_INVALIDARG;
        }
        *value = CComVariant(static_cast<LONG>(parsed));
        return S_OK;
    }
    const unsigned long long parsed = wcstoull(text.c_str(), &end, 10);
    if (errno == ERANGE || end == text.c_str() || *end != L'\0') {
        return E_INVALIDARG;
    }
    value->Clear();
    if (type == L"UI1" &&
        parsed <= (std::numeric_limits<BYTE>::max)()) {
        value->vt = VT_UI1;
        value->bVal = static_cast<BYTE>(parsed);
        return S_OK;
    }
    if (type == L"UI4" &&
        parsed <= (std::numeric_limits<ULONG>::max)()) {
        value->vt = VT_UI4;
        value->ulVal = static_cast<ULONG>(parsed);
        return S_OK;
    }
    return E_INVALIDARG;
}

HRESULT ApplyActionInputs(
    IDispatch* const parameters,
    const std::vector<std::wstring>& inputLines) noexcept {
    if (parameters == nullptr) {
        return inputLines.empty() ? S_OK : E_POINTER;
    }
    for (const std::wstring& line : inputLines) {
        const std::vector<std::wstring> fields = Fields(line);
        if (!fields.empty() &&
            (fields[0] == L"FIXTURE" || fields[0] == L"OPTIONS")) {
            continue;
        }
        if (fields.size() != 4 || fields[0] != L"SET" ||
            !PlainName(fields[1])) {
            return E_INVALIDARG;
        }
        CComVariant value;
        HRESULT status = ParseActionValue(fields[2], fields[3], &value);
        if (SUCCEEDED(status)) {
            CComVariant ignored;
            status = Method(
                parameters,
                L"SetItem",
                {CComVariant(fields[1].c_str()), value},
                &ignored);
        }
        if (FAILED(status)) {
            return status;
        }
    }
    return S_OK;
}

bool HasActionSetInputs(
    const std::vector<std::wstring>& inputLines) noexcept {
    for (const std::wstring& line : inputLines) {
        const std::vector<std::wstring> fields = Fields(line);
        if (!fields.empty() && fields[0] == L"SET") {
            return true;
        }
    }
    return false;
}

bool HasActionOption(
    const std::vector<std::wstring>& inputLines,
    const wchar_t* const option) noexcept {
    for (const std::wstring& line : inputLines) {
        const std::vector<std::wstring> fields = Fields(line);
        if (fields.empty() || fields[0] != L"OPTIONS") {
            continue;
        }
        for (size_t index = 1; index < fields.size(); ++index) {
            if (fields[index] == option) {
                return true;
            }
        }
    }
    return false;
}

LONG BooleanResult(const CComVariant& raw) noexcept {
    if (raw.vt == VT_EMPTY || raw.vt == VT_NULL) {
        return -1;
    }
    bool value = false;
    return SUCCEEDED(AsBool(raw, &value)) ? (value ? 1L : 0L) : -2L;
}

BooleanCall CallBoolean(
    IDispatch* const object,
    const wchar_t* const method,
    const std::vector<CComVariant>& arguments) noexcept {
    CComVariant raw;
    const HRESULT status = Method(object, method, arguments, &raw);
    return BooleanCall{status, SUCCEEDED(status) ? BooleanResult(raw) : -2L};
}

std::wstring ErrorResponse(const wchar_t* const code, const wchar_t* const message) {
    return std::wstring(L"HCV1\tERROR\t") + code + L'\t' + message;
}

std::wstring ProbeAction(
    IDispatch* const hwp,
    const std::wstring& name,
    const std::vector<std::wstring>& inputLines) {
    const auto started = std::chrono::steady_clock::now();
    const HRESULT fixture = PrepareActionFixtures(hwp, inputLines);
    if (FAILED(fixture)) {
        return ErrorResponse(
            L"FIXTURE_FAILED",
            std::to_wstring(static_cast<LONG>(fixture)).c_str());
    }
    const DocumentState before = CaptureDocumentState(hwp);
    const bool observeDialogs =
        HasActionOption(inputLines, L"DIALOGS_OBSERVE");
    const bool executeOnly =
        HasActionOption(inputLines, L"EXECUTE_ONLY");
    CComVariant rawAction;
    const HRESULT createAction = Method(
        hwp,
        L"CreateAction",
        {CComVariant(name.c_str())},
        &rawAction);
    CComPtr<IDispatch> action;
    HRESULT actionDispatch = createAction;
    if (SUCCEEDED(actionDispatch)) {
        actionDispatch = AsDispatch(rawAction, action);
    }

    HRESULT createSet = E_UNEXPECTED;
    HRESULT setDispatch = E_UNEXPECTED;
    CComPtr<IDispatch> parameters;
    if (SUCCEEDED(actionDispatch)) {
        CComVariant rawSet;
        createSet = Method(action, L"CreateSet", {}, &rawSet);
        if (SUCCEEDED(createSet) && rawSet.vt != VT_EMPTY && rawSet.vt != VT_NULL) {
            setDispatch = AsDispatch(rawSet, parameters);
        } else if (SUCCEEDED(createSet)) {
            setDispatch = S_FALSE;
        }
    }

    BooleanCall getDefault;
    BooleanCall execute;
    BooleanCall run;
    HRESULT messageMode = E_UNEXPECTED;
    if (parameters != nullptr) {
        getDefault = CallBoolean(action, L"GetDefault", {CComVariant(parameters)});
        const HRESULT inputStatus = ApplyActionInputs(parameters, inputLines);
        MessageBoxScope messageBoxes(hwp);
        messageMode =
            observeDialogs ? S_FALSE : messageBoxes.Activate();
        const bool canExecute = SUCCEEDED(inputStatus) &&
            (SUCCEEDED(getDefault.status) || HasActionSetInputs(inputLines));
        if (canExecute && SUCCEEDED(messageMode)) {
            execute = CallBoolean(action, L"Execute", {CComVariant(parameters)});
        } else if (FAILED(inputStatus)) {
            execute.status = inputStatus;
        }
        if (!executeOnly && SUCCEEDED(messageMode) &&
            SUCCEEDED(inputStatus) &&
            (!canExecute || FAILED(execute.status))) {
            run = CallBoolean(action, L"Run", {});
        }
    } else if (SUCCEEDED(actionDispatch)) {
        MessageBoxScope messageBoxes(hwp);
        messageMode =
            observeDialogs ? S_FALSE : messageBoxes.Activate();
        if (SUCCEEDED(messageMode)) {
            if (executeOnly) {
                run = CallBoolean(action, L"Run", {});
            } else {
                CComVariant empty;
                execute = CallBoolean(action, L"Execute", {empty});
                run = CallBoolean(action, L"Run", {});
            }
        }
    } else if (!executeOnly && SUCCEEDED(createAction)) {
        CComVariant rawHAction;
        CComPtr<IDispatch> hAction;
        HRESULT hActionStatus = PropertyGet(hwp, L"HAction", &rawHAction);
        if (SUCCEEDED(hActionStatus)) {
            hActionStatus = AsDispatch(rawHAction, hAction);
        }
        if (SUCCEEDED(hActionStatus)) {
            MessageBoxScope messageBoxes(hwp);
            messageMode =
                observeDialogs ? S_FALSE : messageBoxes.Activate();
            if (SUCCEEDED(messageMode)) {
                run = CallBoolean(
                    hAction,
                    L"Run",
                    {CComVariant(name.c_str())});
            }
        } else {
            run.status = hActionStatus;
        }
    }
    const DocumentState after = CaptureDocumentState(hwp);
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started).count();

    std::wostringstream response;
    response << L"HCV1\tACTION\t" << name
             << L'\t' << static_cast<LONG>(createAction)
             << L'\t' << static_cast<LONG>(actionDispatch)
             << L'\t' << static_cast<LONG>(createSet)
             << L'\t' << static_cast<LONG>(setDispatch)
             << L'\t' << static_cast<LONG>(getDefault.status)
             << L'\t' << getDefault.returned
             << L'\t' << static_cast<LONG>(messageMode)
             << L'\t' << static_cast<LONG>(execute.status)
             << L'\t' << execute.returned
             << L'\t' << static_cast<LONG>(run.status)
             << L'\t' << run.returned;
    AppendDocumentState(response, before);
    AppendDocumentState(response, after);
    response << L'\t' << elapsed;
    return response.str();
}

}

std::wstring Probe(IDispatch* const hwp, const std::wstring& payload) noexcept {
    try {
        const std::vector<std::wstring> lines = Lines(payload);
        if (hwp == nullptr || lines.size() < 3 || lines[0] != L"HCV1" ||
            lines.back() != L"END") {
            return ErrorResponse(L"BAD_REQUEST", L"HCV1 request framing is invalid");
        }
        const std::vector<std::wstring> command = Fields(lines[1]);
        if (command.size() == 2 && command[0] == L"CAPABILITY" &&
            (command[1] == L"TYPELIB_EXTENSION" ||
             command[1] == L"CONTAINMENT" ||
             command[1] == L"STORY_SPINE" ||
             command[1] == L"TEXT_CURRENT" ||
             command[1] == L"TEXT_BODY" ||
             command[1] == L"EFFECTIVE_PROPERTIES" ||
             command[1] == L"CONTROL_ADAPTERS" ||
             command[1] == L"CAPTURE_COORDINATOR" ||
             command[1] == L"IMAGE_GRAPH" ||
             command[1] == L"LAYOUT_GRAPH" ||
             command[1] == L"TABLE_GRAPH" ||
             command[1] == L"TABLE_TOPOLOGY")) {
            return capability::ProbeCapability(
                hwp,
                command[1],
                std::vector<std::wstring>(lines.begin() + 2, lines.end() - 1));
        }
        if (command.size() == 2 && command[0] == L"ACTION" &&
            PlainName(command[1])) {
            return ProbeAction(
                hwp,
                command[1],
                std::vector<std::wstring>(lines.begin() + 2, lines.end() - 1));
        }
        if (command.size() == 3 && command[0] == L"PARAMETER_SET" &&
            PlainName(command[1]) &&
            (command[2] == L"-" || PlainName(command[2]))) {
            return ProbeParameterSet(
                hwp,
                command[1],
                command[2],
                std::vector<std::wstring>(lines.begin() + 2, lines.end() - 1));
        }
        if (command.size() == 4 && command[0] == L"AUTOMATION" &&
            PlainName(command[1]) && PlainName(command[2]) &&
            (command[3] == L"property" || command[3] == L"method" ||
             command[3] == L"event")) {
            std::vector<std::wstring> arguments(lines.begin() + 2, lines.end() - 1);
            if (!arguments.empty() && arguments.front() == L"FIXTURE\tISOLATED") {
                const HRESULT fixture = PrepareIsolatedAutomationFixture(
                    hwp,
                    command[1]);
                if (FAILED(fixture)) {
                    return ErrorResponse(
                        L"FIXTURE_FAILED",
                        std::to_wstring(static_cast<LONG>(fixture)).c_str());
                }
                arguments.erase(arguments.begin());
            }
            return ProbeAutomation(
                hwp,
                command[1],
                command[2],
                command[3],
                arguments);
        }
        return ErrorResponse(L"BAD_REQUEST", L"official API command is invalid");
    } catch (...) {
        return ErrorResponse(L"NATIVE_EXCEPTION", L"official API probe failed unexpectedly");
    }
}

std::wstring Probe(
    IDispatch* const hwp,
    const std::wstring& payload,
    const graph::layout::LayoutEnvironmentPlatformV1* const
        environmentPlatform) noexcept {
    try {
        const std::vector<std::wstring> lines = Lines(payload);
        if (hwp == nullptr || lines.size() < 3 || lines[0] != L"HCV1" ||
            lines.back() != L"END") {
            return ErrorResponse(L"BAD_REQUEST", L"HCV1 request framing is invalid");
        }
        const std::vector<std::wstring> command = Fields(lines[1]);
        if (command.size() == 2 && command[0] == L"CAPABILITY" &&
            (command[1] == L"TYPELIB_EXTENSION" ||
             command[1] == L"CONTAINMENT" ||
             command[1] == L"STORY_SPINE" ||
             command[1] == L"TEXT_CURRENT" ||
             command[1] == L"TEXT_BODY" ||
             command[1] == L"EFFECTIVE_PROPERTIES" ||
             command[1] == L"CONTROL_ADAPTERS" ||
             command[1] == L"CAPTURE_COORDINATOR" ||
             command[1] == L"IMAGE_GRAPH" ||
             command[1] == L"LAYOUT_GRAPH" ||
             command[1] == L"TABLE_GRAPH" ||
             command[1] == L"TABLE_RANGE_BASES" ||
             command[1] == L"TABLE_TOPOLOGY")) {
            return capability::ProbeCapability(
                hwp,
                command[1],
                std::vector<std::wstring>(lines.begin() + 2, lines.end() - 1),
                environmentPlatform);
        }
        if (command.size() == 2 && command[0] == L"ACTION" &&
            PlainName(command[1])) {
            return ProbeAction(
                hwp,
                command[1],
                std::vector<std::wstring>(lines.begin() + 2, lines.end() - 1));
        }
        if (command.size() == 3 && command[0] == L"PARAMETER_SET" &&
            PlainName(command[1]) &&
            (command[2] == L"-" || PlainName(command[2]))) {
            return ProbeParameterSet(
                hwp,
                command[1],
                command[2],
                std::vector<std::wstring>(lines.begin() + 2, lines.end() - 1));
        }
        if (command.size() == 4 && command[0] == L"AUTOMATION" &&
            PlainName(command[1]) && PlainName(command[2]) &&
            (command[3] == L"property" || command[3] == L"method" ||
             command[3] == L"event")) {
            std::vector<std::wstring> arguments(lines.begin() + 2, lines.end() - 1);
            if (!arguments.empty() && arguments.front() == L"FIXTURE\tISOLATED") {
                const HRESULT fixture = PrepareIsolatedAutomationFixture(
                    hwp,
                    command[1]);
                if (FAILED(fixture)) {
                    return ErrorResponse(
                        L"FIXTURE_FAILED",
                        std::to_wstring(static_cast<LONG>(fixture)).c_str());
                }
                arguments.erase(arguments.begin());
            }
            return ProbeAutomation(
                hwp,
                command[1],
                command[2],
                command[3],
                arguments);
        }
        return ErrorResponse(L"BAD_REQUEST", L"official API command is invalid");
    } catch (...) {
        return ErrorResponse(L"NATIVE_EXCEPTION", L"official API probe failed unexpectedly");
    }
}

}

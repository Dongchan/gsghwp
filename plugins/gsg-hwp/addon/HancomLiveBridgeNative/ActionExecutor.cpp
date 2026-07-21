#include "ActionExecutor.h"

#include "DispatchInvoke.h"
#include "OfficialApiState.h"
#include "ParagraphFormatting.h"

#include <WinCrypt.h>
#include <atlbase.h>
#include <atlcomcli.h>
#include <wincodec.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <cwctype>
#include <filesystem>
#include <fstream>
#include <limits>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#pragma comment(lib, "windowscodecs.lib")

namespace hancom::actions {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;
using hancom::dispatch::PropertyPut;

struct Position {
    LONG list = 0;
    LONG paragraph = 0;
    LONG character = 0;
};

struct Selection {
    bool selected = false;
    Position start;
    Position end;
};

struct Context {
    CComPtr<IDispatch> hwp;
    CComPtr<IDispatch> action;
    CComPtr<IDispatch> table;
    std::wstring tableId;
    std::wstring currentCell;
    std::map<std::wstring, LONG> cells;
    hancom::formatting::ParagraphFormat copiedTableAnchorFormat;
    bool hasCopiedTableAnchorFormat = false;
    std::wstring copiedTableBlock;
    bool hasCopiedTableBlock = false;
    const Request* request = nullptr;
    ExecutionResult* result = nullptr;
};

constexpr std::uintmax_t kMaximumBlockFileBytes = 256ULL * 1024ULL * 1024ULL;
constexpr char kEncodedBlockMagic[] = "GSG_HWP_ENCODED_BLOCK_V1\n";

bool SetError(
    ExecutionResult* const result,
    std::wstring code,
    std::wstring location,
    std::wstring message) {
    result->error.code = std::move(code);
    result->error.location = std::move(location);
    result->error.message = std::move(message);
    return false;
}

std::wstring HResultText(const wchar_t* const operation, const HRESULT status) {
    std::wostringstream text;
    text << operation << L" failed (HRESULT 0x" << std::hex
         << static_cast<unsigned long>(status) << L')';
    return text.str();
}

class MessageBoxModeScope final {
public:
    bool Activate(IDispatch* const hwp, ExecutionResult* const result) {
        CComVariant previous;
        HRESULT status = Method(
            hwp,
            L"SetMessageBoxMode",
            {CComVariant(static_cast<LONG>(0x00011010))},
            &previous);
        LONG previousMode = 0;
        if (SUCCEEDED(status)) {
            status = AsLong(previous, &previousMode);
        }
        if (FAILED(status)) {
            return SetError(
                result,
                L"MESSAGE_BOX_MODE",
                L"SetMessageBoxMode",
                HResultText(L"enable automatic yes", status));
        }
        hwp_ = hwp;
        previousMode_ = previousMode;
        active_ = true;
        return true;
    }

    bool Restore(ExecutionResult* const result) noexcept {
        if (!active_) {
            return true;
        }
        active_ = false;
        CComVariant ignored;
        const HRESULT status = Method(
            hwp_,
            L"SetMessageBoxMode",
            {CComVariant(previousMode_)},
            &ignored);
        if (FAILED(status)) {
            return SetError(
                result,
                L"MESSAGE_BOX_MODE",
                L"SetMessageBoxMode",
                HResultText(L"restore message box mode", status));
        }
        return true;
    }

    ~MessageBoxModeScope() noexcept {
        if (!active_) {
            return;
        }
        try {
            CComVariant ignored;
            static_cast<void>(Method(
                hwp_,
                L"SetMessageBoxMode",
                {CComVariant(previousMode_)},
                &ignored));
        } catch (...) {
        }
    }

private:
    CComPtr<IDispatch> hwp_;
    LONG previousMode_ = 0;
    bool active_ = false;
};

CComVariant BooleanVariant(const bool value) {
    CComVariant result;
    result.vt = VT_BOOL;
    result.boolVal = value ? VARIANT_TRUE : VARIANT_FALSE;
    return result;
}

bool ReadImagePixelSize(
    const std::wstring& path,
    UINT* const width,
    UINT* const height,
    ExecutionResult* const result) {
    CComPtr<IWICImagingFactory> factory;
    HRESULT status = factory.CoCreateInstance(CLSID_WICImagingFactory);
    CComPtr<IWICBitmapDecoder> decoder;
    if (SUCCEEDED(status)) {
        status = factory->CreateDecoderFromFilename(
            path.c_str(),
            nullptr,
            GENERIC_READ,
            WICDecodeMetadataCacheOnDemand,
            &decoder);
    }
    CComPtr<IWICBitmapFrameDecode> frame;
    if (SUCCEEDED(status)) {
        status = decoder->GetFrame(0, &frame);
    }
    if (SUCCEEDED(status)) {
        status = frame->GetSize(width, height);
    }
    if (FAILED(status)) {
        return SetError(result, L"IMAGE_SIZE", path, HResultText(L"read image size", status));
    }
    if (*width == 0 || *height == 0) {
        return SetError(result, L"IMAGE_SIZE", path, L"image has a zero pixel dimension");
    }

    CComPtr<IWICMetadataQueryReader> metadata;
    PROPVARIANT orientation;
    PropVariantInit(&orientation);
    unsigned long value = 1;
    if (SUCCEEDED(frame->GetMetadataQueryReader(&metadata)) &&
        SUCCEEDED(metadata->GetMetadataByName(L"/app1/ifd/{ushort=274}", &orientation))) {
        if (orientation.vt == VT_UI2) {
            value = orientation.uiVal;
        } else if (orientation.vt == VT_UI4) {
            value = orientation.ulVal;
        }
    }
    PropVariantClear(&orientation);
    if (value >= 5 && value <= 8) {
        std::swap(*width, *height);
    }
    return true;
}

bool FitImageInBox(
    const std::wstring& path,
    const double boxWidthMm,
    const double boxHeightMm,
    double* const widthMm,
    double* const heightMm,
    ExecutionResult* const result) {
    UINT pixelWidth = 0;
    UINT pixelHeight = 0;
    if (!ReadImagePixelSize(path, &pixelWidth, &pixelHeight, result)) {
        return false;
    }
    const double scale = (std::min)(
        boxWidthMm / static_cast<double>(pixelWidth),
        boxHeightMm / static_cast<double>(pixelHeight));
    *widthMm = static_cast<double>(pixelWidth) * scale;
    *heightMm = static_cast<double>(pixelHeight) * scale;
    return std::isfinite(*widthMm) && std::isfinite(*heightMm) &&
        *widthMm > 0.0 && *heightMm > 0.0;
}

std::vector<std::wstring> Split(const std::wstring& value, const wchar_t separator) {
    std::vector<std::wstring> fields;
    size_t start = 0;
    for (;;) {
        const size_t end = value.find(separator, start);
        fields.push_back(value.substr(start, end - start));
        if (end == std::wstring::npos) {
            return fields;
        }
        start = end + 1;
    }
}

std::wstring EncodeUtf8Base64(const std::wstring& value) {
    const int byteCount = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        value.c_str(),
        static_cast<int>(value.size()),
        nullptr,
        0,
        nullptr,
        nullptr);
    if (byteCount < 0) {
        return L"";
    }
    std::vector<BYTE> bytes(static_cast<size_t>(byteCount));
    if (byteCount != 0 && WideCharToMultiByte(
            CP_UTF8,
            WC_ERR_INVALID_CHARS,
            value.c_str(),
            static_cast<int>(value.size()),
            reinterpret_cast<LPSTR>(bytes.data()),
            byteCount,
            nullptr,
            nullptr) != byteCount) {
        return L"";
    }
    DWORD encodedCount = 0;
    if (!CryptBinaryToStringW(
            bytes.data(),
            static_cast<DWORD>(bytes.size()),
            CRYPT_STRING_BASE64 | CRYPT_STRING_NOCRLF,
            nullptr,
            &encodedCount)) {
        return L"";
    }
    std::vector<wchar_t> encoded(encodedCount);
    if (!CryptBinaryToStringW(
            bytes.data(),
            static_cast<DWORD>(bytes.size()),
            CRYPT_STRING_BASE64 | CRYPT_STRING_NOCRLF,
            encoded.data(),
            &encodedCount)) {
        return L"";
    }
    return std::wstring(encoded.data());
}

bool GetDispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value,
    ExecutionResult* const result,
    const std::wstring& location = L"") {
    CComVariant property;
    HRESULT status = PropertyGet(object, name, &property);
    if (SUCCEEDED(status)) {
        status = AsDispatch(property, value);
    }
    if (FAILED(status)) {
        return SetError(result, L"COM_PROPERTY", location, HResultText(name, status));
    }
    return true;
}

bool CallBooleanMethod(
    IDispatch* const object,
    const wchar_t* const name,
    const std::vector<CComVariant>& arguments,
    bool* const returned,
    ExecutionResult* const result,
    const std::wstring& location = L"") {
    CComVariant value;
    HRESULT status = Method(object, name, arguments, &value);
    if (FAILED(status)) {
        return SetError(result, L"COM_METHOD", location, HResultText(name, status));
    }
    if (value.vt == VT_EMPTY) {
        *returned = true;
        return true;
    }
    status = AsBool(value, returned);
    if (FAILED(status)) {
        return SetError(result, L"COM_RESULT", location, HResultText(name, status));
    }
    return true;
}

bool RunAction(
    IDispatch* const action,
    const std::wstring& name,
    ExecutionResult* const result,
    const std::wstring& location = L"") {
    bool returned = false;
    if (!CallBooleanMethod(
            action,
            L"Run",
            {CComVariant(name.c_str())},
            &returned,
            result,
            location)) {
        return false;
    }
    if (!returned) {
        return SetError(result, L"ACTION_FAILED", location, name + L" returned false");
    }
    return true;
}

bool RunVerifiedNavigationAction(
    IDispatch* const action,
    const std::wstring& name,
    ExecutionResult* const result,
    const std::wstring& location = L"") {
    bool returned = false;
    return CallBooleanMethod(
        action,
        L"Run",
        {CComVariant(name.c_str())},
        &returned,
        result,
        location);
}

bool GetPosition(IDispatch* const hwp, Position* const position, ExecutionResult* const result) {
    CComVariant list;
    list.vt = VT_I4 | VT_BYREF;
    list.plVal = &position->list;
    CComVariant paragraph;
    paragraph.vt = VT_I4 | VT_BYREF;
    paragraph.plVal = &position->paragraph;
    CComVariant character;
    character.vt = VT_I4 | VT_BYREF;
    character.plVal = &position->character;
    const HRESULT status = Method(hwp, L"GetPos", {list, paragraph, character}, nullptr);
    if (FAILED(status)) {
        return SetError(result, L"COM_METHOD", L"", HResultText(L"GetPos", status));
    }
    return true;
}

bool SetPosition(
    IDispatch* const hwp,
    const Position& position,
    ExecutionResult* const result,
    const std::wstring& location = L"") {
    bool returned = false;
    if (!CallBooleanMethod(
            hwp,
            L"SetPos",
            {
                CComVariant(position.list),
                CComVariant(position.paragraph),
                CComVariant(position.character),
            },
            &returned,
            result,
            location)) {
        return false;
    }
    if (!returned) {
        return SetError(result, L"POSITION_FAILED", location, L"SetPos returned false");
    }
    return true;
}

bool CreateSet(
    IDispatch* const hwp,
    const wchar_t* const name,
    CComPtr<IDispatch>& set,
    ExecutionResult* const result) {
    CComVariant raw;
    HRESULT status = Method(hwp, L"CreateSet", {CComVariant(name)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, set);
    }
    if (FAILED(status)) {
        return SetError(result, L"COM_METHOD", name, HResultText(L"CreateSet", status));
    }
    return true;
}

bool ItemLong(
    IDispatch* const set,
    const wchar_t* const name,
    LONG* const value,
    ExecutionResult* const result) {
    CComVariant raw;
    HRESULT status = Method(set, L"Item", {CComVariant(name)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsLong(raw, value);
    }
    if (FAILED(status)) {
        return SetError(result, L"PARAMETER_ITEM", name, HResultText(L"Item", status));
    }
    return true;
}

bool GetSelection(
    IDispatch* const hwp,
    Selection* const selection,
    ExecutionResult* const result) {
    CComPtr<IDispatch> start;
    CComPtr<IDispatch> end;
    if (!CreateSet(hwp, L"ListParaPos", start, result) ||
        !CreateSet(hwp, L"ListParaPos", end, result)) {
        return false;
    }
    CComVariant raw;
    HRESULT status = Method(
        hwp,
        L"GetSelectedPosBySet",
        {CComVariant(start), CComVariant(end)},
        &raw);
    if (SUCCEEDED(status)) {
        status = AsBool(raw, &selection->selected);
    }
    if (FAILED(status)) {
        return SetError(result, L"SELECTION", L"", HResultText(L"GetSelectedPosBySet", status));
    }
    return ItemLong(start, L"List", &selection->start.list, result) &&
        ItemLong(start, L"Para", &selection->start.paragraph, result) &&
        ItemLong(start, L"Pos", &selection->start.character, result) &&
        ItemLong(end, L"List", &selection->end.list, result) &&
        ItemLong(end, L"Para", &selection->end.paragraph, result) &&
        ItemLong(end, L"Pos", &selection->end.character, result);
}

bool ValidateDocumentIdentity(
    IDispatch* const hwp,
    const Request& request,
    ExecutionResult* const result) {
    CComPtr<IDispatch> documents;
    CComPtr<IDispatch> document;
    if (!GetDispatchProperty(hwp, L"XHwpDocuments", documents, result) ||
        !GetDispatchProperty(documents, L"Active_XHwpDocument", document, result)) {
        return false;
    }
    CComVariant idValue;
    LONG documentId = -1;
    HRESULT status = PropertyGet(document, L"DocumentID", &idValue);
    if (SUCCEEDED(status)) {
        status = AsLong(idValue, &documentId);
    }
    CComVariant pathValue;
    std::wstring fullName;
    if (SUCCEEDED(status)) {
        status = PropertyGet(document, L"FullName", &pathValue);
    }
    if (SUCCEEDED(status)) {
        status = AsString(pathValue, &fullName);
    }
    if (FAILED(status)) {
        return SetError(result, L"DOCUMENT_IDENTITY", L"", HResultText(L"active document", status));
    }
    auto normalize = [](std::wstring value) {
        std::replace(value.begin(), value.end(), L'/', L'\\');
        std::transform(value.begin(), value.end(), value.begin(), towlower);
        return value;
    };
    if (documentId != request.documentId || normalize(fullName) != normalize(request.fullName)) {
        return SetError(result, L"STALE_DOCUMENT", L"", L"active document identity does not match the request");
    }
    return true;
}

bool ValidateExpectedState(
    IDispatch* const hwp,
    const Request& request,
    ExecutionResult* const result) {
    if (request.hasExpectedCursor) {
        Position current;
        if (!GetPosition(hwp, &current, result)) {
            return false;
        }
        if (current.list != request.expectedList ||
            current.paragraph != request.expectedParagraph ||
            current.character != request.expectedCharacter) {
            return SetError(result, L"STALE_CURSOR", L"", L"cursor moved after the request was prepared");
        }
    }
    if (request.hasExpectedSelection) {
        Selection current;
        if (!GetSelection(hwp, &current, result)) {
            return false;
        }
        if (current.selected != request.expectedSelected ||
            current.start.list != request.selectionStartList ||
            current.start.paragraph != request.selectionStartParagraph ||
            current.start.character != request.selectionStartCharacter ||
            current.end.list != request.selectionEndList ||
            current.end.paragraph != request.selectionEndParagraph ||
            current.end.character != request.selectionEndCharacter) {
            return SetError(result, L"STALE_SELECTION", L"", L"selection changed after the request was prepared");
        }
    }
    return true;
}

bool ValidateImagePath(
    const std::wstring& value,
    ExecutionResult* const result) {
    const std::filesystem::path path(value);
    std::error_code error;
    if (!path.is_absolute() || !std::filesystem::is_regular_file(path, error) || error) {
        return SetError(result, L"IMAGE_PATH", value, L"image is not an existing absolute file");
    }
    return true;
}

bool ValidateBlockOutputPath(
    const std::wstring& value,
    ExecutionResult* const result) {
    const std::filesystem::path path(value);
    const std::filesystem::path parent = path.parent_path();
    std::error_code error;
    if (!path.is_absolute() || parent.empty() ||
        !std::filesystem::is_directory(parent, error) || error) {
        return SetError(result, L"BLOCK_OUTPUT_PATH", value, L"block output parent is not an existing absolute directory");
    }
    if (std::filesystem::exists(path, error) || error) {
        return SetError(result, L"BLOCK_OUTPUT_EXISTS", value, L"block output file already exists");
    }
    return true;
}

bool ValidateAssets(const Request& request, ExecutionResult* const result) {
    for (const Command& command : request.commands) {
        if (command.kind == CommandKind::InsertPicture &&
            !ValidateImagePath(command.first, result)) {
            return false;
        }
        if (command.kind == CommandKind::SaveDocumentFile &&
            !ValidateBlockOutputPath(command.first, result)) {
            return false;
        }
        if (command.kind != CommandKind::Action || command.name != L"PictureChange") {
            continue;
        }
        for (const Setter& setter : command.setters) {
            if (setter.path != L"PicturePath") {
                continue;
            }
            if (setter.value.kind != ValueKind::Text) {
                return SetError(
                    result,
                    L"IMAGE_PATH",
                    setter.path,
                    L"PictureChange.PicturePath must be text");
            }
            if (!ValidateImagePath(setter.value.text, result)) {
                return false;
            }
        }
    }
    return true;
}

bool ConvertValue(
    IDispatch* const hwp,
    const Value& value,
    CComVariant* const converted,
    ExecutionResult* const result,
    const std::wstring& location) {
    switch (value.kind) {
    case ValueKind::Integer:
        *converted = CComVariant(value.integer);
        return true;
    case ValueKind::Boolean:
        *converted = BooleanVariant(value.boolean);
        return true;
    case ValueKind::Text:
        *converted = CComVariant(value.text.c_str());
        return true;
    case ValueKind::Millimeter: {
        CComVariant raw;
        HRESULT status = Method(hwp, L"MiliToHwpUnit", {CComVariant(value.millimeter)}, &raw);
        LONG hwpUnit = 0;
        if (SUCCEEDED(status)) {
            status = AsLong(raw, &hwpUnit);
        }
        if (FAILED(status)) {
            return SetError(result, L"VALUE_CONVERSION", location, HResultText(L"MiliToHwpUnit", status));
        }
        *converted = CComVariant(hwpUnit);
        return true;
    }
    case ValueKind::Enumeration: {
        CComVariant raw;
        const HRESULT status = Method(
            hwp,
            value.converter.c_str(),
            {CComVariant(value.text.c_str())},
            &raw);
        if (FAILED(status)) {
            return SetError(result, L"VALUE_CONVERSION", location, HResultText(value.converter.c_str(), status));
        }
        *converted = raw;
        return true;
    }
    }
    return SetError(result, L"VALUE_CONVERSION", location, L"unsupported value kind");
}

bool PutItemOrProperty(
    IDispatch* const object,
    const std::wstring& name,
    const CComVariant& value,
    ExecutionResult* const result,
    const std::wstring& location) {
    HRESULT status = PropertyPut(object, name.c_str(), value);
    if (FAILED(status)) {
        CComVariant ignored;
        status = Method(object, L"SetItem", {CComVariant(name.c_str()), value}, &ignored);
    }
    if (FAILED(status)) {
        return SetError(result, L"PARAMETER_SET", location, HResultText(name.c_str(), status));
    }
    return true;
}

bool ApplySetter(
    IDispatch* const hwp,
    IDispatch* const root,
    const Setter& setter,
    ExecutionResult* const result) {
    const std::vector<std::wstring> path = Split(setter.path, L'/');
    CComPtr<IDispatch> current(root);
    for (size_t index = 0; index + 1 < path.size(); ++index) {
        CComPtr<IDispatch> next;
        if (!GetDispatchProperty(current, path[index].c_str(), next, result, setter.path)) {
            return false;
        }
        current = next;
    }
    CComVariant value;
    return ConvertValue(hwp, setter.value, &value, result, setter.path) &&
        PutItemOrProperty(current, path.back(), value, result, setter.path);
}

bool ReadAppliedSetterValue(
    IDispatch* const parameter,
    const Command& command,
    const Setter& setter,
    CComVariant* const value,
    ExecutionResult* const result) {
    const std::vector<std::wstring> path = Split(setter.path, L'/');
    const size_t first = command.name == L"CellBorder" && path.front() == L"HSet" ? 1 : 0;
    CComPtr<IDispatch> current(parameter);
    for (size_t index = first; index + 1 < path.size(); ++index) {
        CComPtr<IDispatch> next;
        if (!GetDispatchProperty(current, path[index].c_str(), next, result, setter.path)) {
            return false;
        }
        current = next;
    }
    HRESULT status = PropertyGet(current, path.back().c_str(), value);
    if (FAILED(status)) {
        status = Method(
            current,
            L"Item",
            {CComVariant(path.back().c_str())},
            value);
    }
    if (FAILED(status)) {
        return SetError(
            result,
            L"POSTCONDITION",
            setter.path,
            HResultText(L"read applied cell format", status));
    }
    return true;
}

bool AppliedSetterMatches(
    IDispatch* const hwp,
    const Setter& setter,
    const CComVariant& actual,
    ExecutionResult* const result) {
    CComVariant expected;
    if (!ConvertValue(hwp, setter.value, &expected, result, setter.path)) {
        return false;
    }
    if (setter.value.kind == ValueKind::Text) {
        std::wstring actualText;
        std::wstring expectedText;
        return SUCCEEDED(AsString(actual, &actualText)) &&
            SUCCEEDED(AsString(expected, &expectedText)) && actualText == expectedText;
    }
    if (setter.value.kind == ValueKind::Boolean) {
        bool actualBoolean = false;
        bool expectedBoolean = false;
        return SUCCEEDED(AsBool(actual, &actualBoolean)) &&
            SUCCEEDED(AsBool(expected, &expectedBoolean)) && actualBoolean == expectedBoolean;
    }
    LONG actualInteger = 0;
    LONG expectedInteger = 0;
    return SUCCEEDED(AsLong(actual, &actualInteger)) &&
        SUCCEEDED(AsLong(expected, &expectedInteger)) && actualInteger == expectedInteger;
}

bool GoToCell(Context* context, const std::wstring& requested);

bool VerifyAppliedCellFormat(
    Context* const context,
    const Command& command,
    IDispatch* const parameter,
    IDispatch* const set) {
    const bool cellFormatAction =
        command.name == L"CellFill" || command.name == L"CellBorder";
    if (!cellFormatAction) {
        return true;
    }
    if (context->currentCell.empty()) {
        return SetError(
            context->result,
            L"POSTCONDITION",
            command.name,
            L"cell format verification has no target cell");
    }
    if (!GoToCell(context, context->currentCell)) {
        return false;
    }
    CComVariant ignored;
    const HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(command.name.c_str()), CComVariant(set)},
        &ignored);
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"POSTCONDITION",
            command.name,
            HResultText(L"read applied cell format", status));
    }
    for (const Setter& setter : command.setters) {
        CComVariant actual;
        if (!ReadAppliedSetterValue(
                parameter,
                command,
                setter,
                &actual,
                context->result)) {
            return false;
        }
        if (!AppliedSetterMatches(context->hwp, setter, actual, context->result)) {
            return SetError(
                context->result,
                L"POSTCONDITION",
                setter.path,
                L"applied cell format does not match the requested value");
        }
    }
    return true;
}

bool ExecuteParameterAction(Context* const context, const Command& command) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> parameter;
    CComPtr<IDispatch> set;
    if (!GetDispatchProperty(
            context->hwp,
            L"HParameterSet",
            parameterSets,
            context->result,
            command.name) ||
        !GetDispatchProperty(
            parameterSets,
            command.parameterSet.c_str(),
            parameter,
            context->result,
            command.name) ||
        !GetDispatchProperty(parameter, L"HSet", set, context->result, command.name)) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(command.name.c_str()), CComVariant(set)},
        &ignored);
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"ACTION_DEFAULT",
            command.name,
            HResultText(L"GetDefault", status));
    }
    std::map<std::wstring, CComPtr<IDispatch>> arrays;
    for (const auto& [name, count] : command.arrays) {
        CComVariant rawArray;
        status = Method(
            parameter,
            L"CreateItemArray",
            {CComVariant(name.c_str()), CComVariant(count)},
            &rawArray);
        if (FAILED(status)) {
            return SetError(
                context->result,
                L"PARAMETER_ARRAY",
                name,
                HResultText(L"CreateItemArray", status));
        }
        CComPtr<IDispatch> array;
        status = AsDispatch(rawArray, array);
        if (FAILED(status)) {
            return SetError(
                context->result,
                L"PARAMETER_ARRAY",
                name,
                HResultText(L"CreateItemArray result", status));
        }
        arrays.emplace(name, std::move(array));
    }
    for (const ArrayValue& arrayValue : command.arrayValues) {
        const auto array = arrays.find(arrayValue.name);
        if (array == arrays.end()) {
            return SetError(
                context->result,
                L"PARAMETER_ARRAY",
                arrayValue.name,
                L"array was not created");
        }
        CComVariant value;
        if (!ConvertValue(
                context->hwp,
                arrayValue.value,
                &value,
                context->result,
                arrayValue.name)) {
            return false;
        }
        status = Method(
            array->second,
            L"SetItem",
            {CComVariant(arrayValue.index), value},
            &ignored);
        if (FAILED(status)) {
            return SetError(
                context->result,
                L"PARAMETER_ARRAY",
                arrayValue.name,
                HResultText(L"SetItem", status));
        }
    }
    for (const Setter& setter : command.setters) {
        if (!ApplySetter(context->hwp, parameter, setter, context->result)) {
            return false;
        }
    }
    bool executed = false;
    if (!CallBooleanMethod(
            context->action,
            L"Execute",
            {CComVariant(command.name.c_str()), CComVariant(set)},
            &executed,
            context->result,
            command.name)) {
        return false;
    }
    if (!executed) {
        return SetError(
            context->result,
            L"ACTION_FAILED",
            command.name,
            command.name + L" returned false");
    }
    ++context->result->actionsExecuted;
    if (!VerifyAppliedCellFormat(context, command, parameter, set)) {
        context->result->partialMutation = true;
        return false;
    }
    return true;
}

bool CurrentPhysicalPage(
    IDispatch* const hwp,
    LONG* const page,
    ExecutionResult* const result) {
    CComPtr<IDispatch> documents;
    CComPtr<IDispatch> document;
    CComPtr<IDispatch> info;
    if (!GetDispatchProperty(hwp, L"XHwpDocuments", documents, result, L"page") ||
        !GetDispatchProperty(documents, L"Active_XHwpDocument", document, result, L"page") ||
        !GetDispatchProperty(document, L"XHwpDocumentInfo", info, result, L"page")) {
        return false;
    }
    CComVariant raw;
    LONG zeroBased = -1;
    HRESULT status = PropertyGet(info, L"CurrentPage", &raw);
    if (SUCCEEDED(status)) {
        status = AsLong(raw, &zeroBased);
    }
    if (FAILED(status) || zeroBased < 0) {
        return SetError(result, L"PAGE", L"page", HResultText(L"CurrentPage", status));
    }
    *page = zeroBased + 1;
    return true;
}

bool MoveToPage(Context* const context, const LONG requestedPage) {
    CComVariant rawCount;
    LONG pageCount = 0;
    HRESULT status = PropertyGet(context->hwp, L"PageCount", &rawCount);
    if (SUCCEEDED(status)) {
        status = AsLong(rawCount, &pageCount);
    }
    if (FAILED(status)) {
        return SetError(context->result, L"PAGE", L"page", HResultText(L"PageCount", status));
    }
    if (requestedPage < 1 || requestedPage > pageCount) {
        return SetError(
            context->result,
            L"BAD_PAGE",
            std::to_wstring(requestedPage),
            L"requested page is outside the active document");
    }

    Command goTo;
    goTo.kind = CommandKind::Action;
    goTo.name = L"Goto";
    goTo.parameterSet = L"HGotoE";
    Setter dialogResult;
    dialogResult.path = L"HSet/DialogResult";
    dialogResult.value.kind = ValueKind::Integer;
    dialogResult.value.integer = requestedPage;
    Setter selectionIndex;
    selectionIndex.path = L"SetSelectionIndex";
    selectionIndex.value.kind = ValueKind::Integer;
    selectionIndex.value.integer = 1;
    goTo.setters = {std::move(dialogResult), std::move(selectionIndex)};

    ExecutionResult goToResult;
    Context goToContext = *context;
    goToContext.result = &goToResult;
    if (ExecuteParameterAction(&goToContext, goTo)) {
        context->result->actionsExecuted += goToResult.actionsExecuted;
    }

    LONG currentPage = 0;
    if (!CurrentPhysicalPage(context->hwp, &currentPage, context->result)) {
        return false;
    }
    const std::wstring action = currentPage < requestedPage ? L"MovePageDown" : L"MovePageUp";
    while (currentPage != requestedPage) {
        if (!RunAction(context->action, action, context->result, L"page")) {
            return false;
        }
        ++context->result->actionsExecuted;
        const LONG before = currentPage;
        if (!CurrentPhysicalPage(context->hwp, &currentPage, context->result)) {
            return false;
        }
        if (currentPage == before) {
            return SetError(
                context->result,
                L"PAGE_STALLED",
                std::to_wstring(requestedPage),
                L"cursor stopped before reaching the requested page");
        }
    }
    return true;
}

bool GetControlInstanceId(
    IDispatch* const control,
    std::wstring* const controlId,
    ExecutionResult* const result,
    const std::wstring& location = L"") {
    CComVariant value;
    HRESULT status = Method(control, L"GetCtrlInstID", {}, &value);
    if (SUCCEEDED(status)) {
        status = AsString(value, controlId);
    }
    if (FAILED(status)) {
        return SetError(result, L"CONTROL_ID", location, HResultText(L"GetCtrlInstID", status));
    }
    return true;
}

bool AnchorPosition(
    IDispatch* control,
    Position* position,
    ExecutionResult* result);

bool GetSelectedControl(
    Context* const context,
    const std::wstring& instanceId,
    CComPtr<IDispatch>& selectedControl) {
    CComPtr<IDispatch> control;
    if (!GetDispatchProperty(
            context->hwp,
            L"CurSelectedCtrl",
            control,
            context->result,
            instanceId)) {
        return false;
    }
    std::wstring selectedId;
    if (!GetControlInstanceId(control, &selectedId, context->result, instanceId) ||
        selectedId != instanceId) {
        return SetError(
            context->result,
            L"WRONG_CONTROL",
            instanceId,
            L"selected control identity does not match the request");
    }
    selectedControl = control;
    return true;
}

bool SelectControl(
    Context* const context,
    const std::wstring& instanceId,
    CComPtr<IDispatch>& selectedControl) {
    bool selected = false;
    if (!CallBooleanMethod(
            context->hwp,
            L"SelectCtrl",
            {CComVariant(instanceId.c_str()), CComVariant(1L)},
            &selected,
            context->result,
            instanceId)) {
        return false;
    }
    return GetSelectedControl(context, instanceId, selectedControl);
}

bool SelectControl(Context* const context, const std::wstring& instanceId) {
    CComPtr<IDispatch> selectedControl;
    return SelectControl(context, instanceId, selectedControl);
}

bool CaptureCurrentTable(Context* const context) {
    CComPtr<IDispatch> table;
    ExecutionResult parentResult;
    if (!GetDispatchProperty(
            context->hwp,
            L"ParentCtrl",
            table,
            &parentResult,
            L"table") &&
        !GetDispatchProperty(
            context->hwp,
            L"CurSelectedCtrl",
            table,
            context->result,
            L"table")) {
        return false;
    }
    std::wstring id;
    if (!GetControlInstanceId(table, &id, context->result, L"table")) {
        return false;
    }
    context->table = table;
    context->tableId = std::move(id);
    context->cells.clear();
    return true;
}

bool RunAndCountAction(
    Context* const context,
    const std::wstring& action,
    const std::wstring& location) {
    if (!RunAction(context->action, action, context->result, location)) {
        return false;
    }
    ++context->result->actionsExecuted;
    return true;
}

bool DeleteControl(Context* const context, const std::wstring& instanceId) {
    CComPtr<IDispatch> selectedControl;
    if (!SelectControl(context, instanceId, selectedControl)) {
        return false;
    }
    bool deleted = false;
    if (!CallBooleanMethod(
            context->hwp,
            L"DeleteCtrl",
            {CComVariant(selectedControl)},
            &deleted,
            context->result,
            instanceId)) {
        return false;
    }
    if (!deleted) {
        return SetError(
            context->result,
            L"CONTROL_DELETE_FAILED",
            instanceId,
            L"DeleteCtrl returned false");
    }
    ++context->result->actionsExecuted;
    if (context->tableId == instanceId) {
        context->table.Release();
        context->tableId.clear();
        context->cells.clear();
    }
    return true;
}

bool CopyControl(Context* const context, const std::wstring& instanceId) {
    context->table.Release();
    context->tableId.clear();
    context->cells.clear();
    context->copiedTableBlock.clear();
    context->hasCopiedTableBlock = false;
    context->hasCopiedTableAnchorFormat = false;
    CComPtr<IDispatch> control;
    Position anchor;
    if (!SelectControl(context, instanceId, control) ||
        !AnchorPosition(control, &anchor, context->result) ||
        !SetPosition(context->hwp, anchor, context->result, instanceId)) {
        return false;
    }
    const HRESULT formatStatus = hancom::formatting::ReadParagraphFormat(
        context->hwp,
        &context->copiedTableAnchorFormat);
    if (FAILED(formatStatus)) {
        return SetError(
            context->result,
            L"TABLE_ANCHOR_FORMAT",
            instanceId,
            HResultText(L"read source table anchor format", formatStatus));
    }
    context->hasCopiedTableAnchorFormat = true;
    if (anchor.character == (std::numeric_limits<LONG>::max)()) {
        return SetError(
            context->result,
            L"TABLE_BLOCK_RANGE",
            instanceId,
            L"source table anchor character is outside the selectable range");
    }
    bool selected = false;
    if (!CallBooleanMethod(
            context->hwp,
            L"SelectText",
            {
                CComVariant(anchor.paragraph),
                CComVariant(anchor.character),
                CComVariant(anchor.paragraph),
                CComVariant(anchor.character + 1),
            },
            &selected,
            context->result,
            instanceId)) {
        return false;
    }
    if (!selected) {
        return SetError(
            context->result,
            L"TABLE_BLOCK_RANGE",
            instanceId,
            L"source table control range could not be selected");
    }
    CComVariant rawBlock;
    std::wstring block;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"HWP"), CComVariant(L"saveblock:true")},
        &rawBlock);
    if (SUCCEEDED(status)) {
        status = AsString(rawBlock, &block);
    }
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"TABLE_BLOCK_COPY",
            instanceId,
            HResultText(L"GetTextFile HWP saveblock", status));
    }
    if (block.find_first_not_of(L" \t\r\n") == std::wstring::npos) {
        return SetError(
            context->result,
            L"TABLE_BLOCK_COPY",
            instanceId,
            L"GetTextFile returned an empty HWP table block");
    }
    context->copiedTableBlock = std::move(block);
    context->hasCopiedTableBlock = true;
    return SetPosition(context->hwp, anchor, context->result, instanceId) &&
        SelectControl(context, instanceId);
}

bool ApplyCopiedTableAnchor(Context* const context, const std::wstring& instanceId) {
    if (!context->hasCopiedTableAnchorFormat) {
        return SetError(
            context->result,
            L"NO_TABLE_ANCHOR_FORMAT",
            instanceId,
            L"APPLY_COPIED_TABLE_ANCHOR requires a preceding COPY_CONTROL command");
    }
    CComPtr<IDispatch> control;
    Position anchor;
    if (!SelectControl(context, instanceId, control) ||
        !AnchorPosition(control, &anchor, context->result) ||
        !SetPosition(context->hwp, anchor, context->result, instanceId)) {
        return false;
    }
    HRESULT formatStatus = hancom::formatting::ApplyParagraphFormat(
        context->hwp,
        context->copiedTableAnchorFormat);
    if (FAILED(formatStatus)) {
        return SetError(
            context->result,
            L"TABLE_ANCHOR_FORMAT",
            instanceId,
            HResultText(L"apply copied table anchor format", formatStatus));
    }
    context->result->actionsExecuted += 2;
    hancom::formatting::ParagraphFormat actual;
    formatStatus = hancom::formatting::ReadParagraphFormat(context->hwp, &actual);
    if (FAILED(formatStatus) || !(actual == context->copiedTableAnchorFormat)) {
        return SetError(
            context->result,
            L"TABLE_ANCHOR_FORMAT",
            instanceId,
            FAILED(formatStatus)
                ? HResultText(L"verify copied table anchor format", formatStatus)
                : L"target table anchor format does not match its source");
    }
    return SelectControl(context, instanceId);
}

bool PasteTable(Context* const context) {
    if (!context->hasCopiedTableAnchorFormat || !context->hasCopiedTableBlock) {
        return SetError(
            context->result,
            L"NO_TABLE_ANCHOR_FORMAT",
            L"table",
            L"PASTE_TABLE requires a preceding COPY_CONTROL command");
    }
    CComVariant inserted;
    HRESULT status = Method(
        context->hwp,
        L"SetTextFile",
        {
            CComVariant(context->copiedTableBlock.c_str()),
            CComVariant(L"HWP"),
            CComVariant(L"insertfile"),
        },
        &inserted);
    bool insertedBlock = false;
    if (SUCCEEDED(status)) {
        status = AsBool(inserted, &insertedBlock);
    }
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"TABLE_BLOCK_PASTE",
            L"table",
            HResultText(L"SetTextFile HWP insertfile", status));
    }
    if (!insertedBlock) {
        return SetError(
            context->result,
            L"TABLE_BLOCK_PASTE",
            L"table",
            L"SetTextFile returned false");
    }
    if (!RunAndCountAction(context, L"SelectCtrlReverse", L"table") ||
        !CaptureCurrentTable(context)) {
        return false;
    }
    Position anchor;
    hancom::formatting::ParagraphFormat actual;
    if (!AnchorPosition(context->table, &anchor, context->result) ||
        !SetPosition(context->hwp, anchor, context->result, context->tableId)) {
        return false;
    }
    HRESULT formatStatus = hancom::formatting::ReadParagraphFormat(context->hwp, &actual);
    if (FAILED(formatStatus) || !(actual == context->copiedTableAnchorFormat)) {
        if (SUCCEEDED(formatStatus)) {
            formatStatus = hancom::formatting::ApplyParagraphFormat(
                context->hwp,
                context->copiedTableAnchorFormat);
            if (SUCCEEDED(formatStatus)) {
                context->result->actionsExecuted += 2;
                formatStatus = hancom::formatting::ReadParagraphFormat(context->hwp, &actual);
            }
        }
        if (FAILED(formatStatus) || !(actual == context->copiedTableAnchorFormat)) {
            return SetError(
                context->result,
                L"TABLE_ANCHOR_FORMAT",
                context->tableId,
                FAILED(formatStatus)
                    ? HResultText(L"verify pasted table anchor format", formatStatus)
                    : L"pasted table anchor format does not match its source");
        }
    }
    if (!SelectControl(context, context->tableId)) {
        return false;
    }
    context->result->createdControlIds.push_back(context->tableId);
    return true;
}

bool SetTableTreatAsCharacter(Context* const context) {
    if (context->table == nullptr) {
        return SetError(context->result, L"NO_TABLE", L"", L"created table control is unavailable");
    }
    CComPtr<IDispatch> properties;
    if (!GetDispatchProperty(
            context->table,
            L"Properties",
            properties,
            context->result,
            L"TreatAsChar")) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        properties,
        L"SetItem",
        {CComVariant(L"TreatAsChar"), BooleanVariant(true)},
        &ignored);
    if (SUCCEEDED(status)) {
        status = PropertyPut(context->table, L"Properties", CComVariant(properties));
    }
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"TABLE_PROPERTY",
            L"TreatAsChar",
            HResultText(L"TreatAsChar", status));
    }
    return true;
}

bool GetParentControlId(
    IDispatch* const hwp,
    std::wstring* const id,
    ExecutionResult* const result) {
    CComPtr<IDispatch> parent;
    return GetDispatchProperty(hwp, L"ParentCtrl", parent, result) &&
        GetControlInstanceId(parent, id, result);
}

LONG ExtendTableListEnd(Context* const context, const LONG navigationEnd) {
    LONG extendedEnd = navigationEnd;
    for (long long candidate = static_cast<long long>(navigationEnd) + 1;
         candidate <= (std::numeric_limits<LONG>::max)() &&
         candidate - navigationEnd <= 200'000;
         ++candidate) {
        ExecutionResult local;
        if (!SetPosition(
                context->hwp,
                Position{static_cast<LONG>(candidate), 0, 0},
                &local,
                L"table cell range")) {
            break;
        }
        std::wstring parent;
        local = ExecutionResult{};
        if (!GetParentControlId(context->hwp, &parent, &local) ||
            parent != context->tableId) {
            break;
        }
        extendedEnd = static_cast<LONG>(candidate);
    }
    return extendedEnd;
}

std::wstring GetCellAddress(IDispatch* const hwp) {
    LONG sectionCount = 0;
    LONG sectionNumber = 0;
    LONG pageNumber = 0;
    LONG column = 0;
    LONG line = 0;
    LONG position = 0;
    SHORT over = 0;
    BSTR controlName = nullptr;
    CComVariant sectionCountArgument;
    sectionCountArgument.vt = VT_I4 | VT_BYREF;
    sectionCountArgument.plVal = &sectionCount;
    CComVariant sectionNumberArgument;
    sectionNumberArgument.vt = VT_I4 | VT_BYREF;
    sectionNumberArgument.plVal = &sectionNumber;
    CComVariant pageNumberArgument;
    pageNumberArgument.vt = VT_I4 | VT_BYREF;
    pageNumberArgument.plVal = &pageNumber;
    CComVariant columnArgument;
    columnArgument.vt = VT_I4 | VT_BYREF;
    columnArgument.plVal = &column;
    CComVariant lineArgument;
    lineArgument.vt = VT_I4 | VT_BYREF;
    lineArgument.plVal = &line;
    CComVariant positionArgument;
    positionArgument.vt = VT_I4 | VT_BYREF;
    positionArgument.plVal = &position;
    CComVariant overArgument;
    overArgument.vt = VT_I2 | VT_BYREF;
    overArgument.piVal = &over;
    CComVariant controlNameArgument;
    controlNameArgument.vt = VT_BSTR | VT_BYREF;
    controlNameArgument.pbstrVal = &controlName;
    CComVariant returned;
    const HRESULT status = Method(
        hwp,
        L"KeyIndicator",
        {
            sectionCountArgument,
            sectionNumberArgument,
            pageNumberArgument,
            columnArgument,
            lineArgument,
            positionArgument,
            overArgument,
            controlNameArgument,
        },
        &returned);
    if (FAILED(status)) {
        if (controlName != nullptr) {
            SysFreeString(controlName);
        }
        return L"";
    }
    const std::wstring indicator = controlName == nullptr
        ? std::wstring()
        : std::wstring(controlName, SysStringLen(controlName));
    if (controlName != nullptr) {
        SysFreeString(controlName);
    }
    const size_t opening = indicator.find(L'(');
    const size_t closing = indicator.find(L')', opening == std::wstring::npos ? 0 : opening + 1);
    if (opening == std::wstring::npos || closing == std::wstring::npos || closing <= opening + 1) {
        return L"";
    }
    std::wstring address = indicator.substr(opening + 1, closing - opening - 1);
    std::transform(address.begin(), address.end(), address.begin(), towupper);
    return address;
}

bool EnterCapturedTable(Context* const context) {
    std::wstring parent;
    ExecutionResult local;
    if (GetParentControlId(context->hwp, &parent, &local) &&
        parent == context->tableId) {
        return true;
    }
    if (!SelectControl(context, context->tableId) ||
        !RunAndCountAction(
            context,
            L"ShapeObjTextBoxEdit",
            context->tableId)) {
        return false;
    }
    parent.clear();
    local = ExecutionResult{};
    if (!GetParentControlId(context->hwp, &parent, &local) ||
        parent != context->tableId) {
        return SetError(
            context->result,
            L"TABLE_CONTEXT",
            context->tableId,
            L"selected table did not enter its editable cell context");
    }
    return true;
}

bool BuildCellMap(Context* const context) {
    if ((context->table == nullptr && !CaptureCurrentTable(context)) ||
        !EnterCapturedTable(context)) {
        return false;
    }
    if (!RunVerifiedNavigationAction(
            context->action,
            L"TableColEnd",
            context->result,
            L"table cell range") ||
        !RunVerifiedNavigationAction(
            context->action,
            L"TableColPageDown",
            context->result,
            L"table cell range")) {
        return false;
    }
    Position end;
    if (!GetPosition(context->hwp, &end, context->result) ||
        !RunVerifiedNavigationAction(
            context->action,
            L"TableColBegin",
            context->result,
            L"table cell range") ||
        !RunVerifiedNavigationAction(
            context->action,
            L"TableColPageUp",
            context->result,
            L"table cell range")) {
        return false;
    }
    Position first;
    if (!GetPosition(context->hwp, &first, context->result)) {
        return false;
    }
    const LONG lastList = ExtendTableListEnd(context, end.list);
    if (lastList < first.list || lastList - first.list > 200'000) {
        return SetError(context->result, L"TABLE_RANGE", L"", L"table cell list range is invalid");
    }
    context->cells.clear();
    for (LONG list = first.list; list <= lastList; ++list) {
        bool positioned = false;
        ExecutionResult local;
        if (!CallBooleanMethod(
                context->hwp,
                L"SetPos",
                {CComVariant(list), CComVariant(0L), CComVariant(0L)},
                &positioned,
                &local) ||
            !positioned) {
            continue;
        }
        std::wstring parent;
        local = ExecutionResult{};
        if (!GetParentControlId(context->hwp, &parent, &local) || parent != context->tableId) {
            continue;
        }
        CComVariant shape;
        if (FAILED(PropertyGet(context->hwp, L"CellShape", &shape))) {
            continue;
        }
        const std::wstring address = GetCellAddress(context->hwp);
        if (!address.empty()) {
            context->cells.emplace(address, list);
        }
    }
    if (context->cells.empty()) {
        return SetError(context->result, L"TABLE_RANGE", L"", L"table has no addressable cells");
    }
    return true;
}

std::wstring NormalizeAddress(std::wstring address) {
    std::transform(address.begin(), address.end(), address.begin(), towupper);
    return address;
}

bool GoToCell(Context* const context, const std::wstring& requested) {
    const std::wstring address = NormalizeAddress(requested);
    if ((context->cells.empty() && !BuildCellMap(context)) ||
        context->cells.find(address) == context->cells.end()) {
        if (!context->cells.empty()) {
            return SetError(context->result, L"CELL_NOT_FOUND", address, L"cell is not present in the current table");
        }
        return false;
    }
    const auto enter = [&](ExecutionResult* const result) {
        if (!SetPosition(
                context->hwp,
                Position{context->cells.at(address), 0, 0},
                result,
                address)) {
            return false;
        }
        std::wstring parent;
        ExecutionResult local;
        return GetParentControlId(context->hwp, &parent, &local) &&
            parent == context->tableId && GetCellAddress(context->hwp) == address;
    };
    ExecutionResult firstAttempt;
    if (enter(&firstAttempt)) {
        context->currentCell = address;
        return true;
    }
    context->cells.clear();
    if (!BuildCellMap(context)) {
        return false;
    }
    if (context->cells.find(address) == context->cells.end()) {
        return SetError(context->result, L"CELL_NOT_FOUND", address, L"cell is not present after refreshing the table map");
    }
    if (!enter(context->result)) {
        return SetError(context->result, L"WRONG_CELL", address, L"cursor did not enter the requested cell");
    }
    context->currentCell = address;
    return true;
}

bool ParseAddress(const std::wstring& raw, LONG* const row, LONG* const column) {
    const std::wstring address = NormalizeAddress(raw);
    size_t index = 0;
    long long parsedColumn = 0;
    while (index < address.size() && address[index] >= L'A' && address[index] <= L'Z') {
        parsedColumn = parsedColumn * 26 + address[index] - L'A' + 1;
        ++index;
    }
    if (index == 0 || index >= address.size()) {
        return false;
    }
    wchar_t* end = nullptr;
    const long parsedRow = wcstol(address.c_str() + index, &end, 10);
    if (end == nullptr || *end != L'\0' || parsedRow < 1 || parsedColumn < 1 ||
        parsedColumn > (std::numeric_limits<LONG>::max)()) {
        return false;
    }
    *row = parsedRow - 1;
    *column = static_cast<LONG>(parsedColumn - 1);
    return true;
}

std::wstring FormatAddress(const LONG row, LONG column) {
    std::wstring letters;
    ++column;
    while (column > 0) {
        const LONG remainder = (column - 1) % 26;
        letters.insert(letters.begin(), static_cast<wchar_t>(L'A' + remainder));
        column = (column - 1) / 26;
    }
    return letters + std::to_wstring(row + 1);
}

bool MergeCells(Context* const context, const std::wstring& first, const std::wstring& second) {
    LONG startRow = 0;
    LONG startColumn = 0;
    LONG endRow = 0;
    LONG endColumn = 0;
    if (!ParseAddress(first, &startRow, &startColumn) ||
        !ParseAddress(second, &endRow, &endColumn) ||
        endRow < startRow || endColumn < startColumn ||
        (endRow == startRow && endColumn == startColumn)) {
        return SetError(context->result, L"MERGE_RANGE", first, L"merge range is invalid");
    }
    if (!GoToCell(context, first) ||
        !RunAction(context->action, L"TableCellBlock", context->result, first) ||
        !RunAction(context->action, L"TableCellBlockExtend", context->result, first)) {
        return false;
    }
    for (LONG column = startColumn; column < endColumn; ++column) {
        if (!RunAction(context->action, L"TableRightCell", context->result, second)) {
            return false;
        }
    }
    for (LONG row = startRow; row < endRow; ++row) {
        if (!RunAction(context->action, L"TableLowerCell", context->result, second)) {
            return false;
        }
    }
    if (GetCellAddress(context->hwp) != NormalizeAddress(second)) {
        return SetError(context->result, L"MERGE_RANGE", second, L"merge endpoint was not reached");
    }
    if (!RunAction(context->action, L"TableMergeCell", context->result, first)) {
        return false;
    }
    Position anchor;
    if (!GetPosition(context->hwp, &anchor, context->result)) {
        return false;
    }
    const std::wstring normalizedFirst = NormalizeAddress(first);
    context->cells[normalizedFirst] = anchor.list;
    for (LONG row = startRow; row <= endRow; ++row) {
        for (LONG column = startColumn; column <= endColumn; ++column) {
            const std::wstring address = FormatAddress(row, column);
            if (address != normalizedFirst) {
                context->cells.erase(address);
            }
        }
    }
    return true;
}

bool InsertText(Context* const context, const std::wstring& text, const std::wstring& location) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> insertText;
    CComPtr<IDispatch> set;
    if (!GetDispatchProperty(context->hwp, L"HParameterSet", parameterSets, context->result, location) ||
        !GetDispatchProperty(parameterSets, L"HInsertText", insertText, context->result, location) ||
        !GetDispatchProperty(insertText, L"HSet", set, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"InsertText"), CComVariant(set)},
        &ignored);
    if (SUCCEEDED(status)) {
        status = PropertyPut(insertText, L"Text", CComVariant(text.c_str()));
    }
    if (FAILED(status)) {
        return SetError(context->result, L"INSERT_TEXT", location, HResultText(L"InsertText", status));
    }
    bool executed = false;
    if (!CallBooleanMethod(
            context->action,
            L"Execute",
            {CComVariant(L"InsertText"), CComVariant(set)},
            &executed,
            context->result,
            location)) {
        return false;
    }
    if (!executed) {
        return SetError(context->result, L"INSERT_TEXT", location, L"InsertText returned false");
    }
    ++context->result->textInsertions;
    return true;
}

bool SetCellText(Context* const context, const Command& command) {
    const std::wstring address = NormalizeAddress(command.first);
    if (!GoToCell(context, address) ||
        !RunAction(context->action, L"SelectAll", context->result, address)) {
        return false;
    }
    return InsertText(context, command.second, address);
}

bool ReplaceSelection(Context* const context, const Command& command) {
    Selection before;
    if (!GetSelection(context->hwp, &before, context->result)) {
        return false;
    }
    if (!before.selected) {
        return SetError(
            context->result,
            L"NO_SELECTION",
            L"selection",
            L"the replacement command requires an active text selection");
    }
    if (before.start.list != before.end.list) {
        return SetError(
            context->result,
            L"CROSS_CONTROL_SELECTION",
            L"selection",
            L"the replacement selection crosses HWP controls");
    }

    CComVariant raw;
    std::wstring selected;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"UNICODE"), CComVariant(L"saveblock:true")},
        &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, &selected);
    }
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"SELECTION_TEXT",
            L"selection",
            HResultText(L"GetTextFile", status));
    }
    if (selected != command.first) {
        return SetError(
            context->result,
            L"STALE_SELECTION_TEXT",
            L"selection",
            L"selected text changed after the request was prepared");
    }

    Selection confirmed;
    if (!GetSelection(context->hwp, &confirmed, context->result)) {
        return false;
    }
    if (confirmed.selected != before.selected ||
        confirmed.start.list != before.start.list ||
        confirmed.start.paragraph != before.start.paragraph ||
        confirmed.start.character != before.start.character ||
        confirmed.end.list != before.end.list ||
        confirmed.end.paragraph != before.end.paragraph ||
        confirmed.end.character != before.end.character) {
        return SetError(
            context->result,
            L"STALE_SELECTION",
            L"selection",
            L"selection changed while its text was verified");
    }
    return InsertText(context, command.second, L"selection");
}

bool ConfigureImageCell(Context* const context, const std::wstring& location) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> shape;
    CComPtr<IDispatch> shapeSet;
    CComPtr<IDispatch> cell;
    if (!GetDispatchProperty(context->hwp, L"HParameterSet", parameterSets, context->result, location) ||
        !GetDispatchProperty(parameterSets, L"HShapeObject", shape, context->result, location) ||
        !GetDispatchProperty(shape, L"HSet", shapeSet, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"TablePropertyDialog"), CComVariant(shapeSet)},
        &ignored);
    if (FAILED(status) ||
        !PutItemOrProperty(shapeSet, L"ShapeType", CComVariant(3L), context->result, location) ||
        !PutItemOrProperty(shapeSet, L"ShapeCellSize", CComVariant(0L), context->result, location) ||
        !GetDispatchProperty(shape, L"ShapeTableCell", cell, context->result, location)) {
        return false;
    }
    const std::pair<const wchar_t*, LONG> margins[] = {
        {L"HasMargin", 1L},
        {L"MarginLeft", 0L},
        {L"MarginRight", 0L},
        {L"MarginTop", 0L},
        {L"MarginBottom", 0L},
    };
    for (const auto& [name, value] : margins) {
        status = PropertyPut(cell, name, CComVariant(value));
        if (FAILED(status)) {
            return SetError(context->result, L"IMAGE_CELL_FORMAT", location, HResultText(name, status));
        }
    }
    bool executed = false;
    if (!CallBooleanMethod(
            context->action,
            L"Execute",
            {CComVariant(L"TablePropertyDialog"), CComVariant(shapeSet)},
            &executed,
            context->result,
            location) ||
        !executed) {
        return executed ? false : SetError(
            context->result,
            L"IMAGE_CELL_FORMAT",
            location,
            L"TablePropertyDialog returned false");
    }
    CComPtr<IDispatch> paragraph;
    CComPtr<IDispatch> paragraphSet;
    if (!GetDispatchProperty(parameterSets, L"HParaShape", paragraph, context->result, location) ||
        !GetDispatchProperty(paragraph, L"HSet", paragraphSet, context->result, location)) {
        return false;
    }
    status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"ParagraphShape"), CComVariant(paragraphSet)},
        &ignored);
    if (FAILED(status)) {
        return SetError(context->result, L"IMAGE_CELL_FORMAT", location, HResultText(L"GetDefault", status));
    }
    const wchar_t* const fields[] = {
        L"LeftMargin",
        L"RightMargin",
        L"Indentation",
        L"PrevSpacing",
        L"NextSpacing",
    };
    for (const wchar_t* const field : fields) {
        status = PropertyPut(paragraph, field, CComVariant(0L));
        if (FAILED(status)) {
            return SetError(context->result, L"IMAGE_CELL_FORMAT", location, HResultText(field, status));
        }
    }
    if (!CallBooleanMethod(
            context->action,
            L"Execute",
            {CComVariant(L"ParagraphShape"), CComVariant(paragraphSet)},
            &executed,
            context->result,
            location) ||
        !executed) {
        return executed ? false : SetError(
            context->result,
            L"IMAGE_CELL_FORMAT",
            location,
            L"ParagraphShape returned false");
    }
    return true;
}

bool InsertPicture(Context* const context, const Command& command) {
    const std::wstring& path = command.first;
    const std::wstring address = GetCellAddress(context->hwp);
    const bool inCell = !address.empty();
    const std::wstring location = inCell ? address : path;
    if (inCell && !ConfigureImageCell(context, location)) {
        return false;
    }
    CComVariant raw;
    HRESULT status = Method(
        context->hwp,
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
        &raw);
    CComPtr<IDispatch> picture;
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, picture);
    }
    if (FAILED(status)) {
        return SetError(context->result, L"INSERT_IMAGE", location, HResultText(L"InsertPicture", status));
    }
    CComPtr<IDispatch> properties;
    if (!GetDispatchProperty(picture, L"Properties", properties, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    if (command.hasPictureBox) {
        double widthMm = 0.0;
        double heightMm = 0.0;
        if (!FitImageInBox(
                path,
                command.pictureWidthMm,
                command.pictureHeightMm,
                &widthMm,
                &heightMm,
                context->result)) {
            return false;
        }
        Value widthValue;
        widthValue.kind = ValueKind::Millimeter;
        widthValue.millimeter = widthMm;
        Value heightValue;
        heightValue.kind = ValueKind::Millimeter;
        heightValue.millimeter = heightMm;
        CComVariant width;
        CComVariant height;
        if (!ConvertValue(context->hwp, widthValue, &width, context->result, location) ||
            !ConvertValue(context->hwp, heightValue, &height, context->result, location)) {
            return false;
        }
        status = Method(
            properties,
            L"SetItem",
            {CComVariant(L"Width"), width},
            &ignored);
        if (SUCCEEDED(status)) {
            status = Method(
                properties,
                L"SetItem",
                {CComVariant(L"Height"), height},
                &ignored);
        }
    }
    if (SUCCEEDED(status)) {
        status = Method(
            properties,
            L"SetItem",
            {CComVariant(L"TreatAsChar"), BooleanVariant(true)},
            &ignored);
    }
    if (SUCCEEDED(status)) {
        status = PropertyPut(picture, L"Properties", CComVariant(properties));
    }
    if (FAILED(status)) {
        return SetError(context->result, L"INSERT_IMAGE", location, HResultText(L"TreatAsChar", status));
    }
    ++context->result->imageInsertions;
    return true;
}

bool ApplyStyle(Context* const context, const LONG styleId, const std::wstring& location) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> style;
    CComPtr<IDispatch> set;
    if (!GetDispatchProperty(context->hwp, L"HParameterSet", parameterSets, context->result, location) ||
        !GetDispatchProperty(parameterSets, L"HStyle", style, context->result, location) ||
        !GetDispatchProperty(style, L"HSet", set, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"Style"), CComVariant(set)},
        &ignored);
    if (SUCCEEDED(status)) {
        status = PropertyPut(style, L"Apply", CComVariant(styleId));
    }
    if (FAILED(status)) {
        return SetError(context->result, L"STYLE", location, HResultText(L"Style", status));
    }
    bool executed = false;
    return CallBooleanMethod(
               context->action,
               L"Execute",
               {CComVariant(L"Style"), CComVariant(set)},
               &executed,
               context->result,
               location) &&
        (executed || SetError(context->result, L"STYLE", location, L"Style returned false"));
}

Setter IntegerSetter(const std::wstring& path, const LONG value) {
    Setter setter;
    setter.path = path;
    setter.value.kind = ValueKind::Integer;
    setter.value.integer = value;
    return setter;
}

Setter BooleanSetter(const std::wstring& path, const bool value) {
    Setter setter;
    setter.path = path;
    setter.value.kind = ValueKind::Boolean;
    setter.value.boolean = value;
    return setter;
}

Setter TextSetter(const std::wstring& path, const std::wstring& value) {
    Setter setter;
    setter.path = path;
    setter.value.kind = ValueKind::Text;
    setter.value.text = value;
    return setter;
}

bool ApplyCaptionFormat(Context* const context, const Command& caption) {
    Command character;
    character.kind = CommandKind::Action;
    character.name = L"CharShape";
    character.parameterSet = L"HCharShape";
    character.setters = {
        BooleanSetter(L"Bold", caption.captionBold),
        IntegerSetter(L"Height", caption.captionHeight),
        IntegerSetter(L"TextColor", caption.captionTextColor),
    };
    const wchar_t* const languages[] = {
        L"Hangul",
        L"Latin",
        L"Hanja",
        L"Japanese",
        L"Other",
        L"Symbol",
        L"User",
    };
    for (const wchar_t* const language : languages) {
        character.setters.push_back(
            TextSetter(L"FaceName" + std::wstring(language), caption.captionFaceName));
        character.setters.push_back(
            IntegerSetter(L"FontType" + std::wstring(language), 1L));
    }
    if (!ExecuteParameterAction(context, character)) {
        return false;
    }

    Command paragraph;
    paragraph.kind = CommandKind::Action;
    paragraph.name = L"ParagraphShape";
    paragraph.parameterSet = L"HParaShape";
    paragraph.setters = {
        IntegerSetter(L"AlignType", caption.captionAlignment),
        IntegerSetter(L"LineSpacing", caption.captionLineSpacing),
        IntegerSetter(L"LeftMargin", caption.captionLeftMargin),
        IntegerSetter(L"RightMargin", caption.captionRightMargin),
        IntegerSetter(L"Indentation", caption.captionIndentation),
        IntegerSetter(L"PrevSpacing", caption.captionPreviousSpacing),
        IntegerSetter(L"NextSpacing", caption.captionNextSpacing),
    };
    return ExecuteParameterAction(context, paragraph);
}

bool CopyPasteCaptionFormat(Context* const context) {
    Command shape;
    shape.kind = CommandKind::Action;
    shape.name = L"ShapeCopyPaste";
    shape.parameterSet = L"HShapeCopyPaste";
    shape.setters = {IntegerSetter(L"Type", 2L)};
    return ExecuteParameterAction(context, shape);
}

bool AttachCaption(Context* const context, const Command& command) {
    if (context->table == nullptr || context->tableId.empty()) {
        return SetError(context->result, L"NO_TABLE", L"caption", L"current table is unavailable");
    }
    if (command.hasCaptionFormatSource &&
        (!SetPosition(
             context->hwp,
             Position{
                 command.captionFormatSourceList,
                 command.captionFormatSourceParagraph,
                 command.captionFormatSourceCharacter,
             },
             context->result,
             L"caption format source") ||
         !CopyPasteCaptionFormat(context))) {
        return false;
    }
    if (!SelectControl(context, context->tableId)) {
        return false;
    }
    bool detached = false;
    if (!CallBooleanMethod(
            context->action,
            L"Run",
            {CComVariant(L"ShapeObjDetachCaption")},
            &detached,
            context->result,
            L"caption reset")) {
        return false;
    }
    if (detached) {
        ++context->result->actionsExecuted;
    }
    if (!SelectControl(context, context->tableId) ||
        !RunAction(context->action, L"ShapeObjAttachCaption", context->result, L"caption") ||
        !RunAction(context->action, L"MoveParaEnd", context->result, L"caption") ||
        !InsertText(context, command.first, L"caption")) {
        return false;
    }
    if (!RunAction(context->action, L"SelectAll", context->result, L"caption") ||
        !ApplyStyle(context, command.styleId, L"caption")) {
        return false;
    }
    if (command.hasCaptionFormatSource || command.hasCaptionFormat) {
        if (command.hasCaptionFormatSource) {
            if (!CopyPasteCaptionFormat(context)) {
                return false;
            }
        } else if (!ApplyCaptionFormat(context, command)) {
            return false;
        }
    }
    if (!RunAction(context->action, L"CloseEx", context->result, L"caption")) {
        return false;
    }
    return SelectControl(context, context->tableId);
}

bool AnchorPosition(
    IDispatch* const control,
    Position* const position,
    ExecutionResult* const result) {
    CComVariant raw;
    CComPtr<IDispatch> anchor;
    HRESULT status = Method(control, L"GetAnchorPos", {CComVariant(0L)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, anchor);
    }
    if (FAILED(status)) {
        return SetError(result, L"TABLE_ANCHOR", L"", HResultText(L"GetAnchorPos", status));
    }
    return ItemLong(anchor, L"List", &position->list, result) &&
        ItemLong(anchor, L"Para", &position->paragraph, result) &&
        ItemLong(anchor, L"Pos", &position->character, result);
}

bool LeaveTable(Context* const context) {
    if (context->table == nullptr) {
        return SetError(context->result, L"NO_TABLE", L"", L"current table is unavailable");
    }
    Position anchor;
    if (!AnchorPosition(context->table, &anchor, context->result)) {
        return false;
    }
    for (size_t attempt = 0; attempt < 16; ++attempt) {
        Position current;
        if (!GetPosition(context->hwp, &current, context->result)) {
            return false;
        }
        if (current.list == anchor.list) {
            break;
        }
        if (!RunAction(context->action, L"MoveParentList", context->result, L"table")) {
            return false;
        }
    }
    Position current;
    if (!GetPosition(context->hwp, &current, context->result) || current.list != anchor.list) {
        return SetError(context->result, L"TABLE_PARENT", L"", L"table parent list was not reached");
    }
    if (!SetPosition(
            context->hwp,
            Position{anchor.list, anchor.paragraph, anchor.character + 1},
            context->result,
            L"table") ||
        !RunAction(context->action, L"BreakPara", context->result, L"table")) {
        return false;
    }
    context->table.Release();
    context->tableId.clear();
    context->cells.clear();
    return true;
}

bool ReadTail(
    Context* const context,
    const Position& start,
    std::wstring* const selected) {
    if (!SetPosition(context->hwp, start, context->result, L"DELETE_TAIL") ||
        !RunAction(context->action, L"MoveSelDocEnd", context->result, L"DELETE_TAIL")) {
        return false;
    }
    CComVariant raw;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"UNICODE"), CComVariant(L"saveblock:true")},
        &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, selected);
    }
    if (FAILED(status)) {
        return SetError(context->result, L"DELETE_TAIL", L"", HResultText(L"GetTextFile", status));
    }
    return true;
}

bool DeleteTail(Context* const context, const Command& command) {
    const Position start{command.list, command.paragraph, command.character};
    std::wstring selected;
    if (!ReadTail(context, start, &selected)) {
        return false;
    }
    if (selected.rfind(command.first, 0) != 0) {
        static_cast<void>(RunAction(context->action, L"Cancel", context->result, L"DELETE_TAIL"));
        return SetError(context->result, L"STALE_TAIL", L"", L"tail prefix does not match the request");
    }
    return RunAction(context->action, L"Delete", context->result, L"DELETE_TAIL");
}

bool RollbackAppendTail(Context* const context, const Position& start) {
    ExecutionResult rollbackResult;
    Context rollback;
    rollback.hwp = context->hwp;
    rollback.action = context->action;
    rollback.result = &rollbackResult;
    std::wstring expected;
    if (!ReadTail(&rollback, start, &expected)) {
        return false;
    }
    Command command;
    command.kind = CommandKind::DeleteTail;
    command.list = start.list;
    command.paragraph = start.paragraph;
    command.character = start.character;
    command.first = std::move(expected);
    return DeleteTail(&rollback, command);
}

bool CaptureCallResult(
    ExecutionResult* const result,
    const std::wstring& method,
    const CComVariant& raw) {
    CComVariant value;
    const HRESULT status = VariantCopyInd(&value, &raw);
    if (FAILED(status)) {
        return SetError(result, L"CALL_RETURN", method, HResultText(method.c_str(), status));
    }
    CallResult captured;
    captured.method = method;
    switch (value.vt) {
    case VT_EMPTY:
    case VT_NULL:
        captured.value = std::monostate{};
        break;
    case VT_BOOL:
        captured.value = value.boolVal != VARIANT_FALSE;
        break;
    case VT_I1:
        captured.value = static_cast<std::int64_t>(value.cVal);
        break;
    case VT_I2:
        captured.value = static_cast<std::int64_t>(value.iVal);
        break;
    case VT_I4:
        captured.value = static_cast<std::int64_t>(value.lVal);
        break;
    case VT_INT:
        captured.value = static_cast<std::int64_t>(value.intVal);
        break;
    case VT_I8:
        captured.value = static_cast<std::int64_t>(value.llVal);
        break;
    case VT_UI1:
        captured.value = static_cast<std::uint64_t>(value.bVal);
        break;
    case VT_UI2:
        captured.value = static_cast<std::uint64_t>(value.uiVal);
        break;
    case VT_UI4:
        captured.value = static_cast<std::uint64_t>(value.ulVal);
        break;
    case VT_UINT:
        captured.value = static_cast<std::uint64_t>(value.uintVal);
        break;
    case VT_UI8:
        captured.value = static_cast<std::uint64_t>(value.ullVal);
        break;
    case VT_BSTR:
        captured.value = value.bstrVal == nullptr
            ? std::wstring()
            : std::wstring(value.bstrVal, SysStringLen(value.bstrVal));
        break;
    default:
        return SetError(
            result,
            L"CALL_RETURN_TYPE",
            method,
            L"Automation method returned an unsupported VARIANT type " +
                std::to_wstring(value.vt));
    }
    result->callResults.push_back(std::move(captured));
    return true;
}

bool ExecuteCall(Context* const context, const Command& command) {
    std::vector<CComVariant> arguments;
    arguments.reserve(command.arguments.size());
    for (const Value& value : command.arguments) {
        CComVariant converted;
        if (!ConvertValue(
                context->hwp,
                value,
                &converted,
                context->result,
                command.name)) {
            return false;
        }
        arguments.push_back(converted);
    }
    CComVariant returned;
    const HRESULT status = Method(
        context->hwp,
        command.name.c_str(),
        arguments,
        &returned);
    if (FAILED(status)) {
        return SetError(context->result, L"COM_METHOD", command.name, HResultText(command.name.c_str(), status));
    }
    return CaptureCallResult(context->result, command.name, returned);
}

bool SaveEncodedBlockFile(
    Context* const context,
    const std::wstring& pathText,
    const std::wstring& encodedBlock) {
    const size_t magicLength = std::char_traits<char>::length(kEncodedBlockMagic);
    if (encodedBlock.empty() || encodedBlock.size() > kMaximumBlockFileBytes - magicLength) {
        return SetError(context->result, L"BLOCK_FILE_SIZE", pathText, L"encoded HWP block text is empty or exceeds 256 MiB");
    }
    std::vector<BYTE> bytes;
    bytes.reserve(magicLength + encodedBlock.size());
    bytes.insert(bytes.end(), kEncodedBlockMagic, kEncodedBlockMagic + magicLength);
    for (const wchar_t character : encodedBlock) {
        if (character > 0x7f) {
            return SetError(context->result, L"BLOCK_FILE_ENCODING", pathText, L"encoded HWP block text contains a non-ASCII character");
        }
        bytes.push_back(static_cast<BYTE>(character));
    }
    const HANDLE output = CreateFileW(
        pathText.c_str(),
        GENERIC_WRITE,
        0,
        nullptr,
        CREATE_NEW,
        FILE_ATTRIBUTE_TEMPORARY,
        nullptr);
    if (output == INVALID_HANDLE_VALUE) {
        return SetError(context->result, L"BLOCK_FILE_CREATE", pathText, L"block output file could not be created exclusively");
    }
    DWORD written = 0;
    const BOOL writeSucceeded = WriteFile(
        output,
        bytes.data(),
        static_cast<DWORD>(bytes.size()),
        &written,
        nullptr);
    const BOOL closeSucceeded = CloseHandle(output);
    if (!writeSucceeded || written != bytes.size() || !closeSucceeded) {
        static_cast<void>(DeleteFileW(pathText.c_str()));
        return SetError(context->result, L"BLOCK_FILE_WRITE", pathText, L"encoded HWP block could not be written completely");
    }
    return true;
}

bool SaveDocumentFile(Context* const context, const std::wstring& pathText) {
    CComVariant rawDocument;
    std::wstring documentBlock;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"HWP"), CComVariant(L"")},
        &rawDocument);
    if (SUCCEEDED(status)) {
        status = AsString(rawDocument, &documentBlock);
    }
    if (FAILED(status) || documentBlock.empty()) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_CAPTURE",
            pathText,
            FAILED(status)
                ? HResultText(L"GetTextFile HWP document", status)
                : L"GetTextFile returned an empty HWP document");
    }
    return SaveEncodedBlockFile(context, pathText, documentBlock);
}

bool ExecuteCommand(Context* const context, const Command& command) {
    switch (command.kind) {
    case CommandKind::Run:
        if (!RunAction(context->action, command.name, context->result, command.name)) {
            return false;
        }
        ++context->result->actionsExecuted;
        if (command.name == L"TableAppendRow") {
            context->cells.clear();
        }
        return true;
    case CommandKind::Action:
        if (!ExecuteParameterAction(context, command)) {
            return false;
        }
        if (command.name == L"TableCreate") {
            if (!CaptureCurrentTable(context) || !SetTableTreatAsCharacter(context)) {
                return false;
            }
            context->result->createdControlIds.push_back(context->tableId);
        }
        return true;
    case CommandKind::Call:
        return ExecuteCall(context, command);
    case CommandKind::MovePage:
        return MoveToPage(context, command.page);
    case CommandKind::MovePosition:
        return SetPosition(
            context->hwp,
            Position{command.list, command.paragraph, command.character},
            context->result,
            L"MOVE_POSITION");
    case CommandKind::SelectControl:
        return SelectControl(context, command.first);
    case CommandKind::DeleteControl:
        return DeleteControl(context, command.first);
    case CommandKind::CopyControl:
        return CopyControl(context, command.first);
    case CommandKind::SaveDocumentFile:
        return SaveDocumentFile(context, command.first);
    case CommandKind::ApplyCopiedTableAnchor:
        return ApplyCopiedTableAnchor(context, command.first);
    case CommandKind::PasteTable:
        return PasteTable(context);
    case CommandKind::CaptureTable:
        return CaptureCurrentTable(context);
    case CommandKind::MoveDocumentEnd:
        return RunAction(context->action, L"MoveDocEnd", context->result, L"MOVE_DOC_END");
    case CommandKind::DeleteTail:
        return DeleteTail(context, command);
    case CommandKind::InsertText:
        return InsertText(context, command.first, L"text");
    case CommandKind::ReplaceSelection:
        return ReplaceSelection(context, command);
    case CommandKind::InsertPicture:
        return InsertPicture(context, command);
    case CommandKind::Cell:
        return GoToCell(context, command.first);
    case CommandKind::SetCellText:
        return SetCellText(context, command);
    case CommandKind::Merge:
        return MergeCells(context, command.first, command.second);
    case CommandKind::Caption:
        return AttachCaption(context, command);
    case CommandKind::LeaveTable:
        return LeaveTable(context);
    }
    return SetError(context->result, L"COMMAND", L"", L"unsupported command kind");
}

bool ValidateCommandOrder(const Request& request, ExecutionResult* const result) {
    if (request.atomic && (request.commands.empty() ||
        request.commands.front().kind != CommandKind::MoveDocumentEnd)) {
        return SetError(
            result,
            L"ATOMIC_UNSUPPORTED",
            L"POLICY",
            L"atomic rollback requires an append batch starting with MOVE_DOC_END");
    }
    bool copiedTable = false;
    for (const Command& command : request.commands) {
        if (command.kind == CommandKind::CopyControl) {
            copiedTable = true;
            continue;
        }
        if ((command.kind == CommandKind::PasteTable ||
             command.kind == CommandKind::ApplyCopiedTableAnchor) &&
            !copiedTable) {
            const wchar_t* const name = command.kind == CommandKind::PasteTable
                ? L"PASTE_TABLE"
                : L"APPLY_COPIED_TABLE_ANCHOR";
            return SetError(
                result,
                L"NO_TABLE_ANCHOR_FORMAT",
                command.kind == CommandKind::PasteTable ? L"table" : command.first,
                std::wstring(name) + L" requires a preceding COPY_CONTROL command");
        }
    }
    return true;
}

std::wstring StructureDigest(IDispatch* const hwp) {
    const hancom::official_api::DocumentState state =
        hancom::official_api::CaptureDocumentState(hwp);
    if (state.pageCount < 0 || state.controlCount < 0) {
        return L"";
    }
    return std::to_wstring(state.pageCount) + L":" +
        std::to_wstring(state.controlCount) + L":" +
        std::to_wstring(state.controlHash);
}

std::wstring CommandStep(const Command& command) {
    switch (command.kind) {
    case CommandKind::Run:
    case CommandKind::Action:
    case CommandKind::Call:
        return command.name;
    case CommandKind::MovePage:
        return L"MOVE_PAGE";
    case CommandKind::MovePosition:
        return L"MOVE_POSITION";
    case CommandKind::SelectControl:
        return L"SELECT_CONTROL";
    case CommandKind::DeleteControl:
        return L"DELETE_CONTROL";
    case CommandKind::CopyControl:
        return L"COPY_CONTROL";
    case CommandKind::SaveDocumentFile:
        return L"SAVE_DOCUMENT_FILE";
    case CommandKind::ApplyCopiedTableAnchor:
        return L"APPLY_COPIED_TABLE_ANCHOR";
    case CommandKind::PasteTable:
        return L"PASTE_TABLE";
    case CommandKind::CaptureTable:
        return L"CAPTURE_TABLE";
    case CommandKind::MoveDocumentEnd:
        return L"MOVE_DOC_END";
    case CommandKind::DeleteTail:
        return L"DELETE_TAIL";
    case CommandKind::InsertText:
        return L"INSERT_TEXT";
    case CommandKind::ReplaceSelection:
        return L"REPLACE_SELECTION";
    case CommandKind::InsertPicture:
        return L"INSERT_PICTURE";
    case CommandKind::Cell:
        return L"CELL";
    case CommandKind::SetCellText:
        return L"SET_CELL_TEXT";
    case CommandKind::Merge:
        return L"MERGE";
    case CommandKind::Caption:
        return L"CAPTION";
    case CommandKind::LeaveTable:
        return L"LEAVE_TABLE";
    }
    return L"COMMAND";
}

bool CommandMayMutate(const Command& command) {
    switch (command.kind) {
    case CommandKind::Run:
        return command.name != L"SelectCtrlFront" && command.name != L"Cancel";
    case CommandKind::Action:
    case CommandKind::Call:
    case CommandKind::DeleteControl:
    case CommandKind::ApplyCopiedTableAnchor:
    case CommandKind::PasteTable:
    case CommandKind::DeleteTail:
    case CommandKind::InsertText:
    case CommandKind::ReplaceSelection:
    case CommandKind::InsertPicture:
    case CommandKind::SetCellText:
    case CommandKind::Merge:
    case CommandKind::Caption:
        return true;
    case CommandKind::MovePage:
    case CommandKind::MovePosition:
    case CommandKind::SelectControl:
    case CommandKind::CopyControl:
    case CommandKind::SaveDocumentFile:
    case CommandKind::CaptureTable:
    case CommandKind::MoveDocumentEnd:
    case CommandKind::Cell:
    case CommandKind::LeaveTable:
        return false;
    }
    return false;
}

}

ExecutionResult Execute(IDispatch* const hwp, const Request& request) noexcept {
    const auto started = std::chrono::steady_clock::now();
    ExecutionResult result;
    MessageBoxModeScope messageBoxMode;
    bool atomicRollbackAttempted = false;
    bool atomicRollbackSucceeded = false;
    bool hasAtomicAppendStart = false;
    Position atomicAppendStart;
    Context context;
    context.hwp = hwp;
    context.request = &request;
    context.result = &result;
    const auto finish = [
        &result,
        &messageBoxMode,
        &atomicRollbackAttempted,
        &atomicRollbackSucceeded,
        hwp,
        started]() {
        const Error originalError = result.error;
        const std::wstring originalFailedStep = result.failedStep;
        if (!messageBoxMode.Restore(&result)) {
            result.succeeded = false;
        }
        if (!originalError.code.empty()) {
            result.error = originalError;
            result.failedStep = originalFailedStep;
        }
        if (!result.succeeded) {
            bool structureRestored = false;
            if (!result.structureDigestBefore.empty()) {
                result.structureDigestAfter = StructureDigest(hwp);
                structureRestored = !result.structureDigestAfter.empty() &&
                    result.structureDigestAfter == result.structureDigestBefore;
                if (!result.structureDigestAfter.empty() && !structureRestored) {
                    result.partialMutation = true;
                }
            }
            if (atomicRollbackAttempted) {
                result.partialMutation = !atomicRollbackSucceeded || !structureRestored;
            }
            if (result.failedStep.empty()) {
                result.failedStep = result.error.location;
            }
            result.retrySafe = !result.partialMutation;
        }
        result.elapsedMicroseconds = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count();
        return result;
    };
    const auto attemptAtomicRollback = [&]() noexcept {
        if (!request.atomic || !hasAtomicAppendStart || atomicRollbackAttempted ||
            context.action == nullptr) {
            return;
        }
        atomicRollbackAttempted = true;
        try {
            atomicRollbackSucceeded = RollbackAppendTail(&context, atomicAppendStart);
        } catch (...) {
            atomicRollbackSucceeded = false;
        }
    };
    try {
        if (hwp == nullptr) {
            SetError(&result, L"NO_HWP", L"", L"HwpObject is unavailable");
            return finish();
        }
        if (!ValidateCommandOrder(request, &result) ||
            !ValidateDocumentIdentity(hwp, request, &result) ||
            !ValidateExpectedState(hwp, request, &result) ||
            !ValidateAssets(request, &result)) {
            return finish();
        }
        result.structureDigestBefore = StructureDigest(hwp);
        if (!messageBoxMode.Activate(hwp, &result)) {
            return finish();
        }
        if (!GetDispatchProperty(hwp, L"HAction", context.action, &result)) {
            return finish();
        }
        for (const Command& command : request.commands) {
            result.failedStep = CommandStep(command);
            const bool timesPicture = command.kind == CommandKind::InsertPicture;
            const auto commandStarted = timesPicture
                ? std::chrono::steady_clock::now()
                : std::chrono::steady_clock::time_point{};
            const bool commandSucceeded = ExecuteCommand(&context, command);
            if (timesPicture) {
                const long long elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
                    std::chrono::steady_clock::now() - commandStarted).count();
                ++result.imageTimingCount;
                result.imageMaximumMicroseconds = (std::max)(
                    result.imageMaximumMicroseconds,
                    elapsed);
                result.imageTotalMicroseconds += elapsed;
            }
            if (!commandSucceeded) {
                attemptAtomicRollback();
                return finish();
            }
            if (request.atomic && command.kind == CommandKind::MoveDocumentEnd &&
                !hasAtomicAppendStart) {
                if (!GetPosition(hwp, &atomicAppendStart, &result)) {
                    return finish();
                }
                hasAtomicAppendStart = true;
            }
            if (CommandMayMutate(command)) {
                result.partialMutation = true;
            }
            ++result.commandsExecuted;
            result.failedStep.clear();
        }
        result.succeeded = true;
    } catch (const std::exception& error) {
        const char* const message = error.what();
        SetError(
            &result,
            L"NATIVE_EXCEPTION",
            L"",
            std::wstring(message, message + std::strlen(message)));
        attemptAtomicRollback();
    } catch (...) {
        SetError(&result, L"NATIVE_EXCEPTION", L"", L"action batch failed unexpectedly");
        attemptAtomicRollback();
    }
    return finish();
}

std::wstring SuccessResponse(const ExecutionResult& result) {
    std::wostringstream ids;
    for (size_t index = 0; index < result.createdControlIds.size(); ++index) {
        if (index != 0) {
            ids << L',';
        }
        ids << result.createdControlIds[index];
    }
    std::wostringstream calls;
    for (size_t index = 0; index < result.callResults.size(); ++index) {
        if (index != 0) {
            calls << L'\n';
        }
        const CallResult& call = result.callResults[index];
        if (std::holds_alternative<std::monostate>(call.value)) {
            calls << L"V\t" << EncodeUtf8Base64(call.method);
        } else if (const bool* const booleanValue = std::get_if<bool>(&call.value)) {
            calls << L"B\t" << EncodeUtf8Base64(call.method) << L'\t'
                  << (*booleanValue ? 1 : 0);
        } else if (const std::int64_t* const signedValue = std::get_if<std::int64_t>(&call.value)) {
            calls << L"I\t" << EncodeUtf8Base64(call.method) << L'\t' << *signedValue;
        } else if (const std::uint64_t* const unsignedValue =
                       std::get_if<std::uint64_t>(&call.value)) {
            calls << L"I\t" << EncodeUtf8Base64(call.method) << L'\t' << *unsignedValue;
        } else {
            calls << L"S\t" << EncodeUtf8Base64(call.method) << L'\t'
                  << EncodeUtf8Base64(std::get<std::wstring>(call.value));
        }
    }
    std::wostringstream output;
    output << L"HCA2\tOK\t" << result.commandsExecuted << L'\t'
           << result.actionsExecuted << L'\t' << result.textInsertions << L'\t'
           << result.imageInsertions << L'\t' << result.elapsedMicroseconds << L'\t'
           << EncodeUtf8Base64(ids.str()) << L'\t' << EncodeUtf8Base64(calls.str()) << L'\t'
           << result.imageTimingCount << L'\t' << result.imageMaximumMicroseconds << L'\t'
           << result.imageTotalMicroseconds;
    return output.str();
}

std::wstring FailureResponse(const ExecutionResult& result) {
    std::wostringstream output;
    output << L"HCA2\tERROR\t" << result.error.code << L'\t'
           << EncodeUtf8Base64(result.error.location) << L'\t'
           << EncodeUtf8Base64(result.error.message) << L'\t'
           << result.commandsExecuted << L'\t'
           << EncodeUtf8Base64(result.failedStep) << L'\t'
           << (result.partialMutation ? 1 : 0) << L'\t'
           << (result.retrySafe ? 1 : 0) << L'\t'
           << EncodeUtf8Base64(result.structureDigestBefore) << L'\t'
           << EncodeUtf8Base64(result.structureDigestAfter);
    return output.str();
}

}

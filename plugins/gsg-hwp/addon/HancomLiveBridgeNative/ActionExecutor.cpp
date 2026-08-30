#include "ActionExecutor.h"
#include "ActionExecutorInternal.h"

#include "OfficialApiParameterArray.h"
#include "OfficialApiState.h"
#include "ProtocolEncoding.h"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace hancom::actions {
namespace detail {

using hancom::com::FormatHResult;
using hancom::com_state::CaptureSelection;
using hancom::com_state::Position;
using hancom::com_state::SamePosition;
using hancom::com_state::SameSelection;
using hancom::com_state::Selection;
using hancom::com_state::SelectionCaptureFailure;
using hancom::com_state::SelectionCapturePolicy;
using hancom::com_state::SelectionCaptureStage;
using hancom::com_state::kSelectionModeMask;
using hancom::com_state::kSelectionNone;
using hancom::com_state::kSelectionText;
using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;
using hancom::dispatch::PropertyPut;
using hancom::encoding::EncodeUtf8Base64;

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
                FormatHResult(L"enable automatic yes", status));
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
                FormatHResult(L"restore message box mode", status));
        }
        active_ = false;
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

bool GetDispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value,
    ExecutionResult* const result,
    const std::wstring& location) {
    CComVariant property;
    HRESULT status = PropertyGet(object, name, &property);
    if (SUCCEEDED(status)) {
        status = AsDispatch(property, value);
    }
    if (FAILED(status)) {
        return SetError(result, L"COM_PROPERTY", location, FormatHResult(name, status));
    }
    return true;
}

bool CallBooleanMethod(
    IDispatch* const object,
    const wchar_t* const name,
    const std::vector<CComVariant>& arguments,
    bool* const returned,
    ExecutionResult* const result,
    const std::wstring& location) {
    CComVariant value;
    HRESULT status = Method(object, name, arguments, &value);
    if (FAILED(status)) {
        return SetError(result, L"COM_METHOD", location, FormatHResult(name, status));
    }
    if (value.vt == VT_EMPTY) {
        *returned = true;
        return true;
    }
    status = AsBool(value, returned);
    if (FAILED(status)) {
        return SetError(result, L"COM_RESULT", location, FormatHResult(name, status));
    }
    return true;
}

bool RunAction(
    IDispatch* const action,
    const std::wstring& name,
    ExecutionResult* const result,
    const std::wstring& location) {
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
    const std::wstring& location) {
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
    const HRESULT status = hancom::com_state::CapturePosition(hwp, position);
    if (FAILED(status)) {
        return SetError(result, L"COM_METHOD", L"", FormatHResult(L"GetPos", status));
    }
    return true;
}

bool SetPosition(
    IDispatch* const hwp,
    const Position& position,
    ExecutionResult* const result,
    const std::wstring& location) {
    const hancom::com_state::PositionResult positioned =
        hancom::com_state::ApplyPosition(
            hwp,
            position,
            hancom::com_state::EmptyPositionResult::TreatAsSuccess);
    if (FAILED(positioned.invokeStatus)) {
        return SetError(
            result,
            L"COM_METHOD",
            location,
            FormatHResult(L"SetPos", positioned.invokeStatus));
    }
    if (FAILED(positioned.conversionStatus)) {
        return SetError(
            result,
            L"COM_RESULT",
            location,
            FormatHResult(L"SetPos", positioned.conversionStatus));
    }
    if (!positioned.positioned) {
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
        return SetError(result, L"COM_METHOD", name, FormatHResult(L"CreateSet", status));
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
        return SetError(result, L"PARAMETER_ITEM", name, FormatHResult(L"Item", status));
    }
    return true;
}

bool GetSelection(
    IDispatch* const hwp,
    Selection* const selection,
    ExecutionResult* const result,
    const SelectionCapturePolicy policy) {
    SelectionCaptureFailure failure;
    if (CaptureSelection(
            hwp,
            selection,
            policy,
            &failure)) {
        return true;
    }
    if (failure.stage == SelectionCaptureStage::SelectionMode) {
        return SetError(
            result,
            L"STATE_CAPTURE",
            L"",
            FormatHResult(L"SelectionMode", failure.status));
    }
    if (failure.stage == SelectionCaptureStage::CreateSet) {
        return SetError(
            result,
            L"COM_METHOD",
            L"ListParaPos",
            FormatHResult(L"CreateSet", failure.status));
    }
    if (failure.stage == SelectionCaptureStage::SelectedPositions) {
        return SetError(
            result,
            L"SELECTION",
            L"",
            FormatHResult(L"GetSelectedPosBySet", failure.status));
    }
    if (failure.stage == SelectionCaptureStage::PositionItem) {
        return SetError(
            result,
            L"PARAMETER_ITEM",
            failure.detail,
            FormatHResult(L"Item", failure.status));
    }
    return SetError(
        result,
        L"STATE_CAPTURE",
        L"",
        FormatHResult(failure.detail, failure.status));
}

// SelectText refuses ranges the caret can still reach. Measured in HWP
// 2024: a paragraph whose number is drawn automatically answers false for a
// range that lies inside its body, because the drawn number occupies no
// character cell and the call measures against the paragraph's own extent.
// Moving the caret to the start, turning on block selection, and moving to
// the end selects the same range. Whether it actually did is not assumed
// here -- the endpoint comparison in SelectTextRange decides that, exactly
// as it does for a range SelectText accepted.
bool SelectTextRangeUsingCaret(
    Context* const context,
    const Position& start,
    const Position& end,
    const std::wstring& location) {
    return SetPosition(context->hwp, start, context->result, location) &&
        RunAction(context->action, L"Select", context->result, location) &&
        SetPosition(context->hwp, end, context->result, location);
}

bool SelectTextRange(
    Context* const context,
    const Position& start,
    const Position& end,
    const std::wstring& location) {
    if (start.list != end.list || SamePosition(start, end)) {
        return SetError(
            context->result,
            L"TEXT_RANGE",
            location,
            L"text range must be non-empty and stay within one HWP list");
    }
    if (!SetPosition(
            context->hwp,
            Position{start.list, 0, 0},
            context->result,
            location)) {
        return false;
    }
    bool selected = false;
    const bool queried = CallBooleanMethod(
        context->hwp,
        L"SelectText",
        {
            CComVariant(start.paragraph),
            CComVariant(start.character),
            CComVariant(end.paragraph),
            CComVariant(end.character),
        },
        &selected,
        context->result,
        location);
    if (!queried) {
        return selected
            ? false
            : SetError(
                  context->result,
                  L"TEXT_RANGE",
                  location,
                  L"SelectText returned false");
    }
    bool usedCaret = false;
    if (!selected) {
        if (!SelectTextRangeUsingCaret(context, start, end, location)) {
            return false;
        }
        usedCaret = true;
    }
    Selection actual;
    if (!GetSelection(context->hwp, &actual, context->result)) {
        return false;
    }
    if (!actual.selected || !SamePosition(actual.start, start) ||
        !SamePosition(actual.end, end)) {
        std::wostringstream detail;
        detail << L"selected text range does not match the requested endpoints; requested="
               << start.list << L":" << start.paragraph << L":" << start.character
               << L"-" << end.list << L":" << end.paragraph << L":" << end.character
               << L"; actual=" << actual.start.list << L":"
               << actual.start.paragraph << L":" << actual.start.character
               << L"-" << actual.end.list << L":" << actual.end.paragraph
               << L":" << actual.end.character;
        if (usedCaret) {
            detail << L"; SelectText returned false and the caret selection "
                      L"path did not reach the requested range";
        }
        return SetError(
            context->result,
            L"TEXT_RANGE",
            location,
            detail.str());
    }
    return true;
}

bool RestoreTextPosition(
    Context* const context,
    const Selection& original,
    const std::wstring& location) {
    if (original.selected && !SamePosition(original.start, original.end)) {
        return SelectTextRange(context, original.start, original.end, location);
    }
    return SetPosition(context->hwp, original.start, context->result, location);
}

bool ReadSelectedText(
    Context* const context,
    std::wstring* const selected,
    const std::wstring& location) {
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
        return SetError(
            context->result,
            L"SELECTION_TEXT",
            location,
            FormatHResult(L"GetTextFile", status));
    }
    return true;
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
        return SetError(result, L"DOCUMENT_IDENTITY", L"", FormatHResult(L"active document", status));
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
            return SetError(result, L"VALUE_CONVERSION", location, FormatHResult(L"MiliToHwpUnit", status));
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
            return SetError(result, L"VALUE_CONVERSION", location, FormatHResult(value.converter.c_str(), status));
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
        return SetError(result, L"PARAMETER_SET", location, FormatHResult(name.c_str(), status));
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

const wchar_t* DirectShapePropertyName(const std::wstring& path) {
    for (const wchar_t* const name : {
             L"ProtectSize",
             L"WidthRelTo",
             L"Width",
             L"HeightRelTo",
             L"Height",
         }) {
        if (path == name || path == std::wstring(L"HSet/") + name) {
            return name;
        }
    }
    return nullptr;
}

bool ApplyDirectSelectedShapeProperties(
    Context* const context,
    const Command& command) {
    if (command.name != L"ShapeObjDialog") {
        return true;
    }
    const bool hasDirectProperty = std::any_of(
        command.setters.begin(),
        command.setters.end(),
        [](const Setter& setter) {
            return DirectShapePropertyName(setter.path) != nullptr;
        });
    if (!hasDirectProperty) {
        return true;
    }
    CComPtr<IDispatch> control;
    CComPtr<IDispatch> properties;
    if (!GetDispatchProperty(
            context->hwp,
            L"CurSelectedCtrl",
            control,
            context->result,
            command.name) ||
        !GetDispatchProperty(
            control,
            L"Properties",
            properties,
            context->result,
            command.name)) {
        return false;
    }
    CComVariant ignored;
    for (const Setter& setter : command.setters) {
        const wchar_t* const name = DirectShapePropertyName(setter.path);
        if (name == nullptr) {
            continue;
        }
        CComVariant value;
        if (!ConvertValue(
                context->hwp,
                setter.value,
                &value,
                context->result,
                setter.path)) {
            return false;
        }
        const HRESULT status = Method(
            properties,
            L"SetItem",
            {CComVariant(name), value},
            &ignored);
        if (FAILED(status)) {
            return SetError(
                context->result,
                L"SHAPE_PROPERTY",
                setter.path,
                FormatHResult(name, status));
        }
    }
    const HRESULT status =
        PropertyPut(control, L"Properties", CComVariant(properties));
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"SHAPE_PROPERTY",
            command.name,
            FormatHResult(L"Properties", status));
    }
    return true;
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
            FormatHResult(L"read applied cell format", status));
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

bool VerifyAppliedParameterFormat(
    Context* const context,
    const Command& command,
    IDispatch* const parameter,
    IDispatch* const set) {
    const bool cellFormatAction =
        command.name == L"CellFill" || command.name == L"CellBorder";
    const bool textFormatAction =
        command.name == L"CharShape" || command.name == L"ParagraphShape";
    if (!cellFormatAction && !textFormatAction) {
        return true;
    }
    if (cellFormatAction) {
        Selection selection;
        if (!GetSelection(context->hwp, &selection, context->result)) {
            return false;
        }
        const bool liveCellRange =
            (selection.mode & kSelectionModeMask) ==
            hancom::com_state::kSelectionCells;
        if (!liveCellRange) {
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
        }
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
            FormatHResult(L"read applied format", status));
    }
    // One font choice arrives as fourteen setters -- seven FaceName* and seven
    // FontType*, one pair per script (hwp_live_native_text_format.py:121-134) --
    // and this bridge has never claimed to read all fourteen back. Its capture
    // half reads a single face name for every script and reads FontType not at
    // all (ActionTextPatch.cpp:183-195), and the inverse recipe rebuilds them on
    // that same convention (hwp_live_text_patch_batch_history.py:94-97).
    // Verification is held to the contract the rest of the bridge keeps: a font
    // is proven on the face name the capture reads, and the family kind nothing
    // captures is not something the engine promised to echo. Holding the other
    // twelve to an exact echo failed whole batches with POSTCONDITION and
    // retrySafe=false whenever the engine answered with its own normalization.
    // Every setter outside those two families still has to match exactly.
    const bool characterFormatAction = command.name == L"CharShape";
    for (const Setter& setter : command.setters) {
        if (characterFormatAction && setter.path.rfind(L"FontType", 0) == 0) {
            continue;
        }
        Setter read = setter;
        if (characterFormatAction && setter.path.rfind(L"FaceName", 0) == 0) {
            read.path = L"FaceNameHangul";
        }
        CComVariant actual;
        if (!ReadAppliedSetterValue(
                parameter,
                command,
                read,
                &actual,
                context->result)) {
            return false;
        }
        if (!AppliedSetterMatches(context->hwp, setter, actual, context->result)) {
            return SetError(
                context->result,
                L"POSTCONDITION",
                setter.path,
                L"applied format does not match the requested value");
        }
    }
    return true;
}

bool ExecuteParameterAction(
    Context* const context,
    const Command& command,
    const bool verifyFormat) {
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
            FormatHResult(L"GetDefault", status));
    }
    std::map<
        std::wstring,
        std::unique_ptr<hancom::official_api::ParameterArrayWriter>> arrays;
    for (const auto& [name, count] : command.arrays) {
        CComPtr<IDispatch> array;
        status = hancom::official_api::CreateParameterArray(
            parameter, name, count, array);
        if (FAILED(status)) {
            return SetError(
                context->result,
                L"PARAMETER_ARRAY",
                name,
                FormatHResult(L"CreateItemArray", status));
        }
        auto writer =
            std::make_unique<hancom::official_api::ParameterArrayWriter>();
        status = writer->Bind(array);
        if (FAILED(status)) {
            return SetError(
                context->result,
                L"PARAMETER_ARRAY",
                name,
                FormatHResult(L"bind SetItem", status));
        }
        arrays.emplace(name, std::move(writer));
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
        status = array->second->SetItem(arrayValue.index, value);
        if (FAILED(status)) {
            return SetError(
                context->result,
                L"PARAMETER_ARRAY",
                arrayValue.name,
                FormatHResult(L"SetItem", status));
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
    context->result->partialMutation = true;
    if (!ApplyDirectSelectedShapeProperties(context, command)) {
        context->result->partialMutation = true;
        return false;
    }
    ++context->result->actionsExecuted;
    if (verifyFormat &&
        !VerifyAppliedParameterFormat(context, command, parameter, set)) {
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
        return SetError(result, L"PAGE", L"page", FormatHResult(L"CurrentPage", status));
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
    if (SUCCEEDED(status) && pageCount < 1) {
        // Pagination is not finished yet, so there is no range to check
        // against. Unlike an inspection this cannot simply proceed -- Goto
        // needs a real upper bound to tell a valid page from an invalid one --
        // so the engine is asked to finish paginating on the spot and the
        // count is read again. RecalcPageCount is synchronous, so once is
        // enough; a call that does not land leaves the refusal below unchanged.
        // No time budget bounds this call, unlike the Python wait for the same
        // value -- see the budget-asymmetry note on ReadSettledPageCount in
        // LiveInspection.cpp.
        CComVariant recalculated;
        static_cast<void>(
            Method(context->hwp, L"RecalcPageCount", {}, &recalculated));
        status = PropertyGet(context->hwp, L"PageCount", &rawCount);
        if (SUCCEEDED(status)) {
            status = AsLong(rawCount, &pageCount);
        }
    }
    if (FAILED(status)) {
        return SetError(context->result, L"PAGE", L"page", FormatHResult(L"PageCount", status));
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

bool RequiresReferenceLayoutAppendRollback(const Request& request) {
    if (request.commands.empty() ||
        request.commands.front().kind != CommandKind::MoveDocumentEnd) {
        return false;
    }
    return std::any_of(
        request.commands.begin(),
        request.commands.end(),
        [](const Command& command) {
            return command.kind == CommandKind::Action &&
                command.name == L"ReferenceLayoutBulk";
        });
}

}

using namespace detail;

ExecutionResult Execute(IDispatch* const hwp, const Request& request) noexcept {
    const auto started = std::chrono::steady_clock::now();
    ExecutionResult result;
    const bool rollbackAppendTail =
        request.atomic || RequiresReferenceLayoutAppendRollback(request);
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
            result.retrySafe = result.retrySafe && !result.partialMutation;
        }
        result.elapsedMicroseconds = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count();
        return result;
    };
    const auto attemptAtomicRollback = [&]() noexcept {
        if (!rollbackAppendTail || !hasAtomicAppendStart || atomicRollbackAttempted ||
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
        if (rollbackAppendTail) {
            result.structureDigestBefore = StructureDigest(hwp);
        }
        if (!messageBoxMode.Activate(hwp, &result)) {
            return finish();
        }
        if (!GetDispatchProperty(hwp, L"HAction", context.action, &result)) {
            return finish();
        }
        if (!PreflightTableTextCommands(&context, request)) {
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
            if (rollbackAppendTail && command.kind == CommandKind::MoveDocumentEnd &&
                !hasAtomicAppendStart) {
                // MOVE_DOC_END records where the append starts before it may
                // add a paragraph break. Reading the caret here instead would
                // start the rollback after that break and leave it behind.
                if (!context.hasAppendAnchor) {
                    SetError(
                        &result,
                        L"MOVE_DOC_END",
                        L"",
                        L"append anchor was not recorded by MOVE_DOC_END");
                    return finish();
                }
                atomicAppendStart = context.appendAnchor;
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

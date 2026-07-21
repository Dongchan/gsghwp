#include "LiveInspection.h"

#include "DispatchInvoke.h"
#include "ParagraphFormatting.h"
#include "TableInspection.h"

#include <WinCrypt.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <cwctype>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace hancom::inspection {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

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

struct Formatting {
    LONG styleId = -1;
    std::wstring faceName;
    LONG height = 0;
    bool bold = false;
    LONG textColor = 0;
    LONG alignment = 0;
    LONG lineSpacing = 0;
    LONG leftMargin = 0;
    LONG rightMargin = 0;
    LONG indentation = 0;
    LONG previousSpacing = 0;
    LONG nextSpacing = 0;
};

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

std::wstring ErrorResponse(const wchar_t* const code, const std::wstring& message) {
    return L"HCI1\tERROR\t" + std::wstring(code) + L'\t' + EncodeUtf8Base64(message);
}

bool DispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) && SUCCEEDED(AsDispatch(raw, value));
}

bool LongProperty(IDispatch* const object, const wchar_t* const name, LONG* const value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) && SUCCEEDED(AsLong(raw, value));
}

bool StringProperty(
    IDispatch* const object,
    const wchar_t* const name,
    std::wstring* const value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) && SUCCEEDED(AsString(raw, value));
}

bool BoolProperty(IDispatch* const object, const wchar_t* const name, bool* const value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) && SUCCEEDED(AsBool(raw, value));
}

bool GetPosition(IDispatch* const hwp, Position* const position) {
    CComVariant list;
    list.vt = VT_I4 | VT_BYREF;
    list.plVal = &position->list;
    CComVariant paragraph;
    paragraph.vt = VT_I4 | VT_BYREF;
    paragraph.plVal = &position->paragraph;
    CComVariant character;
    character.vt = VT_I4 | VT_BYREF;
    character.plVal = &position->character;
    return SUCCEEDED(Method(hwp, L"GetPos", {list, paragraph, character}, nullptr));
}

bool SetPosition(IDispatch* const hwp, const Position& position) {
    CComVariant returned;
    if (FAILED(Method(
            hwp,
            L"SetPos",
            {
                CComVariant(position.list),
                CComVariant(position.paragraph),
                CComVariant(position.character),
            },
            &returned))) {
        return false;
    }
    bool positioned = false;
    return SUCCEEDED(AsBool(returned, &positioned)) && positioned;
}

bool CreateSet(IDispatch* const hwp, const wchar_t* const name, CComPtr<IDispatch>& set) {
    CComVariant raw;
    return SUCCEEDED(Method(hwp, L"CreateSet", {CComVariant(name)}, &raw)) &&
        SUCCEEDED(AsDispatch(raw, set));
}

bool SetItemLong(IDispatch* const set, const wchar_t* const name, LONG* const value) {
    CComVariant raw;
    return SUCCEEDED(Method(set, L"Item", {CComVariant(name)}, &raw)) &&
        SUCCEEDED(AsLong(raw, value));
}

bool GetSelection(IDispatch* const hwp, Selection* const selection) {
    CComPtr<IDispatch> start;
    CComPtr<IDispatch> end;
    if (!CreateSet(hwp, L"ListParaPos", start) || !CreateSet(hwp, L"ListParaPos", end)) {
        return false;
    }
    CComVariant raw;
    if (FAILED(Method(hwp, L"GetSelectedPosBySet", {CComVariant(start), CComVariant(end)}, &raw)) ||
        FAILED(AsBool(raw, &selection->selected)) ||
        !SetItemLong(start, L"List", &selection->start.list) ||
        !SetItemLong(start, L"Para", &selection->start.paragraph) ||
        !SetItemLong(start, L"Pos", &selection->start.character) ||
        !SetItemLong(end, L"List", &selection->end.list) ||
        !SetItemLong(end, L"Para", &selection->end.paragraph) ||
        !SetItemLong(end, L"Pos", &selection->end.character)) {
        return false;
    }
    return true;
}

bool RestoreSelection(
    IDispatch* const hwp,
    const Position& cursor,
    const Selection& selection) {
    if (!selection.selected || selection.start.list != selection.end.list) {
        return SetPosition(hwp, cursor);
    }
    if (!SetPosition(hwp, Position{selection.start.list, 0, 0})) {
        return false;
    }
    CComVariant raw;
    if (FAILED(Method(
            hwp,
            L"SelectText",
            {
                CComVariant(selection.start.paragraph),
                CComVariant(selection.start.character),
                CComVariant(selection.end.paragraph),
                CComVariant(selection.end.character),
            },
            &raw))) {
        return false;
    }
    bool selected = false;
    return SUCCEEDED(AsBool(raw, &selected)) && selected;
}

bool ActiveDocument(
    IDispatch* const hwp,
    CComPtr<IDispatch>& document,
    CComPtr<IDispatch>& info) {
    CComPtr<IDispatch> documents;
    if (!DispatchProperty(hwp, L"XHwpDocuments", documents) ||
        !DispatchProperty(documents, L"Active_XHwpDocument", document) ||
        !DispatchProperty(document, L"XHwpDocumentInfo", info)) {
        return false;
    }
    return true;
}

bool CurrentPage(IDispatch* const hwp, LONG* const page) {
    CComPtr<IDispatch> document;
    CComPtr<IDispatch> info;
    LONG zeroBased = 0;
    if (!ActiveDocument(hwp, document, info) ||
        !LongProperty(info, L"CurrentPage", &zeroBased)) {
        return false;
    }
    *page = zeroBased + 1;
    return true;
}

bool CurrentPageFromInfo(IDispatch* const info, LONG* const page) {
    LONG zeroBased = 0;
    if (info == nullptr || !LongProperty(info, L"CurrentPage", &zeroBased)) {
        return false;
    }
    *page = zeroBased + 1;
    return true;
}

std::wstring SelectedText(IDispatch* const hwp, const bool selected) {
    if (!selected) {
        return L"";
    }
    CComVariant raw;
    std::wstring text;
    if (SUCCEEDED(Method(
            hwp,
            L"GetTextFile",
            {CComVariant(L"UNICODE"), CComVariant(L"saveblock:true")},
            &raw))) {
        static_cast<void>(AsString(raw, &text));
    }
    return text;
}

bool ControlIdentity(
    IDispatch* const control,
    std::wstring* const type,
    std::wstring* const instance) {
    if (control == nullptr || !StringProperty(control, L"CtrlID", type)) {
        return false;
    }
    CComVariant raw;
    if (SUCCEEDED(Method(control, L"GetCtrlInstID", {}, &raw))) {
        static_cast<void>(AsString(raw, instance));
    }
    return true;
}

void CurrentControl(
    IDispatch* const hwp,
    std::wstring* const type,
    std::wstring* const instance) {
    CComPtr<IDispatch> control;
    if (DispatchProperty(hwp, L"CurSelectedCtrl", control) &&
        ControlIdentity(control, type, instance)) {
        return;
    }
    control.Release();
    if (DispatchProperty(hwp, L"ParentCtrl", control)) {
        static_cast<void>(ControlIdentity(control, type, instance));
    }
}

std::wstring CellAddress(IDispatch* const hwp) {
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

bool AnchorPosition(
    IDispatch* const control,
    Position* const position,
    const LONG type = 0) {
    CComVariant raw;
    CComPtr<IDispatch> anchor;
    return SUCCEEDED(Method(control, L"GetAnchorPos", {CComVariant(type)}, &raw)) &&
        SUCCEEDED(AsDispatch(raw, anchor)) &&
        SetItemLong(anchor, L"List", &position->list) &&
        SetItemLong(anchor, L"Para", &position->paragraph) &&
        SetItemLong(anchor, L"Pos", &position->character);
}

LONG ParameterLong(IDispatch* const set, const wchar_t* const name) {
    LONG value = -1;
    static_cast<void>(SetItemLong(set, name, &value));
    return value;
}

bool CellCoordinates(
    const std::wstring& address,
    LONG* const row,
    LONG* const column) {
    size_t index = 0;
    long long columnValue = 0;
    while (index < address.size() && address[index] >= L'A' && address[index] <= L'Z') {
        columnValue = columnValue * 26 + (address[index] - L'A' + 1);
        if (columnValue > (std::numeric_limits<LONG>::max)()) {
            return false;
        }
        ++index;
    }
    if (index == 0 || index == address.size()) {
        return false;
    }
    long long rowValue = 0;
    while (index < address.size() && iswdigit(address[index])) {
        rowValue = rowValue * 10 + (address[index] - L'0');
        if (rowValue > (std::numeric_limits<LONG>::max)()) {
            return false;
        }
        ++index;
    }
    if (index != address.size() || rowValue < 1) {
        return false;
    }
    *row = static_cast<LONG>(rowValue);
    *column = static_cast<LONG>(columnValue);
    return true;
}

void TableDimensions(
    const std::vector<TableCellRecord>& cells,
    LONG* const rows,
    LONG* const columns) {
    *rows = -1;
    *columns = -1;
    for (const TableCellRecord& cell : cells) {
        LONG row = 0;
        LONG column = 0;
        if (!CellCoordinates(cell.address, &row, &column)) {
            continue;
        }
        *rows = (std::max)(*rows, row + cell.rowSpan - 1);
        *columns = (std::max)(*columns, column + cell.columnSpan - 1);
    }
}

bool DefaultParameter(
    IDispatch* const hwp,
    const wchar_t* const actionName,
    const wchar_t* const parameterName,
    CComPtr<IDispatch>& parameter) {
    CComPtr<IDispatch> action;
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> set;
    CComVariant ignored;
    return DispatchProperty(hwp, L"HAction", action) &&
        DispatchProperty(hwp, L"HParameterSet", parameterSets) &&
        DispatchProperty(parameterSets, parameterName, parameter) &&
        DispatchProperty(parameter, L"HSet", set) &&
        SUCCEEDED(Method(
            action,
            L"GetDefault",
            {CComVariant(actionName), CComVariant(set)},
            &ignored));
}

bool ReadFormatting(IDispatch* const hwp, Formatting* const formatting) {
    CComPtr<IDispatch> style;
    CComPtr<IDispatch> character;
    CComPtr<IDispatch> paragraph;
    if (!DefaultParameter(hwp, L"Style", L"HStyle", style) ||
        !DefaultParameter(hwp, L"CharShape", L"HCharShape", character) ||
        !DefaultParameter(hwp, L"ParagraphShape", L"HParaShape", paragraph)) {
        return false;
    }
    static_cast<void>(LongProperty(style, L"Apply", &formatting->styleId));
    static_cast<void>(StringProperty(character, L"FaceNameHangul", &formatting->faceName));
    static_cast<void>(LongProperty(character, L"Height", &formatting->height));
    static_cast<void>(BoolProperty(character, L"Bold", &formatting->bold));
    static_cast<void>(LongProperty(character, L"TextColor", &formatting->textColor));
    static_cast<void>(LongProperty(paragraph, L"AlignType", &formatting->alignment));
    static_cast<void>(LongProperty(paragraph, L"LineSpacing", &formatting->lineSpacing));
    static_cast<void>(LongProperty(paragraph, L"LeftMargin", &formatting->leftMargin));
    static_cast<void>(LongProperty(paragraph, L"RightMargin", &formatting->rightMargin));
    static_cast<void>(LongProperty(paragraph, L"Indentation", &formatting->indentation));
    static_cast<void>(LongProperty(paragraph, L"PrevSpacing", &formatting->previousSpacing));
    static_cast<void>(LongProperty(paragraph, L"NextSpacing", &formatting->nextSpacing));
    return formatting->styleId >= 0;
}

struct DetailedControlRecord {
    std::wstring type;
    std::wstring instance;
    std::wstring userDescription;
    Position anchor;
    Position rootAnchor;
    LONG anchorPage = 0;
    LONG width = -1;
    LONG height = -1;
    bool topLevel = false;
};

struct CaptionRecord {
    bool exists = false;
    std::wstring text;
    bool automaticNumber = false;
    LONG styleId = -1;
    std::wstring styleName;
    LONG pageStart = 0;
    LONG pageEnd = 0;
};

bool SamePosition(const Position& left, const Position& right) noexcept {
    return left.list == right.list && left.paragraph == right.paragraph &&
        left.character == right.character;
}

bool CallBoolean(
    IDispatch* const object,
    const wchar_t* const method,
    const std::vector<CComVariant>& arguments,
    bool* const value) {
    CComVariant raw;
    if (FAILED(Method(object, method, arguments, &raw))) {
        return false;
    }
    if (raw.vt == VT_EMPTY) {
        *value = true;
        return true;
    }
    return SUCCEEDED(AsBool(raw, value));
}

bool RunHwpAction(IDispatch* const hwp, const wchar_t* const actionName) {
    CComPtr<IDispatch> action;
    bool result = false;
    return DispatchProperty(hwp, L"HAction", action) &&
        CallBoolean(action, L"Run", {CComVariant(actionName)}, &result) && result;
}

bool ReadCurrentStyle(
    IDispatch* const hwp,
    LONG* const styleId,
    std::wstring* const styleName) {
    CComPtr<IDispatch> style;
    CComPtr<IDispatch> styleItem;
    return DefaultParameter(hwp, L"Style", L"HStyle", style) &&
        LongProperty(style, L"Apply", styleId) && *styleId >= 0 &&
        DefaultParameter(
            hwp,
            L"StyleChangeToCurrentShape",
            L"HStyleItem",
            styleItem) &&
        StringProperty(styleItem, L"NameLocal", styleName);
}

bool ReadControlRecords(
    IDispatch* const hwp,
    IDispatch* const info,
    std::vector<DetailedControlRecord>* const records,
    std::wstring* const error) {
    records->clear();
    CComPtr<IDispatch> control;
    if (!DispatchProperty(hwp, L"HeadCtrl", control)) {
        return true;
    }
    size_t visited = 0;
    while (control != nullptr && visited < 20'000) {
        ++visited;
        DetailedControlRecord record;
        if (!ControlIdentity(control, &record.type, &record.instance) ||
            !StringProperty(control, L"UserDesc", &record.userDescription) ||
            !AnchorPosition(control, &record.anchor, 0) ||
            !AnchorPosition(control, &record.rootAnchor, 2) ||
            !SetPosition(hwp, record.anchor) ||
            !CurrentPageFromInfo(info, &record.anchorPage)) {
            *error = L"control identity, description, anchor, or page could not be read";
            return false;
        }
        record.topLevel = SamePosition(record.anchor, record.rootAnchor);
        CComPtr<IDispatch> properties;
        if (DispatchProperty(control, L"Properties", properties)) {
            record.width = ParameterLong(properties, L"Width");
            record.height = ParameterLong(properties, L"Height");
        }
        records->push_back(std::move(record));

        CComPtr<IDispatch> next;
        if (!DispatchProperty(control, L"Next", next)) {
            control.Release();
            break;
        }
        control = next;
    }
    if (control != nullptr) {
        *error = L"control linked list exceeded the inspection limit";
        return false;
    }
    return true;
}

bool ReadTableCaption(
    IDispatch* const hwp,
    const DetailedControlRecord& table,
    const std::vector<DetailedControlRecord>& controls,
    CaptionRecord* const caption,
    std::wstring* const error) {
    if (!SelectTableControl(hwp, table.instance)) {
        *error = L"table could not be selected for caption inspection";
        return false;
    }
    bool hasCaption = false;
    if (!CallBoolean(
            hwp,
            L"IsActionEnable",
            {CComVariant(L"ShapeObjDetachCaption")},
            &hasCaption)) {
        *error = L"caption availability could not be read";
        return false;
    }
    if (!hasCaption) {
        return true;
    }

    Position tablePosition;
    Position captionPosition;
    if (!GetPosition(hwp, &tablePosition) ||
        !RunHwpAction(hwp, L"ShapeObjAttachCaption") ||
        !GetPosition(hwp, &captionPosition) ||
        captionPosition.list == tablePosition.list) {
        *error = L"existing caption edit list could not be entered";
        return false;
    }
    caption->exists = true;
    bool succeeded = CurrentPage(hwp, &caption->pageStart) &&
        ReadCurrentListText(hwp, &caption->text) &&
        ReadCurrentStyle(hwp, &caption->styleId, &caption->styleName);
    if (succeeded) {
        caption->automaticNumber = std::any_of(
            controls.begin(),
            controls.end(),
            [&captionPosition](const DetailedControlRecord& control) {
                return control.type == L"atno" &&
                    control.anchor.list == captionPosition.list;
            });
        succeeded = RunHwpAction(hwp, L"MoveListEnd") &&
            CurrentPage(hwp, &caption->pageEnd);
    }
    const bool closed = RunHwpAction(hwp, L"CloseEx");
    if (!succeeded || !closed) {
        *error = L"caption text, style, page range, or close operation failed";
        return false;
    }
    if (caption->pageEnd < caption->pageStart) {
        std::swap(caption->pageStart, caption->pageEnd);
    }
    return true;
}

void WriteDetailedControl(
    std::wostringstream& output,
    const DetailedControlRecord& control,
    const LONG pageStart,
    const LONG pageEnd,
    const LONG rows,
    const LONG columns) {
    output << L"CTRL\t" << EncodeUtf8Base64(control.type) << L'\t'
           << EncodeUtf8Base64(control.instance) << L'\t'
           << EncodeUtf8Base64(control.userDescription) << L'\t'
           << control.anchor.list << L'\t' << control.anchor.paragraph << L'\t'
           << control.anchor.character << L'\t' << pageStart << L'\t' << pageEnd
           << L'\t' << (control.topLevel ? 1 : 0) << L'\t' << rows << L'\t'
           << columns << L'\t' << control.width << L'\t' << control.height << L'\n';
}

std::wstring RestoreInspectionState(
    IDispatch* const hwp,
    const Position& cursor,
    const Selection& selection,
    const bool modified,
    const std::wstring& response) {
    bool currentModified = modified;
    if (!RestoreSelection(hwp, cursor, selection) ||
        !BoolProperty(hwp, L"IsModified", &currentModified)) {
        return ErrorResponse(
            L"RESTORE_STATE",
            L"cursor or selection could not be restored after detailed inspection");
    }
    if (currentModified != modified) {
        return ErrorResponse(
            L"DOCUMENT_CHANGED",
            L"detailed inspection unexpectedly changed the document");
    }
    return response;
}

}

std::wstring Snapshot(IDispatch* const hwp) noexcept {
    try {
        if (hwp == nullptr) {
            return ErrorResponse(L"NO_HWP", L"HwpObject is unavailable");
        }
        CComPtr<IDispatch> document;
        CComPtr<IDispatch> info;
        if (!ActiveDocument(hwp, document, info)) {
            return ErrorResponse(L"NO_DOCUMENT", L"active document is unavailable");
        }
        LONG documentId = -1;
        LONG pageCount = 0;
        LONG currentPage = 0;
        std::wstring fullName;
        bool modified = false;
        Position cursor;
        Selection selection;
        Formatting formatting;
        if (!LongProperty(document, L"DocumentID", &documentId) ||
            !StringProperty(document, L"FullName", &fullName) ||
            !LongProperty(hwp, L"PageCount", &pageCount) ||
            !CurrentPageFromInfo(info, &currentPage) ||
            !BoolProperty(hwp, L"IsModified", &modified) ||
            !GetPosition(hwp, &cursor) ||
            !GetSelection(hwp, &selection)) {
            return ErrorResponse(L"SNAPSHOT_FAILED", L"live HWP state could not be read");
        }
        static_cast<void>(ReadFormatting(hwp, &formatting));
        std::wstring controlType;
        std::wstring controlInstance;
        CurrentControl(hwp, &controlType, &controlInstance);
        const std::wstring selectedText = SelectedText(hwp, selection.selected);
        const std::wstring cell = CellAddress(hwp);
        std::wostringstream output;
        output << L"HCS1\nDOC\t" << documentId << L'\t' << EncodeUtf8Base64(fullName)
               << L"\nSTATE\t" << currentPage << L'\t' << pageCount << L'\t'
               << (modified ? 1 : 0)
               << L"\nCURSOR\t" << cursor.list << L'\t' << cursor.paragraph << L'\t'
               << cursor.character
               << L"\nSELECTION\t" << (selection.selected ? 1 : 0) << L'\t'
               << selection.start.list << L'\t' << selection.start.paragraph << L'\t'
               << selection.start.character << L'\t' << selection.end.list << L'\t'
               << selection.end.paragraph << L'\t' << selection.end.character
               << L"\nTEXT\t" << EncodeUtf8Base64(selectedText)
               << L"\nCONTEXT\t" << EncodeUtf8Base64(controlType) << L'\t'
               << EncodeUtf8Base64(controlInstance) << L'\t' << EncodeUtf8Base64(cell)
               << L"\nSTYLE\t" << formatting.styleId
               << L"\nCHAR\t" << EncodeUtf8Base64(formatting.faceName) << L'\t'
               << formatting.height << L'\t' << (formatting.bold ? 1 : 0) << L'\t'
               << formatting.textColor
               << L"\nPARA\t" << formatting.alignment << L'\t' << formatting.lineSpacing
               << L'\t' << formatting.leftMargin << L'\t' << formatting.rightMargin << L'\t'
               << formatting.indentation << L'\t' << formatting.previousSpacing << L'\t'
               << formatting.nextSpacing
               << L"\nEND";
        return output.str();
    } catch (...) {
        return ErrorResponse(L"NATIVE_EXCEPTION", L"snapshot failed unexpectedly");
    }
}

void AppendPageControl(
    IDispatch* const hwp,
    IDispatch* const control,
    const std::wstring& type,
    const std::wstring& instance,
    const Position& anchor,
    const bool includeAnchorFormatting,
    const bool includeTableCells,
    std::wostringstream* const controls) {
    LONG rows = -1;
    LONG columns = -1;
    LONG width = -1;
    LONG height = -1;
    CComPtr<IDispatch> properties;
    if (DispatchProperty(control, L"Properties", properties)) {
        width = ParameterLong(properties, L"Width");
        height = ParameterLong(properties, L"Height");
    }
    hancom::formatting::ParagraphFormat anchorFormat;
    const bool anchorFormatRead = type != L"tbl" ||
        SUCCEEDED(hancom::formatting::ReadParagraphFormat(hwp, &anchorFormat));
    std::vector<TableCellRecord> cells;
    std::wstring tableError;
    const bool tableInspected = type != L"tbl" || !includeTableCells ||
        InspectTableCells(hwp, instance, &cells, &tableError);
    if (type == L"tbl" && includeTableCells && tableInspected) {
        TableDimensions(cells, &rows, &columns);
    }
    *controls << L"CTRL\t" << EncodeUtf8Base64(type) << L'\t'
              << EncodeUtf8Base64(instance) << L'\t' << anchor.list << L'\t'
              << anchor.paragraph << L'\t' << anchor.character << L'\t' << rows
              << L'\t' << columns << L'\t' << width << L'\t' << height << L'\n';
    if (type != L"tbl") {
        return;
    }
    if (includeAnchorFormatting && anchorFormatRead) {
        *controls << L"CTRL_FORMAT\t" << EncodeUtf8Base64(instance) << L'\t'
                  << anchorFormat.styleId << L'\t' << anchorFormat.alignment << L'\t'
                  << anchorFormat.lineSpacing << L'\t' << anchorFormat.leftMargin << L'\t'
                  << anchorFormat.rightMargin << L'\t' << anchorFormat.indentation << L'\t'
                  << anchorFormat.previousSpacing << L'\t' << anchorFormat.nextSpacing
                  << L'\n';
    } else if (includeAnchorFormatting) {
        *controls << L"CTRL_ERROR\t" << EncodeUtf8Base64(instance) << L'\t'
                  << EncodeUtf8Base64(L"ANCHOR_FORMAT") << L'\t'
                  << EncodeUtf8Base64(L"table anchor paragraph format is unavailable")
                  << L'\n';
    }
    if (includeTableCells && !tableInspected) {
        *controls << L"CTRL_ERROR\t" << EncodeUtf8Base64(instance) << L'\t'
                  << EncodeUtf8Base64(L"TABLE_INSPECTION") << L'\t'
                  << EncodeUtf8Base64(tableError) << L'\n';
    } else if (includeTableCells) {
        for (const TableCellRecord& cell : cells) {
            *controls << L"CELL\t" << EncodeUtf8Base64(cell.tableInstanceId)
                      << L'\t' << EncodeUtf8Base64(cell.address) << L'\t'
                      << cell.listId << L'\t' << cell.rowSpan << L'\t'
                      << cell.columnSpan << L'\t' << EncodeUtf8Base64(cell.text)
                      << L'\t' << cell.width << L'\t' << cell.height
                      << L'\n';
        }
    }
}

bool ParseRequestedPages(
    const std::wstring& payload,
    std::vector<LONG>* const pages) {
    pages->clear();
    if (payload.empty()) {
        return false;
    }
    LONG value = 0;
    bool hasDigit = false;
    for (size_t index = 0; index <= payload.size(); ++index) {
        const wchar_t character = index == payload.size() ? L',' : payload[index];
        if (character >= L'0' && character <= L'9') {
            const LONG digit = static_cast<LONG>(character - L'0');
            if (value > ((std::numeric_limits<LONG>::max)() - digit) / 10) {
                return false;
            }
            value = value * 10 + digit;
            hasDigit = true;
            continue;
        }
        if (character != L',' || !hasDigit || value < 1 || pages->size() >= 256 ||
            std::find(pages->begin(), pages->end(), value) != pages->end()) {
            return false;
        }
        pages->push_back(value);
        value = 0;
        hasDigit = false;
    }
    return !pages->empty();
}

static std::wstring InspectPagesResponse(
    IDispatch* const hwp,
    const std::wstring& requestedPages) noexcept {
    try {
        if (hwp == nullptr) {
            return ErrorResponse(L"NO_HWP", L"HwpObject is unavailable");
        }
        std::vector<LONG> pages;
        if (!ParseRequestedPages(requestedPages, &pages)) {
            return ErrorResponse(L"BAD_PAGE_LIST", L"requested page list is invalid");
        }
        CComPtr<IDispatch> document;
        CComPtr<IDispatch> info;
        LONG documentId = -1;
        LONG pageCount = 0;
        std::wstring fullName;
        if (!ActiveDocument(hwp, document, info) ||
            !LongProperty(document, L"DocumentID", &documentId) ||
            !StringProperty(document, L"FullName", &fullName) ||
            !LongProperty(hwp, L"PageCount", &pageCount) ||
            std::any_of(pages.begin(), pages.end(), [pageCount](const LONG page) {
                return page > pageCount;
            })) {
            return ErrorResponse(L"BAD_PAGE", L"requested page is outside the active document");
        }

        std::vector<std::wstring> pageTexts;
        pageTexts.reserve(pages.size());
        for (const LONG page : pages) {
            CComVariant textValue;
            std::wstring pageText;
            if (FAILED(Method(
                    hwp,
                    L"GetPageText",
                    {CComVariant(page - 1), CComVariant(static_cast<LONG>(-1))},
                    &textValue)) ||
                FAILED(AsString(textValue, &pageText))) {
                return ErrorResponse(L"PAGE_TEXT", L"requested page text could not be read");
            }
            pageTexts.push_back(std::move(pageText));
        }

        Position cursor;
        Selection selection;
        if (!GetPosition(hwp, &cursor) || !GetSelection(hwp, &selection)) {
            return ErrorResponse(L"POSITION", L"current cursor and selection could not be preserved");
        }

        std::vector<std::wostringstream> pageControls(pages.size());
        CComPtr<IDispatch> control;
        static_cast<void>(DispatchProperty(hwp, L"HeadCtrl", control));
        size_t visited = 0;
        while (control != nullptr && visited < 20'000) {
            ++visited;
            std::wstring type;
            std::wstring instance;
            Position anchor;
            if (ControlIdentity(control, &type, &instance) && AnchorPosition(control, &anchor) &&
                SetPosition(hwp, anchor)) {
                LONG page = 0;
                if (CurrentPageFromInfo(info, &page)) {
                    const auto match = std::find(pages.begin(), pages.end(), page);
                    if (match != pages.end()) {
                        const size_t pageIndex = static_cast<size_t>(match - pages.begin());
                        AppendPageControl(
                            hwp,
                            control,
                            type,
                            instance,
                            anchor,
                            true,
                            true,
                            &pageControls[pageIndex]);
                    }
                }
            }
            CComPtr<IDispatch> next;
            if (!DispatchProperty(control, L"Next", next)) {
                break;
            }
            control = next;
        }
        if (!RestoreSelection(hwp, cursor, selection)) {
            return ErrorResponse(L"RESTORE_STATE", L"cursor or selection could not be restored after page inspection");
        }

        std::wostringstream output;
        output << L"HPM1\n";
        for (size_t index = 0; index < pages.size(); ++index) {
            std::wostringstream page;
            page << L"HPI1\nDOC\t" << documentId << L'\t' << EncodeUtf8Base64(fullName)
                 << L"\nPAGE\t" << pages[index] << L'\t' << pageCount << L'\t'
                 << EncodeUtf8Base64(pageTexts[index]) << L'\n'
                 << pageControls[index].str() << L"END";
            output << L"ITEM\t" << EncodeUtf8Base64(page.str()) << L'\n';
        }
        output << L"END";
        return output.str();
    } catch (...) {
        return ErrorResponse(L"NATIVE_EXCEPTION", L"multi-page inspection failed unexpectedly");
    }
}

static std::wstring InspectPageResponse(
    IDispatch* const hwp,
    const LONG requestedPage,
    const bool includeAnchorFormatting,
    const bool includeTableCells) noexcept {
    try {
        if (hwp == nullptr) {
            return ErrorResponse(L"NO_HWP", L"HwpObject is unavailable");
        }
        CComPtr<IDispatch> document;
        CComPtr<IDispatch> info;
        LONG documentId = -1;
        LONG pageCount = 0;
        std::wstring fullName;
        if (!ActiveDocument(hwp, document, info) ||
            !LongProperty(document, L"DocumentID", &documentId) ||
            !StringProperty(document, L"FullName", &fullName) ||
            !LongProperty(hwp, L"PageCount", &pageCount) ||
            requestedPage < 1 || requestedPage > pageCount) {
            return ErrorResponse(L"BAD_PAGE", L"requested page is outside the active document");
        }
        CComVariant textValue;
        std::wstring pageText;
        if (FAILED(Method(
                hwp,
                L"GetPageText",
                {CComVariant(requestedPage - 1), CComVariant(static_cast<LONG>(-1))},
                &textValue)) ||
            FAILED(AsString(textValue, &pageText))) {
            return ErrorResponse(L"PAGE_TEXT", L"requested page text could not be read");
        }

        Position cursor;
        Selection selection;
        if (!GetPosition(hwp, &cursor) || !GetSelection(hwp, &selection)) {
            return ErrorResponse(L"POSITION", L"current cursor and selection could not be preserved");
        }

        std::wostringstream controls;
        CComPtr<IDispatch> control;
        static_cast<void>(DispatchProperty(hwp, L"HeadCtrl", control));
        size_t visited = 0;
        while (control != nullptr && visited < 20'000) {
            ++visited;
            std::wstring type;
            std::wstring instance;
            Position anchor;
            if (ControlIdentity(control, &type, &instance) && AnchorPosition(control, &anchor) &&
                SetPosition(hwp, anchor)) {
                LONG page = 0;
                if (CurrentPageFromInfo(info, &page) && page == requestedPage) {
                    AppendPageControl(
                        hwp,
                        control,
                        type,
                        instance,
                        anchor,
                        includeAnchorFormatting,
                        includeTableCells,
                        &controls);
                }
            }
            CComPtr<IDispatch> next;
            if (!DispatchProperty(control, L"Next", next)) {
                break;
            }
            control = next;
        }
        if (!RestoreSelection(hwp, cursor, selection)) {
            return ErrorResponse(L"RESTORE_STATE", L"cursor or selection could not be restored after page inspection");
        }

        std::wostringstream output;
        output << L"HPI1\nDOC\t" << documentId << L'\t' << EncodeUtf8Base64(fullName)
               << L"\nPAGE\t" << requestedPage << L'\t' << pageCount << L'\t'
               << EncodeUtf8Base64(pageText) << L'\n' << controls.str() << L"END";
        return output.str();
    } catch (...) {
        return ErrorResponse(L"NATIVE_EXCEPTION", L"page inspection failed unexpectedly");
    }
}

std::wstring InspectPage(IDispatch* const hwp, const LONG requestedPage) noexcept {
    return InspectPageResponse(hwp, requestedPage, false, true);
}

std::wstring InspectPageV3(IDispatch* const hwp, const LONG requestedPage) noexcept {
    return InspectPageResponse(hwp, requestedPage, true, true);
}

std::wstring InspectPageSummary(IDispatch* const hwp, const LONG requestedPage) noexcept {
    return InspectPageResponse(hwp, requestedPage, true, false);
}

std::wstring InspectPagesV3(
    IDispatch* const hwp,
    const std::wstring& requestedPages) noexcept {
    return InspectPagesResponse(hwp, requestedPages);
}

std::wstring InspectRoutingContext(IDispatch* const hwp, const LONG pageHint) noexcept {
    LONG page = pageHint;
    if (page == 0 && !CurrentPage(hwp, &page)) {
        return ErrorResponse(L"CURRENT_PAGE", L"current page could not be read");
    }
    return InspectPageSummary(hwp, page);
}

std::wstring InspectStructure(IDispatch* const hwp, const LONG requestedPage) noexcept {
    try {
        if (hwp == nullptr) {
            return ErrorResponse(L"NO_HWP", L"HwpObject is unavailable");
        }
        CComPtr<IDispatch> document;
        CComPtr<IDispatch> info;
        LONG documentId = -1;
        LONG pageCount = 0;
        std::wstring fullName;
        if (!ActiveDocument(hwp, document, info) ||
            !LongProperty(document, L"DocumentID", &documentId) ||
            !StringProperty(document, L"FullName", &fullName) ||
            !LongProperty(hwp, L"PageCount", &pageCount) ||
            requestedPage < 1 || requestedPage > pageCount) {
            return ErrorResponse(L"BAD_PAGE", L"requested page is outside the active document");
        }
        CComVariant textValue;
        std::wstring pageText;
        if (FAILED(Method(
                hwp,
                L"GetPageText",
                {CComVariant(requestedPage - 1), CComVariant(static_cast<LONG>(-1))},
                &textValue)) ||
            FAILED(AsString(textValue, &pageText))) {
            return ErrorResponse(L"PAGE_TEXT", L"requested page text could not be read");
        }

        Position cursor;
        Selection selection;
        bool modified = false;
        if (!GetPosition(hwp, &cursor) || !GetSelection(hwp, &selection) ||
            !BoolProperty(hwp, L"IsModified", &modified)) {
            return ErrorResponse(L"POSITION", L"current document state could not be preserved");
        }

        std::vector<DetailedControlRecord> records;
        std::wstring error;
        if (!ReadControlRecords(hwp, info, &records, &error)) {
            return RestoreInspectionState(
                hwp,
                cursor,
                selection,
                modified,
                ErrorResponse(L"CONTROL_SCAN", error));
        }

        std::vector<bool> candidates(records.size(), false);
        bool directTable = false;
        for (size_t index = 0; index < records.size(); ++index) {
            const DetailedControlRecord& control = records[index];
            if (control.topLevel && control.anchorPage == requestedPage) {
                candidates[index] = true;
                directTable = directTable || control.type == L"tbl";
            }
        }
        if (!directTable) {
            LONG precedingPage = 0;
            for (const DetailedControlRecord& control : records) {
                if (control.topLevel && control.type == L"tbl" &&
                    control.anchorPage < requestedPage) {
                    precedingPage = (std::max)(precedingPage, control.anchorPage);
                }
            }
            if (precedingPage > 0) {
                for (size_t index = 0; index < records.size(); ++index) {
                    const DetailedControlRecord& control = records[index];
                    candidates[index] = candidates[index] ||
                        (control.topLevel && control.type == L"tbl" &&
                         control.anchorPage == precedingPage);
                }
            }
        }

        std::wostringstream body;
        std::vector<Position> includedTableAnchors;
        for (size_t index = 0; index < records.size(); ++index) {
            if (!candidates[index]) {
                continue;
            }
            const DetailedControlRecord& control = records[index];
            if (control.type != L"tbl") {
                WriteDetailedControl(
                    body,
                    control,
                    control.anchorPage,
                    control.anchorPage,
                    -1,
                    -1);
                continue;
            }

            std::vector<TableCellRecord> cells;
            if (!InspectTableCells(hwp, control.instance, &cells, &error)) {
                WriteDetailedControl(
                    body,
                    control,
                    control.anchorPage,
                    control.anchorPage,
                    -1,
                    -1);
                body << L"CTRL_ERROR\t" << EncodeUtf8Base64(control.instance) << L'\t'
                     << EncodeUtf8Base64(L"TABLE_INSPECTION") << L'\t'
                     << EncodeUtf8Base64(error) << L'\n';
                continue;
            }
            LONG rows = -1;
            LONG columns = -1;
            TableDimensions(cells, &rows, &columns);
            LONG pageStart = pageCount;
            LONG pageEnd = 1;
            for (const TableCellRecord& cell : cells) {
                pageStart = (std::min)(pageStart, cell.pageStart);
                pageEnd = (std::max)(pageEnd, cell.pageEnd);
            }
            CaptionRecord caption;
            if (!ReadTableCaption(hwp, control, records, &caption, &error)) {
                return RestoreInspectionState(
                    hwp,
                    cursor,
                    selection,
                    modified,
                    ErrorResponse(L"CAPTION_INSPECTION", error));
            }
            if (caption.exists) {
                pageStart = (std::min)(pageStart, caption.pageStart);
                pageEnd = (std::max)(pageEnd, caption.pageEnd);
            }
            if (requestedPage < pageStart || requestedPage > pageEnd) {
                continue;
            }

            WriteDetailedControl(body, control, pageStart, pageEnd, rows, columns);
            for (const TableCellRecord& cell : cells) {
                body << L"CELL\t" << EncodeUtf8Base64(cell.tableInstanceId) << L'\t'
                     << EncodeUtf8Base64(cell.address) << L'\t' << cell.listId << L'\t'
                     << cell.rowSpan << L'\t' << cell.columnSpan << L'\t'
                     << cell.pageStart << L'\t' << cell.pageEnd << L'\t'
                     << EncodeUtf8Base64(cell.text) << L'\t' << cell.width
                     << L'\t' << cell.height << L'\n';
            }
            if (caption.exists) {
                body << L"CAPTION\t" << EncodeUtf8Base64(control.instance) << L'\t'
                     << EncodeUtf8Base64(caption.text) << L'\t'
                     << (caption.automaticNumber ? 1 : 0) << L'\t' << caption.styleId
                     << L'\t' << EncodeUtf8Base64(caption.styleName) << L'\t'
                     << caption.pageStart << L'\t' << caption.pageEnd << L'\n';
            }
            includedTableAnchors.push_back(control.anchor);
        }

        for (const DetailedControlRecord& control : records) {
            if (control.topLevel || !std::any_of(
                    includedTableAnchors.begin(),
                    includedTableAnchors.end(),
                    [&control](const Position& anchor) {
                        return SamePosition(anchor, control.rootAnchor);
                    })) {
                continue;
            }
            WriteDetailedControl(
                body,
                control,
                control.anchorPage,
                control.anchorPage,
                -1,
                -1);
        }

        std::wostringstream output;
        output << L"HDS1\nDOC\t" << documentId << L'\t' << EncodeUtf8Base64(fullName)
               << L"\nPAGE\t" << requestedPage << L'\t' << pageCount << L'\t'
               << EncodeUtf8Base64(pageText) << L'\n' << body.str() << L"END";
        return RestoreInspectionState(
            hwp,
            cursor,
            selection,
            modified,
            output.str());
    } catch (...) {
        return ErrorResponse(L"NATIVE_EXCEPTION", L"detailed inspection failed unexpectedly");
    }
}

}

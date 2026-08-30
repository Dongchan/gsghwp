#include "LiveInspection.h"

#include "ComState.h"
#include "DispatchInvoke.h"
#include "ParagraphFormatting.h"
#include "ProtocolEncoding.h"
#include "TableInspection.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <cwctype>
#include <limits>
#include <optional>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace hancom::inspection {
namespace {

using hancom::com_state::CanRestoreSelection;
using hancom::com_state::CaptureSelection;
using hancom::com_state::Position;
using hancom::com_state::RestoreSelection;
using hancom::com_state::SamePosition;
using hancom::com_state::Selection;
using hancom::com_state::SelectionCapturePolicy;
using hancom::com_state::kSelectionCells;
using hancom::com_state::kSelectionControl;
using hancom::com_state::kSelectionModeMask;
using hancom::com_state::kSelectionNone;
using hancom::com_state::kSelectionStrict;
using hancom::com_state::kSelectionText;
using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;
using hancom::encoding::EncodeUtf8Base64;

bool ControlIdentity(
    IDispatch* control,
    std::wstring* type,
    std::wstring* instance);
void CurrentControl(
    IDispatch* hwp,
    std::wstring* type,
    std::wstring* instance);
bool RunHwpAction(IDispatch* hwp, const wchar_t* actionName);

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
    return SUCCEEDED(hancom::com_state::CapturePosition(hwp, position));
}

bool SetPosition(IDispatch* const hwp, const Position& position) {
    const hancom::com_state::PositionResult result = hancom::com_state::ApplyPosition(
        hwp,
        position,
        hancom::com_state::EmptyPositionResult::Reject);
    return SUCCEEDED(result.invokeStatus) && SUCCEEDED(result.conversionStatus) &&
        result.positioned;
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
    return CaptureSelection(
        hwp,
        selection,
        SelectionCapturePolicy::BestEffortControl,
        nullptr);
}

bool PrepareReadSelection(
    IDispatch* const hwp,
    Position* const cursor,
    Selection* const selection) {
    if (cursor == nullptr || selection == nullptr) {
        return false;
    }
    if (!GetPosition(hwp, cursor) || !GetSelection(hwp, selection)) {
        return false;
    }
    if (CanRestoreSelection(*selection)) {
        return true;
    }
    if (!hancom::com_state::CollapseSelectionToCursor(hwp, *cursor)) {
        return false;
    }
    *selection = Selection{};
    return true;
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

std::wstring LogicalCellAddress(const long row, long column) {
    if (row < 1 || column < 1) {
        return L"";
    }
    std::wstring columnName;
    while (column > 0) {
        const long remainder = (column - 1) % 26;
        columnName.push_back(static_cast<wchar_t>(L'A' + remainder));
        column = (column - 1) / 26;
    }
    std::reverse(columnName.begin(), columnName.end());
    return columnName + std::to_wstring(row);
}

bool ExpandLogicalSelectionAddresses(
    const CellTopology& topology,
    const std::vector<std::wstring>& physical,
    std::vector<std::wstring>* const logical,
    std::wstring* const error) noexcept {
    try {
        if (logical == nullptr || physical.empty()) {
            if (error != nullptr) {
                *error = L"selected cell owner addresses are unavailable";
            }
            return false;
        }
        bool hasMergedCell = false;
        std::vector<std::pair<long, long>> coordinates;
        for (const std::wstring& address : physical) {
            const CellTopologyCell* const cell = topology.Find(address);
            if (cell == nullptr) {
                if (error != nullptr) {
                    *error = L"selected cell address is not a physical topology owner";
                }
                return false;
            }
            hasMergedCell =
                hasMergedCell || cell->rowSpan > 1 || cell->columnSpan > 1;
            for (long rowOffset = 0; rowOffset < cell->rowSpan; ++rowOffset) {
                for (long columnOffset = 0;
                     columnOffset < cell->columnSpan;
                     ++columnOffset) {
                    coordinates.emplace_back(
                        cell->row + rowOffset,
                        cell->column + columnOffset);
                }
            }
        }
        if (!hasMergedCell) {
            *logical = physical;
            return true;
        }
        std::sort(coordinates.begin(), coordinates.end());
        coordinates.erase(
            std::unique(coordinates.begin(), coordinates.end()),
            coordinates.end());
        logical->clear();
        logical->reserve(coordinates.size());
        for (const auto& [row, column] : coordinates) {
            const std::wstring address = LogicalCellAddress(row, column);
            if (address.empty()) {
                if (error != nullptr) {
                    *error = L"selected logical cell address could not be encoded";
                }
                logical->clear();
                return false;
            }
            logical->push_back(address);
        }
        return !logical->empty();
    } catch (...) {
        try {
            if (logical != nullptr) {
                logical->clear();
            }
        } catch (...) {
        }
        try {
            if (error != nullptr) {
                *error = L"selected logical cell expansion failed unexpectedly";
            }
        } catch (...) {
        }
        return false;
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

// Hancom finishes paginating a freshly opened document in the background and
// PageCount answers 0 until it does. That is "not known yet", not "no pages":
// no document has zero body pages. So a reader asks the engine to finish
// pagination once and reads again, instead of taking the 0 at face value.
// RecalcPageCount is IHwpObject's own synchronous method for exactly this and
// is what the preview path already calls; a call that does not land is a hint
// that failed, not a failure of the read. Only one call is made, because the
// method is synchronous: if the count is still below one afterwards, waiting
// longer inside a bridge call would only hold the caller's tab.
//
// Budget asymmetry, stated because it is real and undocumented elsewhere. The
// Python wait that does the same job (hwp_live_rot.settle_page_count) carries a
// 1.0s budget and will decline to *start* RecalcPageCount when less than half
// of it is left, on the grounds that the caller's tab stays switched for the
// duration. The three native callers -- this one, ActionLifecycle
// ReadPageCount and ActionExecutor MoveToPage -- carry no budget at all: they
// call synchronously, without an upper bound, and refuse if the count is still
// unknown. Nothing here can shorten the call once it starts (a synchronous
// in-process COM call is not interruptible), so the only thing a budget could
// control is whether to start it, which would require timing the preceding
// PageCount read the way the Python side does. That is a behaviour change and
// a rebuild, so it is recorded here rather than assumed.
//
// *pageCount is left at whatever was read, so a still-unconfirmed count stays
// 0 and callers must treat it as unknown -- see PageIsOutsideDocument.
bool ReadSettledPageCount(IDispatch* const hwp, LONG* const pageCount) {
    if (!LongProperty(hwp, L"PageCount", pageCount)) {
        return false;
    }
    if (*pageCount >= 1) {
        return true;
    }
    CComVariant recalculated;
    static_cast<void>(Method(hwp, L"RecalcPageCount", {}, &recalculated));
    return LongProperty(hwp, L"PageCount", pageCount);
}

// A page number is outside the document only when there is a range for it to
// be outside of. A page number that is impossible under any count is still
// refused, and so is one past a count that is known; an unconfirmed count
// simply is not a range, so it does not make every request "out of range".
//
// This does not by itself let an unconfirmed count through -- PageCountIsUnsettled
// below stops it a line later. What it does is keep the two facts apart, so a
// document that has not finished paginating stops saying "the page you asked
// for is outside the document", which was never true and sent readers looking
// for the wrong problem.
bool PageIsOutsideDocument(const LONG requestedPage, const LONG pageCount) {
    return requestedPage < 1 || (pageCount >= 1 && requestedPage > pageCount);
}

// The one thing an inspection still cannot carry past this point. Every reader
// downstream states a real total -- FastPageInspection and DocumentStructure
// both declare page_count >= 1 -- so sending 0 up would trade this message for
// a schema error further away from the cause, which is not an improvement.
//
// Reaching here is a different fact from "the page you asked for is out of
// range" and now says so instead of BAD_PAGE: Hancom could not tell how many
// pages the document has even after being asked to finish paginating. In the
// case this whole change is about -- a large document inspected the instant it
// opened -- ReadSettledPageCount has already settled the count and nothing
// reaches this.
bool PageCountIsUnsettled(const LONG pageCount) {
    return pageCount < 1;
}

std::wstring UnsettledPageCountResponse() {
    return ErrorResponse(
        L"PAGE_COUNT_UNSETTLED",
        L"Hangul has not finished paginating this document, so its page count is "
        L"still unavailable after RecalcPageCount; retry the same inspection in a "
        L"moment");
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
    const LONG requestedPage,
    const LONG pageCount,
    std::vector<DetailedControlRecord>* const records,
    std::wstring* const error) {
    records->clear();
    const bool scoped = requestedPage > 0;
    const bool backward = scoped && requestedPage > pageCount / 2;
    CComPtr<IDispatch> control;
    if (!DispatchProperty(hwp, backward ? L"LastCtrl" : L"HeadCtrl", control)) {
        return true;
    }
    size_t visited = 0;
    bool scopeComplete = false;
    bool directTable = false;
    LONG precedingTablePage = 0;
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
        if (scoped && !backward && record.topLevel &&
            record.anchorPage > requestedPage) {
            scopeComplete = true;
            break;
        }
        if (backward && record.topLevel) {
            if (record.anchorPage == requestedPage && record.type == L"tbl") {
                directTable = true;
            }
            if (record.anchorPage < requestedPage) {
                if (directTable ||
                    (precedingTablePage > 0 &&
                     record.anchorPage < precedingTablePage)) {
                    scopeComplete = true;
                    break;
                }
                if (record.type == L"tbl") {
                    precedingTablePage = record.anchorPage;
                }
            }
        }
        records->push_back(std::move(record));

        CComPtr<IDispatch> next;
        if (!DispatchProperty(control, backward ? L"Prev" : L"Next", next)) {
            control.Release();
            break;
        }
        control = next;
    }
    if (control != nullptr && !scopeComplete) {
        *error = L"control linked list exceeded the inspection limit";
        return false;
    }
    if (backward) {
        std::reverse(records->begin(), records->end());
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

void AppendPageParagraphRecords(
    IDispatch* hwp,
    IDispatch* info,
    LONG targetPage,
    std::wostringstream* output);

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

class InspectionStateGuard final {
public:
    InspectionStateGuard(
        IDispatch* const hwp,
        const Position& cursor,
        const Selection& selection,
        const bool modified) noexcept
        : hwp_(hwp),
          cursor_(cursor),
          selection_(selection),
          modified_(modified) {}

    ~InspectionStateGuard() noexcept {
        if (active_) {
            static_cast<void>(RestoreSelection(hwp_, cursor_, selection_));
        }
    }

    std::wstring Restore(const std::wstring& response) {
        const std::wstring restored = RestoreInspectionState(
            hwp_,
            cursor_,
            selection_,
            modified_,
            response);
        active_ = false;
        return restored;
    }

private:
    IDispatch* const hwp_;
    Position cursor_;
    Selection selection_;
    bool modified_;
    bool active_ = true;
};

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
        // The snapshot settles the count for the same reason the page readers
        // do, and it has to settle it the same way: hwp_inspect reads a
        // snapshot and a page in one go and refuses when the two page counts
        // disagree, so leaving one of them on the raw property would turn an
        // unfinished pagination into that disagreement.
        if (!LongProperty(document, L"DocumentID", &documentId) ||
            !StringProperty(document, L"FullName", &fullName) ||
            !ReadSettledPageCount(hwp, &pageCount) ||
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
        std::vector<std::wstring> logicalCellAddresses;
        std::wstring logicalAddressError;
        const bool logicalSelectionAttempted = !selection.cellAddresses.empty();
        if (logicalSelectionAttempted) {
            const Selection physicalSelection = selection;
            logicalAddressError.clear();
            if (!CanRestoreSelection(physicalSelection)) {
                logicalAddressError =
                    L"selected cell logical topology expansion requires a safely restorable table selection";
            } else {
                CellTopology topology;
                std::wstring topologyError;
                const bool inspected = InspectTableTopology(
                    hwp,
                    selection.controlInstance,
                    &topology,
                    &topologyError);
                const bool expanded = inspected && ExpandLogicalSelectionAddresses(
                    topology,
                    physicalSelection.cellAddresses,
                    &logicalCellAddresses,
                    &logicalAddressError);
                bool restoredModified = modified;
                if (!RestoreSelection(hwp, cursor, physicalSelection) ||
                    !BoolProperty(hwp, L"IsModified", &restoredModified)) {
                    return ErrorResponse(
                        L"RESTORE_STATE",
                        L"cursor or selection could not be restored after selected-cell topology inspection");
                }
                if (restoredModified != modified) {
                    return ErrorResponse(
                        L"DOCUMENT_CHANGED",
                        L"selected-cell topology inspection unexpectedly changed the document");
                }
                if (!expanded) {
                    const std::wstring detail = inspected
                        ? logicalAddressError
                        : topologyError;
                    logicalAddressError =
                        L"selected cell logical topology expansion failed: " + detail;
                }
            }
        }
        std::wostringstream selectedCells;
        for (size_t index = 0; index < selection.cellAddresses.size(); ++index) {
            if (index != 0) {
                selectedCells << L',';
            }
            selectedCells << selection.cellAddresses[index];
        }
        std::wostringstream logicalSelectedCells;
        for (size_t index = 0; index < logicalCellAddresses.size(); ++index) {
            if (index != 0) {
                logicalSelectedCells << L',';
            }
            logicalSelectedCells << logicalCellAddresses[index];
        }
        std::wostringstream output;
        output << L"HCS1\nDOC\t" << documentId << L'\t' << EncodeUtf8Base64(fullName)
               << L"\nSTATE\t" << currentPage << L'\t' << pageCount << L'\t'
               << (modified ? 1 : 0)
               << L"\nCURSOR\t" << cursor.list << L'\t' << cursor.paragraph << L'\t'
               << cursor.character
               << L"\nSELECTION\t" << (selection.selected ? 1 : 0) << L'\t'
               << selection.mode << L'\t' << selection.start.list << L'\t'
               << selection.start.paragraph << L'\t'
               << selection.start.character << L'\t' << selection.end.list << L'\t'
               << selection.end.paragraph << L'\t' << selection.end.character << L'\t'
               << EncodeUtf8Base64(selectedCells.str()) << L'\t'
               << EncodeUtf8Base64(selection.cellAddressError);
        if (logicalSelectionAttempted) {
            output << L'\t' << EncodeUtf8Base64(logicalSelectedCells.str()) << L'\t'
                   << EncodeUtf8Base64(logicalAddressError);
        }
        output << L"\nTEXT\t" << EncodeUtf8Base64(selectedText)
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

// One CELLFMT line per distinct appearance the sampled cells of one table
// reported, written straight after that table's CELL lines. The address field
// lists every sampled cell that carries those values, comma separated.
//
// Additive on purpose: a caller that does not know the record ignores it, and
// a bridge that predates it simply writes none, which is how the Python side
// tells "this build reports no cell appearance" apart from "this cell has no
// fill". Every number is -1 when the property could not be read.
void AppendCellFormats(
    std::wostringstream* const controls,
    const std::vector<TableCellFormat>& formats) {
    for (const TableCellFormat& format : formats) {
        std::wstring addresses;
        for (const std::wstring& address : format.addresses) {
            if (!addresses.empty()) {
                addresses += L',';
            }
            addresses += address;
        }
        *controls << L"CELLFMT\t" << EncodeUtf8Base64(format.tableInstanceId)
                  << L'\t' << EncodeUtf8Base64(addresses) << L'\t'
                  << format.fillColor << L'\t' << format.fillBrush;
        for (size_t side = 0; side < kCellBorderSideCount; ++side) {
            *controls << L'\t' << format.borderType[side] << L'\t'
                      << format.borderWidth[side] << L'\t'
                      << format.borderColor[side];
        }
        *controls << L'\t' << format.marginLeft << L'\t' << format.marginRight
                  << L'\t' << format.marginTop << L'\t' << format.marginBottom
                  << L'\t' << format.verticalAlign << L'\t' << format.alignment
                  << L'\t' << EncodeUtf8Base64(format.faceName) << L'\t'
                  << format.characterHeight << L'\t' << format.bold << L'\n';
    }
}

// Why a table reported no cell appearance. Written only when there is nothing
// to write instead: a table that answered even one sampled cell says so with
// CELLFMT lines and needs no excuse.
//
// The code is its own, CELL_FORMAT, and not one of the TABLE_* codes, because
// the cells of this table were read fine -- only their appearance was not. A
// reader that drops a control on any inspection error would otherwise throw
// away a perfectly good table over a failed appearance sample.
void AppendCellFormatError(
    std::wostringstream* const controls,
    const std::wstring& instance,
    const std::vector<TableCellFormat>& formats,
    const std::wstring& error) {
    if (!formats.empty() || error.empty()) {
        return;
    }
    *controls << L"CTRL_ERROR\t" << EncodeUtf8Base64(instance) << L'\t'
              << EncodeUtf8Base64(L"CELL_FORMAT") << L'\t'
              << EncodeUtf8Base64(error) << L'\n';
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
    std::wstring tableErrorCode = L"TABLE_INSPECTION";
    std::wstring tableError;
    const bool tableInspected = type != L"tbl" || !includeTableCells ||
        InspectTableCells(hwp, instance, &cells, &tableError, &tableErrorCode);
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
                  << EncodeUtf8Base64(tableErrorCode) << L'\t'
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
        std::wstring cellFormatError;
        const std::vector<TableCellFormat> formats =
            ReadTableCellFormats(hwp, instance, cells, &cellFormatError);
        AppendCellFormats(controls, formats);
        AppendCellFormatError(controls, instance, formats, cellFormatError);
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
            !ReadSettledPageCount(hwp, &pageCount) ||
            std::any_of(pages.begin(), pages.end(), [pageCount](const LONG page) {
                return PageIsOutsideDocument(page, pageCount);
            })) {
            return ErrorResponse(L"BAD_PAGE", L"requested page is outside the active document");
        }
        if (PageCountIsUnsettled(pageCount)) {
            return UnsettledPageCountResponse();
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
        if (!PrepareReadSelection(hwp, &cursor, &selection)) {
            return ErrorResponse(
                L"UNSUPPORTED_SELECTION",
                L"the active HWP selection cannot be moved and restored safely");
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
            !ReadSettledPageCount(hwp, &pageCount) ||
            PageIsOutsideDocument(requestedPage, pageCount)) {
            return ErrorResponse(L"BAD_PAGE", L"requested page is outside the active document");
        }
        if (PageCountIsUnsettled(pageCount)) {
            return UnsettledPageCountResponse();
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
        if (!PrepareReadSelection(hwp, &cursor, &selection)) {
            return ErrorResponse(
                L"UNSUPPORTED_SELECTION",
                L"the active HWP selection cannot be moved and restored safely");
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
            !ReadSettledPageCount(hwp, &pageCount)) {
            return ErrorResponse(L"BAD_PAGE", L"requested page is outside the active document");
        }
        LONG targetPage = requestedPage < 0 ? -requestedPage : requestedPage;
        if (targetPage == 0 && !CurrentPageFromInfo(info, &targetPage)) {
            return ErrorResponse(L"CURRENT_PAGE", L"current page could not be read");
        }
        if (PageIsOutsideDocument(targetPage, pageCount)) {
            return ErrorResponse(L"BAD_PAGE", L"requested page is outside the active document");
        }
        if (PageCountIsUnsettled(pageCount)) {
            return UnsettledPageCountResponse();
        }
        CComVariant textValue;
        std::wstring pageText;
        if (FAILED(Method(
                hwp,
                L"GetPageText",
                {CComVariant(targetPage - 1), CComVariant(static_cast<LONG>(-1))},
                &textValue)) ||
            FAILED(AsString(textValue, &pageText))) {
            return ErrorResponse(L"PAGE_TEXT", L"requested page text could not be read");
        }

        Position cursor;
        Selection selection;
        bool modified = false;
        if (!PrepareReadSelection(hwp, &cursor, &selection) ||
            !BoolProperty(hwp, L"IsModified", &modified)) {
            return ErrorResponse(
                L"UNSUPPORTED_SELECTION",
                L"the active HWP selection cannot be moved and restored safely");
        }
        InspectionStateGuard stateGuard(hwp, cursor, selection, modified);

        std::vector<DetailedControlRecord> records;
        std::wstring error;
        if (!ReadControlRecords(
                hwp,
                info,
                requestedPage > 0 ? targetPage : 0,
                pageCount,
                &records,
                &error)) {
            return stateGuard.Restore(
                ErrorResponse(L"CONTROL_SCAN", error));
        }

        std::vector<bool> candidates(records.size(), false);
        bool directTable = false;
        for (size_t index = 0; index < records.size(); ++index) {
            const DetailedControlRecord& control = records[index];
            if (control.topLevel && control.anchorPage == targetPage) {
                candidates[index] = true;
                directTable = directTable || control.type == L"tbl";
            }
        }
        if (!directTable) {
            LONG precedingPage = 0;
            for (const DetailedControlRecord& control : records) {
                if (control.topLevel && control.type == L"tbl" &&
                    control.anchorPage < targetPage) {
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
            std::wstring tableErrorCode = L"TABLE_INSPECTION";
            if (!InspectTableCells(
                    hwp,
                    control.instance,
                    &cells,
                    &error,
                    &tableErrorCode)) {
                WriteDetailedControl(
                    body,
                    control,
                    control.anchorPage,
                    control.anchorPage,
                    -1,
                    -1);
                body << L"CTRL_ERROR\t" << EncodeUtf8Base64(control.instance) << L'\t'
                     << EncodeUtf8Base64(tableErrorCode) << L'\t'
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
                return stateGuard.Restore(
                    ErrorResponse(L"CAPTION_INSPECTION", error));
            }
            if (caption.exists) {
                pageStart = (std::min)(pageStart, caption.pageStart);
                pageEnd = (std::max)(pageEnd, caption.pageEnd);
            }
            if (targetPage < pageStart || targetPage > pageEnd) {
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
            std::wstring cellFormatError;
            const std::vector<TableCellFormat> formats =
                ReadTableCellFormats(hwp, control.instance, cells, &cellFormatError);
            AppendCellFormats(&body, formats);
            AppendCellFormatError(&body, control.instance, formats, cellFormatError);
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
        AppendPageParagraphRecords(hwp, info, targetPage, &body);

        std::wostringstream output;
        output << L"HDS1\nDOC\t" << documentId << L'\t' << EncodeUtf8Base64(fullName)
               << L"\nPAGE\t" << targetPage << L'\t' << pageCount << L'\t'
               << EncodeUtf8Base64(pageText) << L'\n' << body.str() << L"END";
        return stateGuard.Restore(output.str());
    } catch (...) {
        return ErrorResponse(L"NATIVE_EXCEPTION", L"detailed inspection failed unexpectedly");
    }
}

namespace {

// InitScan range: paragraph start (0x0030) .. paragraph end (0x0003).
constexpr LONG kParagraphScanRange = 0x0033;
// Only the leading run of a paragraph is needed to tell "circle bullet",
// "(1)", "chapter N" apart, so the payload stays small on long documents.
constexpr size_t kParagraphLeadCharacters = 24;
constexpr size_t kParagraphConventionCharacters = 4096;
constexpr size_t kParagraphConventionProbeCharacters =
    kParagraphConventionCharacters + 1;
constexpr size_t kParagraphTailCharacters = 48;
constexpr size_t kPageParagraphCharacters = 16'000;
constexpr size_t kPageParagraphProbeCharacters = kPageParagraphCharacters + 1;
constexpr size_t kPageParagraphTotalCharacters = 64'000;
constexpr size_t kPageParagraphRecordLimit = 512;
constexpr LONG kParagraphScanHardLimit = 20000;
constexpr LONG kParagraphScanDefaultLimit = 4000;
constexpr size_t kParagraphScanRequestFields = 4;
constexpr size_t kParagraphLeadChunkLimit = 64;
constexpr size_t kParagraphTextChunkLimit = 4096;

// How much of each paragraph's real appearance the scan carries back. The
// request field kept its position and its old meaning for 0 and 1, so a caller
// asking a bridge that predates level 2 still gets level 1 records; the SCAN
// line echoes the level that was actually produced, which is how the caller
// tells the two apart instead of guessing from the bridge version.
constexpr LONG kParagraphShapeNone = 0;
constexpr LONG kParagraphShapeCharacter = 1;
constexpr LONG kParagraphShapeParagraph = 2;
constexpr LONG kParagraphShapeConvention = 3;

struct ParagraphStyleRequest {
    LONG list = 0;
    LONG start = 0;
    LONG limit = kParagraphScanDefaultLimit;
    LONG shapeDetail = kParagraphShapeNone;
};

// A property that may not exist on this HWP build. Absent is not zero: an
// indentation of 0 and an indentation nobody could read are different answers,
// and only the first one may be replayed onto a new paragraph.
struct OptionalLong {
    LONG value = 0;
    bool present = false;
};

struct OptionalBool {
    bool value = false;
    bool present = false;
};

struct ParagraphStyleRecord {
    LONG paragraph = 0;
    LONG styleId = -1;
    std::wstring lead;
    std::wstring faceName;
    LONG height = 0;
    bool bold = false;
    OptionalLong textColor;
    OptionalLong alignment;
    OptionalLong lineSpacing;
    OptionalLong leftMargin;
    OptionalLong rightMargin;
    OptionalLong indentation;
    OptionalLong previousSpacing;
    OptionalLong nextSpacing;
    // Best effort: whether HWP itself numbers or bullets this paragraph. When
    // the property is missing the field is absent and the caller keeps its
    // previous behaviour rather than assuming "no automatic marker".
    OptionalLong headingType;
    OptionalLong headingLevel;
    std::wstring tail;
    OptionalLong textLength;
    bool textComplete = false;
    std::wstring faceNameLatin;
    std::wstring faceNameHanja;
    std::wstring faceNameJapanese;
    std::wstring faceNameOther;
    std::wstring faceNameSymbol;
    std::wstring faceNameUser;
};

struct PageParagraphRange {
    LONG paragraph = 0;
    LONG pageStart = 0;
    LONG pageEnd = 0;
};

struct PageParagraphRecord {
    PageParagraphRange range;
    bool textAvailable = false;
    std::wstring text;
    LONG styleId = -1;
    std::wstring faceName;
    OptionalLong height;
    OptionalBool bold;
    OptionalLong textColor;
    OptionalLong alignment;
    OptionalLong lineSpacing;
    OptionalLong leftMargin;
    OptionalLong rightMargin;
    OptionalLong indentation;
    OptionalLong previousSpacing;
    OptionalLong nextSpacing;
    OptionalLong headingType;
    OptionalLong headingLevel;
};

void ReadOptionalLong(
    IDispatch* const parameter,
    const wchar_t* const name,
    OptionalLong* const target) {
    LONG value = 0;
    if (LongProperty(parameter, name, &value)) {
        target->value = value;
        target->present = true;
    }
}

void WriteOptionalLong(std::wostringstream& output, const OptionalLong& value) {
    output << L'\t';
    if (value.present) {
        output << value.value;
    }
}

void WriteOptionalBool(std::wostringstream& output, const OptionalBool& value) {
    output << L'\t';
    if (value.present) {
        output << (value.value ? 1 : 0);
    }
}

// A hoisted parameter set. DefaultParameter() rebuilds HAction/HParameterSet
// on every call, which is four extra COM calls per paragraph; the loop below
// keeps the objects and only re-runs GetDefault at each caret position.
struct ShapeProbe {
    CComPtr<IDispatch> action;
    CComPtr<IDispatch> parameter;
    CComPtr<IDispatch> set;
    std::wstring actionName;
};

bool PrepareShapeProbe(
    IDispatch* const hwp,
    const wchar_t* const actionName,
    const wchar_t* const parameterName,
    ShapeProbe* const probe) {
    CComPtr<IDispatch> parameterSets;
    if (!DispatchProperty(hwp, L"HAction", probe->action) ||
        !DispatchProperty(hwp, L"HParameterSet", parameterSets) ||
        !DispatchProperty(parameterSets, parameterName, probe->parameter) ||
        !DispatchProperty(probe->parameter, L"HSet", probe->set)) {
        return false;
    }
    probe->actionName = actionName;
    return true;
}

bool RefreshShapeProbe(const ShapeProbe& probe) {
    IDispatch* const set = probe.set;
    CComVariant ignored;
    return SUCCEEDED(Method(
        probe.action,
        L"GetDefault",
        {CComVariant(probe.actionName.c_str()), CComVariant(set)},
        &ignored));
}

bool ParseParagraphStyleRequest(
    const std::wstring& payload,
    ParagraphStyleRequest* const request) {
    if (payload.empty()) {
        return true;
    }
    std::vector<LONG> values;
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
        if (character != L',' || !hasDigit ||
            values.size() >= kParagraphScanRequestFields) {
            return false;
        }
        values.push_back(value);
        value = 0;
        hasDigit = false;
    }
    request->list = values[0];
    if (values.size() > 1) {
        request->start = values[1];
    }
    if (values.size() > 2 && values[2] > 0) {
        request->limit = values[2];
    }
    if (values.size() > 3) {
        request->shapeDetail = values[3] > kParagraphShapeConvention
            ? kParagraphShapeConvention
            : values[3];
    }
    if (request->limit > kParagraphScanHardLimit) {
        request->limit = kParagraphScanHardLimit;
    }
    return true;
}

bool ReadParagraphText(
    IDispatch* const hwp,
    const size_t characterLimit,
    const size_t chunkLimit,
    std::wstring* const text) {
    text->clear();
    bool started = false;
    if (!CallBoolean(
            hwp,
            L"InitScan",
            {CComVariant(0L), CComVariant(kParagraphScanRange), CComVariant(0L),
             CComVariant(0L), CComVariant(0L), CComVariant(0L)},
            &started) ||
        !started) {
        return false;
    }
    bool valid = true;
    bool finished = false;
    for (size_t iteration = 0; iteration < chunkLimit; ++iteration) {
        BSTR chunk = nullptr;
        CComVariant chunkArgument;
        chunkArgument.vt = VT_BSTR | VT_BYREF;
        chunkArgument.pbstrVal = &chunk;
        CComVariant rawState;
        const HRESULT status = Method(hwp, L"GetText", {chunkArgument}, &rawState);
        LONG state = 0;
        valid = SUCCEEDED(status) && SUCCEEDED(AsLong(rawState, &state));
        if (chunk != nullptr) {
            if (valid) {
                text->append(chunk, SysStringLen(chunk));
            }
            SysFreeString(chunk);
        }
        if (!valid || state >= 101) {
            valid = false;
            break;
        }
        if (state <= 1 || text->size() >= characterLimit) {
            finished = true;
            break;
        }
    }
    static_cast<void>(Method(hwp, L"ReleaseScan", {}, nullptr));
    if (!valid || !finished) {
        text->clear();
        return false;
    }
    const size_t breakIndex = text->find_first_of(L"\r\n");
    if (breakIndex != std::wstring::npos) {
        text->erase(breakIndex);
    }
    if (text->size() > characterLimit) {
        text->erase(characterLimit);
    }
    return true;
}

bool ReadParagraphLead(IDispatch* const hwp, std::wstring* const lead) {
    return ReadParagraphText(
        hwp,
        kParagraphLeadCharacters,
        kParagraphLeadChunkLimit,
        lead);
}

void ReadParagraphConventionText(
    IDispatch* const hwp,
    ParagraphStyleRecord* const record) {
    std::wstring text;
    if (!ReadParagraphText(
            hwp,
            kParagraphConventionProbeCharacters,
            kParagraphLeadChunkLimit,
            &text) ||
        text.size() > kParagraphConventionCharacters) {
        return;
    }
    record->textLength.value = static_cast<LONG>(text.size());
    record->textLength.present = true;
    record->textComplete = true;
    size_t tailStart = text.size() > kParagraphTailCharacters
        ? text.size() - kParagraphTailCharacters
        : 0;
    if (tailStart > 0 &&
        text[tailStart] >= static_cast<wchar_t>(0xDC00) &&
        text[tailStart] <= static_cast<wchar_t>(0xDFFF) &&
        text[tailStart - 1] >= static_cast<wchar_t>(0xD800) &&
        text[tailStart - 1] <= static_cast<wchar_t>(0xDBFF)) {
        --tailStart;
    }
    record->tail = text.substr(tailStart);
}

void NoteParagraphObservationFailure(
    const wchar_t* const message,
    bool* const complete,
    std::wstring* const error) {
    *complete = false;
    if (error->empty()) {
        *error = message;
    }
}

bool ReadPageParagraphRange(
    IDispatch* const hwp,
    IDispatch* const info,
    const LONG paragraph,
    PageParagraphRange* const range) {
    Position target;
    target.list = 0;
    target.paragraph = paragraph;
    target.character = 0;
    Position reached;
    if (!SetPosition(hwp, target) || !GetPosition(hwp, &reached) ||
        reached.list != target.list || reached.paragraph != target.paragraph) {
        return false;
    }
    range->paragraph = paragraph;
    if (!CurrentPageFromInfo(info, &range->pageStart) ||
        !RunHwpAction(hwp, L"MoveParaEnd") ||
        !CurrentPageFromInfo(info, &range->pageEnd)) {
        return false;
    }
    if (range->pageEnd < range->pageStart) {
        std::swap(range->pageStart, range->pageEnd);
    }
    return true;
}

void ReadPageParagraphRecord(
    IDispatch* const hwp,
    const ShapeProbe& styleProbe,
    const ShapeProbe& characterProbe,
    const ShapeProbe& paragraphProbe,
    const PageParagraphRange& range,
    PageParagraphRecord* const record,
    bool* const complete,
    std::wstring* const error) {
    record->range = range;
    Position target;
    target.list = 0;
    target.paragraph = range.paragraph;
    target.character = 0;
    if (!SetPosition(hwp, target)) {
        NoteParagraphObservationFailure(
            L"page paragraph position could not be restored",
            complete,
            error);
        return;
    }
    if (!RefreshShapeProbe(styleProbe) ||
        !LongProperty(styleProbe.parameter, L"Apply", &record->styleId)) {
        record->styleId = -1;
        NoteParagraphObservationFailure(
            L"one or more page paragraph styles could not be read",
            complete,
            error);
    }
    if (RefreshShapeProbe(characterProbe)) {
        if (!StringProperty(
                characterProbe.parameter,
                L"FaceNameHangul",
                &record->faceName)) {
            NoteParagraphObservationFailure(
                L"one or more page paragraph character shapes could not be read",
                complete,
                error);
        }
        ReadOptionalLong(characterProbe.parameter, L"Height", &record->height);
        ReadOptionalLong(
            characterProbe.parameter,
            L"TextColor",
            &record->textColor);
        bool bold = false;
        if (BoolProperty(characterProbe.parameter, L"Bold", &bold)) {
            record->bold.value = bold;
            record->bold.present = true;
        }
    } else {
        NoteParagraphObservationFailure(
            L"one or more page paragraph character shapes could not be read",
            complete,
            error);
    }
    if (RefreshShapeProbe(paragraphProbe)) {
        IDispatch* const shape = paragraphProbe.parameter;
        ReadOptionalLong(shape, L"AlignType", &record->alignment);
        ReadOptionalLong(shape, L"LineSpacing", &record->lineSpacing);
        ReadOptionalLong(shape, L"LeftMargin", &record->leftMargin);
        ReadOptionalLong(shape, L"RightMargin", &record->rightMargin);
        ReadOptionalLong(shape, L"Indentation", &record->indentation);
        ReadOptionalLong(shape, L"PrevSpacing", &record->previousSpacing);
        ReadOptionalLong(shape, L"NextSpacing", &record->nextSpacing);
        ReadOptionalLong(shape, L"HeadingType", &record->headingType);
        ReadOptionalLong(shape, L"Level", &record->headingLevel);
    } else {
        NoteParagraphObservationFailure(
            L"one or more page paragraph shapes could not be read",
            complete,
            error);
    }
    if (!SetPosition(hwp, target) ||
        !ReadParagraphText(
            hwp,
            kPageParagraphProbeCharacters,
            kParagraphTextChunkLimit,
            &record->text)) {
        record->text.clear();
        NoteParagraphObservationFailure(
            L"one or more page paragraph texts could not be read",
            complete,
            error);
    } else {
        record->textAvailable = true;
    }
}

void WritePageParagraphRecord(
    std::wostringstream& output,
    const PageParagraphRecord& record) {
    output << L"PARA\t0\t" << record.range.paragraph << L'\t'
           << record.range.pageStart << L'\t' << record.range.pageEnd << L'\t'
           << (record.textAvailable ? 1 : 0) << L'\t'
           << EncodeUtf8Base64(record.text) << L'\t' << record.styleId << L'\t'
           << EncodeUtf8Base64(record.faceName);
    WriteOptionalLong(output, record.height);
    WriteOptionalBool(output, record.bold);
    WriteOptionalLong(output, record.textColor);
    WriteOptionalLong(output, record.alignment);
    WriteOptionalLong(output, record.lineSpacing);
    WriteOptionalLong(output, record.leftMargin);
    WriteOptionalLong(output, record.rightMargin);
    WriteOptionalLong(output, record.indentation);
    WriteOptionalLong(output, record.previousSpacing);
    WriteOptionalLong(output, record.nextSpacing);
    WriteOptionalLong(output, record.headingType);
    WriteOptionalLong(output, record.headingLevel);
    output << L'\n';
}

void AppendPageParagraphRecords(
    IDispatch* const hwp,
    IDispatch* const info,
    const LONG targetPage,
    std::wostringstream* const output) {
    bool complete = true;
    std::wstring error;
    bool hasPrevious = false;
    bool reachedTargetBoundary = false;
    PageParagraphRange previous;
    std::vector<PageParagraphRange> selected;
    Position documentEnd;
    const bool documentEndKnown =
        RunHwpAction(hwp, L"MoveDocEnd") &&
        GetPosition(hwp, &documentEnd) &&
        documentEnd.list == 0 &&
        documentEnd.paragraph >= 0;
    const LONG scanLimit =
        documentEndKnown && documentEnd.paragraph < kParagraphScanHardLimit
        ? documentEnd.paragraph + 1
        : kParagraphScanHardLimit;
    for (LONG paragraph = 0; paragraph < scanLimit; ++paragraph) {
        PageParagraphRange range;
        if (!ReadPageParagraphRange(hwp, info, paragraph, &range)) {
            const std::wstring message =
                L"page paragraph range could not be read at body paragraph " +
                std::to_wstring(paragraph);
            NoteParagraphObservationFailure(
                message.c_str(),
                &complete,
                &error);
            break;
        }
        if (range.pageEnd < targetPage) {
            previous = range;
            hasPrevious = true;
            continue;
        }
        const size_t recordsNeeded = hasPrevious ? 2 : 1;
        if (selected.size() + recordsNeeded > kPageParagraphRecordLimit) {
            const std::wstring message =
                L"page paragraph record limit was reached at body paragraph " +
                std::to_wstring(paragraph);
            NoteParagraphObservationFailure(
                message.c_str(),
                &complete,
                &error);
            break;
        }
        if (hasPrevious) {
            selected.push_back(previous);
            hasPrevious = false;
        }
        selected.push_back(range);
        if (range.pageStart > targetPage) {
            reachedTargetBoundary = true;
            break;
        }
    }
    if (!reachedTargetBoundary && complete &&
        (!documentEndKnown || documentEnd.paragraph >= kParagraphScanHardLimit)) {
        NoteParagraphObservationFailure(
            L"page paragraph scan reached its hard limit at body paragraph 20000",
            &complete,
            &error);
    }
    if (selected.empty() && hasPrevious) {
        selected.push_back(previous);
    }

    ShapeProbe styleProbe;
    ShapeProbe characterProbe;
    ShapeProbe paragraphProbe;
    const bool probesReady =
        PrepareShapeProbe(hwp, L"Style", L"HStyle", &styleProbe) &&
        PrepareShapeProbe(hwp, L"CharShape", L"HCharShape", &characterProbe) &&
        PrepareShapeProbe(
            hwp,
            L"ParagraphShape",
            L"HParaShape",
            &paragraphProbe);
    if (!probesReady && !selected.empty()) {
        NoteParagraphObservationFailure(
            L"page paragraph shape parameter sets could not be prepared",
            &complete,
            &error);
    }

    std::wostringstream records;
    size_t textCharacters = 0;
    for (const PageParagraphRange& range : selected) {
        PageParagraphRecord record;
        if (probesReady) {
            ReadPageParagraphRecord(
                hwp,
                styleProbe,
                characterProbe,
                paragraphProbe,
                range,
                &record,
                &complete,
                &error);
        } else {
            record.range = range;
            Position target;
            target.list = 0;
            target.paragraph = range.paragraph;
            target.character = 0;
            record.textAvailable =
                SetPosition(hwp, target) &&
                ReadParagraphText(
                    hwp,
                    kPageParagraphProbeCharacters,
                    kParagraphTextChunkLimit,
                    &record.text);
        }
        if (record.text.size() > kPageParagraphCharacters) {
            record.text.erase(kPageParagraphCharacters);
            const std::wstring message =
                L"page paragraph character limit was reached at body paragraph " +
                std::to_wstring(range.paragraph);
            NoteParagraphObservationFailure(
                message.c_str(),
                &complete,
                &error);
        }
        const size_t remainingCharacters =
            textCharacters < kPageParagraphTotalCharacters
            ? kPageParagraphTotalCharacters - textCharacters
            : 0;
        if (record.text.size() > remainingCharacters) {
            record.text.erase(remainingCharacters);
            const std::wstring message =
                L"page paragraph text budget was reached at body paragraph " +
                std::to_wstring(range.paragraph);
            NoteParagraphObservationFailure(
                message.c_str(),
                &complete,
                &error);
        }
        textCharacters += record.text.size();
        WritePageParagraphRecord(records, record);
    }
    *output << L"PARA_SCAN\t" << (complete ? 1 : 0) << L'\t'
            << EncodeUtf8Base64(error) << L'\n' << records.str();
}

}

std::wstring InspectParagraphStyles(
    IDispatch* const hwp,
    const std::wstring& request) noexcept {
    std::optional<InspectionStateGuard> stateGuard;
    try {
        if (hwp == nullptr) {
            return ErrorResponse(L"NO_HWP", L"HwpObject is unavailable");
        }
        ParagraphStyleRequest scan;
        if (!ParseParagraphStyleRequest(request, &scan)) {
            return ErrorResponse(
                L"BAD_SCAN_REQUEST",
                L"paragraph style scan request is invalid");
        }
        CComPtr<IDispatch> document;
        CComPtr<IDispatch> info;
        LONG documentId = -1;
        std::wstring fullName;
        if (!ActiveDocument(hwp, document, info) ||
            !LongProperty(document, L"DocumentID", &documentId) ||
            !StringProperty(document, L"FullName", &fullName)) {
            return ErrorResponse(L"NO_DOCUMENT", L"active document is unavailable");
        }
        Position cursor;
        Selection selection;
        bool modified = false;
        if (!PrepareReadSelection(hwp, &cursor, &selection) ||
            !BoolProperty(hwp, L"IsModified", &modified)) {
            return ErrorResponse(
                L"UNSUPPORTED_SELECTION",
                L"the active HWP selection cannot be moved and restored safely");
        }
        stateGuard.emplace(hwp, cursor, selection, modified);
        ShapeProbe styleProbe;
        ShapeProbe characterProbe;
        ShapeProbe paragraphProbe;
        if (!PrepareShapeProbe(hwp, L"Style", L"HStyle", &styleProbe) ||
            (scan.shapeDetail >= kParagraphShapeCharacter &&
             !PrepareShapeProbe(hwp, L"CharShape", L"HCharShape", &characterProbe)) ||
            (scan.shapeDetail >= kParagraphShapeParagraph &&
             !PrepareShapeProbe(
                 hwp,
                 L"ParagraphShape",
                 L"HParaShape",
                 &paragraphProbe))) {
            return stateGuard->Restore(
                ErrorResponse(
                    L"STYLE_PARAMETER",
                    L"style parameter set could not be prepared"));
        }

        std::vector<ParagraphStyleRecord> records;
        bool complete = false;
        for (LONG offset = 0; offset < scan.limit; ++offset) {
            const LONG paragraph = scan.start + offset;
            Position target;
            target.list = scan.list;
            target.paragraph = paragraph;
            target.character = 0;
            Position reached;
            // SetPos clamps an out-of-range paragraph to the document start
            // instead of failing, so the reached position is the real end
            // marker for the list.
            if (!SetPosition(hwp, target) || !GetPosition(hwp, &reached) ||
                reached.list != scan.list || reached.paragraph != paragraph) {
                complete = true;
                break;
            }
            ParagraphStyleRecord record;
            record.paragraph = paragraph;
            if (!RefreshShapeProbe(styleProbe) ||
                !LongProperty(styleProbe.parameter, L"Apply", &record.styleId)) {
                record.styleId = -1;
            }
            if (!ReadParagraphLead(hwp, &record.lead)) {
                record.lead.clear();
            }
            if (scan.shapeDetail >= kParagraphShapeConvention) {
                ReadParagraphConventionText(hwp, &record);
            }
            if (scan.shapeDetail >= kParagraphShapeCharacter &&
                RefreshShapeProbe(characterProbe)) {
                static_cast<void>(StringProperty(
                    characterProbe.parameter,
                    L"FaceNameHangul",
                    &record.faceName));
                static_cast<void>(LongProperty(
                    characterProbe.parameter,
                    L"Height",
                    &record.height));
                static_cast<void>(BoolProperty(
                    characterProbe.parameter,
                    L"Bold",
                    &record.bold));
                if (scan.shapeDetail >= kParagraphShapeParagraph) {
                    ReadOptionalLong(
                        characterProbe.parameter,
                        L"TextColor",
                        &record.textColor);
                }
                if (scan.shapeDetail >= kParagraphShapeConvention) {
                    static_cast<void>(StringProperty(
                        characterProbe.parameter,
                        L"FaceNameLatin",
                        &record.faceNameLatin));
                    static_cast<void>(StringProperty(
                        characterProbe.parameter,
                        L"FaceNameHanja",
                        &record.faceNameHanja));
                    static_cast<void>(StringProperty(
                        characterProbe.parameter,
                        L"FaceNameJapanese",
                        &record.faceNameJapanese));
                    static_cast<void>(StringProperty(
                        characterProbe.parameter,
                        L"FaceNameOther",
                        &record.faceNameOther));
                    static_cast<void>(StringProperty(
                        characterProbe.parameter,
                        L"FaceNameSymbol",
                        &record.faceNameSymbol));
                    static_cast<void>(StringProperty(
                        characterProbe.parameter,
                        L"FaceNameUser",
                        &record.faceNameUser));
                }
            }
            if (scan.shapeDetail >= kParagraphShapeParagraph &&
                RefreshShapeProbe(paragraphProbe)) {
                // The same properties ReadFormatting() already reads at the
                // caret. Reading them per paragraph is what lets a new
                // paragraph reproduce this document's own indentation instead
                // of only inheriting the style default.
                IDispatch* const shape = paragraphProbe.parameter;
                ReadOptionalLong(shape, L"AlignType", &record.alignment);
                ReadOptionalLong(shape, L"LineSpacing", &record.lineSpacing);
                ReadOptionalLong(shape, L"LeftMargin", &record.leftMargin);
                ReadOptionalLong(shape, L"RightMargin", &record.rightMargin);
                ReadOptionalLong(shape, L"Indentation", &record.indentation);
                ReadOptionalLong(shape, L"PrevSpacing", &record.previousSpacing);
                ReadOptionalLong(shape, L"NextSpacing", &record.nextSpacing);
                ReadOptionalLong(shape, L"HeadingType", &record.headingType);
                ReadOptionalLong(shape, L"Level", &record.headingLevel);
            }
            records.push_back(std::move(record));
        }

        std::wostringstream output;
        output << L"HPS1\nDOC\t" << documentId << L'\t' << EncodeUtf8Base64(fullName)
               << L"\nSCAN\t" << scan.list << L'\t' << scan.start << L'\t'
               << static_cast<LONG>(records.size()) << L'\t' << (complete ? 1 : 0)
               << L'\t' << scan.shapeDetail << L'\n';
        for (const ParagraphStyleRecord& record : records) {
            output << L"PSTYLE\t" << record.paragraph << L'\t' << record.styleId
                   << L'\t' << EncodeUtf8Base64(record.lead);
            if (scan.shapeDetail >= kParagraphShapeCharacter) {
                output << L'\t' << EncodeUtf8Base64(record.faceName) << L'\t'
                       << record.height << L'\t' << (record.bold ? 1 : 0);
            }
            if (scan.shapeDetail >= kParagraphShapeParagraph) {
                WriteOptionalLong(output, record.textColor);
                WriteOptionalLong(output, record.alignment);
                WriteOptionalLong(output, record.lineSpacing);
                WriteOptionalLong(output, record.leftMargin);
                WriteOptionalLong(output, record.rightMargin);
                WriteOptionalLong(output, record.indentation);
                WriteOptionalLong(output, record.previousSpacing);
                WriteOptionalLong(output, record.nextSpacing);
                WriteOptionalLong(output, record.headingType);
                WriteOptionalLong(output, record.headingLevel);
            }
            if (scan.shapeDetail >= kParagraphShapeConvention) {
                output << L'\t' << EncodeUtf8Base64(record.tail);
                WriteOptionalLong(output, record.textLength);
                output << L'\t' << (record.textComplete ? 1 : 0)
                       << L'\t' << EncodeUtf8Base64(record.faceNameLatin)
                       << L'\t' << EncodeUtf8Base64(record.faceNameHanja)
                       << L'\t' << EncodeUtf8Base64(record.faceNameJapanese)
                       << L'\t' << EncodeUtf8Base64(record.faceNameOther)
                       << L'\t' << EncodeUtf8Base64(record.faceNameSymbol)
                       << L'\t' << EncodeUtf8Base64(record.faceNameUser);
            }
            output << L'\n';
        }
        output << L"END";
        return stateGuard->Restore(output.str());
    } catch (...) {
        if (stateGuard.has_value()) {
            return stateGuard->Restore(
                ErrorResponse(
                    L"NATIVE_EXCEPTION",
                    L"paragraph style scan failed unexpectedly"));
        }
        return ErrorResponse(
            L"NATIVE_EXCEPTION",
            L"paragraph style scan failed unexpectedly");
    }
}

}

#include "TableInspection.h"

#include "ComState.h"
#include "DispatchInvoke.h"
#include "OfficialApiVirtualMethod.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <cwchar>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace hancom::inspection {
namespace {
thread_local TableCallCounters gTableCallCounters{};
thread_local std::vector<TableRangeProbeTuple> gTableRangeProbeTuples{};
thread_local const TableDispatchTypeContext* gTableDispatchTypes = nullptr;
constexpr GUID kRootVirtualInterfaceId{
    0x5E6A8276, 0xCF1C, 0x42B8,
    {0xBC, 0xED, 0x31, 0x95, 0x48, 0xB0, 0x2A, 0xF6}};
constexpr size_t kHParameterSetSlot = 73U;
constexpr size_t kHActionSlot = 74U;
constexpr size_t kUnresolvedVirtualSlot =
    (std::numeric_limits<size_t>::max)();
thread_local size_t gHParameterSetSlot = kUnresolvedVirtualSlot;
thread_local size_t gHActionSlot = kUnresolvedVirtualSlot;
constexpr GUID kBorderFillVirtualInterfaceId{
    0xC81C513C, 0x94D5, 0x4589,
    {0x8B, 0x92, 0xBF, 0xF9, 0xFD, 0x5D, 0xA9, 0x46}};
constexpr GUID kCellBorderFillVirtualInterfaceId{
    0xC54797D7, 0xB2FF, 0x44A0,
    {0xBE, 0xC2, 0x2D, 0x20, 0xF2, 0x00, 0x89, 0x8C}};
constexpr size_t kBorderColorLeftSlot = 26U;
constexpr size_t kBorderColorRightSlot = 28U;
constexpr size_t kBorderColorTopSlot = 30U;
constexpr size_t kBorderColorBottomSlot = 32U;
thread_local size_t gBorderColorLeftSlot = kUnresolvedVirtualSlot;
thread_local size_t gBorderColorRightSlot = kUnresolvedVirtualSlot;
thread_local size_t gBorderColorTopSlot = kUnresolvedVirtualSlot;
thread_local size_t gBorderColorBottomSlot = kUnresolvedVirtualSlot;

class TableDispatchTypeScope final {
public:
    explicit TableDispatchTypeScope(
        const TableDispatchTypeContext* const current) noexcept
        : previous_(gTableDispatchTypes) {
        gTableDispatchTypes = current;
    }
    ~TableDispatchTypeScope() { gTableDispatchTypes = previous_; }
    TableDispatchTypeScope(const TableDispatchTypeScope&) = delete;
    TableDispatchTypeScope& operator=(const TableDispatchTypeScope&) = delete;
private:
    const TableDispatchTypeContext* previous_ = nullptr;
};

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

HRESULT TypedPropertyGet(
    IDispatch* const object,
    const dispatch::TypeIdentityToken* const type,
    const wchar_t* const name,
    CComVariant* const value) {
    return type == nullptr
        ? PropertyGet(object, name, value)
        : dispatch::PropertyGetQualified(object, *type, name, value);
}

HRESULT TypedMethod(
    IDispatch* const object,
    const dispatch::TypeIdentityToken* const type,
    const wchar_t* const name,
    const std::vector<CComVariant>& arguments,
    CComVariant* const value) {
    return type == nullptr
        ? Method(object, name, arguments, value)
        : dispatch::MethodQualified(object, *type, name, arguments, value);
}

const dispatch::TypeIdentityToken* RootType() noexcept {
    return gTableDispatchTypes == nullptr ? nullptr : &gTableDispatchTypes->root;
}

const dispatch::TypeIdentityToken* ControlTypeToken() noexcept {
    return gTableDispatchTypes == nullptr ? nullptr : &gTableDispatchTypes->control;
}

const dispatch::TypeIdentityToken* TableType() noexcept {
    return gTableDispatchTypes == nullptr ? nullptr : &gTableDispatchTypes->table;
}

const dispatch::TypeIdentityToken* CellType() noexcept {
    return gTableDispatchTypes == nullptr ? nullptr : &gTableDispatchTypes->cell;
}

const dispatch::TypeIdentityToken* ParameterSetType() noexcept {
    return gTableDispatchTypes == nullptr
        ? nullptr : &gTableDispatchTypes->parameterSet;
}

const dispatch::TypeIdentityToken* ActionType() noexcept {
    return gTableDispatchTypes == nullptr
        ? nullptr : &gTableDispatchTypes->action;
}

const dispatch::TypeIdentityToken* DocumentsType() noexcept {
    return gTableDispatchTypes == nullptr
        ? nullptr : &gTableDispatchTypes->documents;
}

const dispatch::TypeIdentityToken* DocumentType() noexcept {
    return gTableDispatchTypes == nullptr
        ? nullptr : &gTableDispatchTypes->document;
}

const dispatch::TypeIdentityToken* DocumentInfoType() noexcept {
    return gTableDispatchTypes == nullptr
        ? nullptr : &gTableDispatchTypes->documentInfo;
}

bool Fail(std::wstring* const error, const std::wstring& message) {
    if (error != nullptr) {
        *error = message;
    }
    return false;
}

bool FailWithCode(
    std::wstring* const error,
    std::wstring* const errorCode,
    const wchar_t* const code,
    const wchar_t* const message) {
    if (errorCode != nullptr) {
        *errorCode = code;
    }
    return Fail(error, message);
}

bool DispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value,
    const dispatch::TypeIdentityToken* const type = nullptr) {
#if defined(_M_IX86)
    if (object != nullptr && name != nullptr && type != nullptr &&
        gTableDispatchTypes != nullptr &&
        type == &gTableDispatchTypes->root &&
        gTableDispatchTypes->systemKind == SYS_WIN32 &&
        InlineIsEqualGUID(type->guid, kRootVirtualInterfaceId) &&
        type->kind == TKIND_DISPATCH &&
        type->flags ==
            (TYPEFLAG_FDUAL | TYPEFLAG_FNONEXTENSIBLE |
             TYPEFLAG_FDISPATCHABLE)) {
        size_t* cachedSlot = nullptr;
        size_t expectedSlot = 0;
        if (std::wcscmp(name, L"HAction") == 0) {
            cachedSlot = &gHActionSlot;
            expectedSlot = kHActionSlot;
        } else if (std::wcscmp(name, L"HParameterSet") == 0) {
            cachedSlot = &gHParameterSetSlot;
            expectedSlot = kHParameterSetSlot;
        }
        if (cachedSlot != nullptr) {
            if (*cachedSlot == kUnresolvedVirtualSlot) {
                CComPtr<IUnknown> interfaceObject;
                size_t resolvedSlot = 0;
                if (SUCCEEDED(
                        official_api::ResolveVirtualPropertyGet(
                            object, name, VT_DISPATCH, interfaceObject,
                            &resolvedSlot)) &&
                    resolvedSlot == expectedSlot) {
                    *cachedSlot = resolvedSlot;
                }
            }
            if (*cachedSlot != kUnresolvedVirtualSlot) {
                CComPtr<IDispatch> directValue;
                if (SUCCEEDED(
                        official_api::InvokeResolvedVirtualPropertyGet(
                            object, kRootVirtualInterfaceId, *cachedSlot,
                            directValue))) {
                    value = directValue;
                    ++gTableCallCounters.directVirtualPropertyGets;
                    return true;
                }
            }
        }
    }
#endif
    CComVariant raw;
    return SUCCEEDED(TypedPropertyGet(object, type, name, &raw)) &&
        SUCCEEDED(AsDispatch(raw, value));
}

bool ItemLong(
    IDispatch* const set,
    const wchar_t* const name,
    LONG* const value) {
    CComVariant raw;
    return SUCCEEDED(Method(set, L"Item", {CComVariant(name)}, &raw)) &&
        SUCCEEDED(AsLong(raw, value));
}

bool ItemDispatch(
    IDispatch* const set,
    const wchar_t* const name,
    CComPtr<IDispatch>& value) {
    CComVariant raw;
    return SUCCEEDED(Method(set, L"Item", {CComVariant(name)}, &raw)) &&
        SUCCEEDED(AsDispatch(raw, value));
}

bool StringProperty(
    IDispatch* const object,
    const wchar_t* const name,
    std::wstring* const value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) &&
        SUCCEEDED(AsString(raw, value));
}

bool XmlAttributeLong(
    const std::wstring& xml,
    const size_t tagStart,
    const size_t tagEnd,
    const wchar_t* const name,
    LONG* const value) {
    const std::wstring marker = std::wstring(name) + L"=\"";
    size_t position = xml.find(marker, tagStart);
    if (position == std::wstring::npos || position >= tagEnd) {
        return false;
    }
    position += marker.size();
    unsigned long long parsed = 0;
    const size_t digitStart = position;
    while (position < tagEnd && xml[position] >= L'0' && xml[position] <= L'9') {
        parsed = parsed * 10 + static_cast<unsigned long long>(xml[position] - L'0');
        if (parsed > static_cast<unsigned long long>((std::numeric_limits<LONG>::max)())) {
            return false;
        }
        ++position;
    }
    if (position == digitStart || position >= xml.size() || xml[position] != L'\"') {
        return false;
    }
    *value = static_cast<LONG>(parsed);
    return true;
}

bool TableFormulaIsSafe(IDispatch* const hwp) {
    CComVariant raw;
    std::wstring xml;
    if (FAILED(Method(
            hwp,
            L"GetTextFile",
            {CComVariant(L"HWPML2X"), CComVariant(L"saveblock:true")},
            &raw)) ||
        FAILED(AsString(raw, &xml))) {
        return false;
    }
    const size_t table = xml.find(L"<TABLE");
    const size_t end = table == std::wstring::npos
        ? std::wstring::npos
        : xml.find(L'>', table + 6);
    LONG rows = 0;
    LONG columns = 0;
    constexpr unsigned long long kMaximumSafeFormulaCells = 81;
    return table != std::wstring::npos && end != std::wstring::npos &&
        XmlAttributeLong(xml, table, end, L"RowCount", &rows) &&
        XmlAttributeLong(xml, table, end, L"ColCount", &columns) &&
        rows > 0 && columns > 0 &&
        static_cast<unsigned long long>(rows) *
            static_cast<unsigned long long>(columns) <= kMaximumSafeFormulaCells;
}

bool ReadCurrentCellSize(
    IDispatch* const hwp,
    LONG* const width,
    LONG* const height) {
    CComPtr<IDispatch> table;
    CComPtr<IDispatch> cell;
    CComVariant raw;
    return DispatchProperty(hwp, L"CellShape", table, RootType()) &&
        SUCCEEDED(TypedMethod(
            table, CellType(), L"Item", {CComVariant(L"Cell")}, &raw)) &&
        SUCCEEDED(AsDispatch(raw, cell)) &&
        SUCCEEDED(TypedMethod(
            cell, CellType(), L"Item", {CComVariant(L"Width")}, &raw)) &&
        SUCCEEDED(AsLong(raw, width)) &&
        SUCCEEDED(TypedMethod(
            cell, CellType(), L"Item", {CComVariant(L"Height")}, &raw)) &&
        SUCCEEDED(AsLong(raw, height));
}

bool DocumentInfo(IDispatch* const hwp, CComPtr<IDispatch>& info) {
    CComPtr<IDispatch> documents;
    CComPtr<IDispatch> document;
    return DispatchProperty(
            hwp, L"XHwpDocuments", documents, RootType()) &&
        DispatchProperty(
            documents, L"Active_XHwpDocument", document, DocumentsType()) &&
        DispatchProperty(
            document, L"XHwpDocumentInfo", info, DocumentType());
}

bool CurrentPage(IDispatch* const info, LONG* const page) {
    CComVariant raw;
    LONG zeroBased = 0;
    if (info == nullptr || FAILED(TypedPropertyGet(
            info, DocumentInfoType(), L"CurrentPage", &raw)) ||
        FAILED(AsLong(raw, &zeroBased))) {
        return false;
    }
    *page = zeroBased + 1;
    return *page >= 1;
}

bool CallBoolean(
    IDispatch* const object,
    const wchar_t* const name,
    const std::vector<CComVariant>& arguments) {
    CComVariant raw;
    if (FAILED(TypedMethod(object, RootType(), name, arguments, &raw))) {
        return false;
    }
    if (raw.vt == VT_EMPTY) {
        return true;
    }
    bool value = false;
    return SUCCEEDED(AsBool(raw, &value)) && value;
}

bool InvokeAction(
    IDispatch* const action,
    const wchar_t* const name,
    bool* const returned) {
    CComVariant raw;
    if (returned == nullptr ||
        FAILED(TypedMethod(
            action, ActionType(), L"Run", {CComVariant(name)}, &raw))) {
        return false;
    }
    if (raw.vt == VT_EMPTY) {
        *returned = true;
        return true;
    }
    return SUCCEEDED(AsBool(raw, returned));
}

bool RunAction(IDispatch* const action, const wchar_t* const name) {
    bool returned = false;
    return InvokeAction(action, name, &returned) && returned;
}

bool ReadTableDispatchDimensions(
    IDispatch* const table,
    LONG* const rows,
    LONG* const columns) {
    CComPtr<IDispatch> properties;
    CComVariant rawRows;
    CComVariant rawColumns;
    return table != nullptr && rows != nullptr && columns != nullptr &&
        DispatchProperty(
            table, L"Properties", properties, ControlTypeToken()) &&
        SUCCEEDED(TypedPropertyGet(
            properties, TableType(), L"RowCount", &rawRows)) &&
        SUCCEEDED(TypedPropertyGet(
            properties, TableType(), L"ColCount", &rawColumns)) &&
        SUCCEEDED(AsLong(rawRows, rows)) &&
        SUCCEEDED(AsLong(rawColumns, columns)) &&
        *rows > 0 && *columns > 0;
}

bool ReadCaretTableDimensions(
    IDispatch* const hwp, LONG* const rows, LONG* const columns) {
    CComVariant rawRows;
    CComVariant rawColumns;
    return rows != nullptr && columns != nullptr &&
        SUCCEEDED(TypedMethod(
            hwp, RootType(), L"GetRowColCount",
            {CComVariant(1L)}, &rawRows)) &&
        SUCCEEDED(TypedMethod(
            hwp, RootType(), L"GetRowColCount",
            {CComVariant(0L)}, &rawColumns)) &&
        SUCCEEDED(AsLong(rawRows, rows)) &&
        SUCCEEDED(AsLong(rawColumns, columns));
}

bool SetPosition(IDispatch* const hwp, const LONG listId) {
    if (gTableDispatchTypes == nullptr) {
        const hancom::com_state::PositionResult result =
            hancom::com_state::ApplyPosition(
                hwp,
                hancom::com_state::Position{listId, 0, 0},
                hancom::com_state::EmptyPositionResult::TreatAsSuccess);
        return SUCCEEDED(result.invokeStatus) &&
            SUCCEEDED(result.conversionStatus) && result.positioned;
    }
    CComVariant returned;
    const HRESULT status = TypedMethod(
        hwp, RootType(), L"SetPos",
        {CComVariant(listId), CComVariant(0L), CComVariant(0L)}, &returned);
    if (FAILED(status)) return false;
    if (returned.vt == VT_EMPTY) return true;
    bool positioned = false;
    return SUCCEEDED(AsBool(returned, &positioned)) && positioned;
}

bool GetPositionList(IDispatch* const hwp, LONG* const listId) {
    if (gTableDispatchTypes == nullptr) {
        hancom::com_state::Position position;
        const HRESULT status =
            hancom::com_state::CapturePosition(hwp, &position);
        if (FAILED(status)) return false;
        *listId = position.list;
        return true;
    }
    LONG paragraph = 0;
    LONG character = 0;
    CComVariant list;
    list.vt = VT_I4 | VT_BYREF;
    list.plVal = listId;
    CComVariant para;
    para.vt = VT_I4 | VT_BYREF;
    para.plVal = &paragraph;
    CComVariant pos;
    pos.vt = VT_I4 | VT_BYREF;
    pos.plVal = &character;
    return SUCCEEDED(TypedMethod(
        hwp, RootType(), L"GetPos", {list, para, pos}, nullptr));
}

bool ControlInstanceId(IDispatch* const control, std::wstring* const id) {
    CComVariant raw;
    return control != nullptr &&
        SUCCEEDED(TypedMethod(
            control, ControlTypeToken(), L"GetCtrlInstID", {}, &raw)) &&
        SUCCEEDED(AsString(raw, id));
}

bool ControlType(IDispatch* const control, std::wstring* const type) {
    CComVariant raw;
    return control != nullptr &&
        SUCCEEDED(TypedPropertyGet(
            control, ControlTypeToken(), L"CtrlID", &raw)) &&
        SUCCEEDED(AsString(raw, type));
}

bool SelectedTableMatches(IDispatch* const hwp, const std::wstring& tableInstanceId) {
    CComPtr<IDispatch> selected;
    std::wstring type;
    std::wstring instance;
    return DispatchProperty(
            hwp, L"CurSelectedCtrl", selected, RootType()) &&
        ControlType(selected, &type) && type == L"tbl" &&
        ControlInstanceId(selected, &instance) && instance == tableInstanceId;
}

bool SelectTableFront(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& tableInstanceId) {
    bool returned = false;
    if (!InvokeAction(action, L"SelectCtrlFront", &returned)) {
        return false;
    }
    static_cast<void>(returned);
    return SelectedTableMatches(hwp, tableInstanceId);
}

bool FindControl(
    IDispatch* const hwp,
    const std::wstring& instanceId,
    CComPtr<IDispatch>& match) {
    CComPtr<IDispatch> control;
    if (!DispatchProperty(hwp, L"HeadCtrl", control, RootType())) {
        return false;
    }
    size_t visited = 0;
    while (control != nullptr && visited < 20'000) {
        ++visited;
        std::wstring currentId;
        if (ControlInstanceId(control, &currentId) && currentId == instanceId) {
            match = control;
            return true;
        }
        CComPtr<IDispatch> next;
        if (!DispatchProperty(
                control, L"Next", next, ControlTypeToken())) {
            break;
        }
        control = next;
    }
    return false;
}

bool MoveToControl(IDispatch* const hwp, IDispatch* const control) {
    CComVariant rawAnchor;
    CComPtr<IDispatch> anchor;
    if (FAILED(TypedMethod(
            control, ControlTypeToken(), L"GetAnchorPos",
            {CComVariant(0L)}, &rawAnchor)) ||
        FAILED(AsDispatch(rawAnchor, anchor))) {
        return false;
    }
    return CallBoolean(hwp, L"SetPosBySet", {CComVariant(anchor.p)});
}

bool SelectExactTable(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& tableInstanceId) {
    CComVariant ignored;
    static_cast<void>(Method(
        hwp,
        L"SelectCtrl",
        {CComVariant(tableInstanceId.c_str()), CComVariant(1L)},
        &ignored));
    if (SelectedTableMatches(hwp, tableInstanceId)) {
        return true;
    }
    CComPtr<IDispatch> control;
    return FindControl(hwp, tableInstanceId, control) &&
        MoveToControl(hwp, control) &&
        SelectTableFront(hwp, action, tableInstanceId);
}

bool ParentMatches(IDispatch* const hwp, const std::wstring& tableInstanceId) {
    CComPtr<IDispatch> parent;
    std::wstring parentId;
    return DispatchProperty(
            hwp, L"ParentCtrl", parent, RootType()) &&
        ControlInstanceId(parent, &parentId) && parentId == tableInstanceId;
}

bool TableContextMatches(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId) {
    LONG currentList = 0;
    return GetPositionList(hwp, &currentList) &&
        currentList >= 0 && ParentMatches(hwp, tableInstanceId);
}

bool RunTableNavigationAction(
    IDispatch* const hwp,
    IDispatch* const action,
    const wchar_t* const name,
    const std::wstring& tableInstanceId) {
    bool returned = false;
    if (!InvokeAction(action, name, &returned)) {
        return false;
    }
    static_cast<void>(returned);
    return TableContextMatches(hwp, tableInstanceId);
}

bool EnterSelectedTable(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& tableInstanceId) {
    bool returned = false;
    if (!InvokeAction(action, L"ShapeObjTextBoxEdit", &returned)) {
        return false;
    }
    static_cast<void>(returned);
    return TableContextMatches(hwp, tableInstanceId);
}

LONG ExtendTableListEnd(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    const LONG navigationEnd) {
    LONG extendedEnd = navigationEnd;
    for (long long candidate = static_cast<long long>(navigationEnd) + 1;
         candidate <= (std::numeric_limits<LONG>::max)() &&
         candidate - navigationEnd <= 200'000;
         ++candidate) {
        const LONG listId = static_cast<LONG>(candidate);
        if (!SetPosition(hwp, listId) || !ParentMatches(hwp, tableInstanceId)) {
            break;
        }
        extendedEnd = listId;
    }
    return extendedEnd;
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
    CComVariant arguments[8];
    arguments[0].vt = VT_I4 | VT_BYREF;
    arguments[0].plVal = &sectionCount;
    arguments[1].vt = VT_I4 | VT_BYREF;
    arguments[1].plVal = &sectionNumber;
    arguments[2].vt = VT_I4 | VT_BYREF;
    arguments[2].plVal = &pageNumber;
    arguments[3].vt = VT_I4 | VT_BYREF;
    arguments[3].plVal = &column;
    arguments[4].vt = VT_I4 | VT_BYREF;
    arguments[4].plVal = &line;
    arguments[5].vt = VT_I4 | VT_BYREF;
    arguments[5].plVal = &position;
    arguments[6].vt = VT_I2 | VT_BYREF;
    arguments[6].piVal = &over;
    arguments[7].vt = VT_BSTR | VT_BYREF;
    arguments[7].pbstrVal = &controlName;
    const HRESULT status = TypedMethod(
        hwp,
        RootType(),
        L"KeyIndicator",
        {arguments[0], arguments[1], arguments[2], arguments[3],
         arguments[4], arguments[5], arguments[6], arguments[7]},
        nullptr);
    const std::wstring indicator = controlName == nullptr
        ? std::wstring()
        : std::wstring(controlName, SysStringLen(controlName));
    if (controlName != nullptr) {
        SysFreeString(controlName);
    }
    if (FAILED(status)) {
        return L"";
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

bool CellCoordinates(
    const std::wstring& address,
    LONG* const row,
    LONG* const column) {
    return ParseCellAddress(address, row, column);
}

enum class DirectCellRangeStatus : std::uint8_t {
    Value = 0,
    MoveToCellFailed,
    NotExposed,
    InvokeFailed,
    ResultInvalid,
    MemberReadFailed,
    BoundsInvalid,
    CoordinateBaseMismatch,
    OwnerMismatch,
    QualificationFailed,
};

DirectCellRangeStatus ReadDirectCellRange(
    IDispatch* const hwp, const std::wstring& tableInstanceId,
    const std::wstring& expectedAddress, const LONG expectedListId,
    const LONG expectedRow, const LONG expectedColumn,
    const LONG moveBase, const LONG boundsBase, const bool selectCell,
    LONG* const rowSpan, LONG* const columnSpan) {
    // MoveToCell arguments and GetCellRangeIndex bounds are independent
    // Automation surfaces. Their bases are qualified separately from a
    // non-A1 owner and then held table-scoped for every physical owner.
    const LONG rowArgument = expectedRow - 1 + moveBase;
    const LONG columnArgument = expectedColumn - 1 + moveBase;
    if (selectCell) {
        CComVariant primed;
        bool primedPosition = false;
        if (FAILED(Method(
                hwp, L"MoveToCell",
                {CComVariant(rowArgument), CComVariant(columnArgument),
                 CComVariant(0L)}, &primed)) ||
            FAILED(AsBool(primed, &primedPosition)) || !primedPosition) {
            return DirectCellRangeStatus::MoveToCellFailed;
        }
    }
    CComVariant moved;
    const HRESULT moveStatus = Method(
        hwp,
        L"MoveToCell",
        {CComVariant(rowArgument), CComVariant(columnArgument),
         CComVariant(selectCell ? 1L : 0L)},
        &moved);
    if (FAILED(moveStatus)) {
        if (gTableRangeProbeTuples.size() < 24) {
            TableRangeProbeTuple tuple{};
            tuple.expectedAddress = expectedAddress;
            tuple.expectedRow = expectedRow;
            tuple.expectedColumn = expectedColumn;
            tuple.moveBase = moveBase;
            tuple.boundsBase = boundsBase;
            tuple.selectCell = selectCell;
            tuple.status = static_cast<long>(
                DirectCellRangeStatus::MoveToCellFailed);
            tuple.moveResultVt = moved.vt;
            tuple.rangeHresult = moveStatus;
            gTableRangeProbeTuples.push_back(std::move(tuple));
        }
        return DirectCellRangeStatus::MoveToCellFailed;
    }
    bool positioned = false;
    if (FAILED(AsBool(moved, &positioned)) || !positioned) {
        if (gTableRangeProbeTuples.size() < 24) {
            TableRangeProbeTuple tuple{};
            tuple.expectedAddress = expectedAddress;
            tuple.expectedRow = expectedRow;
            tuple.expectedColumn = expectedColumn;
            tuple.moveBase = moveBase;
            tuple.boundsBase = boundsBase;
            tuple.selectCell = selectCell;
            tuple.status = static_cast<long>(
                DirectCellRangeStatus::MoveToCellFailed);
            tuple.moveResultVt = moved.vt;
            gTableRangeProbeTuples.push_back(std::move(tuple));
        }
        return DirectCellRangeStatus::MoveToCellFailed;
    }
    const std::wstring postMoveAddress = CellAddress(hwp);
    LONG postMoveList = -1;
    static_cast<void>(GetPositionList(hwp, &postMoveList));
    if (!selectCell &&
        (postMoveAddress != expectedAddress || postMoveList != expectedListId ||
         !ParentMatches(hwp, tableInstanceId))) {
        if (gTableRangeProbeTuples.size() < 24) {
            TableRangeProbeTuple tuple{};
            tuple.expectedAddress = expectedAddress;
            tuple.postMoveAddress = postMoveAddress;
            tuple.expectedRow = expectedRow;
            tuple.expectedColumn = expectedColumn;
            tuple.moveBase = moveBase;
            tuple.boundsBase = boundsBase;
            tuple.selectCell = selectCell;
            tuple.status = static_cast<long>(DirectCellRangeStatus::OwnerMismatch);
            tuple.moveResultVt = moved.vt;
            tuple.postMoveListId = postMoveList;
            gTableRangeProbeTuples.push_back(std::move(tuple));
        }
        return DirectCellRangeStatus::OwnerMismatch;
    }

    CComVariant raw;
    const HRESULT called = Method(hwp, L"GetCellRangeIndex", {}, &raw);
    if (called == DISP_E_UNKNOWNNAME || called == DISP_E_MEMBERNOTFOUND)
        return DirectCellRangeStatus::NotExposed;
    if (FAILED(called)) return DirectCellRangeStatus::InvokeFailed;
    CComPtr<IDispatch> range;
    const HRESULT dispatchStatus = AsDispatch(raw, range);
    if (FAILED(dispatchStatus) || range == nullptr) {
        if (gTableRangeProbeTuples.size() < 24) {
            TableRangeProbeTuple tuple{};
            tuple.expectedAddress = expectedAddress;
            tuple.postMoveAddress = postMoveAddress;
            tuple.expectedRow = expectedRow;
            tuple.expectedColumn = expectedColumn;
            tuple.moveBase = moveBase;
            tuple.boundsBase = boundsBase;
            tuple.selectCell = selectCell;
            tuple.status = static_cast<long>(DirectCellRangeStatus::ResultInvalid);
            tuple.moveResultVt = moved.vt;
            tuple.rangeHresult = called;
            tuple.rangeResultVt = raw.vt;
            tuple.rangeDispatchNull = range == nullptr;
            tuple.postMoveListId = postMoveList;
            gTableRangeProbeTuples.push_back(std::move(tuple));
        }
        return DirectCellRangeStatus::ResultInvalid;
    }
    const auto member = [&range](const wchar_t* const name, LONG* const value) {
        CComVariant rawValue;
        return SUCCEEDED(PropertyGet(range, name, &rawValue)) &&
            SUCCEEDED(AsLong(rawValue, value));
    };
    LONG firstRow = 0, lastRow = 0, firstColumn = 0, lastColumn = 0;
    if (!member(L"StartRow", &firstRow) || !member(L"EndRow", &lastRow) ||
        !member(L"StartCol", &firstColumn) ||
        !member(L"EndCol", &lastColumn))
        return DirectCellRangeStatus::MemberReadFailed;
    if (gTableRangeProbeTuples.size() < 8) {
        TableRangeProbeTuple tuple{};
        tuple.expectedAddress = expectedAddress;
        tuple.postMoveAddress = postMoveAddress;
        tuple.expectedRow = expectedRow;
        tuple.expectedColumn = expectedColumn;
        tuple.moveBase = moveBase;
        tuple.boundsBase = boundsBase;
        tuple.selectCell = selectCell;
        tuple.status = static_cast<long>(DirectCellRangeStatus::Value);
        tuple.moveResultVt = moved.vt;
        tuple.rangeHresult = called;
        tuple.rangeResultVt = raw.vt;
        tuple.rangeDispatchNull = false;
        tuple.postMoveListId = postMoveList;
        tuple.startRow = firstRow;
        tuple.endRow = lastRow;
        tuple.startColumn = firstColumn;
        tuple.endColumn = lastColumn;
        gTableRangeProbeTuples.push_back(std::move(tuple));
    }
    if (firstRow < boundsBase || lastRow < firstRow ||
        firstColumn < boundsBase || lastColumn < firstColumn)
        return DirectCellRangeStatus::BoundsInvalid;
    const LONG normalizedRow = firstRow - boundsBase + 1;
    const LONG normalizedColumn = firstColumn - boundsBase + 1;
    if (normalizedRow != expectedRow || normalizedColumn != expectedColumn) {
        if ((firstRow == expectedRow || firstRow + 1 == expectedRow) &&
            (firstColumn == expectedColumn || firstColumn + 1 == expectedColumn))
            return DirectCellRangeStatus::CoordinateBaseMismatch;
        return DirectCellRangeStatus::OwnerMismatch;
    }
    *rowSpan = lastRow - firstRow + 1;
    *columnSpan = lastColumn - firstColumn + 1;
    return DirectCellRangeStatus::Value;
}

bool GeometryConsistentCover(
    const std::vector<TableCellRecord>& cells,
    const std::vector<std::pair<LONG, LONG>>& spans,
    const LONG rows,
    const LONG columns) {
    const auto axisConsistent = [&cells, &spans](
        const bool horizontal, const LONG limit) {
        using Constraint = std::pair<LONG, long long>;
        std::vector<std::vector<Constraint>> edges(
            static_cast<size_t>(limit) + 1);
        for (size_t index = 0; index < cells.size(); ++index) {
            LONG row = 0;
            LONG column = 0;
            if (!CellCoordinates(cells[index].address, &row, &column)) {
                return false;
            }
            const LONG start = (horizontal ? column : row) - 1;
            const LONG span = horizontal
                ? spans[index].second : spans[index].first;
            const long value = horizontal
                ? cells[index].width : cells[index].height;
            if (span == 0) continue;
            if (start < 0 || span < 1 || start > limit - span) return false;
            if (value < 0) continue;
            const LONG end = start + span;
            edges[static_cast<size_t>(start)].push_back({end, value});
            edges[static_cast<size_t>(end)].push_back({start, -value});
        }
        std::vector<LONG> component(static_cast<size_t>(limit) + 1, -1);
        std::vector<long long> position(static_cast<size_t>(limit) + 1, 0);
        LONG nextComponent = 0;
        for (LONG origin = 0; origin <= limit; ++origin) {
            if (component[static_cast<size_t>(origin)] >= 0) continue;
            std::vector<LONG> pending{origin};
            component[static_cast<size_t>(origin)] = nextComponent++;
            while (!pending.empty()) {
                const LONG from = pending.back();
                pending.pop_back();
                for (const auto& [to, delta] :
                     edges[static_cast<size_t>(from)]) {
                    const long long expected =
                        position[static_cast<size_t>(from)] + delta;
                    if (component[static_cast<size_t>(to)] < 0) {
                        component[static_cast<size_t>(to)] =
                            component[static_cast<size_t>(from)];
                        position[static_cast<size_t>(to)] = expected;
                        pending.push_back(to);
                    } else if (
                        component[static_cast<size_t>(to)] !=
                            component[static_cast<size_t>(from)] ||
                        position[static_cast<size_t>(to)] != expected) {
                        return false;
                    }
                }
            }
        }
        for (LONG left = 0; left < limit; ++left) {
            for (LONG right = left + 1; right <= limit; ++right) {
                if (component[static_cast<size_t>(left)] ==
                        component[static_cast<size_t>(right)] &&
                    position[static_cast<size_t>(left)] >=
                        position[static_cast<size_t>(right)]) {
                    return false;
                }
            }
        }
        return true;
    };
    return axisConsistent(true, columns) && axisConsistent(false, rows);
}

bool InferUniqueExactCover(
    std::vector<TableCellRecord>* const cells,
    const LONG rows,
    const LONG columns) {
    if (cells == nullptr || cells->empty() || rows < 1 || columns < 1) {
        return false;
    }
    std::map<std::pair<LONG, LONG>, size_t> ownerAt;
    for (size_t index = 0; index < cells->size(); ++index) {
        LONG row = 0;
        LONG column = 0;
        if (!CellCoordinates((*cells)[index].address, &row, &column) ||
            row > rows || column > columns ||
            !ownerAt.emplace(std::pair(row, column), index).second) {
            return false;
        }
    }
    std::vector<unsigned char> covered(
        static_cast<size_t>(rows) * static_cast<size_t>(columns), 0);
    std::vector<std::pair<LONG, LONG>> current(cells->size(), {0, 0});
    std::vector<std::pair<LONG, LONG>> unique;
    unsigned solutions = 0;
    std::uint64_t explored = 0;
    bool exhausted = false;
    constexpr std::uint64_t kMaximumExactCoverStates = 100'000;
    const auto slot = [columns](const LONG row, const LONG column) {
        return static_cast<size_t>(row - 1) * static_cast<size_t>(columns) +
            static_cast<size_t>(column - 1);
    };
    std::function<void()> search = [&]() {
        if (solutions > 1 || exhausted) return;
        if (++explored > kMaximumExactCoverStates) {
            exhausted = true;
            return;
        }
        LONG firstRow = 0;
        LONG firstColumn = 0;
        for (LONG row = 1; row <= rows && firstRow == 0; ++row) {
            for (LONG column = 1; column <= columns; ++column) {
                if (covered[slot(row, column)] == 0) {
                    firstRow = row;
                    firstColumn = column;
                    break;
                }
            }
        }
        if (firstRow == 0) {
            if (GeometryConsistentCover(*cells, current, rows, columns)) {
                ++solutions;
                if (solutions == 1) unique = current;
            }
            return;
        }
        const auto owner = ownerAt.find({firstRow, firstColumn});
        if (owner == ownerAt.end()) return;
        const size_t ownerIndex = owner->second;
        for (LONG lastRow = firstRow; lastRow <= rows; ++lastRow) {
            for (LONG lastColumn = firstColumn; lastColumn <= columns;
                 ++lastColumn) {
                bool legal = true;
                for (LONG row = firstRow; row <= lastRow && legal; ++row) {
                    for (LONG column = firstColumn; column <= lastColumn;
                         ++column) {
                        if (covered[slot(row, column)] != 0 ||
                            ((row != firstRow || column != firstColumn) &&
                             ownerAt.find({row, column}) != ownerAt.end())) {
                            legal = false;
                            break;
                        }
                    }
                }
                if (!legal) continue;
                for (LONG row = firstRow; row <= lastRow; ++row)
                    for (LONG column = firstColumn; column <= lastColumn; ++column)
                        covered[slot(row, column)] = 1;
                current[ownerIndex] = {
                    lastRow - firstRow + 1, lastColumn - firstColumn + 1};
                if (GeometryConsistentCover(
                        *cells, current, rows, columns)) {
                    search();
                }
                for (LONG row = firstRow; row <= lastRow; ++row)
                    for (LONG column = firstColumn; column <= lastColumn; ++column)
                        covered[slot(row, column)] = 0;
                current[ownerIndex] = {0, 0};
            }
        }
    };
    search();
    if (exhausted || solutions != 1 || unique.size() != cells->size()) {
        return false;
    }
    for (size_t index = 0; index < cells->size(); ++index) {
        if (unique[index].first < 1 || unique[index].second < 1) return false;
        (*cells)[index].rowSpan = unique[index].first;
        (*cells)[index].columnSpan = unique[index].second;
    }
    return true;
}

bool InferCellSpans(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& tableInstanceId,
    const LONG suppliedRows,
    const LONG suppliedColumns,
    std::vector<TableCellRecord>* const cells,
    std::wstring* const error) {
    LONG rows = 0;
    LONG columns = 0;
    std::set<std::pair<LONG, LONG>> owners;
    for (const TableCellRecord& cell : *cells) {
        LONG row = 0;
        LONG column = 0;
        if (!CellCoordinates(cell.address, &row, &column)) {
            return Fail(error, L"table cell address is invalid");
        }
        rows = (std::max)(rows, row);
        columns = (std::max)(columns, column);
        owners.emplace(row, column);
    }
    if (rows < 1 || columns < 1) {
        return Fail(error, L"table dimensions are invalid");
    }
    LONG authoritativeRows = suppliedRows;
    LONG authoritativeColumns = suppliedColumns;
    // Dimensions supplied by the graph reader came from the exact native table
    // dispatch before caret navigation. Otherwise the caret is already bound
    // to this table by EnterTable; never load TablePropertyDialog defaults.
    bool dimensionsAvailable =
        authoritativeRows > 0 && authoritativeColumns > 0;
    if (!dimensionsAvailable) {
        dimensionsAvailable = ReadCaretTableDimensions(
            hwp, &authoritativeRows, &authoritativeColumns);
        if (dimensionsAvailable) {
            ++gTableCallCounters.dimensionCaret;
        }
    }
    const bool dimensionsValid = dimensionsAvailable &&
        authoritativeRows >= rows && authoritativeColumns >= columns;
    if (dimensionsValid && InferUniqueExactCover(
            cells, authoritativeRows, authoritativeColumns)) {
        ++gTableCallCounters.exactCover;
        return true;
    }
    if (!dimensionsAvailable)
        ++gTableCallCounters.exactCoverDimensionUnavailable;
    else if (!dimensionsValid)
        ++gTableCallCounters.exactCoverDimensionInvalid;
    else
        ++gTableCallCounters.exactCoverAmbiguous;

    LONG moveBase = -1;
    LONG boundsBase = -1;
    const TableCellRecord* baseProbe = nullptr;
    for (const TableCellRecord& candidate : *cells) {
        LONG probeRow = 0;
        LONG probeColumn = 0;
        if (CellCoordinates(candidate.address, &probeRow, &probeColumn) &&
            (probeRow > 1 || probeColumn > 1) && probeRow != probeColumn) {
            baseProbe = &candidate;
            break;
        }
    }
    if (baseProbe == nullptr) {
        for (const TableCellRecord& candidate : *cells) {
            LONG probeRow = 0;
            LONG probeColumn = 0;
            if (CellCoordinates(candidate.address, &probeRow, &probeColumn) &&
                (probeRow > 1 || probeColumn > 1)) {
                baseProbe = &candidate;
                break;
            }
        }
    }
    wchar_t diagnosticPath[2]{};
    const bool diagnostic = GetEnvironmentVariableW(
        L"TODO18_GEOMETRY_PROBE_PATH", diagnosticPath, 2) != 0;
    if (diagnostic && baseProbe != nullptr) {
        std::vector<const TableCellRecord*> probes;
        probes.push_back(&cells->front());
        if (baseProbe != probes.front()) probes.push_back(baseProbe);
        if (&cells->back() != probes.front() && &cells->back() != baseProbe)
            probes.push_back(&cells->back());
        for (const TableCellRecord* const probe : probes) {
            LONG probeRow = 0;
            LONG probeColumn = 0;
            if (!CellCoordinates(
                    probe->address, &probeRow, &probeColumn)) {
                continue;
            }
            for (LONG select = 1; select >= 0; --select) {
                for (LONG candidateMoveBase = 0; candidateMoveBase <= 1;
                     ++candidateMoveBase) {
                    for (LONG candidateBoundsBase = 0;
                         candidateBoundsBase <= 1; ++candidateBoundsBase) {
                        LONG ignoredRowSpan = 0;
                        LONG ignoredColumnSpan = 0;
                        if (SetPosition(hwp, probe->listId)) {
                            static_cast<void>(ReadDirectCellRange(
                                hwp, tableInstanceId, probe->address,
                                probe->listId, probeRow, probeColumn,
                                candidateMoveBase, candidateBoundsBase,
                                select != 0, &ignoredRowSpan,
                                &ignoredColumnSpan));
                        }
                    }
                }
            }
        }
        return Fail(error, L"TODO18_TABLE_RANGE_DIAGNOSTIC_COMPLETE");
    }

    unsigned qualifyingPairs = 0;
    DirectCellRangeStatus qualificationStatus =
        DirectCellRangeStatus::CoordinateBaseMismatch;
    if (dimensionsAvailable && baseProbe != nullptr) {
        LONG probeRow = 0;
        LONG probeColumn = 0;
        static_cast<void>(CellCoordinates(
            baseProbe->address, &probeRow, &probeColumn));
        for (LONG candidateMoveBase = 0; candidateMoveBase <= 1;
             ++candidateMoveBase) {
            for (LONG candidateBoundsBase = 0; candidateBoundsBase <= 1;
                 ++candidateBoundsBase) {
                LONG ignoredRowSpan = 0;
                LONG ignoredColumnSpan = 0;
                if (!SetPosition(hwp, baseProbe->listId)) {
                    qualificationStatus = DirectCellRangeStatus::OwnerMismatch;
                    continue;
                }
                qualificationStatus = ReadDirectCellRange(
                    hwp, tableInstanceId, baseProbe->address,
                    baseProbe->listId, probeRow, probeColumn,
                    candidateMoveBase, candidateBoundsBase, false,
                    &ignoredRowSpan, &ignoredColumnSpan);
                if (qualificationStatus == DirectCellRangeStatus::Value) {
                    moveBase = candidateMoveBase;
                    boundsBase = candidateBoundsBase;
                    ++qualifyingPairs;
                } else if (
                    qualificationStatus == DirectCellRangeStatus::NotExposed ||
                    qualificationStatus == DirectCellRangeStatus::InvokeFailed ||
                    qualificationStatus == DirectCellRangeStatus::ResultInvalid ||
                    qualificationStatus == DirectCellRangeStatus::MemberReadFailed) {
                    candidateMoveBase = 2;
                    break;
                }
            }
        }
    }
    if (qualifyingPairs != 1) {
        moveBase = -1;
        boundsBase = -1;
        switch (qualificationStatus) {
        case DirectCellRangeStatus::MoveToCellFailed:
            ++gTableCallCounters.moveToCellFailed;
            break;
        case DirectCellRangeStatus::NotExposed:
            ++gTableCallCounters.rangeNotExposed;
            break;
        case DirectCellRangeStatus::InvokeFailed:
            ++gTableCallCounters.rangeInvokeFailed;
            break;
        case DirectCellRangeStatus::ResultInvalid:
            ++gTableCallCounters.rangeResultInvalid;
            break;
        case DirectCellRangeStatus::MemberReadFailed:
            ++gTableCallCounters.rangeMemberReadFailed;
            break;
        case DirectCellRangeStatus::BoundsInvalid:
            ++gTableCallCounters.rangeBoundsInvalid;
            break;
        case DirectCellRangeStatus::CoordinateBaseMismatch:
            ++gTableCallCounters.rangeCoordinateBaseMismatch;
            break;
        case DirectCellRangeStatus::OwnerMismatch:
            ++gTableCallCounters.rangeOwnerMismatch;
            break;
        case DirectCellRangeStatus::Value:
            ++gTableCallCounters.rangeTopologyRejected;
            break;
        case DirectCellRangeStatus::QualificationFailed:
            break;
        }
    }

    std::vector<LONG> directColumnSpans(cells->size(), 0);
    for (size_t index = 0; index < cells->size(); ++index) {
        TableCellRecord& cell = (*cells)[index];
        LONG row = 0;
        LONG column = 0;
        static_cast<void>(CellCoordinates(cell.address, &row, &column));

        cell.rowSpan = rows - row + 1;
        if (!SetPosition(hwp, cell.listId)) {
            return Fail(error, L"table cell could not be positioned for range inspection");
        }
        LONG directRowSpan = 0;
        LONG directColumnSpan = 0;
        const DirectCellRangeStatus direct = moveBase < 0 || boundsBase < 0
            ? DirectCellRangeStatus::QualificationFailed
            : ReadDirectCellRange(
                hwp, tableInstanceId, cell.address, cell.listId,
                row, column, moveBase, boundsBase, false,
                &directRowSpan, &directColumnSpan);
        if (direct == DirectCellRangeStatus::Value &&
            directRowSpan >= 1 && directColumnSpan >= 1) {
            const LONG qualifiedLastRow = row + directRowSpan - 1;
            const LONG qualifiedLastColumn = column + directColumnSpan - 1;
            if (qualifiedLastRow >= row && qualifiedLastColumn >= column) {
                ++gTableCallCounters.directRange;
                rows = (std::max)(rows, qualifiedLastRow);
                columns = (std::max)(columns, qualifiedLastColumn);
                cell.rowSpan = directRowSpan;
                directColumnSpans[index] = directColumnSpan;
                continue;
            }
        }
        switch (direct) {
        case DirectCellRangeStatus::MoveToCellFailed:
            ++gTableCallCounters.moveToCellFailed;
            break;
        case DirectCellRangeStatus::NotExposed:
            ++gTableCallCounters.rangeNotExposed;
            break;
        case DirectCellRangeStatus::InvokeFailed:
            ++gTableCallCounters.rangeInvokeFailed;
            break;
        case DirectCellRangeStatus::ResultInvalid:
            ++gTableCallCounters.rangeResultInvalid;
            break;
        case DirectCellRangeStatus::MemberReadFailed:
            ++gTableCallCounters.rangeMemberReadFailed;
            break;
        case DirectCellRangeStatus::BoundsInvalid:
            ++gTableCallCounters.rangeBoundsInvalid;
            break;
        case DirectCellRangeStatus::CoordinateBaseMismatch:
            ++gTableCallCounters.rangeCoordinateBaseMismatch;
            break;
        case DirectCellRangeStatus::OwnerMismatch:
            ++gTableCallCounters.rangeOwnerMismatch;
            break;
        case DirectCellRangeStatus::Value:
            ++gTableCallCounters.rangeTopologyRejected;
            break;
        case DirectCellRangeStatus::QualificationFailed:
            break;
        }
        ++gTableCallCounters.fallback;
        if (RunTableNavigationAction(
                hwp,
                action,
                L"TableLowerCell",
                tableInstanceId)) {
            const std::wstring nextAddress = CellAddress(hwp);
            LONG nextRow = 0;
            LONG nextColumn = 0;
            if (CellCoordinates(nextAddress, &nextRow, &nextColumn) && nextRow > row) {
                cell.downAddress = nextAddress;
                cell.rowSpan = nextRow - row;
            }
        }

        if (!SetPosition(hwp, cell.listId)) {
            return Fail(error, L"table cell could not be positioned for column span inspection");
        }
        if (RunTableNavigationAction(
                hwp,
                action,
                L"TableRightCell",
                tableInstanceId)) {
            const std::wstring nextAddress = CellAddress(hwp);
            LONG nextRow = 0;
            LONG nextColumn = 0;
            if (CellCoordinates(nextAddress, &nextRow, &nextColumn) &&
                nextRow == row && nextColumn > column) {
                cell.rightAddress = nextAddress;
                directColumnSpans[index] = nextColumn - column;
            }
        }
        if (cell.rowSpan < 1 || row + cell.rowSpan - 1 > rows) {
            return Fail(error, L"table row span is invalid");
        }
    }

    std::set<std::pair<LONG, LONG>> covered;
    const auto coverCell = [&](const size_t index, const LONG columnSpan) {
        LONG row = 0;
        LONG column = 0;
        static_cast<void>(CellCoordinates((*cells)[index].address, &row, &column));
        if (columnSpan < 1 || column + columnSpan - 1 > columns) {
            return false;
        }
        for (LONG currentRow = row; currentRow < row + (*cells)[index].rowSpan; ++currentRow) {
            for (LONG currentColumn = column;
                 currentColumn < column + columnSpan;
                 ++currentColumn) {
                if (!covered.emplace(currentRow, currentColumn).second) {
                    return false;
                }
            }
        }
        (*cells)[index].columnSpan = columnSpan;
        return true;
    };

    for (size_t index = 0; index < cells->size(); ++index) {
        if (directColumnSpans[index] > 0 && !coverCell(index, directColumnSpans[index])) {
            return Fail(error, L"table cell geometry overlaps");
        }
    }

    std::vector<size_t> unresolved;
    for (size_t index = 0; index < cells->size(); ++index) {
        if (directColumnSpans[index] == 0) {
            unresolved.push_back(index);
        }
    }
    std::sort(unresolved.begin(), unresolved.end(), [&](const size_t left, const size_t right) {
        LONG leftRow = 0;
        LONG leftColumn = 0;
        LONG rightRow = 0;
        LONG rightColumn = 0;
        static_cast<void>(CellCoordinates((*cells)[left].address, &leftRow, &leftColumn));
        static_cast<void>(CellCoordinates((*cells)[right].address, &rightRow, &rightColumn));
        return std::pair(leftRow, leftColumn) < std::pair(rightRow, rightColumn);
    });
    for (const size_t index : unresolved) {
        LONG row = 0;
        LONG column = 0;
        static_cast<void>(CellCoordinates((*cells)[index].address, &row, &column));
        LONG columnSpan = columns - column + 1;
        for (LONG nextColumn = column + 1; nextColumn <= columns; ++nextColumn) {
            if (covered.find(std::pair(row, nextColumn)) != covered.end()) {
                columnSpan = nextColumn - column;
                break;
            }
        }
        if (!coverCell(index, columnSpan)) {
            return Fail(error, L"table cell geometry overlaps");
        }
    }

    if (covered.size() != static_cast<size_t>(rows) * static_cast<size_t>(columns) ||
        owners.size() != cells->size()) {
        return Fail(error, L"table cell geometry is incomplete");
    }
    return true;
}

}

bool ReadAuthoritativeTableDimensions(
    IDispatch* const hwp,
    IDispatch* const authoritativeTable,
    const std::wstring& tableInstanceId,
    const bool instanceIdUniquelyQualified,
    long* const rows,
    long* const columns,
    const bool authoritativePropertiesAlreadyProbed,
    const TableDispatchTypeContext* const dispatchTypes) noexcept {
    TableDispatchTypeScope dispatchScope(dispatchTypes);
    try {
        if (hwp == nullptr || authoritativeTable == nullptr ||
            rows == nullptr || columns == nullptr) {
            return false;
        }
        LONG observedRows = 0;
        LONG observedColumns = 0;
        if (!authoritativePropertiesAlreadyProbed &&
            ReadTableDispatchDimensions(
                authoritativeTable, &observedRows, &observedColumns)) {
            ++gTableCallCounters.dimensionProperties;
            *rows = observedRows;
            *columns = observedColumns;
            return true;
        }
        CComPtr<IDispatch> action;
        if (!DispatchProperty(hwp, L"HAction", action, RootType())) {
            ++gTableCallCounters.dimensionBindFailed;
            return false;
        }
        const bool bound = instanceIdUniquelyQualified
            ? SelectExactTable(hwp, action, tableInstanceId)
            : MoveToControl(hwp, authoritativeTable) &&
                SelectTableFront(hwp, action, tableInstanceId);
        if (!bound) {
            ++gTableCallCounters.dimensionBindFailed;
            return false;
        }
        if (!ReadCaretTableDimensions(
                hwp, &observedRows, &observedColumns) ||
            observedRows < 1 || observedColumns < 1) {
            ++gTableCallCounters.dimensionReadFailed;
            return false;
        }
        ++gTableCallCounters.dimensionCaret;
        *rows = observedRows;
        *columns = observedColumns;
        return true;
    } catch (...) {
        return false;
    }
}

bool SelectTableControl(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId) noexcept {
    try {
        CComPtr<IDispatch> action;
        return hwp != nullptr &&
            DispatchProperty(hwp, L"HAction", action, RootType()) &&
            SelectExactTable(hwp, action, tableInstanceId);
    } catch (...) {
        return false;
    }
}

bool ReadSelectedCellAddresses(
    IDispatch* const hwp,
    std::vector<std::wstring>* const addresses,
    std::wstring* const error) noexcept {
    try {
        if (hwp == nullptr || addresses == nullptr) {
            return Fail(error, L"selected cell address output is invalid");
        }
        addresses->clear();
        if (!TableFormulaIsSafe(hwp)) {
            return Fail(
                error,
                L"TableFormula selection is larger than 9 by 9 or its table size could not be verified");
        }
        CComPtr<IDispatch> action;
        CComPtr<IDispatch> set;
        CComVariant raw;
        CComVariant ignored;
        std::wstring command;
        if (FAILED(Method(
                hwp,
                L"CreateAction",
                {CComVariant(L"TableFormula")},
                &raw)) ||
            FAILED(AsDispatch(raw, action))) {
            return Fail(error, L"TableFormula action could not be created");
        }
        raw.Clear();
        if (FAILED(Method(action, L"CreateSet", {}, &raw)) ||
            FAILED(AsDispatch(raw, set))) {
            return Fail(error, L"TableFormula parameter set could not be created");
        }
        if (FAILED(Method(
                action,
                L"GetDefault",
                {CComVariant(set)},
                &ignored))) {
            return Fail(error, L"TableFormula action GetDefault failed");
        }
        raw.Clear();
        if (FAILED(Method(set, L"Item", {CComVariant(L"Command")}, &raw)) ||
            FAILED(AsString(raw, &command))) {
            return Fail(error, L"TableFormula Command item could not be read");
        }
        if (!ParseSelectedCellAddresses(command, addresses)) {
            return Fail(error, L"TableFormula Command did not contain selected cell addresses");
        }
        if (addresses->size() < 2) {
            addresses->clear();
            return Fail(
                error,
                L"TableFormula Command resolved only one strict-selection cell: " +
                    command.substr(0, 160));
        }
        return true;
    } catch (...) {
        if (addresses != nullptr) {
            addresses->clear();
        }
        return Fail(error, L"selected cell address inspection failed unexpectedly");
    }
}

bool ReadCurrentListText(
    IDispatch* const hwp,
    std::wstring* const text) noexcept {
    try {
        constexpr LONG kCurrentListRange = 0x0055;
        if (text == nullptr || !CallBoolean(
                hwp,
                L"InitScan",
                {CComVariant(0L), CComVariant(kCurrentListRange), CComVariant(0L),
                 CComVariant(0L), CComVariant(0L), CComVariant(0L)})) {
            return false;
        }
        text->clear();
        bool complete = false;
        bool valid = true;
        for (size_t iteration = 0; iteration < 100'000; ++iteration) {
            BSTR chunk = nullptr;
            CComVariant chunkArgument;
            chunkArgument.vt = VT_BSTR | VT_BYREF;
            chunkArgument.pbstrVal = &chunk;
            CComVariant rawState;
            const HRESULT status = TypedMethod(
                hwp, RootType(), L"GetText", {chunkArgument}, &rawState);
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
            if (state <= 1) {
                complete = true;
                break;
            }
        }
        const HRESULT released = TypedMethod(
            hwp, RootType(), L"ReleaseScan", {}, nullptr);
        return valid && complete && SUCCEEDED(released);
    } catch (...) {
        return false;
    }
}

bool InspectTableCaption(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    TableCaptionObservation* const caption,
    std::wstring* const error) noexcept {
    if (caption == nullptr || hwp == nullptr) return false;
    *caption = {};
    if (error != nullptr) error->clear();
    try {
        if (!SelectTableControl(hwp, tableInstanceId))
            return Fail(error, L"table caption owner could not be selected");
        CComVariant rawEnabled;
        bool enabled = false;
        if (FAILED(Method(
                hwp, L"IsActionEnable",
                {CComVariant(L"ShapeObjDetachCaption")}, &rawEnabled)) ||
            FAILED(AsBool(rawEnabled, &enabled))) {
            caption->presenceState = NativeObservationState::NotExposed;
            return true;
        }
        caption->presenceState = NativeObservationState::Value;
        if (!enabled) return true;

        CComPtr<IDispatch> action;
        hancom::com_state::Position tablePosition{};
        hancom::com_state::Position captionPosition{};
        if (!DispatchProperty(hwp, L"HAction", action, RootType()) ||
            FAILED(hancom::com_state::CapturePosition(hwp, &tablePosition)) ||
            !RunAction(action, L"ShapeObjAttachCaption") ||
            FAILED(hancom::com_state::CapturePosition(hwp, &captionPosition)) ||
            captionPosition.list == tablePosition.list) {
            return Fail(error, L"existing table caption list could not be entered");
        }
        caption->exists = true;
        caption->listId = captionPosition.list;
        caption->paragraph = captionPosition.paragraph;
        caption->character = captionPosition.character;
        caption->textState = ReadCurrentListText(hwp, &caption->text)
            ? NativeObservationState::Value
            : NativeObservationState::ReadFailed;

        CComPtr<IDispatch> parameterSets;
        CComPtr<IDispatch> style;
        CComPtr<IDispatch> styleSet;
        CComVariant ignored;
        if (DispatchProperty(
                hwp, L"HParameterSet", parameterSets, RootType()) &&
            DispatchProperty(parameterSets, L"HStyle", style) &&
            DispatchProperty(style, L"HSet", styleSet) &&
            SUCCEEDED(Method(
                action, L"GetDefault",
                {CComVariant(L"Style"), CComVariant(styleSet)}, &ignored))) {
            CComVariant rawStyle;
            LONG styleId = -1;
            if (SUCCEEDED(PropertyGet(style, L"Apply", &rawStyle)) &&
                SUCCEEDED(AsLong(rawStyle, &styleId)) && styleId >= 0) {
                caption->styleIdState = NativeObservationState::Value;
                caption->styleId = styleId;
            }
        }
        CComPtr<IDispatch> styleItem;
        CComPtr<IDispatch> styleItemSet;
        ignored.Clear();
        if (DispatchProperty(parameterSets, L"HStyleItem", styleItem) &&
            DispatchProperty(styleItem, L"HSet", styleItemSet) &&
            SUCCEEDED(Method(
                action, L"GetDefault",
                {CComVariant(L"StyleChangeToCurrentShape"),
                 CComVariant(styleItemSet)}, &ignored))) {
            CComVariant rawName;
            if (SUCCEEDED(PropertyGet(styleItem, L"NameLocal", &rawName)) &&
                SUCCEEDED(AsString(rawName, &caption->styleName))) {
                caption->styleNameState = NativeObservationState::Value;
            }
        }

        CComPtr<IDispatch> info;
        if (DocumentInfo(hwp, info) &&
            CurrentPage(info, &caption->pageStart)) {
            caption->pageStartState = NativeObservationState::Value;
        } else {
            caption->pageStartState = NativeObservationState::ReadFailed;
        }
        if (RunAction(action, L"MoveListEnd") &&
            CurrentPage(info, &caption->pageEnd)) {
            caption->pageEndState = NativeObservationState::Value;
        } else {
            caption->pageEndState = NativeObservationState::ReadFailed;
        }
        if (!RunAction(action, L"CloseEx"))
            return Fail(error, L"table caption list could not be closed");
        if (caption->pageStartState == NativeObservationState::Value &&
            caption->pageEndState == NativeObservationState::Value &&
            caption->pageEnd < caption->pageStart)
            std::swap(caption->pageStart, caption->pageEnd);
        return true;
    } catch (...) {
        return Fail(error, L"table caption inspection failed unexpectedly");
    }
}

namespace {

bool EnterTable(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& tableInstanceId) {
    if (TableContextMatches(hwp, tableInstanceId)) {
        return true;
    }
    if (SelectExactTable(hwp, action, tableInstanceId) &&
        EnterSelectedTable(hwp, action, tableInstanceId)) {
        return true;
    }
    CComPtr<IDispatch> control;
    return FindControl(hwp, tableInstanceId, control) &&
        MoveToControl(hwp, control) &&
        SelectTableFront(hwp, action, tableInstanceId) &&
        EnterSelectedTable(hwp, action, tableInstanceId);
}

// How many cells of one table report their appearance. Reading every cell
// would multiply the inspection payload by the cell count; twelve bounded
// representative positions cover header, early, middle, and last-row policy.
constexpr size_t kCellFormatSampleLimit = 12;

// Property names of one cell side. The left color really is spelled
// "BorderCorlor" -- the typo lives in HWP, and the write path in
// ReferenceLayoutCommands.cpp and hwp_live_table_format.py spells it the same
// way, so reading it back has to as well.
struct CellBorderPropertyNames {
    const wchar_t* type;
    const wchar_t* width;
    const wchar_t* color;
};

constexpr CellBorderPropertyNames kCellBorderProperties[kCellBorderSideCount] = {
    {L"BorderTypeLeft", L"BorderWidthLeft", L"BorderCorlorLeft"},
    {L"BorderTypeRight", L"BorderWidthRight", L"BorderColorRight"},
    {L"BorderTypeTop", L"BorderWidthTop", L"BorderColorTop"},
    {L"BorderTypeBottom", L"BorderWidthBottom", L"BorderColorBottom"},
};

static_assert(
    kCellBorderLeft == 0 && kCellBorderRight == 1 && kCellBorderTop == 2 &&
        kCellBorderBottom == 3,
    "kCellBorderProperties and every border array are left, right, top, bottom");

// HWP exposes parameter set members both as automation properties and through
// Item(), and which one answers depends on the set. Both are tried before a
// value is reported as unreadable, the same way ReadReferenceBorderValue in
// ActionReferenceLayout.cpp does for the write-side readback.
bool DirectBorderColorPropertyGet(
    IDispatch* const set,
    const wchar_t* const name,
    CComVariant* const value) noexcept {
#if !defined(_M_IX86)
    static_cast<void>(set);
    static_cast<void>(name);
    static_cast<void>(value);
    return false;
#else
    if (set == nullptr || name == nullptr || value == nullptr) {
        return false;
    }
    size_t* cachedSlot = nullptr;
    size_t expectedSlot = 0;
    if (std::wcscmp(name, L"BorderCorlorLeft") == 0) {
        cachedSlot = &gBorderColorLeftSlot;
        expectedSlot = kBorderColorLeftSlot;
    } else if (std::wcscmp(name, L"BorderColorRight") == 0) {
        cachedSlot = &gBorderColorRightSlot;
        expectedSlot = kBorderColorRightSlot;
    } else if (std::wcscmp(name, L"BorderColorTop") == 0) {
        cachedSlot = &gBorderColorTopSlot;
        expectedSlot = kBorderColorTopSlot;
    } else if (std::wcscmp(name, L"BorderColorBottom") == 0) {
        cachedSlot = &gBorderColorBottomSlot;
        expectedSlot = kBorderColorBottomSlot;
    } else {
        return false;
    }
    size_t resolvedSlot = *cachedSlot;
    if (resolvedSlot == kUnresolvedVirtualSlot) {
        CComPtr<IUnknown> interfaceObject;
        if (FAILED(
                official_api::ResolveVirtualPropertyGet(
                    set, name, VT_UI4, interfaceObject, &resolvedSlot)) ||
            resolvedSlot != expectedSlot) {
            return false;
        }
    }
    ULONG directValue = 0;
    HRESULT status =
        official_api::InvokeResolvedVirtualUnsignedLongPropertyGet(
            set,
            kCellBorderFillVirtualInterfaceId,
            resolvedSlot,
            &directValue);
    if (FAILED(status)) {
        status =
            official_api::InvokeResolvedVirtualUnsignedLongPropertyGet(
                set,
                kBorderFillVirtualInterfaceId,
                resolvedSlot,
                &directValue);
    }
    if (FAILED(status) || FAILED(value->Clear())) {
        return false;
    }
    value->vt = VT_UI4;
    value->ulVal = directValue;
    *cachedSlot = resolvedSlot;
    ++gTableCallCounters.directVirtualPropertyGets;
    return true;
#endif
}

HRESULT MemberValue(
    IDispatch* const set,
    const wchar_t* const name,
    CComVariant* const value,
    const dispatch::TypeIdentityToken* const type = nullptr) {
    if (set == nullptr) {
        return E_POINTER;
    }
    if (DirectBorderColorPropertyGet(set, name, value)) {
        return S_OK;
    }
    const HRESULT status = TypedPropertyGet(set, type, name, value);
    if (SUCCEEDED(status)) {
        return status;
    }
    value->Clear();
    return TypedMethod(set, type, L"Item", {CComVariant(name)}, value);
}

bool MemberLong(
    IDispatch* const set,
    const wchar_t* const name,
    long* const value,
    const dispatch::TypeIdentityToken* const type = nullptr) {
    CComVariant raw;
    LONG parsed = 0;
    if (FAILED(MemberValue(set, name, &raw, type)) ||
        FAILED(AsLong(raw, &parsed))) {
        return false;
    }
    *value = static_cast<long>(parsed);
    return true;
}

bool MemberBoolean(
    IDispatch* const set,
    const wchar_t* const name,
    long* const value,
    const dispatch::TypeIdentityToken* const type = nullptr) {
    CComVariant raw;
    bool parsed = false;
    if (FAILED(MemberValue(set, name, &raw, type)) ||
        FAILED(AsBool(raw, &parsed))) {
        return false;
    }
    *value = parsed ? 1 : 0;
    return true;
}

bool MemberText(
    IDispatch* const set,
    const wchar_t* const name,
    std::wstring* const value,
    const dispatch::TypeIdentityToken* const type = nullptr) {
    CComVariant raw;
    return SUCCEEDED(MemberValue(set, name, &raw, type)) &&
        SUCCEEDED(AsString(raw, value));
}

bool MemberDispatch(
    IDispatch* const set,
    const wchar_t* const name,
    CComPtr<IDispatch>& value,
    const dispatch::TypeIdentityToken* const type = nullptr) {
    CComVariant raw;
    value.Release();
    return SUCCEEDED(MemberValue(set, name, &raw, type)) &&
        SUCCEEDED(AsDispatch(raw, value));
}

struct CellFormatDispatches final {
    CComPtr<IDispatch> borderFillParameter;
    CComPtr<IDispatch> borderFillSet;
    CComPtr<IDispatch> shapeParameter;
    CComPtr<IDispatch> shapeSet;
    CComPtr<IDispatch> characterParameter;
    CComPtr<IDispatch> characterSet;
    CComPtr<IDispatch> paragraphParameter;
    CComPtr<IDispatch> paragraphSet;
    CComPtr<IDispatch> cellShape;
};

// Loads what the action would apply to the cell the caret sits in. Parameter
// set dispatches are stable containers, while GetDefault repopulates them for
// every physical cell. Keep the containers for one table but never reuse a
// cell's values.
bool LoadActionDefaults(
    IDispatch* const action,
    IDispatch* const parameterSets,
    const wchar_t* const actionName,
    const wchar_t* const parameterName,
    CComPtr<IDispatch>& parameter,
    CComPtr<IDispatch>& set) {
    if (parameter == nullptr || set == nullptr) {
        parameter.Release();
        set.Release();
        if (!DispatchProperty(
                parameterSets, parameterName, parameter, ParameterSetType()) ||
            !DispatchProperty(
                parameter, L"HSet", set, ParameterSetType())) {
            return false;
        }
    }
    CComVariant ignored;
    return SUCCEEDED(TypedMethod(
        action,
        ActionType(),
        L"GetDefault",
        {CComVariant(actionName), CComVariant(set)},
        &ignored));
}

void ReadCellFillFormat(
    IDispatch* const action,
    IDispatch* const parameterSets,
    CellFormatDispatches* const dispatches,
    TableCellFormat* const format) {
    if (!LoadActionDefaults(
            action,
            parameterSets,
            L"CellFill",
            L"HCellBorderFill",
            dispatches->borderFillParameter,
            dispatches->borderFillSet)) {
        return;
    }
    CComPtr<IDispatch> fill;
    if (!MemberDispatch(
            dispatches->borderFillParameter, L"FillAttr", fill,
            ParameterSetType()) &&
        !MemberDispatch(
            dispatches->borderFillSet, L"FillAttr", fill,
            ParameterSetType())) {
        return;
    }
    static_cast<void>(MemberLong(
        fill, L"WinBrushFaceColor", &format->fillColor, ParameterSetType()));
    static_cast<void>(MemberLong(
        fill, L"WinBrushHatchColor", &format->fillHatchColor,
        ParameterSetType()));
    static_cast<void>(MemberLong(
        fill, L"WinBrushAlpha", &format->fillAlpha, ParameterSetType()));
    static_cast<void>(MemberLong(
        fill, L"WindowsBrush", &format->fillBrush, ParameterSetType()));
}

void ReadCellBorderFormat(
    IDispatch* const action,
    IDispatch* const parameterSets,
    CellFormatDispatches* const dispatches,
    TableCellFormat* const format) {
    if (!LoadActionDefaults(
            action,
            parameterSets,
            L"CellBorder",
            L"HCellBorderFill",
            dispatches->borderFillParameter,
            dispatches->borderFillSet)) {
        return;
    }
    for (size_t side = 0; side < kCellBorderSideCount; ++side) {
        const CellBorderPropertyNames& names = kCellBorderProperties[side];
        const std::pair<const wchar_t*, long*> members[] = {
            {names.type, &format->borderType[side]},
            {names.width, &format->borderWidth[side]},
            {names.color, &format->borderColor[side]},
        };
        for (const auto& [name, value] : members) {
            if (!MemberLong(
                    dispatches->borderFillSet, name, value,
                    ParameterSetType())) {
                static_cast<void>(MemberLong(
                    dispatches->borderFillParameter, name, value,
                    ParameterSetType()));
            }
        }
    }
}

// Cell padding and vertical alignment. The four margins are only meaningful
// together, so a set that answered none of them leaves all four at -1 and the
// next candidate set gets a turn.
//
// Where these names come from, exactly. MarginLeft/Right/Top/Bottom are the
// members the padding write path sets (ReferenceLayoutStyleCommands.cpp,
// "ShapeTableCell/MarginLeft" and the other three), so the fallback set below
// reads back what a write puts in. VertAlign is different: it is a
// ParameterSetTable name from the published catalogue with no write-path
// counterpart in this repository, because vertical alignment is written by the
// TableVAlignTop/Center/Bottom *actions* instead. So is the first attempt in
// ReadCellGeometryFormat, CellShape -> Item("Cell") -> Margin*: nothing here
// writes through CellShape. Both are reads whose spelling rests on the
// catalogue, not on a write this repository performs; a set that does not
// answer leaves -1 and the value is reported as unknown.
bool ReadCellBoxFormat(
    IDispatch* const set,
    const dispatch::TypeIdentityToken* const type,
    TableCellFormat* const format) {
    const std::pair<const wchar_t*, long*> margins[] = {
        {L"MarginLeft", &format->marginLeft},
        {L"MarginRight", &format->marginRight},
        {L"MarginTop", &format->marginTop},
        {L"MarginBottom", &format->marginBottom},
    };
    constexpr size_t kCellMarginCount = 4;
    size_t read = 0;
    for (const auto& [name, value] : margins) {
        if (MemberLong(set, name, value, type)) {
            ++read;
        }
    }
    static_cast<void>(MemberLong(
        set, L"VertAlign", &format->verticalAlign, type));
    return read == kCellMarginCount;
}

void ReadCellGeometryFormat(
    IDispatch* const hwp,
    IDispatch* const action,
    IDispatch* const parameterSets,
    CellFormatDispatches* const dispatches,
    TableCellFormat* const format) {
    dispatches->cellShape.Release();
    dispatches->shapeParameter.Release();
    dispatches->shapeSet.Release();
    if (dispatches->cellShape != nullptr ||
        DispatchProperty(
            hwp, L"CellShape", dispatches->cellShape, RootType())) {
        CComPtr<IDispatch> cell;
        CComVariant rawCell;
        if (SUCCEEDED(TypedMethod(
                dispatches->cellShape, CellType(), L"Item",
                {CComVariant(L"Cell")}, &rawCell)) &&
            SUCCEEDED(AsDispatch(rawCell, cell)) &&
            ReadCellBoxFormat(cell, CellType(), format)) {
            return;
        }
        if (ReadCellBoxFormat(
                dispatches->cellShape, CellType(), format)) {
            return;
        }
    }
    // Same members the padding write path sets through TablePropertyDialog.
    CComPtr<IDispatch> tableCell;
    if (LoadActionDefaults(
            action,
            parameterSets,
            L"TablePropertyDialog",
            L"HShapeObject",
            dispatches->shapeParameter,
            dispatches->shapeSet) &&
        (MemberDispatch(
             dispatches->shapeParameter, L"ShapeTableCell", tableCell,
             ParameterSetType()) ||
         MemberDispatch(
             dispatches->shapeSet, L"ShapeTableCell", tableCell,
             ParameterSetType()))) {
        static_cast<void>(ReadCellBoxFormat(
            tableCell, ParameterSetType(), format));
    }
}

void ReadCellTextFormat(
    IDispatch* const action,
    IDispatch* const parameterSets,
    CellFormatDispatches* const dispatches,
    TableCellFormat* const format) {
    if (LoadActionDefaults(
            action,
            parameterSets,
            L"CharShape",
            L"HCharShape",
            dispatches->characterParameter,
            dispatches->characterSet)) {
        static_cast<void>(MemberText(
            dispatches->characterParameter,
            L"FaceNameHangul",
            &format->faceName,
            ParameterSetType()));
        static_cast<void>(MemberLong(
            dispatches->characterParameter,
            L"Height",
            &format->characterHeight,
            ParameterSetType()));
        static_cast<void>(MemberBoolean(
            dispatches->characterParameter, L"Bold", &format->bold,
            ParameterSetType()));
    }
    if (LoadActionDefaults(
            action,
            parameterSets,
            L"ParagraphShape",
            L"HParaShape",
            dispatches->paragraphParameter,
            dispatches->paragraphSet)) {
        static_cast<void>(MemberLong(
            dispatches->paragraphParameter,
            L"AlignType",
            &format->alignment,
            ParameterSetType()));
    }
}

// Whether two sampled cells reported exactly the same appearance. Only then
// may they share one record; a single differing number keeps them apart,
// including the difference between an unread property and a read one.
bool SameAppearance(const TableCellFormat& left, const TableCellFormat& right) {
    for (size_t side = 0; side < kCellBorderSideCount; ++side) {
        if (left.borderType[side] != right.borderType[side] ||
            left.borderWidth[side] != right.borderWidth[side] ||
            left.borderColor[side] != right.borderColor[side]) {
            return false;
        }
    }
    return left.fillColor == right.fillColor &&
        left.fillHatchColor == right.fillHatchColor &&
        left.fillAlpha == right.fillAlpha &&
        left.fillBrush == right.fillBrush &&
        left.marginLeft == right.marginLeft &&
        left.marginRight == right.marginRight &&
        left.marginTop == right.marginTop &&
        left.marginBottom == right.marginBottom &&
        left.verticalAlign == right.verticalAlign &&
        left.alignment == right.alignment &&
        left.faceName == right.faceName &&
        left.characterHeight == right.characterHeight &&
        left.bold == right.bold;
}

}

void ResetTableCallCounters() noexcept {
    gTableCallCounters = {};
}

void ResetTableVirtualSlotCache() noexcept {
    gHParameterSetSlot = kUnresolvedVirtualSlot;
    gHActionSlot = kUnresolvedVirtualSlot;
    gBorderColorLeftSlot = kUnresolvedVirtualSlot;
    gBorderColorRightSlot = kUnresolvedVirtualSlot;
    gBorderColorTopSlot = kUnresolvedVirtualSlot;
    gBorderColorBottomSlot = kUnresolvedVirtualSlot;
}

void ResetTableRangeProbeTuples() noexcept {
    gTableRangeProbeTuples.clear();
}

std::vector<TableRangeProbeTuple> ReadTableRangeProbeTuples() noexcept {
    return gTableRangeProbeTuples;
}

void NoteTableHeadCtrlCall() noexcept {
    ++gTableCallCounters.headCtrl;
}

void NoteTableNextCall() noexcept {
    ++gTableCallCounters.next;
}

void NoteTableDirectVirtualPropertyGet() noexcept {
    ++gTableCallCounters.directVirtualPropertyGets;
}

TableCallCounters ReadTableCallCounters() noexcept {
    return gTableCallCounters;
}

// Which cells answer. Position only, never meaning: the first, early, middle,
// and last rows crossed with first, middle, and last columns. This is a bounded
// representative sample: it reaches header/middle/last-row formatting without
// emitting an unbounded full table. A merged cell answers through its owner and
// duplicates collapse.
//
// One based throughout, because CellTopology::Build fills row and column from
// ParseCellAddress, which returns 1 for "A1". Coordinates here and in
// CellTopologyCell have to count the same way.
std::vector<const TableCellRecord*> SampleCells(
    const std::vector<TableCellRecord>& cells) {
    long rows = 0;
    long columns = 0;
    for (const TableCellRecord& cell : cells) {
        rows = (std::max)(rows, cell.row + cell.rowSpan - 1);
        columns = (std::max)(columns, cell.column + cell.columnSpan - 1);
    }
    std::vector<const TableCellRecord*> sampled;
    if (rows < 1 || columns < 1) {
        return sampled;
    }
    const long middleRow = (rows + 1) / 2;
    const long earlyRow = rows > 1 ? 2 : 1;
    const long middleColumn = (columns + 1) / 2;
    const std::pair<long, long> wanted[] = {
        {1, 1}, {1, middleColumn}, {1, columns},
        {earlyRow, 1}, {earlyRow, middleColumn}, {earlyRow, columns},
        {middleRow, 1}, {middleRow, middleColumn}, {middleRow, columns},
        {rows, 1}, {rows, middleColumn}, {rows, columns},
    };
    for (const auto& [row, column] : wanted) {
        for (const TableCellRecord& cell : cells) {
            if (row < cell.row || row >= cell.row + cell.rowSpan ||
                column < cell.column || column >= cell.column + cell.columnSpan) {
                continue;
            }
            const bool seen = std::any_of(
                sampled.begin(),
                sampled.end(),
                [&cell](const TableCellRecord* const known) {
                    return known->address == cell.address;
                });
            if (!seen) {
                sampled.push_back(&cell);
            }
            break;
        }
        if (sampled.size() >= kCellFormatSampleLimit) {
            break;
        }
    }
    return sampled;
}

bool InspectTableCellsWithAuthoritativeDimensionsImpl(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    const long authoritativeRows,
    const long authoritativeColumns,
    std::vector<TableCellRecord>* const cells,
    std::vector<TableCellFormat>* const formats,
    std::wstring* const error,
    std::wstring* const errorCode,
    const TableDispatchTypeContext* const dispatchTypes) noexcept {
    TableDispatchTypeScope dispatchScope(dispatchTypes);
    try {
        if (hwp == nullptr || cells == nullptr) {
            return Fail(error, L"table inspection arguments are invalid");
        }
        if (errorCode != nullptr) {
            *errorCode = L"TABLE_INSPECTION";
        }
        CComPtr<IDispatch> action;
        CComPtr<IDispatch> info;
        CComPtr<IDispatch> parameterSets;
        if (!DispatchProperty(hwp, L"HAction", action, RootType())) {
            return FailWithCode(
                error,
                errorCode,
                L"TABLE_HACTION",
                L"table HAction property is unavailable");
        }
        if (!DocumentInfo(hwp, info)) {
            return FailWithCode(
                error,
                errorCode,
                L"TABLE_DOCUMENT_INFO",
                L"table document information is unavailable");
        }
        if (formats != nullptr &&
            !DispatchProperty(
                hwp, L"HParameterSet", parameterSets, RootType())) {
            return FailWithCode(
                error,
                errorCode,
                L"TABLE_PARAMETER_SET",
                L"table cell appearance parameter sets are unavailable");
        }
        if (!EnterTable(hwp, action, tableInstanceId)) {
            return FailWithCode(
                error,
                errorCode,
                L"TABLE_EDIT_CONTEXT",
                L"table edit context is unavailable for the target instance");
        }
        if (!RunTableNavigationAction(
                hwp,
                action,
                L"TableColEnd",
                tableInstanceId)) {
            return FailWithCode(
                error,
                errorCode,
                L"TABLE_COLUMN_END",
                L"table column-end navigation did not preserve the target context");
        }
        if (!RunTableNavigationAction(
                hwp,
                action,
                L"TableColPageDown",
                tableInstanceId)) {
            return FailWithCode(
                error,
                errorCode,
                L"TABLE_PAGE_DOWN",
                L"table page-down navigation did not preserve the target context");
        }
        LONG lastList = 0;
        if (!GetPositionList(hwp, &lastList) ||
            !RunTableNavigationAction(
                hwp,
                action,
                L"TableColBegin",
                tableInstanceId) ||
            !RunTableNavigationAction(
                hwp,
                action,
                L"TableColPageUp",
                tableInstanceId)) {
            return Fail(error, L"table list range could not be read");
        }
        LONG firstList = 0;
        if (!GetPositionList(hwp, &firstList)) {
            return Fail(error, L"table list range is invalid");
        }
        lastList = ExtendTableListEnd(hwp, tableInstanceId, lastList);
        if (lastList < firstList || lastList - firstList > 200'000) {
            return Fail(error, L"table list range is invalid");
        }
        cells->clear();
        if (formats != nullptr) formats->clear();
        std::set<std::wstring> seen;
        CellFormatDispatches formatDispatches;
        for (LONG listId = firstList; listId <= lastList; ++listId) {
            if (!SetPosition(hwp, listId) || !ParentMatches(hwp, tableInstanceId)) {
                continue;
            }
            const std::wstring address = CellAddress(hwp);
            if (address.empty() || !seen.insert(address).second) {
                continue;
            }
            std::wstring text;
            LONG startPage = 0;
            LONG endPage = 0;
            LONG width = -1;
            LONG height = -1;
            if (!CurrentPage(info, &startPage) ||
                !ReadCurrentCellSize(hwp, &width, &height) ||
                !ReadCurrentListText(hwp, &text)) {
                return Fail(error, L"table cell text or page range could not be read");
            }
            if (formats != nullptr) {
                TableCellFormat format;
                format.tableInstanceId = tableInstanceId;
                format.addresses.push_back(address);
                ReadCellFillFormat(
                    action, parameterSets, &formatDispatches, &format);
                ReadCellBorderFormat(
                    action, parameterSets, &formatDispatches, &format);
                ReadCellGeometryFormat(
                    hwp, action, parameterSets, &formatDispatches, &format);
                ReadCellTextFormat(
                    action, parameterSets, &formatDispatches, &format);
                const auto same = std::find_if(
                    formats->begin(), formats->end(),
                    [&format](const TableCellFormat& known) {
                        return SameAppearance(known, format);
                    });
                if (same == formats->end()) {
                    formats->push_back(std::move(format));
                } else {
                    same->addresses.push_back(address);
                }
                ++gTableCallCounters.appearanceVisits;
            }
            if (!RunTableNavigationAction(
                    hwp, action, L"MoveListEnd", tableInstanceId) ||
                !CurrentPage(info, &endPage)) {
                return Fail(error, L"table cell text or page range could not be read");
            }
            cells->push_back(TableCellRecord{
                tableInstanceId,
                address,
                listId,
                1,
                1,
                (std::min)(startPage, endPage),
                (std::max)(startPage, endPage),
                std::move(text),
                width,
                height,
            });
        }
        if (cells->empty()) {
            return Fail(error, L"table contains no addressable cells");
        }
        if (!InferCellSpans(
                hwp, action, tableInstanceId,
                authoritativeRows, authoritativeColumns, cells, error)) {
            return false;
        }
        CellTopology topology;
        if (!topology.Build(*cells, error)) {
            return false;
        }
        *cells = topology.Cells();
        return true;
    } catch (...) {
        return Fail(error, L"table inspection failed unexpectedly");
    }
}

bool InspectTableCellsWithAuthoritativeDimensions(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    const long authoritativeRows,
    const long authoritativeColumns,
    std::vector<TableCellRecord>* const cells,
    std::wstring* const error,
    std::wstring* const errorCode) noexcept {
    return InspectTableCellsWithAuthoritativeDimensionsImpl(
        hwp, tableInstanceId, authoritativeRows, authoritativeColumns,
        cells, nullptr, error, errorCode, nullptr);
}

bool InspectTableCellsAndFormatsWithAuthoritativeDimensions(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    const long authoritativeRows,
    const long authoritativeColumns,
    std::vector<TableCellRecord>* const cells,
    std::vector<TableCellFormat>* const formats,
    std::wstring* const error,
    std::wstring* const errorCode,
    const TableDispatchTypeContext* const dispatchTypes) noexcept {
    if (formats == nullptr) return false;
    return InspectTableCellsWithAuthoritativeDimensionsImpl(
        hwp, tableInstanceId, authoritativeRows, authoritativeColumns,
        cells, formats, error, errorCode, dispatchTypes);
}

bool InspectTableCells(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    std::vector<TableCellRecord>* const cells,
    std::wstring* const error,
    std::wstring* const errorCode) noexcept {
    return InspectTableCellsWithAuthoritativeDimensions(
        hwp, tableInstanceId, 0, 0, cells, error, errorCode);
}

bool InspectTableTopology(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    CellTopology* const topology,
    std::wstring* const error) noexcept {
    if (topology == nullptr) {
        return Fail(error, L"table topology output is invalid");
    }
    std::vector<TableCellRecord> cells;
    return InspectTableCells(hwp, tableInstanceId, &cells, error) &&
        topology->Build(std::move(cells), error);
}

namespace {

std::vector<TableCellFormat> ReadTableCellFormatsImpl(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    const std::vector<TableCellRecord>& cells,
    const bool inspectEveryCell,
    std::wstring* const error) noexcept {
    std::vector<TableCellFormat> formats;
    // Says why nothing came back. An empty result used to be indistinguishable
    // from a table that genuinely carries no readable appearance, so a whole
    // dead read path looked exactly like a plain answer.
    const auto explain = [error](const wchar_t* const message) noexcept {
        if (error == nullptr) {
            return;
        }
        try {
            *error = message;
        } catch (...) {
        }
    };
    if (error != nullptr) {
        error->clear();
    }
    try {
        if (hwp == nullptr || cells.empty()) {
            explain(L"table cell appearance was asked for without an inspected table");
            return formats;
        }
        CComPtr<IDispatch> action;
        CComPtr<IDispatch> parameterSets;
        if (!DispatchProperty(hwp, L"HAction", action, RootType()) ||
            !DispatchProperty(
                hwp, L"HParameterSet", parameterSets, RootType())) {
            explain(
                L"table cell appearance needs HAction and HParameterSet, and this "
                L"build did not answer both");
            return formats;
        }
        std::vector<const TableCellRecord*> selected;
        if (inspectEveryCell) {
            selected.reserve(cells.size());
            for (const TableCellRecord& cell : cells) {
                selected.push_back(&cell);
            }
        } else {
            selected = SampleCells(cells);
        }
        if (selected.empty()) {
            explain(
                L"table cell appearance found no position in the inspected "
                L"cells of this table");
            return formats;
        }
        size_t reached = 0;
        CellFormatDispatches formatDispatches;
        for (const TableCellRecord* const cell : selected) {
            // A cell that cannot be reached, or that turns out to belong to a
            // different table, is skipped rather than reported with guesses.
            if (!SetPosition(hwp, cell->listId) ||
                !ParentMatches(hwp, tableInstanceId)) {
                continue;
            }
            ++reached;
            TableCellFormat format;
            format.tableInstanceId = tableInstanceId;
            format.addresses.push_back(cell->address);
            ReadCellFillFormat(
                action, parameterSets, &formatDispatches, &format);
            ReadCellBorderFormat(
                action, parameterSets, &formatDispatches, &format);
            ReadCellGeometryFormat(
                hwp, action, parameterSets, &formatDispatches, &format);
            ReadCellTextFormat(
                action, parameterSets, &formatDispatches, &format);
            const auto same = std::find_if(
                formats.begin(),
                formats.end(),
                [&format](const TableCellFormat& known) {
                    return SameAppearance(known, format);
                });
            if (same == formats.end()) {
                formats.push_back(std::move(format));
            } else {
                same->addresses.push_back(cell->address);
            }
        }
        if (reached == 0) {
            explain(
                L"table cell appearance could not put the caret in any selected cell "
                L"of this table");
        }
    } catch (...) {
        try {
            formats.clear();
        } catch (...) {
        }
        explain(L"table cell appearance inspection failed unexpectedly");
    }
    return formats;
}

}

std::vector<TableCellFormat> ReadTableCellFormats(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    const std::vector<TableCellRecord>& cells,
    std::wstring* const error) noexcept {
    return ReadTableCellFormatsImpl(
        hwp,
        tableInstanceId,
        cells,
        false,
        error);
}

std::vector<TableCellFormat> ReadAllTableCellFormats(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    const std::vector<TableCellRecord>& cells,
    std::wstring* const error) noexcept {
    return ReadTableCellFormatsImpl(
        hwp,
        tableInstanceId,
        cells,
        true,
        error);
}

}

#include "BatchExecutor.h"

#include "DispatchInvoke.h"
#include "TableInspection.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <cwctype>
#include <filesystem>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace hancom::batch {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;
using hancom::dispatch::PropertyPut;

struct ResolvedOperation {
    const CellOperation* operation = nullptr;
    std::wstring controlId;
    LONG listId = 0;
};

struct Position {
    LONG list = 0;
    LONG paragraph = 0;
    LONG character = 0;
};

struct Selection {
    bool selected = false;
    LONG mode = 0;
    Position start;
    Position end;
    std::wstring controlType;
    std::wstring controlInstance;
    std::vector<std::wstring> cellAddresses;
    std::wstring cellAddressError;
};

constexpr LONG kSelectionModeMask = 0x0F;
constexpr LONG kSelectionNone = 0;
constexpr LONG kSelectionText = 1;
constexpr LONG kSelectionCells = 3;
constexpr LONG kSelectionControl = 4;
constexpr LONG kSelectionStrict = 0x10;

bool SetError(
    ExecutionResult* const result,
    std::wstring code,
    std::wstring address,
    std::wstring message) {
    result->error.code = std::move(code);
    result->error.address = std::move(address);
    result->error.message = std::move(message);
    return false;
}

std::wstring HResultText(const wchar_t* const operation, const HRESULT status) {
    std::wostringstream text;
    text << operation << L" failed (HRESULT 0x" << std::hex
         << static_cast<unsigned long>(status) << L')';
    return text.str();
}

bool GetDispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value,
    ExecutionResult* const result,
    const std::wstring& address = L"") {
    CComVariant property;
    HRESULT status = PropertyGet(object, name, &property);
    if (SUCCEEDED(status)) {
        status = AsDispatch(property, value);
    }
    if (FAILED(status)) {
        return SetError(result, L"COM_PROPERTY", address, HResultText(name, status));
    }
    return true;
}

bool CallBooleanMethod(
    IDispatch* const object,
    const wchar_t* const name,
    const std::vector<CComVariant>& arguments,
    bool* const returned,
    ExecutionResult* const result,
    const std::wstring& address = L"") {
    CComVariant value;
    HRESULT status = Method(object, name, arguments, &value);
    if (FAILED(status)) {
        return SetError(result, L"COM_METHOD", address, HResultText(name, status));
    }
    if (value.vt == VT_EMPTY) {
        *returned = true;
        return true;
    }
    status = AsBool(value, returned);
    if (FAILED(status)) {
        return SetError(result, L"COM_RESULT", address, HResultText(name, status));
    }
    return true;
}

bool RunAction(
    IDispatch* const action,
    const wchar_t* const actionName,
    ExecutionResult* const result,
    const std::wstring& address = L"") {
    bool returned = false;
    if (!CallBooleanMethod(
            action,
            L"Run",
            {CComVariant(actionName)},
            &returned,
            result,
            address)) {
        return false;
    }
    if (!returned) {
        return SetError(
            result,
            L"ACTION_FAILED",
            address,
            std::wstring(actionName) + L" returned false");
    }
    return true;
}

bool RunVerifiedNavigationAction(
    IDispatch* const action,
    const wchar_t* const actionName,
    ExecutionResult* const result,
    const std::wstring& address = L"") {
    bool returned = false;
    return CallBooleanMethod(
        action,
        L"Run",
        {CComVariant(actionName)},
        &returned,
        result,
        address);
}

bool GetPosition(
    IDispatch* const hwp,
    LONG* const listId,
    LONG* const paragraph,
    LONG* const position,
    ExecutionResult* const result) {
    CComVariant listArgument;
    listArgument.vt = VT_I4 | VT_BYREF;
    listArgument.plVal = listId;
    CComVariant paragraphArgument;
    paragraphArgument.vt = VT_I4 | VT_BYREF;
    paragraphArgument.plVal = paragraph;
    CComVariant positionArgument;
    positionArgument.vt = VT_I4 | VT_BYREF;
    positionArgument.plVal = position;
    const HRESULT status = Method(
        hwp,
        L"GetPos",
        {listArgument, paragraphArgument, positionArgument},
        nullptr);
    if (FAILED(status)) {
        return SetError(result, L"COM_METHOD", L"", HResultText(L"GetPos", status));
    }
    return true;
}

bool SetExactPosition(IDispatch* const hwp, const Position& position) {
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

bool GetSelection(
    IDispatch* const hwp,
    Selection* const selection,
    ExecutionResult* const result) {
    CComVariant modeValue;
    HRESULT status = PropertyGet(hwp, L"SelectionMode", &modeValue);
    if (SUCCEEDED(status)) {
        status = AsLong(modeValue, &selection->mode);
    }
    if (FAILED(status)) {
        return SetError(result, L"STATE_CAPTURE", L"", HResultText(L"SelectionMode", status));
    }
    CComVariant startValue;
    CComVariant endValue;
    status = Method(hwp, L"CreateSet", {CComVariant(L"ListParaPos")}, &startValue);
    CComPtr<IDispatch> start;
    if (SUCCEEDED(status)) {
        status = AsDispatch(startValue, start);
    }
    if (FAILED(status)) {
        return SetError(result, L"STATE_CAPTURE", L"", HResultText(L"CreateSet", status));
    }
    status = Method(hwp, L"CreateSet", {CComVariant(L"ListParaPos")}, &endValue);
    CComPtr<IDispatch> end;
    if (SUCCEEDED(status)) {
        status = AsDispatch(endValue, end);
    }
    if (FAILED(status)) {
        return SetError(result, L"STATE_CAPTURE", L"", HResultText(L"CreateSet", status));
    }

    CComVariant selectedValue;
    status = Method(
        hwp,
        L"GetSelectedPosBySet",
        {CComVariant(start), CComVariant(end)},
        &selectedValue);
    if (SUCCEEDED(status)) {
        status = AsBool(selectedValue, &selection->selected);
    }
    if (FAILED(status)) {
        return SetError(
            result,
            L"STATE_CAPTURE",
            L"",
            HResultText(L"GetSelectedPosBySet", status));
    }

    const auto readItem = [result](
                              IDispatch* const set,
                              const wchar_t* const name,
                              LONG* const value) {
        CComVariant raw;
        HRESULT itemStatus = Method(set, L"Item", {CComVariant(name)}, &raw);
        if (SUCCEEDED(itemStatus)) {
            itemStatus = AsLong(raw, value);
        }
        if (FAILED(itemStatus)) {
            return SetError(result, L"STATE_CAPTURE", L"", HResultText(name, itemStatus));
        }
        return true;
    };
    if (!readItem(start, L"List", &selection->start.list) ||
        !readItem(start, L"Para", &selection->start.paragraph) ||
        !readItem(start, L"Pos", &selection->start.character) ||
        !readItem(end, L"List", &selection->end.list) ||
        !readItem(end, L"Para", &selection->end.paragraph) ||
        !readItem(end, L"Pos", &selection->end.character)) {
        return false;
    }
    const LONG baseMode = selection->mode & kSelectionModeMask;
    if (baseMode == kSelectionCells || baseMode == kSelectionControl) {
        CComVariant controlValue;
        CComPtr<IDispatch> control;
        const wchar_t* const property = baseMode == kSelectionControl
            ? L"CurSelectedCtrl"
            : L"ParentCtrl";
        status = PropertyGet(hwp, property, &controlValue);
        if (SUCCEEDED(status)) {
            status = AsDispatch(controlValue, control);
        }
        CComVariant typeValue;
        if (SUCCEEDED(status)) {
            status = PropertyGet(control, L"CtrlID", &typeValue);
        }
        if (SUCCEEDED(status)) {
            status = AsString(typeValue, &selection->controlType);
        }
        CComVariant instanceValue;
        if (SUCCEEDED(status)) {
            status = Method(control, L"GetCtrlInstID", {}, &instanceValue);
        }
        if (SUCCEEDED(status)) {
            status = AsString(instanceValue, &selection->controlInstance);
        }
        if (FAILED(status)) {
            return SetError(result, L"STATE_CAPTURE", L"", HResultText(property, status));
        }
    }
    if (baseMode == kSelectionCells && (selection->mode & kSelectionStrict) != 0) {
        static_cast<void>(hancom::inspection::ReadSelectedCellAddresses(
            hwp,
            &selection->cellAddresses,
            &selection->cellAddressError));
    }
    return true;
}

bool CanRestoreSelection(const Selection& selection) noexcept {
    const LONG baseMode = selection.mode & kSelectionModeMask;
    if (baseMode == kSelectionNone) {
        return true;
    }
    if (baseMode == kSelectionText) {
        return selection.selected && selection.start.list == selection.end.list;
    }
    if (baseMode == kSelectionCells) {
        return (selection.selected || !selection.cellAddresses.empty()) &&
            selection.controlType == L"tbl" &&
            !selection.controlInstance.empty();
    }
    if (baseMode == kSelectionControl) {
        return !selection.controlInstance.empty();
    }
    return false;
}

bool RestoreSelection(
    IDispatch* const hwp,
    const Position& cursor,
    const Selection& selection);

bool SetPosition(
    IDispatch* const hwp,
    const LONG listId,
    ExecutionResult* const result,
    const std::wstring& address) {
    bool returned = false;
    if (!CallBooleanMethod(
            hwp,
            L"SetPos",
            {CComVariant(listId), CComVariant(0L), CComVariant(0L)},
            &returned,
            result,
            address)) {
        return false;
    }
    if (!returned) {
        return SetError(result, L"POSITION_FAILED", address, L"SetPos returned false");
    }
    return true;
}

bool GetControlInstanceId(
    IDispatch* const control,
    std::wstring* const controlId,
    ExecutionResult* const result,
    const std::wstring& address = L"") {
    CComVariant value;
    HRESULT status = Method(control, L"GetCtrlInstID", {}, &value);
    if (SUCCEEDED(status)) {
        status = AsString(value, controlId);
    }
    if (FAILED(status)) {
        return SetError(result, L"CONTROL_ID", address, HResultText(L"GetCtrlInstID", status));
    }
    return true;
}

bool TryDispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value) {
    CComVariant property;
    return SUCCEEDED(PropertyGet(object, name, &property)) &&
        SUCCEEDED(AsDispatch(property, value));
}

bool TryStringProperty(
    IDispatch* const object,
    const wchar_t* const name,
    std::wstring* const value) {
    CComVariant property;
    return SUCCEEDED(PropertyGet(object, name, &property)) &&
        SUCCEEDED(AsString(property, value));
}

bool GetControlAnchorList(
    IDispatch* const control,
    LONG* const listId,
    ExecutionResult* const result) {
    CComVariant raw;
    CComPtr<IDispatch> anchor;
    HRESULT status = Method(control, L"GetAnchorPos", {CComVariant(0L)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, anchor);
    }
    if (FAILED(status)) {
        return SetError(result, L"PICTURE_SCAN", L"", HResultText(L"GetAnchorPos", status));
    }
    status = Method(anchor, L"Item", {CComVariant(L"List")}, &raw);
    if (SUCCEEDED(status)) {
        status = AsLong(raw, listId);
    }
    if (FAILED(status)) {
        return SetError(result, L"PICTURE_SCAN", L"", HResultText(L"Item(List)", status));
    }
    return true;
}

bool IsPictureControl(
    IDispatch* const control,
    const std::wstring& type,
    bool* const isPicture,
    ExecutionResult* const result) {
    *isPicture = type == L"$pic";
    if (*isPicture || type != L"gso") {
        return true;
    }
    CComPtr<IDispatch> properties;
    if (!TryDispatchProperty(control, L"Properties", properties)) {
        return SetError(
            result,
            L"PICTURE_SCAN",
            L"",
            L"graphic object properties are unavailable");
    }
    CComVariant imageAttributes;
    const HRESULT status = Method(
        properties,
        L"Item",
        {CComVariant(L"ShapeDrawImageAttr")},
        &imageAttributes);
    if (FAILED(status)) {
        return SetError(
            result,
            L"PICTURE_SCAN",
            L"",
            HResultText(L"ShapeDrawImageAttr", status));
    }
    if (imageAttributes.vt == VT_EMPTY || imageAttributes.vt == VT_NULL) {
        return true;
    }
    CComPtr<IDispatch> imageAttributeSet;
    const HRESULT dispatchStatus = AsDispatch(imageAttributes, imageAttributeSet);
    if (FAILED(dispatchStatus)) {
        return SetError(
            result,
            L"PICTURE_SCAN",
            L"",
            HResultText(L"ShapeDrawImageAttr", dispatchStatus));
    }
    *isPicture = imageAttributeSet != nullptr;
    return true;
}

bool CollectExistingPictures(
    IDispatch* const hwp,
    const std::set<LONG>& targetLists,
    std::map<LONG, std::vector<std::wstring>>* const pictures,
    ExecutionResult* const result) {
    if (targetLists.empty()) {
        return true;
    }
    CComPtr<IDispatch> control;
    if (!TryDispatchProperty(hwp, L"HeadCtrl", control)) {
        return SetError(result, L"PICTURE_SCAN", L"", L"document control list is unavailable");
    }
    size_t visited = 0;
    while (control != nullptr && visited < 20'000) {
        ++visited;
        std::wstring type;
        if (TryStringProperty(control, L"CtrlID", &type) &&
            (type == L"gso" || type == L"$pic")) {
            LONG listId = 0;
            if (!GetControlAnchorList(control, &listId, result)) {
                return false;
            }
            if (targetLists.find(listId) != targetLists.end()) {
                bool isPicture = false;
                if (!IsPictureControl(control, type, &isPicture, result)) {
                    return false;
                }
                if (isPicture) {
                    std::wstring instanceId;
                    if (!GetControlInstanceId(control, &instanceId, result) || instanceId.empty()) {
                        return SetError(
                            result,
                            L"PICTURE_SCAN",
                            L"",
                            L"picture control identity is unavailable");
                    }
                    (*pictures)[listId].push_back(std::move(instanceId));
                }
            }
        }
        CComPtr<IDispatch> next;
        if (!TryDispatchProperty(control, L"Next", next)) {
            control.Release();
            break;
        }
        control = next;
    }
    if (control != nullptr) {
        return SetError(
            result,
            L"PICTURE_SCAN",
            L"",
            L"document control list exceeds the 20000-control safety limit");
    }
    return true;
}

bool DeleteExactControl(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& instanceId,
    const std::wstring& address,
    ExecutionResult* const result) {
    bool selected = false;
    if (!CallBooleanMethod(
            hwp,
            L"SelectCtrl",
            {CComVariant(instanceId.c_str()), CComVariant(1L)},
            &selected,
            result,
            address)) {
        return false;
    }
    static_cast<void>(selected);
    CComPtr<IDispatch> selectedControl;
    if (!GetDispatchProperty(hwp, L"CurSelectedCtrl", selectedControl, result, address)) {
        return false;
    }
    std::wstring selectedId;
    if (!GetControlInstanceId(selectedControl, &selectedId, result, address) ||
        selectedId != instanceId) {
        return SetError(
            result,
            L"WRONG_CONTROL",
            address,
            L"selected picture identity does not match the replacement target");
    }
    return RunAction(action, L"Delete", result, address);
}

bool GetParentControlId(
    IDispatch* const hwp,
    std::wstring* const controlId,
    ExecutionResult* const result,
    const std::wstring& address = L"") {
    CComPtr<IDispatch> parent;
    if (!GetDispatchProperty(hwp, L"ParentCtrl", parent, result, address)) {
        return false;
    }
    return GetControlInstanceId(parent, controlId, result, address);
}

bool GetCellAddress(
    IDispatch* const hwp,
    std::wstring* const address,
    ExecutionResult* const result) {
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
        return SetError(result, L"CELL_ADDRESS", L"", HResultText(L"KeyIndicator", status));
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
        return SetError(result, L"CELL_ADDRESS", L"", L"KeyIndicator is not a table cell address");
    }
    *address = indicator.substr(opening + 1, closing - opening - 1);
    std::transform(address->begin(), address->end(), address->begin(), towupper);
    return true;
}

bool RestoreTextSelection(IDispatch* const hwp, const Selection& selection) {
    if (!SetExactPosition(hwp, Position{selection.start.list, 0, 0})) {
        return false;
    }
    CComVariant returned;
    if (FAILED(Method(
            hwp,
            L"SelectText",
            {
                CComVariant(selection.start.paragraph),
                CComVariant(selection.start.character),
                CComVariant(selection.end.paragraph),
                CComVariant(selection.end.character),
            },
            &returned))) {
        return false;
    }
    bool selected = false;
    CComVariant modeValue;
    LONG mode = 0;
    return SUCCEEDED(AsBool(returned, &selected)) && selected &&
        SUCCEEDED(PropertyGet(hwp, L"SelectionMode", &modeValue)) &&
        SUCCEEDED(AsLong(modeValue, &mode)) &&
        (mode & kSelectionModeMask) == kSelectionText;
}

bool RestoreCellSelection(IDispatch* const hwp, const Selection& selection) {
    hancom::inspection::CellTopology topology;
    std::wstring error;
    if (!hancom::inspection::InspectTableTopology(
            hwp,
            selection.controlInstance,
            &topology,
            &error)) {
        return false;
    }
    std::wstring first;
    std::wstring last;
    std::vector<hancom::inspection::CellTopologyStep> path;
    std::vector<std::wstring> region;
    const bool planned = selection.cellAddresses.empty()
        ? topology.PlanRectangularSelection(
            selection.start.list,
            selection.end.list,
            &first,
            &last,
            &path,
            &region,
            &error)
        : topology.PlanRectangularSelection(
            selection.cellAddresses,
            &first,
            &last,
            &path,
            &region,
            &error);
    if (!planned) {
        return false;
    }
    const hancom::inspection::CellTopologyCell* const firstCell = topology.Find(first);
    CComPtr<IDispatch> action;
    ExecutionResult local;
    if (firstCell == nullptr ||
        !GetDispatchProperty(hwp, L"HAction", action, &local) ||
        !SetExactPosition(hwp, Position{firstCell->listId, 0, 0}) ||
        !RunAction(action, L"TableCellBlock", &local)) {
        return false;
    }
    if (region.size() > 1) {
        if (!RunAction(action, L"TableCellBlockExtend", &local)) {
            return false;
        }
        for (const hancom::inspection::CellTopologyStep& step : path) {
            const wchar_t* const actionName =
                step.direction == hancom::inspection::CellDirection::Right
                ? L"TableRightCell"
                : L"TableLowerCell";
            std::wstring current;
            if (!RunAction(action, actionName, &local) ||
                !GetCellAddress(hwp, &current, &local) ||
                current != step.destination) {
                return false;
            }
        }
    }
    CComVariant modeValue;
    LONG mode = 0;
    return SUCCEEDED(PropertyGet(hwp, L"SelectionMode", &modeValue)) &&
        SUCCEEDED(AsLong(modeValue, &mode)) &&
        (mode & kSelectionModeMask) == kSelectionCells;
}

bool RestoreControlSelection(IDispatch* const hwp, const Selection& selection) {
    if (selection.controlType == L"tbl" &&
        hancom::inspection::SelectTableControl(hwp, selection.controlInstance)) {
        return true;
    }
    CComVariant ignored;
    static_cast<void>(Method(
        hwp,
        L"SelectCtrl",
        {CComVariant(selection.controlInstance.c_str()), CComVariant(1L)},
        &ignored));
    CComPtr<IDispatch> selected;
    std::wstring type;
    std::wstring instance;
    ExecutionResult local;
    return TryDispatchProperty(hwp, L"CurSelectedCtrl", selected) &&
        TryStringProperty(selected, L"CtrlID", &type) &&
        GetControlInstanceId(selected, &instance, &local) &&
        type == selection.controlType && instance == selection.controlInstance;
}

bool RestoreSelection(
    IDispatch* const hwp,
    const Position& cursor,
    const Selection& selection) {
    const LONG baseMode = selection.mode & kSelectionModeMask;
    if (baseMode == kSelectionNone) {
        return SetExactPosition(hwp, cursor);
    }
    if (baseMode == kSelectionText) {
        return RestoreTextSelection(hwp, selection);
    }
    if (baseMode == kSelectionCells) {
        return RestoreCellSelection(hwp, selection);
    }
    if (baseMode == kSelectionControl) {
        return RestoreControlSelection(hwp, selection);
    }
    return false;
}

bool VerifyCellContext(
    IDispatch* const hwp,
    const std::wstring& controlId,
    const std::wstring& expectedAddress,
    ExecutionResult* const result) {
    CComPtr<IDispatch> cellShape;
    if (!GetDispatchProperty(hwp, L"CellShape", cellShape, result, expectedAddress)) {
        return false;
    }
    std::wstring parentId;
    if (!GetParentControlId(hwp, &parentId, result, expectedAddress)) {
        return false;
    }
    if (parentId != controlId) {
        return SetError(result, L"WRONG_TABLE", expectedAddress, L"cell belongs to another table");
    }
    std::wstring currentAddress;
    if (!GetCellAddress(hwp, &currentAddress, result)) {
        result->error.address = expectedAddress;
        return false;
    }
    std::wstring normalizedExpected = expectedAddress;
    std::transform(
        normalizedExpected.begin(),
        normalizedExpected.end(),
        normalizedExpected.begin(),
        towupper);
    if (currentAddress != normalizedExpected) {
        return SetError(result, L"WRONG_CELL", expectedAddress, L"cursor is in a different cell");
    }
    return true;
}

bool CurrentContextMatchesTable(
    IDispatch* const hwp,
    const std::wstring& controlId,
    ExecutionResult* const result) {
    CComVariant cellShapeValue;
    if (FAILED(PropertyGet(hwp, L"CellShape", &cellShapeValue))) {
        return false;
    }
    CComPtr<IDispatch> cellShape;
    if (FAILED(AsDispatch(cellShapeValue, cellShape))) {
        return false;
    }
    ExecutionResult local;
    std::wstring parentId;
    if (!GetParentControlId(hwp, &parentId, &local)) {
        return false;
    }
    static_cast<void>(result);
    return parentId == controlId;
}

bool EnterTable(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& controlId,
    ExecutionResult* const result) {
    if (CurrentContextMatchesTable(hwp, controlId, result)) {
        return true;
    }

    bool selected = false;
    if (!CallBooleanMethod(
            hwp,
            L"SelectCtrl",
            {CComVariant(controlId.c_str()), CComVariant(1L)},
            &selected,
            result)) {
        return false;
    }
    CComPtr<IDispatch> selectedControl;
    if (!GetDispatchProperty(hwp, L"CurSelectedCtrl", selectedControl, result)) {
        return false;
    }
    std::wstring selectedId;
    if (!GetControlInstanceId(selectedControl, &selectedId, result) || selectedId != controlId) {
        return SetError(result, L"WRONG_TABLE", L"", L"SelectCtrl did not select the requested table");
    }
    static_cast<void>(selected);
    if (!RunAction(action, L"ShapeObjTextBoxEdit", result)) {
        return false;
    }
    if (!CurrentContextMatchesTable(hwp, controlId, result)) {
        return SetError(result, L"WRONG_TABLE", L"", L"could not enter the requested table");
    }
    return true;
}

bool BuildCellMap(
    IDispatch* const hwp,
    IDispatch* const action,
    const TableBatch& table,
    std::map<std::wstring, LONG>* const addresses,
    ExecutionResult* const result) {
    if (!RunVerifiedNavigationAction(action, L"TableColEnd", result) ||
        !RunVerifiedNavigationAction(action, L"TableColPageDown", result)) {
        return false;
    }
    LONG endList = 0;
    LONG paragraph = 0;
    LONG position = 0;
    if (!GetPosition(hwp, &endList, &paragraph, &position, result)) {
        return false;
    }
    if (!RunVerifiedNavigationAction(action, L"TableColBegin", result) ||
        !RunVerifiedNavigationAction(action, L"TableColPageUp", result)) {
        return false;
    }
    LONG firstList = 0;
    if (!GetPosition(hwp, &firstList, &paragraph, &position, result)) {
        return false;
    }
    if (endList < firstList || endList - firstList > 200'000) {
        return SetError(result, L"TABLE_RANGE", L"", L"table cell list range is invalid");
    }

    std::map<std::wstring, bool> requested;
    for (const CellOperation& operation : table.operations) {
        std::wstring address = operation.address;
        std::transform(address.begin(), address.end(), address.begin(), towupper);
        requested.emplace(address, false);
    }

    for (LONG listId = firstList; listId <= endList; ++listId) {
        bool positioned = false;
        if (!CallBooleanMethod(
                hwp,
                L"SetPos",
                {CComVariant(listId), CComVariant(0L), CComVariant(0L)},
                &positioned,
                result)) {
            return false;
        }
        if (!positioned) {
            continue;
        }
        ExecutionResult local;
        std::wstring parentId;
        if (!GetParentControlId(hwp, &parentId, &local) || parentId != table.controlId) {
            continue;
        }
        CComVariant cellShapeValue;
        CComPtr<IDispatch> cellShape;
        if (FAILED(PropertyGet(hwp, L"CellShape", &cellShapeValue)) ||
            FAILED(AsDispatch(cellShapeValue, cellShape))) {
            continue;
        }
        std::wstring address;
        local = ExecutionResult{};
        if (!GetCellAddress(hwp, &address, &local)) {
            continue;
        }
        const auto target = requested.find(address);
        if (target != requested.end() && !target->second) {
            addresses->emplace(address, listId);
            target->second = true;
        }
    }
    for (const auto& [address, found] : requested) {
        if (!found) {
            return SetError(result, L"CELL_NOT_FOUND", address, L"cell address was not found in the exact table");
        }
    }
    return true;
}

bool ReadSelectedText(
    IDispatch* const hwp,
    const std::wstring& address,
    std::wstring* const text,
    ExecutionResult* const result) {
    CComVariant value;
    HRESULT status = Method(
        hwp,
        L"GetTextFile",
        {CComVariant(L"UNICODE"), CComVariant(L"saveblock:true")},
        &value);
    if (FAILED(status)) {
        return SetError(result, L"READ_CELL", address, HResultText(L"GetTextFile", status));
    }
    if (value.vt == VT_EMPTY || value.vt == VT_NULL) {
        text->clear();
        return true;
    }
    status = AsString(value, text);
    if (FAILED(status)) {
        return SetError(result, L"READ_CELL", address, HResultText(L"GetTextFile", status));
    }
    return true;
}

bool SelectResolvedCell(
    IDispatch* const hwp,
    IDispatch* const action,
    const ResolvedOperation& resolved,
    ExecutionResult* const result) {
    const CellOperation& operation = *resolved.operation;
    return SetPosition(hwp, resolved.listId, result, operation.address) &&
        VerifyCellContext(hwp, resolved.controlId, operation.address, result) &&
        RunAction(action, L"SelectAll", result, operation.address);
}

bool VerifyExpectedText(
    IDispatch* const hwp,
    IDispatch* const action,
    const ResolvedOperation& resolved,
    ExecutionResult* const result) {
    const CellOperation& operation = *resolved.operation;
    if (!SelectResolvedCell(hwp, action, resolved, result)) {
        return false;
    }
    std::wstring currentText;
    if (!ReadSelectedText(hwp, operation.address, &currentText, result)) {
        return false;
    }
    if (currentText != operation.expectedText) {
        return SetError(
            result,
            L"STALE_CELL",
            operation.address,
            L"expected text mismatch");
    }
    return true;
}

bool ValidateImagePath(
    const CellOperation& operation,
    ExecutionResult* const result) {
    const std::filesystem::path path(operation.value);
    std::error_code error;
    if (!path.is_absolute() || !std::filesystem::is_regular_file(path, error) || error) {
        return SetError(
            result,
            L"IMAGE_PATH",
            operation.address,
            L"image path is not an existing absolute file");
    }
    return true;
}

bool ValidateDocumentIdentity(
    IDispatch* const hwp,
    const Request& request,
    ExecutionResult* const result) {
    CComPtr<IDispatch> documents;
    CComPtr<IDispatch> activeDocument;
    if (!GetDispatchProperty(hwp, L"XHwpDocuments", documents, result) ||
        !GetDispatchProperty(documents, L"Active_XHwpDocument", activeDocument, result)) {
        return false;
    }

    CComVariant documentIdValue;
    HRESULT status = PropertyGet(activeDocument, L"DocumentID", &documentIdValue);
    LONG documentId = -1;
    if (SUCCEEDED(status)) {
        status = AsLong(documentIdValue, &documentId);
    }
    if (FAILED(status)) {
        return SetError(result, L"DOCUMENT_ID", L"", HResultText(L"DocumentID", status));
    }

    CComVariant fullNameValue;
    status = PropertyGet(activeDocument, L"FullName", &fullNameValue);
    std::wstring fullName;
    if (SUCCEEDED(status)) {
        status = AsString(fullNameValue, &fullName);
    }
    if (FAILED(status)) {
        return SetError(result, L"DOCUMENT_PATH", L"", HResultText(L"FullName", status));
    }
    auto normalize = [](std::wstring value) {
        std::replace(value.begin(), value.end(), L'/', L'\\');
        std::transform(value.begin(), value.end(), value.begin(), towlower);
        return value;
    };
    if (documentId != request.documentId || normalize(fullName) != normalize(request.fullName)) {
        return SetError(result, L"STALE_DOCUMENT", L"", L"active document identity does not match the batch request");
    }
    return true;
}

bool InsertText(
    IDispatch* const hwp,
    IDispatch* const action,
    const CellOperation& operation,
    ExecutionResult* const result) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> insertText;
    CComPtr<IDispatch> parameterSet;
    if (!GetDispatchProperty(hwp, L"HParameterSet", parameterSets, result, operation.address) ||
        !GetDispatchProperty(parameterSets, L"HInsertText", insertText, result, operation.address) ||
        !GetDispatchProperty(insertText, L"HSet", parameterSet, result, operation.address)) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        action,
        L"GetDefault",
        {CComVariant(L"InsertText"), CComVariant(parameterSet)},
        &ignored);
    if (FAILED(status)) {
        return SetError(result, L"INSERT_TEXT", operation.address, HResultText(L"GetDefault", status));
    }
    status = PropertyPut(insertText, L"Text", CComVariant(operation.value.c_str()));
    if (FAILED(status)) {
        return SetError(result, L"INSERT_TEXT", operation.address, HResultText(L"Text", status));
    }
    bool executed = false;
    if (!CallBooleanMethod(
            action,
            L"Execute",
            {CComVariant(L"InsertText"), CComVariant(parameterSet)},
            &executed,
            result,
            operation.address)) {
        return false;
    }
    if (!executed) {
        return SetError(result, L"INSERT_TEXT", operation.address, L"InsertText returned false");
    }
    return true;
}

CComVariant BooleanVariant(const bool value) {
    CComVariant result;
    result.vt = VT_BOOL;
    result.boolVal = value ? VARIANT_TRUE : VARIANT_FALSE;
    return result;
}

bool SetParameterItem(
    IDispatch* const parameter,
    const wchar_t* const name,
    const CComVariant& value,
    const CellOperation& operation,
    ExecutionResult* const result) {
    CComVariant ignored;
    const HRESULT status = Method(
        parameter,
        L"SetItem",
        {CComVariant(name), value},
        &ignored);
    if (FAILED(status)) {
        return SetError(result, L"IMAGE_CELL_FORMAT", operation.address, HResultText(name, status));
    }
    return true;
}

bool ExecuteParameterAction(
    IDispatch* const action,
    const wchar_t* const name,
    IDispatch* const parameterSet,
    const CellOperation& operation,
    ExecutionResult* const result) {
    bool executed = false;
    if (!CallBooleanMethod(
            action,
            L"Execute",
            {CComVariant(name), CComVariant(parameterSet)},
            &executed,
            result,
            operation.address)) {
        return false;
    }
    if (!executed) {
        return SetError(
            result,
            L"IMAGE_CELL_FORMAT",
            operation.address,
            std::wstring(name) + L" returned false");
    }
    return true;
}

bool ConfigureImageCell(
    IDispatch* const hwp,
    IDispatch* const action,
    const CellOperation& operation,
    ExecutionResult* const result) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> shapeObject;
    CComPtr<IDispatch> shapeSet;
    CComPtr<IDispatch> shapeTableCell;
    if (!GetDispatchProperty(hwp, L"HParameterSet", parameterSets, result, operation.address) ||
        !GetDispatchProperty(parameterSets, L"HShapeObject", shapeObject, result, operation.address) ||
        !GetDispatchProperty(shapeObject, L"HSet", shapeSet, result, operation.address)) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        action,
        L"GetDefault",
        {CComVariant(L"TablePropertyDialog"), CComVariant(shapeSet)},
        &ignored);
    if (FAILED(status)) {
        return SetError(result, L"IMAGE_CELL_FORMAT", operation.address, HResultText(L"GetDefault", status));
    }
    if (!SetParameterItem(shapeSet, L"ShapeType", CComVariant(3L), operation, result) ||
        !SetParameterItem(shapeSet, L"ShapeCellSize", CComVariant(0L), operation, result) ||
        !GetDispatchProperty(shapeObject, L"ShapeTableCell", shapeTableCell, result, operation.address)) {
        return false;
    }
    const std::pair<const wchar_t*, LONG> cellValues[] = {
        {L"HasMargin", 1L},
        {L"MarginLeft", 0L},
        {L"MarginRight", 0L},
        {L"MarginTop", 0L},
        {L"MarginBottom", 0L},
    };
    for (const auto& [name, value] : cellValues) {
        status = PropertyPut(shapeTableCell, name, CComVariant(value));
        if (FAILED(status)) {
            return SetError(result, L"IMAGE_CELL_FORMAT", operation.address, HResultText(name, status));
        }
    }
    bool executed = false;
    if (!CallBooleanMethod(
            action,
            L"Execute",
            {CComVariant(L"TablePropertyDialog"), CComVariant(shapeSet)},
            &executed,
            result,
            operation.address)) {
        return false;
    }
    if (!executed) {
        return SetError(
            result,
            L"IMAGE_CELL_FORMAT",
            operation.address,
            L"TablePropertyDialog returned false");
    }

    CComPtr<IDispatch> paragraphShape;
    CComPtr<IDispatch> paragraphSet;
    if (!GetDispatchProperty(parameterSets, L"HParaShape", paragraphShape, result, operation.address) ||
        !GetDispatchProperty(paragraphShape, L"HSet", paragraphSet, result, operation.address)) {
        return false;
    }
    status = Method(
        action,
        L"GetDefault",
        {CComVariant(L"ParagraphShape"), CComVariant(paragraphSet)},
        &ignored);
    if (FAILED(status)) {
        return SetError(result, L"IMAGE_CELL_FORMAT", operation.address, HResultText(L"GetDefault", status));
    }
    const wchar_t* const paragraphValues[] = {
        L"LeftMargin",
        L"RightMargin",
        L"Indentation",
        L"PrevSpacing",
        L"NextSpacing",
    };
    for (const wchar_t* const name : paragraphValues) {
        status = PropertyPut(paragraphShape, name, CComVariant(0L));
        if (FAILED(status)) {
            return SetError(result, L"IMAGE_CELL_FORMAT", operation.address, HResultText(name, status));
        }
    }
    return ExecuteParameterAction(
        action,
        L"ParagraphShape",
        paragraphSet,
        operation,
        result);
}

bool InsertImage(
    IDispatch* const hwp,
    IDispatch* const action,
    const CellOperation& operation,
    ExecutionResult* const result) {
    if (!ConfigureImageCell(hwp, action, operation, result)) {
        return false;
    }
    CComVariant pictureValue;
    const HRESULT status = Method(
        hwp,
        L"InsertPicture",
        {
            CComVariant(operation.value.c_str()),
            BooleanVariant(true),
            CComVariant(3L),
            BooleanVariant(false),
            BooleanVariant(false),
            CComVariant(0L),
            CComVariant(0L),
            CComVariant(0L),
        },
        &pictureValue);
    CComPtr<IDispatch> picture;
    HRESULT dispatchStatus = status;
    if (SUCCEEDED(dispatchStatus)) {
        dispatchStatus = AsDispatch(pictureValue, picture);
    }
    if (FAILED(dispatchStatus)) {
        return SetError(result, L"INSERT_IMAGE", operation.address, HResultText(L"InsertPicture", dispatchStatus));
    }

    CComPtr<IDispatch> properties;
    if (!GetDispatchProperty(picture, L"Properties", properties, result, operation.address)) {
        return false;
    }
    CComVariant ignored;
    const HRESULT setStatus = Method(
        properties,
        L"SetItem",
        {CComVariant(L"TreatAsChar"), BooleanVariant(true)},
        &ignored);
    if (FAILED(setStatus)) {
        return SetError(result, L"INSERT_IMAGE", operation.address, HResultText(L"SetItem", setStatus));
    }
    const HRESULT putStatus = PropertyPut(picture, L"Properties", CComVariant(properties));
    if (FAILED(putStatus)) {
        return SetError(result, L"INSERT_IMAGE", operation.address, HResultText(L"Properties", putStatus));
    }
    return true;
}

}

ExecutionResult Execute(IDispatch* const hwp, const Request& request) noexcept {
    const auto started = std::chrono::steady_clock::now();
    ExecutionResult result;
    TimingEvidence& timing = result.elapsedMicroseconds;
    const auto timed = [](long long* const elapsedMicroseconds, auto&& operation) {
        const auto stageStarted = std::chrono::steady_clock::now();
        const bool succeeded = operation();
        *elapsedMicroseconds += std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - stageStarted).count();
        return succeeded;
    };
    Position cursor;
    Selection selection;
    bool stateCaptured = false;
    const auto finish = [&result, &timing, started, hwp, &cursor, &selection, &stateCaptured]() {
        if (stateCaptured && !RestoreSelection(hwp, cursor, selection)) {
            result.succeeded = false;
            if (result.error.code.empty()) {
                SetError(
                    &result,
                    L"STATE_RESTORE",
                    L"",
                    L"cursor or selection restoration failed after native batch execution");
            }
        }
        timing.totalMicroseconds = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count();
        return result;
    };
    try {
        if (hwp == nullptr) {
            SetError(&result, L"NO_HWP", L"", L"HwpObject is unavailable");
            return finish();
        }
        if (!timed(&timing.validationMicroseconds, [&]() {
                return ValidateDocumentIdentity(hwp, request, &result);
            })) {
            return finish();
        }
        if (!timed(&timing.validationMicroseconds, [&]() {
                return GetPosition(
                           hwp,
                           &cursor.list,
                           &cursor.paragraph,
                           &cursor.character,
                           &result) &&
                    GetSelection(hwp, &selection, &result);
            })) {
            return finish();
        }
        if (!CanRestoreSelection(selection)) {
            SetError(
                &result,
                L"UNSUPPORTED_SELECTION",
                L"",
                selection.cellAddressError.empty()
                    ? L"the active HWP selection cannot be moved and restored safely"
                    : selection.cellAddressError);
            return finish();
        }
        stateCaptured = true;
        CComPtr<IDispatch> action;
        if (!timed(&timing.validationMicroseconds, [&]() {
                return GetDispatchProperty(hwp, L"HAction", action, &result);
            })) {
            return finish();
        }

        std::vector<ResolvedOperation> resolvedOperations;
        for (const TableBatch& table : request.tables) {
            std::map<std::wstring, LONG> addresses;
            if (!timed(&timing.locateMicroseconds, [&]() {
                    return EnterTable(hwp, action, table.controlId, &result) &&
                        BuildCellMap(hwp, action, table, &addresses, &result);
                })) {
                return finish();
            }
            for (const CellOperation& operation : table.operations) {
                std::wstring normalizedAddress = operation.address;
                std::transform(
                    normalizedAddress.begin(),
                    normalizedAddress.end(),
                    normalizedAddress.begin(),
                    towupper);
                const ResolvedOperation resolved{
                    &operation,
                    table.controlId,
                    addresses.at(normalizedAddress),
                };
                if (operation.kind == OperationKind::Image &&
                    !timed(&timing.validationMicroseconds, [&]() {
                        return ValidateImagePath(operation, &result);
                    })) {
                    return finish();
                }
                if (!timed(&timing.verifyMicroseconds, [&]() {
                        return VerifyExpectedText(hwp, action, resolved, &result);
                    })) {
                    return finish();
                }
                resolvedOperations.push_back(resolved);
            }
        }

        std::set<LONG> imageLists;
        for (const ResolvedOperation& resolved : resolvedOperations) {
            if (resolved.operation->kind == OperationKind::Image) {
                imageLists.insert(resolved.listId);
            }
        }
        std::map<LONG, std::vector<std::wstring>> existingPictures;
        if (!timed(&timing.imageMicroseconds, [&]() {
                return CollectExistingPictures(hwp, imageLists, &existingPictures, &result);
            })) {
            return finish();
        }

        for (const ResolvedOperation& resolved : resolvedOperations) {
            if (!timed(&timing.verifyMicroseconds, [&]() {
                    return SelectResolvedCell(hwp, action, resolved, &result);
                })) {
                return finish();
            }
            if (resolved.operation->kind == OperationKind::Text) {
                if (!timed(&timing.textMicroseconds, [&]() {
                        return InsertText(hwp, action, *resolved.operation, &result);
                    })) {
                    return finish();
                }
                ++result.textUpdates;
            } else {
                const auto imageStarted = std::chrono::steady_clock::now();
                const bool imageSucceeded = [&]() {
                    if (!InsertImage(hwp, action, *resolved.operation, &result)) {
                        return false;
                    }
                    const auto existing = existingPictures.find(resolved.listId);
                    if (existing != existingPictures.end()) {
                        for (const std::wstring& instanceId : existing->second) {
                            if (!DeleteExactControl(
                                    hwp,
                                    action,
                                    instanceId,
                                    resolved.operation->address,
                                    &result)) {
                                return false;
                            }
                        }
                    }
                    return true;
                }();
                const long long imageMicroseconds =
                    std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now() - imageStarted).count();
                timing.imageMicroseconds += imageMicroseconds;
                if (!imageSucceeded) {
                    return finish();
                }
                ++timing.imageTimingCount;
                timing.imageMaximumMicroseconds = (std::max)(
                    timing.imageMaximumMicroseconds,
                    imageMicroseconds);
                timing.imageTotalMicroseconds += imageMicroseconds;
                ++result.imageUpdates;
            }
        }
        result.succeeded = true;
    } catch (const std::exception& error) {
        const char* const message = error.what();
        SetError(
            &result,
            L"NATIVE_EXCEPTION",
            L"",
            std::wstring(message, message + std::strlen(message)));
    } catch (...) {
        SetError(&result, L"NATIVE_EXCEPTION", L"", L"native batch execution failed unexpectedly");
    }
    return finish();
}

}

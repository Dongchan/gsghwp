#include "ComState.h"

#include "DispatchInvoke.h"
#include "TableInspection.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <cwctype>

namespace hancom::com_state {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

bool SetCaptureFailure(
    SelectionCaptureFailure* const failure,
    const SelectionCaptureStage stage,
    const HRESULT status,
    const wchar_t* const detail) {
    if (failure != nullptr) {
        failure->stage = stage;
        failure->status = status;
        failure->detail = detail;
    }
    return false;
}

HRESULT DispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value) {
    CComVariant raw;
    HRESULT status = PropertyGet(object, name, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, value);
    }
    return status;
}

HRESULT LongProperty(
    IDispatch* const object,
    const wchar_t* const name,
    LONG* const value) {
    CComVariant raw;
    HRESULT status = PropertyGet(object, name, &raw);
    if (SUCCEEDED(status)) {
        status = AsLong(raw, value);
    }
    return status;
}

HRESULT CreateSet(IDispatch* const hwp, CComPtr<IDispatch>& set) {
    CComVariant raw;
    HRESULT status = Method(hwp, L"CreateSet", {CComVariant(L"ListParaPos")}, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, set);
    }
    return status;
}

HRESULT ReadPositionItem(
    IDispatch* const set,
    const wchar_t* const name,
    LONG* const value) {
    CComVariant raw;
    HRESULT status = Method(set, L"Item", {CComVariant(name)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsLong(raw, value);
    }
    return status;
}

HRESULT ReadControlIdentity(
    IDispatch* const control,
    std::wstring* const type,
    std::wstring* const instance) {
    CComVariant raw;
    HRESULT status = PropertyGet(control, L"CtrlID", &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, type);
    }
    if (FAILED(status)) {
        return status;
    }
    status = Method(control, L"GetCtrlInstID", {}, &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, instance);
    }
    return status;
}

bool TryReadControlIdentity(
    IDispatch* const control,
    std::wstring* const type,
    std::wstring* const instance) {
    CComVariant raw;
    HRESULT status = PropertyGet(control, L"CtrlID", &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, type);
    }
    if (FAILED(status)) {
        return false;
    }
    if (SUCCEEDED(Method(control, L"GetCtrlInstID", {}, &raw))) {
        static_cast<void>(AsString(raw, instance));
    }
    return true;
}

void ReadCurrentControlBestEffort(
    IDispatch* const hwp,
    std::wstring* const type,
    std::wstring* const instance) {
    CComPtr<IDispatch> control;
    if (SUCCEEDED(DispatchProperty(hwp, L"CurSelectedCtrl", control)) &&
        TryReadControlIdentity(control, type, instance)) {
        return;
    }
    control.Release();
    if (SUCCEEDED(DispatchProperty(hwp, L"ParentCtrl", control))) {
        static_cast<void>(TryReadControlIdentity(control, type, instance));
    }
}

bool SetExactPosition(IDispatch* const hwp, const Position& position) {
    const PositionResult result = ApplyPosition(
        hwp,
        position,
        EmptyPositionResult::Reject);
    return SUCCEEDED(result.invokeStatus) && SUCCEEDED(result.conversionStatus) &&
        result.positioned;
}

bool RunHwpAction(IDispatch* const hwp, const wchar_t* const actionName) {
    CComPtr<IDispatch> action;
    if (FAILED(DispatchProperty(hwp, L"HAction", action))) {
        return false;
    }
    CComVariant raw;
    if (FAILED(Method(action, L"Run", {CComVariant(actionName)}, &raw))) {
        return false;
    }
    if (raw.vt == VT_EMPTY) {
        return true;
    }
    bool returned = false;
    return SUCCEEDED(AsBool(raw, &returned)) && returned;
}

bool ReadCurrentCellAddress(IDispatch* const hwp, std::wstring* const address) {
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
        return false;
    }
    const std::wstring indicator = controlName == nullptr
        ? std::wstring()
        : std::wstring(controlName, SysStringLen(controlName));
    if (controlName != nullptr) {
        SysFreeString(controlName);
    }
    const size_t opening = indicator.find(L'(');
    const size_t closing = indicator.find(
        L')',
        opening == std::wstring::npos ? 0 : opening + 1);
    if (opening == std::wstring::npos || closing == std::wstring::npos ||
        closing <= opening + 1) {
        return false;
    }
    *address = indicator.substr(opening + 1, closing - opening - 1);
    std::transform(address->begin(), address->end(), address->begin(), towupper);
    return true;
}

bool RestoreTextSelection(IDispatch* const hwp, const Selection& selection) {
    if (!SetExactPosition(hwp, Position{selection.start.list, 0, 0})) {
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
    LONG mode = 0;
    return SUCCEEDED(AsBool(raw, &selected)) && selected &&
        SUCCEEDED(LongProperty(hwp, L"SelectionMode", &mode)) &&
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
    if (firstCell == nullptr ||
        !SetExactPosition(hwp, Position{firstCell->listId, 0, 0}) ||
        !RunHwpAction(hwp, L"TableCellBlock")) {
        return false;
    }
    if (region.size() > 1) {
        if (!RunHwpAction(hwp, L"TableCellBlockExtend")) {
            return false;
        }
        for (const hancom::inspection::CellTopologyStep& step : path) {
            const wchar_t* const actionName =
                step.direction == hancom::inspection::CellDirection::Right
                ? L"TableRightCell"
                : L"TableLowerCell";
            std::wstring current;
            if (!RunHwpAction(hwp, actionName) ||
                !ReadCurrentCellAddress(hwp, &current) ||
                current != step.destination) {
                return false;
            }
        }
    }
    LONG mode = 0;
    return SUCCEEDED(LongProperty(hwp, L"SelectionMode", &mode)) &&
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
    return SUCCEEDED(DispatchProperty(hwp, L"CurSelectedCtrl", selected)) &&
        SUCCEEDED(ReadControlIdentity(selected, &type, &instance)) &&
        type == selection.controlType && instance == selection.controlInstance;
}

}

HRESULT CapturePosition(IDispatch* const hwp, Position* const position) {
    CComVariant list;
    list.vt = VT_I4 | VT_BYREF;
    list.plVal = &position->list;
    CComVariant paragraph;
    paragraph.vt = VT_I4 | VT_BYREF;
    paragraph.plVal = &position->paragraph;
    CComVariant character;
    character.vt = VT_I4 | VT_BYREF;
    character.plVal = &position->character;
    return Method(
        hwp,
        L"GetPos",
        {list, paragraph, character},
        nullptr);
}

PositionResult ApplyPosition(
    IDispatch* const hwp,
    const Position& position,
    const EmptyPositionResult emptyResult) {
    PositionResult result;
    CComVariant returned;
    result.invokeStatus = Method(
        hwp,
        L"SetPos",
        {
            CComVariant(position.list),
            CComVariant(position.paragraph),
            CComVariant(position.character),
        },
        &returned);
    if (FAILED(result.invokeStatus)) {
        return result;
    }
    if (returned.vt == VT_EMPTY && emptyResult == EmptyPositionResult::TreatAsSuccess) {
        result.conversionStatus = S_OK;
        result.positioned = true;
        return result;
    }
    result.conversionStatus = AsBool(returned, &result.positioned);
    return result;
}

bool SamePosition(const Position& left, const Position& right) noexcept {
    return left.list == right.list && left.paragraph == right.paragraph &&
        left.character == right.character;
}

bool CaptureSelection(
    IDispatch* const hwp,
    Selection* const selection,
    const SelectionCapturePolicy policy,
    SelectionCaptureFailure* const failure) {
    if (failure != nullptr) {
        *failure = SelectionCaptureFailure{};
    }
    HRESULT status = LongProperty(hwp, L"SelectionMode", &selection->mode);
    if (FAILED(status)) {
        return SetCaptureFailure(
            failure,
            SelectionCaptureStage::SelectionMode,
            status,
            L"SelectionMode");
    }
    CComPtr<IDispatch> start;
    status = CreateSet(hwp, start);
    if (FAILED(status)) {
        return SetCaptureFailure(
            failure,
            SelectionCaptureStage::CreateSet,
            status,
            L"ListParaPos");
    }
    CComPtr<IDispatch> end;
    status = CreateSet(hwp, end);
    if (FAILED(status)) {
        return SetCaptureFailure(
            failure,
            SelectionCaptureStage::CreateSet,
            status,
            L"ListParaPos");
    }
    CComVariant raw;
    status = Method(
        hwp,
        L"GetSelectedPosBySet",
        {CComVariant(start), CComVariant(end)},
        &raw);
    if (SUCCEEDED(status)) {
        status = AsBool(raw, &selection->selected);
    }
    if (FAILED(status)) {
        return SetCaptureFailure(
            failure,
            SelectionCaptureStage::SelectedPositions,
            status,
            L"GetSelectedPosBySet");
    }
    struct ItemTarget {
        IDispatch* set;
        const wchar_t* name;
        LONG* value;
    };
    const ItemTarget targets[] = {
        {start, L"List", &selection->start.list},
        {start, L"Para", &selection->start.paragraph},
        {start, L"Pos", &selection->start.character},
        {end, L"List", &selection->end.list},
        {end, L"Para", &selection->end.paragraph},
        {end, L"Pos", &selection->end.character},
    };
    for (const ItemTarget& target : targets) {
        status = ReadPositionItem(target.set, target.name, target.value);
        if (FAILED(status)) {
            return SetCaptureFailure(
                failure,
                SelectionCaptureStage::PositionItem,
                status,
                target.name);
        }
    }
    if (policy == SelectionCapturePolicy::Basic) {
        return true;
    }
    const LONG baseMode = selection->mode & kSelectionModeMask;
    if (baseMode == kSelectionCells || baseMode == kSelectionControl) {
        if (policy == SelectionCapturePolicy::BestEffortControl) {
            ReadCurrentControlBestEffort(
                hwp,
                &selection->controlType,
                &selection->controlInstance);
        } else {
            const wchar_t* const property = baseMode == kSelectionControl
                ? L"CurSelectedCtrl"
                : L"ParentCtrl";
            CComPtr<IDispatch> control;
            status = DispatchProperty(hwp, property, control);
            if (SUCCEEDED(status)) {
                status = ReadControlIdentity(
                    control,
                    &selection->controlType,
                    &selection->controlInstance);
            }
            if (FAILED(status)) {
                return SetCaptureFailure(
                    failure,
                    SelectionCaptureStage::Control,
                    status,
                    property);
            }
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

bool SameSelection(const Selection& left, const Selection& right) noexcept {
    return left.selected == right.selected && left.mode == right.mode &&
        SamePosition(left.start, right.start) && SamePosition(left.end, right.end);
}

}

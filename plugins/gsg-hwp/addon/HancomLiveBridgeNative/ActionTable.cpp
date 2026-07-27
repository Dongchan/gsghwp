#include "ActionExecutorInternal.h"

#include <algorithm>
#include <cwctype>
#include <string>
#include <utility>
#include <vector>

namespace hancom::actions::detail {

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
        return SetError(result, L"CONTROL_ID", location, FormatHResult(L"GetCtrlInstID", status));
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
    context->topology.Clear();
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
        context->topology.Clear();
    }
    return true;
}

bool CopyControl(Context* const context, const std::wstring& instanceId) {
    context->table.Release();
    context->tableId.clear();
    context->topology.Clear();
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
            FormatHResult(L"read source table anchor format", formatStatus));
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
            FormatHResult(L"GetTextFile HWP saveblock", status));
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
            FormatHResult(L"apply copied table anchor format", formatStatus));
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
                ? FormatHResult(L"verify copied table anchor format", formatStatus)
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
            FormatHResult(L"SetTextFile HWP insertfile", status));
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
                    ? FormatHResult(L"verify pasted table anchor format", formatStatus)
                    : L"pasted table anchor format does not match its source");
        }
    }
    if (!SelectControl(context, context->tableId)) {
        return false;
    }
    context->result->createdControlIds.push_back(context->tableId);
    return true;
}

bool SetTableTreatAsCharacter(Context* const context, const bool treatAsCharacter) {
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
        {CComVariant(L"TreatAsChar"), BooleanVariant(treatAsCharacter)},
        &ignored);
    if (SUCCEEDED(status)) {
        status = PropertyPut(context->table, L"Properties", CComVariant(properties));
    }
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"TABLE_PROPERTY",
            L"TreatAsChar",
            FormatHResult(L"TreatAsChar", status));
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

bool BuildCellTopology(Context* const context) {
    if (context->table == nullptr && !CaptureCurrentTable(context)) {
        return false;
    }
    std::wstring error;
    if (!hancom::inspection::InspectTableTopology(
            context->hwp,
            context->tableId,
            &context->topology,
            &error)) {
        return SetError(
            context->result,
            L"TABLE_TOPOLOGY",
            context->tableId,
            error.empty() ? L"table topology inspection failed" : error);
    }
    return true;
}

std::wstring NormalizeAddress(std::wstring address) {
    std::transform(address.begin(), address.end(), address.begin(), towupper);
    return address;
}

bool GoToCell(Context* const context, const std::wstring& requested) {
    const std::wstring address = NormalizeAddress(requested);
    if ((context->topology.Empty() && !BuildCellTopology(context)) ||
        context->topology.Find(address) == nullptr) {
        if (!context->topology.Empty()) {
            return SetError(context->result, L"CELL_NOT_FOUND", address, L"cell is not present in the current table");
        }
        return false;
    }
    const auto enter = [&](ExecutionResult* const result) {
        const hancom::inspection::CellTopologyCell* const cell =
            context->topology.Find(address);
        if (cell == nullptr) {
            return false;
        }
        if (!SetPosition(
                context->hwp,
                Position{cell->listId, 0, 0},
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
    context->topology.Clear();
    if (!BuildCellTopology(context)) {
        return false;
    }
    if (context->topology.Find(address) == nullptr) {
        return SetError(context->result, L"CELL_NOT_FOUND", address, L"cell is not present after refreshing the table map");
    }
    if (!enter(context->result)) {
        return SetError(context->result, L"WRONG_CELL", address, L"cursor did not enter the requested cell");
    }
    context->currentCell = address;
    return true;
}

bool MergeCellsUsingTopology(
    Context* const context,
    const std::wstring& first,
    const std::wstring& second,
    const bool clearTopology) {
    if (context->topology.Empty() && !BuildCellTopology(context)) {
        return false;
    }
    std::vector<hancom::inspection::CellTopologyStep> path;
    std::vector<std::wstring> region;
    std::wstring error;
    if (!context->topology.PlanRectangularMerge(first, second, &path, &region, &error)) {
        return SetError(
            context->result,
            L"MERGE_RANGE",
            first + L":" + second,
            error.empty() ? L"merge range is not a complete cell rectangle" : error);
    }
    if (!GoToCell(context, first) ||
        !RunAction(context->action, L"TableCellBlock", context->result, first) ||
        !RunAction(context->action, L"TableCellBlockExtend", context->result, first)) {
        return false;
    }
    for (const hancom::inspection::CellTopologyStep& step : path) {
        const wchar_t* const action =
            step.direction == hancom::inspection::CellDirection::Right
            ? L"TableRightCell"
            : L"TableLowerCell";
        if (!RunAction(context->action, action, context->result, step.destination)) {
            return false;
        }
        if (GetCellAddress(context->hwp) != step.destination) {
            return SetError(
                context->result,
                L"MERGE_PATH",
                step.destination,
                L"actual cell neighbour did not match the inspected topology");
        }
    }
    if (!RunAction(context->action, L"TableMergeCell", context->result, first)) {
        return false;
    }
    if (clearTopology) {
        context->topology.Clear();
    }
    context->currentCell = NormalizeAddress(first);
    return true;
}

bool MergeCells(Context* const context, const std::wstring& first, const std::wstring& second) {
    return MergeCellsUsingTopology(context, first, second, true);
}

bool InsertTextSegment(
    Context* const context,
    const std::wstring& text,
    const std::wstring& location) {
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
        return SetError(context->result, L"INSERT_TEXT", location, FormatHResult(L"InsertText", status));
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
    return true;
}

bool InsertText(Context* const context, const std::wstring& text, const std::wstring& location) {
    size_t offset = 0;
    bool first = true;
    while (offset <= text.size()) {
        const size_t newline = text.find_first_of(L"\r\n", offset);
        const size_t end =
            newline == std::wstring::npos ? text.size() : newline;
        if (!first) {
            if (!RunAction(
                    context->action,
                    L"BreakPara",
                    context->result,
                    location)) {
                return false;
            }
            ++context->result->actionsExecuted;
            context->result->partialMutation = true;
        }
        const std::wstring segment = text.substr(offset, end - offset);
        if (!segment.empty()) {
            if (!InsertTextSegment(context, segment, location)) {
                return false;
            }
            context->result->partialMutation = true;
        }
        first = false;
        if (newline == std::wstring::npos) {
            break;
        }
        offset = newline + 1;
        if (text[newline] == L'\r' &&
            offset < text.size() &&
            text[offset] == L'\n') {
            ++offset;
        }
    }
    ++context->result->textInsertions;
    return true;
}

}

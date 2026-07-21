#include "TableInspection.h"

#include "DispatchInvoke.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <limits>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace hancom::inspection {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

bool Fail(std::wstring* const error, const std::wstring& message) {
    if (error != nullptr) {
        *error = message;
    }
    return false;
}

bool DispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) && SUCCEEDED(AsDispatch(raw, value));
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
    return DispatchProperty(hwp, L"CellShape", table) &&
        ItemDispatch(table, L"Cell", cell) &&
        ItemLong(cell, L"Width", width) &&
        ItemLong(cell, L"Height", height);
}

bool DocumentInfo(IDispatch* const hwp, CComPtr<IDispatch>& info) {
    CComPtr<IDispatch> documents;
    CComPtr<IDispatch> document;
    return DispatchProperty(hwp, L"XHwpDocuments", documents) &&
        DispatchProperty(documents, L"Active_XHwpDocument", document) &&
        DispatchProperty(document, L"XHwpDocumentInfo", info);
}

bool CurrentPage(IDispatch* const info, LONG* const page) {
    CComVariant raw;
    LONG zeroBased = 0;
    if (info == nullptr || FAILED(PropertyGet(info, L"CurrentPage", &raw)) ||
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
    if (FAILED(Method(object, name, arguments, &raw))) {
        return false;
    }
    if (raw.vt == VT_EMPTY) {
        return true;
    }
    bool value = false;
    return SUCCEEDED(AsBool(raw, &value)) && value;
}

bool RunAction(IDispatch* const action, const wchar_t* const name) {
    return CallBoolean(action, L"Run", {CComVariant(name)});
}

bool SetPosition(IDispatch* const hwp, const LONG listId) {
    return CallBoolean(
        hwp,
        L"SetPos",
        {CComVariant(listId), CComVariant(0L), CComVariant(0L)});
}

bool GetPositionList(IDispatch* const hwp, LONG* const listId) {
    LONG paragraph = 0;
    LONG character = 0;
    CComVariant list;
    list.vt = VT_I4 | VT_BYREF;
    list.plVal = listId;
    CComVariant para;
    para.vt = VT_I4 | VT_BYREF;
    para.plVal = &paragraph;
    CComVariant position;
    position.vt = VT_I4 | VT_BYREF;
    position.plVal = &character;
    return SUCCEEDED(Method(hwp, L"GetPos", {list, para, position}, nullptr));
}

bool ControlInstanceId(IDispatch* const control, std::wstring* const id) {
    CComVariant raw;
    return control != nullptr &&
        SUCCEEDED(Method(control, L"GetCtrlInstID", {}, &raw)) &&
        SUCCEEDED(AsString(raw, id));
}

bool ControlType(IDispatch* const control, std::wstring* const type) {
    CComVariant raw;
    return control != nullptr &&
        SUCCEEDED(PropertyGet(control, L"CtrlID", &raw)) &&
        SUCCEEDED(AsString(raw, type));
}

bool SelectedTableMatches(IDispatch* const hwp, const std::wstring& tableInstanceId) {
    CComPtr<IDispatch> selected;
    std::wstring type;
    std::wstring instance;
    return DispatchProperty(hwp, L"CurSelectedCtrl", selected) &&
        ControlType(selected, &type) && type == L"tbl" &&
        ControlInstanceId(selected, &instance) && instance == tableInstanceId;
}

bool FindControl(
    IDispatch* const hwp,
    const std::wstring& instanceId,
    CComPtr<IDispatch>& match) {
    CComPtr<IDispatch> control;
    if (!DispatchProperty(hwp, L"HeadCtrl", control)) {
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
        if (!DispatchProperty(control, L"Next", next)) {
            break;
        }
        control = next;
    }
    return false;
}

bool MoveToControl(IDispatch* const hwp, IDispatch* const control) {
    CComVariant rawAnchor;
    CComPtr<IDispatch> anchor;
    if (FAILED(Method(control, L"GetAnchorPos", {CComVariant(0L)}, &rawAnchor)) ||
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
        RunAction(action, L"SelectCtrlFront") &&
        SelectedTableMatches(hwp, tableInstanceId);
}

bool ParentMatches(IDispatch* const hwp, const std::wstring& tableInstanceId) {
    CComPtr<IDispatch> parent;
    std::wstring parentId;
    return DispatchProperty(hwp, L"ParentCtrl", parent) &&
        ControlInstanceId(parent, &parentId) && parentId == tableInstanceId;
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
    const HRESULT status = Method(
        hwp,
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

bool InferCellSpans(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& tableInstanceId,
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

    std::vector<LONG> directColumnSpans(cells->size(), 0);
    for (size_t index = 0; index < cells->size(); ++index) {
        TableCellRecord& cell = (*cells)[index];
        LONG row = 0;
        LONG column = 0;
        static_cast<void>(CellCoordinates(cell.address, &row, &column));

        cell.rowSpan = rows - row + 1;
        if (!SetPosition(hwp, cell.listId)) {
            return Fail(error, L"table cell could not be positioned for row span inspection");
        }
        if (RunAction(action, L"TableLowerCell") && ParentMatches(hwp, tableInstanceId)) {
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
        if (RunAction(action, L"TableRightCell") && ParentMatches(hwp, tableInstanceId)) {
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

bool SelectTableControl(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId) noexcept {
    try {
        CComPtr<IDispatch> action;
        return hwp != nullptr && !tableInstanceId.empty() &&
            DispatchProperty(hwp, L"HAction", action) &&
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
            if (state <= 1) {
                complete = true;
                break;
            }
        }
        const HRESULT released = Method(hwp, L"ReleaseScan", {}, nullptr);
        return valid && complete && SUCCEEDED(released);
    } catch (...) {
        return false;
    }
}

namespace {

bool EnterTable(
    IDispatch* const hwp,
    IDispatch* const action,
    const std::wstring& tableInstanceId) {
    if (ParentMatches(hwp, tableInstanceId)) {
        return true;
    }
    if (SelectExactTable(hwp, action, tableInstanceId) &&
        RunAction(action, L"ShapeObjTextBoxEdit") &&
        ParentMatches(hwp, tableInstanceId)) {
        return true;
    }
    CComPtr<IDispatch> control;
    return FindControl(hwp, tableInstanceId, control) &&
        MoveToControl(hwp, control) &&
        RunAction(action, L"SelectCtrlFront") &&
        RunAction(action, L"ShapeObjTextBoxEdit") &&
        ParentMatches(hwp, tableInstanceId);
}

}

bool InspectTableCells(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    std::vector<TableCellRecord>* const cells,
    std::wstring* const error) noexcept {
    try {
        if (hwp == nullptr || cells == nullptr || tableInstanceId.empty()) {
            return Fail(error, L"table inspection arguments are invalid");
        }
        CComPtr<IDispatch> action;
        CComPtr<IDispatch> info;
        if (!DispatchProperty(hwp, L"HAction", action) ||
            !DocumentInfo(hwp, info) ||
            !EnterTable(hwp, action, tableInstanceId) ||
            !RunAction(action, L"TableColEnd") ||
            !RunAction(action, L"TableColPageDown")) {
            return Fail(error, L"table edit context is unavailable");
        }
        LONG lastList = 0;
        if (!GetPositionList(hwp, &lastList) ||
            !RunAction(action, L"TableColBegin") ||
            !RunAction(action, L"TableColPageUp")) {
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
        std::set<std::wstring> seen;
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
                !ReadCurrentListText(hwp, &text) ||
                !RunAction(action, L"MoveListEnd") ||
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
        if (!InferCellSpans(hwp, action, tableInstanceId, cells, error)) {
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

}

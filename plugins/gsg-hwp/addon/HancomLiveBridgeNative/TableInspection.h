#pragma once

#include <Windows.h>
#include <oaidl.h>

#include <string>
#include <vector>

namespace hancom::inspection {

struct TableCellRecord {
    std::wstring tableInstanceId;
    std::wstring address;
    LONG listId = 0;
    LONG rowSpan = 1;
    LONG columnSpan = 1;
    LONG pageStart = 1;
    LONG pageEnd = 1;
    std::wstring text;
    LONG width = -1;
    LONG height = -1;
};

bool ReadCurrentListText(
    IDispatch* hwp,
    std::wstring* text) noexcept;

bool SelectTableControl(
    IDispatch* hwp,
    const std::wstring& tableInstanceId) noexcept;

bool InspectTableCells(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    std::vector<TableCellRecord>* cells,
    std::wstring* error) noexcept;

}

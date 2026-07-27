#pragma once

#include "CellTopology.h"

#include <Windows.h>
#include <oaidl.h>

#include <string>
#include <vector>

namespace hancom::inspection {

using TableCellRecord = CellTopologyCell;

bool ReadCurrentListText(
    IDispatch* hwp,
    std::wstring* text) noexcept;

bool ReadSelectedCellAddresses(
    IDispatch* hwp,
    std::vector<std::wstring>* addresses,
    std::wstring* error) noexcept;

bool SelectTableControl(
    IDispatch* hwp,
    const std::wstring& tableInstanceId) noexcept;

bool InspectTableCells(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    std::vector<TableCellRecord>* cells,
    std::wstring* error,
    std::wstring* errorCode = nullptr) noexcept;

bool InspectTableTopology(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    CellTopology* topology,
    std::wstring* error) noexcept;

}

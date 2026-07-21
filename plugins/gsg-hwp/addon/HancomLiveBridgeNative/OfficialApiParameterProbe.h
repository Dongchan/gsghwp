#pragma once

#include <Windows.h>

#include <string>
#include <vector>

namespace hancom::official_api {

std::wstring ProbeParameterSet(
    IDispatch* hwp,
    const std::wstring& name,
    const std::wstring& linkedAction,
    const std::vector<std::wstring>& itemLines);

}

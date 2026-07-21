#pragma once

#include <oaidl.h>

#include <string>
#include <vector>

namespace hancom::official_api {

std::wstring ProbeAutomation(
    IDispatch* hwp,
    const std::wstring& owner,
    const std::wstring& member,
    const std::wstring& kind,
    const std::vector<std::wstring>& argumentLines);

}

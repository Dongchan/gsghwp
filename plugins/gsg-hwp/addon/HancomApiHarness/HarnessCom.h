#pragma once

#include <Windows.h>

#include <string>

namespace hancom::api_harness {

struct HarnessResult {
    HRESULT status = E_UNEXPECTED;
    DWORD processId = 0;
    std::wstring response;
};

HarnessResult RunOfficialApiCase(
    const std::wstring& request,
    const wchar_t* processIdPath) noexcept;

}

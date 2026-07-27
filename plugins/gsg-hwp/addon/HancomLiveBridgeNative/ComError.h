#pragma once

#include <Windows.h>

#include <string>

namespace hancom::com {

std::wstring FormatHResult(const wchar_t* operation, HRESULT status);

}

#pragma once

#include <Windows.h>

#include <string>

namespace hancom::official_api {

std::wstring Probe(IDispatch* hwp, const std::wstring& payload) noexcept;

}

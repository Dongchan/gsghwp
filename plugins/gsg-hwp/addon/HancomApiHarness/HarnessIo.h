#pragma once

#include <string>

namespace hancom::api_harness {

bool ReadUtf8File(const wchar_t* path, std::wstring* value) noexcept;
bool WriteUtf8File(const wchar_t* path, const std::wstring& value) noexcept;

}

#pragma once

#include <string>

namespace hancom::encoding {

std::wstring EncodeUtf8Base64(const std::wstring& value);
bool DecodeUtf8Base64(const std::wstring& value, std::wstring* decoded) noexcept;

}

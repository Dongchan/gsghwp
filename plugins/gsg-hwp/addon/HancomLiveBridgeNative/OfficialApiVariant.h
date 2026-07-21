#pragma once

#include <oaidl.h>

#include <string>

namespace hancom::official_api {

bool DecodeUtf8Base64(const std::wstring& encoded, std::wstring* decoded) noexcept;
std::wstring VariantText(const VARIANT& value);

}

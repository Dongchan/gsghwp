#include "ProtocolEncoding.h"

#include <Windows.h>
#include <WinCrypt.h>

#include <limits>
#include <vector>

namespace hancom::encoding {

std::wstring EncodeUtf8Base64(const std::wstring& value) {
    if (value.size() > static_cast<size_t>((std::numeric_limits<int>::max)())) {
        return L"";
    }
    const int byteCount = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        value.data(),
        static_cast<int>(value.size()),
        nullptr,
        0,
        nullptr,
        nullptr);
    if (byteCount == 0 && !value.empty()) {
        return L"";
    }
    std::vector<BYTE> bytes(static_cast<size_t>(byteCount));
    if (byteCount != 0 && WideCharToMultiByte(
            CP_UTF8,
            WC_ERR_INVALID_CHARS,
            value.data(),
            static_cast<int>(value.size()),
            reinterpret_cast<LPSTR>(bytes.data()),
            byteCount,
            nullptr,
            nullptr) != byteCount) {
        return L"";
    }
    DWORD encodedCount = 0;
    if (!CryptBinaryToStringW(
            bytes.data(),
            static_cast<DWORD>(bytes.size()),
            CRYPT_STRING_BASE64 | CRYPT_STRING_NOCRLF,
            nullptr,
            &encodedCount)) {
        return L"";
    }
    std::vector<wchar_t> encoded(encodedCount);
    if (!CryptBinaryToStringW(
            bytes.data(),
            static_cast<DWORD>(bytes.size()),
            CRYPT_STRING_BASE64 | CRYPT_STRING_NOCRLF,
            encoded.data(),
            &encodedCount)) {
        return L"";
    }
    return std::wstring(encoded.data());
}

}

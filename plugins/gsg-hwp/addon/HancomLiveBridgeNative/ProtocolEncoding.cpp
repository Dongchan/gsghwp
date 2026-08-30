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

bool DecodeUtf8Base64(
    const std::wstring& value,
    std::wstring* const decoded) noexcept {
    if (decoded == nullptr ||
        value.size() > static_cast<size_t>((std::numeric_limits<DWORD>::max)())) {
        return false;
    }
    DWORD byteCount = 0;
    if (!CryptStringToBinaryW(
            value.c_str(),
            static_cast<DWORD>(value.size()),
            CRYPT_STRING_BASE64,
            nullptr,
            &byteCount,
            nullptr,
            nullptr)) {
        return false;
    }
    std::vector<BYTE> bytes(byteCount);
    if (byteCount != 0 && !CryptStringToBinaryW(
            value.c_str(),
            static_cast<DWORD>(value.size()),
            CRYPT_STRING_BASE64,
            bytes.data(),
            &byteCount,
            nullptr,
            nullptr)) {
        return false;
    }
    if (byteCount > static_cast<DWORD>((std::numeric_limits<int>::max)())) {
        return false;
    }
    const int characterCount = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        reinterpret_cast<LPCCH>(bytes.data()),
        static_cast<int>(byteCount),
        nullptr,
        0);
    if (characterCount == 0 && byteCount != 0) {
        return false;
    }
    std::wstring valueDecoded(static_cast<size_t>(characterCount), L'\0');
    if (characterCount != 0 && MultiByteToWideChar(
            CP_UTF8,
            MB_ERR_INVALID_CHARS,
            reinterpret_cast<LPCCH>(bytes.data()),
            static_cast<int>(byteCount),
            valueDecoded.data(),
            characterCount) != characterCount) {
        return false;
    }
    *decoded = std::move(valueDecoded);
    return true;
}

}

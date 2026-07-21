#include "OfficialApiVariant.h"

#include <Windows.h>
#include <wincrypt.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <string>
#include <vector>

namespace hancom::official_api {

bool DecodeUtf8Base64(
    const std::wstring& encoded,
    std::wstring* const decoded) noexcept {
    if (decoded == nullptr) {
        return false;
    }
    DWORD byteCount = 0;
    if (!CryptStringToBinaryW(
            encoded.c_str(),
            static_cast<DWORD>(encoded.size()),
            CRYPT_STRING_BASE64,
            nullptr,
            &byteCount,
            nullptr,
            nullptr)) {
        return false;
    }
    std::vector<BYTE> bytes(byteCount);
    if (!CryptStringToBinaryW(
            encoded.c_str(),
            static_cast<DWORD>(encoded.size()),
            CRYPT_STRING_BASE64,
            bytes.data(),
            &byteCount,
            nullptr,
            nullptr)) {
        return false;
    }
    if (byteCount == 0) {
        decoded->clear();
        return true;
    }
    const int characterCount = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        reinterpret_cast<const char*>(bytes.data()),
        static_cast<int>(byteCount),
        nullptr,
        0);
    if (characterCount == 0) {
        return false;
    }
    decoded->resize(static_cast<size_t>(characterCount));
    return MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        reinterpret_cast<const char*>(bytes.data()),
        static_cast<int>(byteCount),
        decoded->data(),
        characterCount) == characterCount;
}

std::wstring VariantText(const VARIANT& value) {
    CComVariant dereferenced;
    const VARIANT* printable = &value;
    if ((value.vt & VT_BYREF) != 0) {
        if (FAILED(VariantCopyInd(&dereferenced, const_cast<VARIANT*>(&value)))) {
            return L"UNSERIALIZABLE";
        }
        printable = &dereferenced;
    }
    if (printable->vt == VT_EMPTY) {
        return L"EMPTY";
    }
    if (printable->vt == VT_NULL) {
        return L"NULL";
    }
    if (printable->vt == VT_DISPATCH || printable->vt == VT_UNKNOWN) {
        return L"DISPATCH";
    }
    if ((printable->vt & VT_ARRAY) != 0) {
        return L"ARRAY";
    }
    CComVariant text(*printable);
    if (FAILED(text.ChangeType(VT_BSTR)) || text.bstrVal == nullptr) {
        return L"UNSERIALIZABLE";
    }
    std::wstring result(text.bstrVal, SysStringLen(text.bstrVal));
    for (wchar_t& character : result) {
        if (character == L'\t' || character == L'\r' || character == L'\n') {
            character = L' ';
        }
    }
    return result;
}

}

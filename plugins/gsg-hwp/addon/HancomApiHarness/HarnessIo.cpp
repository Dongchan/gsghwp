#include "HarnessIo.h"

#include <Windows.h>

#include <fstream>
#include <iterator>
#include <string>

namespace hancom::api_harness {
namespace {

bool Utf8ToWide(const std::string& input, std::wstring* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    if (input.empty()) {
        output->clear();
        return true;
    }
    const int size = MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, input.data(), static_cast<int>(input.size()), nullptr, 0);
    if (size == 0) {
        return false;
    }
    output->resize(static_cast<size_t>(size));
    return size == 0 || MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        input.data(),
        static_cast<int>(input.size()),
        output->data(),
        size) == size;
}

bool WideToUtf8(const std::wstring& input, std::string* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    if (input.empty()) {
        output->clear();
        return true;
    }
    const int size = WideCharToMultiByte(
        CP_UTF8, 0, input.data(), static_cast<int>(input.size()), nullptr, 0, nullptr, nullptr);
    if (size == 0) {
        return false;
    }
    output->resize(static_cast<size_t>(size));
    return size == 0 || WideCharToMultiByte(
        CP_UTF8,
        0,
        input.data(),
        static_cast<int>(input.size()),
        output->data(),
        size,
        nullptr,
        nullptr) == size;
}

}

bool ReadUtf8File(const wchar_t* const path, std::wstring* const value) noexcept {
    if (path == nullptr || value == nullptr) {
        return false;
    }
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        return false;
    }
    const std::string bytes{
        std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
    return Utf8ToWide(bytes, value);
}

bool WriteUtf8File(const wchar_t* const path, const std::wstring& value) noexcept {
    if (path == nullptr) {
        return false;
    }
    std::string bytes;
    if (!WideToUtf8(value, &bytes)) {
        return false;
    }
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    return static_cast<bool>(stream);
}

}

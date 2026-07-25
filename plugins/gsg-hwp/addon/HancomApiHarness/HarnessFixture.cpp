#include "HarnessFixture.h"

#include "DispatchInvoke.h"

#include <Windows.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>

namespace hancom::api_harness {
namespace {

using hancom::dispatch::AsDispatch;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyPut;

struct FixturePaths {
    std::filesystem::path root;
    std::filesystem::path inputHwp;
    std::filesystem::path inputImage;
};

FixturePaths Paths() {
    std::array<wchar_t, MAX_PATH + 1> buffer{};
    const DWORD length = GetTempPathW(
        static_cast<DWORD>(buffer.size()),
        buffer.data());
    if (length == 0 || length >= buffer.size()) {
        return {};
    }
    const std::filesystem::path root =
        std::filesystem::path(buffer.data()) / L"HancomApiHarness";
    return FixturePaths{
        root,
        root / L"probe-input.hwp",
        root / L"probe-input.bmp",
    };
}

bool WriteBitmap(const std::filesystem::path& path) {
    constexpr std::uint32_t kWidth = 8;
    constexpr std::uint32_t kHeight = 8;
    constexpr std::uint32_t kStride = 24;
    constexpr std::uint32_t kPixelBytes = kStride * kHeight;
    constexpr std::uint32_t kFileBytes = 54 + kPixelBytes;
    std::vector<std::uint8_t> bytes(kFileBytes, 0);
    const auto put16 = [&bytes](const size_t offset, const std::uint16_t value) {
        bytes[offset] = static_cast<std::uint8_t>(value & 0xFFU);
        bytes[offset + 1] = static_cast<std::uint8_t>((value >> 8U) & 0xFFU);
    };
    const auto put32 = [&bytes](const size_t offset, const std::uint32_t value) {
        bytes[offset] = static_cast<std::uint8_t>(value & 0xFFU);
        bytes[offset + 1] = static_cast<std::uint8_t>((value >> 8U) & 0xFFU);
        bytes[offset + 2] = static_cast<std::uint8_t>((value >> 16U) & 0xFFU);
        bytes[offset + 3] = static_cast<std::uint8_t>((value >> 24U) & 0xFFU);
    };
    bytes[0] = 'B';
    bytes[1] = 'M';
    put32(2, kFileBytes);
    put32(10, 54);
    put32(14, 40);
    put32(18, kWidth);
    put32(22, kHeight);
    put16(26, 1);
    put16(28, 24);
    put32(34, kPixelBytes);
    for (std::uint32_t y = 0; y < kHeight; ++y) {
        for (std::uint32_t x = 0; x < kWidth; ++x) {
            const size_t offset = 54 + static_cast<size_t>(y * kStride + x * 3);
            bytes[offset] = static_cast<std::uint8_t>(32U + x * 20U);
            bytes[offset + 1] = static_cast<std::uint8_t>(32U + y * 20U);
            bytes[offset + 2] = 160U;
        }
    }
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    stream.write(
        reinterpret_cast<const char*>(bytes.data()),
        static_cast<std::streamsize>(bytes.size()));
    return static_cast<bool>(stream);
}

CComVariant BooleanVariant(const bool value) noexcept {
    CComVariant result;
    result.vt = VT_BOOL;
    result.boolVal = value ? VARIANT_TRUE : VARIANT_FALSE;
    return result;
}

HRESULT EnsureInputHwp(
    IDispatch* const hwp,
    const std::filesystem::path& path) noexcept {
    if (std::filesystem::is_regular_file(path)) {
        return S_OK;
    }
    CComVariant ignored;
    static_cast<void>(Method(
        hwp,
        L"RegisterModule",
        {CComVariant(L"FilePathCheckDLL"), CComVariant(L"FilePathCheckerModule")},
        &ignored));
    HRESULT status = Method(
        hwp,
        L"SaveAs",
        {
            CComVariant(path.c_str()),
            CComVariant(L"HWP"),
            CComVariant(L"lock:FALSE"),
        },
        &ignored);
    if (FAILED(status)) {
        return status;
    }
    CComVariant rawDocuments;
    status = hancom::dispatch::PropertyGet(hwp, L"XHwpDocuments", &rawDocuments);
    CComPtr<IDispatch> documents;
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawDocuments, documents);
    }
    CComVariant rawDocument;
    if (SUCCEEDED(status)) {
        status = Method(
            documents,
            L"Add",
            {BooleanVariant(true)},
            &rawDocument);
    }
    return status;
}

}

HRESULT PrepareOfficialApiFixture(
    IDispatch* const hwp,
    const std::wstring& request) noexcept {
    if (hwp == nullptr) {
        return E_POINTER;
    }
    try {
        const FixturePaths paths = Paths();
        if (paths.root.empty() ||
            (!std::filesystem::exists(paths.root) &&
             !std::filesystem::create_directories(paths.root))) {
            return E_FAIL;
        }
        if (!std::filesystem::is_regular_file(paths.inputImage) &&
            !WriteBitmap(paths.inputImage)) {
            return E_FAIL;
        }
        static_cast<void>(request);
        return EnsureInputHwp(hwp, paths.inputHwp);
    } catch (...) {
        return E_UNEXPECTED;
    }
}

}

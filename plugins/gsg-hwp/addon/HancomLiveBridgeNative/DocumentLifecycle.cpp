#include "DocumentLifecycle.h"

#include "DispatchInvoke.h"
#include "OfficialApiState.h"

#include <Windows.h>
#include <WinCrypt.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <chrono>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace hancom::lifecycle {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;
using hancom::official_api::CaptureDocumentFingerprint;
using hancom::official_api::DocumentFingerprint;

HRESULT ReadFullName(IDispatch* const hwp, std::wstring* const path) noexcept {
    CComVariant rawDocuments;
    HRESULT status = PropertyGet(hwp, L"XHwpDocuments", &rawDocuments);
    CComPtr<IDispatch> documents;
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawDocuments, documents);
    }

    CComVariant rawDocument;
    CComPtr<IDispatch> document;
    if (SUCCEEDED(status)) {
        status = PropertyGet(documents, L"Active_XHwpDocument", &rawDocument);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawDocument, document);
    }

    CComVariant raw;
    if (SUCCEEDED(status)) {
        status = PropertyGet(document, L"FullName", &raw);
    }
    if (SUCCEEDED(status)) {
        status = AsString(raw, path);
    }
    return status;
}

LONG ReadModified(IDispatch* const hwp) noexcept {
    CComVariant raw;
    bool modified = false;
    if (FAILED(PropertyGet(hwp, L"IsModified", &raw)) ||
        FAILED(AsBool(raw, &modified))) {
        return -1;
    }
    return modified ? 1L : 0L;
}

LONG BooleanReturn(const CComVariant& value) noexcept {
    if (value.vt == VT_EMPTY || value.vt == VT_NULL) {
        return -1;
    }
    bool returned = false;
    return SUCCEEDED(AsBool(value, &returned)) ? (returned ? 1L : 0L) : -1L;
}

std::wstring NormalizePath(const std::wstring& path) {
    if (path.empty()) {
        return L"";
    }
    const DWORD required = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
    if (required == 0) {
        return L"";
    }
    std::vector<wchar_t> buffer(required);
    const DWORD copied = GetFullPathNameW(
        path.c_str(),
        static_cast<DWORD>(buffer.size()),
        buffer.data(),
        nullptr);
    if (copied == 0 || copied >= buffer.size()) {
        return L"";
    }
    std::wstring normalized(buffer.data(), copied);
    std::replace(normalized.begin(), normalized.end(), L'/', L'\\');
    constexpr wchar_t kExtendedUncPrefix[] = L"\\\\?\\UNC\\";
    constexpr wchar_t kExtendedPrefix[] = L"\\\\?\\";
    if (normalized.rfind(kExtendedUncPrefix, 0) == 0) {
        normalized = L"\\\\" + normalized.substr(8);
    } else if (normalized.rfind(kExtendedPrefix, 0) == 0) {
        normalized.erase(0, 4);
    }
    return normalized;
}

bool SamePath(const std::wstring& before, const std::wstring& after) {
    const std::wstring normalizedBefore = NormalizePath(before);
    const std::wstring normalizedAfter = NormalizePath(after);
    if (normalizedBefore.empty() || normalizedAfter.empty()) {
        return false;
    }
    return CompareStringOrdinal(
               normalizedBefore.c_str(),
               static_cast<int>(normalizedBefore.size()),
               normalizedAfter.c_str(),
               static_cast<int>(normalizedAfter.size()),
               TRUE) == CSTR_EQUAL;
}

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

bool SameFingerprint(
    const DocumentFingerprint& before,
    const DocumentFingerprint& after) noexcept {
    return before.captured && after.captured &&
        before.state.pageCount == after.state.pageCount &&
        before.state.controlCount == after.state.controlCount &&
        before.state.controlHash == after.state.controlHash &&
        before.textLength == after.textLength &&
        before.textHash == after.textHash &&
        before.documentLength == after.documentLength &&
        before.documentHash == after.documentHash;
}

std::uint64_t FileSize(const std::wstring& path) noexcept {
    WIN32_FILE_ATTRIBUTE_DATA attributes = {};
    if (!GetFileAttributesExW(
            path.c_str(),
            GetFileExInfoStandard,
            &attributes) ||
        (attributes.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        return 0;
    }
    return
        (static_cast<std::uint64_t>(attributes.nFileSizeHigh) << 32) |
        static_cast<std::uint64_t>(attributes.nFileSizeLow);
}

bool ReadDocumentBlock(
    IDispatch* const hwp,
    std::wstring* const documentBlock) noexcept {
    CComVariant raw;
    return SUCCEEDED(Method(
               hwp,
               L"GetTextFile",
               {CComVariant(L"HWP"), CComVariant(L"")},
               &raw)) &&
        SUCCEEDED(AsString(raw, documentBlock)) &&
        !documentBlock->empty();
}

}

std::wstring SaveVerify(IDispatch* const hwp) {
    const auto started = std::chrono::steady_clock::now();
    std::wstring path;
    const HRESULT pathStatus = ReadFullName(hwp, &path);
    const DocumentFingerprint before = CaptureDocumentFingerprint(hwp);

    HRESULT saveStatus = E_PENDING;
    LONG saveReturn = -1;
    LONG postSaveModified = -1;
    if (FAILED(pathStatus) || path.empty()) {
        saveStatus = FAILED(pathStatus) ? pathStatus : E_INVALIDARG;
    } else {
        CComVariant saveIfDirty;
        saveIfDirty.vt = VT_BOOL;
        saveIfDirty.boolVal = VARIANT_TRUE;
        CComVariant rawSaveReturn;
        saveStatus = Method(hwp, L"Save", {saveIfDirty}, &rawSaveReturn);
        saveReturn = BooleanReturn(rawSaveReturn);
        if (SUCCEEDED(saveStatus)) {
            postSaveModified = ReadModified(hwp);
        }
    }

    std::wstring afterPath;
    const HRESULT afterPathStatus = ReadFullName(hwp, &afterPath);
    const DocumentFingerprint after = CaptureDocumentFingerprint(hwp);
    const std::uint64_t fileSize =
        SUCCEEDED(afterPathStatus) ? FileSize(afterPath) : 0;
    const bool saveCompleted = SUCCEEDED(saveStatus) &&
        postSaveModified == 0 &&
        (saveReturn == 1 || (before.state.modified == 0 && saveReturn == 0));
    const bool verified =
        SUCCEEDED(pathStatus) &&
        SUCCEEDED(afterPathStatus) &&
        saveCompleted &&
        SamePath(path, afterPath) &&
        SameFingerprint(before, after) &&
        after.state.modified == 0;
    const long long elapsedMicroseconds =
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count();

    std::wostringstream output;
    output << L"HLS1\t" << (verified ? 1 : 0) << L'\t'
           << EncodeUtf8Base64(afterPath)
           << L'\t' << before.state.pageCount
           << L'\t' << before.state.modified
           << L'\t' << before.state.controlCount
           << L'\t' << before.state.controlHash
           << L'\t' << before.textHash
           << L'\t' << before.documentHash
           << L'\t' << saveStatus
           << L'\t' << saveReturn
           << L'\t' << postSaveModified
           << L'\t' << after.state.pageCount
           << L'\t' << after.state.modified
           << L'\t' << after.state.controlCount
           << L'\t' << after.state.controlHash
           << L'\t' << after.textHash
           << L'\t' << after.documentHash
           << L'\t' << fileSize
           << L'\t' << elapsedMicroseconds;
    return output.str();
}

std::wstring SaveReopenVerify(IDispatch* const hwp) {
    const auto started = std::chrono::steady_clock::now();
    std::wstring path;
    const HRESULT pathStatus = ReadFullName(hwp, &path);
    const DocumentFingerprint before = CaptureDocumentFingerprint(hwp);
    std::wstring recoveryBlock;
    const bool recoveryCaptured = ReadDocumentBlock(hwp, &recoveryBlock);

    HRESULT saveStatus = E_PENDING;
    LONG saveReturn = -1;
    LONG postSaveModified = -1;
    HRESULT clearStatus = E_PENDING;
    LONG clearReturn = -1;
    HRESULT openStatus = E_PENDING;
    LONG openReturn = -1;
    HRESULT recoveryStatus = E_PENDING;
    LONG recoveryReturn = -1;
    bool saveCompleted = false;

    if (FAILED(pathStatus) || path.empty()) {
        saveStatus = FAILED(pathStatus) ? pathStatus : E_INVALIDARG;
    } else {
        CComVariant saveIfDirty;
        saveIfDirty.vt = VT_BOOL;
        saveIfDirty.boolVal = VARIANT_TRUE;
        CComVariant rawSaveReturn;
        saveStatus = Method(hwp, L"Save", {saveIfDirty}, &rawSaveReturn);
        saveReturn = BooleanReturn(rawSaveReturn);
        if (SUCCEEDED(saveStatus)) {
            postSaveModified = ReadModified(hwp);
        }
        saveCompleted = SUCCEEDED(saveStatus) && postSaveModified == 0 &&
            (saveReturn == 1 || (before.state.modified == 0 && saveReturn == 0));
        if (saveCompleted) {
            CComVariant discard;
            discard.vt = VT_I2;
            discard.iVal = 1;
            CComVariant rawClearReturn;
            clearStatus = Method(hwp, L"Clear", {discard}, &rawClearReturn);
            clearReturn = BooleanReturn(rawClearReturn);
            if (SUCCEEDED(clearStatus)) {
                CComVariant rawOpenReturn;
                openStatus = Method(
                    hwp,
                    L"Open",
                    {CComVariant(path.c_str()), CComVariant(L"HWP"),
                     CComVariant(L"lock:FALSE")},
                    &rawOpenReturn);
                openReturn = BooleanReturn(rawOpenReturn);
                if ((FAILED(openStatus) || openReturn != 1) && recoveryCaptured) {
                    CComVariant rawRecoveryReturn;
                    recoveryStatus = Method(
                        hwp,
                        L"SetTextFile",
                        {
                            CComVariant(recoveryBlock.c_str()),
                            CComVariant(L"HWP"),
                            CComVariant(L""),
                        },
                        &rawRecoveryReturn);
                    recoveryReturn = BooleanReturn(rawRecoveryReturn);
                }
            }
        }
    }

    std::wstring afterPath;
    const HRESULT afterPathStatus = ReadFullName(hwp, &afterPath);
    const DocumentFingerprint after = CaptureDocumentFingerprint(hwp);
    const std::wstring encodedPath = EncodeUtf8Base64(afterPath);
    const bool openCompleted = SUCCEEDED(openStatus) && openReturn == 1;
    const bool recovered = !openCompleted &&
        SUCCEEDED(recoveryStatus) &&
        recoveryReturn == 1 &&
        SameFingerprint(before, after);
    const bool verified = SUCCEEDED(pathStatus) && SUCCEEDED(afterPathStatus) &&
        saveCompleted && SUCCEEDED(clearStatus) && openCompleted &&
        after.state.modified == 0 && SameFingerprint(before, after) &&
        !encodedPath.empty() &&
        SamePath(path, afterPath);
    const long long elapsedMicroseconds =
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count();

    std::wostringstream output;
    output << L"HCL12\t" << (verified ? 1 : 0) << L'\t' << encodedPath
           << L'\t' << before.state.pageCount
           << L'\t' << before.state.modified
           << L'\t' << before.state.controlCount
           << L'\t' << before.state.controlHash
           << L'\t' << before.textHash
           << L'\t' << before.documentHash
           << L'\t' << saveStatus
           << L'\t' << saveReturn
           << L'\t' << postSaveModified
           << L'\t' << clearStatus
           << L'\t' << clearReturn
           << L'\t' << openStatus
           << L'\t' << openReturn
           << L'\t' << (recovered ? 1 : 0)
           << L'\t' << recoveryStatus
           << L'\t' << recoveryReturn
           << L'\t' << after.state.pageCount
           << L'\t' << after.state.modified
           << L'\t' << after.state.controlCount
           << L'\t' << after.state.controlHash
           << L'\t' << after.textHash
           << L'\t' << after.documentHash
           << L'\t' << elapsedMicroseconds;
    return output.str();
}

}

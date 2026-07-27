#include "DocumentLifecycle.h"

#include "DispatchInvoke.h"
#include "OfficialApiState.h"
#include "ProtocolEncoding.h"

#include <Windows.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

namespace hancom::lifecycle {
namespace {

using hancom::encoding::EncodeUtf8Base64;

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

std::wstring DocumentFormatForPath(const std::wstring& path) {
    const size_t separator = path.find_last_of(L"\\/");
    const size_t dot = path.find_last_of(L'.');
    if (dot == std::wstring::npos ||
        (separator != std::wstring::npos && dot < separator)) {
        return L"";
    }
    const std::wstring extension = path.substr(dot);
    if (CompareStringOrdinal(
            extension.c_str(),
            static_cast<int>(extension.size()),
            L".hwp",
            4,
            TRUE) == CSTR_EQUAL) {
        return L"HWP";
    }
    if (CompareStringOrdinal(
            extension.c_str(),
            static_cast<int>(extension.size()),
            L".hwpx",
            5,
            TRUE) == CSTR_EQUAL) {
        return L"HWPX";
    }
    return L"";
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

void AppendSectionDiagnostics(
    std::wostream& output,
    const DocumentFingerprint& fingerprint) {
    for (size_t index = 0; index < fingerprint.documentSections.size(); ++index) {
        if (index != 0) {
            output << L',';
        }
        const auto& section = fingerprint.documentSections[index];
        output << std::hex << std::nouppercase << std::setfill(L'0')
               << std::setw(16) << section.hash;
    }
    output << std::dec << std::setfill(L' ');
}

void AppendFingerprintDiagnostics(
    std::wostream& output,
    const DocumentFingerprint& before,
    const DocumentFingerprint& after) {
    output << L'\t' << before.documentLength
           << L'\t' << after.documentLength
           << L'\t';
    AppendSectionDiagnostics(output, before);
    output << L'\t';
    AppendSectionDiagnostics(output, after);
}

struct FileEvidence {
    bool captured = false;
    std::uint64_t size = 0;
    std::uint64_t writeTime100ns = 0;
};

FileEvidence ReadFileEvidence(const std::wstring& path) noexcept {
    WIN32_FILE_ATTRIBUTE_DATA attributes = {};
    if (!GetFileAttributesExW(
            path.c_str(),
            GetFileExInfoStandard,
            &attributes) ||
        (attributes.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        return {};
    }
    ULARGE_INTEGER writeTime = {};
    writeTime.HighPart = attributes.ftLastWriteTime.dwHighDateTime;
    writeTime.LowPart = attributes.ftLastWriteTime.dwLowDateTime;
    return FileEvidence{
        true,
        (static_cast<std::uint64_t>(attributes.nFileSizeHigh) << 32) |
            static_cast<std::uint64_t>(attributes.nFileSizeLow),
        writeTime.QuadPart,
    };
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
    const FileEvidence fileEvidence =
        SUCCEEDED(afterPathStatus) ? ReadFileEvidence(afterPath) : FileEvidence{};
    const bool saveCompleted = SUCCEEDED(saveStatus) &&
        postSaveModified == 0 &&
        (saveReturn == 1 || (before.state.modified == 0 && saveReturn == 0));
    const bool verified =
        SUCCEEDED(pathStatus) &&
        SUCCEEDED(afterPathStatus) &&
        saveCompleted &&
        SamePath(path, afterPath) &&
        SameFingerprint(before, after) &&
        after.state.modified == 0 &&
        fileEvidence.captured &&
        fileEvidence.size > 0 &&
        fileEvidence.writeTime100ns > 0;
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
           << L'\t' << fileEvidence.size
           << L'\t' << fileEvidence.writeTime100ns
           << L'\t' << elapsedMicroseconds;
    AppendFingerprintDiagnostics(output, before, after);
    return output.str();
}

std::wstring SaveReopenVerify(IDispatch* const hwp) {
    const auto started = std::chrono::steady_clock::now();
    std::wstring path;
    const HRESULT pathStatus = ReadFullName(hwp, &path);
    const std::wstring documentFormat = DocumentFormatForPath(path);
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
    bool openCompleted = false;
    bool reopenMatched = false;
    bool recoveryAttempted = false;
    std::wstring afterPath;
    HRESULT afterPathStatus = E_PENDING;
    DocumentFingerprint after;

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
            if (documentFormat.empty()) {
                clearStatus = E_INVALIDARG;
            } else if (!before.captured || !recoveryCaptured) {
                clearStatus = E_ABORT;
            } else {
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
                        {
                            CComVariant(path.c_str()),
                            CComVariant(documentFormat.c_str()),
                            CComVariant(L"lock:FALSE"),
                        },
                        &rawOpenReturn);
                    openReturn = BooleanReturn(rawOpenReturn);
                    openCompleted = SUCCEEDED(openStatus) && openReturn == 1;
                    if (openCompleted) {
                        afterPathStatus = ReadFullName(hwp, &afterPath);
                        after = CaptureDocumentFingerprint(hwp);
                        reopenMatched =
                            SUCCEEDED(afterPathStatus) &&
                            SamePath(path, afterPath) &&
                            after.state.modified == 0 &&
                            SameFingerprint(before, after);
                    }
                    if (!reopenMatched) {
                        recoveryAttempted = true;
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
                        if (SUCCEEDED(recoveryStatus) &&
                            recoveryReturn == 1) {
                            CComVariant rawRecoverySaveReturn;
                            recoveryStatus = Method(
                                hwp,
                                L"SaveAs",
                                {
                                    CComVariant(path.c_str()),
                                    CComVariant(documentFormat.c_str()),
                                    CComVariant(L""),
                                },
                                &rawRecoverySaveReturn);
                            recoveryReturn =
                                BooleanReturn(rawRecoverySaveReturn);
                        }
                        afterPathStatus = ReadFullName(hwp, &afterPath);
                        after = CaptureDocumentFingerprint(hwp);
                    }
                }
            }
        }
    }

    if (afterPathStatus == E_PENDING) {
        afterPathStatus = ReadFullName(hwp, &afterPath);
        after = CaptureDocumentFingerprint(hwp);
    }
    const std::wstring encodedPath = EncodeUtf8Base64(afterPath);
    const bool recovered = recoveryAttempted &&
        SUCCEEDED(recoveryStatus) &&
        recoveryReturn == 1 &&
        SUCCEEDED(afterPathStatus) &&
        SamePath(path, afterPath) &&
        after.state.modified == 0 &&
        SameFingerprint(before, after);
    const bool verified = SUCCEEDED(pathStatus) && SUCCEEDED(afterPathStatus) &&
        saveCompleted && SUCCEEDED(clearStatus) && openCompleted &&
        reopenMatched && !recoveryAttempted &&
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
    AppendFingerprintDiagnostics(output, before, after);
    return output.str();
}

}

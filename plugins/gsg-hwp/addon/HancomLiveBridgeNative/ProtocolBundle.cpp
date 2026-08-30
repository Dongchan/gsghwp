#include "ProtocolBundle.h"

#include "ActionExecutor.h"
#include "ActionProtocol.h"
#include "DispatchInvoke.h"
#include "DocumentLifecycle.h"
#include "LiveInspection.h"
#include "OfficialApiProbe.h"
#include "OfficialApiState.h"
#include "ProtocolEncoding.h"

#include <WinCrypt.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <chrono>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <exception>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#pragma comment(lib, "Advapi32.lib")

namespace hancom::protocol_bundle {
namespace {

constexpr size_t kMaximumPayloadCharacters = 8U * 1024U * 1024U;
constexpr wchar_t kCapabilitiesRevision[] = L"native-0.5.169-protocol-14";

struct Request {
    std::wstring requestId;
    std::wstring operationId;
    std::wstring operation;
    LONG documentId = 0;
    std::wstring documentPath;
    std::wstring expectedSignature;
    std::map<std::wstring, std::wstring> arguments;
    std::wstring payload;
};

struct Receipt {
    std::wstring requestId;
    std::wstring operationId;
    std::wstring operation = L"DIAGNOSE";
    std::wstring status = L"ok";
    std::wstring stage = L"verify";
    long long elapsedMicroseconds = 0;
    DWORD processId = GetCurrentProcessId();
    ULONG_PTR windowHandle = 0;
    LONG documentId = 0;
    std::wstring documentPath;
    LONG routeGeneration = 0;
    std::wstring beforeSignature;
    std::wstring afterSignature;
    size_t commandsRequested = 0;
    size_t commandsCompleted = 0;
    long long failedCommandIndex = -1;
    bool partialMutation = false;
    bool retrySafe = true;
    bool reconcileRequired = false;
    bool rollbackAttempted = false;
    int rollbackSucceeded = -1;
    bool undoAvailable = false;
    std::wstring resultWireFormat;
    std::wstring resultPayload;
    std::wstring artifactPath;
    std::wstring artifactSha256;
    unsigned long long eventSequenceBefore = 0;
    unsigned long long eventSequenceAfter = 0;
    std::wstring errorCode;
    std::wstring errorMessage;
    HRESULT nativeHresult = S_OK;
};

std::vector<std::wstring> Split(
    const std::wstring& value,
    const wchar_t delimiter) {
    std::vector<std::wstring> fields;
    size_t start = 0;
    while (true) {
        const size_t end = value.find(delimiter, start);
        fields.push_back(value.substr(start, end - start));
        if (end == std::wstring::npos) {
            return fields;
        }
        start = end + 1;
    }
}

bool ParseLong(const std::wstring& raw, LONG* const value) noexcept {
    if (value == nullptr || raw.empty()) {
        return false;
    }
    wchar_t* end = nullptr;
    errno = 0;
    const long parsed = std::wcstol(raw.c_str(), &end, 10);
    if (errno != 0 || end == raw.c_str() || *end != L'\0') {
        return false;
    }
    *value = parsed;
    return true;
}

bool Decode(
    const std::wstring& raw,
    std::wstring* const decoded) noexcept {
    if (raw.empty()) {
        if (decoded == nullptr) {
            return false;
        }
        decoded->clear();
        return true;
    }
    return hancom::encoding::DecodeUtf8Base64(raw, decoded);
}

bool ParseRequest(
    const std::wstring& raw,
    Request* const request,
    std::wstring* const error) {
    if (request == nullptr || error == nullptr ||
        raw.size() > kMaximumPayloadCharacters) {
        return false;
    }
    std::wistringstream input(raw);
    std::wstring line;
    bool header = false;
    bool ended = false;
    while (std::getline(input, line)) {
        if (!line.empty() && line.back() == L'\r') {
            line.pop_back();
        }
        if (!header) {
            header = line == L"HLB1";
            if (!header) {
                *error = L"invalid HLB1 header";
                return false;
            }
            continue;
        }
        if (line == L"END") {
            ended = true;
            break;
        }
        const std::vector<std::wstring> fields = Split(line, L'\t');
        if (fields.empty()) {
            continue;
        }
        if (fields[0] == L"REQUEST" && fields.size() == 2) {
            if (!Decode(fields[1], &request->requestId)) {
                *error = L"invalid request id";
                return false;
            }
        } else if (fields[0] == L"OPERATION_ID" && fields.size() == 2) {
            if (!Decode(fields[1], &request->operationId)) {
                *error = L"invalid operation id";
                return false;
            }
        } else if (fields[0] == L"OP" && fields.size() == 2) {
            request->operation = fields[1];
        } else if (fields[0] == L"DOC" && fields.size() == 3) {
            if (!ParseLong(fields[1], &request->documentId) ||
                !Decode(fields[2], &request->documentPath)) {
                *error = L"invalid document route";
                return false;
            }
        } else if (fields[0] == L"EXPECTED" && fields.size() == 2) {
            if (!Decode(fields[1], &request->expectedSignature)) {
                *error = L"invalid expected signature";
                return false;
            }
        } else if (fields[0] == L"ARG" && fields.size() == 3) {
            std::wstring key;
            std::wstring value;
            if (!Decode(fields[1], &key) || key.empty() ||
                !Decode(fields[2], &value)) {
                *error = L"invalid operation argument";
                return false;
            }
            request->arguments.insert_or_assign(std::move(key), std::move(value));
        } else if (fields[0] == L"PAYLOAD" && fields.size() == 2) {
            if (!Decode(fields[1], &request->payload)) {
                *error = L"invalid operation payload";
                return false;
            }
        } else {
            *error = L"unknown or malformed HLB1 record";
            return false;
        }
    }
    if (!ended || request->requestId.empty() || request->operationId.empty() ||
        request->operation.empty()) {
        *error = L"incomplete HLB1 request";
        return false;
    }
    return true;
}

std::wstring Argument(
    const Request& request,
    const std::wstring& name,
    const std::wstring& fallback = L"") {
    const auto found = request.arguments.find(name);
    return found == request.arguments.end() ? fallback : found->second;
}

HRESULT ResolveActiveDocument(
    IDispatch* const hwp,
    CComPtr<IDispatch>& document) noexcept {
    CComVariant rawDocuments;
    HRESULT status =
        hancom::dispatch::PropertyGet(hwp, L"XHwpDocuments", &rawDocuments);
    CComPtr<IDispatch> documents;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawDocuments, documents);
    }
    CComVariant rawDocument;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            documents,
            L"Active_XHwpDocument",
            &rawDocument);
    }
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsDispatch(rawDocument, document);
    }
    return status;
}

std::wstring ReadDocumentPath(IDispatch* const hwp) {
    CComPtr<IDispatch> document;
    if (FAILED(ResolveActiveDocument(hwp, document))) {
        return L"";
    }
    CComVariant raw;
    std::wstring path;
    if (FAILED(hancom::dispatch::PropertyGet(document, L"FullName", &raw)) ||
        FAILED(hancom::dispatch::AsString(raw, &path))) {
        return L"";
    }
    return path;
}

std::wstring CaptureSignature(IDispatch* const hwp) {
    return hancom::official_api::FormatDocumentContentSignature(
        hancom::official_api::CaptureDocumentContentSignature(hwp));
}

void Fail(
    Receipt* const receipt,
    const std::wstring& stage,
    const std::wstring& code,
    const std::wstring& message,
    const HRESULT status = E_FAIL) {
    receipt->status = L"error";
    receipt->stage = stage;
    receipt->errorCode = code;
    receipt->errorMessage = message;
    receipt->nativeHresult = status;
}

bool ParseLongLong(const std::wstring& raw, long long* const value) noexcept {
    if (value == nullptr || raw.empty()) {
        return false;
    }
    wchar_t* end = nullptr;
    errno = 0;
    const long long parsed = std::wcstoll(raw.c_str(), &end, 10);
    if (errno != 0 || end == raw.c_str() || *end != L'\0') {
        return false;
    }
    *value = parsed;
    return true;
}

bool LegacyBoolean(const std::wstring& raw, bool* const value) noexcept {
    if (value == nullptr) {
        return false;
    }
    if (raw == L"0") {
        *value = false;
        return true;
    }
    if (raw == L"1") {
        *value = true;
        return true;
    }
    return false;
}

bool LegacyOptionalNonnegative(
    const std::wstring& raw,
    long long* const value) noexcept {
    long long parsed = 0;
    if (!ParseLongLong(raw, &parsed) || parsed < -1) {
        return false;
    }
    *value = parsed;
    return true;
}

bool LegacyReturn(const std::wstring& raw, long long* const value) noexcept {
    long long parsed = 0;
    if (!ParseLongLong(raw, &parsed) ||
        (parsed != -1 && parsed != 0 && parsed != 1)) {
        return false;
    }
    *value = parsed;
    return true;
}

bool LegacyHashPresent(const std::wstring& raw) noexcept {
    if (raw == L"0" || raw.empty()) {
        return false;
    }
    for (const wchar_t character : raw) {
        if (character < L'0' || character > L'9') {
            return false;
        }
    }
    return true;
}

bool LegacyDecodedPathPresent(const std::wstring& raw) noexcept {
    std::wstring decoded;
    return !raw.empty() && Decode(raw, &decoded) && !decoded.empty();
}

bool LegacySaveReceiptSucceeded(const std::vector<std::wstring>& fields) noexcept {
    if (fields.size() != 20 && fields.size() != 21 && fields.size() != 25) {
        return false;
    }
    bool verified = false;
    bool beforeModified = false;
    bool postSaveModified = false;
    bool afterModified = false;
    long long beforePageCount = 0;
    long long beforeControlCount = 0;
    long long saveHresult = 0;
    long long saveReturn = 0;
    long long afterPageCount = 0;
    long long afterControlCount = 0;
    long long fileSize = 0;
    long long elapsedMicroseconds = 0;
    const size_t baseFieldCount = fields.size() == 25 ? 21 : fields.size();
    if (fields[0] != L"HLS1" ||
        !LegacyBoolean(fields[1], &verified) ||
        !LegacyDecodedPathPresent(fields[2]) ||
        !LegacyOptionalNonnegative(fields[3], &beforePageCount) ||
        !LegacyBoolean(fields[4], &beforeModified) ||
        !LegacyOptionalNonnegative(fields[5], &beforeControlCount) ||
        !ParseLongLong(fields[9], &saveHresult) ||
        !LegacyReturn(fields[10], &saveReturn) ||
        !LegacyBoolean(fields[11], &postSaveModified) ||
        !LegacyOptionalNonnegative(fields[12], &afterPageCount) ||
        !LegacyBoolean(fields[13], &afterModified) ||
        !LegacyOptionalNonnegative(fields[14], &afterControlCount) ||
        !ParseLongLong(fields[18], &fileSize) ||
        !ParseLongLong(fields[baseFieldCount - 1], &elapsedMicroseconds)) {
        return false;
    }
    long long fileWriteTime100ns = 1;
    if (baseFieldCount == 21 &&
        !ParseLongLong(fields[19], &fileWriteTime100ns)) {
        return false;
    }
    return verified && beforePageCount > 0 && afterPageCount > 0 &&
        saveHresult >= 0 &&
        (saveReturn == 1 || (!beforeModified && saveReturn == 0)) &&
        !postSaveModified && !afterModified &&
        beforePageCount == afterPageCount &&
        beforeControlCount == afterControlCount && fields[6] == fields[15] &&
        LegacyHashPresent(fields[7]) && fields[7] == fields[16] &&
        LegacyHashPresent(fields[8]) && fields[8] == fields[17] &&
        fileSize > 0 && fileWriteTime100ns > 0 && elapsedMicroseconds >= 0;
}

bool LegacySaveReopenReceiptSucceeded(
    const std::vector<std::wstring>& fields) noexcept {
    if (fields.size() != 26 && fields.size() != 30) {
        return false;
    }
    bool verified = false;
    bool beforeModified = false;
    bool postSaveModified = false;
    bool recovered = false;
    bool afterModified = false;
    long long beforePageCount = 0;
    long long beforeControlCount = 0;
    long long saveHresult = 0;
    long long saveReturn = 0;
    long long clearHresult = 0;
    long long openHresult = 0;
    long long openReturn = 0;
    long long afterPageCount = 0;
    long long afterControlCount = 0;
    long long elapsedMicroseconds = 0;
    if (fields[0] != L"HCL12" ||
        !LegacyBoolean(fields[1], &verified) ||
        !LegacyDecodedPathPresent(fields[2]) ||
        !LegacyOptionalNonnegative(fields[3], &beforePageCount) ||
        !LegacyBoolean(fields[4], &beforeModified) ||
        !LegacyOptionalNonnegative(fields[5], &beforeControlCount) ||
        !ParseLongLong(fields[9], &saveHresult) ||
        !LegacyReturn(fields[10], &saveReturn) ||
        !LegacyBoolean(fields[11], &postSaveModified) ||
        !ParseLongLong(fields[12], &clearHresult) ||
        !ParseLongLong(fields[14], &openHresult) ||
        !LegacyReturn(fields[15], &openReturn) ||
        !LegacyBoolean(fields[16], &recovered) ||
        !LegacyOptionalNonnegative(fields[19], &afterPageCount) ||
        !LegacyBoolean(fields[20], &afterModified) ||
        !LegacyOptionalNonnegative(fields[21], &afterControlCount) ||
        !ParseLongLong(fields[25], &elapsedMicroseconds)) {
        return false;
    }
    return verified && beforePageCount > 0 && afterPageCount > 0 &&
        saveHresult >= 0 &&
        (saveReturn == 1 || (!beforeModified && saveReturn == 0)) &&
        !postSaveModified && clearHresult >= 0 && openHresult >= 0 &&
        openReturn == 1 && !recovered && !afterModified &&
        beforePageCount == afterPageCount &&
        beforeControlCount == afterControlCount && fields[6] == fields[22] &&
        LegacyHashPresent(fields[7]) && fields[7] == fields[23] &&
        LegacyHashPresent(fields[8]) && fields[8] == fields[24] &&
        elapsedMicroseconds >= 0;
}

bool LegacyLifecycleReceiptSucceeded(const std::wstring& payload) noexcept {
    const std::vector<std::wstring> fields = Split(payload, L'\t');
    if (fields.empty()) {
        return false;
    }
    if (fields[0] == L"HLS1") {
        return LegacySaveReceiptSucceeded(fields);
    }
    if (fields[0] == L"HCL12") {
        return LegacySaveReopenReceiptSucceeded(fields);
    }
    return false;
}

void FailLegacyLifecycleReceipt(
    Receipt* const receipt,
    const std::wstring& stage,
    const std::wstring& code) {
    Fail(receipt, stage, code, receipt->resultPayload);
    receipt->retrySafe = false;
    receipt->reconcileRequired = true;
}

std::wstring History(
    IDispatch* const hwp,
    const std::wstring& direction,
    const std::wstring& expectedSignature) {
    const wchar_t* method = direction == L"undo"
        ? L"Undo"
        : direction == L"redo" ? L"Redo" : nullptr;
    if (method == nullptr) {
        return L"HCH1\tERROR\tBAD_DIRECTION";
    }
    if (!hancom::official_api::DocumentContentSignatureIsComplete(
            expectedSignature)) {
        return L"HCH1\tERROR\tBAD_SIGNATURE";
    }
    const std::wstring before = CaptureSignature(hwp);
    if (before != expectedSignature) {
        return L"HCH1\tERROR\tSTALE_CONTENT";
    }
    CComPtr<IDispatch> document;
    if (FAILED(ResolveActiveDocument(hwp, document))) {
        return L"HCH1\tERROR\tACTIVE_DOCUMENT";
    }
    const auto started = std::chrono::steady_clock::now();
    CComVariant raw;
    HRESULT status =
        hancom::dispatch::Method(document, method, {CComVariant(1L)}, &raw);
    bool applied = false;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::AsBool(raw, &applied);
    }
    if (FAILED(status)) {
        return L"HCH1\tERROR\tHISTORY_INVOKE";
    }
    const std::wstring after = CaptureSignature(hwp);
    if (!applied || !hancom::official_api::DocumentContentSignatureIsComplete(after) ||
        before == after) {
        return L"HCH1\tERROR\tHISTORY_NO_CONTENT_CHANGE";
    }
    const long long elapsed =
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count();
    std::wostringstream output;
    output << L"HCH1\tOK\t" << (applied ? 1 : 0) << L'\t' << elapsed
           << L'\t' << before << L'\t' << after;
    return output.str();
}

bool HashFileSha256(
    const std::wstring& path,
    std::wstring* const digest) noexcept {
    if (digest == nullptr) {
        return false;
    }
    std::ifstream input(std::filesystem::path(path), std::ios::binary);
    if (!input) {
        return false;
    }
    HCRYPTPROV provider = 0;
    HCRYPTHASH hash = 0;
    if (!CryptAcquireContextW(
            &provider,
            nullptr,
            nullptr,
            PROV_RSA_AES,
            CRYPT_VERIFYCONTEXT) ||
        !CryptCreateHash(provider, CALG_SHA_256, 0, 0, &hash)) {
        if (provider != 0) {
            CryptReleaseContext(provider, 0);
        }
        return false;
    }
    std::vector<BYTE> buffer(64U * 1024U);
    bool succeeded = true;
    while (input) {
        input.read(
            reinterpret_cast<char*>(buffer.data()),
            static_cast<std::streamsize>(buffer.size()));
        const std::streamsize count = input.gcount();
        if (count > 0 && !CryptHashData(
                hash,
                buffer.data(),
                static_cast<DWORD>(count),
                0)) {
            succeeded = false;
            break;
        }
    }
    BYTE bytes[32]{};
    DWORD size = sizeof(bytes);
    if (succeeded) {
        succeeded = CryptGetHashParam(hash, HP_HASHVAL, bytes, &size, 0) != FALSE;
    }
    CryptDestroyHash(hash);
    CryptReleaseContext(provider, 0);
    if (!succeeded || size != sizeof(bytes)) {
        return false;
    }
    static constexpr wchar_t hex[] = L"0123456789abcdef";
    std::wstring result;
    result.reserve(64);
    for (const BYTE value : bytes) {
        result.push_back(hex[value >> 4]);
        result.push_back(hex[value & 0x0F]);
    }
    *digest = std::move(result);
    return true;
}

std::wstring Render(
    IDispatch* const hwp,
    const Request& request,
    Receipt* const receipt) {
    LONG page = 0;
    LONG dpi = 0;
    const std::wstring path = Argument(request, L"path");
    if (path.empty() ||
        !ParseLong(Argument(request, L"page"), &page) ||
        !ParseLong(Argument(request, L"dpi"), &dpi) ||
        page < 0 || dpi < 72 || dpi > 600) {
        Fail(receipt, L"render", L"BAD_RENDER_ARGUMENT", L"path/page/dpi is invalid");
        return L"HLR1\tERROR\tBAD_RENDER_ARGUMENT";
    }
    std::filesystem::path artifactPath(path);
    artifactPath.replace_extension(L".bmp");
    CComVariant raw;
    const HRESULT status = hancom::dispatch::Method(
        hwp,
        L"CreatePageImage",
        {
            CComVariant(artifactPath.c_str()),
            CComVariant(page),
            CComVariant(static_cast<SHORT>(dpi)),
            CComVariant(static_cast<SHORT>(24)),
            CComVariant(L"bmp"),
        },
        &raw);
    bool rendered = false;
    if (SUCCEEDED(status)) {
        static_cast<void>(hancom::dispatch::AsBool(raw, &rendered));
    }
    receipt->artifactPath = artifactPath.wstring();
    if (FAILED(status) || !rendered ||
        !HashFileSha256(receipt->artifactPath, &receipt->artifactSha256)) {
        Fail(
            receipt,
            L"render",
            L"RENDER_FAILED",
            L"CreatePageImage did not produce a verifiable artifact",
            FAILED(status) ? status : E_FAIL);
        return L"HLR1\tERROR\tRENDER_FAILED";
    }
    std::wostringstream output;
    output << L"HLR1\tOK\t" << page << L'\t' << dpi << L'\t'
           << hancom::encoding::EncodeUtf8Base64(receipt->artifactPath) << L'\t'
           << receipt->artifactSha256;
    return output.str();
}

std::wstring Observe(
    IDispatch* const hwp,
    const Request& request,
    std::wstring* const wireFormat) {
    const std::wstring kind = Argument(request, L"kind", L"snapshot");
    LONG page = 1;
    if (kind == L"snapshot") {
        *wireFormat = L"HCS1";
        return hancom::inspection::Snapshot(hwp);
    }
    if (kind == L"structure") {
        static_cast<void>(ParseLong(Argument(request, L"page", L"1"), &page));
        *wireFormat = L"HDS1";
        return hancom::inspection::InspectStructure(hwp, page);
    }
    if (kind == L"page") {
        static_cast<void>(ParseLong(Argument(request, L"page", L"1"), &page));
        *wireFormat = L"HPI1";
        return hancom::inspection::InspectPageV3(hwp, page);
    }
    if (kind == L"pages") {
        *wireFormat = L"HPM1";
        return hancom::inspection::InspectPagesV3(hwp, request.payload);
    }
    if (kind == L"routing") {
        static_cast<void>(ParseLong(Argument(request, L"page", L"1"), &page));
        *wireFormat = L"HRC1";
        return hancom::inspection::InspectRoutingContext(hwp, page);
    }
    if (kind == L"paragraph_styles") {
        *wireFormat = L"HPS1";
        return hancom::inspection::InspectParagraphStyles(hwp, request.payload);
    }
    if (kind == L"official_api") {
        *wireFormat = L"HCV1";
        return hancom::official_api::Probe(hwp, request.payload);
    }
    if (kind == L"signature") {
        *wireFormat = L"HSG1";
        return CaptureSignature(hwp);
    }
    wireFormat->clear();
    return L"";
}

std::wstring Capabilities() {
    return
        L"observation=HCS1,HPI1,HPM1,HDS1,HPS1,HRC1,HSG1;"
        L"mutation=HCA2;history=HCH1;lifecycle=HLS1,HCL12;"
        L"render=HLR1;official_api=HCV1;"
        L"events=HEV1(external,document_id);"
        L"diagnostics=route,signature,hresult,stage,reconcile";
}

std::wstring FormatReceipt(const Receipt& receipt) {
    std::wostringstream output;
    output << L"HLB1\nMETA\t14\t"
           << hancom::encoding::EncodeUtf8Base64(receipt.requestId) << L'\t'
           << hancom::encoding::EncodeUtf8Base64(receipt.operationId) << L'\t'
           << receipt.operation << L'\t' << receipt.status << L'\t'
           << receipt.stage << L'\t' << receipt.elapsedMicroseconds
           << L"\nROUTE\t" << receipt.processId << L'\t'
           << receipt.windowHandle << L'\t' << receipt.documentId << L'\t'
           << hancom::encoding::EncodeUtf8Base64(receipt.documentPath) << L'\t'
           << receipt.routeGeneration
           << L"\nSIGNATURE\t"
           << hancom::encoding::EncodeUtf8Base64(receipt.beforeSignature) << L'\t'
           << hancom::encoding::EncodeUtf8Base64(receipt.afterSignature)
           << L"\nMUTATION\t" << receipt.commandsRequested << L'\t'
           << receipt.commandsCompleted << L'\t' << receipt.failedCommandIndex
           << L'\t' << (receipt.partialMutation ? 1 : 0) << L'\t'
           << (receipt.retrySafe ? 1 : 0) << L'\t'
           << (receipt.reconcileRequired ? 1 : 0) << L'\t'
           << (receipt.rollbackAttempted ? 1 : 0) << L'\t'
           << receipt.rollbackSucceeded << L'\t'
           << (receipt.undoAvailable ? 1 : 0)
           << L"\nRESULT\t"
           << hancom::encoding::EncodeUtf8Base64(receipt.resultWireFormat) << L'\t'
           << hancom::encoding::EncodeUtf8Base64(receipt.resultPayload)
           << L"\nARTIFACT\t"
           << hancom::encoding::EncodeUtf8Base64(receipt.artifactPath) << L'\t'
           << hancom::encoding::EncodeUtf8Base64(receipt.artifactSha256)
           << L"\nEVENTS\t" << receipt.eventSequenceBefore << L'\t'
           << receipt.eventSequenceAfter
           << L"\nCAPABILITIES\t"
           << hancom::encoding::EncodeUtf8Base64(kCapabilitiesRevision)
           << L"\nERROR\t"
           << hancom::encoding::EncodeUtf8Base64(receipt.errorCode) << L'\t'
           << hancom::encoding::EncodeUtf8Base64(receipt.errorMessage) << L'\t'
           << static_cast<LONG>(receipt.nativeHresult)
           << L"\nEND";
    return output.str();
}

void ExecuteOperation(
    IDispatch* const hwp,
    const Request& request,
    Receipt* const receipt) {
    if (request.operation == L"OBSERVE") {
        receipt->stage = L"observe";
        receipt->resultPayload =
            Observe(hwp, request, &receipt->resultWireFormat);
        if (receipt->resultWireFormat.empty()) {
            Fail(receipt, L"observe", L"UNKNOWN_OBSERVATION", L"unknown observation kind");
        }
        return;
    }
    if (request.operation == L"MUTATE") {
        receipt->stage = L"mutation";
        hancom::actions::Request actionRequest;
        hancom::actions::Error actionError;
        if (!hancom::actions::ParseRequest(
                request.payload,
                &actionRequest,
                &actionError)) {
            receipt->resultWireFormat = L"HCA2";
            receipt->resultPayload = hancom::actions::ErrorResponse(actionError);
            Fail(receipt, L"parse", actionError.code, actionError.message);
            return;
        }
        if (!request.expectedSignature.empty()) {
            if (!hancom::official_api::DocumentContentSignatureIsComplete(
                    request.expectedSignature)) {
                receipt->resultWireFormat = L"HCA2";
                receipt->resultPayload = L"HCA2\tERROR\tBAD_SIGNATURE";
                Fail(
                    receipt,
                    L"mutation",
                    L"BAD_SIGNATURE",
                    L"expected content signature is incomplete or malformed");
                return;
            }
            const std::wstring currentSignature = CaptureSignature(hwp);
            if (currentSignature != request.expectedSignature) {
                receipt->resultWireFormat = L"HCA2";
                receipt->resultPayload = L"HCA2\tERROR\tSTALE_CONTENT";
                Fail(
                    receipt,
                    L"mutation",
                    L"STALE_CONTENT",
                    L"live document content changed before mutation");
                return;
            }
        }
        actionRequest.expectedContentSignature.clear();
        actionRequest.requiresContentAuthorization = false;
        receipt->commandsRequested = actionRequest.commands.size();
        const hancom::actions::ExecutionResult execution =
            hancom::actions::Execute(hwp, actionRequest);
        receipt->commandsCompleted = execution.commandsExecuted;
        receipt->partialMutation = execution.partialMutation;
        receipt->retrySafe = execution.retrySafe;
        receipt->reconcileRequired =
            execution.partialMutation && !execution.retrySafe;
        receipt->failedCommandIndex = execution.succeeded
            ? -1
            : static_cast<long long>(execution.commandsExecuted);
        receipt->rollbackAttempted =
            actionRequest.atomic && !execution.succeeded && execution.partialMutation;
        if (receipt->rollbackAttempted) {
            receipt->rollbackSucceeded =
                !execution.structureDigestBefore.empty() &&
                execution.structureDigestBefore == execution.structureDigestAfter
                ? 1
                : 0;
        }
        receipt->afterSignature = CaptureSignature(hwp);
        const bool contentChanged =
            hancom::official_api::DocumentContentSignatureIsComplete(
                receipt->afterSignature) &&
            receipt->afterSignature != receipt->beforeSignature;
        const bool completeMutationSucceeded = execution.succeeded && contentChanged;
        receipt->undoAvailable = completeMutationSucceeded;
        if (completeMutationSucceeded) {
            receipt->partialMutation = false;
            receipt->retrySafe = true;
            receipt->reconcileRequired = false;
        }
        receipt->resultWireFormat = L"HCA2";
        receipt->resultPayload = execution.succeeded
            ? hancom::actions::SuccessResponse(execution)
            : hancom::actions::FailureResponse(execution);
        if (execution.succeeded && !contentChanged) {
            receipt->resultPayload = L"HCA2\tERROR\tMUTATION_NO_CONTENT_CHANGE";
            receipt->retrySafe = false;
            receipt->reconcileRequired = true;
            Fail(
                receipt,
                L"mutation",
                L"MUTATION_NO_CONTENT_CHANGE",
                L"mutation did not produce a complete changed content signature");
        } else if (!execution.succeeded) {
            Fail(
                receipt,
                L"mutation",
                execution.error.code,
                execution.error.message);
        }
        return;
    }
    if (request.operation == L"HISTORY") {
        receipt->stage = L"history";
        receipt->resultWireFormat = L"HCH1";
        receipt->resultPayload = History(
            hwp,
            Argument(request, L"direction"),
            request.expectedSignature);
        if (receipt->resultPayload == L"HCH1\tERROR\tHISTORY_NO_CONTENT_CHANGE") {
            Fail(
                receipt,
                L"history",
                L"HISTORY_NO_CONTENT_CHANGE",
                L"history invocation did not change complete document content");
        } else if (receipt->resultPayload.find(L"HCH1\tOK\t") != 0) {
            Fail(receipt, L"history", L"HISTORY_FAILED", receipt->resultPayload);
        } else {
            receipt->undoAvailable = true;
        }
        return;
    }
    if (request.operation == L"SAVE") {
        receipt->stage = L"save";
        receipt->resultWireFormat = L"HLS1";
        receipt->resultPayload = hancom::lifecycle::SaveVerify(hwp);
        if (!LegacyLifecycleReceiptSucceeded(receipt->resultPayload)) {
            FailLegacyLifecycleReceipt(receipt, L"save", L"SAVE_FAILED");
        }
        return;
    }
    if (request.operation == L"SAVE_REOPEN") {
        receipt->stage = L"reopen";
        receipt->resultWireFormat = L"HCL12";
        receipt->resultPayload = hancom::lifecycle::SaveReopenVerify(hwp);
        if (!LegacyLifecycleReceiptSucceeded(receipt->resultPayload)) {
            FailLegacyLifecycleReceipt(receipt, L"reopen", L"SAVE_REOPEN_FAILED");
        }
        return;
    }
    if (request.operation == L"RENDER") {
        receipt->stage = L"render";
        receipt->resultWireFormat = L"HLR1";
        receipt->resultPayload = Render(hwp, request, receipt);
        return;
    }
    if (request.operation == L"CAPABILITIES") {
        receipt->stage = L"capability";
        receipt->resultWireFormat = L"HLC1";
        receipt->resultPayload = Capabilities();
        return;
    }
    if (request.operation == L"DIAGNOSE") {
        receipt->stage = L"lifecycle";
        receipt->resultWireFormat = L"HLD1";
        std::wostringstream diagnostics;
        diagnostics << L"HLD1\tOK\t" << receipt->processId << L'\t'
                    << receipt->windowHandle << L'\t' << receipt->documentId
                    << L'\t' << hancom::encoding::EncodeUtf8Base64(receipt->documentPath)
                    << L'\t' << hancom::encoding::EncodeUtf8Base64(
                           L"process_ownership=user_attached;"
                           L"document_ownership=user_attached;"
                           L"close_policy=never;"
                           L"event_correlation=document_id")
                    << L'\t' << hancom::encoding::EncodeUtf8Base64(Capabilities());
        receipt->resultPayload = diagnostics.str();
        return;
    }
    Fail(receipt, L"capability", L"UNKNOWN_OPERATION", L"unknown HLB1 operation");
}

}

std::wstring Execute(
    IDispatch* const hwp,
    const LONG targetDocumentId,
    const HWND windowHandle,
    const std::wstring& payload) noexcept {
    const auto started = std::chrono::steady_clock::now();
    Receipt receipt;
    receipt.windowHandle = reinterpret_cast<ULONG_PTR>(windowHandle);
    receipt.documentId = targetDocumentId > 0 ? targetDocumentId : 0;
    Request request;
    std::wstring parseError;
    try {
        if (!ParseRequest(payload, &request, &parseError)) {
            Fail(&receipt, L"parse", L"INVALID_REQUEST", parseError);
        } else {
            receipt.requestId = request.requestId;
            receipt.operationId = request.operationId;
            receipt.operation = request.operation;
            if (request.documentId > 0) {
                receipt.documentId = request.documentId;
            }
            receipt.documentPath = ReadDocumentPath(hwp);
            if (receipt.documentPath.empty()) {
                receipt.documentPath = request.documentPath;
            }
            const bool captureTickSignatures =
                request.operation == L"MUTATE" ||
                request.operation == L"HISTORY";
            if (captureTickSignatures) {
                receipt.beforeSignature = CaptureSignature(hwp);
            }
            ExecuteOperation(hwp, request, &receipt);
            if (captureTickSignatures && receipt.afterSignature.empty()) {
                receipt.afterSignature = CaptureSignature(hwp);
            }
        }
    } catch (const std::exception& error) {
        const char* const message = error.what();
        Fail(
            &receipt,
            receipt.stage,
            L"NATIVE_EXCEPTION",
            std::wstring(message, message + std::strlen(message)));
    } catch (...) {
        Fail(
            &receipt,
            receipt.stage,
            L"NATIVE_EXCEPTION",
            L"protocol bundle failed unexpectedly");
    }
    receipt.elapsedMicroseconds =
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count();
    return FormatReceipt(receipt);
}

}

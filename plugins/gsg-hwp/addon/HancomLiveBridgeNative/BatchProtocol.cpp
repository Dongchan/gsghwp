#include "BatchProtocol.h"

#include "ProtocolEncoding.h"

#include <WinCrypt.h>

#include <climits>
#include <cwchar>
#include <limits>
#include <set>
#include <sstream>
#include <utility>

namespace hancom::batch {
namespace {

using hancom::encoding::EncodeUtf8Base64;

constexpr size_t kMaximumPayloadCharacters = 8'000'000;
constexpr size_t kMaximumOperations = 5'000;

std::vector<std::wstring> Split(const std::wstring& value, const wchar_t separator) {
    std::vector<std::wstring> fields;
    size_t start = 0;
    for (;;) {
        const size_t end = value.find(separator, start);
        fields.push_back(value.substr(start, end - start));
        if (end == std::wstring::npos) {
            return fields;
        }
        start = end + 1;
    }
}

bool DecodeUtf8Base64(const std::wstring& encoded, std::wstring* const decoded) {
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
    if (byteCount != 0 && !CryptStringToBinaryW(
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
        reinterpret_cast<LPCCH>(bytes.data()),
        static_cast<int>(byteCount),
        nullptr,
        0);
    if (characterCount <= 0) {
        return false;
    }
    decoded->resize(static_cast<size_t>(characterCount));
    return MultiByteToWideChar(
               CP_UTF8,
               MB_ERR_INVALID_CHARS,
               reinterpret_cast<LPCCH>(bytes.data()),
               static_cast<int>(byteCount),
               decoded->data(),
               characterCount) == characterCount;
}


bool Fail(Error* const error, const wchar_t* const message) {
    if (error != nullptr) {
        error->code = L"BAD_REQUEST";
        error->address.clear();
        error->message = message;
    }
    return false;
}

bool ParseDocumentId(const std::wstring& value, LONG* const result) {
    if (value.empty() || result == nullptr) {
        return false;
    }
    wchar_t* end = nullptr;
    errno = 0;
    const long long parsed = wcstoll(value.c_str(), &end, 10);
    if (errno != 0 || end == nullptr || *end != L'\0' || parsed < 0 ||
        parsed > (std::numeric_limits<LONG>::max)()) {
        return false;
    }
    *result = static_cast<LONG>(parsed);
    return true;
}

}

bool ParseRequest(
    const std::wstring& payload,
    Request* const request,
    Error* const error) noexcept {
    try {
        if (request == nullptr || error == nullptr) {
            return false;
        }
        *request = Request{};
        *error = Error{};
        if (payload.empty() || payload.size() > kMaximumPayloadCharacters) {
            return Fail(error, L"payload is empty or exceeds the 8MB limit");
        }
        std::vector<std::wstring> lines = Split(payload, L'\n');
        for (std::wstring& line : lines) {
            if (!line.empty() && line.back() == L'\r') {
                line.pop_back();
            }
        }
        if (lines.size() < 5 || lines.front() != L"HCB1") {
            return Fail(error, L"protocol header is not HCB1");
        }
        const std::vector<std::wstring> document = Split(lines[1], L'\t');
        if (document.size() != 3 || document[0] != L"DOC" ||
            !ParseDocumentId(document[1], &request->documentId) ||
            !DecodeUtf8Base64(document[2], &request->fullName)) {
            return Fail(error, L"DOC record is invalid");
        }

        size_t totalOperations = 0;
        size_t lineIndex = 2;
        while (lineIndex < lines.size() && lines[lineIndex] != L"END") {
            const std::vector<std::wstring> tableFields = Split(lines[lineIndex], L'\t');
            if (tableFields.size() != 2 || tableFields[0] != L"TABLE" ||
                tableFields[1].empty()) {
                return Fail(error, L"TABLE record is invalid");
            }
            TableBatch table;
            table.controlId = tableFields[1];
            std::set<std::wstring> addresses;
            ++lineIndex;
            while (lineIndex < lines.size() && lines[lineIndex] != L"ENDTABLE") {
                const std::vector<std::wstring> fields = Split(lines[lineIndex], L'\t');
                if (fields.size() != 4 || fields[1].empty() ||
                    (fields[0] != L"TEXT" && fields[0] != L"IMAGE")) {
                    return Fail(error, L"cell operation record is invalid");
                }
                CellOperation operation;
                operation.kind = fields[0] == L"TEXT"
                    ? OperationKind::Text
                    : OperationKind::Image;
                operation.address = fields[1];
                if (!addresses.insert(operation.address).second) {
                    return Fail(error, L"a table contains a duplicate cell address");
                }
                if (!DecodeUtf8Base64(fields[2], &operation.expectedText) ||
                    !DecodeUtf8Base64(fields[3], &operation.value)) {
                    return Fail(error, L"cell operation contains invalid UTF-8 base64");
                }
                table.operations.push_back(std::move(operation));
                ++totalOperations;
                if (totalOperations > kMaximumOperations) {
                    return Fail(error, L"batch exceeds the 5000-operation limit");
                }
                ++lineIndex;
            }
            if (lineIndex >= lines.size() || table.operations.empty()) {
                return Fail(error, L"TABLE has no ENDTABLE or no operations");
            }
            request->tables.push_back(std::move(table));
            ++lineIndex;
        }
        if (request->tables.empty() || lineIndex != lines.size() - 1 ||
            lines[lineIndex] != L"END") {
            return Fail(error, L"request has no tables or a malformed END record");
        }
        return true;
    } catch (...) {
        return Fail(error, L"request parsing failed unexpectedly");
    }
}

std::wstring SuccessResponse(
    const size_t textUpdates,
    const size_t imageUpdates,
    const TimingEvidence& timing) {
    std::wostringstream output;
    output << L"HCB1\tOK\t" << textUpdates << L'\t' << imageUpdates << L'\t'
           << timing.totalMicroseconds << L'\t'
           << timing.validationMicroseconds << L'\t'
           << timing.locateMicroseconds << L'\t'
           << timing.textMicroseconds << L'\t'
           << timing.imageMicroseconds << L'\t'
           << timing.verifyMicroseconds << L'\t'
           << timing.imageTimingCount << L'\t'
           << timing.imageMaximumMicroseconds << L'\t'
           << timing.imageTotalMicroseconds;
    return output.str();
}

std::wstring ErrorResponse(const Error& error) {
    return L"HCB1\tERROR\t" + error.code + L'\t' +
        EncodeUtf8Base64(error.address) + L'\t' +
        EncodeUtf8Base64(error.message);
}

}

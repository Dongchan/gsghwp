#pragma once

#include <Windows.h>

#include <string>
#include <vector>

namespace hancom::batch {

enum class OperationKind { Text, Image };

struct CellOperation {
    OperationKind kind = OperationKind::Text;
    std::wstring address;
    std::wstring expectedText;
    std::wstring value;
};

struct TableBatch {
    std::wstring controlId;
    std::vector<CellOperation> operations;
};

struct Request {
    LONG documentId = -1;
    std::wstring fullName;
    std::vector<TableBatch> tables;
};

struct Error {
    std::wstring code;
    std::wstring address;
    std::wstring message;
};

struct TimingEvidence {
    long long totalMicroseconds = 0;
    long long validationMicroseconds = 0;
    long long locateMicroseconds = 0;
    long long textMicroseconds = 0;
    long long imageMicroseconds = 0;
    long long verifyMicroseconds = 0;
    size_t imageTimingCount = 0;
    long long imageMaximumMicroseconds = 0;
    long long imageTotalMicroseconds = 0;
};

bool ParseRequest(const std::wstring& payload, Request* request, Error* error) noexcept;
std::wstring SuccessResponse(
    size_t textUpdates,
    size_t imageUpdates,
    const TimingEvidence& timing);
std::wstring ErrorResponse(const Error& error);

}

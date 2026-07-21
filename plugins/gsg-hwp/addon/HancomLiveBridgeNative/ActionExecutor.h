#pragma once

#include "ActionProtocol.h"

#include <Windows.h>

#include <cstddef>
#include <cstdint>
#include <string>
#include <variant>
#include <vector>

namespace hancom::actions {

struct CallResult {
    std::wstring method;
    std::variant<std::monostate, bool, std::int64_t, std::uint64_t, std::wstring> value;
};

struct ExecutionResult {
    bool succeeded = false;
    size_t commandsExecuted = 0;
    size_t actionsExecuted = 0;
    size_t textInsertions = 0;
    size_t imageInsertions = 0;
    long long elapsedMicroseconds = 0;
    size_t imageTimingCount = 0;
    long long imageMaximumMicroseconds = 0;
    long long imageTotalMicroseconds = 0;
    std::vector<std::wstring> createdControlIds;
    std::vector<CallResult> callResults;
    std::wstring failedStep;
    std::wstring structureDigestBefore;
    std::wstring structureDigestAfter;
    bool partialMutation = false;
    bool retrySafe = true;
    Error error;
};

ExecutionResult Execute(IDispatch* hwp, const Request& request) noexcept;
std::wstring SuccessResponse(const ExecutionResult& result);
std::wstring FailureResponse(const ExecutionResult& result);

}

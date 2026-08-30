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

struct PreparedTextPatchTarget {
    LONG startList = 0;
    LONG startParagraph = 0;
    LONG startCharacter = 0;
    LONG endList = 0;
    LONG endParagraph = 0;
    LONG endCharacter = 0;
    std::wstring text;
    std::wstring faceName;
    LONG height = 0;
    bool bold = false;
    LONG textColor = 0;
    LONG alignment = 0;
    LONG lineSpacing = 0;
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
    size_t preflightTargetCount = 0;
    size_t failedRequestIndex = 0;
    std::vector<std::wstring> createdControlIds;
    std::vector<PreparedTextPatchTarget> preparedTextPatchTargets;
    std::vector<CallResult> callResults;
    std::wstring failedStep;
    std::wstring structureDigestBefore;
    std::wstring structureDigestAfter;
    bool partialMutation = false;
    bool retrySafe = true;
    Error error;
};

ExecutionResult Execute(IDispatch* hwp, const Request& request) noexcept;
ExecutionResult PreflightTextPatches(IDispatch* hwp, const Request& request) noexcept;
std::wstring SuccessResponse(const ExecutionResult& result);
std::wstring FailureResponse(const ExecutionResult& result);

}

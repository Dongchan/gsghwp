#pragma once

#include "BatchProtocol.h"

#include <Windows.h>

#include <cstddef>

namespace hancom::batch {

struct ExecutionResult {
    bool succeeded = false;
    size_t textUpdates = 0;
    size_t imageUpdates = 0;
    TimingEvidence elapsedMicroseconds;
    Error error;
};

ExecutionResult Execute(IDispatch* hwp, const Request& request) noexcept;

}

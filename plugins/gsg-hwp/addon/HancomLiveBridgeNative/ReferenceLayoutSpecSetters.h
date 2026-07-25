#pragma once

#include "ActionProtocol.h"

#include <string>
#include <vector>

namespace hancom::reference_layout::detail {

bool IntegerSetter(
    const actions::Command& command,
    const wchar_t* path,
    LONG* value,
    actions::Error* error);

bool TextSetter(
    const actions::Command& command,
    const wchar_t* path,
    std::wstring* value,
    actions::Error* error);

void DefaultMissing(
    std::vector<LONG>* values,
    size_t count,
    LONG fallback);

}

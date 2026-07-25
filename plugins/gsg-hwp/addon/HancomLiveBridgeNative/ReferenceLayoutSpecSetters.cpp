#include "ReferenceLayoutSpecSetters.h"

namespace hancom::reference_layout::detail {
namespace {

bool Fail(actions::Error* const error, const std::wstring& message) {
    error->code = L"REFERENCE_LAYOUT_PAYLOAD";
    error->location = L"ReferenceLayoutBulk";
    error->message = message;
    return false;
}

}

bool IntegerSetter(
    const actions::Command& command,
    const wchar_t* const path,
    LONG* const value,
    actions::Error* const error) {
    for (const actions::Setter& setter : command.setters) {
        if (setter.path == path &&
            setter.value.kind == actions::ValueKind::Integer) {
            *value = setter.value.integer;
            return true;
        }
    }
    return Fail(error, std::wstring(L"missing integer setter: ") + path);
}

bool TextSetter(
    const actions::Command& command,
    const wchar_t* const path,
    std::wstring* const value,
    actions::Error* const error) {
    for (const actions::Setter& setter : command.setters) {
        if (setter.path == path &&
            setter.value.kind == actions::ValueKind::Text) {
            *value = setter.value.text;
            return true;
        }
    }
    return Fail(error, std::wstring(L"missing text setter: ") + path);
}

void DefaultMissing(
    std::vector<LONG>* const values,
    const size_t count,
    const LONG fallback) {
    if (values->empty() && count > 0) {
        values->assign(count, fallback);
    }
}

}

#include "ReferenceLayoutPayloadArrays.h"

namespace hancom::reference_layout::detail {
namespace {

bool Fail(actions::Error* const error, const std::wstring& message) {
    error->code = L"REFERENCE_LAYOUT_PAYLOAD";
    error->location = L"ReferenceLayout";
    error->message = message;
    return false;
}

}

bool PayloadArrays::Load(
    const actions::Command& command,
    actions::Error* const error) {
    for (const auto& [name, count] : command.arrays) {
        if (count < 0 ||
            !values_.emplace(
                name,
                std::vector<const actions::Value*>(
                    static_cast<size_t>(count),
                    nullptr)).second) {
            return Fail(error, L"array declaration is invalid or duplicated");
        }
    }
    for (const actions::ArrayValue& entry : command.arrayValues) {
        const auto found = values_.find(entry.name);
        if (found == values_.end() || entry.index < 0 ||
            static_cast<size_t>(entry.index) >= found->second.size() ||
            found->second[static_cast<size_t>(entry.index)] != nullptr) {
            return Fail(
                error,
                L"array value is missing its declaration or duplicated");
        }
        found->second[static_cast<size_t>(entry.index)] = &entry.value;
    }
    for (const auto& [name, values] : values_) {
        for (const actions::Value* const value : values) {
            if (value == nullptr) {
                return Fail(error, L"array has an unset item: " + name);
            }
        }
    }
    return true;
}

bool PayloadArrays::Integers(
    const wchar_t* const name,
    std::vector<LONG>* const result,
    actions::Error* const error) const {
    const auto found = values_.find(name);
    if (found == values_.end()) {
        return Fail(error, std::wstring(L"missing array: ") + name);
    }
    result->clear();
    result->reserve(found->second.size());
    for (const actions::Value* const value : found->second) {
        if (value->kind != actions::ValueKind::Integer) {
            return Fail(error, std::wstring(L"array is not integer: ") + name);
        }
        result->push_back(value->integer);
    }
    return true;
}

bool PayloadArrays::Texts(
    const wchar_t* const name,
    std::vector<std::wstring>* const result,
    actions::Error* const error) const {
    const auto found = values_.find(name);
    if (found == values_.end()) {
        return Fail(error, std::wstring(L"missing array: ") + name);
    }
    result->clear();
    result->reserve(found->second.size());
    for (const actions::Value* const value : found->second) {
        if (value->kind != actions::ValueKind::Text) {
            return Fail(error, std::wstring(L"array is not text: ") + name);
        }
        result->push_back(value->text);
    }
    return true;
}

bool PayloadArrays::OptionalIntegers(
    const wchar_t* const name,
    std::vector<LONG>* const result,
    actions::Error* const error) const {
    if (values_.find(name) == values_.end()) {
        result->clear();
        return true;
    }
    return Integers(name, result, error);
}

bool PayloadArrays::OptionalTexts(
    const wchar_t* const name,
    std::vector<std::wstring>* const result,
    actions::Error* const error) const {
    if (values_.find(name) == values_.end()) {
        result->clear();
        return true;
    }
    return Texts(name, result, error);
}

bool SameSize(
    const size_t expected,
    const std::initializer_list<size_t>& sizes,
    actions::Error* const error) {
    for (const size_t size : sizes) {
        if (size != expected) {
            return Fail(
                error,
                L"parallel reference-layout arrays have different sizes");
        }
    }
    return true;
}

}

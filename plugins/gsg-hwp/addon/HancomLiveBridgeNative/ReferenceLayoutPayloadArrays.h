#pragma once

#include "ActionProtocol.h"

#include <initializer_list>
#include <map>
#include <string>
#include <vector>

namespace hancom::reference_layout::detail {

class PayloadArrays final {
public:
    bool Load(
        const actions::Command& command,
        actions::Error* error);
    bool Integers(
        const wchar_t* name,
        std::vector<LONG>* result,
        actions::Error* error) const;
    bool Texts(
        const wchar_t* name,
        std::vector<std::wstring>* result,
        actions::Error* error) const;
    bool OptionalIntegers(
        const wchar_t* name,
        std::vector<LONG>* result,
        actions::Error* error) const;
    bool OptionalTexts(
        const wchar_t* name,
        std::vector<std::wstring>* result,
        actions::Error* error) const;

private:
    std::map<std::wstring, std::vector<const actions::Value*>> values_;
};

bool SameSize(
    size_t expected,
    const std::initializer_list<size_t>& sizes,
    actions::Error* error);

}

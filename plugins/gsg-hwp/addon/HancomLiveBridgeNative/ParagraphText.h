#pragma once

#include <string>

namespace hancom::text {

inline std::wstring NormalizeParagraphText(const std::wstring& value) {
    std::wstring normalized;
    normalized.reserve(value.size());
    for (size_t index = 0; index < value.size(); ++index) {
        if (value[index] == L'\r') {
            normalized.push_back(L'\n');
            if (index + 1 < value.size() && value[index + 1] == L'\n') {
                ++index;
            }
        } else {
            normalized.push_back(value[index]);
        }
    }
    return normalized;
}

inline bool SameParagraphText(
    const std::wstring& left,
    const std::wstring& right) {
    return NormalizeParagraphText(left) == NormalizeParagraphText(right);
}

}

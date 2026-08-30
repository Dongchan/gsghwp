#include "TextPatchReadback.h"

#include "ParagraphText.h"

#include <cwctype>

namespace hancom::text_patch {
namespace {

bool MatchesAt(
    const std::wstring& actual,
    const std::wstring& expected,
    const size_t offset,
    const bool matchCase) {
    if (offset > actual.size() || expected.size() > actual.size() - offset) {
        return false;
    }
    for (size_t index = 0; index < expected.size(); ++index) {
        const wchar_t current = actual[offset + index];
        if ((matchCase && current != expected[index]) ||
            (!matchCase && towlower(current) != towlower(expected[index]))) {
            return false;
        }
    }
    return true;
}

bool MatchesExactExpected(
    const std::wstring& readback,
    const std::wstring& expected,
    const bool matchCase) {
    const std::wstring normalizedReadback =
        hancom::text::NormalizeParagraphText(readback);
    const std::wstring normalizedExpected =
        hancom::text::NormalizeParagraphText(expected);
    return normalizedReadback.size() == normalizedExpected.size() &&
        MatchesAt(normalizedReadback, normalizedExpected, 0, matchCase);
}

bool MatchesUniqueSingleParagraphLiteral(
    const std::wstring& readback,
    const std::wstring& literal,
    const bool matchCase) {
    if (literal.empty() ||
        readback.find_first_of(L"\r\n") != std::wstring::npos ||
        literal.find_first_of(L"\r\n") != std::wstring::npos) {
        return false;
    }
    size_t matches = 0;
    for (size_t offset = 0;
         offset + literal.size() <= readback.size();
         ++offset) {
        if (MatchesAt(readback, literal, offset, matchCase) && ++matches > 1) {
            return false;
        }
    }
    return matches == 1;
}

// The selected body text with the paragraph's automatic number removed, or
// an empty string when that exact prefix -- the number and one space -- is
// not what stands in front. The body itself is never empty on success, so
// an empty answer always means "no automatic number prefix here".
std::wstring WithoutAutomaticNumber(
    const std::wstring& readback,
    const std::wstring& automaticNumber) {
    if (automaticNumber.empty()) {
        return std::wstring();
    }
    const std::wstring prefix = automaticNumber + L' ';
    if (readback.size() <= prefix.size() ||
        readback.compare(0, prefix.size(), prefix) != 0) {
        return std::wstring();
    }
    return readback.substr(prefix.size());
}

}

bool MatchesExpectedReadback(
    const std::wstring& readback,
    const std::wstring& expected,
    const bool matchCase,
    const ReadbackPolicy policy) {
    return MatchesExactExpected(readback, expected, matchCase) ||
        (policy == ReadbackPolicy::Find &&
         MatchesUniqueSingleParagraphLiteral(readback, expected, matchCase));
}

bool MatchesReplacementReadback(
    const std::wstring& readback,
    const std::wstring& replacement,
    const ReadbackPolicy policy) {
    return hancom::text::SameParagraphText(readback, replacement) ||
        (policy == ReadbackPolicy::Find &&
         MatchesUniqueSingleParagraphLiteral(readback, replacement, true));
}

bool MatchesExpectedReadback(
    const std::wstring& readback,
    const std::wstring& expected,
    const std::wstring& automaticNumber,
    const bool matchCase,
    const ReadbackPolicy policy) {
    const std::wstring body =
        WithoutAutomaticNumber(readback, automaticNumber);
    return !body.empty() &&
        MatchesExpectedReadback(body, expected, matchCase, policy);
}

bool MatchesReplacementReadback(
    const std::wstring& readback,
    const std::wstring& replacement,
    const std::wstring& automaticNumber,
    const ReadbackPolicy policy) {
    const std::wstring body =
        WithoutAutomaticNumber(readback, automaticNumber);
    return !body.empty() &&
        MatchesReplacementReadback(body, replacement, policy);
}

}

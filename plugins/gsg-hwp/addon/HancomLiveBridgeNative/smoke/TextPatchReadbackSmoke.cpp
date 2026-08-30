#include "../TextPatchReadback.h"

#include <iostream>

bool TextPatchReadbackSmoke() {
    using hancom::text_patch::MatchesExpectedReadback;
    using hancom::text_patch::MatchesReplacementReadback;
    using hancom::text_patch::ReadbackPolicy;

    const auto expected = [](const wchar_t* const readback,
                             const wchar_t* const literal,
                             const bool matchCase,
                             const ReadbackPolicy policy) {
        return MatchesExpectedReadback(readback, literal, matchCase, policy);
    };
    const auto replacement = [](const wchar_t* const readback,
                                const wchar_t* const literal,
                                const ReadbackPolicy policy) {
        return MatchesReplacementReadback(readback, literal, policy);
    };
    const auto numberedExpected = [](const wchar_t* const readback,
                                      const wchar_t* const literal,
                                      const wchar_t* const automaticNumber,
                                      const ReadbackPolicy policy) {
        return MatchesExpectedReadback(
            readback,
            literal,
            automaticNumber,
            true,
            policy);
    };
    const auto numberedReplacement = [](const wchar_t* const readback,
                                         const wchar_t* const literal,
                                         const wchar_t* const automaticNumber,
                                         const ReadbackPolicy policy) {
        return MatchesReplacementReadback(
            readback,
            literal,
            automaticNumber,
            policy);
    };

    const bool passed =
        expected(L"literal", L"literal", true, ReadbackPolicy::Exact) &&
        replacement(L"literal", L"literal", ReadbackPolicy::Exact) &&
        expected(L"prefix literal", L"literal", true, ReadbackPolicy::Find) &&
        replacement(L"prefix literal", L"literal", ReadbackPolicy::Find) &&
        !expected(L"prefix literal", L"literal", true, ReadbackPolicy::Exact) &&
        !replacement(L"prefix literal", L"literal", ReadbackPolicy::Exact) &&
        !expected(L"literal literal", L"literal", true, ReadbackPolicy::Find) &&
        !replacement(L"literal literal", L"literal", ReadbackPolicy::Find) &&
        !expected(L"ababa", L"aba", true, ReadbackPolicy::Find) &&
        !replacement(L"ababa", L"aba", ReadbackPolicy::Find) &&
        !expected(L"prefix\nliteral", L"literal", true, ReadbackPolicy::Find) &&
        !replacement(L"prefix\rliteral", L"literal", ReadbackPolicy::Find) &&
        expected(L"prefix LITERAL", L"literal", false, ReadbackPolicy::Find) &&
        expected(L"LITERAL", L"literal", false, ReadbackPolicy::Exact) &&
        !expected(L"prefix LITERAL", L"literal", true, ReadbackPolicy::Find) &&
        // The automatic number and one space, and only that.
        numberedExpected(
            L"(2) literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        numberedReplacement(
            L"(2) literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedExpected(
            L"(3) literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedReplacement(
            L"(3) literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedExpected(
            L"(2)  literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedReplacement(
            L"(2)  literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedExpected(
            L"(2)literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedReplacement(
            L"(2)literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedExpected(
            L"(2) ",
            L"",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedExpected(
            L"(2)",
            L"(2)",
            L"(2)",
            ReadbackPolicy::Exact) &&
        // A paragraph with no automatic number allows no prefix at all.
        !numberedExpected(
            L"(2) literal",
            L"literal",
            L"",
            ReadbackPolicy::Exact) &&
        !numberedReplacement(
            L"(2) literal",
            L"literal",
            L"",
            ReadbackPolicy::Exact) &&
        // The stripped body still has to satisfy the readback policy.
        !numberedExpected(
            L"(2) other",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedReplacement(
            L"(2) other",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        // Stripping the automatic number is the whole allowance. What stands
        // between it and the literal is ordinary body text, and a range check
        // that waved it through would have let a replacement delete it and
        // still call the readback a match.
        !numberedExpected(
            L"(2) prefix literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        !numberedReplacement(
            L"(2) prefix literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Exact) &&
        // The same text under FIND is a match, because the find path proves by
        // coordinate span -- not by readback -- that only the literal is
        // selected, and the span is what a replacement deletes.
        numberedExpected(
            L"(2) prefix literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Find) &&
        numberedReplacement(
            L"(2) prefix literal",
            L"literal",
            L"(2)",
            ReadbackPolicy::Find);

    if (!passed) {
        std::wcerr << L"text.patch readback matcher smoke failed\n";
    }
    return passed;
}

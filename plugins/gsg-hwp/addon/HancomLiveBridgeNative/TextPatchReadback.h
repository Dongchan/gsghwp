#pragma once

#include <string>

namespace hancom::text_patch {

enum class ReadbackPolicy {
    Exact,
    Find,
};

bool MatchesExpectedReadback(
    const std::wstring& readback,
    const std::wstring& expected,
    bool matchCase,
    ReadbackPolicy policy);

bool MatchesReplacementReadback(
    const std::wstring& readback,
    const std::wstring& replacement,
    ReadbackPolicy policy);

// A paragraph whose number HWP draws automatically reads back through
// GetTextFile("saveblock:true") with that number in front of the selected
// text, even when the selection starts inside the body -- the number
// occupies no character cell, so no range can exclude it. These overloads
// accept exactly that one prefix: the paragraph's own GetHeadingString
// value followed by a single space, and nothing else. An empty
// automaticNumber accepts nothing.
bool MatchesExpectedReadback(
    const std::wstring& readback,
    const std::wstring& expected,
    const std::wstring& automaticNumber,
    bool matchCase,
    ReadbackPolicy policy);

bool MatchesReplacementReadback(
    const std::wstring& readback,
    const std::wstring& replacement,
    const std::wstring& automaticNumber,
    ReadbackPolicy policy);

}

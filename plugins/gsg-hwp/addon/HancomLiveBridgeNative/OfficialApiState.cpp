#include "OfficialApiState.h"

#include "DispatchInvoke.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <cwctype>
#include <iterator>
#include <ostream>
#include <string>

namespace hancom::official_api {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

constexpr std::uint64_t kFnvOffset = 1469598103934665603ULL;
constexpr std::uint64_t kFnvPrime = 1099511628211ULL;
constexpr std::uint64_t kDocumentSectionLength = 65536ULL;

void AddHashCharacter(
    std::uint64_t* const hash,
    const wchar_t character) noexcept {
    *hash ^= static_cast<std::uint16_t>(character);
    *hash *= kFnvPrime;
}

void AddHashRange(
    std::uint64_t* const hash,
    const std::wstring& value,
    const size_t begin,
    const size_t end) noexcept {
    for (size_t index = begin; index < end; ++index) {
        AddHashCharacter(hash, value[index]);
    }
}

void AddHash(std::uint64_t* const hash, const std::wstring& value) noexcept {
    AddHashRange(hash, value, 0, value.size());
}

size_t FindElementStart(
    const std::wstring& content,
    const wchar_t* const elementPrefix,
    const size_t cursor) noexcept {
    const size_t prefixLength = wcslen(elementPrefix);
    size_t start = content.find(elementPrefix, cursor);
    while (start != std::wstring::npos) {
        const size_t suffix = start + prefixLength;
        if (suffix == content.size() ||
            content[suffix] == L'>' ||
            content[suffix] == L'/' ||
            std::iswspace(content[suffix]) != 0) {
            return start;
        }
        start = content.find(elementPrefix, suffix);
    }
    return std::wstring::npos;
}

size_t FindElementEnd(
    const std::wstring& content,
    const wchar_t* const elementPrefix,
    const wchar_t* const elementEnd,
    const size_t start) noexcept {
    const size_t endLength = wcslen(elementEnd);
    const size_t openingEnd = content.find(L'>', start);
    if (openingEnd == std::wstring::npos) {
        return std::wstring::npos;
    }
    if (openingEnd > start && content[openingEnd - 1] == L'/') {
        return openingEnd + 1;
    }
    size_t depth = 1;
    size_t cursor = openingEnd + 1;
    while (depth != 0) {
        const size_t nestedStart =
            FindElementStart(content, elementPrefix, cursor);
        const size_t closingStart = content.find(elementEnd, cursor);
        if (closingStart == std::wstring::npos) {
            return std::wstring::npos;
        }
        if (nestedStart != std::wstring::npos && nestedStart < closingStart) {
            const size_t nestedEnd = content.find(L'>', nestedStart);
            if (nestedEnd == std::wstring::npos) {
                return std::wstring::npos;
            }
            if (nestedEnd == nestedStart || content[nestedEnd - 1] != L'/') {
                ++depth;
            }
            cursor = nestedEnd + 1;
            continue;
        }
        --depth;
        cursor = closingStart + endLength;
    }
    return cursor;
}

void CaptureHwpmlHash(
    const std::wstring& content,
    std::uint64_t* const hash,
    std::uint64_t* const length,
    std::vector<DocumentSectionFingerprint>* const sections) noexcept {
    constexpr wchar_t kBinaryDataTag[] = L"<BINDATA";
    constexpr wchar_t kBinaryDataEnd[] = L"</BINDATA>";
    constexpr wchar_t kSizeAttribute[] = L" Size=\"";
    constexpr wchar_t kNormalizedSize[] = L"*";
    constexpr wchar_t kNormalizedBinaryData[] = L"<BINARY-DATA/>";
    constexpr wchar_t kCaretPositionTag[] = L"<CARETPOS";
    constexpr wchar_t kNormalizedCaretPosition[] = L"<CARETPOS/>";
    constexpr wchar_t kParameterSetTag[] = L"<PARAMETERSET";
    constexpr wchar_t kParameterSetEnd[] = L"</PARAMETERSET>";
    constexpr wchar_t kNormalizedParameterSet[] = L"<PARAMETERSET/>";
    *hash = kFnvOffset;
    *length = 0;
    sections->clear();
    std::uint64_t sectionHash = kFnvOffset;
    std::uint64_t sectionLength = 0;
    const auto appendRange = [
        hash,
        length,
        sections,
        &sectionHash,
        &sectionLength](
        const std::wstring& value,
        const size_t begin,
        const size_t end) noexcept {
        for (size_t index = begin; index < end; ++index) {
            const wchar_t character = value[index];
            AddHashCharacter(hash, character);
            AddHashCharacter(&sectionHash, character);
            ++*length;
            ++sectionLength;
            if (sectionLength == kDocumentSectionLength) {
                sections->push_back({sectionHash});
                sectionHash = kFnvOffset;
                sectionLength = 0;
            }
        }
    };
    const auto appendContentRange = [&appendRange, &content](
                                 const size_t begin,
                                 const size_t end) noexcept {
        appendRange(content, begin, end);
    };
    const auto appendLiteral = [&appendRange](
                                   const std::wstring& value) noexcept {
        appendRange(value, 0, value.size());
    };
    const auto finish = [sections, &sectionHash, &sectionLength]() noexcept {
        if (sectionLength != 0) {
            sections->push_back({sectionHash});
            sectionLength = 0;
        }
    };
    size_t cursor = 0;
    while (cursor < content.size()) {
        const size_t tagStart = FindElementStart(content, kBinaryDataTag, cursor);
        const size_t caretStart =
            FindElementStart(content, kCaretPositionTag, cursor);
        const size_t parameterSetStart =
            FindElementStart(content, kParameterSetTag, cursor);
        if (parameterSetStart != std::wstring::npos &&
            (tagStart == std::wstring::npos || parameterSetStart < tagStart) &&
            (caretStart == std::wstring::npos ||
             parameterSetStart < caretStart)) {
            const size_t parameterSetEnd = FindElementEnd(
                content,
                kParameterSetTag,
                kParameterSetEnd,
                parameterSetStart);
            if (parameterSetEnd == std::wstring::npos) {
                appendContentRange(cursor, content.size());
                finish();
                return;
            }
            appendContentRange(cursor, parameterSetStart);
            appendLiteral(kNormalizedParameterSet);
            cursor = parameterSetEnd;
            continue;
        }
        if (caretStart != std::wstring::npos &&
            (tagStart == std::wstring::npos || caretStart < tagStart)) {
            const size_t caretEnd = content.find(L'>', caretStart);
            if (caretEnd == std::wstring::npos) {
                appendContentRange(cursor, content.size());
                finish();
                return;
            }
            appendContentRange(cursor, caretStart);
            appendLiteral(kNormalizedCaretPosition);
            cursor = caretEnd + 1;
            continue;
        }
        if (tagStart == std::wstring::npos) {
            appendContentRange(cursor, content.size());
            finish();
            return;
        }
        const size_t tagEnd = content.find(L'>', tagStart);
        if (tagEnd == std::wstring::npos) {
            appendContentRange(cursor, content.size());
            finish();
            return;
        }
        appendContentRange(cursor, tagStart);
        const size_t sizeAttribute = content.find(kSizeAttribute, tagStart);
        if (sizeAttribute != std::wstring::npos && sizeAttribute < tagEnd) {
            const size_t valueStart =
                sizeAttribute + std::size(kSizeAttribute) - 1;
            const size_t valueEnd = content.find(L'"', valueStart);
            if (valueEnd == std::wstring::npos || valueEnd > tagEnd) {
                appendContentRange(tagStart, tagEnd + 1);
            } else {
                appendContentRange(tagStart, valueStart);
                appendLiteral(kNormalizedSize);
                appendContentRange(valueEnd, tagEnd + 1);
            }
        } else {
            appendContentRange(tagStart, tagEnd + 1);
        }
        if (tagEnd > tagStart && content[tagEnd - 1] == L'/') {
            cursor = tagEnd + 1;
            continue;
        }
        const size_t dataEnd = content.find(kBinaryDataEnd, tagEnd + 1);
        if (dataEnd == std::wstring::npos) {
            appendContentRange(tagEnd + 1, content.size());
            finish();
            return;
        }
        appendLiteral(kNormalizedBinaryData);
        appendContentRange(dataEnd, dataEnd + std::size(kBinaryDataEnd) - 1);
        cursor = dataEnd + std::size(kBinaryDataEnd) - 1;
    }
    finish();
}

bool CaptureTextFile(
    IDispatch* const hwp,
    const wchar_t* const format,
    const bool requireContent,
    std::uint64_t* const hash,
    std::uint64_t* const length,
    std::vector<DocumentSectionFingerprint>* const sections = nullptr) noexcept {
    CComVariant raw;
    std::wstring content;
    if (FAILED(Method(
            hwp,
            L"GetTextFile",
            {CComVariant(format), CComVariant(L"")},
            &raw)) ||
        FAILED(AsString(raw, &content)) ||
        (requireContent && content.empty())) {
        return false;
    }
    if (wcscmp(format, L"HWPML2X") == 0) {
        if (sections == nullptr) {
            return false;
        }
        CaptureHwpmlHash(content, hash, length, sections);
    } else {
        *hash = kFnvOffset;
        AddHash(hash, content);
        *length = static_cast<std::uint64_t>(content.size());
    }
    return true;
}

void CaptureControls(IDispatch* const hwp, DocumentState* const state) noexcept {
    CComVariant raw;
    if (FAILED(PropertyGet(hwp, L"HeadCtrl", &raw))) {
        return;
    }
    CComPtr<IDispatch> control;
    if (FAILED(AsDispatch(raw, control))) {
        state->controlCount = 0;
        return;
    }
    state->controlCount = 0;
    state->controlHash = 1469598103934665603ULL;
    while (control != nullptr && state->controlCount < 100000) {
        CComVariant rawId;
        std::wstring id;
        if (SUCCEEDED(PropertyGet(control, L"CtrlID", &rawId))) {
            static_cast<void>(AsString(rawId, &id));
        }
        AddHash(&state->controlHash, id);
        ++state->controlCount;
        CComVariant rawNext;
        if (FAILED(PropertyGet(control, L"Next", &rawNext))) {
            break;
        }
        CComPtr<IDispatch> next;
        if (FAILED(AsDispatch(rawNext, next))) {
            break;
        }
        control = next;
    }
}

}

DocumentState CaptureDocumentState(IDispatch* const hwp) noexcept {
    DocumentState state;
    CComVariant raw;
    if (SUCCEEDED(PropertyGet(hwp, L"PageCount", &raw))) {
        static_cast<void>(AsLong(raw, &state.pageCount));
    }
    raw.Clear();
    bool modified = false;
    if (SUCCEEDED(PropertyGet(hwp, L"IsModified", &raw)) &&
        SUCCEEDED(AsBool(raw, &modified))) {
        state.modified = modified ? 1L : 0L;
    }
    CComVariant list;
    list.vt = VT_I4 | VT_BYREF;
    list.plVal = &state.list;
    CComVariant paragraph;
    paragraph.vt = VT_I4 | VT_BYREF;
    paragraph.plVal = &state.paragraph;
    CComVariant character;
    character.vt = VT_I4 | VT_BYREF;
    character.plVal = &state.character;
    static_cast<void>(Method(hwp, L"GetPos", {list, paragraph, character}, nullptr));
    CaptureControls(hwp, &state);
    return state;
}

DocumentFingerprint CaptureDocumentFingerprint(IDispatch* const hwp) noexcept {
    DocumentFingerprint fingerprint;
    fingerprint.state = CaptureDocumentState(hwp);
    const bool textCaptured = CaptureTextFile(
        hwp,
        L"TEXT",
        false,
        &fingerprint.textHash,
        &fingerprint.textLength);
    const bool documentCaptured = CaptureTextFile(
        hwp,
        L"HWPML2X",
        true,
        &fingerprint.documentHash,
        &fingerprint.documentLength,
        &fingerprint.documentSections);
    fingerprint.captured =
        fingerprint.state.pageCount > 0 &&
        fingerprint.state.controlCount >= 0 &&
        textCaptured &&
        documentCaptured;
    return fingerprint;
}

void AppendDocumentState(std::wostream& output, const DocumentState& state) {
    output << L'\t' << state.pageCount
           << L'\t' << state.modified
           << L'\t' << state.list
           << L'\t' << state.paragraph
           << L'\t' << state.character
           << L'\t' << state.controlCount
           << L'\t' << state.controlHash;
}

}

#include "OfficialApiState.h"

#include "DispatchInvoke.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <cwctype>
#include <iterator>
#include <ostream>
#include <sstream>
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
constexpr LONG kWholeDocumentScanRange = 0x0077;
constexpr size_t kWholeDocumentScanLimit = 100'000;
constexpr size_t kControlScanLimit = 100'000;

DocumentTextPresence CaptureDocumentTextPresence(
    IDispatch* hwp) noexcept;

bool HasNegativeSignatureComponent(const std::wstring& raw) {
    std::wistringstream input(raw);
    std::wstring component;
    if (!(input >> component) || component != L"SIG") {
        return false;
    }
    while (input >> component) {
        if (!component.empty() && component.front() == L'-') {
            return true;
        }
    }
    return false;
}

bool ParseCompleteContentSignature(const std::wstring& raw) {
    if (HasNegativeSignatureComponent(raw)) {
        return false;
    }
    std::wistringstream input(raw);
    std::wstring prefix;
    LONG pageCount = -1;
    LONG controlCount = -1;
    std::uint64_t controlHash = 0;
    std::uint64_t textHash = 0;
    std::uint64_t textLength = 0;
    std::uint64_t emptyMarker = 0;
    std::uint64_t presenceMarker = 0;
    std::uint64_t documentHash = 0;
    std::uint64_t documentLength = 0;
    if (!(input >> prefix >> pageCount >> controlCount >> controlHash >>
          textHash >> textLength >> emptyMarker >> presenceMarker >>
          documentHash >> documentLength) ||
        prefix != L"SIG" ||
        pageCount < 1 ||
        controlCount < 0 ||
        documentLength == 0 ||
        emptyMarker > 1 ||
        presenceMarker < static_cast<std::uint64_t>(
            DocumentTextPresence::Absent) ||
        presenceMarker > static_cast<std::uint64_t>(
            DocumentTextPresence::Present)) {
        return false;
    }
    std::wstring trailing;
    if (input >> trailing) {
        return false;
    }
    const bool textEmpty = emptyMarker == 1;
    const DocumentTextPresence presence =
        static_cast<DocumentTextPresence>(presenceMarker);
    return presence == DocumentTextPresence::Absent
        ? textEmpty && textLength == 0
        : !textEmpty && textLength > 0;
}

// Every component of a formatted revision must be present, non-negative and
// actually observed. Text is the one component whose observation cannot be
// proved from its own value: GetTextFile answers S_OK with an empty string when
// it cannot serialise, exactly as noted at ActionLifecycle.cpp:104-110, and the
// whole-document scan that tells the two apart is the cost this token exists to
// avoid. So an empty text answer is reported as an incomplete token rather than
// as an empty document, and the caller falls back to its cold path.
bool ParseCompleteContentRevision(const std::wstring& raw) {
    std::wistringstream input(raw);
    std::wstring prefix;
    LONG pageCount = -1;
    LONG controlCount = -1;
    std::uint64_t controlHash = 0;
    std::uint64_t textHash = 0;
    std::uint64_t textLength = 0;
    std::uint64_t sessionTag = 0;
    std::uint64_t writeEpoch = 0;
    // Formatted extraction into uint64_t accepts "-1" by wrapping it to the
    // maximum value, so a minus sign at a field boundary is refused first --
    // the same guard ParseContentSignature uses.
    for (std::wstring::size_type index = 1; index < raw.size(); ++index) {
        if (raw[index] == L'-' && std::iswspace(raw[index - 1]) != 0) {
            return false;
        }
    }
    if (!(input >> prefix >> pageCount >> controlCount >> controlHash >>
          textHash >> textLength >> sessionTag >> writeEpoch) ||
        prefix != L"REV" ||
        pageCount < 1 ||
        controlCount < 0 ||
        textLength == 0 ||
        sessionTag == 0) {
        return false;
    }
    std::wstring trailing;
    return !(input >> trailing);
}

std::wstring ContentRevisionLine(const DocumentContentRevision& revision) {
    return L"REV " + std::to_wstring(revision.pageCount) + L' ' +
        std::to_wstring(revision.controlCount) + L' ' +
        std::to_wstring(revision.controlHash) + L' ' +
        std::to_wstring(revision.textHash) + L' ' +
        std::to_wstring(revision.textLength) + L' ' +
        std::to_wstring(revision.sessionTag) + L' ' +
        std::to_wstring(revision.writeEpoch);
}

HRESULT DispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value) noexcept {
    CComVariant raw;
    const HRESULT status = PropertyGet(object, name, &raw);
    return FAILED(status) ? status : AsDispatch(raw, value);
}

HRESULT ReadItemLong(
    IDispatch* const set,
    const wchar_t* const name,
    LONG* const value) noexcept {
    CComVariant raw;
    const HRESULT status = Method(set, L"Item", {CComVariant(name)}, &raw);
    return FAILED(status) ? status : AsLong(raw, value);
}

bool IsEmptyDispatch(const CComVariant& value) noexcept {
    return value.vt == VT_EMPTY || value.vt == VT_NULL ||
        (value.vt == VT_DISPATCH && value.pdispVal == nullptr) ||
        (value.vt == VT_UNKNOWN && value.punkVal == nullptr);
}

bool ReadControlAnchor(
    IDispatch* const control,
    LONG* const list,
    LONG* const paragraph,
    LONG* const character) noexcept {
    CComVariant raw;
    CComPtr<IDispatch> anchor;
    HRESULT status = Method(control, L"GetAnchorPos", {CComVariant(0L)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, anchor);
    }
    return SUCCEEDED(status) &&
        SUCCEEDED(ReadItemLong(anchor, L"List", list)) &&
        SUCCEEDED(ReadItemLong(anchor, L"Para", paragraph)) &&
        SUCCEEDED(ReadItemLong(anchor, L"Pos", character));
}

bool PositionFollows(
    const LONG candidateList,
    const LONG candidateParagraph,
    const LONG candidateCharacter,
    const LONG currentList,
    const LONG currentParagraph,
    const LONG currentCharacter) noexcept {
    return candidateList > currentList ||
        (candidateList == currentList &&
         (candidateParagraph > currentParagraph ||
          (candidateParagraph == currentParagraph &&
           candidateCharacter > currentCharacter)));
}

void CapturePageBreakBefore(
    IDispatch* const hwp,
    DocumentHeadStructure* const captured) noexcept {
    CComPtr<IDispatch> action;
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> paragraphShape;
    CComPtr<IDispatch> paragraphSet;
    HRESULT status = DispatchProperty(hwp, L"HAction", action);
    if (SUCCEEDED(status)) {
        status = DispatchProperty(hwp, L"HParameterSet", parameterSets);
    }
    if (SUCCEEDED(status)) {
        status = DispatchProperty(parameterSets, L"HParaShape", paragraphShape);
    }
    if (SUCCEEDED(status)) {
        status = DispatchProperty(paragraphShape, L"HSet", paragraphSet);
    }
    CComVariant ignored;
    if (SUCCEEDED(status)) {
        status = Method(
            action,
            L"GetDefault",
            {CComVariant(L"ParagraphShape"), CComVariant(paragraphSet)},
            &ignored);
    }
    CComVariant raw;
    bool pageBreakBefore = false;
    if (SUCCEEDED(status)) {
        status = PropertyGet(paragraphShape, L"PageBreakBefore", &raw);
    }
    if (SUCCEEDED(status)) {
        status = AsBool(raw, &pageBreakBefore);
    }
    if (SUCCEEDED(status)) {
        captured->pageBreakBefore = pageBreakBefore ? 1L : 0L;
    }
}

void CaptureSectionStarts(
    IDispatch* const hwp,
    const LONG currentList,
    const LONG currentParagraph,
    const LONG currentCharacter,
    DocumentHeadStructure* const captured) noexcept {
    CComVariant raw;
    if (FAILED(PropertyGet(hwp, L"HeadCtrl", &raw))) {
        return;
    }
    captured->sectionStartsInParagraph = 0;
    if (IsEmptyDispatch(raw)) {
        return;
    }
    CComPtr<IDispatch> control;
    if (FAILED(AsDispatch(raw, control))) {
        captured->sectionStartsInParagraph = -1;
        return;
    }
    bool complete = false;
    bool anchorsComplete = true;
    bool identitiesComplete = true;
    for (size_t visited = 0; visited < kControlScanLimit; ++visited) {
        CComVariant rawId;
        std::wstring id;
        const HRESULT identityStatus = PropertyGet(control, L"CtrlID", &rawId);
        if (FAILED(identityStatus) || FAILED(AsString(rawId, &id))) {
            identitiesComplete = false;
        } else if (id == L"secd") {
            LONG list = -1;
            LONG paragraph = -1;
            LONG character = -1;
            if (!ReadControlAnchor(
                    control, &list, &paragraph, &character)) {
                anchorsComplete = false;
            } else if (
                list == currentList && paragraph == currentParagraph) {
                ++captured->sectionStartsInParagraph;
            } else if (
                PositionFollows(
                    list,
                    paragraph,
                    character,
                    currentList,
                    currentParagraph,
                    currentCharacter)) {
                captured->nextSectionList = list;
                captured->nextSectionParagraph = paragraph;
                captured->nextSectionCharacter = character;
                // HeadCtrl/Next is document order. Once the first later section
                // is found, no control after it can describe the document head.
                complete = true;
                break;
            }
        }

        CComVariant rawNext;
        if (FAILED(PropertyGet(control, L"Next", &rawNext))) {
            break;
        }
        if (IsEmptyDispatch(rawNext)) {
            complete = true;
            break;
        }
        CComPtr<IDispatch> next;
        if (FAILED(AsDispatch(rawNext, next))) {
            break;
        }
        control = next;
    }
    if (!complete || !anchorsComplete || !identitiesComplete) {
        captured->sectionStartsInParagraph = -1;
        captured->nextSectionList = -1;
        captured->nextSectionParagraph = -1;
        captured->nextSectionCharacter = -1;
    }
}

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
    std::vector<DocumentSectionFingerprint>* const sections = nullptr,
    bool* const contentEmpty = nullptr) noexcept {
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
    if (contentEmpty != nullptr) {
        *contentEmpty = content.empty();
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

DocumentTextPresence CaptureDocumentTextPresence(IDispatch* const hwp) noexcept {
    CComVariant rawStarted;
    HRESULT status = Method(
        hwp,
        L"InitScan",
        {CComVariant(0L), CComVariant(kWholeDocumentScanRange), CComVariant(0L),
         CComVariant(0L), CComVariant(0L), CComVariant(0L)},
        &rawStarted);
    bool started = false;
    if (SUCCEEDED(status)) {
        status = AsBool(rawStarted, &started);
    }
    if (FAILED(status) || !started) {
        return DocumentTextPresence::Unknown;
    }

    bool valid = true;
    bool complete = false;
    bool present = false;
    for (size_t iteration = 0; iteration < kWholeDocumentScanLimit; ++iteration) {
        BSTR chunk = nullptr;
        CComVariant chunkArgument;
        chunkArgument.vt = VT_BSTR | VT_BYREF;
        chunkArgument.pbstrVal = &chunk;
        CComVariant rawState;
        const HRESULT textStatus =
            Method(hwp, L"GetText", {chunkArgument}, &rawState);
        LONG state = 0;
        valid =
            SUCCEEDED(textStatus) && SUCCEEDED(AsLong(rawState, &state));
        if (valid && state == 2 && chunk != nullptr) {
            const UINT length = SysStringLen(chunk);
            for (UINT index = 0; index < length; ++index) {
                const wchar_t character = chunk[index];
                if (character != L'\r' && character != L'\n' &&
                    character != L'\f') {
                    present = true;
                    break;
                }
            }
        }
        if (chunk != nullptr) {
            SysFreeString(chunk);
        }
        if (!valid || state >= 101) {
            valid = false;
            break;
        }
        if (state <= 1) {
            complete = true;
            break;
        }
    }
    const HRESULT released = Method(hwp, L"ReleaseScan", {}, nullptr);
    if (present) {
        return DocumentTextPresence::Present;
    }
    return valid && complete && SUCCEEDED(released)
        ? DocumentTextPresence::Absent
        : DocumentTextPresence::Unknown;
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

DocumentHeadStructure CaptureDocumentHeadStructure(
    IDispatch* const hwp) noexcept {
    DocumentHeadStructure captured;
    if (hwp == nullptr) {
        return captured;
    }
    LONG list = -1;
    LONG paragraph = -1;
    LONG character = -1;
    CComVariant listArgument;
    listArgument.vt = VT_I4 | VT_BYREF;
    listArgument.plVal = &list;
    CComVariant paragraphArgument;
    paragraphArgument.vt = VT_I4 | VT_BYREF;
    paragraphArgument.plVal = &paragraph;
    CComVariant characterArgument;
    characterArgument.vt = VT_I4 | VT_BYREF;
    characterArgument.plVal = &character;
    if (FAILED(Method(
            hwp,
            L"GetPos",
            {listArgument, paragraphArgument, characterArgument},
            nullptr))) {
        return captured;
    }
    CapturePageBreakBefore(hwp, &captured);
    CaptureSectionStarts(
        hwp,
        list,
        paragraph,
        character,
        &captured);
    return captured;
}

DocumentFingerprint CaptureDocumentFingerprint(
    IDispatch* const hwp,
    const bool captureDocumentHash) noexcept {
    DocumentFingerprint fingerprint;
    fingerprint.state = CaptureDocumentState(hwp);
    const bool textCaptured = CaptureTextFile(
        hwp,
        L"TEXT",
        false,
        &fingerprint.textHash,
        &fingerprint.textLength);
    fingerprint.documentCaptured = captureDocumentHash &&
        CaptureTextFile(
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
        fingerprint.documentCaptured;
    return fingerprint;
}

DocumentContentSignature CaptureDocumentContentSignature(
    IDispatch* const hwp) noexcept {
    DocumentContentSignature signature;
    const DocumentFingerprint fingerprint = CaptureDocumentFingerprint(hwp);
    signature.pageCount = fingerprint.state.pageCount;
    signature.controlCount = fingerprint.state.controlCount;
    signature.controlHash = fingerprint.state.controlHash;
    signature.textHash = fingerprint.textHash;
    signature.textLength = fingerprint.textLength;
    signature.textEmpty = fingerprint.textLength == 0;
    signature.textPresence = !signature.textEmpty
        ? DocumentTextPresence::Present
        : CaptureDocumentTextPresence(hwp);
    signature.documentHash = fingerprint.documentHash;
    signature.documentLength = fingerprint.documentLength;
    signature.captured = fingerprint.captured;
    return signature;
}

std::wstring FormatDocumentContentSignature(
    const DocumentContentSignature& signature) {
    if (!signature.captured) {
        return std::wstring();
    }
    return L"SIG " + std::to_wstring(signature.pageCount) + L' ' +
        std::to_wstring(signature.controlCount) + L' ' +
        std::to_wstring(signature.controlHash) + L' ' +
        std::to_wstring(signature.textHash) + L' ' +
        std::to_wstring(signature.textLength) + L' ' +
        std::to_wstring(signature.textEmpty ? 1 : 0) + L' ' +
        std::to_wstring(
            static_cast<std::uint64_t>(signature.textPresence)) + L' ' +
        std::to_wstring(signature.documentHash) + L' ' +
        std::to_wstring(signature.documentLength);
}

bool DocumentContentSignatureIsComplete(const std::wstring& raw) {
    return ParseCompleteContentSignature(raw);
}

DocumentContentRevision CaptureDocumentContentRevision(
    IDispatch* const hwp) noexcept {
    DocumentContentRevision revision;
    if (hwp == nullptr) {
        return revision;
    }
    // Deliberately not CaptureDocumentState: `modified` and the caret move
    // without the document changing, and this token is compared for equality.
    // The same mistake is documented at DocumentContentSignature's declaration.
    CComVariant raw;
    if (SUCCEEDED(PropertyGet(hwp, L"PageCount", &raw))) {
        static_cast<void>(AsLong(raw, &revision.pageCount));
    }
    DocumentState controls;
    CaptureControls(hwp, &controls);
    revision.controlCount = controls.controlCount;
    revision.controlHash = controls.controlHash;
    // TEXT, never HWPML2X. The plain text of a document is proportional to what
    // was typed into it; the HWPML serialisation is proportional to what was
    // embedded in it, which is where the seconds live.
    const bool textCaptured = CaptureTextFile(
        hwp,
        L"TEXT",
        false,
        &revision.textHash,
        &revision.textLength);
    revision.captured = revision.pageCount > 0 &&
        revision.controlCount >= 0 &&
        textCaptured &&
        revision.textLength > 0;
    return revision;
}

std::wstring FormatDocumentContentRevision(
    const DocumentContentRevision& revision) {
    if (!revision.captured || revision.sessionTag == 0) {
        return std::wstring();
    }
    // Checked against the completeness rule before it leaves, not only where it
    // is read. A token this side minted but that side refuses is worse than no
    // token: the reader would answer "unknown" for a document that was in fact
    // observed, and nobody would learn which of the two was wrong.
    const std::wstring formatted = ContentRevisionLine(revision);
    return DocumentContentRevisionIsComplete(formatted)
        ? formatted
        : std::wstring();
}

bool DocumentContentRevisionIsComplete(const std::wstring& raw) {
    return ParseCompleteContentRevision(raw);
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

#include "ActionExecutorInternal.h"

#include <algorithm>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace hancom::actions::detail {

bool FormatLongProperty(
    IDispatch* const object,
    const wchar_t* const name,
    LONG* const value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) &&
        SUCCEEDED(AsLong(raw, value));
}

bool FormatBooleanProperty(
    IDispatch* const object,
    const wchar_t* const name,
    bool* const value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) &&
        SUCCEEDED(AsBool(raw, value));
}

bool FormatTextProperty(
    IDispatch* const object,
    const wchar_t* const name,
    std::wstring* const value) {
    CComVariant raw;
    return SUCCEEDED(PropertyGet(object, name, &raw)) &&
        SUCCEEDED(AsString(raw, value));
}

bool DefaultTextFormatParameter(
    Context* const context,
    const wchar_t* const actionName,
    const wchar_t* const parameterName,
    CComPtr<IDispatch>& parameter,
    CComPtr<IDispatch>& set,
    const std::wstring& location) {
    CComPtr<IDispatch> parameterSets;
    if (!GetDispatchProperty(
            context->hwp,
            L"HParameterSet",
            parameterSets,
            context->result,
            location) ||
        !GetDispatchProperty(
            parameterSets,
            parameterName,
            parameter,
            context->result,
            location) ||
        !GetDispatchProperty(parameter, L"HSet", set, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    const HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(actionName), CComVariant(set)},
        &ignored);
    return SUCCEEDED(status) ||
        SetError(
            context->result,
            L"TEXT_FORMAT_READBACK",
            location,
            FormatHResult(actionName, status));
}

bool ReadTextFormatFingerprint(
    IDispatch* const character,
    IDispatch* const paragraph,
    TextFormatFingerprint* const format) {
    return
        FormatTextProperty(character, L"FaceNameHangul", &format->faceName) &&
        FormatLongProperty(character, L"Height", &format->height) &&
        FormatBooleanProperty(character, L"Bold", &format->bold) &&
        FormatLongProperty(character, L"TextColor", &format->textColor) &&
        FormatLongProperty(character, L"RatioHangul", &format->widthRatio) &&
        FormatLongProperty(character, L"SpacingHangul", &format->letterSpacing) &&
        FormatLongProperty(paragraph, L"AlignType", &format->alignment) &&
        FormatLongProperty(
            paragraph,
            L"LineSpacingType",
            &format->lineSpacingType) &&
        FormatLongProperty(paragraph, L"LineSpacing", &format->lineSpacing) &&
        FormatLongProperty(paragraph, L"LeftMargin", &format->leftMargin) &&
        FormatLongProperty(paragraph, L"RightMargin", &format->rightMargin) &&
        FormatLongProperty(paragraph, L"Indentation", &format->indentation) &&
        FormatLongProperty(paragraph, L"PrevSpacing", &format->previousSpacing) &&
        FormatLongProperty(paragraph, L"NextSpacing", &format->nextSpacing);
}

bool CaptureTextFormat(
    Context* const context,
    PreservedTextFormat* const preserved,
    const std::wstring& location) {
    CComPtr<IDispatch> character;
    CComPtr<IDispatch> paragraph;
    return
        DefaultTextFormatParameter(
            context,
            L"CharShape",
            L"HCharShape",
            character,
            preserved->characterSet,
            location) &&
        DefaultTextFormatParameter(
            context,
            L"ParagraphShape",
            L"HParaShape",
            paragraph,
            preserved->paragraphSet,
            location) &&
        (ReadTextFormatFingerprint(character, paragraph, &preserved->fingerprint) ||
         SetError(
             context->result,
             L"TEXT_FORMAT_READBACK",
             location,
             L"character or paragraph format could not be captured"));
}

bool ExecuteCapturedFormat(
    Context* const context,
    const wchar_t* const actionName,
    IDispatch* const set,
    const std::wstring& location) {
    bool executed = false;
    return
        CallBooleanMethod(
            context->action,
            L"Execute",
            {CComVariant(actionName), CComVariant(set)},
            &executed,
            context->result,
            location) &&
        (executed ||
         SetError(
             context->result,
             L"TEXT_FORMAT_APPLY",
             location,
             std::wstring(actionName) + L" returned false"));
}

bool SameTextFormat(
    const TextFormatFingerprint& left,
    const TextFormatFingerprint& right) noexcept {
    return left.faceName == right.faceName &&
        left.height == right.height &&
        left.bold == right.bold &&
        left.textColor == right.textColor &&
        left.widthRatio == right.widthRatio &&
        left.letterSpacing == right.letterSpacing &&
        left.alignment == right.alignment &&
        left.lineSpacingType == right.lineSpacingType &&
        left.lineSpacing == right.lineSpacing &&
        left.leftMargin == right.leftMargin &&
        left.rightMargin == right.rightMargin &&
        left.indentation == right.indentation &&
        left.previousSpacing == right.previousSpacing &&
        left.nextSpacing == right.nextSpacing;
}

LONG ReferenceHwpAlignment(const LONG alignment) noexcept {
    switch (alignment) {
    case 0: return 1;
    case 1: return 3;
    case 2: return 2;
    default: return 0;
    }
}

bool MatchesExpectedReferenceTextFormat(
    const TextFormatFingerprint& actual,
    const hancom::reference_layout::Style& expected) noexcept {
    return
        (expected.fontName.empty() || actual.faceName == expected.fontName) &&
        (expected.fontSize < 0 || actual.height == expected.fontSize) &&
        (expected.bold < 0 || actual.bold == (expected.bold != 0)) &&
        (expected.textColor < 0 || actual.textColor == expected.textColor) &&
        (expected.widthRatio < 0 || actual.widthRatio == expected.widthRatio) &&
        (expected.letterSpacing < -50 ||
         actual.letterSpacing == expected.letterSpacing) &&
        (expected.alignment < 0 ||
         actual.alignment == ReferenceHwpAlignment(expected.alignment)) &&
        (expected.lineSpacingType < 0 ||
         actual.lineSpacingType == expected.lineSpacingType) &&
        (expected.lineSpacing < 0 ||
         actual.lineSpacing == expected.lineSpacing) &&
        (expected.previousSpacing < 0 ||
         actual.previousSpacing == expected.previousSpacing) &&
        (expected.nextSpacing < 0 ||
         actual.nextSpacing == expected.nextSpacing);
}

bool ApplyAndVerifyTextFormat(
    Context* const context,
    const PreservedTextFormat& preserved,
    const std::wstring& location) {
    if (!ExecuteCapturedFormat(
            context,
            L"CharShape",
            preserved.characterSet,
            location) ||
        !ExecuteCapturedFormat(
            context,
            L"ParagraphShape",
            preserved.paragraphSet,
            location)) {
        return false;
    }
    PreservedTextFormat after;
    if (!CaptureTextFormat(context, &after, location)) {
        return false;
    }
    return SameTextFormat(preserved.fingerprint, after.fingerprint) ||
        SetError(
            context->result,
            L"TEXT_FORMAT_READBACK",
            location,
            L"character or paragraph format changed after text replacement");
}

std::wstring NormalizeParagraphText(const std::wstring& value) {
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

bool SameParagraphText(
    const std::wstring& left,
    const std::wstring& right) {
    return NormalizeParagraphText(left) == NormalizeParagraphText(right);
}

bool SetCellText(Context* const context, const Command& command) {
    const std::wstring address = NormalizeAddress(command.first);
    if (!GoToCell(context, address)) {
        return false;
    }
    Position insertionStart;
    if (!command.second.empty() &&
        !GetPosition(context->hwp, &insertionStart, context->result)) {
        return false;
    }
    if (!RunAction(context->action, L"SelectAll", context->result, address)) {
        return false;
    }
    Selection before;
    std::wstring current;
    if (!GetSelection(context->hwp, &before, context->result) ||
        !ReadSelectedText(context, &current, address)) {
        return false;
    }
    if (command.hasExpectedText &&
        !SameParagraphText(current, command.expectedText)) {
        return SetError(
            context->result,
            L"STALE_CELL_TEXT",
            address,
            L"cell text changed before the style-preserving replacement");
    }
    const bool preserveExistingFormat =
        command.preserveFormat && !current.empty();
    PreservedTextFormat preserved;
    if (preserveExistingFormat &&
        !CaptureTextFormat(context, &preserved, address)) {
        return false;
    }
    if (!command.second.empty() &&
        (before.mode & kSelectionModeMask) == kSelectionText &&
        before.start.list == insertionStart.list &&
        before.end.list == insertionStart.list &&
        !SamePosition(before.start, before.end)) {
        insertionStart = before.start;
    }
    const bool replaced = command.second.empty()
        ? RunAction(context->action, L"Delete", context->result, address)
        : InsertText(context, command.second, address);
    if (!replaced) {
        return false;
    }
    if (command.second.empty()) {
        context->result->partialMutation = true;
        if (!GoToCell(context, address) ||
            !RunAction(context->action, L"SelectAll", context->result, address)) {
            return false;
        }
        std::wstring after;
        return
            ReadSelectedText(context, &after, address) &&
            (after.empty() ||
             SetError(
                 context->result,
                 L"TEXT_PATCH_READBACK",
                 address,
                 L"cell text is not empty after replacement"));
    }
    Position end;
    if (!GetPosition(context->hwp, &end, context->result) ||
        !SelectTextRange(context, insertionStart, end, address)) {
        return false;
    }
    if (preserveExistingFormat &&
        !ApplyAndVerifyTextFormat(context, preserved, address)) {
        return false;
    }
    std::wstring inserted;
    return
        ReadSelectedText(context, &inserted, address) &&
        (SameParagraphText(inserted, command.second) ||
         SetError(
             context->result,
             L"TEXT_PATCH_READBACK",
             address,
             L"cell replacement readback does not match the requested text"));
}

bool ReplaceSelection(Context* const context, const Command& command) {
    Selection before;
    if (!GetSelection(context->hwp, &before, context->result)) {
        return false;
    }
    if (!before.selected) {
        return SetError(
            context->result,
            L"NO_SELECTION",
            L"selection",
            L"the replacement command requires an active text selection");
    }
    if (before.start.list != before.end.list) {
        return SetError(
            context->result,
            L"CROSS_CONTROL_SELECTION",
            L"selection",
            L"the replacement selection crosses HWP controls");
    }

    CComVariant raw;
    std::wstring selected;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"UNICODE"), CComVariant(L"saveblock:true")},
        &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, &selected);
    }
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"SELECTION_TEXT",
            L"selection",
            FormatHResult(L"GetTextFile", status));
    }
    if (selected != command.first) {
        return SetError(
            context->result,
            L"STALE_SELECTION_TEXT",
            L"selection",
            L"selected text changed after the request was prepared");
    }

    Selection confirmed;
    if (!GetSelection(context->hwp, &confirmed, context->result)) {
        return false;
    }
    if (confirmed.selected != before.selected ||
        confirmed.start.list != before.start.list ||
        confirmed.start.paragraph != before.start.paragraph ||
        confirmed.start.character != before.start.character ||
        confirmed.end.list != before.end.list ||
        confirmed.end.paragraph != before.end.paragraph ||
        confirmed.end.character != before.end.character) {
        return SetError(
            context->result,
            L"STALE_SELECTION",
            L"selection",
            L"selection changed while its text was verified");
    }
    return InsertText(context, command.second, L"selection");
}

bool TextMatchesAt(
    const std::wstring& actual,
    const std::wstring& expected,
    const size_t offset,
    const bool matchCase) {
    if (offset > actual.size() || expected.size() > actual.size() - offset) {
        return false;
    }
    for (size_t index = 0; index < actual.size(); ++index) {
        if (index == expected.size()) {
            return true;
        }
        const wchar_t current = actual[offset + index];
        if ((matchCase && current != expected[index]) ||
            (!matchCase && towlower(current) != towlower(expected[index]))) {
            return false;
        }
    }
    return expected.size() == actual.size() - offset;
}

bool TextMatches(
    const std::wstring& actual,
    const std::wstring& expected,
    const bool matchCase) {
    const std::wstring normalizedActual = NormalizeParagraphText(actual);
    const std::wstring normalizedExpected = NormalizeParagraphText(expected);
    return normalizedActual.size() == normalizedExpected.size() &&
        TextMatchesAt(
            normalizedActual,
            normalizedExpected,
            0,
            matchCase);
}

bool PatchSelectedText(
    Context* const context,
    const std::wstring& expected,
    const bool requireExpected,
    const bool matchCase,
    const std::wstring& replacement,
    const std::wstring& location,
    const bool preserveFormat = false) {
    Selection before;
    if (!GetSelection(context->hwp, &before, context->result)) {
        return false;
    }
    if ((before.mode & kSelectionModeMask) != kSelectionText) {
        return SetError(
            context->result,
            L"NON_TEXT_SELECTION",
            location,
            L"text.patch cannot replace a non-text selection");
    }
    if (!before.selected || before.start.list != before.end.list) {
        return SetError(
            context->result,
            before.selected ? L"CROSS_CONTROL_SELECTION" : L"NO_SELECTION",
            location,
            L"text.patch requires one active text range");
    }
    std::wstring selected;
    if (!ReadSelectedText(context, &selected, location)) {
        return false;
    }
    if (requireExpected && !TextMatches(selected, expected, matchCase)) {
        return SetError(
            context->result,
            L"STALE_SELECTION_TEXT",
            location,
            L"selected text changed before text.patch");
    }
    Selection confirmed;
    if (!GetSelection(context->hwp, &confirmed, context->result)) {
        return false;
    }
    if (!SameSelection(before, confirmed)) {
        return SetError(
            context->result,
            L"STALE_SELECTION",
            location,
            L"selection changed while text.patch verified its text");
    }
    PreservedTextFormat preserved;
    if (preserveFormat &&
        !CaptureTextFormat(context, &preserved, location)) {
        return false;
    }
    const bool replaced = replacement.empty()
        ? RunAction(context->action, L"Delete", context->result, location)
        : InsertText(context, replacement, location);
    if (!replaced) {
        return false;
    }
    if (replacement.empty()) {
        context->result->partialMutation = true;
    }
    Position end;
    if (!GetPosition(context->hwp, &end, context->result)) {
        return false;
    }
    if (replacement.empty()) {
        Selection afterDeletion;
        if (!SamePosition(before.start, end) ||
            !GetSelection(context->hwp, &afterDeletion, context->result) ||
            afterDeletion.selected) {
            return SetError(
                context->result,
                L"TEXT_PATCH_READBACK",
                location,
                L"deleted range did not collapse to its verified start");
        }
        return true;
    }
    if (!SelectTextRange(context, before.start, end, location)) {
        return false;
    }
    if (preserveFormat &&
        !ApplyAndVerifyTextFormat(context, preserved, location)) {
        return false;
    }
    std::wstring inserted;
    if (!ReadSelectedText(context, &inserted, location) ||
        !SameParagraphText(inserted, replacement)) {
        return SetError(
            context->result,
            L"TEXT_PATCH_READBACK",
            location,
            L"reselected text does not match the requested replacement");
    }
    return true;
}

bool PatchCurrentText(Context* const context, const Command& command) {
    Selection before;
    if (!GetSelection(context->hwp, &before, context->result)) {
        return false;
    }
    if (before.selected) {
        return PatchSelectedText(
            context,
            command.first,
            command.hasExpectedText,
            true,
            command.second,
            L"current",
            command.preserveFormat);
    }
    if ((before.mode & kSelectionModeMask) != kSelectionNone) {
        return SetError(
            context->result,
            L"NON_TEXT_SELECTION",
            L"current",
            L"text.patch cannot insert into a non-text selection");
    }
    if (command.hasExpectedText && !command.first.empty()) {
        return SetError(
            context->result,
            L"NO_SELECTION",
            L"current",
            L"expected old text was supplied but the current cursor has no selection");
    }
    Position start;
    if (!GetPosition(context->hwp, &start, context->result) ||
        !InsertText(context, command.second, L"current")) {
        return false;
    }
    Position end;
    if (!GetPosition(context->hwp, &end, context->result)) {
        return false;
    }
    if (command.second.empty()) {
        return SamePosition(start, end)
            ? true
            : SetError(
                  context->result,
                  L"TEXT_PATCH_READBACK",
                  L"current",
                  L"empty insertion changed the current position");
    }
    if (!SelectTextRange(context, start, end, L"current")) {
        return false;
    }
    std::wstring inserted;
    if (!ReadSelectedText(context, &inserted, L"current") ||
        !SameParagraphText(inserted, command.second)) {
        return SetError(
            context->result,
            L"TEXT_PATCH_READBACK",
            L"current",
            L"reselected insertion does not match the requested text");
    }
    return true;
}

struct TextMatch {
    Selection selection;
};

std::wstring TextMatchKey(const Selection& match) {
    return std::to_wstring(match.start.list) + L":" +
        std::to_wstring(match.start.paragraph) + L":" +
        std::to_wstring(match.start.character) + L"-" +
        std::to_wstring(match.end.list) + L":" +
        std::to_wstring(match.end.paragraph) + L":" +
        std::to_wstring(match.end.character);
}

std::wstring TextMatchCandidates(const std::vector<TextMatch>& matches) {
    std::wostringstream encoded;
    for (size_t index = 0; index < matches.size(); ++index) {
        if (index != 0) {
            encoded << L'|';
        }
        encoded << TextMatchKey(matches[index].selection);
    }
    return encoded.str();
}

Position TextPositionAtOffset(
    const Position& start,
    const std::wstring& text,
    const size_t offset) {
    Position position = start;
    for (size_t index = 0; index < offset; ++index) {
        if (text[index] == L'\r' &&
            index + 1 < offset &&
            text[index + 1] == L'\n') {
            ++position.paragraph;
            position.character = 0;
            ++index;
        } else if (text[index] == L'\r' || text[index] == L'\n') {
            ++position.paragraph;
            position.character = 0;
        } else {
            ++position.character;
        }
    }
    return position;
}

bool SelectCellTextPatchMatch(
    Context* const context,
    const Command& command,
    const Selection& original,
    const std::wstring& location) {
    constexpr size_t kMaximumCandidates = 24;
    if (!RunAction(context->action, L"SelectAll", context->result, location)) {
        return false;
    }
    Selection cellSelection;
    std::wstring cellText;
    if (!GetSelection(context->hwp, &cellSelection, context->result) ||
        !cellSelection.selected ||
        cellSelection.start.list != cellSelection.end.list ||
        !ReadSelectedText(context, &cellText, location)) {
        return SetError(
            context->result,
            L"TEXT_FIND_STATE",
            location,
            L"table cell did not expose one text selection");
    }
    std::vector<TextMatch> matches;
    for (size_t offset = 0;
         offset + command.first.size() <= cellText.size();) {
        if (!TextMatchesAt(cellText, command.first, offset, command.matchCase)) {
            ++offset;
            continue;
        }
        const Position start =
            TextPositionAtOffset(cellSelection.start, cellText, offset);
        const Position end = TextPositionAtOffset(
            cellSelection.start,
            cellText,
            offset + command.first.size());
        matches.push_back(TextMatch{
            Selection{true, kSelectionText, start, end},
        });
        if (command.occurrence > 0 &&
            matches.size() == static_cast<size_t>(command.occurrence)) {
            return SelectTextRange(context, start, end, location);
        }
        if (command.occurrence == 0 && matches.size() == kMaximumCandidates) {
            break;
        }
        offset += command.first.size();
    }
    if (!RestoreTextPosition(context, original, location + L".restore")) {
        return false;
    }
    if (matches.empty()) {
        return SetError(
            context->result,
            command.occurrence > 0
                ? L"TEXT_OCCURRENCE_NOT_FOUND"
                : L"TEXT_NOT_FOUND",
            location,
            L"text.patch found no matching text in the target cell before mutation");
    }
    if (command.occurrence > 0) {
        return SetError(
            context->result,
            L"TEXT_OCCURRENCE_NOT_FOUND",
            location,
            L"requested text occurrence was not found in the target cell before mutation");
    }
    if (matches.size() > 1) {
        return SetError(
            context->result,
            L"AMBIGUOUS_TEXT_MATCH",
            TextMatchCandidates(matches),
            L"multiple text matches in the target cell require an explicit occurrence");
    }
    return SelectTextRange(
        context,
        matches.front().selection.start,
        matches.front().selection.end,
        location);
}

bool PrepareForwardFind(
    Context* const context,
    const std::wstring& expected,
    const bool matchCase,
    CComPtr<IDispatch>& set) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> findReplace;
    if (!GetDispatchProperty(
            context->hwp,
            L"HParameterSet",
            parameterSets,
            context->result,
            L"text.find") ||
        !GetDispatchProperty(
            parameterSets,
            L"HFindReplace",
            findReplace,
            context->result,
            L"text.find") ||
        !GetDispatchProperty(
            findReplace,
            L"HSet",
            set,
            context->result,
            L"text.find")) {
        return false;
    }
    CComVariant ignored;
    const HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"ForwardFind"), CComVariant(set)},
        &ignored);
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"ACTION_DEFAULT",
            L"text.find",
            FormatHResult(L"ForwardFind", status));
    }
    return PutItemOrProperty(
               findReplace,
               L"FindString",
               CComVariant(expected.c_str()),
               context->result,
               L"text.find") &&
        PutItemOrProperty(
               findReplace,
               L"Direction",
               CComVariant(0L),
               context->result,
               L"text.find") &&
        PutItemOrProperty(
               findReplace,
               L"MatchCase",
               BooleanVariant(matchCase),
               context->result,
               L"text.find") &&
        PutItemOrProperty(
               findReplace,
               L"IgnoreMessage",
               BooleanVariant(true),
               context->result,
               L"text.find");
}

bool SelectTextPatchMatch(
    Context* const context,
    const Command& command,
    const Selection& original,
    const LONG listFilter) {
    constexpr size_t kMaximumCandidates = 24;
    constexpr size_t kMaximumSearches = 20'000;
    CComPtr<IDispatch> findSet;
    if (!PrepareForwardFind(context, command.first, command.matchCase, findSet)) {
        return false;
    }
    const bool positioned = listFilter < 0
        ? RunVerifiedNavigationAction(
              context->action,
              L"MoveDocBegin",
              context->result,
              L"text.find")
        : SetPosition(
              context->hwp,
              Position{listFilter, 0, 0},
              context->result,
              L"text.find");
    if (!positioned) {
        return false;
    }
    std::set<std::wstring> seen;
    std::vector<TextMatch> matches;
    bool exhausted = false;
    for (size_t search = 0; search < kMaximumSearches; ++search) {
        bool found = false;
        if (!CallBooleanMethod(
                context->action,
                L"Execute",
                {CComVariant(L"ForwardFind"), CComVariant(findSet)},
                &found,
                context->result,
                L"text.find")) {
            return false;
        }
        if (!found) {
            exhausted = true;
            break;
        }
        Selection current;
        if (!GetSelection(context->hwp, &current, context->result) ||
            !current.selected || current.start.list != current.end.list) {
            return SetError(
                context->result,
                L"TEXT_FIND_STATE",
                L"text.find",
                L"ForwardFind did not return one text selection");
        }
        const std::wstring key = TextMatchKey(current);
        if (!seen.insert(key).second) {
            exhausted = true;
            break;
        }
        if (listFilter < 0 || current.start.list == listFilter) {
            matches.push_back(TextMatch{current});
            if (command.occurrence > 0 &&
                matches.size() == static_cast<size_t>(command.occurrence)) {
                return true;
            }
            if (command.occurrence == 0 && matches.size() == kMaximumCandidates) {
                break;
            }
        }
        if (!SetPosition(
                context->hwp,
                current.end,
                context->result,
                L"text.find")) {
            return false;
        }
    }
    if (command.occurrence > 0) {
        if (!RestoreTextPosition(context, original, L"text.find.restore")) {
            return false;
        }
        return SetError(
            context->result,
            exhausted ? L"TEXT_OCCURRENCE_NOT_FOUND" : L"TEXT_SEARCH_LIMIT",
            L"text.find",
            L"requested text occurrence was not found before mutation");
    }
    if (!exhausted && matches.size() < 2) {
        if (!RestoreTextPosition(context, original, L"text.find.restore")) {
            return false;
        }
        return SetError(
            context->result,
            L"TEXT_SEARCH_LIMIT",
            L"text.find",
            L"text search limit was reached before uniqueness could be proven");
    }
    if (matches.empty()) {
        if (!RestoreTextPosition(context, original, L"text.find.restore")) {
            return false;
        }
        return SetError(
            context->result,
            exhausted ? L"TEXT_NOT_FOUND" : L"TEXT_SEARCH_LIMIT",
            L"text.find",
            L"text.patch found no matching text before mutation");
    }
    if (matches.size() > 1) {
        if (!RestoreTextPosition(context, original, L"text.find.restore")) {
            return false;
        }
        return SetError(
            context->result,
            L"AMBIGUOUS_TEXT_MATCH",
            TextMatchCandidates(matches),
            L"multiple text matches require an explicit occurrence");
    }
    return SelectTextRange(
        context,
        matches.front().selection.start,
        matches.front().selection.end,
        L"text.find");
}

bool PatchSearchedText(
    Context* const context,
    const Command& command,
    const LONG listFilter,
    const Selection& original,
    const std::wstring& location) {
    if (command.occurrence < 0 || command.occurrence > 20'000) {
        return SetError(
            context->result,
            L"TEXT_OCCURRENCE",
            location,
            L"text occurrence must be between 1 and 20000 when supplied");
    }
    return SelectTextPatchMatch(context, command, original, listFilter) &&
        PatchSelectedText(
            context,
            command.first,
            true,
            command.matchCase,
            command.second,
            location,
            command.preserveFormat);
}

using ShadowCellKey = std::pair<std::wstring, std::wstring>;

static thread_local const Request* reusableTableTextFillRequest = nullptr;

bool SameControlIdentity(
    IDispatch* const left,
    IDispatch* const right) {
    if (left == nullptr || right == nullptr) {
        return false;
    }
    if (left == right) {
        return true;
    }
    IUnknown* leftIdentity = nullptr;
    IUnknown* rightIdentity = nullptr;
    const HRESULT leftStatus = left->QueryInterface(
        IID_IUnknown,
        reinterpret_cast<void**>(&leftIdentity));
    const HRESULT rightStatus = right->QueryInterface(
        IID_IUnknown,
        reinterpret_cast<void**>(&rightIdentity));
    const bool same =
        SUCCEEDED(leftStatus) &&
        SUCCEEDED(rightStatus) &&
        leftIdentity == rightIdentity;
    if (leftIdentity != nullptr) {
        leftIdentity->Release();
    }
    if (rightIdentity != nullptr) {
        rightIdentity->Release();
    }
    return same;
}

bool SelectTableForCellPatch(
    Context* const context,
    const std::wstring& tableId) {
    CComPtr<IDispatch> selectedControl;
    if (!SelectControl(context, tableId, selectedControl)) {
        return false;
    }
    const bool safeToReuse =
        context->request == reusableTableTextFillRequest &&
        context->tableId == tableId &&
        SameControlIdentity(context->table, selectedControl);
    if (!safeToReuse && !CaptureCurrentTable(context)) {
        return false;
    }
    return context->tableId == tableId ||
        SetError(
            context->result,
            L"WRONG_CONTROL",
            tableId,
            L"captured table identity does not match the text.patch target");
}

bool PatchText(Context* const context, const Command& command) {
    if (command.name == L"CURRENT") {
        return PatchCurrentText(context, command);
    }
    if (command.name == L"RANGE") {
        const Position start{command.list, command.paragraph, command.character};
        const Position end{
            command.endList,
            command.endParagraph,
            command.endCharacter};
        return SelectTextRange(context, start, end, L"range") &&
            PatchSelectedText(
                context,
                command.first,
                true,
                true,
                command.second,
                L"range",
                command.preserveFormat);
    }
    Selection original;
    if (!GetSelection(context->hwp, &original, context->result)) {
        return false;
    }
    if (command.name == L"FIND") {
        return PatchSearchedText(
            context,
            command,
            -1,
            original,
            L"text.find");
    }
    if (command.name == L"CELL") {
        if (!SelectTableForCellPatch(context, command.tableInstanceId) ||
            !GoToCell(context, command.cellAddress)) {
            return false;
        }
        const std::wstring address = NormalizeAddress(command.cellAddress);
        return
            SelectCellTextPatchMatch(context, command, original, address) &&
            PatchSelectedText(
                context,
                command.first,
                true,
                command.matchCase,
                command.second,
                address,
                command.preserveFormat);
    }
    return SetError(
        context->result,
        L"TEXT_PATCH_TARGET",
        command.name,
        L"unsupported text.patch target");
}

bool IsTableTextFillBatch(const Request& request) {
    if (request.commands.size() < 3 ||
        request.commands[0].kind != CommandKind::SelectControl ||
        request.commands[1].kind != CommandKind::CaptureTable) {
        return false;
    }
    const std::wstring& selectedTableId = request.commands[0].first;
    bool hasTextMutation = false;
    for (size_t index = 2; index < request.commands.size(); ++index) {
        const Command& command = request.commands[index];
        if (command.kind == CommandKind::SetCellText) {
            hasTextMutation = true;
            continue;
        }
        if (command.kind == CommandKind::TextPatch &&
            command.name == L"CELL" &&
            command.tableInstanceId == selectedTableId) {
            hasTextMutation = true;
            continue;
        }
        return false;
    }
    return hasTextMutation;
}

bool ValidateCellTextPatchLimit(
    ExecutionResult* const result,
    const Request& request) {
    constexpr size_t kMaximumCellTextPatches = 100;
    if (request.commands.size() <= kMaximumCellTextPatches) {
        return true;
    }
    std::map<ShadowCellKey, size_t> patchCounts;
    for (const Command& command : request.commands) {
        if (command.kind != CommandKind::TextPatch ||
            command.name != L"CELL") {
            continue;
        }
        const std::wstring address = NormalizeAddress(command.cellAddress);
        const ShadowCellKey key{command.tableInstanceId, address};
        const size_t count = ++patchCounts[key];
        if (count <= kMaximumCellTextPatches) {
            continue;
        }
        result->failedStep = CommandStep(command);
        return SetError(
            result,
            L"TEXT_PATCH_LIMIT",
            command.tableInstanceId + L":" + address,
            L"a single table cell cannot contain more than 100 text.patch commands");
    }
    return true;
}

bool ReadShadowCell(
    Context* const context,
    const std::wstring& tableId,
    const std::wstring& requestedAddress,
    std::map<ShadowCellKey, std::wstring>* const cells,
    std::wstring** const value) {
    const std::wstring address = NormalizeAddress(requestedAddress);
    const ShadowCellKey key{tableId, address};
    const auto existing = cells->find(key);
    if (existing != cells->end()) {
        *value = &existing->second;
        return true;
    }
    if (!SelectTableForCellPatch(context, tableId) ||
        !GoToCell(context, address) ||
        !RunAction(context->action, L"SelectAll", context->result, address)) {
        return false;
    }
    std::wstring current;
    if (!ReadSelectedText(context, &current, address)) {
        return false;
    }
    const auto inserted = cells->emplace(key, std::move(current));
    *value = &inserted.first->second;
    return true;
}

bool ApplyShadowPatch(
    ExecutionResult* const result,
    const Command& command,
    const std::wstring& address,
    std::wstring* const value) {
    if (command.occurrence < 0 || command.occurrence > 20'000) {
        return SetError(
            result,
            L"TEXT_OCCURRENCE",
            address,
            L"text occurrence must be between 1 and 20000 when supplied");
    }
    if (command.first.empty()) {
        return SetError(
            result,
            L"TEXT_PATCH_EXPECTED",
            address,
            L"table cell text.patch requires non-empty expected text");
    }
    std::vector<size_t> matches;
    for (size_t offset = 0;
         offset + command.first.size() <= value->size();) {
        if (TextMatchesAt(
                *value,
                command.first,
                offset,
                command.matchCase)) {
            matches.push_back(offset);
            offset += command.first.size();
        } else {
            ++offset;
        }
    }
    if (matches.empty()) {
        return SetError(
            result,
            command.occurrence > 0
                ? L"TEXT_OCCURRENCE_NOT_FOUND"
                : L"TEXT_NOT_FOUND",
            address,
            L"text.patch found no matching text during table preflight");
    }
    size_t selected = 0;
    if (command.occurrence > 0) {
        const size_t requested =
            static_cast<size_t>(command.occurrence);
        if (requested > matches.size()) {
            return SetError(
                result,
                L"TEXT_OCCURRENCE_NOT_FOUND",
                address,
                L"requested occurrence was not found during table preflight");
        }
        selected = matches[requested - 1];
    } else {
        if (matches.size() != 1) {
            return SetError(
                result,
                L"AMBIGUOUS_TEXT_MATCH",
                address,
                L"multiple table text matches require an explicit occurrence");
        }
        selected = matches.front();
    }
    value->replace(selected, command.first.size(), command.second);
    return true;
}

bool PreflightTableTextCommands(
    Context* const context,
    const Request& request) {
    reusableTableTextFillRequest = nullptr;
    if (!ValidateCellTextPatchLimit(context->result, request)) {
        return false;
    }
    const bool isTableTextFillBatch = IsTableTextFillBatch(request);
    reusableTableTextFillRequest =
        isTableTextFillBatch ? &request : nullptr;
    if (!isTableTextFillBatch) {
        return true;
    }
    Position cursor;
    Selection selection;
    if (!GetPosition(context->hwp, &cursor, context->result) ||
        !GetSelection(
            context->hwp,
            &selection,
            context->result,
            SelectionCapturePolicy::RequiredControl)) {
        return false;
    }
    if (!hancom::com_state::CanRestoreSelection(selection)) {
        return SetError(
            context->result,
            L"UNSUPPORTED_SELECTION",
            L"table.preflight",
            L"the active HWP selection cannot be restored after table preflight");
    }
    std::map<ShadowCellKey, std::wstring> cells;
    const std::wstring selectedTableId = request.commands[0].first;
    bool validated = true;
    for (size_t index = 2; index < request.commands.size(); ++index) {
        const Command& command = request.commands[index];
        context->result->failedStep = CommandStep(command);
        const std::wstring tableId =
            command.kind == CommandKind::TextPatch
            ? command.tableInstanceId
            : selectedTableId;
        const std::wstring address = NormalizeAddress(
            command.kind == CommandKind::TextPatch
            ? command.cellAddress
            : command.first);
        std::wstring* current = nullptr;
        if (!ReadShadowCell(
                context,
                tableId,
                address,
                &cells,
                &current)) {
            validated = false;
            break;
        }
        if (command.kind == CommandKind::SetCellText) {
            if (command.hasExpectedText &&
                !SameParagraphText(*current, command.expectedText)) {
                SetError(
                    context->result,
                    L"STALE_CELL_TEXT",
                    address,
                    L"cell text changed before the table fill batch");
                validated = false;
                break;
            }
            *current = command.second;
        } else if (!ApplyShadowPatch(
                       context->result,
                       command,
                       address,
                       current)) {
            validated = false;
            break;
        }
    }
    const Error validationError = context->result->error;
    const std::wstring validationStep = context->result->failedStep;
    const bool restored = hancom::com_state::RestoreSelection(
        context->hwp,
        cursor,
        selection);
    context->table = nullptr;
    context->tableId.clear();
    context->currentCell.clear();
    context->topology.Clear();
    if (!restored) {
        context->result->retrySafe = false;
        context->result->failedStep = L"table.preflight";
        return SetError(
            context->result,
            L"STATE_RESTORE",
            L"table.preflight",
            L"cursor or selection restoration failed after table preflight");
    }
    if (!validated) {
        context->result->error = validationError;
        context->result->failedStep = validationStep;
        return false;
    }
    context->result->failedStep.clear();
    return true;
}

}

#include "ActionExecutorInternal.h"
#include "ParagraphText.h"
#include "TextPatchReadback.h"

#include <algorithm>
#include <chrono>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace hancom::actions::detail {

using hancom::text::SameParagraphText;
using hancom::text_patch::MatchesExpectedReadback;
using hancom::text_patch::MatchesReplacementReadback;
using hancom::text_patch::ReadbackPolicy;

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

bool CapturePreparedTextFormat(
    Context* const context,
    const Request& request,
    const size_t patchIndex,
    PreparedTextPatchTarget* const captured,
    const std::wstring& location) {
    std::set<std::wstring> characterProperties;
    std::set<std::wstring> paragraphProperties;
    for (size_t index = patchIndex + 1; index < request.commands.size(); ++index) {
        const Command& command = request.commands[index];
        if (command.kind == CommandKind::TextPatch) {
            break;
        }
        if (command.kind != CommandKind::Action) {
            continue;
        }
        std::set<std::wstring>* properties = nullptr;
        if (command.name == L"CharShape") {
            properties = &characterProperties;
        } else if (command.name == L"ParagraphShape") {
            properties = &paragraphProperties;
        } else {
            continue;
        }
        for (const Setter& setter : command.setters) {
            properties->insert(setter.path);
        }
    }

    CComPtr<IDispatch> parameter;
    CComPtr<IDispatch> set;
    if (!characterProperties.empty()) {
        if (!DefaultTextFormatParameter(
                context,
                L"CharShape",
                L"HCharShape",
                parameter,
                set,
                location)) {
            return false;
        }
        for (const std::wstring& property : characterProperties) {
            const bool read =
                property == L"Bold"
                    ? FormatBooleanProperty(parameter, L"Bold", &captured->bold)
                : property == L"Height"
                    ? FormatLongProperty(parameter, L"Height", &captured->height)
                : property == L"TextColor"
                    ? FormatLongProperty(
                          parameter, L"TextColor", &captured->textColor)
                : property.rfind(L"FaceName", 0) == 0
                    ? FormatTextProperty(
                          parameter, L"FaceNameHangul", &captured->faceName)
                : property.rfind(L"FontType", 0) == 0;
            if (!read) {
                return SetError(
                    context->result,
                    L"TEXT_FORMAT_READBACK",
                    location,
                    property + L" could not be captured");
            }
        }
    }
    if (!paragraphProperties.empty()) {
        parameter.Release();
        set.Release();
        if (!DefaultTextFormatParameter(
                context,
                L"ParagraphShape",
                L"HParaShape",
                parameter,
                set,
                location)) {
            return false;
        }
        for (const std::wstring& property : paragraphProperties) {
            const bool read =
                property == L"AlignType"
                    ? FormatLongProperty(
                          parameter, L"AlignType", &captured->alignment)
                : property == L"LineSpacing"
                    ? FormatLongProperty(
                          parameter, L"LineSpacing", &captured->lineSpacing)
                : false;
            if (!read) {
                return SetError(
                    context->result,
                    L"TEXT_FORMAT_READBACK",
                    location,
                    property + L" could not be captured");
            }
        }
    }
    return true;
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
    context->result->partialMutation = true;
    if (command.second.empty()) {
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

// The number HWP draws in front of the caret's paragraph, or an empty
// string when the paragraph has none and when the call is unavailable. Read
// only after a plain readback comparison already failed, because that is
// the only case in which the drawn number can be what made it fail.
std::wstring ReadAutomaticNumber(Context* const context) {
    CComVariant raw;
    std::wstring automaticNumber;
    HRESULT status = Method(context->hwp, L"GetHeadingString", {}, &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, &automaticNumber);
    }
    return SUCCEEDED(status) ? automaticNumber : std::wstring();
}

bool PatchSelectedText(
    Context* const context,
    const std::wstring& expected,
    const bool requireExpected,
    const bool matchCase,
    const std::wstring& replacement,
    const std::wstring& location,
    const bool preserveFormat,
    const ReadbackPolicy readbackPolicy) {
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
    if (requireExpected &&
        !MatchesExpectedReadback(
            selected,
            expected,
            matchCase,
            readbackPolicy) &&
        !MatchesExpectedReadback(
            selected,
            expected,
            ReadAutomaticNumber(context),
            matchCase,
            readbackPolicy)) {
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
    context->result->partialMutation = true;
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
        (!MatchesReplacementReadback(
             inserted,
             replacement,
             readbackPolicy) &&
         !MatchesReplacementReadback(
             inserted,
             replacement,
             ReadAutomaticNumber(context),
             readbackPolicy))) {
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
            command.preserveFormat,
            ReadbackPolicy::Exact);
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
    context->result->partialMutation = true;
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
    if (!command.hasExpectedText) {
        return true;
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

// ForwardFind answers with the coordinates it matched at, and the replacement
// deletes whatever those coordinates span. The block readback is not the same
// measure: an automatically numbered paragraph draws its number in front of
// every readback taken inside it while occupying no character cell, so the
// readback is routinely wider than the span and can never say what a deletion
// would destroy. Only the span can. A span that is already the literal is left
// untouched -- that is the ordinary match, numbered paragraphs included, and it
// costs no call. A wider span is pulled back onto the literal so the surplus
// survives, and a wider span that cannot be mapped back onto the readback fails
// closed, because a surplus that cannot be located is a surplus that must not
// be deleted.
bool NarrowForwardFindSelection(
    Context* const context,
    const Command& command,
    Selection* const selection) {
    const auto stale = [context](const wchar_t* const message) {
        return SetError(
            context->result, L"STALE_SELECTION_TEXT", L"text.find", message);
    };
    if (selection == nullptr || command.first.empty() ||
        selection->start.list != selection->end.list ||
        selection->start.paragraph != selection->end.paragraph ||
        selection->end.character < selection->start.character) {
        return stale(
            L"ForwardFind did not select one paragraph range of text");
    }
    const size_t span = static_cast<size_t>(
        selection->end.character - selection->start.character);
    if (span == command.first.size()) {
        return true;
    }
    if (span < command.first.size()) {
        return stale(
            L"ForwardFind selected fewer characters than the requested text");
    }
    std::wstring readback;
    if (!ReadSelectedText(context, &readback, L"text.find")) {
        return false;
    }
    // One character per cell holds only once the paragraph's drawn number --
    // the single prefix known to carry no cells -- is off the front. If the two
    // still disagree after that, the surplus cannot be placed and the range
    // cannot be narrowed onto the literal.
    const std::wstring automaticNumber = ReadAutomaticNumber(context);
    const std::wstring prefix =
        automaticNumber.empty() ? std::wstring() : automaticNumber + L' ';
    std::wstring body = readback;
    if (!prefix.empty() && body.size() > prefix.size() &&
        body.compare(0, prefix.size(), prefix) == 0) {
        body = body.substr(prefix.size());
    }
    if (body.size() != span ||
        body.find_first_of(L"\r\n") != std::wstring::npos ||
        command.first.find_first_of(L"\r\n") != std::wstring::npos) {
        return stale(
            L"ForwardFind selected more than the requested text and the "
            L"surplus could not be located");
    }
    size_t matchOffset = 0;
    size_t matchCount = 0;
    for (size_t offset = 0; offset + command.first.size() <= body.size();
         ++offset) {
        if (TextMatchesAt(body, command.first, offset, command.matchCase)) {
            matchOffset = offset;
            if (++matchCount > 1) {
                return stale(
                    L"ForwardFind selected more than the requested text, which "
                    L"the surplus repeats");
            }
        }
    }
    if (matchCount != 1) {
        return stale(
            L"ForwardFind selected a range that does not hold the requested "
            L"text once");
    }
    const Position literalStart{
        selection->start.list,
        selection->start.paragraph,
        selection->start.character + static_cast<LONG>(matchOffset)};
    const Position literalEnd{
        literalStart.list,
        literalStart.paragraph,
        literalStart.character + static_cast<LONG>(command.first.size())};
    Selection confirmed;
    if (!SelectTextRange(context, literalStart, literalEnd, L"text.find") ||
        !GetSelection(context->hwp, &confirmed, context->result)) {
        return false;
    }
    if (!confirmed.selected || !SamePosition(confirmed.start, literalStart) ||
        !SamePosition(confirmed.end, literalEnd)) {
        return stale(L"the range narrowed onto the requested text did not hold");
    }
    *selection = confirmed;
    return true;
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
        if (!NarrowForwardFindSelection(context, command, &current)) {
            return false;
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
            command.preserveFormat,
            ReadbackPolicy::Find);
}

using ShadowCellKey = std::pair<std::wstring, std::wstring>;

static thread_local const Request* reusableTableTextFillRequest = nullptr;

bool SelectTableForCellPatch(
    Context* const context,
    const std::wstring& tableId) {
    CComPtr<IDispatch> selectedControl;
    if (!SelectControl(context, tableId, selectedControl)) {
        return false;
    }
    // SelectControl already read the freshly selected control's instance id
    // and refused anything but tableId, so the selection is proven to be the
    // requested table. Asking on top of that whether it is the *same dispatch
    // wrapper* we cached is a question HWP always answers no to: every
    // CurSelectedCtrl read hands back a new wrapper. That never-true term made
    // each cell of a fill batch fall into CaptureCurrentTable, whose
    // topology.Clear made the next GoToCell re-inspect every cell of the
    // table -- an O(commands x table cells) walk before a single character was
    // written.
    const bool safeToReuse =
        context->request == reusableTableTextFillRequest &&
        context->table != nullptr &&
        context->tableId == tableId;
    if (safeToReuse) {
        // Keep the cell map; only refresh the wrapper to the live selection.
        context->table = selectedControl;
        return true;
    }
    if (!CaptureCurrentTable(context)) {
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
    if (command.preflightOnly) {
        return SetError(
            context->result,
            L"TEXT_PATCH_PREFLIGHT_ONLY",
            command.name,
            L"prepared text target records cannot execute as mutations");
    }
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
                command.preserveFormat,
                ReadbackPolicy::Exact);
    }
    Selection original;
    if (!GetSelection(context->hwp, &original, context->result)) {
        return false;
    }
    if (command.name == L"FIND") {
        LONG listFilter = -1;
        std::wstring location = L"text.find";
        if (!command.tableInstanceId.empty()) {
            if (command.cellAddress.empty() ||
                !SelectTableForCellPatch(context, command.tableInstanceId) ||
                !GoToCell(context, command.cellAddress)) {
                return false;
            }
            Position cell;
            if (!GetPosition(context->hwp, &cell, context->result)) {
                return false;
            }
            listFilter = cell.list;
            location = NormalizeAddress(command.cellAddress);
        }
        return PatchSearchedText(
            context,
            command,
            listFilter,
            original,
            location);
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
                command.preserveFormat,
                ReadbackPolicy::Exact);
    }
    return SetError(
        context->result,
        L"TEXT_PATCH_TARGET",
        command.name,
        L"unsupported text.patch target");
}

enum class TableTextBatchKind {
    None,
    Fill,
    Expand,
};

TableTextBatchKind ClassifyTableTextBatch(const Request& request) {
    if (request.commands.size() < 3 ||
        request.commands[0].kind != CommandKind::SelectControl ||
        request.commands[1].kind != CommandKind::CaptureTable) {
        return TableTextBatchKind::None;
    }
    const std::wstring& selectedTableId = request.commands[0].first;
    bool hasTextMutation = false;
    bool hasExpansionAnchor = false;
    bool hasAppendRow = false;
    for (size_t index = 2; index < request.commands.size(); ++index) {
        const Command& command = request.commands[index];
        if (!hasTextMutation && !hasExpansionAnchor && index == 2 &&
            command.kind == CommandKind::Cell) {
            hasExpansionAnchor = true;
            continue;
        }
        if (!hasTextMutation && hasExpansionAnchor &&
            command.kind == CommandKind::Run &&
            command.name == L"TableAppendRow") {
            hasAppendRow = true;
            continue;
        }
        if (hasExpansionAnchor && !hasAppendRow) {
            return TableTextBatchKind::None;
        }
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
        return TableTextBatchKind::None;
    }
    if (!hasTextMutation) {
        return TableTextBatchKind::None;
    }
    return hasAppendRow ? TableTextBatchKind::Expand : TableTextBatchKind::Fill;
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
    const TableTextBatchKind batchKind = ClassifyTableTextBatch(request);
    reusableTableTextFillRequest =
        batchKind == TableTextBatchKind::Fill ? &request : nullptr;
    if (batchKind == TableTextBatchKind::None) {
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
        if (command.kind == CommandKind::Cell ||
            command.kind == CommandKind::Run) {
            continue;
        }
        if (batchKind == TableTextBatchKind::Expand &&
            command.kind == CommandKind::SetCellText &&
            !command.hasExpectedText) {
            // TableAppendRow creates these cells later. Existing cells with
            // expected text are still checked before any row is appended.
            continue;
        }
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

bool PositionBefore(const Position& left, const Position& right) noexcept {
    if (left.list != right.list) {
        return left.list < right.list;
    }
    if (left.paragraph != right.paragraph) {
        return left.paragraph < right.paragraph;
    }
    return left.character < right.character;
}

bool SelectionsOverlap(const Selection& left, const Selection& right) noexcept {
    return left.start.list == right.start.list &&
        PositionBefore(left.start, right.end) &&
        PositionBefore(right.start, left.end);
}

bool VerifyPreflightSelection(
    Context* const context,
    const Command& command,
    const ReadbackPolicy policy,
    const std::wstring& location) {
    if (!command.hasExpectedText) {
        return true;
    }
    std::wstring selected;
    return ReadSelectedText(context, &selected, location) &&
        (MatchesExpectedReadback(
             selected,
             command.first,
             command.matchCase,
             policy) ||
         MatchesExpectedReadback(
             selected,
             command.first,
             ReadAutomaticNumber(context),
             command.matchCase,
             policy) ||
         SetError(
             context->result,
             L"STALE_SELECTION_TEXT",
             location,
             L"selected text changed before text.patch preflight"));
}

bool ResolveTextPatchTarget(
    Context* const context,
    const Command& command,
    const Selection& original,
    Selection* const resolved) {
    ReadbackPolicy policy = ReadbackPolicy::Exact;
    std::wstring location = L"current";
    if (command.name == L"CURRENT") {
        Selection current;
        if (!GetSelection(context->hwp, &current, context->result)) {
            return false;
        }
        if (current.selected) {
            *resolved = current;
        } else if ((current.mode & kSelectionModeMask) != kSelectionNone) {
            return SetError(
                context->result,
                L"NON_TEXT_SELECTION",
                location,
                L"text.patch cannot use a non-text selection");
        } else if (command.hasExpectedText && !command.first.empty()) {
            return SetError(
                context->result,
                L"NO_SELECTION",
                location,
                L"expected old text requires a current selection");
        } else {
            Position cursor;
            if (!GetPosition(context->hwp, &cursor, context->result)) {
                return false;
            }
            *resolved = Selection{false, kSelectionNone, cursor, cursor};
            return true;
        }
    } else if (command.name == L"RANGE") {
        location = L"range";
        const Position start{command.list, command.paragraph, command.character};
        const Position end{
            command.endList,
            command.endParagraph,
            command.endCharacter};
        if (!SelectTextRange(context, start, end, location)) {
            return false;
        }
    } else if (command.name == L"FIND") {
        if (command.occurrence < 0 || command.occurrence > 20'000) {
            return SetError(
                context->result,
                L"TEXT_OCCURRENCE",
                L"text.find",
                L"text occurrence must be between 1 and 20000 when supplied");
        }
        LONG listFilter = -1;
        location = L"text.find";
        if (!command.tableInstanceId.empty()) {
            if (command.cellAddress.empty() ||
                !SelectTableForCellPatch(context, command.tableInstanceId) ||
                !GoToCell(context, command.cellAddress)) {
                return false;
            }
            Position cell;
            if (!GetPosition(context->hwp, &cell, context->result)) {
                return false;
            }
            listFilter = cell.list;
            location = NormalizeAddress(command.cellAddress);
        }
        policy = ReadbackPolicy::Find;
        if (!SelectTextPatchMatch(context, command, original, listFilter)) {
            return false;
        }
    } else if (command.name == L"CELL") {
        if (command.occurrence < 0 || command.occurrence > 20'000) {
            return SetError(
                context->result,
                L"TEXT_OCCURRENCE",
                command.cellAddress,
                L"text occurrence must be between 1 and 20000 when supplied");
        }
        location = NormalizeAddress(command.cellAddress);
        if (!SelectTableForCellPatch(context, command.tableInstanceId) ||
            !GoToCell(context, command.cellAddress) ||
            !SelectCellTextPatchMatch(context, command, original, location)) {
            return false;
        }
    } else {
        return SetError(
            context->result,
            L"TEXT_PATCH_TARGET",
            command.name,
            L"unsupported text.patch preflight target");
    }
    if (!GetSelection(context->hwp, resolved, context->result) ||
        !resolved->selected) {
        return SetError(
            context->result,
            L"TEXT_FIND_STATE",
            location,
            L"text.patch preflight did not resolve one text selection");
    }
    return VerifyPreflightSelection(context, command, policy, location);
}

ExecutionResult PreflightTextPatchTargets(
    IDispatch* const hwp,
    const Request& request) noexcept {
    const auto started = std::chrono::steady_clock::now();
    ExecutionResult result;
    Context context;
    context.hwp = hwp;
    context.request = &request;
    context.result = &result;
    const auto finish = [&]() {
        result.elapsedMicroseconds = std::chrono::duration_cast<
            std::chrono::microseconds>(
                std::chrono::steady_clock::now() - started).count();
        result.retrySafe = !result.partialMutation;
        return result;
    };
    try {
        if (hwp == nullptr ||
            !ValidateCommandOrder(request, &result) ||
            !ValidateDocumentIdentity(hwp, request, &result) ||
            !GetDispatchProperty(hwp, L"HAction", context.action, &result)) {
            return finish();
        }
        Position cursor;
        Selection original;
        if (!GetPosition(hwp, &cursor, &result) ||
            !GetSelection(
                hwp,
                &original,
                &result,
                SelectionCapturePolicy::RequiredControl) ||
            !hancom::com_state::CanRestoreSelection(original)) {
            SetError(
                &result,
                L"UNSUPPORTED_SELECTION",
                L"text.patch.preflight",
                L"the active selection cannot be restored after preflight");
            return finish();
        }
        std::vector<Selection> targets;
        size_t requestIndex = 0;
        for (size_t commandIndex = 0;
             commandIndex < request.commands.size();
             ++commandIndex) {
            const Command& command = request.commands[commandIndex];
            if (command.kind != CommandKind::TextPatch) {
                continue;
            }
            result.failedRequestIndex = requestIndex;
            Selection target;
            bool resolved = ResolveTextPatchTarget(
                &context,
                command,
                original,
                &target);
            PreparedTextPatchTarget captured;
            if (resolved) {
                captured.startList = target.start.list;
                captured.startParagraph = target.start.paragraph;
                captured.startCharacter = target.start.character;
                captured.endList = target.end.list;
                captured.endParagraph = target.end.paragraph;
                captured.endCharacter = target.end.character;
                resolved = ReadSelectedText(
                    &context,
                    &captured.text,
                    L"text.patch.preflight.inverse") &&
                    CapturePreparedTextFormat(
                        &context,
                        request,
                        commandIndex,
                        &captured,
                        L"text.patch.preflight.inverse");
            }
            const Error resolutionError = result.error;
            const bool restored = hancom::com_state::RestoreSelection(
                hwp,
                cursor,
                original);
            context.table = nullptr;
            context.tableId.clear();
            context.currentCell.clear();
            context.topology.Clear();
            if (!restored) {
                result.retrySafe = false;
                SetError(
                    &result,
                    L"STATE_RESTORE",
                    L"text.patch.preflight",
                    L"cursor or selection restoration failed after preflight");
                return finish();
            }
            if (!resolved) {
                result.error = resolutionError;
                return finish();
            }
            if (std::any_of(
                    targets.begin(),
                    targets.end(),
                    [&target](const Selection& prior) {
                        return SelectionsOverlap(prior, target);
                    })) {
                SetError(
                    &result,
                    L"OVERLAPPING_TEXT_TARGET",
                    L"text.patch.preflight",
                    L"text.patch targets overlap in the prepared document state");
                return finish();
            }
            targets.push_back(target);
            result.preparedTextPatchTargets.push_back(std::move(captured));
            ++requestIndex;
        }
        result.preflightTargetCount = targets.size();
        result.succeeded = true;
    } catch (...) {
        SetError(
            &result,
            L"NATIVE_EXCEPTION",
            L"text.patch.preflight",
            L"text.patch preflight failed unexpectedly");
    }
    return finish();
}

}

namespace hancom::actions {

ExecutionResult PreflightTextPatches(
    IDispatch* const hwp,
    const Request& request) noexcept {
    return detail::PreflightTextPatchTargets(hwp, request);
}

}

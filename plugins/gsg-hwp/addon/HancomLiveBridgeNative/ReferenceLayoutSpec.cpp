#include "ReferenceLayoutSpec.h"
#include "ReferenceLayoutGapSpec.h"
#include "ReferenceLayoutPayloadArrays.h"
#include "ReferenceLayoutSpecSetters.h"

#include <utility>

namespace hancom::reference_layout {
namespace {

bool Fail(actions::Error* const error, const std::wstring& message) {
    error->code = L"REFERENCE_LAYOUT_PAYLOAD";
    error->location = L"ReferenceLayoutBulk";
    error->message = message;
    return false;
}

}

bool Parse(
    const actions::Command& command,
    Spec* const spec,
    actions::Error* const error) noexcept {
    try {
        if (spec == nullptr || error == nullptr ||
            (command.name != L"ReferenceLayoutBulk" &&
             command.name != L"ReferenceLayoutPatch")) {
            return false;
        }
        Spec parsed;
        parsed.patch = command.name == L"ReferenceLayoutPatch";
        if (!detail::IntegerSetter(command, L"Rows", &parsed.rows, error) ||
            !detail::IntegerSetter(command, L"Columns", &parsed.columns, error) ||
            !detail::IntegerSetter(command, L"BaseStyleId", &parsed.baseStyleId, error) ||
            !detail::IntegerSetter(command, L"BodyLeft", &parsed.bodyLeft, error) ||
            !detail::IntegerSetter(command, L"BodyTop", &parsed.bodyTop, error) ||
            !detail::IntegerSetter(command, L"BodyWidth", &parsed.bodyWidth, error) ||
            !detail::IntegerSetter(command, L"BodyHeight", &parsed.bodyHeight, error)) {
            return false;
        }
        detail::PayloadArrays arrays;
        if (!arrays.Load(command, error) ||
            !arrays.Integers(L"ColumnWidths", &parsed.columnWidths, error) ||
            !arrays.Integers(L"RowHeights", &parsed.rowHeights, error)) {
            return false;
        }
        if (parsed.patch &&
            (!detail::TextSetter(command, L"TargetControlId", &parsed.targetControlId, error) ||
             !arrays.OptionalIntegers(
                 L"PatchColumnIndexes", &parsed.patchColumns, error) ||
             !arrays.OptionalIntegers(
                 L"PatchRowIndexes", &parsed.patchRows, error))) {
            return false;
        }

        std::vector<LONG> mergeRows, mergeColumns, mergeRowSpans, mergeColumnSpans;
        if (!arrays.OptionalIntegers(L"MergeRows", &mergeRows, error) ||
            !arrays.OptionalIntegers(L"MergeColumns", &mergeColumns, error) ||
            !arrays.OptionalIntegers(L"MergeRowSpans", &mergeRowSpans, error) ||
            !arrays.OptionalIntegers(
                L"MergeColumnSpans", &mergeColumnSpans, error) ||
            !detail::SameSize(mergeRows.size(), {
                mergeColumns.size(), mergeRowSpans.size(), mergeColumnSpans.size()}, error)) {
            return false;
        }
        for (size_t index = 0; index < mergeRows.size(); ++index) {
            parsed.merges.push_back(Merge{
                mergeRows[index], mergeColumns[index],
                mergeRowSpans[index], mergeColumnSpans[index]});
        }

        std::vector<LONG> edgeOrientations, edgeLines, edgeStarts, edgeEnds, edgeColors;
        std::vector<std::wstring> edgeStyles, edgeWidths;
        if (!arrays.OptionalIntegers(
                L"EdgeOrientations", &edgeOrientations, error) ||
            !arrays.OptionalIntegers(L"EdgeLines", &edgeLines, error) ||
            !arrays.OptionalIntegers(L"EdgeStarts", &edgeStarts, error) ||
            !arrays.OptionalIntegers(L"EdgeEnds", &edgeEnds, error) ||
            !arrays.OptionalIntegers(L"EdgeColors", &edgeColors, error) ||
            !arrays.OptionalTexts(L"EdgeStyles", &edgeStyles, error) ||
            !arrays.OptionalTexts(L"EdgeWidths", &edgeWidths, error) ||
            !detail::SameSize(edgeLines.size(), {
                edgeOrientations.size(), edgeStarts.size(), edgeEnds.size(),
                edgeColors.size(), edgeStyles.size(), edgeWidths.size()}, error)) {
            return false;
        }
        for (size_t index = 0; index < edgeLines.size(); ++index) {
            parsed.edges.push_back(Edge{
                edgeOrientations[index], edgeLines[index], edgeStarts[index],
                edgeEnds[index], edgeStyles[index], edgeWidths[index], edgeColors[index]});
        }

        std::vector<std::wstring> styleKeys, fontNames;
        std::vector<LONG> fontSizes, bold, textColors, fillColors, alignments;
        std::vector<LONG> vertical, widthRatios, letterSpacings;
        std::vector<LONG> lineSpacingTypes, lineSpacings, previousSpacings, nextSpacings;
        std::vector<LONG> padLeft, padRight, padTop, padBottom;
        if (!arrays.OptionalTexts(L"StyleKeys", &styleKeys, error) ||
            !arrays.OptionalTexts(L"StyleFontNames", &fontNames, error) ||
            !arrays.OptionalIntegers(L"StyleFontSizes", &fontSizes, error) ||
            !arrays.OptionalIntegers(L"StyleBold", &bold, error) ||
            !arrays.OptionalIntegers(
                L"StyleTextColors", &textColors, error) ||
            !arrays.OptionalIntegers(
                L"StyleFillColors", &fillColors, error) ||
            !arrays.OptionalIntegers(
                L"StyleAlignments", &alignments, error) ||
            !arrays.OptionalIntegers(
                L"StyleVerticalAlignments", &vertical, error) ||
            !arrays.OptionalIntegers(
                L"StyleWidthRatios", &widthRatios, error) ||
            !arrays.OptionalIntegers(
                L"StyleLetterSpacings", &letterSpacings, error) ||
            !arrays.OptionalIntegers(
                L"StyleLineSpacingTypes", &lineSpacingTypes, error) ||
            !arrays.OptionalIntegers(
                L"StyleLineSpacings", &lineSpacings, error) ||
            !arrays.OptionalIntegers(
                L"StylePreviousSpacings", &previousSpacings, error) ||
            !arrays.OptionalIntegers(
                L"StyleNextSpacings", &nextSpacings, error) ||
            !arrays.OptionalIntegers(L"StylePaddingLeft", &padLeft, error) ||
            !arrays.OptionalIntegers(L"StylePaddingRight", &padRight, error) ||
            !arrays.OptionalIntegers(L"StylePaddingTop", &padTop, error) ||
            !arrays.OptionalIntegers(
                L"StylePaddingBottom", &padBottom, error)) {
            return false;
        }
        detail::DefaultMissing(&widthRatios, styleKeys.size(), -1);
        detail::DefaultMissing(&letterSpacings, styleKeys.size(), -1'000);
        detail::DefaultMissing(&lineSpacingTypes, styleKeys.size(), -1);
        detail::DefaultMissing(&previousSpacings, styleKeys.size(), -1);
        detail::DefaultMissing(&nextSpacings, styleKeys.size(), -1);
        if (!detail::SameSize(styleKeys.size(), {
                fontNames.size(), fontSizes.size(), bold.size(), textColors.size(),
                fillColors.size(), alignments.size(), vertical.size(),
                widthRatios.size(), letterSpacings.size(), lineSpacingTypes.size(),
                lineSpacings.size(), previousSpacings.size(), nextSpacings.size(),
                padLeft.size(), padRight.size(), padTop.size(), padBottom.size()}, error)) {
            return false;
        }
        for (size_t index = 0; index < styleKeys.size(); ++index) {
            parsed.styles.push_back(Style{
                styleKeys[index], fontNames[index], fontSizes[index], bold[index],
                textColors[index], fillColors[index], alignments[index], vertical[index],
                widthRatios[index], letterSpacings[index], lineSpacingTypes[index],
                lineSpacings[index], previousSpacings[index], nextSpacings[index],
                padLeft[index], padRight[index], padTop[index], padBottom[index]});
        }

        std::vector<LONG> regionTop, regionLeft, regionBottom, regionRight, regionStyles;
        if (!arrays.OptionalIntegers(L"RegionTop", &regionTop, error) ||
            !arrays.OptionalIntegers(L"RegionLeft", &regionLeft, error) ||
            !arrays.OptionalIntegers(L"RegionBottom", &regionBottom, error) ||
            !arrays.OptionalIntegers(L"RegionRight", &regionRight, error) ||
            !arrays.OptionalIntegers(
                L"RegionStyleIndexes", &regionStyles, error) ||
            !detail::SameSize(regionTop.size(), {
                regionLeft.size(), regionBottom.size(), regionRight.size(), regionStyles.size()}, error)) {
            return false;
        }
        for (size_t index = 0; index < regionTop.size(); ++index) {
            parsed.regions.push_back(Region{
                regionTop[index], regionLeft[index], regionBottom[index],
                regionRight[index], regionStyles[index]});
        }

        std::vector<LONG> textRows, textColumns, textStyles, textBreakModes;
        std::vector<std::wstring> textValues;
        if (!arrays.OptionalIntegers(L"TextRows", &textRows, error) ||
            !arrays.OptionalIntegers(L"TextColumns", &textColumns, error) ||
            !arrays.OptionalIntegers(
                L"TextStyleIndexes", &textStyles, error) ||
            !arrays.OptionalIntegers(
                L"TextBreakModes", &textBreakModes, error) ||
            !arrays.OptionalTexts(L"TextValues", &textValues, error)) {
            return false;
        }
        detail::DefaultMissing(&textBreakModes, textRows.size(), 0);
        if (!detail::SameSize(textRows.size(), {
                textColumns.size(), textStyles.size(), textBreakModes.size(),
                textValues.size()}, error)) {
            return false;
        }
        for (size_t index = 0; index < textRows.size(); ++index) {
            parsed.texts.push_back(Text{
                textRows[index], textColumns[index], textStyles[index],
                textBreakModes[index], textValues[index]});
        }
        if (!ParseProtectedGaps(arrays, &parsed, error)) {
            return false;
        }
        if (!Validate(parsed, error)) {
            return false;
        }
        *spec = std::move(parsed);
        return true;
    } catch (...) {
        return Fail(error, L"reference-layout payload parsing failed unexpectedly");
    }
}

}

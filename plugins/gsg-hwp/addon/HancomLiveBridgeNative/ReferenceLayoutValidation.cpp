#include "ReferenceLayoutSpec.h"
#include "ReferenceLayoutGapSpec.h"

#include <algorithm>
#include <numeric>
#include <set>
#include <utility>

namespace hancom::reference_layout {
namespace {

bool Fail(actions::Error* const error, const std::wstring& message) {
    error->code = L"REFERENCE_LAYOUT_INVALID";
    error->location = L"ReferenceLayoutBulk";
    error->message = message;
    return false;
}

bool StyleIndex(const LONG value, const size_t count) {
    return value == -1 || (value >= 0 && static_cast<size_t>(value) < count);
}

}

bool Validate(const Spec& spec, actions::Error* const error) noexcept {
    try {
        if (spec.rows < 1 || spec.rows > 50 || spec.columns < 1 || spec.columns > 50 ||
            spec.columnWidths.size() != static_cast<size_t>(spec.columns) ||
            spec.rowHeights.size() != static_cast<size_t>(spec.rows)) {
            return Fail(error, L"reference-layout grid dimensions are invalid");
        }
        if (spec.bodyLeft < 0 || spec.bodyTop < 0 ||
            spec.bodyWidth <= 0 || spec.bodyHeight <= 0) {
            return Fail(error, L"reference-layout body rectangle is invalid");
        }
        if (std::any_of(spec.columnWidths.begin(), spec.columnWidths.end(),
                [](const LONG value) { return value <= 0; }) ||
            std::any_of(spec.rowHeights.begin(), spec.rowHeights.end(),
                [](const LONG value) { return value <= 0; }) ||
            std::accumulate(spec.columnWidths.begin(), spec.columnWidths.end(), 0LL) !=
                spec.bodyWidth ||
            std::accumulate(spec.rowHeights.begin(), spec.rowHeights.end(), 0LL) !=
                spec.bodyHeight) {
            return Fail(error, L"reference-layout geometry does not exactly fill the body");
        }
        if (spec.patch) {
            if (spec.targetControlId.empty() ||
                (spec.patchColumns.empty() && spec.patchRows.empty() &&
                 spec.regions.empty() && spec.edges.empty() && spec.texts.empty()) ||
                spec.patchColumns.size() > static_cast<size_t>(spec.columns) ||
                spec.patchRows.size() > static_cast<size_t>(spec.rows)) {
                return Fail(error, L"reference-layout patch target or change set is invalid");
            }
            const std::set<LONG> columns(spec.patchColumns.begin(), spec.patchColumns.end());
            const std::set<LONG> rows(spec.patchRows.begin(), spec.patchRows.end());
            if (columns.size() != spec.patchColumns.size() ||
                rows.size() != spec.patchRows.size() ||
                std::any_of(columns.begin(), columns.end(), [&](const LONG value) {
                    return value < 0 || value >= spec.columns;
                }) ||
                std::any_of(rows.begin(), rows.end(), [&](const LONG value) {
                    return value < 0 || value >= spec.rows;
                })) {
                return Fail(error, L"reference-layout patch indexes are invalid");
            }
        } else if (!spec.targetControlId.empty() ||
                   !spec.patchColumns.empty() ||
                   !spec.patchRows.empty()) {
            return Fail(error, L"reference-layout creation cannot contain patch state");
        }

        std::set<std::wstring> styleKeys;
        for (const Style& style : spec.styles) {
            if (style.key.empty() || !styleKeys.emplace(style.key).second ||
                style.fontSize < -1 || style.bold < -1 || style.bold > 1 ||
                style.alignment < -1 || style.alignment > 3 ||
                style.verticalAlignment < -1 || style.verticalAlignment > 2 ||
                (style.widthRatio != -1 &&
                 (style.widthRatio < 50 || style.widthRatio > 200)) ||
                (style.letterSpacing != -1'000 &&
                 (style.letterSpacing < -50 || style.letterSpacing > 50)) ||
                style.lineSpacingType < -1 || style.lineSpacingType > 2 ||
                style.lineSpacing < -1 || style.previousSpacing < -1 ||
                style.nextSpacing < -1 || style.paddingLeft < -1 ||
                style.paddingRight < -1 || style.paddingTop < -1 ||
                style.paddingBottom < -1) {
                return Fail(error, L"reference-layout style is invalid or duplicated");
            }
        }

        std::set<std::pair<LONG, LONG>> occupied;
        std::set<std::pair<LONG, LONG>> covered;
        for (const Merge& merge : spec.merges) {
            if (merge.row < 0 || merge.column < 0 ||
                merge.rowSpan < 1 || merge.columnSpan < 1 ||
                (merge.rowSpan == 1 && merge.columnSpan == 1) ||
                merge.row + merge.rowSpan > spec.rows ||
                merge.column + merge.columnSpan > spec.columns) {
                return Fail(error, L"reference-layout merge is outside the grid");
            }
            for (LONG row = merge.row; row < merge.row + merge.rowSpan; ++row) {
                for (LONG column = merge.column;
                     column < merge.column + merge.columnSpan; ++column) {
                    const std::pair<LONG, LONG> cell(row, column);
                    if (!occupied.emplace(cell).second) {
                        return Fail(error, L"reference-layout merges overlap");
                    }
                    if (row != merge.row || column != merge.column) {
                        covered.emplace(cell);
                    }
                }
            }
        }

        for (const Region& region : spec.regions) {
            if (region.top < 0 || region.left < 0 ||
                region.top >= region.bottom || region.left >= region.right ||
                region.bottom > spec.rows || region.right > spec.columns ||
                !StyleIndex(region.styleIndex, spec.styles.size()) ||
                region.styleIndex < 0) {
                return Fail(error, L"reference-layout style region is invalid");
            }
        }
        for (const Text& text : spec.texts) {
            if (text.row < 0 || text.row >= spec.rows ||
                text.column < 0 || text.column >= spec.columns ||
                text.value.empty() || !StyleIndex(text.styleIndex, spec.styles.size()) ||
                text.breakMode < 0 || text.breakMode > 1 ||
                covered.find(std::pair(text.row, text.column)) != covered.end()) {
                return Fail(error, L"reference-layout text anchor is invalid");
            }
        }
        for (const Edge& edge : spec.edges) {
            const LONG intervalLimit = edge.orientation == 0 ? spec.columns : spec.rows;
            const LONG lineLimit = edge.orientation == 0 ? spec.rows : spec.columns;
            if ((edge.orientation != 0 && edge.orientation != 1) ||
                edge.line < 0 || edge.line > lineLimit ||
                edge.start < 0 || edge.start >= edge.end || edge.end > intervalLimit ||
                edge.style.empty() || edge.width.empty()) {
                return Fail(error, L"reference-layout visible edge is invalid");
            }
            for (const Merge& merge : spec.merges) {
                const bool crosses = edge.orientation == 1
                    ? merge.column < edge.line &&
                        edge.line < merge.column + merge.columnSpan &&
                        edge.start < merge.row + merge.rowSpan && merge.row < edge.end
                    : merge.row < edge.line &&
                        edge.line < merge.row + merge.rowSpan &&
                        edge.start < merge.column + merge.columnSpan && merge.column < edge.end;
                if (crosses) {
                    return Fail(error, L"reference-layout visible edge crosses a merge");
                }
            }
        }
        return ValidateProtectedGaps(spec, error);
    } catch (...) {
        return Fail(error, L"reference-layout validation failed unexpectedly");
    }
}

}

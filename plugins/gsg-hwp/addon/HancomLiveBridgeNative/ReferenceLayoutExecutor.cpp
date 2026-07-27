#include "ReferenceLayoutExecutor.h"

#include "ReferenceLayoutCommands.h"

#include <algorithm>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace hancom::reference_layout {
namespace {

bool ApplyRegionStyle(
    Host* const host,
    const Spec& spec,
    const Region& region) {
    if (!host->SelectRegion(region.top, region.left, region.bottom, region.right)) {
        return false;
    }
    const Style& style = spec.styles[static_cast<size_t>(region.styleIndex)];
    const bool singleCell =
        region.bottom - region.top == 1 && region.right - region.left == 1;
    for (const actions::Command& command : StyleCommands(style, singleCell)) {
        if (!host->ExecuteParameter(command)) {
            return false;
        }
    }
    if (style.verticalAlignment >= 0) {
        const wchar_t* const action = style.verticalAlignment == 0
            ? L"TableVAlignTop"
            : style.verticalAlignment == 1
            ? L"TableVAlignCenter"
            : L"TableVAlignBottom";
        if (!host->Run(action, style.key)) {
            return false;
        }
    }
    return host->Run(L"Cancel", style.key);
}

bool ApplyVisibleEdge(
    Host* const host,
    const Spec& spec,
    const Edge& edge) {
    LONG row = 0;
    LONG column = 0;
    const wchar_t* side = nullptr;
    if (edge.orientation == 0) {
        row = edge.line == spec.rows ? spec.rows - 1 : edge.line;
        side = edge.line == spec.rows ? L"Bottom" : L"Top";
    } else {
        column = edge.line == spec.columns ? spec.columns - 1 : edge.line;
        side = edge.line == spec.columns ? L"Right" : L"Left";
    }
    std::set<std::pair<LONG, LONG>> selectedOwners;
    for (LONG segment = edge.start; segment < edge.end; ++segment) {
        const LONG targetRow = edge.orientation == 0 ? row : segment;
        const LONG targetColumn = edge.orientation == 0 ? segment : column;
        std::pair<LONG, LONG> owner{targetRow, targetColumn};
        for (const Merge& merge : spec.merges) {
            if (merge.row <= targetRow &&
                targetRow < merge.row + merge.rowSpan &&
                merge.column <= targetColumn &&
                targetColumn < merge.column + merge.columnSpan) {
                owner = {merge.row, merge.column};
                break;
            }
        }
        if (!selectedOwners.insert(owner).second) {
            continue;
        }
        if (!host->SelectRegion(
                targetRow,
                targetColumn,
                targetRow + 1,
                targetColumn + 1) ||
            !host->ExecuteParameter(VisibleEdgeCommand(edge, side)) ||
            !host->Run(L"Cancel", L"visible edge")) {
            return false;
        }
    }
    return true;
}

bool ApplyTextStyle(Host* const host, const Style& style) {
    for (const actions::Command& command : StyleCommands(style, false)) {
        if ((command.name == L"CharShape" || command.name == L"ParagraphShape") &&
            !host->ExecuteParameter(command)) {
            return false;
        }
    }
    return true;
}

bool IntersectsPatchedAxis(
    const LONG start,
    const LONG span,
    const std::vector<LONG>& patched) {
    return std::any_of(
        patched.begin(),
        patched.end(),
        [&](const LONG value) { return start <= value && value < start + span; });
}

std::vector<LONG> ExpandPatchedAxis(
    const std::vector<LONG>& patched,
    const std::vector<Merge>& merges,
    const bool column) {
    std::vector<LONG> expanded = patched;
    bool changed = false;
    do {
        changed = false;
        for (const Merge& merge : merges) {
            const LONG start = column ? merge.column : merge.row;
            const LONG span = column ? merge.columnSpan : merge.rowSpan;
            if (span <= 1 || !IntersectsPatchedAxis(start, span, expanded)) {
                continue;
            }
            for (LONG value = start; value < start + span; ++value) {
                if (std::find(expanded.begin(), expanded.end(), value) ==
                    expanded.end()) {
                    expanded.push_back(value);
                    changed = true;
                }
            }
        }
    } while (changed);
    std::sort(expanded.begin(), expanded.end());
    return expanded;
}

struct AffectedMerge {
    Merge merge;
    bool splitColumns = false;
    bool splitRows = false;
};

std::vector<AffectedMerge> AffectedMerges(
    const Spec& spec,
    const std::vector<LONG>& patchColumns,
    const std::vector<LONG>& patchRows) {
    std::vector<AffectedMerge> affected;
    for (const Merge& merge : spec.merges) {
        const bool splitColumns =
            merge.columnSpan > 1 &&
            IntersectsPatchedAxis(
                merge.column,
                merge.columnSpan,
                patchColumns);
        const bool splitRows =
            merge.rowSpan > 1 &&
            IntersectsPatchedAxis(
                merge.row,
                merge.rowSpan,
                patchRows);
        if (splitColumns || splitRows) {
            affected.push_back(AffectedMerge{
                merge,
                splitColumns,
                splitRows,
            });
        }
    }
    std::sort(
        affected.begin(),
        affected.end(),
        [](const AffectedMerge& left, const AffectedMerge& right) {
            return std::pair(left.merge.row, left.merge.column) >
                std::pair(right.merge.row, right.merge.column);
        });
    return affected;
}

bool ExecutePatch(Host* const host, const Spec& spec) {
    if (!host->CaptureExistingTable(spec.targetControlId)) {
        return false;
    }
    const std::vector<LONG> patchColumns =
        ExpandPatchedAxis(spec.patchColumns, spec.merges, true);
    const std::vector<LONG> patchRows =
        ExpandPatchedAxis(spec.patchRows, spec.merges, false);
    const std::vector<AffectedMerge> affectedMerges =
        AffectedMerges(spec, patchColumns, patchRows);
    for (const AffectedMerge& affected : affectedMerges) {
        if (!host->GoToCell(affected.merge.row, affected.merge.column) ||
            !host->ExecuteParameter(SplitMergedCellCommand(
                affected.merge,
                affected.splitColumns,
                affected.splitRows))) {
            return false;
        }
    }
    if (!affectedMerges.empty() &&
        !host->CaptureExistingTable(spec.targetControlId)) {
        return false;
    }
    if ((!patchColumns.empty() || !patchRows.empty()) &&
        !host->ExecuteParameter(UnlockTableSizeCommand())) {
        return false;
    }
    for (const LONG column : patchColumns) {
        if (!host->ResizeColumn(
                column,
                spec.columnWidths[static_cast<size_t>(column)])) {
            return false;
        }
    }
    for (const LONG row : patchRows) {
        if (!host->ResizeRow(
                row,
                spec.rowHeights[static_cast<size_t>(row)])) {
            return false;
        }
    }
    for (const AffectedMerge& affected : affectedMerges) {
        if (!host->MergeCells(affected.merge)) {
            return false;
        }
    }
    for (const Region& region : spec.regions) {
        if (!ApplyRegionStyle(host, spec, region)) {
            return false;
        }
    }
    for (const Edge& edge : spec.edges) {
        if (!ApplyVisibleEdge(host, spec, edge)) {
            return false;
        }
    }
    for (const Text& text : spec.texts) {
        const Style* const style = text.styleIndex >= 0
            ? &spec.styles[static_cast<size_t>(text.styleIndex)]
            : nullptr;
        if (!host->GoToCell(text.row, text.column) ||
            !host->Run(L"SelectAll", L"reference text patch") ||
            (style != nullptr && !ApplyTextStyle(host, *style)) ||
            !host->InsertText(text, style)) {
            return false;
        }
    }
    Spec verifiedSpec = spec;
    verifiedSpec.patchColumns = patchColumns;
    verifiedSpec.patchRows = patchRows;
    return host->VerifyPatchedTopology(verifiedSpec) && host->LeaveTable(false);
}

}

bool Execute(const actions::Command& command, Host* const host) noexcept {
    try {
        if (host == nullptr) {
            return false;
        }
        Spec spec;
        actions::Error error;
        if (!Parse(command, &spec, &error)) {
            return host->Fail(error);
        }
        if (spec.patch) {
            return ExecutePatch(host, spec);
        }
        if (!host->ExecuteParameter(BaseStyleCommand(spec.baseStyleId)) ||
            !host->ExecuteParameter(AnchorParagraphCommand()) ||
            !host->ExecuteParameter(TableSeedCharacterCommand()) ||
            !host->ExecuteParameter(TableCreateCommand(spec)) ||
            !host->CaptureCreatedTable()) {
            return false;
        }
        for (const Region& region : spec.regions) {
            if (!ApplyRegionStyle(host, spec, region)) {
                return false;
            }
        }
        if (!host->SelectRegion(0, 0, spec.rows, spec.columns) ||
            !host->ExecuteParameter(ClearAllBordersCommand()) ||
            !host->Run(L"Cancel", L"clear all borders")) {
            return false;
        }
        std::vector<Merge> merges = spec.merges;
        std::sort(merges.begin(), merges.end(), [](const Merge& left, const Merge& right) {
            return std::pair(left.row, left.column) > std::pair(right.row, right.column);
        });
        for (const Merge& merge : merges) {
            if (!host->MergeCells(merge)) {
                return false;
            }
        }
        for (const Text& text : spec.texts) {
            const Style* const style = text.styleIndex >= 0
                ? &spec.styles[static_cast<size_t>(text.styleIndex)]
                : nullptr;
            if (!host->GoToCell(text.row, text.column)) {
                return false;
            }
            if (style != nullptr && !ApplyTextStyle(host, *style)) {
                return false;
            }
            if (!host->InsertText(text, style)) {
                return false;
            }
        }
        for (const Edge& edge : spec.edges) {
            if (!ApplyVisibleEdge(host, spec, edge)) {
                return false;
            }
        }
        if (!host->ExecuteParameter(UnlockTableSizeCommand()) ||
            !host->ReconcileFinalGeometry(spec)) {
            return false;
        }
        if (!host->VerifyFinalTopology(spec)) {
            return false;
        }
        return host->LeaveTable(false);
    } catch (...) {
        actions::Error error;
        error.code = L"REFERENCE_LAYOUT_EXECUTION";
        error.location = L"ReferenceLayoutBulk";
        error.message = L"reference-layout execution failed unexpectedly";
        return host != nullptr && host->Fail(error);
    }
}

}

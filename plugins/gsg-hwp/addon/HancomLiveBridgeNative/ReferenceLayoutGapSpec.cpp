#include "ReferenceLayoutGapSpec.h"

#include <numeric>
#include <set>
#include <tuple>

namespace hancom::reference_layout {
namespace {

bool Fail(
    actions::Error* const error,
    const wchar_t* const message) {
    error->code = L"REFERENCE_LAYOUT_INVALID";
    error->location = L"ReferenceLayoutBulk";
    error->message = message;
    return false;
}

bool Overlaps(
    const LONG top,
    const LONG left,
    const LONG bottom,
    const LONG right,
    const ProtectedGap& gap) {
    return top < gap.bottom && gap.top < bottom &&
        left < gap.right && gap.left < right;
}

bool CellInside(
    const LONG row,
    const LONG column,
    const ProtectedGap& gap) {
    return gap.top <= row && row < gap.bottom &&
        gap.left <= column && column < gap.right;
}

bool EdgeCrosses(
    const Edge& edge,
    const ProtectedGap& gap) {
    if (edge.orientation == 0) {
        return gap.top < edge.line && edge.line < gap.bottom &&
            edge.start < gap.right && gap.left < edge.end;
    }
    return gap.left < edge.line && edge.line < gap.right &&
        edge.start < gap.bottom && gap.top < edge.end;
}

}

bool ParseProtectedGaps(
    const detail::PayloadArrays& arrays,
    Spec* const spec,
    actions::Error* const error) {
    std::vector<LONG> axes;
    std::vector<LONG> top;
    std::vector<LONG> left;
    std::vector<LONG> bottom;
    std::vector<LONG> right;
    std::vector<LONG> minimums;
    if (!arrays.OptionalIntegers(L"GapAxes", &axes, error) ||
        !arrays.OptionalIntegers(L"GapTop", &top, error) ||
        !arrays.OptionalIntegers(L"GapLeft", &left, error) ||
        !arrays.OptionalIntegers(L"GapBottom", &bottom, error) ||
        !arrays.OptionalIntegers(L"GapRight", &right, error) ||
        !arrays.OptionalIntegers(L"GapMinimums", &minimums, error) ||
        !detail::SameSize(
            axes.size(),
            {top.size(), left.size(), bottom.size(), right.size(), minimums.size()},
            error)) {
        return false;
    }
    for (size_t index = 0; index < axes.size(); ++index) {
        spec->protectedGaps.push_back(ProtectedGap{
            axes[index],
            top[index],
            left[index],
            bottom[index],
            right[index],
            minimums[index],
        });
    }
    return true;
}

bool ValidateProtectedGaps(
    const Spec& spec,
    actions::Error* const error) {
    std::set<std::tuple<LONG, LONG, LONG, LONG, LONG>> unique;
    for (const ProtectedGap& gap : spec.protectedGaps) {
        if ((gap.axis != 0 && gap.axis != 1) ||
            gap.top < 0 || gap.left < 0 ||
            gap.top >= gap.bottom || gap.left >= gap.right ||
            gap.bottom > spec.rows || gap.right > spec.columns ||
            gap.minimum <= 0 ||
            !unique.emplace(
                gap.axis,
                gap.top,
                gap.left,
                gap.bottom,
                gap.right).second) {
            return Fail(error, L"reference-layout protected gap is invalid");
        }
        const LONG measured = gap.axis == 0
            ? std::accumulate(
                spec.rowHeights.begin() + gap.top,
                spec.rowHeights.begin() + gap.bottom,
                0L)
            : std::accumulate(
                spec.columnWidths.begin() + gap.left,
                spec.columnWidths.begin() + gap.right,
                0L);
        if (measured < gap.minimum) {
            return Fail(
                error,
                L"reference-layout protected gap is below its minimum size");
        }
        for (const Merge& merge : spec.merges) {
            if (Overlaps(
                    merge.row,
                    merge.column,
                    merge.row + merge.rowSpan,
                    merge.column + merge.columnSpan,
                    gap)) {
                return Fail(
                    error,
                    L"reference-layout merge intersects a protected gap");
            }
        }
        for (const Text& text : spec.texts) {
            if (CellInside(text.row, text.column, gap)) {
                return Fail(
                    error,
                    L"reference-layout text intersects a protected gap");
            }
        }
        for (const Region& region : spec.regions) {
            if (spec.styles[static_cast<size_t>(region.styleIndex)].fillColor >= 0 &&
                Overlaps(
                    region.top,
                    region.left,
                    region.bottom,
                    region.right,
                    gap)) {
                return Fail(
                    error,
                    L"reference-layout fill intersects a protected gap");
            }
        }
        for (const Edge& edge : spec.edges) {
            if (EdgeCrosses(edge, gap)) {
                return Fail(
                    error,
                    L"reference-layout edge crosses a protected gap");
            }
        }
    }
    return true;
}

}

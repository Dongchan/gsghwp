#include "ActionExecutorInternal.h"

#include "ReferenceLayoutCommands.h"
#include "ReferenceLayoutExecutor.h"

#include <algorithm>
#include <map>
#include <string>
#include <vector>

namespace hancom::actions::detail {

std::wstring ReferenceCellAddress(const LONG row, LONG column) {
    std::wstring letters;
    do {
        letters.insert(letters.begin(), static_cast<wchar_t>(L'A' + column % 26));
        column = column / 26 - 1;
    } while (column >= 0);
    return letters + std::to_wstring(row + 1);
}

bool RunReferenceAction(
    Context* const context,
    const wchar_t* const action,
    const std::wstring& location) {
    if (!RunAction(context->action, action, context->result, location)) {
        return false;
    }
    ++context->result->actionsExecuted;
    return true;
}

bool SelectReferenceRegion(
    Context* const context,
    const LONG top,
    const LONG left,
    const LONG bottom,
    const LONG right) {
    if (context->topology.Empty() && !BuildCellTopology(context)) {
        return false;
    }
    const hancom::inspection::CellTopologyCell* const firstOwner =
        context->topology.OwnerAt(top + 1, left + 1);
    const hancom::inspection::CellTopologyCell* const lastOwner =
        context->topology.OwnerAt(bottom, right);
    if (firstOwner == nullptr || lastOwner == nullptr) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_REGION",
            ReferenceCellAddress(top, left) + L":" +
                ReferenceCellAddress(bottom - 1, right - 1),
            L"selected region is not covered by the inspected table topology");
    }
    const std::wstring first = firstOwner->address;
    const std::wstring last = lastOwner->address;
    if (!GoToCell(context, first) ||
        !RunReferenceAction(context, L"TableCellBlock", first) ||
        !RunReferenceAction(context, L"TableCellBlockExtend", first)) {
        return false;
    }
    if (first == last) {
        return true;
    }
    std::vector<hancom::inspection::CellTopologyStep> path;
    std::vector<std::wstring> region;
    std::wstring error;
    if (!context->topology.PlanRectangularMerge(first, last, &path, &region, &error)) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_REGION",
            first + L":" + last,
            error.empty() ? L"style region is not a complete cell rectangle" : error);
    }
    for (const hancom::inspection::CellTopologyStep& step : path) {
        const wchar_t* const action =
            step.direction == hancom::inspection::CellDirection::Right
            ? L"TableRightCell"
            : L"TableLowerCell";
        if (!RunReferenceAction(context, action, step.destination) ||
            GetCellAddress(context->hwp) != step.destination) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_REGION",
                step.destination,
                L"selected region did not follow the inspected cell topology");
        }
    }
    context->currentCell = first;
    return true;
}

bool InsertReferenceText(
    Context* const context,
    const hancom::reference_layout::Text& text,
    const hancom::reference_layout::Style* const expectedStyle) {
    const std::wstring location =
        ReferenceCellAddress(text.row, text.column);
    Position start;
    if (!GetPosition(context->hwp, &start, context->result)) {
        return false;
    }
    size_t offset = 0;
    size_t line = 0;
    while (offset <= text.value.size()) {
        const size_t newline = text.value.find(L'\n', offset);
        const size_t end = newline == std::wstring::npos ? text.value.size() : newline;
        std::wstring value = text.value.substr(offset, end - offset);
        if (!value.empty() && value.back() == L'\r') {
            value.pop_back();
        }
        if (line > 0 &&
            !RunReferenceAction(
                context,
                hancom::reference_layout::TextBreakAction(text.breakMode),
                L"reference text")) {
            return false;
        }
        if (!value.empty() && !InsertText(context, value, L"reference text")) {
            return false;
        }
        ++line;
        if (newline == std::wstring::npos) {
            break;
        }
        offset = newline + 1;
    }
    Position end;
    if (!GetPosition(context->hwp, &end, context->result) ||
        !SelectTextRange(context, start, end, location)) {
        return false;
    }
    std::wstring actual;
    if (!ReadSelectedText(context, &actual, location)) {
        return false;
    }
    const auto normalizeLineBreaks = [](const std::wstring& value) {
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
    };
    if (normalizeLineBreaks(actual) != normalizeLineBreaks(text.value)) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_TEXT_READBACK",
            location,
            L"inserted reference text does not match the requested value");
    }
    if (expectedStyle == nullptr) {
        return true;
    }
    PreservedTextFormat format;
    if (!CaptureTextFormat(context, &format, location)) {
        return false;
    }
    return
        MatchesExpectedReferenceTextFormat(format.fingerprint, *expectedStyle) ||
        SetError(
            context->result,
            L"REFERENCE_LAYOUT_TEXT_FORMAT_READBACK",
            location,
            L"inserted reference text format does not match the requested style");
}

struct ExpectedReferenceBorder {
    LONG type = 0;
    LONG width = 0;
    LONG color = 0;
};

const hancom::inspection::CellTopologyCell* ReferenceOwnerAt(
    const Context* const context,
    const LONG row,
    const LONG column) noexcept {
    return context->topology.OwnerAt(row + 1, column + 1);
}

bool ConvertExpectedReferenceBorder(
    Context* const context,
    const hancom::reference_layout::Edge& edge,
    const std::wstring& side,
    ExpectedReferenceBorder* const expected) {
    const Command command =
        hancom::reference_layout::VisibleEdgeCommand(edge, side.c_str());
    const std::wstring typePath =
        L"SelCellsBorderFill/BorderType" + side;
    const std::wstring widthPath =
        L"SelCellsBorderFill/BorderWidth" + side;
    const auto convert = [&](const std::wstring& path, LONG* const value) {
        const auto found = std::find_if(
            command.setters.begin(),
            command.setters.end(),
            [&](const Setter& setter) { return setter.path == path; });
        if (found == command.setters.end()) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_VERIFY",
                path,
                L"requested edge has no comparable border field");
        }
        CComVariant converted;
        if (!ConvertValue(
                context->hwp,
                found->value,
                &converted,
                context->result,
                path)) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_VERIFY",
                path,
                L"requested edge border value could not be converted");
        }
        const HRESULT status = AsLong(converted, value);
        return SUCCEEDED(status) ||
            SetError(
                context->result,
                L"REFERENCE_LAYOUT_VERIFY",
                path,
                FormatHResult(L"convert requested edge border", status));
    };
    if (!convert(typePath, &expected->type) ||
        !convert(widthPath, &expected->width)) {
        return false;
    }
    expected->color = edge.color;
    return true;
}

HRESULT ReadReferenceBorderValue(
    IDispatch* const set,
    const std::wstring& name,
    LONG* const value) {
    CComVariant raw;
    HRESULT status = PropertyGet(set, name.c_str(), &raw);
    if (FAILED(status)) {
        status = Method(
            set,
            L"Item",
            {CComVariant(name.c_str())},
            &raw);
    }
    if (SUCCEEDED(status)) {
        status = AsLong(raw, value);
    }
    return status;
}

bool VerifyReferenceBorders(
    Context* const context,
    const hancom::reference_layout::Spec& spec) {
    using SideExpectations =
        std::vector<std::pair<std::wstring, ExpectedReferenceBorder>>;
    std::map<std::wstring, SideExpectations> expectedByCell;
    size_t expectedSideCount = 0;
    for (const hancom::reference_layout::Edge& edge : spec.edges) {
        const bool horizontal = edge.orientation == 0;
        const LONG fixed = horizontal
            ? (edge.line == spec.rows ? spec.rows - 1 : edge.line)
            : (edge.line == spec.columns ? spec.columns - 1 : edge.line);
        const std::wstring side = horizontal
            ? (edge.line == spec.rows ? L"Bottom" : L"Top")
            : (edge.line == spec.columns ? L"Right" : L"Left");
        ExpectedReferenceBorder expected;
        if (!ConvertExpectedReferenceBorder(
                context,
                edge,
                side,
                &expected)) {
            return false;
        }
        for (LONG segment = edge.start; segment < edge.end; ++segment) {
            const LONG row = horizontal ? fixed : segment;
            const LONG column = horizontal ? segment : fixed;
            const hancom::inspection::CellTopologyCell* const owner =
                ReferenceOwnerAt(context, row, column);
            if (owner == nullptr) {
                return SetError(
                    context->result,
                    L"REFERENCE_LAYOUT_VERIFY",
                    ReferenceCellAddress(row, column) + L":" + side,
                    L"requested edge is not covered by the final table topology");
            }
            SideExpectations& sides = expectedByCell[owner->address];
            const auto existing = std::find_if(
                sides.begin(),
                sides.end(),
                [&](const auto& item) { return item.first == side; });
            if (existing == sides.end()) {
                sides.emplace_back(side, expected);
                ++expectedSideCount;
            } else {
                existing->second = expected;
            }
        }
    }
    if (expectedSideCount == 0) {
        return true;
    }

    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> parameter;
    CComPtr<IDispatch> set;
    CComVariant raw;
    HRESULT status = PropertyGet(context->hwp, L"HParameterSet", &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, parameterSets);
    }
    if (SUCCEEDED(status)) {
        raw.Clear();
        status = PropertyGet(parameterSets, L"HCellBorderFill", &raw);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, parameter);
    }
    if (SUCCEEDED(status)) {
        raw.Clear();
        status = PropertyGet(parameter, L"HSet", &raw);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, set);
    }
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_VERIFY",
            context->tableId,
            FormatHResult(L"prepare CellBorderFill readback", status));
    }

    size_t verifiedSideCount = 0;
    for (const auto& [address, sides] : expectedByCell) {
        const auto failReadback = [&](
            const std::wstring& location,
            const std::wstring& message) {
            static_cast<void>(
                RunReferenceAction(context, L"Cancel", address));
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_VERIFY",
                location,
                message);
        };
        if (!GoToCell(context, address) ||
            !RunReferenceAction(
                context,
                L"TableCellBlock",
                address)) {
            return failReadback(
                address,
                L"could not select the cell for edge readback");
        }
        CComVariant loaded;
        status = Method(
            context->action,
            L"GetDefault",
            {CComVariant(L"CellBorderFill"), CComVariant(set)},
            &loaded);
        bool loadedBorder = false;
        if (SUCCEEDED(status)) {
            status = AsBool(loaded, &loadedBorder);
        }
        if (FAILED(status) || !loadedBorder) {
            return failReadback(
                address,
                FAILED(status)
                    ? FormatHResult(L"read CellBorderFill", status)
                    : L"CellBorderFill readback returned false");
        }
        CComPtr<IDispatch> selectedBorders;
        HRESULT selectedBordersStatus = E_UNEXPECTED;
        bool selectedBordersResolved = false;
        const auto resolveSelectedBorders = [&]() {
            if (selectedBordersResolved) {
                return selectedBordersStatus;
            }
            selectedBordersResolved = true;
            CComVariant selected;
            selectedBordersStatus =
                PropertyGet(parameter, L"SelCellsBorderFill", &selected);
            if (FAILED(selectedBordersStatus)) {
                selectedBordersStatus =
                    PropertyGet(set, L"SelCellsBorderFill", &selected);
            }
            if (SUCCEEDED(selectedBordersStatus)) {
                selectedBordersStatus =
                    AsDispatch(selected, selectedBorders);
            }
            return selectedBordersStatus;
        };
        for (const auto& [side, expected] : sides) {
            const std::wstring location = address + L":" + side;
            const std::wstring typeName = L"BorderType" + side;
            const std::wstring widthName = L"BorderWidth" + side;
            const std::wstring colorName = side == L"Left"
                ? L"BorderCorlorLeft"
                : L"BorderColor" + side;
            for (const auto& [name, expectedValue] :
                 std::vector<std::pair<std::wstring, LONG>>{
                     {typeName, expected.type},
                     {widthName, expected.width},
                     {colorName, expected.color}}) {
                LONG directValue = 0;
                const HRESULT directStatus =
                    ReadReferenceBorderValue(set, name, &directValue);
                if (SUCCEEDED(directStatus) &&
                    directValue == expectedValue) {
                    continue;
                }
                LONG selectedValue = 0;
                HRESULT selectedStatus = resolveSelectedBorders();
                if (SUCCEEDED(selectedStatus)) {
                    selectedStatus = ReadReferenceBorderValue(
                        selectedBorders,
                        name,
                        &selectedValue);
                }
                if (SUCCEEDED(selectedStatus) &&
                    selectedValue == expectedValue) {
                    continue;
                }
                if (FAILED(directStatus) && FAILED(selectedStatus)) {
                    return failReadback(
                        location,
                        FormatHResult(name.c_str(), directStatus) +
                            L"; SelCellsBorderFill fallback " +
                            FormatHResult(name.c_str(), selectedStatus));
                }
                std::wstring actual = SUCCEEDED(directStatus)
                    ? std::to_wstring(directValue)
                    : L"unreadable";
                if (SUCCEEDED(selectedStatus)) {
                    actual += L", selected=" +
                        std::to_wstring(selectedValue);
                }
                return failReadback(
                    location,
                    name + L" does not match the requested edge "
                        L"(expected " + std::to_wstring(expectedValue) +
                        L", actual " + actual + L")");
            }
            ++verifiedSideCount;
        }
        if (!RunReferenceAction(context, L"Cancel", address)) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_VERIFY",
                address,
                L"could not clear the edge readback cell selection");
        }
    }
    return verifiedSideCount == expectedSideCount ||
        SetError(
            context->result,
            L"REFERENCE_LAYOUT_VERIFY",
            context->tableId,
            L"verified unique final cell-side count does not match the request");
}

bool VerifyReferenceTopology(
    Context* const context,
    const hancom::reference_layout::Spec& spec) {
    context->topology.Clear();
    if (!BuildCellTopology(context) ||
        context->topology.Rows() != spec.rows ||
        context->topology.Columns() != spec.columns) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_VERIFY",
            context->tableId,
            L"final table dimensions do not match the compressed layout");
    }
    size_t expectedCells = static_cast<size_t>(spec.rows * spec.columns);
    for (const hancom::reference_layout::Merge& merge : spec.merges) {
        expectedCells -= static_cast<size_t>(
            merge.rowSpan * merge.columnSpan - 1);
        const std::wstring address = ReferenceCellAddress(merge.row, merge.column);
        const hancom::inspection::CellTopologyCell* const cell =
            context->topology.Find(address);
        if (cell == nullptr ||
            cell->rowSpan != merge.rowSpan ||
            cell->columnSpan != merge.columnSpan) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_VERIFY",
                address,
                L"final merged-cell topology does not match the request");
        }
    }
    if (context->topology.Cells().size() != expectedCells) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_VERIFY",
            context->tableId,
            L"final table has an unexpected physical cell count");
    }
    for (const hancom::inspection::CellTopologyCell& cell : context->topology.Cells()) {
        if (cell.row < 1 || cell.column < 1 ||
            cell.row + cell.rowSpan - 1 > spec.rows ||
            cell.column + cell.columnSpan - 1 > spec.columns) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_VERIFY",
                cell.address,
                L"final cell lies outside the requested grid");
        }
        LONG expectedWidth = 0;
        for (LONG column = cell.column - 1;
             column < cell.column - 1 + cell.columnSpan; ++column) {
            expectedWidth += spec.columnWidths[static_cast<size_t>(column)];
        }
        LONG expectedHeight = 0;
        for (LONG row = cell.row - 1;
             row < cell.row - 1 + cell.rowSpan; ++row) {
            expectedHeight += spec.rowHeights[static_cast<size_t>(row)];
        }
        if ((cell.width >= 0 && cell.width != expectedWidth) ||
            (cell.height >= 0 && cell.height != expectedHeight)) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_VERIFY",
                cell.address,
                L"final cell geometry does not match the breakpoint mapping");
        }
    }
    return VerifyReferenceBorders(context, spec);
}

const hancom::inspection::CellTopologyCell* ReferenceAxisAnchor(
    const Context* const context,
    const bool column,
    const LONG index) {
    for (const hancom::inspection::CellTopologyCell& cell :
         context->topology.Cells()) {
        const LONG cellIndex = column ? cell.column - 1 : cell.row - 1;
        const LONG span = column ? cell.columnSpan : cell.rowSpan;
        if (cellIndex == index && span == 1) {
            return &cell;
        }
    }
    return nullptr;
}

bool ResizeReferenceAxis(
    Context* const context,
    const bool column,
    const LONG index,
    const LONG size) {
    if ((context->topology.Empty() && !BuildCellTopology(context))) {
        return false;
    }
    const hancom::inspection::CellTopologyCell* const anchor =
        ReferenceAxisAnchor(context, column, index);
    const std::wstring location =
        std::wstring(column ? L"column " : L"row ") + std::to_wstring(index);
    if (anchor == nullptr) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_PATCH_GEOMETRY",
            location,
            L"no single-span cell can resize the requested grid interval");
    }
    if (!GoToCell(context, anchor->address) ||
        !RunReferenceAction(
            context,
            column ? L"TableCellBlockCol" : L"TableCellBlockRow",
            location) ||
        !ExecuteParameterAction(
            context,
            hancom::reference_layout::CellSizeCommand(column, size),
            false)) {
        return false;
    }
    return RunReferenceAction(context, L"Cancel", location);
}

bool ReconcileReferenceGeometry(
    Context* const context,
    const hancom::reference_layout::Spec& spec) {
    const std::wstring tableId = context->tableId;
    if (tableId.empty() ||
        !LeaveTable(context, false) ||
        !SelectControl(context, tableId) ||
        !CaptureCurrentTable(context)) {
        return false;
    }
    context->topology.Clear();
    if (!BuildCellTopology(context) ||
        context->topology.Rows() != spec.rows ||
        context->topology.Columns() != spec.columns) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_RECONCILE",
            context->tableId,
            L"final table dimensions cannot be reconciled to the compressed layout");
    }
    std::vector<LONG> columns;
    std::vector<LONG> rows;
    for (LONG column = 0; column < spec.columns; ++column) {
        const hancom::inspection::CellTopologyCell* const anchor =
            ReferenceAxisAnchor(context, true, column);
        if (anchor == nullptr || anchor->width < 0) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_RECONCILE",
                L"column " + std::to_wstring(column),
                L"no measurable single-span cell covers the requested column");
        }
        if (anchor->width != spec.columnWidths[static_cast<size_t>(column)]) {
            columns.push_back(column);
        }
    }
    for (LONG row = 0; row < spec.rows; ++row) {
        const hancom::inspection::CellTopologyCell* const anchor =
            ReferenceAxisAnchor(context, false, row);
        if (anchor == nullptr || anchor->height < 0) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_RECONCILE",
                L"row " + std::to_wstring(row),
                L"no measurable single-span cell covers the requested row");
        }
        if (anchor->height != spec.rowHeights[static_cast<size_t>(row)]) {
            rows.push_back(row);
        }
    }
    for (const LONG column : columns) {
        if (!ResizeReferenceAxis(
                context,
                true,
                column,
                spec.columnWidths[static_cast<size_t>(column)])) {
            return false;
        }
    }
    for (const LONG row : rows) {
        if (!ResizeReferenceAxis(
                context,
                false,
                row,
                spec.rowHeights[static_cast<size_t>(row)])) {
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

bool VerifyPatchedReferenceTopology(
    Context* const context,
    const hancom::reference_layout::Spec& spec,
    const hancom::inspection::CellTopology& originalTopology) {
    context->topology.Clear();
    if (!BuildCellTopology(context) ||
        context->topology.Rows() != spec.rows ||
        context->topology.Columns() != spec.columns) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_PATCH_VERIFY",
            context->tableId,
            L"patched table dimensions do not match the compressed layout");
    }
    if (!originalTopology.HasSamePhysicalShape(context->topology)) {
        return SetError(
            context->result,
            L"REFERENCE_LAYOUT_PATCH_VERIFY",
            context->tableId,
            L"patch changed the existing physical cell topology");
    }
    for (const hancom::reference_layout::Merge& merge : spec.merges) {
        const hancom::inspection::CellTopologyCell* const cell =
            context->topology.Find(
                ReferenceCellAddress(merge.row, merge.column));
        if (cell == nullptr ||
            cell->rowSpan != merge.rowSpan ||
            cell->columnSpan != merge.columnSpan) {
            return SetError(
                context->result,
                L"REFERENCE_LAYOUT_PATCH_VERIFY",
                context->tableId,
                L"patch changed the expected merged-cell topology");
        }
    }
    for (const hancom::inspection::CellTopologyCell& cell :
         context->topology.Cells()) {
        if (IntersectsPatchedAxis(
                cell.column - 1,
                cell.columnSpan,
                spec.patchColumns)) {
            LONG expected = 0;
            for (LONG column = cell.column - 1;
                 column < cell.column - 1 + cell.columnSpan;
                 ++column) {
                expected += spec.columnWidths[static_cast<size_t>(column)];
            }
            if (cell.width >= 0 && cell.width != expected) {
                return SetError(
                    context->result,
                    L"REFERENCE_LAYOUT_PATCH_VERIFY",
                    cell.address,
                    L"patched column geometry does not match the requested boundary "
                    L"(expected " + std::to_wstring(expected) +
                    L", actual " + std::to_wstring(cell.width) + L")");
            }
        }
        if (IntersectsPatchedAxis(
                cell.row - 1,
                cell.rowSpan,
                spec.patchRows)) {
            LONG expected = 0;
            for (LONG row = cell.row - 1;
                 row < cell.row - 1 + cell.rowSpan;
                 ++row) {
                expected += spec.rowHeights[static_cast<size_t>(row)];
            }
            if (cell.height >= 0 && cell.height != expected) {
                return SetError(
                    context->result,
                    L"REFERENCE_LAYOUT_PATCH_VERIFY",
                    cell.address,
                    L"patched row geometry does not match the requested boundary "
                    L"(expected " + std::to_wstring(expected) +
                    L", actual " + std::to_wstring(cell.height) + L")");
            }
        }
    }
    return true;
}

class ReferenceLayoutHost final : public hancom::reference_layout::Host {
public:
    explicit ReferenceLayoutHost(Context* const context) : context_(context) {}

    bool Fail(const Error& error) override {
        return SetError(
            context_->result,
            error.code,
            error.location,
            error.message);
    }

    bool ExecuteParameter(const Command& command) override {
        return ExecuteParameterAction(context_, command, false);
    }

    bool CaptureCreatedTable() override {
        if (!CaptureCurrentTable(context_) || !SetTableTreatAsCharacter(context_)) {
            return false;
        }
        context_->result->createdControlIds.push_back(context_->tableId);
        return true;
    }

    bool CaptureExistingTable(const std::wstring& controlId) override {
        if (!SelectControl(context_, controlId) || !CaptureCurrentTable(context_)) {
            return false;
        }
        if (context_->tableId != controlId) {
            return SetError(
                context_->result,
                L"WRONG_CONTROL",
                controlId,
                L"captured table identity does not match the patch target");
        }
        if (!BuildCellTopology(context_)) {
            return false;
        }
        if (originalTopology_.Empty()) {
            originalTopology_ = context_->topology;
        }
        return true;
    }

    bool ResizeColumn(const LONG column, const LONG width) override {
        return ResizeReferenceAxis(context_, true, column, width);
    }

    bool ResizeRow(const LONG row, const LONG height) override {
        return ResizeReferenceAxis(context_, false, row, height);
    }

    bool SelectRegion(
        const LONG top,
        const LONG left,
        const LONG bottom,
        const LONG right) override {
        return SelectReferenceRegion(context_, top, left, bottom, right);
    }

    bool GoToCell(const LONG row, const LONG column) override {
        return ::hancom::actions::detail::GoToCell(
            context_,
            ReferenceCellAddress(row, column));
    }

    bool Run(
        const wchar_t* const action,
        const std::wstring& location) override {
        return RunReferenceAction(context_, action, location);
    }

    bool InsertText(
        const hancom::reference_layout::Text& text,
        const hancom::reference_layout::Style* const expectedStyle) override {
        return InsertReferenceText(context_, text, expectedStyle);
    }

    bool MergeCells(const hancom::reference_layout::Merge& merge) override {
        if (context_->topology.Empty() && !BuildCellTopology(context_)) {
            return false;
        }
        const hancom::inspection::CellTopologyCell* const firstOwner =
            context_->topology.OwnerAt(merge.row + 1, merge.column + 1);
        const hancom::inspection::CellTopologyCell* const lastOwner =
            context_->topology.OwnerAt(
                merge.row + merge.rowSpan,
                merge.column + merge.columnSpan);
        if (firstOwner == nullptr || lastOwner == nullptr) {
            return SetError(
                context_->result,
                L"REFERENCE_LAYOUT_MERGE",
                ReferenceCellAddress(merge.row, merge.column),
                L"merge corner is not covered by the inspected table topology");
        }
        const std::wstring first = firstOwner->address;
        const std::wstring last = lastOwner->address;
        return MergeCellsUsingTopology(
            context_,
            first,
            last,
            true);
    }

    bool ReconcileFinalGeometry(
        const hancom::reference_layout::Spec& spec) override {
        return ReconcileReferenceGeometry(context_, spec);
    }

    bool VerifyFinalTopology(
        const hancom::reference_layout::Spec& spec) override {
        return VerifyReferenceTopology(context_, spec);
    }

    bool VerifyPatchedTopology(
        const hancom::reference_layout::Spec& spec) override {
        return VerifyPatchedReferenceTopology(context_, spec, originalTopology_);
    }

    bool LeaveTable(const bool appendParagraph = true) override {
        return ::hancom::actions::detail::LeaveTable(context_, appendParagraph);
    }

private:
    Context* context_;
    hancom::inspection::CellTopology originalTopology_;
};

bool ExecuteReferenceLayout(Context* const context, const Command& command) {
    ReferenceLayoutHost host(context);
    return hancom::reference_layout::Execute(command, &host);
}

}

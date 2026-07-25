#include "../ReferenceLayoutExecutor.h"
#include "../ReferenceLayoutCommands.h"

#include <algorithm>
#include <string>
#include <utility>
#include <vector>

namespace {

hancom::actions::Value IntegerValue(const LONG value) {
    hancom::actions::Value result;
    result.kind = hancom::actions::ValueKind::Integer;
    result.integer = value;
    return result;
}

hancom::actions::Value TextValue(const wchar_t* const value) {
    hancom::actions::Value result;
    result.kind = hancom::actions::ValueKind::Text;
    result.text = value;
    return result;
}

void AddIntegerArray(
    hancom::actions::Command* const command,
    const wchar_t* const name,
    const std::vector<LONG>& values) {
    command->arrays.emplace_back(name, static_cast<LONG>(values.size()));
    for (size_t index = 0; index < values.size(); ++index) {
        command->arrayValues.push_back({
            name, static_cast<LONG>(index), IntegerValue(values[index])});
    }
}

void AddTextArray(
    hancom::actions::Command* const command,
    const wchar_t* const name,
    const std::vector<const wchar_t*>& values) {
    command->arrays.emplace_back(name, static_cast<LONG>(values.size()));
    for (size_t index = 0; index < values.size(); ++index) {
        command->arrayValues.push_back({
            name, static_cast<LONG>(index), TextValue(values[index])});
    }
}

hancom::actions::Command BulkCommand() {
    hancom::actions::Command command;
    command.kind = hancom::actions::CommandKind::Action;
    command.name = L"ReferenceLayoutBulk";
    command.parameterSet = L"HTableCreation";
    for (const auto& [path, value] : std::vector<std::pair<const wchar_t*, LONG>>{
             {L"Rows", 1}, {L"Columns", 2}, {L"BaseStyleId", 0},
             {L"BodyLeft", 10}, {L"BodyTop", 20},
             {L"BodyWidth", 100}, {L"BodyHeight", 40}}) {
        command.setters.push_back({path, IntegerValue(value)});
    }
    AddIntegerArray(&command, L"ColumnWidths", {60, 40});
    AddIntegerArray(&command, L"RowHeights", {40});
    AddIntegerArray(&command, L"MergeRows", {0});
    AddIntegerArray(&command, L"MergeColumns", {0});
    AddIntegerArray(&command, L"MergeRowSpans", {1});
    AddIntegerArray(&command, L"MergeColumnSpans", {2});
    AddIntegerArray(&command, L"EdgeOrientations", {0});
    AddIntegerArray(&command, L"EdgeLines", {0});
    AddIntegerArray(&command, L"EdgeStarts", {0});
    AddIntegerArray(&command, L"EdgeEnds", {2});
    AddIntegerArray(&command, L"EdgeColors", {0});
    AddTextArray(&command, L"EdgeStyles", {L"solid"});
    AddTextArray(&command, L"EdgeWidths", {L"0.12mm"});
    AddTextArray(&command, L"StyleKeys", {L"title"});
    AddTextArray(&command, L"StyleFontNames", {L"Arial"});
    for (const wchar_t* const name : {
             L"StyleFontSizes", L"StyleBold", L"StyleTextColors",
             L"StyleFillColors", L"StyleAlignments",
             L"StyleVerticalAlignments", L"StyleLineSpacings",
             L"StylePaddingLeft", L"StylePaddingRight",
             L"StylePaddingTop", L"StylePaddingBottom"}) {
        LONG value = std::wstring(name) == L"StyleFontSizes" ? 900L : -1L;
        if (std::wstring(name) == L"StyleLineSpacings") value = 1200;
        if (std::wstring(name) == L"StylePaddingLeft") value = 10;
        if (std::wstring(name) == L"StylePaddingRight") value = 20;
        if (std::wstring(name) == L"StylePaddingTop") value = 30;
        if (std::wstring(name) == L"StylePaddingBottom") value = 40;
        AddIntegerArray(
            &command,
            name,
            {value});
    }
    AddIntegerArray(&command, L"StyleWidthRatios", {93});
    AddIntegerArray(&command, L"StyleLetterSpacings", {-2});
    AddIntegerArray(&command, L"StyleLineSpacingTypes", {1});
    AddIntegerArray(&command, L"StylePreviousSpacings", {28});
    AddIntegerArray(&command, L"StyleNextSpacings", {57});
    AddIntegerArray(&command, L"RegionTop", {0});
    AddIntegerArray(&command, L"RegionLeft", {0});
    AddIntegerArray(&command, L"RegionBottom", {1});
    AddIntegerArray(&command, L"RegionRight", {2});
    AddIntegerArray(&command, L"RegionStyleIndexes", {0});
    AddIntegerArray(&command, L"TextRows", {0});
    AddIntegerArray(&command, L"TextColumns", {0});
    AddIntegerArray(&command, L"TextStyleIndexes", {0});
    AddIntegerArray(&command, L"TextBreakModes", {0});
    AddTextArray(&command, L"TextValues", {L"Title"});
    return command;
}

class RecordingHost final : public hancom::reference_layout::Host {
public:
    bool Fail(const hancom::actions::Error&) override {
        failed = true;
        return false;
    }

    bool ExecuteParameter(const hancom::actions::Command& command) override {
        parameterNames.push_back(command.name);
        if (command.name == L"CellBorderFill" &&
            HasSetter(command, L"HSet/TypeVert") &&
            HasSetter(command, L"HSet/TypeHorz") &&
            HasSetter(command, L"HSet/BorderTypeLeft") &&
            HasSetter(command, L"HSet/BorderTypeRight") &&
            HasSetter(command, L"HSet/BorderTypeTop") &&
            HasSetter(command, L"HSet/BorderTypeBottom")) {
            bordersClearedBeforeMerge =
                merges == 0 && regions == 2 && texts == 0 &&
                reconciled == 0;
        }
        if (command.name == L"CellZoneBorder" &&
            IntegerSetterEquals(command, L"ApplyTo", 2) &&
            IntegerSetterEquals(command, L"NoNeighborCell", 0) &&
            HasSetterPrefix(command, L"SelCellsBorderFill/BorderWidth")) {
            visibleEdgeAppliedAfterClear =
                bordersClearedBeforeMerge && merges == 1;
        }
        if (command.name == L"TablePropertyDialog" &&
            BooleanSetterEquals(command, L"ProtectSize", false)) {
            tableSizeUnlockedBeforeReconcile =
                visibleEdgeAppliedAfterClear && reconciled == 0;
        }
        if (command.name == L"CharShape" &&
            IntegerSetterEquals(command, L"Height", 100)) {
            tableSeedCharacterConfigured = true;
        }
        if (command.name == L"CharShape" &&
            IntegerSetterEquals(command, L"Height", 900) &&
            IntegerSetterEquals(command, L"RatioHangul", 93) &&
            IntegerSetterEquals(command, L"SpacingHangul", -2)) {
            explicitCharacterMetrics = true;
        }
        if (command.name == L"ParagraphShape" &&
            IntegerSetterEquals(command, L"LineSpacingType", 1) &&
            IntegerSetterEquals(command, L"LineSpacing", 1200) &&
            IntegerSetterEquals(command, L"PrevSpacing", 28) &&
            IntegerSetterEquals(command, L"NextSpacing", 57)) {
            explicitParagraphMetrics = true;
        }
        if (command.name == L"TablePropertyDialog" &&
            IntegerSetterEquals(command, L"ShapeTableCell/MarginLeft", 10) &&
            IntegerSetterEquals(command, L"ShapeTableCell/MarginRight", 20) &&
            IntegerSetterEquals(command, L"ShapeTableCell/MarginTop", 30) &&
            IntegerSetterEquals(command, L"ShapeTableCell/MarginBottom", 40)) {
            explicitCellPadding = true;
        }
        if (command.name == L"ParagraphShape" &&
            IntegerSetterEquals(command, L"LineSpacing", 100) &&
            IntegerSetterEquals(command, L"LineSpacingType", 0) &&
            std::any_of(
                command.setters.begin(),
                command.setters.end(),
                [](const hancom::actions::Setter& setter) {
                    return setter.path == L"AlignType" &&
                        setter.value.kind == hancom::actions::ValueKind::Enumeration &&
                        setter.value.text == L"Center";
                })) {
            anchorParagraphConfigured = true;
        }
        if (command.name == L"TableCreate") {
            tableCreateGeometryConfigured =
                IntegerSetterEquals(command, L"HeightType", 1) &&
                BooleanSetterEquals(
                    command, L"TableProperties/TreatAsChar", true) &&
                BooleanSetterEquals(
                    command, L"TableProperties/ProtectSize", false) &&
                IntegerSetterEquals(command, L"TableProperties/Width", 100) &&
                IntegerSetterEquals(command, L"TableProperties/Height", 40) &&
                IntegerSetterEquals(command, L"TableProperties/CellMarginLeft", 0) &&
                IntegerSetterEquals(command, L"TableProperties/CellMarginRight", 0) &&
                IntegerSetterEquals(command, L"TableProperties/CellMarginTop", 0) &&
                IntegerSetterEquals(command, L"TableProperties/CellMarginBottom", 0);
        }
        return true;
    }

    bool CaptureCreatedTable() override {
        ++captured;
        return true;
    }

    bool CaptureExistingTable(const std::wstring&) override {
        return false;
    }

    bool ResizeColumn(LONG, LONG) override {
        return false;
    }

    bool ResizeRow(LONG, LONG) override {
        return false;
    }

    bool SelectRegion(
        const LONG top,
        const LONG startColumn,
        const LONG bottom,
        const LONG endColumn) override {
        lastTop = top;
        lastLeft = startColumn;
        lastBottom = bottom;
        lastRight = endColumn;
        ++regions;
        return true;
    }

    bool GoToCell(LONG, LONG) override {
        ++cells;
        return true;
    }

    bool Run(const wchar_t*, const std::wstring&) override {
        ++runs;
        return true;
    }

    bool InsertText(const hancom::reference_layout::Text&) override {
        textInsertedAfterMerge = merges == 1;
        ++texts;
        return true;
    }

    bool MergeCells(const hancom::reference_layout::Merge&) override {
        mergeAppliedAfterBorderClear =
            bordersClearedBeforeMerge && !visibleEdgeAppliedAfterClear;
        ++merges;
        return true;
    }

    bool ReconcileFinalGeometry(
        const hancom::reference_layout::Spec&) override {
        geometryReconciledAfterMerges =
            mergeAppliedAfterBorderClear && visibleEdgeAppliedAfterClear &&
            tableSizeUnlockedBeforeReconcile && merges == 1;
        ++reconciled;
        return true;
    }

    bool VerifyFinalTopology(const hancom::reference_layout::Spec&) override {
        verifiedAfterReconciliation = reconciled == 1;
        ++verified;
        return true;
    }

    bool VerifyPatchedTopology(const hancom::reference_layout::Spec&) override {
        return false;
    }

    bool LeaveTable(const bool appendParagraph = true) override {
        appendedParagraph = appendParagraph;
        ++left;
        return true;
    }

    bool failed = false;
    bool anchorParagraphConfigured = false;
    bool tableSeedCharacterConfigured = false;
    bool tableCreateGeometryConfigured = false;
    bool explicitCharacterMetrics = false;
    bool explicitParagraphMetrics = false;
    bool explicitCellPadding = false;
    bool bordersClearedBeforeMerge = false;
    bool visibleEdgeAppliedAfterClear = false;
    bool tableSizeUnlockedBeforeReconcile = false;
    bool mergeAppliedAfterBorderClear = false;
    bool textInsertedAfterMerge = false;
    bool geometryReconciledAfterMerges = false;
    bool verifiedAfterReconciliation = false;
    LONG lastTop = -1;
    LONG lastLeft = -1;
    LONG lastBottom = -1;
    LONG lastRight = -1;
    int captured = 0;
    int regions = 0;
    int cells = 0;
    int runs = 0;
    int texts = 0;
    int merges = 0;
    int reconciled = 0;
    int verified = 0;
    int left = 0;
    bool appendedParagraph = false;
    std::vector<std::wstring> parameterNames;

private:
    static bool HasSetter(
        const hancom::actions::Command& command,
        const wchar_t* const path) {
        return std::any_of(
            command.setters.begin(),
            command.setters.end(),
            [&](const hancom::actions::Setter& setter) {
                return setter.path == path;
            });
    }

    static bool HasSetterPrefix(
        const hancom::actions::Command& command,
        const wchar_t* const prefix) {
        return std::any_of(
            command.setters.begin(),
            command.setters.end(),
            [&](const hancom::actions::Setter& setter) {
                return setter.path.rfind(prefix, 0) == 0;
            });
    }

    static bool IntegerSetterEquals(
        const hancom::actions::Command& command,
        const wchar_t* const path,
        const LONG expected) {
        const auto found = std::find_if(
            command.setters.begin(),
            command.setters.end(),
            [&](const hancom::actions::Setter& setter) {
                return setter.path == path;
            });
        return found != command.setters.end() &&
            found->value.kind == hancom::actions::ValueKind::Integer &&
            found->value.integer == expected;
    }

    static bool BooleanSetterEquals(
        const hancom::actions::Command& command,
        const wchar_t* const path,
        const bool expected) {
        const auto found = std::find_if(
            command.setters.begin(),
            command.setters.end(),
            [&](const hancom::actions::Setter& setter) {
                return setter.path == path;
            });
        return found != command.setters.end() &&
            found->value.kind == hancom::actions::ValueKind::Boolean &&
            found->value.boolean == expected;
    }
};

}

bool ReferenceLayoutExecutorSmoke() {
    RecordingHost host;
    const bool executed = hancom::reference_layout::Execute(BulkCommand(), &host);
    hancom::reference_layout::Style filled;
    filled.fillColor = 0x123456;
    const auto singleFill =
        hancom::reference_layout::StyleCommands(filled, true);
    const auto zoneFill =
        hancom::reference_layout::StyleCommands(filled, false);
    const auto hasSetter = [](const auto& commands, const wchar_t* const path) {
        return commands.size() == 1 && std::any_of(
            commands.front().setters.begin(),
            commands.front().setters.end(),
            [&](const hancom::actions::Setter& setter) {
                return setter.path == path;
            });
    };
    const auto count = [&](const wchar_t* const name) {
        return std::count(host.parameterNames.begin(), host.parameterNames.end(), name);
    };
    return executed && !host.failed && host.anchorParagraphConfigured &&
        host.tableSeedCharacterConfigured && host.tableCreateGeometryConfigured &&
        host.explicitCharacterMetrics && host.explicitParagraphMetrics &&
        host.explicitCellPadding &&
        host.bordersClearedBeforeMerge && host.visibleEdgeAppliedAfterClear &&
        host.tableSizeUnlockedBeforeReconcile &&
        host.mergeAppliedAfterBorderClear && host.textInsertedAfterMerge &&
        host.geometryReconciledAfterMerges &&
        host.verifiedAfterReconciliation &&
        host.captured == 1 && host.regions == 3 &&
        host.cells == 1 && host.texts == 1 && host.merges == 1 &&
        host.reconciled == 1 && host.verified == 1 &&
        host.left == 1 && !host.appendedParagraph &&
        host.lastTop == 0 && host.lastLeft == 0 &&
        host.lastBottom == 1 && host.lastRight == 1 &&
        count(L"TableCreate") == 1 && count(L"CellZoneBorderFill") == 0 &&
        count(L"CellBorderFill") == 1 && count(L"CellBorder") == 0 &&
        count(L"CellZoneBorder") == 1 &&
        count(L"CharShape") == 2 &&
        hancom::reference_layout::TextBreakAction(0) == std::wstring(L"BreakLine") &&
        hancom::reference_layout::TextBreakAction(1) == std::wstring(L"BreakPara") &&
        singleFill.size() == 1 && singleFill.front().name == L"CellFill" &&
        hasSetter(singleFill, L"FillAttr/WinBrushFaceColor") &&
        zoneFill.size() == 1 && zoneFill.front().name == L"CellFill" &&
        hasSetter(zoneFill, L"ApplyTo") &&
        hasSetter(zoneFill, L"FillAttr/WinBrushFaceColor");
}

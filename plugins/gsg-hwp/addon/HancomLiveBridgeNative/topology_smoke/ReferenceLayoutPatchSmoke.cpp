#include "../ReferenceLayoutExecutor.h"

#include <string>
#include <vector>

namespace {

hancom::actions::Value IntegerValue(const LONG value) {
    hancom::actions::Value result;
    result.kind = hancom::actions::ValueKind::Integer;
    result.integer = value;
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

hancom::actions::Command PatchCommand() {
    hancom::actions::Command command;
    command.kind = hancom::actions::CommandKind::Action;
    command.name = L"ReferenceLayoutPatch";
    command.parameterSet = L"HTableCreation";
    for (const auto& [path, value] : std::vector<std::pair<const wchar_t*, LONG>>{
             {L"Rows", 1}, {L"Columns", 2}, {L"BaseStyleId", 0},
             {L"BodyLeft", 10}, {L"BodyTop", 20},
             {L"BodyWidth", 100}, {L"BodyHeight", 40}}) {
        command.setters.push_back({path, IntegerValue(value)});
    }
    hancom::actions::Value target;
    target.kind = hancom::actions::ValueKind::Text;
    target.text = L"123456";
    command.setters.push_back({L"TargetControlId", target});
    AddIntegerArray(&command, L"ColumnWidths", {60, 40});
    AddIntegerArray(&command, L"RowHeights", {40});
    AddIntegerArray(&command, L"PatchColumnIndexes", {1});
    return command;
}

hancom::actions::Command MergedRowPatchCommand() {
    hancom::actions::Command command;
    command.kind = hancom::actions::CommandKind::Action;
    command.name = L"ReferenceLayoutPatch";
    command.parameterSet = L"HTableCreation";
    for (const auto& [path, value] : std::vector<std::pair<const wchar_t*, LONG>>{
             {L"Rows", 2}, {L"Columns", 2}, {L"BaseStyleId", 0},
             {L"BodyLeft", 10}, {L"BodyTop", 20},
             {L"BodyWidth", 100}, {L"BodyHeight", 40}}) {
        command.setters.push_back({path, IntegerValue(value)});
    }
    hancom::actions::Value target;
    target.kind = hancom::actions::ValueKind::Text;
    target.text = L"merged-target";
    command.setters.push_back({L"TargetControlId", target});
    AddIntegerArray(&command, L"ColumnWidths", {60, 40});
    AddIntegerArray(&command, L"RowHeights", {25, 15});
    AddIntegerArray(&command, L"PatchRowIndexes", {1});
    AddIntegerArray(&command, L"MergeRows", {0});
    AddIntegerArray(&command, L"MergeColumns", {0});
    AddIntegerArray(&command, L"MergeRowSpans", {2});
    AddIntegerArray(&command, L"MergeColumnSpans", {1});
    return command;
}

class PatchHost final : public hancom::reference_layout::Host {
public:
    bool Fail(const hancom::actions::Error&) override { return false; }
    bool ExecuteParameter(const hancom::actions::Command& command) override {
        parameterNames.push_back(command.name);
        if (command.name == L"TableSplitCell") {
            for (const hancom::actions::Setter& setter : command.setters) {
                if (setter.path == L"Cols") {
                    splitColumns = setter.value.integer;
                } else if (setter.path == L"Rows") {
                    splitRows = setter.value.integer;
                }
            }
        }
        return true;
    }
    bool CaptureCreatedTable() override { return false; }
    bool CaptureExistingTable(const std::wstring& controlId) override {
        target = controlId;
        ++captures;
        return true;
    }
    bool ResizeColumn(const LONG column, const LONG width) override {
        resizedColumn = column;
        resizedWidth = width;
        return true;
    }
    bool ResizeRow(const LONG row, const LONG height) override {
        resizedRow = row;
        resizedHeight = height;
        resizedRows.push_back(row);
        resizedHeights.push_back(height);
        return true;
    }
    bool SelectRegion(LONG, LONG, LONG, LONG) override { return false; }
    bool GoToCell(const LONG row, const LONG column) override {
        selectedRow = row;
        selectedColumn = column;
        return true;
    }
    bool Run(const wchar_t* const action, const std::wstring&) override {
        runNames.emplace_back(action);
        return true;
    }
    bool InsertText(
        const hancom::reference_layout::Text&,
        const hancom::reference_layout::Style*) override {
        return false;
    }
    bool MergeCells(const hancom::reference_layout::Merge& merge) override {
        mergedRows.push_back(merge.row);
        mergedColumns.push_back(merge.column);
        return true;
    }
    bool ReconcileFinalGeometry(
        const hancom::reference_layout::Spec&) override {
        return false;
    }
    bool VerifyFinalTopology(const hancom::reference_layout::Spec&) override {
        return false;
    }
    bool VerifyPatchedTopology(
        const hancom::reference_layout::Spec& spec) override {
        verifiedPatchColumns = spec.patchColumns;
        verifiedPatchRows = spec.patchRows;
        ++verified;
        return true;
    }
    bool LeaveTable(const bool appendParagraph = true) override {
        appendedParagraph = appendParagraph;
        ++left;
        return true;
    }

    std::wstring target;
    LONG resizedColumn = -1;
    LONG resizedWidth = -1;
    LONG resizedRow = -1;
    LONG resizedHeight = -1;
    LONG selectedRow = -1;
    LONG selectedColumn = -1;
    LONG splitColumns = -1;
    LONG splitRows = -1;
    int verified = 0;
    int left = 0;
    int captures = 0;
    bool appendedParagraph = true;
    std::vector<std::wstring> parameterNames;
    std::vector<std::wstring> runNames;
    std::vector<LONG> resizedRows;
    std::vector<LONG> resizedHeights;
    std::vector<LONG> verifiedPatchColumns;
    std::vector<LONG> verifiedPatchRows;
    std::vector<LONG> mergedRows;
    std::vector<LONG> mergedColumns;
};

}

bool ReferenceLayoutPatchSmoke() {
    PatchHost host;
    const bool executed = hancom::reference_layout::Execute(PatchCommand(), &host);
    if (!(executed && host.target == L"123456" &&
        host.resizedColumn == 1 && host.resizedWidth == 40 &&
        host.verified == 1 && host.left == 1 &&
        host.captures == 1 &&
        !host.appendedParagraph &&
        host.verifiedPatchColumns == std::vector<LONG>{1} &&
        host.verifiedPatchRows.empty() &&
        host.parameterNames ==
            std::vector<std::wstring>{L"TablePropertyDialog"})) {
        return false;
    }

    PatchHost mergedHost;
    const bool mergedExecuted =
        hancom::reference_layout::Execute(MergedRowPatchCommand(), &mergedHost);
    return mergedExecuted && mergedHost.target == L"merged-target" &&
        mergedHost.resizedRow == 1 && mergedHost.resizedHeight == 15 &&
        mergedHost.resizedRows == std::vector<LONG>{0, 1} &&
        mergedHost.resizedHeights == std::vector<LONG>{25, 15} &&
        mergedHost.selectedRow == 0 && mergedHost.selectedColumn == 0 &&
        mergedHost.verified == 1 && mergedHost.left == 1 &&
        mergedHost.captures == 2 &&
        !mergedHost.appendedParagraph &&
        mergedHost.splitColumns == 1 && mergedHost.splitRows == 2 &&
        mergedHost.verifiedPatchColumns.empty() &&
        mergedHost.verifiedPatchRows == std::vector<LONG>{0, 1} &&
        mergedHost.parameterNames ==
            std::vector<std::wstring>{
                L"TableSplitCell",
                L"TablePropertyDialog"} &&
        mergedHost.mergedRows == std::vector<LONG>{0} &&
        mergedHost.mergedColumns == std::vector<LONG>{0} &&
        mergedHost.runNames.empty();
}

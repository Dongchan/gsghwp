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
            name,
            static_cast<LONG>(index),
            IntegerValue(values[index]),
        });
    }
}

void AddTextArray(
    hancom::actions::Command* const command,
    const wchar_t* const name,
    const std::vector<const wchar_t*>& values) {
    command->arrays.emplace_back(name, static_cast<LONG>(values.size()));
    for (size_t index = 0; index < values.size(); ++index) {
        command->arrayValues.push_back({
            name,
            static_cast<LONG>(index),
            TextValue(values[index]),
        });
    }
}

hancom::actions::Command TextPatchCommand() {
    hancom::actions::Command command;
    command.kind = hancom::actions::CommandKind::Action;
    command.name = L"ReferenceLayoutPatch";
    command.parameterSet = L"HTableCreation";
    for (const auto& [path, value] : std::vector<std::pair<const wchar_t*, LONG>>{
             {L"Rows", 1},
             {L"Columns", 1},
             {L"BaseStyleId", 0},
             {L"BodyLeft", 10},
             {L"BodyTop", 20},
             {L"BodyWidth", 100},
             {L"BodyHeight", 40},
         }) {
        command.setters.push_back({path, IntegerValue(value)});
    }
    command.setters.push_back({L"TargetControlId", TextValue(L"text-target")});
    AddIntegerArray(&command, L"ColumnWidths", {100});
    AddIntegerArray(&command, L"RowHeights", {40});
    AddTextArray(&command, L"StyleKeys", {L"changed"});
    AddTextArray(&command, L"StyleFontNames", {L"Arial"});
    for (const auto& [name, value] :
         std::vector<std::pair<const wchar_t*, LONG>>{
             {L"StyleFontSizes", 1100},
             {L"StyleBold", 1},
             {L"StyleTextColors", 255},
             {L"StyleFillColors", -1},
             {L"StyleAlignments", -1},
             {L"StyleVerticalAlignments", -1},
             {L"StyleWidthRatios", 100},
             {L"StyleLetterSpacings", 0},
             {L"StyleLineSpacingTypes", 0},
             {L"StyleLineSpacings", 100},
             {L"StylePreviousSpacings", 0},
             {L"StyleNextSpacings", 0},
             {L"StylePaddingLeft", -1},
             {L"StylePaddingRight", -1},
             {L"StylePaddingTop", -1},
             {L"StylePaddingBottom", -1},
         }) {
        AddIntegerArray(&command, name, {value});
    }
    AddIntegerArray(&command, L"TextRows", {0});
    AddIntegerArray(&command, L"TextColumns", {0});
    AddIntegerArray(&command, L"TextStyleIndexes", {0});
    AddIntegerArray(&command, L"TextBreakModes", {0});
    AddTextArray(&command, L"TextValues", {L"changed"});
    return command;
}

class TextPatchHost final : public hancom::reference_layout::Host {
public:
    bool Fail(const hancom::actions::Error&) override { return false; }
    bool ExecuteParameter(const hancom::actions::Command& command) override {
        if (command.name == L"CharShape") {
            for (const hancom::actions::Setter& setter : command.setters) {
                if (setter.path == L"TextColor" && setter.value.integer == 255) {
                    textColorApplied = true;
                }
            }
        }
        return true;
    }
    bool CaptureCreatedTable() override { return false; }
    bool CaptureExistingTable(const std::wstring& controlId) override {
        target = controlId;
        return true;
    }
    bool ResizeColumn(LONG, LONG) override { return false; }
    bool ResizeRow(LONG, LONG) override { return false; }
    bool SelectRegion(LONG, LONG, LONG, LONG) override { return false; }
    bool GoToCell(const LONG row, const LONG column) override {
        selectedRow = row;
        selectedColumn = column;
        return true;
    }
    bool Run(const wchar_t* const action, const std::wstring&) override {
        actions.emplace_back(action);
        return true;
    }
    bool InsertText(
        const hancom::reference_layout::Text& text,
        const hancom::reference_layout::Style* const expectedStyle) override {
        inserted = text.value;
        styleReceived = expectedStyle != nullptr &&
            expectedStyle->textColor == 255;
        return insertSucceeds;
    }
    bool MergeCells(const hancom::reference_layout::Merge&) override { return false; }
    bool ReconcileFinalGeometry(const hancom::reference_layout::Spec&) override {
        return false;
    }
    bool VerifyFinalTopology(const hancom::reference_layout::Spec&) override {
        return false;
    }
    bool VerifyPatchedTopology(const hancom::reference_layout::Spec&) override {
        ++verified;
        return true;
    }
    bool LeaveTable(bool) override {
        ++left;
        return true;
    }

    bool insertSucceeds = true;
    bool textColorApplied = false;
    bool styleReceived = false;
    LONG selectedRow = -1;
    LONG selectedColumn = -1;
    int verified = 0;
    int left = 0;
    std::wstring target;
    std::wstring inserted;
    std::vector<std::wstring> actions;
};

}

bool ReferenceLayoutPatchTextSmoke() {
    TextPatchHost host;
    const bool executed =
        hancom::reference_layout::Execute(TextPatchCommand(), &host);
    if (!(executed && host.target == L"text-target" &&
          host.selectedRow == 0 && host.selectedColumn == 0 &&
          host.actions == std::vector<std::wstring>{L"SelectAll"} &&
          host.textColorApplied && host.styleReceived &&
          host.inserted == L"changed" &&
          host.verified == 1 && host.left == 1)) {
        return false;
    }

    TextPatchHost failedReadback;
    failedReadback.insertSucceeds = false;
    return !hancom::reference_layout::Execute(
               TextPatchCommand(),
               &failedReadback) &&
        failedReadback.verified == 0 &&
        failedReadback.left == 0;
}

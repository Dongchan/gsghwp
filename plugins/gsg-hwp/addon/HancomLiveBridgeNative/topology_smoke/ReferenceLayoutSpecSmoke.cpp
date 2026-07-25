#include "../ReferenceLayoutSpec.h"

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

hancom::actions::Setter IntegerSetter(const wchar_t* const path, const LONG value) {
    return hancom::actions::Setter{path, IntegerValue(value)};
}

void AddIntegerArray(
    hancom::actions::Command* const command,
    const wchar_t* const name,
    const std::vector<LONG>& values) {
    command->arrays.emplace_back(name, static_cast<LONG>(values.size()));
    for (size_t index = 0; index < values.size(); ++index) {
        command->arrayValues.push_back(hancom::actions::ArrayValue{
            name,
            static_cast<LONG>(index),
            IntegerValue(values[index]),
        });
    }
}

hancom::actions::Command BaseCommand() {
    hancom::actions::Command command;
    command.kind = hancom::actions::CommandKind::Action;
    command.name = L"ReferenceLayoutBulk";
    command.parameterSet = L"HTableCreation";
    command.setters = {
        IntegerSetter(L"Rows", 2),
        IntegerSetter(L"Columns", 2),
        IntegerSetter(L"BaseStyleId", 0),
        IntegerSetter(L"BodyLeft", 10),
        IntegerSetter(L"BodyTop", 20),
        IntegerSetter(L"BodyWidth", 100),
        IntegerSetter(L"BodyHeight", 100),
    };
    AddIntegerArray(&command, L"ColumnWidths", {60, 40});
    AddIntegerArray(&command, L"RowHeights", {30, 70});
    AddIntegerArray(&command, L"GapAxes", {0});
    AddIntegerArray(&command, L"GapTop", {1});
    AddIntegerArray(&command, L"GapLeft", {0});
    AddIntegerArray(&command, L"GapBottom", {2});
    AddIntegerArray(&command, L"GapRight", {2});
    AddIntegerArray(&command, L"GapMinimums", {70});
    return command;
}

}

bool ReferenceLayoutSpecSmoke() {
    hancom::actions::Error error;
    hancom::reference_layout::Spec bulk;
    const hancom::actions::Command command = BaseCommand();
    if (!hancom::reference_layout::Parse(command, &bulk, &error) ||
        bulk.rows != 2 || bulk.columns != 2 ||
        bulk.columnWidths != std::vector<LONG>{60, 40} ||
        bulk.rowHeights != std::vector<LONG>{30, 70} ||
        bulk.protectedGaps.size() != 1 ||
        bulk.protectedGaps.front().minimum != 70 ||
        !bulk.merges.empty() || !bulk.edges.empty() || !bulk.styles.empty()) {
        return false;
    }

    hancom::actions::Command invalid = command;
    invalid.arrayValues.front().index = 2;
    hancom::reference_layout::Spec rejected;
    if (hancom::reference_layout::Parse(invalid, &rejected, &error)) {
        return false;
    }

    hancom::actions::Command shrunken = command;
    for (hancom::actions::ArrayValue& value : shrunken.arrayValues) {
        if (value.name == L"GapMinimums") {
            value.value.integer = 71;
        }
    }
    if (hancom::reference_layout::Parse(shrunken, &rejected, &error)) {
        return false;
    }

    hancom::actions::Command patch = command;
    patch.name = L"ReferenceLayoutPatch";
    hancom::actions::Value target;
    target.kind = hancom::actions::ValueKind::Text;
    target.text = L"123456";
    patch.setters.push_back(hancom::actions::Setter{L"TargetControlId", std::move(target)});
    AddIntegerArray(&patch, L"PatchColumnIndexes", {1});
    hancom::reference_layout::Spec parsedPatch;
    return hancom::reference_layout::Parse(patch, &parsedPatch, &error) &&
        parsedPatch.patch && parsedPatch.targetControlId == L"123456" &&
        parsedPatch.patchColumns == std::vector<LONG>{1};
}

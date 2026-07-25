#pragma once

#include "ActionProtocol.h"
#include "ReferenceLayoutSpec.h"

#include <vector>

namespace hancom::reference_layout {

actions::Command AnchorParagraphCommand();
actions::Command BaseStyleCommand(LONG styleId);
actions::Command TableSeedCharacterCommand();
actions::Command TableCreateCommand(const Spec& spec);
actions::Command ClearAllBordersCommand();
actions::Command VisibleEdgeCommand(const Edge& edge, const wchar_t* side);
actions::Command UnlockTableSizeCommand();
actions::Command CellSizeCommand(bool column, LONG size);
actions::Command SplitMergedCellCommand(
    const Merge& merge,
    bool splitColumns,
    bool splitRows);
std::vector<actions::Command> StyleCommands(const Style& style, bool singleCell);
const wchar_t* TextBreakAction(LONG breakMode) noexcept;

}

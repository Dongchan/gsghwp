#include "ActionExecutorInternal.h"

#include "OfficialApiState.h"

#include <string>

namespace hancom::actions::detail {

namespace {

bool RunPreservesCellTopology(const std::wstring& name) noexcept {
    return name == L"TableVAlignTop" ||
        name == L"TableVAlignCenter" ||
        name == L"TableVAlignBottom";
}

bool ParameterActionPreservesCellTopology(const Command& command) noexcept {
    return
        (command.name == L"Style" &&
         command.parameterSet == L"HStyle") ||
        (command.name == L"CharShape" &&
         command.parameterSet == L"HCharShape") ||
        (command.name == L"ParagraphShape" &&
         command.parameterSet == L"HParaShape") ||
        (command.name == L"CellFill" &&
         command.parameterSet == L"HCellBorderFill") ||
        (command.name == L"CellBorder" &&
         command.parameterSet == L"HCellBorderFill");
}

bool IsIntegerSetter(
    const Setter& setter,
    const wchar_t* const path,
    const LONG expected) noexcept {
    return setter.path == path &&
        setter.value.kind == ValueKind::Integer &&
        setter.value.integer == expected;
}

bool IsCellMarginSetter(const Setter& setter) noexcept {
    return setter.path == L"ShapeTableCell/HasMargin" ||
        setter.path == L"ShapeTableCell/MarginLeft" ||
        setter.path == L"ShapeTableCell/MarginRight" ||
        setter.path == L"ShapeTableCell/MarginTop" ||
        setter.path == L"ShapeTableCell/MarginBottom";
}

bool IsMarginOnlyTablePropertyDialog(const Command& command) noexcept {
    if (command.name != L"TablePropertyDialog" ||
        command.parameterSet != L"HShapeObject" ||
        !command.arrays.empty() ||
        !command.arrayValues.empty()) {
        return false;
    }

    bool hasShapeType = false;
    bool hasDisabledCellSize = false;
    bool hasCellMargin = false;
    for (const Setter& setter : command.setters) {
        if (setter.path == L"HSet/ShapeType") {
            if (!IsIntegerSetter(setter, L"HSet/ShapeType", 3)) {
                return false;
            }
            hasShapeType = true;
        } else if (setter.path == L"HSet/ShapeCellSize") {
            if (!IsIntegerSetter(setter, L"HSet/ShapeCellSize", 0)) {
                return false;
            }
            hasDisabledCellSize = true;
        } else if (IsCellMarginSetter(setter)) {
            hasCellMargin = true;
        } else {
            return false;
        }
    }
    return hasShapeType && hasDisabledCellSize && hasCellMargin;
}

// MOVE_DOC_END is only ever emitted to append: every caller that sends it is
// about to add paragraphs, a table or a picture after everything the document
// already has. MoveDocEnd alone leaves the caret at the *end of the last
// paragraph*, so the first INSERT_TEXT lands inside it and the appended block
// runs on from the sentence that was already there -- observed live as the
// previous paragraph and the first appended bullet sharing one line.
//
// So the command means "put the caret where an append starts": at the
// beginning of an empty paragraph at the end of the document. If the document
// already ends with an empty paragraph, character offset 0 says so and nothing
// is inserted, which is what keeps repeated appends from stacking blank lines.
//
// The check is a caret offset, not a text read: after MoveDocEnd the offset is
// the length of the last paragraph, so 0 means empty for a paragraph of plain
// text and for one holding a control alike.
bool MoveDocumentEndForAppend(Context* const context) {
    if (!RunAction(
            context->action,
            L"MoveDocEnd",
            context->result,
            L"MOVE_DOC_END")) {
        return false;
    }
    Position end;
    if (!GetPosition(context->hwp, &end, context->result)) {
        return false;
    }
    // Recorded before the break so an atomic rollback deletes the break too
    // and cannot leave an empty paragraph behind.
    context->appendAnchor = end;
    context->hasAppendAnchor = true;
    if (end.character == 0) {
        return true;
    }
    if (!RunAction(
            context->action,
            L"BreakPara",
            context->result,
            L"MOVE_DOC_END")) {
        return false;
    }
    // The break is a real edit. CommandMayMutate cannot say so -- it only sees
    // the command, not whether the document already ended with an empty
    // paragraph -- so record it here, where the answer is known. A later
    // failure must not report an untouched document.
    context->result->partialMutation = true;
    context->topology.Clear();
    return true;
}

}

bool ExecuteCommand(Context* const context, const Command& command) {
    switch (command.kind) {
    case CommandKind::Run:
        if (!RunAction(context->action, command.name, context->result, command.name)) {
            return false;
        }
        ++context->result->actionsExecuted;
        // The closed vertical-alignment set cannot change the cell grid or
        // table geometry. Every other RUN name remains invalidating by default.
        if (!RunPreservesCellTopology(command.name)) {
            context->topology.Clear();
        }
        return true;
    case CommandKind::Action:
        if (command.name == L"ReferenceLayoutBulk" ||
            command.name == L"ReferenceLayoutPatch") {
            if (!ExecuteReferenceLayout(context, command)) {
                return false;
            }
            context->topology.Clear();
            return true;
        }
        if (!ExecuteParameterAction(context, command)) {
            return false;
        }
        if (command.name == L"TableCreate") {
            if (!CaptureCurrentTable(context) || !SetTableTreatAsCharacter(context)) {
                return false;
            }
            context->result->createdControlIds.push_back(context->tableId);
        }
        // Cell margins cannot change addresses or spans when ShapeCellSize is
        // explicitly off, but they can change rendered dimensions. Preserve
        // the structural map and discard only cached geometry for that closed
        // setter set. Every other unknown/future action remains fully
        // invalidating by default.
        if (IsMarginOnlyTablePropertyDialog(command)) {
            context->topology.InvalidateGeometry();
        } else if (!ParameterActionPreservesCellTopology(command)) {
            context->topology.Clear();
        }
        return true;
    case CommandKind::Call:
        if (!ExecuteCall(context, command)) {
            return false;
        }
        context->topology.Clear();
        return true;
    case CommandKind::MovePage:
        return MoveToPage(context, command.page);
    case CommandKind::MovePosition:
        return SetPosition(
            context->hwp,
            Position{command.list, command.paragraph, command.character},
            context->result,
            L"MOVE_POSITION");
    case CommandKind::SelectControl:
        return SelectControl(context, command.first);
    case CommandKind::DeleteControl:
        return DeleteControl(context, command.first);
    case CommandKind::CopyControl:
        return CopyControl(context, command.first);
    case CommandKind::SaveDocumentFile:
        return SaveDocumentFile(context, command.first);
    case CommandKind::CaptureDocumentBlockProbe:
        return ProbeDocumentBlock(context, command.first);
    case CommandKind::RestoreDocumentFile:
        return RestoreDocumentFile(context, command.first, command.page);
    case CommandKind::ApplyCopiedTableAnchor:
        return ApplyCopiedTableAnchor(context, command.first);
    case CommandKind::PasteTable:
        return PasteTable(context);
    case CommandKind::CaptureTable:
        return CaptureCurrentTable(context);
    case CommandKind::MoveDocumentEnd:
        return MoveDocumentEndForAppend(context);
    case CommandKind::DeleteTail:
        return DeleteTail(context, command);
    case CommandKind::InsertText:
        return InsertText(context, command.first, L"text");
    case CommandKind::ReplaceSelection:
        return ReplaceSelection(context, command);
    case CommandKind::TextPatch:
        return PatchText(context, command);
    case CommandKind::InsertPicture:
        return InsertPicture(context, command);
    case CommandKind::Cell:
        return GoToCell(context, command.first);
    case CommandKind::SetCellText:
        return SetCellText(context, command);
    case CommandKind::Merge:
        return MergeCells(context, command.first, command.second);
    case CommandKind::Caption:
        return AttachCaption(context, command);
    case CommandKind::LeaveTable:
        return LeaveTable(context);
    }
    return SetError(context->result, L"COMMAND", L"", L"unsupported command kind");
}

bool ValidateCommandOrder(const Request& request, ExecutionResult* const result) {
    if (request.atomic && (request.commands.empty() ||
        request.commands.front().kind != CommandKind::MoveDocumentEnd)) {
        return SetError(
            result,
            L"ATOMIC_UNSUPPORTED",
            L"POLICY",
            L"atomic rollback requires an append batch starting with MOVE_DOC_END");
    }
    bool copiedTable = false;
    bool restoreSeen = false;
    for (const Command& command : request.commands) {
        if (request.requiresContentAuthorization && !restoreSeen) {
            if (command.kind == CommandKind::RestoreDocumentFile) {
                restoreSeen = true;
            } else if (CommandMayMutate(command)) {
                return SetError(
                    result,
                    L"CHECKED_RESTORE_REQUIRED",
                    L"POLICY",
                    L"ExecuteActionsChecked requires checkpoint restore before any mutation");
            }
        }
        if (command.kind == CommandKind::CopyControl) {
            copiedTable = true;
            continue;
        }
        if ((command.kind == CommandKind::PasteTable ||
             command.kind == CommandKind::ApplyCopiedTableAnchor) &&
            !copiedTable) {
            const wchar_t* const name = command.kind == CommandKind::PasteTable
                ? L"PASTE_TABLE"
                : L"APPLY_COPIED_TABLE_ANCHOR";
            return SetError(
                result,
                L"NO_TABLE_ANCHOR_FORMAT",
                command.kind == CommandKind::PasteTable ? L"table" : command.first,
                std::wstring(name) + L" requires a preceding COPY_CONTROL command");
        }
    }
    if (request.requiresContentAuthorization && !restoreSeen) {
        return SetError(
            result,
            L"CHECKED_RESTORE_REQUIRED",
            L"POLICY",
            L"ExecuteActionsChecked requires one checkpoint restore command");
    }
    return true;
}

std::wstring StructureDigest(IDispatch* const hwp) {
    const hancom::official_api::DocumentState state =
        hancom::official_api::CaptureDocumentState(hwp);
    if (state.pageCount < 0 || state.controlCount < 0) {
        return L"";
    }
    return std::to_wstring(state.pageCount) + L":" +
        std::to_wstring(state.controlCount) + L":" +
        std::to_wstring(state.controlHash);
}

std::wstring CommandStep(const Command& command) {
    switch (command.kind) {
    case CommandKind::Run:
    case CommandKind::Action:
    case CommandKind::Call:
        return command.name;
    case CommandKind::MovePage:
        return L"MOVE_PAGE";
    case CommandKind::MovePosition:
        return L"MOVE_POSITION";
    case CommandKind::SelectControl:
        return L"SELECT_CONTROL";
    case CommandKind::DeleteControl:
        return L"DELETE_CONTROL";
    case CommandKind::CopyControl:
        return L"COPY_CONTROL";
    case CommandKind::SaveDocumentFile:
        return L"SAVE_DOCUMENT_FILE";
    case CommandKind::CaptureDocumentBlockProbe:
        return L"CAPTURE_DOCUMENT_BLOCK_PROBE";
    case CommandKind::RestoreDocumentFile:
        return L"RESTORE_DOCUMENT_FILE";
    case CommandKind::ApplyCopiedTableAnchor:
        return L"APPLY_COPIED_TABLE_ANCHOR";
    case CommandKind::PasteTable:
        return L"PASTE_TABLE";
    case CommandKind::CaptureTable:
        return L"CAPTURE_TABLE";
    case CommandKind::MoveDocumentEnd:
        return L"MOVE_DOC_END";
    case CommandKind::DeleteTail:
        return L"DELETE_TAIL";
    case CommandKind::InsertText:
        return L"INSERT_TEXT";
    case CommandKind::ReplaceSelection:
        return L"REPLACE_SELECTION";
    case CommandKind::TextPatch:
        return L"PATCH_TEXT";
    case CommandKind::InsertPicture:
        return L"INSERT_PICTURE";
    case CommandKind::Cell:
        return L"CELL";
    case CommandKind::SetCellText:
        return L"SET_CELL_TEXT";
    case CommandKind::Merge:
        return L"MERGE";
    case CommandKind::Caption:
        return L"CAPTION";
    case CommandKind::LeaveTable:
        return L"LEAVE_TABLE";
    }
    return L"COMMAND";
}

bool CommandMayMutate(const Command& command) {
    switch (command.kind) {
    case CommandKind::Run:
        return command.name != L"SelectCtrlFront" && command.name != L"Cancel";
    case CommandKind::Action:
    case CommandKind::Call:
    case CommandKind::DeleteControl:
    case CommandKind::RestoreDocumentFile:
    case CommandKind::ApplyCopiedTableAnchor:
    case CommandKind::PasteTable:
    case CommandKind::DeleteTail:
    case CommandKind::InsertText:
    case CommandKind::ReplaceSelection:
    case CommandKind::TextPatch:
    case CommandKind::InsertPicture:
    case CommandKind::SetCellText:
    case CommandKind::Merge:
    case CommandKind::Caption:
        return true;
    case CommandKind::MovePage:
    case CommandKind::MovePosition:
    case CommandKind::SelectControl:
    case CommandKind::CopyControl:
    case CommandKind::SaveDocumentFile:
    // Reads the document and writes the answer to a file the caller named. It
    // is the one command here that exists only to measure, so it must never be
    // able to report a mutation.
    case CommandKind::CaptureDocumentBlockProbe:
    case CommandKind::CaptureTable:
    case CommandKind::MoveDocumentEnd:
    case CommandKind::Cell:
    case CommandKind::LeaveTable:
        return false;
    }
    return false;
}

}

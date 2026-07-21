#pragma once

#include <Windows.h>

#include <string>
#include <vector>

namespace hancom::actions {

enum class ValueKind { Integer, Boolean, Text, Millimeter, Enumeration };

struct Value {
    ValueKind kind = ValueKind::Integer;
    LONG integer = 0;
    bool boolean = false;
    double millimeter = 0.0;
    std::wstring text;
    std::wstring converter;
};

struct Setter {
    std::wstring path;
    Value value;
};

struct ArrayValue {
    std::wstring name;
    LONG index = 0;
    Value value;
};

enum class CommandKind {
    Run,
    Action,
    Call,
    MovePage,
    MovePosition,
    SelectControl,
    DeleteControl,
    CopyControl,
    SaveDocumentFile,
    ApplyCopiedTableAnchor,
    PasteTable,
    CaptureTable,
    MoveDocumentEnd,
    DeleteTail,
    InsertText,
    ReplaceSelection,
    InsertPicture,
    Cell,
    SetCellText,
    Merge,
    Caption,
    LeaveTable,
};

struct Command {
    CommandKind kind = CommandKind::Run;
    std::wstring name;
    std::wstring parameterSet;
    std::vector<Setter> setters;
    std::vector<std::pair<std::wstring, LONG>> arrays;
    std::vector<ArrayValue> arrayValues;
    std::vector<Value> arguments;
    LONG list = 0;
    LONG paragraph = 0;
    LONG character = 0;
    LONG page = 0;
    LONG styleId = -1;
    bool hasCaptionFormat = false;
    std::wstring captionFaceName;
    LONG captionHeight = 0;
    bool captionBold = false;
    LONG captionTextColor = 0;
    LONG captionAlignment = 0;
    LONG captionLineSpacing = 0;
    LONG captionLeftMargin = 0;
    LONG captionRightMargin = 0;
    LONG captionIndentation = 0;
    LONG captionPreviousSpacing = 0;
    LONG captionNextSpacing = 0;
    bool hasCaptionFormatSource = false;
    LONG captionFormatSourceList = 0;
    LONG captionFormatSourceParagraph = 0;
    LONG captionFormatSourceCharacter = 0;
    bool hasPictureBox = false;
    double pictureWidthMm = 0.0;
    double pictureHeightMm = 0.0;
    std::wstring first;
    std::wstring second;
};

struct Request {
    LONG documentId = -1;
    std::wstring fullName;
    bool atomic = false;
    bool hasExpectedCursor = false;
    LONG expectedList = 0;
    LONG expectedParagraph = 0;
    LONG expectedCharacter = 0;
    bool hasExpectedSelection = false;
    bool expectedSelected = false;
    LONG selectionStartList = 0;
    LONG selectionStartParagraph = 0;
    LONG selectionStartCharacter = 0;
    LONG selectionEndList = 0;
    LONG selectionEndParagraph = 0;
    LONG selectionEndCharacter = 0;
    std::vector<Command> commands;
};

struct Error {
    std::wstring code;
    std::wstring location;
    std::wstring message;
};

bool ParseRequest(const std::wstring& payload, Request* request, Error* error) noexcept;
std::wstring ErrorResponse(const Error& error);

}

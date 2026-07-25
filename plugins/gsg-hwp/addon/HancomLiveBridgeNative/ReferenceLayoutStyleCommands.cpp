#include "ReferenceLayoutCommands.h"

#include <utility>

namespace hancom::reference_layout {
namespace {

actions::Value Integer(const LONG value) {
    actions::Value result;
    result.kind = actions::ValueKind::Integer;
    result.integer = value;
    return result;
}

actions::Value Boolean(const bool value) {
    actions::Value result;
    result.kind = actions::ValueKind::Boolean;
    result.boolean = value;
    return result;
}

actions::Value TextValue(const std::wstring& value) {
    actions::Value result;
    result.kind = actions::ValueKind::Text;
    result.text = value;
    return result;
}

actions::Value Enumeration(const wchar_t* const converter, const wchar_t* const value) {
    actions::Value result;
    result.kind = actions::ValueKind::Enumeration;
    result.converter = converter;
    result.text = value;
    return result;
}

actions::Setter Set(std::wstring path, actions::Value value) {
    return actions::Setter{std::move(path), std::move(value)};
}

actions::Command Action(
    const wchar_t* const name,
    const wchar_t* const parameterSet,
    std::vector<actions::Setter> setters) {
    actions::Command command;
    command.kind = actions::CommandKind::Action;
    command.name = name;
    command.parameterSet = parameterSet;
    command.setters = std::move(setters);
    return command;
}

const wchar_t* Alignment(const LONG alignment) {
    switch (alignment) {
    case 0: return L"Left";
    case 1: return L"Center";
    case 2: return L"Right";
    default: return L"Justify";
    }
}

}

std::vector<actions::Command> StyleCommands(
    const Style& style,
    const bool singleCell) {
    std::vector<actions::Command> commands;
    std::vector<actions::Setter> character;
    if (style.bold >= 0) {
        character.push_back(Set(L"Bold", Boolean(style.bold != 0)));
    }
    if (style.fontSize >= 0) {
        character.push_back(Set(L"Height", Integer(style.fontSize)));
    }
    if (style.textColor >= 0) {
        character.push_back(Set(L"TextColor", Integer(style.textColor)));
    }
    for (const wchar_t* const language : {
             L"Hangul", L"Latin", L"Hanja", L"Japanese",
             L"Other", L"Symbol", L"User"}) {
        if (style.widthRatio >= 0) {
            character.push_back(Set(
                std::wstring(L"Ratio") + language,
                Integer(style.widthRatio)));
        }
        if (style.letterSpacing >= -50) {
            character.push_back(Set(
                std::wstring(L"Spacing") + language,
                Integer(style.letterSpacing)));
        }
    }
    if (!style.fontName.empty()) {
        for (const wchar_t* const language : {
                 L"Hangul", L"Latin", L"Hanja", L"Japanese",
                 L"Other", L"Symbol", L"User"}) {
            character.push_back(Set(
                std::wstring(L"FaceName") + language, TextValue(style.fontName)));
            character.push_back(Set(
                std::wstring(L"FontType") + language, Integer(1)));
        }
    }
    if (!character.empty()) {
        commands.push_back(Action(L"CharShape", L"HCharShape", std::move(character)));
    }

    std::vector<actions::Setter> paragraph;
    if (style.alignment >= 0) {
        paragraph.push_back(Set(
            L"AlignType", Enumeration(L"HAlign", Alignment(style.alignment))));
    }
    if (style.lineSpacing >= 0) {
        paragraph.push_back(Set(L"LineSpacing", Integer(style.lineSpacing)));
    }
    if (style.lineSpacingType >= 0) {
        paragraph.push_back(Set(
            L"LineSpacingType", Integer(style.lineSpacingType)));
    }
    if (style.previousSpacing >= 0) {
        paragraph.push_back(Set(
            L"PrevSpacing", Integer(style.previousSpacing)));
    }
    if (style.nextSpacing >= 0) {
        paragraph.push_back(Set(
            L"NextSpacing", Integer(style.nextSpacing)));
    }
    if (!paragraph.empty()) {
        commands.push_back(Action(
            L"ParagraphShape", L"HParaShape", std::move(paragraph)));
    }

    if (style.fillColor >= 0) {
        std::vector<actions::Setter> fill;
        if (!singleCell) {
            fill.push_back(Set(L"ApplyTo", Integer(0)));
        }
        fill.push_back(Set(
            L"FillAttr/Type",
            Enumeration(L"BrushType", L"NullBrush|WinBrush")));
        fill.push_back(Set(
            L"FillAttr/WinBrushFaceColor",
            Integer(style.fillColor)));
        fill.push_back(Set(
            L"FillAttr/WinBrushHatchColor",
            Integer(0x999999)));
        fill.push_back(Set(
            L"FillAttr/WinBrushFaceStyle",
            Enumeration(L"HatchStyle", L"None")));
        fill.push_back(Set(L"FillAttr/WindowsBrush", Integer(1)));
        commands.push_back(Action(
            L"CellFill",
            L"HCellBorderFill",
            std::move(fill)));
    }

    if (style.paddingLeft >= 0) {
        commands.push_back(Action(L"TablePropertyDialog", L"HShapeObject", {
            Set(L"HSet/ShapeType", Integer(3)),
            Set(L"HSet/ShapeCellSize", Integer(0)),
            Set(L"ShapeTableCell/HasMargin", Integer(1)),
            Set(L"ShapeTableCell/MarginLeft", Integer(style.paddingLeft)),
            Set(L"ShapeTableCell/MarginRight", Integer(style.paddingRight)),
            Set(L"ShapeTableCell/MarginTop", Integer(style.paddingTop)),
            Set(L"ShapeTableCell/MarginBottom", Integer(style.paddingBottom)),
        }));
    }
    return commands;
}

}

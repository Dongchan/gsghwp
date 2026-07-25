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

actions::Value Text(std::wstring value) {
    actions::Value result;
    result.kind = actions::ValueKind::Text;
    result.text = std::move(value);
    return result;
}

actions::Value Enumeration(const wchar_t* const converter, std::wstring value) {
    actions::Value result;
    result.kind = actions::ValueKind::Enumeration;
    result.converter = converter;
    result.text = std::move(value);
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

std::wstring LineType(const std::wstring& style) {
    if (style == L"none") return L"None";
    if (style == L"solid") return L"Solid";
    if (style == L"dash") return L"Dash";
    if (style == L"dot") return L"Dot";
    if (style == L"dash_dot") return L"DashDot";
    if (style == L"dash_dot_dot") return L"DashDotDot";
    if (style == L"long_dash") return L"LongDash";
    if (style == L"circle") return L"Circle";
    if (style == L"double_slim") return L"DoubleSlim";
    if (style == L"slim_thick") return L"SlimThick";
    if (style == L"thick_slim") return L"ThickSlim";
    return L"SlimThickSlim";
}

}

actions::Command AnchorParagraphCommand() {
    return Action(L"ParagraphShape", L"HParaShape", {
        Set(L"AlignType", Enumeration(L"HAlign", L"Center")),
        Set(L"LeftMargin", Integer(0)),
        Set(L"RightMargin", Integer(0)),
        Set(L"Indentation", Integer(0)),
        Set(L"PrevSpacing", Integer(0)),
        Set(L"NextSpacing", Integer(0)),
        Set(L"LineSpacingType", Integer(0)),
        Set(L"LineSpacing", Integer(100)),
    });
}

actions::Command BaseStyleCommand(const LONG styleId) {
    return Action(L"Style", L"HStyle", {Set(L"Apply", Integer(styleId))});
}

actions::Command TableSeedCharacterCommand() {
    return Action(L"CharShape", L"HCharShape", {
        Set(L"Height", Integer(100)),
    });
}

const wchar_t* TextBreakAction(const LONG breakMode) noexcept {
    return breakMode == 1 ? L"BreakPara" : L"BreakLine";
}

actions::Command TableCreateCommand(const Spec& spec) {
    actions::Command command = Action(L"TableCreate", L"HTableCreation", {
        Set(L"Rows", Integer(spec.rows)),
        Set(L"Cols", Integer(spec.columns)),
        Set(L"WidthType", Integer(2)),
        Set(L"HeightType", Integer(1)),
        Set(L"WidthValue", Integer(spec.bodyWidth)),
        Set(L"HeightValue", Integer(spec.bodyHeight)),
        Set(L"TableProperties/TreatAsChar", Boolean(true)),
        Set(L"TableProperties/ProtectSize", Boolean(false)),
        Set(L"TableProperties/Width", Integer(spec.bodyWidth)),
        Set(L"TableProperties/Height", Integer(spec.bodyHeight)),
        Set(L"TableProperties/CellMarginLeft", Integer(0)),
        Set(L"TableProperties/CellMarginRight", Integer(0)),
        Set(L"TableProperties/CellMarginTop", Integer(0)),
        Set(L"TableProperties/CellMarginBottom", Integer(0)),
    });
    command.arrays.emplace_back(L"ColWidth", spec.columns);
    command.arrays.emplace_back(L"RowHeight", spec.rows);
    for (LONG index = 0; index < spec.columns; ++index) {
        command.arrayValues.push_back(actions::ArrayValue{
            L"ColWidth", index, Integer(spec.columnWidths[static_cast<size_t>(index)])});
    }
    for (LONG index = 0; index < spec.rows; ++index) {
        command.arrayValues.push_back(actions::ArrayValue{
            L"RowHeight", index, Integer(spec.rowHeights[static_cast<size_t>(index)])});
    }
    return command;
}

actions::Command ClearAllBordersCommand() {
    std::vector<actions::Setter> setters;
    for (const wchar_t* const axis : {L"Vert", L"Horz"}) {
        setters.push_back(Set(
            std::wstring(L"HSet/Type") + axis,
            Enumeration(L"HwpLineType", L"None")));
    }
    for (const wchar_t* const side : {L"Left", L"Right", L"Top", L"Bottom"}) {
        setters.push_back(Set(
            std::wstring(L"HSet/BorderType") + side,
            Enumeration(L"HwpLineType", L"None")));
    }
    return Action(L"CellBorderFill", L"HCellBorderFill", std::move(setters));
}

actions::Command VisibleEdgeCommand(
    const Edge& edge,
    const wchar_t* const side) {
    const std::wstring prefix = L"SelCellsBorderFill/";
    const std::wstring colorName = std::wstring(side) == L"Left"
        ? L"BorderCorlorLeft"
        : std::wstring(L"BorderColor") + side;
    return Action(L"CellZoneBorder", L"HCellBorderFill", {
        Set(L"ApplyTo", Integer(2)),
        Set(L"NoNeighborCell", Integer(0)),
        Set(prefix + L"BorderType" + side,
            Enumeration(L"HwpLineType", LineType(edge.style))),
        Set(prefix + L"BorderWidth" + side,
            Enumeration(L"HwpLineWidth", edge.width)),
        Set(prefix + colorName, Integer(edge.color)),
    });
}

actions::Command UnlockTableSizeCommand() {
    return Action(L"TablePropertyDialog", L"HShapeObject", {
        Set(L"ProtectSize", Boolean(false)),
    });
}

actions::Command CellSizeCommand(const bool column, const LONG size) {
    return Action(L"TablePropertyDialog", L"HShapeObject", {
        Set(L"HSet/ShapeType", Integer(3)),
        Set(L"HSet/ShapeCellSize", Integer(1)),
        Set(
            column ? L"ShapeTableCell/Width" : L"ShapeTableCell/Height",
            Integer(size)),
    });
}

actions::Command SplitMergedCellCommand(
    const Merge& merge,
    const bool splitColumns,
    const bool splitRows) {
    return Action(L"TableSplitCell", L"HTableSplitCell", {
        Set(L"Cols", Integer(splitColumns ? merge.columnSpan : 1)),
        Set(L"Rows", Integer(splitRows ? merge.rowSpan : 1)),
        Set(L"DistributeHeight", Integer(0)),
        Set(L"Merge", Integer(0)),
        Set(L"Mode2", Integer(1)),
    });
}

}

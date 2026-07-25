#pragma once

#include "ActionProtocol.h"

#include <string>
#include <vector>

namespace hancom::reference_layout {

struct Merge {
    LONG row = 0;
    LONG column = 0;
    LONG rowSpan = 1;
    LONG columnSpan = 1;
};

struct Edge {
    LONG orientation = 0;
    LONG line = 0;
    LONG start = 0;
    LONG end = 0;
    std::wstring style;
    std::wstring width;
    LONG color = 0;
};

struct Style {
    std::wstring key;
    std::wstring fontName;
    LONG fontSize = -1;
    LONG bold = -1;
    LONG textColor = -1;
    LONG fillColor = -1;
    LONG alignment = -1;
    LONG verticalAlignment = -1;
    LONG widthRatio = -1;
    LONG letterSpacing = -1'000;
    LONG lineSpacingType = -1;
    LONG lineSpacing = -1;
    LONG previousSpacing = -1;
    LONG nextSpacing = -1;
    LONG paddingLeft = -1;
    LONG paddingRight = -1;
    LONG paddingTop = -1;
    LONG paddingBottom = -1;
};

struct Region {
    LONG top = 0;
    LONG left = 0;
    LONG bottom = 0;
    LONG right = 0;
    LONG styleIndex = -1;
};

struct Text {
    LONG row = 0;
    LONG column = 0;
    LONG styleIndex = -1;
    LONG breakMode = 0;
    std::wstring value;
};

struct ProtectedGap {
    LONG axis = 0;
    LONG top = 0;
    LONG left = 0;
    LONG bottom = 0;
    LONG right = 0;
    LONG minimum = 0;
};

struct Spec {
    bool patch = false;
    std::wstring targetControlId;
    LONG rows = 0;
    LONG columns = 0;
    LONG baseStyleId = 0;
    LONG bodyLeft = 0;
    LONG bodyTop = 0;
    LONG bodyWidth = 0;
    LONG bodyHeight = 0;
    std::vector<LONG> columnWidths;
    std::vector<LONG> rowHeights;
    std::vector<LONG> patchColumns;
    std::vector<LONG> patchRows;
    std::vector<Merge> merges;
    std::vector<Edge> edges;
    std::vector<Style> styles;
    std::vector<Region> regions;
    std::vector<Text> texts;
    std::vector<ProtectedGap> protectedGaps;
};

bool Parse(
    const actions::Command& command,
    Spec* spec,
    actions::Error* error) noexcept;

bool Validate(const Spec& spec, actions::Error* error) noexcept;

}

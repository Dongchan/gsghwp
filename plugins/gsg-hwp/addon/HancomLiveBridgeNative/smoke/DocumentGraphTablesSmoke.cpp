#include "../DocumentGraphTables.h"

#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

using hancom::graph::tables::BuildStatus;
using hancom::graph::tables::BuildTableGraphTopology;
using hancom::graph::tables::AdaptCompleteCellAppearances;
using hancom::graph::tables::AttachNestedTables;
using hancom::graph::tables::CellAppearanceObservation;
using hancom::graph::tables::CellObservationState;
using hancom::graph::tables::HeaderCellObservation;
using hancom::graph::tables::IntegerObservation;
using hancom::graph::tables::NestedTableObservation;
using hancom::graph::tables::ResolveNestedTableHost;
using hancom::graph::tables::TableGraphRecord;
using hancom::graph::tables::TextObservation;
using hancom::inspection::CellTopology;
using hancom::inspection::CellTopologyCell;

CellTopologyCell Cell(
    std::wstring address,
    const long list,
    const long rowSpan = 1,
    const long columnSpan = 1) {
    CellTopologyCell cell;
    cell.tableInstanceId = L"table-session";
    cell.address = std::move(address);
    cell.listId = list;
    cell.rowSpan = rowSpan;
    cell.columnSpan = columnSpan;
    cell.width = 100;
    cell.height = 50;
    return cell;
}

bool BuildFixture(CellTopology* const topology, const bool duplicateList) {
    std::vector<CellTopologyCell> cells{
        Cell(L"A1", 10, 2, 2),
        Cell(L"C1", 11),
        Cell(L"C2", 12),
        Cell(L"A3", 13),
        Cell(L"B3", duplicateList ? 10 : 14),
        Cell(L"C3", 15),
    };
    std::wstring error;
    return topology->Build(std::move(cells), &error);
}

IntegerObservation Integer(const long value) {
    return {CellObservationState::Value, value};
}

TextObservation Text(std::wstring value) {
    return {CellObservationState::Value, std::move(value)};
}

std::vector<CellAppearanceObservation> CompleteAppearances(
    const CellTopology& topology) {
    std::vector<CellAppearanceObservation> appearances;
    for (const CellTopologyCell& cell : topology.Cells()) {
        CellAppearanceObservation appearance;
        appearance.address = cell.address;
        appearance.fillColor = Integer(0x00ffffff);
        appearance.fillBrush = Integer(1);
        appearance.marginLeft = Integer(100);
        appearance.marginRight = Integer(101);
        appearance.marginTop = Integer(102);
        appearance.marginBottom = Integer(103);
        appearance.verticalAlign = Integer(2);
        appearance.alignment = Integer(1);
        appearance.faceName = Text(L"Test Face");
        appearance.characterHeight = Integer(1000);
        appearance.bold = Integer(0);
        for (size_t side = 0; side < appearance.borderType.size(); ++side) {
            appearance.borderType[side] = Integer(static_cast<long>(side + 1));
            appearance.borderWidth[side] = Integer(static_cast<long>(side + 5));
            appearance.borderColor[side] =
                Integer(static_cast<long>(0x10 + side));
        }
        appearances.push_back(std::move(appearance));
    }
    return appearances;
}

bool PhysicalCellsAndOccupancySmoke() {
    CellTopology topology;
    if (!BuildFixture(&topology, false)) {
        return false;
    }
    TableGraphRecord graph;
    const std::vector<HeaderCellObservation> headers{
        {L"A1", true},
        {L"C1", false},
    };
    const std::vector<CellAppearanceObservation> appearances =
        CompleteAppearances(topology);
    const std::wstring sessionInstanceId = L"table-session";
    const BuildStatus status = BuildTableGraphTopology(
        topology,
        true,
        true,
        sessionInstanceId,
        headers,
        appearances,
        &graph);
    return status == BuildStatus::Complete &&
        graph.rowCount == 3 && graph.columnCount == 3 &&
        graph.physicalCells.size() == 6 &&
        graph.physicalIntervals.size() == 6 &&
        graph.physicalIntervals[0].rowBegin1 == 1 &&
        graph.physicalIntervals[0].rowEnd1 == 3 &&
        graph.physicalIntervals[0].columnBegin1 == 1 &&
        graph.physicalIntervals[0].columnEnd1 == 3 &&
        graph.repeatHeaderExposed && graph.repeatHeader &&
        graph.physicalCells[0].headerExposed &&
        graph.physicalCells[0].header &&
        graph.physicalCells[1].headerExposed &&
        !graph.physicalCells[1].header &&
        !graph.physicalCells[3].headerExposed &&
        graph.physicalCells[0].appearance.marginLeft.value == 100 &&
        graph.physicalCells[0].appearance.borderColor[3].value == 0x13 &&
        graph.physicalCells[0].text == L"" &&
        graph.physicalCells[0].textRunsState ==
            CellObservationState::NotExposed;
}

bool NestedTableHostUsesCellStoryListSmoke() {
    CellTopology topology;
    if (!BuildFixture(&topology, false)) {
        return false;
    }
    TableGraphRecord graph;
    const std::wstring sessionInstanceId = L"table-session";
    const std::vector<HeaderCellObservation> noHeaders;
    const std::vector<CellAppearanceObservation> appearances =
        CompleteAppearances(topology);
    if (BuildTableGraphTopology(
            topology,
            true,
            false,
            sessionInstanceId,
            noHeaders,
            appearances,
            &graph) != BuildStatus::Complete) {
        return false;
    }
    std::wstring host;
    const std::vector<NestedTableObservation> nestedTables{
        {L"nested-table", 14},
    };
    return ResolveNestedTableHost(graph, 14, &host) ==
            BuildStatus::Complete &&
        host == L"B3" &&
        AttachNestedTables(nestedTables, &graph) == BuildStatus::Complete &&
        graph.nestedTables.size() == 1 &&
        graph.nestedTables[0].hostAddress == L"B3" &&
        ResolveNestedTableHost(graph, 999, &host) ==
            BuildStatus::InvalidTopology;
}

bool AmbiguousNestedHostFailsClosedSmoke() {
    CellTopology topology;
    if (!BuildFixture(&topology, true)) {
        return false;
    }
    TableGraphRecord graph;
    const std::wstring sessionInstanceId = L"table-session";
    const std::vector<HeaderCellObservation> noHeaders;
    const std::vector<CellAppearanceObservation> appearances =
        CompleteAppearances(topology);
    if (BuildTableGraphTopology(
            topology,
            false,
            false,
            sessionInstanceId,
            noHeaders,
            appearances,
            &graph) != BuildStatus::Complete) {
        return false;
    }
    std::wstring host;
    return ResolveNestedTableHost(graph, 10, &host) ==
        BuildStatus::AmbiguousNestedHost;
}

bool UnknownHeaderAddressFailsClosedSmoke() {
    CellTopology topology;
    if (!BuildFixture(&topology, false)) {
        return false;
    }
    TableGraphRecord graph;
    const std::wstring sessionInstanceId = L"table-session";
    const std::vector<HeaderCellObservation> headers{{L"Z99", true}};
    const std::vector<CellAppearanceObservation> appearances =
        CompleteAppearances(topology);
    return BuildTableGraphTopology(
               topology,
               true,
               true,
               sessionInstanceId,
               headers,
               appearances,
               &graph) == BuildStatus::InvalidTopology;
}

bool MissingCellObservationFailsClosedSmoke() {
    CellTopology topology;
    if (!BuildFixture(&topology, false)) {
        return false;
    }
    std::vector<CellAppearanceObservation> appearances =
        CompleteAppearances(topology);
    appearances.pop_back();
    TableGraphRecord graph;
    const std::vector<HeaderCellObservation> noHeaders;
    return BuildTableGraphTopology(
               topology,
               false,
               false,
               L"table-session",
               noHeaders,
               appearances,
               &graph) == BuildStatus::InvalidTopology;
}

bool ParentMismatchFailsClosedSmoke() {
    CellTopology topology;
    if (!BuildFixture(&topology, false)) {
        return false;
    }
    TableGraphRecord graph;
    const std::vector<HeaderCellObservation> noHeaders;
    const std::vector<CellAppearanceObservation> appearances =
        CompleteAppearances(topology);
    return BuildTableGraphTopology(
               topology,
               false,
               false,
               L"different-table",
               noHeaders,
               appearances,
               &graph) == BuildStatus::InvalidTopology;
}

bool LargeMergedCellUsesOneIntervalSmoke() {
    CellTopology topology;
    std::vector<CellTopologyCell> cells{
        Cell(L"A1", 71, 1, 200000),
    };
    std::wstring error;
    if (!topology.Build(std::move(cells), &error)) {
        return false;
    }
    TableGraphRecord graph;
    const std::vector<HeaderCellObservation> noHeaders;
    const std::vector<CellAppearanceObservation> appearances =
        CompleteAppearances(topology);
    return BuildTableGraphTopology(
               topology,
               false,
               false,
               L"table-session",
               noHeaders,
               appearances,
               &graph) == BuildStatus::Complete &&
        graph.columnCount == 200000 &&
        graph.physicalIntervals.size() == 1;
}

bool DeduplicateOnlyAfterObservationSmoke() {
    CellTopology topology;
    if (!BuildFixture(&topology, false)) {
        return false;
    }
    hancom::inspection::TableCellFormat common;
    common.tableInstanceId = L"table-session";
    common.addresses = {L"A1", L"C1"};
    common.fillColor = 0;
    common.fillBrush = 1;
    common.marginLeft = 10;
    common.marginRight = 11;
    common.marginTop = 12;
    common.marginBottom = 13;
    common.verticalAlign = 2;
    common.alignment = 1;
    common.faceName = L"Observed";
    common.characterHeight = 1000;
    common.bold = 0;
    for (size_t side = 0; side < common.addresses.size() + 2; ++side) {
        common.borderType[side] = static_cast<long>(side);
        common.borderWidth[side] = static_cast<long>(side + 4);
        common.borderColor[side] = static_cast<long>(side + 8);
    }
    std::vector<hancom::inspection::TableCellFormat> formats;
    formats.push_back(common);
    for (const CellTopologyCell& cell : topology.Cells()) {
        if (cell.address == L"A1" || cell.address == L"C1") {
            continue;
        }
        hancom::inspection::TableCellFormat distinct = common;
        distinct.addresses = {cell.address};
        formats.push_back(std::move(distinct));
    }
    std::vector<CellAppearanceObservation> appearances;
    return AdaptCompleteCellAppearances(
               topology,
               formats,
               &appearances) == BuildStatus::Complete &&
        appearances.size() == topology.Cells().size() &&
        appearances[0].fillColor.state == CellObservationState::Value &&
        appearances[0].fillColor.value == 0 &&
        appearances[0].faceName.value == L"Observed";
}

bool SplitPageRangePreservedSmoke() {
    CellTopology topology;
    std::vector<CellTopologyCell> cells{
        Cell(L"A1", 81),
    };
    cells[0].pageStart = 2;
    cells[0].pageEnd = 3;
    cells[0].text = L"split";
    std::wstring error;
    if (!topology.Build(std::move(cells), &error)) {
        return false;
    }
    TableGraphRecord graph;
    const std::vector<HeaderCellObservation> noHeaders;
    const std::vector<CellAppearanceObservation> appearances =
        CompleteAppearances(topology);
    return BuildTableGraphTopology(
               topology,
               false,
               false,
               L"table-session",
               noHeaders,
               appearances,
               &graph) == BuildStatus::Complete &&
        graph.physicalCells[0].pageStart == 2 &&
        graph.physicalCells[0].pageEnd == 3 &&
        graph.physicalCells[0].text == L"split";
}

} // namespace

bool DocumentGraphTablesSmoke() {
    const bool occupancy = PhysicalCellsAndOccupancySmoke();
    const bool nested = NestedTableHostUsesCellStoryListSmoke();
    const bool ambiguous = AmbiguousNestedHostFailsClosedSmoke();
    const bool header = UnknownHeaderAddressFailsClosedSmoke();
    const bool observations = MissingCellObservationFailsClosedSmoke();
    const bool parent = ParentMismatchFailsClosedSmoke();
    const bool sparse = LargeMergedCellUsesOneIntervalSmoke();
    const bool deduplication = DeduplicateOnlyAfterObservationSmoke();
    const bool splitPage = SplitPageRangePreservedSmoke();
    std::wcout << L"TABLE_GRAPH_PHYSICAL_OCCUPANCY " << occupancy << L'\n'
               << L"TABLE_GRAPH_NESTED_HOST_LIST " << nested << L'\n'
               << L"TABLE_GRAPH_AMBIGUOUS_HOST_FAIL_CLOSED " << ambiguous
               << L'\n'
               << L"TABLE_GRAPH_HEADER_NO_GEOMETRY_GUESS " << header << L'\n'
               << L"TABLE_GRAPH_COMPLETE_CELL_OBSERVATIONS " << observations
               << L'\n'
               << L"TABLE_GRAPH_PARENT_MISMATCH_FAIL_CLOSED " << parent << L'\n'
               << L"TABLE_GRAPH_SPARSE_INTERVALS " << sparse << L'\n';
    std::wcout << L"TABLE_GRAPH_DEDUP_AFTER_OBSERVATION " << deduplication
               << L'\n'
               << L"TABLE_GRAPH_SPLIT_PAGE_RANGE " << splitPage << L'\n';
    return occupancy && nested && ambiguous && header && observations &&
        parent && sparse && deduplication && splitPage;
}

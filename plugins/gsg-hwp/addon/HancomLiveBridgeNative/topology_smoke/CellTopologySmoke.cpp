#include "../CellTopology.h"

#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

using hancom::inspection::CellDirection;
using hancom::inspection::CellTopology;
using hancom::inspection::CellTopologyCell;
using hancom::inspection::CellTopologyStep;
using hancom::inspection::ParseSelectedCellAddresses;

CellTopologyCell Cell(
    const wchar_t* const address,
    const long rowSpan,
    const long columnSpan,
    const wchar_t* const right,
    const wchar_t* const down) {
    CellTopologyCell cell;
    cell.address = address;
    long row = 0;
    long column = 0;
    if (hancom::inspection::ParseCellAddress(cell.address, &row, &column)) {
        cell.listId = row * 100 + column;
    }
    cell.rowSpan = rowSpan;
    cell.columnSpan = columnSpan;
    cell.rightAddress = right;
    cell.downAddress = down;
    return cell;
}

int Fail(const char* const message) {
    std::cerr << message << '\n';
    return 1;
}

}

bool ReferenceLayoutSpecSmoke();
bool ReferenceLayoutExecutorSmoke();
bool ReferenceLayoutPatchSmoke();
bool ReferenceLayoutPatchTextSmoke();

int wmain() {
    if (!ReferenceLayoutSpecSmoke()) {
        return Fail("compressed reference-layout payload did not parse");
    }
    if (!ReferenceLayoutExecutorSmoke()) {
        return Fail("compressed reference-layout execution was not bulk");
    }
    if (!ReferenceLayoutPatchSmoke()) {
        return Fail("reference-layout patch recreated or over-applied the table");
    }
    if (!ReferenceLayoutPatchTextSmoke()) {
        return Fail("reference-layout text patch was not applied and verified in place");
    }
    std::vector<std::wstring> selectedAddresses;
    if (!ParseSelectedCellAddresses(
            L"= B2,C2,B3,C3;",
            &selectedAddresses) ||
        selectedAddresses !=
            std::vector<std::wstring>{L"B2", L"C2", L"B3", L"C3"}) {
        return Fail("TableFormula cell addresses were not parsed");
    }

    CellTopology topology;
    std::wstring error;
    if (!topology.Build(
            {
                Cell(L"A1", 1, 2, L"C1", L"A2"),
                Cell(L"C1", 1, 2, L"E1", L"B2"),
                Cell(L"E1", 1, 1, L"", L"D2"),
                Cell(L"A2", 1, 1, L"B2", L""),
                Cell(L"B2", 1, 2, L"D2", L""),
                Cell(L"D2", 1, 2, L"", L""),
            },
            &error)) {
        return Fail("irregular topology did not build");
    }

    std::vector<CellTopologyCell> measuredCells{
        Cell(L"A1", 1, 1, L"B1", L""),
        Cell(L"B1", 1, 1, L"", L""),
    };
    measuredCells[0].width = 1200;
    measuredCells[0].height = 3400;
    measuredCells[1].width = 5600;
    measuredCells[1].height = 7800;
    CellTopology geometryInvalidation;
    if (!geometryInvalidation.Build(std::move(measuredCells), &error)) {
        return Fail("measured topology did not build");
    }
    geometryInvalidation.InvalidateGeometry();
    const CellTopologyCell* const measuredA1 =
        geometryInvalidation.Find(L"A1");
    const CellTopologyCell* const measuredB1 =
        geometryInvalidation.OwnerAt(1, 2);
    if (geometryInvalidation.Empty() ||
        geometryInvalidation.Rows() != 1 ||
        geometryInvalidation.Columns() != 2 ||
        measuredA1 == nullptr ||
        measuredB1 == nullptr ||
        measuredA1->rowSpan != 1 ||
        measuredA1->columnSpan != 1 ||
        measuredB1->address != L"B1" ||
        measuredA1->width != -1 ||
        measuredA1->height != -1 ||
        measuredB1->width != -1 ||
        measuredB1->height != -1) {
        return Fail("geometry invalidation discarded structure or retained stale dimensions");
    }

    CellTopology geometryOnlyChange;
    if (!geometryOnlyChange.Build(
            {
                Cell(L"A1", 1, 2, L"", L""),
                Cell(L"C1", 1, 2, L"", L""),
                Cell(L"E1", 1, 1, L"", L""),
                Cell(L"A2", 1, 1, L"", L""),
                Cell(L"B2", 1, 2, L"", L""),
                Cell(L"D2", 1, 2, L"", L""),
            },
            &error) ||
        !topology.HasSamePhysicalShape(geometryOnlyChange)) {
        return Fail("geometry-only changes did not preserve physical topology");
    }

    CellTopology splitShape;
    if (!splitShape.Build(
            {
                Cell(L"A1", 1, 1, L"", L""),
                Cell(L"B1", 1, 1, L"", L""),
                Cell(L"C1", 1, 1, L"", L""),
                Cell(L"D1", 1, 1, L"", L""),
                Cell(L"E1", 1, 1, L"", L""),
                Cell(L"A2", 1, 1, L"", L""),
                Cell(L"B2", 1, 1, L"", L""),
                Cell(L"C2", 1, 1, L"", L""),
                Cell(L"D2", 1, 1, L"", L""),
                Cell(L"E2", 1, 1, L"", L""),
            },
            &error) ||
        topology.HasSamePhysicalShape(splitShape)) {
        return Fail("changed merge topology was not detected");
    }

    const std::vector<std::wstring> column = topology.IntersectingColumn(3);
    if (column != std::vector<std::wstring>{L"C1", L"B2"}) {
        return Fail("physical column intersections were not resolved from spans");
    }

    std::vector<CellTopologyStep> path;
    std::vector<std::wstring> region;
    if (!topology.PlanRectangularMerge(L"A1", L"C1", &path, &region, &error) ||
        path.size() != 1 || path.front().direction != CellDirection::Right ||
        path.front().destination != L"C1") {
        return Fail("A1 to C1 did not use the one actual right neighbour");
    }

    if (topology.PlanRectangularMerge(L"A1", L"B2", &path, &region, &error)) {
        return Fail("partial-overlap merge region was not rejected");
    }

    if (!topology.PlanRectangularMerge(L"A1", L"D2", &path, &region, &error) ||
        path.size() != 3 || path[0].destination != L"C1" ||
        path[1].destination != L"E1" || path[2].destination != L"D2") {
        return Fail("merge endpoint was not reached through actual neighbours");
    }
    std::wstring first;
    std::wstring last;
    if (!topology.PlanRectangularSelection(
            204,
            101,
            &first,
            &last,
            &path,
            &region,
            &error) ||
        first != L"A1" || last != L"D2" || region.size() != 6 || path.size() != 3) {
        return Fail("reversed cell selection was not normalized from list ids");
    }
    if (!topology.PlanRectangularSelection(
            std::vector<std::wstring>{L"A1", L"C1"},
            &first,
            &last,
            &path,
            &region,
            &error) ||
        first != L"A1" || last != L"C1" || region.size() != 2 || path.size() != 1) {
        return Fail("cell selection was not normalized from physical addresses");
    }
    std::cout << "CELL_TOPOLOGY_GEOMETRY_INVALIDATION 1\n";
    return 0;
}

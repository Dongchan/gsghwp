#pragma once

#include <map>
#include <string>
#include <vector>

namespace hancom::inspection {

enum class CellDirection {
    Right,
    Down,
};

struct CellTopologyCell {
    std::wstring tableInstanceId;
    std::wstring address;
    long listId = 0;
    long rowSpan = 1;
    long columnSpan = 1;
    long pageStart = 1;
    long pageEnd = 1;
    std::wstring text;
    long width = -1;
    long height = -1;
    std::wstring rightAddress;
    std::wstring downAddress;
    long row = 0;
    long column = 0;
};

struct CellTopologyStep {
    CellDirection direction = CellDirection::Right;
    std::wstring destination;
};

bool ParseCellAddress(
    const std::wstring& address,
    long* row,
    long* column) noexcept;

bool ParseSelectedCellAddresses(
    const std::wstring& command,
    std::vector<std::wstring>* addresses) noexcept;

class CellTopology final {
public:
    bool Build(
        std::vector<CellTopologyCell> cells,
        std::wstring* error) noexcept;
    void Clear() noexcept;
    bool Empty() const noexcept;
    long Rows() const noexcept;
    long Columns() const noexcept;
    const CellTopologyCell* Find(const std::wstring& address) const noexcept;
    const std::vector<CellTopologyCell>& Cells() const noexcept;
    bool HasSamePhysicalShape(const CellTopology& other) const noexcept;
    std::vector<std::wstring> IntersectingColumn(long physicalColumn) const;
    bool PlanRectangularMerge(
        const std::wstring& first,
        const std::wstring& second,
        std::vector<CellTopologyStep>* path,
        std::vector<std::wstring>* region,
        std::wstring* error) const noexcept;
    bool PlanRectangularSelection(
        long firstListId,
        long secondListId,
        std::wstring* firstAddress,
        std::wstring* lastAddress,
        std::vector<CellTopologyStep>* path,
        std::vector<std::wstring>* region,
        std::wstring* error) const noexcept;
    bool PlanRectangularSelection(
        const std::vector<std::wstring>& selectedAddresses,
        std::wstring* firstAddress,
        std::wstring* lastAddress,
        std::vector<CellTopologyStep>* path,
        std::vector<std::wstring>* region,
        std::wstring* error) const noexcept;

private:
    std::vector<CellTopologyCell> cells_;
    std::map<std::wstring, size_t> byAddress_;
    long rows_ = 0;
    long columns_ = 0;
};

}

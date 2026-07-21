#include "CellTopology.h"

#include <algorithm>
#include <cwctype>
#include <limits>
#include <queue>
#include <set>
#include <utility>

namespace hancom::inspection {
namespace {

constexpr unsigned long long kMaximumPhysicalSlots = 200'000;

bool Fail(std::wstring* const error, const wchar_t* const message) {
    if (error != nullptr) {
        *error = message;
    }
    return false;
}

std::wstring Normalize(std::wstring address) {
    std::transform(address.begin(), address.end(), address.begin(), towupper);
    return address;
}

bool Overlaps(
    const long firstStart,
    const long firstSpan,
    const long secondStart,
    const long secondSpan) {
    return firstStart <= secondStart + secondSpan - 1 &&
        secondStart <= firstStart + firstSpan - 1;
}

}

bool ParseCellAddress(
    const std::wstring& raw,
    long* const row,
    long* const column) noexcept {
    try {
        if (row == nullptr || column == nullptr) {
            return false;
        }
        const std::wstring address = Normalize(raw);
        unsigned long long parsedColumn = 0;
        size_t index = 0;
        while (index < address.size() && address[index] >= L'A' && address[index] <= L'Z') {
            parsedColumn = parsedColumn * 26 + static_cast<unsigned long long>(address[index] - L'A' + 1);
            ++index;
        }
        if (index == 0 || index == address.size()) {
            return false;
        }
        unsigned long long parsedRow = 0;
        while (index < address.size() && address[index] >= L'0' && address[index] <= L'9') {
            parsedRow = parsedRow * 10 + static_cast<unsigned long long>(address[index] - L'0');
            ++index;
        }
        const auto maximum = static_cast<unsigned long long>((std::numeric_limits<long>::max)());
        if (index != address.size() || parsedRow < 1 || parsedColumn < 1 ||
            parsedRow > maximum || parsedColumn > maximum) {
            return false;
        }
        *row = static_cast<long>(parsedRow);
        *column = static_cast<long>(parsedColumn);
        return true;
    } catch (...) {
        return false;
    }
}

bool ParseSelectedCellAddresses(
    const std::wstring& command,
    std::vector<std::wstring>* const addresses) noexcept {
    try {
        if (addresses == nullptr) {
            return false;
        }
        addresses->clear();
        std::set<std::wstring> observed;
        size_t index = 0;
        while (index < command.size()) {
            const wchar_t current = towupper(command[index]);
            if (current < L'A' || current > L'Z') {
                ++index;
                continue;
            }
            const size_t start = index;
            while (index < command.size()) {
                const wchar_t letter = towupper(command[index]);
                if (letter < L'A' || letter > L'Z') {
                    break;
                }
                ++index;
            }
            const size_t digitStart = index;
            while (index < command.size() &&
                   command[index] >= L'0' && command[index] <= L'9') {
                ++index;
            }
            if (digitStart == index) {
                continue;
            }
            std::wstring candidate = Normalize(command.substr(start, index - start));
            long row = 0;
            long column = 0;
            if (ParseCellAddress(candidate, &row, &column) &&
                observed.emplace(candidate).second) {
                addresses->push_back(std::move(candidate));
            }
        }
        return !addresses->empty();
    } catch (...) {
        if (addresses != nullptr) {
            addresses->clear();
        }
        return false;
    }
}

bool CellTopology::Build(
    std::vector<CellTopologyCell> cells,
    std::wstring* const error) noexcept {
    try {
        if (cells.empty()) {
            return Fail(error, L"table topology contains no cells");
        }
        std::map<std::wstring, size_t> byAddress;
        long rows = 0;
        long columns = 0;
        for (size_t index = 0; index < cells.size(); ++index) {
            CellTopologyCell& cell = cells[index];
            cell.address = Normalize(cell.address);
            cell.rightAddress = Normalize(cell.rightAddress);
            cell.downAddress = Normalize(cell.downAddress);
            if (!ParseCellAddress(cell.address, &cell.row, &cell.column) ||
                cell.rowSpan < 1 || cell.columnSpan < 1 ||
                !byAddress.emplace(cell.address, index).second) {
                return Fail(error, L"table topology has an invalid or duplicate cell");
            }
            const long long bottom = static_cast<long long>(cell.row) + cell.rowSpan - 1;
            const long long right = static_cast<long long>(cell.column) + cell.columnSpan - 1;
            if (bottom > (std::numeric_limits<long>::max)() ||
                right > (std::numeric_limits<long>::max)()) {
                return Fail(error, L"table topology dimensions overflow");
            }
            rows = (std::max)(rows, static_cast<long>(bottom));
            columns = (std::max)(columns, static_cast<long>(right));
        }
        const unsigned long long expectedSlots =
            static_cast<unsigned long long>(rows) * static_cast<unsigned long long>(columns);
        if (expectedSlots == 0 || expectedSlots > kMaximumPhysicalSlots) {
            return Fail(error, L"table topology physical grid exceeds the inspection limit");
        }

        std::map<std::pair<long, long>, size_t> owners;
        for (size_t index = 0; index < cells.size(); ++index) {
            const CellTopologyCell& cell = cells[index];
            for (long row = cell.row; row < cell.row + cell.rowSpan; ++row) {
                for (long column = cell.column; column < cell.column + cell.columnSpan; ++column) {
                    if (!owners.emplace(std::pair(row, column), index).second) {
                        return Fail(error, L"table topology cells overlap");
                    }
                }
            }
        }
        if (owners.size() != expectedSlots) {
            return Fail(error, L"table topology has a physical-grid gap");
        }

        for (const CellTopologyCell& cell : cells) {
            const auto validateNeighbor = [&](const std::wstring& address, const CellDirection direction) {
                if (address.empty()) {
                    return true;
                }
                const auto found = byAddress.find(address);
                if (found == byAddress.end() || address == cell.address) {
                    return false;
                }
                const CellTopologyCell& next = cells[found->second];
                if (direction == CellDirection::Right) {
                    return next.column > cell.column &&
                        Overlaps(cell.row, cell.rowSpan, next.row, next.rowSpan);
                }
                return next.row > cell.row &&
                    Overlaps(cell.column, cell.columnSpan, next.column, next.columnSpan);
            };
            if (!validateNeighbor(cell.rightAddress, CellDirection::Right) ||
                !validateNeighbor(cell.downAddress, CellDirection::Down)) {
                return Fail(error, L"table topology has an invalid physical neighbour");
            }
        }
        cells_ = std::move(cells);
        byAddress_ = std::move(byAddress);
        rows_ = rows;
        columns_ = columns;
        return true;
    } catch (...) {
        return Fail(error, L"table topology build failed unexpectedly");
    }
}

void CellTopology::Clear() noexcept {
    cells_.clear();
    byAddress_.clear();
    rows_ = 0;
    columns_ = 0;
}

bool CellTopology::Empty() const noexcept {
    return cells_.empty();
}

long CellTopology::Rows() const noexcept {
    return rows_;
}

long CellTopology::Columns() const noexcept {
    return columns_;
}

const CellTopologyCell* CellTopology::Find(const std::wstring& raw) const noexcept {
    try {
        const auto found = byAddress_.find(Normalize(raw));
        return found == byAddress_.end() ? nullptr : &cells_[found->second];
    } catch (...) {
        return nullptr;
    }
}

const std::vector<CellTopologyCell>& CellTopology::Cells() const noexcept {
    return cells_;
}

std::vector<std::wstring> CellTopology::IntersectingColumn(const long physicalColumn) const {
    std::vector<const CellTopologyCell*> matches;
    for (const CellTopologyCell& cell : cells_) {
        if (cell.column <= physicalColumn &&
            physicalColumn < cell.column + cell.columnSpan) {
            matches.push_back(&cell);
        }
    }
    std::sort(matches.begin(), matches.end(), [](const auto* const left, const auto* const right) {
        return std::pair(left->row, left->column) < std::pair(right->row, right->column);
    });
    std::vector<std::wstring> result;
    result.reserve(matches.size());
    for (const CellTopologyCell* const cell : matches) {
        result.push_back(cell->address);
    }
    return result;
}

bool CellTopology::PlanRectangularMerge(
    const std::wstring& first,
    const std::wstring& second,
    std::vector<CellTopologyStep>* const path,
    std::vector<std::wstring>* const region,
    std::wstring* const error) const noexcept {
    try {
        if (path == nullptr || region == nullptr) {
            return Fail(error, L"merge topology outputs are invalid");
        }
        path->clear();
        region->clear();
        const CellTopologyCell* const start = Find(first);
        const CellTopologyCell* const end = Find(second);
        if (start == nullptr || end == nullptr ||
            end->row < start->row || end->column < start->column) {
            return Fail(error, L"merge endpoints are not present in forward order");
        }
        const long bottom = end->row + end->rowSpan - 1;
        const long right = end->column + end->columnSpan - 1;
        const auto contained = [&](const CellTopologyCell& cell) {
            return cell.row >= start->row && cell.column >= start->column &&
                cell.row + cell.rowSpan - 1 <= bottom &&
                cell.column + cell.columnSpan - 1 <= right;
        };
        const auto intersects = [&](const CellTopologyCell& cell) {
            return cell.row <= bottom && start->row <= cell.row + cell.rowSpan - 1 &&
                cell.column <= right && start->column <= cell.column + cell.columnSpan - 1;
        };
        unsigned long long coveredSlots = 0;
        std::set<std::wstring> allowed;
        for (const CellTopologyCell& cell : cells_) {
            if (!intersects(cell)) {
                continue;
            }
            if (!contained(cell)) {
                return Fail(error, L"merge region is not a gap-free cell rectangle");
            }
            coveredSlots += static_cast<unsigned long long>(cell.rowSpan) *
                static_cast<unsigned long long>(cell.columnSpan);
            allowed.insert(cell.address);
            region->push_back(cell.address);
        }
        const unsigned long long expectedSlots =
            static_cast<unsigned long long>(bottom - start->row + 1) *
            static_cast<unsigned long long>(right - start->column + 1);
        if (coveredSlots != expectedSlots || allowed.size() < 2) {
            return Fail(error, L"merge region is not a gap-free cell rectangle");
        }

        struct Previous {
            std::wstring address;
            CellDirection direction;
        };
        std::queue<std::wstring> pending;
        std::map<std::wstring, Previous> previous;
        pending.push(start->address);
        previous.emplace(start->address, Previous{});
        while (!pending.empty() && previous.find(end->address) == previous.end()) {
            const std::wstring currentAddress = pending.front();
            pending.pop();
            const CellTopologyCell* const current = Find(currentAddress);
            if (current == nullptr) {
                return Fail(error, L"merge path references a missing cell");
            }
            for (const auto& edge : {
                     std::pair(CellDirection::Right, current->rightAddress),
                     std::pair(CellDirection::Down, current->downAddress)}) {
                if (edge.second.empty() || allowed.find(edge.second) == allowed.end() ||
                    previous.find(edge.second) != previous.end()) {
                    continue;
                }
                previous.emplace(edge.second, Previous{currentAddress, edge.first});
                pending.push(edge.second);
            }
        }
        if (previous.find(end->address) == previous.end()) {
            return Fail(error, L"merge endpoint has no actual right/down neighbour path");
        }
        std::vector<CellTopologyStep> reversed;
        for (std::wstring current = end->address; current != start->address;) {
            const Previous& step = previous.at(current);
            reversed.push_back(CellTopologyStep{step.direction, current});
            current = step.address;
        }
        path->assign(reversed.rbegin(), reversed.rend());
        return true;
    } catch (...) {
        return Fail(error, L"merge topology planning failed unexpectedly");
    }
}

bool CellTopology::PlanRectangularSelection(
    const long firstListId,
    const long secondListId,
    std::wstring* const firstAddress,
    std::wstring* const lastAddress,
    std::vector<CellTopologyStep>* const path,
    std::vector<std::wstring>* const region,
    std::wstring* const error) const noexcept {
    try {
        if (firstAddress == nullptr || lastAddress == nullptr ||
            path == nullptr || region == nullptr) {
            return Fail(error, L"selection topology outputs are invalid");
        }
        const CellTopologyCell* first = nullptr;
        const CellTopologyCell* second = nullptr;
        for (const CellTopologyCell& cell : cells_) {
            if (cell.listId == firstListId) {
                if (first != nullptr) {
                    return Fail(error, L"selection start list id is ambiguous");
                }
                first = &cell;
            }
            if (cell.listId == secondListId) {
                if (second != nullptr) {
                    return Fail(error, L"selection end list id is ambiguous");
                }
                second = &cell;
            }
        }
        if (first == nullptr || second == nullptr) {
            return Fail(error, L"selection endpoints are not table cells");
        }
        const long top = (std::min)(first->row, second->row);
        const long left = (std::min)(first->column, second->column);
        const long bottom = (std::max)(
            first->row + first->rowSpan - 1,
            second->row + second->rowSpan - 1);
        const long right = (std::max)(
            first->column + first->columnSpan - 1,
            second->column + second->columnSpan - 1);
        const CellTopologyCell* upperLeft = nullptr;
        const CellTopologyCell* lowerRight = nullptr;
        for (const CellTopologyCell& cell : cells_) {
            if (cell.row <= top && top < cell.row + cell.rowSpan &&
                cell.column <= left && left < cell.column + cell.columnSpan) {
                upperLeft = &cell;
            }
            if (cell.row <= bottom && bottom < cell.row + cell.rowSpan &&
                cell.column <= right && right < cell.column + cell.columnSpan) {
                lowerRight = &cell;
            }
        }
        if (upperLeft == nullptr || lowerRight == nullptr) {
            return Fail(error, L"selection rectangle corners are not covered by cells");
        }
        *firstAddress = upperLeft->address;
        *lastAddress = lowerRight->address;
        if (upperLeft == lowerRight) {
            path->clear();
            region->assign(1, upperLeft->address);
            return true;
        }
        return PlanRectangularMerge(
            upperLeft->address,
            lowerRight->address,
            path,
            region,
            error);
    } catch (...) {
        return Fail(error, L"selection topology planning failed unexpectedly");
    }
}

bool CellTopology::PlanRectangularSelection(
    const std::vector<std::wstring>& selectedAddresses,
    std::wstring* const firstAddress,
    std::wstring* const lastAddress,
    std::vector<CellTopologyStep>* const path,
    std::vector<std::wstring>* const region,
    std::wstring* const error) const noexcept {
    try {
        if (selectedAddresses.empty()) {
            return Fail(error, L"selection contains no physical cell addresses");
        }
        long top = (std::numeric_limits<long>::max)();
        long left = (std::numeric_limits<long>::max)();
        long bottom = 0;
        long right = 0;
        for (const std::wstring& address : selectedAddresses) {
            const CellTopologyCell* const cell = Find(address);
            if (cell == nullptr) {
                return Fail(error, L"selection address is not a physical table cell");
            }
            top = (std::min)(top, cell->row);
            left = (std::min)(left, cell->column);
            bottom = (std::max)(bottom, cell->row + cell->rowSpan - 1);
            right = (std::max)(right, cell->column + cell->columnSpan - 1);
        }
        const CellTopologyCell* upperLeft = nullptr;
        const CellTopologyCell* lowerRight = nullptr;
        for (const CellTopologyCell& cell : cells_) {
            if (cell.row <= top && top < cell.row + cell.rowSpan &&
                cell.column <= left && left < cell.column + cell.columnSpan) {
                upperLeft = &cell;
            }
            if (cell.row <= bottom && bottom < cell.row + cell.rowSpan &&
                cell.column <= right && right < cell.column + cell.columnSpan) {
                lowerRight = &cell;
            }
        }
        if (upperLeft == nullptr || lowerRight == nullptr) {
            return Fail(error, L"selection rectangle corners are unavailable");
        }
        return PlanRectangularSelection(
            upperLeft->listId,
            lowerRight->listId,
            firstAddress,
            lastAddress,
            path,
            region,
            error);
    } catch (...) {
        return Fail(error, L"address selection topology planning failed unexpectedly");
    }
}

}

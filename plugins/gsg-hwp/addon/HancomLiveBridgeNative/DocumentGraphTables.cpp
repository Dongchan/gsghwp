#include "DocumentGraphTables.h"

#include <algorithm>
#include <limits>
#include <new>
#include <map>
#include <utility>

namespace hancom::graph::tables {
namespace {

bool Overlaps(
    const PhysicalCellInterval& first,
    const PhysicalCellInterval& second) noexcept {
    return first.rowBegin1 < second.rowEnd1 &&
        second.rowBegin1 < first.rowEnd1 &&
        first.columnBegin1 < second.columnEnd1 &&
        second.columnBegin1 < first.columnEnd1;
}

IntegerObservation ObserveInteger(const long value) noexcept {
    if (value < 0) {
        return {CellObservationState::NotExposed, 0};
    }
    return {CellObservationState::Value, value};
}

TextObservation ObserveText(const std::wstring& value) {
    if (value.empty()) {
        return {CellObservationState::NotExposed, {}};
    }
    return {CellObservationState::Value, value};
}

bool Fail(std::wstring* const error, const wchar_t* const message) noexcept {
    if (error != nullptr) {
        try {
            *error = message;
        } catch (...) {
        }
    }
    return false;
}

}

BuildStatus BuildTableGraphTopology(
    const hancom::inspection::CellTopology& topology,
    const bool repeatHeaderExposed,
    const bool repeatHeader,
    const std::wstring& sessionInstanceId,
    const std::vector<HeaderCellObservation>& headerObservations,
    const std::vector<CellAppearanceObservation>& appearanceObservations,
    TableGraphRecord* const output) noexcept {
    if (output == nullptr || topology.Empty() ||
        topology.Rows() <= 0 || topology.Columns() <= 0) {
        return BuildStatus::InvalidArgument;
    }
    try {
        std::map<std::wstring, bool> headers;
        for (const HeaderCellObservation& observation : headerObservations) {
            if (!headers.emplace(
                    observation.address,
                    observation.header).second) {
                return BuildStatus::InvalidTopology;
            }
        }
        std::map<std::wstring, CellAppearanceObservation> appearances;
        for (const CellAppearanceObservation& observation :
             appearanceObservations) {
            if (observation.address.empty() ||
                !appearances.emplace(
                    observation.address,
                    observation).second) {
                return BuildStatus::InvalidTopology;
            }
        }
        TableGraphRecord record;
        record.sessionInstanceId = sessionInstanceId;
        record.rowCount = topology.Rows();
        record.columnCount = topology.Columns();
        record.repeatHeaderExposed = repeatHeaderExposed;
        record.repeatHeader = repeatHeader;
        record.physicalCells.reserve(topology.Cells().size());
        record.physicalIntervals.reserve(topology.Cells().size());
        std::uint64_t coveredSlots = 0;
        for (const auto& source : topology.Cells()) {
            if (source.tableInstanceId != sessionInstanceId ||
                source.row <= 0 || source.column <= 0 ||
                source.rowSpan <= 0 || source.columnSpan <= 0 ||
                source.pageStart <= 0 || source.pageEnd < source.pageStart) {
                return BuildStatus::InvalidTopology;
            }
            const auto header = headers.find(source.address);
            const auto appearance = appearances.find(source.address);
            if (appearance == appearances.end()) {
                return BuildStatus::InvalidTopology;
            }
            TableCellRecord cell;
            cell.address = source.address;
            cell.listId = source.listId;
            cell.row = source.row;
            cell.column = source.column;
            cell.rowSpan = source.rowSpan;
            cell.columnSpan = source.columnSpan;
            cell.width = source.width;
            cell.height = source.height;
            cell.pageStart = source.pageStart;
            cell.pageEnd = source.pageEnd;
            cell.text = source.text;
            cell.appearance = appearance->second;
            cell.headerExposed = header != headers.end();
            cell.header = header != headers.end() && header->second;
            if (header != headers.end()) {
                headers.erase(header);
            }
            appearances.erase(appearance);
            record.physicalCells.push_back(std::move(cell));
            PhysicalCellInterval interval;
            interval.ownerAddress = source.address;
            interval.rowBegin1 = source.row;
            interval.rowEnd1 = source.row + source.rowSpan;
            interval.columnBegin1 = source.column;
            interval.columnEnd1 = source.column + source.columnSpan;
            for (const PhysicalCellInterval& existing :
                 record.physicalIntervals) {
                if (Overlaps(existing, interval)) {
                    return BuildStatus::InvalidTopology;
                }
            }
            const std::uint64_t cellSlots =
                static_cast<std::uint64_t>(source.rowSpan) *
                static_cast<std::uint64_t>(source.columnSpan);
            if (coveredSlots >
                (std::numeric_limits<std::uint64_t>::max)() - cellSlots) {
                return BuildStatus::InvalidTopology;
            }
            coveredSlots += cellSlots;
            record.physicalIntervals.push_back(std::move(interval));
        }
        if (!headers.empty() || !appearances.empty()) {
            return BuildStatus::InvalidTopology;
        }
        const std::uint64_t expectedSlots =
            static_cast<std::uint64_t>(record.rowCount) *
            static_cast<std::uint64_t>(record.columnCount);
        if (coveredSlots != expectedSlots) {
            return BuildStatus::InvalidTopology;
        }
        *output = std::move(record);
        return BuildStatus::Complete;
    } catch (const std::bad_alloc&) {
        return BuildStatus::ResourceExhausted;
    } catch (...) {
        return BuildStatus::InvalidTopology;
    }
}

BuildStatus AdaptCompleteCellAppearances(
    const hancom::inspection::CellTopology& topology,
    const std::vector<hancom::inspection::TableCellFormat>& formats,
    std::vector<CellAppearanceObservation>* const output) noexcept {
    if (output == nullptr || topology.Empty()) {
        return BuildStatus::InvalidArgument;
    }
    try {
        std::map<std::wstring, CellAppearanceObservation> observations;
        for (const hancom::inspection::TableCellFormat& format : formats) {
            for (const std::wstring& address : format.addresses) {
                const auto* const cell = topology.Find(address);
                if (cell == nullptr ||
                    format.tableInstanceId != cell->tableInstanceId) {
                    return BuildStatus::InvalidTopology;
                }
                CellAppearanceObservation observation;
                observation.address = address;
                observation.fillColor = ObserveInteger(format.fillColor);
                observation.fillHatchColor =
                    ObserveInteger(format.fillHatchColor);
                observation.fillAlpha = ObserveInteger(format.fillAlpha);
                observation.fillBrush = ObserveInteger(format.fillBrush);
                observation.marginLeft = ObserveInteger(format.marginLeft);
                observation.marginRight = ObserveInteger(format.marginRight);
                observation.marginTop = ObserveInteger(format.marginTop);
                observation.marginBottom = ObserveInteger(format.marginBottom);
                observation.verticalAlign =
                    ObserveInteger(format.verticalAlign);
                observation.alignment = ObserveInteger(format.alignment);
                observation.faceName = ObserveText(format.faceName);
                observation.characterHeight =
                    ObserveInteger(format.characterHeight);
                observation.bold = ObserveInteger(format.bold);
                for (size_t side = 0;
                     side < hancom::inspection::kCellBorderSideCount;
                     ++side) {
                    observation.borderType[side] =
                        ObserveInteger(format.borderType[side]);
                    observation.borderWidth[side] =
                        ObserveInteger(format.borderWidth[side]);
                    observation.borderColor[side] =
                        ObserveInteger(format.borderColor[side]);
                }
                if (!observations.emplace(
                        address,
                        std::move(observation)).second) {
                    return BuildStatus::InvalidTopology;
                }
            }
        }
        if (observations.size() != topology.Cells().size()) {
            return BuildStatus::InvalidTopology;
        }
        std::vector<CellAppearanceObservation> result;
        result.reserve(topology.Cells().size());
        for (const hancom::inspection::CellTopologyCell& cell :
             topology.Cells()) {
            const auto found = observations.find(cell.address);
            if (found == observations.end()) {
                return BuildStatus::InvalidTopology;
            }
            result.push_back(found->second);
        }
        *output = std::move(result);
        return BuildStatus::Complete;
    } catch (const std::bad_alloc&) {
        return BuildStatus::ResourceExhausted;
    } catch (...) {
        return BuildStatus::InvalidTopology;
    }
}

BuildStatus CaptureTableGraphFromNativeWithDimensions(
    IDispatch* const hwp,
    const std::wstring& sessionInstanceId,
    const bool repeatHeaderExposed,
    const bool repeatHeader,
    const std::int32_t authoritativeRows,
    const std::int32_t authoritativeColumns,
    const std::vector<HeaderCellObservation>& headerObservations,
    TableGraphRecord* const output,
    std::wstring* const error,
    const hancom::inspection::TableDispatchTypeContext* const dispatchTypes) noexcept {
    if (hwp == nullptr || output == nullptr) {
        static_cast<void>(Fail(error, L"table graph input is invalid"));
        return BuildStatus::InvalidArgument;
    }
    if (error != nullptr) {
        error->clear();
    }
    std::vector<hancom::inspection::TableCellRecord> cells;
    std::vector<hancom::inspection::TableCellFormat> formats;
    if (!hancom::inspection::
            InspectTableCellsAndFormatsWithAuthoritativeDimensions(
                hwp,
                sessionInstanceId,
                authoritativeRows,
                authoritativeColumns,
                &cells,
                &formats,
                error,
                nullptr,
                dispatchTypes)) {
        return BuildStatus::SourceFailed;
    }
    hancom::inspection::CellTopology topology;
    if (!topology.Build(cells, error)) {
        return BuildStatus::InvalidTopology;
    }
    std::vector<CellAppearanceObservation> appearances;
    const BuildStatus adapted =
        AdaptCompleteCellAppearances(topology, formats, &appearances);
    if (adapted != BuildStatus::Complete) {
        static_cast<void>(Fail(
            error,
            L"not every physical cell returned a complete appearance observation"));
        return adapted == BuildStatus::ResourceExhausted
            ? adapted
            : BuildStatus::SourceFailed;
    }
    return BuildTableGraphTopology(
        topology,
        repeatHeaderExposed,
        repeatHeader,
        sessionInstanceId,
        headerObservations,
        appearances,
        output);
}

BuildStatus CaptureTableGraphFromNative(
    IDispatch* const hwp,
    const std::wstring& sessionInstanceId,
    const bool repeatHeaderExposed,
    const bool repeatHeader,
    const std::vector<HeaderCellObservation>& headerObservations,
    TableGraphRecord* const output,
    std::wstring* const error) noexcept {
    return CaptureTableGraphFromNativeWithDimensions(
        hwp, sessionInstanceId, repeatHeaderExposed, repeatHeader, 0, 0,
        headerObservations, output, error);
}

BuildStatus AttachNestedTables(
    const std::vector<NestedTableObservation>& observations,
    TableGraphRecord* const table) noexcept {
    if (table == nullptr || table->physicalCells.empty()) {
        return BuildStatus::InvalidArgument;
    }
    try {
        std::map<std::wstring, bool> sessionIds;
        std::vector<NestedTableRecord> nestedTables;
        nestedTables.reserve(observations.size());
        for (const NestedTableObservation& observation : observations) {
            if (observation.sessionInstanceId.empty() ||
                observation.anchorListId <= 0 ||
                !sessionIds.emplace(
                    observation.sessionInstanceId,
                    true).second) {
                return BuildStatus::InvalidTopology;
            }
            std::wstring hostAddress;
            const BuildStatus resolved = ResolveNestedTableHost(
                *table,
                observation.anchorListId,
                &hostAddress);
            if (resolved != BuildStatus::Complete) {
                return resolved;
            }
            nestedTables.push_back({
                observation.sessionInstanceId,
                observation.anchorListId,
                std::move(hostAddress),
            });
        }
        table->nestedTables = std::move(nestedTables);
        return BuildStatus::Complete;
    } catch (const std::bad_alloc&) {
        return BuildStatus::ResourceExhausted;
    } catch (...) {
        return BuildStatus::InvalidTopology;
    }
}

BuildStatus ResolveNestedTableHost(
    const TableGraphRecord& outer,
    const std::int32_t nestedAnchorList,
    std::wstring* const hostAddress) noexcept {
    if (hostAddress == nullptr) {
        return BuildStatus::InvalidArgument;
    }
    const TableCellRecord* match = nullptr;
    for (const TableCellRecord& cell : outer.physicalCells) {
        if (cell.listId != nestedAnchorList) {
            continue;
        }
        if (match != nullptr) {
            return BuildStatus::AmbiguousNestedHost;
        }
        match = &cell;
    }
    if (match == nullptr) {
        return BuildStatus::InvalidTopology;
    }
    try {
        *hostAddress = match->address;
        return BuildStatus::Complete;
    } catch (const std::bad_alloc&) {
        return BuildStatus::ResourceExhausted;
    }
}

} // namespace hancom::graph::tables

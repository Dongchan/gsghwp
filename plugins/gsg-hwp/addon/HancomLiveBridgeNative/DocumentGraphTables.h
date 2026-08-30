#pragma once

#include "TableInspection.h"

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace hancom::graph::tables {

enum class BuildStatus : std::uint8_t {
    Complete = 0,
    InvalidArgument,
    InvalidTopology,
    SourceFailed,
    AmbiguousNestedHost,
    ResourceExhausted,
};

enum class CellObservationState : std::uint8_t {
    Value = 0,
    NotExposed,
    ReadFailed,
};

struct IntegerObservation final {
    CellObservationState state = CellObservationState::NotExposed;
    std::int64_t value = 0;
};

struct TextObservation final {
    CellObservationState state = CellObservationState::NotExposed;
    std::wstring value{};
};

struct CellAppearanceObservation final {
    std::wstring address{};
    IntegerObservation fillColor{};
    IntegerObservation fillHatchColor{};
    IntegerObservation fillAlpha{};
    IntegerObservation fillBrush{};
    IntegerObservation marginLeft{};
    IntegerObservation marginRight{};
    IntegerObservation marginTop{};
    IntegerObservation marginBottom{};
    IntegerObservation verticalAlign{};
    IntegerObservation alignment{};
    TextObservation faceName{};
    IntegerObservation characterHeight{};
    IntegerObservation bold{};
    std::array<IntegerObservation, 4> borderType{};
    std::array<IntegerObservation, 4> borderWidth{};
    std::array<IntegerObservation, 4> borderColor{};
};

struct TableCellRecord final {
    std::wstring address{};
    std::int32_t listId = 0;
    std::int32_t row = 0;
    std::int32_t column = 0;
    std::int32_t rowSpan = 1;
    std::int32_t columnSpan = 1;
    std::int32_t width = -1;
    std::int32_t height = -1;
    std::int32_t pageStart = 1;
    std::int32_t pageEnd = 1;
    std::wstring text{};
    CellObservationState textRunsState = CellObservationState::NotExposed;
    CellAppearanceObservation appearance{};
    bool headerExposed = false;
    bool header = false;
};

// Coordinates are one-based. The end coordinates are exclusive, allowing one
// record per physical owner cell without expanding a rows-by-columns slot grid.
struct PhysicalCellInterval final {
    std::wstring ownerAddress{};
    std::int32_t rowBegin1 = 0;
    std::int32_t rowEnd1 = 0;
    std::int32_t columnBegin1 = 0;
    std::int32_t columnEnd1 = 0;
};

struct NestedTableObservation final {
    std::wstring sessionInstanceId{};
    std::int32_t anchorListId = 0;
};

struct NestedTableRecord final {
    std::wstring sessionInstanceId{};
    std::int32_t anchorListId = 0;
    std::wstring hostAddress{};
};

struct TableGraphRecord final {
    std::wstring sessionInstanceId{};
    std::int32_t rowCount = 0;
    std::int32_t columnCount = 0;
    bool repeatHeaderExposed = false;
    bool repeatHeader = false;
    std::vector<TableCellRecord> physicalCells{};
    std::vector<PhysicalCellInterval> physicalIntervals{};
    std::vector<NestedTableRecord> nestedTables{};
};

struct HeaderCellObservation final {
    std::wstring address{};
    bool header = false;
};

BuildStatus BuildTableGraphTopology(
    const hancom::inspection::CellTopology& topology,
    bool repeatHeaderExposed,
    bool repeatHeader,
    const std::wstring& sessionInstanceId,
    const std::vector<HeaderCellObservation>& headerObservations,
    const std::vector<CellAppearanceObservation>& appearanceObservations,
    TableGraphRecord* output) noexcept;

BuildStatus AdaptCompleteCellAppearances(
    const hancom::inspection::CellTopology& topology,
    const std::vector<hancom::inspection::TableCellFormat>& formats,
    std::vector<CellAppearanceObservation>* output) noexcept;

BuildStatus CaptureTableGraphFromNative(
    IDispatch* hwp,
    const std::wstring& sessionInstanceId,
    bool repeatHeaderExposed,
    bool repeatHeader,
    const std::vector<HeaderCellObservation>& headerObservations,
    TableGraphRecord* output,
    std::wstring* error) noexcept;

BuildStatus CaptureTableGraphFromNativeWithDimensions(
    IDispatch* hwp,
    const std::wstring& sessionInstanceId,
    bool repeatHeaderExposed,
    bool repeatHeader,
    std::int32_t authoritativeRows,
    std::int32_t authoritativeColumns,
    const std::vector<HeaderCellObservation>& headerObservations,
    TableGraphRecord* output,
    std::wstring* error,
    const hancom::inspection::TableDispatchTypeContext* dispatchTypes = nullptr) noexcept;

BuildStatus AttachNestedTables(
    const std::vector<NestedTableObservation>& observations,
    TableGraphRecord* table) noexcept;

BuildStatus ResolveNestedTableHost(
    const TableGraphRecord& outer,
    std::int32_t nestedAnchorList,
    std::wstring* hostAddress) noexcept;

} // namespace hancom::graph::tables

#pragma once

#include "CellTopology.h"
#include "DispatchInvoke.h"

#include <Windows.h>
#include <oaidl.h>

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace hancom::inspection {

using TableCellRecord = CellTopologyCell;

struct TableDispatchTypeContext final {
    dispatch::TypeIdentityToken root{};
    dispatch::TypeIdentityToken control{};
    dispatch::TypeIdentityToken table{};
    dispatch::TypeIdentityToken cell{};
    dispatch::TypeIdentityToken range{};
    dispatch::TypeIdentityToken parameterSet{};
    dispatch::TypeIdentityToken action{};
    dispatch::TypeIdentityToken documents{};
    dispatch::TypeIdentityToken document{};
    dispatch::TypeIdentityToken documentInfo{};
    SYSKIND systemKind = SYS_WIN32;
};

struct TableCallCounters final {
    std::uint64_t headCtrl = 0;
    std::uint64_t next = 0;
    std::uint64_t directVirtualPropertyGets = 0;
    std::uint64_t directRange = 0;
    std::uint64_t fallback = 0;
    std::uint64_t exactCover = 0;
    std::uint64_t exactCoverDimensionUnavailable = 0;
    std::uint64_t exactCoverDimensionInvalid = 0;
    std::uint64_t exactCoverAmbiguous = 0;
    std::uint64_t dimensionProperties = 0;
    std::uint64_t dimensionCaret = 0;
    std::uint64_t dimensionAction = 0;
    std::uint64_t dimensionBindFailed = 0;
    std::uint64_t dimensionIdentityFailed = 0;
    std::uint64_t dimensionReadFailed = 0;
    std::uint64_t moveToCellFailed = 0;
    std::uint64_t rangeNotExposed = 0;
    std::uint64_t rangeInvokeFailed = 0;
    std::uint64_t rangeResultInvalid = 0;
    std::uint64_t rangeMemberReadFailed = 0;
    std::uint64_t rangeBoundsInvalid = 0;
    std::uint64_t rangeCoordinateBaseMismatch = 0;
    std::uint64_t rangeOwnerMismatch = 0;
    std::uint64_t rangeTopologyRejected = 0;
    std::uint64_t appearanceVisits = 0;
    std::uint64_t appearanceSecondPass = 0;
};
void ResetTableCallCounters() noexcept;
void ResetTableVirtualSlotCache() noexcept;
void NoteTableHeadCtrlCall() noexcept;
void NoteTableNextCall() noexcept;
void NoteTableDirectVirtualPropertyGet() noexcept;
TableCallCounters ReadTableCallCounters() noexcept;

struct TableRangeProbeTuple final {
    std::wstring expectedAddress{};
    std::wstring postMoveAddress{};
    long expectedRow = 0;
    long expectedColumn = 0;
    long moveBase = -1;
    long boundsBase = -1;
    bool selectCell = false;
    long status = -1;
    long moveResultVt = VT_EMPTY;
    long rangeHresult = E_UNEXPECTED;
    long rangeResultVt = VT_EMPTY;
    bool rangeDispatchNull = true;
    long postMoveListId = -1;
    long startRow = 0;
    long endRow = 0;
    long startColumn = 0;
    long endColumn = 0;
};
void ResetTableRangeProbeTuples() noexcept;
std::vector<TableRangeProbeTuple> ReadTableRangeProbeTuples() noexcept;

enum class NativeObservationState : std::uint8_t {
    Value = 0,
    NotExposed,
    ReadFailed,
};

struct TableCaptionObservation final {
    NativeObservationState presenceState = NativeObservationState::NotExposed;
    bool exists = false;
    NativeObservationState textState = NativeObservationState::NotExposed;
    std::wstring text{};
    NativeObservationState styleIdState = NativeObservationState::NotExposed;
    long styleId = -1;
    NativeObservationState styleNameState = NativeObservationState::NotExposed;
    std::wstring styleName{};
    NativeObservationState pageStartState = NativeObservationState::NotExposed;
    long pageStart = 0;
    NativeObservationState pageEndState = NativeObservationState::NotExposed;
    long pageEnd = 0;
    long listId = 0;
    long paragraph = 0;
    long character = 0;
};

// The four cell sides, in the order every border array below carries them and
// in the order the wire line writes them. size_t rather than an enum so that
// indexing and loop counters never mix signedness (the build is /W4 /WX).
inline constexpr size_t kCellBorderLeft = 0;
inline constexpr size_t kCellBorderRight = 1;
inline constexpr size_t kCellBorderTop = 2;
inline constexpr size_t kCellBorderBottom = 3;
inline constexpr size_t kCellBorderSideCount = 4;

// How some cells of an existing table actually look. Every field is -1 (or
// empty for text) when this HWP build did not report it: absent is not zero.
// A fill color of 0 is black and a fill color nobody could read are different
// answers, and only the first one may be replayed onto a new table.
//
// "addresses" holds every sampled cell that reported exactly these values, so
// a table whose sampled cells all look alike costs one record instead of four.
// Collapsing equal observations is not a rule about the table: two cells share
// a record only because the document gave them the same numbers.
//
// No field here is a judgement. There is no "is a header row" flag on purpose:
// the caller sees the addresses and the values the document carries, and
// decides for itself. Guessing intent from a gray fill is how the earlier
// pattern lists got it wrong.
struct TableCellFormat {
    std::wstring tableInstanceId;
    std::vector<std::wstring> addresses;
    long fillColor = -1;
    long fillHatchColor = -1;
    long fillAlpha = -1;
    long fillBrush = -1;
    long borderType[kCellBorderSideCount] = {-1, -1, -1, -1};
    long borderWidth[kCellBorderSideCount] = {-1, -1, -1, -1};
    long borderColor[kCellBorderSideCount] = {-1, -1, -1, -1};
    long marginLeft = -1;
    long marginRight = -1;
    long marginTop = -1;
    long marginBottom = -1;
    long verticalAlign = -1;
    long alignment = -1;
    std::wstring faceName;
    long characterHeight = -1;
    long bold = -1;
};

// Which cells of an inspected table answer for its appearance.
//
// Position only, never meaning: a bounded cross-product of first, early,
// middle, and last rows with first, middle, and last columns. Rows and columns
// are counted the way CellTopology::Build fills them, which is one based --
// "A1" is row 1 column 1, not row 0 column 0. A merged cell answers through
// its owner and duplicates collapse. Exposed so the smoke test can run the
// real function on real topology output instead of a copy of it.
std::vector<const TableCellRecord*> SampleCells(
    const std::vector<TableCellRecord>& cells);

// Reads the appearance of a few cells of one already inspected table.
//
// Sampling, not policy. The addresses are chosen by position alone -- see
// SampleCells above -- so a table whose first row is not a header still
// reports what its first row really carries. Merged cells report their owner.
// Reading every cell would multiply an inspection response by the cell count,
// which is why this stops at twelve cells per table.
//
// Never fails the surrounding inspection: a cell whose caret could not be
// reached is skipped, and a property this build does not expose stays -1.
// The caret is moved and left in the table; the caller restores it.
//
// "error" says why the result is empty, so that a caller can tell a table
// that carries no readable appearance from a table nobody could read. It is
// cleared on entry and stays empty when at least one cell answered.
std::vector<TableCellFormat> ReadTableCellFormats(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    const std::vector<TableCellRecord>& cells,
    std::wstring* error = nullptr) noexcept;

// Graph capture path: reads every physical owner cell before equivalent
// appearances are deduplicated. Unlike the legacy inspection view above, this
// is not a positional sample.
std::vector<TableCellFormat> ReadAllTableCellFormats(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    const std::vector<TableCellRecord>& cells,
    std::wstring* error = nullptr) noexcept;

bool ReadCurrentListText(
    IDispatch* hwp,
    std::wstring* text) noexcept;

bool InspectTableCaption(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    TableCaptionObservation* caption,
    std::wstring* error = nullptr) noexcept;

bool ReadSelectedCellAddresses(
    IDispatch* hwp,
    std::vector<std::wstring>* addresses,
    std::wstring* error) noexcept;

bool SelectTableControl(
    IDispatch* hwp,
    const std::wstring& tableInstanceId) noexcept;

// Reads dimensions from the exact authoritative table dispatch. If its
// Properties surface is absent, binds that exact owner and uses the qualified
// caret GetRowColCount surface. Set authoritativePropertiesAlreadyProbed only
// when the caller has just made the same exact-owner Properties/RowCount/
// ColCount probe; this prevents a duplicate COM traversal before the caret
// fallback. Graph capture never calls the UI-mutating
// TablePropertyDialog.GetDefault dimension route.
bool ReadAuthoritativeTableDimensions(
    IDispatch* hwp,
    IDispatch* authoritativeTable,
    const std::wstring& tableInstanceId,
    bool instanceIdUniquelyQualified,
    long* rows,
    long* columns,
    bool authoritativePropertiesAlreadyProbed = false,
    const TableDispatchTypeContext* dispatchTypes = nullptr) noexcept;

bool InspectTableCells(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    std::vector<TableCellRecord>* cells,
    std::wstring* error,
    std::wstring* errorCode = nullptr) noexcept;

// Graph-reader path with dimensions observed from the exact authoritative
// table dispatch before caret navigation. Zero dimensions retain the ordinary
// qualified acquisition and strict fallback behavior.
bool InspectTableCellsWithAuthoritativeDimensions(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    long authoritativeRows,
    long authoritativeColumns,
    std::vector<TableCellRecord>* cells,
    std::wstring* error,
    std::wstring* errorCode = nullptr) noexcept;

// Graph capture variant. Appearance is acquired while each exact physical
// owner is already current, so no second SetPos sweep is needed.
bool InspectTableCellsAndFormatsWithAuthoritativeDimensions(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    long authoritativeRows,
    long authoritativeColumns,
    std::vector<TableCellRecord>* cells,
    std::vector<TableCellFormat>* formats,
    std::wstring* error,
    std::wstring* errorCode = nullptr,
    const TableDispatchTypeContext* dispatchTypes = nullptr) noexcept;

bool InspectTableTopology(
    IDispatch* hwp,
    const std::wstring& tableInstanceId,
    CellTopology* topology,
    std::wstring* error) noexcept;

}

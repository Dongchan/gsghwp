#pragma once

#include <Windows.h>
#include <oaidl.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <string>
#include <vector>

namespace hancom::com_state {

struct Position {
    LONG list = 0;
    LONG paragraph = 0;
    LONG character = 0;
};

struct DocumentRoute {
    LONG documentId = 0;
    CComPtr<IDispatch> documents{};
    CComPtr<IDispatch> document{};
};

struct Selection {
    bool selected = false;
    LONG mode = 0;
    Position start;
    Position end;
    std::wstring controlType;
    std::wstring controlInstance;
    bool controlInstancePresent = false;
    std::vector<std::wstring> cellAddresses;
    std::wstring cellAddressError;
};

inline constexpr LONG kSelectionModeMask = 0x0F;
inline constexpr LONG kSelectionNone = 0;
inline constexpr LONG kSelectionText = 1;
inline constexpr LONG kSelectionCells = 3;
inline constexpr LONG kSelectionControl = 4;
inline constexpr LONG kSelectionStrict = 0x10;

enum class EmptyPositionResult { Reject, TreatAsSuccess };

struct PositionResult {
    HRESULT invokeStatus = E_UNEXPECTED;
    HRESULT conversionStatus = E_UNEXPECTED;
    bool positioned = false;
};

enum class SelectionCapturePolicy {
    Basic,
    BestEffortControl,
    RequiredControl,
};

enum class SelectionCaptureStage {
    None,
    SelectionMode,
    CreateSet,
    SelectedPositions,
    PositionItem,
    Control,
};

struct SelectionCaptureFailure {
    SelectionCaptureStage stage = SelectionCaptureStage::None;
    HRESULT status = S_OK;
    const wchar_t* detail = L"";
};

bool CaptureDocumentRoute(IDispatch* hwp, DocumentRoute* route) noexcept;
bool RestoreDocumentRoute(
    IDispatch* hwp,
    const DocumentRoute& route) noexcept;
bool VerifyDocumentRoute(
    IDispatch* hwp,
    const DocumentRoute& route) noexcept;

HRESULT CapturePosition(IDispatch* hwp, Position* position);
PositionResult ApplyPosition(
    IDispatch* hwp,
    const Position& position,
    EmptyPositionResult emptyResult);
bool SamePosition(const Position& left, const Position& right) noexcept;
bool ReadCurrentCellAddress(
    IDispatch* hwp,
    std::wstring* address) noexcept;
bool ReadSelectionMode(IDispatch* hwp, LONG* mode) noexcept;

bool CaptureSelection(
    IDispatch* hwp,
    Selection* selection,
    SelectionCapturePolicy policy,
    SelectionCaptureFailure* failure);
bool CanRestoreSelection(const Selection& selection) noexcept;
bool RestoreSelection(
    IDispatch* hwp,
    const Position& cursor,
    const Selection& selection);
bool CollapseSelectionToCursor(
    IDispatch* hwp,
    const Position& cursor);

// The CellBorderFill action reads SelCellsBorderFill, not the caret's cell.
// Qualify the already-positioned caret as exactly one selected cell and verify
// both the native address and selection mode before any property read.
bool QualifyCurrentTableCellSelection(
    IDispatch* hwp,
    const std::wstring& expectedAddress);
bool RestoreSelectionAfterMerge(
    IDispatch* hwp,
    const Position& cursor,
    const Selection& selection,
    const std::wstring& mergedOwner,
    bool ownerFallbackAllowed);
bool SameSelection(const Selection& left, const Selection& right) noexcept;

}

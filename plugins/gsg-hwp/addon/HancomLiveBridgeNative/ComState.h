#pragma once

#include <Windows.h>
#include <oaidl.h>

#include <string>
#include <vector>

namespace hancom::com_state {

struct Position {
    LONG list = 0;
    LONG paragraph = 0;
    LONG character = 0;
};

struct Selection {
    bool selected = false;
    LONG mode = 0;
    Position start;
    Position end;
    std::wstring controlType;
    std::wstring controlInstance;
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

HRESULT CapturePosition(IDispatch* hwp, Position* position);
PositionResult ApplyPosition(
    IDispatch* hwp,
    const Position& position,
    EmptyPositionResult emptyResult);
bool SamePosition(const Position& left, const Position& right) noexcept;

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
bool SameSelection(const Selection& left, const Selection& right) noexcept;

}

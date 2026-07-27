#pragma once

#include "ActionProtocol.h"
#include "ReferenceLayoutSpec.h"

namespace hancom::reference_layout {

class Host {
public:
    virtual ~Host() = default;
    virtual bool Fail(const actions::Error& error) = 0;
    virtual bool ExecuteParameter(const actions::Command& command) = 0;
    virtual bool CaptureCreatedTable() = 0;
    virtual bool CaptureExistingTable(const std::wstring& controlId) = 0;
    virtual bool ResizeColumn(LONG column, LONG width) = 0;
    virtual bool ResizeRow(LONG row, LONG height) = 0;
    virtual bool SelectRegion(
        LONG top,
        LONG left,
        LONG bottom,
        LONG right) = 0;
    virtual bool GoToCell(LONG row, LONG column) = 0;
    virtual bool Run(const wchar_t* action, const std::wstring& location) = 0;
    virtual bool InsertText(const Text& text, const Style* expectedStyle) = 0;
    virtual bool MergeCells(const Merge& merge) = 0;
    virtual bool ReconcileFinalGeometry(const Spec& spec) = 0;
    virtual bool VerifyFinalTopology(const Spec& spec) = 0;
    virtual bool VerifyPatchedTopology(const Spec& spec) = 0;
    virtual bool LeaveTable(bool appendParagraph = true) = 0;
};

bool Execute(const actions::Command& command, Host* host) noexcept;

}

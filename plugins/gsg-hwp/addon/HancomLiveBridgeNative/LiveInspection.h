#pragma once

#include <Windows.h>

#include <string>

namespace hancom::inspection {

std::wstring Snapshot(IDispatch* hwp) noexcept;
std::wstring InspectPage(IDispatch* hwp, LONG page) noexcept;
std::wstring InspectPageV3(IDispatch* hwp, LONG page) noexcept;
std::wstring InspectPageSummary(IDispatch* hwp, LONG page) noexcept;
std::wstring InspectPagesV3(IDispatch* hwp, const std::wstring& pages) noexcept;
std::wstring InspectRoutingContext(IDispatch* hwp, LONG pageHint) noexcept;
std::wstring InspectStructure(IDispatch* hwp, LONG page) noexcept;

// Walks a paragraph list in process and reports, per paragraph, the leading
// text plus the style the document actually applies there. The request is
// "list,start,limit,charshape" (all optional, decimal, comma separated).
// One call replaces one COM round trip per paragraph from Python.
std::wstring InspectParagraphStyles(
    IDispatch* hwp,
    const std::wstring& request) noexcept;

}

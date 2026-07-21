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

}

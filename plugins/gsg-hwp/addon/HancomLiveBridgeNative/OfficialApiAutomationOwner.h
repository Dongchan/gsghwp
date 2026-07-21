#pragma once

#include <atlbase.h>
#include <atlcomcli.h>

#include <string>

namespace hancom::official_api {

HRESULT ResolveAutomationOwner(
    IDispatch* hwp,
    const std::wstring& owner,
    CComPtr<IDispatch>& result) noexcept;

}

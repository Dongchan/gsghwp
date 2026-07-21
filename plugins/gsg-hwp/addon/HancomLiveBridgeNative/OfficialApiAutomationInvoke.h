#pragma once

#include <atlbase.h>
#include <atlcomcli.h>

#include <string>
#include <vector>

namespace hancom::official_api {

struct AutomationInvocation {
    HRESULT argumentStatus = E_UNEXPECTED;
    HRESULT invokeStatus = E_UNEXPECTED;
    HRESULT writeStatus = E_NOTIMPL;
    CComVariant value;
    std::wstring outputs;
};

AutomationInvocation InvokeAutomationMember(
    IDispatch* hwp,
    IDispatch* target,
    const std::wstring& owner,
    const std::wstring& member,
    const std::wstring& kind,
    const std::vector<std::wstring>& argumentLines);

}

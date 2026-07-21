#pragma once

#include <oaidl.h>

#include <string>

namespace hancom::official_api {

HRESULT PrepareIsolatedAutomationFixture(
    IDispatch* hwp,
    const std::wstring& owner) noexcept;

}

#pragma once

#include <oaidl.h>

#include <string>

namespace hancom::api_harness {

HRESULT PrepareOfficialApiFixture(
    IDispatch* hwp,
    const std::wstring& request) noexcept;

}

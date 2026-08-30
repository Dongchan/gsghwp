#pragma once

#include <Windows.h>

#include <string>

namespace hancom::graph::layout {
struct LayoutEnvironmentPlatformV1;
}

namespace hancom::official_api {

std::wstring Probe(IDispatch* hwp, const std::wstring& payload) noexcept;
std::wstring Probe(
    IDispatch* hwp,
    const std::wstring& payload,
    const graph::layout::LayoutEnvironmentPlatformV1* environmentPlatform)
    noexcept;

}

#pragma once

#include <Windows.h>
#include <oaidl.h>

#include <string>

namespace hancom::protocol_bundle {

std::wstring Execute(
    IDispatch* hwp,
    LONG targetDocumentId,
    HWND windowHandle,
    const std::wstring& payload) noexcept;

}

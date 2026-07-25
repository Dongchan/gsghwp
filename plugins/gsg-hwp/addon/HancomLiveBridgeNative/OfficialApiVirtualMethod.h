#pragma once

#include <atlbase.h>
#include <atlcomcli.h>

#include <cstddef>

namespace hancom::official_api {

HRESULT ResolveVirtualMethod(
    IDispatch* target,
    const wchar_t* member,
    const VARTYPE* parameterTypes,
    USHORT parameterCount,
    VARTYPE returnType,
    CComPtr<IUnknown>& interfaceObject,
    size_t* slot) noexcept;

HRESULT ResolveVirtualPropertyPut(
    IDispatch* target,
    const wchar_t* member,
    const VARTYPE* parameterTypes,
    USHORT parameterCount,
    CComPtr<IUnknown>& interfaceObject,
    size_t* slot) noexcept;

}

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

HRESULT ResolveVirtualPropertyGet(
    IDispatch* target,
    const wchar_t* member,
    VARTYPE returnType,
    CComPtr<IUnknown>& interfaceObject,
    size_t* slot) noexcept;

HRESULT InvokeResolvedVirtualPropertyGet(
    IDispatch* target,
    REFIID interfaceId,
    size_t slot,
    CComPtr<IDispatch>& value) noexcept;

HRESULT InvokeResolvedVirtualUnsignedLongPropertyGet(
    IDispatch* target,
    REFIID interfaceId,
    size_t slot,
    ULONG* value) noexcept;

HRESULT ResolveVirtualPropertyPut(
    IDispatch* target,
    const wchar_t* member,
    const VARTYPE* parameterTypes,
    USHORT parameterCount,
    CComPtr<IUnknown>& interfaceObject,
    size_t* slot) noexcept;

}

#pragma once

#include <Windows.h>
#include <OleAuto.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <string>
#include <vector>

namespace hancom::dispatch {

HRESULT Invoke(
    IDispatch* object,
    LPCOLESTR name,
    WORD flags,
    const std::vector<CComVariant>& arguments,
    CComVariant* result) noexcept;

HRESULT Method(
    IDispatch* object,
    LPCOLESTR name,
    const std::vector<CComVariant>& arguments,
    CComVariant* result) noexcept;

HRESULT PropertyGet(
    IDispatch* object,
    LPCOLESTR name,
    CComVariant* result) noexcept;

HRESULT PropertyPut(
    IDispatch* object,
    LPCOLESTR name,
    const CComVariant& value) noexcept;

HRESULT AsDispatch(const CComVariant& value, CComPtr<IDispatch>& result) noexcept;
HRESULT AsString(const CComVariant& value, std::wstring* result) noexcept;
HRESULT AsLong(const CComVariant& value, LONG* result) noexcept;
HRESULT AsBool(const CComVariant& value, bool* result) noexcept;

}

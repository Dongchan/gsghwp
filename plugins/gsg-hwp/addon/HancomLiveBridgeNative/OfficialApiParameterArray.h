#pragma once

#include <atlbase.h>
#include <atlcomcli.h>

#include <cstddef>
#include <string>

namespace hancom::official_api {

class ParameterArrayWriter final {
public:
    HRESULT Bind(IDispatch* array) noexcept;
    HRESULT SetItem(LONG index, const CComVariant& value) noexcept;

private:
    CComPtr<IUnknown> virtualInterface_;
    CComPtr<IDispatch> dispatch_;
    size_t virtualSlot_ = 0;
    DISPID dispatchMember_ = DISPID_UNKNOWN;
    bool dispatchPropertyPut_ = false;
};

HRESULT CreateParameterArray(
    IDispatch* parameter,
    const std::wstring& name,
    LONG count,
    CComPtr<IDispatch>& result) noexcept;

HRESULT SetParameterArrayItem(
    IDispatch* array,
    LONG index,
    const CComVariant& value) noexcept;

}

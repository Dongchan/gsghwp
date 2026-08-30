#pragma once

#include <Windows.h>
#include <OleAuto.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <cstdint>
#include <string>
#include <vector>

namespace hancom::dispatch {

// A caller may supply this token only after independently validating the exact
// dispatch interface. It contains copied scalar TYPEATTR identity only.
struct TypeIdentityToken final {
    GUID guid{};
    LCID lcid = 0;
    WORD majorVersion = 0;
    WORD minorVersion = 0;
    TYPEKIND kind = TKIND_MAX;
    WORD flags = 0;
};

struct DispidCacheDiagnostics final {
    std::uint64_t qualifiedCalls = 0;
    std::uint64_t unqualifiedCalls = 0;
    std::uint64_t hits = 0;
    std::uint64_t misses = 0;
    std::uint64_t insertions = 0;
    std::uint64_t evictions = 0;
    std::uint64_t keys = 0;
};

void ResetDispidCacheDiagnostics() noexcept;
DispidCacheDiagnostics ReadDispidCacheDiagnostics() noexcept;

HRESULT ResolveDispidQualified(
    IDispatch* object,
    const TypeIdentityToken& type,
    LPCOLESTR name,
    DISPID* member) noexcept;

HRESULT Invoke(
    IDispatch* object,
    LPCOLESTR name,
    WORD flags,
    const std::vector<CComVariant>& arguments,
    CComVariant* result) noexcept;

HRESULT InvokeQualified(
    IDispatch* object,
    const TypeIdentityToken& type,
    LPCOLESTR name,
    WORD flags,
    const std::vector<CComVariant>& arguments,
    CComVariant* result) noexcept;

HRESULT Method(
    IDispatch* object,
    LPCOLESTR name,
    const std::vector<CComVariant>& arguments,
    CComVariant* result) noexcept;

HRESULT MethodQualified(
    IDispatch* object,
    const TypeIdentityToken& type,
    LPCOLESTR name,
    const std::vector<CComVariant>& arguments,
    CComVariant* result) noexcept;

HRESULT PropertyGet(
    IDispatch* object,
    LPCOLESTR name,
    CComVariant* result) noexcept;

HRESULT PropertyGetQualified(
    IDispatch* object,
    const TypeIdentityToken& type,
    LPCOLESTR name,
    CComVariant* result) noexcept;

HRESULT PropertyPut(
    IDispatch* object,
    LPCOLESTR name,
    const CComVariant& value) noexcept;

HRESULT PropertyPutQualified(
    IDispatch* object,
    const TypeIdentityToken& type,
    LPCOLESTR name,
    const CComVariant& value) noexcept;

HRESULT AsDispatch(const CComVariant& value, CComPtr<IDispatch>& result) noexcept;
HRESULT AsString(const CComVariant& value, std::wstring* result) noexcept;
HRESULT AsLong(const CComVariant& value, LONG* result) noexcept;
HRESULT AsBool(const CComVariant& value, bool* result) noexcept;

}

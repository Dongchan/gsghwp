#include "OfficialApiParameterProbe.h"

#include "DispatchInvoke.h"
#include "OfficialApiState.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <chrono>
#include <sstream>
#include <string>
#include <vector>

namespace hancom::official_api {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

struct ItemResult {
    std::wstring name;
    HRESULT create = S_OK;
    HRESULT set = E_UNEXPECTED;
    HRESULT get = E_UNEXPECTED;
    HRESULT exists = E_UNEXPECTED;
    LONG existsReturn = -1;
    bool passed = false;
};

std::vector<std::wstring> Fields(const std::wstring& line) {
    std::vector<std::wstring> fields;
    size_t start = 0;
    for (;;) {
        const size_t end = line.find(L'\t', start);
        fields.push_back(line.substr(start, end - start));
        if (end == std::wstring::npos) {
            return fields;
        }
        start = end + 1;
    }
}

CComVariant BooleanVariant(const bool value) noexcept {
    CComVariant result;
    result.vt = VT_BOOL;
    result.boolVal = value ? VARIANT_TRUE : VARIANT_FALSE;
    return result;
}

CComVariant BinaryVariant() {
    CComVariant result;
    SAFEARRAYBOUND bound{};
    bound.cElements = 3;
    result.vt = VT_ARRAY | VT_UI1;
    result.parray = SafeArrayCreate(VT_UI1, 1, &bound);
    if (result.parray == nullptr) {
        result.Clear();
        return result;
    }
    for (LONG index = 0; index < 3; ++index) {
        BYTE value = static_cast<BYTE>(index + 1);
        static_cast<void>(SafeArrayPutElement(result.parray, &index, &value));
    }
    return result;
}

CComVariant ExpectedValue(const std::wstring& type) {
    if (type == L"PIT_BSTR" || type == L"PIT_BSRT") {
        return CComVariant(L"codex-runtime-probe");
    }
    if (type == L"PMT_BOOL") {
        return BooleanVariant(true);
    }
    if (type == L"PIT_BINDATA") {
        return BinaryVariant();
    }
    return CComVariant(1L);
}

HRESULT DispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& result) noexcept {
    CComVariant raw;
    HRESULT status = PropertyGet(object, name, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, result);
    }
    return status;
}

HRESULT DispatchMethod(
    IDispatch* const object,
    const wchar_t* const name,
    const std::vector<CComVariant>& arguments,
    CComPtr<IDispatch>& result) noexcept {
    CComVariant raw;
    HRESULT status = Method(object, name, arguments, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, result);
    }
    return status;
}

ItemResult ProbeItem(IDispatch* const set, const std::wstring& line) {
    ItemResult result;
    const std::vector<std::wstring> fields = Fields(line);
    if (fields.size() != 4 || fields[0] != L"ITEM") {
        result.name = L"invalid";
        result.create = E_INVALIDARG;
        return result;
    }
    result.name = fields[1];
    const std::wstring& type = fields[2];
    const std::wstring& subtype = fields[3];
    CComVariant observed;
    if (type == L"PIT_SET") {
        CComVariant rawChild;
        result.create = Method(
            set,
            L"CreateItemSet",
            {CComVariant(result.name.c_str()), CComVariant(subtype.c_str())},
            &rawChild);
        CComPtr<IDispatch> child;
        if (SUCCEEDED(result.create)) {
            result.create = AsDispatch(rawChild, child);
        }
        result.set = result.create;
        result.get = Method(set, L"Item", {CComVariant(result.name.c_str())}, &observed);
    } else if (type == L"PIT_ARRAY") {
        CComVariant rawArray;
        result.create = Method(
            set,
            L"CreateItemArray",
            {CComVariant(result.name.c_str()), CComVariant(2L)},
            &rawArray);
        CComPtr<IDispatch> array;
        if (SUCCEEDED(result.create)) {
            result.create = AsDispatch(rawArray, array);
        }
        if (SUCCEEDED(result.create)) {
            const CComVariant expected = ExpectedValue(subtype);
            CComVariant ignored;
            result.set = Method(array, L"SetItem", {CComVariant(1L), expected}, &ignored);
            result.get = Method(array, L"Item", {CComVariant(1L)}, &observed);
            result.passed = SUCCEEDED(result.set) && SUCCEEDED(result.get);
        }
    } else {
        const CComVariant expected = ExpectedValue(type);
        CComVariant ignored;
        result.set = Method(
            set,
            L"SetItem",
            {CComVariant(result.name.c_str()), expected},
            &ignored);
        result.get = Method(set, L"Item", {CComVariant(result.name.c_str())}, &observed);
        result.passed = SUCCEEDED(result.set) && SUCCEEDED(result.get);
    }
    CComVariant exists;
    result.exists = Method(
        set,
        L"ItemExist",
        {CComVariant(result.name.c_str())},
        &exists);
    bool present = false;
    if (SUCCEEDED(result.exists)) {
        const HRESULT converted = AsBool(exists, &present);
        if (SUCCEEDED(converted)) {
            result.existsReturn = present ? 1L : 0L;
        }
    }
    result.passed = result.passed ||
        (type == L"PIT_SET" && SUCCEEDED(result.create) && SUCCEEDED(result.get));
    result.passed = result.passed && SUCCEEDED(result.exists);
    return result;
}

}

std::wstring ProbeParameterSet(
    IDispatch* const hwp,
    const std::wstring& name,
    const std::wstring& linkedAction,
    const std::vector<std::wstring>& itemLines) {
    const auto started = std::chrono::steady_clock::now();
    const DocumentState before = CaptureDocumentState(hwp);
    CComPtr<IDispatch> action;
    CComPtr<IDispatch> set;
    HRESULT createAction = S_FALSE;
    HRESULT createSet = E_UNEXPECTED;
    if (linkedAction != L"-") {
        createAction = DispatchMethod(
            hwp,
            L"CreateAction",
            {CComVariant(linkedAction.c_str())},
            action);
        if (SUCCEEDED(createAction)) {
            createSet = DispatchMethod(action, L"CreateSet", {}, set);
        }
    } else {
        createSet = DispatchMethod(
            hwp,
            L"CreateSet",
            {CComVariant(name.c_str())},
            set);
    }
    if (FAILED(createSet) || set == nullptr) {
        createSet = DispatchMethod(
            hwp,
            L"CreateSet",
            {CComVariant(name.c_str())},
            set);
    }
    HRESULT getDefault = S_FALSE;
    HRESULT execute = S_FALSE;
    LONG executeReturn = -1;
    if (SUCCEEDED(createSet) && action != nullptr) {
        CComVariant raw;
        getDefault = Method(
            action,
            L"GetDefault",
            {CComVariant(set)},
            &raw);
    }
    std::vector<ItemResult> items;
    items.reserve(itemLines.size());
    for (const std::wstring& line : itemLines) {
        items.push_back(ProbeItem(set, line));
    }
    LONG previousMessageMode = 0;
    HRESULT messageMode = S_FALSE;
    if (SUCCEEDED(createSet) && action != nullptr) {
        CComVariant raw;
        CComVariant previous;
        messageMode = Method(
            hwp, L"SetMessageBoxMode", {CComVariant(0x00011010L)}, &previous);
        if (SUCCEEDED(messageMode)) {
            static_cast<void>(AsLong(previous, &previousMessageMode));
            execute = Method(action, L"Execute", {CComVariant(set)}, &raw);
            CComVariant ignored;
            static_cast<void>(Method(
                hwp, L"SetMessageBoxMode", {CComVariant(previousMessageMode)}, &ignored));
        }
        bool returned = false;
        if (SUCCEEDED(execute) && SUCCEEDED(AsBool(raw, &returned))) {
            executeReturn = returned ? 1L : 0L;
        }
    }
    const size_t passed = static_cast<size_t>(std::count_if(
        items.begin(),
        items.end(),
        [](const ItemResult& item) { return item.passed; }));
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started).count();
    const DocumentState after = CaptureDocumentState(hwp);
    std::wostringstream response;
    response << L"HCV1\tPARAMETER_SET\t" << name
             << L'\t' << static_cast<LONG>(createAction)
             << L'\t' << static_cast<LONG>(createSet)
             << L'\t' << static_cast<LONG>(getDefault)
             << L'\t' << static_cast<LONG>(messageMode)
             << L'\t' << static_cast<LONG>(execute)
             << L'\t' << executeReturn
             << L'\t' << items.size()
             << L'\t' << passed
             << L'\t' << items.size() - passed;
    AppendDocumentState(response, before);
    AppendDocumentState(response, after);
    response << L'\t' << elapsed;
    for (const ItemResult& item : items) {
        response << L"\nITEM\t" << item.name
                 << L'\t' << static_cast<LONG>(item.create)
                 << L'\t' << static_cast<LONG>(item.set)
                 << L'\t' << static_cast<LONG>(item.get)
                 << L'\t' << static_cast<LONG>(item.exists)
                 << L'\t' << item.existsReturn
                 << L'\t' << (item.passed ? 1 : 0);
    }
    return response.str();
}

}

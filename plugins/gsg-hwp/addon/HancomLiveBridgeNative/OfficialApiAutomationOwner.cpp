#include "OfficialApiAutomationOwner.h"

#include "DispatchInvoke.h"
#include "OfficialApiParameterArray.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <string>
#include <vector>

namespace hancom::official_api {
namespace {

using hancom::dispatch::AsDispatch;
using hancom::dispatch::Invoke;
using hancom::dispatch::PropertyGet;

HRESULT ObjectProperty(
    IDispatch* const source,
    const wchar_t* const name,
    CComPtr<IDispatch>& result) noexcept {
    CComVariant value;
    HRESULT status = PropertyGet(source, name, &value);
    if (SUCCEEDED(status)) {
        status = AsDispatch(value, result);
    }
    return status;
}

HRESULT ObjectMember(
    IDispatch* const source,
    const wchar_t* const name,
    const std::vector<CComVariant>& arguments,
    CComPtr<IDispatch>& result) noexcept {
    CComVariant value;
    HRESULT status = Invoke(
        source,
        name,
        DISPATCH_METHOD | DISPATCH_PROPERTYGET,
        arguments,
        &value);
    if (SUCCEEDED(status)) {
        status = AsDispatch(value, result);
    }
    return status;
}

HRESULT ActiveDocument(IDispatch* const hwp, CComPtr<IDispatch>& result) noexcept {
    CComPtr<IDispatch> documents;
    HRESULT status = ObjectProperty(hwp, L"XHwpDocuments", documents);
    if (SUCCEEDED(status)) {
        status = ObjectProperty(documents, L"Active_XHwpDocument", result);
    }
    return status;
}

HRESULT ActiveWindow(IDispatch* const hwp, CComPtr<IDispatch>& result) noexcept {
    CComPtr<IDispatch> windows;
    HRESULT status = ObjectProperty(hwp, L"XHwpWindows", windows);
    if (SUCCEEDED(status)) {
        status = ObjectProperty(windows, L"Active_XHwpWindow", result);
    }
    return status;
}

HRESULT FormCollection(
    IDispatch* const hwp,
    const std::wstring& owner,
    CComPtr<IDispatch>& result) noexcept {
    CComPtr<IDispatch> document;
    const HRESULT status = ActiveDocument(hwp, document);
    if (FAILED(status)) {
        return status;
    }
    static constexpr const wchar_t* kOwners[] = {
        L"IXHwpFormPushButtons", L"IXHwpFormCheckButtons",
        L"IXHwpFormRadioButtons", L"IXHwpFormComboBoxs", L"IXHwpFormEdits"};
    for (const wchar_t* const candidate : kOwners) {
        if (owner == candidate) {
            return ObjectProperty(document, candidate + 1, result);
        }
    }
    return E_INVALIDARG;
}

HRESULT FormItem(
    IDispatch* const hwp,
    const std::wstring& owner,
    CComPtr<IDispatch>& result) noexcept {
    struct Mapping {
        const wchar_t* item;
        const wchar_t* collection;
    };
    static constexpr Mapping kMappings[] = {
        {L"IXHwpFormPushButton", L"IXHwpFormPushButtons"},
        {L"IXHwpFormCheckButton", L"IXHwpFormCheckButtons"},
        {L"IXHwpFormRadioButton", L"IXHwpFormRadioButtons"},
        {L"IXHwpFormComboBox", L"IXHwpFormComboBoxs"},
        {L"IXHwpFormEdit", L"IXHwpFormEdits"},
    };
    for (const Mapping& mapping : kMappings) {
        if (owner == mapping.item) {
            CComPtr<IDispatch> collection;
            const HRESULT status = FormCollection(hwp, mapping.collection, collection);
            return FAILED(status)
                ? status
                : ObjectMember(collection, L"Item", {CComVariant(0L)}, result);
        }
    }
    return E_INVALIDARG;
}

HRESULT TabsOwner(
    IDispatch* const hwp,
    const std::wstring& owner,
    CComPtr<IDispatch>& result) noexcept {
    CComPtr<IDispatch> window;
    HRESULT status = ActiveWindow(hwp, window);
    CComPtr<IDispatch> tabs;
    if (SUCCEEDED(status)) {
        status = ObjectProperty(window, L"XHwpTabs", tabs);
    }
    if (owner == L"IXHwpTabs" || FAILED(status)) {
        result = tabs;
        return status;
    }
    return ObjectMember(tabs, L"Item", {CComVariant(0L)}, result);
}

HRESULT ParameterOwner(
    IDispatch* const hwp,
    const std::wstring& owner,
    CComPtr<IDispatch>& result) noexcept {
    if (owner == L"IDHwpParameterArray") {
        CComPtr<IDispatch> parameterSets;
        HRESULT status = ObjectProperty(hwp, L"HParameterSet", parameterSets);
        CComPtr<IDispatch> styleTemplate;
        if (SUCCEEDED(status)) {
            status = ObjectProperty(
                parameterSets, L"HStyleTemplate", styleTemplate);
        }
        CComPtr<IDispatch> array;
        if (SUCCEEDED(status)) {
            status = CreateParameterArray(
                styleTemplate,
                L"NameLocals",
                1L,
                array);
        }
        if (SUCCEEDED(status)) {
            const CComVariant localName(L"바탕글");
            status = SetParameterArrayItem(array, 1L, localName);
        }
        if (SUCCEEDED(status)) {
            result = array;
        }
        if (FAILED(status)) {
            result.Release();
        }
        return status;
    }
    CComPtr<IDispatch> set;
    HRESULT status = ObjectMember(
        hwp, L"CreateSet", {CComVariant(L"Sort")}, set);
    CComPtr<IDispatch> hAction;
    if (SUCCEEDED(status)) {
        status = ObjectProperty(hwp, L"HAction", hAction);
    }
    if (SUCCEEDED(status)) {
        CComVariant ignored;
        status = Invoke(
            hAction,
            L"GetDefault",
            DISPATCH_METHOD,
            {CComVariant(L"Sort"), CComVariant(set)},
            &ignored);
    }
    result = set;
    return status;
}

}

HRESULT ResolveAutomationOwner(
    IDispatch* const hwp,
    const std::wstring& owner,
    CComPtr<IDispatch>& result) noexcept {
    result.Release();
    if (owner == L"IHwpObject" || owner == L"IHwpObjectEvents") {
        result = hwp;
        return S_OK;
    }
    if (owner == L"IXHwpDocuments") {
        return ObjectProperty(hwp, L"XHwpDocuments", result);
    }
    if (owner == L"IXHwpDocument") {
        return ActiveDocument(hwp, result);
    }
    if (owner == L"IXHwpWindows") {
        return ObjectProperty(hwp, L"XHwpWindows", result);
    }
    if (owner == L"IXHwpWindow") {
        return ActiveWindow(hwp, result);
    }
    if (owner == L"IXHwpTabs" || owner == L"IXHwpTab") {
        return TabsOwner(hwp, owner, result);
    }
    if (owner == L"HAction") {
        return ObjectProperty(hwp, owner.c_str(), result);
    }
    if (owner == L"IXHwpMessageBox") {
        return ObjectProperty(hwp, L"XHwpMessageBox", result);
    }
    if (owner == L"IDHwpAction") {
        return ObjectMember(hwp, L"CreateAction", {CComVariant(L"Cancel")}, result);
    }
    if (owner == L"IDHwpParameterSet" || owner == L"IDHwpParameterArray") {
        return ParameterOwner(hwp, owner, result);
    }
    if (owner == L"IDHwpCtrlCode") {
        return ObjectProperty(hwp, L"HeadCtrl", result);
    }
    CComPtr<IDispatch> collection;
    const HRESULT status = FormCollection(hwp, owner, collection);
    if (SUCCEEDED(status)) {
        result = collection;
        return S_OK;
    }
    return FormItem(hwp, owner, result);
}

}

#include "OfficialApiFixture.h"

#include "OfficialApiAutomationInvoke.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <string>

namespace hancom::official_api {
namespace {

const wchar_t* FormCreatorAction(const std::wstring& owner) noexcept {
    if (owner.find(L"FormPushButton") != std::wstring::npos) {
        return L"Rm9ybU9iakNyZWF0b3JQdXNoQnV0dG9u";
    }
    if (owner.find(L"FormCheckButton") != std::wstring::npos) {
        return L"Rm9ybU9iakNyZWF0b3JDaGVja0J1dHRvbg==";
    }
    if (owner.find(L"FormRadioButton") != std::wstring::npos) {
        return L"Rm9ybU9iakNyZWF0b3JSYWRpb0J1dHRvbg==";
    }
    if (owner.find(L"FormComboBox") != std::wstring::npos) {
        return L"Rm9ybU9iakNyZWF0b3JDb21ib0JveA==";
    }
    if (owner.find(L"FormEdit") != std::wstring::npos) {
        return L"Rm9ybU9iakNyZWF0b3JFZGl0";
    }
    return nullptr;
}

}

HRESULT PrepareIsolatedAutomationFixture(
    IDispatch* const hwp,
    const std::wstring& owner) noexcept {
    if (hwp == nullptr) {
        return E_POINTER;
    }
    const wchar_t* const action = FormCreatorAction(owner);
    if (action == nullptr) {
        return S_OK;
    }
    const AutomationInvocation invocation = InvokeAutomationMember(
        hwp,
        hwp,
        L"IHwpObject",
        L"Run",
        L"method",
        {std::wstring(L"ARG\tBSTR64\t") + action});
    return FAILED(invocation.argumentStatus)
        ? invocation.argumentStatus
        : invocation.invokeStatus;
}

}

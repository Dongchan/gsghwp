#include "OfficialApiAutomationProbe.h"

#include "OfficialApiAutomationInvoke.h"
#include "OfficialApiAutomationOwner.h"
#include "OfficialApiState.h"
#include "OfficialApiVariant.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <chrono>
#include <sstream>
#include <string>
#include <vector>

namespace hancom::official_api {
std::wstring ProbeAutomation(
    IDispatch* const hwp,
    const std::wstring& owner,
    const std::wstring& member,
    const std::wstring& kind,
    const std::vector<std::wstring>& argumentLines) {
    const auto started = std::chrono::steady_clock::now();
    const DocumentState before = CaptureDocumentState(hwp);
    CComPtr<IDispatch> target;
    const HRESULT ownerStatus = ResolveAutomationOwner(hwp, owner, target);
    AutomationInvocation invocation;
    if (SUCCEEDED(ownerStatus)) {
        invocation = InvokeAutomationMember(
            hwp,
            target,
            owner,
            member,
            kind,
            argumentLines);
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started).count();
    const DocumentState after = CaptureDocumentState(hwp);
    std::wostringstream response;
    response << L"HCV1\tAUTOMATION\t" << owner << L'\t' << member << L'\t' << kind
             << L'\t' << static_cast<LONG>(ownerStatus)
             << L'\t' << static_cast<LONG>(invocation.argumentStatus)
             << L'\t' << static_cast<LONG>(invocation.invokeStatus)
             << L'\t' << static_cast<LONG>(invocation.writeStatus)
             << L'\t' << invocation.value.vt
             << L'\t' << VariantText(invocation.value);
    AppendDocumentState(response, before);
    AppendDocumentState(response, after);
    response << L'\t' << elapsed << invocation.outputs;
    return response.str();
}

}

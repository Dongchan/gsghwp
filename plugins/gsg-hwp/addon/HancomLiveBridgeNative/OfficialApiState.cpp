#include "OfficialApiState.h"

#include "DispatchInvoke.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <ostream>
#include <string>

namespace hancom::official_api {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

void AddHash(std::uint64_t* const hash, const std::wstring& value) noexcept {
    constexpr std::uint64_t kPrime = 1099511628211ULL;
    for (const wchar_t character : value) {
        *hash ^= static_cast<std::uint16_t>(character);
        *hash *= kPrime;
    }
}

void CaptureControls(IDispatch* const hwp, DocumentState* const state) noexcept {
    CComVariant raw;
    if (FAILED(PropertyGet(hwp, L"HeadCtrl", &raw))) {
        return;
    }
    CComPtr<IDispatch> control;
    if (FAILED(AsDispatch(raw, control))) {
        state->controlCount = 0;
        return;
    }
    state->controlCount = 0;
    state->controlHash = 1469598103934665603ULL;
    while (control != nullptr && state->controlCount < 100000) {
        CComVariant rawId;
        std::wstring id;
        if (SUCCEEDED(PropertyGet(control, L"CtrlID", &rawId))) {
            static_cast<void>(AsString(rawId, &id));
        }
        AddHash(&state->controlHash, id);
        ++state->controlCount;
        CComVariant rawNext;
        if (FAILED(PropertyGet(control, L"Next", &rawNext))) {
            break;
        }
        CComPtr<IDispatch> next;
        if (FAILED(AsDispatch(rawNext, next))) {
            break;
        }
        control = next;
    }
}

}

DocumentState CaptureDocumentState(IDispatch* const hwp) noexcept {
    DocumentState state;
    CComVariant raw;
    if (SUCCEEDED(PropertyGet(hwp, L"PageCount", &raw))) {
        static_cast<void>(AsLong(raw, &state.pageCount));
    }
    raw.Clear();
    bool modified = false;
    if (SUCCEEDED(PropertyGet(hwp, L"IsModified", &raw)) &&
        SUCCEEDED(AsBool(raw, &modified))) {
        state.modified = modified ? 1L : 0L;
    }
    CComVariant list;
    list.vt = VT_I4 | VT_BYREF;
    list.plVal = &state.list;
    CComVariant paragraph;
    paragraph.vt = VT_I4 | VT_BYREF;
    paragraph.plVal = &state.paragraph;
    CComVariant character;
    character.vt = VT_I4 | VT_BYREF;
    character.plVal = &state.character;
    static_cast<void>(Method(hwp, L"GetPos", {list, paragraph, character}, nullptr));
    CaptureControls(hwp, &state);
    return state;
}

void AppendDocumentState(std::wostream& output, const DocumentState& state) {
    output << L'\t' << state.pageCount
           << L'\t' << state.modified
           << L'\t' << state.list
           << L'\t' << state.paragraph
           << L'\t' << state.character
           << L'\t' << state.controlCount
           << L'\t' << state.controlHash;
}

}

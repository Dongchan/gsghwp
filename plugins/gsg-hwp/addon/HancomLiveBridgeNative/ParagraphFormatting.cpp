#include "ParagraphFormatting.h"

#include "DispatchInvoke.h"

#include <atlbase.h>
#include <atlcomcli.h>

namespace hancom::formatting {
namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;
using hancom::dispatch::PropertyPut;

HRESULT DispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value) noexcept {
    CComVariant raw;
    const HRESULT status = PropertyGet(object, name, &raw);
    return FAILED(status) ? status : AsDispatch(raw, value);
}

HRESULT LongProperty(
    IDispatch* const object,
    const wchar_t* const name,
    LONG* const value) noexcept {
    CComVariant raw;
    const HRESULT status = PropertyGet(object, name, &raw);
    return FAILED(status) ? status : AsLong(raw, value);
}

HRESULT DefaultParameter(
    IDispatch* const action,
    IDispatch* const parameter,
    const wchar_t* const actionName,
    CComPtr<IDispatch>& set) noexcept {
    HRESULT status = DispatchProperty(parameter, L"HSet", set);
    if (SUCCEEDED(status)) {
        CComVariant ignored;
        status = Method(
            action,
            L"GetDefault",
            {CComVariant(actionName), CComVariant(set)},
            &ignored);
    }
    return status;
}

HRESULT ExecuteParameter(
    IDispatch* const action,
    const wchar_t* const actionName,
    IDispatch* const set) noexcept {
    CComVariant returned;
    HRESULT status = Method(
        action,
        L"Execute",
        {CComVariant(actionName), CComVariant(set)},
        &returned);
    if (FAILED(status) || returned.vt == VT_EMPTY) {
        return status;
    }
    bool executed = false;
    status = AsBool(returned, &executed);
    return SUCCEEDED(status) && executed ? S_OK : FAILED(status) ? status : E_FAIL;
}

HRESULT RuntimeObjects(
    IDispatch* const hwp,
    CComPtr<IDispatch>& action,
    CComPtr<IDispatch>& parameterSets) noexcept {
    HRESULT status = DispatchProperty(hwp, L"HAction", action);
    return FAILED(status)
        ? status
        : DispatchProperty(hwp, L"HParameterSet", parameterSets);
}

}

bool ParagraphFormat::operator==(const ParagraphFormat& other) const noexcept {
    return styleId == other.styleId &&
        alignment == other.alignment &&
        lineSpacing == other.lineSpacing &&
        leftMargin == other.leftMargin &&
        rightMargin == other.rightMargin &&
        indentation == other.indentation &&
        previousSpacing == other.previousSpacing &&
        nextSpacing == other.nextSpacing;
}

HRESULT ReadParagraphFormat(
    IDispatch* const hwp,
    ParagraphFormat* const format) noexcept {
    if (hwp == nullptr || format == nullptr) {
        return E_POINTER;
    }
    CComPtr<IDispatch> action;
    CComPtr<IDispatch> parameterSets;
    HRESULT status = RuntimeObjects(hwp, action, parameterSets);
    CComPtr<IDispatch> style;
    CComPtr<IDispatch> styleSet;
    if (SUCCEEDED(status)) {
        status = DispatchProperty(parameterSets, L"HStyle", style);
    }
    if (SUCCEEDED(status)) {
        status = DefaultParameter(action, style, L"Style", styleSet);
    }
    if (SUCCEEDED(status)) {
        status = LongProperty(style, L"Apply", &format->styleId);
    }

    CComPtr<IDispatch> paragraph;
    CComPtr<IDispatch> paragraphSet;
    if (SUCCEEDED(status)) {
        status = DispatchProperty(parameterSets, L"HParaShape", paragraph);
    }
    if (SUCCEEDED(status)) {
        status = DefaultParameter(action, paragraph, L"ParagraphShape", paragraphSet);
    }
    const std::pair<const wchar_t*, LONG*> fields[] = {
        {L"AlignType", &format->alignment},
        {L"LineSpacing", &format->lineSpacing},
        {L"LeftMargin", &format->leftMargin},
        {L"RightMargin", &format->rightMargin},
        {L"Indentation", &format->indentation},
        {L"PrevSpacing", &format->previousSpacing},
        {L"NextSpacing", &format->nextSpacing},
    };
    for (const auto& [name, value] : fields) {
        if (SUCCEEDED(status)) {
            status = LongProperty(paragraph, name, value);
        }
    }
    return status;
}

HRESULT ApplyParagraphFormat(
    IDispatch* const hwp,
    const ParagraphFormat& format) noexcept {
    if (hwp == nullptr) {
        return E_POINTER;
    }
    CComPtr<IDispatch> action;
    CComPtr<IDispatch> parameterSets;
    HRESULT status = RuntimeObjects(hwp, action, parameterSets);
    CComPtr<IDispatch> style;
    CComPtr<IDispatch> styleSet;
    if (SUCCEEDED(status)) {
        status = DispatchProperty(parameterSets, L"HStyle", style);
    }
    if (SUCCEEDED(status)) {
        status = DefaultParameter(action, style, L"Style", styleSet);
    }
    if (SUCCEEDED(status)) {
        status = PropertyPut(style, L"Apply", CComVariant(format.styleId));
    }
    if (SUCCEEDED(status)) {
        status = ExecuteParameter(action, L"Style", styleSet);
    }

    CComPtr<IDispatch> paragraph;
    CComPtr<IDispatch> paragraphSet;
    if (SUCCEEDED(status)) {
        status = DispatchProperty(parameterSets, L"HParaShape", paragraph);
    }
    if (SUCCEEDED(status)) {
        status = DefaultParameter(action, paragraph, L"ParagraphShape", paragraphSet);
    }
    const std::pair<const wchar_t*, LONG> fields[] = {
        {L"AlignType", format.alignment},
        {L"LineSpacing", format.lineSpacing},
        {L"LeftMargin", format.leftMargin},
        {L"RightMargin", format.rightMargin},
        {L"Indentation", format.indentation},
        {L"PrevSpacing", format.previousSpacing},
        {L"NextSpacing", format.nextSpacing},
    };
    for (const auto& [name, value] : fields) {
        if (SUCCEEDED(status)) {
            status = PropertyPut(paragraph, name, CComVariant(value));
        }
    }
    return FAILED(status)
        ? status
        : ExecuteParameter(action, L"ParagraphShape", paragraphSet);
}

}

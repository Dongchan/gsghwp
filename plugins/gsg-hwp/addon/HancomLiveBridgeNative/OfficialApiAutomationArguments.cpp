#include "OfficialApiAutomationArguments.h"

#include "DispatchInvoke.h"
#include "OfficialApiAutomationOwner.h"
#include "OfficialApiVariant.h"

#include <Windows.h>
#include <atlbase.h>
#include <atlcomcli.h>

#include <cerrno>
#include <cmath>
#include <cwchar>
#include <limits>
#include <new>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#ifdef min
#undef min
#endif
#ifdef max
#undef max
#endif

namespace hancom::official_api {
namespace {

using hancom::dispatch::AsDispatch;
using hancom::dispatch::Method;

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

bool SignedValue(const std::wstring& text, LONGLONG* const value) noexcept {
    if (value == nullptr || text.empty()) {
        return false;
    }
    wchar_t* end = nullptr;
    errno = 0;
    const LONGLONG parsed = _wcstoi64(text.c_str(), &end, 10);
    if (errno == ERANGE || end == text.c_str() || *end != L'\0') {
        return false;
    }
    *value = parsed;
    return true;
}

bool UnsignedValue(const std::wstring& text, ULONGLONG* const value) noexcept {
    if (value == nullptr || text.empty() || text.front() == L'-') {
        return false;
    }
    wchar_t* end = nullptr;
    errno = 0;
    const ULONGLONG parsed = _wcstoui64(text.c_str(), &end, 10);
    if (errno == ERANGE || end == text.c_str() || *end != L'\0') {
        return false;
    }
    *value = parsed;
    return true;
}

bool RealValue(const std::wstring& text, double* const value) noexcept {
    if (value == nullptr || text.empty()) {
        return false;
    }
    wchar_t* end = nullptr;
    errno = 0;
    const double parsed = wcstod(text.c_str(), &end);
    if (errno == ERANGE || end == text.c_str() || *end != L'\0' || !std::isfinite(parsed)) {
        return false;
    }
    *value = parsed;
    return true;
}

bool BooleanValue(const std::wstring& text, VARIANT_BOOL* const value) noexcept {
    if (value == nullptr) {
        return false;
    }
    if (text == L"1" || text == L"true" || text == L"TRUE") {
        *value = VARIANT_TRUE;
        return true;
    }
    if (text == L"0" || text == L"false" || text == L"FALSE") {
        *value = VARIANT_FALSE;
        return true;
    }
    return false;
}

HRESULT MakeActionSet(
    IDispatch* const hwp,
    const std::wstring& actionName,
    CComPtr<IDispatch>& result) noexcept {
    CComVariant rawParameterSets;
    CComPtr<IDispatch> parameterSets;
    HRESULT status = hancom::dispatch::PropertyGet(
        hwp, L"HParameterSet", &rawParameterSets);
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawParameterSets, parameterSets);
    }
    CComVariant rawParameter;
    CComPtr<IDispatch> parameter;
    const std::wstring parameterName = L"H" + actionName;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            parameterSets, parameterName.c_str(), &rawParameter);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawParameter, parameter);
    }
    CComVariant rawSet;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(parameter, L"HSet", &rawSet);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawSet, result);
    }
    if (FAILED(status)) {
        result.Release();
        rawSet.Clear();
    }
    if (FAILED(status)) {
        status = Method(
            hwp,
            L"CreateSet",
            {CComVariant(actionName.c_str())},
            &rawSet);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawSet, result);
    }
    if (FAILED(status)) {
        result.Release();
    }
    CComVariant rawAction;
    if (FAILED(status)) {
        status = Method(
            hwp,
            L"CreateAction",
            {CComVariant(actionName.c_str())},
            &rawAction);
    }
    CComPtr<IDispatch> action;
    if (SUCCEEDED(status) && result == nullptr) {
        status = AsDispatch(rawAction, action);
    }
    if (SUCCEEDED(status) && result == nullptr) {
        rawSet.Clear();
        status = Method(action, L"CreateSet", {}, &rawSet);
    }
    if (SUCCEEDED(status) && result == nullptr) {
        status = AsDispatch(rawSet, result);
    }
    if (SUCCEEDED(status)) {
        CComVariant rawHAction;
        CComPtr<IDispatch> hAction;
        HRESULT initialize = hancom::dispatch::PropertyGet(
            hwp, L"HAction", &rawHAction);
        if (SUCCEEDED(initialize)) {
            initialize = AsDispatch(rawHAction, hAction);
        }
        if (SUCCEEDED(initialize)) {
            CComVariant ignored;
            static_cast<void>(Method(
                hAction,
                L"GetDefault",
                {CComVariant(actionName.c_str()), CComVariant(result)},
                &ignored));
        }
    }
    return status;
}

HRESULT MakeParameterSet(
    IDispatch* const hwp,
    const std::wstring& setName,
    CComPtr<IDispatch>& result) noexcept {
    CComVariant rawParameterSets;
    CComPtr<IDispatch> parameterSets;
    HRESULT status = hancom::dispatch::PropertyGet(
        hwp, L"HParameterSet", &rawParameterSets);
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawParameterSets, parameterSets);
    }
    CComVariant rawParameter;
    CComPtr<IDispatch> parameter;
    const std::wstring parameterName = L"H" + setName;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            parameterSets, parameterName.c_str(), &rawParameter);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawParameter, parameter);
    }
    CComVariant rawSet;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(parameter, L"HSet", &rawSet);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawSet, result);
    }
    if (FAILED(status)) {
        result.Release();
        rawSet.Clear();
        status = Method(
            hwp,
            L"CreateSet",
            {CComVariant(setName.c_str())},
            &rawSet);
    }
    if (SUCCEEDED(status) && result == nullptr) {
        status = AsDispatch(rawSet, result);
    }
    return status;
}

HRESULT MakeStyleTemplateSet(
    IDispatch* const hwp,
    const std::wstring& fileName,
    CComPtr<IDispatch>& result) noexcept {
    CComVariant rawParameterSets;
    CComPtr<IDispatch> parameterSets;
    HRESULT status = hancom::dispatch::PropertyGet(
        hwp, L"HParameterSet", &rawParameterSets);
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawParameterSets, parameterSets);
    }
    CComVariant rawTemplate;
    CComPtr<IDispatch> styleTemplate;
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyGet(
            parameterSets, L"HStyleTemplate", &rawTemplate);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawTemplate, styleTemplate);
    }
    if (SUCCEEDED(status)) {
        status = hancom::dispatch::PropertyPut(
            styleTemplate, L"filename", CComVariant(fileName.c_str()));
    }
    if (SUCCEEDED(status)) {
        result = styleTemplate;
    }
    return status;
}

}

class AutomationArgument final {
public:
    AutomationArgument() = default;
    ~AutomationArgument() noexcept {
        if (bstrValue_ != nullptr) {
            SysFreeString(bstrValue_);
        }
    }

    AutomationArgument(const AutomationArgument&) = delete;
    AutomationArgument& operator=(const AutomationArgument&) = delete;

    HRESULT Initialize(
        IDispatch* const hwp,
        IDispatch* const target,
        const std::vector<std::wstring>& fields) noexcept {
        if (fields.size() < 2 || fields[0] != L"ARG") {
            return E_INVALIDARG;
        }
        const std::wstring& kind = fields[1];
        if (kind == L"EMPTY" && fields.size() == 2) {
            value_.Clear();
            return S_OK;
        }
        if (kind == L"NULL" && fields.size() == 2) {
            value_.Clear();
            value_.vt = VT_NULL;
            return S_OK;
        }
        if (kind == L"MISSING" && fields.size() == 2) {
            value_.Clear();
            value_.vt = VT_ERROR;
            value_.scode = DISP_E_PARAMNOTFOUND;
            return S_OK;
        }
        if (kind == L"TARGET" && fields.size() == 2 && target != nullptr) {
            return SetDispatch(target);
        }
        if (kind == L"DISPATCH_OWNER" && fields.size() == 3) {
            const HRESULT status = ResolveAutomationOwner(hwp, fields[2], dispatch_);
            return SUCCEEDED(status) ? SetDispatch(dispatch_) : status;
        }
        if (kind == L"ACTION_SET" && fields.size() == 3) {
            const HRESULT status = MakeActionSet(hwp, fields[2], dispatch_);
            return SUCCEEDED(status) ? SetDispatch(dispatch_) : status;
        }
        if (kind == L"PARAMETER_SET" && fields.size() == 3) {
            const HRESULT status = MakeParameterSet(hwp, fields[2], dispatch_);
            return SUCCEEDED(status) ? SetDispatch(dispatch_) : status;
        }
        if (kind == L"PARAMETER_SET_BSTR" && fields.size() == 5) {
            std::wstring decoded;
            if (!DecodeUtf8Base64(fields[4], &decoded)) {
                return E_INVALIDARG;
            }
            const bool isStyleTemplateFile = fields[2] == L"StyleTemplate" &&
                fields[3] == L"FileName";
            HRESULT status = isStyleTemplateFile
                ? MakeStyleTemplateSet(hwp, decoded, dispatch_)
                : MakeParameterSet(hwp, fields[2], dispatch_);
            if (SUCCEEDED(status) && !isStyleTemplateFile) {
                CComVariant ignored;
                status = Method(
                    dispatch_,
                    L"SetItem",
                    {CComVariant(fields[3].c_str()), CComVariant(decoded.c_str())},
                    &ignored);
            }
            return SUCCEEDED(status) ? SetDispatch(dispatch_) : status;
        }
        if (kind == L"BSTR64" && fields.size() == 3) {
            std::wstring decoded;
            if (!DecodeUtf8Base64(fields[2], &decoded)) {
                return E_INVALIDARG;
            }
            value_ = CComVariant(decoded.c_str());
            return S_OK;
        }
        if (kind == L"BOOL" && fields.size() == 3) {
            VARIANT_BOOL parsed = VARIANT_FALSE;
            if (!BooleanValue(fields[2], &parsed)) {
                return E_INVALIDARG;
            }
            value_.Clear();
            value_.vt = VT_BOOL;
            value_.boolVal = parsed;
            return S_OK;
        }
        if (kind == L"I2" && fields.size() == 3) {
            LONGLONG parsed = 0;
            if (!SignedValue(fields[2], &parsed) ||
                parsed < std::numeric_limits<SHORT>::min() ||
                parsed > std::numeric_limits<SHORT>::max()) {
                return E_INVALIDARG;
            }
            value_ = CComVariant(static_cast<SHORT>(parsed));
            return S_OK;
        }
        if (kind == L"UI2" && fields.size() == 3) {
            ULONGLONG parsed = 0;
            if (!UnsignedValue(fields[2], &parsed) ||
                parsed > std::numeric_limits<USHORT>::max()) {
                return E_INVALIDARG;
            }
            value_.Clear();
            value_.vt = VT_UI2;
            value_.uiVal = static_cast<USHORT>(parsed);
            return S_OK;
        }
        if (kind == L"I4" && fields.size() == 3) {
            LONGLONG parsed = 0;
            if (!SignedValue(fields[2], &parsed) ||
                parsed < std::numeric_limits<LONG>::min() ||
                parsed > std::numeric_limits<LONG>::max()) {
                return E_INVALIDARG;
            }
            value_ = CComVariant(static_cast<LONG>(parsed));
            return S_OK;
        }
        if (kind == L"UI4" && fields.size() == 3) {
            ULONGLONG parsed = 0;
            if (!UnsignedValue(fields[2], &parsed) ||
                parsed > std::numeric_limits<ULONG>::max()) {
                return E_INVALIDARG;
            }
            value_.Clear();
            value_.vt = VT_UI4;
            value_.ulVal = static_cast<ULONG>(parsed);
            return S_OK;
        }
        if (kind == L"I8" && fields.size() == 3) {
            LONGLONG parsed = 0;
            if (!SignedValue(fields[2], &parsed)) {
                return E_INVALIDARG;
            }
            value_.Clear();
            value_.vt = VT_I8;
            value_.llVal = parsed;
            return S_OK;
        }
        if (kind == L"UI8" && fields.size() == 3) {
            ULONGLONG parsed = 0;
            if (!UnsignedValue(fields[2], &parsed)) {
                return E_INVALIDARG;
            }
            value_.Clear();
            value_.vt = VT_UI8;
            value_.ullVal = parsed;
            return S_OK;
        }
        if ((kind == L"R4" || kind == L"R8") && fields.size() == 3) {
            double parsed = 0.0;
            if (!RealValue(fields[2], &parsed)) {
                return E_INVALIDARG;
            }
            value_.Clear();
            if (kind == L"R4") {
                value_.vt = VT_R4;
                value_.fltVal = static_cast<FLOAT>(parsed);
            } else {
                value_.vt = VT_R8;
                value_.dblVal = parsed;
            }
            return S_OK;
        }
        if (kind == L"BYREF_I2" && (fields.size() == 2 || fields.size() == 3)) {
            LONGLONG parsed = 0;
            if (fields.size() == 3 &&
                (!SignedValue(fields[2], &parsed) ||
                 parsed < std::numeric_limits<SHORT>::min() ||
                 parsed > std::numeric_limits<SHORT>::max())) {
                return E_INVALIDARG;
            }
            i2Value_ = static_cast<SHORT>(parsed);
            value_.Clear();
            value_.vt = VT_I2 | VT_BYREF;
            value_.piVal = &i2Value_;
            output_ = true;
            return S_OK;
        }
        if (kind == L"BYREF_I4" && (fields.size() == 2 || fields.size() == 3)) {
            LONGLONG parsed = 0;
            if (fields.size() == 3 &&
                (!SignedValue(fields[2], &parsed) ||
                 parsed < std::numeric_limits<LONG>::min() ||
                 parsed > std::numeric_limits<LONG>::max())) {
                return E_INVALIDARG;
            }
            i4Value_ = static_cast<LONG>(parsed);
            value_.Clear();
            value_.vt = VT_I4 | VT_BYREF;
            value_.plVal = &i4Value_;
            output_ = true;
            return S_OK;
        }
        if (kind == L"BYREF_UI4" && (fields.size() == 2 || fields.size() == 3)) {
            ULONGLONG parsed = 0;
            if (fields.size() == 3 &&
                (!UnsignedValue(fields[2], &parsed) ||
                 parsed > std::numeric_limits<ULONG>::max())) {
                return E_INVALIDARG;
            }
            ui4Value_ = static_cast<ULONG>(parsed);
            value_.Clear();
            value_.vt = VT_UI4 | VT_BYREF;
            value_.pulVal = &ui4Value_;
            output_ = true;
            return S_OK;
        }
        if (kind == L"BYREF_BOOL" && (fields.size() == 2 || fields.size() == 3)) {
            if (fields.size() == 3 && !BooleanValue(fields[2], &boolValue_)) {
                return E_INVALIDARG;
            }
            value_.Clear();
            value_.vt = VT_BOOL | VT_BYREF;
            value_.pboolVal = &boolValue_;
            output_ = true;
            return S_OK;
        }
        if (kind == L"BYREF_BSTR" && (fields.size() == 2 || fields.size() == 3)) {
            std::wstring decoded;
            if (fields.size() == 3 && !DecodeUtf8Base64(fields[2], &decoded)) {
                return E_INVALIDARG;
            }
            bstrValue_ = SysAllocStringLen(decoded.data(), static_cast<UINT>(decoded.size()));
            if (bstrValue_ == nullptr && !decoded.empty()) {
                return E_OUTOFMEMORY;
            }
            value_.Clear();
            value_.vt = VT_BSTR | VT_BYREF;
            value_.pbstrVal = &bstrValue_;
            output_ = true;
            return S_OK;
        }
        if (kind == L"BYREF_VARIANT" && fields.size() == 2) {
            byrefVariant_.Clear();
            value_.Clear();
            value_.vt = VT_VARIANT | VT_BYREF;
            value_.pvarVal = &byrefVariant_;
            output_ = true;
            return S_OK;
        }
        return E_INVALIDARG;
    }

    CComVariant Value() const {
        return value_;
    }

    bool IsOutput() const noexcept {
        return output_;
    }

    std::wstring OutputValue() const {
        if (value_.vt == (VT_VARIANT | VT_BYREF) && value_.pvarVal != nullptr) {
            return VariantText(*value_.pvarVal);
        }
        return VariantText(value_);
    }

    VARTYPE OutputType() const noexcept {
        if (value_.vt == (VT_VARIANT | VT_BYREF) && value_.pvarVal != nullptr) {
            return value_.pvarVal->vt;
        }
        return static_cast<VARTYPE>(value_.vt & ~VT_BYREF);
    }

private:
    HRESULT SetDispatch(IDispatch* const value) noexcept {
        if (value == nullptr) {
            return E_POINTER;
        }
        value_.Clear();
        value_.vt = VT_DISPATCH;
        value_.pdispVal = value;
        value_.pdispVal->AddRef();
        return S_OK;
    }

    CComVariant value_;
    CComVariant byrefVariant_;
    CComPtr<IDispatch> dispatch_;
    SHORT i2Value_ = 0;
    LONG i4Value_ = 0;
    ULONG ui4Value_ = 0;
    VARIANT_BOOL boolValue_ = VARIANT_FALSE;
    BSTR bstrValue_ = nullptr;
    bool output_ = false;
};

PreparedAutomationArguments::PreparedAutomationArguments() = default;
PreparedAutomationArguments::~PreparedAutomationArguments() = default;
PreparedAutomationArguments::PreparedAutomationArguments(
    PreparedAutomationArguments&&) noexcept = default;
PreparedAutomationArguments& PreparedAutomationArguments::operator=(
    PreparedAutomationArguments&&) noexcept = default;

HRESULT PreparedAutomationArguments::Parse(
    IDispatch* const hwp,
    IDispatch* const target,
    const std::vector<std::wstring>& lines) noexcept {
    try {
        arguments_.clear();
        arguments_.reserve(lines.size());
        for (const std::wstring& line : lines) {
            std::unique_ptr<AutomationArgument> argument =
                std::make_unique<AutomationArgument>();
            const HRESULT status = argument->Initialize(hwp, target, Fields(line));
            if (FAILED(status)) {
                arguments_.clear();
                return status;
            }
            arguments_.push_back(std::move(argument));
        }
        return S_OK;
    } catch (const std::bad_alloc&) {
        arguments_.clear();
        return E_OUTOFMEMORY;
    } catch (...) {
        arguments_.clear();
        return E_UNEXPECTED;
    }
}

std::vector<CComVariant> PreparedAutomationArguments::Values() const {
    std::vector<CComVariant> values;
    values.reserve(arguments_.size());
    for (const std::unique_ptr<AutomationArgument>& argument : arguments_) {
        values.push_back(argument->Value());
    }
    return values;
}

std::wstring PreparedAutomationArguments::Outputs() const {
    std::wostringstream output;
    for (size_t index = 0; index < arguments_.size(); ++index) {
        const AutomationArgument& argument = *arguments_[index];
        if (argument.IsOutput()) {
            output << L"\nOUT\t" << index
                   << L'\t' << argument.OutputType()
                   << L'\t' << argument.OutputValue();
        }
    }
    return output.str();
}

}

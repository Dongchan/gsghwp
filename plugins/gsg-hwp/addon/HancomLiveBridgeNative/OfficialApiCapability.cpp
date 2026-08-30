#include "OfficialApiCapability.h"

#include "ComState.h"
#include "DocumentGraphControls.h"
#include "DocumentGraphCapture.h"
#include "DocumentGraphCaptureRecords.h"
#include "DocumentGraphCaptureSpool.h"
#include "DocumentGraphEffectiveProperties.h"
#include "DocumentGraphImages.h"
#include "DocumentGraphLayout.h"
#include "DocumentGraphStories.h"
#include "DocumentGraphTables.h"
#include "DocumentGraphText.h"
#include "DispatchInvoke.h"
#include "OfficialApiState.h"
#include "TableInspection.h"

#include <atlbase.h>
#include <bcrypt.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <map>
#include <memory>
#include <numeric>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <type_traits>
#include <vector>

namespace hancom::graph::capture {

class NativeCaptionLocationIssuer final {
public:
    static void Clear(ImageObservation* const image) noexcept {
        if (image != nullptr) {
            image->captionLocationQualification = {};
        }
    }

    static bool Issue(
        const CaptureIdentityArena& capture,
        ImageObservation* const image,
        const NativePosition start) noexcept {
        if (image == nullptr) {
            return false;
        }
        try {
            image->captionLocationQualification =
                CaptionLocationQualification(
                    capture.captionProvenance_,
                    image->ctrlId,
                    image->instanceIdPresent,
                    image->instanceId,
                    image->headCtrlOrdinal,
                    image->anchor,
                    start);
            return true;
        } catch (...) {
            image->captionLocationQualification = {};
            return false;
        }
    }
};

} // namespace hancom::graph::capture

namespace hancom::official_api::capability {

namespace {

constexpr GUID kHwpObjectLib = {
    0x7D2B6F3C, 0x1D95, 0x4E0C,
    {0xBF, 0x5A, 0x5E, 0xE5, 0x64, 0x18, 0x6F, 0xBC}};
constexpr GUID kHwpAutomationLib = {
    0xCB0895B7, 0xBAC9, 0x4E39,
    {0xA7, 0x8F, 0x08, 0x30, 0x20, 0x5F, 0xFF, 0x57}};
constexpr wchar_t kHwpObjectHash[] =
    L"9d3f8dc5743464eae621a62dc224eba13f11c75ec545cddcc6337d808d5bb3e9";
constexpr wchar_t kHwpAutomationHash[] =
    L"7dc8467c682fd415bfc74ba5382cedae1d0c5cc82cc2cfde1eccdd91feeb217b";
thread_local TableGraphDiagnosticCounters g_tableGraphDiagnosticCounters;

std::wstring BstrText(BSTR value) {
    return value == nullptr ? std::wstring() :
        std::wstring(value, SysStringLen(value));
}

void CaptureException(EXCEPINFO* source, ExceptionObservation* target) noexcept {
    target->code = source->wCode;
    target->reserved = source->wReserved;
    target->reservedPointerPresent = source->pvReserved != nullptr;
    target->deferredFillPresent = source->pfnDeferredFillIn != nullptr;
    if (source->pfnDeferredFillIn != nullptr) {
        target->deferredFillStatus = source->pfnDeferredFillIn(source);
    }
    target->scode = source->scode;
    target->helpContext = source->dwHelpContext;
    target->sourcePresent = source->bstrSource != nullptr;
    target->descriptionPresent = source->bstrDescription != nullptr;
    target->helpFilePresent = source->bstrHelpFile != nullptr;
    target->source = BstrText(source->bstrSource);
    target->description = BstrText(source->bstrDescription);
    target->helpFile = BstrText(source->bstrHelpFile);
    SysFreeString(source->bstrSource);
    SysFreeString(source->bstrDescription);
    SysFreeString(source->bstrHelpFile);
    *source = {};
}

bool ParameterAccepts(const TYPEDESC& formal, const VARIANTARG& actual) noexcept {
    // A formal VARIANT is a slot whose contained value is scenario-specific.
    // It must not be confused with an argument whose wire VARTYPE is VT_VARIANT.
    if (formal.vt == VT_VARIANT) return (actual.vt & VT_BYREF) == 0;
    if (formal.vt == VT_PTR && formal.lptdesc != nullptr) {
        return actual.vt == static_cast<VARTYPE>(formal.lptdesc->vt | VT_BYREF);
    }
    return formal.vt == actual.vt;
}

USHORT ExpectedParameterFlags(const CallSpec& spec, const SHORT parameter,
                              const VARIANTARG& argument) noexcept {
    if (std::wstring(spec.name) == L"GetAnchorPos") return PARAMFLAG_NONE;
    if (std::wstring(spec.name) == L"MoveToCell" && parameter == 2) {
        return PARAMFLAG_FOPT;
    }
    return (argument.vt & VT_BYREF) != 0 ? PARAMFLAG_FOUT : PARAMFLAG_FIN;
}

HRESULT ValidateTypeInfo(IDispatch* target, const CallSpec& spec,
                         const std::vector<VARIANTARG>& arguments) noexcept {
    CComPtr<ITypeInfo> info;
    HRESULT status = target->GetTypeInfo(0, 0, &info);
    if (FAILED(status)) return status;
    TYPEATTR* attributes = nullptr;
    status = info->GetTypeAttr(&attributes);
    if (FAILED(status)) return status;
    bool found = false;
    bool exact = false;
    for (UINT index = 0; index < attributes->cFuncs; ++index) {
        FUNCDESC* function = nullptr;
        if (FAILED(info->GetFuncDesc(index, &function))) continue;
        if (function->memid == spec.dispid) {
            found = true;
            BSTR name = nullptr;
            UINT count = 0;
            const HRESULT nameStatus = info->GetNames(
                function->memid, &name, 1, &count);
            exact = SUCCEEDED(nameStatus) && count == 1 && name != nullptr &&
                BstrText(name) == spec.name &&
                static_cast<WORD>(function->invkind) == spec.invkind &&
                function->cParams == spec.arity &&
                function->elemdescFunc.tdesc.vt == spec.resultType;
            for (SHORT parameter = 0; exact && parameter < function->cParams;
                 ++parameter) {
                const ELEMDESC& formal = function->lprgelemdescParam[parameter];
                const VARIANTARG& actual = arguments[static_cast<size_t>(parameter)];
                exact = ParameterAccepts(formal.tdesc, actual) &&
                    formal.paramdesc.wParamFlags ==
                        ExpectedParameterFlags(spec, parameter, actual);
            }
            SysFreeString(name);
        }
        info->ReleaseFuncDesc(function);
        if (exact) break;
    }
    info->ReleaseTypeAttr(attributes);
    return exact ? S_OK : (found ? TYPE_E_TYPEMISMATCH : DISP_E_MEMBERNOTFOUND);
}

SemanticStatus ClassifyResult(const CallSpec& spec, const VARIANT& value) noexcept {
    if (spec.resultType != VT_VARIANT && value.vt != spec.resultType) {
        return SemanticStatus::MalformedOutput;
    }
    if (spec.resultType == VT_VARIANT && value.vt == VT_EMPTY) {
        return SemanticStatus::MalformedOutput;
    }
    if (value.vt == VT_BOOL && value.boolVal == VARIANT_FALSE) {
        return SemanticStatus::FalseResult;
    }
    if ((value.vt == VT_DISPATCH && value.pdispVal == nullptr) ||
        (value.vt == VT_UNKNOWN && value.punkVal == nullptr) ||
        (value.vt == VT_BSTR && value.bstrVal == nullptr) || value.vt == VT_NULL) {
        return SemanticStatus::NullResult;
    }
    return SemanticStatus::Pass;
}

const wchar_t* SemanticName(SemanticStatus status) noexcept {
    switch (status) {
    case SemanticStatus::Pass: return L"PASS";
    case SemanticStatus::TypeInfoFailure: return L"TYPEINFO_FAILURE";
    case SemanticStatus::NameFailure: return L"NAME_FAILURE";
    case SemanticStatus::ContractMismatch: return L"CONTRACT_MISMATCH";
    case SemanticStatus::ComFailure: return L"COM_FAILURE";
    case SemanticStatus::MalformedOutput: return L"MALFORMED_OUTPUT";
    case SemanticStatus::FalseResult: return L"FALSE_RESULT";
    case SemanticStatus::NullResult: return L"NULL_RESULT";
    case SemanticStatus::NoProgress: return L"NO_PROGRESS";
    case SemanticStatus::RepeatedState: return L"REPEATED_STATE";
    case SemanticStatus::Inconclusive: return L"INCONCLUSIVE";
    case SemanticStatus::NotObserved: return L"NOT_OBSERVED";
    }
    return L"UNKNOWN";
}

std::wstring EncodedBstr(const bool present, const std::wstring& value) {
    if (!present) return L"NULL";
    std::wostringstream output;
    output << L"BSTR:" << value.size() << L":" << std::hex << std::setfill(L'0');
    for (const wchar_t codeUnit : value) {
        output << std::setw(4) << static_cast<unsigned short>(codeUnit);
    }
    return output.str();
}

std::wstring VariantText(const VARIANT& value) {
    std::wostringstream output;
    output << value.vt << L":";
    switch (value.vt) {
    case VT_EMPTY: output << L"EMPTY"; break;
    case VT_NULL: output << L"NULL"; break;
    case VT_BOOL: output << (value.boolVal == VARIANT_FALSE ? 0 : 1); break;
    case VT_I2: output << value.iVal; break;
    case VT_UI2: output << value.uiVal; break;
    case VT_I4: output << value.lVal; break;
    case VT_INT: output << value.intVal; break;
    case VT_BSTR:
        output << (value.bstrVal == nullptr ? L"<NULL>" : BstrText(value.bstrVal));
        break;
    case VT_DISPATCH:
        output << (value.pdispVal == nullptr ? L"<NULL>" : L"DISPATCH");
        break;
    default: output << L"PRESERVED"; break;
    }
    return output.str();
}

void AppendCall(std::wostringstream& output, const wchar_t* owner,
                const CallSpec& spec, const RawCall& call) {
    output << L"CALL\t" << owner << L'.' << spec.name << L'\t'
           << SemanticName(call.semantic) << L'\t'
           << static_cast<LONG>(call.typeInfoStatus) << L'\t'
           << static_cast<LONG>(call.getIdsStatus) << L'\t'
           << static_cast<LONG>(call.invokeStatus) << L'\t'
           << call.argumentError << L'\t' << VariantText(call.result) << L'\t'
           << call.exception.code << L'\t' << call.exception.reserved << L'\t'
           << (call.exception.reservedPointerPresent ? 1 : 0) << L'\t'
           << (call.exception.deferredFillPresent ? 1 : 0) << L'\t'
           << static_cast<LONG>(call.exception.deferredFillStatus) << L'\t'
           << static_cast<LONG>(call.exception.scode) << L'\t'
           << call.exception.helpContext << L'\t'
           << EncodedBstr(call.exception.sourcePresent, call.exception.source) << L'\t'
           << EncodedBstr(call.exception.descriptionPresent, call.exception.description) << L'\t'
           << EncodedBstr(call.exception.helpFilePresent, call.exception.helpFile) << L'\n';
    for (size_t index = 0; index < call.byRefOutputs.size(); ++index) {
        const ByRefObservation& observed = call.byRefOutputs[index];
        output << L"OUT\t" << index << L'\t' << observed.declaredType << L'\t'
               << (observed.pointerNull ? L"NULL_POINTER" :
                   VariantText(observed.value)) << L'\n';
    }
}

VARIANTARG LongArgument(LONG value) noexcept {
    VARIANTARG result{};
    result.vt = VT_I4;
    result.lVal = value;
    return result;
}

VARIANTARG IntArgument(INT value) noexcept {
    VARIANTARG result{};
    result.vt = VT_INT;
    result.intVal = value;
    return result;
}

bool SameIntegralValue(const VARIANT& left, const VARIANT& right) noexcept {
    const auto value = [](const VARIANT& item, LONGLONG* output) noexcept {
        switch (item.vt) {
        case VT_BOOL: *output = item.boolVal == VARIANT_FALSE ? 0 : 1; return true;
        case VT_UI2: *output = item.uiVal; return true;
        case VT_I2: *output = item.iVal; return true;
        case VT_I4: *output = item.lVal; return true;
        case VT_INT: *output = item.intVal; return true;
        default: return false;
        }
    };
    LONGLONG leftValue = 0;
    LONGLONG rightValue = 0;
    return value(left, &leftValue) && value(right, &rightValue) &&
        leftValue == rightValue;
}

bool AsDispatch(const RawCall& call, CComPtr<IDispatch>& target) noexcept {
    target.Release();
    if (call.invokeStatus != S_OK || call.result.vt != VT_DISPATCH ||
        call.result.pdispVal == nullptr) return false;
    target = call.result.pdispVal;
    return true;
}

struct ControlToken {
    std::wstring type;
    std::wstring instance;
    bool anchorObserved = false;
    LONG list = 0;
    LONG paragraph = 0;
    LONG character = 0;

    bool operator==(const ControlToken& other) const noexcept {
        return std::tie(type, instance, anchorObserved, list, paragraph, character) ==
            std::tie(
                other.type,
                other.instance,
                other.anchorObserved,
                other.list,
                other.paragraph,
                other.character);
    }
};

bool ReadAnchorItem(
    IDispatch* const anchor,
    const wchar_t* const name,
    LONG* const value) noexcept {
    CComVariant raw;
    return SUCCEEDED(hancom::dispatch::Method(
            anchor, L"Item", {CComVariant(name)}, &raw)) &&
        SUCCEEDED(hancom::dispatch::AsLong(raw, value));
}

bool BuildControlToken(
    const RawCall& idCall,
    const RawCall& instanceCall,
    const RawCall& anchorCall,
    ControlToken* const token) noexcept {
    if (token == nullptr || idCall.result.vt != VT_BSTR ||
        instanceCall.result.vt != VT_BSTR) {
        return false;
    }
    token->type = BstrText(idCall.result.bstrVal);
    token->instance = BstrText(instanceCall.result.bstrVal);
    CComPtr<IDispatch> anchor;
    token->anchorObserved = AsDispatch(anchorCall, anchor) &&
        ReadAnchorItem(anchor, L"List", &token->list) &&
        ReadAnchorItem(anchor, L"Para", &token->paragraph) &&
        ReadAnchorItem(anchor, L"Pos", &token->character);
    return !token->type.empty() &&
        (!token->instance.empty() || token->anchorObserved);
}

void AppendControlToken(
    std::wostringstream& output,
    const wchar_t* const direction,
    const ControlToken& token) {
    output << L"CONTROL_TOKEN\t" << direction << L'\t'
           << EncodedBstr(true, token.type) << L'\t'
           << EncodedBstr(true, token.instance) << L'\t'
           << (token.anchorObserved ? L"ANCHOR" : L"NO_ANCHOR") << L'\t'
           << token.list << L'\t' << token.paragraph << L'\t'
           << token.character << L'\n';
}

HRESULT TypeInfoByName(ITypeLib* library, const wchar_t* name,
                       CComPtr<ITypeInfo>& found) noexcept {
    found.Release();
    const UINT count = library->GetTypeInfoCount();
    for (UINT index = 0; index < count; ++index) {
        BSTR candidate = nullptr;
        if (FAILED(library->GetDocumentation(
                index, &candidate, nullptr, nullptr, nullptr))) continue;
        const bool matched = candidate != nullptr && BstrText(candidate) == name;
        SysFreeString(candidate);
        if (matched) return library->GetTypeInfo(index, &found);
    }
    return TYPE_E_ELEMENTNOTFOUND;
}

void AppendTypeDescription(std::wostringstream& output, const TYPEDESC& type) {
    output << type.vt;
    if ((type.vt == VT_PTR || type.vt == VT_SAFEARRAY) && type.lptdesc != nullptr) {
        output << L'(';
        AppendTypeDescription(output, *type.lptdesc);
        output << L')';
    } else if (type.vt == VT_CARRAY && type.lpadesc != nullptr) {
        output << L'(';
        AppendTypeDescription(output, type.lpadesc->tdescElem);
        output << L";DIM=" << type.lpadesc->cDims;
        for (USHORT dimension = 0; dimension < type.lpadesc->cDims; ++dimension) {
            output << L',' << type.lpadesc->rgbounds[dimension].cElements
                   << L'@' << type.lpadesc->rgbounds[dimension].lLbound;
        }
        output << L')';
    } else if (type.vt == VT_USERDEFINED) {
        output << L'(' << static_cast<unsigned long>(type.hreftype) << L')';
    }
}

std::wstring FunctionSignature(const FUNCDESC& function) {
    std::wostringstream output;
    output << L"D=" << function.memid << L";I=" << function.invkind
           << L";N=" << function.cParams << L";R=";
    AppendTypeDescription(output, function.elemdescFunc.tdesc);
    output << L";A=";
    for (SHORT parameter = 0; parameter < function.cParams; ++parameter) {
        if (parameter != 0) output << L',';
        AppendTypeDescription(output, function.lprgelemdescParam[parameter].tdesc);
        output << L'/' << function.lprgelemdescParam[parameter].paramdesc.wParamFlags;
    }
    return output.str();
}

std::wstring ExpectedLibrarySignature(const wchar_t* owner, const CallSpec& spec) {
    std::wostringstream prefix;
    prefix << L"D=" << spec.dispid << L";I=" << spec.invkind
           << L";N=" << spec.arity << L";R=" << spec.resultType << L";A=";
    const std::wstring name = spec.name;
    const std::wstring ownerName = owner;
    if (name == L"GetTableCellAddr") prefix << L"3/1";
    else if (name == L"GetRowColCount") prefix << L"22/1";
    else if (name == L"MoveToCell") prefix << L"22/1,22/1,22/16";
    else if (name == L"Item") prefix << L"8/1";
    else if (name == L"GetSelectedPos") {
        const int flags = ownerName == L"IHwpObject" ? 2 : 1;
        for (int parameter = 0; parameter < 6; ++parameter) {
            if (parameter != 0) prefix << L',';
            prefix << L"26(3)/" << flags;
        }
    }
    return prefix.str();
}

HRESULT ValidateLibraryContract(ITypeLib* library, const wchar_t* owner,
                                const CallSpec& spec,
                                std::wstring* observedSignature) noexcept {
    CComPtr<ITypeInfo> info;
    HRESULT status = TypeInfoByName(library, owner, info);
    if (FAILED(status)) return status;
    TYPEATTR* attributes = nullptr;
    status = info->GetTypeAttr(&attributes);
    if (FAILED(status)) return status;
    bool exact = false;
    for (UINT index = 0; index < attributes->cFuncs; ++index) {
        FUNCDESC* function = nullptr;
        if (FAILED(info->GetFuncDesc(index, &function))) continue;
        if (function->memid == spec.dispid &&
            static_cast<WORD>(function->invkind) == spec.invkind) {
            const std::wstring signature = FunctionSignature(*function);
            if (observedSignature != nullptr) *observedSignature = signature;
            BSTR member = nullptr;
            UINT names = 0;
            exact = signature == ExpectedLibrarySignature(owner, spec) &&
                SUCCEEDED(info->GetNames(spec.dispid, &member, 1, &names)) &&
                names == 1 && member != nullptr && BstrText(member) == spec.name;
            SysFreeString(member);
        }
        info->ReleaseFuncDesc(function);
        if (exact) break;
    }
    info->ReleaseTypeAttr(attributes);
    return exact ? S_OK : TYPE_E_TYPEMISMATCH;
}

std::wstring RegisteredTypeLibPath(REFGUID libraryId) {
    BSTR raw = nullptr;
    const HRESULT status = QueryPathOfRegTypeLib(
        libraryId, 1, 0, 0, &raw);
    if (FAILED(status) || raw == nullptr) return std::wstring();
    const std::wstring path = BstrText(raw);
    SysFreeString(raw);
    return path;
}

std::wstring Sha256File(const std::wstring& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) return std::wstring();
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD objectBytes = 0;
    DWORD written = 0;
    if (BCryptOpenAlgorithmProvider(
            &algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) != 0 ||
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
            reinterpret_cast<PUCHAR>(&objectBytes), sizeof(objectBytes),
            &written, 0) != 0) {
        if (algorithm != nullptr) BCryptCloseAlgorithmProvider(algorithm, 0);
        return std::wstring();
    }
    std::vector<UCHAR> object(objectBytes);
    if (BCryptCreateHash(algorithm, &hash, object.data(), objectBytes,
            nullptr, 0, 0) != 0) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
        return std::wstring();
    }
    std::array<UCHAR, 65536> block{};
    while (input) {
        input.read(reinterpret_cast<char*>(block.data()), block.size());
        const std::streamsize count = input.gcount();
        if (count > 0 && BCryptHashData(hash, block.data(),
                static_cast<ULONG>(count), 0) != 0) {
            BCryptDestroyHash(hash);
            BCryptCloseAlgorithmProvider(algorithm, 0);
            return std::wstring();
        }
    }
    std::array<UCHAR, 32> digest{};
    const NTSTATUS finished = BCryptFinishHash(
        hash, digest.data(), static_cast<ULONG>(digest.size()), 0);
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    if (finished != 0) return std::wstring();
    std::wostringstream output;
    output << std::hex << std::setfill(L'0');
    for (const UCHAR byte : digest) output << std::setw(2) << unsigned(byte);
    return output.str();
}

std::wstring ProbeTypeLibExtension() {
    struct Library { const GUID* id; const wchar_t* label; const wchar_t* hash; };
    static constexpr Library libraries[] = {
        {&kHwpObjectLib, L"HwpObject", kHwpObjectHash},
        {&kHwpAutomationLib, L"HwpAutomation", kHwpAutomationHash},
    };
    static constexpr struct Contract {
        const wchar_t* owner;
        CallSpec spec;
        const wchar_t* authority;
    } contracts[] = {
        {L"IDHwpCtrlCode", {L"GetCtrlInstID",15001,DISPATCH_METHOD,0,VT_BSTR}, L"TYPELIB_EXTENSION"},
        {L"IHwpObject", {L"GetTableCellAddr",10049,DISPATCH_METHOD,1,VT_I4}, L"TYPELIB_EXTENSION"},
        {L"IHwpObject", {L"GetRowColCount",10051,DISPATCH_METHOD,1,VT_INT}, L"TYPELIB_EXTENSION"},
        {L"IHwpObject", {L"MoveToCell",10052,DISPATCH_METHOD,3,VT_BOOL}, L"TYPELIB_EXTENSION"},
        {L"IHwpObject", {L"GetCellRangeIndex",10053,DISPATCH_METHOD,0,VT_DISPATCH}, L"TYPELIB_EXTENSION"},
        {L"IHwpObject", {L"GetSelectedPos",30105,DISPATCH_METHOD,6,VT_BOOL}, L"TYPELIB_DISCREPANCY"},
        {L"IHwpAutomation", {L"GetTableCellAddr",30249,DISPATCH_METHOD,1,VT_I4}, L"TYPELIB_EXTENSION"},
        {L"IHwpAutomation", {L"GetRowColCount",30251,DISPATCH_METHOD,1,VT_INT}, L"TYPELIB_EXTENSION"},
        {L"IHwpAutomation", {L"MoveToCell",30252,DISPATCH_METHOD,3,VT_BOOL}, L"TYPELIB_EXTENSION"},
        {L"IHwpAutomation", {L"GetCellRangeIndex",30253,DISPATCH_METHOD,0,VT_DISPATCH}, L"TYPELIB_EXTENSION"},
        {L"IHwpAutomation", {L"GetSelectedPos",30105,DISPATCH_METHOD,6,VT_BOOL}, L"TYPELIB_DISCREPANCY"},
        {L"HCellRangeRowCol", {L"StartRow",16415,DISPATCH_PROPERTYGET,0,VT_I4}, L"TYPELIB_EXTENSION"},
        {L"HCellRangeRowCol", {L"EndRow",16416,DISPATCH_PROPERTYGET,0,VT_I4}, L"TYPELIB_EXTENSION"},
        {L"HCellRangeRowCol", {L"StartCol",16417,DISPATCH_PROPERTYGET,0,VT_I4}, L"TYPELIB_EXTENSION"},
        {L"HCellRangeRowCol", {L"EndCol",16418,DISPATCH_PROPERTYGET,0,VT_I4}, L"TYPELIB_EXTENSION"},
        {L"HTable", {L"RepeatHeader",16928,DISPATCH_PROPERTYGET,0,VT_UI2}, L"TYPELIB_EXTENSION"},
        {L"HCell", {L"Header",16929,DISPATCH_PROPERTYGET,0,VT_UI2}, L"TYPELIB_EXTENSION"},
        {L"IDHwpParameterSet", {L"Item",15005,DISPATCH_METHOD,1,VT_VARIANT}, L"TYPELIB_EXTENSION"},
    };
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tTYPELIB_EXTENSION\tV1\n"
           << L"AUTHORITY\tPDF376\tCOMPACT_DENOMINATOR_UNCHANGED\n"
           << L"AUTHORITY\tTYPELIB_EXTENSION\tINSTALLED_HASH_BOUND\n"
           << L"AUTHORITY\tCAPABILITY_SCENARIO\tLIVE_OBSERVATION_ONLY\n";
    bool all = true;
    for (const Library& item : libraries) {
        const std::wstring path = RegisteredTypeLibPath(*item.id);
        const std::wstring hash = Sha256File(path);
        CComPtr<ITypeLib> library;
        const HRESULT load = path.empty() ? TYPE_E_CANTLOADLIBRARY :
            LoadTypeLibEx(path.c_str(), REGKIND_NONE, &library);
        const bool hashMatched = hash == item.hash;
        output << L"TYPELIB\t" << item.label << L"\t1.0\tWIN32\t"
               << path << L'\t' << hash << L'\t'
               << (hashMatched && SUCCEEDED(load) ? L"PASS" : L"FAIL") << L'\n';
        all = all && hashMatched && SUCCEEDED(load);
        if (SUCCEEDED(load)) {
            for (const Contract& contract : contracts) {
                const bool wrongRoot =
                    (std::wstring(item.label) == L"HwpAutomation" &&
                     std::wstring(contract.owner) == L"IHwpObject") ||
                    (std::wstring(item.label) == L"HwpObject" &&
                     std::wstring(contract.owner) == L"IHwpAutomation");
                if (wrongRoot) {
                    output << L"CONTRACT\t" << item.label << L'\t'
                           << contract.owner << L'.' << contract.spec.name
                           << L"\tNOT_APPLICABLE\tROOT_SPECIFIC\n";
                    continue;
                }
                std::wstring observedSignature;
                const HRESULT status = ValidateLibraryContract(
                    library, contract.owner, contract.spec, &observedSignature);
                output << L"CONTRACT\t" << item.label << L'\t'
                       << contract.owner << L'.' << contract.spec.name << L'\t'
                       << contract.authority << L'\t'
                       << (SUCCEEDED(status) ? L"PASS" : L"FAIL") << L'\t'
                       << static_cast<LONG>(status) << L'\t'
                       << L"EXPECTED=" << ExpectedLibrarySignature(
                            contract.owner, contract.spec) << L'\t'
                       << L"OBSERVED=" << observedSignature << L'\n';
                all = all && SUCCEEDED(status);
            }
        }
    }
    output << L"TYPELIB_DISCREPANCY\tGetSelectedPos\t"
           << L"HwpObject=6xPTR_I4_PARAMFLAG_FOUT\t"
           << L"HwpAutomation=6xPTR_I4_PARAMFLAG_FIN\tOBSERVED\n"
           << L"RESULT\t" << (all ? L"PASS" : L"FAIL");
    return output.str();
}

FixtureCoverage ParseCoverage(const std::vector<std::wstring>& lines) {
    FixtureCoverage result;
    for (const std::wstring& line : lines) {
        const size_t first = line.find(L'\t');
        const size_t second = first == std::wstring::npos ? first :
            line.find(L'\t', first + 1);
        if (first == std::wstring::npos || second == std::wstring::npos ||
            line.substr(0, first) != L"FIXTURE_COVERAGE" ||
            line.substr(second + 1) != L"OBSERVED") continue;
        const std::wstring name = line.substr(first + 1, second - first - 1);
        if (name == L"horizontal_merge") result.horizontalMerge = true;
        else if (name == L"vertical_merge") result.verticalMerge = true;
        else if (name == L"rectangular_merge") result.rectangularMerge = true;
        else if (name == L"terminal_merge") result.terminalMerge = true;
        else if (name == L"nested_table") result.nestedTable = true;
        else if (name == L"shared_header_footer") result.sharedHeaderFooter = true;
        else if (name == L"notes") result.notes = true;
        else if (name == L"text_boxes") result.textBoxes = true;
        else if (name == L"captions") result.captions = true;
    }
    return result;
}

bool ContainmentCoverage(const FixtureCoverage& value) noexcept {
    return value.sharedHeaderFooter && value.notes && value.textBoxes && value.captions;
}

bool TableCoverage(const FixtureCoverage& value) noexcept {
    return value.horizontalMerge && value.verticalMerge && value.rectangularMerge &&
        value.terminalMerge && value.nestedTable;
}

std::wstring ProbeContainment(IDispatch* hwp) {
    static constexpr CallSpec head{L"HeadCtrl",9,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec last{L"LastCtrl",10,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec parent{L"ParentCtrl",13,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec next{L"Next",4,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec prev{L"Prev",5,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec ctrlId{L"CtrlID",2,DISPATCH_PROPERTYGET,0,VT_BSTR};
    static constexpr CallSpec ctrlCh{L"CtrlCh",1,DISPATCH_PROPERTYGET,0,VT_I4};
    static constexpr CallSpec hasList{L"HasList",3,DISPATCH_PROPERTYGET,0,VT_BOOL};
    static constexpr CallSpec anchor{L"GetAnchorPos",15000,DISPATCH_METHOD,1,VT_DISPATCH};
    static constexpr CallSpec instance{L"GetCtrlInstID",15001,DISPATCH_METHOD,0,VT_BSTR};
    static constexpr CallSpec getPos{L"GetPos",10020,DISPATCH_METHOD,3,VT_VOID};
    static constexpr CallSpec setPos{L"SetPos",10021,DISPATCH_METHOD,3,VT_BOOL};
    static constexpr CallSpec initScan{L"InitScan",10017,DISPATCH_METHOD,6,VT_BOOL};
    static constexpr CallSpec getText{L"GetText",10019,DISPATCH_METHOD,1,VT_I4};
    static constexpr CallSpec releaseScan{L"ReleaseScan",10018,DISPATCH_METHOD,0,VT_VOID};
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tCONTAINMENT\tV1\n";
    const RawCall headCall = InvokeOneShot(hwp, head, {}, true);
    const RawCall lastCall = InvokeOneShot(hwp, last, {}, true);
    const RawCall parentCall = InvokeOneShot(hwp, parent, {}, true);
    AppendCall(output, L"IHwpObject", head, headCall);
    AppendCall(output, L"IHwpObject", last, lastCall);
    AppendCall(output, L"IHwpObject", parent, parentCall);
    CComPtr<IDispatch> current;
    CComPtr<IDispatch> reverseCurrent;
    if (!AsDispatch(headCall, current) || !AsDispatch(lastCall, reverseCurrent)) {
        output << L"TERMINAL\t" << SemanticName(headCall.semantic)
               << L"\nRESULT\tINCONCLUSIVE";
        return output.str();
    }
    std::vector<CComPtr<IUnknown>> forwardHeld;
    std::set<IUnknown*> forwardWrappers;
    std::vector<ControlToken> forwardTokens;
    bool complete = false;
    bool tokenAmbiguous = false;
    for (size_t ordinal = 0; current != nullptr && ordinal < 100'000; ++ordinal) {
        CComPtr<IUnknown> identity;
        if (FAILED(current->QueryInterface(IID_IUnknown,
                reinterpret_cast<void**>(&identity))) || identity == nullptr) {
            output << L"TERMINAL\tCOM_FAILURE\nRESULT\tFAIL";
            return output.str();
        }
        if (!forwardWrappers.insert(identity).second) {
            output << L"TERMINAL\tREPEATED_STATE\nRESULT\tFAIL";
            return output.str();
        }
        forwardHeld.push_back(identity);
        const RawCall idCall = InvokeOneShot(current, ctrlId, {}, true);
        const RawCall chCall = InvokeOneShot(current, ctrlCh, {}, true);
        const RawCall listCall = InvokeOneShot(current, hasList, {}, true);
        const RawCall prevCall = InvokeOneShot(current, prev, {}, true);
        const RawCall anchorCall = InvokeOneShot(current, anchor, {LongArgument(0)}, true);
        const RawCall instanceCall = InvokeOneShot(current, instance, {}, true);
        AppendCall(output, L"IDHwpCtrlCode", ctrlId, idCall);
        AppendCall(output, L"IDHwpCtrlCode", ctrlCh, chCall);
        AppendCall(output, L"IDHwpCtrlCode", hasList, listCall);
        AppendCall(output, L"IDHwpCtrlCode", prev, prevCall);
        AppendCall(output, L"IDHwpCtrlCode", anchor, anchorCall);
        AppendCall(output, L"IDHwpCtrlCode", instance, instanceCall);
        output << L"OBSERVATION\tGetCtrlInstID\tSESSION_ONLY\n";
        const bool mandatory = idCall.semantic == SemanticStatus::Pass &&
            chCall.semantic == SemanticStatus::Pass &&
            (listCall.semantic == SemanticStatus::Pass ||
             listCall.semantic == SemanticStatus::FalseResult) &&
            (prevCall.semantic == SemanticStatus::Pass ||
             prevCall.semantic == SemanticStatus::NullResult) &&
            anchorCall.semantic == SemanticStatus::Pass;
        if (!mandatory) {
            output << L"TERMINAL\tMALFORMED_OUTPUT\nRESULT\tFAIL";
            return output.str();
        }
        ControlToken token;
        if (BuildControlToken(idCall, instanceCall, anchorCall, &token)) {
            forwardTokens.push_back(token);
            AppendControlToken(output, L"FORWARD", token);
        } else {
            tokenAmbiguous = true;
            output << L"CONTROL_TOKEN\tFORWARD\tAMBIGUOUS\tORDINAL="
                   << ordinal << L'\n';
        }
        if (listCall.semantic == SemanticStatus::Pass) {
            output << L"CONTROL_CHILD_LIST\tOBSERVED\tEXACT_OWNER=NOT_EXPOSED\n";
        }
        const RawCall nextCall = InvokeOneShot(current, next, {}, true);
        AppendCall(output, L"IDHwpCtrlCode", next, nextCall);
        if (nextCall.semantic == SemanticStatus::NullResult) {
            complete = true;
            output << L"TERMINAL\tNULL_END\n";
            break;
        }
        CComPtr<IDispatch> following;
        if (!AsDispatch(nextCall, following)) {
            output << L"TERMINAL\t" << SemanticName(nextCall.semantic)
                   << L"\nRESULT\tFAIL";
            return output.str();
        }
        current = following;
    }
    if (!complete) {
        output << L"TERMINAL\tLIMIT_REACHED\nRESULT\tFAIL";
        return output.str();
    }
    std::set<IUnknown*> reverseWrappers;
    std::vector<CComPtr<IUnknown>> reverseHeld;
    std::vector<ControlToken> reverseTokens;
    bool reverseComplete = false;
    for (size_t ordinal = 0;
         reverseCurrent != nullptr && ordinal < 100'000;
         ++ordinal) {
        CComPtr<IUnknown> identity;
        if (FAILED(reverseCurrent->QueryInterface(IID_IUnknown,
                reinterpret_cast<void**>(&identity))) || identity == nullptr ||
            !reverseWrappers.insert(identity).second) {
            output << L"REVERSE_TERMINAL\tREPEATED_STATE_OR_COM_FAILURE\nRESULT\tFAIL";
            return output.str();
        }
        reverseHeld.push_back(identity);
        const RawCall idCall = InvokeOneShot(reverseCurrent, ctrlId, {}, true);
        const RawCall anchorCall = InvokeOneShot(
            reverseCurrent, anchor, {LongArgument(0)}, true);
        const RawCall instanceCall = InvokeOneShot(reverseCurrent, instance, {}, true);
        AppendCall(output, L"IDHwpCtrlCode", ctrlId, idCall);
        AppendCall(output, L"IDHwpCtrlCode", anchor, anchorCall);
        AppendCall(output, L"IDHwpCtrlCode", instance, instanceCall);
        ControlToken token;
        if (BuildControlToken(idCall, instanceCall, anchorCall, &token)) {
            reverseTokens.push_back(token);
            AppendControlToken(output, L"REVERSE", token);
        } else {
            tokenAmbiguous = true;
            output << L"CONTROL_TOKEN\tREVERSE\tAMBIGUOUS\tORDINAL="
                   << ordinal << L'\n';
        }
        const RawCall previous = InvokeOneShot(reverseCurrent, prev, {}, true);
        AppendCall(output, L"IDHwpCtrlCode", prev, previous);
        if (previous.semantic == SemanticStatus::NullResult) {
            reverseComplete = true;
            break;
        }
        CComPtr<IDispatch> prior;
        if (!AsDispatch(previous, prior)) {
            output << L"REVERSE_TERMINAL\t" << SemanticName(previous.semantic)
                   << L"\nRESULT\tFAIL";
            return output.str();
        }
        reverseCurrent = prior;
    }
    if (!reverseComplete) {
        output << L"REVERSE_TERMINAL\tLIMIT_REACHED\nRESULT\tFAIL";
        return output.str();
    }
    output << L"REVERSE_TERMINAL\tNULL_END\n";
    if (!tokenAmbiguous) {
        const std::vector<ControlToken> normalizedReverse(
            reverseTokens.rbegin(), reverseTokens.rend());
        if (normalizedReverse != forwardTokens) {
            output << L"CONTROL_ORDER\tMISMATCH\nRESULT\tFAIL";
            return output.str();
        }
        output << L"CONTROL_ORDER\tAGREE\n";
    } else {
        output << L"CONTROL_ORDER\tINCONCLUSIVE\tAMBIGUOUS_TOKEN\n";
    }
    LONG list = 0, paragraph = 0, character = 0;
    VARIANTARG listOut{}; listOut.vt = VT_I4 | VT_BYREF; listOut.plVal = &list;
    VARIANTARG paraOut{}; paraOut.vt = VT_I4 | VT_BYREF; paraOut.plVal = &paragraph;
    VARIANTARG charOut{}; charOut.vt = VT_I4 | VT_BYREF; charOut.plVal = &character;
    const RawCall position = InvokeOneShot(hwp, getPos, {listOut,paraOut,charOut}, true);
    AppendCall(output, L"IHwpObject", getPos, position);
    const std::vector<VARIANTARG> exactPosition{
        LongArgument(list), LongArgument(paragraph), LongArgument(character)};
    const RawCall set = InvokeOneShot(hwp, setPos, exactPosition, true);
    AppendCall(output, L"IHwpObject", setPos, set);
    const RawCall scan = InvokeOneShot(hwp, initScan,
        {LongArgument(0),LongArgument(0x0077),LongArgument(0),LongArgument(0),
         LongArgument(0),LongArgument(0)}, true);
    AppendCall(output, L"IHwpObject", initScan, scan);
    bool scanComplete = false;
    if (scan.semantic == SemanticStatus::Pass) {
        for (;;) {
            BSTR text = nullptr;
            VARIANTARG textOut{}; textOut.vt = VT_BSTR | VT_BYREF;
            textOut.pbstrVal = &text;
            const RawCall textCall = InvokeOneShot(hwp, getText, {textOut}, true);
            AppendCall(output, L"IHwpObject", getText, textCall);
            const LONG state = textCall.result.vt == VT_I4 ? textCall.result.lVal : -1;
            output << L"TEXT\t" << (text == nullptr ? L"NULL" : BstrText(text)) << L'\n';
            SysFreeString(text);
            if (textCall.semantic != SemanticStatus::Pass) break;
            if (state <= 1) { scanComplete = true; break; }
            if (state >= 101) break;
        }
    }
    const RawCall release = InvokeOneShot(hwp, releaseScan, {}, true);
    AppendCall(output, L"IHwpObject", releaseScan, release);
    output << L"HEADER_FOOTER_APPLICATION\tDEFINING_ANCHOR_ONLY\tCROSS_SECTION=NOT_EXPOSED\n"
           << L"CHILD_CONTAINMENT\tNOT_EXPOSED\tEXACT_OWNER_ACCESSOR_REQUIRED\n";
    if (position.semantic != SemanticStatus::Pass ||
        set.semantic != SemanticStatus::Pass ||
        scan.semantic != SemanticStatus::Pass || !scanComplete ||
        release.invokeStatus != S_OK) {
        output << L"RESULT\tFAIL";
    } else if (tokenAmbiguous) {
        output << L"RESULT\tINCONCLUSIVE\tCONTROL_TOKEN_AMBIGUOUS";
    } else {
        output << L"RESULT\tINCONCLUSIVE\tEXACT_CHILD_CONTAINMENT_NOT_EXPOSED";
    }
    return output.str();
}

std::wstring TableProbeAddress(IDispatch* const hwp) {
    LONG values[6]{};
    SHORT over = 0;
    BSTR control = nullptr;
    CComVariant arguments[8];
    for (size_t index = 0; index < 6; ++index) {
        arguments[index].vt = VT_I4 | VT_BYREF;
        arguments[index].plVal = &values[index];
    }
    arguments[6].vt = VT_I2 | VT_BYREF;
    arguments[6].piVal = &over;
    arguments[7].vt = VT_BSTR | VT_BYREF;
    arguments[7].pbstrVal = &control;
    const HRESULT status = hancom::dispatch::Method(
        hwp, L"KeyIndicator",
        {arguments[0], arguments[1], arguments[2], arguments[3], arguments[4],
         arguments[5], arguments[6], arguments[7]}, nullptr);
    const std::wstring indicator = control == nullptr
        ? std::wstring() : std::wstring(control, SysStringLen(control));
    SysFreeString(control);
    if (FAILED(status)) return L"";
    const size_t opening = indicator.find(L'(');
    const size_t closing = indicator.find(L')', opening + 1);
    if (opening == std::wstring::npos || closing == std::wstring::npos)
        return L"";
    std::wstring address = indicator.substr(opening + 1, closing - opening - 1);
    std::transform(address.begin(), address.end(), address.begin(), towupper);
    return address;
}

bool TableProbeCoordinates(
    const std::wstring& address, LONG* const row, LONG* const column) {
    if (row == nullptr || column == nullptr || address.empty()) return false;
    unsigned long long parsedColumn = 0;
    size_t at = 0;
    while (at < address.size() && address[at] >= L'A' && address[at] <= L'Z') {
        parsedColumn = parsedColumn * 26 +
            static_cast<unsigned long long>(address[at] - L'A' + 1);
        ++at;
    }
    unsigned long long parsedRow = 0;
    const size_t digitStart = at;
    while (at < address.size() && address[at] >= L'0' && address[at] <= L'9') {
        parsedRow = parsedRow * 10 +
            static_cast<unsigned long long>(address[at] - L'0');
        ++at;
    }
    if (at != address.size() || at == digitStart || parsedRow == 0 ||
        parsedRow > LONG_MAX || parsedColumn == 0 || parsedColumn > LONG_MAX)
        return false;
    *row = static_cast<LONG>(parsedRow);
    *column = static_cast<LONG>(parsedColumn);
    return true;
}

std::wstring ProbeTableRangeBases(IDispatch* const hwp) {
    static constexpr CallSpec parent{L"ParentCtrl",13,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec ctrlId{L"CtrlID",2,DISPATCH_PROPERTYGET,0,VT_BSTR};
    static constexpr CallSpec instance{L"GetCtrlInstID",15001,DISPATCH_METHOD,0,VT_BSTR};
    static constexpr CallSpec count{L"GetRowColCount",10051,DISPATCH_METHOD,1,VT_INT};
    static constexpr CallSpec move{L"MoveToCell",10052,DISPATCH_METHOD,3,VT_BOOL};
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tTABLE_RANGE_BASES\tV1\n";
    for (const hancom::inspection::TableRangeProbeTuple& tuple :
         hancom::inspection::ReadTableRangeProbeTuples()) {
        output << L"CAPTURE_TUPLE\t" << tuple.expectedAddress << L'\t'
               << tuple.expectedRow << L'\t' << tuple.expectedColumn << L'\t'
               << tuple.moveBase << L'\t' << tuple.boundsBase << L'\t'
               << (tuple.selectCell ? 1 : 0) << L'\t' << tuple.status << L'\t'
               << tuple.moveResultVt << L'\t' << tuple.rangeHresult << L'\t'
               << tuple.rangeResultVt << L'\t'
               << (tuple.rangeDispatchNull ? 1 : 0) << L'\t'
               << tuple.postMoveListId << L'\t' << tuple.postMoveAddress << L'\t'
               << tuple.startRow << L'\t' << tuple.endRow << L'\t'
               << tuple.startColumn << L'\t' << tuple.endColumn << L'\n';
    }
    const RawCall parentCall = InvokeOneShot(hwp, parent, {}, true);
    CComPtr<IDispatch> table;
    if (!AsDispatch(parentCall, table))
        return output.str() + L"RESULT\tINCONCLUSIVE\tCURRENT_TABLE_REQUIRED";
    const RawCall idCall = InvokeOneShot(table, ctrlId, {}, true);
    const RawCall instanceCall = InvokeOneShot(table, instance, {}, true);
    const RawCall rowCall = InvokeOneShot(hwp, count, {IntArgument(1)}, true);
    const RawCall columnCall = InvokeOneShot(hwp, count, {IntArgument(0)}, true);
    hancom::com_state::Position original{};
    const std::wstring originalAddress = TableProbeAddress(hwp);
    LONG expectedRow = 0;
    LONG expectedColumn = 0;
    if (idCall.result.vt != VT_BSTR || BstrText(idCall.result.bstrVal) != L"tbl" ||
        FAILED(hancom::com_state::CapturePosition(hwp, &original)) ||
        !TableProbeCoordinates(originalAddress, &expectedRow, &expectedColumn)) {
        output << L"PREREQUISITES\t" << idCall.result.vt << L'\t'
               << rowCall.result.vt << L'\t' << columnCall.result.vt << L'\t'
               << originalAddress << L'\n';
        return output.str() + L"RESULT\tINCONCLUSIVE\tCELL_IDENTITY_REQUIRED";
    }
    output << L"TABLE_INSTANCE\t"
           << (instanceCall.result.vt == VT_BSTR
                ? BstrText(instanceCall.result.bstrVal) : L"")
           << L"\nDIMENSIONS\t" << rowCall.result.intVal << L'\t'
           << columnCall.result.intVal << L"\nORIGINAL\t" << original.list
           << L'\t' << original.paragraph << L'\t' << original.character
           << L'\t' << originalAddress << L'\t' << expectedRow << L'\t'
           << expectedColumn << L'\n';
    for (LONG moveBase = 0; moveBase <= 1; ++moveBase) {
        const hancom::com_state::PositionResult restored =
            hancom::com_state::ApplyPosition(
                hwp, original,
                hancom::com_state::EmptyPositionResult::TreatAsSuccess);
        const LONG rowArgument = expectedRow - 1 + moveBase;
        const LONG columnArgument = expectedColumn - 1 + moveBase;
        const RawCall moved = InvokeOneShot(
            hwp, move,
            {IntArgument(rowArgument), IntArgument(columnArgument), IntArgument(0)},
            true);
        hancom::com_state::Position post{};
        const HRESULT postStatus = hancom::com_state::CapturePosition(hwp, &post);
        const std::wstring postAddress = TableProbeAddress(hwp);
        output << L"MOVE\t" << moveBase << L'\t' << rowArgument << L'\t'
               << columnArgument << L'\t' << SemanticName(moved.semantic)
               << L'\t' << static_cast<LONG>(restored.invokeStatus) << L'\t'
               << static_cast<LONG>(postStatus) << L'\t' << post.list << L'\t'
               << post.paragraph << L'\t' << post.character << L'\t'
               << postAddress;
        CComVariant rawRange;
        const HRESULT rangeStatus =
            hancom::dispatch::Method(hwp, L"GetCellRangeIndex", {}, &rawRange);
        CComPtr<IDispatch> bounds;
        const HRESULT dispatchStatus =
            SUCCEEDED(rangeStatus)
            ? hancom::dispatch::AsDispatch(rawRange, bounds)
            : rangeStatus;
        const auto rawLong = [&bounds](const wchar_t* const name, LONG* const value) {
            CComVariant rawValue;
            return hancom::dispatch::PropertyGet(bounds, name, &rawValue) == S_OK &&
                hancom::dispatch::AsLong(rawValue, value) == S_OK;
        };
        LONG sr = 0, er = 0, sc = 0, ec = 0;
        if (bounds != nullptr && rawLong(L"StartRow", &sr) &&
            rawLong(L"EndRow", &er) && rawLong(L"StartCol", &sc) &&
            rawLong(L"EndCol", &ec)) {
            output << L'\t' << sr << L'\t' << er << L'\t' << sc << L'\t' << ec;
        } else {
            output << L"\tRANGE_UNAVAILABLE\t" << static_cast<LONG>(rangeStatus)
                   << L'\t' << static_cast<LONG>(dispatchStatus) << L'\t'
                   << rawRange.vt;
        }
        output << L'\n';
    }
    static_cast<void>(hancom::com_state::ApplyPosition(
        hwp, original, hancom::com_state::EmptyPositionResult::TreatAsSuccess));
    output << L"RESULT\tPASS";
    return output.str();
}

std::wstring ProbeTable(IDispatch* hwp) {
    static constexpr CallSpec head{L"HeadCtrl",9,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec parent{L"ParentCtrl",13,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec ctrlId{L"CtrlID",2,DISPATCH_PROPERTYGET,0,VT_BSTR};
    static constexpr CallSpec instance{L"GetCtrlInstID",15001,DISPATCH_METHOD,0,VT_BSTR};
    static constexpr CallSpec count{L"GetRowColCount",10051,DISPATCH_METHOD,1,VT_INT};
    static constexpr CallSpec move{L"MoveToCell",10052,DISPATCH_METHOD,3,VT_BOOL};
    static constexpr CallSpec range{L"GetCellRangeIndex",10053,DISPATCH_METHOD,0,VT_DISPATCH};
    static constexpr CallSpec startRow{L"StartRow",16415,DISPATCH_PROPERTYGET,0,VT_I4};
    static constexpr CallSpec endRow{L"EndRow",16416,DISPATCH_PROPERTYGET,0,VT_I4};
    static constexpr CallSpec startCol{L"StartCol",16417,DISPATCH_PROPERTYGET,0,VT_I4};
    static constexpr CallSpec endCol{L"EndCol",16418,DISPATCH_PROPERTYGET,0,VT_I4};
    static constexpr CallSpec properties{L"Properties",6,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec hset{L"HSet",1,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec repeatHeader{L"RepeatHeader",16928,DISPATCH_PROPERTYGET,0,VT_UI2};
    static constexpr CallSpec cell{L"Cell",563,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec header{L"Header",16929,DISPATCH_PROPERTYGET,0,VT_UI2};
    static constexpr CallSpec item{L"Item",15005,DISPATCH_METHOD,1,VT_VARIANT};
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tTABLE_TOPOLOGY\tV1\n";
    const RawCall parentCall = InvokeOneShot(hwp, parent, {}, true);
    AppendCall(output, L"IHwpObject", parent, parentCall);
    CComPtr<IDispatch> table;
    if (!AsDispatch(parentCall, table)) {
        output << L"RESULT\tINCONCLUSIVE\tCURRENT_SELECTED_TABLE_REQUIRED";
        return output.str();
    }
    const RawCall idCall = InvokeOneShot(table, ctrlId, {}, true);
    const RawCall instanceCall = InvokeOneShot(table, instance, {}, true);
    AppendCall(output, L"IDHwpCtrlCode", ctrlId, idCall);
    AppendCall(output, L"IDHwpCtrlCode", instance, instanceCall);
    if (idCall.result.vt != VT_BSTR || BstrText(idCall.result.bstrVal) != L"tbl") {
        output << L"RESULT\tINCONCLUSIVE\tTYPED_TABLE_CONTRACT_REQUIRED";
        return output.str();
    }
    const RawCall propertiesCall = InvokeOneShot(table, properties, {}, true);
    AppendCall(output, L"IDHwpCtrlCode", properties, propertiesCall);
    CComPtr<IDispatch> tableSet;
    if (!AsDispatch(propertiesCall, tableSet)) {
        output << L"RESULT\tINCONCLUSIVE\tTYPED_TABLE_PARAMETERSET_REQUIRED";
        return output.str();
    }
    const RawCall typedRepeat = InvokeOneShot(tableSet, repeatHeader, {}, true);
    const RawCall tableHsetCall = InvokeOneShot(tableSet, hset, {}, true);
    AppendCall(output, L"HTable", hset, tableHsetCall);
    CComPtr<IDispatch> genericTableSet;
    if (!AsDispatch(tableHsetCall, genericTableSet)) {
        output << L"RESULT\tFAIL\tGENERIC_TABLE_PARAMETERSET_REQUIRED";
        return output.str();
    }
    CComBSTR repeatName(L"RepeatHeader");
    VARIANTARG repeatArgument{};
    repeatArgument.vt = VT_BSTR;
    repeatArgument.bstrVal = repeatName;
    const RawCall genericRepeat = InvokeOneShot(
        genericTableSet, item, {repeatArgument}, true);
    AppendCall(output, L"HTable", repeatHeader, typedRepeat);
    AppendCall(output, L"IDHwpParameterSet", item, genericRepeat);
    const bool repeatCompared = typedRepeat.semantic == SemanticStatus::Pass &&
        genericRepeat.semantic == SemanticStatus::Pass &&
        SameIntegralValue(typedRepeat.result, genericRepeat.result);
    output << L"TYPED_REPEAT_HEADER\t" << (repeatCompared ? L"MATCH" : L"MISMATCH") << L'\n';
    if (!repeatCompared) {
        output << L"RESULT\tFAIL\tREPEAT_HEADER_COMPARE";
        return output.str();
    }

    const RawCall rowCall = InvokeOneShot(hwp, count, {IntArgument(1)}, true);
    const RawCall columnCall = InvokeOneShot(hwp, count, {IntArgument(0)}, true);
    AppendCall(output, L"IHwpObject", count, rowCall);
    AppendCall(output, L"IHwpObject", count, columnCall);
    if (rowCall.semantic != SemanticStatus::Pass ||
        columnCall.semantic != SemanticStatus::Pass) {
        output << L"RESULT\tFAIL\tROW_COLUMN_COUNT";
        return output.str();
    }
    const LONG rows = static_cast<LONG>(rowCall.result.intVal);
    const LONG columns = static_cast<LONG>(columnCall.result.intVal);
    if (rows <= 0 || columns <= 0) {
        output << L"RESULT\tINCONCLUSIVE\tMALFORMED_DIMENSIONS";
        return output.str();
    }
    const auto probeMove = [&](LONG row, LONG column) {
        return InvokeOneShot(hwp, move,
            {IntArgument(static_cast<INT>(row)),
             IntArgument(static_cast<INT>(column)), IntArgument(0)}, true);
    };
    const RawCall zeroFirst = probeMove(0, 0);
    const RawCall zeroLast = probeMove(rows - 1, columns - 1);
    const RawCall zeroRowOutside = probeMove(rows, 0);
    const RawCall zeroColumnOutside = probeMove(0, columns);
    const RawCall oneFirst = probeMove(1, 1);
    const RawCall oneLast = probeMove(rows, columns);
    const RawCall oneRowOutside = probeMove(0, 1);
    const RawCall oneColumnOutside = probeMove(1, 0);
    for (const RawCall* call : {&zeroFirst,&zeroLast,&zeroRowOutside,
            &zeroColumnOutside,&oneFirst,&oneLast,&oneRowOutside,
            &oneColumnOutside}) {
        AppendCall(output, L"IHwpObject", move, *call);
    }
    const bool zeroBased = zeroFirst.semantic == SemanticStatus::Pass &&
        zeroLast.semantic == SemanticStatus::Pass &&
        zeroRowOutside.semantic == SemanticStatus::FalseResult &&
        zeroColumnOutside.semantic == SemanticStatus::FalseResult;
    const bool oneBased = oneFirst.semantic == SemanticStatus::Pass &&
        oneLast.semantic == SemanticStatus::Pass &&
        oneRowOutside.semantic == SemanticStatus::FalseResult &&
        oneColumnOutside.semantic == SemanticStatus::FalseResult;
    if (zeroBased == oneBased) {
        output << L"COORDINATE_BASE\tINCONCLUSIVE\nRESULT\tINCONCLUSIVE";
        return output.str();
    }
    const LONG coordinateBase = oneBased ? 1 : 0;
    output << L"COORDINATE_BASE\t" << (oneBased ? L"ONE" : L"ZERO") << L'\n';
    using Bounds = std::tuple<LONG,LONG,LONG,LONG>;
    std::set<Bounds> owners;
    std::map<LONG,std::vector<std::pair<LONG,LONG>>> intervals;
    bool valid = true;
    bool cellHeaderCompared = false;
    bool horizontalObserved = false;
    bool verticalObserved = false;
    bool rectangularObserved = false;
    bool terminalObserved = false;
    bool nestedObserved = false;
    for (LONG row = 0; row < rows && valid; ++row) {
        for (LONG column = 0; column < columns; ++column) {
            const RawCall moved = probeMove(
                row + coordinateBase, column + coordinateBase);
            AppendCall(output, L"IHwpObject", move, moved);
            if (moved.semantic != SemanticStatus::Pass) { valid = false; break; }
            const RawCall nestedHeadCall = InvokeOneShot(hwp, head, {}, true);
            AppendCall(output,L"IHwpObject",head,nestedHeadCall);
            CComPtr<IDispatch> nestedCandidate;
            if (AsDispatch(nestedHeadCall,nestedCandidate)) {
                const RawCall nestedId = InvokeOneShot(nestedCandidate,ctrlId,{},true);
                const RawCall nestedInstance = InvokeOneShot(nestedCandidate,instance,{},true);
                AppendCall(output,L"IDHwpCtrlCode",ctrlId,nestedId);
                AppendCall(output,L"IDHwpCtrlCode",instance,nestedInstance);
                nestedObserved = nestedObserved ||
                    (nestedId.result.vt==VT_BSTR && nestedId.result.bstrVal!=nullptr &&
                     BstrText(nestedId.result.bstrVal)==L"tbl" &&
                     nestedInstance.result.vt==VT_BSTR &&
                     instanceCall.result.vt==VT_BSTR &&
                     BstrText(nestedInstance.result.bstrVal)!=
                        BstrText(instanceCall.result.bstrVal));
            }
            const RawCall cellCall = InvokeOneShot(tableSet, cell, {}, true);
            AppendCall(output, L"HTable", cell, cellCall);
            CComPtr<IDispatch> cellSet;
            if (!AsDispatch(cellCall, cellSet)) { valid = false; break; }
            const RawCall typedHeader = InvokeOneShot(cellSet, header, {}, true);
            const RawCall cellHsetCall = InvokeOneShot(cellSet, hset, {}, true);
            AppendCall(output,L"HCell",hset,cellHsetCall);
            CComPtr<IDispatch> genericCellSet;
            if (!AsDispatch(cellHsetCall,genericCellSet)) { valid=false; break; }
            CComBSTR headerName(L"Header");
            VARIANTARG headerArgument{};
            headerArgument.vt = VT_BSTR;
            headerArgument.bstrVal = headerName;
            const RawCall genericHeader = InvokeOneShot(
                genericCellSet, item, {headerArgument}, true);
            AppendCall(output, L"HCell", header, typedHeader);
            AppendCall(output, L"IDHwpParameterSet", item, genericHeader);
            if (typedHeader.semantic != SemanticStatus::Pass ||
                genericHeader.semantic != SemanticStatus::Pass ||
                !SameIntegralValue(typedHeader.result, genericHeader.result)) {
                valid = false; break;
            }
            cellHeaderCompared = true;
            const RawCall rangeCall = InvokeOneShot(hwp, range, {}, true);
            AppendCall(output, L"IHwpObject", range, rangeCall);
            CComPtr<IDispatch> bounds;
            if (!AsDispatch(rangeCall, bounds)) { valid = false; break; }
            const RawCall sr = InvokeOneShot(bounds,startRow,{},true);
            const RawCall er = InvokeOneShot(bounds,endRow,{},true);
            const RawCall sc = InvokeOneShot(bounds,startCol,{},true);
            const RawCall ec = InvokeOneShot(bounds,endCol,{},true);
            AppendCall(output,L"HCellRangeRowCol",startRow,sr);
            AppendCall(output,L"HCellRangeRowCol",endRow,er);
            AppendCall(output,L"HCellRangeRowCol",startCol,sc);
            AppendCall(output,L"HCellRangeRowCol",endCol,ec);
            if (sr.semantic != SemanticStatus::Pass || er.semantic != SemanticStatus::Pass ||
                sc.semantic != SemanticStatus::Pass || ec.semantic != SemanticStatus::Pass ||
                sr.result.lVal < coordinateBase ||
                sr.result.lVal > er.result.lVal ||
                sc.result.lVal < coordinateBase ||
                sc.result.lVal > ec.result.lVal ||
                er.result.lVal >= rows + coordinateBase ||
                ec.result.lVal >= columns + coordinateBase ||
                row + coordinateBase < sr.result.lVal ||
                row + coordinateBase > er.result.lVal ||
                column + coordinateBase < sc.result.lVal ||
                column + coordinateBase > ec.result.lVal) {
                valid = false; break;
            }
            const Bounds owner{sr.result.lVal,er.result.lVal,sc.result.lVal,ec.result.lVal};
            if (owners.insert(owner).second) {
                const LONG rowSpan=er.result.lVal-sr.result.lVal;
                const LONG columnSpan=ec.result.lVal-sc.result.lVal;
                horizontalObserved = horizontalObserved || (rowSpan==0 && columnSpan>0);
                verticalObserved = verticalObserved || (rowSpan>0 && columnSpan==0);
                rectangularObserved = rectangularObserved || (rowSpan>0 && columnSpan>0);
                terminalObserved = terminalObserved || ((rowSpan>0 || columnSpan>0) &&
                    (er.result.lVal==rows-1+coordinateBase ||
                     ec.result.lVal==columns-1+coordinateBase));
                for (LONG coveredRow = sr.result.lVal; coveredRow <= er.result.lVal; ++coveredRow) {
                    intervals[coveredRow - coordinateBase].push_back({
                        sc.result.lVal - coordinateBase,
                        ec.result.lVal - coordinateBase});
                }
                output << L"OWNER\t"
                       << (instanceCall.result.vt == VT_BSTR && instanceCall.result.bstrVal != nullptr
                           ? BstrText(instanceCall.result.bstrVal) : L"CANONICAL_IUNKNOWN")
                       << L'\t' << sr.result.lVal << L'\t' << er.result.lVal
                       << L'\t' << sc.result.lVal << L'\t' << ec.result.lVal << L'\n';
            }
        }
    }
    for (LONG row = 0; row < rows && valid; ++row) {
        auto& spans = intervals[row];
        std::sort(spans.begin(), spans.end());
        LONG expected = 0;
        for (const auto& span : spans) {
            if (span.first != expected) { valid = false; break; }
            expected = span.second + 1;
        }
        valid = valid && expected == columns;
    }
    valid = valid && horizontalObserved && verticalObserved &&
        rectangularObserved && terminalObserved && nestedObserved;
    output << L"TOPOLOGY_COVERAGE\thorizontal=" << horizontalObserved
           << L"\tvertical=" << verticalObserved
           << L"\trectangular=" << rectangularObserved
           << L"\tterminal=" << terminalObserved
           << L"\tnested=" << nestedObserved << L'\n'
           << L"TYPED_CELL_HEADER\t"
           << (cellHeaderCompared && valid ? L"MATCH" : L"MISMATCH") << L'\n'
           << L"GENERIC_PARAMETERSET_COMPARE\t"
           << (repeatCompared && cellHeaderCompared && valid ? L"MATCH" : L"MISMATCH") << L'\n'
           << L"RESULT\t" << (valid && cellHeaderCompared ? L"PASS" : L"FAIL");
    return output.str();
}

class StorySpineSink final
    : public hancom::graph::stories::DocumentGraphStorySink {
public:
    bool BeginBodyStory() noexcept override {
        began_ = true;
        return true;
    }

    bool BeginBodyStory(
        const hancom::graph::ObservationV1<std::int64_t>& bodyList)
        noexcept override {
        bodyList_ = bodyList;
        return BeginBodyStory();
    }

    bool AppendSection(
        const hancom::graph::stories::NativeSectionRecord& record)
        noexcept override {
        try {
            sections_.push_back(record);
            return true;
        } catch (...) {
            return false;
        }
    }

    bool AppendControl(
        const hancom::graph::stories::NativeControlRecord& record)
        noexcept override {
        try {
            controls_.push_back(record);
            return true;
        } catch (...) {
            return false;
        }
    }

    bool MarkChildContainmentNotExposed(
        const hancom::graph::stories::NativeControlRecord& record)
        noexcept override {
        try {
            childGaps_.push_back(record.headCtrlOrdinal);
            return true;
        } catch (...) {
            return false;
        }
    }

    bool Commit() noexcept override {
        committed_ = true;
        return true;
    }

    void Abort() noexcept override {
        aborted_ = true;
        sections_.clear();
        controls_.clear();
        childGaps_.clear();
        bodyList_ = {};
    }

    bool Began() const noexcept { return began_; }
    bool Committed() const noexcept { return committed_; }
    const hancom::graph::ObservationV1<std::int64_t>& BodyList()
        const noexcept {
        return bodyList_;
    }
    bool Aborted() const noexcept { return aborted_; }
    const std::vector<hancom::graph::stories::NativeSectionRecord>&
    Sections() const noexcept {
        return sections_;
    }
    const std::vector<hancom::graph::stories::NativeControlRecord>&
    Controls() const noexcept {
        return controls_;
    }
    const std::vector<std::uint64_t>& ChildGaps() const noexcept {
        return childGaps_;
    }

private:
    bool began_ = false;
    bool committed_ = false;
    bool aborted_ = false;
    hancom::graph::ObservationV1<std::int64_t> bodyList_{};
    std::vector<hancom::graph::stories::NativeSectionRecord> sections_{};
    std::vector<hancom::graph::stories::NativeControlRecord> controls_{};
    std::vector<std::uint64_t> childGaps_{};
};

bool AdaptStorySpinePayload(
    const hancom::graph::stories::CaptureStatus status,
    const StorySpineSink& sink,
    hancom::graph::capture::ReaderPayload* const typed) {
    if (typed == nullptr) return false;
    hancom::graph::capture::ReaderPayload staged;
    staged.reader = hancom::graph::capture::QualifiedReader::StorySpine;
    const bool complete =
        status == hancom::graph::stories::CaptureStatus::Complete &&
        sink.Began() && sink.Committed() && !sink.Aborted();
    if (!complete) {
        staged.outcome = hancom::graph::capture::ReaderOutcome::Failed;
        staged.coverage = hancom::graph::CoverageState::ReadFailed;
        *typed = std::move(staged);
        return false;
    }
    staged.bodyList = sink.BodyList();
    for (const auto& section : sink.Sections()) {
        staged.sections.push_back({
            section.sectionOrdinal,
            {section.startAnchor.list,
             section.startAnchor.paragraph,
             section.startAnchor.character},
        });
    }
    for (const auto& control : sink.Controls()) {
        staged.controls.push_back({
            control.ctrlId,
            control.instanceId,
            control.headCtrlOrdinal,
            {control.anchor.list,
             control.anchor.paragraph,
             control.anchor.character},
            control.hasList,
        });
        staged.controls.back().instanceIdPresent =
            control.instanceIdPresent;
    }
    staged.outcome = hancom::graph::capture::ReaderOutcome::Complete;
    staged.coverage = hancom::graph::CoverageState::Complete;
    *typed = std::move(staged);
    return true;
}

std::wstring ProbeStorySpine(
    IDispatch* const hwp,
    hancom::graph::capture::ReaderPayload* const typed = nullptr) {
    StorySpineSink sink;
    hancom::graph::stories::CaptureDiagnostics diagnostics;
    const hancom::graph::stories::CaptureStatus status =
        hancom::graph::stories::CaptureNativeStructureStories(
            hwp,
            sink,
            &diagnostics);
    if (typed != nullptr) {
        static_cast<void>(AdaptStorySpinePayload(status, sink, typed));
    }
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tSTORY_SPINE\tV1\n"
           << L"BODY_STORY\t" << sink.Began() << L'\n'
           << L"CAPTURE_STATUS\t" << static_cast<int>(status) << L'\n'
           << L"SECTION_COUNT\t" << diagnostics.sectionCount << L'\n'
           << L"CONTROL_COUNT\t" << diagnostics.controlCount << L'\n'
           << L"CHILD_CONTAINMENT_NOT_EXPOSED_COUNT\t"
           << diagnostics.childContainmentNotExposedCount << L'\n';
    for (const auto& section : sink.Sections()) {
        output << L"SECTION\t" << section.sectionOrdinal << L'\t'
               << section.definitionControlPresent << L'\t'
               << section.definitionControlOrdinal << L'\t'
               << section.startAnchor.list << L'\t'
               << section.startAnchor.paragraph << L'\t'
               << section.startAnchor.character << L'\n';
    }
    for (const auto& control : sink.Controls()) {
        output << L"CONTROL\t" << control.headCtrlOrdinal << L'\t'
               << control.ctrlId << L'\t' << control.ctrlCh << L'\t'
               << control.hasList << L'\t' << control.anchor.list << L'\t'
               << control.anchor.paragraph << L'\t'
               << control.anchor.character << L'\n';
    }
    for (const std::uint64_t ordinal : sink.ChildGaps()) {
        output << L"COVERAGE\tCONTROL\t" << ordinal
               << L"\tCHILD_CONTAINMENT\tNOT_EXPOSED\n"
               << L"DIAGNOSTIC\tCONTROL\t" << ordinal
               << L"\tUNSUPPORTED_ADAPTER\tWARNING\n";
    }
    output << L"UNOWNED_STORY_EMISSIONS\t0\n"
           << L"RESULT\t"
           << (status == hancom::graph::stories::CaptureStatus::Complete &&
                       sink.Began() && sink.Committed() && !sink.Aborted()
                   ? L"PASS"
                   : L"FAIL");
    return output.str();
}

class TextProbeSink final : public hancom::graph::text::DocumentGraphTextSink {
public:
    bool BeginParagraph(
        const hancom::graph::text::NativeParagraphRecord& paragraph)
        noexcept override {
        paragraph_ = paragraph;
        try {
            paragraphStarts_.push_back(paragraph.start);
        } catch (...) {
            return false;
        }
        began_ = true;
        return true;
    }

    bool AppendAtom(
        const hancom::graph::text::NativeTextAtomRecord& atom)
        noexcept override {
        try {
            text_.append(atom.text);
            ++atomCount_;
            textCodeUnits_ += atom.text.size();
            if (atom.nativeState >= 0 && atom.nativeState < 6) {
                ++states_[static_cast<size_t>(atom.nativeState)];
            }
            if (atom.kind ==
                hancom::graph::text::AtomKind::ParagraphBoundary) {
                auto next = atom.end;
                ++next.paragraph;
                next.character = 0;
                paragraphStarts_.push_back(next);
            }
            return true;
        } catch (...) {
            return false;
        }
    }

    bool AppendRun(
        const hancom::graph::text::NativeTextRunRecord& run)
        noexcept override {
        try {
            runs_.push_back(run);
            ++runCount_;
            runCodeUnits_ += run.text.size();
            return true;
        } catch (...) {
            return false;
        }
    }

    bool MarkRunShapeNotExposed(
        const hancom::graph::text::NativeTextRunRecord&) noexcept override {
        ++shapeGapCount_;
        return true;
    }

    bool MarkParagraphCoverage(
        const hancom::graph::text::NativeParagraphCoverage&) noexcept override {
        ++paragraphCoverageCount_;
        return true;
    }

    bool Commit() noexcept override {
        committed_ = true;
        return true;
    }

    void Abort() noexcept override {
        aborted_ = true;
        atomCount_ = 0;
        runCount_ = 0;
        textCodeUnits_ = 0;
        runCodeUnits_ = 0;
        shapeGapCount_ = 0;
        paragraphCoverageCount_ = 0;
        states_.fill(0);
        text_.clear();
        runs_.clear();
        paragraphStarts_.clear();
    }

    bool Began() const noexcept { return began_; }
    bool Committed() const noexcept { return committed_; }
    bool Aborted() const noexcept { return aborted_; }
    const hancom::graph::text::NativeParagraphRecord& Paragraph()
        const noexcept {
        return paragraph_;
    }
    std::uint64_t AtomCount() const noexcept { return atomCount_; }
    std::uint64_t RunCount() const noexcept { return runCount_; }
    std::uint64_t TextCodeUnits() const noexcept { return textCodeUnits_; }
    std::uint64_t RunCodeUnits() const noexcept { return runCodeUnits_; }
    std::uint64_t ShapeGapCount() const noexcept { return shapeGapCount_; }
    std::uint64_t ParagraphCoverageCount() const noexcept {
        return paragraphCoverageCount_;
    }
    std::uint64_t StateCount(const size_t state) const noexcept {
        return state < states_.size() ? states_[state] : 0;
    }
    const std::wstring& Text() const noexcept { return text_; }
    const std::vector<hancom::graph::text::NativeTextRunRecord>& Runs()
        const noexcept {
        return runs_;
    }
    const std::vector<hancom::graph::text::NativeTextPosition>& ParagraphStarts()
        const noexcept {
        return paragraphStarts_;
    }

private:
    bool began_ = false;
    bool committed_ = false;
    bool aborted_ = false;
    hancom::graph::text::NativeParagraphRecord paragraph_{};
    std::uint64_t atomCount_ = 0;
    std::uint64_t runCount_ = 0;
    std::uint64_t textCodeUnits_ = 0;
    std::uint64_t runCodeUnits_ = 0;
    std::uint64_t shapeGapCount_ = 0;
    std::uint64_t paragraphCoverageCount_ = 0;
    std::array<std::uint64_t, 6> states_{};
    std::wstring text_{};
    std::vector<hancom::graph::text::NativeTextRunRecord> runs_{};
    std::vector<hancom::graph::text::NativeTextPosition> paragraphStarts_{};
};

std::wstring ProbeText(
    IDispatch* const hwp,
    const bool body,
    hancom::graph::capture::ReaderPayload* const typed = nullptr) {
    TextProbeSink sink;
    hancom::graph::text::CaptureDiagnostics diagnostics;
    const hancom::graph::text::CaptureStatus status = body
        ? hancom::graph::text::CaptureNativeBodyText(hwp, sink, &diagnostics)
        : hancom::graph::text::CaptureNativeCurrentParagraphText(
              hwp,
              sink,
              &diagnostics);
    TextProbeSink secondSink;
    hancom::graph::text::CaptureDiagnostics secondDiagnostics;
    const hancom::graph::text::CaptureStatus secondStatus = body
        ? hancom::graph::text::CaptureNativeBodyText(
              hwp,
              secondSink,
              &secondDiagnostics)
        : hancom::graph::text::CaptureNativeCurrentParagraphText(
              hwp,
              secondSink,
              &secondDiagnostics);
    const auto samePosition = [](const auto& left, const auto& right) {
        return left.list == right.list &&
            left.paragraph == right.paragraph &&
            left.character == right.character;
    };
    const bool paragraphStartsIdentical =
        sink.ParagraphStarts().size() == secondSink.ParagraphStarts().size() &&
        std::equal(
            sink.ParagraphStarts().begin(), sink.ParagraphStarts().end(),
            secondSink.ParagraphStarts().begin(), samePosition);
    const bool runPositionsIdentical =
        sink.Runs().size() == secondSink.Runs().size() &&
        std::equal(
            sink.Runs().begin(), sink.Runs().end(), secondSink.Runs().begin(),
            [&samePosition](const auto& left, const auto& right) {
                return left.positionComplete == right.positionComplete &&
                    samePosition(left.start, right.start) &&
                    samePosition(left.end, right.end);
            });
    const bool byteIdentical =
        sink.Text() == secondSink.Text() &&
        sink.AtomCount() == secondSink.AtomCount() &&
        sink.RunCount() == secondSink.RunCount() &&
        paragraphStartsIdentical && runPositionsIdentical;
    const auto& paragraph = sink.Paragraph();
    const bool capturesComplete =
        status == hancom::graph::text::CaptureStatus::Complete &&
        secondStatus == hancom::graph::text::CaptureStatus::Complete &&
        sink.Began() && sink.Committed() && !sink.Aborted() &&
        secondSink.Committed() && !secondSink.Aborted() &&
        diagnostics.scanDisposition ==
            hancom::graph::text::ScanDisposition::Released &&
        secondDiagnostics.scanDisposition ==
            hancom::graph::text::ScanDisposition::Released &&
        byteIdentical;
    if (typed != nullptr && capturesComplete) {
        typed->paragraphs.clear();
        for (const auto& start : sink.ParagraphStarts()) {
            hancom::graph::capture::ParagraphObservation observed;
            observed.start = {start.list, start.paragraph, 0};
            observed.sectionOrdinal = 0;
            typed->paragraphs.push_back(std::move(observed));
        }
        for (const auto& run : sink.Runs()) {
            if (!run.positionComplete) {
                typed->paragraphs.clear();
                break;
            }
            const auto paragraphIt = std::find_if(
                typed->paragraphs.begin(), typed->paragraphs.end(),
                [&](const auto& value) {
                    return value.start.list == run.start.list &&
                        value.start.paragraph == run.start.paragraph;
                });
            if (paragraphIt == typed->paragraphs.end()) {
                typed->paragraphs.clear();
                break;
            }
            paragraphIt->runs.push_back({
                {run.start.list, run.start.paragraph, run.start.character},
                {run.end.list, run.end.paragraph, run.end.character},
                run.text,
            });
        }
        if (!typed->paragraphs.empty()) {
            typed->outcome = hancom::graph::capture::ReaderOutcome::Complete;
            typed->coverage = hancom::graph::CoverageState::Complete;
        } else {
            typed->outcome = hancom::graph::capture::ReaderOutcome::Failed;
            typed->coverage = hancom::graph::CoverageState::ReadFailed;
        }
    } else if (typed != nullptr) {
        typed->outcome = hancom::graph::capture::ReaderOutcome::Failed;
        typed->coverage = hancom::graph::CoverageState::ReadFailed;
    }
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\t"
           << (body ? L"TEXT_BODY" : L"TEXT_CURRENT") << L"\tV1\n"
           << L"CAPTURE_STATUS\t" << static_cast<int>(status) << L'\n'
           << L"PARAGRAPH_START\t" << paragraph.start.list << L'\t'
           << paragraph.start.paragraph << L'\t'
           << paragraph.start.character << L'\n'
           << L"ATOM_COUNT\t" << sink.AtomCount() << L'\n'
           << L"RUN_COUNT\t" << sink.RunCount() << L'\n'
           << L"TEXT_CODE_UNITS\t" << sink.TextCodeUnits() << L'\n'
           << L"RUN_CODE_UNITS\t" << sink.RunCodeUnits() << L'\n'
           << L"SHAPE_NOT_EXPOSED_COUNT\t" << sink.ShapeGapCount() << L'\n';
    for (size_t state = 2; state <= 5; ++state) {
        output << L"STATE_COUNT\t" << state << L'\t'
               << sink.StateCount(state) << L'\n';
    }
    output << L"PARAGRAPH_COVERAGE_COUNT\t"
           << sink.ParagraphCoverageCount() << L'\n'
           << L"SCAN_DISPOSITION\t"
           << static_cast<int>(diagnostics.scanDisposition) << L'\n'
           << L"PARAGRAPH_BOUNDARY_COUNT\t"
           << diagnostics.paragraphBoundaryCount << L'\n'
           << L"CONTROL_BOUNDARY_COUNT\t"
           << diagnostics.controlBoundaryCount << L'\n'
           << L"SECOND_CAPTURE_STATUS\t"
           << static_cast<int>(secondStatus) << L'\n'
           << L"SECOND_SCAN_DISPOSITION\t"
           << static_cast<int>(secondDiagnostics.scanDisposition) << L'\n'
           << L"CAPTURES_BYTE_IDENTICAL\t" << byteIdentical << L'\n'
           << L"RESULT\t"
           << (status == hancom::graph::text::CaptureStatus::Complete &&
                       sink.Began() && sink.Committed() && !sink.Aborted() &&
                       diagnostics.scanDisposition ==
                           hancom::graph::text::ScanDisposition::Released &&
                       secondStatus ==
                           hancom::graph::text::CaptureStatus::Complete &&
                       secondSink.Committed() && !secondSink.Aborted() &&
                       secondDiagnostics.scanDisposition ==
                           hancom::graph::text::ScanDisposition::Released &&
                       byteIdentical
                   ? L"PASS"
                   : L"FAIL");
    return output.str();
}

class PropertyProbeSink final
    : public hancom::graph::properties::EffectivePropertySink {
public:
    bool Append(
        const hancom::graph::properties::PropertyObservation& observation)
        noexcept override {
        try {
            observations_.push_back(observation);
            return true;
        } catch (...) {
            return false;
        }
    }

    bool MarkCatalogCoverage(
        const hancom::graph::properties::CatalogCoverage& coverage)
        noexcept override {
        coverage_ = coverage;
        coverageMarked_ = true;
        return true;
    }

    bool Commit() noexcept override {
        committed_ = true;
        return true;
    }

    void Abort() noexcept override {
        aborted_ = true;
        observations_.clear();
        coverageMarked_ = false;
    }

    bool Committed() const noexcept { return committed_; }
    bool Aborted() const noexcept { return aborted_; }
    bool CoverageMarked() const noexcept { return coverageMarked_; }
    const hancom::graph::properties::CatalogCoverage& Coverage()
        const noexcept {
        return coverage_;
    }
    const std::vector<hancom::graph::properties::PropertyObservation>&
    Observations() const noexcept {
        return observations_;
    }

private:
    bool coverageMarked_ = false;
    bool committed_ = false;
    bool aborted_ = false;
    hancom::graph::properties::CatalogCoverage coverage_{};
    std::vector<hancom::graph::properties::PropertyObservation>
        observations_{};
};

std::wstring ProbeEffectiveProperties(
    IDispatch* const hwp,
    hancom::graph::capture::ReaderPayload* const typed = nullptr,
    const hancom::graph::properties::EffectivePropertyContext* const
        requestedContext = nullptr) {
    const hancom::graph::properties::EffectivePropertyContext defaultContext{
        hancom::graph::capture::PropertyTarget::Paragraph, L""};
    const auto& propertyContext = requestedContext == nullptr
        ? defaultContext : *requestedContext;
    PropertyProbeSink sink;
    hancom::graph::properties::CaptureDiagnostics diagnostics;
    const hancom::graph::properties::CaptureStatus status =
        hancom::graph::properties::CaptureCurrentEffectiveProperties(
            hwp,
            propertyContext,
            sink,
            &diagnostics);
    const bool typedComplete =
        status == hancom::graph::properties::CaptureStatus::Complete &&
        sink.Committed() && !sink.Aborted() && sink.CoverageMarked();
    if (typed != nullptr && typedComplete) {
        typed->properties.clear();
        for (const auto& observation : sink.Observations()) {
            hancom::graph::capture::PropertyObservation property;
            property.key = observation.key;
            property.scalar = observation.scalar;
            const hancom::graph::PropertyRule* const rule =
                hancom::graph::FindPropertyRule(observation.key);
            if (rule == nullptr) {
                typed->outcome =
                    hancom::graph::capture::ReaderOutcome::Failed;
                continue;
            }
            if (rule->origin == hancom::graph::RegistryOrigin::Generated) {
                continue;
            }
            property.target = observation.target;
            property.targetIdentity = observation.targetIdentity;
            property.ownerField = observation.ownerField;
            if (observation.ownerField == 0) {
                // Tuple-incompatible observations are terminal at the read
                // seam. They are omitted from node property bags because the
                // schema intentionally has no property coordinate at this site.
                if (observation.status !=
                    hancom::graph::properties::ReadStatus::NotApplicable) {
                    typed->outcome =
                        hancom::graph::capture::ReaderOutcome::Failed;
                }
                continue;
            }
            property.origin = observation.origin;
            switch (observation.status) {
            case hancom::graph::properties::ReadStatus::Value:
                property.state = hancom::graph::ObservationState::Value;
                break;
            case hancom::graph::properties::ReadStatus::NotApplicable:
                property.state =
                    hancom::graph::ObservationState::NotApplicable;
                break;
            case hancom::graph::properties::ReadStatus::NotExposed:
                property.state =
                    hancom::graph::ObservationState::NotExposed;
                break;
            case hancom::graph::properties::ReadStatus::ReadFailed:
                property.state =
                    hancom::graph::ObservationState::ReadFailed;
                break;
            }
            if (property.state == hancom::graph::ObservationState::Value) {
                if (property.scalar == hancom::graph::ScalarTag::UTF16) {
                    property.textValue =
                        observation.canonicalValue.rfind(L"s:", 0) == 0
                        ? observation.canonicalValue.substr(2)
                        : observation.canonicalValue;
                } else {
                    const wchar_t* number = observation.canonicalValue.c_str();
                    if (observation.canonicalValue.size() > 2 &&
                        observation.canonicalValue[1] == L':') {
                        number += 2;
                    }
                    property.integerValue = _wcstoi64(
                        number, nullptr, 10);
                }
            }
            typed->properties.push_back(std::move(property));
        }
        if (typed->outcome !=
            hancom::graph::capture::ReaderOutcome::Failed) {
            typed->outcome =
                hancom::graph::capture::ReaderOutcome::Complete;
            typed->coverage = hancom::graph::CoverageState::Complete;
        } else {
            typed->coverage = hancom::graph::CoverageState::ReadFailed;
        }
    } else if (typed != nullptr) {
        typed->outcome = hancom::graph::capture::ReaderOutcome::Failed;
        typed->coverage = hancom::graph::CoverageState::ReadFailed;
    }
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tEFFECTIVE_PROPERTIES\tV1\n"
           << L"CAPTURE_STATUS\t" << static_cast<int>(status) << L'\n'
           << L"OBSERVATION_COUNT\t" << sink.Observations().size() << L'\n'
           << L"VALUE_COUNT\t" << diagnostics.valueCount << L'\n'
           << L"NOT_APPLICABLE_COUNT\t"
           << diagnostics.notApplicableCount << L'\n'
           << L"NOT_EXPOSED_COUNT\t" << diagnostics.notExposedCount << L'\n'
           << L"READ_FAILED_COUNT\t" << diagnostics.readFailedCount << L'\n';
    for (const auto& observation : sink.Observations()) {
        output << L"PROPERTY_STATE\t" << observation.key << L'\t'
               << static_cast<int>(observation.status) << L'\t'
               << static_cast<unsigned>(observation.origin) << L'\t'
               << observation.canonicalValue.size() << L'\n';
        if (observation.key == 1000 || observation.key == 2000 ||
            observation.key == 6000) {
            output << L"PROPERTY\t" << observation.key << L'\t'
                   << static_cast<int>(observation.status) << L'\t'
                   << static_cast<unsigned>(observation.origin) << L'\t'
                   << observation.canonicalValue << L'\n';
        }
    }
    const auto& coverage = sink.Coverage();
    output << L"CATALOG_COVERAGE\t"
           << coverage.styleDefinitionsNotExposed << L'\t'
           << coverage.numberingDefinitionsNotExposed << L'\t'
           << coverage.bulletDefinitionsNotExposed << L'\t'
           << coverage.tabDefinitionCatalogNotExposed << L'\t'
           << coverage.directInheritedOriginNotExposed << L'\n'
           << L"RESULT\t"
           << (status ==
                           hancom::graph::properties::CaptureStatus::Complete &&
                       sink.Committed() && !sink.Aborted() &&
                       sink.CoverageMarked() &&
                       sink.Observations().size() ==
                           hancom::graph::kPropertyRegistryV1.size() &&
                       diagnostics.valueCount != 0
                   ? L"PASS"
                   : L"FAIL");
    return output.str();
}

class ControlAdapterProbeSink final
    : public hancom::graph::stories::DocumentGraphStorySink {
public:
    bool BeginBodyStory() noexcept override {
        began_ = true;
        return true;
    }

    bool AppendSection(
        const hancom::graph::stories::NativeSectionRecord&) noexcept override {
        ++sectionCount_;
        return true;
    }

    bool AppendControl(
        const hancom::graph::stories::NativeControlRecord& source)
        noexcept override {
        hancom::graph::controls::AdaptedControlRecord adapted;
        if (!hancom::graph::controls::AdaptNativeControl(source, &adapted)) {
            return false;
        }
        try {
            controls_.push_back(std::move(adapted));
        } catch (...) {
            return false;
        }
        ++controlCount_;
        ++kinds_[static_cast<size_t>(controls_.back().kind)];
        if (controls_.back().unknownTypeDiagnostic) {
            ++unknownCount_;
        }
        return true;
    }

    bool MarkChildContainmentNotExposed(
        const hancom::graph::stories::NativeControlRecord&) noexcept override {
        ++childGapCount_;
        return true;
    }

    bool Commit() noexcept override {
        committed_ = true;
        return true;
    }

    void Abort() noexcept override {
        aborted_ = true;
        controlCount_ = 0;
        sectionCount_ = 0;
        childGapCount_ = 0;
        unknownCount_ = 0;
        kinds_.fill(0);
        controls_.clear();
    }

    bool Began() const noexcept { return began_; }
    bool Committed() const noexcept { return committed_; }
    bool Aborted() const noexcept { return aborted_; }
    std::uint64_t ControlCount() const noexcept { return controlCount_; }
    std::uint64_t SectionCount() const noexcept { return sectionCount_; }
    std::uint64_t ChildGapCount() const noexcept { return childGapCount_; }
    std::uint64_t UnknownCount() const noexcept { return unknownCount_; }
    std::uint64_t KindCount(
        const hancom::graph::controls::ControlKind kind) const noexcept {
        return kinds_[static_cast<size_t>(kind)];
    }
    const std::vector<hancom::graph::controls::AdaptedControlRecord>&
    Controls() const noexcept {
        return controls_;
    }

private:
    bool began_ = false;
    bool committed_ = false;
    bool aborted_ = false;
    std::uint64_t controlCount_ = 0;
    std::uint64_t sectionCount_ = 0;
    std::uint64_t childGapCount_ = 0;
    std::uint64_t unknownCount_ = 0;
    std::array<std::uint64_t, 12> kinds_{};
    std::vector<hancom::graph::controls::AdaptedControlRecord> controls_{};
};

std::wstring ProbeControlAdapters(
    IDispatch* const hwp,
    hancom::graph::capture::ReaderPayload* const typed = nullptr) {
    ControlAdapterProbeSink sink;
    hancom::graph::stories::CaptureDiagnostics diagnostics;
    const hancom::graph::stories::CaptureStatus status =
        hancom::graph::stories::CaptureNativeStructureStories(
            hwp,
            sink,
            &diagnostics);
    const bool typedComplete =
        status == hancom::graph::stories::CaptureStatus::Complete &&
        sink.Began() && sink.Committed() && !sink.Aborted() &&
        sink.ControlCount() == diagnostics.controlCount;
    if (typed != nullptr && typedComplete) {
        typed->controls.clear();
        for (const auto& control : sink.Controls()) {
            typed->controls.push_back({
                control.nativeTypeId,
                control.sessionInstanceId,
                control.headCtrlOrdinal,
                {control.anchor.list,
                 control.anchor.paragraph,
                 control.anchor.character},
                control.hasList,
                static_cast<std::uint8_t>(control.kind),
                control.unknownTypeDiagnostic,
            });
            typed->controls.back().instanceIdPresent =
                control.instanceIdPresent;
        }
        typed->outcome = hancom::graph::capture::ReaderOutcome::Complete;
        typed->coverage = hancom::graph::CoverageState::Complete;
    } else if (typed != nullptr) {
        typed->outcome = hancom::graph::capture::ReaderOutcome::Failed;
        typed->coverage = hancom::graph::CoverageState::ReadFailed;
    }
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tCONTROL_ADAPTERS\tV1\n"
           << L"CAPTURE_STATUS\t" << static_cast<int>(status) << L'\n'
           << L"SECTION_COUNT\t" << sink.SectionCount() << L'\n'
           << L"CONTROL_COUNT\t" << sink.ControlCount() << L'\n'
           << L"CHILD_GAP_COUNT\t" << sink.ChildGapCount() << L'\n'
           << L"UNKNOWN_COUNT\t" << sink.UnknownCount() << L'\n';
    for (int kind = 0; kind <=
         static_cast<int>(hancom::graph::controls::ControlKind::Unknown);
         ++kind) {
        output << L"KIND_COUNT\t" << kind << L'\t'
               << sink.KindCount(
                      static_cast<hancom::graph::controls::ControlKind>(kind))
               << L'\n';
    }
    output << L"RESULT\t"
           << (status == hancom::graph::stories::CaptureStatus::Complete &&
                       sink.Began() && sink.Committed() && !sink.Aborted() &&
                       sink.ControlCount() == diagnostics.controlCount
                   ? L"PASS"
                   : L"FAIL");
    return output.str();
}

struct CaptionScanCandidate final {
    std::uint64_t ownerHeadCtrlOrdinal = 0;
    hancom::graph::capture::NativePosition start{};
    std::wstring text{};
};

struct ScanOwnerMatch final {
    bool ambiguous = false;
    bool found = false;
    std::uint64_t headCtrlOrdinal = 0;
};

ScanOwnerMatch ResolveScanOwner(
    IDispatch* const hwp,
    const std::vector<hancom::graph::capture::ControlObservation>& controls) {
    ScanOwnerMatch result;
    CComVariant rawOwner;
    CComPtr<IDispatch> owner;
    if (FAILED(hancom::dispatch::PropertyGet(hwp, L"ParentCtrl", &rawOwner)) ||
        FAILED(hancom::dispatch::AsDispatch(rawOwner, owner))) {
        return result;
    }
    CComVariant rawType;
    CComVariant rawInstance;
    std::wstring type;
    std::wstring instance;
    if (FAILED(hancom::dispatch::PropertyGet(owner, L"CtrlID", &rawType)) ||
        FAILED(hancom::dispatch::AsString(rawType, &type)) ||
        FAILED(hancom::dispatch::Method(
            owner, L"GetCtrlInstID", {}, &rawInstance)) ||
        FAILED(hancom::dispatch::AsString(rawInstance, &instance))) {
        return result;
    }
    hancom::graph::capture::NativePosition anchor;
    bool anchorObserved = false;
    CComVariant rawAnchor;
    CComPtr<IDispatch> anchorSet;
    if (SUCCEEDED(hancom::dispatch::Method(
            owner, L"GetAnchorPos", {CComVariant(0L)}, &rawAnchor)) &&
        SUCCEEDED(hancom::dispatch::AsDispatch(rawAnchor, anchorSet))) {
        LONG list = 0;
        LONG paragraph = 0;
        LONG character = 0;
        anchorObserved = ReadAnchorItem(anchorSet, L"List", &list) &&
            ReadAnchorItem(anchorSet, L"Para", &paragraph) &&
            ReadAnchorItem(anchorSet, L"Pos", &character);
        anchor = {list, paragraph, character};
    }
    for (const auto& control : controls) {
        if (control.ctrlId != type) {
            continue;
        }
        const bool instanceMatch = !instance.empty() &&
            control.instanceIdPresent && control.instanceId == instance;
        const bool anchorMatch = instance.empty() && anchorObserved &&
            control.anchor.list == anchor.list &&
            control.anchor.paragraph == anchor.paragraph &&
            control.anchor.character == anchor.character;
        if (!instanceMatch && !anchorMatch) {
            continue;
        }
        if (result.found) {
            result.ambiguous = true;
            return result;
        }
        result.found = true;
        result.headCtrlOrdinal = control.headCtrlOrdinal;
    }
    return result;
}

bool QualifyCaptionLocations(
    IDispatch* const hwp,
    const std::vector<hancom::graph::capture::ControlObservation>& controls,
    const hancom::graph::capture::CaptureIdentityArena& capture,
    hancom::graph::capture::ReaderPayload* const payload) {
    if (hwp == nullptr || payload == nullptr) {
        return false;
    }
    const bool needsCaption = std::any_of(
        payload->images.begin(), payload->images.end(),
        [](const auto& image) {
            return image.text[2].state ==
                       hancom::graph::ObservationState::Value &&
                !image.text[2].value.empty();
        });
    if (!needsCaption) {
        return true;
    }

    CComVariant rawStarted;
    bool started = false;
    const HRESULT initStatus = hancom::dispatch::Method(
        hwp, L"InitScan",
        {CComVariant(0L), CComVariant(0x0077L), CComVariant(0L),
         CComVariant(0L), CComVariant(0L), CComVariant(0L)},
        &rawStarted);
    const bool scanOpen = SUCCEEDED(initStatus);
    bool scanComplete = false;
    bool scanReadable = scanOpen &&
        SUCCEEDED(hancom::dispatch::AsBool(rawStarted, &started)) && started;
    std::vector<CaptionScanCandidate> candidates;
    std::vector<std::ptrdiff_t> ownerStack;
    while (scanReadable) {
        BSTR text = nullptr;
        CComVariant textArgument;
        textArgument.vt = VT_BSTR | VT_BYREF;
        textArgument.pbstrVal = &text;
        CComVariant rawState;
        const HRESULT textStatus = hancom::dispatch::Method(
            hwp, L"GetText", {textArgument}, &rawState);
        LONG state = 0;
        const HRESULT stateStatus =
            hancom::dispatch::AsLong(rawState, &state);
        std::wstring value;
        if (text != nullptr) {
            value.assign(text, SysStringLen(text));
        }
        SysFreeString(text);
        if (FAILED(textStatus) || FAILED(stateStatus) ||
            state < 0 || state >= 101) {
            scanReadable = false;
            break;
        }
        if (state <= 1) {
            scanComplete = true;
            break;
        }
        if (state == 4) {
            CComVariant rawMoved;
            bool moved = false;
            const HRESULT moveStatus = hancom::dispatch::Method(
                hwp, L"MovePos",
                {CComVariant(201L), CComVariant(0L), CComVariant(0L)},
                &rawMoved);
            hancom::com_state::Position position;
            if (FAILED(moveStatus) ||
                FAILED(hancom::dispatch::AsBool(rawMoved, &moved)) || !moved ||
                FAILED(hancom::com_state::CapturePosition(hwp, &position)) ||
                position.list <= 0 || position.paragraph < 0 ||
                position.character < 0) {
                scanReadable = false;
                break;
            }
            const ScanOwnerMatch owner = ResolveScanOwner(hwp, controls);
            if (owner.ambiguous) {
                scanReadable = false;
                break;
            }
            const bool imageOwner = owner.found && std::any_of(
                payload->images.begin(), payload->images.end(),
                [&owner](const auto& image) {
                    return image.headCtrlOrdinal == owner.headCtrlOrdinal;
                });
            if (!imageOwner) {
                ownerStack.push_back(-1);
                continue;
            }
            candidates.push_back({
                owner.headCtrlOrdinal,
                {position.list, position.paragraph, position.character},
                {}});
            ownerStack.push_back(
                static_cast<std::ptrdiff_t>(candidates.size() - 1));
            continue;
        }
        if (state == 5) {
            if (ownerStack.empty()) {
                scanReadable = false;
                break;
            }
            ownerStack.pop_back();
            continue;
        }
        if (state == 2 && !ownerStack.empty() && ownerStack.back() >= 0) {
            candidates[static_cast<size_t>(ownerStack.back())].text += value;
        }
    }
    const bool released = !scanOpen || SUCCEEDED(
        hancom::dispatch::Method(hwp, L"ReleaseScan", {}, nullptr));
    if (!scanReadable || !scanComplete || !released || !ownerStack.empty()) {
        for (auto& image : payload->images) {
            if (image.text[2].state ==
                    hancom::graph::ObservationState::Value &&
                !image.text[2].value.empty()) {
                image.captionList = {
                    hancom::graph::ObservationState::ReadFailed, 0};
                image.captionStart = {};
                hancom::graph::capture::NativeCaptionLocationIssuer::Clear(
                    &image);
            }
        }
        return released;
    }
    for (auto& image : payload->images) {
        if (image.text[2].state !=
                hancom::graph::ObservationState::Value ||
            image.text[2].value.empty()) {
            image.captionList = {
                hancom::graph::ObservationState::NotApplicable, 0};
            hancom::graph::capture::NativeCaptionLocationIssuer::Clear(
                &image);
            continue;
        }
        const auto first = std::find_if(
            candidates.begin(), candidates.end(),
            [&image](const CaptionScanCandidate& candidate) {
                return candidate.ownerHeadCtrlOrdinal == image.headCtrlOrdinal;
            });
        if (first == candidates.end()) {
            image.captionList = {
                hancom::graph::ObservationState::NotExposed, 0};
            image.captionStart = {};
            hancom::graph::capture::NativeCaptionLocationIssuer::Clear(
                &image);
            continue;
        }
        if (std::find_if(
                std::next(first), candidates.end(),
                [&image](const CaptionScanCandidate& candidate) {
                    return candidate.ownerHeadCtrlOrdinal ==
                        image.headCtrlOrdinal;
                }) != candidates.end() ||
            first->text != image.text[2].value) {
            return false;
        }
        image.captionList = {
            hancom::graph::ObservationState::Value, first->start.list};
        image.captionStart = first->start;
        if (!hancom::graph::capture::NativeCaptionLocationIssuer::Issue(
                capture, &image, first->start)) {
            return false;
        }
    }
    return true;
}

std::wstring ProbeImageGraph(
    IDispatch* const hwp,
    hancom::graph::capture::ReaderPayload* const typed = nullptr) {
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tIMAGE_GRAPH\tV1\n";
    CComVariant rawControl;
    CComPtr<IDispatch> control;
    if (FAILED(hancom::dispatch::PropertyGet(
            hwp,
            L"HeadCtrl",
            &rawControl)) ||
        FAILED(hancom::dispatch::AsDispatch(rawControl, control))) {
        output << L"RESULT\tFAIL\tHEAD_CONTROL_NOT_EXPOSED";
        return output.str();
    }
    size_t visited = 0;
    size_t pictureCount = 0;
    size_t imageShapeCount = 0;
    size_t genericShapeCount = 0;
    size_t failedCount = 0;
    while (control != nullptr && visited < 20'000) {
        ++visited;
        CComVariant rawCtrlId;
        std::wstring ctrlId;
        if (SUCCEEDED(hancom::dispatch::PropertyGet(
                control,
                L"CtrlID",
                &rawCtrlId)) &&
            SUCCEEDED(hancom::dispatch::AsString(rawCtrlId, &ctrlId)) &&
            (ctrlId == L"$pic" || ctrlId == L"gso")) {
            CComVariant rawInstanceId;
            std::wstring instanceId;
            if (SUCCEEDED(hancom::dispatch::Method(
                    control,
                    L"GetCtrlInstID",
                    {},
                    &rawInstanceId)) &&
                SUCCEEDED(hancom::dispatch::AsString(
                    rawInstanceId,
                    &instanceId))) {
                hancom::graph::images::ImageGraphRecord image;
                const auto status =
                    hancom::graph::images::CaptureImageGraphFromNative(
                        control,
                        instanceId,
                        &image);
                if (status !=
                    hancom::graph::images::CaptureStatus::Complete) {
                    ++failedCount;
                } else {
                    if (image.kind ==
                        hancom::graph::images::ImageControlKind::Picture) {
                        ++pictureCount;
                    } else if (image.kind ==
                        hancom::graph::images::ImageControlKind::ImageShape) {
                        ++imageShapeCount;
                    } else {
                        ++genericShapeCount;
                    }
                    if (typed != nullptr) {
                        hancom::graph::capture::ImageObservation observed;
                        observed.instanceId = image.sessionInstanceId;
                        observed.instanceIdPresent = true;
                        observed.ctrlId = image.ctrlId;
                        observed.headCtrlOrdinal =
                            static_cast<std::uint64_t>(visited - 1);
                        for (size_t field = 0;
                             field < image.scalar.size(); ++field) {
                            observed.scalar[field].state =
                                static_cast<hancom::graph::ObservationState>(
                                    image.scalar[field].state);
                            observed.scalar[field].value =
                                image.scalar[field].value;
                        }
                        for (size_t field = 0;
                             field < image.text.size(); ++field) {
                            observed.text[field].state =
                                static_cast<hancom::graph::ObservationState>(
                                    image.text[field].state);
                            observed.text[field].value =
                                image.text[field].value;
                        }
                        observed.asset.storageState =
                            static_cast<hancom::graph::ObservationState>(
                                image.asset.storageState);
                        observed.asset.storage =
                            static_cast<std::uint8_t>(image.asset.storage);
                        observed.asset.binaryState =
                            static_cast<hancom::graph::ObservationState>(
                                image.asset.binaryContentState);
                        observed.asset.byteLength = image.asset.byteLength;
                        observed.asset.sha256 = image.asset.sha256;
                        typed->images.push_back(std::move(observed));
                    }
                    output << L"IMAGE\t" << image.sessionInstanceId << L'\t'
                           << image.ctrlId << L'\t'
                           << static_cast<int>(image.kind) << L'\t'
                           << static_cast<int>(
                                image.asset.storageState) << L'\t'
                           << static_cast<int>(image.asset.storage) << L'\t'
                           << static_cast<int>(
                                image.asset.binaryContentState) << L'\t'
                           << image.asset.byteLength << L'\t'
                           << image.asset.sha256 << L'\n';
                    for (size_t field = 0;
                         field < image.scalar.size();
                         ++field) {
                        output << L"IMAGE_SCALAR\t"
                               << image.sessionInstanceId << L'\t'
                               << field << L'\t'
                               << static_cast<int>(
                                    image.scalar[field].state) << L'\t'
                               << image.scalar[field].value << L'\n';
                    }
                    for (size_t field = 0;
                         field < image.text.size();
                         ++field) {
                        output << L"IMAGE_TEXT\t"
                               << image.sessionInstanceId << L'\t'
                               << field << L'\t'
                               << static_cast<int>(
                                    image.text[field].state) << L'\t'
                               << image.text[field].value.size() << L'\n';
                    }
                }
            } else {
                ++failedCount;
            }
        }
        CComVariant rawNext;
        CComPtr<IDispatch> next;
        if (FAILED(hancom::dispatch::PropertyGet(
                control,
                L"Next",
                &rawNext)) ||
            FAILED(hancom::dispatch::AsDispatch(rawNext, next))) {
            break;
        }
        control = next;
    }
    output << L"PICTURES\t" << pictureCount << L'\n'
           << L"IMAGE_SHAPES\t" << imageShapeCount << L'\n'
           << L"GENERIC_SHAPES\t" << genericShapeCount << L'\n'
           << L"CAPTURE_FAILURES\t" << failedCount << L'\n';
    const bool passed =
        pictureCount + imageShapeCount != 0 && failedCount == 0;
    if (typed != nullptr) {
        typed->outcome = passed
            ? hancom::graph::capture::ReaderOutcome::Complete
            : failedCount == 0
                ? hancom::graph::capture::ReaderOutcome::Inconclusive
                : hancom::graph::capture::ReaderOutcome::Failed;
        typed->coverage = passed
            ? hancom::graph::CoverageState::Complete
            : failedCount == 0
                ? hancom::graph::CoverageState::NotExposed
                : hancom::graph::CoverageState::ReadFailed;
    }
    output << L"RESULT\t"
           << (passed ? L"PASS" : L"INCONCLUSIVE\tNO_NATIVE_IMAGE");
    return output.str();
}

bool IsLayoutMemberNotExposed(const HRESULT status) noexcept {
    return status == DISP_E_UNKNOWNNAME || status == DISP_E_MEMBERNOTFOUND;
}

bool ReadCurrentLayoutPage(
    IDispatch* const hwp,
    std::int64_t* const page) noexcept {
    CComVariant raw;
    CComPtr<IDispatch> documents;
    CComPtr<IDispatch> document;
    CComPtr<IDispatch> info;
    LONG zeroBased = 0;
    return hwp != nullptr && page != nullptr &&
        SUCCEEDED(hancom::dispatch::PropertyGet(hwp, L"XHwpDocuments", &raw)) &&
        SUCCEEDED(hancom::dispatch::AsDispatch(raw, documents)) &&
        SUCCEEDED(hancom::dispatch::PropertyGet(
            documents, L"Active_XHwpDocument", &raw)) &&
        SUCCEEDED(hancom::dispatch::AsDispatch(raw, document)) &&
        SUCCEEDED(hancom::dispatch::PropertyGet(
            document, L"XHwpDocumentInfo", &raw)) &&
        SUCCEEDED(hancom::dispatch::AsDispatch(raw, info)) &&
        SUCCEEDED(hancom::dispatch::PropertyGet(info, L"CurrentPage", &raw)) &&
        SUCCEEDED(hancom::dispatch::AsLong(raw, &zeroBased)) &&
        zeroBased >= 0 && ((*page = static_cast<std::int64_t>(zeroBased) + 1), true);
}

bool ReadPageAtPosition(
    IDispatch* const hwp,
    const hancom::graph::capture::NativePosition& position,
    hancom::graph::layout::ScalarObservation* const output) noexcept {
    if (output == nullptr || position.list < LONG_MIN || position.list > LONG_MAX ||
        position.paragraph < LONG_MIN || position.paragraph > LONG_MAX ||
        position.character < LONG_MIN || position.character > LONG_MAX) {
        return false;
    }
    const auto positioned = hancom::com_state::ApplyPosition(
        hwp,
        {static_cast<LONG>(position.list),
         static_cast<LONG>(position.paragraph),
         static_cast<LONG>(position.character)},
        hancom::com_state::EmptyPositionResult::Reject);
    std::int64_t page = 0;
    if (!positioned.positioned || !ReadCurrentLayoutPage(hwp, &page)) {
        return false;
    }
    *output = {hancom::graph::layout::ObservationState::Value, page};
    return true;
}

bool ReadControlLayoutGeometry(
    IDispatch* const hwp,
    const hancom::graph::capture::ControlObservation& wanted,
    std::array<hancom::graph::layout::ScalarObservation, 2>* const output)
    noexcept {
    if (hwp == nullptr || output == nullptr) return false;
    CComVariant raw;
    CComPtr<IDispatch> control;
    HRESULT status = hancom::dispatch::PropertyGet(hwp, L"HeadCtrl", &raw);
    if (FAILED(status) || FAILED(hancom::dispatch::AsDispatch(raw, control))) {
        return false;
    }
    for (std::uint64_t ordinal = 0; ordinal < wanted.headCtrlOrdinal; ++ordinal) {
        CComPtr<IDispatch> next;
        status = hancom::dispatch::PropertyGet(control, L"Next", &raw);
        if (FAILED(status) || FAILED(hancom::dispatch::AsDispatch(raw, next)) ||
            next == nullptr) {
            return false;
        }
        control = next;
    }
    std::wstring ctrlId;
    if (FAILED(hancom::dispatch::PropertyGet(control, L"CtrlID", &raw)) ||
        FAILED(hancom::dispatch::AsString(raw, &ctrlId)) ||
        ctrlId != wanted.ctrlId) {
        return false;
    }
    if (wanted.instanceIdPresent) {
        std::wstring instanceId;
        if (FAILED(hancom::dispatch::Method(
                control, L"GetCtrlInstID", {}, &raw)) ||
            FAILED(hancom::dispatch::AsString(raw, &instanceId)) ||
            instanceId != wanted.instanceId) {
            return false;
        }
    }
    CComPtr<IDispatch> properties;
    status = hancom::dispatch::PropertyGet(control, L"Properties", &raw);
    if (IsLayoutMemberNotExposed(status)) {
        (*output)[0] = {};
        (*output)[1] = {};
        return true;
    }
    if (FAILED(status) || FAILED(hancom::dispatch::AsDispatch(raw, properties))) {
        return false;
    }
    const auto read = [&properties](
        const wchar_t* const name,
        hancom::graph::layout::ScalarObservation* const observed) {
        CComVariant value;
        LONG number = 0;
        const HRESULT member = hancom::dispatch::PropertyGet(
            properties, name, &value);
        if (IsLayoutMemberNotExposed(member)) {
            *observed = {};
            return true;
        }
        if (FAILED(member) || FAILED(hancom::dispatch::AsLong(value, &number)) ||
            number < 0) {
            return false;
        }
        *observed = {
            hancom::graph::layout::ObservationState::Value,
            static_cast<std::int64_t>(number)};
        return true;
    };
    return read(L"Width", &(*output)[0]) &&
        read(L"Height", &(*output)[1]);
}

struct QualifiedLayoutContext final {
    IDispatch* hwp = nullptr;
    const std::vector<hancom::graph::capture::ReaderPayload>* payloads = nullptr;
};

hancom::graph::layout::RecalculationObservation RecalculateQualifiedLayout(
    void* const raw) noexcept {
    auto* const context = static_cast<QualifiedLayoutContext*>(raw);
    hancom::graph::layout::RecalculationObservation observation;
    if (context == nullptr || context->hwp == nullptr) return observation;
    CComVariant value;
    const HRESULT direct = hancom::dispatch::Method(
        context->hwp, L"RecalcPageCount", {}, &value);
    observation.direct = SUCCEEDED(direct)
        ? hancom::graph::layout::RecalculationRouteState::Performed
        : IsLayoutMemberNotExposed(direct)
            ? hancom::graph::layout::RecalculationRouteState::MemberNotExposed
            : hancom::graph::layout::RecalculationRouteState::Failed;
    CComPtr<IDispatch> action;
    bool ran = false;
    const HRESULT actionProperty = hancom::dispatch::PropertyGet(
        context->hwp, L"HAction", &value);
    const HRESULT actionDispatch = hancom::dispatch::AsDispatch(value, action);
    const HRESULT actionRun = action == nullptr ? E_POINTER :
        hancom::dispatch::Method(
            action, L"Run", {CComVariant(L"RecalcPageCount")}, &value);
    const HRESULT actionValue = hancom::dispatch::AsBool(value, &ran);
    observation.action = SUCCEEDED(actionProperty) &&
            SUCCEEDED(actionDispatch) && SUCCEEDED(actionRun) &&
            SUCCEEDED(actionValue)
        ? ran ? hancom::graph::layout::RecalculationRouteState::Performed
              : hancom::graph::layout::RecalculationRouteState::SemanticFalse
        : IsLayoutMemberNotExposed(actionProperty) ||
                IsLayoutMemberNotExposed(actionRun)
            ? hancom::graph::layout::RecalculationRouteState::MemberNotExposed
            : hancom::graph::layout::RecalculationRouteState::Failed;
    observation.aggregate =
        observation.direct == hancom::graph::layout::RecalculationRouteState::Performed ||
        observation.action == hancom::graph::layout::RecalculationRouteState::Performed
        ? hancom::graph::layout::RecalculationState::Performed
        : observation.direct == hancom::graph::layout::RecalculationRouteState::Failed ||
          observation.action == hancom::graph::layout::RecalculationRouteState::Failed
            ? hancom::graph::layout::RecalculationState::Failed
            : hancom::graph::layout::RecalculationState::NotExposed;
    return observation;
}

bool ObserveQualifiedNativeLayout(
    void* const raw,
    hancom::graph::layout::NativeLayoutObservationV1* const output) noexcept {
    auto* const context = static_cast<QualifiedLayoutContext*>(raw);
    if (context == nullptr || context->hwp == nullptr ||
        context->payloads == nullptr || output == nullptr ||
        context->payloads->size() < static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::StableLayout)) {
        return false;
    }
    try {
        const auto& story = (*context->payloads)[static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::StorySpine)];
        const auto& text = (*context->payloads)[static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::TextAtoms)];
        const auto& properties = (*context->payloads)[static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::EffectiveProperties)];
        const auto& tables = (*context->payloads)[static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::TableTopology)];
        const auto& images = (*context->payloads)[static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::ImagesShapesCaptions)];
        CComVariant rawPageCount;
        LONG pageCount = 0;
        if (FAILED(hancom::dispatch::PropertyGet(
                context->hwp, L"PageCount", &rawPageCount)) ||
            FAILED(hancom::dispatch::AsLong(rawPageCount, &pageCount)) ||
            pageCount < 1) {
            return false;
        }
        hancom::graph::layout::LayoutSnapshot snapshot;
        snapshot.pageCount = pageCount;
        if (!hancom::graph::layout::BuildSectionLayoutObservationsV1(
                properties, &snapshot.sections) ||
            snapshot.sections.size() != story.sections.size()) {
            return false;
        }
        const auto addParagraph = [&snapshot, &context](
            const auto& paragraph) {
            hancom::graph::layout::LayoutNodeObservation node;
            node.kind = hancom::graph::layout::LayoutNodeKind::Paragraph;
            node.nodeId = std::to_wstring(paragraph.start.list) + L":" +
                std::to_wstring(paragraph.start.paragraph);
            auto end = paragraph.start;
            if (!paragraph.runs.empty()) end = paragraph.runs.back().end;
            if (!ReadPageAtPosition(
                    context->hwp, paragraph.start, &node.pageStart)) {
                return false;
            }
            if (!ReadPageAtPosition(context->hwp, end, &node.pageEnd)) {
                return false;
            }
            snapshot.nodes.push_back(std::move(node));
            return true;
        };
        for (const auto& paragraph : text.paragraphs) {
            if (!addParagraph(paragraph)) return false;
        }
        for (const auto& table : tables.tables) {
            hancom::graph::capture::ControlObservation control{
                L"tbl", table.instanceId, table.headCtrlOrdinal, table.anchor};
            control.instanceIdPresent = table.instanceIdPresent;
            hancom::graph::layout::LayoutNodeObservation tableNode;
            tableNode.kind = hancom::graph::layout::LayoutNodeKind::Table;
            tableNode.nodeId = hancom::graph::capture::CanonicalNativeSiteIdentity(
                hancom::graph::capture::PropertyTarget::Table, L"tbl",
                table.headCtrlOrdinal, table.anchor,
                table.instanceIdPresent, table.instanceId);
            std::array<hancom::graph::layout::ScalarObservation, 2> geometry{};
            if (!ReadControlLayoutGeometry(context->hwp, control, &geometry)) {
                return false;
            }
            tableNode.geometry[0] = geometry[0];
            tableNode.geometry[1] = geometry[1];
            tableNode.geometry[static_cast<size_t>(
                hancom::graph::layout::GeometryField::AnchorListId)] = {
                    hancom::graph::layout::ObservationState::Value,
                    table.anchor.list};
            std::vector<hancom::graph::layout::LayoutNodeObservation> children;
            for (const auto& cell : table.cells) {
                hancom::graph::layout::LayoutNodeObservation cellNode;
                cellNode.kind = hancom::graph::layout::LayoutNodeKind::Cell;
                cellNode.nodeId = hancom::graph::capture::CanonicalNativeSiteIdentity(
                    hancom::graph::capture::PropertyTarget::Cell, L"tbl",
                    table.headCtrlOrdinal, {cell.listId, 0, 0},
                    table.instanceIdPresent, table.instanceId) + L":" + cell.address;
                cellNode.pageStart = {
                    static_cast<hancom::graph::layout::ObservationState>(cell.pageStart.state),
                    cell.pageStart.value};
                cellNode.pageEnd = {
                    static_cast<hancom::graph::layout::ObservationState>(cell.pageEnd.state),
                    cell.pageEnd.value};
                cellNode.geometry[static_cast<size_t>(
                    hancom::graph::layout::GeometryField::Width)] = {
                        static_cast<hancom::graph::layout::ObservationState>(
                            cell.width.state),
                        cell.width.value};
                cellNode.geometry[static_cast<size_t>(
                    hancom::graph::layout::GeometryField::Height)] = {
                        static_cast<hancom::graph::layout::ObservationState>(
                            cell.height.state),
                        cell.height.value};
                cellNode.geometry[static_cast<size_t>(
                    hancom::graph::layout::GeometryField::AnchorListId)] = {
                        hancom::graph::layout::ObservationState::Value,
                        cell.listId};
                children.push_back(cellNode);
                snapshot.nodes.push_back(std::move(cellNode));
                for (const auto& paragraph : cell.paragraphs) {
                    if (!addParagraph(paragraph)) return false;
                }
            }
            if (hancom::graph::layout::UnionPageSpans(
                    children, &tableNode.pageStart, &tableNode.pageEnd) !=
                hancom::graph::layout::CaptureStatus::Complete) {
                return false;
            }
            snapshot.nodes.push_back(std::move(tableNode));
        }
        for (const auto& image : images.images) {
            hancom::graph::capture::ControlObservation control{
                image.ctrlId, image.instanceId, image.headCtrlOrdinal, image.anchor};
            control.instanceIdPresent = image.instanceIdPresent;
            hancom::graph::layout::LayoutNodeObservation node;
            node.kind = hancom::graph::layout::LayoutNodeKind::Image;
            node.nodeId = hancom::graph::capture::CanonicalNativeSiteIdentity(
                hancom::graph::capture::PropertyTarget::Image, image.ctrlId,
                image.headCtrlOrdinal, image.anchor,
                image.instanceIdPresent, image.instanceId);
            if (!ReadPageAtPosition(context->hwp, image.anchor, &node.pageStart)) return false;
            node.pageEnd = node.pageStart;
            std::array<hancom::graph::layout::ScalarObservation, 2> geometry{};
            if (!ReadControlLayoutGeometry(context->hwp, control, &geometry)) return false;
            node.geometry[0] = geometry[0];
            node.geometry[1] = geometry[1];
            node.geometry[static_cast<size_t>(
                hancom::graph::layout::GeometryField::AnchorListId)] = {
                    hancom::graph::layout::ObservationState::Value,
                    image.anchor.list};
            snapshot.nodes.push_back(node);
            const auto& captionText = image.text[static_cast<size_t>(
                hancom::graph::images::ImageTextField::CaptionText)];
            if (captionText.state == hancom::graph::ObservationState::Value &&
                image.captionList.state ==
                    hancom::graph::ObservationState::Value) {
                hancom::graph::layout::LayoutNodeObservation caption;
                caption.kind = hancom::graph::layout::LayoutNodeKind::Caption;
                caption.nodeId = std::to_wstring(image.captionStart.list) +
                    L":" + std::to_wstring(image.captionStart.paragraph);
                auto captionEnd = image.captionStart;
                captionEnd.character += static_cast<std::int64_t>(
                    captionText.value.size());
                if (!ReadPageAtPosition(
                        context->hwp, image.captionStart,
                        &caption.pageStart) ||
                    !ReadPageAtPosition(
                        context->hwp, captionEnd, &caption.pageEnd)) {
                    return false;
                }
                const auto& captionWidth = image.scalar[static_cast<size_t>(
                    hancom::graph::images::ImageScalarField::CaptionWidth)];
                caption.geometry[static_cast<size_t>(
                    hancom::graph::layout::GeometryField::Width)] = {
                        static_cast<hancom::graph::layout::ObservationState>(
                            captionWidth.state),
                        captionWidth.value};
                caption.geometry[static_cast<size_t>(
                    hancom::graph::layout::GeometryField::AnchorListId)] = {
                        hancom::graph::layout::ObservationState::Value,
                        image.anchor.list};
                snapshot.nodes.push_back(std::move(caption));
            }
        }
        for (const auto& control : story.controls) {
            const bool specialized = std::any_of(
                tables.tables.begin(), tables.tables.end(), [&control](const auto& table) {
                    return table.headCtrlOrdinal == control.headCtrlOrdinal;
                }) || std::any_of(
                images.images.begin(), images.images.end(), [&control](const auto& image) {
                    return image.headCtrlOrdinal == control.headCtrlOrdinal;
                });
            if (specialized) continue;
            hancom::graph::layout::LayoutNodeObservation node;
            node.kind = hancom::graph::layout::LayoutNodeKind::Control;
            node.nodeId = hancom::graph::capture::CanonicalNativeSiteIdentity(
                hancom::graph::capture::PropertyTarget::Control, control.ctrlId,
                control.headCtrlOrdinal, control.anchor,
                control.instanceIdPresent, control.instanceId);
            if (!ReadPageAtPosition(context->hwp, control.anchor, &node.pageStart)) return false;
            node.pageEnd = node.pageStart;
            std::array<hancom::graph::layout::ScalarObservation, 2> geometry{};
            if (!ReadControlLayoutGeometry(context->hwp, control, &geometry)) return false;
            node.geometry[0] = geometry[0];
            node.geometry[1] = geometry[1];
            node.geometry[static_cast<size_t>(
                hancom::graph::layout::GeometryField::AnchorListId)] = {
                    hancom::graph::layout::ObservationState::Value,
                    control.anchor.list};
            snapshot.nodes.push_back(std::move(node));
        }
        output->pageCount = snapshot.pageCount;
        output->sections = std::move(snapshot.sections);
        output->nodes = std::move(snapshot.nodes);
        return true;
    } catch (...) {
        return false;
    }
}

bool DerivePerKindLayoutCertification(
    const hancom::graph::layout::LayoutSnapshot& snapshot,
    hancom::graph::capture::ReaderPayload* const payload) noexcept {
    if (payload == nullptr) return false;
    payload->layoutObservedKindCounts.fill(0);
    try {
        for (const auto& node : snapshot.nodes) {
            size_t index = 0;
            size_t requiredFields = 2;
            switch (node.kind) {
            case hancom::graph::layout::LayoutNodeKind::Paragraph:
            case hancom::graph::layout::LayoutNodeKind::Caption:
                index = 0;
                break;
            case hancom::graph::layout::LayoutNodeKind::Control:
                index = 1; requiredFields = 4;
                break;
            case hancom::graph::layout::LayoutNodeKind::Table:
                index = 2; requiredFields = 4;
                break;
            case hancom::graph::layout::LayoutNodeKind::Cell:
                index = 3;
                break;
            case hancom::graph::layout::LayoutNodeKind::Image:
                index = 4; requiredFields = 4;
                break;
            }
            const auto target = index == 0
                ? hancom::graph::capture::PropertyTarget::Paragraph
                : index == 1
                    ? hancom::graph::capture::PropertyTarget::Control
                : index == 2
                    ? hancom::graph::capture::PropertyTarget::Table
                : index == 3
                    ? hancom::graph::capture::PropertyTarget::Cell
                    : hancom::graph::capture::PropertyTarget::Image;
            const size_t observed = static_cast<size_t>(std::count_if(
                payload->layoutProperties.begin(),
                payload->layoutProperties.end(),
                [&node, target](const auto& property) {
                    return property.target == target &&
                        property.targetIdentity == node.nodeId &&
                        property.key >= 12000 && property.key <= 12003;
                }));
            if (observed != requiredFields) {
                payload->layoutObservedKindCounts.fill(0);
                return false;
            }
            ++payload->layoutObservedKindCounts[index];
        }
        return std::accumulate(
            payload->layoutObservedKindCounts.begin(),
            payload->layoutObservedKindCounts.end(), std::uint64_t{0}) ==
            snapshot.nodes.size() && !snapshot.nodes.empty();
    } catch (...) {
        payload->layoutObservedKindCounts.fill(0);
        return false;
    }
}

std::wstring ProbeLayoutGraph(
    IDispatch* const hwp,
    hancom::graph::capture::ReaderPayload* const typed = nullptr,
    const hancom::graph::layout::LayoutEnvironmentPlatformV1* const
        environmentPlatform = nullptr,
    const std::vector<hancom::graph::capture::ReaderPayload>* const
        qualifiedPayloads = nullptr) {
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tLAYOUT_GRAPH\tV1\n";
    hancom::graph::layout::StableLayoutRecord layout;
    hancom::graph::layout::CaptureStatus status =
        hancom::graph::layout::CaptureStatus::SourceFailed;
    if (qualifiedPayloads != nullptr) {
        QualifiedLayoutContext context{hwp, qualifiedPayloads};
        status = environmentPlatform == nullptr
            ? hancom::graph::layout::CaptureStableNativeLayoutV1(
                RecalculateQualifiedLayout,
                ObserveQualifiedNativeLayout,
                &context,
                hancom::graph::layout::QualificationMode::
                    AllowStableObservationWhenNotExposed,
                &layout)
            : hancom::graph::layout::CaptureStableNativeLayoutV1(
                RecalculateQualifiedLayout,
                ObserveQualifiedNativeLayout,
                &context,
                hancom::graph::layout::QualificationMode::
                    AllowStableObservationWhenNotExposed,
                *environmentPlatform,
                &layout);
        output << L"CAPTURE_PATH\tTYPED_NATIVE_V1\n";
    } else {
    CComVariant rawTable;
    CComPtr<IDispatch> table;
    if (FAILED(hancom::dispatch::PropertyGet(
            hwp,
            L"ParentCtrl",
            &rawTable)) ||
        FAILED(hancom::dispatch::AsDispatch(rawTable, table))) {
        output << L"RESULT\tINCONCLUSIVE\tCURRENT_SELECTED_TABLE_REQUIRED";
        return output.str();
    }
    CComVariant rawCtrlId;
    CComVariant rawInstanceId;
    std::wstring ctrlId;
    std::wstring instanceId;
    if (FAILED(hancom::dispatch::PropertyGet(
            table,
            L"CtrlID",
            &rawCtrlId)) ||
        FAILED(hancom::dispatch::AsString(rawCtrlId, &ctrlId)) ||
        ctrlId != L"tbl" ||
        FAILED(hancom::dispatch::Method(
            table,
            L"GetCtrlInstID",
            {},
            &rawInstanceId)) ||
        FAILED(hancom::dispatch::AsString(
            rawInstanceId,
            &instanceId))) {
        output << L"RESULT\tINCONCLUSIVE\tTYPED_TABLE_CONTRACT_REQUIRED";
        return output.str();
    }
    status = environmentPlatform == nullptr
        ? hancom::graph::layout::CaptureCurrentTableLayoutFromNative(
            hwp, instanceId, &layout)
        : hancom::graph::layout::CaptureCurrentTableLayoutFromNative(
            hwp, instanceId, *environmentPlatform, &layout);
    }
    output << L"CAPTURE_STATUS\t" << static_cast<int>(status) << L'\n'
           << L"PUBLICATION_STATE\t"
           << (status ==
                hancom::graph::layout::CaptureStatus::Complete
                ? L"STABLE_OBSERVED"
                : L"NOT_PUBLISHED") << L'\n'
           << L"LAYOUT_ROOT\t" << layout.layoutRoot << L'\n'
           << L"PAGE_COUNT\t" << layout.snapshot.pageCount << L'\n'
           << L"NODE_COUNT\t" << layout.snapshot.nodes.size() << L'\n';
    for (size_t attempt = 0;
         attempt < layout.settleAttempts.size();
         ++attempt) {
        output << L"SETTLE_ROUTE\t" << attempt << L'\t'
               << static_cast<int>(
                    layout.settleAttempts[attempt].direct) << L'\t'
               << static_cast<int>(
                    layout.settleAttempts[attempt].action) << L'\t'
               << static_cast<int>(
                    layout.settleAttempts[attempt].aggregate) << L'\n';
    }
    for (const hancom::graph::layout::LayoutNodeObservation& node :
         layout.snapshot.nodes) {
        if (typed != nullptr) {
            const auto addLayout = [typed, &node](
                const hancom::graph::PropertyKeyId key,
                const hancom::graph::layout::ScalarObservation& observed) {
                hancom::graph::capture::PropertyObservation property;
                switch (node.kind) {
                case hancom::graph::layout::LayoutNodeKind::Paragraph:
                    property.target =
                        hancom::graph::capture::PropertyTarget::Paragraph;
                    break;
                case hancom::graph::layout::LayoutNodeKind::Control:
                    property.target =
                        hancom::graph::capture::PropertyTarget::Control;
                    break;
                case hancom::graph::layout::LayoutNodeKind::Table:
                    property.target =
                        hancom::graph::capture::PropertyTarget::Table;
                    break;
                case hancom::graph::layout::LayoutNodeKind::Cell:
                    property.target =
                        hancom::graph::capture::PropertyTarget::Cell;
                    break;
                case hancom::graph::layout::LayoutNodeKind::Image:
                    property.target =
                        hancom::graph::capture::PropertyTarget::Image;
                    break;
                case hancom::graph::layout::LayoutNodeKind::Caption:
                    property.target =
                        hancom::graph::capture::PropertyTarget::Paragraph;
                    break;
                }
                property.targetIdentity = node.nodeId;
                property.ownerField = 10;
                property.key = key;
                property.scalar = key == 12000 || key == 12001
                    ? hancom::graph::ScalarTag::Uint64
                    : hancom::graph::ScalarTag::HWPUNIT64;
                property.state = static_cast<hancom::graph::ObservationState>(
                    observed.state);
                property.origin = observed.state ==
                        hancom::graph::layout::ObservationState::Value
                    ? hancom::graph::PropertyOrigin::Generated
                    : hancom::graph::PropertyOrigin::Unavailable;
                property.integerValue = observed.value;
                typed->layoutProperties.push_back(std::move(property));
            };
            addLayout(12000, node.pageStart);
            addLayout(12001, node.pageEnd);
            if (node.kind ==
                    hancom::graph::layout::LayoutNodeKind::Control ||
                node.kind ==
                    hancom::graph::layout::LayoutNodeKind::Table ||
                node.kind ==
                    hancom::graph::layout::LayoutNodeKind::Image) {
                addLayout(12002, node.geometry[0]);
                addLayout(12003, node.geometry[1]);
            }
        }
        output << L"LAYOUT_NODE\t" << node.nodeId << L'\t'
               << static_cast<int>(node.kind) << L'\t'
               << static_cast<int>(node.pageStart.state) << L'\t'
               << node.pageStart.value << L'\t'
               << static_cast<int>(node.pageEnd.state) << L'\t'
               << node.pageEnd.value;
        for (const auto& geometry : node.geometry) {
            output << L'\t' << static_cast<int>(geometry.state)
                   << L'\t' << geometry.value;
        }
        output << L'\n';
    }
    if (typed != nullptr) {
        typed->layoutEnvironment = layout.snapshot.environment;
        const bool complete =
            status == hancom::graph::layout::CaptureStatus::Complete &&
            !layout.snapshot.nodes.empty() &&
            hancom::graph::layout::ValidateLayoutEnvironmentV1(
                typed->layoutEnvironment);
        typed->layoutPerKindComplete = complete &&
            DerivePerKindLayoutCertification(layout.snapshot, typed);
        typed->outcome = complete
            ? hancom::graph::capture::ReaderOutcome::Complete
            : status ==
                    hancom::graph::layout::CaptureStatus::LayoutUnstable
                ? hancom::graph::capture::ReaderOutcome::Inconclusive
                : hancom::graph::capture::ReaderOutcome::Failed;
        typed->coverage = complete
            ? hancom::graph::CoverageState::Complete
            : hancom::graph::CoverageState::ReadFailed;
    }
    output << L"RESULT\t"
           << (status ==
                hancom::graph::layout::CaptureStatus::Complete
                ? L"PASS"
                : status ==
                    hancom::graph::layout::CaptureStatus::LayoutUnstable
                ? L"FAIL\tLAYOUT_UNSTABLE"
                : L"FAIL\tLAYOUT_CAPTURE");
    return output.str();
}

std::wstring ProbeTableGraph(
    IDispatch* const hwp,
    hancom::graph::capture::ReaderPayload* const typed = nullptr,
    IDispatch* const authoritativeTable = nullptr,
    const bool instanceIdUniquelyQualified = false,
    const bool renderDiagnostics = true,
    const hancom::graph::capture::ControlObservation* const
        validatedControl = nullptr,
    const hancom::inspection::TableDispatchTypeContext* const
        dispatchTypes = nullptr) {
    static constexpr CallSpec parent{
        L"ParentCtrl",13,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec ctrlId{
        L"CtrlID",2,DISPATCH_PROPERTYGET,0,VT_BSTR};
    static constexpr CallSpec instance{
        L"GetCtrlInstID",15001,DISPATCH_METHOD,0,VT_BSTR};
    static constexpr CallSpec properties{
        L"Properties",6,DISPATCH_PROPERTYGET,0,VT_DISPATCH};
    static constexpr CallSpec repeatHeader{
        L"RepeatHeader",16928,DISPATCH_PROPERTYGET,0,VT_UI2};
    std::unique_ptr<std::wostringstream> output;
    if (renderDiagnostics) {
        output = std::make_unique<std::wostringstream>();
        ++g_tableGraphDiagnosticCounters.probes;
        *output << L"HCV1\tCAPABILITY\tTABLE_GRAPH\tV1\n";
    }
    CComPtr<IDispatch> table = authoritativeTable;
    if (table == nullptr) {
        const RawCall parentCall = InvokeOneShot(
            hwp, parent, {}, dispatchTypes == nullptr,
            dispatchTypes == nullptr ? nullptr : &dispatchTypes->root);
        if (!AsDispatch(parentCall, table)) {
            if (output != nullptr) {
                *output << L"RESULT\tINCONCLUSIVE\tCURRENT_SELECTED_TABLE_REQUIRED";
                return output->str();
            }
            return {};
        }
    }
    hancom::graph::capture::TableObservation observed;
    if (validatedControl != nullptr &&
        validatedControl->ctrlId == L"tbl" &&
        validatedControl->instanceIdPresent) {
        observed.instanceIdPresent = true;
        observed.instanceId = validatedControl->instanceId;
    } else {
        const RawCall idCall = InvokeOneShot(
            table, ctrlId, {}, dispatchTypes == nullptr,
            dispatchTypes == nullptr ? nullptr : &dispatchTypes->control);
        const RawCall instanceCall = InvokeOneShot(
            table, instance, {}, dispatchTypes == nullptr,
            dispatchTypes == nullptr ? nullptr : &dispatchTypes->control);
        if (idCall.result.vt != VT_BSTR ||
            BstrText(idCall.result.bstrVal) != L"tbl" ||
            !AdaptTableInstanceIdObservation(instanceCall, &observed)) {
            if (output != nullptr) {
                *output <<
                    L"RESULT\tINCONCLUSIVE\tTYPED_TABLE_CONTRACT_REQUIRED";
                return output->str();
            }
            return {};
        }
    }
    const std::wstring& sessionInstanceId = observed.instanceId;
    bool repeatHeaderExposed = false;
    bool repeatHeaderValue = false;
    LONG authoritativeRows = 0;
    LONG authoritativeColumns = 0;
    CComVariant rawProperties;
    CComPtr<IDispatch> tableSet;
    const HRESULT propertiesStatus = dispatchTypes == nullptr
        ? hancom::dispatch::PropertyGet(
            table, properties.name, &rawProperties)
        : hancom::dispatch::PropertyGetQualified(
            table, dispatchTypes->control, properties.name, &rawProperties);
    if (SUCCEEDED(propertiesStatus) &&
        SUCCEEDED(hancom::dispatch::AsDispatch(rawProperties, tableSet))) {
        CComVariant rawRows;
        CComVariant rawColumns;
        const HRESULT rowsStatus = dispatchTypes == nullptr
            ? hancom::dispatch::PropertyGet(
                tableSet, L"RowCount", &rawRows)
            : hancom::dispatch::PropertyGetQualified(
                tableSet, dispatchTypes->table, L"RowCount", &rawRows);
        const HRESULT columnsStatus = SUCCEEDED(rowsStatus)
            ? dispatchTypes == nullptr
                ? hancom::dispatch::PropertyGet(
                    tableSet, L"ColCount", &rawColumns)
                : hancom::dispatch::PropertyGetQualified(
                    tableSet, dispatchTypes->table, L"ColCount", &rawColumns)
            : rowsStatus;
        if (FAILED(rowsStatus) || FAILED(columnsStatus) ||
            FAILED(hancom::dispatch::AsLong(rawRows, &authoritativeRows)) ||
            FAILED(hancom::dispatch::AsLong(
                rawColumns, &authoritativeColumns)) ||
            authoritativeRows < 1 || authoritativeColumns < 1) {
            authoritativeRows = 0;
            authoritativeColumns = 0;
        }
        const RawCall repeatCall = InvokeOneShot(
            tableSet, repeatHeader, {}, false,
            dispatchTypes == nullptr ? nullptr : &dispatchTypes->table);
        if (repeatCall.semantic == SemanticStatus::Pass) {
            if (repeatCall.result.vt == VT_UI2) {
                repeatHeaderExposed = true;
                repeatHeaderValue = repeatCall.result.uiVal != 0;
            } else if (repeatCall.result.vt == VT_BOOL) {
                repeatHeaderExposed = true;
                repeatHeaderValue =
                    repeatCall.result.boolVal != VARIANT_FALSE;
            }
        }
    }
    if ((authoritativeRows < 1 || authoritativeColumns < 1) &&
        !hancom::inspection::ReadAuthoritativeTableDimensions(
            hwp, table, sessionInstanceId, instanceIdUniquelyQualified,
            &authoritativeRows, &authoritativeColumns, true,
            dispatchTypes)) {
        authoritativeRows = 0;
        authoritativeColumns = 0;
    }
    hancom::graph::tables::TableGraphRecord graph;
    std::wstring error;
    const std::vector<hancom::graph::tables::HeaderCellObservation>
        noHeaderObservations;
    std::wstring* const errorSink = renderDiagnostics ? &error : nullptr;
    if (errorSink != nullptr) {
        ++g_tableGraphDiagnosticCounters.errorSinks;
    }
    const hancom::graph::tables::BuildStatus status =
        observed.instanceIdPresent
        ? hancom::graph::tables::CaptureTableGraphFromNativeWithDimensions(
              hwp,
              sessionInstanceId,
              repeatHeaderExposed,
              repeatHeaderValue,
              authoritativeRows,
              authoritativeColumns,
              noHeaderObservations,
              &graph,
              errorSink,
              dispatchTypes)
        : hancom::graph::tables::BuildStatus::SourceFailed;
    const bool instanceIdUnavailable = !observed.instanceIdPresent;
    if (typed != nullptr &&
        (status == hancom::graph::tables::BuildStatus::Complete ||
         instanceIdUnavailable)) {
        observed.rowCount = static_cast<std::uint64_t>(graph.rowCount);
        observed.columnCount = static_cast<std::uint64_t>(graph.columnCount);
        const auto cellState = [](
            const hancom::graph::tables::CellObservationState state) {
            return state == hancom::graph::tables::CellObservationState::Value
                ? hancom::graph::ObservationState::Value
                : state == hancom::graph::tables::CellObservationState::NotExposed
                    ? hancom::graph::ObservationState::NotExposed
                    : hancom::graph::ObservationState::ReadFailed;
        };
        for (const auto& cell : graph.physicalCells) {
            hancom::graph::capture::CellObservation physical;
            physical.address = cell.address;
            physical.listState = hancom::graph::ObservationState::Value;
            physical.listId = cell.listId;
            physical.width = {
                cell.width >= 0 ? hancom::graph::ObservationState::Value
                                : hancom::graph::ObservationState::NotExposed,
                cell.width};
            physical.height = {
                cell.height >= 0 ? hancom::graph::ObservationState::Value
                                 : hancom::graph::ObservationState::NotExposed,
                cell.height};
            physical.pageStart = {
                cell.pageStart >= 1 ? hancom::graph::ObservationState::Value
                                    : hancom::graph::ObservationState::ReadFailed,
                cell.pageStart};
            physical.pageEnd = {
                cell.pageEnd >= 1 ? hancom::graph::ObservationState::Value
                                  : hancom::graph::ObservationState::ReadFailed,
                cell.pageEnd};
            physical.text = {
                hancom::graph::ObservationState::Value,
                cell.text};
            const auto addCellProperty = [&physical, &cellState](
                const hancom::graph::PropertyKeyId key,
                const hancom::graph::ScalarTag scalar,
                const hancom::graph::tables::IntegerObservation& source) {
                hancom::graph::capture::PropertyObservation property;
                property.target = hancom::graph::capture::PropertyTarget::Cell;
                property.ownerField = 109;
                property.key = key;
                property.scalar = scalar;
                property.state = cellState(source.state);
                property.origin = hancom::graph::PropertyOrigin::Direct;
                property.integerValue = source.value;
                physical.properties.push_back(std::move(property));
            };
            hancom::graph::tables::IntegerObservation width;
            width.state = cell.width >= 0
                ? hancom::graph::tables::CellObservationState::Value
                : hancom::graph::tables::CellObservationState::NotExposed;
            width.value = cell.width;
            hancom::graph::tables::IntegerObservation height;
            height.state = cell.height >= 0
                ? hancom::graph::tables::CellObservationState::Value
                : hancom::graph::tables::CellObservationState::NotExposed;
            height.value = cell.height;
            addCellProperty(10000, hancom::graph::ScalarTag::HWPUNIT64, width);
            addCellProperty(10001, hancom::graph::ScalarTag::HWPUNIT64, height);
            addCellProperty(10002, hancom::graph::ScalarTag::HWPUNIT64,
                            cell.appearance.marginLeft);
            addCellProperty(10003, hancom::graph::ScalarTag::HWPUNIT64,
                            cell.appearance.marginRight);
            addCellProperty(10004, hancom::graph::ScalarTag::HWPUNIT64,
                            cell.appearance.marginTop);
            addCellProperty(10005, hancom::graph::ScalarTag::HWPUNIT64,
                            cell.appearance.marginBottom);
            addCellProperty(10006, hancom::graph::ScalarTag::Enum,
                            cell.appearance.verticalAlign);
            addCellProperty(10008, hancom::graph::ScalarTag::BGR,
                            cell.appearance.fillColor);
            addCellProperty(10009, hancom::graph::ScalarTag::Sint64,
                            cell.appearance.fillBrush);
            for (size_t side = 0; side < 4; ++side) {
                const hancom::graph::PropertyKeyId base =
                    static_cast<hancom::graph::PropertyKeyId>(10010 + side * 3);
                addCellProperty(base, hancom::graph::ScalarTag::Enum,
                                cell.appearance.borderType[side]);
                addCellProperty(base + 1, hancom::graph::ScalarTag::Sint64,
                                cell.appearance.borderWidth[side]);
                addCellProperty(base + 2, hancom::graph::ScalarTag::BGR,
                                cell.appearance.borderColor[side]);
            }
            addCellProperty(10022, hancom::graph::ScalarTag::Enum,
                            cell.appearance.alignment);
            hancom::graph::capture::PropertyObservation faceName;
            faceName.target = hancom::graph::capture::PropertyTarget::Cell;
            faceName.ownerField = 109;
            faceName.key = 10023;
            faceName.scalar = hancom::graph::ScalarTag::UTF16;
            faceName.state = cellState(cell.appearance.faceName.state);
            faceName.origin = hancom::graph::PropertyOrigin::Direct;
            faceName.textValue = cell.appearance.faceName.value;
            physical.properties.push_back(std::move(faceName));
            addCellProperty(10024, hancom::graph::ScalarTag::HWPUNIT64,
                            cell.appearance.characterHeight);
            addCellProperty(10025, hancom::graph::ScalarTag::Bool,
                            cell.appearance.bold);
            for (const auto& interval : graph.physicalIntervals) {
                if (interval.ownerAddress == cell.address) {
                    physical.row1 = static_cast<std::uint64_t>(
                        interval.rowBegin1);
                    physical.column1 = static_cast<std::uint64_t>(
                        interval.columnBegin1);
                    physical.rowSpan = static_cast<std::uint64_t>(
                        interval.rowEnd1 - interval.rowBegin1);
                    physical.columnSpan = static_cast<std::uint64_t>(
                        interval.columnEnd1 - interval.columnBegin1);
                }
            }
            observed.cells.push_back(std::move(physical));
        }
        hancom::graph::capture::PropertyObservation repeat;
        repeat.target = hancom::graph::capture::PropertyTarget::Table;
        repeat.ownerField = 106;
        repeat.key = 9004;
        repeat.scalar = hancom::graph::ScalarTag::Bool;
        repeat.state = repeatHeaderExposed
            ? hancom::graph::ObservationState::Value
            : hancom::graph::ObservationState::NotExposed;
        repeat.origin = hancom::graph::PropertyOrigin::Direct;
        repeat.integerValue = repeatHeaderValue ? 1 : 0;
        observed.properties.push_back(std::move(repeat));
        typed->tables.push_back(std::move(observed));
        for (const auto& nested : graph.nestedTables) {
            hancom::graph::capture::TableObservation nestedObserved;
            nestedObserved.instanceId = nested.sessionInstanceId;
            nestedObserved.instanceIdPresent = true;
            nestedObserved.anchor.list = nested.anchorListId;
            nestedObserved.hostTableInstanceId = graph.sessionInstanceId;
            nestedObserved.hostTableInstanceIdPresent = true;
            nestedObserved.hostCellAddress = nested.hostAddress;
            typed->tables.push_back(std::move(nestedObserved));
        }
    }
    const bool complete =
        status == hancom::graph::tables::BuildStatus::Complete &&
        !graph.physicalCells.empty() &&
        graph.physicalCells.size() == graph.physicalIntervals.size();
    const bool terminal = instanceIdUnavailable;
    if (typed != nullptr) {
        typed->outcome = complete || terminal
            ? hancom::graph::capture::ReaderOutcome::Complete
            : status == hancom::graph::tables::BuildStatus::Complete
                ? hancom::graph::capture::ReaderOutcome::Inconclusive
                : hancom::graph::capture::ReaderOutcome::Failed;
        typed->coverage = complete
            ? hancom::graph::CoverageState::Complete
            : terminal || status ==
                    hancom::graph::tables::BuildStatus::Complete
                ? hancom::graph::CoverageState::NotExposed
                : hancom::graph::CoverageState::ReadFailed;
    }
    if (output == nullptr) {
        return {};
    }
    g_tableGraphDiagnosticCounters.cellRows +=
        static_cast<std::uint64_t>(graph.physicalCells.size());
    *output << L"CAPTURE_STATUS\t" << static_cast<int>(status) << L'\n'
           << L"INSTANCE_ID_PRESENT\t" << !instanceIdUnavailable << L'\n'
           << L"TABLE_INSTANCE\t" << sessionInstanceId << L'\n'
           << L"ROWS\t" << graph.rowCount << L'\n'
           << L"COLUMNS\t" << graph.columnCount << L'\n'
           << L"PHYSICAL_CELLS\t" << graph.physicalCells.size() << L'\n'
           << L"PHYSICAL_INTERVALS\t"
           << graph.physicalIntervals.size() << L'\n'
           << L"REPEAT_HEADER_EXPOSED\t" << repeatHeaderExposed << L'\n'
           << L"REPEAT_HEADER\t" << repeatHeaderValue << L'\n';
    for (const hancom::graph::tables::TableCellRecord& cell :
         graph.physicalCells) {
        *output << L"CELL\t" << cell.address << L'\t'
               << cell.listId << L'\t'
               << cell.row << L'\t'
               << cell.column << L'\t'
               << cell.rowSpan << L'\t'
               << cell.columnSpan << L'\t'
               << cell.pageStart << L'\t'
               << cell.pageEnd << L'\t'
               << cell.text.size() << L'\t'
               << static_cast<int>(cell.appearance.fillColor.state) << L'\t'
               << static_cast<int>(
                    cell.appearance.borderColor[0].state) << L'\t'
               << static_cast<int>(
                    cell.appearance.borderColor[1].state) << L'\t'
               << static_cast<int>(
                    cell.appearance.borderColor[2].state) << L'\t'
               << static_cast<int>(
                    cell.appearance.borderColor[3].state) << L'\t'
               << static_cast<int>(cell.textRunsState) << L'\n';
    }
    if (!complete && !terminal && !error.empty()) {
        *output << L"ERROR\t" << error << L'\n';
    }
    *output << L"RESULT\t" << (complete || terminal ? L"PASS" : L"FAIL");
    return output->str();
}

std::atomic<std::uint64_t> gCaptureSpoolSerial{0};

bool PrepareCaptureSpoolRoot(
    const wchar_t* const prefix,
    std::filesystem::path* const spoolRoot) {
    if (prefix == nullptr || spoolRoot == nullptr) return false;
    wchar_t temporary[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, temporary) == 0) return false;
    const std::uint64_t serial =
        gCaptureSpoolSerial.fetch_add(1, std::memory_order_relaxed) + 1;
    *spoolRoot = std::filesystem::path(temporary) /
        (std::wstring(prefix) + L"-" +
         std::to_wstring(GetCurrentProcessId()) + L"-" +
         std::to_wstring(GetTickCount64()) + L"-" +
         std::to_wstring(serial));
    std::error_code directoryError;
    if (std::filesystem::create_directories(*spoolRoot, directoryError)) {
        return true;
    }
    return directoryError == std::errc::file_exists ||
        std::filesystem::is_directory(*spoolRoot);
}

struct CaptureQualificationContext final {
    ~CaptureQualificationContext() noexcept {
        if (cancellationCheckpoint != nullptr) {
            CloseHandle(cancellationCheckpoint);
        }
        if (cancellationRequest != nullptr) {
            CloseHandle(cancellationRequest);
        }
    }

    IDispatch* hwp = nullptr;
    const hancom::graph::layout::LayoutEnvironmentPlatformV1*
        environmentPlatform = nullptr;
    bool baselineCaptured = false;
    hancom::com_state::Position baselineCursor{};
    hancom::com_state::Selection baselineSelection{};
    bool baselineModified = false;
    std::wstring baselineContent{};
    std::vector<std::wstring> readerOutputs{};
    std::vector<hancom::graph::capture::ReaderPayload> readerPayloads{};
    std::filesystem::path spoolRoot{};
    std::filesystem::path preparedStoreRoot{};
    std::array<std::unique_ptr<
        hancom::graph::capture::CaptureSpool>, 2> spools{};
    hancom::graph::capture::CaptureIdentityArena captureArena{};
    hancom::graph::capture::AttemptResult committedResult{};
    std::wstring committedCaptionDiagnostics{};
    std::wstring readerFailure{};
    size_t readerCalls = 0;
    std::uint64_t captureSessionSerial = 0;
    std::uint64_t currentAttemptReceipt = 0;
    bool captureSessionActive = false;
    HANDLE cancellationCheckpoint = nullptr;
    HANDLE cancellationRequest = nullptr;
    bool cancellationObserved = false;
    bool committedEffectiveProducerOwned = false;
    bool committedLayoutFamiliesComplete = false;
    bool committedTypedLayoutCapturePath = false;
    bool captionQualificationExact = false;
    bool captionQualificationCrossOwnerRejected = false;
    bool captionQualificationStaleRejected = false;
    bool captionQualificationUnsetRejected = false;
};

void RecordCaptureProgress(
    void* const raw,
    const hancom::graph::capture::CaptureProgressPoint point,
    const std::uint64_t attempt,
    const std::uint64_t reader) noexcept {
    if (raw == nullptr) return;
    hancom::graph::capture::RecordCaptureProgressPoint(
        point, attempt, reader);
}

void RecordCanonicalPass(
    void* const raw, const size_t pass, const bool complete) noexcept {
    using Point = hancom::graph::capture::CaptureProgressPoint;
    const Point point = pass == 1
        ? (complete ? Point::CanonicalPass1End : Point::CanonicalPass1Start)
        : pass == 2
            ? (complete ? Point::CanonicalPass2End : Point::CanonicalPass2Start)
            : (complete ? Point::CanonicalPass3End : Point::CanonicalPass3Start);
    RecordCaptureProgress(raw, point, pass, complete ? 1 : 0);
}

hancom::graph::Sha256 HashWideText(const std::wstring& value) {
    hancom::graph::Sha256 digest;
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD objectBytes = 0;
    DWORD written = 0;
    if (BCryptOpenAlgorithmProvider(
            &algorithm,
            BCRYPT_SHA256_ALGORITHM,
            nullptr,
            0) != 0 ||
        BCryptGetProperty(
            algorithm,
            BCRYPT_OBJECT_LENGTH,
            reinterpret_cast<PUCHAR>(&objectBytes),
            sizeof(objectBytes),
            &written,
            0) != 0) {
        if (algorithm != nullptr) {
            BCryptCloseAlgorithmProvider(algorithm, 0);
        }
        return digest;
    }
    std::vector<UCHAR> object(objectBytes);
    if (BCryptCreateHash(
            algorithm,
            &hash,
            object.data(),
            objectBytes,
            nullptr,
            0,
            0) != 0) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
        return digest;
    }
    static_cast<void>(BCryptHashData(
        hash,
        reinterpret_cast<PUCHAR>(
            const_cast<wchar_t*>(value.data())),
        static_cast<ULONG>(value.size() * sizeof(wchar_t)),
        0));
    static_cast<void>(BCryptFinishHash(
        hash,
        digest.bytes.data(),
        static_cast<ULONG>(digest.bytes.size()),
        0));
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    return digest;
}

hancom::graph::store::CanonicalStream StreamFact(
    const std::wstring& value) {
    return {
        value.size() * sizeof(wchar_t),
        HashWideText(value),
    };
}

std::wstring PositionFact(
    const hancom::com_state::Position& position) {
    return std::to_wstring(position.list) + L":" +
        std::to_wstring(position.paragraph) + L":" +
        std::to_wstring(position.character);
}

std::wstring DocumentStateFact(
    const hancom::official_api::DocumentState& state) {
    return std::to_wstring(state.pageCount) + L':' +
        std::to_wstring(state.controlCount) + L':' +
        std::to_wstring(state.controlHash) + L':' +
        std::to_wstring(state.modified);
}

bool RunCoordinatorAction(IDispatch* hwp, const wchar_t* name) noexcept;

std::wstring SelectionFact(
    const hancom::com_state::Selection& selection) {
    std::wostringstream output;
    output << selection.selected << L':' << selection.mode << L':'
           << PositionFact(selection.start) << L':'
           << PositionFact(selection.end) << L':'
           << selection.controlType << L':'
           << selection.controlInstancePresent << L':'
           << selection.controlInstance;
    for (const std::wstring& address : selection.cellAddresses) {
        output << L':' << address;
    }
    return output.str();
}

bool CaptureCoordinatorState(
    void* const raw,
    hancom::graph::store::CapturedState* const output) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr || context->hwp == nullptr ||
        output == nullptr) {
        return false;
    }
    hancom::com_state::Position cursor;
    hancom::com_state::Selection selection;
    hancom::com_state::SelectionCaptureFailure failure;
    if (FAILED(hancom::com_state::CapturePosition(
            context->hwp,
            &cursor))) {
        context->readerFailure = L"baseline-position";
        return false;
    }
    if (!hancom::com_state::CaptureSelection(
            context->hwp,
            &selection,
            hancom::com_state::SelectionCapturePolicy::RequiredControl,
            &failure) &&
        !hancom::com_state::CaptureSelection(
            context->hwp,
            &selection,
            hancom::com_state::SelectionCapturePolicy::Basic,
            &failure)) {
        context->readerFailure = L"baseline-selection";
        return false;
    }
    if (!hancom::com_state::CanRestoreSelection(selection)) {
        // A leftover cell/control selection after a prior GraphOpen parks the
        // caret in a list (e.g. 5812:1) that StorySpine never emits. Drop the
        // selection and return to the document start so readers bind body
        // paragraphs that actually exist in the assembled graph.
        static_cast<void>(RunCoordinatorAction(context->hwp, L"Cancel"));
        static_cast<void>(RunCoordinatorAction(context->hwp, L"MoveDocBegin"));
        if (FAILED(hancom::com_state::CapturePosition(
                context->hwp, &cursor))) {
            context->readerFailure = L"baseline-doc-begin";
            return false;
        }
        selection = {};
        selection.mode = hancom::com_state::kSelectionNone;
        selection.start = cursor;
        selection.end = cursor;
    }
    static constexpr CallSpec modifiedSpec{
        L"IsModified",1,DISPATCH_PROPERTYGET,0,VT_BOOL};
    const RawCall modified =
        InvokeOneShot(context->hwp, modifiedSpec, {}, true);
    if (modified.semantic != SemanticStatus::Pass &&
        modified.semantic != SemanticStatus::FalseResult) {
        context->readerFailure = L"baseline-modified";
        return false;
    }
    std::wstring content = FormatDocumentContentSignature(
        CaptureDocumentContentSignature(context->hwp));
    if (content.empty()) {
        // GetTextFile/PageCount can fail immediately after a cancelled
        // GraphOpen even though the document is still open. A DocumentState
        // hash is enough for attempt-to-attempt equality on this path.
        content = L"STATE " + DocumentStateFact(
            CaptureDocumentState(context->hwp));
    }
    const bool modifiedValue =
        modified.result.boolVal != VARIANT_FALSE;
    output->route = StreamFact(content);
    output->cursor = StreamFact(PositionFact(cursor));
    output->selection = StreamFact(SelectionFact(selection));
    output->modified = modifiedValue;
    if (!context->baselineCaptured) {
        context->baselineCaptured = true;
        context->baselineCursor = cursor;
        context->baselineSelection = selection;
        context->baselineModified = modifiedValue;
        context->baselineContent = content;
    }
    return true;
}

bool BeginCoordinatorSession(
    void* const raw,
    const std::uint64_t serial) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr || serial == 0 ||
        context->captureSessionActive) {
        return false;
    }
    context->captureSessionSerial = serial;
    context->captureSessionActive = true;
    bool cleaned = true;
    for (auto& spool : context->spools) {
        if (spool != nullptr) {
            cleaned = spool->Reset() && cleaned;
            spool.reset();
        }
    }
    if (!cleaned) return false;
    context->captureArena.ResetSession();
    context->readerOutputs.clear();
    context->readerPayloads.clear();
    context->baselineCaptured = false;
    return true;
}

bool PrepareCoordinatorPublication(
    void* const raw,
    const std::uint64_t serial) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr || !context->captureSessionActive ||
        context->captureSessionSerial != serial ||
        context->spools[1] == nullptr) {
        return false;
    }
    if (context->spools[0] != nullptr) {
        if (!context->spools[0]->Reset()) return false;
        context->spools[0].reset();
    }
    return true;
}

bool AbortCoordinatorSession(
    void* const raw,
    const std::uint64_t serial) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr || !context->captureSessionActive ||
        context->captureSessionSerial != serial) {
        return false;
    }
    bool cleaned = true;
    for (auto& spool : context->spools) {
        if (spool != nullptr) {
            cleaned = spool->Reset() && cleaned;
            spool.reset();
        }
    }
    context->readerOutputs.clear();
    context->readerPayloads.clear();
    context->captureArena.ResetSession();
    context->captureSessionSerial = 0;
    context->captureSessionActive = false;
    return cleaned;
}

bool EffectivePropertiesExcludeGeneratedRules(
    const CaptureQualificationContext& context) noexcept;
bool RequiredLayoutFamiliesComplete(
    const CaptureQualificationContext& context) noexcept;

void CommitCoordinatorSession(
    void* const raw,
    const std::uint64_t serial) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr || !context->captureSessionActive ||
        context->captureSessionSerial != serial) {
        return;
    }
    context->committedCaptionDiagnostics.clear();
    if (context->readerPayloads.size() > static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::ImagesShapesCaptions)) {
        std::wostringstream captions;
        const auto& images = context->readerPayloads[static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::ImagesShapesCaptions)];
        for (const auto& image : images.images) {
            captions << L"CAPTION_LOCATION\tORDINAL=" << image.headCtrlOrdinal
                     << L"\tSTATE="
                     << static_cast<unsigned>(image.captionList.state)
                     << L"\tLIST=" << image.captionList.value
                     << L"\tPARAGRAPH=" << image.captionStart.paragraph
                     << L"\tCHARACTER=" << image.captionStart.character
                     << L'\n';
        }
        context->committedCaptionDiagnostics = captions.str();
    }
    context->committedEffectiveProducerOwned =
        EffectivePropertiesExcludeGeneratedRules(*context);
    context->committedLayoutFamiliesComplete =
        RequiredLayoutFamiliesComplete(*context);
    context->committedTypedLayoutCapturePath = std::any_of(
        context->readerOutputs.begin(), context->readerOutputs.end(),
        [](const std::wstring& output) {
            return output.find(L"CAPTURE_PATH\tTYPED_NATIVE_V1") !=
                std::wstring::npos;
        });
    // Store publication has already copied every source byte. Preserve only
    // bounded manifest facts for diagnostics, then release all candidate state.
    if (context->spools[1] != nullptr) {
        context->committedResult = context->spools[1]->Result();
        context->committedResult.records = {};
        context->committedResult.index = {};
        context->committedResult.blobContext = nullptr;
        context->committedResult.readBlob = nullptr;
    }
    for (auto& spool : context->spools) {
        if (spool != nullptr) {
            static_cast<void>(spool->Reset());
            spool.reset();
        }
    }
    context->readerOutputs.clear();
    context->readerPayloads.clear();
    context->captureArena.ResetSession();
    context->captureSessionSerial = 0;
    context->captureSessionActive = false;
}

bool BeginCoordinatorAttempt(
    void* const raw,
    const size_t attempt) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr) {
        return false;
    }
    context->readerOutputs.clear();
    context->readerPayloads.clear();
    context->currentAttemptReceipt =
        (context->captureSessionSerial << 2) ^
        (static_cast<std::uint64_t>(attempt) + 1);
    if (context->currentAttemptReceipt == 0) {
        context->currentAttemptReceipt =
            static_cast<std::uint64_t>(attempt) + 1;
    }
    return true;
}

bool RunCoordinatorAction(
    IDispatch* const hwp,
    const wchar_t* const name) noexcept {
    CComVariant rawAction;
    CComPtr<IDispatch> action;
    CComVariant rawResult;
    bool result = false;
    return hwp != nullptr && name != nullptr &&
        SUCCEEDED(hancom::dispatch::PropertyGet(
            hwp,
            L"HAction",
            &rawAction)) &&
        SUCCEEDED(hancom::dispatch::AsDispatch(rawAction, action)) &&
        SUCCEEDED(hancom::dispatch::Method(
            action,
            L"Run",
            {CComVariant(name)},
            &rawResult)) &&
        SUCCEEDED(hancom::dispatch::AsBool(rawResult, &result)) &&
        result;
}

bool RunQualifiedReader(
    void* const raw,
    const size_t,
    const size_t reader) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr || context->hwp == nullptr) {
        return false;
    }
    std::wstring output;
    hancom::graph::capture::ReaderPayload payload;
    payload.reader =
        static_cast<hancom::graph::capture::QualifiedReader>(reader);
    switch (payload.reader) {
    case hancom::graph::capture::QualifiedReader::StorySpine:
        if (!RunCoordinatorAction(context->hwp, L"MoveDocBegin")) {
            return false;
        }
        output = ProbeStorySpine(context->hwp, &payload);
        break;
    case hancom::graph::capture::QualifiedReader::TextAtoms:
        output = ProbeText(context->hwp, true, &payload);
        break;
    case hancom::graph::capture::QualifiedReader::EffectiveProperties: {
        std::wstring paragraphIdentity;
        for (const auto& prior : context->readerPayloads) {
            if (prior.paragraphs.empty()) continue;
            paragraphIdentity =
                std::to_wstring(prior.paragraphs.front().start.list) + L":" +
                std::to_wstring(prior.paragraphs.front().start.paragraph);
            break;
        }
        if (paragraphIdentity.empty()) {
            if (!RunCoordinatorAction(context->hwp, L"MoveDocBegin")) {
                return false;
            }
            hancom::com_state::Position current;
            if (FAILED(hancom::com_state::CapturePosition(
                    context->hwp, &current))) {
                return false;
            }
            paragraphIdentity = std::to_wstring(current.list) + L":" +
                std::to_wstring(current.paragraph);
        }
        const hancom::graph::properties::EffectivePropertyContext
            propertyContext{
                hancom::graph::capture::PropertyTarget::Paragraph,
                paragraphIdentity,
            };
        output = ProbeEffectiveProperties(
            context->hwp, &payload, &propertyContext);
        if (context->readerPayloads.empty()) {
            return false;
        }
        std::vector<hancom::graph::properties::ReferenceSite> sectionSites;
        for (const auto& section : context->readerPayloads.front().sections) {
            sectionSites.push_back({
                hancom::graph::capture::PropertyTarget::Section,
                std::to_wstring(section.ordinal),
                section.start,
                section.ordinal,
            });
        }
        hancom::graph::capture::ReaderPayload references;
        hancom::graph::properties::ReferenceClosureDiagnostics diagnostics;
        if (sectionSites.empty() ||
            hancom::graph::properties::CaptureCurrentReferenceClosure(
                context->hwp, sectionSites, &references, &diagnostics) !=
                hancom::graph::properties::CaptureStatus::Complete ||
            diagnostics.visitedSites != sectionSites.size()) {
            return false;
        }
        payload.definitions = std::move(references.definitions);
        payload.definitionReferences =
            std::move(references.definitionReferences);
        payload.referenceTraversal = references.referenceTraversal;
        break;
    }
    case hancom::graph::capture::QualifiedReader::ControlAdapters:
        if (!RunCoordinatorAction(context->hwp, L"MoveDocBegin")) {
            return false;
        }
        output = ProbeControlAdapters(context->hwp, &payload);
        break;
    case hancom::graph::capture::QualifiedReader::TableTopology: {
        if (context->readerPayloads.empty()) {
            return false;
        }
        const auto& story = context->readerPayloads.front();
        if (!CaptureTableTopologyReaderPayload(
                context->hwp, story.controls, &payload,
                &context->readerFailure)) {
            return false;
        }
        output = L"HCV1\tCAPABILITY\tTABLE_TOPOLOGY_READER\tV1\n"
                 L"NATIVE_TABLES\t" +
            std::to_wstring(payload.tables.size()) + L"\nRESULT\tPASS";
        break;
    }
    case hancom::graph::capture::QualifiedReader::ImagesShapesCaptions: {
        if (context->readerPayloads.empty()) {
            return false;
        }
        const auto& story = context->readerPayloads.front();
        const bool hasNativeImage = std::any_of(
            story.controls.begin(), story.controls.end(),
            [](const auto& control) {
                return control.ctrlId == L"$pic" || control.ctrlId == L"gso";
            });
        if (hasNativeImage) {
            if (!RunCoordinatorAction(context->hwp, L"MoveDocBegin")) {
                return false;
            }
            output = ProbeImageGraph(context->hwp, &payload);
            if (payload.outcome ==
                    hancom::graph::capture::ReaderOutcome::Complete &&
                !QualifyCaptionLocations(
                    context->hwp, story.controls, context->captureArena,
                    &payload)) {
                return false;
            }
        } else {
            payload.outcome =
                hancom::graph::capture::ReaderOutcome::Complete;
            payload.coverage = hancom::graph::CoverageState::Complete;
            output = L"HCV1\tCAPABILITY\tIMAGE_GRAPH\tV1\n"
                     L"NATIVE_IMAGE_CONTROLS\t0\nRESULT\tPASS\n";
        }
        break;
    }
    case hancom::graph::capture::QualifiedReader::StableLayout: {
        output = ProbeLayoutGraph(
            context->hwp, &payload, context->environmentPlatform,
            &context->readerPayloads);
        break;
    }
    default:
        return false;
    }
    ++context->readerCalls;
    const bool restored = hancom::com_state::RestoreSelection(
        context->hwp,
        context->baselineCursor,
        context->baselineSelection);
    if (!restored ||
        payload.outcome !=
            hancom::graph::capture::ReaderOutcome::Complete ||
        payload.coverage == hancom::graph::CoverageState::NotRequested) {
        return false;
    }
    context->readerOutputs.push_back(std::move(output));
    context->readerPayloads.push_back(std::move(payload));
    return true;
}

struct DetachedAttemptBuild final {
    std::vector<hancom::graph::capture::ReaderPayload> payloads{};
    std::vector<std::uint8_t> environment{};
    std::filesystem::path directory{};
    std::unique_ptr<hancom::graph::capture::CaptureIdentityArena> arena{};
    std::unique_ptr<hancom::graph::capture::CaptureSpool> spool{};
    std::wstring failure{};
    std::vector<std::pair<size_t, bool>> canonicalProgress{};
    std::uint64_t observedControlCount = 0;
    bool finalAttempt = false;
    bool prepareCandidate = false;
};

static_assert(!std::is_pointer_v<decltype(DetachedAttemptBuild::payloads)>);
static_assert(!std::is_pointer_v<decltype(DetachedAttemptBuild::arena)>);
static_assert(!std::is_pointer_v<decltype(DetachedAttemptBuild::spool)>);

void RecordDetachedCanonicalPass(
    void* const raw, const size_t pass, const bool complete) noexcept {
    auto* const state = static_cast<DetachedAttemptBuild*>(raw);
    if (state == nullptr) return;
    try { state->canonicalProgress.emplace_back(pass, complete); }
    catch (...) {}
}

bool BuildDetachedAttemptArtifact(
    const std::shared_ptr<void>& owner,
    hancom::graph::capture::AttemptResult* const output) noexcept {
    const auto state = std::static_pointer_cast<DetachedAttemptBuild>(owner);
    if (state == nullptr || output == nullptr || state->arena == nullptr ||
        state->spool == nullptr)
        return false;
    if (!state->spool->BuildAttempt(
            state->directory, state->payloads, state->environment,
            *state->arena, state->finalAttempt, &state->failure,
            RecordDetachedCanonicalPass, state.get(),
            state->prepareCandidate))
        return false;
    *output = state->spool->Result();
    output->observedControlCount = state->observedControlCount;
    return true;
}

bool DetachedAttemptFailureDetail(
    const std::shared_ptr<void>& owner,
    std::wstring* const output) noexcept {
    const auto state =
        std::static_pointer_cast<DetachedAttemptBuild>(owner);
    if (state == nullptr || output == nullptr) return false;
    try {
        *output = state->failure;
        return true;
    } catch (...) {
        return false;
    }
}

bool PrepareDetachedReplay(
    const std::shared_ptr<void>& firstOwner,
    const std::shared_ptr<void>& secondOwner) noexcept {
    const auto first =
        std::static_pointer_cast<DetachedAttemptBuild>(firstOwner);
    const auto second =
        std::static_pointer_cast<DetachedAttemptBuild>(secondOwner);
    if (first == nullptr || second == nullptr || first->arena == nullptr ||
        second->arena != nullptr)
        return false;
    second->arena = first->arena->CloneForLocalBuild();
    if (second->arena == nullptr) return false;
    second->arena->ReplayNextEncodingPass();
    return true;
}

bool RetainDetachedAttemptArtifact(
    void* const raw, const size_t attempt,
    const std::shared_ptr<void>& owner) noexcept {
    auto* const context = static_cast<CaptureQualificationContext*>(raw);
    const auto state =
        std::static_pointer_cast<DetachedAttemptBuild>(owner);
    if (context == nullptr || state == nullptr || attempt >= 2 ||
        state->spool == nullptr)
        return false;
    for (const auto& point : state->canonicalProgress)
        RecordCanonicalPass(context, point.first, point.second);
    const auto result = state->spool->Result();
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::CanonicalizeEnd,
        attempt, 0);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::GraphAssemblyEnd,
        result.nodeFingerprints.size(), result.records.length);
    context->spools[attempt] = std::move(state->spool);
    if (attempt == 1)
        context->readerPayloads = std::move(state->payloads);
    return true;
}

bool FinishCoordinatorAttempt(
    void* const raw,
    const size_t attempt,
    hancom::graph::capture::AttemptArtifactInput* const output) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr || output == nullptr ||
        context->readerOutputs.size() !=
            hancom::graph::capture::kQualifiedReaderCount ||
        context->readerPayloads.size() !=
            hancom::graph::capture::kQualifiedReaderCount) {
        return false;
    }
    auto& story = context->readerPayloads[static_cast<size_t>(
        hancom::graph::capture::QualifiedReader::StorySpine)];
    auto& text = context->readerPayloads[static_cast<size_t>(
        hancom::graph::capture::QualifiedReader::TextAtoms)];
    auto& tables = context->readerPayloads[static_cast<size_t>(
        hancom::graph::capture::QualifiedReader::TableTopology)];
    auto& images = context->readerPayloads[static_cast<size_t>(
        hancom::graph::capture::QualifiedReader::ImagesShapesCaptions)];
    if (story.sections.empty() || text.paragraphs.empty()) {
        context->readerFailure = L"finish:empty-story-or-text";
        return false;
    }
    const auto beforeOrEqual = [](
        const hancom::graph::capture::NativePosition& left,
        const hancom::graph::capture::NativePosition& right) {
        return left.list < right.list ||
            (left.list == right.list &&
             (left.paragraph < right.paragraph ||
              (left.paragraph == right.paragraph &&
               left.character <= right.character)));
    };
    for (auto& paragraph : text.paragraphs) {
        paragraph.sectionOrdinal = story.sections.front().ordinal;
        for (const auto& section : story.sections) {
            if (beforeOrEqual(section.start, paragraph.start)) {
                paragraph.sectionOrdinal = section.ordinal;
            }
        }
    }
    if (!ReconcileControlLocators(
            story.controls, &tables.tables, &images.images)) {
        context->readerFailure = L"finish:reconcile-control-locators";
        return false;
    }
    auto& properties = context->readerPayloads[static_cast<size_t>(
        hancom::graph::capture::QualifiedReader::EffectiveProperties)];
    std::vector<hancom::graph::properties::ReferenceSite> referenceSites;
    const auto addSite = [&referenceSites](
        const hancom::graph::capture::PropertyTarget target,
        std::wstring identity,
        const hancom::graph::capture::NativePosition& position,
        const std::uint64_t sectionOrdinal = 0,
        std::wstring ownerIdentity = {},
        std::wstring tableOwnerIdentity = {}) {
        referenceSites.push_back(
            {target, std::move(identity), position, sectionOrdinal,
             std::move(ownerIdentity), std::move(tableOwnerIdentity)});
    };
    const auto nativeControlLocator = [](
        const hancom::graph::capture::PropertyTarget target,
        const std::wstring& ctrlId,
        const std::uint64_t ordinal,
        const hancom::graph::capture::NativePosition& anchor,
        const bool instanceIdPresent,
        const std::wstring& rawInstanceId) {
        return hancom::graph::capture::CanonicalNativeSiteIdentity(
            target, ctrlId, ordinal, anchor, instanceIdPresent,
            rawInstanceId);
    };
    for (const auto& section : story.sections) {
        addSite(
            hancom::graph::capture::PropertyTarget::Section,
            std::to_wstring(section.ordinal), section.start,
            section.ordinal);
    }
    const auto addParagraphSites = [&addSite](const auto& paragraph) {
        const std::wstring paragraphIdentity =
            std::to_wstring(paragraph.start.list) + L":" +
            std::to_wstring(paragraph.start.paragraph);
        addSite(
            hancom::graph::capture::PropertyTarget::Paragraph,
            paragraphIdentity, paragraph.start);
        for (const auto& run : paragraph.runs) {
            addSite(
                hancom::graph::capture::PropertyTarget::Run,
                std::to_wstring(run.start.list) + L":" +
                    std::to_wstring(run.start.paragraph) + L":" +
                    std::to_wstring(run.start.character) + L":" +
                    std::to_wstring(run.end.character),
                run.start);
        }
    };
    for (const auto& paragraph : text.paragraphs) {
        addParagraphSites(paragraph);
    }
    for (const auto& control : story.controls) {
        const bool tableControl = std::any_of(
            tables.tables.begin(), tables.tables.end(),
            [&control](const auto& table) {
                return table.headCtrlOrdinal == control.headCtrlOrdinal;
            });
        const bool imageControl = std::any_of(
            images.images.begin(), images.images.end(),
            [&control](const auto& image) {
                return image.headCtrlOrdinal == control.headCtrlOrdinal;
            });
        if (!tableControl && !imageControl) {
            addSite(
                hancom::graph::capture::PropertyTarget::Control,
                nativeControlLocator(
                    hancom::graph::capture::PropertyTarget::Control,
                    control.ctrlId, control.headCtrlOrdinal,
                    control.anchor, control.instanceIdPresent,
                    control.instanceId),
                control.anchor);
        }
    }
    for (const auto& table : tables.tables) {
        const std::wstring tableOwnerIdentity = nativeControlLocator(
            hancom::graph::capture::PropertyTarget::Table,
            L"tbl", table.headCtrlOrdinal, table.anchor,
            table.instanceIdPresent, table.instanceId);
        addSite(
            hancom::graph::capture::PropertyTarget::Table,
            tableOwnerIdentity,
            table.anchor);
        for (const auto& cell : table.cells) {
            addSite(
                hancom::graph::capture::PropertyTarget::Cell,
                nativeControlLocator(
                    hancom::graph::capture::PropertyTarget::Cell,
                    L"tbl", table.headCtrlOrdinal,
                    {cell.listId, 0, 0}, table.instanceIdPresent,
                    table.instanceId) + L":" + cell.address,
                {cell.listId, 0, 0}, 0, {}, tableOwnerIdentity);
            for (const auto& paragraph : cell.paragraphs) {
                addParagraphSites(paragraph);
            }
        }
        if (table.captionPresent.state == graph::ObservationState::Value &&
            table.captionPresent.value != 0 &&
            table.captionText.state == graph::ObservationState::Value) {
            size_t begin = 0;
            std::int64_t paragraph = table.captionStart.paragraph;
            for (;;) {
                size_t end = begin;
                while (end < table.captionText.value.size() &&
                       table.captionText.value[end] != L'\r' &&
                       table.captionText.value[end] != L'\n') ++end;
                if (end < table.captionText.value.size()) {
                    ++end;
                    if (table.captionText.value[end - 1] == L'\r' &&
                        end < table.captionText.value.size() &&
                        table.captionText.value[end] == L'\n') ++end;
                }
                const std::int64_t character =
                    paragraph == table.captionStart.paragraph
                        ? table.captionStart.character : 0;
                const graph::capture::NativePosition start{
                    table.captionStart.list, paragraph, character};
                addSite(
                    graph::capture::PropertyTarget::Paragraph,
                    std::to_wstring(start.list) + L":" +
                        std::to_wstring(start.paragraph),
                    start);
                addSite(
                    graph::capture::PropertyTarget::Run,
                    std::to_wstring(start.list) + L":" +
                        std::to_wstring(start.paragraph) + L":" +
                        std::to_wstring(start.character) + L":" +
                        std::to_wstring(
                            start.character +
                            static_cast<std::int64_t>(end - begin)),
                    start);
                if (end == table.captionText.value.size()) break;
                begin = end;
                ++paragraph;
            }
        }
    }
    for (const auto& image : images.images) {
        addSite(
            hancom::graph::capture::PropertyTarget::Image,
            nativeControlLocator(
                hancom::graph::capture::PropertyTarget::Image,
                image.ctrlId, image.headCtrlOrdinal, image.anchor,
                image.instanceIdPresent, image.instanceId),
            image.anchor);
        // Caption paragraph/run reference families are intentionally terminal
        // at this boundary and are owned by AccountCaptionReferenceTerminals.
        // Adding them to the positioned reference traversal as well creates
        // contradictory Complete/NotExposed evidence for one coordinate.
    }
    hancom::graph::properties::ReferenceClosureDiagnostics
        referenceDiagnostics;
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::ReferenceClosureStart,
        attempt, referenceSites.size());
    const auto referenceStatus =
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            context->hwp,
            referenceSites,
            &properties,
            &referenceDiagnostics);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::ReferenceClosureEnd,
        referenceDiagnostics.visitedSites,
        referenceDiagnostics.expectedSites);
    for (size_t target = 0;
         target < referenceDiagnostics.sitesByTarget.size(); ++target) {
        RecordCaptureProgress(
            context,
            hancom::graph::capture::CaptureProgressPoint::ReferenceSitesByTarget,
            target, referenceDiagnostics.sitesByTarget[target]);
    }
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::CellReferencePath,
        referenceDiagnostics.cellSites,
        referenceDiagnostics.cellFastMode0Attempts);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::CellReferenceProofReads,
        referenceDiagnostics.cellAddressReads,
        referenceDiagnostics.cellModeReads);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::CellReferenceFastReads,
        referenceDiagnostics.cellFastMode0Hits,
        referenceDiagnostics.cellFastMode0Rejections);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::
            CellReferenceFallbackCalls,
        referenceDiagnostics.cellDiscardedProvisionalSets,
        referenceDiagnostics.cellQualifiedFallbackCalls);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::
            CellReferenceFallbackReads,
        referenceDiagnostics.cellTableCellBlockCalls,
        referenceDiagnostics.cellFallbackGetDefaultCalls);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::
            CellReferencePositionWall,
        referenceDiagnostics.cellPositionWallNanoseconds,
        referenceDiagnostics.cellFastProofWallNanoseconds);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::CellReferenceFastWall,
        referenceDiagnostics.cellFastReadWallNanoseconds,
        referenceDiagnostics.cellFastGetDefaultCalls);
    RecordCaptureProgress(
        context,
        hancom::graph::capture::CaptureProgressPoint::
            CellReferenceFallbackWall,
        referenceDiagnostics.cellFallbackWallNanoseconds,
        referenceDiagnostics.cellFallbackAddressReads +
            referenceDiagnostics.cellFallbackModeReads);
    if (referenceStatus !=
            hancom::graph::properties::CaptureStatus::Complete ||
        referenceDiagnostics.visitedSites !=
            referenceDiagnostics.expectedSites) {
        context->readerFailure = L"finish:reference-closure:" +
            std::to_wstring(referenceDiagnostics.visitedSites) + L":" +
            std::to_wstring(referenceDiagnostics.expectedSites);
        return false;
    }
    context->captionQualificationExact = false;
    context->captionQualificationCrossOwnerRejected = false;
    context->captionQualificationStaleRejected = false;
    context->captionQualificationUnsetRejected = false;
    try {
        std::vector<size_t> qualified;
        for (size_t index = 0; index < images.images.size(); ++index) {
            if (images.images[index].captionList.state ==
                    hancom::graph::ObservationState::Value) {
                qualified.push_back(index);
            }
        }
        hancom::graph::capture::ReaderPayload exactScratch;
        hancom::graph::properties::ReferenceClosureDiagnostics
            exactDiagnostics;
        context->captionQualificationExact = !qualified.empty() &&
            hancom::graph::capture::CaptionLocationsReadyForGraphPublication(
                context->captureArena, images.images) &&
            hancom::graph::properties::AccountCaptionReferenceTerminals(
                context->captureArena, images.images, &exactScratch,
                &exactDiagnostics) &&
            exactScratch.coverageFacts.size() == qualified.size() * 3;
        if (!qualified.empty()) {
            const auto effectiveRejects = [context](const auto& candidates) {
                hancom::graph::capture::ReaderPayload scratch;
                hancom::graph::properties::ReferenceClosureDiagnostics
                    diagnostics;
                return !hancom::graph::properties::
                    AccountCaptionReferenceTerminals(
                        context->captureArena, candidates, &scratch,
                        &diagnostics) && scratch.coverageFacts.empty();
            };
            auto unset = images.images;
            unset[qualified.front()].captionLocationQualification = {};
            context->captionQualificationUnsetRejected =
                !hancom::graph::capture::
                    CaptionLocationsReadyForGraphPublication(
                        context->captureArena, unset) &&
                effectiveRejects(unset);

            hancom::graph::capture::CaptureIdentityArena staleCapture;
            hancom::graph::capture::ReaderPayload staleScratch;
            hancom::graph::properties::ReferenceClosureDiagnostics
                staleDiagnostics;
            context->captionQualificationStaleRejected =
                !hancom::graph::capture::
                    CaptionLocationsReadyForGraphPublication(
                        staleCapture, images.images) &&
                !hancom::graph::properties::
                    AccountCaptionReferenceTerminals(
                        staleCapture, images.images, &staleScratch,
                        &staleDiagnostics) &&
                staleScratch.coverageFacts.empty();
        }
        if (qualified.size() >= 2) {
            auto crossed = images.images;
            crossed[qualified[1]].captionLocationQualification =
                crossed[qualified[0]].captionLocationQualification;
            hancom::graph::capture::ReaderPayload crossedScratch;
            hancom::graph::properties::ReferenceClosureDiagnostics
                crossedDiagnostics;
            context->captionQualificationCrossOwnerRejected =
                !hancom::graph::capture::
                    CaptionLocationsReadyForGraphPublication(
                        context->captureArena, crossed) &&
                !hancom::graph::properties::
                    AccountCaptionReferenceTerminals(
                        context->captureArena, crossed, &crossedScratch,
                        &crossedDiagnostics) &&
                crossedScratch.coverageFacts.empty();
        }
    } catch (...) {
        context->readerFailure = L"finish:caption-diagnostics-exception";
        return false;
    }
    if (!hancom::graph::properties::AccountCaptionReferenceTerminals(
            context->captureArena, images.images, &properties,
            &referenceDiagnostics)) {
        context->readerFailure = L"finish:caption-reference-terminals";
        return false;
    }
    hancom::graph::Sha256 pageSetupDigest{};
    if (!hancom::graph::properties::DerivePageSetupDigest(
            properties, &pageSetupDigest)) {
        context->readerFailure = L"finish:page-setup-digest";
        return false;
    }
    auto& layoutForEnvironment = context->readerPayloads[static_cast<size_t>(
        hancom::graph::capture::QualifiedReader::StableLayout)];
    if (!layoutForEnvironment.layoutEnvironment.empty()) {
        std::vector<std::uint8_t> completedEnvironment;
        if (!hancom::graph::layout::ReplacePageSetupDigestV1(
                layoutForEnvironment.layoutEnvironment,
                pageSetupDigest,
                &completedEnvironment)) {
            context->readerFailure = L"finish:layout-environment";
            return false;
        }
        layoutForEnvironment.layoutEnvironment =
            std::move(completedEnvironment);
    }
    const std::vector<hancom::graph::capture::ReaderPayload>& payloads =
        context->readerPayloads;
    const auto firstParagraphIdentity = [&text]() -> std::wstring {
        if (text.paragraphs.empty()) return {};
        return std::to_wstring(text.paragraphs.front().start.list) + L":" +
            std::to_wstring(text.paragraphs.front().start.paragraph);
    };
    const auto selectionAt = [&context](
        const hancom::graph::capture::NativePosition& position) {
        const auto same = [&position](const hancom::com_state::Position& selected) {
            return position.list == selected.list &&
                position.paragraph == selected.paragraph &&
                position.character == selected.character;
        };
        return same(context->baselineSelection.start) ||
            same(context->baselineSelection.end);
    };
    const auto selectedControl = [
        &context, &story, &nativeControlLocator, &selectionAt]() {
        if (!context->baselineSelection.controlInstancePresent) return std::wstring{};
        std::vector<const hancom::graph::capture::ControlObservation*> raw;
        std::vector<const hancom::graph::capture::ControlObservation*> exact;
        for (const auto& value : story.controls) {
            if (value.ctrlId != context->baselineSelection.controlType ||
                value.instanceIdPresent !=
                    context->baselineSelection.controlInstancePresent ||
                value.instanceId != context->baselineSelection.controlInstance) {
                continue;
            }
            raw.push_back(&value);
            if (selectionAt(value.anchor)) exact.push_back(&value);
        }
        const auto* found = exact.size() == 1
            ? exact.front()
            : raw.size() == 1 &&
                    !context->baselineSelection.controlInstance.empty()
                ? raw.front() : nullptr;
        if (found == nullptr) return std::wstring{};
        return nativeControlLocator(
            hancom::graph::capture::PropertyTarget::Control,
            found->ctrlId, found->headCtrlOrdinal, found->anchor,
            found->instanceIdPresent, found->instanceId);
    };
    const auto selectedTable = [
        &context, &tables, &nativeControlLocator, &selectionAt]() {
        if (!context->baselineSelection.controlInstancePresent ||
            context->baselineSelection.controlType != L"tbl") return std::wstring{};
        std::vector<const hancom::graph::capture::TableObservation*> raw;
        std::vector<const hancom::graph::capture::TableObservation*> exact;
        for (const auto& value : tables.tables) {
            if (value.instanceIdPresent !=
                    context->baselineSelection.controlInstancePresent ||
                value.instanceId != context->baselineSelection.controlInstance) {
                continue;
            }
            raw.push_back(&value);
            if (selectionAt(value.anchor)) exact.push_back(&value);
        }
        const auto* found = exact.size() == 1
            ? exact.front()
            : raw.size() == 1 &&
                    !context->baselineSelection.controlInstance.empty()
                ? raw.front() : nullptr;
        if (found == nullptr) return std::wstring{};
        return nativeControlLocator(
            hancom::graph::capture::PropertyTarget::Table, L"tbl",
            found->headCtrlOrdinal, found->anchor,
            found->instanceIdPresent, found->instanceId);
    };
    const auto selectedCell = [
        &context, &tables, &nativeControlLocator]() {
        if (!context->baselineSelection.controlInstancePresent ||
            context->baselineSelection.controlType != L"tbl" ||
            context->baselineSelection.cellAddresses.size() != 1) {
            return std::wstring{};
        }
        const std::wstring& address =
            context->baselineSelection.cellAddresses.front();
        const hancom::graph::capture::TableObservation* selectedTable = nullptr;
        const hancom::graph::capture::CellObservation* selectedCell = nullptr;
        size_t matches = 0;
        for (const auto& table : tables.tables) {
            if (table.instanceIdPresent !=
                    context->baselineSelection.controlInstancePresent ||
                table.instanceId != context->baselineSelection.controlInstance) {
                continue;
            }
            for (const auto& cell : table.cells) {
                const bool selectedList = cell.listId ==
                        context->baselineSelection.start.list ||
                    cell.listId == context->baselineSelection.end.list;
                if (cell.address == address && selectedList) {
                    ++matches;
                    selectedTable = &table;
                    selectedCell = &cell;
                }
            }
        }
        if (matches != 1 || selectedTable == nullptr || selectedCell == nullptr) {
            return std::wstring{};
        }
        return nativeControlLocator(
            hancom::graph::capture::PropertyTarget::Cell, L"tbl",
            selectedTable->headCtrlOrdinal, {selectedCell->listId, 0, 0},
            selectedTable->instanceIdPresent, selectedTable->instanceId) +
            L":" + address;
    };
    const auto selectedImage = [
        &context, &images, &nativeControlLocator, &selectionAt]() {
        if (!context->baselineSelection.controlInstancePresent) return std::wstring{};
        std::vector<const hancom::graph::capture::ImageObservation*> raw;
        std::vector<const hancom::graph::capture::ImageObservation*> exact;
        for (const auto& value : images.images) {
            if (value.ctrlId != context->baselineSelection.controlType ||
                value.instanceIdPresent !=
                    context->baselineSelection.controlInstancePresent ||
                value.instanceId != context->baselineSelection.controlInstance) {
                continue;
            }
            raw.push_back(&value);
            if (selectionAt(value.anchor)) exact.push_back(&value);
        }
        const auto* found = exact.size() == 1
            ? exact.front()
            : raw.size() == 1 &&
                    !context->baselineSelection.controlInstance.empty()
                ? raw.front() : nullptr;
        if (found == nullptr) return std::wstring{};
        return nativeControlLocator(
            hancom::graph::capture::PropertyTarget::Image, found->ctrlId,
            found->headCtrlOrdinal, found->anchor,
            found->instanceIdPresent, found->instanceId);
    };
    for (auto property = properties.properties.begin();
         property != properties.properties.end();) {
        const bool exactProducerTarget = !property->targetIdentity.empty();
        switch (property->target) {
        case hancom::graph::capture::PropertyTarget::Run:
            // Run properties are captured independently at each exact native
            // run start by the reference-closure reader. Their identity must
            // never be replaced by the first or previously visited run.
            break;
        case hancom::graph::capture::PropertyTarget::Paragraph:
            if (!exactProducerTarget)
                property->targetIdentity = firstParagraphIdentity();
            break;
        case hancom::graph::capture::PropertyTarget::Control:
            if (!exactProducerTarget)
                property->targetIdentity = selectedControl();
            break;
        case hancom::graph::capture::PropertyTarget::Table:
            if (!exactProducerTarget)
                property->targetIdentity = selectedTable();
            break;
        case hancom::graph::capture::PropertyTarget::Cell:
            if (!exactProducerTarget)
                property->targetIdentity = selectedCell();
            break;
        case hancom::graph::capture::PropertyTarget::Image:
            if (!exactProducerTarget)
                property->targetIdentity = selectedImage();
            break;
        default:
            break;
        }
        const bool needsIdentity = property->target !=
            hancom::graph::capture::PropertyTarget::Document;
        if (!needsIdentity || !property->targetIdentity.empty()) {
            ++property;
            continue;
        }
        if (property->state == hancom::graph::ObservationState::Value) {
            context->readerFailure = L"finish:unresolved-value-property:" +
                std::to_wstring(property->key);
            return false;
        }
        hancom::graph::capture::CoverageObservation coverage;
        coverage.target =
            hancom::graph::capture::PropertyTarget::Document;
        coverage.coordinate =
            hancom::graph::CoverageCoordinateKind::Profile;
        coverage.ownerField = 0;
        coverage.propertyKeyPresent = false;
        coverage.detail = L"UnresolvedPropertySite:" +
            std::to_wstring(property->key);
        const hancom::graph::PropertyRule* const rule =
            hancom::graph::FindPropertyRule(property->key);
        if (rule == nullptr) {
            context->readerFailure = L"finish:unregistered-property:" +
                std::to_wstring(property->key);
            return false;
        }
        for (const hancom::graph::ProfileRule& profile :
             hancom::graph::kProfileRules) {
            if ((rule->profileBits &
                 hancom::graph::ProfileBit(profile.profile)) != 0) {
                coverage.profile = profile.profile;
                break;
            }
        }
        coverage.state = property->state ==
                hancom::graph::ObservationState::ReadFailed
            ? hancom::graph::CoverageState::ReadFailed
            : property->state ==
                    hancom::graph::ObservationState::NotApplicable
                ? hancom::graph::CoverageState::NotApplicable
                : hancom::graph::CoverageState::NotExposed;
        properties.coverageFacts.push_back(std::move(coverage));
        property = properties.properties.erase(property);
    }

    const auto& layout = context->readerPayloads[static_cast<size_t>(
        hancom::graph::capture::QualifiedReader::StableLayout)];
    try {
        auto state = std::make_shared<DetachedAttemptBuild>();
        state->environment = layout.layoutEnvironment;
        state->observedControlCount = story.controls.size();
        state->finalAttempt = attempt == 1;
        state->prepareCandidate = attempt == 1 &&
            !context->preparedStoreRoot.empty();
        state->directory = state->prepareCandidate
            ? context->preparedStoreRoot /
                (L"prepared-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
                 std::to_wstring(context->captureSessionSerial))
            : context->spoolRoot /
                (L"attempt-" + std::to_wstring(attempt));
        state->spool =
            std::make_unique<hancom::graph::capture::CaptureSpool>();
        if (attempt == 0) {
            state->arena = context->captureArena.CloneForLocalBuild();
            if (state->arena == nullptr) return false;
        }
        // This move is the ownership boundary: after it, every worker input
        // is value-owned by `state`; no COM object, session pointer, positioned
        // observation view, or attempt-one reference is reachable.
        state->payloads = std::move(context->readerPayloads);
        output->owner = std::move(state);
        output->build = BuildDetachedAttemptArtifact;
        output->prepareReplay = PrepareDetachedReplay;
        output->failureDetail = DetachedAttemptFailureDetail;
        output->comInterfacePointers = 0;
        output->mutableContextReferences = 0;
        RecordCaptureProgress(
            context,
            hancom::graph::capture::CaptureProgressPoint::GraphAssemblyStart,
            attempt, payloads.size());
        RecordCaptureProgress(
            context,
            hancom::graph::capture::CaptureProgressPoint::CanonicalizeStart,
            attempt, 0);
        return true;
    } catch (...) {
        context->readerFailure = L"finish:detach-attempt-artifact";
        return false;
    }
}

bool ReleaseCoordinatorScans(void*) noexcept {
    return true;
}

bool RestoreCoordinatorState(
    void* const raw,
    const hancom::graph::store::CapturedState&) noexcept {
    auto* const context =
        static_cast<CaptureQualificationContext*>(raw);
    if (context == nullptr || context->hwp == nullptr ||
        !context->baselineCaptured) {
        return false;
    }
    return hancom::com_state::RestoreSelection(
        context->hwp,
        context->baselineCursor,
        context->baselineSelection);
}

bool CoordinatorCancelled(void* const raw) noexcept {
    const auto* const context =
        static_cast<const CaptureQualificationContext*>(raw);
    return context != nullptr && context->cancellationObserved;
}

std::wstring DigestHex(const hancom::graph::Sha256& digest) {
    std::wostringstream output;
    output << std::hex << std::setfill(L'0');
    for (const std::uint8_t value : digest.bytes) {
        output << std::setw(2) << static_cast<unsigned>(value);
    }
    return output.str();
}

bool EffectivePropertiesExcludeGeneratedRules(
    const CaptureQualificationContext& context) noexcept {
    if (context.readerPayloads.size() !=
        hancom::graph::capture::kQualifiedReaderCount) {
        return false;
    }
    const auto& effective = context.readerPayloads[static_cast<size_t>(
        hancom::graph::capture::QualifiedReader::EffectiveProperties)];
    return std::all_of(
        effective.properties.begin(), effective.properties.end(),
        [](const auto& property) {
            const auto* const rule =
                hancom::graph::FindPropertyRule(property.key);
            return rule != nullptr &&
                rule->origin != hancom::graph::RegistryOrigin::Generated;
        });
}

bool RequiredLayoutFamiliesComplete(
    const CaptureQualificationContext& context) noexcept {
    try {
        if (context.readerPayloads.size() !=
            hancom::graph::capture::kQualifiedReaderCount) {
            return false;
        }
        const auto& layout = context.readerPayloads[static_cast<size_t>(
            hancom::graph::capture::QualifiedReader::StableLayout)];
        if (layout.layoutPerKindComplete &&
            layout.coverage == hancom::graph::CoverageState::Complete &&
            std::all_of(
                layout.layoutObservedKindCounts.begin(),
                layout.layoutObservedKindCounts.end(),
                [](const std::uint64_t count) { return count != 0; })) {
            return true;
        }
        std::array<bool, 5> observedFamilies{};
        std::map<std::pair<hancom::graph::capture::PropertyTarget,
                           std::wstring>, std::set<hancom::graph::PropertyKeyId>>
            observed;
        const auto family = [](const auto target, size_t* const index,
                               hancom::graph::NodeKind* const kind,
                               bool* const geometry) {
            using hancom::graph::capture::PropertyTarget;
            switch (target) {
            case PropertyTarget::Paragraph:
                *index = 0; *kind = hancom::graph::NodeKind::Paragraph;
                *geometry = false; return true;
            case PropertyTarget::Control:
                *index = 1; *kind = hancom::graph::NodeKind::GenericControl;
                *geometry = true; return true;
            case PropertyTarget::Table:
                *index = 2; *kind = hancom::graph::NodeKind::Table;
                *geometry = true; return true;
            case PropertyTarget::Cell:
                *index = 3; *kind = hancom::graph::NodeKind::TableCell;
                *geometry = false; return true;
            case PropertyTarget::Image:
                *index = 4; *kind = hancom::graph::NodeKind::Image;
                *geometry = true; return true;
            default:
                return false;
            }
        };
        for (const auto& property : layout.layoutProperties) {
            size_t familyIndex = 0;
            hancom::graph::NodeKind nodeKind =
                hancom::graph::NodeKind::Document;
            bool requiresGeometry = false;
            if (!family(property.target, &familyIndex, &nodeKind,
                        &requiresGeometry) ||
                property.targetIdentity.empty() || property.ownerField != 10 ||
                property.origin != hancom::graph::PropertyOrigin::Generated ||
                property.state != hancom::graph::ObservationState::Value ||
                property.key < 12000 || property.key > 12003 ||
                (!requiresGeometry && property.key >= 12002)) {
                return false;
            }
            const auto* const rule =
                hancom::graph::FindPropertyRule(property.key);
            const auto expectedScalar = property.key <= 12001
                ? hancom::graph::ScalarTag::Uint64
                : hancom::graph::ScalarTag::HWPUNIT64;
            if (rule == nullptr || property.scalar != expectedScalar ||
                !hancom::graph::IsPropertyApplicable(
                    *rule, nodeKind, property.ownerField) ||
                !observed[{property.target, property.targetIdentity}]
                     .insert(property.key).second) {
                return false;
            }
            observedFamilies[familyIndex] = true;
        }
        for (const auto& entry : observed) {
            size_t familyIndex = 0;
            hancom::graph::NodeKind nodeKind =
                hancom::graph::NodeKind::Document;
            bool requiresGeometry = false;
            if (!family(entry.first.first, &familyIndex, &nodeKind,
                        &requiresGeometry)) {
                return false;
            }
            const std::set<hancom::graph::PropertyKeyId> expected =
                requiresGeometry
                ? std::set<hancom::graph::PropertyKeyId>{12000, 12001,
                                                         12002, 12003}
                : std::set<hancom::graph::PropertyKeyId>{12000, 12001};
            if (entry.second != expected) {
                return false;
            }
        }
        return std::all_of(
            observedFamilies.begin(), observedFamilies.end(),
            [](const bool value) { return value; });
    } catch (...) {
        return false;
    }
}

std::wstring ProbeCaptureCoordinator(
    IDispatch* const hwp,
    const hancom::graph::layout::LayoutEnvironmentPlatformV1* const
        environmentPlatform) {
    CaptureQualificationContext context;
    context.hwp = hwp;
    context.environmentPlatform = environmentPlatform;
    if (!PrepareCaptureSpoolRoot(L"hwp-live-capture", &context.spoolRoot)) {
        return L"HCV1\tCAPABILITY\tCAPTURE_COORDINATOR\tV1\n"
            L"RESULT\tFAIL\tTEMP_DIRECTORY";
    }
    const hancom::graph::capture::ReaderSuite suite{
        hancom::graph::capture::kQualifiedReaderCount,
        CaptureCoordinatorState,
        BeginCoordinatorAttempt,
        RunQualifiedReader,
        nullptr,
        ReleaseCoordinatorScans,
        RestoreCoordinatorState,
        CoordinatorCancelled,
        BeginCoordinatorSession,
        PrepareCoordinatorPublication,
        AbortCoordinatorSession,
        CommitCoordinatorSession,
        nullptr,
        FinishCoordinatorAttempt,
        RetainDetachedAttemptArtifact,
    };
    hancom::graph::capture::CaptureCoordinator coordinator;
    hancom::graph::capture::CaptureStatus status =
        hancom::graph::capture::CaptureStatus::PublishFailed;
    std::uint64_t activeSerial = 0;
    std::wstring recordsHash;
    std::wstring indexHash;
    std::wstring manifestHash;
    {
        hancom::graph::store::GraphStore store(
            (context.spoolRoot / L"store").wstring());
        if (store.Initialize()) {
            status = coordinator.CaptureToStore(
                suite,
                &context,
                &store,
                {});
            activeSerial = store.ActiveSerial();
            const auto generation = store.PinActive();
            if (generation != nullptr) {
                const std::filesystem::path path(generation->Path());
                recordsHash = Sha256File(
                    (path / L"records.hgn").wstring());
                indexHash = Sha256File(
                    (path / L"index.hgi").wstring());
                manifestHash = Sha256File(
                    (path / L"manifest.hgm").wstring());
            }
        }
    }
    const hancom::graph::capture::AttemptResult captured =
        context.committedResult;
    bool reopenedGeneration = false;
    bool sealedManifest580 = false;
    bool readOnlyGeneration = false;
    {
        hancom::graph::store::GraphStore reopened(
            (context.spoolRoot / L"store").wstring());
        if (reopened.Initialize() && reopened.ActiveSerial() == 1) {
            const auto generation = reopened.PinActive();
            if (generation != nullptr) {
                const std::filesystem::path path(generation->Path());
                std::error_code sizeError;
                sealedManifest580 = std::filesystem::file_size(
                    path / L"manifest.hgm", sizeError) == 580 &&
                    !sizeError;
                readOnlyGeneration = true;
                for (const wchar_t* const name : {
                         L"records.hgn", L"index.hgi", L"manifest.hgm"}) {
                    const DWORD attributes = GetFileAttributesW(
                        (path / name).c_str());
                    readOnlyGeneration = readOnlyGeneration &&
                        attributes != INVALID_FILE_ATTRIBUTES &&
                        (attributes & FILE_ATTRIBUTE_READONLY) != 0;
                }
                reopenedGeneration = sealedManifest580 &&
                    readOnlyGeneration;
            }
        }
    }
    bool cleanupSucceeded = true;
    for (auto& spool : context.spools) {
        if (spool != nullptr) {
            cleanupSucceeded = spool->Reset() && cleanupSucceeded;
            spool.reset();
        }
    }
    std::error_code cleanupError;
    std::filesystem::remove_all(context.spoolRoot, cleanupError);
    cleanupSucceeded = !cleanupError && cleanupSucceeded;
    const bool effectiveProducerOwned =
        context.committedEffectiveProducerOwned;
    const bool layoutFamiliesComplete =
        context.committedLayoutFamiliesComplete;
    const bool typedLayoutCapturePath =
        context.committedTypedLayoutCapturePath;
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\tCAPTURE_COORDINATOR\tV1\n"
           << L"CAPTURE_STATUS\t" << static_cast<int>(status) << L'\n'
           << L"READER_CALLS\t" << context.readerCalls << L'\n'
           << L"READER_FAILURE\t" << context.readerFailure << L'\n'
           << L"ACTIVE_SERIAL\t" << activeSerial << L'\n'
           << L"SEMANTIC_ROOT\t"
           << DigestHex(captured.manifest.observedSemanticRoot) << L'\n'
           << L"LAYOUT_ROOT\t"
           << DigestHex(captured.manifest.layoutRoot) << L'\n'
           << L"CAPTURE_ROOT\t"
           << DigestHex(captured.manifest.captureRoot) << L'\n'
           << L"INTEGRITY\t"
           << static_cast<int>(captured.manifest.integrity) << L'\n';
    output << context.committedCaptionDiagnostics
           << L"CAPTION_QUALIFICATION_EXACT_OWNER_SESSION\t"
           << (context.captionQualificationExact ? 1 : 0) << L'\n'
           << L"CAPTION_QUALIFICATION_CROSS_OWNER_REJECTED\t"
           << (context.captionQualificationCrossOwnerRejected ? 1 : 0)
           << L'\n'
           << L"CAPTION_QUALIFICATION_STALE_SESSION_REJECTED\t"
           << (context.captionQualificationStaleRejected ? 1 : 0) << L'\n'
           << L"CAPTION_QUALIFICATION_UNSET_REJECTED\t"
           << (context.captionQualificationUnsetRejected ? 1 : 0) << L'\n'
           << L"CAPTURE_INTEGRITY\t"
           << (status ==
                       hancom::graph::capture::CaptureStatus::Complete &&
                   captured.manifest.integrity ==
                       hancom::graph::CaptureIntegrity::Complete
               ? L"Complete" : L"Incomplete") << L'\n'
           << L"GENERATION_RECORDS_SHA256\t" << recordsHash << L'\n'
           << L"GENERATION_INDEX_SHA256\t" << indexHash << L'\n'
           << L"GENERATION_MANIFEST_SHA256\t" << manifestHash << L'\n'
           << L"REOPENED_SERIAL_1\t"
           << (reopenedGeneration ? 1 : 0) << L'\n'
           << L"SEALED_MANIFEST_580\t"
           << (sealedManifest580 ? 1 : 0) << L'\n'
           << L"READ_ONLY_GENERATION\t"
           << (readOnlyGeneration ? 1 : 0) << L'\n'
           << L"EFFECTIVE_PROPERTY_PRODUCER_OWNERSHIP\t"
           << (effectiveProducerOwned ? L"PASS" : L"FAIL") << L'\n'
           << L"LAYOUT_PROPERTY_CONTRACT\t"
           << (layoutFamiliesComplete ? L"PASS" : L"FAIL") << L'\n'
           << L"LAYOUT_TYPED_CAPTURE_PATH\t"
           << (typedLayoutCapturePath ? L"V1" : L"NONE") << L'\n';
    if (layoutFamiliesComplete) {
        output << L"LAYOUT_REQUIRED_FAMILIES\tPARAGRAPH\tCONTROL\tTABLE\tCELL\tIMAGE\n";
    }
    output << L"CLEANUP\t" << (cleanupSucceeded ? L"PASS" : L"FAIL")
           << L'\n'
           << L"RESULT\t"
           << (cleanupSucceeded && reopenedGeneration &&
                effectiveProducerOwned && layoutFamiliesComplete &&
                typedLayoutCapturePath && status ==
                    hancom::graph::capture::CaptureStatus::Complete &&
                activeSerial == 1 && !recordsHash.empty() &&
                !indexHash.empty() && !manifestHash.empty()
                ? L"PASS"
                : L"FAIL\tINCOMPLETE_CAPTURE");
    return output.str();
}

std::wstring StateGuardedProbe(
    IDispatch* hwp,
    const std::wstring& scenario,
    const hancom::graph::layout::LayoutEnvironmentPlatformV1* const
        environmentPlatform) {
    const DocumentContentSignature before = CaptureDocumentContentSignature(hwp);
    const std::wstring beforeText = FormatDocumentContentSignature(before);
    hancom::com_state::Position cursor;
    hancom::com_state::Selection selection;
    hancom::com_state::SelectionCaptureFailure failure;
    static constexpr CallSpec modifiedSpec{
        L"IsModified",1,DISPATCH_PROPERTYGET,0,VT_BOOL};
    const RawCall modified = InvokeOneShot(hwp, modifiedSpec, {}, true);
    const HRESULT positionStatus=hancom::com_state::CapturePosition(hwp,&cursor);
    const bool selectionCaptured=hancom::com_state::CaptureSelection(
        hwp,&selection,hancom::com_state::SelectionCapturePolicy::RequiredControl,
        &failure);
    const bool selectionRestorable=selectionCaptured &&
        hancom::com_state::CanRestoreSelection(selection);
    const bool captured = !beforeText.empty() && SUCCEEDED(positionStatus) &&
        selectionCaptured && selectionRestorable &&
        (modified.semantic == SemanticStatus::Pass ||
         modified.semantic == SemanticStatus::FalseResult);
    if (!captured) {
        std::wostringstream diagnostic;
        diagnostic << L"HCV1\tCAPABILITY\t" << scenario
            << L"\tV1\nSTATE_CAPTURE\tSIGNATURE=" << (!beforeText.empty())
            << L"\tPOSITION=" << static_cast<LONG>(positionStatus)
            << L"\tSELECTION=" << selectionCaptured
            << L"\tRESTORABLE=" << selectionRestorable
            << L"\tSELECTION_STAGE=" << static_cast<int>(failure.stage)
            << L"\tSELECTION_HRESULT=" << static_cast<LONG>(failure.status)
            << L"\tMODIFIED=" << SemanticName(modified.semantic)
            << L"\nRESULT\tFAIL\tSTATE_CAPTURE_OR_SIGNATURE";
        return diagnostic.str();
    }
    if (scenario == L"TABLE_GRAPH") {
        hancom::inspection::ResetTableRangeProbeTuples();
    }
    const std::wstring body = scenario == L"CONTAINMENT"
        ? ProbeContainment(hwp)
        : scenario == L"STORY_SPINE"
        ? ProbeStorySpine(hwp)
        : scenario == L"TEXT_CURRENT"
        ? ProbeText(hwp, false)
        : scenario == L"TEXT_BODY"
        ? ProbeText(hwp, true)
        : scenario == L"EFFECTIVE_PROPERTIES"
        ? ProbeEffectiveProperties(hwp)
        : scenario == L"CONTROL_ADAPTERS"
        ? ProbeControlAdapters(hwp)
        : scenario == L"CAPTURE_COORDINATOR"
        ? ProbeCaptureCoordinator(hwp, environmentPlatform)
        : scenario == L"IMAGE_GRAPH"
        ? ProbeImageGraph(hwp)
        : scenario == L"LAYOUT_GRAPH"
        ? ProbeLayoutGraph(hwp, nullptr, environmentPlatform)
        : scenario == L"TABLE_GRAPH"
        ? ProbeTableGraph(hwp)
        : scenario == L"TABLE_RANGE_BASES"
        ? ProbeTableRangeBases(hwp)
        : ProbeTable(hwp);
    const bool restored = hancom::com_state::RestoreSelection(hwp, cursor, selection);
    const RawCall modifiedAfter = InvokeOneShot(hwp, modifiedSpec, {}, true);
    const std::wstring afterText = FormatDocumentContentSignature(
        CaptureDocumentContentSignature(hwp));
    if (!restored ||
        (modifiedAfter.semantic != SemanticStatus::Pass &&
         modifiedAfter.semantic != SemanticStatus::FalseResult) ||
        modifiedAfter.result.boolVal != modified.result.boolVal ||
        afterText.empty() || afterText != beforeText) {
        return L"HCV1\tCAPABILITY\t" + scenario +
            L"\tV1\nRESULT\tFAIL\tSTATE_RESTORE_OR_CONTENT_SIGNATURE";
    }
    return body + L"\nSTATE\tRESTORED\nCONTENT_SIGNATURE\tUNCHANGED";
}

}

void ResetTableGraphDiagnosticCounters() noexcept {
    g_tableGraphDiagnosticCounters = {};
}

TableGraphDiagnosticCounters ReadTableGraphDiagnosticCounters() noexcept {
    return g_tableGraphDiagnosticCounters;
}

bool CaptureStorySpineReaderPayload(
    IDispatch* const hwp,
    graph::capture::ReaderPayload* const output,
    StorySpineReaderDiagnostics* const diagnostics) noexcept {
    if (output == nullptr) return false;
    StorySpineSink sink;
    graph::stories::CaptureDiagnostics captureDiagnostics;
    const graph::stories::CaptureStatus status =
        graph::stories::CaptureNativeStructureStories(
            hwp, sink, &captureDiagnostics);
    if (diagnostics != nullptr) {
        diagnostics->began = sink.Began();
        diagnostics->committed = sink.Committed();
        diagnostics->aborted = sink.Aborted();
    }
    try {
        return AdaptStorySpinePayload(status, sink, output);
    } catch (...) {
        graph::capture::ReaderPayload failed;
        failed.reader = graph::capture::QualifiedReader::StorySpine;
        failed.outcome = graph::capture::ReaderOutcome::Failed;
        failed.coverage = graph::CoverageState::ReadFailed;
        *output = std::move(failed);
        return false;
    }
}

bool AdaptTableInstanceIdObservation(
    const RawCall& call,
    graph::capture::TableObservation* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    try {
        graph::capture::TableObservation observed;
        if (call.semantic == SemanticStatus::Pass &&
            call.result.vt == VT_BSTR) {
            observed.instanceId = BstrText(call.result.bstrVal);
            observed.instanceIdPresent = true;
        } else {
            observed.instanceId.clear();
            observed.instanceIdPresent = false;
        }
        *output = std::move(observed);
        return true;
    } catch (...) {
        return false;
    }
}

const hancom::inspection::TableDispatchTypeContext&
PrevalidatedTableDispatchTypes() noexcept {
    static const hancom::inspection::TableDispatchTypeContext types{
        {{0x5E6A8276,0xCF1C,0x42B8,{0xBC,0xED,0x31,0x95,0x48,0xB0,0x2A,0xF6}},
         0,0,0,TKIND_DISPATCH,4288},
        {{0x46BB1DBE,0x1919,0x4B84,{0xA1,0x2C,0x55,0xC0,0x51,0xEA,0x68,0x9F}},
         0,0,0,TKIND_DISPATCH,4160},
        {{0x6CD92669,0x5926,0x48DC,{0x99,0x65,0x56,0xBD,0x81,0x0E,0x13,0x49}},
         0,0,0,TKIND_DISPATCH,4160},
        {{0xA1AE7374,0x1B71,0x4C4B,{0x81,0x55,0xC3,0x15,0xB9,0xF1,0xAB,0xB3}},
         0,0,0,TKIND_DISPATCH,4160},
        {{0x9F62533A,0xF775,0x4DEC,{0xA9,0xA1,0x04,0x75,0x7E,0xB6,0x94,0xFA}},
         0,0,0,TKIND_DISPATCH,4160},
        {{0x599CBB08,0x7780,0x4F3B,{0x8A,0xDA,0x7F,0x2E,0xCF,0xB5,0x71,0x81}},
         0,0,0,TKIND_DISPATCH,4160},
        {{0x1BE3D304,0x747E,0x4702,{0xB0,0x2A,0x3F,0x23,0xB1,0xA6,0xF5,0x3C}},
         0,0,0,TKIND_DISPATCH,4160},
        {{0xFBA628D5,0x0C64,0x4BBF,{0x81,0xDF,0x47,0xFE,0xAF,0x65,0xBF,0x2B}},
         0,0,0,TKIND_DISPATCH,4160},
        {{0xAEB10CD9,0x3A09,0x4752,{0xB2,0xAB,0x94,0x71,0x37,0x27,0x34,0x36}},
         0,0,0,TKIND_DISPATCH,4160},
        {{0x384CF56D,0x8A4D,0x4002,{0xB2,0x23,0x4A,0x8D,0x9D,0xB1,0x7F,0xEE}},
         0,0,0,TKIND_DISPATCH,4160},
        SYS_WIN32,
    };
    return types;
}

struct ResolvedObservedControl final {
    CComPtr<IDispatch> dispatch;
    hancom::dispatch::TypeIdentityToken type;
};

bool ValidateObservedControlDispatch(
    IDispatch* const control,
    const graph::capture::ControlObservation& wanted,
    ResolvedObservedControl* const validated) noexcept {
    if (control == nullptr) return false;
    CComVariant raw;
    std::wstring ctrlId;
    if (FAILED(hancom::dispatch::PropertyGet(control, L"CtrlID", &raw)) ||
        FAILED(hancom::dispatch::AsString(raw, &ctrlId)) ||
        ctrlId != wanted.ctrlId)
        return false;
    if (wanted.instanceIdPresent) {
        std::wstring instanceId;
        if (FAILED(hancom::dispatch::Method(
                control, L"GetCtrlInstID", {}, &raw)) ||
            FAILED(hancom::dispatch::AsString(raw, &instanceId)) ||
            instanceId != wanted.instanceId)
            return false;
    }
    if (validated != nullptr) {
        validated->dispatch = control;
        validated->type = PrevalidatedTableDispatchTypes().control;
    }
    return true;
}

struct TableCriticalPathTelemetry final {
    bool enabled = false;
    std::uint64_t headCtrlWall100ns = 0;
    std::uint64_t nextWall100ns = 0;
    std::uint64_t graphProbeWall100ns = 0;
    std::uint64_t graphProbeTables = 0;
    std::uint64_t captionWall100ns = 0;
    std::uint64_t captionTablesAttempted = 0;
    std::uint64_t reconciliationWall100ns = 0;
    std::uint64_t reconciliationItems = 0;
};

bool TableCriticalPathTelemetryEnabled() noexcept {
    return GetEnvironmentVariableW(
        L"TODO18_CAPTURE_PROGRESS_PATH", nullptr, 0) != 0;
}

std::uint64_t TableCriticalPathTick() noexcept {
    LARGE_INTEGER value{};
    return QueryPerformanceCounter(&value) != FALSE
        ? static_cast<std::uint64_t>(value.QuadPart)
        : 0;
}

std::uint64_t TableCriticalPathWall100ns(
    const std::uint64_t started) noexcept {
    if (started == 0) return 0;
    LARGE_INTEGER frequency{};
    const std::uint64_t finished = TableCriticalPathTick();
    if (finished < started ||
        QueryPerformanceFrequency(&frequency) == FALSE ||
        frequency.QuadPart <= 0) return 0;
    const std::uint64_t ticks = finished - started;
    const std::uint64_t denominator =
        static_cast<std::uint64_t>(frequency.QuadPart);
    return ticks / denominator * UINT64_C(10000000) +
        ticks % denominator * UINT64_C(10000000) / denominator;
}

bool ResolveObservedControlsOnce(
    IDispatch* const hwp,
    const std::vector<graph::capture::ControlObservation>& controls,
    std::map<std::uint64_t, ResolvedObservedControl>* const resolved,
    TableCriticalPathTelemetry* const telemetry) noexcept {
    if (hwp == nullptr || resolved == nullptr) return false;
    resolved->clear();
    std::map<std::uint64_t, const graph::capture::ControlObservation*> wanted;
    for (const auto& control : controls) {
        if (control.ctrlId == L"tbl" &&
            !wanted.emplace(control.headCtrlOrdinal, &control).second)
            return false;
    }
    if (wanted.empty()) return true;
    CComVariant raw;
    const std::uint64_t headStarted = telemetry != nullptr && telemetry->enabled
        ? TableCriticalPathTick() : 0;
    hancom::inspection::NoteTableHeadCtrlCall();
    CComPtr<IDispatch> current;
    if (FAILED(hancom::dispatch::PropertyGet(hwp, L"HeadCtrl", &raw)) ||
        FAILED(hancom::dispatch::AsDispatch(raw, current)) || current == nullptr)
        return false;
    if (telemetry != nullptr && telemetry->enabled) {
        telemetry->headCtrlWall100ns =
            TableCriticalPathWall100ns(headStarted);
    }
    const std::uint64_t last = wanted.rbegin()->first;
    const std::uint64_t nextStarted = telemetry != nullptr && telemetry->enabled
        ? TableCriticalPathTick() : 0;
    for (std::uint64_t ordinal = 0; ordinal <= last; ++ordinal) {
        const auto expected = wanted.find(ordinal);
        if (expected != wanted.end()) {
            ResolvedObservedControl validated;
            if (!ValidateObservedControlDispatch(
                    current, *expected->second, &validated))
                return false;
            resolved->emplace(ordinal, std::move(validated));
        }
        if (ordinal == last) break;
        CComPtr<IDispatch> next;
        raw.Clear();
        hancom::inspection::NoteTableNextCall();
        if (FAILED(hancom::dispatch::PropertyGet(current, L"Next", &raw)) ||
            FAILED(hancom::dispatch::AsDispatch(raw, next)) || next == nullptr)
            return false;
        current = next;
    }
    if (telemetry != nullptr && telemetry->enabled) {
        telemetry->nextWall100ns = TableCriticalPathWall100ns(nextStarted);
    }
    return resolved->size() == wanted.size();
}

bool CaptureTableTopologyReaderPayload(
    IDispatch* const hwp,
    const std::vector<graph::capture::ControlObservation>& controls,
    graph::capture::ReaderPayload* const output,
    std::wstring* const failure) noexcept {
    const auto fail = [failure](const std::wstring& detail) noexcept {
        if (failure != nullptr) {
            try {
                *failure = detail;
            } catch (...) {
            }
        }
        return false;
    };
    if (hwp == nullptr || output == nullptr) {
        return fail(L"table:invalid-argument");
    }
    try {
        hancom::inspection::ResetTableCallCounters();
        graph::capture::ReaderPayload staged;
        staged.reader = graph::capture::QualifiedReader::TableTopology;
        staged.outcome = graph::capture::ReaderOutcome::Complete;
        staged.coverage = graph::CoverageState::Complete;
        TableCriticalPathTelemetry telemetry;
        telemetry.enabled = TableCriticalPathTelemetryEnabled();
        std::map<std::uint64_t, ResolvedObservedControl> resolvedControls;
        if (!ResolveObservedControlsOnce(
                hwp, controls, &resolvedControls, &telemetry))
            return fail(L"table:authoritative-control-traversal");
        std::set<std::int64_t> automaticNumberLists;
        for (const auto& control : controls) {
            if (control.ctrlId == L"atno")
                automaticNumberLists.insert(control.anchor.list);
        }
        for (const auto& control : controls) {
            if (control.ctrlId != L"tbl") {
                continue;
            }
            const auto resolvedTable =
                resolvedControls.find(control.headCtrlOrdinal);
            if (resolvedTable == resolvedControls.end()) {
                return fail(
                    L"table:resolve:" +
                    std::to_wstring(control.headCtrlOrdinal));
            }
            CComPtr<IDispatch> tableDispatch = resolvedTable->second.dispatch;
            hancom::inspection::TableDispatchTypeContext dispatchTypes =
                PrevalidatedTableDispatchTypes();
            dispatchTypes.control = resolvedTable->second.type;
            const bool uniqueInstance = control.instanceIdPresent &&
                !control.instanceId.empty() &&
                std::count_if(
                    controls.begin(), controls.end(),
                    [&control](const auto& candidate) {
                        return candidate.ctrlId == L"tbl" &&
                            candidate.instanceIdPresent &&
                            candidate.instanceId == control.instanceId;
                    }) == 1;
            graph::capture::ReaderPayload one;
            one.reader = staged.reader;
            const std::uint64_t graphStarted = telemetry.enabled
                ? TableCriticalPathTick() : 0;
            static_cast<void>(ProbeTableGraph(
                hwp, &one, tableDispatch, uniqueInstance, false, &control,
                &dispatchTypes));
            if (telemetry.enabled) {
                telemetry.graphProbeWall100ns +=
                    TableCriticalPathWall100ns(graphStarted);
                ++telemetry.graphProbeTables;
            }
            if (one.outcome != graph::capture::ReaderOutcome::Complete ||
                one.tables.size() != 1) {
                return fail(
                    L"table:probe:" +
                    std::to_wstring(control.headCtrlOrdinal));
            }
            one.tables.front().headCtrlOrdinal = control.headCtrlOrdinal;
            one.tables.front().anchor = control.anchor;
            hancom::inspection::TableCaptionObservation caption;
            std::wstring captionError;
            if (one.tables.front().instanceIdPresent) {
                const std::uint64_t captionStarted = telemetry.enabled
                    ? TableCriticalPathTick() : 0;
                const bool captionInspected =
                    hancom::inspection::InspectTableCaption(
                        hwp, one.tables.front().instanceId, &caption,
                        &captionError);
                if (telemetry.enabled) {
                    telemetry.captionWall100ns +=
                        TableCriticalPathWall100ns(captionStarted);
                    ++telemetry.captionTablesAttempted;
                }
                if (!captionInspected) {
                    return fail(
                        L"table:caption:" +
                        std::to_wstring(control.headCtrlOrdinal) + L":" +
                        captionError);
                }
            }
            auto& table = one.tables.front();
            const auto scalarState = [](
                const hancom::inspection::NativeObservationState state) {
                return state ==
                        hancom::inspection::NativeObservationState::Value
                    ? graph::ObservationState::Value
                    : state == hancom::inspection::NativeObservationState::NotExposed
                        ? graph::ObservationState::NotExposed
                        : graph::ObservationState::ReadFailed;
            };
            table.captionPresent = {
                scalarState(caption.presenceState), caption.exists ? 1 : 0};
            if (caption.exists) {
                table.captionList = {
                    graph::ObservationState::Value, caption.listId};
                table.captionStart = {
                    caption.listId, caption.paragraph, caption.character};
                table.captionText = {
                    scalarState(caption.textState), caption.text};
                table.captionStyleId = {
                    scalarState(caption.styleIdState), caption.styleId};
                table.captionStyleName = {
                    scalarState(caption.styleNameState), caption.styleName};
                table.captionPageStart = {
                    scalarState(caption.pageStartState), caption.pageStart};
                table.captionPageEnd = {
                    scalarState(caption.pageEndState), caption.pageEnd};
                table.captionAutomaticNumber = {
                    graph::ObservationState::Value,
                    automaticNumberLists.find(caption.listId) !=
                            automaticNumberLists.end()
                        ? 1 : 0};
            } else {
                const graph::ObservationState absentState =
                    caption.presenceState ==
                            hancom::inspection::NativeObservationState::Value
                        ? graph::ObservationState::NotApplicable
                        : scalarState(caption.presenceState);
                table.captionList.state = absentState;
                table.captionText.state = absentState;
                table.captionAutomaticNumber.state = absentState;
                table.captionStyleId.state = absentState;
                table.captionStyleName.state = absentState;
                table.captionPageStart.state = absentState;
                table.captionPageEnd.state = absentState;
            }
            if (one.coverage != graph::CoverageState::Complete) {
                staged.coverage = one.coverage;
            }
            staged.coverageFacts.insert(
                staged.coverageFacts.end(),
                std::make_move_iterator(one.coverageFacts.begin()),
                std::make_move_iterator(one.coverageFacts.end()));
            staged.tables.push_back(std::move(one.tables.front()));
        }
        const std::uint64_t reconciliationStarted = telemetry.enabled
            ? TableCriticalPathTick() : 0;
        std::map<std::int64_t,
                 std::vector<std::pair<size_t, std::wstring>>> cellOwnersByList;
        for (size_t table = 0; table < staged.tables.size(); ++table) {
            for (const auto& cell : staged.tables[table].cells)
                cellOwnersByList[cell.listId].push_back({table, cell.address});
        }
        for (size_t nested = 0; nested < staged.tables.size(); ++nested) {
            const std::int64_t anchorList = staged.tables[nested].anchor.list;
            if (anchorList == 0) {
                continue;
            }
            size_t matches = 0;
            const auto owners = cellOwnersByList.find(anchorList);
            if (owners != cellOwnersByList.end()) {
                for (const auto& owner : owners->second) {
                    if (owner.first == nested) continue;
                    staged.tables[nested].hostTableInstanceId =
                        staged.tables[owner.first].instanceId;
                    staged.tables[nested].hostTableInstanceIdPresent =
                        staged.tables[owner.first].instanceIdPresent;
                    staged.tables[nested].hostCellAddress = owner.second;
                    ++matches;
                }
            }
            if (matches > 1) {
                return fail(
                    L"table:nested-owner:" + std::to_wstring(nested) +
                    L":" + std::to_wstring(matches));
            }
        }
        if (telemetry.enabled) {
            telemetry.reconciliationWall100ns =
                TableCriticalPathWall100ns(reconciliationStarted);
            telemetry.reconciliationItems = staged.tables.size();
        }
        *output = std::move(staged);
        const hancom::inspection::TableCallCounters counters =
            hancom::inspection::ReadTableCallCounters();
        if (telemetry.enabled) {
            hancom::graph::capture::RecordCaptureProgressPoint(
                hancom::graph::capture::CaptureProgressPoint::
                    TableHeadCtrlAcquisition,
                telemetry.headCtrlWall100ns, counters.headCtrl);
            hancom::graph::capture::RecordCaptureProgressPoint(
                hancom::graph::capture::CaptureProgressPoint::
                    TableNextTraversal,
                telemetry.nextWall100ns, counters.next);
            hancom::graph::capture::RecordCaptureProgressPoint(
                hancom::graph::capture::CaptureProgressPoint::TableGraphProbe,
                telemetry.graphProbeWall100ns, telemetry.graphProbeTables);
            hancom::graph::capture::RecordCaptureProgressPoint(
                hancom::graph::capture::CaptureProgressPoint::
                    TableCaptionInspection,
                telemetry.captionWall100ns,
                telemetry.captionTablesAttempted);
            hancom::graph::capture::RecordCaptureProgressPoint(
                hancom::graph::capture::CaptureProgressPoint::
                    TableReconciliationMerge,
                telemetry.reconciliationWall100ns,
                telemetry.reconciliationItems);
        }
        hancom::graph::capture::RecordCaptureProgressPoint(
            hancom::graph::capture::CaptureProgressPoint::TableControlCalls,
            counters.headCtrl, counters.next);
        hancom::graph::capture::RecordCaptureProgressPoint(
            hancom::graph::capture::CaptureProgressPoint::TableRangeCalls,
            counters.directRange, counters.fallback);
        hancom::graph::capture::RecordCaptureProgressPoint(
            hancom::graph::capture::CaptureProgressPoint::TableRangeDispatchReasons,
            counters.moveToCellFailed, counters.rangeNotExposed);
        hancom::graph::capture::RecordCaptureProgressPoint(
            hancom::graph::capture::CaptureProgressPoint::TableRangeResultReasons,
            counters.rangeInvokeFailed + counters.rangeResultInvalid,
            counters.rangeMemberReadFailed);
        hancom::graph::capture::RecordCaptureProgressPoint(
            hancom::graph::capture::CaptureProgressPoint::TableRangeGeometryReasons,
            counters.rangeBoundsInvalid +
                counters.rangeCoordinateBaseMismatch +
                counters.rangeOwnerMismatch,
            counters.rangeTopologyRejected);
        hancom::graph::capture::RecordCaptureProgressPoint(
            hancom::graph::capture::CaptureProgressPoint::TableExactCover,
            counters.exactCover, counters.fallback);
        hancom::graph::capture::RecordCaptureProgressPoint(
            hancom::graph::capture::CaptureProgressPoint::TableExactCoverReasons,
            counters.exactCoverDimensionUnavailable +
                counters.exactCoverDimensionInvalid,
            counters.exactCoverAmbiguous);
        hancom::graph::capture::RecordCaptureProgressPoint(
            hancom::graph::capture::CaptureProgressPoint::TableDimensionSources,
            counters.dimensionProperties,
            counters.dimensionCaret);
        return true;
    } catch (...) {
        return fail(L"table:exception");
    }
}

bool ReconcileControlLocators(
    const std::vector<graph::capture::ControlObservation>& controls,
    std::vector<graph::capture::TableObservation>* const tables,
    std::vector<graph::capture::ImageObservation>* const images) noexcept {
    if (tables == nullptr || images == nullptr) {
        return false;
    }
    try {
        std::vector<graph::capture::TableObservation> stagedTables = *tables;
        std::vector<graph::capture::ImageObservation> stagedImages = *images;
        const auto merge = [&controls](
            const std::wstring& ctrlId,
            const bool instanceIdPresent,
            const std::wstring& instanceId,
            const std::uint64_t ordinal,
            const bool locatorComplete,
            graph::capture::NativePosition* const anchor) {
            const auto matched = std::find_if(
                controls.begin(), controls.end(),
                [&ctrlId, ordinal](const auto& control) {
                    return control.ctrlId == ctrlId &&
                        control.headCtrlOrdinal == ordinal;
                });
            if (matched == controls.end() ||
                matched->instanceIdPresent != instanceIdPresent ||
                matched->instanceId != instanceId ||
                std::find_if(std::next(matched), controls.end(),
                    [&ctrlId, ordinal](const auto& control) {
                        return control.ctrlId == ctrlId &&
                            control.headCtrlOrdinal == ordinal;
                    }) != controls.end()) {
                return false;
            }
            if (locatorComplete) {
                return anchor->list == matched->anchor.list &&
                    anchor->paragraph == matched->anchor.paragraph &&
                    anchor->character == matched->anchor.character;
            }
            *anchor = matched->anchor;
            return true;
        };
        for (auto& table : stagedTables) {
            if (!merge(L"tbl", table.instanceIdPresent, table.instanceId,
                       table.headCtrlOrdinal, table.locatorComplete,
                       &table.anchor)) {
                return false;
            }
        }
        const size_t observedTables = static_cast<size_t>(std::count_if(
            controls.begin(), controls.end(),
            [](const auto& control) { return control.ctrlId == L"tbl"; }));
        if (stagedTables.size() != observedTables) {
            return false;
        }
        for (auto& image : stagedImages) {
            if (!merge(image.ctrlId, image.instanceIdPresent,
                       image.instanceId, image.headCtrlOrdinal,
                       image.locatorComplete, &image.anchor)) {
                return false;
            }
        }
        *tables = std::move(stagedTables);
        *images = std::move(stagedImages);
        return true;
    } catch (...) {
        return false;
    }
}

RawCall InvokeOneShot(
    IDispatch* const target,
    const CallSpec& spec,
    const std::vector<VARIANTARG>& arguments,
    const bool validateTypeInfo,
    const dispatch::TypeIdentityToken* const prevalidatedType) noexcept {
    RawCall call;
    if (target == nullptr || spec.name == nullptr ||
        arguments.size() != static_cast<size_t>(spec.arity)) {
        call.typeInfoStatus = E_INVALIDARG;
        return call;
    }
    call.typeInfoStatus = validateTypeInfo && prevalidatedType == nullptr
        ? ValidateTypeInfo(target, spec, arguments) : S_OK;
    if (FAILED(call.typeInfoStatus)) {
        call.semantic = SemanticStatus::TypeInfoFailure;
        return call;
    }
    DISPID member = DISPID_UNKNOWN;
    if (prevalidatedType == nullptr) {
        LPOLESTR name = const_cast<LPOLESTR>(spec.name);
        call.getIdsStatus = target->GetIDsOfNames(
            IID_NULL, &name, 1, 0, &member);
    } else {
        call.getIdsStatus = dispatch::ResolveDispidQualified(
            target, *prevalidatedType, spec.name, &member);
    }
    if (FAILED(call.getIdsStatus)) {
        call.semantic = SemanticStatus::NameFailure;
        return call;
    }
    if (member != spec.dispid) {
        call.semantic = SemanticStatus::ContractMismatch;
        return call;
    }
    std::vector<VARIANTARG> reversed(arguments.size());
    for (VARIANTARG& value : reversed) VariantInit(&value);
    for (size_t index = 0; index < arguments.size(); ++index) {
        const HRESULT copied = VariantCopy(
            &reversed[index], const_cast<VARIANTARG*>(&arguments[arguments.size()-index-1]));
        if (FAILED(copied)) {
            for (VARIANTARG& value : reversed) VariantClear(&value);
            call.invokeStatus = copied;
            call.semantic = SemanticStatus::MalformedOutput;
            return call;
        }
    }
    DISPPARAMS parameters{};
    parameters.rgvarg = reversed.empty() ? nullptr : reversed.data();
    parameters.cArgs = static_cast<UINT>(reversed.size());
    EXCEPINFO exception{};
    call.argumentError = UINT_MAX;
    call.invokeStatus = target->Invoke(
        member, IID_NULL, 0, spec.invkind, &parameters,
        spec.resultType == VT_VOID ? nullptr : &call.result,
        &exception, &call.argumentError);
    for (const VARIANTARG& argument : arguments) {
        if ((argument.vt & VT_BYREF) == 0) continue;
        ByRefObservation observed;
        observed.declaredType = argument.vt;
        observed.pointerNull = argument.byref == nullptr;
        if (!observed.pointerNull) {
            VARIANTARG indirect = argument;
            if (FAILED(VariantCopyInd(&observed.value, &indirect))) {
                observed.value.Clear();
                observed.value.vt = VT_ERROR;
                observed.value.scode = DISP_E_TYPEMISMATCH;
            }
        }
        call.byRefOutputs.push_back(observed);
    }
    for (VARIANTARG& value : reversed) VariantClear(&value);
    CaptureException(&exception, &call.exception);
    if (FAILED(call.invokeStatus)) {
        call.semantic = SemanticStatus::ComFailure;
        return call;
    }
    if (spec.resultType == VT_VOID) {
        call.semantic = SemanticStatus::Pass;
        return call;
    }
    call.semantic = ClassifyResult(spec, call.result);
    return call;
}

std::wstring FormatFixturePrerequisites(const std::wstring& scenario,
                                        const FixtureCoverage& coverage) {
    std::wostringstream output;
    output << L"HCV1\tCAPABILITY\t" << scenario << L"\tV1\n"
           << L"RESULT\tINCONCLUSIVE\tFIXTURE_CAPABILITY_PREREQUISITES\n";
    if (scenario == L"CONTAINMENT") {
        output << L"COVERAGE\tshared_header_footer=" << (coverage.sharedHeaderFooter ? L"OBSERVED" : L"NOT_OBSERVED") << L'\n'
               << L"COVERAGE\tnotes=" << (coverage.notes ? L"OBSERVED" : L"NOT_OBSERVED") << L'\n'
               << L"COVERAGE\ttext_boxes=" << (coverage.textBoxes ? L"OBSERVED" : L"NOT_OBSERVED") << L'\n'
               << L"COVERAGE\tcaptions=" << (coverage.captions ? L"OBSERVED" : L"NOT_OBSERVED");
    } else {
        output << L"COVERAGE\thorizontal_merges=" << (coverage.horizontalMerge ? L"OBSERVED" : L"NOT_OBSERVED") << L'\n'
               << L"COVERAGE\tvertical_merges=" << (coverage.verticalMerge ? L"OBSERVED" : L"NOT_OBSERVED") << L'\n'
               << L"COVERAGE\trectangular_merges=" << (coverage.rectangularMerge ? L"OBSERVED" : L"NOT_OBSERVED") << L'\n'
               << L"COVERAGE\tterminal_merges=" << (coverage.terminalMerge ? L"OBSERVED" : L"NOT_OBSERVED") << L'\n'
               << L"COVERAGE\tnested_tables=" << (coverage.nestedTable ? L"OBSERVED" : L"NOT_OBSERVED");
    }
    return output.str();
}

hancom::graph::capture::CaptureStatus CaptureDocumentGraphToStore(
    IDispatch* const hwp,
    hancom::graph::store::GraphStore* const store,
    const hancom::graph::layout::LayoutEnvironmentPlatformV1* const
        environmentPlatform,
    const hancom::graph::identity::DocumentSessionId* const session,
    hancom::graph::capture::CaptureDiagnostics* const diagnostics) noexcept {
    using hancom::graph::capture::CaptureStatus;
    if (diagnostics != nullptr) *diagnostics = {};
    if (hwp == nullptr || store == nullptr) {
        if (diagnostics != nullptr)
            diagnostics->stage =
                hancom::graph::capture::FailureStage::BeginSession;
        return CaptureStatus::InvalidArgument;
    }
    try {
        CaptureQualificationContext context;
        context.hwp = hwp;
        context.environmentPlatform = environmentPlatform;
        if (session != nullptr) {
            const std::wstring base = L"Local\\HancomGraphCapture." +
                hancom::graph::identity::FormatCanonicalUuid(*session);
            context.cancellationCheckpoint = OpenEventW(
                EVENT_MODIFY_STATE, FALSE, (base + L".Checkpoint").c_str());
            context.cancellationRequest = OpenEventW(
                SYNCHRONIZE, FALSE, (base + L".Cancel").c_str());
            // A leftover handle on only one of the two named events used to
            // abort every GraphOpen as IncompleteCapture/stage=None. Treat an
            // unpaired pair as "no waiter" and continue the capture.
            if ((context.cancellationCheckpoint == nullptr) !=
                (context.cancellationRequest == nullptr)) {
                if (context.cancellationCheckpoint != nullptr) {
                    CloseHandle(context.cancellationCheckpoint);
                    context.cancellationCheckpoint = nullptr;
                }
                if (context.cancellationRequest != nullptr) {
                    CloseHandle(context.cancellationRequest);
                    context.cancellationRequest = nullptr;
                }
            }
        }
        if (!PrepareCaptureSpoolRoot(L"hwp-live-graphread", &context.spoolRoot)) {
            if (diagnostics != nullptr) {
                diagnostics->stage =
                    hancom::graph::capture::FailureStage::BeginSession;
                diagnostics->failureDetail = L"spool-root";
            }
            return CaptureStatus::IncompleteCapture;
        }
        context.preparedStoreRoot = store->Root();
        const hancom::graph::capture::ReaderSuite suite{
            hancom::graph::capture::kQualifiedReaderCount,
            CaptureCoordinatorState,
            BeginCoordinatorAttempt,
            RunQualifiedReader,
            nullptr,
            ReleaseCoordinatorScans,
            RestoreCoordinatorState,
            CoordinatorCancelled,
            BeginCoordinatorSession,
            PrepareCoordinatorPublication,
            AbortCoordinatorSession,
            CommitCoordinatorSession,
            RecordCaptureProgress,
            FinishCoordinatorAttempt,
            RetainDetachedAttemptArtifact,
        };
        RecordCaptureProgress(
            &context,
            hancom::graph::capture::CaptureProgressPoint::CaptureBegin,
            0, 0);
        if (context.cancellationCheckpoint != nullptr &&
            context.cancellationRequest != nullptr) {
            if (SetEvent(context.cancellationCheckpoint) == FALSE) {
                if (diagnostics != nullptr) {
                    diagnostics->stage =
                        hancom::graph::capture::FailureStage::BeginSession;
                    diagnostics->failureDetail = L"checkpoint-signal";
                }
                return CaptureStatus::IncompleteCapture;
            }
            RecordCaptureProgress(
                &context,
                hancom::graph::capture::CaptureProgressPoint::CancellationCheckpoint,
                0, 0);
            context.cancellationObserved =
                WaitForSingleObject(context.cancellationRequest, 30'000) ==
                WAIT_OBJECT_0;
            if (context.cancellationObserved) {
                std::error_code ignored;
                std::filesystem::remove_all(context.spoolRoot, ignored);
                return CaptureStatus::Cancelled;
            }
        }
        hancom::graph::capture::CaptureCoordinator coordinator;
        const CaptureStatus status = coordinator.CaptureToStore(
            suite, &context, store, {},
            hancom::graph::store::FailurePoint::None, diagnostics);
        RecordCaptureProgress(
            &context,
            hancom::graph::capture::CaptureProgressPoint::CoordinatorReturn,
            static_cast<std::uint64_t>(status), 0);
        if (diagnostics != nullptr && !context.readerFailure.empty())
            diagnostics->failureDetail = context.readerFailure;
        RecordCaptureProgress(
            &context,
            hancom::graph::capture::CaptureProgressPoint::SpoolCleanupStart,
            0, 0);
        std::error_code ignored;
        std::filesystem::remove_all(context.spoolRoot, ignored);
        RecordCaptureProgress(
            &context,
            hancom::graph::capture::CaptureProgressPoint::SpoolCleanupEnd,
            ignored.value(), 0);
        RecordCaptureProgress(
            &context,
            hancom::graph::capture::CaptureProgressPoint::CaptureProviderReturn,
            static_cast<std::uint64_t>(status), 0);
        return status;
    } catch (...) {
        if (diagnostics != nullptr)
            diagnostics->stage =
                hancom::graph::capture::FailureStage::Exception;
        return CaptureStatus::IncompleteCapture;
    }
}

std::wstring ProbeCapability(
    IDispatch* const hwp,
    const std::wstring& scenario,
    const std::vector<std::wstring>& inputLines) {
    return ProbeCapability(hwp, scenario, inputLines, nullptr);
}

std::wstring ProbeCapability(
    IDispatch* const hwp,
    const std::wstring& scenario,
    const std::vector<std::wstring>& inputLines,
    const hancom::graph::layout::LayoutEnvironmentPlatformV1* const
        environmentPlatform) {
    if (hwp == nullptr) return L"HCV1\tERROR\tNO_HWP\tcapability target is unavailable";
    if (scenario == L"TYPELIB_EXTENSION") return ProbeTypeLibExtension();
    if (scenario == L"STORY_SPINE" || scenario == L"TEXT_CURRENT" ||
        scenario == L"TEXT_BODY" ||
        scenario == L"EFFECTIVE_PROPERTIES" ||
        scenario == L"CONTROL_ADAPTERS" ||
        scenario == L"CAPTURE_COORDINATOR" ||
        scenario == L"IMAGE_GRAPH" || scenario == L"LAYOUT_GRAPH" ||
        scenario == L"TABLE_GRAPH" || scenario == L"TABLE_RANGE_BASES") {
        return StateGuardedProbe(hwp, scenario, environmentPlatform);
    }
    const FixtureCoverage coverage = ParseCoverage(inputLines);
    if (scenario == L"CONTAINMENT") {
        return ContainmentCoverage(coverage)
            ? StateGuardedProbe(hwp, scenario, environmentPlatform)
            : FormatFixturePrerequisites(scenario, coverage);
    }
    if (scenario == L"TABLE_TOPOLOGY") {
        return TableCoverage(coverage)
            ? StateGuardedProbe(hwp, scenario, environmentPlatform)
            : FormatFixturePrerequisites(scenario, coverage);
    }
    return L"HCV1\tERROR\tBAD_CAPABILITY\tunknown capability scenario";
}

}

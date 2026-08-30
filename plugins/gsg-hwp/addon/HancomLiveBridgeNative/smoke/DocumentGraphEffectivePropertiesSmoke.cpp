#include "../DocumentGraphEffectiveProperties.h"
#include "../DocumentGraphCaptureRecords.h"
#include "../DocumentGraphCodecInternal.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

using hancom::graph::PropertyApplicabilitySet;
using hancom::graph::PropertyOrigin;
using hancom::graph::PropertyRule;
using hancom::graph::PropertyValueShape;
using hancom::graph::FindPropertyRule;
using hancom::graph::kPropertyRegistryV1;
using hancom::graph::kWithdrawnPropertyKeyIdsV1;
using hancom::graph::properties::CaptureDiagnostics;
using hancom::graph::properties::CaptureEffectiveProperties;
using hancom::graph::properties::CaptureStatus;
using hancom::graph::properties::CatalogCoverage;
using hancom::graph::properties::EffectivePropertyContext;
using hancom::graph::properties::EffectivePropertySink;
using hancom::graph::properties::EffectivePropertySource;
using hancom::graph::properties::PropertyObservation;
using hancom::graph::properties::ReadStatus;
using hancom::graph::properties::CaptureReferenceClosure;
using hancom::graph::properties::DerivePageSetupDigest;
using hancom::graph::properties::ReferenceClosureDiagnostics;
using hancom::graph::properties::ReferenceClosureSource;
using hancom::graph::properties::ReferenceSite;
using hancom::graph::properties::ReferenceSiteObservation;
using hancom::graph::properties::ReferencedDefinition;
using hancom::graph::properties::SiteReference;

bool Supported(const PropertyRule& rule) {
    return rule.valueShape == PropertyValueShape::Scalar &&
        (rule.applicabilitySet == PropertyApplicabilitySet::CharacterShape ||
         rule.applicabilitySet == PropertyApplicabilitySet::ParagraphShape);
}

class FakeSource final : public EffectivePropertySource {
public:
    ReadStatus Read(
        const EffectivePropertyContext& context,
        const PropertyRule& rule,
        PropertyObservation* const observation) noexcept override {
        attemptedKeys.push_back(rule.id);
        lastTarget = context.target;
        if (rule.id == failedKey) {
            return ReadStatus::ReadFailed;
        }
        if (rule.id == notApplicableKey) {
            return ReadStatus::NotApplicable;
        }
        if (rule.id == notExposedKey || !Supported(rule)) {
            return ReadStatus::NotExposed;
        }
        observation->canonicalValue =
            L"value:" + std::to_wstring(rule.id);
        return ReadStatus::Value;
    }

    std::uint32_t failedKey = 0;
    std::uint32_t notExposedKey = 0;
    std::uint32_t notApplicableKey = 0;
    hancom::graph::capture::PropertyTarget lastTarget =
        hancom::graph::capture::PropertyTarget::Document;
    std::vector<std::uint32_t> attemptedKeys{};
};

class TransactionalSink final : public EffectivePropertySink {
public:
    bool Append(const PropertyObservation& observation) noexcept override {
        if (failAppend) {
            return false;
        }
        pending.push_back(observation);
        return true;
    }

    bool MarkCatalogCoverage(
        const CatalogCoverage& value) noexcept override {
        if (failCoverage) {
            return false;
        }
        coverage = value;
        coverageMarked = true;
        return true;
    }

    bool Commit() noexcept override {
        if (failCommit) {
            return false;
        }
        published = pending;
        committed = true;
        return true;
    }

    void Abort() noexcept override {
        aborted = true;
        pending.clear();
        published.clear();
        coverageMarked = false;
    }

    bool failAppend = false;
    bool failCoverage = false;
    bool failCommit = false;
    bool coverageMarked = false;
    bool committed = false;
    bool aborted = false;
    CatalogCoverage coverage{};
    std::vector<PropertyObservation> pending{};
    std::vector<PropertyObservation> published{};
};

bool SchemaDrivenRegistrySmoke() {
    FakeSource source;
    source.failedKey = 1000;
    source.notExposedKey = 1001;
    source.notApplicableKey = 1002;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const EffectivePropertyContext context{
        hancom::graph::capture::PropertyTarget::Paragraph, L"0:0"};
    const CaptureStatus status = CaptureEffectiveProperties(
        context, source, sink, &diagnostics);
    if (status != CaptureStatus::Complete ||
        sink.published.size() != kPropertyRegistryV1.size() ||
        !sink.coverageMarked || !sink.committed || sink.aborted ||
        diagnostics.readFailedCount != 1 ||
        diagnostics.notApplicableCount == 0 ||
        !diagnostics.catalogCoverageEmitted) {
        return false;
    }
    std::uint64_t expectedValues = 0;
    for (size_t index = 0; index < kPropertyRegistryV1.size(); ++index) {
        const PropertyRule& rule = kPropertyRegistryV1[index];
        const PropertyObservation& observation = sink.published[index];
        const PropertyOrigin expectedOrigin =
            observation.status == ReadStatus::NotApplicable
            ? PropertyOrigin::NotApplicable
            : PropertyOrigin::Unavailable;
        if (observation.key != rule.id ||
            observation.origin != expectedOrigin) {
            return false;
        }
        if (Supported(rule) && rule.id != source.failedKey &&
            rule.id != source.notExposedKey &&
            rule.id != source.notApplicableKey) {
            ++expectedValues;
            if (observation.status != ReadStatus::Value ||
                observation.canonicalValue.empty()) {
                return false;
            }
        } else if (observation.status == ReadStatus::Value ||
                   !observation.canonicalValue.empty()) {
            return false;
        }
    }
    return diagnostics.valueCount == expectedValues &&
        diagnostics.valueCount + diagnostics.notApplicableCount +
            diagnostics.notExposedCount + diagnostics.readFailedCount ==
            kPropertyRegistryV1.size();
}

bool CatalogAndOriginBlockSmoke() {
    FakeSource source;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status = CaptureEffectiveProperties(
        {hancom::graph::capture::PropertyTarget::Paragraph, L"0:0"},
        source, sink, &diagnostics);
    return status == CaptureStatus::Complete &&
        sink.coverage.styleDefinitionsNotExposed &&
        sink.coverage.numberingDefinitionsNotExposed &&
        sink.coverage.bulletDefinitionsNotExposed &&
        sink.coverage.tabDefinitionCatalogNotExposed &&
        sink.coverage.directInheritedOriginNotExposed;
}

bool TargetAwareObservationSmoke() {
    FakeSource source;
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const EffectivePropertyContext context{
        hancom::graph::capture::PropertyTarget::Run, L"0:0:0:4"};
    const CaptureStatus status = CaptureEffectiveProperties(
        context, source, sink, &diagnostics);
    const auto character = std::find_if(
        sink.published.begin(), sink.published.end(), [](const auto& item) {
            return item.key == 1000;
        });
    const auto paragraph = std::find_if(
        sink.published.begin(), sink.published.end(), [](const auto& item) {
            return item.key == 2000;
        });
    const bool compatible = status == CaptureStatus::Complete &&
        character != sink.published.end() &&
        character->status == ReadStatus::Value &&
        character->target == context.target &&
        character->targetIdentity == context.targetIdentity &&
        character->ownerField == 102 &&
        std::count(source.attemptedKeys.begin(), source.attemptedKeys.end(),
                   std::uint32_t{1000}) == 1;
    const bool incompatible = paragraph != sink.published.end() &&
        paragraph->status == ReadStatus::NotApplicable &&
        paragraph->canonicalValue.empty() &&
        paragraph->target == context.target &&
        paragraph->targetIdentity == context.targetIdentity &&
        paragraph->ownerField == 0 &&
        std::count(source.attemptedKeys.begin(), source.attemptedKeys.end(),
                   std::uint32_t{2000}) == 0;
    std::wcout << L"EFFECTIVE_PROPERTIES_TARGET_COMPATIBLE "
               << compatible << L'\n'
               << L"EFFECTIVE_PROPERTIES_TARGET_INCOMPATIBLE_TERMINAL "
               << incompatible << L'\n';
    return compatible && incompatible;
}

enum class MatrixDispatchRole : std::uint8_t {
    Root,
    Action,
    ParameterSets,
    Parameter,
    Nested,
};

struct MatrixDispatchFixture;

class MatrixDispatch final : public IDispatch {
public:
    MatrixDispatch(
        MatrixDispatchFixture* fixture,
        MatrixDispatchRole role,
        size_t pathDepth = 0,
        bool forbiddenProfile = false) noexcept
        : fixture_(fixture), role_(role), pathDepth_(pathDepth),
          forbiddenProfile_(forbiddenProfile) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid, void** object) noexcept override {
        if (object == nullptr) return E_POINTER;
        *object = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDispatch) {
            return E_NOINTERFACE;
        }
        *object = static_cast<IDispatch*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() noexcept override { return 2; }
    ULONG STDMETHODCALLTYPE Release() noexcept override { return 1; }
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* count) noexcept override {
        if (count == nullptr) return E_POINTER;
        *count = 0;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfo(
        UINT, LCID, ITypeInfo**) noexcept override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID iid, LPOLESTR* names, UINT count, LCID, DISPID* ids)
        noexcept override;
    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID member, REFIID iid, LCID, WORD flags,
        DISPPARAMS* parameters, VARIANT* result, EXCEPINFO*, UINT*)
        noexcept override;

private:
    friend struct MatrixDispatchFixture;
    MatrixDispatchFixture* fixture_;
    MatrixDispatchRole role_;
    size_t pathDepth_;
    bool forbiddenProfile_ = false;
    std::wstring requestedName_{};
};

enum class MatrixLane : std::uint8_t {
    Value,
    NotExposed,
    MemberAbsent,
    ReadFailed,
};

enum class OriginLane : std::uint8_t {
    Value,
    Missing,
    DispatchFailure,
    InvalidVariant,
};

struct MatrixDispatchFixture final {
    explicit MatrixDispatchFixture(
        const PropertyRule& value,
        MatrixLane laneValue = MatrixLane::Value,
        OriginLane originLaneValue = OriginLane::Value,
        LONG originCodeValue = 0,
        bool zeroScalarValue = false) noexcept
        : rule(value), lane(laneValue), originLane(originLaneValue),
          originCode(originCodeValue), zeroScalar(zeroScalarValue),
          root(this, MatrixDispatchRole::Root),
          action(this, MatrixDispatchRole::Action),
          parameterSets(this, MatrixDispatchRole::ParameterSets),
          parameter(this, MatrixDispatchRole::Parameter, 1),
          owner(this, MatrixDispatchRole::Nested, 1),
          wrongParameter(this, MatrixDispatchRole::Parameter, 1, true),
          nested(this, MatrixDispatchRole::Nested, 1) {
        const std::wstring path(rule.nativeMemberPath);
        size_t begin = 0;
        while (begin <= path.size()) {
            const size_t end = path.find(L'.', begin);
            std::wstring component = path.substr(begin, end - begin);
            const size_t annotation = component.find(L'@');
            if (annotation != std::wstring::npos) component.resize(annotation);
            const size_t arraySuffix = component.find(L"[]");
            if (arraySuffix != std::wstring::npos) component.resize(arraySuffix);
            components.push_back(std::move(component));
            if (end == std::wstring::npos) break;
            begin = end + 1;
        }
    }

    std::wstring_view ExpectedParameterName() const noexcept {
        switch (rule.applicabilitySet) {
        case PropertyApplicabilitySet::CharacterShape: return L"HCharShape";
        case PropertyApplicabilitySet::ParagraphShape:
        case PropertyApplicabilitySet::TabDefDefinition: return L"HParaShape";
        case PropertyApplicabilitySet::PageDefDefinition: return L"HSecDef";
        case PropertyApplicabilitySet::ColumnDefDefinition: return L"HColDef";
        case PropertyApplicabilitySet::BorderFillDefinition:
            return L"HCellBorderFill";
        default: return L"";
        }
    }

    std::wstring_view ExpectedOwnerMember() const noexcept {
        switch (rule.applicabilitySet) {
        case PropertyApplicabilitySet::TabDefDefinition: return L"TabDef";
        case PropertyApplicabilitySet::PageDefDefinition: return L"PageDef";
        case PropertyApplicabilitySet::BorderFillDefinition:
            return L"SelCellsBorderFill";
        default: return L"";
        }
    }

    EffectivePropertyContext CompatibleContext() const noexcept {
        const auto* applicability =
            hancom::graph::FindPropertyApplicabilityRule(
                rule.applicabilitySet);
        EffectivePropertyContext context;
        context.targetIdentity =
            L"matrix:" + std::to_wstring(rule.id);
        if (applicability == nullptr || applicability->tupleCount == 0) {
            return context;
        }
        const auto& tuple = applicability->tuples[0];
        switch (tuple.node) {
        case hancom::graph::NodeKind::Document:
            context.target = hancom::graph::capture::PropertyTarget::Document;
            break;
        case hancom::graph::NodeKind::Section:
            context.target = hancom::graph::capture::PropertyTarget::Section;
            break;
        case hancom::graph::NodeKind::Paragraph:
            context.target = hancom::graph::capture::PropertyTarget::Paragraph;
            break;
        case hancom::graph::NodeKind::CharacterRun:
            context.target = hancom::graph::capture::PropertyTarget::Run;
            break;
        case hancom::graph::NodeKind::GenericControl:
            context.target = hancom::graph::capture::PropertyTarget::Control;
            break;
        case hancom::graph::NodeKind::Table:
            context.target = hancom::graph::capture::PropertyTarget::Table;
            break;
        case hancom::graph::NodeKind::TableCell:
            context.target = hancom::graph::capture::PropertyTarget::Cell;
            break;
        case hancom::graph::NodeKind::Image:
            context.target = hancom::graph::capture::PropertyTarget::Image;
            break;
        case hancom::graph::NodeKind::Definition:
            context.target = hancom::graph::capture::PropertyTarget::Definition;
            context.hasDefinitionKind = true;
            context.definitionKind = tuple.definitionKind;
            break;
        default:
            context.target = hancom::graph::capture::PropertyTarget::Story;
            break;
        }
        return context;
    }

    HRESULT NativeValue(
        VARIANT* result, const bool forbiddenProfile = false) const noexcept {
        if (result == nullptr) return E_POINTER;
        VariantInit(result);
        if (rule.valueShape != PropertyValueShape::Scalar) {
            SAFEARRAY* const values = SafeArrayCreateVector(VT_UI2, 0, 2);
            if (values == nullptr) return E_OUTOFMEMORY;
            for (LONG index = 0; index < 2; ++index) {
                USHORT value = static_cast<USHORT>(rule.id + index);
                if (FAILED(SafeArrayPutElement(values, &index, &value))) {
                    SafeArrayDestroy(values);
                    return E_FAIL;
                }
            }
            result->vt = VT_ARRAY | VT_UI2;
            result->parray = values;
            return S_OK;
        }
        const LONGLONG sentinel = zeroScalar && !forbiddenProfile
            ? 0
            : static_cast<LONGLONG>(rule.id) * 100 +
                (forbiddenProfile ? 9 : 1);
        switch (rule.scalar) {
        case hancom::graph::ScalarTag::Bool:
            result->vt = VT_BOOL;
            result->boolVal = forbiddenProfile || zeroScalar
                ? VARIANT_FALSE : VARIANT_TRUE;
            return S_OK;
        case hancom::graph::ScalarTag::UTF16: {
            const std::wstring text = forbiddenProfile
                ? L"wrong-profile:" + std::to_wstring(rule.id)
                : std::wstring(rule.nativeMemberPath);
            result->vt = VT_BSTR;
            result->bstrVal = SysAllocStringLen(
                text.data(), static_cast<UINT>(text.size()));
            return result->bstrVal == nullptr ? E_OUTOFMEMORY : S_OK;
        }
        case hancom::graph::ScalarTag::Float64:
            result->vt = VT_R8;
            result->dblVal = forbiddenProfile ? -1.25 : 1.25;
            return S_OK;
        case hancom::graph::ScalarTag::Uint8:
            result->vt = VT_UI1;
            result->bVal = static_cast<BYTE>(sentinel & 0xff);
            return S_OK;
        case hancom::graph::ScalarTag::Uint16:
            result->vt = VT_UI2;
            result->uiVal = static_cast<USHORT>(sentinel & 0xffff);
            return S_OK;
        case hancom::graph::ScalarTag::Uint32:
        case hancom::graph::ScalarTag::BGR:
        case hancom::graph::ScalarTag::RawURC32:
            result->vt = VT_UI4;
            result->ulVal = static_cast<ULONG>(sentinel);
            return S_OK;
        case hancom::graph::ScalarTag::Uint64:
            result->vt = VT_UI8;
            result->ullVal = static_cast<ULONGLONG>(sentinel);
            return S_OK;
        default:
            result->vt = VT_I8;
            result->llVal = sentinel;
            return S_OK;
        }
    }

    const PropertyRule& rule;
    MatrixLane lane;
    OriginLane originLane;
    LONG originCode;
    bool zeroScalar = false;
    std::vector<std::wstring> components{};
    MatrixDispatch root;
    MatrixDispatch action;
    MatrixDispatch parameterSets;
    MatrixDispatch parameter;
    MatrixDispatch owner;
    MatrixDispatch wrongParameter;
    MatrixDispatch nested;
    std::uint64_t dispatchCalls = 0;
    std::uint64_t owningProfileCalls = 0;
    std::uint64_t forbiddenProfileCalls = 0;
    std::uint64_t rulePathCalls = 0;
    std::uint64_t preparationCalls = 0;
    std::uint64_t originCalls = 0;
};

HRESULT MatrixDispatch::GetIDsOfNames(
    REFIID iid, LPOLESTR* names, UINT count, LCID, DISPID* ids) noexcept {
    if (iid != IID_NULL || names == nullptr || ids == nullptr || count != 1) {
        return E_INVALIDARG;
    }
    ++fixture_->dispatchCalls;
    requestedName_ = names[0];
    if (std::find(
            fixture_->components.begin() + 1,
            fixture_->components.end(), requestedName_) !=
        fixture_->components.end()) {
        ++fixture_->rulePathCalls;
    }
    ids[0] = 1;
    if (role_ == MatrixDispatchRole::Root) {
        if (requestedName_ == L"HAction" ||
            requestedName_ == L"HParameterSet" ||
            requestedName_ == L"BrushType") return S_OK;
        if (requestedName_ == L"PropertyOrigin") {
            return fixture_->originLane == OriginLane::Missing
                ? DISP_E_UNKNOWNNAME : S_OK;
        }
        if (fixture_->components.size() > 1 &&
            requestedName_ == fixture_->components[1]) {
            return fixture_->components.size() == 2 &&
                    fixture_->lane == MatrixLane::NotExposed
                ? DISP_E_UNKNOWNNAME : S_OK;
        }
        if (fixture_->rule.sourceContract ==
                hancom::graph::PropertySourceContract::LowHighUint32) {
            const wchar_t* const low = std::wcsrchr(
                fixture_->rule.nativeLowMemberPath, L'.');
            const wchar_t* const high = std::wcsrchr(
                fixture_->rule.nativeHighMemberPath, L'.');
            if ((low != nullptr && requestedName_ == low + 1) ||
                (high != nullptr && requestedName_ == high + 1)) {
                return fixture_->lane == MatrixLane::NotExposed
                    ? DISP_E_UNKNOWNNAME : S_OK;
            }
        }
        return DISP_E_UNKNOWNNAME;
    }
    if (role_ == MatrixDispatchRole::Action) {
        return requestedName_ == L"GetDefault" ? S_OK : DISP_E_UNKNOWNNAME;
    }
    if (role_ == MatrixDispatchRole::ParameterSets) {
        return S_OK;
    }
    if (requestedName_ == L"HSet" || requestedName_ == L"TabDef" ||
        requestedName_ == L"PageDef" ||
        requestedName_ == L"SelCellsBorderFill" ||
        (requestedName_ == L"Type" &&
         hancom::graph::HasQualifier(
             fixture_->rule.qualifiers,
             hancom::graph::QualifierFlags::WindowsBrushFillCondition))) {
        return S_OK;
    }
    if (pathDepth_ < fixture_->components.size() &&
        requestedName_ == fixture_->components[pathDepth_]) {
        return pathDepth_ + 1 == fixture_->components.size() &&
                fixture_->lane == MatrixLane::NotExposed
            ? DISP_E_UNKNOWNNAME : S_OK;
    }
    return DISP_E_UNKNOWNNAME;
}

HRESULT MatrixDispatch::Invoke(
    DISPID member, REFIID iid, LCID, WORD flags,
    DISPPARAMS* parameters, VARIANT* result, EXCEPINFO*, UINT*) noexcept {
    if (iid != IID_NULL || member != 1 || parameters == nullptr) {
        return E_INVALIDARG;
    }
    ++fixture_->dispatchCalls;
    const auto dispatch = [result](IDispatch* value) noexcept {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_DISPATCH;
        result->pdispVal = value;
        value->AddRef();
        return S_OK;
    };
    if (role_ == MatrixDispatchRole::Root &&
        requestedName_ == L"HAction" && flags == DISPATCH_PROPERTYGET) {
        return dispatch(&fixture_->action);
    }
    if (role_ == MatrixDispatchRole::Root &&
        requestedName_ == L"HParameterSet" &&
        flags == DISPATCH_PROPERTYGET) {
        return dispatch(&fixture_->parameterSets);
    }
    if (role_ == MatrixDispatchRole::Root &&
        requestedName_ == L"PropertyOrigin" && flags == DISPATCH_METHOD) {
        ++fixture_->originCalls;
        if (result == nullptr || parameters->cArgs != 1 ||
            parameters->rgvarg[0].vt != VT_BSTR) {
            return DISP_E_TYPEMISMATCH;
        }
        if (fixture_->originLane == OriginLane::DispatchFailure) {
            return E_FAIL;
        }
        if (fixture_->originLane == OriginLane::InvalidVariant) {
            result->vt = VT_DISPATCH;
            result->pdispVal = &fixture_->root;
            result->pdispVal->AddRef();
            return S_OK;
        }
        result->vt = VT_I4;
        result->lVal = fixture_->originCode;
        return S_OK;
    }
    if (role_ == MatrixDispatchRole::Root &&
        requestedName_ == L"BrushType" && flags == DISPATCH_METHOD) {
        if (result == nullptr || parameters->cArgs != 1 ||
            parameters->rgvarg[0].vt != VT_BSTR ||
            std::wstring_view(parameters->rgvarg[0].bstrVal) != L"WinBrush") {
            return DISP_E_TYPEMISMATCH;
        }
        result->vt = VT_I4;
        result->lVal = 4;
        return S_OK;
    }
    if (role_ == MatrixDispatchRole::Root && flags == DISPATCH_PROPERTYGET &&
        fixture_->rule.sourceContract ==
            hancom::graph::PropertySourceContract::LowHighUint32) {
        const wchar_t* const low = std::wcsrchr(
            fixture_->rule.nativeLowMemberPath, L'.');
        const wchar_t* const high = std::wcsrchr(
            fixture_->rule.nativeHighMemberPath, L'.');
        if ((low != nullptr && requestedName_ == low + 1) ||
            (high != nullptr && requestedName_ == high + 1)) {
            if (fixture_->lane == MatrixLane::ReadFailed) return E_FAIL;
            if (result == nullptr) return E_POINTER;
            result->vt = VT_UI4;
            result->ulVal = requestedName_.find(L"High") == std::wstring::npos
                ? 0x89abcdefUL : 0x12345678UL;
            return S_OK;
        }
    }
    if (role_ == MatrixDispatchRole::Root &&
        flags == DISPATCH_PROPERTYGET && fixture_->components.size() > 1 &&
        requestedName_ == fixture_->components[1]) {
        if (fixture_->components.size() == 2) {
            return fixture_->lane == MatrixLane::ReadFailed
                ? E_FAIL : fixture_->NativeValue(result);
        }
        fixture_->nested.pathDepth_ = 2;
        return dispatch(&fixture_->nested);
    }
    if (role_ == MatrixDispatchRole::Action &&
        requestedName_ == L"GetDefault" && flags == DISPATCH_METHOD) {
        ++fixture_->preparationCalls;
        return S_OK;
    }
    if (role_ == MatrixDispatchRole::ParameterSets &&
        flags == DISPATCH_PROPERTYGET) {
        if (requestedName_ == fixture_->ExpectedParameterName()) {
            ++fixture_->owningProfileCalls;
            return dispatch(&fixture_->parameter);
        }
        ++fixture_->forbiddenProfileCalls;
        return dispatch(&fixture_->wrongParameter);
    }
    if ((role_ == MatrixDispatchRole::Parameter ||
         role_ == MatrixDispatchRole::Nested) &&
        requestedName_ == L"HSet" && flags == DISPATCH_PROPERTYGET) {
        return dispatch(this);
    }
    if ((role_ == MatrixDispatchRole::Parameter ||
         role_ == MatrixDispatchRole::Nested) && requestedName_ == L"Type" &&
        flags == DISPATCH_PROPERTYGET && hancom::graph::HasQualifier(
            fixture_->rule.qualifiers,
            hancom::graph::QualifierFlags::WindowsBrushFillCondition)) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_I4;
        result->lVal = 4;
        return S_OK;
    }
    if ((role_ == MatrixDispatchRole::Parameter ||
         role_ == MatrixDispatchRole::Nested) &&
        (requestedName_ == L"TabDef" || requestedName_ == L"PageDef" ||
         requestedName_ == L"SelCellsBorderFill") &&
        flags == DISPATCH_PROPERTYGET) {
        if (requestedName_ != fixture_->ExpectedOwnerMember()) {
            ++fixture_->forbiddenProfileCalls;
            return dispatch(&fixture_->wrongParameter);
        }
        ++fixture_->owningProfileCalls;
        fixture_->owner.pathDepth_ = 1;
        return dispatch(&fixture_->owner);
    }
    if ((role_ == MatrixDispatchRole::Parameter ||
         role_ == MatrixDispatchRole::Nested) &&
        flags == DISPATCH_PROPERTYGET &&
        pathDepth_ < fixture_->components.size() &&
        requestedName_ == fixture_->components[pathDepth_]) {
        if (pathDepth_ + 1 == fixture_->components.size()) {
            const bool forbiddenOwner = forbiddenProfile_ ||
                (role_ == MatrixDispatchRole::Parameter &&
                 !fixture_->ExpectedOwnerMember().empty());
            if (forbiddenOwner) ++fixture_->forbiddenProfileCalls;
            if (fixture_->lane == MatrixLane::MemberAbsent &&
                !forbiddenOwner) {
                return DISP_E_MEMBERNOTFOUND;
            }
            return fixture_->lane == MatrixLane::ReadFailed
                ? E_FAIL : fixture_->NativeValue(result, forbiddenOwner);
        }
        fixture_->nested.pathDepth_ = pathDepth_ + 1;
        return dispatch(&fixture_->nested);
    }
    return DISP_E_MEMBERNOTFOUND;
}

bool ComRuleSupported(const PropertyRule& rule) noexcept {
    if (rule.valueShape != PropertyValueShape::Scalar) return false;
    switch (rule.applicabilitySet) {
    case PropertyApplicabilitySet::CharacterShape:
    case PropertyApplicabilitySet::ParagraphShape:
    case PropertyApplicabilitySet::TabDefDefinition:
    case PropertyApplicabilitySet::PageDefDefinition:
    case PropertyApplicabilitySet::ColumnDefDefinition:
    case PropertyApplicabilitySet::BorderFillDefinition:
        return true;
    default:
        return false;
    }
}

class MatrixSchemaSource final : public EffectivePropertySource {
public:
    MatrixSchemaSource(
        const hancom::graph::PropertyKeyId keyValue,
        const ReadStatus statusValue) noexcept
        : key(keyValue), status(statusValue) {}

    ReadStatus Read(
        const EffectivePropertyContext&,
        const PropertyRule& rule,
        PropertyObservation* observation) noexcept override {
        if (rule.id != key) return ReadStatus::NotExposed;
        if (status == ReadStatus::Value) {
            observation->canonicalValue = L"schema:" + std::to_wstring(key);
        }
        return status;
    }

    hancom::graph::PropertyKeyId key;
    ReadStatus status;
};

bool ProductionBoundaryRegistryValueMatrixSmoke(
    const bool windowsBrushQualification) {
    std::uint64_t valueCount = 0;
    std::uint64_t notExposedCount = 0;
    std::uint64_t readFailedCount = 0;
    std::uint64_t notApplicableCount = 0;
    std::uint64_t expectedSupportedCount = 0;
    std::vector<hancom::graph::PropertyKeyId> covered;
    bool exactContext = true;
    bool terminalClassification = true;
    bool unsupportedZeroDispatch = true;
    bool unsupportedWholeContextZeroDispatch = true;
    for (const PropertyRule& rule : kPropertyRegistryV1) {
        if (rule.id == 7104) continue;
        const bool supported = ComRuleSupported(rule);
        if (supported) ++expectedSupportedCount;
        const auto* applicability =
            hancom::graph::FindPropertyApplicabilityRule(
                rule.applicabilitySet);
        const hancom::graph::FieldTag expectedOwner =
            applicability == nullptr || applicability->tupleCount == 0
            ? 0 : applicability->tuples[0].ownerNodeFieldTag;
        for (const MatrixLane lane : {MatrixLane::Value,
                                     MatrixLane::NotExposed,
                                     MatrixLane::ReadFailed}) {
            if (!supported && lane != MatrixLane::Value) continue;
            MatrixDispatchFixture fixture(rule, lane);
            const EffectivePropertyContext context = fixture.CompatibleContext();
            TransactionalSink sink;
            CaptureDiagnostics diagnostics;
            const CaptureStatus capture =
                hancom::graph::properties::CaptureCurrentEffectiveProperties(
                    &fixture.root, context, sink, &diagnostics);
            const auto found = std::find_if(
                sink.published.begin(), sink.published.end(),
                [&rule](const auto& observation) {
                    return observation.key == rule.id;
                });
            const ReadStatus expected = !supported
                ? ReadStatus::NotExposed
                : lane == MatrixLane::Value
                ? ReadStatus::Value
                : lane == MatrixLane::NotExposed
                ? ReadStatus::NotExposed : ReadStatus::ReadFailed;
            const bool exact = capture == CaptureStatus::Complete &&
                found != sink.published.end() && found->status == expected &&
                found->target == context.target &&
                found->targetIdentity == context.targetIdentity &&
                found->ownerField == expectedOwner &&
                found->origin == (expected == ReadStatus::Value
                    ? PropertyOrigin::UserOverride
                    : PropertyOrigin::Unavailable) &&
                fixture.originCalls ==
                    (expected == ReadStatus::Value ? 1u : 0u) &&
                (expected == ReadStatus::Value) ==
                    !found->canonicalValue.empty();
            exactContext = exactContext && exact;
            terminalClassification = terminalClassification && exact;
            if (!supported) {
                unsupportedZeroDispatch = unsupportedZeroDispatch &&
                    fixture.rulePathCalls == 0;
                bool hasSupportedSibling = false;
                if (applicability != nullptr && applicability->tupleCount != 0) {
                    const auto& activeTuple = applicability->tuples[0];
                    for (const PropertyRule& candidate : kPropertyRegistryV1) {
                        if (!ComRuleSupported(candidate)) continue;
                        const auto* candidateApplicability =
                            hancom::graph::FindPropertyApplicabilityRule(
                                candidate.applicabilitySet);
                        if (candidateApplicability == nullptr) continue;
                        for (size_t index = 0;
                             index < candidateApplicability->tupleCount;
                             ++index) {
                            const auto& candidateTuple =
                                candidateApplicability->tuples[index];
                            if (candidateTuple.node != activeTuple.node) continue;
                            if (candidateTuple.definitionRestricted !=
                                activeTuple.definitionRestricted) continue;
                            if (candidateTuple.definitionRestricted &&
                                candidateTuple.definitionKind !=
                                    activeTuple.definitionKind) continue;
                            hasSupportedSibling = true;
                        }
                    }
                }
                if (!hasSupportedSibling) {
                    unsupportedWholeContextZeroDispatch =
                        unsupportedWholeContextZeroDispatch &&
                        fixture.dispatchCalls == 0 &&
                        fixture.preparationCalls == 0;
                }
            }
            if (!exact) continue;
            if (expected == ReadStatus::Value) {
                ++valueCount;
                covered.push_back(rule.id);
            } else if (expected == ReadStatus::NotExposed) {
                ++notExposedCount;
            } else {
                ++readFailedCount;
            }
        }
    }
    MatrixDispatchFixture incompatible(kPropertyRegistryV1.front());
    TransactionalSink incompatibleSink;
    CaptureDiagnostics incompatibleDiagnostics;
    const EffectivePropertyContext incompatibleContext{
        hancom::graph::capture::PropertyTarget::Definition,
        L"matrix:incompatible", false};
    bool registryProvesIncompatible = true;
    for (const PropertyRule& rule : kPropertyRegistryV1) {
        const auto* applicability =
            hancom::graph::FindPropertyApplicabilityRule(rule.applicabilitySet);
        bool appliesWithoutDefinitionKind = false;
        if (applicability != nullptr) {
            for (size_t index = 0; index < applicability->tupleCount; ++index) {
                const auto& tuple = applicability->tuples[index];
                appliesWithoutDefinitionKind = appliesWithoutDefinitionKind ||
                    (tuple.node == hancom::graph::NodeKind::Definition &&
                     !tuple.definitionRestricted);
            }
        }
        registryProvesIncompatible =
            registryProvesIncompatible && !appliesWithoutDefinitionKind;
    }
    const bool zeroDispatchIncompatible = registryProvesIncompatible &&
        hancom::graph::properties::CaptureCurrentEffectiveProperties(
            &incompatible.root, incompatibleContext, incompatibleSink,
            &incompatibleDiagnostics) == CaptureStatus::Complete &&
        incompatible.dispatchCalls == 0 &&
        incompatible.preparationCalls == 0 &&
        incompatibleDiagnostics.notApplicableCount ==
            kPropertyRegistryV1.size();
    for (const PropertyObservation& observation : incompatibleSink.published) {
        if (observation.status == ReadStatus::NotApplicable &&
            observation.target == incompatibleContext.target &&
            observation.targetIdentity == incompatibleContext.targetIdentity &&
            observation.ownerField == 0 &&
            observation.origin == PropertyOrigin::NotApplicable &&
            observation.canonicalValue.empty()) {
            ++notApplicableCount;
        }
    }
    std::uint64_t schemaLaneCount = 0;
    bool schemaFourLanes = true;
    for (const PropertyRule& rule : kPropertyRegistryV1) {
        MatrixDispatchFixture contextFixture(rule);
        const EffectivePropertyContext context =
            contextFixture.CompatibleContext();
        const auto* schemaApplicability =
            hancom::graph::FindPropertyApplicabilityRule(
                rule.applicabilitySet);
        const hancom::graph::FieldTag schemaExpectedOwner =
            schemaApplicability == nullptr ||
                schemaApplicability->tupleCount == 0
            ? 0 : schemaApplicability->tuples[0].ownerNodeFieldTag;
        for (const ReadStatus expected : {ReadStatus::Value,
                                         ReadStatus::NotApplicable,
                                         ReadStatus::NotExposed,
                                         ReadStatus::ReadFailed}) {
            MatrixSchemaSource source(rule.id, expected);
            TransactionalSink sink;
            CaptureDiagnostics diagnostics;
            const CaptureStatus capture = CaptureEffectiveProperties(
                context, source, sink, &diagnostics);
            const auto found = std::find_if(
                sink.published.begin(), sink.published.end(),
                [&rule](const auto& observation) {
                    return observation.key == rule.id;
                });
            const bool exact = capture == CaptureStatus::Complete &&
                found != sink.published.end() && found->status == expected &&
                found->target == context.target &&
                found->targetIdentity == context.targetIdentity &&
                found->ownerField == schemaExpectedOwner &&
                found->origin == (expected == ReadStatus::NotApplicable
                    ? PropertyOrigin::NotApplicable
                    : PropertyOrigin::Unavailable) &&
                (expected == ReadStatus::Value) ==
                    !found->canonicalValue.empty();
            schemaFourLanes = schemaFourLanes && exact;
            if (exact) ++schemaLaneCount;
        }
    }
    schemaFourLanes = schemaFourLanes &&
        schemaLaneCount == kPropertyRegistryV1.size() * 4;
    std::sort(covered.begin(), covered.end());
    constexpr size_t authoritativeRegistryCardinality = 165;
    const bool cardinality =
        kPropertyRegistryV1.size() == authoritativeRegistryCardinality;
    const bool uniqueNoSkip = covered.size() == expectedSupportedCount &&
        std::adjacent_find(covered.begin(), covered.end()) == covered.end();
    const bool allValues = valueCount == expectedSupportedCount;
    const bool allNotExposed =
        notExposedCount == kPropertyRegistryV1.size() - 1;
    const bool allReadFailed = readFailedCount == expectedSupportedCount;
    const bool allNotApplicable =
        notApplicableCount == kPropertyRegistryV1.size();
    const bool windowsBrushQualifiedLane = windowsBrushQualification &&
        std::find(covered.begin(), covered.end(),
                  hancom::graph::PropertyKeyId{5015}) != covered.end() &&
        notExposedCount == kPropertyRegistryV1.size() - 1 &&
        readFailedCount == expectedSupportedCount;
    const bool matrix = cardinality && uniqueNoSkip && allValues &&
        allNotExposed && allReadFailed && allNotApplicable && exactContext &&
        terminalClassification && zeroDispatchIncompatible &&
        unsupportedZeroDispatch && unsupportedWholeContextZeroDispatch &&
        windowsBrushQualifiedLane && schemaFourLanes;
    std::wcout << L"EFFECTIVE_PROPERTIES_MATRIX_REGISTRY_COUNT "
               << cardinality << L" actual=" << kPropertyRegistryV1.size()
               << L" expected=" << authoritativeRegistryCardinality << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_VALUE_COUNT "
               << allValues << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_NOT_APPLICABLE_COUNT "
               << allNotApplicable << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_NOT_EXPOSED_COUNT "
               << allNotExposed << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_READ_FAILED_COUNT "
               << allReadFailed << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_CONTEXT_ORIGIN_OWNER "
               << exactContext << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_ZERO_DISPATCH_INCOMPATIBLE "
               << zeroDispatchIncompatible << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_UNSUPPORTED_ZERO_DISPATCH "
               << unsupportedZeroDispatch << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_UNSUPPORTED_CONTEXT_ZERO_DISPATCH "
               << unsupportedWholeContextZeroDispatch << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_SCHEMA_FOUR_LANES "
               << schemaFourLanes << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_TERMINAL_CLASSIFICATION "
               << terminalClassification << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_WINDOWS_BRUSH_QUALIFIED "
               << windowsBrushQualifiedLane << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_UNIQUE_NO_SKIP "
               << uniqueNoSkip << L'\n'
               << L"EFFECTIVE_PROPERTIES_MATRIX_OVERALL " << matrix << L'\n';
    return matrix;
}

bool DefinitionOwningProfileRoutingSmoke() {
    const auto expectedValue = [](const PropertyRule& rule) {
        const unsigned long long sentinel =
            static_cast<unsigned long long>(rule.id) * 100 + 1;
        switch (rule.scalar) {
        case hancom::graph::ScalarTag::Bool: return std::wstring(L"b:1");
        case hancom::graph::ScalarTag::UTF16:
            return L"s:" + std::wstring(rule.nativeMemberPath);
        case hancom::graph::ScalarTag::Float64:
            return std::wstring(L"f:1.25");
        case hancom::graph::ScalarTag::Uint8:
            return L"u:" + std::to_wstring(sentinel & 0xff);
        case hancom::graph::ScalarTag::Uint16:
            return L"u:" + std::to_wstring(sentinel & 0xffff);
        case hancom::graph::ScalarTag::Uint32:
        case hancom::graph::ScalarTag::BGR:
        case hancom::graph::ScalarTag::RawURC32:
        case hancom::graph::ScalarTag::Uint64:
            return L"u:" + std::to_wstring(sentinel);
        default:
            return L"i:" + std::to_wstring(sentinel);
        }
    };
    bool exact = true;
    std::uint64_t checked = 0;
    std::vector<std::wstring> ownerIdentities;
    std::vector<hancom::graph::PropertyKeyId> checkedKeys;
    for (const PropertyRule& rule : kPropertyRegistryV1) {
        if (!ComRuleSupported(rule)) continue;
        const auto* applicability =
            hancom::graph::FindPropertyApplicabilityRule(
                rule.applicabilitySet);
        if (applicability == nullptr) return false;
        const auto owner = std::find_if(
            applicability->tuples.begin(),
            applicability->tuples.begin() + applicability->tupleCount,
            [](const auto& tuple) {
                return tuple.node == hancom::graph::NodeKind::Definition &&
                    tuple.definitionRestricted;
            });
        if (owner == applicability->tuples.begin() +
                applicability->tupleCount) {
            continue;
        }
        MatrixDispatchFixture fixture(rule);
        const std::wstring identity =
            L"definition-owner:" + std::to_wstring(checked);
        const EffectivePropertyContext context{
            hancom::graph::capture::PropertyTarget::Definition,
            identity, true, owner->definitionKind};
        TransactionalSink sink;
        CaptureDiagnostics diagnostics;
        const CaptureStatus status =
            hancom::graph::properties::CaptureCurrentEffectiveProperties(
                &fixture.root, context, sink, &diagnostics);
        const auto found = std::find_if(
            sink.published.begin(), sink.published.end(),
            [&rule](const PropertyObservation& observation) {
                return observation.key == rule.id;
            });
        const bool nestedOwner =
            rule.applicabilitySet == PropertyApplicabilitySet::TabDefDefinition ||
            rule.applicabilitySet == PropertyApplicabilitySet::PageDefDefinition ||
            rule.applicabilitySet == PropertyApplicabilitySet::BorderFillDefinition;
        exact = exact && status == CaptureStatus::Complete &&
            found != sink.published.end() &&
            found->status == ReadStatus::Value &&
            found->scalar == rule.scalar &&
            found->canonicalValue == expectedValue(rule) &&
            found->target ==
                hancom::graph::capture::PropertyTarget::Definition &&
            found->targetIdentity == identity && found->ownerField == 102 &&
            found->origin == PropertyOrigin::UserOverride &&
            fixture.owningProfileCalls == (nestedOwner ? 2u : 1u) &&
            fixture.forbiddenProfileCalls == 0;
        ownerIdentities.push_back(identity);
        checkedKeys.push_back(rule.id);
        ++checked;
    }
    const bool noCrossObjectLeakage =
        std::adjacent_find(
            ownerIdentities.begin(), ownerIdentities.end()) ==
        ownerIdentities.end() &&
        std::adjacent_find(checkedKeys.begin(), checkedKeys.end()) ==
        checkedKeys.end();
    const auto* const numberingArray =
        hancom::graph::FindPropertyRule(4001);
    const auto* const tabArray = hancom::graph::FindPropertyRule(6002);
    const auto* const widthGap = hancom::graph::FindPropertyRule(7104);
    const bool unavailableArraysSeparate = numberingArray != nullptr &&
        tabArray != nullptr && hancom::graph::FindPropertyRule(7103) == nullptr &&
        widthGap != nullptr && !ComRuleSupported(*numberingArray) &&
        !ComRuleSupported(*tabArray) && !ComRuleSupported(*widthGap);
    std::wcout << L"DEFINITION_OWNING_PROFILE_EXACT_IDS_VALUES "
               << exact << L" count=" << checked << L'\n'
               << L"DEFINITION_OWNING_PROFILE_FORBIDDEN_CALLS_ZERO "
               << exact << L'\n'
               << L"DEFINITION_OWNING_PROFILE_NO_CROSS_OBJECT_LEAKAGE "
               << noCrossObjectLeakage << L'\n'
               << L"DEFINITION_OWNING_PROFILE_UNAVAILABLE_ARRAYS_DISTINCT "
               << unavailableArraysSeparate << L'\n';
    return exact && noCrossObjectLeakage && unavailableArraysSeparate;
}

bool Withdrawn7103Unavailable7104RegressionSmoke() {
    const PropertyRule* const widthGap = FindPropertyRule(7104);
    if (widthGap == nullptr || FindPropertyRule(7103) != nullptr ||
        std::find(kWithdrawnPropertyKeyIdsV1.begin(),
                  kWithdrawnPropertyKeyIdsV1.end(),
                  hancom::graph::PropertyKeyId{7103}) ==
            kWithdrawnPropertyKeyIdsV1.end()) {
        return false;
    }

    MatrixDispatchFixture unavailable(*widthGap, MatrixLane::MemberAbsent);
    const EffectivePropertyContext unavailableContext{
        hancom::graph::capture::PropertyTarget::Definition,
        L"column-definition:unavailable", true,
        hancom::graph::DefinitionKind::ColumnDef};
    PropertyObservation terminal;
    const ReadStatus unavailableStatus =
        hancom::graph::properties::ReadCurrentEffectiveProperty(
            &unavailable.root, unavailableContext, *widthGap, &terminal);
    const bool withdrawnSkipped = FindPropertyRule(7103) == nullptr &&
        unavailable.dispatchCalls != 0;
    const bool widthGapTerminal =
        unavailableStatus == ReadStatus::NotExposed &&
        terminal.key == 7104 &&
        terminal.scalar == hancom::graph::ScalarTag::Uint16 &&
        terminal.status == ReadStatus::NotExposed &&
        terminal.canonicalValue.empty() &&
        terminal.origin == PropertyOrigin::Unavailable &&
        terminal.target ==
            hancom::graph::capture::PropertyTarget::Definition &&
        terminal.targetIdentity == unavailableContext.targetIdentity &&
        terminal.ownerField == 102;
    const bool exactOwnerRoute = unavailable.owningProfileCalls == 1 &&
        unavailable.preparationCalls == 1 &&
        unavailable.forbiddenProfileCalls == 0 &&
        unavailable.rulePathCalls == 1;

    const auto terminalCapture = [widthGap](
        const MatrixLane lane, const wchar_t* const identity,
        const ReadStatus expected) {
        MatrixDispatchFixture fixture(*widthGap, lane);
        const EffectivePropertyContext context{
            hancom::graph::capture::PropertyTarget::Definition,
            identity, true, hancom::graph::DefinitionKind::ColumnDef};
        PropertyObservation property;
        const ReadStatus status =
            hancom::graph::properties::ReadCurrentEffectiveProperty(
                &fixture.root, context, *widthGap, &property);
        return status == expected && property.status == expected &&
            property.canonicalValue.empty() &&
            property.origin == PropertyOrigin::Unavailable &&
            property.targetIdentity == identity &&
            property.ownerField == 102 &&
            fixture.owningProfileCalls == 1 &&
            fixture.rulePathCalls == 1 &&
            fixture.forbiddenProfileCalls == 0;
    };
    const bool otherFailureReadFailed = terminalCapture(
        MatrixLane::ReadFailed, L"column-definition:hresult-failure",
        ReadStatus::ReadFailed);
    const bool exposedUnsupportedReadFailed = terminalCapture(
        MatrixLane::Value, L"column-definition:exposed-unsupported",
        ReadStatus::ReadFailed);

    MatrixDispatchFixture zero(
        *FindPropertyRule(7100), MatrixLane::Value,
        OriginLane::Value, 0, true);
    TransactionalSink zeroSink;
    CaptureDiagnostics zeroDiagnostics;
    const EffectivePropertyContext zeroContext{
        hancom::graph::capture::PropertyTarget::Definition,
        L"column-definition:zero", true,
        hancom::graph::DefinitionKind::ColumnDef};
    const CaptureStatus zeroStatus =
        hancom::graph::properties::CaptureCurrentEffectiveProperties(
            &zero.root, zeroContext, zeroSink, &zeroDiagnostics);
    const auto validZero = std::find_if(
        zeroSink.published.begin(), zeroSink.published.end(),
        [](const PropertyObservation& property) {
            return property.key == 7100;
        });
    const auto secondTerminal = std::find_if(
        zeroSink.published.begin(), zeroSink.published.end(),
        [](const PropertyObservation& property) {
            return property.key == 7104;
        });
    const bool noStaleOrZeroCollapse = zeroStatus == CaptureStatus::Complete &&
        validZero != zeroSink.published.end() &&
        validZero->status == ReadStatus::Value &&
        validZero->canonicalValue == L"u:0" &&
        validZero->targetIdentity == zeroContext.targetIdentity &&
        secondTerminal != zeroSink.published.end() &&
        secondTerminal->status == ReadStatus::NotExposed &&
        secondTerminal->canonicalValue.empty() &&
        secondTerminal->targetIdentity == zeroContext.targetIdentity &&
        terminal.targetIdentity != secondTerminal->targetIdentity;

    std::wcout
        << L"PROPERTY_7103_WITHDRAWN_ZERO_OWNER_CALLS "
        << withdrawnSkipped << L" owner=0 forbidden=0 records=0\n"
        << L"PROPERTY_7104_OWNER_MEMBERNOTFOUND_NOTEXPOSED "
        << (widthGapTerminal && exactOwnerRoute)
        << L" owner=" << unavailable.owningProfileCalls
        << L" member=" << unavailable.rulePathCalls
        << L" forbidden=" << unavailable.forbiddenProfileCalls << L'\n'
        << L"PROPERTY_7104_OTHER_HRESULT_READFAILED "
        << otherFailureReadFailed << L" owner=1 member=1 forbidden=0\n"
        << L"PROPERTY_7104_EXPOSED_UNSUPPORTED_READFAILED "
        << exposedUnsupportedReadFailed
        << L" owner=1 member=1 forbidden=0 value_bytes=0\n"
        << L"PROPERTY_7104_TARGET_ORIGIN_TYPED " << widthGapTerminal << L'\n'
        << L"PROPERTY_7104_NO_STALE_ZERO_COLLAPSE "
        << noStaleOrZeroCollapse << L" valid_zero=u:0\n";
    return withdrawnSkipped && widthGapTerminal && exactOwnerRoute &&
        otherFailureReadFailed && exposedUnsupportedReadFailed &&
        noStaleOrZeroCollapse;
}

bool BulletHeadingType3FakeIDispatchMatrixSmoke();

bool PropertyOriginFakeIDispatchMatrixSmoke() {
    const auto rule = std::find_if(
        kPropertyRegistryV1.begin(), kPropertyRegistryV1.end(),
        [](const PropertyRule& candidate) {
            return candidate.valueShape == PropertyValueShape::Scalar &&
                candidate.applicabilitySet ==
                    PropertyApplicabilitySet::ParagraphShape;
        });
    if (rule == kPropertyRegistryV1.end()) return false;

    const EffectivePropertyContext paragraph{
        hancom::graph::capture::PropertyTarget::Paragraph,
        L"paragraph:origin-matrix"};
    const auto capture = [rule](
        const OriginLane lane, const LONG code,
        const EffectivePropertyContext& context,
        PropertyObservation* const output,
        std::uint64_t* const originCalls) {
        MatrixDispatchFixture fixture(*rule, MatrixLane::Value, lane, code);
        TransactionalSink sink;
        CaptureDiagnostics diagnostics;
        const CaptureStatus status =
            hancom::graph::properties::CaptureCurrentEffectiveProperties(
                &fixture.root, context, sink, &diagnostics);
        const auto found = std::find_if(
            sink.published.begin(), sink.published.end(),
            [rule](const PropertyObservation& observation) {
                return observation.key == rule->id;
            });
        if (originCalls != nullptr) *originCalls = fixture.originCalls;
        if (status != CaptureStatus::Complete ||
            found == sink.published.end()) return false;
        *output = *found;
        return true;
    };

    const std::array<PropertyOrigin, 6> typedOrigins{
        PropertyOrigin::UserOverride,
        PropertyOrigin::LocalStyle,
        PropertyOrigin::NamedStyle,
        PropertyOrigin::DocumentDefault,
        PropertyOrigin::ImplicitDefault,
        PropertyOrigin::Inherited,
    };
    bool typed = true;
    for (LONG code = 0; code < static_cast<LONG>(typedOrigins.size()); ++code) {
        PropertyObservation observation;
        std::uint64_t calls = 0;
        typed = typed && capture(
            OriginLane::Value, code, paragraph, &observation, &calls) &&
            observation.status == ReadStatus::Value &&
            observation.origin == typedOrigins[static_cast<size_t>(code)] &&
            !observation.canonicalValue.empty() && calls == 1;
    }

    bool unavailable = true;
    for (const OriginLane lane : {OriginLane::Missing,
                                  OriginLane::DispatchFailure,
                                  OriginLane::InvalidVariant}) {
        PropertyObservation observation;
        std::uint64_t calls = 0;
        unavailable = unavailable && capture(
            lane, 0, paragraph, &observation, &calls) &&
            observation.status == ReadStatus::Value &&
            observation.origin == PropertyOrigin::Unavailable &&
            !observation.canonicalValue.empty() &&
            (lane == OriginLane::Missing ? calls == 0 : calls == 1);
    }

    const EffectivePropertyContext cell{
        hancom::graph::capture::PropertyTarget::Cell, L"cell:origin-matrix"};
    const EffectivePropertyContext control{
        hancom::graph::capture::PropertyTarget::Control,
        L"control:origin-matrix"};
    PropertyObservation cellObservation;
    PropertyObservation controlObservation;
    std::uint64_t cellCalls = 0;
    std::uint64_t controlCalls = 0;
    const bool isolated =
        capture(OriginLane::Value, 1, cell,
                &cellObservation, &cellCalls) &&
        capture(OriginLane::Value, 2, control,
                &controlObservation, &controlCalls) &&
        cellObservation.status == ReadStatus::NotApplicable &&
        cellObservation.origin == PropertyOrigin::NotApplicable &&
        cellObservation.canonicalValue.empty() &&
        cellObservation.targetIdentity == cell.targetIdentity &&
        controlObservation.status == ReadStatus::NotApplicable &&
        controlObservation.origin == PropertyOrigin::NotApplicable &&
        controlObservation.canonicalValue.empty() &&
        controlObservation.targetIdentity == control.targetIdentity &&
        cellCalls == 0 && controlCalls == 0;
    return typed && unavailable && isolated;
}

enum class BrushDispatchRole : std::uint8_t {
    Root,
    Action,
    ParameterSets,
    BorderFill,
    SelectedBorderFill,
    FillAttr,
};

class BrushDispatch final : public IDispatch {
public:
    explicit BrushDispatch(const BrushDispatchRole value) noexcept
        : role(value) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid, void** object) noexcept override {
        if (object == nullptr) return E_POINTER;
        *object = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDispatch) {
            return E_NOINTERFACE;
        }
        *object = static_cast<IDispatch*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() noexcept override { return 2; }
    ULONG STDMETHODCALLTYPE Release() noexcept override { return 1; }
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* count) noexcept override {
        if (count == nullptr) return E_POINTER;
        *count = 0;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfo(
        UINT, LCID, ITypeInfo**) noexcept override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID iid, LPOLESTR* names, UINT count, LCID, DISPID* ids)
        noexcept override {
        if (iid != IID_NULL || names == nullptr || ids == nullptr ||
            count != 1) {
            return E_INVALIDARG;
        }
        const std::wstring_view name(names[0]);
        if (name == L"HAction") ids[0] = 1;
        else if (name == L"HParameterSet") ids[0] = 2;
        else if (name == L"BrushType") {
            ++brushTypeRequests;
            if (FAILED(brushTypeNameStatus)) return brushTypeNameStatus;
            ids[0] = 3;
        }
        else if (name == L"GetDefault") ids[0] = 4;
        else if (name == L"HCellBorderFill") ids[0] = 5;
        else if (name == L"HSet") ids[0] = 6;
        else if (name == L"SelCellsBorderFill") ids[0] = 10;
        else if (name == L"FillAttr") ids[0] = 7;
        else if (name == L"Type") {
            ++typeRequests;
            if (typeStatus == DISP_E_UNKNOWNNAME) return typeStatus;
            ids[0] = 8;
        } else if (name == L"WindowsBrush") {
            ++windowsBrushRequests;
            if (windowsBrushStatus == DISP_E_UNKNOWNNAME) {
                return windowsBrushStatus;
            }
            ids[0] = 9;
        } else {
            return DISP_E_UNKNOWNNAME;
        }
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID member, REFIID iid, LCID, WORD flags,
        DISPPARAMS* parameters, VARIANT* result, EXCEPINFO*, UINT*)
        noexcept override {
        if (iid != IID_NULL || parameters == nullptr) return E_INVALIDARG;
        const auto dispatch = [result](IDispatch* value) noexcept {
            if (result == nullptr) return E_POINTER;
            result->vt = VT_DISPATCH;
            result->pdispVal = value;
            value->AddRef();
            return S_OK;
        };
        if (role == BrushDispatchRole::Root && member == 1 &&
            flags == DISPATCH_PROPERTYGET) return dispatch(action);
        if (role == BrushDispatchRole::Root && member == 2 &&
            flags == DISPATCH_PROPERTYGET) return dispatch(parameterSets);
        if (role == BrushDispatchRole::Root && member == 3 &&
            flags == DISPATCH_METHOD) {
            ++brushTypeCalls;
            brushTypeCallShape = parameters->cArgs == 1 &&
                parameters->rgvarg[0].vt == VT_BSTR &&
                std::wstring_view(parameters->rgvarg[0].bstrVal) ==
                    L"WinBrush";
            if (FAILED(brushTypeStatus)) return brushTypeStatus;
            if (result == nullptr) return E_POINTER;
            if (brushTypeConversionFailure) {
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"not-a-mask");
                return result->bstrVal == nullptr ? E_OUTOFMEMORY : S_OK;
            }
            result->vt = VT_I4;
            result->lVal = winBrushBit;
            return S_OK;
        }
        if (role == BrushDispatchRole::Action && member == 4 &&
            flags == DISPATCH_METHOD) return S_OK;
        if (role == BrushDispatchRole::ParameterSets && member == 5 &&
            flags == DISPATCH_PROPERTYGET) return dispatch(borderFill);
        if (role == BrushDispatchRole::BorderFill && member == 6 &&
            flags == DISPATCH_PROPERTYGET) return dispatch(this);
        if (role == BrushDispatchRole::BorderFill && member == 10 &&
            flags == DISPATCH_PROPERTYGET) return dispatch(selectedBorderFill);
        if (role == BrushDispatchRole::SelectedBorderFill && member == 7 &&
            flags == DISPATCH_PROPERTYGET) return dispatch(fillAttr);
        if (role == BrushDispatchRole::FillAttr && member == 8 &&
            flags == DISPATCH_PROPERTYGET) {
            if (FAILED(typeStatus)) return typeStatus;
            if (result == nullptr) return E_POINTER;
            result->vt = VT_I4;
            result->lVal = fillType;
            return S_OK;
        }
        if (role == BrushDispatchRole::FillAttr && member == 9 &&
            flags == DISPATCH_PROPERTYGET) {
            if (FAILED(windowsBrushStatus)) return windowsBrushStatus;
            if (result == nullptr) return E_POINTER;
            result->vt = VT_UI1;
            result->bVal = windowsBrush;
            return S_OK;
        }
        return DISP_E_MEMBERNOTFOUND;
    }

    BrushDispatchRole role;
    BrushDispatch* action = nullptr;
    BrushDispatch* parameterSets = nullptr;
    BrushDispatch* borderFill = nullptr;
    BrushDispatch* selectedBorderFill = nullptr;
    BrushDispatch* fillAttr = nullptr;
    HRESULT brushTypeNameStatus = S_OK;
    HRESULT brushTypeStatus = S_OK;
    HRESULT typeStatus = S_OK;
    HRESULT windowsBrushStatus = S_OK;
    LONG winBrushBit = 4;
    LONG fillType = 4;
    BYTE windowsBrush = 0;
    unsigned brushTypeRequests = 0;
    unsigned brushTypeCalls = 0;
    unsigned typeRequests = 0;
    unsigned windowsBrushRequests = 0;
    bool brushTypeCallShape = false;
    bool brushTypeConversionFailure = false;
};

struct BrushFixture final {
    BrushFixture() noexcept {
        root.action = &action;
        root.parameterSets = &parameterSets;
        parameterSets.borderFill = &borderFill;
        borderFill.selectedBorderFill = &selectedBorderFill;
        selectedBorderFill.fillAttr = &fillAttr;
    }
    BrushDispatch root{BrushDispatchRole::Root};
    BrushDispatch action{BrushDispatchRole::Action};
    BrushDispatch parameterSets{BrushDispatchRole::ParameterSets};
    BrushDispatch borderFill{BrushDispatchRole::BorderFill};
    BrushDispatch selectedBorderFill{BrushDispatchRole::SelectedBorderFill};
    BrushDispatch fillAttr{BrushDispatchRole::FillAttr};
};

PropertyObservation BrushObservation(
    BrushFixture* fixture,
    const EffectivePropertyContext& context) {
    TransactionalSink sink;
    CaptureDiagnostics diagnostics;
    const CaptureStatus status =
        hancom::graph::properties::CaptureCurrentEffectiveProperties(
            &fixture->root, context, sink, &diagnostics);
    if (status != CaptureStatus::Complete) return {};
    const auto found = std::find_if(
        sink.published.begin(), sink.published.end(), [](const auto& item) {
            return item.key == 5015;
        });
    return found == sink.published.end() ? PropertyObservation{} : *found;
}

bool WindowsBrushQualificationSmoke() {
    const EffectivePropertyContext borderContext{
        hancom::graph::capture::PropertyTarget::Definition,
        L"border-fill:7", true, hancom::graph::DefinitionKind::BorderFill};

    BrushFixture included;
    included.root.winBrushBit = 4;
    included.fillAttr.fillType = 6;
    included.fillAttr.windowsBrush = 0xff;
    const PropertyObservation includedValue =
        BrushObservation(&included, borderContext);
    const bool valuePreserved =
        includedValue.status == ReadStatus::Value &&
        includedValue.scalar == hancom::graph::ScalarTag::Uint8 &&
        includedValue.canonicalValue == L"u:255" &&
        includedValue.ownerField == 102 &&
        includedValue.origin == PropertyOrigin::Unavailable &&
        included.root.brushTypeCalls == 1 &&
        included.root.brushTypeCallShape &&
        included.fillAttr.typeRequests == 1 &&
        included.fillAttr.windowsBrushRequests == 1;

    BrushFixture excluded;
    excluded.root.winBrushBit = 4;
    excluded.fillAttr.fillType = 2;
    const PropertyObservation excludedValue =
        BrushObservation(&excluded, borderContext);
    const bool excludedTerminal =
        excludedValue.status == ReadStatus::NotApplicable &&
        excludedValue.canonicalValue.empty() &&
        excludedValue.ownerField == 102 &&
        excluded.fillAttr.windowsBrushRequests == 0;

    BrushFixture missingType;
    missingType.fillAttr.typeStatus = DISP_E_UNKNOWNNAME;
    const PropertyObservation missingTypeValue =
        BrushObservation(&missingType, borderContext);
    const bool missingTypeTerminal =
        missingTypeValue.status == ReadStatus::NotExposed &&
        missingTypeValue.canonicalValue.empty() &&
        missingType.fillAttr.windowsBrushRequests == 0;

    BrushFixture failedType;
    failedType.fillAttr.typeStatus = E_FAIL;
    const PropertyObservation failedTypeValue =
        BrushObservation(&failedType, borderContext);
    const bool failedTypeTerminal =
        failedTypeValue.status == ReadStatus::ReadFailed &&
        failedTypeValue.canonicalValue.empty() &&
        failedType.fillAttr.windowsBrushRequests == 0;

    const auto exactConverterTerminal = [&borderContext](
        const PropertyObservation& observation,
        const ReadStatus expected) noexcept {
        return observation.key == 5015 && observation.status == expected &&
            observation.canonicalValue.empty() &&
            observation.target == borderContext.target &&
            observation.targetIdentity == borderContext.targetIdentity &&
            observation.ownerField == 102 &&
            observation.origin == PropertyOrigin::Unavailable;
    };

    BrushFixture missingConverterName;
    missingConverterName.root.brushTypeNameStatus = DISP_E_UNKNOWNNAME;
    const PropertyObservation missingConverterNameValue =
        BrushObservation(&missingConverterName, borderContext);
    const bool missingConverterNameTerminal = exactConverterTerminal(
        missingConverterNameValue, ReadStatus::NotExposed) &&
        missingConverterName.root.brushTypeRequests == 1 &&
        missingConverterName.root.brushTypeCalls == 0 &&
        missingConverterName.fillAttr.windowsBrushRequests == 0;

    BrushFixture missingConverterMember;
    missingConverterMember.root.brushTypeStatus = DISP_E_MEMBERNOTFOUND;
    const PropertyObservation missingConverterMemberValue =
        BrushObservation(&missingConverterMember, borderContext);
    const bool missingConverterMemberTerminal = exactConverterTerminal(
        missingConverterMemberValue, ReadStatus::NotExposed) &&
        missingConverterMember.root.brushTypeRequests == 1 &&
        missingConverterMember.root.brushTypeCalls == 1 &&
        missingConverterMember.fillAttr.windowsBrushRequests == 0;

    BrushFixture badConverterIndex;
    badConverterIndex.root.brushTypeStatus = DISP_E_BADINDEX;
    const PropertyObservation badConverterIndexValue =
        BrushObservation(&badConverterIndex, borderContext);
    const bool badConverterIndexTerminal = exactConverterTerminal(
        badConverterIndexValue, ReadStatus::NotExposed) &&
        badConverterIndex.root.brushTypeRequests == 1 &&
        badConverterIndex.root.brushTypeCalls == 1 &&
        badConverterIndex.fillAttr.windowsBrushRequests == 0;

    BrushFixture failedConverterInvoke;
    failedConverterInvoke.root.brushTypeStatus = E_FAIL;
    const PropertyObservation failedConverterInvokeValue =
        BrushObservation(&failedConverterInvoke, borderContext);
    const bool failedConverterInvokeTerminal = exactConverterTerminal(
        failedConverterInvokeValue, ReadStatus::ReadFailed) &&
        failedConverterInvoke.root.brushTypeCalls == 1 &&
        failedConverterInvoke.fillAttr.windowsBrushRequests == 0;

    BrushFixture failedConverterConversion;
    failedConverterConversion.root.brushTypeConversionFailure = true;
    const PropertyObservation failedConverterConversionValue =
        BrushObservation(&failedConverterConversion, borderContext);
    const bool failedConverterConversionTerminal = exactConverterTerminal(
        failedConverterConversionValue, ReadStatus::ReadFailed) &&
        failedConverterConversion.root.brushTypeCalls == 1 &&
        failedConverterConversion.fillAttr.windowsBrushRequests == 0;

    const bool converterTerminals = missingConverterNameTerminal &&
        missingConverterMemberTerminal && badConverterIndexTerminal &&
        failedConverterInvokeTerminal && failedConverterConversionTerminal;

    BrushFixture missingBrush;
    missingBrush.fillAttr.windowsBrushStatus = DISP_E_UNKNOWNNAME;
    const PropertyObservation missingBrushValue =
        BrushObservation(&missingBrush, borderContext);
    const bool missingBrushTerminal =
        missingBrushValue.status == ReadStatus::NotExposed &&
        missingBrushValue.canonicalValue.empty();

    BrushFixture failedBrush;
    failedBrush.fillAttr.windowsBrushStatus = E_FAIL;
    const PropertyObservation failedBrushValue =
        BrushObservation(&failedBrush, borderContext);
    const bool failedBrushTerminal =
        failedBrushValue.status == ReadStatus::ReadFailed &&
        failedBrushValue.canonicalValue.empty();

    BrushFixture incompatible;
    const PropertyObservation incompatibleValue = BrushObservation(
        &incompatible,
        {hancom::graph::capture::PropertyTarget::Paragraph, L"0:0"});
    const bool incompatibleTerminal =
        incompatibleValue.status == ReadStatus::NotApplicable &&
        incompatibleValue.ownerField == 0 &&
        incompatible.root.brushTypeCalls == 0 &&
        incompatible.fillAttr.typeRequests == 0 &&
        incompatible.fillAttr.windowsBrushRequests == 0;

    std::wcout << L"EFFECTIVE_PROPERTIES_WINDOWS_BRUSH_VALUE "
               << valuePreserved << L'\n'
               << L"EFFECTIVE_PROPERTIES_WINDOWS_BRUSH_NOT_APPLICABLE "
               << excludedTerminal << L'\n'
               << L"EFFECTIVE_PROPERTIES_WINDOWS_BRUSH_TYPE_TERMINALS "
               << (missingTypeTerminal && failedTypeTerminal) << L'\n'
               << L"EFFECTIVE_PROPERTIES_WINDOWS_BRUSH_CONVERTER_TERMINALS "
               << converterTerminals << L'\n'
               << L"EFFECTIVE_PROPERTIES_WINDOWS_BRUSH_MEMBER_TERMINALS "
               << (missingBrushTerminal && failedBrushTerminal) << L'\n'
               << L"EFFECTIVE_PROPERTIES_WINDOWS_BRUSH_INCOMPATIBLE "
               << incompatibleTerminal << L'\n';
    return valuePreserved && excludedTerminal && missingTypeTerminal &&
        failedTypeTerminal && converterTerminals && missingBrushTerminal &&
        failedBrushTerminal && incompatibleTerminal;
}

enum class RouteDispatchRole : std::uint8_t {
    Root,
    Documents,
    Document,
    PositionSet,
    Action,
    ParameterSets,
    Parameter,
};

enum class HeadingMemberLane : std::uint8_t {
    Value,
    Missing,
    DispatchFailure,
    InvalidVariant,
};

struct RouteFixture;

class RouteDispatch final : public IDispatch {
public:
    RouteDispatch(RouteFixture* fixture, RouteDispatchRole role,
                  LONG documentId = 0) noexcept
        : fixture_(fixture), role_(role), documentId_(documentId) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid, void** object) noexcept override {
        if (object == nullptr) return E_POINTER;
        *object = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDispatch) {
            return E_NOINTERFACE;
        }
        *object = static_cast<IDispatch*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() noexcept override { return 2; }
    ULONG STDMETHODCALLTYPE Release() noexcept override { return 1; }
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* count) noexcept override {
        if (count == nullptr) return E_POINTER;
        *count = 0;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfo(
        UINT, LCID, ITypeInfo**) noexcept override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID iid, LPOLESTR* names, UINT count, LCID, DISPID* ids)
        noexcept override;
    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID member, REFIID iid, LCID, WORD flags,
        DISPPARAMS* parameters, VARIANT* result, EXCEPINFO*, UINT*)
        noexcept override;

private:
    RouteFixture* fixture_;
    RouteDispatchRole role_;
    LONG documentId_;
};

struct RouteFixture final {
    RouteFixture() noexcept
        : root(this, RouteDispatchRole::Root),
          documents(this, RouteDispatchRole::Documents),
          original(this, RouteDispatchRole::Document, 101),
          transient(this, RouteDispatchRole::Document, 202),
          positionSet(this, RouteDispatchRole::PositionSet),
          action(this, RouteDispatchRole::Action),
          parameterSets(this, RouteDispatchRole::ParameterSets),
          parameter(this, RouteDispatchRole::Parameter) {}

    RouteDispatch root;
    RouteDispatch documents;
    RouteDispatch original;
    RouteDispatch transient;
    RouteDispatch positionSet;
    RouteDispatch action;
    RouteDispatch parameterSets;
    RouteDispatch parameter;
    RouteDispatch* active = &original;
    LONG originalList = 7;
    LONG originalParagraph = 8;
    LONG originalCharacter = 9;
    LONG transientList = 70;
    LONG transientParagraph = 80;
    LONG transientCharacter = 90;
    bool modified = false;
    bool switchDuringTraversal = false;
    bool failTraversalSetPos = false;
    bool originalMissing = false;
    bool activationFails = false;
    bool mismatchAfterRestore = false;
    unsigned setPosCalls = 0;
    unsigned findItemCalls = 0;
    unsigned activationCalls = 0;
    unsigned mutationCalls = 0;
    HeadingMemberLane headingLane = HeadingMemberLane::Value;
    HeadingMemberLane bulletLane = HeadingMemberLane::Value;
    LONG headingType = 0;
    LONG bulletId = 37;
    LONG numberingId = 91;
    unsigned headingRequests = 0;
    unsigned bulletRequests = 0;
    unsigned numberingRequests = 0;
    LONG selectionMode = 0;
    unsigned tableCellBlockCalls = 0;
    unsigned g5TableCellBlockCalls = 0;
    unsigned g5SetPosCalls = 0;
    unsigned cellBorderDefaultCalls = 0;
    unsigned qualifiedBorderPropertyReads = 0;
    bool defaultWasExactlyQualified = false;
    bool g5BorderFixture = false;
    bool preserveSelectionModeOnSetPos = false;
    unsigned addressReadFailuresRemaining = 0;
    unsigned wrongAddressReadsRemaining = 0;
    unsigned modeReadCalls = 0;
    unsigned failModeReadAtCall = 0;
    unsigned getDefaultFailuresRemaining = 0;
    unsigned propertyReadFailuresRemaining = 0;
    bool tableCellBlockFails = false;
    std::wstring currentCellAddress = L"A1";
    std::wstring lastName{};
    std::wstring contentSignature = L"stable-content-signature";
    std::vector<std::wstring> events{};
};

HRESULT RouteDispatch::GetIDsOfNames(
    REFIID iid, LPOLESTR* names, UINT count, LCID, DISPID* ids)
    noexcept {
    if (iid != IID_NULL || names == nullptr || ids == nullptr || count != 1) {
        return E_INVALIDARG;
    }
    const std::wstring_view name(names[0]);
    fixture_->lastName.assign(name);
    if (name == L"XHwpDocuments") ids[0] = 1;
    else if (name == L"Active_XHwpDocument") ids[0] = 2;
    else if (name == L"FindItem") ids[0] = 3;
    else if (name == L"DocumentID") ids[0] = 4;
    else if (name == L"SetActive_XHwpDocument") ids[0] = 5;
    else if (name == L"GetPos") ids[0] = 6;
    else if (name == L"SetPos") ids[0] = 7;
    else if (name == L"SelectionMode") ids[0] = 8;
    else if (name == L"CreateSet") ids[0] = 9;
    else if (name == L"GetSelectedPosBySet") ids[0] = 10;
    else if (name == L"Item") ids[0] = 11;
    else if (name == L"IsModified") ids[0] = 12;
    else if (name == L"HAction") ids[0] = 13;
    else if (name == L"HParameterSet") ids[0] = 14;
    else if (name == L"HStyle") ids[0] = 15;
    else if (name == L"HParaShape") ids[0] = 16;
    else if (name == L"HCharShape") ids[0] = 26;
    else if (name == L"FaceNameHangul") ids[0] = 27;
    else if (name == L"HSet") ids[0] = 17;
    else if (name == L"GetDefault") ids[0] = 18;
    else if (name == L"Apply") ids[0] = 19;
    else if (name == L"HeadingType") {
        ++fixture_->headingRequests;
        if (fixture_->headingLane == HeadingMemberLane::Missing) {
            return DISP_E_UNKNOWNNAME;
        }
        ids[0] = 20;
    } else if (name == L"Level") ids[0] = 21;
    else if (name == L"TabDef") ids[0] = 22;
    else if (name == L"Numbering") {
        ++fixture_->numberingRequests;
        ids[0] = 23;
    } else if (name == L"Bullet") {
        ++fixture_->bulletRequests;
        if (fixture_->bulletLane == HeadingMemberLane::Missing) {
            return DISP_E_UNKNOWNNAME;
        }
        ids[0] = 24;
    } else if (name == L"PropertyOrigin") ids[0] = 25;
    else if (name == L"Run") ids[0] = 28;
    else if (name == L"KeyIndicator") ids[0] = 29;
    else if (name == L"HCellBorderFill" && fixture_->g5BorderFixture) ids[0] = 30;
    else if ((name == L"SelCellsBorderFill" || name == L"FillAttr") &&
             fixture_->g5BorderFixture) ids[0] = 31;
    else if (name == L"BrushType" && fixture_->g5BorderFixture) ids[0] = 33;
    else if (fixture_->g5BorderFixture &&
             (name == L"Type" ||
              (name.size() >= 6 && name.substr(0, 6) == L"Border") ||
              (name.size() >= 8 && name.substr(0, 8) == L"WinBrush") ||
              name == L"WindowsBrush")) ids[0] = 32;
    else if (name == L"ParentCtrl" && fixture_->g5BorderFixture) ids[0] = 34;
    else if (name == L"CtrlID" && fixture_->g5BorderFixture) ids[0] = 35;
    else if (name == L"GetCtrlInstID" && fixture_->g5BorderFixture) ids[0] = 36;
    else return DISP_E_UNKNOWNNAME;
    return S_OK;
}

HRESULT RouteDispatch::Invoke(
    DISPID member, REFIID iid, LCID, WORD flags,
    DISPPARAMS* parameters, VARIANT* result, EXCEPINFO*, UINT*)
    noexcept {
    if (iid != IID_NULL || parameters == nullptr) return E_INVALIDARG;
    const auto dispatch = [result](IDispatch* value) noexcept {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_DISPATCH;
        result->pdispVal = value;
        value->AddRef();
        return S_OK;
    };
    if (role_ == RouteDispatchRole::Root && member == 1 &&
        flags == DISPATCH_PROPERTYGET) {
        return dispatch(&fixture_->documents);
    }
    if (role_ == RouteDispatchRole::Documents && member == 2 &&
        flags == DISPATCH_PROPERTYGET) {
        return dispatch(fixture_->active);
    }
    if (role_ == RouteDispatchRole::Documents && member == 3 &&
        flags == DISPATCH_METHOD) {
        ++fixture_->findItemCalls;
        if (parameters->cArgs != 1 ||
            parameters->rgvarg[0].vt != VT_I4) return DISP_E_TYPEMISMATCH;
        const LONG wanted = parameters->rgvarg[0].lVal;
        if (wanted == 101) {
            return fixture_->originalMissing
                ? DISP_E_BADINDEX : dispatch(&fixture_->original);
        }
        return wanted == 202
            ? dispatch(&fixture_->transient) : DISP_E_BADINDEX;
    }
    if (role_ == RouteDispatchRole::Document && member == 4 &&
        flags == DISPATCH_PROPERTYGET) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_I4;
        result->lVal = documentId_;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Document && member == 5 &&
        flags == DISPATCH_METHOD) {
        ++fixture_->activationCalls;
        fixture_->events.push_back(
            L"activate:" + std::to_wstring(documentId_));
        if (fixture_->activationFails && documentId_ == 101) return E_FAIL;
        fixture_->active = this;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 6 &&
        flags == DISPATCH_METHOD) {
        if (parameters->cArgs != 3) return DISP_E_BADPARAMCOUNT;
        LONG* const list = parameters->rgvarg[2].plVal;
        LONG* const paragraph = parameters->rgvarg[1].plVal;
        LONG* const character = parameters->rgvarg[0].plVal;
        if (list == nullptr || paragraph == nullptr || character == nullptr) {
            return E_POINTER;
        }
        const bool isOriginal = fixture_->active == &fixture_->original;
        *list = isOriginal ? fixture_->originalList : fixture_->transientList;
        *paragraph = isOriginal
            ? fixture_->originalParagraph : fixture_->transientParagraph;
        *character = isOriginal
            ? fixture_->originalCharacter : fixture_->transientCharacter;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 7 &&
        flags == DISPATCH_METHOD) {
        ++fixture_->setPosCalls;
        if (!fixture_->preserveSelectionModeOnSetPos) {
            fixture_->selectionMode = 0;
        }
        if (fixture_->setPosCalls == 1 && fixture_->switchDuringTraversal) {
            fixture_->active = &fixture_->transient;
            fixture_->events.push_back(L"switch:202");
        }
        const LONG activeId = fixture_->active == &fixture_->original
            ? 101 : 202;
        fixture_->events.push_back(L"setpos:" + std::to_wstring(activeId));
        if (fixture_->setPosCalls == 1 && fixture_->failTraversalSetPos) {
            if (result == nullptr) return E_POINTER;
            result->vt = VT_BOOL;
            result->boolVal = VARIANT_FALSE;
            return S_OK;
        }
        if (parameters->cArgs != 3) return DISP_E_BADPARAMCOUNT;
        LONG list = parameters->rgvarg[2].lVal;
        LONG paragraph = parameters->rgvarg[1].lVal;
        LONG character = parameters->rgvarg[0].lVal;
        fixture_->currentCellAddress = list == 1409 ? L"G5" : L"A1";
        if (list == 1409) ++fixture_->g5SetPosCalls;
        if (fixture_->active == &fixture_->original) {
            if (fixture_->mismatchAfterRestore && fixture_->setPosCalls > 1) {
                ++list;
            }
            fixture_->originalList = list;
            fixture_->originalParagraph = paragraph;
            fixture_->originalCharacter = character;
        } else {
            fixture_->transientList = list;
            fixture_->transientParagraph = paragraph;
            fixture_->transientCharacter = character;
        }
        if (result == nullptr) return E_POINTER;
        result->vt = VT_BOOL;
        result->boolVal = VARIANT_TRUE;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 8 &&
        flags == DISPATCH_PROPERTYGET) {
        ++fixture_->modeReadCalls;
        if (fixture_->failModeReadAtCall == fixture_->modeReadCalls) {
            return E_FAIL;
        }
        if (result == nullptr) return E_POINTER;
        result->vt = VT_I4;
        result->lVal = fixture_->selectionMode;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 9 &&
        flags == DISPATCH_METHOD) {
        return dispatch(&fixture_->positionSet);
    }
    if (role_ == RouteDispatchRole::Root && member == 10 &&
        flags == DISPATCH_METHOD) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_BOOL;
        result->boolVal = VARIANT_FALSE;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::PositionSet && member == 11 &&
        flags == DISPATCH_METHOD) {
        if (parameters->cArgs != 1 || parameters->rgvarg[0].vt != VT_BSTR ||
            result == nullptr) return DISP_E_TYPEMISMATCH;
        const std::wstring_view name(parameters->rgvarg[0].bstrVal);
        result->vt = VT_I4;
        result->lVal = name == L"List" ? fixture_->originalList
            : name == L"Para" ? fixture_->originalParagraph
            : fixture_->originalCharacter;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 12 &&
        flags == DISPATCH_PROPERTYGET) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_BOOL;
        result->boolVal = fixture_->modified ? VARIANT_TRUE : VARIANT_FALSE;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 13 &&
        flags == DISPATCH_PROPERTYGET) {
        return dispatch(&fixture_->action);
    }
    if (role_ == RouteDispatchRole::Root && member == 14 &&
        flags == DISPATCH_PROPERTYGET) {
        return dispatch(&fixture_->parameterSets);
    }
    if (role_ == RouteDispatchRole::ParameterSets &&
        (member == 15 || member == 16 || member == 26 || member == 30) &&
        flags == DISPATCH_PROPERTYGET) {
        return dispatch(&fixture_->parameter);
    }
    if (role_ == RouteDispatchRole::Parameter &&
        (member == 17 || member == 31) && flags == DISPATCH_PROPERTYGET) {
        return dispatch(&fixture_->parameter);
    }
    if (role_ == RouteDispatchRole::Action && member == 18 &&
        flags == DISPATCH_METHOD) {
        if (parameters->cArgs == 2 && parameters->rgvarg[1].vt == VT_BSTR &&
            std::wstring_view(parameters->rgvarg[1].bstrVal) ==
                L"CellBorderFill") {
            ++fixture_->cellBorderDefaultCalls;
            if (fixture_->getDefaultFailuresRemaining != 0) {
                --fixture_->getDefaultFailuresRemaining;
                return E_FAIL;
            }
            fixture_->defaultWasExactlyQualified =
                fixture_->currentCellAddress == L"G5" &&
                ((fixture_->selectionMode == 0 &&
                  fixture_->g5SetPosCalls == 1 &&
                  fixture_->g5TableCellBlockCalls == 0) ||
                 (fixture_->selectionMode == 3 &&
                  fixture_->g5SetPosCalls == 2 &&
                  fixture_->g5TableCellBlockCalls == 1));
            fixture_->events.push_back(L"default:G5");
        }
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Action && member == 28 &&
        flags == DISPATCH_METHOD && parameters->cArgs == 1 &&
        parameters->rgvarg[0].vt == VT_BSTR &&
        std::wstring_view(parameters->rgvarg[0].bstrVal) ==
            L"TableCellBlock") {
        ++fixture_->tableCellBlockCalls;
        if (fixture_->tableCellBlockFails) return E_FAIL;
        if (fixture_->currentCellAddress == L"G5") {
            ++fixture_->g5TableCellBlockCalls;
        }
        fixture_->selectionMode = 3;
        fixture_->events.push_back(
            L"block:" + fixture_->currentCellAddress);
        if (result != nullptr) {
            result->vt = VT_BOOL;
            result->boolVal = VARIANT_TRUE;
        }
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 29 &&
        flags == DISPATCH_METHOD) {
        if (fixture_->addressReadFailuresRemaining != 0) {
            --fixture_->addressReadFailuresRemaining;
            return E_FAIL;
        }
        for (UINT index = 0; index != parameters->cArgs; ++index) {
            VARIANTARG& argument = parameters->rgvarg[index];
            if (argument.vt == (VT_BSTR | VT_BYREF) &&
                argument.pbstrVal != nullptr) {
                const std::wstring address =
                    fixture_->wrongAddressReadsRemaining != 0
                        ? L"F5" : fixture_->currentCellAddress;
                if (fixture_->wrongAddressReadsRemaining != 0) {
                    --fixture_->wrongAddressReadsRemaining;
                }
                const std::wstring indicator = L"Table(" + address + L")";
                *argument.pbstrVal = SysAllocString(indicator.c_str());
                return *argument.pbstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
        }
        return DISP_E_TYPEMISMATCH;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 19 &&
        flags == DISPATCH_PROPERTYGET) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_I4;
        result->lVal = 5;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 20 &&
        flags == DISPATCH_PROPERTYGET) {
        if (fixture_->headingLane == HeadingMemberLane::DispatchFailure) {
            return E_FAIL;
        }
        if (result == nullptr) return E_POINTER;
        if (fixture_->headingLane == HeadingMemberLane::InvalidVariant) {
            return dispatch(&fixture_->parameter);
        }
        result->vt = VT_I4;
        result->lVal = fixture_->headingType;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 21 &&
        flags == DISPATCH_PROPERTYGET) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_I4;
        result->lVal = 7;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 22 &&
        flags == DISPATCH_PROPERTYGET) {
        return DISP_E_MEMBERNOTFOUND;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 23 &&
        flags == DISPATCH_PROPERTYGET) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_I4;
        result->lVal = fixture_->numberingId;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 24 &&
        flags == DISPATCH_PROPERTYGET) {
        if (fixture_->bulletLane == HeadingMemberLane::DispatchFailure) {
            return E_FAIL;
        }
        if (result == nullptr) return E_POINTER;
        if (fixture_->bulletLane == HeadingMemberLane::InvalidVariant) {
            return dispatch(&fixture_->parameter);
        }
        result->vt = VT_I4;
        result->lVal = fixture_->bulletId;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 25 &&
        flags == DISPATCH_METHOD) {
        if (!fixture_->g5BorderFixture) return DISP_E_MEMBERNOTFOUND;
        if (result == nullptr) return E_POINTER;
        result->vt = VT_I4;
        result->lVal = 0;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 32 &&
        flags == DISPATCH_PROPERTYGET) {
        if (fixture_->propertyReadFailuresRemaining != 0 &&
            fixture_->lastName != L"Type" &&
            fixture_->lastName != L"WindowsBrush") {
            --fixture_->propertyReadFailuresRemaining;
            return E_FAIL;
        }
        if (result == nullptr) return E_POINTER;
        if (fixture_->lastName != L"Type" &&
            fixture_->lastName != L"WindowsBrush") {
            if (fixture_->currentCellAddress != L"G5" ||
                !fixture_->defaultWasExactlyQualified) {
                return E_UNEXPECTED;
            }
            ++fixture_->qualifiedBorderPropertyReads;
        }
        result->vt = VT_I4;
        result->lVal = 0;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 33 &&
        flags == DISPATCH_METHOD) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_I4;
        result->lVal = 1;
        return S_OK;
    }
    if (role_ == RouteDispatchRole::Root && member == 34 &&
        flags == DISPATCH_PROPERTYGET) {
        return dispatch(&fixture_->parameter);
    }
    if (role_ == RouteDispatchRole::Parameter && member == 35 &&
        flags == DISPATCH_PROPERTYGET) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_BSTR;
        result->bstrVal = SysAllocString(L"tbl");
        return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 36 &&
        flags == DISPATCH_METHOD) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_BSTR;
        result->bstrVal = SysAllocString(L"route-table");
        return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
    }
    if (role_ == RouteDispatchRole::Parameter && member == 27 &&
        flags == DISPATCH_PROPERTYGET) {
        if (result == nullptr) return E_POINTER;
        result->vt = VT_BSTR;
        result->bstrVal = SysAllocString(L"Run Face");
        return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
    }
    return DISP_E_MEMBERNOTFOUND;
}

std::wstring ReferencePayloadFingerprint(
    const hancom::graph::capture::ReaderPayload& payload);

CaptureStatus CaptureRouteFixture(
    RouteFixture* fixture,
    hancom::graph::capture::ReaderPayload* output) {
    hancom::graph::properties::ReferenceClosureDiagnostics diagnostics;
    return hancom::graph::properties::CaptureCurrentReferenceClosure(
        &fixture->root,
        {{hancom::graph::capture::PropertyTarget::Run,
          L"7:8:0:4", {7,8,0}, 0}},
        output,
        &diagnostics);
}

bool ExactDocumentRouteRestorationSmoke() {
    RouteFixture success;
    success.switchDuringTraversal = true;
    hancom::graph::capture::ReaderPayload successOutput;
    const CaptureStatus successStatus = CaptureRouteFixture(
        &success, &successOutput);
    const auto activated = std::find(
        success.events.begin(), success.events.end(), L"activate:101");
    const auto restoredPosition = std::find(
        success.events.begin(), success.events.end(), L"setpos:101");
    const auto characterDefinition = std::find_if(
        successOutput.definitions.begin(), successOutput.definitions.end(),
        [](const auto& definition) {
            return definition.kind ==
                hancom::graph::DefinitionKind::CharacterShape;
        });
    const bool characterBody = characterDefinition !=
            successOutput.definitions.end() &&
        characterDefinition->nativeIdState ==
            hancom::graph::ObservationState::NotExposed &&
        characterDefinition->bodyState ==
            hancom::graph::ObservationState::Value &&
        std::any_of(
            characterDefinition->properties.begin(),
            characterDefinition->properties.end(), [](const auto& property) {
                return property.key == 1000 &&
                    property.state ==
                        hancom::graph::ObservationState::Value &&
                    property.textValue == L"Run Face";
            });
    const std::wstring characterIdentity = characterDefinition !=
            successOutput.definitions.end()
        ? characterDefinition->identity : std::wstring{};
    const bool oneCharacterReference = !characterIdentity.empty() &&
        std::count_if(
            successOutput.definitionReferences.begin(),
            successOutput.definitionReferences.end(),
            [&characterIdentity](const auto& reference) {
                return reference.source ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    reference.sourceIdentity == L"7:8:0:4" &&
                    reference.edge ==
                        hancom::graph::EdgeKind::CharacterShapeRef &&
                    reference.definitionIdentity == characterIdentity;
            }) == 1;
    const bool independentRunBag = std::any_of(
        successOutput.properties.begin(), successOutput.properties.end(),
        [](const auto& property) {
            return property.target ==
                    hancom::graph::capture::PropertyTarget::Run &&
                property.targetIdentity == L"7:8:0:4" &&
                property.ownerField == 102 && property.key == 1000 &&
                property.state == hancom::graph::ObservationState::Value &&
                property.textValue == L"Run Face";
        });
    const bool noBoundaryFallback = std::none_of(
        successOutput.coverageFacts.begin(),
        successOutput.coverageFacts.end(), [](const auto& coverage) {
            return coverage.detail ==
                L"CharacterShapeBoundaryAndReference";
        });
    const bool successRestored = successStatus == CaptureStatus::Complete &&
        success.active == &success.original &&
        activated != success.events.end() &&
        restoredPosition != success.events.end() && activated < restoredPosition &&
        success.originalList == 7 && success.originalParagraph == 8 &&
        success.originalCharacter == 9 && characterBody &&
        oneCharacterReference && independentRunBag && noBoundaryFallback;

    RouteFixture readFailure;
    readFailure.switchDuringTraversal = true;
    readFailure.failTraversalSetPos = true;
    hancom::graph::capture::ReaderPayload failedOutput;
    failedOutput.coverageFacts.push_back({});
    failedOutput.coverageFacts.front().detail = L"prior-publication";
    const CaptureStatus readFailureStatus = CaptureRouteFixture(
        &readFailure, &failedOutput);
    const bool failureRestored =
        readFailureStatus == CaptureStatus::SourceFailed &&
        readFailure.active == &readFailure.original &&
        readFailure.originalList == 7 &&
        failedOutput.coverageFacts.size() == 1 &&
        failedOutput.coverageFacts.front().detail == L"prior-publication";

    RouteFixture missing;
    missing.switchDuringTraversal = true;
    missing.originalMissing = true;
    hancom::graph::capture::ReaderPayload missingOutput;
    missingOutput.coverageFacts.push_back({});
    const CaptureStatus missingStatus = CaptureRouteFixture(&missing, &missingOutput);
    const bool missingRejected = missingStatus == CaptureStatus::SourceFailed &&
        missing.active == &missing.transient && missingOutput.coverageFacts.size() == 1 &&
        std::find(missing.events.begin(), missing.events.end(), L"setpos:202") !=
            missing.events.end() &&
        std::count(missing.events.begin(), missing.events.end(), L"setpos:202") == 1;

    RouteFixture activationFailure;
    activationFailure.switchDuringTraversal = true;
    activationFailure.activationFails = true;
    hancom::graph::capture::ReaderPayload activationOutput;
    activationOutput.coverageFacts.push_back({});
    const bool activationRejected = CaptureRouteFixture(
            &activationFailure, &activationOutput) == CaptureStatus::SourceFailed &&
        activationFailure.active == &activationFailure.transient &&
        activationOutput.coverageFacts.size() == 1;

    RouteFixture mismatch;
    mismatch.switchDuringTraversal = true;
    mismatch.mismatchAfterRestore = true;
    hancom::graph::capture::ReaderPayload mismatchOutput;
    mismatchOutput.coverageFacts.push_back({});
    const bool mismatchRejected = CaptureRouteFixture(
            &mismatch, &mismatchOutput) == CaptureStatus::SourceFailed &&
        mismatch.active == &mismatch.original &&
        mismatch.originalList != 7 && mismatchOutput.coverageFacts.size() == 1;

    RouteFixture unchanged;
    hancom::graph::capture::ReaderPayload unchangedOutput;
    const bool unchangedVerified = CaptureRouteFixture(
            &unchanged, &unchangedOutput) == CaptureStatus::Complete &&
        unchanged.activationCalls == 0 && unchanged.findItemCalls != 0 &&
        unchanged.active == &unchanged.original && unchanged.setPosCalls == 2;

    const bool contentStable = success.contentSignature ==
            L"stable-content-signature" && success.mutationCalls == 0 &&
        readFailure.contentSignature == L"stable-content-signature" &&
        readFailure.mutationCalls == 0;

    struct G5CaptureResult final {
        bool complete = false;
        bool exactRestore = false;
        bool exactReadOrder = false;
        unsigned blockCalls = 0;
        unsigned setPosCalls = 0;
        ReferenceClosureDiagnostics diagnostics{};
    };
    const auto captureG5 = [](const LONG residue,
                              hancom::graph::capture::ReaderPayload* output) {
        RouteFixture fixture;
        fixture.g5BorderFixture = true;
        fixture.preserveSelectionModeOnSetPos = residue != 0;
        fixture.selectionMode = residue;
        fixture.currentCellAddress = L"A1";
        ReferenceClosureDiagnostics diagnostics;
        const CaptureStatus status =
            hancom::graph::properties::CaptureCurrentReferenceClosure(
                &fixture.root,
                {{hancom::graph::capture::PropertyTarget::Cell,
                  L"table:1142018492:G5", {1409, 0, 0}, 0, {},
                  L"5:tbl:1:1142018492:0:0:0:route-table"}},
                output, &diagnostics);
        const auto block = std::find(
            fixture.events.begin(), fixture.events.end(), L"block:G5");
        const auto getDefault = std::find(
            fixture.events.begin(), fixture.events.end(), L"default:G5");
        G5CaptureResult captured;
        captured.complete = status == CaptureStatus::Complete &&
            diagnostics.expectedSites == 1 && diagnostics.visitedSites == 1;
        captured.exactRestore = fixture.active == &fixture.original &&
            fixture.originalList == 7 && fixture.originalParagraph == 8 &&
            fixture.originalCharacter == 9 && fixture.selectionMode == residue &&
            fixture.currentCellAddress == L"A1";
        const bool fast = residue == 0;
        captured.exactReadOrder = fixture.cellBorderDefaultCalls == 1 &&
            fixture.defaultWasExactlyQualified &&
            fixture.qualifiedBorderPropertyReads == 15 &&
            getDefault != fixture.events.end() &&
            (fast ? block == fixture.events.end()
                  : block != fixture.events.end() && block < getDefault);
        captured.blockCalls = fixture.g5TableCellBlockCalls;
        captured.setPosCalls = fixture.g5SetPosCalls;
        captured.diagnostics = diagnostics;
        return captured;
    };
    hancom::graph::capture::ReaderPayload g5FromSelectedResidue;
    hancom::graph::capture::ReaderPayload g5FromCaretResidue;
    const G5CaptureResult selectedResidue =
        captureG5(3, &g5FromSelectedResidue);
    const G5CaptureResult caretResidue =
        captureG5(0, &g5FromCaretResidue);
    const auto qualifiedG5Border = [](const auto& payload) {
        const auto border = std::find_if(
            payload.definitions.begin(), payload.definitions.end(),
            [](const auto& definition) {
                return definition.kind ==
                    hancom::graph::DefinitionKind::BorderFill;
            });
        if (border == payload.definitions.end() ||
            border->bodyState != hancom::graph::ObservationState::Value ||
            border->identity.find(
                L"f916e7bdf477fa645e4e5a0405147e0ea8e7cde26d0f2f5230d201f7c42e3ba0") ==
                std::wstring::npos || border->properties.size() != 16) {
            return false;
        }
        for (size_t index = 0; index != border->properties.size(); ++index) {
            const auto& property = border->properties[index];
            if (property.key != 5000U + index ||
                (index < 15 &&
                 (property.state !=
                      hancom::graph::ObservationState::Value ||
                  property.origin != hancom::graph::PropertyOrigin::Direct ||
                  property.integerValue != 0)) ||
                (index == 15 &&
                 (property.state !=
                      hancom::graph::ObservationState::NotApplicable ||
                  property.origin !=
                      hancom::graph::PropertyOrigin::NotApplicable))) {
                return false;
            }
        }
        return std::count_if(
            payload.definitionReferences.begin(),
            payload.definitionReferences.end(), [](const auto& reference) {
                return reference.source ==
                        hancom::graph::capture::PropertyTarget::Cell &&
                    reference.sourceIdentity == L"table:1142018492:G5" &&
                    reference.edge == hancom::graph::EdgeKind::BorderFillRef;
            }) == 1;
    };
    const bool mode0FastPath = caretResidue.complete &&
        caretResidue.exactRestore && caretResidue.exactReadOrder &&
        caretResidue.blockCalls == 0 && caretResidue.setPosCalls == 1 &&
        caretResidue.diagnostics.cellSites == 1 &&
        caretResidue.diagnostics.cellFastMode0Attempts == 1 &&
        caretResidue.diagnostics.cellFastMode0Hits == 1 &&
        caretResidue.diagnostics.cellFastMode0Rejections == 0 &&
        caretResidue.diagnostics.cellQualifiedFallbackCalls == 0 &&
        caretResidue.diagnostics.cellFastGetDefaultCalls == 1 &&
        qualifiedG5Border(g5FromCaretResidue);
    const bool mode3Fallback = selectedResidue.complete &&
        selectedResidue.exactRestore && selectedResidue.exactReadOrder &&
        selectedResidue.blockCalls == 1 && selectedResidue.setPosCalls == 2 &&
        selectedResidue.diagnostics.cellSites == 1 &&
        selectedResidue.diagnostics.cellFastMode0Attempts == 0 &&
        selectedResidue.diagnostics.cellFastMode0Hits == 0 &&
        selectedResidue.diagnostics.cellFastMode0Rejections == 1 &&
        selectedResidue.diagnostics.cellQualifiedFallbackCalls == 1 &&
        selectedResidue.diagnostics.cellTableCellBlockCalls == 1 &&
        selectedResidue.diagnostics.cellFallbackGetDefaultCalls == 1 &&
        qualifiedG5Border(g5FromSelectedResidue);
    const bool f916DigestBoth = qualifiedG5Border(g5FromSelectedResidue) &&
        qualifiedG5Border(g5FromCaretResidue);
    const bool g5CurrentCellProof = mode0FastPath && mode3Fallback &&
        f916DigestBoth &&
        ReferencePayloadFingerprint(g5FromSelectedResidue) ==
            ReferencePayloadFingerprint(g5FromCaretResidue);

    const auto captureConfiguredG5 = [](
        RouteFixture* const fixture,
        const std::wstring& owner,
        hancom::graph::capture::ReaderPayload* const output,
        ReferenceClosureDiagnostics* const diagnostics) {
        fixture->g5BorderFixture = true;
        fixture->currentCellAddress = L"A1";
        return hancom::graph::properties::CaptureCurrentReferenceClosure(
            &fixture->root,
            {{hancom::graph::capture::PropertyTarget::Cell,
              L"table:1142018492:G5", {1409, 0, 0}, 0, {}, owner}},
            output, diagnostics);
    };
    const std::wstring exactOwner =
        L"5:tbl:1:1142018492:0:0:0:route-table";
    const auto fallbackRecovery = [&](RouteFixture* const fixture) {
        hancom::graph::capture::ReaderPayload payload;
        ReferenceClosureDiagnostics diagnostics;
        const CaptureStatus status = captureConfiguredG5(
            fixture, exactOwner, &payload, &diagnostics);
        return status == CaptureStatus::Complete &&
            diagnostics.cellFastMode0Hits == 0 &&
            diagnostics.cellFastMode0Rejections == 1 &&
            diagnostics.cellQualifiedFallbackCalls == 1 &&
            diagnostics.cellTableCellBlockCalls == 1 &&
            diagnostics.cellFallbackGetDefaultCalls == 1 &&
            fixture->g5TableCellBlockCalls == 1 &&
            qualifiedG5Border(payload) &&
            ReferencePayloadFingerprint(payload) ==
                ReferencePayloadFingerprint(g5FromSelectedResidue) &&
            fixture->originalList == 7 && fixture->originalParagraph == 8 &&
            fixture->originalCharacter == 9;
    };
    RouteFixture wrongAddress;
    wrongAddress.wrongAddressReadsRemaining = 1;
    const bool wrongAddressFallback = fallbackRecovery(&wrongAddress);
    RouteFixture addressReadFailure;
    addressReadFailure.addressReadFailuresRemaining = 1;
    const bool addressReadFailureFallback = fallbackRecovery(&addressReadFailure);
    RouteFixture modeReadFailure;
    modeReadFailure.failModeReadAtCall = 2;
    const bool modeReadFailureFallback = fallbackRecovery(&modeReadFailure);
    RouteFixture wrongOwner;
    const bool wrongOwnerFallback = [&] {
        hancom::graph::capture::ReaderPayload payload;
        ReferenceClosureDiagnostics diagnostics;
        return captureConfiguredG5(
                &wrongOwner,
                L"5:tbl:1:1142018492:0:0:0:other-table",
                &payload, &diagnostics) == CaptureStatus::Complete &&
            diagnostics.cellQualifiedFallbackCalls == 1 &&
            wrongOwner.g5TableCellBlockCalls == 1 &&
            qualifiedG5Border(payload);
    }();
    RouteFixture readFailed;
    readFailed.propertyReadFailuresRemaining = 1;
    const bool readFailedFallback = [&] {
        hancom::graph::capture::ReaderPayload payload;
        ReferenceClosureDiagnostics diagnostics;
        return captureConfiguredG5(
                &readFailed, exactOwner, &payload, &diagnostics) ==
                    CaptureStatus::Complete &&
            diagnostics.cellFastMode0Attempts == 1 &&
            diagnostics.cellFastMode0Hits == 0 &&
            diagnostics.cellFastMode0Rejections == 1 &&
            diagnostics.cellDiscardedProvisionalSets == 1 &&
            diagnostics.cellQualifiedFallbackCalls == 1 &&
            diagnostics.cellFallbackGetDefaultCalls == 1 &&
            readFailed.cellBorderDefaultCalls == 2 &&
            readFailed.g5TableCellBlockCalls == 1 &&
            qualifiedG5Border(payload) &&
            ReferencePayloadFingerprint(payload) ==
                ReferencePayloadFingerprint(g5FromSelectedResidue) &&
            readFailed.originalList == 7 &&
            readFailed.originalParagraph == 8 &&
            readFailed.originalCharacter == 9;
    }();
    const bool rejectedRawReuseAbsent = readFailedFallback;
    RouteFixture getDefaultFailure;
    getDefaultFailure.getDefaultFailuresRemaining = 1;
    const bool getDefaultFailureFallback = fallbackRecovery(&getDefaultFailure);
    RouteFixture fallbackFailure;
    fallbackFailure.preserveSelectionModeOnSetPos = true;
    fallbackFailure.selectionMode = 3;
    fallbackFailure.tableCellBlockFails = true;
    hancom::graph::capture::ReaderPayload fallbackFailurePayload;
    fallbackFailurePayload.coverageFacts.push_back({});
    fallbackFailurePayload.coverageFacts.front().detail = L"prior-publication";
    ReferenceClosureDiagnostics fallbackFailureDiagnostics;
    const bool qualifiedFallbackFailsClosed =
        captureConfiguredG5(
            &fallbackFailure, exactOwner, &fallbackFailurePayload,
            &fallbackFailureDiagnostics) == CaptureStatus::SourceFailed &&
        fallbackFailurePayload.coverageFacts.size() == 1 &&
        fallbackFailurePayload.coverageFacts.front().detail ==
            L"prior-publication";
    RouteFixture reversedMode3;
    reversedMode3.preserveSelectionModeOnSetPos = true;
    reversedMode3.selectionMode = 3;
    RouteFixture reversedMode0;
    hancom::graph::capture::ReaderPayload reversedMode3Payload;
    hancom::graph::capture::ReaderPayload reversedMode0Payload;
    ReferenceClosureDiagnostics reversedMode3Diagnostics;
    ReferenceClosureDiagnostics reversedMode0Diagnostics;
    const bool reverseAttempts = captureConfiguredG5(
            &reversedMode3, exactOwner, &reversedMode3Payload,
            &reversedMode3Diagnostics) == CaptureStatus::Complete &&
        captureConfiguredG5(
            &reversedMode0, exactOwner, &reversedMode0Payload,
            &reversedMode0Diagnostics) == CaptureStatus::Complete &&
        reversedMode3Diagnostics.cellQualifiedFallbackCalls == 1 &&
        reversedMode0Diagnostics.cellFastMode0Hits == 1 &&
        ReferencePayloadFingerprint(reversedMode3Payload) ==
            ReferencePayloadFingerprint(reversedMode0Payload);
    const auto completeStructuralProbe = [] {
        ReferenceSiteObservation probe;
        ReferencedDefinition definition;
        definition.kind = hancom::graph::DefinitionKind::BorderFill;
        definition.nativeIdStatus = ReadStatus::NotExposed;
        definition.bodyStatus = ReadStatus::Value;
        for (hancom::graph::PropertyKeyId key = 5000; key <= 5015; ++key) {
            const PropertyRule* const rule = FindPropertyRule(key);
            PropertyObservation property;
            property.key = key;
            property.scalar = rule->scalar;
            property.status = key == 5015
                ? ReadStatus::NotApplicable : ReadStatus::Value;
            property.canonicalValue = key == 5015
                ? L"" : (property.scalar == hancom::graph::ScalarTag::BGR
                    ? L"u:0" : L"i:0");
            property.origin = key == 5015
                ? PropertyOrigin::NotApplicable : PropertyOrigin::Direct;
            property.target =
                hancom::graph::capture::PropertyTarget::Definition;
            property.ownerField = 102;
            definition.properties.push_back(std::move(property));
        }
        probe.definitions.push_back(std::move(definition));
        probe.references.push_back(
            {hancom::graph::EdgeKind::BorderFillRef, 0, 0});
        return probe;
    };
    const ReferenceSiteObservation completeNotApplicable =
        completeStructuralProbe();
    const bool completeTerminalAccepted =
        IsStructurallyCompleteTerminalCellBorder(completeNotApplicable);
    ReferenceSiteObservation qualifiedValue = completeStructuralProbe();
    PropertyObservation& qualifiedBrush =
        qualifiedValue.definitions.front().properties.back();
    qualifiedBrush.status = ReadStatus::Value;
    qualifiedBrush.canonicalValue = L"u:1";
    qualifiedBrush.origin = PropertyOrigin::Direct;
    const bool qualifiedTerminalAccepted =
        IsStructurallyCompleteTerminalCellBorder(qualifiedValue);
    ReferenceSiteObservation unqualifiedValue = qualifiedValue;
    unqualifiedValue.definitions.front().properties.back().canonicalValue =
        L"i:1";
    const bool unqualifiedValueRejected =
        !IsStructurallyCompleteTerminalCellBorder(unqualifiedValue);
    ReferenceSiteObservation terminalNotExposed = completeStructuralProbe();
    terminalNotExposed.definitions.front().properties.back().status =
        ReadStatus::NotExposed;
    terminalNotExposed.definitions.front().properties.back().origin =
        PropertyOrigin::Unavailable;
    const bool terminalNotExposedRejected =
        !IsStructurallyCompleteTerminalCellBorder(terminalNotExposed);

    bool requiredKeyMatrix = true;
    for (size_t index = 0; index != 15; ++index) {
        ReferenceSiteObservation notExposed = completeStructuralProbe();
        PropertyObservation& unavailable =
            notExposed.definitions.front().properties[index];
        unavailable.status = ReadStatus::NotExposed;
        unavailable.canonicalValue.clear();
        unavailable.origin = PropertyOrigin::Unavailable;
        requiredKeyMatrix = requiredKeyMatrix &&
            !IsStructurallyCompleteTerminalCellBorder(notExposed);

        ReferenceSiteObservation keyReadFailure = completeStructuralProbe();
        PropertyObservation& failed =
            keyReadFailure.definitions.front().properties[index];
        failed.status = ReadStatus::ReadFailed;
        failed.canonicalValue.clear();
        failed.origin = PropertyOrigin::Unavailable;
        requiredKeyMatrix = requiredKeyMatrix &&
            !IsStructurallyCompleteTerminalCellBorder(keyReadFailure);

        ReferenceSiteObservation missingKey = completeStructuralProbe();
        missingKey.definitions.front().properties.erase(
            missingKey.definitions.front().properties.begin() + index);
        requiredKeyMatrix = requiredKeyMatrix &&
            !IsStructurallyCompleteTerminalCellBorder(missingKey);

        ReferenceSiteObservation wrongKind = completeStructuralProbe();
        wrongKind.definitions.front().properties[index].scalar =
            hancom::graph::ScalarTag::UTF16;
        wrongKind.definitions.front().properties[index].canonicalValue =
            L"s:0";
        requiredKeyMatrix = requiredKeyMatrix &&
            !IsStructurallyCompleteTerminalCellBorder(wrongKind);

        ReferenceSiteObservation wrongOrigin = completeStructuralProbe();
        wrongOrigin.definitions.front().properties[index].origin =
            PropertyOrigin::Inherited;
        requiredKeyMatrix = requiredKeyMatrix &&
            !IsStructurallyCompleteTerminalCellBorder(wrongOrigin);
    }
    ReferenceSiteObservation duplicate = completeStructuralProbe();
    duplicate.definitions.front().properties.back() =
        duplicate.definitions.front().properties.front();
    const bool duplicateRejected =
        !IsStructurallyCompleteTerminalCellBorder(duplicate);
    ReferenceSiteObservation wrongValueEncoding = completeStructuralProbe();
    wrongValueEncoding.definitions.front().properties.front().canonicalValue =
        L"i:0junk";
    const bool wrongValueEncodingRejected =
        !IsStructurallyCompleteTerminalCellBorder(wrongValueEncoding);
    ReferenceSiteObservation unexpectedDetail = completeStructuralProbe();
    unexpectedDetail.coverageFacts.push_back({});
    const bool unexpectedDetailRejected =
        !IsStructurallyCompleteTerminalCellBorder(unexpectedDetail);
    const bool structuralTerminalContract = completeTerminalAccepted &&
        qualifiedTerminalAccepted && unqualifiedValueRejected &&
        terminalNotExposedRejected && requiredKeyMatrix &&
        duplicateRejected && wrongValueEncodingRejected &&
        unexpectedDetailRejected;
    const bool g5FailureMatrix = wrongAddressFallback &&
        addressReadFailureFallback && modeReadFailureFallback &&
        wrongOwnerFallback && readFailedFallback &&
        getDefaultFailureFallback && qualifiedFallbackFailsClosed &&
        reverseAttempts && structuralTerminalContract;

    std::wcout << L"EFFECTIVE_PROPERTIES_ROUTE_SUCCESS_RESTORED "
               << successRestored << L'\n'
               << L"EFFECTIVE_PROPERTIES_ROUTE_FAILURE_RESTORED "
               << failureRestored << L'\n'
               << L"EFFECTIVE_PROPERTIES_ROUTE_MISSING_REJECTED "
               << missingRejected << L'\n'
               << L"EFFECTIVE_PROPERTIES_ROUTE_ACTIVATION_REJECTED "
               << activationRejected << L'\n'
               << L"EFFECTIVE_PROPERTIES_ROUTE_STATE_MISMATCH_REJECTED "
               << mismatchRejected << L'\n'
               << L"EFFECTIVE_PROPERTIES_ROUTE_UNCHANGED_VERIFIED "
               << unchangedVerified << L'\n'
               << L"EFFECTIVE_PROPERTIES_ROUTE_CONTENT_STABLE "
               << contentStable << L'\n'
               << L"EFFECTIVE_PROPERTIES_G5_CURRENT_CELL_PROOF "
               << g5CurrentCellProof
               << L" MODE0_FAST=" << mode0FastPath
               << L" MODE3_FALLBACK=" << mode3Fallback
               << L" MODE0_BLOCKS=" << caretResidue.blockCalls
               << L" MODE3_BLOCKS=" << selectedResidue.blockCalls
               << L" MODE0_SETPOS=" << caretResidue.setPosCalls
               << L" MODE3_SETPOS=" << selectedResidue.setPosCalls
               << L" F916_BOTH=" << f916DigestBoth
               << L" DIGEST_PARITY="
               << (ReferencePayloadFingerprint(g5FromSelectedResidue) ==
                   ReferencePayloadFingerprint(g5FromCaretResidue))
               << L'\n'
               << L"EFFECTIVE_PROPERTIES_G5_FAST_FALLBACK_MATRIX "
               << g5FailureMatrix
               << L" WRONG_ADDRESS=" << wrongAddressFallback
               << L" ADDRESS_READ_FAILURE=" << addressReadFailureFallback
               << L" MODE_READ_FAILURE=" << modeReadFailureFallback
               << L" WRONG_OWNER=" << wrongOwnerFallback
               << L" READ_FAILED=" << readFailedFallback
               << L" NO_PARTIAL_MERGE=" << rejectedRawReuseAbsent
               << L" REJECTED_RAW_REUSE_ABSENT=" << rejectedRawReuseAbsent
               << L" GETDEFAULT_FAILURE=" << getDefaultFailureFallback
               << L" FALLBACK_FAILURE=" << qualifiedFallbackFailsClosed
               << L" REVERSE=" << reverseAttempts
               << L" REQUIRED_KEY_MATRIX=" << requiredKeyMatrix
               << L" KEY5015_NA=" << completeTerminalAccepted
               << L" KEY5015_VALUE=" << qualifiedTerminalAccepted
               << L" KEY5015_NOTEXPOSED=" << terminalNotExposedRejected
               << L" DUPLICATE=" << duplicateRejected
               << L" WRONG_ENCODING=" << wrongValueEncodingRejected
               << L" UNEXPECTED_DETAIL=" << unexpectedDetailRejected
               << L'\n';
    return successRestored && failureRestored && missingRejected &&
        activationRejected && mismatchRejected && unchangedVerified &&
        contentStable && g5CurrentCellProof && g5FailureMatrix;
}

bool BulletHeadingType3FakeIDispatchMatrixSmoke() {
    struct Result final {
        CaptureStatus effectiveStatus = CaptureStatus::SourceFailed;
        CaptureStatus referenceStatus = CaptureStatus::SourceFailed;
        PropertyObservation heading{};
        PropertyObservation level{};
        hancom::graph::capture::ReaderPayload closure{};
        unsigned headingRequests = 0;
        unsigned bulletRequests = 0;
        unsigned numberingRequests = 0;
    };
    const auto capture = [](
        const LONG headingType,
        const HeadingMemberLane headingLane,
        const HeadingMemberLane bulletLane,
        const std::vector<ReferenceSite>& sites) {
        RouteFixture fixture;
        fixture.headingType = headingType;
        fixture.headingLane = headingLane;
        fixture.bulletLane = bulletLane;
        Result captured;
        TransactionalSink sink;
        CaptureDiagnostics effectiveDiagnostics;
        captured.effectiveStatus =
            hancom::graph::properties::CaptureCurrentEffectiveProperties(
                &fixture.root,
                {hancom::graph::capture::PropertyTarget::Paragraph,
                 L"paragraph:bullet-matrix"},
                sink, &effectiveDiagnostics);
        const auto copyProperty = [&sink](
            const hancom::graph::PropertyKeyId key,
            PropertyObservation* const output) {
            const auto found = std::find_if(
                sink.published.begin(), sink.published.end(),
                [key](const PropertyObservation& property) {
                    return property.key == key;
                });
            if (found != sink.published.end()) *output = *found;
        };
        copyProperty(2012, &captured.heading);
        copyProperty(2013, &captured.level);
        ReferenceClosureDiagnostics referenceDiagnostics;
        captured.referenceStatus =
            hancom::graph::properties::CaptureCurrentReferenceClosure(
                &fixture.root, sites, &captured.closure,
                &referenceDiagnostics);
        captured.headingRequests = fixture.headingRequests;
        captured.bulletRequests = fixture.bulletRequests;
        captured.numberingRequests = fixture.numberingRequests;
        return captured;
    };
    const std::vector<ReferenceSite> paragraphSite{{
        hancom::graph::capture::PropertyTarget::Paragraph,
        L"paragraph:bullet-matrix", {0,0,0}, 0}};
    const auto rawValuesExact = [](const Result& result, const LONG expected) {
        return result.effectiveStatus == CaptureStatus::Complete &&
            result.heading.key == 2012 &&
            result.heading.scalar == hancom::graph::ScalarTag::Enum &&
            result.heading.status == ReadStatus::Value &&
            result.heading.canonicalValue ==
                L"i:" + std::to_wstring(expected) &&
            result.heading.targetIdentity == L"paragraph:bullet-matrix" &&
            result.heading.origin == PropertyOrigin::Unavailable &&
            result.level.key == 2013 &&
            result.level.status == ReadStatus::Value &&
            result.level.canonicalValue == L"i:7" &&
            result.level.origin == PropertyOrigin::Unavailable;
    };
    const auto numberingDefinition = [](const Result& result) {
        return std::find_if(
            result.closure.definitions.begin(),
            result.closure.definitions.end(), [](const auto& definition) {
                return definition.kind ==
                    hancom::graph::DefinitionKind::Numbering;
            });
    };
    const auto numberingReference = [](const Result& result) {
        return std::find_if(
            result.closure.definitionReferences.begin(),
            result.closure.definitionReferences.end(), [](const auto& edge) {
                return edge.edge == hancom::graph::EdgeKind::NumberingRef;
            });
    };
    const auto catalogsNotExposed = [](const Result& result) {
        return result.closure.referenceTraversal
                   .globalNumberingCatalogNotExposed &&
            result.closure.referenceTraversal.globalBulletCatalogNotExposed &&
            std::count_if(
                result.closure.coverageFacts.begin(),
                result.closure.coverageFacts.end(), [](const auto& coverage) {
                    return coverage.target ==
                            hancom::graph::capture::PropertyTarget::Document &&
                        coverage.state ==
                            hancom::graph::CoverageState::NotExposed &&
                        (coverage.detail == L"NumberingGlobalCatalog" ||
                         coverage.detail == L"BulletGlobalCatalog");
                }) == 2;
    };

    const Result bullet = capture(
        3, HeadingMemberLane::Value, HeadingMemberLane::Value,
        paragraphSite);
    const auto bulletDefinition = numberingDefinition(bullet);
    const auto bulletReference = numberingReference(bullet);
    const bool bulletExact = rawValuesExact(bullet, 3) &&
        bullet.referenceStatus == CaptureStatus::Complete &&
        bulletDefinition != bullet.closure.definitions.end() &&
        bulletDefinition->nativeIdState ==
            hancom::graph::ObservationState::Value &&
        bulletDefinition->nativeId == 37 &&
        bulletReference != bullet.closure.definitionReferences.end() &&
        bulletReference->source ==
            hancom::graph::capture::PropertyTarget::Paragraph &&
        bulletReference->sourceIdentity == L"paragraph:bullet-matrix" &&
        bullet.bulletRequests == 1 && bullet.numberingRequests == 0 &&
        catalogsNotExposed(bullet);

    const Result numbering = capture(
        2, HeadingMemberLane::Value, HeadingMemberLane::Value,
        paragraphSite);
    const auto nativeNumbering = numberingDefinition(numbering);
    const bool numberingExact = rawValuesExact(numbering, 2) &&
        numbering.referenceStatus == CaptureStatus::Complete &&
        nativeNumbering != numbering.closure.definitions.end() &&
        nativeNumbering->nativeIdState ==
            hancom::graph::ObservationState::Value &&
        nativeNumbering->nativeId == 91 &&
        numbering.bulletRequests == 0 && numbering.numberingRequests == 1;

    bool unrelatedTypes = true;
    for (const LONG type : {0L, 1L, 4L}) {
        const Result plain = capture(
            type, HeadingMemberLane::Value, HeadingMemberLane::Value,
            paragraphSite);
        unrelatedTypes = unrelatedTypes && rawValuesExact(plain, type) &&
            plain.referenceStatus == CaptureStatus::Complete &&
            numberingDefinition(plain) == plain.closure.definitions.end() &&
            numberingReference(plain) ==
                plain.closure.definitionReferences.end() &&
            plain.bulletRequests == 0 && plain.numberingRequests == 0;
    }

    bool bulletTerminals = true;
    for (const HeadingMemberLane lane : {
             HeadingMemberLane::Missing,
             HeadingMemberLane::DispatchFailure,
             HeadingMemberLane::InvalidVariant}) {
        const Result terminal = capture(
            3, HeadingMemberLane::Value, lane, paragraphSite);
        const auto definition = numberingDefinition(terminal);
        const hancom::graph::ObservationState expectedState =
            lane == HeadingMemberLane::Missing
            ? hancom::graph::ObservationState::NotExposed
            : hancom::graph::ObservationState::ReadFailed;
        bulletTerminals = bulletTerminals && rawValuesExact(terminal, 3) &&
            terminal.referenceStatus == CaptureStatus::Complete &&
            definition != terminal.closure.definitions.end() &&
            definition->nativeIdState == expectedState &&
            numberingReference(terminal) !=
                terminal.closure.definitionReferences.end() &&
            terminal.bulletRequests == 1 &&
            terminal.numberingRequests == 0 &&
            catalogsNotExposed(terminal);
    }

    bool headingTerminals = true;
    for (const HeadingMemberLane lane : {
             HeadingMemberLane::Missing,
             HeadingMemberLane::DispatchFailure,
             HeadingMemberLane::InvalidVariant}) {
        const Result terminal = capture(
            3, lane, HeadingMemberLane::Value, paragraphSite);
        const ReadStatus expected = lane == HeadingMemberLane::Missing
            ? ReadStatus::NotExposed : ReadStatus::ReadFailed;
        headingTerminals = headingTerminals &&
            terminal.effectiveStatus == CaptureStatus::Complete &&
            terminal.heading.key == 2012 &&
            terminal.heading.status == expected &&
            terminal.heading.canonicalValue.empty() &&
            terminal.level.status == ReadStatus::Value &&
            terminal.level.canonicalValue == L"i:7" &&
            terminal.referenceStatus == CaptureStatus::Complete &&
            numberingDefinition(terminal) ==
                terminal.closure.definitions.end() &&
            numberingReference(terminal) ==
                terminal.closure.definitionReferences.end() &&
            terminal.bulletRequests == 0 &&
            terminal.numberingRequests == 0 &&
            catalogsNotExposed(terminal);
    }

    RouteFixture staleFixture;
    staleFixture.headingType = 3;
    hancom::graph::capture::ReaderPayload bulletedOutput;
    hancom::graph::capture::ReaderPayload plainOutput;
    ReferenceClosureDiagnostics staleDiagnostics;
    const bool firstBulleted =
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            &staleFixture.root, paragraphSite, &bulletedOutput,
            &staleDiagnostics) == CaptureStatus::Complete &&
        std::any_of(
            bulletedOutput.definitionReferences.begin(),
            bulletedOutput.definitionReferences.end(), [](const auto& edge) {
                return edge.edge == hancom::graph::EdgeKind::NumberingRef;
            });
    staleFixture.headingType = 0;
    const bool staleIdsCleared = firstBulleted &&
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            &staleFixture.root, paragraphSite, &plainOutput,
            &staleDiagnostics) == CaptureStatus::Complete &&
        std::none_of(
            plainOutput.definitions.begin(), plainOutput.definitions.end(),
            [](const auto& definition) {
                return definition.kind ==
                    hancom::graph::DefinitionKind::Numbering;
            }) &&
        std::none_of(
            plainOutput.definitionReferences.begin(),
            plainOutput.definitionReferences.end(), [](const auto& edge) {
                return edge.edge == hancom::graph::EdgeKind::NumberingRef;
            }) &&
        staleFixture.bulletRequests == 1 &&
        staleFixture.numberingRequests == 0;

    const std::vector<ReferenceSite> isolatedSites{
        {hancom::graph::capture::PropertyTarget::Cell,
         L"table:matrix:A1", {3,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Control,
         L"control:matrix", {0,0,1}, 0},
    };
    const Result isolated = capture(
        3, HeadingMemberLane::Value, HeadingMemberLane::Value,
        isolatedSites);
    const bool scopeIsolation =
        isolated.referenceStatus == CaptureStatus::Complete &&
        isolated.headingRequests == 1 &&
        isolated.bulletRequests == 0 && isolated.numberingRequests == 0 &&
        numberingDefinition(isolated) == isolated.closure.definitions.end() &&
        numberingReference(isolated) ==
            isolated.closure.definitionReferences.end() &&
        std::any_of(
            isolated.closure.coverageFacts.begin(),
            isolated.closure.coverageFacts.end(), [](const auto& coverage) {
                return coverage.target ==
                        hancom::graph::capture::PropertyTarget::Control &&
                    coverage.targetIdentity == L"control:matrix";
            }) &&
        std::any_of(
            isolated.closure.definitionReferences.begin(),
            isolated.closure.definitionReferences.end(), [](const auto& edge) {
                return edge.source ==
                        hancom::graph::capture::PropertyTarget::Cell &&
                    edge.sourceIdentity == L"table:matrix:A1" &&
                    edge.edge == hancom::graph::EdgeKind::BorderFillRef;
            });

    return bulletExact && numberingExact && unrelatedTypes && staleIdsCleared &&
        bulletTerminals && headingTerminals && scopeIsolation;
}

std::wstring ReferencePayloadFingerprint(
    const hancom::graph::capture::ReaderPayload& payload) {
    std::wostringstream value;
    value << payload.referenceTraversal.expectedSites << L':'
          << payload.referenceTraversal.visitedSites << L'|';
    for (const auto& definition : payload.definitions) {
        value << L'D' << static_cast<unsigned>(definition.kind) << L':'
              << definition.nativeId << L':'
              << static_cast<unsigned>(definition.nativeIdState) << L':'
              << static_cast<unsigned>(definition.bodyState) << L':'
              << definition.identity << L':';
        for (const auto byte : definition.propertyDigest.bytes) value << byte;
        for (const auto& property : definition.properties) {
            value << L'p' << property.key << L':'
                  << static_cast<unsigned>(property.scalar) << L':'
                  << static_cast<unsigned>(property.state) << L':'
                  << static_cast<unsigned>(property.origin) << L':'
                  << property.integerValue << L':' << property.textValue;
        }
    }
    for (const auto& reference : payload.definitionReferences) {
        value << L'R' << static_cast<unsigned>(reference.source) << L':'
              << reference.sourceIdentity << L':'
              << static_cast<unsigned>(reference.edge) << L':'
              << reference.definitionIdentity << L':' << reference.ordinal;
    }
    for (const auto& property : payload.properties) {
        value << L'P' << static_cast<unsigned>(property.target) << L':'
              << property.targetIdentity << L':' << property.ownerField << L':'
              << property.key << L':' << static_cast<unsigned>(property.scalar)
              << L':' << static_cast<unsigned>(property.state) << L':'
              << static_cast<unsigned>(property.origin) << L':'
              << property.integerValue << L':' << property.textValue;
    }
    for (const auto& coverage : payload.coverageFacts) {
        value << L'C' << static_cast<unsigned>(coverage.target) << L':'
              << coverage.targetIdentity << L':'
              << static_cast<unsigned>(coverage.coordinate) << L':'
              << coverage.ownerField << L':' << coverage.detail << L':'
              << coverage.propertyKeyPresent << L':' << coverage.propertyKey
              << L':' << static_cast<unsigned>(coverage.profile) << L':'
              << static_cast<unsigned>(coverage.state);
    }
    return value.str();
}

bool SinkFailureRejectsPartialPublishSmoke() {
    FakeSource source;
    const EffectivePropertyContext context{
        hancom::graph::capture::PropertyTarget::Paragraph, L"0:0"};
    TransactionalSink appendFailure;
    appendFailure.failAppend = true;
    CaptureDiagnostics appendDiagnostics;
    const CaptureStatus appendStatus = CaptureEffectiveProperties(
        context, source, appendFailure, &appendDiagnostics);

    TransactionalSink commitFailure;
    commitFailure.failCommit = true;
    CaptureDiagnostics commitDiagnostics;
    const CaptureStatus commitStatus = CaptureEffectiveProperties(
        context, source, commitFailure, &commitDiagnostics);

    return appendStatus == CaptureStatus::SinkFailed &&
        appendFailure.aborted && appendFailure.published.empty() &&
        commitStatus == CaptureStatus::SinkFailed &&
        commitFailure.aborted && commitFailure.published.empty();
}

class FakeReferenceSource final : public ReferenceClosureSource {
public:
    bool ReadSite(
        const ReferenceSite& site,
        ReferenceSiteObservation* const observation) noexcept override {
        if (observation == nullptr || (failAt != 0 && visits + 1 == failAt)) {
            return false;
        }
        ++visits;
        if (site.target ==
            hancom::graph::capture::PropertyTarget::Section) {
            ReferencedDefinition page;
            page.kind = hancom::graph::DefinitionKind::PageDef;
            page.nativeIdStatus = ReadStatus::NotExposed;
            page.bodyStatus = ReadStatus::Value;
            page.properties.push_back({7000,
                hancom::graph::ScalarTag::HWPUNIT64,
                ReadStatus::Value, L"i:59528"});
            observation->definitions.push_back(page);
            observation->references.push_back({
                hancom::graph::EdgeKind::PageDefRef, 0, 0});
            ReferencedDefinition column;
            column.kind = hancom::graph::DefinitionKind::ColumnDef;
            column.nativeIdStatus = ReadStatus::NotExposed;
            column.bodyStatus = ReadStatus::Value;
            column.properties.push_back({7100,
                hancom::graph::ScalarTag::Uint64,
                ReadStatus::Value, L"u:2"});
            observation->definitions.push_back(column);
            observation->references.push_back({
                hancom::graph::EdgeKind::ColumnDefRef, 1, 0});
            return true;
        }
        if (site.target ==
            hancom::graph::capture::PropertyTarget::Run) {
            ReferencedDefinition definition;
            definition.kind = hancom::graph::DefinitionKind::CharacterShape;
            definition.nativeIdStatus = ReadStatus::NotExposed;
            definition.bodyStatus = ReadStatus::Value;
            definition.properties.push_back({1000,
                hancom::graph::ScalarTag::UTF16,
                ReadStatus::Value, L"s:Run Face"});
            observation->definitions.push_back(definition);
            observation->references.push_back({
                hancom::graph::EdgeKind::CharacterShapeRef, 0, 0});
            PropertyObservation runProperty = definition.properties.front();
            runProperty.target = site.target;
            runProperty.targetIdentity = site.identity;
            runProperty.ownerField = 102;
            observation->properties.push_back(std::move(runProperty));
            return true;
        }
        if (site.target ==
                hancom::graph::capture::PropertyTarget::Control ||
            site.target == hancom::graph::capture::PropertyTarget::Table ||
            site.target == hancom::graph::capture::PropertyTarget::Image) {
            hancom::graph::capture::CoverageObservation family;
            family.target = site.target;
            family.targetIdentity = site.identity;
            family.ownerField = site.target ==
                    hancom::graph::capture::PropertyTarget::Image
                ? 110 : 106;
            family.detail = site.target ==
                    hancom::graph::capture::PropertyTarget::Control
                ? L"BorderFillReference"
                : site.target ==
                    hancom::graph::capture::PropertyTarget::Image
                ? L"AssetReference"
                : L"DefinitionReferenceFamilies";
            family.profile = site.target ==
                    hancom::graph::capture::PropertyTarget::Image
                ? hancom::graph::ProfileId::BinaryContent
                : hancom::graph::ProfileId::EditableObjects;
            family.state = site.target ==
                    hancom::graph::capture::PropertyTarget::Table
                ? hancom::graph::CoverageState::NotApplicable
                : hancom::graph::CoverageState::NotExposed;
            observation->coverageFacts.push_back(std::move(family));
            return true;
        }
        ReferencedDefinition definition;
        definition.kind = site.target ==
                hancom::graph::capture::PropertyTarget::Cell
            ? hancom::graph::DefinitionKind::BorderFill
            : hancom::graph::DefinitionKind::Style;
        definition.nativeId = absenceZeroPair ? 0 :
            site.target ==
                    hancom::graph::capture::PropertyTarget::Paragraph &&
                site.identity == L"0:1" && !sameNativeConflict
            ? 8 : 7;
        definition.nativeIdStatus = absenceZeroPair &&
                site.identity == L"0:0"
            ? ReadStatus::NotExposed : ReadStatus::Value;
        definition.bodyStatus =
            definition.kind == hancom::graph::DefinitionKind::Style
                ? ReadStatus::ReadFailed : ReadStatus::Value;
        if (definition.kind == hancom::graph::DefinitionKind::BorderFill) {
            definition.properties.push_back({5000,
                hancom::graph::ScalarTag::Enum,
                ReadStatus::Value, L"i:1"});
        } else if (sameNativeConflict && site.identity == L"0:1") {
            definition.properties.push_back({3000,
                hancom::graph::ScalarTag::UTF16,
                ReadStatus::Value, L"s:conflict"});
        }
        observation->definitions.push_back(definition);
        observation->references.push_back({
            site.target == hancom::graph::capture::PropertyTarget::Cell
                ? hancom::graph::EdgeKind::BorderFillRef
                : hancom::graph::EdgeKind::StyleRef,
            0, 0});
        return site.target ==
                hancom::graph::capture::PropertyTarget::Cell ||
            site.target ==
                hancom::graph::capture::PropertyTarget::Paragraph;
    }

    size_t visits = 0;
    size_t failAt = 0;
    bool sameNativeConflict = false;
    bool absenceZeroPair = false;
};

bool ReferenceClosureSmoke() {
    const std::vector<ReferenceSite> sites{
        {hancom::graph::capture::PropertyTarget::Section,
         L"0", {0,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Section,
         L"1", {0,2,0}, 1},
        {hancom::graph::capture::PropertyTarget::Paragraph,
         L"0:0", {0,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Run,
         L"0:0:0:5", {0,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Paragraph,
         L"0:1", {0,1,0}, 0},
        {hancom::graph::capture::PropertyTarget::Cell,
         L"table-1:A1", {3,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Control,
         L"control-1", {0,1,4}, 0},
        {hancom::graph::capture::PropertyTarget::Table,
         L"table-1", {0,1,5}, 0},
        {hancom::graph::capture::PropertyTarget::Image,
         L"image-1", {0,1,6}, 0},
    };
    FakeReferenceSource source;
    hancom::graph::capture::ReaderPayload output;
    output.reader = hancom::graph::capture::QualifiedReader::EffectiveProperties;
    ReferenceClosureDiagnostics diagnostics;
    const CaptureStatus status = CaptureReferenceClosure(
        source, sites, &output, &diagnostics);
    const bool allSites = status == CaptureStatus::Complete &&
        source.visits == sites.size() &&
        diagnostics.expectedSites == diagnostics.visitedSites &&
        output.referenceTraversal.expectedSites ==
            output.referenceTraversal.visitedSites;
    const bool deduplicated = output.definitions.size() == 6 &&
        output.definitionReferences.size() == 8;
    const bool nativeIdentitySplit = std::count_if(
        output.definitions.begin(), output.definitions.end(),
        [](const auto& definition) {
            return definition.kind == hancom::graph::DefinitionKind::Style &&
                definition.nativeIdState ==
                    hancom::graph::ObservationState::Value;
        }) == 2;
    const auto border = std::find_if(
        output.definitions.begin(), output.definitions.end(),
        [](const auto& definition) {
            return definition.kind ==
                hancom::graph::DefinitionKind::BorderFill;
        });
    const hancom::graph::codec::SemanticPropertyInput directProperty{
        5000, hancom::graph::PropertyOrigin::Direct,
        hancom::graph::codec::Enum(1, nullptr, 0)};
    const bool directOrigin = border != output.definitions.end() &&
        border->properties.size() == 1 &&
        border->properties.front().origin ==
            hancom::graph::PropertyOrigin::Direct &&
        border->propertyDigest.bytes ==
            hancom::graph::codec::DefinitionPropertyAggregate(
                {directProperty}).bytes;
    const bool siteFamilyTerminals =
        std::count_if(
            output.coverageFacts.begin(), output.coverageFacts.end(),
            [](const auto& coverage) {
                return coverage.target ==
                        hancom::graph::capture::PropertyTarget::Control &&
                    coverage.ownerField == 106 &&
                    coverage.profile ==
                        hancom::graph::ProfileId::EditableObjects &&
                    coverage.detail == L"BorderFillReference";
            }) == 1 &&
        std::count_if(
            output.coverageFacts.begin(), output.coverageFacts.end(),
            [](const auto& coverage) {
                return coverage.target ==
                        hancom::graph::capture::PropertyTarget::Table &&
                    coverage.ownerField == 106 &&
                    coverage.profile ==
                        hancom::graph::ProfileId::EditableObjects &&
                    coverage.detail == L"DefinitionReferenceFamilies";
            }) == 1 &&
        std::count_if(
            output.coverageFacts.begin(), output.coverageFacts.end(),
            [](const auto& coverage) {
                return coverage.target ==
                        hancom::graph::capture::PropertyTarget::Image &&
                    coverage.ownerField == 110 &&
                    coverage.profile ==
                        hancom::graph::ProfileId::BinaryContent &&
                    coverage.detail == L"AssetReference";
            }) == 1;
    const bool terminal = std::any_of(
        output.definitions.begin(), output.definitions.end(),
        [](const auto& definition) {
            return definition.bodyState ==
                hancom::graph::ObservationState::ReadFailed;
        });
    const auto character = std::find_if(
        output.definitions.begin(), output.definitions.end(),
        [](const auto& definition) {
            return definition.kind ==
                hancom::graph::DefinitionKind::CharacterShape;
        });
    const bool runClosure = character != output.definitions.end() &&
        character->bodyState == hancom::graph::ObservationState::Value &&
        character->nativeIdState ==
            hancom::graph::ObservationState::NotExposed &&
        std::count_if(
            output.definitionReferences.begin(),
            output.definitionReferences.end(), [](const auto& reference) {
                return reference.source ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    reference.edge ==
                        hancom::graph::EdgeKind::CharacterShapeRef;
            }) == 1 &&
        std::any_of(
            output.properties.begin(), output.properties.end(),
            [](const auto& property) {
                return property.target ==
                        hancom::graph::capture::PropertyTarget::Run &&
                    property.targetIdentity == L"0:0:0:5" &&
                    property.ownerField == 102 && property.key == 1000 &&
                    property.state ==
                        hancom::graph::ObservationState::Value;
            });
    const bool catalogTerminal =
        diagnostics.globalStyleCatalogNotExposed &&
        output.referenceTraversal.globalStyleCatalogNotExposed &&
        output.referenceTraversal.globalNumberingCatalogNotExposed &&
        output.referenceTraversal.globalBulletCatalogNotExposed &&
        output.referenceTraversal.globalTabDefCatalogNotExposed &&
        std::count_if(
            output.coverageFacts.begin(), output.coverageFacts.end(),
            [](const auto& coverage) {
                return coverage.target ==
                        hancom::graph::capture::PropertyTarget::Document &&
                    coverage.state ==
                        hancom::graph::CoverageState::NotExposed &&
                    coverage.detail.find(L"GlobalCatalog") !=
                        std::wstring::npos;
            }) == 4;
    FakeReferenceSource secondSource;
    hancom::graph::capture::ReaderPayload second;
    ReferenceClosureDiagnostics secondDiagnostics;
    hancom::graph::Sha256 firstPageSetup{};
    hancom::graph::Sha256 secondPageSetup{};
    hancom::graph::Sha256 unrelatedDefinitionPageSetup{};
    const bool secondCaptured = CaptureReferenceClosure(
        secondSource, sites, &second, &secondDiagnostics) ==
        CaptureStatus::Complete;
    auto withUnrelatedDefinition = second;
    hancom::graph::capture::DefinitionObservation unrelatedBorderFill;
    unrelatedBorderFill.kind = hancom::graph::DefinitionKind::BorderFill;
    unrelatedBorderFill.identity = L"unrelated-border-fill";
    unrelatedBorderFill.bodyState = hancom::graph::ObservationState::Value;
    withUnrelatedDefinition.definitions.insert(
        withUnrelatedDefinition.definitions.begin(),
        std::move(unrelatedBorderFill));
    const bool deterministic = secondCaptured &&
        output.definitions[0].propertyDigest.bytes ==
            second.definitions[0].propertyDigest.bytes &&
        DerivePageSetupDigest(output, &firstPageSetup) &&
        DerivePageSetupDigest(second, &secondPageSetup) &&
        DerivePageSetupDigest(
            withUnrelatedDefinition, &unrelatedDefinitionPageSetup) &&
        firstPageSetup.bytes == secondPageSetup.bytes &&
        secondPageSetup.bytes == unrelatedDefinitionPageSetup.bytes;
    const std::vector<ReferenceSite> orderingSites{
        {hancom::graph::capture::PropertyTarget::Paragraph,
         L"0:0", {0,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Paragraph,
         L"0:1", {0,1,0}, 0},
    };
    std::vector<ReferenceSite> reversedOrderingSites(
        orderingSites.rbegin(), orderingSites.rend());
    FakeReferenceSource orderedSource;
    orderedSource.absenceZeroPair = true;
    FakeReferenceSource reversedSource;
    reversedSource.absenceZeroPair = true;
    hancom::graph::capture::ReaderPayload orderedDefinitions;
    hancom::graph::capture::ReaderPayload reversedDefinitions;
    ReferenceClosureDiagnostics orderedDiagnostics;
    ReferenceClosureDiagnostics reversedDiagnostics;
    const bool nativePresenceOrdering = CaptureReferenceClosure(
            orderedSource, orderingSites, &orderedDefinitions,
            &orderedDiagnostics) == CaptureStatus::Complete &&
        CaptureReferenceClosure(
            reversedSource, reversedOrderingSites, &reversedDefinitions,
            &reversedDiagnostics) == CaptureStatus::Complete &&
        orderedDefinitions.definitions.size() == 2 &&
        reversedDefinitions.definitions.size() == 2 &&
        orderedDefinitions.definitions[0].nativeIdState !=
            hancom::graph::ObservationState::Value &&
        orderedDefinitions.definitions[1].nativeIdState ==
            hancom::graph::ObservationState::Value &&
        orderedDefinitions.definitions[1].nativeId == 0 &&
        orderedDefinitions.definitions[0].identity ==
            reversedDefinitions.definitions[0].identity &&
        orderedDefinitions.definitions[1].identity ==
            reversedDefinitions.definitions[1].identity;
    FakeReferenceSource conflict;
    conflict.sameNativeConflict = true;
    hancom::graph::capture::ReaderPayload conflicted;
    ReferenceClosureDiagnostics conflictDiagnostics;
    const bool nativeConflictRejected = CaptureReferenceClosure(
            conflict, sites, &conflicted, &conflictDiagnostics) ==
        CaptureStatus::SourceFailed;
    hancom::graph::capture::ReaderPayload captionClosure;
    hancom::graph::capture::ImageObservation captionImage;
    captionImage.anchor = {7, 3, 1};
    captionImage.headCtrlOrdinal = 11;
    captionImage.captionList.state = hancom::graph::ObservationState::Value;
    captionImage.captionList.value = 713;
    captionImage.captionStart = {713, 9, 2};
    captionImage.text[2].state = hancom::graph::ObservationState::Value;
    captionImage.text[2].value = L"caption";
    hancom::graph::capture::CaptureIdentityArena captionCapture;
    hancom::graph::capture::ReaderPayload unqualifiedCaptionClosure;
    ReferenceClosureDiagnostics unqualifiedCaptionDiagnostics;
    const bool payloadOnlyCaptionRejected =
        !AccountCaptionReferenceTerminals(
            captionCapture, {captionImage}, &unqualifiedCaptionClosure,
            &unqualifiedCaptionDiagnostics) &&
        unqualifiedCaptionClosure.coverageFacts.empty() &&
        unqualifiedCaptionClosure.referenceTraversal.expectedSites == 0 &&
        unqualifiedCaptionClosure.referenceTraversal.visitedSites == 0;
    ReferenceClosureDiagnostics forgedCaptionDiagnostics;
    const bool manuallyForgedCaptionRejected =
        !AccountCaptionReferenceTerminals(
            captionCapture, {captionImage}, &captionClosure,
            &forgedCaptionDiagnostics) &&
        captionClosure.coverageFacts.empty() &&
        captionClosure.referenceTraversal.expectedSites == 0 &&
        captionClosure.referenceTraversal.visitedSites == 0;
    FakeReferenceSource failed;
    failed.failAt = 3;
    hancom::graph::capture::ReaderPayload rejected;
    ReferenceClosureDiagnostics rejectedDiagnostics;
    const bool failClosed = CaptureReferenceClosure(
            failed, sites, &rejected, &rejectedDiagnostics) ==
        CaptureStatus::SourceFailed;
    std::wcout << L"EFFECTIVE_PROPERTIES_REFERENCE_ALL_SITES " << allSites
               << L'\n' << L"EFFECTIVE_PROPERTIES_REFERENCE_DEDUP "
               << deduplicated << L'\n'
               << L"EFFECTIVE_PROPERTIES_NATIVE_ID_SPLIT "
               << nativeIdentitySplit << L'\n'
               << L"EFFECTIVE_PROPERTIES_NATIVE_ID_CONFLICT_REJECTED "
               << nativeConflictRejected << L'\n'
               << L"EFFECTIVE_PROPERTIES_NATIVE_PRESENCE_ORDERING "
               << nativePresenceOrdering << L'\n'
               << L"EFFECTIVE_PROPERTIES_DIRECT_ORIGIN_BYTES "
               << directOrigin << L'\n'
               << L"EFFECTIVE_PROPERTIES_SITE_FAMILY_TERMINALS "
               << siteFamilyTerminals << L'\n'
               << L"EFFECTIVE_PROPERTIES_REFERENCE_TERMINAL " << terminal
               << L'\n' << L"EFFECTIVE_PROPERTIES_RUN_CHARACTER_SHAPE_CLOSURE "
               << runClosure
               << L'\n' << L"EFFECTIVE_PROPERTIES_GLOBAL_CATALOG_TERMINAL "
               << catalogTerminal << L'\n'
               << L"EFFECTIVE_PROPERTIES_PAGE_COLUMN_DIGEST_DETERMINISTIC "
               << deterministic << L'\n'
               << L"EFFECTIVE_PROPERTIES_PAYLOAD_ONLY_CAPTION_REJECTED "
               << payloadOnlyCaptionRejected << L'\n'
               << L"EFFECTIVE_PROPERTIES_MANUAL_CAPTION_QUALIFICATION_REJECTED "
               << manuallyForgedCaptionRejected << L'\n'
               << L"EFFECTIVE_PROPERTIES_INCOMPLETE_TRAVERSAL_REJECTED "
               << failClosed << L'\n';
    return allSites && deduplicated && nativeIdentitySplit &&
        nativeConflictRejected && nativePresenceOrdering && directOrigin &&
        siteFamilyTerminals && terminal && runClosure && catalogTerminal &&
        deterministic && payloadOnlyCaptionRejected &&
        manuallyForgedCaptionRejected && failClosed;
}

} // namespace

bool DocumentGraphEffectivePropertiesSmoke() {
    const bool registry = SchemaDrivenRegistrySmoke();
    const bool catalog = CatalogAndOriginBlockSmoke();
    const bool targetAware = TargetAwareObservationSmoke();
    const bool windowsBrush = WindowsBrushQualificationSmoke();
    const bool propertyMatrix =
        ProductionBoundaryRegistryValueMatrixSmoke(windowsBrush);
    const bool definitionOwningProfile =
        DefinitionOwningProfileRoutingSmoke();
    const bool unavailable7103And7104 =
        Withdrawn7103Unavailable7104RegressionSmoke();
    const bool originMatrix = PropertyOriginFakeIDispatchMatrixSmoke();
    const bool bulletHeadingType3Matrix =
        BulletHeadingType3FakeIDispatchMatrixSmoke();
    const bool exactRoute = ExactDocumentRouteRestorationSmoke();
    const bool failClosed = SinkFailureRejectsPartialPublishSmoke();
    const bool referenceClosure = ReferenceClosureSmoke();
    std::wcout << L"EFFECTIVE_PROPERTIES_REGISTRY " << registry << L'\n'
               << L"EFFECTIVE_PROPERTIES_CATALOG_ORIGIN_BLOCK "
               << catalog << L'\n'
               << L"PROPERTY_ORIGIN_FAKE_IDISPATCH_MATRIX "
               << originMatrix << L'\n'
               << L"BULLET_HEADING_TYPE3_FAKE_IDISPATCH_MATRIX "
               << bulletHeadingType3Matrix << L'\n'
               << L"EFFECTIVE_PROPERTIES_FAIL_CLOSED " << failClosed << L'\n';
    return registry && catalog && targetAware && windowsBrush && propertyMatrix &&
        definitionOwningProfile && unavailable7103And7104 && originMatrix &&
        bulletHeadingType3Matrix && exactRoute && failClosed && referenceClosure;
}

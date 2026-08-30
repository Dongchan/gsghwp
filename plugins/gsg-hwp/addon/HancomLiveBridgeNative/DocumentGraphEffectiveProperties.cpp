#include "DocumentGraphEffectiveProperties.h"

#include "DispatchInvoke.h"
#include "DocumentGraphCodec.h"
#include "DocumentGraphCodecInternal.h"
#include "DocumentGraphCaptureRecords.h"
#include "ComState.h"
#include "OfficialApiVirtualMethod.h"
#include "TableInspection.h"

#include <atlbase.h>
#include <atlcomcli.h>

#include <algorithm>
#include <cerrno>
#include <cwchar>
#include <iomanip>
#include <iterator>
#include <limits>
#include <map>
#include <new>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <utility>
#include <vector>

namespace hancom::graph::properties {
namespace {

constexpr GUID kRootVirtualInterfaceId{
    0x5E6A8276, 0xCF1C, 0x42B8,
    {0xBC, 0xED, 0x31, 0x95, 0x48, 0xB0, 0x2A, 0xF6}};
constexpr size_t kHParameterSetSlot = 73U;
constexpr size_t kHActionSlot = 74U;
constexpr size_t kUnresolvedVirtualSlot =
    (std::numeric_limits<size_t>::max)();
thread_local size_t gHParameterSetSlot = kUnresolvedVirtualSlot;
thread_local size_t gHActionSlot = kUnresolvedVirtualSlot;
constexpr GUID kCellBorderFillVirtualInterfaceId{
    0xC54797D7, 0xB2FF, 0x44A0,
    {0xBE, 0xC2, 0x2D, 0x20, 0xF2, 0x00, 0x89, 0x8C}};
constexpr GUID kBorderFillVirtualInterfaceId{
    0xC81C513C, 0x94D5, 0x4589,
    {0x8B, 0x92, 0xBF, 0xF9, 0xFD, 0x5D, 0xA9, 0x46}};
constexpr size_t kBorderColorLeftSlot = 26U;
constexpr size_t kBorderColorRightSlot = 28U;
constexpr size_t kBorderColorTopSlot = 30U;
constexpr size_t kBorderColorBottomSlot = 32U;
thread_local size_t gBorderColorLeftSlot = kUnresolvedVirtualSlot;
thread_local size_t gBorderColorRightSlot = kUnresolvedVirtualSlot;
thread_local size_t gBorderColorTopSlot = kUnresolvedVirtualSlot;
thread_local size_t gBorderColorBottomSlot = kUnresolvedVirtualSlot;

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

void SetDiagnostics(
    CaptureDiagnostics* const diagnostics,
    const CaptureStatus status) noexcept {
    if (diagnostics != nullptr) {
        diagnostics->status = status;
    }
}

CaptureStatus Fail(
    EffectivePropertySink& sink,
    CaptureDiagnostics* const diagnostics,
    const CaptureStatus status) noexcept {
    sink.Abort();
    SetDiagnostics(diagnostics, status);
    return status;
}

bool MissingMember(const HRESULT status) noexcept {
    return status == DISP_E_UNKNOWNNAME ||
        status == DISP_E_MEMBERNOTFOUND ||
        status == DISP_E_BADINDEX;
}

}

void ResetEffectivePropertyVirtualSlotCache() noexcept {
    gHParameterSetSlot = kUnresolvedVirtualSlot;
    gHActionSlot = kUnresolvedVirtualSlot;
    gBorderColorLeftSlot = kUnresolvedVirtualSlot;
    gBorderColorRightSlot = kUnresolvedVirtualSlot;
    gBorderColorTopSlot = kUnresolvedVirtualSlot;
    gBorderColorBottomSlot = kUnresolvedVirtualSlot;
}

namespace {

HRESULT DispatchProperty(
    IDispatch* const object,
    const wchar_t* const name,
    CComPtr<IDispatch>& value) noexcept {
#if defined(_M_IX86)
    size_t* cachedSlot = nullptr;
    size_t expectedSlot = 0;
    if (object != nullptr && name != nullptr) {
        if (std::wcscmp(name, L"HAction") == 0) {
            cachedSlot = &gHActionSlot;
            expectedSlot = kHActionSlot;
        } else if (std::wcscmp(name, L"HParameterSet") == 0) {
            cachedSlot = &gHParameterSetSlot;
            expectedSlot = kHParameterSetSlot;
        }
    }
    if (cachedSlot != nullptr) {
        if (*cachedSlot == kUnresolvedVirtualSlot) {
            CComPtr<IUnknown> interfaceObject;
            size_t resolvedSlot = 0;
            if (SUCCEEDED(
                    hancom::official_api::ResolveVirtualPropertyGet(
                        object, name, VT_DISPATCH, interfaceObject,
                        &resolvedSlot)) &&
                resolvedSlot == expectedSlot) {
                *cachedSlot = resolvedSlot;
            }
        }
        if (*cachedSlot != kUnresolvedVirtualSlot) {
            CComPtr<IDispatch> directValue;
            if (SUCCEEDED(
                    hancom::official_api::InvokeResolvedVirtualPropertyGet(
                        object,
                        kRootVirtualInterfaceId,
                        *cachedSlot,
                        directValue))) {
                value = directValue;
                hancom::inspection::NoteTableDirectVirtualPropertyGet();
                return S_OK;
            }
        }
    }
#endif
    CComVariant raw;
    const HRESULT status = PropertyGet(object, name, &raw);
    return FAILED(status) ? status : AsDispatch(raw, value);
}

HRESULT ScalarPropertyGet(
    IDispatch* const object,
    const wchar_t* const name,
    CComVariant* const value) noexcept {
    if (object == nullptr || name == nullptr || value == nullptr) {
        return E_POINTER;
    }
#if defined(_M_IX86)
    size_t* cachedSlot = nullptr;
    size_t expectedSlot = 0;
    if (std::wcscmp(name, L"BorderCorlorLeft") == 0) {
        cachedSlot = &gBorderColorLeftSlot;
        expectedSlot = kBorderColorLeftSlot;
    } else if (std::wcscmp(name, L"BorderColorRight") == 0) {
        cachedSlot = &gBorderColorRightSlot;
        expectedSlot = kBorderColorRightSlot;
    } else if (std::wcscmp(name, L"BorderColorTop") == 0) {
        cachedSlot = &gBorderColorTopSlot;
        expectedSlot = kBorderColorTopSlot;
    } else if (std::wcscmp(name, L"BorderColorBottom") == 0) {
        cachedSlot = &gBorderColorBottomSlot;
        expectedSlot = kBorderColorBottomSlot;
    }
    if (cachedSlot != nullptr) {
        size_t resolvedSlot = *cachedSlot;
        if (resolvedSlot == kUnresolvedVirtualSlot) {
            CComPtr<IUnknown> interfaceObject;
            if (FAILED(
                    hancom::official_api::ResolveVirtualPropertyGet(
                        object, name, VT_UI4, interfaceObject,
                        &resolvedSlot)) ||
                resolvedSlot != expectedSlot) {
                resolvedSlot = kUnresolvedVirtualSlot;
            }
        }
        if (resolvedSlot != kUnresolvedVirtualSlot) {
            ULONG directValue = 0;
            HRESULT directStatus =
                hancom::official_api::
                    InvokeResolvedVirtualUnsignedLongPropertyGet(
                        object,
                        kCellBorderFillVirtualInterfaceId,
                        resolvedSlot,
                        &directValue);
            if (FAILED(directStatus)) {
                directStatus =
                    hancom::official_api::
                        InvokeResolvedVirtualUnsignedLongPropertyGet(
                            object,
                            kBorderFillVirtualInterfaceId,
                            resolvedSlot,
                            &directValue);
            }
            if (SUCCEEDED(directStatus)) {
                const HRESULT cleared = value->Clear();
                if (FAILED(cleared)) {
                    return cleared;
                }
                value->vt = VT_UI4;
                value->ulVal = directValue;
                *cachedSlot = resolvedSlot;
                hancom::inspection::NoteTableDirectVirtualPropertyGet();
                return S_OK;
            }
        }
    }
#endif
    return PropertyGet(object, name, value);
}

enum class Profile : std::uint8_t {
    Unsupported = 0,
    Character,
    Paragraph,
    Tab,
    Page,
    Column,
    BorderFill,
};

bool ContextNodeKind(
    const EffectivePropertyContext& context,
    NodeKind* const node) noexcept {
    switch (context.target) {
    case capture::PropertyTarget::Document:
        *node = NodeKind::Document;
        return true;
    case capture::PropertyTarget::Section:
        *node = NodeKind::Section;
        return true;
    case capture::PropertyTarget::Run:
        *node = NodeKind::CharacterRun;
        return true;
    case capture::PropertyTarget::Paragraph:
        *node = NodeKind::Paragraph;
        return true;
    case capture::PropertyTarget::Control:
        *node = NodeKind::GenericControl;
        return true;
    case capture::PropertyTarget::Table:
        *node = NodeKind::Table;
        return true;
    case capture::PropertyTarget::Cell:
        *node = NodeKind::TableCell;
        return true;
    case capture::PropertyTarget::Image:
        *node = NodeKind::Image;
        return true;
    case capture::PropertyTarget::Definition:
        *node = NodeKind::Definition;
        return context.hasDefinitionKind;
    case capture::PropertyTarget::Story:
        *node = NodeKind::Story;
        return true;
    }
    return false;
}

bool ApplicableOwnerField(
    const EffectivePropertyContext& context,
    const PropertyRule& property,
    FieldTag* const ownerField) noexcept {
    NodeKind node = NodeKind::Document;
    const PropertyApplicabilityRule* const applicability =
        FindPropertyApplicabilityRule(property.applicabilitySet);
    if (ownerField == nullptr || applicability == nullptr ||
        !ContextNodeKind(context, &node)) {
        return false;
    }
    for (std::size_t index = 0; index < applicability->tupleCount; ++index) {
        const PropertyApplicabilityTuple& tuple = applicability->tuples[index];
        if (tuple.node != node ||
            (tuple.definitionRestricted &&
             (!context.hasDefinitionKind ||
              tuple.definitionKind != context.definitionKind))) {
            continue;
        }
        *ownerField = tuple.ownerNodeFieldTag;
        return true;
    }
    return false;
}

Profile ProfileFor(const PropertyApplicabilitySet set) noexcept {
    switch (set) {
        case PropertyApplicabilitySet::CharacterShape:
            return Profile::Character;
        case PropertyApplicabilitySet::ParagraphShape:
            return Profile::Paragraph;
        case PropertyApplicabilitySet::TabDefDefinition:
            return Profile::Tab;
        case PropertyApplicabilitySet::PageDefDefinition:
            return Profile::Page;
        case PropertyApplicabilitySet::ColumnDefDefinition:
            return Profile::Column;
        case PropertyApplicabilitySet::BorderFillDefinition:
            return Profile::BorderFill;
        default:
            return Profile::Unsupported;
    }
}

HRESULT PropertyAtNativePath(
    IDispatch* const root,
    const wchar_t* const path,
    const wchar_t* const leafOverride,
    CComVariant* const value) noexcept {
    if (root == nullptr || path == nullptr || value == nullptr) {
        return E_POINTER;
    }
    const wchar_t* component = std::wcschr(path, L'.');
    if (component == nullptr || component[1] == L'\0') {
        return E_INVALIDARG;
    }
    ++component;
    CComPtr<IDispatch> owner = root;
    for (const wchar_t* separator = std::wcschr(component, L'.');
         separator != nullptr;
         separator = std::wcschr(component, L'.')) {
        const std::wstring member(component, separator);
        CComPtr<IDispatch> nested;
        const HRESULT status = DispatchProperty(
            owner, member.c_str(), nested);
        if (FAILED(status)) {
            return status;
        }
        owner = nested;
        component = separator + 1;
    }
    return ScalarPropertyGet(
        owner,
        leafOverride == nullptr ? component : leafOverride,
        value);
}

bool LongValue(const CComVariant& raw, LONG* const value) noexcept {
    if (value == nullptr) {
        return false;
    }
    CComVariant converted;
    if (FAILED(VariantChangeType(
            &converted,
            const_cast<VARIANT*>(static_cast<const VARIANT*>(&raw)),
            0,
            VT_I4))) {
        return false;
    }
    *value = converted.lVal;
    return true;
}

PropertyOrigin NativePropertyOrigin(
    IDispatch* const hwp,
    const wchar_t* const nativeMemberPath) noexcept {
    CComVariant raw;
    const HRESULT status = Method(
        hwp, L"PropertyOrigin", {CComVariant(nativeMemberPath)}, &raw);
    LONG encoded = 0;
    if (FAILED(status) || !LongValue(raw, &encoded)) {
        return PropertyOrigin::Unavailable;
    }
    switch (encoded) {
    case 0: return PropertyOrigin::UserOverride;
    case 1: return PropertyOrigin::LocalStyle;
    case 2: return PropertyOrigin::NamedStyle;
    case 3: return PropertyOrigin::DocumentDefault;
    case 4: return PropertyOrigin::ImplicitDefault;
    case 5: return PropertyOrigin::Inherited;
    default: return PropertyOrigin::Unavailable;
    }
}

bool CanonicalValue(
    const CComVariant& raw,
    const ScalarTag scalar,
    std::wstring* const value) {
    std::wostringstream output;
    switch (scalar) {
        case ScalarTag::Bool: {
            bool converted = false;
            if (FAILED(AsBool(raw, &converted))) {
                return false;
            }
            output << L"b:" << (converted ? 1 : 0);
            break;
        }
        case ScalarTag::UTF16: {
            std::wstring converted;
            if (FAILED(AsString(raw, &converted))) {
                return false;
            }
            output << L"s:" << converted;
            break;
        }
        case ScalarTag::Float64: {
            CComVariant converted;
            if (FAILED(VariantChangeType(
                    &converted,
                    const_cast<VARIANT*>(
                        static_cast<const VARIANT*>(&raw)),
                    0,
                    VT_R8))) {
                return false;
            }
            output << L"f:" << std::setprecision(17) << converted.dblVal;
            break;
        }
        case ScalarTag::Uint64:
        case ScalarTag::BGR:
        case ScalarTag::RawURC32:
        case ScalarTag::Uint8:
        case ScalarTag::Uint16:
        case ScalarTag::Uint32: {
            CComVariant converted;
            if (FAILED(VariantChangeType(
                    &converted,
                    const_cast<VARIANT*>(
                        static_cast<const VARIANT*>(&raw)),
                    0,
                    VT_UI8))) {
                return false;
            }
            output << L"u:" << converted.ullVal;
            break;
        }
        case ScalarTag::Sint64:
        case ScalarTag::HWPUNIT64:
        case ScalarTag::Enum:
        case ScalarTag::Sint32: {
            CComVariant converted;
            if (FAILED(VariantChangeType(
                    &converted,
                    const_cast<VARIANT*>(
                        static_cast<const VARIANT*>(&raw)),
                    0,
                    VT_I8))) {
                return false;
            }
            output << L"i:" << converted.llVal;
            break;
        }
        default:
            return false;
    }
    *value = output.str();
    return true;
}

struct NativeReadState final {
    com_state::DocumentRoute route{};
    com_state::Position cursor{};
    com_state::Selection selection{};
    bool modified = false;
};

bool CaptureNativeReadState(
    IDispatch* const hwp,
    NativeReadState* const state) noexcept {
    if (hwp == nullptr || state == nullptr ||
        !com_state::CaptureDocumentRoute(hwp, &state->route)) {
        return false;
    }
    com_state::SelectionCaptureFailure selectionFailure;
    CComVariant modifiedRaw;
    return SUCCEEDED(com_state::CapturePosition(hwp, &state->cursor)) &&
        com_state::CaptureSelection(
            hwp, &state->selection,
            com_state::SelectionCapturePolicy::RequiredControl,
            &selectionFailure) &&
        com_state::CanRestoreSelection(state->selection) &&
        SUCCEEDED(PropertyGet(hwp, L"IsModified", &modifiedRaw)) &&
        SUCCEEDED(AsBool(modifiedRaw, &state->modified));
}

bool RestoreNativeReadState(
    IDispatch* const hwp,
    const NativeReadState& state) noexcept {
    if (!com_state::RestoreDocumentRoute(hwp, state.route) ||
        !com_state::RestoreSelection(hwp, state.cursor, state.selection) ||
        !com_state::VerifyDocumentRoute(hwp, state.route)) {
        return false;
    }
    com_state::Position restoredCursor;
    com_state::Selection restoredSelection;
    com_state::SelectionCaptureFailure selectionFailure;
    CComVariant restoredModifiedRaw;
    bool restoredModified = !state.modified;
    return SUCCEEDED(com_state::CapturePosition(hwp, &restoredCursor)) &&
        com_state::SamePosition(state.cursor, restoredCursor) &&
        com_state::CaptureSelection(
            hwp, &restoredSelection,
            com_state::SelectionCapturePolicy::RequiredControl,
            &selectionFailure) &&
        com_state::SameSelection(state.selection, restoredSelection) &&
        SUCCEEDED(PropertyGet(
            hwp, L"IsModified", &restoredModifiedRaw)) &&
        SUCCEEDED(AsBool(restoredModifiedRaw, &restoredModified)) &&
        restoredModified == state.modified;
}

class ComEffectivePropertySource final : public EffectivePropertySource {
public:
    explicit ComEffectivePropertySource(
        IDispatch* const hwp,
        IDispatch* const action = nullptr,
        IDispatch* const parameterSets = nullptr,
        IDispatch* const borderParameter = nullptr,
        IDispatch* const borderSet = nullptr) noexcept
        : hwp_(hwp), action_(action), parameterSets_(parameterSets),
          borderParameter_(borderParameter), borderSet_(borderSet) {}

    ReadStatus Read(
        const EffectivePropertyContext& context,
        const PropertyRule& rule,
        PropertyObservation* const observation) noexcept override {
        FieldTag ownerField = 0;
        if (!ApplicableOwnerField(context, rule, &ownerField)) {
            return ReadStatus::NotApplicable;
        }
        const Profile profile = ProfileFor(rule.applicabilitySet);
        if (profile == Profile::Unsupported ||
            (rule.valueShape != PropertyValueShape::Scalar &&
             !HasQualifier(
                 rule.qualifiers,
                 QualifierFlags::OwnerQualificationRequired))) {
            return ReadStatus::NotExposed;
        }
        PreparedProfile* prepared = Prepare(profile);
        if (prepared == nullptr || prepared->target == nullptr) {
            return prepared != nullptr ? prepared->status : ReadStatus::ReadFailed;
        }
        if (rule.valueShape != PropertyValueShape::Scalar) {
            const wchar_t* const separator =
                std::wcsrchr(rule.nativeMemberPath, L'.');
            if (separator == nullptr || separator[1] == L'\0') {
                return ReadStatus::ReadFailed;
            }
            std::wstring member(separator + 1);
            if (member.size() >= 2 &&
                member.compare(member.size() - 2, 2, L"[]") == 0) {
                member.resize(member.size() - 2);
            }
            CComVariant raw;
            const HRESULT status = PropertyAtNativePath(
                prepared->target, rule.nativeMemberPath, member.c_str(), &raw);
            if (MissingMember(status)) return ReadStatus::NotExposed;
            return ReadStatus::ReadFailed;
        }
        if (HasQualifier(
                rule.qualifiers,
                QualifierFlags::WindowsBrushFillCondition)) {
            CComVariant typeRaw;
            const HRESULT typeStatus = PropertyAtNativePath(
                prepared->target, rule.nativeMemberPath, L"Type", &typeRaw);
            if (MissingMember(typeStatus)) {
                return ReadStatus::NotExposed;
            }
            LONG fillType = 0;
            if (FAILED(typeStatus) || !LongValue(typeRaw, &fillType)) {
                return ReadStatus::ReadFailed;
            }
            CComVariant bitRaw;
            const HRESULT bitStatus = Method(
                hwp_, L"BrushType", {CComVariant(L"WinBrush")}, &bitRaw);
            LONG windowsBrushBit = 0;
            if (MissingMember(bitStatus)) return ReadStatus::NotExposed;
            if (FAILED(bitStatus) ||
                !LongValue(bitRaw, &windowsBrushBit) ||
                windowsBrushBit == 0) {
                return ReadStatus::ReadFailed;
            }
            if ((fillType & windowsBrushBit) == 0) {
                return ReadStatus::NotApplicable;
            }
        }
        CComVariant raw;
        const HRESULT status = PropertyAtNativePath(
            prepared->target, rule.nativeMemberPath, nullptr, &raw);
        if (MissingMember(status)) return ReadStatus::NotExposed;
        if (FAILED(status)) return ReadStatus::ReadFailed;
        if (!CanonicalValue(raw, rule.scalar, &observation->canonicalValue)) {
            return ReadStatus::ReadFailed;
        }
        observation->origin = NativePropertyOrigin(
            hwp_, rule.nativeMemberPath);
        return ReadStatus::Value;
    }

private:
    struct PreparedProfile final {
        bool attempted = false;
        ReadStatus status = ReadStatus::ReadFailed;
        CComPtr<IDispatch> target{};
    };

    PreparedProfile* Prepare(const Profile profile) noexcept {
        PreparedProfile* prepared = profile == Profile::Character
            ? &character_
            : profile == Profile::Paragraph
            ? &paragraph_
            : profile == Profile::Tab
            ? &tab_
            : profile == Profile::Page
            ? &page_
            : profile == Profile::Column
            ? &column_
            : &borderFill_;
        if (prepared->attempted) return prepared;
        prepared->attempted = true;
        CComPtr<IDispatch> action = action_;
        CComPtr<IDispatch> parameterSets = parameterSets_;
        HRESULT status = action != nullptr
            ? S_OK : DispatchProperty(hwp_, L"HAction", action);
        if (FAILED(status)) {
            prepared->status = MissingMember(status)
                ? ReadStatus::NotExposed : ReadStatus::ReadFailed;
            return prepared;
        }
        status = parameterSets != nullptr
            ? S_OK
            : DispatchProperty(hwp_, L"HParameterSet", parameterSets);
        if (FAILED(status)) {
            prepared->status = MissingMember(status)
                ? ReadStatus::NotExposed : ReadStatus::ReadFailed;
            return prepared;
        }
        const wchar_t* const parameterName = profile == Profile::Character
            ? L"HCharShape"
            : profile == Profile::Page
            ? L"HSecDef"
            : profile == Profile::Column
            ? L"HColDef"
            : profile == Profile::BorderFill
            ? L"HCellBorderFill"
            : L"HParaShape";
        const wchar_t* const actionName = profile == Profile::Character
            ? L"CharShape"
            : profile == Profile::Paragraph
            ? L"ParagraphShape"
            : profile == Profile::Page
            ? L"PageSetup"
            : profile == Profile::Column
            ? L"MultiColumn"
            : profile == Profile::BorderFill
            ? L"CellBorderFill"
            : L"BulletDlg";
        CComPtr<IDispatch> parameter = profile == Profile::BorderFill
            ? borderParameter_ : nullptr;
        CComPtr<IDispatch> set = profile == Profile::BorderFill
            ? borderSet_ : nullptr;
        status = parameter != nullptr
            ? S_OK
            : DispatchProperty(parameterSets, parameterName, parameter);
        if (FAILED(status)) {
            prepared->status = MissingMember(status)
                ? ReadStatus::NotExposed : ReadStatus::ReadFailed;
            return prepared;
        }
        status = set != nullptr
            ? S_OK : DispatchProperty(parameter, L"HSet", set);
        if (FAILED(status)) {
            prepared->status = MissingMember(status)
                ? ReadStatus::NotExposed : ReadStatus::ReadFailed;
            return prepared;
        }
        CComVariant ignored;
        std::vector<CComVariant> arguments;
        arguments.emplace_back(CComBSTR(actionName));
        arguments.emplace_back();
        arguments.back().vt = VT_DISPATCH;
        arguments.back().pdispVal = set;
        arguments.back().pdispVal->AddRef();
        status = Method(action, L"GetDefault", arguments, &ignored);
        if (FAILED(status)) {
            prepared->status = MissingMember(status)
                ? ReadStatus::NotExposed : ReadStatus::ReadFailed;
            return prepared;
        }
        const wchar_t* ownerMember = profile == Profile::Tab
            ? L"TabDef"
            : profile == Profile::Page
            ? L"PageDef"
            : profile == Profile::BorderFill
            ? L"SelCellsBorderFill"
            : nullptr;
        if (ownerMember != nullptr) {
            status = DispatchProperty(
                parameter, ownerMember, prepared->target);
            if (FAILED(status)) {
                prepared->status = MissingMember(status)
                    ? ReadStatus::NotExposed : ReadStatus::ReadFailed;
                return prepared;
            }
        } else {
            prepared->target = parameter;
        }
        prepared->status = prepared->target != nullptr
            ? ReadStatus::Value : ReadStatus::ReadFailed;
        return prepared;
    }

    CComPtr<IDispatch> hwp_{};
    CComPtr<IDispatch> action_{};
    CComPtr<IDispatch> parameterSets_{};
    CComPtr<IDispatch> borderParameter_{};
    CComPtr<IDispatch> borderSet_{};
    PreparedProfile character_{};
    PreparedProfile paragraph_{};
    PreparedProfile tab_{};
    PreparedProfile page_{};
    PreparedProfile column_{};
    PreparedProfile borderFill_{};
};

HRESULT PreparedActionParameter(
    IDispatch* const hwp,
    const wchar_t* const parameterName,
    const wchar_t* const actionName,
    CComPtr<IDispatch>& parameter) noexcept {
    CComPtr<IDispatch> action;
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> set;
    if (FAILED(DispatchProperty(hwp, L"HAction", action)) ||
        FAILED(DispatchProperty(hwp, L"HParameterSet", parameterSets)) ||
        FAILED(DispatchProperty(parameterSets, parameterName, parameter)) ||
        FAILED(DispatchProperty(parameter, L"HSet", set))) {
        return E_FAIL;
    }
    CComVariant ignored;
    std::vector<CComVariant> arguments;
    arguments.emplace_back(CComBSTR(actionName));
    arguments.emplace_back();
    arguments.back().vt = VT_DISPATCH;
    arguments.back().pdispVal = set;
    arguments.back().pdispVal->AddRef();
    return Method(action, L"GetDefault", arguments, &ignored);
}

ReadStatus ReadIntegerMember(
    IDispatch* const object,
    const wchar_t* const name,
    std::int64_t* const value) noexcept {
    CComVariant raw;
    const HRESULT status = ScalarPropertyGet(object, name, &raw);
    if (MissingMember(status)) {
        return ReadStatus::NotExposed;
    }
    CComVariant converted;
    if (FAILED(status) || FAILED(VariantChangeType(
            &converted, &raw, 0, VT_I8))) {
        return ReadStatus::ReadFailed;
    }
    *value = converted.llVal;
    return ReadStatus::Value;
}

codec::Bytes SemanticValueBytes(const PropertyObservation& property) {
    const std::wstring& value = property.canonicalValue;
    const wchar_t* number = value.c_str();
    if (value.size() > 2 && value[1] == L':') {
        number += 2;
    }
    switch (property.scalar) {
    case ScalarTag::UTF16:
        return codec::Utf16(
            reinterpret_cast<const std::uint16_t*>(
                (value.rfind(L"s:", 0) == 0 ? value.data() + 2 : value.data())),
            value.size() - (value.rfind(L"s:", 0) == 0 ? 2 : 0));
    case ScalarTag::Bool:
        return codec::Bool(_wcstoi64(number, nullptr, 10) != 0);
    case ScalarTag::Uint8:
        return codec::Uint8(static_cast<std::uint8_t>(
            _wcstoui64(number, nullptr, 10)));
    case ScalarTag::Uint16:
        return codec::Uint16(static_cast<std::uint16_t>(
            _wcstoui64(number, nullptr, 10)));
    case ScalarTag::Uint32:
    case ScalarTag::BGR:
        return codec::Uint32(static_cast<std::uint32_t>(
            _wcstoui64(number, nullptr, 10)));
    case ScalarTag::Uint64:
        return codec::Uint64(_wcstoui64(number, nullptr, 10));
    case ScalarTag::Sint32:
        return codec::Sint32(static_cast<std::int32_t>(
            _wcstoi64(number, nullptr, 10)));
    case ScalarTag::RawURC32:
        return codec::Uint32(static_cast<std::uint32_t>(
            _wcstoui64(number, nullptr, 10)));
    case ScalarTag::Enum:
        return codec::Enum(_wcstoi64(number, nullptr, 10), nullptr, 0);
    default:
        return codec::Sint64(_wcstoi64(number, nullptr, 10));
    }
}

PropertyOrigin ObservationOrigin(const PropertyKeyId key) noexcept {
    const PropertyRule* const rule = FindPropertyRule(key);
    if (rule == nullptr) return PropertyOrigin::Unknown;
    if (rule->origin == RegistryOrigin::Direct) return PropertyOrigin::Direct;
    if (rule->origin == RegistryOrigin::Generated) {
        return PropertyOrigin::Generated;
    }
    return PropertyOrigin::Unknown;
}

Sha256 DefinitionAggregate(
    const std::vector<PropertyObservation>& properties) {
    std::vector<codec::SemanticPropertyInput> semantic;
    for (const PropertyObservation& property : properties) {
        if (property.status != ReadStatus::Value) {
            continue;
        }
        semantic.push_back({
            property.key,
            ObservationOrigin(property.key),
            SemanticValueBytes(property),
        });
    }
    return codec::DefinitionPropertyAggregate(std::move(semantic));
}

std::uint64_t ReferenceWallNanoseconds() noexcept {
    LARGE_INTEGER counter{};
    LARGE_INTEGER frequency{};
    if (!QueryPerformanceCounter(&counter) ||
        !QueryPerformanceFrequency(&frequency) || frequency.QuadPart <= 0) {
        return 0;
    }
    const std::uint64_t whole = static_cast<std::uint64_t>(
        counter.QuadPart / frequency.QuadPart);
    const std::uint64_t remainder = static_cast<std::uint64_t>(
        counter.QuadPart % frequency.QuadPart);
    return whole * UINT64_C(1000000000) +
        remainder * UINT64_C(1000000000) /
            static_cast<std::uint64_t>(frequency.QuadPart);
}

class ComReferenceClosureSource final : public ReferenceClosureSource {
public:
    ComReferenceClosureSource(
        IDispatch* const hwp,
        ReferenceClosureDiagnostics* const diagnostics) noexcept
        : hwp_(hwp), diagnostics_(diagnostics) {}

    bool ReadSite(
        const ReferenceSite& site,
        ReferenceSiteObservation* const observation) noexcept override {
        if (PositionIndependent(site.target)) {
            return ReadPositionedSite(site, observation);
        }
        if (site.target != capture::PropertyTarget::Cell) {
            const com_state::PositionResult positioned =
                com_state::ApplyPosition(
                    hwp_,
                    {static_cast<LONG>(site.position.list),
                     static_cast<LONG>(site.position.paragraph),
                     static_cast<LONG>(site.position.character)},
                    com_state::EmptyPositionResult::Reject);
            return positioned.positioned &&
                ReadPositionedSite(site, observation);
        }
        if (observation == nullptr) return false;
        if (diagnostics_ != nullptr) ++diagnostics_->cellSites;
        const size_t separator = site.identity.rfind(L':');
        if (separator == std::wstring::npos ||
            separator + 1 == site.identity.size()) {
            return false;
        }
        const std::wstring expectedAddress =
            site.identity.substr(separator + 1);

        const std::uint64_t positionStart = ReferenceWallNanoseconds();
        const com_state::PositionResult positioned = com_state::ApplyPosition(
            hwp_,
            {static_cast<LONG>(site.position.list),
             static_cast<LONG>(site.position.paragraph),
             static_cast<LONG>(site.position.character)},
            com_state::EmptyPositionResult::Reject);
        if (diagnostics_ != nullptr) {
            diagnostics_->cellPositionWallNanoseconds +=
                ReferenceWallNanoseconds() - positionStart;
        }
        if (!positioned.positioned) return false;

        const std::uint64_t proofStart = ReferenceWallNanoseconds();
        std::wstring actualAddress;
        LONG mode = -1;
        if (diagnostics_ != nullptr) {
            ++diagnostics_->cellAddressReads;
            ++diagnostics_->cellModeReads;
        }
        const bool exactAddress = com_state::ReadCurrentCellAddress(
                hwp_, &actualAddress) && actualAddress == expectedAddress;
        const bool exactOwner = exactAddress &&
            VerifyCurrentTableOwner(site.tableOwnerIdentity);
        const bool modeRead = com_state::ReadSelectionMode(hwp_, &mode);
        if (diagnostics_ != nullptr) {
            diagnostics_->cellFastProofWallNanoseconds +=
                ReferenceWallNanoseconds() - proofStart;
        }
        // Exact mode 0 proves that no selected-cell range can affect
        // CellBorderFill; the exact address and parent instance bind the HSet
        // to this caret cell. Every other state must use selected-cell
        // qualification before reading the action parameter set.
        if (exactOwner && modeRead && mode == com_state::kSelectionNone) {
            if (diagnostics_ != nullptr) {
                ++diagnostics_->cellFastMode0Attempts;
                ++diagnostics_->cellFastGetDefaultCalls;
            }
            ReferenceSiteObservation provisional;
            const std::uint64_t readStart = ReferenceWallNanoseconds();
            const bool read = ReadPositionedSite(site, &provisional);
            if (diagnostics_ != nullptr) {
                diagnostics_->cellFastReadWallNanoseconds +=
                    ReferenceWallNanoseconds() - readStart;
            }
            if (read && IsStructurallyCompleteTerminalCellBorder(provisional)) {
                *observation = std::move(provisional);
                if (diagnostics_ != nullptr) {
                    ++diagnostics_->cellFastMode0Hits;
                }
                return true;
            }
            if (diagnostics_ != nullptr) {
                ++diagnostics_->cellFastMode0Rejections;
                ++diagnostics_->cellDiscardedProvisionalSets;
            }
        } else if (diagnostics_ != nullptr) {
            ++diagnostics_->cellFastMode0Rejections;
        }

        if (diagnostics_ != nullptr) {
            ++diagnostics_->cellQualifiedFallbackCalls;
            ++diagnostics_->cellFallbackSetPosCalls;
        }
        const std::uint64_t fallbackStart = ReferenceWallNanoseconds();
        const auto accountFallbackWall = [this, fallbackStart]() noexcept {
            if (diagnostics_ != nullptr) {
                diagnostics_->cellFallbackWallNanoseconds +=
                    ReferenceWallNanoseconds() - fallbackStart;
            }
        };
        const com_state::PositionResult fallbackPosition =
            com_state::ApplyPosition(
                hwp_,
                {static_cast<LONG>(site.position.list),
                 static_cast<LONG>(site.position.paragraph),
                 static_cast<LONG>(site.position.character)},
                com_state::EmptyPositionResult::Reject);
        if (!fallbackPosition.positioned) {
            accountFallbackWall();
            return false;
        }
        if (diagnostics_ != nullptr) {
            ++diagnostics_->cellTableCellBlockCalls;
            ++diagnostics_->cellFallbackAddressReads;
            ++diagnostics_->cellFallbackModeReads;
        }
        if (!com_state::QualifyCurrentTableCellSelection(
                hwp_, expectedAddress)) {
            accountFallbackWall();
            return false;
        }
        if (diagnostics_ != nullptr) {
            ++diagnostics_->cellFallbackGetDefaultCalls;
        }
        const bool fallbackRead = ReadPositionedSite(site, observation);
        accountFallbackWall();
        return fallbackRead;
    }

    static bool PositionIndependent(
        const capture::PropertyTarget target) noexcept {
        return target == capture::PropertyTarget::Control ||
            target == capture::PropertyTarget::Table ||
            target == capture::PropertyTarget::Image;
    }

    bool ReadPositionedSite(
        const ReferenceSite& site,
        ReferenceSiteObservation* const observation) noexcept {
        if (observation == nullptr) {
            return false;
        }
        if (site.target == capture::PropertyTarget::Section) {
            const size_t page = AddDefinition(
                DefinitionKind::PageDef,
                0,
                ReadStatus::NotExposed,
                PropertyApplicabilitySet::PageDefDefinition,
                observation);
            const size_t column = AddDefinition(
                DefinitionKind::ColumnDef,
                0,
                ReadStatus::NotExposed,
                PropertyApplicabilitySet::ColumnDefDefinition,
                observation);
            observation->references.push_back(
                {EdgeKind::PageDefRef, page, site.sectionOrdinal});
            observation->references.push_back(
                {EdgeKind::ColumnDefRef, column, site.sectionOrdinal});
            return true;
        }
        if (site.target == capture::PropertyTarget::Run) {
            const size_t characterShape = AddDefinition(
                DefinitionKind::CharacterShape,
                0,
                ReadStatus::NotExposed,
                PropertyApplicabilitySet::CharacterShape,
                observation);
            const ReferencedDefinition& definition =
                observation->definitions[characterShape];
            for (const PropertyObservation& definitionProperty :
                 definition.properties) {
                PropertyObservation runProperty = definitionProperty;
                runProperty.target = capture::PropertyTarget::Run;
                runProperty.targetIdentity = site.identity;
                runProperty.ownerField = 102;
                observation->properties.push_back(std::move(runProperty));
            }
            observation->references.push_back(
                {EdgeKind::CharacterShapeRef, characterShape, 0});
            capture::CoverageObservation coverage;
            coverage.target = capture::PropertyTarget::Run;
            coverage.targetIdentity = site.identity;
            coverage.coordinate = CoverageCoordinateKind::NodeField;
            coverage.ownerField = 102;
            coverage.detail = L"CharacterShapeReferenceClosure";
            coverage.profile = ProfileId::EditableText;
            coverage.state = CoverageState::Complete;
            observation->coverageFacts.push_back(std::move(coverage));
            return true;
        }
        const auto terminalFamily = [&site, observation](
            const wchar_t* const family,
            const FieldTag ownerField,
            const ProfileId profile,
            const CoverageState state) {
            capture::CoverageObservation coverage;
            coverage.target = site.target;
            coverage.targetIdentity = site.identity;
            coverage.ownerField = ownerField;
            coverage.detail = family;
            coverage.profile = profile;
            coverage.state = state;
            observation->coverageFacts.push_back(std::move(coverage));
        };
        if (site.target == capture::PropertyTarget::Control) {
            terminalFamily(
                L"BorderFillReference", 106,
                ProfileId::EditableObjects, CoverageState::NotExposed);
            return true;
        }
        if (site.target == capture::PropertyTarget::Table) {
            terminalFamily(
                L"DefinitionReferenceFamilies", 106,
                ProfileId::EditableObjects, CoverageState::NotApplicable);
            return true;
        }
        if (site.target == capture::PropertyTarget::Image) {
            terminalFamily(
                L"AssetReference", 110,
                ProfileId::BinaryContent, CoverageState::NotExposed);
            return true;
        }
        if (site.target == capture::PropertyTarget::Cell) {
            const size_t border = AddDefinition(
                DefinitionKind::BorderFill,
                0,
                ReadStatus::NotExposed,
                PropertyApplicabilitySet::BorderFillDefinition,
                observation);
            observation->references.push_back(
                {EdgeKind::BorderFillRef, border, 0});
            return true;
        }
        if (site.target != capture::PropertyTarget::Paragraph) {
            return false;
        }
        CComPtr<IDispatch> styleParameter;
        std::int64_t styleId = 0;
        ReadStatus styleStatus = ReadStatus::ReadFailed;
        if (SUCCEEDED(PreparedActionParameter(
                hwp_, L"HStyle", L"Style", styleParameter))) {
            styleStatus = ReadIntegerMember(
                styleParameter, L"Apply", &styleId);
        }
        ReferencedDefinition style;
        style.kind = DefinitionKind::Style;
        style.nativeId = styleId;
        style.nativeIdStatus = styleStatus;
        style.bodyStatus = ReadStatus::NotExposed;
        observation->definitions.push_back(std::move(style));
        observation->references.push_back({EdgeKind::StyleRef, 0, 0});
        const size_t paragraph = AddDefinition(
            DefinitionKind::ParagraphShape,
            0,
            ReadStatus::NotExposed,
            PropertyApplicabilitySet::ParagraphShape,
            observation);
        observation->references.push_back(
            {EdgeKind::ParagraphShapeRef, paragraph, 0});

        CComPtr<IDispatch> paragraphParameter;
        if (SUCCEEDED(PreparedActionParameter(
                hwp_, L"HParaShape", L"ParagraphShape",
                paragraphParameter))) {
            AddOptionalIdDefinition(
                paragraphParameter, L"TabDef", DefinitionKind::TabDef,
                EdgeKind::TabDefRef, observation);
            std::int64_t headingType = 0;
            const ReadStatus headingStatus = ReadIntegerMember(
                paragraphParameter, L"HeadingType", &headingType);
            if (headingStatus == ReadStatus::Value && headingType == 2) {
                AddOptionalIdDefinition(
                    paragraphParameter, L"Numbering",
                    DefinitionKind::Numbering, EdgeKind::NumberingRef,
                    observation);
            } else if (headingStatus == ReadStatus::Value &&
                       headingType == 3) {
                // The schema represents bullet catalogs in the Numbering
                // definition family; the native Bullet ID remains explicit.
                AddOptionalIdDefinition(
                    paragraphParameter, L"Bullet",
                    DefinitionKind::Numbering, EdgeKind::NumberingRef,
                    observation);
            }
        }
        return true;
    }

private:
    bool VerifyCurrentTableOwner(
        const std::wstring& expectedIdentity) const noexcept {
        if (expectedIdentity.empty()) return false;
        std::vector<std::wstring> fields;
        size_t begin = 0;
        for (size_t separator = expectedIdentity.find(L':');;
             separator = expectedIdentity.find(L':', begin)) {
            if (separator == std::wstring::npos) {
                fields.push_back(expectedIdentity.substr(begin));
                break;
            }
            fields.push_back(expectedIdentity.substr(begin, separator - begin));
            begin = separator + 1;
        }
        if (fields.size() != 8 ||
            fields[0] != std::to_wstring(static_cast<unsigned>(
                capture::PropertyTarget::Table)) ||
            fields[1] != L"tbl" || fields[2] != L"1" ||
            fields[7].empty()) {
            return false;
        }
        CComVariant rawOwner;
        CComPtr<IDispatch> owner;
        CComVariant rawType;
        CComVariant rawInstance;
        std::wstring type;
        std::wstring instance;
        return SUCCEEDED(PropertyGet(hwp_, L"ParentCtrl", &rawOwner)) &&
            SUCCEEDED(AsDispatch(rawOwner, owner)) &&
            SUCCEEDED(PropertyGet(owner, L"CtrlID", &rawType)) &&
            SUCCEEDED(AsString(rawType, &type)) && type == fields[1] &&
            SUCCEEDED(Method(owner, L"GetCtrlInstID", {}, &rawInstance)) &&
            SUCCEEDED(AsString(rawInstance, &instance)) &&
            instance == fields[7];
    }

    size_t AddDefinition(
        const DefinitionKind kind,
        const std::int64_t nativeId,
        const ReadStatus nativeIdStatus,
        const PropertyApplicabilitySet applicability,
        ReferenceSiteObservation* const output) noexcept {
        ReferencedDefinition definition;
        definition.kind = kind;
        definition.nativeId = nativeId;
        definition.nativeIdStatus = nativeIdStatus;
        definition.bodyStatus = ReadStatus::Value;
        if (kind == DefinitionKind::BorderFill) {
            if (action_ == nullptr)
                static_cast<void>(DispatchProperty(hwp_, L"HAction", action_));
            if (parameterSets_ == nullptr)
                static_cast<void>(DispatchProperty(
                    hwp_, L"HParameterSet", parameterSets_));
            if (borderParameter_ == nullptr && parameterSets_ != nullptr)
                static_cast<void>(DispatchProperty(
                    parameterSets_, L"HCellBorderFill", borderParameter_));
            if (borderSet_ == nullptr && borderParameter_ != nullptr)
                static_cast<void>(DispatchProperty(
                    borderParameter_, L"HSet", borderSet_));
        }
        ComEffectivePropertySource source(
            hwp_, action_, parameterSets_, borderParameter_, borderSet_);
        const EffectivePropertyContext context{
            capture::PropertyTarget::Definition,
            L"",
            true,
            kind,
        };
        bool anyValue = false;
        bool allFailed = true;
        for (const PropertyRule& rule : kPropertyRegistryV1) {
            if (rule.applicabilitySet != applicability) {
                continue;
            }
            PropertyObservation property;
            property.target = context.target;
            property.ownerField = 102;
            property.key = rule.id;
            property.scalar = rule.scalar;
            property.status = source.Read(context, rule, &property);
            if (property.status == ReadStatus::Value &&
                rule.origin == RegistryOrigin::Direct) {
                property.origin = PropertyOrigin::Direct;
            } else if (property.status == ReadStatus::NotApplicable) {
                property.origin = PropertyOrigin::NotApplicable;
            } else if (property.status != ReadStatus::Value) {
                property.origin = PropertyOrigin::Unavailable;
            }
            anyValue = anyValue || property.status == ReadStatus::Value;
            allFailed = allFailed && property.status == ReadStatus::ReadFailed;
            definition.properties.push_back(std::move(property));
        }
        if (!anyValue && allFailed) {
            definition.bodyStatus = ReadStatus::ReadFailed;
        } else if (!anyValue) {
            definition.bodyStatus = ReadStatus::NotExposed;
        }
        output->definitions.push_back(std::move(definition));
        return output->definitions.size() - 1;
    }

    void AddOptionalIdDefinition(
        IDispatch* const paragraph,
        const wchar_t* const member,
        const DefinitionKind kind,
        const EdgeKind edge,
        ReferenceSiteObservation* const output) noexcept {
        std::int64_t id = 0;
        const ReadStatus status = ReadIntegerMember(paragraph, member, &id);
        if (status == ReadStatus::NotApplicable ||
            (status == ReadStatus::Value && id < 0)) {
            return;
        }
        ReferencedDefinition definition;
        definition.kind = kind;
        definition.nativeId = id;
        definition.nativeIdStatus = status;
        definition.bodyStatus = ReadStatus::NotExposed;
        output->definitions.push_back(std::move(definition));
        output->references.push_back(
            {edge, output->definitions.size() - 1, 0});
    }

    CComPtr<IDispatch> hwp_{};
    ReferenceClosureDiagnostics* diagnostics_ = nullptr;
    CComPtr<IDispatch> action_{};
    CComPtr<IDispatch> parameterSets_{};
    CComPtr<IDispatch> borderParameter_{};
    CComPtr<IDispatch> borderSet_{};
};

} // namespace

bool IsStructurallyCompleteTerminalCellBorder(
    const ReferenceSiteObservation& observation) noexcept {
    if (observation.definitions.size() != 1 ||
        observation.references.size() != 1 ||
        !observation.properties.empty() ||
        !observation.coverageFacts.empty()) {
        return false;
    }
    const SiteReference& reference = observation.references.front();
    if (reference.edge != EdgeKind::BorderFillRef ||
        reference.definition != 0 || reference.ordinal != 0) {
        return false;
    }
    const ReferencedDefinition& definition = observation.definitions.front();
    if (definition.kind != DefinitionKind::BorderFill ||
        definition.nativeId != 0 ||
        definition.nativeIdStatus != ReadStatus::NotExposed ||
        definition.bodyStatus != ReadStatus::Value ||
        definition.properties.size() != 16) {
        return false;
    }
    const auto exactCanonicalScalar = [](
        const PropertyObservation& property) noexcept {
        const bool unsignedValue = property.scalar == ScalarTag::BGR ||
            property.scalar == ScalarTag::Uint8;
        const wchar_t prefix = unsignedValue ? L'u' : L'i';
        const std::wstring& value = property.canonicalValue;
        if (value.size() < 3 || value[0] != prefix || value[1] != L':') {
            return false;
        }
        size_t offset = 2;
        if (!unsignedValue && value[offset] == L'-') {
            if (++offset == value.size()) return false;
        }
        for (; offset != value.size(); ++offset) {
            if (value[offset] < L'0' || value[offset] > L'9') return false;
        }
        wchar_t* end = nullptr;
        errno = 0;
        if (unsignedValue) {
            const unsigned long long parsed = std::wcstoull(
                value.c_str() + 2, &end, 10);
            const unsigned long long maximum =
                property.scalar == ScalarTag::Uint8 ? 0xffULL : 0xffffffffULL;
            return errno != ERANGE && end == value.c_str() + value.size() &&
                parsed <= maximum;
        }
        static_cast<void>(std::wcstoll(value.c_str() + 2, &end, 10));
        return errno != ERANGE && end == value.c_str() + value.size();
    };
    bool seen[16]{};
    for (const PropertyObservation& property : definition.properties) {
        if (property.key < 5000 || property.key > 5015) return false;
        const size_t index = property.key - 5000;
        const PropertyRule* const rule = FindPropertyRule(property.key);
        if (seen[index] || rule == nullptr ||
            rule->domain != PropertyDomain::BorderFill ||
            rule->applicabilitySet !=
                PropertyApplicabilitySet::BorderFillDefinition ||
            rule->valueShape != PropertyValueShape::Scalar ||
            rule->origin != RegistryOrigin::Direct ||
            property.scalar != rule->scalar ||
            property.target != capture::PropertyTarget::Definition ||
            !property.targetIdentity.empty() || property.ownerField != 102) {
            return false;
        }
        seen[index] = true;
        if (property.key != 5015) {
            if (property.status != ReadStatus::Value ||
                property.origin != PropertyOrigin::Direct ||
                !exactCanonicalScalar(property)) {
                return false;
            }
            continue;
        }
        if (!HasQualifier(
                rule->qualifiers,
                QualifierFlags::WindowsBrushFillCondition)) {
            return false;
        }
        const bool exactNotApplicable =
            property.status == ReadStatus::NotApplicable &&
            property.origin == PropertyOrigin::NotApplicable &&
            property.canonicalValue.empty();
        const bool exactQualifiedValue =
            property.status == ReadStatus::Value &&
            property.origin == PropertyOrigin::Direct &&
            exactCanonicalScalar(property);
        if (!exactNotApplicable && !exactQualifiedValue) return false;
    }
    return std::all_of(std::begin(seen), std::end(seen),
                       [](const bool present) { return present; });
}

CaptureStatus CaptureEffectiveProperties(
    const EffectivePropertyContext& context,
    EffectivePropertySource& source,
    EffectivePropertySink& sink,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (diagnostics != nullptr) {
        *diagnostics = {};
    }
    try {
        for (const PropertyRule& rule : kPropertyRegistryV1) {
            PropertyObservation observation;
            observation.target = context.target;
            observation.targetIdentity = context.targetIdentity;
            observation.key = rule.id;
            observation.scalar = rule.scalar;
            if (!ApplicableOwnerField(context, rule, &observation.ownerField)) {
                observation.status = ReadStatus::NotApplicable;
                observation.origin = PropertyOrigin::NotApplicable;
            } else {
                observation.status = source.Read(context, rule, &observation);
                if (observation.status == ReadStatus::NotApplicable) {
                    observation.origin = PropertyOrigin::NotApplicable;
                } else if (observation.status != ReadStatus::Value) {
                    observation.origin = PropertyOrigin::Unavailable;
                }
            }
            if (observation.status == ReadStatus::Value &&
                observation.canonicalValue.empty()) {
                return Fail(
                    sink,
                    diagnostics,
                    CaptureStatus::SourceFailed);
            }
            if (!sink.Append(observation)) {
                return Fail(
                    sink,
                    diagnostics,
                    CaptureStatus::SinkFailed);
            }
            if (diagnostics != nullptr) {
                switch (observation.status) {
                    case ReadStatus::Value:
                        ++diagnostics->valueCount;
                        break;
                    case ReadStatus::NotApplicable:
                        ++diagnostics->notApplicableCount;
                        break;
                    case ReadStatus::NotExposed:
                        ++diagnostics->notExposedCount;
                        break;
                    case ReadStatus::ReadFailed:
                        ++diagnostics->readFailedCount;
                        break;
                }
            }
        }
        CatalogCoverage coverage;
        if (!sink.MarkCatalogCoverage(coverage)) {
            return Fail(
                sink,
                diagnostics,
                CaptureStatus::SinkFailed);
        }
        if (diagnostics != nullptr) {
            diagnostics->catalogCoverageEmitted = true;
        }
        if (!sink.Commit()) {
            return Fail(
                sink,
                diagnostics,
                CaptureStatus::SinkFailed);
        }
        SetDiagnostics(diagnostics, CaptureStatus::Complete);
        return CaptureStatus::Complete;
    } catch (const std::bad_alloc&) {
        return Fail(
            sink,
            diagnostics,
            CaptureStatus::ResourceExhausted);
    } catch (...) {
        return Fail(
            sink,
            diagnostics,
            CaptureStatus::SourceFailed);
    }
}

ReadStatus ReadCurrentEffectiveProperty(
    IDispatch* const hwp,
    const EffectivePropertyContext& context,
    const PropertyRule& rule,
    PropertyObservation* const observation) noexcept {
    if (hwp == nullptr || observation == nullptr) {
        return ReadStatus::ReadFailed;
    }
    *observation = {};
    observation->target = context.target;
    observation->targetIdentity = context.targetIdentity;
    observation->key = rule.id;
    observation->scalar = rule.scalar;
    if (!ApplicableOwnerField(context, rule, &observation->ownerField)) {
        observation->status = ReadStatus::NotApplicable;
        observation->origin = PropertyOrigin::NotApplicable;
        return observation->status;
    }
    ComEffectivePropertySource source(hwp);
    observation->status = source.Read(context, rule, observation);
    if (observation->status != ReadStatus::Value) {
        observation->origin = observation->status == ReadStatus::NotApplicable
            ? PropertyOrigin::NotApplicable
            : PropertyOrigin::Unavailable;
        observation->canonicalValue.clear();
    }
    return observation->status;
}

CaptureStatus CaptureCurrentEffectiveProperties(
    IDispatch* const hwp,
    const EffectivePropertyContext& context,
    EffectivePropertySink& sink,
    CaptureDiagnostics* const diagnostics) noexcept {
    if (hwp == nullptr) {
        if (diagnostics != nullptr) {
            *diagnostics = {};
        }
        SetDiagnostics(diagnostics, CaptureStatus::InvalidArgument);
        return CaptureStatus::InvalidArgument;
    }
    ComEffectivePropertySource source(hwp);
    return CaptureEffectiveProperties(context, source, sink, diagnostics);
}

CaptureStatus CaptureCurrentReferenceClosure(
    IDispatch* const hwp,
    const std::vector<ReferenceSite>& sites,
    capture::ReaderPayload* const output,
    ReferenceClosureDiagnostics* const diagnostics) noexcept {
    if (hwp == nullptr || output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    NativeReadState state;
    if (!CaptureNativeReadState(hwp, &state)) {
        if (diagnostics != nullptr) {
            *diagnostics = {};
            diagnostics->status = CaptureStatus::SourceFailed;
        }
        return CaptureStatus::SourceFailed;
    }
    capture::ReaderPayload staged;
    CaptureStatus status = CaptureStatus::SourceFailed;
    try {
        staged = *output;
        ComReferenceClosureSource source(hwp, diagnostics);
        status = CaptureReferenceClosure(
            source, sites, &staged, diagnostics);
    } catch (const std::bad_alloc&) {
        status = CaptureStatus::ResourceExhausted;
    } catch (...) {
        status = CaptureStatus::SourceFailed;
    }
    if (!RestoreNativeReadState(hwp, state)) {
        if (diagnostics != nullptr) {
            diagnostics->status = CaptureStatus::SourceFailed;
        }
        return CaptureStatus::SourceFailed;
    }
    if (status == CaptureStatus::Complete) {
        *output = std::move(staged);
    } else if (diagnostics != nullptr) {
        diagnostics->status = status;
    }
    return status;
}

bool DerivePageSetupDigest(
    const capture::ReaderPayload& referenceClosure,
    Sha256* const digest) noexcept {
    if (digest == nullptr) {
        return false;
    }
    try {
        const auto appendFrame = [](codec::Bytes* const destination,
                                    const codec::Bytes& field) {
            const codec::Bytes length = codec::Uint64(field.size());
            destination->insert(
                destination->end(), length.begin(), length.end());
            destination->insert(
                destination->end(), field.begin(), field.end());
        };
        std::vector<const capture::DefinitionObservation*> definitions;
        for (const auto& definition : referenceClosure.definitions) {
            if ((definition.kind == DefinitionKind::PageDef ||
                 definition.kind == DefinitionKind::ColumnDef) &&
                definition.bodyState == ObservationState::Value) {
                definitions.push_back(&definition);
            }
        }
        std::sort(definitions.begin(), definitions.end(),
            [](const auto* left, const auto* right) {
                return left->kind < right->kind ||
                    (left->kind == right->kind &&
                     left->propertyDigest.bytes < right->propertyDigest.bytes);
            });
        codec::Bytes definitionBytes;
        appendFrame(&definitionBytes, codec::Uint64(definitions.size()));
        for (const auto* definition : definitions) {
            codec::Bytes entry = codec::Uint16(
                static_cast<std::uint16_t>(definition->kind));
            std::vector<codec::SemanticPropertyInput> properties;
            for (const auto& property : definition->properties) {
                if (property.state != ObservationState::Value) continue;
                properties.push_back({property.key, property.origin,
                    property.scalar == ScalarTag::UTF16
                        ? codec::Utf16(
                            reinterpret_cast<const std::uint16_t*>(
                                property.textValue.data()),
                            property.textValue.size())
                        : SemanticValueBytes({property.key, property.scalar,
                            ReadStatus::Value,
                            L"i:" + std::to_wstring(property.integerValue),
                            PropertyOrigin::Unavailable})});
            }
            std::sort(properties.begin(), properties.end(),
                [](const auto& left, const auto& right) {
                    return left.key < right.key;
                });
            appendFrame(&entry, codec::Uint64(properties.size()));
            for (const auto& property : properties) {
                appendFrame(&entry, codec::SemanticPropertyBytes(property));
            }
            appendFrame(&definitionBytes, entry);
        }
        struct OrderedReference final {
            const capture::DefinitionReferenceObservation* reference;
            size_t definitionOrdinal;
        };
        std::vector<OrderedReference> references;
        bool page = false;
        bool column = false;
        for (const auto& reference : referenceClosure.definitionReferences) {
            if (reference.edge != EdgeKind::PageDefRef &&
                reference.edge != EdgeKind::ColumnDefRef) continue;
            const auto found = std::find_if(
                definitions.begin(), definitions.end(),
                [&reference](const auto* candidate) {
                    return candidate->identity == reference.definitionIdentity;
                });
            if (found == definitions.end()) return false;
            page = page || reference.edge == EdgeKind::PageDefRef;
            column = column || reference.edge == EdgeKind::ColumnDefRef;
            references.push_back({
                &reference,
                static_cast<size_t>(found - definitions.begin()),
            });
        }
        std::sort(references.begin(), references.end(),
            [](const OrderedReference& left, const OrderedReference& right) {
                return left.reference->ordinal < right.reference->ordinal ||
                    (left.reference->ordinal == right.reference->ordinal &&
                     left.reference->edge < right.reference->edge);
            });
        if (!page || !column) return false;
        codec::Bytes referenceBytes;
        appendFrame(&referenceBytes, codec::Uint64(references.size()));
        for (const OrderedReference& item : references) {
            codec::Bytes entry;
            appendFrame(&entry, codec::Uint64(item.reference->ordinal));
            codec::ReferenceInput descriptor;
            descriptor.edge = item.reference->edge;
            descriptor.targetKind = NodeKind::Definition;
            descriptor.ordinalTarget = true;
            descriptor.targetOrdinal = item.definitionOrdinal;
            appendFrame(
                &entry, codec::ReferenceDescriptor(descriptor));
            appendFrame(&referenceBytes, entry);
        }
        codec::Bytes canonical;
        appendFrame(&canonical, definitionBytes);
        appendFrame(&canonical, referenceBytes);
        *digest = codec::DomainHash(
            "HWPGRAPH\0PAGESETUP\0V1", codec::View(canonical));
        return true;
    } catch (...) {
        return false;
    }
}

bool AccountCaptionReferenceTerminals(
    const capture::CaptureIdentityArena& captureArena,
    const std::vector<capture::ImageObservation>& images,
    capture::ReaderPayload* const output,
    ReferenceClosureDiagnostics* const diagnostics) noexcept {
    if (output == nullptr) {
        return false;
    }
    for (const capture::ImageObservation& image : images) {
        if (image.text[2].state == ObservationState::Value &&
            image.captionList.state == ObservationState::Value &&
            !capture::IsQualifiedNativeCaptionLocation(
                captureArena, image)) {
            return false;
        }
    }
    try {
        for (const capture::ImageObservation& image : images) {
            if (image.text[2].state != ObservationState::Value) {
                continue;
            }
            if (image.captionList.state == ObservationState::NotExposed ||
                image.captionList.state == ObservationState::ReadFailed) {
                continue;
            }
            if (image.captionList.state != ObservationState::Value ||
                !capture::IsQualifiedNativeCaptionLocation(
                    captureArena, image) ||
                image.captionList.value <= 0 ||
                image.captionStart.list != image.captionList.value ||
                image.captionStart.paragraph < 0 ||
                image.captionStart.character < 0) {
                return false;
            }
            const std::int64_t list = image.captionList.value;
            const std::wstring paragraphIdentity =
                std::to_wstring(list) + L":" +
                std::to_wstring(image.captionStart.paragraph);
            const std::wstring runIdentity = paragraphIdentity + L":" +
                std::to_wstring(image.captionStart.character) + L":" +
                std::to_wstring(
                    image.captionStart.character +
                    static_cast<std::int64_t>(image.text[2].value.size()));
            const auto terminal = [output](
                const capture::PropertyTarget target,
                std::wstring identity,
                const FieldTag field) {
                capture::CoverageObservation coverage;
                coverage.target = target;
                coverage.targetIdentity = std::move(identity);
                coverage.coordinate = CoverageCoordinateKind::NodeField;
                coverage.ownerField = field;
                coverage.detail = L"CaptionReferenceFamilyNotExposed";
                coverage.profile = ProfileId::EditableText;
                coverage.state = CoverageState::NotExposed;
                output->coverageFacts.push_back(std::move(coverage));
            };
            terminal(capture::PropertyTarget::Story,
                     std::to_wstring(list), 100);
            terminal(capture::PropertyTarget::Paragraph,
                     paragraphIdentity, 102);
            terminal(capture::PropertyTarget::Run,
                     runIdentity, 102);
            output->referenceTraversal.expectedSites += 3;
            output->referenceTraversal.visitedSites += 3;
            if (diagnostics != nullptr) {
                diagnostics->expectedSites += 3;
                diagnostics->visitedSites += 3;
            }
        }
        return true;
    } catch (...) {
        return false;
    }
}

CaptureStatus CaptureReferenceClosure(
    ReferenceClosureSource& source,
    const std::vector<ReferenceSite>& sites,
    capture::ReaderPayload* const output,
    ReferenceClosureDiagnostics* const diagnostics) noexcept {
    if (diagnostics != nullptr) {
        *diagnostics = {};
        diagnostics->expectedSites = sites.size();
    }
    if (output == nullptr || sites.empty()) {
        return CaptureStatus::InvalidArgument;
    }
    try {
        const auto observationState = [](const ReadStatus status) {
            switch (status) {
            case ReadStatus::Value: return ObservationState::Value;
            case ReadStatus::NotApplicable:
                return ObservationState::NotApplicable;
            case ReadStatus::NotExposed:
                return ObservationState::NotExposed;
            case ReadStatus::ReadFailed:
                return ObservationState::ReadFailed;
            }
            return ObservationState::ReadFailed;
        };
        const auto coverageState = [](const ReadStatus status) {
            switch (status) {
            case ReadStatus::Value: return CoverageState::Complete;
            case ReadStatus::NotApplicable:
                return CoverageState::NotApplicable;
            case ReadStatus::NotExposed:
                return CoverageState::NotExposed;
            case ReadStatus::ReadFailed:
                return CoverageState::ReadFailed;
            }
            return CoverageState::ReadFailed;
        };
        output->definitions.clear();
        output->definitionReferences.clear();
        output->properties.erase(
            std::remove_if(
                output->properties.begin(), output->properties.end(),
                [](const capture::PropertyObservation& property) {
                    return property.target == capture::PropertyTarget::Run;
                }),
            output->properties.end());
        output->coverageFacts.clear();
        output->referenceTraversal = {};
        output->referenceTraversal.expectedSites = sites.size();
        output->referenceTraversal.globalStyleCatalogNotExposed = true;
        output->referenceTraversal.globalNumberingCatalogNotExposed = true;
        output->referenceTraversal.globalBulletCatalogNotExposed = true;
        output->referenceTraversal.globalTabDefCatalogNotExposed = true;
        if (diagnostics != nullptr) {
            for (const ReferenceSite& site : sites) {
                const size_t target = static_cast<size_t>(site.target);
                if (target < diagnostics->sitesByTarget.size()) {
                    ++diagnostics->sitesByTarget[target];
                }
            }
        }
        for (const ReferenceSite& site : sites) {
            if (site.identity.empty() ||
                site.target == capture::PropertyTarget::Document ||
                site.target == capture::PropertyTarget::Definition) {
                return CaptureStatus::SourceFailed;
            }
            ReferenceSiteObservation observed;
            if (!source.ReadSite(site, &observed)) {
                return CaptureStatus::SourceFailed;
            }
            std::vector<size_t> remap(observed.definitions.size());
            for (size_t index = 0; index < observed.definitions.size(); ++index) {
                const ReferencedDefinition& sourceDefinition =
                    observed.definitions[index];
                const Sha256 digest = DefinitionAggregate(
                    sourceDefinition.properties);
                for (const capture::DefinitionObservation& prior :
                     output->definitions) {
                    if (prior.kind == sourceDefinition.kind &&
                        prior.nativeIdState == ObservationState::Value &&
                        sourceDefinition.nativeIdStatus == ReadStatus::Value &&
                        prior.nativeId == sourceDefinition.nativeId &&
                        prior.propertyDigest.bytes != digest.bytes) {
                        return CaptureStatus::SourceFailed;
                    }
                }
                std::wostringstream identity;
                identity << static_cast<unsigned>(sourceDefinition.kind)
                         << L':' << (sourceDefinition.nativeIdStatus ==
                                ReadStatus::Value ? L"id:" : L"absent:");
                if (sourceDefinition.nativeIdStatus == ReadStatus::Value) {
                    identity << sourceDefinition.nativeId << L':';
                } else {
                    identity << static_cast<unsigned>(
                        sourceDefinition.nativeIdStatus) << L':';
                }
                identity << std::hex << std::setfill(L'0');
                for (const std::uint8_t byte : digest.bytes) {
                    identity << std::setw(2) << static_cast<unsigned>(byte);
                }
                const std::wstring key = identity.str();
                const auto existing = std::find_if(
                    output->definitions.begin(), output->definitions.end(),
                    [&key](const capture::DefinitionObservation& definition) {
                        return definition.identity == key;
                    });
                if (existing != output->definitions.end()) {
                    if (existing->nativeIdState != observationState(
                            sourceDefinition.nativeIdStatus) ||
                        existing->bodyState != observationState(
                            sourceDefinition.bodyStatus)) {
                        return CaptureStatus::SourceFailed;
                    }
                    remap[index] = static_cast<size_t>(
                        existing - output->definitions.begin());
                    continue;
                }
                capture::DefinitionObservation definition;
                definition.kind = sourceDefinition.kind;
                definition.nativeId = sourceDefinition.nativeId;
                definition.nativeIdState = observationState(
                    sourceDefinition.nativeIdStatus);
                definition.bodyState = observationState(
                    sourceDefinition.bodyStatus);
                definition.identity = key;
                definition.propertyDigest = digest;
                for (const PropertyObservation& property :
                     sourceDefinition.properties) {
                    capture::PropertyObservation captured;
                    captured.target = capture::PropertyTarget::Definition;
                    captured.targetIdentity = key;
                    captured.ownerField = 102;
                    captured.key = property.key;
                    captured.scalar = property.scalar;
                    captured.state = observationState(property.status);
                    captured.origin = property.status == ReadStatus::Value
                        ? ObservationOrigin(property.key)
                        : property.status == ReadStatus::NotApplicable
                            ? PropertyOrigin::NotApplicable
                            : PropertyOrigin::Unavailable;
                    if (captured.state == ObservationState::Value) {
                        const std::wstring& value = property.canonicalValue;
                        if (captured.scalar == ScalarTag::UTF16) {
                            captured.textValue = value.rfind(L"s:", 0) == 0
                                ? value.substr(2) : value;
                        } else {
                            const wchar_t* number = value.c_str();
                            if (value.size() > 2 && value[1] == L':') {
                                number += 2;
                            }
                            captured.integerValue = _wcstoi64(
                                number, nullptr, 10);
                        }
                    }
                    definition.properties.push_back(std::move(captured));
                }
                remap[index] = output->definitions.size();
                output->definitions.push_back(std::move(definition));
                if (sourceDefinition.bodyStatus != ReadStatus::Value) {
                    capture::CoverageObservation bodyCoverage;
                    bodyCoverage.target = capture::PropertyTarget::Definition;
                    bodyCoverage.targetIdentity = key;
                    bodyCoverage.ownerField = 102;
                    bodyCoverage.profile = OwningProfile(
                        sourceDefinition.kind);
                    bodyCoverage.state = coverageState(
                        sourceDefinition.bodyStatus);
                    output->coverageFacts.push_back(
                        std::move(bodyCoverage));
                }
            }
            const std::wstring& ownerIdentity = site.ownerIdentity.empty()
                ? site.identity : site.ownerIdentity;
            for (const PropertyObservation& property : observed.properties) {
                if (property.target != site.target ||
                    property.targetIdentity != site.identity ||
                    property.ownerField == 0) {
                    return CaptureStatus::SourceFailed;
                }
                capture::PropertyObservation captured;
                captured.target = property.target;
                captured.targetIdentity = ownerIdentity;
                captured.ownerField = property.ownerField;
                captured.key = property.key;
                captured.scalar = property.scalar;
                captured.state = observationState(property.status);
                captured.origin = property.status == ReadStatus::Value
                    ? property.origin
                    : property.status == ReadStatus::NotApplicable
                        ? PropertyOrigin::NotApplicable
                        : PropertyOrigin::Unavailable;
                if (captured.state == ObservationState::Value) {
                    const std::wstring& value = property.canonicalValue;
                    if (captured.scalar == ScalarTag::UTF16) {
                        captured.textValue = value.rfind(L"s:", 0) == 0
                            ? value.substr(2) : value;
                    } else {
                        const wchar_t* number = value.c_str();
                        if (value.size() > 2 && value[1] == L':') {
                            number += 2;
                        }
                        captured.integerValue = _wcstoi64(
                            number, nullptr, 10);
                    }
                }
                output->properties.push_back(std::move(captured));
            }
            for (capture::CoverageObservation& coverage :
                 observed.coverageFacts) {
                coverage.targetIdentity = ownerIdentity;
            }
            output->coverageFacts.insert(
                output->coverageFacts.end(),
                std::make_move_iterator(observed.coverageFacts.begin()),
                std::make_move_iterator(observed.coverageFacts.end()));
            for (const SiteReference& reference : observed.references) {
                if (reference.definition >= remap.size()) {
                    return CaptureStatus::SourceFailed;
                }
                capture::DefinitionReferenceObservation captured;
                captured.source = site.target;
                captured.sourceIdentity = ownerIdentity;
                captured.edge = reference.edge;
                captured.definitionIdentity =
                    output->definitions[remap[reference.definition]].identity;
                captured.ordinal = reference.ordinal;
                output->definitionReferences.push_back(std::move(captured));
            }
            ++output->referenceTraversal.visitedSites;
            if (diagnostics != nullptr) {
                ++diagnostics->visitedSites;
            }
        }
        if (output->referenceTraversal.visitedSites != sites.size()) {
            return CaptureStatus::SourceFailed;
        }
        std::sort(
            output->definitions.begin(), output->definitions.end(),
            [](const capture::DefinitionObservation& left,
               const capture::DefinitionObservation& right) {
                const bool leftPresent =
                    left.nativeIdState == ObservationState::Value;
                const bool rightPresent =
                    right.nativeIdState == ObservationState::Value;
                return codec::DefinitionOrderLess(
                    left.kind, leftPresent, left.nativeId,
                    left.propertyDigest, right.kind, rightPresent,
                    right.nativeId, right.propertyDigest);
            });
        for (const wchar_t* const name : {
                 L"StyleGlobalCatalog", L"NumberingGlobalCatalog",
                 L"BulletGlobalCatalog", L"TabDefGlobalCatalog"}) {
            capture::CoverageObservation catalog;
            catalog.target = capture::PropertyTarget::Document;
            catalog.coordinate = CoverageCoordinateKind::Global;
            catalog.ownerField = 0;
            catalog.detail = name;
            catalog.profile = static_cast<ProfileId>(kNoProfile);
            catalog.state = CoverageState::NotExposed;
            output->coverageFacts.push_back(std::move(catalog));
        }
        if (diagnostics != nullptr) {
            diagnostics->status = CaptureStatus::Complete;
            diagnostics->uniqueDefinitions = output->definitions.size();
            diagnostics->globalStyleCatalogNotExposed = true;
        }
        return CaptureStatus::Complete;
    } catch (const std::bad_alloc&) {
        return CaptureStatus::ResourceExhausted;
    } catch (...) {
        return CaptureStatus::SourceFailed;
    }
}

} // namespace hancom::graph::properties

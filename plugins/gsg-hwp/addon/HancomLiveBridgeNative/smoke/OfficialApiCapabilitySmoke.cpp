#include "../OfficialApiCapability.h"
#include "../OfficialApiProbe.h"
#include "../OfficialApiVirtualMethod.h"
#include "../TableInspection.h"
#include "../DocumentGraphEffectiveProperties.h"
#include "../DocumentGraphImages.h"
#include "../DocumentGraphLayout.h"
#include "../DocumentGraphQuery.h"
#include "../DocumentGraphProtocol.h"
#include "../DocumentGraphTables.h"

#include <atlbase.h>
#include <atlcom.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <new>
#include <sstream>
#include <string>
#include <vector>

bool DuplicateUnavailableTableStableKeyRecaptureSmoke(
    const std::vector<hancom::graph::capture::ControlObservation>& firstControls,
    const hancom::graph::capture::ReaderPayload& firstTables,
    const std::vector<hancom::graph::capture::ControlObservation>& secondControls,
    const hancom::graph::capture::ReaderPayload& secondTables);

bool DuplicateEmptyRealTableReaderRecaptureSmoke(
    const std::vector<hancom::graph::capture::ControlObservation>& firstControls,
    const hancom::graph::capture::ReaderPayload& firstTables,
    const std::vector<hancom::graph::capture::ControlObservation>& secondControls,
    const hancom::graph::capture::ReaderPayload& secondTables);
bool DuplicateEmptyRealTableReaderByteParitySmoke(
    const std::vector<hancom::graph::capture::ControlObservation>& firstControls,
    const hancom::graph::capture::ReaderPayload& firstTables,
    const std::vector<hancom::graph::capture::ControlObservation>& secondControls,
    const hancom::graph::capture::ReaderPayload& secondTables);

static_assert(static_cast<unsigned>(
    hancom::graph::capture::CaptureProgressPoint::CaptureBegin) == 0);
static_assert(static_cast<unsigned>(
    hancom::graph::capture::CaptureProgressPoint::TableControlCalls) == 63);
static_assert(static_cast<unsigned>(
    hancom::graph::capture::CaptureProgressPoint::CellReferenceFallbackWall) ==
    84);
static_assert(static_cast<unsigned>(
    hancom::graph::capture::CaptureProgressPoint::TableHeadCtrlAcquisition) ==
    85);
static_assert(static_cast<unsigned>(
    hancom::graph::capture::CaptureProgressPoint::TableReconciliationMerge) ==
    89);

namespace {

using hancom::official_api::capability::CallSpec;
using hancom::official_api::capability::RawCall;

class ATL_NO_VTABLE ScriptDispatch :
    public CComObjectRootEx<CComSingleThreadModel>,
    public IDispatch {
public:
    BEGIN_COM_MAP(ScriptDispatch)
        COM_INTERFACE_ENTRY(IDispatch)
    END_COM_MAP()

    STDMETHOD(GetTypeInfoCount)(UINT* count) override {
        if (count == nullptr) return E_POINTER;
        *count = 1;
        return S_OK;
    }
    STDMETHOD(GetTypeInfo)(UINT index, LCID lcid, ITypeInfo** result) override {
        if (index != 0) return DISP_E_BADINDEX;
        if (lcid != 0 || result == nullptr) return E_INVALIDARG;
        *result = nullptr;
        const GUID libraryId = {0x7D2B6F3C,0x1D95,0x4E0C,
            {0xBF,0x5A,0x5E,0xE5,0x64,0x18,0x6F,0xBC}};
        BSTR path = nullptr;
        HRESULT status = QueryPathOfRegTypeLib(libraryId,1,0,0,&path);
        CComPtr<ITypeLib> library;
        if (SUCCEEDED(status)) status = LoadTypeLibEx(path,REGKIND_NONE,&library);
        SysFreeString(path);
        if (FAILED(status)) return status;
        for (UINT type = 0; type < library->GetTypeInfoCount(); ++type) {
            BSTR name = nullptr;
            status = library->GetDocumentation(type,&name,nullptr,nullptr,nullptr);
            const bool matched = SUCCEEDED(status) && name != nullptr &&
                std::wstring(name,SysStringLen(name)) == L"IHwpObject";
            SysFreeString(name);
            if (matched) return library->GetTypeInfo(type,result);
        }
        return TYPE_E_ELEMENTNOTFOUND;
    }
    STDMETHOD(GetIDsOfNames)(REFIID iid, LPOLESTR* names, UINT count,
                             LCID lcid, DISPID* ids) override {
        ++getIdsCalls;
        if (iid != IID_NULL || lcid != 0 || count != 1 || names == nullptr ||
            ids == nullptr || std::wstring(names[0]) != expectedName) {
            return DISP_E_UNKNOWNNAME;
        }
        ids[0] = returnedDispid;
        return getIdsStatus;
    }
    STDMETHOD(Invoke)(DISPID member, REFIID iid, LCID lcid, WORD flags,
                      DISPPARAMS* parameters, VARIANT* result,
                      EXCEPINFO* exception, UINT* argumentError) override {
        ++invokeCalls;
        sawExactInvoke = member == expectedDispid && iid == IID_NULL &&
            lcid == 0 && flags == expectedFlags && parameters != nullptr &&
            parameters->cArgs == expectedArguments.size() &&
            parameters->cNamedArgs == 0;
        if (sawExactInvoke) {
            for (UINT index = 0; index < parameters->cArgs; ++index) {
                const VARIANTARG& actual = parameters->rgvarg[index];
                const VARIANTARG& expected = expectedArguments[index];
                if (actual.vt != expected.vt ||
                    (actual.vt == VT_I4 && actual.lVal != expected.lVal)) {
                    sawExactInvoke = false;
                }
            }
        }
        if (argumentError != nullptr) *argumentError = scriptedArgError;
        if (exception != nullptr && scriptedDescription != nullptr) {
            exception->bstrDescription = SysAllocString(scriptedDescription);
            exception->scode = scriptedScode;
        }
        if (result != nullptr && SUCCEEDED(invokeStatus)) {
            VariantCopy(result, &scriptedResult);
        }
        return invokeStatus;
    }

    std::wstring expectedName = L"MovePos";
    DISPID expectedDispid = 10016;
    DISPID returnedDispid = 10016;
    WORD expectedFlags = DISPATCH_METHOD;
    std::vector<VARIANTARG> expectedArguments;
    HRESULT getIdsStatus = S_OK;
    HRESULT invokeStatus = S_OK;
    UINT scriptedArgError = 0;
    const wchar_t* scriptedDescription = nullptr;
    SCODE scriptedScode = 0;
    VARIANT scriptedResult{};
    unsigned getIdsCalls = 0;
    unsigned invokeCalls = 0;
    bool sawExactInvoke = false;
};

struct GeometryEpochState final {
    struct Values final {
        LONG left = 0;
        LONG right = 0;
        LONG top = 0;
        LONG bottom = 0;
        LONG vertical = 0;
    };
    std::array<Values, 2> cells{{
        {0, 850, 566, 0, 0},
        {141, 0, 141, 0, 1},
    }};
    LONG current = 0;
    bool forwarding = false;
    bool directCellShape = true;
    unsigned cellShapeAcquisitions = 0;
    unsigned shapeParameterAcquisitions = 0;
    unsigned shapeSetAcquisitions = 0;
    unsigned shapeTargetAcquisitions = 0;
    unsigned geometryDefaults = 0;
};

class GeometrySnapshotDispatch final : public IDispatch {
public:
    GeometrySnapshotDispatch(
        GeometryEpochState* const state,
        const LONG epoch) noexcept : state_(state), epoch_(epoch) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid, void** const object) override {
        if (object == nullptr) return E_POINTER;
        if (iid != IID_IUnknown && iid != IID_IDispatch) {
            *object = nullptr;
            return E_NOINTERFACE;
        }
        *object = static_cast<IDispatch*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }
    ULONG STDMETHODCALLTYPE Release() override {
        const LONG remaining = InterlockedDecrement(&references_);
        if (remaining == 0) delete this;
        return static_cast<ULONG>(remaining);
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* const count) override {
        if (count == nullptr) return E_POINTER;
        *count = 0;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT, LCID, ITypeInfo**) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID iid, LPOLESTR* names, UINT count, LCID,
        DISPID* ids) override {
        if (iid != IID_NULL || names == nullptr || ids == nullptr || count != 1)
            return E_INVALIDARG;
        static const std::map<std::wstring, DISPID> members{
            {L"Item", 1}, {L"HSet", 2}, {L"ShapeTableCell", 3},
            {L"MarginLeft", 4}, {L"MarginRight", 5},
            {L"MarginTop", 6}, {L"MarginBottom", 7},
            {L"VertAlign", 8},
        };
        const auto found = members.find(names[0]);
        if (found == members.end()) return DISP_E_UNKNOWNNAME;
        *ids = found->second;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID member, REFIID iid, LCID, WORD flags,
        DISPPARAMS* parameters, VARIANT* result, EXCEPINFO*, UINT*) override {
        if (iid != IID_NULL || result == nullptr) return E_INVALIDARG;
        VariantInit(result);
        if (member == 1 && (flags & DISPATCH_METHOD) != 0 &&
            parameters != nullptr && parameters->cArgs == 1 &&
            parameters->rgvarg[0].vt == VT_BSTR &&
            std::wstring_view(parameters->rgvarg[0].bstrVal) == L"Cell") {
            return DispatchResult(result);
        }
        if ((flags & DISPATCH_PROPERTYGET) == 0) return DISP_E_MEMBERNOTFOUND;
        if (member == 2) {
            ++state_->shapeSetAcquisitions;
            return DispatchResult(result);
        }
        if (member == 3) {
            ++state_->shapeTargetAcquisitions;
            return DispatchResult(result);
        }
        const LONG selected = state_->forwarding ? state_->current : epoch_;
        if (selected < 0 || selected >= static_cast<LONG>(state_->cells.size()))
            return DISP_E_BADINDEX;
        const GeometryEpochState::Values& cell =
            state_->cells[static_cast<size_t>(selected)];
        result->vt = VT_I4;
        result->lVal = member == 4 ? cell.left :
            member == 5 ? cell.right :
            member == 6 ? cell.top :
            member == 7 ? cell.bottom :
            member == 8 ? cell.vertical : -1;
        return member >= 4 && member <= 8 ? S_OK : DISP_E_MEMBERNOTFOUND;
    }

private:
    HRESULT DispatchResult(VARIANT* const result) noexcept {
        result->vt = VT_DISPATCH;
        result->pdispVal = this;
        AddRef();
        return S_OK;
    }
    ~GeometrySnapshotDispatch() = default;
    volatile LONG references_ = 1;
    GeometryEpochState* state_ = nullptr;
    LONG epoch_ = 0;
};

class GeometryEpochRootDispatch final : public IDispatch {
public:
    explicit GeometryEpochRootDispatch(GeometryEpochState* const state) noexcept
        : state_(state) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid, void** const object) override {
        if (object == nullptr) return E_POINTER;
        if (iid != IID_IUnknown && iid != IID_IDispatch) {
            *object = nullptr;
            return E_NOINTERFACE;
        }
        *object = static_cast<IDispatch*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }
    ULONG STDMETHODCALLTYPE Release() override {
        return static_cast<ULONG>(InterlockedDecrement(&references_));
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* const count) override {
        if (count == nullptr) return E_POINTER;
        *count = 0;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT, LCID, ITypeInfo**) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID iid, LPOLESTR* names, UINT count, LCID,
        DISPID* ids) override {
        if (iid != IID_NULL || names == nullptr || ids == nullptr || count != 1)
            return E_INVALIDARG;
        static const std::map<std::wstring, DISPID> members{
            {L"HAction", 1}, {L"HParameterSet", 2}, {L"SetPos", 3},
            {L"ParentCtrl", 4}, {L"GetCtrlInstID", 5},
            {L"CellShape", 6}, {L"HShapeObject", 7},
            {L"GetDefault", 8},
        };
        const auto found = members.find(names[0]);
        if (found == members.end()) return DISP_E_UNKNOWNNAME;
        *ids = found->second;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID member, REFIID iid, LCID, WORD flags,
        DISPPARAMS* parameters, VARIANT* result, EXCEPINFO*, UINT*) override {
        if (iid != IID_NULL) return E_INVALIDARG;
        if (result != nullptr) VariantInit(result);
        if ((flags & DISPATCH_PROPERTYGET) != 0) {
            if (result == nullptr) return E_POINTER;
            if (member == 1 || member == 2 || member == 4)
                return DispatchSelf(result);
            if (member == 6) {
                if (!state_->directCellShape) return DISP_E_MEMBERNOTFOUND;
                ++state_->cellShapeAcquisitions;
                return DispatchSnapshot(result);
            }
            if (member == 7) {
                ++state_->shapeParameterAcquisitions;
                return DispatchSnapshot(result);
            }
            return DISP_E_MEMBERNOTFOUND;
        }
        if ((flags & DISPATCH_METHOD) == 0) return DISP_E_MEMBERNOTFOUND;
        if (member == 3 && parameters != nullptr && parameters->cArgs == 3 &&
            parameters->rgvarg[2].vt == VT_I4) {
            const LONG list = parameters->rgvarg[2].lVal;
            if (list < 1 || list > 2) return DISP_E_BADINDEX;
            state_->current = list - 1;
            if (result != nullptr) {
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
            }
            return S_OK;
        }
        if (member == 5 && result != nullptr) {
            result->vt = VT_BSTR;
            result->bstrVal = SysAllocString(L"geometry-table");
            return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
        }
        if (member == 8) {
            ++state_->geometryDefaults;
            return S_OK;
        }
        return DISP_E_MEMBERNOTFOUND;
    }

private:
    HRESULT DispatchSelf(VARIANT* const result) noexcept {
        result->vt = VT_DISPATCH;
        result->pdispVal = this;
        AddRef();
        return S_OK;
    }
    HRESULT DispatchSnapshot(VARIANT* const result) noexcept {
        auto* const snapshot = new (std::nothrow)
            GeometrySnapshotDispatch(state_, state_->current);
        if (snapshot == nullptr) return E_OUTOFMEMORY;
        result->vt = VT_DISPATCH;
        result->pdispVal = snapshot;
        return S_OK;
    }
    volatile LONG references_ = 1;
    GeometryEpochState* state_ = nullptr;
};

enum class WorkflowRole {
    Root,
    Control,
    Table,
    Cell,
    Range,
    Parameter,
};

class WorkflowDispatch;

struct WorkflowHiddenDualProxy final {
    void** vtable = nullptr;
    WorkflowDispatch* owner = nullptr;
};

void** WorkflowHiddenDualVtable() noexcept;
void** WorkflowCellBorderFillVtable() noexcept;

class ATL_NO_VTABLE WorkflowDispatch :
    public CComObjectRootEx<CComSingleThreadModel>, public IDispatch {
public:
    WorkflowDispatch() noexcept
        : hiddenDual{WorkflowHiddenDualVtable(), this},
          cellBorderFillHiddenDual{
              WorkflowCellBorderFillVtable(), this} {}

    static HRESULT WINAPI QueryRootHiddenDual(
        void* object,
        REFIID iid,
        void** result,
        DWORD_PTR) noexcept;

    BEGIN_COM_MAP(WorkflowDispatch)
        COM_INTERFACE_ENTRY(IDispatch)
        COM_INTERFACE_ENTRY_FUNC_BLIND(0, QueryRootHiddenDual)
    END_COM_MAP()

    STDMETHOD(GetTypeInfoCount)(UINT* count) override {
        if (count == nullptr) return E_POINTER;
        *count = 1; return S_OK;
    }
    STDMETHOD(GetTypeInfo)(UINT index, LCID lcid, ITypeInfo** result) override {
        ++*typeInfoCalls;
        if (index != 0) return DISP_E_BADINDEX;
        if ((lcid != 0 && lcid != LOCALE_USER_DEFAULT) || result == nullptr)
            return E_INVALIDARG;
        *result = nullptr;
        const GUID libraryId = {0x7D2B6F3C,0x1D95,0x4E0C,
            {0xBF,0x5A,0x5E,0xE5,0x64,0x18,0x6F,0xBC}};
        BSTR path = nullptr;
        HRESULT status = QueryPathOfRegTypeLib(libraryId,1,0,0,&path);
        CComPtr<ITypeLib> library;
        if (SUCCEEDED(status)) status = LoadTypeLibEx(path,REGKIND_NONE,&library);
        SysFreeString(path);
        if (FAILED(status)) return status;
        const wchar_t* owner = role == WorkflowRole::Root ? L"IHwpObject" :
            role == WorkflowRole::Control ? L"IDHwpCtrlCode" :
            role == WorkflowRole::Table ? L"HTable" :
            role == WorkflowRole::Cell ? L"HCell" :
            role == WorkflowRole::Range ? L"HCellRangeRowCol" : L"IDHwpParameterSet";
        if (cellBorderFillView) {
            owner = L"HCellBorderFill";
        } else if (borderFillView) {
            owner = L"HBorderFill";
        }
        const bool consumeBorderFillView =
            cellBorderFillView || borderFillView;
        for (UINT type = 0; type < library->GetTypeInfoCount(); ++type) {
            BSTR name = nullptr;
            status = library->GetDocumentation(type,&name,nullptr,nullptr,nullptr);
            const bool matched = SUCCEEDED(status) && name != nullptr &&
                std::wstring(name,SysStringLen(name)) == owner;
            SysFreeString(name);
            if (matched) {
                status = library->GetTypeInfo(type,result);
                if (consumeBorderFillView) {
                    cellBorderFillView = false;
                    borderFillView = false;
                }
                return status;
            }
        }
        if (consumeBorderFillView) {
            cellBorderFillView = false;
            borderFillView = false;
        }
        return TYPE_E_ELEMENTNOTFOUND;
    }
    STDMETHOD(GetIDsOfNames)(REFIID iid, LPOLESTR* names, UINT count,
                             LCID lcid, DISPID* ids) override {
        ++*getIdsCalls;
        if (iid != IID_NULL || (lcid != 0 && lcid != LOCALE_USER_DEFAULT) ||
            count != 1 || names == nullptr || ids == nullptr) return E_INVALIDARG;
        static const std::map<std::wstring,DISPID> members{
            {L"HeadCtrl",9},{L"LastCtrl",10},{L"ParentCtrl",13},
            {L"CurSelectedCtrl",30030},
            {L"Next",4},{L"Prev",5},{L"CtrlID",2},{L"CtrlCh",1},
            {L"HasList",3},{L"GetAnchorPos",15000},{L"GetCtrlInstID",15001},
            {L"GetPos",10020},{L"SetPos",10021},{L"MovePos",10016},
            {L"InitScan",10017},{L"GetText",10019},{L"ReleaseScan",10018},
            {L"GetRowColCount",10051},{L"MoveToCell",10052},
            {L"GetCellRangeIndex",10053},{L"Properties",6},{L"HSet",1},
            {L"RepeatHeader",16928},{L"Cell",563},{L"Header",16929},
            {L"Item",15005},{L"StartRow",16415},{L"EndRow",16416},
            {L"StartCol",16417},{L"EndCol",16418},{L"IsModified",1},
            {L"PageCount",20001},{L"SelectionMode",20002},
            {L"CreateSet",20003},{L"GetSelectedPosBySet",20004},
            {L"GetTextFile",10044},{L"HAction",30001},
            {L"IsActionEnable",30029},
            {L"HParameterSet",30002},{L"HSet",30003},
            {L"GetDefault",30004},{L"HStyle",30005},
            {L"HCharShape",30006},{L"HParaShape",30007},
            {L"HPageDef",30008},{L"HColDef",30009},
            {L"HSecDef",30026},{L"PageDef",30027},
            {L"SelCellsBorderFill",30028},
            {L"Apply",30010},{L"HeadingType",30011},
            {L"TabDef",30012},{L"Numbering",30013},
            {L"Bullet",30014},{L"XHwpDocuments",30015},
            {L"Active_XHwpDocument",30016},{L"DocumentID",30023},
            {L"FindItem",30024},{L"SetActive_XHwpDocument",30025},
            {L"XHwpDocumentInfo",30017},{L"CurrentPage",30018},
            {L"KeyIndicator",30019},{L"CellShape",30020},
            {L"Width",30021},{L"Height",30022}};
        if (cellReferenceRegression &&
            std::wstring_view(names[0]) == L"BrushType") {
            lastName = names[0];
            *ids = 30031;
            resolvedNames[*ids] = lastName;
            return S_OK;
        }
        const auto found = members.find(names[0]);
        if (found != members.end()) {
            lastName = found->first;
            *ids = found->second;
            resolvedNames[*ids] = lastName;
            return S_OK;
        }
        if (role == WorkflowRole::Parameter) {
            lastName = names[0];
            if (sectionPageSetupFixture && lastName == L"WidthGap") {
                return DISP_E_UNKNOWNNAME;
            }
            *ids = 31000 + static_cast<DISPID>(
                std::hash<std::wstring>{}(lastName) % 1000000);
            resolvedNames[*ids] = lastName;
            return S_OK;
        }
        return DISP_E_UNKNOWNNAME;
    }
    STDMETHOD(Invoke)(DISPID member, REFIID iid, LCID lcid, WORD flags,
                      DISPPARAMS* parameters, VARIANT* result,
                      EXCEPINFO*, UINT*) override {
        ++invokeCount;
        ++*dispatchInvokeCalls;
        const auto resolvedName = resolvedNames.find(member);
        if (resolvedName != resolvedNames.end()) {
            lastName = resolvedName->second;
        } else {
            const auto fixedName = [this, member]() -> const wchar_t* {
                if (member == 9) return L"HeadCtrl";
                if (member == 13) return L"ParentCtrl";
                if (member == 15000) return L"GetAnchorPos";
                if (member == 15001) return L"GetCtrlInstID";
                if (member == 10017) return L"InitScan";
                if (member == 10018) return L"ReleaseScan";
                if (member == 10019) return L"GetText";
                if (member == 10051) return L"GetRowColCount";
                if (member == 16928) return L"RepeatHeader";
                if (member == 30001) return L"HAction";
                if (member == 30002) return L"HParameterSet";
                if (member == 30019) return L"KeyIndicator";
                if (member == 30020) return L"CellShape";
                if (member == 30030) return L"CurSelectedCtrl";
                if (member == 15005) return L"Item";
                if (role == WorkflowRole::Control && member == 2)
                    return L"CtrlID";
                if (role == WorkflowRole::Control && member == 4)
                    return L"Next";
                if (role == WorkflowRole::Control && member == 6)
                    return L"Properties";
                if (role == WorkflowRole::Parameter && member == 1)
                    return L"HSet";
                return nullptr;
            }();
            if (fixedName != nullptr) lastName = fixedName;
        }
        if (memberInvokeCalls != nullptr) {
            ++(*memberInvokeCalls)[lastName];
        }
        if (iid != IID_NULL || (lcid != 0 && lcid != LOCALE_USER_DEFAULT) ||
            parameters == nullptr) return E_INVALIDARG;
        if (lastName == L"TableLowerCell" || lastName == L"TableRightCell") {
            ++*spanNavigationCalls;
            cellPositionCurrent = false;
        }
        const auto exactCall = [member, flags, parameters](
            const DISPID expectedMember, const WORD expectedFlags,
            const UINT expectedArguments) {
            return member == expectedMember && flags == expectedFlags &&
                parameters->cArgs == expectedArguments &&
                parameters->cNamedArgs == 0;
        };
        const auto dispatchResult = [&](IDispatch* value) {
            if (result == nullptr) return E_POINTER;
            result->vt = VT_DISPATCH; result->pdispVal = value;
            if (value != nullptr) value->AddRef(); return S_OK;
        };
        if (lastName == L"HAction" || lastName == L"HParameterSet")
            return dispatchResult(parameterSet);
        if (lastName == L"XHwpDocuments" ||
            lastName == L"Active_XHwpDocument" ||
            lastName == L"XHwpDocumentInfo" || lastName == L"FindItem")
            return dispatchResult(this);
        if (lastName == L"SetActive_XHwpDocument") return S_OK;
        if (lastName == L"DocumentID") {
            result->vt = VT_I4;
            result->lVal = 1;
            return S_OK;
        }
        if (lastName == L"CurrentPage") {
            result->vt = VT_I4;
            result->lVal = 0;
            return S_OK;
        }
        if (lastName == L"CellShape") return dispatchResult(this);
        if (lastName == L"Width" || lastName == L"Height") {
            result->vt = VT_I4;
            result->lVal = 100;
            return S_OK;
        }
        if (role == WorkflowRole::Parameter &&
            lastName == L"FillAttr") {
            borderFillView = true;
            cellBorderFillView = false;
            return dispatchResult(this);
        }
        if (role == WorkflowRole::Parameter &&
            lastName == L"SelCellsBorderFill") {
            borderFillView = false;
            cellBorderFillView = true;
            return dispatchResult(this);
        }
        if (role == WorkflowRole::Parameter &&
            lastName == L"HCellBorderFill") {
            borderFillView = false;
            cellBorderFillView = true;
            return dispatchResult(this);
        }
        if (role == WorkflowRole::Parameter &&
            (lastName == L"HSet" || lastName == L"HStyle" ||
             lastName == L"HCharShape" || lastName == L"HParaShape" ||
             lastName == L"HPageDef" || lastName == L"HColDef" ||
             lastName == L"HStyleItem" ||
             lastName == L"HSecDef" || lastName == L"PageDef" ||
             lastName == L"FillAttr")) {
            if (sectionPageSetupFixture) {
                if (lastName == L"HSecDef") {
                    ++sectionOwnerCalls;
                    sectionOwnerLane = 1;
                } else if (lastName == L"PageDef") {
                    ++pageOwnerCalls;
                    sectionOwnerLane = sectionOwnerLane == 1 ? 2 : 3;
                } else if (lastName == L"HColDef") {
                    ++columnOwnerCalls;
                    sectionOwnerLane = 4;
                } else if (lastName == L"HPageDef") {
                    ++forbiddenPageProfileCalls;
                    sectionOwnerLane = 3;
                }
            }
            return dispatchResult(this);
        }
        if (role == WorkflowRole::Parameter && lastName == L"GetDefault") {
            if (parameters->cArgs == 2 &&
                parameters->rgvarg[1].vt == VT_BSTR) {
                const std::wstring action(
                    parameters->rgvarg[1].bstrVal,
                    SysStringLen(parameters->rgvarg[1].bstrVal));
                if (action == L"CellBorderFill") ++cellBorderDefaultCalls;
                if (sectionPageSetupFixture) {
                    if (action == L"PageSetup") ++pageSetupDefaultCalls;
                    if (action == L"MultiColumn") ++columnDefaultCalls;
                    if (action == L"TablePropertyDialog")
                        ++tablePropertyDefaultCalls;
                }
            }
            return S_OK;
        }
        if (lastName == L"CurSelectedCtrl") return dispatchResult(control);
        if (lastName == L"HeadCtrl") {
            ++*headCtrlCalls;
            return dispatchResult(authoritativeHead != nullptr
                ? authoritativeHead
                : coordinatorFixture
                ? control
                : separateLogicalWrappers || tableMode
                ? nestedControl
                : control);
        }
        if (lastName == L"LastCtrl") return dispatchResult(control);
        if (lastName == L"ParentCtrl") {
            IDispatch* positioned = control;
            if (positionedControlA != nullptr && positionedControlB != nullptr) {
                positioned = *restoredCharacter == positionedCharacterB
                    ? positionedControlB : positionedControlA;
            }
            return dispatchResult(activeScanOwner != nullptr
                ? activeScanOwner : positioned);
        }
        if (lastName == L"CreateSet") return dispatchResult(parameterSet);
        if (lastName == L"Properties") {
            if (role == WorkflowRole::Control) ++*tablePropertiesCalls;
            return dispatchResult(table);
        }
        if (lastName == L"HSet") return dispatchResult(parameterSet);
        if (lastName == L"Cell") return dispatchResult(cell);
        if (lastName == L"GetCellRangeIndex") {
            ++*cellRangeCalls;
            if (!exactCall(10053, DISPATCH_METHOD, 0)) return DISP_E_BADPARAMCOUNT;
            if (cellRangeUnavailable || !cellPositionCurrent)
                return DISP_E_MEMBERNOTFOUND;
            return dispatchResult(range);
        }
        if (lastName == L"GetAnchorPos") return dispatchResult(range);
        if (lastName == L"Next") {
            ++*nextCalls;
            return dispatchResult(coordinatorFixture ? nextControl : nullptr);
        }
        if (lastName == L"Prev") return dispatchResult(nullptr);
        if (lastName == L"PageCount" || lastName == L"SelectionMode") {
            result->vt = VT_I4;
            result->lVal = lastName == L"PageCount" ? 1 :
                *cellAddressVerified ? 3 : 0;
            if (lastName == L"SelectionMode") *cellAddressVerified = false;
            return S_OK;
        }
        if (lastName == L"IsModified") {
            result->vt=VT_BOOL; result->boolVal=VARIANT_FALSE; return S_OK;
        }
        if (lastName == L"GetSelectedPosBySet") {
            result->vt=VT_BOOL; result->boolVal=VARIANT_FALSE; return S_OK;
        }
        if (lastName == L"IsActionEnable") {
            result->vt = VT_BOOL;
            result->boolVal = coordinatorFixture && tableMode
                ? VARIANT_TRUE : VARIANT_FALSE;
            return S_OK;
        }
        if (lastName == L"GetTextFile") {
            const bool hwpml=parameters->cArgs==2 &&
                parameters->rgvarg[1].vt==VT_BSTR &&
                std::wstring(parameters->rgvarg[1].bstrVal,
                    SysStringLen(parameters->rgvarg[1].bstrVal))==L"HWPML2X";
            if (hwpml) ++*hwpmlCaptures;
            const wchar_t* value=hwpml ?
                ((*signatureDrift && *hwpmlCaptures>1)?L"<HWPML><DRIFT/></HWPML>":L"<HWPML/>") :
                L"script document text";
            result->vt=VT_BSTR; result->bstrVal=SysAllocString(value); return S_OK;
        }
        if (lastName == L"CtrlID") {
            result->vt=VT_BSTR;
            result->bstrVal=SysAllocString(identityFixture
                ? fixtureCtrlId.c_str()
                : scriptedTableInstances != nullptr || tableMode
                    ? L"tbl" : L"gso");
            return S_OK;
        }
        if (lastName == L"GetCtrlInstID") {
            if (instanceIdUnavailable) return DISP_E_MEMBERNOTFOUND;
            const auto scripted = scriptedTableInstances == nullptr
                ? std::map<LONG, std::wstring>::const_iterator{}
                : scriptedTableInstances->find(*sharedList);
            const bool hasScriptedInstance =
                scriptedTableInstances != nullptr &&
                scripted != scriptedTableInstances->end();
            result->vt=VT_BSTR;
            result->bstrVal=SysAllocString(identityFixture
                ? fixtureInstanceId.c_str()
                : hasScriptedInstance ? scripted->second.c_str()
                : nestedInstance ? L"script-nested" : L"script-owner");
            return S_OK;
        }
        if (lastName == L"CtrlCh" || lastName == L"StartRow" ||
            lastName == L"EndRow" || lastName == L"StartCol" || lastName == L"EndCol") {
            if (lastName != L"CtrlCh") {
                const DISPID expected = lastName == L"StartRow" ? 16415 :
                    lastName == L"EndRow" ? 16416 :
                    lastName == L"StartCol" ? 16417 : 16418;
                if (!exactCall(expected, DISPATCH_PROPERTYGET, 0))
                    return DISP_E_BADPARAMCOUNT;
            }
            result->vt=VT_I4;
            LONG sr=*sharedRow,er=*sharedRow,sc=*sharedColumn,ec=*sharedColumn;
            if (!directCellRangeExact && *sharedRow==0 && *sharedColumn<2) {sc=0;ec=1;}
            else if (!directCellRangeExact &&
                     (*sharedRow==1 || *sharedRow==2) && *sharedColumn==0) {sr=1;er=2;}
            else if (!directCellRangeExact &&
                     (*sharedRow==1 || *sharedRow==2) &&
                     (*sharedColumn==1 || *sharedColumn==2)) {sr=1;er=2;sc=1;ec=2;}
            else if (!directCellRangeExact && *sharedRow==3) {sc=0;ec=3;}
            const LONG base = oneBasedBoundsCoordinates ? 1 : 0;
            result->lVal = lastName==L"StartRow"?sr + base:
                lastName==L"EndRow"?er + base:
                lastName==L"StartCol"?sc + base:
                lastName==L"EndCol"?ec + base:1;
            return S_OK;
        }
        if (lastName == L"BrushType" && parameters->cArgs == 1 &&
            cellReferenceRegression) {
            result->vt = VT_I4;
            result->lVal = 1;
            return S_OK;
        }
        if (lastName == L"PropertyOrigin" && parameters->cArgs == 1 &&
            parameters->rgvarg[0].vt == VT_BSTR) {
            const std::wstring path(
                parameters->rgvarg[0].bstrVal,
                SysStringLen(parameters->rgvarg[0].bstrVal));
            if (path.find(L"FaceNameUser") != std::wstring::npos) {
                propertyOriginUnavailable = true;
                return DISP_E_MEMBERNOTFOUND;
            }
            const LONG origin =
                path.find(L"FaceNameLatin") != std::wstring::npos ? 1L :
                path.find(L"FaceNameHanja") != std::wstring::npos ? 2L :
                path.find(L"FaceNameJapanese") != std::wstring::npos ? 3L :
                path.find(L"FaceNameOther") != std::wstring::npos ? 4L :
                path.find(L"FaceNameSymbol") != std::wstring::npos ? 5L : 0L;
            propertyOriginMask |= 1UL << static_cast<unsigned long>(origin);
            result->vt = VT_I4;
            result->lVal = origin;
            return S_OK;
        }
        if (lastName == L"HasList") {
            result->vt=VT_BOOL;
            result->boolVal = coordinatorFixture || *sharedList == 1
                ? VARIANT_TRUE : VARIANT_FALSE;
            return S_OK;
        }
        if (lastName == L"SetPos" || lastName == L"InitScan") {
            if (lastName==L"InitScan") {
                scanStep = 0;
                scanStartedInList = *sharedList;
                activeScanOwner = nullptr;
            }
            if (lastName==L"SetPos") {
                ++*setPosCalls;
                cellPositionCurrent = false;
                *selectionMode = 0;
                *cellBlockPending = false;
                *cellAddressVerified = false;
                *sharedList=parameters->rgvarg[2].lVal;
                *restoredParagraph=parameters->rgvarg[1].lVal;
                *restoredCharacter=parameters->rgvarg[0].lVal;
            }
            result->vt=VT_BOOL;
            result->boolVal=(lastName==L"SetPos" && *failRestore) ||
                (lastName==L"SetPos" && multiCellDirectFixture && *sharedList > 2) ||
                (lastName==L"SetPos" && productionShape2077 &&
                 (*sharedList < 1 || *sharedList > 2077))
                ? VARIANT_FALSE : VARIANT_TRUE;
            if (lastName == L"SetPos" && result->boolVal == VARIANT_TRUE) {
                if (productionShape2077) {
                    *sharedRow = (*sharedList - 1) / 67;
                    *sharedColumn = (*sharedList - 1) % 67;
                } else if (multiCellDirectFixture) {
                    *sharedRow = 0;
                    *sharedColumn = *sharedList - 1;
                }
            }
            return S_OK;
        }
        if (lastName == L"MovePos") {
            const LONG mode = parameters->rgvarg[2].lVal;
            if (mode == 14) *sharedList=2;
            else if (mode == 15) *sharedList=1;
            else if (mode == 201) {
                ++*moveScanPosCalls;
                *sharedList=scanList;
                *restoredParagraph=scanParagraph;
                *restoredCharacter=scanCharacter;
            }
            result->vt=VT_BOOL; result->boolVal=VARIANT_TRUE; return S_OK;
        }
        if (lastName == L"GetPos") {
            parameters->rgvarg[0].plVal[0]=*restoredCharacter < 0
                ? 0 : *restoredCharacter;
            parameters->rgvarg[1].plVal[0]=*restoredParagraph < 0
                ? 0 : *restoredParagraph;
            parameters->rgvarg[2].plVal[0]=*sharedList; return S_OK;
        }
        if (lastName == L"GetText") {
            const unsigned step = scanStep++;
            LONG state = 1;
            const wchar_t* text = L"";
            const auto enter = [this](IDispatch* const owner,
                                      const LONG list,
                                      const LONG paragraph) {
                activeScanOwner = owner;
                scanList = list;
                scanParagraph = paragraph;
                scanCharacter = 0;
            };
            if (step == 0) {
                state = scanStartedInList == 9 ? 1 : 2;
                text = scanStartedInList == 9
                    ? L"Table fixture caption\r\nsecond paragraph"
                    : L"script document text";
            } else if (*captionMatchCount == 1) {
                if (step == 1) { enter(scanOwnerA, 2, 4); state = 4; }
                else if (step == 2) {
                    state = 2; text = L"Capability fixture caption";
                } else if (step == 3) { state = 5; activeScanOwner = nullptr; }
                else if (step == 4) { enter(scanOwnerB, 5, 6); state = 4; }
                else if (step == 5) {
                    state = 2; text = L"Capability fixture caption";
                } else if (step == 6) { state = 5; activeScanOwner = nullptr; }
            } else if (*captionMatchCount == 2) {
                if (step == 1) { enter(scanOwnerA, 2, 4); state = 4; }
                else if (step == 2) {
                    state = 2; text = L"Capability fixture caption";
                } else if (step == 3) { state = 5; activeScanOwner = nullptr; }
                else if (step == 4) { enter(scanOwnerA, 7, 8); state = 4; }
                else if (step == 5) {
                    state = 2; text = L"Capability fixture caption";
                } else if (step == 6) { state = 5; activeScanOwner = nullptr; }
            } else if (*captionMatchCount == 3) {
                if (step == 1) { enter(scanOwnerB, 5, 6); state = 4; }
                else if (step == 2) {
                    state = 2; text = L"Capability fixture caption";
                } else if (step == 3) { state = 5; activeScanOwner = nullptr; }
            }
            *parameters->rgvarg[0].pbstrVal = SysAllocString(text);
            result->vt = VT_I4;
            result->lVal = state;
            return S_OK;
        }
        if (lastName == L"ReleaseScan") return S_OK;
        if (lastName == L"KeyIndicator") {
            for (UINT index = 0; index < parameters->cArgs; ++index) {
                VARIANTARG& argument = parameters->rgvarg[index];
                if (argument.vt == (VT_BSTR | VT_BYREF) &&
                    argument.pbstrVal != nullptr) {
                    if (multiCellDirectFixture || productionShape2077) {
                        std::wstring letters;
                        LONG value = *sharedColumn + 1;
                        while (value > 0) {
                            --value;
                            letters.push_back(static_cast<wchar_t>(L'A' + value % 26));
                            value /= 26;
                        }
                        std::reverse(letters.begin(), letters.end());
                        const std::wstring address = L"Table(" + letters +
                            std::to_wstring(*sharedRow + 1) + L")";
                        *argument.pbstrVal = SysAllocString(address.c_str());
                    } else {
                        const auto scripted = scriptedCellAddresses == nullptr
                            ? std::map<LONG, std::wstring>::const_iterator{}
                            : scriptedCellAddresses->find(*sharedList);
                        const bool hasScriptedAddress =
                            scriptedCellAddresses != nullptr &&
                            scripted != scriptedCellAddresses->end();
                        const std::wstring address = hasScriptedAddress
                            ? L"Table(" + scripted->second + L")"
                            : cellReferenceRegression && *sharedList == 1409
                                ? L"Table(G5)" : L"Table(A1)";
                        *argument.pbstrVal = SysAllocString(address.c_str());
                    }
                    if (*cellBlockPending) {
                        *cellBlockPending = false;
                        *cellAddressVerified = true;
                    }
                }
            }
            return S_OK;
        }
        if (lastName == L"GetRowColCount") {
            result->vt=VT_INT;
            result->intVal = productionShape2077
                ? (parameters->rgvarg[0].intVal == 1 ? 31 : 67) : 4;
            return S_OK;
        }
        if (lastName == L"MoveToCell") {
            if (!exactCall(10052, DISPATCH_METHOD, 3))
                return DISP_E_BADPARAMCOUNT;
            *sharedRow=parameters->rgvarg[2].intVal;
            *sharedColumn=parameters->rgvarg[1].intVal;
            result->vt=VT_BOOL;
            const LONG base = oneBasedMoveCoordinates ? 1 : 0;
            const LONG rowLimit = productionShape2077 ? 31 : 4;
            const LONG columnLimit = productionShape2077 ? 67 : 4;
            result->boolVal=(*sharedRow>=base && *sharedRow<rowLimit + base &&
                *sharedColumn>=base && *sharedColumn<columnLimit + base)
                ? VARIANT_TRUE : VARIANT_FALSE;
            if (result->boolVal == VARIANT_TRUE) {
                *sharedRow -= base;
                *sharedColumn -= base;
                if (productionShape2077)
                    *sharedList = *sharedRow * 67 + *sharedColumn + 1;
            }
            cellPositionCurrent = result->boolVal == VARIANT_TRUE;
            return S_OK;
        }
        if (lastName == L"Item") {
            if (parameters->cArgs==1 && parameters->rgvarg[0].vt==VT_BSTR) {
                const std::wstring itemName(parameters->rgvarg[0].bstrVal,
                    SysStringLen(parameters->rgvarg[0].bstrVal));
                if (itemName==L"List" || itemName==L"Para" || itemName==L"Pos") {
                    if (itemName == L"List" && identityFixture &&
                        fixtureCtrlId == L"$pic") {
                        ++captionListItemCalls;
                    }
                    result->vt=VT_I4;
                    result->lVal=itemName==L"List"
                        ? identityFixture && fixtureCtrlId == L"$pic" ? 3 : *sharedList
                        : 0;
                    return S_OK;
                }
                if (itemName == L"Cell") return dispatchResult(this);
                if (itemName == L"ShapeCaption") {
                    ++shapeCaptionItemCalls;
                    return dispatchResult(this);
                }
                if (itemName == L"Text") {
                    ++captionTextItemCalls;
                    result->vt = VT_BSTR;
                    result->bstrVal =
                        SysAllocString(L"Capability fixture caption");
                    return S_OK;
                }
            }
            result->vt=VT_UI2; result->uiVal=1; return S_OK;
        }
        if (lastName == L"RepeatHeader" || lastName == L"Header") {
            result->vt=VT_UI2; result->uiVal=1; return S_OK;
        }
        if (role == WorkflowRole::Parameter && lastName == L"Run" &&
            parameters->cArgs == 1 &&
            parameters->rgvarg[0].vt == VT_BSTR) {
            const std::wstring action(
                parameters->rgvarg[0].bstrVal,
                SysStringLen(parameters->rgvarg[0].bstrVal));
            const bool qualifyingCellReference =
                action == L"TableCellBlock" && cellReferenceQualification;
            if (!coordinatorFixture && !qualifyingCellReference) {
                result->vt = VT_I4;
                result->lVal = 1;
                return S_OK;
            }
            if (action == L"TableCellBlock") {
                ++*tableCellBlockCalls;
                *cellBlockPending = true;
                if (*sharedList != 1409) *sharedList = 1;
            } else if (productionShape2077 && action == L"TableColEnd") {
                *sharedList = 67;
                *sharedRow = 0;
                *sharedColumn = 66;
            } else if (productionShape2077 && action == L"TableColPageDown") {
                *sharedList = 2077;
                *sharedRow = 30;
                *sharedColumn = 66;
            } else if (productionShape2077 && action == L"TableColBegin") {
                *sharedList = 2011;
                *sharedRow = 30;
                *sharedColumn = 0;
            } else if (productionShape2077 && action == L"TableColPageUp") {
                *sharedList = 1;
                *sharedRow = 0;
                *sharedColumn = 0;
            } else if (productionShape2077 && action == L"TableLowerCell") {
                if (*sharedRow < 30) ++*sharedRow;
                *sharedList = *sharedRow * 67 + *sharedColumn + 1;
            } else if (productionShape2077 && action == L"TableRightCell") {
                if (*sharedColumn < 66) ++*sharedColumn;
                *sharedList = *sharedRow * 67 + *sharedColumn + 1;
            } else if (action.rfind(L"Table", 0) == 0) {
                *sharedList = 1;
            } else if (action == L"ShapeObjAttachCaption") {
                *sharedList = 9;
                *restoredParagraph = 0;
                *restoredCharacter = 0;
            } else if (action == L"CloseEx") {
                *sharedList = 1;
            }
            result->vt = VT_BOOL;
            result->boolVal = VARIANT_TRUE;
            return S_OK;
        }
        if (role == WorkflowRole::Parameter) {
            static const std::array<std::wstring_view, 17> borderMembers{{
                L"BorderTypeLeft", L"BorderWidthLeft", L"BorderCorlorLeft",
                L"BorderTypeRight", L"BorderWidthRight", L"BorderColorRight",
                L"BorderTypeTop", L"BorderWidthTop", L"BorderColorTop",
                L"BorderTypeBottom", L"BorderWidthBottom",
                L"BorderColorBottom", L"WinBrushFaceColor",
                L"WinBrushHatchColor", L"WinBrushAlpha", L"WindowsBrush",
                L"Type",
            }};
            if (cellReferenceRegression &&
                std::find(borderMembers.begin(), borderMembers.end(),
                          std::wstring_view(lastName)) != borderMembers.end()) {
                result->vt = VT_I4;
                result->lVal = 0;
            } else if (lastName == L"Name" || lastName == L"NameLocal" ||
                lastName.find(L"FaceName") == 0) {
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"Fake Definition");
            } else {
                result->vt = VT_I4;
                if (sectionPageSetupFixture &&
                    (sectionOwnerLane == 2 || sectionOwnerLane == 4)) {
                    const bool second = *sharedList == 9;
                    result->lVal = lastName == L"PaperWidth" ? (second ? 61001 : 60001) :
                        lastName == L"PaperHeight" ? (second ? 82002 : 80002) :
                        lastName == L"LeftMargin" ? (second ? 3103 : 0) :
                        lastName == L"RightMargin" ? (second ? 4104 : 4004) :
                        lastName == L"TopMargin" ? (second ? 5105 : 5005) :
                        lastName == L"BottomMargin" ? (second ? 6106 : 6006) :
                        lastName == L"HeaderLen" ? (second ? 7107 : 7007) :
                        lastName == L"FooterLen" ? (second ? 8108 : 8008) :
                        lastName == L"GutterLen" ? (second ? 9109 : 0) :
                        lastName == L"Landscape" ? (second ? 1 : 0) :
                        lastName == L"GutterType" ? (second ? 2 : 0) :
                        lastName == L"Count" ? (second ? 2 : 1) :
                        lastName == L"SameSize" ? (second ? 0 : 1) :
                        lastName == L"SameGap" ? (second ? 919 : 0) : 1;
                } else if (sectionPageSetupFixture && sectionOwnerLane == 3) {
                    ++forbiddenPageProfileCalls;
                    result->lVal = 990000 + static_cast<LONG>(lastName.size());
                } else {
                    result->lVal = lastName == L"RowCount" && productionShape2077 ? 31 :
                        lastName == L"ColCount" && productionShape2077 ? 67 :
                        lastName == L"Apply" ? 5 :
                        lastName == L"HeadingType" ? 0 :
                        lastName == L"PaperWidth" ? pageWidth :
                        lastName == L"PaperHeight" ? 84189 :
                        lastName == L"LeftMargin" ? 8504 :
                        lastName == L"RightMargin" ? 8504 :
                        lastName == L"TopMargin" ? 5669 :
                        lastName == L"BottomMargin" ? 4252 :
                        lastName == L"HeaderLen" ? 4252 :
                        lastName == L"FooterLen" ? 4252 :
                        lastName == L"Count" ? columnCount :
                        lastName == L"SameGap" ? 850 : 1;
                }
            }
            return S_OK;
        }
        return DISP_E_MEMBERNOTFOUND;
    }

    WorkflowRole role = WorkflowRole::Root;
    bool tableMode = false;
    bool nestedInstance = false;
    bool separateLogicalWrappers = false;
    bool coordinatorFixture = false;
    bool cellReferenceQualification = false;
    bool cellReferenceRegression = false;
    bool borderFillView = false;
    bool cellBorderFillView = false;
    unsigned long propertyOriginMask = 0;
    bool propertyOriginUnavailable = false;
    bool identityFixture = false;
    bool directCellRangeExact = false;
    bool multiCellDirectFixture = false;
    bool productionShape2077 = false;
    bool cellRangeUnavailable = false;
    bool cellPositionCurrent = false;
    bool oneBasedMoveCoordinates = false;
    bool oneBasedBoundsCoordinates = false;
    bool instanceIdUnavailable = false;
    std::wstring fixtureCtrlId{};
    std::wstring fixtureInstanceId{};
    LONG row = 0;
    LONG column = 0;
    LONG currentList = 1;
    LONG localSelectionMode = 0;
    bool localCellBlockPending = false;
    bool localCellAddressVerified = false;
    LONG* sharedRow = &row;
    LONG* sharedColumn = &column;
    LONG* sharedList = &currentList;
    const std::map<LONG, std::wstring>* scriptedCellAddresses = nullptr;
    const std::map<LONG, std::wstring>* scriptedTableInstances = nullptr;
    LONG* selectionMode = &localSelectionMode;
    bool* cellBlockPending = &localCellBlockPending;
    bool* cellAddressVerified = &localCellAddressVerified;
    bool localSignatureDrift = false;
    bool localFailRestore = false;
    unsigned localHwpmlCaptures = 0;
    unsigned localSetPosCalls = 0;
    unsigned localTypeInfoCalls = 0;
    unsigned localGetIdsCalls = 0;
    unsigned localDispatchInvokeCalls = 0;
    unsigned localTableCellBlockCalls = 0;
    unsigned localHeadCtrlCalls = 0;
    unsigned localNextCalls = 0;
    unsigned localTablePropertiesCalls = 0;
    unsigned localCellRangeCalls = 0;
    unsigned localSpanNavigationCalls = 0;
    unsigned localMoveScanPosCalls = 0;
    unsigned localCaptionMatchCount = 1;
    LONG localRestoredParagraph = -1;
    LONG localRestoredCharacter = -1;
    bool* signatureDrift = &localSignatureDrift;
    bool* failRestore = &localFailRestore;
    unsigned* hwpmlCaptures = &localHwpmlCaptures;
    unsigned* setPosCalls = &localSetPosCalls;
    unsigned* typeInfoCalls = &localTypeInfoCalls;
    unsigned* getIdsCalls = &localGetIdsCalls;
    unsigned* dispatchInvokeCalls = &localDispatchInvokeCalls;
    unsigned* tableCellBlockCalls = &localTableCellBlockCalls;
    unsigned* headCtrlCalls = &localHeadCtrlCalls;
    unsigned* nextCalls = &localNextCalls;
    unsigned* tablePropertiesCalls = &localTablePropertiesCalls;
    unsigned* cellRangeCalls = &localCellRangeCalls;
    unsigned* spanNavigationCalls = &localSpanNavigationCalls;
    unsigned* moveScanPosCalls = &localMoveScanPosCalls;
    unsigned* captionMatchCount = &localCaptionMatchCount;
    LONG* restoredParagraph = &localRestoredParagraph;
    LONG* restoredCharacter = &localRestoredCharacter;
    std::wstring lastName;
    std::map<DISPID, std::wstring> resolvedNames;
    std::map<std::wstring, std::uint64_t>* memberInvokeCalls = nullptr;
    unsigned invokeCount = 0;
    unsigned scanStep = 0;
    LONG scanStartedInList = 0;
    unsigned shapeCaptionItemCalls = 0;
    unsigned captionTextItemCalls = 0;
    unsigned captionListItemCalls = 0;
    LONG pageWidth = 59528;
    LONG columnCount = 1;
    bool sectionPageSetupFixture = false;
    unsigned sectionOwnerLane = 0;
    unsigned sectionOwnerCalls = 0;
    unsigned pageOwnerCalls = 0;
    unsigned columnOwnerCalls = 0;
    unsigned pageSetupDefaultCalls = 0;
    unsigned columnDefaultCalls = 0;
    unsigned tablePropertyDefaultCalls = 0;
    unsigned cellBorderDefaultCalls = 0;
    unsigned forbiddenPageProfileCalls = 0;
    IDispatch* control = nullptr;
    IDispatch* table = nullptr;
    IDispatch* cell = nullptr;
    IDispatch* range = nullptr;
    IDispatch* parameterSet = nullptr;
    IDispatch* nestedControl = nullptr;
    IDispatch* nextControl = nullptr;
    IDispatch* authoritativeHead = nullptr;
    IDispatch* scanOwnerA = nullptr;
    IDispatch* scanOwnerB = nullptr;
    IDispatch* activeScanOwner = nullptr;
    IDispatch* positionedControlA = nullptr;
    IDispatch* positionedControlB = nullptr;
    WorkflowHiddenDualProxy hiddenDual;
    WorkflowHiddenDualProxy cellBorderFillHiddenDual;
    LONG positionedCharacterB = 0;
    LONG scanList = 0;
    LONG scanParagraph = 0;
    LONG scanCharacter = 0;
};

HRESULT STDMETHODCALLTYPE WorkflowHiddenQueryInterface(
    WorkflowHiddenDualProxy* const proxy,
    REFIID iid,
    void** const result) noexcept {
    return proxy->owner->QueryInterface(iid, result);
}

ULONG STDMETHODCALLTYPE WorkflowHiddenAddRef(
    WorkflowHiddenDualProxy* const proxy) noexcept {
    return proxy->owner->AddRef();
}

ULONG STDMETHODCALLTYPE WorkflowHiddenRelease(
    WorkflowHiddenDualProxy* const proxy) noexcept {
    return proxy->owner->Release();
}

HRESULT STDMETHODCALLTYPE WorkflowHiddenDispatchProperty(
    WorkflowHiddenDualProxy* const proxy,
    IDispatch** const result) noexcept {
    if (result == nullptr) {
        return E_POINTER;
    }
    *result = proxy->owner->parameterSet;
    if (*result != nullptr) {
        static_cast<void>((*result)->AddRef());
    }
    return S_OK;
}

HRESULT STDMETHODCALLTYPE WorkflowHiddenLongProperty(
    WorkflowHiddenDualProxy* const proxy,
    ULONG* const result) noexcept {
    if (result == nullptr) {
        return E_POINTER;
    }
    *result = proxy->owner->cellReferenceRegression ? 0 : 1;
    return S_OK;
}

struct WorkflowHiddenDualVtableStorage final {
    WorkflowHiddenDualVtableStorage() noexcept {
        slots[0] =
            reinterpret_cast<void*>(&WorkflowHiddenQueryInterface);
        slots[1] = reinterpret_cast<void*>(&WorkflowHiddenAddRef);
        slots[2] = reinterpret_cast<void*>(&WorkflowHiddenRelease);
        slots[73] =
            reinterpret_cast<void*>(&WorkflowHiddenDispatchProperty);
        slots[74] =
            reinterpret_cast<void*>(&WorkflowHiddenDispatchProperty);
    }

    void* slots[75]{};
};

void** WorkflowHiddenDualVtable() noexcept {
    static WorkflowHiddenDualVtableStorage storage;
    return storage.slots;
}

struct WorkflowCellBorderFillVtableStorage final {
    WorkflowCellBorderFillVtableStorage() noexcept {
        slots[0] =
            reinterpret_cast<void*>(&WorkflowHiddenQueryInterface);
        slots[1] = reinterpret_cast<void*>(&WorkflowHiddenAddRef);
        slots[2] = reinterpret_cast<void*>(&WorkflowHiddenRelease);
        slots[26] =
            reinterpret_cast<void*>(&WorkflowHiddenLongProperty);
        slots[28] =
            reinterpret_cast<void*>(&WorkflowHiddenLongProperty);
        slots[30] =
            reinterpret_cast<void*>(&WorkflowHiddenLongProperty);
        slots[32] =
            reinterpret_cast<void*>(&WorkflowHiddenLongProperty);
    }

    void* slots[33]{};
};

void** WorkflowCellBorderFillVtable() noexcept {
    static WorkflowCellBorderFillVtableStorage storage;
    return storage.slots;
}

HRESULT WINAPI WorkflowDispatch::QueryRootHiddenDual(
    void* const object,
    REFIID iid,
    void** const result,
    DWORD_PTR) noexcept {
    constexpr GUID rootInterfaceId{
        0x5E6A8276, 0xCF1C, 0x42B8,
        {0xBC, 0xED, 0x31, 0x95, 0x48, 0xB0, 0x2A, 0xF6}};
    constexpr GUID cellBorderFillInterfaceId{
        0xC54797D7, 0xB2FF, 0x44A0,
        {0xBE, 0xC2, 0x2D, 0x20, 0xF2, 0x00, 0x89, 0x8C}};
    constexpr GUID borderFillInterfaceId{
        0xC81C513C, 0x94D5, 0x4589,
        {0x8B, 0x92, 0xBF, 0xF9, 0xFD, 0x5D, 0xA9, 0x46}};
    if (result == nullptr) {
        return E_POINTER;
    }
    *result = nullptr;
    auto* const dispatch = static_cast<WorkflowDispatch*>(object);
    if (InlineIsEqualGUID(iid, rootInterfaceId) &&
        dispatch->role == WorkflowRole::Root) {
        *result = &dispatch->hiddenDual;
    } else if (
        InlineIsEqualGUID(iid, cellBorderFillInterfaceId) &&
        dispatch->role == WorkflowRole::Parameter &&
        dispatch->productionShape2077) {
        *result = &dispatch->cellBorderFillHiddenDual;
    } else if (
        InlineIsEqualGUID(iid, borderFillInterfaceId) &&
        dispatch->role == WorkflowRole::Parameter &&
        dispatch->productionShape2077) {
        *result = &dispatch->cellBorderFillHiddenDual;
    } else {
        return E_NOINTERFACE;
    }
    static_cast<void>(dispatch->AddRef());
    return S_OK;
}

bool FixtureHwpVersion(
    void*, std::array<std::uint16_t, 4>* const output) noexcept {
    if (output == nullptr) return false;
    *output = {13, 0, 4, 2};
    return true;
}

bool FixturePrinterName(void*, std::wstring* const output) noexcept {
    if (output == nullptr) return false;
    try {
        *output = L"Capability Fixture Printer";
        return true;
    } catch (...) {
        return false;
    }
}

bool FixturePrinterDevMode(
    void*,
    const std::wstring& printerName,
    std::vector<std::uint8_t>* const output) noexcept {
    if (output == nullptr ||
        printerName != L"Capability Fixture Printer") return false;
    try {
        *output = {0x44, 0x4d, 0x01, 0x00};
        return true;
    } catch (...) {
        return false;
    }
}

bool FixtureFonts(
    void*,
    std::vector<hancom::graph::layout::LayoutFontTupleV1>* const output)
    noexcept {
    if (output == nullptr) return false;
    try {
        *output = {{L"Capability Fixture Font", 0, 0, 400, 0}};
        return true;
    } catch (...) {
        return false;
    }
}

bool FixtureDpi(
    void*, std::uint32_t* const x, std::uint32_t* const y) noexcept {
    if (x == nullptr || y == nullptr) return false;
    *x = 96;
    *y = 96;
    return true;
}

bool FixtureSystemLcid(void*, std::uint32_t* const output) noexcept {
    if (output == nullptr) return false;
    *output = 0x0412;
    return true;
}

hancom::graph::layout::LayoutEnvironmentPlatformV1
FixtureEnvironmentPlatform() {
    return {
        nullptr,
        FixtureHwpVersion,
        FixturePrinterName,
        FixturePrinterDevMode,
        FixtureFonts,
        FixtureDpi,
        FixtureSystemLcid,
    };
}

bool WorkflowProductionPathSmoke() {
    CComPtr<WorkflowDispatch> objects[10];
    for (int index=0; index<10; ++index) {
        CComObjectNoLock<WorkflowDispatch>* raw =
            new (std::nothrow) CComObjectNoLock<WorkflowDispatch>();
        if (raw == nullptr) return false;
        objects[index] = raw;
        raw->role = index >= 6 ? WorkflowRole::Control :
            static_cast<WorkflowRole>(index);
        raw->nestedInstance = index==6;
    }
    for (auto& object : objects) {
        object->control=objects[1]; object->table=objects[2];
        object->cell=objects[3]; object->range=objects[4];
        object->parameterSet=objects[5];
        object->nestedControl=objects[6];
        object->scanOwnerA=objects[7];
        object->scanOwnerB=objects[9];
        object->sharedRow=&objects[0]->row;
        object->sharedColumn=&objects[0]->column;
        object->sharedList=&objects[0]->currentList;
        object->selectionMode=&objects[0]->localSelectionMode;
        object->cellBlockPending=&objects[0]->localCellBlockPending;
        object->cellAddressVerified=&objects[0]->localCellAddressVerified;
        object->signatureDrift=&objects[0]->localSignatureDrift;
        object->failRestore=&objects[0]->localFailRestore;
        object->hwpmlCaptures=&objects[0]->localHwpmlCaptures;
        object->setPosCalls=&objects[0]->localSetPosCalls;
        object->tableCellBlockCalls=
            &objects[0]->localTableCellBlockCalls;
        object->moveScanPosCalls=&objects[0]->localMoveScanPosCalls;
        object->captionMatchCount=&objects[0]->localCaptionMatchCount;
        object->restoredParagraph=&objects[0]->localRestoredParagraph;
        object->restoredCharacter=&objects[0]->localRestoredCharacter;
    }
    // Qualified capture walks this production-shaped control chain rather
    // than inferring node families from the baseline selection. Empty native
    // IDs intentionally exercise ordinal+anchor locator ownership.
    objects[1]->identityFixture = true;
    objects[1]->fixtureCtrlId = L"tbl";
    objects[1]->fixtureInstanceId = L"";
    objects[7]->identityFixture = true;
    objects[7]->fixtureCtrlId = L"$pic";
    objects[7]->fixtureInstanceId = L"owner-a";
    objects[7]->table = objects[7];
    objects[9]->identityFixture = true;
    objects[9]->fixtureCtrlId = L"$pic";
    objects[9]->fixtureInstanceId = L"owner-b";
    objects[9]->table = objects[9];
    objects[8]->identityFixture = true;
    objects[8]->fixtureCtrlId = L"secd";
    objects[8]->fixtureInstanceId = L"";
    objects[1]->nextControl = objects[7];
    objects[7]->nextControl = objects[9];
    objects[9]->nextControl = objects[8];
    const std::wstring containment = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tCONTAINMENT\n"
        L"FIXTURE_COVERAGE\tshared_header_footer\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tnotes\tOBSERVED\n"
        L"FIXTURE_COVERAGE\ttext_boxes\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tcaptions\tOBSERVED\nEND");
    for (auto& object : objects) object->tableMode=true;
    const std::wstring table = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tTABLE_TOPOLOGY\n"
        L"FIXTURE_COVERAGE\thorizontal_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tvertical_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\trectangular_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tterminal_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tnested_table\tOBSERVED\nEND");
    const auto environmentPlatform = FixtureEnvironmentPlatform();
    objects[1]->fixtureInstanceId = L"selected-layout-table";
    const unsigned tablePropertyDefaultsBefore =
        objects[5]->tablePropertyDefaultCalls;
    objects[0]->cellReferenceQualification = true;
    objects[5]->cellReferenceQualification = true;
    objects[0]->localSelectionMode = 0;
    const std::wstring selectedTableLayout = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tLAYOUT_GRAPH\nEND",
        &environmentPlatform);
    objects[0]->cellReferenceQualification = true;
    objects[5]->cellReferenceQualification = true;
    objects[1]->fixtureInstanceId.clear();
    std::wcout << L"WORKFLOW_SELECTED_TABLE_LAYOUT_DIAGNOSTIC\n"
               << selectedTableLayout << L'\n';
    const bool selectedTableLayoutPreserved =
        selectedTableLayout.find(L"CAPTURE_STATUS\t0") != std::wstring::npos &&
        selectedTableLayout.find(L"PUBLICATION_STATE\tSTABLE_OBSERVED") !=
            std::wstring::npos &&
        selectedTableLayout.find(L"RESULT\tPASS") != std::wstring::npos &&
        objects[5]->tablePropertyDefaultCalls ==
            tablePropertyDefaultsBefore;
    hancom::graph::capture::ReaderPayload closure;
    closure.reader =
        hancom::graph::capture::QualifiedReader::EffectiveProperties;
    const std::vector<hancom::graph::properties::ReferenceSite> sites{
        {hancom::graph::capture::PropertyTarget::Section,
         L"0", {1,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Paragraph,
         L"1:0", {1,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Run,
         L"1:0:0:4", {1,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Cell,
         L"script-owner:A1", {3,0,0}, 0},
    };
    hancom::graph::properties::ReferenceClosureDiagnostics
        closureDiagnostics;
    hancom::graph::Sha256 pageSetupDigest{};
    const bool referenceClosure =
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            objects[0], sites, &closure, &closureDiagnostics) ==
            hancom::graph::properties::CaptureStatus::Complete &&
        closureDiagnostics.visitedSites == sites.size() &&
        closure.referenceTraversal.globalStyleCatalogNotExposed &&
        hancom::graph::properties::DerivePageSetupDigest(
            closure, &pageSetupDigest) &&
        objects[0]->currentList == 1;
    const unsigned reversedCellBlocksBefore =
        objects[0]->localTableCellBlockCalls;
    const unsigned reversedCellDefaultsBefore =
        objects[5]->cellBorderDefaultCalls;
    std::array<hancom::graph::capture::ReaderPayload, 2> reversedCellAttempts;
    objects[0]->cellReferenceRegression = true;
    objects[5]->cellReferenceRegression = true;
    bool reversedCellCaptures = true;
    const std::array<unsigned, 2> reversedAttemptOrder{{1, 0}};
    for (size_t index = 0; index < reversedAttemptOrder.size(); ++index) {
        objects[0]->currentList = 1;
        objects[0]->localSelectionMode = 0;
        objects[5]->lastName = reversedAttemptOrder[index] == 0
            ? L"HCellBorderFill" : L"SelCellsBorderFill";
        hancom::graph::properties::ReferenceClosureDiagnostics diagnostics;
        reversedCellCaptures = reversedCellCaptures &&
            hancom::graph::properties::CaptureCurrentReferenceClosure(
                objects[0],
                {{hancom::graph::capture::PropertyTarget::Cell,
                  L"table:1142018492:G5", {1409,0,0}, 0}},
                &reversedCellAttempts[index], &diagnostics) ==
                hancom::graph::properties::CaptureStatus::Complete &&
            diagnostics.expectedSites == 1 && diagnostics.visitedSites == 1 &&
            objects[0]->currentList == 1 &&
            objects[0]->localSelectionMode == 0;
    }
    objects[0]->cellReferenceRegression = false;
    objects[5]->cellReferenceRegression = false;
    const auto qualifiedBorder = [](const auto& payload) {
        const auto definition = std::find_if(
            payload.definitions.begin(), payload.definitions.end(),
            [](const auto& value) {
                return value.kind ==
                    hancom::graph::DefinitionKind::BorderFill;
            });
        if (definition == payload.definitions.end() ||
            definition->properties.size() != 16 ||
            definition->identity.find(
                L"f916e7bdf477fa645e4e5a0405147e0ea8e7cde26d0f2f5230d201f7c42e3ba0") ==
                std::wstring::npos) {
            return false;
        }
        for (size_t index = 0; index != definition->properties.size(); ++index) {
            const auto& property = definition->properties[index];
            if (property.key != 5000U + index ||
                (index != 15 &&
                 (property.state != hancom::graph::ObservationState::Value ||
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
        return payload.definitionReferences.size() == 1 &&
            payload.definitionReferences.front().edge ==
                hancom::graph::EdgeKind::BorderFillRef;
    };
    const unsigned qualifiedCellBlockCalls =
        objects[0]->localTableCellBlockCalls - reversedCellBlocksBefore;
    const unsigned qualifiedCellDefaultCalls =
        objects[5]->cellBorderDefaultCalls - reversedCellDefaultsBefore;
    const bool cellReferenceAttemptIsolation = reversedCellCaptures &&
        qualifiedBorder(reversedCellAttempts[0]) &&
        qualifiedBorder(reversedCellAttempts[1]) &&
        reversedCellAttempts[0].definitions.front().identity ==
            reversedCellAttempts[1].definitions.front().identity &&
        qualifiedCellBlockCalls == 2 && qualifiedCellDefaultCalls == 2;

    const auto propertyValue = [](const auto& payload, const auto kind,
                                  const auto key, const LONG expected) {
        return std::any_of(
            payload.definitions.begin(), payload.definitions.end(),
            [kind, key, expected](const auto& definition) {
                return definition.kind == kind &&
                    definition.bodyState ==
                        hancom::graph::ObservationState::Value &&
                    std::any_of(
                        definition.properties.begin(),
                        definition.properties.end(),
                        [key, expected](const auto& property) {
                            return property.key == key &&
                                property.state ==
                                    hancom::graph::ObservationState::Value &&
                                property.integerValue == expected;
                        });
            });
    };
    const auto layoutRoot = [&environmentPlatform](
        const wchar_t* const label,
        const hancom::graph::capture::ReaderPayload& references,
        std::wstring* const root) {
        hancom::graph::layout::NativeLayoutObservationV1 native;
        native.pageCount = 1;
        hancom::graph::layout::LayoutNodeObservation paragraph;
        paragraph.nodeId = L"1:0";
        paragraph.pageStart = {
            hancom::graph::layout::ObservationState::Value, 1};
        paragraph.pageEnd = {
            hancom::graph::layout::ObservationState::Value, 1};
        native.nodes.push_back(std::move(paragraph));
        hancom::graph::layout::LayoutSnapshot snapshot;
        const bool sectionsBuilt =
            hancom::graph::layout::BuildSectionLayoutObservationsV1(
                references, &native.sections);
        std::wcout << L"WORKFLOW_COM_SECTION_DIAGNOSTIC " << label
                   << L" BUILD_SECTIONS=" << sectionsBuilt
                   << L" SECTION_COUNT=" << native.sections.size();
        if (!native.sections.empty()) {
            std::wcout << L" SETUP_STATES=";
            for (const auto& field : native.sections.front().setup) {
                std::wcout << static_cast<unsigned>(field.state);
            }
        }
        const bool snapshotBuilt = sectionsBuilt &&
            hancom::graph::layout::BuildNativeLayoutSnapshotV1(
                native, environmentPlatform, &snapshot);
        std::wcout << L" BUILD_SNAPSHOT=" << snapshotBuilt << L'\n';
        return snapshotBuilt &&
            hancom::graph::layout::ComputeLayoutRoot(snapshot, root) ==
                hancom::graph::layout::CaptureStatus::Complete;
    };
    std::wstring baselineRoot;
    std::wstring changedPageRoot;
    std::wstring changedColumnRoot;
    objects[5]->pageWidth = 61234;
    hancom::graph::capture::ReaderPayload changedPageClosure;
    hancom::graph::properties::ReferenceClosureDiagnostics
        changedPageDiagnostics;
    const bool changedPageCaptured =
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            objects[0], {sites.front()}, &changedPageClosure,
            &changedPageDiagnostics) ==
            hancom::graph::properties::CaptureStatus::Complete;
    objects[5]->pageWidth = 59528;
    objects[5]->columnCount = 2;
    hancom::graph::capture::ReaderPayload changedColumnClosure;
    hancom::graph::properties::ReferenceClosureDiagnostics
        changedColumnDiagnostics;
    const bool changedColumnCaptured =
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            objects[0], {sites.front()}, &changedColumnClosure,
            &changedColumnDiagnostics) ==
            hancom::graph::properties::CaptureStatus::Complete;
    objects[5]->columnCount = 1;
    const bool productionSectionPayloadValues =
        propertyValue(closure, hancom::graph::DefinitionKind::PageDef,
                      7000U, 59528) &&
        propertyValue(closure, hancom::graph::DefinitionKind::ColumnDef,
                      7100U, 1) &&
        propertyValue(changedPageClosure,
                      hancom::graph::DefinitionKind::PageDef,
                      7000U, 61234) &&
        propertyValue(changedColumnClosure,
                      hancom::graph::DefinitionKind::ColumnDef,
                      7100U, 2);
    const auto baselineColumn = std::find_if(
        closure.definitions.begin(), closure.definitions.end(),
        [](const auto& definition) {
            return definition.kind == hancom::graph::DefinitionKind::ColumnDef;
        });
    std::wcout << L"WORKFLOW_COM_COLUMN_DEFINITION_DIAGNOSTIC BODY=";
    if (baselineColumn == closure.definitions.end()) {
        std::wcout << L"MISSING";
    } else {
        std::wcout << static_cast<unsigned>(baselineColumn->bodyState)
                   << L" KEYS=";
        for (const auto key : {7100U, 7101U, 7102U}) {
            const auto property = std::find_if(
                baselineColumn->properties.begin(),
                baselineColumn->properties.end(),
                [key](const auto& candidate) {
                    return candidate.key == key;
                });
            std::wcout << key << L":"
                       << (property == baselineColumn->properties.end()
                               ? 99U
                               : static_cast<unsigned>(property->state))
                       << L",";
        }
    }
    std::wcout << L'\n';
    const bool baselineRootBuilt =
        layoutRoot(L"BASELINE", closure, &baselineRoot);
    const bool changedPageRootBuilt =
        layoutRoot(L"PAGE", changedPageClosure, &changedPageRoot);
    const bool changedColumnRootBuilt =
        layoutRoot(L"COLUMN", changedColumnClosure, &changedColumnRoot);
    const bool productionSectionPayloadAffectsLayoutRoot =
        productionSectionPayloadValues && changedPageCaptured &&
        changedColumnCaptured && baselineRootBuilt && changedPageRootBuilt &&
        changedColumnRootBuilt &&
        baselineRoot != changedPageRoot &&
        baselineRoot != changedColumnRoot;

    objects[5]->sectionPageSetupFixture = true;
    objects[5]->sectionOwnerLane = 0;
    objects[5]->sectionOwnerCalls = 0;
    objects[5]->pageOwnerCalls = 0;
    objects[5]->columnOwnerCalls = 0;
    objects[5]->pageSetupDefaultCalls = 0;
    objects[5]->columnDefaultCalls = 0;
    objects[5]->forbiddenPageProfileCalls = 0;
    hancom::graph::capture::ReaderPayload multiSectionClosure;
    hancom::graph::properties::ReferenceClosureDiagnostics
        multiSectionDiagnostics;
    const std::vector<hancom::graph::properties::ReferenceSite>
        multiSectionSites{
            {hancom::graph::capture::PropertyTarget::Section,
             L"0", {1,0,0}, 0},
            {hancom::graph::capture::PropertyTarget::Section,
             L"1", {9,0,0}, 1},
        };
    const bool multiSectionCaptured =
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            objects[0], multiSectionSites, &multiSectionClosure,
            &multiSectionDiagnostics) ==
            hancom::graph::properties::CaptureStatus::Complete &&
        multiSectionDiagnostics.visitedSites == 2;
    const auto definitionFor = [&multiSectionClosure](
        const std::uint64_t section, const hancom::graph::EdgeKind edge,
        const hancom::graph::DefinitionKind kind) {
        const auto reference = std::find_if(
            multiSectionClosure.definitionReferences.begin(),
            multiSectionClosure.definitionReferences.end(),
            [section, edge](const auto& value) {
                return value.source ==
                        hancom::graph::capture::PropertyTarget::Section &&
                    value.ordinal == section && value.edge == edge;
            });
        if (reference == multiSectionClosure.definitionReferences.end()) {
            return multiSectionClosure.definitions.end();
        }
        return std::find_if(
            multiSectionClosure.definitions.begin(),
            multiSectionClosure.definitions.end(),
            [&reference, kind](const auto& value) {
                return value.kind == kind &&
                    value.identity == reference->definitionIdentity;
            });
    };
    const auto exactProperty = [](const auto& definition,
                                  const std::uint32_t key,
                                  const hancom::graph::ScalarTag scalar,
                                  const std::int64_t expected) {
        const auto property = std::find_if(
            definition.properties.begin(), definition.properties.end(),
            [key](const auto& value) { return value.key == key; });
        return property != definition.properties.end() &&
            property->state == hancom::graph::ObservationState::Value &&
            property->scalar == scalar && property->integerValue == expected &&
            property->origin == hancom::graph::PropertyOrigin::Direct &&
            property->ownerField == 102;
    };
    const auto terminalProperty = [](const auto& definition,
                                     const std::uint32_t key) {
        const auto property = std::find_if(
            definition.properties.begin(), definition.properties.end(),
            [key](const auto& value) { return value.key == key; });
        return property != definition.properties.end() &&
            property->state == hancom::graph::ObservationState::NotExposed &&
            property->integerValue == 0 && property->ownerField == 102;
    };
    const auto page0 = definitionFor(
        0, hancom::graph::EdgeKind::PageDefRef,
        hancom::graph::DefinitionKind::PageDef);
    const auto page1 = definitionFor(
        1, hancom::graph::EdgeKind::PageDefRef,
        hancom::graph::DefinitionKind::PageDef);
    const auto column0 = definitionFor(
        0, hancom::graph::EdgeKind::ColumnDefRef,
        hancom::graph::DefinitionKind::ColumnDef);
    const auto column1 = definitionFor(
        1, hancom::graph::EdgeKind::ColumnDefRef,
        hancom::graph::DefinitionKind::ColumnDef);
    const bool multiSectionExactProperties = multiSectionCaptured &&
        page0 != multiSectionClosure.definitions.end() &&
        page1 != multiSectionClosure.definitions.end() &&
        column0 != multiSectionClosure.definitions.end() &&
        column1 != multiSectionClosure.definitions.end() &&
        page0->identity != page1->identity &&
        column0->identity != column1->identity &&
        exactProperty(*page0, 7000, hancom::graph::ScalarTag::HWPUNIT64, 60001) &&
        exactProperty(*page0, 7001, hancom::graph::ScalarTag::HWPUNIT64, 80002) &&
        exactProperty(*page0, 7002, hancom::graph::ScalarTag::HWPUNIT64, 0) &&
        exactProperty(*page0, 7008, hancom::graph::ScalarTag::HWPUNIT64, 0) &&
        exactProperty(*page0, 7009, hancom::graph::ScalarTag::Bool, 0) &&
        exactProperty(*page0, 7010, hancom::graph::ScalarTag::Enum, 0) &&
        exactProperty(*page1, 7000, hancom::graph::ScalarTag::HWPUNIT64, 61001) &&
        exactProperty(*page1, 7001, hancom::graph::ScalarTag::HWPUNIT64, 82002) &&
        exactProperty(*page1, 7002, hancom::graph::ScalarTag::HWPUNIT64, 3103) &&
        exactProperty(*page1, 7008, hancom::graph::ScalarTag::HWPUNIT64, 9109) &&
        exactProperty(*page1, 7009, hancom::graph::ScalarTag::Bool, 1) &&
        exactProperty(*page1, 7010, hancom::graph::ScalarTag::Enum, 2) &&
        exactProperty(*column0, 7100, hancom::graph::ScalarTag::Uint64, 1) &&
        exactProperty(*column0, 7101, hancom::graph::ScalarTag::Bool, 1) &&
        exactProperty(*column0, 7102, hancom::graph::ScalarTag::HWPUNIT64, 0) &&
        terminalProperty(*column0, 7104) &&
        exactProperty(*column1, 7100, hancom::graph::ScalarTag::Uint64, 2) &&
        exactProperty(*column1, 7101, hancom::graph::ScalarTag::Bool, 0) &&
        exactProperty(*column1, 7102, hancom::graph::ScalarTag::HWPUNIT64, 919) &&
        terminalProperty(*column1, 7104);
    std::vector<hancom::graph::layout::SectionLayoutObservation>
        multiSectionLayout;
    const bool multiSectionLayoutExact =
        hancom::graph::layout::BuildSectionLayoutObservationsV1(
            multiSectionClosure, &multiSectionLayout) &&
        multiSectionLayout.size() == 2 &&
        multiSectionLayout[0].sectionIndex == 0 &&
        multiSectionLayout[1].sectionIndex == 1 &&
        multiSectionLayout[0].setup.size() == 14 &&
        multiSectionLayout[0].setup[8].value == 0 &&
        multiSectionLayout[0].setup[9].value == 0 &&
        multiSectionLayout[0].setup[10].value == 0 &&
        multiSectionLayout[1].setup[8].value == 9109 &&
        multiSectionLayout[1].setup[9].value == 1 &&
        multiSectionLayout[1].setup[10].value == 2;
    for (const auto* const definition : {&*page0, &*page1, &*column0, &*column1}) {
        std::wcout << L"WORKFLOW_MULTI_SECTION_DEFINITION kind="
                   << static_cast<unsigned>(definition->kind) << L" properties=";
        for (const auto& property : definition->properties) {
            std::wcout << property.key << L":"
                       << static_cast<unsigned>(property.state) << L":"
                       << property.integerValue << L":"
                       << static_cast<unsigned>(property.origin) << L",";
        }
        std::wcout << L'\n';
    }
    const bool multiSectionOwnerRouting =
        objects[5]->sectionOwnerCalls == 2 &&
        objects[5]->pageOwnerCalls == 2 &&
        objects[5]->columnOwnerCalls == 2 &&
        objects[5]->pageSetupDefaultCalls == 2 &&
        objects[5]->columnDefaultCalls == 2 &&
        objects[5]->forbiddenPageProfileCalls == 0;
    objects[5]->sectionPageSetupFixture = false;
    objects[5]->sectionOwnerLane = 0;
    objects[0]->currentList = 1;

    const std::wstring coordinator = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tCAPTURE_COORDINATOR\nEND",
        &environmentPlatform);
    std::wcout << L"WORKFLOW_COORDINATOR_DIAGNOSTIC\n"
               << coordinator << L'\n';
    const bool coordinatorSparseRejected =
        coordinator.find(L"RESULT\tFAIL\tINCOMPLETE_CAPTURE") !=
            std::wstring::npos &&
        coordinator.find(L"ACTIVE_SERIAL\t0") != std::wstring::npos;
    objects[0]->currentList = 0;
    objects[0]->propertyOriginMask = 0;
    objects[0]->propertyOriginUnavailable = false;
    for (auto& object : objects) {
        object->coordinatorFixture = true;
    }
    const std::wstring coordinatorComplete = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tCAPTURE_COORDINATOR\nEND",
        &environmentPlatform);
    std::wcout << L"WORKFLOW_COORDINATOR_COMPLETE_DIAGNOSTIC\n"
               << coordinatorComplete << L'\n';
    const bool coordinatorPublished =
        coordinatorComplete.find(L"RESULT\tPASS") != std::wstring::npos &&
        coordinatorComplete.find(L"CAPTURE_STATUS\t0") != std::wstring::npos &&
        coordinatorComplete.find(L"CAPTURE_INTEGRITY\tComplete") !=
            std::wstring::npos &&
        coordinatorComplete.find(
            L"CAPTION_LOCATION\tORDINAL=1\tSTATE=0\tLIST=2\tPARAGRAPH=4\tCHARACTER=0") !=
            std::wstring::npos &&
        coordinatorComplete.find(
            L"CAPTION_LOCATION\tORDINAL=2\tSTATE=0\tLIST=5\tPARAGRAPH=6\tCHARACTER=0") !=
            std::wstring::npos &&
        coordinatorComplete.find(
            L"EFFECTIVE_PROPERTY_PRODUCER_OWNERSHIP\tPASS") !=
            std::wstring::npos &&
        coordinatorComplete.find(L"READER_CALLS\t14") != std::wstring::npos &&
        coordinatorComplete.find(L"ACTIVE_SERIAL\t1") != std::wstring::npos &&
        coordinatorComplete.find(L"REOPENED_SERIAL_1\t1") !=
            std::wstring::npos &&
        coordinatorComplete.find(L"SEALED_MANIFEST_580\t1") !=
            std::wstring::npos &&
        coordinatorComplete.find(L"READ_ONLY_GENERATION\t1") !=
            std::wstring::npos &&
        coordinatorComplete.find(
            L"LAYOUT_REQUIRED_FAMILIES\tPARAGRAPH\tCONTROL\tTABLE\tCELL\tIMAGE") !=
            std::wstring::npos &&
        coordinatorComplete.find(L"LAYOUT_TYPED_CAPTURE_PATH\tV1") !=
            std::wstring::npos &&
        coordinatorComplete.find(L"CLEANUP\tPASS") != std::wstring::npos;
    const bool captionQualificationMatrix =
        coordinatorComplete.find(
            L"CAPTION_QUALIFICATION_EXACT_OWNER_SESSION\t1") !=
            std::wstring::npos &&
        coordinatorComplete.find(
            L"CAPTION_QUALIFICATION_CROSS_OWNER_REJECTED\t1") !=
            std::wstring::npos &&
        coordinatorComplete.find(
            L"CAPTION_QUALIFICATION_STALE_SESSION_REJECTED\t1") !=
            std::wstring::npos &&
        coordinatorComplete.find(
            L"CAPTION_QUALIFICATION_UNSET_REJECTED\t1") !=
            std::wstring::npos;
    const bool propertyOriginCodecMatrix =
        objects[0]->propertyOriginMask == 0 &&
        !objects[0]->propertyOriginUnavailable;
    const bool productionImageCaptionObserved =
        objects[7]->shapeCaptionItemCalls != 0 &&
        objects[7]->captionTextItemCalls != 0 &&
        objects[9]->shapeCaptionItemCalls != 0 &&
        objects[9]->captionTextItemCalls != 0;
    const bool productionImageCaptionIndependentlyQualified =
        objects[0]->localMoveScanPosCalls != 0 &&
        objects[7]->captionListItemCalls == 0 &&
        objects[9]->captionListItemCalls == 0;
    const bool threeGenerationHashes =
        coordinatorComplete.find(L"GENERATION_RECORDS_SHA256\t\n") ==
            std::wstring::npos &&
        coordinatorComplete.find(L"GENERATION_INDEX_SHA256\t\n") ==
            std::wstring::npos &&
        coordinatorComplete.find(L"GENERATION_MANIFEST_SHA256\t\n") ==
            std::wstring::npos;
    const std::filesystem::path graphReadRoot =
        std::filesystem::temp_directory_path() /
        (L"hwp-graphread-production-workflow-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code graphReadCleanup;
    std::filesystem::remove_all(graphReadRoot, graphReadCleanup);
    hancom::graph::store::GraphStore graphReadStore(graphReadRoot.wstring());
    const hancom::graph::capture::CaptureStatus graphReadCapture =
        graphReadStore.Initialize()
            ? hancom::official_api::capability::CaptureDocumentGraphToStore(
                  objects[0], &graphReadStore, &environmentPlatform)
            : hancom::graph::capture::CaptureStatus::PublishFailed;
    hancom::graph::query::QueryView graphReadView{};
    hancom::graph::query::Status graphReadQuery =
        hancom::graph::query::Status::StorageFailure;
    hancom::graph::identity::DocumentSessionId session{};
    session.bytes = {0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x40, 0x70,
                     0x80, 0x90, 0xa0, 0xb0, 0xc0, 0xd0, 0xe0, 0xf0};
    if (graphReadCapture == hancom::graph::capture::CaptureStatus::Complete &&
        graphReadStore.PinActive() != nullptr) {
        hancom::graph::query::GraphStoreGenerationSource source(&graphReadStore);
        hancom::graph::query::Query query{};
        query.projectionBits = hancom::graph::query::ProjectStructure |
            hancom::graph::query::ProjectText |
            hancom::graph::query::ProjectProperties |
            hancom::graph::query::ProjectAssets |
            hancom::graph::query::ProjectReferences |
            hancom::graph::query::ProjectLayout;
        graphReadQuery = hancom::graph::query::BuildQueryView(
            &source, query, &graphReadView, &session);
    }
    const bool productionGraphReadReachable =
        graphReadCapture == hancom::graph::capture::CaptureStatus::Complete &&
        graphReadStore.PinActive() != nullptr &&
        graphReadQuery == hancom::graph::query::Status::Terminal &&
        !graphReadView.records.empty();

    using hancom::graph::codec::Bytes;
    using namespace hancom::graph::protocol;
    const auto appendBytes = [](Bytes* target, const Bytes& value) {
        target->insert(target->end(), value.begin(), value.end());
    };
    const auto unsigned64 = [](const std::uint64_t value) {
        return hancom::graph::codec::Uint64(value);
    };
    Bytes predicates(16, 0);
    predicates[0] = static_cast<std::uint8_t>(
        hancom::graph::ScalarTag::Struct);
    Bytes queryBytes;
    for (const Bytes& field : {
             EncodeField(2, 1, hancom::graph::ScalarTag::Uint8,
                         hancom::graph::codec::View(Bytes{3})),
             EncodeField(3, 1, hancom::graph::ScalarTag::Uint64,
                         hancom::graph::codec::View(unsigned64(0))),
             EncodeField(4, 1, hancom::graph::ScalarTag::Uint64,
                         hancom::graph::codec::View(unsigned64(0))),
             EncodeField(7, 3, hancom::graph::ScalarTag::Struct,
                         hancom::graph::codec::View(predicates), 0),
             EncodeField(8, 1, hancom::graph::ScalarTag::Uint64,
                         hancom::graph::codec::View(unsigned64(0xfff))),
             EncodeField(9, 1, hancom::graph::ScalarTag::Uint8,
                         hancom::graph::codec::View(Bytes{0}))}) {
        appendBytes(&queryBytes, field);
    }
    const Route graphReadRoute{91, 9091};
    std::uint64_t negotiatedGraphRead = 0;
    CleanupProcessState();
    const bool graphReadNegotiated = NegotiateCapabilities(
        session, kCapabilityGraphRead | kCapabilityAssetRead, graphReadRoute,
        &negotiatedGraphRead) == CapabilityNegotiationStatus::Negotiated &&
        negotiatedGraphRead ==
            (kCapabilityGraphRead | kCapabilityAssetRead);
    Bytes openPayload;
    const Bytes sessionBytes(session.bytes.begin(), session.bytes.end());
    for (const Bytes& field : {
             EncodeField(1, 1, hancom::graph::ScalarTag::UUID128,
                         hancom::graph::codec::View(sessionBytes)),
             EncodeField(3, 1, hancom::graph::ScalarTag::Uint64,
                         hancom::graph::codec::View(unsigned64(0x1f))),
             EncodeField(4, 1, hancom::graph::ScalarTag::Struct,
                         hancom::graph::codec::View(queryBytes)),
             EncodeField(5, 1, hancom::graph::ScalarTag::Uint64,
                         hancom::graph::codec::View(unsigned64(4096)))}) {
        appendBytes(&openPayload, field);
    }
    Header openHeader{};
    openHeader.message = MessageKind::GraphOpenRequest;
    openHeader.session = session;
    openHeader.profileBits = 0x1f;
    Bytes openRequest;
    const bool openEncoded = EncodeFrame(
        openHeader, hancom::graph::codec::View(openPayload), &openRequest);
    const Bytes openResponse = openEncoded
        ? ProcessRequest(
              MessageKind::GraphOpenRequest,
              hancom::graph::codec::View(openRequest), graphReadRoute,
              objects[0], &environmentPlatform)
        : Bytes{};
    Header openReceipt{};
    hancom::graph::codec::ByteView openReceiptPayload{};
    ErrorCode graphReadError{};
    std::vector<ParsedField> openFields;
    const bool openDecoded = !openResponse.empty() &&
        DecodeFrame(hancom::graph::codec::View(openResponse), &openReceipt,
                    &openReceiptPayload, &graphReadError) &&
        ParseFields(openReceiptPayload, &openFields, &graphReadError);
    const bool openReached =
        openDecoded && openReceipt.message == MessageKind::OpenReceipt;
    std::uint32_t openErrorCode = UINT32_MAX;
    const auto openCodeField = std::find_if(
        openFields.begin(), openFields.end(),
        [](const ParsedField& field) { return field.tag == 1; });
    if (openReceipt.message == MessageKind::Error &&
        openCodeField != openFields.end() && openCodeField->value.size == 4) {
        std::memcpy(&openErrorCode, openCodeField->value.data, 4);
    }
    std::wstring openErrorDetail;
    const auto openDetailField = std::find_if(
        openFields.begin(), openFields.end(),
        [](const ParsedField& field) { return field.tag == 4; });
    if (openDetailField != openFields.end() &&
        (openDetailField->value.size & 1U) == 0) {
        openErrorDetail.assign(
            reinterpret_cast<const wchar_t*>(openDetailField->value.data),
            static_cast<std::size_t>(openDetailField->value.size / 2));
    }
    hancom::graph::identity::GraphVersionV1 openVersion{};
    hancom::graph::identity::SerializedGraphVersionV1 serializedVersion{};
    if (openReached) {
        const auto versionField = std::find_if(
            openFields.begin(), openFields.end(),
            [](const ParsedField& field) { return field.tag == 2; });
        if (versionField != openFields.end()) {
            static_cast<void>(hancom::graph::identity::DeserializeGraphVersion(
                versionField->value.data, versionField->value.size,
                &openVersion));
            static_cast<void>(hancom::graph::identity::SerializeGraphVersion(
                openVersion, &serializedVersion));
        }
    }
    const Bytes cursorBytes(
        openReceipt.cursorOrUpload.bytes.begin(),
        openReceipt.cursorOrUpload.bytes.end());
    std::vector<Bytes> productionFrames;
    if (openReached) productionFrames.push_back(openResponse);
    Header nextReceipt{};
    bool sawGraphChunk = false;
    bool sawGraphTerminal = false;
    hancom::graph::Sha256 previousResponse = openReceipt.chainDigest;
    for (std::uint64_t sequence = 1;
         openReached && !sawGraphTerminal && sequence <= 10000;
         ++sequence) {
        const Bytes previousBytes(
            previousResponse.bytes.begin(), previousResponse.bytes.end());
        Bytes nextPayload;
        for (const Bytes& field : {
                 EncodeField(1, 1, hancom::graph::ScalarTag::UUID128,
                             hancom::graph::codec::View(cursorBytes)),
                 EncodeField(
                     2, 1, hancom::graph::ScalarTag::Struct,
                     {serializedVersion.data(), serializedVersion.size()}),
                 EncodeField(3, 1, hancom::graph::ScalarTag::Uint64,
                             hancom::graph::codec::View(unsigned64(4096))),
                 EncodeField(4, 1, hancom::graph::ScalarTag::Uint64,
                             hancom::graph::codec::View(unsigned64(sequence))),
                 EncodeField(5, 1, hancom::graph::ScalarTag::SHA256,
                             hancom::graph::codec::View(previousBytes))}) {
            appendBytes(&nextPayload, field);
        }
        Header nextHeader{};
        nextHeader.message = MessageKind::GraphNextRequest;
        nextHeader.sequence = sequence;
        nextHeader.cursorOrUpload = openReceipt.cursorOrUpload;
        nextHeader.previousChain = previousResponse;
        static_cast<void>(ApplyVersionToHeader(openVersion, &nextHeader));
        Bytes nextRequest;
        if (!EncodeFrame(nextHeader, hancom::graph::codec::View(nextPayload),
                         &nextRequest)) {
            break;
        }
        const Bytes nextResponse = ProcessRequest(
            MessageKind::GraphNextRequest,
            hancom::graph::codec::View(nextRequest), graphReadRoute,
            objects[0], &environmentPlatform);
        hancom::graph::codec::ByteView nextReceiptPayload{};
        if (nextResponse.empty() ||
            !DecodeFrame(hancom::graph::codec::View(nextResponse),
                         &nextReceipt, &nextReceiptPayload, &graphReadError)) {
            break;
        }
        productionFrames.push_back(nextResponse);
        sawGraphChunk = sawGraphChunk ||
            nextReceipt.message == MessageKind::GraphChunk;
        sawGraphTerminal =
            nextReceipt.message == MessageKind::GraphTerminal;
        if (!sawGraphTerminal &&
            nextReceipt.message != MessageKind::GraphChunk) {
            break;
        }
        previousResponse = nextReceipt.chainDigest;
    }
    bool productionBlobReadReachable = false;
    unsigned blobResponseKind = 0;
    std::vector<hancom::graph::query::BlobSlice> protocolBlobClosure;
    static_cast<void>(DebugBlobClosure(
        openReceipt.cursorOrUpload, &protocolBlobClosure));
    if (sawGraphTerminal && !protocolBlobClosure.empty()) {
        const auto& blob = protocolBlobClosure.front();
        const Bytes blobId(blob.id.bytes.begin(), blob.id.bytes.end());
        const Bytes blobDigest(blob.digest.bytes.begin(), blob.digest.bytes.end());
        Bytes blobPayload;
        for (const Bytes& field : {
                 EncodeField(1, 1, hancom::graph::ScalarTag::UUID128,
                             hancom::graph::codec::View(cursorBytes)),
                 EncodeField(
                     2, 1, hancom::graph::ScalarTag::Struct,
                     {serializedVersion.data(), serializedVersion.size()}),
                 EncodeField(3, 1, hancom::graph::ScalarTag::UUID128,
                             hancom::graph::codec::View(blobId)),
                 EncodeField(4, 1, hancom::graph::ScalarTag::Uint64,
                             hancom::graph::codec::View(unsigned64(blob.offset))),
                 EncodeField(5, 1, hancom::graph::ScalarTag::Uint64,
                             hancom::graph::codec::View(unsigned64(blob.length))),
                 EncodeField(6, 1, hancom::graph::ScalarTag::SHA256,
                             hancom::graph::codec::View(blobDigest)),
                 EncodeField(7, 1, hancom::graph::ScalarTag::Uint64,
                             hancom::graph::codec::View(unsigned64(0))),
                 EncodeField(8, 1, hancom::graph::ScalarTag::Uint64,
                             hancom::graph::codec::View(unsigned64(32768)))}) {
            appendBytes(&blobPayload, field);
        }
        Header blobHeader{};
        blobHeader.message = MessageKind::BlobReadRequest;
        blobHeader.cursorOrUpload = openReceipt.cursorOrUpload;
        static_cast<void>(ApplyVersionToHeader(openVersion, &blobHeader));
        Bytes blobRequest;
        if (EncodeFrame(blobHeader, hancom::graph::codec::View(blobPayload),
                        &blobRequest)) {
            blobResponseKind = 999;
            const Bytes blobResponse = ProcessRequest(
                MessageKind::BlobReadRequest,
                hancom::graph::codec::View(blobRequest), graphReadRoute,
                objects[0], &environmentPlatform);
            Header blobReceipt{};
            hancom::graph::codec::ByteView blobReceiptPayload{};
            productionBlobReadReachable = !blobResponse.empty() &&
                DecodeFrame(hancom::graph::codec::View(blobResponse),
                            &blobReceipt, &blobReceiptPayload,
                            &graphReadError) &&
                blobReceipt.message == MessageKind::BlobChunk &&
                blobReceipt.fragmentTotal == blob.length;
            if (!blobResponse.empty())
                blobResponseKind = static_cast<unsigned>(blobReceipt.message);
        }
    }
    const bool productionGraphOpenCursorReachable = graphReadNegotiated &&
        openReached && sawGraphChunk && sawGraphTerminal &&
        productionBlobReadReachable && DebugCursorCount() == 1;

    std::array<wchar_t, 32768> blobOutputPath{};
    const DWORD blobOutputLength = GetEnvironmentVariableW(
        L"HWP_GRAPHREAD_BLOB_OUTPUT", blobOutputPath.data(),
        static_cast<DWORD>(blobOutputPath.size()));
    if (productionGraphOpenCursorReachable && blobOutputLength != 0 &&
        blobOutputLength < blobOutputPath.size()) {
        const std::filesystem::path directory(blobOutputPath.data());
        std::error_code outputError;
        std::filesystem::create_directories(directory, outputError);
        for (const auto& blob : protocolBlobClosure) {
            Bytes binding(blob.id.bytes.begin(), blob.id.bytes.end());
            appendBytes(&binding, unsigned64(blob.offset));
            appendBytes(&binding, unsigned64(blob.length));
            binding.insert(binding.end(), blob.digest.bytes.begin(),
                           blob.digest.bytes.end());
            const auto key = hancom::graph::codec::Hash(
                hancom::graph::codec::View(binding));
            wchar_t name[65]{};
            for (std::size_t byte = 0; byte != key.bytes.size(); ++byte)
                swprintf_s(name + byte * 2, 3, L"%02x", key.bytes[byte]);
            Bytes content;
            if (DebugReadBlob(openReceipt.cursorOrUpload, blob, &content)) {
                std::ofstream output(directory / name,
                                     std::ios::binary | std::ios::trunc);
                output.write(reinterpret_cast<const char*>(content.data()),
                             static_cast<std::streamsize>(content.size()));
            }
        }
    }
    std::array<wchar_t, 32768> frameOutputPath{};
    const DWORD frameOutputLength = GetEnvironmentVariableW(
        L"HWP_GRAPHREAD_FRAME_OUTPUT", frameOutputPath.data(),
        static_cast<DWORD>(frameOutputPath.size()));
    if (productionGraphOpenCursorReachable && frameOutputLength != 0 &&
        frameOutputLength < frameOutputPath.size()) {
        std::ofstream output(
            std::filesystem::path(frameOutputPath.data()),
            std::ios::binary | std::ios::trunc);
        output.write("HGNG1", 5);
        const auto write64 = [&output](const std::uint64_t value) {
            std::array<std::uint8_t, 8> encoded{};
            for (unsigned byte = 0; byte != encoded.size(); ++byte) {
                encoded[byte] =
                    static_cast<std::uint8_t>(value >> (byte * 8));
            }
            output.write(
                reinterpret_cast<const char*>(encoded.data()),
                static_cast<std::streamsize>(encoded.size()));
        };
        write64(productionFrames.size());
        for (const Bytes& frame : productionFrames) {
            write64(frame.size());
            output.write(
                reinterpret_cast<const char*>(frame.data()),
                static_cast<std::streamsize>(frame.size()));
        }
    }
    CleanupProcessState();
    std::filesystem::remove_all(graphReadRoot, graphReadCleanup);
    objects[0]->localCaptionMatchCount = 0;
    const std::wstring zeroCaptionMatch = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tCAPTURE_COORDINATOR\nEND",
        &environmentPlatform);
    const bool zeroCaptionMatchTerminal =
        zeroCaptionMatch.find(L"RESULT\tPASS") != std::wstring::npos &&
        zeroCaptionMatch.find(L"CAPTURE_INTEGRITY\tComplete") !=
            std::wstring::npos &&
        zeroCaptionMatch.find(
            L"CAPTION_LOCATION\tORDINAL=1\tSTATE=2\tLIST=0\tPARAGRAPH=0\tCHARACTER=0") !=
            std::wstring::npos &&
        zeroCaptionMatch.find(
            L"CAPTION_LOCATION\tORDINAL=2\tSTATE=2\tLIST=0\tPARAGRAPH=0\tCHARACTER=0") !=
            std::wstring::npos;
    objects[0]->localCaptionMatchCount = 2;
    const unsigned beforeTwoCaptionMoves =
        objects[0]->localMoveScanPosCalls;
    const std::wstring twoCaptionMatches = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tCAPTURE_COORDINATOR\nEND",
        &environmentPlatform);
    const bool twoCaptionMatchesRejected =
        twoCaptionMatches.find(L"RESULT\tFAIL") != std::wstring::npos &&
        twoCaptionMatches.find(L"CAPTURE_STATUS\t2") != std::wstring::npos &&
        twoCaptionMatches.find(L"ACTIVE_SERIAL\t0") != std::wstring::npos;
    if (!twoCaptionMatchesRejected) {
        std::wcout << L"WORKFLOW_TWO_CAPTION_MATCH_DIAGNOSTIC moves="
                   << (objects[0]->localMoveScanPosCalls -
                       beforeTwoCaptionMoves) << L'\n'
                   << twoCaptionMatches << L'\n';
    }
    objects[0]->localCaptionMatchCount = 3;
    const std::wstring wrongOwnerCaptionMatch = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tCAPTURE_COORDINATOR\nEND",
        &environmentPlatform);
    const bool wrongOwnerCaptionRejectedForA =
        wrongOwnerCaptionMatch.find(L"RESULT\tPASS") != std::wstring::npos &&
        wrongOwnerCaptionMatch.find(
            L"CAPTION_LOCATION\tORDINAL=1\tSTATE=2\tLIST=0\tPARAGRAPH=0\tCHARACTER=0") !=
            std::wstring::npos &&
        wrongOwnerCaptionMatch.find(
            L"CAPTION_LOCATION\tORDINAL=2\tSTATE=0\tLIST=5\tPARAGRAPH=6\tCHARACTER=0") !=
            std::wstring::npos;
    objects[0]->localCaptionMatchCount = 1;
    for (auto& object : objects) object->coordinatorFixture = false;
    objects[0]->currentList = 1;
    const bool exactRestoration=objects[0]->localSetPosCalls>0 &&
        objects[0]->currentList==1 && objects[0]->localRestoredParagraph==0 &&
        objects[0]->localRestoredCharacter==0;
    objects[0]->localHwpmlCaptures=0;
    objects[0]->localSignatureDrift=true;
    const std::wstring signatureDrift=hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tTABLE_TOPOLOGY\n"
        L"FIXTURE_COVERAGE\thorizontal_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tvertical_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\trectangular_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tterminal_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tnested_table\tOBSERVED\nEND");
    objects[0]->localSignatureDrift=false;
    objects[0]->localHwpmlCaptures=0;
    objects[0]->localFailRestore=true;
    const std::wstring restoreFailure=hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tTABLE_TOPOLOGY\n"
        L"FIXTURE_COVERAGE\thorizontal_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tvertical_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\trectangular_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tterminal_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tnested_table\tOBSERVED\nEND");
    objects[0]->localFailRestore=false;
    const bool driftRejected=signatureDrift.find(
        L"RESULT\tFAIL\tSTATE_RESTORE_OR_CONTENT_SIGNATURE")!=std::wstring::npos;
    const bool restoreRejected=restoreFailure.find(
        L"RESULT\tFAIL\tSTATE_RESTORE_OR_CONTENT_SIGNATURE")!=std::wstring::npos;
    const bool containmentClassified =
        containment.find(L"CONTROL_ORDER\tAGREE")!=std::wstring::npos &&
        containment.find(L"CHILD_CONTAINMENT\tNOT_EXPOSED")!=std::wstring::npos &&
        containment.find(L"RESULT\tINCONCLUSIVE")!=std::wstring::npos;
    if (!containmentClassified)
        std::wcout << L"WORKFLOW_CONTAINMENT_FAILURE\n" << containment << L'\n';
    if (table.find(L"RESULT\tPASS") == std::wstring::npos)
        std::wcout << L"WORKFLOW_TABLE_FAILURE\n" << table << L'\n';
    std::wcout << L"WORKFLOW_PRODUCTION_GRAPHREAD_CAPTURE_QUERY "
               << productionGraphReadReachable << L" CAPTURE="
               << static_cast<unsigned>(graphReadCapture) << L" QUERY="
               << static_cast<unsigned>(graphReadQuery) << L" BYTES="
               << graphReadView.records.size() << L'\n'
               << L"WORKFLOW_PRODUCTION_GRAPHOPEN_CURSOR "
               << productionGraphOpenCursorReachable << L" NEGOTIATED="
               << graphReadNegotiated << L" OPEN="
               << static_cast<unsigned>(openReceipt.message) << L" NEXT="
               << static_cast<unsigned>(nextReceipt.message) << L" ERROR="
               << static_cast<unsigned>(graphReadError) << L" OPEN_CODE="
               << openErrorCode << L" DETAIL=" << openErrorDetail
               << L" BLOBS=" << protocolBlobClosure.size()
               << L" BLOB_READ=" << productionBlobReadReachable
               << L" BLOB_KIND=" << blobResponseKind << L'\n'
               << L"WORKFLOW_CONTAINMENT_RESULT "
               << containmentClassified << L'\n'
               << L"WORKFLOW_TABLE_RESULT "
               << (table.find(L"RESULT\tPASS")!=std::wstring::npos) << L'\n'
               << L"WORKFLOW_SELECTED_TABLE_LAYOUT_PRESERVED "
               << selectedTableLayoutPreserved << L'\n'
               << L"WORKFLOW_PRODUCTION_TYPED_LAYOUT_PATH "
               << (coordinatorComplete.find(L"LAYOUT_TYPED_CAPTURE_PATH\tV1") !=
                   std::wstring::npos) << L'\n'
               << L"WORKFLOW_COM_SECTION_PROPERTY_VALUES "
               << productionSectionPayloadValues << L'\n'
               << L"WORKFLOW_COM_SECTION_LAYOUT_ROOTS_BUILT "
               << baselineRootBuilt << changedPageRootBuilt
               << changedColumnRootBuilt << L'\n'
               << L"WORKFLOW_COM_SECTION_LAYOUT_ROOT_DIFFERENCES "
               << (baselineRoot != changedPageRoot)
               << (baselineRoot != changedColumnRoot) << L'\n'
               << L"WORKFLOW_COM_SECTION_PROPERTY_ROOT_SENSITIVITY "
               << productionSectionPayloadAffectsLayoutRoot << L'\n'
               << L"WORKFLOW_MULTI_SECTION_PAGE_SETUP_EXACT "
               << multiSectionExactProperties << L'\n'
               << L"WORKFLOW_MULTI_SECTION_LAYOUT_EXACT "
               << multiSectionLayoutExact << L'\n'
               << L"WORKFLOW_MULTI_SECTION_OWNER_CALLS "
               << multiSectionOwnerRouting << L" HSECDEF="
               << objects[5]->sectionOwnerCalls << L" PAGEDEF="
               << objects[5]->pageOwnerCalls << L" HCOLDEF="
               << objects[5]->columnOwnerCalls << L" PAGESETUP="
               << objects[5]->pageSetupDefaultCalls << L" MULTICOLUMN="
               << objects[5]->columnDefaultCalls << L" FORBIDDEN="
               << objects[5]->forbiddenPageProfileCalls << L'\n'
               << L"WORKFLOW_EXACT_RESTORATION " << exactRestoration << L'\n'
               << L"WORKFLOW_SIGNATURE_DRIFT_REJECTED " << driftRejected << L'\n'
               << L"WORKFLOW_RESTORE_FAILURE_REJECTED " << restoreRejected << L'\n'
               << L"WORKFLOW_REFERENCE_CLOSURE_FAKE_IDISPATCH "
               << referenceClosure << L'\n'
               << L"WORKFLOW_CELL_REFERENCE_ATTEMPT_ISOLATION "
               << cellReferenceAttemptIsolation << L" BLOCKS="
               << qualifiedCellBlockCalls << L" DEFAULTS="
               << qualifiedCellDefaultCalls << L'\n'
               << L"WORKFLOW_COM_COORDINATOR_SPARSE_REJECTED "
               << coordinatorSparseRejected << L'\n'
               << L"WORKFLOW_COM_COORDINATOR_PUBLISHED "
               << coordinatorPublished << L'\n'
               << L"WORKFLOW_PROPERTY_ORIGIN_CODEC_MATRIX "
               << propertyOriginCodecMatrix << L'\n'
               << L"WORKFLOW_COM_COORDINATOR_THREE_HASHES "
               << threeGenerationHashes << L'\n'
               << L"WORKFLOW_COM_PRODUCTION_IMAGE_CAPTION "
               << productionImageCaptionObserved << L'\n'
               << L"WORKFLOW_COM_CAPTION_LOCATION_INDEPENDENT "
               << productionImageCaptionIndependentlyQualified << L'\n'
               << L"WORKFLOW_COM_CAPTION_QUALIFICATION_MATRIX "
               << captionQualificationMatrix << L'\n'
               << L"WORKFLOW_COM_CAPTION_ZERO_MATCH_TERMINAL "
               << zeroCaptionMatchTerminal << L'\n'
               << L"WORKFLOW_COM_CAPTION_TWO_MATCH_FAIL_CLOSED "
               << twoCaptionMatchesRejected << L'\n'
               << L"WORKFLOW_COM_CAPTION_WRONG_OWNER_REJECTED_FOR_A "
               << wrongOwnerCaptionRejectedForA << L'\n';
    return productionGraphReadReachable &&
        productionGraphOpenCursorReachable && containmentClassified &&
        selectedTableLayoutPreserved &&
        productionSectionPayloadAffectsLayoutRoot &&
        multiSectionExactProperties && multiSectionLayoutExact &&
        multiSectionOwnerRouting &&
        table.find(L"RESULT\tPASS")!=std::wstring::npos && exactRestoration &&
        driftRejected && restoreRejected && referenceClosure &&
        cellReferenceAttemptIsolation && coordinatorSparseRejected &&
        coordinatorPublished &&
        propertyOriginCodecMatrix && threeGenerationHashes &&
        productionImageCaptionObserved &&
        productionImageCaptionIndependentlyQualified &&
        captionQualificationMatrix && zeroCaptionMatchTerminal &&
        twoCaptionMatchesRejected &&
        wrongOwnerCaptionRejectedForA &&
        table.find(L"TYPED_REPEAT_HEADER\tMATCH")!=std::wstring::npos &&
        table.find(L"TYPED_CELL_HEADER\tMATCH")!=std::wstring::npos &&
        table.find(L"GENERIC_PARAMETERSET_COMPARE\tMATCH")!=std::wstring::npos;
}

bool LogicalWrapperIdentitySmoke() {
    CComPtr<WorkflowDispatch> objects[7];
    for (int index=0; index<7; ++index) {
        CComObjectNoLock<WorkflowDispatch>* raw =
            new (std::nothrow) CComObjectNoLock<WorkflowDispatch>();
        if (raw == nullptr) return false;
        objects[index] = raw;
        raw->role = index==6 ? WorkflowRole::Control :
            static_cast<WorkflowRole>(index);
    }
    for (auto& object : objects) {
        object->control=objects[1];
        object->table=objects[2];
        object->cell=objects[3];
        object->range=objects[4];
        object->parameterSet=objects[5];
        object->nestedControl=objects[6];
        object->sharedRow=&objects[0]->row;
        object->sharedColumn=&objects[0]->column;
        object->sharedList=&objects[0]->currentList;
        object->signatureDrift=&objects[0]->localSignatureDrift;
        object->failRestore=&objects[0]->localFailRestore;
        object->hwpmlCaptures=&objects[0]->localHwpmlCaptures;
        object->setPosCalls=&objects[0]->localSetPosCalls;
        object->restoredParagraph=&objects[0]->localRestoredParagraph;
        object->restoredCharacter=&objects[0]->localRestoredCharacter;
    }
    objects[0]->separateLogicalWrappers=true;
    objects[0]->currentList=0;
    const std::wstring containment = hancom::official_api::Probe(
        objects[0],
        L"HCV1\nCAPABILITY\tCONTAINMENT\n"
        L"FIXTURE_COVERAGE\tshared_header_footer\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tnotes\tOBSERVED\n"
        L"FIXTURE_COVERAGE\ttext_boxes\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tcaptions\tOBSERVED\nEND");
    const bool accepted =
        containment.find(L"CONTROL_ORDER\tAGREE")!=std::wstring::npos &&
        containment.find(L"CHILD_CONTAINMENT\tNOT_EXPOSED")!=std::wstring::npos &&
        containment.find(L"RESULT\tINCONCLUSIVE")!=std::wstring::npos &&
        containment.find(L"SET_MISMATCH")==std::wstring::npos;
    if (!accepted) {
        std::wcout << L"LOGICAL_WRAPPER_FAILURE\n" << containment << L'\n';
    }
    return accepted;
}

bool StrictRawInvokeSmoke() {
    CComObjectNoLock<ScriptDispatch>* raw =
        new (std::nothrow) CComObjectNoLock<ScriptDispatch>();
    if (raw == nullptr) return false;
    CComPtr<ScriptDispatch> dispatch(raw);
    VARIANTARG first{};
    first.vt = VT_I4;
    first.lVal = 2;
    VARIANTARG second{};
    second.vt = VT_I4;
    second.lVal = 0;
    VARIANTARG third{};
    third.vt = VT_I4;
    third.lVal = 0;
    dispatch->expectedArguments = {third, second, first};
    dispatch->scriptedResult.vt = VT_BOOL;
    dispatch->scriptedResult.boolVal = VARIANT_TRUE;

    const CallSpec spec{L"MovePos", 10016, DISPATCH_METHOD, 3, VT_BOOL};
    const RawCall call = hancom::official_api::capability::InvokeOneShot(
        dispatch, spec, {first, second, third}, true);
    const bool success = call.semantic ==
            hancom::official_api::capability::SemanticStatus::Pass &&
        call.getIdsStatus == S_OK && call.invokeStatus == S_OK &&
        call.result.vt == VT_BOOL && call.result.boolVal == VARIANT_TRUE &&
        dispatch->getIdsCalls == 1 && dispatch->invokeCalls == 1 &&
        dispatch->sawExactInvoke;

    VARIANTARG seven{}; seven.vt = VT_I4; seven.lVal = 7;
    VARIANTARG flags{}; flags.vt = VT_I4; flags.lVal = 0x77;
    dispatch->expectedName = L"InitScan";
    dispatch->expectedDispid = 10017;
    dispatch->returnedDispid = 10017;
    dispatch->expectedArguments = {third,third,third,third,flags,seven};
    const CallSpec initSpec{L"InitScan",10017,DISPATCH_METHOD,6,VT_BOOL};
    const unsigned beforeInit = dispatch->invokeCalls;
    const RawCall initCall = hancom::official_api::capability::InvokeOneShot(
        dispatch, initSpec, {seven,flags,third,third,third,third}, true);
    std::wcout << L"TYPEINFO_MOVEPOS " << static_cast<int>(call.semantic)
               << L' ' << call.typeInfoStatus << L" INITSCAN "
               << static_cast<int>(initCall.semantic) << L' '
               << initCall.typeInfoStatus << L'\n';
    const bool initSuccess = initCall.semantic ==
            hancom::official_api::capability::SemanticStatus::Pass &&
        dispatch->invokeCalls == beforeInit + 1 && dispatch->sawExactInvoke;
    dispatch->expectedName = L"MovePos";
    dispatch->expectedDispid = 10016;
    dispatch->returnedDispid = 10016;
    dispatch->expectedArguments = {third, second, first};

    dispatch->invokeStatus = DISP_E_TYPEMISMATCH;
    dispatch->scriptedArgError = 2;
    dispatch->scriptedDescription = L"wrong coordinate";
    dispatch->scriptedScode = E_INVALIDARG;
    const RawCall failure = hancom::official_api::capability::InvokeOneShot(
        dispatch, spec, {first, second, third}, false);
    const bool exactFailure = failure.semantic ==
            hancom::official_api::capability::SemanticStatus::ComFailure &&
        failure.invokeStatus == DISP_E_TYPEMISMATCH && failure.argumentError == 2 &&
        failure.exception.scode == E_INVALIDARG &&
        failure.exception.descriptionPresent &&
        failure.exception.description == L"wrong coordinate";
    dispatch->scriptedDescription = L"";
    const RawCall allocatedEmpty = hancom::official_api::capability::InvokeOneShot(
        dispatch, spec, {first,second,third}, false);
    const bool bstrPresencePreserved = allocatedEmpty.exception.descriptionPresent &&
        allocatedEmpty.exception.description.empty() &&
        !allocatedEmpty.exception.sourcePresent && !allocatedEmpty.exception.helpFilePresent;

    dispatch->getIdsStatus = DISP_E_UNKNOWNNAME;
    const RawCall unknown = hancom::official_api::capability::InvokeOneShot(
        dispatch, spec, {first, second, third}, false);
    dispatch->getIdsStatus = S_OK;
    dispatch->returnedDispid = 99;
    const RawCall mismatch = hancom::official_api::capability::InvokeOneShot(
        dispatch, spec, {first, second, third}, false);
    dispatch->returnedDispid = dispatch->expectedDispid;
    dispatch->invokeStatus = S_OK;
    dispatch->scriptedDescription = nullptr;
    dispatch->scriptedResult.vt = VT_I4;
    dispatch->scriptedResult.lVal = 1;
    const RawCall malformed = hancom::official_api::capability::InvokeOneShot(
        dispatch, spec, {first, second, third}, false);
    dispatch->scriptedResult.vt = VT_BOOL;
    dispatch->scriptedResult.boolVal = VARIANT_FALSE;
    const RawCall falseResult = hancom::official_api::capability::InvokeOneShot(
        dispatch, spec, {first, second, third}, false);
    const CallSpec nullSpec{
        L"MovePos", 10016, DISPATCH_METHOD, 3, VT_DISPATCH};
    dispatch->scriptedResult.vt = VT_DISPATCH;
    dispatch->scriptedResult.pdispVal = nullptr;
    const RawCall nullResult = hancom::official_api::capability::InvokeOneShot(
        dispatch, nullSpec, {first, second, third}, false);
    const bool negativeBranches =
        unknown.semantic == hancom::official_api::capability::SemanticStatus::NameFailure &&
        mismatch.semantic == hancom::official_api::capability::SemanticStatus::ContractMismatch &&
        malformed.semantic == hancom::official_api::capability::SemanticStatus::MalformedOutput &&
        falseResult.semantic == hancom::official_api::capability::SemanticStatus::FalseResult &&
        nullResult.semantic == hancom::official_api::capability::SemanticStatus::NullResult;

    const std::wstring typeLib = hancom::official_api::Probe(
        dispatch, L"HCV1\nCAPABILITY\tTYPELIB_EXTENSION\nEND");
    std::wcout << typeLib << L'\n';
    const bool strictCounts =
        dispatch->getIdsCalls == 9 && dispatch->invokeCalls == 7;
    const std::wstring containment = hancom::official_api::Probe(
        dispatch,
        L"HCV1\nCAPABILITY\tCONTAINMENT\n"
        L"FIXTURE_COVERAGE\tshared_header_footer\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tnotes\tOBSERVED\n"
        L"FIXTURE_COVERAGE\ttext_boxes\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tcaptions\tOBSERVED\nEND");
    const std::wstring table = hancom::official_api::Probe(
        dispatch,
        L"HCV1\nCAPABILITY\tTABLE_TOPOLOGY\n"
        L"FIXTURE_COVERAGE\thorizontal_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tvertical_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\trectangular_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tterminal_merge\tOBSERVED\n"
        L"FIXTURE_COVERAGE\tnested_table\tOBSERVED\nEND");
    const bool stateFailuresAreExact =
        containment.find(L"RESULT\tFAIL\tSTATE_CAPTURE_OR_SIGNATURE") !=
            std::wstring::npos &&
        table.find(L"RESULT\tFAIL\tSTATE_CAPTURE_OR_SIGNATURE") !=
            std::wstring::npos;
    return success && initSuccess && exactFailure && bstrPresencePreserved &&
        negativeBranches && strictCounts &&
        stateFailuresAreExact &&
        typeLib.find(L"RESULT\tPASS") != std::wstring::npos;
}

bool QualificationOutputSmoke() {
    using hancom::official_api::capability::FixtureCoverage;
    const FixtureCoverage absent{};
    const std::wstring containment =
        hancom::official_api::capability::FormatFixturePrerequisites(
            L"CONTAINMENT", absent);
    const std::wstring table =
        hancom::official_api::capability::FormatFixturePrerequisites(
            L"TABLE_TOPOLOGY", absent);
    return containment.find(L"INCONCLUSIVE") != std::wstring::npos &&
        containment.find(L"NOT_OBSERVED") != std::wstring::npos &&
        table.find(L"terminal_merges=NOT_OBSERVED") != std::wstring::npos &&
        table.find(L"nested_tables=NOT_OBSERVED") != std::wstring::npos;
}

}

struct FakeReferenceClosureReceipt final {
    hancom::graph::properties::ReferenceClosureDiagnostics diagnostics{};
    std::map<std::wstring, std::uint64_t> memberInvokes{};
    hancom::dispatch::DispidCacheDiagnostics metadata{};
    unsigned invokes = 0;
    unsigned getIds = 0;
    unsigned setPos = 0;
};

bool BuildMeasuredFakeDispatchReferenceClosure(
    const std::vector<hancom::graph::properties::ReferenceSite>& sites,
    hancom::graph::capture::ReaderPayload* const output,
    FakeReferenceClosureReceipt* const receipt) {
    if (output == nullptr || sites.empty()) return false;
    CComPtr<WorkflowDispatch> objects[7];
    std::map<LONG, std::wstring> cellAddressesByList;
    std::map<LONG, std::wstring> tableInstancesByList;
    for (const auto& site : sites) {
        if (site.target !=
                hancom::graph::capture::PropertyTarget::Cell) continue;
        const size_t separator = site.identity.rfind(L':');
        if (separator == std::wstring::npos ||
            separator + 1 == site.identity.size() ||
            site.position.list < (std::numeric_limits<LONG>::min)() ||
            site.position.list > (std::numeric_limits<LONG>::max)()) {
            return false;
        }
        const auto [found, inserted] = cellAddressesByList.emplace(
            static_cast<LONG>(site.position.list),
            site.identity.substr(separator + 1));
        if (!inserted && found->second != site.identity.substr(separator + 1)) {
            std::wcout << L"REFERENCE_FIXTURE_CELL_LIST_COLLISION list="
                       << site.position.list << L" first=" << found->second
                       << L" second=" << site.identity.substr(separator + 1)
                       << L'\n';
            return false;
        }
        const size_t ownerSeparator = site.tableOwnerIdentity.rfind(L':');
        if (ownerSeparator == std::wstring::npos ||
            ownerSeparator + 1 == site.tableOwnerIdentity.size()) {
            return false;
        }
        tableInstancesByList.emplace(
            static_cast<LONG>(site.position.list),
            site.tableOwnerIdentity.substr(ownerSeparator + 1));
    }
    for (int index = 0; index < 7; ++index) {
        auto* const raw = new (std::nothrow)
            CComObjectNoLock<WorkflowDispatch>();
        if (raw == nullptr) return false;
        objects[index] = raw;
        raw->role = index == 6
            ? WorkflowRole::Control
            : static_cast<WorkflowRole>(index);
    }
    for (auto& object : objects) {
        object->control = objects[1];
        object->table = objects[2];
        object->cell = objects[3];
        object->range = objects[4];
        object->parameterSet = objects[5];
        object->nestedControl = objects[6];
        object->sharedRow = &objects[0]->row;
        object->sharedColumn = &objects[0]->column;
        object->sharedList = &objects[0]->currentList;
        object->scriptedCellAddresses = &cellAddressesByList;
        object->scriptedTableInstances = &tableInstancesByList;
        object->cellReferenceRegression = true;
        object->cellReferenceQualification = true;
        object->setPosCalls = &objects[0]->localSetPosCalls;
        object->restoredParagraph = &objects[0]->localRestoredParagraph;
        object->restoredCharacter = &objects[0]->localRestoredCharacter;
        object->getIdsCalls = &objects[0]->localGetIdsCalls;
        object->dispatchInvokeCalls = &objects[0]->localDispatchInvokeCalls;
        if (receipt != nullptr) {
            object->memberInvokeCalls = &receipt->memberInvokes;
        }
    }
    hancom::graph::properties::ReferenceClosureDiagnostics diagnostics;
    hancom::graph::Sha256 pageSetup{};
    output->reader =
        hancom::graph::capture::QualifiedReader::EffectiveProperties;
    output->outcome = hancom::graph::capture::ReaderOutcome::Complete;
    output->coverage = hancom::graph::CoverageState::Complete;
    const auto status =
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            objects[0], sites, output, &diagnostics);
    const bool hasSection = std::any_of(
        sites.begin(), sites.end(), [](const auto& site) {
            return site.target ==
                hancom::graph::capture::PropertyTarget::Section;
        });
    const bool pageSetupDerived =
        !hasSection || hancom::graph::properties::DerivePageSetupDigest(
            *output, &pageSetup);
    const bool complete =
        status == hancom::graph::properties::CaptureStatus::Complete &&
        diagnostics.visitedSites == sites.size() && pageSetupDerived &&
        objects[0]->currentList == 1;
    if (receipt != nullptr) {
        receipt->diagnostics = diagnostics;
        receipt->metadata = hancom::dispatch::ReadDispidCacheDiagnostics();
        receipt->invokes = objects[0]->localDispatchInvokeCalls;
        receipt->getIds = objects[0]->localGetIdsCalls;
        receipt->setPos = objects[0]->localSetPosCalls;
    }
    if (!complete) {
        std::wcout << L"REFERENCE_FIXTURE_CLOSURE_DETAIL status="
                   << static_cast<unsigned>(status) << L" expected="
                   << sites.size() << L" visited=" << diagnostics.visitedSites
                   << L" cells=" << diagnostics.cellSites
                   << L" fast_attempts="
                   << diagnostics.cellFastMode0Attempts << L" fast_hits="
                   << diagnostics.cellFastMode0Hits << L" fast_rejections="
                   << diagnostics.cellFastMode0Rejections << L" fallback="
                   << diagnostics.cellQualifiedFallbackCalls
                   << L" cell_blocks="
                   << diagnostics.cellTableCellBlockCalls
                   << L" page_setup=" << pageSetupDerived
                   << L" restored_list=" << objects[0]->currentList << L'\n';
    }
    return complete;
}

bool BuildFakeDispatchReferenceClosure(
    const std::vector<hancom::graph::properties::ReferenceSite>& sites,
    hancom::graph::capture::ReaderPayload* const output) {
    return BuildMeasuredFakeDispatchReferenceClosure(sites, output, nullptr);
}

bool FakeDispatchReferenceClosureQualification() {
    const std::vector<hancom::graph::properties::ReferenceSite> sites{
        {hancom::graph::capture::PropertyTarget::Section,
         L"0", {1,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Paragraph,
         L"1:0", {1,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Run,
         L"1:0:0:4", {1,0,0}, 0},
        {hancom::graph::capture::PropertyTarget::Cell,
         L"script-owner:A1", {3,0,0}, 0},
    };
    hancom::graph::capture::ReaderPayload output;
    return BuildFakeDispatchReferenceClosure(sites, &output);
}

std::wstring ReferenceClosureDigest(
    const hancom::graph::capture::ReaderPayload& payload) {
    hancom::graph::codec::Bytes bytes;
    const auto integer = [&bytes](const std::uint64_t value) {
        const auto encoded = hancom::graph::codec::Uint64(value);
        bytes.insert(bytes.end(), encoded.begin(), encoded.end());
    };
    const auto text = [&bytes, &integer](const std::wstring& value) {
        integer(value.size());
        for (const wchar_t character : value) {
            bytes.push_back(static_cast<std::uint8_t>(character & 0xff));
            bytes.push_back(static_cast<std::uint8_t>((character >> 8) & 0xff));
        }
    };
    integer(payload.definitions.size());
    for (const auto& definition : payload.definitions) {
        integer(static_cast<unsigned>(definition.kind));
        integer(static_cast<unsigned>(definition.nativeIdState));
        integer(static_cast<std::uint64_t>(definition.nativeId));
        integer(static_cast<unsigned>(definition.bodyState));
        text(definition.identity);
        bytes.insert(bytes.end(), definition.propertyDigest.bytes.begin(),
                     definition.propertyDigest.bytes.end());
        integer(definition.properties.size());
        for (const auto& property : definition.properties) {
            integer(property.key);
            integer(static_cast<unsigned>(property.scalar));
            integer(static_cast<unsigned>(property.state));
            integer(static_cast<unsigned>(property.origin));
            integer(static_cast<std::uint64_t>(property.integerValue));
            text(property.textValue);
        }
    }
    integer(payload.definitionReferences.size());
    for (const auto& reference : payload.definitionReferences) {
        integer(static_cast<unsigned>(reference.source));
        text(reference.sourceIdentity);
        integer(static_cast<unsigned>(reference.edge));
        text(reference.definitionIdentity);
        integer(reference.ordinal);
    }
    integer(payload.coverageFacts.size());
    for (const auto& coverage : payload.coverageFacts) {
        integer(static_cast<unsigned>(coverage.target));
        text(coverage.targetIdentity);
        integer(coverage.ownerField);
        integer(static_cast<unsigned>(coverage.state));
        text(coverage.detail);
    }
    integer(payload.referenceTraversal.expectedSites);
    integer(payload.referenceTraversal.visitedSites);
    const hancom::graph::Sha256 digest = hancom::graph::codec::DomainHash(
        "HWPGRAPH\0REFERENCECLOSUREPROFILE\0V1",
        hancom::graph::codec::View(bytes));
    static constexpr wchar_t hex[] = L"0123456789abcdef";
    std::wstring result;
    result.reserve(64);
    for (const std::uint8_t byte : digest.bytes) {
        result.push_back(hex[byte >> 4]);
        result.push_back(hex[byte & 15]);
    }
    return result;
}

struct HeapLiveSnapshot final {
    std::uint64_t blocks = 0;
    std::uint64_t bytes = 0;
};

HeapLiveSnapshot ReadHeapLiveSnapshot() noexcept {
    HeapLiveSnapshot result;
    HANDLE heap = GetProcessHeap();
    if (heap == nullptr || !HeapLock(heap)) return result;
    PROCESS_HEAP_ENTRY entry{};
    while (HeapWalk(heap, &entry)) {
        if ((entry.wFlags & PROCESS_HEAP_ENTRY_BUSY) != 0) {
            ++result.blocks;
            result.bytes += entry.cbData;
        }
    }
    HeapUnlock(heap);
    return result;
}

class CapturedCellReferenceSource final
    : public hancom::graph::properties::ReferenceClosureSource {
public:
    explicit CapturedCellReferenceSource(
        const hancom::graph::capture::ReaderPayload& captured) {
        if (!captured.definitions.empty()) {
            const auto& source = captured.definitions.front();
            definition_.kind = source.kind;
            definition_.nativeId = source.nativeId;
            definition_.nativeIdStatus = ToReadStatus(source.nativeIdState);
            definition_.bodyStatus = ToReadStatus(source.bodyState);
            for (const auto& property : source.properties) {
                hancom::graph::properties::PropertyObservation replay;
                replay.key = property.key;
                replay.scalar = property.scalar;
                replay.status = ToReadStatus(property.state);
                replay.origin = property.origin;
                replay.canonicalValue = property.scalar ==
                        hancom::graph::ScalarTag::UTF16
                    ? L"s:" + property.textValue
                    : L"i:" + std::to_wstring(property.integerValue);
                definition_.properties.push_back(std::move(replay));
            }
        }
    }

    bool ReadSite(
        const hancom::graph::properties::ReferenceSite&,
        hancom::graph::properties::ReferenceSiteObservation* const output)
        noexcept override {
        if (output == nullptr || definition_.properties.size() != 16) {
            return false;
        }
        try {
            output->definitions.push_back(definition_);
            output->references.push_back({
                hancom::graph::EdgeKind::BorderFillRef, 0, 0});
            ++visits;
            return true;
        } catch (...) {
            return false;
        }
    }

    std::uint64_t visits = 0;

private:
    static hancom::graph::properties::ReadStatus ToReadStatus(
        const hancom::graph::ObservationState state) noexcept {
        using hancom::graph::ObservationState;
        using hancom::graph::properties::ReadStatus;
        if (state == ObservationState::Value) return ReadStatus::Value;
        if (state == ObservationState::NotApplicable)
            return ReadStatus::NotApplicable;
        if (state == ObservationState::NotExposed)
            return ReadStatus::NotExposed;
        return ReadStatus::ReadFailed;
    }

    hancom::graph::properties::ReferencedDefinition definition_{};
};

bool ReferenceClosureProfileSmoke() {
    using hancom::graph::capture::PropertyTarget;
    using hancom::graph::capture::QualifiedReader;
    using hancom::graph::capture::ReaderPayload;
    using hancom::graph::properties::CaptureReferenceClosure;
    using hancom::graph::properties::CaptureStatus;
    using hancom::graph::properties::ReferenceClosureDiagnostics;
    using hancom::graph::properties::ReferenceSite;

    constexpr std::uint64_t kSites = 2077;
    constexpr std::uint64_t kColdLiveNanoseconds = 602069700;
    constexpr std::uint64_t kWarmLiveNanoseconds = 558256700;
    std::vector<ReferenceSite> sites;
    sites.reserve(kSites);
    for (std::uint64_t index = 0; index < kSites; ++index) {
        const std::uint64_t row = index / 67 + 1;
        std::uint64_t column = index % 67 + 1;
        std::wstring letters;
        while (column != 0) {
            --column;
            letters.push_back(static_cast<wchar_t>(L'A' + column % 26));
            column /= 26;
        }
        std::reverse(letters.begin(), letters.end());
        sites.push_back({
            PropertyTarget::Cell,
            L"closure-cell:" + letters + std::to_wstring(row),
            {static_cast<std::int64_t>(index + 1), 0, 0}, 0, {},
            L"5:tbl:1:0:1:0:1:closure-table",
        });
    }

    const auto run = [&sites](const bool resetMetadata) {
        if (resetMetadata) hancom::dispatch::ResetDispidCacheDiagnostics();
        struct Result final {
            bool complete = false;
            ReaderPayload payload{};
            FakeReferenceClosureReceipt receipt{};
            std::wstring digest{};
        } result;
        result.payload.reader = QualifiedReader::EffectiveProperties;
        result.complete = BuildMeasuredFakeDispatchReferenceClosure(
            sites, &result.payload, &result.receipt);
        result.digest = ReferenceClosureDigest(result.payload);
        return result;
    };

    const auto resetFirst = run(true);
    const auto resetSecond = run(true);
    const auto persistentFirst = run(true);
    const auto persistentSecond = run(false);
    const std::wstring authorityDigest = resetFirst.digest;
    const auto exactAttempt = [&](const auto& attempt) {
        return attempt.complete && attempt.receipt.diagnostics.expectedSites ==
                kSites && attempt.receipt.diagnostics.visitedSites == kSites &&
            attempt.receipt.diagnostics.cellFastMode0Attempts == kSites &&
            attempt.receipt.diagnostics.cellFastMode0Hits == kSites &&
            attempt.receipt.diagnostics.cellFastMode0Rejections == 0 &&
            attempt.receipt.diagnostics.cellQualifiedFallbackCalls == 0 &&
            attempt.payload.definitions.size() == 1 &&
            attempt.payload.definitions.front().properties.size() == 16 &&
            attempt.payload.definitionReferences.size() == kSites &&
            attempt.digest == authorityDigest;
    };
    const bool attemptsExact = exactAttempt(resetFirst) &&
        exactAttempt(resetSecond) && exactAttempt(persistentFirst) &&
        exactAttempt(persistentSecond);

    CapturedCellReferenceSource algorithmSource(resetFirst.payload);
    ReaderPayload algorithmPayload;
    algorithmPayload.reader = QualifiedReader::EffectiveProperties;
    ReferenceClosureDiagnostics algorithmDiagnostics;
    const HeapLiveSnapshot heapBefore = ReadHeapLiveSnapshot();
    LARGE_INTEGER qpcBefore{};
    LARGE_INTEGER qpcAfter{};
    LARGE_INTEGER qpcFrequency{};
    QueryPerformanceFrequency(&qpcFrequency);
    QueryPerformanceCounter(&qpcBefore);
    const CaptureStatus algorithmStatus = CaptureReferenceClosure(
        algorithmSource, sites, &algorithmPayload, &algorithmDiagnostics);
    QueryPerformanceCounter(&qpcAfter);
    const HeapLiveSnapshot heapAfter = ReadHeapLiveSnapshot();
    const std::wstring algorithmDigest =
        ReferenceClosureDigest(algorithmPayload);
    const bool algorithmExact = algorithmStatus == CaptureStatus::Complete &&
        algorithmSource.visits == kSites &&
        algorithmDiagnostics.visitedSites == kSites &&
        algorithmDigest == authorityDigest;
    const std::uint64_t algorithmQpcTicks =
        static_cast<std::uint64_t>(qpcAfter.QuadPart - qpcBefore.QuadPart);
    const std::int64_t heapBlockDelta =
        static_cast<std::int64_t>(heapAfter.blocks) -
        static_cast<std::int64_t>(heapBefore.blocks);
    const std::int64_t heapByteDelta =
        static_cast<std::int64_t>(heapAfter.bytes) -
        static_cast<std::int64_t>(heapBefore.bytes);

    const auto count = [](const FakeReferenceClosureReceipt& receipt,
                          const wchar_t* const member) {
        const auto found = receipt.memberInvokes.find(member);
        return found == receipt.memberInvokes.end()
            ? UINT64_C(0) : found->second;
    };
    const auto group = [&count](const FakeReferenceClosureReceipt& receipt,
                                const std::initializer_list<const wchar_t*> names) {
        std::uint64_t total = 0;
        for (const wchar_t* name : names) total += count(receipt, name);
        return total;
    };
    const auto& members = persistentFirst.receipt;
    const std::uint64_t setPos = count(members, L"SetPos");
    const std::uint64_t keyIndicator = count(members, L"KeyIndicator");
    const std::uint64_t owner = group(
        members, {L"ParentCtrl", L"CtrlID", L"GetCtrlInstID"});
    const std::uint64_t mode = count(members, L"SelectionMode");
    const std::uint64_t getDefault = count(members, L"GetDefault");
    const std::uint64_t border = group(members, {
        L"BorderTypeLeft", L"BorderWidthLeft", L"BorderCorlorLeft",
        L"BorderTypeRight", L"BorderWidthRight", L"BorderColorRight",
        L"BorderTypeTop", L"BorderWidthTop", L"BorderColorTop",
        L"BorderTypeBottom", L"BorderWidthBottom", L"BorderColorBottom"});
    const std::uint64_t fill = group(members, {
        L"FillAttr", L"WinBrushFaceColor", L"WinBrushHatchColor",
        L"WinBrushAlpha", L"WindowsBrush", L"Type", L"BrushType"});
    const std::uint64_t origin = count(members, L"PropertyOrigin");
    const std::uint64_t classified = setPos + keyIndicator + owner + mode +
        getDefault + border + fill + origin;
    const std::uint64_t other = members.invokes >= classified
        ? members.invokes - classified : 0;

    const auto virtualShare = [](const std::uint64_t liveNanoseconds,
                                 const std::uint64_t operations,
                                 const std::uint64_t total) {
        return total == 0 ? UINT64_C(0) :
            liveNanoseconds * operations / total;
    };
    const std::uint64_t candidateRemovedInvokes =
        (std::min)(fill, UINT64_C(4) * kSites);
    const std::uint64_t candidateAggregateCeiling =
        virtualShare(kColdLiveNanoseconds + kWarmLiveNanoseconds,
                     candidateRemovedInvokes * 2, members.invokes * 2);
    const std::uint64_t metadataResetSecondGetIds = resetSecond.receipt.getIds;
    const std::uint64_t metadataPersistentSecondGetIds =
        persistentSecond.receipt.getIds;
    const std::uint64_t metadataSavedGetIds =
        metadataResetSecondGetIds > metadataPersistentSecondGetIds
        ? metadataResetSecondGetIds - metadataPersistentSecondGetIds : 0;
    const std::uint64_t metadataAggregateCeiling =
        virtualShare(kColdLiveNanoseconds + kWarmLiveNanoseconds,
                     metadataSavedGetIds, members.invokes * 2);

    std::wcout << L"REFERENCE_CLOSURE_PROFILE attempts_exact=" << attemptsExact
               << L" sites=" << kSites << L" visited="
               << persistentFirst.receipt.diagnostics.visitedSites
               << L" fast_hits="
               << persistentFirst.receipt.diagnostics.cellFastMode0Hits
               << L" fallbacks="
               << persistentFirst.receipt.diagnostics.cellQualifiedFallbackCalls
               << L" values=16 origins=16 definitions="
               << persistentFirst.payload.definitions.size()
               << L" references="
               << persistentFirst.payload.definitionReferences.size()
               << L" digest=" << authorityDigest << L'\n'
               << L"REFERENCE_CLOSURE_MEMBER_COUNTS invokes=" << members.invokes
               << L" setpos=" << setPos << L" keyindicator=" << keyIndicator
               << L" owner=" << owner << L" mode=" << mode
               << L" getdefault=" << getDefault << L" border=" << border
               << L" fill=" << fill << L" origin=" << origin
               << L" other=" << other << L'\n'
               << L"REFERENCE_CLOSURE_ALGORITHM exact=" << algorithmExact
               << L" qpc_ticks=" << algorithmQpcTicks
               << L" qpc_frequency=" << qpcFrequency.QuadPart
               << L" heap_live_blocks_delta=" << heapBlockDelta
               << L" heap_live_bytes_delta=" << heapByteDelta
               << L" definition_assemblies=" << kSites
               << L" linear_conflict_probes=" << (kSites - 1)
               << L" linear_dedup_probes=" << (kSites - 1)
               << L" structural_map_keys=0 digest=" << algorithmDigest << L'\n'
               << L"REFERENCE_CLOSURE_LATENCY_TOGGLE injected_by_events=1"
               << L" sleeps=0 zero_latency_ns=0 cold_latency_ns="
               << kColdLiveNanoseconds << L" warm_latency_ns="
               << kWarmLiveNanoseconds << L" aggregate_latency_ns="
               << (kColdLiveNanoseconds + kWarmLiveNanoseconds) << L'\n'
               << L"REFERENCE_CLOSURE_METADATA_LIFETIME reset_second_getids="
               << metadataResetSecondGetIds
               << L" persistent_second_getids=" << metadataPersistentSecondGetIds
               << L" saved_getids=" << metadataSavedGetIds
               << L" aggregate_ceiling_ns=" << metadataAggregateCeiling << L'\n'
               << L"REFERENCE_CLOSURE_FILLATTR_CANDIDATE removed_invokes_per_attempt="
               << candidateRemovedInvokes << L" setpos_preserved=" << setPos
               << L" aggregate_ceiling_ns=" << candidateAggregateCeiling
               << L" threshold_ns=300000000 qualifies="
               << (candidateAggregateCeiling >= UINT64_C(300000000)) << L'\n';
    return attemptsExact && algorithmExact &&
        candidateAggregateCeiling < UINT64_C(300000000);
}

bool TableInstanceIdNativePresenceSmoke() {
    using hancom::graph::capture::QualifiedReader;
    using hancom::graph::capture::ReaderOutcome;
    using hancom::graph::capture::ReaderPayload;

    const auto observe = [](const bool available, const wchar_t* const value,
                            bool* const present, std::wstring* const raw) {
        CComPtr<WorkflowDispatch> root;
        CComPtr<WorkflowDispatch> table;
        CComPtr<WorkflowDispatch> range;
        auto* const rawRoot = new (std::nothrow)
            CComObjectNoLock<WorkflowDispatch>();
        auto* const rawTable = new (std::nothrow)
            CComObjectNoLock<WorkflowDispatch>();
        auto* const rawRange = new (std::nothrow)
            CComObjectNoLock<WorkflowDispatch>();
        if (rawRoot == nullptr || rawTable == nullptr || rawRange == nullptr) {
            delete rawRoot;
            delete rawTable;
            delete rawRange;
            return false;
        }
        root = rawRoot;
        table = rawTable;
        range = rawRange;
        root->role = WorkflowRole::Root;
        root->currentList = 0;
        table->role = WorkflowRole::Control;
        range->role = WorkflowRole::Range;
        root->control = table;
        root->table = table;
        root->range = range;
        table->range = range;
        table->identityFixture = true;
        table->fixtureCtrlId = L"tbl";
        table->fixtureInstanceId = value;
        table->instanceIdUnavailable = !available;
        ReaderPayload payload;
        hancom::official_api::capability::StorySpineReaderDiagnostics diagnostics;
        const bool captured =
            hancom::official_api::capability::CaptureStorySpineReaderPayload(
                root, &payload, &diagnostics);
        if (!captured || payload.reader != QualifiedReader::StorySpine ||
            payload.outcome != ReaderOutcome::Complete ||
            payload.controls.size() != 1 || !diagnostics.committed ||
            diagnostics.aborted) {
            return false;
        }
        *present = payload.controls[0].instanceIdPresent;
        *raw = payload.controls[0].instanceId;
        return true;
    };

    bool absentPresent = true;
    bool emptyPresent = false;
    bool valuePresent = false;
    std::wstring absentRaw = L"fabricated";
    std::wstring emptyRaw = L"fabricated";
    std::wstring valueRaw;
    const bool absent = observe(false, L"ignored", &absentPresent, &absentRaw);
    const bool empty = observe(true, L"", &emptyPresent, &emptyRaw);
    const bool value = observe(
        true, L"table-instance-exact-42", &valuePresent, &valueRaw);
    const bool exact = absent && empty && value && !absentPresent &&
        absentRaw.empty() && emptyPresent && emptyRaw.empty() &&
        valuePresent && valueRaw == L"table-instance-exact-42";
    const bool negativeCollapse = exact &&
        absentPresent != emptyPresent && emptyPresent == valuePresent &&
        emptyRaw != valueRaw;
    std::wcout << L"TABLE_NATIVE_INSTANCE_ID_ABSENT "
               << (absent && !absentPresent && absentRaw.empty()) << L'\n'
               << L"TABLE_NATIVE_INSTANCE_ID_PRESENT_EMPTY "
               << (empty && emptyPresent && emptyRaw.empty()) << L'\n'
               << L"TABLE_NATIVE_INSTANCE_ID_PRESENT_VALUE "
               << (value && valuePresent &&
                   valueRaw == L"table-instance-exact-42") << L'\n'
               << L"TABLE_NATIVE_INSTANCE_ID_NEGATIVE_COLLAPSE "
               << negativeCollapse << L'\n';
    return exact && negativeCollapse;
}

bool CellGeometryEpochCacheSmoke() {
    using hancom::inspection::TableCellFormat;
    using hancom::inspection::TableCellRecord;

    std::vector<TableCellRecord> cells(2);
    cells[0].tableInstanceId = L"geometry-table";
    cells[0].address = L"A1";
    cells[0].listId = 1;
    cells[0].row = 1;
    cells[0].column = 1;
    cells[1].tableInstanceId = L"geometry-table";
    cells[1].address = L"A2";
    cells[1].listId = 2;
    cells[1].row = 2;
    cells[1].column = 1;

    struct Lane final {
        bool exact = false;
        bool valuesExact = false;
        bool acquisitionsExact = false;
        LONG firstRight = -1;
        LONG secondRight = -1;
        LONG firstTop = -1;
        LONG secondTop = -1;
        unsigned cellShape = 0;
        unsigned shapeParameter = 0;
        unsigned shapeSet = 0;
        unsigned shapeTarget = 0;
        unsigned defaults = 0;
    };
    const auto run = [&cells](const bool forwarding,
                              const bool directCellShape) {
        GeometryEpochState state;
        state.forwarding = forwarding;
        state.directCellShape = directCellShape;
        GeometryEpochRootDispatch root(&state);
        std::wstring error;
        const std::vector<TableCellFormat> formats =
            hancom::inspection::ReadAllTableCellFormats(
                &root, L"geometry-table", cells, &error);
        const auto valueFor = [&formats](const wchar_t* const address,
                                         const bool top) {
            for (const TableCellFormat& format : formats) {
                if (std::find(format.addresses.begin(), format.addresses.end(),
                              address) != format.addresses.end()) {
                    return top ? format.marginTop : format.marginRight;
                }
            }
            return -1L;
        };
        Lane lane;
        lane.firstRight = valueFor(L"A1", false);
        lane.secondRight = valueFor(L"A2", false);
        lane.firstTop = valueFor(L"A1", true);
        lane.secondTop = valueFor(L"A2", true);
        lane.cellShape = state.cellShapeAcquisitions;
        lane.shapeParameter = state.shapeParameterAcquisitions;
        lane.shapeSet = state.shapeSetAcquisitions;
        lane.shapeTarget = state.shapeTargetAcquisitions;
        lane.defaults = state.geometryDefaults;
        lane.valuesExact = error.empty() && lane.firstRight == 850 &&
            lane.secondRight == 0 && lane.firstTop == 566 &&
            lane.secondTop == 141;
        lane.acquisitionsExact = directCellShape
            ? lane.cellShape == cells.size() && lane.shapeParameter == 0 &&
                lane.shapeSet == 0 && lane.shapeTarget == 0 &&
                lane.defaults == 0
            : lane.cellShape == 0 &&
                lane.shapeParameter == cells.size() &&
                lane.shapeSet == cells.size() &&
                lane.shapeTarget == cells.size() &&
                lane.defaults == cells.size();
        lane.exact = lane.valuesExact && lane.acquisitionsExact;
        return lane;
    };

    const Lane snapshotDirect = run(false, true);
    const Lane forwardingDirect = run(true, true);
    const Lane snapshotFallback = run(false, false);
    const Lane forwardingFallback = run(true, false);
    constexpr unsigned kObservedCells = 2077;
    constexpr unsigned kObservedTables = 55;
    constexpr unsigned kAdditionalAcquisitions =
        kObservedCells - kObservedTables;
    constexpr unsigned kAdditionalDirectInterfaceCalls =
        kAdditionalAcquisitions * 2;
    constexpr unsigned kMaximumFallbackInterfaceCalls =
        kAdditionalAcquisitions * 4;
    const bool liveCorrelationExact = kAdditionalAcquisitions == 2022 &&
        kAdditionalDirectInterfaceCalls == 4044 &&
        kMaximumFallbackInterfaceCalls == 8088;
    const bool passed = liveCorrelationExact && snapshotDirect.exact &&
        forwardingDirect.exact && snapshotFallback.exact &&
        forwardingFallback.exact;
    const auto print = [](const wchar_t* const name, const Lane& lane) {
        std::wcout << L" lane=" << name << L":" << lane.exact
                  << L":values=" << lane.valuesExact
                  << L":acquisitions=" << lane.acquisitionsExact
                  << L":" << lane.firstRight << L"->" << lane.secondRight
                  << L":" << lane.firstTop << L"->" << lane.secondTop
                  << L":cellshape=" << lane.cellShape
                  << L":shapeparam=" << lane.shapeParameter
                  << L":shapeset=" << lane.shapeSet
                  << L":shapetarget=" << lane.shapeTarget
                  << L":defaults=" << lane.defaults;
    };
    std::wcout << L"TABLE_CELL_GEOMETRY_EPOCH_CACHE " << passed
               << L" observed_cells=" << kObservedCells
               << L" observed_tables=" << kObservedTables
               << L" direct_added_getids=" << kAdditionalAcquisitions
               << L" direct_added_invoke=" << kAdditionalAcquisitions
               << L" direct_added_interface_calls="
               << kAdditionalDirectInterfaceCalls
               << L" fallback_max_added_getids="
               << kAdditionalDirectInterfaceCalls
               << L" fallback_max_added_invoke="
               << kAdditionalDirectInterfaceCalls
               << L" fallback_max_added_interface_calls="
               << kMaximumFallbackInterfaceCalls;
    print(L"snapshot-direct", snapshotDirect);
    print(L"forwarding-direct", forwardingDirect);
    print(L"snapshot-fallback", snapshotFallback);
    print(L"forwarding-fallback", forwardingFallback);
    std::wcout << L'\n';
    return passed;
}

bool RealTableReaderDuplicateEmptyParitySmoke() {
    using hancom::graph::capture::ControlObservation;
    using hancom::graph::capture::ReaderOutcome;
    using hancom::graph::capture::ReaderPayload;

    CComPtr<WorkflowDispatch> objects[7];
    for (int index = 0; index < 7; ++index) {
        auto* const raw = new (std::nothrow)
            CComObjectNoLock<WorkflowDispatch>();
        if (raw == nullptr) return false;
        objects[index] = raw;
        raw->role = index == 6
            ? WorkflowRole::Control
            : static_cast<WorkflowRole>(index);
    }
    for (auto& object : objects) {
        object->control = objects[1];
        object->table = objects[2];
        object->cell = objects[3];
        object->range = objects[4];
        object->parameterSet = objects[5];
        object->nestedControl = objects[6];
        object->sharedRow = &objects[0]->row;
        object->sharedColumn = &objects[0]->column;
        object->sharedList = &objects[0]->currentList;
        object->setPosCalls = &objects[0]->localSetPosCalls;
        object->typeInfoCalls = &objects[0]->localTypeInfoCalls;
        object->getIdsCalls = &objects[0]->localGetIdsCalls;
        object->dispatchInvokeCalls =
            &objects[0]->localDispatchInvokeCalls;
        object->headCtrlCalls = &objects[0]->localHeadCtrlCalls;
        object->nextCalls = &objects[0]->localNextCalls;
        object->tablePropertiesCalls =
            &objects[0]->localTablePropertiesCalls;
        object->cellRangeCalls = &objects[0]->localCellRangeCalls;
        object->spanNavigationCalls =
            &objects[0]->localSpanNavigationCalls;
        object->restoredParagraph = &objects[0]->localRestoredParagraph;
        object->restoredCharacter = &objects[0]->localRestoredCharacter;
        object->positionedControlA = objects[1];
        object->positionedControlB = objects[6];
        object->positionedCharacterB = 9;
        object->coordinatorFixture = true;
        object->directCellRangeExact = true;
        object->multiCellDirectFixture = true;
    }
    for (const size_t index : {size_t{1}, size_t{6}}) {
        objects[index]->identityFixture = true;
        objects[index]->fixtureCtrlId = L"tbl";
        objects[index]->fixtureInstanceId = L"";
        objects[index]->range = objects[4];
    }
    std::array<CComPtr<WorkflowDispatch>, 8> nativeControls{};
    nativeControls[3] = objects[1];
    nativeControls[7] = objects[6];
    for (size_t ordinal = 0; ordinal < nativeControls.size(); ++ordinal) {
        if (nativeControls[ordinal] == nullptr) {
            auto* const raw = new (std::nothrow)
                CComObjectNoLock<WorkflowDispatch>();
            if (raw == nullptr) return false;
            nativeControls[ordinal] = raw;
            raw->role = WorkflowRole::Control;
            raw->control = objects[1];
            raw->table = objects[2];
            raw->cell = objects[3];
            raw->range = objects[4];
            raw->parameterSet = objects[5];
            raw->sharedRow = &objects[0]->row;
            raw->sharedColumn = &objects[0]->column;
            raw->sharedList = &objects[0]->currentList;
            raw->setPosCalls = &objects[0]->localSetPosCalls;
            raw->typeInfoCalls = &objects[0]->localTypeInfoCalls;
            raw->getIdsCalls = &objects[0]->localGetIdsCalls;
            raw->dispatchInvokeCalls =
                &objects[0]->localDispatchInvokeCalls;
            raw->headCtrlCalls = &objects[0]->localHeadCtrlCalls;
            raw->nextCalls = &objects[0]->localNextCalls;
            raw->tablePropertiesCalls =
                &objects[0]->localTablePropertiesCalls;
            raw->cellRangeCalls = &objects[0]->localCellRangeCalls;
            raw->spanNavigationCalls =
                &objects[0]->localSpanNavigationCalls;
            raw->restoredParagraph = &objects[0]->localRestoredParagraph;
            raw->restoredCharacter = &objects[0]->localRestoredCharacter;
            raw->coordinatorFixture = true;
        }
    }
    for (size_t ordinal = 0; ordinal < nativeControls.size(); ++ordinal) {
        nativeControls[ordinal]->nextControl =
            ordinal + 1 < nativeControls.size()
                ? nativeControls[ordinal + 1]
                : nullptr;
    }
    objects[0]->nestedControl = nativeControls[0];
    objects[0]->authoritativeHead = nativeControls[0];
    const std::vector<ControlObservation> controls{
        {L"tbl", L"", 3, {0, 0, 5}},
        {L"tbl", L"", 7, {0, 0, 9}},
    };
    const std::vector<ControlObservation> reversedControls{
        controls[1], controls[0]};
    ReaderPayload acquired;
    ReaderPayload reversedAcquired;
    std::wstring acquiredFailure;
    std::wstring reversedFailure;
    objects[0]->localHeadCtrlCalls = 0;
    objects[0]->localNextCalls = 0;
    objects[0]->localTablePropertiesCalls = 0;
    objects[0]->localCellRangeCalls = 0;
    objects[0]->localSpanNavigationCalls = 0;
    wchar_t priorProgressPath[32768]{};
    const DWORD priorProgressPathLength = GetEnvironmentVariableW(
        L"TODO18_CAPTURE_PROGRESS_PATH", priorProgressPath,
        static_cast<DWORD>(std::size(priorProgressPath)));
    const bool priorProgressPathValid = priorProgressPathLength != 0 &&
        priorProgressPathLength < std::size(priorProgressPath);
    const std::filesystem::path telemetryPath =
        std::filesystem::temp_directory_path() /
        (L"todo18-table-critical-path-" +
         std::to_wstring(GetCurrentProcessId()) + L".tsv");
    std::error_code telemetryError;
    std::filesystem::remove(telemetryPath, telemetryError);
    SetEnvironmentVariableW(L"TODO18_CAPTURE_PROGRESS_PATH", nullptr);
    hancom::official_api::capability::ResetTableGraphDiagnosticCounters();
    hancom::inspection::ResetTableVirtualSlotCache();
    objects[0]->localTypeInfoCalls = 0;
    objects[0]->localGetIdsCalls = 0;
    objects[0]->localDispatchInvokeCalls = 0;
    hancom::dispatch::ResetDispidCacheDiagnostics();
    const bool captured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], controls, &acquired, &acquiredFailure);
    const auto typedFastPathDiagnostics =
        hancom::official_api::capability::ReadTableGraphDiagnosticCounters();
    const bool typedFastPathSkippedDiagnostics =
        typedFastPathDiagnostics.probes == 0 &&
        typedFastPathDiagnostics.cellRows == 0 &&
        typedFastPathDiagnostics.errorSinks == 0;
    const hancom::inspection::TableCallCounters firstProductionCounters =
        hancom::inspection::ReadTableCallCounters();
    const unsigned firstHeadCtrlCalls = objects[0]->localHeadCtrlCalls;
    const unsigned firstNextCalls = objects[0]->localNextCalls;
    const unsigned firstTypeInfoCalls = objects[0]->localTypeInfoCalls;
    const unsigned firstGetIdsCalls = objects[0]->localGetIdsCalls;
    const unsigned firstDispatchInvokeCalls =
        objects[0]->localDispatchInvokeCalls;
    const hancom::dispatch::DispidCacheDiagnostics firstCacheDiagnostics =
        hancom::dispatch::ReadDispidCacheDiagnostics();
    const unsigned firstTablePropertiesCalls =
        objects[0]->localTablePropertiesCalls;
    const unsigned firstCellRangeCalls = objects[0]->localCellRangeCalls;
    const unsigned firstSpanNavigationCalls =
        objects[0]->localSpanNavigationCalls;
    objects[0]->localHeadCtrlCalls = 0;
    objects[0]->localNextCalls = 0;
    objects[0]->localTablePropertiesCalls = 0;
    objects[0]->localCellRangeCalls = 0;
    objects[0]->localSpanNavigationCalls = 0;
    const bool reversedCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], reversedControls, &reversedAcquired,
            &reversedFailure);
    const hancom::inspection::TableCallCounters reversedProductionCounters =
        hancom::inspection::ReadTableCallCounters();
    const unsigned reversedHeadCtrlCalls = objects[0]->localHeadCtrlCalls;
    const unsigned reversedNextCalls = objects[0]->localNextCalls;
    const unsigned reversedTablePropertiesCalls =
        objects[0]->localTablePropertiesCalls;
    const unsigned reversedCellRangeCalls = objects[0]->localCellRangeCalls;
    const unsigned reversedSpanNavigationCalls =
        objects[0]->localSpanNavigationCalls;
    ReaderPayload telemetryBaselineAcquired;
    const bool telemetryBaselineCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], controls, &telemetryBaselineAcquired);
    ReaderPayload telemetryAcquired;
    const bool telemetryEnabled = SetEnvironmentVariableW(
        L"TODO18_CAPTURE_PROGRESS_PATH", telemetryPath.c_str()) != FALSE;
    const bool telemetryCaptured = telemetryEnabled &&
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], controls, &telemetryAcquired);
    SetEnvironmentVariableW(
        L"TODO18_CAPTURE_PROGRESS_PATH",
        priorProgressPathValid ? priorProgressPath : nullptr);
    struct TableTelemetryEvent final {
        unsigned point = 0;
        std::uint64_t wall100ns = 0;
        std::uint64_t count = 0;
    };
    std::vector<TableTelemetryEvent> telemetryEvents;
    {
        std::ifstream telemetry(telemetryPath);
        std::string line;
        while (std::getline(telemetry, line)) {
            std::istringstream fields(line);
            std::uint64_t elapsed = 0;
            std::uint64_t qpc = 0;
            TableTelemetryEvent event;
            if (fields >> elapsed >> qpc >> event.point >> event.wall100ns >>
                    event.count &&
                event.point >= 85 && event.point <= 89) {
                telemetryEvents.push_back(event);
            }
        }
    }
    std::filesystem::remove(telemetryPath, telemetryError);
    const std::array<std::uint64_t, 5> expectedTelemetryCounts{1, 7, 2, 2, 2};
    bool telemetrySequenceExact = telemetryEvents.size() == 5;
    for (size_t index = 0;
         telemetrySequenceExact && index < telemetryEvents.size(); ++index) {
        telemetrySequenceExact =
            telemetryEvents[index].point == 85 + index &&
            telemetryEvents[index].count == expectedTelemetryCounts[index];
    }
    const auto exact = [](const ReaderPayload& payload,
                          const std::uint64_t first,
                          const std::uint64_t second) {
        return payload.reader ==
                hancom::graph::capture::QualifiedReader::TableTopology &&
            payload.outcome == ReaderOutcome::Complete &&
            payload.tables.size() == 2 &&
            payload.tables[0].instanceIdPresent &&
            payload.tables[1].instanceIdPresent &&
            payload.tables[0].instanceId.empty() &&
            payload.tables[1].instanceId.empty() &&
            payload.tables[0].headCtrlOrdinal == first &&
            payload.tables[1].headCtrlOrdinal == second &&
            payload.tables[0].captionPresent.state ==
                hancom::graph::ObservationState::Value &&
            payload.tables[0].captionPresent.value == 0 &&
            payload.tables[1].captionPresent.state ==
                hancom::graph::ObservationState::Value &&
            payload.tables[1].captionPresent.value == 0 &&
            !payload.tables[0].cells.empty() &&
            !payload.tables[1].cells.empty();
    };
    const bool firstNativeOrder = captured && exact(acquired, 3, 7) &&
        firstHeadCtrlCalls == 1 && firstNextCalls == 7 &&
        firstTablePropertiesCalls == 2 &&
        firstCellRangeCalls == 0 && firstSpanNavigationCalls == 0 &&
        firstProductionCounters.headCtrl == 1 &&
        firstProductionCounters.next == 7 &&
        firstTypeInfoCalls == 18 && firstGetIdsCalls <= 100 &&
        firstDispatchInvokeCalls == 324 &&
        firstCacheDiagnostics.qualifiedCalls > 0 &&
        firstCacheDiagnostics.hits > 0 &&
        firstCacheDiagnostics.misses > 0 &&
        firstCacheDiagnostics.keys > 0 &&
        firstCacheDiagnostics.evictions == 0 &&
        firstCacheDiagnostics.unqualifiedCalls <= 100 &&
        firstProductionCounters.directRange == 0 &&
        firstProductionCounters.fallback == 0;
    const bool secondReversedNativeOrder =
        reversedCaptured && exact(reversedAcquired, 7, 3) &&
        reversedHeadCtrlCalls == 1 && reversedNextCalls == 7 &&
        reversedTablePropertiesCalls == 2 &&
        reversedCellRangeCalls == 0 && reversedSpanNavigationCalls == 0 &&
        reversedProductionCounters.headCtrl == 1 &&
        reversedProductionCounters.next == 7 &&
        reversedProductionCounters.directRange == 0 &&
        reversedProductionCounters.fallback == 0;
    for (auto& object : objects) {
        object->oneBasedMoveCoordinates = true;
        object->oneBasedBoundsCoordinates = true;
    }
    objects[0]->localCellRangeCalls = 0;
    ReaderPayload oneBasedAcquired;
    const bool oneBasedCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], controls, &oneBasedAcquired);
    const hancom::inspection::TableCallCounters oneBasedCounters =
        hancom::inspection::ReadTableCallCounters();
    for (auto& object : objects) {
        object->oneBasedMoveCoordinates = false;
        object->oneBasedBoundsCoordinates = false;
    }
    const bool oneBasedExact = oneBasedCaptured &&
        exact(oneBasedAcquired, 3, 7) &&
        objects[0]->localCellRangeCalls == 0 &&
        oneBasedCounters.directRange == 0 && oneBasedCounters.fallback == 0;
    const auto mixedBaseExact = [&](const bool moveOneBased,
                                    const bool boundsOneBased) {
        for (auto& object : objects) {
            object->oneBasedMoveCoordinates = moveOneBased;
            object->oneBasedBoundsCoordinates = boundsOneBased;
        }
        objects[0]->localCellRangeCalls = 0;
        ReaderPayload mixed;
        const bool capturedMixed =
            hancom::official_api::capability::CaptureTableTopologyReaderPayload(
                objects[0], controls, &mixed);
        const hancom::inspection::TableCallCounters counters =
            hancom::inspection::ReadTableCallCounters();
        return capturedMixed && exact(mixed, 3, 7) &&
            objects[0]->localCellRangeCalls == 0 &&
            counters.directRange == 0 && counters.fallback == 0;
    };
    const bool zeroMoveOneBoundsExact = mixedBaseExact(false, true);
    const bool oneMoveZeroBoundsExact = mixedBaseExact(true, false);
    for (auto& object : objects) {
        object->oneBasedMoveCoordinates = false;
        object->oneBasedBoundsCoordinates = false;
        object->multiCellDirectFixture = false;
        object->productionShape2077 = true;
    }
    const std::vector<ControlObservation> productionShapeControls{controls[0]};
    CComPtr<IUnknown> resolvedActionInterface;
    size_t resolvedActionSlot = 0;
    const HRESULT resolvedActionStatus =
        hancom::official_api::ResolveVirtualPropertyGet(
            objects[0],
            L"HAction",
            VT_DISPATCH,
            resolvedActionInterface,
            &resolvedActionSlot);
    CComPtr<IUnknown> resolvedParameterSetInterface;
    size_t resolvedParameterSetSlot = 0;
    const HRESULT resolvedParameterSetStatus =
        hancom::official_api::ResolveVirtualPropertyGet(
            objects[0],
            L"HParameterSet",
            VT_DISPATCH,
            resolvedParameterSetInterface,
            &resolvedParameterSetSlot);
    constexpr GUID rootVirtualInterfaceId{
        0x5E6A8276, 0xCF1C, 0x42B8,
        {0xBC, 0xED, 0x31, 0x95, 0x48, 0xB0, 0x2A, 0xF6}};
    CComPtr<IDispatch> resolvedActionValue;
    const HRESULT invokedActionStatus =
        hancom::official_api::InvokeResolvedVirtualPropertyGet(
            objects[0],
            rootVirtualInterfaceId,
            resolvedActionSlot,
            resolvedActionValue);
    CComPtr<IDispatch> resolvedParameterSetValue;
    const HRESULT invokedParameterSetStatus =
        hancom::official_api::InvokeResolvedVirtualPropertyGet(
            objects[0],
            rootVirtualInterfaceId,
            resolvedParameterSetSlot,
            resolvedParameterSetValue);
    const bool productionShapeVirtualResolverExact =
        resolvedActionStatus == S_OK && resolvedActionSlot == 74U &&
        resolvedParameterSetStatus == S_OK &&
        resolvedParameterSetSlot == 73U &&
        invokedActionStatus == S_OK &&
        resolvedActionValue != nullptr &&
        invokedParameterSetStatus == S_OK &&
        resolvedParameterSetValue != nullptr;
    ReaderPayload productionShapeDirect;
    const bool productionShapeDirectCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], productionShapeControls, &productionShapeDirect);
    const hancom::inspection::TableCallCounters productionShapeDirectCounters =
        hancom::inspection::ReadTableCallCounters();
    for (auto& object : objects) object->cellRangeUnavailable = true;
    ReaderPayload productionShapeFallback;
    const bool productionShapeFallbackCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], productionShapeControls, &productionShapeFallback);
    const hancom::inspection::TableCallCounters productionShapeFallbackCounters =
        hancom::inspection::ReadTableCallCounters();
    for (auto& object : objects) object->cellRangeUnavailable = false;
    const auto topologyBytes = [](const ReaderPayload& payload) {
        std::wostringstream encoded;
        if (payload.tables.size() != 1) return encoded.str();
        for (const auto& cell : payload.tables[0].cells) {
            encoded << cell.address << L'\t' << cell.listId << L'\t'
                    << cell.rowSpan << L'\t' << cell.columnSpan << L'\n';
        }
        return encoded.str();
    };
    const bool productionShape2077Exact =
        productionShapeVirtualResolverExact &&
        productionShapeDirectCaptured &&
        productionShapeFallbackCaptured && productionShapeDirect.tables.size() == 1 &&
        productionShapeFallback.tables.size() == 1 &&
        productionShapeDirect.tables[0].cells.size() == 2077 &&
        productionShapeFallback.tables[0].cells.size() == 2077 &&
        productionShapeDirectCounters.directRange == 0 &&
        productionShapeDirectCounters.fallback == 0 &&
        productionShapeFallbackCounters.directRange == 0 &&
        productionShapeFallbackCounters.fallback == 0 &&
        topologyBytes(productionShapeDirect) == topologyBytes(productionShapeFallback);

    std::vector<ControlObservation> closureControls = productionShapeControls;
    closureControls.front().instanceIdPresent = true;
    closureControls.front().instanceId = L"closure-table";
    objects[1]->fixtureInstanceId = L"closure-table";
    objects[6]->fixtureInstanceId = L"closure-table";
    for (auto& object : objects) {
        object->cellReferenceQualification = true;
        object->cellReferenceRegression = true;
    }
    hancom::graph::properties::ResetEffectivePropertyVirtualSlotCache();
    objects[0]->localDispatchInvokeCalls = 0;
    objects[0]->localTypeInfoCalls = 0;
    objects[0]->localGetIdsCalls = 0;
    objects[0]->localSetPosCalls = 0;
    objects[0]->currentList = 1;
    objects[0]->localSelectionMode = 0;
    objects[0]->localCellBlockPending = false;
    objects[0]->localCellAddressVerified = false;
    std::map<std::wstring, std::uint64_t> closureMemberInvokes;
    for (auto& object : objects) {
        object->memberInvokeCalls = &closureMemberInvokes;
    }
    ReaderPayload closureTable;
    const bool closureTableCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], closureControls, &closureTable);
    std::vector<hancom::graph::properties::ReferenceSite> closureSites;
    if (closureTable.tables.size() == 1) {
        closureSites.reserve(closureTable.tables.front().cells.size());
        for (const auto& cell : closureTable.tables.front().cells) {
            closureSites.push_back({
                hancom::graph::capture::PropertyTarget::Cell,
                L"closure-cell:" + cell.address,
                {cell.listId, 0, 0}, 0, {},
                L"5:tbl:1:0:1:0:1:closure-table",
            });
        }
    }
    ReaderPayload closureReferences;
    closureReferences.reader =
        hancom::graph::capture::QualifiedReader::EffectiveProperties;
    hancom::graph::properties::ReferenceClosureDiagnostics closureDiagnostics;
    const bool closureReferencesCaptured =
        hancom::graph::properties::CaptureCurrentReferenceClosure(
            objects[0], closureSites, &closureReferences,
            &closureDiagnostics) ==
            hancom::graph::properties::CaptureStatus::Complete;
    constexpr unsigned kClosureCellCount = 2077;
    constexpr unsigned kAuthorityInvokes = 176631;
    constexpr unsigned kAuthoritySetPos = 4156;
    const unsigned closureInvokes = objects[0]->localDispatchInvokeCalls;
    const unsigned closureTypeInfo = objects[0]->localTypeInfoCalls;
    const unsigned closureSetPos = objects[0]->localSetPosCalls;
    const hancom::inspection::TableCallCounters closureCallCounters =
        hancom::inspection::ReadTableCallCounters();
    const std::array<VARTYPE, 4> colorReturnTypes{
        VT_I4, VT_UI4, VT_I2, VT_UI2};
    std::array<HRESULT, 4> colorResolveStatuses{};
    std::array<size_t, 4> colorResolveSlots{};
    for (size_t index = 0; index < colorReturnTypes.size(); ++index) {
        objects[5]->borderFillView = false;
        objects[5]->cellBorderFillView = true;
        CComPtr<IUnknown> interfaceObject;
        colorResolveStatuses[index] =
            hancom::official_api::ResolveVirtualPropertyGet(
                objects[5],
                L"BorderColorRight",
                colorReturnTypes[index],
                interfaceObject,
            &colorResolveSlots[index]);
    }
    constexpr GUID cellBorderFillVirtualInterfaceId{
        0xC54797D7, 0xB2FF, 0x44A0,
        {0xBE, 0xC2, 0x2D, 0x20, 0xF2, 0x00, 0x89, 0x8C}};
    constexpr GUID borderFillVirtualInterfaceId{
        0xC81C513C, 0x94D5, 0x4589,
        {0x8B, 0x92, 0xBF, 0xF9, 0xFD, 0x5D, 0xA9, 0x46}};
    ULONG colorDirectValue = 0;
    HRESULT colorInvokeStatus =
        hancom::official_api::
            InvokeResolvedVirtualUnsignedLongPropertyGet(
                objects[5],
                cellBorderFillVirtualInterfaceId,
                colorResolveSlots[1],
                &colorDirectValue);
    if (FAILED(colorInvokeStatus)) {
        colorInvokeStatus =
            hancom::official_api::
                InvokeResolvedVirtualUnsignedLongPropertyGet(
                    objects[5],
                    borderFillVirtualInterfaceId,
                    colorResolveSlots[1],
                    &colorDirectValue);
    }
    const bool closureColorAbiExact =
        colorResolveStatuses[0] == DISP_E_TYPEMISMATCH &&
        colorResolveStatuses[1] == S_OK &&
        colorResolveSlots[1] == 28U &&
        colorResolveStatuses[2] == DISP_E_TYPEMISMATCH &&
        colorResolveStatuses[3] == DISP_E_TYPEMISMATCH &&
        colorInvokeStatus == S_OK &&
        colorDirectValue == 0;
    constexpr std::uint64_t kMinimumDirectVirtualPropertyGets = 15584;
    const bool closureAuthorityExact = closureTableCaptured &&
        closureReferencesCaptured &&
        closureColorAbiExact &&
        closureSites.size() == kClosureCellCount &&
        closureDiagnostics.visitedSites == kClosureCellCount &&
        closureDiagnostics.cellSites == kClosureCellCount &&
        closureDiagnostics.cellFastMode0Attempts == kClosureCellCount &&
        closureDiagnostics.cellFastMode0Hits == kClosureCellCount &&
        closureDiagnostics.cellFastMode0Rejections == 0 &&
        closureDiagnostics.cellQualifiedFallbackCalls == 0 &&
        closureDiagnostics.cellTableCellBlockCalls == 0 &&
        closureDiagnostics.cellFallbackSetPosCalls == 0 &&
        closureCallCounters.directVirtualPropertyGets >=
            kMinimumDirectVirtualPropertyGets &&
        closureInvokes +
            closureCallCounters.directVirtualPropertyGets ==
            kAuthorityInvokes &&
        closureTypeInfo == 12 &&
        closureSetPos == kAuthoritySetPos;
    std::wcout << L"TABLE_REFERENCE_CLOSURE_AUTHORITY_2077 "
               << closureAuthorityExact << L" INVOKES=" << closureInvokes
               << L" DIRECT_PROPERTY_GETS="
               << closureCallCounters.directVirtualPropertyGets
               << L" TYPEINFO=" << closureTypeInfo
               << L" SETPOS=" << closureSetPos
               << L" SITES=" << closureSites.size()
               << L" VISITED=" << closureDiagnostics.visitedSites
               << L" FAST_ATTEMPTS="
               << closureDiagnostics.cellFastMode0Attempts
               << L" FAST_HITS=" << closureDiagnostics.cellFastMode0Hits
               << L" FAST_REJECTIONS="
               << closureDiagnostics.cellFastMode0Rejections
               << L" QUALIFIED_FALLBACKS="
               << closureDiagnostics.cellQualifiedFallbackCalls
               << L" CELL_BLOCKS="
               << closureDiagnostics.cellTableCellBlockCalls << L'\n';
    std::wcout << L"TABLE_REFERENCE_CLOSURE_COLOR_ABI"
               << L" I4=" << static_cast<unsigned long>(
                      colorResolveStatuses[0])
               << L":" << colorResolveSlots[0]
               << L" UI4=" << static_cast<unsigned long>(
                      colorResolveStatuses[1])
               << L":" << colorResolveSlots[1]
               << L" I2=" << static_cast<unsigned long>(
                      colorResolveStatuses[2])
               << L":" << colorResolveSlots[2]
               << L" UI2=" << static_cast<unsigned long>(
                      colorResolveStatuses[3])
               << L":" << colorResolveSlots[3]
               << L" INVOKE=" << static_cast<unsigned long>(
                      colorInvokeStatus)
               << L":" << colorDirectValue << L'\n';
    std::vector<std::pair<std::wstring, std::uint64_t>>
        closureInvokeContributors(
            closureMemberInvokes.begin(), closureMemberInvokes.end());
    std::sort(
        closureInvokeContributors.begin(),
        closureInvokeContributors.end(),
        [](const auto& left, const auto& right) {
            return left.second > right.second;
        });
    for (const auto& contributor : closureInvokeContributors) {
        std::wcout << L"TABLE_REFERENCE_CLOSURE_MEMBER_INVOKES "
                   << contributor.first << L'=' << contributor.second
                   << L'\n';
    }

    for (auto& object : objects) {
        object->oneBasedMoveCoordinates = false;
        object->oneBasedBoundsCoordinates = false;
        object->multiCellDirectFixture = false;
        object->productionShape2077 = false;
        object->cellReferenceQualification = false;
        object->cellReferenceRegression = false;
    }
    objects[5]->cellBorderFillView = false;
    objects[5]->borderFillView = false;
    objects[1]->fixtureInstanceId.clear();
    objects[6]->fixtureInstanceId.clear();
    ReaderPayload fallbackAcquired;
    std::wstring fallbackFailure;
    objects[0]->cellRangeUnavailable = true;
    objects[0]->localHeadCtrlCalls = 0;
    objects[0]->localNextCalls = 0;
    objects[0]->localCellRangeCalls = 0;
    objects[0]->localSpanNavigationCalls = 0;
    const bool fallbackCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], controls, &fallbackAcquired, &fallbackFailure);
    objects[0]->cellRangeUnavailable = false;
    const unsigned fallbackHeadCtrlCalls = objects[0]->localHeadCtrlCalls;
    const unsigned fallbackNextCalls = objects[0]->localNextCalls;
    const unsigned fallbackCellRangeCalls = objects[0]->localCellRangeCalls;
    const unsigned fallbackSpanNavigationCalls =
        objects[0]->localSpanNavigationCalls;
    const bool unavailableFallbackExact =
        fallbackCaptured && exact(fallbackAcquired, 3, 7) &&
        fallbackHeadCtrlCalls == 1 && fallbackNextCalls == 7 &&
        fallbackCellRangeCalls == 0 && fallbackSpanNavigationCalls == 0;
    for (auto& object : objects) object->directCellRangeExact = false;
    ReaderPayload malformedFallbackAcquired;
    objects[0]->localHeadCtrlCalls = 0;
    objects[0]->localNextCalls = 0;
    objects[0]->localCellRangeCalls = 0;
    const bool malformedFallbackCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], controls, &malformedFallbackAcquired);
    for (auto& object : objects) object->directCellRangeExact = true;
    const bool malformedFallbackExact = malformedFallbackCaptured &&
        exact(malformedFallbackAcquired, 3, 7) &&
        objects[0]->localHeadCtrlCalls == 1 &&
        objects[0]->localNextCalls == 7 &&
        objects[0]->localCellRangeCalls == 0;
    std::vector<hancom::graph::capture::ImageObservation> noImages;
    const bool firstReconciled = firstNativeOrder &&
        hancom::official_api::capability::ReconcileControlLocators(
            controls, &acquired.tables, &noImages);
    const bool secondReconciled = secondReversedNativeOrder &&
        hancom::official_api::capability::ReconcileControlLocators(
            reversedControls, &reversedAcquired.tables, &noImages);
    ReaderPayload parityAcquired;
    ReaderPayload parityReversedAcquired;
    const bool parityRecaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], controls, &parityAcquired) &&
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], reversedControls, &parityReversedAcquired);
    const bool downstream = firstReconciled && secondReconciled &&
        oneBasedExact && zeroMoveOneBoundsExact && oneMoveZeroBoundsExact &&
        productionShape2077Exact && closureAuthorityExact &&
        unavailableFallbackExact &&
        malformedFallbackExact && parityRecaptured &&
        DuplicateEmptyRealTableReaderRecaptureSmoke(
            controls, parityAcquired, reversedControls, parityReversedAcquired);
    const bool telemetryByteParity = telemetryBaselineCaptured &&
        telemetryCaptured && telemetrySequenceExact &&
        DuplicateEmptyRealTableReaderByteParitySmoke(
            controls, telemetryBaselineAcquired, controls, telemetryAcquired);
    ReaderPayload noTableControlsAcquired;
    const bool noTableControlsComplete =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], {}, &noTableControlsAcquired) &&
        noTableControlsAcquired.reader ==
            hancom::graph::capture::QualifiedReader::TableTopology &&
        noTableControlsAcquired.outcome == ReaderOutcome::Complete &&
        noTableControlsAcquired.coverage ==
            hancom::graph::CoverageState::Complete &&
        noTableControlsAcquired.tables.empty();

    for (auto& object : objects) object->tableMode = true;
    std::vector<ControlObservation> captionControls{controls[0]};
    captionControls.push_back({L"atno", L"caption-number", 8, {9, 0, 0}});
    ReaderPayload captionAcquired;
    const bool captionCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], captionControls, &captionAcquired) &&
        captionAcquired.tables.size() == 1 &&
        captionAcquired.tables[0].captionPresent.state ==
            hancom::graph::ObservationState::Value &&
        captionAcquired.tables[0].captionPresent.value == 1 &&
        captionAcquired.tables[0].captionText.state ==
            hancom::graph::ObservationState::Value &&
        captionAcquired.tables[0].captionText.value ==
            L"Table fixture caption\r\nsecond paragraph" &&
        captionAcquired.tables[0].captionAutomaticNumber.state ==
            hancom::graph::ObservationState::Value &&
        captionAcquired.tables[0].captionAutomaticNumber.value == 1 &&
        captionAcquired.tables[0].captionStyleId.state ==
            hancom::graph::ObservationState::Value &&
        captionAcquired.tables[0].captionStyleId.value == 5 &&
        captionAcquired.tables[0].captionStyleName.state ==
            hancom::graph::ObservationState::Value &&
        captionAcquired.tables[0].captionStyleName.value ==
            L"Fake Definition" &&
        captionAcquired.tables[0].captionList.value == 9 &&
        captionAcquired.coverageFacts.empty();
    for (auto& object : objects) object->tableMode = false;

    for (const size_t index : {size_t{1}, size_t{6}}) {
        objects[index]->instanceIdUnavailable = true;
    }
    std::vector<ControlObservation> unavailableControls = controls;
    for (auto& control : unavailableControls) {
        control.instanceIdPresent = false;
        control.instanceId.clear();
    }
    const std::vector<ControlObservation> reversedUnavailableControls{
        unavailableControls[1], unavailableControls[0]};
    ReaderPayload unavailableAcquired;
    ReaderPayload reversedUnavailableAcquired;
    const bool unavailableCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], unavailableControls, &unavailableAcquired);
    const bool reversedUnavailableCaptured =
        hancom::official_api::capability::CaptureTableTopologyReaderPayload(
            objects[0], reversedUnavailableControls,
            &reversedUnavailableAcquired);
    const auto unavailableExact = [](
        const ReaderPayload& payload,
        const std::uint64_t first,
        const std::uint64_t second) {
        return payload.tables.size() == 2 &&
            payload.tables[0].headCtrlOrdinal == first &&
            payload.tables[1].headCtrlOrdinal == second &&
            std::all_of(
                payload.tables.begin(), payload.tables.end(),
                [](const auto& table) {
                    return !table.instanceIdPresent &&
                        table.instanceId.empty() && table.cells.empty() &&
                        table.captionPresent.state ==
                            hancom::graph::ObservationState::NotExposed;
                });
    };
    const bool unavailableFirstExact = unavailableCaptured &&
        unavailableExact(unavailableAcquired, 3, 7);
    const bool unavailableSecondExact = reversedUnavailableCaptured &&
        unavailableExact(reversedUnavailableAcquired, 7, 3);
    const bool unavailableNativeExact =
        unavailableFirstExact && unavailableSecondExact;
    const bool unavailableReconciled = unavailableNativeExact &&
        hancom::official_api::capability::ReconcileControlLocators(
            unavailableControls, &unavailableAcquired.tables, &noImages) &&
        hancom::official_api::capability::ReconcileControlLocators(
            reversedUnavailableControls,
            &reversedUnavailableAcquired.tables, &noImages);
    const bool unavailableDownstream = unavailableReconciled &&
        DuplicateUnavailableTableStableKeyRecaptureSmoke(
            unavailableControls, unavailableAcquired,
            reversedUnavailableControls, reversedUnavailableAcquired);
    std::wcout << L"REAL_TABLE_READER_PROBE_ACQUISITION "
               << (firstNativeOrder && secondReversedNativeOrder)
               << L" first_failure=" << acquiredFailure
               << L" reversed_failure=" << reversedFailure
               << L" first_head=" << firstHeadCtrlCalls
               << L" first_next=" << firstNextCalls
               << L" reversed_head=" << reversedHeadCtrlCalls
               << L" reversed_next=" << reversedNextCalls
               << L" first_properties=" << firstTablePropertiesCalls
               << L" first_typeinfo=" << firstTypeInfoCalls
               << L" first_getids=" << firstGetIdsCalls
               << L" first_invoke=" << firstDispatchInvokeCalls
               << L" qualified=" << firstCacheDiagnostics.qualifiedCalls
               << L" unqualified=" << firstCacheDiagnostics.unqualifiedCalls
               << L" cache_keys=" << firstCacheDiagnostics.keys
               << L" cache_hits=" << firstCacheDiagnostics.hits
               << L" cache_misses=" << firstCacheDiagnostics.misses
               << L" cache_evictions=" << firstCacheDiagnostics.evictions
               << L" reversed_properties=" << reversedTablePropertiesCalls
               << L" first_ranges=" << firstCellRangeCalls
               << L" first_span_nav=" << firstSpanNavigationCalls
               << L" reversed_ranges=" << reversedCellRangeCalls
               << L" reversed_span_nav=" << reversedSpanNavigationCalls
               << L" fallback_failure=" << fallbackFailure
               << L" fallback_head=" << fallbackHeadCtrlCalls
               << L" fallback_next=" << fallbackNextCalls
               << L" fallback_ranges=" << fallbackCellRangeCalls
               << L" fallback_span_nav=" << fallbackSpanNavigationCalls
               << L'\n'
               << L"REAL_TABLE_READER_NATIVE_RECONCILIATION "
               << (firstReconciled && secondReconciled) << L'\n'
               << L"REAL_TABLE_READER_CAPTURE_IDENTITY_PARITY " << downstream
               << L" one=" << oneBasedExact
               << L" mixed01=" << zeroMoveOneBoundsExact
               << L" mixed10=" << oneMoveZeroBoundsExact
               << L" unavailable=" << unavailableFallbackExact
               << L" malformed=" << malformedFallbackExact
               << L" shape2077=" << productionShape2077Exact
               << L" action_hr="
               << static_cast<unsigned long>(resolvedActionStatus)
               << L" action_slot=" << resolvedActionSlot
               << L" parameter_set_hr="
               << static_cast<unsigned long>(resolvedParameterSetStatus)
               << L" parameter_set_slot=" << resolvedParameterSetSlot
               << L" action_invoke_hr="
               << static_cast<unsigned long>(invokedActionStatus)
               << L" parameter_set_invoke_hr="
               << static_cast<unsigned long>(invokedParameterSetStatus)
               << L" direct_property_gets="
               << productionShapeDirectCounters.directVirtualPropertyGets
               << L" fallback_property_gets="
               << productionShapeFallbackCounters.directVirtualPropertyGets
               << L" recaptured=" << parityRecaptured
               << L'\n'
               << L"TABLE_CRITICAL_PATH_TELEMETRY_SEQUENCE_COUNTS "
               << telemetrySequenceExact
               << L" events=" << telemetryEvents.size() << L'\n'
               << L"TABLE_CRITICAL_PATH_TELEMETRY_OFF_ON_BYTE_PARITY "
               << telemetryByteParity << L'\n'
               << L"TABLE_TYPED_FAST_PATH_NO_DIAGNOSTIC_RENDER "
               << typedFastPathSkippedDiagnostics << L'\n'
               << L"TABLE_TOPOLOGY_NO_TABLE_CONTROLS_COMPLETE_EMPTY "
               << noTableControlsComplete << L'\n'
               << L"TABLE_CAPTION_NATIVE_MULTIPARAGRAPH_STYLE_NUMBER "
               << captionCaptured << L'\n'
               << L"TABLE_CAPTION_NO_CAPTION_TYPED_VALUE "
               << (firstNativeOrder && secondReversedNativeOrder) << L'\n'
               << L"TABLE_CAPTION_IDENTITY_UNAVAILABLE_TYPED_NOT_EXPOSED "
               << unavailableNativeExact << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_EMPTY_PERMUTATION_MATRIX "
               << downstream << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_ACQUIRED_COUNT2 "
               << (unavailableAcquired.tables.size() == 2 &&
                   reversedUnavailableAcquired.tables.size() == 2) << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_FIRST_ORDER "
               << unavailableFirstExact << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_REVERSED_ORDER "
               << unavailableSecondExact << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_PRESENT_FALSE "
               << unavailableNativeExact << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_RAW_EMPTY "
               << unavailableNativeExact << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_NATIVE_EXACT "
               << unavailableNativeExact << L'\n'
               << L"TABLE_STABLE_KEY_DUPLICATE_UNAVAILABLE_MATRIX "
               << unavailableDownstream << L'\n';
    const bool geometryEpochCache = CellGeometryEpochCacheSmoke();
    return geometryEpochCache && firstNativeOrder && secondReversedNativeOrder &&
        firstReconciled && secondReconciled && downstream &&
        telemetrySequenceExact && telemetryByteParity &&
        typedFastPathSkippedDiagnostics &&
        noTableControlsComplete && captionCaptured && unavailableNativeExact &&
        unavailableReconciled && unavailableDownstream;
}

bool DuplicateEmptyControlIdentityProductionParitySmoke() {
    using hancom::graph::capture::ControlObservation;
    using hancom::graph::capture::ImageObservation;
    using hancom::graph::capture::NativePosition;
    using hancom::graph::capture::TableObservation;
    static constexpr CallSpec ctrlId{
        L"CtrlID", 2, DISPATCH_PROPERTYGET, 0, VT_BSTR};
    static constexpr CallSpec instanceId{
        L"GetCtrlInstID", 15001, DISPATCH_METHOD, 0, VT_BSTR};
    const std::array<std::pair<const wchar_t*, const wchar_t*>, 6> identities{{
        {L"tbl", L""}, {L"tbl", L"duplicate-table"},
        {L"tbl", L"duplicate-table"}, {L"gso", L""},
        {L"gso", L"duplicate-image"},
        {L"gso", L"duplicate-image"},
    }};
    std::vector<ControlObservation> controls;
    std::vector<TableObservation> tables;
    std::vector<ImageObservation> images;
    bool honestRawIds = true;
    bool emptyImageReader = false;
    bool emptyTableReader = false;
    {
        CComPtr<WorkflowDispatch> objects[7];
        for (int index = 0; index < 7; ++index) {
            auto* const raw = new (std::nothrow)
                CComObjectNoLock<WorkflowDispatch>();
            if (raw == nullptr) return false;
            objects[index] = raw;
            raw->role = index == 6
                ? WorkflowRole::Control
                : static_cast<WorkflowRole>(index);
        }
        for (auto& object : objects) {
            object->control = objects[1];
            object->table = objects[2];
            object->cell = objects[3];
            object->range = objects[4];
            object->parameterSet = objects[5];
            object->nestedControl = objects[6];
            object->sharedRow = &objects[0]->row;
            object->sharedColumn = &objects[0]->column;
            object->sharedList = &objects[0]->currentList;
            object->setPosCalls = &objects[0]->localSetPosCalls;
            object->restoredParagraph = &objects[0]->localRestoredParagraph;
            object->restoredCharacter = &objects[0]->localRestoredCharacter;
        }
        objects[1]->identityFixture = true;
        objects[1]->fixtureCtrlId = L"tbl";
        objects[1]->fixtureInstanceId = L"";
        hancom::graph::tables::TableGraphRecord nativeTable;
        std::wstring tableError;
        emptyTableReader =
            hancom::graph::tables::CaptureTableGraphFromNative(
                objects[0], L"", false, false, {}, &nativeTable,
                &tableError) ==
                hancom::graph::tables::BuildStatus::Complete &&
            nativeTable.sessionInstanceId.empty() &&
            !nativeTable.physicalCells.empty();
    }
    for (size_t index = 0; index < identities.size(); ++index) {
        auto* const raw = new (std::nothrow)
            CComObjectNoLock<WorkflowDispatch>();
        if (raw == nullptr) return false;
        CComPtr<WorkflowDispatch> control = raw;
        control->role = WorkflowRole::Control;
        control->identityFixture = true;
        control->fixtureCtrlId = identities[index].first;
        control->fixtureInstanceId = identities[index].second;
        const RawCall observedCtrlId =
            hancom::official_api::capability::InvokeOneShot(
                control, ctrlId, {}, false);
        const RawCall observedInstanceId =
            hancom::official_api::capability::InvokeOneShot(
                control, instanceId, {}, false);
        const std::wstring rawCtrlId = observedCtrlId.result.vt == VT_BSTR
            ? std::wstring(observedCtrlId.result.bstrVal,
                           SysStringLen(observedCtrlId.result.bstrVal))
            : std::wstring();
        const std::wstring rawInstanceId =
            observedInstanceId.result.vt == VT_BSTR
            ? std::wstring(observedInstanceId.result.bstrVal,
                           SysStringLen(observedInstanceId.result.bstrVal))
            : std::wstring();
        honestRawIds = honestRawIds &&
            rawCtrlId == identities[index].first &&
            rawInstanceId == identities[index].second;
        if (index == 3) {
            control->fixtureCtrlId = L"$pic";
            hancom::graph::images::ImageGraphRecord nativeImage;
            emptyImageReader =
                hancom::graph::images::CaptureImageGraphFromNative(
                    control, L"", &nativeImage) ==
                    hancom::graph::images::CaptureStatus::Complete &&
                nativeImage.sessionInstanceId.empty();
            control->fixtureCtrlId = identities[index].first;
        }
        const NativePosition anchor{
            static_cast<std::int64_t>(index + 1),
            static_cast<std::int64_t>(index + 10),
            static_cast<std::int64_t>(index + 100)};
        controls.push_back({rawCtrlId, rawInstanceId,
                            static_cast<std::uint64_t>(index), anchor, false});
        if (rawCtrlId == L"tbl") {
            TableObservation table;
            table.instanceId = rawInstanceId;
            table.headCtrlOrdinal = static_cast<std::uint64_t>(index);
            table.anchor = anchor;
            table.locatorComplete = true;
            tables.push_back(std::move(table));
        } else {
            ImageObservation image;
            image.ctrlId = rawCtrlId;
            image.instanceId = rawInstanceId;
            image.headCtrlOrdinal = static_cast<std::uint64_t>(index);
            image.anchor = anchor;
            image.locatorComplete = true;
            images.push_back(std::move(image));
        }
    }
    const bool reconciled = honestRawIds &&
        hancom::official_api::capability::ReconcileControlLocators(
            controls, &tables, &images);
    bool canonicalLocators = reconciled;
    for (size_t index = 0; index < tables.size(); ++index) {
        canonicalLocators = canonicalLocators &&
            tables[index].anchor.list == static_cast<std::int64_t>(index + 1);
    }
    for (size_t index = 0; index < images.size(); ++index) {
        canonicalLocators = canonicalLocators && images[index].anchor.list ==
            static_cast<std::int64_t>(index + 4);
    }
    std::vector<TableObservation> rejectedTables = tables;
    std::vector<ImageObservation> rejectedImages = images;
    for (auto& table : rejectedTables) table.anchor = {-1, -1, -1};
    for (auto& image : rejectedImages) image.anchor = {-1, -1, -1};
    rejectedImages.back().headCtrlOrdinal = 999;
    const bool rejected =
        !hancom::official_api::capability::ReconcileControlLocators(
            controls, &rejectedTables, &rejectedImages);
    const bool noLeakage = rejected &&
        std::all_of(rejectedTables.begin(), rejectedTables.end(),
            [](const auto& table) { return table.anchor.list == -1; }) &&
        std::all_of(rejectedImages.begin(), rejectedImages.end(),
            [](const auto& image) { return image.anchor.list == -1; });

    const auto presenceCase = [](
        const bool controlTablePresent,
        const bool tablePresent,
        const bool controlImagePresent,
        const bool imagePresent,
        const bool duplicateTable,
        const bool expectSuccess) {
        std::vector<ControlObservation> observed{
            {L"tbl", L"", 41, {410, 411, 412}},
            {L"$pic", L"", 42, {420, 421, 422}},
        };
        observed[0].instanceIdPresent = controlTablePresent;
        observed[1].instanceIdPresent = controlImagePresent;
        if (duplicateTable) observed.push_back(observed[0]);

        TableObservation table;
        table.instanceIdPresent = tablePresent;
        table.instanceId = L"";
        table.headCtrlOrdinal = 41;
        table.anchor = {-41, -42, -43};
        table.locatorComplete = false;
        ImageObservation image;
        image.instanceIdPresent = imagePresent;
        image.instanceId = L"";
        image.ctrlId = L"$pic";
        image.headCtrlOrdinal = 42;
        image.anchor = {-51, -52, -53};
        image.locatorComplete = false;
        std::vector<TableObservation> candidateTables{table};
        std::vector<ImageObservation> candidateImages{image};

        const bool result =
            hancom::official_api::capability::ReconcileControlLocators(
                observed, &candidateTables, &candidateImages);
        if (result != expectSuccess) return false;
        if (expectSuccess) {
            return candidateTables.size() == 1 &&
                candidateImages.size() == 1 &&
                candidateTables[0].anchor.list == 410 &&
                candidateTables[0].anchor.paragraph == 411 &&
                candidateTables[0].anchor.character == 412 &&
                candidateImages[0].anchor.list == 420 &&
                candidateImages[0].anchor.paragraph == 421 &&
                candidateImages[0].anchor.character == 422;
        }
        return candidateTables.size() == 1 && candidateImages.size() == 1 &&
            candidateTables[0].instanceIdPresent == tablePresent &&
            candidateTables[0].instanceId.empty() &&
            candidateTables[0].headCtrlOrdinal == 41 &&
            candidateTables[0].anchor.list == -41 &&
            candidateTables[0].anchor.paragraph == -42 &&
            candidateTables[0].anchor.character == -43 &&
            !candidateTables[0].locatorComplete &&
            candidateImages[0].instanceIdPresent == imagePresent &&
            candidateImages[0].instanceId.empty() &&
            candidateImages[0].ctrlId == L"$pic" &&
            candidateImages[0].headCtrlOrdinal == 42 &&
            candidateImages[0].anchor.list == -51 &&
            candidateImages[0].anchor.paragraph == -52 &&
            candidateImages[0].anchor.character == -53 &&
            !candidateImages[0].locatorComplete;
    };
    const bool presenceIdentity =
        presenceCase(true, true, true, true, false, true) &&
        presenceCase(false, false, false, false, false, true) &&
        presenceCase(true, false, true, true, false, false) &&
        presenceCase(false, true, true, true, false, false) &&
        presenceCase(true, true, true, false, false, false) &&
        presenceCase(true, true, false, true, false, false);
    const bool presenceAtomic = presenceIdentity &&
        presenceCase(true, true, true, true, true, false);
    std::wcout << L"WORKFLOW_COM_DUPLICATE_EMPTY_TABLE_IMAGE_IDENTITY "
               << canonicalLocators << L'\n'
               << L"WORKFLOW_COM_EMPTY_TABLE_NATIVE_READER "
               << emptyTableReader << L'\n'
               << L"WORKFLOW_COM_EMPTY_IMAGE_NATIVE_READER "
               << emptyImageReader << L'\n'
               << L"WORKFLOW_COM_NO_CROSS_OBJECT_LEAKAGE "
               << noLeakage << L'\n'
               << L"WORKFLOW_COM_INSTANCE_PRESENCE_IDENTITY "
               << presenceIdentity << L'\n'
               << L"WORKFLOW_COM_INSTANCE_PRESENCE_ATOMIC "
               << presenceAtomic << L'\n';
    return canonicalLocators && emptyTableReader && emptyImageReader &&
        noLeakage && presenceIdentity && presenceAtomic;
}

bool CancellationCheckpointBeforeFirstReaderSmoke() {
    CComPtr<WorkflowDispatch> objects[10];
    for (int index = 0; index < 10; ++index) {
        CComObjectNoLock<WorkflowDispatch>* raw =
            new (std::nothrow) CComObjectNoLock<WorkflowDispatch>();
        if (raw == nullptr) return false;
        objects[index] = raw;
        raw->role = index >= 6 ? WorkflowRole::Control :
            static_cast<WorkflowRole>(index);
        raw->nestedInstance = index == 6;
    }
    for (auto& object : objects) {
        object->control = objects[1];
        object->table = objects[2];
        object->cell = objects[3];
        object->range = objects[4];
        object->parameterSet = objects[5];
        object->nestedControl = objects[6];
        object->scanOwnerA = objects[7];
        object->scanOwnerB = objects[9];
        object->sharedRow = &objects[0]->row;
        object->sharedColumn = &objects[0]->column;
        object->sharedList = &objects[0]->currentList;
        object->selectionMode = &objects[0]->localSelectionMode;
        object->cellBlockPending = &objects[0]->localCellBlockPending;
        object->cellAddressVerified = &objects[0]->localCellAddressVerified;
        object->signatureDrift = &objects[0]->localSignatureDrift;
        object->failRestore = &objects[0]->localFailRestore;
        object->hwpmlCaptures = &objects[0]->localHwpmlCaptures;
        object->setPosCalls = &objects[0]->localSetPosCalls;
        object->tableCellBlockCalls = &objects[0]->localTableCellBlockCalls;
        object->moveScanPosCalls = &objects[0]->localMoveScanPosCalls;
        object->captionMatchCount = &objects[0]->localCaptionMatchCount;
        object->restoredParagraph = &objects[0]->localRestoredParagraph;
        object->restoredCharacter = &objects[0]->localRestoredCharacter;
    }
    const auto environmentPlatform = FixtureEnvironmentPlatform();
    hancom::graph::identity::DocumentSessionId session{};
    session.bytes = {0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x40, 0x70,
                     0x80, 0x90, 0xa0, 0xb0, 0xc0, 0xd0, 0xe0, 0xf0};
    const std::wstring base =
        L"Local\\HancomGraphCapture." +
        hancom::graph::identity::FormatCanonicalUuid(session);
    HANDLE checkpoint = CreateEventW(
        nullptr, TRUE, FALSE, (base + L".Checkpoint").c_str());
    HANDLE cancel = CreateEventW(
        nullptr, TRUE, FALSE, (base + L".Cancel").c_str());
    if (checkpoint == nullptr || cancel == nullptr) {
        if (checkpoint != nullptr) CloseHandle(checkpoint);
        if (cancel != nullptr) CloseHandle(cancel);
        return false;
    }
    if (SetEvent(cancel) == FALSE) {
        CloseHandle(checkpoint);
        CloseHandle(cancel);
        return false;
    }
    wchar_t priorProgressPath[32768]{};
    const DWORD priorProgressPathLength = GetEnvironmentVariableW(
        L"TODO18_CAPTURE_PROGRESS_PATH", priorProgressPath,
        static_cast<DWORD>(std::size(priorProgressPath)));
    const bool priorProgressPathValid = priorProgressPathLength != 0 &&
        priorProgressPathLength < std::size(priorProgressPath);
    const std::filesystem::path telemetryPath =
        std::filesystem::temp_directory_path() /
        (L"todo18-cancel-checkpoint-order-" +
         std::to_wstring(GetCurrentProcessId()) + L".tsv");
    std::error_code telemetryError;
    std::filesystem::remove(telemetryPath, telemetryError);
    const bool telemetryEnabled = SetEnvironmentVariableW(
        L"TODO18_CAPTURE_PROGRESS_PATH", telemetryPath.c_str()) != FALSE;
    const std::filesystem::path graphReadRoot =
        std::filesystem::temp_directory_path() /
        (L"hwp-graphread-cancel-order-" +
         std::to_wstring(GetCurrentProcessId()));
    std::filesystem::remove_all(graphReadRoot, telemetryError);
    hancom::graph::store::GraphStore graphReadStore(graphReadRoot.wstring());
    const hancom::graph::capture::CaptureStatus status =
        telemetryEnabled && graphReadStore.Initialize()
            ? hancom::official_api::capability::CaptureDocumentGraphToStore(
                  objects[0], &graphReadStore, &environmentPlatform, &session)
            : hancom::graph::capture::CaptureStatus::IncompleteCapture;
    SetEnvironmentVariableW(
        L"TODO18_CAPTURE_PROGRESS_PATH",
        priorProgressPathValid ? priorProgressPath : nullptr);
    bool sawBegin = false;
    bool sawCheckpoint = false;
    bool sawReaderStart = false;
    bool checkpointBeforeReader = false;
    {
        std::ifstream telemetry(telemetryPath);
        std::string line;
        while (std::getline(telemetry, line)) {
            std::istringstream fields(line);
            std::uint64_t elapsed = 0;
            std::uint64_t qpc = 0;
            unsigned point = 0;
            std::uint64_t first = 0;
            std::uint64_t second = 0;
            if (!(fields >> elapsed >> qpc >> point >> first >> second)) {
                continue;
            }
            if (point == static_cast<unsigned>(
                    hancom::graph::capture::CaptureProgressPoint::CaptureBegin)) {
                sawBegin = true;
            }
            if (point == static_cast<unsigned>(
                    hancom::graph::capture::CaptureProgressPoint::
                        CancellationCheckpoint)) {
                sawCheckpoint = true;
                checkpointBeforeReader = !sawReaderStart;
            }
            if (point == static_cast<unsigned>(
                    hancom::graph::capture::CaptureProgressPoint::ReaderStart)) {
                sawReaderStart = true;
            }
        }
    }
    std::filesystem::remove(telemetryPath, telemetryError);
    std::filesystem::remove_all(graphReadRoot, telemetryError);
    CloseHandle(checkpoint);
    CloseHandle(cancel);
    std::wcout << L"CANCEL_CHECKPOINT_BEFORE_READER begin=" << sawBegin
               << L" checkpoint=" << sawCheckpoint
               << L" reader=" << sawReaderStart
               << L" order=" << checkpointBeforeReader
               << L" status=" << static_cast<unsigned>(status) << L'\n';
    return sawBegin && sawCheckpoint && checkpointBeforeReader &&
        !sawReaderStart &&
        status == hancom::graph::capture::CaptureStatus::Cancelled;
}

bool UnpairedCancellationEventDoesNotAbortSmoke() {
    CComPtr<WorkflowDispatch> root;
    auto* const raw = new (std::nothrow) CComObjectNoLock<WorkflowDispatch>();
    if (raw == nullptr) return false;
    root = raw;
    raw->role = WorkflowRole::Root;
    const auto environmentPlatform = FixtureEnvironmentPlatform();
    hancom::graph::identity::DocumentSessionId session{};
    session.bytes = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x47, 0x28,
                     0x89, 0x2a, 0x2b, 0x2c, 0x2d, 0x2e, 0x2f, 0x30};
    const std::wstring cancelName =
        L"Local\\HancomGraphCapture." +
        hancom::graph::identity::FormatCanonicalUuid(session) + L".Cancel";
    HANDLE cancel = CreateEventW(nullptr, TRUE, FALSE, cancelName.c_str());
    if (cancel == nullptr) return false;
    const std::filesystem::path graphReadRoot =
        std::filesystem::temp_directory_path() /
        (L"hwp-graphread-unpaired-cancel-" +
         std::to_wstring(GetCurrentProcessId()));
    std::error_code telemetryError;
    std::filesystem::remove_all(graphReadRoot, telemetryError);
    hancom::graph::store::GraphStore graphReadStore(graphReadRoot.wstring());
    hancom::graph::capture::CaptureDiagnostics diagnostics{};
    if (!graphReadStore.Initialize()) {
        std::filesystem::remove_all(graphReadRoot, telemetryError);
        CloseHandle(cancel);
        std::wcout << L"UNPAIRED_CANCEL_EVENT_IGNORED xor_abort=0 status=init\n";
        return false;
    }
    const hancom::graph::capture::CaptureStatus status =
        hancom::official_api::capability::CaptureDocumentGraphToStore(
            root, &graphReadStore, &environmentPlatform, &session,
            &diagnostics);
    std::filesystem::remove_all(graphReadRoot, telemetryError);
    CloseHandle(cancel);
    const bool xorAbort =
        status == hancom::graph::capture::CaptureStatus::IncompleteCapture &&
        diagnostics.stage == hancom::graph::capture::FailureStage::None &&
        diagnostics.failureDetail.empty();
    std::wcout << L"UNPAIRED_CANCEL_EVENT_IGNORED xor_abort=" << xorAbort
               << L" status=" << static_cast<unsigned>(status)
               << L" stage=" << static_cast<unsigned>(diagnostics.stage)
               << L" failure=" << diagnostics.failureDetail << L"\n";
    return !xorAbort &&
        (status != hancom::graph::capture::CaptureStatus::IncompleteCapture ||
         diagnostics.stage != hancom::graph::capture::FailureStage::Baseline ||
         !diagnostics.failureDetail.empty());
}

bool OfficialApiCapabilitySmoke() {
    const bool raw = StrictRawInvokeSmoke();
    const bool prerequisites = QualificationOutputSmoke();
    const bool workflows = WorkflowProductionPathSmoke();
    const bool cancelOrder = CancellationCheckpointBeforeFirstReaderSmoke();
    const bool unpairedCancel = UnpairedCancellationEventDoesNotAbortSmoke();
    const bool logicalWrappers = LogicalWrapperIdentitySmoke();
    const bool duplicateEmptyParity =
        DuplicateEmptyControlIdentityProductionParitySmoke();
    const bool tableInstancePresence = TableInstanceIdNativePresenceSmoke();
    const bool realTableDuplicateEmpty =
        RealTableReaderDuplicateEmptyParitySmoke();
    std::wcout << L"CAPABILITY_RAW " << raw << L'\n'
               << L"CAPABILITY_PREREQUISITES " << prerequisites << L'\n'
               << L"CAPABILITY_WORKFLOWS " << workflows << L'\n'
               << L"CAPABILITY_CANCEL_CHECKPOINT_BEFORE_READER "
               << cancelOrder << L'\n'
               << L"CAPABILITY_UNPAIRED_CANCEL_EVENT_IGNORED "
               << unpairedCancel << L'\n'
               << L"CAPABILITY_LOGICAL_WRAPPERS " << logicalWrappers << L'\n'
               << L"CAPABILITY_DUPLICATE_EMPTY_PRODUCTION_PARITY "
               << duplicateEmptyParity << L'\n'
               << L"CAPABILITY_TABLE_INSTANCE_ID_PRESENCE "
               << tableInstancePresence << L'\n'
               << L"CAPABILITY_REAL_TABLE_DUPLICATE_EMPTY_PARITY "
               << realTableDuplicateEmpty << L'\n';
    return raw && prerequisites && workflows && cancelOrder && unpairedCancel &&
        logicalWrappers && duplicateEmptyParity && tableInstancePresence &&
        realTableDuplicateEmpty;
}

bool TableReaderPerformanceSmoke() {
    return RealTableReaderDuplicateEmptyParitySmoke();
}

bool ReferenceClosurePerformanceSmoke() {
    return ReferenceClosureProfileSmoke();
}
